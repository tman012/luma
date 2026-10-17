"""
outbound_guard — fetch a user-supplied URL without letting it reach inside.

/api/downloads/add takes a pasted .torrent link and fetches it server-side. A
plain ``requests.get(url, allow_redirects=True)`` there is a server-side
request forgery hole: the link (or any redirect it answers with) can point at
the cloud metadata service, the LAN, loopback services or Luma itself.

This module is the minimal guard main needs until the tracker stack's
``tracker_http`` lands; ``fetch()`` is the one entry point, so swapping the
implementation later is a one-line change at the call site.

Every hop:
  * the scheme must be http or https, with a host and no user info;
  * the host is resolved ONCE and every address it resolves to must be public
    (no private, loopback, link-local, ULA, shared 100.64/10, multicast,
    unspecified or reserved space, including IPv4 embedded in IPv6);
  * the connection goes to that pinned address (TLS still verifies the
    certificate for the original host name via SNI), so a DNS answer that
    changes between the check and the connect cannot redirect the request;
  * redirects are never followed automatically: each Location is resolved
    against the current URL and checked again as a new hop, up to MAX_HOPS;
  * environment proxies are ignored and the body size is capped;
  * one total deadline covers every hop, so a server that trickles bytes (or
    a slow redirect chain) cannot hold the request open past TOTAL_TIMEOUT.
"""

import ipaddress
import socket
import threading
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import urllib3

MAX_HOPS = 5
MAX_BYTES = 10 * 1024 * 1024
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0
TOTAL_TIMEOUT = 30.0
USER_AGENT = 'Luma'

_REDIRECTS = (301, 302, 303, 307, 308)
_DEFAULT_PORTS = {'http': 80, 'https': 443}

# Not "global" in practice even where ipaddress says otherwise.
_EXTRA_BLOCKED = tuple(ipaddress.ip_network(n) for n in (
    '0.0.0.0/8',
    '100.64.0.0/10',
    '192.0.0.0/24',
    '198.18.0.0/15',
    '224.0.0.0/4',
    '240.0.0.0/4',
    '64:ff9b:1::/48',
    'fc00::/7',
    'fe80::/10',
    'fec0::/10',
    'ff00::/8',
))
_NAT64 = ipaddress.ip_network('64:ff9b::/96')
_6TO4 = ipaddress.ip_network('2002::/16')


class BlockedURL(ValueError):
    """The URL, one of its redirects or its resolved address is not allowed."""


class FetchError(RuntimeError):
    """The fetch itself failed (network error, timeout, oversized body)."""


class Response:
    """The small subset of a requests.Response the caller uses."""

    def __init__(self, url: str, status_code: int, headers: Dict[str, str],
                 content: bytes):
        self.url = url
        self.status_code = status_code
        self.headers = headers
        self.content = content


def _embedded_v4(ip: ipaddress.IPv6Address) -> Optional[ipaddress.IPv4Address]:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _6TO4:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    # IPv4-compatible (deprecated) ::a.b.c.d
    if int(ip) >> 32 == 0:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_blocked_ip(value) -> bool:
    """True for any address a user-supplied URL must never reach."""
    try:
        ip = ipaddress.ip_address(str(value).split('%', 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_v4(ip)
        if inner is not None:
            if is_blocked_ip(inner):
                return True
            if ip.ipv4_mapped is not None or int(ip) >> 32 == 0:
                return False   # plain IPv4 in IPv6 clothing: the IPv4 decides
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or not ip.is_global):
        return True
    return any(ip.version == n.version and ip in n for n in _EXTRA_BLOCKED)


def _resolve(host: str, port: int) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise FetchError(f'Could not resolve {host}') from exc
    out: List[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in out:
            out.append(addr)
    if not out:
        raise FetchError(f'Could not resolve {host}')
    return out


def check_url(url: str) -> Tuple[str, str, int, str, str]:
    """Validate one hop. Returns (scheme, host, port, pinned_ip, target) where
    target is the path and query to request. Raises BlockedURL."""
    if not isinstance(url, str) or not url or any(c in url for c in '\r\n\t\x00'):
        raise BlockedURL('Invalid URL')
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise BlockedURL('Invalid URL') from exc
    scheme = (parts.scheme or '').lower()
    if scheme not in _DEFAULT_PORTS:
        raise BlockedURL('Only http and https links are allowed')
    if parts.username is not None or parts.password is not None or '@' in parts.netloc:
        raise BlockedURL('Links with credentials are not allowed')
    host = (parts.hostname or '').rstrip('.')
    if not host:
        raise BlockedURL('Invalid URL')
    port = port or _DEFAULT_PORTS[scheme]
    if not 0 < port < 65536:
        raise BlockedURL('Invalid URL')
    addrs = _resolve(host, port)
    if any(is_blocked_ip(a) for a in addrs):
        raise BlockedURL('Address not allowed')
    target = parts.path or '/'
    if parts.query:
        target += '?' + parts.query
    return scheme, host, port, addrs[0], target


def _host_header(host: str, port: int, scheme: str) -> str:
    h = f'[{host}]' if ':' in host else host
    return h if port == _DEFAULT_PORTS[scheme] else f'{h}:{port}'


def _open(scheme: str, ip: str, port: int, host: str, target: str,
          headers: Dict[str, str], timeout: urllib3.Timeout,
          max_bytes: int, deadline: Optional[float] = None) -> Tuple[int, Dict[str, str], bytes]:
    """One request to the pinned address. No redirects, no retries, no proxy.
    *deadline* is a time.monotonic() value the whole read must finish by."""
    if scheme == 'https':
        pool = urllib3.HTTPSConnectionPool(
            ip, port=port, timeout=timeout, retries=False, maxsize=1,
            server_hostname=host, assert_hostname=host, cert_reqs='CERT_REQUIRED')
    else:
        pool = urllib3.HTTPConnectionPool(ip, port=port, timeout=timeout,
                                          retries=False, maxsize=1)
    # Watchdog: the socket timeout only bounds each recv, so a server that
    # trickles the status line or headers could hold urlopen() open forever.
    # At the deadline the live socket is shut down, which ends any read.
    conns: list = []
    expired = threading.Event()
    base_cls = getattr(pool, 'ConnectionCls', None)
    if base_cls is not None:
        class _Tracked(base_cls):  # type: ignore[misc, valid-type]
            def connect(self):
                conns.append(self)
                super().connect()
                if expired.is_set():
                    _kill_sockets(conns)

        pool.ConnectionCls = _Tracked
    timer = None
    if deadline is not None:
        def _expire():
            expired.set()
            _kill_sockets(conns)
        timer = threading.Timer(max(0.0, deadline - time.monotonic()), _expire)
        timer.daemon = True
        timer.start()
    try:
        resp = pool.urlopen('GET', target, headers=headers, redirect=False,
                            retries=False, preload_content=False,
                            assert_same_host=False)
        try:
            chunks, size = [], 0
            read = getattr(resp, 'read1', None) or resp.read
            while True:
                if deadline is not None:
                    left = deadline - time.monotonic()
                    if left <= 0 or expired.is_set():
                        raise FetchError('Request took too long')
                    sock = getattr(getattr(resp, 'connection', None), 'sock', None)
                    if sock is not None:
                        try:
                            sock.settimeout(max(0.05, min(left, timeout.read_timeout or left)))
                        except OSError:
                            pass
                chunk = read(65536)
                if not chunk:
                    if expired.is_set():
                        raise FetchError('Request took too long')
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError('Response too large')
                chunks.append(chunk)
            return resp.status, dict(resp.headers.items()), b''.join(chunks)
        finally:
            resp.release_conn()
    except FetchError:
        raise
    except Exception as exc:
        if expired.is_set():
            raise FetchError('Request took too long') from exc
        raise FetchError(f'Request to {host} failed: {type(exc).__name__}') from exc
    finally:
        if timer is not None:
            timer.cancel()
        pool.close()


def _kill_sockets(conns) -> None:
    for c in list(conns):
        sock = getattr(c, 'sock', None)
        if sock is None:
            continue
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def fetch(url: str, *, max_hops: int = MAX_HOPS, max_bytes: int = MAX_BYTES,
          connect_timeout: float = CONNECT_TIMEOUT,
          read_timeout: float = READ_TIMEOUT,
          total_timeout: Optional[float] = None) -> Response:
    """GET a user-supplied URL with every hop checked and its address pinned.

    A redirect to a magnet: link ends the chain and is returned as the final
    URL (status 3xx, empty body) so the caller can hand it to qBittorrent.
    Raises BlockedURL for a refused URL/hop and FetchError for failures,
    including running past *total_timeout* seconds across all hops and body
    reads. By default that is connect + read timeout, capped at TOTAL_TIMEOUT,
    so a 1 byte/s upstream can't hold a worker thread past it."""
    if total_timeout is None:
        total_timeout = min(TOTAL_TIMEOUT, connect_timeout + read_timeout)
    deadline = time.monotonic() + total_timeout
    current = url
    for _hop in range(max_hops + 1):
        scheme, host, port, ip, target = check_url(current)
        left = deadline - time.monotonic()
        if left <= 0:
            raise FetchError('Request took too long')
        timeout = urllib3.Timeout(connect=min(connect_timeout, left),
                                  read=min(read_timeout, left))
        headers = {
            'Host': _host_header(host, port, scheme),
            'User-Agent': USER_AGENT,
            'Accept': '*/*',
            'Accept-Encoding': 'identity',
        }
        status, resp_headers, body = _open(scheme, ip, port, host, target,
                                           headers, timeout, max_bytes,
                                           deadline=deadline)
        lower = {k.lower(): v for k, v in resp_headers.items()}
        location = lower.get('location')
        if status in _REDIRECTS and location:
            nxt = urljoin(current, location.strip())
            if nxt.lower().startswith('magnet:'):
                return Response(nxt, status, resp_headers, b'')
            current = nxt
            continue
        return Response(current, status, resp_headers, body)
    raise BlockedURL('Too many redirects')
