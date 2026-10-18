"""TV Import speed: a 24 h answer cache in tv.db, the daily refresh that
skips it, seasons batched 20 a call, and one budget shared by all workers."""
import threading
import time

import pytest

from app import tv_tmdb
from tests import tv_import_mock as M
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401


@pytest.fixture
def mt(tv_env, monkeypatch):  # noqa: F811
    m = M.MockTmdb()
    monkeypatch.setitem(tv_tmdb._cfg, 'get_key', lambda: 'k')
    monkeypatch.setitem(tv_tmdb._cfg, 'session_factory', m.session)
    monkeypatch.setitem(tv_tmdb._cfg, 'cache', True)
    monkeypatch.setattr(tv_tmdb, 'tmdb_base_url', lambda: 'https://mock.tmdb/3')
    monkeypatch.setattr(tv_tmdb, 'RATE_PER_S', 0)
    return m


SPONGE = 800374           # 14 seasons with specials


def test_search_is_cached_for_a_day(mt, monkeypatch):
    a = tv_tmdb.search('Cheers')
    assert tv_tmdb.search('Cheers') == a and len(mt.calls) == 1
    real = time.time
    monkeypatch.setattr(tv_tmdb.time, 'time', lambda: real() + tv_tmdb.CACHE_TTL_S + 1)
    tv_tmdb.search('Cheers')
    assert len(mt.calls) == 2


def test_cache_off_by_default(mt, monkeypatch):
    monkeypatch.setitem(tv_tmdb._cfg, 'cache', False)
    tv_tmdb.search('Cheers')
    tv_tmdb.search('Cheers')
    assert len(mt.calls) == 2


def test_fresh_skips_reads_but_refreshes_the_cache(mt):
    tv_tmdb.show(SPONGE)
    with tv_tmdb.fresh():
        tv_tmdb.show(SPONGE)
    assert len(mt.calls) == 2
    tv_tmdb.show(SPONGE)
    assert len(mt.calls) == 2


def test_fresh_is_per_thread(mt):
    tv_tmdb.search('Cheers')
    seen = []
    with tv_tmdb.fresh():
        t = threading.Thread(target=lambda: seen.append(tv_tmdb.search('Cheers')))
        t.start()
        t.join()
    assert seen and len(mt.calls) == 1


def test_prune_drops_old_answers(mt, monkeypatch):
    from app import tv_store
    tv_tmdb.search('Cheers')
    real = time.time
    monkeypatch.setattr(tv_tmdb.time, 'time', lambda: real() + tv_tmdb.CACHE_TTL_S + 1)
    tv_tmdb.prune_cache()
    with tv_store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM tmdb_cache').fetchone()[0] == 0


def test_seasons_ride_twenty_a_call_and_are_cached(mt):
    nums = list(range(0, 25))
    tv_tmdb.seasons(SPONGE, nums)
    batched = [c for c in mt.calls if c[0] == f'/tv/{SPONGE}']
    assert len(batched) == 2
    sizes = [len(dict(c[1])['append_to_response'].split(',')) for c in batched]
    assert sizes == [20, 5]
    got = tv_tmdb.seasons(SPONGE, list(range(0, 14)))
    assert len(mt.calls) == 2 and sorted(got) == list(range(0, 14))


def test_one_rate_budget_for_all_workers(mt, monkeypatch):
    monkeypatch.setattr(tv_tmdb, 'RATE_PER_S', 50.0)
    monkeypatch.setattr(tv_tmdb, 'BURST', 1)
    tv_tmdb._bucket.update(tokens=1.0, ts=time.monotonic(), pause_until=0.0)
    t0 = time.monotonic()
    ts = [threading.Thread(target=lambda: [tv_tmdb.wait_turn() for _ in range(5)]) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert time.monotonic() - t0 >= (40 - 1) / 50.0 * 0.9         # 40 turns at 50/s across 8 threads


def test_a_429_pauses_every_worker(mt, monkeypatch):
    monkeypatch.setattr(tv_tmdb, 'RATE_PER_S', 1000.0)
    tv_tmdb._bucket.update(pause_until=0.0)
    tv_tmdb.note_retry_after(1.0)
    t0 = time.monotonic()
    tv_tmdb.wait_turn()
    assert time.monotonic() - t0 >= 0.9


def test_the_background_backfill_stays_well_under_the_budget(mt, monkeypatch):
    """The Calendar-open refresh (stills backfill) peaked at 38/s against the
    35/s budget: in background() it is paced at 25/s, burst included."""
    monkeypatch.setattr(tv_tmdb, 'RATE_PER_S', 35.0)
    monkeypatch.setattr(tv_tmdb, 'BURST', 4)
    tv_tmdb._bucket.update(tokens=4.0, ts=time.monotonic(), pause_until=0.0)   # a quiet spell: full burst
    tv_tmdb._bg_bucket.update(tokens=1.0, ts=time.monotonic())
    stamps, lock = [], threading.Lock()

    def worker():
        with tv_tmdb.background():
            for _ in range(8):
                tv_tmdb.wait_turn()
                with lock:
                    stamps.append(time.monotonic())
    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    stamps.sort()
    peak = max(sum(1 for s in stamps if a <= s < a + 1.0) for a in stamps)
    assert peak <= 26                                     # 25/s + 1, never the 35 + 4 burst
    assert getattr(tv_tmdb._local, 'background', False) is False


def test_the_calendar_open_refresh_runs_in_the_background_budget(monkeypatch):
    from app import tv_scan
    seen = {}
    monkeypatch.setattr(tv_scan, 'refresh_added', lambda **k: seen.update(k) or 0)
    monkeypatch.setattr(tv_scan, '_RefreshThread', type('T', (), {
        '__init__': lambda self, target, **k: setattr(self, 't', target), 'start': lambda self: self.t()}))
    from app import tv_airtimes
    monkeypatch.setattr(tv_airtimes, 'refresh', lambda **k: 0)
    assert tv_scan.refresh_on_open() is True
    assert seen.get('background') is True and seen.get('folders') is True
