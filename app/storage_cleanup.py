"""
storage_cleanup — downloads-folder disk usage + oldest-first space reclaim.

Backs the compact-home storage pill:
    GET  /api/storage/downloads          -> free/used/total snapshot
    POST /api/storage/downloads/cleanup  -> permanently delete oldest entries
                                            until `target_bytes` is reclaimed

Deletion is PERMANENT — there is no trash/undo. Guards, applied in order:
  * only paths inside DOWNLOADS_ROOT are ever considered (realpath-checked;
    symlinks are unlinked, never followed out of the tree)
  * qBittorrent staging dirs ("incomplete"/"complete") and dotfiles are skipped
  * entries newer than MIN_AGE_HOURS are skipped — an in-flight download or one
    the auto-renamer has not imported yet
  * entries backing an UNFINISHED qBittorrent torrent are skipped, matched by
    name so container path mapping can't produce a false negative

Sizing/ordering treats each TOP-LEVEL entry as one unit: a torrent folder is
deleted whole, and its age is the NEWEST mtime anywhere inside it (so a folder
with recent activity is never mistaken for an old one).

Selection can be biased toward particular trackers via `prefer_trackers`, which
spends a tracker's back catalogue first and only widens to everything else once
that tracker is exhausted (see `plan_cleanup`). Attribution comes from
`tracker_files`; when a preference is asked for and attribution is unavailable,
the run is refused rather than quietly widened — see `plan_cleanup`'s note.
"""

import os
import shutil
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Default reclaim target: 100 GB (binary GiB, matching how free space is shown).
DEFAULT_TARGET_BYTES = 100 * 1024 ** 3

# Never delete an entry touched more recently than this.
MIN_AGE_HOURS = 48

# qBittorrent staging directories — deleting these breaks in-flight downloads.
PROTECTED_NAMES = {'incomplete', 'complete', 'temp', '.recycle'}

# Partial-download suffixes written by qBittorrent / other clients.
PARTIAL_SUFFIXES = ('.!qb', '.part', '.parts', '.tmp', '.downloading')

# Shown on prod and test builds alike, so it names neither install.
QBIT_UNREADABLE = "Can't reach qBittorrent, so nothing was deleted"

# Hard ceiling on a single run so a bad target can't empty the folder.
MAX_TARGET_BYTES = 2 * 1024 ** 4  # 2 TiB


def downloads_root() -> str:
    """Resolve the downloads directory from settings, falling back to Config."""
    root = None
    try:
        from app.settings_manager import SettingsManager
        root = SettingsManager().get('media.downloads_root')
    except Exception:
        root = None
    if not root:
        try:
            from app.config import Config
            root = Config.DOWNLOADS_ROOT
        except Exception:
            root = '/downloads'
    return os.path.realpath(root)


def usage() -> Dict:
    """Return a disk-usage snapshot for the downloads filesystem."""
    root = downloads_root()
    if not os.path.isdir(root):
        return {
            'path': root,
            'available': False,
            'total_bytes': 0,
            'used_bytes': 0,
            'free_bytes': 0,
        }
    st = shutil.disk_usage(root)
    return {
        'path': root,
        'available': True,
        'total_bytes': st.total,
        'used_bytes': st.used,
        'free_bytes': st.free,
    }


# ---------------------------------------------------------------------------
# Candidate scanning
# ---------------------------------------------------------------------------

def _entry_stats(path: str) -> tuple:
    """Return (size_bytes, newest_mtime) for a file or directory tree."""
    try:
        st = os.lstat(path)
    except OSError:
        return (0, 0.0)

    if not os.path.isdir(path) or os.path.islink(path):
        return (st.st_size, st.st_mtime)

    total = 0
    newest = st.st_mtime
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in dirnames + filenames:
            try:
                child = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            if not os.path.isdir(os.path.join(dirpath, name)) or os.path.islink(os.path.join(dirpath, name)):
                total += child.st_size
            if child.st_mtime > newest:
                newest = child.st_mtime
    return (total, newest)


def _qbit_snapshot() -> Tuple[Optional[Dict[str, Dict[str, Any]]], Set[str]]:
    """One qBittorrent read, serving both guards: (attribution, protected names).

    They come from the same payload but are keyed differently on purpose.

    Attribution is keyed strictly on where the data actually lives
    (``content_path``), because mis-keying it would attribute an entry to a
    tracker it doesn't belong to and delete the wrong file.

    Protection is keyed on the torrent's ``name`` *as well*, which is broader
    than the data's real location. That asymmetry is deliberate: an extra
    protected name can only ever skip a deletion, never cause one, so erring
    wide here is free while erring wide in attribution is not.

    Attribution is None when qBittorrent is unreachable; protection degrades to
    an empty set, leaving the mtime guard — which is what the manual pill has
    always relied on.
    """
    from app import tracker_files

    torrents = tracker_files.fetch_torrents()
    if torrents is None:
        logger.warning("storage_cleanup: qBittorrent unreachable — relying on age guard only")
        return None, set()

    protected: Set[str] = set(luma_test_entry_names(torrents))
    for t in torrents:
        if not ((t.get('progress') or 0) < 1.0 or t.get('state') in tracker_files.BUSY_STATES):
            continue
        for key in ('name', 'content_path', 'root_path'):
            val = t.get(key)
            if val:
                protected.add(os.path.basename(str(val).rstrip('/')))

    return tracker_files.by_entry(torrents), protected


def luma_test_entry_names(torrents: List[Dict[str, Any]]) -> Set[str]:
    """Entry names that hold data of torrents the test install added.

    Production and the test install (LUMA_MINIMAL, :5002) can share one
    qBittorrent; the test install tags its torrents (app/qbit_owner) and copies
    them into its own library some time after they finish. Their data is never
    a cleanup candidate here, finished or not. Only each torrent's own entry is
    named (the last part of its data path): a category folder that holds it is
    not, so its other downloads stay cleanable. scan_candidates never offers a
    folder holding a protected entry whole; it offers that folder's other
    entries one by one instead.
    """
    from app.qbit_owner import is_test_torrent

    names: Set[str] = set()
    for t in torrents:
        if not is_test_torrent(t):
            continue
        for key in ('name', 'content_path', 'root_path'):
            val = t.get(key)
            if not val:
                continue
            part = os.path.basename(str(val).replace('\\', '/').rstrip('/'))
            if part and part not in ('.', '..'):
                names.add(part)
    return names


HOLD_DEPTH = 4   # how deep a folder is searched for a protected entry


def _holds(path: str, protected: Set[str], depth: int = HOLD_DEPTH) -> bool:
    """True when a protected name sits anywhere within *depth* levels under
    *path* (a folder). Unreadable means True: never delete what can't be seen."""
    if not protected or depth <= 0:
        return depth <= 0 and bool(protected)
    try:
        with os.scandir(path) as it:
            entries = list(it)
    except OSError:
        return True
    for e in entries:
        if e.name in protected:
            return True
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False) and _holds(e.path, protected, depth - 1):
                return True
        except OSError:
            return True
    return False


def scan_candidates(root: Optional[str] = None,
                    snapshot: Optional[Tuple] = None) -> List[Dict]:
    """List deletable top-level entries, oldest first.

    Each candidate: {name, path, size_bytes, mtime, is_dir, trackers,
    tracker_label, attributed}. `snapshot` lets a caller reuse one qBittorrent
    read across the scan and its own checks instead of asking twice.
    """
    from app import tracker_files

    root = root or downloads_root()
    if not os.path.isdir(root):
        return []

    attribution, protected = snapshot if snapshot is not None else _qbit_snapshot()
    cutoff = time.time() - MIN_AGE_HOURS * 3600
    candidates: List[Dict] = []

    def eligible(name: str) -> bool:
        return not (name.startswith('.') or name.lower() in PROTECTED_NAMES
                    or name.lower().endswith(PARTIAL_SUFFIXES) or name in protected)

    entries: List[Tuple[str, str, str]] = []     # (shown name, path, entry name)
    for name in os.listdir(root):
        if not eligible(name):
            continue
        path = os.path.join(root, name)
        if protected and os.path.isdir(path) and not os.path.islink(path) and _holds(path, protected):
            # A folder (a category, say) holding the test install's data: its
            # other entries are candidates one by one, never the folder.
            try:
                children = os.listdir(path)
            except OSError:
                continue
            for child in children:
                cpath = os.path.join(path, child)
                if not eligible(child):
                    continue
                if os.path.isdir(cpath) and not os.path.islink(cpath) and _holds(cpath, protected):
                    continue
                entries.append((f'{name}/{child}', cpath, child))
            continue
        entries.append((name, path, name))

    for name, path, entry in entries:
        # Refuse anything that resolves outside the downloads tree.
        if not os.path.realpath(path).startswith(root + os.sep):
            continue

        size, mtime = _entry_stats(path)
        if mtime > cutoff:
            continue
        if size <= 0:
            continue

        record = (attribution or {}).get(entry)
        trackers = sorted(tracker_files.entry_trackers(record))
        candidates.append({
            'name': name,
            'path': path,
            'size_bytes': size,
            'mtime': mtime,
            'is_dir': os.path.isdir(path) and not os.path.islink(path),
            'trackers': trackers,
            # A list, because a cross-seeded entry belongs to more than one.
            'tracker_label': ' + '.join(tracker_files.label_for(t) for t in trackers)
                             or ('unknown' if attribution is not None else 'unavailable'),
            # False for an entry qBittorrent has no torrent for (an orphan the
            # client forgot) and for one whose announce host isn't recognised.
            'attributed': bool(trackers) and not (record or {}).get('unclassified'),
        })

    candidates.sort(key=lambda c: c['mtime'])
    return candidates


def plan_cleanup(target_bytes: int = DEFAULT_TARGET_BYTES, root: Optional[str] = None,
                 prefer_trackers: Optional[List[str]] = None,
                 snapshot: Optional[Tuple] = None,
                 require_qbit: bool = True) -> Dict:
    """Pick the oldest entries that together reach `target_bytes`.

    Selection stops at the first entry that meets or exceeds the target, so the
    final total is >= target (never short by design, unless the folder runs out
    of eligible entries).

    With `prefer_trackers`, selection runs in two passes: oldest-first through
    entries belonging *only* to those trackers, then — if the target still isn't
    met — oldest-first through everything else. So a tracker's back catalogue is
    spent before anything else is touched, and running that tracker dry widens
    the net instead of stalling.

    If a preference is asked for but qBittorrent can't be reached, the plan is
    refused with an `error` rather than falling through to pass two. "Spend the
    other trackers because this one is empty" and "spend them because I couldn't
    tell them apart" are different situations that would look identical in the
    log, and only the first is what a tracker preference asked for.
    """
    target_bytes = max(0, min(int(target_bytes), MAX_TARGET_BYTES))
    prefer = {str(t).strip() for t in (prefer_trackers or []) if str(t).strip()}

    if snapshot is None:
        snapshot = _qbit_snapshot()
    attribution = snapshot[0]

    # The unattended guard (require_qbit) deletes nothing while qBittorrent
    # can't be read: without it, the test install's downloads can't be told
    # apart from production's, and they may not be copied yet.
    if require_qbit and attribution is None:
        return {
            'target_bytes': target_bytes, 'planned_bytes': 0, 'entries': [],
            'eligible_count': 0, 'short': True,
            'preferred_bytes': 0, 'fallback_bytes': 0,
            'error': QBIT_UNREADABLE,
        }

    if prefer and attribution is None:
        return {
            'target_bytes': target_bytes, 'planned_bytes': 0, 'entries': [],
            'eligible_count': 0, 'short': True,
            'preferred_bytes': 0, 'fallback_bytes': 0,
            'error': 'qBittorrent unreachable — cannot tell which tracker a file '
                     'belongs to, so a tracker-scoped cleanup was not attempted',
        }

    candidates = scan_candidates(root, snapshot=snapshot)

    from app import tracker_files
    if prefer:
        first = [c for c in candidates
                 if tracker_files.belongs_only_to(
                     (attribution or {}).get(c['name']), prefer)]
        chosen_names = {c['name'] for c in first}
        second = [c for c in candidates if c['name'] not in chosen_names]
    else:
        first, second = candidates, []

    selected: List[Dict] = []
    total = 0
    preferred_bytes = 0
    for phase, group in (('preferred', first), ('fallback', second)):
        for c in group:
            if total >= target_bytes:
                break
            selected.append({**c, 'phase': phase})
            total += c['size_bytes']
            if phase == 'preferred':
                preferred_bytes += c['size_bytes']

    return {
        'target_bytes': target_bytes,
        'planned_bytes': total,
        'entries': selected,
        'eligible_count': len(candidates),
        'short': total < target_bytes,
        # Split out so the log can say whether the preference actually covered
        # the target or the run had to reach past it.
        'preferred_available': len(first) if prefer else 0,
        'preferred_bytes': preferred_bytes,
        'fallback_bytes': total - preferred_bytes,
        'error': None,
    }


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def _delete_entry(entry: Dict) -> bool:
    path = entry['path']
    try:
        if entry['is_dir']:
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True
    except Exception as e:
        logger.error("storage_cleanup: failed to delete %s: %s", path, e)
        return False


def _forget_torrents(names: List[str]) -> int:
    """Drop qBittorrent entries whose data we just deleted (files already gone).

    Best-effort: leaving stale entries only makes qBit show "missing files",
    so a failure here is logged and ignored.
    """
    if not names:
        return 0
    try:
        from app.auto_rename import auto_renamer
        session = auto_renamer.get_authenticated_session()
        if not session:
            return 0
        url = auto_renamer._qbit_settings()[0]
        resp = session.get(f"{url}/api/v2/torrents/info", timeout=10)
        if resp.status_code != 200:
            return 0
        wanted = set(names)
        hashes = [
            t['hash'] for t in resp.json()
            if t.get('hash') and (
                t.get('name') in wanted
                or os.path.basename(str(t.get('content_path') or '').rstrip('/')) in wanted
            )
        ]
        if not hashes:
            return 0
        session.post(
            f"{url}/api/v2/torrents/delete",
            data={'hashes': '|'.join(hashes), 'deleteFiles': 'false'},
            timeout=15,
        )
        return len(hashes)
    except Exception as e:
        logger.warning("storage_cleanup: could not remove qBittorrent entries: %s", e)
        return 0


def run_cleanup(target_bytes: int = DEFAULT_TARGET_BYTES,
                prefer_trackers: Optional[List[str]] = None,
                require_qbit: bool = True) -> Dict:
    """PERMANENTLY delete the oldest downloads until `target_bytes` is freed.

    With `prefer_trackers`, spends those trackers' oldest entries first and only
    widens to the rest once they run out — see `plan_cleanup`.

    Returns before/after usage plus what was removed. Never raises for a single
    failed delete — it moves on and reports the shortfall.
    """
    root = downloads_root()
    if not os.path.isdir(root):
        return {
            'success': False,
            'error': f'Downloads directory not found: {root}',
        }

    before = usage()
    plan = plan_cleanup(target_bytes, root, prefer_trackers=prefer_trackers,
                        require_qbit=require_qbit)
    if plan.get('error'):
        return {'success': False, 'error': plan['error'], 'before': before,
                'after': before, 'freed_bytes': 0, 'deleted_count': 0,
                'deleted': [], 'failed': [], 'short': True}

    deleted: List[Dict] = []
    failed: List[str] = []
    freed = 0
    freed_by_phase = {'preferred': 0, 'fallback': 0}
    for entry in plan['entries']:
        if _delete_entry(entry):
            deleted.append({'name': entry['name'], 'size_bytes': entry['size_bytes'],
                            'mtime': entry['mtime'],
                            'tracker_label': entry.get('tracker_label'),
                            'phase': entry.get('phase', 'preferred')})
            freed += entry['size_bytes']
            freed_by_phase[entry.get('phase', 'preferred')] += entry['size_bytes']
        else:
            failed.append(entry['name'])

    forgotten = _forget_torrents([os.path.basename(d['name']) for d in deleted])
    after = usage()

    logger.info(
        "storage_cleanup: freed %.1f GB across %d entries "
        "(%.1f GB preferred + %.1f GB other, target %.1f GB, %d failed, %d qBit entries removed)",
        freed / 1024 ** 3, len(deleted),
        freed_by_phase['preferred'] / 1024 ** 3, freed_by_phase['fallback'] / 1024 ** 3,
        plan['target_bytes'] / 1024 ** 3, len(failed), forgotten,
    )

    return {
        'success': True,
        'target_bytes': plan['target_bytes'],
        'freed_bytes': freed,
        'freed_preferred_bytes': freed_by_phase['preferred'],
        'freed_fallback_bytes': freed_by_phase['fallback'],
        'deleted_count': len(deleted),
        'deleted': deleted,
        'failed': failed,
        'short': plan['short'],
        'eligible_count': plan['eligible_count'],
        'preferred_available': plan.get('preferred_available', 0),
        'before': before,
        'after': after,
    }
