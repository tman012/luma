"""v260 (PM / Ops): one-time prune of download_monitor.json (meta key dlmon_prune_v260 in tv.db).

auto_rename's download_monitor.json can hold entries for torrents qBittorrent no longer has (today: The
Manchurian Candidate, …201e). Once, at boot on minimal (test) builds, in the Downloads guard's boot thread
(downloads_guard.startup_seed_and_log), so boot never waits on qBittorrent:

1. Ask qBittorrent which torrents it has, through the guard's own read-only torrents/info call
   (storage_cleanup.fetch_torrents). If qBittorrent can't be reached, errors, or lists no torrent at all,
   nothing is pruned: one WARNING, and the migration is tried again on the next start. Nothing is written
   to qBittorrent.
2. Back up download_monitor.json (in the data dir, paths.data_path; LUMA_DATA_DIR respected) to a
   timestamped .bak next to it.
3. Drop the entries whose torrent hash qBittorrent doesn't list (case-insensitive). Keys that aren't a
   torrent hash stay. Sanity limit first (Tester BLOCK on e055c3d): if the drop would be more than
   max(10, 10% of the hash entries), or qBittorrent lists fewer torrents than would be dropped, the list is
   treated as partial (a qBittorrent fault or the wrong instance): nothing is backed up or written, one
   WARNING, and the migration is tried again on the next start.
4. One INFO line with the count and the last 4 of each dropped hash; then the meta key is set.

While qBittorrent stays unreadable, only the first start in a row warns; later ones log INFO (meta key
dlmon_prune_v260_retry holds the reason already warned about).

grab_origins.json is never read or written. Everything runs in try/except: a failure logs and boot goes on.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

META_KEY = 'dlmon_prune_v260'
RETRY_KEY = 'dlmon_prune_v260_retry'
MIN_LIMIT = 10                     # never refuse a drop of this many or fewer
MAX_SHARE = 0.10                   # ... unless it is more than this share of the hash entries
MONITOR_FILE = 'download_monitor.json'
_HASH = re.compile(r'^[0-9a-f]{40}$|^[0-9a-f]{64}$')
_cfg: Dict[str, Any] = {}          # tests: {'now': callable}


def monitor_path() -> str:
    from app import paths
    return str(paths.data_path(MONITOR_FILE))


def known_torrents() -> Optional[Tuple[Set[str], int]]:
    """(lower-case hashes, torrent count) of what qBittorrent lists (read-only torrents/info), or None when it
    can't be read or lists none (an empty answer is far likelier a fault than no torrents)."""
    from app import storage_cleanup
    torrents = storage_cleanup.fetch_torrents()
    if not torrents:
        return None
    out: Set[str] = set()
    count = 0
    for t in torrents:
        if isinstance(t, dict):
            mine = {str(t.get(k) or '').strip().lower() for k in ('hash', 'infohash_v1', 'infohash_v2')} - {''}
            if mine:
                out |= mine
                count += 1
    return (out, count) if out else None


def known_hashes() -> Optional[Set[str]]:
    got = known_torrents()
    return got[0] if got else None


def drop_limit(hash_entries: int) -> int:
    """Most entries one run may drop: max(10, 10% of the hash entries)."""
    return max(MIN_LIMIT, int(hash_entries * MAX_SHARE))


def _retry_log(reason: str, msg: str, *args: Any) -> None:
    """WARNING the first start in a row that hits this reason, INFO on later ones (Tester LOW)."""
    from app import tv_store
    try:
        seen = tv_store.get_meta(RETRY_KEY) == reason
        if not seen:
            tv_store.set_meta(RETRY_KEY, reason)
    except Exception:                                        # noqa: BLE001
        seen = False
    logger.log(logging.INFO if seen else logging.WARNING, msg + ('' if seen else '; trying again next start'), *args)


def _now() -> float:
    return (_cfg.get('now') or time.time)()


def run_once() -> Dict[str, Any]:
    """The migration. Returns what it did (for logs and tests); never raises."""
    try:
        from app import tv_store
        if tv_store.get_meta(META_KEY):
            return {'status': 'done before'}
        got = known_torrents()
        if got is None:
            _retry_log('unreadable', "Download monitor prune: qBittorrent couldn't be read or listed no torrents; "
                       "nothing pruned")
            return {'status': 'unreadable'}
        have, listed = got
        path = monitor_path()
        if not os.path.exists(path):
            tv_store.set_meta(META_KEY, 'no file')
            logger.info('Download monitor prune: dropped 0 entries (no %s)', MONITOR_FILE)
            return {'status': 'no file', 'dropped': []}
        with open(path, 'r') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            _retry_log('unexpected', 'Download monitor prune: %s is not an object; nothing pruned', MONITOR_FILE)
            return {'status': 'unexpected'}
        hash_keys = [k for k in data if _HASH.match(str(k).lower())]
        drop = [k for k in hash_keys if str(k).lower() not in have]
        limit = drop_limit(len(hash_keys))
        if len(drop) > limit or listed < len(drop):
            logger.warning('Download monitor prune: refused, would drop %d of %d entries but qBittorrent listed '
                           '%d torrents (limit %d); looks like a partial list, nothing pruned, trying again next '
                           'start', len(drop), len(hash_keys), listed, limit)
            return {'status': 'refused', 'would_drop': len(drop), 'hash_entries': len(hash_keys),
                    'listed': listed, 'limit': limit}
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(_now()))
        backup = f'{path}.{stamp}.bak'
        shutil.copy2(path, backup)
        if drop:
            kept = {k: v for k, v in data.items() if k not in drop}
            tmp = f'{path}.tmp'
            with open(tmp, 'w') as f:
                json.dump(kept, f, indent=2)
            os.replace(tmp, path)
            _forget_in_memory(drop)
        tv_store.set_meta(META_KEY, stamp)
        logger.info('Download monitor prune: dropped %d entr%s%s (backup %s)', len(drop), 'y' if len(drop) == 1 else 'ies',
                    (': ' + ', '.join('…' + str(h)[-4:].lower() for h in drop)) if drop else '', os.path.basename(backup))
        return {'status': 'pruned', 'dropped': drop, 'backup': backup}
    except Exception as e:                                   # noqa: BLE001
        try:
            _retry_log('error', 'Download monitor prune failed (%s); nothing more done', type(e).__name__)
        except Exception:                                    # noqa: BLE001
            pass
        return {'status': 'error'}


def _forget_in_memory(hashes: List[str]) -> None:
    """If auto_rename is already loaded, drop the same entries from its memory so a later save of its own
    can't write them back. Nothing is written here."""
    mod = sys.modules.get('app.auto_rename')
    renamer = getattr(mod, 'auto_renamer', None) if mod else None
    tracked = getattr(renamer, 'tracked_downloads', None)
    if isinstance(tracked, dict):
        for h in hashes:
            tracked.pop(h, None)
