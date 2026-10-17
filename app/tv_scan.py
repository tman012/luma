"""TV library scan (Phase 1): read the TV library paths, match each top-level
folder to a TMDB show, and record the shows, episodes and files in tv.db.

STRICTLY READ-ONLY on library files. This module only lists folders
(os.scandir) and stats files; it never opens, writes, renames, moves,
deletes, chmods or creates anything inside a library path (no sidecars,
.nfo or image files, no temp files). Everything it learns goes to tv.db in
the data dir. tests/test_tv_scan_readonly.py holds it to that, both by
running a scan over a fixture tree and by reading this file's calls.

* A top-level folder is a show candidate, whatever its naming
  (app/tv_match decides the show). Imported shows are not monitored.
* Files: video files anywhere under the show folder; "Season NN" folders and
  names are read with tv_release.parse_file. Each file is
    linked        its season/episode exist for the show (TMDB) and agree
                  with its Season folder,
    mismatch      it names an episode, but the numbering doesn't fit
                  (unknown episode, or the folder says another season),
    unrecognized  no episode could be read from it.
* A folder or file that is gone loses its rows (rows only). A missing or
  unreadable library path is skipped as a whole, so an unmounted share never
  empties the list.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app import tv_match, tv_release, tv_store, tv_tmdb

logger = logging.getLogger(__name__)

MAX_DEPTH = 4                    # show folder / Season / extras / ...
MAX_FILES_PER_SHOW = 5000
REFRESH_EPISODES_S = 24 * 3600
LAST_SCAN_KEY = 'last_scan'
MISSING = 'Folder not found'

_scan_lock = threading.Lock()
_state: Dict[str, Any] = {'running': False, 'error': None, 'summary': None}


# --------------------------------------------------------------------------- listing (read-only)

def _real_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), root]) == root
    except ValueError:
        return False


def show_folders(root_real: str) -> List[str]:
    """Top-level folders of a library path (no hidden ones, none that point
    outside it)."""
    out = []
    with os.scandir(root_real) as it:
        for e in it:
            if e.name.startswith('.'):
                continue
            try:
                if not e.is_dir(follow_symlinks=True):
                    continue
            except OSError:
                continue
            if e.is_symlink() and not _real_under(e.path, root_real):
                continue
            out.append(e.name)
    return sorted(out, key=str.casefold)


def video_files(show_dir: str, root_real: str) -> Iterator[Tuple[str, os.stat_result]]:
    """(path relative to the show folder, stat) for every video file under it."""
    n = 0
    stack: List[Tuple[str, str, int]] = [(show_dir, '', 0)]
    while stack:
        d, rel, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if e.name.startswith('.'):
                continue
            sub = f'{rel}/{e.name}' if rel else e.name
            try:
                if e.is_symlink() and not _real_under(e.path, root_real):
                    continue
                if e.is_dir(follow_symlinks=True):
                    if depth + 1 < MAX_DEPTH:
                        stack.append((e.path, sub, depth + 1))
                    continue
                if not e.is_file(follow_symlinks=True):
                    continue
                if os.path.splitext(e.name)[1].lower() not in tv_release.VIDEO_EXTS:
                    continue
                st = e.stat(follow_symlinks=True)
            except OSError:
                continue
            yield sub, st
            n += 1
            if n >= MAX_FILES_PER_SHOW:
                return


# --------------------------------------------------------------------------- tv.db

def _series_row(conn, path_id: str, folder: str):
    return conn.execute('SELECT * FROM series WHERE path_id = ? AND folder = ?', (path_id, folder)).fetchone()


def store_show(path_id: str, folder: str, tmdb_id: int, now: float, *, via: str,
               event: str = 'matched', series_id: Optional[int] = None) -> Optional[int]:
    """Insert (or repoint *series_id* to) the show *tmdb_id* for a folder,
    with its seasons and episodes. Returns the series id, or None when that
    show already belongs to another folder."""
    data = tv_tmdb.show(tmdb_id)
    if not data:
        return None
    fields, aliases = tv_match.show_fields(data)
    seasons = {}
    for n in tv_match.season_numbers(data):
        seasons[n] = tv_tmdb.season(tmdb_id, n) or {}
    old = None
    with tv_store.write() as conn:
        other = conn.execute('SELECT id FROM series WHERE tmdb_id = ?', (tmdb_id,)).fetchone()
        if other and other['id'] != series_id:
            return None
        cols = dict(fields, tmdb_id=tmdb_id, aliases=json.dumps(aliases), tmdb_refreshed_at=now)
        if series_id is None:
            cols.update(path_id=path_id, folder=folder, monitored=0, monitor_new_seasons=0, added_at=now)
            keys = ', '.join(cols)
            cur = conn.execute(f'INSERT INTO series ({keys}) VALUES ({", ".join("?" * len(cols))})', tuple(cols.values()))
            sid = int(cur.lastrowid)
        else:
            old = conn.execute('SELECT tmdb_id, title FROM series WHERE id = ?', (series_id,)).fetchone()
            conn.execute('DELETE FROM episodes WHERE series_id = ?', (series_id,))
            conn.execute('DELETE FROM seasons WHERE series_id = ?', (series_id,))
            conn.execute(f'UPDATE series SET {", ".join(f"{k} = ?" for k in cols)} WHERE id = ?',
                         (*cols.values(), series_id))
            sid = series_id
        _store_episodes(conn, sid, seasons)
        detail = {'via': via, 'folder': folder, 'path_id': path_id, 'tmdb_id': tmdb_id, 'title': fields['title']}
        if event == 'rematched' and old:
            detail.update(from_tmdb_id=old['tmdb_id'], from_title=old['title'])
        conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                     (now, sid, '[]', event, json.dumps(detail)))
        conn.execute('DELETE FROM unmatched WHERE path_id = ? AND folder = ?', (path_id, folder))
    return sid


def _store_episodes(conn, sid: int, seasons: Dict[int, Dict[str, Any]]) -> None:
    for n, s in seasons.items():
        conn.execute('INSERT INTO seasons (series_id, season_number, monitored, name, poster_path) VALUES (?, ?, 0, ?, ?)'
                     ' ON CONFLICT(series_id, season_number) DO UPDATE SET name = excluded.name,'
                     ' poster_path = excluded.poster_path',
                     (sid, n, str(s.get('name') or '') or None,
                      s.get('poster_path') if isinstance(s.get('poster_path'), str) else None))
        for ep in s.get('episodes') or []:
            if not isinstance(ep, dict):
                continue
            num = ep.get('episode_number')
            if not isinstance(num, int) or isinstance(num, bool) or num < 0:
                continue
            conn.execute(
                'INSERT INTO episodes (series_id, season_number, episode_number, tmdb_episode_id, title, overview,'
                ' air_date, runtime, monitored) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)'
                ' ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET'
                ' tmdb_episode_id = excluded.tmdb_episode_id, title = excluded.title, overview = excluded.overview,'
                ' air_date = excluded.air_date, runtime = excluded.runtime',
                (sid, n, num, ep.get('id') if isinstance(ep.get('id'), int) else None,
                 str(ep.get('name') or '')[:300] or None, str(ep.get('overview') or '')[:2000] or None,
                 ep.get('air_date') if isinstance(ep.get('air_date'), str) else None,
                 ep.get('runtime') if isinstance(ep.get('runtime'), int) else None))


def refresh_episodes(sid: int, tmdb_id: int, now: float) -> None:
    data = tv_tmdb.show(tmdb_id)
    if not data:
        return
    seasons = {n: tv_tmdb.season(tmdb_id, n) or {} for n in tv_match.season_numbers(data)}
    with tv_store.write() as conn:
        _store_episodes(conn, sid, seasons)
        conn.execute('UPDATE series SET tmdb_refreshed_at = ? WHERE id = ?', (now, sid))


def _store_unmatched(path_id: str, folder: str, m: Dict[str, Any], now: float, reason: Optional[str] = None) -> None:
    with tv_store.write() as conn:
        conn.execute('INSERT INTO unmatched (path_id, folder, guess_title, guess_year, candidates, reason, ts)'
                     ' VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path_id, folder) DO UPDATE SET'
                     ' guess_title = excluded.guess_title, guess_year = excluded.guess_year,'
                     ' candidates = excluded.candidates, reason = excluded.reason, ts = excluded.ts',
                     (path_id, folder, m['guess']['title'], m['guess']['year'],
                      json.dumps(m.get('candidates') or []), reason or m.get('reason') or 'not_exact', now))


# --------------------------------------------------------------------------- files

def classify(conn, sid: int, rel_in_show: str) -> Tuple[str, tv_release.Release, List[int]]:
    """(state, parsed, episode ids) for one file of show *sid*."""
    name = rel_in_show.rsplit('/', 1)[-1]
    r = tv_release.parse_file(name, rel_in_show)
    if r.kind in ('episode', 'multi') and r.season is not None and r.episodes:
        folder_season = tv_release.season_from_folder(rel_in_show)
        ids = []
        for e in r.episodes:
            row = conn.execute('SELECT id FROM episodes WHERE series_id = ? AND season_number = ? AND episode_number = ?',
                               (sid, r.season, e)).fetchone()
            ids.append(row['id'] if row else None)
        if (folder_season is not None and folder_season != r.season) or None in ids:
            return 'mismatch', r, []
        return 'linked', r, ids
    if r.kind == 'daily' and r.air_date:
        row = conn.execute('SELECT id FROM episodes WHERE series_id = ? AND air_date = ?', (sid, r.air_date)).fetchone()
        return ('linked', r, [row['id']]) if row else ('mismatch', r, [])
    return 'unrecognized', r, []


def scan_files(sid: int, path_id: str, folder: str, root_real: str, now: float) -> Dict[str, int]:
    show_dir = os.path.join(root_real, folder)
    found = list(video_files(show_dir, root_real))
    counts = {'linked': 0, 'mismatch': 0, 'unrecognized': 0}
    with tv_store.write() as conn:
        keep = set()
        for rel, st in found:
            rel_path = f'{folder}/{rel}'
            keep.add(rel_path)
            state, r, ids = classify(conn, sid, rel)
            counts[state] += 1
            parsed = json.dumps({'summary': r.summary(), 'kind': r.kind, 'season': r.season, 'episodes': r.episodes,
                                 'air_date': r.air_date})
            conn.execute(
                'INSERT INTO episode_files (series_id, path_id, rel_path, size, mtime, dev, ino, resolution,'
                ' release_group, proper, origin, added_at, state, parsed)'
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scan', ?, ?, ?)"
                ' ON CONFLICT(path_id, rel_path) DO UPDATE SET series_id = excluded.series_id, size = excluded.size,'
                ' mtime = excluded.mtime, dev = excluded.dev, ino = excluded.ino, resolution = excluded.resolution,'
                ' release_group = excluded.release_group, proper = excluded.proper, state = excluded.state,'
                ' parsed = excluded.parsed',
                (sid, path_id, rel_path, st.st_size, st.st_mtime, st.st_dev, st.st_ino, r.resolution or None,
                 r.group or None, int(r.proper or 0), now, state, parsed))
            fid = conn.execute('SELECT id FROM episode_files WHERE path_id = ? AND rel_path = ?',
                               (path_id, rel_path)).fetchone()['id']
            conn.execute('DELETE FROM episode_file_links WHERE file_id = ?', (fid,))
            for eid in ids:
                conn.execute('INSERT OR IGNORE INTO episode_file_links (file_id, episode_id) VALUES (?, ?)', (fid, eid))
        for row in conn.execute('SELECT id, rel_path FROM episode_files WHERE series_id = ?', (sid,)).fetchall():
            if row['rel_path'] not in keep:
                conn.execute('DELETE FROM episode_files WHERE id = ?', (row['id'],))
        conn.execute('UPDATE series SET last_scan_at = ? WHERE id = ?', (now, sid))
    return counts


# --------------------------------------------------------------------------- scan

def scan_path(rec: Dict[str, Any], now: float, retry_unmatched: bool = True) -> Dict[str, int]:
    from app import file_management as fm
    root_real = os.path.realpath(rec['path'])
    if fm.blocked(root_real) or not os.path.isdir(root_real):
        raise FileNotFoundError(MISSING)
    pid = rec['id']
    folders = show_folders(root_real)
    out = {'shows': 0, 'unmatched': 0, 'linked': 0, 'mismatch': 0, 'unrecognized': 0}
    with tv_store.connect() as conn:
        known = {r['folder']: dict(r) for r in conn.execute('SELECT id, folder, tmdb_id, tmdb_refreshed_at FROM series'
                                                              ' WHERE path_id = ?', (pid,))}
        unmatched = {r['folder'] for r in conn.execute('SELECT folder FROM unmatched WHERE path_id = ?', (pid,))}
    with tv_store.write() as conn:            # folders that are gone: rows only
        for f in set(known) - set(folders):
            conn.execute('DELETE FROM series WHERE path_id = ? AND folder = ?', (pid, f))
        for f in unmatched - set(folders):
            conn.execute('DELETE FROM unmatched WHERE path_id = ? AND folder = ?', (pid, f))
    for folder in folders:
        row = known.get(folder)
        if row is None:
            if folder in unmatched and not retry_unmatched:
                out['unmatched'] += 1
                continue
            m = tv_match.match(folder)
            sid = None
            if m['status'] == 'matched':
                sid = store_show(pid, folder, m['tmdb_id'], now, via=m['via'])
                if sid is None:
                    taken = [b for b in [tv_tmdb.brief({'id': m['tmdb_id'], 'name': m['guess']['title']})] if b]
                    _store_unmatched(pid, folder, dict(m, candidates=taken), now, reason='duplicate')
            else:
                _store_unmatched(pid, folder, m, now)
            if sid is None:
                out['unmatched'] += 1
                continue
        else:
            sid = row['id']
            if now - (row['tmdb_refreshed_at'] or 0) >= REFRESH_EPISODES_S:
                refresh_episodes(sid, row['tmdb_id'], now)
        out['shows'] += 1
        for k, v in scan_files(sid, pid, folder, root_real, now).items():
            out[k] += v
    return out


def scan_all(now: Optional[float] = None, retry_unmatched: bool = True) -> Dict[str, Any]:
    """Scan every TV library path. Raises TmdbError when TMDB can't be asked
    (nothing is half-recorded for the folder in hand)."""
    from app import tv_settings
    now = time.time() if now is None else now
    total = {'paths': 0, 'shows': 0, 'unmatched': 0, 'linked': 0, 'mismatch': 0, 'unrecognized': 0, 'missing': []}
    for rec in tv_settings.load()['paths']:
        try:
            counts = scan_path(rec, now, retry_unmatched)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            total['missing'].append(rec['id'])
            continue
        total['paths'] += 1
        for k, v in counts.items():
            total[k] += v
    tv_store.set_meta(LAST_SCAN_KEY, repr(now))
    return total


def last_scan() -> Optional[float]:
    try:
        v = tv_store.get_meta(LAST_SCAN_KEY)
        return float(v) if v else None
    except (ValueError, TypeError):
        return None


def run(retry_unmatched: bool = True, now: Optional[float] = None) -> bool:
    """One scan, unless one is running (False then). Records the outcome for
    the Settings view."""
    if not _scan_lock.acquire(blocking=False):
        return False
    _state.update(running=True, error=None)
    try:
        _state['summary'] = scan_all(now, retry_unmatched)
    except tv_tmdb.TmdbError as e:
        _state['error'] = str(e)
        logger.warning('TV scan stopped: %s', e)
    except Exception as e:
        _state['error'] = 'Scan failed'
        logger.error('TV scan failed: %s', type(e).__name__, exc_info=True)
    finally:
        _state['running'] = False
        _scan_lock.release()
    return True


def start(retry_unmatched: bool = True) -> bool:
    """Scan in the background; False when one is already running."""
    if _scan_lock.locked():
        return False
    threading.Thread(target=run, kwargs={'retry_unmatched': retry_unmatched}, daemon=True,
                     name='tv-scan').start()
    return True


def status() -> Dict[str, Any]:
    return {'running': bool(_state['running'] or _scan_lock.locked()), 'error': _state['error'],
            'last_scan': last_scan(), 'summary': _state['summary']}


# --------------------------------------------------------------------------- scheduled rescan

TICK_S = 300                    # how often the scheduler looks; scans run every rescan_interval_h
_sched_stop = threading.Event()
_sched_thread: Optional[threading.Thread] = None


def due(now: float) -> bool:
    """A scheduled scan is due: TV paths exist and the last scan is older
    than rescan_interval_h (or there never was one)."""
    from app import tv_settings
    st = tv_settings.load()
    if not st['paths']:
        return False
    last = last_scan()
    return last is None or now - last >= st['rescan_interval_h'] * 3600


def tick(now: Optional[float] = None) -> bool:
    """One scheduler step: scan when due. Scheduled scans keep unmatched
    folders as they are (a manual Scan asks TMDB about them again)."""
    now = time.time() if now is None else now
    try:
        if not due(now):
            return False
    except Exception as e:
        logger.warning('TV rescan check failed: %s', type(e).__name__)
        return False
    return run(retry_unmatched=False, now=now)


def _loop() -> None:
    while not _sched_stop.wait(TICK_S):
        tick()


def start_scheduler() -> bool:
    """Start the rescan thread once (main.start_services, minimal builds).
    Idle until TV paths are saved."""
    global _sched_thread
    if _sched_thread is not None and _sched_thread.is_alive():
        return False
    _sched_stop.clear()
    _sched_thread = threading.Thread(target=_loop, daemon=True, name='tv-rescan')
    _sched_thread.start()
    return True


def stop_scheduler() -> None:
    _sched_stop.set()


# --------------------------------------------------------------------------- fix-match

NOT_FOUND = 'Not found'
SHOW_NOT_FOUND = 'Show not found on TMDB'
TAKEN = 'That show is already matched to another folder'
BUSY = 'A scan is already running'
LOCK_WAIT_S = 30


def _root_for(path_id: str) -> Optional[str]:
    from app import file_management as fm, tv_settings
    for rec in tv_settings.load()['paths']:
        if rec['id'] == path_id:
            root = os.path.realpath(rec['path'])
            return root if not fm.blocked(root) and os.path.isdir(root) else None
    return None


def _apply(path_id: str, folder: str, tmdb_id: int, now: float, *, via: str, event: str,
           series_id: Optional[int]) -> Dict[str, Any]:
    with tv_store.connect() as conn:
        other = conn.execute('SELECT id FROM series WHERE tmdb_id = ?', (tmdb_id,)).fetchone()
    if other and other['id'] != series_id:
        return {'ok': False, 'error': TAKEN, 'status': 409}
    if not tv_tmdb.show(tmdb_id):
        return {'ok': False, 'error': SHOW_NOT_FOUND, 'status': 404}
    sid = store_show(path_id, folder, tmdb_id, now, via=via, event=event, series_id=series_id)
    if sid is None:
        return {'ok': False, 'error': TAKEN, 'status': 409}
    root = _root_for(path_id)
    if root is not None:
        scan_files(sid, path_id, folder, root, now)
    return {'ok': True, 'series_id': sid}


def _locked(fn):
    def wrapped(*a, **k):
        if not _scan_lock.acquire(timeout=LOCK_WAIT_S):
            return {'ok': False, 'error': BUSY, 'status': 409}
        try:
            return fn(*a, **k)
        finally:
            _scan_lock.release()
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


@_locked
def rematch(series_id: int, tmdb_id: int, now: Optional[float] = None) -> Dict[str, Any]:
    """"Wrong show?": point a matched folder at another show (history: rematched)."""
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        row = conn.execute('SELECT path_id, folder, tmdb_id FROM series WHERE id = ? AND path_id IS NOT NULL',
                           (series_id,)).fetchone()
    if not row:
        return {'ok': False, 'error': NOT_FOUND, 'status': 404}
    return _apply(row['path_id'], row['folder'], tmdb_id, now, via='manual', event='rematched',
                  series_id=series_id)


@_locked
def match_unmatched(unmatched_id: int, tmdb_id: int, now: Optional[float] = None) -> Dict[str, Any]:
    """"Match": give an unmatched folder its show (history: matched, via manual)."""
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        row = conn.execute('SELECT path_id, folder FROM unmatched WHERE id = ?', (unmatched_id,)).fetchone()
    if not row:
        return {'ok': False, 'error': NOT_FOUND, 'status': 404}
    return _apply(row['path_id'], row['folder'], tmdb_id, now, via='manual', event='matched', series_id=None)


# --------------------------------------------------------------------------- view

def library_view() -> Dict[str, Any]:
    from app import tv_settings
    names = {p['id']: p['name'] for p in tv_settings.load()['paths']}
    with tv_store.connect() as conn:
        shows = []
        for r in conn.execute(
                'SELECT s.id, s.tmdb_id, s.title, s.year, s.path_id, s.folder, s.poster_path, s.monitored,'
                " SUM(f.state = 'linked') AS linked, SUM(f.state = 'mismatch') AS mismatch,"
                " SUM(f.state = 'unrecognized') AS unrecognized, COUNT(f.id) AS files"
                ' FROM series s LEFT JOIN episode_files f ON f.series_id = s.id'
                ' WHERE s.path_id IS NOT NULL GROUP BY s.id ORDER BY s.title COLLATE NOCASE'):
            shows.append({'id': r['id'], 'tmdb_id': r['tmdb_id'], 'title': r['title'], 'year': r['year'],
                          'path_id': r['path_id'], 'path_name': names.get(r['path_id'], ''), 'folder': r['folder'],
                          'poster_path': r['poster_path'], 'monitored': bool(r['monitored']),
                          'files': r['files'] or 0, 'linked': r['linked'] or 0, 'mismatch': r['mismatch'] or 0,
                          'unrecognized': r['unrecognized'] or 0})
        unmatched = []
        for r in conn.execute('SELECT * FROM unmatched ORDER BY folder COLLATE NOCASE'):
            try:
                cands = json.loads(r['candidates'] or '[]')
            except ValueError:
                cands = []
            unmatched.append({'id': r['id'], 'path_id': r['path_id'], 'path_name': names.get(r['path_id'], ''),
                              'folder': r['folder'], 'guess_title': r['guess_title'], 'guess_year': r['guess_year'],
                              'candidates': cands[:tv_match.TOP_N], 'reason': r['reason']})
    return {'shows': shows, 'unmatched': unmatched, 'scan': status()}
