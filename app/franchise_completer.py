"""
Franchise Completer — nightly auto-grab of missing parts from super-popular
TMDb franchises that are almost complete in the library.

Uses the incomplete-collections cache from movie_collections.py (fame_score,
owned vs missing vs upcoming). Only collections above min_fame with at most
max_missing_parts released gaps are considered; upcoming/unreleased parts are
skipped. Caps grabs at movies_per_night.

Quality profile defaults to the Radarr default profile from Settings. After add+search, newly added
titles are unmonitored so they never pollute the intentional Watchlist rail
(same lesson as Genre Roulette ghosts).

Settings under automation.franchise_completer.*:
    enabled, hour, minute, quality_profile_id, min_fame, max_missing_parts,
    movies_per_night, min_year

State: data/franchise_completer_state.json
Log:   data/franchise_completer_log.json
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_HOUR = 23
_DEFAULT_MINUTE = 30
_DEFAULT_QUALITY_PROFILE_ID = 0  # 0 = radarr.default_quality_profile_id
# fame_score is max IMDb votes across released parts (see movie_collections).
# ~500k keeps only truly huge franchises (Die Hard, Jurassic, Spider-Man…).
_DEFAULT_MIN_FAME = 500000
_DEFAULT_MAX_MISSING_PARTS = 1
_DEFAULT_MOVIES_PER_NIGHT = 1
_DEFAULT_MIN_YEAR = 0
_MAX_LOG_ENTRIES = 200
# Upper bound on real grab attempts per night when picks get skipped/fail —
# keeps a bad night from walking the whole candidate list.
_MAX_ATTEMPTS_PER_NIGHT = 5
_CHECK_INTERVAL_SECONDS = 5 * 60

_last_status: Dict[str, Any] = {
    'running': False,
    'last_run': None,
    'last_result': None,
    'cycle_count': 0,
}
_lock = threading.Lock()
_hydrated = False


# ── Settings helpers ──────────────────────────────────────────────────────

def _int_setting(settings, key: str, default: int, low: int, high: int) -> int:
    """Read an int setting, clamped. Zero is valid (e.g. hour=0 / minute=0).

    Do NOT use get(...) or default — that turns midnight into 23:30.
    """
    try:
        raw = settings.get(key, default)
        v = default if raw is None or raw == "" else int(raw)
    except (TypeError, ValueError):
        v = default
    return max(low, min(high, v))


def _float_setting(settings, key: str, default: float, low: float, high: float) -> float:
    try:
        raw = settings.get(key, default)
        v = default if raw is None or raw == "" else float(raw)
    except (TypeError, ValueError):
        v = default
    return max(low, min(high, v))


def _profile_id(settings) -> int:
    return _int_setting(
        settings, 'automation.franchise_completer.quality_profile_id',
        _DEFAULT_QUALITY_PROFILE_ID, 0, 10_000)


def _min_fame(settings) -> float:
    return _float_setting(
        settings, 'automation.franchise_completer.min_fame',
        float(_DEFAULT_MIN_FAME), 0.0, 50_000_000.0)


def _max_missing(settings) -> int:
    return _int_setting(
        settings, 'automation.franchise_completer.max_missing_parts',
        _DEFAULT_MAX_MISSING_PARTS, 1, 10)


def _movies_per_night(settings) -> int:
    return _int_setting(
        settings, 'automation.franchise_completer.movies_per_night',
        _DEFAULT_MOVIES_PER_NIGHT, 1, 10)


def _min_year(settings) -> int:
    try:
        v = int(settings.get('automation.franchise_completer.min_year',
                             _DEFAULT_MIN_YEAR) or 0)
    except (TypeError, ValueError):
        return 0
    return v if 1800 < v < 2200 else 0


def _scheduled_time(settings) -> str:
    hour = _int_setting(settings, 'automation.franchise_completer.hour',
                        _DEFAULT_HOUR, 0, 23)
    minute = _int_setting(settings, 'automation.franchise_completer.minute',
                          _DEFAULT_MINUTE, 0, 59)
    return f'{hour:02d}:{minute:02d}'


# ── Paths / state ─────────────────────────────────────────────────────────

def _data_dir() -> Path:
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent
    return Path(__file__).resolve().parent.parent / 'data'


def _state_path() -> Path:
    return _data_dir() / 'franchise_completer_state.json'


def _log_path() -> Path:
    return _data_dir() / 'franchise_completer_log.json'


def _read_state() -> Dict[str, Any]:
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                data.setdefault('grabbed_tmdb_ids', [])
                data.setdefault('last_run_date', None)
                data.setdefault('last_scheduled_date', None)
                data.setdefault('last_scheduled_time', None)
                data.setdefault('last_run', None)
                data.setdefault('last_result', None)
                return data
    except Exception as e:
        logger.warning('Franchise Completer: failed to read state: %s', e)
    return {
        'grabbed_tmdb_ids': [],
        'last_run_date': None,
        'last_scheduled_date': None,
        'last_scheduled_time': None,
        'last_run': None,
        'last_result': None,
    }


def _write_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning('Franchise Completer: failed to write state: %s', e)


def _append_log(entry: Optional[Dict[str, Any]]) -> None:
    if not entry:
        return
    try:
        p = _log_path()
        existing = json.loads(p.read_text()) if p.exists() else []
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(existing[-_MAX_LOG_ENTRIES:]))
    except Exception as e:
        logger.warning('Franchise Completer: failed to write log: %s', e)


def _hydrate_status() -> None:
    global _hydrated
    if _hydrated:
        return
    with _lock:
        if _hydrated:
            return
        state = _read_state()
        if state.get('last_run'):
            _last_status['last_run'] = state['last_run']
        if state.get('last_result') is not None:
            _last_status['last_result'] = state['last_result']
        _hydrated = True


# ── Status / next run ─────────────────────────────────────────────────────

def _next_run_dt() -> Optional[datetime]:
    from app.settings_manager import SettingsManager
    settings = SettingsManager()
    if not settings.get('automation.franchise_completer.enabled', False):
        return None
    at = _scheduled_time(settings)
    hour, minute = (int(p) for p in at.split(':'))
    now = datetime.now()
    state = _read_state()
    done = (state.get('last_scheduled_date') == now.strftime('%Y-%m-%d')
            and state.get('last_scheduled_time') == at)
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if done:
        return slot + timedelta(days=1)
    if now >= slot:
        return now.replace(second=0, microsecond=0)
    return slot


def _next_run() -> Optional[str]:
    try:
        nxt = _next_run_dt()
        return nxt.isoformat() if nxt else None
    except Exception as e:
        logger.warning('Franchise Completer: could not compute next run: %s', e)
        return None


def get_status() -> Dict[str, Any]:
    _hydrate_status()
    with _lock:
        out = dict(_last_status)
    out['next_run'] = _next_run()
    try:
        out['server_tz'] = datetime.now().astimezone().tzname()
    except Exception:
        out['server_tz'] = None
    return out


def get_log() -> List[Dict[str, Any]]:
    try:
        p = _log_path()
        entries = json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []
    out = [e for e in entries if isinstance(e, dict)]
    out.reverse()
    return out


# ── Candidates ────────────────────────────────────────────────────────────

def _load_collections() -> List[Dict[str, Any]]:
    """Full collection entries (with parts) from the incomplete cache."""
    from app import movie_collections
    cache = movie_collections._load_cache()
    cols = list(cache.get('collections') or [])
    for c in cols:
        if c.get('fame_score') is None:
            c['fame_score'] = movie_collections._collection_fame(c.get('parts') or [])
    cols.sort(key=lambda c: (-float(c.get('fame_score') or 0),
                             (c.get('name') or '').lower()))
    return cols


def _radarr_tmdb_ids(settings) -> Optional[set]:
    """Every tmdbId already in Radarr (monitored or not, any file state).

    Returns None when Radarr can't be read, so callers fall back to the
    per-pick existence check in _grab instead of failing the night.
    """
    try:
        from app.radarr_api import RadarrAPI
        url = settings.get('radarr.url', '')
        key = settings.get('radarr.api_key', '')
        if not url or not key:
            return None
        movies = RadarrAPI(url, key).get_movies()
        if not movies:
            return None
        return {int(m['tmdbId']) for m in movies if m.get('tmdbId')}
    except Exception as e:
        logger.warning('Franchise Completer: could not read Radarr library: %s', e)
        return None


def candidates(settings=None) -> Dict[str, Any]:
    """Popular almost-complete franchise gaps, ordered by fame then fewest missing.

    Dry-run and live cycles both call this. Touches no external systems.
    """
    from app.settings_manager import SettingsManager
    settings = settings or SettingsManager()

    min_fame = _min_fame(settings)
    max_missing = _max_missing(settings)
    min_year = _min_year(settings)
    want = _movies_per_night(settings)
    state = _read_state()
    grabbed = set(int(x) for x in (state.get('grabbed_tmdb_ids') or []) if str(x).isdigit() or isinstance(x, int))
    # The collections cache tracks Plex/library ownership, so a title can be
    # "missing" there while already sitting in Radarr (unmonitored ghost,
    # queued, wanted…). Those were being picked night after night and then
    # skipped by _grab as "Already in Radarr" — exclude them up front.
    in_radarr = _radarr_tmdb_ids(settings)
    excluded_in_radarr: List[Dict[str, Any]] = []

    try:
        collections = _load_collections()
    except Exception as e:
        logger.error('Franchise Completer: collections cache unavailable: %s', e)
        return {'error': f'Collections cache unavailable: {e}', 'films': [],
                'collections_considered': 0}

    if not collections:
        return {
            'films': [],
            'collections_considered': 0,
            'reason': 'Collections cache empty — refresh incomplete collections first',
            'min_fame': min_fame,
            'max_missing_parts': max_missing,
            'wanted': want,
        }

    eligible_cols = []
    films: List[Dict[str, Any]] = []
    seen_tmdb = set()

    for coll in collections:
        fame = float(coll.get('fame_score') or 0)
        if fame < min_fame:
            continue
        missing_count = int(coll.get('missing_count') or 0)
        if missing_count < 1 or missing_count > max_missing:
            continue
        parts = coll.get('parts') or []
        # Prefer almost-complete: fewer missing first among fame-qualified.
        missing_parts = [p for p in parts if p.get('status') == 'missing']
        # Skip upcoming explicitly even if a stale cache mislabels them.
        missing_parts = [p for p in missing_parts if p.get('status') != 'upcoming'
                         and p.get('released', True)]
        if not missing_parts:
            continue
        if min_year:
            missing_parts = [
                p for p in missing_parts
                if int(p.get('year') or 0) >= min_year
            ]
            if not missing_parts:
                continue

        eligible_cols.append({
            'id': coll.get('id'),
            'name': coll.get('name'),
            'fame_score': fame,
            'owned_count': coll.get('owned_count'),
            'missing_count': missing_count,
        })

        # Within a collection, older released gaps first (fill classic holes).
        missing_parts.sort(key=lambda p: (
            p.get('release_date') or '9999-99-99',
            p.get('tmdb_id') or 0,
        ))
        for part in missing_parts:
            tid = int(part.get('tmdb_id') or 0)
            if not tid or tid in seen_tmdb or tid in grabbed:
                continue
            seen_tmdb.add(tid)
            if in_radarr is not None and tid in in_radarr:
                excluded_in_radarr.append({
                    'tmdb_id': tid,
                    'title': part.get('title') or '',
                    'year': part.get('year'),
                    'collection_name': coll.get('name'),
                })
                continue
            films.append({
                'tmdb_id': tid,
                'title': part.get('title') or '',
                'year': part.get('year'),
                'release_date': part.get('release_date'),
                'collection_id': coll.get('id'),
                'collection_name': coll.get('name'),
                'fame_score': fame,
                'collection_missing': missing_count,
                'collection_owned': coll.get('owned_count'),
                'poster_path': part.get('poster_path'),
            })

    # Almost-complete first (1 missing before 2), then higher fame.
    films.sort(key=lambda f: (
        int(f.get('collection_missing') or 99),
        -float(f.get('fame_score') or 0),
        f.get('release_date') or '9999-99-99',
    ))

    return {
        'films': films,
        'picks': films[:want],
        'collections_considered': len(collections),
        'collections_eligible': len(eligible_cols),
        'eligible_collections': eligible_cols[:40],
        'candidates': len(films),
        'excluded_in_radarr': excluded_in_radarr[:40],
        'excluded_in_radarr_count': len(excluded_in_radarr),
        'radarr_checked': in_radarr is not None,
        'wanted': want,
        'min_fame': min_fame,
        'max_missing_parts': max_missing,
        'min_year': min_year,
        'quality_profile_id': _profile_id(settings),
    }


# ── Grab ──────────────────────────────────────────────────────────────────

def _undo_failed_grab(radarr, movie: Dict[str, Any], created: bool,
                      prior, film: Dict[str, Any], reason: str) -> None:
    """Remove ghosts after a refused/failed grab (same lesson as Roulette)."""
    try:
        if created and movie and movie.get('id'):
            if radarr.delete_movie(movie['id'], delete_files=False):
                logger.info(
                    'Franchise Completer: removed %s from Radarr after failure '
                    '(%s) — nothing left monitored', film.get('title'), reason)
            else:
                logger.error(
                    'Franchise Completer: could not remove %s after failure — '
                    'may remain monitored', film.get('title'))
            return
        if prior is None or not movie:
            return
        was_monitored, was_profile = prior
        movie['monitored'] = was_monitored
        if was_profile is not None:
            movie['qualityProfileId'] = was_profile
        if radarr.update_movie(movie):
            logger.info(
                'Franchise Completer: restored %s to monitored=%s profile=%s '
                'after failure (%s)', film.get('title'), was_monitored,
                was_profile, reason)
    except Exception as e:
        logger.error(
            'Franchise Completer: rollback failed for %s: %s',
            film.get('title'), e, exc_info=True)


def _pick_radarr_release(releases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Best approved release from Radarr interactive search (seeders, then quality)."""
    approved = []
    for r in releases or []:
        if r.get('approved') is False:
            continue
        if r.get('rejected') is True:
            continue
        rejections = r.get('rejections') or []
        if rejections:
            continue
        approved.append(r)
    if not approved:
        return None

    def _score(r: Dict[str, Any]):
        seeders = int(r.get('seeders') or 0)
        q = ((r.get('quality') or {}).get('quality') or {})
        qid = int(q.get('id') or 0)
        size = int(r.get('size') or 0)
        return (-seeders, -qid, size)

    approved.sort(key=_score)
    return approved[0]


def _grab(film: Dict[str, Any], settings) -> Dict[str, Any]:
    """Add on the standard profile, grab a verified Radarr release, unmonitor on success.

    Uses Radarr interactive release search + grab, then movie_download_active
    to confirm a client handoff — never leave monitored ghosts on failure
    (same patterns as Genre Roulette / Movie Night).
    """
    from app.radarr_api import RadarrAPI

    radarr_url = settings.get('radarr.url', '')
    radarr_key = settings.get('radarr.api_key', '')
    if not radarr_url or not radarr_key:
        return {'ok': False, 'detail': 'Radarr not configured'}

    profile_id = _profile_id(settings) or int(settings.get('radarr.default_quality_profile_id') or 0)
    if not profile_id:
        return {'ok': False, 'detail': 'Franchise completer quality profile is not set'}

    radarr = RadarrAPI(radarr_url, radarr_key)
    roots = radarr.get_root_folders()
    if not roots:
        return {'ok': False, 'detail': 'No Radarr root folders'}

    tmdb_id = int(film['tmdb_id'])
    title = film.get('title') or ('Movie %s' % tmdb_id)
    year = int(film.get('year') or datetime.now().year)
    created = False
    prior = None
    existing = None

    try:
        existing = radarr.get_movie_by_tmdb_id(tmdb_id)
        if existing:
            mid = existing.get('id')
            if existing.get('hasFile'):
                return {'ok': False, 'detail': 'Already has file in Radarr',
                        'skipped': True, 'radarr_id': mid}
            if mid and getattr(radarr, 'movie_download_active', None) and radarr.movie_download_active(mid):
                return {'ok': False, 'detail': 'Already downloading in Radarr',
                        'skipped': True, 'radarr_id': mid}
            return {
                'ok': False,
                'detail': 'Already in Radarr (left untouched)',
                'skipped': True,
                'radarr_id': mid,
            }

        existing = radarr.add_movie(
            tmdb_id=tmdb_id,
            title=title,
            year=year,
            quality_profile_id=profile_id,
            root_folder_path=roots[0].get('path', ''),
            monitored=True,
            search_for_movie=False,
        )
        if not existing or not existing.get('id'):
            return {'ok': False, 'detail': 'Radarr add returned None'}
        created = True
        radarr_id = existing['id']

        releases = radarr.search_movie_releases(radarr_id)
        pick = _pick_radarr_release(releases)
        if not pick:
            _undo_failed_grab(radarr, existing, created, prior, film,
                              'No approved Radarr releases')
            return {'ok': False, 'detail': 'No approved Radarr releases'}

        guid = pick.get('guid')
        indexer_id = pick.get('indexerId')
        if not guid or indexer_id is None:
            _undo_failed_grab(radarr, existing, created, prior, film,
                              'Release missing guid/indexerId')
            return {'ok': False, 'detail': 'Release missing guid/indexerId'}

        grabbed = radarr.grab_release(guid, int(indexer_id), movie_id=radarr_id)
        if not grabbed.get('ok'):
            reason = grabbed.get('error') or 'grab refused'
            if getattr(radarr, 'movie_download_active', None) and radarr.movie_download_active(radarr_id):
                logger.info(
                    'Franchise Completer: %s grab said fail (%s) but download '
                    'active — counting as grabbed', title, reason)
            else:
                _undo_failed_grab(radarr, existing, created, prior, film, reason)
                return {'ok': False, 'detail': reason}

        active = False
        for attempt in range(12):
            if attempt:
                time.sleep(1.5)
            if getattr(radarr, 'movie_download_active', None) and radarr.movie_download_active(radarr_id):
                active = True
                break

        if not active:
            reason = 'Radarr accepted the release but no download appeared in the queue'
            if getattr(radarr, 'movie_download_active', None) and radarr.movie_download_active(radarr_id):
                active = True
            else:
                try:
                    qrec = radarr._queue_record(radarr_id)
                    if qrec and qrec.get('id') is not None:
                        radarr.remove_from_queue(qrec['id'], remove_from_client=True)
                except Exception as e:
                    logger.warning(
                        'Franchise Completer: could not clear queue before undo for %s: %s',
                        title, e)
                _undo_failed_grab(radarr, existing, created, prior, film, reason)
                return {'ok': False, 'detail': reason}

        unmonitored = False
        try:
            if radarr.unmonitor_movie(radarr_id):
                unmonitored = True
                logger.info(
                    'Franchise Completer: unmonitored %s after grab — '
                    'download continues without Watchlist pollution', title)
            else:
                logger.warning(
                    'Franchise Completer: could not unmonitor %s after grab', title)
        except Exception as e:
            logger.warning(
                'Franchise Completer: unmonitor after grab failed for %s: %s',
                title, e)

        rel_title = pick.get('title') or pick.get('releaseTitle') or ''
        return {
            'ok': True,
            'detail': 'grabbed',
            'radarr_id': radarr_id,
            'unmonitored': unmonitored,
            'created': True,
            'release': rel_title,
        }
    except Exception as e:
        logger.error('Franchise Completer grab failed for %s: %s', title, e,
                     exc_info=True)
        if created and existing:
            _undo_failed_grab(radarr, existing, created, prior, film, str(e))
        return {'ok': False, 'detail': str(e)}



# ── Cycle ─────────────────────────────────────────────────────────────────

def run_cycle(dry_run: bool = False, settings=None,
              scheduled: bool = False) -> Dict[str, Any]:
    """Grab up to movies_per_night franchise gaps. dry_run touches nothing."""
    from app.settings_manager import SettingsManager
    settings = settings or SettingsManager()

    started = datetime.now(timezone.utc).isoformat()
    cand = candidates(settings)
    if cand.get('error'):
        return {
            'error': cand['error'],
            'started_at': started,
            'dry_run': dry_run,
            'grabbed': 0,
            'picks': [],
        }

    picks = list(cand.get('picks') or [])
    films = list(cand.get('films') or picks)
    want = int(cand.get('wanted') or 1)
    result: Dict[str, Any] = {
        'started_at': started,
        'dry_run': dry_run,
        'candidates': cand.get('candidates', 0),
        'excluded_in_radarr_count': cand.get('excluded_in_radarr_count', 0),
        'excluded_in_radarr': cand.get('excluded_in_radarr') or [],
        'collections_considered': cand.get('collections_considered', 0),
        'collections_eligible': cand.get('collections_eligible', 0),
        'eligible_collections': cand.get('eligible_collections') or [],
        'wanted': cand.get('wanted'),
        'min_fame': cand.get('min_fame'),
        'max_missing_parts': cand.get('max_missing_parts'),
        'min_year': cand.get('min_year'),
        'quality_profile_id': cand.get('quality_profile_id'),
        'picks': [],
        'grabbed': 0,
        'reason': None,
    }

    if not picks:
        result['reason'] = cand.get('reason') or (
            'No popular almost-complete franchise gaps matched the fame gate')
        if scheduled and not dry_run:
            _stamp_scheduled(settings)
        return result

    grabbed_now: List[int] = []
    # Dry run shows the first `want` picks. Live run walks the ordered list:
    # a pick that is skipped (already in Radarr / has file / downloading) or
    # fails moves on to the next candidate instead of ending the night empty.
    queue = picks if dry_run else films
    attempts = 0
    for film in queue:
        if not dry_run:
            if result['grabbed'] >= want:
                break
            if attempts >= _MAX_ATTEMPTS_PER_NIGHT:
                result['attempt_cap_hit'] = True
                break
            attempts += 1
        row = {
            'title': film.get('title'),
            'year': film.get('year'),
            'tmdb_id': film.get('tmdb_id'),
            'collection_name': film.get('collection_name'),
            'collection_id': film.get('collection_id'),
            'fame_score': film.get('fame_score'),
            'collection_missing': film.get('collection_missing'),
            'grabbed': False,
            'detail': None,
            'unmonitored': False,
        }
        if dry_run:
            row['detail'] = 'would grab'
            result['picks'].append(row)
            continue

        outcome = _grab(film, settings)
        row['detail'] = outcome.get('detail')
        row['unmonitored'] = bool(outcome.get('unmonitored'))
        if outcome.get('ok'):
            row['grabbed'] = True
            result['grabbed'] += 1
            grabbed_now.append(int(film['tmdb_id']))
        result['picks'].append(row)

    if dry_run:
        result['reason'] = f'Would grab {len(result["picks"])} title(s)'
        return result

    if result['grabbed'] == 0 and result['picks']:
        result['reason'] = 'No titles grabbed'
    elif result['grabbed']:
        result['reason'] = f"Grabbed {result['grabbed']}"

    state = _read_state()
    if grabbed_now:
        ids = set(int(x) for x in (state.get('grabbed_tmdb_ids') or [])
                  if str(x).lstrip('-').isdigit() or isinstance(x, int))
        ids.update(grabbed_now)
        state['grabbed_tmdb_ids'] = sorted(ids)[-5000:]
        # Collections cache is TMDb-franchise ownership — refresh so newly
        # grabbed parts flip from missing → owned without waiting for daily rebuild.
        try:
            from app.movie_collections import schedule_ownership_refresh
            schedule_ownership_refresh(reason='franchise_completer')
        except Exception as e:
            logger.warning(
                'Franchise Completer: could not schedule collections refresh: %s', e
            )
    today = datetime.now().strftime('%Y-%m-%d')
    state['last_run_date'] = today
    if scheduled:
        state['last_scheduled_date'] = today
        state['last_scheduled_time'] = _scheduled_time(settings)
    _write_state(state)
    return result


def _stamp_scheduled(settings) -> None:
    """Mark today's slot spent even when nothing matched, so we don't spin."""
    state = _read_state()
    now = datetime.now()
    state['last_scheduled_date'] = now.strftime('%Y-%m-%d')
    state['last_scheduled_time'] = _scheduled_time(settings)
    _write_state(state)


def summarise(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return 'No result recorded'
    if result.get('error'):
        return str(result['error'])
    picks = result.get('picks') or []
    got = [p for p in picks if p.get('grabbed')]
    if got:
        names = '; '.join(
            f"{p['title']} ({p.get('year')}) [{p.get('collection_name')}]"
            for p in got[:4])
        return f"Grabbed {len(got)}: {names}"
    if picks and result.get('dry_run'):
        names = '; '.join(
            f"{p['title']} ({p.get('year')}) [{p.get('collection_name')}]"
            for p in picks[:4])
        return f"Would grab {len(picks)}: {names}"
    if picks:
        details = '; '.join(
            f"{p.get('title')} — {p.get('detail')}" for p in picks[:4])
        return f"Nothing grabbed: {details}"
    return result.get('reason') or 'Nothing to grab'


def _log_entry(result: Dict[str, Any], at: str) -> Dict[str, Any]:
    picks = result.get('picks') or []
    got = [p for p in picks if p.get('grabbed')]
    return {
        'at': at,
        'outcome': ('grabbed' if got
                    else 'error' if result.get('error')
                    else 'failed' if picks
                    else 'nothing'),
        'summary': summarise(result),
        'candidates': result.get('candidates'),
        'grabbed': len(got),
        'picks': [
            {
                'title': p.get('title'),
                'year': p.get('year'),
                'tmdb_id': p.get('tmdb_id'),
                'collection_name': p.get('collection_name'),
                'grabbed': p.get('grabbed'),
                'detail': p.get('detail'),
            }
            for p in picks
        ],
    }


def preview() -> Dict[str, Any]:
    return run_cycle(dry_run=True)


def trigger_manual_run(dry_run: bool = False) -> None:
    threading.Thread(target=lambda: _guarded(dry_run), daemon=True).start()


def _guarded(dry_run: bool, scheduled: bool = False) -> None:
    _hydrate_status()
    with _lock:
        if _last_status['running']:
            logger.warning('Franchise Completer already running, skipping')
            return
        _last_status['running'] = True
    try:
        result = run_cycle(dry_run=dry_run, scheduled=scheduled)
    except Exception as e:
        logger.error('Franchise Completer cycle error: %s', e, exc_info=True)
        result = {'error': f'Run failed: {e}', 'dry_run': dry_run, 'grabbed': 0, 'picks': []}

    at = datetime.now(timezone.utc).isoformat()
    with _lock:
        _last_status['last_result'] = result
        if not dry_run:
            _last_status['last_run'] = at
            _last_status['cycle_count'] += 1
        _last_status['running'] = False

    logger.info('Franchise Completer: %s', summarise(result))

    if dry_run:
        return
    try:
        state = _read_state()
        state['last_run'] = at
        # Drop bulky eligible list from persisted result.
        slim = {k: v for k, v in result.items() if k != 'eligible_collections'}
        state['last_result'] = slim
        _write_state(state)
    except Exception as e:
        logger.warning('Franchise Completer: could not persist last run: %s', e)
    _append_log(_log_entry(result, at))


# ── Scheduler ─────────────────────────────────────────────────────────────

def start_franchise_completer_thread() -> None:
    from app import instance as _instance
    if not _instance.writes_allowed('franchise_completer'):   # LUMA_INSTANCE=test: off unless opted in
        return None
    _hydrate_status()
    t = threading.Thread(target=_scheduler_loop, daemon=True,
                         name='franchise-completer')
    t.start()
    logger.info(
        'Franchise Completer scheduler started (disabled until enabled in settings)')


def _scheduler_loop() -> None:
    time.sleep(150)  # after Genre Roulette's startup burst
    while True:
        try:
            _maybe_run()
        except Exception as e:
            logger.error('Franchise Completer scheduler error: %s', e, exc_info=True)
        time.sleep(_sleep_seconds())


def _sleep_seconds() -> float:
    try:
        nxt = _next_run_dt()
        if nxt:
            delta = (nxt - datetime.now()).total_seconds()
            if 0 < delta < _CHECK_INTERVAL_SECONDS:
                return delta + 1
    except Exception as e:
        logger.warning('Franchise Completer: falling back to poll interval: %s', e)
    return _CHECK_INTERVAL_SECONDS


def _maybe_run() -> None:
    from app.settings_manager import SettingsManager
    settings = SettingsManager()

    if not settings.get('automation.franchise_completer.enabled', False):
        return

    at = _scheduled_time(settings)
    hour, minute = (int(p) for p in at.split(':'))
    now = datetime.now()
    if now < now.replace(hour=hour, minute=minute, second=0, microsecond=0):
        return

    state = _read_state()
    if (state.get('last_scheduled_date') == now.strftime('%Y-%m-%d')
            and state.get('last_scheduled_time') == at):
        return

    logger.info('Franchise Completer: starting nightly run (scheduled %s)', at)
    _guarded(dry_run=False, scheduled=True)
