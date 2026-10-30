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
from typing import Any, Dict, Iterator, List, Optional, Tuple

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

# v158a monitoring: the series' Monitor option (all/future/missing/...), specials and
# season folders, and the default TV quality profile. Shows with no monitoring choice of their
# own become Monitored with Future episodes (no back catalog) and new seasons on; a show that
# already had one (monitored, new seasons on, or any monitored season or episode) keeps it
# (PM, v158b). monitor_defaults() is that step: idempotent, never resets a choice.
SCHEMA_V7 = """
ALTER TABLE series ADD COLUMN monitor TEXT NOT NULL DEFAULT 'none';
ALTER TABLE series ADD COLUMN monitor_specials INTEGER NOT NULL DEFAULT 0;
ALTER TABLE series ADD COLUMN season_folder INTEGER NOT NULL DEFAULT 1;
ALTER TABLE quality_profiles ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0;
"""

# v158b: monitor_chosen marks a show whose monitoring was set (an Add, an import, an Edit, a
# toggle, or the v7 defaults). Every show that reached v7 has been through one of those, so all
# existing rows are chosen.
SCHEMA_V8 = """
ALTER TABLE series ADD COLUMN monitor_chosen INTEGER NOT NULL DEFAULT 0;
UPDATE series SET monitor_chosen = 1;
"""


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}


def monitor_defaults(conn: sqlite3.Connection) -> int:
    """Future (episodes airing from today, new seasons on) for each show with no monitoring
    choice; returns how many. Safe to run again: a show with any choice is never touched."""
    cols = _columns(conn, 'series')
    if 'monitor' not in cols:
        return 0
    chosen = ' AND s.monitor_chosen = 0' if 'monitor_chosen' in cols else ''
    ids = [r[0] for r in conn.execute(
        "SELECT s.id FROM series s WHERE s.monitored = 0 AND s.monitor = 'none' AND s.monitor_new_seasons = 0"
        ' AND NOT EXISTS (SELECT 1 FROM episodes e WHERE e.series_id = s.id AND e.monitored = 1)'
        ' AND NOT EXISTS (SELECT 1 FROM seasons x WHERE x.series_id = s.id AND x.monitored = 1)' + chosen)]
    for sid in ids:
        conn.execute("UPDATE series SET monitored = 1, monitor = 'future', monitor_new_seasons = 1"
                     + (', monitor_chosen = 1' if chosen else '') + ' WHERE id = ?', (sid,))
        conn.execute("UPDATE episodes SET monitored = CASE WHEN season_number > 0 AND (air_date IS NULL"
                     " OR air_date >= date('now', 'localtime')) THEN 1 ELSE 0 END WHERE series_id = ?", (sid,))
        conn.execute('UPDATE seasons SET monitored = CASE WHEN EXISTS (SELECT 1 FROM episodes e'
                     ' WHERE e.series_id = seasons.series_id AND e.season_number = seasons.season_number'
                     ' AND e.monitored = 1) THEN 1 ELSE 0 END WHERE series_id = ?', (sid,))
    return len(ids)


# v158c (Tester): a show that kept its v6 monitoring choice (monitored, or some episodes monitored)
# reached v7 with the column default monitor = 'none', so the Edit sheet said None. v9 gives those
# shows the option that matches their episodes (no episode, season or series switch changes).
SCHEMA_V9 = "UPDATE series SET monitor = monitor WHERE 0;"
LABEL_ORDER = ('all', 'future', 'missing', 'existing', 'recent', 'first_season', 'last_season', 'pilot')


def monitor_labels(conn: sqlite3.Connection, today: Optional[str] = None) -> int:
    """The Monitor option for each show labelled 'none' whose series switch is on: the first
    option in LABEL_ORDER whose episodes are exactly the monitored regular episodes, else the
    closest one (fewest differences). Labels only; returns how many.

    A show whose series switch is off keeps 'none' even with episodes switched on (v165, Tester):
    every other option turns the series switch on, so those episodes were picked one by one (e.g.
    the owner chose None and later switched one episode on). Calling that 'pilot' was wrong."""
    import datetime as _dt
    cols = _columns(conn, 'series')
    if 'monitor' not in cols:
        return 0
    today = today or _dt.date.today().isoformat()
    ids = [r[0] for r in conn.execute(
        "SELECT s.id FROM series s WHERE s.monitor = 'none' AND s.monitored = 1")]
    for sid in ids:
        best = _best_label(conn, sid, today)
        conn.execute('UPDATE series SET monitor = ? WHERE id = ?', (best, sid))
    return len(ids)


def _best_label(conn: sqlite3.Connection, sid: int, today: str) -> str:
    """monitor_labels' pick for one show: the option closest to its monitored regular episodes."""
    from app import tv_monitor
    eps = [dict(zip(('season_number', 'episode_number', 'air_date', 'monitored', 'has_file'), r)) for r in conn.execute(
        f'SELECT e.season_number, e.episode_number, e.air_date, e.monitored, {tv_monitor._LINKED} FROM episodes e'
        ' WHERE e.series_id = ? AND e.season_number > 0', (sid,))]
    regular = sorted({e['season_number'] for e in eps})
    first, last = (regular[0], regular[-1]) if regular else (1, 1)
    best, best_d = 'all', None
    for opt in LABEL_ORDER:
        d = sum(1 for e in eps if bool(tv_monitor._wants(opt, e, today, first, last, bool(e['has_file']))) != bool(e['monitored']))
        if best_d is None or d < best_d:
            best, best_d = opt, d
        if d == 0:
            break
    return best


# v166 (PM): v9 was corrected in place (v165), so a tv.db that reached v9 on an earlier build (:5002)
# kept that build's labels: a show whose series switch is off but with episodes switched on (picked
# one by one, e.g. None and then one episode) was labelled with the closest option ('pilot'). v10
# applies the corrected rule to those shows: their label goes back to 'none'. Labels only: series,
# season and episode switches, monitor_chosen and everything else stay as they are. A show is
# relabelled only when its label is exactly what the old v9 would have given it, so an option the
# owner picked that no longer matches its episodes is left alone. Safe to run again (a 'none' show is
# never selected). The upgrade's verified backup (tv.db.bak-v9) is made first, as for every step.
SCHEMA_V10 = "UPDATE series SET monitor = monitor WHERE 0;"


def repair_v9_labels(conn: sqlite3.Connection, today: Optional[str] = None) -> int:
    import datetime as _dt
    cols = _columns(conn, 'series')
    if 'monitor' not in cols:
        return 0
    today = today or _dt.date.today().isoformat()
    marks = ','.join('?' * len(LABEL_ORDER))
    ids = [r[0] for r in conn.execute(
        f"SELECT s.id FROM series s WHERE s.monitored = 0 AND s.monitor IN ({marks})"
        ' AND EXISTS (SELECT 1 FROM episodes e WHERE e.series_id = s.id AND e.season_number > 0 AND e.monitored = 1)'
        ' ORDER BY s.id', LABEL_ORDER)]
    fixed = 0
    for sid in ids:
        cur = conn.execute('SELECT monitor FROM series WHERE id = ?', (sid,)).fetchone()[0]
        if _best_label(conn, sid, today) == cur:
            conn.execute("UPDATE series SET monitor = 'none' WHERE id = ?", (sid,))
            fixed += 1
    if fixed:
        logger.info('tv.db v10: %d monitor label(s) from the old v9 set back to None (labels only)', fixed)
    return fixed


# v220 (Taylor via PM): date-named shows are Daily (Sonarr's daily series type): WWE Raw and Judy Justice,
# and any show with a grab whose release is named by its air date (YYYY MM DD / YYYY.MM.DD / YYYY-MM-DD,
# no SxxEyy). Only 'standard' shows change; the type stays editable. Safe to run again.
SCHEMA_V11 = "UPDATE series SET series_type = series_type WHERE 0;"
# v233: Judy Justice is released as SxxEyy (Judy Justice S04E117 1080p WEB h264-EDITH), so it is not Daily
DAILY_TITLES = ('wwe monday night raw', 'wwe raw')
NOT_DAILY_TITLES = ('judy justice',)


def daily_types(conn: sqlite3.Connection) -> List[int]:
    if 'series_type' not in _columns(conn, 'series'):
        return []
    from app import tv_match, tv_release
    ids = {r[0] for r in conn.execute('SELECT id, title FROM series') if tv_match.normalize(r[1] or '') in
           {tv_match.normalize(t) for t in DAILY_TITLES}}
    for r in conn.execute('SELECT DISTINCT series_id, release_title FROM grabs WHERE series_id IS NOT NULL'):
        if r[1] and tv_release.parse(r[1]).kind == 'daily':
            ids.add(r[0])
    out = []
    for sid in sorted(ids):
        if conn.execute("UPDATE series SET series_type = 'daily' WHERE id = ? AND series_type = 'standard'", (sid,)).rowcount:
            out.append(sid)
    if out:
        logger.info('tv.db v11: %d show(s) set to Daily: %s', len(out), out)
    return out


# v233 (PM): v11 set Judy Justice to Daily by title; its releases are SxxEyy. Put it back to Standard unless
# a grab of it was named by air date. Safe to run again.
SCHEMA_V12 = "UPDATE series SET series_type = series_type WHERE 0;"


def undaily_titles(conn: sqlite3.Connection) -> List[int]:
    if 'series_type' not in _columns(conn, 'series'):
        return []
    from app import tv_match, tv_release
    names = {tv_match.normalize(t) for t in NOT_DAILY_TITLES}
    out = []
    for sid, title in conn.execute("SELECT id, title FROM series WHERE series_type = 'daily'").fetchall():
        if tv_match.normalize(title or '') not in names:
            continue
        rels = [r[0] for r in conn.execute('SELECT release_title FROM grabs WHERE series_id = ?', (sid,))]
        if any(r and tv_release.parse(r).kind == 'daily' for r in rels):
            continue
        conn.execute("UPDATE series SET series_type = 'standard' WHERE id = ?", (sid,))
        out.append(sid)
    if out:
        logger.info('tv.db v12: %d show(s) back to Standard from Daily: %s', len(out), out)
    return out


# v321 (PM): the series detail header shows the show's TheTVDB banner (series artwork type 1).
SCHEMA_V13 = "ALTER TABLE series ADD COLUMN banner_url TEXT;"


# MIGRATIONS[n] takes user_version n to n + 1.
MIGRATIONS: List[Any] = [SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5, SCHEMA_V6,
                         (SCHEMA_V7, monitor_defaults), SCHEMA_V8, (SCHEMA_V9, monitor_labels),
                         (SCHEMA_V10, repair_v9_labels), (SCHEMA_V11, daily_types), (SCHEMA_V12, undaily_titles),
                         SCHEMA_V13]
# A step is SQL, or (SQL, fn(conn)) run in the same transaction after it.
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


class BackupFailed(RuntimeError):
    """No verified pre-upgrade copy: tv.db is not migrated (it stays at its old schema)."""


def _copy(conn: sqlite3.Connection, out: sqlite3.Connection) -> None:
    conn.backup(out)


def _verified(path: str, version: int) -> bool:
    """A usable backup: non-empty, passes quick_check, and is at *version*."""
    try:
        if os.path.getsize(path) <= 0:
            return False
        c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            return (c.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
                    and c.execute('PRAGMA user_version').fetchone()[0] == version)
        finally:
            c.close()
    except (OSError, sqlite3.DatabaseError):
        return False


def _remove(*paths: str) -> None:
    for f in paths:
        for g in (f, f + '-journal', f + '-wal', f + '-shm'):
            try:
                os.unlink(g)
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning('Could not remove %s', os.path.basename(g))


def _backup(conn: sqlite3.Connection, p: Path, version: int) -> None:
    """Before an upgrade: tv.db (its -wal checkpointed first) copied to a temp
    file, checked (non-empty, quick_check, user_version == *version*), then
    os.replace'd to tv.db.bak-v{version}, 0600. An existing backup that checks
    out is kept (the older copy); a bad one is redone. Any failure (a full disk
    included) removes the partial temp file and raises BackupFailed."""
    dst = f'{p}.bak-v{version}'
    if os.path.exists(dst):
        if _verified(dst, version):
            return
        logger.warning('tv.db backup %s failed its check; making it again', os.path.basename(dst))
        _remove(dst)
    tmp = f'{dst}.tmp-{os.getpid()}'                    # per process: two workers never share one
    _remove(tmp)
    try:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0), 0o600)
        os.close(fd)
        out = sqlite3.connect(tmp)
        try:
            _copy(conn, out)
            out.execute('PRAGMA journal_mode = DELETE')
        finally:
            out.close()
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if not _verified(tmp, version):
            raise BackupFailed('the copy failed its check')
        os.chmod(tmp, 0o600)
        os.replace(tmp, dst)
        try:
            dfd = os.open(os.path.dirname(dst) or '.', os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BackupFailed:
        _remove(tmp)
        raise
    except (OSError, sqlite3.Error) as e:
        _remove(tmp)
        raise BackupFailed(f'{type(e).__name__}: {getattr(e, "strerror", None) or e}') from e
    logger.info('tv.db backed up (verified) before upgrading from version %d', version)


def _migrate(conn: sqlite3.Connection, p: Optional[Path] = None) -> None:
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f'tv.db is from a newer Luma (schema {version} > {SCHEMA_VERSION})')
    if p is not None and 0 < version < SCHEMA_VERSION:      # an upgrade, not a fresh create
        try:
            _backup(conn, p, version)
        except BackupFailed as e:
            # Never migrate without a verified copy. Raising leaves the file untouched at its old
            # version; _ready stays unset, so this connect() fails (tv.db reads/writes error out
            # while the rest of Luma serves) and the next connect() tries the backup again.
            logger.error('tv.db not upgraded from version %d: no verified backup (%s)', version, e)
            raise
    # One backup (of the starting version) covers the whole chain. Each step commits on its own, so a
    # step that fails rolls back only itself: the file stays at the last step that completed (a
    # valid, older schema) and the next open resumes from there.
    while version < SCHEMA_VERSION:
        conn.execute('BEGIN IMMEDIATE')
        try:
            step = MIGRATIONS[version]
            sql, fn = step if isinstance(step, tuple) else (step, None)
            for stmt in _statements(sql):
                conn.execute(stmt)
            if fn is not None:
                fn(conn)
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
            _migrate(conn, p)
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


VACUUM_FREE_MIN = 32 * 1024 * 1024      # free pages worth giving back to the disk at boot


def compact_on_start() -> Optional[Tuple[int, int]]:
    """Boot, after the migration: when most of tv.db is free pages (an old, larger
    TMDB cache), VACUUM once to give the space back. (before, after) bytes, or
    None when not needed or it failed (logged; tv.db is unchanged then)."""
    p = db_path()
    try:
        with connect() as conn:
            size_page = conn.execute('PRAGMA page_size').fetchone()[0]
            free = conn.execute('PRAGMA freelist_count').fetchone()[0] * size_page
            total = conn.execute('PRAGMA page_count').fetchone()[0] * size_page
        if free < VACUUM_FREE_MIN or free * 2 < total:
            return None
        before = os.path.getsize(p)
        with _lock:
            conn = _open(p)
            try:
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                conn.execute('VACUUM')
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally:
                conn.close()
        after = os.path.getsize(p)
        _private(p)
        logger.info('tv.db compacted: %.1f MB -> %.1f MB', before / 1e6, after / 1e6)
        return before, after
    except Exception as e:                # noqa: BLE001 - a full disk or a busy file: it stays as it is
        logger.warning('tv.db not compacted (%s)', type(e).__name__)
        return None


def migrate_on_start() -> bool:
    """Open and migrate tv.db once at boot (the backup copy is taken first, in
    _migrate). Never raises: on failure it logs and leaves the file to the
    lazy path, which retries on the next connect()."""
    p = db_path()
    try:
        with _lock:
            _prepare(p)
        logger.info('tv.db ready at schema v%d', schema_version())
        return True
    except Exception as e:                # boot must go on
        logger.error('tv.db migration at boot failed (%s); will retry on first use', type(e).__name__)
        return False


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
