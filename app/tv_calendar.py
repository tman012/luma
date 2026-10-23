"""TV Calendar, Search and Show detail (Settings > TV; Designer spec docs/design/tv.md).

Reads tv.db only, except Search (TMDB through tv_tmdb: the shared rate budget
and 24 h cache) and Add, which saves a show to tv.db for one of the selected
TV library paths. Add creates no folder or file and downloads nothing.

Episode dates are TMDB air dates (YYYY-MM-DD, the network's day). Whether an
aired episode is missing depends on the viewer's today, so the browser decides
that; the server says only whether a file is linked ("downloaded").
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Dict, List, Optional

from app import tv_match, tv_plex, tv_scan, tv_store, tv_tmdb

logger = logging.getLogger(__name__)

MAX_DAYS = 62
FIND_LIBRARY_MAX = 10
FIND_TMDB_MAX = 10
SEASONS_LOOKUP = 8                 # TMDB results whose season count is looked up (cached details)
CHOOSE_LIBRARY = 'Choose a library'
NOT_A_LIBRARY = 'Not a TV library'
NO_LIBRARIES = 'No TV libraries'
ALREADY = 'Already in library'
NOT_FOUND = 'Not found'


def _visible_paths() -> Dict[str, Dict[str, Any]]:
    from app import tv_settings
    return {p['id']: p for p in tv_settings.libraries()}


def _in(ids) -> str:
    return ','.join('?' * len(ids))


def parse_day(s: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)) if isinstance(s, str) and len(s) == 10 else None
    except ValueError:
        return None


def _in_plex(r) -> bool:
    try:
        return tv_plex.has(r['show_tmdb'], r['show_tvdb'], r['season_number'], r['episode_number'])
    except Exception:                     # noqa: BLE001 - Plex trouble: linked files still answer
        return False


def calendar(start: dt.date, days: int) -> Dict[str, Any]:
    """Episodes of the shows in the selected TV libraries airing on
    [start, start + days), by air date then show."""
    days = max(1, min(int(days), MAX_DAYS))
    end = start + dt.timedelta(days=days)
    paths = _visible_paths()
    out: List[Dict[str, Any]] = []
    if paths:
        with tv_store.connect() as conn:
            for r in conn.execute(
                    'SELECT e.id, e.series_id, e.season_number, e.episode_number, e.title, e.air_date, e.airstamp,'
                    ' COALESCE(e.air_runtime, e.runtime) AS minutes,'
                    ' s.title AS show, s.year, s.poster_path, s.tmdb_id AS show_tmdb, s.tvdb_id AS show_tvdb,'
                    ' (s.monitored AND e.monitored) AS mon,'
                    # downloaded: a linked file for this exact episode row (one rule, no fallback)
                    ' EXISTS (SELECT 1 FROM episode_file_links l JOIN episode_files f ON f.id = l.file_id'
                    "         WHERE l.episode_id = e.id AND f.state = 'linked')"
                    ' AS downloaded'
                    ' FROM episodes e JOIN series s ON s.id = e.series_id'
                    f' WHERE s.path_id IN ({_in(paths)}) AND e.air_date >= ? AND e.air_date < ?'
                    ' ORDER BY e.air_date, s.title COLLATE NOCASE, e.season_number, e.episode_number',
                    (*paths, start.isoformat(), end.isoformat())):
                out.append({'episode_id': r['id'], 'series_id': r['series_id'], 'tmdb_id': r['show_tmdb'], 'monitored': bool(r['mon']), 'show': r['show'], 'year': r['year'],
                            'poster_path': r['poster_path'], 'season': r['season_number'],
                            'episode': r['episode_number'], 'title': r['title'] or '', 'air_date': r['air_date'],
                            'airstamp': r['airstamp'], 'runtime': r['minutes'],
                            # v178 (Taylor): text-only cards, so no episode stills or backdrops in the answer
                            # or Plex has it (tv_plex: read-only, cached; empty when Plex is down)
                            'downloaded': bool(r['downloaded']) or _in_plex(r),
                            'quality': None})
            _add_file_quality(conn, out)
    out.sort(key=lambda e: (e['air_date'], tv_scan.sort_title(e['show']), e['season'] or 0, e['episode'] or 0))
    return {'start': start.isoformat(), 'days': days, 'episodes': out, 'libraries': len(paths),
            'refreshed_at': _last_refresh()}


def _add_file_quality(conn, eps: List[Dict[str, Any]]) -> None:
    """v197: each episode's 'quality' is its linked file's ('1080p WEB', the detail page's tv_monitor._file_quality
    from episode_files resolution/source, or the release name for older scan rows); None when there's no file."""
    if not eps:
        return
    from app import tv_monitor               # lazy: tv_monitor is the heavier module
    by_id = {e['episode_id']: e for e in eps}
    ids = list(by_id)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for x in conn.execute(
                'SELECT l.episode_id, f.resolution, f.source, f.release_name, f.rel_path FROM episode_file_links l'
                f" JOIN episode_files f ON f.id = l.file_id WHERE f.state = 'linked' AND l.episode_id IN ({_in(chunk)})"
                ' ORDER BY f.id', tuple(chunk)):
            e = by_id.get(x['episode_id'])
            if e is not None and e['quality'] is None:
                e['quality'] = _label(dict(x))
    _add_grab_quality(conn, [e for e in eps if e['quality'] is None and e['downloaded']])


def _label(f: Dict[str, Any]) -> Optional[str]:
    """v211 (Taylor): the quality label only when the quality is known (a resolution AND a source, stored or read
    off the name); a resolution alone is ignored (None), and the phone chip then reads 'Downloaded'."""
    from app import tv_monitor
    if f.get('resolution') and not f.get('source'):
        return None
    return tv_monitor._file_quality(f)


def _add_grab_quality(conn, eps: List[Dict[str, Any]]) -> None:
    """v208 (Taylor, live 87f6a4c): a file named by the standard format ('Show - S01E05 - Title', no quality
    token) has no resolution/source in its scan row, so its quality is the newest imported grab's for that
    episode (grabs.quality '1080p-web', else read off release_title). Reads only; None when no grab placed it."""
    if not eps:
        return
    from app import tv_monitor
    by_id = {e['episode_id']: e for e in eps}
    ids = list(by_id)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for x in conn.execute(
                'SELECT CAST(j.value AS INTEGER) AS episode_id, g.quality, g.release_title FROM grabs g, json_each(g.episode_ids) j'
                f" WHERE g.state IN ('imported', 'partial') AND CAST(j.value AS INTEGER) IN ({_in(chunk)})"
                ' ORDER BY COALESCE(g.finished_at, g.grabbed_at) DESC', tuple(chunk)):
            e = by_id.get(x['episode_id'])
            if e is None or e['quality'] is not None:
                continue
            q = x['quality'] if x['quality'] in tv_monitor.QUALITY_IDS else tv_monitor.quality_of(str(x['release_title'] or ''))
            if q:
                res, _, src = q.partition('-')
                e['quality'] = _label({'resolution': res, 'source': src})


def _last_refresh() -> Optional[float]:
    with tv_store.connect() as conn:
        r = conn.execute('SELECT MAX(tmdb_refreshed_at) AS t FROM series WHERE path_id IS NOT NULL').fetchone()
    return r['t'] if r else None


def _season_count(conn, sid: int) -> int:
    return conn.execute('SELECT COUNT(DISTINCT season_number) AS n FROM episodes WHERE series_id = ? AND season_number > 0',
                        (sid,)).fetchone()['n']


def find(q: str) -> Dict[str, Any]:
    """Search: shows in the selected TV libraries first ("In library"), then
    TMDB TV results not already listed. tmdb:/tvdb:/imdb: ids work too.
    Raises tv_tmdb.TmdbError when TMDB can't be asked."""
    q = q.strip()
    paths = _visible_paths()
    lib: List[Dict[str, Any]] = []
    seen = set()
    nq = tv_match.normalize(q)
    if paths and nq:
        with tv_store.connect() as conn:
            rows = list(conn.execute(f'SELECT id, tmdb_id, title, original_title, aliases, year, poster_path FROM series'
                                     f' WHERE path_id IN ({_in(paths)})', tuple(paths)))
            hits = []
            for r in rows:
                names = [r['title'] or '', r['original_title'] or ''] + _aliases(r['aliases'])
                norm = [tv_match.normalize(n) for n in names if n]
                if any(nq == n for n in norm):
                    rank = 0
                elif any(n.startswith(nq) for n in norm):
                    rank = 1
                elif any(nq in n for n in norm):
                    rank = 2
                else:
                    continue
                hits.append((rank, (r['title'] or '').lower(), r))
            for _, _, r in sorted(hits, key=lambda x: (x[0], x[1]))[:FIND_LIBRARY_MAX]:
                seen.add(r['tmdb_id'])
                lib.append({'tmdb_id': r['tmdb_id'], 'title': r['title'], 'year': r['year'],
                            'poster_path': r['poster_path'], 'seasons': _season_count(conn, r['id']),
                            'in_library': True, 'series_id': r['id']})
    ids = tv_scan.lookup(q)
    results = ids if ids is not None else tv_tmdb.search(q)
    tmdb = []
    for c in results:
        if c['tmdb_id'] in seen:
            continue
        seen.add(c['tmdb_id'])
        tmdb.append({'tmdb_id': c['tmdb_id'], 'title': c['title'], 'year': c.get('year'),
                     'poster_path': c.get('poster_path'), 'seasons': None, 'in_library': False, 'series_id': None})
        if len(tmdb) >= FIND_TMDB_MAX:
            break
    # season counts for the first results (cached details; one budget with every other TMDB read)
    def seasons_of(c):
        try:
            d = tv_tmdb.show(c['tmdb_id'])
        except tv_tmdb.TmdbError:
            return {}
        n = d.get('number_of_seasons') if isinstance(d, dict) else None
        return {'n': n if isinstance(n, int) and not isinstance(n, bool) else None}
    head = tmdb[:SEASONS_LOOKUP]
    for c, res in zip(head, tv_scan.run_pool(head, seasons_of)):
        c['seasons'] = res.get('n')
    return {'results': lib + tmdb}


def _aliases(raw) -> List[str]:
    import json
    try:
        v = json.loads(raw or '[]')
    except ValueError:
        return []
    return [str(a) for a in v if isinstance(a, str)]


@tv_scan._locked
def add(tmdb_id: int, path_id: Optional[str], now: Optional[float] = None, monitor: Optional[str] = None,
        profile_id: Optional[int] = None, search: bool = True) -> Dict[str, Any]:
    """Add from Search: the show goes into tv.db for *path_id* (one of the
    selected TV libraries; optional when there's only one) and onto the
    calendar. No folder or file is created and nothing is downloaded. A show
    tv.db already knows but hides (detached) comes back with its history."""
    now = time.time() if now is None else now
    paths = _visible_paths()
    if not paths:
        return {'ok': False, 'error': NO_LIBRARIES, 'status': 409}
    if path_id is None:
        if len(paths) != 1:
            return {'ok': False, 'error': CHOOSE_LIBRARY, 'status': 400}
        path_id = next(iter(paths))
    elif path_id not in paths:
        return {'ok': False, 'error': NOT_A_LIBRARY, 'status': 400}
    with tv_store.connect() as conn:
        known = conn.execute('SELECT id, path_id FROM series WHERE tmdb_id = ?', (tmdb_id,)).fetchone()
    if known is not None and known['path_id'] is not None:
        if known['path_id'] in paths:
            return {'ok': False, 'error': ALREADY, 'status': 409, 'series_id': known['id']}
        # in a library that's no longer selected: it stays there (its files are there)
        return {'ok': False, 'error': ALREADY, 'status': 409}
    data = tv_scan.fetch_show(tmdb_id)
    if not data:
        return {'ok': False, 'error': tv_scan.SHOW_NOT_FOUND, 'status': 404}
    if known is not None:                 # hidden: back in a library, history kept
        with tv_store.write() as conn:
            conn.execute('UPDATE series SET path_id = ?, folder = NULL WHERE id = ?', (path_id, known['id']))
        tv_scan.refresh_episodes(known['id'], tmdb_id, now, data)
        if monitor is not None or profile_id is not None:
            from app import tv_monitor
            tv_monitor.edit(known['id'], {**({'monitor': monitor} if monitor else {}),
                                          **({'profile_id': profile_id} if profile_id is not None else {})})
        with tv_store.write() as conn:
            conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                         (now, known['id'], '[]', 'added', __import__('json').dumps(
                             {'via': 'search', 'path_id': path_id, 'tmdb_id': tmdb_id})))
        if search:
            _search_on_add(known['id'], now)
        return {'ok': True, 'series_id': known['id']}
    sid = tv_scan.store_show(path_id, None, tmdb_id, now, via='search', event='added', data=data,
                             monitor=monitor, profile_id=profile_id)
    if sid is None:
        return {'ok': False, 'error': ALREADY, 'status': 409}
    if search:                            # v253: Anticipated shows' Search when added (on by default)
        _search_on_add(sid, now)
    return {'ok': True, 'series_id': sid}


def _search_on_add(sid: int, now: float) -> None:
    """v158b: the new show's wanted episodes (its Monitor option) are searched on the next pass."""
    try:
        from app import tv_search
        tv_search.on_added(sid, now)
    except Exception:                     # noqa: BLE001 - an Add never fails over its search
        logger.exception('TV search on add failed')
