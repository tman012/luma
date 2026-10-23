"""v252: Genre roulette's automatic movie searches have their own purpose (tv_search.AUTOMATION). search_log (whose
purpose CHECK is never migrated) records them as 'backlog' in the movies section. No daily search cap (Taylor):
see tests/test_tv_search_no_daily_cap.py. Trackers are fakes (tests.test_tv_search_no_daily_cap.Engine)."""
import pytest

from app import tv_search, tv_store
from app.automation import gr_engine
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401
from tests.test_tv_search_no_daily_cap import IPT, at, eng  # noqa: F401

MOV = {'id': 9, 'name': 'MoreThanTV', 'type': 'morethantv', 'priority': 1, 'categories': [2000, 2040, 5000, 5030]}   # movies and TV
FILM = {'title': 'Heat', 'year': 1995}


def _gr(now, n=1):
    for i in range(n):
        _, st = gr_engine.search({**FILM, 'title': f'Heat {i}'}, [MOV], now + i)
    return st


def test_search_log_records_them_as_backlog_in_the_movies_section(eng):  # noqa: F811
    now = at(2026, 10, 23, 10)
    _gr(now, 2)
    with tv_store.connect() as conn:
        got = [tuple(r) for r in conn.execute('SELECT section, purpose, outcome FROM search_log ORDER BY id')]
    assert got == [('movies', 'backlog', 'ok')] * 2
    assert list(tv_store.search_counts('movies', now=now + 10).values()) == [{'backlog': 2}]
    assert tv_store.search_counts('tv', now=now + 10) == {}


def test_the_tv_lanes_are_unchanged(eng):  # noqa: F811
    now = at(2026, 10, 23, 10)
    tv_search.query_trackers('Show S01E01', 'episode', None, False, now, [IPT])
    tv_search.query_trackers('Show S01E02', tv_search.BACKLOG, None, False, now + 1, [IPT])
    tv_search.query_trackers('Movie 2000', 'backlog', None, False, now + 2, [MOV], categories=[2000], section='movies')
    assert [(n, p) for n, _, p in eng.sent] == [('IPTorrents', 'tv'), ('IPTorrents', 'tv'), ('MoreThanTV', 'tv')]
    assert tv_search.LOG_PURPOSE == {tv_search.MANUAL_BACKGROUND: 'manual', tv_search.AUTOMATION: 'backlog'}


@pytest.mark.parametrize('purpose', ['automation', 'manual-background'])
def test_the_search_log_check_constraint_is_unchanged(env, purpose):  # noqa: F811
    """No migration: the raw labels are still refused, which is why _log maps them."""
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        with tv_store.write() as conn:
            conn.execute("INSERT INTO search_log (ts, section, tracker_key, purpose) VALUES (1, 'movies', 'x', ?)", (purpose,))
