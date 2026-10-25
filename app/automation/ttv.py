"""v279 Trending TV (prod 1b4ea74 app/trending_tv_watchlist.py, native): every few hours Trakt's
trending shows are added through Luma's TV Add (tv.db only; TV search grabs episodes as they air).
No Sonarr. Prod's Sonarr job (app/trending_tv_watchlist.py) is left alone for non-minimal installs.

Every value prod hard-codes is a setting here, with prod's value as the default: shows per run 10,
every 2 hours (automation.interval_hours), page 1, monitor All episodes, search on add, English
originals, unknown language allowed, Plex-owned skipped, the run skipped when Plex can't be read,
shows without a TVDB ID skipped, 1-season-only filter on, a busy Trakt skips the run, 200 history
entries. On by default (Taylor: trending TV working on test). Run now ignores the switch. Shows
already in tv.db are always skipped. Own enabled gate only — not automation.auto_grab (Anticipated
parity on MINIMAL=1).

Store: automation.db (Genre Roulette file): meta 'ttv_settings' / 'ttv_state', ttv_log, ttv_seen.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

TRAKT_URL = 'https://api.trakt.tv/shows/trending'
TRAKT_TIMEOUT_S = 15
LANG_WORKERS = 8
FIRST_RUN_S = 75
CHECK_S = 30
BUSY_RETRY_S = 15 * 60
LANGUAGES = ('en', 'fr', 'es', 'de', 'it', 'ja', 'ko', 'zh', 'hi', 'sv', 'da', 'no', 'pt', 'ru')
BUSY = ('skip', 'retry')
HISTORY_KEPT = (50, 200, 500)
MONITOR = ('all', 'future', 'missing', 'first_season', 'last_season', 'none')
SKIPS = ('existing', 'plex', 'language', 'no_tvdb', 'no_tmdb', 'removed', 'seasons')

_lock = threading.RLock()
_run = {'running': False}
_boot = {'at': time.time()}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ttv_log (
  id INTEGER PRIMARY KEY, tmdb_id INTEGER NOT NULL, tvdb_id INTEGER, title TEXT NOT NULL, year INTEGER,
  added_at REAL NOT NULL, series_id INTEGER
);
CREATE TABLE IF NOT EXISTS ttv_seen (
  tmdb_id INTEGER NOT NULL PRIMARY KEY, tvdb_id INTEGER, first_added_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ttv_seen_tvdb ON ttv_seen (tvdb_id);
"""


# ------------------------------------------------------------------ store

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


def defaults() -> Dict[str, Any]:
    return {
        'enabled': True, 'limit': 10, 'profile_id': None, 'search_on_add': True, 'languages': ['en'],
        'allow_unknown_language': True, 'skip_plex': True, 'skip_if_plex_unreadable': True,
        'no_readd': True, 'page': 1, 'path_id': None, 'monitor': 'all', 'require_tvdb': True,
        'one_season_only': True, 'interval_hours': 2, 'when_busy': 'skip', 'history_kept': 200,
    }


class Invalid(Exception):
    def __init__(self, errors: Dict[str, str]):
        super().__init__('invalid')
        self.errors = errors


def _int(v: Any, lo: int, hi: int) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
        return None
    v = int(v)
    return v if lo <= v <= hi else None


def _bool(v: Any) -> Optional[bool]:
    return v if isinstance(v, bool) else None


def _langs(v: Any) -> Optional[List[str]]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x in LANGUAGES for x in v):
        return None
    return [x for x in LANGUAGES if x in v]


def _opt_id(v: Any) -> Tuple[bool, Any]:
    return (v is None or _int(v, 1, 10 ** 9) is not None), v


def _opt_path(v: Any) -> Tuple[bool, Any]:
    return (v is None or (isinstance(v, str) and 0 < len(v) <= 64)), v


def _rule(fn):
    def wrap(v: Any) -> Tuple[bool, Any]:
        got = fn(v)
        return got is not None, got
    return wrap


RULES = {
    'enabled': _rule(_bool), 'search_on_add': _rule(_bool), 'allow_unknown_language': _rule(_bool),
    'skip_plex': _rule(_bool), 'skip_if_plex_unreadable': _rule(_bool), 'no_readd': _rule(_bool),
    'require_tvdb': _rule(_bool), 'one_season_only': _rule(_bool),
    'limit': _rule(lambda v: _int(v, 1, 40)), 'page': _rule(lambda v: _int(v, 1, 100)),
    'monitor': _rule(lambda v: v if v in MONITOR else None), 'languages': _rule(_langs),
    'profile_id': _opt_id, 'path_id': _opt_path,
    'interval_hours': _rule(lambda v: _int(v, 1, 24)),
    'when_busy': _rule(lambda v: v if v in BUSY else None),
    'history_kept': _rule(lambda v: v if not isinstance(v, bool) and v in HISTORY_KEPT else None),
}


def check(body: Any, cur: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise Invalid({'_': 'Invalid'})
    out = dict(cur)
    errors: Dict[str, str] = {}
    for k, v in body.items():
        rule = RULES.get(k) if k in out else None
        if rule is None:
            errors[k] = 'Unknown'
            continue
        ok, got = rule(v)
        if ok:
            out[k] = got
        else:
            errors[k] = 'Invalid'
    if errors:
        raise Invalid(errors)
    return out


def _legacy() -> Dict[str, Any]:
    """Prod's automation.trending_tv_watchlist keys, when this install has them (first load only)."""
    try:
        from app.settings_manager import SettingsFile
        s = SettingsFile()
        get = lambda k: s.get(k, None)              # noqa: E731
    except Exception:                               # noqa: BLE001
        return {}
    base = 'automation.trending_tv_watchlist.'
    out = defaults()
    tries = [
        {'interval_hours': get('automation.interval_hours')},
        {'limit': get(base + 'limit')},
        {'search_on_add': get(base + 'search_on_add')},
    ]
    langs = get(base + 'allowed_languages')
    if isinstance(langs, list):
        tries.append({'languages': [str(x).lower() for x in langs]})
    # Prod default enabled=false; native defaults True — only import limit/langs/search/interval.
    for t in tries:
        if any(v is None for v in t.values()):
            continue
        try:
            out = check(t, out)
        except Invalid:
            continue
    return out


def _fill(cur: Any) -> Dict[str, Any]:
    d = defaults()
    cur = cur if isinstance(cur, dict) else {}
    return {k: cur.get(k, v) for k, v in d.items()}


def settings() -> Dict[str, Any]:
    with connect() as conn:
        cur = _meta(conn, 'ttv_settings', None)
    if cur is None:
        cur = _legacy()
        # First load: native defaults (enabled True). Legacy only fills limit/langs/etc.
    return _fill(cur)


def save(body: Any) -> Dict[str, Any]:
    out = check(body, settings())
    with connect() as conn:
        _set_meta(conn, 'ttv_settings', out)
    return out


def state() -> Dict[str, Any]:
    with connect() as conn:
        return _meta(conn, 'ttv_state', {}) or {}


def _put_state(**kw: Any) -> None:
    with connect() as conn:
        st = _meta(conn, 'ttv_state', {}) or {}
        st.update(kw)
        _set_meta(conn, 'ttv_state', st)


def history(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    n = limit or settings()['history_kept']
    with connect() as conn:
        rows = conn.execute('SELECT * FROM ttv_log ORDER BY added_at DESC, id DESC LIMIT ?', (n,)).fetchall()
    return [{k: r[k] for k in ('id', 'tmdb_id', 'tvdb_id', 'title', 'year', 'added_at')} for r in rows]


def _record_add(e: Dict[str, Any], now: float, ref: Optional[int], keep: int) -> None:
    with connect() as conn:
        conn.execute('INSERT INTO ttv_log (tmdb_id, tvdb_id, title, year, added_at, series_id)'
                     ' VALUES (?, ?, ?, ?, ?, ?)', (e['tmdb'], e['tvdb'], e['title'], e['year'], now, ref))
        conn.execute('INSERT INTO ttv_seen (tmdb_id, tvdb_id, first_added_at) VALUES (?, ?, ?)'
                     ' ON CONFLICT(tmdb_id) DO UPDATE SET tvdb_id = COALESCE(ttv_seen.tvdb_id, excluded.tvdb_id)',
                     (e['tmdb'], e['tvdb'], now))
        conn.execute('DELETE FROM ttv_log WHERE id NOT IN (SELECT id FROM ttv_log ORDER BY added_at DESC, id DESC'
                     ' LIMIT ?)', (keep,))


def seen(tmdb: Optional[int], tvdb: Optional[int]) -> bool:
    with connect() as conn:
        return conn.execute('SELECT 1 FROM ttv_seen WHERE tmdb_id = ? OR (tvdb_id IS NOT NULL AND tvdb_id = ?)',
                            (tmdb, tvdb)).fetchone() is not None


# ------------------------------------------------------------------ outside world (tests replace these)

class Busy(Exception):
    pass


def trakt_client_id() -> str:
    from app import connectors
    try:
        return connectors._stored_trakt()
    except Exception:                               # noqa: BLE001
        return ''


def fetch_trakt(client_id: str, page: int, limit: int) -> List[Dict[str, Any]]:
    """Trakt trending shows (extended=full). Raises Busy on 429, RuntimeError on any other failure."""
    import requests
    from app import trakt_http
    try:
        r = trakt_http.get(TRAKT_URL, client_id,
                           params={'page': str(page), 'limit': str(limit), 'extended': 'full'},
                           timeout=TRAKT_TIMEOUT_S)
    except requests.RequestException as e:
        raise RuntimeError(type(e).__name__) from None
    if r.status_code == 429:
        raise Busy()
    if r.status_code != 200:
        raise RuntimeError(f'HTTP {r.status_code}')
    data = r.json()
    return data if isinstance(data, list) else []


def show_details(tmdb_id: int) -> Dict[str, Any]:
    """{'language': TMDB original language or '', 'seasons': number_of_seasons or -1}."""
    from app import tv_tmdb
    try:
        d = tv_tmdb.show(tmdb_id)
    except Exception:                               # noqa: BLE001
        return {'language': '', 'seasons': -1}
    if not d:
        return {'language': '', 'seasons': -1}
    seasons = d.get('number_of_seasons')
    n = seasons if isinstance(seasons, int) and not isinstance(seasons, bool) else -1
    return {'language': str(d.get('original_language') or '').lower(), 'seasons': n}


def plex_index() -> Dict[str, Any]:
    from app.plex_service import plex_service
    return plex_service.build_ownership_index()


def in_tv_db(tmdb: Optional[int], tvdb: Optional[int]) -> bool:
    from app import tv_store
    with tv_store.connect() as conn:
        return conn.execute('SELECT 1 FROM series WHERE tmdb_id = ? OR (tvdb_id IS NOT NULL AND tvdb_id = ?)',
                            (tmdb, tvdb)).fetchone() is not None


def libraries() -> List[Dict[str, Any]]:
    from app import tv_settings
    return [{'id': p['id'], 'name': p['name']} for p in tv_settings.libraries()]


def tv_profiles() -> List[Dict[str, Any]]:
    from app import tv_monitor
    return [{'id': p['id'], 'name': p['name'], 'default': p['default']} for p in tv_monitor.profiles()]


def tv_add(tmdb_id: int, path_id: str, monitor: str, profile_id: Optional[int], search: bool) -> Dict[str, Any]:
    from app import tv_calendar
    return tv_calendar.add(tmdb_id, path_id, monitor=monitor, profile_id=profile_id, search=search)


# ------------------------------------------------------------------ engine

def _ids(entry: Dict[str, Any]) -> Dict[str, Any]:
    obj = entry.get('show') if isinstance(entry, dict) else None
    obj = obj if isinstance(obj, dict) else {}
    ids = obj.get('ids') if isinstance(obj.get('ids'), dict) else {}
    num = lambda v: v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None    # noqa: E731
    year = obj.get('year')
    return {'tmdb': num(ids.get('tmdb')), 'tvdb': num(ids.get('tvdb')), 'title': str(obj.get('title') or 'Unknown'),
            'year': year if isinstance(year, int) and not isinstance(year, bool) else None}


def _has(m: Any, k: Any) -> bool:
    return bool(k) and isinstance(m, dict) and (k in m or str(k) in m)


def _owned(index: Dict[str, Any], e: Dict[str, Any]) -> bool:
    if _has(index.get('tv_by_tmdb'), e['tmdb']) or _has(index.get('tv_by_tvdb'), e['tvdb']):
        return True
    key = f"{e['title'].lower()}|{e['year']}" if e['title'] and e['year'] else None
    return _has(index.get('tv'), key)


def running() -> bool:
    return _run['running']


def _empty() -> Dict[str, Any]:
    return {'checked': 0, 'added': 0, 'skipped': {k: 0 for k in SKIPS}, 'errors': [], 'error': None}


def run(kind: str = 'scheduled', now: Optional[float] = None) -> Dict[str, Any]:
    """One cycle. kind 'manual' (Run now) runs even when switched off."""
    with _lock:
        if _run['running']:
            return {'error': 'Already running'}
        _run['running'] = True
    now = time.time() if now is None else now
    try:
        result = _cycle(settings(), now, force=kind == 'manual')
    except Exception:                               # noqa: BLE001
        logger.exception('Trending TV: run failed')
        result = {**_empty(), 'error': 'Run failed'}
    finally:
        with _lock:
            _run['running'] = False
    st = state()
    retry = result.pop('_retry_at', None)
    upd: Dict[str, Any] = {'last_run': now, 'last_result': result, 'last_kind': kind, 'retry_at': retry,
                           'cycle_count': int(st.get('cycle_count') or 0) + 1}
    if kind == 'scheduled':
        upd['last_auto_at'] = now
    _put_state(**upd)
    logger.info('Trending TV: %s', json.dumps(result)[:500])
    return result


def _cycle(cfg: Dict[str, Any], now: float, force: bool = False) -> Dict[str, Any]:
    if not force and not cfg['enabled']:
        return _empty()
    cid = trakt_client_id()
    if not cid:
        return {**_empty(), 'error': 'Trakt not connected'}
    index: Optional[Dict[str, Any]] = None
    readable = False
    if cfg['skip_plex'] or cfg['skip_if_plex_unreadable']:
        try:
            index = plex_index() or {}
            readable = bool(index.get('tv') or index.get('tv_by_tmdb') or index.get('tv_by_tvdb'))
        except Exception as e:                      # noqa: BLE001
            logger.warning('Trending TV: Plex unreadable (%s)', type(e).__name__)
            index = {}
    if index is not None and not readable and cfg['skip_if_plex_unreadable']:
        return {**_empty(), 'error': "Plex can't be read"}
    try:
        return _group_run(cfg, index or {}, cid, now)
    except Busy:
        out = {**_empty(), 'error': 'Trakt is busy'}
        if cfg['when_busy'] == 'retry':
            out['_retry_at'] = now + BUSY_RETRY_S
        return out


def _group_run(g: Dict[str, Any], index: Dict[str, Any], cid: str, now: float) -> Dict[str, Any]:
    res = _empty()
    libs = libraries()
    if not libs:
        return {**res, 'error': 'No TV library'}
    path_id = g['path_id'] if any(l['id'] == g['path_id'] for l in libs) else libs[0]['id']
    try:
        raw = fetch_trakt(cid, g['page'], g['limit'])
    except Busy:
        raise
    except Exception as e:                          # noqa: BLE001
        return {**res, 'error': f'Trakt failed ({e})'}
    entries = [_ids(x) for x in raw[:g['limit']]]
    langs = g['languages']
    need_meta = bool(langs) or g['one_season_only']
    details: Dict[int, Dict[str, Any]] = {}
    if need_meta:
        tids = [e['tmdb'] for e in entries if e['tmdb']]
        with ThreadPoolExecutor(max_workers=LANG_WORKERS) as pool:
            details = dict(zip(tids, pool.map(show_details, tids)))
    for e in entries:
        res['checked'] += 1
        meta = details.get(e['tmdb']) or {} if e['tmdb'] else {}
        lang = meta.get('language', '')
        seasons = meta.get('seasons', -1)
        why = None
        if not e['tmdb']:
            why = 'no_tmdb'
        elif g['require_tvdb'] and not e['tvdb']:
            why = 'no_tvdb'
        elif langs and (lang not in langs if lang else not g['allow_unknown_language']):
            why = 'language'
        elif g['one_season_only'] and seasons != 1:
            why = 'seasons'
        elif g['skip_plex'] and _owned(index, e):
            why = 'plex'
        elif in_tv_db(e['tmdb'], e['tvdb']):
            why = 'existing'
        elif g['no_readd'] and seen(e['tmdb'], e['tvdb']):
            why = 'removed'
        if why:
            res['skipped'][why] += 1
            continue
        try:
            r = tv_add(e['tmdb'], path_id, g['monitor'], g['profile_id'], g['search_on_add'])
            ref = r.get('series_id')
        except Exception as ex:                     # noqa: BLE001
            res['errors'].append(f"{e['title']}: {type(ex).__name__}")
            continue
        if r.get('ok'):
            _record_add(e, now, ref, g['history_kept'])
            res['added'] += 1
            logger.info('Trending TV: added %s (%s)', e['title'], e['year'])
        elif r.get('status') == 409:
            res['skipped']['existing'] += 1
        else:
            res['errors'].append(f"{e['title']}: {r.get('error') or 'Add failed'}")
    return res


# ------------------------------------------------------------------ schedule

def next_run(cfg: Optional[Dict[str, Any]] = None, st: Optional[Dict[str, Any]] = None) -> Optional[float]:
    cfg = cfg or settings()
    if not cfg['enabled']:
        return None
    st = state() if st is None else st
    last = st.get('last_auto_at')
    base = float(last) + cfg['interval_hours'] * 3600 if last else _boot['at'] + FIRST_RUN_S
    retry = st.get('retry_at')
    return min(base, float(retry)) if retry else base


def due(now: float) -> bool:
    n = next_run()
    return n is not None and now >= n and not running()


def status() -> Dict[str, Any]:
    cfg, st = settings(), state()
    return {'running': running(), 'enabled': bool(cfg['enabled']), 'next_run': next_run(cfg, st),
            'last_run': st.get('last_run'), 'last_kind': st.get('last_kind'), 'last_result': st.get('last_result')}


_started = {'on': False}


def _loop() -> None:
    time.sleep(FIRST_RUN_S)
    while True:
        try:
            if due(time.time()):
                run('scheduled')
        except Exception:                           # noqa: BLE001
            logger.exception('Trending TV: schedule tick failed')
        time.sleep(CHECK_S)


def start() -> bool:
    from app import movie_scanner
    if _started['on'] or not movie_scanner.on_test():     # test install only, like Anticipated
        return False
    _started['on'] = True
    _boot['at'] = time.time()
    threading.Thread(target=_loop, daemon=True, name='trending-tv').start()
    logger.info('Trending TV scheduler started')
    return True
