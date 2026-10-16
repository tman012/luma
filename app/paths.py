"""Single source of truth for Luma's on-disk locations.

LUMA_DATA_DIR   persistent data (settings, databases, JSON state); default /app/data
LUMA_CACHE_DIR  rebuildable caches (explore, library, hero); default /app/cache
HERO_CACHE_DIR  hero images; default $LUMA_CACHE_DIR/hero
DATABASE_PATH   application SQLite database; see database_path()
"""

import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_DATA_DIR = '/app/data'
DEFAULT_CACHE_DIR = '/app/cache'


def data_dir() -> Path:
    """Directory for persistent data (LUMA_DATA_DIR, default /app/data)."""
    return Path(os.environ.get('LUMA_DATA_DIR') or DEFAULT_DATA_DIR)


def data_path(*parts: str) -> Path:
    """Path of a file inside the data directory."""
    return data_dir().joinpath(*parts)


def cache_dir() -> Path:
    """Root of the cache tree (LUMA_CACHE_DIR, default /app/cache)."""
    return Path(os.environ.get('LUMA_CACHE_DIR') or DEFAULT_CACHE_DIR)


def hero_cache_dir() -> Path:
    """Hero image directory: HERO_CACHE_DIR, else $LUMA_CACHE_DIR/hero."""
    explicit = os.environ.get('HERO_CACHE_DIR')
    if explicit:
        return Path(explicit)
    return cache_dir() / 'hero'


LEGACY_DATABASE_PATH = '/app/data/media.db'
_db_logged = set()
_log = logging.getLogger(__name__)


def resolve_database_path(saved: Optional[str] = None) -> Tuple[Path, str]:
    """(database path, source). Order:

    1. DATABASE_PATH in the environment ('env');
    2. a saved media.database_path setting ('setting');
    3. $LUMA_DATA_DIR/media.db ('data dir'), except when that file does not
       exist but a database already sits at the old default /app/data/media.db:
       then the existing file is used ('existing database') and a warning names
       both paths. A new, empty database is never created next to an old one.
    """
    env = (os.environ.get('DATABASE_PATH') or '').strip()
    if env:
        return Path(env), 'env'
    if saved and str(saved).strip():
        return Path(str(saved).strip()), 'setting'
    derived = data_path('media.db')
    legacy = Path(LEGACY_DATABASE_PATH)
    if derived != legacy and not derived.exists() and legacy.is_file():
        return legacy, 'existing database'
    return derived, 'data dir'


def database_path(saved: Optional[str] = None) -> Path:
    """The database path (see resolve_database_path), logged once per path."""
    path, source = resolve_database_path(saved)
    key = (str(path), source)
    if key not in _db_logged:
        _db_logged.add(key)
        if source == 'existing database':
            _log.warning('Database: using the existing %s instead of %s (LUMA_DATA_DIR). '
                         'Set media.database_path or DATABASE_PATH to pin it.',
                         path, data_path('media.db'))
        else:
            _log.info('Database: %s (from %s)', path, source)
    return path


def atomic_write_json(path, data, mode: int = 0o644) -> None:
    """Write *data* as JSON to *path* atomically: a temp file in the same
    directory, flush + fsync, then os.replace. On any error the temp file is
    removed and the old file is left untouched; the error propagates."""
    import json
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def quarantine(path, reason: str, logger: logging.Logger = None) -> Optional[Path]:
    """Move an unreadable state file aside to <name>.bad-<unix ts> and log an
    error. Returns the backup path, or None if it could not be moved."""
    import time
    path = Path(path)
    backup = path.with_name(f'{path.name}.bad-{int(time.time())}')
    try:
        os.replace(path, backup)
    except OSError as e:
        (logger or _log).error('%s is unreadable (%s) and could not be moved aside: %s', path, reason, e)
        return None
    (logger or _log).error('%s is unreadable (%s); moved it to %s', path, reason, backup)
    return backup


class StartupCheckError(RuntimeError):
    """A data/cache directory exists but Luma cannot write to it."""


def _db_files(root: Path, max_depth: int = 2) -> List[Path]:
    out = []
    for p in root.rglob('*.db'):
        if len(p.relative_to(root).parts) <= max_depth and p.is_file():
            out.append(p)
    return out


def check_writable_dirs(dirs=None) -> None:
    """Fail fast unless every data/cache dir is really writable.

    For each directory: create it if needed, create + delete a temp file, and
    open every SQLite database in it (up to 2 levels deep) for write with
    BEGIN IMMEDIATE. A populated read-only mount passes an exists/mkdir check
    but fails here, instead of logging "readonly database" errors later.
    Raises StartupCheckError listing every problem.
    """
    if dirs is None:
        dirs = {'LUMA_DATA_DIR': data_dir(), 'LUMA_CACHE_DIR': cache_dir(),
                'HERO_CACHE_DIR': hero_cache_dir()}
    problems = []
    for name, d in dirs.items():
        d = Path(d)
        try:
            d.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix='.luma-write-test-', dir=d)
            os.close(fd)
            os.unlink(tmp)
        except OSError as e:
            problems.append(f"{name}={d} is not writable: {e}")
            continue
        for db in _db_files(d):
            if not os.access(db, os.W_OK):
                problems.append(f"{name}: database {db} cannot be opened for write: file is not writable")
                continue
            try:
                # mode=rw: never silently fall back to a read-only connection
                conn = sqlite3.connect(f"file:{db}?mode=rw", uri=True, timeout=5)
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    conn.execute('ROLLBACK')
                finally:
                    conn.close()
            except sqlite3.Error as e:
                problems.append(f"{name}: database {db} cannot be opened for write: {e}")
    if problems:
        raise StartupCheckError(
            "Luma startup check failed (data/cache must be writable by the container user):\n  - "
            + "\n  - ".join(problems))


def ensure_writable_or_exit(logger: logging.Logger = None) -> None:
    """Run check_writable_dirs(); on failure log the reason and exit with status 1."""
    try:
        check_writable_dirs()
    except StartupCheckError as e:
        (logger or logging.getLogger(__name__)).critical(str(e))
        raise SystemExit(1)
