"""Watch history + progress handlers.

Two intents:
  - watch_progress: "what episode of Chosen are we on", "where am I in Cast Away"
  - watch_history: "what did I watch yesterday", "what movies did I watch recently"

watch_progress uses the Plex On Deck (Continue Watching) list via Luma.
watch_history queries Plex's session history API directly.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

import requests

from config import PLEX_URL, PLEX_TOKEN

logger = logging.getLogger(__name__)

FLASK_BASE = os.getenv('FLASK_BASE', 'http://localhost:5001')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')


# ---------------------------------------------------------------------------
# watch_progress — uses Continue Watching (on-deck)
# ---------------------------------------------------------------------------

def _fetch_on_deck() -> list:
    """Fetch the user's Plex Continue Watching list via Luma."""
    try:
        headers = {}
        if SERVICE_API_KEY:
            headers['X-API-Key'] = SERVICE_API_KEY
        resp = requests.get(
            f'{FLASK_BASE}/api/plex/continue_watching',
            headers=headers,
            params={'limit': 50},
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json().get('items', []) or []
    except Exception as e:
        logger.warning('Continue Watching fetch failed: %s', e)
        return []


def _fuzzy_match(query: str, target: str) -> bool:
    import re
    def norm(s: str) -> str:
        s = (s or '').lower().strip()
        s = re.sub(r"^(the|a|an)\s+", "", s)
        s = re.sub(r"[^\w\s]", "", s)
        return re.sub(r"\s+", " ", s).strip()
    nq, nt = norm(query), norm(target)
    return bool(nq and nt and (nq in nt or nt in nq))


def handle_watch_progress(title: str) -> Dict[str, Any]:
    """Report which episode or how far into a movie the user is."""
    if not title:
        return {'type': 'watch_progress', 'status': 'error',
                'message': "Which show or movie?", 'data': {}}

    on_deck = _fetch_on_deck()
    for item in on_deck:
        if item.get('type') == 'episode':
            if _fuzzy_match(title, item.get('show_title', '')):
                show = item.get('show_title', title)
                s = item.get('season_number')
                e = item.get('episode_number')
                ep_title = item.get('episode_title', '')
                progress = item.get('progress_percent', 0)
                reason = item.get('reason', '')

                if reason == 'next_up' or progress == 0:
                    msg = (f"You're on season {s} episode {e} of {show}"
                           f" — \"{ep_title}\". Ready to start." if ep_title
                           else f"You're on season {s} episode {e} of {show}. Ready to start.")
                else:
                    mins_in = (item.get('view_offset', 0) or 0) // 60000
                    msg = (f"You're on season {s} episode {e} of {show}"
                           f" — \"{ep_title}\", about {mins_in} minutes in." if ep_title
                           else f"You're on season {s} episode {e} of {show}, about {mins_in} minutes in.")
                return {'type': 'watch_progress', 'status': 'ok',
                        'message': msg, 'data': {
                            'show': show, 'season': s, 'episode': e,
                            'progress_percent': progress,
                        }}
        else:
            if _fuzzy_match(title, item.get('title', '')):
                movie = item.get('title', title)
                progress = item.get('progress_percent', 0)
                mins_in = (item.get('view_offset', 0) or 0) // 60000
                duration_min = (item.get('duration', 0) or 0) // 60000
                remaining = max(0, duration_min - mins_in)
                msg = (f"You're {mins_in} minutes into {movie}"
                       f" — about {remaining} minutes left ({int(progress)}% done).")
                return {'type': 'watch_progress', 'status': 'ok',
                        'message': msg, 'data': {
                            'title': movie, 'progress_percent': progress,
                            'minutes_in': mins_in, 'remaining': remaining,
                        }}

    return {'type': 'watch_progress', 'status': 'ok',
            'message': f"I don't see {title} in your Continue Watching list.",
            'data': {'found': False}}


# ---------------------------------------------------------------------------
# watch_history — uses Plex session history API
# ---------------------------------------------------------------------------

# period → (start_offset_days, end_offset_days) relative to today
_PERIOD_WINDOWS = {
    'today':       (0, 0),
    'tonight':     (0, 0),
    'yesterday':   (1, 1),
    'last night':  (1, 1),
    'this week':   (7, 0),
    'recently':    (14, 0),
    'last week':   (14, 7),
}


# Plex account ID for the server owner. The history endpoint returns ALL
# shared users by default; filtering to accountID=1 restricts it to the
# household's primary account (Taylor). If this ever needs to change,
# it can be made configurable via config.py.
_PLEX_ACCOUNT_ID = '1'


def _fetch_plex_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch recently played items from Plex's session history.

    Filtered to the server owner's account only — shared users' watches
    are excluded.
    """
    if not PLEX_URL or not PLEX_TOKEN:
        return []
    try:
        resp = requests.get(
            f'{PLEX_URL}/status/sessions/history/all',
            headers={
                'X-Plex-Token': PLEX_TOKEN,
                'Accept': 'application/xml',
            },
            params={
                'sort': 'viewedAt:desc',
                'limit': limit,
                'accountID': _PLEX_ACCOUNT_ID,
            },
            timeout=10,
        )
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        items: List[Dict[str, Any]] = []
        for v in root:
            vtype = v.get('type', '')
            viewed_at = int(v.get('viewedAt', 0))
            if vtype == 'episode':
                items.append({
                    'type': 'episode',
                    'show_title': v.get('grandparentTitle', ''),
                    'season': int(v.get('parentIndex', 0) or 0),
                    'episode': int(v.get('index', 0) or 0),
                    'episode_title': v.get('title', ''),
                    'viewed_at': viewed_at,
                })
            elif vtype == 'movie':
                items.append({
                    'type': 'movie',
                    'title': v.get('title', ''),
                    'year': v.get('year', ''),
                    'viewed_at': viewed_at,
                })
            elif vtype == 'track':
                continue  # skip music
            else:
                items.append({
                    'type': vtype or 'unknown',
                    'title': v.get('title', ''),
                    'viewed_at': viewed_at,
                })
        return items
    except Exception as e:
        logger.exception('Plex history fetch failed: %s', e)
        return []


def _day_label(ts: int) -> str:
    """Human-readable day label for a Unix timestamp (ET approximation)."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=4)
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    d = dt.date()
    if d == today:
        return 'today'
    if d == today - timedelta(days=1):
        return 'yesterday'
    return d.strftime('%A')


def _join(items: List[str]) -> str:
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ', '.join(items[:-1]) + f", and {items[-1]}"


def handle_watch_history(period: Optional[str] = None,
                         media_type: Optional[str] = None) -> Dict[str, Any]:
    """Report what the user has watched recently, optionally filtered."""
    history = _fetch_plex_history(limit=100)
    if not history:
        return {'type': 'watch_history', 'status': 'error',
                'message': "I couldn't pull your watch history from Plex.",
                'data': {}}

    # Resolve the time window
    period_key = (period or 'recently').lower().strip()
    window = _PERIOD_WINDOWS.get(period_key, _PERIOD_WINDOWS['recently'])
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    start_date = (now_et - timedelta(days=window[0])).date()
    end_date = (now_et - timedelta(days=window[1])).date()

    # Filter by date window
    filtered = []
    for item in history:
        viewed_at = item.get('viewed_at', 0)
        if not viewed_at:
            continue
        item_date = (datetime.fromtimestamp(viewed_at, tz=timezone.utc) - timedelta(hours=4)).date()
        if start_date <= item_date <= end_date:
            filtered.append(item)

    # Filter by media type if requested
    if media_type:
        mt = media_type.lower().strip()
        if mt in ('movie', 'movies', 'film', 'films'):
            filtered = [i for i in filtered if i['type'] == 'movie']
        elif mt in ('show', 'shows', 'tv', 'episode', 'episodes', 'series'):
            filtered = [i for i in filtered if i['type'] == 'episode']

    if not filtered:
        desc = period_key if period_key != 'recently' else 'in the last two weeks'
        mt_desc = f' {media_type}' if media_type else ''
        return {'type': 'watch_history', 'status': 'ok',
                'message': f"I don't see any{mt_desc} watched {desc}.",
                'data': {'count': 0}}

    # Build a natural summary. Collapse episodes into show names.
    movies = []
    show_episodes: Dict[str, int] = {}  # show_title → episode count
    for item in filtered:
        if item['type'] == 'movie':
            movies.append(item['title'])
        elif item['type'] == 'episode':
            show = item.get('show_title', 'Unknown Show')
            show_episodes[show] = show_episodes.get(show, 0) + 1

    parts = []
    if movies:
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for m in movies:
            if m.lower() not in seen:
                seen.add(m.lower())
                unique.append(m)
        if len(unique) <= 5:
            parts.append(f"Movies: {_join(unique)}")
        else:
            parts.append(f"{len(unique)} movies including {_join(unique[:4])}")

    if show_episodes:
        show_parts = []
        for show, count in sorted(show_episodes.items(), key=lambda x: -x[1]):
            if count > 1:
                show_parts.append(f"{count} episodes of {show}")
            else:
                show_parts.append(show)
        if len(show_parts) <= 5:
            parts.append(f"Shows: {_join(show_parts)}")
        else:
            parts.append(f"{len(show_parts)} shows including {_join(show_parts[:4])}")

    desc = period_key if period_key != 'recently' else 'recently'
    msg = f"Here's what you watched {desc}. " + '. '.join(parts) + '.'

    return {
        'type': 'watch_history',
        'status': 'ok',
        'message': msg,
        'data': {
            'count': len(filtered),
            'movies': movies,
            'shows': dict(show_episodes),
        },
    }
