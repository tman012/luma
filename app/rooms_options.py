"""Settings premium pass: owner-only pickers for Settings > Rooms (no raw text entry).

GET /api/rooms/options/ha               {entities: [{id, name}]}  TV-power candidates from Home Assistant
GET /api/rooms/options/harmony?hub=IP   {ok, activities, devices}  labels from a hub already saved on a room (else 400)
GET /api/rooms/<id>/mac                 {mac}                      the room TV's MAC from the ARP table

Read-only: nothing here wakes, plays or sends a command. Writes stay in rooms_setup (PUT /api/rooms/<id>).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from app import rooms_setup
from app.rooms_setup import Invalid, _host, _rooms, _writer

bp = Blueprint('rooms_options', __name__)
_cfg: Dict[str, Any] = rooms_setup._cfg   # shared: settings, signed_in_owner (+ test hooks)


POWER_DOMAINS = ('media_player', 'switch', 'remote')


def _ha_states() -> List[Dict[str, Any]]:
    fn = _cfg.get('ha_states_fn')
    if fn:
        return fn()
    s = _cfg['settings']
    url = (s.get('homeassistant.url') or '').strip()
    token = (s.get('homeassistant.access_token') or '').strip()
    if not url or not token:
        return []
    from app import ha_service
    return ha_service._fetch_all_states(url, token)


def ha_power_entities() -> List[Dict[str, str]]:
    """TV-power candidates from Home Assistant (media_player first, then switch and remote)."""
    out = []
    for st in _ha_states() or []:
        eid = st.get('entity_id') if isinstance(st, dict) else None
        if not isinstance(eid, str) or eid.split('.', 1)[0] not in POWER_DOMAINS:
            continue
        name = ((st.get('attributes') or {}).get('friendly_name') or eid) if isinstance(st.get('attributes'), dict) else eid
        out.append({'id': eid, 'name': str(name)})
    out.sort(key=lambda e: (POWER_DOMAINS.index(e['id'].split('.', 1)[0]), e['name'].lower()))
    return out


def configured_hubs() -> set:
    """Hubs saved on a room in settings (devices.rooms[*].harmonyHubIp); nothing else is contacted."""
    out = set()
    for r in _rooms():
        h = r.get('harmonyHubIp') if isinstance(r, dict) else None
        if isinstance(h, str) and h.strip():
            out.add(h.strip())
    return out


def harmony_options(hub: str) -> Optional[Dict[str, List[str]]]:
    fn = _cfg.get('harmony_config_fn')
    if fn:
        cfg = fn(hub)
    else:
        from app import harmony_service
        cfg = harmony_service.get_config(hub)
    if not isinstance(cfg, dict):
        return None
    labels = lambda xs: [x['label'] for x in xs or [] if isinstance(x, dict) and x.get('label')]  # noqa: E731
    return {'activities': [a for a in labels(cfg.get('activities')) if a != 'PowerOff'], 'devices': labels(cfg.get('devices'))}


def arp_mac(host: str) -> str:
    """The MAC for host from the kernel ARP table ('' when unknown)."""
    fn = _cfg.get('arp_fn')
    text = fn() if fn else None
    if text is None:
        try:
            with open('/proc/net/arp', encoding='utf-8') as f:
                text = f.read()
        except OSError:
            return ''
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == host and parts[3] != '00:00:00:00:00:00':
            return parts[3].upper()
    return ''


@bp.route('/api/rooms/options/ha', methods=['GET'])
def rooms_options_ha():
    return _writer() or jsonify({'entities': ha_power_entities()})


@bp.route('/api/rooms/options/harmony', methods=['GET'])
def rooms_options_harmony():
    err = _writer()
    if err:
        return err
    try:
        hub = _host(request.args.get('hub', ''))
    except Invalid as e:
        return jsonify({'errors': e.errors}), 400
    if not hub:
        return jsonify({'errors': {'harmony_hub': 'Required'}}), 400
    if hub not in configured_hubs():
        return jsonify({'errors': {'harmony_hub': 'Not configured'}}), 400   # only a hub saved on a room
    opts = harmony_options(hub)
    if opts is None:
        return jsonify({'ok': False, 'activities': [], 'devices': []})
    return jsonify({'ok': True, **opts})


@bp.route('/api/rooms/<room_id>/mac', methods=['GET'])
def rooms_mac(room_id):
    err = _writer()
    if err:
        return err
    room = next((r for r in _rooms() if r['id'] == room_id), None)
    if room is None:
        return jsonify({'error': 'Room not found'}), 404
    devs = [d for d in room.get('devices') or [] if isinstance(d, dict)]
    host = (devs[0].get('host') if devs else '') or ''
    return jsonify({'mac': arp_mac(host) if host else ''})
