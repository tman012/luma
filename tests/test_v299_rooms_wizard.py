"""v299 Rooms Add wizard API: wake=homeassistant + ha_power_entity; create empty; Pair-only persist."""
import os
import re

from app import rooms_setup
from tests.test_v224_rooms_setup import API, _create_empty, rs  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401


def test_create_has_empty_devices(rs):
    c, fake, _ = rs
    r = _create_empty(c, 'Family Room')
    assert r.status_code == 201
    room = r.get_json()['rooms'][0]
    assert room.get('player') is None
    assert fake._settings['devices']['rooms'][0]['devices'] == []
    # reject player on create (phantom ATV .108 class of bug)
    bad = _req(c, 'post', API, {'name': 'Den', 'player': {'host': '192.168.1.108', 'platform': 'android_tv'}})
    assert bad.status_code == 400
    assert 'Pair' in str(bad.get_json().get('errors', {})).lower() or bad.get_json().get('errors')


def test_connect_only_persist_on_pair_success(rs, monkeypatch):
    c, fake, _ = rs
    rid = _create_empty(c, 'Family Room').get_json()['rooms'][0]['id']
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn',
                        lambda *a, **k: {'ok': False, 'state': 'failed', 'hint': 'wrong_port', 'detail': 'port'})
    d = _req(c, 'post', f'{API}/{rid}/connect', {'player': {'host': '192.168.1.108', 'platform': 'android_tv'}}).get_json()
    assert d['ok'] is False and d['hint'] == 'wrong_port'
    assert fake._settings['devices']['rooms'][0]['devices'] == []
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn',
                        lambda *a, **k: {'ok': True, 'state': 'connected', 'hint': 'connected'})
    d = _req(c, 'post', f'{API}/{rid}/connect', {'player': {'host': '192.168.1.50', 'platform': 'android_tv'}}).get_json()
    assert d['ok'] is True
    room = _req(c, 'get', API).get_json()['rooms'][0]
    assert room['player']['host'] == '192.168.1.50' and room['player']['paired'] is True


def test_wake_homeassistant_and_ha_power_entity(rs):
    c, fake, _ = rs
    rid = _create_empty(c, 'Family Room').get_json()['rooms'][0]['id']
    assert _req(c, 'post', f'{API}/{rid}/connect', {
        'player': {'host': '192.168.1.50', 'name': 'Shield', 'platform': 'android_tv'}}).get_json()['ok']
    d = _req(c, 'put', f'{API}/{rid}', {
        'wake': 'homeassistant', 'ha_power_entity': 'scene.tv_power'}).get_json()
    p = d['rooms'][0]['player']
    assert p['wake'] == 'homeassistant'
    assert p.get('ha_power_entity') == 'scene.tv_power'
    stored = fake._settings['devices']['rooms'][0]['devices'][0]
    assert stored['powerMethod'] == 'homeassistant'
    assert stored['haPowerEntity'] == 'scene.tv_power'
    assert fake._settings['devices']['rooms'][0].get('useHarmonyHub') is False


def test_ha_power_entity_rejected_on_empty_room(rs):
    c, _, _ = rs
    rid = _create_empty(c, 'Empty').get_json()['rooms'][0]['id']
    assert _req(c, 'put', f'{API}/{rid}', {'wake': 'homeassistant', 'ha_power_entity': 'scene.x'}).status_code == 400


def test_delete_empty_room_after_create(rs):
    """Wizard Cancel/Back before Pair: DELETE empty shell."""
    c, fake, _ = rs
    rid = _create_empty(c, 'Family Room').get_json()['rooms'][0]['id']
    assert fake._settings['devices']['rooms'][0]['devices'] == []
    assert _req(c, 'delete', f'{API}/{rid}').status_code == 200
    assert fake._settings['devices']['rooms'] == []


def test_sw_v299():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 299
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp
