"""New files in TV folders get linked as they land (tv.db only; files are only
read: stat and directory listings).

* link_path(path): Luma's own mover/importer just placed a TV file: link that
  show now.
* poll(): every POLL_S, compare the directory mtimes of the library shows'
  folders (and their season folders) with the last pass and rescan only the
  shows whose folders changed. A directory's mtime moves when a file is added,
  removed or renamed in it, so a landed file is seen within one poll; this
  works on network mounts, where inotify sees nothing.
* inotify (Linux, best effort): watches the show and season folders and links a
  show about a second after a file lands there; any failure (no inotify, a
  network mount, the watch limit) leaves the poll alone.

Every link pass runs under tv_scan's scan lock or is skipped (an Import or
Calendar refresh holds it; the next poll tries again). The boot pass (start())
and the Calendar-open pass are the safety nets.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import select
import struct
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from app import tv_scan, tv_store, tv_tmdb

logger = logging.getLogger(__name__)

POLL_S = 60.0
INOTIFY_DEBOUNCE_S = 1.0
MAX_DEPTH = 3                        # show/Season/extra: the same depth video_files reads

# path_id/folder -> {dir path: mtime_ns}; the dirs under a show seen at its last listing
_sigs: Dict[Tuple[str, str], Dict[str, int]] = {}
_lock = threading.Lock()
_started = {'poll': False, 'inotify': False}
_Thread = threading.Thread
_sleep: Callable[[float], None] = time.sleep


def _shows() -> List[Dict[str, object]]:
    """The selected libraries' folder shows: {id, path_id, folder, root}."""
    out = []
    roots = {}
    from app import tv_settings
    for rec in tv_settings.libraries():
        r = tv_scan._root_for(rec['id'])
        if r:
            roots[rec['id']] = r
    if not roots:
        return out
    with tv_store.connect() as conn:
        for row in conn.execute('SELECT id, path_id, folder, tmdb_id FROM series WHERE folder IS NOT NULL AND path_id IN'
                                f' ({",".join("?" * len(roots))})', list(roots)):
            out.append({'id': row['id'], 'path_id': row['path_id'], 'folder': row['folder'], 'tmdb_id': row['tmdb_id'],
                        'root': roots[row['path_id']]})
    return out


def _dirs(show_dir: str, old: Optional[Dict[str, int]]) -> Optional[Dict[str, int]]:
    """{dir: mtime_ns} for the show folder and the folders under it. Only the
    folders whose mtime changed since *old* are listed again (a stat each for
    the rest). None when the show folder is gone."""
    try:
        top = os.stat(show_dir).st_mtime_ns
    except OSError:
        return None
    sig: Dict[str, int] = {}
    stack = [(show_dir, top, 0)]
    while stack:
        d, m, depth = stack.pop()
        sig[d] = m
        if old is not None and old.get(d) == m:
            # unchanged: its subfolders are the ones seen last time (stat them, don't list)
            subs = [k for k in old if os.path.dirname(k) == d]
            for k in subs:
                try:
                    stack.append((k, os.stat(k).st_mtime_ns, depth + 1))
                except OSError:
                    pass
            continue
        if depth >= MAX_DEPTH:
            continue
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.name.startswith('.'):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append((e.path, e.stat(follow_symlinks=False).st_mtime_ns, depth + 1))
                    except OSError:
                        pass
        except OSError:
            pass
    return sig


def _new_mismatch(sid: int, now: float) -> bool:
    """A file that just landed for an episode tv.db doesn't know yet (TMDB added it after the last refresh)."""
    with tv_store.connect() as conn:
        return conn.execute("SELECT 1 FROM episode_files WHERE series_id = ? AND state = 'mismatch' AND added_at >= ?",
                            (sid, now)).fetchone() is not None


def _link_shows(shows: List[Dict[str, object]], now: float) -> int:
    n = 0
    for s in shows:
        try:
            sid = int(s['id'])
            tv_scan.scan_files(sid, str(s['path_id']), str(s['folder']), str(s['root']), now)
            if s.get('tmdb_id') and _new_mismatch(sid, now):
                # read the show again (fresh, background budget) and link once more: a late-listed episode
                with tv_tmdb.fresh(), tv_tmdb.background():
                    tv_scan.refresh_episodes(sid, int(s['tmdb_id']), now)
                tv_scan.scan_files(sid, str(s['path_id']), str(s['folder']), str(s['root']), now)
                logger.info('TV link: %s re-read for a new episode', s['folder'])
            n += 1
        except Exception:                         # noqa: BLE001 - one show's trouble doesn't stop the rest
            logger.exception('TV link failed for one show')
    return n


def changed_shows(prime: bool = False) -> List[Dict[str, object]]:
    """The shows whose folders changed since the last call (every show the first time, unless *prime*)."""
    out = []
    seen = set()
    for s in _shows():
        key = (str(s['path_id']), str(s['folder']))
        seen.add(key)
        old = _sigs.get(key)
        sig = _dirs(os.path.join(str(s['root']), str(s['folder'])), old)
        if sig is None:
            continue
        _sigs[key] = sig
        if (old is None and not prime) or (old is not None and old != sig):
            out.append(s)
    for key in list(_sigs):
        if key not in seen:
            del _sigs[key]
    return out


def poll(now: Optional[float] = None, prime: bool = False) -> Optional[int]:
    """One pass: link the shows whose folders changed. None when skipped (the scan lock is busy)."""
    if not tv_scan._scan_lock.acquire(blocking=False):
        return None
    try:
        with _lock:
            t0 = time.monotonic()
            shows = changed_shows(prime=prime)
            n = _link_shows(shows, time.time() if now is None else now)
        if n:
            logger.info('TV link: %d show(s) with new or changed files linked in %.2fs', n, time.monotonic() - t0)
        return n
    finally:
        tv_scan._scan_lock.release()


OPEN_DEBOUNCE_S = 15.0
_last_open = [0.0]


def poll_soon() -> Optional[int]:
    """The Calendar-open pass (debounced to once every OPEN_DEBOUNCE_S)."""
    now = time.monotonic()
    if now - _last_open[0] < OPEN_DEBOUNCE_S:
        return None
    _last_open[0] = now
    return poll()


def link_path(path: str, now: Optional[float] = None) -> bool:
    """Luma's mover/importer just placed *path* (a file or folder): link its show now.
    False when no library show holds it or the scan lock is busy (the poll catches it)."""
    real = os.path.realpath(path)
    for s in _shows():
        show_dir = os.path.realpath(os.path.join(str(s['root']), str(s['folder'])))
        try:
            inside = os.path.commonpath([real, show_dir]) == show_dir
        except ValueError:
            inside = False
        if not inside:
            continue
        if not tv_scan._scan_lock.acquire(timeout=5):
            return False
        try:
            with _lock:
                ok = _link_shows([s], time.time() if now is None else now) == 1
                _sigs.pop((str(s['path_id']), str(s['folder'])), None)
                sig = _dirs(show_dir, None)
                if sig is not None:
                    _sigs[(str(s['path_id']), str(s['folder']))] = sig
            logger.info('TV link: a new file in %s linked (mover)', s['folder'])
            return ok
        finally:
            tv_scan._scan_lock.release()
    return False


# ------------------------------------------------------------------ inotify (best effort)

_IN_FLAGS = 0x00000100 | 0x00000080 | 0x00000008 | 0x00000200 | 0x00000040   # CREATE, MOVED_TO, CLOSE_WRITE, DELETE, MOVED_FROM
_IN_Q_OVERFLOW = 0x00004000


class _Inotify:
    def __init__(self):
        name = ctypes.util.find_library('c')
        self.libc = ctypes.CDLL(name or 'libc.so.6', use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), 'inotify_init1')
        self.wd: Dict[int, Tuple[str, str]] = {}

    def add(self, d: str, key: Tuple[str, str]) -> None:
        w = self.libc.inotify_add_watch(self.fd, os.fsencode(d), _IN_FLAGS)
        if w < 0:
            raise OSError(ctypes.get_errno(), 'inotify_add_watch')
        self.wd[w] = key

    def read(self, timeout: float) -> Tuple[set, bool]:
        """(keys with events, overflowed)."""
        r, _, _ = select.select([self.fd], [], [], timeout)
        keys, over = set(), False
        if not r:
            return keys, over
        buf = os.read(self.fd, 64 * 1024)
        i = 0
        while i + 16 <= len(buf):
            wd, mask, _cookie, ln = struct.unpack_from('iIII', buf, i)
            i += 16 + ln
            if mask & _IN_Q_OVERFLOW:
                over = True
            elif wd in self.wd:
                keys.add(self.wd[wd])
        return keys, over

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


def _network_fs(path: str) -> bool:
    """A network mount (nfs, cifs/smb, fuse...), where inotify misses remote writes."""
    try:
        best, fstype = '', ''
        with open('/proc/mounts') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and path.startswith(parts[1].replace('\\040', ' ')) and len(parts[1]) > len(best):
                    best, fstype = parts[1], parts[2]
        return fstype.startswith(('nfs', 'cifs', 'smb', 'fuse', '9p', 'sshfs'))
    except OSError:
        return False


def _inotify_loop(stop: threading.Event) -> None:
    try:
        ino = _Inotify()
    except (OSError, AttributeError) as e:
        logger.info('TV link: inotify unavailable (%s); polling every %ds', type(e).__name__, int(POLL_S))
        return
    try:
        watched = 0
        for (pid, folder), sig in list(_sigs.items()):
            for d in sig:
                if _network_fs(d):
                    continue
                ino.add(d, (pid, folder))
                watched += 1
        logger.info('TV link: inotify on %d folder(s), plus the %ds poll', watched, int(POLL_S))
        pending: Dict[Tuple[str, str], float] = {}
        while not stop.is_set():
            keys, over = ino.read(0.5)
            now = time.monotonic()
            for k in keys:
                pending.setdefault(k, now)
            if over:
                return                                   # the poll catches up
            due = [k for k, t in pending.items() if now - t >= INOTIFY_DEBOUNCE_S]
            if not due:
                continue
            for k in due:
                pending.pop(k, None)
            shows = [s for s in _shows() if (str(s['path_id']), str(s['folder'])) in due]
            if shows and tv_scan._scan_lock.acquire(blocking=False):
                try:
                    with _lock:
                        n = _link_shows(shows, time.time())
                        for s in shows:
                            key = (str(s['path_id']), str(s['folder']))
                            sig = _dirs(os.path.join(str(s['root']), str(s['folder'])), None)
                            if sig is not None:
                                for d in set(sig) - set(_sigs.get(key, {})):   # a new season folder: watch it too
                                    try:
                                        ino.add(d, key)
                                    except OSError:
                                        pass
                                _sigs[key] = sig
                    logger.info('TV link: %d show(s) linked (inotify)', n)
                finally:
                    tv_scan._scan_lock.release()
            elif shows:
                for k in due:
                    pending[k] = now                     # busy: try again shortly
    except OSError as e:
        logger.info('TV link: inotify stopped (%s); polling every %ds', type(e).__name__, int(POLL_S))
    finally:
        ino.close()


# ------------------------------------------------------------------ boot

_stop = threading.Event()


def start() -> bool:
    """Boot: one full link pass (background), then the poll every POLL_S and inotify where it works."""
    if _started['poll'] or os.environ.get('LUMA_SKIP_BACKGROUND_THREADS'):
        return False
    _started['poll'] = True

    def loop():
        first = True
        while not _stop.is_set():
            try:
                n = poll()
                if first and n is not None:
                    logger.info('TV link: boot pass done (%d show(s) read)', n)
                    first = False
                    if not _started['inotify']:
                        _started['inotify'] = True
                        _Thread(target=_inotify_loop, args=(_stop,), daemon=True, name='tv-link-inotify').start()
            except Exception:                    # noqa: BLE001 - the next poll tries again
                logger.exception('TV link poll failed')
            _stop.wait(POLL_S if not first else 10.0)
    _Thread(target=loop, daemon=True, name='tv-link-poll').start()
    _Thread(target=plex_loop, args=(_stop,), daemon=True, name='tv-plex-poll').start()
    return True


PLEX_POLL_S = 60.0


def plex_tick() -> int:
    """Plex, the primary live source: recently added every minute (a full set every 30 min).
    However a file arrived, Plex has it within its own scan; this picks it up within a minute."""
    from app import tv_plex
    try:
        n = tv_plex.rebuild()
        n = tv_plex.recent() if n < 0 else n
        new, tv_plex._new[:] = list(tv_plex._new), []
        _read_unknown(new)
        return n
    except Exception:                        # noqa: BLE001 - the next tick tries again
        logger.exception('TV Plex poll failed')
        return -1


PLEX_REREAD_MAX = 5


def _read_unknown(keys) -> None:
    """Plex has an episode tv.db doesn't list yet (TMDB added it after the last refresh):
    read that show again (fresh, background budget) so its card exists. A few shows a tick."""
    want: Dict[int, int] = {}
    with tv_store.connect() as conn:
        for kind, sid_ext, s, e in keys:
            col = 'tmdb_id' if kind == 'tmdb' else 'tvdb_id'
            row = conn.execute(f'SELECT id, tmdb_id FROM series WHERE {col} = ? AND tmdb_id IS NOT NULL', (sid_ext,)).fetchone()
            if row is None or row['id'] in want:
                continue
            if not conn.execute('SELECT 1 FROM episodes WHERE series_id = ? AND season_number = ? AND episode_number = ?',
                                (row['id'], s, e)).fetchone():
                want[row['id']] = row['tmdb_id']
    for sid, tid in list(want.items())[:PLEX_REREAD_MAX]:
        try:
            with tv_tmdb.fresh(), tv_tmdb.background():
                tv_scan.refresh_episodes(sid, int(tid), time.time())
            logger.info('TV Plex: show %d re-read for a newly added episode', sid)
        except Exception as e:               # noqa: BLE001
            logger.info('TV Plex: re-read failed (%s)', type(e).__name__)


def plex_loop(stop: threading.Event) -> None:
    """Its own thread: a slow folder poll or an import holding the scan lock never delays Plex."""
    while not stop.is_set():
        plex_tick()
        stop.wait(PLEX_POLL_S)


def episode_imported(path: str) -> bool:
    """The import hook: Luma's download pipeline calls this when it has finished placing a TV
    episode file at *path* (TV downloads aren't wired yet; the mover calls it today). Links that
    episode at once in tv.db (read-only on the file); False when no library show holds the
    path or an import holds the lock (the 60s poll then catches it)."""
    try:
        return link_path(path)
    except Exception:                        # noqa: BLE001 - never fail the pipeline over a link
        logger.exception('TV link failed for an imported file')
        return False
