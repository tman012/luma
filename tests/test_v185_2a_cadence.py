"""v185.2a (Taylor): no-runtime episodes are searched at air time; aired, missing episodes every 15 min for
6 h after air, then hourly (floor, no-backlog and caps unchanged); every feed every 15 min; never-searched
episodes scheduled late by an older build are picked up on the next pass."""
import time as _time

import pytest

from app import tv_search, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_tv_search_v158b import _auto, allow_grabs, env  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def _air(e, at, runtime, nxt=None):
    stamp = _time.strftime('%Y-%m-%dT%H:%M:%S+00:00', _time.gmtime(at))
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET airstamp = ?, air_date = ?, runtime = ?, next_search_at = ?, last_search_at = NULL,'
                     ' search_attempts = 0 WHERE id = ?', (stamp, _time.strftime('%Y-%m-%d', _time.localtime(at)), runtime, nxt, e))


def _due(e):
    return rows('SELECT next_search_at, last_search_at FROM episodes WHERE id = ?', e)[0]


def test_first_search_is_air_plus_runtime_with_no_delay_even_when_delay_set(env, monkeypatch):
    allow_grabs(monkeypatch)
    _auto(delay=4)
    t = _time.time() - 600
    e3, e4 = env.e2[3], env.e2[4]
    _air(e3, t, None)
    _air(e4, t, 43)
    tv_search.schedule(t + 60)
    assert _due(e3)['next_search_at'] == pytest.approx(t)
    assert _due(e4)['next_search_at'] == pytest.approx(t + 43 * 60)   # v196a: no 4 h delay


def test_missing_episodes_retry_every_15_min_for_6_h_then_hourly(env, monkeypatch):
    allow_grabs(monkeypatch)
    _auto(delay=0)
    t = _time.time() - 3600
    e3 = env.e2[3]
    _air(e3, t, None, nxt=t)
    env.results = []
    now = t + 60
    assert tv_search.run_due(now)['shows'] == 1
    assert _due(e3)['next_search_at'] == pytest.approx(now + 15 * 60)
    late = t + 6 * 3600 + 60
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = ? WHERE id = ?', (late - 1, e3))
    assert tv_search.run_due(late)['shows'] == 1
    assert _due(e3)['next_search_at'] == pytest.approx(late + 3600)


def test_a_never_searched_episode_scheduled_late_is_picked_up_next_pass(env, monkeypatch):
    allow_grabs(monkeypatch)
    _auto(delay=4)
    t = _time.time() - 2 * 3600
    e3 = env.e2[3]
    _air(e3, t, None, nxt=t + 4 * 3600 + 43 * 60)          # an older build's air + 4h43m
    now = t + 2 * 3600
    tv_search.schedule(now)
    assert _due(e3)['next_search_at'] == pytest.approx(t)
    env.results = []
    assert tv_search.run_due(now)['shows'] == 1 and _due(e3)['last_search_at'] == pytest.approx(now)


def test_every_feed_is_due_every_15_minutes_capped_or_not(monkeypatch):
    from app import tv_budget
    monkeypatch.setattr(tv_store, 'get_meta', lambda k: '1000')
    for capped in (True, False):
        monkeypatch.setattr(tv_budget, 'capped', lambda cfg, c=capped: c)
        monkeypatch.setattr(tv_budget, 'slot_free', lambda cfg, now: False)
        assert not tv_search.feed_due({'id': 1}, 1000 + 14 * 60)
        assert tv_search.feed_due({'id': 1}, 1000 + 15 * 60)


def test_v196a_line_of_fire_scheduled_late_with_a_runtime_is_pulled_to_air_plus_runtime(env, monkeypatch):
    allow_grabs(monkeypatch)
    _auto(delay=4)
    t = _time.time() - 3 * 3600
    e = env.e2[3]
    _air(e, t, 60, nxt=t + 4 * 3600 + 3600)     # an older build's 4 h delay
    tv_search.schedule(_time.time())
    assert _due(e)['next_search_at'] == pytest.approx(t + 3600)
