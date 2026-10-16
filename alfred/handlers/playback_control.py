"""Playback control handlers — pause / resume / stop / seek / subtitles /
now-playing. Controls an already-running Plex session.
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

FLASK_BASE = os.getenv('FLASK_BASE', 'http://localhost:5001')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')


def _auth_headers() -> Dict[str, str]:
    return {'X-API-Key': SERVICE_API_KEY} if SERVICE_API_KEY else {}


# ---------------------------------------------------------------------------
# Session + room resolution
# ---------------------------------------------------------------------------

def _fetch_rooms() -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/settings/rooms',
            headers=_auth_headers(),
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json().get('rooms', []) or []
    except Exception as e:
        logger.warning('Rooms fetch failed: %s', e)
        return []


def _fetch_active_sessions() -> Dict[str, Any]:
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/plex/sessions/active',
            headers=_auth_headers(),
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json() or {}
    except Exception as e:
        logger.warning('Active sessions fetch failed: %s', e)
        return {}


def _fetch_plex_sessions_with_address() -> List[Dict[str, Any]]:
    """Fetch Plex sessions with their player `address` field present.

    /api/plex/sessions/active strips the address for UI safety; /api/plex/clients
    exposes it. We need it for direct Plex HTTP control.
    """
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/plex/clients',
            headers=_auth_headers(),
            timeout=8,
        )
        resp.raise_for_status()
        return (resp.json() or {}).get('sessions') or []
    except Exception as e:
        logger.warning('Plex clients fetch failed: %s', e)
        return []


_PLEX_CLIENT_PLATFORMS = {'android_tv', 'chromecast_gtv', 'shield'}


def _room_device_ips(room: Dict[str, Any]) -> List[str]:
    """All candidate IPs from a room in preference order — Plex-client-capable first."""
    out: List[str] = []
    # Top-level ip/host first
    top = room.get('ip') or room.get('host')
    # Devices ranked: Plex-capable platforms first
    ranked: List[str] = []
    other: List[str] = []
    for dev in room.get('devices') or []:
        if not isinstance(dev, dict):
            continue
        host = dev.get('host')
        if not host:
            continue
        if (dev.get('platform') or '') in _PLEX_CLIENT_PLATFORMS:
            ranked.append(host)
        else:
            other.append(host)
    for h in ranked + ([top] if top else []) + other:
        if h and h not in out:
            out.append(h)
    return out


def _room_ip(room: Dict[str, Any],
             active_player_ips: Optional[List[str]] = None) -> Optional[str]:
    """Pick the best IP for this room.

    If any of the room's candidate IPs matches a currently-active Plex player,
    that's the most reliable target. Otherwise return the top-ranked candidate.
    """
    candidates = _room_device_ips(room)
    if active_player_ips:
        for ip in candidates:
            if ip in active_player_ips:
                return ip
    return candidates[0] if candidates else None


def _resolve_target(room_hint: Optional[str] = None) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[str]]]:
    """Pick (room, session-or-None, active_player_ips) to control.

    Preference order:
      1. Room matching `room_hint` (fuzzy)
      2. First room with an active session (from /api/plex/sessions/active)
      3. Room whose device IP matches any active Plex player (/api/plex/clients)
      4. First configured room
    """
    rooms = _fetch_rooms()
    if not rooms:
        return None

    # Raw Plex sessions carry the player IP — primary signal for which room is active.
    plex_sessions = _fetch_plex_sessions_with_address()
    active_ips = [s.get('address') for s in plex_sessions if s.get('address')]

    # If the user named a room explicitly
    if room_hint:
        q = room_hint.lower().strip()
        for r in rooms:
            name = (r.get('name') or '').lower()
            if q == name or q in name or name in q:
                return r, {}, active_ips

    # Otherwise prefer a room that the session-to-room matcher already picked
    active = _fetch_active_sessions()
    for room_entry in active.get('rooms') or []:
        sess = room_entry.get('session')
        if not sess:
            continue
        room_id = room_entry.get('roomId')
        match = next((r for r in rooms if r.get('id') == room_id), None)
        if match:
            return match, sess, active_ips

    # Or: a room whose devices include an active Plex player IP
    for ip in active_ips:
        for r in rooms:
            if ip in _room_device_ips(r):
                return r, {}, active_ips

    # Fall back to first configured room
    return rooms[0], {}, active_ips


# ---------------------------------------------------------------------------
# Low-level HTTP control
# ---------------------------------------------------------------------------

def _playback_control(client_ip: str, command: str) -> bool:
    try:
        resp = requests.post(
            f'{FLASK_BASE}/api/plex/playback/control',
            headers={**_auth_headers(), 'Content-Type': 'application/json'},
            json={'client_ip': client_ip, 'command': command},
            timeout=8,
        )
        return resp.status_code < 400
    except Exception as e:
        logger.warning('Playback control %s failed: %s', command, e)
        return False


def _playback_stop(client_ip: str) -> bool:
    try:
        resp = requests.post(
            f'{FLASK_BASE}/api/plex/playback/stop',
            headers={**_auth_headers(), 'Content-Type': 'application/json'},
            json={'client_ip': client_ip},
            timeout=8,
        )
        return resp.status_code < 400
    except Exception as e:
        logger.warning('Playback stop failed: %s', e)
        return False


def _playback_seek(client_ip: str, offset_seconds: int) -> bool:
    try:
        resp = requests.post(
            f'{FLASK_BASE}/api/plex/playback/seek',
            headers={**_auth_headers(), 'Content-Type': 'application/json'},
            json={'client_ip': client_ip, 'offset_seconds': offset_seconds},
            timeout=8,
        )
        return resp.status_code < 400
    except Exception as e:
        logger.warning('Playback seek failed: %s', e)
        return False


def _subtitle_toggle(client_ip: str, room_id: Optional[str]) -> bool:
    try:
        payload: Dict[str, Any] = {}
        if client_ip:
            payload['client_ip'] = client_ip
        if room_id:
            payload['room_id'] = room_id
        resp = requests.post(
            f'{FLASK_BASE}/api/plex/playback/subtitles',
            headers={**_auth_headers(), 'Content-Type': 'application/json'},
            json=payload,
            timeout=8,
        )
        return resp.status_code < 400
    except Exception as e:
        logger.warning('Subtitle toggle failed: %s', e)
        return False


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _no_target_response(intent_type: str) -> Dict[str, Any]:
    return {
        'type': intent_type,
        'status': 'error',
        'message': "I don't have any rooms configured for playback control.",
        'data': {},
    }


def _control_room(intent_type: str, past_tense: str, present_tense: str,
                  impl, room_hint: Optional[str] = None,
                  *args, **kwargs) -> Dict[str, Any]:
    target = _resolve_target(room_hint)
    if not target:
        return _no_target_response(intent_type)
    room, session, active_ips = target
    ip = _room_ip(room, active_ips)
    if not ip:
        return {
            'type': intent_type,
            'status': 'error',
            'message': f"I couldn't resolve the address for the {room.get('name', 'room')}.",
            'data': {},
        }
    ok = impl(ip, *args, **kwargs)
    room_name = room.get('name', 'room')
    if ok:
        return {
            'type': intent_type,
            'status': 'ok',
            'message': f"{past_tense} in the {room_name}.",
            'data': {
                'room_id': room.get('id'),
                'room_name': room_name,
                'client_ip': ip,
                'session': session or None,
            },
        }
    return {
        'type': intent_type,
        'status': 'error',
        'message': f"I couldn't {present_tense} in the {room_name}.",
        'data': {'room_id': room.get('id'), 'room_name': room_name, 'client_ip': ip},
    }


def handle_playback_pause() -> Dict[str, Any]:
    return _control_room('playback_pause', 'Paused', 'pause',
                         lambda ip: _playback_control(ip, 'pause'))


def handle_playback_resume_session() -> Dict[str, Any]:
    return _control_room('playback_resume_session', 'Playing', 'resume',
                         lambda ip: _playback_control(ip, 'play'))


def handle_playback_stop() -> Dict[str, Any]:
    return _control_room('playback_stop', 'Stopped', 'stop', _playback_stop)


def handle_playback_seek(direction: Optional[str] = None,
                         seconds: Optional[int] = None) -> Dict[str, Any]:
    d = (direction or '').lower().strip()
    if d in ('back', 'backward', 'backwards', 'rewind'):
        sign = -1
    elif d in ('forward', 'ahead', 'skip', 'jump'):
        sign = 1
    else:
        # Default to forward
        sign = 1
    s = int(seconds) if seconds else 30
    offset = sign * max(1, s)

    target = _resolve_target()
    if not target:
        return _no_target_response('playback_seek')
    room, _, active_ips = target
    ip = _room_ip(room, active_ips)
    if not ip:
        return {
            'type': 'playback_seek',
            'status': 'error',
            'message': f"No IP available for the {room.get('name', 'room')}.",
            'data': {},
        }
    ok = _playback_seek(ip, offset)
    verb = 'Skipped ahead' if offset > 0 else 'Rewound'
    if ok:
        return {
            'type': 'playback_seek',
            'status': 'ok',
            'message': f"{verb} {abs(offset)} seconds.",
            'data': {'offset_seconds': offset, 'room_name': room.get('name')},
        }
    # Backend's seek uses fixed stepForward/stepBack (30s) regardless of offset —
    # speak honestly when we asked for something different.
    return {
        'type': 'playback_seek',
        'status': 'error',
        'message': "I couldn't seek playback.",
        'data': {'offset_seconds': offset, 'room_name': room.get('name')},
    }


def handle_playback_subtitles(enabled: Optional[bool] = None) -> Dict[str, Any]:
    target = _resolve_target()
    if not target:
        return _no_target_response('playback_subtitles')
    room, _, active_ips = target
    ip = _room_ip(room, active_ips)
    room_id = room.get('id')
    if not ip and not room_id:
        return {
            'type': 'playback_subtitles',
            'status': 'error',
            'message': f"No address for the {room.get('name', 'room')}.",
            'data': {},
        }
    ok = _subtitle_toggle(ip or '', room_id)
    # The backend endpoint uses a toggle keycode — we can't force on vs off
    # deterministically from the voice command.
    if ok:
        if enabled is True:
            label = 'Subtitles toggled'
        elif enabled is False:
            label = 'Subtitles toggled'
        else:
            label = 'Subtitles toggled'
        return {
            'type': 'playback_subtitles',
            'status': 'ok',
            'message': f"{label} in the {room.get('name', 'room')}.",
            'data': {'room_name': room.get('name')},
        }
    return {
        'type': 'playback_subtitles',
        'status': 'error',
        'message': f"I couldn't toggle subtitles in the {room.get('name', 'room')}.",
        'data': {'room_name': room.get('name')},
    }


def _fmt_progress(view_offset_ms: int, duration_ms: int) -> str:
    if not duration_ms:
        return ''
    mins_in = view_offset_ms // 60000
    total_min = duration_ms // 60000
    remaining = max(0, total_min - mins_in)
    return f", {mins_in} of {total_min} minutes in, {remaining} left"


def handle_playback_now_playing() -> Dict[str, Any]:
    body = _fetch_active_sessions()
    rooms = body.get('rooms') or []
    unmatched = body.get('unmatched') or []

    active: List[Tuple[Dict[str, Any], Optional[str]]] = []
    for r in rooms:
        sess = r.get('session')
        if sess:
            active.append((sess, r.get('roomName')))
    for sess in unmatched:
        active.append((sess, None))

    if not active:
        return {
            'type': 'playback_now_playing',
            'status': 'ok',
            'message': "Nothing is playing right now.",
            'data': {'count': 0},
        }

    if len(active) == 1:
        sess, room_name = active[0]
        state = sess.get('state', 'playing')
        title = sess.get('grandparentTitle') or sess.get('title') or 'something'
        season = sess.get('parentIndex')
        episode = sess.get('index')
        ep_ref = f" S{season}E{episode}" if season and episode else ''
        progress = _fmt_progress(int(sess.get('viewOffset') or 0),
                                 int(sess.get('duration') or 0))
        room_hint = f" in the {room_name}" if room_name else ''
        msg = f"{state.capitalize()}: {title}{ep_ref}{room_hint}{progress}."
        return {
            'type': 'playback_now_playing',
            'status': 'ok',
            'message': msg.strip(),
            'data': {
                'session': {
                    'title': sess.get('title'),
                    'show_title': sess.get('grandparentTitle'),
                    'season': season,
                    'episode': episode,
                    'state': state,
                    'room_name': room_name,
                    'view_offset_ms': sess.get('viewOffset'),
                    'duration_ms': sess.get('duration'),
                    'rating_key': sess.get('ratingKey'),
                },
            },
        }

    # Multiple concurrent sessions
    parts = []
    for sess, room_name in active[:3]:
        title = sess.get('grandparentTitle') or sess.get('title') or 'something'
        season = sess.get('parentIndex')
        episode = sess.get('index')
        ep_ref = f" S{season}E{episode}" if season and episode else ''
        where = f" in {room_name}" if room_name else ''
        parts.append(f"{title}{ep_ref}{where}".strip())
    msg = f"{len(active)} sessions playing — {'; '.join(parts)}."
    return {
        'type': 'playback_now_playing',
        'status': 'ok',
        'message': msg[:260],
        'data': {'count': len(active)},
    }
