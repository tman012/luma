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
    '/api/home', '/api/home/libraries', '/api/img/plex', '/settings/libraries',
    # Web kiosk home (#12, >=1024px): prod's hero slideshow data. Sign-in required.
    '/api/hero/content',
})
ALLOWED_PREFIXES = ('/api/auth/', '/api/start/', '/static/', '/assets/', '/fonts/')
# Read-only data for main's movie/TV detail popup (tapping a Home card).
# GET only, sign-in required like everything else in minimal mode.
DETAIL_READ_PREFIXES = (
    '/api/plex/movie/', '/api/plex/tv/', '/api/movie/', '/api/tv/',
    '/api/plex/ratings/', '/api/plex/ultrablur/', '/api/tmdb/colors/',
    '/api/clearlogo/', '/api/ratings/', '/api/rt/reviews', '/api/plex/lookup',
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
