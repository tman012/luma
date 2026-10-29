"""v204 (movie piece 4): the movie grab records in movies.db, shared by the grabber (piece 3, which writes a
row when it sends a release to the download client) and the importer (movie_import, which marks it imported).

Table movie_grabs: id, tmdb_id, release_title, hash (lower-case info hash, unique), state, grabbed_at,
updated_at, path (the imported file), error.
States: 'grabbed' (sent), 'importing', 'imported', 'failed'.

API (all take an optional now; nothing here talks to a download client or touches files):
  record(tmdb_id, release_title, download_hash, now=None) -> dict   a new grab ('grabbed'); the same hash again
                                                                    returns the existing row unchanged
  by_hash(download_hash) -> dict | None
  for_movie(tmdb_id) -> list[dict]                                  newest first
  pending() -> list[dict]                                           rows not yet imported
  set_state(download_hash, state, path=None, error=None, now=None) -> bool
  claim_importing(download_hash, now=None) -> bool                v324: atomic 'grabbed' -> 'importing' (a stale
                                                                    'importing' older than STALE_IMPORTING_S too)
  mark_imported(download_hash, path, now=None) -> bool             v324: atomic, once only: True for exactly one
                                                                    caller, the one that fires notify_library_added
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from app import movie_settings as ms

STATES = ('grabbed', 'importing', 'imported', 'failed')
STALE_IMPORTING_S = 12 * 3600
_HASH_RE = re.compile(r'^([0-9a-f]{40}|[0-9a-f]{64})$')

SCHEMA = """
CREATE TABLE IF NOT EXISTS movie_grabs (
  id INTEGER PRIMARY KEY, tmdb_id INTEGER NOT NULL, release_title TEXT NOT NULL,
  hash TEXT NOT NULL UNIQUE, state TEXT NOT NULL DEFAULT 'grabbed',
  grabbed_at REAL NOT NULL, updated_at REAL NOT NULL, path TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS movie_grabs_tmdb ON movie_grabs (tmdb_id);
"""


def _conn():
    cm = ms.connect()
    conn = cm.__enter__()
    conn.executescript(SCHEMA)
    return cm, conn


class _Db:
    def __enter__(self):
        self.cm, conn = _conn()
        return conn

    def __exit__(self, *exc):
        return self.cm.__exit__(*exc)


def _hash(h: Any) -> str:
    s = str(h or '').strip().lower()
    if not _HASH_RE.match(s):
        raise ValueError('bad download hash')
    return s


def record(tmdb_id: int, release_title: str, download_hash: str, now: Optional[float] = None) -> Dict[str, Any]:
    if not isinstance(tmdb_id, int) or isinstance(tmdb_id, bool) or tmdb_id < 1:
        raise ValueError('bad tmdb_id')
    title = str(release_title or '').strip()[:300]
    if not title:
        raise ValueError('no release title')
    h = _hash(download_hash)
    now = time.time() if now is None else now
    with _Db() as conn:
        conn.execute('INSERT OR IGNORE INTO movie_grabs (tmdb_id, release_title, hash, state, grabbed_at, updated_at)'
                     " VALUES (?, ?, ?, 'grabbed', ?, ?)", (tmdb_id, title, h, now, now))
        return dict(conn.execute('SELECT * FROM movie_grabs WHERE hash = ?', (h,)).fetchone())


def by_hash(download_hash: str) -> Optional[Dict[str, Any]]:
    try:
        h = _hash(download_hash)
    except ValueError:
        return None
    with _Db() as conn:
        r = conn.execute('SELECT * FROM movie_grabs WHERE hash = ?', (h,)).fetchone()
        return dict(r) if r else None


def for_movie(tmdb_id: int) -> List[Dict[str, Any]]:
    with _Db() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM movie_grabs WHERE tmdb_id = ? ORDER BY grabbed_at DESC, id DESC', (tmdb_id,))]


def pending() -> List[Dict[str, Any]]:
    with _Db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM movie_grabs WHERE state != 'imported' ORDER BY grabbed_at")]


def all_rows(limit: int = 100) -> List[Dict[str, Any]]:
    with _Db() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM movie_grabs ORDER BY grabbed_at DESC LIMIT ?', (limit,))]


def set_state(download_hash: str, state: str, path: Optional[str] = None, error: Optional[str] = None,
              now: Optional[float] = None) -> bool:
    if state not in STATES:
        raise ValueError('bad state')
    h = _hash(download_hash)
    now = time.time() if now is None else now
    with _Db() as conn:
        cur = conn.execute('UPDATE movie_grabs SET state = ?, path = COALESCE(?, path), error = ?, updated_at = ? WHERE hash = ?',
                           (state, path, error, now, h))
        return cur.rowcount > 0


def claim_importing(download_hash: str, now: Optional[float] = None) -> bool:
    """v324: take the row for one import pass. True only for the caller that moved it to 'importing'."""
    h = _hash(download_hash)
    now = time.time() if now is None else now
    with _Db() as conn:
        cur = conn.execute("UPDATE movie_grabs SET state = 'importing', error = NULL, updated_at = ? WHERE hash = ?"
                           " AND (state = 'grabbed' OR (state = 'importing' AND updated_at < ?))",
                           (now, h, now - STALE_IMPORTING_S))
        return cur.rowcount == 1


def mark_imported(download_hash: str, path: Optional[str], now: Optional[float] = None) -> bool:
    """v324: once-only. True for exactly one caller (that caller, and only it, notifies)."""
    h = _hash(download_hash)
    now = time.time() if now is None else now
    with _Db() as conn:
        cur = conn.execute("UPDATE movie_grabs SET state = 'imported', path = COALESCE(?, path), error = NULL,"
                           " updated_at = ? WHERE hash = ? AND state != 'imported'", (path, now, h))
        return cur.rowcount == 1
