"""Genre Roulette spacing waits use the run's moving clock, not its start time.

Nov 25-26 2026: the guard's movie spacing status carries reset_at = last search + 61 s (absolute).
The run measured it against the time the run STARTED (before Plex, candidates and IMDb lookups,
~35 s), saw ~95 s > the 60 s gap, and aborted every night as trackers_capped at 0/3.
"""
from __future__ import annotations

import random

from app.automation import gr_engine as eng
from app import tv_search
from tests.test_v252_genre_roulette import ONLY3, cfg, rel, three_genre_world

pytest_plugins = ['tests.test_v252_genre_roulette']

START = 2e9
PREP_S = 35.0        # Plex index + candidates + IMDb lookups before the first search


def test_spacing_reset_is_measured_from_the_running_clock_not_the_run_start(world, monkeypatch):
    three_genre_world(world)
    slept: list[float] = []
    calls = {'clock': 0, 'search': 0}

    def clock():
        calls['clock'] += 1
        return 0.0 if calls['clock'] == 1 else PREP_S + sum(slept)

    def search(f, cfgs, now):
        calls['search'] += 1
        tid = f['tmdb_id']
        real_now = START + PREP_S + sum(slept)
        if calls['search'] == 2:          # the guard: the last search was one second ago
            return [], [{'id': 1, 'name': 'IPT', 'status': tv_search.SLOT_BUSY, 'reset_at': real_now - 1 + 61.0}]
        return list(world.releases.get(tid, [rel(tid)])), [{'id': 1, 'name': 'IPT', 'status': 'ok'}]

    monkeypatch.setattr(eng, 'search', search)
    r = eng.run('manual', cfg=cfg(**ONLY3), now=START, rng=random.Random(1),
                sleep=lambda s: slept.append(s), clock=clock)
    assert r['stop_reason'] == 'target_met', r
    assert r['grabbed'] == 3
    paced = [s for s in slept if s >= 30]
    assert len(paced) == 1 and 55 <= paced[0] <= 61


def test_gap_plus_one_second_is_not_a_long_backoff(world, monkeypatch):
    three_genre_world(world)
    slept: list[float] = []
    calls = {'search': 0}

    def search(f, cfgs, now):
        calls['search'] += 1
        tid = f['tmdb_id']
        if calls['search'] == 2:
            return [], [{'id': 1, 'name': 'IPT', 'status': tv_search.SLOT_BUSY, 'reset_at': now + 61.0}]
        return list(world.releases.get(tid, [rel(tid)])), [{'id': 1, 'name': 'IPT', 'status': 'ok'}]

    monkeypatch.setattr(eng, 'search', search)
    r = eng.run('manual', cfg=cfg(**ONLY3), now=START, rng=random.Random(1),
                sleep=lambda s: slept.append(s))
    assert r['stop_reason'] == 'target_met', r
