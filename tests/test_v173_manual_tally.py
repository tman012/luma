"""v173 (PM ruling, Tester FF-6): a manual grab counts toward the tracker's daily automation tally
(8/day) so automation backs off; manual grabs are never blocked by that cap, while the rate limits
and backoffs still apply. Fake trackers, a fake clock."""
import time as _real_time
import types

import pytest

from app import tracker_guard, tracker_sources, tv_budget
from app.tracker_guard import guard_key
from tests.test_tracker_sources import R, IPT_HTML, _search, env, ipt, signed_in, torrent_bytes  # noqa: F401


@pytest.fixture
def clock(monkeypatch):
    c = {'t': 1_800_000_000.0}
    fake = types.SimpleNamespace(time=lambda: c['t'], sleep=lambda s: None, monotonic=_real_time.monotonic)
    monkeypatch.setattr(tracker_guard, 'time', fake)
    monkeypatch.setattr(tv_budget, 'time', fake)
    return c


def _setup(signed_in, env):  # noqa: F811
    rec = ipt(env['store'])
    env['mock'].set('ipt.example', R(body=IPT_HTML), *[R(body=torrent_bytes()) for _ in range(12)])
    rel = _search(signed_in)[0]
    tok = tracker_sources.release_token(rel['download_url'])
    cfg = env['store'].config(env['store'].get(rec['id']))
    return tok, cfg


@pytest.mark.skip(reason='v204a (PM, Taylor): Luma daily per-tracker cap removed; see test_v204a_no_daily_cap')
def test_five_manual_grabs_leave_automation_three(signed_in, env, clock):  # noqa: F811
    tok, cfg = _setup(signed_in, env)
    assert tv_budget.capped(cfg) and tv_budget.AUTO_PER_TRACKER_DAILY == 8
    for _ in range(5):
        kind, _ = tracker_sources.fetch_release(tok)          # manual
        assert kind == 'torrent'
        clock['t'] += 60
    assert tv_budget.usage(cfg, now=clock['t'])['used'] == 5
    allowed = 0
    for _ in range(20):                                       # automation through the rest of the day
        clock['t'] += 3600
        if tv_budget.take(cfg, now=clock['t']) is None:
            allowed += 1
    assert allowed == 3                                       # 8 - 5
    assert tv_budget.usage(cfg, now=clock['t'])['used'] == 8
    assert not tv_budget.slot_free(cfg, now=clock['t'])

    for _ in range(2):                                        # manual grabs past the cap still go
        clock['t'] += 60
        kind, _ = tracker_sources.fetch_release(tok)
        assert kind == 'torrent'
    assert tv_budget.usage(cfg, now=clock['t'])['used'] == 10


@pytest.mark.skip(reason='v204a (PM, Taylor): Luma daily per-tracker cap removed; see test_v204a_no_daily_cap')
def test_a_new_day_resets_the_tally(signed_in, env, clock):  # noqa: F811
    tok, cfg = _setup(signed_in, env)
    for _ in range(8):
        tracker_sources.fetch_release(tok)
        clock['t'] += 60
    clock['t'] += 3600
    assert tv_budget.take(cfg, now=clock['t']) is not None            # automation: none left today
    clock['t'] += 86400
    assert tv_budget.take(cfg, now=clock['t']) is None


def test_manual_grabs_still_obey_backoff_and_rate_limits(signed_in, env, clock):  # noqa: F811
    tok, cfg = _setup(signed_in, env)
    gk = guard_key(cfg)
    env['guard'].cooldown[gk] = [clock['t'] + 600, 'rate']           # the tracker said slow down
    with pytest.raises(tracker_sources.GrabError) as e:
        tracker_sources.fetch_release(tok)
    assert e.value.status == 429
    assert tv_budget.usage(cfg, now=clock['t'])['used'] == 0          # a refused grab isn't counted


def test_a_redirected_manual_grab_counts_once(signed_in, env, clock):  # noqa: F811
    rec = ipt(env['store'])
    env['mock'].set('ipt.example', R(body=IPT_HTML), R(302, headers={'Location': 'https://ipt.example/f/2'}),
                    R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    tok = tracker_sources.release_token(rel['download_url'])
    cfg = env['store'].config(env['store'].get(rec['id']))
    tracker_sources.fetch_release(tok)
    assert tv_budget.usage(cfg, now=clock['t'])['used'] == 1
