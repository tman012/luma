"""v252 Genre Roulette schedule: 10:50 PM ET by default, once per slot, a moved time gets its own run, short
nights retried up to the limit, days, next run, status (last run, failures in 7 days)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.automation import gr_scheduler as sch, gr_store as gs

ET = ZoneInfo('America/New_York')


def at(h, m, day=23):
    return datetime(2026, 10, day, h, m, tzinfo=ET)       # Fri Oct 23 2026


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setitem(gs._cfg, 'db_path', str(tmp_path / 'automation.db'))
    gs.save({'enabled': True})
    calls = []
    result = {'grabbed': 3, 'wanted': 3, 'status': 'done'}

    def run(kind, now=None):
        calls.append((kind, now))
        return dict(result)
    return calls, result, run


def test_runs_once_at_1050_pm_et(env):
    calls, _, run = env
    assert sch.tick(at(22, 49), run) is None
    assert sch.tick(at(22, 50), run)['grabbed'] == 3
    assert sch.tick(at(22, 55), run) is None and sch.tick(at(23, 59), run) is None
    assert [c[0] for c in calls] == ['scheduled']
    assert sch.tick(at(22, 50, 24), run) and len(calls) == 2         # tomorrow


def test_off_never_runs(env):
    calls, _, run = env
    gs.save({'enabled': False})
    assert sch.tick(at(23, 0), run) is None and calls == [] and sch.next_run() is None


def test_moving_the_time_after_tonights_run_gets_its_own_run(env):
    calls, _, run = env
    sch.tick(at(22, 50), run)
    gs.save({'time': '23:30'})
    assert sch.tick(at(23, 0), run) is None
    assert sch.tick(at(23, 30), run) and len(calls) == 2


def test_short_nights_retry_up_to_the_limit_and_gap(env):
    calls, result, run = env
    result.update(grabbed=1)
    sch.tick(at(22, 50), run)
    assert sch.tick(at(23, 0), run) is None                           # 20 min gap
    assert sch.next_run(now=at(23, 0)) == at(23, 10)
    for t in (at(23, 10), at(23, 30), at(23, 50)):
        assert sch.tick(t, run)
    assert sch.tick(at(23, 59), run) is None                          # 3 retries spent
    assert len(calls) == 4
    assert sch.next_run(now=at(23, 59)) == at(22, 50, 24)


def test_a_full_night_is_not_retried(env):
    calls, _, run = env
    sch.tick(at(22, 50), run)
    assert sch.tick(at(23, 20), run) is None and len(calls) == 1


def test_days_weekdays_and_weekends(env):
    calls, _, run = env
    gs.save({'days': 'weekends'})
    assert sch.tick(at(22, 50), run) is None                           # Friday
    assert sch.next_run(now=at(12, 0)) == at(22, 50, 24)               # Saturday
    gs.save({'days': 'weekdays'})
    assert sch.next_run(now=at(23, 0, 24)) == at(22, 50, 26)           # Sat night -> Monday


def test_next_run_and_sleep(env):
    assert sch.next_run(now=at(12, 0)) == at(22, 50)
    assert sch.sleep_s(now=at(22, 48)) == 121.0
    assert sch.sleep_s(now=at(12, 0)) == sch.CHECK_S


def test_status_last_run_and_failures(env):
    now = at(12, 0).timestamp()
    for i, (g, w, st, dry) in enumerate(((3, 3, 'done', False), (2, 3, 'done', False), (0, 3, 'error', False),
                                         (3, 3, 'done', True))):
        rid = gs.start_run('scheduled', dry, now - 86400 * (3 - i))
        gs.finish_run(rid, {'grabbed': g, 'wanted': w, 'status': st, 'stop_reason': 'x'}, now - 86400 * (3 - i))
    old = gs.start_run('scheduled', False, now - 86400 * 9)
    gs.finish_run(old, {'grabbed': 0, 'wanted': 3, 'status': 'error'}, now - 86400 * 9)
    s = sch.status(now=at(12, 0))
    assert s['failures_7d'] == 2 and s['enabled'] and not s['running']
    assert s['last_run']['grabbed'] == 0 and s['last_run']['status'] == 'error'   # newest real (not dry) run
    assert s['next_run'] == at(22, 50).isoformat()


def test_interrupted_runs_are_closed_at_boot(env):
    rid = gs.start_run('scheduled', False, 1.0)
    gs.interrupted(2.0)
    r = [x for x in gs.runs() if x['id'] == rid][0]
    assert r['status'] == 'error' and r['stop_reason'] == 'interrupted'


def test_starts_only_on_the_test_install(monkeypatch):
    from app import movie_scanner
    monkeypatch.setattr(movie_scanner, 'on_test', lambda: False)
    monkeypatch.setitem(sch._started, 'on', False)
    assert sch.start() is False


def test_main_starts_it_next_to_the_movie_scanner():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / 'app' / 'main.py').read_text()
    assert 'gr_scheduler.start()' in src
