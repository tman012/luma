"""Prod hotfix: the Plex token never goes into a URL, never reaches a
caller-chosen host, and never comes back to the browser."""

import inspect
import json
from types import SimpleNamespace

import pytest

import app.main

LAN = {'REMOTE_ADDR': '192.168.1.20'}
TOKEN = 'secret-plex-token-xyz'
ADMIN = {'id': 1, 'username': 'owner', 'is_admin': True}
MEMBER = {'id': 2, 'username': 'member', 'is_admin': False}

PLAYBACK = [
    ('/api/plex/playback/control', {'command': 'pause'}),
    ('/api/plex/playback/stop', {}),
    ('/api/plex/playback/seek', {'offset_seconds': 30}),
    ('/api/plex/playback/seek', {'offset_seconds': -30}),
    ('/api/plex/playback/seekTo', {'position_ms': 60000}),
]


class _Resp:
    def __init__(self, status=200, content=b'', data=None):
        self.status_code = status
        self.content = content
        self._data = data
        self.headers = {}

    def json(self):
        return self._data


@pytest.fixture
def plex(test_app, monkeypatch):
    app.main.settings.update({
        'plex': {'url': 'http://plex.test', 'token': TOKEN, 'client_port': 32500},
        'devices': {'rooms': [
            {'id': 'r1', 'name': 'Living room',
             'devices': [{'id': 'd1', 'host': '10.0.0.5', 'services': [{'id': 's1', 'type': 'plex'}]}]},
            {'id': 'r2', 'name': 'Den', 'host': '10.0.0.6'},
        ]},
    })
    calls = []
    state = {'user': ADMIN, 'clients_xml': b'<MediaContainer/>'}
    monkeypatch.setattr(app.main, '_plex_clients_cache', {})
    monkeypatch.setattr(app.main, '_get_current_user', lambda: state['user'])

    def fake_get(url, **k):
        calls.append(SimpleNamespace(url=url, **k))
        if url == 'http://plex.test/clients':
            return _Resp(200, state['clients_xml'])
        return _Resp(200, b'')
    monkeypatch.setattr(app.main.requests, 'get', fake_get)
    return test_app.test_client(), calls, state


def _post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type='application/json', environ_base=LAN)


def _assert_token_only_in_headers(calls):
    for c in calls:
        assert TOKEN not in c.url
        assert TOKEN not in json.dumps(getattr(c, 'params', None) or {}, default=str)
        assert c.headers.get('X-Plex-Token') == TOKEN
        assert c.allow_redirects is False


@pytest.mark.parametrize('path, extra', PLAYBACK)
def test_non_admin_gets_403(plex, path, extra):
    client, calls, state = plex
    state['user'] = MEMBER
    r = _post(client, path, {'client_ip': '10.0.0.5', **extra})
    assert r.status_code == 403
    assert calls == []


@pytest.mark.parametrize('path, extra', PLAYBACK)
@pytest.mark.parametrize('client_ip', [
    '127.0.0.1:6124/steal-control?',
    '10.0.0.5/steal?',
    '10.0.0.5?x=1',
    '10.0.0.5#frag',
    'user@10.0.0.5',
    '10.0.0.5\\evil',
    '10.0.0.5:6124',
    '10.0.0.5:0',
    '10.0.0.5:99999',
    '10.0.0.99',
    'evil.test',
    'http://10.0.0.5',
    ' 10.0.0.5',
    '10.0.0.5 ',
    '10.0.0.5\n',
    123,
    ['10.0.0.5'],
])
def test_injected_or_unknown_client_rejected(plex, path, extra, client_ip):
    client, calls, _ = plex
    r = _post(client, path, {'client_ip': client_ip, **extra})
    assert r.status_code == 400
    assert [c for c in calls if '/player/' in c.url] == []
    assert TOKEN not in r.get_data(as_text=True)


@pytest.mark.parametrize('path, extra', PLAYBACK)
@pytest.mark.parametrize('client_ip', ['10.0.0.5', '10.0.0.5:32500', '10.0.0.6'])
def test_configured_client_gets_token_in_header_only(plex, path, extra, client_ip):
    client, calls, _ = plex
    r = _post(client, path, {'client_ip': client_ip, **extra})
    assert r.status_code == 200, r.get_data(as_text=True)
    player = [c for c in calls if '/player/playback/' in c.url]
    assert len(player) == 1
    host = client_ip.split(':')[0]
    assert player[0].url.startswith(f'http://{host}:32500/player/playback/')
    _assert_token_only_in_headers(calls)
    assert TOKEN not in r.get_data(as_text=True)


def test_seek_to_offset_is_a_number(plex):
    client, calls, _ = plex
    r = _post(client, '/api/plex/playback/seekTo', {'client_ip': '10.0.0.5', 'position_ms': '5&X-Plex-Token=1'})
    assert r.status_code == 400
    assert calls == []
    r = _post(client, '/api/plex/playback/seekTo', {'client_ip': '10.0.0.5', 'position_ms': 1234})
    player = [c for c in calls if '/player/' in c.url]
    assert player[0].url == 'http://10.0.0.5:32500/player/playback/seekTo'
    assert player[0].params == {'offset': 1234}


def test_client_listed_by_plex_is_accepted(plex):
    client, calls, state = plex
    state['clients_xml'] = (b'<MediaContainer><Server name="Shield" host="10.0.0.7" address="10.0.0.7" '
                            b'port="32500" machineIdentifier="abc"/></MediaContainer>')
    r = _post(client, '/api/plex/playback/stop', {'client_ip': '10.0.0.7'})
    assert r.status_code == 200
    assert calls[-1].url == 'http://10.0.0.7:32500/player/playback/stop'
    # A player known only from /clients never gets the owner token.
    assert 'X-Plex-Token' not in calls[-1].headers
    assert calls[-1].headers['X-Plex-Target-Client-Identifier'] == 'abc'
    assert calls[-1].allow_redirects is False
    _assert_token_only_in_headers([c for c in calls if c.url.endswith('/clients')])
    r = _post(client, '/api/plex/playback/stop', {'client_ip': '10.0.0.7:6124'})
    assert r.status_code == 400


def test_player_play_rejects_caller_supplied_address(plex):
    client, calls, _ = plex
    r = _post(client, '/api/player/play', {'rating_key': 5, 'client_ip': '127.0.0.1:6124/steal?'})
    assert r.status_code == 400
    assert [c for c in calls if '/player/' in c.url] == []
    r = _post(client, '/api/player/play', {'rating_key': 5, 'client_ip': '10.0.0.99'})
    assert r.status_code == 400


def test_no_token_in_any_url_in_main():
    src = inspect.getsource(app.main)
    assert 'X-Plex-Token=' not in src
    assert "?X-Plex-Token" not in src


def _fake_adb(cmd, **k):
    if 'dumpsys window' in cmd:
        out = 'mCurrentFocus=Window{1 u0 com.plexapp.android/com.plexapp.main}'
    else:
        out = '\n'.join([
            'Sessions Stack',
            '  Plex package=com.plexapp.android',
            '    active=true',
            '    state=PlaybackState {state=3, position=1000, buffered position=0}',
        ])
    return SimpleNamespace(returncode=0, stdout=out, stderr='')


@pytest.mark.parametrize('path', ['/api/activity', '/api/adb/shield_activity'])
def test_activity_artwork_goes_through_proxy(plex, monkeypatch, path):
    client, calls, state = plex
    state['user'] = MEMBER
    monkeypatch.setattr(app.main.subprocess, 'run', _fake_adb)
    sessions = {'MediaContainer': {'Metadata': [{
        'type': 'movie', 'title': 'Heat', 'year': 1995, 'duration': 600000, 'viewOffset': 60000,
        'thumb': '/library/metadata/5/thumb/1700000000', 'art': '/library/metadata/5/art/1700000000',
        'Player': {'state': 'playing', 'title': 'Shield'},
    }]}}

    def fake_get(url, **k):
        calls.append(SimpleNamespace(url=url, **k))
        return _Resp(200, b'', sessions)
    monkeypatch.setattr(app.main.requests, 'get', fake_get)

    r = client.get(path + '?device_ip=10.0.0.5', environ_base=LAN)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert TOKEN not in body
    media = r.get_json()['media']
    assert media['artwork_url'] == '/api/img/plex?url=/library/metadata/5/thumb/1700000000'
    assert media['backdrop_url'] == '/api/img/plex?url=/library/metadata/5/art/1700000000'
    assert calls and calls[0].url == 'http://plex.test/status/sessions'
    _assert_token_only_in_headers(calls)

def test_rogue_clients_player_never_gets_token(plex):
    """A device that announces itself to Plex is listed in /clients; it must
    not receive the owner token."""
    client, calls, state = plex
    state['clients_xml'] = (b'<MediaContainer><Server name="Rogue" host="10.0.0.66" port="6124" '
                            b'machineIdentifier="rogue-id"/></MediaContainer>')
    for path, extra in PLAYBACK:
        r = _post(client, path, {'client_ip': '10.0.0.66:6124', **extra})
        assert r.status_code == 200
    player = [c for c in calls if '/player/' in c.url]
    assert len(player) == len(PLAYBACK)
    for c in player:
        assert 'X-Plex-Token' not in c.headers
        assert TOKEN not in json.dumps(c.headers) and TOKEN not in c.url
        assert c.headers['X-Plex-Target-Client-Identifier'] == 'rogue-id'


def test_player_play_non_admin_gets_403(plex):
    client, calls, state = plex
    state['user'] = MEMBER
    r = _post(client, '/api/player/play', {'rating_key': 5, 'room_id': 'r1'})
    assert r.status_code == 403
    assert calls == []


@pytest.mark.parametrize('path, field', [('/api/plex/playback/seek', 'offset_seconds'),
                                         ('/api/plex/playback/seekTo', 'position_ms')])
@pytest.mark.parametrize('raw', ['true', 'false', 'NaN', 'Infinity', '-Infinity', '"nan"', '"inf"',
                                 '1e400', 'null', '"x"', '[1]', '{}'])
def test_seek_rejects_non_finite_values(plex, path, field, raw):
    client, calls, _ = plex
    body = '{"client_ip": "10.0.0.5", "%s": %s}' % (field, raw)
    r = client.post(path, data=body, content_type='application/json', environ_base=LAN)
    assert r.status_code == 400, (raw, r.status_code)
    assert [c for c in calls if '/player/' in c.url] == []


@pytest.mark.parametrize('value, sent', [(-5, 0), (1234.9, 1234), (10 ** 12, 24 * 3600 * 1000), ('600000', 600000)])
def test_seek_to_clamped(plex, value, sent):
    client, calls, _ = plex
    r = _post(client, '/api/plex/playback/seekTo', {'client_ip': '10.0.0.5', 'position_ms': value})
    assert r.status_code == 200
    assert [c for c in calls if '/player/' in c.url][-1].params == {'offset': sent}


def test_bracketed_ipv6_room_devices(plex):
    client, calls, _ = plex
    rooms = app.main.settings.get('devices.rooms')
    rooms.append({'id': 'r3', 'name': 'Office', 'devices': [{'id': 'd3', 'host': '[fd00::5]'},
                                                             {'id': 'd4', 'host': 'FD00:0:0::6'}]})
    app.main.settings.update({'devices': {'rooms': rooms}})
    for ip, url in [('[fd00::5]', 'http://[fd00::5]:32500/'), ('[fd00::6]:32500', 'http://[fd00::6]:32500/'),
                    ('[FD00::5]', 'http://[fd00::5]:32500/')]:
        r = _post(client, '/api/plex/playback/stop', {'client_ip': ip})
        assert r.status_code == 200, ip
        assert calls[-1].url.startswith(url)
        assert calls[-1].headers['X-Plex-Token'] == TOKEN
    for ip in ['fd00::5', '[fd00::5]:6124', '[10.0.0.5]', '[fd00::7]', '[fd00::5%eth0]', '[fd00::5]x']:
        assert _post(client, '/api/plex/playback/stop', {'client_ip': ip}).status_code == 400, ip


def test_clients_list_cached_45s(plex, monkeypatch):
    client, calls, state = plex
    now = [1000.0]
    monkeypatch.setattr(app.main.time, 'time', lambda: now[0])
    state['clients_xml'] = (b'<MediaContainer><Server host="10.0.0.7" port="32500" '
                            b'machineIdentifier="abc"/></MediaContainer>')

    def fetches():
        return len([c for c in calls if c.url.endswith('/clients')])
    for _ in range(3):
        assert _post(client, '/api/plex/playback/stop', {'client_ip': '10.0.0.7'}).status_code == 200
    assert fetches() == 1
    now[0] += 44
    _post(client, '/api/plex/playback/stop', {'client_ip': '10.0.0.7'})
    assert fetches() == 1
    now[0] += 2
    _post(client, '/api/plex/playback/stop', {'client_ip': '10.0.0.7'})
    assert fetches() == 2


def test_no_token_query_params_in_app():
    import pathlib
    root = pathlib.Path(app.main.__file__).parent
    for f in root.rglob('*.py'):
        src = f.read_text()
        assert "params={'X-Plex-Token'" not in src, f
        assert "'X-Plex-Token': token,\n" not in src, f
