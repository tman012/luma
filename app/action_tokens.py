"""Signed, single-use, expiring links for admin actions sent to Telegram.

A link carries action, uid, exp and nonce plus the full hex HMAC-SHA256 of
"telegram-action:{action}:{uid}:{exp}:{nonce}" keyed with SECRET_KEY. The GET
only shows a confirm page; the POST from that page performs the action once
and records the nonce, so a replayed link is refused (used nonces persist in
the data directory until their link would have expired anyway).
"""

import fcntl
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

TTL_SECONDS = 24 * 3600
ACTIONS = ('approve_user', 'deny_user', 'approve_plex', 'deny_plex')
NONCE_FILENAME = 'telegram_action_nonces.json'
_DOMAIN = 'telegram-action'
_lock = threading.Lock()


def _message(action: str, uid: int, exp: int, nonce: str) -> bytes:
    return f'{_DOMAIN}:{action}:{uid}:{exp}:{nonce}'.encode()


def sign(secret: str, action: str, uid: int, now: Optional[float] = None) -> Dict[str, str]:
    """Fields for a new link: action, uid, exp, nonce, sig."""
    if not secret:
        raise ValueError('SECRET_KEY is not set')
    if action not in ACTIONS:
        raise ValueError(f'unknown action {action}')
    exp = int((now if now is not None else time.time()) + TTL_SECONDS)
    nonce = secrets.token_urlsafe(16)
    sig = hmac.new(secret.encode(), _message(action, int(uid), exp, nonce), hashlib.sha256).hexdigest()
    return {'action': action, 'uid': str(int(uid)), 'exp': str(exp), 'nonce': nonce, 'sig': sig}


def parse(fields) -> Optional[Dict]:
    """Typed fields from a request mapping, or None when malformed."""
    try:
        action = str(fields.get('action') or '')
        uid = int(fields.get('uid') or '')
        exp = int(fields.get('exp') or '')
        nonce = str(fields.get('nonce') or '')
        sig = str(fields.get('sig') or '')
    except (TypeError, ValueError):
        return None
    if action not in ACTIONS or not nonce or len(nonce) > 64 or len(sig) != 64:
        return None
    return {'action': action, 'uid': uid, 'exp': exp, 'nonce': nonce, 'sig': sig}


def check(secret: str, fields, data_dir, now: Optional[float] = None) -> tuple:
    """(token, None) when the link is valid and unused, else (None, reason).
    Never changes state."""
    tok = parse(fields)
    if tok is None:
        return None, 'malformed'
    if not secret:
        return None, 'not configured'
    expected = hmac.new(secret.encode(), _message(tok['action'], tok['uid'], tok['exp'], tok['nonce']),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(tok['sig'], expected):
        return None, 'bad signature'
    now = time.time() if now is None else now
    if tok['exp'] <= now or tok['exp'] > now + TTL_SECONDS + 60:
        return None, 'expired'
    if tok['nonce'] in _load(data_dir, now):
        return None, 'used'
    return tok, None


def consume(secret: str, fields, data_dir, now: Optional[float] = None) -> tuple:
    """Like check(), and atomically marks the nonce used when valid."""
    now = time.time() if now is None else now
    with _lock, _file_lock(data_dir):
        tok, reason = check(secret, fields, data_dir, now)
        if tok is None:
            return None, reason
        used = _load(data_dir, now)
        used[tok['nonce']] = tok['exp']
        _save(data_dir, used)
    return tok, None


class _file_lock:
    def __init__(self, data_dir):
        self.path = os.path.join(str(data_dir), NONCE_FILENAME + '.lock')

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def _load(data_dir, now: float) -> Dict[str, int]:
    try:
        with open(os.path.join(str(data_dir), NONCE_FILENAME)) as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        # Fail closed would lock admins out; fail open would allow replays.
        # A broken file is replaced on the next save; until then links still
        # need a valid, unexpired signature.
        logger.warning('Used-link store unreadable: %s', e)
        return {}
    return {str(n): int(e) for n, e in raw.items() if int(e) > now}


def _save(data_dir, used: Dict[str, int]) -> None:
    d = str(data_dir)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.telegram_action_nonces.', dir=d)
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(used, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, os.path.join(d, NONCE_FILENAME))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
