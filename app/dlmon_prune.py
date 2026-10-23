"""v260 (PM / Ops): one-time prune of download_monitor.json (meta key dlmon_prune_v260 in tv.db).

auto_rename's download_monitor.json can hold entries for torrents qBittorrent no longer has (today: The
Manchurian Candidate, …201e). Once, at boot on minimal (test) builds:

1. Read qBittorrent's BT_backup folder, read-only: the hashes that still have a <hash>.fastresume file. The
   folder comes from settings.json `qbittorrent.bt_backup_dir` (BT_BACKUP_SETTING). If it isn't set, can't be
   read, or holds no .fastresume file at all (most likely the wrong folder), nothing is pruned: one WARNING,
   and the migration is tried again on the next boot. Missing data never prunes.
2. Back up download_monitor.json (in the data dir, paths.data_path; LUMA_DATA_DIR respected) to a
   timestamped .bak next to it.
3. Drop the entries whose torrent hash has no .fastresume file. Entries whose key isn't a torrent hash stay.
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
BT_BACKUP_SETTING = 'qbittorrent.bt_backup_dir'
_HASH = re.compile(r'^[0-9a-f]{40}$|^[0-9a-f]{64}$')
_cfg: Dict[str, Any] = {}          # tests: {'now': callable}


def monitor_path() -> str:
    from app import paths
    return str(paths.data_path(MONITOR_FILE))


def bt_backup_dir() -> str:
    from app.settings_manager import SettingsFile
    return str(SettingsFile().get(BT_BACKUP_SETTING) or '').strip()


def resume_hashes(folder: str) -> Optional[Set[str]]:
    """Hashes with a .fastresume file in *folder* (read-only listing), or None if it can't be used."""
    if not folder or not os.path.isabs(folder):
        return None
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    out = {n[:-len('.fastresume')].lower() for n in names if n.lower().endswith('.fastresume')}
    return out or None                # an empty BT_backup is far likelier the wrong folder than no torrents


def _now() -> float:
    return (_cfg.get('now') or time.time)()


def run_once() -> Dict[str, Any]:
    """The migration. Returns what it did (for logs and tests); never raises."""
    try:
        from app import tv_store
        if tv_store.get_meta(META_KEY):
            return {'status': 'done before'}
        folder = bt_backup_dir()
        have = resume_hashes(folder)
        if have is None:
            logger.warning("Download monitor prune: qBittorrent's BT_backup folder %s can't be read (%s); "
                           "nothing pruned, trying again next start", folder or '(not set)', BT_BACKUP_SETTING)
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


def _forget_in_memory(hashes: List[str]) -> None:
    """If auto_rename is already loaded, drop the same entries from its memory so a later save of its own
    can't write them back. Nothing is written here."""
    mod = sys.modules.get('app.auto_rename')
    renamer = getattr(mod, 'auto_renamer', None) if mod else None
    tracked = getattr(renamer, 'tracked_downloads', None)
    if isinstance(tracked, dict):
        for h in hashes:
            tracked.pop(h, None)
