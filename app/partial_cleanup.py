"""Luma cleans up its own stale temp partials.

Only files named exactly like Luma's copy temp ('.luma-<hex16>.luma-partial',
see library_mover.partial_name) are ever removed, and only when:
  * older than LUMA_PARTIAL_MAX_AGE_MIN minutes (default 60) by mtime,
  * not registered as in flight in this process (register()/unregister()),
  * their size/mtime did not change across a short re-check (another writer),
  * they are plain regular files reached without following any symlink,
    inside a root Luma already writes partials into (the library paths).
Never other files, never symlinks, never outside the scanned roots.
"""
from __future__ import annotations

import errno
import logging
import os
import re
import stat as _stat
import threading
import time
from typing import Callable, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

PARTIAL_RE = re.compile(r'^\.luma-[0-9a-f]{16}\.luma-partial$')
DEFAULT_MAX_AGE_MIN = 60
SWEEP_EVERY_S = 600
SETTLE_S = 2.0
MAX_DEPTH = 6   # library roots: <root>/<title folder>/<Season NN>/file

_O_DIR = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)

_inflight_lock = threading.Lock()
_inflight: set = set()


def is_partial_name(name: str) -> bool:
    return bool(PARTIAL_RE.match(name or ''))


def max_age_s() -> float:
    try:
        m = float(os.environ.get('LUMA_PARTIAL_MAX_AGE_MIN', DEFAULT_MAX_AGE_MIN))
    except ValueError:
        m = DEFAULT_MAX_AGE_MIN
    return max(1.0, m) * 60.0


def _key(path: str) -> str:
    p = os.path.abspath(path)
    return os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))


def register(path: str) -> None:
    with _inflight_lock:
        _inflight.add(_key(path))


def unregister(path: str) -> None:
    with _inflight_lock:
        _inflight.discard(_key(path))


def in_flight(path: str) -> bool:
    with _inflight_lock:
        return _key(path) in _inflight


class tracking:
    """with tracking(path): ... marks *path* as an active Luma writer."""
    def __init__(self, path: str):
        self.path = path

    def __enter__(self):
        register(self.path)
        return self

    def __exit__(self, *a):
        unregister(self.path)
        return False


def _candidates(rfd: int, root: str, depth: int, out: List[Tuple[int, str, str]], fds: List[int]) -> None:
    try:
        names = os.listdir(rfd)
    except OSError:
        return
    for n in names:
        try:
            st = os.stat(n, dir_fd=rfd, follow_symlinks=False)
        except OSError:
            continue
        if _stat.S_ISLNK(st.st_mode):
            continue                       # never follow a symlink, file or folder
        if _stat.S_ISDIR(st.st_mode):
            if depth < MAX_DEPTH:
                try:
                    cfd = os.open(n, _O_DIR, dir_fd=rfd)
                except OSError:
                    continue
                fds.append(cfd)
                _candidates(cfd, os.path.join(root, n), depth + 1, out, fds)
        elif _stat.S_ISREG(st.st_mode) and is_partial_name(n):
            out.append((rfd, root, n))


def sweep(roots: Iterable[str], *, now: Optional[float] = None, max_age: Optional[float] = None,
          settle_s: float = SETTLE_S, sleep: Callable[[float], None] = time.sleep) -> List[str]:
    """Remove stale Luma partials under *roots*; returns the removed paths."""
    age = max_age_s() if max_age is None else max_age
    removed: List[str] = []
    for root in roots:
        if not root:
            continue
        root = os.path.realpath(root)
        try:
            rfd = os.open(root, _O_DIR)
        except OSError:
            continue
        fds = [rfd]
        try:
            cands: List[Tuple[int, str, str]] = []
            _candidates(rfd, root, 0, cands, fds)
            t = time.time() if now is None else now
            stale = []
            for dfd, d, n in cands:
                p = os.path.join(d, n)
                if in_flight(p):
                    continue
                try:
                    st = os.stat(n, dir_fd=dfd, follow_symlinks=False)
                except OSError:
                    continue
                if not _stat.S_ISREG(st.st_mode) or t - st.st_mtime < age:
                    continue
                stale.append((dfd, d, n, st))
            if stale and settle_s > 0:
                sleep(settle_s)            # a writer still going changes size or mtime
            for dfd, d, n, st in stale:
                p = os.path.join(d, n)
                if in_flight(p):
                    continue
                try:
                    st2 = os.stat(n, dir_fd=dfd, follow_symlinks=False)
                except OSError:
                    continue
                if (not _stat.S_ISREG(st2.st_mode) or (st2.st_dev, st2.st_ino) != (st.st_dev, st.st_ino)
                        or st2.st_size != st.st_size or st2.st_mtime_ns != st.st_mtime_ns):
                    continue
                try:
                    os.unlink(n, dir_fd=dfd)
                except OSError as e:
                    if e.errno != errno.ENOENT:
                        logger.warning('Partial cleanup: could not remove %s (%s)', p,
                                       errno.errorcode.get(e.errno, e.errno))
                    continue
                logger.info('Partial cleanup: removed stale Luma partial %s (%d bytes, %.0f min old)',
                            p, st.st_size, (t - st.st_mtime) / 60.0)
                removed.append(p)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
    return removed


def library_roots() -> List[str]:
    """Roots Luma writes partials into: the configured library paths."""
    try:
        from app import file_management as fm
        return [p['path'] for p in fm.load().get('paths', []) if p.get('path')]
    except Exception:
        return []


_last = {'t': 0.0}


def maybe_sweep(now: Optional[float] = None) -> List[str]:
    """Called from the library mover loop: at its first pass, then every SWEEP_EVERY_S."""
    t = time.time() if now is None else now
    if _last['t'] and t - _last['t'] < SWEEP_EVERY_S:
        return []
    _last['t'] = t
    try:
        return sweep(library_roots())
    except Exception as e:
        logger.error('Partial cleanup pass failed: %s', type(e).__name__)
        return []
