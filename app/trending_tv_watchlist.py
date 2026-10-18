"""
Trending TV Watchlist Automation
Background daemon that periodically fetches Trakt trending TV shows
and adds qualifying first-season-only shows to Sonarr
(skipping Plex-owned and existing Sonarr entries).
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
_INITIAL_DELAY = 45              # seconds before first cycle (offset from movie's 30s)
_MAX_LOG_ENTRIES = 200


def _log_path() -> Path:
    """Resolve the persistent log file path (same dir as settings.json)."""
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data / 'trending_tv_watchlist_log.json'
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent / 'trending_tv_watchlist_log.json'
    return Path(__file__).resolve().parent.parent / 'data' / 'trending_tv_watchlist_log.json'


def _read_log() -> List[Dict[str, Any]]:
    try:
        p = _log_path()
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        logger.warning(f'Failed to read trending TV watchlist log: {e}')
    return []


def _write_log(entries: List[Dict[str, Any]]) -> None:
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries[-_MAX_LOG_ENTRIES:], indent=None))
    except Exception as e:
        logger.warning(f'Failed to write trending TV watchlist log: {e}')


def _append_log(new_entries: List[Dict[str, Any]]) -> None:
    if not new_entries:
        return
    existing = _read_log()
    existing.extend(new_entries)
    _write_log(existing)



# # PERSIST_LAST_RUN:trending_tv_watchlist
def _state_path() -> Path:
    """Persistent cycle status (survives restarts). Separate from the add-only
    log so a quiet cycle still updates 'Last ran' in the UI."""
    return _log_path().parent / 'trending_tv_watchlist_state.json'


def _read_cycle_state() -> Dict[str, Any]:
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f'Failed to read trending_tv_watchlist cycle state: {e}')
    return {}


def _write_cycle_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning(f'Failed to write trending_tv_watchlist cycle state: {e}')


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


def start_trending_tv_watchlist_thread() -> None:
    """Start the daemon loop thread."""
    from app import instance as _instance
    if not _instance.writes_allowed('trending_tv_watchlist'):   # LUMA_INSTANCE=test: off unless opted in
        return None
    t = threading.Thread(target=_update_loop, daemon=True, name='trending-tv-watchlist')
    t.start()
    logger.info('Trending TV watchlist automation started')


# ── Internal ──────────────────────────────────────────────────────

def _update_loop() -> None:
    time.sleep(_INITIAL_DELAY)
    while True:
        try:
            _run_cycle()
        except Exception as e:
            logger.error(f'Trending TV watchlist cycle error: {e}', exc_info=True)
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
    from app.sonarr_api import SonarrAPI

    settings = SettingsManager()

    if not force:
        enabled = _automation_enabled(settings, 'trending_tv_watchlist')
        if not enabled:
            logger.debug('Trending TV watchlist disabled, skipping cycle')
            return

    with _lock:
        if _last_status['running']:
            logger.warning('Trending TV watchlist cycle already running, skipping')
            return
        _last_status['running'] = True

    try:
        result = _do_cycle(settings, plex_service, SonarrAPI)
        with _lock:
            _last_status['last_run'] = datetime.utcnow().isoformat() + 'Z'
            _last_status['cycle_count'] += 1
            _last_status['last_result'] = result
            snap = {
                'last_run': _last_status['last_run'],
                'cycle_count': _last_status['cycle_count'],
                'last_result': {
                    k: v for k, v in (result or {}).items()
                    if k not in ('added_shows', 'skipped_plex_titles', 'skipped_sonarr_titles')
                },
            }
        _write_cycle_state(snap)
    finally:
        with _lock:
            _last_status['running'] = False


def _fetch_show_meta(tmdb_id: int, tmdb_api_key: str) -> Dict[str, Any]:
    """Fetch season count + origin metadata from TMDb in one call.

    Returns {'seasons': int, 'language': str, 'countries': List[str]}.
    On error, seasons is -1 and language/countries are empty.
    """
    try:
        resp = requests.get(
            f'https://api.themoviedb.org/3/tv/{tmdb_id}',
            params={'api_key': tmdb_api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                'seasons': data.get('number_of_seasons', -1),
                'language': (data.get('original_language') or '').lower(),
                'countries': [c.upper() for c in (data.get('origin_country') or [])],
            }
    except Exception as e:
        logger.warning(f'TMDb meta check failed for {tmdb_id}: {e}')
    return {'seasons': -1, 'language': '', 'countries': []}


def _do_cycle(settings, plex_service, SonarrAPI) -> Dict[str, Any]:
    """Core cycle logic. Returns a result summary dict."""
    # ── Read settings ─────────────────────────────────────────────
    limit = settings.get('automation.trending_tv_watchlist.limit', 10)
    quality_profile_id = settings.get('automation.trending_tv_watchlist.quality_profile_id', 0)
    search_on_add = settings.get('automation.trending_tv_watchlist.search_on_add', True)
    # Origin filter: only auto-add shows whose TMDb original_language is in this
    # list. Defaults to English-only to keep out foreign titles (Japanese anime,
    # Korean dramas, etc.). Set to an empty list to disable the filter.
    allowed_languages = settings.get('automation.trending_tv_watchlist.allowed_languages', ['en'])
    allowed_languages = [str(l).lower() for l in (allowed_languages or [])]

    sonarr_url = settings.get('sonarr.url', '')
    sonarr_key = settings.get('sonarr.api_key', '')
    trakt_enabled = settings.get('apis.trakt.enabled', False)
    trakt_client_id = settings.get('apis.trakt.client_id', '')
    tmdb_api_key = settings.get('apis.tmdb.api_key', '')

    if not sonarr_url or not sonarr_key:
        return {'error': 'Sonarr not configured', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}
    if not trakt_enabled or not trakt_client_id:
        return {'error': 'Trakt not enabled or client_id missing', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}
    if not tmdb_api_key:
        return {'error': 'TMDb API key not configured (needed for season count)', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    # ── Init Sonarr client ────────────────────────────────────────
    sonarr = SonarrAPI(sonarr_url, sonarr_key)

    # Resolve quality profile
    if quality_profile_id == 0:
        profiles = sonarr.get_quality_profiles()
        if profiles:
            quality_profile_id = profiles[0]['id']
        else:
            return {'error': 'No Sonarr quality profiles found', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    # Resolve root folder
    root_folders = sonarr.get_root_folders()
    if not root_folders:
        return {'error': 'No Sonarr root folders configured', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}
    root_folder_path = root_folders[0].get('path', '')

    # ── Fetch Trakt trending TV shows ─────────────────────────────
    try:
        resp = requests.get(
            'https://api.trakt.tv/shows/trending',
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
        raw_items = resp.json()  # list of {watchers, show: {title, year, ids: {tmdb, tvdb, imdb, ...}}}
    except Exception as e:
        logger.error(f'Failed to fetch Trakt trending shows: {e}')
        return {'error': f'Trakt fetch failed: {e}', 'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    # Normalize into simple dicts — require both tmdb_id and tvdb_id
    trending_shows: List[Dict[str, Any]] = []
    for entry in raw_items:
        show = entry.get('show', {})
        ids = show.get('ids', {})
        tmdb_id = ids.get('tmdb')
        tvdb_id = ids.get('tvdb')
        if not tmdb_id or not tvdb_id:
            continue
        trending_shows.append({
            'tmdb_id': tmdb_id,
            'tvdb_id': tvdb_id,
            'title': show.get('title', 'Unknown'),
            'year': show.get('year') or 0,
        })

    logger.info(f'Trending TV watchlist: fetched {len(trending_shows)} trending shows from Trakt (limit={limit})')

    # ── TMDb filter — keep only 1-season shows in an allowed language ──
    def _check(show):
        meta = _fetch_show_meta(show['tmdb_id'], tmdb_api_key)
        return (show, meta)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_check, trending_shows))

    filtered_shows: List[Dict[str, Any]] = []
    skipped_seasons: List[str] = []
    skipped_language: List[str] = []
    for show, meta in results:
        count = meta['seasons']
        # Origin-language gate — drop foreign shows before the season check
        if allowed_languages and meta['language'] and meta['language'] not in allowed_languages:
            skipped_language.append(f"{show['title']} ({show['year']}): language '{meta['language']}'")
            continue
        if count == 1:
            filtered_shows.append(show)
        else:
            reason = f"{show['title']} ({show['year']}): {count} seasons" if count > 0 else f"{show['title']} ({show['year']}): TMDb lookup failed"
            skipped_seasons.append(reason)

    logger.info(
        f'Trending TV watchlist: {len(filtered_shows)} shows have 1 season, '
        f'{len(skipped_seasons)} filtered by seasons, {len(skipped_language)} filtered by language'
    )

    # ── Build Plex ownership index ────────────────────────────────
    try:
        ownership = plex_service.build_ownership_index()
    except Exception as e:
        # An empty index makes every show look un-owned, so proceeding would
        # re-add shows already in Plex. Skip the cycle instead.
        logger.warning(f'Could not build Plex ownership index: {e}')
        return {'error': f'Plex ownership index unavailable ({e}) — cycle skipped',
                'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}
    if not ownership.get('tv'):
        logger.warning('Plex ownership index is empty — skipping cycle rather '
                       'than treating the whole library as missing')
        return {'error': 'Plex ownership index empty — cycle skipped',
                'checked': 0, 'added': 0, 'skipped': 0, 'errors': []}

    plex_tv = ownership.get('tv', {})
    plex_tv_by_tmdb = ownership.get('tv_by_tmdb', {})
    plex_tv_by_tvdb = ownership.get('tv_by_tvdb', {})

    # ── Process each show ─────────────────────────────────────────
    added_list: List[Dict[str, Any]] = []
    skipped_plex: List[str] = []
    skipped_sonarr: List[str] = []
    errors: List[str] = []
    now_iso = datetime.utcnow().isoformat() + 'Z'

    for show in filtered_shows:
        tmdb_id = show['tmdb_id']
        tvdb_id = show['tvdb_id']
        title = show['title']
        year = show['year']

        # Check Plex ownership — by tmdb_id, tvdb_id, or title|year key
        in_plex = False
        if tmdb_id in plex_tv_by_tmdb:
            in_plex = True
        elif tvdb_id in plex_tv_by_tvdb:
            in_plex = True
        elif title and year:
            key = f"{title.lower()}|{year}"
            if key in plex_tv:
                in_plex = True

        if in_plex:
            skipped_plex.append(f'{title} ({year})')
            continue

        # Check Sonarr existence
        if sonarr.series_exists(tvdb_id, tmdb_id):
            skipped_sonarr.append(f'{title} ({year})')
            continue

        # Add to Sonarr
        try:
            result = sonarr.add_series(
                tvdb_id=tvdb_id,
                title=title,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder_path,
                monitor='all',
                search_for_missing=search_on_add,
            )
            if result:
                added_list.append({
                    'title': title,
                    'year': year,
                    'tmdb_id': tmdb_id,
                    'tvdb_id': tvdb_id,
                    'added_at': now_iso,
                    'type': 'tv',
                })
                logger.info(f'Trending TV watchlist: added {title} ({year}) to Sonarr')
            else:
                errors.append(f'{title} ({year}): Sonarr add returned None')
        except Exception as e:
            errors.append(f'{title} ({year}): {e}')
            logger.error(f'Trending TV watchlist: failed to add {title} ({year}): {e}')

    # ── Persist added shows to log file ───────────────────────────
    _append_log(added_list)

    total_checked = len(trending_shows)
    logger.info(
        f'Trending TV watchlist cycle complete: checked={total_checked}, '
        f'filtered_1season={len(filtered_shows)}, '
        f'added={len(added_list)}, skipped_plex={len(skipped_plex)}, '
        f'skipped_sonarr={len(skipped_sonarr)}, errors={len(errors)}'
    )

    return {
        'checked': total_checked,
        'filtered_first_season': len(filtered_shows),
        'added': len(added_list),
        'added_shows': added_list,
        'skipped_seasons': len(skipped_seasons),
        'skipped_seasons_detail': skipped_seasons,
        'skipped_language': len(skipped_language),
        'skipped_language_detail': skipped_language,
        'skipped_plex': len(skipped_plex),
        'skipped_plex_titles': skipped_plex,
        'skipped_sonarr': len(skipped_sonarr),
        'skipped_sonarr_titles': skipped_sonarr,
        'errors': errors,
    }
