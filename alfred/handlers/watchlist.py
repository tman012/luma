"""Watchlist CRUD handlers — list, check, remove.

Add is handled by the existing download_action flow.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

from config import RADARR_URL, RADARR_API_KEY
from handlers.title_match import best_match

logger = logging.getLogger(__name__)

FLASK_BASE = os.getenv('FLASK_BASE', 'http://localhost:5001')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')


def _auth_headers() -> Dict[str, str]:
    return {'X-API-Key': SERVICE_API_KEY} if SERVICE_API_KEY else {}


def _normalize(s: str) -> str:
    s = re.sub(r'^the\s+', '', (s or '').lower().strip())
    return re.sub(r'\s+', ' ', s)


def _fetch_watchlist() -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/watchlist',
            headers=_auth_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        # Endpoint returns a bare list
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return body.get('watchlist', []) or []
        return []
    except Exception as e:
        logger.warning('Watchlist fetch failed: %s', e)
        return []


def _resolve_tmdb_id(title: str) -> Optional[Dict[str, Any]]:
    """Resolve a spoken title to a TMDb match via Radarr lookup."""
    if not (RADARR_URL and RADARR_API_KEY):
        return None
    try:
        resp = requests.get(
            f'{RADARR_URL}/api/v3/movie/lookup',
            params={'term': title},
            headers={'X-Api-Key': RADARR_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json() or []
    except Exception as e:
        logger.warning('Radarr lookup failed for "%s": %s', title, e)
        return None
    if not results:
        return None
    # Convert to title_match input shape
    candidates = [{
        'title': r.get('title', ''),
        'year': r.get('year'),
        'tmdb_id': r.get('tmdbId'),
        'popularity': r.get('popularity', 0),
    } for r in results if r.get('tmdbId')]
    return best_match(title, candidates)


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


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_watchlist_list() -> Dict[str, Any]:
    """Read the current watchlist aloud."""
    items = _fetch_watchlist()
    if not items:
        return {
            'type': 'watchlist_list',
            'status': 'ok',
            'message': "Your watchlist is empty.",
            'data': {'count': 0},
            'suggested_ui_action': {'action': 'navigate', 'path': '/watchlist'},
        }

    # Sort by added_date descending if present
    items.sort(key=lambda i: i.get('added_date') or '', reverse=True)
    titles = [i.get('title', '') for i in items]
    count = len(items)
    name_list = _join_titles(titles, top=3)

    if count == 1:
        msg = f"One thing on your watchlist: {name_list}."
    elif count <= 3:
        msg = f"{count} on your watchlist: {name_list}."
    else:
        msg = f"{count} on your watchlist — including {name_list}. Want me to open it?"

    return {
        'type': 'watchlist_list',
        'status': 'ok',
        'message': msg,
        'data': {
            'count': count,
            'items': [{'title': i.get('title'), 'year': i.get('year'),
                       'tmdb_id': i.get('tmdb_id'),
                       'quality_profile_name': i.get('quality_profile_name')}
                      for i in items[:25]],
        },
        'suggested_ui_action': {'action': 'navigate', 'path': '/watchlist'},
    }


def handle_watchlist_check(title: str) -> Dict[str, Any]:
    """Check whether a given title is on the user's watchlist."""
    if not title:
        return {
            'type': 'watchlist_check',
            'status': 'error',
            'message': "Which title should I check?",
            'data': {},
        }

    items = _fetch_watchlist()
    nq = _normalize(title)

    match = None
    for i in items:
        if _normalize(i.get('title', '')) == nq:
            match = i
            break
    if not match:
        # Loose contains match
        for i in items:
            t = _normalize(i.get('title', ''))
            if t and (nq in t or t in nq):
                match = i
                break

    if match:
        year = match.get('year', '')
        profile = match.get('quality_profile_name', '')
        suffix = f" ({year})" if year else ''
        profile_suffix = f' at {profile}' if profile else ''
        msg = f"Yes — {match.get('title')}{suffix} is on your watchlist{profile_suffix}."
        return {
            'type': 'watchlist_check',
            'status': 'ok',
            'message': msg,
            'data': {
                'on_watchlist': True,
                'title': match.get('title'),
                'year': match.get('year'),
                'tmdb_id': match.get('tmdb_id'),
                'quality_profile_name': profile,
            },
            'suggested_ui_action': {
                'action': 'open_modal',
                'media_type': 'movie',
                'tmdb_id': match.get('tmdb_id'),
            } if match.get('tmdb_id') else {'action': 'navigate', 'path': '/watchlist'},
        }

    return {
        'type': 'watchlist_check',
        'status': 'ok',
        'message': f"{title} is not on your watchlist.",
        'data': {'on_watchlist': False, 'title': title},
    }


def handle_watchlist_remove(title: str) -> Dict[str, Any]:
    """Remove a title from the watchlist by name."""
    if not title:
        return {
            'type': 'watchlist_remove',
            'status': 'error',
            'message': "Which title should I remove?",
            'data': {},
        }

    # Prefer the watchlist itself for matching (so we remove something that's
    # actually there). Fall back to Radarr lookup for tmdb_id resolution.
    items = _fetch_watchlist()
    nq = _normalize(title)

    match = None
    for i in items:
        if _normalize(i.get('title', '')) == nq:
            match = i
            break
    if not match:
        for i in items:
            t = _normalize(i.get('title', ''))
            if t and (nq in t or t in nq):
                match = i
                break

    if not match:
        return {
            'type': 'watchlist_remove',
            'status': 'ok',
            'message': f"{title} isn't on your watchlist, so there's nothing to remove.",
            'data': {'found': False, 'title': title},
        }

    tmdb_id = match.get('tmdb_id')
    if not tmdb_id:
        return {
            'type': 'watchlist_remove',
            'status': 'error',
            'message': f"I found {match.get('title')} but couldn't get its ID to remove it.",
            'data': {'found': True, 'title': match.get('title')},
        }

    try:
        resp = requests.delete(
            f'{FLASK_BASE}/api/watchlist/{int(tmdb_id)}',
            headers=_auth_headers(),
            timeout=15,
        )
        ok = resp.status_code < 400
    except Exception as e:
        logger.warning('Watchlist delete failed: %s', e)
        ok = False

    if ok:
        year = match.get('year', '')
        suffix = f" ({year})" if year else ''
        return {
            'type': 'watchlist_remove',
            'status': 'ok',
            'message': f"Removed {match.get('title')}{suffix} from your watchlist.",
            'data': {
                'removed': True,
                'title': match.get('title'),
                'tmdb_id': tmdb_id,
            },
        }

    return {
        'type': 'watchlist_remove',
        'status': 'error',
        'message': f"I couldn't remove {match.get('title')} — the watchlist call failed.",
        'data': {'removed': False, 'title': match.get('title')},
    }
