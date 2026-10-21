"""v237: Settings > Home screen hero source and count.

v239 'Most popular': popular movies just added (last 60 days, not obscure)
first, then the library by release recency and popularity.

Sources: 'newest' (the current hero, by date added) or 'popular' (one chosen
Plex movie library ranked by TMDb popularity, recent releases first).
Reads only: Plex library listings and TMDb movie lookups. The ranking is cached
on disk and rebuilt at most once a day in one background thread; a request
never runs the TMDb sweep itself.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
bp = Blueprint('hero_source', __name__)

SOURCES = ('newest', 'popular')
LIMIT_MIN, LIMIT_MAX, LIMIT_DEFAULT = 5, 30, 10
DAY_S = 24 * 3600
RECENT_YEARS = 2
POOL_MAX = 2000          # movies read from the library per refresh
FRESH_DAYS = 60          # v239: 'just added' window (Plex addedAt)
MIN_VOTES = 200          # v239: a just-added movie counts when TMDb has >= this many votes ...
POP_FLOOR = 40.0         # ... or its TMDb popularity is above this
# v240: TMDb's movie genre list, the fallback when no ranking has been built yet.
TMDB_MOVIE_GENRES = ('Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Documentary', 'Drama',
                     'Family', 'Fantasy', 'History', 'Horror', 'Music', 'Mystery', 'Romance',
                     'Science Fiction', 'TV Movie', 'Thriller', 'War', 'Western')
MAX_EXCLUDED = 50
CHECK_S = 15 * 60        # v239: at most one newest-addedAt check per library per 15 minutes

_cfg: Dict[str, Any] = {}
_lock = threading.Lock()
_inflight: set = set()
_checked: Dict[str, float] = {}   # v239: last newest-addedAt check per library


def _newest_added(section_id: str) -> Optional[int]:
    """The library's newest addedAt (one Plex listing of one item); None if Plex doesn't answer."""
    try:
        res = _cfg['get_plex']().get_all_movies(int(section_id), sort='addedAt:desc', limit=1) or {}
        ms = res.get('movies') or []
        return int(ms[0].get('added_at') or ms[0].get('addedAt') or 0) if ms else 0
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Hero newest check failed: {e}")
        return None


def init(*, settings, get_plex: Callable[[], Any], get_current_user: Callable[[], Optional[dict]],
         tmdb_details: Callable[[int], Optional[dict]], cache_dir: Callable[[], Path],
         sections: Callable[[Any], List[dict]], clear_hero: Callable[[], None] = lambda: None) -> None:
    _cfg.update(settings=settings, get_plex=get_plex, get_current_user=get_current_user,
                tmdb_details=tmdb_details, cache_dir=cache_dir, sections=sections, clear_hero=clear_hero)


# --- settings -----------------------------------------------------------------

def clamp_limit(v: Any) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return LIMIT_DEFAULT
    return max(LIMIT_MIN, min(LIMIT_MAX, n))


def current() -> Dict[str, Any]:
    s = _cfg['settings']
    src = s.get('ui.hero.source', 'newest')
    return {'source': src if src in SOURCES else 'newest',
            'section_id': str(s.get('ui.hero.library_section_id', 0) or ''),
            'limit': clamp_limit(s.get('ui.hero.limit', LIMIT_DEFAULT)),
            'excluded_genres': excluded(s.get('ui.hero.excluded_genres', []))}


LEGACY_DEFAULT = ['sport', 'sports']   # v242: the old schema default, never picked by anyone


def excluded(raw: Any) -> List[str]:
    """v240: the stored exclusions as a clean list (older builds stored a comma string).
    v242: the old seeded default (Sport, Sports) reads as nothing excluded."""
    if isinstance(raw, list) and sorted(str(g).strip().lower() for g in raw) == LEGACY_DEFAULT:
        return []
    if isinstance(raw, str) and sorted(g.strip().lower() for g in raw.replace('\n', ',').split(',') if g.strip()) == LEGACY_DEFAULT:
        return []
    if isinstance(raw, str):
        raw = raw.replace('\n', ',').split(',')
    out: List[str] = []
    for g in raw if isinstance(raw, list) else []:
        g = str(g).strip()
        if g and g.lower() not in {x.lower() for x in out}:
            out.append(g)
    return out


def library_genres(section_id: str) -> List[str]:
    """v240: genres of the chosen library's movies from the cached ranking build
    (no outside call); TMDb's movie genre list until a ranking exists."""
    data = load(section_id) if section_id else None
    found = [g for m in (data or {}).get('movies') or [] for g in (m.get('genres') or []) + (m.get('plex_genres') or [])]
    return _dedupe(found or list(TMDB_MOVIE_GENRES))


def _dedupe(names: List[str]) -> List[str]:
    """v242: one entry per genre, case-insensitively (Plex 'Sports' vs 'sports'), sorted."""
    seen: Dict[str, str] = {}
    for g in names:
        g = str(g).strip()
        if g and g.lower() not in seen:
            seen[g.lower()] = g
    return sorted(seen.values(), key=str.lower)


def cached_genres(section_id: str) -> Dict[str, List[str]]:
    """v240: rating_key -> TMDb genre names from the cached ranking build."""
    data = load(str(section_id)) if section_id else None
    return {m['rating_key']: (m.get('genres') or []) + (m.get('plex_genres') or []) for m in (data or {}).get('movies') or [] if m.get('rating_key')}


# --- ranking ------------------------------------------------------------------

def _release_year(m: dict) -> Optional[int]:
    d = m.get('release_date') or ''
    if len(d) >= 4 and d[:4].isdigit():
        return int(d[:4])
    return m.get('year')


def fallback(movies: List[dict], today: Optional[date] = None) -> List[dict]:
    """Released in the last RECENT_YEARS years first, each group by TMDb popularity."""
    today = today or date.today()
    cutoff = today.year - RECENT_YEARS

    def key(m):
        d = m.get('release_date') or ''
        if len(d) == 10:
            try:
                recent = date.fromisoformat(d) >= today.replace(year=today.year - RECENT_YEARS)
            except ValueError:
                recent = (_release_year(m) or 0) > cutoff
        else:
            recent = (_release_year(m) or 0) > cutoff
        return (0 if recent else 1, -(m.get('popularity') or 0), (m.get('title') or '').lower())
    return sorted(movies, key=key)


def notable(m: dict) -> bool:
    return (m.get('vote_count') or 0) >= MIN_VOTES or (m.get('popularity') or 0) > POP_FLOOR


def rank(movies: List[dict], today: Optional[date] = None, now: Optional[float] = None) -> List[dict]:
    """v239: notable movies added in the last FRESH_DAYS days by popularity, then
    everything else in the fallback order. One list, no duplicates: the hero
    takes its first N, so the fallback fills in when few qualify."""
    now = now or time.time()
    cutoff = now - FRESH_DAYS * DAY_S
    fresh = [m for m in movies if (m.get('added_at') or 0) >= cutoff and notable(m)]
    fresh.sort(key=lambda m: (-(m.get('popularity') or 0), (m.get('title') or '').lower()))
    seen = {id(m) for m in fresh}
    return fresh + fallback([m for m in movies if id(m) not in seen], today)


def _path(section_id: str) -> Path:
    return Path(_cfg['cache_dir']()) / 'hero' / f'popular-{section_id}.json'


def load(section_id: str) -> Optional[dict]:
    try:
        return json.loads(_path(section_id).read_text())
    except (OSError, ValueError):
        return None


def build(section_id: str) -> List[dict]:
    """Plex reads + TMDb lookups only. Returns the ranked list and caches it."""
    plex = _cfg['get_plex']()
    res = plex.get_all_movies(int(section_id), sort='addedAt:desc', limit=POOL_MAX) or {}   # v239b: newest first, so recent additions are always in the pool
    items = [m for m in (res.get('movies') or []) if m.get('tmdb_id') and m.get('artPath')]

    def look(m):
        d = _cfg['tmdb_details'](int(m['tmdb_id'])) or {}
        return {'rating_key': str(m.get('ratingKey') or m.get('rating_key') or ''), 'title': m.get('title'),
                'year': m.get('year'), 'tmdb_id': m['tmdb_id'],
                'popularity': float(d.get('popularity') or 0), 'vote_count': int(d.get('vote_count') or 0),
                'genres': [g.get('name') for g in d.get('genres') or [] if isinstance(g, dict) and g.get('name')],
                'plex_genres': [str(g) for g in m.get('genres') or [] if g],   # v242
                'release_date': d.get('release_date') or '', 'added_at': int(m.get('added_at') or m.get('addedAt') or 0)}
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(look, items))
    ranked = rank(rows)
    seen, uniq = set(), []
    for r in ranked:   # one entry per Plex item
        if r['rating_key'] not in seen:
            seen.add(r['rating_key']); uniq.append(r)
    ranked = uniq
    newest = _newest_added(section_id)   # v239b: the same value the 15-minute check compares
    if newest is None:
        newest = max([int(m.get('added_at') or m.get('addedAt') or 0) for m in (res.get('movies') or [])] or [0])
    p = _path(section_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps({'ts': time.time(), 'section_id': section_id, 'newest_added': newest, 'movies': ranked}))
    tmp.replace(p)
    _cfg['clear_hero']()
    return ranked


def _refresh(section_id: str) -> None:
    try:
        build(section_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Hero popular refresh failed for section {section_id}: {e}")
        try:   # keep the old ranking; try again tomorrow, not on the next request
            old = load(section_id) or {'section_id': section_id, 'movies': []}
            old['ts'] = time.time()
            cur = _newest_added(section_id)   # v239b: so the 15-minute check doesn't loop
            if cur is not None:
                old['newest_added'] = cur
            _path(section_id).parent.mkdir(parents=True, exist_ok=True)
            _path(section_id).write_text(json.dumps(old))
        except OSError:
            pass
    finally:
        with _lock:
            _inflight.discard(section_id)


def ranked_keys(section_id: str, now: Optional[float] = None,
                spawn: Callable[[Callable[[], None]], None] = None) -> List[str]:
    """The cached ranking's Plex keys. When the cache is missing or a day old,
    start one background rebuild (at most one per section at a time)."""
    now = now or time.time()
    data = load(section_id)
    stale = not data or now - float(data.get('ts') or 0) >= DAY_S
    if not stale and now - _checked.get(section_id, 0) >= CHECK_S:
        _checked[section_id] = now   # v239: one cheap Plex listing (newest item) per 15 minutes
        newest = _newest_added(section_id)
        stale = newest is not None and newest != int(data.get('newest_added') or 0)
    if stale:
        with _lock:
            go = section_id not in _inflight
            if go:
                _inflight.add(section_id)
        if go:
            (spawn or (lambda f: threading.Thread(target=f, daemon=True).start()))(lambda: _refresh(section_id))
    return [m['rating_key'] for m in (data or {}).get('movies') or [] if m.get('rating_key')]


# --- routes -------------------------------------------------------------------

def _user():
    u = _cfg['get_current_user']()
    if not u:
        return None, (jsonify({'error': 'Authentication required', 'sign_in': True}), 401)
    return u, None


def _movie_libraries() -> List[dict]:
    plex = _cfg['get_plex']()
    if plex is None or not getattr(plex, 'is_configured', False):
        return []
    return [{'id': str(s['id']), 'title': s['title']} for s in _cfg['sections'](plex) if s.get('type') == 'movie']


@bp.route('/api/settings/hero', methods=['GET'])
def get_hero():
    _, err = _user()
    if err:
        return err
    return _out()


def _out():
    cur = current()
    return jsonify({**cur, 'libraries': _movie_libraries(),
                    'genres': _dedupe(library_genres(cur['section_id']) + cur['excluded_genres'])})   # v242


@bp.route('/api/settings/hero', methods=['PUT'])
def put_hero():
    user, err = _user()
    if err:
        return err
    if not user.get('is_admin'):
        return jsonify({'error': 'Admin access required'}), 403
    body = request.get_json(silent=True) or {}
    hero: Dict[str, Any] = {}
    if 'source' in body:
        if body['source'] not in SOURCES:
            return jsonify({'error': 'Invalid source'}), 400
        hero['source'] = body['source']
    if 'section_id' in body:
        sid = str(body['section_id'] or '')
        libs = {l['id'] for l in _movie_libraries()}
        if sid and libs and sid not in libs:
            return jsonify({'error': 'Invalid library'}), 400
        hero['library_section_id'] = int(sid) if sid.isdigit() else 0
    if 'limit' in body:
        if isinstance(body['limit'], bool) or not isinstance(body['limit'], (int, float)):
            return jsonify({'error': 'Invalid count'}), 400
        hero['limit'] = clamp_limit(body['limit'])
    if 'excluded_genres' in body:
        raw = body['excluded_genres']
        if not isinstance(raw, list) or len(raw) > MAX_EXCLUDED or not all(isinstance(g, str) and 0 < len(g.strip()) <= 64 for g in raw):
            return jsonify({'error': 'Invalid genres'}), 400
        # v242: any non-empty name up to 64 characters; a valid pick never fails
        hero['excluded_genres'] = excluded(raw)
    if hero:
        _cfg['settings'].update({'ui': {'hero': hero}})
        _cfg['clear_hero']()   # v240: exclusions are applied when the hero is served; no rebuild
    return _out()
