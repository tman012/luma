"""Sign-in rate limiting: too many wrong PINs locks that client and account for a while."""

import threading
import time
from typing import Dict, List, Tuple

MAX_FAILURES = 5          # wrong PINs allowed per window
WINDOW_SECONDS = 15 * 60  # counting window, and the lockout length

_fail: Dict[str, List[float]] = {}
_lock = threading.Lock()


def _keys(ip: str, identifier: str) -> Tuple[str, ...]:
    # Behind a local reverse proxy every client shares 127.0.0.1, so only the
    # account key applies there; direct clients are also limited per address.
    ident = f'id:{(identifier or "").strip().lower()}'
    if not ip or ip in ('127.0.0.1', '::1'):
        return (ident,)
    return f'ip:{ip}', ident


def _recent(key: str, now: float) -> List[float]:
    ts = [t for t in _fail.get(key, []) if now - t < WINDOW_SECONDS]
    if ts:
        _fail[key] = ts
    else:
        _fail.pop(key, None)
    return ts


def retry_after(ip: str, identifier: str) -> int:
    """Seconds until sign-in is allowed again (0 = allowed now)."""
    now = time.time()
    wait = 0
    with _lock:
        for k in _keys(ip, identifier):
            ts = _recent(k, now)
            if len(ts) >= MAX_FAILURES:
                wait = max(wait, int(ts[-MAX_FAILURES] + WINDOW_SECONDS - now) + 1)
    return wait


def record_failure(ip: str, identifier: str) -> None:
    now = time.time()
    with _lock:
        for k in _keys(ip, identifier):
            _fail.setdefault(k, []).append(now)


def record_success(ip: str, identifier: str) -> None:
    with _lock:
        for k in _keys(ip, identifier):
            _fail.pop(k, None)
