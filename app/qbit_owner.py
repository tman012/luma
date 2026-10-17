"""Which Luma install a torrent in a shared qBittorrent belongs to.

Production (:5001) and the test install (LUMA_MINIMAL, :5002) can share one
qBittorrent. The test install tags every torrent it adds with its test tag
(LUMA_TEST_QBIT_TAG, default 'luma-test'), and only ever moves torrents it
tracked by hash *and* that carry the tag. Only the tag counts: a category of
the same name does not, and a tag equal to an app's category ('luma',
'radarr', 'sonarr', ..., or the configured qbittorrent.app_category) is
refused and the default used instead, so the test install can never claim
production's downloads.
Production's auto-rename monitor skips torrents that carry it. The tag lives in
the environment, not in settings.json, so production's config is never seeded
with it.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping, Optional, Set

TAG_ENV = 'LUMA_TEST_QBIT_TAG'
DEFAULT_TAG = 'luma-test'
# qBittorrent refuses commas in tag names; keep it to a plain label.
TAG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$')
# Categories production and the *arr apps file their grabs under. A test tag
# equal to one of these would match production's own torrents.
RESERVED = frozenset({'luma', 'radarr', 'sonarr', 'lidarr', 'readarr', 'whisparr',
                      'prowlarr', 'tv', 'movies', 'tv-sonarr', 'radarr4k', 'sonarr4k'})

logger = logging.getLogger(__name__)
_warned: Set[str] = set()


_cfg: dict = {}


def init(settings=None) -> None:
    """Give tag_name() the settings, so the configured app category
    (Settings > qBittorrent, qbittorrent.app_category) is reserved too."""
    _cfg['settings'] = settings


def _app_category() -> str:
    s = _cfg.get('settings')
    if s is None:
        return ''
    try:
        v = s.get('qbittorrent.app_category')
    except Exception:
        return ''
    return v.strip().casefold() if isinstance(v, str) else ''


def _reserved() -> Set[str]:
    extra = {(os.environ.get('LUMA_QBIT_APP_CATEGORY') or '').strip().casefold(), _app_category()}
    return set(RESERVED) | {x for x in extra if x}


def tag_name() -> str:
    """The test install's tag: LUMA_TEST_QBIT_TAG when valid and not an app's
    category, else 'luma-test'."""
    raw = (os.environ.get(TAG_ENV) or '').strip()
    if not TAG_RE.match(raw):
        return DEFAULT_TAG
    if raw.casefold() in _reserved():
        if raw not in _warned:
            _warned.add(raw)
            logger.warning('%s=%r is an app category; using %r', TAG_ENV, raw, DEFAULT_TAG)
        return DEFAULT_TAG
    return raw


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
    """True when the torrent carries the test tag (compared casefolded). The
    category never counts."""
    if not isinstance(torrent, Mapping):
        return False
    t = (tag or tag_name()).casefold()
    return t in torrent_tags(torrent)
