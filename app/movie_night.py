"""
Movie Night — one weekly grab: the best canon film missing from Plex, in 2160p Remux.

Deliberately one job with one bar:

    Walk the canon ranking best-first, skip anything already in Plex, and grab
    the first film that has a real 2160p Remux release. Nothing else qualifies —
    not 2160p WEB, not 1080p Remux. A film with no 4K remux is parked with a
    date and re-checked after _RECHECK_DAYS, because "no remux yet" is the
    normal outcome under an absolute bar, not an error.

Upgrading films already in Plex is upgrade_finder's job, not this one. This
module only fills gaps.

Configurable under automation.movie_night.*:
    enabled, weekday, hour, quality_profile_id, trackers, min_year,
    imdb_min_votes, imdb_min_rating
Everything else — seeder floor, search depth, re-check window, how a release is
chosen among several remuxes — is a constant below. They were settings once;
they were noise.

An IMDb fame/quality gate sits in front of the indexer search: expensive
60–80GB remuxes only fire for films that clear both a vote floor (fame) and a
rating floor (regard). Obscure mid-tier canon entries that merely happen to
have a remux available are skipped with a clear log line.

Self-contained: own state file (data/movie_night_state.json), own DB reads
(canon.db, plus upgrade_library.db READ-ONLY), disabled by default.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Remuxes of older films are thinly seeded — a high floor rejects legitimate
# releases far more often than it catches dead ones.
_MIN_SEEDERS = 3
# How deep the weekly walk goes looking for a film with a 4K remux. Bounds
# runtime: each step is one indexer search plus a 1s pause.
_MAX_SEARCHES = 40
# A film with no 4K remux today may well have one next quarter.
_RECHECK_DAYS = 90
# Consecutive failed searches that mean the indexer is down rather than the
# films being obscure — abort the run instead of burning the weekly slot.
_MAX_SEARCH_FAILURES = 3
# Failed grabs of the same film before it's parked so the queue can advance.
_MAX_GRAB_FAILURES = 3
# Films a single run may try to grab before giving up. A refusal used to end the
# run — one film Radarr wouldn't take cost the whole week, and three weeks in a
# row before that film was finally parked. The walk now moves on to the next
# candidate instead, so a run comes back empty only if every one of these was
# refused, which is a systemic fault worth reporting rather than a bad pick.
_MAX_GRAB_ATTEMPTS = 6
_DEFAULT_WEEKDAY = 4   # Monday=0 … Friday=4
_DEFAULT_HOUR = 18
_DEFAULT_MINUTE = 0
# IMDb floors for auto-grabbing a 60-80GB remux. Both apply (AND). Measured
# against recent weak picks: Brick ~113k/7.1 and King Richard ~169k/7.5 both
# fail votes>=200k; Eyes Wide Shut / The Lighthouse / Dark City still clear.
# Genre Roulette is configured at 250k votes with no rating floor; Movie Night
# spends far more disk per grab so it keeps a rating floor too.
_DEFAULT_IMDB_MIN_VOTES = 200_000
_DEFAULT_IMDB_MIN_RATING = 7.0
_IMDB_CACHE_DAYS = 90
_IMDB_CACHE_MAX = 2000
_IMDB_MAX_LOOKUPS = 60
_IMDB_MAX_FAILURES = 3
# Cheap TMDb-vote prefilter before spending an OMDb call. TMDb runs ~1-2% of
# IMDb vote counts; ratio 100 matched Genre Roulette measured samples.
_PREFILTER_VOTES_RATIO = 100
_PREFILTER_VOTES_MIN = 100
_MAX_LOG_ENTRIES = 200
# Poll floor only — _sleep_seconds() shortens this to land on the exact
# scheduled minute. A coarse grid would visibly miss a schedule that carries
# minutes: at 15 min, a 00:00 slot could fire as late as 00:14.
_CHECK_INTERVAL_SECONDS = 5 * 60

_TIER_2160P_REMUX = 5

_last_status: Dict[str, Any] = {
    'running': False,
    'last_run': None,
    'last_result': None,
    'cycle_count': 0,
}
_lock = threading.Lock()
_hydrated = False


# ── Trackers ──────────────────────────────────────────────────────────────

# The only trackers worth searching for a 4K movie remux. Not derived from
# Prowlarr's advertised capabilities on purpose: MoreThanTV publishes movie
# categories including Movies/UHD, so capability detection would happily include
# it, but it's a TV tracker in practice and searching it for remuxes is wasted
# traffic. Which trackers actually carry the goods is a judgement, not a lookup.
MOVIE_TRACKERS = ('BeyondHD', 'IPTorrents', 'PrivateHD')


TRACKERS_SETTING = 'automation.movie_night.trackers'


def eligible_trackers(client) -> List[Dict[str, Any]]:
    """Configured indexers that Movie Night is allowed to search."""
    from app import tracker_selection
    return tracker_selection.eligible(client, MOVIE_TRACKERS)


def _tracker_plan(settings, client) -> Dict[str, Any]:
    """What this run may search: {'names', 'ids', 'unknown', 'error'}."""
    from app import tracker_selection
    return tracker_selection.plan(settings, client, MOVIE_TRACKERS,
                                  TRACKERS_SETTING)


# No floor by default: the canon ranking is deliberately age-weighted, so
# silently excluding the silent era would contradict the list it walks.
_DEFAULT_MIN_YEAR = 0


def _min_year(settings) -> int:
    """Oldest release year the walk will consider. 0 means no floor.

    The canon reaches back to the 1920s and the age bonus in canon_index pushes
    those films up the ranking, so without a floor the top of the missing list
    skews much older than most people want to actually sit down and watch.
    """
    try:
        v = int(settings.get('automation.movie_night.min_year',
                             _DEFAULT_MIN_YEAR) or 0)
    except (TypeError, ValueError):
        return 0
    # Guards a stray value (a 2-digit year, a negative) from filtering the whole
    # list to nothing.
    return v if 1800 < v < 2200 else 0

def _imdb_min_votes(settings) -> int:
    """IMDb vote-count fame floor. 0 disables the vote half of the gate."""
    try:
        v = int(settings.get('automation.movie_night.imdb_min_votes',
                             _DEFAULT_IMDB_MIN_VOTES) or 0)
    except (TypeError, ValueError):
        return _DEFAULT_IMDB_MIN_VOTES
    return max(0, min(v, 10_000_000))

def _imdb_min_rating(settings) -> float:
    """IMDb rating floor. 0 disables the rating half of the gate."""
    try:
        v = float(settings.get('automation.movie_night.imdb_min_rating',
                               _DEFAULT_IMDB_MIN_RATING) or 0)
    except (TypeError, ValueError):
        return _DEFAULT_IMDB_MIN_RATING
    return max(0.0, min(v, 10.0))

def _tmdb_key(settings) -> str:
    try:
        from app.config import Config
        return (settings.get('apis.tmdb.api_key') or Config.TMDB_API_KEY or '').strip()
    except Exception:
        return (settings.get('apis.tmdb.api_key') or '').strip()

def _imdb_fresh(hit) -> bool:
    if not isinstance(hit, dict) or 'at' not in hit:
        return False
    try:
        cutoff = (date.today() - timedelta(days=_IMDB_CACHE_DAYS)).isoformat()
        return str(hit.get('at') or '')[:10] >= cutoff
    except Exception:
        return False

def _prune_imdb_cache(cache: Dict[str, Any]) -> Dict[str, Any]:
    fresh = {k: v for k, v in (cache or {}).items() if _imdb_fresh(v)}
    if len(fresh) <= _IMDB_CACHE_MAX:
        return fresh
    ranked = sorted(fresh.items(), key=lambda kv: str(kv[1].get('at') or ''),
                    reverse=True)[:_IMDB_CACHE_MAX]
    return dict(ranked)

def _prefilter_tmdb_votes(imdb_min_votes: int) -> int:
    """TMDb vote floor derived from the IMDb vote floor (saves OMDb calls)."""
    if imdb_min_votes <= 0:
        return 0
    return max(_PREFILTER_VOTES_MIN, imdb_min_votes // _PREFILTER_VOTES_RATIO)

def _imdb_gate_reason(votes, rating, min_votes: int, min_rating: float) -> str:
    """Human skip reason, or empty string when the film clears."""
    if min_votes and votes is None:
        return f'IMDb votes unknown (need >={min_votes:,})'
    if min_votes and (votes or 0) < min_votes:
        return f'IMDb votes {votes:,} < {min_votes:,}'
    if min_rating and rating is None:
        return f'IMDb rating unknown (need >={min_rating})'
    if min_rating and (rating or 0) < min_rating:
        return f'IMDb rating {rating} < {min_rating}'
    return ''


def _profile_id(settings) -> int:
    """Radarr profile for the grab.

    Reads the old `premium_quality_profile_id` as a fallback so an existing
    install keeps working after the two-track split was removed.
    """
    for key in ('automation.movie_night.quality_profile_id',
                'automation.movie_night.premium_quality_profile_id'):
        try:
            v = int(settings.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if v:
            return v
    return 0


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
    return _data_dir() / 'movie_night_state.json'


def _log_path() -> Path:
    return _data_dir() / 'movie_night_log.json'


def _read_state() -> Dict[str, Any]:
    """State, with parked films normalised to {tmdb_id: iso_date}.

    Earlier builds stored this under `exhausted`, and older ones still as a flat
    list of permanently-dead ids. Both are migrated in; legacy list entries are
    dated to the epoch so they fall straight out of the re-check window and get
    retried on the next run.
    """
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                data.setdefault('grabbed_tmdb_ids', [])
                data.setdefault('last_run_date', None)
                data.setdefault('grab_failures', {})
                # When the *weekly* run last fired. Distinct from last_run_date,
                # which any run writes — see _maybe_run.
                data.setdefault('last_scheduled_date', None)
                data.setdefault('last_scheduled_time', None)
                # Survives a restart, which an in-memory status cannot. A job
                # that runs once a week is otherwise reported as "Never" for
                # most of its life.
                data.setdefault('last_run', None)
                data.setdefault('last_result', None)
                # Tracker selection the parked verdicts were reached under. An
                # older state file predates the setting and is equivalent to
                # "all trackers", which is what an empty list means.
                data.setdefault('trackers', [])
                # tmdb_id -> {r, v, at} IMDb verdicts so weekly walks reuse
                # OMDb answers across runs.
                data.setdefault('imdb_cache', {})
                parked = (data.get('parked') or data.get('exhausted')
                          or data.get('exhausted_tmdb_ids') or {})
                if isinstance(parked, list):
                    parked = {str(i): '1970-01-01' for i in parked}
                data['parked'] = parked if isinstance(parked, dict) else {}
                data.pop('exhausted', None)
                data.pop('exhausted_tmdb_ids', None)
                data.pop('last_decade', None)
                return data
    except Exception as e:
        logger.warning(f'Movie Night: failed to read state: {e}')
    return {'grabbed_tmdb_ids': [], 'parked': {}, 'last_run_date': None,
            'last_scheduled_date': None, 'last_scheduled_time': None,
            'trackers': [], 'grab_failures': {}, 'imdb_cache': {},
            'last_run': None, 'last_result': None}


def _active_parked(state: Dict[str, Any]) -> set:
    """tmdb_ids still inside their re-check window."""
    cutoff = date.today() - timedelta(days=_RECHECK_DAYS)
    out = set()
    for tid, when in (state.get('parked') or {}).items():
        try:
            if date.fromisoformat(str(when)[:10]) >= cutoff:
                out.add(int(tid))
        except (ValueError, TypeError):
            continue
    return out


def _write_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning(f'Movie Night: failed to write state: {e}')


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
        logger.warning(f'Movie Night: failed to write log: {e}')


def _hydrate_status() -> None:
    """Seed the in-memory status from disk, once per process.

    Without this the card reads "Never" after every restart. A weekly job spends
    almost all of its life in that window, which is indistinguishable from one
    that has never fired.
    """
    global _hydrated
    with _lock:
        if _hydrated:
            return
        _hydrated = True
        state = _read_state()
        if state.get('last_run'):
            _last_status['last_run'] = state['last_run']
            _last_status['last_result'] = state.get('last_result')
        elif state.get('last_run_date'):
            # State written before last_run existed. The date is all we have —
            # better than reporting "Never" for a run we know happened.
            _last_status['last_run'] = f"{state['last_run_date']}T00:00:00+00:00"


def _int_setting(settings, key: str, default: int, low: int, high: int) -> int:
    """A clamped integer setting — a half-typed value must not park a schedule."""
    try:
        v = int(settings.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(v, high))


def _scheduled_time(settings) -> str:
    """The configured run time as ``HH:MM``, in the server's clock.

    Stored as two integers rather than a string so a half-typed "2" in the time
    field can't park the schedule at an unparseable value.
    """
    hour = _int_setting(settings, 'automation.movie_night.hour',
                        _DEFAULT_HOUR, 0, 23)
    minute = _int_setting(settings, 'automation.movie_night.minute',
                          _DEFAULT_MINUTE, 0, 59)
    return f'{hour:02d}:{minute:02d}'


def _next_run_dt() -> Optional[datetime]:
    """When the weekly run is next due, or None if it is disabled.

    Naive local on purpose: the time setting is the server's clock, so an aware
    UTC instant would render in the browser as a different time than the one
    typed into the field.
    """
    from app.settings_manager import SettingsManager
    settings = SettingsManager()
    if not settings.get('automation.movie_night.enabled', False):
        return None

    weekday = _int_setting(settings, 'automation.movie_night.weekday',
                           _DEFAULT_WEEKDAY, 0, 6)
    at = _scheduled_time(settings)
    hour, minute = (int(p) for p in at.split(':'))
    now = datetime.now()
    state = _read_state()
    done = (state.get('last_scheduled_date') == now.strftime('%Y-%m-%d')
            and state.get('last_scheduled_time') == at)

    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (weekday - now.weekday()) % 7
    if days_ahead == 0:
        if done:
            return slot + timedelta(days=7)
        if now >= slot:
            # Past the time with the slot unspent — the next check picks it up.
            return now.replace(second=0, microsecond=0)
        return slot
    return slot + timedelta(days=days_ahead)


def _next_run() -> Optional[str]:
    try:
        nxt = _next_run_dt()
        return nxt.isoformat() if nxt else None
    except Exception as e:
        logger.warning(f'Movie Night: could not compute next run: {e}')
        return None


def get_status() -> Dict[str, Any]:
    _hydrate_status()
    with _lock:
        out = dict(_last_status)
    out['next_run'] = _next_run()
    # Named, not implied. A bare "next run 00:00" is unreadable when the
    # container's zone and the viewer's don't agree — which is exactly how a
    # schedule four hours off its intended hour went unnoticed here.
    try:
        out['server_tz'] = datetime.now().astimezone().tzname()
    except Exception:
        out['server_tz'] = None
    return out


def get_log() -> List[Dict[str, Any]]:
    """Run history, newest first, with pre-`summary` rows filled in.

    Older builds only logged successful grabs and stored bare release fields, so
    those rows have no outcome or summary and would render blank.
    """
    try:
        p = _log_path()
        entries = json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []

    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if not e.get('summary'):
            e = dict(e)
            e['outcome'] = e.get('outcome') or 'grabbed'
            e['summary'] = (f"Grabbed {e.get('title')} ({e.get('year')}) — "
                            f"{e.get('quality')}, {e.get('size_gb')}GB, "
                            f"{e.get('tracker')}")
        out.append(e)
    out.reverse()
    return out


# ── Release selection ─────────────────────────────────────────────────────

def _search_releases(client, title: str, year: Optional[int],
                     indexer_ids: Optional[List[int]] = None,
                     tracker_names: Optional[List[str]] = None) -> Optional[List[Dict[str, Any]]]:
    """Targeted indexer search for one film, classified into quality tiers.

    *indexer_ids* keeps the request from reaching trackers the user excluded;
    *tracker_names* filters what comes back, which is the only lever available
    on a Torznab aggregate feed and a cheap consistency check on Prowlarr.

    Returns None when the search could not be performed, as distinct from []
    for "searched fine, nothing matched". Collapsing the two is what let a
    Prowlarr outage look like a clean no-result run and burn the weekly slot.
    """
    from app.upgrade_finder import _classify_tracker_quality, _normalize_title

    query = f'{title} {year}' if year else title
    try:
        raw = client._search_single(query, '2000', 100, indexer_ids or None)
        # Prowlarr returns raw JSON that still needs normalising; Torznab's
        # _search_single already parses its RSS into the same shape.
        parsed = client._parse_items(raw) if hasattr(client, '_parse_items') else raw
    except Exception as e:
        logger.warning(f'Movie Night: indexer search failed for {query}: {e}')
        return None

    allowed = {n.strip().lower() for n in (tracker_names or [])}
    want = _normalize_title(title)
    out = []
    for item in parsed:
        if allowed and (item.get('tracker') or '').strip().lower() not in allowed:
            continue
        got = _normalize_title(item.get('title', ''))
        # Loose containment match in both directions — indexer titles carry
        # edition/region junk, and our canon titles are sometimes shorter.
        if want and got and want not in got and got not in want:
            continue
        label, tier = _classify_tracker_quality(
            item.get('resolution', ''), item.get('torrent_name', ''))
        out.append({**item, 'quality_label': label, 'tier': tier})
    return out


# Remux groups with a reputation for untouched, complete discs. Membership is
# a strong positive signal; absence is not a negative one.
_PREMIUM_GROUPS = (
    'framestor', 'bluranium', 'kralimarko', 'hdmaniacs', 'w4nk3r', 'triton',
    '3l', 'bmf', 'pmp', 'hifi', 'surcode', 'zqz', 'tayto', 'flux', 'sicfoi',
)

# Format markers worth paying disk for, with their score. Dolby Vision and
# lossless object audio are the whole reason to hold a 4K remux.
_FORMAT_MARKERS = (
    (30.0, ('dolby vision', 'dovi', ' dv ', '.dv.', 'dv hevc')),
    (25.0, ('atmos',)),
    (20.0, ('hdr10+', 'hdr10plus')),
    (15.0, ('truehd',)),
    (12.0, ('imax',)),
    (10.0, ('dts-hd', 'dtshd', 'dts-x', 'dtsx')),
    (8.0, ('criterion',)),
    (6.0, ('hybrid',)),
)
_GROUP_BONUS = 25.0
_SIZE_WEIGHT = 0.45     # per GB, saturating — a bigger remux is a fuller disc
_SEEDER_WEIGHT = 0.06   # health only; the most-seeded remux is often the smallest


def _score_release(r: Dict[str, Any]) -> float:
    """Rank remuxes of the same film by what's actually in the file.

    Seeders measure popularity, not fidelity, so they count for very little.
    Format markers and group reputation decide.
    """
    name = f"{r.get('torrent_name') or ''}".lower()
    score = 0.0

    for points, needles in _FORMAT_MARKERS:
        if any(n in name for n in needles):
            score += points

    if any(g in name for g in _PREMIUM_GROUPS):
        score += _GROUP_BONUS

    score += min(float(r.get('size_gb') or 0), 120) * _SIZE_WEIGHT
    score += min(float(r.get('seeders') or 0), 200) * _SEEDER_WEIGHT
    return score


# Language tags that mean the audio is a foreign dub. Radarr's profile wants
# English and parses these same tokens out of the release name, so a release
# carrying one is rejected at the push — cheaper to skip it here than to spend a
# grab attempt discovering it.
_FOREIGN_AUDIO_TAGS = (
    'german', 'french', 'italian', 'ita', 'spanish', 'castellano', 'latino',
    'russian', 'rus', 'hindi', 'tamil', 'telugu', 'korean', 'japanese',
    'chinese', 'cantonese', 'mandarin', 'thai', 'polish', 'czech', 'hungarian',
    'turkish', 'portuguese', 'brazilian', 'dutch', 'nordic', 'swedish',
    'danish', 'norwegian', 'finnish', 'greek', 'hebrew', 'arabic', 'ukrainian',
    'romanian', 'bulgarian', 'vietnamese', 'indonesian', 'dubbed', 'dublado',
)
# Tags sit between separators, never inside a word: matches ".GERMAN." but not
# "Germany".
_FOREIGN_AUDIO_RE = re.compile(
    r'(?<![a-z])(?:' + '|'.join(_FOREIGN_AUDIO_TAGS) + r')(?![a-z])')
# Release names run Title.Year.Quality.Source.Audio-Group. Everything before the
# year is the film's own title, and scanning it for language names is how you
# reject "The.French.Connection.2160p.Remux" as a French dub — so the scan
# starts after the year, which is also where Radarr looks.
_YEAR_RE = re.compile(r'(?:19|20)\d{2}')


def _is_english_audio(release: Dict[str, Any]) -> bool:
    """False when the release name advertises a foreign-language audio track.

    Absence of any tag reads as English, matching both the scene convention and
    Radarr's own default for an untagged release.
    """
    name = f"{release.get('torrent_name') or ''}".lower()
    years = list(_YEAR_RE.finditer(name))
    # Last match, not first: a title can itself contain a year ("2001: A Space
    # Odyssey", "Blade Runner 2049").
    tags = name[years[-1].end():] if years else name
    return not _FOREIGN_AUDIO_RE.search(tags)


def _pick(releases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Best English-audio 2160p Remux, or nothing.

    No fallback tier by design: a 2160p WEB is not what this is for, and a
    1080p Remux is a compromise. If no 4K remux exists the film waits.
    """
    pool = [r for r in releases
            if r['tier'] == _TIER_2160P_REMUX
            and (r.get('seeders') or 0) >= _MIN_SEEDERS
            and _is_english_audio(r)]
    return max(pool, key=_score_release) if pool else None


# ── Candidates ────────────────────────────────────────────────────────────

def _ensure_languages(films: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Backfill missing `original_language` values, once, and re-read.

    The language gate below is only as good as this column. A canon.db built
    before the column existed has it empty for every film, and gating on that
    would reject the entire list. Filling it is a one-off TMDb pass over ids we
    already hold; after it completes this is a no-op.
    """
    from app import canon_index

    if all(f['original_language'] for f in films):
        return films

    try:
        from app.config import Config
        from app.settings_manager import SettingsManager
        api_key = SettingsManager().get('apis.tmdb.api_key') or Config.TMDB_API_KEY
    except Exception:
        api_key = ''
    if not api_key:
        logger.warning('Movie Night: cannot backfill canon languages — '
                       'no TMDb API key')
        return films

    try:
        canon_index.backfill_languages(api_key)
    except Exception as e:
        logger.warning(f'Movie Night: canon language backfill failed: {e}')
        return films
    return canon_index.get_canon(with_ownership=True)


def _candidates(min_year: int = 0) -> Dict[str, Any]:
    """English-language canon films absent from Plex, best-first.

    *min_year* drops anything released before it, for the very reasonable case
    of not wanting a 1920s film turning up on movie night. 0 means no floor.

    Non-English films are excluded outright rather than attempted and parked.
    The Radarr profile Movie Night grabs into wants English audio, so a Korean
    or Japanese pick is rejected at the push every time — and because the walk
    stops at the *first* film with a usable remux, one such film at the top of
    the list blocks the automation for three weeks before it parks and the walk
    can advance. The canon backlog is mostly foreign-language, so that state was
    effectively permanent.
    """
    from app import canon_index

    films = canon_index.get_canon(with_ownership=True)
    if not films:
        return {'error': 'Canon index has not been built yet', 'films': []}

    if not any(f['owned'] for f in films):
        # Ownership comes from upgrade_library.db. If it reads empty, every
        # canon film looks missing and this would happily start downloading the
        # entire list. Refuse instead.
        return {'error': 'Ownership data unavailable (upgrade_library.db empty '
                         'or unsynced) — refusing to run',
                'films': []}

    films = _ensure_languages(films)
    if not any(f['original_language'] for f in films):
        # Same shape of hazard as empty ownership: gating on a column that is
        # blank everywhere silently reduces the candidate list to nothing, and a
        # weekly automation that finds nothing looks identical to one that is
        # broken. Refuse and say why.
        return {'error': 'Canon language data unavailable — cannot tell which '
                         'films are English. Rebuild the film list.',
                'films': []}

    state = _read_state()
    grabbed = set(state.get('grabbed_tmdb_ids') or [])
    parked = _active_parked(state)

    missing = [f for f in films if not f['owned']]
    english = [f for f in missing
               if f['original_language'] == canon_index.ENGLISH_LANG]

    # Requires a positive 'en', so a film whose language never resolved is
    # skipped rather than attempted. That is the safe direction for an
    # irreversible grab, but it is a silent exclusion, so say how many.
    unknown = len([f for f in missing if not f['original_language']])
    if unknown:
        logger.info('Movie Night: %d missing films have no language on record '
                    'and are excluded until the backfill resolves them', unknown)

    # A film with no year on record can't be judged against the floor, so it is
    # excluded while one is set — the floor exists to stop surprises.
    recent = ([f for f in english if int(f.get('year') or 0) >= min_year]
              if min_year else english)

    return {'films': [f for f in recent
                      if int(f['tmdb_id']) not in grabbed
                      and int(f['tmdb_id']) not in parked],
            'missing_total': len(missing),
            'language_unknown': unknown,
            'skipped_foreign': len(missing) - len(english) - unknown,
            'skipped_old': len(english) - len(recent),
            'min_year': min_year}


# ── Grab ──────────────────────────────────────────────────────────────────

def _undo_failed_grab(radarr, movie: Dict[str, Any], created: bool,
                      prior, film: Dict[str, Any], reason: str) -> None:
    """Put Radarr back how it was after a refused push.

    A refusal used to leave the film sitting in Radarr **monitored on the 2160p
    Remux profile**, which is a standing instruction to Radarr's RSS sync to
    fetch a 60-80GB remux for it later, unprompted. That is not a cosmetic
    leftover: one refusal is ~65GB of download nobody asked for, and a run that
    now tries up to _MAX_GRAB_ATTEMPTS films could queue most of a terabyte.

    So a grab either lands the release it chose or leaves no trace. Only what
    this attempt changed is undone — a film that was already in Radarr keeps its
    own monitored state and profile, because it may well belong to the 4K
    upgrade automation, which shares this quality profile.
    """
    try:
        if created:
            # It was not in Radarr before this attempt. Remove it, files
            # untouched — there are none, the push is what failed.
            if radarr.delete_movie(movie['id'], delete_files=False):
                logger.info('Movie Night: removed %s from Radarr after refusal '
                            '(%s) — nothing left monitored', film['title'], reason)
            else:
                logger.error('Movie Night: could not remove %s (radarr id %s) '
                             'after a refused grab — it is still monitored on the '
                             'remux profile and RSS may fetch it',
                             film['title'], movie.get('id'))
            return

        if prior is None:
            return

        was_monitored, was_profile = prior
        movie['monitored'] = was_monitored
        if was_profile is not None:
            movie['qualityProfileId'] = was_profile
        if radarr.update_movie(movie):
            logger.info('Movie Night: restored %s to monitored=%s profile=%s '
                        'after refusal (%s)', film['title'], was_monitored,
                        was_profile, reason)
        else:
            logger.error('Movie Night: could not restore %s after a refused '
                         'grab — left monitored=%s profile=%s',
                         film['title'], movie.get('monitored'),
                         movie.get('qualityProfileId'))
    except Exception as e:
        logger.error('Movie Night: rollback after refused grab failed for %s: %s',
                     film.get('title'), e, exc_info=True)


def _grab(film: Dict[str, Any], release: Dict[str, Any], settings) -> tuple:
    """Add to Radarr on the configured profile and push the chosen release."""
    from app.radarr_api import RadarrAPI

    radarr_url = settings.get('radarr.url', '')
    radarr_key = settings.get('radarr.api_key', '')
    if not radarr_url or not radarr_key:
        return False, 'Radarr not configured'

    profile_id = _profile_id(settings)
    if not profile_id:
        return False, 'Movie Night quality profile is not set'

    radarr = RadarrAPI(radarr_url, radarr_key)
    roots = radarr.get_root_folders()
    if not roots:
        return False, 'No Radarr root folders'

    tmdb_id = int(film['tmdb_id'])
    # What Radarr looked like before this attempt, so a refusal can be undone.
    created = False
    prior = None
    try:
        existing = radarr.get_movie_by_tmdb_id(tmdb_id)
        if not existing:
            existing = radarr.add_movie(
                tmdb_id=tmdb_id,
                title=film['title'],
                year=int(film.get('year') or datetime.now().year),
                quality_profile_id=profile_id,
                root_folder_path=roots[0].get('path', ''),
                monitored=True,
                search_for_movie=False,
            )
            if not existing:
                return False, 'Radarr add returned None'
            created = True
        else:
            needs = False
            prior = (bool(existing.get('monitored')), existing.get('qualityProfileId'))
            if not existing.get('monitored'):
                existing['monitored'] = True
                needs = True
            if existing.get('qualityProfileId') != profile_id:
                existing['qualityProfileId'] = profile_id
                needs = True
            if not needs:
                prior = None  # nothing was changed, so nothing to restore
            if needs:
                updated = radarr.update_movie(existing)
                if updated:
                    existing = updated

        payload = {
            'title': release.get('torrent_name') or film['title'],
            'downloadUrl': release.get('download_url') or '',
            'protocol': 'torrent',
            'publishDate': release.get('pub_date') or datetime.now(timezone.utc).isoformat(),
            'indexer': release.get('tracker') or 'Prowlarr',
        }
        if release.get('info_hash'):
            payload['infoHash'] = release['info_hash']
        if release.get('size_gb'):
            try:
                payload['size'] = int(float(release['size_gb']) * (1024 ** 3))
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
        # download. A parked release comes back as a rejection, so the undo
        # below runs and the film is retried on a later spin.
        push = radarr.push_release_verified(payload, (existing or {}).get('id'))
        if push.get('ok'):
            return True, 'grabbed'
        reason = ('; '.join(push.get('rejections') or [])
                  or push.get('error') or 'rejected')
        _undo_failed_grab(radarr, existing, created, prior, film, reason)
        return False, reason
    except Exception as e:
        logger.error(f'Movie Night grab failed for {film["title"]}: {e}', exc_info=True)
        return False, str(e)


# ── Cycle ─────────────────────────────────────────────────────────────────

def run_cycle(dry_run: bool = False, settings=None,
              scheduled: bool = False) -> Dict[str, Any]:
    """Find and grab one film. Safe to call ad hoc; `dry_run` touches nothing.

    `scheduled` marks the weekly run, which is the only one that consumes the
    week's slot — see the `last_scheduled_date` write below.
    """
    from app.settings_manager import SettingsManager
    from app.indexer_api import get_indexer_client
    settings = settings or SettingsManager()

    started = datetime.now(timezone.utc).isoformat()
    cand = _candidates(min_year=_min_year(settings))
    if cand.get('error'):
        return {'error': cand['error'], 'started_at': started, 'dry_run': dry_run}

    client = get_indexer_client(settings)
    if not client:
        return {'error': 'No indexer configured', 'started_at': started, 'dry_run': dry_run}

    plan = _tracker_plan(settings, client)
    if plan['error']:
        return {'error': plan['error'], 'started_at': started, 'dry_run': dry_run}
    if plan['unknown'] and not plan['ids']:
        # Every selected tracker is gone, renamed, or no longer eligible.
        # Searching anyway would query everything and then filter the results to
        # nothing — a silent no-op every week. Say so instead.
        return {'error': 'None of the selected trackers are available: '
                         + ', '.join(plan['unknown']),
                'started_at': started, 'dry_run': dry_run}
    if plan['unknown']:
        logger.warning('Movie Night: ignoring unavailable trackers: '
                       + ', '.join(plan['unknown']))
    tracker_names = plan['names']

    films = cand['films']
    scanned: List[Dict[str, Any]] = []
    parked_now: List[int] = []
    pick = release = None
    # Every grab this run tried, refusals included — the record that turns
    # "nothing grabbed" from a dead end into a list of reasons.
    attempts: List[Dict[str, Any]] = []
    first_usable = None
    grabbed = False

    # ── IMDb fame/quality gate ──────────────────────────────────────────
    # Expensive remuxes need both a vote floor (fame) and a rating floor
    # (regard). Checked before any indexer search so obscure mid-tier canon
    # entries never burn a search slot or get parked as "no remux".
    imdb_min_votes = _imdb_min_votes(settings)
    imdb_min_rating = _imdb_min_rating(settings)
    omdb_key = (settings.get('apis.omdb.api_key') or '').strip()
    tmdb_key = _tmdb_key(settings)
    imdb_on = bool(omdb_key) and bool(imdb_min_votes or imdb_min_rating)
    if not imdb_on and (imdb_min_votes or imdb_min_rating):
        logger.warning('Movie Night: IMDb floors are set but no OMDb API key '
                       'is configured — refusing unknowns rather than grabbing '
                       'blind')
    state_at_start = _read_state()
    imdb_cache = dict(state_at_start.get('imdb_cache') or {})
    imdb_lookups = 0
    imdb_failures = 0
    skipped_imdb = 0
    skipped_imdb_titles: List[str] = []
    tmdb_prefilter = _prefilter_tmdb_votes(imdb_min_votes)

    def imdb_verdict(film: Dict[str, Any]) -> tuple:
        """(passes, stats_or_None, reason). Fail closed under a configured floor."""
        nonlocal imdb_lookups, imdb_failures
        if not imdb_on and not (imdb_min_votes or imdb_min_rating):
            return True, None, ''
        # Floors set but no OMDb key — never admit unknowns.
        if not imdb_on:
            return False, None, 'IMDb gate on but OMDb key missing'
        if imdb_failures >= _IMDB_MAX_FAILURES:
            return False, None, 'OMDb unreachable — IMDb gate fail-closed'

        # Cheap TMDb prefilter using the canon column we already hold.
        tmdb_votes = int(film.get('tmdb_votes') or 0)
        if tmdb_prefilter and tmdb_votes and tmdb_votes < tmdb_prefilter:
            return (False, {'r': None, 'v': None, 'at': date.today().isoformat()},
                    f'TMDb votes {tmdb_votes} < prefilter {tmdb_prefilter} '
                    f'(IMDb floor {imdb_min_votes:,})')

        key = str(film['tmdb_id'])
        hit = imdb_cache.get(key)
        if not _imdb_fresh(hit):
            if imdb_lookups >= _IMDB_MAX_LOOKUPS:
                return False, None, 'IMDb lookup budget exhausted'
            from app import imdb_ratings
            imdb_lookups += 1
            looked = imdb_ratings.lookup_by_tmdb_id(
                int(film['tmdb_id']), tmdb_key=tmdb_key, omdb_key=omdb_key)
            if looked is None:
                imdb_failures += 1
                return False, None, 'IMDb lookup failed'
            imdb_failures = 0
            hit = {
                'r': looked.get('rating'),
                'v': looked.get('votes'),
                'at': date.today().isoformat(),
            }
            imdb_cache[key] = hit

        reason = _imdb_gate_reason(hit.get('v'), hit.get('r'),
                                  imdb_min_votes, imdb_min_rating)
        return (not reason), hit, reason

    consecutive_failures = 0
    searches_done = 0
    for film in films:
        if searches_done >= _MAX_SEARCHES:
            break

        passes, imdb_stats, imdb_reason = imdb_verdict(film)
        if not passes:
            skipped_imdb += 1
            label = f"{film['title']} ({film.get('year')})"
            if len(skipped_imdb_titles) < 12:
                skipped_imdb_titles.append(f"{label} — {imdb_reason}")
            logger.info('Movie Night: skip %s — %s', label, imdb_reason)
            continue

        searches_done += 1
        releases = _search_releases(client, film['title'], film.get('year'),
                                    plan['ids'], tracker_names)

        if releases is None:
            # Couldn't search at all. Walking the remaining candidates would
            # fail the same way, so stop early rather than spending 40 doomed
            # requests, and leave the run unrecorded so it retries instead of
            # spending this week's slot on an outage.
            consecutive_failures += 1
            if consecutive_failures >= _MAX_SEARCH_FAILURES:
                logger.warning(
                    'Movie Night: aborting — %d consecutive indexer failures; '
                    'the run is not recorded and will be retried',
                    consecutive_failures)
                return {'error': 'Indexer unreachable — run abandoned, will retry',
                        'started_at': started, 'dry_run': dry_run,
                        'candidates': len(films), 'searched': len(scanned),
                        'scanned': scanned, 'trackers': tracker_names,
                        'pick': None, 'grabbed': False}
            time.sleep(1)
            continue
        consecutive_failures = 0

        chosen = _pick(releases)

        scanned.append({
            'rank': film['rank'],
            'title': film['title'],
            'year': film.get('year'),
            'tmdb_id': film['tmdb_id'],
            'releases_found': len(releases),
            'best': ({
                'quality': chosen['quality_label'],
                'seeders': chosen.get('seeders'),
                'size_gb': chosen.get('size_gb'),
                'tracker': chosen.get('tracker'),
                'torrent_name': (chosen.get('torrent_name') or '')[:90],
            } if chosen else None),
        })

        if chosen:
            # The first film with a usable release is what a dry run reports,
            # and the fallback `pick` if every grab below is refused — so the
            # card still names what the run was aiming at.
            if first_usable is None:
                first_usable = (film, chosen)

            if dry_run:
                pick, release = film, chosen
                break

            ok, detail = _grab(film, chosen, settings)
            attempts.append({
                'title': film['title'], 'year': film.get('year'),
                'tmdb_id': int(film['tmdb_id']), 'ok': bool(ok),
                'detail': detail,
                'torrent_name': chosen.get('torrent_name'),
            })
            if ok:
                pick, release = film, chosen
                grabbed = True
                break

            # Radarr refused this release. Moving on is the whole point: the run
            # is trying to come home with *a* film, not with this one.
            logger.warning('Movie Night: %s (%s) refused — %s; trying the next '
                           'candidate', film['title'], film.get('year'), detail)
            if len(attempts) >= _MAX_GRAB_ATTEMPTS:
                logger.error('Movie Night: %d grab attempts all refused — '
                             'stopping. Check the Radarr profile.', len(attempts))
                break
            time.sleep(1)
            continue

        # No 4K remux out there. Park it so next week's walk doesn't stall on
        # the same dead entries; _active_parked lets it back in later. Only when
        # the search actually returned something — an empty result is as likely
        # to be a flaky indexer as a genuinely unavailable film.
        if releases:
            parked_now.append(int(film['tmdb_id']))

        time.sleep(1)

    result: Dict[str, Any] = {
        'started_at': started,
        'dry_run': dry_run,
        'candidates': len(films),
        'skipped_foreign': cand.get('skipped_foreign', 0),
        'language_unknown': cand.get('language_unknown', 0),
        'skipped_old': cand.get('skipped_old', 0),
        'skipped_imdb': skipped_imdb,
        'skipped_imdb_titles': skipped_imdb_titles,
        'imdb_min_votes': imdb_min_votes,
        'imdb_min_rating': imdb_min_rating,
        'min_year': cand.get('min_year', 0),
        'searched': len(scanned),
        'scanned': scanned,
        'trackers': tracker_names,
        'pick': None,
        'grabbed': False,
        'attempts': attempts,
    }

    # Nothing was grabbed, but something was tried — report the run as aiming at
    # the first usable candidate so the card names a film and its refusal reason
    # rather than the bare "no candidate had a 2160p Remux" of a dead walk.
    if pick is None and first_usable is not None:
        pick, release = first_usable

    if pick:
        result['pick'] = {
            'title': pick['title'],
            'year': pick.get('year'),
            'tmdb_id': pick['tmdb_id'],
            'rank': pick['rank'],
            'quality': release['quality_label'],
            'size_gb': release.get('size_gb'),
            'seeders': release.get('seeders'),
            'tracker': release.get('tracker'),
            'torrent_name': release.get('torrent_name'),
        }
    elif films:
        reason = 'No candidate had a 2160p Remux available'
        if skipped_imdb:
            reason += (f' ({skipped_imdb} skipped by IMDb floors '
                       f'votes>={imdb_min_votes:,}/rating>={imdb_min_rating})')
        result['reason'] = reason
    else:
        # An empty candidate list is the one outcome most likely to be a setting
        # rather than a genuine dead end, so name the filter that emptied it.
        bits = []
        if cand.get('skipped_old'):
            bits.append(f"{cand['skipped_old']} excluded by the "
                        f"{cand['min_year']} year floor")
        if cand.get('skipped_foreign'):
            bits.append(f"{cand['skipped_foreign']} non-English")
        if skipped_imdb:
            bits.append(f"{skipped_imdb} below IMDb floors "
                        f"(votes>={imdb_min_votes:,}, rating>={imdb_min_rating})")
        result['reason'] = ('Nothing left to fill'
                            + (f" — {', '.join(bits)}" if bits else ''))

    if dry_run:
        if pick:
            result['reason'] = 'dry run — nothing grabbed'
        # Warm the IMDb cache on previews so the next weekly run is cheap.
        if imdb_cache:
            st = _read_state()
            st['imdb_cache'] = _prune_imdb_cache(imdb_cache)
            _write_state(st)
        return result

    # The grabs happened inside the walk, so a refusal could move on to the next
    # candidate instead of ending the run.
    result['grabbed'] = grabbed
    if attempts:
        result['grab_detail'] = next(
            (a['detail'] for a in reversed(attempts) if a['ok']),
            attempts[-1]['detail'])
        if not grabbed:
            result['reason'] = (
                f"{len(attempts)} candidate{'s' if len(attempts) != 1 else ''} "
                f"found a 2160p Remux but Radarr refused every one")

    state = _read_state()
    today = datetime.now().strftime('%Y-%m-%d')

    # A film whose grab keeps failing must not hold the queue. Refusals no
    # longer cost the run — the walk moves past them — but a film that is
    # persistently un-grabbable would still burn an attempt slot every week, so
    # it gets parked after _MAX_GRAB_FAILURES runs and stops being offered.
    # Counted per attempted film, not per run: a run can now refuse five films
    # and grab the sixth, and each of those five needs its own tally or the four
    # that were never the final pick would be retried forever.
    failures = dict(state.get('grab_failures') or {})
    for att in attempts:
        key = str(att['tmdb_id'])
        if att['ok']:
            failures.pop(key, None)
            continue
        failures[key] = int(failures.get(key, 0)) + 1
        if failures[key] >= _MAX_GRAB_FAILURES:
            logger.warning(
                'Movie Night: parking %s after %d failed grabs (%s)',
                att['title'], failures[key], att.get('detail'))
            parked_now.append(int(att['tmdb_id']))
            failures.pop(key, None)
    state['grab_failures'] = failures
    # A parked film means "no 2160p Remux on the trackers we searched". Change
    # which trackers those are and every one of those verdicts is stale —
    # without this, adding a tracker back wouldn't take effect for up to 90
    # days. Compared sorted, since re-picking the same set in a different order
    # is not a change.
    searched = sorted(tracker_names)
    parked_map = ({} if state.get('trackers') != searched
                  else dict(state.get('parked') or {}))
    for tid in parked_now:
        parked_map[str(tid)] = today
    state['parked'] = parked_map
    state['trackers'] = searched
    state['last_run_date'] = today
    # Only a scheduled run consumes the week's slot. Writing this from a manual
    # "Run now" meant pressing the button on a Friday morning silently cancelled
    # that evening's scheduled run.
    if scheduled:
        state['last_scheduled_date'] = today
        state['last_scheduled_time'] = _scheduled_time(settings)

    if result['grabbed'] and pick:
        grabbed_ids = set(state.get('grabbed_tmdb_ids') or [])
        grabbed_ids.add(int(pick['tmdb_id']))
        state['grabbed_tmdb_ids'] = sorted(grabbed_ids)[-5000:]

    if imdb_cache:
        state['imdb_cache'] = _prune_imdb_cache(imdb_cache)
    _write_state(state)
    return result


def summarise(result: Dict[str, Any]) -> str:
    """One line describing how a run ended, for the log and the settings card."""
    if not isinstance(result, dict):
        return 'No result recorded'
    if result.get('error'):
        return str(result['error'])
    pick = result.get('pick')
    attempts = result.get('attempts') or []
    refused = [a for a in attempts if not a.get('ok')]

    if result.get('grabbed') and pick:
        line = (f"Grabbed {pick['title']} ({pick.get('year')}) — "
                f"{pick.get('quality')}, {pick.get('size_gb')}GB, "
                f"{pick.get('tracker')}")
        # A grab on the third try is a success, but a silent one hides that two
        # films ahead of it were refused — which is how a misconfigured profile
        # stays invisible while the automation still delivers.
        if refused:
            line += (f" (after {len(refused)} refused: "
                     + '; '.join(f"{a['title']} — {a.get('detail')}" for a in refused[:3])
                     + ')')
        return line
    if pick and result.get('dry_run'):
        return f"Would grab {pick['title']} ({pick.get('year')})"
    if refused:
        return (f"Radarr refused all {len(refused)} candidate"
                f"{'s' if len(refused) != 1 else ''}: "
                + '; '.join(f"{a['title']} ({a.get('year')}) — {a.get('detail')}"
                            for a in refused[:4]))
    if pick:
        return (f"Failed to grab {pick['title']} ({pick.get('year')}): "
                f"{result.get('grab_detail') or 'unknown reason'}")
    searched = result.get('searched', 0)
    reason = result.get('reason') or 'Nothing grabbed'
    return f'{reason} (searched {searched})'


def _log_entry(result: Dict[str, Any], started: str) -> Dict[str, Any]:
    """A log row for *every* run, not just a successful grab.

    Only logging grabs is why a run that errored or came up empty left no trace
    at all — the automation looked like it had never fired.
    """
    pick = result.get('pick') or {}
    return {
        'at': started,
        'outcome': ('grabbed' if result.get('grabbed')
                    else 'error' if result.get('error')
                    else 'failed' if pick
                    else 'nothing'),
        # How many films the run had to try. A steady climb here is the early
        # warning that the profile or the tracker set is drifting out of spec.
        'attempted': len(result.get('attempts') or []),
        'refused': [
            {'title': a.get('title'), 'year': a.get('year'), 'detail': a.get('detail')}
            for a in (result.get('attempts') or []) if not a.get('ok')
        ],
        'summary': summarise(result),
        'candidates': result.get('candidates'),
        'searched': result.get('searched'),
        'title': pick.get('title'),
        'year': pick.get('year'),
        'tmdb_id': pick.get('tmdb_id'),
        'quality': pick.get('quality'),
        'size_gb': pick.get('size_gb'),
        'seeders': pick.get('seeders'),
        'tracker': pick.get('tracker'),
    }


def preview() -> Dict[str, Any]:
    """Dry run — what the next run would grab. Touches no external state."""
    return run_cycle(dry_run=True)


def trigger_manual_run(dry_run: bool = False) -> None:
    threading.Thread(target=lambda: _guarded(dry_run), daemon=True).start()


def _guarded(dry_run: bool, scheduled: bool = False) -> None:
    _hydrate_status()
    with _lock:
        if _last_status['running']:
            logger.warning('Movie Night already running, skipping')
            return
        _last_status['running'] = True
    try:
        result = run_cycle(dry_run=dry_run, scheduled=scheduled)
    except Exception as e:
        logger.error(f'Movie Night cycle error: {e}', exc_info=True)
        # A crash is the one outcome the user most needs to see, so it gets
        # recorded like any other rather than leaving the status untouched.
        result = {'error': f'Run failed: {e}', 'dry_run': dry_run}

    # Stamp last_run (and clear running) in one locked section so a status poll
    # never sees idle-with-stale last_run after a finished cycle.
    at = datetime.now(timezone.utc).isoformat()
    with _lock:
        _last_status['last_result'] = result
        if not dry_run:
            _last_status['last_run'] = at
            _last_status['cycle_count'] += 1
        _last_status['running'] = False

    logger.info('Movie Night: %s', summarise(result))

    # A preview is a question, not an event — it must not overwrite the record
    # of the last real run.
    if dry_run:
        return
    try:
        state = _read_state()
        state['last_run'] = at
        # `scanned` is up to 40 rows of per-film search detail — useful in the
        # live response, far too big to accumulate in the state file.
        state['last_result'] = {k: v for k, v in result.items() if k != 'scanned'}
        _write_state(state)
    except Exception as e:
        logger.warning(f'Movie Night: could not persist last run: {e}')
    _append_log(_log_entry(result, at))


# ── Weekly scheduler ──────────────────────────────────────────────────────

def _migrate_settings() -> None:
    """Carry the pre-simplification profile key forward, once.

    The two-track split is gone, so `premium_quality_profile_id` is now just
    `quality_profile_id`. `_profile_id` reads the old key as a fallback, but the
    settings UI binds to the new one — without this, an existing install shows
    an empty profile dropdown while the automation quietly still works.
    """
    try:
        from app.settings_manager import SettingsManager
        settings = SettingsManager()
        if settings.get('automation.movie_night.quality_profile_id', 0):
            return
        old = settings.get('automation.movie_night.premium_quality_profile_id', 0)
        if not old:
            return
        settings.update({'automation': {'movie_night': {
            'quality_profile_id': int(old)}}})
        logger.info('Movie Night: migrated quality profile setting to '
                    'automation.movie_night.quality_profile_id')
    except Exception as e:
        logger.warning(f'Movie Night: settings migration skipped: {e}')


def start_movie_night_thread() -> None:
    _migrate_settings()
    _hydrate_status()
    t = threading.Thread(target=_scheduler_loop, daemon=True, name='movie-night')
    t.start()
    logger.info('Movie Night scheduler started (disabled until enabled in settings)')


def _backfill_languages_once() -> None:
    """Fill canon language data off the request path.

    _ensure_languages would do this lazily, but the first call costs a TMDb
    round trip per film and `/api/movie-night/preview` is synchronous — the user
    would wear the whole minute as a hung request. Idempotent and one SELECT
    once it's done, so running it on every start is free.
    """
    try:
        from app import canon_index
        from app.config import Config
        from app.settings_manager import SettingsManager
        api_key = SettingsManager().get('apis.tmdb.api_key') or Config.TMDB_API_KEY
        if api_key:
            canon_index.backfill_languages(api_key)
    except Exception as e:
        logger.warning(f'Movie Night: canon language backfill skipped: {e}')


def _scheduler_loop() -> None:
    time.sleep(90)  # let the other automations finish their startup burst
    _backfill_languages_once()
    while True:
        try:
            _maybe_run()
        except Exception as e:
            logger.error(f'Movie Night scheduler error: {e}', exc_info=True)
        time.sleep(_sleep_seconds())


def _sleep_seconds() -> float:
    """How long to wait before the next check.

    A fixed poll can only fire on its own grid, so a 00:00 schedule would land
    wherever the grid happened to fall — up to _CHECK_INTERVAL_SECONDS late.
    Sleep exactly up to the next due time when that is sooner, and otherwise
    poll; the poll is what picks up a schedule changed mid-wait.
    """
    try:
        nxt = _next_run_dt()
        if nxt:
            delta = (nxt - datetime.now()).total_seconds()
            if 0 < delta < _CHECK_INTERVAL_SECONDS:
                return delta + 1  # land just past the minute, never just shy
    except Exception as e:
        logger.warning(f'Movie Night: falling back to poll interval: {e}')
    return _CHECK_INTERVAL_SECONDS


def _maybe_run() -> None:
    from app.settings_manager import SettingsManager
    settings = SettingsManager()

    if not settings.get('automation.movie_night.enabled', False):
        return

    weekday = _int_setting(settings, 'automation.movie_night.weekday',
                           _DEFAULT_WEEKDAY, 0, 6)
    at = _scheduled_time(settings)
    hour, minute = (int(p) for p in at.split(':'))

    now = datetime.now()
    if now.weekday() != weekday:
        return
    if now < now.replace(hour=hour, minute=minute, second=0, microsecond=0):
        return

    # Keyed on the scheduled run only. Using last_run_date here meant a manual
    # "Run now" earlier the same day cancelled the scheduled one. The HH:MM is
    # part of the key too: without it, moving the schedule after the day's run
    # had already fired would silently do nothing until next week, which reads
    # exactly like a scheduler that never runs at all.
    state = _read_state()
    if (state.get('last_scheduled_date') == now.strftime('%Y-%m-%d')
            and state.get('last_scheduled_time') == at):
        return

    logger.info(f'Movie Night: starting weekly run (scheduled {at})')
    _guarded(dry_run=False, scheduled=True)
