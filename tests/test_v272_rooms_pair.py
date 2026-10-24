"""v272 Rooms Pair: empty room on create; persist player only after Pair success; no Offline orphans;
ADB under MINIMAL when HOME+COMMANDS set; clear fail hints (wrong_port / adb_disabled)."""
import os

import pytest

from app import adb_utils, rooms_setup
from tests.test_v224_rooms_setup import API, _create_empty, rs  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401


def test_create_empty_room_rejects_player(rs):
    c, fake, _ = rs
    r = _create_empty(c, 'Family Room')
    assert r.status_code == 201
    room = r.get_json()['rooms'][0]
    assert room['name'] == 'Family Room' and room.get('player') is None
    assert fake._settings['devices']['rooms'][0]['devices'] == []
    assert _req(c, 'post', API, {'name': 'Den', 'player': {'host': '192.168.1.108'}}).status_code == 400


def test_failed_pair_does_not_persist_orphan(rs, monkeypatch):
    c, fake, _ = rs
    rid = _create_empty(c, 'Family Room').get_json()['rooms'][0]['id']
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn',
                        lambda *a, **k: {'ok': False, 'state': 'failed', 'hint': 'wrong_port', 'detail': 'port'})
    d = _req(c, 'post', f'{API}/{rid}/connect', {'player': {'host': '192.168.1.108', 'platform': 'android_tv'}}).get_json()
    assert d['ok'] is False and d['hint'] == 'wrong_port'
    assert d.get('state') == 'failed'
    assert fake._settings['devices']['rooms'][0]['devices'] == []
    assert _req(c, 'get', API).get_json()['rooms'][0].get('player') is None


def test_pair_success_persists_paired_player(rs):
    c, fake, _ = rs
    rid = _create_empty(c, 'Family Room').get_json()['rooms'][0]['id']
    d = _req(c, 'post', f'{API}/{rid}/connect', {
        'player': {'host': '192.168.1.50', 'name': 'Shield', 'product': 'Plex for Android (TV)',
                   'platform': 'Android', 'machine_id': 'mid-fr'}}).get_json()
    assert d == {'ok': True, 'state': 'connected', 'hint': 'connected'} or (
        d['ok'] is True and d['state'] == 'connected')
    room = _req(c, 'get', API).get_json()['rooms'][0]
    assert room['player']['host'] == '192.168.1.50' and room['player']['paired'] is True
    assert fake._settings['devices']['rooms'][0]['devices'][0]['paired'] is True


def test_adb_disabled_returns_clear_hint(rs, monkeypatch):
    c, fake, _ = rs
    rid = _create_empty(c, 'Family Room').get_json()['rooms'][0]['id']
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn', None)  # real wait_for_authorization
    monkeypatch.setenv('LUMA_DISABLE_ADB', '1')
    d = _req(c, 'post', f'{API}/{rid}/connect', {'player': {'host': '192.168.1.50', 'platform': 'android_tv'}}).get_json()
    assert d['ok'] is False and d['hint'] == 'adb_disabled'
    assert 'LUMA_DISABLE_ADB' in (d.get('detail') or '')
    assert fake._settings['devices']['rooms'][0]['devices'] == []
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)


def test_players_list_excludes_configured_offline(rs):
    c, _, _ = rs
    rid = _create_empty(c, 'Living Room').get_json()['rooms'][0]['id']
    _req(c, 'post', f'{API}/{rid}/connect', {
        'player': {'host': '192.168.1.108', 'name': '192.168.1.108', 'platform': 'android_tv'}})
    # force offline by clearing clients
    from app import rooms_setup as rs_mod
    # clients still return CLIENTS from fixture — 108 is not among them; must not appear as pick seed from config
    ps = _req(c, 'get', API + '/players').get_json()['players']
    assert all(p['online'] for p in ps)
    assert not any(p['host'] == '192.168.1.108' for p in ps)


def test_entrypoint_starts_adb_under_minimal_when_commands_set():
    text = open(os.path.join(os.path.dirname(__file__), '..', 'entrypoint.sh')).read()
    assert 'LUMA_DISABLE_ADB' in text
    # Must not treat MINIMAL alone as DISABLE
    assert 'LUMA_MINIMAL' in text and 'LUMA_ADB_COMMANDS' in text
    assert 'ADB server ready (LUMA_ADB_HOME/COMMANDS)' in text
    # Misleading log only when DISABLE is actually set
    assert 'LUMA_DISABLE_ADB is set: skipping ADB server' in text
    # MINIMAL must not print that DISABLE line by itself — DISABLE branch is separate
    disable_block = text.split('Starting Luma application')[0]
    # The DISABLE echo is inside the DISABLE-only if, not the MINIMAL connect skip
    assert 'tr \'[:upper:]\'' in disable_block or "tr '[:upper:]'" in disable_block
    assert 'no boot auto-connect' in text


def test_compose_test_keeps_minimal_and_adb_pair_env():
    y = open(os.path.join(os.path.dirname(__file__), '..', 'docker-compose.test.yml')).read()
    assert 'LUMA_MINIMAL=1' in y
    assert 'LUMA_ADB_HOME=/app/data/adb' in y
    assert 'LUMA_ADB_COMMANDS=connect,disconnect,devices,pair' in y
    assert 'LUMA_DISABLE_ADB=1' not in y


def test_sw_v272():
    sw = open(os.path.join(os.path.dirname(__file__), '..', 'static', 'sw.js')).read()
    import re
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 274
