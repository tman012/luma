"""Access control: who may use Luma without signing in.

Authentication is controlled by security.auth_required:
- 'disabled_local': local requests (private IP or via trusted proxy) get implicit owner access
- 'enabled': everyone must authenticate

X-Forwarded-For headers are trusted only from security.trusted_proxies.
An optional security.trusted_proxy_header setting specifies a custom header to read
(e.g., 'X-Real-IP'); defaults to standard XFF behavior.
"""

import ipaddress
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# Headers a proxy or tunnel adds. Their presence from an untrusted source means
# the request was relayed, so it is treated as remote (spoofing one can only make
# a request *less* trusted).
_FORWARD_HEADERS = ('X-Forwarded-For', 'X-Real-IP', 'Forwarded')


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
        # Check for custom trusted_proxy_header setting
        custom_header = (settings.get('security.trusted_proxy_header') or '').strip() if settings else ''
        if custom_header:
            # Use custom header if configured
            custom_value = request.headers.get(custom_header, '').strip()
            if custom_value:
                return custom_value
        
        # Standard XFF behavior
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


def is_local_request(request, settings=None) -> bool:
    """True when the request originates from a local/private IP.
    
    Checks:
    1. Direct connection from private IP (LAN, loopback)
    2. Connection via trusted proxy from private IP (using X-Forwarded-For)
    """
    ip = client_ip(request, settings)
    return bool(ip and _is_private(ip))


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
    
    Uses security.auth_required (migrated from legacy access_mode):
    - 'enabled': everyone must authenticate
    - 'disabled_local': local requests get implicit owner, remote must authenticate
    
    Default: 'disabled_local' (LAN and kiosk don't need sign-in)
    
    Portal traffic always requires authentication (never gets implicit owner).
    """
    # Portal traffic never gets implicit owner
    if is_portal_traffic(request, settings):
        return True
    
    # Read auth_required setting (legacy access_mode is migrated at settings load)
    auth_req = (settings.get('security.auth_required') or 'disabled_local').strip()
    
    if auth_req == 'enabled':
        # Everyone must authenticate
        return True
    elif auth_req == 'disabled_local':
        # Local gets implicit owner, remote must authenticate
        return not is_local_request(request, settings)
    else:
        # Unknown value, default to safe (require auth)
        return True


def owner(user_auth) -> Optional[dict]:
    """The install owner: the first active admin account."""
    for u in user_auth.list_users():
        if u.get('is_admin') and (u.get('status') or 'active') == 'active':
            return u
    return None
