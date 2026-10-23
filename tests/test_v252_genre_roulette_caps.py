"""v252: Genre roulette's automatic movie searches have their own purpose (tv_search.AUTOMATION): they count toward
each tracker's daily background search cap (take_search/refund_search) but never use the TV backlog's 60% share;
search_log (whose purpose CHECK is never migrated) records them as 'backlog' in the movies section. Its movie grabs
go through movie_scanner.grab, never tv_grab.grab, so the TV backlog auto-grab cap (v253a) never counts or holds them.
Trackers are fakes (tests.test_v251_tv_daily_search_cap.Engine)."""
import random

import pytest

from app import tracker_search, tv_budget, tv_search, tv_store
from app.automation import gr_engine
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401
from tests.test_v251_tv_daily_search_cap import IPT, _cap, at, eng  # noqa: F401

MOV = {'id': 9, 'name': 'MoreThanTV', 'type': 'morethantv', 'priority': 1, 'categories': [2000, 2040, 5000, 5030]}   # movies and TV
FILM = {'title': 'Heat', 'year': 1995}


def _gr(now, n=1):
    for i in range(n):
        _, st = gr_engine.search({**FILM, 'title': f'Heat {i}'}, [MOV], now + i)
    return st


def test_automation_counts_toward_the_daily_cap_but_not_the_backlog_share(eng):  # noqa: F811
    _cap(50)
    now = at(2026, 10, 23, 10)
    for i in range(40):                                                     # TV backlog: held at its 30
        tv_search.query_trackers(f'Old S01E{i:02d}', tv_search.BACKLOG, None, False, now + i, [MOV])
    assert len(eng.sent) == 30 and tv_budget.searches_today(MOV, now) == 30
    st = _gr(now + 100, 25)                                                 # Genre roulette: the other 20
    assert len(eng.sent) == 50 and tv_budget.searches_today(MOV, now) == 50
    assert st[0]['status'] == tv_search.SLOT_BUSY and st[0]['reset_at'] == at(2026, 10, 24)
    assert _gr(at(2026, 10, 24, 0, 1))[0]['status'] == 'ok'                 # a new day ET


def test_a_full_backlog_share_never_holds_genre_roulette(eng):  # noqa: F811
    _cap(10)
    now = at(2026, 10, 23, 10)
    for _ in range(6):
        tv_budget.take_search(MOV, now, backlog=True)                       # 6 of 10: the backlog share is full
    assert not tv_budget.take_search(MOV, now, backlog=True)
    assert _gr(now + 1)[0]['status'] == 'ok' and tv_budget.searches_today(MOV, now) == 7


def test_searches_are_reserved_without_the_backlog_flag_and_refunded_when_the_guard_holds_them(eng, monkeypatch):  # noqa: F811
    calls = []
    real = tv_budget.take_search
    monkeypatch.setattr(tv_budget, 'take_search', lambda cfg, now=None, backlog=False: calls.append(backlog) or real(cfg, now, backlog))
    now = at(2026, 10, 23, 10)
    _gr(now, 3)
    assert calls == [False, False, False] and tv_budget.searches_today(MOV, now) == 3

    def held(*a, **k):
        raise tracker_search.TrackerSkipped('backoff')
    monkeypatch.setattr(eng, 'search_one', held)
    _gr(now + 10)
    assert tv_budget.searches_today(MOV, now) == 3                          # not sent: given back


def test_search_log_records_them_as_backlog_in_the_movies_section(eng):  # noqa: F811
    now = at(2026, 10, 23, 10)
    _gr(now, 2)
    with tv_store.connect() as conn:
        got = [tuple(r) for r in conn.execute('SELECT section, purpose, outcome FROM search_log ORDER BY id')]
    assert got == [('movies', 'backlog', 'ok')] * 2
    assert list(tv_store.search_counts('movies', now=now + 10).values()) == [{'backlog': 2}]
    assert tv_store.search_counts('tv', now=now + 10) == {}


def test_a_cap_skip_is_logged_once_and_apart_from_the_tv_lanes(eng):  # noqa: F811
    _cap(1)
    now = at(2026, 10, 23, 10)
    _gr(now, 4)
    with tv_store.connect() as conn:
        got = [tuple(r) for r in conn.execute('SELECT purpose, outcome FROM search_log ORDER BY id')]
    assert got == [('backlog', 'ok'), ('backlog', 'skipped')]
    keys = [k for k in _meta_keys() if k.startswith('tv_capskip:')]
    assert len(keys) == 1 and keys[0].endswith(':automation')


def _meta_keys():
    with tv_store.connect() as conn:
        return [r[0] for r in conn.execute('SELECT key FROM meta')]


def test_the_tv_lanes_are_unchanged(eng):  # noqa: F811
    _cap(50)
    now = at(2026, 10, 23, 10)
    tv_search.query_trackers('Show S01E01', 'episode', None, False, now, [IPT])
    tv_search.query_trackers('Show S01E02', tv_search.BACKLOG, None, False, now + 1, [IPT])
    tv_search.query_trackers('Movie 2000', 'backlog', None, False, now + 2, [MOV], categories=[2000], section='movies')
    assert tv_budget.searches_today(IPT, now) == 2 and tv_budget.searches_today(MOV, now) == 0   # the movie scanner's own lane: as before
    assert tv_search.LOG_PURPOSE == {tv_search.MANUAL_BACKGROUND: 'manual', tv_search.AUTOMATION: 'backlog'}


# ------------------------------------------------------------------ the TV backlog auto-grab cap never sees roulette grabs

from tests.test_v252_genre_roulette import World, cfg, film, NOW  # noqa: E402
from tests.test_v252_genre_roulette import REAL_GRAB  # noqa: E402
from tests.test_v203_movie_scanner import mv, rel as srel, _torrent  # noqa: E402,F401
from tests.test_tv_v158c import guard  # noqa: E402,F401


def test_roulette_grabs_are_not_counted_or_held_by_the_backlog_grab_cap(mv, tmp_path, monkeypatch):  # noqa: F811
    from app import grab_origin, movie_scanner as sc, tv_grab
    from app.automation import gr_select as sel, gr_store as gs
    monkeypatch.setitem(gs._cfg, 'db_path', str(tmp_path / 'automation.db'))
    w = World([film(693134, 'Dune: Part Two', ['Science Fiction'], year=2024, votes=9000)])
    w.owned.append((1, 'Owned', 1990, 'Movies'))
    for k, v in (('tmdb_key', lambda: 'k'), ('tmdb_get', w.tmdb_get), ('omdb_key_set', lambda: False), ('plex_index', w.plex)):
        monkeypatch.setattr(sel, k, v)
    monkeypatch.setattr(gr_engine, 'notify', lambda t, b: None)
    monkeypatch.setattr(grab_origin, 'record', lambda origin, **k: None)
    cap = tv_budget.backlog_grab_cap()
    tv_budget._bump_key(tv_budget.BACKLOG_GRAB_KEY, NOW, cap, None)          # today's TV backlog grabs: at the cap
    assert not tv_budget.take_backlog_grab(NOW)

    def never(*a, **k):
        raise AssertionError('the TV backlog grab cap was consulted')
    monkeypatch.setattr(tv_budget, 'take_backlog_grab', never)
    monkeypatch.setattr(tv_budget, 'refund_backlog_grab', never)
    monkeypatch.setattr(tv_grab, 'grab', never)
    q = mv.q
    monkeypatch.setattr(sc, 'grab', lambda item, tid, origin, now=None, **k: REAL_GRAB(
        item, tid, origin, now, session_factory=q.factory, fetch=lambda u: ('torrent', _torrent(item['title'])), **k))
    mv.results = {1: [srel('Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1-OK', seeders=40)]}
    r = gr_engine.run('manual', cfg=cfg(include_genres=['Science Fiction'], genres_per_run=1), now=NOW,
                      rng=random.Random(1), sleep=lambda s: None)
    assert r['grabbed'] == 1, r
    assert tv_budget.backlog_grabs_today(NOW) == cap                        # unchanged


@pytest.mark.parametrize('purpose', ['automation', 'manual-background'])
def test_the_search_log_check_constraint_is_unchanged(env, purpose):  # noqa: F811
    """No migration: the raw labels are still refused, which is why _log maps them."""
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        with tv_store.write() as conn:
            conn.execute("INSERT INTO search_log (ts, section, tracker_key, purpose) VALUES (1, 'movies', 'x', ?)", (purpose,))
