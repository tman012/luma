"""v237: Settings > Home screen hero source and count.

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

_cfg: Dict[str, Any] = {}
_lock = threading.Lock()
_inflight: set = set()


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
            'limit': clamp_limit(s.get('ui.hero.limit', LIMIT_DEFAULT))}


# --- ranking ------------------------------------------------------------------

def _release_year(m: dict) -> Optional[int]:
    d = m.get('release_date') or ''
    if len(d) >= 4 and d[:4].isdigit():
        return int(d[:4])
    return m.get('year')


def rank(movies: List[dict], today: Optional[date] = None) -> List[dict]:
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
    res = plex.get_all_movies(int(section_id), sort='titleSort', limit=POOL_MAX) or {}
    items = [m for m in (res.get('movies') or []) if m.get('tmdb_id') and m.get('artPath')]

    def look(m):
        d = _cfg['tmdb_details'](int(m['tmdb_id'])) or {}
        return {'rating_key': str(m.get('ratingKey') or m.get('rating_key') or ''), 'title': m.get('title'),
                'year': m.get('year'), 'tmdb_id': m['tmdb_id'],
                'popularity': float(d.get('popularity') or 0), 'release_date': d.get('release_date') or ''}
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(look, items))
    ranked = rank(rows)
    p = _path(section_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps({'ts': time.time(), 'section_id': section_id, 'movies': ranked}))
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
    if not data or now - float(data.get('ts') or 0) >= DAY_S:
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
    return jsonify({**current(), 'libraries': _movie_libraries()})


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
    if hero:
        _cfg['settings'].update({'ui': {'hero': hero}})
        _cfg['clear_hero']()
    return jsonify({**current(), 'libraries': _movie_libraries()})
