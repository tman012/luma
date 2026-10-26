"""v292: Offline badge — Plex machine_id key + ADB reachable/authorized for ADB-wake rooms."""
import copy
import os
import re

import pytest

from app import rooms_setup
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.test_v224_rooms_setup import API, FakeSettings  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401


@pytest.fixture
def rs(owner, monkeypatch):  # noqa: F811
    fake = FakeSettings({'devices': {'rooms': []}})
    clients = [
        {'name': 'Living Room TV', 'machine_id': 'mid-lrtv', 'product': 'Plex for Android (TV)',
         'platform': 'Android', 'address': '192.168.1.108'},
        {'name': 'Family Room TV', 'machine_id': 'mid-fr', 'product': 'Plex for Android (TV)',
         'platform': 'Android', 'address': '192.168.1.126'},
    ]

    def clients_fn():
        return copy.deepcopy(clients)

    monkeypatch.setitem(rooms_setup._cfg, 'settings', fake)
    monkeypatch.setitem(rooms_setup._cfg, 'clients_fn', clients_fn)
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn',
                        lambda *a, **k: {'ok': True, 'state': 'connected', 'hint': 'connected'})
    monkeypatch.setitem(rooms_setup._cfg, 'adb_online_fn', None)
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)
    return owner, fake, clients


def _seed_adb_room(fake, *, host='192.168.1.108', mid='mid-lrtv', paired=True, power='adb'):
    fake._settings['devices']['rooms'] = [{
        'id': 'room-lr', 'name': 'Living Room',
        'devices': [{
            'id': 'dev-lr', 'name': 'Living Room TV', 'platform': 'android_tv',
            'host': host, 'connectPort': 5555, 'machineId': mid,
            'paired': paired, 'powerMethod': power, 'services': [],
        }],
    }]


def _seed_harmony_room(fake, *, host='192.168.1.126', mid='mid-fr'):
    fake._settings['devices']['rooms'] = [{
        'id': 'room-fr', 'name': 'Family Room', 'useHarmonyHub': True,
        'harmonyHubIp': '192.168.1.19',
        'devices': [{
            'id': 'dev-fr', 'name': 'Family Room TV', 'platform': 'android_tv',
            'host': host, 'connectPort': 5555, 'machineId': mid,
            'paired': True, 'powerMethod': 'adb', 'services': [],
        }],
    }]


def test_v292_online_matches_machine_id_from_get_active_clients(rs, monkeypatch):
    """LIVE get_active_clients returns machine_id only — must match device machineId."""
    c, fake, _ = rs
    _seed_harmony_room(fake)  # harmony wake → Plex path (no ADB probe)
    monkeypatch.setitem(rooms_setup._cfg, 'adb_online_fn', lambda d: False)  # must not matter
    room = _req(c, 'get', API).get_json()['rooms'][0]
    assert room['online'] is True
    assert room['player']['online'] is True
    assert room['player']['wake'] == 'harmony'


def test_v292_players_lists_machine_id_clients(rs):
    c, fake, _ = rs
    ps = _req(c, 'get', f'{API}/players').get_json()['players']
    assert {p['machine_id'] for p in ps} == {'mid-lrtv', 'mid-fr'}
    assert all(p['online'] for p in ps)


def test_v292_adb_authorized_online_even_if_plex_misses(rs, monkeypatch):
    c, fake, clients = rs
    _seed_adb_room(fake, mid='mid-not-in-clients')
    clients.clear()  # Plex /clients empty
    monkeypatch.setitem(rooms_setup._cfg, 'adb_online_fn', lambda d: True)
    room = _req(c, 'get', API).get_json()['rooms'][0]
    assert room['player']['wake'] == 'adb'
    assert room['player']['paired'] is True
    assert room['online'] is True


def test_v292_adb_unauthorized_offline_despite_paired_and_plex(rs, monkeypatch):
    """Living .108 Tip N: paired=true + ADB unauthorized → Offline (not Plex Online)."""
    c, fake, _ = rs
    _seed_adb_room(fake, host='192.168.1.108', mid='mid-lrtv', paired=True)
    monkeypatch.setitem(rooms_setup._cfg, 'adb_online_fn', lambda d: False)
    room = _req(c, 'get', API).get_json()['rooms'][0]
    assert room['player']['paired'] is True
    assert room['player']['wake'] == 'adb'
    assert room['online'] is False
    assert room['player']['online'] is False


def test_v292_adb_unreachable_offline(rs, monkeypatch):
    c, fake, _ = rs
    _seed_adb_room(fake, paired=True)
    monkeypatch.setitem(rooms_setup._cfg, 'adb_online_fn', lambda d: False)
    assert _req(c, 'get', API).get_json()['rooms'][0]['online'] is False


def test_v292_paired_alone_never_online_without_adb_or_plex(rs, monkeypatch):
    c, fake, clients = rs
    _seed_adb_room(fake, mid='', paired=True)
    clients.clear()
    monkeypatch.setitem(rooms_setup._cfg, 'adb_online_fn', lambda d: False)
    room = _req(c, 'get', API).get_json()['rooms'][0]
    assert room['player']['paired'] is True
    assert room['online'] is False


def test_v292_adb_probe_none_falls_back_to_plex_machine_id(rs, monkeypatch):
    """No ADB binary / disabled → keep Plex machine_id match (unit-test path)."""
    c, fake, _ = rs
    _seed_adb_room(fake, host='192.168.1.108', mid='mid-lrtv')
    monkeypatch.setitem(rooms_setup._cfg, 'adb_online_fn', lambda d: None)
    assert _req(c, 'get', API).get_json()['rooms'][0]['online'] is True


def test_v292_client_machine_id_helper_prefers_machineIdentifier():
    assert rooms_setup._client_machine_id({'machineIdentifier': 'A', 'machine_id': 'B'}) == 'A'
    assert rooms_setup._client_machine_id({'machine_id': 'B'}) == 'B'
    assert rooms_setup._client_machine_id({}) == ''


def test_v292_plex_online_accepts_either_key():
    dev = {'machineId': 'm1', 'host': '10.0.0.1'}
    assert rooms_setup._plex_online(dev, [{'machine_id': 'm1'}]) is True
    assert rooms_setup._plex_online(dev, [{'machineIdentifier': 'm1'}]) is True
    assert rooms_setup._plex_online(dev, [{'machine_id': 'other', 'address': '10.0.0.1'}]) is True
    assert rooms_setup._plex_online(dev, [{'machine_id': 'x', 'address': '9.9.9.9'}]) is False


def test_sw_v292_or_newer():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 292
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp
