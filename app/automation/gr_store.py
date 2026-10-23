"""v252 Genre Roulette store: automation.db in the install's data dir (sqlite, 0600).

Tables: meta (settings and engine state as JSON), gr_runs (run history), gr_grabs (what a run grabbed:
tmdb id, title, year, info hash; the movie importer reads the title and year from here, since a roulette
film is not on the watchlist). Nothing here talks to a network service.

Settings keep prod 1b4ea74's defaults (app/genre_roulette.py): off on a new install (:475, :2663;
automation_features "Everything is OFF on a new install"), 3 genres a night (:181), one movie per genre
(the drain stops a genre at its first grab, :2182-2201), TMDb votes 800 (:196), IMDb rating 0.0 (:103),
IMDb votes 120,000 (:96), seeders 5 (:197), English originals (:579), years this year-40 .. this year-3
(:201-202). The run time is 10:50 PM ET, the time prod actually runs at.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app import paths as _paths

DB_NAME = 'automation.db'
_lock = threading.RLock()
_cfg: Dict[str, Any] = {}

GENRES: Dict[str, int] = {
    'Action': 28, 'Adventure': 12, 'Animation': 16, 'Comedy': 35,
    'Crime': 80, 'Documentary': 99, 'Drama': 18, 'Family': 10751,
    'Fantasy': 14, 'History': 36, 'Horror': 27, 'Music': 10402,
    'Mystery': 9648, 'Romance': 10749, 'Science Fiction': 878,
    'Thriller': 53, 'War': 10752, 'Western': 37,
}
LANGUAGES = ('any', 'en', 'fr', 'es', 'de', 'it', 'ja', 'ko', 'zh', 'hi', 'sv', 'da', 'no', 'pt', 'ru')
DAYS = ('daily', 'weekdays', 'weekends')
NOTIFY = ('off', 'failure', 'finish')
HISTORY_DAYS = (30, 90, 365)
YEARS_BACK, YEARS_RECENT_GAP = 40, 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gr_runs (
  id INTEGER PRIMARY KEY, started_at REAL NOT NULL, finished_at REAL, kind TEXT NOT NULL,
  dry INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, wanted INTEGER NOT NULL DEFAULT 0,
  grabbed INTEGER NOT NULL DEFAULT 0, stop_reason TEXT, genres TEXT NOT NULL DEFAULT '[]',
  picks TEXT NOT NULL DEFAULT '[]', skips TEXT NOT NULL DEFAULT '{}', failures TEXT NOT NULL DEFAULT '[]',
  stats TEXT NOT NULL DEFAULT '{}', error TEXT
);
CREATE TABLE IF NOT EXISTS gr_grabs (
  hash TEXT PRIMARY KEY, tmdb_id INTEGER NOT NULL, title TEXT NOT NULL, year INTEGER,
  run_id INTEGER, grabbed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS gr_grabs_tmdb ON gr_grabs (tmdb_id);
"""


def db_path() -> Path:
    return Path(_cfg.get('db_path') or _paths.data_path(DB_NAME))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        pass
    conn = sqlite3.connect(str(p), timeout=10)
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

def defaults(today: Optional[date] = None) -> Dict[str, Any]:
    y = (today or date.today()).year
    return {
        'enabled': False, 'dry_run': False,
        'time': '22:50', 'days': 'daily',
        'genres_per_run': 3, 'movies_per_genre': 1,
        'include_genres': [], 'exclude_genres': [],
        'year_from': y - YEARS_BACK, 'year_to': y - YEARS_RECENT_GAP, 'language': 'en',
        'min_votes': 800, 'imdb_min_rating': 0.0, 'imdb_min_votes': 120000,
        'profile_id': None, 'min_seeders': 5, 'skip_same_collection': True,
        'attempt_budget': 60, 'time_budget_min': 45, 'retries': 3, 'retry_gap_min': 20,
        'owned_sections': [], 'notify': 'failure', 'history_days': 90,
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


def _genres(v: Any) -> Optional[List[str]]:
    if not isinstance(v, list) or not all(isinstance(g, str) for g in v):
        return None
    if any(g not in GENRES for g in v):
        return None
    return [g for g in GENRES if g in v]


def _time(v: Any) -> Optional[str]:
    if not isinstance(v, str) or len(v) != 5 or v[2] != ':':
        return None
    h, m = _int_s(v[:2]), _int_s(v[3:])
    return f'{h:02d}:{m:02d}' if h is not None and m is not None and h < 24 and m < 60 else None


def _int_s(s: str) -> Optional[int]:
    return int(s) if s.isdigit() else None


def check(body: Any, cur: Dict[str, Any]) -> Dict[str, Any]:
    """The settings after applying *body* (a partial update) to *cur*. Raises Invalid."""
    if not isinstance(body, dict):
        raise Invalid({'_': 'Invalid'})
    out, errors = dict(cur), {}
    rules = {
        'enabled': lambda v: v if isinstance(v, bool) else None,
        'dry_run': lambda v: v if isinstance(v, bool) else None,
        'skip_same_collection': lambda v: v if isinstance(v, bool) else None,
        'time': _time,
        'days': lambda v: v if v in DAYS else None,
        'genres_per_run': lambda v: _int(v, 1, len(GENRES)),
        'movies_per_genre': lambda v: _int(v, 1, 5),
        'include_genres': _genres, 'exclude_genres': _genres,
        'year_from': lambda v: _int(v, 1900, 2200), 'year_to': lambda v: _int(v, 1900, 2200),
        'language': lambda v: v if v in LANGUAGES else None,
        'min_votes': lambda v: _int(v, 0, 1_000_000),
        'imdb_min_rating': lambda v: float(v) if not isinstance(v, bool) and isinstance(v, (int, float))
        and 0 <= v <= 10 else None,
        'imdb_min_votes': lambda v: _int(v, 0, 10_000_000),
        'min_seeders': lambda v: _int(v, 0, 1000),
        'attempt_budget': lambda v: _int(v, 1, 500), 'time_budget_min': lambda v: _int(v, 1, 240),
        'retries': lambda v: _int(v, 0, 5), 'retry_gap_min': lambda v: _int(v, 5, 240),
        'owned_sections': lambda v: v if isinstance(v, list) and all(isinstance(x, str) and 0 < len(x) <= 200
                                                                    for x in v) and len(v) <= 50 else None,
        'notify': lambda v: v if v in NOTIFY else None,
        'history_days': lambda v: v if v in HISTORY_DAYS and not isinstance(v, bool) else None,
    }
    for k, v in body.items():
        if k == 'profile_id':                       # None: the default profile of Settings > Movies
            if v is None or _int(v, 1, 10 ** 9) is not None:
                out[k] = v
            else:
                errors[k] = 'Invalid'
            continue
        if k not in rules:
            errors[k] = 'Unknown'
            continue
        got = rules[k](v)
        if got is None:
            errors[k] = 'Invalid'
        else:
            out[k] = got
    if not errors and out['year_from'] > out['year_to']:
        errors['year_to'] = 'Invalid'
    if not errors and set(out['include_genres']) & set(out['exclude_genres']):
        errors['exclude_genres'] = 'Invalid'
    if errors:
        raise Invalid(errors)
    return out


_LEGACY = 'automation.genre_roulette.'


def _legacy() -> Dict[str, Any]:
    """Prod's settings keys, when this install has them (one time, at first load). Imported off."""
    try:
        from app.settings_manager import SettingsFile      # v259: read as on disk; no rewrite of settings.json
        s = SettingsFile()
        get = lambda k: s.get(_LEGACY + k, None)       # noqa: E731
    except Exception:                                   # noqa: BLE001
        return {}
    out: Dict[str, Any] = {}
    hour, minute = get('hour'), get('minute')
    if isinstance(hour, int) and 0 <= hour < 24:
        m = minute if isinstance(minute, int) and 0 <= minute < 60 else 0
        out['time'] = f'{hour:02d}:{m:02d}'
    for old, new in (('genres_per_night', 'genres_per_run'), ('year_from', 'year_from'), ('year_to', 'year_to'),
                     ('min_votes', 'min_votes'), ('imdb_min_rating', 'imdb_min_rating'),
                     ('imdb_min_votes', 'imdb_min_votes'), ('min_seeders', 'min_seeders')):
        v = get(old)
        if v is not None:
            out[new] = v
    g = get('genres')
    if isinstance(g, str):
        g = [x.strip() for x in g.split(',') if x.strip()]
    if isinstance(g, list):
        out['include_genres'] = [x for x in g if x in GENRES]
    out['enabled'] = False
    return out


def settings() -> Dict[str, Any]:
    with connect() as conn:
        cur = _meta(conn, 'settings', None)
        if not isinstance(cur, dict):
            base = defaults()
            try:
                cur = check({k: v for k, v in _legacy().items()}, base)
            except Invalid as e:
                cur = check({k: v for k, v in _legacy().items() if k not in e.errors}, base)
            _set_meta(conn, 'settings', cur)
        base = defaults()
        return {**base, **{k: v for k, v in cur.items() if k in base}}


def save(body: Any) -> Dict[str, Any]:
    new = check(body, settings())
    with connect() as conn:
        _set_meta(conn, 'settings', new)
    return new


# ------------------------------------------------------------------ engine state

def state() -> Dict[str, Any]:
    with connect() as conn:
        s = _meta(conn, 'state', {})
    return s if isinstance(s, dict) else {}


def save_state(s: Dict[str, Any]) -> None:
    with connect() as conn:
        _set_meta(conn, 'state', s)


# ------------------------------------------------------------------ runs and grabs

def start_run(kind: str, dry: bool, now: Optional[float] = None) -> int:
    with connect() as conn:
        cur = conn.execute('INSERT INTO gr_runs (started_at, kind, dry, status) VALUES (?, ?, ?, ?)',
                           (time.time() if now is None else now, kind, int(dry), 'running'))
        return int(cur.lastrowid)


def finish_run(run_id: int, result: Dict[str, Any], now: Optional[float] = None) -> None:
    with connect() as conn:
        conn.execute(
            'UPDATE gr_runs SET finished_at = ?, status = ?, wanted = ?, grabbed = ?, stop_reason = ?, genres = ?,'
            ' picks = ?, skips = ?, failures = ?, stats = ?, error = ? WHERE id = ?',
            (time.time() if now is None else now, result.get('status') or 'done', int(result.get('wanted') or 0),
             int(result.get('grabbed') or 0), result.get('stop_reason'), json.dumps(result.get('genres') or []),
             json.dumps(result.get('picks') or []), json.dumps(result.get('skips') or {}),
             json.dumps(result.get('failures') or []), json.dumps(result.get('stats') or {}), result.get('error'),
             run_id))


def _run(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    for k, empty in (('genres', []), ('picks', []), ('skips', {}), ('failures', []), ('stats', {})):
        try:
            d[k] = json.loads(d[k] or 'null') or empty
        except ValueError:
            d[k] = empty
    d['dry'] = bool(d['dry'])
    return d


def runs(limit: int = 30) -> List[Dict[str, Any]]:
    with connect() as conn:
        return [_run(r) for r in conn.execute('SELECT * FROM gr_runs ORDER BY started_at DESC, id DESC LIMIT ?',
                                              (int(limit),))]


def prune(history_days: int, now: Optional[float] = None) -> int:
    cut = (time.time() if now is None else now) - history_days * 86400
    with connect() as conn:
        return conn.execute("DELETE FROM gr_runs WHERE started_at < ? AND status != 'running'", (cut,)).rowcount


def interrupted(now: Optional[float] = None) -> None:
    """A run that was 'running' when the app stopped is closed as interrupted (at boot)."""
    with connect() as conn:
        conn.execute("UPDATE gr_runs SET status = 'error', error = 'Interrupted', stop_reason = 'interrupted',"
                     " finished_at = ? WHERE status = 'running'", (time.time() if now is None else now,))


def record_grab(info_hash: str, tmdb_id: int, title: str, year: Optional[int], run_id: Optional[int],
                now: Optional[float] = None) -> None:
    with connect() as conn:
        conn.execute('INSERT OR IGNORE INTO gr_grabs (hash, tmdb_id, title, year, run_id, grabbed_at)'
                     ' VALUES (?, ?, ?, ?, ?, ?)', (str(info_hash).lower(), int(tmdb_id), title, year, run_id,
                                                    time.time() if now is None else now))


def title_for(tmdb_id: int) -> Optional[Dict[str, Any]]:
    """{'title', 'year'} of a film a run grabbed (the movie importer's fallback), else None."""
    try:
        with connect() as conn:
            r = conn.execute('SELECT title, year FROM gr_grabs WHERE tmdb_id = ? ORDER BY grabbed_at DESC LIMIT 1',
                             (int(tmdb_id),)).fetchone()
    except sqlite3.Error:
        return None
    return {'title': r['title'], 'year': r['year']} if r else None


def grabbed_ids() -> set:
    with connect() as conn:
        return {int(r['tmdb_id']) for r in conn.execute('SELECT tmdb_id FROM gr_grabs')}
