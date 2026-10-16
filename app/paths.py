"""Single source of truth for Luma's on-disk locations.

LUMA_DATA_DIR   persistent data (settings, databases, JSON state); default /app/data
LUMA_CACHE_DIR  rebuildable caches (explore, library, hero); default /app/cache
HERO_CACHE_DIR  hero images; default $LUMA_CACHE_DIR/hero
"""

import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import List

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
