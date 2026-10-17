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
    assert isinstance(cmd, list) and not k.get('shell')
    if cmd[-1] == 'window':
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
    state['user'] = ADMIN
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


@pytest.fixture
def play(plex, monkeypatch):
    """Real users with each approval state; playback itself is stubbed."""
    import app.user_auth as ua
    client, calls, state = plex
    state['clients_xml'] = (b'<MediaContainer><Server host="10.0.0.7" port="32500" '
                            b'machineIdentifier="abc"/></MediaContainer>')
    ids = {}
    ids['approved'] = ua.create_user('approved', '1234')['id']
    ua.save_user_settings(ids['approved'], {'use_shared_server': True, 'plex_access_status': 'approved'})
    ids['unapproved'] = ua.create_user('unapproved', '1234')['id']
    ids['pending'] = ua.create_user('pendingplex', '1234')['id']
    ua.save_user_settings(ids['pending'], {'plex_access_status': 'pending'})
    ids['denied'] = ua.create_user('denied', '1234')['id']
    ua.save_user_settings(ids['denied'], {'use_shared_server': False, 'plex_access_status': 'denied'})
    ids['revoked'] = ua.create_user('revoked', '1234')['id']
    ua.save_user_settings(ids['revoked'], {'use_shared_server': True, 'plex_access_status': 'denied'})
    ids['inactive'] = ua.create_user('inactive', '1234', status='pending')['id']
    ua.save_user_settings(ids['inactive'], {'use_shared_server': True, 'plex_access_status': 'approved'})
    played = []

    def fake_play(service, rating_key, resume_offset, room_name, device_ip, *a, **k):
        played.append(device_ip)
        return app.main.jsonify({'success': True})
    monkeypatch.setattr(app.main, '_play_via_plex', fake_play)
    monkeypatch.setattr(app.main, '_play_via_kodi', fake_play)

    def as_user(kind):
        state['user'] = None if kind == 'anonymous' else (
            ADMIN if kind == 'admin' else {'id': ids[kind], 'username': kind, 'is_admin': False})
    return client, calls, as_user, played


@pytest.mark.parametrize('kind', ['unapproved', 'pending', 'denied', 'revoked', 'inactive', 'anonymous'])
def test_player_play_refused_for_unapproved_users(play, kind):
    client, calls, as_user, played = play
    as_user(kind)
    r = _post(client, '/api/player/play', {'rating_key': 5, 'room_id': 'r1'})
    assert r.status_code == 403
    assert played == [] and calls == []


@pytest.mark.parametrize('body, host', [
    ({'rating_key': 5, 'room_id': 'r1'}, '10.0.0.5'),
    ({'rating_key': 5, 'room_id': 'r1', 'device_id': 'd1'}, '10.0.0.5'),
    ({'rating_key': 5, 'room_id': 'r2'}, '10.0.0.6'),
    ({'rating_key': 5, 'client_ip': '10.0.0.6'}, '10.0.0.6'),
])
def test_player_play_approved_user_to_configured_device(play, body, host):
    client, _, as_user, played = play
    as_user('approved')
    r = _post(client, '/api/player/play', body)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert played == [host]


@pytest.mark.parametrize('body', [
    {'rating_key': 5, 'client_ip': '10.0.0.99'},
    {'rating_key': 5, 'client_ip': '10.0.0.7'},          # listed by Plex /clients only
    {'rating_key': 5, 'client_ip': '10.0.0.5:6124'},
    {'rating_key': 5, 'client_ip': '127.0.0.1:6124/steal?'},
    {'rating_key': 5, 'client_ip': '10.0.0.5; id'},
    {'rating_key': 5, 'room_id': 'r2', 'client_ip': '10.0.0.99'},  # flat room: body overrides
])
def test_player_play_approved_user_cannot_pick_other_hosts(play, body):
    client, calls, as_user, played = play
    as_user('approved')
    r = _post(client, '/api/player/play', body)
    assert r.status_code == 403
    assert played == []


def test_player_play_admin_full_access(play):
    client, calls, as_user, played = play
    as_user('admin')
    assert _post(client, '/api/player/play', {'rating_key': 5, 'room_id': 'r1'}).status_code == 200
    assert played == ['10.0.0.5']
    assert _post(client, '/api/player/play', {'rating_key': 5, 'client_ip': '10.0.0.99'}).status_code == 400
    assert _post(client, '/api/player/play', {'rating_key': 5, 'client_ip': '10.0.0.7'}).status_code == 200
    assert played[-1] == '10.0.0.7'


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


def test_seek_zero_is_refused(plex):
    client, calls, _ = plex
    for body in ({'client_ip': '10.0.0.5', 'offset_seconds': 0}, {'client_ip': '10.0.0.5', 'offset_seconds': -0.0},
                 {'client_ip': '10.0.0.5'}):
        assert _post(client, '/api/plex/playback/seek', body).status_code == 400
    assert [c for c in calls if '/player/' in c.url] == []


def test_clients_cache_keyed_on_token(plex):
    client, calls, state = plex
    state['clients_xml'] = (b'<MediaContainer><Server host="10.0.0.7" port="32500" '
                            b'machineIdentifier="abc"/></MediaContainer>')
    _post(client, '/api/plex/playback/stop', {'client_ip': '10.0.0.7'})
    _post(client, '/api/plex/playback/stop', {'client_ip': '10.0.0.7'})
    assert len([c for c in calls if c.url.endswith('/clients')]) == 1
    app.main.settings.update({'plex': {'token': 'rotated-token'}})
    _post(client, '/api/plex/playback/stop', {'client_ip': '10.0.0.7'})
    assert len([c for c in calls if c.url.endswith('/clients')]) == 2
    assert TOKEN not in repr(app.main._plex_clients_cache)


def test_hero_cache_plex_download_token_in_header_and_no_redirects(tmp_path, monkeypatch):
    """A fake Plex that 302s elsewhere: the download fails and nothing is
    fetched from the redirect target; the token is never in a URL."""
    import requests
    from requests.adapters import BaseAdapter
    from requests.models import Response as RResponse
    from app.hero_cache import HeroCache

    seen = []

    class FakePlex(BaseAdapter):
        def send(self, request, **kwargs):
            seen.append((request.url, request.headers.get('X-Plex-Token')))
            r = RResponse()
            r.request, r.url = request, request.url
            r.raw = __import__('io').BytesIO(b'stolen')
            if request.url.startswith('http://plex.test/'):
                r.status_code = 302
                r.headers['Location'] = 'http://evil.test/steal'
            else:
                r.status_code = 200
            return r

        def close(self):
            pass

    hc = HeroCache.__new__(HeroCache)
    hc.plex_url, hc.plex_token = 'http://plex.test', TOKEN
    hc.cache_dir = tmp_path
    hc.session = requests.Session()
    hc.session.mount('http://', FakePlex())
    url = hc._plex_download_url('/library/metadata/5/thumb/1')
    assert TOKEN not in url
    assert hc.download_image(url, 'p.jpg', headers=hc._plex_headers()) is None
    assert seen == [('http://plex.test/library/metadata/5/thumb/1', TOKEN)]
    assert not (tmp_path / 'p.jpg').exists()
