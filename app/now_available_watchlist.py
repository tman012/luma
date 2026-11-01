"""
Now Available Watchlist Automation
Background daemon that periodically pulls movies from the explore cache
(Prowlarr-indexed tracker movies) and adds qualifying ones to Radarr
(skipping Plex-owned and existing Radarr entries).
"""

from app.err_text import public as _err_public
import json
import os
import threading
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from app.automation_features import is_enabled as _automation_enabled

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────
_last_status: Dict[str, Any] = {
    'running': False,
    'last_run': None,
    'cycle_count': 0,
    'last_result': None,
}
_lock = threading.Lock()
_DEFAULT_INTERVAL_HOURS = 2
_INITIAL_DELAY = 60              # seconds before first cycle (offset from other automations)
_MAX_LOG_ENTRIES = 200


def _log_path() -> Path:
    """Resolve the persistent log file path (same dir as settings.json)."""
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data / 'now_available_watchlist_log.json'
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent / 'now_available_watchlist_log.json'
    return Path(__file__).resolve().parent.parent / 'data' / 'now_available_watchlist_log.json'


def _read_log() -> List[Dict[str, Any]]:
    try:
        p = _log_path()
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        logger.warning(f'Failed to read now available watchlist log: {e}')
    return []


def _write_log(entries: List[Dict[str, Any]]) -> None:
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries[-_MAX_LOG_ENTRIES:], indent=None))
    except Exception as e:
        logger.warning(f'Failed to write now available watchlist log: {e}')


def _append_log(new_entries: List[Dict[str, Any]]) -> None:
    if not new_entries:
        return
    existing = _read_log()
    existing.extend(new_entries)
    _write_log(existing)



# # PERSIST_LAST_RUN:now_available_watchlist
def _state_path() -> Path:
    """Persistent cycle status (survives restarts). Separate from the add-only
    log so a quiet cycle still updates 'Last ran' in the UI."""
    return _log_path().parent / 'now_available_watchlist_state.json'


def _read_cycle_state() -> Dict[str, Any]:
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f'Failed to read now_available_watchlist cycle state: {e}')
    return {}


def _write_cycle_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning(f'Failed to write now_available_watchlist cycle state: {e}')


_hydrated = False


def _hydrate_status() -> None:
    """Load last_run from disk once so the UI is not stuck on Never after restart."""
    global _hydrated
    with _lock:
        if _hydrated:
            return
        _hydrated = True
        state = _read_cycle_state()
        if state.get('last_run') and not _last_status.get('last_run'):
            _last_status['last_run'] = state['last_run']
            _last_status['last_result'] = state.get('last_result')
            _last_status['cycle_count'] = int(state.get('cycle_count') or 0)


def get_status() -> Dict[str, Any]:
    """Return a snapshot of the last run status."""
    _hydrate_status()
    with _lock:
        return dict(_last_status)


def get_log() -> List[Dict[str, Any]]:
    """Return the persistent add-log (newest first)."""
    entries = _read_log()
    entries.reverse()
    return entries


def trigger_manual_run() -> None:
    """Fire a one-off cycle in a background thread (bypasses enabled check)."""
    t = threading.Thread(target=lambda: _run_cycle(force=True), daemon=True)
    t.start()


def start_now_available_watchlist_thread() -> None:
    """Start the daemon loop thread."""
    from app import instance as _instance
    if not _instance.writes_allowed('now_available_watchlist'):   # LUMA_INSTANCE=test: off unless opted in
        return None
    t = threading.Thread(target=_update_loop, daemon=True, name='now-available-watchlist')
    t.start()
    logger.info('Now available watchlist automation started')


# ── Internal ──────────────────────────────────────────────────────

def _update_loop() -> None:
    time.sleep(_INITIAL_DELAY)
    while True:
        try:
            _run_cycle()
        except Exception as e:
            logger.error(f'Now available watchlist cycle error: {e}', exc_info=True)
        # Re-read interval each cycle so settings changes take effect
        try:
            from app.settings_manager import SettingsManager
            hours = SettingsManager().get('automation.interval_hours',
                    SettingsManager().get('automation.trending_watchlist.interval_hours', _DEFAULT_INTERVAL_HOURS))
        except Exception:
            hours = _DEFAULT_INTERVAL_HOURS
        time.sleep(max(1, int(hours)) * 3600)


def _run_cycle(force: bool = False) -> None:
    # Lazy imports to avoid circular dependencies at module load
    from app.settings_manager import SettingsManager
    from app.plex_service import plex_service
    from app.radarr_api import RadarrAPI

    settings = SettingsManager()

    if not force:
        enabled = _automation_enabled(settings, 'now_available_watchlist')
        if not enabled:
            logger.debug('Now available watchlist disabled, skipping cycle')
            return

    with _lock:
        if _last_status['running']:
            logger.warning('Now available watchlist cycle already running, skipping')
            return
        _last_status['running'] = True

    try:
        result = _do_cycle(settings, plex_service, RadarrAPI)
        with _lock:
            _last_status['last_run'] = datetime.utcnow().isoformat() + 'Z'
            _last_status['cycle_count'] += 1
            _last_status['last_result'] = result
            snap = {
                'last_run': _last_status['last_run'],
                'cycle_count': _last_status['cycle_count'],
                'last_result': {
                    k: v for k, v in (result or {}).items()
                    if k not in ('added_movies', 'skipped_plex_titles', 'skipped_radarr_titles')
                },
            }
        _write_cycle_state(snap)
    finally:
        with _lock:
            _last_status['running'] = False


def _do_cycle(settings, plex_service, RadarrAPI) -> Dict[str, Any]:
    """Core cycle logic. Returns a result summary dict."""
    # ── Read settings ─────────────────────────────────────────────
    limit = settings.get('automation.now_available_watchlist.limit', 15)
    quality_profile_id = settings.get('automation.now_available_watchlist.quality_profile_id', 0)
    search_on_add = settings.get('automation.now_available_watchlist.search_on_add', True)

    radarr_url = settings.get('radarr.url', '')
    radarr_key = settings.get('radarr.api_key', '')

    if not radarr_url or not radarr_key:
        return {'error': 'Radarr not configured', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    # ── Fetch movies from explore cache ───────────────────────────
    try:
        from app.explore_cache import explore_cache
        cached = explore_cache.get_cached_movies(limit=500)
    except Exception as e:
        logger.error(f'Failed to read explore cache: {e}')
        return {'error': _err_public(e, 'Explore cache read failed', 'now_available'), 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    if not cached:
        return {'error': 'Explore cache is empty', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    # Filter: must have tmdb_id AND a poster
    candidates: List[Dict[str, Any]] = []
    for m in cached:
        tmdb_id = m.get('tmdb_id')
        if not tmdb_id:
            continue
        has_poster = bool(m.get('poster') or m.get('poster_url') or m.get('poster_path'))
        if not has_poster:
            continue
        candidates.append({
            'tmdb_id': int(tmdb_id),
            'title': m.get('title', 'Unknown'),
            'year': m.get('year') or 0,
            'seeders': m.get('seeders', 0) or 0,
        })

    # Sort by seeders DESC, take up to limit
    candidates.sort(key=lambda x: x['seeders'], reverse=True)
    candidates = candidates[:limit]

    logger.info(f'Now available watchlist: {len(candidates)} candidates from explore cache (limit={limit})')

    # ── Init Radarr client ────────────────────────────────────────
    radarr = RadarrAPI(radarr_url, radarr_key)

    # Resolve quality profile
    if quality_profile_id == 0:
        profiles = radarr.get_quality_profiles()
        if profiles:
            quality_profile_id = profiles[0]['id']
        else:
            return {'error': 'No Radarr quality profiles found', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    # Resolve root folder
    root_folders = radarr.get_root_folders()
    if not root_folders:
        return {'error': 'No Radarr root folders configured', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}
    root_folder_path = root_folders[0].get('path', '')

    # ── Build Plex ownership index ────────────────────────────────
    try:
        ownership = plex_service.build_ownership_index()
    except Exception as e:
        # An empty index makes every candidate look un-owned, so proceeding
        # would add films already in Plex. Skip the cycle instead — the next
        # one is minutes away and costs nothing to wait for.
        logger.warning(f'Could not build Plex ownership index: {e}')
        return {'error': _err_public(e, 'Plex ownership index unavailable — cycle skipped', 'now_available'),
                'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}
    if not ownership.get('movies'):
        logger.warning('Plex ownership index is empty — skipping cycle rather '
                       'than treating the whole library as missing')
        return {'error': 'Plex ownership index empty — cycle skipped',
                'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    plex_movies = ownership.get('movies', {})

    # ── Process each movie ────────────────────────────────────────
    added_list: List[Dict[str, Any]] = []
    skipped_plex: List[str] = []
    skipped_radarr: List[str] = []
    errors: List[str] = []
    now_iso = datetime.utcnow().isoformat() + 'Z'

    for movie in candidates:
        tmdb_id = movie['tmdb_id']
        title = movie['title']
        year = movie['year']

        # Check Plex ownership — match by tmdb_id in ownership values, fallback to title|year key
        in_plex = False
        for entry in plex_movies.values():
            if entry.get('tmdb_id') and int(entry['tmdb_id']) == tmdb_id:
                in_plex = True
                break
        if not in_plex and title and year:
            key = f"{title.lower()}|{year}"
            if key in plex_movies:
                in_plex = True

        if in_plex:
            skipped_plex.append(f'{title} ({year})')
            continue

        # Check Radarr existence
        existing = radarr.get_movie_by_tmdb_id(tmdb_id)
        if existing:
            skipped_radarr.append(f'{title} ({year})')
            continue

        # Add to Radarr
        try:
            result = radarr.add_movie(
                tmdb_id=tmdb_id,
                title=title,
                year=year or 2024,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder_path,
                monitored=True,
                search_for_movie=search_on_add,
            )
            if result:
                added_list.append({
                    'title': title,
                    'year': year,
                    'tmdb_id': tmdb_id,
                    'added_at': now_iso,
                    'type': 'now_available',
                })
                logger.info(f'Now available watchlist: added {title} ({year}) to Radarr')
            else:
                errors.append(f'{title} ({year}): Radarr add returned None')
        except Exception as e:
            errors.append(f'{title} ({year}): ' + _err_public(e, 'failed', 'now_available'))
            logger.error(f'Now available watchlist: failed to add {title} ({year}): {e}')

    # ── Persist added movies to log file ──────────────────────────
    _append_log(added_list)

    total_checked = len(candidates)
    logger.info(
        f'Now available watchlist cycle complete: checked={total_checked}, '
        f'added={len(added_list)}, skipped_plex={len(skipped_plex)}, '
        f'skipped_radarr={len(skipped_radarr)}, errors={len(errors)}'
    )

    return {
        'checked': total_checked,
        'added': len(added_list),
        'added_movies': added_list,
        'skipped_plex': len(skipped_plex),
        'skipped_plex_titles': skipped_plex,
        'skipped_radarr': len(skipped_radarr),
        'skipped_radarr_titles': skipped_radarr,
        'errors': errors,
    }
