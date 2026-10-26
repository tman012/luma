"""v265 Pair (prod 1b4ea74's ADB flow): `adb connect` -> the TV asks "Allow network debugging?" -> Luma waits
for Allow -> paired. adb is a fake binary / mocked subprocess; nothing contacts a device."""
import os
import stat
import subprocess

import pytest

from app import adb_utils, minimal, rooms_setup
from tests.test_v224_rooms_setup import API, _add, rs  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401


def _room(c):
    return _add(c, 'Living Room', mid='mid-lrtv', host='192.168.1.41').get_json()['rooms'][0]['id']


def test_pair_waits_for_allow_then_marks_paired(rs, monkeypatch):
    c, fake, _ = rs
    rid = _room(c)
    seen = {}

    def wait(host, port, timeout):
        seen.update(host=host, port=port, timeout=timeout)
        return {'ok': True, 'state': 'connected', 'hint': 'connected'}
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn', wait)
    r = _req(c, 'post', f'{API}/{rid}/connect')
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'state': 'connected', 'hint': 'connected'}
    assert seen == {'host': '192.168.1.41', 'port': 5555, 'timeout': rooms_setup.PAIR_WAIT_S}
    assert fake._settings['devices']['rooms'][0]['devices'][0]['paired'] is True
    assert _req(c, 'get', API).get_json()['rooms'][0]['player']['paired'] is True


def test_pair_not_allowed_in_time_stays_unpaired(rs, monkeypatch):
    c, fake, _ = rs
    # Seed an unpaired device (legacy / re-pair); failed Pair must not flip paired True
    rid = _req(c, 'post', API, {'name': 'Living Room'}).get_json()['rooms'][0]['id']
    fake._settings['devices']['rooms'][0]['devices'] = [{
        'id': 'd1', 'name': 'Living Room TV', 'host': '192.168.1.41', 'platform': 'android_tv',
        'connectPort': 5555, 'paired': False, 'machineId': 'mid-lrtv', 'powerMethod': 'adb',
        'services': [{'id': 's1', 'type': 'plex'}]}]
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn', lambda *a, **k: {'ok': False, 'state': 'unauthorized', 'hint': 'unauthorized'})
    d = _req(c, 'post', f'{API}/{rid}/connect').get_json()
    assert d['ok'] is False and d['state'] == 'unauthorized'
    assert not fake._settings['devices']['rooms'][0]['devices'][0].get('paired')


def test_pair_code_runs_adb_pair_first_and_never_returns_the_code(rs, monkeypatch, caplog):
    c, fake, _ = rs
    rid = _room(c)
    calls = []
    monkeypatch.setitem(rooms_setup._cfg, 'pair_fn', lambda h, p, code: calls.append((h, p, code)) or {'ok': True})
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn', lambda *a, **k: {'ok': True, 'state': 'connected', 'hint': 'connected'})
    r = _req(c, 'post', f'{API}/{rid}/connect', {'pair_port': 37123, 'pair_code': '482913'})
    assert calls == [('192.168.1.41', 37123, '482913')]
    assert '482913' not in r.get_data(as_text=True) and '482913' not in caplog.text
    assert fake._settings['devices']['rooms'][0]['devices'][0]['pairPort'] == 37123
    for bad in ({'pair_port': 0, 'pair_code': '482913'}, {'pair_port': 37123, 'pair_code': '12ab'}):
        assert _req(c, 'post', f'{API}/{rid}/connect', bad).status_code == 400


def test_pair_is_signed_in_owner_only_and_404s(rs, monkeypatch):
    c, _, _ = rs
    rid = _room(c)
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn', lambda *a, **k: pytest.fail('must not connect'))
    assert _req(c, 'post', f'{API}/nope/connect').status_code == 404
    monkeypatch.setitem(rooms_setup._cfg, 'signed_in_owner', lambda: (None, ({'error': 'Sign in first'}, 401)))
    assert _req(c, 'post', f'{API}/{rid}/connect').status_code == 401


def test_minimal_routes():
    assert minimal.path_allowed('/api/rooms/abc/connect', 'POST')
    for p in ('/api/rooms/abc/test', '/api/rooms/abc/pair', '/api/rooms/a/devices/b/test', '/api/rooms/a/devices/b/pair'):
        assert not minimal.path_allowed(p, 'POST'), p
    assert minimal.path_allowed('/api/plex/sessions/active', 'GET')
    assert not minimal.path_allowed('/api/plex/sessions/active', 'POST')
    assert minimal.path_allowed('/api/room/abc/remote', 'POST')
    assert not minimal.path_allowed('/api/room/abc/remote', 'GET')


# ---------------------------------------------------------------- adb_utils (subprocess mocked)

class FakeAdb:
    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw.get('env', {}).get('HOME')))
        sub = adb_utils._subcommand(args)
        out = ''
        if sub == 'connect':
            out = 'connected to 192.168.1.41:5555' if self.states[0] == 'device' else 'failed to authenticate to 192.168.1.41:5555'
        elif sub == 'devices':
            st = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            out = f'List of devices attached\n192.168.1.41:5555\t{st}\n'
        return subprocess.CompletedProcess(args, 0, out, '')


def _patch(monkeypatch, fake, home):
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)
    monkeypatch.setattr(adb_utils, '_has_adb', lambda: True)
    monkeypatch.setattr(adb_utils.subprocess, 'run', fake)
    monkeypatch.setattr(adb_utils, 'ADB_HOME', str(home))
    monkeypatch.setattr(adb_utils, 'ADB_ENV', {'HOME': str(home)})


def test_wait_for_authorization_until_allow(monkeypatch, tmp_path):
    fake = FakeAdb(['unauthorized', 'unauthorized', 'device'])
    _patch(monkeypatch, fake, tmp_path)
    (tmp_path / '.android').mkdir()
    for n in ('adbkey', 'adbkey.pub'):
        (tmp_path / '.android' / n).write_text('k')
        os.chmod(tmp_path / '.android' / n, 0o644)
    t = [0.0]
    r = adb_utils.wait_for_authorization('192.168.1.41', 5555, timeout=30, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert r['ok'] is True and r['state'] == 'connected'
    assert all(h == str(tmp_path) for _, h in fake.calls)
    for n in ('adbkey', 'adbkey.pub'):
        assert stat.S_IMODE(os.stat(tmp_path / '.android' / n).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / '.android').st_mode) == 0o700


def test_wait_for_authorization_times_out(monkeypatch, tmp_path):
    _patch(monkeypatch, FakeAdb(['unauthorized']), tmp_path)
    t = [0.0]
    r = adb_utils.wait_for_authorization('192.168.1.41', 5555, timeout=5, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert r == {'ok': False, 'state': 'unauthorized', 'hint': 'unauthorized', 'detail': ''}
    assert t[0] >= 5


def test_adb_commands_allowlist_blocks_shell(monkeypatch, tmp_path):
    fake = FakeAdb(['device'])
    _patch(monkeypatch, fake, tmp_path)
    monkeypatch.setattr(adb_utils, '_ADB_ALLOWED', frozenset({'connect', 'disconnect', 'devices', 'pair'}))
    r = adb_utils.run_adb(['adb', '-s', '192.168.1.41:5555', 'shell', 'input', 'keyevent', '85'])
    assert r['ok'] is False and fake.calls == []
    assert adb_utils.run_adb(['adb', 'devices'])['ok'] is True


def test_test_compose_keeps_the_adb_key_on_the_data_volume():
    y = open(os.path.join(os.path.dirname(__file__), '..', 'docker-compose.test.yml')).read()
    assert 'LUMA_ADB_HOME=/app/data/adb' in y and 'luma-test-data:/app/data' in y
    assert 'LUMA_ADB_COMMANDS=connect,disconnect,devices,pair' in y and 'LUMA_DISABLE_ADB=1' not in y


# ---------------------------------------------------------------- Remote side channels under ADB allowlist (Tester MED)

def test_remote_skips_kodi_ha_wol_when_allowlist_active_but_harmony_opens(minimal_app, owner, monkeypatch):
    """v285: LUMA_ADB_COMMANDS still blocks Kodi / HA / WoL / ADB shell; Harmony Hub
    by room IP stays open so volume / transport work on LIVE. Pair/Connect unchanged."""
    import subprocess
    import app.main as M
    from app import wol, harmony_service, ha_service, kodi_service
    from tests.test_home_rails import REMOTE

    calls = []
    monkeypatch.setattr(wol, 'send_wol', lambda mac: calls.append(('WOL', mac)) or {'ok': True, 'detail': 'sent'})
    monkeypatch.setattr(harmony_service, 'get_current_activity', lambda ip: calls.append(('HARMONY get', ip)) or 'Watch TV')
    monkeypatch.setattr(harmony_service, 'power_off', lambda ip: calls.append(('HARMONY power_off', ip)) or True)
    monkeypatch.setattr(harmony_service, 'send_command', lambda ip, d, c: calls.append(('HARMONY cmd', d, c)) or True)
    monkeypatch.setattr(ha_service, 'fire_entity', lambda u, t, e: calls.append(('HA fire', e)) or {'ok': True})

    class K:
        def __init__(s, *a, **k):
            calls.append(('KODI conn',) + a[:2])

        def get_active_player(s):
            return {'playerid': 1}

        def pause_toggle(s, i):
            calls.append(('KODI pause_toggle', i))

        def stop(s, i):
            calls.append(('KODI stop', i))

    monkeypatch.setattr(kodi_service, 'KodiService', K)
    monkeypatch.setattr(subprocess, 'run', lambda args, **k: calls.append(('SUBPROCESS', ' '.join(str(a) for a in args))) or subprocess.CompletedProcess(args, 0, 'connected', ''))
    monkeypatch.setattr(adb_utils, '_ADB_ALLOWED', frozenset({'connect', 'disconnect', 'devices', 'pair'}))
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)
    monkeypatch.setattr(adb_utils, '_has_adb', lambda: True)
    assert adb_utils.adb_allowlist_active() is True

    room = {
        'id': 'fr', 'name': 'Family Room', 'useHarmonyHub': True, 'harmonyHubIp': '10.0.0.9',
        'harmonyVolumeDevice': 'AVR',
        'devices': [{
            'id': 'd1', 'host': '10.0.0.50', 'connectPort': 5555, 'macAddress': 'AA:BB:CC:DD:EE:FF',
            'powerMethod': 'homeassistant', 'haPowerEntity': 'switch.tv',
            'harmonyNavigationDevice': 'Shield',
            'services': [{'type': 'kodi', 'kodi_host': '10.0.0.50'}],
        }],
    }
    monkeypatch.setattr(M.settings, 'get_room_by_id', lambda rid: room)
    _orig_get = M.settings.get
    def _get(key, default=None):
        if key == 'homeassistant.url':
            return 'http://ha.local'
        if key == 'homeassistant.access_token':
            return 'tok'
        return _orig_get(key, default)
    monkeypatch.setattr(M.settings, 'get', _get)

    def _blocked_side_channels():
        out = []
        for c in calls:
            if c[0] in ('WOL', 'HA fire', 'KODI conn', 'KODI pause_toggle', 'KODI stop'):
                out.append(c)
            elif c[0] == 'SUBPROCESS' and ' shell' in f' {c[1]} ':
                out.append(c)
        return out

    c = owner
    # Harmony power / volume / play_pause / home open under allowlist when Hub IP set
    r = c.post('/api/room/fr/remote', json={'command': 'power'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json().get('ok') is True
    assert ('HARMONY get', '10.0.0.9') in calls and ('HARMONY power_off', '10.0.0.9') in calls
    assert _blocked_side_channels() == [], calls
    calls.clear()

    r = c.post('/api/room/fr/remote', json={'command': 'vol_up'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json().get('ok') is True
    assert ('HARMONY cmd', 'AVR', 'VolumeUp') in calls
    assert _blocked_side_channels() == [], calls
    calls.clear()

    r = c.post('/api/room/fr/remote', json={'command': 'play_pause'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json().get('ok') is True
    assert ('HARMONY cmd', 'Shield', 'Pause') in calls
    assert _blocked_side_channels() == [], calls
    calls.clear()

    r = c.post('/api/room/fr/remote', json={'command': 'home'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json().get('ok') is True
    assert ('HARMONY cmd', 'Shield', 'Home') in calls
    assert _blocked_side_channels() == [], calls
    calls.clear()

    # HA-only power (no Hub IP): still no fire under allowlist
    room['useHarmonyHub'] = False
    room['harmonyHubIp'] = ''
    room['devices'][0]['services'] = []
    r = c.post('/api/room/fr/remote', json={'command': 'power'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json().get('ok') is False
    assert 'LUMA_ADB_COMMANDS' in (r.get_json().get('detail') or '')
    assert _blocked_side_channels() == [], calls
    assert not any(c[0].startswith('HARMONY') for c in calls), calls
    calls.clear()

    # No Hub IP + Harmony flag with empty IP: volume must not reach Hub; ADB shell blocked
    room['useHarmonyHub'] = True
    room['harmonyHubIp'] = ''
    r = c.post('/api/room/fr/remote', json={'command': 'vol_up'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json().get('ok') is False
    assert 'LUMA_ADB_COMMANDS' in (r.get_json().get('detail') or '')
    assert not any(c[0].startswith('HARMONY') for c in calls), calls
    assert _blocked_side_channels() == [], calls


def test_remote_side_channels_open_when_allowlist_unset(minimal_app, owner, monkeypatch):
    """Prod: empty allowlist — Harmony / Kodi / HA / WoL still fire from Remote."""
    import subprocess
    import app.main as M
    from app import wol, harmony_service, ha_service, kodi_service
    from tests.test_home_rails import REMOTE

    calls = []
    monkeypatch.setattr(wol, 'send_wol', lambda mac: calls.append(('WOL', mac)) or {'ok': True, 'detail': 'sent'})
    monkeypatch.setattr(harmony_service, 'get_current_activity', lambda ip: calls.append(('HARMONY get', ip)) or 'Watch TV')
    monkeypatch.setattr(harmony_service, 'power_off', lambda ip: calls.append(('HARMONY power_off', ip)) or True)
    monkeypatch.setattr(harmony_service, 'send_command', lambda ip, d, c: calls.append(('HARMONY cmd', d, c)) or True)
    monkeypatch.setattr(ha_service, 'fire_entity', lambda u, t, e: calls.append(('HA fire', e)) or {'ok': True})

    class K:
        def __init__(s, *a, **k):
            calls.append(('KODI conn',) + a[:2])

        def get_active_player(s):
            return {'playerid': 1}

        def pause_toggle(s, i):
            calls.append(('KODI pause_toggle', i))

        def stop(s, i):
            calls.append(('KODI stop', i))

    monkeypatch.setattr(kodi_service, 'KodiService', K)
    monkeypatch.setattr(subprocess, 'run', lambda args, **k: calls.append(('SUBPROCESS', ' '.join(str(a) for a in args))) or subprocess.CompletedProcess(args, 0, '', ''))
    monkeypatch.setattr(adb_utils, '_ADB_ALLOWED', frozenset())
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)
    monkeypatch.setattr(adb_utils, '_has_adb', lambda: True)
    assert adb_utils.adb_allowlist_active() is False

    room = {
        'id': 'fr', 'name': 'Family Room', 'useHarmonyHub': True, 'harmonyHubIp': '10.0.0.9',
        'harmonyVolumeDevice': 'AVR',
        'devices': [{
            'id': 'd1', 'host': '10.0.0.50', 'connectPort': 5555, 'macAddress': 'AA:BB:CC:DD:EE:FF',
            'powerMethod': 'homeassistant', 'haPowerEntity': 'switch.tv',
            'services': [{'type': 'kodi', 'kodi_host': '10.0.0.50'}],
        }],
    }
    monkeypatch.setattr(M.settings, 'get_room_by_id', lambda rid: room)
    _orig_get = M.settings.get
    def _get(key, default=None):
        if key == 'homeassistant.url':
            return 'http://ha.local'
        if key == 'homeassistant.access_token':
            return 'tok'
        return _orig_get(key, default)
    monkeypatch.setattr(M.settings, 'get', _get)

    c = owner
    r = c.post('/api/room/fr/remote', json={'command': 'power'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and ('HARMONY get', '10.0.0.9') in calls and ('HARMONY power_off', '10.0.0.9') in calls
    calls.clear()

    r = c.post('/api/room/fr/remote', json={'command': 'vol_up'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and ('HARMONY cmd', 'AVR', 'VolumeUp') in calls
    calls.clear()

    r = c.post('/api/room/fr/remote', json={'command': 'play_pause'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and any(x[0] == 'KODI pause_toggle' for x in calls)
    calls.clear()

    # HA power when Hub IP is cleared (Harmony path inactive)
    room['useHarmonyHub'] = False
    room['harmonyHubIp'] = ''
    room['devices'][0]['services'] = []
    r = c.post('/api/room/fr/remote', json={'command': 'power'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and ('HA fire', 'switch.tv') in calls
    calls.clear()

    # No Harmony / no Kodi / no HA → WoL then ADB
    room['devices'][0]['powerMethod'] = ''
    r = c.post('/api/room/fr/remote', json={'command': 'home'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and ('WOL', 'AA:BB:CC:DD:EE:FF') in calls


def test_pair_still_works_with_allowlist_active(rs, monkeypatch):
    """Pair/Connect must keep working when the ADB allowlist (test safety) is on."""
    monkeypatch.setattr(adb_utils, '_ADB_ALLOWED', frozenset({'connect', 'disconnect', 'devices', 'pair'}))
    assert adb_utils.adb_allowlist_active() is True
    c, fake, _ = rs
    rid = _room(c)
    monkeypatch.setitem(rooms_setup._cfg, 'wait_fn', lambda *a, **k: {'ok': True, 'state': 'connected', 'hint': 'connected'})
    r = _req(c, 'post', f'{API}/{rid}/connect')
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'state': 'connected', 'hint': 'connected'}
    assert fake._settings['devices']['rooms'][0]['devices'][0]['paired'] is True
