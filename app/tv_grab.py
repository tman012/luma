"""TV grabs (v158b): send one chosen release to qBittorrent for a show's episodes.

Every grab carries this instance's TV name (tv_downloads.category(): luma-test-tv on test,
luma-tv on prod) as BOTH its qBittorrent category and its only tag, so the TV download watch
imports it and nothing else in a shared qBittorrent is touched. No save path is ever sent.

On LUMA_INSTANCE=test a grab is a gated job ('tv_grab', OFF unless LUMA_TEST_ALLOW names it),
checked here and again at the write call: the instance write guard lets exactly one kind of
qBittorrent write through for it (instance.scoped_add: torrents/add with that category and tag),
while every other qBittorrent write stays blocked. Prod: always allowed here; the automatic
searches that lead to grabs have their own switches (tv_settings automation, off by default).

The release comes from one of the owner's own trackers, through the release token the search
issued (tracker_sources.fetch_release: guarded, never follows a link to another site with a
saved credential). Recorded in tv.db grabs (state 'downloading'); the watch takes it from there.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from app import instance, tv_downloads, tv_store

logger = logging.getLogger(__name__)

JOB = 'tv_grab'
OFF = 'Grabs are off on the test instance'
NO_CLIENT = 'Download client unavailable'
ORIGINS = ('auto', 'manual', 'upgrade')
_BTIH_RE = re.compile(r'xt=urn:btih:([0-9a-fA-F]{40}|[A-Z2-7]{32})', re.I)


class GrabFailed(Exception):
    pass


def allowed() -> bool:
    return instance.writes_allowed(JOB)


def _bdecode_info_span(data: bytes) -> Optional[bytes]:
    """The raw bytes of a .torrent's 'info' dict (its v1 info hash is their sha1)."""
    def skip(i: int) -> int:
        c = data[i:i + 1]
        if c == b'i':
            return data.index(b'e', i) + 1
        if c in (b'l', b'd'):
            i += 1
            while data[i:i + 1] != b'e':
                i = skip(i)
            return i + 1
        if c.isdigit():
            colon = data.index(b':', i)
            return colon + 1 + int(data[i:colon])
        raise ValueError('bencode')
    try:
        if data[:1] != b'd':
            return None
        i = 1
        while data[i:i + 1] != b'e':
            colon = data.index(b':', i)
            key = data[colon + 1:colon + 1 + int(data[i:colon])]
            i = colon + 1 + int(data[i:colon])
            end = skip(i)
            if key == b'info':
                return data[i:end]
            i = end
    except (ValueError, IndexError):
        return None
    return None


def info_hash(kind: str, payload: Any) -> Optional[str]:
    if kind == 'torrent':
        span = _bdecode_info_span(payload)
        return hashlib.sha1(span).hexdigest() if span else None
    m = _BTIH_RE.search(str(payload or ''))
    if not m:
        return None
    h = m.group(1)
    if len(h) == 32:
        import base64
        try:
            h = base64.b32decode(h.upper()).hex()
        except ValueError:
            return None
    return h.lower()


def _fields(name: str) -> Dict[str, str]:
    from app import library_mover as mover
    s = mover._cfg.get('settings')
    f = {'category': name, 'tags': name, 'autoTMM': 'false'}
    try:
        if s is not None and s.get('qbittorrent.add_paused'):
            f.update(paused='true', stopped='true')
    except Exception:                                        # noqa: BLE001
        pass
    return f


def grab(item: Dict[str, Any], series_id: int, season: Optional[int], episode_ids: List[int], origin: str,
         now: Optional[float] = None, session_factory: Callable = requests.Session,
         fetch: Optional[Callable] = None) -> Dict[str, Any]:
    """Send *item* (a search result: title, downloadUrl, indexerId, size, quality) to qBittorrent.
    {'ok': True, 'hash'} or {'ok': False, 'error'}. Never raises for an expected failure."""
    from app import library_mover as mover, tracker_sources
    now = time.time() if now is None else now
    if origin not in ORIGINS:
        raise ValueError('origin')
    if not allowed():
        logger.info('TV grab: %s not sent (%s)', item.get('title'), OFF)
        return {'ok': False, 'error': OFF, 'blocked': True}
    token = tracker_sources.release_token(item.get('downloadUrl'))
    try:
        kind, payload = (fetch or tracker_sources.fetch_release)(token)
    except tracker_sources.GrabError as e:
        return {'ok': False, 'error': str(e)}
    h = info_hash(kind, payload)
    if not h:
        return {'ok': False, 'error': 'This release has no info hash'}
    with tv_store.connect() as conn:
        if conn.execute('SELECT 1 FROM grabs WHERE hash = ?', (h,)).fetchone():
            return {'ok': True, 'hash': h, 'existing': True}
        if conn.execute('SELECT 1 FROM blocklist WHERE info_hash = ? OR (release_title = ? AND series_id = ?)',
                        (h, item.get('title'), series_id)).fetchone():
            return {'ok': False, 'error': 'Blocklisted'}
    name = tv_downloads.category()
    sess, url = mover._qbit_session(session_factory, now)
    if not sess:
        return {'ok': False, 'error': NO_CLIENT}
    try:
        with instance.scoped_add(JOB, name):
            if kind == 'torrent':
                r = sess.post(f'{url}/api/v2/torrents/add', data=_fields(name), timeout=30, allow_redirects=False,
                              files={'torrents': ('release.torrent', payload, 'application/x-bittorrent')})
            else:
                r = sess.post(f'{url}/api/v2/torrents/add', data={'urls': payload, **_fields(name)}, timeout=30,
                              allow_redirects=False)
    except instance.WritesBlocked:
        return {'ok': False, 'error': OFF, 'blocked': True}
    except requests.RequestException as e:
        logger.warning('TV grab: qBittorrent add failed (%s)', type(e).__name__)
        return {'ok': False, 'error': NO_CLIENT}
    finally:
        mover._close(sess)
    from app.qbit_connector import text_of
    if r.status_code != 200 or text_of(r).strip() not in ('Ok.', ''):
        return {'ok': False, 'error': f'qBittorrent returned HTTP {r.status_code}'}
    with tv_store.write() as conn:
        conn.execute('INSERT OR IGNORE INTO grabs (hash, series_id, season_number, episode_ids, release_title, tracker_id,'
                     ' quality, size, origin, grabbed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     (h, series_id, season, json.dumps(sorted(episode_ids)), item.get('title'), item.get('indexerId'),
                      item.get('quality'), int(item.get('size') or 0), origin, now))
        conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                     (now, series_id, json.dumps(sorted(episode_ids)), 'grabbed',
                      json.dumps({'title': item.get('title'), 'origin': origin, 'hash': h, 'tracker': item.get('indexer')})))
    logger.info('TV grab: %s (%s, %d episode(s), category %s)', item.get('title'), origin, len(episode_ids), name)
    return {'ok': True, 'hash': h}
