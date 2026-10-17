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

Implicit owner access needs BOTH a local client address (above) AND a known
Host: an IP literal, localhost, this machine's own host name, a home-network
name (one label such as "docker-server", or *.local, *.lan, *.home.arpa,
*.internal) or a name listed in security.hostnames. The Host header is chosen
by the client, so it never grants access on its own; it only narrows it. A
page on a public name (for example a DNS-rebinding domain that resolves to a
LAN address) gets no implicit access. Requests that came through Cloudflare
(CF-Connecting-IP, CF-Ray, ... present) are never local, even from a trusted
proxy.
X-Forwarded-Host is only honored from a trusted proxy and only when it names
such a known host.
"""

import ipaddress
import logging
import socket
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


# Narrowest prefix accepted as a trusted proxy range. Anything broader (e.g.
# 0.0.0.0/0 or ::/0) would let the whole Internet vouch for client addresses.
MIN_PREFIX = {4: 8, 6: 32}
_warned: set = set()


def proxy_range_problem(value: str) -> Optional[str]:
    """Why *value* can't be a trusted proxy range, or None when it can."""
    try:
        net = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return f'Not an IP address or range: {value.strip()}'
    if net.prefixlen < MIN_PREFIX[net.version]:
        return f'Range too broad: {value.strip()} (broadest allowed is /{MIN_PREFIX[net.version]})'
    return None


def is_public_range(value: str) -> bool:
    try:
        net = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return False
    return not any(net.version == n.version and net.subnet_of(n) for n in _LOCAL_NETS)


def trusted_proxies(settings) -> List[ipaddress._BaseNetwork]:
    """Parsed security.trusted_proxies. Invalid or too-broad entries are
    ignored (logged once); public ranges are allowed but logged once."""
    raw = settings.get('security.trusted_proxies') or ''
    nets = []
    for part in str(raw).replace(';', ',').split(','):
        part = part.strip()
        if not part:
            continue
        problem = proxy_range_problem(part)
        if problem:
            if part not in _warned:
                _warned.add(part)
                logger.warning('[access] ignoring trusted proxy %s: %s', part, problem)
            continue
        if is_public_range(part) and part not in _warned:
            _warned.add(part)
            logger.warning('[access] trusted proxy %s is a public address range', part)
        nets.append(ipaddress.ip_network(part, strict=False))
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
        if not remaining:
            return None  # every hop is a proxy: no client address given
        ip = remaining[-1]
        return ip if _addr(ip) else None
    if any(request.headers.get(h) for h in _FORWARD_HEADERS):
        return None
    return peer


# Headers Cloudflare adds to every proxied request. A request carrying any of
# them came through the tunnel/CDN, so it is never a LAN request.
_CLOUDFLARE_HEADERS = ('CF-Connecting-IP', 'CF-Ray', 'CF-Visitor', 'CF-IPCountry', 'True-Client-IP')


def via_cloudflare(request) -> bool:
    if any(request.headers.get(h) for h in _CLOUDFLARE_HEADERS):
        return True
    return 'cloudflare' in (request.headers.get('CDN-Loop') or '').lower()


def is_local_request(request, settings=None) -> bool:
    """True when the request originates from a local/private IP.
    
    Checks:
    1. Direct connection from private IP (LAN, loopback)
    2. Connection via trusted proxy from private IP (using X-Forwarded-For)
    """
    if via_cloudflare(request):
        return False
    ip = client_ip(request, settings)
    return bool(ip and _is_private(ip))


def configured_hostnames(settings) -> set:
    """Lower-cased names from security.hostnames (comma or space separated)."""
    raw = (settings.get('security.hostnames') or '') if settings is not None else ''
    names = set()
    for part in str(raw).replace(';', ',').replace(' ', ',').split(','):
        name = split_host(part.strip())
        if name:
            names.add(name)
    return names


def split_host(host: str) -> str:
    """The lower-cased host name of a Host header value, without port or brackets."""
    host = (host or '').strip().lower()
    if not host:
        return ''
    if host.startswith('['):
        end = host.find(']')
        return host[1:end] if end > 0 else ''
    if host.count(':') == 1:
        host = host.split(':', 1)[0]
    return host.rstrip('.')


# Names that only resolve inside a home network (never public DNS).
# Chosen for names that public DNS can never (or by ICANN policy will not)
# delegate: .local (mDNS, RFC 6762), .home.arpa (RFC 8375), .internal (ICANN
# reserved for private use, 2024), .localdomain (de facto, never delegated),
# .home (ICANN name-collision block, not delegated) and .lan (common router
# default, not a TLD). Left out on purpose: .corp is collision-blocked too but
# used by company networks; .private, .intranet and other words are open to
# future TLD applications, so a public site could one day use them.
# app.lan_hosts uses this same list.
HOME_SUFFIXES = ('.local', '.lan', '.home.arpa', '.internal', '.localdomain', '.home')


def is_home_name(name: str) -> bool:
    """One label (e.g. 'docker-server') or a home-network suffix; not an IP."""
    name = split_host(name)
    if not name or _addr(name) is not None:
        return False
    return '.' not in name or name.endswith(HOME_SUFFIXES)


def own_hostnames() -> set:
    """This machine's host name, with and without its domain."""
    try:
        raw = split_host(socket.gethostname())
    except OSError:
        return set()
    return {raw, raw.split('.', 1)[0]} if raw else set()


def is_known_host(host: str, settings=None) -> bool:
    """True for a private or loopback IP literal, localhost, a home-network
    name, this machine's host name, or a name (or public IP) listed in
    security.hostnames. Never enough on its own: see should_authenticate."""
    name = split_host(host)
    if not name:
        return False
    if _is_private(name) or name == 'localhost' or is_home_name(name):
        return True
    return name in own_hostnames() or name in configured_hostnames(settings)


def is_portal_traffic(request, settings) -> bool:
    """Detect if request is portal traffic (via dedicated hostname only).
    
    Portal traffic is detected ONLY by Host header matching request_portal.hostname.
    Owner can use the full app remotely via tunnel on the main hostname.
    """
    portal = split_host(str(settings.get('request_portal.hostname') or ''))
    if not portal:
        return False
    return split_host(request.headers.get('Host') or '') == portal


def should_authenticate(settings, request) -> bool:
    """True when this request must authenticate (new auth_required logic).
    
    Uses security.auth_required (migrated from legacy access_mode):
    - 'enabled': everyone must authenticate
    - 'disabled_local': local requests get implicit owner, remote must authenticate
    
    Default: 'disabled_local' (LAN and kiosk don't need sign-in)
    
    Portal traffic always requires authentication (never gets implicit owner).
    """
    # A minimal (test) build never treats any address as the implicit owner.
    from app import minimal
    if minimal.enabled():
        return True

    # Portal traffic never gets implicit owner
    if is_portal_traffic(request, settings):
        return True

    # A trusted proxy sent an unknown or inconsistent X-Forwarded-Host
    if getattr(request, 'environ', {}).get(HOST_UNTRUSTED):
        return True
    
    # Read auth_required setting (legacy access_mode is migrated at settings load)
    auth_req = (settings.get('security.auth_required') or 'disabled_local').strip()
    
    if auth_req == 'enabled':
        # Everyone must authenticate
        return True
    elif auth_req == 'disabled_local':
        # Local gets implicit owner on a known Host; everyone else signs in
        return not (is_local_request(request, settings)
                    and is_known_host(request.host, settings))
    else:
        # Unknown value, default to safe (require auth)
        return True


def owner(user_auth) -> Optional[dict]:
    """The install owner: the first active admin account."""
    for u in user_auth.list_users():
        if u.get('is_admin') and (u.get('status') or 'active') == 'active':
            return u
    return None


HOST_UNTRUSTED = 'luma.host_untrusted'


class TrustedProxyMiddleware:
    """WSGI middleware: honor X-Forwarded-Proto / X-Forwarded-Host only from
    trusted proxies, so request.scheme, request.is_secure and request.host
    can't be spoofed by a direct client. REMOTE_ADDR is not changed.

    From a trusted proxy:
    - no X-Forwarded-Host: the Host header is used as is;
    - X-Forwarded-Host present: it must be a single value, a
      known host AND name the same host as the Host header (the proxy is
      expected to send `Host $host` and overwrite X-Forwarded-Host). Otherwise
      the request is flagged HOST_UNTRUSTED and never gets implicit access.
    """

    def __init__(self, wsgi_app, get_settings):
        self.wsgi_app = wsgi_app
        self._get_settings = get_settings

    def __call__(self, environ, start_response):
        try:
            settings = self._get_settings()
            nets = trusted_proxies(settings)
        except Exception:
            settings, nets = None, []
        if nets and _in(nets, environ.get('REMOTE_ADDR', '')):
            proto = environ.get('HTTP_X_FORWARDED_PROTO', '').split(',')[-1].strip().lower()
            if proto in ('http', 'https'):
                environ['wsgi.url_scheme'] = proto
            xfh = environ.get('HTTP_X_FORWARDED_HOST', '').strip()
            if xfh:
                single = ',' not in xfh
                same = split_host(xfh) == split_host(environ.get('HTTP_HOST', ''))
                if single and same and is_known_host(xfh, settings):
                    environ['HTTP_HOST'] = xfh
                else:
                    environ[HOST_UNTRUSTED] = True
        return self.wsgi_app(environ, start_response)
