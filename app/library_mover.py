"""Library mover for LUMA_MINIMAL builds: files each finished grab into the
folder Settings > Library paths picks for it.

Ported from prod's AutoRenamer (app/auto_rename.py): the completed-state
check, find_video_files (samples, screeners, under-100 MB extras, unpack locks,
unstable files), select_main_video_files, the paced copy with a size check,
and "Title (Year)/Title (Year).ext" naming. Rulings for luma-test:

* Only torrents this install grabbed: tracked by hash at grab time AND tagged
  with the install's qBittorrent tag (app/qbit_owner). It asks qBittorrent
  about its own tracked hashes only (torrents/info?hashes=...), never lists the
  shared client, so production's torrents are never read or copied.
* No library paths: qBittorrent is not contacted and nothing moves.
* Copy by default (the download keeps seeding); Hardlink when chosen, falling
  back to a copy when the link fails. Never a move.
* A file already at the destination is skipped and recorded as 'exists'.
* A copy that won't fit in the library folder is skipped and recorded as
  'insufficient space', never retried.
* Files 664, created folders 775; when running as root, both are chowned to
  the owner of the library path. No chmod 777. On a successful file into the
  library (state 'done'), library_notify fires prod AutoRenamer's Web Push + bell.
* Anything under <downloads>/incomplete/ or named *.!qB is never processed.
* A tracked torrent that is gone from qBittorrent is dropped after a few
  checks; one that isn't ours is dropped at once.
* Movies only for now; TV grabs are recorded as skipped (later pass).
* Sources are read only from inside the downloads folder
  (file_management.downloads_root, LUMA_DOWNLOADS_ROOT), whatever save path
  qBittorrent reports. A save path outside it waits for a path map and is
  marked 'files not found' after NOT_FOUND_AFTER_S. Each source is opened
  O_NOFOLLOW and checked by inode against what was inspected; the library
  folder is worked through directory descriptors (no symlink is ever
  followed, chmod/chown act on open descriptors) and a file is put in place
  with RENAME_NOREPLACE where the kernel has it.
* qBittorrent's "no subfolder" layout (content path == save path) files only
  the torrent's own files, as listed by torrents/files.
"""

from __future__ import annotations
from app.err_text import public as _err_public, remote_reason as _remote_reason

import errno
import logging
import os
import re
import stat as _stat
import threading
import time
from datetime import date
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

import requests

from app import file_management as fm
from app import partial_cleanup
from app import qbit_owner
from app.paced_copy import _paced_copy

logger = logging.getLogger(__name__)

_cfg: Dict[str, Any] = {}
_thread: Optional[threading.Thread] = None
_start_lock = threading.Lock()

INTERVAL_S = 30
BATCH = 50
ORPHAN_MISSES = 3          # checks in a row a tracked torrent may be missing
ORPHAN_MIN_S = 1800        # ...and for at least this long: a qBit restart/reboot lists nothing for a while
MAX_ATTEMPTS = 5           # failed transfers before a job is marked failed
RELEASE_WAIT_S = 600       # how long a missing TMDB date may hold a job
EXTRACT_WAIT_S = 3600      # prod: wait up to an hour for an unpack
MIN_VIDEO_BYTES = 100 * 1024 * 1024
STABLE_WAIT_S = 3          # prod's is_file_stable window
MAX_JOBS_WAITING = 500
# Prod's completed states, plus qBittorrent 5's names for them. Paused/stopped torrents are never
# imported (PM): a user pausing a torrent is how they say "don't file this".
PAUSED_STATES = frozenset(('pausedUP', 'pausedDL', 'stoppedUP', 'stoppedDL'))  # qBit v4 paused*, v5 stopped*
COMPLETED_STATES = ('completed', 'stalledUP', 'uploading', 'queuedUP', 'forcedUP')


def is_paused(t: Dict[str, Any]) -> bool:
    return str(t.get('state') or '') in PAUSED_STATES
VIDEO_EXTS = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.m2ts')
_HASH_RE = re.compile(r'^([0-9a-f]{40}|[0-9a-f]{64})$')
TERMINAL = ('done', 'exists', 'failed', 'skipped')


NOT_WRITABLE = 'library folder not writable'
NO_WRITABLE_DEST = 'no writable destination'   # v169: flagged on the Downloads item right away
NO_DESTINATION = 'no destination'              # v171: no rule matched and no fallback (shown on the item)


class Pending(Exception):
    """Not ready yet (unpacking, still being written, waiting for TMDB)."""


class Refused(Exception):
    """Will never work as things stand (unsafe path, nothing to file)."""


class Transient(Exception):
    """A remote copy was cut off (stall, helper connection failure, remote host reboot): try again later."""


# A cut-off remote copy is retried after these waits, then the job fails (REMOTE_TRIES tries in all).
REMOTE_RETRY_S = (120, 600)
REMOTE_TRIES = len(REMOTE_RETRY_S) + 1
CLEANUP_REQUEUES = 1      # a failed job whose partial the sweep removed goes back to waiting this many times
_current = {'h': None}    # the job tick() is running (transfer() records its remote copy on it)


class InsufficientSpace(Exception):
    """The library folder can't hold the copy (ENOSPC or the pre-check). The job
    fails with INSUFFICIENT_SPACE. Free space as reported is never trusted on its own
    (Unraid's shares report pool/disk space that the target disk may not have): a
    manual Retry puts it back, or requeue_for_space() at most ONCE, after the reported
    free space has grown by at least the file size since the failure, no sooner than
    SPACE_RETRY_S after it, checked no more often than every SPACE_RETRY_S."""

    def __init__(self, msg: str = '', path: str = '', need: int = 0):
        super().__init__(msg)
        self.path, self.need = path, int(need or 0)


INSUFFICIENT_SPACE = 'insufficient space'
SPACE_MARGIN_BYTES = 64 * 1024 * 1024
SPACE_RETRY_S = 15 * 60          # the one automatic retry: not sooner, and checks no more often


def free_bytes(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def _check_space(folder: str, need: int) -> None:
    try:
        free = free_bytes(folder)
    except OSError:
        return   # can't tell (some shares); the copy itself will say
    if free < need + SPACE_MARGIN_BYTES:
        raise InsufficientSpace(INSUFFICIENT_SPACE, folder, need)


def init(*, settings) -> None:
    _cfg['settings'] = settings
    _load_login()
    try:
        from app import qbit_connector
        qbit_connector.on_verified(reset_login)
    except Exception:   # the connector is optional in tests
        pass


# --------------------------------------------------------------------------- tracking

def track(torrent_hash: str, tmdb_data: Any, torrent_name: Optional[str] = None) -> bool:
    """Record a grab (called by /api/downloads/add once the hash is confirmed)."""
    h = (torrent_hash or '').strip().lower()
    if not _HASH_RE.match(h) or not isinstance(tmdb_data, dict):
        return False
    tmdb_id = tmdb_data.get('tmdb_id') or tmdb_data.get('id')
    try:
        tmdb_id = int(tmdb_id)
    except (TypeError, ValueError):
        tmdb_id = None
    title = tmdb_data.get('title') or tmdb_data.get('name') or ''
    job = {
        'hash': h, 'tmdb_id': tmdb_id,
        'media_type': 'tv' if tmdb_data.get('media_type') == 'tv' else 'movie',
        'title': str(title)[:300], 'year': str(tmdb_data.get('year') or '')[:4],
        'name': str(torrent_name or '')[:500], 'added': time.time(), 'state': 'waiting',
        'misses': 0, 'attempts': 0,
    }

    def apply(data):
        jobs = data['jobs']
        old = jobs.get(h)
        if old and old.get('state') == 'waiting':
            return
        waiting = [k for k, v in jobs.items() if v.get('state') == 'waiting']
        if len(waiting) >= MAX_JOBS_WAITING:
            oldest = min(waiting, key=lambda k: jobs[k].get('added') or 0)
            jobs.pop(oldest, None)
        jobs[h] = job
    fm.update(apply)
    logger.info('Library mover: tracking %s', job['title'] or h[:8])
    return True


def _set_job(h: str, **fields) -> None:
    def apply(data):
        job = data['jobs'].get(h)
        if job is not None:
            job.update(fields)
            _prune(data)
    fm.update(apply)


def _drop_job(h: str, why: str) -> None:
    def apply(data):
        data['jobs'].pop(h, None)
    fm.update(apply)
    logger.info('Library mover: dropped %s (%s)', h[:8], why)


def _prune(data) -> None:
    """Keep the newest JOBS_KEEP finished jobs."""
    done = sorted((v.get('finished') or 0, k) for k, v in data['jobs'].items() if v.get('state') in TERMINAL)
    for _ts, k in done[:max(0, len(done) - fm.JOBS_KEEP)]:
        data['jobs'].pop(k, None)


# --------------------------------------------------------------------------- qBittorrent

# qBittorrent bans an IP after 5 failed sign-ins (its default), and this IP is
# shared with production. After a rejected sign-in ('Fails.', 401 or 403) the
# mover waits LOGIN_BACKOFF_S * 2**(n-1) before trying again, and after
# LOGIN_MAX_FAILURES in a row it stops until the credentials change or the owner
# tests the qBittorrent connector successfully. The count is kept on disk
# (qbit_login.json next to the store, keyed by a hash of the credentials), so
# neither a restart nor a Save with the same credentials allows more tries. An
# unreachable qBit is not a failed sign-in (nothing was checked) but is backed
# off the same way, capped.
LOGIN_BACKOFF_S = 60
LOGIN_MAX_FAILURES = 3
UNREACHABLE_MAX_WAIT_S = 900
LOGIN_FILE = 'qbit_login.json'
_login: Dict[str, Any] = {}
_login_lock = threading.Lock()
_FRESH = dict(failures=0, unreachable=0, next_at=0.0, held=False, creds=None)


def _login_path():
    return fm.store_path().parent / LOGIN_FILE


def _save_login_locked() -> None:
    """Persist the sign-in count (caller holds _login_lock). Best effort."""
    rec = {k: _login.get(k) for k in ('failures', 'next_at', 'held', 'creds')}
    try:
        from app.paths import atomic_write_json
        path = _login_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, rec, mode=0o600)
    except Exception as e:
        logger.warning('Library mover: could not save the sign-in state (%s)', type(e).__name__)


def _load_login() -> None:
    with _login_lock:
        _login.clear()
        _login.update(_FRESH)
        try:
            import json
            raw = json.loads(_login_path().read_text())
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or not isinstance(raw.get('creds'), str):
            return
        try:
            _login.update(failures=max(0, int(raw.get('failures') or 0)),
                          next_at=float(raw.get('next_at') or 0.0),
                          held=bool(raw.get('held')), creds=raw['creds'])
        except (TypeError, ValueError):
            _login.update(_FRESH)


def reset_login() -> None:
    """A fresh start: after the owner's successful connector Test."""
    with _login_lock:
        _login.clear()
        _login.update(_FRESH)
        try:
            _login_path().unlink()
        except OSError:
            pass


def login_state() -> Dict[str, Any]:
    with _login_lock:
        return dict(_login)


def _creds_key(url: str, user: str, password: str) -> str:
    import hashlib
    return hashlib.sha256('\x00'.join((url, user, password)).encode('utf-8', 'replace')).hexdigest()


def _qbit_session(session_factory, now: Optional[float] = None) -> Tuple[Optional[Any], str]:
    from app.qbit_connector import new_session, normalize_url, text_of, login_ok
    now = time.time() if now is None else now
    s = _cfg['settings']
    url = normalize_url(s.get('qbittorrent.url') or '')
    if not url:
        return None, ''
    user = s.get('qbittorrent.username') or ''
    password = s.get('qbittorrent.password') or ''
    key = _creds_key(url, user, password)
    with _login_lock:
        if not _login:
            _login.update(_FRESH)
        if _login.get('creds') != key:          # new credentials: a fresh start
            _login.update(_FRESH)
            _login['creds'] = key
            _save_login_locked()
        if _login['held'] or now < _login['next_at']:
            return None, url
    sess = new_session(session_factory)
    try:
        r = sess.post(f'{url}/api/v2/auth/login', data={'username': user, 'password': password},
                      timeout=10, allow_redirects=False)
    except requests.RequestException as e:
        with _login_lock:
            _login['unreachable'] += 1
            _login['next_at'] = now + min(UNREACHABLE_MAX_WAIT_S, 30 * 2 ** (_login['unreachable'] - 1))
        logger.warning('Library mover: qBittorrent unreachable (%s)', type(e).__name__)
        _close(sess)
        return None, url
    body = text_of(r).strip()
    if login_ok(r):
        with _login_lock:
            dirty = _login['failures'] or _login['next_at'] or _login['held']
            _login.update(failures=0, unreachable=0, next_at=0.0, held=False)
            if dirty:
                _save_login_locked()
        return sess, url
    _close(sess)
    # A redirect counts as a rejected sign-in too: the session never follows it,
    # so retrying it forever would only resend the password to the same 3xx.
    rejected = (r.status_code in (401, 403) or 300 <= r.status_code < 400
                or body == 'Fails.')
    with _login_lock:
        _login['unreachable'] = 0
        if rejected:
            _login['failures'] += 1
            if _login['failures'] >= LOGIN_MAX_FAILURES:
                _login['held'] = True
            else:
                _login['next_at'] = now + LOGIN_BACKOFF_S * 2 ** (_login['failures'] - 1)
        else:                                   # not a qBit answer we know: back off, don't count
            _login['next_at'] = now + UNREACHABLE_MAX_WAIT_S
        _save_login_locked()
        held, n = _login['held'], _login['failures']
    if held:
        logger.error('Library mover: qBittorrent refused sign-in %d times; paused until the '
                     'credentials change or the qBittorrent connector is tested', n)
    else:
        logger.warning('Library mover: qBittorrent sign-in failed (HTTP %s)', r.status_code)
    return None, url


def _close(sess) -> None:
    close = getattr(sess, 'close', None)
    if close:
        try:
            close()
        except Exception:
            pass


def _torrents(sess, url: str, hashes: List[str]) -> Optional[Dict[str, Dict[str, Any]]]:
    """{hash: torrent} for exactly these hashes; None when qBit didn't answer."""
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(hashes), BATCH):
        batch = hashes[i:i + BATCH]
        try:
            r = sess.get(f'{url}/api/v2/torrents/info', params={'hashes': '|'.join(batch)}, timeout=10,
                         allow_redirects=False)
            data = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            data = None
        if not isinstance(data, list):
            return None
        want = set(batch)
        for t in data:
            h = str((t or {}).get('hash') or '').lower()
            if h in want:          # never anything we didn't ask about
                out[h] = t
    return out


# --------------------------------------------------------------------------- TMDB

def _release_date(tmdb_id: Optional[int]) -> Tuple[bool, Optional[date]]:
    """(answered, date). answered False: TMDB unreachable or no key, try later."""
    if not tmdb_id:
        return True, None
    s = _cfg['settings']
    key = s.get('apis.tmdb.api_key') or ''
    if not key:
        return True, None
    base = (s.get('apis.tmdb.base_url') or 'https://api.themoviedb.org/3').rstrip('/')
    try:
        r = requests.get(f'{base}/movie/{int(tmdb_id)}', params={'api_key': key}, timeout=10)
    except requests.RequestException:
        return False, None
    if r.status_code == 404:
        return True, None
    if r.status_code != 200:
        return False, None
    try:
        raw = (r.json() or {}).get('release_date') or ''
        return True, date.fromisoformat(raw[:10]) if raw else None
    except (ValueError, AttributeError):
        return True, None


# --------------------------------------------------------------------------- files

def is_sample_file(filename: str) -> bool:
    """Prod's is_sample_file."""
    f = filename.lower()
    for ind in ('sample', 'trailer', 'preview', 'promo', 'featurette', 'deleted.scene', 'deleted_scene',
                'extra', 'behind.the.scenes', 'behind_the_scenes', 'making.of', 'making_of'):
        if ind in f:
            return True
    return bool(re.search(r'[\[\(]sample[\]\)]', f))


def is_screener_file(filename: str) -> bool:
    """Prod's is_screener_file."""
    f = filename.lower()
    for ind in ('screener', 'scr', 'dvdscr', 'dvdscreener', 'bdscr', 'webscreener', 'hdscreener'):
        if re.search(r'\b' + ind + r'\b', f):
            return True
    return bool(re.search(r'[\[\(\.]screener[\]\)\.]', f))


def is_file_stable(path: str, wait: Optional[float] = None) -> bool:
    """Prod's is_file_stable: size and mtime unchanged over a short window."""
    try:
        a = os.stat(path)
        time.sleep(STABLE_WAIT_S if wait is None else wait)
        b = os.stat(path)
    except OSError:
        return False
    return a.st_size == b.st_size and a.st_mtime == b.st_mtime


def _is_video(name: str) -> bool:
    return name.lower().endswith(VIDEO_EXTS) and not is_sample_file(name) and not is_screener_file(name)


def _real_under(path: str, root: str) -> bool:
    return fm._under(os.path.realpath(path), root)


class Found(NamedTuple):
    """A video picked by a scan, with the identity it had then: the copy must
    read this very file (same device, inode and size), not one swapped in
    under the same name since."""
    path: str
    size: int
    dev: int = -1
    ino: int = -1


def _found(path: str, st: os.stat_result) -> Found:
    return Found(path, st.st_size, st.st_dev, st.st_ino)


def _scan_file(full: str, videos: List[Found]) -> Tuple[bool, bool]:
    """Look at one candidate file: (is_rar, is_video). Adds stable videos to
    *videos*; raises Pending for files still being written."""
    f = os.path.basename(full)
    try:
        st = os.lstat(full)
    except FileNotFoundError:
        raise Pending('not there yet')
    if not _stat.S_ISREG(st.st_mode):
        return False, False
    if f.lower().endswith('.!qb'):
        raise Pending('still downloading')
    is_rar = bool(f.endswith('.rar') or re.match(r'.*\.r\d+$', f))
    if not _is_video(f) or st.st_size < MIN_VIDEO_BYTES:
        return is_rar, False
    if not is_file_stable(full):
        raise Pending('still being written')
    videos.append(_found(full, st))
    return is_rar, True


def _not_in_temp(real: str, root: str, temp_dirs: Tuple[str, ...]) -> None:
    for tmp in (os.path.join(root, 'incomplete'),) + tuple(temp_dirs):
        if fm._under(real, os.path.realpath(tmp)):
            raise Pending('still in incomplete')


def find_video_files(content: str, root: str, added: float, now: float,
                     temp_dirs: Tuple[str, ...] = ()) -> List[Found]:
    """[Found(path, size, dev, ino)] of the movie's video files inside *content* (a file or
    folder under the real downloads *root*). Raises Pending / Refused.
    Port of prod's find_video_files; symlinks and anything in <root>/incomplete,
    qBittorrent's temp folder (*temp_dirs*) or named *.!qB are never touched."""
    real = os.path.realpath(content)
    _not_in_temp(real, root, temp_dirs)
    if not fm._under(real, root) or real == root:
        raise Refused('outside the downloads folder')
    if os.path.islink(content):
        raise Refused('symlink')
    if os.path.isfile(real):
        name = os.path.basename(real)
        if name.lower().endswith('.!qb'):
            raise Pending('still downloading')
        if not _is_video(name):
            raise Refused('not a video')
        if not is_file_stable(real):
            raise Pending('still being written')
        try:
            st = os.lstat(real)
        except FileNotFoundError:
            raise Pending('not there yet')
        if not _stat.S_ISREG(st.st_mode):
            raise Refused('not a regular file')
        return [_found(real, st)]
    if not os.path.isdir(real):
        raise Pending('not there yet')
    videos: List[Found] = []
    rar_only = False
    for dirpath, dirnames, files in os.walk(real, followlinks=False):
        if '.extraction_in_progress' in files:
            raise Pending('unpacking')
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        has_rar = has_video = False
        for f in sorted(files):
            r, v = _scan_file(os.path.join(dirpath, f), videos)
            has_rar, has_video = has_rar or r, has_video or v
        if has_rar and not has_video:
            rar_only = True
    return _finish_scan(videos, rar_only, added, now)


def _finish_scan(videos, rar_only, added, now) -> List[Found]:
    if not videos and rar_only and now - added < EXTRACT_WAIT_S:
        raise Pending('unpacking')
    if not videos:
        raise Refused('no video files')
    return sorted(videos)


def _clean_rel(name: Any) -> Optional[str]:
    """A qBittorrent file name relative to the save path, or None when it is
    absolute, empty or climbs out ('..')."""
    if not isinstance(name, str) or not name or '\x00' in name:
        return None
    parts = name.replace('\\', '/').split('/')
    if name.startswith('/') or any(p in ('', '.', '..') for p in parts):
        return None
    return '/'.join(parts)


def find_listed_files(save_real: str, listed: List[Dict[str, Any]], root: str, added: float, now: float,
                      temp_dirs: Tuple[str, ...] = ()) -> List[Found]:
    """The "no subfolder" layout: the torrent's files sit straight in the save
    folder next to other downloads. Only the files qBittorrent lists for this
    torrent (torrents/files) are considered."""
    _not_in_temp(save_real, root, temp_dirs)
    if not fm._under(save_real, root):
        raise Refused('outside the downloads folder')
    videos: List[Found] = []
    rar_only = False
    names = set()
    for f in listed:
        if not isinstance(f, dict) or f.get('priority') == 0:
            continue
        rel = _clean_rel(f.get('name'))
        if rel is None:
            raise Refused('unsafe file name')
        names.add(rel)
    if not names:
        raise Pending('not there yet')
    has_video_dir: Dict[str, bool] = {}
    rar_dirs = set()
    for rel in sorted(names):
        full = os.path.join(save_real, rel)
        if not fm._under(os.path.realpath(full), save_real):
            raise Refused('outside the downloads folder')
        parent = os.path.dirname(rel)
        if os.path.exists(os.path.join(save_real, parent, '.extraction_in_progress')):
            raise Pending('unpacking')
        if not _is_video(rel) and not os.path.lexists(full):
            continue          # an extra (nfo, subs) someone removed
        r, v = _scan_file(full, videos)
        if r:
            rar_dirs.add(parent)
        if v:
            has_video_dir[parent] = True
    rar_only = any(not has_video_dir.get(d) for d in rar_dirs)
    return _finish_scan(videos, rar_only, added, now)


def select_main(videos: List[Tuple[str, int]]) -> List[str]:
    """Prod's select_main_video_files for movies: the largest when it is 5x the
    next, else every file (multi-part), largest first, minus likely duplicates."""
    videos = [(v[0], v[1]) for v in videos]
    v = sorted(videos, key=lambda x: x[1], reverse=True)
    if len(v) > 1 and v[0][1] > v[1][1] * 5:
        return [v[0][0]]
    picked: List[str] = []
    for path, _size in v:
        if any(_likely_duplicate(os.path.basename(path), os.path.basename(p)) for p in picked):
            continue
        picked.append(path)
    return sorted(picked)


def _likely_duplicate(a: str, b: str) -> bool:
    """Prod's is_likely_duplicate."""
    def clean(s):
        s = re.sub(r'\.(mkv|mp4|avi|mov|wmv|flv|webm)$', '', s.lower())
        for p in (r'720p', r'1080p', r'2160p', r'4k', r'bluray', r'webrip', r'web-dl', r'hdtv',
                  r'x264', r'x265', r'h264', r'h265', r'remux', r'hdr', r'10bit'):
            s = re.sub(p, '', s, flags=re.IGNORECASE)
        return re.sub(r'[\s\.\-_]+', ' ', s).strip()
    return clean(a) == clean(b)


def _owner_of(root: str) -> Optional[Tuple[int, int]]:
    """(uid, gid) to give new files when running as root; None otherwise."""
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        return None
    st = os.stat(root)
    return st.st_uid, st.st_gid


_O_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
_O_DIR = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | _O_NOFOLLOW | _O_CLOEXEC
_O_SRC = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOCTTY', 0)


def _fd_path(fd: int, name: Optional[str] = None) -> str:
    """/proc/self/fd/<fd>[/name]: reaches exactly the open file or folder."""
    p = f'/proc/self/fd/{fd}'
    return f'{p}/{name}' if name else p


def _finish_fd(fd: int, mode: int, owner: Optional[Tuple[int, int]]) -> None:
    """664/775 and, as root, the library path's owner, on an open descriptor
    (so a swapped-in symlink can never redirect it)."""
    try:
        os.fchmod(fd, mode)
    except OSError:
        pass   # CIFS may force its own modes
    if owner:
        try:
            os.fchown(fd, owner[0], owner[1])
        except OSError as e:
            logger.info('Library mover: chown skipped (%s)', type(e).__name__)


def _finish(path: str, mode: int, owner: Optional[Tuple[int, int]], dir_fd: Optional[int] = None) -> None:
    """_finish_fd on *path* opened without following a symlink; a symlink (or
    anything that can't be opened) is left alone."""
    try:
        fd = os.open(path, _O_SRC, dir_fd=dir_fd)
    except OSError:
        return
    try:
        _finish_fd(fd, mode, owner)
    finally:
        os.close(fd)


def _lexists_at(dfd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dfd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _unlink_at(dfd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dfd)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning('Library mover: could not remove %s (%s)', name, type(e).__name__)


RENAME_NOREPLACE = 1
_renameat2 = None


def _rename2(dfd: int, old: str, new: str) -> Optional[bool]:
    """renameat2(RENAME_NOREPLACE) inside one folder: True renamed, False *new* exists, None when the
    kernel or filesystem lacks it (the caller falls back)."""
    global _renameat2
    if _renameat2 is None:
        _rename_noreplace_load()
    if not _renameat2:
        return None
    import ctypes
    rc = _renameat2(dfd, os.fsencode(old), dfd, os.fsencode(new), RENAME_NOREPLACE)
    if rc == 0:
        return True
    err = ctypes.get_errno()
    if err == errno.EEXIST:
        return False
    if err in (errno.EINVAL, errno.ENOSYS, getattr(errno, 'EOPNOTSUPP', -1), errno.ENOTSUP):
        return None
    raise OSError(err, os.strerror(err))


def _rename_noreplace_load() -> None:
    global _renameat2
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        _renameat2 = fn
    except (OSError, AttributeError):
        _renameat2 = False


def _rename_noreplace(dfd: int, old: str, new: str) -> bool:
    """Rename old -> new inside one folder, never replacing new. True when
    renamed, False when new already exists. Uses renameat2(RENAME_NOREPLACE);
    where the kernel or the filesystem lacks it, checks first then renames."""
    global _renameat2
    if _renameat2 is None:
        try:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            fn = libc.renameat2
            fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            fn.restype = ctypes.c_int
            _renameat2 = fn
        except (OSError, AttributeError):
            _renameat2 = False
    if _renameat2:
        import ctypes
        rc = _renameat2(dfd, os.fsencode(old), dfd, os.fsencode(new), RENAME_NOREPLACE)
        if rc == 0:
            return True
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            return False
        if err not in (errno.EINVAL, errno.ENOSYS, getattr(errno, 'EOPNOTSUPP', -1), errno.ENOTSUP):
            raise OSError(err, os.strerror(err))
    if _lexists_at(dfd, new):
        return False
    os.rename(old, new, src_dir_fd=dfd, dst_dir_fd=dfd)
    return True


SOURCE_CHANGED = 'the file changed since it was checked'


def _open_source(src: str, root: str, expect: Optional[Found] = None) -> Tuple[int, os.stat_result]:
    """Open a download file for reading: never through a symlink, and only
    when the open file is the one inspected (same device and inode) and it
    really sits inside the downloads *root*. With *expect* (what the scan
    saw), the open file must also be that one: same device, inode and size;
    else Pending (the next pass scans again)."""
    try:
        st0 = os.lstat(src)
    except FileNotFoundError:
        raise Pending('not there yet')
    if not _stat.S_ISREG(st0.st_mode):
        raise Refused('not a regular file')
    if not _real_under(src, root):
        raise Refused('outside the downloads folder')
    try:
        fd = os.open(src, _O_SRC)
    except FileNotFoundError:
        raise Pending('not there yet')
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EMLINK):
            raise Refused('symlink')
        raise
    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (st0.st_dev, st0.st_ino):
            raise Refused('the file changed while being checked')
        if not fm._under(os.path.realpath(_fd_path(fd)), root):
            raise Refused('outside the downloads folder')
        if expect is not None and (st.st_dev, st.st_ino, st.st_size) != (expect.dev, expect.ino, expect.size):
            raise Pending(SOURCE_CHANGED)
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def _open_root(lib_root: str) -> int:
    """The library folder as a descriptor, checked to be the real path."""
    try:
        rfd = os.open(lib_root, _O_DIR)
    except OSError:
        raise Pending('library folder not available')
    if os.path.realpath(_fd_path(rfd)) != lib_root:
        os.close(rfd)
        raise Refused('destination folder resolves elsewhere')
    from app import remote_mover
    if not remote_mover.enabled() and not os.access(lib_root, os.W_OK | os.X_OK):  # v167; v318 remote skips
        os.close(rfd)
        raise Pending(NOT_WRITABLE)
    return rfd


def _ensure_folder(rfd: int, lib_root: str, folder: str, owner) -> Tuple[int, bool, str]:
    """(descriptor, created this pass, path) of lib_root/folder, created 775
    when missing; refused when it is a symlink or not a folder."""
    if not folder or '/' in folder or folder in ('.', '..'):
        raise Refused('unsafe file name')
    created = False
    full = os.path.join(lib_root, folder)
    from app import remote_mover
    if remote_mover.enabled():
        # Create on .72 first (Unraid /media lives there); local mkdir is best-effort.
        try:
            remote_mover.mkdir_p(full)
        except remote_mover.RemoteMoverError as e:
            raise Refused(_err_public(e, 'remote folder could not be created', 'library_mover')) from e
    try:
        os.mkdir(folder, 0o775, dir_fd=rfd)
        created = True
    except FileExistsError:
        pass
    except OSError as e:
        if not (remote_mover.enabled() and e.errno in (errno.EROFS, errno.EACCES, errno.EPERM)):
            raise
    try:
        dfd = os.open(folder, _O_DIR, dir_fd=rfd)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise Refused('destination folder is not a plain folder')
        if remote_mover.enabled() and e.errno == errno.ENOENT:
            # Mount lag: remote dir exists on Unraid; retry next pass.
            raise Pending('library folder not available')
        raise
    if created:
        try:
            _finish_fd(dfd, 0o775, owner)
        except OSError:
            if not remote_mover.enabled():
                raise
    return dfd, created, full


PARTIAL_SUFFIX = '.luma-partial'


def partial_name() -> str:
    """Temp name for one copy into a library folder: '.luma-<hex16>.luma-partial',
    hidden, fixed length, never derived from the title. Created in the SAME
    folder as the final name and renamed into place. Shared by the local copy
    (_new_partial) and the v324 remote hop (remote_mover.transfer) so both use
    one naming rule."""
    import secrets
    return f'.luma-{secrets.token_hex(8)}{PARTIAL_SUFFIX}'


def _new_partial(dfd: int, name: str) -> Tuple[str, int, Tuple[int, int]]:
    """(name, write fd, (dev, ino)) of a new, uniquely named partial file,
    created here (O_EXCL, never through a symlink). A partial that is already
    there belongs to someone else and is never touched."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
    for _ in range(8):
        # Hotfix (Reviewer, REQUIRED): short and fixed-length, never derived from *name*: a long final name
        # ('.<name>.<hex8>.luma-partial') no longer hits ENAMETOOLONG before the rename
        tmp = partial_name()
        try:
            fd = os.open(tmp, flags, 0o600, dir_fd=dfd)
        except FileExistsError:
            continue
        st = os.fstat(fd)
        partial_cleanup.register(os.path.join(os.path.realpath(_fd_path(dfd)), tmp))
        return tmp, fd, (st.st_dev, st.st_ino)
    raise OSError(errno.EEXIST, 'no free partial name')


def _unlink_ours(dfd: int, tmp: str, ident: Tuple[int, int]) -> None:
    """Remove *tmp* only while it is still the file this pass created."""
    try:
        st = os.stat(tmp, dir_fd=dfd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        return
    if (st.st_dev, st.st_ino) == ident:
        _unlink_at(dfd, tmp)


def transfer(src: str, dfd: int, folder: str, name: str, mode: str, owner, root: str,
             expect: Optional[Found] = None) -> str:
    """'linked', 'copied' or 'exists' for one download file *src* into the open
    library folder *dfd* (path *folder*) as *name*. Never overwrites, never
    moves, never follows a symlink on either side, and reads only the file
    the scan saw (*expect*)."""
    if mode not in ('hardlink', 'copy'):   # never a move (LUMA_INSTANCE=test asserts it; prod has no other mode)
        raise Refused('transfer must be copy or hardlink')
    if _lexists_at(dfd, name) and not _inflight_of(_current['h']).get(os.path.join(folder, name)):
        return 'exists'        # (a final that our own remote copy put there while we were away is verified below)
    # v318: remote push-import — .72 copies downloads→/media over SSH (no byte
    # copy through the Luma container). Hardlink falls back to remote copy.
    from app import remote_mover
    if remote_mover.enabled():
        if not _real_under(src, root):
            raise Refused('outside the downloads folder')
        return _remote_transfer(src, os.path.join(folder, name))
    sfd, st = _open_source(src, root, expect)
    try:
        if mode == 'hardlink':
            try:
                # linkat through /proc: the inode that was checked, not the path.
                os.link(_fd_path(sfd), name, dst_dir_fd=dfd, follow_symlinks=True)
                return 'linked'   # same inode as the seeding download: modes left alone
            except FileExistsError:
                return 'exists'
            except OSError as e:
                logger.info('Library mover: hardlink failed (%s), copying', errno.errorcode.get(e.errno, e.errno))
        _check_space(folder, st.st_size)
        tmp, tfd, ident = _new_partial(dfd, name)
        try:
            try:
                try:
                    _paced_copy(sfd, tfd)
                except OSError as e:
                    if e.errno in (errno.ENOSPC, getattr(errno, 'EDQUOT', -1)):
                        raise InsufficientSpace(INSUFFICIENT_SPACE, folder, st.st_size) from e
                    raise
                tst = os.fstat(tfd)
                if not _stat.S_ISREG(tst.st_mode) or tst.st_size != st.st_size:
                    raise OSError(errno.EIO, 'size mismatch after copy')
                _finish_fd(tfd, 0o664, owner)
            finally:
                os.close(tfd)
            # v184 (Ops): the partial is renamed into place (renameat2 NOREPLACE: atomic, never replaces), so
            # nothing is left to delete: deleting a same-size partial on a NAS share sends it to the recycle bin
            got = _rename2(dfd, tmp, name)
            if got is not None:
                return 'copied' if got else 'exists'
            try:
                os.link(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)   # refuses an existing name
            except FileExistsError:
                return 'exists'
            except OSError:
                if _lexists_at(dfd, name):
                    return 'exists'
                return 'copied' if _rename_noreplace(dfd, tmp, name) else 'exists'
            return 'copied'
        finally:
            try:
                _unlink_ours(dfd, tmp, ident)      # only when it was not renamed into place (or the name exists)
            finally:
                partial_cleanup.unregister(os.path.join(os.path.realpath(_fd_path(dfd)), tmp))
    finally:
        os.close(sfd)


def _inflight_of(h: Optional[str]) -> Dict[str, str]:
    if not h:
        return {}
    j = fm.load()['jobs'].get(h) or {}
    v = j.get('inflight')
    return dict(v) if isinstance(v, dict) else {}


def _set_inflight(h: Optional[str], dest: str, tmp: Optional[str], abandon: bool = False) -> None:
    """Record (tmp) or clear the remote copy into *dest*. abandon: the copy was cut off; its partial is
    remembered (cut_partials) so that when partial cleanup removes it the job is requeued."""
    if not h:
        return

    def apply(data):
        j = data['jobs'].get(h)
        if j is None:
            return
        cur = dict(j.get('inflight') or {})
        if abandon and cur.get(dest):
            j['cut_partials'] = (list(j.get('cut_partials') or []) + [cur[dest]])[-5:]
        if tmp:
            cur[dest] = tmp
        else:
            cur.pop(dest, None)
        if cur:
            j['inflight'] = cur
        else:
            j.pop('inflight', None)
    fm.update(apply)


def _remote_transfer(src: str, dest: str) -> str:
    """One remote copy, at most one per destination across restarts. The temp name is saved on the job
    before the helper is asked, so after a restart (or a cut-off call) a copy still running on the remote
    host is adopted, not started again. A copy that was cut off raises Transient; a refusal raises Refused."""
    from app import remote_mover
    h = _current['h']
    tmp = _inflight_of(h).get(dest)
    try:
        if tmp and remote_mover.partial_alive(tmp):
            try:
                return remote_mover.adopt(src, dest, tmp)
            finally:
                _set_inflight(h, dest, None)
        if tmp and os.path.lexists(dest) and not os.path.lexists(tmp):
            how = remote_mover.adopt(src, dest, tmp)      # it landed while we were away: verify its size
            _set_inflight(h, dest, None)
            return how
        # (a dead partial from an earlier attempt is left to partial cleanup)
        tmp = remote_mover.remote_temp_for(dest)
        _set_inflight(h, dest, tmp)
        how = remote_mover.transfer(src, dest, tmp_container=tmp)
        _set_inflight(h, dest, None)
        return how
    except remote_mover.RemoteTransient as e:
        # A dropped connection (helper SSH rc 255, socket gone) doesn't mean the remote cp died. Keep the
        # temp on the job while its partial is still growing (or the final landed) so the retry adopts it;
        # abandon it only once the partial is confirmed dead, and the retry starts one fresh copy.
        if isinstance(e, remote_mover.RemoteConnectionLost) and tmp and (remote_mover.partial_alive(tmp) or (os.path.lexists(dest) and not os.path.lexists(tmp))):
            _set_inflight(h, dest, tmp)
        else:
            _set_inflight(h, dest, None, abandon=True)
        raise Transient(_remote_reason(e, 'remote copy cut off', 'library_mover')) from e
    except remote_mover.RemoteMoverError as e:
        _set_inflight(h, dest, None)
        raise Refused(_err_public(e, 'remote copy refused', 'library_mover')) from e


def partials_removed(paths) -> int:
    """Partial cleanup removed these partials. A job that was copying into one of them and didn't finish
    (still waiting, or failed/skipped after the copy was cut off) goes back to waiting so the copy is made
    again. A failed job only CLEANUP_REQUEUES times. Returns the number requeued."""
    gone = {os.path.realpath(os.path.dirname(p)) + os.sep + os.path.basename(p) for p in paths or ()}
    if not gone:
        return 0

    def key(p):
        return os.path.realpath(os.path.dirname(p)) + os.sep + os.path.basename(p)

    def apply(data):
        n = 0
        for j in data['jobs'].values():
            inf = j.get('inflight') if isinstance(j.get('inflight'), dict) else {}
            cut = [t for t in (j.get('cut_partials') or []) if isinstance(t, str)]
            hit = [d for d, t in inf.items() if isinstance(t, str) and key(t) in gone]
            hit_cut = [t for t in cut if key(t) in gone]
            if not hit and not hit_cut:
                continue
            for d in hit:
                inf.pop(d, None)
            if not inf:
                j.pop('inflight', None)
            rest = [t for t in cut if t not in hit_cut]
            if rest:
                j['cut_partials'] = rest
            else:
                j.pop('cut_partials', None)
            if j.get('state') in ('done', 'exists'):
                continue
            if j.get('state') in ('failed', 'skipped'):
                if int(j.get('cleanup_requeues') or 0) >= CLEANUP_REQUEUES:
                    continue
                j['cleanup_requeues'] = int(j.get('cleanup_requeues') or 0) + 1
            _requeue(j)
            n += 1
            logger.info('Library mover: %s back to waiting (its unfinished copy was cleaned up)', j.get('title'))
        return n
    return fm.update(apply)


# --------------------------------------------------------------------------- one job

NOT_MAPPED = 'not in the downloads folder'
NO_DOWNLOADS = 'download folder not available'
FILES_NOT_FOUND = 'files not found'
PATH_NOT_MAPPED = 'path not mapped'   # qBit's save path doesn't map under the downloads root
NOT_FOUND_AFTER_S = 3600   # a grab whose files can't be reached this long is flagged
# Pending reasons that mean "can't see the files" (a path map or mount problem)
# rather than "not ready yet".
NOT_FOUND_REASONS = (NOT_MAPPED, NO_DOWNLOADS, 'not there yet', 'no download path')


def _downloads_root() -> str:
    root = fm.downloads_root()
    if fm.blocked(root) or not os.path.isdir(root):
        raise Pending(NO_DOWNLOADS)
    return root


def process(job: Dict[str, Any], torrent: Dict[str, Any], data: Dict[str, Any], now: float,
            list_files: Optional[Callable[[str], Optional[List[Dict[str, Any]]]]] = None) -> Dict[str, Any]:
    """File one completed movie. Returns the job fields to save. Raises
    Pending (try again next pass) or Refused (give up). *list_files(hash)*
    returns qBittorrent's file list (torrents/files), used for the "no
    subfolder" layout."""
    path_map = data['path_map']
    save_path = fm.map_path(str(torrent.get('save_path') or ''), path_map)
    content = fm.map_path(str(torrent.get('content_path') or ''), path_map)
    if not save_path.startswith('/') or not content.startswith('/'):
        raise Pending('no download path')
    save_real = os.path.realpath(save_path)
    if fm.blocked(save_real):
        raise Refused(NO_DOWNLOADS)
    root = _downloads_root()
    if not fm._under(save_real, root):
        raise Pending(NOT_MAPPED)       # a path map may fix it; flagged after an hour
    if not os.path.isdir(save_real):
        raise Pending('not there yet')
    lib_roots = [os.path.realpath(p['path']) for p in data['paths']]
    if any(fm._under(root, lr) or fm._under(lr, root) for lr in lib_roots):
        raise Refused('download folder overlaps a library path')

    temp_dirs = ()
    dl_path = str(torrent.get('download_path') or '')
    if dl_path.startswith('/'):
        mapped = fm.map_path(dl_path, path_map)
        if os.path.realpath(mapped) != save_real:
            temp_dirs = (mapped,)
    added = job.get('added') or now
    if os.path.realpath(content) == save_real:
        # "Don't create subfolder": the save folder itself, shared with other
        # downloads. Only this torrent's own files.
        listed = list_files(str(torrent.get('hash') or job.get('hash') or '')) if list_files else None
        if listed is None:
            raise Pending('waiting for the file list')
        found = find_listed_files(save_real, listed, root, added, now, temp_dirs)
    else:
        found = find_video_files(content, root, added, now, temp_dirs)
    files = select_main(found)
    seen = {f[0]: f for f in found}

    info = fm.classify(job.get('name') or str(torrent.get('name') or ''), os.path.basename(files[0]))
    if fm.needs_release_date(data):
        if 'release_date' not in job:
            answered, d = _release_date(job.get('tmdb_id'))
            if not answered and now - added < RELEASE_WAIT_S:
                raise Pending('waiting for TMDB')
            job['release_date'] = d.isoformat() if d else ''
        if job.get('release_date'):
            info['release_date'] = date.fromisoformat(job['release_date'])
    dest = fm.pick_destination(data, info)
    if not dest:
        if fm.no_writable_destination(data):      # v169: read-only paths only: say so, wait for a fix
            raise Pending(NO_WRITABLE_DEST)
        raise Refused(NO_DESTINATION)
    lib_root = os.path.realpath(dest['path'])
    if fm.blocked(lib_root) or not os.path.isdir(lib_root) or fm.overlaps_downloads(lib_root):
        raise Pending('library folder not available')
    owner = _owner_of(lib_root)

    year = job.get('year') or (job.get('release_date') or '')[:4]
    multi = len(files) > 1
    folder_name, _ = fm.movie_names(job.get('title'), year, '')
    names = []
    for i, src in enumerate(files, 1):
        _, name = fm.movie_names(job.get('title'), year, os.path.splitext(src)[1], part=i if multi else None)
        if not name or '/' in name or name in ('.', '..'):
            raise Refused('unsafe file name')
        names.append(name)
    if data['transfer'] == 'copy':
        # Everything that still has to be copied must fit (hardlinks need no room).
        need = 0
        for src, name in zip(files, names):
            if not os.path.lexists(os.path.join(lib_root, folder_name, name)):
                need += os.path.getsize(src)
        if need:
            _check_space(lib_root, need)
    rfd = _open_root(lib_root)
    try:
        dfd, created, folder = _ensure_folder(rfd, lib_root, folder_name, owner)
        made: List[str] = []
        try:
            results = []
            for src, name in zip(files, names):
                result = transfer(src, dfd, folder, name, data['transfer'], owner, root,
                                  seen.get(src) if isinstance(seen.get(src), Found) else None)
                if result != 'exists':
                    made.append(name)
                row = {'file': name, 'result': result}
                if result == 'exists':
                    row['match'] = _same_file(src, dfd, name, seen.get(src))
                results.append(row)
        except BaseException:
            # A failed pass leaves nothing half-filed: the parts it put in place
            # go, and so does the folder when this pass created it.
            for name in made:
                _unlink_at(dfd, name)
            if created:
                try:
                    os.rmdir(folder_name, dir_fd=rfd)
                except OSError:
                    pass
            raise
        finally:
            os.close(dfd)
    finally:
        os.close(rfd)
    state = 'done' if any(r['result'] != 'exists' for r in results) else 'exists'
    return {'state': state, 'path_id': dest['id'], 'dest': folder, 'files': results, 'finished': now,
            'release_date': job.get('release_date', ''), 'error': '', 'status': '', 'pending_since': None}


SHA_MAX_BYTES = 64 * 1024 * 1024   # 'exists' is also checked by sha256 when both files are this small


def _same_file(src: str, dfd: int, name: str, expect: Any = None) -> Optional[bool]:
    """An 'exists' destination is the same file as the download: same size (and sha256 when that is cheap).
    None when either side can't be read here (a remote mount that doesn't show it)."""
    try:
        d = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        s = os.stat(src)
    except OSError:
        return None
    if not _stat.S_ISREG(d.st_mode):
        return False
    size = expect.size if isinstance(expect, Found) else s.st_size
    if d.st_size != size or s.st_size != size:
        return False
    if (d.st_dev, d.st_ino) == (s.st_dev, s.st_ino) or size > SHA_MAX_BYTES:
        return True
    try:
        import hashlib
        fd = os.open(name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=dfd)
        with os.fdopen(fd, 'rb') as a, open(src, 'rb') as b:
            ha, hb = hashlib.sha256(), hashlib.sha256()
            for chunk in iter(lambda: a.read(1 << 20), b''):
                ha.update(chunk)
            for chunk in iter(lambda: b.read(1 << 20), b''):
                hb.update(chunk)
        return ha.digest() == hb.digest()
    except OSError:
        return None


def exists_matches(fields: Dict[str, Any]) -> bool:
    """Every file of an 'exists' result was checked and matches the download."""
    files = fields.get('files') or []
    return bool(files) and all(f.get('result') != 'exists' or f.get('match') is True for f in files)


def _files_of(sess, url: str) -> Callable[[str], Optional[List[Dict[str, Any]]]]:
    def list_files(h: str) -> Optional[List[Dict[str, Any]]]:
        if not _HASH_RE.match(h or ''):
            return None
        try:
            r = sess.get(f'{url}/api/v2/torrents/files', params={'hash': h}, timeout=10, allow_redirects=False)
            data = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return None
        return data if isinstance(data, list) else None
    return list_files


# --------------------------------------------------------------------------- pass

def _note_pending(h: str, job: Dict[str, Any], reason: str, now: float) -> None:
    """Remember since when a grab's files can't be found; flag it after
    NOT_FOUND_AFTER_S so Settings can show it."""
    if reason in (NOT_WRITABLE, NO_WRITABLE_DEST):
        # v169 (Reviewer, PM): a destination Luma can't write is never a silent wait: the Downloads
        # item says 'No writable destination' at once, and files as soon as a writable one exists.
        if job.get('status') != NO_WRITABLE_DEST:
            logger.warning('Library mover: no writable destination for %s (%s)', job.get('title'), reason)
            _set_job(h, pending_since=job.get('pending_since') or now, status=NO_WRITABLE_DEST)
        return
    if reason not in NOT_FOUND_REASONS:
        if job.get('pending_since') or job.get('status'):
            _set_job(h, pending_since=None, status='')
        return
    since = job.get('pending_since')
    if reason == NOT_MAPPED:
        # No waiting it out: only a path map (or LUMA_DOWNLOADS_ROOT) fixes it,
        # so say so right away instead of failing silently.
        if job.get('status') != PATH_NOT_MAPPED:
            logger.warning('Library mover: save path for %s does not map under the downloads folder %s',
                           job.get('title'), fm.downloads_root())
            _set_job(h, pending_since=since if since is not None else now, status=PATH_NOT_MAPPED)
        return
    if job.get('status') in (PATH_NOT_MAPPED, NO_WRITABLE_DEST):
        _set_job(h, pending_since=now, status='')
        return
    if since is None:
        _set_job(h, pending_since=now)
        return
    if now - since >= NOT_FOUND_AFTER_S and job.get('status') != FILES_NOT_FOUND:
        logger.warning('Library mover: files for %s not found for %d min (%s)', job.get('title'),
                       int((now - since) // 60), reason)
        _set_job(h, status=FILES_NOT_FOUND)


def _fail(h: str, job: Dict[str, Any], err: str, now: float) -> None:
    attempts = int(job.get('attempts') or 0) + 1
    logger.error('Library mover: copy failed for %s (%s, attempt %d/%d)',
                 job.get('title'), err, attempts, MAX_ATTEMPTS)
    if attempts >= MAX_ATTEMPTS:
        _set_job(h, state='failed', attempts=attempts, error=err, finished=now)
    else:
        _set_job(h, attempts=attempts, error=err)


def _run_job(h: str, job: Dict[str, Any], t: Dict[str, Any], data: Dict[str, Any], now: float,
             tag: str, list_files, summary: Dict[str, int]) -> None:
    if not qbit_owner.is_test_torrent(t, tag):
        _drop_job(h, f'not tagged {tag}')
        summary['dropped'] += 1
        return
    if job.get('misses') or job.get('missing_since'):
        _set_job(h, misses=0, missing_since=None)
    if job.get('media_type') != 'movie':
        _set_job(h, state='skipped', error='tv', finished=now)
        return
    if not (t.get('progress') == 1 and t.get('state') in COMPLETED_STATES):
        return
    if float(job.get('retry_at') or 0) > now:
        return                                  # a cut-off remote copy: waiting out its backoff
    _current['h'] = h
    try:
        fields = process(dict(job), t, data, now, list_files)
    except Transient as e:
        tries = int(job.get('remote_tries') or 0) + 1
        if tries >= REMOTE_TRIES:
            logger.error('Library mover: copy of %s failed (%s, try %d/%d)', job.get('title'), e, tries, REMOTE_TRIES)
            _set_job(h, state='failed', error=str(e), remote_tries=tries, retry_at=None, finished=now)
        else:
            wait = REMOTE_RETRY_S[tries - 1]
            logger.warning('Library mover: copy of %s was cut off (%s), try %d/%d, next in %d min',
                           job.get('title'), e, tries, REMOTE_TRIES, wait // 60)
            _set_job(h, remote_tries=tries, retry_at=now + wait, error=str(e))
        return
    except Pending as e:
        logger.debug('Library mover: %s waiting (%s)', job.get('title'), e)
        _note_pending(h, job, str(e), now)
        return
    except Refused as e:
        logger.warning('Library mover: not moving %s (%s)', job.get('title'), e)
        _set_job(h, state='skipped', error=str(e), finished=now)
        return
    except InsufficientSpace as e:
        free = _free_or_none(e.path)
        logger.warning('Library mover: %s failed (%s: needs %s bytes, %s free at %s); retry manually or when space frees up',
                       job.get('title'), INSUFFICIENT_SPACE, e.need, free, e.path)
        _set_job(h, state='failed', error=INSUFFICIENT_SPACE, finished=now,
                 space={'path': e.path, 'need': e.need, 'free': free})
        return
    except OSError as e:
        _fail(h, job, errno.errorcode.get(e.errno, type(e).__name__) if e.errno else type(e).__name__, now)
        return
    finally:
        _current['h'] = None
    fields.update(remote_tries=0, retry_at=None)
    _set_job(h, **fields)
    summary['filed'] += 1
    logger.info('Library mover: %s -> %s (%s)', job.get('title'), fields['dest'],
                ', '.join(r['result'] for r in fields['files']))
    if fields.get('state') == 'exists':
        _settle_exists(h, job, fields, now)
        return
    # Import-complete only (state 'done' = newly filed). 'exists' means already in the library.
    if fields.get('state') == 'done' and _claim_movie_grab(h, job, str(fields.get('dest') or ''), now):
        try:
            from app import library_notify
            library_notify.notify_library_added(
                media_type='movie',
                title=str(job.get('title') or ''),
                year=job.get('year') or '',
                tmdb_id=job.get('tmdb_id'),
                dest_path=str(fields.get('dest') or ''),
                source='library_mover',
            )
        except Exception:
            logger.exception('Library mover: notify failed for %s', job.get('title'))


def _settle_exists(h: str, job: Dict[str, Any], fields: Dict[str, Any], now: float) -> None:
    """Ops (Smile 2 requeue): the mover found the file already in place. When it is the same file (size,
    sha256 when cheap), the grab and the reconcile ledger are marked imported (no push: nothing new was
    filed). A different or unreadable file keeps the old behavior (job 'exists' only) and is logged."""
    if not exists_matches(fields):
        logger.warning('Library mover: %s already exists at %s but does not match the download (%s); grab not'
                       ' marked imported', job.get('title'), fields.get('dest'),
                       ', '.join(f"{f.get('file')}: {f.get('match')}" for f in fields.get('files') or []))
        return
    try:
        from app import movie_grabs
        g = movie_grabs.by_hash(h)
        if g:
            dest = str(fields.get('dest') or '')
            first = next((f['file'] for f in fields.get('files') or []), '')
            movie_grabs.mark_imported(h, path=os.path.join(dest, first) if dest and first else dest or None, now=now)
            from app import movie_settings as ms
            with ms.connect() as conn:
                conn.execute('DELETE FROM watchlist WHERE tmdb_id = ?', (g['tmdb_id'],))
    except Exception:                                # noqa: BLE001
        logger.exception('Library mover: movie_grabs mark failed for %s', job.get('title'))
    try:
        from app import import_reconciler
        import_reconciler.mark_imported(h, now)
    except Exception:                                # noqa: BLE001
        logger.exception('Library mover: reconcile ledger mark failed for %s', job.get('title'))
    logger.info('Library mover: %s already in the library (same file), marked imported', job.get('title'))


def _claim_movie_grab(h: str, job: Dict[str, Any], dest: str, now: float) -> bool:
    """v324c: a filed torrent with a movies.db movie_grabs row (matched by qBit hash, the only key that row
    has) is marked imported the way movie_import does (state, path, off the watchlist). True when this call
    should notify: no row at all, or this call flipped it. False when it was already imported (no double push)."""
    try:
        from app import movie_grabs
        g = movie_grabs.by_hash(h)
        if not g:
            return True
        if not movie_grabs.mark_imported(h, path=dest or None, now=now):
            return False
        try:
            from app import movie_settings as ms
            with ms.connect() as conn:
                conn.execute('DELETE FROM watchlist WHERE tmdb_id = ?', (g['tmdb_id'],))
        except Exception:                            # noqa: BLE001
            logger.exception('Library mover: watchlist cleanup failed for %s', job.get('title'))
        return True
    except Exception:                                # noqa: BLE001 - never blocks the notify
        logger.exception('Library mover: movie_grabs mark failed for %s', job.get('title'))
        return True


def _free_or_none(path: str) -> Optional[int]:
    """Free bytes on *path*'s filesystem (its nearest existing parent: a failed pass removes the folder it made)."""
    p = path or ''
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    if not p:
        return None
    try:
        return free_bytes(p)
    except OSError:
        return None


def _requeue(j: Dict[str, Any]) -> None:
    j.update(state='waiting', error='', attempts=0, misses=0, status='', pending_since=None,
             remote_tries=0, retry_at=None)
    for k in ('finished', 'space'):
        j.pop(k, None)


_space_check = {'at': None}


def space_retry_due(j: Dict[str, Any], now: float) -> Optional[int]:
    """The no-space retry rule (the mover's jobs and the TV download watch share it): a record
    {state 'failed', error INSUFFICIENT_SPACE, space {path, need, free at the failure}, finished}
    gets its ONE automatic retry when the free space reported on its destination has grown by
    >= need and SPACE_RETRY_S have passed since the failure, unless it already had it
    (auto_retried; the owner's Retry clears that). Returns the growth when due, else None."""
    sp = j.get('space') if isinstance(j.get('space'), dict) else None
    if j.get('state') != 'failed' or j.get('error') != INSUFFICIENT_SPACE or not sp or j.get('auto_retried'):
        return None
    at_fail, need = sp.get('free'), int(sp.get('need') or 0)
    if not isinstance(at_fail, (int, float)) or need <= 0:
        return None                           # no baseline: only the Retry button
    if now - float(j.get('finished') or 0) < SPACE_RETRY_S:
        return None
    free = _free_or_none(str(sp.get('path') or ''))
    if free is not None and free - at_fail >= need:
        return int(free - at_fail)
    return None


def requeue_for_space(now: Optional[float] = None) -> int:
    """The one automatic retry of a no-space failure: free space reported on its destination
    has grown by >= the file size since the failure, SPACE_RETRY_S have passed since it, and the
    job hasn't had its automatic retry yet (auto_retried survives the requeue; a second failure
    then waits for a manual Retry). Checked no more often than every SPACE_RETRY_S."""
    now = time.time() if now is None else now
    last = _space_check['at']
    if last is not None and now - last < SPACE_RETRY_S:
        return 0
    _space_check['at'] = now

    def apply(data):
        n = 0
        for j in data['jobs'].values():
            grew = space_retry_due(j, now)
            if grew is not None:
                _requeue(j)
                j['auto_retried'] = True
                n += 1
                logger.info('Library mover: %s back to waiting (free space grew by %d bytes; the one automatic retry)',
                            j.get('title'), grew)
        return n
    return fm.update(apply)


def retry(h: str) -> bool:
    """Manual Retry: a failed or skipped job goes back to waiting. False when there is no such job."""
    key = (h or '').lower()

    def apply(data):
        j = data['jobs'].get(key)
        if not j or j.get('state') not in ('failed', 'skipped'):
            return False
        _requeue(j)
        j.pop('auto_retried', None)           # the owner's Retry: a later no-space failure gets its one auto retry again
        return True
    return fm.update(apply)


def tick(now: Optional[float] = None, session_factory: Callable = requests.Session) -> Dict[str, int]:
    """One pass over the waiting jobs. Never contacts qBittorrent when no
    library path is configured, nothing is waiting or the downloads folder is
    missing. One job's failure never stops the others."""
    now = time.time() if now is None else now
    summary = {'checked': 0, 'filed': 0, 'dropped': 0}
    data = fm.load()
    if any(j.get('state') == 'failed' and j.get('error') == INSUFFICIENT_SPACE for j in data['jobs'].values()):
        if requeue_for_space(now):
            data = fm.load()
    if not data['paths']:
        return summary
    waiting = {h: j for h, j in data['jobs'].items() if j.get('state') == 'waiting'}
    if not waiting:
        return summary
    try:
        _downloads_root()
    except Pending as e:
        # No downloads folder (not mounted, not set up): nothing could be read,
        # so don't sign in to qBittorrent at all. The jobs wait and are
        # flagged 'files not found' after NOT_FOUND_AFTER_S.
        for h, job in waiting.items():
            summary['checked'] += 1
            _note_pending(h, job, str(e), now)
        return summary
    sess, url = _qbit_session(session_factory, now)
    if not sess:
        return summary
    try:
        torrents = _torrents(sess, url, sorted(waiting))
        if torrents is None:
            return summary
        tag = qbit_owner.tag_name()
        list_files = _files_of(sess, url)
        for h, job in waiting.items():
            summary['checked'] += 1
            t = torrents.get(h)
            if t is None:
                # A torrent qBit doesn't list is dropped only after ORPHAN_MISSES passes AND ORPHAN_MIN_S: a qBit
                # that is restarting (or a remote host rebooting) answers 200 with an empty or partial list, and
                # three 30 s passes used to drop the job for good (nothing re-tracks it; import_reconciler does now).
                misses = int(job.get('misses') or 0) + 1
                since = float(job.get('missing_since') or now)
                if misses >= ORPHAN_MISSES and now - since >= ORPHAN_MIN_S:
                    _drop_job(h, 'removed from qBittorrent')
                    summary['dropped'] += 1
                else:
                    _set_job(h, misses=misses, missing_since=since)
                continue
            try:
                _run_job(h, job, t, data, now, tag, list_files, summary)
            except Exception as e:   # one bad job never ends the pass
                logger.exception('Library mover: %s failed', job.get('title') or h[:8])
                try:
                    _fail(h, job, type(e).__name__, now)
                except Exception:
                    pass
    finally:
        _close(sess)
    return summary


def _loop() -> None:
    logger.info('Library mover started (tag %s)', qbit_owner.tag_name())
    while True:
        partial_cleanup.maybe_sweep()     # at startup, then every partial_cleanup.SWEEP_EVERY_S
        try:
            tick()
        except Exception as e:   # never let one bad pass end the thread
            logger.error('Library mover pass failed: %s', type(e).__name__)
        time.sleep(INTERVAL_S)


def start() -> bool:
    global _thread
    with _start_lock:
        if _thread and _thread.is_alive():
            return False
        _thread = threading.Thread(target=_loop, name='library-mover', daemon=True)
        _thread.start()
        return True
