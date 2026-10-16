"""Discovery-feed handlers: trending, popular, in_theaters.

Queries TMDb (and Trakt if enabled) via the Luma backend.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

FLASK_BASE = os.getenv('FLASK_BASE', 'http://localhost:5001')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')


def _auth_headers() -> Dict[str, str]:
    return {'X-API-Key': SERVICE_API_KEY} if SERVICE_API_KEY else {}


def _join_titles(titles: List[str], top: int = 3) -> str:
    titles = [t for t in titles if t]
    if not titles:
        return ''
    head = titles[:top]
    if len(head) == 1:
        return head[0]
    if len(head) == 2:
        return f"{head[0]} and {head[1]}"
    return f"{', '.join(head[:-1])}, and {head[-1]}"


def _normalize_media_type(media_type: Optional[str]) -> str:
    if not media_type:
        return 'movie'
    mt = media_type.lower().strip()
    if mt in ('tv', 'show', 'shows', 'series', 'tv shows', 'episode', 'episodes'):
        return 'tv'
    return 'movie'


def _get(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Optional[Dict[str, Any]]:
    try:
        resp = requests.get(url, params=params, headers=_auth_headers(), timeout=timeout)
        resp.raise_for_status()
        return resp.json() or {}
    except Exception as e:
        logger.warning('GET %s failed: %s', url, e)
        return None


def handle_trending(media_type: Optional[str] = None,
                    window: Optional[str] = None) -> Dict[str, Any]:
    """What's trending on TMDb (and Trakt if enabled)."""
    mt = _normalize_media_type(media_type)
    win = (window or 'week').lower().strip()
    if win not in ('day', 'week'):
        win = 'week'

    body = _get(f'{FLASK_BASE}/api/trending', params={'type': mt, 'window': win})
    if not body:
        return {
            'type': 'trending',
            'status': 'error',
            'message': "I couldn't pull trending right now.",
            'data': {},
        }

    results: List[Dict[str, Any]] = body.get('results') or []
    if not results:
        return {
            'type': 'trending',
            'status': 'ok',
            'message': f"Nothing trending in {mt} right now.",
            'data': {'count': 0, 'media_type': mt, 'window': win},
            'suggested_ui_action': {'action': 'navigate', 'path': '/explore'},
        }

    titles = [r.get('title') or r.get('name') or '' for r in results]
    top3 = _join_titles(titles, top=3)
    noun = 'shows' if mt == 'tv' else 'movies'
    win_desc = 'today' if win == 'day' else 'this week'
    msg = f"Trending {noun} {win_desc}: {top3}."

    return {
        'type': 'trending',
        'status': 'ok',
        'message': msg,
        'data': {
            'count': len(results),
            'media_type': mt,
            'window': win,
            'items': [{'title': r.get('title') or r.get('name'),
                       'year': (r.get('release_date') or r.get('first_air_date') or '')[:4],
                       'tmdb_id': r.get('id'),
                       'rating': r.get('vote_average'),
                       'poster_path': r.get('poster_path'),
                       'media_type': mt}
                      for r in results[:20]],
        },
        'suggested_ui_action': {'action': 'navigate', 'path': '/explore'},
    }


def handle_popular(media_type: Optional[str] = None) -> Dict[str, Any]:
    """What's popular on TMDb."""
    mt = _normalize_media_type(media_type)
    # /api/popular is movie-only on the backend; use /api/trending with window=week
    # for TV since it's the closest fit we currently expose.
    if mt == 'tv':
        return handle_trending(media_type='tv', window='week')

    body = _get(f'{FLASK_BASE}/api/popular')
    if not body:
        return {
            'type': 'popular',
            'status': 'error',
            'message': "I couldn't pull popular movies right now.",
            'data': {},
        }

    results: List[Dict[str, Any]] = body.get('results') or []
    if not results:
        return {
            'type': 'popular',
            'status': 'ok',
            'message': "No popular movies came back.",
            'data': {'count': 0},
            'suggested_ui_action': {'action': 'navigate', 'path': '/explore'},
        }

    titles = [r.get('title') or r.get('name') or '' for r in results]
    msg = f"Popular right now: {_join_titles(titles, top=3)}."
    return {
        'type': 'popular',
        'status': 'ok',
        'message': msg,
        'data': {
            'count': len(results),
            'items': [{'title': r.get('title') or r.get('name'),
                       'year': (r.get('release_date') or r.get('first_air_date') or '')[:4],
                       'tmdb_id': r.get('id'),
                       'rating': r.get('vote_average'),
                       'poster_path': r.get('poster_path'),
                       'media_type': 'movie'}
                      for r in results[:20]],
        },
        'suggested_ui_action': {'action': 'navigate', 'path': '/explore'},
    }


def handle_in_theaters() -> Dict[str, Any]:
    """What's in theaters now (US release calendar)."""
    body = _get(f'{FLASK_BASE}/api/in-theaters')
    if not body:
        return {
            'type': 'in_theaters',
            'status': 'error',
            'message': "I couldn't pull in-theaters right now.",
            'data': {},
        }

    movies = body.get('movies') or body.get('results') or []
    if not movies:
        return {
            'type': 'in_theaters',
            'status': 'ok',
            'message': "Nothing is in theaters right now.",
            'data': {'count': 0},
            'suggested_ui_action': {'action': 'navigate', 'path': '/explore'},
        }
    titles = [m.get('title') or '' for m in movies]
    msg = f"In theaters now: {_join_titles(titles, top=3)}."
    return {
        'type': 'in_theaters',
        'status': 'ok',
        'message': msg,
        'data': {
            'count': len(movies),
            'items': [{'title': m.get('title'),
                       'year': m.get('year'),
                       'tmdb_id': m.get('tmdb_id'),
                       'release_date': m.get('release_date'),
                       'poster_path': m.get('poster_path'),
                       'media_type': 'movie'}
                      for m in movies[:20]],
        },
        'suggested_ui_action': {'action': 'navigate', 'path': '/explore'},
    }
