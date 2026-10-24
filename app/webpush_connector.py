"""Settings > Connectors: Web Push (owner only).

GET  /api/settings/connectors/webpush
PUT  /api/settings/connectors/webpush        {enabled?, vapid_email?}
POST /api/settings/connectors/webpush/test  send a test push (mocked in tests)

Same notifications.web_push.* keys as prod Services > Web Push Notifications.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('webpush_connector', __name__)
_cfg: Dict[str, Any] = {}

EMAIL_MAX = 320
THROTTLE_MAX, THROTTLE_WINDOW_S = 10, 60
_tests: Dict[Tuple[Any, str], list] = {}


def init(*, settings, owner_or_error: Callable) -> None:
    _cfg.update(settings=settings, owner_or_error=owner_or_error)


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def view() -> Dict[str, Any]:
    s = _cfg['settings']
    email = str(s.get('notifications.web_push.vapid_email') or '').strip()
    return {
        'enabled': bool(s.get('notifications.web_push.enabled')),
        'vapid_email': email,
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


def _clean_email(raw: Any) -> Optional[str]:
    if raw is None:
        return ''
    if not isinstance(raw, str):
        return None
    v = raw.strip()
    if len(v) > EMAIL_MAX:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        return None
    return v


@bp.route('/api/settings/connectors/webpush', methods=['GET'])
def webpush_get():
    _user, err = _cfg['owner_or_error']()
    if err:
        return err
    return _no_store(jsonify(view()))


@bp.route('/api/settings/connectors/webpush', methods=['PUT'])
def webpush_save():
    _user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _no_store(jsonify({'error': 'Invalid'}), 400)
    patch: Dict[str, Any] = {}
    if 'enabled' in body:
        if not isinstance(body['enabled'], bool):
            return _no_store(jsonify({'error': 'Invalid', 'fields': {'enabled': 'Invalid'}}), 400)
        patch['enabled'] = body['enabled']
    if 'vapid_email' in body:
        email = _clean_email(body.get('vapid_email'))
        if email is None:
            return _no_store(jsonify({'error': 'Invalid', 'fields': {'vapid_email': 'Invalid'}}), 400)
        patch['vapid_email'] = email
    if not patch:
        return _no_store(jsonify(view()))
    ok, problems = _cfg['settings'].update({'notifications': {'web_push': patch}})
    if not ok:
        logger.warning('Web Push settings rejected (%d problems)', len(problems))
        return _no_store(jsonify({'error': "Couldn't save"}), 400)
    logger.info('Web Push connector saved (enabled=%s)', view().get('enabled'))
    return _no_store(jsonify(view()))


@bp.route('/api/settings/connectors/webpush/test', methods=['POST'])
def webpush_test():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    if _throttled(user.get('id'), 'webpush-test'):
        return _no_store(jsonify({'ok': False, 'error': 'Rate limited'}))
    try:
        from app.push_service import send_push_notification, get_subscription_count
        count = get_subscription_count()
        if count == 0:
            return _no_store(jsonify({'ok': False, 'error': 'No subscribers'}))
        # Prod Services test copy.
        sent = send_push_notification(
            title='Test Movie (2025) added!',
            body='Tap to view details',
            tag='test',
            url='/',
        )
        if not sent:
            return _no_store(jsonify({'ok': False, 'error': 'Push disabled'}))
        return _no_store(jsonify({'ok': True, 'message': f'Sent to {count} subscriber(s)'}))
    except Exception as e:
        logger.warning('Web Push test failed (%s)', type(e).__name__)
        return _no_store(jsonify({'ok': False, 'error': 'Unexpected response'}))
