"""Hotfix: no request that carries the qBittorrent password or SID cookie may
follow a redirect (a 307/308 would re-POST the password to the target), and a
3xx on login is a failed sign-in. Real HTTP on 127.0.0.1, nothing mocked in
requests."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import app.main
import app.user_auth as user_auth
from app import qbit_connector

PASSWORD = 'hunter2-qbit-secret'


class _Server:
    def __init__(self, login_status=307, info_status=200):
        self.caught, self.login_status, self.info_status = [], login_status, info_status
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _body(self):
                n = int(self.headers.get('Content-Length') or 0)
                return self.rfile.read(n).decode('utf-8', 'replace') if n else ''

            def _send(self, status, body=b'', headers=None):
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _route(self, method):
                body = self._body()
                if self.path.startswith('/catch'):
                    srv.caught.append((method, self.path, body, self.headers.get('Cookie') or ''))
                    return self._send(200, b'Ok.')
                if self.path == '/api/v2/auth/login':
                    if srv.login_status in (301, 302, 303, 307, 308):
                        return self._send(srv.login_status, headers={'Location': '/catch/login'})
                    return self._send(200, b'Ok.', {'Set-Cookie': 'SID=sid-secret-cookie; path=/'})
                if self.path.startswith('/api/v2/torrents/info'):
                    if srv.info_status != 200:
                        return self._send(srv.info_status, headers={'Location': '/catch/info'})
                    return self._send(200, b'[]', {'Content-Type': 'application/json'})
                if self.path == '/api/v2/app/version':
                    return self._send(307, headers={'Location': '/catch/version'})
                return self._send(404)

            def do_POST(self):
                self._route('POST')

            def do_GET(self):
                self._route('GET')

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    made = []

    def make(**kw):
        s = _Server(**kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_session_never_follows_even_when_asked(server, status):
    s = server(login_status=status)
    sess = qbit_connector.new_session()
    r = sess.post(f'{s.url}/api/v2/auth/login', data={'username': 'u', 'password': PASSWORD},
                  allow_redirects=True, timeout=5)
    assert r.status_code == status and not s.caught
    assert sess.trust_env is False


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_connector_test_treats_a_redirect_as_failed_sign_in(server, status):
    s = server(login_status=status)
    err, version = qbit_connector.check(s.url, 'u', PASSWORD)
    assert err == qbit_connector.SIGN_IN_FAILED and version == ''
    assert 'HTTP' not in err and not s.caught


def test_sid_cookie_is_never_sent_on_to_a_redirect(server):
    s = server(login_status=200, info_status=307)
    sess = qbit_connector.new_session()
    assert sess.post(f'{s.url}/api/v2/auth/login', data={'password': PASSWORD}, timeout=5).text == 'Ok.'
    r = sess.get(f'{s.url}/api/v2/torrents/info', timeout=5)
    assert r.status_code == 307 and not s.caught


def test_after_login_version_redirect_is_not_followed(server):
    s = server(login_status=200)
    err, _ = qbit_connector.check(s.url, 'u', PASSWORD)
    assert err and not s.caught


@pytest.mark.parametrize('status', [307, 308, 302])
def test_mover_login_redirect_is_a_failed_sign_in(server, status):
    library_mover = pytest.importorskip('app.library_mover')   # file-management only
    s = server(login_status=status)
    library_mover.init(settings={'qbittorrent.url': s.url, 'qbittorrent.username': 'u',
                                 'qbittorrent.password': PASSWORD})
    sess, _ = library_mover._qbit_session(None)
    assert sess is None and not s.caught


@pytest.fixture
def grab(test_app, monkeypatch):
    import sys
    import types
    fake = types.ModuleType('app.auto_rename')
    fake.auto_renamer = types.SimpleNamespace(track_download=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, 'app.auto_rename', fake)
    monkeypatch.setattr(app.main, '_record_origin', lambda *a, **k: None)
    c = test_app.test_client()
    user_auth.create_user('owner', '1234', is_admin=True)
    c.set_cookie('luma_auth_session', user_auth.create_session(user_auth.get_user_by_name('owner')['id']))
    return c


@pytest.mark.parametrize('status', [307, 308])
def test_grab_path_never_reposts_the_password(server, grab, status):
    s = server(login_status=status)
    app.main.settings.update({'qbittorrent': {'url': s.url, 'username': 'u', 'password': PASSWORD}})
    r = grab.post('/api/downloads/add', headers={'Origin': 'http://localhost'},
                  environ_base={'REMOTE_ADDR': '192.168.1.20'},
                  json={'torrent_url': 'magnet:?xt=urn:btih:' + 'a' * 40 + '&dn=x'})
    assert r.status_code == 502
    assert r.get_json() == {'error': 'Download client sign-in failed'}
    assert not s.caught


@pytest.mark.parametrize('status', [307, 308])
def test_active_downloads_login_redirect_is_not_followed(server, grab, status):
    s = server(login_status=status)
    app.main.settings.update({'qbittorrent': {'url': s.url, 'username': 'u', 'password': PASSWORD}})
    r = grab.get('/api/downloads/active', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    assert r.get_json() == {'count': 0} and not s.caught


def test_every_qbit_session_in_main_is_the_safe_one():
    import inspect
    import re
    src = inspect.getsource(app.main)
    for m in re.finditer(r'/api/v2/auth/login', src):
        before = src[max(0, m.start() - 400):m.start()]
        assert '_qbit_new_session()' in before, before[-200:]
    assert 'req.Session()' not in src
