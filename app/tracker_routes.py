"""Owner-only API for trackers (Settings > Trackers).

GET    /api/trackers/types         type list with per-type field schemas
GET    /api/trackers               trackers with status, secrets masked
POST   /api/trackers               add
GET    /api/trackers/<id>          one tracker, secrets masked
PUT    /api/trackers/<id>          edit (a secret sent back masked is kept)
PUT    /api/trackers/<id>/enabled  {"enabled": bool}, without a full edit
DELETE /api/trackers/<id>          delete
POST   /api/trackers/<id>/test     test the saved tracker, updates its status
POST   /api/trackers/test          test an unsaved form ({"id": n} fills masked secrets)

Only the install owner (the first active admin, including the implicit LAN
owner) may call these. The service API key is not the owner. Secrets never
leave the server: responses carry SECRET_MASK, Test answers a short message.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request

from app import tracker_clients
from app.tracker_http import TrackerError
from app.tracker_store import (STATUS_ERROR, STATUS_OK, CredentialsUnreadable, get_store)
from app.tracker_types import public_types

logger = logging.getLogger(__name__)

bp = Blueprint('trackers', __name__)
_cfg: Dict[str, Any] = {}

CONNECTED = 'Connected'
UNREADABLE = 'Credentials unreadable'
TEST_SPACING_S = 3.0           # per tracker target
TEST_WINDOW_S, TEST_MAX = 600, 10

_test_log: Dict[str, List[float]] = {}
_test_lock = threading.Lock()


def init(*, get_current_user: Callable[[], Optional[dict]], get_owner: Callable[[], Optional[dict]],
         tester: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
    """*tester* runs one connection test (raises TrackerError); defaults to
    tracker_clients.test. The search manager swaps in its guarded version."""
    _cfg.update(get_current_user=get_current_user, get_owner=get_owner,
                tester=tester or (lambda cfg: tracker_clients.test(cfg)))


def _no_store(resp, status=200):
    if isinstance(resp, tuple):
        resp, status = resp
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _require_owner():
    user = _cfg['get_current_user']()
    if not user:
        return _no_store(jsonify({'error': 'Authentication required'}), 401)
    owner = _cfg['get_owner']()
    uid = user.get('id')
    if not owner or not uid or uid != owner.get('id') or not user.get('is_admin'):
        return _no_store(jsonify({'error': 'Owner access required'}), 403)
    return None


@bp.before_request
def _guard():
    return _require_owner()


def _body() -> Optional[Dict[str, Any]]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _invalid(fields: Dict[str, str]):
    if fields.get('_') == 'Not found':
        return _no_store(jsonify({'error': 'Not found'}), 404)
    return _no_store(jsonify({'error': 'Invalid', 'fields': fields}), 400)


def _not_found():
    return _no_store(jsonify({'error': 'Not found'}), 404)


def _throttle_key(cfg: Dict[str, Any]) -> str:
    return f"{cfg.get('type')}|{str(cfg.get('url') or '').lower()}"


def _test_allowed(cfg: Dict[str, Any]) -> bool:
    """A few Test clicks are fine; a stuck key must not hammer a tracker."""
    key, now = _throttle_key(cfg), time.time()
    with _test_lock:
        log = [t for t in _test_log.get(key, []) if t > now - TEST_WINDOW_S]
        if (log and now - log[-1] < TEST_SPACING_S) or len(log) >= TEST_MAX:
            _test_log[key] = log
            return False
        log.append(now)
        _test_log[key] = log
        return True


def _run_test(cfg: Dict[str, Any]) -> Optional[str]:
    """None when the tracker answered correctly, else a short error."""
    if not _test_allowed(cfg):
        return 'Rate limited'
    try:
        _cfg['tester'](cfg)
        return None
    except TrackerError as e:
        logger.info('Tracker test failed: %s (%s)', cfg.get('name') or cfg.get('type'), e.kind)
        return e.message
    except Exception as e:  # a parser bug must not leak details or a 500
        logger.warning('Tracker test error: %s (%s)', cfg.get('name') or cfg.get('type'), type(e).__name__)
        return 'Unexpected response'


def _test_response(err: Optional[str]):
    if err is None:
        return _no_store(jsonify({'ok': True, 'message': CONNECTED}))
    return _no_store(jsonify({'ok': False, 'error': err}))


@bp.route('/api/trackers/types', methods=['GET'])
def tracker_types():
    return _no_store(jsonify({'types': public_types()}))


@bp.route('/api/trackers', methods=['GET'])
def list_trackers():
    return _no_store(jsonify({'trackers': get_store().list_public()}))


@bp.route('/api/trackers', methods=['POST'])
def add_tracker():
    data = _body()
    if data is None:
        return _invalid({'_': 'Invalid'})
    rec, errors = get_store().create(data)
    if errors:
        return _invalid(errors)
    return _no_store(jsonify({'tracker': get_store().public(rec)}), 201)


@bp.route('/api/trackers/<int:tracker_id>', methods=['GET'])
def get_tracker(tracker_id: int):
    rec = get_store().get(tracker_id)
    if rec is None:
        return _not_found()
    return _no_store(jsonify({'tracker': get_store().public(rec)}))


@bp.route('/api/trackers/<int:tracker_id>', methods=['PUT'])
def edit_tracker(tracker_id: int):
    data = _body()
    if data is None:
        return _invalid({'_': 'Invalid'})
    rec, errors = get_store().update(tracker_id, data)
    if errors:
        return _invalid(errors)
    return _no_store(jsonify({'tracker': get_store().public(rec)}))


@bp.route('/api/trackers/<int:tracker_id>/enabled', methods=['PUT'])
def set_tracker_enabled(tracker_id: int):
    data = _body()
    if data is None or not isinstance(data.get('enabled'), bool):
        return _invalid({'enabled': 'Invalid'})
    rec = get_store().set_enabled(tracker_id, data['enabled'])
    if rec is None:
        return _not_found()
    return _no_store(jsonify({'tracker': get_store().public(rec)}))


@bp.route('/api/trackers/<int:tracker_id>', methods=['DELETE'])
def delete_tracker(tracker_id: int):
    if not get_store().delete(tracker_id):
        return _not_found()
    return _no_store(jsonify({'ok': True}))


@bp.route('/api/trackers/<int:tracker_id>/test', methods=['POST'])
def test_saved_tracker(tracker_id: int):
    store = get_store()
    rec = store.get(tracker_id)
    if rec is None:
        return _not_found()
    try:
        cfg = store.config(rec)
    except CredentialsUnreadable:
        store.set_status(tracker_id, STATUS_ERROR, UNREADABLE)
        return _test_response(UNREADABLE)
    err = _run_test(cfg)
    if err != 'Rate limited':
        store.set_status(tracker_id, STATUS_OK if err is None else STATUS_ERROR, err or '')
    return _test_response(err)


@bp.route('/api/trackers/test', methods=['POST'])
def test_unsaved_tracker():
    data = _body()
    if data is None:
        return _invalid({'_': 'Invalid'})
    tid = data.get('id')
    if tid is not None and (isinstance(tid, bool) or not isinstance(tid, int)):
        return _invalid({'id': 'Invalid'})
    payload = {k: v for k, v in data.items() if k != 'id'}
    try:
        cfg, errors = get_store().candidate_config(payload, tid)
    except CredentialsUnreadable:
        return _test_response(UNREADABLE)
    if errors:
        return _invalid(errors)
    return _test_response(_run_test(cfg))
