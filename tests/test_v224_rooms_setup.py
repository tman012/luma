"""v224 Settings > Rooms (Option A): prod's devices.rooms model, default room, Plex /clients discovery
(mocked), wake methods, legacy flat rooms, owner-only writes. Nothing contacts a device or Plex."""
import json
import copy
import threading

import pytest

from app import minimal, rooms_setup
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import _client
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req
from app.settings_manager import SettingsManager

API = '/api/rooms'
CLIENTS = [{'name': 'SHIELD Android TV', 'machineIdentifier': 'mid-shield', 'product': 'Plex for Android (TV)',
            'platform': 'Android', 'address': '192.168.1.40'},
           {'name': 'Living Room TV', 'machineIdentifier': 'mid-lrtv', 'product': 'Plex for Android (TV)',
            'platform': 'Android', 'address': '192.168.1.41'}]


class FakeSettings:
    """SettingsManager's room methods on an in-memory dict (never data/settings.json)."""
    def __init__(self, data=None):
        self._settings = copy.deepcopy(data or {})
        self._lock = threading.RLock()
        self.saves = 0

    def _save(self):
        self.saves += 1

    def get(self, dotted, default=None):
        cur = self._settings
        for k in dotted.split('.'):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return copy.deepcopy(cur)

    add_room = SettingsManager.add_room
    update_room = SettingsManager.update_room
    delete_room = SettingsManager.delete_room
    get_room_by_id = SettingsManager.get_room_by_id


@pytest.fixture
def rs(owner, monkeypatch):  # noqa: F811
    fake = FakeSettings({'devices': {'rooms': []}})
    calls = []

    def clients():
        calls.append(1)
        return copy.deepcopy(CLIENTS)
    monkeypatch.setitem(rooms_setup._cfg, 'settings', fake)
    monkeypatch.setitem(rooms_setup._cfg, 'clients_fn', clients)
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)
    # v272: _add pairs through connect; default wait succeeds (tests that need fail override it)
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn', lambda *a, **k: {'ok': True, 'state': 'connected', 'hint': 'connected'})
    return owner, fake, calls


def _create_empty(c, name):
    return _req(c, 'post', API, {'name': name})


def _add(c, name, mid='mid-shield', host='192.168.1.40', **kw):
    """v272 helper: empty create + Pair/connect (wait_fn mocked to success) so tests keep a player.
    Returns the create response (201) after the player is paired, matching the old helper's status."""
    r = _create_empty(c, name)
    assert r.status_code == 201, r.get_json()
    rid = next(x['id'] for x in r.get_json()['rooms'] if x['name'] == name)
    body = {'player': {'machine_id': mid, 'name': 'SHIELD Android TV',
                       'product': 'Plex for Android (TV)', 'platform': 'Android', 'host': host}}
    cr = _req(c, 'post', f'{API}/{rid}/connect', body)
    assert cr.status_code == 200 and cr.get_json().get('ok'), cr.get_json()
    if 'wake' in kw:
        _req(c, 'put', f'{API}/{rid}', {'wake': kw['wake']})
    # Re-fetch as 201-shaped response for callers that check status_code / get_json rooms
    class _R:
        status_code = 201
        def get_json(self_inner):
            return _req(c, 'get', API).get_json()
    return _R()


def test_routes_allowlisted_in_minimal():
    for p, m in ((API, 'GET'), (API, 'POST'), (API + '/abc', 'PUT'), (API + '/abc', 'DELETE'), (API + '/players', 'GET')):
        assert minimal.path_allowed(p, m), p


def test_empty_then_create_first_room_is_default_with_plex_service(rs):
    c, fake, _ = rs
    assert _req(c, 'get', API).get_json() == {'rooms': [], 'default_room_id': ''}
    r = _create_empty(c, 'Living Room')
    assert r.status_code == 201, r.get_json()
    d = r.get_json()
    room = d['rooms'][0]
    assert room['name'] == 'Living Room' and room['default'] is True and d['default_room_id'] == room['id']
    assert room.get('player') in (None, {}) or room['player'] is None
    assert fake._settings['devices']['rooms'][0]['devices'] == []
    # Pair/add player
    rid = room['id']
    assert _req(c, 'post', f'{API}/{rid}/connect', {'player': {'machine_id': 'mid-shield', 'name': 'SHIELD Android TV',
        'product': 'Plex for Android (TV)', 'platform': 'Android', 'host': '192.168.1.40'}}).get_json()['ok']
    d = _req(c, 'get', API).get_json()
    room = d['rooms'][0]
    assert room['player']['online'] is True and room['player']['wake'] == 'adb' and room['player']['port'] == 5555
    stored = fake._settings['devices']['rooms'][0]
    dev = stored['devices'][0]
    assert dev['machineId'] == 'mid-shield' and dev['platform'] == 'android_tv' and dev['powerMethod'] == 'adb' and dev['paired'] is True
    assert [s['type'] for s in dev['services']] == ['plex']
    assert 'homeassistant' not in str(stored).lower()


def test_validation(rs):
    c, _, _ = rs
    assert _create_empty(c, 'Living Room').status_code == 201
    # player on create is rejected (Pair adds it); empty name / clash / junk
    for body in ({'name': ''}, {'name': 'x' * 41}, {'name': 'living room'},
                 {'name': 'Den', 'player': {'host': '10.0.0.9'}}, {'name': 'Den', 'player': {}},
                 [1, 2]):
        assert _req(c, 'post', API, body).status_code == 400, body
    rid = _req(c, 'get', API).get_json()['rooms'][0]['id']
    # empty room: host/wake without a paired player rejected
    assert _req(c, 'put', f'{API}/{rid}', {'host': '10.0.0.9'}).status_code == 400
    assert _req(c, 'put', f'{API}/{rid}', {'player': {'host': '10.0.0.9'}}).status_code == 400
    _add(c, 'Office')
    oid = next(r['id'] for r in _req(c, 'get', API).get_json()['rooms'] if r['name'] == 'Office')
    for body in ({'port': 0}, {'port': 70000}, {'mac': 'zz'}, {'wake': 'ha'}, {'default': 'yes'}, {'devices': []}, {}):
        assert _req(c, 'put', f'{API}/{oid}', body).status_code == 400, body
    assert _req(c, 'put', f'{API}/nope', {'name': 'X'}).status_code == 404
    assert _req(c, 'delete', f'{API}/nope').status_code == 404


def test_add_by_ip_offline_and_wake_methods(rs):
    c, fake, _ = rs
    r = _create_empty(c, 'Office')
    assert r.status_code == 201, r.get_json()
    rid = r.get_json()['rooms'][0]['id']
    # Pair by IP (wait mocked ok) — device persists only after Pair
    assert _req(c, 'post', f'{API}/{rid}/connect', {'player': {'host': '192.168.1.77'}}).get_json()['ok']
    room = _req(c, 'get', API).get_json()['rooms'][0]
    assert room['player']['online'] is False and room['player']['name'] == '192.168.1.77' and room['player']['paired'] is True
    d = _req(c, 'put', f'{API}/{rid}', {'wake': 'wol', 'mac': 'aa-bb-cc-dd-ee-ff'}).get_json()
    p = d['rooms'][0]['player']
    assert p['wake'] == 'wol' and p['mac'] == 'AA:BB:CC:DD:EE:FF'
    d = _req(c, 'put', f'{API}/{rid}', {'wake': 'harmony', 'harmony_hub': '192.168.1.5', 'harmony_activity': 'Watch TV'}).get_json()
    assert d['rooms'][0]['player']['wake'] == 'harmony' and d['rooms'][0]['harmony_hub'] == '192.168.1.5'
    assert fake._settings['devices']['rooms'][0]['useHarmonyHub'] is True
    d = _req(c, 'put', f'{API}/{rid}', {'wake': 'none'}).get_json()
    assert d['rooms'][0]['player']['wake'] == 'none' and fake._settings['devices']['rooms'][0]['useHarmonyHub'] is False


def test_default_room_rename_change_player_and_remove(rs):
    c, fake, _ = rs
    a = _add(c, 'Living Room').get_json()['rooms'][0]['id']
    b = next(r['id'] for r in _add(c, 'Bedroom', mid='mid-lrtv', host='192.168.1.41').get_json()['rooms'] if r['name'] == 'Bedroom')
    d = _req(c, 'put', f'{API}/{b}', {'default': True, 'name': 'Main Bedroom'}).get_json()
    assert d['default_room_id'] == b and fake._settings['devices']['defaultRoomId'] == b
    assert [r['name'] for r in d['rooms']] == ['Living Room', 'Main Bedroom']
    fake._settings['devices']['rooms'][0]['devices'][0]['paired'] = True
    # v272: Change player is Pair/connect; success replaces and marks paired
    assert _req(c, 'put', f'{API}/{a}', {'player': {'machine_id': 'mid-lrtv', 'name': 'Living Room TV', 'host': '192.168.1.41'}}).status_code == 400
    assert _req(c, 'post', f'{API}/{a}/connect', {'player': {'machine_id': 'mid-lrtv', 'name': 'Living Room TV',
        'product': 'Plex for Android (TV)', 'platform': 'Android', 'host': '192.168.1.41'}}).get_json()['ok']
    pa = _req(c, 'get', API).get_json()['rooms'][0]['player']
    assert pa['name'] == 'Living Room TV' and pa['paired'] is True and pa['host'] == '192.168.1.41'
    d = _req(c, 'put', f'{API}/{b}', {'default': False}).get_json()
    assert d['default_room_id'] == a
    _req(c, 'put', f'{API}/{b}', {'default': True})
    d = _req(c, 'delete', f'{API}/{b}').get_json()
    assert [r['name'] for r in d['rooms']] == ['Living Room'] and d['default_room_id'] == a


def test_players_discovery_online_only_no_offline_orphans(rs):
    """v272: configured Offline hosts must not appear in the pick list (phantom .108 root cause)."""
    c, _, _ = rs
    _create_empty(c, 'Bedroom')
    rid = next(r['id'] for r in _req(c, 'get', API).get_json()['rooms'] if r['name'] == 'Bedroom')
    # Apple TV (no ADB host platform) persists without ADB wait
    assert _req(c, 'post', f'{API}/{rid}/connect', {'player': {
        'host': '', 'name': 'Apple TV 4K', 'product': 'Plex for Apple TV', 'platform': 'tvOS', 'machine_id': 'mid-atv'}}).get_json()['ok']
    ps = _req(c, 'get', API + '/players').get_json()['players']
    assert [(p['name'], p['online']) for p in ps] == [('SHIELD Android TV', True), ('Living Room TV', True)]
    assert not any(p.get('host') == '192.168.1.90' for p in ps)


def test_plex_unreachable_reads_offline(rs, monkeypatch):
    c, _, _ = rs
    _add(c, 'Living Room')
    monkeypatch.setitem(rooms_setup._cfg, 'clients_fn', lambda: (_ for _ in ()).throw(OSError('down')))
    d = _req(c, 'get', API).get_json()
    assert d['rooms'][0]['player']['online'] is False
    # v272: players endpoint is Plex-online only (no configured Offline orphans)
    assert _req(c, 'get', API + '/players').get_json()['players'] == []


def test_legacy_flat_room_reads_and_writes_back_nested(rs):
    c, fake, _ = rs
    fake._settings['devices'] = {'rooms': [{'id': 'r1', 'name': 'Family Room', 'ip': '192.168.1.40', 'adb_port': 5555,
                                            'type': 'android_tv', 'plex_machine_id': 'mid-shield'}]}
    d = _req(c, 'get', API).get_json()
    r = d['rooms'][0]
    assert r['name'] == 'Family Room' and r['default'] is True and r['player']['host'] == '192.168.1.40'
    assert r['player']['online'] is True and r['player']['wake'] == 'adb'
    assert _req(c, 'put', f'{API}/r1', {'name': 'Family'}).status_code == 200
    stored = fake._settings['devices']['rooms'][0]
    assert isinstance(stored['devices'], list) and stored['devices'][0]['host'] == '192.168.1.40'
    assert not {'ip', 'adb_port', 'type'} & set(stored)


def test_v3_room_extra_devices_and_kodi_services_untouched(rs):
    c, fake, _ = rs
    kodi = {'id': 'k', 'type': 'kodi', 'kodi_host': '10.0.0.2', 'kodi_port': 8080}
    fake._settings['devices'] = {'rooms': [{'id': 'r1', 'name': 'Den', 'devices': [
        {'id': 'd1', 'name': 'Shield', 'host': '192.168.1.40', 'platform': 'android_tv', 'powerMethod': 'homeassistant', 'services': [kodi]},
        {'id': 'd2', 'name': 'Other', 'host': '10.0.0.3', 'services': []}]}]}
    r = _req(c, 'get', API).get_json()['rooms'][0]
    assert r['player']['wake'] == 'homeassistant'               # v299: HA wake exposed
    assert _req(c, 'put', f'{API}/r1', {'port': 5556}).status_code == 200
    devs = fake._settings['devices']['rooms'][0]['devices']
    assert devs[0]['powerMethod'] == 'homeassistant' and devs[0]['connectPort'] == 5556   # left as is unless wake changes
    assert devs[0]['services'][0] == kodi and [s['type'] for s in devs[0]['services']] == ['kodi', 'plex']
    assert devs[1] == {'id': 'd2', 'name': 'Other', 'host': '10.0.0.3', 'services': []}


def test_settings_rooms_default_target_follows_default_room(rs, monkeypatch):
    c, fake, _ = rs
    from app import main
    monkeypatch.setattr(main, 'settings', fake)
    monkeypatch.setattr(minimal, 'path_allowed', lambda *a, **k: True)   # the playback SHA opens this GET in minimal
    a = _add(c, 'Living Room').get_json()['rooms'][0]['id']
    b = next(r['id'] for r in _add(c, 'Bedroom', mid='mid-lrtv').get_json()['rooms'] if r['name'] == 'Bedroom')
    r0 = _req(c, 'get', '/api/settings/rooms')
    assert r0.status_code == 200, (r0.status_code, r0.get_json())
    assert r0.get_json()['defaultPlaybackTarget']['roomId'] == a
    _req(c, 'put', f'{API}/{b}', {'default': True})
    d = _req(c, 'get', '/api/settings/rooms').get_json()
    assert d['defaultPlaybackTarget']['roomId'] == b and d['defaultRoomId'] == b


def test_writes_need_the_signed_in_owner(minimal_app, monkeypatch):  # noqa: F811
    fake = FakeSettings({'devices': {'rooms': [{'id': 'r1', 'name': 'Den', 'devices': []}]}})
    monkeypatch.setitem(rooms_setup._cfg, 'settings', fake)
    monkeypatch.setitem(rooms_setup._cfg, 'clients_fn', lambda: [])
    c = minimal_app.test_client()
    for m, u, b in (('get', API, None), ('get', API + '/players', None), ('post', API, {'name': 'X', 'player': {'host': '1.2.3.4'}}),
                    ('put', API + '/r1', {'name': 'Y'}), ('delete', API + '/r1', None)):
        r = _req(c, m, u, b)
        assert r.status_code in (401, 403), (m, u, r.status_code)
    assert fake.saves == 0 and fake._settings['devices']['rooms'][0]['name'] == 'Den'


def test_no_play_or_wake_or_home_assistant_in_module():
    src = open(rooms_setup.__file__).read()
    for bad in ('device_wake', 'harmony_service', 'playqueue', 'player/play', 'adb_shell', 'send_magic', 'requests.'):
        assert bad not in src, bad


def test_v225_stop_route_stops_only_that_rooms_player_mocked(rs, monkeypatch):
    c, _, _ = rs
    a = _add(c, 'Living Room').get_json()['rooms'][0]['id']
    stopped = []
    monkeypatch.setitem(rooms_setup._cfg, 'stop_fn', lambda dev: stopped.append(dev['machineId']) or True)
    r = _req(c, 'post', f'{API}/{a}/stop')
    assert r.status_code == 200 and r.get_json() == {'stopped': True} and stopped == ['mid-shield']
    assert _req(c, 'post', f'{API}/nope/stop').status_code == 404
    monkeypatch.setitem(rooms_setup._cfg, 'stop_fn', lambda dev: (_ for _ in ()).throw(OSError('down')))
    assert _req(c, 'post', f'{API}/{a}/stop').get_json() == {'stopped': False}
    monkeypatch.setitem(rooms_setup._cfg, 'can_play', lambda u: False)
    assert _req(c, 'post', f'{API}/{a}/stop').status_code == 403
    assert minimal.path_allowed(f'{API}/{a}/stop', 'POST')


def test_v225_plex_stop_matches_by_machine_id_through_the_server(rs, monkeypatch):
    _, fake, _ = rs
    fake._settings['plex'] = {'url': 'http://pms.invalid:32400', 'token': 't'}
    events = []

    class Cl:
        def __init__(self, mid, addr):
            self.machineIdentifier, self.address = mid, addr

        def proxyThroughServer(self, on, server):
            events.append(('proxy', self.machineIdentifier, on))

        def stop(self):
            events.append(('stop', self.machineIdentifier))

    class Srv:
        def clients(self):
            return [Cl('other', '192.168.1.40'), Cl('mid-shield', '192.168.1.99')]
    from app import plex_client_service
    monkeypatch.setattr(plex_client_service, '_plex_server', lambda u, t: Srv())
    assert rooms_setup._plex_stop({'machineId': 'mid-shield', 'host': '192.168.1.40'}) is True
    assert events == [('proxy', 'mid-shield', True), ('stop', 'mid-shield')]
    events.clear()
    assert rooms_setup._plex_stop({'machineId': 'gone', 'host': '192.168.1.40'}) is False and events == []


# v231 (Reviewer): GET /api/rooms hides device details from signed-in non-owners

def _wake_room(c):
    assert _add(c, 'Living Room').status_code == 201
    rid = _req(c, 'get', API).get_json()['rooms'][0]['id']
    assert _req(c, 'put', f'{API}/{rid}', {'wake': 'wol', 'mac': 'AA:BB:CC:DD:EE:FF', 'port': 5555,
                                           'harmony_hub': '192.168.1.9', 'harmony_activity': 'Shield'}).status_code == 200
    assert _add(c, 'Bedroom', mid='mid-lrtv', host='192.168.1.41').status_code == 201
    return rid


def test_rooms_get_owner_keeps_full_device_data(rs):
    c, _, _ = rs
    _wake_room(c)
    d = _req(c, 'get', API).get_json()
    lr = next(r for r in d['rooms'] if r['name'] == 'Living Room')
    assert lr['player']['host'] == '192.168.1.40' and lr['player']['mac'] == 'AA:BB:CC:DD:EE:FF'
    assert lr['harmony_hub'] == '192.168.1.9' and lr['online'] is True and lr['default'] is True


def test_rooms_get_non_owner_sees_only_id_name_default_online(rs, monkeypatch):
    c, _, _ = rs
    rid = _wake_room(c)
    owner_view = _req(c, 'get', API).get_json()
    monkeypatch.setitem(rooms_setup._cfg, 'get_current_user', lambda: {'id': 2, 'name': 'guest', 'is_admin': False})
    monkeypatch.setitem(rooms_setup._cfg, 'signed_in_owner', lambda: (None, ({'error': 'Owner only'}, 403)))
    r = _req(c, 'get', API)
    assert r.status_code == 200
    d = r.get_json()
    assert d['default_room_id'] == rid == owner_view['default_room_id']
    assert [sorted(x) for x in d['rooms']] == [['default', 'id', 'name', 'online']] * 2
    assert [(x['id'], x['name'], x['default'], x['online']) for x in d['rooms']] == \
        [(x['id'], x['name'], x['default'], x['online']) for x in owner_view['rooms']]
    raw = r.get_data(as_text=True)
    for secret in ('192.168.1.40', '192.168.1.41', 'AA:BB:CC:DD:EE:FF', '192.168.1.9', '5555', 'mid-shield', 'harmony', 'host', 'mac', 'player'):
        assert secret not in raw
    # writes and player discovery stay owner-only
    assert _req(c, 'get', f'{API}/players').status_code == 403
    assert _req(c, 'post', API, {'name': 'Den'}).status_code == 403


def test_rooms_get_signed_out_is_401(rs, monkeypatch):
    c, _, _ = rs
    monkeypatch.setitem(rooms_setup._cfg, 'get_current_user', lambda: None)
    assert _req(c, 'get', API).status_code == 401


def test_v231_family_user_gets_the_default_room_preselected(rs, monkeypatch):
    """PM: everyone who can play gets the default room, family users included; editing stays owner-only."""
    c, fake, _ = rs
    from app import main
    monkeypatch.setattr(main, 'settings', fake)
    monkeypatch.setattr(minimal, 'path_allowed', lambda *a, **k: True)
    a = _add(c, 'Living Room').get_json()['rooms'][0]['id']
    b = next(r['id'] for r in _add(c, 'Bedroom', mid='mid-lrtv').get_json()['rooms'] if r['name'] == 'Bedroom')
    _req(c, 'put', f'{API}/{b}', {'default': True})
    rooms = fake.get('devices.rooms')
    family = {'id': 5, 'username': 'kid', 'is_admin': False}
    monkeypatch.setattr(main, '_get_current_user', lambda: family)
    monkeypatch.setattr(main.user_auth, 'get_user_settings', lambda uid: {'rooms': rooms} if uid == 5 else {})
    monkeypatch.setitem(rooms_setup._cfg, 'get_current_user', lambda: family)
    monkeypatch.setitem(rooms_setup._cfg, 'signed_in_owner', lambda: (None, ({'error': 'Owner only'}, 403)))
    d = _req(c, 'get', '/api/settings/rooms').get_json()
    assert d['defaultPlaybackTarget']['roomId'] == b and d['defaultRoomId'] == b
    v = _req(c, 'get', API).get_json()
    assert v['default_room_id'] == b
    assert [(r['id'], r['default']) for r in v['rooms']] == [(a, False), (b, True)]
    # editing stays owner-only
    assert _req(c, 'put', f'{API}/{a}', {'default': True}).status_code == 403
    assert fake.get('devices.defaultRoomId') == b


# v231e (Reviewer): GET /api/settings/rooms gives non-admins only what PlaybackModal needs

def _settings_rooms_setup(rs, monkeypatch):
    c, fake, _ = rs
    from app import main
    monkeypatch.setattr(main, 'settings', fake)
    monkeypatch.setattr(minimal, 'path_allowed', lambda *a, **k: True)
    a = _add(c, 'Living Room').get_json()['rooms'][0]['id']
    assert _req(c, 'put', f'{API}/{a}', {'wake': 'wol', 'mac': 'AA:BB:CC:DD:EE:FF', 'port': 5555,
                                         'harmony_hub': '192.168.1.9', 'harmony_activity': 'Shield'}).status_code == 200
    b = next(r['id'] for r in _add(c, 'Bedroom', mid='mid-lrtv', host='192.168.1.41').get_json()['rooms'] if r['name'] == 'Bedroom')
    return c, fake, main, a, b


def _as_family(monkeypatch, main, fake):
    family = {'id': 5, 'username': 'kid', 'is_admin': False}
    rooms = fake.get('devices.rooms')
    monkeypatch.setattr(main, '_get_current_user', lambda: family)
    monkeypatch.setattr(main.user_auth, 'get_user_settings', lambda uid: {'rooms': rooms})
    return family


def test_v231_settings_rooms_admin_keeps_full_device_data(rs, monkeypatch):
    c, _, _, a, _ = _settings_rooms_setup(rs, monkeypatch)
    d = _req(c, 'get', '/api/settings/rooms').get_json()
    raw = json.dumps(d)
    assert '192.168.1.40' in raw and '192.168.1.41' in raw
    lr = next(r for r in d['rooms'] if r['id'] == a)
    assert lr['devices'][0]['host'] == '192.168.1.40'


def test_v231_settings_rooms_non_admin_gets_ids_names_default_only(rs, monkeypatch):
    c, fake, main, a, b = _settings_rooms_setup(rs, monkeypatch)
    _req(c, 'put', f'{API}/{b}', {'default': True})
    full = _req(c, 'get', '/api/settings/rooms').get_json()
    _as_family(monkeypatch, main, fake)
    r = _req(c, 'get', '/api/settings/rooms')
    assert r.status_code == 200
    d = r.get_json()
    assert d['defaultRoomId'] == b and d['defaultPlaybackTarget'] == full['defaultPlaybackTarget']
    assert [(x['id'], x['name'], x['default']) for x in d['rooms']] == [(a, 'Living Room', False), (b, 'Bedroom', True)]
    for room in d['rooms']:
        assert sorted(room) == ['default', 'devices', 'id', 'name']
        for dev in room['devices']:
            assert sorted(dev) == ['available', 'id', 'name', 'platform', 'services'] and dev['available'] is True
            assert all(sorted(sv) == ['id', 'type'] for sv in dev['services'])
    raw = r.get_data(as_text=True)
    for secret in ('192.168.1.40', '192.168.1.41', 'AA:BB:CC:DD:EE:FF', '192.168.1.9', '5555', 'host', 'mac',
                   'connectPort', 'harmony', 'Harmony', 'kodi_password', 'kodi_username'):
        assert secret not in raw, secret


def test_v231_non_admin_still_plays_by_room_id(rs, monkeypatch):
    """player/play resolves the host from the room id on the server; the client sends no address."""
    c, fake, main, a, _ = _settings_rooms_setup(rs, monkeypatch)
    _as_family(monkeypatch, main, fake)
    d = _req(c, 'get', '/api/settings/rooms').get_json()
    room = next(r for r in d['rooms'] if r['id'] == a)
    dev, svc = room['devices'][0], room['devices'][0]['services'][0]
    monkeypatch.setattr(main.user_auth, 'is_plex_approved', lambda u: True)
    monkeypatch.setattr(fake, 'get_default_adb_port', lambda: 5555, raising=False)
    monkeypatch.setattr(main, 'settings', fake)
    played = []
    fake_play = lambda service, rk, off, name, ip, *a, **k: (played.append((rk, ip, name)), main.jsonify({'success': True}))[1]
    monkeypatch.setattr(main, '_play_via_plex', fake_play)
    monkeypatch.setattr(main, '_play_via_kodi', fake_play)
    monkeypatch.setattr(main, '_configured_plex_clients', lambda: [('192.168.1.40', None), ('192.168.1.41', None)], raising=False)
    r = _req(c, 'post', '/api/player/play', {'rating_key': 11, 'client_ip': '', 'room_id': a,
                                             'device_id': dev['id'], 'service_id': svc['id']})
    assert r.status_code == 200, r.get_json()
    assert played == [(11, '192.168.1.40', 'Living Room')]
