"""Remote lock: visitors arriving through the Cloudflare tunnel from outside the
home network need a Luma session (or the service key) for everything except
sign-in, the request page and the static files those need.

Classification (cloudflared and Caddy both connect from loopback):
  local   peer is private/loopback and there are no Cloudflare headers
          (LAN browsers via Caddy, kiosks on the LAN IP, webhooks, sports-hub,
          Alfred, automations)                                   -> unchanged
  home    via the tunnel, but CF-Connecting-IP is the home WAN IP
          (home devices that use tflixapp.com, e.g. the kiosk app) -> unchanged
  remote  via the tunnel from anywhere else, or a public peer      -> locked

Mode (settings security.remote_lock): enforce | report | off.
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
_STATE = '/app/data/remote_lock.json'

_OPEN_API = {
    '/api/auth/login', '/api/auth/me', '/api/auth/check-user', '/api/auth/logout', '/api/auth/register',
    '/api/admin/telegram-action',   # HMAC-signed approve/deny links
    '/api/version', '/api/_appdiag',
}
_OPEN_API_PREFIX = ('/api/request/',)
_LOCKED_PAGES = ('/search', '/legacy/', '/browse_libraries', '/browse_media', '/activity')


def mode() -> str:
    m = (_settings.get('security.remote_lock') if _settings else '') or 'enforce'
    return m if m in ('enforce', 'report', 'off') else 'enforce'


def _private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def _cf_ip() -> str:
    return (request.headers.get('CF-Connecting-IP') or '').strip()


def classify() -> str:
    peer = request.remote_addr or ''
    via_cf = bool(_cf_ip() or request.headers.get('Cf-Ray'))
    if _private(peer):
        if not via_cf:
            return 'local'
        with _home_lock:
            if _cf_ip() in _home:
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
    ip = _lookup()
    if not ip:
        return
    with _home_lock:
        if ip not in _home:
            logger.info('[remote-lock] home address updated')
        _home.add(ip)
        while len(_home) > 3:
            _home.pop()
        keep = sorted(_home)
    try:
        with open(_STATE, 'w') as f:
            json.dump({'home': keep, 'ts': time.time()}, f)
    except OSError:
        pass


def _loop() -> None:
    while True:
        time.sleep(600)
        _refresh()


def init_app(app, settings) -> None:
    global _settings
    _settings = settings
    try:
        with open(_STATE) as f:
            _home.update(json.load(f).get('home') or [])
    except (OSError, ValueError):
        pass
    _refresh()
    threading.Thread(target=_loop, name='remote-lock-home-ip', daemon=True).start()
    app.before_request(guard)
    logger.info('[remote-lock] mode=%s, home address known=%s', mode(), bool(_home))
