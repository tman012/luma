"""Universal tracker guard: same limits as the live install, for every indexer."""

import json
import time

import pytest

import app.indexer_api as ia


class _Resp:
    status_code = 200

    def __init__(self, items):
        self._items = items

    def raise_for_status(self):
        pass

    def json(self):
        return self._items


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return _Resp([{'title': f'r{len(self.calls)}'}])


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_STATE_DIR', str(tmp_path))
    for name in ('_guard_log', '_guard_buckets', '_query_cache', '_indexer_req_log',
                 '_indexer_last_ts', '_ix_backoff_cache'):
        if hasattr(ia, name):
            monkeypatch.setattr(ia, name, {})
    monkeypatch.setattr(ia, '_guard_loaded', [False])
    monkeypatch.setattr(ia, '_RATE_LIMITED_MIN_INTERVAL', 0.0)
    monkeypatch.setattr(ia, '_RATE_LIMITED_INTERACTIVE_CAP', 10 ** 6)
    monkeypatch.setattr(ia, '_take_token', lambda ix, now: True)
    c = ia.ProwlarrClient('http://prowlarr.test', 'k')
    c.session = _Session()
    monkeypatch.setattr(c, '_backed_off_until', lambda ix: None)
    monkeypatch.setattr(c, '_indexer_name', lambda ix: f'ix{ix}')
    return c


def _q(i):
    return [('query', f'q{i}'), ('type', 'search')]


def test_limits_match_live_install():
    assert ia._GUARD_PER_DAY == 300
    assert ia._GUARD_GLOBAL_PER_HOUR == 120
    assert ia._GUARD_USER_PER_HOUR == 60
    assert ia._GUARD_BUCKET_CAPACITY == 10
    assert ia._GUARD_BUCKET_REFILL == 120.0
    assert ia._GUARD_QUERY_TTL_BG == 3 * 3600
    assert ia._GUARD_QUERY_TTL_USER == 900


def test_every_indexer_metered(client):
    assert client._is_rate_limited(12345) is True


def test_identical_query_served_from_cache(client):
    a = client._get_search(_q(1), [7])
    b = client._get_search(_q(1), [7])
    assert a == b and len(client.session.calls) == 1


def test_background_bucket_bursts_ten_then_stops(client):
    for i in range(10):
        client._get_search(_q(i), [7])
    with pytest.raises(ia.IndexerSkipped):
        client._get_search(_q(99), [7])
    assert len(client.session.calls) == 10


def test_user_searches_capped_per_hour(client):
    now = time.time()
    ia._guard_log[7] = [now - 10] * 60
    with pytest.raises(ia.IndexerSkipped):
        client._get_search(_q(1), [7], interactive=True)
    assert client.session.calls == []
    client._get_search(_q(1), [8], interactive=True)   # other indexers unaffected
    assert len(client.session.calls) == 1


def test_daily_cap_per_indexer(client):
    ia._guard_log[7] = [time.time() - 7200 - i for i in range(300)]
    with pytest.raises(ia.IndexerSkipped):
        client._get_search(_q(1), [7], interactive=True)
    assert client.session.calls == []


def test_global_hourly_cap(client):
    now = time.time()
    for ix in range(12):
        ia._guard_log[100 + ix] = [now - 5] * 10
    with pytest.raises(ia.IndexerSkipped):
        client._get_search(_q(1), [7], interactive=True)


def test_over_cap_returns_stale_cached_answer(client):
    first = client._get_search(_q(1), [7], interactive=True)
    ia._query_cache[(7, tuple(_q(1)))] = (time.time() - 3600, first)  # older than the user TTL
    ia._guard_log[7] = [time.time()] * 300
    assert client._get_search(_q(1), [7], interactive=True) == first
    assert len(client.session.calls) == 1


def test_unscoped_search_counted_under_minus_one(client):
    client._get_search(_q(1), None, interactive=True)
    assert len(ia._guard_log[-1]) == 1
    ia._guard_log[-1] = [time.time()] * 300
    with pytest.raises(ia.IndexerSkipped):
        client._get_search(_q(2), None, interactive=True)


def test_counters_persist_in_data_dir(client, tmp_path):
    client._get_search(_q(1), [7], interactive=True)
    saved = json.loads((tmp_path / ia.GUARD_FILENAME).read_text())
    assert len(saved['log']['7']) == 1
    ia._guard_log.clear()
    ia._guard_loaded[0] = False
    ia._guard_load()
    assert len(ia._guard_log[7]) == 1


def test_old_entries_dropped_on_load(tmp_path, monkeypatch):
    monkeypatch.setenv('LUMA_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(ia, '_guard_log', {})
    monkeypatch.setattr(ia, '_guard_loaded', [False])
    (tmp_path / ia.GUARD_FILENAME).write_text(json.dumps(
        {'log': {'7': [time.time() - 90000, time.time() - 10]}}))
    ia._guard_load()
    assert len(ia._guard_log[7]) == 1


def test_upgrade_batch_pauses_on_guard(tmp_path, monkeypatch):
    import sqlite3
    from app import upgrade_finder
    uf = upgrade_finder.UpgradeFinder(None, {}, cache_dir=str(tmp_path))
    with sqlite3.connect(str(uf.db_path)) as c:
        for i, t in enumerate(['A', 'B', 'C']):
            c.execute('INSERT INTO library_movies (tmdb_id, section_id, title, year, current_tier) '
                      'VALUES (?, 1, ?, 2000, 1)', (str(i + 1), t))

    class Fake:
        n = 0

        def _search_single(self, *a, **k):
            Fake.n += 1
            if Fake.n == 2:
                raise ia.IndexerSkipped('guard')
            return []

        def _parse_items(self, raw):
            return []

    monkeypatch.setattr(ia, 'get_indexer_client', lambda s: Fake())
    uf._do_batch(10)
    with sqlite3.connect(str(uf.db_path)) as c:
        searched = {r[0] for r in c.execute('SELECT tmdb_id FROM upgrade_results')}
    assert searched == {'1'}   # B and C stay unsearched for the next run


def test_nightly_and_sweep_caps():
    import inspect
    from app import upgrade_scheduler
    from app.explore_cache import ExploreCache
    assert 'max_batches = 2 ' in inspect.getsource(upgrade_scheduler)
    assert ExploreCache.__init__ and '6 * 3600' in inspect.getsource(ExploreCache.__init__)
