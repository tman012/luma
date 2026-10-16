"""Access mode: who may use Luma without signing in.

Modes (setting ``security.access_mode``):

* ``pin_remote``  "Disabled for local addresses" (default for a new install):
                  private/LAN addresses are open, anyone else needs the PIN.
* ``pin``         "Always": everyone signs in.
* ``open``        "Never": no sign-in at all.

Forwarded-for headers are believed only from ``security.trusted_proxies``.

An install that already has users but no saved mode (every install created
before the Start flow) is treated as ``pin`` so its sign-in behavior doesn't
change. An install with no users has no owner, so nothing is open until the
Start flow creates one.

Home WAN IP tracking: when accessed via Cloudflare tunnel from the home WAN IP,
requests are treated as local (included in pin_remote exemption).
"""

import ipaddress
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

logger = logging.getLogger(__name__)

MODES = ('open', 'pin_remote', 'pin')

# Home WAN IP tracking (IP -> timestamp dict)
_home_ips: Dict[str, float] = {}
_home_lock = threading.Lock()
_HOME_MAX_SIZE = 10
_settings = None
_state_path: Optional[Path] = None

# Headers a proxy or tunnel adds. Their values are trusted only from a
# configured trusted proxy; from anyone else their mere presence means the
# request was relayed, so it is treated as remote (spoofing one can only make
# a request *less* trusted).
_FORWARD_HEADERS = ('X-Forwarded-For', 'X-Real-IP', 'Forwarded', 'CF-Connecting-IP', 'CF-Ray', 'True-Client-IP')


def mode(settings) -> str:
    value = (settings.get('security.access_mode') or '').strip()
    return value if value in MODES else 'pin'


def _addr(ip: str):
    try:
        a = ipaddress.ip_address((ip or '').strip().strip('[]'))
    except ValueError:
        return None
    return getattr(a, 'ipv4_mapped', None) or a


# What counts as a local address. Listed explicitly: ipaddress.is_private
# also covers documentation and other special ranges that aren't a LAN.
_LOCAL_NETS = [ipaddress.ip_network(n) for n in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',   # RFC 1918
    '127.0.0.0/8', '169.254.0.0/16',                   # loopback, link-local
    '::1/128', 'fc00::/7', 'fe80::/10',                # IPv6 loopback, ULA, link-local
)]


def _is_private(ip: str) -> bool:
    a = _addr(ip)
    return bool(a and any(a.version == n.version and a in n for n in _LOCAL_NETS))


def trusted_proxies(settings) -> List[ipaddress._BaseNetwork]:
    raw = settings.get('security.trusted_proxies') or ''
    nets = []
    for part in str(raw).replace(';', ',').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return nets


def _in(nets, ip: str) -> bool:
    a = _addr(ip)
    return bool(a and any(a.version == n.version and a in n for n in nets))


def client_ip(request, settings=None) -> Optional[str]:
    """The real client address, or None when a request was relayed by a proxy
    that isn't trusted (its forwarded headers can't be believed).
    """
    peer = request.remote_addr or ''
    nets = trusted_proxies(settings) if settings is not None else []
    if nets and _in(nets, peer):
        cf = request.headers.get('CF-Connecting-IP', '').strip()
        if cf:
            return cf
        hops = [h.strip() for h in request.headers.get('X-Forwarded-For', '').split(',') if h.strip()]
        # Walk back from the nearest hop past any further trusted proxies.
        while hops and _in(nets, hops[-1]):
            hops.pop()
        if hops:
            return hops[-1]
        real = request.headers.get('X-Real-IP', '').strip()
        return real or peer
    if any(request.headers.get(h) for h in _FORWARD_HEADERS):
        return None
    return peer


def _get_cf_ip(request, settings) -> Optional[str]:
    """Get CF-Connecting-IP, but only trust it from configured trusted proxies."""
    peer = request.remote_addr or ''
    nets = trusted_proxies(settings) if settings else []
    if not (nets and _in(nets, peer)):
        return None
    return request.headers.get('CF-Connecting-IP', '').strip() or None


def is_local_request(request, settings=None) -> bool:
    """True when the request originates from a local/private IP or home WAN via tunnel.
    
    Checks:
    1. Direct connection from private IP (LAN, loopback)
    2. Cloudflare tunnel from home WAN IP (tracked dynamically)
    """
    ip = client_ip(request, settings)
    if ip and _is_private(ip):
        return True
    
    # Check if via Cloudflare tunnel from home WAN IP
    cf_ip = _get_cf_ip(request, settings)
    if cf_ip:
        with _home_lock:
            # _home_ips is now a dict, check if IP is a key
            if cf_ip in _home_ips:
                return True
    
    return False


def is_portal_traffic(request, settings) -> bool:
    """Detect if request is portal traffic (via dedicated hostname only).
    
    Portal traffic is detected ONLY by Host header matching request_portal.hostname.
    Owner can use the full app remotely via tunnel on the main hostname.
    """
    portal_hostname = (settings.get('request_portal.hostname') or '').strip().lower()
    if portal_hostname:
        request_host = (request.headers.get('Host') or '').strip().lower()
        # Remove port if present
        request_host = request_host.split(':')[0]
        if request_host == portal_hostname:
            return True
    
    return False


def should_authenticate(settings, request) -> bool:
    """True when this request must authenticate (new auth_required logic).
    
    Uses security.auth_required instead of legacy access_mode:
    - 'enabled': everyone must authenticate
    - 'disabled_local': local requests get implicit owner, remote must authenticate
    
    Falls back to legacy access_mode for backwards compatibility:
    - 'pin': everyone must authenticate (maps to 'enabled')
    - 'pin_remote': local gets implicit owner, remote must authenticate (maps to 'disabled_local')
    - 'open': local gets implicit owner, remote must authenticate (maps to 'disabled_local')
    
    Default (when neither setting is present): 'enabled' (secure default, everyone must authenticate)
    
    Portal traffic always requires authentication (never gets implicit owner).
    """
    # Portal traffic never gets implicit owner
    if is_portal_traffic(request, settings):
        return True
    
    # Try new auth_required setting first
    auth_req = settings.get('security.auth_required', '').strip()
    
    # Fall back to legacy access_mode for backwards compatibility
    if not auth_req:
        legacy_mode = settings.get('security.access_mode', '').strip()
        if legacy_mode == 'pin':
            auth_req = 'enabled'
        elif legacy_mode in ('pin_remote', 'open'):
            auth_req = 'disabled_local'
        else:
            # Default to 'enabled' (secure default - everyone must authenticate)
            # This matches the old default behavior of 'pin' mode
            auth_req = 'enabled'
    
    if auth_req == 'enabled':
        # Everyone must authenticate
        return True
    elif auth_req == 'disabled_local':
        # Local gets implicit owner, remote must authenticate
        return not is_local_request(request, settings)
    else:
        # Unknown value, default to safe (require auth)
        return True


def needs_pin(settings, request) -> bool:
    """Backward compatibility wrapper for should_authenticate.
    
    DEPRECATED: Use should_authenticate() instead.
    """
    return should_authenticate(settings, request)


def owner(user_auth) -> Optional[Dict]:
    """The install owner: the first active admin account."""
    for u in user_auth.list_users():
        if u.get('is_admin') and (u.get('status') or 'active') == 'active':
            return u
    return None


# ---------------------------------------------------------------------------
# Home WAN IP tracking
# ---------------------------------------------------------------------------

def _lookup_wan_ip() -> Optional[str]:
    """Look up the current WAN IP via Cloudflare trace endpoints."""
    # Try 1.1.1.1 first
    try:
        resp = requests.get('https://1.1.1.1/cdn-cgi/trace', timeout=5)
        for line in resp.text.splitlines():
            if line.startswith('ip='):
                return line.split('=', 1)[1].strip()
    except Exception:
        pass
    
    # Fallback to cloudflare.com
    try:
        resp = requests.get('https://www.cloudflare.com/cdn-cgi/trace', timeout=5)
        for line in resp.text.splitlines():
            if line.startswith('ip='):
                return line.split('=', 1)[1].strip()
    except Exception:
        pass
    
    return None


def _refresh_home_wan_ip() -> None:
    """Refresh the home WAN IP in background thread."""
    ip = _lookup_wan_ip()
    if not ip:
        return
    
    now = time.time()
    with _home_lock:
        # Update timestamp for this IP
        _home_ips[ip] = now
        
        # Evict oldest IPs if over limit
        if len(_home_ips) > _HOME_MAX_SIZE:
            # Sort by timestamp and keep only the most recent
            sorted_ips = sorted(_home_ips.items(), key=lambda x: x[1], reverse=True)
            _home_ips.clear()
            _home_ips.update(dict(sorted_ips[:_HOME_MAX_SIZE]))
            logger.info('[access] evicted old home WAN IPs, now tracking %d', len(_home_ips))
        
        logger.info('[access] home WAN IP updated: %s (tracking %d)', ip, len(_home_ips))
    
    _save_home_ips()


def _periodic_refresh() -> None:
    """Background thread that refreshes home WAN IP every 10 minutes."""
    while True:
        time.sleep(600)  # 10 minutes
        _refresh_home_wan_ip()


def _load_home_ips() -> None:
    """Load persisted home IPs with timestamps from state file."""
    if not _state_path or not _state_path.exists():
        return
    try:
        data = json.loads(_state_path.read_text())
        with _home_lock:
            _home_ips.clear()
            # Load dict of IP -> timestamp
            home_data = data.get('home_ips', {})
            if isinstance(home_data, dict):
                _home_ips.update(home_data)
            elif isinstance(home_data, list):
                # Legacy format: list of IPs without timestamps
                now = time.time()
                _home_ips.update({ip: now for ip in home_data})
        logger.info('[access] loaded %d home IPs', len(_home_ips))
    except Exception:
        logger.warning('[access] failed to load home IPs', exc_info=True)


def _save_home_ips() -> None:
    """Persist home IPs with timestamps to state file."""
    if not _state_path:
        return
    try:
        with _home_lock:
            data = {'home_ips': dict(_home_ips)}
        _state_path.write_text(json.dumps(data, indent=2))
    except Exception:
        logger.warning('[access] failed to save home IPs', exc_info=True)


def init_home_wan_tracking(settings) -> None:
    """Initialize home WAN IP tracking with background refresh."""
    global _settings, _state_path
    _settings = settings
    
    # Determine state file path
    try:
        data_dir = Path(settings.path).parent
        _state_path = data_dir / 'home_wan_ips.json'
    except Exception:
        logger.warning('[access] could not determine state path for home IPs')
        _state_path = None
    
    # Load existing state
    _load_home_ips()
    
    # Check if background threads should be skipped
    skip_bg = os.getenv('LUMA_SKIP_BACKGROUND_THREADS', '').strip().lower() in ('1', 'true', 'yes')
    if not skip_bg:
        skip_bg = settings.get('general.skip_background_threads', False)
    
    # Start background refresh thread (skip in test mode)
    if not skip_bg:
        def _background_refresh():
            time.sleep(2)  # Small startup delay
            _refresh_home_wan_ip()
            _periodic_refresh()
        
        threading.Thread(target=_background_refresh, name='home-wan-refresh', daemon=True).start()
    
    logger.info('[access] home WAN tracking initialized (%d IPs known)', len(_home_ips))
