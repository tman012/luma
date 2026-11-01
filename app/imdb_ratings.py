"""IMDb rating enrichment for user-facing scores.

TMDb vote_average is kept for discovery/sorting where useful, but any number
shown to the user as a rating should come from IMDb (via OMDb). Prefer
`imdb_id` lookups; fall back to title+year. Cache aggressively — OMDb free
tier is tight and rail responses can hit dozens of titles at once.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# Shared process cache (same shape as hero popularity cache in main.py).
_CACHE: Dict[str, tuple] = {}  # key -> (ts, {rating, votes})
_CACHE_TTL = 12 * 3600
_CACHE_MAX = 4000


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, data = entry
    if time.time() - ts >= _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return data


def _cache_set(key: str, data: Dict[str, Any]) -> None:
    _CACHE[key] = (time.time(), data)
    if len(_CACHE) > _CACHE_MAX:
        oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])[0]
        del _CACHE[oldest]


def _parse_rating(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.upper() == 'N/A':
        return None
    try:
        val = float(s)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return round(val, 1)


def _parse_votes(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).replace(',', '').strip()
    if not s or not s.isdigit():
        return None
    return int(s)


def _omdb_key(settings) -> str:
    try:
        return (settings.get('apis.omdb.api_key') or '').strip()
    except Exception:
        return ''


def _lookup_one(item: Dict[str, Any], omdb_key: str) -> Dict[str, Any]:
    """Return {rating, votes} for one item. Missing -> nulls."""
    imdb_id = (item.get('imdb_id') or item.get('imdbId') or '').strip()
    title = (item.get('title') or item.get('name') or '').strip()
    year = item.get('year')
    year_s = str(year).strip() if year not in (None, '') else ''

    cache_key = f'imdb:{imdb_id}' if imdb_id else (
        f'imdb:{title.lower()}_{year_s}' if title else ''
    )
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    empty = {'rating': None, 'votes': None}
    if not omdb_key:
        return empty

    params: Dict[str, str] = {'apikey': omdb_key}
    if imdb_id:
        params['i'] = imdb_id
    elif title:
        params['t'] = title
        if year_s:
            params['y'] = year_s
    else:
        return empty

    try:
        resp = requests.get('https://www.omdbapi.com/', params=params, timeout=5)
        if not resp.ok:
            return empty
        data = resp.json() or {}
        if data.get('Response') != 'True':
            result = empty
        else:
            result = {
                'rating': _parse_rating(data.get('imdbRating')),
                'votes': _parse_votes(data.get('imdbVotes')),
            }
            # Prefer stable imdb id cache key when OMDb returns one.
            resolved = (data.get('imdbID') or '').strip()
            if resolved:
                _cache_set(f'imdb:{resolved}', result)
        if cache_key:
            _cache_set(cache_key, result)
        return result
    except Exception as e:
        logger.debug(f'IMDb rating lookup failed for {imdb_id or title}: {e}')
        return empty


def enrich_imdb_ratings(
    items: List[Dict[str, Any]],
    *,
    settings=None,
    max_items: int = 40,
    replace_rating: bool = True,
    rating_field: str = 'rating',
    workers: int = 8,
) -> List[Dict[str, Any]]:
    """Attach imdb_rating / imdb_votes; optionally replace display rating_field.

    When replace_rating is True and an IMDb score is found, rating_field becomes
    that score. When IMDb is missing, rating_field is cleared (set to None) so
    the UI never presents TMDb/Trakt numbers as if they were IMDb. Original
    non-IMDb value is preserved on `_legacy_rating` for debugging only.
    """
    if not items:
        return items

    omdb_key = _omdb_key(settings) if settings is not None else ''
    if not omdb_key:
        logger.warning('IMDb enrich: no OMDb API key — clearing display ratings')
        for item in items:
            if replace_rating and rating_field in item:
                item['_legacy_rating'] = item.get(rating_field)
                item[rating_field] = None
            item['imdb_rating'] = None
            item.setdefault('imdb_votes', None)
        return items

    targets = items[:max_items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda it: _lookup_one(it, omdb_key), targets))

    hit = 0
    for item, result in zip(targets, results):
        rating = result.get('rating')
        votes = result.get('votes')
        item['imdb_rating'] = rating
        item['imdb_votes'] = votes
        if rating is not None:
            hit += 1
        if replace_rating:
            if rating_field in item or rating is not None:
                item['_legacy_rating'] = item.get(rating_field)
                item[rating_field] = rating

    # Items beyond max_items: clear display rating rather than leave TMDb values.
    if replace_rating and len(items) > max_items:
        for item in items[max_items:]:
            item['_legacy_rating'] = item.get(rating_field)
            item[rating_field] = None
            item.setdefault('imdb_rating', None)

    logger.info(f'IMDb enrich: {hit}/{len(targets)} ratings resolved')
    return items


def imdb_rating_for(
    *,
    settings=None,
    imdb_id: str = '',
    title: str = '',
    year: Any = None,
) -> Optional[float]:
    """Single-title helper for endpoints that return one pick."""
    item = {'imdb_id': imdb_id or '', 'title': title or '', 'year': year}
    return _lookup_one(item, _omdb_key(settings) if settings is not None else '').get('rating')


def lookup_by_tmdb_id(
    tmdb_id: int,
    *,
    tmdb_key: str = "",
    omdb_key: str = "",
    settings=None,
) -> Optional[Dict[str, Any]]:
    """IMDb rating + votes for one TMDb id (TMDb external id → OMDb).

    Returns ``{"rating": float|None, "votes": int|None, "imdb_id": str}`` on a
    successful ask, or ``None`` when TMDb/OMDb could not be reached (caller
    should treat that as "no verdict", distinct from "asked, nothing on file").
    """
    tmdb_key = (tmdb_key or "").strip()
    if not omdb_key and settings is not None:
        omdb_key = _omdb_key(settings)
    omdb_key = (omdb_key or "").strip()
    if not tmdb_key or not omdb_key:
        return None

    try:
        r = requests.get(
            f"https://api.themoviedb.org/3/movie/{int(tmdb_id)}",
            params={"api_key": tmdb_key},
            timeout=15,
        )
        r.raise_for_status()
        imdb_id = ((r.json() or {}).get("imdb_id") or "").strip()
    except Exception as e:
        logger.debug("IMDb lookup: TMDb id %s failed: %s", tmdb_id, e)
        return None

    if not imdb_id:
        return {"rating": None, "votes": None, "imdb_id": ""}

    cache_key = f"imdb:{imdb_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "imdb_id": imdb_id}

    try:
        o = requests.get(
            "https://www.omdbapi.com/",
            params={"apikey": omdb_key, "i": imdb_id},
            timeout=15,
        )
        o.raise_for_status()
        body = o.json() or {}
    except Exception as e:
        logger.debug("IMDb lookup: OMDb %s failed: %s", imdb_id, e)
        return None

    if body.get("Response") != "True":
        result = {"rating": None, "votes": None, "imdb_id": imdb_id}
    else:
        result = {
            "rating": _parse_rating(body.get("imdbRating")),
            "votes": _parse_votes(body.get("imdbVotes")),
            "imdb_id": imdb_id,
        }
    _cache_set(cache_key, {"rating": result["rating"], "votes": result["votes"]})
    return result
