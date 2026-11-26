"""v359 Settings > Automation > 4K Remux auto-grab, native (luma-test; Taylor 2026-11-26: "bring it back with
prod's settings").

Prod's upgrade_4k_auto_grab.py (a Prowlarr sweep, then a Radarr push to its "2160p Remux" profile) rebuilt on
Luma's own trackers and grab path; no Prowlarr, no Radarr. Every scan_interval_minutes while on (the first scan
about 50 s after boot):

* search each tracker on in Settings > Movies, in the movie order, for '2160p REMUX' (tv_search.query_trackers,
  automatic movie purpose: the tracker guard's movie bucket, spacing and backoff apply unchanged; a tracker the
  guard holds past a short spacing wait is skipped this cycle);
* keep releases whose name says 2160p (or UHD/4K) and REMUX, with at least min_seeders seeders, published within
  max_age_hours, that match a TMDb movie (the tracker's IMDb id, else title and year within one);
* skip a release this job already tried and a movie it already grabbed; one release per movie (most seeders);
* skip a movie already in the chosen Plex library (4K Movies); when that library can't be read or reads empty
  the cycle refuses to grab;
* grab every other one through movie_scanner.grab (scope '4k': only a 4K copy on disk or in qBittorrent is a
  duplicate) into qBittorrent with the movie category and tag and a movie_grabs row; movie_import files it under
  Video/4K, the folder Plex reads as 4K Movies.

No rating, year, size or tracker caps (prod had none). Preview runs the same cycle without grabbing or recording.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

JOB = 'upgrade_4k'
ORIGIN = 'upgrade_4k'
QUERY = '2160p REMUX'
FIRST_RUN_S = 50
CHECK_S = 20
HISTORY_KEPT = 50
TRIED_KEPT = 5000
RELEASES_SHOWN = 200
TMDB_TTL_S = 7 * 86400
TMDB_MISS_TTL_S = 6 * 3600
PACE_SLACK_S = 5.0
PACE_TRIES = 2
LEGACY = 'automation.upgrade_4k_auto_grab'

DEFAULTS: Dict[str, Any] = {'enabled': False, 'min_seeders': 100, 'max_age_hours': 24,
                            'scan_interval_minutes': 15, 'plex_4k_section_id': 0}
LIMITS = {'min_seeders': (1, 5000), 'max_age_hours': (1, 168), 'scan_interval_minutes': (5, 120),
          'plex_4k_section_id': (0, 1000000)}

#: a release's decision in a run's releases list
REASONS = {
    'would_grab': 'Would grab', 'grabbed': 'Grabbed', 'seeders': 'Below minimum seeders',
    'age': 'Older than max age', 'no_date': 'No publish date', 'tried': 'Already tried',
    'no_tmdb': 'No TMDb match', 'tmdb_error': "TMDb couldn't be reached", 'already_grabbed': 'Already grabbed',
    'same_movie': 'Another release of this movie has more seeders', 'in_4k': 'Already in 4K Movies',
    'refused': "4K Plex library couldn't be read", 'duplicate': 'Already have it', 'link': "Couldn't fetch the release",
    'rejected': 'Grab refused', 'stopped': 'Not tried: grabs stopped',
}

_lock = threading.RLock()
_run = {'running': False, 'kind': None}
_boot = {'at': time.time()}
_sleep: Dict[str, Callable[[float], None]] = {'fn': time.sleep}
_preview: Dict[str, Any] = {'running': False, 'result': None, 'at': None}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fk_runs (
  id INTEGER PRIMARY KEY, started_at REAL NOT NULL, finished_at REAL, kind TEXT NOT NULL,
  status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS fk_grabs (
  hash TEXT PRIMARY KEY, tmdb_id INTEGER NOT NULL, title TEXT NOT NULL, year INTEGER, release TEXT NOT NULL,
  tracker TEXT, seeders INTEGER, size INTEGER, run_id INTEGER, grabbed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS fk_grabs_tmdb ON fk_grabs (tmdb_id);
"""


# ------------------------------------------------------------------ store (automation.db)

def db_path() -> str:
    from app.automation import gr_store
    return str(gr_store.db_path())


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    p = db_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        os.close(os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        pass
    conn = sqlite3.connect(p, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with _lock:
            conn.executescript(SCHEMA)
            yield conn
            conn.commit()
    finally:
        conn.close()


def _meta(conn, key: str, default: Any) -> Any:
    r = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    if not r:
        return default
    try:
        return json.loads(r['value'])
    except ValueError:
        return default


def _set_meta(conn, key: str, value: Any) -> None:
    conn.execute('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                 (key, json.dumps(value)))


# ------------------------------------------------------------------ settings

class Invalid(ValueError):
    def __init__(self, errors: Dict[str, str]):
        super().__init__('invalid settings')
        self.errors = errors


def check(body: Any, cur: Dict[str, Any]) -> Dict[str, Any]:
    """*cur* with *body*'s known keys applied; Invalid({key: reason}) when any is bad. Unknown keys (prod's
    quality_profile_id) are ignored."""
    if not isinstance(body, dict):
        raise Invalid({'_': 'Send an object'})
    out, errors = dict(cur), {}
    for k, v in body.items():
        if k not in DEFAULTS:
            continue
        if k == 'enabled':
            if isinstance(v, bool):
                out[k] = v
            else:
                errors[k] = 'Must be on or off'
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and
                                                                      (not math.isfinite(v) or v != int(v))):
            errors[k] = 'Must be a whole number'
            continue
        lo, hi = LIMITS[k]
        if not lo <= int(v) <= hi:
            errors[k] = f'Must be {lo} to {hi}'
            continue
        out[k] = int(v)
    if errors:
        raise Invalid(errors)
    return out


def _legacy() -> Dict[str, Any]:
    """Prod's settings.json block (automation.upgrade_4k_auto_grab), read once at first load; bad keys dropped."""
    try:
        from app.settings_manager import SettingsFile
        s = SettingsFile()
        raw = {k: s.get(f'{LEGACY}.{k}', None) for k in DEFAULTS}
    except Exception:                                   # noqa: BLE001
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if v is None:
            continue
        try:
            out.update({k: check({k: v}, DEFAULTS)[k]})
        except Invalid:
            pass
    return out


def _fill(cur: Any) -> Dict[str, Any]:
    cur = cur if isinstance(cur, dict) else {}
    return {k: cur.get(k, v) for k, v in DEFAULTS.items()}


def settings() -> Dict[str, Any]:
    with connect() as conn:
        cur = _meta(conn, 'fk_settings', None)
        if not isinstance(cur, dict):
            cur = {**DEFAULTS, **_legacy()}
            _set_meta(conn, 'fk_settings', cur)
    return _fill(cur)


def save(body: Any) -> Dict[str, Any]:
    new = check(body, settings())
    with connect() as conn:
        _set_meta(conn, 'fk_settings', new)
    _mirror(new)
    return new


def _mirror(cfg: Dict[str, Any]) -> None:
    """Keep settings.json's prod block in step (the backup and prod parity read it). Never raises."""
    try:
        from app.settings_manager import SettingsManager
        SettingsManager().update({'automation': {'upgrade_4k_auto_grab': {k: cfg[k] for k in DEFAULTS}}})
    except Exception:                                   # noqa: BLE001
        logger.warning('4K Remux: settings.json mirror failed', exc_info=True)


def state() -> Dict[str, Any]:
    with connect() as conn:
        st = _meta(conn, 'fk_state', {})
    return st if isinstance(st, dict) else {}


def _put_state(**kw: Any) -> None:
    with connect() as conn:
        st = _meta(conn, 'fk_state', {})
        st = st if isinstance(st, dict) else {}
        st.update(kw)
        _set_meta(conn, 'fk_state', st)


def _tried() -> Dict[str, float]:
    t = state().get('tried')
    return t if isinstance(t, dict) else {}


def _mark_tried(guids: List[str], now: float) -> None:
    if not guids:
        return
    with connect() as conn:
        st = _meta(conn, 'fk_state', {})
        st = st if isinstance(st, dict) else {}
        t = st.get('tried') if isinstance(st.get('tried'), dict) else {}
        for g in guids:
            t[g] = now
        if len(t) > TRIED_KEPT:
            t = dict(sorted(t.items(), key=lambda kv: kv[1])[-TRIED_KEPT:])
        st['tried'] = t
        _set_meta(conn, 'fk_state', st)


def grabbed_ids() -> Set[int]:
    with connect() as conn:
        return {int(r['tmdb_id']) for r in conn.execute('SELECT tmdb_id FROM fk_grabs')}


def grabs(limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM fk_grabs ORDER BY grabbed_at DESC LIMIT ?', (limit,))]


def _record_grab(h: str, c: Dict[str, Any], run_id: Optional[int], now: float) -> None:
    with connect() as conn:
        conn.execute('INSERT OR REPLACE INTO fk_grabs (hash, tmdb_id, title, year, release, tracker, seeders, size,'
                     ' run_id, grabbed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     (h, c['tmdb_id'], c['title'], c.get('year'), c['release'], c.get('tracker'), c.get('seeders'),
                      c.get('size'), run_id, now))


def owns(h: str) -> bool:
    """A download this job grabbed (movie_import files it under 4K)."""
    try:
        with connect() as conn:
            return conn.execute('SELECT 1 FROM fk_grabs WHERE hash = ?', (str(h or '').lower(),)).fetchone() is not None
    except (sqlite3.Error, OSError):
        return False


def title_for(tmdb_id: int) -> Optional[Dict[str, Any]]:
    """{title, year} of a movie this job grabbed (movie_import files grabs not on the watchlist by it)."""
    try:
        with connect() as conn:
            r = conn.execute('SELECT title, year FROM fk_grabs WHERE tmdb_id = ? ORDER BY grabbed_at DESC LIMIT 1',
                             (int(tmdb_id),)).fetchone()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return None
    return {'tmdb_id': int(tmdb_id), 'title': r['title'], 'year': r['year']} if r else None


def _start_run(kind: str, now: float) -> int:
    with connect() as conn:
        cur = conn.execute("INSERT INTO fk_runs (started_at, kind, status) VALUES (?, ?, 'running')", (now, kind))
        return int(cur.lastrowid)


def _finish_run(run_id: int, result: Dict[str, Any], now: float) -> None:
    with connect() as conn:
        conn.execute('UPDATE fk_runs SET finished_at = ?, status = ?, result = ? WHERE id = ?',
                     (now, 'error' if result.get('error') else 'ok', json.dumps(result), run_id))
        conn.execute('DELETE FROM fk_runs WHERE id NOT IN (SELECT id FROM fk_runs ORDER BY id DESC LIMIT ?)',
                     (HISTORY_KEPT,))


def interrupted(now: Optional[float] = None) -> None:
    """A run that was 'running' when the app stopped is closed as interrupted (at boot)."""
    with connect() as conn:
        conn.execute("UPDATE fk_runs SET status = 'error', finished_at = ?, result = ? WHERE status = 'running'",
                     (time.time() if now is None else now, json.dumps({'error': 'Interrupted'})))


def _slim(result: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in (result or {}).items() if k != 'releases'}


def history(limit: int = 30) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute('SELECT * FROM fk_runs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    out = []
    for r in rows:
        try:
            res = json.loads(r['result'] or '{}')
        except ValueError:
            res = {}
        out.append({'id': r['id'], 'started_at': r['started_at'], 'finished_at': r['finished_at'], 'kind': r['kind'],
                    'status': r['status'], **_slim(res)})
    return out


# ------------------------------------------------------------------ release helpers

_SEP = re.compile(r'[._]+')
_REMUX = re.compile(r'\bREMUX\b')
_UHD = re.compile(r'\b(?:2160P|4K|UHD)\b')
_LOW = re.compile(r'\b(?:1080[PI]|720P|576P|480P)\b')


def is_2160p_remux(name: Any) -> bool:
    """Prod's quality test ('2160P' and 'REMUX'), on the release name: REMUX, and 2160p (or UHD/4K with no lower
    resolution named)."""
    up = _SEP.sub(' ', str(name or '')).upper()
    if not _REMUX.search(up):
        return False
    if re.search(r'\b2160P\b', up):
        return True
    return bool(_UHD.search(up)) and not _LOW.search(up)


def published(v: Any) -> Optional[float]:
    """Epoch seconds of a release's publish date (ISO 8601, RFC 2822 or epoch), else None."""
    if v in (None, ''):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) if v > 0 else None
    s = str(v).strip()
    if s.isdigit():
        return float(s) if int(s) > 0 else None
    d: Optional[datetime] = None
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        try:
            d = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            d = None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _imdb(v: Any) -> Optional[str]:
    s = str(v or '').strip().lower()
    if s.startswith('tt'):
        s = s[2:]
    return f'tt{int(s):07d}' if s.isdigit() and int(s) > 0 else None


def _year(v: Any) -> Optional[int]:
    s = str(v or '')[:4]
    return int(s) if s.isdigit() else None


# ------------------------------------------------------------------ outside calls (tests swap these)

def tracker_list() -> List[Dict[str, Any]]:
    from app import movie_scanner
    return movie_scanner.trackers()


def search(cfg: Dict[str, Any], now: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    from app import movie_scanner, tv_search
    return tv_search.query_trackers(QUERY, tv_search.AUTOMATION, None, False, now, [cfg],
                                    categories=movie_scanner.MOVIE_CATEGORIES, section=movie_scanner.SECTION)


def paced_status(st: Dict[str, Any]) -> bool:
    from app import tv_search
    return st.get('status') in (tv_search.SLOT_BUSY, 'rate')


def spacing_s() -> float:
    """The guard's spacing between automatic movie searches on one tracker (read, never changed)."""
    try:
        from app.tracker_guard import MOVIE_GUARD_LIMITS
        return float(MOVIE_GUARD_LIMITS.get('min_gap_s') or 60.0)
    except Exception:                                   # noqa: BLE001
        return 60.0


def tmdb_key() -> str:
    from app.automation import gr_select
    return gr_select.tmdb_key()


def tmdb_get(path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from app.automation import gr_select
    return gr_select.tmdb_get(path, params)


def plex_index() -> Optional[Dict[str, Any]]:
    from app.automation import gr_select
    return gr_select.plex_index()


def plex_sections() -> List[Dict[str, Any]]:
    from app.plex_service import plex_service
    return [{'id': int(s['id']), 'title': s['title']} for s in (plex_service.get_sections_lean() or [])
            if s.get('type') == 'movie' and str(s.get('id', '')).isdigit()]


def grabs_allowed() -> bool:
    from app import instance, movie_scanner
    return movie_scanner.on_test() and instance.writes_allowed(movie_scanner.JOB) and instance.native_allowed(JOB)


def grab(item: Dict[str, Any], tmdb_id: int, movie: Dict[str, Any], now: float) -> Dict[str, Any]:
    from app import movie_scanner
    return movie_scanner.grab(item, tmdb_id, ORIGIN, now, movie=movie, pending=False, scope='4k')


def note_origin(h: str, item: Dict[str, Any], c: Dict[str, Any]) -> None:
    try:
        from app import grab_origin
        grab_origin.record(ORIGIN, info_hash=h, guid=str(item.get('guid') or ''), tmdb_id=c['tmdb_id'],
                           media_type='movie', title=c['title'], evidence='4k_remux')
    except Exception:                                   # noqa: BLE001
        logger.debug('4K Remux: origin note failed', exc_info=True)


# ------------------------------------------------------------------ TMDb and Plex

class TmdbDown(Exception):
    pass


def _norm(s: Any) -> str:
    from app import tv_match
    return tv_match.normalize(str(s or ''))


def _parse(name: str) -> Tuple[str, Optional[int]]:
    from app import movie_scanner
    return movie_scanner.parse(name)


def _hit(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        return {'tmdb_id': int(r['id']), 'title': str(r.get('title') or r.get('original_title') or ''),
                'year': _year(r.get('release_date'))}
    except (KeyError, TypeError, ValueError):
        return None


def tmdb_match(name: str, year: Optional[int], imdb: Optional[str]) -> Optional[Dict[str, Any]]:
    """{tmdb_id, title, year} for a release, else None. TmdbDown when TMDb can't be asked."""
    key = tmdb_key()
    if not key:
        raise TmdbDown('no TMDb key')
    if imdb:
        body = tmdb_get(f'/find/{imdb}', {'api_key': key, 'external_source': 'imdb_id'})
        if body is None:
            raise TmdbDown('TMDb find failed')
        for r in body.get('movie_results') or []:
            h = _hit(r)
            if h:
                return h
    if not name or year is None:
        return None
    body = tmdb_get('/search/movie', {'api_key': key, 'query': name, 'include_adult': 'false'})
    if body is None:
        raise TmdbDown('TMDb search failed')
    for r in (body.get('results') or [])[:20]:
        ry = _year(r.get('release_date'))
        if ry is None or abs(ry - year) > 1:
            continue
        if name in {_norm(r.get('title')), _norm(r.get('original_title'))}:
            return _hit(r)
    return None


def _cached_match(cache: Dict[str, Any], name: str, year: Optional[int], imdb: Optional[str],
                  now: float) -> Optional[Dict[str, Any]]:
    k = f'{imdb or ""}|{name}|{year or ""}'
    c = cache.get(k)
    if isinstance(c, dict) and now - float(c.get('ts') or 0) < (TMDB_TTL_S if c.get('tmdb_id') else TMDB_MISS_TTL_S):
        return {kk: c[kk] for kk in ('tmdb_id', 'title', 'year')} if c.get('tmdb_id') else None
    hit = tmdb_match(name, year, imdb)
    cache[k] = {**(hit or {'tmdb_id': None}), 'ts': now}
    return hit


def plex_4k(section_id: int) -> Optional[Tuple[Set[int], Set[Tuple[str, Optional[int]]]]]:
    """(tmdb ids, (normalized title, year)) in the Plex library *section_id*; None when it can't be read or reads
    empty (prod: the cycle refuses then, never grabs blind)."""
    if not section_id:
        return None
    try:
        idx = plex_index() or {}
    except Exception:                                   # noqa: BLE001
        logger.warning('4K Remux: Plex index failed', exc_info=True)
        return None
    ids: Set[int] = set()
    names: Set[Tuple[str, Optional[int]]] = set()
    for entries in (idx.get('movies_by_title') or {}).values():
        for e in entries or []:
            if not isinstance(e, dict) or str(e.get('library_section_id')) != str(section_id):
                continue
            if str(e.get('tmdb_id') or '').isdigit():
                ids.add(int(e['tmdb_id']))
            names.add((_norm(e.get('title')), _year(e.get('year'))))
    return (ids, names) if names else None


def _owned(owned: Tuple[Set[int], Set[Tuple[str, Optional[int]]]], c: Dict[str, Any]) -> bool:
    ids, names = owned
    if c['tmdb_id'] in ids:
        return True
    n, y = _norm(c['title']), c.get('year')
    return any(nn == n and (y is None or yy is None or abs(yy - y) <= 1) for nn, yy in names)


# ------------------------------------------------------------------ the cycle

def running() -> bool:
    return _run['running']


def _empty(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {'checked': 0, 'remux': 0, 'candidates': 0, 'grabbed': 0, 'would_grab': 0, 'grabbed_movies': [],
            'skipped_in_4k': 0, 'skipped_in_4k_titles': [], 'rejected': 0, 'rejected_details': [],
            'trackers': [], 'releases': [], 'error': None, 'dry': False,
            'min_seeders': cfg['min_seeders'], 'max_age_hours': cfg['max_age_hours'],
            'section_4k_id': cfg['plex_4k_section_id']}


def _search_all(trackers: List[Dict[str, Any]], res: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One '2160p REMUX' search per tracker. A tracker the guard paces is waited for only when its slot opens within
    the guard's spacing (+5 s); else it is skipped this cycle with the guard's status (the guard is never bent)."""
    items: List[Dict[str, Any]] = []
    gap = spacing_s()
    for cfg in trackers:
        st: Dict[str, Any] = {'status': 'none'}
        got: List[Dict[str, Any]] = []
        for attempt in range(PACE_TRIES + 1):
            got, sts = search(cfg, time.time())
            st = sts[0] if sts else {'status': 'none'}
            if st.get('status') == 'ok' or not paced_status(st) or attempt == PACE_TRIES:
                break
            wait = float(st['reset_at']) - time.time() if st.get('reset_at') else None
            if wait is None or wait > gap + PACE_SLACK_S:
                break
            _sleep['fn'](max(1.0, wait))
        ok = st.get('status') == 'ok'
        row = {'name': cfg.get('name'), 'status': st.get('status'), 'results': len(got) if ok else 0}
        if not ok and st.get('reset_at'):
            row['reset_at'] = st['reset_at']
        res['trackers'].append(row)
        if ok:
            items += got
    return items


def _pub(it: Dict[str, Any]) -> Optional[float]:
    """The tracker's publish date, else its estimate (IPT shows only "1.6 hours ago")."""
    return published(it.get('publishDate')) or published(it.get('publishEstimate'))


def _rec(it: Dict[str, Any], now: float) -> Dict[str, Any]:
    pub = _pub(it)
    return {'release': str(it.get('title') or ''), 'tracker': it.get('indexer'), 'seeders': int(it.get('seeders') or 0),
            'size': int(it.get('size') or 0) or None, 'age_hours': round((now - pub) / 3600, 1) if pub else None,
            'guid': str(it.get('guid') or ''), 'decision': None, 'reason': None}


def _decide(rec: Dict[str, Any], decision: str, detail: Optional[str] = None) -> None:
    rec['decision'] = decision
    rec['reason'] = detail or REASONS.get(decision, decision)


def _cycle(cfg: Dict[str, Any], now: float, dry: bool, run_id: Optional[int]) -> Dict[str, Any]:
    from app import movie_dupes
    res = _empty(cfg)
    res['dry'] = dry
    if not dry and not grabs_allowed():
        res['error'] = 'Grabs are off on this install'
        return res
    trackers = tracker_list()
    if not trackers:
        res['error'] = 'No movie trackers are on'
        return res
    items = _search_all(trackers, res)
    if not any(t['status'] == 'ok' for t in res['trackers']):
        paced = all(paced_status(t) for t in res['trackers'])
        res['error'] = 'Tracker limits reached; the next scan tries again' if paced else "Trackers couldn't be searched"
        return res
    res['checked'] = len(items)
    tried = _tried()
    mine = grabbed_ids()
    st = state()
    cache = st.get('tmdb') if isinstance(st.get('tmdb'), dict) else {}
    recs: List[Dict[str, Any]] = []
    best: Dict[int, Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = {}
    max_age_s = cfg['max_age_hours'] * 3600
    tmdb_down = False
    seen: Set[str] = set()
    for it in sorted(items, key=lambda i: -int(i.get('seeders') or 0)):
        if not is_2160p_remux(it.get('title')):
            continue
        rec = _rec(it, now)
        key = rec['guid'] or rec['release']
        if key in seen:
            continue
        seen.add(key)
        res['remux'] += 1
        recs.append(rec)
        pub = _pub(it)
        if rec['seeders'] < cfg['min_seeders']:
            _decide(rec, 'seeders')
            continue
        if pub is None:
            _decide(rec, 'no_date')
            continue
        if now - pub > max_age_s:
            _decide(rec, 'age')
            continue
        if rec['guid'] and rec['guid'] in tried:
            _decide(rec, 'tried')
            continue
        name, year = _parse(rec['release'])
        if tmdb_down:
            _decide(rec, 'tmdb_error')
            continue
        try:
            m = _cached_match(cache, name, year, _imdb(it.get('imdbId')), now)
        except TmdbDown:
            tmdb_down = True
            _decide(rec, 'tmdb_error')
            continue
        if not m:
            _decide(rec, 'no_tmdb')
            continue
        rec.update({'tmdb_id': m['tmdb_id'], 'title': m['title'], 'year': m['year']})
        if m['tmdb_id'] in mine:
            _decide(rec, 'already_grabbed')
            continue
        if m['tmdb_id'] in best:
            _decide(rec, 'same_movie')
            continue
        best[m['tmdb_id']] = (rec, it, m)
    cache = {k: v for k, v in cache.items() if isinstance(v, dict) and now - float(v.get('ts') or 0) < TMDB_TTL_S}
    _put_state(tmdb=cache)
    res['candidates'] = len(best)
    res['releases'] = recs[:RELEASES_SHOWN]
    if not best:
        return res
    owned = plex_4k(cfg['plex_4k_section_id'])
    if owned is None:
        res['error'] = "Couldn't read the 4K Plex library" if cfg['plex_4k_section_id'] else 'Choose the 4K Plex library'
        for rec, _, _ in best.values():
            _decide(rec, 'refused', res['error'])
        return res
    todo = []
    done: List[str] = []
    for rec, it, m in best.values():
        if _owned(owned, rec):
            _decide(rec, 'in_4k')
            res['skipped_in_4k'] += 1
            res['skipped_in_4k_titles'].append(f"{rec['title']} ({rec['year']})" if rec.get('year') else rec['title'])
            if rec['guid']:
                done.append(rec['guid'])
        else:
            todo.append((rec, it, m))
    stop: Optional[str] = None
    for rec, it, m in todo:
        label = f"{rec['title']} ({rec['year']})" if rec.get('year') else rec['title']
        if dry:
            _decide(rec, 'would_grab')
            res['would_grab'] += 1
            continue
        if stop:
            _decide(rec, 'stopped', f'Not tried: {stop}')
            continue
        try:
            g = grab(it, m['tmdb_id'], {'title': m['title'], 'year': m['year']}, time.time())
        except Exception:                               # noqa: BLE001
            logger.exception('4K Remux: grab failed for %s', label)
            g = {'ok': False, 'error': 'Grab failed', 'client': True}
        if g.get('ok'):
            h = str(g.get('hash') or '').lower()
            _record_grab(h, {**rec, 'release': rec['release']}, run_id, time.time())
            note_origin(h, it, rec)
            _decide(rec, 'grabbed')
            res['grabbed'] += 1
            res['grabbed_movies'].append(label)
            done.append(rec['guid'])
            logger.info('4K Remux: grabbed %s (%s, %s seeders, %s)', label, rec['release'], rec['seeders'],
                        rec['tracker'])
            continue
        err = str(g.get('error') or 'Grab refused')
        if g.get('client') or err == movie_dupes.NO_QBIT:
            stop = "qBittorrent can't be reached"
            _decide(rec, 'stopped', f'Not tried: {stop}')
            continue
        if g.get('blocked') and not g.get('duplicate'):
            stop = 'grabs are off'
            _decide(rec, 'stopped', f'Not tried: {stop}')
            continue
        if g.get('link'):
            _decide(rec, 'link', f"Couldn't fetch the release: {err}")
            res['rejected'] += 1
            res['rejected_details'].append({'title': label, 'reason': err})
            continue
        _decide(rec, 'duplicate' if g.get('duplicate') or g.get('existing') else 'rejected', err)
        res['rejected'] += 1
        res['rejected_details'].append({'title': label, 'reason': err})
        if rec['guid']:
            done.append(rec['guid'])
    if stop:
        res['error'] = f'Grabs stopped: {stop}'
    if not dry:
        _mark_tried([g for g in done if g], now)
    return res


def run(kind: str = 'scheduled', now: Optional[float] = None) -> Dict[str, Any]:
    """One cycle. kind 'scheduled' (only while on), 'manual' (Run now: runs even when off, as prod's button did) or
    'preview' (no grabs, nothing recorded or marked tried)."""
    with _lock:
        if _run['running']:
            return {'error': 'Already running'}
        _run.update(running=True, kind=kind)
    now = time.time() if now is None else now
    cfg = settings()
    dry = kind == 'preview'
    run_id = None if dry else _start_run(kind, now)
    if dry:
        _preview.update(running=True)
    try:
        result = _cycle(cfg, now, dry, run_id)
    except Exception:                                   # noqa: BLE001
        logger.exception('4K Remux: run failed')
        result = {**_empty(cfg), 'dry': dry, 'error': 'Run failed'}
    finally:
        with _lock:
            _run.update(running=False, kind=None)
    if dry:
        _preview.update(running=False, result=result, at=now)
        return result
    _finish_run(run_id, result, time.time())
    st = state()
    upd: Dict[str, Any] = {'last_run': now, 'last_kind': kind, 'last_result': _slim(result),
                           'cycle_count': int(st.get('cycle_count') or 0) + 1}
    if kind == 'scheduled':
        upd['last_auto_at'] = now
    _put_state(**upd)
    logger.info('4K Remux: %s run: checked %s, candidates %s, grabbed %s, already 4K %s, rejected %s%s', kind,
                result['checked'], result['candidates'], result['grabbed'], result['skipped_in_4k'], result['rejected'],
                f", error: {result['error']}" if result.get('error') else '')
    return result


def preview_state() -> Dict[str, Any]:
    return {'running': bool(_preview['running']), 'at': _preview['at'], 'result': _preview['result']}


# ------------------------------------------------------------------ schedule

def next_run(cfg: Optional[Dict[str, Any]] = None, st: Optional[Dict[str, Any]] = None) -> Optional[float]:
    cfg = cfg or settings()
    if not cfg['enabled']:
        return None
    st = state() if st is None else st
    first = _boot['at'] + FIRST_RUN_S
    last = st.get('last_auto_at')
    if not last or float(last) < _boot['at']:
        return first
    return float(last) + cfg['scan_interval_minutes'] * 60


def due(now: float) -> bool:
    n = next_run()
    return n is not None and now >= n and not running()


def status() -> Dict[str, Any]:
    cfg, st = settings(), state()
    return {'running': running() and _run['kind'] != 'preview', 'enabled': bool(cfg['enabled']),
            'next_run': next_run(cfg, st), 'last_run': st.get('last_run'), 'last_kind': st.get('last_kind'),
            'last_result': st.get('last_result'), 'grabbed_total': len(grabbed_ids())}


_started = {'on': False}


def _loop() -> None:
    time.sleep(max(0.0, _boot['at'] + FIRST_RUN_S - time.time()))
    while True:
        try:
            if due(time.time()):
                run('scheduled')
        except Exception:                               # noqa: BLE001
            logger.exception('4K Remux: schedule tick failed')
        time.sleep(CHECK_S)


def start() -> bool:
    from app import movie_scanner
    if _started['on'] or not movie_scanner.on_test():   # test install only, like Genre Roulette
        return False
    _started['on'] = True
    _boot['at'] = time.time()
    try:
        interrupted()
    except Exception:                                   # noqa: BLE001
        logger.warning('4K Remux: closing interrupted runs failed', exc_info=True)
    threading.Thread(target=_loop, daemon=True, name='remux-4k').start()
    logger.info('4K Remux auto-grab scheduler started (first scan in %s s)', FIRST_RUN_S)
    return True
