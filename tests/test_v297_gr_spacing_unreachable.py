"""v297: Genre Roulette must not label indexer spacing / rate pacing as tracker_unreachable.

False alarm: "Trackers couldn't be reached" when TV purpose spacing (AUTO_MIN_GAP_S)
or rate backoff aborted the rest of a short night. Pacing → trackers_capped;
real hard search failures still → tracker_unreachable.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.automation import gr_engine as eng, gr_store as gs
from app import tv_search
from app.tracker_search import TrackerSkipped, search_status
from tests.test_v252_genre_roulette import ONLY3, cfg, go, rel, three_genre_world

pytest_plugins = ['tests.test_v252_genre_roulette']

ROOT = Path(__file__).resolve().parents[1]


def test_sw_is_v297():
    sw = (ROOT / 'static/sw.js').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 297
    stamp = (ROOT / 'static/sw-stamp.json').read_text()
    assert 'media-kiosk-v' in stamp and int(__import__('re').search(r'v(\d+)', stamp).group(1)) >= 297


def test_search_status_maps_tv_spacing_to_rate():
    assert search_status(TrackerSkipped('tv spacing')) == 'rate'
    assert search_status(TrackerSkipped('feed spacing')) == 'rate'


def test_spacing_aborts_as_trackers_capped_not_unreachable(world, monkeypatch):
    three_genre_world(world)
    world.search_status = 'rate'  # purpose spacing / rate backoff after search_status()
    # v303: waits then still ends as trackers_capped (never unreachable)
    monkeypatch.setattr(eng, 'MAX_PACE_WAITS', 2)
    r = eng.run(
        'manual', cfg=cfg(**ONLY3), now=2e9,
        rng=__import__('random').Random(1),
        sleep=lambda s: None,
    )
    assert r['stop_reason'] == 'trackers_capped'
    assert r['error'] == eng.STOP_TEXT['trackers_capped']
    assert "couldn't be reached" not in (r.get('error') or '').lower()
    assert r['grabbed'] == 0
    assert len(world.searches) == 1 + 2  # initial + retries after waits


def test_slot_busy_still_trackers_capped(world, monkeypatch):
    three_genre_world(world)
    world.search_status = tv_search.SLOT_BUSY
    monkeypatch.setattr(eng, 'MAX_PACE_WAITS', 2)
    r = eng.run(
        'manual', cfg=cfg(**ONLY3), now=2e9,
        rng=__import__('random').Random(1),
        sleep=lambda s: None,
    )
    assert r['stop_reason'] == 'trackers_capped'
    assert r['error'] == eng.STOP_TEXT['trackers_capped']


def test_real_hard_failures_still_tracker_unreachable(world):
    three_genre_world(world)
    world.search_status = 'unavailable'
    r = go(world, cfg(**ONLY3))
    assert r['stop_reason'] == 'tracker_unreachable'
    assert r['error'] == eng.STOP_TEXT['tracker_unreachable']
    assert len(world.searches) == 3


def test_short_night_spacing_after_one_grab_is_not_unreachable(world, monkeypatch):
    """v297 label + v303 wait: after 1 grab, persistent spacing still ends trackers_capped (never unreachable)."""
    three_genre_world(world)
    n = {'i': 0}

    def search(f, cfgs, now):
        world.searches.append(f['tmdb_id'])
        n['i'] += 1
        if n['i'] == 1:
            tid = f['tmdb_id']
            return list(world.releases.get(tid, [rel(tid)])), [{'id': 1, 'name': 'IPT', 'status': 'ok'}]
        return [], [
            {'id': 1, 'name': 'IPT', 'status': 'rate'},
            {'id': 2, 'name': 'BHD', 'status': 'rate'},
            {'id': 3, 'name': 'PHD', 'status': tv_search.SLOT_BUSY},
        ]

    monkeypatch.setattr(eng, 'search', search)
    monkeypatch.setattr(eng, 'MAX_PACE_WAITS', 2)
    r = eng.run(
        'manual', cfg=cfg(**ONLY3), now=2e9,
        rng=__import__('random').Random(1),
        sleep=lambda s: None,
    )
    assert r['grabbed'] == 1
    assert r['wanted'] == 3
    assert r['stop_reason'] == 'trackers_capped'
    assert not r.get('error')  # picks non-empty: engine leaves error unset
    assert r['stop_reason'] != 'tracker_unreachable'
    assert gs.runs()[0]['stop_reason'] == 'trackers_capped'
