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

v158c: the TV Sources popup grabs here too (fetch_link: a release token, a Sources link, a
magnet, or a .torrent link fetched through the outbound guard; v172: no Prowlarr bridge). Sonarr is only the popup's fallback when it is configured.

The category (v158c, PM): before the first add, qBittorrent's categories are read and this
instance's TV category is created when it's missing (torrents/createCategory, no save path; on
test the write guard lets exactly that call through inside the same scope). When the category
can't be read or created, nothing is added: the grab fails with CATEGORY_FAILED, recorded as a
failed job (meta 'tvgrab:<hash>', listed by tv_downloads.failures()) so the TV downloads list
shows it. A grab that succeeds later clears that job.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from app import instance, tv_downloads, tv_store

logger = logging.getLogger(__name__)

JOB = 'tv_grab'
OFF = 'Grabs are off on the test instance'
NO_CLIENT = 'Download client unavailable'
CATEGORY_FAILED = "Couldn't create the qBittorrent category {}"
CATEGORY_UNREAD = "Couldn't read the qBittorrent categories"
LINK_EXPIRED = 'This download link has expired. Search again.'
NOT_A_TORRENT = 'The indexer returned an invalid or expired download link'
JOB_KEY = 'tvgrab:'
_have_category: set = set()                # (qBittorrent URL, name) already checked this run
_link_resolvers: List[Callable[[str], Any]] = []   # main's Sources links: url -> (is_link, real_url)
ORIGINS = ('auto', 'manual', 'upgrade')


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


NO_HASH = 'This release has no info hash'


def info_hash(kind: str, payload: Any) -> Optional[str]:
    if kind == 'torrent':
        span = _bdecode_info_span(payload)
        return hashlib.sha1(span).hexdigest() if span else None
    from app.magnet import btih            # v171+ (Reviewer): one parser for TV and movie
    return btih(payload)


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


def register_link_resolver(fn: Callable[[str], Any]) -> None:
    if fn not in _link_resolvers:
        _link_resolvers.append(fn)


RAW_LINK = 'Grab from a search result (a pasted link or magnet is not accepted here)'


def fetch_link(url: Any, automatic: bool = False, tokens_only: bool = False) -> Any:
    """('torrent', bytes) or ('magnet', uri) for a Sources download link. Raises GrabError.
    *automatic*: a grab Luma made on its own (tracker_sources.fetch_release charges it as one).
    *tokens_only* (v165, Reviewer; the Sources popup): only a release token or one of main's Sources
    links; a raw URL or magnet is refused (400) and never reaches safe_magnet or outbound_guard, so a
    pasted tracker link with a passkey can't get around the tracker budget. v172 (tracker port step 4):
    the LAN Prowlarr bridge is gone; any other link goes through outbound_guard (public addresses only,
    every hop checked and pinned), and in tokens_only mode nothing but a token or a Sources magnet does."""
    from app import tracker_sources as ts
    u = str(url or '').strip()
    if not u:
        raise ts.GrabError('This release has no download link', 400)
    tok = ts.release_token(u)
    if tok is not None:
        return ts.fetch_release(tok, automatic=automatic)
    source = False
    for fn in _link_resolvers:
        is_link, real = fn(u)
        if is_link:
            if not real:
                raise ts.GrabError(LINK_EXPIRED, 410)
            u, source = real, True
            break
    if tokens_only and not source:
        raise ts.GrabError(RAW_LINK, 400)
    if u.lower().startswith('magnet:'):
        m = ts.safe_magnet(u)
        if not m:
            raise ts.GrabError('Link not allowed', 400)
        return 'magnet', m
    if tokens_only:
        raise ts.GrabError('Link not allowed', 400)
    from app import outbound_guard
    try:
        dl = outbound_guard.fetch(u)
    except outbound_guard.BlockedURL:
        raise ts.GrabError('Link not allowed', 400)
    except Exception as e:                                    # noqa: BLE001
        logger.warning('TV grab: link fetch failed (%s)', type(e).__name__)
        raise ts.GrabError("Couldn't get the torrent from that link", 502)
    body = dl.content or b''
    if str(dl.url or '').lower().startswith('magnet:') or body[:7].lower() == b'magnet:':
        raw = dl.url if str(dl.url or '').lower().startswith('magnet:') else body.decode('utf-8', 'ignore').strip().splitlines()[0]
        m = ts.safe_magnet(raw)
        if not m:
            raise ts.GrabError('Link not allowed', 400)
        return 'magnet', m
    if dl.status_code == 200 and body[:1] == b'd':
        return 'torrent', body
    raise ts.GrabError(NOT_A_TORRENT, 502)


def _categories(sess, url: str) -> Optional[Dict[str, Any]]:
    try:
        r = sess.get(f'{url}/api/v2/torrents/categories', timeout=10, allow_redirects=False)
        got = r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None
    return got if isinstance(got, dict) else None


def ensure_category(sess, url: str, name: str) -> Optional[str]:
    """None when qBittorrent has *name* (created now when missing), else the error to show."""
    if (url, name) in _have_category:
        return None
    cats = _categories(sess, url)
    if cats is None:
        return CATEGORY_UNREAD
    if name not in cats:
        try:
            with instance.scoped_add(JOB, name):
                r = sess.post(f'{url}/api/v2/torrents/createCategory', data={'category': name}, timeout=10,
                              allow_redirects=False)
        except instance.WritesBlocked:
            return OFF
        except requests.RequestException as e:
            logger.warning('TV grab: creating category %s failed (%s)', name, type(e).__name__)
            return CATEGORY_FAILED.format(name)
        if r.status_code != 200:
            again = _categories(sess, url)                   # 409: made meanwhile, or refused
            if again is None or name not in again:
                logger.warning('TV grab: qBittorrent refused category %s (HTTP %s)', name, r.status_code)
                return CATEGORY_FAILED.format(name)
        logger.info('TV grab: created the qBittorrent category %s', name)
    _have_category.add((url, name))
    return None


def _job_failed(h: str, item: Dict[str, Any], series_id: Optional[int], error: str, now: float) -> None:
    try:
        tv_store.set_meta(JOB_KEY + h, json.dumps({'state': 'grab_failed', 'name': item.get('title'), 'error': error,
                                                   'at': now, 'series_id': series_id}))
    except Exception:                                        # noqa: BLE001 - recording never breaks the answer
        logger.exception('TV grab: could not record the failure')


def _job_clear(h: str) -> None:
    with tv_store.write() as conn:
        conn.execute('DELETE FROM meta WHERE key = ?', (JOB_KEY + h,))


def grab(item: Dict[str, Any], series_id: int, season: Optional[int], episode_ids: List[int], origin: str,
         now: Optional[float] = None, session_factory: Callable = requests.Session,
         fetch: Optional[Callable] = None, tokens_only: bool = False) -> Dict[str, Any]:
    """Send *item* (a search result: title, downloadUrl, indexerId, size, quality) to qBittorrent.
    {'ok': True, 'hash'} or {'ok': False, 'error'}. Never raises for an expected failure."""
    from app import library_mover as mover, tracker_sources
    now = time.time() if now is None else now
    if origin not in ORIGINS:
        raise ValueError('origin')
    if not allowed():
        logger.info('TV grab: %s not sent (%s)', item.get('title'), OFF)
        return {'ok': False, 'error': OFF, 'blocked': True}
    try:
        kind, payload = (fetch or (lambda u: fetch_link(u, automatic=origin == 'auto', tokens_only=tokens_only)))(item.get('downloadUrl'))
    except tracker_sources.GrabError as e:
        return {'ok': False, 'error': str(e), 'link': True, 'status': getattr(e, 'status', 502)}
    h = info_hash(kind, payload)
    if not h:
        # v171 (Tester FF-1): a magnet with no (or a short) btih is a bad link: 400 at the route, no fallback
        return {'ok': False, 'error': NO_HASH, 'link': True, 'status': 400}
    with tv_store.connect() as conn:
        if conn.execute('SELECT 1 FROM grabs WHERE hash = ?', (h,)).fetchone():
            return {'ok': True, 'hash': h, 'existing': True}
        if conn.execute('SELECT 1 FROM blocklist WHERE info_hash = ? OR (release_title = ? AND series_id = ?)',
                        (h, item.get('title'), series_id)).fetchone():
            return {'ok': False, 'error': 'Blocklisted'}
    name = tv_downloads.category()
    sess, url = mover._qbit_session(session_factory, now)
    if not sess:
        _job_failed(h, item, series_id, NO_CLIENT, now)
        return {'ok': False, 'error': NO_CLIENT, 'client': True}
    try:
        why = ensure_category(sess, url, name)
        if why:                                              # nothing was added
            if why != OFF:
                _job_failed(h, item, series_id, why, now)
            mover._close(sess)
            return {'ok': False, 'error': why, **({'blocked': True} if why == OFF else {})}
    except Exception as e:                                   # noqa: BLE001 - fail cleanly, never crash
        logger.exception('TV grab: category check failed')
        mover._close(sess)
        _job_failed(h, item, series_id, CATEGORY_FAILED.format(name), now)
        return {'ok': False, 'error': CATEGORY_FAILED.format(name)}
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
        _job_failed(h, item, series_id, NO_CLIENT, now)
        return {'ok': False, 'error': NO_CLIENT, 'client': True}
    finally:
        mover._close(sess)
    from app.qbit_connector import text_of
    if r.status_code != 200 or text_of(r).strip() not in ('Ok.', ''):
        err = f'qBittorrent returned HTTP {r.status_code}' if r.status_code != 200 else 'qBittorrent refused the torrent'
        _job_failed(h, item, series_id, err, now)
        return {'ok': False, 'error': err}
    with tv_store.write() as conn:
        conn.execute('INSERT OR IGNORE INTO grabs (hash, series_id, season_number, episode_ids, release_title, tracker_id,'
                     ' quality, size, origin, grabbed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     (h, series_id, season, json.dumps(sorted(episode_ids)), item.get('title'), item.get('indexerId'),
                      item.get('quality'), int(item.get('size') or 0), origin, now))
        conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                     (now, series_id, json.dumps(sorted(episode_ids)), 'grabbed',
                      json.dumps({'title': item.get('title'), 'origin': origin, 'hash': h, 'tracker': item.get('indexer')})))
    _job_clear(h)
    logger.info('TV grab: %s (%s, %d episode(s), category %s)', item.get('title'), origin, len(episode_ids), name)
    return {'ok': True, 'hash': h}
