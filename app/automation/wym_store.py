"""v254 What you're missing: settings (Settings > Automation), JSON in the data dir.

Every value prod 1b4ea74 hard-codes in app/daily_missing.py is a setting here, with prod's value as the default:
RAIL_SIZE 20 (:37), REFRESH_INTERVAL_SEC / POOL_MAX_AGE_SEC 8 h (:54-55), EXCLUDE_RECENT_DAYS 3 (:63), YEAR_MIN 1985
(:57), ENGLISH (:56, discover with_original_language=en), TMDB_VOTE_FALLBACK 150 (:53), IMDB_HARD_REJECT 1,000 (:52),
Trakt watchers 50 (:423), the eight Trakt lists (:471-480), the TMDb lists, genre buckets and year windows (:493-562),
include_adult=false and the poster requirement (:392, :578). Whether the rail shows, and where, is Home screen
settings' (home_rails: the one show/hide and order, last by default), not a setting here. One deviation: Latest year defaults to the current year
(prod's YEAR_MAX is a fixed 2025, which already hides 2026 films). Pool plumbing (page counts, sleeps, timeouts,
workers, caches, pool target and minimum) stays in daily_missing.

Files: what_youre_missing_settings.json next to the pool (what_youre_missing_pool.json), 0600, atomic replace.
Nothing here talks to a network service.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

_cfg: Dict[str, Any] = {}
_lock = threading.RLock()
_changed = {'at': 0.0}

SETTINGS_FILE = 'what_youre_missing_settings.json'

# Trakt movie lists (prod :471-480), in the order the page shows them.
TRAKT_LISTS: Dict[str, str] = {
    'popular': 'Popular', 'trending': 'Trending', 'anticipated': 'Anticipated', 'boxoffice': 'Box office',
    'watched_yearly': 'Most watched this year', 'watched_all': 'Most watched all time',
    'favorited_yearly': 'Most favorited this year', 'favorited_all': 'Most favorited all time',
}
# TMDb lists (prod :493-562): /movie/popular, /movie/top_rated, discover by votes, popularity, revenue and
# rating, the genre buckets and the two-year windows.
TMDB_LISTS: Dict[str, str] = {
    'popular': 'Popular', 'top_rated': 'Top rated', 'most_voted': 'Most voted', 'most_popular': 'Most popular',
    'highest_grossing': 'Highest grossing', 'highest_rated': 'Highest rated', 'by_genre': 'By genre',
    'by_year': 'By year',
}
# Prod's 14 genre buckets (:522), TMDb ids.
GENRES: Dict[str, int] = {
    'Action': 28, 'Adventure': 12, 'Animation': 16, 'Comedy': 35, 'Crime': 80, 'Drama': 18, 'Family': 10751,
    'Fantasy': 14, 'History': 36, 'Horror': 27, 'Mystery': 9648, 'Romance': 10749, 'Science Fiction': 878,
    'Thriller': 53,
}
LANGUAGES = ('en', 'fr', 'es', 'de', 'it', 'ja', 'ko', 'zh', 'hi', 'sv', 'da', 'no', 'pt', 'ru')
LANGUAGE_NAMES: Dict[str, str] = {
    'en': 'English', 'fr': 'French', 'es': 'Spanish', 'de': 'German', 'it': 'Italian', 'ja': 'Japanese',
    'ko': 'Korean', 'zh': 'Chinese', 'hi': 'Hindi', 'sv': 'Swedish', 'da': 'Danish', 'no': 'Norwegian',
    'pt': 'Portuguese', 'ru': 'Russian',
}
# Prod's non-Latin title filter (:331-352) keeps an English shelf; it applies while every chosen language is
# written in Latin script.
LATIN_LANGUAGES = frozenset({'en', 'fr', 'es', 'de', 'it', 'sv', 'da', 'no', 'pt'})
REBUILD_HOURS = (2, 4, 6, 8, 12, 24)
MOVIES_MIN, MOVIES_MAX = 5, 40
NO_REPEAT_MAX = 14                       # served history is kept 14 days (prod SERVED_HISTORY_KEEP_DAYS)

# Settings that change which films are in the pool; a change rebuilds it (the slate applies them at once).
POOL_KEYS = ('year_from', 'year_to', 'languages', 'min_tmdb_votes', 'min_imdb_votes', 'min_trakt_watchers',
             'trakt_lists', 'tmdb_lists', 'exclude_genres', 'include_adult', 'require_poster')


def defaults() -> Dict[str, Any]:
    return {
        'movies_shown': 20, 'rebuild_hours': 8, 'no_repeat_days': 3,
        'year_from': 1985, 'year_to': None,            # None: the current year
        'languages': ['en'], 'min_tmdb_votes': 150, 'min_imdb_votes': 1000, 'min_trakt_watchers': 50,
        'trakt_lists': list(TRAKT_LISTS), 'tmdb_lists': list(TMDB_LISTS), 'exclude_genres': [],
        'include_adult': False, 'require_poster': True,
    }


def data_dir() -> Path:
    """Prod's data dir for these files (daily_missing._data_dir): /app/data, else next to DATABASE_PATH."""
    if _cfg.get('data_dir'):
        return Path(_cfg['data_dir'])
    if os.environ.get('LUMA_STATE_DIR'):                 # luma-test's state dir (first_run.data_dir)
        return Path(os.environ['LUMA_STATE_DIR'])
    docker_data = Path('/app/data')
    try:
        if docker_data.is_dir():
            return docker_data
    except OSError:                                      # e.g. /app unreadable outside the container
        pass
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent
    return Path(__file__).resolve().parent.parent.parent / 'data'


def path() -> Path:
    return data_dir() / SETTINGS_FILE


class Invalid(Exception):
    def __init__(self, errors: Dict[str, str]):
        super().__init__('invalid')
        self.errors = errors


def _int(v: Any, lo: int, hi: int) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
        return None
    v = int(v)
    return v if lo <= v <= hi else None


def _subset(v: Any, allowed, empty_ok: bool = True) -> Optional[List[str]]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v) or any(x not in allowed for x in v):
        return None
    if not v and not empty_ok:
        return None
    return [x for x in allowed if x in v]


def _bool(v: Any) -> Optional[bool]:
    return v if isinstance(v, bool) else None


_MISSING = object()


def _year_to(v: Any) -> Any:
    return None if v is None else (_int(v, 1900, 2200) if _int(v, 1900, 2200) is not None else _MISSING)


RULES = {
    'include_adult': _bool, 'require_poster': _bool,
    'movies_shown': lambda v: _int(v, MOVIES_MIN, MOVIES_MAX),
    'rebuild_hours': lambda v: v if not isinstance(v, bool) and v in REBUILD_HOURS else None,
    'no_repeat_days': lambda v: _int(v, 0, NO_REPEAT_MAX),
    'year_from': lambda v: _int(v, 1900, 2200),
    'languages': lambda v: _subset(v, LANGUAGES, empty_ok=False),
    'min_tmdb_votes': lambda v: _int(v, 0, 1_000_000),
    'min_imdb_votes': lambda v: _int(v, 0, 10_000_000),
    'min_trakt_watchers': lambda v: _int(v, 0, 1_000_000),
    'trakt_lists': lambda v: _subset(v, TRAKT_LISTS),
    'tmdb_lists': lambda v: _subset(v, TMDB_LISTS),
    'exclude_genres': lambda v: _subset(v, GENRES),
}


def year_to(cfg: Dict[str, Any], today: Optional[date] = None) -> int:
    """The effective latest year: the saved one, or the current year."""
    y = cfg.get('year_to')
    return int(y) if isinstance(y, int) else (today or date.today()).year


def check(body: Any, cur: Dict[str, Any]) -> Dict[str, Any]:
    """The settings after applying *body* (a partial update) to *cur*. Raises Invalid."""
    if not isinstance(body, dict):
        raise Invalid({'_': 'Invalid'})
    out, errors = dict(cur), {}
    for k, v in body.items():
        if k == 'year_to':
            got = _year_to(v)
            if got is _MISSING:
                errors[k] = 'Invalid'
            else:
                out[k] = got
            continue
        if k not in RULES:
            errors[k] = 'Unknown'
            continue
        got = RULES[k](v)
        if got is None:
            errors[k] = 'Invalid'
        else:
            out[k] = got
    if not errors and out['year_from'] > year_to(out):
        errors['year_to' if 'year_to' in body else 'year_from'] = 'Invalid'
    if errors:
        raise Invalid(errors)
    return out


def _read() -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path().read_text())
    except (FileNotFoundError, ValueError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def settings() -> Dict[str, Any]:
    """Saved settings over the defaults; a bad saved value falls back to its default."""
    base = defaults()
    raw = _read() or {}
    out = dict(base)
    for k, v in raw.items():
        if k not in base:
            continue
        try:
            out = check({k: v}, out)
        except Invalid:
            pass
    return out


def _write(data: Dict[str, Any]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def save(body: Any) -> Dict[str, Any]:
    with _lock:
        before = settings()
        new = check(body, before)
        _write(new)
    if pool_sig(new) != pool_sig(before) or new['rebuild_hours'] != before['rebuild_hours']:
        _changed['at'] = time.time()
    return new


def changed_at() -> float:
    """When a setting that changes the pool or its schedule was last saved (this process)."""
    return _changed['at']


def pool_sig(cfg: Dict[str, Any], extra: Optional[Dict[str, Any]] = None, today: Optional[date] = None) -> str:
    """A fingerprint of everything that decides the pool (the settings plus which sources are connected)."""
    body = {k: cfg.get(k) for k in POOL_KEYS}
    body['year_to'] = year_to(cfg, today)
    body.update(extra or {})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
