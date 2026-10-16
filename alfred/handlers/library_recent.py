"""Library recent-additions handler.

Answers: "what's new", "what got added this week", "anything fresh in 4K",
"new shows this month", "what hit the library since Friday".
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

FLASK_BASE = os.getenv('FLASK_BASE', 'http://localhost:5001')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')


def _auth_headers() -> Dict[str, str]:
    return {'X-API-Key': SERVICE_API_KEY} if SERVICE_API_KEY else {}


def _period_to_seconds(period: Optional[str]) -> Optional[int]:
    """Parse a spoken period into a cutoff age in seconds.

    Returns None if the period is empty or unknown (no filter applied).
    """
    if not period:
        return None
    p = period.lower().strip()
    # Straight mappings
    mapping = {
        'today': 1 * 86400,
        'tonight': 1 * 86400,
        'overnight': 1 * 86400,
        'yesterday': 2 * 86400,
        'this week': 7 * 86400,
        'past week': 7 * 86400,
        'last week': 7 * 86400,
        'this month': 30 * 86400,
        'past month': 30 * 86400,
        'last month': 30 * 86400,
        'recently': 28 * 86400,
        'lately': 28 * 86400,
        'the other day': 7 * 86400,
    }
    if p in mapping:
        return mapping[p]
    # "since Friday", "since Monday" — approximate to "past week"
    if re.match(r'^since\s+\w+', p):
        return 7 * 86400
    # "last N days"
    m = re.match(r'^(?:last|past)\s+(\d+)\s+days?$', p)
    if m:
        return int(m.group(1)) * 86400
    return None


def _normalize_quality(quality: Optional[str]) -> Optional[str]:
    """Normalize spoken quality into a Plex-resolution token."""
    if not quality:
        return None
    q = quality.lower().strip()
    if any(k in q for k in ('4k', 'uhd', '2160')):
        return '4k'
    if 'remux' in q:
        return 'remux'
    if '1080' in q or 'full hd' in q or 'full rip' in q:
        return '1080'
    if '720' in q or q == 'hd':
        return '720'
    if 'sd' in q:
        return 'sd'
    return None


def _fetch_recent_movies() -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/library/recent',
            headers=_auth_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get('movies', []) or []
    except Exception as e:
        logger.warning('Recent movies fetch failed: %s', e)
        return []


def _fetch_recent_shows() -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/library/episodes',
            headers=_auth_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json() or {}
        # Endpoint shape: { shows: [...] } or { episodes: [...] }
        return body.get('shows') or body.get('episodes') or []
    except Exception as e:
        logger.warning('Recent episodes fetch failed: %s', e)
        return []


def _filter_by_period(items: List[Dict[str, Any]], max_age_s: Optional[int]) -> List[Dict[str, Any]]:
    if not max_age_s:
        return items
    cutoff = time.time() - max_age_s
    return [i for i in items if (i.get('added_at') or 0) >= cutoff]


def _filter_by_quality(items: List[Dict[str, Any]], quality: Optional[str]) -> List[Dict[str, Any]]:
    """Filter by resolution token. Uses item['videoResolution'] if present,
    otherwise leaves the list alone (the /api/library/recent endpoint does
    not currently include codec/resolution fields — the handler will speak
    about that honestly)."""
    if not quality:
        return items
    def matches(item: Dict[str, Any]) -> bool:
        res = str(item.get('videoResolution') or item.get('resolution') or '').lower()
        if quality == '4k':
            return res in ('4k', '2160', '2160p', 'uhd')
        if quality == 'remux':
            # 'remux' isn't a Plex resolution value — check title
            return 'remux' in (item.get('title', '').lower())
        if quality == '1080':
            return res in ('1080', '1080p', 'fhd')
        if quality == '720':
            return res in ('720', '720p', 'hd')
        if quality == 'sd':
            return res in ('sd', '480', '480p', '576', '576p')
        return True
    filtered = [i for i in items if matches(i)]
    return filtered


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


def handle_library_recent(media_type: Optional[str] = None,
                          quality: Optional[str] = None,
                          period: Optional[str] = None) -> Dict[str, Any]:
    """Answer "what's new / recently added" queries."""
    mt = (media_type or 'all').lower().strip()
    if mt in ('movies', 'film', 'films'):
        mt = 'movie'
    if mt in ('shows', 'series', 'episode', 'episodes'):
        mt = 'tv'

    age = _period_to_seconds(period)
    qnorm = _normalize_quality(quality)

    movies: List[Dict[str, Any]] = []
    shows: List[Dict[str, Any]] = []

    if mt in ('movie', 'all'):
        movies = _filter_by_period(_fetch_recent_movies(), age)
        movies = _filter_by_quality(movies, qnorm)
    if mt in ('tv', 'all'):
        shows = _filter_by_period(_fetch_recent_shows(), age)

    total = len(movies) + len(shows)
    if total == 0:
        period_desc = f' in the {period}' if period else ''
        quality_desc = f' in {quality}' if quality else ''
        return {
            'type': 'library_recent',
            'status': 'ok',
            'message': f"Nothing new{quality_desc} has landed{period_desc}.",
            'data': {'count': 0, 'media_type': mt, 'quality': quality, 'period': period},
        }

    # Voice summary: media-type aware
    movie_titles = [m.get('title', '') for m in movies]
    show_titles = [s.get('title', '') for s in shows]

    parts = []
    if movies:
        n = len(movies)
        names = _join_titles(movie_titles, top=3)
        noun = 'movie' if n == 1 else 'movies'
        parts.append(f"{n} new {noun}" + (f" — {names}" if names else ''))
    if shows:
        n = len(shows)
        names = _join_titles(show_titles, top=3)
        noun = 'show' if n == 1 else 'shows'
        parts.append(f"{n} new {noun}" + (f" — {names}" if names else ''))

    period_desc = f' {period}' if period else ' recently'
    quality_desc = f' ({quality})' if quality else ''
    msg = (' and '.join(parts)).rstrip('.') + f'{quality_desc}{period_desc}.'
    # Truncate overly long messages — TTS cadence suffers beyond ~50 words
    msg = msg[:260]

    # UI action: take the user to the most relevant page
    ui_action: Dict[str, Any] = {'action': 'navigate', 'path': '/'}
    # If single movie result, open its modal directly
    if len(movies) == 1 and not shows and movies[0].get('tmdb_id'):
        ui_action = {
            'action': 'open_modal',
            'media_type': 'movie',
            'tmdb_id': movies[0]['tmdb_id'],
        }
    elif mt == 'tv' and shows:
        ui_action = {'action': 'navigate', 'path': '/explore'}
    else:
        ui_action = {'action': 'navigate', 'path': '/explore'}

    return {
        'type': 'library_recent',
        'status': 'ok',
        'message': msg,
        'data': {
            'count': total,
            'movies_count': len(movies),
            'shows_count': len(shows),
            'movies': [{'title': m.get('title'), 'year': m.get('year'),
                        'tmdb_id': m.get('tmdb_id'), 'added_at': m.get('added_at')}
                       for m in movies[:20]],
            'shows': [{'title': s.get('title'), 'year': s.get('year'),
                       'tmdb_id': s.get('tmdb_id'), 'added_at': s.get('added_at')}
                      for s in shows[:20]],
            'media_type': mt,
            'quality': quality,
            'period': period,
        },
        'suggested_ui_action': ui_action,
    }
