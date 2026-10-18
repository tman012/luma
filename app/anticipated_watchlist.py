"""
Anticipated Watchlist Automation

Background daemon that periodically fetches Trakt "anticipated" (unreleased)
movies and shows and PRE-ORDERS them into Radarr/Sonarr as monitored, so they
download automatically once released and sources appear. Skips titles already
owned in Plex or already present in Radarr/Sonarr.
"""

import json
import os
import threading
import time
import logging
import requests
from concurrent.futures import ThreadPoolExecutor
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
_INITIAL_DELAY = 75              # seconds before first cycle (offset from other automations)
_MAX_LOG_ENTRIES = 200


def _log_path() -> Path:
    """Resolve the persistent log file path (same dir as settings.json)."""
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data / 'anticipated_watchlist_log.json'
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent / 'anticipated_watchlist_log.json'
    return Path(__file__).resolve().parent.parent / 'data' / 'anticipated_watchlist_log.json'


def _read_log() -> List[Dict[str, Any]]:
    try:
        p = _log_path()
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        logger.warning(f'Failed to read anticipated watchlist log: {e}')
    return []


def _write_log(entries: List[Dict[str, Any]]) -> None:
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries[-_MAX_LOG_ENTRIES:], indent=None))
    except Exception as e:
        logger.warning(f'Failed to write anticipated watchlist log: {e}')


def _append_log(new_entries: List[Dict[str, Any]]) -> None:
    if not new_entries:
        return
    existing = _read_log()
    existing.extend(new_entries)
    _write_log(existing)



# # PERSIST_LAST_RUN:anticipated_watchlist
def _state_path() -> Path:
    """Persistent cycle status (survives restarts). Separate from the add-only
    log so a quiet cycle still updates 'Last ran' in the UI."""
    return _log_path().parent / 'anticipated_watchlist_state.json'


def _read_cycle_state() -> Dict[str, Any]:
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f'Failed to read anticipated_watchlist cycle state: {e}')
    return {}


def _write_cycle_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning(f'Failed to write anticipated_watchlist cycle state: {e}')


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


def start_anticipated_watchlist_thread() -> None:
    """Start the daemon loop thread."""
    from app import instance as _instance
    if not _instance.writes_allowed('anticipated_watchlist'):   # LUMA_INSTANCE=test: off unless opted in
        return None
    t = threading.Thread(target=_update_loop, daemon=True, name='anticipated-watchlist')
    t.start()
    logger.info('Anticipated watchlist automation started')


# ── Internal ──────────────────────────────────────────────────────

def _update_loop() -> None:
    time.sleep(_INITIAL_DELAY)
    while True:
        try:
            _run_cycle()
        except Exception as e:
            logger.error(f'Anticipated watchlist cycle error: {e}', exc_info=True)
        try:
            from app.settings_manager import SettingsManager
            hours = SettingsManager().get('automation.interval_hours', _DEFAULT_INTERVAL_HOURS)
        except Exception:
            hours = _DEFAULT_INTERVAL_HOURS
        time.sleep(max(1, int(hours)) * 3600)


def _run_cycle(force: bool = False) -> None:
    from app.settings_manager import SettingsManager

    settings = SettingsManager()

    if not force:
        enabled = _automation_enabled(settings, 'anticipated_watchlist')
        if not enabled:
            logger.debug('Anticipated watchlist disabled, skipping cycle')
            return

    with _lock:
        if _last_status['running']:
            logger.warning('Anticipated watchlist cycle already running, skipping')
            return
        _last_status['running'] = True

    try:
        result = _do_cycle(settings)
        with _lock:
            _last_status['last_run'] = datetime.utcnow().isoformat() + 'Z'
            _last_status['cycle_count'] += 1
            _last_status['last_result'] = result
            snap = {
                'last_run': _last_status['last_run'],
                'cycle_count': _last_status['cycle_count'],
                'last_result': {
                    k: v for k, v in (result or {}).items()
                    if k not in ('added_movies', 'added_shows', 'skipped_titles')
                },
            }
        _write_cycle_state(snap)
    finally:
        with _lock:
            _last_status['running'] = False


def _fetch_anticipated(media: str, client_id: str, limit: int) -> List[Dict[str, Any]]:
    """Fetch Trakt anticipated entries for 'movies' or 'shows'."""
    try:
        resp = requests.get(
            f'https://api.trakt.tv/{media}/anticipated',
            headers={
                'Content-Type': 'application/json',
                'trakt-api-version': '2',
                'trakt-api-key': client_id,
            },
            params={'page': '1', 'limit': str(limit), 'extended': 'full'},
            timeout=15,
        )
        if resp.status_code == 429:
            logger.warning('Trakt anticipated rate limited')
            return []
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f'Failed to fetch Trakt anticipated {media}: {e}')
        return []


def _tmdb_language(tmdb_id: int, tmdb_api_key: str) -> str:
    """Fetch a show's TMDb original_language (lowercased). Empty on failure."""
    try:
        resp = requests.get(
            f'https://api.themoviedb.org/3/tv/{tmdb_id}',
            params={'api_key': tmdb_api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            return (resp.json().get('original_language') or '').lower()
    except Exception:
        pass
    return ''


def _do_cycle(settings) -> Dict[str, Any]:
    """Core cycle: pre-order anticipated movies to Radarr and shows to Sonarr."""
    base = 'automation.anticipated_watchlist.'
    enabled_movies = settings.get(base + 'movies_enabled', True)
    enabled_shows = settings.get(base + 'shows_enabled', True)
    limit = settings.get(base + 'limit', 20)
    movie_profile_id = settings.get(base + 'movie_quality_profile_id', 0)
    tv_profile_id = settings.get(base + 'tv_quality_profile_id', 0)
    search_on_add = settings.get(base + 'search_on_add', True)
    allowed_languages = [str(l).lower() for l in (settings.get(base + 'tv_allowed_languages', ['en']) or [])]

    trakt_enabled = settings.get('apis.trakt.enabled', False)
    trakt_client_id = settings.get('apis.trakt.client_id', '')
    tmdb_api_key = settings.get('apis.tmdb.api_key', '')

    if not trakt_enabled or not trakt_client_id:
        return {'error': 'Trakt not enabled or client_id missing', 'added': 0}

    from app.plex_service import plex_service
    try:
        ownership = plex_service.build_ownership_index()
    except Exception as e:
        # An empty index makes every title look un-owned, so proceeding would
        # add things already in Plex. Skip the cycle instead.
        logger.warning(f'Could not build Plex ownership index: {e}')
        return {'error': f'Plex ownership index unavailable ({e}) — cycle skipped',
                'added': 0}
    if not ownership.get('movies') and not ownership.get('tv'):
        logger.warning('Plex ownership index is empty — skipping cycle rather '
                       'than treating the whole library as missing')
        return {'error': 'Plex ownership index empty — cycle skipped', 'added': 0}

    added_list: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        'added': 0, 'added_movies': 0, 'added_shows': 0,
        'skipped_owned': 0, 'skipped_existing': 0, 'skipped_language': 0, 'errors': [],
    }
    now_iso = datetime.utcnow().isoformat() + 'Z'

    # ── Movies → Radarr ───────────────────────────────────────────
    if enabled_movies:
        radarr_url = settings.get('radarr.url', '')
        radarr_key = settings.get('radarr.api_key', '')
        if radarr_url and radarr_key:
            from app.radarr_api import RadarrAPI
            radarr = RadarrAPI(radarr_url, radarr_key)
            profile_id = movie_profile_id
            if profile_id == 0:
                profiles = radarr.get_quality_profiles()
                profile_id = profiles[0]['id'] if profiles else 0
            root_folders = radarr.get_root_folders()
            root_folder_path = root_folders[0].get('path', '') if root_folders else ''
            plex_movies = ownership.get('movies', {})
            plex_movies_by_tmdb = ownership.get('movies_by_tmdb', {})

            if profile_id and root_folder_path:
                for entry in _fetch_anticipated('movies', trakt_client_id, limit):
                    movie = entry.get('movie', {})
                    ids = movie.get('ids', {})
                    tmdb_id = ids.get('tmdb')
                    title = movie.get('title', 'Unknown')
                    year = movie.get('year') or 0
                    if not tmdb_id:
                        continue
                    if tmdb_id in plex_movies_by_tmdb or (title and year and f"{title.lower()}|{year}" in plex_movies):
                        result['skipped_owned'] += 1
                        continue
                    if radarr.get_movie_by_tmdb_id(tmdb_id):
                        result['skipped_existing'] += 1
                        continue
                    try:
                        added = radarr.add_movie(
                            tmdb_id=tmdb_id, title=title, year=year or 2025,
                            quality_profile_id=profile_id, root_folder_path=root_folder_path,
                            monitored=True, search_for_movie=search_on_add,
                        )
                        if added:
                            added_list.append({'title': title, 'year': year, 'tmdb_id': tmdb_id,
                                               'added_at': now_iso, 'type': 'movie', 'source': 'anticipated'})
                            result['added_movies'] += 1
                            logger.info(f'Anticipated watchlist: pre-ordered movie {title} ({year}) to Radarr')
                        else:
                            result['errors'].append(f'{title} ({year}): Radarr add returned None')
                    except Exception as e:
                        result['errors'].append(f'{title} ({year}): {e}')
            else:
                result['errors'].append('Radarr profile/root folder unavailable')
        else:
            logger.debug('Anticipated watchlist: Radarr not configured, skipping movies')

    # ── Shows → Sonarr ────────────────────────────────────────────
    if enabled_shows:
        sonarr_url = settings.get('sonarr.url', '')
        sonarr_key = settings.get('sonarr.api_key', '')
        if sonarr_url and sonarr_key:
            from app.sonarr_api import SonarrAPI
            sonarr = SonarrAPI(sonarr_url, sonarr_key)
            profile_id = tv_profile_id
            if profile_id == 0:
                profiles = sonarr.get_quality_profiles()
                profile_id = profiles[0]['id'] if profiles else 0
            root_folders = sonarr.get_root_folders()
            root_folder_path = root_folders[0].get('path', '') if root_folders else ''
            plex_tv_by_tmdb = ownership.get('tv_by_tmdb', {})
            plex_tv_by_tvdb = ownership.get('tv_by_tvdb', {})

            if profile_id and root_folder_path:
                entries = _fetch_anticipated('shows', trakt_client_id, limit)
                # Resolve TMDb languages in parallel when a language gate is set.
                lang_map: Dict[int, str] = {}
                if allowed_languages and tmdb_api_key:
                    tmdb_ids = [e.get('show', {}).get('ids', {}).get('tmdb') for e in entries]
                    tmdb_ids = [t for t in tmdb_ids if t]
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        for tid, lang in zip(tmdb_ids, pool.map(lambda t: _tmdb_language(t, tmdb_api_key), tmdb_ids)):
                            lang_map[tid] = lang

                for entry in entries:
                    show = entry.get('show', {})
                    ids = show.get('ids', {})
                    tmdb_id = ids.get('tmdb')
                    tvdb_id = ids.get('tvdb')
                    title = show.get('title', 'Unknown')
                    year = show.get('year') or 0
                    if not tvdb_id:
                        continue
                    if allowed_languages and tmdb_id in lang_map and lang_map[tmdb_id] and lang_map[tmdb_id] not in allowed_languages:
                        result['skipped_language'] += 1
                        continue
                    if tmdb_id in plex_tv_by_tmdb or tvdb_id in plex_tv_by_tvdb:
                        result['skipped_owned'] += 1
                        continue
                    if sonarr.series_exists(tvdb_id, tmdb_id):
                        result['skipped_existing'] += 1
                        continue
                    try:
                        added = sonarr.add_series(
                            tvdb_id=tvdb_id, title=title, quality_profile_id=profile_id,
                            root_folder_path=root_folder_path, monitor='all',
                            search_for_missing=search_on_add,
                        )
                        if added:
                            added_list.append({'title': title, 'year': year, 'tmdb_id': tmdb_id,
                                               'tvdb_id': tvdb_id, 'added_at': now_iso, 'type': 'tv',
                                               'source': 'anticipated'})
                            result['added_shows'] += 1
                            logger.info(f'Anticipated watchlist: pre-ordered show {title} ({year}) to Sonarr')
                        else:
                            result['errors'].append(f'{title} ({year}): Sonarr add returned None')
                    except Exception as e:
                        result['errors'].append(f'{title} ({year}): {e}')
            else:
                result['errors'].append('Sonarr profile/root folder unavailable')
        else:
            logger.debug('Anticipated watchlist: Sonarr not configured, skipping shows')

    _append_log(added_list)
    result['added'] = len(added_list)
    logger.info(
        f"Anticipated watchlist cycle complete: added={result['added']} "
        f"(movies={result['added_movies']}, shows={result['added_shows']}), "
        f"skipped_owned={result['skipped_owned']}, skipped_existing={result['skipped_existing']}, "
        f"skipped_language={result['skipped_language']}, errors={len(result['errors'])}"
    )
    return result
