"""OMDb quota protection for title ratings: negative cache, per-load warm cap, daily warm budget, https."""
import pytest

import app.main
from app import omdb


class Resp:
    def __init__(self, ok, body):
        self.ok, self._b = ok, body

    def json(self):
        return self._b


class FakeReq:
    def __init__(self, body=None, ok=True):
        self.calls, self.body, self.ok = [], body or {'Response': 'False', 'Error': 'Movie not found!'}, ok

    def get(self, url, params=None, **kw):
        self.calls.append((url, dict(params or {})))
        return Resp(self.ok, self.body)


@pytest.fixture
def fake(monkeypatch):
    f = FakeReq()
    monkeypatch.setattr(app.main, 'requests', f)
    monkeypatch.setattr(omdb, 'api_key', lambda: 'k-test')
    omdb._reset_guard()
    app.main._omdb_ratings_cache.clear()
    yield f
    omdb._reset_guard()
    app.main._omdb_ratings_cache.clear()


def call(title, headers=None, year=''):
    with app.main.app.test_request_context(f'/api/ratings/{title}?year={year}', headers=headers or {}):
        r = app.main.get_ratings(title)
        return r if isinstance(r, tuple) else (r, 200)


def test_https_url(fake):
    call('Arrival', year='2016')
    url, params = fake.calls[0]
    assert url.startswith('https://www.omdbapi.com/')
    assert params['t'] == 'Arrival' and params['y'] == '2016'
    assert all('http://www.omdbapi.com' not in open(p).read() for p in ('app/main.py', 'app/omdb.py', 'app/imdb_ratings.py', 'app/genre_roulette.py'))


def test_negative_cache_ttl(fake, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(omdb, '_now', lambda: t[0])
    assert call('Nope')[1] == 404
    assert call('Nope')[1] == 404
    assert len(fake.calls) == 1              # the miss is remembered
    t[0] += omdb.NEG_TTL_S - 1
    call('Nope')
    assert len(fake.calls) == 1
    t[0] += 2                                # past ~6 h: asks again
    call('Nope')
    assert len(fake.calls) == 2
    assert omdb.NEG_TTL_S == 6 * 3600


def test_errors_are_negative_cached(fake):
    fake.ok = False
    assert call('Err')[1] == 500
    call('Err')
    assert len(fake.calls) == 1


def test_warm_cap_per_load(fake):
    for i in range(omdb.WARMS_PER_LOAD + 3):
        call(f'T{i}', {omdb.WARM_HEADER: 'load-a'})
    assert len(fake.calls) == omdb.WARMS_PER_LOAD == 12
    call('Other', {omdb.WARM_HEADER: 'load-b'})          # a new load has its own cap
    call('Tapped')                                       # a user open is never capped
    assert len(fake.calls) == 14


def test_daily_budget_blocks_warms_not_opens(fake, monkeypatch):
    monkeypatch.setattr(omdb, 'daily_quota', lambda: 10)
    for _ in range(7):                                   # 70% of the quota used
        omdb.count_call()
    r = call('Warm', {omdb.WARM_HEADER: 'load-c'})
    assert r[1] == 429 and fake.calls == []
    call('Open')
    assert len(fake.calls) == 1


def test_quota_configurable(monkeypatch):
    from app.config import Config
    monkeypatch.setattr(Config, 'OMDB_DAILY_QUOTA', 5000, raising=False)
    monkeypatch.setattr('app.settings_manager.SettingsFile.get', lambda self, k, d=None: None)
    assert omdb.daily_quota() == 5000
    monkeypatch.setattr(Config, 'OMDB_DAILY_QUOTA', None, raising=False)
    assert omdb.daily_quota() == omdb.DEFAULT_DAILY_QUOTA == 1000


def test_search_warms_capped_per_query_inside_the_load(fake):
    for i in range(6):
        call(f'S{i}', {omdb.WARM_HEADER: 'load-s/1'})
    assert len(fake.calls) == omdb.WARMS_PER_QUERY == 4
    for i in range(6):
        call(f'Q{i}', {omdb.WARM_HEADER: 'load-s/2'})
    assert len(fake.calls) == 8
    for i in range(10):                                  # Home warms share the load's 12
        call(f'H{i}', {omdb.WARM_HEADER: 'load-s'})
    assert len(fake.calls) == omdb.WARMS_PER_LOAD


class _Pool:
    def submit(self, fn, *a):
        from concurrent.futures import Future
        f = Future()
        if fn is app.main._rt_scores:
            return f                                     # never answers within the budget
        f.set_result(fn(*a))
        return f


def test_inline_ratings_budget_partial_and_merge(fake, monkeypatch):
    fake.body = {'Response': 'True', 'imdbRating': '7.9', 'imdbVotes': '1', 'Metascore': '81', 'Ratings': [], 'imdbID': 'tt1'}
    monkeypatch.setattr(app.main, '_ratings_executor', lambda: _Pool())
    monkeypatch.setattr(app.main, '_plex_ratings_cached', lambda rk: {'imdb': {'score': 8.0}})
    r, partial, skipped = app.main.inline_ratings('movie', 'Arrival', '2016', '5003', budget=0.05)
    assert r['imdb'] == {'score': 8.0}                   # Plex first
    assert r['metacritic'] == {'score': 81}              # OMDb fills
    assert r['rotten_tomatoes_critic'] is None and r['tmdb'] is None
    assert partial is True and skipped is False          # RT missed the budget: left out


def test_inline_ratings_warm_guard(fake, monkeypatch):
    monkeypatch.setattr(app.main, '_ratings_executor', lambda: _Pool())
    monkeypatch.setattr(omdb, 'daily_quota', lambda: 1)
    omdb.count_call()
    r, partial, skipped = app.main.inline_ratings('movie', 'Arrival', '2016', None, warm='w', budget=0.05)
    assert skipped is True and fake.calls == []


def test_detail_routes_carry_ratings(fake, monkeypatch):
    monkeypatch.setattr(app.main, 'inline_ratings', lambda *a, **k: ({'imdb': {'score': 7.0}, 'tmdb': None}, True, False))
    assert set(app.main._INLINE_RATINGS_ROUTES) == {'movie_details', 'tv_details', 'plex_movie_metadata', 'plex_tv_metadata'}
    monkeypatch.setitem(app.main._INLINE_RATINGS_ROUTES, '', app.main._INLINE_RATINGS_ROUTES['tv_details'])
    with app.main.app.test_request_context('/api/tv/1'):
        from flask import jsonify
        resp = app.main._detail_ratings_inline(jsonify({'name': 'Severance', 'first_air_date': '2022-02-18'}))
        data = resp.get_json()
    assert data['ratings']['imdb'] == {'score': 7.0} and data['ratings_partial'] is True
