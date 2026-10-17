"""Scheduled TV rescan: every rescan_interval_h, last scan time in tv.db meta,
idle with no TV paths, unmatched folders left for a manual Scan."""
import threading

import pytest

from app import tv_scan, tv_settings, tv_store
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_library_paths import tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

H = 3600


def test_idle_without_tv_paths(tv_env, fake_tmdb):  # noqa: F811
    assert tv_scan.due(1e9) is False
    assert tv_scan.tick(1e9) is False
    assert fake_tmdb.calls == [] and tv_scan.last_scan() is None


def test_first_tick_scans_then_waits_for_the_interval(lib):
    assert tv_scan.due(10_000.0)
    assert tv_scan.tick(10_000.0) is True
    assert tv_store.get_meta('last_scan') == '10000.0' and tv_scan.last_scan() == 10_000.0
    assert len(rows('SELECT id FROM series')) == 5
    assert tv_scan.tick(10_000.0 + 6 * H - 1) is False                 # default 6 h
    assert tv_scan.tick(10_000.0 + 6 * H) is True
    assert tv_scan.last_scan() == 10_000.0 + 6 * H


def test_interval_comes_from_tv_settings(lib):
    st = tv_settings.load()
    st['rescan_interval_h'] = 24
    tv_settings.save(st)
    tv_scan.tick(0.0 + 1)
    assert not tv_scan.due(1 + 23 * H) and tv_scan.due(1 + 24 * H)


def test_a_manual_scan_resets_the_clock(lib):
    tv_scan.scan_all(now=50_000.0)
    assert not tv_scan.due(50_000.0 + 5 * H)


def test_scheduled_scans_leave_unmatched_for_a_manual_scan(lib, fake_tmdb):  # noqa: F811
    tv_scan.scan_all(now=1.0)
    fake_tmdb.calls.clear()
    assert tv_scan.tick(1.0 + 7 * H)
    assert not any(c[0] == 'search' for c in fake_tmdb.calls)
    assert len(rows('SELECT id FROM unmatched')) == 3


def test_a_running_scan_is_not_doubled(lib):
    assert tv_scan._scan_lock.acquire(blocking=False)
    try:
        assert tv_scan.tick(1e9) is False
    finally:
        tv_scan._scan_lock.release()


def test_a_tmdb_error_is_recorded_and_retried_next_tick(lib, fake_tmdb):  # noqa: F811
    from app import tv_tmdb
    fake_tmdb.fail = tv_tmdb.UNREACHABLE
    assert tv_scan.tick(1e6) is True
    assert tv_scan.status()['error'] == "Couldn't reach TMDB"
    fake_tmdb.fail = None
    assert tv_scan.due(1e6 + 60)              # the failed scan didn't count
    assert tv_scan.tick(1e6 + 60) is False    # ...but the scheduler backs off (Reviewer)
    tv_scan.tick(1e6 + 600)
    assert tv_scan.status()['error'] is None and tv_scan.last_scan() == 1e6 + 600


def test_scheduler_thread_starts_once_and_stops(monkeypatch):
    ticks = threading.Event()
    monkeypatch.setattr(tv_scan, 'TICK_S', 0.01)
    monkeypatch.setattr(tv_scan, 'tick', lambda now=None: ticks.set())
    try:
        assert tv_scan.start_scheduler() is True
        assert tv_scan.start_scheduler() is False
        assert ticks.wait(2)
        assert tv_scan._sched_thread.name == 'tv-rescan' and tv_scan._sched_thread.daemon
    finally:
        tv_scan.stop_scheduler()
        tv_scan._sched_thread.join(2)
    assert not tv_scan._sched_thread.is_alive()


def test_a_broken_settings_store_never_kills_the_thread(monkeypatch):
    monkeypatch.setattr(tv_settings, 'load', lambda: (_ for _ in ()).throw(OSError('disk')))
    assert tv_scan.tick(1e9) is False


# Tester tv1-8646479 M3: a scan that could read no path at all doesn't count.

def test_all_paths_missing_is_retried_next_tick(lib, tmp_path):
    import shutil
    moved = tmp_path / 'unmounted'
    shutil.move(str(lib), str(moved))                 # the share is gone
    assert tv_scan.tick(2e6) is True
    assert tv_scan.status()['summary']['missing'] == [PID] and tv_scan.status()['summary']['paths'] == 0
    assert tv_scan.last_scan() is None and tv_scan.due(2e6 + 300)
    shutil.move(str(moved), str(lib))                 # back again: the next tick scans
    assert tv_scan.tick(2e6 + 300) is True
    assert tv_scan.last_scan() == 2e6 + 300 and len(rows('SELECT id FROM series')) == 5


def test_one_readable_path_still_counts(lib, tmp_path):
    st = tv_settings.load()
    st['paths'].append({'id': 't_0000bbbb', 'name': 'Gone', 'path': str(tmp_path / 'nope')})
    tv_settings.save(st)
    assert tv_scan.tick(3e6) is True
    assert tv_scan.status()['summary']['missing'] == ['t_0000bbbb'] and tv_scan.last_scan() == 3e6



# Reviewer: the scheduled rescan backs off while TMDB fails.

@pytest.fixture
def fresh_backoff(monkeypatch):
    monkeypatch.setattr(tv_scan, '_backoff', {'fails': 0, 'until': 0.0})


def test_backoff_doubles_up_to_the_interval_and_resets(lib, fake_tmdb, fresh_backoff):  # noqa: F811
    from app import tv_tmdb
    assert [tv_scan.backoff_s(n) for n in (1, 2, 3, 4, 5, 6, 7, 8)] == \
        [600, 1200, 2400, 4800, 9600, 19200, 21600, 21600]              # 6 h default interval
    fake_tmdb.fail = tv_tmdb.BUSY
    t, tries = 1e7, []
    while t < 1e7 + 12 * H:
        if tv_scan.tick(t):
            tries.append(int(t - 1e7))
        t += tv_scan.TICK_S
    assert tries == [0, 600, 1800, 4200, 9000, 18600, 37800]
    fake_tmdb.fail = None
    t = 1e7 + 37800 + 21600
    assert tv_scan.tick(t) is True and tv_scan.status()['error'] is None
    assert tv_scan._backoff == {'fails': 0, 'until': 0.0}


def test_a_manual_scan_ignores_the_backoff(lib, fake_tmdb, fresh_backoff):  # noqa: F811
    from app import tv_tmdb
    fake_tmdb.fail = tv_tmdb.UNREACHABLE
    assert tv_scan.tick(2e7) is True and not tv_scan.tick(2e7 + 300)
    fake_tmdb.fail = None
    assert tv_scan.run(now=2e7 + 300) is True                           # the Scan button
    assert tv_scan.status()['error'] is None and tv_scan.tick(2e7 + 600) is False   # scanned: not due
