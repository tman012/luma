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
  the owner of the library path. No chmod 777, no Telegram.
* Anything under <downloads>/incomplete/ or named *.!qB is never processed.
* A tracked torrent that is gone from qBittorrent is dropped after a few
  checks; one that isn't ours is dropped at once.
* Movies only for now; TV grabs are recorded as skipped (later pass).
"""

from __future__ import annotations

import errno
import logging
import os
import re
import stat as _stat
import threading
import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from app import file_management as fm
from app import qbit_owner
from app.paced_copy import _paced_copy

logger = logging.getLogger(__name__)

_cfg: Dict[str, Any] = {}
_thread: Optional[threading.Thread] = None
_start_lock = threading.Lock()

INTERVAL_S = 30
BATCH = 50
ORPHAN_MISSES = 3          # checks in a row a tracked torrent may be missing
MAX_ATTEMPTS = 5           # failed transfers before a job is marked failed
RELEASE_WAIT_S = 600       # how long a missing TMDB date may hold a job
EXTRACT_WAIT_S = 3600      # prod: wait up to an hour for an unpack
MIN_VIDEO_BYTES = 100 * 1024 * 1024
STABLE_WAIT_S = 3          # prod's is_file_stable window
MAX_JOBS_WAITING = 500
# Prod's completed states, plus qBittorrent 5's names for them.
COMPLETED_STATES = ('completed', 'pausedUP', 'stalledUP', 'uploading', 'queuedUP',
                    'stoppedUP', 'forcedUP')
VIDEO_EXTS = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.m2ts')
_HASH_RE = re.compile(r'^([0-9a-f]{40}|[0-9a-f]{64})$')
TERMINAL = ('done', 'exists', 'failed', 'skipped')


class Pending(Exception):
    """Not ready yet (unpacking, still being written, waiting for TMDB)."""


class Refused(Exception):
    """Will never work as things stand (unsafe path, nothing to file)."""


class InsufficientSpace(Exception):
    """The library folder can't hold the copy. Recorded, never retried."""


INSUFFICIENT_SPACE = 'insufficient space'
SPACE_MARGIN_BYTES = 64 * 1024 * 1024


def free_bytes(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def _check_space(folder: str, need: int) -> None:
    try:
        free = free_bytes(folder)
    except OSError:
        return   # can't tell (some shares); the copy itself will say
    if free < need + SPACE_MARGIN_BYTES:
        raise InsufficientSpace(INSUFFICIENT_SPACE)


def init(*, settings) -> None:
    _cfg['settings'] = settings


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

def _qbit_session(session_factory) -> Tuple[Optional[Any], str]:
    from app.qbit_connector import normalize_url, text_of
    s = _cfg['settings']
    url = normalize_url(s.get('qbittorrent.url') or '')
    if not url:
        return None, ''
    sess = session_factory()
    try:
        r = sess.post(f'{url}/api/v2/auth/login',
                      data={'username': s.get('qbittorrent.username') or '',
                            'password': s.get('qbittorrent.password') or ''}, timeout=10)
    except requests.RequestException as e:
        logger.warning('Library mover: qBittorrent unreachable (%s)', type(e).__name__)
        return None, url
    if r.status_code != 200 or text_of(r).strip() != 'Ok.':
        logger.warning('Library mover: qBittorrent sign-in failed (%s)', r.status_code)
        return None, url
    return sess, url


def _torrents(sess, url: str, hashes: List[str]) -> Optional[Dict[str, Dict[str, Any]]]:
    """{hash: torrent} for exactly these hashes; None when qBit didn't answer."""
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(hashes), BATCH):
        batch = hashes[i:i + BATCH]
        try:
            r = sess.get(f'{url}/api/v2/torrents/info', params={'hashes': '|'.join(batch)}, timeout=10)
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


def find_video_files(content: str, root: str, added: float, now: float,
                     temp_dirs: Tuple[str, ...] = ()) -> List[Tuple[str, int]]:
    """[(path, size)] of the movie's video files inside *content* (a file or
    folder under the real downloads *root*). Raises Pending / Refused.
    Port of prod's find_video_files; symlinks and anything in <root>/incomplete,
    qBittorrent's temp folder (*temp_dirs*) or named *.!qB are never touched."""
    real = os.path.realpath(content)
    for tmp in (os.path.join(root, 'incomplete'),) + tuple(temp_dirs):
        if fm._under(real, os.path.realpath(tmp)):
            raise Pending('still in incomplete')
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
        return [(real, os.path.getsize(real))]
    if not os.path.isdir(real):
        raise Pending('not there yet')
    videos: List[Tuple[str, int]] = []
    rar_only = False
    for dirpath, dirnames, files in os.walk(real, followlinks=False):
        if '.extraction_in_progress' in files:
            raise Pending('unpacking')
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        has_rar = has_video = False
        for f in sorted(files):
            full = os.path.join(dirpath, f)
            if os.path.islink(full):
                continue
            if f.lower().endswith('.!qb'):
                raise Pending('still downloading')
            if f.endswith('.rar') or re.match(r'.*\.r\d+$', f):
                has_rar = True
            if not _is_video(f):
                continue
            size = os.path.getsize(full)
            if size < MIN_VIDEO_BYTES:
                continue
            if not is_file_stable(full):
                raise Pending('still being written')
            videos.append((full, size))
            has_video = True
        if has_rar and not has_video:
            rar_only = True
    if not videos and rar_only and now - added < EXTRACT_WAIT_S:
        raise Pending('unpacking')
    if not videos:
        raise Refused('no video files')
    return sorted(videos)


def select_main(videos: List[Tuple[str, int]]) -> List[str]:
    """Prod's select_main_video_files for movies: the largest when it is 5x the
    next, else every file (multi-part), largest first, minus likely duplicates."""
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


def _finish(path: str, mode: int, owner: Optional[Tuple[int, int]]) -> None:
    """664/775 and, as root, the library path's owner. Only ever called on a
    file or folder this pass just created (never a symlink)."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass   # CIFS may force its own modes
    if owner:
        try:
            os.chown(path, owner[0], owner[1], follow_symlinks=False)
        except OSError as e:
            logger.info('Library mover: chown skipped (%s)', type(e).__name__)


def _ensure_folder(root: str, folder: str, owner) -> str:
    """root/folder, created 775 if missing; refused when it is a symlink or
    resolves outside root."""
    target = os.path.join(root, folder)
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        os.mkdir(target, 0o775)
        _finish(target, 0o775, owner)
        st = os.lstat(target)
    if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISDIR(st.st_mode):
        raise Refused('destination folder is not a plain folder')
    if os.path.realpath(target) != target or not fm._under(target, root):
        raise Refused('destination folder resolves elsewhere')
    return target


def transfer(src: str, dst: str, mode: str, owner) -> str:
    """'linked', 'copied' or 'exists'. Never overwrites, never moves."""
    if os.path.lexists(dst):
        return 'exists'
    if mode == 'hardlink':
        try:
            os.link(src, dst, follow_symlinks=False)
            return 'linked'   # same inode as the seeding download: modes left alone
        except FileExistsError:
            return 'exists'
        except OSError as e:
            logger.info('Library mover: hardlink failed (%s), copying', errno.errorcode.get(e.errno, e.errno))
    folder, name = os.path.split(dst)
    tmp = os.path.join(folder, f'.{name}.luma-partial')
    _check_space(folder, os.path.getsize(src))
    try:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        try:
            _paced_copy(src, tmp, exclusive=True)
        except OSError as e:
            if e.errno in (errno.ENOSPC, getattr(errno, 'EDQUOT', -1)):
                raise InsufficientSpace(INSUFFICIENT_SPACE) from e
            raise
        if os.path.getsize(tmp) != os.path.getsize(src):
            raise OSError(errno.EIO, 'size mismatch after copy')
        _finish(tmp, 0o664, owner)
        try:
            os.link(tmp, dst)          # atomic, refuses an existing name
        except FileExistsError:
            return 'exists'
        except OSError:
            if os.path.lexists(dst):
                return 'exists'
            os.rename(tmp, dst)
            return 'copied'
        return 'copied'
    finally:
        try:
            if os.path.lexists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


# --------------------------------------------------------------------------- one job

def process(job: Dict[str, Any], torrent: Dict[str, Any], data: Dict[str, Any], now: float) -> Dict[str, Any]:
    """File one completed movie. Returns the job fields to save. Raises
    Pending (try again next pass) or Refused (give up)."""
    path_map = data['path_map']
    save_path = fm.map_path(str(torrent.get('save_path') or ''), path_map)
    content = fm.map_path(str(torrent.get('content_path') or ''), path_map)
    if not save_path.startswith('/') or not content.startswith('/'):
        raise Refused('qBittorrent gave no download path')
    root = os.path.realpath(save_path)
    if fm.blocked(root) or not os.path.isdir(root):
        raise Refused('download folder not available')
    lib_roots = [os.path.realpath(p['path']) for p in data['paths']]
    if any(fm._under(root, lr) or fm._under(lr, root) for lr in lib_roots):
        raise Refused('download folder overlaps a library path')

    temp_dirs = ()
    dl_path = str(torrent.get('download_path') or '')
    if dl_path.startswith('/'):
        mapped = fm.map_path(dl_path, path_map)
        if os.path.realpath(mapped) != root:
            temp_dirs = (mapped,)
    files = select_main(find_video_files(content, root, job.get('added') or now, now, temp_dirs))

    info = fm.classify(job.get('name') or str(torrent.get('name') or ''), os.path.basename(files[0]))
    if fm.needs_release_date(data):
        if 'release_date' not in job:
            answered, d = _release_date(job.get('tmdb_id'))
            if not answered and now - (job.get('added') or now) < RELEASE_WAIT_S:
                raise Pending('waiting for TMDB')
            job['release_date'] = d.isoformat() if d else ''
        if job.get('release_date'):
            info['release_date'] = date.fromisoformat(job['release_date'])
    dest = fm.pick_destination(data, info)
    if not dest:
        raise Refused('no destination')
    lib_root = os.path.realpath(dest['path'])
    if fm.blocked(lib_root) or not os.path.isdir(lib_root):
        raise Pending('library folder not available')
    owner = _owner_of(lib_root)

    year = job.get('year') or (job.get('release_date') or '')[:4]
    multi = len(files) > 1
    folder_name, _ = fm.movie_names(job.get('title'), year, '')
    if data['transfer'] == 'copy':
        # Everything that still has to be copied must fit (hardlinks need no room).
        need = 0
        for i, src in enumerate(files, 1):
            _, name = fm.movie_names(job.get('title'), year, os.path.splitext(src)[1], part=i if multi else None)
            if not os.path.lexists(os.path.join(lib_root, folder_name, name)):
                need += os.path.getsize(src)
        if need:
            _check_space(lib_root, need)
    folder = _ensure_folder(lib_root, folder_name, owner)
    results = []
    for i, src in enumerate(files, 1):
        if not _real_under(src, root):
            raise Refused('outside the downloads folder')
        _, name = fm.movie_names(job.get('title'), year, os.path.splitext(src)[1], part=i if multi else None)
        dst = os.path.join(folder, name)
        if not fm._under(os.path.abspath(dst), folder):
            raise Refused('unsafe file name')
        results.append({'file': name, 'result': transfer(src, dst, data['transfer'], owner)})
    state = 'done' if any(r['result'] != 'exists' for r in results) else 'exists'
    return {'state': state, 'path_id': dest['id'], 'dest': folder, 'files': results, 'finished': now,
            'release_date': job.get('release_date', ''), 'error': ''}


# --------------------------------------------------------------------------- pass

def tick(now: Optional[float] = None, session_factory: Callable = requests.Session) -> Dict[str, int]:
    """One pass over the waiting jobs. Never contacts qBittorrent when no
    library path is configured or nothing is waiting."""
    now = time.time() if now is None else now
    summary = {'checked': 0, 'filed': 0, 'dropped': 0}
    data = fm.load()
    if not data['paths']:
        return summary
    waiting = {h: j for h, j in data['jobs'].items() if j.get('state') == 'waiting'}
    if not waiting:
        return summary
    sess, url = _qbit_session(session_factory)
    if not sess:
        return summary
    try:
        torrents = _torrents(sess, url, sorted(waiting))
    finally:
        close = getattr(sess, 'close', None)
        if close:
            close()
    if torrents is None:
        return summary
    tag = qbit_owner.tag_name()
    for h, job in waiting.items():
        summary['checked'] += 1
        t = torrents.get(h)
        if t is None:
            misses = int(job.get('misses') or 0) + 1
            if misses >= ORPHAN_MISSES:
                _drop_job(h, 'removed from qBittorrent')
                summary['dropped'] += 1
            else:
                _set_job(h, misses=misses)
            continue
        if not qbit_owner.is_test_torrent(t, tag):
            _drop_job(h, f'not tagged {tag}')
            summary['dropped'] += 1
            continue
        if job.get('misses'):
            _set_job(h, misses=0)
        if job.get('media_type') != 'movie':
            _set_job(h, state='skipped', error='tv', finished=now)
            continue
        if not (t.get('progress') == 1 and t.get('state') in COMPLETED_STATES):
            continue
        try:
            fields = process(dict(job), t, data, now)
        except Pending as e:
            logger.debug('Library mover: %s waiting (%s)', job.get('title'), e)
            continue
        except Refused as e:
            logger.warning('Library mover: not moving %s (%s)', job.get('title'), e)
            _set_job(h, state='skipped', error=str(e), finished=now)
            continue
        except InsufficientSpace:
            logger.warning('Library mover: not moving %s (%s)', job.get('title'), INSUFFICIENT_SPACE)
            _set_job(h, state='skipped', error=INSUFFICIENT_SPACE, finished=now)
            continue
        except OSError as e:
            attempts = int(job.get('attempts') or 0) + 1
            err = errno.errorcode.get(e.errno, type(e).__name__) if e.errno else type(e).__name__
            logger.error('Library mover: copy failed for %s (%s, attempt %d/%d)',
                         job.get('title'), err, attempts, MAX_ATTEMPTS)
            if attempts >= MAX_ATTEMPTS:
                _set_job(h, state='failed', attempts=attempts, error=err, finished=now)
            else:
                _set_job(h, attempts=attempts, error=err)
            continue
        _set_job(h, **fields)
        summary['filed'] += 1
        logger.info('Library mover: %s -> %s (%s)', job.get('title'), fields['dest'],
                    ', '.join(r['result'] for r in fields['files']))
    return summary


def _loop() -> None:
    logger.info('Library mover started (tag %s)', qbit_owner.tag_name())
    while True:
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
