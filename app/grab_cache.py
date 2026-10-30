"""The .torrent / magnet Luma sent to qBittorrent, kept by info hash at grab time, so a grab that qBittorrent
lost (it took the add, then restarted before saving its resume data) can be added again by import_reconciler
without searching. Files: <data>/grab_cache/<hash>.torrent | <hash>.magnet (0600); older than KEEP_DAYS go."""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app import paths as _paths

logger = logging.getLogger(__name__)
KEEP_DAYS = 30
MAX_BYTES = 10 * 1024 * 1024
_HASH_RE = re.compile(r'^([0-9a-f]{40}|[0-9a-f]{64})$')
_cfg: Dict[str, Any] = {}


def cache_dir() -> Path:
    return Path(_cfg.get('dir') or _paths.data_path('grab_cache'))


def save(h: Any, kind: str, payload: Any, now: Optional[float] = None) -> bool:
    """Never raises (caching must not break a grab)."""
    try:
        h = str(h or '').lower()
        if not _HASH_RE.match(h) or kind not in ('torrent', 'magnet'):
            return False
        data = payload if isinstance(payload, (bytes, bytearray)) else str(payload or '').encode()
        if not data or len(data) > MAX_BYTES:
            return False
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f'.{h}.{kind}.tmp'
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(bytes(data))
        os.replace(tmp, d / f'{h}.{kind}')
        _prune(now)
        return True
    except Exception as e:                              # noqa: BLE001
        logger.debug('Grab cache: could not save %s (%s)', str(h)[:8], type(e).__name__)
        return False


def load(h: Any) -> Optional[Tuple[str, Any]]:
    h = str(h or '').lower()
    if not _HASH_RE.match(h):
        return None
    for kind in ('torrent', 'magnet'):
        p = cache_dir() / f'{h}.{kind}'
        try:
            b = p.read_bytes()
        except OSError:
            continue
        return (kind, b) if kind == 'torrent' else (kind, b.decode('utf-8', 'replace').strip())
    return None


def _prune(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    try:
        for p in cache_dir().iterdir():
            if p.is_file() and now - p.stat().st_mtime > KEEP_DAYS * 86400:
                p.unlink()
    except OSError:
        pass
