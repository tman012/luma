"""v224 Rooms & Players (Taylor's Option A, room first): Settings > Rooms.

Built on prod's ``devices.rooms`` (room -> devices[] -> services[]). A room here has one player,
its first device; any other devices and services in the room are kept untouched. The default room
is ``devices.defaultRoomId`` (prod already clears it when that room is deleted); when unset or
stale the first room is the default, as prod's ``defaultPlaybackTarget`` always was.

Routes (reads: any signed-in user; writes: the owner signed in with a real session):
    GET    /api/rooms               rooms with their player, wake method and online state
    GET    /api/rooms/players       players Plex can see now (PMS /clients) plus the configured ones
    POST   /api/rooms               {name, player, wake?}
    PUT    /api/rooms/<id>          {name?, player?, wake?, host?, port?, mac?, harmony_hub?, harmony_activity?, default?}
    DELETE /api/rooms/<id>

Nothing here starts playback, wakes, pairs with or connects to a device; the only outside call is
the read-only PMS /clients list (``clients_fn``), which tests replace. No Home Assistant: a stored
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
         can_play: Optional[Callable] = None, stop_fn: Optional[Callable] = None) -> None:
    _cfg.update(settings=settings, get_current_user=get_current_user, signed_in_owner=signed_in_owner,
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
            'harmony_hub': room.get('harmonyHubIp') or '', 'player': player}


def view() -> Dict[str, Any]:
    rooms = _rooms()
    clients = _clients() if rooms else []
    d = default_room_id(rooms)
    return {'rooms': [view_room(r, d, clients) for r in rooms], 'default_room_id': d}


def players() -> List[Dict[str, Any]]:
    """Plex players online now, then configured players Plex can't see (Offline)."""
    out, seen = [], set()
    for c in _clients():
        mid = str(c.get('machineIdentifier') or '')
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({'machine_id': mid, 'name': str(c.get('name') or c.get('title') or 'Player'),
                    'product': str(c.get('product') or ''), 'platform': str(c.get('platform') or ''),
                    'host': str(c.get('address') or ''), 'online': True})
    for r in _rooms():
        for d in r.get('devices') or []:
            mid = d.get('machineId') or ''
            key = mid or f"host:{d.get('host')}"
            if key in seen or not (mid or d.get('host')):
                continue
            seen.add(key)
            out.append({'machine_id': mid, 'name': d.get('name') or 'Player', 'product': d.get('product') or '',
                        'platform': d.get('platform') or '', 'host': d.get('host') or '', 'online': False})
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
    if not isinstance(body, dict):
        raise Invalid({'_': 'Invalid'})
    s = _cfg['settings']
    name = _name(body.get('name'), _rooms())
    p = _player(body.get('player'))
    wake = _wake_in(body.get('wake') or ('adb' if p['platform'] in ('android_tv', 'chromecast_gtv') and p['host'] else 'none'))
    dev = {'id': uuid.uuid4().hex[:8], **p, 'connectPort': 5555 if wake == 'adb' else None, 'pairPort': None, 'paired': False,
           'macAddress': '', 'powerMethod': _POWER[wake], 'harmonyActivity': '',
           'services': [{'id': uuid.uuid4().hex[:8], 'type': 'plex'}]}
    room = s.add_room({'name': name, 'useHarmonyHub': wake == 'harmony', 'harmonyHubIp': '', 'harmonyVolumeDevice': '',
                       'devices': [dev]})
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
    dev = dict(devs[0]) if devs else {'id': uuid.uuid4().hex[:8], 'services': [{'id': uuid.uuid4().hex[:8], 'type': 'plex'}]}
    dchanged = False
    if 'player' in body:
        p = _player(body['player'])
        if p['machineId'] != dev.get('machineId') or p['host'] != dev.get('host'):
            dev['paired'] = False                    # another player: pair again
        dev.update(p)
        dchanged = True
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
    return _reader() or jsonify(view())


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
