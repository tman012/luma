"""Taylor: no daily search cap. The v251 per-tracker cap (automation.daily_search_cap, 50/day ET), its 60%
backlog share and its reserve/refund accounting are gone: background TV searches (early phase and backlog) and
Genre roulette's 'automation' searches are paced only by the tracker guard (60 s gap, hourly limits, 429 backoff).
Every tracker and the search engine are mocked; tv_search.query_trackers itself is real."""
import datetime as dt

import pytest

from zoneinfo import ZoneInfo

from app import tracker_search, tv_budget, tv_search, tv_settings, tv_store
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401
from tests.test_v233_tv_audit import _real_query_trackers

ET = ZoneInfo('America/New_York')
IPT = {'id': 7, 'name': 'IPTorrents', 'type': 'iptorrents', 'priority': 1, 'categories': [5000, 5030, 5040]}
TL = {'id': 8, 'name': 'MoreThanTV', 'type': 'morethantv', 'priority': 2, 'categories': [5000, 5030, 5040]}


def at(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=ET).timestamp()


class Engine:
    """Stands in for tracker_search.get_search(): records what each tracker was sent."""
    def __init__(self, hits=None):
        self.sent, self.hits = [], hits or {}

    def search_one(self, cfg, query, cats, limit, interactive, purpose=None):
        self.sent.append((cfg['name'], query, purpose))
        return [{'title': t} for t in self.hits.get(query, [])]

    def to_item(self, cfg, r, sv):
        return item(r['title'], tracker=cfg['id'])

    class tokens:                                      # noqa: N801
        @staticmethod
        def flush():
            pass

    class guard:                                       # noqa: N801
        @staticmethod
        def note_purpose(*a, **k):
            pass


@pytest.fixture
def eng(env, monkeypatch):
    e = Engine()
    monkeypatch.setattr(tv_search, 'query_trackers', _real_query_trackers)
    monkeypatch.setattr(tracker_search, 'get_search', lambda: e)
    monkeypatch.setattr(tv_search, 'trackers', lambda: [IPT, TL])
    return e


def _q(q, now, purpose='episode', interactive=False, cfgs=None):
    return tv_search.query_trackers(q, purpose, None, interactive, now, cfgs or [IPT, TL])


def _meta_keys():
    with tv_store.connect() as conn:
        return [r[0] for r in conn.execute('SELECT key FROM meta')]


def test_there_is_no_daily_search_cap_setting_or_accounting(env):
    assert 'daily_search_cap' not in tv_settings.DEFAULT_AUTOMATION
    assert 'daily_search_cap' not in tv_settings.load()['automation']
    assert tv_settings.apply_put(tv_settings.load(), {'automation': {'daily_search_cap': 50}}) == {'automation': 'Invalid'}
    for gone in ('DAILY_SEARCH_CAP', 'search_cap', 'take_search', 'refund_search', 'count_search', 'searches_today',
                 'under_search_cap', 'search_cap_reset', 'backlog_cap', 'BACKLOG_SHARE', '_bump',
                 'take_backlog_grab', 'refund_backlog_grab', 'backlog_grab_cap'):
        assert not hasattr(tv_budget, gone), gone
    assert tv_budget.GUARD_LIMITS == {'min_gap_s': 60}                   # the pacing from before today stays


def test_no_daily_cap_blocks_tv_backlog_or_early_phase_searches(eng):
    now = at(2026, 10, 23, 10)
    for i in range(120):                                                  # far past the old 50 (and its 30 backlog)
        _, st = _q(f'Old S01E{i:03d}', now + i, purpose=tv_search.BACKLOG)
        assert all(s['status'] == 'ok' for s in st), st
    for i in range(60):
        _, st = _q(f'New S01E{i:02d}', now + 200 + i)
        assert all(s['status'] == 'ok' for s in st), st
    assert len(eng.sent) == 2 * 180
    assert all(p == 'tv' for _, _, p in eng.sent)                         # still the guard's TV purpose (its pacing)
    assert not [k for k in _meta_keys() if k.startswith(('tv_searchday:', 'tv_capskip:'))]


def test_no_daily_cap_blocks_genre_roulette_searches(eng):
    from app import movie_scanner as sc
    from app.automation import gr_engine
    mov = {'id': 9, 'name': 'MoreThanTV', 'type': 'morethantv', 'priority': 1, 'categories': [2000, 2040, 5000, 5030]}
    now = at(2026, 10, 23, 10)
    for i in range(120):
        _, st = gr_engine.search({'title': f'Heat {i}', 'year': 1995}, [mov], now + i)
        assert [s['status'] for s in st] == ['ok']
    assert len(eng.sent) == 120
    with tv_store.connect() as conn:
        got = {tuple(r) for r in conn.execute('SELECT section, purpose, outcome FROM search_log')}
    assert got == {(sc.SECTION, 'backlog', 'ok')}                         # the label the CHECK allows


def test_no_grab_limit_tv_grab_has_no_cap_gate():
    from app import tv_grab
    assert not hasattr(tv_grab, 'BACKLOG_CAPPED') and not hasattr(tv_grab, 'backlog')


def test_a_guard_skip_is_still_reported_as_skipped(eng, monkeypatch):
    from app.tracker_search import TrackerSkipped

    def skip(*a, **k):
        raise TrackerSkipped('busy')
    monkeypatch.setattr(eng, 'search_one', skip)
    _q('D S01E01', at(2026, 10, 23, 10))
    assert [r['outcome'] for r in rows('SELECT outcome FROM search_log WHERE query = ?', 'D S01E01')] == ['skipped'] * 2


def test_manual_background_logs_against_the_real_schema(eng):
    """The real tv.db schema (tv_store DDL, no migration touches search_log) has the purpose CHECK; a monitor
    click's background lane is logged as 'manual' instead of failing the INSERT."""
    with tv_store.connect() as conn:
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'search_log'").fetchone()[0]
    assert "purpose IN ('feed', 'episode', 'season', 'backlog', 'manual')" in ddl
    items, st = _q('E S01E01', at(2026, 10, 23, 10), purpose=tv_search.MANUAL_BACKGROUND)
    assert all(s['status'] == 'ok' for s in st)
    got = rows('SELECT purpose, query FROM search_log WHERE query = ?', 'E S01E01')
    assert [r['purpose'] for r in got] == ['manual', 'manual']
