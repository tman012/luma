"""Settings > Connectors: Telegram (owner only).

GET  /api/settings/connectors/telegram
PUT  /api/settings/connectors/telegram        {bot_token?, chat_id?}
POST /api/settings/connectors/telegram/test   getMe (mocked in tests; never a real send)

Stores bot_token + admin_chat_id in data/telegram_bot.json (prod's file). The
token never leaves the server: GET answers only whether one is saved and its
last 4 characters, and it is never logged. On the test instance, actual sends
still need LUMA_TEST_ALLOW=telegram (v182).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('telegram_connector', __name__)
_cfg: Dict[str, Any] = {}

MASK = '********'
TOKEN_MAX = 256
CHAT_MAX = 64
THROTTLE_MAX, THROTTLE_WINDOW_S = 10, 60
_tests: Dict[Tuple[Any, str], list] = {}

# Injected in tests; production uses urllib against api.telegram.org.
_get_me: Optional[Callable[[str], Dict[str, Any]]] = None


def init(*, owner_or_error: Callable) -> None:
    _cfg.update(owner_or_error=owner_or_error)


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _path():
    from app.paths import telegram_bot_config_path
    return telegram_bot_config_path()


def _load() -> Dict[str, str]:
    try:
        p = _path()
        if p.is_file():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return {
                    'bot_token': str(data.get('bot_token') or '').strip(),
                    'admin_chat_id': str(data.get('admin_chat_id') or '').strip(),
                }
    except Exception as e:
        logger.warning('Telegram connector: could not read config (%s)', type(e).__name__)
    return {'bot_token': '', 'admin_chat_id': ''}


def _save(cfg: Dict[str, str]) -> None:
    from app.paths import atomic_write_json
    atomic_write_json(_path(), {
        'bot_token': cfg.get('bot_token') or '',
        'admin_chat_id': cfg.get('admin_chat_id') or '',
    })


def view() -> Dict[str, Any]:
    cfg = _load()
    tok = cfg['bot_token']
    return {
        'bot_token': {'set': bool(tok), 'last4': tok[-4:] if len(tok) >= 8 else ''},
        'chat_id': cfg['admin_chat_id'],
    }


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


def _token_in(body: Dict[str, Any], stored: str) -> Tuple[Optional[str], Optional[str]]:
    """Returns (token_or_None_if_invalid, error). Absent/mask keeps stored."""
    if 'bot_token' not in body or body.get('bot_token') in (None, MASK):
        return stored, None
    raw = body.get('bot_token')
    if not isinstance(raw, str) or len(raw) > TOKEN_MAX:
        return None, 'Invalid'
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return None, 'Invalid'
    return raw.strip(), None


def _chat_in(body: Dict[str, Any], stored: str) -> Tuple[Optional[str], Optional[str]]:
    if 'chat_id' not in body:
        return stored, None
    raw = body.get('chat_id')
    if raw is None:
        return '', None
    if not isinstance(raw, str) or len(raw) > CHAT_MAX:
        return None, 'Invalid'
    v = raw.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        return None, 'Invalid'
    # Telegram chat ids are digits, optionally with a leading '-'.
    if v and not (v.lstrip('-').isdigit()):
        return None, 'Invalid'
    return v, None


def get_me(token: str) -> Dict[str, Any]:
    """Call Telegram getMe. Never logs the token. Overridable via _get_me in tests."""
    if _get_me is not None:
        return _get_me(token)
    import urllib.error
    import urllib.request
    url = f'https://api.telegram.org/bot{token}/getMe'
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        if body.get('ok'):
            return {'ok': True, 'message': 'Connected'}
        return {'ok': False, 'error': 'Invalid token'}
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            return {'ok': False, 'error': 'Invalid token'}
        return {'ok': False, 'error': 'Unexpected response'}
    except Exception:
        return {'ok': False, 'error': "Can't reach Telegram"}


@bp.route('/api/settings/connectors/telegram', methods=['GET'])
def telegram_get():
    _user, err = _cfg['owner_or_error']()
    if err:
        return err
    return _no_store(jsonify(view()))


@bp.route('/api/settings/connectors/telegram', methods=['PUT'])
def telegram_save():
    _user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _no_store(jsonify({'error': 'Invalid'}), 400)

    stored = _load()
    # Remove: empty token + empty chat clears the file.
    if body.get('bot_token') in ('', None) and body.get('chat_id') in ('', None) \
            and 'bot_token' in body and 'chat_id' in body:
        _save({'bot_token': '', 'admin_chat_id': ''})
        logger.info('Telegram connector removed')
        return _no_store(jsonify(view()))

    token, terr = _token_in(body, stored['bot_token'])
    chat, cerr = _chat_in(body, stored['admin_chat_id'])
    fields = {}
    if terr:
        fields['bot_token'] = terr
    if cerr:
        fields['chat_id'] = cerr
    if fields:
        return _no_store(jsonify({'error': 'Invalid', 'fields': fields}), 400)
    if not token:
        return _no_store(jsonify({'error': 'Invalid', 'fields': {'bot_token': 'Required'}}), 400)

    if _throttled(_user.get('id'), 'tg-save'):
        return _no_store(jsonify({'error': 'Rate limited'}), 429)

    result = get_me(token)
    if not result.get('ok'):
        return _no_store(jsonify({'error': result.get('error') or 'Invalid token', 'fields': {}}), 400)

    _save({'bot_token': token, 'admin_chat_id': chat or ''})
    logger.info('Telegram connector saved (chat_set=%s)', bool(chat))
    return _no_store(jsonify(view()))


@bp.route('/api/settings/connectors/telegram/test', methods=['POST'])
def telegram_test():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    stored = _load()
    token, terr = _token_in(body, stored['bot_token'])
    if terr or not token:
        return _no_store(jsonify({
            'ok': False,
            'error': terr or 'Required',
            'fields': {'bot_token': terr or 'Required'},
        }), 400)
    if _throttled(user.get('id'), 'tg-test'):
        return _no_store(jsonify({'ok': False, 'error': 'Rate limited'}))
    result = get_me(token)
    if not result.get('ok'):
        logger.info('Telegram test failed')
        return _no_store(jsonify({'ok': False, 'error': result.get('error') or 'Invalid token'}))
    return _no_store(jsonify({'ok': True, 'message': 'Connected'}))
