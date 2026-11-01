"""
Latest Auto-Grab Automation

Periodically scans the Prowlarr "latest" feed (the same feed that powers the
Latest sheet in the kiosk UI) and auto-grabs releases that:

  • were published within the configured max_age window (default 24h),
  • have at least min_seeders seeders (default 60),
  • match the configured quality profile (the user's standard profile by default).

The grab path is identical to what the Latest sheet's download button uses
(`/api/indexer/grab` semantics → add-if-missing → Radarr push_release), so
quality-profile filtering, rejection reasons, etc. are all handled by Radarr.

Dedup: once a release is grabbed, we record its (tmdb_id, guid) in
`data/latest_auto_grab_state.json`. We also rely on the existing
`radarr.get_movie_by_tmdb_id` check inside the grab path — and the other
watchlist automations (trending, now_available) already skip movies that
exist in Radarr, so cross-automation dedup happens automatically.
"""

from app.err_text import public as _err_public
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from app.automation_features import is_enabled as _automation_enabled

logger = logging.getLogger(__name__)

# ── standard release eligibility ─────────────────────────────────────────────
# Mirror the standard (720p/1080p) Radarr quality profile so we only ever grab (and add to
# Radarr) releases that actually qualify — instead of adding first and letting
# Radarr reject them, which left "ghost" monitored-but-empty movies behind.
#
# The standard profile accepts: 720p / 1080p, from WEB-DL / WEBRip / Bluray, NOT remux, and
# NOT x265/HEVC (the profile's "no x265" custom format scores those out).
_STANDARD_RESOLUTIONS = {'720p', '1080p'}
_STANDARD_SOURCES = {'WEBDL', 'WEBRIP', 'BLURAY'}
_X265_RE = re.compile(r'(x\.?\s?265|h\.?\s?265|hevc)', re.IGNORECASE)


def _matches_standard(item: Dict[str, Any]) -> tuple[bool, str]:
    """Return (eligible, reason). Eligible means the release matches the standard
    profile, so it's worth adding to Radarr and grabbing."""
    resolution = (item.get('resolution') or '').lower()
    # Normalize source: drop spaces/hyphens so 'WEB-DL' -> 'WEBDL', 'Blu-Ray' -> 'BLURAY'.
    source = (item.get('source') or '').upper().replace(' ', '').replace('-', '')
    name = item.get('torrent_name') or item.get('title') or ''

    if resolution not in _STANDARD_RESOLUTIONS:
        return False, f"{resolution or '?'} (only 720p/1080p)"
    if 'REMUX' in source:
        return False, 'remux'
    if source not in _STANDARD_SOURCES:
        return False, f"{item.get('source') or '?'} (only WEB-DL/WEBRip/Bluray)"
    if _X265_RE.search(name):
        return False, 'x265/HEVC'
    return True, ''

# ── Module-level state ────────────────────────────────────────────
_last_status: Dict[str, Any] = {
    'running': False,
    'last_run': None,
    'cycle_count': 0,
    'last_result': None,
}
_lock = threading.Lock()
_DEFAULT_INTERVAL_MINUTES = 15
_DEFAULT_MIN_SEEDERS = 60
_DEFAULT_MAX_AGE_HOURS = 24
_INITIAL_DELAY = 45
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
    return _data_dir() / 'latest_auto_grab_log.json'


def _state_path() -> Path:
    return _data_dir() / 'latest_auto_grab_state.json'


# ── Log + state IO ────────────────────────────────────────────────

def _read_log() -> List[Dict[str, Any]]:
    try:
        p = _log_path()
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        logger.warning(f'Failed to read latest auto-grab log: {e}')
    return []


def _write_log(entries: List[Dict[str, Any]]) -> None:
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries[-_MAX_LOG_ENTRIES:], indent=None))
    except Exception as e:
        logger.warning(f'Failed to write latest auto-grab log: {e}')


def _append_log(new_entries: List[Dict[str, Any]]) -> None:
    if not new_entries:
        return
    existing = _read_log()
    existing.extend(new_entries)
    _write_log(existing)


def _read_state() -> Dict[str, Any]:
    """State: { 'grabbed_guids': [list of guids], 'grabbed_tmdb_ids': [list of ints] }.
    Used to prevent re-grabbing the same release on subsequent scans."""
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f'Failed to read latest auto-grab state: {e}')
    return {'grabbed_guids': [], 'grabbed_tmdb_ids': []}


def _write_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # Cap each list at 5000 entries so the file can't grow without bound
        if isinstance(state.get('grabbed_guids'), list):
            state['grabbed_guids'] = state['grabbed_guids'][-5000:]
        if isinstance(state.get('grabbed_tmdb_ids'), list):
            state['grabbed_tmdb_ids'] = state['grabbed_tmdb_ids'][-5000:]
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning(f'Failed to write latest auto-grab state: {e}')


# ── Public API ────────────────────────────────────────────────────


# # PERSIST_LAST_RUN:latest_auto_grab
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
    """Return the persistent log (newest first)."""
    entries = _read_log()
    entries.reverse()
    return entries


def trigger_manual_run() -> None:
    """Fire a one-off cycle in a background thread."""
    t = threading.Thread(target=lambda: _run_cycle(force=True), daemon=True)
    t.start()


def start_latest_auto_grab_thread() -> None:
    from app import instance as _instance
    if not _instance.writes_allowed('latest_auto_grab'):   # LUMA_INSTANCE=test: off unless opted in
        return None
    t = threading.Thread(target=_update_loop, daemon=True, name='latest-auto-grab')
    t.start()
    logger.info('Latest auto-grab automation started')


# ── Internal loop ─────────────────────────────────────────────────

def _update_loop() -> None:
    time.sleep(_INITIAL_DELAY)
    while True:
        try:
            _run_cycle()
        except Exception as e:
            logger.error(f'Latest auto-grab cycle error: {e}', exc_info=True)
        # Re-read interval each cycle so settings changes take effect
        try:
            from app.settings_manager import SettingsManager
            mins = SettingsManager().get(
                'automation.latest_auto_grab.scan_interval_minutes',
                _DEFAULT_INTERVAL_MINUTES,
            )
        except Exception:
            mins = _DEFAULT_INTERVAL_MINUTES
        time.sleep(max(1, int(mins)) * 60)


def _run_cycle(force: bool = False) -> None:
    from app.settings_manager import SettingsManager

    settings = SettingsManager()

    if not force:
        enabled = _automation_enabled(settings, 'latest_auto_grab')
        if not enabled:
            logger.debug('Latest auto-grab disabled, skipping cycle')
            return

    with _lock:
        if _last_status['running']:
            logger.warning('Latest auto-grab cycle already running, skipping')
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
            logger.warning(f'latest_auto_grab: could not persist last_run: {e}')
    finally:
        with _lock:
            _last_status['running'] = False


def _pub_date_age_hours(pub_date_str: str) -> float:
    """Return age in hours, or +inf if unparseable / missing."""
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


def _build_non_4k_plex_tmdb_ids(plex_service, section_4k_id: int) -> set:
    """Walk every Plex movie section EXCEPT the configured 4K one and collect
    tmdb_ids. A candidate that hits this set is one we already own in standard-
    profile terms (1080p / mixed quality), so Latest auto-grab skips it.

    Movies that exist ONLY in the 4K section are intentionally NOT in this
    set — standard profile doesn't include 2160p Remux, so we still want to
    grab the matching 1080p/Remux version for the rest of the library.
    """
    tmdb_ids: set = set()
    try:
        sections = plex_service.get_library_sections() or []
        movie_sections = [s for s in sections if (s.get('type') or '').lower() == 'movie']
        for section in movie_sections:
            sid = section.get('id')
            if not sid or (section_4k_id and int(sid) == int(section_4k_id)):
                continue
            offset = 0
            page_size = 200
            pages = 0
            while pages < 50:  # cap at ~10k movies per section
                result = plex_service.get_all_movies(int(sid), limit=page_size,
                                                     offset=offset, timeout=30)
                for item in (result.get('movies') or []):
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
        logger.warning(f'Latest auto-grab: Plex ownership build failed: {e}')
    return tmdb_ids


def _do_cycle(settings) -> Dict[str, Any]:
    """Pull latest releases, filter to age+seeder threshold, grab via push_release.
    Ownership check is Plex-based (NOT Radarr) because this user's Radarr
    instance has no filesystem awareness — file movement is handled by an
    external automation, so radarr.hasFile/monitored are unreliable signals."""

    # Lazy imports to avoid circular deps at module load
    from app.indexer_api import get_indexer_client
    from app.radarr_api import RadarrAPI
    from app.plex_service import plex_service
    import requests

    min_seeders = settings.get('automation.latest_auto_grab.min_seeders', _DEFAULT_MIN_SEEDERS)
    max_age_hours = settings.get('automation.latest_auto_grab.max_age_hours', _DEFAULT_MAX_AGE_HOURS)
    quality_profile_id = settings.get('automation.latest_auto_grab.quality_profile_id', 0)
    # The 4K section is shared with the 4K Remux auto-grab — same library,
    # same purpose. Read directly so the user only configures it once.
    section_4k_id = settings.get('automation.upgrade_4k_auto_grab.plex_4k_section_id', 0)

    radarr_url = settings.get('radarr.url', '')
    radarr_key = settings.get('radarr.api_key', '')
    if not radarr_url or not radarr_key:
        return {'error': 'Radarr not configured', 'checked': 0, 'grabbed': 0, 'skipped_in_plex': 0, 'skipped_rejected': 0, 'errors': []}

    radarr = RadarrAPI(radarr_url, radarr_key)

    # Resolve quality profile — fall back to the global default if none set
    if not quality_profile_id:
        quality_profile_id = settings.get('radarr.default_quality_profile_id', 0)
    if not quality_profile_id:
        profiles = radarr.get_quality_profiles()
        quality_profile_id = profiles[0]['id'] if profiles else None
    if not quality_profile_id:
        return {'error': 'No Radarr quality profile available', 'checked': 0, 'grabbed': 0, 'skipped_in_plex': 0, 'skipped_rejected': 0, 'errors': []}

    root_folders = radarr.get_root_folders()
    if not root_folders:
        return {'error': 'No Radarr root folders configured', 'checked': 0, 'grabbed': 0, 'skipped_in_plex': 0, 'skipped_rejected': 0, 'errors': []}
    root_folder_path = root_folders[0].get('path', '')

    # Fetch latest releases from our own endpoint. We pull both:
    #   1. browse mode — fast empty-search, covers most quality tiers
    #   2. sweep=remux2160p — targeted Prowlarr keyword sweep that surfaces
    #      2160p Remux uploads from high-quality indexers (BHD/PHD) which
    #      otherwise get crowded out of browse by high-volume indexers (IPT)
    # Results are deduped by guid so anything in both sources counts once.
    def _fetch(params):
        # In-process call: no HTTP round trip through the auth layer.
        from app.main import latest_releases
        return latest_releases(params)

    items: List[Dict[str, Any]] = []
    seen_guids: set = set()
    try:
        for params in (
            {'limit': 300},                          # browse
            {'limit': 600, 'sweep': 'remux2160p'},   # 2160p Remux sweep
        ):
            try:
                for it in _fetch(params):
                    g = it.get('guid')
                    if not g or g in seen_guids:
                        continue
                    seen_guids.add(g)
                    items.append(it)
            except Exception as e:
                logger.warning(f'Latest auto-grab: fetch with {params} failed: {e}')
    except Exception as e:
        logger.error(f'Latest auto-grab: failed to fetch /api/indexer/latest: {e}')
        return {'error': _err_public(e, 'Latest fetch failed', 'latest_auto_grab'), 'checked': 0, 'grabbed': 0, 'skipped_in_plex': 0, 'skipped_rejected': 0, 'errors': []}

    if not items:
        return {'error': 'No items returned from either feed', 'checked': 0, 'grabbed': 0,
                'skipped_in_plex': 0, 'skipped_rejected': 0, 'errors': []}
    state = _read_state()
    grabbed_guids = set(state.get('grabbed_guids') or [])
    grabbed_tmdb_ids = set(state.get('grabbed_tmdb_ids') or [])

    # ── Filter to candidates ──────────────────────────────────────
    candidates = []
    skipped_not_standard = 0
    for item in items:
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
            # We already grabbed *something* for this movie via this
            # automation — skip even if it's a different release.
            continue
        # Only consider releases that actually match the standard profile. This is
        # done up front so non-matching releases (2160p, remux, x265, etc.) are
        # never added to Radarr just to be rejected.
        eligible, reason = _matches_standard(item)
        if not eligible:
            skipped_not_standard += 1
            title = item.get('display_title') or item.get('title') or '?'
            logger.debug(f"Latest auto-grab: skipped '{title}' — not standard-eligible: {reason}")
            continue
        candidates.append(item)

    # Sort by seeders desc so the highest-seeded release wins when multiple
    # releases for the same tmdb_id appear in one cycle.
    candidates.sort(key=lambda x: -(x.get('seeders') or 0))

    logger.info(
        f'Latest auto-grab: checked={len(items)} candidates_in_window='
        f'{len(candidates)} skipped_not_standard={skipped_not_standard} '
        f'min_seeders={min_seeders} max_age_hours={max_age_hours}'
    )

    # ── Build the Plex "non-4K ownership" set once per cycle ──────────
    # A movie that exists in any movie section EXCEPT the 4K one counts as
    # already-owned for Latest's purposes. Movies that exist ONLY in the 4K
    # section are still grab targets, because the standard profile doesn't
    # overlap with 2160p Remux.
    non_4k_tmdb_ids = _build_non_4k_plex_tmdb_ids(plex_service, section_4k_id)
    logger.info(
        f'Latest auto-grab: Plex ownership = {len(non_4k_tmdb_ids)} tmdb_ids '
        f'across non-4K movie sections (4K section excluded: {section_4k_id or "none"})'
    )

    # ── For each candidate, skip on Plex non-4K ownership; otherwise grab. ──
    grabbed_list: List[Dict[str, Any]] = []
    skipped_in_plex: List[str] = []
    skipped_rejected: List[Dict[str, Any]] = []
    errors: List[str] = []
    now_iso = datetime.utcnow().isoformat() + 'Z'

    # Within-cycle dedup: if two different releases surface for the same
    # movie in one scan, we only push the first one. This in-memory tracker
    # is separate from the persisted state — the persisted set only contains
    # things we've actually pushed to Radarr.
    grabbed_this_cycle: set = set()

    for item in candidates:
        tmdb_id = item['tmdb_id']
        title = item.get('display_title') or item.get('title') or ''
        year = item.get('year') or 0
        torrent_name = item.get('torrent_name') or ''
        guid = item.get('guid')

        # Same tmdb_id grabbed earlier in THIS cycle — skip silently.
        if tmdb_id in grabbed_this_cycle:
            continue

        # Plex-based ownership skip: we already have a standard-quality copy.
        # IMPORTANT: do NOT write to state here — Plex is the source of truth
        # and we want to re-check Plex every cycle. If we marked this in
        # state, a later library re-org or section move would never re-grab.
        if tmdb_id in non_4k_tmdb_ids:
            skipped_in_plex.append(f'{title} ({year})')
            continue

        # Get-or-add the Radarr movie. Radarr is just our grab transport in
        # this setup — it doesn't manage files, so we use it purely to push
        # the release to the download client.
        try:
            added_now = False  # did THIS cycle add the movie to Radarr?
            existing = radarr.get_movie_by_tmdb_id(tmdb_id)
            if not existing:
                year_int = None
                try:
                    if year:
                        year_int = int(str(year))
                except (ValueError, TypeError):
                    year_int = None
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
                # Movie already exists in Radarr (possibly from the 4K auto-
                # grab which set the 4K-only profile). Force the profile to
                # this automation's standard profile so Radarr will accept our
                # 1080p/mixed-quality push. The 4K release is already on disk
                # at this point — the profile change only affects future
                # Radarr-initiated upgrades, which is harmless.
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
                            f"Latest auto-grab: flipped Radarr movie tmdb_id={tmdb_id} "
                            f"to standard profile {quality_profile_id} for push"
                        )

            movie_id = existing.get('id')

            # Push the release directly (same as Latest download button).
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
            # download. A parked release is demoted to a rejection so the
            # branch below removes the movie we auto-added and records the skip.
            push_result = radarr.push_release_verified(push_payload, movie_id)
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
                grabbed_this_cycle.add(tmdb_id)
                logger.info(
                    f"Latest auto-grab: grabbed '{title}' ({year}) "
                    f"[{item.get('indexer')}] seeders={item.get('seeders')} "
                    f"quality={item.get('quality')}"
                )
            else:
                rejections = push_result.get('rejections') or []
                skipped_rejected.append({
                    'title': f'{title} ({year})',
                    'torrent_name': torrent_name[:80],
                    'reason': '; '.join(rejections) or push_result.get('error') or 'Unknown',
                })
                # Mark guid so we don't retry every 15min — but leave the
                # tmdb_id unmarked so a different release for the same movie
                # could still succeed next scan.
                grabbed_guids.add(guid)
                # If WE auto-added this movie just to push a release that Radarr
                # then rejected (e.g. size cap), remove it so it doesn't linger
                # monitored-but-empty. Never touch movies that already existed.
                if added_now and movie_id:
                    try:
                        if radarr.delete_movie(movie_id):
                            logger.info(
                                f"Latest auto-grab: removed auto-added '{title}' from Radarr "
                                f"(release rejected, nothing grabbed)"
                            )
                    except Exception as del_err:
                        logger.warning(f"Latest auto-grab: failed to remove '{title}': {del_err}")
                logger.info(
                    f"Latest auto-grab: skipped '{title}' [{item.get('indexer')}] — "
                    f"rejected: {'; '.join(rejections) or push_result.get('error')}"
                )
        except Exception as e:
            errors.append(f'{title} ({year}): ' + _err_public(e, 'failed', 'latest_auto_grab'))
            logger.error(f'Latest auto-grab: failed on {title} ({year}): {e}', exc_info=True)

    # Persist new grabbed entries + state
    _append_log(grabbed_list)
    state['grabbed_guids'] = list(grabbed_guids)
    state['grabbed_tmdb_ids'] = list(grabbed_tmdb_ids)
    _write_state(state)

    result = {
        'checked': len(items),
        'candidates': len(candidates),
        'grabbed': len(grabbed_list),
        'grabbed_movies': grabbed_list,
        'skipped_in_plex': len(skipped_in_plex),
        'skipped_in_plex_titles': skipped_in_plex,
        'skipped_rejected': len(skipped_rejected),
        'skipped_rejected_details': skipped_rejected,
        'errors': errors,
        'min_seeders': min_seeders,
        'max_age_hours': max_age_hours,
        'section_4k_id': section_4k_id,
    }
    logger.info(
        f'Latest auto-grab cycle complete: checked={result["checked"]} '
        f'candidates={result["candidates"]} grabbed={result["grabbed"]} '
        f'skipped_in_plex={result["skipped_in_plex"]} '
        f'skipped_rejected={result["skipped_rejected"]} errors={len(errors)}'
    )
    return result
