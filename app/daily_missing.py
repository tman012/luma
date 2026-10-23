"""What you're missing — daily home rail of popular movies the library does not own.

Ported from prod 1b4ea74 (app/daily_missing.py). Discovery only (NOT an auto-grabber). Builds a large
unowned-popular pool from Trakt + TMDb, quality-gates it for crowd signal, and serves a slate that is stable
for the America/New_York calendar day and reshuffles the next morning, preferring titles not served in the
last few days.

v254 (Taylor): everything prod hard-codes is a setting (app/automation/wym_store, Settings > Automation,
prod's values as defaults); the pool plumbing below stays internal. Ownership is the Plex movie sections,
in-flight grabs (movie_grabs and grab_origin) and the native movie watchlist: no Radarr, no
upgrade_library.db. Settings that change the pool rebuild it (after a short pause, so a run of edits builds
once); the slate applies them at once to the pool it has.

Writes: only what_youre_missing_{pool,dismissed,served,settings}.json in the data dir. It never adds,
grabs, moves or deletes anything, so on the test instance it runs as an explicit exemption
(instance.DATA_DIR_ONLY), not through the writes gate.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import requests

from app.automation import wym_store

logger = logging.getLogger(__name__)

NY = ZoneInfo('America/New_York')

RAIL_ID = 'what_youre_missing'
TITLE = "What you're missing"
RAIL_SIZE = 20                     # prod's default; the setting is Movies shown
# ---- plumbing (kept internal, prod's values) ----
POOL_TARGET = 4000
POOL_MIN_HEALTHY = 400             # below this, dig deeper once (prod :612)
TRAKT_PAGE_SIZE = 20
TRAKT_PATHS = {
    'popular': '/movies/popular', 'trending': '/movies/trending', 'anticipated': '/movies/anticipated',
    'boxoffice': '/movies/boxoffice', 'watched_yearly': '/movies/watched/yearly', 'watched_all': '/movies/watched/all',
    'favorited_yearly': '/movies/favorited/yearly', 'favorited_all': '/movies/favorited/all',
}
TRAKT_PAGES = {'popular': 40, 'watched_yearly': 20, 'watched_all': 15, 'anticipated': 15, 'favorited_yearly': 15,
               'favorited_all': 15, 'boxoffice': 1, 'trending': 10}
TRAKT_EXPAND = {'popular': 50, 'watched_all': 40, 'favorited_all': 30, 'watched_yearly': 30}
TMDB_POPULAR_PAGES = 40
TMDB_TOP_RATED_PAGES = 25
TMDB_DISCOVER_PAGES = 45
GENRE_PAGES = 10
YEAR_VOTE_PAGES = 12
YEAR_POP_PAGES = 5
HIGHEST_RATED_VOTES = 2000         # prod :518
EXPAND_MIN_VOTES = 800             # prod :638
IMDB_SOFT_VOTES = 5_000            # prod IMDB_MIN_VOTES: enough IMDb votes admit a film on their own
TRAKT_VOTES_PER_WATCHER = 10       # prod: 500 Trakt votes stand in for 50 watchers
TMDB_SOFT_SHARE = 3                # prod: a third of the TMDb minimum (50 of 150) still admits a film
OMDB_MAX = 900                     # OMDb lookups per build (prod :593)
OMDB_WORKERS = 8
TMDB_WORKERS = 12
TRAKT_TIMEOUT_S, TMDB_TIMEOUT_S, TMDB_DETAIL_TIMEOUT_S = 15, 12, 6
TRAKT_SLEEP_S, TMDB_SLEEP_S = 0.12, 0.08
SERVED_HISTORY_KEEP_DAYS = 14
STARTUP_DELAY_S = 8
CHECK_S = 300                      # the scheduler looks every 5 minutes (and at once on a settings change)
SETTLE_S = 30                      # a pool-changing edit waits this long for the next one before rebuilding
RETRY_S = 1800                     # after a failed rebuild, try again in 30 minutes (or on the next edit)

_lock = threading.RLock()
_build_lock = threading.Lock()
_wake = threading.Event()
_thread_started = False
_mem_pool: Optional[Dict[str, Any]] = None
_mem_key: Optional[Tuple[Any, ...]] = None
_mem_seed_ids: List[int] = []
_state: Dict[str, Any] = {'building': False, 'last_attempt': 0.0, 'last_error': None}
_start = {'fn': lambda target: threading.Thread(target=target, name='daily-missing-build', daemon=True).start()}

# Trakt genre slugs -> TMDb genre ids (Trakt extended=full lists genres by slug).
TRAKT_GENRES = {
    'action': 28, 'adventure': 12, 'animation': 16, 'comedy': 35, 'crime': 80, 'documentary': 99, 'drama': 18,
    'family': 10751, 'fantasy': 14, 'history': 36, 'horror': 27, 'music': 10402, 'musical': 10402,
    'mystery': 9648, 'romance': 10749, 'science-fiction': 878, 'thriller': 53, 'war': 10752, 'western': 37,
}


def _data_dir() -> Path:
    return wym_store.data_dir()


def _pool_path() -> Path:
    return _data_dir() / 'what_youre_missing_pool.json'


def _dismissed_path() -> Path:
    return _data_dir() / 'what_youre_missing_dismissed.json'


def _served_path() -> Path:
    return _data_dir() / 'what_youre_missing_served.json'


def _today_ny() -> str:
    return datetime.now(NY).strftime('%Y-%m-%d')


def _settings():
    """v259: settings.json as on disk (SettingsFile): reading the connector keys never rewrites it."""
    from app.settings_manager import SettingsFile
    return SettingsFile()


# ------------------------------------------------------------------ outside services (mocked in tests)

def trakt_client_id() -> str:
    """The Trakt client ID while Trakt is connected (Settings > Connectors), else ''."""
    try:
        s = _settings()
        if not s.get('apis.trakt.enabled', False):
            return ''
        return str(s.get('apis.trakt.client_id', '') or '').strip()
    except Exception:                                    # noqa: BLE001
        return ''


def tmdb_key() -> str:
    """Settings > Connectors' TMDb key; full builds also take TMDB_API_KEY (prod :454)."""
    import os
    try:
        key = str(_settings().get('apis.tmdb.api_key', '') or '').strip()
    except Exception:                                    # noqa: BLE001
        key = ''
    if key:
        return key
    from app import minimal
    return '' if minimal.enabled() else (os.environ.get('TMDB_API_KEY') or '').strip()


def omdb_on() -> bool:
    from app import omdb
    return bool(omdb.api_key())


def omdb_ratings(imdb_id: str) -> Optional[Dict[str, Any]]:
    """IMDb rating and votes (app/omdb: cached 90 days, fail open)."""
    from app import omdb
    return omdb.get_ratings(imdb_id)


def http_get(url: str, params: Dict[str, Any], headers: Optional[Dict[str, str]] = None, timeout: float = 12):
    return requests.get(url, params=params, headers=headers, timeout=timeout)


def plex_index(cached_only: bool = False) -> Optional[Dict[str, Any]]:
    """Plex's ownership index (every movie section). cached_only: the last one built, never a fresh scan
    (the Home rail stays inside its time budget); a stale one is refreshed in the background."""
    from app.plex_service import plex_service
    if not cached_only:
        return plex_service.build_ownership_index()
    idx = getattr(plex_service, '_ownership_index', None)
    age = time.time() - float(getattr(plex_service, '_ownership_index_ts', 0) or 0)
    if not idx or age >= getattr(plex_service, '_OWNERSHIP_TTL', 300):
        _refresh_plex_index_async()
    return idx or None


_plex_refresh = {'on': False}


def _refresh_plex_index_async() -> None:
    with _lock:
        if _plex_refresh['on']:
            return
        _plex_refresh['on'] = True

    def go():
        try:
            from app.plex_service import plex_service
            plex_service.build_ownership_index()
        except Exception as e:                           # noqa: BLE001
            logger.warning("What you're missing: Plex index refresh failed: %s", type(e).__name__)
        finally:
            _plex_refresh['on'] = False
    threading.Thread(target=go, name='daily-missing-plex', daemon=True).start()


def in_flight() -> Set[int]:
    """tmdb ids with an active movie grab (movies.db) or a movie in the grab_origin store."""
    from app.automation import gr_select
    return gr_select.in_flight()


def watchlist_ids() -> Set[int]:
    """The native movie watchlist (test installs only)."""
    from app import instance
    if not instance.is_test():
        return set()
    from app import movie_watchlist
    return {int(x) for x in movie_watchlist.tmdb_ids()}


def _norm(title: str) -> str:
    from app import tv_match
    return tv_match.normalize(title)


# ------------------------------------------------------------------ fetching

def _trakt_headers(client_id: str) -> Dict[str, str]:
    from app import trakt_http
    return trakt_http.headers(client_id)


def _fetch_trakt_list(path: str, client_id: str, pages: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in range(1, pages + 1):
        params = {'page': page, 'limit': TRAKT_PAGE_SIZE, 'extended': 'full'}
        try:
            resp = http_get(f'https://api.trakt.tv{path}', params, _trakt_headers(client_id), TRAKT_TIMEOUT_S)
            if resp.status_code == 429:
                retry = int(resp.headers.get('Retry-After', '20'))
                logger.warning('Trakt rate-limited on %s page %s — sleeping %ss', path, page, retry)
                time.sleep(min(retry, 60))
                resp = http_get(f'https://api.trakt.tv{path}', params, _trakt_headers(client_id), TRAKT_TIMEOUT_S)
            if not resp.ok:
                logger.warning('Trakt %s page %s -> %s', path, page, resp.status_code)
                break
            batch = resp.json() or []
            if not batch:
                break
            out.extend(batch)
            time.sleep(TRAKT_SLEEP_S)
        except Exception as e:                           # noqa: BLE001
            logger.warning('Trakt fetch failed %s page %s: %s', path, page, type(e).__name__)
            break
    return out


def _normalize_trakt_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    movie = entry.get('movie') or entry
    if not isinstance(movie, dict):
        return None
    ids = movie.get('ids') or {}
    tmdb_id = ids.get('tmdb')
    if not tmdb_id:
        return None
    try:
        tmdb_id = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    genres = [TRAKT_GENRES[g] for g in (movie.get('genres') or []) if isinstance(g, str) and g in TRAKT_GENRES]
    return {
        'tmdb_id': tmdb_id,
        'trakt_id': ids.get('trakt'),
        'imdb_id': ids.get('imdb') or None,
        'title': movie.get('title') or '',
        'year': movie.get('year'),
        'overview': movie.get('overview') or '',
        'trakt_rating': movie.get('rating'),
        'trakt_votes': movie.get('votes') or 0,
        'watchers': entry.get('watchers') or entry.get('watcher_count') or 0,
        'genre_ids': genres,
        'media_type': 'movie',
        'owned': False,
        'source': 'trakt',
    }


def _fetch_tmdb_list(api_key: str, path: str, pages: int, extra: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """TMDb paginated list/discover → normalized candidate dicts."""
    out: List[Dict[str, Any]] = []
    if not api_key:
        return out
    for page in range(1, pages + 1):
        params: Dict[str, Any] = {'api_key': api_key, 'page': page, 'language': 'en-US'}
        if extra:
            params.update(extra)
        try:
            resp = http_get(f'https://api.themoviedb.org/3{path}', params, None, TMDB_TIMEOUT_S)
            if not resp.ok:
                logger.warning('TMDb %s page %s -> %s', path, page, resp.status_code)
                break
            data = resp.json() or {}
            results = data.get('results') or []
            if not results:
                break
            for m in results:
                tid = m.get('id')
                if not tid:
                    continue
                out.append({
                    'tmdb_id': int(tid),
                    'trakt_id': None,
                    'imdb_id': None,
                    'title': m.get('title') or '',
                    'year': int(str(m.get('release_date') or '0')[:4]) if m.get('release_date') else None,
                    'overview': m.get('overview') or '',
                    'trakt_rating': None,
                    'trakt_votes': 0,
                    'watchers': 0,
                    'poster_path': m.get('poster_path'),
                    'backdrop_path': m.get('backdrop_path'),
                    'original_language': (m.get('original_language') or '').lower(),
                    'tmdb_vote_count': m.get('vote_count') or 0,
                    'tmdb_vote_average': m.get('vote_average'),
                    'genre_ids': [int(g) for g in (m.get('genre_ids') or []) if isinstance(g, int)],
                    'adult': bool(m.get('adult')),
                    'media_type': 'movie',
                    'owned': False,
                    'source': 'tmdb',
                })
            if int(data.get('total_pages') or page) <= page:
                break
            time.sleep(TMDB_SLEEP_S)
        except Exception as e:                           # noqa: BLE001
            logger.warning('TMDb fetch failed %s page %s: %s', path, page, type(e).__name__)
            break
    return out


# ------------------------------------------------------------------ ownership

def _owned(cached_only: bool = False) -> Dict[str, Any]:
    """{'ids', 'title_years', 'plex_ok'}: every Plex movie section, plus in-flight grabs and the watchlist."""
    ids: Set[int] = set()
    tys: Set[Tuple[str, int]] = set()
    try:
        index = plex_index(cached_only) or {}
    except Exception as e:                               # noqa: BLE001
        logger.warning("What you're missing: Plex ownership unavailable: %s", type(e).__name__)
        index = {}

    def add(e: Any) -> None:
        if not isinstance(e, dict):
            return
        tid = e.get('tmdb_id')
        if tid is not None and str(tid).isdigit():
            ids.add(int(tid))
        if e.get('title') and str(e.get('year') or '').isdigit():
            tys.add((_norm(e['title']), int(e['year'])))

    for tid, e in (index.get('movies_by_tmdb') or {}).items():
        if str(tid).isdigit():
            ids.add(int(tid))
        add(e)
    for e in (index.get('movies') or {}).values():
        add(e)
    for entries in (index.get('movies_by_title') or {}).values():
        for e in entries or []:
            add(e)
    plex_ok = bool(ids or tys)
    for name, fn in (('in-flight grabs', in_flight), ('watchlist', watchlist_ids)):
        try:
            ids |= set(fn() or ())
        except Exception as e:                           # noqa: BLE001
            logger.warning("What you're missing: %s unreadable: %s", name, type(e).__name__)
    return {'ids': ids, 'title_years': tys, 'plex_ok': plex_ok}


def _is_owned(item: Dict[str, Any], own: Dict[str, Any]) -> bool:
    tid = item.get('tmdb_id')
    if tid is not None and int(tid) in own['ids']:
        return True
    y = item.get('year')
    return bool(own['title_years']) and isinstance(y, int) and (_norm(item.get('title') or ''), y) in own['title_years']


# ------------------------------------------------------------------ enrichment

def _tmdb_enrich(candidates: List[Dict[str, Any]], api_key: str) -> None:
    if not api_key or not candidates:
        return

    def one(item: Dict[str, Any]) -> None:
        tid = item.get('tmdb_id')
        if not tid:
            return
        try:
            resp = http_get(f'https://api.themoviedb.org/3/movie/{tid}', {'api_key': api_key}, None, TMDB_DETAIL_TIMEOUT_S)
            if not resp.ok:
                return
            data = resp.json() or {}
            if not item.get('poster_path'):
                item['poster_path'] = data.get('poster_path')
            if not item.get('backdrop_path'):
                item['backdrop_path'] = data.get('backdrop_path')
            lang = (data.get('original_language') or '').lower()
            if lang:
                item['original_language'] = lang
            item['tmdb_vote_count'] = max(item.get('tmdb_vote_count') or 0, data.get('vote_count') or 0)
            if data.get('vote_average') is not None:
                item['tmdb_vote_average'] = data.get('vote_average')
            if not item.get('overview'):
                item['overview'] = data.get('overview') or ''
            if not item.get('year') and data.get('release_date'):
                try:
                    item['year'] = int(str(data['release_date'])[:4])
                except ValueError:
                    pass
            if data.get('imdb_id') and not item.get('imdb_id'):
                item['imdb_id'] = data.get('imdb_id')
            g = [x.get('id') for x in (data.get('genres') or []) if isinstance(x, dict) and isinstance(x.get('id'), int)]
            if g:
                item['genre_ids'] = g
            if 'adult' in data:
                item['adult'] = bool(data.get('adult'))
        except Exception:                                # noqa: BLE001
            pass

    # Only items missing critical fields (Trakt-sourced, or list rows without an IMDb id)
    need = [c for c in candidates if not c.get('poster_path') or not c.get('original_language') or not c.get('imdb_id')]
    if not need:
        return
    with ThreadPoolExecutor(max_workers=TMDB_WORKERS) as pool:
        list(pool.map(one, need))


def _imdb_enrich(candidates: List[Dict[str, Any]]) -> None:
    """IMDb rating and votes from OMDb (only with an OMDb key; capped per build, fail open)."""
    if not omdb_on():
        for c in candidates:
            c['imdb_rating'] = None
            c['imdb_votes'] = None
        return
    targets = [c for c in candidates if c.get('imdb_id')][:OMDB_MAX]

    def one(item: Dict[str, Any]) -> None:
        try:
            r = omdb_ratings(str(item['imdb_id'])) or {}
        except Exception:                                # noqa: BLE001
            r = {}
        item['imdb_rating'] = r.get('rating')
        item['imdb_votes'] = r.get('votes')
    with ThreadPoolExecutor(max_workers=OMDB_WORKERS) as pool:
        list(pool.map(one, targets))
    for c in candidates:
        c.setdefault('imdb_rating', None)
        c.setdefault('imdb_votes', None)


# ------------------------------------------------------------------ filters (pool and slate)

_NON_LATIN_RE = re.compile(
    r'[\u0400-\u04FF\u0500-\u052F'  # Cyrillic
    r'\u0600-\u06FF'  # Arabic
    r'\u0900-\u097F'  # Devanagari
    r'\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF'  # CJK / Hangul
    r'\u0E00-\u0E7F]'  # Thai
)


def _title_has_strong_non_latin(title: str) -> bool:
    """True if title is dominated by non-Latin script (skip for a Latin-script shelf)."""
    if not title:
        return False
    hits = _NON_LATIN_RE.findall(title)
    if not hits:
        return False
    non_latin = sum(len(h) for h in hits)
    letters = sum(1 for ch in title if ch.isalpha())
    if non_latin >= 3:
        return True
    return letters > 0 and (non_latin / letters) >= 0.4


def _coerce_imdb_votes(raw: Any) -> Optional[int]:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    s = str(raw).strip().replace(',', '')
    if not s or s.upper() in ('N/A', 'NA', 'NONE', 'NULL'):
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _int0(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _year_ok(item: Dict[str, Any], cfg: Dict[str, Any], today: Optional[date] = None) -> bool:
    """Year window (Earliest year .. Latest year); an unknown year is allowed (enrich may fill it)."""
    y = item.get('year')
    try:
        y = int(y) if y is not None else None
    except (TypeError, ValueError):
        y = None
    if y is None:
        return True
    return int(cfg['year_from']) <= y <= wym_store.year_to(cfg, today)


def _crowd_ok(item: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    """Prod's pool gate (:385-428) with its numbers as settings: reject a film with too few IMDb votes (only
    known with an OMDb key); otherwise any one crowd signal is enough: IMDb votes, TMDb votes (the minimum,
    or a third of it as prod's soft tier), Trakt watchers or Trakt votes."""
    votes = _coerce_imdb_votes(item.get('imdb_votes'))
    if votes is not None and votes < int(cfg['min_imdb_votes']):
        return False
    if votes is not None and votes >= max(IMDB_SOFT_VOTES, int(cfg['min_imdb_votes'])):
        return True
    tmdb_min = int(cfg['min_tmdb_votes'])
    if _int0(item.get('tmdb_vote_count')) >= min(tmdb_min, math.ceil(tmdb_min / TMDB_SOFT_SHARE)):
        return True
    w = int(cfg['min_trakt_watchers'])
    if w > 0 and (_int0(item.get('watchers')) >= w or _int0(item.get('trakt_votes')) >= w * TRAKT_VOTES_PER_WATCHER):
        return True
    return False


def keep(item: Dict[str, Any], cfg: Dict[str, Any], today: Optional[date] = None) -> bool:
    """Every filter setting, for the pool and again for the slate (so a change applies at once)."""
    return basic_ok(item, cfg, today) and _crowd_ok(item, cfg)


def basic_ok(item: Dict[str, Any], cfg: Dict[str, Any], today: Optional[date] = None) -> bool:
    """Poster, language (and prod's Latin-script shelf), years, adult and excluded genres (prod :575-581)."""
    if cfg['require_poster'] and not item.get('poster_path'):
        return False
    langs = cfg['languages']
    lang = (item.get('original_language') or '').lower()
    if (lang or 'en') not in langs:
        return False
    if all(x in wym_store.LATIN_LANGUAGES for x in langs) and _title_has_strong_non_latin(item.get('title') or ''):
        return False
    if not _year_ok(item, cfg, today):
        return False
    if not cfg['include_adult'] and item.get('adult'):
        return False
    out = {wym_store.GENRES[g] for g in cfg['exclude_genres']}
    return not (out and out & set(item.get('genre_ids') or []))


def _merge_candidate(by_tmdb: Dict[int, Dict[str, Any]], item: Dict[str, Any], own: Dict[str, Any]) -> None:
    tid = item.get('tmdb_id')
    if not tid or _is_owned(item, own):
        return
    prev = by_tmdb.get(tid)
    if not prev:
        by_tmdb[tid] = item
        return
    prev['watchers'] = max(prev.get('watchers') or 0, item.get('watchers') or 0)
    prev['trakt_votes'] = max(prev.get('trakt_votes') or 0, item.get('trakt_votes') or 0)
    prev['tmdb_vote_count'] = max(prev.get('tmdb_vote_count') or 0, item.get('tmdb_vote_count') or 0)
    for k in ('poster_path', 'backdrop_path', 'imdb_id', 'overview', 'original_language', 'year', 'genre_ids'):
        if not prev.get(k) and item.get(k):
            prev[k] = item[k]
    if item.get('adult'):
        prev['adult'] = True


# ------------------------------------------------------------------ the pool

class PoolError(Exception):
    pass


def sources_sig() -> Dict[str, bool]:
    return {'trakt': bool(trakt_client_id()), 'tmdb': bool(tmdb_key()), 'omdb': omdb_on()}


def current_sig(cfg: Optional[Dict[str, Any]] = None) -> str:
    return wym_store.pool_sig(cfg or wym_store.settings(), sources_sig())


def tmdb_queries(cfg: Dict[str, Any], today: Optional[date] = None, expand: bool = False) -> List[Tuple[str, int, Optional[Dict[str, Any]]]]:
    """The TMDb lists the settings switch on (prod :493-562; the expand pass :631-646)."""
    on = set(cfg['tmdb_lists'])
    v = int(cfg['min_tmdb_votes'])
    yf, yt = int(cfg['year_from']), wym_store.year_to(cfg, today)
    base = {'include_adult': 'true' if cfg['include_adult'] else 'false',
            'with_original_language': '|'.join(cfg['languages'])}
    q: List[Tuple[str, int, Optional[Dict[str, Any]]]] = []
    if expand:
        vv = max(EXPAND_MIN_VOTES, v // 2)
        if 'popular' in on:
            q.append(('/movie/popular', TMDB_POPULAR_PAGES + 20, None))
        if 'top_rated' in on:
            q.append(('/movie/top_rated', TMDB_TOP_RATED_PAGES + 15, None))
        if 'most_voted' in on:
            q.append(('/discover/movie', TMDB_DISCOVER_PAGES + 30, {'sort_by': 'vote_count.desc', **base, 'vote_count.gte': vv}))
        if 'most_popular' in on:
            q.append(('/discover/movie', 25, {'sort_by': 'popularity.desc', **base, 'vote_count.gte': vv}))
        return q
    if 'popular' in on:
        q.append(('/movie/popular', TMDB_POPULAR_PAGES, None))
    if 'top_rated' in on:
        q.append(('/movie/top_rated', TMDB_TOP_RATED_PAGES, None))
    if 'most_voted' in on:
        q.append(('/discover/movie', TMDB_DISCOVER_PAGES, {'sort_by': 'vote_count.desc', **base, 'vote_count.gte': v}))
    if 'most_popular' in on:
        q.append(('/discover/movie', max(20, TMDB_DISCOVER_PAGES // 2), {'sort_by': 'popularity.desc', **base, 'vote_count.gte': v}))
    if 'highest_grossing' in on:
        q.append(('/discover/movie', max(15, TMDB_DISCOVER_PAGES // 3), {'sort_by': 'revenue.desc', **base, 'vote_count.gte': v}))
    if 'highest_rated' in on:
        q.append(('/discover/movie', max(15, TMDB_DISCOVER_PAGES // 3),
                  {'sort_by': 'vote_average.desc', **base, 'vote_count.gte': max(v, HIGHEST_RATED_VOTES)}))
    if 'by_genre' in on:
        for name, gid in wym_store.GENRES.items():
            if name in cfg['exclude_genres']:
                continue
            q.append(('/discover/movie', GENRE_PAGES, {
                'sort_by': 'vote_count.desc', **base, 'vote_count.gte': v, 'with_genres': str(gid),
                'primary_release_date.gte': f'{yf}-01-01', 'primary_release_date.lte': f'{yt}-12-31'}))
    if 'by_year' in on:
        for y0 in range(yf, yt + 1, 2):
            y1 = min(y0 + 1, yt)
            window = {'primary_release_date.gte': f'{y0}-01-01', 'primary_release_date.lte': f'{y1}-12-31'}
            q.append(('/discover/movie', YEAR_VOTE_PAGES, {'sort_by': 'vote_count.desc', **base, 'vote_count.gte': v, **window}))
            q.append(('/discover/movie', YEAR_POP_PAGES, {'sort_by': 'popularity.desc', **base,
                                                          'vote_count.gte': max(50, v // 2), **window}))
    return q


def trakt_sources(cfg: Dict[str, Any], expand: bool = False) -> List[Tuple[str, int]]:
    on = cfg['trakt_lists']
    if expand:
        return [(TRAKT_PATHS[k], TRAKT_PAGES[k] + n) for k, n in TRAKT_EXPAND.items() if k in on]
    return [(TRAKT_PATHS[k], TRAKT_PAGES[k]) for k in wym_store.TRAKT_LISTS if k in on]


def _collect(cfg: Dict[str, Any], own: Dict[str, Any], client_id: str, key: str, expand: bool,
             into: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[int, Dict[str, Any]]:
    by_tmdb: Dict[int, Dict[str, Any]] = into if into is not None else {}
    if client_id:
        for path, pages in trakt_sources(cfg, expand):
            raw = _fetch_trakt_list(path, client_id, pages)
            logger.info("What you're missing: Trakt %s → %d raw", path, len(raw))
            for entry in raw:
                norm = _normalize_trakt_entry(entry)
                if norm:
                    _merge_candidate(by_tmdb, norm, own)
    if key:
        for path, pages, extra in tmdb_queries(cfg, expand=expand):
            raw = _fetch_tmdb_list(key, path, pages, extra)
            for item in raw:
                _merge_candidate(by_tmdb, item, own)
    return by_tmdb


def _gate(cands: List[Dict[str, Any]], cfg: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    _tmdb_enrich(cands, key)
    pre = [c for c in cands if basic_ok(c, cfg)]
    _imdb_enrich(pre)
    gated = [c for c in pre if keep(c, cfg)]
    gated.sort(key=lambda c: (_coerce_imdb_votes(c.get('imdb_votes')) or 0, c.get('tmdb_vote_count') or 0,
                              c.get('watchers') or 0), reverse=True)
    return gated[:POOL_TARGET]


def build_pool(force: bool = False) -> Dict[str, Any]:
    """Rebuild the unowned-popular candidate pool and persist it. Raises PoolError when it can't (the old
    pool stays)."""
    global _mem_pool, _mem_key, _mem_seed_ids
    cfg = wym_store.settings()
    client_id, key = trakt_client_id(), tmdb_key()
    sig = wym_store.pool_sig(cfg, sources_sig())
    if not force:
        cached = _read_pool()
        if cached and cached.get('sig') == sig and (time.time() - float(cached.get('built_at') or 0)) < int(cfg['rebuild_hours']) * 3600:
            return cached
    if not client_id and not key:
        raise PoolError('No TMDb key or Trakt client ID')
    own = _owned()
    if not own['plex_ok']:
        raise PoolError("Can't reach Plex")
    logger.info("What you're missing: building the pool (owned or grabbing: %d)", len(own['ids']))
    by_tmdb = _collect(cfg, own, client_id, key, expand=False)
    gated = _gate(list(by_tmdb.values()), cfg, key)
    if len(gated) < POOL_MIN_HEALTHY and key:
        logger.warning("What you're missing: pool only %d (< %d), digging deeper once", len(gated), POOL_MIN_HEALTHY)
        extra = _collect(cfg, own, client_id, key, expand=True, into={int(c['tmdb_id']): c for c in gated})
        gated = _gate(list(extra.values()), cfg, key)
    now = time.time()
    payload = {
        'built_at': now,
        'built_at_iso': datetime.fromtimestamp(now, NY).isoformat(),
        'sig': sig,
        'owned_excluded': len(own['ids']),
        'candidates': gated,
    }
    _write_pool(payload)
    with _lock:
        _mem_pool = payload
        _mem_key = None
        _mem_seed_ids = []
    logger.info("What you're missing: pool ready — %d candidates", len(gated))
    return payload


def _write_pool(payload: Dict[str, Any]) -> None:
    path = _pool_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(path)


def _read_pool() -> Optional[Dict[str, Any]]:
    global _mem_pool
    with _lock:
        if _mem_pool and _mem_pool.get('candidates') is not None:
            return _mem_pool
    path = _pool_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            with _lock:
                _mem_pool = data
            return data
    except Exception as e:                               # noqa: BLE001
        logger.warning("What you're missing: pool read failed: %s", type(e).__name__)
    return None


def _forget_memory() -> None:
    """Tests: drop the in-memory pool and slate."""
    global _mem_pool, _mem_key, _mem_seed_ids
    with _lock:
        _mem_pool, _mem_key, _mem_seed_ids = None, None, []


# ------------------------------------------------------------------ scheduler

def rail_on() -> bool:
    """The one switch is Home screen settings (home_rails' show/hide); the pool idles while it is hidden."""
    try:
        from app import home_rails
        return home_rails.WYM_ID not in set(home_rails.load_prefs().get('hidden') or [])
    except Exception:                                    # noqa: BLE001
        return True


def _backoff_until(now: float) -> float:
    if _state['last_error'] and wym_store.changed_at() <= _state['last_attempt']:
        return _state['last_attempt'] + RETRY_S
    return 0.0


def next_rebuild(cfg: Optional[Dict[str, Any]] = None, pool: Optional[Dict[str, Any]] = None,
                 now: Optional[float] = None) -> Optional[float]:
    """When the scheduler rebuilds next (epoch seconds), None while off or building."""
    cfg = cfg or wym_store.settings()
    if not rail_on() or _state['building']:
        return None
    now = time.time() if now is None else now
    pool = _read_pool() if pool is None else pool
    hold = _backoff_until(now)
    if not pool or pool.get('sig') != current_sig(cfg):
        return max(now, wym_store.changed_at() + SETTLE_S, hold)
    return max(float(pool.get('built_at') or 0) + int(cfg['rebuild_hours']) * 3600, hold)


def due(now: Optional[float] = None) -> bool:
    nxt = next_rebuild(now=now)
    return nxt is not None and nxt <= (time.time() if now is None else now)


def rebuild(force: bool = True) -> Optional[Dict[str, Any]]:
    """One rebuild (single flight). Records the outcome for the status; never raises."""
    if not _build_lock.acquire(blocking=False):
        return None
    _state['building'] = True
    _state['last_attempt'] = time.time()
    try:
        payload = build_pool(force=force)
        _state['last_error'] = None
        return payload
    except PoolError as e:
        _state['last_error'] = str(e)
        logger.warning("What you're missing: rebuild skipped: %s", e)
    except Exception as e:                               # noqa: BLE001
        _state['last_error'] = "Couldn't rebuild"
        logger.error("What you're missing: rebuild failed: %s", type(e).__name__)
    finally:
        _state['building'] = False
        _build_lock.release()
    return None


def rebuild_async() -> bool:
    """Refresh now: start a rebuild in the background. False when one is already running."""
    if _state['building'] or _build_lock.locked():
        return False
    _start['fn'](lambda: rebuild(force=True))
    return True


def tick(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Rebuild if due. Returns the new pool, or None."""
    if not due(now):
        return None
    return rebuild(force=True)


def sleep_s(now: Optional[float] = None) -> float:
    try:
        nxt = next_rebuild(now=now)
        if nxt is not None:
            delta = nxt - (time.time() if now is None else now)
            if 0 < delta < CHECK_S:
                return delta + 1
    except Exception:                                    # noqa: BLE001
        logger.exception("What you're missing: next rebuild unknown")
    return CHECK_S


def wake() -> None:
    """A settings change or Refresh now: look again now instead of at the next 5-minute check."""
    _wake.set()


def status(now: Optional[float] = None) -> Dict[str, Any]:
    cfg = wym_store.settings()
    pool = _read_pool()
    nxt = next_rebuild(cfg, pool, now)
    built = float(pool.get('built_at') or 0) if pool else 0.0
    return {
        'on_home': rail_on(),
        'building': bool(_state['building']),
        'last_rebuilt': datetime.fromtimestamp(built, NY).isoformat() if built else None,
        'pool_size': len(pool.get('candidates') or []) if pool else 0,
        'next_rebuild': datetime.fromtimestamp(nxt, NY).isoformat() if nxt else None,
        'error': _state['last_error'],
    }


def start_daily_missing_thread() -> bool:
    """The pool refresher. Runs on every instance, minimal builds included: it only reads Plex, Trakt,
    TMDb and OMDb and writes its own JSON in the data dir (instance.DATA_DIR_ONLY)."""
    global _thread_started
    from app import instance
    if _thread_started or not instance.data_dir_only('daily_missing'):
        return False
    _thread_started = True

    def loop():
        time.sleep(STARTUP_DELAY_S)
        while True:
            try:
                tick()
            except Exception:                            # noqa: BLE001
                logger.exception("What you're missing: scheduler pass failed")
            _wake.wait(sleep_s())
            _wake.clear()

    threading.Thread(target=loop, name='daily-missing-pool', daemon=True).start()
    logger.info("What you're missing: pool refresher started (every %sh, Settings > Automation)",
                wym_store.settings()['rebuild_hours'])
    return True


def refresh_now() -> Dict[str, Any]:
    """Prod's synchronous rebuild (full builds' POST /refresh)."""
    return build_pool(force=True)


# ------------------------------------------------------------------ dismissed

def _read_dismissed_doc() -> Dict[str, Any]:
    path = _dismissed_path()
    if not path.exists():
        return {'tmdb_ids': [], 'items': {}}
    try:
        data = json.loads(path.read_text())
    except Exception as e:                               # noqa: BLE001
        logger.warning("What you're missing: dismissed read failed: %s", type(e).__name__)
        return {'tmdb_ids': [], 'items': {}}
    if isinstance(data, list):                           # a bare list of ids
        data = {'tmdb_ids': data}
    if not isinstance(data, dict):
        return {'tmdb_ids': [], 'items': {}}
    items = data.get('items') if isinstance(data.get('items'), dict) else {}
    return {'tmdb_ids': data.get('tmdb_ids') or [], 'items': items}


def _read_dismissed() -> Set[int]:
    """Permanent tmdb_id dismissals (survive restarts/rotation)."""
    out: Set[int] = set()
    for x in _read_dismissed_doc()['tmdb_ids']:
        try:
            tid = int(x)
        except (TypeError, ValueError):
            continue
        if tid > 0:
            out.add(tid)
    return out


def _write_dismissed(ids: Set[int], items: Dict[str, Any]) -> None:
    path = _dismissed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'tmdb_ids': sorted(int(x) for x in ids),
        'items': {k: v for k, v in items.items() if k.isdigit() and int(k) in ids},
        'updated_at_iso': datetime.now(NY).isoformat(),
    }
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, indent=2) + chr(10))
    tmp.replace(path)


def _pool_item(tid: int) -> Optional[Dict[str, Any]]:
    pool = _read_pool() or {}
    return next((c for c in pool.get('candidates') or [] if c.get('tmdb_id') == tid), None)


def dismiss(tmdb_id: int) -> Dict[str, Any]:
    """Permanently hide a title from What you're missing by tmdb_id."""
    global _mem_seed_ids
    tid = int(tmdb_id)
    if tid <= 0:
        raise ValueError('tmdb_id must be a positive int')
    with _lock:
        doc = _read_dismissed_doc()
        ids = _read_dismissed()
        already = tid in ids
        ids.add(tid)
        items = dict(doc['items'])
        meta = _pool_item(tid) or {}
        items.setdefault(str(tid), {'title': meta.get('title') or '', 'year': meta.get('year'),
                                    'poster_path': meta.get('poster_path'), 'at': time.time()})
        _write_dismissed(ids, items)
        if _mem_seed_ids:
            _mem_seed_ids = [x for x in _mem_seed_ids if x != tid]
    logger.info("What you're missing: dismissed tmdb_id=%s (already=%s, total=%d)", tid, already, len(ids))
    return {'ok': True, 'tmdb_id': tid, 'already': already, 'dismissed_count': len(ids)}


def restore(tmdb_id: int) -> Dict[str, Any]:
    """Undo a dismissal (Settings > Automation > Dismissed movies)."""
    global _mem_key
    tid = int(tmdb_id)
    if tid <= 0:
        raise ValueError('tmdb_id must be a positive int')
    with _lock:
        doc = _read_dismissed_doc()
        ids = _read_dismissed()
        was = tid in ids
        ids.discard(tid)
        items = {k: v for k, v in doc['items'].items() if k != str(tid)}
        _write_dismissed(ids, items)
        _mem_key = None                                  # it may come back in today's slate
    return {'ok': True, 'tmdb_id': tid, 'restored': was, 'dismissed_count': len(ids)}


def tmdb_title(tid: int) -> Dict[str, Any]:
    """Title, year and poster of a dismissed film the pool no longer has (TMDb)."""
    key = tmdb_key()
    if not key:
        return {}
    try:
        r = http_get(f'https://api.themoviedb.org/3/movie/{tid}', {'api_key': key}, None, TMDB_DETAIL_TIMEOUT_S)
        d = r.json() if r.ok else {}
    except Exception:                                    # noqa: BLE001
        return {}
    rd = str(d.get('release_date') or '')
    return {'title': d.get('title') or '', 'year': int(rd[:4]) if rd[:4].isdigit() else None,
            'poster_path': d.get('poster_path')}


def list_dismissed(lookups: int = 20) -> Dict[str, Any]:
    """Dismissed films, newest first, with title, year and poster."""
    doc = _read_dismissed_doc()
    ids = _read_dismissed()
    out = []
    for tid in ids:
        meta = dict(doc['items'].get(str(tid)) or {})
        if not meta.get('title'):
            p = _pool_item(tid)
            if p:
                meta.update(title=p.get('title'), year=p.get('year'), poster_path=p.get('poster_path'))
            elif lookups > 0:
                lookups -= 1
                meta.update(tmdb_title(tid))
        out.append({'tmdb_id': tid, 'title': meta.get('title') or '', 'year': meta.get('year'),
                    'poster_path': meta.get('poster_path'), 'at': meta.get('at') or 0})
    out.sort(key=lambda m: (-(m['at'] or 0), m['tmdb_id']))
    return {'tmdb_ids': sorted(ids), 'count': len(ids), 'items': out}


# ------------------------------------------------------------------ served history and the slate

def _read_served_history() -> Dict[str, List[int]]:
    """NY-day -> list of tmdb_ids shown that day (persisted across restarts)."""
    path = _served_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        days = (data.get('days') if isinstance(data, dict) else None) or {}
        out: Dict[str, List[int]] = {}
        for day, ids in days.items():
            cleaned = [int(x) for x in (ids or []) if str(x).isdigit() and int(x) > 0]
            if cleaned:
                out[str(day)] = cleaned
        return out
    except Exception as e:                               # noqa: BLE001
        logger.warning("What you're missing: served history read failed: %s", type(e).__name__)
        return {}


def _write_served_history(days: Dict[str, List[int]]) -> None:
    path = _served_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        keep_after = (datetime.now(NY).date() - timedelta(days=SERVED_HISTORY_KEEP_DAYS)).isoformat()
        pruned = {d: ids for d, ids in days.items() if d >= keep_after}
        payload = {'days': pruned, 'updated_at_iso': datetime.now(NY).isoformat()}
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, indent=2) + chr(10))
        tmp.replace(path)
    except Exception as e:                               # noqa: BLE001
        logger.error("What you're missing: served history write failed: %s", type(e).__name__)


def _recent_served_ids(before_day: str, n_days: int) -> Set[int]:
    """Union of served ids for the n NY calendar days strictly before before_day."""
    if n_days <= 0:
        return set()
    try:
        anchor = datetime.strptime(before_day, '%Y-%m-%d').date()
    except ValueError:
        return set()
    wanted = {(anchor - timedelta(days=i)).isoformat() for i in range(1, n_days + 1)}
    hist = _read_served_history()
    return {int(t) for day in wanted for t in hist.get(day) or []}


def _record_served(day: str, ids: List[int]) -> None:
    if not ids:
        return
    with _lock:
        hist = _read_served_history()
        if hist.get(day) == [int(x) for x in ids]:
            return
        hist[day] = [int(x) for x in ids]
        _write_served_history(hist)


def _day_seed(day: str) -> int:
    digest = hashlib.sha256(f'what_youre_missing:{day}'.encode()).hexdigest()
    return int(digest[:16], 16)


def _daily_slice(candidates: List[Dict[str, Any]], day: str, banned: Set[int], limit: int,
                 exclude_ids: Optional[Set[int]] = None) -> List[Dict[str, Any]]:
    """Date-seeded shuffle. Prefer titles not in exclude_ids; fill from excluded if short."""
    exclude = set(exclude_ids or set()) - banned
    primary = [c for c in candidates if int(c.get('tmdb_id') or 0) not in banned | exclude]
    secondary = [c for c in candidates if int(c.get('tmdb_id') or 0) in exclude]
    rng = random.Random(_day_seed(day))
    order = list(primary)
    rng.shuffle(order)
    if len(order) < limit and secondary:
        fill = list(secondary)
        rng.shuffle(fill)
        order.extend(fill)
    return order[:limit]


def _empty(day: str, error: Optional[str] = None) -> Dict[str, Any]:
    out = {'rail_id': RAIL_ID, 'title': TITLE, 'day': day, 'movies': [], 'enabled': True, 'empty': True}
    if error:
        out['error'] = error
    return out


def kick() -> None:
    """No pool yet: build one in the background when the schedule allows (never inside a request)."""
    try:
        if due():
            rebuild_async()
    except Exception:                                    # noqa: BLE001
        logger.exception("What you're missing: background build not started")


def get_rail(limit: Optional[int] = None, refresh_owned: bool = True, cached_owned: bool = False) -> Dict[str, Any]:
    """Today's What you're missing slate (TMDb-shaped, owned:false)."""
    global _mem_key, _mem_seed_ids
    cfg = wym_store.settings()
    limit = int(limit or cfg['movies_shown'])
    day = _today_ny()
    if not rail_on():
        return {**_empty(day), 'enabled': False}
    pool = _read_pool()
    if not pool or not pool.get('candidates'):
        kick()
        return _empty(day)

    candidates = [c for c in pool.get('candidates') or [] if c.get('tmdb_id') and keep(c, cfg)]
    own = _owned(cached_only=cached_owned) if refresh_owned else {'ids': set(), 'title_years': set()}
    dismissed = _read_dismissed()
    banned = {int(c['tmdb_id']) for c in candidates if _is_owned(c, own)} | dismissed
    by_id = {int(c['tmdb_id']): c for c in candidates}
    exclude_ids = _recent_served_ids(day, int(cfg['no_repeat_days']))
    slate_key = (day, pool.get('built_at'), current_slate_sig(cfg), tuple(sorted(exclude_ids)))

    with _lock:
        if _mem_key != slate_key or not _mem_seed_ids:
            seeded = _daily_slice(candidates, day, banned, limit=len(candidates), exclude_ids=exclude_ids)
            _mem_seed_ids = [int(m['tmdb_id']) for m in seeded]
            _mem_key = slate_key
        movies: List[Dict[str, Any]] = []
        for tid in _mem_seed_ids:
            if tid in banned or tid not in by_id:
                continue
            movies.append(dict(by_id[tid]))
            if len(movies) >= limit:
                break

    try:
        _record_served(day, [int(m['tmdb_id']) for m in movies])
    except Exception as e:                               # noqa: BLE001
        logger.warning("What you're missing: record served failed: %s", type(e).__name__)

    for m in movies:
        m['owned'] = False
        m['media_type'] = 'movie'
        if m.get('imdb_rating') is not None:
            m['rating'] = m['imdb_rating']

    return {
        'rail_id': RAIL_ID,
        'title': TITLE,
        'subtitle': f"Popular blind spots · refreshes {day}",
        'day': day,
        'movies': movies,
        'enabled': True,
        'empty': len(movies) == 0,
        'pool_size': len(pool.get('candidates') or []),
        'dismissed_count': len(dismissed),
        'excluded_recent': len(exclude_ids),
        'built_at_iso': pool.get('built_at_iso'),
    }


def current_slate_sig(cfg: Dict[str, Any]) -> str:
    """The settings the slate applies itself (so a change re-deals today's order)."""
    return wym_store.pool_sig(cfg, {'no_repeat_days': cfg['no_repeat_days']})
