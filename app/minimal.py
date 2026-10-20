"""LUMA_MINIMAL=1: a fresh-install test build with only the core turned on.

With the flag set, Luma runs Get Started, sign-in and the main screen only.
No background threads start (caches, automations, schedulers, tunnel), and
every other route answers 404. Nothing is removed: with the flag unset,
Luma behaves exactly as before. The flag is read at call time.
"""

from __future__ import annotations

import os

_TRUE = ('1', 'true', 'yes', 'on')

# Pages and API paths that stay on in minimal mode.
ALLOWED_PATHS = frozenset({
    '/', '/start', '/login', '/logout', '/unlock', '/health',
    '/manifest.json', '/sw.js', '/api/security', '/api/version',
    # Home (#11): rails, the Libraries choice and proxied posters. Sign-in required.
    '/api/home', '/api/home/libraries', '/api/img/plex', '/settings', '/settings/libraries', '/settings/home-screen', '/settings/plex',
    '/api/settings/plex', '/api/settings/plex/pin', '/api/settings/plex/server',
    # Web kiosk home (#12, >=1024px): prod's hero slideshow data. Sign-in required.
    '/api/hero/content', '/api/home/kiosk',
    # Search (prod's typo-tolerant library + TMDB search). Sign-in required.
    '/search', '/api/library/search',
    # Settings > Connectors (owner only): the TMDB key.
    '/settings/connectors', '/api/settings/connectors', '/api/settings/connectors/tmdb',
    '/api/settings/connectors/tmdb/test',
    # Settings > Connectors: Trakt (its rails join Home's list once connected).
    '/api/settings/connectors/trakt', '/api/settings/connectors/trakt/test',
    # Settings > Connectors: TheTVDB (Calendar air times).
    '/api/settings/connectors/tvdb', '/api/settings/connectors/tvdb/test',
    # Settings > Connectors: qBittorrent (the download path's settings section).
    '/api/settings/connectors/qbittorrent', '/api/settings/connectors/qbittorrent/test',
    # Search and the detail popup: TMDB images through prod's proxy (works when
    # the browser can't reach image.tmdb.org) and the read-only ownership check.
    '/api/img/tmdb', '/api/check-ownership-batch',
    # Prowlarr port (#16, PR2, PR3): Settings > Trackers and the popup's
    # Sources with grab. Tracker use needs a real sign-in (owner-only for
    # Settings > Trackers); the routes check that themselves.
    '/api/trackers', '/api/sources/all-results', '/api/sources/config',
    '/api/downloads/add', '/api/tmdb/match-search', '/settings/trackers',
    # Settings > Library paths (owner only; the routes check that themselves).
    '/api/settings/library-paths', '/api/settings/library-rules',
    # TV settings (owner only; the route checks).
    '/api/settings/tv',
    # v198 Settings > Movies (owner only; the routes check).
    '/api/settings/movies',
    # v202 the native movie watchlist (test instance only; the routes check).
    '/api/movies/watchlist',
})
ALLOWED_PREFIXES = ('/api/auth/', '/api/start/', '/static/', '/assets/', '/fonts/', '/settings/',
                    '/api/trackers/', '/api/settings/library-paths/',
                    # Per-section tracker lists (owner only; the routes check).
                    '/api/settings/sections/',
                    # TV library paths, scan, shows and fix-match (owner only; the routes check).
                    '/api/settings/tv/', '/api/settings/movies/', '/api/movies/watchlist/',
                    # v216: the file-info sheet's Fix Match (admin only; the routes check).
                    '/api/plex/match/')
# Read-only data for main's movie/TV detail popup (tapping a Home card).
# GET only, sign-in required like everything else in minimal mode.
DETAIL_READ_PREFIXES = (
    '/api/plex/movie/', '/api/plex/tv/', '/api/movie/', '/api/tv/',
    '/api/plex/ratings/', '/api/plex/ultrablur/', '/api/tmdb/colors/',
    '/api/clearlogo/', '/api/plex/lookup', '/api/plex/episode/',
    '/api/tmdb/person_by_id/',
    # v216: the wide popup's file-info (i) sheet, prod 1b4ea74's (signed-in; the route checks).
    '/api/plex/file-info/',
)
# v180: prod's library ownership (Plex match) on test. These are the routes the
# owned Play/Watch and Home's owned orbs read; every one was already on above
# (no new allowance). All are reads: GET, plus the batch check's POST body query.
# tests/test_v180_ownership_routes.py keeps them reads and off the gated list.
OWNERSHIP_READ = (
    ('GET', '/api/plex/lookup'),
    ('POST', '/api/check-ownership-batch'),
    ('GET', '/api/plex/movie/<int:rating_key>'),
    ('GET', '/api/plex/tv/<int:rating_key>'),
    ('GET', '/api/plex/tv/<int:rating_key>/seasons'),
    ('GET', '/api/tv/ownership/<int:tvdb_id>/cached'),
)
# Inside /static/, these belong to disabled features.
BLOCKED_PREFIXES = ('/static/request/', '/static/hero_cache/')
# Under /api/auth/, these belong to disabled features (no self sign-up).
BLOCKED_PATHS = frozenset({'/api/auth/register'})
# Reachable signed out (minimal mode signs in every address, LAN included).
SIGNED_OUT_PATHS = frozenset({'/', '/start', '/unlock'})
SIGNED_OUT_PREFIXES = ('/api/start/', '/assets/', '/fonts/')


def enabled() -> bool:
    return os.environ.get('LUMA_MINIMAL', '').strip().lower() in _TRUE


def path_allowed(path: str, method: str = 'GET') -> bool:
    path = path or '/'
    if path.startswith(DETAIL_READ_PREFIXES):
        return method in ('GET', 'HEAD') and '..' not in path
    if len(path) > 1 and path.endswith('/') and path.rstrip('/') in ALLOWED_PATHS:
        path = path.rstrip('/')
    if path in ALLOWED_PATHS:
        return True
    if path.rstrip('/') in BLOCKED_PATHS or path.startswith(BLOCKED_PREFIXES):
        return False
    return path.startswith(ALLOWED_PREFIXES)
