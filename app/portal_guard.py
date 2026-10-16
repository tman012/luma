"""Portal traffic guard - restricts portal traffic to portal routes only.

Portal traffic (from the internet via Cloudflare tunnel) can only access:
- /request and its static assets
- /api/request/* (excluding /api/request/admin/*)

All other routes return 404 for portal traffic.
"""
import logging
from flask import request, abort

logger = logging.getLogger(__name__)

# Portal-accessible routes (allowlist)
_PORTAL_PATHS = {
    '/request',
    '/api/request/unlock',
    '/api/request/search',
    '/api/request/tmdb',
    '/api/request/library',
    '/api/request/submit',
    '/api/request/status',
}

_PORTAL_PREFIXES = (
    '/static/request/',
)


def is_portal_allowed(path: str) -> bool:
    """Check if path is allowed for portal traffic (deny-by-default allowlist)."""
    # Exact path matches
    if path in _PORTAL_PATHS:
        return True
    
    # Prefix matches
    if any(path.startswith(prefix) for prefix in _PORTAL_PREFIXES):
        return True
    
    # /api/request/* but NOT /api/request/admin/*
    if path.startswith('/api/request/'):
        if path.startswith('/api/request/admin/'):
            return False
        return True
    
    return False


def guard_portal_traffic(settings):
    """Before-request guard: restrict portal traffic to portal routes only."""
    from app import access
    
    # Check if this is portal traffic
    is_portal = access.is_portal_traffic(request, settings)
    path = request.path
    print(f'[portal-guard-DEBUG] path={path}, host={request.headers.get("Host")}, is_portal={is_portal}')
    
    if not is_portal:
        return None
    
    # Check if path is allowed for portal traffic
    allowed = is_portal_allowed(path)
    print(f'[portal-guard-DEBUG] portal traffic to {path}: allowed={allowed}')
    
    if not allowed:
        # Portal traffic trying to access non-portal route
        logger.warning('[portal-guard] Portal traffic blocked from %s (host=%s, cf-ray=%s)',
                      path, request.headers.get('Host'), request.headers.get('CF-Ray'))
        abort(404)
    
    return None
