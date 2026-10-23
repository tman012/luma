"""v260 Settings > Automation > Downloads guard routes (luma-test builds; owner only).

GET /api/settings/automation/downloads-guard    {settings, defaults, folder, trackers, allowed, protected_names}
PUT /api/settings/automation/downloads-guard    partial settings -> the same view (400 {errors})

POST /api/downloads-guard/keep    {hashes} -> {protected_hashes}  anyone signed in (Never delete only protects)
POST /api/downloads-guard/unkeep  {hashes} -> {protected_hashes}  the owner signed in with a session (401 else)

A change that turns the guard on or makes it delete more, sooner, more often or somewhere else
(dg_store.widens) needs the owner signed in with a session: the implicit LAN owner gets 401 with
"sign_in": true. Turning it off or tightening it is open to the owner either way. Status, preview and runs
are the /api/downloads-guard/* routes in main.py; the free-space pill is /api/storage/downloads.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, request

from app.automation import dg_store

logger = logging.getLogger(__name__)
bp = Blueprint('downloads_guard_settings', __name__)
URL = '/api/settings/automation/downloads-guard'


def _guard(write: bool = False):
    from app import movie_settings
    return movie_settings._guard(write)


def _out(body: Any, status: int = 200):
    from app import movie_settings
    return movie_settings._out(body, status)


def view() -> Dict[str, Any]:
    from app import instance, storage_cleanup, tracker_files
    cfg = dg_store.settings()
    return {'settings': cfg, 'defaults': dg_store.defaults(),
            'folder': storage_cleanup.downloads_root(cfg), 'media_folder': storage_cleanup._media_root(),
            'trackers': tracker_files.known_trackers(),
            'allowed': instance.writes_allowed('downloads_guard'),
            'protected_names': dg_store.names()}     # v261: qBit's names of the Never delete hashes


@bp.route(URL, methods=['GET'])
def dg_get():
    err = _guard()
    if err:
        return err
    try:
        from app import downloads_guard
        downloads_guard.fill_names_later()           # a Never delete hash with no name yet: read qBit lazily
    except Exception as e:                           # noqa: BLE001
        logger.warning('Downloads guard: name lookup failed (%s)', type(e).__name__)
    return _out(view())


@bp.route(URL, methods=['PUT'])
def dg_put():
    err = _guard()
    if err:
        return err
    body = request.get_json(silent=True)
    cur = dg_store.settings()
    try:
        new = dg_store.check(body, cur)
    except dg_store.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    wider = dg_store.widens(cur, new)
    if wider:
        err = _guard(write=True)
        if err:
            return err
    try:
        dg_store.save(body)
    except dg_store.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    if wider:
        logger.info('Downloads guard: %s widened by the signed-in owner', ', '.join(wider))
    return _out(view())


KEEP = '/api/downloads-guard/keep'
UNKEEP = '/api/downloads-guard/unkeep'


def _signed_in_user():
    """Keep is open to anyone signed in (it only protects); no user gets 401 with "sign_in": true."""
    from flask import jsonify
    from app import start_flow
    try:
        user = start_flow._cfg['get_current_user']()
    except Exception:                                        # noqa: BLE001
        user = None
    if not user or not user.get('id'):
        return None, (jsonify({'error': start_flow.SIGN_IN_FIRST, 'sign_in': True}), 401)
    return user, None


def _hashes_body():
    body = request.get_json(silent=True)
    hashes = body.get('hashes') if isinstance(body, dict) else None
    return hashes if isinstance(hashes, list) else None


@bp.route(KEEP, methods=['POST'])
def dg_keep():
    user, err = _signed_in_user()
    if err:
        resp, status = err
        resp.headers['Cache-Control'] = 'no-store'
        return resp, status
    from app import downloads_guard
    hashes = [str(h).lower() for h in (_hashes_body() or []) if isinstance(h, str)]
    try:
        kept = dg_store.keep(_hashes_body() or [], downloads_guard.known_names(hashes))   # names from Preview's qBit read
    except dg_store.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    logger.info('Downloads guard: kept %s (Never delete)', ', '.join('…' + h[-4:] for h in (_hashes_body() or [])))
    return _out({'protected_hashes': kept, 'protected_names': dg_store.names()})


@bp.route(UNKEEP, methods=['POST'])
def dg_unkeep():
    from app import start_flow
    _user, err = start_flow.require_signed_in_owner()
    if err:
        resp, status = err
        resp.headers['Cache-Control'] = 'no-store'
        return resp, status
    try:
        kept = dg_store.unkeep(_hashes_body() or [])
    except dg_store.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    logger.info('Downloads guard: unkept %s by the signed-in owner', ', '.join('…' + h[-4:] for h in (_hashes_body() or [])))
    return _out({'protected_hashes': kept})
