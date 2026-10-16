"""
plex_file_info — "File Info" sheet data for the movie detail modal.

Mirrors Plex's own File Info screen (Media → Part → Stream) and rewrites the
file path from the Plex *container's* mount (e.g. ``/movies/Movies/...``) to
the real location on the NAS share (e.g. ``/Media/Video/Movies/...``).

Path mapping
------------
A prefix map, longest prefix wins, matched on whole path segments:

    PLEX_PATH_MAP="/movies=/Media/Video,/tv=/Media/Video/TV,/media=/Media"

Resolution order: env ``PLEX_PATH_MAP`` → settings.json ``plex.path_map``
(string in the same format, or a {from: to} object) → DEFAULT_PATH_MAP below.

Defaults were derived (Oct 2026) from every Plex library location reported by
/library/sections and checked against the //192.168.1.25/Media SMB share
(mounted on docker-server at /mnt/media):

    Plex container path        NAS share path
    /movies/<lib folder>   →   /Media/Video/<lib folder>   (Movies, 4K, Full Rips,
                                                             Recently Released,
                                                             Remux TV, Home Videos,
                                                             3D Movies*)
    /tv                    →   /Media/Video/TV
    /media                 →   /Media                       (Video/4K TV Shows,
                                                             Video/Temp Home Videos,
                                                             Music/Christmas Music)

    * "3D Movies" no longer exists on the share (stale Plex library, 2 items).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PATH_MAP = '/movies=/Media/Video,/tv=/Media/Video/TV,/media=/Media'

_server_name_cache: Dict[str, Tuple[str, float]] = {}  # plex_url -> (name, ts)
_server_name_lock = threading.Lock()
_SERVER_NAME_TTL = 3600


# ---------------------------------------------------------------------------
# Path mapping
# ---------------------------------------------------------------------------

def _norm_prefix(p: str) -> str:
    p = (p or '').strip()
    if not p:
        return ''
    if not p.startswith('/'):
        p = '/' + p
    return p.rstrip('/') or '/'


def parse_path_map(raw: Any) -> List[Tuple[str, str]]:
    """Parse ``"/a=/x,/b=/y"`` (or a dict) into [(from, to)], longest first."""
    pairs: List[Tuple[str, str]] = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, str):
        items = []
        for chunk in raw.replace(';', ',').split(','):
            if '=' not in chunk:
                continue
            src, dst = chunk.split('=', 1)
            items.append((src, dst))
    else:
        items = []
    for src, dst in items:
        s, d = _norm_prefix(str(src)), _norm_prefix(str(dst))
        if s:
            pairs.append((s, d if d != '/' else ''))
    pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
    return pairs


def get_path_map(settings: Any = None) -> List[Tuple[str, str]]:
    raw: Any = os.environ.get('PLEX_PATH_MAP')
    if not raw and settings is not None:
        try:
            raw = settings.get('plex.path_map')
        except Exception:  # noqa: BLE001 — settings is optional
            raw = None
    pairs = parse_path_map(raw) if raw else []
    return pairs or parse_path_map(DEFAULT_PATH_MAP)


def map_path(path: str, pairs: List[Tuple[str, str]]) -> str:
    """Rewrite *path* using the first (longest) matching prefix."""
    if not path:
        return path
    for src, dst in pairs:
        if src == '/':
            return (dst + path) if dst else path
        if path == src or path.startswith(src + '/'):
            return (dst or '') + path[len(src):] or '/'
    return path


# ---------------------------------------------------------------------------
# Plex helpers
# ---------------------------------------------------------------------------

def _int(v: Any) -> Optional[int]:
    try:
        return int(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def get_server_name(plex_service: Any) -> Optional[str]:
    now = time.time()
    url = getattr(plex_service, 'plex_url', '') or ''
    with _server_name_lock:
        hit = _server_name_cache.get(url)
        if hit and now - hit[1] < _SERVER_NAME_TTL:
            return hit[0]
    root = plex_service._get('/')
    name = root.get('friendlyName') if root is not None else None
    if name:
        with _server_name_lock:
            _server_name_cache[url] = (name, now)
    return name


_STREAM_KINDS = {'1': 'video', '2': 'audio', '3': 'subtitle'}


def _stream(st: Any) -> Dict[str, Any]:
    title = st.get('extendedDisplayTitle') or st.get('displayTitle') or st.get('codec', '').upper()
    return {
        'id': _int(st.get('id')),
        'display': title,
        'codec': st.get('codec'),
        'language': st.get('language'),
        'default': st.get('default') == '1',
        'selected': st.get('selected') == '1',
        'forced': st.get('forced') == '1',
        'external': bool(st.get('key')),  # sidecar subtitle files have a key
    }


def build_file_info(plex_service: Any, rating_key: int, settings: Any = None,
                    apply_path_map: bool = True) -> Optional[Dict[str, Any]]:
    """Return File Info for *rating_key*, or None if Plex doesn't know it.

    *apply_path_map* is False for a user's own Plex server — the map describes
    the shared TFlix-NAS container mounts only.
    """
    root = plex_service._get(f'/library/metadata/{rating_key}')
    if root is None:
        return None
    video = root.find('.//Video')
    if video is None:
        return None

    pairs = get_path_map(settings) if apply_path_map else []
    versions: List[Dict[str, Any]] = []
    for media in video.findall('Media'):
        parts: List[Dict[str, Any]] = []
        for part in media.findall('Part'):
            streams = {'video': [], 'audio': [], 'subtitle': []}
            for st in part.findall('Stream'):
                kind = _STREAM_KINDS.get(st.get('streamType', ''))
                if kind:
                    streams[kind].append(_stream(st))
            raw_file = part.get('file') or ''
            parts.append({
                'id': _int(part.get('id')),
                'plex_file': raw_file,
                'path': map_path(raw_file, pairs),
                'size_bytes': _int(part.get('size')),
                'duration_ms': _int(part.get('duration')),
                'container': part.get('container'),
                'video_streams': streams['video'],
                'audio_streams': streams['audio'],
                'subtitle_streams': streams['subtitle'],
            })
        sizes = [p['size_bytes'] for p in parts if p['size_bytes']]
        versions.append({
            'id': _int(media.get('id')),
            'duration_ms': _int(media.get('duration')),
            'bitrate_kbps': _int(media.get('bitrate')),
            'resolution': media.get('videoResolution'),
            'width': _int(media.get('width')),
            'height': _int(media.get('height')),
            'container': media.get('container'),
            'video_codec': media.get('videoCodec'),
            'audio_codec': media.get('audioCodec'),
            'audio_channels': _int(media.get('audioChannels')),
            'size_bytes': sum(sizes) if sizes else None,
            'parts': parts,
        })

    return {
        'rating_key': rating_key,
        'title': video.get('title', ''),
        'year': _int(video.get('year')),
        'server': get_server_name(plex_service),
        'library': video.get('librarySectionTitle') or root.get('librarySectionTitle'),
        'library_id': _int(video.get('librarySectionID') or root.get('librarySectionID')),
        'duration_ms': _int(video.get('duration')),
        'versions': versions,
    }
