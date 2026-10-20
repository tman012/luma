"""v204c (PM): pre-grab duplicate guard for movies. Before any movie grab (scanner automatic and manual),
the movie is skipped when
(a) the library already has it: a 'Title (Year)' folder (Luma's folder naming, matched like the importer's
    existing_folder) holding a video file, in any routed root (Video/4K, Full Rips, Recently Released,
    Movies: movie_import.ROUTE_FOLDERS, siblings of library_root) or library_root itself; or
(b) qBittorrent already has a torrent for it: GET /api/v2/torrents/info (every category and tag, Radarr's
    too) through Luma's own qBittorrent Web API session (library_mover._qbit_session, the one grabs use),
    each torrent name parsed with movie_scanner.parse and matched on normalized title and year.
qBittorrent unreachable, or a bad answer: fail closed (skip; the scanner retries on its next pass).
Every skip is logged at INFO. Reads only."""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Optional

import requests

logger = logging.getLogger(__name__)

ON_DISK = 'already in the library'
IN_QBIT = 'already in qBittorrent'
NO_QBIT = "qBittorrent can't be reached to check for a duplicate"
VIDEO_EXTS = ('.mkv', '.mp4', '.avi', '.m4v', '.ts', '.m2ts', '.mov', '.wmv', '.mpg', '.mpeg', '.webm')


def _roots() -> list:
    from app import movie_import as mi
    lib = mi.root()
    if not lib:
        return []
    parent = os.path.dirname(os.path.normpath(lib))
    out = [os.path.join(parent, v) for v in mi.ROUTE_FOLDERS.values()]
    return out + ([lib] if os.path.normpath(lib) not in [os.path.normpath(p) for p in out] else [])


def _has_video(path: str) -> bool:
    for _, _, files in os.walk(path):
        if any(os.path.splitext(f)[1].lower() in VIDEO_EXTS for f in files):
            return True
    return False


def on_disk(title: str, year: Any) -> Optional[str]:
    """The library folder already holding this movie (a video file inside), else None."""
    from app import movie_import as mi
    folder = mi.render(mi.naming()['folder'], title, year, '', mi.naming())
    for r in _roots():
        if not os.path.isdir(r):
            continue
        hit = mi.existing_folder(r, folder)
        if hit and _has_video(os.path.join(r, hit)):
            return os.path.join(r, hit)
    return None


def in_qbit(title: str, year: Any, session_factory: Callable = requests.Session,
            now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """{'hit': name or None} after asking qBittorrent; None when it can't be asked (fail closed)."""
    from app import library_mover as mover, movie_scanner, tv_match
    sess, url = mover._qbit_session(session_factory, now)
    if not sess:
        return None
    try:
        try:
            r = sess.get(f'{url}/api/v2/torrents/info', timeout=10, allow_redirects=False)
            got = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            got = None
        if not isinstance(got, list):
            return None
        want = tv_match.normalize(title)
        y = int(year) if str(year or '').isdigit() else None
        for t in got:
            name = str((t or {}).get('name') or '') if isinstance(t, dict) else ''
            n, ty = movie_scanner.parse(name)
            if n and n == want and (y is None or ty == y):
                return {'hit': name}
        return {'hit': None}
    finally:
        mover._close(sess)


def check(title: str, year: Any, session_factory: Callable = requests.Session,
          now: Optional[float] = None) -> Optional[str]:
    """None: no duplicate, grab. Else the skip reason (logged at INFO)."""
    where = on_disk(title, year)
    if where:
        logger.info('Movie grab skipped: %s (%s) %s (%s)', title, year, ON_DISK, where)
        return ON_DISK
    q = in_qbit(title, year, session_factory, now)
    if q is None:
        logger.info('Movie grab skipped: %s (%s): %s; retried later', title, year, NO_QBIT)
        return NO_QBIT
    if q['hit']:
        logger.info('Movie grab skipped: %s (%s) %s (%s)', title, year, IN_QBIT, q['hit'])
        return IN_QBIT
    return None
