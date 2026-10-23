"""v252 Settings > Automation > Genre Roulette routes (luma-test builds; owner only; changes, runs and
previews need the signed-in owner: the implicit LAN owner gets 401).

GET  /api/settings/automation/genre-roulette           {settings, genres, profiles, imdb_active, status, history}
PUT  /api/settings/automation/genre-roulette           partial settings -> the same view (400 {errors})
GET  /api/settings/automation/genre-roulette/status    {status, history, preview}
POST /api/settings/automation/genre-roulette/run       start a run now (the Dry run setting applies) -> 202
POST /api/settings/automation/genre-roulette/preview   start a dry run that is not recorded -> 202
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from flask import Blueprint, request

from app.automation import gr_engine, gr_scheduler, gr_select, gr_store

logger = logging.getLogger(__name__)
bp = Blueprint('genre_roulette_native', __name__)
URL = '/api/settings/automation/genre-roulette'
HISTORY = 30
_preview: Dict[str, Any] = {'running': False, 'result': None}
_plock = threading.Lock()
_start = {'fn': lambda target: threading.Thread(target=target, daemon=True, name='genre-roulette-run').start()}


def _guard(write: bool = False):
    from app import movie_settings
    return movie_settings._guard(write)


def _out(body: Any, status: int = 200):
    from app import movie_settings
    return movie_settings._out(body, status)


def _profiles():
    try:
        from app import movie_settings as ms
        return [{'id': p['id'], 'name': p['name'], 'default': bool(p.get('default'))} for p in ms.profiles()]
    except Exception:                                    # noqa: BLE001
        logger.exception('Genre Roulette: profiles unreadable')
        return []


def _history():
    return [{k: r.get(k) for k in ('id', 'started_at', 'finished_at', 'kind', 'dry', 'status', 'wanted', 'grabbed',
                                   'stop_reason', 'genres', 'picks', 'failures', 'skips', 'error')}
            for r in gr_store.runs(HISTORY)]


def _preview_view() -> Dict[str, Any]:
    with _plock:
        return {'running': _preview['running'], 'result': _preview['result']}


def view() -> Dict[str, Any]:
    cfg = gr_store.settings()
    return {'settings': cfg, 'genres': list(gr_store.GENRES), 'profiles': _profiles(),
            'imdb_active': gr_select.omdb_key_set(), 'status': gr_scheduler.status(), 'history': _history()}


@bp.route(URL, methods=['GET'])
def gr_get():
    return _guard() or _out(view())


@bp.route(URL, methods=['PUT'])
def gr_put():
    err = _guard(write=True)
    if err:
        return err
    try:
        gr_store.save(request.get_json(silent=True))
    except gr_store.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    return _out(view())


@bp.route(f'{URL}/status', methods=['GET'])
def gr_status():
    return _guard() or _out({'status': gr_scheduler.status(), 'history': _history(), 'preview': _preview_view()})


@bp.route(f'{URL}/run', methods=['POST'])
def gr_run():
    err = _guard(write=True)
    if err:
        return err
    if gr_engine.running().get('running'):
        return _out({'error': 'Already running'}, 409)
    _start['fn'](lambda: gr_engine.run('manual'))
    return _out({'ok': True, 'started': True}, 202)


@bp.route(f'{URL}/preview', methods=['POST'])
def gr_preview():
    err = _guard(write=True)
    if err:
        return err
    with _plock:
        if _preview['running']:
            return _out({'error': 'Already running'}, 409)
        _preview.update(running=True, result=None)

    def go(result: Optional[Dict[str, Any]] = None):
        try:
            result = gr_engine.run('preview')
        except Exception:                                # noqa: BLE001
            logger.exception('Genre Roulette preview failed')
            result = {'error': 'Preview failed', 'status': 'error'}
        finally:
            with _plock:
                _preview.update(running=False, result=result)
    _start['fn'](go)
    return _out({'ok': True, 'started': True}, 202)
