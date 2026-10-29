"""Settings premium pass: a persisted room ``icon`` (migration + default + API read/write) and the
owner-only Rooms pickers (HA entities, Harmony activities/devices, ARP MAC). Nothing contacts a device."""
import copy

import pytest

from app import minimal, rooms_options, rooms_setup
from app.settings_manager import _migrate_room_icons
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.test_v224_rooms_setup import API, FakeSettings, rs  # noqa: F401

ICONS = ('sofa', 'bed', 'kitchen', 'dining', 'office', 'theater', 'game', 'kids', 'bath', 'gym',
         'music', 'library', 'patio', 'garage', 'home')


def test_icon_set_matches_the_frontend_and_designer():
    assert rooms_setup.ROOM_ICONS == ICONS and rooms_setup.DEFAULT_ICON == 'home'
    src = open('frontend/src/components/rooms/roomIcons.tsx').read()
    assert "ROOM_ICONS = ['sofa', 'bed', 'kitchen', 'dining', 'office', 'theater', 'game', 'kids', 'bath', 'gym',\n  'music', 'library', 'patio', 'garage', 'home']" in src


def test_migration_gives_existing_rooms_the_home_icon_and_keeps_valid_ones():
    data = {'devices': {'rooms': [{'id': 'a', 'name': 'Living Room'}, {'id': 'b', 'name': 'Bed', 'icon': 'bed'},
                                  {'id': 'c', 'name': 'X', 'icon': 'rocket'}, 'junk']}}
    assert _migrate_room_icons(data) is True
    assert [r['icon'] for r in data['devices']['rooms'][:3]] == ['home', 'bed', 'home']
    assert data['devices']['rooms'][3] == 'junk'
    assert _migrate_room_icons(data) is False                      # idempotent
    assert _migrate_room_icons({}) is False and _migrate_room_icons({'devices': {'rooms': None}}) is False


def test_migration_runs_with_the_settings_migrations():
    import inspect
    from app import settings_manager
    src = inspect.getsource(settings_manager)
    assert 'changed |= _migrate_room_icons(data)' in src
    assert src.index('changed |= _migrate_room_ids(data)') < src.index('changed |= _migrate_room_icons(data)')


def test_rooms_read_icon_with_a_default_for_old_rooms(rs):  # noqa: F811
    c, fake, _ = rs
    fake._settings['devices']['rooms'] = [{'id': 'r1', 'name': 'Den', 'devices': []},
                                          {'id': 'r2', 'name': 'Gym', 'icon': 'gym', 'devices': []}]
    rooms = _req(c, 'get', API).get_json()['rooms']
    assert [(r['id'], r['icon']) for r in rooms] == [('r1', 'home'), ('r2', 'gym')]
    assert fake.saves == 0                                          # reading never writes


def test_create_stores_home_and_put_writes_a_valid_icon_only(rs):  # noqa: F811
    c, fake, _ = rs
    r = _req(c, 'post', API, {'name': 'Theater'})
    assert r.status_code == 201
    rid = r.get_json()['rooms'][0]['id']
    assert r.get_json()['rooms'][0]['icon'] == 'home'
    assert fake._settings['devices']['rooms'][0]['icon'] == 'home'
    r = _req(c, 'put', f'{API}/{rid}', {'icon': 'theater'})
    assert r.status_code == 200 and r.get_json()['rooms'][0]['icon'] == 'theater'
    assert fake._settings['devices']['rooms'][0]['icon'] == 'theater'
    saves = fake.saves
    for bad in ('rocket', '', None, 3, ['bed']):
        r = _req(c, 'put', f'{API}/{rid}', {'icon': bad})
        assert r.status_code == 400 and r.get_json()['errors'] == {'icon': 'Invalid'}, bad
    assert fake.saves == saves and fake._settings['devices']['rooms'][0]['icon'] == 'theater'


def test_icon_is_not_in_the_non_owner_view():
    full = {'rooms': [{'id': 'a', 'name': 'A', 'icon': 'bed', 'default': True, 'online': False, 'player': None}], 'default_room_id': 'a'}
    assert rooms_setup.public_view(full)['rooms'] == [{'id': 'a', 'name': 'A', 'default': True, 'online': False}]


def test_picker_routes_are_allowlisted_in_minimal():
    for p in ('/api/rooms/options/ha', '/api/rooms/options/harmony', '/api/rooms/abc/mac'):
        assert minimal.path_allowed(p, 'GET'), p


def test_ha_entities_lists_tv_power_candidates(rs, monkeypatch):  # noqa: F811
    c, _, _ = rs
    states = [{'entity_id': 'light.lamp', 'attributes': {'friendly_name': 'Lamp'}},
              {'entity_id': 'switch.tv_plug', 'attributes': {'friendly_name': 'TV plug'}},
              {'entity_id': 'media_player.living_room_tv', 'attributes': {'friendly_name': 'Living Room TV'}},
              {'entity_id': 'remote.harmony', 'attributes': {}}, 'junk', {'entity_id': 5}]
    monkeypatch.setitem(rooms_setup._cfg, 'ha_states_fn', lambda: copy.deepcopy(states))
    r = _req(c, 'get', '/api/rooms/options/ha')
    assert r.status_code == 200
    assert r.get_json() == {'entities': [{'id': 'media_player.living_room_tv', 'name': 'Living Room TV'},
                                         {'id': 'switch.tv_plug', 'name': 'TV plug'},
                                         {'id': 'remote.harmony', 'name': 'remote.harmony'}]}


def test_ha_entities_empty_without_a_connector(rs):  # noqa: F811
    c, _, _ = rs
    assert _req(c, 'get', '/api/rooms/options/ha').get_json() == {'entities': []}


def test_harmony_options_from_the_hub(rs, monkeypatch):  # noqa: F811
    c, _, _ = rs
    seen = []

    def cfg(hub):
        seen.append(hub)
        return {'activities': [{'label': 'Watch TV'}, {'label': 'PowerOff'}, {}], 'devices': [{'label': 'AV Receiver'}, {'id': 1}]}
    monkeypatch.setitem(rooms_setup._cfg, 'harmony_config_fn', cfg)
    r = _req(c, 'get', '/api/rooms/options/harmony?hub=192.168.1.19')
    assert r.get_json() == {'ok': True, 'activities': ['Watch TV'], 'devices': ['AV Receiver']} and seen == ['192.168.1.19']
    monkeypatch.setitem(rooms_setup._cfg, 'harmony_config_fn', lambda hub: None)
    assert _req(c, 'get', '/api/rooms/options/harmony?hub=192.168.1.19').get_json() == {'ok': False, 'activities': [], 'devices': []}
    assert _req(c, 'get', '/api/rooms/options/harmony').status_code == 400
    assert _req(c, 'get', '/api/rooms/options/harmony?hub=bad%20host!').status_code == 400


ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.40     0x1         0x2         00:04:4b:aa:bb:cc     *        eth0
192.168.1.41     0x1         0x0         00:00:00:00:00:00     *        eth0
"""


def test_mac_lookup_from_arp(rs, monkeypatch):  # noqa: F811
    c, fake, _ = rs
    fake._settings['devices']['rooms'] = [
        {'id': 'r1', 'name': 'Den', 'devices': [{'id': 'd', 'host': '192.168.1.40'}]},
        {'id': 'r2', 'name': 'Gym', 'devices': [{'id': 'e', 'host': '192.168.1.41'}]},
        {'id': 'r3', 'name': 'Empty', 'devices': []}]
    monkeypatch.setitem(rooms_setup._cfg, 'arp_fn', lambda: ARP)
    assert _req(c, 'get', f'{API}/r1/mac').get_json() == {'mac': '00:04:4B:AA:BB:CC'}
    assert _req(c, 'get', f'{API}/r2/mac').get_json() == {'mac': ''}       # incomplete entry
    assert _req(c, 'get', f'{API}/r3/mac').get_json() == {'mac': ''}
    assert _req(c, 'get', f'{API}/nope/mac').status_code == 404
    assert fake.saves == 0


def test_pickers_need_the_signed_in_owner(minimal_app, monkeypatch):  # noqa: F811
    fake = FakeSettings({'devices': {'rooms': [{'id': 'r1', 'name': 'Den', 'devices': []}]}})
    monkeypatch.setitem(rooms_setup._cfg, 'settings', fake)
    monkeypatch.setitem(rooms_setup._cfg, 'ha_states_fn', lambda: pytest.fail('no HA call'))
    monkeypatch.setitem(rooms_setup._cfg, 'harmony_config_fn', lambda hub: pytest.fail('no hub call'))
    c = minimal_app.test_client()
    for u in ('/api/rooms/options/ha', '/api/rooms/options/harmony?hub=1.2.3.4', API + '/r1/mac'):
        assert _req(c, 'get', u).status_code in (401, 403), u


def test_pickers_module_never_wakes_or_plays():
    src = open(rooms_options.__file__).read()
    for bad in ('start_activity', 'send_command', 'power_off', 'call_service', 'fire_entity', 'send_magic', 'adb_shell', 'playqueue'):
        assert bad not in src, bad
