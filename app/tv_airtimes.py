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


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """JSON for *path*, None on 404; raises on other failures."""
    _pace()
    if _cfg['get'] is not None:
        return _cfg['get'](path, params or {})
    r = requests.get(BASE + path, params=params, timeout=TIMEOUT_S, allow_redirects=True,
                     headers={'User-Agent': 'Luma'})
    if r.status_code == 404:
        return None
    r.raise_for_status()
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


def refresh(now: Optional[float] = None, first: Optional[set] = None) -> int:
    """Air times for the selected libraries' shows not asked in REFRESH_S. Returns shows written."""
    from app import tv_settings
    now = time.time() if now is None else now
    pids = [p['id'] for p in tv_settings.libraries()]
    if not pids:
        return 0
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT id, tmdb_id, tvdb_id, imdb_id, tvmaze_id, tvmaze_checked_at FROM series WHERE tmdb_id IS NOT NULL'
            f' AND path_id IN ({",".join("?" * len(pids))}) ORDER BY COALESCE(tvmaze_checked_at, 0)', pids)
            if r['tvmaze_checked_at'] is None or now - r['tvmaze_checked_at'] >= REFRESH_S]
    first = first or set()
    # the visible week first, then never-asked shows, then the stalest
    rows.sort(key=lambda r: (r['id'] not in first, r['tvmaze_checked_at'] is not None, r['tvmaze_checked_at'] or 0))
    rows = rows[:MAX_SHOWS]
    from app import connectors, tv_tvdb
    creds = connectors.tvdb_credentials()
    n = 0
    for r in rows:
        try:
            if creds[0]:                      # TheTVDB (key in Connectors) first; nothing there: the date only
                mid = None
                eps = tv_tvdb.episodes(r['tvdb_id'], creds, _pace) if r['tvdb_id'] else {}
            else:                             # no TVDB key: TVmaze, no key
                mid = r['tvmaze_id'] or lookup(r['tvdb_id'], r['imdb_id'])
                eps = episodes(mid) if mid else {}
        except (requests.RequestException, ValueError, tv_tvdb.TvdbError) as e:
            logger.info('Air times unavailable (%s); they wait for the next open', type(e).__name__)
            break
        n += store(r['id'], r['tmdb_id'], mid, eps, now)
    return n


def _pace() -> None:
    wait = _last[0] + PACE_S - time.monotonic()
    if wait > 0:
        _cfg['sleep'](wait)
    _last[0] = time.monotonic()


def configure(get: Optional[Callable[[str, Dict[str, Any]], Any]] = None, sleep: Callable[[float], None] = time.sleep) -> None:
    """Tests: a fake TVmaze (*get*) and clock."""
    _cfg.update(get=get, sleep=sleep)
    _last[0] = 0.0


def forget() -> None:
    """A TVDB key was saved or removed: every show's air times and images are asked again on the next open."""
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET tvmaze_checked_at = NULL')
