"""Settings > Connectors: the TMDB API key, the OMDb API key, the Trakt client ID and the TheTVDB key and PIN (owner only).

The key is stored server-side in settings (apis.tmdb.api_key, a sensitive
field) and never sent back to the browser: GET answers whether one is saved
and its last 4 characters. It is never logged.

In a minimal (LUMA_MINIMAL=1) build nothing is preconfigured: TMDB stays off
until the owner saves a key here. An env TMDB_API_KEY, a key bootstrapped
into settings.json from the env, or the legacy Config class is ignored
(settings_manager gates apis.tmdb.api_key on apis.tmdb.connector_saved).
Trakt works the same way: prod's client-ID check, and nothing from the env or
a bootstrapped settings.json counts until the owner saves one here.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Dict, Optional

import os

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('connectors', __name__)
_cfg: Dict[str, Callable] = {}

MASK = '********'
TMDB_DEFAULT_BASE = 'https://api.themoviedb.org/3'
TMDB_BASE_ENV = 'LUMA_TMDB_BASE_URL'


def tmdb_base_url() -> str:
    """TMDB API base: LUMA_TMDB_BASE_URL (an http(s) URL, e.g. a test mock)
    or TMDB itself. Used by the search client and the Connectors Test, so
    both talk to the same server."""
    raw = (os.environ.get(TMDB_BASE_ENV) or '').strip().rstrip('/')
    if raw.startswith(('http://', 'https://')) and ' ' not in raw:
        return raw
    return TMDB_DEFAULT_BASE
TRAKT_TEST_URL = 'https://api.trakt.tv/shows/trending'
KEY_MAX = 512
_tests: Dict[int, list] = {}


def init(*, settings, owner_or_error: Callable, on_tmdb_key: Callable[[str], None],
         on_trakt_change: Callable[[], None] = lambda: None) -> None:
    _cfg.update(settings=settings, owner_or_error=owner_or_error, on_tmdb_key=on_tmdb_key,
                on_trakt_change=on_trakt_change)


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _stored_key() -> str:
    return str(_cfg['settings'].get('apis.tmdb.api_key') or '').strip()


def _stored_trakt() -> str:
    s = _cfg['settings']
    if not s.get('apis.trakt.enabled', False):
        return ''
    return str(s.get('apis.trakt.client_id') or '').strip()


def _masked(key: str) -> dict:
    return {'set': bool(key), 'last4': key[-4:] if len(key) >= 8 else ''}


def trakt_connected() -> bool:
    return bool(_cfg) and bool(_stored_trakt())


def _stored_tvdb() -> tuple:
    s = _cfg['settings']
    return str(s.get('apis.tvdb.api_key') or '').strip(), str(s.get('apis.tvdb.pin') or '').strip()


def tvdb_credentials() -> tuple:
    """(api key, pin) for TheTVDB v4, ('', '') when no key is saved."""
    return _stored_tvdb() if _cfg else ('', '')


def _stored_omdb() -> str:
    return str(_cfg['settings'].get('apis.omdb.api_key') or '').strip()


def _view() -> dict:
    key, pin = _stored_tvdb()
    return {'tmdb': _masked(_stored_key()), 'trakt': _masked(_stored_trakt()), 'omdb': _masked(_stored_omdb()),
            'tvdb': dict(_masked(key), pin_set=bool(pin))}


def _clean(raw) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    key = raw.strip()
    if not key or len(key) > KEY_MAX or any(ord(c) < 33 or ord(c) == 127 for c in key):
        return None
    return key


def _test_get(url: str, **kw):
    """A key test's one GET: no proxy env, no redirects (a 3xx is 'Unexpected response', never followed)."""
    with requests.Session() as sess:
        sess.trust_env = False
        return sess.get(url, timeout=8, allow_redirects=False, **kw)


def test_tmdb_key(key: str) -> Optional[str]:
    """None when TMDB accepts *key*, else a short error."""
    if key.startswith('eyJ'):
        return 'Use the API key, not the access token'
    try:
        r = _test_get(f'{tmdb_base_url()}/configuration', params={'api_key': key})
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


def test_trakt_client_id(client_id: str) -> Optional[str]:
    """None when Trakt accepts *client_id* (prod's check), else a short error."""
    try:
        from app import trakt_http
        r = _test_get(TRAKT_TEST_URL, params={'limit': 1}, headers=trakt_http.headers(client_id))
    except requests.exceptions.Timeout:
        return 'Timed out'
    except Exception:
        return "Can't reach Trakt"
    if r.status_code in (401, 403):
        return 'Invalid client ID'
    if r.status_code == 429:
        return 'Rate limited'
    if r.status_code != 200:
        return 'Unexpected response'
    return None


def _throttled(uid: int, bucket: str = 'test') -> bool:
    """10 upstream checks a minute per user and bucket (Test and Save apart)."""
    now = time.time()
    k = (uid, bucket)
    log = [t for t in _tests.get(k, []) if t > now - 60]
    if len(log) >= 10:
        _tests[k] = log
        return True
    log.append(now)
    _tests[k] = log
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
    if _throttled(user['id'], 'tmdb-test'):
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
    if _throttled(user['id'], 'tmdb-save'):
        return _no_store(jsonify({'error': 'Rate limited'}), 429)
    problem = test_tmdb_key(key)
    if problem:
        return _no_store(jsonify({'error': problem}), 400)
    _cfg['settings'].update({'apis': {'tmdb': {'api_key': key, 'connector_saved': True}}})
    _cfg['on_tmdb_key'](key)
    logger.info('TMDB key saved')
    return _no_store(jsonify(_view()))


@bp.route('/api/settings/connectors/trakt/test', methods=['POST'])
def connectors_trakt_test():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    raw = body.get('client_id', MASK) if isinstance(body, dict) else MASK
    cid = _stored_trakt() if raw in (MASK, None) else _clean(raw)
    if not cid:
        return _no_store(jsonify({'ok': False, 'error': 'Required'}), 400)
    if _throttled(user['id'], 'trakt-test'):
        return _no_store(jsonify({'ok': False, 'error': 'Rate limited'}))
    problem = test_trakt_client_id(cid)
    if problem:
        return _no_store(jsonify({'ok': False, 'error': problem}))
    return _no_store(jsonify({'ok': True, 'message': 'Connected'}))


@bp.route('/api/settings/connectors/trakt', methods=['PUT'])
def connectors_trakt_save():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or 'client_id' not in body:
        return _no_store(jsonify({'error': 'Required'}), 400)
    raw = body.get('client_id')
    if raw == MASK:
        return _no_store(jsonify(_view()))
    if raw in ('', None):
        _cfg['settings'].update({'apis': {'trakt': {'client_id': '', 'client_secret': '', 'enabled': False,
                                                    'connector_saved': True}}})
        _cfg['on_trakt_change']()
        logger.info('Trakt client ID removed')
        return _no_store(jsonify(_view()))
    cid = _clean(raw)
    if not cid:
        return _no_store(jsonify({'error': 'Invalid client ID'}), 400)
    if _throttled(user['id'], 'trakt-save'):
        return _no_store(jsonify({'error': 'Rate limited'}), 429)
    problem = test_trakt_client_id(cid)
    if problem:
        return _no_store(jsonify({'error': problem}), 400)
    _cfg['settings'].update({'apis': {'trakt': {'client_id': cid, 'enabled': True, 'connector_saved': True}}})
    _cfg['on_trakt_change']()
    logger.info('Trakt client ID saved')
    return _no_store(jsonify(_view()))


@bp.route('/api/settings/connectors/omdb/test', methods=['POST'])
def connectors_omdb_test():
    from app import omdb
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    raw = body.get('api_key', MASK) if isinstance(body, dict) else MASK
    key = _stored_omdb() if raw in (MASK, None) else _clean(raw)
    if not key:
        return _no_store(jsonify({'ok': False, 'error': 'Required'}), 400)
    if _throttled(user['id'], 'omdb-test'):
        return _no_store(jsonify({'ok': False, 'error': 'Rate limited'}))
    problem = omdb.test_key(key)
    if problem:
        return _no_store(jsonify({'ok': False, 'error': problem}))
    return _no_store(jsonify({'ok': True, 'message': 'Connected'}))


@bp.route('/api/settings/connectors/omdb', methods=['PUT'])
def connectors_omdb_save():
    from app import omdb
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
        _cfg['settings'].update({'apis': {'omdb': {'api_key': '', 'connector_saved': True}}})
        omdb.forget()
        logger.info('OMDb key removed')
        return _no_store(jsonify(_view()))
    key = _clean(raw)
    if not key:
        return _no_store(jsonify({'error': 'Invalid key'}), 400)
    if _throttled(user['id'], 'omdb-save'):
        return _no_store(jsonify({'error': 'Rate limited'}), 429)
    problem = omdb.test_key(key)
    if problem:
        return _no_store(jsonify({'error': problem}), 400)
    _cfg['settings'].update({'apis': {'omdb': {'api_key': key, 'connector_saved': True}}})
    omdb.forget()
    logger.info('OMDb key saved')
    return _no_store(jsonify(_view()))


def test_tvdb_key(key: str, pin: str = '') -> Optional[str]:
    """None when TheTVDB v4 logs in with *key* (and *pin*), else a short error."""
    from app import tv_tvdb
    return tv_tvdb.check_login(key, pin)


def _tvdb_body(body) -> tuple:
    """(key, pin) from a Test/Save body: the mask (or nothing) keeps the stored value."""
    skey, spin = _stored_tvdb()
    raw_k = body.get('api_key', MASK) if isinstance(body, dict) else MASK
    raw_p = body.get('pin', MASK) if isinstance(body, dict) else MASK
    key = skey if raw_k in (MASK, None) else (_clean(raw_k) or '')
    pin = spin if raw_p in (MASK, None) else ('' if raw_p == '' else (_clean(raw_p) or ''))
    return key, pin


@bp.route('/api/settings/connectors/tvdb/test', methods=['POST'])
def connectors_tvdb_test():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    key, pin = _tvdb_body(request.get_json(silent=True) or {})
    if not key:
        return _no_store(jsonify({'ok': False, 'error': 'Required'}), 400)
    if _throttled(user['id'], 'tvdb-test'):
        return _no_store(jsonify({'ok': False, 'error': 'Rate limited'}))
    problem = test_tvdb_key(key, pin)
    if problem:
        return _no_store(jsonify({'ok': False, 'error': problem}))
    return _no_store(jsonify({'ok': True, 'message': 'Connected'}))


@bp.route('/api/settings/connectors/tvdb', methods=['PUT'])
def connectors_tvdb_save():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or 'api_key' not in body:
        return _no_store(jsonify({'error': 'Required'}), 400)
    if body.get('api_key') in ('', None):
        _cfg['settings'].update({'apis': {'tvdb': {'api_key': '', 'pin': ''}}})
        from app import tv_tvdb
        tv_tvdb.forget_token()
        _forget_air_times()
        logger.info('TVDB key removed')
        return _no_store(jsonify(_view()))
    if body.get('api_key') == MASK and body.get('pin', MASK) == MASK:
        return _no_store(jsonify(_view()))
    key, pin = _tvdb_body(body)
    if not key:
        return _no_store(jsonify({'error': 'Invalid key'}), 400)
    if _throttled(user['id'], 'tvdb-save'):
        return _no_store(jsonify({'error': 'Rate limited'}), 429)
    problem = test_tvdb_key(key, pin)
    if problem:
        return _no_store(jsonify({'error': problem}), 400)
    _cfg['settings'].update({'apis': {'tvdb': {'api_key': key, 'pin': pin}}})
    from app import tv_tvdb
    tv_tvdb.forget_token()
    _forget_air_times()
    logger.info('TVDB key saved')
    return _no_store(jsonify(_view()))


def _forget_air_times() -> None:
    try:
        from app import tv_airtimes
        tv_airtimes.forget()
    except Exception as e:             # noqa: BLE001 - no tv.db yet: nothing to ask again
        logger.debug('Air times not reset: %s', type(e).__name__)
