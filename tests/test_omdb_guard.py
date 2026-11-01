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


def test_http_error_does_not_block_the_next_tap(fake):
    fake.ok = False                                      # OMDb 500
    assert call('Err')[1] == 500
    fake.ok = True
    fake.body = {'Response': 'True', 'imdbRating': '7.0', 'imdbVotes': '5', 'Ratings': [], 'imdbID': 'tt1'}
    r = call('Err')
    assert r[1] == 200 and len(fake.calls) == 2          # a fresh query, not an error entry


def test_timeout_does_not_block_the_next_tap(fake, monkeypatch):
    import requests as real
    def boom(url, params=None, **kw):
        fake.calls.append((url, dict(params or {})))
        raise real.exceptions.Timeout('read timed out')
    monkeypatch.setattr(fake, 'get', boom)
    assert call('Slow')[1] == 500
    assert call('Slow')[1] == 500
    assert len(fake.calls) == 2


def test_error_entry_holds_back_warms_only_briefly(fake, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(omdb, '_now', lambda: t[0])
    fake.ok = False
    call('E')
    call('E', {omdb.WARM_HEADER: 'w1'})
    assert len(fake.calls) == 1                          # a warm doesn't retry within ERR_TTL_S
    t[0] += omdb.ERR_TTL_S
    call('E', {omdb.WARM_HEADER: 'w1'})
    assert len(fake.calls) == 2                          # and does after it
    assert omdb.ERR_TTL_S <= 120


def test_limit_and_bad_key_are_errors_not_not_found(fake):
    for err in ('Request limit reached!', 'Invalid API key!'):
        omdb._reset_guard()
        fake.calls.clear()
        fake.body = {'Response': 'False', 'Error': err}
        assert call('L')[1] == 500
        call('L')
        assert len(fake.calls) == 2                      # never the 6 h entry


def test_api_key_never_in_the_response(fake, monkeypatch, caplog):
    import requests as real
    def boom(url, params=None, **kw):
        raise real.exceptions.SSLError(f"HTTPSConnectionPool: Max retries exceeded with url: /?apikey={params['apikey']}&t=X")
    monkeypatch.setattr(fake, 'get', boom)
    with app.main.app.test_request_context('/api/ratings/X'):
        resp, status = app.main.get_ratings('X')
        body = resp.get_data(as_text=True)
    assert status == 500 and 'k-test' not in body and 'apikey' not in body
    assert resp.get_json() == {'error': 'Failed to fetch ratings'}
    assert 'k-test' not in caplog.text


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
    def submit(self, fn, *a, warm=False):
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


def test_rt_warms_are_capped_per_load(monkeypatch):
    omdb._reset_guard()
    calls = []
    monkeypatch.setattr(app.main.rt_scraper, 'get_reviews', lambda *a, **k: calls.append(a) or {'critic_score': 90})
    monkeypatch.setattr(app.main.rt_scraper, '_get_cached', lambda k: None)
    for i in range(omdb.WARMS_PER_LOAD + 4):
        app.main._rt_scores(f'T{i}', 2020, 'movie', 'load-rt')
    assert len(calls) == omdb.WARMS_PER_LOAD
    app.main._rt_scores('Tap', 2020, 'movie')            # a user open is never capped
    assert len(calls) == omdb.WARMS_PER_LOAD + 1
    monkeypatch.setattr(omdb, 'daily_quota', lambda: 1)
    omdb.count_call()
    app.main._rt_scores('Budget', 2020, 'movie', 'load-rt2')
    assert len(calls) == omdb.WARMS_PER_LOAD + 1         # over the warm budget: no scrape
    omdb._reset_guard()


def test_daily_budget_persists_by_utc_date(tmp_path, monkeypatch):
    monkeypatch.setattr(omdb, '_BUDGET_PATH_OVERRIDE', str(tmp_path / 'b.db'))
    monkeypatch.setattr(omdb, '_utc_today', lambda: '2026-11-01')
    omdb._reset_guard()
    for _ in range(3):
        omdb.count_call()
    omdb._reset_guard()                                  # a restart: memory gone
    assert omdb._today_calls() == 3
    monkeypatch.setattr(omdb, '_utc_today', lambda: '2026-11-02')
    assert omdb._today_calls() == 0                      # a new UTC day starts at zero
    omdb._reset_guard()


def test_pool_queue_drops_oldest_warms_never_taps():
    import threading
    gate = threading.Event()
    pool = app.main._RatingsPool(workers=1, max_queue=3)
    pool.submit(gate.wait)                               # occupies the worker
    import time
    time.sleep(0.05)
    w1 = pool.submit(lambda: 'w1', warm=True)
    t1 = pool.submit(lambda: 't1')
    w2 = pool.submit(lambda: 'w2', warm=True)
    w3 = pool.submit(lambda: 'w3', warm=True)            # full: the oldest warm (w1) goes
    t2 = pool.submit(lambda: 't2')                       # full: w2 goes, the tap gets in
    assert w1.cancelled() and w2.cancelled()
    t3 = pool.submit(lambda: 't3')                       # full: w3 goes
    t4 = pool.submit(lambda: 't4')                       # only taps queued: a tap still gets in
    assert w3.cancelled()
    gate.set()
    assert [f.result(timeout=2) for f in (t1, t2, t3, t4)] == ['t1', 't2', 't3', 't4']


def _detail_chain(monkeypatch, ratings):
    monkeypatch.setattr(app.main, 'inline_ratings', lambda *a, **k: (ratings, False, False))
    with app.main.app.test_request_context('/api/tv/1396'):
        from flask import jsonify, request
        assert request.endpoint == 'tv_details'
        resp = app.main.app.process_response(jsonify({'name': 'Breaking Bad', 'first_air_date': '2008-01-20'}))
        return resp.get_data(as_text=True)


def test_inline_ratings_run_before_the_redaction_backstop():
    funcs = [f.__name__ for f in app.main.app.after_request_funcs[None]]
    # Flask runs after_request handlers last-registered first: the ratings are added, then the backstop checks them.
    assert funcs.index('_detail_ratings_inline') > funcs.index('_redact_api_error_bodies')


def test_detail_reply_with_inline_ratings_passes_the_backstop_unchanged(monkeypatch):
    import json
    r = {'imdb': {'score': 8.0}, 'rotten_tomatoes_critic': None, 'rotten_tomatoes_audience': None, 'metacritic': None, 'tmdb': None}
    monkeypatch.setattr(app.main, 'inline_ratings', lambda *a, **k: (r, False, False))
    with app.main.app.test_request_context('/api/tv/1396'):
        from flask import jsonify
        after_inline = app.main._detail_ratings_inline(jsonify({'name': 'Breaking Bad', 'first_air_date': '2008-01-20'}))
        before = after_inline.get_data()
        out = app.main._redact_api_error_bodies(after_inline)
        assert out.get_data() == before                   # byte for byte
    assert json.loads(before)['ratings'] == r
    assert _detail_chain(monkeypatch, r).strip() == before.decode().strip()   # the whole chain too


def test_a_secret_in_inline_ratings_is_redacted_and_json_stays_valid(monkeypatch):
    import json
    body = _detail_chain(monkeypatch, {'imdb': {'score': 8.0, 'src': 'https://x/?apikey=SECRET123'}})
    assert 'SECRET123' not in body
    assert json.loads(body)['ratings']['imdb']['score'] == 8.0


def test_large_detail_replies_are_still_checked(monkeypatch):
    big = {'imdb': {'score': 8.0, 'src': '?apikey=SECRET123'}, 'pad': 'x' * (300 * 1024)}
    assert 'SECRET123' not in _detail_chain(monkeypatch, big)


def test_budget_write_happens_outside_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(omdb, '_BUDGET_PATH_OVERRIDE', str(tmp_path / 'b.db'))
    held = []
    real = omdb._budget_db
    monkeypatch.setattr(omdb, '_budget_db', lambda fn: held.append(omdb._guard_lock.locked()) or real(fn))
    omdb._reset_guard()
    omdb._today_calls()
    held.clear()
    omdb.count_call()
    assert held == [False]
    assert omdb.BUDGET_DB_TIMEOUT_S <= 0.1
    omdb._reset_guard()
