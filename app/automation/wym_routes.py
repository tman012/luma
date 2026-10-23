"""v254 What you're missing routes: the Home rail's own endpoints and Settings > Automation.

Rail (prod 1b4ea74 main.py:6974-7042, moved here):
GET  /api/rails/what-youre-missing                       today's slate (signed-in user on minimal builds)
POST /api/rails/what-youre-missing/dismiss   {tmdb_id}   hide a film for good -> {ok, ..., rail}
POST /api/rails/what-youre-missing/restore   {tmdb_id}   undo a dismissal -> {ok, ..., dismissed}
POST /api/rails/what-youre-missing/refresh               rebuild the pool (minimal: in the background, 202)
GET  /api/rails/what-youre-missing/dismissed             debug list (not on the minimal allowlist)

Settings (minimal builds only, like Genre roulette):
GET  /api/settings/automation/what-youre-missing         {settings, lists, genres, languages, status, dismissed, ...}
PUT  /api/settings/automation/what-youre-missing         partial settings -> the same view (400 {errors})
GET  /api/settings/automation/what-youre-missing/status  {status} (the page polls it)

On minimal builds, dismiss, restore, refresh and settings changes need the owner signed in with a real session
(start_flow.require_signed_in_owner): the implicit LAN owner gets 401. Full builds keep prod's behavior.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app import daily_missing
from app.automation import wym_store

logger = logging.getLogger(__name__)
bp = Blueprint('whats_missing', __name__)
RAIL = '/api/rails/what-youre-missing'
URL = '/api/settings/automation/what-youre-missing'


def _minimal() -> bool:
    from app import minimal
    return minimal.enabled()


def _signed_in_owner():
    """None, or the 401/403 for a minimal build (full builds keep prod's open routes)."""
    if not _minimal():
        return None
    from app import tv_settings
    return tv_settings._signed_in_owner()          # start_flow.require_signed_in_owner (implicit LAN: 401)


def _signed_in():
    if not _minimal():
        return None
    from app import home_rails
    _user, err = home_rails._need_user()
    return err


def _guard(write: bool = False):
    from app import movie_settings
    return movie_settings._guard(write)


def _out(body: Any, status: int = 200):
    from app import movie_settings
    return movie_settings._out(body, status)


def _tmdb_id() -> Optional[int]:
    raw = (request.get_json(silent=True) or {}).get('tmdb_id')
    if isinstance(raw, bool):
        return None
    try:
        tid = int(raw)
    except (TypeError, ValueError):
        return None
    return tid if tid > 0 else None


def view() -> Dict[str, Any]:
    return {
        'settings': wym_store.settings(),
        'trakt_lists': [{'id': k, 'label': v} for k, v in wym_store.TRAKT_LISTS.items()],
        'tmdb_lists': [{'id': k, 'label': v} for k, v in wym_store.TMDB_LISTS.items()],
        'genres': list(wym_store.GENRES),
        'languages': [{'id': k, 'label': wym_store.LANGUAGE_NAMES[k]} for k in wym_store.LANGUAGES],
        'rebuild_hours': list(wym_store.REBUILD_HOURS),
        'movies_min': wym_store.MOVIES_MIN, 'movies_max': wym_store.MOVIES_MAX,
        'no_repeat_max': wym_store.NO_REPEAT_MAX,
        'imdb_active': daily_missing.omdb_on(),
        'sources': daily_missing.sources_sig(),
        'status': daily_missing.status(),
        'dismissed': daily_missing.list_dismissed()['items'],
    }


# ------------------------------------------------------------------ rail

@bp.route(RAIL, methods=['GET'])
def rail_get():
    err = _signed_in()
    if err:
        return err
    try:
        limit = request.args.get('limit', type=int)
        limit = max(1, min(limit, wym_store.MOVIES_MAX)) if limit else None
        return jsonify(daily_missing.get_rail(limit=limit))
    except Exception as e:                               # noqa: BLE001
        logger.error("What you're missing rail error: %s", type(e).__name__)
        return jsonify({'rail_id': daily_missing.RAIL_ID, 'title': daily_missing.TITLE, 'movies': [],
                        'enabled': True, 'empty': True, 'error': "Couldn't load"}), 500


@bp.route(RAIL + '/dismiss', methods=['POST'])
def rail_dismiss():
    err = _signed_in_owner()
    if err:
        return err
    tid = _tmdb_id()
    if tid is None:
        return jsonify({'ok': False, 'error': 'tmdb_id required'}), 400
    try:
        result = daily_missing.dismiss(tid)
        return jsonify({**result, 'rail': daily_missing.get_rail()})
    except Exception as e:                               # noqa: BLE001
        logger.error("What you're missing dismiss error: %s", type(e).__name__)
        return jsonify({'ok': False, 'error': "Couldn't dismiss"}), 500


@bp.route(RAIL + '/restore', methods=['POST'])
def rail_restore():
    err = _signed_in_owner()
    if err:
        return err
    tid = _tmdb_id()
    if tid is None:
        return jsonify({'ok': False, 'error': 'tmdb_id required'}), 400
    try:
        result = daily_missing.restore(tid)
        return _out({**result, 'dismissed': daily_missing.list_dismissed()['items']})
    except Exception as e:                               # noqa: BLE001
        logger.error("What you're missing restore error: %s", type(e).__name__)
        return jsonify({'ok': False, 'error': "Couldn't restore"}), 500


@bp.route(RAIL + '/refresh', methods=['POST'])
def rail_refresh():
    err = _signed_in_owner()
    if err:
        return err
    if _minimal():
        if not daily_missing.rebuild_async():
            return _out({'ok': False, 'error': 'Already rebuilding', 'status': daily_missing.status()}, 409)
        return _out({'ok': True, 'status': daily_missing.status()}, 202)
    try:                                                 # prod: synchronous (ops / smoke)
        payload = daily_missing.refresh_now()
        return jsonify({'ok': True, 'pool_size': len(payload.get('candidates') or []),
                        'built_at_iso': payload.get('built_at_iso'), 'note': payload.get('note')})
    except Exception as e:                               # noqa: BLE001
        logger.error("What you're missing refresh error: %s", type(e).__name__)
        return jsonify({'ok': False, 'error': str(e) if isinstance(e, daily_missing.PoolError) else "Couldn't rebuild"}), 500


@bp.route(RAIL + '/dismissed', methods=['GET'])
def rail_dismissed():
    err = _signed_in()
    if err:
        return err
    try:
        return jsonify(daily_missing.list_dismissed())
    except Exception as e:                               # noqa: BLE001
        logger.error("What you're missing dismissed list error: %s", type(e).__name__)
        return jsonify({'tmdb_ids': [], 'count': 0, 'error': "Couldn't load"}), 500


# ------------------------------------------------------------------ settings

@bp.route(URL, methods=['GET'])
def wym_get():
    return _guard() or _out(view())


@bp.route(URL, methods=['PUT'])
def wym_put():
    err = _guard(write=True)
    if err:
        return err
    try:
        wym_store.save(request.get_json(silent=True))
    except wym_store.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    daily_missing.wake()
    return _out(view())


@bp.route(URL + '/status', methods=['GET'])
def wym_status():
    return _guard() or _out({'status': daily_missing.status()})
