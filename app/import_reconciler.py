"""Import reconciler (P0): no completed Luma download is left unimported.

Every INTERVAL_S (10 min; first pass STARTUP_DELAY_S after start, so a restart or reboot is reconciled right
away) on the test install, one pass lists the COMPLETED torrents in qBittorrent that Luma owns: any category or
tag Luma assigns (the test tag qbit_owner.tag_name(), the movie names movie_import.categories(), the TV
category tv_downloads.category(); never the other install's TV category) plus every hash a library_mover job
tracks. Each is matched against the library and the import jobs, and one INFO line is logged per decision:

  already_imported: <how>       a mover job done/exists, the movie grab imported, its file (or another grab
                                of the same TMDB id) in the library, or the TV watch's 'imported' record
  requeued: <prior state>: <reason>
                                a terminal skipped/failed job whose reason is safe to retry (a refused or
                                cut-off remote copy, a file error, a restart mid-copy, ...) goes back to
                                waiting; a mover job lost from the store (dropped as an orphan while qBit was
                                restarting, or pruned) is tracked again from the snapshot kept here; a movie
                                grab nothing imports (movie import folder not set) is handed to the mover
  library_present: <where>      no grab record, but the movie is already in the library (terminal no-op:
                                never adopted or requeued)
  skipped: <why>                queued / in flight, permanent failure, backoff, requeue limit, no grab record

and one summary line per pass.

Never a double import:
  * an idempotent key per torrent hash (movies) and per hash+file (TV episodes) in a small SQLite ledger
    (import_reconcile.db next to the library store); a requeue CLAIMS the torrent key with a conditional
    UPDATE (requeues < MAX_REQUEUES and the last one at least RETRY_AFTER_S ago), so two passes (or a pass and
    the Retry button) can't both requeue it, and each key is requeued at most MAX_REQUEUES times;
  * a waiting/queued job, an in-flight remote copy (f177d0a6: jobs[h].inflight, retry_at backoff) or an
    'importing' grab is never touched: the owner adopts its own copy;
  * the store change itself re-checks the state inside fm.update / a conditional SQL UPDATE, so a job that
    moved on between the read and the write is left alone;
  * the importers keep their own guards (exclusive create / no-replace rename / 'exists' on the same file).
Permanent failures (not a video, unsafe name, a different file already there, overlaps, no show, ...) stay
terminal; so do insufficient-space failures (library_mover.space_retry_due owns them).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

import requests

from app import file_management as fm
from app import instance, qbit_owner

logger = logging.getLogger(__name__)

INTERVAL_S = 600
STARTUP_DELAY_S = 120         # after the watchers' first passes
BACKOFF_S = (60, 300, 1800, 7200)   # wait before requeue n+1 (1m, 5m, 30m, 2h; the last repeats)
MAX_REQUEUES = 5              # then the key is 'import_failed' (push); a manual Retry is still always possible
RETRY_AFTER_S = BACKOFF_S[0]  # a terminal job is retried no sooner than this after it ended
MAX_RESEARCHES = 3            # re-searches per wanted target (episodes / movie) after lost grabs
LOST_AFTER_S = 300            # a grab qBittorrent doesn't have this long after it was added is lost
WINDOW_DAYS = 14              # completed torrents older than this are not looked at
DB_NAME = 'import_reconcile.db'

# PM spec: skipped/failed/unknown are all retryable (with BACKOFF_S, at most MAX_REQUEUES, then
# 'import_failed'). Terminal only: imported, a user dismissal (an unmonitored episode), a QA disposable
# (LUMA_QA_DISPOSABLE_TAG, default 'qa-disposable', on the torrent).
PERMANENT = ('already imported', 'episode not monitored')
QA_TAG_ENV = 'LUMA_QA_DISPOSABLE_TAG'
QA_TAG_DEFAULT = 'qa-disposable'


def backoff(n: int) -> int:
    """Seconds to wait before requeue number n+1 (n requeues so far)."""
    return BACKOFF_S[min(max(n, 0), len(BACKOFF_S) - 1)]


def qa_disposable(t: Dict[str, Any]) -> bool:
    tag = (os.environ.get(QA_TAG_ENV) or '').strip() or QA_TAG_DEFAULT
    return tag.casefold() in qbit_owner.torrent_tags(t)

_lock = threading.Lock()
_cfg: Dict[str, Any] = {}
_last = {'reachable': True}


# ------------------------------------------------------------------ ledger (the DB-level guard)

SCHEMA = """
CREATE TABLE IF NOT EXISTS reconcile_keys (
  key TEXT PRIMARY KEY, hash TEXT NOT NULL, file TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '', requeues INTEGER NOT NULL DEFAULT 0, last_requeue REAL NOT NULL DEFAULT 0,
  decision TEXT NOT NULL DEFAULT '', snapshot TEXT, updated REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS reconcile_keys_hash ON reconcile_keys (hash);
CREATE TABLE IF NOT EXISTS reconcile_researches (
  target TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0, last REAL NOT NULL DEFAULT 0
);
"""


def db_path() -> str:
    return _cfg.get('db_path') or os.path.join(os.path.dirname(str(fm.store_path())), DB_NAME)


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    p = db_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


def key_for(h: str, file: str = '') -> str:
    """The idempotent key: the torrent hash, plus the file inside it for a TV episode."""
    return f'{h.lower()}:{file}' if file else h.lower()


def _touch(conn, key: str, h: str, file: str, kind: str, decision: str, now: float,
           snapshot: Optional[str] = None) -> None:
    conn.execute('INSERT OR IGNORE INTO reconcile_keys (key, hash, file, kind, updated) VALUES (?, ?, ?, ?, ?)',
                 (key, h, file, kind, now))
    if snapshot is not None:
        conn.execute(f'UPDATE reconcile_keys SET decision = {_KEEP}, updated = ?, snapshot = ? WHERE key = ?',
                     (decision, now, snapshot, key))
    else:
        conn.execute(f'UPDATE reconcile_keys SET decision = {_KEEP}, updated = ? WHERE key = ?', (decision, now, key))


# 'import_failed' and 'lost' stick (the push is sent once); 'seen'/'missing' never overwrite them
_KEEP = "CASE WHEN decision IN ('import_failed', 'lost', 'imported') THEN decision ELSE ? END"


def claim(h: str, kind: str, now: float, files: Optional[List[str]] = None) -> Optional[str]:
    """Claim one requeue of torrent *h*. None when claimed; else why not. Atomic (BEGIN IMMEDIATE + a
    conditional UPDATE): of two concurrent passes exactly one wins. TV: every file key is claimed with it."""
    k = key_for(h)
    with _db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            conn.execute('INSERT OR IGNORE INTO reconcile_keys (key, hash, kind, updated) VALUES (?, ?, ?, ?)',
                         (k, h, kind, now))
            r = conn.execute('SELECT requeues, last_requeue FROM reconcile_keys WHERE key = ?', (k,)).fetchone()
            n = int(r['requeues'])
            wait = backoff(n - 1) if n else 0
            cur = conn.execute('UPDATE reconcile_keys SET requeues = requeues + 1, last_requeue = ?, updated = ?,'
                               " decision = 'requeued' WHERE key = ? AND requeues = ? AND requeues < ?"
                               ' AND last_requeue <= ?', (now, now, k, n, MAX_REQUEUES, now - wait))
            if cur.rowcount != 1:
                conn.execute('ROLLBACK')
                if n >= MAX_REQUEUES:
                    return IMPORT_FAILED
                return f'backoff ({wait // 60 or 1} min after requeue {n})'
            for f in files or ():
                fk = key_for(h, f)
                conn.execute('INSERT OR IGNORE INTO reconcile_keys (key, hash, file, kind, updated) VALUES (?, ?, ?, ?, ?)',
                             (fk, h, f, kind, now))
                conn.execute("UPDATE reconcile_keys SET requeues = requeues + 1, last_requeue = ?, updated = ?,"
                             " decision = 'requeued' WHERE key = ?", (now, now, fk))
            conn.execute('COMMIT')
            return None
        except Exception:
            conn.execute('ROLLBACK')
            raise


IMPORT_FAILED = 'import_failed'


def mark_import_failed(h: str, name: Any, why: str, now: float) -> bool:
    """After MAX_REQUEUES: the key becomes 'import_failed' once (True the first time: the caller pushes)."""
    with _db() as conn:
        cur = conn.execute("UPDATE reconcile_keys SET decision = ?, updated = ? WHERE key = ? AND decision != ?",
                           (IMPORT_FAILED, now, key_for(h), IMPORT_FAILED))
        first = cur.rowcount == 1
    if first:
        try:
            from app import library_notify
            library_notify._push('Import failed', f'{name or h[:8]} finished downloading but could not be imported'
                                 f' ({why})', f'import-failed-{h[:12]}', '/', '')
        except Exception:                               # noqa: BLE001
            logger.warning('Import reconcile: push failed for %s', h[:8])
    return first


IMPORTED = 'imported'


def mark_imported(h: str, now: Optional[float] = None) -> None:
    """The download is in the library (e.g. the mover found the same file already there): every key of
    the hash becomes 'imported', so no further requeue, and no import_failed push, follows."""
    now = time.time() if now is None else now
    h = str(h or '').lower()
    with _db() as conn:
        conn.execute('INSERT OR IGNORE INTO reconcile_keys (key, hash, updated) VALUES (?, ?, ?)', (key_for(h), h, now))
        conn.execute('UPDATE reconcile_keys SET decision = ?, updated = ? WHERE hash = ?', (IMPORTED, now, h))


def unclaim(h: str) -> None:
    """The claimed requeue didn't happen (the job moved on meanwhile): give the count back."""
    with _db() as conn:
        conn.execute('UPDATE reconcile_keys SET requeues = MAX(requeues - 1, 0), last_requeue = 0'
                     ' WHERE hash = ?', (h,))


def keys(h: str) -> List[Dict[str, Any]]:
    with _db() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM reconcile_keys WHERE hash = ? ORDER BY key', (h,))]


def _snapshot(h: str) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        r = conn.execute('SELECT snapshot FROM reconcile_keys WHERE key = ?', (key_for(h),)).fetchone()
    try:
        v = json.loads(r['snapshot']) if r and r['snapshot'] else None
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


# ------------------------------------------------------------------ ownership and qBittorrent

def owned_names() -> List[str]:
    out: List[str] = []
    names = [qbit_owner.tag_name()]
    try:
        from app import movie_import
        names += movie_import.categories()
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from app import tv_downloads
        names.append(tv_downloads.category())
    except Exception:                                   # noqa: BLE001
        pass
    for n in names:
        if n and n.casefold() not in {x.casefold() for x in out}:
            out.append(n)
    return out


def _other_install(t: Dict[str, Any]) -> bool:
    try:
        from app import tv_downloads
        other = tv_downloads.other_category().casefold()
    except Exception:                                   # noqa: BLE001
        return False
    return str(t.get('category') or '').strip().casefold() == other or other in qbit_owner.torrent_tags(t)


def is_owned(t: Dict[str, Any], tracked: Set[str]) -> bool:
    if not isinstance(t, dict) or _other_install(t):
        return False
    if str(t.get('hash') or '').lower() in tracked:
        return True
    names = {n.casefold() for n in owned_names()}
    return str(t.get('category') or '').strip().casefold() in names or bool(names & qbit_owner.torrent_tags(t))


def is_tv(t: Dict[str, Any]) -> bool:
    try:
        from app import tv_downloads
        return tv_downloads.is_mine(t)
    except Exception:                                   # noqa: BLE001
        return False


def complete(t: Dict[str, Any]) -> bool:
    from app import library_mover as mover
    if mover.is_paused(t):
        return False
    try:
        return float(t.get('progress') or 0) >= 1.0
    except (TypeError, ValueError):
        return False


def fetch_completed(session_factory: Callable = requests.Session, now: Optional[float] = None,
                    tracked: Optional[Set[str]] = None):
    """(torrents, list_files) or (None, None) when qBit can't be asked (the pass then decides nothing).
    One torrents/info for everything: the pass needs every hash qBit has (a grab it lost is one it doesn't)."""
    from app import library_mover as mover
    sess, url = mover._qbit_session(session_factory, now)
    if not sess:
        return None, None
    try:
        try:
            r = sess.get(f'{url}/api/v2/torrents/info', params={}, timeout=30, allow_redirects=False)
            got = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            got = None
        if not isinstance(got, list):
            return None, None
        out = [t for t in got if isinstance(t, dict) and t.get('hash')]
        files = {}
        lf = mover._files_of(sess, url)
        for t in out:
            if is_tv(t) and complete(t) and is_owned(t, tracked or set()):
                files[str(t['hash']).lower()] = lf(str(t['hash']).lower())
        return out, (lambda h: files.get(h))
    finally:
        mover._close(sess)


# ------------------------------------------------------------------ grabs qBittorrent never kept (lost)

def open_grabs(now: float) -> List[Dict[str, Any]]:
    """Grab rows that aren't imported or failed (TV grabs, movie_grabs, gr_grabs without a movie_grabs row),
    added in the last WINDOW_DAYS."""
    since = now - WINDOW_DAYS * 86400
    out: List[Dict[str, Any]] = []
    try:
        from app import tv_store
        with tv_store.connect() as conn:
            for r in conn.execute("SELECT hash, series_id, episode_ids, release_title, grabbed_at FROM grabs"
                                  " WHERE state IN ('downloading', 'importing') AND grabbed_at >= ?", (since,)):
                try:
                    eids = [int(x) for x in json.loads(r['episode_ids'] or '[]')]
                except (ValueError, TypeError):
                    eids = []
                out.append({'hash': r['hash'].lower(), 'kind': 'tv', 'name': r['release_title'], 'added': r['grabbed_at'],
                            'series_id': r['series_id'], 'episode_ids': eids})
    except Exception as e:                              # noqa: BLE001
        logger.warning('Import reconcile: TV grabs unreadable (%s)', type(e).__name__)
    movie_hashes: Set[str] = set()
    try:
        from app import movie_grabs, movie_settings as ms
        with ms.connect() as conn:
            conn.executescript(movie_grabs.SCHEMA)
            for r in conn.execute('SELECT hash, tmdb_id, release_title, state, grabbed_at FROM movie_grabs'):
                movie_hashes.add(r['hash'].lower())
                if r['state'] in ('grabbed', 'importing') and r['grabbed_at'] >= since:
                    out.append({'hash': r['hash'].lower(), 'kind': 'movie', 'name': r['release_title'],
                                'added': r['grabbed_at'], 'tmdb_id': r['tmdb_id']})
    except Exception as e:                              # noqa: BLE001
        logger.warning('Import reconcile: movie grabs unreadable (%s)', type(e).__name__)
    try:
        from app.automation import gr_store
        with gr_store.connect() as conn:
            for r in conn.execute('SELECT hash, tmdb_id, title, grabbed_at FROM gr_grabs WHERE grabbed_at >= ?', (since,)):
                if r['hash'] and r['hash'].lower() not in movie_hashes:
                    out.append({'hash': r['hash'].lower(), 'kind': 'gr', 'name': r['title'], 'added': r['grabbed_at'],
                                'tmdb_id': r['tmdb_id']})
    except Exception as e:                              # noqa: BLE001
        logger.debug('Import reconcile: GR grabs unreadable (%s)', type(e).__name__)
    return out


def grab_hashes(now: float) -> Set[str]:
    """Every hash Luma grabbed (any state) in the window: such a torrent is adopted in any category."""
    out = {g['hash'] for g in open_grabs(now)}
    since = now - WINDOW_DAYS * 86400
    try:
        from app import tv_store
        with tv_store.connect() as conn:
            out |= {r['hash'].lower() for r in conn.execute('SELECT hash FROM grabs WHERE grabbed_at >= ?', (since,))}
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from app import movie_grabs
        out |= {r['hash'].lower() for r in movie_grabs.pending()}
    except Exception:                                   # noqa: BLE001
        pass
    return out


def readd(g: Dict[str, Any], kind: str, payload: Any, session_factory: Callable = requests.Session,
          now: Optional[float] = None) -> Optional[str]:
    """Send the cached .torrent/magnet to qBittorrent again in the grab's own category (and tag).
    None when qBit took it, else why not."""
    from app import library_mover as mover, tv_grab
    from app.qbit_connector import add_ok
    if g['kind'] == 'tv':
        name, job = tv_downloads_category(), tv_grab.JOB
    else:
        from app import movie_scanner
        name, job = movie_scanner.category(), movie_scanner.JOB
    sess, url = mover._qbit_session(session_factory, now)
    if not sess:
        return 'qBittorrent not reachable'
    try:
        why = tv_grab.ensure_category(sess, url, name, job=job)
        if why:
            return why
        with instance.scoped_add(job, name):
            if kind == 'torrent':
                r = sess.post(f'{url}/api/v2/torrents/add', data=tv_grab._fields(name), timeout=30, allow_redirects=False,
                              files={'torrents': ('release.torrent', payload, 'application/x-bittorrent')})
            else:
                r = sess.post(f'{url}/api/v2/torrents/add', data={'urls': payload, **tv_grab._fields(name)},
                              timeout=30, allow_redirects=False)
        return None if add_ok(r) else f'qBittorrent refused it (HTTP {r.status_code})'
    except instance.WritesBlocked:
        return 'writes off'
    except requests.RequestException as e:
        return f'qBittorrent add failed ({type(e).__name__})'
    finally:
        mover._close(sess)


def tv_downloads_category() -> str:
    from app import tv_downloads
    return tv_downloads.category()


def research_target(g: Dict[str, Any]) -> str:
    """What a lost grab was for: the re-search counter key (a new hash for the same thing shares it)."""
    if g['kind'] == 'tv':
        return f"tv:{g.get('series_id')}:{','.join(str(i) for i in sorted(g.get('episode_ids') or []))}"
    return f"{g['kind']}:{g.get('tmdb_id')}"


def claim_research(g: Dict[str, Any], now: float) -> Optional[int]:
    """Count one re-search for *g*'s target; the new count, or None once MAX_RESEARCHES were used."""
    t = research_target(g)
    with _db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            conn.execute('INSERT OR IGNORE INTO reconcile_researches (target) VALUES (?)', (t,))
            cur = conn.execute('UPDATE reconcile_researches SET n = n + 1, last = ? WHERE target = ? AND n < ?',
                               (now, t, MAX_RESEARCHES))
            n = conn.execute('SELECT n FROM reconcile_researches WHERE target = ?', (t,)).fetchone()['n']
            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            raise
    return int(n) if cur.rowcount == 1 else None


def blocklist_lost(g: Dict[str, Any], now: float) -> bool:
    """The lost release goes on the show's / movie's blocklist (hash and release name), so the re-search
    can't pick it again. True when it is on the blocklist."""
    h, title = g['hash'], str(g.get('name') or '')
    try:
        if g['kind'] == 'tv' and g.get('series_id'):
            from app import tv_store
            with tv_store.write() as conn:
                if not conn.execute('SELECT 1 FROM blocklist WHERE info_hash = ? AND series_id = ?',
                                    (h, g['series_id'])).fetchone():
                    conn.execute('INSERT INTO blocklist (series_id, info_hash, release_title, reason, ts)'
                                 ' VALUES (?, ?, ?, ?, ?)', (g['series_id'], h, title or None, 'lost', now))
            return True
        if g['kind'] in ('movie', 'gr') and g.get('tmdb_id'):
            from app import movie_scanner, movie_settings as ms
            with ms.connect() as conn:
                movie_scanner.ensure(conn)
                if not conn.execute('SELECT 1 FROM movie_blocklist WHERE info_hash = ? AND tmdb_id = ?',
                                    (h, g['tmdb_id'])).fetchone():
                    conn.execute('INSERT INTO movie_blocklist (tmdb_id, release_title, info_hash, added_at)'
                                 ' VALUES (?, ?, ?, ?)', (g['tmdb_id'], title or None, h, now))
            return True
    except Exception as e:                              # noqa: BLE001
        logger.warning('Import reconcile: could not blocklist lost %s (%s)', h[:8], type(e).__name__)
    return False


def research_lost(g: Dict[str, Any], now: float) -> str:
    """Blocklist the lost release, then re-search (at most MAX_RESEARCHES per target)."""
    if g['kind'] not in ('tv', 'movie'):
        return research(g)                              # (GR lost grabs: the GR Failures tip)
    if not blocklist_lost(g, now):
        return 're-search skipped (the lost release could not be blocklisted)'
    n = claim_research(g, now)
    if n is None:
        return f're-search skipped (limit of {MAX_RESEARCHES} reached)'
    return f'{research(g)} (re-search {n}/{MAX_RESEARCHES})'


def research(g: Dict[str, Any]) -> str:
    """Search again for what a lost grab was for. Returns a short outcome for the log."""
    try:
        if g['kind'] == 'tv' and g.get('series_id'):
            from app import tv_search
            r = tv_search.search_show(int(g['series_id']), g.get('episode_ids') or None, origin='auto')
            picks = [p for p in (r.get('picks') or []) if p.get('grabbed')]
            return f"re-searched: {len(picks)} grabbed" if r.get('ok') else f"re-search failed: {r.get('error')}"
        if g['kind'] == 'movie' and g.get('tmdb_id'):
            from app import movie_scanner
            r = movie_scanner.search_movie(int(g['tmdb_id']), 'auto')
            return 're-searched' if r.get('ok', True) else f"re-search failed: {r.get('error')}"
    except Exception as e:                              # noqa: BLE001
        return f're-search failed ({type(e).__name__})'
    return 'no re-search for this grab type'


def _mark_lost(g: Dict[str, Any], why: str, now: float) -> bool:
    """The grab row leaves 'downloading' for good: TV grabs/movie_grabs 'failed' with error 'lost: <why>'
    (atomic: only from an open state, so a row that moved on is left alone)."""
    err = f'lost: {why}'
    if g['kind'] == 'tv':
        from app import tv_store
        with tv_store.write() as conn:
            cur = conn.execute("UPDATE grabs SET state = 'failed', error = ?, finished_at = ? WHERE hash = ?"
                               " AND state IN ('downloading', 'importing')", (err, now, g['hash']))
            return cur.rowcount == 1
    if g['kind'] == 'movie':
        from app import movie_grabs, movie_settings as ms
        with ms.connect() as conn:
            conn.executescript(movie_grabs.SCHEMA)
            cur = conn.execute("UPDATE movie_grabs SET state = 'failed', error = ?, updated_at = ? WHERE hash = ?"
                               " AND state IN ('grabbed', 'importing')", (err, now, g['hash']))
            return cur.rowcount == 1
    return True


def lost_pass(grabs: List[Dict[str, Any]], present: Set[str], healthy: bool, now: float,
              out: Dict[str, int]) -> None:
    from app import grab_cache
    for g in grabs:
        h, name = g['hash'], g.get('name')
        if h in present or now - float(g.get('added') or now) < LOST_AFTER_S:
            continue
        out['checked'] += 1
        if not healthy:
            _log(out, 'skipped', name, h, 'not in qBittorrent, but qBittorrent listed nothing (restarting?)')
            continue
        with _db() as conn:
            _touch(conn, key_for(h), h, '', g['kind'], 'missing', now)
            r = conn.execute('SELECT snapshot FROM reconcile_keys WHERE key = ?', (key_for(h),)).fetchone()
        try:
            st = json.loads(r['snapshot']) if r and r['snapshot'] else {}
        except ValueError:
            st = {}
        readded_at = float(st.get('readded_at') or 0)
        if readded_at and now - readded_at < LOST_AFTER_S:
            _log(out, 'skipped', name, h, 're-added, waiting for qBittorrent to list it')
            continue
        cached = None if readded_at else grab_cache.load(h)
        if cached:
            why = readd(g, cached[0], cached[1], now=now)
            if why is None:
                with _db() as conn:
                    conn.execute('UPDATE reconcile_keys SET snapshot = ?, decision = ? WHERE key = ?',
                                 (json.dumps({**st, 'readded_at': now}), 'readded', key_for(h)))
                _log(out, 'readded', name, h, f'not in qBittorrent {int((now - float(g["added"])) // 60)} min after the'
                                              f' grab; re-added from the cached .{cached[0]}')
                continue
            reason = f'not in qBittorrent and the re-add failed ({why})'
        elif readded_at:
            reason = 'not in qBittorrent even after a re-add'
        else:
            reason = f'not in qBittorrent {int((now - float(g["added"])) // 60)} min after the grab, no cached .torrent/magnet'
        if not _mark_lost(g, reason, now):
            _log(out, 'skipped', name, h, 'grab changed meanwhile')
            continue
        with _db() as conn:
            conn.execute("UPDATE reconcile_keys SET decision = 'lost' WHERE key = ?", (key_for(h),))
        _log(out, 'lost', name, h, f'{reason}; {research_lost(g, now)}')


# ------------------------------------------------------------------ decisions

def permanent(err: Any) -> bool:
    e = str(err or '').strip().casefold()
    if not e:
        return False
    if e == 'tv':
        return True
    return any(p in e for p in PERMANENT if p != 'tv')


def _log(out: Dict[str, int], decision: str, name: Any, h: str, why: str) -> None:
    out[decision] = out.get(decision, 0) + 1
    logger.info('Import reconcile: %s %s [%s]: %s', decision, name or '?', h[:8], why)


def _refused(out: Dict[str, int], name: Any, h: str, why: str, msg: str, now: float) -> None:
    """A claim was refused: backing off (skipped), or out of requeues (import_failed, pushed once)."""
    if why == IMPORT_FAILED:
        mark_import_failed(h, name, msg, now)
        return _log(out, IMPORT_FAILED, name, h, f'{msg} (after {MAX_REQUEUES} requeues)')
    return _log(out, 'skipped', name, h, msg)


def _in_library(j: Optional[Dict[str, Any]], g: Optional[Dict[str, Any]], data: Dict[str, Any]) -> Optional[str]:
    """Why this movie is already in the library (by its grab's path, the mover's filed folder, or another
    grab of the same TMDB id imported to a file that exists), else None."""
    if g and g.get('path') and os.path.isfile(str(g['path'])):
        return 'file in library'
    if j and j.get('dest') and j.get('path_id'):
        root = next((p['path'] for p in data.get('paths') or [] if p.get('id') == j['path_id']), None)
        if root and any(isinstance(f, dict) and os.path.isfile(os.path.join(root, j['dest'], str(f.get('file') or '')))
                        for f in j.get('files') or []):
            return 'file in library'
    tmdb = (g or {}).get('tmdb_id') or (j or {}).get('tmdb_id')
    if tmdb:
        try:
            from app import movie_grabs
            for o in movie_grabs.for_movie(int(tmdb)):
                if o.get('state') == 'imported' and o.get('path') and os.path.isfile(str(o['path'])):
                    return f"tmdb {tmdb} imported from another download"
        except Exception:                               # noqa: BLE001
            pass
    return None


def _requeue_mover(h: str, prior: Dict[str, Any], now: float) -> bool:
    """Back to waiting only if the job is still in the same terminal state (checked inside the store lock).
    inflight (a remote copy recorded by f177d0a6) is kept, so the retry adopts that copy."""
    from app import library_mover as mover

    def apply(data):
        j = data['jobs'].get(h)
        if not j or j.get('state') != prior.get('state') or (j.get('error') or '') != (prior.get('error') or ''):
            return False
        mover._requeue(j)
        j['reconcile_requeues'] = int(j.get('reconcile_requeues') or 0) + 1
        return True
    return bool(fm.update(apply))


def _movie(t: Dict[str, Any], h: str, data: Dict[str, Any], now: float, out: Dict[str, int]) -> None:
    from app import library_mover as mover
    name = t.get('name')
    j = data['jobs'].get(h)
    try:
        from app import movie_grabs
        g = movie_grabs.by_hash(h)
    except Exception:                                   # noqa: BLE001
        g = None
    if j:                                               # remember enough to track it again if the job is lost
        snap = {k: j.get(k) for k in ('tmdb_id', 'title', 'year', 'media_type', 'name')}
        with _db() as conn:
            _touch(conn, key_for(h), h, '', 'movie', 'seen', now, json.dumps(snap))
    if j and j.get('state') in ('done', 'exists'):
        if j['state'] == 'exists' and mover.exists_matches(j) and not (g and g.get('state') == 'imported'):
            mover._settle_exists(h, j, j, now)          # Ops: same file already there = imported
        elif j['state'] == 'exists' and mover.exists_matches(j):
            mark_imported(h, now)
        return _log(out, 'already_imported', name, h, f"mover job {j['state']}")
    if g and g.get('state') == 'imported':
        return _log(out, 'already_imported', name, h, 'movie grab imported')
    lib = _in_library(j, g, data)
    if lib:
        return _log(out, 'already_imported', name, h, lib)
    if j and j.get('state') == 'waiting':
        why = 'in flight (remote copy recorded, will be adopted)' if j.get('inflight') else 'queued in library mover'
        if float(j.get('retry_at') or 0) > now:
            why += ', backing off'
        return _log(out, 'skipped', name, h, why)
    if j and j.get('state') in ('failed', 'skipped'):
        err = str(j.get('error') or '')
        if err == mover.INSUFFICIENT_SPACE:
            return _log(out, 'skipped', name, h, 'insufficient space (space retry owns it)')
        if permanent(err):
            return _log(out, 'skipped', name, h, f"permanent: {j['state']}: {err}")
        if now - float(j.get('finished') or 0) < RETRY_AFTER_S:
            return _log(out, 'skipped', name, h, f"{j['state']} recently, retry after {RETRY_AFTER_S // 60} min")
        why = claim(h, 'movie', now)
        if why:
            return _refused(out, name, h, why, f"{j['state']}: {err}: {why}", now)
        if not _requeue_mover(h, j, now):
            unclaim(h)
            return _log(out, 'skipped', name, h, 'job changed meanwhile')
        return _log(out, 'requeued', name, h, f"{j['state']}: {err or 'no reason recorded'}")
    if g and g.get('state') == 'failed':
        err = str(g.get('error') or '')
        if permanent(err):
            return _log(out, 'skipped', name, h, f'permanent: failed: {err}')
        if now - float(g.get('updated_at') or 0) < RETRY_AFTER_S:
            return _log(out, 'skipped', name, h, f'failed recently, retry after {RETRY_AFTER_S // 60} min')
        why = claim(h, 'movie', now)
        if why:
            return _refused(out, name, h, why, f'failed: {err}: {why}', now)
        from app import movie_grabs
        if not movie_grabs.requeue_failed(h, expect_error=g.get('error') or '', now=now):
            unclaim(h)
            return _log(out, 'skipped', name, h, 'grab changed meanwhile')
        return _log(out, 'requeued', name, h, f'failed: {err or "no reason recorded"}')
    if g and g.get('state') in ('grabbed', 'importing'):
        from app import movie_import
        try:
            has_root = bool(movie_import.root())
        except Exception:                               # noqa: BLE001
            has_root = False
        if has_root and movie_import._is_movie(t):
            return _log(out, 'skipped', name, h, f"queued in movie import ({g['state']})")
        if qbit_owner.is_test_torrent(t):
            # Nothing would ever import it (no Movies import folder): the library mover files it by the
            # Library paths rules, like any /api/downloads/add grab. It marks this grab imported when done.
            m = movie_import._watch(int(g['tmdb_id'])) if g.get('tmdb_id') else None
            if not m or not m.get('title'):
                return _log(out, 'skipped', name, h, 'movie import folder not set and no title for the mover')
            why = claim(h, 'movie', now)
            if why:
                return _refused(out, name, h, why, f'movie import folder not set: {why}', now)
            mover.track(h, {'tmdb_id': g['tmdb_id'], 'media_type': 'movie', 'title': m['title'],
                            'year': m.get('year') or ''}, str(name or ''))
            return _log(out, 'requeued', name, h, f"grab {g['state']}: movie import folder not set, handed to the library mover")
        return _log(out, 'skipped', name, h, 'movie import folder not set and not tagged for the library mover')
    if not j:
        snap = _snapshot(h)
        if snap and snap.get('media_type', 'movie') == 'movie' and snap.get('title') and qbit_owner.is_test_torrent(t):
            why = claim(h, 'movie', now)
            if why:
                return _refused(out, name, h, why, f'mover job lost: {why}', now)
            mover.track(h, {'tmdb_id': snap.get('tmdb_id'), 'media_type': 'movie', 'title': snap['title'],
                            'year': snap.get('year') or ''}, snap.get('name') or str(name or ''))
            return _log(out, 'requeued', name, h, 'lost: mover job no longer in the store (dropped or pruned)')
    if not j and not g:
        where = library_present(t)
        if where:
            return _log(out, 'library_present', name, h, f'no grab record; already in the library ({where})')
    return _log(out, 'skipped', name, h, 'no grab record')


def library_present(t: Dict[str, Any]) -> Optional[str]:
    """The library folder that already holds the movie this torrent is (parsed from its name), else None."""
    try:
        from app import movie_dupes, movie_scanner
        title, year = movie_scanner.parse(str(t.get('name') or ''))
        if not title or not year:
            return None
        return movie_dupes.on_disk(title, year)
    except Exception:                                   # noqa: BLE001
        return None


def _video_files(listed: Optional[List[Dict[str, Any]]]) -> List[str]:
    from app import library_mover as mover
    out = []
    for f in listed or []:
        n = str((f or {}).get('name') or '') if isinstance(f, dict) else ''
        if n and mover._is_video(n) and not mover.is_sample_file(os.path.basename(n)):
            out.append(n)
    return sorted(out)


def _tv(t: Dict[str, Any], h: str, now: float, out: Dict[str, int], list_files) -> None:
    from app import library_mover as mover, tv_downloads
    name = t.get('name')
    files = _video_files(list_files(h) if list_files else None)
    st = tv_downloads.status(h) or {}
    state, err = st.get('state'), str(st.get('error') or '')
    with _db() as conn:                                 # one key per episode file
        for f in files:
            _touch(conn, key_for(h, f), h, f, 'tv', state or 'new', now)
    if state == 'imported' or (state == 'refused' and err == tv_downloads.ALREADY_IMPORTED):
        return _log(out, 'already_imported', name, h, f'tv {len(files) or "?"} file(s)')
    if state in (None, 'waiting'):
        why = 'queued in TV import' + (f' ({err})' if err else '')
        if float(st.get('next_try') or 0) > now:
            why += ', backing off'
        return _log(out, 'skipped', name, h, why)
    if state in ('failed', 'refused'):
        if err == mover.INSUFFICIENT_SPACE:
            return _log(out, 'skipped', name, h, 'insufficient space (space retry owns it)')
        if permanent(err):
            return _log(out, 'skipped', name, h, f'permanent: {state}: {err}')
        if now - float(st.get('at') or 0) < RETRY_AFTER_S:
            return _log(out, 'skipped', name, h, f'{state} recently, retry after {RETRY_AFTER_S // 60} min')
        why = claim(h, 'tv', now, files)
        if why:
            return _refused(out, name, h, why, f'{state}: {err}: {why}', now)
        cur = tv_downloads.status(h) or {}
        if cur.get('state') != state or str(cur.get('error') or '') != err or not tv_downloads.retry(h):
            unclaim(h)
            return _log(out, 'skipped', name, h, 'job changed meanwhile')
        return _log(out, 'requeued', name, h, f'{state}: {err or "no reason recorded"} ({len(files)} file(s))')
    return _log(out, 'skipped', name, h, f'unknown state {state}')


def _counts() -> Dict[str, int]:
    return {'checked': 0, 'already_imported': 0, 'requeued': 0, 'readded': 0, 'lost': 0, IMPORT_FAILED: 0,
            'library_present': 0, 'skipped': 0}


def reconcile(torrents: List[Dict[str, Any]], now: Optional[float] = None, list_files=None,
              grabs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Decide every completed Luma-owned torrent in *torrents* (all of qBittorrent's), then every open grab
    qBittorrent doesn't have. Returns the counts per decision."""
    now = time.time() if now is None else now
    out = _counts()
    data = fm.load()
    grabs = open_grabs(now) if grabs is None else grabs
    tracked = set(data['jobs']) | grab_hashes(now)
    present = {str(t.get('hash') or '').lower() for t in torrents or [] if isinstance(t, dict)}
    since = now - WINDOW_DAYS * 86400
    for t in torrents or []:
        if not isinstance(t, dict):
            continue
        h = str(t.get('hash') or '').lower()
        if not h or not is_owned(t, tracked) or not complete(t):
            continue
        done_at = t.get('completion_on')
        if isinstance(done_at, (int, float)) and 0 < done_at < since:
            continue
        out['checked'] += 1
        if qa_disposable(t):
            _log(out, 'skipped', t.get('name'), h, 'terminal: QA disposable')
            continue
        try:
            if is_tv(t):
                _tv(t, h, now, out, list_files)
            else:
                _movie(t, h, data, now, out)
        except Exception as e:                          # noqa: BLE001 - one torrent never ends the pass
            logger.exception('Import reconcile: %s failed', t.get('name') or h[:8])
            _log(out, 'skipped', t.get('name'), h, f'error {type(e).__name__}')
    try:
        lost_pass(grabs, present, bool(present), now, out)
    except Exception as e:                              # noqa: BLE001
        logger.exception('Import reconcile: lost-grab check failed')
        _log(out, 'skipped', '-', '--------', f'lost-grab check error {type(e).__name__}')
    return out


def tick(now: Optional[float] = None, session_factory: Callable = requests.Session,
         fetch: Optional[Callable[[], Any]] = None) -> Dict[str, int]:
    now = time.time() if now is None else now
    empty = _counts()
    if not instance.is_test():
        return empty
    if not _lock.acquire(blocking=False):
        logger.info('Import reconcile: pass skipped (another pass is running)')
        return empty
    try:
        if fetch is not None:
            got = fetch()
            torrents, list_files = (got if isinstance(got, tuple) else (got, None))
        else:
            torrents, list_files = fetch_completed(session_factory, now, set(fm.load()['jobs']) | grab_hashes(now))
        if torrents is None:
            _last['reachable'] = False
            logger.info('Import reconcile: pass skipped (qBittorrent not reachable)')
            return empty
        if not torrents:
            # Reviewer: an empty list (qBittorrent restarting, nothing loaded yet) is not 'reachable': the
            # loop keeps waiting for a real list and passes again once one arrives
            _last['reachable'] = False
            logger.info('Import reconcile: qBittorrent listed no torrents (restarting?)')
            return reconcile(torrents, now, list_files)
        if not _last['reachable']:
            logger.info('Import reconcile: qBittorrent is reachable again')
        _last['reachable'] = True
        out = reconcile(torrents, now, list_files)
        logger.info('Import reconcile: pass done: %d checked, %d already imported, %d requeued, %d re-added,'
                    ' %d lost, %d import failed, %d skipped, %d library present', out['checked'], out['already_imported'],
                    out['requeued'], out['readded'], out['lost'], out[IMPORT_FAILED], out['skipped'],
                    out['library_present'])
        return out
    finally:
        _lock.release()


_started = {'on': False}
_wake = threading.Event()


def poke() -> None:
    """Run a pass now (qBittorrent reconnected or restarted)."""
    _wake.set()


def start() -> bool:
    if _started['on'] or os.environ.get('LUMA_SKIP_BACKGROUND_THREADS') or not instance.is_test():
        return False
    _started['on'] = True
    try:
        from app import qbit_connector
        qbit_connector.on_verified(poke)                # a reconnect (Test with the saved credentials)
    except Exception:                                   # noqa: BLE001
        pass

    def loop():
        time.sleep(STARTUP_DELAY_S)
        reachable = True
        while True:
            _wake.clear()
            try:
                out = tick()
                ok = bool(_last['reachable'])          # an empty list is not reachable (Reviewer)
                if ok and not reachable:                # qBittorrent is back (restart/reboot): once more soon
                    _wake.set()
                reachable = ok
            except Exception as e:                      # noqa: BLE001 - the loop never dies
                logger.warning('Import reconcile pass failed: %s', type(e).__name__)
            _wake.wait(INTERVAL_S if not _wake.is_set() else LOST_AFTER_S)
    threading.Thread(target=loop, name='import-reconciler', daemon=True).start()
    logger.info('Import reconciler started (every %d min, first pass in %d s)', INTERVAL_S // 60, STARTUP_DELAY_S)
    return True
