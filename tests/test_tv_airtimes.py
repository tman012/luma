"""Calendar air times from TVmaze (no key): TMDB external ids -> TVmaze show
(tvdb first, else imdb), episodes' airstamp + runtime into tv.db, paced, a
show TVmaze doesn't know keeps the date only, and a rematch mid-refresh never
attaches another show's times (or TMDB episodes)."""
import datetime as dt

import pytest

from app import tv_airtimes, tv_calendar, tv_scan, tv_store
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_file_management import store  # noqa: F401


def write(sql, *a):
    with tv_store.write() as conn:
        conn.execute(sql, a)


class Maze:
    def __init__(self, shows):
        self.shows, self.calls = shows, []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if path == '/lookup/shows':
            for mid, s in self.shows.items():
                if params.get('thetvdb') == s.get('tvdb') or params.get('imdb') == s.get('imdb'):
                    return {'id': mid}
            return None
        mid = int(path.split('/')[2])
        return [{'season': se, 'number': n, 'airstamp': st, 'runtime': rt} for (se, n), (st, rt) in self.shows[mid]['eps'].items()]


SEV = {'tvdb': 371980, 'eps': {(2, 1): ('2026-10-13T01:00:00+00:00', 42), (2, 2): ('2026-10-14T01:00:00+00:00', None)}}
DARK_IMDB = {'imdb': 'tt5753856', 'eps': {(1, 1): ('2026-10-15T07:00:00+00:00', 60)}}


def _eps(tmdb_id):
    return {(r['season_number'], r['episode_number']): (r['airstamp'], r['air_runtime']) for r in rows(
        'SELECT e.season_number, e.episode_number, e.airstamp, e.air_runtime FROM episodes e JOIN series s'
        ' ON s.id = e.series_id WHERE s.tmdb_id = ? AND e.airstamp IS NOT NULL', tmdb_id)}


def test_maps_by_tvdb_then_imdb_and_stores_airstamps(scanned):  # noqa: F811
    write("UPDATE series SET tvdb_id = NULL WHERE tmdb_id = 70523")                 # Dark: imdb only
    maze = Maze({1: SEV, 2: {**DARK_IMDB, 'tvdb': -1}})
    sleeps = []
    tv_airtimes.configure(get=maze, sleep=sleeps.append)
    assert tv_airtimes.refresh(now=5000.0) >= 2
    assert _eps(95396) == {(2, 1): ('2026-10-13T01:00:00+00:00', 42), (2, 2): ('2026-10-14T01:00:00+00:00', None)}
    assert _eps(70523) == {(1, 1): ('2026-10-15T07:00:00+00:00', 60)}
    assert ('/lookup/shows', {'imdb': 'tt5753856'}) in maze.calls
    assert all(s <= tv_airtimes.PACE_S for s in sleeps) and tv_airtimes.PACE_S >= 0.5   # well under 20 calls / 10 s
    out = {(e['show'], e['season'], e['episode']): (e['airstamp'], e['runtime'])
           for e in tv_calendar.calendar(dt.date(2026, 10, 11), 7)['episodes']}
    assert out[('Severance', 2, 1)] == ('2026-10-13T01:00:00+00:00', 42)
    n = len(maze.calls)
    assert tv_airtimes.refresh(now=5001.0) == 0 and len(maze.calls) == n              # once a day per show


def test_no_match_keeps_the_date_only(scanned):  # noqa: F811
    tv_airtimes.configure(get=Maze({}), sleep=lambda s: None)
    tv_airtimes.refresh(now=5000.0)
    eps = tv_calendar.calendar(dt.date(2026, 10, 11), 7)['episodes']
    assert eps and all(e['airstamp'] is None for e in eps)
    assert rows('SELECT COUNT(*) AS n FROM series WHERE tvmaze_checked_at = 5000.0')[0]['n'] > 0   # asked; not again today


def test_a_rematch_mid_refresh_never_attaches_the_wrong_show(scanned):  # noqa: F811
    sid = rows('SELECT id FROM series WHERE tmdb_id = 95396')[0]['id']
    write('UPDATE series SET tmdb_id = 999001 WHERE id = ?', sid)                    # rematched after the read
    assert tv_airtimes.store(sid, 95396, 1, {(2, 1): {'airstamp': '2026-10-13T01:00:00+00:00', 'runtime': 42}}, 5000.0) is False
    assert rows('SELECT COUNT(*) AS n FROM episodes WHERE series_id = ? AND airstamp IS NOT NULL', sid)[0]['n'] == 0
    before = rows('SELECT COUNT(*) AS n FROM episodes WHERE series_id = ?', sid)[0]['n']
    data = tv_scan.fetch_show(95396)
    tv_scan.refresh_episodes(sid, 95396, 6000.0, data)                               # Reviewer on d6dfb3b: same guard
    assert rows('SELECT COUNT(*) AS n FROM episodes WHERE series_id = ?', sid)[0]['n'] == before
    assert rows('SELECT tmdb_refreshed_at FROM series WHERE id = ?', sid)[0]['tmdb_refreshed_at'] != 6000.0


def test_tvmaze_down_stops_quietly(scanned):  # noqa: F811
    import requests

    def down(path, params):
        raise requests.ConnectionError('down')
    tv_airtimes.configure(get=down, sleep=lambda s: None)
    assert tv_airtimes.refresh(now=5000.0) == 0
