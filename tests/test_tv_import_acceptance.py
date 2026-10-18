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
    # v157 (Taylor via PM): Luma imports its ranked pick for every folder with a TMDB result. Review keeps only
    # no TMDB result (4), duplicates (another folder has the show: 6) and 'unclear' (a true tie, or a partial
    # title the files don't back: 3). Accept-list folders may now auto-pick; they're reported, not failed.
    from collections import Counter
    assert ev['wrong'] == [], ev['wrong']
    print('\nambiguous auto-picks (reported):', ev['ambiguous_auto'])
    assert all(tid for _, tid in ev['ambiguous_auto'])
    assert Counter(why for _, why in ev['unchecked']) == {'none': 4, 'duplicate': 6, 'unclear': 3}, ev['unchecked']
    assert ev['tiers'] == {'high': 642, 'auto': 32, 'likely': 3, 'duplicate': 6, 'none': 4}, ev['tiers']
    assert ev['prechecked_wrong'] == []
    assert ev['decidable_pct'] >= 99.0, ev['decidable_pct']
    assert ev['same_name']['Cheers'] == {'auto': 141, 'year': 1982}
    assert ev['same_name']['The Office (US)'] == {'auto': 2316, 'year': 2005}
    assert ev['same_name']['Love'] == {'auto': 9000001, 'year': 2016}            # 2016, never The Love Boat or #Love
    assert ev['same_name']['Cheat (2019)'] == {'auto': 87356, 'year': 2019}
    with tv_scan.tv_store.connect() as conn:                                      # WWE Raw -> Raw, never Raw Classics
        assert conn.execute("SELECT tmdb_id FROM series WHERE folder = 'WWE Raw'").fetchone()['tmdb_id'] == 4656
    for name, left in ev['leftovers'].items():
        assert left['pct'] < 1.0, name
        assert left['group'], name                                    # numbered by the TVDB-order group
    assert sum(ev['tiers'].values()) == 687
    p = tv_scan.progress()
    assert (p['done'], p['total']) == (687, 687) and p['matched'] == ev['tiers']['high'] + ev['tiers']['auto']


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


def test_687_old_review_rows_import_at_boot(fresh, monkeypatch):
    """Taylor's rows left in review by the old rule: the boot re-run imports the ones with a ranked pick."""
    root = fresh / 'TV'
    A.build_tree(str(root))
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', False)
    A.run(str(root))
    old = A.evaluate()
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', True)
    from unittest import mock
    m = M.MockTmdb()
    with mock.patch.dict(tv_tmdb._cfg, {'get_key': lambda: 'k', 'session_factory': m.session, 'cache': True}), \
            mock.patch.object(tv_tmdb, 'tmdb_base_url', lambda: 'https://mock.tmdb/3'), \
            mock.patch.object(tv_tmdb, 'RATE_PER_S', 0.0):
        out = tv_scan.auto_import_pending()
    ev = A.evaluate()
    print('\nboot re-run:', len(out['imported']), 'imported,', out['kept'], 'kept,', len(out['duplicates']), 'now duplicates;', 'tiers before', dict(old['tiers']),
          'after', dict(ev['tiers']))
    assert ev['wrong'] == [] and out['failed'] == []
    assert ev['same_name']['Love'] == {'auto': 9000001, 'year': 2016}
    from collections import Counter
    assert set(Counter(why for _, why in ev['unchecked'])) <= {'none', 'duplicate', 'unclear'}
