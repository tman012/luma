"""
Genre Roulette — a few well-regarded films you don't own, every night.

The counterweight to Movie Night. That one walks a fixed canon best-first and
holds an absolute 2160p Remux bar, so it is narrow by design and grabs one film
a week. This one is for breadth: pick a handful of genres at random, ask TMDb for
films in them that are well-liked but not necessarily canonical, drop everything
already in the library, and grab a couple in standard quality.

Randomising the *genres* is the whole point; the order *within* a genre is not.
Discover still uses with_genres, but only films whose PRIMARY TMDb genre
(genre_ids[0]) matches the spun genre are kept — secondary tags do not count.
Sorting the entire back catalogue by rating and walking it top-down converges on
the same few hundred famous films — but drawing Westerns and Animation one night
and Horror and Music the next, then taking the best-rated unowned film in each,
keeps that breadth while spending the night's slots on the films most worth
having. Each spun genre keeps trying its next fame-ranked passer until one
grabs or that genre is exhausted; only then may leftover slots fill globally
by fame. Films from the same TMDb collection are never grabbed twice in one
night (or against a recent grab of another part). So each genre is walked
best-rated-first from a saved cursor: tonight
resumes where the last night that drew this genre stopped, and the wheel works
its way down the ratings over weeks instead of paying to re-read the acclaimed
films it already owns.

Deliberately *not* configurable, for the same reason Movie Night isn't: how a
release is scored among several candidates, and the standard resolution/source
rules (which are reused from latest_auto_grab so the two agree).

Everything else is, under automation.genre_roulette.*:
    enabled, hour, genres_per_night, movies_per_night, genres, year_from,
    year_to, min_rating, min_votes, min_seeders, quality_profile_id, trackers

Self-contained: own state file (data/genre_roulette_state.json), own log, and a
READ-ONLY ownership read via canon_index. Disabled by default.
"""

import json
import logging
import os
import random
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# v173: the indexer name Radarr's history shows for a pushed release with no tracker name. Searches
# run on Luma's own trackers now (v172), so not 'Prowlarr'.
PUSH_INDEXER_FALLBACK = 'Luma'

TMDB_BASE = 'https://api.themoviedb.org/3'

# ── Constants that were settings in spirit and noise in practice ───────────

# TMDb caps /discover at page 500, and that cap is now the only one. A ceiling
# of 12 pages used to fence the draw into the ~240 best-known films per genre —
# exactly the region a deep library already owns outright. Measured 2026-09-03
# against a 4,622-film index: the top pages of every enabled genre were 100%
# owned, while page 30 of Adventure was 18/20 unowned. Depth costs fame, not
# quality: the bar is enforced by `vote_average`/`vote_count` in the query, so a
# film on page 30 still clears the same rating and vote floor as one on page 1.
#
# Depth is no longer paid for at random, though — the saved cursor resumes the
# walk at the frontier, so an owned page above it is read once rather than
# every night. Measured 2026-09-04, that frontier is currently page 1 of every
# genre: ordering by rating rather than vote count puts acclaimed-but-obscure
# films at the top, and those a deep library never bought. So the cursor costs
# nothing today and earns its keep later, as the nights below work downwards.
_MAX_DISCOVER_PAGES = 500
# Pages one genre may draw in the opening round while still short of its share.
_MAX_DISCOVER_PAGES_PER_GENRE = 10
# Pages a genre may add per top-up round, and how many rounds may run.
_TOPUP_PAGES = 4
_MAX_TOPUP_ROUNDS = 3
# Unowned candidates wanted per film we intend to grab. A candidate can still
# die at the indexer (no usable standard-quality release), at Radarr (size cap), or at the
# IMDb floors, so the pool has to be a multiple of the target rather than equal
# to it. Raised from 6 when the IMDb gate arrived, which rejects ~38% at the
# default floors.
_FRESH_PER_WANTED = 10
# Floor for the pool regardless of target, so a 1-film night still has slack.
_MIN_FRESH_POOL = 12

# IMDb floors. TMDb's own numbers are a weak filter for this: its ratings run
# high and compress (Kung Pow: Enter the Fist clears a TMDb 6.5 bar at 6.522,
# and Spenser Confidential at 6.504) while its vote counts are small enough
# that 300 admits genuinely obscure films. IMDb votes measure fame and IMDb
# rating measures regard, and the two disagree often enough that both are
# needed — Kung Pow has 50,110 IMDb votes, so a vote floor alone would not
# touch it; its 6.2 rating is what does.
#
# Measured on samples of the live pool: at rating>=6.5 with votes>=80k, 25% of
# the prefiltered pool passes — ~497 films, about 5 months at 3 a night, after
# which the widening ladder carries it. The IMDb vote floor is a hard wall —
# films below it are never grabbed, even if the night comes up short.
_DEFAULT_IMDB_MIN_VOTES = 120000
# Zero disables the rating gate entirely: fame is the whole criterion, on the
# view that a film 120,000 people bothered to rate is worth having regardless
# of what they scored it. The consequence is real and intended — the films
# clearing 120k votes run from 3.4 to 7.7, so the occasional famous disaster
# will come through. Measured, a 6.0 floor would have cut 7 of 33 of them, and
# cutting them roughly halves the pool.
_DEFAULT_IMDB_MIN_RATING = 0.0
# Votes accumulate, so a cached verdict goes stale slowly, not never.
_IMDB_CACHE_DAYS = 90
_IMDB_CACHE_MAX = 5000
# The TMDb vote prefilter is derived as imdb_min_votes / RATIO, never below
# MIN. RATIO comes from the measured relationship between the two vote counts
# (TMDb runs ~1-2% of IMDb, and 80,000/100 = 800 matched it exactly).
_PREFILTER_VOTES_RATIO = 100
_PREFILTER_VOTES_MIN = 100
# Bounds OMDb usage per run; its free tier allows 1,000 calls a day.
_IMDB_MAX_LOOKUPS = 80
# Consecutive lookup failures that mean OMDb is down rather than the films
# being unknown. Past this the gate fails closed — unknowns are not admitted.
_IMDB_MAX_FAILURES = 3
# When a night is still short after the configured IMDb floor left too few
# passers (or only franchise-blocked leftovers), step the vote floor down these
# stops and keep trying. Prefer skip-and-retry-next over aborting under target.
# Never climb above the configured floor. Rating floor stays as configured.
_IMDB_RELAX_VOTE_STOPS = (180000, 120000, 80000, 50000, 25000)
_IMDB_RELAX_VOTE_FLOOR = 25000
# Extra genres to borrow when spun genres + relaxation still cannot fill want.
_FILL_EXTRA_GENRES = 3
# TMDb belongs_to_collection lookups. Discover does not return collection
# membership, so each candidate needs a cheap movie detail call. Cache is
# long-lived (collections rarely change) and capped.
_COLLECTION_CACHE_MAX = 8000
# Cap on how many collection-detail part lists we keep in memory this run.
_COLLECTION_PARTS_CACHE_MAX = 200


# How far back "all years" reaches. Earlier than any film the trackers carry.
_WIDE_YEAR_FROM = 1900
# Widening ladder. The configured settings describe the *preferred* night, not
# the only permissible one: a library deep enough to own most of its own taste
# will drain the configured pool, and when it does the honest choice is to
# reach further rather than hand back one film. Measured 2026-09-03: the
# configured query spans ~6,500 genre-slots against 4,622 already owned, while
# stage 3 spans ~11,200 — so widening is what buys the years, not the page cap.
#
# Ordered least-compromise-first, and each stage keeps tonight's genre draw
# intact for as long as it can, because the genre wheel is the feature. Only
# once years, votes and rating have all been relaxed does the night borrow
# other genres. A stage only runs if the ones before it came up short.
# Breadth first, fame prefilter last. Reordered when the IMDb gate arrived:
# widening years or genres keeps the pool as famous as it was, while lowering
# the TMDb vote floor trades directly against IMDb-pass density, so it is the
# stage of last resort rather than the first thing tried.
_WIDENING_STAGES = (
    {'name': 'configured'},
    {'name': 'all years', 'years': True},
    {'name': 'other enabled genres', 'years': True,
     'extra_genres': 'enabled'},
    {'name': 'every genre', 'years': True, 'extra_genres': 'all'},
    {'name': 'every genre, 100+ votes', 'years': True, 'min_votes': 100,
     'extra_genres': 'all'},
    {'name': 'every genre, 50+ votes', 'years': True, 'min_votes': 50,
     'extra_genres': 'all'},
)
# Bounds the run: each attempt is one indexer search plus a pause.
_MAX_SEARCH_ATTEMPTS = 25
# Releases to offer Radarr per film before giving up on it. More than one
# because the profile's size cap can only be discovered by being refused; a
# small number because past the top few the remaining releases are worse on
# every axis anyway.
_MAX_RELEASE_ATTEMPTS = 4
# Consecutive failed searches that mean the indexer is down rather than the
# films being obscure — abort rather than burning the night's slot.
_MAX_SEARCH_FAILURES = 3
# Pause between indexer searches, seconds. Same courtesy as Movie Night.
_SEARCH_PAUSE = 1.0
_MAX_LOG_ENTRIES = 300
# Poll floor only — _sleep_seconds() shortens this to land on the exact
# scheduled minute. Tighter than Movie Night's 15 min because this schedule
# carries minutes, so a coarse grid would visibly miss the configured time.
_CHECK_INTERVAL_SECONDS = 5 * 60

_DEFAULT_HOUR = 3
_DEFAULT_MINUTE = 0
_DEFAULT_GENRES_PER_NIGHT = 3
_DEFAULT_MOVIES_PER_NIGHT = 3  # v276 (Taylor/prod): one movie from each of 3 genres
# No TMDb rating floor at all. It was 6.5, which demonstrably excluded famous,
# well-regarded films (The Dictator, TMDb 6.24 / IMDb 6.5 with 363k votes; Safe
# House, TMDb 6.49 / IMDb 6.7 with 249k votes) — but the reason it is now zero
# rather than merely low is that rating is not a criterion here at all. With the
# gate being IMDb vote count, the fail-open path if OMDb dies still leaves the
# TMDb vote prefilter standing, which is a fame filter too, so dropping this one
# costs no safety.
_DEFAULT_MIN_RATING = 0.0
# Raised from 300: TMDb vote count is the best cheap proxy for IMDb fame, and
# every film measured at 80k+ IMDb votes had at least 1,304 TMDb votes, so 800
# keeps margin while making the pool markedly denser in films that will pass.
# Measured: this prefilter grew the unowned pool 1,513 -> 1,990 and the
# IMDb-passing count 378 -> 497.
_DEFAULT_MIN_VOTES = 800
_DEFAULT_MIN_SEEDERS = 5
# "Semi-older": inside the last 40 years, but not this year's releases, which
# are Latest Auto-Grab's job. Computed rather than hardcoded so the window
# doesn't silently rot into a fixed range.
_DEFAULT_YEARS_BACK = 40
_DEFAULT_YEARS_RECENT_GAP = 3

_last_status: Dict[str, Any] = {
    'running': False,
    'last_run': None,
    'last_result': None,
    'cycle_count': 0,
}
_lock = threading.Lock()
_hydrated = False


# ── Genres ────────────────────────────────────────────────────────────────

# TMDb's movie genre ids. Committed rather than fetched: the list has not
# changed in years, and a network hiccup must not empty the wheel.
#
# "TV Movie" is omitted on purpose — it is a production-origin tag rather than a
# genre, and it selects for exactly the made-for-TV filler this automation
# exists to avoid.
GENRES: Dict[str, int] = {
    'Action': 28, 'Adventure': 12, 'Animation': 16, 'Comedy': 35,
    'Crime': 80, 'Documentary': 99, 'Drama': 18, 'Family': 10751,
    'Fantasy': 14, 'History': 36, 'Horror': 27, 'Music': 10402,
    'Mystery': 9648, 'Romance': 10749, 'Science Fiction': 878,
    'Thriller': 53, 'War': 10752, 'Western': 37,
}

# Trackers worth searching for a 720p/1080p WEB-DL or Bluray. A different
# question from Movie Night's remux trackers: IPTorrents' volume is an asset
# here, where for a 4K remux it is mostly noise.
TRACKERS = ('IPTorrents', 'BeyondHD', 'PrivateHD')
TRACKERS_SETTING = 'automation.genre_roulette.trackers'

# Preference order, best-value first. Searched in order and stopped at the
# first tier that yields a usable release, which is what "prefer IPTorrents"
# means in practice: a premium tracker is only ever contacted for a film the
# cheaper one could not serve, so its share of the night's traffic falls out of
# the ordering instead of needing a quota to hold it down. It also cuts load on
# PrivateHD, which 429-bans under the Explore sweep's volume.
TRACKER_TIERS = (('IPTorrents',), ('PrivateHD',), ('BeyondHD',))

# The tracker whose volume is worth spending deliberately rather than freely.
# Capped on two horizons because they answer different worries: the nightly cap
# stops one bad night eating a month's goodwill, the rolling one bounds the
# year. Both are settings; these are the defaults.
_PREMIUM_TRACKER = 'BeyondHD'
# The premium tier rescues gaps: it is searched for any film the cheaper tiers
# could not serve, not held back for emergencies. That is what it is good for —
# measured, BeyondHD covered 2 of 3 films IPTorrents missed, and PrivateHD none.
#
# Preference alone keeps its share small, because it is only ever reached on an
# IPTorrents miss: at the measured 80% hit rate that is ~13% of grabs, roughly
# 0.4 a night. So the caps below are not the mechanism that keeps volume low —
# the tier ordering is. They exist only to bound a runaway, chiefly a prolonged
# IPTorrents outage that would otherwise route a whole month through BeyondHD.
# Both are settings; these are the defaults.
_DEFAULT_PREMIUM_PER_NIGHT = 3
_DEFAULT_PREMIUM_PER_MONTH = 45
_PREMIUM_WINDOW_DAYS = 30

# A night that came up short gets more attempts rather than waiting 24h. With
# a candidate pool in the dozens a shortfall means transient indexer trouble,
# and it is exactly the case where sleeping until tomorrow turns bad luck into
# a permanently missed night.
_SHORT_RETRY_LIMIT = 3
_SHORT_RETRY_GAP_MINUTES = 20


def eligible_trackers(client) -> List[Dict[str, Any]]:
    """Configured indexers this automation may search. Backs the UI picker."""
    from app import tracker_selection
    return tracker_selection.eligible(client, TRACKERS)


# ── Settings ──────────────────────────────────────────────────────────────

def _int_setting(settings, key: str, default: int,
                 lo: int, hi: int) -> int:
    """Read an int setting, clamped. A stray value falls back to the default.

    Settings written by the UI arrive as strings often enough ("4" for weekday
    in movie_night) that parsing has to be defensive, and a count of 0 or a
    negative would either disable the automation silently or blow the bounds.
    """
    try:
        v = int(settings.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _float_setting(settings, key: str, default: float,
                   lo: float, hi: float) -> float:
    try:
        v = float(settings.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _year_window(settings) -> tuple:
    """(from, to) release years to draw from, always ordered low-high."""
    this_year = date.today().year
    lo = _int_setting(settings, 'automation.genre_roulette.year_from',
                      this_year - _DEFAULT_YEARS_BACK, 1900, 2200)
    hi = _int_setting(settings, 'automation.genre_roulette.year_to',
                      this_year - _DEFAULT_YEARS_RECENT_GAP, 1900, 2200)
    # A window the user has inverted would return nothing from TMDb and look
    # like an empty catalogue, so straighten it instead.
    return (lo, hi) if lo <= hi else (hi, lo)


def selected_genres(settings) -> List[str]:
    """Genre names the wheel may draw from. Empty means all of GENRES."""
    raw = settings.get('automation.genre_roulette.genres', None)
    if isinstance(raw, str):
        raw = raw.split(',')
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(g).strip() for g in raw
            if str(g).strip() in GENRES]


def _profile_id(settings) -> int:
    try:
        return int(settings.get(
            'automation.genre_roulette.quality_profile_id', 0) or 0)
    except (TypeError, ValueError):
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
    return _data_dir() / 'genre_roulette_state.json'


def _log_path() -> Path:
    return _data_dir() / 'genre_roulette_log.json'


def _read_state() -> Dict[str, Any]:
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                data.setdefault('grabbed_tmdb_ids', [])
                # Films searched with no usable release. Not permanent: dated so
                # they come back round, same as Movie Night's parked map.
                data.setdefault('parked', {})
                data.setdefault('last_run_date', None)
                # Only the nightly run writes these — a manual "Run now" must
                # not consume the night's slot. The HH:MM is part of the key so
                # that moving the schedule mid-day gives the new time its own
                # chance today instead of silently doing nothing until tomorrow.
                data.setdefault('last_scheduled_date', None)
                data.setdefault('last_scheduled_time', None)
                data.setdefault('last_run', None)
                data.setdefault('last_result', None)
                data.setdefault('recent_genres', [])
                # Dates of premium-tracker grabs, pruned to the rolling window.
                data.setdefault('premium_grabs', [])
                # Tonight's shortfall, if any, and how many retries it has had.
                data.setdefault('short_night', {})
                # tmdb_id -> {'r','v','at'}: IMDb verdicts, so a film redrawn
                # on a later night costs no OMDb call.
                data.setdefault('imdb_cache', {})
                # tmdb_id -> collection_id (int) or 0 if the film has none.
                data.setdefault('collection_cache', {})
                # {'sig': ..., 'pages': {'<stage>|<genre>': page}}: how far down
                # each genre's ratings the wheel has already worked.
                data.setdefault('cursors', {})
                if not isinstance(data.get('parked'), dict):
                    data['parked'] = {}
                return data
    except Exception as e:
        logger.warning(f'Genre Roulette: failed to read state: {e}')
    return {'grabbed_tmdb_ids': [], 'parked': {}, 'last_run_date': None,
            'last_scheduled_date': None, 'last_scheduled_time': None,
            'last_run': None, 'last_result': None, 'recent_genres': [],
            'premium_grabs': [], 'short_night': {}, 'imdb_cache': {},
            'collection_cache': {}, 'cursors': {}}


def _write_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning(f'Genre Roulette: failed to write state: {e}')


# A film with no usable release now may well have one in a month. Shorter than
# Movie Night's 90 days because the bar here is far easier to clear, so a miss
# is more likely to be transient.
_RECHECK_DAYS = 30


def _active_parked(state: Dict[str, Any]) -> set:
    cutoff = date.today() - timedelta(days=_RECHECK_DAYS)
    out = set()
    for tid, when in (state.get('parked') or {}).items():
        try:
            if date.fromisoformat(str(when)[:10]) >= cutoff:
                out.add(int(tid))
        except (ValueError, TypeError):
            continue
    return out


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
        logger.warning(f'Genre Roulette: failed to write log: {e}')


def _hydrate_status() -> None:
    """Seed in-memory status from disk once, so a restart doesn't erase it."""
    global _hydrated
    with _lock:
        if _hydrated:
            return
        _hydrated = True
        state = _read_state()
        if state.get('last_run'):
            _last_status['last_run'] = state['last_run']
            _last_status['last_result'] = state.get('last_result')


def _scheduled_time(settings) -> str:
    """The configured run time as ``HH:MM``, in the server's clock.

    Stored as two integers rather than a string so a half-typed "2" in the
    time field can't park the schedule at an unparseable value.
    """
    hour = _int_setting(settings, 'automation.genre_roulette.hour',
                        _DEFAULT_HOUR, 0, 23)
    minute = _int_setting(settings, 'automation.genre_roulette.minute',
                          _DEFAULT_MINUTE, 0, 59)
    return f'{hour:02d}:{minute:02d}'


def _next_run_dt() -> Optional[datetime]:
    """When the nightly run is next due, or None if it is disabled.

    Naive local on purpose: the time setting is the server's clock, so an
    aware UTC instant would render in the browser as a different time than
    the one the user typed into the field.
    """
    from app.settings_manager import SettingsManager
    settings = SettingsManager()
    if not settings.get('automation.genre_roulette.enabled', False):
        return None
    at = _scheduled_time(settings)
    hour, minute = (int(p) for p in at.split(':'))
    now = datetime.now()
    state = _read_state()
    done = (state.get('last_scheduled_date') == now.strftime('%Y-%m-%d')
            and state.get('last_scheduled_time') == at)
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if done:
        if _retry_due(state, now):
            # A retry is pending tonight, so tomorrow's slot is not what's next.
            return now.replace(second=0, microsecond=0)
        return slot + timedelta(days=1)
    if now >= slot:
        # Past the time with the slot unspent — the next check picks it up.
        return now.replace(second=0, microsecond=0)
    return slot


def _next_run() -> Optional[str]:
    try:
        nxt = _next_run_dt()
        return nxt.isoformat() if nxt else None
    except Exception as e:
        logger.warning(f'Genre Roulette: could not compute next run: {e}')
        return None


def get_status() -> Dict[str, Any]:
    _hydrate_status()
    with _lock:
        out = dict(_last_status)
    out['next_run'] = _next_run()
    # Named, not implied. A bare "next run 22:30" is unreadable when the
    # container's zone and the viewer's don't agree — which is exactly how a
    # schedule 4h off its intended hour went unnoticed.
    try:
        out['server_tz'] = datetime.now().astimezone().tzname()
    except Exception:
        out['server_tz'] = None
    return out


def get_log() -> List[Dict[str, Any]]:
    """Run history, newest first."""
    try:
        p = _log_path()
        entries = json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []
    out = [e for e in entries if isinstance(e, dict)]
    out.reverse()
    return out


# ── Candidate discovery ───────────────────────────────────────────────────

def _tmdb_key(settings) -> str:
    try:
        from app.config import Config
        return settings.get('apis.tmdb.api_key') or Config.TMDB_API_KEY or ''
    except Exception:
        return settings.get('apis.tmdb.api_key') or ''


def _spin(settings, rng: random.Random) -> List[str]:
    """Draw this run's genres, avoiding the ones the last run used.

    Without the recency exclusion a 3-of-18 draw repeats a genre on ~40% of
    consecutive nights, which reads as the wheel being stuck.
    """
    pool = selected_genres(settings) or list(GENRES)
    count = _int_setting(settings, 'automation.genre_roulette.genres_per_night',
                         _DEFAULT_GENRES_PER_NIGHT, 1, len(GENRES))
    count = min(count, len(pool))

    recent = set(_read_state().get('recent_genres') or [])
    fresh = [g for g in pool if g not in recent]
    # Only honour the exclusion while it leaves enough to draw from — with a
    # narrow pool (or a large count) it otherwise starves the draw entirely.
    if len(fresh) >= count:
        pool = fresh
    return rng.sample(pool, count)


def _discover_params(api_key: str, genre: str, year_from: int, year_to: int,
                     min_rating: float, min_votes: int) -> Dict[str, Any]:
    """The query that defines "worth grabbing" for one genre.

    Every quality gate lives here rather than in the page selection, which is
    what makes drawing from deep pages safe.
    """
    return {
        'api_key': api_key,
        'with_genres': GENRES.get(genre, ''),
        'primary_release_date.gte': f'{year_from}-01-01',
        'primary_release_date.lte': f'{year_to}-12-31',
        'vote_average.gte': min_rating,
        'vote_count.gte': min_votes,
        # English-audio only, matching the rest of the app (see indexer_latest
        # and movie_night). The standard profile accepts the original language, so this is a
        # preference rather than a Radarr constraint.
        'with_original_language': 'en',
        'include_adult': 'false',
        # Feature-length only. Not a content filter of the kind that over-blocks
        # (see the keyword-denylist lesson) — it is the structural feature/short
        # split, taken from TMDb's own runtime, and it exists because the deeper
        # pages the draw now reaches contain shorts that the famous top pages
        # never did: Paperman (7m) and Marvel One-Shots rank well on vote count
        # and would otherwise spend a night's grab slot. Measured cost: 4 of 126
        # unowned candidates excluded, all genuinely shorts.
        'with_runtime.gte': 60,
        # Most-voted first (TMDb vote_count as a cheap fame proxy). Rating is
        # not a selection criterion — page N is "the Nth slice down this
        # genre's fame", and `vote_count.gte` keeps the obscure tail out of
        # the OMDb budget.
        'sort_by': 'vote_count.desc',
    }


def _discover_page(params: Dict[str, Any], genre: str,
                   page: int) -> Optional[tuple]:
    """One page of TMDb discover: (films, total_pages). None means it failed.

    None vs [] matters for the same reason it does in Movie Night: treating a
    TMDb outage as "this genre is empty" makes an outage look like a clean run.
    """
    import requests

    if not params.get('with_genres'):
        return [], 1

    try:
        r = requests.get(f'{TMDB_BASE}/discover/movie',
                         params={**params, 'page': page}, timeout=20)
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        logger.warning(f'Genre Roulette: TMDb discover page {page} '
                       f'for {genre} failed: {e}')
        return None

    total = min(int(body.get('total_pages') or 1), _MAX_DISCOVER_PAGES)
    wanted = GENRES.get(genre)
    try:
        wanted_id = int(wanted) if wanted is not None else None
    except (TypeError, ValueError):
        wanted_id = None
    films = []
    for m in body.get('results') or []:
        tmdb_id = m.get('id')
        rel = (m.get('release_date') or '')[:4]
        if not tmdb_id or not rel.isdigit():
            continue
        # with_genres matches ANY listed genre. Keep only films whose PRIMARY
        # genre (genre_ids[0]) is tonight's spun genre — otherwise a secondary
        # Thriller tag puts Fast & Furious on a Thriller night.
        ids = m.get('genre_ids') or []
        if wanted_id is None or not ids:
            continue
        try:
            primary = int(ids[0])
        except (TypeError, ValueError):
            continue
        if primary != wanted_id:
            continue
        films.append({
            'tmdb_id': int(tmdb_id),
            'title': m.get('title') or m.get('original_title') or '',
            'year': int(rel),
            'rating': float(m.get('vote_average') or 0),
            'votes': int(m.get('vote_count') or 0),
            'poster_path': m.get('poster_path') or '',
            'genre': genre,
            'genre_ids': [int(x) for x in ids if str(x).lstrip('-').isdigit()],
        })
    return films, total


def _new_genre_draw(start_page: int = 1) -> Dict[str, Any]:
    """Per-genre draw state, carried across the opening and top-up rounds.

    `next_page` opens at the saved cursor — the shallowest page of this genre
    that still held a film owed a night when it was last walked. `hold` is what
    the next cursor will become (the shallowest such page seen this run) and
    `last` the deepest page paid for, so a genre walked clean advances past it.
    """
    return {'next_page': max(1, start_page), 'total': None, 'reached': False,
            'failed': False, 'done': False, 'pages': 0,
            'hold': None, 'last': 0}


def _draw_genre(params: Dict[str, Any], genre: str, exclude: set, hard: set,
                target: int, budget: int,
                ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Walk this genre's pages, best-rated first, until `target` unowned turn up.

    Adaptive rather than a fixed page count because ownership is not uniform
    across a genre's depth — the pages a deep library owns outright are exactly
    the acclaimed ones at the top, so a fixed shallow read reliably returns a
    pool of nearly zero and the night's target goes unmet for want of
    candidates, not releases. Walking in order from the cursor means that cost
    is paid once per page rather than every night.

    `ctx` persists the position and the `total_pages` bound, so a top-up round
    never re-reads a page and never has to re-probe.
    """
    films: List[Dict[str, Any]] = []
    fresh = 0
    pages = 0

    while not ctx['done'] and pages < budget and fresh < target:
        page = ctx['next_page']
        if page > (ctx['total'] or _MAX_DISCOVER_PAGES):
            # Walked clean to the end of the genre: every page above the cursor
            # is owned or parked. Rewind so the cursor is not stranded past the
            # catalogue, and let the widening stages carry the night from here.
            ctx['next_page'] = 1
            ctx['done'] = True
            break

        got = _discover_page(params, genre, page)
        pages += 1
        if got is None:
            # Only a total failure disqualifies the genre; a page that dies
            # after others succeeded just ends this round early. `next_page` is
            # left alone so the page is retried rather than skipped.
            if not ctx['reached']:
                ctx['failed'] = True
            break

        page_films, total = got
        ctx['total'] = total
        ctx['reached'] = True
        ctx['next_page'] = page + 1
        ctx['last'] = max(ctx['last'], page)
        for f in page_films:
            films.append(f)
            if f['tmdb_id'] not in exclude:
                fresh += 1
            # A film neither owned nor already grabbed is still owed a night,
            # even when tonight passes it over — parked for now, or simply
            # surplus to a pool that only needed the first few. The cursor may
            # not advance past the page holding it, or the best-rated films in
            # the genre would be skipped for being one slot too far down.
            if ctx['hold'] is None and f['tmdb_id'] not in hard:
                ctx['hold'] = page

    ctx['pages'] += pages
    return films


def _draw_stage(params_for, s_genres: List[str], walk: Dict[str, Any],
                target: int, pool: Dict[int, Dict[str, Any]],
                stage: str) -> tuple:
    """One widening stage: an opening round per genre, then top-ups.

    Mutates `pool` so every stage's finds accumulate — a film surfaced by the
    configured query is not thrown away because a later stage also ran.
    Returns (genres_reached, genres_failed).
    """
    excluded = walk['excluded']

    def absorb(found: List[Dict[str, Any]]) -> None:
        for f in found:
            # A film in two of tonight's genres is one candidate, credited to
            # the first wheel that produced it.
            pool.setdefault(f['tmdb_id'], f)

    def fresh_total() -> int:
        return len([t for t in pool if t not in excluded])

    def draw(genre: str, ctx: Dict[str, Any], want: int,
             budget: int) -> List[Dict[str, Any]]:
        return _draw_genre(params_for(genre), genre, excluded, walk['hard'],
                           want, budget, ctx)

    share = max(1, -(-target // max(1, len(s_genres))))
    ctxs: Dict[str, Dict[str, Any]] = {}
    for genre in s_genres:
        # Checked before each genre so a wide stage listing all 18 stops as
        # soon as it is full rather than reading every genre it was handed.
        if fresh_total() >= target:
            break
        key = (stage, genre)
        ctx = walk['ctxs'].get(key)
        if ctx is None:
            # Each stage walks its own query, so each keeps its own cursor:
            # page 7 of "all years" is not page 7 of the configured window.
            ctx = _new_genre_draw(_cursor(walk['cursors'], stage, genre))
            walk['ctxs'][key] = ctx
        ctxs[genre] = ctx
        absorb(draw(genre, ctx, share, _MAX_DISCOVER_PAGES_PER_GENRE))

    # Genres differ wildly in how deep their owned region runs (Drama measured
    # ~90% owned well past the depth where Adventure had turned over), so an
    # even per-genre share can come up short while the catalogue holds plenty.
    for _ in range(_MAX_TOPUP_ROUNDS):
        if fresh_total() >= target:
            break
        progressed = False
        for genre, ctx in ctxs.items():
            if fresh_total() >= target:
                break
            if ctx['failed'] or ctx['done'] or not ctx['reached']:
                continue
            before = len(pool)
            absorb(draw(genre, ctx, target, _TOPUP_PAGES))
            progressed = progressed or len(pool) > before
        if not progressed:
            break

    return (len([c for c in ctxs.values() if c['reached']]),
            [g for g, c in ctxs.items() if c['failed']])


def _stage_genres(stage: Dict[str, Any], genres: List[str], settings,
                  rng: random.Random) -> List[str]:
    """Tonight's genres, plus borrowed ones only if the stage allows it."""
    scope = stage.get('extra_genres')
    if not scope:
        return genres
    enabled = [g for g in (selected_genres(settings) or list(GENRES))
               if g not in genres]
    rng.shuffle(enabled)
    out = genres + enabled
    if scope == 'all':
        rest = [g for g in GENRES if g not in out]
        rng.shuffle(rest)
        out = out + rest
    return out


def _prefilter_votes(configured: int, imdb_min_votes: int) -> int:
    """TMDb vote floor to use, which may only ever be *looser* than configured.

    The TMDb vote count is a cheap proxy for IMDb fame, and the right proxy
    depends on the IMDb floor being asked for: measured, films at 80k IMDb votes
    all had >=1,304 TMDb votes, so a TMDb floor of 800 discards none of them (0
    of 60 in the discarded 300-799 band). Drop the IMDb floor to 25k, though,
    and that same TMDb 800 silently costs ~414 eligible films.

    So the prefilter is derived rather than fixed. It exists only to save OMDb
    lookups and must never be the thing that bounds the pool — hence min(), and
    hence a floor of 100 rather than 0.
    """
    if imdb_min_votes <= 0:
        return configured
    derived = max(_PREFILTER_VOTES_MIN, imdb_min_votes // _PREFILTER_VOTES_RATIO)
    return min(configured, derived) if configured else derived


# ── Rating cursors ────────────────────────────────────────────────────────

def _cursor_sig(year_from: int, year_to: int, min_rating: float,
                min_votes: int) -> str:
    """Identity of the query the saved cursors were walked against.

    "Page 7 of Westerns" only means something alongside the window and floors
    that produced the ordering, so a settings change has to retire the cursors
    rather than silently resume into a different genre's worth of films.
    """
    return f'{year_from}-{year_to}|{min_rating:g}|{min_votes}'


def _read_cursors(state: Dict[str, Any], sig: str) -> Dict[str, int]:
    """Saved resume pages, or nothing if they describe a different query."""
    saved = state.get('cursors')
    if not isinstance(saved, dict) or saved.get('sig') != sig:
        return {}
    pages = saved.get('pages')
    return dict(pages) if isinstance(pages, dict) else {}


def _cursor(cursors: Dict[str, int], stage: str, genre: str) -> int:
    try:
        return max(1, int(cursors.get(f'{stage}|{genre}', 1)))
    except (TypeError, ValueError):
        return 1


def _next_cursors(walk: Dict[str, Any]) -> Dict[str, Any]:
    """Where each genre walked this run should resume next time.

    Lags reality by one page in one case: a film grabbed *tonight* was not in
    the ownership set the draw ran against, so it still holds the cursor at its
    page. That costs one re-read on the next night that draws the genre, at
    which point it is in `grabbed_tmdb_ids` and the cursor moves on.
    """
    pages = dict(walk['cursors'])
    for (stage, genre), ctx in walk['ctxs'].items():
        if not ctx['reached']:
            continue
        if ctx['done']:
            page = 1
        elif ctx['hold'] is not None:
            page = ctx['hold']
        else:
            page = ctx['last'] + 1
        pages[f'{stage}|{genre}'] = max(1, page)
    return {'sig': walk['sig'], 'pages': pages}


def _best_first(films: List[Dict[str, Any]], genres: List[str],
                rng: random.Random) -> List[Dict[str, Any]]:
    """Candidates in the order the night should try them (pre-IMDb).

    Most TMDb-voted first (fame proxy), taken in turn from each of tonight's
    genres. Round-robin rather than one flat sort so one genre cannot monopolise
    every slot. run_cycle re-ranks passers by real IMDb votes before grabbing.

    Genres borrowed by a widening stage go last: widening is what a drained
    configured pool falls back to, not a peer of tonight's draw.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for f in films:
        groups.setdefault(f.get('genre') or '', []).append(f)
    for group in groups.values():
        # Fame only — TMDb rating is not a criterion.
        group.sort(key=lambda f: -(f.get('votes') or 0))

    tonight = [g for g in genres if g in groups]
    borrowed = [g for g in groups if g not in tonight]
    # Shuffled so one genre does not hold first refusal every night it is
    # drawn; inside a genre the order is fame, never chance.
    rng.shuffle(tonight)
    rng.shuffle(borrowed)

    out: List[Dict[str, Any]] = []
    for tier in (tonight, borrowed):
        lists = [groups[g] for g in tier]
        for rank in range(max((len(l) for l in lists), default=0)):
            out.extend(l[rank] for l in lists if rank < len(l))
    return out


def candidates(settings, rng: Optional[random.Random] = None) -> Dict[str, Any]:
    """Unowned films from this run's genres, shuffled.

    Ownership comes from the same READ-ONLY library DB Movie Night uses. If it
    reads empty every film looks missing, so refuse rather than start grabbing
    the catalogue.
    """
    from app import canon_index

    rng = rng or random.Random()
    api_key = _tmdb_key(settings)
    if not api_key:
        return {'error': 'TMDb API key not configured', 'films': []}

    owned = canon_index.owned_tmdb_ids()
    if not owned:
        return {'error': 'Ownership data unavailable (upgrade_library.db empty '
                         'or unsynced) — refusing to run',
                'films': []}

    state = _read_state()
    grabbed = set(state.get('grabbed_tmdb_ids') or [])
    parked = _active_parked(state)

    genres = _spin(settings, rng)
    year_from, year_to = _year_window(settings)
    # TMDb rating is not a criterion — always 0 so discover never filters on
    # vote_average. Legacy min_rating settings are ignored on purpose.
    min_rating = 0.0
    min_votes = _int_setting(settings, 'automation.genre_roulette.min_votes',
                             _DEFAULT_MIN_VOTES, 0, 1_000_000)
    # The IMDb floor is the real gate, so the cheap TMDb prefilter is relaxed
    # to match it rather than being left to bound the pool on its own.
    min_votes = _prefilter_votes(
        min_votes,
        _int_setting(settings, 'automation.genre_roulette.imdb_min_votes',
                     _DEFAULT_IMDB_MIN_VOTES, 0, 10_000_000))

    want = _int_setting(settings,
                        'automation.genre_roulette.movies_per_night',
                        _DEFAULT_MOVIES_PER_NIGHT, 1, 20)
    # Ownership is the whole difficulty here, so the three exclusion sets are
    # unioned once and handed to the draw, which uses them to decide when it
    # has dug deep enough rather than filtering only after the fact.
    excluded = set(owned) | grabbed | parked
    target = max(want * _FRESH_PER_WANTED, _MIN_FRESH_POOL)
    # High IMDb fame bars reject most unowned Discover hits (measured ~5% at
    # 250k). Enlarge the pool so collection skips / indexer misses still leave
    # alternates inside the night rather than exiting at 0 of 3.
    imdb_floor_for_pool = _int_setting(
        settings, 'automation.genre_roulette.imdb_min_votes',
        _DEFAULT_IMDB_MIN_VOTES, 0, 10_000_000)
    if imdb_floor_for_pool >= 200_000:
        target = max(target, want * 40)
    elif imdb_floor_for_pool >= 120_000:
        target = max(target, want * 25)
    elif imdb_floor_for_pool >= 80_000:
        target = max(target, want * 15)

    # The cursor may only advance past films that are gone for good. Parked
    # films are not — they come back in 30 days — so they are excluded from
    # tonight's pool but still pin the cursor to their page.
    sig = _cursor_sig(year_from, year_to, min_rating, min_votes)
    walk: Dict[str, Any] = {
        'excluded': excluded,
        'hard': set(owned) | grabbed,
        'cursors': _read_cursors(state, sig),
        'ctxs': {},
        'sig': sig,
    }

    pool: Dict[int, Dict[str, Any]] = {}
    failed: List[str] = []
    reached = 0
    stages_run: List[str] = []
    widened = ''

    def fresh_total() -> int:
        return len([t for t in pool if t not in excluded])

    for stage in _WIDENING_STAGES:
        if fresh_total() >= target:
            break
        s_genres = _stage_genres(stage, genres, settings, rng)
        s_from, s_to = ((_WIDE_YEAR_FROM, datetime.now().year)
                        if stage.get('years') else (year_from, year_to))
        # Never tighten past what the user asked for — a stage may only relax.
        s_votes = min(min_votes, stage.get('min_votes', min_votes))
        s_rating = min(min_rating, stage.get('min_rating', min_rating))

        def params_for(genre, _f=s_from, _t=s_to, _r=s_rating, _v=s_votes):
            return _discover_params(api_key, genre, _f, _t, _r, _v)

        got, bad = _draw_stage(params_for, s_genres, walk, target, pool,
                               stage['name'])
        stages_run.append(stage['name'])
        reached = max(reached, got)
        if stage is _WIDENING_STAGES[0]:
            # Only the configured stage's failures describe tonight's wheels;
            # a wide stage skipping genres it never needed is not a failure.
            failed = bad
        if got and stage is not _WIDENING_STAGES[0]:
            widened = stage['name']

    if not reached:
        return {'error': 'TMDb discover failed for every genre ('
                         + ', '.join(failed or genres) + ')',
                'films': [], 'genres': genres}

    fresh = [f for f in pool.values() if f['tmdb_id'] not in excluded]
    # Most-voted first (TMDb fame proxy), per genre. The wheel supplies variety;
    # run_cycle then re-ranks by IMDb votes among films that clear the floor.
    fresh = _best_first(fresh, genres, rng)

    pages_read = sum(c['pages'] for c in walk['ctxs'].values())
    if widened:
        logger.info(
            'Genre Roulette: configured pool came up short — widened to "%s" '
            'for %d unowned candidates (%d considered, %d pages)',
            widened, len(fresh), len(pool), pages_read)
    if len(fresh) < target:
        logger.warning(
            'Genre Roulette: pool still short after every widening stage — '
            '%d unowned from %d considered (wanted %d). The catalogue inside '
            'the widest query is close to fully owned.',
            len(fresh), len(pool), target)

    return {
        'films': fresh,
        'genres': genres,
        'genres_failed': failed,
        # Persisted by the caller, and only on a real run — a preview must not
        # spend the wheel's position.
        'cursors': _next_cursors(walk),
        'considered': len(pool),
        'already_owned': len([tid for tid in pool if tid in owned]),
        'pages_read': pages_read,
        'pool_target': target,
        'widened': widened,
        'stages_run': stages_run,
        'year_from': year_from,
        'year_to': year_to,
    }


# ── Release selection ─────────────────────────────────────────────────────

def _imdb_lookup(tmdb_id: int, tmdb_key: str,
                 omdb_key: str) -> Optional[Dict[str, Any]]:
    """IMDb rating and votes for one film, via TMDb's imdb_id then OMDb.

    Resolved by id rather than OMDb's title search because a title query
    silently misses films whose OMDb title differs — "Twitches Too" and
    "Rugrats in Paris: The Movie" both come back "Movie not found!" — and such a
    miss is indistinguishable from a film that genuinely has no votes. By id,
    all 60 of a 60-film sample resolved.

    None means "could not ask", which the caller treats as no verdict. A dict
    of nulls means "asked, and IMDb has nothing", which is itself a verdict.
    """
    import requests

    today = datetime.now().strftime('%Y-%m-%d')
    try:
        r = requests.get(f'https://api.themoviedb.org/3/movie/{tmdb_id}',
                         params={'api_key': tmdb_key}, timeout=15)
        r.raise_for_status()
        imdb_id = ((r.json() or {}).get('imdb_id') or '').strip()
    except Exception as e:
        logger.warning(f'Genre Roulette: TMDb id lookup for {tmdb_id}: {e}')
        return None

    if not imdb_id:
        return {'r': None, 'v': None, 'at': today}

    try:
        o = requests.get('https://www.omdbapi.com/',
                         params={'apikey': omdb_key, 'i': imdb_id}, timeout=15)
        o.raise_for_status()
        body = o.json() or {}
    except Exception as e:
        logger.warning(f'Genre Roulette: OMDb lookup for {imdb_id}: {e}')
        return None

    if body.get('Response') != 'True':
        return {'r': None, 'v': None, 'at': today}

    votes = (body.get('imdbVotes') or '').replace(',', '')
    rating = (body.get('imdbRating') or '').strip()
    return {
        'r': float(rating) if rating not in ('', 'N/A') else None,
        'v': int(votes) if votes.isdigit() else None,
        'at': today,
    }


def _imdb_fresh(hit: Any) -> bool:
    """Whether a cached IMDb verdict is recent enough to reuse."""
    if not isinstance(hit, dict) or 'at' not in hit:
        return False
    cutoff = (datetime.now()
              - timedelta(days=_IMDB_CACHE_DAYS)).strftime('%Y-%m-%d')
    return str(hit.get('at') or '') >= cutoff


def _prune_imdb_cache(cache: Dict[str, Any]) -> Dict[str, Any]:
    """Drop stale verdicts and cap the cache."""
    fresh = {k: v for k, v in cache.items() if _imdb_fresh(v)}
    if len(fresh) <= _IMDB_CACHE_MAX:
        return fresh
    newest = sorted(fresh.items(), key=lambda kv: str(kv[1].get('at') or ''),
                    reverse=True)[:_IMDB_CACHE_MAX]
    return dict(newest)


def _prune_collection_cache(cache: Dict[str, Any]) -> Dict[str, Any]:
    """Cap the TMDb collection-id cache. Values are ints (0 = no collection)."""
    if not isinstance(cache, dict):
        return {}
    clean = {}
    for k, v in cache.items():
        try:
            clean[str(int(k))] = int(v)
        except (TypeError, ValueError):
            continue
    if len(clean) <= _COLLECTION_CACHE_MAX:
        return clean
    # Prefer keeping denser (non-zero) entries; drop arbitrary extras by key.
    nonzero = {k: v for k, v in clean.items() if v}
    zero = {k: v for k, v in clean.items() if not v}
    keep = dict(list(nonzero.items())[:_COLLECTION_CACHE_MAX])
    room = _COLLECTION_CACHE_MAX - len(keep)
    if room > 0:
        keep.update(dict(list(zero.items())[:room]))
    return keep


def _collection_id_for(tmdb_id: int, tmdb_key: str,
                       cache: Dict[str, Any]) -> Optional[int]:
    """TMDb belongs_to_collection.id for one film, or None if none/unknown.

    Cache stores 0 for "no collection" so we do not re-hit TMDb every night
    for standalones. A failed lookup leaves the cache untouched and returns
    None (caller treats that as "do not block").
    """
    import requests

    key = str(int(tmdb_id))
    if key in cache:
        try:
            cid = int(cache[key])
        except (TypeError, ValueError):
            cid = 0
        return cid or None
    if not tmdb_key:
        return None
    try:
        r = requests.get(f'{TMDB_BASE}/movie/{tmdb_id}',
                         params={'api_key': tmdb_key}, timeout=15)
        r.raise_for_status()
        body = r.json() or {}
    except Exception as e:
        logger.warning('Genre Roulette: TMDb collection lookup for %s: %s',
                       tmdb_id, e)
        return None
    col = body.get('belongs_to_collection') or None
    cid = 0
    if isinstance(col, dict) and col.get('id') is not None:
        try:
            cid = int(col['id'])
        except (TypeError, ValueError):
            cid = 0
    cache[key] = cid
    return cid or None


def _collection_part_ids(collection_id: int, tmdb_key: str,
                         parts_cache: Dict[int, List[int]]) -> List[int]:
    """All movie ids in a TMDb collection (cached in-memory for this run)."""
    import requests

    cid = int(collection_id)
    if cid in parts_cache:
        return parts_cache[cid]
    if not tmdb_key:
        return []
    try:
        r = requests.get(f'{TMDB_BASE}/collection/{cid}',
                         params={'api_key': tmdb_key}, timeout=15)
        r.raise_for_status()
        parts = (r.json() or {}).get('parts') or []
    except Exception as e:
        logger.warning('Genre Roulette: TMDb collection/%s parts: %s', cid, e)
        parts_cache[cid] = []
        return []
    ids: List[int] = []
    for p in parts:
        try:
            ids.append(int(p['id']))
        except (TypeError, ValueError, KeyError):
            continue
    if len(parts_cache) >= _COLLECTION_PARTS_CACHE_MAX:
        # Drop an arbitrary entry to bound memory on a long widen night.
        parts_cache.pop(next(iter(parts_cache)))
    parts_cache[cid] = ids
    return ids


def _recent_grab_tmdb_ids(state: Dict[str, Any]) -> set:
    """tmdb ids that should block other films in the same collection.

    Union of persisted grabs and successfully grabbed last_result picks
    (covers the prior night / last scheduled run).
    """
    out = set()
    for tid in (state.get('grabbed_tmdb_ids') or []):
        try:
            out.add(int(tid))
        except (TypeError, ValueError):
            continue
    lr = state.get('last_result')
    if isinstance(lr, dict):
        for p in (lr.get('picks') or []):
            if not isinstance(p, dict) or not p.get('grabbed'):
                continue
            try:
                out.add(int(p['tmdb_id']))
            except (TypeError, ValueError, KeyError):
                continue
    return out


def _tier_plan(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`plan` split into preference tiers, keeping only what the user selected.

    A tracker the user selected that no tier names is appended as a final tier,
    so widening TRACKERS is enough to include it — forgetting to tier something
    should cost it preference, never exclude it from the search entirely.
    """
    allowed = {n.strip().lower() for n in plan['names']}
    by_name: Dict[str, int] = {}
    if plan['ids'] and len(plan['ids']) == len(plan['names']):
        by_name = {n.strip().lower(): i
                   for n, i in zip(plan['names'], plan['ids'])}

    def tier_for(names: List[str]) -> Dict[str, Any]:
        return {'name': names[0], 'names': names,
                'ids': [by_name[n.strip().lower()] for n in names
                        if n.strip().lower() in by_name]}

    out = [tier_for([n for n in tier if n.strip().lower() in allowed])
           for tier in TRACKER_TIERS
           if [n for n in tier if n.strip().lower() in allowed]]

    tiered = {n.strip().lower() for t in TRACKER_TIERS for n in t}
    extra = [n for n in plan['names'] if n.strip().lower() not in tiered]
    if extra:
        out.append(tier_for(extra))
    return out


def _search_tiers(client, film: Dict[str, Any], tiers: List[Dict[str, Any]],
                  min_seeders: int, skip: tuple = ()) -> tuple:
    """First tier with a usable release wins. (found, ranked, tier_name).

    `found` None means every tier it was allowed to search errored — a real
    outage, which must stay distinguishable from searching fine and finding
    nothing usable.
    """
    merged: Dict[str, Any] = {'ok': True, 'usable': [], 'raw': 0, 'rejects': {}}
    any_ok = False

    for tier in tiers:
        if tier['name'] in skip:
            continue
        found = _search_releases(client, film, tier['ids'], tier['names'])
        if not found['ok']:
            continue
        any_ok = True
        merged['raw'] += found['raw']
        for reason, n in (found['rejects'] or {}).items():
            merged['rejects'][reason] = merged['rejects'].get(reason, 0) + n
        merged['usable'] = found['usable']
        ranked = _ranked(found['usable'], min_seeders)
        if ranked:
            return merged, ranked, tier['name']
        # Only pause when about to ask another tracker for the same film.
        time.sleep(_SEARCH_PAUSE)

    return (merged if any_ok else None), [], ''


def _premium_used(state: Dict[str, Any]) -> tuple:
    """(grabs tonight, grabs in the rolling window) from the premium tracker."""
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    cutoff = (now - timedelta(days=_PREMIUM_WINDOW_DAYS)).strftime('%Y-%m-%d')
    rows = [d for d in (state.get('premium_grabs') or []) if str(d) >= cutoff]
    return len([d for d in rows if d == today]), len(rows)


def _retry_due(state: Dict[str, Any], now: datetime) -> bool:
    """Whether tonight's shortfall has earned another attempt."""
    short = state.get('short_night') or {}
    if short.get('date') != now.strftime('%Y-%m-%d'):
        return False
    if int(short.get('retries') or 0) >= _SHORT_RETRY_LIMIT:
        return False
    last = state.get('last_run')
    if last:
        try:
            prev = datetime.fromisoformat(str(last))
            if prev.tzinfo is not None:
                # last_run is stored aware-UTC; the schedule is local and naive.
                prev = prev.astimezone().replace(tzinfo=None)
            if now - prev < timedelta(minutes=_SHORT_RETRY_GAP_MINUTES):
                return False
        except Exception:
            pass
    return True


def _search_releases(client, film: Dict[str, Any],
                     indexer_ids: Optional[List[int]],
                     tracker_names: Optional[List[str]]) -> Dict[str, Any]:
    """standard-eligible, English-audio releases for one film, with reject counts.

    `ok` False means the search itself failed, which is not the same as
    searching fine and finding nothing usable — collapsing the two is what lets
    an indexer outage look like a clean empty night.

    The reject tally is the point of the return shape: the filters here discard
    most of what an indexer returns (a popular film is mostly 2160p and x265),
    so "found 93, kept 0" and "found 0" need to be distinguishable in the log.
    """
    from app.latest_auto_grab import _matches_standard
    from app.movie_night import _is_english_audio
    from app.upgrade_finder import _normalize_title

    query = f"{film['title']} {film['year']}" if film.get('year') else film['title']
    try:
        raw = client._search_single(query, '2000', 100, indexer_ids or None)
        parsed = client._parse_items(raw) if hasattr(client, '_parse_items') else raw
    except Exception as e:
        logger.warning(f'Genre Roulette: search failed for {query}: {e}')
        return {'ok': False, 'usable': [], 'raw': 0, 'rejects': {}}

    allowed = {n.strip().lower() for n in (tracker_names or [])}
    want = _normalize_title(film['title'])
    usable: List[Dict[str, Any]] = []
    rejects: Dict[str, int] = {}

    def reject(reason: str) -> None:
        rejects[reason] = rejects.get(reason, 0) + 1

    for item in parsed:
        if allowed and (item.get('tracker') or '').strip().lower() not in allowed:
            reject('other tracker')
            continue
        got = _normalize_title(item.get('title', ''))
        # Loose containment both ways — indexer titles carry edition/region
        # junk and TMDb titles are sometimes shorter.
        if want and got and want not in got and got not in want:
            reject('different film')
            continue
        if not _is_english_audio(item):
            reject('non-English audio')
            continue
        if not _is_single_film(item):
            reject('multi-film pack')
            continue
        ok, reason = _matches_standard(item)
        if not ok:
            reject(reason)
            continue
        usable.append(item)

    return {'ok': True, 'usable': usable, 'raw': len(parsed), 'rejects': rejects}


def _score(r: Dict[str, Any]) -> tuple:
    """Rank standard-eligible releases: 1080p first, then seeders.

    Seeders carry real weight here, unlike Movie Night. Everything that reaches
    this point already satisfies the same resolution/source rules, so there is
    no fidelity difference left to weigh — only how fast it will actually
    download.
    """
    is_1080 = (r.get('resolution') or '').lower() == '1080p'
    return (1 if is_1080 else 0, r.get('seeders') or 0)


# Multi-film torrents. The title match is loose containment, so a pack named
# after one of its films sails through it — "Legally Blonde Duology" matches
# "Legally Blonde". Grabbing one means a 22GB download for a film we asked for
# plus sequels we didn't, filed under the wrong movie in Radarr.
#
# Word choice matters more than coverage here: a false positive silently drops a
# legitimate release. "Complete" is left out because "A Complete Unknown" is a
# film, and "Collection" is excused after "Criterion", which labels single-film
# releases (and is a *positive* signal in movie_night's group list).
_PACK_RE = re.compile(
    r'(?<![a-z])('
    r'duology|trilogy|quadrilogy|tetralogy|pentalogy|hexalogy|anthology|'
    r'(?<!criterion.)collection|'
    r'movie\s*pack|moviepack|film\s*pack|filmpack|boxset|box\s*set|'
    r'complete\s*(?:series|collection|set)|'
    r'\d\s*movies?\s*pack'
    r')(?![a-z])', re.IGNORECASE)


def _is_single_film(release: Dict[str, Any]) -> bool:
    """False when the release name says it contains more than one film."""
    return not _PACK_RE.search(f"{release.get('torrent_name') or ''}")


def _ranked(releases: List[Dict[str, Any]], min_seeders: int) -> List[Dict[str, Any]]:
    """Usable releases, best first.

    A list rather than a single winner because Radarr can refuse the top pick
    for reasons only it knows — chiefly the quality profile's size cap, which
    depends on the film's runtime and so isn't checkable from the release alone.
    The caller walks down the list instead of writing the film off.
    """
    pool = [r for r in releases if (r.get('seeders') or 0) >= min_seeders]
    return sorted(pool, key=_score, reverse=True)


# Radarr reports its profile size cap only by rejecting a push, and the cap is
# per-film (MB per minute × runtime), so it can't be predicted from the release.
# Parsing it out of the rejection lets the remaining releases for that film be
# filtered for free, instead of pushing each one to be told the same thing.
_SIZE_CAP_RE = re.compile(
    r'larger than maximum allowed\s+([\d.]+)\s*(GB|MB)', re.IGNORECASE)


def _parse_size_cap(detail: str) -> Optional[float]:
    """GB cap from a Radarr size rejection, or None if it wasn't one."""
    m = _SIZE_CAP_RE.search(detail or '')
    if not m:
        return None
    try:
        value = float(m.group(1))
    except (TypeError, ValueError):
        return None
    return value / 1024 if m.group(2).upper() == 'MB' else value


# ── Grab ──────────────────────────────────────────────────────────────────

def _undo_failed_grab(radarr, movie: Dict[str, Any], created: bool,
                      prior, film: Dict[str, Any], reason: str) -> None:
    """Leave no monitored-empty Radarr entry after a refused push.

    Same contract as Movie Night / latest_auto_grab: a grab either lands a
    release or restores Radarr to how it looked before this attempt. Leaving
    the film monitored on the standard profile after a refusal is a standing order for RSS
    to mass-download later — which is how ghosts like Far from Heaven /
    Legally Blonde accumulated.

    If a download already started (queue lag / history grab), do NOT delete
    the movie — that orphaned the torrent in qBit and the next release retry
    created a second copy. Clear any queue item with removeFromClient on a
    true undo so refused pushes do not leave client orphans either.
    """
    try:
        mid = (movie or {}).get('id')
        if mid and getattr(radarr, 'movie_download_active', None):
            try:
                if radarr.movie_download_active(mid):
                    logger.warning(
                        'Genre Roulette: skip undo for %s — download/history '
                        'already active (%s); leaving Radarr entry',
                        film.get('title'), reason)
                    return
            except Exception as e:
                logger.warning(
                    'Genre Roulette: download-active check failed for %s: %s',
                    film.get('title'), e)

        # On a true refusal, drop any parked/queued item and remove it from
        # the download client so a half-accepted push cannot linger as an
        # orphan while we restore/delete the Radarr movie.
        if mid and hasattr(radarr, 'remove_from_queue'):
            try:
                q = radarr._queue_record(mid) if hasattr(radarr, '_queue_record') else None
                if q and q.get('id'):
                    radarr.remove_from_queue(
                        q['id'], remove_from_client=True, blocklist=False)
                    logger.info(
                        'Genre Roulette: cleared queue item %s for %s on undo '
                        '(removeFromClient=true)', q['id'], film.get('title'))
            except Exception as e:
                logger.warning(
                    'Genre Roulette: queue clear on undo failed for %s: %s',
                    film.get('title'), e)

        if created:
            if movie and movie.get('id') and radarr.delete_movie(movie['id'], delete_files=False):
                logger.info(
                    'Genre Roulette: removed %s from Radarr after refusal (%s) '
                    '— nothing left monitored', film.get('title'), reason)
            else:
                logger.error(
                    'Genre Roulette: could not remove %s after refused grab — '
                    'it may still be monitored empty', film.get('title'))
            return
        if prior is None or not movie:
            return
        was_monitored, was_profile = prior
        movie['monitored'] = was_monitored
        if was_profile is not None:
            movie['qualityProfileId'] = was_profile
        if radarr.update_movie(movie):
            logger.info(
                'Genre Roulette: restored %s to monitored=%s profile=%s after '
                'refusal (%s)', film.get('title'), was_monitored, was_profile,
                reason)
        else:
            logger.error(
                'Genre Roulette: could not restore %s after refused grab',
                film.get('title'))
    except Exception as e:
        logger.error(
            'Genre Roulette: rollback after refused grab failed for %s: %s',
            film.get('title'), e, exc_info=True)


def _note_origin(film: Dict[str, Any], release: Dict[str, Any]) -> None:
    """Tell the Activity feed this download came from Genre Roulette."""
    try:
        from app import grab_origin
        grab_origin.record('genre_roulette', info_hash=release.get('info_hash') or '',
                           download_url=release.get('download_url') or '',
                           tmdb_id=film.get('tmdb_id'), media_type='movie',
                           title=film.get('title') or '')
    except Exception as e:  # noqa: BLE001
        logger.debug('Genre Roulette: origin record failed: %s', e)


def _grab(film: Dict[str, Any], release: Dict[str, Any], radarr,
          profile_id: int, root_folder: str) -> tuple:
    """Add to Radarr on the configured profile and push the chosen release.

    On refusal, undo whatever this attempt changed so a failed search never
    leaves a monitored-but-empty movie behind.
    """
    tmdb_id = int(film['tmdb_id'])
    created = False
    prior = None
    existing = None
    try:
        existing = radarr.get_movie_by_tmdb_id(tmdb_id)
        if not existing:
            existing = radarr.add_movie(
                tmdb_id=tmdb_id,
                title=film['title'],
                year=int(film.get('year') or datetime.now().year),
                quality_profile_id=profile_id,
                root_folder_path=root_folder,
                monitored=True,
                search_for_movie=False,
            )
            if not existing:
                return False, 'Radarr add returned None'
            created = True
        else:
            # The film may already exist under another automation's profile (the
            # 4K auto-grab sets a 2160p-only one). Force this profile so Radarr
            # will accept a 720p/1080p push.
            needs = False
            prior = (bool(existing.get('monitored')), existing.get('qualityProfileId'))
            if not existing.get('monitored'):
                existing['monitored'] = True
                needs = True
            if existing.get('qualityProfileId') != profile_id:
                existing['qualityProfileId'] = profile_id
                needs = True
            if not needs:
                prior = None  # nothing changed — nothing to restore
            if needs:
                updated = radarr.update_movie(existing)
                if updated:
                    existing = updated

        payload = {
            'title': release.get('torrent_name') or film['title'],
            'downloadUrl': release.get('download_url') or '',
            'protocol': 'torrent',
            'publishDate': release.get('pub_date') or datetime.now(timezone.utc).isoformat(),
            'indexer': release.get('tracker') or PUSH_INDEXER_FALLBACK,
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

        # Verified, not bare push_release: an approved push is not yet a
        # download. Reporting "grabbed 3" for three films that never left
        # Radarr's pending queue is how this went unnoticed for a whole night.
        # A parked/rejected push rolls back the add (or restores prior state)
        # so Genre Roulette never leaves monitored ghosts.
        push = radarr.push_release_verified(payload, (existing or {}).get('id'))
        mid = (existing or {}).get('id')
        # Even if verification timed out, a history/queue grab means the
        # client already has the torrent — treat as success, never undo+retry.
        if (not push.get('ok') and mid
                and getattr(radarr, 'movie_download_active', None)
                and radarr.movie_download_active(mid)):
            logger.info(
                'Genre Roulette: %s verification said fail (%s) but download/'
                'history already active — counting as grabbed',
                film.get('title'),
                ('; '.join(push.get('rejections') or [])
                 or push.get('error') or 'rejected'),
            )
            push = {'ok': True, 'data': push.get('data')}

        if push.get('ok'):
            # Roulette must not leave a standing monitored order. The download
            # is already in Radarr's queue; keeping monitored=True only invites
            # RSS retries that show up as "Watchlist" on Luma. Preserve
            # monitored when the user already had this title on their
            # intentional watchlist (existed + monitored before we touched it).
            keep_monitored = (
                (not created and prior is None)  # already monitored+right profile
                or (prior is not None and bool(prior[0]))
            )
            if (not keep_monitored and existing and existing.get('monitored')
                    and existing.get('id')):
                try:
                    if radarr.unmonitor_movie(existing['id']):
                        existing['monitored'] = False
                        logger.info(
                            'Genre Roulette: unmonitored %s after grab — '
                            'download continues without Watchlist pollution',
                            film.get('title'))
                    else:
                        logger.warning(
                            'Genre Roulette: could not unmonitor %s after grab',
                            film.get('title'))
                except Exception as e:
                    logger.warning(
                        'Genre Roulette: unmonitor after grab failed for %s: %s',
                        film.get('title'), e)
            _note_origin(film, release)
            return True, 'grabbed'
        reason = ('; '.join(push.get('rejections') or [])
                  or push.get('error') or 'rejected')
        # False-negative guard: verified said no queue row, but Radarr may
        # already have grabbed into the client. Never undo+retry in that case
        # — that is how duplicate torrents were created.
        mid = (existing or {}).get('id')
        if mid and radarr.movie_download_active(mid):
            logger.info(
                'Genre Roulette: treating %s as grabbed despite verify miss '
                '(%s) — download already present', film.get('title'), reason)
            keep_monitored = (
                (not created and prior is None)
                or (prior is not None and bool(prior[0]))
            )
            if (not keep_monitored and existing and existing.get('monitored')
                    and existing.get('id')):
                try:
                    radarr.unmonitor_movie(existing['id'])
                except Exception:
                    pass
            _note_origin(film, release)
            return True, 'grabbed'
        if mid:
            try:
                qrec = radarr._queue_record(mid)
                if qrec and qrec.get('id') is not None:
                    radarr.remove_from_queue(
                        qrec['id'], remove_from_client=True)
            except Exception as e:
                logger.warning(
                    'Genre Roulette: could not clear queue before undo for %s: %s',
                    film.get('title'), e)
        _undo_failed_grab(radarr, existing, created, prior, film, reason)
        return False, reason
    except Exception as e:
        logger.error(f"Genre Roulette grab failed for {film['title']}: {e}",
                     exc_info=True)
        if created or prior is not None:
            _undo_failed_grab(radarr, existing, created, prior, film, str(e))
        return False, str(e)


# ── Cycle ─────────────────────────────────────────────────────────────────

def _imdb_relax_ladder(configured_votes: int) -> List[int]:
    """Vote floors strictly below the configured one, descending."""
    if configured_votes <= _IMDB_RELAX_VOTE_FLOOR:
        return []
    out: List[int] = []
    for stop in _IMDB_RELAX_VOTE_STOPS:
        if _IMDB_RELAX_VOTE_FLOOR <= stop < configured_votes:
            out.append(stop)
    if configured_votes > _IMDB_RELAX_VOTE_FLOOR and (
            not out or out[-1] > _IMDB_RELAX_VOTE_FLOOR):
        if _IMDB_RELAX_VOTE_FLOOR not in out:
            out.append(_IMDB_RELAX_VOTE_FLOOR)
    return out


def _stats_clear_imdb(stats: Optional[Dict[str, Any]], min_votes: int,
                      min_rating: float) -> bool:
    """Whether cached/lookup IMDb stats clear the given floors."""
    if not isinstance(stats, dict):
        return False
    votes, rating = stats.get('v'), stats.get('r')
    if min_votes and votes is None:
        return False
    if votes is None and rating is None:
        return False
    if min_votes and (votes or 0) < min_votes:
        return False
    if min_rating and (rating or 0) < min_rating:
        return False
    return True


def _supplement_films(settings, genres: List[str], exclude: set,
                      want_count: int, tmdb_key: str,
                      year_from: int, year_to: int,
                      min_votes: int) -> List[Dict[str, Any]]:
    """Pull more unowned Discover hits for genres not yet tried tonight."""
    if not genres or want_count <= 0 or not tmdb_key:
        return []
    walk: Dict[str, Any] = {
        'excluded': set(exclude),
        'hard': set(exclude),
        'cursors': {},
        'ctxs': {},
        'sig': 'supplement',
    }
    pool: Dict[int, Dict[str, Any]] = {}

    def params_for(genre: str) -> Dict[str, Any]:
        return _discover_params(tmdb_key, genre, year_from, year_to,
                                0.0, min_votes)

    _draw_stage(params_for, list(genres), walk, want_count, pool, 'supplement')
    out = [f for f in pool.values() if int(f['tmdb_id']) not in exclude]
    out.sort(key=lambda f: -(f.get('votes') or 0))
    return out


def run_cycle(dry_run: bool = False, settings=None,
              scheduled: bool = False) -> Dict[str, Any]:
    """Spin the wheel and grab up to movies_per_night films."""
    from app.settings_manager import SettingsManager
    from app.indexer_api import get_indexer_client
    from app.radarr_api import RadarrAPI
    from app import tracker_selection

    settings = settings or SettingsManager()
    rng = random.Random()
    started = datetime.now(timezone.utc).isoformat()

    want = _int_setting(settings, 'automation.genre_roulette.movies_per_night',
                        _DEFAULT_MOVIES_PER_NIGHT, 1, 20)
    min_seeders = _int_setting(settings, 'automation.genre_roulette.min_seeders',
                               _DEFAULT_MIN_SEEDERS, 0, 10_000)

    cand = candidates(settings, rng)
    if cand.get('error'):
        return {'error': cand['error'], 'started_at': started,
                'dry_run': dry_run, 'genres': cand.get('genres', [])}

    client = get_indexer_client(settings)
    if not client:
        return {'error': 'No indexer configured', 'started_at': started,
                'dry_run': dry_run, 'genres': cand['genres']}

    plan = tracker_selection.plan(settings, client, TRACKERS, TRACKERS_SETTING)
    if plan['error']:
        return {'error': plan['error'], 'started_at': started,
                'dry_run': dry_run, 'genres': cand['genres']}
    if plan['unknown'] and not plan['ids']:
        return {'error': 'None of the selected trackers are available: '
                         + ', '.join(plan['unknown']),
                'started_at': started, 'dry_run': dry_run,
                'genres': cand['genres']}
    if plan['unknown']:
        logger.warning('Genre Roulette: ignoring unavailable trackers: '
                       + ', '.join(plan['unknown']))

    profile_id = _profile_id(settings)
    radarr = None
    root_folder = ''
    if not dry_run:
        radarr_url = settings.get('radarr.url', '')
        radarr_key = settings.get('radarr.api_key', '')
        if not radarr_url or not radarr_key:
            return {'error': 'Radarr not configured', 'started_at': started,
                    'dry_run': dry_run, 'genres': cand['genres']}
        if not profile_id:
            return {'error': 'Genre roulette quality profile is not set',
                    'started_at': started, 'dry_run': dry_run,
                    'genres': cand['genres']}
        radarr = RadarrAPI(radarr_url, radarr_key)
        roots = radarr.get_root_folders()
        if not roots:
            return {'error': 'No Radarr root folders', 'started_at': started,
                    'dry_run': dry_run, 'genres': cand['genres']}
        root_folder = roots[0].get('path', '')

    tiers = _tier_plan(plan)
    if not tiers:
        return {'error': 'No usable trackers after tiering',
                'started_at': started, 'dry_run': dry_run,
                'genres': cand['genres']}

    # Premium budget is read once, from state, so concurrent manual runs cannot
    # both believe the night's allowance is unspent.
    prem_night_cap = _int_setting(
        settings, 'automation.genre_roulette.premium_per_night',
        _DEFAULT_PREMIUM_PER_NIGHT, 0, 20)
    prem_month_cap = _int_setting(
        settings, 'automation.genre_roulette.premium_per_month',
        _DEFAULT_PREMIUM_PER_MONTH, 0, 500)
    state_at_start = _read_state()
    prem_before_night, prem_before_month = _premium_used(state_at_start)
    prem_grabbed = 0

    # ── IMDb gate ────────────────────────────────────────────────────────
    imdb_min_votes = _int_setting(
        settings, 'automation.genre_roulette.imdb_min_votes',
        _DEFAULT_IMDB_MIN_VOTES, 0, 10_000_000)
    imdb_min_rating = _float_setting(
        settings, 'automation.genre_roulette.imdb_min_rating',
        _DEFAULT_IMDB_MIN_RATING, 0.0, 10.0)
    omdb_key = settings.get('apis.omdb.api_key') or ''
    tmdb_key = _tmdb_key(settings)
    imdb_on = bool(omdb_key) and bool(imdb_min_votes or imdb_min_rating)
    if not imdb_on and (imdb_min_votes or imdb_min_rating):
        logger.warning('Genre Roulette: IMDb floors are set but no OMDb API '
                       'key is configured — running on TMDb numbers alone')
    imdb_cache = dict(state_at_start.get('imdb_cache') or {})
    imdb_lookups = 0
    imdb_failures = 0
    imdb_rejected = 0
    imdb_relaxed = False

    films = cand['films']
    rows: List[Dict[str, Any]] = []
    scanned: List[Dict[str, Any]] = []
    parked_now: List[int] = []
    attempts = 0
    got = 0
    consecutive_failures = 0
    aborted = None

    # Diagnostic only: how often the preferred trackers had nothing usable.
    # This is the number that says whether BeyondHD is carrying more than its
    # share, and the one to watch if IPTorrents starts degrading.
    cheap_misses = 0

    def imdb_verdict(film: Dict[str, Any]) -> tuple:
        """(passes, stats). Hard wall on imdb_min_votes / imdb_min_rating.

        Fails closed when OMDb cannot return a usable vote count: exhausted
        lookup budget, consecutive OMDb failures, a failed lookup, or a blank
        vote field. Unknowns are never admitted under a configured floor.
        When the gate is off (no floors, or no OMDb key), everything passes.
        """
        nonlocal imdb_lookups, imdb_failures
        if not imdb_on:
            return True, None
        # Systemic OMDb trouble — do not admit unknowns under a floor.
        if imdb_failures >= _IMDB_MAX_FAILURES:
            return False, None

        key = str(film['tmdb_id'])
        hit = imdb_cache.get(key)
        if not _imdb_fresh(hit):
            if imdb_lookups >= _IMDB_MAX_LOOKUPS:
                return False, None
            imdb_lookups += 1
            hit = _imdb_lookup(int(film['tmdb_id']), tmdb_key, omdb_key)
            if hit is None:
                imdb_failures += 1
                return False, None
            imdb_failures = 0
            imdb_cache[key] = hit

        votes, rating = hit.get('v'), hit.get('r')
        # No vote count from OMDb => fail closed when a vote floor is set.
        if imdb_min_votes and votes is None:
            return False, hit
        if votes is None and rating is None:
            return False, hit
        if imdb_min_votes and votes < imdb_min_votes:
            return False, hit
        if imdb_min_rating and (rating or 0) < imdb_min_rating:
            return False, hit
        return True, hit

    def attempt(film: Dict[str, Any]) -> str:
        """Search and try to grab one film. 'grabbed', 'none' or 'abort'."""
        nonlocal attempts, got, consecutive_failures, prem_grabbed, cheap_misses
        attempts += 1

        # Premium is searched for this film unless its allowance is spent.
        # Skipping the tier rather than the film matters: the cheaper tiers may
        # still serve it, and if they cannot the next candidate is just as good.
        allowance = (prem_before_night + prem_grabbed < prem_night_cap
                     and prem_before_month + prem_grabbed < prem_month_cap)
        skip = () if allowance else (_PREMIUM_TRACKER,)

        found, ranked, tier_name = _search_tiers(
            client, film, tiers, min_seeders, skip=skip)
        if found is None:
            consecutive_failures += 1
            if consecutive_failures >= _MAX_SEARCH_FAILURES:
                # The rest would fail identically. Stop, and leave the night
                # unrecorded so it retries rather than spending the slot on an
                # outage.
                return 'abort'
            time.sleep(_SEARCH_PAUSE)
            return 'none'
        consecutive_failures = 0

        scanned.append({
            'title': film['title'], 'year': film['year'],
            'genre': film['genre'], 'releases_found': found['raw'],
            'eligible': len(found['usable']),
            'rejects': found['rejects'],
            'tier': tier_name,
            'imdb': film.get('imdb'),
            'picked': bool(ranked),
        })

        if tier_name == _PREMIUM_TRACKER or not ranked:
            cheap_misses += 1

        if not ranked:
            if found['raw']:
                # Something came back but nothing was usable. Park it so
                # tomorrow doesn't stall here; _active_parked lets it back in.
                parked_now.append(int(film['tmdb_id']))
            time.sleep(_SEARCH_PAUSE)
            return 'none'

        def row_for(release, grabbed=False, detail=None):
            return {
                'title': film['title'], 'year': film['year'],
                'tmdb_id': film['tmdb_id'], 'genre': film['genre'],
                'rating': film['rating'], 'votes': film['votes'],
                'imdb': film.get('imdb'),
                'quality': f"{release.get('resolution') or '?'} "
                           f"{release.get('source') or ''}".strip(),
                'size_gb': release.get('size_gb'),
                'seeders': release.get('seeders'),
                'tracker': release.get('tracker'),
                'torrent_name': release.get('torrent_name'),
                'grabbed': grabbed, 'detail': detail,
            }

        if dry_run:
            rows.append(row_for(ranked[0]))
            got += 1
            imdb_v = ((film.get('imdb') or {}).get('votes')
                      if isinstance(film.get('imdb'), dict) else None)
            logger.info(
                'Genre Roulette: dry-run pick %s (%s) imdb_votes=%s '
                '(floor=%s)',
                film['title'], film['year'], imdb_v, imdb_min_votes or 'off')
            time.sleep(_SEARCH_PAUSE)
            return 'grabbed'

        # Walk this film's releases until one is accepted. Radarr's size cap is
        # per-film and unknowable up front, so the top-ranked release being
        # refused says nothing about the next one.
        size_cap: Optional[float] = None
        last_row = None
        pushes = 0
        for release in ranked:
            # Budget counts pushes, not list positions — a release skipped
            # because we already know it exceeds the cap costs nothing, and
            # letting skips consume the budget would abandon films whose only
            # oversized releases happen to rank highest.
            if pushes >= _MAX_RELEASE_ATTEMPTS:
                break
            size_gb = release.get('size_gb') or 0
            if size_cap is not None and size_gb and size_gb > size_cap:
                continue

            # If a prior push already started a download (queue lag made
            # verification fail, movie was briefly removed, etc.), do NOT
            # push another release — that is tonight's double-copy bug.
            try:
                existing_now = radarr.get_movie_by_tmdb_id(int(film['tmdb_id']))
                mid_now = (existing_now or {}).get('id')
                if (mid_now and getattr(radarr, 'movie_download_active', None)
                        and radarr.movie_download_active(mid_now)):
                    logger.info(
                        'Genre Roulette: %s already downloading/history-grabbed '
                        '— stopping release walk, counting as grabbed',
                        film['title'])
                    last_row = row_for(release, True, 'already downloading')
                    break
            except Exception as e:
                logger.warning(
                    'Genre Roulette: pre-push download check failed for %s: %s',
                    film['title'], e)

            pushes += 1
            ok, detail = _grab(film, release, radarr, profile_id, root_folder)
            last_row = row_for(release, ok, detail)
            if ok:
                break
            # After a failed grab, re-check: undo may have been skipped because
            # the torrent actually landed — treat as success, do not retry.
            try:
                existing_now = radarr.get_movie_by_tmdb_id(int(film['tmdb_id']))
                mid_now = (existing_now or {}).get('id')
                if (mid_now and getattr(radarr, 'movie_download_active', None)
                        and radarr.movie_download_active(mid_now)):
                    logger.info(
                        'Genre Roulette: %s failed push detail=%r but download '
                        'active — counting as grabbed, no further releases',
                        film['title'], detail)
                    last_row = row_for(release, True, 'grabbed (post-fail active)')
                    _note_origin(film, release)
                    break
            except Exception as e:
                logger.warning(
                    'Genre Roulette: post-fail download check failed for %s: %s',
                    film['title'], e)
            cap = _parse_size_cap(detail)
            if cap is not None:
                size_cap = cap
                logger.info(
                    'Genre Roulette: %s capped at %.1fGB by the Radarr profile '
                    '— trying a smaller release', film['title'], cap)

        outcome = 'none'
        if last_row:
            rows.append(last_row)
            if last_row['grabbed']:
                got += 1
                outcome = 'grabbed'
                imdb_v = ((film.get('imdb') or {}).get('votes')
                          if isinstance(film.get('imdb'), dict) else None)
                logger.info(
                    'Genre Roulette: grabbed %s (%s) imdb_votes=%s '
                    '(floor=%s) via %s',
                    film['title'], film['year'], imdb_v,
                    imdb_min_votes or 'off',
                    last_row.get('tracker') or '?')
                if (last_row.get('tracker') or '').strip().lower() \
                        == _PREMIUM_TRACKER.lower():
                    prem_grabbed += 1
            else:
                # Every release we were willing to try was refused. Park it so
                # tomorrow spends its searches elsewhere.
                parked_now.append(int(film['tmdb_id']))
        time.sleep(_SEARCH_PAUSE)
        return outcome

    # Gate first, then rank passers by real IMDb fame. The configured floor is
    # the preferred bar; rejects are kept so a short night can relax the vote
    # floor and keep filling rather than exit under movies_per_night.
    passers: List[Dict[str, Any]] = []
    non_passers: List[Dict[str, Any]] = []
    for film in films:
        passes, stats = imdb_verdict(film)
        if stats:
            film['imdb'] = {'rating': stats.get('r'), 'votes': stats.get('v')}
        if not passes:
            imdb_rejected += 1
            non_passers.append(film)
            continue
        passers.append(film)

    this_year = date.today().year

    def _fame_key(f: Dict[str, Any]) -> tuple:
        imdb_votes = ((f.get('imdb') or {}).get('votes')
                      if isinstance(f.get('imdb'), dict) else None)
        # Prefer established titles over last ~2 years of Netflix-of-the-moment,
        # then pure IMDb vote count. Recents still clear if nothing older remains.
        recent = 1 if (f.get('year') or 0) >= (this_year - 1) else 0
        return (recent, -(imdb_votes or 0), -(f.get('votes') or 0))

    # Per-genre persistence: each spun genre keeps trying its next fame-ranked
    # passer until one grabs OR that genre's passers are exhausted. Only after
    # every spun genre has had that fair shot may leftover slots fill from
    # remaining passers by fame. A failed Action (RED) must not donate its
    # slot to leftover Action before Comedy/Romance get their turn.
    by_genre: Dict[str, List[Dict[str, Any]]] = {}
    for f in passers:
        by_genre.setdefault(f.get('genre') or '', []).append(f)
    for bucket in by_genre.values():
        bucket.sort(key=_fame_key)

    spun_genres: List[str] = list(cand.get('genres') or [])
    if passers and spun_genres:
        lead_g = next((g for g in spun_genres if by_genre.get(g)), spun_genres[0])
        top = (by_genre.get(lead_g) or passers)[0]
        logger.info(
            'Genre Roulette: %d / %d cleared IMDb floors (min_votes=%s) — '
            'leading %s with %s (%s) imdb_votes=%s '
            '(per-genre persistence, then fame fill)',
            len(passers), len(films), imdb_min_votes or 'off',
            lead_g, top.get('title'), top.get('year'),
            (top.get('imdb') or {}).get('votes'))
    elif films and not passers:
        logger.info(
            'Genre Roulette: 0 / %d cleared IMDb floors (min_votes=%s) — '
            'will relax / borrow genres if still short of %d',
            len(films), imdb_min_votes or 'off', want)

    # Franchise wall: never grab two films from the same TMDb collection in
    # one night, and skip a candidate whose collection already has a part in
    # recent state (grabbed_tmdb_ids / last_result picks).
    collection_cache: Dict[str, Any] = dict(
        state_at_start.get('collection_cache') or {})
    collection_parts_cache: Dict[int, List[int]] = {}
    blocked_tmdb = _recent_grab_tmdb_ids(state_at_start)
    # This-run grabs join the block set as they succeed.
    grabbed_this_run: set = set()

    def collection_blocked(film: Dict[str, Any]) -> bool:
        tid = int(film['tmdb_id'])
        cid = _collection_id_for(tid, tmdb_key, collection_cache)
        if not cid:
            return False
        parts = _collection_part_ids(cid, tmdb_key, collection_parts_cache)
        if not parts:
            # Lookup failed — do not block on incomplete data.
            return False
        rivals = (blocked_tmdb | grabbed_this_run) - {tid}
        hit = next((p for p in parts if p in rivals), None)
        if hit is None:
            return False
        logger.info(
            'Genre Roulette: skipping %s (%s) — same TMDb collection %s as '
            'recent/this-run grab tmdb_id=%s',
            film.get('title'), film.get('year'), cid, hit)
        return True

    def try_film(film: Dict[str, Any]) -> str:
        """Attempt one film; 'grabbed'/'none'/'abort'/'skip'."""
        if collection_blocked(film):
            return 'skip'
        outcome = attempt(film)
        if outcome == 'grabbed':
            grabbed_this_run.add(int(film['tmdb_id']))
        return outcome

    tried: set = set()
    genres_satisfied: set = set()
    collection_skips = 0
    effective_imdb_votes = imdb_min_votes

    def drain_bucket(bucket: List[Dict[str, Any]], label: str) -> None:
        """Try fame-ranked films until one grabs, bucket ends, or budget ends."""
        nonlocal aborted, collection_skips
        for film in bucket:
            if got >= want or attempts >= _MAX_SEARCH_ATTEMPTS or aborted:
                return
            tid = int(film['tmdb_id'])
            if tid in tried:
                continue
            tried.add(tid)
            outcome = try_film(film)
            if outcome == 'abort':
                aborted = 'Indexer unreachable — run abandoned, will retry'
                return
            if outcome == 'skip':
                collection_skips += 1
                continue
            if outcome == 'grabbed':
                if label:
                    genres_satisfied.add(label)
                return
            # 'none' → keep trying next fame-ranked film

    def fame_fill(candidates_list: List[Dict[str, Any]]) -> None:
        nonlocal aborted, collection_skips
        ordered = [f for f in candidates_list if int(f['tmdb_id']) not in tried]
        ordered.sort(key=_fame_key)
        for film in ordered:
            if got >= want or attempts >= _MAX_SEARCH_ATTEMPTS or aborted:
                return
            tid = int(film['tmdb_id'])
            if tid in tried:
                continue
            tried.add(tid)
            outcome = try_film(film)
            if outcome == 'abort':
                aborted = 'Indexer unreachable — run abandoned, will retry'
                return
            if outcome == 'skip':
                collection_skips += 1

    # Phase 1 — fair shot per spun genre (one success each, or exhaust).
    for g in spun_genres:
        if got >= want or attempts >= _MAX_SEARCH_ATTEMPTS or aborted:
            break
        bucket = by_genre.get(g) or []
        if not bucket:
            logger.info('Genre Roulette: spun genre %s has no IMDb passers', g)
            continue
        before = got
        drain_bucket(bucket, g)
        if aborted:
            break
        if got == before and got < want:
            logger.info(
                'Genre Roulette: spun genre %s exhausted without a grab '
                '(%d passers tried/skipped)', g, len(bucket))

    # Phase 2 — leftover slots by global fame among remaining passers.
    if not aborted and got < want and attempts < _MAX_SEARCH_ATTEMPTS:
        fame_fill(passers)

    # Phase 3 — relax IMDb vote floor and keep filling. Tonight 2026-09-18:
    # 250k left only two Twilight passers, both collection-blocked → 0 of 3
    # with zero indexer searches. Skip-and-retry-next at a lower fame bar.
    if (not aborted and got < want and attempts < _MAX_SEARCH_ATTEMPTS
            and imdb_on and imdb_min_votes):
        for floor in _imdb_relax_ladder(imdb_min_votes):
            if got >= want or attempts >= _MAX_SEARCH_ATTEMPTS or aborted:
                break
            newly: List[Dict[str, Any]] = []
            still_out: List[Dict[str, Any]] = []
            for film in non_passers:
                tid = int(film['tmdb_id'])
                if tid in tried:
                    continue
                stats = None
                if isinstance(film.get('imdb'), dict):
                    stats = {'v': film['imdb'].get('votes'),
                             'r': film['imdb'].get('rating')}
                # Ensure we have stats when possible (uses same cache/budget).
                if stats is None or (stats.get('v') is None and stats.get('r') is None):
                    passes, hit = imdb_verdict(film)
                    if hit:
                        film['imdb'] = {'rating': hit.get('r'), 'votes': hit.get('v')}
                        stats = hit
                    # imdb_verdict uses configured floors; ignore passes here.
                if _stats_clear_imdb(stats, floor, imdb_min_rating):
                    newly.append(film)
                else:
                    still_out.append(film)
            non_passers = still_out
            if not newly:
                continue
            imdb_relaxed = True
            effective_imdb_votes = floor
            newly.sort(key=_fame_key)
            for f in newly:
                by_genre.setdefault(f.get('genre') or '', []).append(f)
                passers.append(f)
            logger.info(
                'Genre Roulette: short of %d (have %d) — relaxing IMDb min_votes '
                '%s → %s; admitted %d more passers',
                want, got, imdb_min_votes, floor, len(newly))
            # Unfinished spun genres first, then global fame fill.
            for g in spun_genres:
                if got >= want or aborted:
                    break
                if g in genres_satisfied:
                    continue
                bucket = [f for f in newly if (f.get('genre') or '') == g]
                if bucket:
                    drain_bucket(bucket, g)
            if not aborted and got < want:
                fame_fill(newly)

    # Phase 4 — borrow more enabled genres rather than end under target.
    if (not aborted and got < want and attempts < _MAX_SEARCH_ATTEMPTS):
        enabled = selected_genres(settings) or list(GENRES)
        extras = [g for g in enabled if g not in spun_genres]
        rng.shuffle(extras)
        extras = extras[:_FILL_EXTRA_GENRES]
        if extras:
            from app import canon_index as _canon
            exclude_ids = set(int(f['tmdb_id']) for f in films) | tried | grabbed_this_run
            # Owned + parked + prior grabs must stay out of the supplemental draw.
            try:
                exclude_ids |= set(_canon.owned_tmdb_ids() or set())
            except Exception as e:
                logger.warning('Genre Roulette: owned set for supplement: %s', e)
            exclude_ids |= set(int(x) for x in (state_at_start.get('grabbed_tmdb_ids') or [])
                               if str(x).lstrip('-').isdigit())
            exclude_ids |= _active_parked(state_at_start)
            prefilter = _prefilter_votes(
                _int_setting(settings, 'automation.genre_roulette.min_votes',
                             _DEFAULT_MIN_VOTES, 0, 1_000_000),
                effective_imdb_votes or imdb_min_votes or _DEFAULT_IMDB_MIN_VOTES)
            need = max((want - got) * _FRESH_PER_WANTED, _MIN_FRESH_POOL)
            logger.info(
                'Genre Roulette: still short (%d/%d) — borrowing genres %s',
                got, want, ', '.join(extras))
            more = _supplement_films(
                settings, extras, exclude_ids, need, tmdb_key,
                int(cand.get('year_from') or _WIDE_YEAR_FROM),
                int(cand.get('year_to') or date.today().year),
                prefilter)
            admitted: List[Dict[str, Any]] = []
            for film in more:
                tid = int(film['tmdb_id'])
                if tid in tried:
                    continue
                # Gate at the effective (possibly relaxed) floor.
                if imdb_on:
                    passes, stats = imdb_verdict(film)
                    if stats:
                        film['imdb'] = {'rating': stats.get('r'),
                                        'votes': stats.get('v')}
                    # Re-check against effective floor if configured verdict used
                    # the higher bar.
                    stats_use = None
                    if isinstance(film.get('imdb'), dict):
                        stats_use = {'v': film['imdb'].get('votes'),
                                     'r': film['imdb'].get('rating')}
                    if not _stats_clear_imdb(
                            stats_use, effective_imdb_votes or 0, imdb_min_rating):
                        # Try lower ladder stops before discarding.
                        ok = False
                        for floor in _imdb_relax_ladder(imdb_min_votes or 0):
                            if floor > (effective_imdb_votes or 0):
                                continue
                            if _stats_clear_imdb(stats_use, floor, imdb_min_rating):
                                effective_imdb_votes = floor
                                imdb_relaxed = True
                                ok = True
                                break
                        if not ok:
                            continue
                admitted.append(film)
                films.append(film)
                passers.append(film)
                by_genre.setdefault(film.get('genre') or '', []).append(film)
            if admitted:
                logger.info(
                    'Genre Roulette: supplemental draw admitted %d films from %s',
                    len(admitted), ', '.join(extras))
                for g in extras:
                    if got >= want or aborted:
                        break
                    bucket = [f for f in admitted if (f.get('genre') or '') == g]
                    bucket.sort(key=_fame_key)
                    drain_bucket(bucket, g)
                if not aborted and got < want:
                    fame_fill(admitted)

    result: Dict[str, Any] = {
        'started_at': started,
        'dry_run': dry_run,
        'genres': cand['genres'],
        'genres_failed': cand.get('genres_failed') or [],
        'year_from': cand.get('year_from'),
        'year_to': cand.get('year_to'),
        'candidates': len(films),
        'considered': cand.get('considered'),
        'pool_target': cand.get('pool_target'),
        'pages_read': cand.get('pages_read'),
        'widened': cand.get('widened') or '',
        'stages_run': cand.get('stages_run') or [],
        'searched': attempts,
        'scanned': scanned,
        'wanted': want,
        'trackers': plan['names'],
        'tiers': [t['name'] for t in tiers],
        'premium_tonight': prem_before_night + prem_grabbed,
        'premium_window': prem_before_month + prem_grabbed,
        'premium_caps': [prem_night_cap, prem_month_cap],
        'cheap_misses': cheap_misses,
        'imdb_floors': [imdb_min_rating, imdb_min_votes] if imdb_on else None,
        'imdb_rejected': imdb_rejected,
        'imdb_lookups': imdb_lookups,
        'imdb_relaxed': imdb_relaxed,
        'imdb_effective_votes': effective_imdb_votes if imdb_on else None,
        'collection_skips': collection_skips,
        'picks': [],
        'grabbed': 0,
    }
    if aborted:
        result['error'] = aborted
        return result

    result['picks'] = rows
    result['grabbed'] = sum(1 for r in rows if r['grabbed'])

    if not rows:
        if not films:
            result['reason'] = "Nothing unowned found in tonight's genres"
        elif not passers and not imdb_relaxed:
            result['reason'] = (
                f'No candidates cleared IMDb floors '
                f'(rejected {imdb_rejected}/{len(films)})')
        elif collection_skips and attempts == 0:
            result['reason'] = (
                f'All {collection_skips} IMDb passers skipped by collection '
                f'dedupe (no indexer search)')
        else:
            result['reason'] = 'No candidate had a usable standard-quality release'
    elif dry_run:
        result['reason'] = 'dry run — nothing grabbed'
    elif result['grabbed'] < want:
        # Short of target is a real outcome worth naming, not silence: either the
        # candidate pool ran dry, the search budget did, or Radarr refused every
        # release we offered. Phrased as the cause only — summarise() supplies
        # the counts.
        refused = len([r for r in rows if not r['grabbed']])
        if attempts >= _MAX_SEARCH_ATTEMPTS:
            result['reason'] = f'hit the {_MAX_SEARCH_ATTEMPTS}-search limit'
        elif attempts >= len(films):
            # Reaching this now means the widest query is genuinely close to
            # fully owned, not that the search was fenced in too shallow.
            result['reason'] = (
                f'ran out of unowned candidates ({len(films)} available'
                + (f', {refused} refused by Radarr' if refused else '')
                + (f'; searched as wide as "{cand.get("widened")}"'
                   if cand.get('widened') else '')
                + ')')
        else:
            result['reason'] = f'stopped after {attempts} searches'

    if dry_run:
        return result

    state = _read_state()
    today = datetime.now().strftime('%Y-%m-%d')

    grabbed_ids = set(state.get('grabbed_tmdb_ids') or [])
    for p in result['picks']:
        if p['grabbed']:
            grabbed_ids.add(int(p['tmdb_id']))
    state['grabbed_tmdb_ids'] = sorted(grabbed_ids)[-5000:]

    parked_map = dict(state.get('parked') or {})
    for tid in parked_now:
        parked_map[str(tid)] = today
    state['parked'] = parked_map
    state['recent_genres'] = cand['genres']
    state['last_run_date'] = today
    # Written here rather than in candidates() so an aborted run — indexer
    # down, Radarr misconfigured — leaves the wheel where it was instead of
    # advancing past films it never actually searched for.
    if cand.get('cursors'):
        state['cursors'] = cand['cursors']

    if imdb_cache:
        state['imdb_cache'] = _prune_imdb_cache(imdb_cache)

    if collection_cache:
        state['collection_cache'] = _prune_collection_cache(collection_cache)

    if prem_grabbed:
        cutoff = (datetime.now()
                  - timedelta(days=_PREMIUM_WINDOW_DAYS)).strftime('%Y-%m-%d')
        kept = [d for d in (state.get('premium_grabs') or []) if str(d) >= cutoff]
        state['premium_grabs'] = kept + [today] * prem_grabbed

    if scheduled:
        state['last_scheduled_date'] = today
        state['last_scheduled_time'] = _scheduled_time(settings)
        # A shortfall is recorded rather than latched away, so _maybe_run can
        # come back to it tonight instead of writing the night off.
        prev = state.get('short_night') or {}
        if result['grabbed'] < want:
            state['short_night'] = {
                'date': today,
                'retries': (int(prev.get('retries') or 0) + 1
                            if prev.get('date') == today else 0),
                'grabbed': result['grabbed'], 'wanted': want,
            }
        else:
            state['short_night'] = {}
    _write_state(state)
    return result


def preview() -> Dict[str, Any]:
    """Dry run — tonight's wheel and what it would grab. Grabs nothing."""
    return run_cycle(dry_run=True)


def summarise(result: Dict[str, Any]) -> str:
    """One line describing how a run ended, for the log and the card."""
    if not isinstance(result, dict):
        return 'No result recorded'
    if result.get('error'):
        return str(result['error'])
    genres = ', '.join(result.get('genres') or []) or 'no genres'
    picks = result.get('picks') or []
    got = [p for p in picks if p['grabbed']]
    want = result.get('wanted')
    if got:
        names = '; '.join(f"{p['title']} ({p['year']})" for p in got)
        # Naming the shortfall matters: "grabbed 2" when 3 were asked for looks
        # like a broken setting unless the run says why it stopped short.
        short = (f' (of {want} — {result["reason"]})'
                 if want and len(got) < want and result.get('reason')
                 else f' of {want}' if want and len(got) < want else '')
        # Worth saying on a *successful* night too: a full 3 pulled from a
        # widened query is the signal that the configured window is draining.
        wide = (f' [widened: {result["widened"]}]'
                if result.get('widened') else '')
        # Which tracker served each pick is the thing to watch when the point
        # of the tiering is to keep the premium one lightly used.
        by_tracker: Dict[str, int] = {}
        for p in got:
            t = p.get('tracker') or '?'
            by_tracker[t] = by_tracker.get(t, 0) + 1
        split = (' [' + ', '.join(f'{k}×{v}' for k, v in
                                  sorted(by_tracker.items())) + ']'
                 if by_tracker else '')
        return f'{genres} → grabbed {len(got)}{short}{wide}{split}: {names}'
    if picks and result.get('dry_run'):
        names = '; '.join(f"{p['title']} ({p['year']})" for p in picks)
        return f'{genres} → would grab {names}'
    if picks:
        detail = picks[0].get('detail') or 'unknown reason'
        return f'{genres} → {len(picks)} found but none grabbed: {detail}'
    return f"{genres} → {result.get('reason') or 'nothing grabbed'}"


def _log_entry(result: Dict[str, Any], at: str) -> Dict[str, Any]:
    picks = result.get('picks') or []
    got = [p for p in picks if p['grabbed']]
    return {
        'at': at,
        'outcome': ('grabbed' if got
                    else 'error' if result.get('error')
                    else 'failed' if picks
                    else 'nothing'),
        'summary': summarise(result),
        'genres': result.get('genres') or [],
        'candidates': result.get('candidates'),
        'widened': result.get('widened') or '',
        'pages_read': result.get('pages_read'),
        'premium_tonight': result.get('premium_tonight'),
        'premium_window': result.get('premium_window'),
        'searched': result.get('searched'),
        'grabbed': len(got),
        'titles': [{'title': p['title'], 'year': p['year'],
                    'genre': p['genre'], 'quality': p['quality'],
                    'size_gb': p['size_gb'], 'tracker': p['tracker']}
                   for p in got],
    }


def trigger_manual_run(dry_run: bool = False) -> None:
    threading.Thread(target=lambda: _guarded(dry_run), daemon=True).start()


def _guarded(dry_run: bool, scheduled: bool = False) -> None:
    _hydrate_status()
    with _lock:
        if _last_status['running']:
            logger.warning('Genre Roulette already running, skipping')
            return
        _last_status['running'] = True
    try:
        result = run_cycle(dry_run=dry_run, scheduled=scheduled)
    except Exception as e:
        logger.error(f'Genre Roulette cycle error: {e}', exc_info=True)
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

    logger.info('Genre Roulette: %s', summarise(result))

    # A preview is a question, not an event — it must not overwrite the record
    # of the last real run.
    if dry_run:
        return
    try:
        state = _read_state()
        state['last_run'] = at
        # `scanned` is per-film search detail — useful in the live response, too
        # big to accumulate in the state file.
        state['last_result'] = {k: v for k, v in result.items() if k != 'scanned'}
        _write_state(state)
    except Exception as e:
        logger.warning(f'Genre Roulette: could not persist last run: {e}')
    _append_log(_log_entry(result, at))


# ── Nightly scheduler ─────────────────────────────────────────────────────

def start_genre_roulette_thread() -> None:
    from app import instance as _instance
    if not _instance.writes_allowed('roulette'):   # LUMA_INSTANCE=test: off unless opted in
        return None
    _hydrate_status()
    t = threading.Thread(target=_scheduler_loop, daemon=True,
                         name='genre-roulette')
    t.start()
    logger.info('Genre Roulette scheduler started '
                '(disabled until enabled in settings)')


def _scheduler_loop() -> None:
    time.sleep(120)  # after the other automations' startup burst
    while True:
        try:
            _maybe_run()
        except Exception as e:
            logger.error(f'Genre Roulette scheduler error: {e}', exc_info=True)
        time.sleep(_sleep_seconds())


def _sleep_seconds() -> float:
    """How long to wait before the next check.

    A fixed poll can only fire on its own grid, so a 22:30 schedule would land
    wherever the grid happened to fall. Instead, sleep exactly up to the next
    due time when that is sooner than the poll interval, and otherwise poll —
    the poll is what picks up a schedule the user changed mid-wait.
    """
    try:
        nxt = _next_run_dt()
        if nxt:
            delta = (nxt - datetime.now()).total_seconds()
            if 0 < delta < _CHECK_INTERVAL_SECONDS:
                return delta + 1  # land just past the minute, never just shy
    except Exception as e:
        logger.warning(f'Genre Roulette: falling back to poll interval: {e}')
    return _CHECK_INTERVAL_SECONDS


def _maybe_run() -> None:
    from app.settings_manager import SettingsManager
    settings = SettingsManager()

    if not settings.get('automation.genre_roulette.enabled', False):
        return

    at = _scheduled_time(settings)
    hour, minute = (int(p) for p in at.split(':'))
    now = datetime.now()
    if now < now.replace(hour=hour, minute=minute, second=0, microsecond=0):
        return

    # Nightly, and keyed on the scheduled run only so a manual "Run now"
    # earlier in the day doesn't cancel tonight's. The HH:MM is part of the key
    # too: without it, moving the schedule after the day's run had already
    # fired would silently do nothing until tomorrow, which reads exactly like
    # a scheduler that never runs at all.
    state = _read_state()
    if (state.get('last_scheduled_date') == now.strftime('%Y-%m-%d')
            and state.get('last_scheduled_time') == at):
        # The slot is spent — but a night that came up short is not finished.
        if not _retry_due(state, now):
            return
        short = state.get('short_night') or {}
        logger.info(
            'Genre Roulette: retrying tonight (grabbed %s of %s, retry %d '
            'of %d)', short.get('grabbed'), short.get('wanted'),
            int(short.get('retries') or 0) + 1, _SHORT_RETRY_LIMIT)
    else:
        logger.info(f'Genre Roulette: starting nightly run (scheduled {at})')
    _guarded(dry_run=False, scheduled=True)
