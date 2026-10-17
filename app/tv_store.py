"""tv.db: TV series, episodes, files, grabs, history and the search log for
luma-test (minimal) builds.

SQLite in the data dir (never on a media share), WAL, foreign keys on, one
writer at a time (a process lock plus SQLite's busy timeout), short
transactions. The schema is versioned with PRAGMA user_version; MIGRATIONS[n]
takes the file from version n to n + 1. A file that can't be opened or isn't a
database is quarantined (moved aside) and a new one starts empty, like the
JSON stores. Luma's rows only: nothing here ever deletes a library file.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app import paths as _paths

logger = logging.getLogger(__name__)

DB_NAME = 'tv.db'
SEARCH_LOG_KEEP_S = 30 * 86400
PURPOSES = ('feed', 'episode', 'season', 'backlog', 'manual')
SECTIONS = ('tv', 'movies')

_cfg: Dict[str, Any] = {}
_lock = threading.RLock()
_ready: Dict[str, bool] = {}

SCHEMA_V1 = """
CREATE TABLE series (
  id INTEGER PRIMARY KEY,
  tmdb_id INTEGER UNIQUE NOT NULL,
  tvdb_id INTEGER, imdb_id TEXT,
  title TEXT NOT NULL DEFAULT '', original_title TEXT, sort_title TEXT,
  year INTEGER, status TEXT, network TEXT, overview TEXT,
  poster_path TEXT, backdrop_path TEXT, original_language TEXT,
  aliases TEXT NOT NULL DEFAULT '[]',
  series_type TEXT NOT NULL DEFAULT 'standard' CHECK (series_type IN ('standard', 'daily', 'anime')),
  episode_order TEXT NOT NULL DEFAULT 'tmdb', episode_group_id TEXT,
  path_id TEXT, folder TEXT,
  profile_id INTEGER REFERENCES quality_profiles(id) ON DELETE SET NULL,
  monitored INTEGER NOT NULL DEFAULT 0, monitor_new_seasons INTEGER NOT NULL DEFAULT 0,
  added_at REAL NOT NULL, tmdb_refreshed_at REAL, last_scan_at REAL
);
CREATE TABLE seasons (
  series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  season_number INTEGER NOT NULL,
  monitored INTEGER NOT NULL DEFAULT 0, name TEXT, poster_path TEXT,
  PRIMARY KEY (series_id, season_number)
);
CREATE TABLE episodes (
  id INTEGER PRIMARY KEY,
  series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  season_number INTEGER NOT NULL, episode_number INTEGER NOT NULL,
  absolute_number INTEGER, tmdb_episode_id INTEGER,
  title TEXT, overview TEXT, air_date TEXT, runtime INTEGER,
  monitored INTEGER NOT NULL DEFAULT 0,
  next_search_at REAL, last_search_at REAL, search_attempts INTEGER NOT NULL DEFAULT 0,
  first_seen_release_at REAL,
  UNIQUE (series_id, season_number, episode_number)
);
CREATE INDEX episodes_due ON episodes(next_search_at) WHERE next_search_at IS NOT NULL;
CREATE TABLE episode_files (
  id INTEGER PRIMARY KEY,
  series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  path_id TEXT NOT NULL, rel_path TEXT NOT NULL,
  size INTEGER, mtime REAL, dev INTEGER, ino INTEGER,
  resolution TEXT, source TEXT, quality_rank INTEGER,
  release_name TEXT, release_group TEXT, proper INTEGER NOT NULL DEFAULT 0,
  origin TEXT NOT NULL CHECK (origin IN ('scan', 'import')),
  superseded_by INTEGER REFERENCES episode_files(id) ON DELETE SET NULL,
  added_at REAL NOT NULL,
  UNIQUE (path_id, rel_path)
);
CREATE TABLE episode_file_links (
  file_id INTEGER NOT NULL REFERENCES episode_files(id) ON DELETE CASCADE,
  episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  PRIMARY KEY (file_id, episode_id)
);
CREATE TABLE quality_profiles (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  items TEXT NOT NULL DEFAULT '[]', cutoff TEXT,
  upgrades INTEGER NOT NULL DEFAULT 0,
  min_mb_per_min REAL, max_mb_per_min REAL,
  min_seeders INTEGER NOT NULL DEFAULT 1, prefer_freeleech INTEGER NOT NULL DEFAULT 0,
  must_contain TEXT NOT NULL DEFAULT '[]', must_not_contain TEXT NOT NULL DEFAULT '[]',
  prefer_season_packs INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE grabs (
  hash TEXT PRIMARY KEY,
  series_id INTEGER REFERENCES series(id) ON DELETE SET NULL,
  season_number INTEGER, episode_ids TEXT NOT NULL DEFAULT '[]',
  release_title TEXT, tracker_id INTEGER, quality TEXT, size INTEGER,
  origin TEXT NOT NULL CHECK (origin IN ('auto', 'manual', 'upgrade')),
  grabbed_at REAL NOT NULL,
  state TEXT NOT NULL DEFAULT 'downloading'
    CHECK (state IN ('downloading', 'importing', 'imported', 'partial', 'failed', 'manual_needed', 'skipped')),
  error TEXT, results TEXT, finished_at REAL
);
CREATE TABLE history (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL,
  series_id INTEGER REFERENCES series(id) ON DELETE CASCADE,
  episode_ids TEXT NOT NULL DEFAULT '[]', event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX history_series ON history(series_id, ts);
CREATE TABLE search_log (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL,
  section TEXT NOT NULL CHECK (section IN ('tv', 'movies')),
  tracker_key TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('feed', 'episode', 'season', 'backlog', 'manual')),
  series_id INTEGER, query TEXT, results INTEGER, outcome TEXT
);
CREATE INDEX search_log_ts ON search_log(ts);
CREATE INDEX search_log_tracker ON search_log(tracker_key, ts);
CREATE TABLE blocklist (
  id INTEGER PRIMARY KEY,
  series_id INTEGER REFERENCES series(id) ON DELETE CASCADE,
  info_hash TEXT, release_title TEXT, reason TEXT, ts REAL NOT NULL
);
CREATE TABLE unmatched (
  id INTEGER PRIMARY KEY, path_id TEXT, folder TEXT, guess_title TEXT, guess_year INTEGER,
  candidates TEXT NOT NULL DEFAULT '[]', reason TEXT, ts REAL NOT NULL
);
"""

# MIGRATIONS[n] takes user_version n to n + 1.
MIGRATIONS: List[str] = [SCHEMA_V1]
SCHEMA_VERSION = len(MIGRATIONS)


def db_path() -> Path:
    return Path(_cfg.get('path') or _paths.data_path(DB_NAME))


def init(path: Optional[str] = None) -> None:
    with _lock:
        if path:
            _cfg['path'] = path
        _ready.clear()


def _open(p: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(p), timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 10000')
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f'tv.db is from a newer Luma (schema {version} > {SCHEMA_VERSION})')
    while version < SCHEMA_VERSION:
        conn.execute('BEGIN IMMEDIATE')
        try:
            for stmt in _statements(MIGRATIONS[version]):
                conn.execute(stmt)
            conn.execute(f'PRAGMA user_version = {version + 1}')
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise
        version += 1
        logger.info('tv.db schema now at version %d', version)


def _statements(script: str) -> Iterator[str]:
    buf = ''
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf.strip()
            buf = ''
    if buf.strip():
        yield buf.strip()


def _prepare(p: Path) -> None:
    """Open, check and migrate the file once per path; a broken file is
    quarantined and replaced with an empty one."""
    if _ready.get(str(p)):
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        conn = None
        try:
            conn = _open(p)
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('quick_check failed')
            conn.execute('PRAGMA journal_mode = WAL')
            _migrate(conn)
            break
        except sqlite3.DatabaseError as e:
            if conn is not None:
                conn.close()
                conn = None
            if attempt == 2:
                raise
            logger.error('tv.db unreadable (%s); starting empty', type(e).__name__)
            for suffix in ('-wal', '-shm'):
                try:
                    Path(str(p) + suffix).unlink()
                except OSError:
                    pass
            _paths.quarantine(p, 'unreadable', logger)
        finally:
            if conn is not None:
                conn.close()
    try:
        p.chmod(0o600)
    except OSError:
        pass
    _ready[str(p)] = True


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """A connection for reads (autocommit)."""
    p = db_path()
    with _lock:
        _prepare(p)
    conn = _open(p)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """One short write transaction (BEGIN IMMEDIATE ... COMMIT), one writer at
    a time in this process."""
    with _lock:
        with connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise


def schema_version() -> int:
    with connect() as conn:
        return conn.execute('PRAGMA user_version').fetchone()[0]


# --------------------------------------------------------------------------- search log

def log_search(*, tracker_key: str, purpose: str, section: str = 'tv', series_id: Optional[int] = None,
               query: str = '', results: Optional[int] = None, outcome: str = '',
               now: Optional[float] = None) -> None:
    """Record one tracker request made for a section (powers the budget view)."""
    if purpose not in PURPOSES or section not in SECTIONS or not isinstance(tracker_key, str) or not tracker_key:
        raise ValueError('bad search log entry')
    ts = time.time() if now is None else now
    with write() as conn:
        conn.execute('INSERT INTO search_log (ts, section, tracker_key, purpose, series_id, query, results, outcome)'
                     ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (ts, section, tracker_key[:200], purpose, series_id, (query or '')[:300], results,
                      (outcome or '')[:80]))
        conn.execute('DELETE FROM search_log WHERE ts < ?', (ts - SEARCH_LOG_KEEP_S,))


def search_counts(section: str = 'tv', since: Optional[float] = None,
                  now: Optional[float] = None) -> Dict[str, Dict[str, int]]:
    """{tracker_key: {purpose: n}} for requests since *since* (default: the last 24 h)."""
    now = time.time() if now is None else now
    since = now - 86400 if since is None else since
    out: Dict[str, Dict[str, int]] = {}
    with connect() as conn:
        for row in conn.execute('SELECT tracker_key, purpose, COUNT(*) AS n FROM search_log'
                                ' WHERE section = ? AND ts >= ? GROUP BY tracker_key, purpose',
                                (section, since)):
            out.setdefault(row['tracker_key'], {})[row['purpose']] = row['n']
    return out


def add_history(event: str, *, series_id: Optional[int] = None, episode_ids: Optional[List[int]] = None,
                detail: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> int:
    with write() as conn:
        cur = conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                           (time.time() if now is None else now, series_id, json.dumps(episode_ids or []),
                            event[:40], json.dumps(detail or {})))
        return int(cur.lastrowid)
