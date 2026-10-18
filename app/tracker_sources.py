"""Sources and grab on top of direct tracker search (Settings > Trackers).

This is the one backend file that knows how direct tracker results look
(app.tracker_search): a result's download link is an opaque release link,
<URL base>/api/trackers/release/<token>, that only the server can turn back
into the tracker's real link (which carries a passkey). If that shape
changes, only this file changes.

Grab (POST /api/downloads/add with a release link):
- The token is resolved server-side; the real link never reaches the browser.
- A saved credential (cookie, user agent) only ever goes to the tracker's
  saved base URL: same scheme, host and port. A download link (or redirect)
  on any other origin is fetched with no credentials, only when it carries
  none of the tracker's saved secrets, and only from a public address.
- Link-local, metadata and unspecified addresses are never fetched.
- The request goes through the shared tracker guard (rate limits, 429 and
  Retry-After backoff) like a search does, and a 429 starts a backoff.
- Only a real sign-in (session or service key) may use trackers; implicit
  LAN access does not count. The user must be an admin or an approved user.

Nothing here logs a URL, a header or a body.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from app import tracker_types as tt
from app.tracker_guard import address_scope, record_failure, strict_400
from app.tracker_http import TrackerError, looks_banned, raise_for_status

logger = logging.getLogger(__name__)

MAX_TORRENT_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 3
_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')


class GrabError(Exception):
    """A grab that can't go ahead. str() is short, sentence case, safe to show."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status

    @property
    def message(self) -> str:
        return str(self)


# ------------------------------------------------------------------ result shape

def _release_prefix() -> str:
    from app.tracker_search import RELEASE_PATH
    return RELEASE_PATH


def _url_base() -> str:
    try:
        from flask import current_app
        return (current_app.config.get('URL_BASE') or '').rstrip('/')
    except RuntimeError:
        return ''


def release_token(url: Any) -> Optional[str]:
    """The token of a release link from a tracker result, else None.

    Accepts the link exactly as /api/sources/all-results handed it out
    (relative, with or without the URL base). Absolute URLs are never
    release links, so a pasted tracker URL keeps the old path."""
    s = str(url or '').strip()
    if not s.startswith('/'):
        return None
    prefix = _release_prefix()
    base = _url_base()
    path = s
    if base and path.startswith(base + prefix):
        path = path[len(base):]
    if not path.startswith(prefix):
        return None
    tok = path[len(prefix):]
    return tok if _TOKEN_RE.match(tok) else ''


def trackers_in_use() -> bool:
    """True when Sources searches Luma's own trackers (at least one enabled)."""
    from app.tracker_search import has_enabled_trackers
    return has_enabled_trackers()


def indexer_available(settings) -> bool:
    """True when Sources has anything to search: an enabled tracker (v172: the only source; a saved
    Prowlarr/Torznab setup no longer counts)."""
    try:
        return trackers_in_use()
    except Exception:
        return False


# ------------------------------------------------------------------ access

def member_denied(get_user: Callable[[], Optional[dict]], implicit: bool,
                  is_approved: Callable[[Optional[dict]], bool]):
    """None when the request may use trackers; else (json, status).

    Trackers need a real sign-in: a session or the service key. Implicit LAN
    access is anonymous here (401). Signed-in users must be an admin or an
    approved user (403)."""
    from flask import jsonify
    user = get_user()
    if not user or implicit:
        return jsonify({'error': 'Authentication required', 'sign_in': True}), 401
    if user.get('is_admin'):
        return None
    try:
        if is_approved(user):
            return None
    except Exception:
        pass
    return jsonify({'error': 'Access required'}), 403


# ------------------------------------------------------------------ origins

def _origin(url: str) -> Optional[Tuple[str, str, int]]:
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return None
    scheme = (p.scheme or '').lower()
    host = (p.hostname or '').lower().rstrip('.')
    if scheme not in ('http', 'https') or not host or p.username or p.password:
        return None
    return scheme, host, port or (443 if scheme == 'https' else 80)


def _blocked_address(addr: str) -> bool:
    try:
        a = ipaddress.ip_address(addr.split('%', 1)[0])
    except ValueError:
        return True
    a = getattr(a, 'ipv4_mapped', None) or a
    # Link-local covers 169.254.169.254 (cloud metadata); fd00:ec2::254 is
    # the IPv6 metadata address.
    return bool(a.is_link_local or a.is_unspecified or a.is_multicast or a.is_reserved
                or str(a) == 'fd00:ec2::254')


def _literal(host: str):
    try:
        return ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        return None


def _resolve(host: str):
    lit = _literal(host)
    if lit is not None:
        return [str(lit)]
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    return sorted({i[4][0] for i in infos})


def _host_ok(host: str, port: int, resolver=None) -> bool:
    """Every address *host* resolves to is public and *port* is allowed on it
    (the same rules tracker HTTP applies to every hop: any port on a public
    host, Luma's own ports refused only on Luma's own addresses)."""
    from app.tracker_http import address_allowed, port_allowed
    addrs = (resolver or _resolve)(host)
    if not addrs:
        return False
    return all(not _blocked_address(a) and address_allowed(a) and port_allowed(a, port) for a in addrs)


def _secret_values(cfg: Dict[str, Any]):
    return [str(cfg[k]) for k in tt.secret_keys(cfg.get('type')) if cfg.get(k) and len(str(cfg[k])) >= 4]


def _download_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Credentials a tracker needs on its own download links (IPTorrents:
    the saved cookie and user agent). Sent to the saved base origin only."""
    if cfg.get('type') == 'iptorrents':
        from app.tracker_clients import iptorrents_download_headers
        return {k: v for k, v in iptorrents_download_headers(cfg).items() if v}
    return {}


def _is_torrent(body: bytes) -> bool:
    return body[:1] == b'd' and b'4:info' in body[:MAX_TORRENT_BYTES]


# ------------------------------------------------------------------ grab

def safe_magnet(uri: Any) -> Optional[str]:
    """A magnet URI with no control characters (one line), or None.
    qBittorrent splits its `urls` field on newlines, so a second line (for
    example a Torznab magneturl carrying &#10;) would be fetched by qBit."""
    s = str(uri or '').strip()
    if not s.lower().startswith('magnet:'):
        return None
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in s):
        return None
    return s


def _magnet_or_refuse(uri: Any, status: int = 502) -> Tuple[str, str]:
    m = safe_magnet(uri)
    if not m:
        raise GrabError('This download link isn’t allowed.', status)
    return 'magnet', m


def fetch_release(token: Optional[str], resolver=None, automatic: bool = False) -> Tuple[str, Any]:
    """('magnet', uri) or ('torrent', bytes) for a release token.

    *automatic* (v158c, a TV grab Luma made on its own): charged as a background request, purpose
    'tv' on a capped tracker (tv_budget.capped), without the TV spacing (the search just went);
    otherwise interactive. Every HTTP request is charged: take() runs again before each redirect
    hop and the fetch stops when one is refused.

    Raises GrabError with a short, UI-safe message."""
    from app.tracker_search import get_search, guard_key
    from app.tracker_store import CredentialsUnreadable

    if not token:
        raise GrabError('This download link isn’t valid. Search again.', 400)
    engine = get_search()
    hit = engine.tokens.resolve(token)
    if not hit:
        raise GrabError('This download link has expired. Search again.', 410)
    tracker_id, target = hit
    rec = engine.store.get(int(tracker_id))
    if rec is None:
        raise GrabError('This tracker was removed. Search again.', 410)
    if not rec.get('enabled', True):
        raise GrabError('This tracker is turned off.', 409)
    try:
        cfg = engine.store.config(rec)
    except CredentialsUnreadable:
        raise GrabError('This tracker’s saved sign-in can’t be read. Edit it in Settings.', 409) from None

    if target.lower().startswith('magnet:'):
        return _magnet_or_refuse(target, 400)        # v169 (Tester): the token's own target refused: a 400

    base = _origin(str(cfg.get('url') or ''))
    if base is None:
        raise GrabError('This tracker’s URL isn’t valid. Edit it in Settings.', 409)
    secrets = _secret_values(cfg)
    creds = _download_headers(cfg)
    strict = tt.is_rate_strict(cfg['type'])
    gk = guard_key(cfg)

    def plan(url: str, status: int = 502) -> Dict[str, str]:
        """Headers for *url*, or GrabError when it may not be fetched. v169 (Tester): *status* 400
        for the token's own target (the link itself is bad: no fallback), 502 for a hop the tracker
        redirected to."""
        o = _origin(url)
        if o is None:
            raise GrabError('This download link isn’t valid.', status)
        if o == base:
            if not _host_ok(o[1], o[2], resolver=resolver):
                raise GrabError('This download link isn’t allowed.', status)
            return dict(creds)
        # Another origin: never with a saved credential.
        if any(s in url for s in secrets) or not _host_ok(o[1], o[2], resolver=resolver):
            raise GrabError('This download link points to another site and wasn’t fetched.', status)
        return {}

    purpose = None
    if automatic:
        from app import tv_budget
        purpose = tv_budget.PURPOSE if tv_budget.capped(cfg) else None

    def charge() -> None:
        if automatic:
            reason = engine.guard.take(gk, False, strict, purpose=purpose, spacing=False)
        else:
            reason = engine.guard.take(gk, True, strict)
        if reason:
            logger.info('Tracker %s (id %s) download not sent: %s', cfg['name'], cfg['id'], reason)
            raise GrabError('This tracker is rate limited right now. Try again later.', 429)
        engine.guard.wait_spacing(gk)

    plan(target, 400)             # v169 (Tester): a target we'd refuse costs nothing: not charged, not fetched
    with engine.guard.tracker_lock(gk):
        charge()
        url = target
        # Torznab/Newznab: refuse a server whose resolved address is backing
        # off, and key a new rate limit or ban to the addresses contacted.
        scope = address_scope(cfg, engine.guard)
        try:
            with scope:
                for hop in range(MAX_REDIRECTS + 1):
                    headers = plan(url)
                    if hop:
                        charge()                              # every request is charged
                    resp = engine.request('GET', url, headers=headers, max_bytes=MAX_TORRENT_BYTES,
                                          follow_redirects=False)
                    if resp.status in (301, 302, 303, 307, 308):
                        loc = (resp.headers.get('Location') or resp.headers.get('location') or '').strip()
                        if loc.lower().startswith('magnet:'):
                            engine.guard.success(gk)
                            return _magnet_or_refuse(loc)
                        if not loc:
                            raise TrackerError('redirect', resp.status)
                        url = urljoin(url, loc)
                        continue
                    raise_for_status(resp)
                    break
                else:
                    raise TrackerError('redirect')
        except TrackerError as e:
            if e.kind in ('rate', 'banned', 'auth') or strict_400(gk, e):    # v169: strict 400 = rate
                record_failure(engine.guard, gk, e, scope)
            logger.info('Tracker %s (id %s) download failed: %s', cfg['name'], cfg['id'], e.kind)
            if e.kind == 'rate':
                raise GrabError('This tracker is rate limited right now. Try again later.', 429) from None
            if e.kind == 'banned':
                raise GrabError('This tracker is paused right now. Try again later.', 429) from None
            if e.kind == 'auth':
                raise GrabError('The tracker refused the download. Check its sign-in in Settings.', 502) from None
            if e.kind == 'too_large':
                raise GrabError('The tracker sent a file that’s too large.', 502) from None
            raise GrabError('Couldn’t get the torrent from the tracker. Search again.', 502) from None

    body = resp.body or b''
    head = body[:8].lower()
    if head.startswith(b'magnet:'):
        engine.guard.success(gk)
        lines = body.decode('utf-8', 'ignore').strip().splitlines()
        return _magnet_or_refuse(lines[0] if lines else '')
    if resp.status == 200 and _is_torrent(body):
        engine.guard.success(gk)
        return 'torrent', body
    if looks_banned(body[:4000].decode('utf-8', 'ignore')):
        record_failure(engine.guard, gk, TrackerError('banned', resp.status), scope)
        logger.info('Tracker %s (id %s) download refused: banned', cfg['name'], cfg['id'])
        raise GrabError('This tracker is paused right now. Try again later.', 429)
    logger.info('Tracker %s (id %s) download was not a torrent', cfg['name'], cfg['id'])
    raise GrabError('The tracker didn’t send a torrent. Search again.', 502)
