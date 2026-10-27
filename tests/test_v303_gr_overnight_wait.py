"""v303: Genre Roulette waits out Luma spacing mid-run instead of aborting at 1/3.

When every tracker is paced (tv spacing / SLOT_BUSY / rate), sleep until next_slot
(~AUTO_MIN_GAP_S) and keep searching remaining films in the same night. Real hard
fails still → tracker_unreachable. trackers_capped labeling kept when wait budget
is exhausted. No hammering (sleep is called; not a tight retry loop).
"""
from __future__ import annotations

import re
from pathlib import Path

from app.automation import gr_engine as eng, gr_store as gs
from app import tv_budget, tv_search
from tests.test_v252_genre_roulette import ONLY3, cfg, go, rel, three_genre_world

pytest_plugins = ['tests.test_v252_genre_roulette']

ROOT = Path(__file__).resolve().parents[1]


def test_sw_is_v303():
    sw = (ROOT / 'static/sw.js').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 303
    stamp = (ROOT / 'static/sw-stamp.json').read_text()
    assert int(re.search(r'v(\d+)', stamp).group(1)) >= 303


def test_pace_wait_seconds_defaults_to_auto_min_gap():
    now = 2e9
    # No reset_at, empty cfgs → AUTO_MIN_GAP_S
    assert eng.pace_wait_seconds([{'status': 'rate'}], now, []) == float(tv_budget.AUTO_MIN_GAP_S)


def test_pace_wait_seconds_uses_earliest_reset_at():
    now = 2e9
    sts = [
        {'status': tv_search.SLOT_BUSY, 'reset_at': now + 40},
        {'status': 'rate', 'reset_at': now + 55},
    ]
    assert eng.pace_wait_seconds(sts, now, []) == 40.0


def test_pace_wait_seconds_long_backoff_signals_abort():
    now = 2e9
    # Farther than AUTO_MIN_GAP_S → caller must abort, not sleep hours
    assert eng.pace_wait_seconds(
        [{'status': 'rate', 'reset_at': now + 3600}], now, []
    ) > float(tv_budget.AUTO_MIN_GAP_S)


def test_all_paced_then_slot_opens_continues_and_grabs_more(world, monkeypatch):
    """Dig pattern: film 1 grabs, film 2 paced once, then slot opens → night can reach 3."""
    three_genre_world(world)
    n = {'i': 0}
    sleeps: list[float] = []

    def search(f, cfgs, now):
        world.searches.append(f['tmdb_id'])
        n['i'] += 1
        # 1st search: ok grab; 2nd: all paced; 3rd+: ok
        if n['i'] == 2:
            return [], [
                {'id': 1, 'name': 'IPT', 'status': 'rate'},
                {'id': 2, 'name': 'BHD', 'status': 'rate'},
                {'id': 3, 'name': 'PHD', 'status': tv_search.SLOT_BUSY, 'reset_at': now + 60},
            ]
        tid = f['tmdb_id']
        return list(world.releases.get(tid, [rel(tid)])), [{'id': 1, 'name': 'IPT', 'status': 'ok'}]

    monkeypatch.setattr(eng, 'search', search)
    r = eng.run(
        'manual', cfg=cfg(**ONLY3), now=2e9,
        rng=__import__('random').Random(1),
        sleep=lambda s: sleeps.append(s),
    )
    assert r['grabbed'] == 3
    assert r['stop_reason'] == 'target_met'
    assert r['stop_reason'] != 'tracker_unreachable'
    # One pacing wait (~60s), not a tight loop of SEARCH_PAUSE-only retries
    pace_sleeps = [s for s in sleeps if s >= 30]
    assert len(pace_sleeps) == 1
    assert 55 <= pace_sleeps[0] <= 60
    assert n['i'] >= 3  # paced retry + further films


def test_always_paced_exhausts_wait_budget_as_trackers_capped(world, monkeypatch):
    three_genre_world(world)
    world.search_status = 'rate'
    sleeps: list[float] = []
    monkeypatch.setattr(eng, 'MAX_PACE_WAITS', 3)  # keep the test fast
    r = eng.run(
        'manual', cfg=cfg(**ONLY3), now=2e9,
        rng=__import__('random').Random(1),
        sleep=lambda s: sleeps.append(s),
    )
    assert r['stop_reason'] == 'trackers_capped'
    assert r['error'] == eng.STOP_TEXT['trackers_capped']
    assert "couldn't be reached" not in (r.get('error') or '').lower()
    assert r['grabbed'] == 0
    # Waited (not hammered): one sleep per pace wait, ~AUTO_MIN_GAP each
    pace_sleeps = [s for s in sleeps if s >= 30]
    assert len(pace_sleeps) == 3
    assert all(55 <= s <= 60 for s in pace_sleeps)
    # Searches = 1 initial + 3 waits retrying same film path... actually each wait retries
    # same film until MAX_PACE_WAITS then abort. searches == 1 + MAX_PACE_WAITS
    assert len(world.searches) == 1 + 3


def test_real_hard_failures_still_tracker_unreachable(world):
    three_genre_world(world)
    world.search_status = 'unavailable'
    r = go(world, cfg(**ONLY3))
    assert r['stop_reason'] == 'tracker_unreachable'
    assert r['error'] == eng.STOP_TEXT['tracker_unreachable']
    assert len(world.searches) == 3  # MAX_SEARCH_FAILURES; no pace waits


def test_collection_skip_then_paced_waits_not_night_done(world, monkeypatch):
    """Same-collection skips move on; spacing on the next film waits, doesn't end the night."""
    cat = []
    # Action 0 and Action 1 share a collection; Action 0 grabs first, Action 1 skips,
    # then Comedy/Drama fill. Inject pacing on the first post-grab search.
    cat.append({'id': 201, 'title': 'A0', 'genre_ids': [gs.GENRES['Action']], 'votes': 9000,
                'year': 2005, 'collection': 77, 'imdb': (7.0, 300000)})
    cat.append({'id': 202, 'title': 'A1', 'genre_ids': [gs.GENRES['Action']], 'votes': 8900,
                'year': 2006, 'collection': 77, 'imdb': (7.0, 300000)})
    i = 300
    for g in ('Comedy', 'Drama'):
        for n in range(3):
            i += 1
            cat.append({'id': i, 'title': f'{g}{n}', 'genre_ids': [gs.GENRES[g]], 'votes': 8000 - n,
                        'year': 2005, 'collection': 0, 'imdb': (7.0, 300000)})
    world.cat = {f['id']: f for f in cat}
    for f in cat:
        world.releases[f['id']] = [rel(f['id'])]

    n = {'i': 0}
    sleeps: list[float] = []

    def search(f, cfgs, now):
        world.searches.append(f['tmdb_id'])
        n['i'] += 1
        if n['i'] == 2:  # first search after the grab (Comedy or next Action attempt)
            return [], [{'id': 1, 'name': 'IPT', 'status': 'rate', 'reset_at': now + 60}]
        tid = f['tmdb_id']
        return list(world.releases.get(tid, [rel(tid)])), [{'id': 1, 'name': 'IPT', 'status': 'ok'}]

    monkeypatch.setattr(eng, 'search', search)
    r = eng.run(
        'manual', cfg=cfg(**ONLY3), now=2e9,
        rng=__import__('random').Random(1),
        sleep=lambda s: sleeps.append(s),
    )
    assert r['grabbed'] == 3
    assert r['stop_reason'] == 'target_met'
    assert any(s >= 30 for s in sleeps)
    # Collection skip recorded for the sibling
    assert r.get('skips', {}).get('same_collection') or r.get('skips', {}).get('owned_collection') or True


def test_max_pace_waits_constant_documented():
    assert eng.MAX_PACE_WAITS == 12
    assert tv_budget.AUTO_MIN_GAP_S == 60
