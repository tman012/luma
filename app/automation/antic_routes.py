"""v253 Settings > Automation > Anticipated routes (luma-test builds; owner only; changes and Run now need the
signed-in owner: the implicit LAN owner gets 401).

GET  /api/settings/automation/anticipated          {settings, profiles, movie_profiles, libraries, monitor, languages,
                                                    status, history}
PUT  /api/settings/automation/anticipated          partial settings -> the same view (400 {errors})
GET  /api/settings/automation/anticipated/status   {status, history}
POST /api/settings/automation/anticipated/run      run now, even when switched off (prod parity) -> 202
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict

from flask import Blueprint, request

from app.automation import antic

logger = logging.getLogger(__name__)
bp = Blueprint('anticipated_native', __name__)
URL = '/api/settings/automation/anticipated'
_start = {'fn': lambda target: threading.Thread(target=target, daemon=True, name='anticipated-run').start()}


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
        logger.exception('Anticipated: %s unreadable', getattr(fn, '__name__', 'list'))
        return default


def view() -> Dict[str, Any]:
    return {'settings': antic.settings(), 'profiles': _safe(antic.tv_profiles, []),
            'movie_profiles': _safe(antic.movie_profiles, []), 'libraries': _safe(antic.libraries, []),
            'monitor': list(antic.MONITOR), 'languages': list(antic.LANGUAGES),
            'status': antic.status(), 'history': antic.history()}


@bp.route(URL, methods=['GET'])
def antic_get():
    return _guard() or _out(view())


@bp.route(URL, methods=['PUT'])
def antic_put():
    err = _guard(write=True)
    if err:
        return err
    try:
        antic.save(request.get_json(silent=True))
    except antic.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    return _out(view())


@bp.route(f'{URL}/status', methods=['GET'])
def antic_status():
    return _guard() or _out({'status': antic.status(), 'history': antic.history()})


@bp.route(f'{URL}/run', methods=['POST'])
def antic_run():
    err = _guard(write=True)
    if err:
        return err
    if antic.running():
        return _out({'error': 'Already running'}, 409)
    _start['fn'](lambda: antic.run('manual'))
    return _out({'ok': True, 'started': True}, 202)
