"""TV download watch (v158a, v158b): every minute, completed qBittorrent torrents of THIS
instance's TV category are imported into their show through the library mover's transfer
(hardlink, else copy; never a move, never an overwrite, never a delete), then linked in tv.db at
once (tv_watch.episode_imported), so the card turns downloaded on this same pass.

The name comes from the instance (category()): luma-test-tv on LUMA_INSTANCE=test
(LUMA_TEST_TV_TAG), luma-tv on prod (LUMA_TV_CATEGORY). Luma's TV grabs set it as both the
qBittorrent category and tag. A torrent is this instance's when its category or a tag is that
name and it carries nothing of the other instance's name; everything else in a shared
qBittorrent is invisible here, so neither instance ever acts on the other's torrents.

On: test, while the instance allows tv_import (the default there); prod, only when TV settings'
automation.import_enabled is on (off by default). Reads only from qBittorrent (torrents/info,
torrents/files). The destination is the show's folder in its TV library (Season NN when season
folders are on); the library must be writable and have room. No room: failed with the reason,
then the mover's rule (library_mover.space_retry_due): one automatic retry once the free space
grew by the files' size and 15 min passed, else the Retry button (retry()).
Each file must parse to an episode the show has; anything else is left alone.

State per torrent hash is in tv.db meta ('tvdl:<hash>'): {state, at, completed, files, error}.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from app import file_management as fm
from app import instance, library_mover as mover, qbit_owner, tv_match, tv_release, tv_settings, tv_store

logger = logging.getLogger(__name__)

TAG_ENV = 'LUMA_TEST_TV_TAG'
DEFAULT_TAG = 'luma-test-tv'
PROD_ENV = 'LUMA_TV_CATEGORY'
PROD_DEFAULT = 'luma-tv'
INTERVAL_S = 60
JOB = 'tv_import'
NOT_WRITABLE = 'library folder not writable'
NO_SHOW = 'no show in the library'
_DONE = ('imported', 'refused')
_lock = threading.Lock()


def _valid(raw: str, other: str) -> bool:
    return bool(qbit_owner.TAG_RE.match(raw)) and raw.casefold() not in qbit_owner._reserved() \
        and raw.casefold() != qbit_owner.tag_name().casefold() and raw.casefold() != other.casefold()


def _names() -> Tuple[str, str]:
    """(test's name, prod's name), never equal."""
    raw_t = (os.environ.get(TAG_ENV) or '').strip()
    raw_p = (os.environ.get(PROD_ENV) or '').strip()
    test = raw_t if raw_t and _valid(raw_t, PROD_DEFAULT if not raw_p else raw_p) else DEFAULT_TAG
    prod = raw_p if raw_p and _valid(raw_p, test) else PROD_DEFAULT
    if prod.casefold() == test.casefold():
        test, prod = DEFAULT_TAG, PROD_DEFAULT
    return test, prod


def category() -> str:
    """This instance's TV category and tag: luma-test-tv on test, luma-tv on prod."""
    test, prod = _names()
    return test if instance.is_test() else prod


def other_category() -> str:
    test, prod = _names()
    return prod if instance.is_test() else test


def tag_name() -> str:                    # the v158a name, kept for callers
    return category()


def is_mine(t: Dict[str, Any]) -> bool:
    """This instance's TV torrent: category or a tag is ours, nothing is the other instance's."""
    mine, other = category().casefold(), other_category().casefold()
    cat = str(t.get('category') or '').strip().casefold()
    tags = {x.casefold() for x in qbit_owner.torrent_tags(t)}
    if cat == other or other in tags:
        return False
    return cat == mine or mine in tags


def watch_enabled() -> bool:
    if not instance.writes_allowed(JOB):
        return False
    if instance.is_test():
        return True
    return bool(tv_settings.load()['automation'].get('import_enabled'))


def _key(h: str) -> str:
    return f'tvdl:{h}'


def status(h: str) -> Optional[Dict[str, Any]]:
    v = tv_store.get_meta(_key(h))
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None


def _save(h: str, **fields) -> None:
    tv_store.set_meta(_key(h), json.dumps({**(status(h) or {}), **fields}))


def _complete(t: Dict[str, Any]) -> bool:
    try:
        return float(t.get('progress') or 0) >= 1.0
    except (TypeError, ValueError):
        return False


def _series_for(name: str) -> Optional[Dict[str, Any]]:
    """The library show a release name is for (title or alias, year when the name has one)."""
    rel = tv_release.parse(name)
    want = tv_match.normalize(rel.title or '')
    if not want:
        return None
    paths = {r['id']: os.path.realpath(r['path']) for r in tv_settings.libraries()}
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT id, title, year, aliases, path_id, folder, season_folder FROM series WHERE path_id IS NOT NULL')]
    hits = []
    for r in rows:
        if r['path_id'] not in paths:
            continue
        names = {tv_match.normalize(r['title'])}
        try:
            names |= {tv_match.normalize(a) for a in json.loads(r['aliases'] or '[]') if isinstance(a, str)}
        except ValueError:
            pass
        if want in names and (rel.year is None or r['year'] in (None, rel.year)):
            hits.append({**r, 'root': paths[r['path_id']]})
    return hits[0] if len(hits) == 1 else None


def _episodes(sid: int, name: str, rel_path: str) -> Tuple[Optional[int], List[int]]:
    r = tv_release.parse_file(os.path.basename(name), rel_path)
    if r.season is None or not r.episodes:
        return None, []
    with tv_store.connect() as conn:
        have = {x['episode_number'] for x in conn.execute(
            'SELECT episode_number FROM episodes WHERE series_id = ? AND season_number = ?', (sid, r.season))}
    eps = [e for e in r.episodes if e in have]
    return (r.season, eps) if eps and len(eps) == len(r.episodes) else (None, [])


def _show_folder(s: Dict[str, Any]) -> str:
    if s['folder']:
        return s['folder']
    folder, _ = fm.movie_names(s['title'], s['year'] or '', '')
    if not folder or '/' in folder or folder in ('.', '..'):
        raise mover.Refused('unsafe file name')
    with tv_store.write() as conn:                      # a show added from Search gets its folder now
        conn.execute('UPDATE series SET folder = ? WHERE id = ? AND folder IS NULL', (folder, s['id']))
    return folder


def import_torrent(t: Dict[str, Any], data: Dict[str, Any], now: float,
                   list_files: Optional[Callable] = None) -> Dict[str, Any]:
    """File one completed TV torrent. Returns {state, files, error}. Raises mover.Pending / Refused."""
    from app import tv_watch
    name = str(t.get('name') or '')
    s = _series_for(name)
    if s is None:
        raise mover.Pending(NO_SHOW)
    path_map = data['path_map']
    save_real = os.path.realpath(fm.map_path(str(t.get('save_path') or ''), path_map))
    content = fm.map_path(str(t.get('content_path') or ''), path_map)
    if not content.startswith('/'):
        raise mover.Pending('no download path')
    root = mover._downloads_root()
    if not fm._under(save_real, root):
        raise mover.Pending(mover.NOT_MAPPED)
    lib_root = s['root']
    if fm.blocked(lib_root) or not os.path.isdir(lib_root) or fm.overlaps_downloads(lib_root) \
            or fm._under(root, lib_root) or fm._under(lib_root, root):
        raise mover.Pending('library folder not available')
    if not os.access(lib_root, os.W_OK | os.X_OK):
        raise mover.Pending(NOT_WRITABLE)
    added = float(t.get('added_on') or now)
    if os.path.realpath(content) == save_real:
        listed = list_files(str(t.get('hash') or '')) if list_files else None
        if listed is None:
            raise mover.Pending('waiting for the file list')
        found = mover.find_listed_files(save_real, listed, root, added, now)
    else:
        found = mover.find_video_files(content, root, added, now)
    plan = []
    for f in found:
        rel = os.path.relpath(f.path, os.path.dirname(os.path.realpath(content)))
        season, eps = _episodes(s['id'], f.path, rel)
        if eps:
            plan.append((f, season))
    if not plan:
        raise mover.Refused('no episodes of this show in the download')
    folder = _show_folder(s)
    owner = mover._owner_of(lib_root)
    mode = 'hardlink' if data.get('transfer') == 'hardlink' else 'copy'
    if mode == 'copy':
        mover._check_space(lib_root, sum(f.size for f, _ in plan))
    files = []
    rfd = mover._open_root(lib_root)
    try:
        sfd, _, show_path = mover._ensure_folder(rfd, lib_root, folder, owner)
        try:
            for f, season in plan:
                dfd, dpath, close = sfd, show_path, False
                if s['season_folder']:
                    dfd, _, dpath = mover._ensure_folder(sfd, show_path, f'Season {season:02d}', owner)
                    close = True
                try:
                    fname = os.path.basename(f.path)
                    if not fname or fname in ('.', '..'):
                        raise mover.Refused('unsafe file name')
                    how = mover.transfer(f.path, dfd, dpath, fname, mode, owner, root, f)
                finally:
                    if close:
                        os.close(dfd)
                dest = os.path.join(dpath, fname)
                linked = tv_watch.episode_imported(dest)          # the import hook: the card turns now
                files.append({'path': dest, 'how': how, 'linked': bool(linked)})
        finally:
            os.close(sfd)
    finally:
        os.close(rfd)
    return {'state': 'imported', 'files': files, 'series_id': s['id']}


def tick(now: Optional[float] = None, session_factory: Callable = requests.Session) -> Dict[str, int]:
    """One pass. Never contacts qBittorrent without a TV library or a downloads folder."""
    now = time.time() if now is None else now
    out = {'checked': 0, 'imported': 0}
    if not watch_enabled() or not tv_settings.libraries():
        return out
    try:
        mover._downloads_root()
    except mover.Pending:
        return out
    if not _lock.acquire(blocking=False):
        return out
    try:
        sess, url = mover._qbit_session(session_factory, now)
        if not sess:
            return out
        try:
            name = category()
            torrents, seen = [], set()
            for params in ({'category': name, 'filter': 'completed'}, {'tag': name, 'filter': 'completed'}):
                try:
                    r = sess.get(f'{url}/api/v2/torrents/info', params=params, timeout=10, allow_redirects=False)
                    got = r.json() if r.status_code == 200 else None
                except (requests.RequestException, ValueError):
                    got = None
                if not isinstance(got, list):
                    return out
                for t in got:
                    if isinstance(t, dict) and str(t.get('hash') or '').lower() not in seen:
                        seen.add(str(t.get('hash') or '').lower())
                        torrents.append(t)
            data = fm.load()
            list_files = mover._files_of(sess, url)
            for t in torrents:
                if not is_mine(t) or not _complete(t):
                    continue                                  # only this instance's TV torrents, never others
                h = str(t.get('hash') or '').lower()
                if not mover._HASH_RE.match(h):
                    continue
                st = status(h) or {}
                if st.get('state') in _DONE:
                    continue
                if st.get('state') == 'failed' and st.get('error') == mover.INSUFFICIENT_SPACE:
                    grew = mover.space_retry_due(st, now)     # the mover's rule: once, or Retry
                    if grew is None:
                        continue
                    _save(h, auto_retried=True)
                    logger.info('TV import: %s retried once (free space grew by %d bytes)', t.get('name'), grew)
                out['checked'] += 1
                try:
                    res = import_torrent(t, data, now, list_files)
                    _save(h, **res, at=now, completed=t.get('completion_on'), name=t.get('name'), error=None)
                    out['imported'] += 1
                    logger.info('TV import: %s -> %d file(s)', t.get('name'), len(res['files']))
                except mover.InsufficientSpace as e:
                    _save(h, state='failed', error=mover.INSUFFICIENT_SPACE, at=now, finished=now, name=t.get('name'),
                          space={'path': e.path, 'need': e.need, 'free': mover._free_or_none(e.path)})
                except mover.Refused as e:
                    _save(h, state='refused', error=str(e), at=now, name=t.get('name'))
                except mover.Pending as e:
                    _save(h, state='waiting', error=str(e), at=now, name=t.get('name'))
                except Exception as e:                        # one bad torrent never ends the pass
                    logger.exception('TV import: %s failed', t.get('name'))
                    _save(h, state='waiting', error=type(e).__name__, at=now, name=t.get('name'))
        finally:
            mover._close(sess)
    finally:
        _lock.release()
    return out


def retry(h: str) -> bool:
    """The Retry button: a failed or refused torrent is looked at again on the next pass, and a later
    no-space failure gets its one automatic retry again. False when there's nothing to retry."""
    h = (h or '').lower()
    st = status(h)
    if not st or st.get('state') not in ('failed', 'refused', 'waiting'):
        return False
    st.update(state='waiting', error=None)
    for k in ('auto_retried', 'space', 'finished'):
        st.pop(k, None)
    tv_store.set_meta(_key(h), json.dumps(st))
    return True


def failures() -> List[Dict[str, Any]]:
    """Torrents the watch couldn't import: {hash, name, state, error, at}."""
    out = []
    with tv_store.connect() as conn:
        for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tvdl:%'"):
            try:
                v = json.loads(r['value'])
            except ValueError:
                continue
            if v.get('state') in ('failed', 'refused'):
                out.append({'hash': r['key'][5:], 'name': v.get('name'), 'state': v['state'], 'error': v.get('error'),
                            'at': v.get('at')})
    return sorted(out, key=lambda x: -(x['at'] or 0))


_started = {'on': False}


def start() -> bool:
    if _started['on'] or os.environ.get('LUMA_SKIP_BACKGROUND_THREADS') or not watch_enabled():
        return False
    _started['on'] = True

    def loop():
        while True:
            time.sleep(INTERVAL_S)
            try:
                tick()
            except Exception as e:                            # never let one pass end the thread
                logger.error('TV import pass failed: %s', type(e).__name__)
    threading.Thread(target=loop, name='tv-download-watch', daemon=True).start()
    logger.info('TV download watch started (category %s, every %ds)', category(), INTERVAL_S)
    return True
