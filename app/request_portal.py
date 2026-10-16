"""Request page for friends and family (/request).

One shared password unlocks a 30-day "requester" cookie. A requester can only
search TMDB, see whether a title is already in the owner's Plex (excluding the
excluded libraries, e.g. Full Rips and 4K), and request it:

  movie   -> POST /api/watchlist path (ensure_movie_monitored)
  new show / season / episode -> POST /api/tv/watchlist path
  show already in Sonarr      -> sonarr_status.set_series_monitor_mode('all')   (SeriesSearch)
  season already in Sonarr    -> sonarr_status.set_season_monitored(True)       (SeasonSearch)
  episodes already in Sonarr  -> sonarr_status.set_episodes_monitored(True)     (EpisodeSearch)

Everything else is refused server-side for a requester (see _requester_guard).
Requests go through the owner's own code paths (same profile, root folder,
monitoring and search behavior as Luma's Watchlist / Sources), tagged 'Requested'
in Activity with the requester's name.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Blueprint, current_app, g, jsonify, make_response, redirect, request, send_from_directory
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

bp = Blueprint('request_portal', __name__)

COOKIE = 'luma_req'
MAX_AGE = 30 * 86400
TMDB = 'https://api.themoviedb.org/3'

_settings = None          # SettingsManager, injected by init_app
_record_origin = None     # main._record_origin, injected by init_app


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def cfg(key: str, default: Any = None) -> Any:
    return _settings.get(f'request_portal.{key}', default) if _settings else default


def _set(partial: Dict[str, Any]) -> Tuple[bool, List[str]]:
    return _settings.update({'request_portal': partial})


def excluded_libraries() -> List[str]:
    """Return the list of Plex libraries excluded from requester availability checks."""
    v = cfg('exclude_libraries', [])
    return list(v) if isinstance(v, list) else []


# ---------------------------------------------------------------------------
# Requester session (signed cookie; no users row)
# ---------------------------------------------------------------------------

def _serializer() -> URLSafeTimedSerializer:
    """Create a serializer using the Flask secret key (required)."""
    secret = current_app.config['SECRET_KEY']
    if not secret:
        raise RuntimeError("Flask SECRET_KEY is required for request portal")
    return URLSafeTimedSerializer(secret, salt='luma-request')


def current_requester() -> Optional[Dict[str, Any]]:
    if hasattr(g, '_requester'):
        return g._requester
    g._requester = None
    tok = request.cookies.get(COOKIE)
    if tok and cfg('enabled', False) and cfg('password_hash'):
        try:
            data = _serializer().loads(tok, max_age=MAX_AGE)
            if data.get('v') == int(cfg('password_version', 1) or 1):
                g._requester = {'name': data.get('n') or '', 'sid': data.get('i')}
        except BadSignature:
            pass
    return g._requester


def client_ip() -> str:
    """Real client IP using access.client_ip with trusted_proxies from settings."""
    from app import access
    return access.client_ip(request, _settings)


# ---------------------------------------------------------------------------
# Rate limiting for the shared password (per-IP + soft global backoff)
# ---------------------------------------------------------------------------

_fail_lock = threading.Lock()
_fails: Dict[str, List[float]] = {}
_MAX_PER_IP_ENTRIES = 1000  # Cap dict size to prevent memory exhaustion

WINDOW = 15 * 60  # 15 minutes
PER_IP_LIMIT = 5   # Max 5 failures per IP per window
GLOBAL_SOFT_LIMIT = 50  # When global failures exceed this, add extra backoff


def _limited(ip: str) -> int:
    """Check if IP is rate-limited. Returns remaining cooldown seconds, or 0 if not limited."""
    now = time.time()
    with _fail_lock:
        # Evict expired entries
        for k in list(_fails):
            _fails[k] = [t for t in _fails[k] if now - t < WINDOW]
            if not _fails[k]:
                del _fails[k]
        
        # Check per-IP limit (hard limit)
        mine = _fails.get(ip, [])
        if len(mine) >= PER_IP_LIMIT:
            return int(WINDOW - (now - mine[0])) + 1
        
        # Soft global backoff: if total failures across all IPs > threshold, add delay
        total_failures = sum(len(v) for v in _fails.values())
        if total_failures >= GLOBAL_SOFT_LIMIT:
            # Add 30 seconds of backoff when under attack
            if mine and (now - mine[-1]) < 30:
                return 30
    
    return 0


def _fail(ip: str) -> None:
    """Record a failed authentication attempt for an IP."""
    with _fail_lock:
        # Prevent unbounded growth if attacker varies IPs
        if len(_fails) >= _MAX_PER_IP_ENTRIES:
            # Evict oldest entry
            oldest_ip = min(_fails, key=lambda k: _fails[k][0] if _fails[k] else float('inf'))
            del _fails[oldest_ip]
        _fails.setdefault(ip, []).append(time.time())


def _clear(ip: str) -> None:
    """Clear failed attempts for an IP after successful auth."""
    with _fail_lock:
        _fails.pop(ip, None)


# ---------------------------------------------------------------------------
# Server-side role enforcement
# ---------------------------------------------------------------------------

_REQUESTER_PAGES = ('/request',)
_PUBLIC_PREFIXES = ('/api/request/', '/static/request/')
_PUBLIC_EXACT = {'/request', '/request/', '/health', '/static/images/luma_text.png',
                 '/static/images/apple-touch-icon.png', '/static/images/icon-192.png', '/favicon.ico',
                 # so the owner can still reach the normal sign-in from a device that has the cookie
                 '/login', '/api/auth/login', '/api/auth/me', '/api/auth/check-user', '/api/auth/logout',
                 '/manifest.json', '/sw.js',
                 # rating icons used by the request sheet
                 '/static/images/imdb-logo.png', '/static/images/rotten-tomatoes-logo.png',
                 '/static/images/rotten-tomatoes-splat.png', '/static/images/rotten-tomatoes-popcorn-good.png',
                 '/static/images/rotten-tomatoes-popcorn-bad.png'}
_PUBLIC_ASSET_PREFIX = '/assets/'


def _requester_guard():
    """A requester (valid luma_req cookie, no full Luma session) can reach the
    request page and /api/request/* only. Everything else: 403 for APIs,
    redirect to /request for pages."""
    if getattr(g, 'current_user', None):
        return None  # full Luma session: untouched
    path = request.path
    if path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES):
        return None
    if path.startswith(_PUBLIC_ASSET_PREFIX) and request.method == 'GET':
        return None
    if not current_requester():
        return None  # anonymous: unchanged behavior (see report: lock_remote_anonymous)
    if path.startswith('/api/') or path.startswith('/ws') or request.method != 'GET':
        return jsonify({'error': 'forbidden'}), 403
    return redirect('/request')


def _need_requester():
    if getattr(g, 'current_user', None) or current_requester():
        return None
    return jsonify({'error': 'locked'}), 401


def _who() -> str:
    u = getattr(g, 'current_user', None)
    if u:
        return u.get('name') or 'Owner'
    r = current_requester()
    return (r or {}).get('name') or ''  # requesters are anonymous (password only)


# ---------------------------------------------------------------------------
# Plex ownership (excluding Full Rips / 4K)
# ---------------------------------------------------------------------------

_idx_lock = threading.Lock()
_idx: Dict[str, Any] = {}
_IDX_TTL = 600


def _plex():
    from app.plex_service import plex_service
    return plex_service


_idx_building = threading.Lock()


def _refresh_index_bg() -> None:
    """Rebuild the ownership index in the background (at most one build at a time)."""
    if _idx_building.locked():
        return

    def run():
        if not _idx_building.acquire(blocking=False):
            return
        try:
            ownership_index(force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning('[request] ownership index refresh failed: %s', exc)
        finally:
            _idx_building.release()
    threading.Thread(target=run, name='request-index-refresh', daemon=True).start()


def owned_index_nowait() -> Optional[Dict[str, Any]]:
    """Current index (possibly stale) without waiting; kicks off a rebuild when missing/stale."""
    with _idx_lock:
        cur = dict(_idx) if _idx else None
    if not cur or time.time() - cur.get('ts', 0) >= _IDX_TTL:
        _refresh_index_bg()
    return cur


def ownership_index(force: bool = False) -> Dict[str, Any]:
    with _idx_lock:
        if not force and _idx:
            if time.time() - _idx.get('ts', 0) >= _IDX_TTL:
                _refresh_index_bg()  # serve stale, rebuild in the background
            return _idx
    plex = _plex()
    excl = {x.strip().lower() for x in excluded_libraries()}
    movies_tmdb, movies_imdb, movies_ty = set(), set(), set()
    shows_tmdb: Dict[int, List[int]] = {}
    shows_tvdb: Dict[int, List[int]] = {}
    libs = []
    for sec in plex.get_sections_lean() or []:
        name = (sec.get('title') or '').strip()
        if name.lower() in excl or sec.get('type') not in ('movie', 'show'):
            continue
        libs.append(name)
        off = 0
        while True:
            if sec['type'] == 'movie':
                res = plex.get_all_movies(sec['id'], limit=500, offset=off, timeout=60)
                for it in res.get('movies', []):
                    if it.get('tmdb_id'):
                        movies_tmdb.add(int(it['tmdb_id']))
                    if it.get('imdb_id'):
                        movies_imdb.add(str(it['imdb_id']))
                    if not it.get('tmdb_id') and not it.get('imdb_id') and it.get('title'):
                        movies_ty.add(f"{it['title'].lower()}|{it.get('year')}")
            else:
                res = plex.get_all_shows(sec['id'], limit=500, offset=off, timeout=60)
                for it in res.get('shows', []):
                    rk = it.get('ratingKey') or it.get('rating_key')
                    for mid in (it.get('tmdb_ids') or ([it['tmdb_id']] if it.get('tmdb_id') else [])):
                        shows_tmdb.setdefault(int(mid), []).append(rk)
                    for vid in (it.get('tvdb_ids') or ([it['tvdb_id']] if it.get('tvdb_id') else [])):
                        shows_tvdb.setdefault(int(vid), []).append(rk)
            if not res.get('has_more'):
                break
            off += 500
    new = {'ts': time.time(), 'libraries': libs, 'movies_tmdb': movies_tmdb, 'movies_imdb': movies_imdb,
           'movies_ty': movies_ty, 'shows_tmdb': shows_tmdb, 'shows_tvdb': shows_tvdb}
    with _idx_lock:
        _idx.clear()
        _idx.update(new)
    logger.info('[request] ownership index: %d movies, %d shows from %s',
                len(movies_tmdb), len(shows_tmdb), ', '.join(libs))
    return _idx


def movie_in_plex(tmdb_id: int, imdb_id: str = '', title: str = '', year=None) -> bool:
    idx = ownership_index()
    if int(tmdb_id) in idx['movies_tmdb'] or (imdb_id and imdb_id in idx['movies_imdb']):
        return True
    return bool(title) and f"{title.lower()}|{year}" in idx['movies_ty']


def show_episodes_in_plex(tmdb_id: int, tvdb_id: Optional[int]) -> set:
    idx = ownership_index()
    rks = list(idx['shows_tmdb'].get(int(tmdb_id), []))
    if tvdb_id:
        rks += idx['shows_tvdb'].get(int(tvdb_id), [])
    have = set()
    for rk in dict.fromkeys(rks):
        try:
            for ep in _plex().get_show_episodes(int(rk)) or []:
                s, e = ep.get('season_number'), ep.get('episode_number')
                if s is not None and e is not None:
                    have.add((int(s), int(e)))
        except Exception as exc:  # noqa: BLE001
            logger.warning('[request] plex episodes failed for %s: %s', rk, exc)
    return have


# ---------------------------------------------------------------------------
# TMDB
# ---------------------------------------------------------------------------

def _tmdb(path: str, **params) -> Optional[Dict[str, Any]]:
    key = _settings.get('apis.tmdb.api_key') if _settings else ''
    key = key or os.environ.get('TMDB_API_KEY', '')
    if not key:
        return None
    try:
        r = requests.get(f'{TMDB}{path}', params={'api_key': key, 'language': 'en-US', **params}, timeout=10)
        return r.json() if r.ok else None
    except requests.RequestException:
        return None


def _year(d: Optional[str]) -> Optional[int]:
    return int(d[:4]) if d and d[:4].isdigit() else None


# ---------------------------------------------------------------------------
# Radarr / Sonarr access (reads only; writes go through the owner code paths)
# ---------------------------------------------------------------------------

class Arr:
    def __init__(self, kind: str):
        self.kind = kind
        self.url = (_settings.get(f'{kind}.url') or '').rstrip('/')
        self.key = _settings.get(f'{kind}.api_key') or ''

    @property
    def ok(self) -> bool:
        return bool(self.url and self.key)

    def get(self, path: str, **params):
        r = requests.get(f'{self.url}/api/v3/{path}', params=params, headers={'X-Api-Key': self.key}, timeout=30)
        r.raise_for_status()
        return r.json()


_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (now, val)
    return val


def _bust(prefix: str) -> None:
    with _cache_lock:
        for k in [k for k in _cache if k.startswith(prefix)]:
            del _cache[k]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def movie_status(tmdb_id: int, imdb_id: str = '', title: str = '', year=None) -> Dict[str, Any]:
    if movie_in_plex(tmdb_id, imdb_id, title, year):
        return {'state': 'available'}
    radarr = Arr('radarr')
    if not radarr.ok:
        return {'state': 'requestable'}
    found = radarr.get('movie', tmdbId=int(tmdb_id))
    m = next((x for x in found if x.get('tmdbId') == int(tmdb_id)), None) if isinstance(found, list) else None
    if not m:
        return {'state': 'requestable'}
    hs = held_stage('movie', tmdb_id)
    if hs:
        return dict(hs, state='downloading', radarr_id=m['id'])
    # (Radarr queue rows alone don't count as downloading: only what Activity shows in flight, above.)
    if m.get('monitored'):
        return {'state': 'requested', 'radarr_id': m['id']}
    return {'state': 'requestable', 'radarr_id': m['id']}


def _sonarr_series(sonarr: Arr, tvdb_id: Optional[int], tmdb_id: int) -> Optional[Dict[str, Any]]:
    allser = _cached('sonarr:series', 60, lambda: sonarr.get('series'))
    for s in allser:
        if tvdb_id and s.get('tvdbId') == int(tvdb_id):
            return s
    for s in allser:
        if s.get('tmdbId') == int(tmdb_id):
            return s
    return None


def tv_state(tmdb_id: int, details: Dict[str, Any]) -> Dict[str, Any]:
    """Per-episode state for a show: {(s,e): state}, plus Sonarr ids."""
    tvdb_id = (details.get('external_ids') or {}).get('tvdb_id')
    have = show_episodes_in_plex(tmdb_id, tvdb_id)
    sonarr = Arr('sonarr')
    series, eps, queued = None, {}, set()
    hold = _HOLD.get(('tv', int(tmdb_id))) or {}
    live = set(hold.get('hashes') or ())
    if sonarr.ok:
        series = _sonarr_series(sonarr, tvdb_id, tmdb_id)
        if series:
            for ep in _cached(f'sonarr:eps:{series["id"]}', 30, lambda: sonarr.get('episode', seriesId=series['id'])):
                eps[(ep['seasonNumber'], ep['episodeNumber'])] = ep
            for q in _cached(f'sonarr:queue:{series["id"]}', 30,
                             lambda: sonarr.get('queue/details', seriesId=series['id'], includeEpisode='true')):
                ep = q.get('episode') or {}
                # Only what Activity shows as in flight counts; stale/stuck Sonarr queue rows don't.
                if ep and (q.get('downloadId') or '').lower() in live:
                    queued.add((ep.get('seasonNumber'), ep.get('episodeNumber')))
    # imported by Sonarr but not in Plex yet: still in flight (Processing...), never available/requested
    for x in hold.get('epset') or []:
        if tuple(x) not in have:
            queued.add(tuple(x))
    return {'tvdb_id': tvdb_id, 'have': have, 'series': series, 'eps': eps, 'queued': queued}


def ep_state(key: Tuple[int, int], st: Dict[str, Any], aired: bool) -> str:
    if key in st['have']:
        return 'available'
    if key in st['queued']:
        return 'downloading'
    ep = st['eps'].get(key)
    series = st['series']
    if ep and ep.get('hasFile'):
        return 'requested'
    if ep and ep.get('monitored') and series and series.get('monitored'):
        return 'requested'
    return 'requestable' if aired else 'upcoming_requestable'


def _aired_count(details: Dict[str, Any], season: int, ep_count: int) -> int:
    last = details.get('last_episode_to_air') or {}
    ls, le = last.get('season_number'), last.get('episode_number')
    if ls is None:
        return 0
    if season < ls:
        return ep_count
    if season == ls:
        return min(ep_count, le or 0)
    return 0


def _summarize(states: List[str]) -> str:
    if not states:
        return 'upcoming'
    if all(s == 'available' for s in states):
        return 'available'
    if any(s == 'downloading' for s in states):
        return 'downloading'
    rest = [s for s in states if s != 'available']
    if all(s in ('requested',) for s in rest):
        return 'requested'
    if any(s == 'available' for s in states):
        return 'partial'
    return 'requestable'


# ---------------------------------------------------------------------------
# Request log
# ---------------------------------------------------------------------------

def _log_path() -> str:
    base = '/app/data' if os.path.isdir('/app/data') else os.path.dirname(os.environ.get('DATABASE_PATH', '') or '.')
    return os.path.join(base, 'request_log.json')


_log_lock = threading.Lock()


def _read_log() -> List[Dict[str, Any]]:
    import json
    try:
        with open(_log_path()) as f:
            v = json.load(f)
            return v if isinstance(v, list) else []
    except Exception:
        return []


def _append_log(entry: Dict[str, Any]) -> None:
    import json
    with _log_lock:
        items = _read_log()
        items.insert(0, entry)
        del items[2000:]
        tmp = _log_path() + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(items, f, indent=1, default=str)
        os.replace(tmp, _log_path())


def _update_log(entry_id: str, **fields) -> None:
    import json
    with _log_lock:
        items = _read_log()
        for it in items:
            if it.get('id') == entry_id:
                it.update(fields)
                break
        tmp = _log_path() + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(items, f, indent=1, default=str)
        os.replace(tmp, _log_path())


def _log_request(kind: str, tmdb_id: int, title: str, year, scope: str, action: str,
                 ops: List[Dict[str, Any]], **extra) -> Dict[str, Any]:
    entry = {
        'id': uuid.uuid4().hex[:12], 'ts': time.time(),
        'at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'who': _who() or client_ip(), 'ip': client_ip(),
        'ua': (request.headers.get('User-Agent') or '')[:160],
        'kind': kind, 'tmdb_id': tmdb_id, 'title': title, 'year': year, 'scope': scope,
        'action': action, 'steps': ops,
        'admin': bool((getattr(g, 'current_user', None) or {}).get('is_admin')), **extra,
    }
    _append_log(entry)
    if action != 'error' and tmdb_id:
        with _cache_lock:
            hit = _cache.get('wanted')
            if hit:
                hit[1].setdefault('movie' if kind == 'movie' else 'tv', set()).add(int(tmdb_id))
    logger.info('[request] %s requested %s %s (%s) scope=%s action=%s',
                entry['who'], kind, title, tmdb_id, scope, action)
    return entry


# ---------------------------------------------------------------------------
# Routes: page + gate
# ---------------------------------------------------------------------------

@bp.route('/request')
@bp.route('/request/')
def request_page():
    resp = make_response(send_from_directory(os.path.join(current_app.static_folder, 'request'), 'index.html'))
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Referrer-Policy'] = 'same-origin'
    return resp


@bp.route('/api/request/session')
def session_info():
    r = current_requester()
    u = getattr(g, 'current_user', None)
    return jsonify({'enabled': bool(cfg('enabled', False) and cfg('password_hash')),
                    'unlocked': bool(r or u), 'name': (r or {}).get('name') or (u or {}).get('name')})


@bp.route('/api/request/unlock', methods=['POST'])
def unlock():
    if not (cfg('enabled', False) and cfg('password_hash')):
        return jsonify({'error': 'disabled'}), 404
    ip = client_ip()
    wait = _limited(ip)
    if wait:
        return jsonify({'error': 'rate_limited', 'retry_after': wait}), 429
    data = request.get_json(silent=True) or {}
    name = ' '.join(str(data.get('name') or '').split())[:40]
    pw = str(data.get('password') or '')
    if not pw or not check_password_hash(cfg('password_hash'), pw):
        _fail(ip)
        logger.info('[request] wrong password from %s', ip)
        return jsonify({'error': 'wrong_password'}), 401
    _clear(ip)
    tok = _serializer().dumps({'n': name, 'v': int(cfg('password_version', 1) or 1), 'i': uuid.uuid4().hex[:10]})
    resp = jsonify({'ok': True, 'name': name})
    resp.set_cookie(COOKIE, tok, max_age=MAX_AGE, httponly=True, samesite='Lax',
                    secure=request.headers.get('X-Forwarded-Proto', request.scheme) == 'https', path='/')
    logger.info('[request] unlocked from %s', ip)
    return resp


@bp.route('/api/request/signout', methods=['POST'])
def signout():
    resp = jsonify({'ok': True})
    resp.delete_cookie(COOKIE, path='/')
    return resp


# ---------------------------------------------------------------------------
# Routes: search + detail
# ---------------------------------------------------------------------------

@bp.route('/api/request/search')
def search():
    err = _need_requester()
    if err:
        return err
    q = (request.args.get('q') or '').strip()[:100]
    if len(q) < 2:
        return jsonify({'results': []})
    kind = request.args.get('type')
    if kind in ('movie', 'tv'):
        data = _tmdb(f'/search/{kind}', query=q, include_adult='false', page=1) or {}
        for r in data.get('results', []):
            r['media_type'] = kind
    else:
        data = _tmdb('/search/multi', query=q, include_adult='false', page=1) or {}
    out = []
    idx = None
    try:
        idx = owned_index_nowait()  # never wait on a cold index
    except Exception as exc:  # noqa: BLE001
        logger.warning('[request] ownership index failed: %s', exc)
    for r in data.get('results', []):
        mt = r.get('media_type')
        if mt not in ('movie', 'tv'):
            continue
        title = r.get('title') or r.get('name') or ''
        date = r.get('release_date') or r.get('first_air_date')
        fl = _flags(idx, mt, r['id']) if idx else {'in_library': False, 'wanted': False}
        out.append({'id': r['id'], 'type': mt, 'title': title, 'year': _year(date),
                    'poster': r.get('poster_path'), **fl,
                    'popularity': r.get('popularity') or 0})
    return jsonify({'results': out[:24]})


_disc: Dict[str, Dict[str, Any]] = {}
_disc_lock = threading.Lock()
DISCOVER_TTL = 3600


def _disc_item(r: Dict[str, Any], mt: str) -> Dict[str, Any]:
    return {'id': r['id'], 'type': mt, 'title': r.get('title') or r.get('name') or '',
            'year': _year(r.get('release_date') or r.get('first_air_date')),
            'poster': r.get('poster_path'), 'backdrop': r.get('backdrop_path')}


ANIME_LANGS = {'ja', 'zh', 'ko'}


def _is_anime(r: Dict[str, Any]) -> bool:
    """Excluded from hero/Trending/Popular: foreign-language titles and anime."""
    lang = r.get('original_language') or ''
    return lang != 'en' or (16 in (r.get('genre_ids') or []) and lang in ANIME_LANGS)


def _build_discover(kind: str) -> Dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    if kind == 'movie':
        year = int(time.strftime('%Y'))
        calls = [('hero', '/trending/movie/day', {}), ('Trending Movies', '/trending/movie/week', {}),
                 ('Popular Movies', '/movie/popular', {'region': 'US'}), ('Coming Soon', '/movie/upcoming', {'region': 'US'})]
    else:
        calls = [('hero', '/trending/tv/day', {}), ('Trending Shows', '/trending/tv/week', {}),
                 ('Popular Shows', '/tv/popular', {})]
    def fetch(c):
        rows = (_tmdb(c[1], **c[2]) or {}).get('results') or []
        if c[0] == 'hero' or c[0].startswith(('Trending', 'Popular')):
            # Hero / Trending / Popular: English-language only and no anime (Western animation like
            # Bluey or The Simpsons stays); backfill from later pages so the rail stays full.
            keep = [r for r in rows if not _is_anime(r)]
            page = 2
            while len(keep) < 20 and page <= 5:
                more = (_tmdb(c[1], page=page, **c[2]) or {}).get('results') or []
                if not more:
                    break
                rows += more
                keep += [r for r in more if not _is_anime(r)]
                page += 1
            gone = [r.get('name') or r.get('title') for r in rows if _is_anime(r)]
            if gone:
                logger.info('[request] %s %s: removed %s', kind, c[0], gone)
            rows = keep[:20]
        return rows
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(fetch, calls))
    hero = []
    for r in res[0]:
        if r.get('backdrop_path') and r.get('overview'):
            hero.append(_disc_item(r, kind))
        if len(hero) >= 7:
            break
    rails = []
    for (title, _, _), rows in zip(calls[1:], res[1:]):
        items = [_disc_item(r, kind) for r in rows if r.get('poster_path')]
        if title == 'Coming Soon':
            items = [x for x in items if (x.get('year') or 0) >= year]
        rails.append({'title': title, 'items': items})
    return {'hero': hero, 'rails': rails}


_disc_refreshing: set = set()


def _refresh_discover(kind: str) -> Optional[Dict[str, Any]]:
    try:
        fresh = _build_discover(kind)
    except Exception as exc:  # noqa: BLE001
        logger.warning('[request] discover %s refresh failed: %s', kind, exc)
        return None
    finally:
        _disc_refreshing.discard(kind)
    if fresh['hero'] or any(r['items'] for r in fresh['rails']):
        with _disc_lock:
            _disc[kind] = {'at': time.time(), 'data': fresh}
        return fresh
    return None


def discover_payload(kind: str) -> Dict[str, Any]:
    """Serve the cached payload immediately; refresh in the background once it's stale."""
    with _disc_lock:
        c = _disc.get(kind)
    if c:
        if time.time() - c['at'] > DISCOVER_TTL and kind not in _disc_refreshing:
            _disc_refreshing.add(kind)
            threading.Thread(target=_refresh_discover, args=(kind,), daemon=True).start()
        return c['data']
    _disc_refreshing.add(kind)
    return _refresh_discover(kind) or {'hero': [], 'rails': []}


WANTED_TTL = 120


def _build_wanted() -> Dict[str, set]:
    """tmdb ids that are requested / monitored in Radarr or Sonarr (or requested on this page)
    - shown as a blue orb until they are actually in Plex."""
    mv, tv = set(), set()
    try:
        for m in Arr('radarr').get('movie') or []:
            if m.get('monitored') and m.get('tmdbId'):  # hasFile != in Plex; _flags drops it once Plex has it
                mv.add(int(m['tmdbId']))
    except Exception as exc:  # noqa: BLE001
        logger.info('[request] radarr wanted failed: %s', exc)
    try:
        for x in _cached('sonarr:series', 60, lambda: Arr('sonarr').get('series')) or []:
            if x.get('monitored') and x.get('tmdbId') and any(z.get('monitored') for z in x.get('seasons') or []):
                tv.add(int(x['tmdbId']))
    except Exception as exc:  # noqa: BLE001
        logger.info('[request] sonarr wanted failed: %s', exc)
    for e in _read_log()[:300]:
        if e.get('action') != 'error' and e.get('tmdb_id') and time.time() - (e.get('ts') or 0) < 30 * 86400:
            (mv if e.get('kind') == 'movie' else tv).add(int(e['tmdb_id']))
    return {'movie': mv, 'tv': tv}


_wanted_building = threading.Lock()


def wanted_index() -> Dict[str, set]:
    """Never blocks a request: serves the last build and refreshes in the background."""
    with _cache_lock:
        hit = _cache.get('wanted')
    if not hit or time.time() - hit[0] > WANTED_TTL:
        if _wanted_building.acquire(blocking=False):
            def run():
                try:
                    v = _build_wanted()
                    with _cache_lock:
                        _cache['wanted'] = (time.time(), v)
                finally:
                    _wanted_building.release()
            threading.Thread(target=run, name='request-wanted', daemon=True).start()
    return hit[1] if hit else {'movie': set(), 'tv': set()}


def _owned(idx, kind: str, tid: int) -> bool:
    return bool(idx) and tid in (idx['movies_tmdb'] if kind == 'movie' else idx['shows_tmdb'])


def _flags(idx, kind: str, tid: int) -> Dict[str, Any]:
    """in_library: in Plex (non-excluded libraries; a show counts once >=1 episode is there).
    wanted: requested/monitored but not in Plex yet (blue orb)."""
    if not idx:
        return {'in_library': None, 'wanted': False}
    if (kind, int(tid)) in _HOLD:  # downloading / processing right now: blue orb, never the green check
        return {'in_library': False, 'wanted': True}
    own = _owned(idx, kind, tid)
    return {'in_library': own, 'wanted': (not own) and tid in wanted_index().get(kind, set())}


def _mark(payload: Dict[str, Any], idx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    def one(it):
        return dict(it, **_flags(idx, it['type'], it['id']))
    return {'hero': [one(x) for x in payload['hero']],
            'rails': [dict(r, items=[one(x) for x in r['items']]) for r in payload['rails'] if r['items']]}


@bp.route('/api/request/discover')
def discover():
    err = _need_requester()
    if err:
        return err
    kind = 'tv' if request.args.get('type') == 'tv' else 'movie'
    resp = jsonify(_mark(discover_payload(kind), owned_index_nowait()))
    resp.headers['Cache-Control'] = 'private, no-cache'
    return resp


@bp.route('/api/request/home')
def home():
    """Movies and TV hero + rails in one response. Never waits on the ownership index:
    in_library is null until it is ready (owned_ready=false) and the page re-fetches."""
    err = _need_requester()
    if err:
        return err
    idx = owned_index_nowait()
    resp = jsonify({'movie': _mark(discover_payload('movie'), idx), 'tv': _mark(discover_payload('tv'), idx),
                    'owned_ready': bool(idx)})
    resp.headers['Cache-Control'] = 'private, no-cache'
    return resp


@bp.route('/api/request/movie/<int:tmdb_id>')
def movie_detail(tmdb_id: int):
    err = _need_requester()
    if err:
        return err
    d = _cached(f'tmdb:movie:{tmdb_id}', 600, lambda: _tmdb(f'/movie/{tmdb_id}', append_to_response='credits,release_dates'))
    if not d or not d.get('id'):
        return jsonify({'error': 'not_found'}), 404
    st = movie_status(tmdb_id, d.get('imdb_id') or '', d.get('title') or '', _year(d.get('release_date')))
    return jsonify({
        'id': tmdb_id, 'type': 'movie', 'title': d.get('title'), 'year': _year(d.get('release_date')),
        'overview': d.get('overview') or '', 'poster': d.get('poster_path'), 'backdrop': d.get('backdrop_path'),
        'runtime': d.get('runtime'), 'genres': [x['name'] for x in d.get('genres', [])][:3],
        'released': bool(d.get('status') == 'Released'), 'status': st,
        'cert': _movie_cert(d), 'people': _people(d, 'movie'),
    })


def _movie_cert(d: Dict[str, Any]) -> str:
    for r in (d.get('release_dates') or {}).get('results') or []:
        if r.get('iso_3166_1') == 'US':
            for x in r.get('release_dates') or []:
                if x.get('certification'):
                    return x['certification']
    return ''


def _tv_cert(d: Dict[str, Any]) -> str:
    for r in (d.get('content_ratings') or {}).get('results') or []:
        if r.get('iso_3166_1') == 'US' and r.get('rating'):
            return r['rating']
    return ''


def _people(d: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    """Directors / creators first, then top-billed cast (name, role, photo path)."""
    out: List[Dict[str, Any]] = []
    if kind == 'movie':
        cr = d.get('credits') or {}
        for c in cr.get('crew') or []:
            if c.get('job') == 'Director' and len(out) < 2:
                out.append({'name': c.get('name'), 'role': 'Director', 'photo': c.get('profile_path')})
        cast = [{'name': c.get('name'), 'role': c.get('character') or '', 'photo': c.get('profile_path')} for c in cr.get('cast') or []]
    else:
        for c in (d.get('created_by') or [])[:2]:
            out.append({'name': c.get('name'), 'role': 'Creator', 'photo': c.get('profile_path')})
        cr = d.get('aggregate_credits') or {}
        cast = [{'name': c.get('name'), 'role': ((c.get('roles') or [{}])[0].get('character') or ''), 'photo': c.get('profile_path')}
                for c in cr.get('cast') or []]
    return out + cast[:18]


def _call_view(name: str, path: str, args: Dict[str, Any], **view_args) -> Tuple[int, Dict[str, Any]]:
    """Run one of Luma's own read-only views (ratings, logo, colors) and return (status, json)."""
    app = current_app._get_current_object()
    with app.test_request_context(path, query_string=args, method='GET'):
        try:
            rv = app.make_response(app.view_functions[name](**view_args))
            return rv.status_code, (rv.get_json(silent=True) or {})
        except Exception as exc:  # noqa: BLE001
            logger.info('[request] %s failed: %s', name, exc)
            return 500, {}


LOOK_TTL = 7 * 86400     # clearlogo + color blur: art rarely changes
SCORES_TTL = 12 * 3600   # IMDb / RT scores


def _in_app(app, fn, *a, **kw):
    with app.app_context():
        return fn(*a, **kw)


def _build_look(app, kind: str, tmdb_id: int) -> Dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        fl = ex.submit(_in_app, app, _call_view, 'get_clearlogo', f'/api/clearlogo/{tmdb_id}', {'type': kind}, tmdb_id=str(tmdb_id))
        fc = ex.submit(_in_app, app, _call_view, 'get_tmdb_colors', f'/api/tmdb/colors/{tmdb_id}', {'type': kind}, tmdb_id=str(tmdb_id))
        lg, co = fl.result()[1], fc.result()[1]
    logo = (lg.get('clearlogo_url') or '').replace('/t/p/original/', '/t/p/w500/') or None
    colors = co if any(co.get(k) for k in ('topLeft', 'topRight', 'bottomLeft', 'bottomRight', 'blobs')) else None
    return {'logo': logo, 'colors': colors}


def _title_year(kind: str, tmdb_id: int, title: str, year: Optional[int]) -> Tuple[str, Optional[int]]:
    if title:
        return title, year
    d = _cached(f'tmdb:basic:{kind}:{tmdb_id}', 86400, lambda: _tmdb(f'/{kind}/{tmdb_id}')) or {}
    return (d.get('title') or d.get('name') or ''), _year(d.get('release_date') or d.get('first_air_date'))


from concurrent.futures import ThreadPoolExecutor as _TPE, wait as _fwait
_SCORE_POOL = _TPE(max_workers=6, thread_name_prefix='request-scores')  # bounded: never starves request threads
_BG_SCORE_POOL = _TPE(max_workers=2, thread_name_prefix='request-scores-bg')  # warm-up / prewarm only
_bg_local = threading.local()
SCORE_SOURCE_TIMEOUT = 2.0


def _scores_from(r: Dict[str, Any], rt: Dict[str, Any]) -> Dict[str, Any]:
    r = r if r and not r.get('error') else {}
    out = {k: (r.get(k) or {}).get('score') for k in ('imdb', 'rotten_tomatoes_critic', 'rotten_tomatoes_audience')}
    rt = rt or {}
    if out['rotten_tomatoes_critic'] is None:
        out['rotten_tomatoes_critic'] = rt.get('critic_score')
    if out['rotten_tomatoes_audience'] is None:
        out['rotten_tomatoes_audience'] = rt.get('audience_score')
    return out


def _build_scores(app, kind: str, tmdb_id: int, title: str, year: Optional[int]) -> Dict[str, Any]:
    """OMDb/IMDb ratings and the RT lookup run in parallel with a hard per-source wait.
    Whatever is ready is returned (partial=True if a source is still running); the slow
    source keeps going in the background and completes the cache entry when it lands."""
    title, year = _title_year(kind, tmdb_id, title, year)
    if not title:
        return {'imdb': None, 'rotten_tomatoes_critic': None, 'rotten_tomatoes_audience': None}
    yq = {'year': year} if year else {}
    pool = _BG_SCORE_POOL if getattr(_bg_local, 'bg', False) else _SCORE_POOL
    f_om = pool.submit(_in_app, app, _call_view, 'get_ratings', f'/api/ratings/{title}', yq, title=title)
    f_rt = pool.submit(_in_app, app, _call_view, 'get_rt_reviews', '/api/rt/reviews', {'title': title, **yq, 'type': kind})
    _fwait([f_om, f_rt], timeout=SCORE_SOURCE_TIMEOUT)
    get = lambda f: (f.result()[1] if f.done() and not f.exception() else {})  # noqa: E731
    out = _scores_from(get(f_om), get(f_rt))
    if not (f_om.done() and f_rt.done()):
        out['partial'] = True
        key = f'scores:{kind}:{tmdb_id}'

        def finish(_f):
            if f_om.done() and f_rt.done():
                full = _scores_from(get(f_om), get(f_rt))
                with _cache_lock:
                    _cache[key] = (time.time(), full)
        f_om.add_done_callback(finish)
        f_rt.add_done_callback(finish)
    return out


def get_look(app, kind, tmdb_id):
    return _cached(f'look:{kind}:{tmdb_id}', LOOK_TTL, lambda: _build_look(app, kind, tmdb_id))


def get_scores(app, kind, tmdb_id, title='', year=None):
    key = f'scores:{kind}:{tmdb_id}'
    with _cache_lock:
        hit = _cache.get(key)
    if hit and (time.time() - hit[0] < (15 if hit[1].get('partial') else SCORES_TTL)):
        return hit[1]
    return _cached(key, 0, lambda: _build_scores(app, kind, tmdb_id, title, year))


def _extras_args():
    kind = request.view_args.get('kind')
    if kind not in ('movie', 'tv'):
        return None
    return kind, (request.args.get('title') or '').strip()[:200], request.args.get('year', type=int)


def _cached_json(data, max_age):
    resp = jsonify(data)
    resp.headers['Cache-Control'] = f'private, max-age={max_age}'
    return resp


@bp.route('/api/request/look/<kind>/<int:tmdb_id>')
def look(kind: str, tmdb_id: int):
    """Clearlogo + color blur only (fast path, cached 7 days)."""
    err = _need_requester()
    if err:
        return err
    if not _extras_args():
        return jsonify({'error': 'bad_type'}), 400
    return _cached_json(get_look(current_app._get_current_object(), kind, tmdb_id), 86400)


@bp.route('/api/request/scores/<kind>/<int:tmdb_id>')
def scores(kind: str, tmdb_id: int):
    """IMDb / RT scores (slower lookups, cached 12h)."""
    err = _need_requester()
    if err:
        return err
    a = _extras_args()
    if not a:
        return jsonify({'error': 'bad_type'}), 400
    sc = get_scores(current_app._get_current_object(), kind, tmdb_id, a[1], a[2])
    return _cached_json({'ratings': sc}, 0 if sc.get('partial') else 3600)


@bp.route('/api/request/extras/<kind>/<int:tmdb_id>')
def extras(kind: str, tmdb_id: int):
    """Combined look + scores (kept for older cached pages)."""
    err = _need_requester()
    if err:
        return err
    a = _extras_args()
    if not a:
        return jsonify({'error': 'bad_type'}), 400
    app = current_app._get_current_object()
    return _cached_json(dict(get_look(app, kind, tmdb_id), ratings=get_scores(app, kind, tmdb_id, a[1], a[2])), 3600)


_prewarm_stats: Dict[str, Any] = {}


def _prewarm(app, kind: str) -> None:
    """Warm look + scores (and TV details) for every hero/rail title so the sheet opens instantly."""
    from concurrent.futures import ThreadPoolExecutor
    with _disc_lock:
        c = _disc.get(kind)
    if not c:
        return
    seen, items = set(), []
    for it in c['data'].get('hero', []) + [x for r in c['data'].get('rails', []) for x in r.get('items', [])]:
        if (it['type'], it['id']) not in seen:
            seen.add((it['type'], it['id']))
            items.append(it)
    t0 = time.time()

    def one(it):
        _bg_local.bg = True
        try:
            get_look(app, it['type'], it['id'])
            get_scores(app, it['type'], it['id'], it.get('title') or '', it.get('year'))
            if it['type'] == 'tv':
                _tv_details(it['id'])
        except Exception as exc:  # noqa: BLE001
            logger.info('[request] prewarm %s %s: %s', it['type'], it['id'], exc)
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(one, items))
    _prewarm_stats[kind] = {'titles': len(items), 'secs': round(time.time() - t0, 1), 'at': time.time()}
    logger.info('[request] prewarmed %d %s titles in %.1fs', len(items), kind, time.time() - t0)


# ---------------------------------------------------------------------------
# Recently Added (downloading first, then newest in Plex). Requester-safe shape:
# id/type/title/year/poster/progress only - never release names, indexers or files.
# ---------------------------------------------------------------------------

RECENT_EXCLUDE = {'4k movies', '4k tv', 'full rips', '3d', 'remux tv'}
QUEUE_TTL, PLEX_RECENT_TTL, RECENT_MAX = 30, 180, 20


def _basic(kind: str, tmdb_id: int) -> Dict[str, Any]:
    d = _cached(f'tmdb:basic:{kind}:{tmdb_id}', 86400, lambda: _tmdb(f'/{kind}/{tmdb_id}')) or {}
    return {'id': int(tmdb_id), 'type': kind, 'title': d.get('title') or d.get('name') or '',
            'year': _year(d.get('release_date') or d.get('first_air_date')), 'poster': d.get('poster_path')}


IMPORT_WINDOW = 2 * 3600
_QB_ACTIVE = {'downloading', 'stalledDL', 'metaDL', 'queuedDL', 'forcedDL', 'checkingDL', 'forcedMetaDL'}


def _qbit_active() -> Optional[Dict[str, float]]:
    """hash -> progress for torrents qBittorrent is actually still downloading (None if unreachable)."""
    try:
        from app import tracker_files
        tor = tracker_files.fetch_torrents()
    except Exception as exc:  # noqa: BLE001
        logger.info('[request] qbit read failed: %s', exc)
        return None
    if tor is None:
        return None
    out = {}
    now = time.time()
    for t in tor:
        h, prog = (t.get('hash') or '').lower(), float(t.get('progress') or 0)
        if t.get('state') in _QB_ACTIVE and prog < 1:
            out[h] = {'progress': prog, 'done': False, 'size': float(t.get('size') or t.get('total_size') or 0)}
        elif prog >= 1 and now - float(t.get('completion_on') or 0) < IMPORT_WINDOW:
            out[h] = {'progress': 1.0, 'done': True, 'size': float(t.get('size') or t.get('total_size') or 0)}  # finished recently: import / rename may be running
    return out


_HOLD: Dict[Any, Dict[str, Any]] = {}   # (kind, tmdb) -> last Activity stage, held until Plex/Sonarr has it
HOLD_MAX = 6 * 3600
COPY_LABEL, PROC_LABEL = 'Copying to library\u2026', 'Processing\u2026'


def _activity_downloads() -> Optional[List[Dict[str, Any]]]:
    """Exactly what Luma's Activity feed shows: the main app's /api/downloads/queue view
    (qBittorrent progress, AutoRenamer copy %, post-download 'importing' bridge)."""
    from flask import current_app
    vf = current_app.view_functions.get('get_downloads_queue')
    if not vf:
        return None
    try:
        with current_app.test_request_context('/api/downloads/queue'):
            r = vf()
        r = r[0] if isinstance(r, tuple) else r
        data = r.get_json() if hasattr(r, 'get_json') else r
        return list((data or {}).get('downloads') or [])
    except Exception as exc:  # noqa: BLE001
        logger.info('[request] activity read failed: %s', exc)
        return None


def _eta_secs(v: Any) -> Optional[int]:
    """Activity's qBittorrent eta ("1h 5m" / "12m" / "<1m"; None when unknown/infinite) -> seconds."""
    if not v or not isinstance(v, str):
        return None
    if v.strip() == '<1m':
        return 30
    m = re.fullmatch(r'\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*', v)
    if not m or not (m.group(1) or m.group(2)):
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60


def _eta_text(secs: Optional[int]) -> Optional[str]:
    if not secs or secs <= 0:
        return None
    if secs < 60:
        return '<1 min left'
    h, mi = secs // 3600, (secs % 3600) // 60
    return f'{h} hr {mi} min left' if h and mi else f'{h} hr left' if h else f'{mi} min left'


def _stage(d: Dict[str, Any]) -> Dict[str, Any]:
    pct = round(float(d.get('progress') or 0) * 100)  # Activity: Math.round(progress * 100)
    st = d.get('status') or 'downloading'
    if st == 'importing':
        return {'progress': 100, 'label': PROC_LABEL, 'bar': False, 'importing': True}
    if st == 'copying':
        return {'progress': pct, 'label': f'{COPY_LABEL} {pct}%', 'bar': True, 'importing': True, 'copy': True}
    eta = d['eta_secs'] if 'eta_secs' in d else _eta_secs(d.get('eta'))
    return {'progress': pct, 'label': None, 'bar': True, 'importing': False, 'eta': _eta_text(eta)}


def _season_poster(tid: int, season: Optional[int]) -> Optional[str]:
    """TMDB poster for one season (cached a day); None -> caller keeps the series poster."""
    if season is None or season < 1:
        return None
    def get():
        return ((_tmdb(f'/tv/{int(tid)}/season/{int(season)}') or {}).get('poster_path')) or ''
    return _cached(f'seasonposter:{tid}:{season}', 86400, get) or None


def _with_season(item: Dict[str, Any], season: Optional[int]) -> Dict[str, Any]:
    sp = _season_poster(item['id'], season) if item.get('type') == 'tv' else None
    return dict(item, poster=sp) if sp else item


def _queue_items() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {'movie': [], 'tv': []}
    acts = _activity_downloads()
    if acts is None:
        return out
    # hash -> (kind, tmdb): Activity's own metadata first, then the Radarr/Sonarr queues (TV packs)
    hmap: Dict[str, Any] = {}
    seasons: Dict[Any, int] = {}
    sonarr_hashes = set()
    epmap: Dict[str, set] = {}
    eptitles: Dict[Any, str] = {}
    for kind, arr_kind, key in (('movie', 'radarr', 'movie'), ('tv', 'sonarr', 'series')):
        arr = Arr(arr_kind)
        if not arr.ok:
            continue
        try:
            q = arr.get('queue/details', **({'includeMovie': 'true'} if kind == 'movie' else {'includeSeries': 'true', 'includeEpisode': 'true'}))
        except Exception as exc:  # noqa: BLE001
            logger.info('[request] %s queue failed: %s', arr_kind, exc)
            continue
        for it in q if isinstance(q, list) else (q or {}).get('records', []):
            h = (it.get('downloadId') or '').lower()
            tid = (it.get(key) or {}).get('tmdbId')
            if h and tid:
                hmap.setdefault(h, (kind, int(tid)))
                if kind == 'tv' and it.get('seasonNumber') is not None:
                    seasons.setdefault((kind, int(tid)), it.get('seasonNumber'))
                if kind == 'tv':
                    sonarr_hashes.add(h)
                    e_ = it.get('episode') or {}
                    if e_.get('episodeNumber') is not None:
                        epmap.setdefault(h, set()).add((e_.get('seasonNumber', it.get('seasonNumber')), e_['episodeNumber']))
                        if e_.get('title'):
                            eptitles[(e_.get('seasonNumber', it.get('seasonNumber')), e_['episodeNumber'])] = e_['title']
    now = time.time()
    groups: Dict[Any, Dict[str, Any]] = {}
    for d in acts:
        h = (d.get('hash') or '').lower()
        k = hmap.get(h)
        if not k and d.get('tmdb_id'):
            k = ('tv' if d.get('media_type') == 'tv' else 'movie', int(d['tmdb_id']))
        if not k:
            continue
        g_ = groups.setdefault(k, {'order': len(groups), 'items': []})
        g_['items'].append(d)
    idx = owned_index_nowait()
    for k, g_ in sorted(groups.items(), key=lambda kv: kv[1]['order']):
        its = g_['items']
        dl = [d for d in its if (d.get('status') or 'downloading') in ('downloading', 'stalled')]
        if dl:  # still downloading: size-weighted qBittorrent progress (one torrent = Activity's number)
            tot = sum(float(d.get('size_total') or 0) for d in dl)
            frac = sum(float(d.get('size_total') or 0) * float(d.get('progress') or 0) for d in dl) / tot if tot \
                else sum(float(d.get('progress') or 0) for d in dl) / len(dl)
            etas = [_eta_secs(d.get('eta')) for d in dl]  # all torrents must finish: the slowest one; any unknown -> hide
            st = _stage({'status': 'downloading', 'progress': frac,
                         'eta_secs': max(etas) if etas and all(etas) else None})
        else:
            cp = [d for d in its if d.get('status') == 'copying']
            st = _stage(cp[0] if cp else its[0])
        prev = _HOLD.get(k, {})
        hst = dict(prev.get('hst') or {})  # per torrent (per episode / per pack) stage, same numbers Activity shows
        for d in its:
            h_ = (d.get('hash') or '').lower()
            if h_:
                hst[h_] = _stage(dict(d, status='downloading') if (d.get('status') or 'downloading') == 'stalled' else d)
        eps_ = sorted({x for d in its for x in epmap.get((d.get('hash') or '').lower(), ())}) or list(prev.get('epset') or [])
        epst = dict(prev.get('epst') or {})
        for h_, st_ in hst.items():
            for (sn_, en_) in epmap.get(h_, ()):
                epst[f'{sn_}-{en_}'] = st_
        _HOLD[k] = dict(st, since=prev.get('since', now), seen=now, hashes={(d.get('hash') or '').lower() for d in its},
                        hst=hst, epset=eps_, epst=epst)
        if k in seasons:
            _HOLD[k]['season'] = seasons[k]
        # tag single/few-episode requests; a whole-season pack (many eps) just gets the season poster
        eptag, sn_dom = (_queue_tag(k[1], eps_) if k[0] == 'tv' else (None, None))
        if eptag and len(eps_) == 1:
            eptag = _with_title(eptag, eptitles.get(tuple(eps_[0])) or _tmdb_ep_title(k[1], *eps_[0]))
        out[k[0]].append(dict(_with_season(_basic(k[0], k[1]), sn_dom or seasons.get(k)), in_library=False, wanted=True, eptag=eptag,
                              ep_stages=_ep_stages(k[1]) if k[0] == 'tv' else None, **st))
    # Left Activity but not in Plex yet (movies) / Sonarr not done importing (TV): keep "Processing..."
    for k, hd in list(_HOLD.items()):
        if k in groups:
            continue
        done = (now - hd['since'] > HOLD_MAX) or (k[0] == 'movie' and _owned(idx, 'movie', k[1])) or \
            (k[0] == 'tv' and not (hd['hashes'] & sonarr_hashes) and now - hd['seen'] > 20 and _tv_hold_in_plex(k[1], hd))
        if done:
            _HOLD.pop(k, None)
            continue
        st = {'progress': 100, 'label': PROC_LABEL, 'bar': False, 'importing': True, 'eta': None}
        hd.update(st)  # sheet / season rows / episode rows read the hold too: Processing... until Plex has it
        hd['epst'] = {e_: dict(st) for e_ in (hd.get('epst') or {})}
        tag_, sn_ = _queue_tag(k[1], [tuple(x) for x in hd.get('epset') or []]) if k[0] == 'tv' else (None, None)
        out[k[0]].append(dict(_with_season(_basic(k[0], k[1]), sn_ or hd.get('season')), in_library=False, wanted=True,
                              eptag=tag_, ep_stages=_ep_stages(k[1]) if k[0] == 'tv' else None, **st))
    logger.info('[request] activity %s -> %s', [(d.get('status'), round(float(d.get('progress') or 0) * 100)) for d in acts], [(x['type'], x['id'], x['progress'], x.get('label'))
                                                         for k_ in ('movie', 'tv') for x in out[k_]])
    return out


def _tv_hold_in_plex(tid: int, hd: Dict[str, Any]) -> bool:
    """TV leaves 'Processing...' only once Plex has every held episode (Sonarr's import / hasFile is not enough:
    the Plex scan can lag). Unknown episode list -> Sonarr done is the best signal we have."""
    eps = [tuple(x) for x in hd.get('epset') or []]
    if not eps:
        return True
    try:
        d = _tv_details(tid) or {}
        have = _cached(f'plex:eps:{tid}', 20, lambda: show_episodes_in_plex(tid, (d.get('external_ids') or {}).get('tvdb_id')))
    except Exception as exc:  # noqa: BLE001
        logger.info('[request] hold plex check failed for %s: %s', tid, exc)
        return False
    return all(e in have for e in eps)


def _ep_stages(tid: int) -> Dict[str, Dict[str, Any]]:
    """{"S-E": stage} for every in-flight episode of a show (pack episodes share the pack's stage)."""
    hd = _HOLD.get(('tv', int(tid))) or {}
    return dict(hd.get('epst') or {})


def _tmdb_ep_title(tid: int, sn: int, en: int) -> Optional[str]:
    sd = _cached(f'tmdb:tv:{tid}:s{sn}', 600, lambda: _tmdb(f'/tv/{tid}/season/{sn}')) or {}
    return next((e.get('name') for e in sd.get('episodes') or [] if e.get('episode_number') == en), None)


def _queue_tag(tid: int, eps: List[Tuple[int, int]]):
    """(badge, season) for an in-flight show: the season with the most episodes in flight wins
    (AHS S4 pack + S9E2 -> 'Season 4'); a pack / full season -> 'Season N', else S09E02-style."""
    if not eps:
        return None, None
    by: Dict[int, List[int]] = {}
    for sn_, en_ in eps:
        by.setdefault(sn_, []).append(en_)
    sn = max(by, key=lambda x: (len(by[x]), x))
    ns = by[sn]
    total = next((int(x.get('episode_count') or 0) for x in ((_tv_details(tid) or {}).get('seasons') or [])
                  if x.get('season_number') == sn), 0)
    if len(ns) > 4 or (total and len(ns) >= total):
        return f'Season {sn}', sn
    return _ep_tag([(sn, n) for n in ns]), sn


def _ep_tag(eps: List[Tuple[int, int]]) -> Optional[str]:
    """S09E02 / S09E02–E04 / S09 · 3 episodes (None for multi-season or nothing)."""
    if not eps or len({s_ for s_, _ in eps}) != 1:
        return None
    sn, ns = eps[0][0], sorted(e for _, e in eps)
    if len(ns) == 1:
        return f'S{sn:02d}E{ns[0]:02d}'
    if ns == list(range(ns[0], ns[-1] + 1)):
        return f'S{sn:02d}E{ns[0]:02d}\u2013E{ns[-1]:02d}'
    return f'S{sn:02d} \u00b7 {len(ns)} episodes'


def held_stage(kind: str, tid: int) -> Optional[Dict[str, Any]]:
    hd = _HOLD.get((kind, int(tid)))
    if not hd:
        return None
    return {x: hd[x] for x in ('progress', 'label', 'bar', 'importing', 'eta') if x in hd}


def _plex_recent() -> Dict[str, List[Dict[str, Any]]]:
    import re
    plex = _plex()
    idx = owned_index_nowait() or {}
    rk_to_tmdb = {str(rk): tid for tid, rks in (idx.get('shows_tmdb') or {}).items() for rk in rks}
    found: Dict[str, Dict[int, int]] = {'movie': {}, 'tv': {}}
    season_of: Dict[int, Optional[int]] = {}
    season_rk: Dict[int, str] = {}
    excl = RECENT_EXCLUDE | {x.strip().lower() for x in excluded_libraries()}
    for sec in plex.get_sections_lean() or []:
        if (sec.get('title') or '').strip().lower() in excl or sec.get('type') not in ('movie', 'show'):
            continue
        root = plex._get(f"/library/sections/{sec['id']}/recentlyAdded",
                         params={'X-Plex-Container-Start': 0, 'X-Plex-Container-Size': 60 if sec['type'] == 'movie' else 300, 'includeGuids': 1})
        if root is None:
            continue
        for el in list(root):
            added = int(el.get('addedAt') or 0)
            if sec['type'] == 'movie':
                tid = next((int(m.group(1)) for gd in el.findall('Guid')
                            for m in [re.match(r'tmdb://(\d+)', gd.get('id') or '')] if m), None)
                kind = 'movie'
            else:
                rk = el.get('grandparentRatingKey') if el.get('type') == 'episode' else \
                    el.get('parentRatingKey') if el.get('type') == 'season' else el.get('ratingKey')
                tid = rk_to_tmdb.get(str(rk))
                kind = 'tv'
            if tid and added > found[kind].get(tid, 0):
                found[kind][tid] = added
                if kind == 'tv':
                    sn = el.get('parentIndex') if el.get('type') == 'episode' else el.get('index') if el.get('type') == 'season' else None
                    season_of[tid] = int(sn) if (sn or '').isdigit() else None
                    srk = el.get('parentRatingKey') if el.get('type') == 'episode' else el.get('ratingKey') if el.get('type') == 'season' else None
                    if srk:
                        season_rk[tid] = str(srk)
                    else:
                        season_rk.pop(tid, None)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for k, v in found.items():
        rows = []
        for tid, added in sorted(v.items(), key=lambda kv: -kv[1])[:RECENT_MAX]:
            item = dict(_with_season(_basic(k, tid), season_of.get(tid) if k == 'tv' else None), progress=None, in_library=True)
            if k == 'tv':
                try:
                    item['eptag'] = _recent_ep_tag(plex, tid, season_of.get(tid), season_rk.get(tid), added)
                except Exception as exc:  # noqa: BLE001
                    logger.info('[request] recent tag failed for %s: %s', tid, exc)
            rows.append(item)
        out[k] = rows
    return out


RECENT_BATCH_S = 6 * 3600  # episodes added within 6 h of the show's latest addition = one "drop"


def _recent_ep_tag(plex, tid: int, season: Optional[int], season_rk: Optional[str], latest: int) -> Optional[str]:
    """Tag for what was just added to Plex in the show's latest season: S01E05 / S01E03\u2013E05 /
    S01 \u00b7 3 episodes; the full season (every TMDB episode) gets no tag, a season-sized partial drop 'Season N'."""
    if not season or not season_rk:
        return None
    root = plex._get(f'/library/metadata/{season_rk}/children')
    if root is None:
        return None
    eps = [(int(e.get('index')), int(e.get('addedAt') or 0)) for e in list(root)
           if e.get('type') == 'episode' and (e.get('index') or '').isdigit()]
    titles = {int(e.get('index')): e.get('title') or '' for e in list(root)
              if e.get('type') == 'episode' and (e.get('index') or '').isdigit()}
    new = sorted({n for n, a in eps if a >= latest - RECENT_BATCH_S})
    if not new:
        return None
    total = next((int(x.get('episode_count') or 0) for x in ((_tv_details(tid) or {}).get('seasons') or [])
                  if x.get('season_number') == season), 0)
    if total and len(new) >= total:
        return f'Season {season}'  # whole season arrived
    if len(new) == len(eps) and len(new) > 4:
        return f'Season {season}'
    tag = _ep_tag([(season, n) for n in new])
    return _with_title(tag, titles.get(new[0]) if len(new) == 1 else None)


def _with_title(tag: Optional[str], title: Optional[str]) -> Optional[str]:
    """'S09E02 · Mr. Jingles' (Luma Continue Watching style); generic 'Episode 2' titles are dropped."""
    t = (title or '').strip()
    if not tag or not t or re.fullmatch(r'(episode|ep\.?)\s*\d+', t, re.I):
        return tag
    return f'{tag} \u00b7 {t}'


_recent_state: Dict[str, Any] = {}


def recent_payload() -> Dict[str, List[Dict[str, Any]]]:
    q = _cached('recent:queue', QUEUE_TTL, _queue_items)
    ready = bool((owned_index_nowait() or {}).get('shows_tmdb'))
    # (v2: Plex rows carry an episode tag)  TV needs the ownership index to map Plex shows; a list built without it is only kept briefly
    pr = _cached(f'recent:plex3:{int(ready)}', PLEX_RECENT_TTL if ready else 10, _plex_recent)
    out = {}
    for k in ('movie', 'tv'):
        idx = owned_index_nowait()
        dl = [x for x in q.get(k, []) if (x.get('poster') or x.get('title'))
              ]
        ids = {x['id'] for x in dl}
        out[k] = (dl + [x for x in pr.get(k, []) if x['id'] not in ids])[:RECENT_MAX + len(dl)]
    return out


def _recent_warm_loop(app) -> None:
    with app.app_context():
        while True:
            try:
                _bust('recent:queue')
                if time.time() - _recent_state.get('plex_at', 0) > PLEX_RECENT_TTL - 15:
                    _bust('recent:plex')
                    _recent_state['plex_at'] = time.time()
                recent_payload()
                wanted_index()
            except Exception as exc:  # noqa: BLE001
                logger.info('[request] recent warm failed: %s', exc)
            time.sleep(QUEUE_TTL - 5)


@bp.route('/api/request/progress')
def progress():
    """Live % / importing for downloading posters only (cached ~2s; same requester-safe shape)."""
    err = _need_requester()
    if err:
        return err
    q = _cached('recent:progress', 2, _queue_items)
    _cache['recent:queue'] = (time.time(), q)  # keep the rail's copy in step
    out = {k: [{'id': x['id'], 'progress': x['progress'], 'importing': bool(x.get('importing')),
                'label': x.get('label'), 'bar': bool(x.get('bar')), 'eta': x.get('eta'),
                'eptag': x.get('eptag'), 'eps': x.get('ep_stages')} for x in v]
           for k, v in q.items()}
    resp = jsonify(out)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.route('/api/request/recent')
def recent():
    err = _need_requester()
    if err:
        return err
    resp = jsonify(recent_payload())
    resp.headers['Cache-Control'] = 'private, no-cache'
    return resp


def _tv_details(tmdb_id: int) -> Optional[Dict[str, Any]]:
    return _cached(f'tmdb:tv:{tmdb_id}', 600, lambda: _tmdb(f'/tv/{tmdb_id}', append_to_response='external_ids,aggregate_credits,content_ratings'))


@bp.route('/api/request/tv/<int:tmdb_id>')
def tv_detail(tmdb_id: int):
    err = _need_requester()
    if err:
        return err
    d = _tv_details(tmdb_id)
    if not d or not d.get('id'):
        return jsonify({'error': 'not_found'}), 404
    st = tv_state(tmdb_id, d)
    seasons, all_states = [], []
    for s in d.get('seasons', []):
        n = s.get('season_number')
        if not n:  # specials are not offered
            continue
        count = s.get('episode_count') or 0
        aired = _aired_count(d, n, count)
        states = [ep_state((n, e), st, e <= aired) for e in range(1, count + 1)]
        aired_states = states[:aired]
        all_states += aired_states
        have = sum(1 for x in states if x == 'available')
        seasons.append({'season': n, 'name': s.get('name') or f'Season {n}', 'episodes': count, 'aired': aired,
                        'available': have, 'getting_ready': sum(1 for x in states if x == 'downloading'), 'status': _summarize(aired_states) if aired else 'upcoming',
                        'year': _year(s.get('air_date')), 'poster': s.get('poster_path')})
    series = st['series']
    whole = _summarize(all_states)
    if series and series.get('monitored') and all(
            x.get('monitored') for x in series.get('seasons', []) if x.get('seasonNumber', 0) > 0) and whole != 'available':
        whole = 'requested' if whole != 'downloading' else whole
    return jsonify({
        'id': tmdb_id, 'type': 'tv', 'title': d.get('name'), 'year': _year(d.get('first_air_date')),
        'overview': d.get('overview') or '', 'poster': d.get('poster_path'), 'backdrop': d.get('backdrop_path'),
        'genres': [x['name'] for x in d.get('genres', [])][:3], 'network': ((d.get('networks') or [{}])[0]).get('name'),
        'ended': d.get('status') in ('Ended', 'Canceled'), 'seasons': seasons, 'status': {'state': whole},
        'cert': _tv_cert(d), 'people': _people(d, 'tv'), 'seasons_count': d.get('number_of_seasons'),
        'first_air_date': d.get('first_air_date') or None,
        'next_ep': ({'season': (d.get('next_episode_to_air') or {}).get('season_number'),
                     'episode': (d.get('next_episode_to_air') or {}).get('episode_number'),
                     'air_date': (d.get('next_episode_to_air') or {}).get('air_date')}
                    if (d.get('next_episode_to_air') or {}).get('air_date') else None),
        'busy_season': None if (getattr(g, 'current_user', None) or {}).get('is_admin') else outstanding_season(tmdb_id, st),
        'in_sonarr': bool(series),
        'stage': held_stage('tv', tmdb_id) if whole == 'downloading' or any(x['status'] == 'downloading' for x in seasons) else None,
    })


@bp.route('/api/request/tv/<int:tmdb_id>/season/<int:season>')
def tv_season(tmdb_id: int, season: int):
    err = _need_requester()
    if err:
        return err
    d = _tv_details(tmdb_id)
    sd = _cached(f'tmdb:tv:{tmdb_id}:s{season}', 600, lambda: _tmdb(f'/tv/{tmdb_id}/season/{season}'))
    if not d or not sd or season < 1:
        return jsonify({'error': 'not_found'}), 404
    st = tv_state(tmdb_id, d)
    today = time.strftime('%Y-%m-%d')
    eps = []
    for e in sd.get('episodes', []):
        n = e.get('episode_number')
        aired = bool(e.get('air_date')) and e['air_date'] <= today
        eps.append({'episode': n, 'name': e.get('name') or f'Episode {n}', 'air_date': e.get('air_date'),
                    'still': e.get('still_path'), 'runtime': e.get('runtime'),
                    'status': ep_state((season, n), st, aired),
                    'tag': f'S{season:02d}E{int(n or 0):02d}'})
    epst = _ep_stages(tmdb_id)
    for e in eps:
        if e['status'] == 'downloading':
            e['stage'] = epst.get(f"{season}-{e['episode']}") or held_stage('tv', tmdb_id)
    return jsonify({'season': season, 'name': sd.get('name') or f'Season {season}', 'episodes': eps})


# ---------------------------------------------------------------------------
# Routes: request
# ---------------------------------------------------------------------------

def _owner_view(name: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Run one of Luma's own owner endpoints (same code path the owner's UI uses)
    in a nested request context; the 'Requested' Activity tag carries the name."""
    view = current_app.view_functions[name]
    g.origin_evidence = f'request page: {_who()}' if _who() else 'request page'
    with current_app.test_request_context(json=payload, method='POST'):
        rv = view()
    resp, code = (rv if isinstance(rv, tuple) else (rv, None))
    code = code or getattr(resp, 'status_code', 200)
    try:
        body = resp.get_json() or {}
    except Exception:  # noqa: BLE001
        body = {}
    return int(code), body


def _record(**kw) -> None:
    rec = current_app.view_functions['add_to_watchlist'].__globals__.get('_record_origin')
    if rec:
        g.origin_evidence = f'request page: {_who()}' if _who() else 'request page'
        rec('requested', **kw)


@bp.route('/api/request/movie/<int:tmdb_id>', methods=['POST'])
def request_movie(tmdb_id: int):
    err = _need_requester()
    if err:
        return err
    d = _tmdb(f'/movie/{tmdb_id}')
    if not d or not d.get('id'):
        return jsonify({'error': 'not_found'}), 404
    title, year = d.get('title') or '', _year(d.get('release_date'))
    st = movie_status(tmdb_id, d.get('imdb_id') or '', title, year)
    if st['state'] in ('available', 'downloading', 'requested'):
        return jsonify({'ok': True, 'state': st['state'], 'already': True})
    payload = {'tmdb_id': tmdb_id, 'title': title, 'year': year}
    if cfg('movie_profile'):
        payload['quality_profile'] = cfg('movie_profile')
    code, body = _owner_view('add_to_watchlist', payload)
    ok = code < 300 and body.get('success')
    if ok and body.get('action') == 'updated':
        _record(tmdb_id=tmdb_id, media_type='movie', title=title)  # owner path only tags fresh adds
    _log_request('movie', tmdb_id, title, year, 'movie', body.get('action') or 'error',
                 [{'path': 'POST /api/watchlist', 'payload': payload, 'status': code,
                   'profile': body.get('profile_name'), 'radarr_id': body.get('radarr_id')}],
                 error=None if ok else body.get('error'))
    _bust('radarr')
    if not ok:
        return jsonify({'error': 'radarr_failed'}), 502
    return jsonify({'ok': True, 'state': 'requested', 'action': body.get('action')})


ONE_SEASON_MSG = 'One season at a time. Come back to request the next one once Season {n} is available.'


def _aired_eps(tmdb_id: int, season: int) -> List[int]:
    sd = _cached(f'tmdb:tv:{tmdb_id}:s{season}', 600, lambda: _tmdb(f'/tv/{tmdb_id}/season/{season}')) or {}
    today = time.strftime('%Y-%m-%d')
    return [e['episode_number'] for e in sd.get('episodes') or [] if e.get('air_date') and e['air_date'] <= today]


def outstanding_season(tmdb_id: int, st: Dict[str, Any]) -> Optional[int]:
    """A season this show already has a request-page request for that isn't fully in Plex yet.
    Requested episodes (or, for a whole-season request, every aired episode) must all be available."""
    for e in _read_log():
        if (e.get('kind') != 'tv' or e.get('tmdb_id') != tmdb_id or e.get('admin')
                or e.get('action') == 'error' or e.get('scope') not in ('season', 'episodes') or e.get('season') is None):
            continue
        n = int(e['season'])
        eps = e.get('episodes') or _aired_eps(tmdb_id, n)
        if any((n, x) not in st['have'] for x in eps):
            return n
    return None


def _sonarr_add_series(d: Dict[str, Any], title: str) -> int:
    """Add a show to Sonarr with the series monitored but no episodes yet (monitor none,
    no search). Same Sonarr client / profile resolution as the owner's Watchlist path."""
    g_main = current_app.view_functions['add_tv_to_watchlist'].__globals__
    tvdb_id = (d.get('external_ids') or {}).get('tvdb_id')
    if not tvdb_id:
        raise RuntimeError('no TVDB id for this show')
    sonarr = g_main['get_sonarr_for_request']()
    if not sonarr:
        raise RuntimeError('Sonarr not configured')
    existing = sonarr.get_series(tvdb_id)
    if existing:
        return existing[0]['id']
    profiles = sonarr.get_quality_profiles() or []
    want = (cfg('tv_profile') or 'TFlix').lower()
    pid = next((p['id'] for p in profiles if (p.get('name') or '').lower() == want), None) \
        or g_main['_resolve_sonarr_profile_id'](profiles) or 1
    res = sonarr.add_series(tvdb_id=tvdb_id, title=title, quality_profile_id=pid,
                            monitor='none', search_for_missing=False, monitored=True)
    if not res or not res.get('id'):
        raise RuntimeError('Sonarr did not add the series')
    return int(res['id'])


def _tv_followup(sid: int, scope: str, season: int, wanted: List[int], attempts: int = 4,
                 wait_s: int = 300) -> Dict[str, Any]:
    """Make sure the series, the requested season (or episodes) are monitored and a search
    was sent. Waits for Sonarr's episode list (fresh adds fill in asynchronously), retries,
    and verifies the flags by reading them back."""
    from app.sonarr_api import sonarr as owner_sonarr
    from app import sonarr_status
    last_err = ''
    deadline = time.time() + wait_s
    eps: List[Dict[str, Any]] = []
    while True:  # wait for the episodes to exist
        eps = [e for e in (owner_sonarr._request('GET', 'episode', {'seriesId': sid}, timeout=20) or [])
               if e.get('seasonNumber') == season]
        if eps and (scope == 'season' or all(any(e.get('episodeNumber') == w for e in eps) for w in wanted)):
            break
        if time.time() > deadline:
            return {'ok': False, 'error': f'Sonarr has no episodes for season {season} yet', 'waited_s': wait_s}
        time.sleep(4)
    ids = [e['id'] for e in eps if scope == 'season' or e.get('episodeNumber') in wanted]
    for n in range(1, attempts + 1):
        try:
            if scope == 'season':
                res = sonarr_status.set_season_monitored(owner_sonarr, sid, season, True)
            else:
                res = sonarr_status.set_episodes_monitored(owner_sonarr, sid, ids, True)
            ser = owner_sonarr._request('GET', f'series/{sid}', timeout=15) or {}
            if not ser.get('monitored'):  # belt and braces: the series flag must be on
                ser['monitored'] = True
                owner_sonarr._request('PUT', f'series/{sid}', ser, timeout=20)
                ser = owner_sonarr._request('GET', f'series/{sid}', timeout=15) or {}
            now = {e['id']: e for e in (owner_sonarr._request('GET', 'episode', {'seriesId': sid}, timeout=20) or [])}
            season_ok = any(x.get('seasonNumber') == season and x.get('monitored')  # episodes need their season on too
                                                 for x in ser.get('seasons') or [])
            eps_ok = all(now.get(i, {}).get('monitored') for i in ids)
            cmd = res.get('commandId')
            if ser.get('monitored') and season_ok and eps_ok and cmd:
                sonarr_status.invalidate(sid, include_series=True)
                return {'ok': True, 'attempt': n, 'commandId': cmd, 'episodeIds': ids,
                        'search': 'SeasonSearch' if scope == 'season' else 'EpisodeSearch'}
            last_err = f'verify failed (series={ser.get("monitored")}, season={season_ok}, eps={eps_ok}, cmd={cmd})'
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        logger.warning('[request] follow-up attempt %d for series %s: %s', n, sid, last_err)
        time.sleep(min(30, 5 * n))
    return {'ok': False, 'error': last_err, 'attempts': attempts}


def _followup_bg(app, entry_id: str, tmdb_id: int, title: str, sid: int, scope: str, season: int,
                 wanted: List[int]) -> None:
    with app.app_context():
        try:
            res = _tv_followup(sid, scope, season, wanted)
        except Exception as exc:  # noqa: BLE001
            res = {'ok': False, 'error': str(exc)}
        res['at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        if res.get('ok'):
            _update_log(entry_id, followup=dict(res, state='done'))
            logger.info('[request] follow-up done for %s (series %s): %s', title, sid, res)
        else:
            _update_log(entry_id, followup=dict(res, state='failed'), action='error')
            logger.error('[request] follow-up FAILED for %s (series %s): %s', title, sid, res)
        _bust('sonarr')


@bp.route('/api/request/tv/<int:tmdb_id>', methods=['POST'])
def request_tv(tmdb_id: int):
    err = _need_requester()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    scope = data.get('scope')
    if scope not in ('show', 'season', 'episodes'):
        return jsonify({'error': 'bad_scope'}), 400
    season = int(data['season']) if data.get('season') is not None else None
    wanted = sorted({int(x) for x in (data.get('episodes') or [])})
    if scope in ('season', 'episodes') and not season:
        return jsonify({'error': 'season_required'}), 400
    if scope == 'episodes' and not wanted:
        return jsonify({'error': 'episodes_required'}), 400

    d = _tv_details(tmdb_id)
    if not d or not d.get('id'):
        return jsonify({'error': 'not_found'}), 404
    title, year = d.get('name') or '', _year(d.get('first_air_date'))
    st = tv_state(tmdb_id, d)
    if not (getattr(g, 'current_user', None) or {}).get('is_admin'):
        if scope == 'show':
            return jsonify({'error': 'one_season', 'message': 'Pick one season at a time.'}), 400
        busy = outstanding_season(tmdb_id, st)
        if busy is not None and busy != season:
            return jsonify({'error': 'season_outstanding', 'season': busy, 'message': ONE_SEASON_MSG.format(n=busy)}), 409
    if scope == 'season':
        aired = _aired_eps(tmdb_id, season)
        if aired and all((season, x) in st['have'] for x in aired):
            return jsonify({'ok': True, 'state': 'available', 'already': True})
    if scope == 'episodes':
        wanted = [e for e in wanted if (season, e) not in st['have']]
        if not wanted:
            return jsonify({'ok': True, 'state': 'available', 'already': True})
    series = st['series']
    steps: List[Dict[str, Any]] = []
    ok = True
    followup = False
    sid = None
    try:
        if not series:
            if scope == 'show':
                # Owner's Watchlist path (admins only: full show, monitor all + search).
                payload = {'tmdb_id': tmdb_id, 'title': title}
                if cfg('tv_profile'):
                    payload['quality_profile'] = cfg('tv_profile')
                code, body = _owner_view('add_tv_to_watchlist', payload)
                steps.append({'path': 'POST /api/tv/watchlist', 'payload': payload, 'status': code,
                              'action': body.get('action'), 'sonarr_id': body.get('sonarr_id')})
                ok = code < 300 and bool(body.get('success'))
                sid = body.get('sonarr_id')
            else:
                # Add the series inert-but-monitored (monitor none, no search), then a
                # follow-up job waits for Sonarr's episode list and monitors + searches
                # exactly the requested season/episodes.
                sid = _sonarr_add_series(d, title)
                steps.append({'path': 'sonarr add_series(monitor=none)', 'sonarr_id': sid})
                _record(tmdb_id=tmdb_id, media_type='tv', title=title, series_id=sid)
                followup = True
        else:
            sid = series['id']
            if scope == 'show':
                from app.sonarr_api import sonarr as owner_sonarr
                from app import sonarr_status
                res = sonarr_status.set_series_monitor_mode(owner_sonarr, sid, 'all')
                steps.append({'path': "sonarr_status.set_series_monitor_mode('all')", 'series_id': sid, 'result': res})
            else:
                if scope == 'episodes' and not any((season, e) in st['eps'] for e in wanted):
                    return jsonify({'error': 'unknown_episode'}), 404
                res = _tv_followup(sid, scope, season, wanted, attempts=2, wait_s=20)
                steps.append({'path': 'monitor+search (verified)', 'series_id': sid, 'result': res})
                ok = bool(res.get('ok'))
            _record(tmdb_id=tmdb_id, media_type='tv', title=title, series_id=sid)
    except Exception as exc:  # noqa: BLE001
        logger.error('[request] sonarr request failed: %s', exc)
        ok = False
        steps.append({'error': str(exc)})
    entry = _log_request('tv', tmdb_id, title, year, scope, scope if ok else 'error', steps, season=season,
                         episodes=wanted or None, new_series=not series,
                         followup={'state': 'pending'} if (ok and followup) else None)
    if ok and followup and sid:
        app = current_app._get_current_object()
        threading.Thread(target=_followup_bg, args=(app, entry['id'], tmdb_id, title, sid, scope, season, wanted),
                         name=f'request-followup-{sid}', daemon=True).start()
    _bust('sonarr')
    try:
        from app import sonarr_status
        sonarr_status.invalidate(series.get('id') if series else None, include_series=True)
    except Exception:  # noqa: BLE001
        pass
    if not ok:
        return jsonify({'error': 'sonarr_failed'}), 502
    return jsonify({'ok': True, 'state': 'requested', 'action': scope})


# ---------------------------------------------------------------------------
# Owner settings (Settings -> Requests)
# ---------------------------------------------------------------------------

def _need_admin():
    u = getattr(g, 'current_user', None)
    if not u:
        return jsonify({'error': 'Authentication required'}), 401
    if not u.get('is_admin'):
        return jsonify({'error': 'Admin access required'}), 403
    return None


@bp.route('/api/request/admin/settings', methods=['GET'])
def admin_get():
    err = _need_admin()
    if err:
        return err
    out = {'enabled': bool(cfg('enabled', False)), 'has_password': bool(cfg('password_hash')),
           'movie_profile': cfg('movie_profile') or '', 'tv_profile': cfg('tv_profile') or '',
           'exclude_libraries': excluded_libraries(),
           'hostname': cfg('hostname') or '', 'public_url': _public_url()}
    try:
        out['radarr_profiles'] = [p['name'] for p in Arr('radarr').get('qualityprofile')]
        out['sonarr_profiles'] = [p['name'] for p in Arr('sonarr').get('qualityprofile')]
        out['libraries'] = [s.get('title') for s in _plex().get_sections_lean() or [] if s.get('type') in ('movie', 'show')]
    except Exception:  # noqa: BLE001
        pass
    return jsonify(out)


@bp.route('/api/request/admin/settings', methods=['PUT'])
def admin_put():
    err = _need_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    partial: Dict[str, Any] = {}
    for k in ('movie_profile', 'tv_profile'):
        if k in data:
            partial[k] = str(data[k] or '').strip()[:80]
    if 'enabled' in data:
        partial['enabled'] = bool(data['enabled'])
    if 'hostname' in data:
        partial['hostname'] = str(data['hostname'] or '').strip().lower()[:120]
    if 'exclude_libraries' in data and isinstance(data['exclude_libraries'], list):
        partial['exclude_libraries'] = [str(x)[:80] for x in data['exclude_libraries']][:20]
    if data.get('password') is not None:
        pw = str(data['password'])
        if len(pw) < 4:
            return jsonify({'error': 'password_too_short'}), 400
        partial['password_hash'] = generate_password_hash(pw)
        partial['password_version'] = int(cfg('password_version', 1) or 1) + 1  # signs everyone out
    ok, errors = _set(partial)
    if not ok:
        return jsonify({'error': 'invalid', 'details': errors}), 400
    with _idx_lock:
        _idx.clear()
    return admin_get()


@bp.route('/api/request/admin/log')
def admin_log():
    err = _need_admin()
    if err:
        return err
    return jsonify(_read_log()[:200])


def _public_url() -> str:
    h = _request_hosts()
    if h:
        return f'https://{h[0]}/'
    return (os.environ.get('APP_URL', '') or '').rstrip('/') + '/request' if os.environ.get('APP_URL') else ''


def _request_hosts() -> List[str]:
    raw = str(cfg('hostname') or '')
    return [x.strip().lower() for x in raw.split(',') if x.strip()]


_HOST_STATIC = ('/static/request/',)
_HOST_FILES = {'/static/images/luma_text.png', '/static/images/apple-touch-icon.png', '/static/images/imdb-logo.png',
               '/static/images/rotten-tomatoes-logo.png', '/static/images/rotten-tomatoes-splat.png',
               '/static/images/rotten-tomatoes-popcorn-good.png', '/static/images/rotten-tomatoes-popcorn-bad.png'}


def init_app(app, settings, record_origin=None) -> None:
    global _settings, _record_origin
    _settings = settings
    _record_origin = record_origin
    app.register_blueprint(bp)
    # Portal host guard is now handled by portal_guard.guard_portal_traffic() in main.py
    app.before_request(_requester_guard)

    def _warm():
        time.sleep(3)
        if not cfg('enabled', False):
            return
        for kind in ('movie', 'tv'):  # home payload first: it only needs TMDB
            _disc_refreshing.add(kind)
            _refresh_discover(kind)
        for kind in ('movie', 'tv'):
            _prewarm(app, kind)
        with _idx_building:  # same lock as background refreshes: one build at a time
            try:
                ownership_index(force=True)
            except Exception as exc:  # noqa: BLE001
                logger.info('[request] ownership warm-up skipped: %s', exc)
        while True:  # keep the home payload fresh so nobody waits on TMDB
            time.sleep(DISCOVER_TTL - 300)
            for kind in ('movie', 'tv'):
                if kind not in _disc_refreshing:
                    _disc_refreshing.add(kind)
                    _refresh_discover(kind)
                _prewarm(app, kind)
    threading.Thread(target=_warm, name='request-index-warm', daemon=True).start()

    def _recent_start():
        time.sleep(20)  # after the ownership index starts building
        if cfg('enabled', False):
            _recent_warm_loop(app)
    threading.Thread(target=_recent_start, name='request-recent-warm', daemon=True).start()
