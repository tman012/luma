"""v233 TV search audit (PM): Judy Justice by SxxEyy, season searches that miss episodes, the 15-min retry
window after air, and episodes with no air time. Every tracker, Plex and Sonarr call is mocked."""
import datetime as dt
import json

import pytest

from app import tv_search, tv_store
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401

ET = dt.timezone(dt.timedelta(hours=-4))          # October: EDT


def ts(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=ET).timestamp()


def make_show(env, title, series_type, season, numbers, air_date, airstamp=None, runtime=None):
    """Turn the fixture's show into *title* with wanted S{season}E{n} episodes aired on *air_date*."""
    sid = env.sid
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET title = ?, aliases = ?, year = NULL, series_type = ? WHERE id = ?',
                     (title, '[]', series_type, sid))
        conn.execute('UPDATE episodes SET monitored = 0 WHERE series_id = ?', (sid,))
        ids = {}
        for n in numbers:
            cur = conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date, airstamp, runtime,'
                               ' monitored) VALUES (?, ?, ?, ?, ?, ?, 1)', (sid, season, n, air_date, airstamp, runtime))
            ids[n] = cur.lastrowid
    return sid, ids


# ------------------------------------------------------------------ 1. Judy Justice

def test_judy_justice_is_not_in_the_daily_titles_but_raw_is():
    assert 'judy justice' not in tv_store.DAILY_TITLES
    assert {'wwe raw', 'wwe monday night raw'} <= set(tv_store.DAILY_TITLES)


def test_v12_migration_puts_judy_justice_back_to_standard_raw_stays_daily(env):
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET title = 'Judy Justice', series_type = 'daily' WHERE id = ?", (env.sid,))
        other = conn.execute('SELECT id FROM series WHERE id != ? ORDER BY id LIMIT 1', (env.sid,)).fetchone()['id']
        conn.execute("UPDATE series SET title = 'WWE Raw', series_type = 'daily' WHERE id = ?", (other,))
        assert tv_store.undaily_titles(conn) == [env.sid]
        assert tv_store.undaily_titles(conn) == []                                   # idempotent
        assert tv_store.daily_types(conn) == []                                      # v11 no longer sets it Daily
    got = {r['id']: r['series_type'] for r in rows('SELECT id, series_type FROM series WHERE id IN (?, ?)', env.sid, other)}
    assert got == {env.sid: 'standard', other: 'daily'}


def test_v12_keeps_daily_when_a_grab_was_named_by_date(env):
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET title = 'Judy Justice', series_type = 'daily' WHERE id = ?", (env.sid,))
        conn.execute("INSERT INTO grabs (hash, series_id, release_title, origin, grabbed_at) VALUES (?, ?, ?, 'auto', 1)",
                     ('f' * 40, env.sid, 'Judy.Justice.2026.10.19.720p.WEB.h264-GRP'))
        assert tv_store.undaily_titles(conn) == []


def test_daily_shows_search_by_date_and_by_sxxeyy_when_numbered():
    show = {'title': 'WWE Raw', 'series_type': 'daily'}
    want = [{'season_number': 2026, 'episode_number': 42, 'air_date': '2026-10-20'},
            {'season_number': 0, 'episode_number': 0, 'air_date': '2026-10-13'}]
    assert tv_search._queries(show, want) == [('episode', 'WWE Raw 2026.10.13'), ('episode', 'WWE Raw 2026.10.20'),
                                              ('episode', 'WWE Raw S2026E42')]


@pytest.mark.parametrize('series_type', ['standard', 'daily'])
def test_judy_justice_e117_e118_found_and_grabbed(env, monkeypatch, series_type):
    allow_grabs(monkeypatch)
    sid, ids = make_show(env, 'Judy Justice', series_type, 4, (117, 118), '2026-10-20', '2026-10-20T07:00:00Z', 30)
    names = ['Judy Justice S04E117 1080p WEB h264-EDITH', 'Judy Justice S04E118 1080p WEB h264-EDITH']
    env.results = [item(n) for n in names]
    out = tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 20, 4))
    sent = [q for q, _, _ in env.sent]
    # standard: the season query; daily: by date and by SxxEyy (date alone found nothing on IPT)
    assert sent == (['Judy Justice S04'] if series_type == 'standard' else
                    ['Judy Justice 2026.10.20', 'Judy Justice S04E117', 'Judy Justice S04E118'])
    assert sorted(p['title'] for p in out['picks'] if p['grabbed']) == names
