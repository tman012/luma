"""
Trending Watchlist Automation
Background daemon that periodically fetches Trakt trending movies
and adds qualifying ones to Radarr (skipping Plex-owned and existing Radarr entries).
"""

from app.err_text import public as _err_public
import json
import os
import threading
import time
import logging
import requests
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
_INITIAL_DELAY = 30              # seconds before first cycle
_MAX_LOG_ENTRIES = 200


def _log_path() -> Path:
    """Resolve the persistent log file path (same dir as settings.json)."""
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data / 'trending_watchlist_log.json'
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent / 'trending_watchlist_log.json'
    return Path(__file__).resolve().parent.parent / 'data' / 'trending_watchlist_log.json'


def _read_log() -> List[Dict[str, Any]]:
    try:
        p = _log_path()
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        logger.warning(f'Failed to read trending watchlist log: {e}')
    return []


def _write_log(entries: List[Dict[str, Any]]) -> None:
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries[-_MAX_LOG_ENTRIES:], indent=None))
    except Exception as e:
        logger.warning(f'Failed to write trending watchlist log: {e}')


def _append_log(new_entries: List[Dict[str, Any]]) -> None:
    if not new_entries:
        return
    existing = _read_log()
    existing.extend(new_entries)
    _write_log(existing)


def _state_path() -> Path:
    """Persistent status (survives container restarts). Separate from the
    add-only log so a quiet cycle still updates 'Last ran' in the UI."""
    return _log_path().parent / 'trending_watchlist_state.json'


def _read_state() -> Dict[str, Any]:
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f'Failed to read trending watchlist state: {e}')
    return {}


def _write_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning(f'Failed to write trending watchlist state: {e}')


_hydrated = False


def _hydrate_status() -> None:
    """Load last_run from disk once so the UI is not stuck on Never/stale add-log."""
    global _hydrated
    with _lock:
        if _hydrated:
            return
        _hydrated = True
        state = _read_state()
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


def start_trending_watchlist_thread() -> None:
    """Start the daemon loop thread."""
    from app import instance as _instance
    if not _instance.writes_allowed('trending_watchlist'):   # LUMA_INSTANCE=test: off unless opted in
        return None
    t = threading.Thread(target=_update_loop, daemon=True, name='trending-watchlist')
    t.start()
    logger.info('Trending watchlist automation started')


# ── Internal ──────────────────────────────────────────────────────

def _update_loop() -> None:
    time.sleep(_INITIAL_DELAY)
    while True:
        try:
            _run_cycle()
        except Exception as e:
            logger.error(f'Trending watchlist cycle error: {e}', exc_info=True)
        # Re-read interval each cycle so settings changes take effect
        try:
            from app.settings_manager import SettingsManager
            sm = SettingsManager()
            hours = sm.get('automation.interval_hours',
                    sm.get('automation.trending_watchlist.interval_hours', _DEFAULT_INTERVAL_HOURS))
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
        enabled = _automation_enabled(settings, 'trending_watchlist')
        if not enabled:
            logger.debug('Trending watchlist disabled, skipping cycle')
            return

    with _lock:
        if _last_status['running']:
            logger.warning('Trending watchlist cycle already running, skipping')
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
                # Drop bulky title lists from persisted status.
                'last_result': {
                    k: v for k, v in (result or {}).items()
                    if k not in ('added_movies', 'skipped_plex_titles',
                                 'skipped_radarr_titles')
                },
            }
        _write_state(snap)
    finally:
        with _lock:
            _last_status['running'] = False


def _do_cycle(settings, plex_service, RadarrAPI) -> Dict[str, Any]:
    """Core cycle logic. Returns a result summary dict."""
    # ── Read settings ─────────────────────────────────────────────
    limit = settings.get('automation.trending_watchlist.limit', 15)
    quality_profile_id = settings.get('automation.trending_watchlist.quality_profile_id', 0)
    search_on_add = settings.get('automation.trending_watchlist.search_on_add', True)

    radarr_url = settings.get('radarr.url', '')
    radarr_key = settings.get('radarr.api_key', '')
    trakt_enabled = settings.get('apis.trakt.enabled', False)
    trakt_client_id = settings.get('apis.trakt.client_id', '')

    if not radarr_url or not radarr_key:
        return {'error': 'Radarr not configured', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}
    if not trakt_enabled or not trakt_client_id:
        return {'error': 'Trakt not enabled or client_id missing', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

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

    # ── Fetch Trakt trending movies ──────────────────────────────
    try:
        resp = requests.get(
            'https://api.trakt.tv/movies/trending',
            headers={
                'Content-Type': 'application/json',
                'trakt-api-version': '2',
                'trakt-api-key': trakt_client_id,
            },
            params={'page': '1', 'limit': str(limit), 'extended': 'full'},
            timeout=15,
        )
        if resp.status_code == 429:
            retry_after = resp.headers.get('Retry-After', '30')
            logger.warning(f'Trakt rate limited, retry after {retry_after}s')
            return {'error': f'Trakt rate limited (retry in {retry_after}s)', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}
        resp.raise_for_status()
        raw_items = resp.json()  # list of {watchers, movie: {title, year, ids: {tmdb, imdb, ...}}}
    except Exception as e:
        logger.error(f'Failed to fetch Trakt trending movies: {e}')
        return {'error': _err_public(e, 'Trakt fetch failed', 'trending'), 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    # Normalize into simple dicts with tmdb_id
    trending_movies: List[Dict[str, Any]] = []
    for entry in raw_items:
        movie = entry.get('movie', {})
        ids = movie.get('ids', {})
        tmdb_id = ids.get('tmdb')
        if not tmdb_id:
            continue
        trending_movies.append({
            'tmdb_id': tmdb_id,
            'title': movie.get('title', 'Unknown'),
            'year': movie.get('year') or 0,
        })

    logger.info(f'Trending watchlist: fetched {len(trending_movies)} trending movies from Trakt (limit={limit})')

    # ── Build Plex ownership index ────────────────────────────────
    try:
        ownership = plex_service.build_ownership_index()
    except Exception as e:
        # An empty index makes every candidate look un-owned, so proceeding
        # would add films already in Plex. Skip the cycle instead — the next
        # one is minutes away and costs nothing to wait for.
        logger.warning(f'Could not build Plex ownership index: {e}')
        return {'error': _err_public(e, 'Plex ownership index unavailable — cycle skipped', 'trending'),
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

    for movie in trending_movies:
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
                })
                logger.info(f'Trending watchlist: added {title} ({year}) to Radarr')
            else:
                errors.append(f'{title} ({year}): Radarr add returned None')
        except Exception as e:
            errors.append(f'{title} ({year}): ' + _err_public(e, 'failed', 'trending'))
            logger.error(f'Trending watchlist: failed to add {title} ({year}): {e}')

    # ── Persist added movies to log file ──────────────────────────
    _append_log(added_list)

    total_checked = len(trending_movies)
    logger.info(
        f'Trending watchlist cycle complete: checked={total_checked}, '
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
