"""Portal traffic guard - restricts portal traffic to portal routes only.

Portal traffic (detected via request_portal.hostname) can only access:
- /request and /request/ (portal page)
- /static/request/* (portal static assets)
- Specific /api/request/* endpoints (explicit allowlist, no wildcard)

All other routes return 404 for portal traffic.
"""
import logging
from flask import request, abort

logger = logging.getLogger(__name__)

# Portal-accessible routes (explicit allowlist)
_PORTAL_PATHS = {
    '/request',
    '/request/',
    # Public request portal API endpoints
    '/api/request/session',
    '/api/request/unlock',
    '/api/request/signout',
    # Requester API endpoints (after unlock)
    '/api/request/search',
    '/api/request/discover',
    '/api/request/home',
    '/api/request/recent',
    '/api/request/progress',
}

# Portal-accessible path patterns (for dynamic routes)
_PORTAL_PATH_PATTERNS = [
    # Movie/TV detail and requests
    ('/api/request/movie/', ['GET', 'POST']),  # /api/request/movie/<id>
    ('/api/request/tv/', ['GET', 'POST']),     # /api/request/tv/<id> and /api/request/tv/<id>/season/<season>
    # Additional metadata endpoints
    ('/api/request/look/', ['GET']),           # /api/request/look/<kind>/<id>
    ('/api/request/scores/', ['GET']),         # /api/request/scores/<kind>/<id>
    ('/api/request/extras/', ['GET']),         # /api/request/extras/<kind>/<id>
]

_PORTAL_STATIC_PREFIX = '/static/request/'


def is_portal_allowed(path: str, method: str = 'GET') -> bool:
    """Check if path is allowed for portal traffic (deny-by-default allowlist)."""
    # Exact path matches
    if path in _PORTAL_PATHS:
        return True
    
    # Static assets
    if path.startswith(_PORTAL_STATIC_PREFIX):
        return True
    
    # Pattern matches with method check
    for pattern_prefix, allowed_methods in _PORTAL_PATH_PATTERNS:
        if path.startswith(pattern_prefix):
            # Extra check: block admin routes even if they match a pattern
            if '/admin/' in path or path.startswith('/api/request/admin'):
                return False
            return method in allowed_methods
    
    return False


def guard_portal_traffic(settings):
    """Before-request guard: restrict portal traffic to portal routes only."""
    from app import access
    
    # Check if this is portal traffic
    if not access.is_portal_traffic(request, settings):
        return None
    
    # Check if path is allowed for portal traffic
    path = request.path
    method = request.method
    
    if not is_portal_allowed(path, method):
        # Portal traffic trying to access non-portal route
        logger.warning('[portal-guard] Portal traffic blocked from %s %s (host=%s)',
                      method, path, request.headers.get('Host'))
        abort(404)
    
    return None
