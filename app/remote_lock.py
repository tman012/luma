"""Remote lock: visitors arriving through the Cloudflare tunnel from outside the
home network need a Luma session (or the service key) for everything except
sign-in, the request page and the static files those need.

Classification (cloudflared and Caddy both connect from loopback):
  local   peer is private/loopback and there are no Cloudflare headers
          (LAN browsers via Caddy, kiosks on the LAN IP, webhooks, automations) -> unchanged
  home    via the tunnel, but CF-Connecting-IP is the home WAN IP
          (home devices using the public domain) -> unchanged
  remote  via the tunnel from anywhere else, or a public peer -> locked

Mode (settings security.remote_lock): enforce | report | off.
Defaults to 'report' for new installs (log-only, non-blocking).

INTEGRATION WITH ACCESS_MODE (start-flow):
- remote_lock applies AFTER access_mode's authentication check
- If access_mode='open', remote_lock still runs (remote_lock can lock down
  an otherwise-open install when accessed via tunnel from outside)
- If access_mode='pin_remote', it already requires auth for remote IPs,
  so remote_lock's 'enforce' mode provides redundant protection
- remote_lock classification is independent: it focuses on tunnel/CF headers,
  while access_mode uses IP privacy checks
"""
from __future__ import annotations

import ipaddress
import json
import logging
import threading
import time
from typing import Optional, Set

import requests
from flask import g, jsonify, redirect, request

logger = logging.getLogger(__name__)

_settings = None
_home: Set[str] = set()
_home_lock = threading.Lock()
_HOME_MAX_SIZE = 10  # Keep only the 10 most recent home IPs
_state_path = None  # Set during init_app

_OPEN_API = {
    '/api/auth/login', '/api/auth/me', '/api/auth/check-user', '/api/auth/logout', '/api/auth/register',
    '/api/admin/telegram-action',   # HMAC-signed approve/deny links
    '/api/version', '/api/_appdiag',
}
_OPEN_API_PREFIX = ('/api/request/',)
_LOCKED_PAGES = ('/search', '/legacy/', '/browse_libraries', '/browse_media', '/activity')


def mode() -> str:
    """Get remote_lock mode from settings. Defaults to 'report' (log-only)."""
    m = (_settings.get('security.remote_lock') if _settings else '') or 'report'
    return m if m in ('enforce', 'report', 'off') else 'report'


def _private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def _cf_ip() -> str:
    """Get CF-Connecting-IP header, but only trust it from configured trusted proxies."""
    from app import access
    # Only trust CF headers if the peer is a configured trusted proxy
    peer = request.remote_addr or ''
    nets = access.trusted_proxies(_settings) if _settings else []
    if not (nets and access._in(nets, peer)):
        return ''
    return (request.headers.get('CF-Connecting-IP') or '').strip()


def classify() -> str:
    """Classify the current request as 'local', 'home', or 'remote'."""
    peer = request.remote_addr or ''
    via_cf = bool(_cf_ip() or request.headers.get('Cf-Ray'))
    if _private(peer):
        if not via_cf:
            return 'local'
        cf_ip = _cf_ip()
        with _home_lock:
            # Check if IP is in home set (stored as (ip, timestamp) tuples)
            if any(ip == cf_ip for ip, _ in _home):
                return 'home'
        return 'remote'
    return 'remote'


def _allowed(path: str, method: str) -> bool:
    if path.startswith('/api/'):
        return path in _OPEN_API or path.startswith(_OPEN_API_PREFIX)
    if method not in ('GET', 'HEAD', 'OPTIONS'):
        return False
    return not any(path == p.rstrip('/') or path.startswith(p) for p in _LOCKED_PAGES)


def guard():
    """Remote lock guard runs AFTER access_mode authentication.
    
    This provides an additional layer: even if access_mode='open' (no PIN required),
    remote_lock can still require authentication for traffic via tunnel from outside.
    If a user is already authenticated (by access_mode or otherwise), remote_lock
    allows them through.
    """
    m = mode()
    if m == 'off' or getattr(g, 'current_user', None):
        return None
    path, method = request.path, request.method
    if method == 'OPTIONS' or _allowed(path, method):
        return None
    if classify() != 'remote':
        return None
    if m == 'report':
        logger.info('[remote-lock] would block %s %s cf=%s ua=%s', method, path, _cf_ip(),
                    (request.headers.get('User-Agent') or '')[:60])
        return None
    if path.startswith('/api/') or method != 'GET':
        return jsonify({'error': 'Authentication required'}), 401
    return redirect('/login')


# --- home WAN IP -----------------------------------------------------------

def _lookup() -> Optional[str]:
    """Discover public WAN IP via Cloudflare's trace endpoint."""
    for url in ('https://1.1.1.1/cdn-cgi/trace', 'https://www.cloudflare.com/cdn-cgi/trace'):
        try:
            r = requests.get(url, timeout=5)
            for line in r.text.splitlines():
                if line.startswith('ip='):
                    return line[3:].strip()
        except requests.RequestException:
            continue
    return None


def _refresh() -> None:
    """Look up current home WAN IP and add it to the home set with LRU eviction."""
    ip = _lookup()
    if not ip:
        return
    now = time.time()
    with _home_lock:
        # Check if IP already exists (update timestamp)
        existing = [item for item in _home if item[0] == ip]
        if existing:
            _home.discard(existing[0])
        else:
            logger.info('[remote-lock] home address updated: %s', ip)
        
        # Add with current timestamp
        _home.add((ip, now))
        
        # Evict oldest IPs if over limit (keep most recent)
        if len(_home) > _HOME_MAX_SIZE:
            # Sort by timestamp, keep the most recent
            sorted_ips = sorted(_home, key=lambda x: x[1], reverse=True)
            _home.clear()
            _home.update(sorted_ips[:_HOME_MAX_SIZE])
    
    _save()


def _loop() -> None:
    """Background thread to refresh home WAN IP every 10 minutes."""
    while True:
        time.sleep(600)
        _refresh()


def init_app(app, settings) -> None:
    """Initialize remote lock with settings and start background refresh thread."""
    global _settings, _state_path
    _settings = settings
    
    # Determine state file path from settings data directory
    try:
        from pathlib import Path
        data_dir = Path(settings.path).parent
        _state_path = data_dir / 'remote_lock.json'
    except Exception:
        logger.warning('remote_lock: could not determine state path, persistence disabled')
        _state_path = None
    
    # Load existing state
    _load()
    
    # Start background refresh thread (non-blocking)
    def _background_refresh():
        time.sleep(2)  # Small delay to not block startup
        _refresh()
        _loop()
    
    threading.Thread(target=_background_refresh, name='remote-lock-home-ip', daemon=True).start()
    
    # Register guard
    app.before_request(guard)
    
    logger.info('[remote-lock] mode=%s, home IPs known=%d', mode(), len(_home))
