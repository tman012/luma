"""Library quality-scoped handlers.

Answers: "show me my 4K movies", "all my REMUXes", "how many 4K movies total",
"anything still in 720p I should upgrade", "library count by genre".
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


def _normalize_quality_token(quality: Optional[str]) -> Optional[str]:
    """Normalize spoken quality into a URL-safe filter token."""
    if not quality:
        return None
    q = quality.lower().strip()
    if any(k in q for k in ('4k', 'uhd', '2160')):
        return '4k'
    if 'remux' in q:
        return 'remux'
    if '1080' in q or 'full hd' in q or 'full rip' in q:
        return '1080p'
    if '720' in q or q == 'hd':
        return '720p'
    if 'h265' in q or 'hevc' in q or 'x265' in q:
        return 'h265'
    if 'h264' in q or 'x264' in q:
        return 'h264'
    if 'sd' in q:
        return 'sd'
    return q  # pass through unknown tokens


def _quality_friendly(token: Optional[str]) -> str:
    if not token:
        return ''
    return {
        '4k': '4K',
        'remux': 'REMUX',
        '1080p': '1080p',
        '720p': '720p',
        'h265': 'h265',
        'h264': 'h264',
        'sd': 'SD',
    }.get(token, token)


def handle_library_quality(quality: Optional[str] = None,
                           media_type: Optional[str] = None,
                           genre: Optional[str] = None,
                           sort: Optional[str] = None) -> Dict[str, Any]:
    """Navigate the user to a quality-filtered browse view.

    We don't currently have a dedicated backend endpoint that counts items
    by resolution. For now, the spoken response announces the filter and
    the UI action navigates to /browse with filter hints in the querystring
    so the UI can apply them as it gains support.
    """
    qtoken = _normalize_quality_token(quality)
    qlabel = _quality_friendly(qtoken)

    # Build a navigation action with filter hints
    params: List[str] = []
    if qtoken:
        params.append(f'quality={qtoken}')
    if media_type:
        mt = media_type.lower().strip()
        if mt in ('movie', 'movies', 'film', 'films'):
            params.append('type=movie')
        elif mt in ('tv', 'show', 'shows', 'series'):
            params.append('type=tv')
    if genre:
        params.append(f'genre={genre}')
    if sort:
        params.append(f'sort={sort}')

    querystring = ('?' + '&'.join(params)) if params else ''
    target_path = f'/browse{querystring}'

    # Voice message — be brief
    bits: List[str] = []
    if qlabel:
        bits.append(qlabel)
    if media_type:
        bits.append(media_type.lower())
    if genre:
        bits.append(genre.lower())
    descriptor = ' '.join(bits).strip() or 'library'

    msg = f"Opening your {descriptor} shelf."

    return {
        'type': 'library_quality',
        'status': 'ok',
        'message': msg,
        'data': {
            'quality': qtoken,
            'media_type': media_type,
            'genre': genre,
            'sort': sort,
        },
        'suggested_ui_action': {
            'action': 'navigate',
            'path': target_path,
        },
    }


def handle_library_stats(group_by: Optional[str] = None,
                         filter: Optional[str] = None) -> Dict[str, Any]:
    """Answer count-type questions about the library."""
    # Prefer the cached variant (ultra fast); fall back to the live endpoint
    # only if the cache is empty / initializing.
    stats: Dict[str, Any] = {}
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/library/stats/cached',
            headers=_auth_headers(),
            timeout=8,
        )
        resp.raise_for_status()
        stats = resp.json() or {}
    except Exception as e:
        logger.warning('Library stats (cached) fetch failed: %s', e)

    # If the cache is empty / all zeros, degrade gracefully without the
    # live endpoint — it's slow and often times out against Plex under load.
    has_totals = bool(stats.get('total_movies') or stats.get('total_shows'))
    if not has_totals:
        return {
            'type': 'library_stats',
            'status': 'ok',
            'message': ("Library stats are still warming up — try again in a minute, or "
                        "open the browser to see the shelves."),
            'data': stats,
            'suggested_ui_action': {'action': 'navigate', 'path': '/browse'},
        }

    total_movies = int(stats.get('total_movies') or 0)
    total_shows = int(stats.get('total_shows') or 0)
    total_episodes = int(stats.get('total_episodes') or 0)

    gb = (group_by or '').lower().strip()

    if gb == 'genre':
        msg = ("I don't have a per-genre count handy — opening the library browser so "
               "you can filter by genre.")
        return {
            'type': 'library_stats',
            'status': 'ok',
            'message': msg,
            'data': stats,
            'suggested_ui_action': {'action': 'navigate', 'path': '/browse'},
        }

    # Default total summary
    if total_movies and total_shows:
        msg = (f"You have {total_movies:,} movies and {total_shows:,} shows "
               f"with {total_episodes:,} episodes in your library.")
    elif total_movies:
        msg = f"You have {total_movies:,} movies in your library."
    elif total_shows:
        msg = f"You have {total_shows:,} shows with {total_episodes:,} episodes."
    else:
        msg = "Your library looks empty right now — I couldn't find any items."

    return {
        'type': 'library_stats',
        'status': 'ok',
        'message': msg,
        'data': {
            'total_movies': total_movies,
            'total_shows': total_shows,
            'total_episodes': total_episodes,
            'movies_by_library': stats.get('movies', {}),
            'tv_shows_by_library': stats.get('tv_shows', {}),
        },
    }
