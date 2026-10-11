"""What You're Missing — daily home rail of popular movies the library does not own.

Discovery only (NOT an auto-grabber). Builds a large unowned-popular pool from
Trakt + TMDb, quality-gates for crowd signal, and serves a ~20-title slate that is
stable for the America/New_York calendar day and reshuffles the next morning.

A 4.7k owned library starves shallow popular lists — dig deep across Trakt/TMDb
and keep POOL_MIN_HEALTHY large enough that consecutive NY days can look
different. Primary daily slice also excludes recently served ids (last N NY
days), filling from the remainder only if needed.

Pool gate is intentionally soft (year 1985–2025, English+poster, modest
TMDb/IMDb signal) so the shelf has hundreds/thousands to rotate through.
"""
from __future__ import annotations

import hashlib
import re
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

NY = ZoneInfo('America/New_York')

RAIL_ID = 'what_youre_missing'
RAIL_SIZE = 20
POOL_TARGET = 4000
# Below this, treat pool as unhealthy (tiny pools reshuffle to ~same slate).
POOL_MIN_HEALTHY = 400
TRAKT_PAGE_SIZE = 20
# A large library (~5k films) owns nearly all of the top Trakt pages. Dig deep.
TRAKT_POPULAR_PAGES = 40
TRAKT_WATCHED_PAGES = 20
TRAKT_ANTICIPATED_PAGES = 15
TRAKT_FAVORITED_PAGES = 15
TRAKT_BOXOFFICE_PAGES = 1
TRAKT_TRENDING_PAGES = 10
TRAKT_WATCHED_ALL_PAGES = 15
TRAKT_FAVORITED_ALL_PAGES = 15
IMDB_MIN_VOTES = 5_000  # pool soft prefer; not a hard starve bar
IMDB_HARD_REJECT = 1_000  # known tiny-vote junk only; pool must stay huge
TMDB_VOTE_FALLBACK = 150  # pool inclusion: modest crowd signal + poster/English/year
POOL_MAX_AGE_SEC = 8 * 3600
REFRESH_INTERVAL_SEC = 8 * 3600
ENGLISH = 'en'
YEAR_MIN = 1985
YEAR_MAX = 2025  # through last complete-ish year; skip unreleased future
TMDB_DISCOVER_PAGES = 45
TMDB_POPULAR_PAGES = 40
TMDB_TOP_RATED_PAGES = 25
# How many prior NY calendar days' served ids to exclude from today's primary pick.
EXCLUDE_RECENT_DAYS = 3
SERVED_HISTORY_KEEP_DAYS = 14

_lock = threading.RLock()
_thread_started = False
_mem_pool: Optional[Dict[str, Any]] = None
_mem_day: Optional[str] = None
_mem_seed_ids: List[int] = []


def _data_dir() -> Path:
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent
    return Path(__file__).resolve().parent.parent / 'data'


def _pool_path() -> Path:
    return _data_dir() / 'what_youre_missing_pool.json'


def _dismissed_path() -> Path:
    return _data_dir() / 'what_youre_missing_dismissed.json'


def _served_path() -> Path:
    return _data_dir() / 'what_youre_missing_served.json'


def _today_ny() -> str:
    return datetime.now(NY).strftime('%Y-%m-%d')


def _settings():
    from app.settings_manager import SettingsManager
    return SettingsManager()


def _trakt_headers(client_id: str) -> Dict[str, str]:
    return {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': client_id,
    }


def _fetch_trakt_list(path: str, client_id: str, pages: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in range(1, pages + 1):
        try:
            resp = requests.get(
                f'https://api.trakt.tv{path}',
                headers=_trakt_headers(client_id),
                params={'page': page, 'limit': TRAKT_PAGE_SIZE, 'extended': 'full'},
                timeout=15,
            )
            if resp.status_code == 429:
                retry = int(resp.headers.get('Retry-After', '20'))
                logger.warning('Trakt rate-limited on %s page %s — sleeping %ss', path, page, retry)
                time.sleep(min(retry, 60))
                resp = requests.get(
                    f'https://api.trakt.tv{path}',
                    headers=_trakt_headers(client_id),
                    params={'page': page, 'limit': TRAKT_PAGE_SIZE, 'extended': 'full'},
                    timeout=15,
                )
            if not resp.ok:
                logger.warning('Trakt %s page %s -> %s', path, page, resp.status_code)
                break
            batch = resp.json() or []
            if not batch:
                break
            out.extend(batch)
            time.sleep(0.12)
        except Exception as e:
            logger.warning('Trakt fetch failed %s page %s: %s', path, page, e)
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
            resp = requests.get(f'https://api.themoviedb.org/3{path}', params=params, timeout=12)
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
                    'media_type': 'movie',
                    'owned': False,
                    'source': 'tmdb',
                })
            time.sleep(0.08)
        except Exception as e:
            logger.warning('TMDb fetch failed %s page %s: %s', path, page, e)
            break
    return out


def _owned_tmdb_ids() -> Set[int]:
    owned: Set[int] = set()
    try:
        from app.plex_service import plex_service
        index = plex_service.build_ownership_index()
        for tid in (index.get('movies_by_tmdb') or {}):
            try:
                owned.add(int(tid))
            except (TypeError, ValueError):
                pass
        for entry in (index.get('movies') or {}).values():
            tid = entry.get('tmdb_id') if isinstance(entry, dict) else None
            if tid:
                try:
                    owned.add(int(tid))
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        logger.warning('daily_missing: ownership index unavailable: %s', e)

    try:
        from app import canon_index
        for tid in canon_index.owned_tmdb_ids() or []:
            try:
                owned.add(int(tid))
            except (TypeError, ValueError):
                pass
    except Exception as e:
        logger.debug('daily_missing: canon owned lookup skipped: %s', e)

    try:
        settings = _settings()
        radarr_url = (settings.get('radarr.url') or '').rstrip('/')
        radarr_key = settings.get('radarr.api_key') or ''
        if radarr_url and radarr_key:
            resp = requests.get(
                f'{radarr_url}/api/v3/movie',
                headers={'X-Api-Key': radarr_key},
                timeout=20,
            )
            if resp.ok:
                for m in resp.json() or []:
                    tid = m.get('tmdbId')
                    if tid:
                        try:
                            owned.add(int(tid))
                        except (TypeError, ValueError):
                            pass
    except Exception as e:
        logger.warning('daily_missing: Radarr catalog unavailable: %s', e)

    return owned


def _tmdb_enrich(candidates: List[Dict[str, Any]], api_key: str) -> None:
    if not api_key or not candidates:
        return
    from concurrent.futures import ThreadPoolExecutor

    def one(item: Dict[str, Any]) -> None:
        tid = item.get('tmdb_id')
        if not tid:
            return
        # Skip if we already have poster + language + votes from list endpoint
        if item.get('poster_path') and item.get('original_language') and item.get('tmdb_vote_count'):
            if item.get('imdb_id'):
                return
        try:
            resp = requests.get(
                f'https://api.themoviedb.org/3/movie/{tid}',
                params={'api_key': api_key},
                timeout=6,
            )
            if not resp.ok:
                return
            data = resp.json() or {}
            item.setdefault('poster_path', data.get('poster_path'))
            if not item.get('poster_path'):
                item['poster_path'] = data.get('poster_path')
            item.setdefault('backdrop_path', data.get('backdrop_path'))
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
            tmdb_imdb = data.get('imdb_id')
            if tmdb_imdb and not item.get('imdb_id'):
                item['imdb_id'] = tmdb_imdb
        except Exception:
            pass

    # Only enrich items missing critical fields (Trakt-sourced)
    need = [c for c in candidates if not c.get('poster_path') or not c.get('original_language') or not c.get('imdb_id')]
    if not need:
        return
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(one, need))


_NON_LATIN_RE = re.compile(
    r'[\u0400-\u04FF\u0500-\u052F'  # Cyrillic
    r'\u0600-\u06FF'  # Arabic
    r'\u0900-\u097F'  # Devanagari
    r'\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF'  # CJK / Hangul
    r'\u0E00-\u0E7F]'  # Thai
)


def _title_has_strong_non_latin(title: str) -> bool:
    """True if title is dominated by non-Latin script (skip for English shelf)."""
    if not title:
        return False
    hits = _NON_LATIN_RE.findall(title)
    if not hits:
        return False
    # Strong signal: several non-Latin chars, or most of the alphabetic content
    non_latin = sum(len(h) for h in hits)
    letters = sum(1 for ch in title if ch.isalpha())
    if non_latin >= 3:
        return True
    return letters > 0 and (non_latin / letters) >= 0.4


def _coerce_imdb_votes(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool):
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


def _year_ok(item: Dict[str, Any]) -> bool:
    """Theatrical-ish window: ~1985 through YEAR_MAX (skip future/unreleased)."""
    y = item.get('year')
    try:
        y = int(y) if y is not None else None
    except (TypeError, ValueError):
        y = None
    if y is None:
        return True  # allow unknowns; enrich may fill later
    return YEAR_MIN <= y <= YEAR_MAX


def _passes_quality_gate(item: Dict[str, Any]) -> bool:
    """Pool gate — inclusive. Rail still feels fresh because the pool is huge.

    Require poster + English-shelf script + year window. Reject only tiny known
    IMDb junk. Accept modest TMDb/Trakt signal so a 4.7k library still yields
    hundreds/thousands of unowned candidates (1985–2025).
    """
    if not item.get('poster_path'):
        return False
    if _title_has_strong_non_latin(item.get('title') or ''):
        return False
    if not _year_ok(item):
        return False

    votes = _coerce_imdb_votes(item.get('imdb_votes'))
    if votes is not None and votes < IMDB_HARD_REJECT:
        return False

    tmdb_votes = item.get('tmdb_vote_count') or 0
    try:
        tmdb_votes = int(tmdb_votes)
    except (TypeError, ValueError):
        tmdb_votes = 0
    watchers = item.get('watchers') or 0
    try:
        watchers = int(watchers)
    except (TypeError, ValueError):
        watchers = 0
    trakt_votes = item.get('trakt_votes') or 0
    try:
        trakt_votes = int(trakt_votes)
    except (TypeError, ValueError):
        trakt_votes = 0

    if votes is not None and votes >= IMDB_MIN_VOTES:
        return True
    if tmdb_votes >= TMDB_VOTE_FALLBACK:
        return True
    if watchers >= 50 or trakt_votes >= 500:
        return True
    # Poster+English+year with *some* TMDb signal still OK for deep pool
    if tmdb_votes >= 50:
        return True
    return False


def _merge_candidate(by_tmdb: Dict[int, Dict[str, Any]], item: Dict[str, Any], owned: Set[int]) -> None:
    tid = item.get('tmdb_id')
    if not tid or tid in owned:
        return
    prev = by_tmdb.get(tid)
    if not prev:
        by_tmdb[tid] = item
        return
    prev['watchers'] = max(prev.get('watchers') or 0, item.get('watchers') or 0)
    prev['trakt_votes'] = max(prev.get('trakt_votes') or 0, item.get('trakt_votes') or 0)
    prev['tmdb_vote_count'] = max(prev.get('tmdb_vote_count') or 0, item.get('tmdb_vote_count') or 0)
    for k in ('poster_path', 'backdrop_path', 'imdb_id', 'overview', 'original_language', 'year'):
        if not prev.get(k) and item.get(k):
            prev[k] = item[k]


def build_pool(force: bool = False) -> Dict[str, Any]:
    """Rebuild the unowned-popular candidate pool and persist it."""
    global _mem_pool, _mem_day, _mem_seed_ids

    settings = _settings()
    enabled = settings.get('apis.trakt.enabled', False)
    client_id = settings.get('apis.trakt.client_id', '')
    tmdb_key = settings.get('apis.tmdb.api_key', '') or os.environ.get('TMDB_API_KEY', '')

    if not force:
        cached = _read_pool()
        if cached and (time.time() - float(cached.get('built_at') or 0)) < POOL_MAX_AGE_SEC:
            if len(cached.get('candidates') or []) >= POOL_MIN_HEALTHY:
                with _lock:
                    _mem_pool = cached
                return cached

    logger.info('daily_missing: building candidate pool…')
    owned = _owned_tmdb_ids()
    logger.info('daily_missing: owned/grabbed ids = %d', len(owned))

    by_tmdb: Dict[int, Dict[str, Any]] = {}

    if enabled and client_id:
        trakt_sources = [
            ('/movies/popular', TRAKT_POPULAR_PAGES),
            ('/movies/watched/yearly', TRAKT_WATCHED_PAGES),
            ('/movies/watched/all', TRAKT_WATCHED_ALL_PAGES),
            ('/movies/anticipated', TRAKT_ANTICIPATED_PAGES),
            ('/movies/favorited/yearly', TRAKT_FAVORITED_PAGES),
            ('/movies/favorited/all', TRAKT_FAVORITED_ALL_PAGES),
            ('/movies/boxoffice', TRAKT_BOXOFFICE_PAGES),
            ('/movies/trending', TRAKT_TRENDING_PAGES),
        ]
        for path, pages in trakt_sources:
            raw = _fetch_trakt_list(path, client_id, pages)
            logger.info('daily_missing: Trakt %s → %d raw', path, len(raw))
            for entry in raw:
                norm = _normalize_trakt_entry(entry)
                if norm:
                    _merge_candidate(by_tmdb, norm, owned)
    else:
        logger.warning('daily_missing: Trakt not configured — relying on TMDb only')

    # TMDb deep coverage — popular / top_rated / multi-sort discover
    if tmdb_key:
        tmdb_queries = [
            ('/movie/popular', TMDB_POPULAR_PAGES, None),
            ('/movie/top_rated', TMDB_TOP_RATED_PAGES, None),
            ('/discover/movie', TMDB_DISCOVER_PAGES, {
                'sort_by': 'vote_count.desc',
                'include_adult': 'false',
                'with_original_language': 'en',
                'vote_count.gte': TMDB_VOTE_FALLBACK,
            }),
            ('/discover/movie', max(20, TMDB_DISCOVER_PAGES // 2), {
                'sort_by': 'popularity.desc',
                'include_adult': 'false',
                'with_original_language': 'en',
                'vote_count.gte': TMDB_VOTE_FALLBACK,
            }),
            ('/discover/movie', max(15, TMDB_DISCOVER_PAGES // 3), {
                'sort_by': 'revenue.desc',
                'include_adult': 'false',
                'with_original_language': 'en',
                'vote_count.gte': TMDB_VOTE_FALLBACK,
            }),
            ('/discover/movie', max(15, TMDB_DISCOVER_PAGES // 3), {
                'sort_by': 'vote_average.desc',
                'include_adult': 'false',
                'with_original_language': 'en',
                'vote_count.gte': max(TMDB_VOTE_FALLBACK, 2000),
            }),
        ]
        # Genre buckets diversify past the owned-heavy head of global lists.
        for genre_id in (28, 35, 18, 27, 878, 53, 10749, 16, 80, 12, 14, 36, 9648, 10751):
            tmdb_queries.append((
                '/discover/movie',
                10,
                {
                    'sort_by': 'vote_count.desc',
                    'include_adult': 'false',
                    'with_original_language': 'en',
                    'vote_count.gte': TMDB_VOTE_FALLBACK,
                    'with_genres': str(genre_id),
                    'primary_release_date.gte': f'{YEAR_MIN}-01-01',
                    'primary_release_date.lte': f'{YEAR_MAX}-12-31',
                },
            ))
        # Year windows 1985–2025 — this is where a big library still leaves tons missing.
        for y0 in range(YEAR_MIN, YEAR_MAX + 1, 2):
            y1 = min(y0 + 1, YEAR_MAX)
            tmdb_queries.append((
                '/discover/movie',
                12,
                {
                    'sort_by': 'vote_count.desc',
                    'include_adult': 'false',
                    'with_original_language': 'en',
                    'vote_count.gte': TMDB_VOTE_FALLBACK,
                    'primary_release_date.gte': f'{y0}-01-01',
                    'primary_release_date.lte': f'{y1}-12-31',
                },
            ))
            tmdb_queries.append((
                '/discover/movie',
                5,
                {
                    'sort_by': 'popularity.desc',
                    'include_adult': 'false',
                    'with_original_language': 'en',
                    'vote_count.gte': max(50, TMDB_VOTE_FALLBACK // 2),
                    'primary_release_date.gte': f'{y0}-01-01',
                    'primary_release_date.lte': f'{y1}-12-31',
                },
            ))
        for path, pages, extra in tmdb_queries:
            raw = _fetch_tmdb_list(tmdb_key, path, pages, extra)
            label = path if not extra else f"{path}:{extra.get('sort_by') or extra.get('with_genres')}"
            logger.info('daily_missing: TMDb %s → %d raw', label, len(raw))
            for item in raw:
                _merge_candidate(by_tmdb, item, owned)

    candidates = list(by_tmdb.values())
    logger.info('daily_missing: %d unique unowned after dedupe', len(candidates))

    _tmdb_enrich(candidates, tmdb_key)

    candidates = [
        c for c in candidates
        if (c.get('original_language') or ENGLISH) == ENGLISH
        and c.get('poster_path')
        and not _title_has_strong_non_latin(c.get('title') or '')
        and _year_ok(c)
    ]
    logger.info(
        'daily_missing: %d after English+poster(+script)+year[%s-%s] filter',
        len(candidates), YEAR_MIN, YEAR_MAX,
    )

    try:
        from app.imdb_ratings import enrich_imdb_ratings
        # Cap OMDb calls — free tier is tight; gate still has TMDb fallback
        enrich_imdb_ratings(
            candidates,
            settings=settings,
            max_items=min(len(candidates), 900),
            replace_rating=True,
            workers=8,
        )
    except Exception as e:
        logger.warning('daily_missing: IMDb enrich failed: %s', e)

    gated = [c for c in candidates if _passes_quality_gate(c)]
    gated.sort(
        key=lambda c: (
            _coerce_imdb_votes(c.get('imdb_votes')) or 0,
            c.get('tmdb_vote_count') or 0,
            c.get('watchers') or 0,
        ),
        reverse=True,
    )
    if len(gated) > POOL_TARGET:
        gated = gated[:POOL_TARGET]

    # If the gate left a tiny/unhealthy pool, dig deeper once so days can rotate.
    if len(gated) < POOL_MIN_HEALTHY and tmdb_key:
        logger.warning(
            'daily_missing: gated pool only %d (< %d) — expanding TMDb/Trakt pages once',
            len(gated), POOL_MIN_HEALTHY,
        )
        extra_by: Dict[int, Dict[str, Any]] = {int(c['tmdb_id']): c for c in gated if c.get('tmdb_id')}
        if enabled and client_id:
            for path, pages in [
                ('/movies/popular', TRAKT_POPULAR_PAGES + 50),
                ('/movies/watched/all', TRAKT_WATCHED_ALL_PAGES + 40),
                ('/movies/favorited/all', TRAKT_FAVORITED_ALL_PAGES + 30),
                ('/movies/watched/yearly', TRAKT_WATCHED_PAGES + 30),
            ]:
                raw = _fetch_trakt_list(path, client_id, pages)
                for entry in raw:
                    norm = _normalize_trakt_entry(entry)
                    if norm:
                        _merge_candidate(extra_by, norm, owned)
        for path, pages, extra in [
            ('/movie/popular', TMDB_POPULAR_PAGES + 20, None),
            ('/movie/top_rated', TMDB_TOP_RATED_PAGES + 15, None),
            ('/discover/movie', TMDB_DISCOVER_PAGES + 30, {
                'sort_by': 'vote_count.desc',
                'include_adult': 'false',
                'with_original_language': 'en',
                'vote_count.gte': max(800, TMDB_VOTE_FALLBACK // 2),
            }),
            ('/discover/movie', 25, {
                'sort_by': 'popularity.desc',
                'include_adult': 'false',
                'with_original_language': 'en',
                'vote_count.gte': max(800, TMDB_VOTE_FALLBACK // 2),
            }),
        ]:
            raw = _fetch_tmdb_list(tmdb_key, path, pages, extra)
            for item in raw:
                _merge_candidate(extra_by, item, owned)
        more = list(extra_by.values())
        _tmdb_enrich(more, tmdb_key)
        more = [
            c for c in more
            if (c.get('original_language') or ENGLISH) == ENGLISH
            and c.get('poster_path')
            and not _title_has_strong_non_latin(c.get('title') or '')
            and _year_ok(c)
        ]
        try:
            from app.imdb_ratings import enrich_imdb_ratings
            enrich_imdb_ratings(
                more,
                settings=settings,
                max_items=min(len(more), 900),
                replace_rating=True,
                workers=8,
            )
        except Exception as e:
            logger.warning('daily_missing: expand IMDb enrich failed: %s', e)
        gated = [c for c in more if _passes_quality_gate(c)]
        gated.sort(
            key=lambda c: (
                _coerce_imdb_votes(c.get('imdb_votes')) or 0,
                c.get('tmdb_vote_count') or 0,
                c.get('watchers') or 0,
            ),
            reverse=True,
        )
        if len(gated) > POOL_TARGET:
            gated = gated[:POOL_TARGET]
        logger.info('daily_missing: after expand — %d gated', len(gated))

    payload = {
        'built_at': time.time(),
        'built_at_iso': datetime.now(NY).isoformat(),
        'imdb_min_votes': IMDB_MIN_VOTES,
        'tmdb_vote_fallback': TMDB_VOTE_FALLBACK,
        'owned_excluded': len(owned),
        'candidates': gated,
        'note': (
            f'Pool gate soft: year {YEAR_MIN}-{YEAR_MAX}, English+poster; '
            f'reject known IMDb <{IMDB_HARD_REJECT}; TMDb votes>={TMDB_VOTE_FALLBACK} '
            f'or IMDb>={IMDB_MIN_VOTES}. Huge pool → daily 20 rotates.'
        ),
        'imdb_hard_reject': IMDB_HARD_REJECT,
    }
    _write_pool(payload)
    with _lock:
        _mem_pool = payload
        _mem_day = None
        _mem_seed_ids = []
    logger.info('daily_missing: pool ready — %d candidates', len(gated))
    return payload


def _write_pool(payload: Dict[str, Any]) -> None:
    path = _pool_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        tmp.replace(path)
    except Exception as e:
        logger.error('daily_missing: failed to write pool: %s', e)


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
    except Exception as e:
        logger.warning('daily_missing: pool read failed: %s', e)
    return None


def _day_seed(day: str) -> int:
    digest = hashlib.sha256(f'what_youre_missing:{day}'.encode()).hexdigest()
    return int(digest[:16], 16)



def _read_dismissed() -> Set[int]:
    """Permanent tmdb_id dismissals for What You're Missing (survive restarts/rotation)."""
    path = _dismissed_path()
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        raw = data.get('tmdb_ids') if isinstance(data, dict) else data
        out: Set[int] = set()
        for x in (raw or []):
            try:
                tid = int(x)
            except (TypeError, ValueError):
                continue
            if tid > 0:
                out.add(tid)
        return out
    except Exception as e:
        logger.warning('daily_missing: dismissed read failed: %s', e)
        return set()


def _write_dismissed(ids: Set[int]) -> None:
    path = _dismissed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'tmdb_ids': sorted(int(x) for x in ids),
        'updated_at_iso': datetime.now(NY).isoformat(),
    }
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, indent=2) + chr(10))
    tmp.replace(path)


def list_dismissed() -> Dict[str, Any]:
    ids = sorted(_read_dismissed())
    return {
        'tmdb_ids': ids,
        'count': len(ids),
        'path': str(_dismissed_path()),
    }


def dismiss(tmdb_id: int) -> Dict[str, Any]:
    """Permanently hide a title from What You're Missing by tmdb_id."""
    global _mem_seed_ids
    tid = int(tmdb_id)
    if tid <= 0:
        raise ValueError('tmdb_id must be a positive int')
    with _lock:
        ids = _read_dismissed()
        already = tid in ids
        ids.add(tid)
        _write_dismissed(ids)
        if _mem_seed_ids:
            _mem_seed_ids = [x for x in _mem_seed_ids if x != tid]
    logger.info('daily_missing: dismissed tmdb_id=%s (already=%s, total=%d)', tid, already, len(ids))
    return {
        'ok': True,
        'tmdb_id': tid,
        'already': already,
        'dismissed_count': len(ids),
    }


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
            cleaned: List[int] = []
            for x in (ids or []):
                try:
                    tid = int(x)
                except (TypeError, ValueError):
                    continue
                if tid > 0:
                    cleaned.append(tid)
            if cleaned:
                out[str(day)] = cleaned
        return out
    except Exception as e:
        logger.warning('daily_missing: served history read failed: %s', e)
        return {}


def _write_served_history(days: Dict[str, List[int]]) -> None:
    path = _served_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep only recent calendar days
        keep_after = (datetime.now(NY).date() - timedelta(days=SERVED_HISTORY_KEEP_DAYS)).isoformat()
        pruned = {d: ids for d, ids in days.items() if d >= keep_after}
        payload = {
            'days': pruned,
            'updated_at_iso': datetime.now(NY).isoformat(),
            'exclude_recent_days': EXCLUDE_RECENT_DAYS,
        }
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, indent=2) + chr(10))
        tmp.replace(path)
    except Exception as e:
        logger.error('daily_missing: served history write failed: %s', e)


def _recent_served_ids(before_day: str, n_days: int = EXCLUDE_RECENT_DAYS) -> Set[int]:
    """Union of served ids for the n NY calendar days strictly before before_day."""
    if n_days <= 0:
        return set()
    try:
        anchor = datetime.strptime(before_day, '%Y-%m-%d').date()
    except ValueError:
        return set()
    wanted = {(anchor - timedelta(days=i)).isoformat() for i in range(1, n_days + 1)}
    hist = _read_served_history()
    out: Set[int] = set()
    for day in wanted:
        for tid in hist.get(day) or []:
            out.add(int(tid))
    return out


def _record_served(day: str, ids: List[int]) -> None:
    if not ids:
        return
    with _lock:
        hist = _read_served_history()
        hist[day] = [int(x) for x in ids]
        _write_served_history(hist)


def _daily_slice(
    candidates: List[Dict[str, Any]],
    day: str,
    owned: Set[int],
    limit: int,
    dismissed: Optional[Set[int]] = None,
    exclude_ids: Optional[Set[int]] = None,
) -> List[Dict[str, Any]]:
    """Date-seeded shuffle. Prefer titles not in exclude_ids; fill from excluded if short."""
    banned = set(owned) | (dismissed or set())
    exclude = set(exclude_ids or set()) - banned
    primary = [
        c for c in candidates
        if int(c.get('tmdb_id') or 0) not in banned
        and int(c.get('tmdb_id') or 0) not in exclude
    ]
    secondary = [
        c for c in candidates
        if int(c.get('tmdb_id') or 0) not in banned
        and int(c.get('tmdb_id') or 0) in exclude
    ]
    rng = random.Random(_day_seed(day))
    order = list(primary)
    rng.shuffle(order)
    if len(order) < limit and secondary:
        fill = list(secondary)
        rng.shuffle(fill)
        order.extend(fill)
    return order[:limit]


def get_rail(limit: int = RAIL_SIZE, refresh_owned: bool = True) -> Dict[str, Any]:
    """Return today's What You're Missing slate (TMDb-shaped, owned:false)."""
    global _mem_day, _mem_seed_ids

    day = _today_ny()
    pool = _read_pool()
    if not pool or not pool.get('candidates'):
        try:
            pool = build_pool(force=False)
        except Exception as e:
            logger.error('daily_missing: lazy build failed: %s', e)
            return {
                'rail_id': RAIL_ID,
                'title': "What You're Missing",
                'day': day,
                'movies': [],
                'enabled': True,
                'empty': True,
                'error': str(e),
            }

    candidates = list(pool.get('candidates') or [])
    owned = _owned_tmdb_ids() if refresh_owned else set()
    dismissed = _read_dismissed()
    by_id = {int(c['tmdb_id']): c for c in candidates if c.get('tmdb_id')}

    exclude_ids = _recent_served_ids(day, EXCLUDE_RECENT_DAYS)

    with _lock:
        if _mem_day != day or not _mem_seed_ids:
            seeded = _daily_slice(
                candidates,
                day,
                owned,
                limit=max(limit * 4, 80),
                dismissed=dismissed,
                exclude_ids=exclude_ids,
            )
            _mem_seed_ids = [int(m['tmdb_id']) for m in seeded]
            _mem_day = day

        movies: List[Dict[str, Any]] = []
        for tid in _mem_seed_ids:
            if tid in owned or tid in dismissed:
                continue
            item = by_id.get(tid)
            if not item:
                continue
            movies.append(dict(item))
            if len(movies) >= limit:
                break

        if len(movies) < limit:
            have = {int(m['tmdb_id']) for m in movies}
            for item in _daily_slice(
                candidates,
                day,
                owned,
                limit=max(limit * 5, 100),
                dismissed=dismissed,
                exclude_ids=exclude_ids,
            ):
                tid = int(item['tmdb_id'])
                if tid in have or tid in dismissed:
                    continue
                movies.append(dict(item))
                have.add(tid)
                if tid not in _mem_seed_ids:
                    _mem_seed_ids.append(tid)
                if len(movies) >= limit:
                    break

    # Persist today's served slate so tomorrow's primary pick can exclude it.
    try:
        _record_served(day, [int(m['tmdb_id']) for m in movies if m.get('tmdb_id')])
    except Exception as e:
        logger.warning('daily_missing: record served failed: %s', e)

    for m in movies:
        m['owned'] = False
        m['media_type'] = 'movie'
        if m.get('imdb_rating') is not None:
            m['rating'] = m['imdb_rating']

    return {
        'rail_id': RAIL_ID,
        'title': "What You're Missing",
        'subtitle': f"Popular blind spots · refreshes {day}",
        'day': day,
        'movies': movies,
        'enabled': True,
        'empty': len(movies) == 0,
        'pool_size': len(candidates),
        'dismissed_count': len(dismissed),
        'excluded_recent': len(exclude_ids),
        'imdb_min_votes': IMDB_MIN_VOTES,
        'built_at_iso': pool.get('built_at_iso'),
        'note': pool.get('note'),
    }


def verify_day_rotation() -> Dict[str, Any]:
    """Prove consecutive NY days yield substantially different id sets."""
    pool = _read_pool() or {'candidates': []}
    cands = list(pool.get('candidates') or [])
    owned: Set[int] = set()
    dismissed = _read_dismissed()
    day_a = '2026-09-09'
    day_b = '2026-09-10'
    day_c = '2026-09-11'
    a = [m['tmdb_id'] for m in _daily_slice(cands, day_a, owned, RAIL_SIZE, dismissed=dismissed)]
    # Simulate persistence: day B excludes day A's served ids
    b = [
        m['tmdb_id']
        for m in _daily_slice(
            cands, day_b, owned, RAIL_SIZE, dismissed=dismissed, exclude_ids=set(a),
        )
    ]
    c = [
        m['tmdb_id']
        for m in _daily_slice(
            cands, day_c, owned, RAIL_SIZE, dismissed=dismissed, exclude_ids=set(a) | set(b),
        )
    ]
    # Also show pure seed shuffle without exclusion (pool-size sanity)
    a0 = [m['tmdb_id'] for m in _daily_slice(cands, day_a, owned, RAIL_SIZE, dismissed=dismissed)]
    b0 = [m['tmdb_id'] for m in _daily_slice(cands, day_b, owned, RAIL_SIZE, dismissed=dismissed)]
    return {
        'day_a': day_a,
        'day_b': day_b,
        'day_c': day_c,
        'pool_size': len(cands),
        'ids_a': a,
        'ids_b': b,
        'ids_c': c,
        'differ_ab': a != b,
        'differ_bc': b != c,
        'overlap_ab': len(set(a) & set(b)),
        'overlap_bc': len(set(b) & set(c)),
        'new_in_b': len(set(b) - set(a)),
        'new_in_c': len(set(c) - set(b)),
        'overlap_ab_no_exclude': len(set(a0) & set(b0)),
        'healthy_pool': len(cands) >= POOL_MIN_HEALTHY,
    }


def start_daily_missing_thread() -> None:
    global _thread_started
    if _thread_started:
        return
    _thread_started = True

    def loop():
        time.sleep(8)
        while True:
            try:
                build_pool(force=False)
            except Exception as e:
                logger.error('daily_missing: refresh error: %s', e)
            time.sleep(REFRESH_INTERVAL_SEC)

    threading.Thread(target=loop, name='daily-missing-pool', daemon=True).start()
    logger.info('daily_missing: background pool refresh started (every %sh)', REFRESH_INTERVAL_SEC // 3600)


def refresh_now() -> Dict[str, Any]:
    return build_pool(force=True)
