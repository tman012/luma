"""Air times for the Calendar: TheTVDB v4 when its key is saved (tv_tvdb), else
TVmaze's free, keyless API.

A show maps to TVmaze through its TMDB external ids (tvdb_id, else imdb_id:
/lookup/shows), then /shows/{id}/episodes gives each episode's airstamp (UTC)
and runtime. Runs in the Calendar-open background refresh, paced well under
TVmaze's 20 calls / 10 s, once a day per show. Writes only tv.db, and only if
the series row still has the tmdb_id it was read for (a rematch mid-refresh
can't attach another show's times). No match: the card shows the date only.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from app import tv_store

logger = logging.getLogger(__name__)

BASE = 'https://api.tvmaze.com'
PACE_S = 1.0                    # one call a second: 10 / 10 s, half TVmaze's limit
REFRESH_S = 24 * 3600
TIMEOUT_S = 10
MAX_SHOWS = 200                 # per refresh; the rest wait for the next open

_cfg: Dict[str, Any] = {'get': None, 'sleep': time.sleep}
_last = [0.0]
# A failed refresh backs off (1, 2, 4 ... 60 min) instead of asking again on every Calendar open (every minute).
BACKOFF_S = 60.0
BACKOFF_MAX_S = 3600.0
_fail: Dict[str, float] = {'until': 0.0, 'n': 0}
_SHOW_LOC = re.compile(r'^(?:https://api\.tvmaze\.com)?/shows/(\d+)$')


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """JSON for *path*, None on 404; raises on other failures."""
    _pace()
    if _cfg['get'] is not None:
        return _cfg['get'](path, params or {})
    sess = requests.Session()
    sess.trust_env = False                       # no env proxies / netrc
    r = sess.get(BASE + path, params=params, timeout=TIMEOUT_S, allow_redirects=False, headers={'User-Agent': 'Luma'})
    if r.status_code == 404:
        return None
    if path == '/lookup/shows' and r.status_code in (301, 302):
        # TVmaze answers a found lookup with a redirect to /shows/{id}: read the id, never follow it.
        m = _SHOW_LOC.match((r.headers.get('Location') or '').strip())
        if m:
            return {'id': int(m.group(1))}
    if r.status_code != 200:                     # a redirect is never followed
        raise requests.HTTPError(f'HTTP {r.status_code}')
    return r.json()


def lookup(tvdb_id: Optional[int], imdb_id: Optional[str]) -> Optional[int]:
    """The TVmaze show id for TMDB's external ids: tvdb first, else imdb."""
    for key, val in (('thetvdb', tvdb_id), ('imdb', imdb_id)):
        if not val:
            continue
        data = _get('/lookup/shows', {key: val})
        if isinstance(data, dict) and isinstance(data.get('id'), int):
            return data['id']
    return None


def episodes(maze_id: int) -> Dict[tuple, Dict[str, Any]]:
    """{(season, number): {'airstamp', 'runtime'}} for a TVmaze show."""
    data = _get(f'/shows/{int(maze_id)}/episodes') or []
    out: Dict[tuple, Dict[str, Any]] = {}
    for e in data if isinstance(data, list) else []:
        s, n, stamp = e.get('season'), e.get('number'), e.get('airstamp')
        if isinstance(s, int) and isinstance(n, int) and isinstance(stamp, str) and stamp:
            rt = e.get('runtime')
            out[(s, n)] = {'airstamp': stamp[:40], 'runtime': rt if isinstance(rt, int) and 0 < rt < 1000 else None}
    return out


def store(sid: int, tmdb_id: int, maze_id: Optional[int], eps: Dict[tuple, Dict[str, Any]], now: float) -> bool:
    """Write one show's air times, only if row *sid* still holds *tmdb_id* (same transaction)."""
    with tv_store.write() as conn:
        if not conn.execute('SELECT 1 FROM series WHERE id = ? AND tmdb_id = ?', (sid, tmdb_id)).fetchone():
            return False
        conn.execute('UPDATE series SET tvmaze_id = ?, tvmaze_checked_at = ? WHERE id = ?', (maze_id, now, sid))
        for (s, n), v in eps.items():
            conn.execute('UPDATE episodes SET airstamp = ?, air_runtime = ?, tvdb_image = ? WHERE series_id = ?'
                         ' AND season_number = ? AND episode_number = ?',
                         (v.get('airstamp'), v.get('runtime'), v.get('image'), sid, s, n))
    return True


def refresh(now: Optional[float] = None, first: Optional[set] = None, repair: Optional[set] = None,
            only: Optional[set] = None) -> int:
    """Air times for the selected libraries' shows not asked in REFRESH_S. Returns shows written."""
    from app import tv_settings
    now = time.time() if now is None else now
    if now < _fail['until']:
        return 0                                   # backing off after a failure; cached air times stay
    pids = [p['id'] for p in tv_settings.libraries()]
    if not pids:
        return 0
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT id, tmdb_id, tvdb_id, imdb_id, tvmaze_id, tvmaze_checked_at FROM series WHERE tmdb_id IS NOT NULL'
            f' AND path_id IN ({",".join("?" * len(pids))}) ORDER BY COALESCE(tvmaze_checked_at, 0)', pids)
            if r['tvmaze_checked_at'] is None or now - r['tvmaze_checked_at'] >= REFRESH_S
            or r['id'] in (repair or ())]          # an aired episode still has no image: ask again
    if only is not None:
        rows = [r for r in rows if r['id'] in only]
    first = first or set()
    # the visible week first, then never-asked shows, then the stalest
    rows.sort(key=lambda r: (r['id'] not in first, r['tvmaze_checked_at'] is not None, r['tvmaze_checked_at'] or 0))
    rows = rows[:MAX_SHOWS]
    from app import connectors, tv_tvdb
    creds = connectors.tvdb_credentials()
    n = 0
    for r in rows:
        try:
            mid, eps = None, {}
            if creds[0] and r['tvdb_id']:     # TheTVDB (key in Connectors) first
                eps = tv_tvdb.episodes(r['tvdb_id'], creds, _pace)
            if not eps:                       # no key, no tvdb_id, or TVDB doesn't know it: TVmaze (no key)
                mid = r['tvmaze_id'] or lookup(r['tvdb_id'], r['imdb_id'])
                eps = episodes(mid) if mid else {}
        except (requests.RequestException, ValueError, tv_tvdb.TvdbError) as e:
            _fail['n'] += 1
            wait = min(BACKOFF_S * 2 ** (_fail['n'] - 1), BACKOFF_MAX_S)
            _fail['until'] = now + wait
            logger.info('Air times unavailable (%s: %s); saved air times kept, next try in %d min',
                        type(e).__name__, str(e)[:40], int(wait // 60))
            break
        n += store(r['id'], r['tmdb_id'], mid, eps, now)
    else:
        _fail.update(until=0.0, n=0)
    if rows:
        logger.info('TV air times%s: %d of %d shows written (%s), %d episodes with a TVDB image',
                    ' (this week)' if only is not None else '', n, len(rows), 'TheTVDB' if creds[0] else 'TVmaze',
                    _images([r['id'] for r in rows]))
    return n


def _images(ids) -> int:
    with tv_store.connect() as conn:
        return conn.execute(f'SELECT COUNT(*) AS n FROM episodes WHERE tvdb_image IS NOT NULL AND series_id IN'
                            f' ({",".join("?" * len(ids))})', ids).fetchone()['n']


def _pace() -> None:
    wait = _last[0] + PACE_S - time.monotonic()
    if wait > 0:
        _cfg['sleep'](wait)
    _last[0] = time.monotonic()


def configure(get: Optional[Callable[[str, Dict[str, Any]], Any]] = None, sleep: Callable[[float], None] = time.sleep) -> None:
    """Tests: a fake TVmaze (*get*) and clock."""
    _cfg.update(get=get, sleep=sleep)
    _last[0] = 0.0
    _fail.update(until=0.0, n=0)


def forget() -> None:
    """A TVDB key was saved or removed: every show's air times and images are asked again on the next open."""
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET tvmaze_checked_at = NULL')
