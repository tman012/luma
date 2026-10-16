"""Nova Agent — tool-use loop replacing the intent-classifier pattern.

Rather than matching the user's question against a fixed enum of intents, the
agent gives Claude a catalog of tools (backend queries + UI actions) and lets
it decide which to call, in what order, and how to compose the answer.

Fast-path commands (smart home, time/date, playback control) still bypass the
agent via regex in router.py — those need sub-200ms latency and deterministic
behavior. Everything else flows here.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

FLASK_BASE = os.getenv('FLASK_BASE', 'http://localhost:5001')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')

_API_URL = 'https://api.anthropic.com/v1/messages'
_MODEL = os.getenv('ALFRED_AGENT_MODEL', 'claude-haiku-4-5-20251001')
_MAX_TOOL_ITERATIONS = 7


def _auth_headers() -> Dict[str, str]:
    return {'X-API-Key': SERVICE_API_KEY} if SERVICE_API_KEY else {}


def _get(path: str, params: Optional[Dict[str, Any]] = None,
         timeout: int = 10) -> Dict[str, Any]:
    try:
        resp = requests.get(
            f'{FLASK_BASE}{path}', params=params,
            headers=_auth_headers(), timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json() or {}
    except Exception as e:
        logger.warning('GET %s failed: %s', path, e)
        return {'_error': str(e)}


# ---------------------------------------------------------------------------
# Library browser — the workhorse for content queries
# ---------------------------------------------------------------------------

_VIBE_CACHE: Dict[str, Any] = {'ts': 0, 'items': []}
_VIBE_TTL = 5 * 60  # 5 minutes (endpoint itself caches 10 min)


def _vibe_library() -> List[Dict[str, Any]]:
    now = time.time()
    if now - _VIBE_CACHE['ts'] < _VIBE_TTL and _VIBE_CACHE['items']:
        return _VIBE_CACHE['items']
    body = _get('/api/library/vibe-engine', timeout=20)
    items = body.get('items') or []
    _VIBE_CACHE['ts'] = now
    _VIBE_CACHE['items'] = items
    return items


def _matches_quality(item: Dict[str, Any], quality: str) -> bool:
    """Best-effort resolution check on Plex items.

    The vibe-engine endpoint doesn't return videoResolution today, so a
    quality filter is a soft hint — we match on library_name (4K Movies,
    Full Rips, etc.) or title-token heuristics.
    """
    q = quality.lower()
    lib = (item.get('library_name') or '').lower()
    title = (item.get('title') or '').lower()
    if q in ('4k', 'uhd', '2160'):
        return '4k' in lib or '2160' in lib or '4k' in title or 'uhd' in title
    if q == 'remux':
        return 'remux' in lib or 'remux' in title or 'full rip' in lib
    if q in ('1080', '1080p', 'hd'):
        return 'movies' in lib and 'recently' not in lib and '4k' not in lib
    return False


def _ts_cutoff(days: Optional[int]) -> Optional[float]:
    if not days:
        return None
    return time.time() - int(days) * 86400


def tool_browse_library(media_type: Optional[str] = None,
                        genre: Optional[str] = None,
                        avoid_genre: Optional[str] = None,
                        quality: Optional[str] = None,
                        watched: Optional[bool] = None,
                        added_since_days: Optional[int] = None,
                        min_rating: Optional[float] = None,
                        year: Optional[int] = None,
                        year_min: Optional[int] = None,
                        year_max: Optional[int] = None,
                        title_contains: Optional[str] = None,
                        sort: Optional[str] = None,
                        limit: int = 20) -> Dict[str, Any]:
    items = _vibe_library()
    out: List[Dict[str, Any]] = []

    mt = (media_type or '').lower()
    if mt in ('movies', 'film', 'films'):
        mt = 'movie'
    if mt in ('shows', 'series', 'tv', 'episode', 'episodes'):
        mt = 'tv'

    cutoff = _ts_cutoff(added_since_days)
    genre_l = genre.lower() if genre else None
    avoid_l = avoid_genre.lower() if avoid_genre else None
    title_l = title_contains.lower() if title_contains else None

    for it in items:
        if mt and (it.get('type') or '').lower() != mt:
            continue
        genres = {str(g).lower() for g in (it.get('genres') or [])}
        if genre_l and genre_l not in genres and not any(genre_l in g for g in genres):
            continue
        if avoid_l and (avoid_l in genres or any(avoid_l in g for g in genres)):
            continue
        if watched is not None:
            vc = int(it.get('view_count') or 0)
            if watched and vc == 0:
                continue
            if not watched and vc > 0:
                continue
        if cutoff is not None:
            added = it.get('added_at') or 0
            if not added or added < cutoff:
                continue
        if min_rating is not None:
            if float(it.get('rating') or 0) < float(min_rating):
                continue
        iy = int(it.get('year') or 0)
        if year is not None and iy != int(year):
            continue
        if year_min is not None and iy < int(year_min):
            continue
        if year_max is not None and iy > int(year_max):
            continue
        if title_l:
            if title_l not in (it.get('title') or '').lower():
                continue
        if quality and not _matches_quality(it, quality):
            continue
        out.append(it)

    # Sort
    s = (sort or '').lower()
    if s == 'added':
        out.sort(key=lambda i: i.get('added_at') or 0, reverse=True)
    elif s == 'rating':
        out.sort(key=lambda i: i.get('rating') or 0, reverse=True)
    elif s == 'year':
        out.sort(key=lambda i: i.get('year') or 0, reverse=True)
    elif s == 'runtime':
        out.sort(key=lambda i: i.get('runtime_minutes') or 0)
    elif s == 'view_count':
        out.sort(key=lambda i: i.get('view_count') or 0, reverse=True)
    else:
        # Default: most-recently-added first
        out.sort(key=lambda i: i.get('added_at') or 0, reverse=True)

    capped = out[:max(1, min(int(limit or 60), 200))]
    return {
        'total_matching': len(out),
        'returned': len(capped),
        'filters_applied': {
            'media_type': mt or None, 'genre': genre, 'avoid_genre': avoid_genre,
            'quality': quality, 'watched': watched,
            'added_since_days': added_since_days, 'min_rating': min_rating,
            'title_contains': title_contains, 'sort': s or 'added',
        },
        'items': [{
            'title': i.get('title'),
            'year': i.get('year'),
            'type': i.get('type'),
            'rating': i.get('rating'),
            'genres': i.get('genres'),
            'runtime_minutes': i.get('runtime_minutes'),
            'view_count': i.get('view_count'),
            'added_at': i.get('added_at'),
            'library_name': i.get('library_name'),
            'tmdb_id': i.get('tmdb_id'),
            'rating_key': i.get('rating_key'),
        } for i in capped],
    }


# ---------------------------------------------------------------------------
# Thin delegates to existing Phase 1/2 handlers
# ---------------------------------------------------------------------------

def _delegate(fn: Callable, **kw) -> Dict[str, Any]:
    """Call a Phase 1/2 handler and return only its data + message for the agent.

    We drop suggested_ui_action — the agent decides UI via its own tools.
    """
    try:
        r = fn(**kw) or {}
    except Exception as e:
        logger.warning('Delegate %s failed: %s', getattr(fn, '__name__', '?'), e)
        return {'_error': str(e)}
    return {
        'summary': r.get('message', ''),
        'data': r.get('data', {}),
        'status': r.get('status', 'ok'),
    }


def _tool_ownership_check(title: str, media_type: Optional[str] = None,
                          quality: Optional[str] = None) -> Dict[str, Any]:
    import re as _re
    from handlers.ownership import _search_plex as _plex_search, library_name_to_quality
    nq = (title or '').strip()
    if not nq:
        return {'found': False, 'matches': []}

    results = _plex_search(nq)

    def _strip_the(s: str) -> str:
        return _re.sub(r'^the\s+', '', s.lower())

    nq_lower = nq.lower()
    # Strict matching only — exact title or exact-minus-leading-"The"
    hits = [r for r in results
            if r['title'].lower() == nq_lower
            or _strip_the(r['title']) == _strip_the(nq)]

    if hits:
        return {
            'found': True,
            'matches': [{
                'title': h.get('title'), 'year': h.get('year'),
                'tmdb_id': h.get('tmdb_id'), 'rating_key': h.get('rating_key'),
                'library_name': h.get('library_name'),
                'quality': library_name_to_quality(h.get('library_name', '')),
                'type': h.get('type'),
            } for h in hits[:5]],
        }
    return {'found': False, 'matches': []}


def _tool_recently_added(media_type: Optional[str] = None,
                         quality: Optional[str] = None,
                         period: Optional[str] = None) -> Dict[str, Any]:
    from handlers.library_recent import handle_library_recent
    return _delegate(handle_library_recent, media_type=media_type,
                     quality=quality, period=period)


def _tool_library_stats() -> Dict[str, Any]:
    from handlers.library_quality import handle_library_stats
    return _delegate(handle_library_stats)


def _tool_watchlist() -> Dict[str, Any]:
    from handlers.watchlist import handle_watchlist_list
    return _delegate(handle_watchlist_list)


def _tool_watchlist_check(title: str) -> Dict[str, Any]:
    from handlers.watchlist import handle_watchlist_check
    return _delegate(handle_watchlist_check, title=title)


def _tool_trending(media_type: Optional[str] = None,
                   window: Optional[str] = None) -> Dict[str, Any]:
    from handlers.discovery import handle_trending
    return _delegate(handle_trending, media_type=media_type, window=window)


def _tool_popular(media_type: Optional[str] = None) -> Dict[str, Any]:
    from handlers.discovery import handle_popular
    return _delegate(handle_popular, media_type=media_type)


def _tool_in_theaters() -> Dict[str, Any]:
    from handlers.discovery import handle_in_theaters
    return _delegate(handle_in_theaters)


def _tool_indexer_top(quality: Optional[str] = None, tracker: Optional[str] = None,
                      sort: Optional[str] = None, min_seeders: Optional[int] = None,
                      since_days: Optional[int] = None,
                      limit: Optional[int] = None) -> Dict[str, Any]:
    from handlers.indexer_top import handle_indexer_top
    return _delegate(handle_indexer_top, quality=quality, tracker=tracker,
                     sort=sort, min_seeders=min_seeders,
                     since_days=since_days, limit=limit)


def _tool_tv_calendar(period: Optional[str] = None) -> Dict[str, Any]:
    from handlers.tv import handle_tv_airing
    return _delegate(handle_tv_airing, period=period)


def _tool_next_episode(title: str) -> Dict[str, Any]:
    from handlers.tv import handle_tv_next_episode
    return _delegate(handle_tv_next_episode, title=title)


def _tool_season_ownership(title: str, season: Optional[int] = None) -> Dict[str, Any]:
    from handlers.tv import handle_tv_season_ownership
    return _delegate(handle_tv_season_ownership, title=title, season=season)


def _tool_vibe_pick(mood: Optional[str] = None) -> Dict[str, Any]:
    from handlers.vibe import handle_vibe_mood, handle_vibe_pick_for_me
    if mood:
        return _delegate(handle_vibe_mood, mood=mood)
    return _delegate(handle_vibe_pick_for_me)


def _tool_continue_watching() -> Dict[str, Any]:
    from handlers.continue_watching import handle_continue_watching_list
    return _delegate(handle_continue_watching_list)


def _tool_now_playing() -> Dict[str, Any]:
    from handlers.playback_control import handle_playback_now_playing
    return _delegate(handle_playback_now_playing)


def _tool_watch_history(period: Optional[str] = None,
                        media_type: Optional[str] = None) -> Dict[str, Any]:
    from handlers.watch_history import handle_watch_history
    return _delegate(handle_watch_history, period=period, media_type=media_type)


def _tool_watch_progress(title: str) -> Dict[str, Any]:
    from handlers.watch_history import handle_watch_progress
    return _delegate(handle_watch_progress, title=title)


def _tool_radarr_releases(title: str,
                           tmdb_id: Optional[int] = None) -> Dict[str, Any]:
    """Search Radarr for available releases (qualities + seeders) for a movie."""
    params: Dict[str, Any] = {}
    if tmdb_id:
        params['tmdb_id'] = tmdb_id
    if title:
        params['title'] = title
    if not params:
        return {'_error': 'title or tmdb_id required'}

    # Interactive Radarr search can take 30-60s
    body = _get('/api/radarr/movie/releases', params=params, timeout=90)
    if '_error' in body:
        return body
    if not body.get('success'):
        return {'_error': body.get('error', 'Radarr search failed')}

    releases = body.get('releases') or []
    # Keep only non-rejected releases, group by quality label
    kept = [r for r in releases if not r.get('rejected')]
    formatted = []
    for r in kept[:30]:
        size_gb = round(r.get('size', 0) / 1_073_741_824, 1) if r.get('size') else None
        formatted.append({
            'title': r.get('title', ''),
            'quality': r.get('quality', 'Unknown'),
            'seeders': r.get('seeders'),
            'size_gb': size_gb,
            'indexer': r.get('indexer', ''),
            'age_days': round(r.get('age', 0) / 1440) if r.get('age') else None,
        })

    # Sort by seeders desc
    formatted.sort(key=lambda x: x.get('seeders') or 0, reverse=True)

    return {
        'total_available': len(kept),
        'total_rejected': len(releases) - len(kept),
        'releases': formatted,
    }


def _tool_storage() -> Dict[str, Any]:
    from handlers.storage import handle_storage
    return _delegate(handle_storage)


def _tool_free_space() -> Dict[str, Any]:
    from handlers.free_space import handle_free_space
    return _delegate(handle_free_space)


def _tool_downloads() -> Dict[str, Any]:
    from handlers.downloads import handle_downloads
    return _delegate(handle_downloads)


def _tool_download_history(limit: int = 25,
                           include_active: bool = True) -> Dict[str, Any]:
    """Recently completed downloads (qBittorrent + auto-renamer + TMDb), plus
    optionally currently-downloading items so the user sees the full picture
    of what's been grabbed and what's in flight.
    """
    cap = max(1, min(int(limit or 25), 50))
    body = _get('/api/downloads/history', params={'limit': cap}, timeout=10)
    history = body.get('history') if isinstance(body, dict) else []
    completed_items: List[Dict[str, Any]] = []
    for h in history or []:
        completed_items.append({
            'title': h.get('tmdb_title') or h.get('torrent_name') or 'Unknown',
            'year': h.get('tmdb_year'),
            'tmdb_id': h.get('tmdb_id'),
            'media_type': h.get('media_type') or 'movie',
            'poster_url': h.get('poster_path'),
            'tracker': h.get('tracker'),
            'completed_at': h.get('completed_timestamp'),
            'success': bool(h.get('success', True)),
            'subtitle': ('completed' if h.get('success', True) else 'failed'),
        })

    active_items: List[Dict[str, Any]] = []
    if include_active:
        from handlers.downloads import handle_downloads
        try:
            dl = handle_downloads() or {}
            for t in (dl.get('data', {}).get('downloads') or [])[:25]:
                pct = t.get('progress')
                active_items.append({
                    'title': t.get('name', 'Unknown'),
                    'subtitle': f"{pct:.0f}% — {t.get('download_speed', '')}",
                    'state': t.get('state'),
                    'progress': pct,
                })
        except Exception as e:
            logger.warning('Active downloads fetch failed: %s', e)

    # Combined: active first, then completed (most recent already first from /history)
    combined = active_items + completed_items
    return {
        'count': len(combined),
        'active_count': len(active_items),
        'completed_count': len(completed_items),
        'items': combined[:cap],
    }


def _tool_sports_score(team: Optional[str] = None) -> Dict[str, Any]:
    from handlers.sports import handle_score
    return _delegate(handle_score, team=team)


def _tool_next_game(team: Optional[str] = None, day: Optional[str] = None) -> Dict[str, Any]:
    from handlers.sports import handle_next_game
    return _delegate(handle_next_game, team=team, day=day)


# ---------------------------------------------------------------------------
# Smart home / weather / news / activity — read tools
# ---------------------------------------------------------------------------

_HA_ON_STATES = {'on', 'open', 'home', 'playing', 'detected', 'active'}


def _tool_smart_home_state(area: Optional[str] = None,
                           entity_type: Optional[str] = None,
                           on_only: Optional[bool] = None) -> Dict[str, Any]:
    """Query Home Assistant entity state.

    `area`: room name (fuzzy match), e.g. "kitchen". Omit for all rooms.
    `entity_type`: light, fan, switch, climate, sensor, binary_sensor.
    `on_only`: True to return only entities currently on/open/active.
    """
    areas_body = _get('/api/ha/areas')
    if not areas_body.get('enabled', True):
        return {'_error': 'Home Assistant is not configured.'}
    all_areas = areas_body.get('areas') or []

    targets = all_areas
    if area:
        q = area.lower().strip()
        targets = [a for a in all_areas
                   if q in (a.get('name') or '').lower()
                   or q in (a.get('id') or '').lower()]
        if not targets:
            return {
                'matched_area': None,
                'available_areas': [a.get('name') for a in all_areas],
            }

    et = (entity_type or '').lower().strip()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for a in targets[:6]:
        body = _get(f"/api/ha/area/{a.get('id')}/entities")
        ents = body.get('entities') or []
        slim: List[Dict[str, Any]] = []
        for e in ents:
            eid = e.get('entity_id') or ''
            domain = eid.split('.', 1)[0] if '.' in eid else ''
            if et and domain != et:
                continue
            state = e.get('state')
            if on_only and state not in _HA_ON_STATES:
                continue
            attrs = e.get('attributes') or {}
            brightness = attrs.get('brightness')
            if isinstance(brightness, (int, float)):
                brightness_pct = round(brightness / 255 * 100)
            else:
                brightness_pct = None
            slim.append({
                'entity_id': eid,
                'name': attrs.get('friendly_name') or eid,
                'state': state,
                'domain': domain,
                'brightness_percent': brightness_pct,
                'temperature': attrs.get('current_temperature') or attrs.get('temperature'),
            })
        out[a.get('name') or a.get('id')] = slim

    # Quick aggregate: how many "on" lights total
    total_on = 0
    for ents in out.values():
        for e in ents:
            if e.get('state') in _HA_ON_STATES:
                total_on += 1

    return {
        'areas_checked': [a.get('name') for a in targets],
        'total_on': total_on,
        'entities_by_area': out,
    }


def _tool_weather() -> Dict[str, Any]:
    body = _get('/api/weather', timeout=8)
    if not body.get('configured', True):
        return {'_error': 'Weather is not configured.'}
    return {
        'location': body.get('location'),
        'units': body.get('units'),
        'current': body.get('current'),
        'forecast': (body.get('forecast') or [])[:5],
        'hourly': (body.get('hourly') or [])[:8],
    }


def _tool_news(limit: int = 5) -> Dict[str, Any]:
    body = _get('/api/news', timeout=8)
    if not body.get('enabled', True):
        return {'_error': 'News feed is not configured.'}
    items = body.get('items') or []
    return {
        'feed_name': body.get('feed_name'),
        'count': len(items),
        'items': [{
            'title': i.get('title'),
            'description': (i.get('description') or '')[:240],
            'published': i.get('published') or i.get('pub_date'),
            'link': i.get('link'),
        } for i in items[:max(1, min(int(limit or 5), 10))]],
    }


def _tool_shield_activity() -> Dict[str, Any]:
    """What's playing / what app is open on the Shield."""
    body = _get('/api/activity', timeout=8)
    if isinstance(body, dict) and body:
        return body
    # Fallback to legacy endpoint
    return _get('/api/adb/shield_activity', timeout=8)


# ---------------------------------------------------------------------------
# UI-action tools — Claude calls these to tell the frontend what to show
# ---------------------------------------------------------------------------

_UI_ACTION_STATE: Dict[str, Any] = {}


def _tool_open_modal(media_type: str, tmdb_id: int,
                     season: Optional[int] = None) -> Dict[str, Any]:
    action: Dict[str, Any] = {
        'action': 'open_modal',
        'media_type': 'tv' if media_type == 'tv' else 'movie',
        'tmdb_id': int(tmdb_id),
    }
    if season is not None:
        action['season'] = int(season)
    _UI_ACTION_STATE['ui_action'] = action
    return {'ok': True, 'action': action}


def _tool_navigate_to(path: str) -> Dict[str, Any]:
    action = {'action': 'navigate', 'path': path}
    _UI_ACTION_STATE['ui_action'] = action
    return {'ok': True, 'action': action}


def _tool_show_filtered_library(quality: Optional[str] = None,
                                genre: Optional[str] = None,
                                media_type: Optional[str] = None) -> Dict[str, Any]:
    qs = []
    if quality:
        qs.append(f'quality={quality}')
    if genre:
        qs.append(f'genre={genre}')
    if media_type:
        mt = 'movie' if media_type in ('movie', 'movies', 'film', 'films') else 'tv'
        qs.append(f'type={mt}')
    path = '/browse' + (('?' + '&'.join(qs)) if qs else '')
    action = {'action': 'navigate', 'path': path}
    _UI_ACTION_STATE['ui_action'] = action
    return {'ok': True, 'action': action}


# ---------------------------------------------------------------------------
# show_results — visual grid overlay
# ---------------------------------------------------------------------------

# Cache poster lookups so a grid of 20 items doesn't hammer TMDb 20 times
# inside one response. 1-hour TTL is plenty.
_POSTER_CACHE: Dict[str, str] = {}
_POSTER_CACHE_AT: Dict[str, float] = {}
_POSTER_TTL = 60 * 60


def _resolve_poster(tmdb_id: int, media_type: str) -> Optional[str]:
    key = f"{media_type}:{tmdb_id}"
    now = time.time()
    cached_ts = _POSTER_CACHE_AT.get(key, 0)
    if cached_ts and now - cached_ts < _POSTER_TTL:
        return _POSTER_CACHE.get(key)
    path = '/api/movie/' if media_type == 'movie' else '/api/tv/'
    body = _get(f'{path}{int(tmdb_id)}', timeout=8)
    poster = body.get('poster_path') if isinstance(body, dict) else None
    if poster:
        _POSTER_CACHE[key] = poster
        _POSTER_CACHE_AT[key] = now
        return poster
    return None


def _tool_show_results(title: str,
                       items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Display a titled grid of media tiles in the kiosk overlay.

    If `items` is omitted or empty, falls back to the most recent
    list-shaped tool result from this agent run (browse_library results,
    indexer_top releases, trending items, etc.). This lets Claude call
    show_results with just a title without re-pasting the full list.
    """
    if _UI_ACTION_STATE.get('ui_action', {}).get('action') == 'show_results':
        return {
            '_error': ('show_results was already called this turn. The grid is '
                       'visible — write your final spoken response now and stop.'),
        }

    # Auto-fill from the most recent data tool result if items missing/empty
    if not items:
        items = _UI_ACTION_STATE.get('last_items') or []

    normalized: List[Dict[str, Any]] = []
    for raw in (items or [])[:120]:
        if not isinstance(raw, dict):
            continue
        item: Dict[str, Any] = {
            'title': raw.get('title') or raw.get('name') or '',
            'year': raw.get('year'),
            'tmdb_id': raw.get('tmdb_id') or raw.get('tmdbId'),
            'rating_key': raw.get('rating_key') or raw.get('ratingKey'),
            'media_type': raw.get('media_type') or raw.get('type') or 'movie',
            'poster_url': raw.get('poster_url') or raw.get('posterUrl') or raw.get('poster_path'),
            'backdrop_url': raw.get('backdrop_url') or raw.get('backdropUrl') or raw.get('backdrop_path'),
            'rating': raw.get('rating'),
            'subtitle': raw.get('subtitle'),
            'owned': bool(raw.get('owned')),
            'in_watchlist': bool(raw.get('in_watchlist')),
            'tracker': raw.get('tracker') or raw.get('indexer'),
            'seeders': raw.get('seeders'),
            'resolution': raw.get('resolution'),
            'pub_date': raw.get('pub_date') or raw.get('publishDate'),
        }
        # Auto-derive subtitle for tracker releases: "dropped 4h ago"
        if not item['subtitle'] and item['pub_date']:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(str(item['pub_date']).replace('Z', '+00:00'))
                age = (datetime.now(timezone.utc) - dt).total_seconds()
                if age < 3600:
                    item['subtitle'] = f"dropped {int(age // 60)}m ago"
                elif age < 86400:
                    item['subtitle'] = f"dropped {int(age // 3600)}h ago"
                elif age < 86400 * 7:
                    item['subtitle'] = f"dropped {int(age // 86400)}d ago"
            except Exception:
                pass
        # Normalize media_type
        if item['media_type'] in ('show', 'tv', 'series'):
            item['media_type'] = 'tv'
        else:
            item['media_type'] = 'movie'
        # Enrich missing poster via TMDb
        if not item['poster_url'] and item['tmdb_id']:
            poster = _resolve_poster(int(item['tmdb_id']), item['media_type'])
            if poster:
                item['poster_url'] = poster
        normalized.append(item)

    action = {
        'action': 'show_results',
        'title': title or 'Results',
        'items': normalized,
    }
    _UI_ACTION_STATE['ui_action'] = action
    return {'ok': True, 'count': len(normalized)}


# ---------------------------------------------------------------------------
# Tool catalog — schemas + implementations
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        'name': 'browse_library',
        'description': (
            'Query the user\'s Plex library with rich filters. Use this for any '
            '"what do I have", "show me my X", "4K horror I haven\'t watched", '
            '"movies from last year rated over 8", or similar combined-filter '
            'question. Returns matching items plus tmdb_id and rating_key so you '
            'can open a modal afterward.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'media_type': {'type': 'string', 'enum': ['movie', 'tv']},
                'genre': {'type': 'string', 'description': 'e.g. "horror", "comedy"'},
                'avoid_genre': {'type': 'string', 'description': 'exclude titles in this genre'},
                'quality': {'type': 'string', 'description': '"4K", "remux", "1080p"'},
                'watched': {'type': 'boolean',
                            'description': 'true = watched at least once, false = never watched'},
                'added_since_days': {'type': 'integer',
                                     'description': 'only items added within the last N days'},
                'min_rating': {'type': 'number', 'description': '0-10 TMDb scale'},
                'year': {'type': 'integer', 'description': 'exact release year match'},
                'year_min': {'type': 'integer', 'description': 'earliest release year (inclusive)'},
                'year_max': {'type': 'integer', 'description': 'latest release year (inclusive)'},
                'title_contains': {'type': 'string'},
                'sort': {'type': 'string',
                         'enum': ['added', 'rating', 'year', 'runtime', 'view_count']},
                'limit': {'type': 'integer', 'default': 60,
                          'description': 'How many items to return. Default 60. '
                                         'Use 100-200 when user says "all" / "my entire" / '
                                         '"every". Cap is 200.'},
            },
        },
    },
    {
        'name': 'check_ownership',
        'description': 'Check whether a specific title is in the user\'s Plex library.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'title': {'type': 'string'},
                'media_type': {'type': 'string', 'enum': ['movie', 'tv']},
                'quality': {'type': 'string'},
            },
            'required': ['title'],
        },
    },
    {
        'name': 'get_recently_added',
        'description': ('Recently-added items from Plex, optionally scoped by media type, '
                        'quality, or period ("today", "this week", "this month").'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'media_type': {'type': 'string', 'enum': ['movie', 'tv']},
                'quality': {'type': 'string'},
                'period': {'type': 'string'},
            },
        },
    },
    {
        'name': 'get_library_stats',
        'description': 'Total counts of movies, shows, episodes in the Plex library.',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_watchlist',
        'description': 'Read the Radarr watchlist (monitored undownloaded movies).',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'check_watchlist',
        'description': 'Check whether a specific title is on the watchlist.',
        'input_schema': {
            'type': 'object',
            'properties': {'title': {'type': 'string'}},
            'required': ['title'],
        },
    },
    {
        'name': 'get_trending',
        'description': 'What\'s trending on TMDb (movies or TV, day or week window).',
        'input_schema': {
            'type': 'object',
            'properties': {
                'media_type': {'type': 'string', 'enum': ['movie', 'tv']},
                'window': {'type': 'string', 'enum': ['day', 'week']},
            },
        },
    },
    {
        'name': 'get_popular',
        'description': (
            'Most popular movies (or TV) on TMDb right now — GLOBAL popularity, '
            'no quality/format awareness. Use ONLY for bare phrasings: \"what\'s '
            'popular\", \"popular movies right now\", \"top 10 right now\". '
            'DO NOT use when the user combines popular with a quality token like '
            '\"popular 4K movies\" or \"popular 1080p\" — those mean \"top-seeded '
            'releases of that quality\" and should call get_indexer_top with '
            'sort=\"seeders\" instead.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {'media_type': {'type': 'string', 'enum': ['movie', 'tv']}},
        },
    },
    {
        'name': 'get_in_theaters',
        'description': 'Movies currently in US theaters.',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_indexer_top',
        'description': (
            'Top releases available on torrent trackers (Prowlarr sweep), sorted by '
            'seeders or publish date. Filter by quality, tracker, or recency.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'quality': {'type': 'string', 'description': '"4K", "1080p", "remux", "4k_remux"'},
                'tracker': {'type': 'string', 'description': '"PHD", "BHD", "IPT", "BTN"'},
                'sort': {'type': 'string', 'enum': ['seeders', 'newest']},
                'min_seeders': {'type': 'integer'},
                'since_days': {'type': 'integer',
                               'description': ('only releases dropped within the last N days. '
                                               'Use 7 for "this week", 1 for "today", 30 for "this month".')},
                'limit': {'type': 'integer', 'default': 10},
            },
        },
    },
    {
        'name': 'get_tv_calendar',
        'description': 'Upcoming TV episodes from Sonarr. Period: "tonight", "this week", etc.',
        'input_schema': {
            'type': 'object',
            'properties': {'period': {'type': 'string'}},
        },
    },
    {
        'name': 'get_next_episode',
        'description': 'When is the next episode of a given show airing.',
        'input_schema': {
            'type': 'object',
            'properties': {'title': {'type': 'string'}},
            'required': ['title'],
        },
    },
    {
        'name': 'get_season_ownership',
        'description': ('Which episodes of a show the user owns, total and by season. '
                        'Omit `season` for a global summary.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'title': {'type': 'string'},
                'season': {'type': 'integer'},
            },
            'required': ['title'],
        },
    },
    {
        'name': 'pick_by_mood',
        'description': (
            'Pick a Plex library title matching a mood. Moods: cozy, dark, mind-bending, '
            'adventure, funny, throwback, edge-of-seat, easy. Omit `mood` for a random pick.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {'mood': {'type': 'string'}},
        },
    },
    {
        'name': 'get_continue_watching',
        'description': 'Items on the Plex Continue Watching (on-deck) list.',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_now_playing',
        'description': 'What is currently playing on any Plex client (movie, show, room).',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_watch_history',
        'description': ('What the user has watched recently. Period like "yesterday", '
                        '"last night", "this week".'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'period': {'type': 'string'},
                'media_type': {'type': 'string', 'enum': ['movie', 'tv']},
            },
        },
    },
    {
        'name': 'get_watch_progress',
        'description': 'Where the user left off in a specific show or movie.',
        'input_schema': {
            'type': 'object',
            'properties': {'title': {'type': 'string'}},
            'required': ['title'],
        },
    },
    {
        'name': 'check_radarr_releases',
        'description': (
            'Search Radarr for available releases for a specific movie. Returns quality, '
            'seeders, size_gb, and indexer for each release. Use when the user asks about '
            'available qualities, seeders, file size, or tracker availability for a title. '
            'WARNING: triggers a live indexer search, takes 30-60 seconds.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'title': {'type': 'string', 'description': 'Movie title'},
                'tmdb_id': {'type': 'integer', 'description': 'TMDb movie ID if known'},
            },
            'required': ['title'],
        },
    },
    {
        'name': 'get_storage',
        'description': 'Disk usage. Any storage/space/disk question always means the Downloads volume — lead the reply with Downloads free space.',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'free_space',
        'description': 'Delete the oldest 20 IPTorrents files from downloads to free space. Use when the user says "free up space", "clean up downloads", "remove old IPT files", etc.',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_downloads',
        'description': 'Current qBittorrent downloads (active only, brief).',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_download_history',
        'description': (
            'Recently completed downloads from qBittorrent enriched with TMDb '
            'titles, posters, and IDs — plus currently-downloading items by '
            'default. Use this for "show me my downloads", "download history", '
            '"what was just downloaded", "what\'s downloading right now plus '
            'history". Each item is ready to feed into show_results.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'limit': {'type': 'integer', 'default': 25},
                'include_active': {'type': 'boolean', 'default': True,
                                   'description': 'Include in-progress torrents at the top.'},
            },
        },
    },
    {
        'name': 'get_sports_score',
        'description': 'Current or latest score for a sports team.',
        'input_schema': {
            'type': 'object',
            'properties': {'team': {'type': 'string'}},
        },
    },
    {
        'name': 'get_next_game',
        'description': 'When a team plays next (or on a specific day).',
        'input_schema': {
            'type': 'object',
            'properties': {
                'team': {'type': 'string'},
                'day': {'type': 'string'},
            },
        },
    },
    {
        'name': 'get_smart_home_state',
        'description': (
            'Query Home Assistant state. Use this for "what lights are on", '
            '"is the kitchen fan running", "what\'s the temperature in the bedroom", '
            '"what\'s on in the office". Pass `area` for a specific room (kitchen, '
            'office, family room, etc.), `entity_type` to filter (light, fan, switch, '
            'climate, sensor), and `on_only=true` to only show active entities.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'area': {'type': 'string', 'description': 'Room name; omit for all rooms.'},
                'entity_type': {'type': 'string',
                                'enum': ['light', 'fan', 'switch', 'climate', 'sensor', 'binary_sensor']},
                'on_only': {'type': 'boolean',
                            'description': 'Only return entities currently on/active.'},
            },
        },
    },
    {
        'name': 'get_weather',
        'description': ('Current weather and 5-day forecast for the configured '
                        'location. Use for "what\'s the weather", "is it going to '
                        'rain", "forecast tomorrow", "how cold tonight".'),
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'get_news',
        'description': ('Top headlines from the configured RSS feed. Use for '
                        '"what\'s in the news", "top headlines", "what\'s happening".'),
        'input_schema': {
            'type': 'object',
            'properties': {'limit': {'type': 'integer', 'default': 5}},
        },
    },
    {
        'name': 'get_shield_activity',
        'description': ('What app or content is currently active on the TV (Shield, '
                        'Google TV, Kodi). Use for "what\'s on the Shield", "what\'s '
                        'the TV doing", "is anyone watching".'),
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'open_modal',
        'description': ('Open the detail modal for a specific title. Call this AFTER a '
                        'browse/check tool when you want to show the user a single item.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'media_type': {'type': 'string', 'enum': ['movie', 'tv']},
                'tmdb_id': {'type': 'integer'},
                'season': {'type': 'integer'},
            },
            'required': ['media_type', 'tmdb_id'],
        },
    },
    {
        'name': 'navigate_to',
        'description': ('Navigate the kiosk to a specific page. Paths: /, /explore, '
                        '/library (vibe engine), /watchlist, /now-available, /browse, '
                        '/daily, /settings.'),
        'input_schema': {
            'type': 'object',
            'properties': {'path': {'type': 'string'}},
            'required': ['path'],
        },
    },
    {
        'name': 'show_filtered_library',
        'description': ('Open the Browse Library page with quality/genre/type filters '
                        'preset in the URL.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'quality': {'type': 'string'},
                'genre': {'type': 'string'},
                'media_type': {'type': 'string', 'enum': ['movie', 'tv']},
            },
        },
    },
    {
        'name': 'show_results',
        'description': (
            'Display a titled grid of media tiles in the kiosk overlay. THIS IS THE '
            'PREFERRED VISUAL OUTPUT for any "show me", "pull up", "browse", or '
            '"display" query that produced multiple items. Call this AFTER a data '
            'tool (browse_library / get_indexer_top / get_trending / get_popular / '
            'get_in_theaters / get_watchlist / get_recently_added / etc.). You may '
            'omit the items array and the runtime will use the items from your most '
            'recent data tool call automatically — just pass the title.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'title': {
                    'type': 'string',
                    'description': 'Header text for the overlay (e.g. "4K REMUXes available", '
                                   '"Unwatched horror added this month").',
                },
                'items': {
                    'type': 'array',
                    'description': 'Optional. Omit to auto-fill from the previous tool result.',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'title': {'type': 'string'},
                            'year': {'type': 'integer'},
                            'tmdb_id': {'type': 'integer'},
                            'rating_key': {'type': 'integer'},
                            'media_type': {'type': 'string', 'enum': ['movie', 'tv']},
                            'poster_url': {'type': 'string'},
                        },
                    },
                },
            },
            'required': ['title'],
        },
    },
]


TOOL_IMPLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    'browse_library': tool_browse_library,
    'check_ownership': _tool_ownership_check,
    'get_recently_added': _tool_recently_added,
    'get_library_stats': lambda: _tool_library_stats(),
    'get_watchlist': lambda: _tool_watchlist(),
    'check_watchlist': _tool_watchlist_check,
    'get_trending': _tool_trending,
    'get_popular': _tool_popular,
    'get_in_theaters': lambda: _tool_in_theaters(),
    'get_indexer_top': _tool_indexer_top,
    'get_tv_calendar': _tool_tv_calendar,
    'get_next_episode': _tool_next_episode,
    'get_season_ownership': _tool_season_ownership,
    'pick_by_mood': _tool_vibe_pick,
    'get_continue_watching': lambda: _tool_continue_watching(),
    'get_now_playing': lambda: _tool_now_playing(),
    'get_watch_history': _tool_watch_history,
    'get_watch_progress': _tool_watch_progress,
    'check_radarr_releases': _tool_radarr_releases,
    'get_storage': lambda: _tool_storage(),
    'free_space': lambda: _tool_free_space(),
    'get_downloads': lambda: _tool_downloads(),
    'get_download_history': _tool_download_history,
    'get_sports_score': _tool_sports_score,
    'get_next_game': _tool_next_game,
    'get_smart_home_state': _tool_smart_home_state,
    'get_weather': lambda: _tool_weather(),
    'get_news': _tool_news,
    'get_shield_activity': lambda: _tool_shield_activity(),
    'open_modal': _tool_open_modal,
    'navigate_to': _tool_navigate_to,
    'show_filtered_library': _tool_show_filtered_library,
    'show_results': _tool_show_results,
}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _system_prompt() -> str:
    today = datetime.now(timezone.utc) - timedelta(hours=4)
    return (
        f"You are Nova, the voice assistant for Taylor's home media kiosk. Today is "
        f"{today.strftime('%A, %B %-d, %Y')}.\n\n"
        "You have tools to read the user's Plex library, watchlist, tracker releases, "
        "TV calendar, and live playback state, plus tools to tell the kiosk UI what "
        "to show. Use them to answer accurately.\n\n"

        "LIBRARY ACCURACY — CRITICAL\n"
        "NEVER state whether a specific movie or show is or is not in the library based "
        "on your own knowledge or training data. You do not know what is in this Plex "
        "library. ALWAYS call check_ownership before answering any question about "
        "whether a specific title is owned, available, or in the library. "
        "Answering without calling the tool will produce wrong information.\n\n"

        "LIBRARY → QUALITY MAPPING\n"
        "The `quality` field in check_ownership results maps library names to quality tiers:\n"
        "  'Recently Released' or 'Movies' → 1080p\n"
        "  'Full Rips' → 1080p Remux\n"
        "  '4K' → 4K Remux\n"
        "NEVER mention quality or library name unless the user explicitly asked about quality. "
        "A plain ownership answer is just: \"Yes, you have Interstellar.\" "
        "Only use the quality field if the user asked 'what quality', 'do I have it in 4K', etc.\n\n"

        "RADARR RELEASES (tracker availability)\n"
        "Use check_radarr_releases when the user asks about tracker availability, "
        "quality options, seeders, OR file size for a specific movie. "
        "Each release includes: quality, seeders, size_gb, indexer. "
        "Always include file size (size_gb) in your summary — e.g.: "
        "\"4K WEB-DL: 91 seeders, 52.3 GB. 1080p Remux: 45 seeders, 28.1 GB.\" "
        "If the user asks about size after you already ran the search, call the tool "
        "again — do not say you don't have the data.\n\n"

        "RESPONSE STYLE\n"
        "- Speak briefly. 1-2 short sentences is ideal. Never more than 3.\n"
        "- Use specific names, counts, and quality labels — users love concrete answers.\n"
        "- When listing items, name at most 3 and summarize the rest (\"and 12 more\").\n"
        "- Don't describe your process, don't say \"let me check\" or \"based on results\".\n"
        "- Don't apologize. If something fails, state it plainly and move on.\n"
        "- Numbers: use digits (\"8 movies\", not \"eight movies\").\n"
        "- ABSOLUTELY NO MARKDOWN. No asterisks, no bold, no bullets, no headers. This text "
        "is spoken aloud — asterisks will be read as 'asterisk'. Plain sentences only.\n"
        "- ALWAYS produce a final plain-text response after calling tools. Even when you "
        "call open_modal or navigate_to, you MUST also speak a sentence describing what "
        "you're showing the user.\n\n"

        "WHEN TO CALL TOOLS\n"
        "- Any library question (\"what do I have\", \"show me X\", ownership checks, "
        "counts by quality/genre/year) → browse_library or check_ownership.\n"
        "- Compound filters (\"4K horror unwatched\") → browse_library with all relevant params.\n"
        "- \"What's new\" / \"recently added\" → get_recently_added.\n"
        "- \"What's trending / popular\" / \"in theaters\" → those tools (external, not Plex).\n"
        "- \"What's available to grab\" / \"top seeders\" → get_indexer_top.\n"
        "- TV airing / next episode / season ownership → the get_tv_* tools.\n"
        "- Mood / \"surprise me\" / \"pick something\" → pick_by_mood.\n"
        "- After a single-title result, call open_modal with its tmdb_id to show it.\n"
        "- Smart-home state (\"what lights are on\", \"is the fan on\", \"what's the "
        "temperature in the kitchen\") → get_smart_home_state. For \"what's on\" or "
        "\"what lights are on\" use on_only=true so you only see active entities.\n"
        "- Weather (\"what's the weather\", \"forecast\", \"is it raining\") → get_weather.\n"
        "- News headlines (\"what's in the news\", \"top stories\") → get_news.\n"
        "- TV/Shield state (\"what's on the Shield\", \"is anyone watching\") → "
        "get_shield_activity (or get_now_playing if asking about Plex specifically).\n\n"

        "VISUAL VS SPOKEN OUTPUT — IMPORTANT\n"
        "Two distinct modes:\n"
        "1. VISUAL — when the user's message contains ANY of these verbs/phrases: "
        "\"show me\", \"show us\", \"pull up\", \"display\", \"browse\", \"let me see\", "
        "\"open my\", \"open the\", \"bring up\", \"let's see\", \"see my\". "
        "These are EXPLICIT REQUESTS TO SEE SOMETHING ON SCREEN. You MUST: "
        "  (a) call your data tool to get the list, then "
        "  (b) call show_results to render the grid. "
        "ALWAYS do both, EVEN IF the list is empty or has only 1-2 items. "
        "ALWAYS call show_results in this mode — never substitute speech.\n"
        "After show_results runs, stop. Do not write any text. Do not call any "
        "more tools. The grid IS the answer; the runtime auto-suppresses TTS.\n"
        "Examples (call data tool then show_results, then STOP):\n"
        "  \"Show me 4K REMUXes available\" → get_indexer_top → show_results\n"
        "  \"Show me today's top seeded 4K remuxes\" → get_indexer_top(since_days=1) → show_results\n"
        "  \"Pull up unwatched horror\" → browse_library → show_results\n"
        "  \"Let me see trending shows\" → get_trending → show_results\n"
        "  \"Open my watchlist\" → get_watchlist → show_results\n"
        "  \"Show me my recently added\" → get_recently_added → show_results\n"
        "  \"Browse my comedies\" → browse_library → show_results\n"
        "\n"
        "2. SPOKEN — when the user asks a QUESTION without one of those verbs: "
        "\"what's on my watchlist\", \"how many comedies do I have\", \"what's "
        "trending\", \"do I have Dune\". Answer with speech only — DO NOT call "
        "show_results. Numbers, yes/no, single-item answers stay spoken.\n"
        "\n"
        "DATE / TIME FILTERS\n"
        "When the user mentions a time window, ALWAYS pass it as a filter to "
        "your data tool — never ignore it:\n"
        "  \"today\" / \"today's\"      → since_days=1\n"
        "  \"yesterday\"                → since_days=2\n"
        "  \"this week\" / \"this week's\" → since_days=7\n"
        "  \"past week\" / \"last week\"  → since_days=7\n"
        "  \"this month\"               → since_days=30\n"
        "  \"recently\" / \"lately\"      → since_days=14\n"
        "If the data tool exposes since_days (e.g. get_indexer_top) or "
        "added_since_days (browse_library, get_recently_added), USE IT. Don't "
        "fetch all and let the user filter mentally.\n\n"

        "TOOL CHOICE — trackers (DEFAULT) vs. library vs. recently-added\n"
        "DEFAULT to get_indexer_top (torrent tracker releases) for any media query "
        "that doesn't EXPLICITLY name the user's personal library.\n"
        "\n"
        "browse_library — ONLY when the user explicitly says one of:\n"
        "  \"my\", \"my library\", \"in my library\", \"I have\", \"I own\", \"on my plex\", "
        "\"in my collection\", \"on my server\", \"that I added\", \"my 4K shelf\".\n"
        "If any of those phrases appear, browse_library. Otherwise NOT.\n"
        "\n"
        "get_indexer_top — DEFAULT for everything else: bare quality requests, \"latest\", "
        "\"new\", \"top seeded\", \"available\", \"to grab\", \"to download\", tracker names "
        "(PHD/BHD/IPT/BTN), or any \"show me X movies/shows\" without a library qualifier. "
        "These are torrent releases.\n"
        "\n"
        "get_recently_added — when the user says \"what's new\" / \"what got added\" / "
        "\"recently added\" / \"new in my library\" — those are Plex-library-recent. \n"
        "\n"
        "Examples (memorize these):\n"
        "  \"show me 4K movies\"               → get_indexer_top(quality=\"4K\")\n"
        "  \"show me latest 4K movies\"        → get_indexer_top(quality=\"4K\", sort=\"newest\")\n"
        "  \"show me top seeded 4K today\"     → get_indexer_top(quality=\"4K\", since_days=1, sort=\"seeders\")\n"
        "  \"show me 4K horror\"               → get_indexer_top(quality=\"4K\")  — title \"Top 4K releases\"\n"
        "  \"show me popular 4K movies\"       → get_indexer_top(quality=\"4K\", sort=\"seeders\")  — title \"Popular 4K movies\"\n"
        "  \"top 1080p of the week\"           → get_indexer_top(quality=\"1080p\", since_days=7, sort=\"seeders\")\n"
        "  \"what dropped on PHD today\"       → get_indexer_top(tracker=\"PHD\", since_days=1)\n"
        "  \"show me MY 4K library\"           → browse_library(quality=\"4K\")\n"
        "  \"4K horror in my library\"         → browse_library(quality=\"4K\", genre=\"horror\")\n"
        "  \"do I have Inception\"             → check_ownership(title=\"Inception\")\n"
        "  \"show me my downloads\"            → get_download_history → show_results\n"
        "  \"show me my download history\"     → get_download_history → show_results\n"
        "  \"what's new on Plex\"              → get_recently_added\n"
        "  \"what's new\" (bare)               → get_recently_added\n"
        "  \"what's popular\" (bare)           → get_popular  (TMDb global popularity)\n"
        "  \"popular X movies\" (with quality) → get_indexer_top(quality=X, sort=\"seeders\")  — NOT get_popular\n"
        "When in doubt and no library qualifier appears: get_indexer_top.\n"
        "\n"
        "ABSOLUTE RULE — do not fall back to browse_library just because get_indexer_top "
        "doesn't filter by genre/mood/actor. If the user said \"4K horror\" without a "
        "library qualifier, call get_indexer_top(quality=\"4K\") and accept that genre "
        "isn't filtered. Title the overlay honestly: \"Top 4K releases\" — DO NOT title "
        "it \"Your 4K horror collection\" because (a) it's not the user's library and "
        "(b) genre wasn't actually filtered. NEVER call BOTH browse_library AND "
        "get_indexer_top in the same turn — pick one and commit.\n\n"

        "RESULT COUNTS\n"
        "browse_library default returns 60 items. When user says \"all\", \"my entire\", "
        "\"every\", or \"the whole\", pass limit=200 so they see everything. Otherwise "
        "60 is fine — overlay scrolls.\n\n"

        "QUALITY TERMS — natural-language convention\n"
        "People rarely say \"REMUX\" — it sounds nerdy. Map their casual phrasings:\n"
        "  \"4K\" / \"4K movies\" / \"UHD\" / \"2160p\"   → quality=\"4K\" "
        "(handler resolves to 4k_remux automatically)\n"
        "  \"1080p\" / \"BluRay\" / \"Blu-ray\" / \"Full HD\" → quality=\"1080p\" "
        "(handler resolves to 1080p_remux automatically)\n"
        "  \"WEB-DL\" / \"WebDL\"                       → quality=\"WEB-DL\"\n"
        "  Only pass \"REMUX\" explicitly when the user said \"REMUX\".\n"
        "TITLES for show_results: NEVER inject \"REMUX\" into the title unless the "
        "user used that word. Use natural labels:\n"
        "  user said \"4K movies\" → title \"Today's top 4K movies\" (NOT \"4K REMUXes\")\n"
        "  user said \"1080p\"     → title \"Top 1080p Bluray\" or just \"Top 1080p\"\n"
        "  user said \"Bluray\"    → title \"Top Bluray releases\"\n"
        "Mirror the words the user actually used in your title.\n\n"

        "show_results is SINGLE-SHOT: call it ONCE then stop. The grid is on "
        "screen — no further tool calls or text needed.\n\n"

        "RULES\n"
        "- Never invent data. If a tool returns no items, say so.\n"
        "- For compound questions, chain multiple tools rather than giving a partial answer.\n"
        "- Pronouns in follow-ups (\"play it\", \"the first one\", \"add it\") resolve to "
        "items from the most recent assistant turn — look at conversation history.\n"
        "- If the user wants to actually add/play/download something, DO NOT call tools "
        "for it directly — respond with a plain confirmation question and let the "
        "classic flow handle it.\n"
    )


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    message: str
    ui_action: Optional[Dict[str, Any]]
    tool_trace: List[Dict[str, Any]]
    status: str = 'ok'


def _call_claude(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Call Claude with our tool catalog."""
    payload: Dict[str, Any] = {
        'model': _MODEL,
        'max_tokens': 1024,
        'system': _system_prompt(),
        'messages': messages,
        'tools': TOOL_SCHEMAS,
    }
    resp = requests.post(
        _API_URL,
        headers={
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
        },
        json=payload,
        timeout=45,
    )
    if resp.status_code in (429, 529):
        wait = min(int(resp.headers.get('retry-after', 3)), 10)
        logger.info('Claude API %d, retrying in %ds', resp.status_code, wait)
        time.sleep(wait)
        resp = requests.post(
            _API_URL,
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=45,
        )
    resp.raise_for_status()
    return resp.json()


def _execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    impl = TOOL_IMPLS.get(name)
    if not impl:
        return {'_error': f'Unknown tool: {name}'}
    try:
        logger.info('Agent tool call: %s args=%s', name, args)
        return impl(**(args or {}))
    except TypeError as e:
        return {'_error': f'Bad arguments for {name}: {e}'}
    except Exception as e:
        logger.exception('Tool %s raised', name)
        return {'_error': str(e)}


def run_agent(command: str,
              history: Optional[List[Dict[str, str]]] = None,
              context: Optional[Dict[str, Any]] = None) -> AgentResult:
    """Execute the agent loop for a user command. Returns speech + UI action."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError('ANTHROPIC_API_KEY not set')

    # Clear transient UI-action state from prior calls
    _UI_ACTION_STATE.clear()

    # Seed messages with short history for context + the current query
    messages: List[Dict[str, Any]] = []
    for h in (history or [])[-6:]:
        role = h.get('role')
        content = (h.get('content') or '').strip()
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})
    messages.append({'role': 'user', 'content': command})

    tool_trace: List[Dict[str, Any]] = []
    final_text = ''

    show_results_called = False
    for iteration in range(_MAX_TOOL_ITERATIONS):
        # Once show_results fires, the user wanted a VISUAL answer. The overlay
        # is already on screen — no spoken response needed. Exit the loop with
        # an empty message so the frontend skips TTS entirely.
        if show_results_called:
            final_text = ''
            break
        data = _call_claude(messages)
        stop_reason = data.get('stop_reason')
        content_blocks = data.get('content') or []

        # Collect tool_use blocks — may be multiple in one turn
        tool_uses = [b for b in content_blocks if b.get('type') == 'tool_use']
        text_blocks = [b.get('text', '') for b in content_blocks if b.get('type') == 'text']

        # Always append the assistant's turn (text + tool_use blocks) back into history
        messages.append({'role': 'assistant', 'content': content_blocks})

        if not tool_uses:
            final_text = ' '.join(t for t in text_blocks if t).strip()
            break

        # Run each requested tool and send results back
        tool_results = []
        for tu in tool_uses:
            name = tu.get('name', '')
            args = tu.get('input', {}) or {}
            result = _execute_tool(name, args)
            tool_trace.append({'tool': name, 'args': args, 'result_summary': _summary(result)})
            if name == 'show_results' and not result.get('_error'):
                show_results_called = True
            # Capture list-shaped tool results so show_results can auto-fill
            if name != 'show_results' and isinstance(result, dict):
                payload = result.get('data') if isinstance(result.get('data'), dict) else result
                for key in ('items', 'releases', 'movies', 'shows', 'episodes',
                            'results', 'tracks', 'matches', 'runners_up'):
                    cand = payload.get(key) if isinstance(payload, dict) else None
                    if isinstance(cand, list) and cand:
                        _UI_ACTION_STATE['last_items'] = cand
                        break
            tool_results.append({
                'type': 'tool_result',
                'tool_use_id': tu.get('id'),
                'content': json.dumps(result, default=str)[:12000],
            })

        messages.append({'role': 'user', 'content': tool_results})

        if stop_reason == 'end_turn':
            final_text = ' '.join(t for t in text_blocks if t).strip()
            break
    else:
        # Max iterations hit — synthesize a response from whatever text we have
        final_text = final_text or "I ran out of steps trying to answer that — try rephrasing."

    # Strip any markdown Claude might have leaked in (asterisks, underscores around words).
    final_text = _strip_markdown(final_text)

    if not final_text:
        ui = _UI_ACTION_STATE.get('ui_action')
        # show_results is visual-only — no speech needed. Frontend skips TTS
        # when the message is empty.
        if ui and ui.get('action') == 'show_results':
            final_text = ''
        elif ui and ui.get('action') == 'open_modal':
            # Quick acknowledgement so the user hears something
            final_text = ''
        elif ui and ui.get('action') == 'navigate':
            final_text = ''
        else:
            final_text = "I wasn't sure how to answer that."

    return AgentResult(
        message=final_text,
        ui_action=_UI_ACTION_STATE.get('ui_action'),
        tool_trace=tool_trace,
    )


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax that would be read aloud as characters."""
    import re as _re
    # Bold/italic: **word**, __word__, *word*, _word_  — keep inner text
    text = _re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = _re.sub(r'__(.+?)__', r'\1', text)
    text = _re.sub(r'(?<!\w)\*([^*\n]+?)\*(?!\w)', r'\1', text)
    text = _re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'\1', text)
    # Leading bullet markers
    text = _re.sub(r'(?m)^\s*[-*]\s+', '', text)
    # Stray standalone asterisks
    text = text.replace('*', '').replace('•', '')
    return text.strip()


def _summary(obj: Any) -> str:
    """Compact stringification for logs."""
    try:
        s = json.dumps(obj, default=str)
    except Exception:
        s = str(obj)
    return s[:200]
