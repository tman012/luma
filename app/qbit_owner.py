"""Which Luma install a torrent in a shared qBittorrent belongs to.

Production (:5001) and the test install (LUMA_MINIMAL, :5002) can share one
qBittorrent. The test install tags every torrent it adds with its test tag
(LUMA_TEST_QBIT_TAG, default 'luma-test'), and only ever moves torrents it
tracked by hash *and* that carry the tag (or sit in a category of that name).
Production's auto-rename monitor skips torrents that carry it. The tag lives in
the environment, not in settings.json, so production's config is never seeded
with it.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping, Optional, Set

TAG_ENV = 'LUMA_TEST_QBIT_TAG'
DEFAULT_TAG = 'luma-test'
# qBittorrent refuses commas in tag names; keep it to a plain label.
TAG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$')


def tag_name() -> str:
    """The test install's tag: LUMA_TEST_QBIT_TAG when valid, else 'luma-test'."""
    raw = (os.environ.get(TAG_ENV) or '').strip()
    return raw if TAG_RE.match(raw) else DEFAULT_TAG


def torrent_tags(torrent: Mapping[str, Any]) -> Set[str]:
    """qBittorrent's comma-separated `tags` as a casefolded set."""
    raw = torrent.get('tags') if isinstance(torrent, Mapping) else None
    if isinstance(raw, (list, tuple)):
        parts = raw
    elif isinstance(raw, str):
        parts = raw.split(',')
    else:
        return set()
    return {str(p).strip().casefold() for p in parts if str(p).strip()}


def is_test_torrent(torrent: Mapping[str, Any], tag: Optional[str] = None) -> bool:
    """True when the torrent carries the test tag or sits in a category of
    that name (compared casefolded)."""
    if not isinstance(torrent, Mapping):
        return False
    t = (tag or tag_name()).casefold()
    if t in torrent_tags(torrent):
        return True
    cat = torrent.get('category')
    return isinstance(cat, str) and cat.strip().casefold() == t
