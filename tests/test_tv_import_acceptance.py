"""TV Import acceptance on Taylor's 687 folders (synthesized TMDB mock, see
tests/fixtures/tv_import/build_fixture.py): the real scan, end to end."""
import time

import pytest

from app import tv_scan, tv_tmdb
from tests import tv_import_acceptance as A
from tests import tv_import_mock as M
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401


@pytest.fixture
def fresh(tv_env):  # noqa: F811
    tv_scan._state.update(running=False, error=None, summary=None)
    tv_scan._backoff.update(fails=0, until=0.0)
    tv_tmdb.clear_cache()
    yield tv_env


def test_687_folders(fresh):
    root = fresh / 'TV'
    A.build_tree(str(root))
    out = A.run(str(root))
    assert out['error'] is None
    ev = A.evaluate()
    assert ev['folders'] == 687
    # zero wrong auto-picks, every pick checked as the matcher made it (before duplicate resolution can
    # hide one: The Staircase), and no accept-list folder auto-picked at all
    assert ev['wrong'] == [] and ev['ambiguous_auto'] == []
    # PM: at least 96.5% of decidable folders import automatically (measured 97.29%, 646 of 664; raw 94.32%).
    # Popularity never decides: a same-name rival auto-picks only when the files fit it and rule out every rival.
    assert ev['decidable_pct'] >= 96.5, ev['decidable_pct']
    assert ev['auto_pct'] >= 94.0, ev['auto_pct']
    assert ev['same_name']['Cheers'] == {'auto': 141, 'year': 1982}
    assert ev['same_name']['The Office (US)'] == {'auto': 2316, 'year': 2005}
    assert ev['same_name']['Love'] == {'review': 'likely', 'pick': 9000001}     # 2016, never #Love (2026)
    assert ev['same_name']['Cheat (2019)']['review'] in ('likely', 'none')
    for name, left in ev['leftovers'].items():
        assert left['pct'] < 1.0, name
        assert left['group'], name                                    # numbered by the TVDB-order group
    assert sum(ev['tiers'].values()) == 687
    p = tv_scan.progress()
    assert (p['done'], p['total']) == (687, 687) and p['matched'] == ev['auto']


def test_rescan_is_cheap_and_stable(fresh):
    root = fresh / 'TV'
    A.build_tree(str(root))
    A.run(str(root))
    before = A.evaluate()
    m = M.MockTmdb()
    again = A.run(str(root))                     # known shows: a 24h cache, no new searches
    assert again['calls_by_kind'].get('search', 0) == 0
    assert A.evaluate()['tiers'] == before['tiers']
    del m


def test_rate_cap_holds_with_eight_workers(fresh):
    """40 folders through the real limiter at 35 req/s against a server that
    allows 40/s: nothing is throttled and the pace stays under the cap."""
    root = fresh / 'TV'
    A.build_tree(str(root))
    keep = set(M.load()['folders'][:40])
    for p in root.iterdir():
        if p.name not in keep:
            __import__('shutil').rmtree(p)
    t0 = time.monotonic()
    out = A.run(str(root), rate=tv_tmdb.RATE_PER_S, latency=0.02, server_rate=40)
    took = time.monotonic() - t0
    assert out['error'] is None and out['throttled_429'] == 0
    assert out['tmdb_calls'] / took <= tv_tmdb.RATE_PER_S + tv_tmdb.BURST
    assert tv_scan.WORKERS == 8
