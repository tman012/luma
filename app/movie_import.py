"""v204 (movie piece 4): completed movie downloads are imported into the Movies library (test only).

Every INTERVAL_S on LUMA_INSTANCE=test: completed torrents in the download client with the movie category or
tag (LUMA_MOVIE_CATEGORY, default 'movie') are matched to a watchlist movie through their movie_grabs row
(by info hash; piece 3 writes it). The biggest video file is COPIED (never moved; the source is never touched
or deleted) into the Movies root (Settings > Movies, meta 'library_root') under the Movies naming (meta
'naming'; v203 (PM) default Luma's own: rename on, 'Title (Year)/Title (Year).ext', a colon becomes a dash).
An existing movie folder is reused as spelled; an existing file is never touched. The Library path is pre-filled
from Luma's Movies library path (Radarr's root folder isn't in the export).
The copy goes to a temporary name in the movie folder, is verified (size and sha256 against the source), then
linked into place with an exclusive create, so an existing file is never overwritten (a different file
already there: refused). Only after that is the grab marked 'imported' and the movie taken off the watchlist.
v283: library_notify (Web Push + bell) fires on that import-complete — never on grab.

Routes (test only, else 404; reading needs the owner, changes and a manual run need the signed-in owner):
GET  /api/settings/movies/import        {root, naming, grabs}
PUT  /api/settings/movies/import        {root?, naming?: {standard, folder}}
POST /api/settings/movies/import/run    one pass now -> {checked, imported}
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests
from flask import Blueprint, request

from app import instance, movie_grabs, movie_settings as ms

logger = logging.getLogger(__name__)
bp = Blueprint('movie_import', __name__)

CATEGORY_ENV = 'LUMA_MOVIE_CATEGORY'
DEFAULT_CATEGORY = 'movie'
INTERVAL_S = 60
VIDEO_EXT = ('.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mov', '.wmv')
PART = '.lumapart'
# v203 (PM): Luma's own movie naming (file_management.movie_names, prod's auto_rename): rename on,
# 'Title (Year)/Title (Year).ext', a colon becomes a dash ('Mission- Impossible - Fallout (2018)')
DEFAULT_NAMING: Dict[str, Any] = {
    'rename': True,
    'standard': '{Movie Title} ({Release Year})',
    'folder': '{Movie Title} ({Release Year})',
    'colon_replacement': 'dash', 'replace_illegal': True,
}
_lock = threading.Lock()


class Skip(Exception):
    """Not now (left for a later pass)."""


class Refused(Exception):
    """Never (the grab is marked failed with the reason)."""


# ------------------------------------------------------------------ settings

def category() -> str:
    """The legacy movie category/tag ('movie'; LUMA_MOVIE_CATEGORY): still watched so older grabs import."""
    return (os.environ.get(CATEGORY_ENV) or '').strip() or DEFAULT_CATEGORY


def categories() -> List[str]:
    """v324c: every name a movie grab can carry: the luma-test tag (movie_scanner.category(), v324b) and the
    legacy 'movie'. Only torrents with a movie_grabs row are ever imported here."""
    from app import qbit_owner
    out: List[str] = []
    for n in (qbit_owner.tag_name(), category()):
        if n and n.casefold() not in {x.casefold() for x in out}:
            out.append(n)
    return out


def _mover_owns(h: str) -> bool:
    """v324c: a /api/downloads/add job in library_mover files this hash itself (it marks the row)."""
    try:
        from app import file_management as fm
        return h in (fm.load().get('jobs') or {})
    except Exception:                                  # noqa: BLE001
        return False


def luma_movies_root() -> str:
    """v203 (PM): the pre-fill: Luma's own Movies library path (Settings > Library paths, type 'movies')."""
    try:
        from app.settings_manager import SettingsManager
        for p in (SettingsManager().get('media.paths') or []):
            if isinstance(p, dict) and p.get('type') == 'movies' and isinstance(p.get('path'), str) and p['path'].startswith('/'):
                return p['path']
    except Exception:                                    # noqa: BLE001
        pass
    return ''


def root() -> str:
    with ms.connect() as conn:
        return str(ms._meta(conn, 'library_root', '') or '')   # PM: never fall back; unset means no imports


def naming() -> Dict[str, Any]:
    with ms.connect() as conn:
        raw = ms._meta(conn, 'naming', {}) or {}
    out = dict(DEFAULT_NAMING)
    if isinstance(raw.get('rename'), bool):
        out['rename'] = raw['rename']
    for k in ('standard', 'folder'):
        if isinstance(raw.get(k), str) and raw[k].strip():
            out[k] = raw[k].strip()[:300]
    return out


def save_settings(body: Any) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    if not isinstance(body, dict):
        return {'body': 'Invalid'}
    upd: Dict[str, Any] = {}
    if 'root' in body:
        r = body['root']
        if not isinstance(r, str) or not r.startswith('/') or '\x00' in r or len(r) > 1024:
            errors['root'] = 'Use a full folder path'
        elif not os.path.isdir(r):
            errors['root'] = 'Folder not found'
        else:
            upd['library_root'] = os.path.normpath(r)
    if 'naming' in body:
        n = body['naming']
        if not isinstance(n, dict) or any(
                (k == 'rename' and not isinstance(v, bool)) or (k != 'rename' and (k not in ('standard', 'folder')
                                                                                   or not isinstance(v, str) or '/' in v))
                for k, v in n.items()):
            errors['naming'] = 'Invalid'
        else:
            upd['naming'] = {k: (v if isinstance(v, bool) else v.strip()[:300]) for k, v in n.items()
                             if isinstance(v, bool) or v.strip()}
    if errors:
        return errors
    with ms.connect() as conn:
        for k, v in upd.items():
            ms._set_meta(conn, k, v)
    return {}


# ------------------------------------------------------------------ naming

def _quality(release: str):
    from app import tv_monitor, tv_naming
    q = tv_monitor.quality_of(release)
    return tv_naming.quality_full(q, bool(re.search(r'\b(proper|repack)\b', release, re.I)), release), q


def render(fmt: str, title: str, year: Optional[int], release: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    from app import tv_monitor, tv_naming
    cfg = cfg or DEFAULT_NAMING
    full, q = _quality(release)
    vals = {'movie title': title, 'movie cleantitle': tv_naming.clean_title(title), 'release year': str(year or ''),
            'quality full': full, 'quality title': tv_monitor.quality_label(q) or ''}
    out = re.sub(r'\{([^{}]+)\}', lambda m: vals.get(m.group(1).strip().lower(), ''), fmt)
    out = tv_naming.clean_text(out, {'colon_replacement': cfg.get('colon_replacement', 'smart'),
                                     'replace_illegal': cfg.get('replace_illegal', True)})
    out = re.sub(r'\(\s*\)|\[\s*\]', '', out)               # an empty year or token
    out = re.sub(r'\s+', ' ', out).strip(' .-')
    if not out or out in ('.', '..') or '/' in out or '\x00' in out:
        raise Refused('unsafe file name')
    return out


# ------------------------------------------------------------------ copy

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def copy_verified(src: str, dest: str) -> str:
    """Copy *src* to *dest* without ever overwriting: 'copied', or 'exists' when the same file is already there.
    The source is only read. A different file at *dest* raises Refused."""
    # v318: remote push-import — .72 performs cp -a (no bytes through Luma).
    from app import remote_mover
    if remote_mover.enabled():
        if os.path.lexists(dest):
            if os.path.isfile(dest) and not os.path.islink(dest) and os.path.getsize(dest) == os.path.getsize(src) \
                    and _sha256(dest) == _sha256(src):
                return 'exists'
            raise Refused('a different file is already there')
        try:
            # v324: final name; temp = library_mover.partial_name() in dest dir (see remote_mover).
            return remote_mover.transfer(src, dest)
        except remote_mover.RemoteTransient as e:
            # a cut-off copy (helper unreachable, ssh rc 255, stall, remote reboot) is not a refusal: the
            # grab stays 'grabbed' and the next pass tries again (it used to be 'failed' for good)
            raise Skip(f'remote copy cut off ({e})') from e
        except remote_mover.RemoteMoverError as e:
            raise Refused(str(e)) from e
    if os.path.lexists(dest):
        if os.path.isfile(dest) and not os.path.islink(dest) and os.path.getsize(dest) == os.path.getsize(src) \
                and _sha256(dest) == _sha256(src):
            return 'exists'
        raise Refused('a different file is already there')
    tmp = dest + PART
    if os.path.lexists(tmp):
        os.unlink(tmp)                                     # only our own leftover temp name
    try:
        with open(src, 'rb') as fi, open(tmp, 'xb') as fo:
            for b in iter(lambda: fi.read(1 << 20), b''):
                fo.write(b)
            fo.flush()
            os.fsync(fo.fileno())
        if os.path.getsize(tmp) != os.path.getsize(src) or _sha256(tmp) != _sha256(src):
            raise OSError('copy did not verify')
        os.link(tmp, dest)                                 # exclusive: fails if dest appeared meanwhile
    except FileExistsError:
        raise Refused('a different file is already there')
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)
    return 'copied'


# ------------------------------------------------------------------ import

def _video(content: str) -> str:
    if os.path.islink(content):
        raise Refused('symlink')
    if os.path.isfile(content):
        if content.lower().endswith(VIDEO_EXT):
            return content
        raise Refused('no video file')
    if not os.path.isdir(content):
        raise Skip('download not found')
    best, size = '', -1
    for d, dirs, files in os.walk(content):
        dirs[:] = [x for x in dirs if x.lower() not in ('sample', 'samples', 'extras')]
        for f in files:
            p = os.path.join(d, f)
            if f.lower().endswith(VIDEO_EXT) and 'sample' not in f.lower() and not os.path.islink(p):
                s = os.path.getsize(p)
                if s > size:
                    best, size = p, s
    if not best:
        raise Refused('no video file')
    return best


def _watch(tmdb_id: int) -> Optional[Dict[str, Any]]:
    with ms.connect() as conn:
        r = conn.execute('SELECT * FROM watchlist WHERE tmdb_id = ?', (tmdb_id,)).fetchone()
        if r:
            return dict(r)
    from app.automation import gr_store       # v252: a Genre Roulette grab is not on the watchlist
    return gr_store.title_for(tmdb_id)


def import_one(t: Dict[str, Any], lib_root: str, path_map: Optional[Callable[[str], str]] = None,
               now: Optional[float] = None) -> Dict[str, Any]:
    """File one completed torrent. Returns {state, path, how}. Raises Skip / Refused."""
    now = time.time() if now is None else now
    h = str(t.get('hash') or '').lower()
    g = movie_grabs.by_hash(h)
    if not g:
        raise Skip('no movie grab for this download')
    if g['state'] == 'imported':
        return {'state': 'imported', 'path': g['path'], 'how': 'done'}
    if _mover_owns(h):
        raise Skip('filed by the library mover')
    m = _watch(g['tmdb_id'])
    title, year = (m['title'], m['year']) if m else (None, None)
    if not title:
        raise Refused('movie not on the watchlist')
    content = str(t.get('content_path') or '')
    content = path_map(content) if path_map else content
    if not content.startswith('/'):
        raise Skip('no download path')
    src = _video(os.path.realpath(content) if not os.path.islink(content) else content)
    cfg = naming()
    release = str(g['release_title'] or t.get('name') or '')
    lib_root = route(lib_root, release, os.path.basename(src), title, year, cfg, now)
    from app import remote_mover as _rm
    if not lib_root or not os.path.isdir(lib_root) or (
            not _rm.enabled() and not os.access(lib_root, os.W_OK | os.X_OK)):
        raise Skip('movies folder not available')
    rr, sr = os.path.realpath(lib_root), os.path.realpath(src)
    if sr.startswith(rr + os.sep) or rr.startswith(os.path.realpath(os.path.dirname(sr)) + os.sep):
        raise Refused('download and library overlap')
    folder = render(cfg['folder'], title, year, release, cfg)
    if cfg.get('rename', True):
        fname = render(cfg['standard'], title, year, release, cfg) + os.path.splitext(src)[1].lower()
    else:
        fname = os.path.basename(src)
    # v203 (PM): a movie folder that already exists is reused as spelled (same name, any case or punctuation)
    folder = existing_folder(rr, folder) or folder
    dest_dir = os.path.join(rr, folder)
    if os.path.realpath(os.path.dirname(dest_dir)) != rr:
        raise Refused('unsafe file name')
    movie_grabs.set_state(h, 'importing', now=now)
    if _rm.enabled():
        try:
            _rm.mkdir_p(dest_dir)
        except _rm.RemoteTransient as e:
            raise Skip(f'remote host unreachable ({e})') from e
        except _rm.RemoteMoverError as e:
            raise Refused(str(e)) from e
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError:
            pass
    else:
        os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, fname)
    how = copy_verified(src, dest)
    # verified: only now is the grab imported and the movie off the watchlist
    first = movie_grabs.mark_imported(h, path=dest, now=now)
    with ms.connect() as conn:
        conn.execute('DELETE FROM watchlist WHERE tmdb_id = ?', (g['tmdb_id'],))
    logger.info('Movie import: %s -> %s (%s)', t.get('name'), dest, how)
    # v283: prod AutoRenamer fires Web Push on import-complete; GR/watchlist movies
    # use this path (not library_mover). Grab stays silent — notify only here.
    if not first:                                    # v324c: someone else marked it; they notified
        return {'state': 'imported', 'path': dest, 'how': how}
    try:
        from app import library_notify
        library_notify.notify_library_added(
            media_type='movie',
            title=title or '',
            year=year or '',
            tmdb_id=g.get('tmdb_id'),
            dest_path=dest,
            source='movie_import',
        )
    except Exception:
        logger.exception('Movie import: notify failed for %s', title)
    return {'state': 'imported', 'path': dest, 'how': how}


ROUTE_FOLDERS = {'4k': '4K', 'remux': 'Full Rips', 'recent': 'Recently Released', 'movies': 'Movies'}


def route(lib_root: str, release: str, fname: str, title: str, year: Any, cfg: Dict[str, Any],
          now: Optional[float] = None) -> str:
    """v203b (PM, ported from prod auto_rename 1b4ea74): the sibling of *lib_root* this movie belongs in:
    4K, Full Rips (remux), Recently Released (year >= current-1), else Movies. A movie whose folder
    already exists in any of them stays there."""
    if not lib_root:
        return lib_root
    parent = os.path.dirname(os.path.normpath(lib_root))
    roots = {k: os.path.join(parent, v) for k, v in ROUTE_FOLDERS.items()}
    if os.path.normpath(lib_root) not in roots.values():
        return lib_root                                   # not the Video/<4K|Full Rips|...> layout: one root
    folder = render(cfg['folder'], title, year, release, cfg)
    for k in ('4k', 'remux', 'recent', 'movies'):
        if os.path.isdir(roots[k]) and existing_folder(roots[k], folder):
            return roots[k]
    pick = _route_pick(roots, release, fname, year, now)
    return pick if os.path.isdir(pick) else lib_root      # a missing sibling folder: file under the set root


def _route_pick(roots: Dict[str, str], release: str, fname: str, year: Any, now: Optional[float]) -> str:
    from app import file_management as fm
    c = dict(fm.classify(release, fname))
    if c.get('resolution') == '2160p':
        return roots['4k']
    if c.get('source') == 'remux':
        return roots['remux']
    cur = time.localtime(time.time() if now is None else now).tm_year
    if str(year or '').isdigit() and int(year) >= cur - 1:
        return roots['recent']
    return roots['movies']


def existing_folder(lib_root: str, folder: str) -> Optional[str]:
    """The library's folder for this movie as already spelled on disk, if there is one."""
    from app import tv_match
    want = tv_match.normalize(folder)
    try:
        names = sorted(os.listdir(lib_root))
    except OSError:
        return None
    exact = [n for n in names if n == folder and os.path.isdir(os.path.join(lib_root, n))]
    if exact:
        return exact[0]
    near = [n for n in names if tv_match.normalize(n) == want and os.path.isdir(os.path.join(lib_root, n))]
    return near[0] if len(near) == 1 else None


def _torrents(session_factory: Callable = requests.Session, now: Optional[float] = None) -> Optional[List[Dict[str, Any]]]:
    from app import library_mover as mover
    sess, url = mover._qbit_session(session_factory, now)
    if not sess:
        return None
    try:
        seen, out = set(), []
        for params in [p for n in categories() for p in ({'category': n, 'filter': 'completed'},
                                                         {'tag': n, 'filter': 'completed'})]:
            try:
                r = sess.get(f'{url}/api/v2/torrents/info', params=params, timeout=10, allow_redirects=False)
                got = r.json() if r.status_code == 200 else None
            except (requests.RequestException, ValueError):
                got = None
            if not isinstance(got, list):
                return None
            for t in got:
                h = str((t or {}).get('hash') or '').lower() if isinstance(t, dict) else ''
                if h and h not in seen:
                    seen.add(h)
                    out.append(t)
        return out
    finally:
        mover._close(sess)


def _is_movie(t: Dict[str, Any]) -> bool:
    names = {n.casefold() for n in categories()}
    tags = {x.strip().casefold() for x in str(t.get('tags') or '').split(',') if x.strip()}
    return str(t.get('category') or '').casefold() in names or bool(names & tags)


def _complete(t: Dict[str, Any]) -> bool:
    from app import library_mover as mover
    if mover.is_paused(t):  # never import a paused/stopped torrent
        return False
    try:
        return float(t.get('progress') or 0) >= 1.0 and float(t.get('amount_left') or 0) == 0
    except (TypeError, ValueError):
        return False


def tick(now: Optional[float] = None, fetch: Optional[Callable[[], Optional[List[Dict[str, Any]]]]] = None,
         path_map: Optional[Callable[[str], str]] = None) -> Dict[str, int]:
    """One pass (test only). *fetch* returns the completed torrents (default: qBittorrent)."""
    now = time.time() if now is None else now
    out = {'checked': 0, 'imported': 0}
    if not instance.is_test():
        return out
    lib = root()
    if not lib or not _lock.acquire(blocking=False):
        return out
    try:
        if path_map is None:
            from app import file_management as fm
            pm = fm.load().get('path_map') or []
            path_map = lambda p: fm.map_path(p, pm)  # noqa: E731
        torrents = (fetch or _torrents)() or []
        for t in torrents:
            if not isinstance(t, dict) or not _is_movie(t) or not _complete(t):
                continue
            g = movie_grabs.by_hash(str(t.get('hash') or ''))
            if not g or g['state'] in ('imported', 'failed'):
                continue
            out['checked'] += 1
            try:
                if import_one(t, lib, path_map, now)['state'] == 'imported':
                    out['imported'] += 1
            except Refused as e:
                movie_grabs.set_state(g['hash'], 'failed', error=str(e), now=now)
                logger.info('Movie import: %s refused (%s)', t.get('name'), e)
            except Skip as e:
                movie_grabs.set_state(g['hash'], 'grabbed', error=str(e), now=now)
            except OSError as e:
                movie_grabs.set_state(g['hash'], 'grabbed', error=f'file error ({type(e).__name__})', now=now)
                logger.warning('Movie import: %s file error %s', t.get('name'), type(e).__name__)
    finally:
        _lock.release()
    return out


_started = False


def start() -> bool:
    global _started
    if _started or not instance.is_test():
        return False
    _started = True

    def loop():
        while True:
            time.sleep(INTERVAL_S)
            try:
                tick()
            except Exception:                          # noqa: BLE001 - the loop never dies
                logger.exception('Movie import pass failed')
    threading.Thread(target=loop, name='movie-import', daemon=True).start()
    return True


# ------------------------------------------------------------------ routes

def _guard(write: bool = False):
    if not instance.is_test():
        return ms._out({'error': 'Not found'}, 404)
    return ms._guard(write)


@bp.route('/api/settings/movies/import', methods=['GET'])
def import_get():
    return _guard() or ms._out({'root': root(), 'naming': naming(), 'grabs': movie_grabs.all_rows()})


@bp.route('/api/settings/movies/import', methods=['PUT'])
def import_put():
    bad = _guard(write=True)
    if bad:
        return bad
    errors = save_settings(request.get_json(silent=True))
    if errors:
        return ms._out({'errors': errors}, 400)
    return ms._out({'root': root(), 'naming': naming()})


@bp.route('/api/settings/movies/import/run', methods=['POST'])
def import_run():
    return _guard(write=True) or ms._out(tick())
