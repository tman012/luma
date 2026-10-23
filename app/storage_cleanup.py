"""
storage_cleanup — downloads-folder disk usage + oldest-first space reclaim, through qBittorrent.

Backs the Home storage pill and the Downloads guard:
    GET  /api/storage/downloads          -> free/used/total snapshot
    POST /api/storage/downloads/cleanup  -> permanently delete the oldest downloads
                                            until `target_bytes` is reclaimed

v260 (PM): ported from prod 1b4ea74 to the test install, whose downloads folder is mounted read-only.
Nothing here deletes a file itself: every delete is qBittorrent's torrents/delete with deleteFiles=true, so
only data qBittorrent tracks can go (files qBittorrent doesn't know about are out of scope). The unit is a
download: every torrent whose data sits at the same path (a cross-seed is one unit, deleted together).

Deletion is PERMANENT. A download is a candidate only when all of these hold, for every torrent in it:
  * qBittorrent can be read (if not, nothing is deleted: QBIT_UNREADABLE)
  * finished: progress 1 and not in a busy state (tracker_files.BUSY_STATES)
  * in scope: every finished torrent ('all', the default) or only the ones this install added
  * its data, as Luma sees the path (Library paths' path map), is inside the guard's folder and inside no
    library folder
  * finished at least `min_age_days` ago (prod's 48 h, 2 days), and seeded and shared enough when those are on
  * imported: Luma's movie mover or TV importer has filed it, or Luma never tracked it (prod's own grabs,
    which prod's importer handles, as prod did). Waiting, mid-import, failed or refused imports stay.
  * Plex isn't playing it (read-only /status/sessions, matched by folder or file name); if Plex is set up
    but can't be read, nothing is deleted (PLEX_UNREADABLE)

Selection (deletion_order, the one order for every path and for Preview): the delete-first trackers in the
owner's order, then everything else, oldest first within each group. A cross-seed counts for a tracker only
when every tracker it is seeded on is a chosen one so far (prod's rule). Selection stops at the first download
that reaches the target. One run at a time (`_run_lock`).
"""

import logging
import os
import shutil
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

GIB = 1024 ** 3
# Prod's defaults; the live values are Downloads guard settings (app/automation/dg_store).
DEFAULT_TARGET_BYTES = 100 * GIB
MIN_AGE_HOURS = 48                    # prod; the setting is in days (min_age_days 2)
MAX_TARGET_BYTES = 2 * 1024 ** 4  # 2 TiB

QBIT_UNREADABLE = "Can't reach qBittorrent, so nothing was deleted"
PLEX_UNREADABLE = "Can't reach Plex, so nothing was deleted"
BUSY_RUN = 'A cleanup is already running'
DELETE_FAILED = "qBittorrent didn't delete them"

_INFO_TIMEOUT = 90
_run_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Settings and folder
# ---------------------------------------------------------------------------

def guard_settings() -> Dict[str, Any]:
    from app.automation import dg_store
    try:
        return dg_store.settings()
    except Exception:                                    # noqa: BLE001 - unreadable reads as prod's defaults
        logger.exception('storage_cleanup: Downloads guard settings unreadable')
        return dg_store.defaults()


def _media_root() -> str:
    """media.downloads_root (LUMA_DOWNLOADS_ROOT when set, else settings, else /downloads)."""
    from app import file_management as _fm
    if (os.environ.get(_fm.DOWNLOADS_ROOT_ENV) or '').strip().startswith('/'):
        return _fm.downloads_root()
    root = None
    try:
        from app.settings_manager import SettingsFile      # read as on disk; never rewrites settings.json
        root = SettingsFile().get('media.downloads_root')
    except Exception:                                   # noqa: BLE001
        root = None
    if not isinstance(root, str) or not root.strip().startswith('/'):
        root = _fm.DEFAULT_DOWNLOADS_ROOT
    return os.path.realpath(root)


def downloads_root(cfg: Optional[Dict[str, Any]] = None) -> str:
    """The guard's folder: the Downloads guard Folder setting, else media.downloads_root."""
    cfg = cfg if cfg is not None else guard_settings()
    folder = str(cfg.get('folder') or '').strip()
    return os.path.realpath(folder) if folder.startswith('/') else _media_root()


def usage(cfg: Optional[Dict[str, Any]] = None) -> Dict:
    """Disk-usage snapshot for the downloads filesystem (read-only)."""
    root = downloads_root(cfg)
    if not os.path.isdir(root):
        return {'path': root, 'available': False, 'total_bytes': 0, 'used_bytes': 0, 'free_bytes': 0}
    st = shutil.disk_usage(root)
    return {'path': root, 'available': True, 'total_bytes': st.total, 'used_bytes': st.used, 'free_bytes': st.free}


# ---------------------------------------------------------------------------
# Outside services (each replaced in tests)
# ---------------------------------------------------------------------------

def _session():
    from app import library_mover
    if 'settings' not in library_mover._cfg:
        return None, ''
    return library_mover._qbit_session(requests.Session)


def fetch_torrents() -> Optional[List[Dict[str, Any]]]:
    """Every torrent qBittorrent lists, or None when it can't be read."""
    from app import library_mover
    sess, url = _session()
    if sess is None:
        return None
    try:
        r = sess.get(f'{url}/api/v2/torrents/info', timeout=_INFO_TIMEOUT, allow_redirects=False)
        data = r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError) as e:
        logger.warning('storage_cleanup: torrents/info failed (%s)', type(e).__name__)
        data = None
    finally:
        library_mover._close(sess)
    return data if isinstance(data, list) else None


def delete_with_files(hashes: List[str]) -> bool:
    """qBittorrent's torrents/delete with deleteFiles=true for exactly these hashes."""
    from app import instance, library_mover
    if not hashes:
        return True
    sess, url = _session()
    if sess is None:
        return False
    try:
        with instance.scoped_delete('downloads_guard', hashes):
            r = sess.post(f'{url}/api/v2/torrents/delete',
                          data={'hashes': '|'.join(hashes), 'deleteFiles': 'true'},
                          timeout=30, allow_redirects=False)
        return r.status_code == 200
    except Exception as e:                              # noqa: BLE001 - WritesBlocked, network errors
        logger.error('storage_cleanup: torrents/delete failed (%s)', type(e).__name__)
        return False
    finally:
        library_mover._close(sess)


def plex_playing_files() -> Optional[List[str]]:
    """File paths Plex is playing now ([] when Plex isn't set up), or None when it can't be read. Read only."""
    try:
        from app.settings_manager import SettingsFile
        s = SettingsFile()
        url = str(s.get('plex.url') or '').rstrip('/')
        token = str(s.get('plex.token') or '')
    except Exception:                                   # noqa: BLE001
        return None
    if not url or not token:
        return []
    try:
        r = requests.get(f'{url}/status/sessions', headers={'X-Plex-Token': token, 'Accept': 'application/json'},
                         timeout=10, allow_redirects=False)
        if r.status_code != 200:
            return None
        mc = (r.json() or {}).get('MediaContainer') or {}
    except (requests.RequestException, ValueError, AttributeError):
        return None
    files: List[str] = []
    for item in mc.get('Metadata') or []:
        for media in (item or {}).get('Media') or []:
            for part in (media or {}).get('Part') or []:
                f = (part or {}).get('file')
                if isinstance(f, str) and f:
                    files.append(f)
    return files


MOVER_IMPORTED = ('done', 'exists')
TV_OPEN = ('downloading', 'importing')


def import_state(h: str, torrent: Dict[str, Any]) -> str:
    """'imported', 'untracked' (Luma never tracked it), 'pending' (waiting or mid-import) or 'not_imported'."""
    from app import file_management as fm
    h = (h or '').lower()
    job = (fm.load().get('jobs') or {}).get(h)
    tv_handoff = False
    if job:
        st = job.get('state')
        if st in MOVER_IMPORTED:
            return 'imported'
        if st == 'skipped' and job.get('error') == 'tv':
            tv_handoff = True
        else:
            return 'pending' if st == 'waiting' else 'not_imported'
    try:
        from app import tv_downloads, tv_store
        st = tv_downloads.status(h)
        if st:
            return 'imported' if st.get('state') == 'imported' else 'pending'
        with tv_store.connect() as conn:
            row = conn.execute('SELECT state FROM grabs WHERE hash = ?', (h,)).fetchone()
    except Exception:                                   # noqa: BLE001 - unreadable: never treat as imported
        logger.exception('storage_cleanup: TV import state unreadable')
        return 'pending'
    if row is not None:
        state = row[0]
        return 'imported' if state == 'imported' else 'pending' if state in TV_OPEN else 'not_imported'
    if tv_handoff or _is_ours(torrent):
        return 'pending'        # this install's download with no import record: never assume it was filed
    return 'untracked'


def _is_ours(t: Dict[str, Any]) -> bool:
    from app import qbit_owner, tv_downloads
    try:
        return qbit_owner.is_test_torrent(t) or tv_downloads.is_mine(t)
    except Exception:                                   # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _finished(t: Dict[str, Any]) -> bool:
    from app import tracker_files
    return _num(t.get('progress')) >= 1.0 and t.get('state') not in tracker_files.BUSY_STATES


def _in_use(local: str, playing: List[str]) -> bool:
    """Plex plays it: the download's folder or file name is a part of a playing file's path."""
    name = os.path.basename(local.rstrip('/'))
    if not name:
        return True
    for f in playing:
        if name in f.replace('\\', '/').split('/'):
            return True
    return False


def _units(torrents: List[Dict[str, Any]], cfg: Dict[str, Any], root: str, now: float,
           playing: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Downloads that may be deleted, oldest first, and how many were held back, by reason."""
    from app import file_management as fm
    from app import tracker_files
    store = fm.load()
    path_map = store.get('path_map') or []
    libraries = [os.path.realpath(p['path']) for p in store.get('paths') or [] if isinstance(p, dict) and p.get('path')]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for t in torrents:
        raw = str((t or {}).get('content_path') or '').replace('\\', '/').rstrip('/')
        if not raw or not t.get('hash'):
            continue
        local = os.path.normpath(fm.map_path(raw, path_map))
        groups.setdefault(local, []).append(t)

    min_age = cfg['min_age_days'] * 86400
    min_seed = cfg['min_seed_hours'] * 3600
    min_ratio = float(cfg['min_ratio'] or 0)
    held: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []

    def hold(why: str) -> None:
        held[why] = held.get(why, 0) + 1

    for local, ts in groups.items():
        if not local.startswith('/') or local == root or not fm._under(local, root):
            hold('outside folder')
            continue
        if any(fm._under(local, lib) or fm._under(lib, local) for lib in libraries):
            hold('library')
            continue
        if not all(_finished(t) for t in ts):
            hold('unfinished')
            continue
        if cfg['scope'] == 'test_tagged' and not all(_is_ours(t) for t in ts):
            hold('out of scope')
            continue
        done = [_num(t.get('completion_on')) for t in ts]
        if any(d <= 0 for d in done):
            hold('unknown age')
            continue
        newest = max(done)
        if now - newest < min_age:
            hold('too new')
            continue
        if min_seed and any(_num(t.get('seeding_time')) < min_seed for t in ts):
            hold('seed time')
            continue
        if min_ratio and any(_num(t.get('ratio')) < min_ratio for t in ts):
            hold('ratio')
            continue
        states = [import_state(str(t['hash']), t) for t in ts]
        if any(s not in ('imported', 'untracked') for s in states):
            hold('not imported')
            continue
        if _in_use(local, playing):
            hold('playing')
            continue
        keys = [tracker_files.classify(t) for t in ts]
        out.append({
            'name': os.path.basename(local),
            'path': local,
            'hashes': [str(t['hash']).lower() for t in ts],
            'size_bytes': int(max(_num(t.get('size')) for t in ts)),
            'mtime': newest,
            'trackers': sorted({k for k in keys if k}),
            'unclassified': any(k is None for k in keys),
            'tracker_label': ' + '.join(tracker_files.label_for(k) for k in sorted({k for k in keys if k})) or 'unknown',
        })
    out.sort(key=lambda c: (c['mtime'], c['name']))
    return out, held


def deletion_order(candidates: List[Dict[str, Any]], trackers: List[str]) -> List[Tuple[str, Dict[str, Any]]]:
    """v260 (Taylor via PM): THE deletion order, shared by scheduled runs, Clean up now and the Storage pill's
    Free up, and the order Preview lists. The delete-first trackers come first, in the owner's order (a download
    goes in the group of the last listed tracker it needs, so a cross-seed waits until every tracker it is on has
    come up), then everything else; oldest first within each group (finished time, then name)."""
    order: List[str] = []
    for t in trackers or []:
        t = str(t).strip()
        if t and t not in order:
            order.append(t)
    rank = {t: i for i, t in enumerate(order)}
    oldest = sorted(candidates, key=lambda c: (c['mtime'], c['name']))

    def group(c: Dict[str, Any]) -> int:
        keys = c.get('trackers') or []
        if c.get('unclassified') or not keys or any(k not in rank for k in keys):
            return len(order)
        return max(rank[k] for k in keys)
    ranked = sorted(oldest, key=group)                        # stable: oldest first inside each group
    return [('preferred' if group(c) < len(order) else 'fallback', c) for c in ranked]


def plan_cleanup(target_bytes: int = DEFAULT_TARGET_BYTES, cfg: Optional[Dict[str, Any]] = None,
                 now: Optional[float] = None) -> Dict:
    """Pick downloads in deletion_order() until they reach `target_bytes` (see the module docs). Deletes nothing."""
    cfg = cfg if cfg is not None else guard_settings()
    ceiling = int(cfg['max_run_gb']) * GIB
    target_bytes = max(0, min(int(target_bytes), ceiling))
    base = {'target_bytes': target_bytes, 'planned_bytes': 0, 'entries': [], 'eligible_count': 0, 'short': True,
            'preferred_available': 0, 'preferred_bytes': 0, 'fallback_bytes': 0, 'held': {}, 'error': None}

    torrents = fetch_torrents()
    if torrents is None:
        logger.warning('storage_cleanup: qBittorrent unreadable; nothing is deleted')
        return {**base, 'error': QBIT_UNREADABLE}
    playing = plex_playing_files()
    if playing is None:
        logger.warning('storage_cleanup: Plex sessions unreadable; nothing is deleted')
        return {**base, 'error': PLEX_UNREADABLE}

    root = downloads_root(cfg)
    candidates, held = _units(torrents, cfg, root, time.time() if now is None else now, playing)
    ordered = deletion_order(candidates, cfg['trackers'])

    selected: List[Dict] = []
    total = preferred = 0
    for phase, c in ordered:
        if total >= target_bytes:
            break
        selected.append({**c, 'phase': phase})
        total += c['size_bytes']
        if phase == 'preferred':
            preferred += c['size_bytes']
    return {**base, 'planned_bytes': total, 'entries': selected, 'eligible_count': len(candidates),
            'short': total < target_bytes, 'preferred_available': sum(1 for p, _ in ordered if p == 'preferred'),
            'preferred_bytes': preferred,
            'fallback_bytes': total - preferred, 'held': held}


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def run_cleanup(target_bytes: int = DEFAULT_TARGET_BYTES, cfg: Optional[Dict[str, Any]] = None) -> Dict:
    """PERMANENTLY delete downloads in deletion_order() (with their files, through qBittorrent) until
    `target_bytes` is freed. Every path (the scheduler, Clean up now, the Storage pill) comes through here.
    One run at a time; a second one gets BUSY_RUN."""
    if not _run_lock.acquire(blocking=False):
        return {'success': False, 'error': BUSY_RUN, 'freed_bytes': 0, 'deleted_count': 0, 'deleted': [],
                'failed': [], 'short': True}
    try:
        return _run(target_bytes, cfg if cfg is not None else guard_settings())
    finally:
        _run_lock.release()


def running() -> bool:
    return _run_lock.locked()


def _run(target_bytes: int, cfg: Dict[str, Any]) -> Dict:
    root = downloads_root(cfg)
    if not os.path.isdir(root):
        return {'success': False, 'error': f'Downloads folder not found: {root}'}
    before = usage(cfg)
    plan = plan_cleanup(target_bytes, cfg)
    if plan.get('error'):
        return {'success': False, 'error': plan['error'], 'before': before, 'after': before, 'freed_bytes': 0,
                'deleted_count': 0, 'deleted': [], 'failed': [], 'short': True}

    deleted: List[Dict] = []
    failed: List[str] = []
    freed = 0
    by_phase = {'preferred': 0, 'fallback': 0}
    for entry in plan['entries']:
        if delete_with_files(entry['hashes']):
            deleted.append({'name': entry['name'], 'size_bytes': entry['size_bytes'], 'mtime': entry['mtime'],
                            'tracker_label': entry.get('tracker_label'), 'phase': entry.get('phase', 'preferred')})
            freed += entry['size_bytes']
            by_phase[entry.get('phase', 'preferred')] += entry['size_bytes']
        else:
            failed.append(entry['name'])
    after = usage(cfg)
    logger.info('storage_cleanup: deleted %d downloads through qBittorrent, %.1f GB '
                '(%.1f GB chosen trackers + %.1f GB other, target %.1f GB, %d failed)',
                len(deleted), freed / GIB, by_phase['preferred'] / GIB, by_phase['fallback'] / GIB,
                plan['target_bytes'] / GIB, len(failed))
    return {
        'success': True,
        'target_bytes': plan['target_bytes'],
        'freed_bytes': freed,
        'freed_preferred_bytes': by_phase['preferred'],
        'freed_fallback_bytes': by_phase['fallback'],
        'deleted_count': len(deleted),
        'deleted': deleted,
        'failed': failed,
        'short': plan['short'],
        'eligible_count': plan['eligible_count'],
        'preferred_available': plan.get('preferred_available', 0),
        'before': before,
        'after': after,
    }
