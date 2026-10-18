"""v156 polish: show-name sort ignores punctuation and a leading The/A/An; TheTVDB
episode images go through Luma's image proxy (artworks.thetvdb.com only)."""
import pytest
import requests

import app.main
from app import tv_scan
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE
from tests.test_tv_libraries import owner, tv_env  # noqa: F401


@pytest.mark.parametrize('title,key', [
    ('S.W.A.T.', 'swat'), ('S.W.A.T. Exiles', 'swat exiles'), ('The Santa Clauses', 'santa clauses'),
    ("Grey's Anatomy", 'greys anatomy'), ('A Knight of the Seven Kingdoms', 'knight of the seven kingdoms'),
    ('An Idiot Abroad', 'idiot abroad'), ('A.P. Bio', 'ap bio'), ('Shōgun', 'shogun'), ('The 4400', '4400'),
    ('Teen Mom: Family Reunion', 'teen mom family reunion'), ('The', 'the'), ('A', 'a'), ('', ''),
])
def test_sort_title(title, key):
    assert tv_scan.sort_title(title) == key


def test_tester_punctuation_cases_sort_by_words():
    names = ['The Santa Clauses', 'S.W.A.T.', 'I, Sniper', 'I Will Find You', 'The Marvelous Mrs. Maisel',
             "Marvel's The Punisher", 'Teen Mom OG', 'Teen Mom: Family Reunion', 'SWAT Kats', 'A Knight of the Seven Kingdoms']
    got = sorted(names, key=tv_scan.sort_title)
    assert got == ['I, Sniper', 'I Will Find You', 'A Knight of the Seven Kingdoms', 'The Marvelous Mrs. Maisel',
                   "Marvel's The Punisher", 'The Santa Clauses', 'S.W.A.T.', 'SWAT Kats',
                   'Teen Mom: Family Reunion', 'Teen Mom OG']


class _Resp:
    def __init__(self, status=200, ctype='image/jpeg', body=b'\xff\xd8img'):
        self.status_code, self.headers, self.content = status, {'Content-Type': ctype}, body


class _Sess:
    calls = []
    answer = _Resp()

    def __init__(self):
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **k):
        _Sess.calls.append((url, k, self.trust_env))
        return _Sess.answer


@pytest.fixture
def tvdb_proxy(owner, monkeypatch):  # noqa: F811
    _Sess.calls, _Sess.answer = [], _Resp()
    monkeypatch.setattr(app.main.requests, 'Session', _Sess)
    monkeypatch.setattr(app.main.requests, 'get', lambda *a, **k: pytest.fail('TMDB path used for a TVDB image'))
    return owner


def _img(c, path):
    return c.get('/api/img/tmdb', query_string={'host': 'tvdb', 'path': path}, environ_base=REMOTE, headers=HDR)


def test_tvdb_images_go_through_the_proxy(tvdb_proxy):
    r = _img(tvdb_proxy, '/banners/v4/episode/123/screencap/abc.jpg')
    assert r.status_code == 200 and r.data == b'\xff\xd8img' and r.mimetype == 'image/jpeg'
    url, k, trust = _Sess.calls[0]
    assert url == 'https://artworks.thetvdb.com/banners/v4/episode/123/screencap/abc.jpg'
    assert k['allow_redirects'] is False and trust is False


@pytest.mark.parametrize('path', ['/../etc/x.jpg', '//evil.com/a.jpg', '/a b.jpg', '/a.jpg?x=1.jpg', '/a.svg', 'a.jpg',
                                  '/a%2F..%2Fb.jpg', '/@evil.com/a.jpg'])
def test_tvdb_proxy_refuses_odd_paths(tvdb_proxy, path):
    assert _img(tvdb_proxy, path).status_code == 400
    assert _Sess.calls == []


@pytest.mark.parametrize('answer', [_Resp(302, 'text/html'), _Resp(200, 'text/html'), _Resp(404)])
def test_tvdb_proxy_passes_only_images(tvdb_proxy, answer):
    _Sess.answer = answer
    assert _img(tvdb_proxy, '/banners/a.jpg').status_code == 404


def test_tvdb_proxy_unreachable_is_502(tvdb_proxy, monkeypatch):
    def boom(self, url, **k):
        raise requests.ConnectionError('down')
    monkeypatch.setattr(_Sess, 'get', boom)
    assert _img(tvdb_proxy, '/banners/a.jpg').status_code == 502
