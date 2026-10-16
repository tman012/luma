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
    store = _load(data_dir, now)
    if tok['exp'] - TTL_SECONDS < store['reject_issued_before']:
        return None, 'revoked'   # issued before the used-link record was lost
    if tok['nonce'] in store['used']:
        return None, 'used'
    return tok, None


def consume(secret: str, fields, data_dir, now: Optional[float] = None) -> tuple:
    """Like check(), and atomically marks the nonce used when valid."""
    now = time.time() if now is None else now
    with _lock, _file_lock(data_dir):
        tok, reason = check(secret, fields, data_dir, now)
        if tok is None:
            return None, reason
        store = _load(data_dir, now)
        store['used'][tok['nonce']] = tok['exp']
        _save(data_dir, store)
    return tok, None


def release(data_dir, nonce: str) -> None:
    """Forget a consumed nonce (the action failed and changed nothing), so the
    same link can be retried."""
    with _lock, _file_lock(data_dir):
        store = _load(data_dir, time.time())
        if store['used'].pop(nonce, None) is not None:
            _save(data_dir, store)


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


def _empty() -> Dict:
    return {'used': {}, 'reject_issued_before': 0}


def _load(data_dir, now: float) -> Dict:
    """{'used': {nonce: exp}, 'reject_issued_before': ts}.

    If the store is unreadable, it is moved aside and every link issued before
    now is refused from then on (the cutoff is saved right away), since the
    record of which ones were used is gone."""
    path = os.path.join(str(data_dir), NONCE_FILENAME)
    try:
        with open(path) as fh:
            raw = json.load(fh)
        store = _parse_store(raw)
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError, TypeError, UnicodeDecodeError) as e:
        from app.paths import quarantine
        quarantine(path, f'used-link store unreadable: {e}', logger)
        store = _empty()
        store['reject_issued_before'] = int(now) + 1
        logger.error('Telegram admin links issued before now are refused (used-link record lost)')
        try:
            _save(data_dir, store)
        except OSError as save_err:
            logger.error('Could not save the link cutoff: %s', save_err)
        return store
    store['used'] = {n: e for n, e in store['used'].items() if e > now}
    return store


def _parse_store(raw) -> Dict:
    if not isinstance(raw, dict):
        raise ValueError('not an object')
    if 'used' not in raw and all(isinstance(v, int) for v in raw.values()):
        raw = {'used': raw}                      # earlier flat format {nonce: exp}
    used, cutoff = raw.get('used'), raw.get('reject_issued_before', 0)
    if not isinstance(used, dict) or isinstance(cutoff, bool) or not isinstance(cutoff, (int, float)):
        raise ValueError('wrong shape')
    out = {}
    for n, e in used.items():
        if isinstance(e, bool) or not isinstance(e, (int, float)):
            raise ValueError('bad entry')
        out[str(n)] = int(e)
    return {'used': out, 'reject_issued_before': int(cutoff)}


def _save(data_dir, store: Dict) -> None:
    from app.paths import atomic_write_json
    atomic_write_json(os.path.join(str(data_dir), NONCE_FILENAME), store, mode=0o600)
