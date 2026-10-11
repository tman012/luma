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
"""

import ipaddress
from typing import Dict, List, Optional

MODES = ('open', 'pin_remote', 'pin')

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


def is_local_request(request, settings=None) -> bool:
    ip = client_ip(request, settings)
    return bool(ip) and _is_private(ip)


def needs_pin(settings, request) -> bool:
    """True when this request must sign in."""
    m = mode(settings)
    if m == 'open':
        return False
    if m == 'pin_remote':
        return not is_local_request(request, settings)
    return True


def owner(user_auth) -> Optional[Dict]:
    """The install owner: the first active admin account."""
    for u in user_auth.list_users():
        if u.get('is_admin') and (u.get('status') or 'active') == 'active':
            return u
    return None
