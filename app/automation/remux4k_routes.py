"""v359 Settings > Automation > 4K Remux auto-grab routes (luma-test builds; owner only; changes, runs and
previews need the signed-in owner: the implicit LAN owner gets 401).

GET  /api/settings/automation/4k-remux           {settings, libraries, status, history, grabs}
PUT  /api/settings/automation/4k-remux           partial settings -> the same view (400 {errors})
GET  /api/settings/automation/4k-remux/status    {status, history, preview}
POST /api/settings/automation/4k-remux/run       scan and grab now, even when switched off (prod parity) -> 202
POST /api/settings/automation/4k-remux/preview   scan without grabbing; nothing recorded -> 202
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict

from flask import Blueprint, request

from app.automation import remux4k

logger = logging.getLogger(__name__)
bp = Blueprint('remux_4k_native', __name__)
URL = '/api/settings/automation/4k-remux'
_start = {'fn': lambda target: threading.Thread(target=target, daemon=True, name='remux-4k-run').start()}


def _guard(write: bool = False):
    from app import movie_settings
    return movie_settings._guard(write)


def _out(body: Any, status: int = 200):
    from app import movie_settings
    return movie_settings._out(body, status)


def _safe(fn, default):
    try:
        return fn()
    except Exception:                                    # noqa: BLE001
        logger.exception('4K Remux: %s unreadable', getattr(fn, '__name__', 'list'))
        return default


def view() -> Dict[str, Any]:
    return {'settings': remux4k.settings(), 'libraries': _safe(remux4k.plex_sections, []),
            'status': remux4k.status(), 'history': remux4k.history(), 'grabs': remux4k.grabs(20)}


def live() -> Dict[str, Any]:
    return {'status': remux4k.status(), 'history': remux4k.history(), 'preview': remux4k.preview_state()}


@bp.route(URL, methods=['GET'])
def remux4k_get():
    return _guard() or _out(view())


@bp.route(URL, methods=['PUT'])
def remux4k_put():
    err = _guard(write=True)
    if err:
        return err
    try:
        remux4k.save(request.get_json(silent=True))
    except remux4k.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    return _out(view())


@bp.route(f'{URL}/status', methods=['GET'])
def remux4k_status():
    return _guard() or _out(live())


def _kick(kind: str):
    err = _guard(write=True)
    if err:
        return err
    if remux4k.running():
        return _out({'error': 'Already running'}, 409)
    if kind == 'preview':
        remux4k._preview.update(running=True)
    _start['fn'](lambda: remux4k.run(kind))
    return _out({'ok': True, 'started': True}, 202)


@bp.route(f'{URL}/run', methods=['POST'])
def remux4k_run():
    return _kick('manual')


@bp.route(f'{URL}/preview', methods=['POST'])
def remux4k_preview():
    return _kick('preview')
