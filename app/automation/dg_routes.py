"""v260 Settings > Automation > Downloads guard routes (luma-test builds; owner only).

GET /api/settings/automation/downloads-guard    {settings, defaults, folder, trackers, allowed}
PUT /api/settings/automation/downloads-guard    partial settings -> the same view (400 {errors})

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
            'allowed': instance.writes_allowed('downloads_guard')}


@bp.route(URL, methods=['GET'])
def dg_get():
    return _guard() or _out(view())


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
