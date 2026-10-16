"""Access control: who may use Luma without signing in.

security.auth_required decides:
- 'disabled_local' (default, also when nothing is saved): requests from a LAN
  address get implicit owner access; everyone else must sign in.
- 'enabled': everyone must sign in.

"Local" means the client address is private (RFC 1918, loopback, link-local,
IPv6 ULA). The client address is the TCP peer, or, only when that peer is in
security.trusted_proxies, the nearest untrusted X-Forwarded-For hop (or the
header named by the optional security.trusted_proxy_header, off by default).
No other header (CF-Connecting-IP, X-Real-IP from untrusted peers, ...) and no
"home WAN" address is ever treated as local.
"""

import ipaddress
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# Headers a proxy or tunnel adds. Their presence from an untrusted source means
# the request was relayed, so it is treated as remote (spoofing one can only make
# a request *less* trusted).
# CF-Connecting-IP / True-Client-IP are listed so a tunnel connector on the LAN
# (e.g. cloudflared in Docker) is never mistaken for a local client; their
# values are never used as the client address.
_FORWARD_HEADERS = ('X-Forwarded-For', 'X-Real-IP', 'Forwarded',
                    'CF-Connecting-IP', 'True-Client-IP')


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
    """The real client address, or None when it can't be trusted.

    - Peer not in security.trusted_proxies: the peer itself, unless the request
      carries any forwarding header (then None: relayed, treated as remote).
    - Peer is a trusted proxy: the header named by security.trusted_proxy_header
      when that setting is on (off by default), else the nearest
      X-Forwarded-For hop that isn't itself a trusted proxy. With neither,
      None (the proxy didn't say who the client is).
    """
    peer = request.remote_addr or ''
    nets = trusted_proxies(settings) if settings is not None else []
    if nets and _in(nets, peer):
        custom_header = (settings.get('security.trusted_proxy_header') or '').strip()
        if custom_header:
            value = request.headers.get(custom_header, '').split(',')[-1].strip()
            return value if _addr(value) else None
        hops = [h.strip() for h in request.headers.get('X-Forwarded-For', '').split(',') if h.strip()]
        if not hops:
            return None
        remaining = list(hops)
        # Walk back from the nearest hop past any further trusted proxies.
        while remaining and _in(nets, remaining[-1]):
            remaining.pop()
        ip = remaining[-1] if remaining else hops[0]
        return ip if _addr(ip) else None
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


class TrustedProxyMiddleware:
    """WSGI middleware: honor X-Forwarded-Proto / X-Forwarded-Host only from
    trusted proxies, so request.scheme, request.is_secure and request.host
    can't be spoofed by a direct client. REMOTE_ADDR is not changed.
    """

    def __init__(self, wsgi_app, get_settings):
        self.wsgi_app = wsgi_app
        self._get_settings = get_settings

    def __call__(self, environ, start_response):
        try:
            nets = trusted_proxies(self._get_settings())
        except Exception:
            nets = []
        if nets and _in(nets, environ.get('REMOTE_ADDR', '')):
            proto = environ.get('HTTP_X_FORWARDED_PROTO', '').split(',')[-1].strip().lower()
            if proto in ('http', 'https'):
                environ['wsgi.url_scheme'] = proto
            host = environ.get('HTTP_X_FORWARDED_HOST', '').split(',')[-1].strip()
            if host:
                environ['HTTP_HOST'] = host
        return self.wsgi_app(environ, start_response)
