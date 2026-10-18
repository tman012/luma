"""tv.db: TV series, episodes, files, grabs, history and the search log for
luma-test (minimal) builds.

SQLite in the data dir (never on a media share), WAL, foreign keys on, one
writer at a time (a process lock plus SQLite's busy timeout), short
transactions. The schema is versioned with PRAGMA user_version; MIGRATIONS[n]
takes the file from version n to n + 1. A file that is corrupt or isn't a
database (SQLITE_CORRUPT, SQLITE_NOTADB, a failed quick_check) is quarantined
with its -wal/-shm (moved aside, never deleted) and a new one starts empty,
like the JSON stores. Anything else (a lock or busy timeout, a read-only or
full disk, a missing folder) is raised as is and the file is left alone.
tv.db, tv.db-wal and tv.db-shm are 0600: the file is created 0600 before
SQLite opens it (SQLite gives -wal/-shm the database file's mode) and all
three are chmodded after opening as well.
Luma's rows only: nothing here ever deletes a library file.
"""

from __future__ import annotations

import json
import os
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

# Phase 1 (library scan): a scanned file's link state, one series and one
# unmatched row per library folder, and a small key/value table (last scan).
SCHEMA_V2 = """
ALTER TABLE episode_files ADD COLUMN state TEXT NOT NULL DEFAULT 'linked'
  CHECK (state IN ('linked', 'unrecognized', 'mismatch'));
ALTER TABLE episode_files ADD COLUMN parsed TEXT;
CREATE UNIQUE INDEX series_folder ON series(path_id, folder) WHERE path_id IS NOT NULL AND folder IS NOT NULL;
CREATE UNIQUE INDEX unmatched_folder ON unmatched(path_id, folder);
CREATE INDEX episode_files_series ON episode_files(series_id);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

# TV Import: a folder's confidence tier and best guess (review pre-selects
# it), episode-group numbering per show, and a 24 h TMDB answer cache.
SCHEMA_V3 = """
ALTER TABLE unmatched ADD COLUMN tier TEXT NOT NULL DEFAULT 'none'
  CHECK (tier IN ('likely', 'none', 'duplicate'));
ALTER TABLE unmatched ADD COLUMN pick INTEGER;
ALTER TABLE series ADD COLUMN match_tier TEXT;
CREATE TABLE episode_numbering (
  series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  season_number INTEGER NOT NULL, episode_number INTEGER NOT NULL,
  episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  PRIMARY KEY (series_id, season_number, episode_number)
);
CREATE TABLE tmdb_cache (key TEXT PRIMARY KEY, ts REAL NOT NULL, body TEXT NOT NULL);
"""

# Air times (TVmaze, no key): an episode's UTC airstamp and runtime, and the
# show's TVmaze id with when it was last asked.
SCHEMA_V4 = """
ALTER TABLE episodes ADD COLUMN airstamp TEXT;
ALTER TABLE episodes ADD COLUMN air_runtime INTEGER;
ALTER TABLE series ADD COLUMN tvmaze_id INTEGER;
ALTER TABLE series ADD COLUMN tvmaze_checked_at REAL;
"""

# Calendar stills: TMDB's episode still and TheTVDB's episode image (with a key).
SCHEMA_V5 = """
ALTER TABLE episodes ADD COLUMN still_path TEXT;
ALTER TABLE episodes ADD COLUMN tvdb_image TEXT;
"""

# When a show's episode images (stills) were last read: NULL is never, so the
# Calendar backfills it at once (a show with no images isn't asked again).
SCHEMA_V6 = """
ALTER TABLE series ADD COLUMN stills_at REAL;
"""

# MIGRATIONS[n] takes user_version n to n + 1.
MIGRATIONS: List[str] = [SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5, SCHEMA_V6]
SCHEMA_VERSION = len(MIGRATIONS)


def db_path() -> Path:
    return Path(_cfg.get('path') or _paths.data_path(DB_NAME))


def init(path: Optional[str] = None) -> None:
    with _lock:
        if path:
            _cfg['path'] = path
        _ready.clear()


_CORRUPT_CODES = {11, 26}            # SQLITE_CORRUPT, SQLITE_NOTADB
_CORRUPT_TEXT = ('malformed', 'not a database', 'quick_check failed', 'corrupt')


class Corrupt(sqlite3.DatabaseError):
    """The file is not a usable database (as opposed to busy or locked)."""


def is_corrupt(e: BaseException) -> bool:
    """True only for real corruption. A lock or busy timeout, a read-only or
    full disk, or a missing folder is an OperationalError and never counts."""
    if isinstance(e, Corrupt):
        return True
    if not isinstance(e, sqlite3.DatabaseError):
        return False
    code = getattr(e, 'sqlite_errorcode', None)
    if isinstance(code, int):
        return (code & 0xff) in _CORRUPT_CODES
    if isinstance(e, sqlite3.OperationalError):
        return False
    return any(t in str(e).lower() for t in _CORRUPT_TEXT)


def _private(p: Path) -> None:
    """Create the file 0600 if it doesn't exist, and chmod it and its
    -wal/-shm to 0600."""
    try:
        os.close(os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0), 0o600))
    except FileExistsError:
        pass
    for f in (str(p), str(p) + '-wal', str(p) + '-shm'):
        try:
            fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0))
        except OSError:
            continue
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        finally:
            os.close(fd)


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
            _private(p)
            conn = _open(p)
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise Corrupt('quick_check failed')
            conn.execute('PRAGMA journal_mode = WAL')
            _migrate(conn)
            _private(p)
            break
        except sqlite3.DatabaseError as e:
            if conn is not None:
                conn.close()
                conn = None
            if attempt == 2 or not is_corrupt(e):
                raise                     # busy, locked, read-only...: leave the file alone
            logger.error('tv.db is corrupt (%s); starting empty', type(e).__name__)
            backup = _paths.quarantine(p, 'corrupt', logger)
            if backup is None:
                raise
            for suffix in ('-wal', '-shm'):   # they belong to the bad file: keep them with it
                side = Path(str(p) + suffix)
                if side.exists():
                    try:
                        os.replace(side, str(backup) + suffix)
                    except OSError:
                        pass
        finally:
            if conn is not None:
                conn.close()
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


def get_meta(key: str) -> Optional[str]:
    with connect() as conn:
        row = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
        return row[0] if row else None


def set_meta(key: str, value: Optional[str]) -> None:
    with write() as conn:
        if value is None:
            conn.execute('DELETE FROM meta WHERE key = ?', (key,))
        else:
            conn.execute('INSERT INTO meta (key, value) VALUES (?, ?)'
                         ' ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, value))
