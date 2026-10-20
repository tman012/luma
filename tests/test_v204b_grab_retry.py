"""v204b (PM, Taylor): no daily ceiling on automatic TV grabs; a grab held back by the guard's burst
bucket / 120/h total / backoff is stored as pending and retried once capacity is back, never dropped."""
import pytest

from app import grab_retry, tracker_guard, tracker_sources, tv_safety, tv_store
from app.tracker_guard import TrackerGuard
from tests.test_v177_tv_safety import NOW, _fill, adds, air, grab  # noqa: F401
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_tv_search_v158b import _auto, allow_grabs, env, item  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def test_no_daily_ceiling_more_than_30_automatic_grabs_a_day(env, monkeypatch):  # noqa: F811
    allow_grabs(monkeypatch)
    assert not hasattr(tv_safety, 'DAILY_CAP')
    _fill(50)                                                  # 50 automatic grabs in the last hour
    air(env.e2[3], NOW - 3600)
    assert grab(env, 3).get('ok') and len(adds(env)) == 1


def test_burst_refill_time_from_the_guard(tmp_path):
    g = TrackerGuard(tmp_path / 'g.json')
    for _ in range(tracker_guard.LIMITS['burst']):
        assert g.take('site:x', False, False, now=NOW, spacing=False) is None
    assert g.take('site:x', False, False, now=NOW, spacing=False) == 'rate limit (bucket empty)'
    at = g.ready_at('site:x', NOW)
    assert NOW + tracker_guard.LIMITS['refill_s'] <= at <= NOW + tracker_guard.LIMITS['refill_s'] + 2
    assert g.take('site:x', False, False, now=at, spacing=False) is None
    g.failure('site:y', 'rate', retry_after=900, now=NOW, status=429)
    assert g.ready_at('site:y', NOW) >= NOW + 900


def test_a_burst_blocked_grab_is_pending_and_retried_after_the_refill(env, monkeypatch):  # noqa: F811
    allow_grabs(monkeypatch)
    air(env.e2[3], NOW - 3600)
    due = NOW + 180

    import inspect
    from app import tv_grab
    from tests.test_tv_search_v158b import T
    real = inspect.getclosurevars(tv_grab.grab).nonlocals['real']      # the env's unpatched grab
    calls = []

    def fetch(url):                                    # the first fetch is held by the burst bucket
        calls.append(url)
        if len(calls) == 1:
            e = tracker_sources.GrabError('This tracker is rate limited right now. Try again later.', 429)
            e.retry_at = due
            raise e
        return 'torrent', T
    monkeypatch.setattr(tv_grab, 'grab', lambda it, sid, season, ids, origin, now=None, **k:
                        real(it, sid, season, ids, origin, now, session_factory=env.q.factory, fetch=fetch))
    g = tv_grab.grab(item('Severance.S02E03.1080p.WEB-DL-B'), env.sid, 2, [env.e2[3]], 'auto', NOW)
    assert g['ok'] is False and g['pending'] is True and adds(env) == []
    assert [p['due_at'] for p in grab_retry.pending('tv')] == [due]
    assert grab_retry.run_due(due - 1, 'tv') == {'retried': 0, 'grabbed': 0}     # not before the refill
    assert grab_retry.run_due(due, 'tv') == {'retried': 1, 'grabbed': 1}
    assert len(adds(env)) == 1 and grab_retry.pending('tv') == []

