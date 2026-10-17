"""HTTP for tracker requests: timeouts, a response size cap, same-host
redirects only, and short error strings that are safe to show the owner.

Nothing here logs a URL, a header or a body: tracker URLs and headers carry
API keys, passkeys and cookies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlsplit

import requests

MAX_BYTES = 8 * 1024 * 1024
CONNECT_TIMEOUT, READ_TIMEOUT = 6.0, 30.0
MAX_REDIRECTS = 3
USER_AGENT = 'Luma'

# Short, owner-facing messages (the Test result and the list status).
MESSAGES = {
    'auth': 'Unauthorized',
    'banned': 'Banned',
    'rate': 'Rate limited',
    'timeout': 'Timed out',
    'network': 'Connection failed',
    'tls': 'Certificate error',
    'response': 'Unexpected response',
    'too_large': 'Response too large',
    'redirect': 'Unexpected redirect',
    'http': 'Server error',
}


class TrackerError(Exception):
    """A tracker request failed. str() is a short message safe for the UI."""

    def __init__(self, kind: str, status: Optional[int] = None, retry_after: Optional[float] = None,
                 message: Optional[str] = None):
        self.kind = kind if kind in MESSAGES else 'response'
        self.status = status
        self.retry_after = retry_after
        msg = message or MESSAGES[self.kind]
        if self.kind == 'http' and status and not message:
            msg = f'HTTP {status}'
        super().__init__(msg)

    @property
    def message(self) -> str:
        return str(self)


@dataclass
class Response:
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b''
    url: str = ''

    def text(self) -> str:
        return self.body.decode('utf-8', 'replace')


def retry_after_seconds(value: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """Seconds from a Retry-After header (delta-seconds or HTTP-date), 1 s to 24 h."""
    if not value:
        return None
    value = str(value).strip()
    now = time.time() if now is None else now
    if value.isdigit():
        secs = float(value)
    else:
        from email.utils import parsedate_to_datetime
        try:
            secs = parsedate_to_datetime(value).timestamp() - now
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    return min(max(secs, 1.0), 86400.0)


_BAN_WORDS = ('banned', 'ban ', 'suspended', 'disabled account', 'account disabled')


def looks_banned(text: str) -> bool:
    low = (text or '')[:4000].lower()
    return any(w in low for w in _BAN_WORDS)


def raise_for_status(resp: Response) -> None:
    """Map HTTP failures to TrackerError (401 auth, 403 auth or ban, 429 rate,
    other 4xx/5xx http). 2xx and 3xx return."""
    s = resp.status
    if s < 400:
        return
    if s == 401:
        raise TrackerError('auth', s)
    if s == 403:
        raise TrackerError('banned' if looks_banned(resp.text()) else 'auth', s)
    if s == 429 or s == 509:
        raise TrackerError('rate', s, retry_after_seconds(resp.headers.get('Retry-After')))
    if s == 503 and resp.headers.get('Retry-After'):
        raise TrackerError('rate', s, retry_after_seconds(resp.headers.get('Retry-After')))
    raise TrackerError('http', s)


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    host_a, host_b = (pa.hostname or '').lower(), (pb.hostname or '').lower()
    if host_a.startswith('www.'):
        host_a = host_a[4:]
    if host_b.startswith('www.'):
        host_b = host_b[4:]
    if host_a != host_b:
        return False
    # https -> http is a downgrade; http -> https is fine.
    return not (pa.scheme == 'https' and pb.scheme != 'https')


def request(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
            params: Any = None, data: Any = None, json: Any = None,
            timeout: Optional[tuple] = None, max_bytes: int = MAX_BYTES,
            follow_redirects: bool = True, session: Optional[requests.Session] = None) -> Response:
    """One request with a size cap. Redirects are followed only to the same
    host (never https -> http), at most MAX_REDIRECTS times; any other
    redirect is returned as is so the caller can treat it (for example a
    sign-in page). Raises TrackerError on timeouts and network errors."""
    sess = session or requests.Session()
    hdrs = {'User-Agent': USER_AGENT}
    hdrs.update(headers or {})
    cur_url, cur_method, cur_params, cur_data, cur_json = url, method.upper(), params, data, json
    try:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                r = sess.request(cur_method, cur_url, headers=hdrs, params=cur_params, data=cur_data,
                                 json=cur_json, timeout=timeout or (CONNECT_TIMEOUT, READ_TIMEOUT),
                                 allow_redirects=False, stream=True)
            except requests.exceptions.SSLError:
                raise TrackerError('tls') from None
            except requests.exceptions.Timeout:
                raise TrackerError('timeout') from None
            except (requests.exceptions.ConnectionError, requests.exceptions.InvalidURL,
                    requests.exceptions.MissingSchema, requests.exceptions.InvalidSchema):
                raise TrackerError('network') from None
            except requests.exceptions.RequestException:
                raise TrackerError('network') from None
            try:
                loc = r.headers.get('Location') if r.status_code in (301, 302, 303, 307, 308) else None
                if loc and follow_redirects:
                    nxt = urljoin(getattr(r, 'url', '') or cur_url, loc)
                    if _same_origin(url, nxt):
                        if r.status_code == 303 or (r.status_code in (301, 302) and cur_method == 'POST'):
                            cur_method, cur_data, cur_json = 'GET', None, None
                        cur_url, cur_params = nxt, None
                        continue
                body = bytearray()
                try:
                    for chunk in r.iter_content(65536):
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise TrackerError('too_large', r.status_code)
                except requests.exceptions.Timeout:
                    raise TrackerError('timeout') from None
                except requests.exceptions.RequestException:
                    raise TrackerError('network') from None
                return Response(r.status_code, {k: v for k, v in r.headers.items()}, bytes(body),
                                getattr(r, 'url', cur_url) or cur_url)
            finally:
                r.close()
        raise TrackerError('redirect')
    finally:
        if session is None:
            sess.close()
