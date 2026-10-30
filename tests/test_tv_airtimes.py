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


class _Resp:
    def __init__(self, code, loc=None, body=None):
        self.status_code, self.headers, self._b = code, ({'Location': loc} if loc else {}), body

    def json(self):
        return self._b


def test_lookup_reads_tvmaze_301_without_following(monkeypatch):
    """TVmaze answers a found /lookup/shows with 301 -> /shows/{id}; that raised HTTPError every open."""
    import requests
    seen = []

    class Sess:
        trust_env = True

        def get(self, url, params=None, timeout=None, allow_redirects=True, headers=None):
            seen.append((url, allow_redirects))
            if '/lookup/shows' in url:
                return _Resp(301, 'https://api.tvmaze.com/shows/44933')
            return _Resp(301, 'https://evil.example/shows/1')
    monkeypatch.setattr(requests, 'Session', Sess)
    tv_airtimes.configure(get=None, sleep=lambda s: None)
    assert tv_airtimes.lookup(371980, None) == 44933
    assert all(a is False for _, a in seen)                                         # never followed
    with pytest.raises(requests.HTTPError):
        tv_airtimes._get('/shows/1/episodes')                                       # other redirects still fail
    tv_airtimes.configure(get=None, sleep=lambda s: None)


def test_failure_keeps_saved_air_times_and_backs_off(scanned):  # noqa: F811
    import requests
    tv_airtimes.configure(get=Maze({1: SEV}), sleep=lambda s: None)
    tv_airtimes.refresh(now=5000.0)
    saved = _eps(95396)
    assert saved
    calls = []

    def down(path, params):
        calls.append(path)
        raise requests.HTTPError('HTTP 429')
    tv_airtimes.configure(get=down, sleep=lambda s: None)
    write('UPDATE series SET tvmaze_checked_at = NULL, tvmaze_id = NULL')
    assert tv_airtimes.refresh(now=200000.0) == 0
    n = len(calls)
    assert n >= 1 and _eps(95396) == saved                                          # last good times kept
    assert tv_airtimes.refresh(now=200030.0) == 0 and len(calls) == n               # next minute: backing off
    assert tv_airtimes.refresh(now=200061.0) == 0 and len(calls) > n                # 1 min later: one try
    m = len(calls)
    assert tv_airtimes.refresh(now=200121.0) == 0 and len(calls) == m               # now 2 min
    tv_airtimes.configure(get=Maze({1: SEV}), sleep=lambda s: None)
