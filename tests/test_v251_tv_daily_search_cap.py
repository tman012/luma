"""v251: a per-tracker daily cap on background TV searches (default 50, local day ET). Every tracker,
the search engine and qBittorrent are mocked; tv_search.query_trackers itself is real."""
import datetime as dt
import logging

import pytest

from app import tracker_search, tv_budget, tv_search, tv_settings, tv_store
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401
from tests.test_v233_tv_audit import _real_query_trackers, make_show

ET = tv_budget.ET
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


def _cap(n):
    d = tv_settings.load()
    d['automation']['daily_search_cap'] = n
    tv_settings.save(d)


def _q(q, now, purpose='episode', interactive=False):
    return tv_search.query_trackers(q, purpose, None, interactive, now, [IPT, TL])


def test_default_is_50_and_stored_with_the_tv_search_settings(env):
    assert tv_settings.DEFAULT_AUTOMATION['daily_search_cap'] == 50
    assert tv_settings.load()['automation']['daily_search_cap'] == 50
    d = tv_settings.load()
    assert tv_settings.apply_put(d, {'automation': {'daily_search_cap': 12}}) == {}
    tv_settings.save(d)
    assert tv_budget.search_cap() == 12
    for bad in (0, -1, 'x', True, 10001):
        assert tv_settings.apply_put(tv_settings.load(), {'automation': {'daily_search_cap': bad}}) == {
            'automation.daily_search_cap': 'Invalid'}


def test_at_the_cap_that_tracker_is_skipped_and_the_others_continue(eng, caplog):
    _cap(3)
    now = at(2026, 10, 23, 10)
    tv_budget.count_search(TL, now - 60)          # TL already sent one today: it hits the cap first
    tv_budget.count_search(TL, now - 30)
    caplog.set_level(logging.INFO, logger='app.tv_budget')
    for i in range(5):
        _, st = _q(f'Show S01E0{i + 1}', now + i)
    by = {}
    for name, q, _ in eng.sent:
        by.setdefault(name, []).append(q)
    assert len(by['IPTorrents']) == 3 and len(by['MoreThanTV']) == 1
    assert {s['name']: s['status'] for s in st} == {'IPTorrents': tv_search.SLOT_BUSY, 'MoreThanTV': tv_search.SLOT_BUSY}
    assert st[0]['reset_at'] == at(2026, 10, 24)                         # midnight ET
    hits = [r for r in caplog.records if 'reached its daily cap' in r.getMessage()]
    assert sorted(r.getMessage().split(' reached')[0] for r in hits) == ['TV search: IPTorrents', 'TV search: MoreThanTV']
    _q('Show S01E09', now + 99)
    assert len([r for r in caplog.records if 'reached its daily cap' in r.getMessage()]) == 2   # once each


def test_a_capped_tracker_does_not_stop_a_search_finding_on_another(eng):
    _cap(1)
    now = at(2026, 10, 23, 10)
    tv_budget.count_search(IPT, now)
    eng.hits = {'Show S01E01': ['Show.S01E01.1080p.WEB.h264-GRP']}
    items, st = _q('Show S01E01', now + 1)
    assert [n for n, _, _ in eng.sent] == ['MoreThanTV'] and len(items) == 1
    assert {s['name']: s['status'] for s in st} == {'IPTorrents': tv_search.SLOT_BUSY, 'MoreThanTV': 'ok'}


def test_title_and_alias_fallbacks_count(eng, env):
    _cap(50)
    sid, ids = make_show(env, 'Ghosts (US)', 'standard', 5, (3,), '2026-10-21', '2026-10-22T00:00:00Z', 30)
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET aliases = ? WHERE id = ?', ('["Ghosts: Américain"]', sid))
    now = at(2026, 10, 21, 21)
    out = tv_search.search_show(sid, list(ids.values()), origin='auto', now=now, do_grab=False)
    assert out['searched'] == 3                                          # clean, stripped, alias
    assert tv_budget.searches_today(IPT, now) == 3 and tv_budget.searches_today(TL, now) == 3


def test_hourly_catchup_counts(eng, env):
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3,), '2026-10-21', '2026-10-22T00:00:00Z', 30)
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET search_attempts = 5 WHERE series_id = ?', (sid,))
    now = at(2026, 10, 21, 20) + tv_search.FAST_WINDOW_S + 3600         # past the fast window (backlog cadence)
    tv_search.search_show(sid, list(ids.values()), origin='auto', now=now, do_grab=False)
    assert tv_budget.searches_today(IPT, now) == 1


def test_feed_polls_and_the_owners_searches_are_not_counted_or_held(eng):
    _cap(1)
    now = at(2026, 10, 23, 10)
    tv_budget.count_search(IPT, now)
    tv_budget.count_search(TL, now)
    for p, inter in (('feed', False), ('manual', True), ('manual', False)):
        _, st = _q('', now + 1, purpose=p, interactive=inter)
        assert all(s['status'] == 'ok' for s in st), (p, st)
    assert len(eng.sent) == 6
    assert tv_budget.searches_today(IPT, now) == 1 and tv_budget.searches_today(TL, now) == 1


def test_the_count_resets_at_midnight_et(eng):
    _cap(2)
    late = at(2026, 10, 23, 23, 50)
    _q('A S01E01', late)
    _q('A S01E02', late + 1)
    _, st = _q('A S01E03', late + 2)
    assert all(s['status'] == tv_search.SLOT_BUSY for s in st)
    nxt = at(2026, 10, 24, 0, 5)                                          # the next day ET
    assert tv_budget.searches_today(IPT, nxt) == 0
    _, st = _q('A S01E03', nxt)
    assert all(s['status'] == 'ok' for s in st)
    assert tv_budget.searches_today(IPT, nxt) == 1


def test_the_setting_is_respected(eng):
    now = at(2026, 10, 23, 10)
    _cap(5)
    for i in range(8):
        _q(f'B S01E0{i}', now + i)
    assert tv_budget.searches_today(IPT, now) == 5
    _cap(7)
    for i in range(8):
        _q(f'C S01E0{i}', now + 100 + i)
    assert tv_budget.searches_today(IPT, now) == 7


def test_a_guard_skip_is_not_counted(eng, monkeypatch):
    from app.tracker_search import TrackerSkipped
    now = at(2026, 10, 23, 10)

    def skip(*a, **k):
        raise TrackerSkipped('busy')
    monkeypatch.setattr(eng, 'search_one', skip)
    _q('D S01E01', now)
    assert tv_budget.searches_today(IPT, now) == 0


# ------------------------------------------------------------------ v251b fix-forward

def test_manual_background_logs_against_the_real_schema(eng):
    """(3) The real tv.db schema (tv_store DDL, no migration touches search_log) has the purpose CHECK;
    a monitor click's background lane is logged as 'manual' instead of failing the INSERT."""
    with tv_store.connect() as conn:
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'search_log'").fetchone()[0]
    assert "purpose IN ('feed', 'episode', 'season', 'backlog', 'manual')" in ddl
    now = at(2026, 10, 23, 10)
    items, st = _q('E S01E01', now, purpose=tv_search.MANUAL_BACKGROUND)
    assert all(s['status'] == 'ok' for s in st)
    got = rows('SELECT purpose, query FROM search_log WHERE query = ?', 'E S01E01')
    assert [r['purpose'] for r in got] == ['manual', 'manual']
    assert tv_budget.searches_today(IPT, now) == 0                       # still the owner's: not counted


@pytest.mark.parametrize('raw', ['{', '[]', '"x"', '{"day": "2026-10-23", "n": "x"}', '{"day": "2026-10-23", "n": -3}',
                                 '{"day": "2026-10-23", "n": true}', '{"day": "2026-10-23"}', 'null', '7'])
def test_a_corrupt_count_fails_open_and_never_raises(eng, raw):
    """(1) Fail open: anything unreadable counts as 0 today; the search goes ahead and the next count
    writes a clean record."""
    now = at(2026, 10, 23, 10)
    tv_store.set_meta(tv_budget._count_key(IPT), raw)
    assert tv_budget.searches_today(IPT, now) == 0
    _, st = _q('F S01E01', now)
    assert all(s['status'] == 'ok' for s in st)
    assert tv_budget.searches_today(IPT, now) == 1
    assert tv_store.get_meta(tv_budget._count_key(IPT)) == '{"day": "2026-10-23", "n": 1}'


def test_a_count_store_failure_never_fails_the_search(eng, monkeypatch):
    now = at(2026, 10, 23, 10)

    def boom(*a, **k):
        raise RuntimeError('db locked')
    monkeypatch.setattr(tv_budget, '_bump', boom)
    monkeypatch.setattr(tv_store, 'get_meta', boom)
    _, st = _q('G S01E01', now)
    assert all(s['status'] == 'ok' for s in st)
    assert tv_budget.searches_today(IPT, now) == 0


def test_parallel_takes_hit_the_cap_exactly(eng):
    """(2) 16 threads x 10 takes against a cap of 50: exactly 50 granted, the stored count is 50."""
    import threading
    _cap(50)
    now = at(2026, 10, 23, 10)
    granted, lock = [], threading.Lock()

    def worker():
        for _ in range(10):
            ok = tv_budget.take_search(IPT, now)
            with lock:
                granted.append(ok)
    ts_ = [threading.Thread(target=worker) for _ in range(16)]
    for t in ts_:
        t.start()
    for t in ts_:
        t.join()
    assert granted.count(True) == 50 and granted.count(False) == 110
    assert tv_budget.searches_today(IPT, now) == 50


def test_parallel_counts_never_undercount(eng):
    import threading
    now = at(2026, 10, 23, 10)
    ts_ = [threading.Thread(target=lambda: [tv_budget.count_search(MTV_CFG, now) for _ in range(25)]) for _ in range(8)]
    for t in ts_:
        t.start()
    for t in ts_:
        t.join()
    assert tv_budget.searches_today(MTV_CFG, now) == 200


def test_parallel_query_trackers_never_overshoot(eng):
    import threading
    _cap(20)
    now = at(2026, 10, 23, 10)
    ts_ = [threading.Thread(target=lambda i=i: [_q(f'H S01E{i:02d}{j}', now) for j in range(5)]) for i in range(10)]
    for t in ts_:
        t.start()
    for t in ts_:
        t.join()
    by = {}
    for name, _, _ in eng.sent:
        by[name] = by.get(name, 0) + 1
    assert by == {'IPTorrents': 20, 'MoreThanTV': 20}


MTV_CFG = TL
