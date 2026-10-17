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
})
ALLOWED_PREFIXES = ('/api/auth/', '/api/start/', '/static/', '/assets/', '/fonts/')
# Inside /static/, these belong to disabled features.
BLOCKED_PREFIXES = ('/static/request/', '/static/hero_cache/')
# Under /api/auth/, these belong to disabled features (no self sign-up).
BLOCKED_PATHS = frozenset({'/api/auth/register'})
# Reachable signed out (minimal mode signs in every address, LAN included).
SIGNED_OUT_PATHS = frozenset({'/', '/start', '/unlock'})
SIGNED_OUT_PREFIXES = ('/api/start/', '/assets/', '/fonts/')


def enabled() -> bool:
    return os.environ.get('LUMA_MINIMAL', '').strip().lower() in _TRUE


def path_allowed(path: str) -> bool:
    path = path or '/'
    if len(path) > 1 and path.endswith('/') and path.rstrip('/') in ALLOWED_PATHS:
        path = path.rstrip('/')
    if path in ALLOWED_PATHS:
        return True
    if path.rstrip('/') in BLOCKED_PATHS or path.startswith(BLOCKED_PREFIXES):
        return False
    return path.startswith(ALLOWED_PREFIXES)
