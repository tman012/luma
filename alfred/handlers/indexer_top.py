"""Indexer-top handler — answers 'what's available to download right now'.

Queries the Prowlarr-backed explore cache via /api/explore/top with spoken
quality / tracker filters.
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


# Spoken quality token → backend quality filter.
#
# Bias toward the *premium* interpretation when the user is non-specific —
# people don't say "REMUX" naturally. "4K" alone means the best 4K (REMUX).
# "1080p" / "BluRay" alone means 1080p REMUX. Web-DL only when explicit.
_QUALITY_MAP = {
    # 4K family — premium default
    '4k': '4k_remux',
    'uhd': '4k_remux',
    '2160': '4k_remux',
    '2160p': '4k_remux',
    '4k movies': '4k_remux',
    '4k film': '4k_remux',
    '4k uhd': '4k_remux',
    '4k remux': '4k_remux',
    '4k bluray': '4k_remux',
    '4k blu-ray': '4k_remux',
    '4k web': '4k_web',
    '4k webdl': '4k_web',
    '4k web-dl': '4k_web',
    # 1080p family — premium default
    '1080': '1080p_remux',
    '1080p': '1080p_remux',
    'full hd': '1080p_remux',
    'fhd': '1080p_remux',
    'bluray': '1080p_remux',
    'blu-ray': '1080p_remux',
    'blu ray': '1080p_remux',
    'full rip': '1080p_remux',
    '1080 remux': '1080p_remux',
    '1080p remux': '1080p_remux',
    '1080p web': '1080p_web',
    '1080p webdl': '1080p_web',
    '1080p web-dl': '1080p_web',
    'webdl': '1080p_web',
    'web-dl': '1080p_web',
    # Generic remux (no resolution preference)
    'remux': 'remux',
}


_TRACKER_ALIASES = {
    'phd': 'PHD',
    'privatehd': 'PHD',
    'private hd': 'PHD',
    'bhd': 'BHD',
    'beyondhd': 'BHD',
    'beyond hd': 'BHD',
    'ipt': 'IPT',
    'iptorrents': 'IPT',
    'btn': 'BTN',
    'broadcasthenet': 'BTN',
    'broadcast the net': 'BTN',
}


def _normalize_quality(q: Optional[str]) -> Optional[str]:
    if not q:
        return None
    key = q.lower().strip()
    if key in _QUALITY_MAP:
        return _QUALITY_MAP[key]
    # Check if it's already a canonical token
    if key in {'4k', '4k_remux', '4k_web', 'remux', '1080p', '1080p_remux', '1080p_web'}:
        return key
    return None


def _normalize_tracker(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    key = t.lower().strip()
    if key in _TRACKER_ALIASES:
        return _TRACKER_ALIASES[key]
    return t.upper()


def _format_seeders(n: int) -> str:
    if n >= 1000:
        return f'{n / 1000:.1f}K'
    return str(n)


def handle_indexer_top(quality: Optional[str] = None,
                       tracker: Optional[str] = None,
                       sort: Optional[str] = None,
                       min_seeders: Optional[int] = None,
                       since_days: Optional[int] = None,
                       limit: Optional[int] = None) -> Dict[str, Any]:
    """Return the top available releases, filtered by quality/tracker/date.

    `since_days` filters to releases with a pub_date within the last N days.
    The backend doesn't support this natively, so we over-fetch then filter.
    """
    params: Dict[str, Any] = {}
    q_norm = _normalize_quality(quality)
    t_norm = _normalize_tracker(tracker)
    if q_norm:
        params['quality'] = q_norm
    if t_norm:
        params['tracker'] = t_norm
    if sort in ('seeders', 'newest'):
        params['sort'] = sort
    elif sort and 'date' in sort.lower():
        params['sort'] = 'newest'
    else:
        params['sort'] = 'seeders'
    if isinstance(min_seeders, int) and min_seeders > 0:
        params['min_seeders'] = min_seeders
    requested_limit = max(1, min(int(limit or 10), 50))
    # Over-fetch when filtering by date so we can still return enough items
    params['limit'] = min(100, requested_limit * (5 if since_days else 1))

    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/explore/top',
            params=params,
            headers=_auth_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json() or {}
    except Exception as e:
        logger.warning('Explore-top fetch failed: %s', e)
        return {
            'type': 'indexer_top',
            'status': 'error',
            'message': "I couldn't reach the tracker cache right now.",
            'data': {},
        }

    movies: List[Dict[str, Any]] = body.get('movies') or []

    # Apply date filter client-side
    if since_days and movies:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(since_days))
        def _within(m: Dict[str, Any]) -> bool:
            ts = m.get('pub_date') or m.get('publishDate')
            if not ts:
                return False
            try:
                dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                return dt >= cutoff
            except Exception:
                return False
        movies = [m for m in movies if _within(m)]

    # Apply requested limit after filtering
    movies = movies[:requested_limit]

    if not movies:
        q_desc = f' in {quality}' if quality else ''
        t_desc = f' on {t_norm}' if t_norm else ''
        return {
            'type': 'indexer_top',
            'status': 'ok',
            'message': f"No releases available{q_desc}{t_desc} right now.",
            'data': {'count': 0, 'filters': body.get('filters', {})},
            'suggested_ui_action': {'action': 'navigate', 'path': '/now-available'},
        }

    top = movies[0]
    title = top.get('title') or top.get('clean_title') or 'a release'
    seeders = int(top.get('seeders') or 0)
    tracker_name = top.get('tracker') or top.get('indexer') or ''
    quality_label = top.get('resolution') or top.get('quality') or quality or ''
    # The explore cache item often carries year + clean title separately
    year = top.get('year')

    # Speakable sentence: top item + count
    count = len(movies)
    parts: List[str] = []
    if count == 1:
        parts.append("One release")
    else:
        parts.append(f"{count} available")
    q_hint = quality or quality_label
    if q_hint:
        parts.append(f"in {q_hint}")
    if t_norm:
        parts.append(f"on {t_norm}")
    top_line = (f"{title} ({year})" if year else title)
    seed_hint = f" with {_format_seeders(seeders)} seeders" if seeders else ''
    tracker_hint = f" from {tracker_name}" if tracker_name else ''

    summary = ' '.join(parts) + f". Top is {top_line}{seed_hint}{tracker_hint}."
    summary = summary[:260]

    return {
        'type': 'indexer_top',
        'status': 'ok',
        'message': summary,
        'data': {
            'count': count,
            'filters': body.get('filters', {}),
            'top': {
                'title': title,
                'year': year,
                'seeders': seeders,
                'tracker': tracker_name,
                'resolution': quality_label,
                'size': top.get('size') or top.get('sizeBytes'),
                'guid': top.get('guid'),
                'tmdb_id': top.get('tmdb_id'),
            },
            'releases': [{
                'title': m.get('title'),
                'year': m.get('year'),
                'seeders': m.get('seeders'),
                'tracker': m.get('tracker') or m.get('indexer'),
                'resolution': m.get('resolution'),
                'tmdb_id': m.get('tmdb_id'),
                'poster_url': m.get('poster_url'),
                'pub_date': m.get('pub_date'),
                'media_type': 'movie',
            } for m in movies[:20]],
        },
        'suggested_ui_action': {'action': 'navigate', 'path': '/now-available'},
    }
