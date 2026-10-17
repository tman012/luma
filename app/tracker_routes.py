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

Only the install owner (the first active admin), signed in with a session,
may call these. The implicit LAN owner (no sign-in) gets 401, and the service
API key is not the owner. Secrets never leave the server: responses carry
SECRET_MASK, Test answers a short message. A stored secret is only ever sent
to the URL it was saved with (see TrackerStore).

Test goes through app.tracker_guard like searches do: it counts toward the
per-tracker and global caps, a rate-limit or ban backoff refuses it, and a
429 or ban answer starts a persisted backoff that honors Retry-After. All of
it is keyed by site (type for known sites, normalized host for
Torznab/Newznab), never by record id.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request

from app import tracker_clients
from app.tracker_guard import guard_key, guarded_test, normalize_host
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
         is_implicit: Callable[[], bool],
         tester: Optional[Callable[[Dict[str, Any]], None]] = None,
         on_change: Optional[Callable[[Dict[str, Any]], None]] = None,
         on_delete: Optional[Callable[[Dict[str, Any]], None]] = None,
         on_list_change: Optional[Callable[[], None]] = None) -> None:
    """*is_implicit* tells whether the current user is the implicit LAN
    owner (no session). *tester* runs one connection test (raises
    TrackerError); defaults to tracker_clients.test. It always runs inside
    tracker_guard.guarded_test. *on_change*/*on_delete* get the stored record
    after an edit or a delete (the search manager resets backoffs and drops
    release links there); *on_change* also gets url_changed=bool.
    *on_list_change* runs after any add, edit, enable/disable or delete (cached
    search answers no longer match the tracker list)."""
    _cfg.update(get_current_user=get_current_user, get_owner=get_owner, is_implicit=is_implicit,
                tester=tester or (lambda cfg: tracker_clients.test(cfg)),
                on_change=on_change or (lambda rec, **kw: None), on_delete=on_delete or (lambda rec: None),
                on_list_change=on_list_change or (lambda: None))


def _list_changed() -> None:
    try:
        _cfg.get('on_list_change', lambda: None)()
    except Exception as e:   # a cache reset must never fail the request
        logger.warning('Tracker list change hook failed: %s', type(e).__name__)


def _no_store(resp, status=200):
    if isinstance(resp, tuple):
        resp, status = resp
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _require_owner():
    user = _cfg['get_current_user']()
    if not user or _cfg['is_implicit']():
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


def _unreadable():
    """The secret key file is unreadable or invalid: nothing can be saved."""
    return _no_store(jsonify({'error': UNREADABLE}), 409)


def _not_found():
    return _no_store(jsonify({'error': 'Not found'}), 404)


def _throttle_keys(cfg: Dict[str, Any]) -> List[str]:
    """The guard key (site type, or normalized host for Torznab/Newznab) and
    the normalized host: never the record id, so re-adding, editing, an
    unsaved form or another path on the same host shares one budget."""
    keys = [guard_key(cfg), f"host:{normalize_host(cfg.get('url')) or '-'}"]
    return list(dict.fromkeys(keys))


def _test_allowed(cfg: Dict[str, Any]) -> bool:
    """A few Test clicks are fine; a stuck key must not hammer a tracker.
    (The persisted limits live in app.tracker_guard; this is the click debounce.)"""
    keys, now = _throttle_keys(cfg), time.time()
    with _test_lock:
        logs = {k: [t for t in _test_log.get(k, []) if t > now - TEST_WINDOW_S] for k in keys}
        _test_log.update(logs)
        if any((log and now - log[-1] < TEST_SPACING_S) or len(log) >= TEST_MAX for log in logs.values()):
            return False
        for log in logs.values():
            log.append(now)
        return True


def _run_test(cfg: Dict[str, Any]) -> Optional[str]:
    """None when the tracker answered correctly, else a short error."""
    if not _test_allowed(cfg):
        return 'Rate limited'
    try:
        guarded_test(cfg, _cfg['tester'])
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
    try:
        rec, errors = get_store().create(data)
    except CredentialsUnreadable:
        return _unreadable()
    if errors:
        return _invalid(errors)
    _list_changed()
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
    before = get_store().get(tracker_id)
    try:
        rec, errors = get_store().update(tracker_id, data)
    except CredentialsUnreadable:
        return _unreadable()
    if errors:
        return _invalid(errors)
    _cfg['on_change'](rec, url_changed=bool(before) and before.get('url') != rec.get('url'))
    _list_changed()
    return _no_store(jsonify({'tracker': get_store().public(rec)}))


@bp.route('/api/trackers/<int:tracker_id>/enabled', methods=['PUT'])
def set_tracker_enabled(tracker_id: int):
    data = _body()
    if data is None or not isinstance(data.get('enabled'), bool):
        return _invalid({'enabled': 'Invalid'})
    rec = get_store().set_enabled(tracker_id, data['enabled'])
    if rec is None:
        return _not_found()
    _list_changed()
    return _no_store(jsonify({'tracker': get_store().public(rec)}))


@bp.route('/api/trackers/<int:tracker_id>', methods=['DELETE'])
def delete_tracker(tracker_id: int):
    rec = get_store().get(tracker_id)
    if rec is None or not get_store().delete(tracker_id):
        return _not_found()
    tracker_clients.clear_avistaz_tokens()
    _cfg['on_delete'](rec)
    _list_changed()
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
