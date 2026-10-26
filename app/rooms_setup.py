"""v224 Rooms & Players (Taylor's Option A, room first): Settings > Rooms.

Built on prod's ``devices.rooms`` (room -> devices[] -> services[]). A room here has one player,
its first device; any other devices and services in the room are kept untouched. The default room
is ``devices.defaultRoomId`` (prod already clears it when that room is deleted); when unset or
stale the first room is the default, as prod's ``defaultPlaybackTarget`` always was.

v272 (Taylor): a room is created empty. Pair is part of Add device — the player is persisted only
after ADB Pair succeeds (Allow on the TV). Offline orphans are never saved from a failed Pair.

Routes (reads: any signed-in user; writes: the owner signed in with a real session):
    GET    /api/rooms               rooms with their player, wake method and online state
    GET    /api/rooms/players       players Plex can see now (PMS /clients only; no Offline seeds)
    POST   /api/rooms               {name} — empty room (no player)
    PUT    /api/rooms/<id>          {name?, wake?, host?, port?, mac?, harmony_hub?, harmony_activity?, default?}
                                    (player changes go through Pair / connect)
    DELETE /api/rooms/<id>
    POST   /api/rooms/<id>/connect  Pair (owner): optional player body adds the device only on Pair
                                    success; re-pair uses the stored host. {pair_port, pair_code} runs
                                    prod's `adb pair` first.

Nothing else here starts playback or wakes a device; the only outside call besides Pair is the
read-only PMS /clients list (``clients_fn``), which tests replace. No Home Assistant: a stored
``powerMethod: homeassistant`` is shown as None and is left as is unless the wake method is changed.
"""
import copy
import ipaddress
import logging
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
bp = Blueprint('rooms_setup', __name__)

WAKES = ('adb', 'wol', 'harmony', 'none')
_POWER = {'adb': 'adb', 'wol': 'wol', 'harmony': 'harmony', 'none': 'none'}
MAX_NAME = 40
_HOST_RE = re.compile(r'^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')
_MAC_RE = re.compile(r'^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$')

_cfg: Dict[str, Any] = {}


def init(*, settings, get_current_user: Callable, signed_in_owner: Callable, clients_fn: Optional[Callable] = None,
         can_play: Optional[Callable] = None, stop_fn: Optional[Callable] = None,
         pair_fn: Optional[Callable] = None, wait_fn: Optional[Callable] = None) -> None:
    _cfg.update(pair_fn=pair_fn, wait_fn=wait_fn, settings=settings, get_current_user=get_current_user, signed_in_owner=signed_in_owner,
                clients_fn=clients_fn or _plex_clients, can_play=can_play or (lambda u: bool(u)), stop_fn=stop_fn or _plex_stop)


def _plex_stop(player: Dict[str, Any]) -> bool:
    """v225 Change (PM): stop what the room's player is playing, through the Plex server (never the player's own
    address), matched by machine identifier, else address. A player Plex doesn't list is left alone."""
    s = _cfg['settings']
    url, token = s.get('plex.url'), s.get('plex.token')
    if not url or not token:
        return False
    from app.plex_client_service import _plex_server
    server = _plex_server(url, token)
    for c in server.clients():
        if (player.get('machineId') and c.machineIdentifier == player['machineId']) or \
                (not player.get('machineId') and player.get('host') and getattr(c, 'address', '') == player['host']):
            c.proxyThroughServer(True, server)
            c.stop()
            return True
    return False


def _plex_clients() -> List[Dict[str, Any]]:
    """Players advertising to the Plex server now (read-only PMS /clients)."""
    s = _cfg['settings']
    url, token = s.get('plex.url'), s.get('plex.token')
    if not url or not token:
        return []
    from app.plex_client_service import get_active_clients
    return get_active_clients(url, token) or []


def _clients() -> List[Dict[str, Any]]:
    try:
        return [c for c in (_cfg['clients_fn']() or []) if isinstance(c, dict)]
    except Exception:  # noqa: BLE001 - Plex unreachable: everything reads Offline
        logger.warning('Rooms: Plex players unavailable')
        return []


# ---------------------------------------------------------------- model

def nested(r: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """A stored room in prod's nested shape; a legacy flat room (ip/adb_port, no devices list) becomes
    one Android TV device, as GET /api/settings/rooms has always shown it."""
    if isinstance(r.get('devices'), list):
        return r
    rid = r.get('id') or f'room-{idx}'
    host = r.get('host') or r.get('ip') or ''
    return {**{k: v for k, v in r.items() if k not in ('ip', 'adb_port', 'type', 'plex_client_port')},
            'id': rid, 'name': r.get('name') or f'Room {idx + 1}',
            'devices': [{'id': f'{rid}-dev-0', 'name': r.get('name') or f'Room {idx + 1}',
                         'platform': r.get('platform') or r.get('type') or 'android_tv', 'host': host,
                         'connectPort': r.get('connectPort') or r.get('adb_port') or 5555,
                         'machineId': r.get('plex_machine_id') or '', 'paired': bool(r.get('paired')),
                         'powerMethod': 'adb', 'services': [{'id': f'{rid}-svc-plex', 'type': 'plex'}]}]}


def _rooms() -> List[Dict[str, Any]]:
    return [nested(copy.deepcopy(r), i) for i, r in enumerate(_cfg['settings'].get('devices.rooms') or []) if isinstance(r, dict)]


def default_room_id(rooms: Optional[List[Dict[str, Any]]] = None) -> str:
    rooms = _rooms() if rooms is None else rooms
    want = _cfg['settings'].get('devices.defaultRoomId') or ''
    ids = [r['id'] for r in rooms]
    return want if want in ids else (ids[0] if ids else '')


def _wake(room: Dict[str, Any], dev: Dict[str, Any]) -> str:
    pm = (dev.get('powerMethod') or '').lower()
    if room.get('useHarmonyHub') or pm == 'harmony':
        return 'harmony'
    if pm in ('wol', 'wake_on_lan', 'wakeonlan'):
        return 'wol'
    if pm == 'none':
        return 'none'
    if pm == 'adb' or (pm == '' and dev.get('platform') in ('android_tv', 'shield', 'chromecast_gtv') and dev.get('host')):
        return 'adb'
    return 'none'


def _online(dev: Dict[str, Any], clients: List[Dict[str, Any]]) -> bool:
    mid, host = dev.get('machineId') or '', dev.get('host') or ''
    for c in clients:
        if mid and c.get('machineIdentifier') == mid:
            return True
        if host and c.get('address') == host:
            return True
    return False


def view_room(room: Dict[str, Any], default_id: str, clients: List[Dict[str, Any]]) -> Dict[str, Any]:
    devs = [d for d in room.get('devices') or [] if isinstance(d, dict)]
    dev = devs[0] if devs else None
    player = None
    if dev:
        player = {'id': dev.get('id'), 'name': dev.get('name') or '', 'product': dev.get('product') or '',
                  'platform': dev.get('platform') or '', 'machine_id': dev.get('machineId') or '',
                  'host': dev.get('host') or '', 'port': dev.get('connectPort'), 'mac': dev.get('macAddress') or '',
                  'harmony_activity': dev.get('harmonyActivity') or '', 'paired': bool(dev.get('paired')),
                  'online': _online(dev, clients), 'wake': _wake(room, dev)}
    return {'id': room['id'], 'name': room.get('name') or '', 'default': room['id'] == default_id,
            'online': bool(player and player['online']), 'harmony_hub': room.get('harmonyHubIp') or '', 'player': player}


PUBLIC_ROOM_KEYS = ('id', 'name', 'default', 'online')


def public_view(full: Dict[str, Any]) -> Dict[str, Any]:
    """v231 (Reviewer): what a signed-in non-owner sees: each room's id, name, default and online only;
    no player, host, port, MAC, Harmony hub or any other device detail."""
    return {'rooms': [{k: r[k] for k in PUBLIC_ROOM_KEYS} for r in full.get('rooms', [])],
            'default_room_id': full.get('default_room_id')}


def view() -> Dict[str, Any]:
    rooms = _rooms()
    clients = _clients() if rooms else []
    d = default_room_id(rooms)
    return {'rooms': [view_room(r, d, clients) for r in rooms], 'default_room_id': d}


def players() -> List[Dict[str, Any]]:
    """Plex players online now (PMS /clients). v272: never list configured Offline hosts — that seeded
    orphan ATV rows (e.g. 192.168.1.108) into Add device before Pair succeeded."""
    out, seen = [], set()
    for c in _clients():
        mid = str(c.get('machineIdentifier') or '')
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({'machine_id': mid, 'name': str(c.get('name') or c.get('title') or 'Player'),
                    'product': str(c.get('product') or ''), 'platform': str(c.get('platform') or ''),
                    'host': str(c.get('address') or ''), 'online': True})
    return out


# ---------------------------------------------------------------- validation

class Invalid(Exception):
    def __init__(self, errors: Dict[str, str]):
        super().__init__('invalid')
        self.errors = errors


def _name(v: Any, rooms: List[Dict[str, Any]], own: str = '') -> str:
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > MAX_NAME:
        raise Invalid({'name': 'Enter a name'})
    v = ' '.join(v.split())
    if any(r['id'] != own and (r.get('name') or '').casefold() == v.casefold() for r in rooms):
        raise Invalid({'name': 'A room has this name'})
    return v


def _host(v: Any) -> str:
    if v in (None, ''):
        return ''
    if not isinstance(v, str):
        raise Invalid({'host': 'Enter an IP address'})
    v = v.strip()
    try:
        ipaddress.ip_address(v)
        return v
    except ValueError:
        if _HOST_RE.match(v):
            return v
    raise Invalid({'host': 'Enter an IP address'})


def _port(v: Any) -> Optional[int]:
    if v in (None, ''):
        return None
    if isinstance(v, bool) or not isinstance(v, (int, str)) or not str(v).isdigit() or not 1 <= int(v) <= 65535:
        raise Invalid({'port': 'Enter a port'})
    return int(v)


def _mac(v: Any) -> str:
    if v in (None, ''):
        return ''
    if not isinstance(v, str) or not _MAC_RE.match(v.strip()):
        raise Invalid({'mac': 'Enter a MAC address'})
    return v.strip().upper().replace('-', ':')


def _text(v: Any, key: str, n: int = 80) -> str:
    if v in (None, ''):
        return ''
    if not isinstance(v, str) or len(v) > n:
        raise Invalid({key: 'Invalid'})
    return v.strip()


def _player(v: Any) -> Dict[str, Any]:
    if not isinstance(v, dict):
        raise Invalid({'player': 'Pick a player'})
    out = {'name': _text(v.get('name'), 'player', MAX_NAME * 2), 'machineId': _text(v.get('machine_id'), 'player'),
           'product': _text(v.get('product'), 'player'), 'platform': _text(v.get('platform'), 'player', 40),
           'host': _host(v.get('host'))}
    if not out['machineId'] and not out['host']:
        raise Invalid({'player': 'Pick a player'})
    if not out['name']:
        out['name'] = out['host'] or 'Player'
    if not out['platform']:
        prod = out['product'].lower()
        out['platform'] = 'android_tv' if 'android' in prod or 'shield' in out['name'].lower() else 'network' if out['product'] else 'android_tv'
    elif out['platform'].lower() == 'android':
        out['platform'] = 'android_tv'
    return out


def _wake_in(v: Any) -> str:
    if v not in WAKES:
        raise Invalid({'wake': 'Invalid'})
    return v


# ---------------------------------------------------------------- writes

def create(body: Any) -> Dict[str, Any]:
    """v272: create an empty room only. Players are added through Pair (POST .../connect)."""
    if not isinstance(body, dict):
        raise Invalid({'_': 'Invalid'})
    if body.get('player') not in (None, ''):
        raise Invalid({'player': 'Pair the player to add it'})
    s = _cfg['settings']
    name = _name(body.get('name'), _rooms())
    room = s.add_room({'name': name, 'useHarmonyHub': False, 'harmonyHubIp': '', 'harmonyVolumeDevice': '',
                       'devices': []})
    return room


def update(room_id: str, body: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(body, dict) or not body:
        raise Invalid({'_': 'Invalid'})
    allowed = {'name', 'player', 'wake', 'host', 'port', 'mac', 'harmony_hub', 'harmony_activity', 'default'}
    if set(body) - allowed:
        raise Invalid({'_': 'Invalid'})
    s = _cfg['settings']
    rooms = _rooms()
    room = next((r for r in rooms if r['id'] == room_id), None)
    if room is None:
        return None
    patch: Dict[str, Any] = {}
    if 'name' in body:
        patch['name'] = _name(body['name'], rooms, room_id)
    devs = [d for d in room.get('devices') or [] if isinstance(d, dict)]
    if not devs and any(k in body for k in ('host', 'port', 'mac', 'wake', 'harmony_activity', 'player')):
        raise Invalid({'player': 'Pair the player to add it'})
    dev = dict(devs[0]) if devs else {'id': uuid.uuid4().hex[:8], 'services': [{'id': uuid.uuid4().hex[:8], 'type': 'plex'}]}
    dchanged = False
    if 'player' in body:
        # v272: Add/Change player goes through Pair (POST .../connect) so Offline orphans are never saved.
        raise Invalid({'player': 'Pair the player to add it'})
    if 'host' in body:
        dev['host'] = _host(body['host']); dchanged = True
    if 'port' in body:
        dev['connectPort'] = _port(body['port']); dchanged = True
    if 'mac' in body:
        dev['macAddress'] = _mac(body['mac']); dchanged = True
    if 'harmony_activity' in body:
        dev['harmonyActivity'] = _text(body['harmony_activity'], 'harmony_activity'); dchanged = True
    if 'harmony_hub' in body:
        patch['harmonyHubIp'] = _host(body['harmony_hub'])
        # v285: saving a Hub IP enables direct Harmony for remote keys (wake may already).
        if patch['harmonyHubIp'] and 'wake' not in body:
            patch['useHarmonyHub'] = True
        elif not patch['harmonyHubIp'] and 'wake' not in body:
            patch['useHarmonyHub'] = False
    if 'wake' in body:
        w = _wake_in(body['wake'])
        dev['powerMethod'] = _POWER[w]
        patch['useHarmonyHub'] = w == 'harmony'
        if w == 'adb' and not dev.get('connectPort'):
            dev['connectPort'] = 5555
        dchanged = True
    if 'default' in body and not isinstance(body['default'], bool):
        raise Invalid({'default': 'Invalid'})
    if dchanged:
        dev.setdefault('services', [])
        if not any(isinstance(x, dict) and x.get('type') == 'plex' for x in dev['services']):
            dev['services'] = [*dev['services'], {'id': uuid.uuid4().hex[:8], 'type': 'plex'}]
        patch['devices'] = [dev, *devs[1:]]
    stored = next((r for r in (s.get('devices.rooms') or []) if isinstance(r, dict) and r.get('id') == room_id), None)
    if stored is not None and not isinstance(stored.get('devices'), list):
        patch.setdefault('devices', room['devices'])            # a legacy flat room is written back nested
        for k in ('ip', 'adb_port', 'type', 'plex_client_port'):
            patch[k] = None
    if patch:
        s.update_room(room_id, patch)
        with s._lock:                                   # drop the legacy flat keys once the room is nested
            for r in s._settings.get('devices', {}).get('rooms', []):
                if isinstance(r, dict) and r.get('id') == room_id:
                    for k in ('ip', 'adb_port', 'type', 'plex_client_port'):
                        if k in r and r[k] is None:
                            del r[k]
            s._save()
    if body.get('default') is True:
        _set_default(room_id)
    elif body.get('default') is False and default_room_id() == room_id:
        others = [r['id'] for r in _rooms() if r['id'] != room_id]
        _set_default(others[0] if others else '')
    return next((r for r in _rooms() if r['id'] == room_id), None)


def _set_default(room_id: str) -> None:
    s = _cfg['settings']
    with s._lock:
        s._settings.setdefault('devices', {})['defaultRoomId'] = room_id
        s._save()


def delete(room_id: str) -> bool:
    return bool(_cfg['settings'].delete_room(room_id))


# ---------------------------------------------------------------- routes

def _reader():
    if not _cfg['get_current_user']():
        return jsonify({'error': 'Sign in first', 'sign_in': True}), 401
    return None


def _writer():
    _, err = _cfg['signed_in_owner']()
    return err


@bp.route('/api/rooms', methods=['GET'])
def rooms_get():
    err = _reader()
    if err:
        return err
    _, not_owner = _cfg['signed_in_owner']()
    return jsonify(public_view(view()) if not_owner else view())


@bp.route('/api/rooms/players', methods=['GET'])
def rooms_players():
    return _writer() or jsonify({'players': players()})


def _json() -> Any:
    body = request.get_json(silent=True, force=True) if request.get_data(cache=True).strip() else {}
    return body


@bp.route('/api/rooms', methods=['POST'])
def rooms_post():
    err = _writer()
    if err:
        return err
    try:
        create(_json())
    except Invalid as e:
        return jsonify({'errors': e.errors}), 400
    return jsonify(view()), 201


@bp.route('/api/rooms/<room_id>', methods=['PUT'])
def rooms_put(room_id):
    err = _writer()
    if err:
        return err
    try:
        room = update(room_id, _json())
    except Invalid as e:
        return jsonify({'errors': e.errors}), 400
    if room is None:
        return jsonify({'error': 'Room not found'}), 404
    return jsonify(view())


@bp.route('/api/rooms/<room_id>/stop', methods=['POST'])
def rooms_stop(room_id):
    """v225: Change moved playback to another room and it started there; stop the first room's player.
    Who may play may stop (the play route's Plex-approved check). Stops playback only; never the library."""
    user = _cfg['get_current_user']()
    if not user:
        return jsonify({'error': 'Sign in first', 'sign_in': True}), 401
    if not _cfg['can_play'](user):
        return jsonify({'error': 'Not allowed'}), 403
    room = next((r for r in _rooms() if r['id'] == room_id), None)
    dev = next((d for d in (room or {}).get('devices') or [] if isinstance(d, dict)), None)
    if room is None or dev is None:
        return jsonify({'error': 'Room not found'}), 404
    try:
        ok = bool(_cfg['stop_fn'](dev))
    except Exception:  # noqa: BLE001 - Plex or the player unreachable: nothing to stop from here
        logger.warning('Rooms: stop in %s failed', room_id)
        ok = False
    return jsonify({'stopped': ok})


@bp.route('/api/rooms/<room_id>', methods=['DELETE'])
def rooms_delete(room_id):
    err = _writer()
    if err:
        return err
    if not delete(room_id):
        return jsonify({'error': 'Room not found'}), 404
    return jsonify(view())


PAIR_WAIT_S = 30.0
ADB_PLATFORMS = frozenset({'android_tv', 'shield', 'chromecast_gtv', 'android'})


def _needs_adb(platform: str, host: str) -> bool:
    """Android TV / Shield with a host: Pair (ADB Allow) is required before persist."""
    return bool(host) and (platform or '').lower() in ADB_PLATFORMS


def _pair_response(ok: bool, state: str, hint: str, detail: str = ''):
    """Always a JSON body (live :5002 logged connect as 200 with 0 bytes once — keep Content-Type JSON)."""
    body = {'ok': bool(ok), 'state': state or 'failed', 'hint': hint or 'unknown'}
    if detail:
        body['detail'] = detail
    return jsonify(body)


def _persist_paired_device(room: Dict[str, Any], room_id: str, p: Dict[str, Any], port: int,
                          pair_port: Optional[int] = None) -> Dict[str, Any]:
    """Write the room's first device after Pair succeeded. Replaces device[0]; keeps extras."""
    devs = [d for d in room.get('devices') or [] if isinstance(d, dict)]
    wake = 'adb' if _needs_adb(p.get('platform') or '', p.get('host') or '') else 'none'
    if wake == 'none' and p.get('platform'):
        wake = 'adb' if (p.get('host') and (p.get('platform') or '').lower() in ADB_PLATFORMS) else 'none'
    base = dict(devs[0]) if devs else {'id': uuid.uuid4().hex[:8],
                                       'services': [{'id': uuid.uuid4().hex[:8], 'type': 'plex'}]}
    base.update(p)
    base['connectPort'] = port
    base['paired'] = True
    base['powerMethod'] = base.get('powerMethod') or _POWER['adb' if p.get('host') else 'none']
    if _needs_adb(base.get('platform') or '', base.get('host') or ''):
        base['powerMethod'] = 'adb'
        base['connectPort'] = port or 5555
    if pair_port:
        base['pairPort'] = pair_port
    base.setdefault('macAddress', '')
    base.setdefault('harmonyActivity', '')
    base.setdefault('services', [])
    if not any(isinstance(x, dict) and x.get('type') == 'plex' for x in base['services']):
        base['services'] = [*base['services'], {'id': uuid.uuid4().hex[:8], 'type': 'plex'}]
    _cfg['settings'].update_room(room_id, {'devices': [base, *devs[1:]], 'useHarmonyHub': False})
    return base


@bp.route('/api/rooms/<room_id>/connect', methods=['POST'])
def rooms_connect(room_id):
    """v272 Pair (signed-in owner). Optional player body = Add device: Pair first, persist only on
    success. Re-pair uses the stored host. Prod ADB: `adb connect` → Allow on TV (wait PAIR_WAIT_S);
    Google TV sends pair_port + pair_code first. Key pair in LUMA_ADB_HOME, 0600; code never logged."""
    err = _writer()
    if err:
        return err
    from app import adb_utils
    rooms = _rooms()
    room = next((r for r in rooms if r['id'] == room_id), None)
    if room is None:
        return jsonify({'error': 'Room not found'}), 404
    body = _json()
    body = body if isinstance(body, dict) else {}
    existing = next((d for d in (room.get('devices') or []) if isinstance(d, dict)), None)
    incoming = None
    if body.get('player') is not None or body.get('host') or body.get('machine_id'):
        raw = body.get('player') if isinstance(body.get('player'), dict) else {
            'host': body.get('host'), 'machine_id': body.get('machine_id'), 'name': body.get('name'),
            'product': body.get('product'), 'platform': body.get('platform')}
        try:
            incoming = _player(raw)
        except Invalid as e:
            return jsonify({'errors': e.errors, 'ok': False, 'state': 'failed', 'hint': 'unreachable'}), 400
    if incoming is None and existing is None:
        return _pair_response(False, 'failed', 'unreachable', 'Add a player IP or pick a Plex player'), 400

    p = incoming or {
        'name': existing.get('name') or '', 'machineId': existing.get('machineId') or '',
        'product': existing.get('product') or '', 'platform': existing.get('platform') or 'android_tv',
        'host': existing.get('host') or ''}
    host = (p.get('host') or '').strip()
    try:
        port = int(body.get('port') or (existing or {}).get('connectPort') or 5555)
    except (TypeError, ValueError):
        port = 5555
    if not 1 <= port <= 65535:
        return _pair_response(False, 'failed', 'wrong_port', 'Enter a port'), 400

    # Non-ADB players (Apple TV, etc.): persist on Add without ADB.
    if not _needs_adb(p.get('platform') or '', host):
        if not (p.get('machineId') or host):
            return _pair_response(False, 'failed', 'unreachable'), 400
        _persist_paired_device(room, room_id, p, port)
        logger.info('Rooms: pair %s -> connected (no ADB)', room_id)
        return _pair_response(True, 'connected', 'connected')

    if not adb_utils.adb_target(host, port, {host.lower()}):
        return _pair_response(False, 'failed', 'unreachable', 'Enter an IP address'), 400

    # adb_disabled is enforced inside wait_for_authorization (real path). Tests inject wait_fn.

    code = str(body.get('pair_code') or '').strip()
    pport = None
    if code:
        try:
            pport = int(body.get('pair_port') or 0)
        except (TypeError, ValueError):
            pport = 0
        if not (1 <= pport <= 65535) or not re.fullmatch(r'\d{6}', code):
            return _pair_response(False, 'failed', 'pair_failed'), 400
        pr = (_cfg.get('pair_fn') or adb_utils.adb_pair)(host, pport, code)
        if not pr.get('ok'):
            return _pair_response(False, 'failed', pr.get('hint', 'pair_failed'), pr.get('detail', ''))

    res = (_cfg.get('wait_fn') or adb_utils.wait_for_authorization)(host, int(port), timeout=PAIR_WAIT_S)
    if res.get('ok'):
        _persist_paired_device(room, room_id, p, int(port), pair_port=pport)
    else:
        # v272: never persist / keep an Offline orphan from a failed Add. Re-pair of an existing
        # device leaves it unpaired (paired flag cleared only when we had written success before).
        if incoming is not None and existing is None:
            pass  # room stays empty
        elif incoming is not None and existing is not None:
            # Failed Change: do not swap in the new host
            pass
    logger.info('Rooms: pair %s -> %s hint=%s', room_id, res.get('state'), res.get('hint'))
    return _pair_response(bool(res.get('ok')), res.get('state', 'failed'), res.get('hint', 'unknown'),
                          res.get('detail', ''))
