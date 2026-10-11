"""Incomplete movie Collections — TMDb franchise gaps in the standard library.

Scans Movies + Recently Released (not 4K / Full Rips / remux) for titles that
belong to a TMDb collection, then surfaces collections where at least one
released part is owned in those libraries and at least one released part is
missing. Ownership is TMDb-id first, with a normalized title+year fallback
against live Plex for Movies/Recent items that lack a usable tmdb:// guid
(e.g. Force Awakens matched only as imdb:// with a wrong id). Goal: when
someone is watching a franchise film, the sequel is on the shelf.

Cache: data/movie_collections_cache.json
Refresh: on luma start (background) + daily + POST /api/collections/refresh
"""
from __future__ import annotations

import json
import re
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

NY = ZoneInfo('America/New_York')
TMDB_BASE = 'https://api.themoviedb.org/3'

# Standard (non-4K / non-remux) Plex movie libraries — ownership for this feature.
SCOPE_SECTIONS = frozenset({'Movies', 'Recently Released'})


def _norm_title(title: str) -> str:
    """Normalize a title for ownership matching across Plex vs TMDb naming."""
    t = (title or "").lower().strip()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\bepisode\s+(?:[ivxlcdm]+|\d+)\b", " ", t)
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _part_year(part: Dict[str, Any]) -> Optional[int]:
    release_date = (part.get("release_date") or "")[:10]
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        return int(release_date[:4])
    return None


def _scoped_plex_indexes() -> Tuple[Dict[int, Dict[str, Any]], Dict[Tuple[str, int], Dict[str, Any]]]:
    """Live Plex Movies/Recent indexes: by tmdb_id and by (norm_title, year).

    Complements upgrade_library.db, which skips Plex items with no/invalid TMDb
    guid. Title+year catches those (e.g. Force Awakens in Movies with a bad
    imdb:// guid and no tmdb://).
    """
    by_tmdb: Dict[int, Dict[str, Any]] = {}
    by_title_year: Dict[Tuple[str, int], Dict[str, Any]] = {}
    try:
        from app.plex_service import plex_service
        index = plex_service.build_ownership_index()
    except Exception as e:
        logger.warning("movie_collections: plex ownership index unavailable: %s", e)
        return by_tmdb, by_title_year

    for entries in (index.get("movies_by_title") or {}).values():
        for raw in entries or []:
            lib = (raw.get("library_name") or "").strip()
            if lib not in SCOPE_SECTIONS:
                continue
            title = raw.get("title") or ""
            year = raw.get("year")
            try:
                year_i = int(year) if year is not None and str(year).isdigit() else None
            except (TypeError, ValueError):
                year_i = None
            entry = {
                "tmdb_id": None,
                "title": title,
                "year": year_i,
                "poster_path": None,
                "rating_key": raw.get("rating_key"),
                "section_name": lib,
                "current_quality": None,
                "current_tier": None,
            }
            tid_raw = raw.get("tmdb_id")
            if tid_raw is not None and str(tid_raw).isdigit():
                tid = int(tid_raw)
                entry["tmdb_id"] = tid
                prev = by_tmdb.get(tid)
                if prev is None or (
                    prev.get("section_name") != "Movies" and lib == "Movies"
                ):
                    by_tmdb[tid] = entry
            if title and year_i is not None:
                key = (_norm_title(title), year_i)
                if not key[0]:
                    continue
                prev = by_title_year.get(key)
                if prev is None or (
                    prev.get("section_name") != "Movies" and lib == "Movies"
                ):
                    by_title_year[key] = entry
    logger.info(
        "movie_collections: plex scoped indexes tmdb=%d title_year=%d",
        len(by_tmdb), len(by_title_year),
    )
    return by_tmdb, by_title_year


def _lookup_owned_by_title(
    part: Dict[str, Any],
    title_index: Dict[Tuple[str, int], Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    year = _part_year(part)
    if year is None or not title_index:
        return None
    candidates = []
    for field in ("title", "original_title"):
        raw = part.get(field) or ""
        nt = _norm_title(raw)
        if nt:
            candidates.append(nt)
    # Dedupe while preserving order
    seen = set()
    for nt in candidates:
        if nt in seen:
            continue
        seen.add(nt)
        hit = title_index.get((nt, year))
        if hit:
            return hit
    return None


REFRESH_INTERVAL_SEC = 24 * 3600
STARTUP_DELAY_SEC = 12
MOVIE_FETCH_WORKERS = 8
COLLECTION_FETCH_WORKERS = 6
REQUEST_PAUSE = 0.03  # gentle on TMDb

_lock = threading.RLock()
_thread_started = False
_building = False
_mem: Optional[Dict[str, Any]] = None


def _data_dir() -> Path:
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent
    return Path(__file__).resolve().parent.parent / 'data'


def _cache_path() -> Path:
    return _data_dir() / 'movie_collections_cache.json'


def _upgrade_db_path() -> Path:
    return _data_dir() / 'upgrade_library.db'


def _settings():
    from app.settings_manager import SettingsManager
    return SettingsManager()


def _tmdb_key() -> str:
    try:
        key = (_settings().get('apis.tmdb.api_key') or '').strip()
        if key:
            return key
    except Exception:
        pass
    return (os.environ.get('TMDB_API_KEY') or '').strip()


def _today() -> date:
    return datetime.now(NY).date()


def _is_released(release_date: Optional[str], today: Optional[date] = None) -> bool:
    """True when TMDb has a release date on or before today.

    Empty / malformed dates are treated as unreleased so they don't create
    phantom gaps (trailers, TBA entries).
    """
    if not release_date or len(release_date) < 8:
        return False
    try:
        rd = date.fromisoformat(release_date[:10])
    except ValueError:
        return False
    return rd <= (today or _today())


def scoped_owned_movies() -> Dict[int, Dict[str, Any]]:
    """tmdb_id → best row from Movies / Recently Released (READ-ONLY)."""
    p = _upgrade_db_path()
    if not p.exists():
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT tmdb_id, title, year, poster_path, rating_key, section_name,
                   current_quality, current_tier
            FROM library_movies
            WHERE section_name IN ('Movies', 'Recently Released')
              AND tmdb_id IS NOT NULL AND tmdb_id != ''
            """
        ).fetchall()
        conn.close()
        for r in rows:
            try:
                tid = int(r['tmdb_id'])
            except (TypeError, ValueError):
                continue
            entry = {
                'tmdb_id': tid,
                'title': r['title'] or '',
                'year': int(r['year']) if str(r['year'] or '').isdigit() else None,
                'poster_path': r['poster_path'] or None,
                'rating_key': r['rating_key'],
                'section_name': r['section_name'],
                'current_quality': r['current_quality'],
                'current_tier': r['current_tier'],
            }
            prev = out.get(tid)
            # Prefer Movies over Recently Released when both exist.
            if prev is None or (
                prev.get('section_name') != 'Movies' and entry['section_name'] == 'Movies'
            ):
                out[tid] = entry
    except Exception as e:
        logger.warning('movie_collections: could not read scoped ownership: %s', e)
    return out


def _load_cache() -> Dict[str, Any]:
    global _mem
    with _lock:
        if _mem is not None:
            return _mem
    path = _cache_path()
    if not path.exists():
        empty = {
            'built_at': 0,
            'built_at_iso': None,
            'movie_map': {},
            'collections': [],
            'stats': {},
        }
        with _lock:
            _mem = empty
        return empty
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('cache root not a dict')
        data.setdefault('movie_map', {})
        data.setdefault('collections', [])
        data.setdefault('stats', {})
        with _lock:
            _mem = data
        return data
    except Exception as e:
        logger.warning('movie_collections: bad cache, starting fresh: %s', e)
        empty = {
            'built_at': 0,
            'built_at_iso': None,
            'movie_map': {},
            'collections': [],
            'stats': {},
        }
        with _lock:
            _mem = empty
        return empty


def _save_cache(data: Dict[str, Any]) -> None:
    global _mem
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(path)
    with _lock:
        _mem = data


def _tmdb_get(path: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    q = dict(params or {})
    q['api_key'] = api_key
    try:
        resp = requests.get(f'{TMDB_BASE}{path}', params=q, timeout=12)
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            retry = int(resp.headers.get('Retry-After', '2'))
            time.sleep(min(retry, 15))
            resp = requests.get(f'{TMDB_BASE}{path}', params=q, timeout=12)
        if not resp.ok:
            logger.debug('TMDb %s -> %s', path, resp.status_code)
            return None
        return resp.json()
    except Exception as e:
        logger.debug('TMDb GET %s failed: %s', path, e)
        return None


def _fetch_movie_collection_id(tmdb_id: int, api_key: str) -> Tuple[int, Optional[int]]:
    """Return (tmdb_id, collection_id|None)."""
    data = _tmdb_get(f'/movie/{tmdb_id}', api_key)
    time.sleep(REQUEST_PAUSE)
    if not data:
        return tmdb_id, None
    coll = data.get('belongs_to_collection')
    if not isinstance(coll, dict) or not coll.get('id'):
        return tmdb_id, None
    try:
        return tmdb_id, int(coll['id'])
    except (TypeError, ValueError):
        return tmdb_id, None


def _fetch_collection(collection_id: int, api_key: str) -> Optional[Dict[str, Any]]:
    data = _tmdb_get(f'/collection/{collection_id}', api_key)
    time.sleep(REQUEST_PAUSE)
    return data


def _normalize_part(
    part: Dict[str, Any],
    owned: Dict[int, Dict[str, Any]],
    today: date,
    title_index: Optional[Dict[Tuple[str, int], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    tid = int(part['id'])
    release_date = (part.get('release_date') or '')[:10] or None
    released = _is_released(release_date, today)
    own = owned.get(tid)
    if not own and title_index:
        own = _lookup_owned_by_title(part, title_index)
        # Remember title-matched ownership under this tmdb id for the rest of the build.
        if own:
            owned[tid] = own
    status = 'owned' if own else ('upcoming' if not released else 'missing')
    year = None
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        year = int(release_date[:4])
    try:
        popularity = float(part.get('popularity') or 0)
    except (TypeError, ValueError):
        popularity = 0.0
    try:
        vote_count = int(part.get('vote_count') or 0)
    except (TypeError, ValueError):
        vote_count = 0
    return {
        'tmdb_id': tid,
        'title': part.get('title') or part.get('original_title') or '',
        'year': year,
        'release_date': release_date,
        'poster_path': part.get('poster_path'),
        'overview': part.get('overview') or '',
        'status': status,
        'owned': status == 'owned',
        'released': released,
        'rating_key': own.get('rating_key') if own else None,
        'section_name': own.get('section_name') if own else None,
        'imdb_rating': None,
        'imdb_votes': None,
        'imdb_id': None,
        'tmdb_popularity': popularity,
        'tmdb_vote_count': vote_count,
        'media_type': 'movie',
    }



def _collection_fame(parts: List[Dict[str, Any]]) -> float:
    """Prominence for list ranking — big franchises float to the top.

    Prefer max IMDb votes across released parts; fall back to TMDb vote_count,
    then popularity. Max (not sum) so one blockbuster defines the franchise.
    """
    released = [p for p in parts if p.get('status') != 'upcoming']
    pool = released or parts
    imdb = [int(p.get('imdb_votes') or 0) for p in pool]
    if any(v > 0 for v in imdb):
        return float(max(imdb))
    tmdb_votes = [int(p.get('tmdb_vote_count') or 0) for p in pool]
    if any(v > 0 for v in tmdb_votes):
        return float(max(tmdb_votes))
    pops = [float(p.get('tmdb_popularity') or 0) for p in pool]
    return float(max(pops) if pops else 0)


def _build_collection_entry(
    raw: Dict[str, Any],
    owned: Dict[int, Dict[str, Any]],
    today: date,
    title_index: Optional[Dict[Tuple[str, int], Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        cid = int(raw['id'])
    except (KeyError, TypeError, ValueError):
        return None
    parts_raw = raw.get('parts') or []
    parts = []
    for part in parts_raw:
        if not part or not part.get('id'):
            continue
        try:
            parts.append(_normalize_part(part, owned, today, title_index=title_index))
        except Exception:
            continue
    owned_parts = [p for p in parts if p['status'] == 'owned']
    missing_parts = [p for p in parts if p['status'] == 'missing']
    upcoming_parts = [p for p in parts if p['status'] == 'upcoming']
    released_total = len(owned_parts) + len(missing_parts)

    if not owned_parts or not missing_parts:
        return None  # complete, empty, or only-upcoming gaps

    # Owned first, then missing, then upcoming; release order within each group.
    _STATUS_ORDER = {'owned': 0, 'missing': 1, 'upcoming': 2}
    parts.sort(key=lambda p: (
        _STATUS_ORDER.get(p.get('status'), 9),
        p.get('release_date') or '9999-99-99',
        p.get('tmdb_id') or 0,
    ))

    fame = _collection_fame(parts)

    return {
        'id': cid,
        'name': raw.get('name') or f'Collection {cid}',
        'overview': raw.get('overview') or '',
        'poster_path': raw.get('poster_path'),
        'backdrop_path': raw.get('backdrop_path'),
        'owned_count': len(owned_parts),
        'missing_count': len(missing_parts),
        'upcoming_count': len(upcoming_parts),
        'released_total': released_total,
        'parts_total': len(parts),
        'fame_score': fame,
        'parts': parts,
    }


def build_cache(force: bool = False) -> Dict[str, Any]:
    """Scan scoped library → incomplete TMDb collections. Thread-safe."""
    global _building
    with _lock:
        if _building:
            logger.info('movie_collections: build already in progress')
            return _load_cache()
        _building = True

    try:
        api_key = _tmdb_key()
        if not api_key:
            logger.info('movie_collections: skipped (TMDB not connected)')
            return _load_cache()

        owned = scoped_owned_movies()
        plex_by_tmdb, title_index = _scoped_plex_indexes()
        for tid, entry in plex_by_tmdb.items():
            prev = owned.get(tid)
            if prev is None or (
                prev.get('section_name') != 'Movies' and entry.get('section_name') == 'Movies'
            ):
                owned[tid] = entry
        owned_ids = set(owned.keys())
        if not owned_ids:
            logger.warning('movie_collections: scoped library empty — refusing to mark all missing')
            payload = {
                'built_at': time.time(),
                'built_at_iso': datetime.now(NY).isoformat(),
                'movie_map': {},
                'collections': [],
                'stats': {'owned_scoped': 0, 'note': 'empty_library'},
            }
            _save_cache(payload)
            return payload

        cache = _load_cache()
        movie_map: Dict[str, Any] = dict(cache.get('movie_map') or {})
        if force:
            # Keep known mappings (they're stable); still re-resolve collections.
            pass

        # Drop mappings for titles no longer in scoped libs; discover new ones.
        stale_keys = [k for k in movie_map if int(k) not in owned_ids]
        for k in stale_keys:
            movie_map.pop(k, None)

        need_fetch = [tid for tid in owned_ids if str(tid) not in movie_map]
        logger.info(
            'movie_collections: scoped=%d mapped=%d to_fetch=%d force=%s',
            len(owned_ids), len(movie_map), len(need_fetch), force,
        )

        if need_fetch:
            done = 0
            with ThreadPoolExecutor(max_workers=MOVIE_FETCH_WORKERS) as pool:
                futs = {
                    pool.submit(_fetch_movie_collection_id, tid, api_key): tid
                    for tid in need_fetch
                }
                for fut in as_completed(futs):
                    tid, cid = fut.result()
                    movie_map[str(tid)] = cid  # may be None
                    done += 1
                    if done % 200 == 0:
                        logger.info('movie_collections: movie map progress %d/%d', done, len(need_fetch))

        # Unique collections that have ≥1 owned part.
        coll_ids: Set[int] = set()
        for tid in owned_ids:
            cid = movie_map.get(str(tid))
            if cid is not None:
                try:
                    coll_ids.add(int(cid))
                except (TypeError, ValueError):
                    continue

        logger.info('movie_collections: fetching %d collections', len(coll_ids))
        today = _today()
        incomplete: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=COLLECTION_FETCH_WORKERS) as pool:
            futs = {pool.submit(_fetch_collection, cid, api_key): cid for cid in coll_ids}
            for fut in as_completed(futs):
                raw = fut.result()
                if not raw:
                    continue
                entry = _build_collection_entry(raw, owned, today, title_index=title_index)
                if entry:
                    incomplete.append(entry)

        # Provisional fame order (TMDb signals); recomputed after IMDb enrich below.
        for coll in incomplete:
            coll['fame_score'] = _collection_fame(coll.get('parts') or [])
        incomplete.sort(key=lambda c: (-float(c.get('fame_score') or 0), c['name'].lower()))

        # IMDb badges: spread across collections, prefer missing titles (the gaps).
        try:
            from app.imdb_ratings import enrich_imdb_ratings
            badge_items: List[Dict[str, Any]] = []
            seen_tmdb: Set[int] = set()
            # Pass 1 — up to 2 missing parts per collection
            for coll in incomplete:
                n = 0
                for part in coll['parts']:
                    if part['status'] != 'missing':
                        continue
                    tid = int(part['tmdb_id'])
                    if tid in seen_tmdb:
                        continue
                    seen_tmdb.add(tid)
                    badge_items.append(part)
                    n += 1
                    if n >= 2:
                        break
                if len(badge_items) >= 100:
                    break
            # Pass 2 — one owned part per collection for context (fill to 140)
            if len(badge_items) < 140:
                for coll in incomplete:
                    for part in coll['parts']:
                        if part['status'] != 'owned':
                            continue
                        tid = int(part['tmdb_id'])
                        if tid in seen_tmdb:
                            continue
                        seen_tmdb.add(tid)
                        badge_items.append(part)
                        break
                    if len(badge_items) >= 140:
                        break
            enrich_imdb_ratings(
                badge_items,
                settings=_settings(),
                max_items=140,
                replace_rating=False,
            )
            for coll in incomplete:
                coll['fame_score'] = _collection_fame(coll.get('parts') or [])
            incomplete.sort(key=lambda c: (-float(c.get('fame_score') or 0), c['name'].lower()))
        except Exception as e:
            logger.debug('movie_collections: imdb enrich skipped: %s', e)

        payload = {
            'built_at': time.time(),
            'built_at_iso': datetime.now(NY).isoformat(),
            'movie_map': movie_map,
            'collections': incomplete,
            'stats': {
                'owned_scoped': len(owned_ids),
                'mapped_with_collection': sum(1 for tid in owned_ids if movie_map.get(str(tid))),
                'collections_checked': len(coll_ids),
                'incomplete': len(incomplete),
                'movies_fetched_this_run': len(need_fetch),
            },
        }
        _save_cache(payload)
        logger.info(
            'movie_collections: done — %d incomplete of %d collections (scoped owned %d)',
            len(incomplete), len(coll_ids), len(owned_ids),
        )
        return payload
    finally:
        with _lock:
            _building = False



def _restamp_part(
    part: Dict[str, Any],
    owned: Dict[int, Dict[str, Any]],
    title_index: Optional[Dict[Tuple[str, int], Dict[str, Any]]],
    today: date,
) -> Dict[str, Any]:
    """Re-evaluate owned/missing/upcoming for a cached part against live indexes.

    Avoids a full TMDb rebuild when Plex gains a title after Franchise Completer
    (or any grab) while movie_collections_cache.json is still stale.
    """
    try:
        tid = int(part.get("tmdb_id") or 0)
    except (TypeError, ValueError):
        tid = 0
    if not tid:
        return dict(part)

    own = owned.get(tid)
    if not own and title_index:
        pseudo = {
            "id": tid,
            "title": part.get("title") or "",
            "release_date": part.get("release_date") or "",
        }
        own = _lookup_owned_by_title(pseudo, title_index)
        if own:
            owned[tid] = own

    release_date = (part.get("release_date") or "")[:10] or None
    released = part.get("released")
    if released is None:
        released = _is_released(release_date, today)
    else:
        released = bool(released)

    status = "owned" if own else ("upcoming" if not released else "missing")
    out = dict(part)
    out["status"] = status
    out["owned"] = status == "owned"
    out["released"] = released
    out["rating_key"] = own.get("rating_key") if own else None
    out["section_name"] = own.get("section_name") if own else None
    return out


def _restamp_collection(
    coll: Dict[str, Any],
    owned: Dict[int, Dict[str, Any]],
    title_index: Optional[Dict[Tuple[str, int], Dict[str, Any]]],
    today: date,
) -> Dict[str, Any]:
    """Return a shallow-copied collection with live ownership counts/parts."""
    parts = [
        _restamp_part(p, owned, title_index, today)
        for p in (coll.get("parts") or [])
        if isinstance(p, dict)
    ]
    owned_parts = [p for p in parts if p.get("status") == "owned"]
    missing_parts = [p for p in parts if p.get("status") == "missing"]
    upcoming_parts = [p for p in parts if p.get("status") == "upcoming"]
    released_total = len(owned_parts) + len(missing_parts)
    out = dict(coll)
    out["parts"] = parts
    out["owned_count"] = len(owned_parts)
    out["missing_count"] = len(missing_parts)
    out["upcoming_count"] = len(upcoming_parts)
    out["released_total"] = released_total
    out["parts_total"] = len(parts)
    return out


def _live_restamp_collections(collections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-stamp every collection against live Plex + upgrade_library ownership."""
    if not collections:
        return collections
    try:
        owned, title_index = _live_owned_indexes()
    except Exception as e:
        logger.warning("movie_collections: live ownership unavailable for restamp: %s", e)
        return collections
    today = datetime.now(NY).date()
    return [_restamp_collection(c, owned, title_index, today) for c in collections]


def schedule_ownership_refresh(reason: str = "") -> None:
    """Kick a background cache rebuild (e.g. after Franchise Completer grab)."""
    def _run() -> None:
        try:
            # Brief pause so Plex/import paths can settle after a grab.
            time.sleep(3)
            logger.info("movie_collections: ownership refresh starting (%s)", reason or "manual")
            build_cache(force=True)
        except Exception as e:
            logger.warning(
                "movie_collections: ownership refresh failed (%s): %s",
                reason or "manual",
                e,
            )

    threading.Thread(
        target=_run, name="collections-ownership-refresh", daemon=True
    ).start()


def get_incomplete_list() -> Dict[str, Any]:
    cache = _load_cache()
    collections = list(cache.get('collections') or [])
    # Live ownership re-stamp: Plex may have gained titles since cache build
    # (Franchise Completer / manual grab) without a full TMDb rebuild.
    collections = _live_restamp_collections(collections)
    # Drop franchises that are now complete after restamp.
    collections = [c for c in collections if int(c.get('missing_count') or 0) > 0]
    # Ensure fame ranking even for caches built before fame_score existed.
    for c in collections:
        if c.get('fame_score') is None:
            c['fame_score'] = _collection_fame(c.get('parts') or [])
    collections.sort(key=lambda c: (-float(c.get('fame_score') or 0), (c.get('name') or '').lower()))
    return {
        'collections': [
            {
                'id': c['id'],
                'name': c['name'],
                'poster_path': c.get('poster_path'),
                'backdrop_path': c.get('backdrop_path'),
                'owned_count': c.get('owned_count', 0),
                'missing_count': c.get('missing_count', 0),
                'upcoming_count': c.get('upcoming_count', 0),
                'released_total': c.get('released_total', 0),
                'parts_total': c.get('parts_total', 0),
                'fame_score': c.get('fame_score', 0),
            }
            for c in collections
        ],
        'built_at': cache.get('built_at'),
        'built_at_iso': cache.get('built_at_iso'),
        'stats': cache.get('stats') or {},
        'building': _building,
    }



def _build_collection_entry_any(
    raw: Dict[str, Any],
    owned: Dict[int, Dict[str, Any]],
    today: date,
    title_index: Optional[Dict[Tuple[str, int], Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Like _build_collection_entry but keeps complete / empty-owned franchises.

    Used by the movie-modal Collection sheet so Force Awakens → Star Wars still
    opens even when every released part is owned (not in the incomplete cache).
    Still requires more than one released part (single-title "collections" hide).
    """
    try:
        cid = int(raw['id'])
    except (KeyError, TypeError, ValueError):
        return None
    parts_raw = raw.get('parts') or []
    parts = []
    for part in parts_raw:
        if not part or not part.get('id'):
            continue
        try:
            parts.append(_normalize_part(part, owned, today, title_index=title_index))
        except Exception:
            continue
    owned_parts = [p for p in parts if p['status'] == 'owned']
    missing_parts = [p for p in parts if p['status'] == 'missing']
    upcoming_parts = [p for p in parts if p['status'] == 'upcoming']
    released_total = len(owned_parts) + len(missing_parts)
    if released_total <= 1:
        return None

    _STATUS_ORDER = {'owned': 0, 'missing': 1, 'upcoming': 2}
    parts.sort(key=lambda p: (
        _STATUS_ORDER.get(p.get('status'), 9),
        p.get('release_date') or '9999-99-99',
        p.get('tmdb_id') or 0,
    ))
    fame = _collection_fame(parts)
    return {
        'id': cid,
        'name': raw.get('name') or f'Collection {cid}',
        'overview': raw.get('overview') or '',
        'poster_path': raw.get('poster_path'),
        'backdrop_path': raw.get('backdrop_path'),
        'owned_count': len(owned_parts),
        'missing_count': len(missing_parts),
        'upcoming_count': len(upcoming_parts),
        'released_total': released_total,
        'parts_total': len(parts),
        'fame_score': fame,
        'parts': parts,
    }


def _live_owned_indexes() -> Tuple[Dict[int, Dict[str, Any]], Dict[Tuple[str, int], Dict[str, Any]]]:
    owned = scoped_owned_movies()
    plex_by_tmdb, title_index = _scoped_plex_indexes()
    for tid, entry in plex_by_tmdb.items():
        prev = owned.get(tid)
        if prev is None or (
            prev.get('section_name') != 'Movies' and entry.get('section_name') == 'Movies'
        ):
            owned[tid] = entry
    return owned, title_index


def get_collection_detail(collection_id: int) -> Optional[Dict[str, Any]]:
    """Return collection ownership detail.

    Prefer the incomplete-collections cache (fast, already enriched). Fall back
    to a live TMDb fetch + Movies/Recent ownership so complete franchises
    (e.g. Star Wars) and uncached ids still open from the movie modal.
    """
    cache = _load_cache()
    for c in cache.get('collections') or []:
        if int(c.get('id', -1)) == int(collection_id):
            # Lazy IMDb fill for parts still missing a score (detail view).
            try:
                need = [p for p in (c.get('parts') or []) if p.get('imdb_rating') is None and p.get('status') != 'upcoming']
                if need:
                    from app.imdb_ratings import enrich_imdb_ratings
                    enrich_imdb_ratings(need[:24], settings=_settings(), max_items=24, replace_rating=False)
                    _save_cache(cache)
            except Exception as e:
                logger.debug('movie_collections: detail imdb enrich skipped: %s', e)
            # Live ownership re-stamp before serving (stale cache after grabs).
            try:
                owned, title_index = _live_owned_indexes()
                today = datetime.now(NY).date()
                stamped = _restamp_collection(c, owned, title_index, today)
            except Exception as e:
                logger.debug('movie_collections: detail restamp skipped: %s', e)
                stamped = dict(c)
                stamped['parts'] = list(c.get('parts') or [])
            parts = list(stamped.get('parts') or [])
            _STATUS_ORDER = {'owned': 0, 'missing': 1, 'upcoming': 2}
            parts.sort(key=lambda p: (
                _STATUS_ORDER.get(p.get('status'), 9),
                p.get('release_date') or '9999-99-99',
                p.get('tmdb_id') or 0,
            ))
            out = dict(stamped)
            out['parts'] = parts
            if out.get('fame_score') is None:
                out['fame_score'] = _collection_fame(parts)
            return out

    # Live fetch for complete / uncached collections (movie-modal Collection button).
    api_key = _tmdb_key()
    if not api_key:
        return None
    try:
        raw = _fetch_collection(int(collection_id), api_key)
    except Exception as e:
        logger.warning('movie_collections: live fetch %s failed: %s', collection_id, e)
        return None
    if not raw:
        return None
    try:
        owned, title_index = _live_owned_indexes()
        today = datetime.now(NY).date()
        return _build_collection_entry_any(raw, owned, today, title_index=title_index)
    except Exception as e:
        logger.warning('movie_collections: live build %s failed: %s', collection_id, e)
        return None


def refresh_now() -> Dict[str, Any]:
    return build_cache(force=True)


def ensure_warm() -> None:
    """If cache is empty/stale, kick a background build (non-blocking)."""
    cache = _load_cache()
    age = time.time() - float(cache.get('built_at') or 0)
    if (cache.get('collections') or cache.get('movie_map')) and age < REFRESH_INTERVAL_SEC:
        return
    threading.Thread(target=lambda: build_cache(force=False), name='collections-warm', daemon=True).start()


def start_movie_collections_thread() -> None:
    global _thread_started
    if _thread_started:
        return
    _thread_started = True

    def loop():
        time.sleep(STARTUP_DELAY_SEC)
        while True:
            try:
                build_cache(force=False)
            except Exception as e:
                logger.error('movie_collections: refresh error: %s', e)
            time.sleep(REFRESH_INTERVAL_SEC)

    threading.Thread(target=loop, name='movie-collections', daemon=True).start()
    logger.info(
        'movie_collections: background refresh started (every %sh)',
        REFRESH_INTERVAL_SEC // 3600,
    )
