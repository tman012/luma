"""v279 Settings > Automation > Trending TV routes (luma-test builds; owner only; changes and Run now need the
signed-in owner: the implicit LAN owner gets 401).

GET  /api/settings/automation/trending-tv          {settings, profiles, libraries, monitor, languages, status, history}
PUT  /api/settings/automation/trending-tv          partial settings -> the same view (400 {errors})
GET  /api/settings/automation/trending-tv/status   {status, history}
POST /api/settings/automation/trending-tv/run      run now, even when switched off (prod parity) -> 202
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict

from flask import Blueprint, request

from app.automation import ttv

logger = logging.getLogger(__name__)
bp = Blueprint('trending_tv_native', __name__)
URL = '/api/settings/automation/trending-tv'
_start = {'fn': lambda target: threading.Thread(target=target, daemon=True, name='trending-tv-run').start()}


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
        logger.exception('Trending TV: %s unreadable', getattr(fn, '__name__', 'list'))
        return default


def view() -> Dict[str, Any]:
    return {'settings': ttv.settings(), 'profiles': _safe(ttv.tv_profiles, []),
            'libraries': _safe(ttv.libraries, []), 'monitor': list(ttv.MONITOR),
            'languages': list(ttv.LANGUAGES), 'status': ttv.status(), 'history': ttv.history()}


@bp.route(URL, methods=['GET'])
def ttv_get():
    return _guard() or _out(view())


@bp.route(URL, methods=['PUT'])
def ttv_put():
    err = _guard(write=True)
    if err:
        return err
    try:
        ttv.save(request.get_json(silent=True))
    except ttv.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    return _out(view())


@bp.route(f'{URL}/status', methods=['GET'])
def ttv_status():
    return _guard() or _out({'status': ttv.status(), 'history': ttv.history()})


@bp.route(f'{URL}/run', methods=['POST'])
def ttv_run():
    err = _guard(write=True)
    if err:
        return err
    if ttv.running():
        return _out({'error': 'Already running'}, 409)
    _start['fn'](lambda: ttv.run('manual'))
    return _out({'ok': True, 'started': True}, 202)
