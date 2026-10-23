"""v260 (PM / Ops): one-time prune of download_monitor.json (meta key dlmon_prune_v260 in tv.db).

auto_rename's download_monitor.json can hold entries for torrents qBittorrent no longer has (today: The
Manchurian Candidate, …201e). Once, at boot on minimal (test) builds, off the boot thread:

1. Ask qBittorrent which torrents it has, through the guard's own read-only torrents/info call
   (storage_cleanup.fetch_torrents). If qBittorrent can't be reached, errors, or lists no torrent at all,
   nothing is pruned: one WARNING, and the migration is tried again on the next start. Nothing is written
   to qBittorrent.
2. Back up download_monitor.json (in the data dir, paths.data_path; LUMA_DATA_DIR respected) to a
   timestamped .bak next to it.
3. Drop the entries whose torrent hash qBittorrent doesn't list (case-insensitive). Keys that aren't a
   torrent hash stay.
4. One INFO line with the count and the last 4 of each dropped hash; then the meta key is set.

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
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

META_KEY = 'dlmon_prune_v260'
MONITOR_FILE = 'download_monitor.json'
_HASH = re.compile(r'^[0-9a-f]{40}$|^[0-9a-f]{64}$')
_cfg: Dict[str, Any] = {}          # tests: {'now': callable}


def monitor_path() -> str:
    from app import paths
    return str(paths.data_path(MONITOR_FILE))


def known_hashes() -> Optional[Set[str]]:
    """Lower-case hashes of every torrent qBittorrent lists (read-only torrents/info), or None when it can't
    be read or lists none (an empty answer is far likelier a fault than no torrents)."""
    from app import storage_cleanup
    torrents = storage_cleanup.fetch_torrents()
    if not torrents:
        return None
    out = set()
    for t in torrents:
        if isinstance(t, dict):
            for k in ('hash', 'infohash_v1', 'infohash_v2'):
                v = str(t.get(k) or '').strip().lower()
                if v:
                    out.add(v)
    return out or None


def _now() -> float:
    return (_cfg.get('now') or time.time)()


def run_once() -> Dict[str, Any]:
    """The migration. Returns what it did (for logs and tests); never raises."""
    try:
        from app import tv_store
        if tv_store.get_meta(META_KEY):
            return {'status': 'done before'}
        have = known_hashes()
        if have is None:
            logger.warning("Download monitor prune: qBittorrent couldn't be read or listed no torrents; "
                           "nothing pruned, trying again next start")
            return {'status': 'unreadable'}
        path = monitor_path()
        if not os.path.exists(path):
            tv_store.set_meta(META_KEY, 'no file')
            logger.info('Download monitor prune: dropped 0 entries (no %s)', MONITOR_FILE)
            return {'status': 'no file', 'dropped': []}
        with open(path, 'r') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning('Download monitor prune: %s is not an object; nothing pruned', MONITOR_FILE)
            return {'status': 'unexpected'}
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(_now()))
        backup = f'{path}.{stamp}.bak'
        shutil.copy2(path, backup)
        drop = [k for k in data if _HASH.match(str(k).lower()) and str(k).lower() not in have]
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
        logger.error('Download monitor prune failed (%s); nothing more done', type(e).__name__)
        return {'status': 'error'}


def start() -> None:
    """run_once off the boot thread (torrents/info can take a while)."""
    import threading
    threading.Thread(target=run_once, name='dlmon-prune', daemon=True).start()


def _forget_in_memory(hashes: List[str]) -> None:
    """If auto_rename is already loaded, drop the same entries from its memory so a later save of its own
    can't write them back. Nothing is written here."""
    mod = sys.modules.get('app.auto_rename')
    renamer = getattr(mod, 'auto_renamer', None) if mod else None
    tracked = getattr(renamer, 'tracked_downloads', None)
    if isinstance(tracked, dict):
        for h in hashes:
            tracked.pop(h, None)
