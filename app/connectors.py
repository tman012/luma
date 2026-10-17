"""Settings > Connectors: the TMDB API key (owner only).

The key is stored server-side in settings (apis.tmdb.api_key, a sensitive
field) and never sent back to the browser: GET answers whether one is saved
and its last 4 characters. It is never logged.

In a minimal (LUMA_MINIMAL=1) build nothing is preconfigured: TMDB stays off
until the owner saves a key here. An env TMDB_API_KEY, a key bootstrapped
into settings.json from the env, or the legacy Config class is ignored
(settings_manager gates apis.tmdb.api_key on apis.tmdb.connector_saved).
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Dict, Optional

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('connectors', __name__)
_cfg: Dict[str, Callable] = {}

MASK = '********'
TMDB_TEST_URL = 'https://api.themoviedb.org/3/configuration'
KEY_MAX = 512
_tests: Dict[int, list] = {}


def init(*, settings, owner_or_error: Callable, on_tmdb_key: Callable[[str], None]) -> None:
    _cfg.update(settings=settings, owner_or_error=owner_or_error, on_tmdb_key=on_tmdb_key)


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _stored_key() -> str:
    return str(_cfg['settings'].get('apis.tmdb.api_key') or '').strip()


def _view() -> dict:
    key = _stored_key()
    return {'tmdb': {'set': bool(key), 'last4': key[-4:] if len(key) >= 8 else ''}}


def _clean(raw) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    key = raw.strip()
    if not key or len(key) > KEY_MAX or any(ord(c) < 33 or ord(c) == 127 for c in key):
        return None
    return key


def test_tmdb_key(key: str) -> Optional[str]:
    """None when TMDB accepts *key*, else a short error."""
    if key.startswith('eyJ'):
        return 'Use the API key, not the access token'
    try:
        r = requests.get(TMDB_TEST_URL, params={'api_key': key}, timeout=8)
    except requests.exceptions.Timeout:
        return 'Timed out'
    except Exception:
        return "Can't reach TMDB"
    if r.status_code in (401, 403):
        return 'Invalid key'
    if r.status_code == 429:
        return 'Rate limited'
    if r.status_code != 200:
        return 'Unexpected response'
    return None


def _throttled(uid: int) -> bool:
    now = time.time()
    log = [t for t in _tests.get(uid, []) if t > now - 60]
    if len(log) >= 10:
        _tests[uid] = log
        return True
    log.append(now)
    _tests[uid] = log
    return False


@bp.route('/api/settings/connectors', methods=['GET'])
def connectors_get():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    return _no_store(jsonify(_view()))


@bp.route('/api/settings/connectors/tmdb/test', methods=['POST'])
def connectors_tmdb_test():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    raw = body.get('api_key', MASK) if isinstance(body, dict) else MASK
    key = _stored_key() if raw in (MASK, None) else _clean(raw)
    if not key:
        return _no_store(jsonify({'ok': False, 'error': 'Required'}), 400)
    if _throttled(user['id']):
        return _no_store(jsonify({'ok': False, 'error': 'Rate limited'}))
    problem = test_tmdb_key(key)
    if problem:
        return _no_store(jsonify({'ok': False, 'error': problem}))
    return _no_store(jsonify({'ok': True, 'message': 'Connected'}))


@bp.route('/api/settings/connectors/tmdb', methods=['PUT'])
def connectors_tmdb_save():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or 'api_key' not in body:
        return _no_store(jsonify({'error': 'Required'}), 400)
    raw = body.get('api_key')
    if raw == MASK:
        return _no_store(jsonify(_view()))
    if raw in ('', None):
        _cfg['settings'].update({'apis': {'tmdb': {'api_key': '', 'connector_saved': True}}})
        _cfg['on_tmdb_key']('')
        logger.info('TMDB key removed')
        return _no_store(jsonify(_view()))
    key = _clean(raw)
    if not key:
        return _no_store(jsonify({'error': 'Invalid key'}), 400)
    problem = test_tmdb_key(key)
    if problem:
        return _no_store(jsonify({'error': problem}), 400)
    _cfg['settings'].update({'apis': {'tmdb': {'api_key': key, 'connector_saved': True}}})
    _cfg['on_tmdb_key'](key)
    logger.info('TMDB key saved')
    return _no_store(jsonify(_view()))
