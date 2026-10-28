"""Settings > Connectors: qBittorrent. Owner only, password never returned,
pre-filled from (and saved to) the settings qbittorrent section that
/api/downloads/add uses, Test = login + app/version with a specific error."""

import pytest

import app.main
from app import qbit_connector
from tests.test_home_rails import REMOTE, _client

HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}
API = '/api/settings/connectors/qbittorrent'
PW = 'pw-' + 'x' * 13   # test-only value


@pytest.fixture
def minimal(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    # The owner is the first admin each test creates (no Start-flow state left from other tests).
    from app import start_flow
    monkeypatch.setattr(start_flow, '_load_state', lambda: {})
    saved = app.main.settings.get_section('qbittorrent')
    app.main.settings.update({'qbittorrent': {'url': '', 'username': '', 'password': '', 'app_category': 'luma'}})
    yield test_app
    app.main.settings.update({'qbittorrent': saved})
    qbit_connector._tests.clear()


class R:
    def __init__(self, code=200, text=''):
        self.status_code, self.text = code, text


class FakeQbit:
    """qBittorrent's Web API: login (Ok./Fails./204), app/version, torrents."""

    def __init__(self, user='admin', password=PW, version='v4.6.5', login_code=200,
                 version_code=200):
        self.user, self.password, self.version = user, password, version
        self.login_code, self.version_code = login_code, version_code
        self.calls = []
        self.exc = None

    def session(self):
        fake = self

        class S:
            def __init__(self):
                self.ok = False

            def post(self, url, data=None, **kw):
                fake.calls.append(('POST', url, dict(data or {}), kw))
                if fake.exc:
                    raise fake.exc
                if url.endswith('/api/v2/auth/login'):
                    creds_ok = ((data or {}).get('username') == fake.user
                                and (data or {}).get('password') == fake.password)
                    if fake.login_code == 204 and creds_ok:
                        self.ok = True
                        return R(204, '')
                    if fake.login_code != 200:
                        return R(fake.login_code, 'Forbidden')
                    self.ok = creds_ok
                    return R(200, 'Ok.' if self.ok else 'Fails.')
                return R(404)

            def get(self, url, **kw):
                fake.calls.append(('GET', url, None, kw))
                if url.endswith('/api/v2/app/version'):
                    if not self.ok:
                        return R(403, 'Forbidden')
                    if fake.version_code == 204:
                        return R(204, fake.version)
                    return R(fake.version_code, fake.version)
                return R(404)

            def close(self):
                pass
        return S()


@pytest.fixture
def qbit(monkeypatch):
    fake = FakeQbit()
    monkeypatch.setattr(qbit_connector.requests, 'Session', fake.session)
    return fake


def _put(c, body):
    return c.put(API, json=body, environ_base=REMOTE, headers=HDR)


def _test(c, body):
    return c.post(API + '/test', json=body, environ_base=REMOTE, headers=HDR)


def _get(c):
    return c.get(API, environ_base=REMOTE, headers=HDR)


# ------------------------------------------------------------------ access

def test_paths_allowlisted_in_minimal_and_not_signed_out():
    from app import minimal
    for p in (API, API + '/test'):
        assert minimal.path_allowed(p, 'PUT') and minimal.path_allowed(p, 'POST')
        assert p not in minimal.SIGNED_OUT_PATHS


def test_owner_only(minimal, qbit):
    _client(minimal)  # the owner
    for c in (_client(minimal, admin=True, name='second'), _client(minimal, admin=False, name='kid')):
        assert _get(c).status_code == 403
        assert _put(c, {'url': 'http://qbit.local:8080'}).status_code == 403
        assert _test(c, {}).status_code == 403
    anon = minimal.test_client()
    assert anon.get(API, environ_base=REMOTE, headers=HDR).status_code in (401, 302)
    assert qbit.calls == []


# ------------------------------------------------------------------ read

def test_fresh_install_starts_empty(minimal):
    c = _client(minimal)
    r = _get(c)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    assert r.get_json() == {'url': '', 'username': '', 'category': '', 'password': {'set': False}, 'add_paused': False}


def test_prefills_from_settings_section_and_never_returns_password(minimal):
    app.main.settings.update({'qbittorrent': {'url': 'http://qbit.local:8080', 'username': 'admin',
                                              'password': PW, 'app_category': 'luma-test'}})
    c = _client(minimal)
    r = _get(c)
    assert r.get_json() == {'url': 'http://qbit.local:8080', 'username': 'admin', 'category': 'luma-test',
                            'password': {'set': True}, 'add_paused': False}
    assert PW not in r.get_data(as_text=True)


# ------------------------------------------------------------------ save

def test_save_writes_the_same_section_download_add_reads(minimal):
    c = _client(minimal)
    r = _put(c, {'url': ' http://qbit.local:8080/ ', 'username': 'admin', 'password': PW, 'category': 'luma-test'})
    assert r.status_code == 200, r.get_json()
    assert PW not in r.get_data(as_text=True)
    s = app.main.settings
    assert s.get('qbittorrent.url') == 'http://qbit.local:8080'
    assert s.get('qbittorrent.username') == 'admin'
    assert s.get('qbittorrent.password') == PW
    assert s.get('qbittorrent.app_category') == 'luma-test'
    assert app.main._qbit_app_category() == 'luma-test'


def test_save_keeps_stored_password_when_left_blank_or_masked(minimal):
    c = _client(minimal)
    assert _put(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW, 'category': 'a'}).status_code == 200
    assert _put(c, {'url': 'http://qbit.local:8080/', 'username': 'admin2', 'category': 'b'}).status_code == 200
    assert _put(c, {'url': 'http://qbit.local:8080', 'username': 'admin2', 'password': '********', 'category': 'b'}).status_code == 200
    assert app.main.settings.get('qbittorrent.password') == PW
    assert app.main.settings.get('qbittorrent.username') == 'admin2'


def test_new_url_needs_the_password_again(minimal):
    c = _client(minimal)
    _put(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW, 'category': 'a'})
    r = _put(c, {'url': 'http://elsewhere.example:8080', 'username': 'admin', 'category': 'a'})
    assert r.status_code == 400 and r.get_json()['fields'] == {'password': 'Required'}
    assert app.main.settings.get('qbittorrent.url') == 'http://qbit.local:8080'
    r = _put(c, {'url': 'http://elsewhere.example:8080', 'username': 'admin', 'password': 'new-one', 'category': 'a'})
    assert r.status_code == 200
    assert app.main.settings.get('qbittorrent.password') == 'new-one'


@pytest.mark.parametrize('body,field,msg', [
    ({'url': ''}, 'url', 'Required'),
    ({'url': 'ftp://qbit.local'}, 'url', 'Invalid URL'),
    ({'url': 'http://user:pw@qbit.local'}, 'url', 'Invalid URL'),
    ({'url': 'http://qbit.local:99999'}, 'url', 'Invalid URL'),
    ({'url': 'http://qbit.local?x=1'}, 'url', 'Invalid URL'),
    ({'url': 'http://qbit.local', 'category': 'a/b'}, 'category', 'Invalid'),
    ({'url': 'http://qbit.local', 'username': 'a\nb'}, 'username', 'Invalid'),
])
def test_save_validates(minimal, body, field, msg):
    c = _client(minimal)
    r = _put(c, body)
    assert r.status_code == 400 and r.get_json()['fields'][field] == msg
    assert app.main.settings.get('qbittorrent.url') == ''


# ------------------------------------------------------------------ test

def test_test_logs_in_and_reads_version(minimal, qbit):
    c = _client(minimal)
    r = _test(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW})
    assert r.get_json() == {'ok': True, 'message': 'Connected', 'version': 'v4.6.5'}
    assert [(m, u) for m, u, _, _ in qbit.calls] == [
        ('POST', 'http://qbit.local:8080/api/v2/auth/login'), ('GET', 'http://qbit.local:8080/api/v2/app/version')]
    assert all(kw.get('allow_redirects') is False and kw.get('timeout') for _, _, _, kw in qbit.calls)


def test_test_wrong_password_is_specific_and_secret_free(minimal, qbit):
    c = _client(minimal)
    r = _test(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': 'nope-secret'})
    body = r.get_data(as_text=True)
    assert r.get_json() == {'ok': False, 'error': 'Wrong username or password'}
    assert 'nope-secret' not in body and 'qbit.local' not in body


@pytest.mark.parametrize('setup,msg', [
    (lambda q: setattr(q, 'login_code', 403), 'qBittorrent blocked this address after failed logins'),
    (lambda q: setattr(q, 'login_code', 404), 'Not a qBittorrent Web UI'),
    (lambda q: setattr(q, 'login_code', 502), 'qBittorrent answered HTTP 502'),
    (lambda q: setattr(q, 'exc', qbit_connector.requests.exceptions.ConnectTimeout()), 'Timed out'),
    (lambda q: setattr(q, 'exc', qbit_connector.requests.exceptions.ConnectionError()), "Can't reach qBittorrent"),
    (lambda q: setattr(q, 'exc', qbit_connector.requests.exceptions.SSLError()), 'Certificate error'),
])
def test_test_errors(minimal, qbit, setup, msg):
    setup(qbit)
    c = _client(minimal)
    r = _test(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW})
    assert r.get_json() == {'ok': False, 'error': msg}


def test_test_uses_saved_config_and_password_only_for_saved_url(minimal, qbit):
    c = _client(minimal)
    _put(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW, 'category': 'luma-test'})
    assert _test(c, {}).get_json()['ok'] is True
    assert _test(c, {'url': 'http://qbit.local:8080', 'username': 'admin'}).get_json()['ok'] is True
    n = len(qbit.calls)
    r = _test(c, {'url': 'http://evil.example:8080', 'username': 'admin'})
    assert r.status_code == 400 and r.get_json()['error'] == 'Required'
    assert len(qbit.calls) == n          # the stored password went nowhere


def test_test_is_rate_limited(minimal, qbit):
    c = _client(minimal)
    body = {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW}
    answers = [_test(c, body).get_json() for _ in range(qbit_connector.THROTTLE_MAX + 1)]
    assert answers[-1] == {'ok': False, 'error': 'Rate limited'}


def test_odd_version_text_is_dropped(minimal, qbit):
    qbit.version = '<script>alert(1)</script>'
    c = _client(minimal)
    r = _test(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW})
    assert r.get_json() == {'ok': True, 'message': 'Connected', 'version': ''}


# ------------------------------------------------------------------ download path

def test_download_add_uses_saved_connector(minimal, monkeypatch):
    """After a Save, /api/downloads/add logs in to the saved URL with the saved
    user and password and adds to the saved category."""
    c = _client(minimal)
    assert _put(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW,
                    'category': 'luma-test'}).status_code == 200
    seen = []
    magnet = 'magnet:?xt=urn:btih:' + 'a' * 40

    class S:
        def post(self, url, data=None, **kw):
            seen.append(('POST', url, dict(data or {})))
            if url.endswith('/auth/login'):
                return R(200, 'Ok.')
            return R(200, 'Ok.')

        def get(self, url, params=None, **kw):
            seen.append(('GET', url, dict(params or {})))

            class J(R):
                def json(self):
                    return [{'hash': 'a' * 40, 'added_on': 1}] if len(seen) > 3 else []
            return J(200)

    import requests as real_requests
    monkeypatch.setattr(real_requests, 'Session', S)
    monkeypatch.setattr('time.sleep', lambda s: None)
    r = c.post('/api/downloads/add', json={'torrent_url': magnet, 'torrent_name': 'x'},
               environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()
    login = [x for x in seen if x[1].endswith('/auth/login')][0]
    assert login[1] == 'http://qbit.local:8080/api/v2/auth/login'
    assert login[2] == {'username': 'admin', 'password': PW}
    add = [x for x in seen if x[1].endswith('/torrents/add')][0]
    assert add[2]['category'] == 'luma-test'


def test_charsetless_ascii_body_is_read_as_utf8():
    """A short body with no charset is decoded as UTF-8, not requests' guess."""
    import requests
    r = requests.models.Response()
    r.status_code, r._content, r.headers['Content-Type'] = 200, b'v4.6.5', 'application/octet-stream'
    assert qbit_connector._text_of(r) == 'v4.6.5'
    r2 = requests.models.Response()
    r2.status_code, r2._content = 200, b'Ok.'
    assert qbit_connector._text_of(r2) == 'Ok.'


def test_add_paused_is_saved_and_shown_and_defaults_off(minimal):
    c = _client(minimal)
    assert c.get('/api/settings/connectors/qbittorrent').get_json()['add_paused'] is False
    r = _put(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW, 'category': 'a', 'add_paused': True})
    assert r.status_code == 200 and r.get_json()['add_paused'] is True
    assert app.main.settings.get('qbittorrent.add_paused') is True
    assert app.main._qbit_add_fields()['paused'] == 'true'
    r = _put(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'category': 'a', 'add_paused': 'yes'})
    assert r.status_code == 400
    assert _put(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'category': 'a'}).get_json()['add_paused'] is True


# ------------------------------------------------------------------ HTTP 204

def test_ok_status_accepts_2xx_including_204():
    assert qbit_connector.ok_status(200) and qbit_connector.ok_status(204)
    assert qbit_connector.ok_status(201) and qbit_connector.ok_status(299)
    assert not qbit_connector.ok_status(199) and not qbit_connector.ok_status(300)
    assert not qbit_connector.ok_status(403) and not qbit_connector.ok_status(502)
    assert not qbit_connector.ok_status('nope') and not qbit_connector.ok_status(None)


def test_login_ok_accepts_200_ok_and_204_empty():
    assert qbit_connector.login_ok(R(200, 'Ok.'))
    assert qbit_connector.login_ok(R(204, ''))
    assert qbit_connector.login_ok(R(204, 'Ok.'))
    assert not qbit_connector.login_ok(R(200, 'Fails.'))
    assert not qbit_connector.login_ok(R(200, ''))          # bare 200+empty stays not-qBit
    assert not qbit_connector.login_ok(R(204, 'Fails.'))
    assert not qbit_connector.login_ok(R(502, 'Ok.'))
    assert not qbit_connector.login_ok(R(403, ''))


def test_test_accepts_login_http_204(minimal, qbit):
    qbit.login_code = 204
    c = _client(minimal)
    r = _test(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW})
    assert r.get_json() == {'ok': True, 'message': 'Connected', 'version': 'v4.6.5'}


def test_test_accepts_version_http_204(minimal, qbit):
    qbit.version_code = 204
    c = _client(minimal)
    r = _test(c, {'url': 'http://qbit.local:8080', 'username': 'admin', 'password': PW})
    assert r.get_json() == {'ok': True, 'message': 'Connected', 'version': 'v4.6.5'}


def test_check_login_204_then_version_200():
    """Direct check(): 204 login + 200 version is Connected."""
    class Sess:
        def post(self, *a, **k):
            return R(204, '')
        def get(self, *a, **k):
            return R(200, 'v5.0.0')
        def close(self):
            pass
    import app.qbit_connector as qc
    # bypass new_session
    orig = qc.new_session
    qc.new_session = lambda factory=None: Sess()
    try:
        problem, version = qc.check('http://qbit.local:8080', 'admin', PW)
    finally:
        qc.new_session = orig
    assert problem is None and version == 'v5.0.0'
