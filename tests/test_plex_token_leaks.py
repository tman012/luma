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
    _assert_token_only_in_headers(calls)
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