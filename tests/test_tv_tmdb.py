"""app/tv_tmdb: TV calls to TMDB through LUMA_TMDB_BASE_URL, key never logged."""
import logging

import pytest

from app import tv_tmdb


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class Fake:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def factory(self):
        fake = self

        class S:
            def get(self, url, params=None, timeout=None, allow_redirects=True):
                fake.calls.append((url, dict(params or {})))
                a = fake.answers.pop(0)
                if isinstance(a, Exception):
                    raise a
                return a

            def close(self):
                pass
        s = S()
        fake.sessions = getattr(fake, 'sessions', []) + [s]
        return s


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv('LUMA_TMDB_BASE_URL', 'http://tmdb.test/3')
    monkeypatch.setattr(tv_tmdb, 'MIN_GAP_S', 0)
    monkeypatch.setattr(tv_tmdb.time, 'sleep', lambda s: None)
    f = Fake([])
    tv_tmdb.init(get_key=lambda: 'k' * 32, session_factory=f.factory)
    yield f
    tv_tmdb._cfg.clear()


def test_search_uses_the_base_url_and_year(fake):
    fake.answers = [Resp(body={'results': [
        {'id': 1399, 'name': 'Game of Thrones', 'original_name': 'Game of Thrones', 'first_air_date': '2011-04-17',
         'poster_path': '/p.jpg', 'overview': 'x'},
        {'id': 'bad'}, {'id': 7, 'name': 'No date'}]})]
    out = tv_tmdb.search('Game of Thrones', 2011)
    assert [(r['tmdb_id'], r['title'], r['year']) for r in out] == [(1399, 'Game of Thrones', 2011), (7, 'No date', None)]
    url, params = fake.calls[0]
    assert url == 'http://tmdb.test/3/search/tv'
    assert params['query'] == 'Game of Thrones' and params['first_air_date_year'] == 2011


def test_find_by_tvdb_and_show_details(fake):
    fake.answers = [Resp(body={'tv_results': [{'id': 1399, 'name': 'GoT', 'first_air_date': '2011-01-01'}]}),
                    Resp(body={'id': 1399, 'name': 'GoT', 'seasons': []}), Resp(404, {}),
                    Resp(body={'id': 5, 'name': 'Other'})]
    assert tv_tmdb.find('121361', 'tvdb_id')[0]['tmdb_id'] == 1399
    assert fake.calls[0] == ('http://tmdb.test/3/find/121361', {'external_source': 'tvdb_id', 'api_key': 'k' * 32})
    assert tv_tmdb.show(1399)['name'] == 'GoT'
    assert tv_tmdb.show(1) is None                    # 404
    assert tv_tmdb.show(6) is None                    # answered for another id
    with pytest.raises(ValueError):
        tv_tmdb.find('1', 'imdb')


def test_errors(fake, caplog):
    caplog.set_level(logging.DEBUG)
    import requests
    fake.answers = [requests.ConnectionError('http://tmdb.test/3?api_key=' + 'k' * 32)]
    with pytest.raises(tv_tmdb.TmdbError) as e:
        tv_tmdb.search('x')
    assert str(e.value) == tv_tmdb.UNREACHABLE
    fake.answers = [Resp(429, {}, {'Retry-After': '1'}), Resp(body={'results': []})]
    assert tv_tmdb.search('x') == []                  # one retry after a rate limit
    fake.answers = [Resp(429, {}), Resp(429, {})]
    with pytest.raises(tv_tmdb.TmdbError) as e:       # Tester tv1-8646479 M5
        tv_tmdb.search('x')
    assert str(e.value) == tv_tmdb.BUSY == 'TMDB is busy. Try again later.'
    fake.answers = [Resp(401, {})]
    with pytest.raises(tv_tmdb.TmdbError, match='refused'):
        tv_tmdb.search('x')
    fake.answers = [Resp(200, ValueError('no json'))]
    with pytest.raises(tv_tmdb.TmdbError):
        tv_tmdb.search('x')
    fake.answers = [Resp(500, {})]
    with pytest.raises(tv_tmdb.TmdbError):
        tv_tmdb.season(1, 1)
    assert 'k' * 32 not in caplog.text


def test_no_key_never_calls(fake):
    tv_tmdb.init(get_key=lambda: '  ', session_factory=fake.factory)
    assert not tv_tmdb.has_key()
    with pytest.raises(tv_tmdb.TmdbError, match='Connectors'):
        tv_tmdb.search('x')
    assert fake.calls == []


def test_session_ignores_the_environment(fake, monkeypatch):
    """Reviewer: trust_env=False, so HTTP(S)_PROXY or ~/.netrc never see the TMDB key."""
    fake.answers = [Resp(body={'results': []})]
    tv_tmdb.search('x')
    assert fake.sessions[-1].trust_env is False


def test_real_session_ignores_proxy_env(monkeypatch):
    import requests
    seen = []

    class Rec(requests.Session):
        def get(self, url, **kw):
            seen.append(self.trust_env)
            raise requests.ConnectionError('offline')
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy.invalid:3128')
    monkeypatch.setattr(tv_tmdb, 'MIN_GAP_S', 0)
    tv_tmdb.init(get_key=lambda: 'k' * 32, session_factory=Rec)
    with pytest.raises(tv_tmdb.TmdbError):
        tv_tmdb.search('x')
    assert seen == [False]
