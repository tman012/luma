"""Which Luma this is, and what it may change.

LUMA_INSTANCE=test (the test install sharing production's Radarr, Sonarr and
qBittorrent): every job that grabs, moves, renames, imports, unmonitors or
deletes is OFF unless opted in by name in LUMA_TEST_ALLOW (comma-separated,
default empty). Gated twice (defense in depth):
  1. job start: start_services() and each manual endpoint ask writes_allowed(job);
  2. the write call: install_write_guard() refuses every Radarr/Sonarr/Prowlarr
     write (POST/PUT/DELETE under /api/v1|v3) and every qBittorrent torrent
     write (/api/v2/torrents/<action> other than reads) unless 'arr' / 'qbit'
     is opted in.
The library mover is the one writer allowed on test by default: it acts only on
torrents tagged luma-test (qbit_owner) and only hardlinks or copies (never
moves, renames or deletes a source file). TV tv.db scan/link is DB-only and
always allowed. Unset or 'prod': nothing changes.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Dict, FrozenSet, Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

ENV = 'LUMA_INSTANCE'
ALLOW_ENV = 'LUMA_TEST_ALLOW'

# job -> what it does (the gated list, reported at boot)
GATED: Dict[str, str] = {
    'auto_rename': 'auto-rename monitor loop (renames, moves, deletes downloads)',
    'daily_missing': "What You're Missing (adds to Radarr)",
    'downloads_guard': 'Downloads Guard (deletes/pauses torrents)',
    'franchise_completer': 'Franchise Completer (adds to Radarr)',
    'roulette': 'Genre Roulette (grabs)',
    'movie_night': 'Movie Night (grabs)',
    'anticipated_watchlist': 'Anticipated watchlist (adds, unmonitors)',
    'now_available_watchlist': 'Now available watchlist (grabs, unmonitors)',
    'trending_watchlist': 'Trending watchlist (adds to Radarr)',
    'trending_tv_watchlist': 'Trending TV watchlist (adds to Sonarr)',
    'latest_auto_grab': 'Latest auto-grab (grabs)',
    'upgrade_4k': '4K Remux auto-grab (grabs)',
    'upgrade_scheduler': 'Daily upgrade scheduler (grabs)',
    'movie_collections': 'Movie Collections refresher (adds to Radarr)',
    'watchlist_digital_dates': 'Watchlist digital dates (Radarr edits)',
    'arr': 'Radarr/Sonarr/Prowlarr write calls (add, delete, unmonitor, grab, command)',
    'qbit': 'qBittorrent write calls (add, delete, pause, category/tag change, move)',
    'storage_cleanup': 'Downloads cleanup (deletes files)',
}
# Allowed on test by default (scoped by their own rules, see the module doc).
TEST_DEFAULT_ALLOWED: FrozenSet[str] = frozenset({'mover', 'tv_link'})


class WritesBlocked(requests.RequestException):
    """A gated write on LUMA_INSTANCE=test (a RequestException, so callers' error paths handle it)."""


def name() -> str:
    return (os.environ.get(ENV) or 'prod').strip().casefold() or 'prod'


def is_test() -> bool:
    return name() == 'test'


def allowed() -> FrozenSet[str]:
    return frozenset(x.strip().casefold() for x in (os.environ.get(ALLOW_ENV) or '').split(',') if x.strip())


def writes_allowed(job: str) -> bool:
    """True on prod; on test only for the mover/TV link or a job named in LUMA_TEST_ALLOW."""
    if not is_test():
        return True
    j = job.casefold()
    return j in TEST_DEFAULT_ALLOWED or j in allowed()


def require(job: str) -> None:
    if not writes_allowed(job):
        logger.info('Instance test: %s blocked', job)
        raise WritesBlocked(f'{job} is off on the test instance')


def blocked_jobs() -> Dict[str, str]:
    return {j: d for j, d in GATED.items() if not writes_allowed(j)}


_logged = [False]


def log_blocked_once() -> None:
    if _logged[0] or not is_test():
        return
    _logged[0] = True
    for j, d in blocked_jobs().items():
        logger.info('Instance test: %s is OFF (%s); opt in with %s=%s', j, d, ALLOW_ENV, j)


# ----------------------------------------------------------------- the write-call gate

# qBittorrent reads (everything else under /api/v2/torrents/ is a write)
_QBIT_READS = frozenset({'info', 'properties', 'trackers', 'webseeds', 'files', 'piecestates', 'piecehashes',
                         'categories', 'tags', 'count'})
_QBIT = re.compile(r'/api/v2/torrents/([A-Za-z]+)/?$')
_ARR = re.compile(r'/api/v[13]/')
_READ_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


def write_kind(method: str, url: str) -> Optional[str]:
    """'qbit' / 'arr' when this request would change qBittorrent or an *arr app, else None."""
    path = urlsplit(url or '').path or ''
    m = _QBIT.search(path)
    if m:
        return None if m.group(1).casefold() in _QBIT_READS else 'qbit'
    if (method or 'GET').upper() in _READ_METHODS:
        return None
    if _ARR.search(path):
        return 'arr'
    return None


_installed = {'on': False}
_ilock = threading.Lock()


def install_write_guard() -> bool:
    """On test, wrap requests.Session.send so gated writes never leave the box. Idempotent."""
    if not is_test():
        return False
    with _ilock:
        if _installed['on']:
            return True
        orig = requests.Session.send

        def send(self, request, **kw):
            kind = write_kind(request.method, request.url)
            if kind and not writes_allowed(kind):
                logger.warning('Instance test: blocked %s %s (%s write)', request.method, urlsplit(request.url).path, kind)
                raise WritesBlocked(f'{kind} writes are off on the test instance')
            return orig(self, request, **kw)
        send._luma_orig = orig  # type: ignore[attr-defined]
        requests.Session.send = send
        _installed['on'] = True
        return True


def uninstall_write_guard() -> None:
    with _ilock:
        orig = getattr(requests.Session.send, '_luma_orig', None)
        if orig:
            requests.Session.send = orig
        _installed['on'] = False


# ----------------------------------------------------------------- manual endpoints

# POST/PUT/DELETE route prefix -> job
ENDPOINTS = (
    ('/api/movie-night/run', 'movie_night'), ('/api/genre-roulette/run', 'roulette'),
    ('/api/franchise-completer/run', 'franchise_completer'), ('/api/watchlist-digital-dates/run', 'watchlist_digital_dates'),
    ('/api/downloads-guard/run', 'downloads_guard'), ('/api/storage/downloads/cleanup', 'storage_cleanup'),
    ('/api/rails/what-youre-missing/refresh', 'daily_missing'), ('/api/collections/refresh', 'movie_collections'),
    ('/api/upgrades/auto-grab', 'upgrade_4k'), ('/api/upgrades/batch', 'arr'), ('/api/upgrades/scheduler/trigger', 'upgrade_scheduler'),
    ('/api/automation/trending-watchlist/run', 'trending_watchlist'),
    ('/api/automation/trending-tv-watchlist/run', 'trending_tv_watchlist'),
    ('/api/automation/now-available-watchlist/run', 'now_available_watchlist'),
    ('/api/automation/anticipated-watchlist/run', 'anticipated_watchlist'),
    ('/api/automation/latest-auto-grab/run', 'latest_auto_grab'),
    ('/api/automation/upgrade-4k-auto-grab/run', 'upgrade_4k'),
    ('/api/radarr/movie/grab', 'arr'), ('/api/sonarr/add', 'arr'), ('/api/sonarr/monitor/', 'arr'),
    ('/api/sonarr/episode/download', 'arr'), ('/api/sonarr/search/', 'arr'),
    ('/api/watchlist', 'arr'), ('/api/tv/watchlist', 'arr'),
    ('/api/downloads/add', 'qbit'), ('/api/downloads/remove/', 'qbit'),
)


def endpoint_job(method: str, path: str) -> Optional[str]:
    if (method or 'GET').upper() in _READ_METHODS:
        return None
    for prefix, job in ENDPOINTS:
        if path == prefix or (prefix.endswith('/') and path.startswith(prefix)) or path.startswith(prefix + '/'):
            return job
    return None


def init_app(app) -> None:
    """Boot: install the write gate, log the blocked jobs once, refuse gated manual endpoints (403)."""
    if not is_test():
        return
    install_write_guard()
    log_blocked_once()
    from flask import jsonify, request

    @app.before_request
    def _instance_gate():
        job = endpoint_job(request.method, request.path)
        if job and not writes_allowed(job):
            logger.info('Instance test: %s %s refused (%s)', request.method, request.path, job)
            return jsonify({'error': 'Off on the test instance', 'job': job}), 403
        return None
