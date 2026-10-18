"""v157b (Reviewer): /api/img/tmdb streams upstream images with a 10 MB cap, no proxy env and no
redirects on both branches (TMDB and TheTVDB); the TMDB/Trakt key tests use the same kind of session."""
import pytest
import requests

import app.main
from app import connectors
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_polish_v156 import _Resp, _Sess


@pytest.fixture
def proxy(owner, monkeypatch):  # noqa: F811
    _Sess.calls, _Sess.answer = [], _Resp()
    monkeypatch.setattr(app.main.requests, 'Session', _Sess)
    monkeypatch.setattr(app.main.requests, 'get', lambda *a, **k: pytest.fail('a bare requests.get'))
    return owner


def _img(c, path, host=None):
    q = {'path': path, 'size': 'w300' if host is None else 'w500'}
    if host:
        q['host'] = host
    return c.get('/api/img/tmdb', query_string=q, environ_base=REMOTE, headers=HDR)


@pytest.mark.parametrize('host,url', [(None, 'https://image.tmdb.org/t/p/w500/abc.jpg'),
                                      ('tvdb', 'https://artworks.thetvdb.com/abc.jpg')])
def test_both_branches_stream_without_proxy_env_or_redirects(proxy, host, url):
    r = _img(proxy, '/abc.jpg', host)
    assert r.status_code == 200 and r.data == b'\xff\xd8img'
    got, k, trust = _Sess.calls[0]
    assert got == url and k['allow_redirects'] is False and k['stream'] is True and trust is False


@pytest.mark.parametrize('host', [None, 'tvdb'])
def test_a_redirect_is_not_followed(proxy, host):
    _Sess.answer = _Resp(302, 'text/html')
    assert _img(proxy, '/abc.jpg', host).status_code == 404 and len(_Sess.calls) == 1


@pytest.mark.parametrize('host', [None, 'tvdb'])
def test_a_declared_size_over_the_cap_is_refused_unread(proxy, host, monkeypatch):
    monkeypatch.setattr(app.main, 'IMG_PROXY_MAX_BYTES', 1000)
    ans = _Resp(body=b'x' * 10, length=5000)
    ans.iter_content = lambda **k: pytest.fail('read past a too-large Content-Length')
    _Sess.answer = ans
    assert _img(proxy, '/abc.jpg', host).status_code == 502 and ans.closed


@pytest.mark.parametrize('host', [None, 'tvdb'])
def test_a_body_running_past_the_cap_is_cut_off(proxy, host, monkeypatch):
    monkeypatch.setattr(app.main, 'IMG_PROXY_MAX_BYTES', 100_000)
    ans = _Resp(body=b'x' * 300_000)                                  # no Content-Length (chunked)
    _Sess.answer = ans
    assert _img(proxy, '/abc.jpg', host).status_code == 502 and ans.closed
    assert app.main.IMG_PROXY_MAX_BYTES == 100_000


def test_the_cap_is_10_mb():
    assert app.main.IMG_PROXY_MAX_BYTES == 10 * 1024 * 1024


def test_tmdb_branch_refuses_dot_dot_and_double_slash(proxy):
    assert _img(proxy, '/a/../b.jpg').status_code == 400 and _Sess.calls == []


def test_tmdb_branch_unreachable_is_502(proxy, monkeypatch):
    def boom(self, url, **k):
        raise requests.ConnectionError('down')
    monkeypatch.setattr(_Sess, 'get', boom)
    assert _img(proxy, '/abc.jpg').status_code == 502


@pytest.mark.parametrize('fn,arg', [(connectors.test_tmdb_key, 'abcdef0123456789abcdef0123456789'),
                                    (connectors.test_trakt_client_id, 'c' * 64)])
def test_key_tests_use_no_proxy_env_and_never_follow_a_redirect(monkeypatch, fn, arg):
    calls = []

    class S(_Sess):
        def get(self, url, **k):
            calls.append((url, k, self.trust_env))
            return _Resp(307, 'text/html')
    monkeypatch.setattr(connectors.requests, 'Session', S)
    monkeypatch.setattr(connectors.requests, 'get', lambda *a, **k: pytest.fail('a bare requests.get'))
    assert fn(arg) == 'Unexpected response'
    (url, k, trust), = calls
    assert k['allow_redirects'] is False and trust is False and k['timeout'] == 8
