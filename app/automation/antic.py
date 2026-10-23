"""v253 Anticipated (prod 1b4ea74 app/anticipated_watchlist.py, native): every few hours the top anticipated shows
and movies on Trakt are added natively, with no Sonarr or Radarr: shows through Luma's TV Add (tv.db only; TV search
grabs episodes as they air), movies onto the native movie watchlist (movies.db; the movie scanner searches and grabs
once it is available under Settings > Movies availability (v256), by search or feed, like any watchlist movie).

Every value prod hard-codes is a setting here, with prod's value as the default: shows per run 20 (:246), every
2 hours (:33, :160), page 1 (:213), monitor All episodes (:379), search on add (:249), English originals (:250),
unknown language allowed (:367), Plex-owned skipped (:370), the run skipped when Plex can't be read (:259-272),
shows without a TVDB ID skipped (:365), a busy Trakt skips the run
(:216-218), 200 history entries (:35). One PM ruling departs from prod (v259): Don't re-add removed is on by
default for shows and movies (prod re-added them, with no memory); a saved choice is kept, and settings saved
before the key existed read it as on. Movies: no language gate in prod (only shows had one), so none = any.
Off on a new install (settings_manager.py:379). Run now ignores the switches (:136-139). Shows already in tv.db
and movies already on the watchlist or with a grab are always skipped (prod: already in Sonarr/Radarr, :308, :373).

Store: automation.db (the Genre Roulette file): meta 'antic_settings' / 'antic_state', antic_log (the history,
trimmed to History kept) and antic_seen (every show this ever added, by TMDB/TVDB id, never trimmed: Don't
re-add removed shows reads it).
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

TRAKT_URL = 'https://api.trakt.tv/{media}/anticipated'
TRAKT_TIMEOUT_S = 15
LANG_WORKERS = 8
FIRST_RUN_S = 75
CHECK_S = 30
BUSY_RETRY_S = 15 * 60
LANGUAGES = ('en', 'fr', 'es', 'de', 'it', 'ja', 'ko', 'zh', 'hi', 'sv', 'da', 'no', 'pt', 'ru')
BUSY = ('skip', 'retry')
HISTORY_KEPT = (50, 200, 500)
MONITOR = ('all', 'future', 'missing', 'first_season', 'last_season', 'none')     # the TV Add's options
SKIPS = ('existing', 'plex', 'language', 'no_tvdb', 'no_tmdb', 'removed', 'grabbed')
KINDS = ('shows', 'movies')

_lock = threading.RLock()
_run = {'running': False}
_boot = {'at': time.time()}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS antic_log (
  id INTEGER PRIMARY KEY, tmdb_id INTEGER NOT NULL, tvdb_id INTEGER, title TEXT NOT NULL, year INTEGER,
  added_at REAL NOT NULL, series_id INTEGER, kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS antic_seen (
  kind TEXT NOT NULL, tmdb_id INTEGER NOT NULL, tvdb_id INTEGER, first_added_at REAL NOT NULL, PRIMARY KEY (kind, tmdb_id)
);
CREATE INDEX IF NOT EXISTS antic_seen_tvdb ON antic_seen (kind, tvdb_id);
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


def _group(kind: str) -> Dict[str, Any]:
    g = {'enabled': False, 'limit': 20, 'profile_id': None, 'search_on_add': True,
         'languages': ['en'] if kind == 'shows' else [], 'allow_unknown_language': True, 'skip_plex': True,
         'skip_if_plex_unreadable': True, 'no_readd': True, 'page': 1}
    if kind == 'shows':
        g.update(path_id=None, monitor='all', require_tvdb=True)
    return g


def defaults() -> Dict[str, Any]:
    return {'shows': _group('shows'), 'movies': _group('movies'), 'interval_hours': 2, 'when_busy': 'skip',
            'history_kept': 200}


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


GROUP_RULES = {
    'enabled': _rule(_bool), 'search_on_add': _rule(_bool), 'allow_unknown_language': _rule(_bool),
    'skip_plex': _rule(_bool), 'skip_if_plex_unreadable': _rule(_bool), 'no_readd': _rule(_bool),
    'require_tvdb': _rule(_bool), 'limit': _rule(lambda v: _int(v, 1, 40)), 'page': _rule(lambda v: _int(v, 1, 100)),
    'monitor': _rule(lambda v: v if v in MONITOR else None), 'languages': _rule(_langs),
    'profile_id': _opt_id, 'path_id': _opt_path,
}
SHARED_RULES = {
    'interval_hours': _rule(lambda v: _int(v, 1, 24)), 'when_busy': _rule(lambda v: v if v in BUSY else None),
    'history_kept': _rule(lambda v: v if not isinstance(v, bool) and v in HISTORY_KEPT else None),
}


def check(body: Any, cur: Dict[str, Any]) -> Dict[str, Any]:
    """The settings after applying *body* (partial: {'shows': {...}, 'movies': {...}, shared keys}). Raises Invalid."""
    if not isinstance(body, dict):
        raise Invalid({'_': 'Invalid'})
    out = {**cur, 'shows': dict(cur['shows']), 'movies': dict(cur['movies'])}
    errors: Dict[str, str] = {}
    for k, v in body.items():
        if k in KINDS:
            if not isinstance(v, dict):
                errors[k] = 'Invalid'
                continue
            for gk, gv in v.items():
                rule = GROUP_RULES.get(gk) if gk in out[k] else None
                if rule is None:
                    errors[f'{k}.{gk}'] = 'Unknown'
                    continue
                ok, got = rule(gv)
                if ok:
                    out[k][gk] = got
                else:
                    errors[f'{k}.{gk}'] = 'Invalid'
        elif k in SHARED_RULES:
            ok, got = SHARED_RULES[k](v)
            if ok:
                out[k] = got
            else:
                errors[k] = 'Invalid'
        else:
            errors[k] = 'Unknown'
    if errors:
        raise Invalid(errors)
    return out


def _legacy() -> Dict[str, Any]:
    """Prod's automation.anticipated_watchlist keys, when this install has them (first load only; imported off)."""
    try:
        from app.settings_manager import SettingsFile      # v259: read as on disk; no rewrite of settings.json
        s = SettingsFile()
        get = lambda k: s.get(k, None)              # noqa: E731
    except Exception:                               # noqa: BLE001
        return {}
    base = 'automation.anticipated_watchlist.'
    langs = get(base + 'tv_allowed_languages')
    tries = [{'interval_hours': get('automation.interval_hours')}]
    for kind in KINDS:
        tries += [{kind: {'limit': get(base + 'limit')}}, {kind: {'search_on_add': get(base + 'search_on_add')}}]
    if isinstance(langs, list):
        tries.append({'shows': {'languages': [str(x).lower() for x in langs]}})
    out = defaults()
    for t in tries:
        if any(v is None or (isinstance(v, dict) and None in v.values()) for v in t.values()):
            continue
        try:
            out = check(t, out)
        except Invalid:
            continue
    return out


def _fill(cur: Any) -> Dict[str, Any]:
    d = defaults()
    cur = cur if isinstance(cur, dict) else {}
    out = {k: cur.get(k, d[k]) for k in SHARED_RULES}
    for kind in KINDS:
        g = cur.get(kind) if isinstance(cur.get(kind), dict) else {}
        out[kind] = {k: g.get(k, v) for k, v in d[kind].items()}
    return out


EXPLICIT = ('no_readd',)       # stored only once the owner sets it, so an unset one follows the default (v259: on)


def _stored(out: Dict[str, Any], raw: Any, body: Any) -> Dict[str, Any]:
    """*out* as stored: an EXPLICIT key is kept only when it was saved before or *body* sets it."""
    raw = raw if isinstance(raw, dict) else {}
    body = body if isinstance(body, dict) else {}
    res = {**out}
    for kind in KINDS:
        was = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
        now = body.get(kind) if isinstance(body.get(kind), dict) else {}
        res[kind] = {k: v for k, v in out[kind].items() if k not in EXPLICIT or k in was or k in now}
    return res


def settings() -> Dict[str, Any]:
    with connect() as conn:
        cur = _meta(conn, 'antic_settings', None)
        if cur is None:
            cur = _legacy()
            for kind in KINDS:
                cur[kind]['enabled'] = False
            cur = _stored(cur, None, None)
            _set_meta(conn, 'antic_settings', cur)
    return _fill(cur)


def save(body: Any) -> Dict[str, Any]:
    out = check(body, settings())
    with connect() as conn:
        _set_meta(conn, 'antic_settings', _stored(out, _meta(conn, 'antic_settings', None), body))
    return out


def state() -> Dict[str, Any]:
    with connect() as conn:
        return _meta(conn, 'antic_state', {}) or {}


def _put_state(**kw: Any) -> None:
    with connect() as conn:
        st = _meta(conn, 'antic_state', {}) or {}
        st.update(kw)
        _set_meta(conn, 'antic_state', st)


def history(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    n = limit or settings()['history_kept']
    with connect() as conn:
        rows = conn.execute('SELECT * FROM antic_log ORDER BY added_at DESC, id DESC LIMIT ?', (n,)).fetchall()
    return [{k: r[k] for k in ('id', 'kind', 'tmdb_id', 'tvdb_id', 'title', 'year', 'added_at')} for r in rows]


def _record_add(kind: str, e: Dict[str, Any], now: float, ref: Optional[int], keep: int) -> None:
    with connect() as conn:
        conn.execute('INSERT INTO antic_log (tmdb_id, tvdb_id, title, year, added_at, series_id, kind)'
                     ' VALUES (?, ?, ?, ?, ?, ?, ?)', (e['tmdb'], e['tvdb'], e['title'], e['year'], now, ref, kind))
        conn.execute('INSERT INTO antic_seen (kind, tmdb_id, tvdb_id, first_added_at) VALUES (?, ?, ?, ?)'
                     ' ON CONFLICT(kind, tmdb_id) DO UPDATE SET tvdb_id = COALESCE(antic_seen.tvdb_id, excluded.tvdb_id)',
                     (kind, e['tmdb'], e['tvdb'], now))
        conn.execute('DELETE FROM antic_log WHERE id NOT IN (SELECT id FROM antic_log ORDER BY added_at DESC, id DESC'
                     ' LIMIT ?)', (keep,))


def seen(kind: str, tmdb: Optional[int], tvdb: Optional[int]) -> bool:
    """Ever added by Anticipated (Don't re-add removed shows/movies)."""
    with connect() as conn:
        return conn.execute('SELECT 1 FROM antic_seen WHERE kind = ? AND (tmdb_id = ? OR (tvdb_id IS NOT NULL AND'
                            ' tvdb_id = ?))', (kind, tmdb, tvdb)).fetchone() is not None


# ------------------------------------------------------------------ outside world (tests replace these)

class Busy(Exception):
    pass


def trakt_client_id() -> str:
    from app import connectors
    try:
        return connectors._stored_trakt()
    except Exception:                               # noqa: BLE001
        return ''


def fetch_trakt(media: str, client_id: str, page: int, limit: int) -> List[Dict[str, Any]]:
    """Trakt's anticipated shows or movies (extended=full). Raises Busy on 429, RuntimeError on any other failure."""
    import requests
    from app import trakt_http                      # v255: Trakt's headers + User-Agent, a WARNING on any failure
    try:
        r = trakt_http.get(TRAKT_URL.format(media=media), client_id,
                           params={'page': str(page), 'limit': str(limit), 'extended': 'full'}, timeout=TRAKT_TIMEOUT_S)
    except requests.RequestException as e:
        raise RuntimeError(type(e).__name__) from None
    if r.status_code == 429:
        raise Busy()
    if r.status_code != 200:
        raise RuntimeError(f'HTTP {r.status_code}')
    data = r.json()
    return data if isinstance(data, list) else []


def show_details(tmdb_id: int) -> Dict[str, Any]:
    """{'language': TMDB original language or ''}."""
    from app import tv_tmdb
    try:
        d = tv_tmdb.show(tmdb_id)
    except Exception:                               # noqa: BLE001
        return {'language': ''}
    return {'language': str((d or {}).get('original_language') or '').lower()}


def movie_details(tmdb_id: int) -> Dict[str, Any]:
    """{'language', 'available_at'} from TMDB: the movie scanner's lookup, and when the movie may be searched under
    Settings > Movies availability (v256 movie_scanner.available_at: announced, in cinemas or released); '' / None
    when unknown."""
    from app import movie_scanner
    try:
        d = movie_scanner._tmdb(tmdb_id)
    except Exception:                               # noqa: BLE001
        d = None
    try:
        at = movie_scanner.available_at(movie_scanner.dates_of(d))
    except Exception:                               # noqa: BLE001
        at = None
    return {'language': str((d or {}).get('original_language') or '').lower(), 'available_at': at}


def plex_index() -> Dict[str, Any]:
    from app.plex_service import plex_service
    return plex_service.build_ownership_index()


def in_tv_db(tmdb: Optional[int], tvdb: Optional[int]) -> bool:
    from app import tv_store
    with tv_store.connect() as conn:
        return conn.execute('SELECT 1 FROM series WHERE tmdb_id = ? OR (tvdb_id IS NOT NULL AND tvdb_id = ?)',
                            (tmdb, tvdb)).fetchone() is not None


def on_watchlist(tmdb: int) -> bool:
    from app import movie_watchlist
    return tmdb in set(movie_watchlist.tmdb_ids())


def movie_grabbed(tmdb: int) -> bool:
    """An in-flight or finished grab: the movie scanner's (movie_grabs) or Genre Roulette's (gr_grabs)."""
    from app import movie_scanner, movie_settings as ms
    with ms.connect() as conn:
        movie_scanner.ensure(conn)
        if conn.execute(f"SELECT 1 FROM movie_grabs WHERE tmdb_id = ? AND state IN ({','.join('?' * len(movie_scanner.ACTIVE))})",
                        (tmdb, *movie_scanner.ACTIVE)).fetchone():
            return True
    from app.automation import gr_store
    with gr_store.connect() as conn:
        return conn.execute('SELECT 1 FROM gr_grabs WHERE tmdb_id = ?', (tmdb,)).fetchone() is not None


def libraries() -> List[Dict[str, Any]]:
    from app import tv_settings
    return [{'id': p['id'], 'name': p['name']} for p in tv_settings.libraries()]


def tv_profiles() -> List[Dict[str, Any]]:
    from app import tv_monitor
    return [{'id': p['id'], 'name': p['name'], 'default': p['default']} for p in tv_monitor.profiles()]


def movie_profiles() -> List[Dict[str, Any]]:
    from app import movie_settings as ms
    return [{'id': p['id'], 'name': p['name'], 'default': bool(p.get('default'))} for p in ms.picker_profiles()]


def tv_add(tmdb_id: int, path_id: str, monitor: str, profile_id: Optional[int], search: bool) -> Dict[str, Any]:
    from app import tv_calendar
    return tv_calendar.add(tmdb_id, path_id, monitor=monitor, profile_id=profile_id, search=search)


def movie_add(e: Dict[str, Any], profile_id: Optional[int], search: bool, available_at: Optional[float],
              now: float) -> Dict[str, Any]:
    """Onto the native movie watchlist (monitored: the movie scanner searches it once available under Settings >
    Movies availability, v256). Search when added: a movie that is already available is searched on the scanner's
    next pass instead of waiting for its schedule; one that isn't waits (run_due never searches it early)."""
    from app import movie_scanner, movie_settings as ms, movie_watchlist
    body = {'tmdb_id': e['tmdb'], 'title': e['title'], **({'year': e['year']} if e['year'] else {}),
            **({'profile_id': profile_id} if profile_id is not None else {})}
    try:
        movie_watchlist.add(body, now)
    except ms.Invalid as ex:
        return {'ok': False, 'error': f'Invalid {sorted(ex.errors)}'}
    if search and available_at is not None and available_at <= now:
        with ms.connect() as conn:
            movie_scanner.ensure(conn)
            movie_scanner._set(conn, e['tmdb'], next_search_at=now)
    return {'ok': True, 'ref': None}


# ------------------------------------------------------------------ engine

def _ids(entry: Dict[str, Any], key: str) -> Dict[str, Any]:
    obj = entry.get(key) if isinstance(entry, dict) else None
    obj = obj if isinstance(obj, dict) else {}
    ids = obj.get('ids') if isinstance(obj.get('ids'), dict) else {}
    num = lambda v: v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None    # noqa: E731
    year = obj.get('year')
    return {'tmdb': num(ids.get('tmdb')), 'tvdb': num(ids.get('tvdb')), 'title': str(obj.get('title') or 'Unknown'),
            'year': year if isinstance(year, int) and not isinstance(year, bool) else None}


def _has(m: Any, k: Any) -> bool:
    return bool(k) and isinstance(m, dict) and (k in m or str(k) in m)


def _owned(kind: str, index: Dict[str, Any], e: Dict[str, Any]) -> bool:
    if kind == 'shows':
        return _has(index.get('tv_by_tmdb'), e['tmdb']) or _has(index.get('tv_by_tvdb'), e['tvdb'])
    key = f"{e['title'].lower()}|{e['year']}" if e['title'] and e['year'] else None
    return _has(index.get('movies_by_tmdb'), e['tmdb']) or _has(index.get('movies'), key)


def running() -> bool:
    return _run['running']


def _empty() -> Dict[str, Any]:
    return {'checked': 0, 'added': 0, 'skipped': {k: 0 for k in SKIPS}, 'errors': [], 'error': None}


def run(kind: str = 'scheduled', now: Optional[float] = None) -> Dict[str, Any]:
    """One cycle over both groups. kind 'manual' (Run now) runs even when they're switched off."""
    with _lock:
        if _run['running']:
            return {'error': 'Already running'}
        _run['running'] = True
    now = time.time() if now is None else now
    try:
        result = _cycle(settings(), now, force=kind == 'manual')
    except Exception:                               # noqa: BLE001
        logger.exception('Anticipated: run failed')
        result = {'shows': None, 'movies': None, 'error': 'Run failed'}
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
    logger.info('Anticipated: %s', json.dumps(result)[:500])
    return result


def _cycle(cfg: Dict[str, Any], now: float, force: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {'shows': None, 'movies': None, 'error': None}
    kinds = [k for k in KINDS if force or cfg[k]['enabled']]
    if not kinds:
        return out
    cid = trakt_client_id()
    if not cid:
        return {**out, 'error': 'Trakt not connected'}
    index: Optional[Dict[str, Any]] = None
    readable = False
    if any(cfg[k]['skip_plex'] or cfg[k]['skip_if_plex_unreadable'] for k in kinds):
        try:
            index = plex_index() or {}
            readable = bool(index.get('movies') or index.get('tv'))
        except Exception as e:                      # noqa: BLE001
            logger.warning('Anticipated: Plex unreadable (%s)', type(e).__name__)
            index = {}
    for kind in kinds:
        g = cfg[kind]
        if index is not None and not readable and g['skip_if_plex_unreadable']:
            out[kind] = {**_empty(), 'error': "Plex can't be read"}
            continue
        try:
            out[kind] = _group_run(kind, g, index or {}, cid, now, cfg['history_kept'])
        except Busy:
            out[kind] = {**_empty(), 'error': 'Trakt is busy'}
            if cfg['when_busy'] == 'retry':
                out['_retry_at'] = now + BUSY_RETRY_S
    return out


def _group_run(kind: str, g: Dict[str, Any], index: Dict[str, Any], cid: str, now: float, keep: int) -> Dict[str, Any]:
    res = _empty()
    path_id = None
    if kind == 'shows':
        libs = libraries()
        if not libs:
            return {**res, 'error': 'No TV library'}
        path_id = g['path_id'] if any(l['id'] == g['path_id'] for l in libs) else libs[0]['id']
    try:
        raw = fetch_trakt(kind, cid, g['page'], g['limit'])
    except Busy:
        raise
    except Exception as e:                          # noqa: BLE001
        return {**res, 'error': f'Trakt failed ({e})'}
    entries = [_ids(x, 'show' if kind == 'shows' else 'movie') for x in raw[:g['limit']]]
    langs = g['languages']
    details: Dict[int, Dict[str, Any]] = {}
    want_details = bool(langs) or (kind == 'movies' and g['search_on_add'])
    if want_details:
        tids = [e['tmdb'] for e in entries if e['tmdb']]
        fn = show_details if kind == 'shows' else movie_details
        with ThreadPoolExecutor(max_workers=LANG_WORKERS) as pool:
            details = dict(zip(tids, pool.map(fn, tids)))
    for e in entries:
        res['checked'] += 1
        lang = (details.get(e['tmdb']) or {}).get('language', '') if e['tmdb'] else ''
        why = None
        if not e['tmdb']:
            why = 'no_tmdb'
        elif kind == 'shows' and g['require_tvdb'] and not e['tvdb']:
            why = 'no_tvdb'
        elif langs and (lang not in langs if lang else not g['allow_unknown_language']):
            why = 'language'
        elif g['skip_plex'] and _owned(kind, index, e):
            why = 'plex'
        elif (in_tv_db(e['tmdb'], e['tvdb']) if kind == 'shows' else on_watchlist(e['tmdb'])):
            why = 'existing'
        elif kind == 'movies' and movie_grabbed(e['tmdb']):
            why = 'grabbed'
        elif g['no_readd'] and seen(kind, e['tmdb'], e['tvdb']):
            why = 'removed'
        if why:
            res['skipped'][why] += 1
            continue
        try:
            if kind == 'shows':
                r = tv_add(e['tmdb'], path_id, g['monitor'], g['profile_id'], g['search_on_add'])
                ref = r.get('series_id')
            else:
                r = movie_add(e, g['profile_id'], g['search_on_add'],
                              (details.get(e['tmdb']) or {}).get('available_at'), now)
                ref = None
        except Exception as ex:                     # noqa: BLE001
            res['errors'].append(f"{e['title']}: {type(ex).__name__}")
            continue
        if r.get('ok'):
            _record_add(kind, e, now, ref, keep)
            res['added'] += 1
            logger.info('Anticipated: added %s %s (%s)', kind[:-1], e['title'], e['year'])
        elif r.get('status') == 409:
            res['skipped']['existing'] += 1
        else:
            res['errors'].append(f"{e['title']}: {r.get('error') or 'Add failed'}")
    return res


# ------------------------------------------------------------------ schedule

def next_run(cfg: Optional[Dict[str, Any]] = None, st: Optional[Dict[str, Any]] = None) -> Optional[float]:
    cfg = cfg or settings()
    if not any(cfg[k]['enabled'] for k in KINDS):
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
    return {'running': running(), 'enabled': any(cfg[k]['enabled'] for k in KINDS), 'next_run': next_run(cfg, st),
            'last_run': st.get('last_run'), 'last_kind': st.get('last_kind'), 'last_result': st.get('last_result')}


_started = {'on': False}


def _loop() -> None:
    time.sleep(FIRST_RUN_S)
    while True:
        try:
            if due(time.time()):
                run('scheduled')
        except Exception:                           # noqa: BLE001
            logger.exception('Anticipated: schedule tick failed')
        time.sleep(CHECK_S)


def start() -> bool:
    from app import movie_scanner
    if _started['on'] or not movie_scanner.on_test():     # test install only, like Genre Roulette
        return False
    _started['on'] = True
    _boot['at'] = time.time()
    threading.Thread(target=_loop, daemon=True, name='anticipated').start()
    logger.info('Anticipated scheduler started')
    return True
