"""Trackers on the payload Luma hands to qBittorrent.

The bytes of a .torrent are never rewritten, and a magnet is never rebuilt.
Tiers are read from ``announce`` / ``announce-list`` or from magnet ``tr=``.
A details-page comment is not a tracker and is never turned into one.

qBittorrent's saved or exported .torrent can omit ``announce`` even when the
session tracker list was filled from the payload that was added. That export
shape is not treated as proof the payload was stripped. What this module
checks is the payload in hand against the live session list:

* a private payload with no usable http(s)/udp tracker is not a confirmed add
  (and is not uploaded when the client does not already have one);
* after a private add, or when a duplicate is already in the client, supplied
  tiers that are missing are added with ``torrents/addTrackers`` (blank line
  between tiers, the WebAPI tier encoding). Existing tracker URLs are not
  removed or replaced;
* a public magnet or torrent with no trackers is left to DHT, unchanged;
* if the session list cannot be read, the result is pending, not success.

Nothing here logs a tracker URL or a passkey.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlsplit

import requests

logger = logging.getLogger(__name__)

SKIP, CONFIRMED, PENDING, UNUSABLE = 'skip', 'confirmed', 'pending', 'unusable'
NO_USABLE = "This private torrent has no usable tracker, so it wasn't added"
NOT_CONFIRMED = "The download client hasn't confirmed this torrent's trackers"
_SCHEMES = ('http://', 'https://', 'udp://')
_PSEUDO_PREFIXES = ('** [dht]', '** [pex]', '** [lsd]')


class Payload:
    def __init__(self, private: Optional[bool], tiers: List[List[str]]):
        self.private = private
        self.tiers = tiers

    @property
    def has_usable(self) -> bool:
        return any(self.tiers)


def failure(result: str) -> Optional[dict]:
    """Grab-shaped error for a result that is not success, else None."""
    if result in (SKIP, CONFIRMED):
        return None
    if result == PENDING:
        return {'ok': False, 'error': NOT_CONFIRMED, 'pending': True, 'status': 409}
    return {'ok': False, 'error': NO_USABLE, 'status': 409}


def usable_tracker(url: Any) -> bool:
    """An http(s) or udp tracker URL. DHT/PeX/LSD placeholders and comments are not."""
    if not isinstance(url, str):
        return False
    s = url.strip()
    if not s or any(ord(c) < 32 or ord(c) == 127 for c in s):
        return False
    low = s.lower()
    if low.startswith(_PSEUDO_PREFIXES) or not low.startswith(_SCHEMES):
        return False
    try:
        host = urlsplit(s).hostname
    except ValueError:
        return False
    return bool(host)


def prefer_download_target(link: str, magnet: str) -> str:
    """Which target to fetch. A trackerless magnet does not replace one that carries ``tr=``.

    An http(s) download link stays the target: its body is the payload, and a
    companion magnet is not substituted for it.
    """
    link, magnet = link or '', magnet or ''
    if link.lower().startswith('magnet:') and magnet.lower().startswith('magnet:'):
        if magnet_tiers(magnet) and not magnet_tiers(link):
            return magnet
    return link or magnet


def magnet_tiers(uri: Any) -> List[List[str]]:
    """Magnet ``tr=`` values, in order, as one tier. Empty when there are none."""
    s = str(uri or '').strip()
    if not s.lower().startswith('magnet:'):
        return []
    try:
        query = urlsplit(s).query
    except ValueError:
        return []
    urls: List[str] = []
    for key, val in parse_qsl(query, keep_blank_values=True):
        if key == 'tr' and usable_tracker(val) and val not in urls:
            urls.append(val)
    return [urls] if urls else []


def _bdecode(data: bytes, i: int = 0) -> Tuple[Any, int]:
    if i >= len(data):
        raise ValueError('bencode')
    c = data[i:i + 1]
    if c == b'i':
        end = data.index(b'e', i)
        return int(data[i + 1:end]), end + 1
    if c == b'l':
        i += 1
        out: List[Any] = []
        while data[i:i + 1] != b'e':
            val, i = _bdecode(data, i)
            out.append(val)
        return out, i + 1
    if c == b'd':
        i += 1
        out: dict = {}
        while data[i:i + 1] != b'e':
            key, i = _bdecode(data, i)
            val, i = _bdecode(data, i)
            if isinstance(key, bytes) and key not in out:
                out[key] = val
        return out, i + 1
    colon = data.index(b':', i)
    n = int(data[i:colon])
    if n < 0:
        raise ValueError('bencode')
    start = colon + 1
    return data[start:start + n], start + n


def _text(raw: Any) -> Optional[str]:
    if not isinstance(raw, bytes):
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _add_url(tiers: List[List[str]], tier: int, url: Optional[str]) -> None:
    if not url or not usable_tracker(url):
        return
    while len(tiers) <= tier:
        tiers.append([])
    if any(url in group for group in tiers):
        return
    tiers[tier].append(url)


def torrent_payload(data: bytes) -> Payload:
    """Private flag and announce tiers. Unreadable bytes are not called private."""
    try:
        root, end = _bdecode(data, 0)
    except (ValueError, IndexError, OverflowError):
        return Payload(None, [])
    if end != len(data) or not isinstance(root, dict):
        return Payload(None, [])
    info = root.get(b'info')
    if not isinstance(info, dict):
        return Payload(None, [])
    private = info.get(b'private') == 1
    tiers: List[List[str]] = []
    listed = root.get(b'announce-list')
    if isinstance(listed, list):
        for n, group in enumerate(listed):
            if not isinstance(group, list):
                continue
            for raw in group:
                _add_url(tiers, n, _text(raw))
    _add_url(tiers, 0, _text(root.get(b'announce')))
    while tiers and not tiers[-1]:
        tiers.pop()
    return Payload(private, tiers)


def inspect(kind: str, payload: Any) -> Payload:
    if kind == 'torrent' and isinstance(payload, (bytes, bytearray)):
        return torrent_payload(bytes(payload))
    if kind == 'magnet':
        return Payload(None, magnet_tiers(payload))
    return Payload(None, [])


def encode_tiers(tiers: Sequence[Sequence[str]]) -> str:
    """qBittorrent ``addTrackers`` body: one URL per line, a blank line starts the next tier."""
    lines: List[str] = []
    for i, tier in enumerate(tiers):
        if i:
            lines.append('')
        for url in tier:
            if usable_tracker(url):
                lines.append(url)
    return '\n'.join(lines)


def _present(entries: Sequence[dict]) -> set:
    found = set()
    for item in entries:
        url = item.get('url') if isinstance(item, dict) else None
        if isinstance(url, str) and url.strip():
            found.add(url.strip())
    return found


def _client_usable(entries: Sequence[dict]) -> bool:
    return any(usable_tracker(url) for url in _present(entries))


def missing_tiers(tiers: Sequence[Sequence[str]], present: set) -> List[List[str]]:
    out: List[List[str]] = []
    any_missing = False
    for group in tiers:
        keep = [url for url in group if url not in present]
        if keep:
            any_missing = True
        out.append(keep)
    if not any_missing:
        return []
    while out and not out[-1]:
        out.pop()
    return out


def _parse_tracker_list(data: Any) -> Optional[List[dict]]:
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        if not isinstance(item, dict) or 'url' not in item:
            return None
        out.append(item)
    return out


def read_trackers(sess, base: str, info_hash: str) -> Tuple[str, List[dict]]:
    """('ok', entries), ('absent', []) when the hash is not in the client, or ('unknown', [])."""
    try:
        resp = sess.get(f'{base}/api/v2/torrents/trackers', params={'hash': info_hash},
                        timeout=10, allow_redirects=False)
    except requests.RequestException as exc:
        logger.warning('qBittorrent tracker list failed (%s)', type(exc).__name__)
        return 'unknown', []
    code = getattr(resp, 'status_code', None)
    if code == 404:
        return 'absent', []
    if code != 200:
        logger.info('qBittorrent tracker list HTTP %s', code)
        return 'unknown', []
    try:
        parsed = _parse_tracker_list(resp.json())
    except (ValueError, TypeError, AttributeError):
        return 'unknown', []
    if parsed is None:
        return 'unknown', []
    return 'ok', parsed


def _restore(sess, base: str, info_hash: str, tiers: Sequence[Sequence[str]],
             permit: Optional[Callable[[Sequence[str]], None]]) -> bool:
    body = encode_tiers(tiers)
    if not body.strip():
        return False
    try:
        if permit is not None:
            permit([info_hash])
        resp = sess.post(f'{base}/api/v2/torrents/addTrackers',
                         data={'hash': info_hash, 'urls': body},
                         timeout=20, allow_redirects=False)
    except requests.RequestException as exc:
        logger.warning('qBittorrent tracker restore failed (%s)', type(exc).__name__)
        return False
    except Exception as exc:  # noqa: BLE001 - the test-instance write guard
        if type(exc).__name__ != 'WritesBlocked':
            raise
        logger.warning('qBittorrent tracker restore blocked')
        return False
    code = getattr(resp, 'status_code', None)
    if not isinstance(code, int) or not (200 <= code < 300):
        logger.info('qBittorrent tracker restore HTTP %s', code)
        return False
    return True


def precheck(sess, base: str, info_hash: str, kind: str, payload: Any) -> str:
    """Before upload: private payloads with no usable tracker.

    ``confirmed`` means the client already has a usable tracker for this hash,
    so the trackerless private bytes must not be uploaded over it. ``skip``
    means upload as usual (public, a magnet, or a private payload that itself
    carries trackers).
    """
    info = inspect(kind, payload)
    if info.private is not True or info.has_usable:
        return SKIP
    if sess is None or not base or not info_hash:
        return PENDING
    state, entries = read_trackers(sess, base, info_hash)
    if state == 'unknown':
        return PENDING
    if state == 'ok' and _client_usable(entries):
        return CONFIRMED
    return UNUSABLE


def settle(sess, base: str, info_hash: str, kind: str, payload: Any, *,
           duplicate: bool, sent: bool,
           permit: Optional[Callable[[Sequence[str]], None]] = None) -> str:
    """After an add, or for a hash already in the client.

    Restores supplied tiers that the session is missing. Does not remove or
    replace a tracker the client already has. ``skip`` when this payload does
    not need a session read (so public DHT adds do not call the tracker API).
    """
    info = inspect(kind, payload)
    if info.private is not True and not (duplicate and info.has_usable):
        return SKIP
    if sess is None or not base or not info_hash:
        return PENDING
    state, entries = read_trackers(sess, base, info_hash)
    if state == 'unknown':
        return PENDING
    if state == 'absent':
        return PENDING if sent else SKIP
    missing = missing_tiers(info.tiers, _present(entries))
    restored = False
    if missing and (sent or duplicate):
        if not _restore(sess, base, info_hash, missing, permit):
            return PENDING
        restored = True
        state, entries = read_trackers(sess, base, info_hash)
        if state != 'ok':
            return PENDING
        if missing_tiers(info.tiers, _present(entries)):
            return PENDING
    if info.private is True and not _client_usable(entries):
        return UNUSABLE
    if info.has_usable and missing_tiers(info.tiers, _present(entries)):
        return PENDING
    logger.info('qBittorrent trackers %s', 'restored' if restored else 'confirmed')
    return CONFIRMED
