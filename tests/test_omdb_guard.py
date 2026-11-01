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
