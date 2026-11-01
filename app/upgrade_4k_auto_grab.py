"""
4K Remux Auto-Grab Automation

Aggressive 4K upgrade pipeline. Scans the Prowlarr "latest" feed and grabs any
2160p Remux release that crosses the seeder threshold inside the age window —
regardless of whether the movie already exists in the user's standard Movies
library (or any other non-4K library). The ONLY skip condition is "movie is
already in the configured 4K Plex section."

Concretely:
  • Filter releases to quality == "2160p Remux" (strict).
  • Require seeders >= min_seeders (default 100).
  • Require age <= max_age_hours (default 24).
  • Build a set of tmdb_ids in the configured Plex 4K section.
  • For each remaining candidate:
      - Skip if movie tmdb_id is in the 4K section.
      - Add to Radarr if missing (monitored, with the configured 4K profile).
      - If the movie exists with a different profile, update it to the 4K
        profile so Radarr accepts the 2160p Remux push.
      - Push the release via Radarr's push_release endpoint.
  • Persist grabbed guids + tmdb_ids to dedup across scans.
  • Append every grab to a log file that the unified Activity Log surfaces.
"""

from app.err_text import public as _err_public
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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
_DEFAULT_INTERVAL_MINUTES = 15
_DEFAULT_MIN_SEEDERS = 100
_DEFAULT_MAX_AGE_HOURS = 24
_INITIAL_DELAY = 50      # offset slightly from latest_auto_grab's 45s start
_MAX_LOG_ENTRIES = 200


# ── File paths ────────────────────────────────────────────────────

def _data_dir() -> Path:
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent
    return Path(__file__).resolve().parent.parent / 'data'


def _log_path() -> Path:
    return _data_dir() / 'upgrade_4k_auto_grab_log.json'


def _state_path() -> Path:
    return _data_dir() / 'upgrade_4k_auto_grab_state.json'


# ── Log + state IO ────────────────────────────────────────────────

def _read_log() -> List[Dict[str, Any]]:
    try:
        p = _log_path()
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        logger.warning(f'Failed to read 4K auto-grab log: {e}')
    return []


def _write_log(entries: List[Dict[str, Any]]) -> None:
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries[-_MAX_LOG_ENTRIES:], indent=None))
    except Exception as e:
        logger.warning(f'Failed to write 4K auto-grab log: {e}')


def _append_log(new_entries: List[Dict[str, Any]]) -> None:
    if not new_entries:
        return
    existing = _read_log()
    existing.extend(new_entries)
    _write_log(existing)


def _read_state() -> Dict[str, Any]:
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f'Failed to read 4K auto-grab state: {e}')
    return {'grabbed_guids': [], 'grabbed_tmdb_ids': []}


def _write_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(state.get('grabbed_guids'), list):
            state['grabbed_guids'] = state['grabbed_guids'][-5000:]
        if isinstance(state.get('grabbed_tmdb_ids'), list):
            state['grabbed_tmdb_ids'] = state['grabbed_tmdb_ids'][-5000:]
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning(f'Failed to write 4K auto-grab state: {e}')


# ── Public API ────────────────────────────────────────────────────


# # PERSIST_LAST_RUN:upgrade_4k_auto_grab
_hydrated = False


def _hydrate_status() -> None:
    """Load last_run from the existing state file so restarts keep 'Last ran'."""
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
    _hydrate_status()
    with _lock:
        return dict(_last_status)


def get_log() -> List[Dict[str, Any]]:
    entries = _read_log()
    entries.reverse()
    return entries


def trigger_manual_run() -> None:
    t = threading.Thread(target=lambda: _run_cycle(force=True), daemon=True)
    t.start()


def start_upgrade_4k_auto_grab_thread() -> None:
    from app import instance as _instance
    if not _instance.writes_allowed('upgrade_4k'):   # LUMA_INSTANCE=test: off unless opted in
        return None
    t = threading.Thread(target=_update_loop, daemon=True, name='upgrade-4k-auto-grab')
    t.start()
    logger.info('4K Remux auto-grab automation started')


# ── Internal loop ─────────────────────────────────────────────────

def _update_loop() -> None:
    time.sleep(_INITIAL_DELAY)
    while True:
        try:
            _run_cycle()
        except Exception as e:
            logger.error(f'4K auto-grab cycle error: {e}', exc_info=True)
        try:
            from app.settings_manager import SettingsManager
            mins = SettingsManager().get(
                'automation.upgrade_4k_auto_grab.scan_interval_minutes',
                _DEFAULT_INTERVAL_MINUTES,
            )
        except Exception:
            mins = _DEFAULT_INTERVAL_MINUTES
        time.sleep(max(1, int(mins)) * 60)


def _run_cycle(force: bool = False) -> None:
    from app.settings_manager import SettingsManager
    settings = SettingsManager()

    if not force:
        enabled = _automation_enabled(settings, 'upgrade_4k_auto_grab')
        if not enabled:
            logger.debug('4K auto-grab disabled, skipping cycle')
            return

    with _lock:
        if _last_status['running']:
            logger.warning('4K auto-grab cycle already running, skipping')
            return
        _last_status['running'] = True

    try:
        result = _do_cycle(settings)
        with _lock:
            _last_status['last_run'] = datetime.utcnow().isoformat() + 'Z'
            _last_status['cycle_count'] += 1
            _last_status['last_result'] = result
            lr = _last_status['last_run']
            cc = _last_status['cycle_count']
            slim = {
                k: v for k, v in (result or {}).items()
                if k not in ('grabbed', 'skipped', 'candidates')
            }
        try:
            state = _read_state()
            state['last_run'] = lr
            state['cycle_count'] = cc
            state['last_result'] = slim
            _write_state(state)
        except Exception as e:
            logger.warning(f'upgrade_4k_auto_grab: could not persist last_run: {e}')
    finally:
        with _lock:
            _last_status['running'] = False


def _pub_date_age_hours(pub_date_str: str) -> float:
    if not pub_date_str:
        return float('inf')
    try:
        clean = pub_date_str.replace('Z', '+00:00')
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0.0, delta.total_seconds() / 3600.0)
    except Exception:
        return float('inf')


def _is_2160p_remux(quality: str) -> bool:
    """True iff the quality label denotes a 2160p Remux. Accepts the canonical
    "2160p Remux" the latest endpoint emits, plus a couple of common variants."""
    if not quality:
        return False
    up = quality.upper()
    return '2160P' in up and 'REMUX' in up


def _load_4k_section_tmdb_ids(plex_service, section_id: int) -> Optional[Set[int]]:
    """tmdb_ids of everything in the 4K Plex section, or None if unknowable.

    This set is the only thing standing between the automation and re-grabbing
    films already owned in 4K, and every candidate it doesn't skip also gets
    its Radarr quality profile overwritten. An empty set therefore cannot mean
    "skip nothing" — with Plex down that is an instruction to grab the entire
    sweep. Returning None lets the caller refuse to run instead, which is what
    movie_night does when its ownership data reads empty.

    A configured section that legitimately holds zero movies is indistinguishable
    from a failed read, and is treated the same way: an idle automation with a
    clear reason beats an unattended grab storm.
    """
    if not section_id:
        return None
    tmdb_ids: Set[int] = set()
    offset = 0
    page_size = 200
    safety_pages = 50  # upper bound — 50 pages × 200 = 10,000 movies
    pages = 0
    try:
        while pages < safety_pages:
            result = plex_service.get_all_movies(section_id, limit=page_size,
                                                 offset=offset, timeout=30)
            for item in result.get('movies', []) or []:
                tid = item.get('tmdb_id')
                if tid:
                    try:
                        tmdb_ids.add(int(tid))
                    except (ValueError, TypeError):
                        pass
            if not result.get('has_more', False):
                break
            offset += page_size
            pages += 1
    except Exception as e:
        logger.warning(f'Failed to load 4K section {section_id} from Plex: {e}')
        return None
    return tmdb_ids or None


def _do_cycle(settings) -> Dict[str, Any]:
    from app.radarr_api import RadarrAPI
    from app.plex_service import plex_service
    import requests

    min_seeders = settings.get('automation.upgrade_4k_auto_grab.min_seeders', _DEFAULT_MIN_SEEDERS)
    max_age_hours = settings.get('automation.upgrade_4k_auto_grab.max_age_hours', _DEFAULT_MAX_AGE_HOURS)
    quality_profile_id = settings.get('automation.upgrade_4k_auto_grab.quality_profile_id', 0)
    section_4k_id = settings.get('automation.upgrade_4k_auto_grab.plex_4k_section_id', 0)

    radarr_url = settings.get('radarr.url', '')
    radarr_key = settings.get('radarr.api_key', '')
    if not radarr_url or not radarr_key:
        return {'error': 'Radarr not configured', 'checked': 0, 'grabbed': 0, 'skipped_in_4k': 0,
                'skipped_rejected': 0, 'errors': []}
    if not section_4k_id:
        return {'error': '4K Plex section not configured', 'checked': 0, 'grabbed': 0,
                'skipped_in_4k': 0, 'skipped_rejected': 0, 'errors': []}

    radarr = RadarrAPI(radarr_url, radarr_key)

    # Resolve quality profile — the user MUST pick one explicitly because this
    # automation will overwrite the movie's existing profile.
    if not quality_profile_id:
        return {'error': 'Quality profile must be configured for 4K auto-grab',
                'checked': 0, 'grabbed': 0, 'skipped_in_4k': 0, 'skipped_rejected': 0, 'errors': []}

    root_folders = radarr.get_root_folders()
    if not root_folders:
        return {'error': 'No Radarr root folders configured', 'checked': 0, 'grabbed': 0,
                'skipped_in_4k': 0, 'skipped_rejected': 0, 'errors': []}
    root_folder_path = root_folders[0].get('path', '')

    # ── Fetch latest releases via the *targeted* 2160p Remux sweep ──
    # The default browse mode at /api/indexer/latest is dominated by high-
    # volume indexers (IPT), which pushes BHD/PHD Remux uploads past the
    # 300-item cap. sweep=remux2160p runs Prowlarr keyword searches for
    # "2160p REMUX", "UHD REMUX", etc. and surfaces those reliably.
    try:
        # In-process call: no HTTP round trip through the auth layer.
        from app.main import latest_releases
        payload = {'results': latest_releases({'limit': 600, 'sweep': 'remux2160p'})}
    except Exception as e:
        logger.error(f'4K auto-grab: failed to fetch /api/indexer/latest sweep: {e}')
        return {'error': _err_public(e, 'Latest sweep fetch failed', 'upgrade_4k'), 'checked': 0, 'grabbed': 0,
                'skipped_in_4k': 0, 'skipped_rejected': 0, 'errors': []}

    items: List[Dict[str, Any]] = payload.get('results') or []
    state = _read_state()
    grabbed_guids = set(state.get('grabbed_guids') or [])
    grabbed_tmdb_ids = set(state.get('grabbed_tmdb_ids') or [])

    # ── Pre-filter to 2160p Remux candidates in the window ──────────
    candidates = []
    for item in items:
        if not _is_2160p_remux(item.get('quality') or ''):
            continue
        seeders = item.get('seeders') or 0
        if seeders < min_seeders:
            continue
        age_hours = _pub_date_age_hours(item.get('pub_date') or '')
        if age_hours > max_age_hours:
            continue
        guid = item.get('guid')
        tmdb_id = item.get('tmdb_id')
        if not guid or not tmdb_id:
            continue
        if guid in grabbed_guids:
            continue
        if tmdb_id in grabbed_tmdb_ids:
            # Already pulled a 2160p Remux for this movie via this automation
            continue
        candidates.append(item)

    logger.info(
        f'4K auto-grab: checked={len(items)} matched_2160p_remux={len(candidates)} '
        f'min_seeders={min_seeders} max_age_hours={max_age_hours}'
    )

    if not candidates:
        result = {
            'checked': len(items),
            'candidates': 0,
            'grabbed': 0,
            'grabbed_movies': [],
            'skipped_in_4k': 0,
            'skipped_in_4k_titles': [],
            'skipped_rejected': 0,
            'skipped_rejected_details': [],
            'errors': [],
            'min_seeders': min_seeders,
            'max_age_hours': max_age_hours,
            'section_4k_id': section_4k_id,
        }
        logger.info(f'4K auto-grab cycle complete (no candidates).')
        return result

    # ── Load the 4K library section once per cycle ───────────────────
    # Refuse rather than proceed blind: without this set every candidate looks
    # un-owned, so a Plex outage turns one cycle into a sweep-wide grab storm
    # that also rewrites each movie's Radarr quality profile.
    plex_4k_tmdb_ids = _load_4k_section_tmdb_ids(plex_service, section_4k_id)
    if plex_4k_tmdb_ids is None:
        logger.warning('4K auto-grab: 4K Plex section %s unreadable or empty — '
                       'refusing to run rather than treating everything as missing',
                       section_4k_id)
        return {'error': f'Could not read 4K Plex section {section_4k_id} '
                         '(Plex unreachable or section empty) — skipped this cycle',
                'checked': len(items), 'candidates': len(candidates), 'grabbed': 0,
                'skipped_in_4k': 0, 'skipped_rejected': 0, 'errors': []}
    logger.info(f'4K auto-grab: loaded {len(plex_4k_tmdb_ids)} tmdb_ids from Plex section {section_4k_id}')

    # ── Iterate candidates ───────────────────────────────────────────
    grabbed_list: List[Dict[str, Any]] = []
    skipped_in_4k: List[str] = []
    skipped_rejected: List[Dict[str, Any]] = []
    errors: List[str] = []
    now_iso = datetime.utcnow().isoformat() + 'Z'

    for item in candidates:
        tmdb_id = int(item['tmdb_id'])
        title = item.get('display_title') or item.get('title') or ''
        year = item.get('year') or 0
        torrent_name = item.get('torrent_name') or ''
        guid = item.get('guid')

        # Hard skip: already in 4K Plex library
        if tmdb_id in plex_4k_tmdb_ids:
            skipped_in_4k.append(f'{title} ({year})')
            grabbed_tmdb_ids.add(tmdb_id)  # don't re-evaluate next scan
            grabbed_guids.add(guid)
            continue

        # ── Radarr add / profile-set / monitor ───────────────────────
        try:
            existing = radarr.get_movie_by_tmdb_id(tmdb_id)
            year_int = None
            try:
                if year:
                    year_int = int(str(year))
            except (ValueError, TypeError):
                year_int = None

            added_now = False
            if not existing:
                added = radarr.add_movie(
                    tmdb_id=tmdb_id,
                    title=title or f'Movie {tmdb_id}',
                    year=year_int or datetime.now().year,
                    quality_profile_id=quality_profile_id,
                    root_folder_path=root_folder_path,
                    monitored=True,
                    search_for_movie=False,
                )
                if not added:
                    errors.append(f'{title} ({year}): Radarr add returned None')
                    continue
                existing = added
                added_now = True
            else:
                # Movie exists. Ensure it's monitored AND on the 4K profile so
                # Radarr will accept the 2160p Remux push. If user picked a
                # profile that doesn't include 2160p Remux, the push will be
                # rejected — which is the right outcome (their setting wins).
                needs_update = False
                if not existing.get('monitored'):
                    existing['monitored'] = True
                    needs_update = True
                if existing.get('qualityProfileId') != quality_profile_id:
                    existing['qualityProfileId'] = quality_profile_id
                    needs_update = True
                if needs_update:
                    updated = radarr.update_movie(existing)
                    if updated:
                        existing = updated
                        logger.info(
                            f"4K auto-grab: updated movie tmdb_id={tmdb_id} "
                            f"(profile={quality_profile_id}, monitored=True)"
                        )

            # ── Push release ─────────────────────────────────────────
            push_payload = {
                'title': torrent_name or title,
                'downloadUrl': item.get('download_url') or '',
                'protocol': 'torrent',
                'publishDate': item.get('pub_date') or now_iso,
                'indexer': item.get('indexer') or 'Prowlarr',
            }
            if item.get('info_hash'):
                push_payload['infoHash'] = item['info_hash']
            if item.get('size_gb'):
                try:
                    push_payload['size'] = int(float(item['size_gb']) * (1024 ** 3))
                except (ValueError, TypeError):
                    pass
            # NOT indexerId: that field is a *Radarr* indexer id, but the id we
            # have is Prowlarr's, and the two are unrelated numbering spaces.
            # They collided here — Prowlarr 3 = IPTorrents, Radarr 3 = PHD — so
            # every push was filed under PHD, and the night PHD went into
            # failure backoff Radarr stopped fetching the torrents and parked
            # them in PendingReleases instead. The `indexer` name above is what
            # shows in Radarr's history; omitting the id makes Radarr fetch
            # downloadUrl directly, which is what we want for a Prowlarr link.

            # Verified, not bare push_release — an approved push is not yet a
            # download. A parked release is demoted to a rejection so it lands
            # in skipped_rejected instead of being reported as an upgrade.
            push_result = radarr.push_release_verified(
                push_payload, (existing or {}).get('id'))
            if push_result.get('ok'):
                grabbed_list.append({
                    'title': title,
                    'year': year,
                    'tmdb_id': tmdb_id,
                    'torrent_name': torrent_name,
                    'indexer': item.get('indexer'),
                    'seeders': item.get('seeders'),
                    'size_gb': item.get('size_gb'),
                    'quality': item.get('quality'),
                    'guid': guid,
                    'added_at': now_iso,
                })
                grabbed_guids.add(guid)
                grabbed_tmdb_ids.add(tmdb_id)
                logger.info(
                    f"4K auto-grab: grabbed '{title}' ({year}) "
                    f"[{item.get('indexer')}] seeders={item.get('seeders')}"
                )
            else:
                rejections = push_result.get('rejections') or []
                skipped_rejected.append({
                    'title': f'{title} ({year})',
                    'torrent_name': torrent_name[:80],
                    'reason': '; '.join(rejections) or push_result.get('error') or 'Unknown',
                })
                # Mark the guid so we don't retry this specific release. Leave
                # tmdb_id unmarked so a better release for the same movie
                # could still succeed.
                grabbed_guids.add(guid)
                # If WE auto-added this movie just to push a release Radarr
                # then rejected, remove it so it does not linger monitored
                # empty (same contract as latest_auto_grab / Genre Roulette).
                if added_now and existing and existing.get('id'):
                    try:
                        if radarr.delete_movie(existing['id'], delete_files=False):
                            logger.info(
                                f"4K auto-grab: removed auto-added '{title}' "
                                f"from Radarr (release rejected, nothing grabbed)"
                            )
                    except Exception as del_err:
                        logger.warning(
                            f"4K auto-grab: failed to remove '{title}': {del_err}"
                        )
                logger.info(
                    f"4K auto-grab: rejected '{title}' [{item.get('indexer')}]: "
                    f"{'; '.join(rejections) or push_result.get('error')}"
                )
        except Exception as e:
            errors.append(f'{title} ({year}): ' + _err_public(e, 'failed', 'upgrade_4k'))
            logger.error(f'4K auto-grab: failed on {title} ({year}): {e}', exc_info=True)

    _append_log(grabbed_list)
    state['grabbed_guids'] = list(grabbed_guids)
    state['grabbed_tmdb_ids'] = list(grabbed_tmdb_ids)
    _write_state(state)

    result = {
        'checked': len(items),
        'candidates': len(candidates),
        'grabbed': len(grabbed_list),
        'grabbed_movies': grabbed_list,
        'skipped_in_4k': len(skipped_in_4k),
        'skipped_in_4k_titles': skipped_in_4k,
        'skipped_rejected': len(skipped_rejected),
        'skipped_rejected_details': skipped_rejected,
        'errors': errors,
        'min_seeders': min_seeders,
        'max_age_hours': max_age_hours,
        'section_4k_id': section_4k_id,
    }
    logger.info(
        f'4K auto-grab cycle complete: checked={result["checked"]} '
        f'candidates={result["candidates"]} grabbed={result["grabbed"]} '
        f'skipped_in_4k={result["skipped_in_4k"]} '
        f'skipped_rejected={result["skipped_rejected"]} errors={len(errors)}'
    )
    return result
