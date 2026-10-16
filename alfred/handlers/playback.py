"""Playback handler — search Plex and play on a room device."""

import logging
import os
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

import requests

from config import PLEX_URL, PLEX_TOKEN
from handlers.title_match import best_match

logger = logging.getLogger(__name__)

FLASK_BASE = 'http://localhost:5001'
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')

# Quality keywords → preferred Plex library section IDs (movies)
# Ordered by preference: first match wins
_QUALITY_SECTIONS_MOVIE = {
    '4k':       [4],           # "4K Movies"
    '2160':     [4],
    'uhd':      [4],
    'remux':    [7],           # "Full Rips"
    'full rip': [7],
    'fullrip':  [7],
    '1080':     [2, 1],        # "Movies", "Recently Released"
    'hd':       [2, 1],
    '3d':       [8],           # "3D"
    'highest':  [4, 7, 2, 1],  # Try 4K → Full Rips → Movies → Recent
    'best':     [4, 7, 2, 1],
}

_QUALITY_SECTIONS_TV = {
    '4k':       [13],          # "4K TV"
    '2160':     [13],
    'uhd':      [13],
    'remux':    [9],           # "Remux TV"
    '1080':     [3],           # "TV Shows"
    'hd':       [3],
    'highest':  [13, 9, 3],
    'best':     [13, 9, 3],
}

# Default section search order (all movie sections, then TV)
_DEFAULT_MOVIE_SECTIONS = [4, 7, 2, 1]  # 4K, Full Rips, Movies, Recent
_DEFAULT_TV_SECTIONS = [13, 9, 3]        # 4K TV, Remux TV, TV Shows


def _plex_search(query: str, media_type: int = 1, limit: int = 5,
                 section_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Search Plex library directly via XML API.

    media_type: 1=movie, 2=show, 4=episode
    section_id: search within a specific library section
    """
    if not PLEX_URL or not PLEX_TOKEN:
        return []

    try:
        if section_id:
            url = f'{PLEX_URL}/library/sections/{section_id}/search'
        else:
            url = f'{PLEX_URL}/search'

        resp = requests.get(
            url,
            params={'query': query, 'type': media_type, 'limit': limit},
            headers={
                'X-Plex-Token': PLEX_TOKEN,
                'Accept': 'application/xml',
            },
            timeout=10,
        )
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        results = []
        for video in root.iter('Video') if media_type != 2 else root.iter('Directory'):
            results.append({
                'title': video.get('title', ''),
                'year': int(video.get('year', 0) or 0),
                'rating_key': video.get('ratingKey', ''),
                'type': video.get('type', ''),
                'section_id': section_id,
            })
        return results
    except Exception as e:
        logger.error('Plex search failed: %s', e)
        return []


def _search_sections(query: str, section_ids: List[int],
                     media_type: int) -> List[Dict[str, Any]]:
    """Search across multiple Plex sections in order, return first match."""
    for sid in section_ids:
        results = _plex_search(query, media_type=media_type, section_id=sid)
        if results:
            return results
    return []


def _get_rooms() -> List[Dict[str, Any]]:
    """Fetch configured rooms from Flask backend."""
    try:
        headers = {}
        if SERVICE_API_KEY:
            headers['X-API-Key'] = SERVICE_API_KEY
        resp = requests.get(
            f'{FLASK_BASE}/api/settings/rooms',
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rooms', [])
    except Exception as e:
        logger.error('Failed to fetch rooms: %s', e)
        return []


def _match_room(rooms: List[Dict], room_query: str) -> Optional[Dict]:
    """Fuzzy-match a spoken room name to a configured room."""
    if not rooms:
        return None
    query = room_query.lower().strip()
    # Exact match first
    for room in rooms:
        if room.get('name', '').lower() == query:
            return room
    # Partial match (room name contains query or vice versa)
    for room in rooms:
        name = room.get('name', '').lower()
        if query in name or name in query:
            return room
    return None


def _trigger_play(rating_key: str, room_id: str,
                  resume_offset: int = 0) -> Dict[str, Any]:
    """Call Flask /api/player/play to start playback.

    Luma's /api/player/play owns the full wake sequence (HA power-on, ADB
    reconnect, readiness probe). We just need a long enough timeout to
    cover a cold GTV boot (~60s wake + launch + intent).

    resume_offset (ms) is forwarded to Luma. Non-zero causes Plex to pick
    up mid-stream — used when the item is in the user's Continue Watching
    list so "play Chosen" resumes instead of starting over.
    """
    try:
        headers = {'Content-Type': 'application/json'}
        if SERVICE_API_KEY:
            headers['X-API-Key'] = SERVICE_API_KEY
        body: Dict[str, Any] = {'rating_key': rating_key, 'room_id': room_id}
        if resume_offset and resume_offset > 0:
            body['resume_offset'] = int(resume_offset)
        resp = requests.post(
            f'{FLASK_BASE}/api/player/play',
            headers=headers,
            json=body,
            timeout=90,
        )
        # Surface Luma's structured error message when available
        if resp.status_code >= 400:
            try:
                body = resp.json()
                return {'error': body.get('message') or body.get('error') or f'HTTP {resp.status_code}'}
            except Exception:
                return {'error': f'HTTP {resp.status_code}'}
        return resp.json()
    except Exception as e:
        logger.error('Play request failed: %s', e)
        return {'error': str(e)}


def _fetch_on_deck() -> list:
    """Fetch the user's Plex Continue Watching list via Luma.

    Returns an empty list on failure — the shortcut is a nice-to-have,
    never a blocker for the fall-through Plex search path.
    """
    try:
        headers = {}
        if SERVICE_API_KEY:
            headers['X-API-Key'] = SERVICE_API_KEY
        resp = requests.get(
            f'{FLASK_BASE}/api/plex/continue_watching',
            headers=headers,
            params={'limit': 50},
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json().get('items', []) or []
    except Exception as e:
        logger.info('Continue Watching lookup skipped (%s)', e)
        return []


def _fuzzy_title_eq(a: str, b: str) -> bool:
    """Loose title comparison: same after stripping articles, punctuation, case."""
    import re as _re

    def _norm(s: str) -> str:
        s = (s or '').lower().strip()
        s = _re.sub(r"^(the|a|an)\s+", "", s)
        s = _re.sub(r"[^\w\s]", "", s)
        s = _re.sub(r"\s+", " ", s).strip()
        return s

    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _find_in_on_deck(title: str, on_deck: list) -> Optional[Dict[str, Any]]:
    """Return the first Continue Watching entry whose title matches.

    For TV episodes, `show_title` is what the user would have said
    ("Chosen"), not the episode title. Movies use `title` directly.
    """
    for item in on_deck:
        if item.get('type') == 'episode':
            if _fuzzy_title_eq(title, item.get('show_title', '')):
                return item
        else:
            if _fuzzy_title_eq(title, item.get('title', '')):
                return item
    return None


def handle_play(title: str, room: Optional[str] = None,
                quality: Optional[str] = None) -> Dict[str, Any]:
    """Search Plex for a title and play it in the specified room.

    quality: optional quality preference (4k, remux, 1080p, highest, etc.)

    Continue-Watching shortcut: before hitting the library search, we
    check Plex's On Deck list. If the spoken title matches something
    the user is mid-way through (a partially-watched movie or the next
    episode of a show they're watching), we play THAT specific rating
    key and pass its view offset so Plex resumes from where they left
    off. Quality-specific asks bypass the shortcut — "play Dune in 4K"
    shouldn't silently pull a 1080p resume session.
    """
    quality_label = ''

    # ── Continue Watching shortcut ─────────────────────────────────────
    on_deck_match: Optional[Dict[str, Any]] = None
    if not quality:
        on_deck = _fetch_on_deck()
        on_deck_match = _find_in_on_deck(title, on_deck)
        if on_deck_match:
            logger.info(
                'On-deck match for "%s": %s %s (offset=%sms, type=%s)',
                title,
                on_deck_match.get('show_title') or on_deck_match.get('title'),
                f"S{on_deck_match.get('season_number')}E{on_deck_match.get('episode_number')}"
                if on_deck_match.get('type') == 'episode' else '',
                on_deck_match.get('view_offset', 0),
                on_deck_match.get('type'),
            )

    # When Continue Watching already has it, skip the library search entirely
    # and use the on-deck entry's specific rating key + resume offset.
    resume_offset_ms = 0
    resume_context_label = ''  # for the spoken confirmation
    if on_deck_match:
        if on_deck_match.get('type') == 'episode':
            rating_key = str(on_deck_match.get('episode_rating_key') or on_deck_match.get('rating_key'))
            match_title = on_deck_match.get('show_title') or title
            media_type = 'show'
            season = on_deck_match.get('season_number')
            episode = on_deck_match.get('episode_number')
            if season and episode:
                resume_context_label = f'season {season} episode {episode}'
        else:
            rating_key = str(on_deck_match.get('rating_key'))
            match_title = on_deck_match.get('title') or title
            media_type = 'movie'
        resume_offset_ms = int(on_deck_match.get('view_offset') or 0)
    else:
        if quality:
            q = quality.lower().strip()
            # Try movie sections for this quality first
            movie_sections = _QUALITY_SECTIONS_MOVIE.get(q)
            tv_sections = _QUALITY_SECTIONS_TV.get(q)
            quality_label = quality

            if movie_sections:
                results = _search_sections(title, movie_sections, media_type=1)
                media_type = 'movie'
                if not results and tv_sections:
                    results = _search_sections(title, tv_sections, media_type=2)
                    media_type = 'show'
            elif tv_sections:
                results = _search_sections(title, tv_sections, media_type=2)
                media_type = 'show'
            else:
                # Unknown quality keyword — fall back to default search
                results = _plex_search(title, media_type=1)
                media_type = 'movie'
                if not results:
                    results = _plex_search(title, media_type=2)
                    media_type = 'show'
                quality_label = ''
        else:
            # No quality specified — search all sections
            results = _plex_search(title, media_type=1)
            media_type = 'movie'
            if not results:
                results = _plex_search(title, media_type=2)
                media_type = 'show'

        if not results:
            qual_msg = f' in {quality}' if quality_label else ''
            return {
                'type': 'playback',
                'status': 'error',
                'message': f"I couldn't find \"{title}\"{qual_msg} in your Plex library.",
                'data': {},
            }

        match = best_match(title, results) or results[0]
        match_title = match['title']
        rating_key = match['rating_key']

    # Resolve the room
    rooms = _get_rooms()

    if room:
        target_room = _match_room(rooms, room)
        if not target_room:
            room_names = ', '.join(r.get('name', '?') for r in rooms) if rooms else 'none configured'
            return {
                'type': 'playback',
                'status': 'error',
                'message': f"I don't know a room called \"{room}\". Available rooms: {room_names}.",
                'data': {},
            }
    elif rooms:
        target_room = rooms[0]
    else:
        return {
            'type': 'playback',
            'status': 'error',
            'message': f"Found \"{match_title}\" but no playback rooms are configured.",
            'data': {},
        }

    room_name = target_room.get('name', 'unknown')
    room_id = target_room.get('id', '')

    play_result = _trigger_play(rating_key, room_id, resume_offset=resume_offset_ms)

    if 'error' in play_result and not play_result.get('success'):
        return {
            'type': 'playback',
            'status': 'error',
            'message': f"Found \"{match_title}\" but couldn't start playback: {play_result.get('error', 'unknown error')}",
            'data': {},
        }

    qual_msg = f' ({quality_label})' if quality_label else ''

    # Shape the spoken confirmation to reflect what actually happened.
    # - Movie mid-stream:   "Resuming <title> in the family room."
    # - Episode mid-stream: "Resuming <show>, season 2 episode 3, in the family room."
    # - Episode next-up (offset=0) from on-deck: "Playing <show>, S2E3, in the family room."
    # - Fresh play:         "Playing <title> in the family room."
    if on_deck_match:
        if resume_offset_ms > 0 and resume_context_label:
            msg = f"Resuming {match_title}, {resume_context_label}, in the {room_name}."
        elif resume_offset_ms > 0:
            msg = f"Resuming {match_title} in the {room_name}."
        elif resume_context_label:
            msg = f"Playing {match_title}, {resume_context_label}, in the {room_name}."
        else:
            msg = f"Playing {match_title} in the {room_name}."
    else:
        msg = f"Playing \"{match_title}\"{qual_msg} in the {room_name}."

    return {
        'type': 'playback',
        'status': 'ok',
        'message': msg,
        'data': {
            'title': match_title,
            'rating_key': rating_key,
            'room': room_name,
            'media_type': media_type,
            'quality': quality_label or None,
            'resume_offset_ms': resume_offset_ms,
            'from_continue_watching': bool(on_deck_match),
        },
    }
