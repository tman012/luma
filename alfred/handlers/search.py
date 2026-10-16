"""Media search handler — searches Radarr/Sonarr for releases."""

import logging
from typing import Any, Dict, List, Optional

import requests

from config import RADARR_URL, RADARR_API_KEY, SONARR_URL, SONARR_API_KEY

logger = logging.getLogger(__name__)


def _radarr_get(path: str, params: Optional[Dict] = None,
                timeout: int = 15) -> Optional[Any]:
    if not RADARR_URL or not RADARR_API_KEY:
        return None
    try:
        resp = requests.get(
            f'{RADARR_URL}/api/v3{path}',
            params=params,
            headers={'X-Api-Key': RADARR_API_KEY},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning('Radarr GET %s failed: %s', path, e)
        return None


def _sonarr_get(path: str, params: Optional[Dict] = None,
                timeout: int = 15) -> Optional[Any]:
    if not SONARR_URL or not SONARR_API_KEY:
        return None
    try:
        resp = requests.get(
            f'{SONARR_URL}/api/v3{path}',
            params=params,
            headers={'X-Api-Key': SONARR_API_KEY},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning('Sonarr GET %s failed: %s', path, e)
        return None


def handle_search_movie(query: str) -> Dict[str, Any]:
    """Search Radarr for a movie by title."""
    results = _radarr_get('/movie/lookup', {'term': query})
    if results is None:
        return {
            'type': 'search_results',
            'status': 'error',
            'message': 'Radarr is not available.',
            'data': {},
        }

    items: List[Dict[str, Any]] = []
    for m in (results or [])[:10]:
        items.append({
            'title': m.get('title', ''),
            'year': m.get('year'),
            'tmdb_id': m.get('tmdbId'),
            'overview': (m.get('overview', '') or '')[:150],
            'in_library': m.get('id', 0) > 0,
            'monitored': m.get('monitored', False),
        })

    return {
        'type': 'search_results',
        'status': 'ok',
        'message': f'Found {len(items)} results for "{query}".' if items else f'No results for "{query}".',
        'data': {
            'media_type': 'movie',
            'query': query,
            'results': items,
        },
        'suggested_ui_action': {
            'action': 'show_search_results',
            'query': query,
        } if items else None,
    }


def handle_search_tv(query: str) -> Dict[str, Any]:
    """Search Sonarr for a TV show by title."""
    results = _sonarr_get('/series/lookup', {'term': query})
    if results is None:
        return {
            'type': 'search_results',
            'status': 'error',
            'message': 'Sonarr is not available.',
            'data': {},
        }

    items: List[Dict[str, Any]] = []
    for s in (results or [])[:10]:
        items.append({
            'title': s.get('title', ''),
            'year': s.get('year'),
            'tvdb_id': s.get('tvdbId'),
            'overview': (s.get('overview', '') or '')[:150],
            'in_library': s.get('id', 0) > 0,
            'monitored': s.get('monitored', False),
            'season_count': s.get('seasonCount', 0),
        })

    return {
        'type': 'search_results',
        'status': 'ok',
        'message': f'Found {len(items)} results for "{query}".' if items else f'No results for "{query}".',
        'data': {
            'media_type': 'tv',
            'query': query,
            'results': items,
        },
        'suggested_ui_action': {
            'action': 'show_search_results',
            'query': query,
        } if items else None,
    }
