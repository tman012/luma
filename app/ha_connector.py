"""Settings > Connectors: Home Assistant (owner only).

GET  /api/settings/connectors/homeassistant        url + access_token {set, last4}
PUT  /api/settings/connectors/homeassistant        save into settings homeassistant
POST /api/settings/connectors/homeassistant/test   GET /api/ via ha_service (mocked in tests)

Reads and writes the same homeassistant.url / homeassistant.access_token the
Rooms power path and smart-home panel use. The access token never leaves the
server: GET answers only whether one is saved and its last 4 characters, and
it is never logged. A stored token is only kept for the URL it was saved with:
a Test or Save for another URL needs the token typed again.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('ha_connector', __name__)
_cfg: Dict[str, Any] = {}

MASK = '********'
URL_MAX = 512
TOKEN_MAX = 2048
THROTTLE_MAX, THROTTLE_WINDOW_S = 10, 60

CONNECTED = 'Connected'
REQUIRED = 'Required'
INVALID_URL = 'Invalid URL'
INVALID = 'Invalid'
RATE_LIMITED = 'Rate limited'

_tests: Dict[Tuple[Any, str], list] = {}


def init(*, settings, owner_or_error: Callable) -> None:
    _cfg.update(settings=settings, owner_or_error=owner_or_error)


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _s(key: str) -> str:
    v = _cfg['settings'].get(f'homeassistant.{key}')
    return v.strip() if isinstance(v, str) else ''


def normalize_url(raw: Any) -> Optional[str]:
    """An http(s) HA base without a trailing slash, query, fragment or
    userinfo; None when it isn't one."""
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    if not url or len(url) > URL_MAX or any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return None
    host = parts.hostname
    if ':' in host:
        host = f'[{host}]'
    netloc = f'{host}:{port}' if port is not None else host
    path = parts.path.rstrip('/')
    return f'{parts.scheme.lower()}://{netloc}{path}'


def _same_url(a: str, b: str) -> bool:
    na, nb = normalize_url(a), normalize_url(b)
    return bool(na) and na == nb


def view() -> Dict[str, Any]:
    """What the card shows. Empty until a URL is saved."""
    url = _s('url')
    token = _s('access_token')
    if not url:
        return {'url': '', 'access_token': {'set': False, 'last4': ''}}
    return {
        'url': url,
        'access_token': {
            'set': bool(token),
            'last4': token[-4:] if len(token) >= 8 else '',
        },
    }


def _token_in(body: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """(given, value): given is False when the field is absent or the mask
    (keep the stored one). value is None when it is invalid."""
    if 'access_token' not in body or body.get('access_token') in (None, MASK):
        return False, ''
    raw = body.get('access_token')
    if not isinstance(raw, str) or len(raw) > TOKEN_MAX or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return True, None
    # Empty string means clear (Remove).
    return True, raw


def _resolve(body: Dict[str, Any]) -> Tuple[Optional[Dict[str, str]], Dict[str, str]]:
    """Candidate config from a form body. Fills the stored token only for the
    stored URL. Returns (cfg, field_errors)."""
    errors: Dict[str, str] = {}
    raw_url = body.get('url')
    url = normalize_url(raw_url) if raw_url not in (None, '') else None
    if raw_url in (None, ''):
        errors['url'] = REQUIRED
    elif url is None:
        errors['url'] = INVALID_URL

    given, token = _token_in(body)
    if given and token is None:
        errors['access_token'] = INVALID
    elif given and token == '':
        # Explicit clear is only for Remove (handled by the route).
        pass
    elif not given:
        stored = _s('access_token')
        if stored and url and not _same_url(url, _s('url')):
            errors['access_token'] = REQUIRED
        token = stored if url and _same_url(url, _s('url')) else (token or '')
        if not token and not errors.get('access_token'):
            errors['access_token'] = REQUIRED

    if errors:
        return None, errors
    return {'url': url or '', 'access_token': token or ''}, {}


def _throttled(uid: Any, bucket: str) -> bool:
    now = time.time()
    k = (uid, bucket)
    log = [t for t in _tests.get(k, []) if t > now - THROTTLE_WINDOW_S]
    if len(log) >= THROTTLE_MAX:
        _tests[k] = log
        return True
    log.append(now)
    _tests[k] = log
    return False


def _short_detail(detail: str) -> str:
    """Map ha_service detail strings to Connectors-style short errors."""
    d = (detail or '').strip()
    low = d.lower()
    if 'required' in low and 'url' in low:
        return REQUIRED
    if 'required' in low and 'token' in low:
        return REQUIRED
    if '401' in d or 'invalid access token' in low:
        return 'Invalid token'
    if 'timed out' in low:
        return 'Timed out'
    if 'cannot connect' in low or "can't reach" in low:
        return "Can't reach Home Assistant"
    if d in ('API running.', 'Connected') or low.startswith('api running'):
        return CONNECTED
    if not d:
        return 'Unexpected response'
    # Keep short; never echo a token-shaped string.
    if len(d) > 80:
        return 'Unexpected response'
    return d


@bp.route('/api/settings/connectors/homeassistant', methods=['GET'])
def ha_get():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    return _no_store(jsonify(view()))


@bp.route('/api/settings/connectors/homeassistant', methods=['PUT'])
def ha_save():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _no_store(jsonify({'error': INVALID}), 400)

    # Remove: empty URL clears the section.
    if body.get('url') in ('', None) and body.get('access_token') in ('', None):
        ok, problems = _cfg['settings'].update({'homeassistant': {'url': '', 'access_token': ''}})
        if not ok:
            logger.warning('Home Assistant clear rejected (%d problems)', len(problems))
            return _no_store(jsonify({'error': "Couldn't save"}), 400)
        logger.info('Home Assistant connector removed')
        return _no_store(jsonify(view()))

    cfg, errors = _resolve(body)
    if errors:
        return _no_store(jsonify({'error': INVALID, 'fields': errors}), 400)

    if _throttled(user.get('id'), 'ha-save'):
        return _no_store(jsonify({'error': RATE_LIMITED}), 429)

    from app.ha_service import test_connection
    result = test_connection(cfg['url'], cfg['access_token'])
    if not result.get('ok'):
        return _no_store(jsonify({
            'error': _short_detail(result.get('detail') or ''),
            'fields': {},
        }), 400)

    ok, problems = _cfg['settings'].update({
        'homeassistant': {'url': cfg['url'], 'access_token': cfg['access_token']},
    })
    if not ok:
        logger.warning('Home Assistant settings rejected (%d problems)', len(problems))
        return _no_store(jsonify({'error': "Couldn't save"}), 400)
    logger.info('Home Assistant connector saved')
    return _no_store(jsonify(view()))


@bp.route('/api/settings/connectors/homeassistant/test', methods=['POST'])
def ha_test():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    if not body:
        body = {'url': _s('url')}

    cfg, errors = _resolve(body)
    if errors:
        first = next(iter(errors.values()))
        return _no_store(jsonify({'ok': False, 'error': first, 'fields': errors}), 400)

    if _throttled(user.get('id'), 'ha-test'):
        return _no_store(jsonify({'ok': False, 'error': RATE_LIMITED}))

    from app.ha_service import test_connection
    result = test_connection(cfg['url'], cfg['access_token'])
    if not result.get('ok'):
        logger.info('Home Assistant test failed: %s', _short_detail(result.get('detail') or ''))
        return _no_store(jsonify({'ok': False, 'error': _short_detail(result.get('detail') or '')}))
    return _no_store(jsonify({'ok': True, 'message': CONNECTED}))
