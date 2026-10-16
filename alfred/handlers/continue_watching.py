"""Continue-watching list handler.

Answers "show me my continue watching", "what's halfway done", "anything I
started but didn't finish".
"""

import logging
import os
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

FLASK_BASE = os.getenv('FLASK_BASE', 'http://localhost:5001')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')


def _auth_headers() -> Dict[str, str]:
    return {'X-API-Key': SERVICE_API_KEY} if SERVICE_API_KEY else {}


def _fetch_on_deck() -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/plex/continue_watching',
            headers=_auth_headers(),
            params={'limit': 25},
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json().get('items') or []
    except Exception as e:
        logger.warning('Continue-watching fetch failed: %s', e)
        return []


def _join(items: List[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ', '.join(items[:-1]) + f", and {items[-1]}"


def handle_continue_watching_list() -> Dict[str, Any]:
    items = _fetch_on_deck()
    if not items:
        return {
            'type': 'continue_watching_list',
            'status': 'ok',
            'message': "Your continue-watching list is empty.",
            'data': {'count': 0},
            'suggested_ui_action': {'action': 'navigate', 'path': '/'},
        }

    labels: List[str] = []
    for it in items[:3]:
        if it.get('type') == 'episode':
            show = it.get('show_title') or 'a show'
            s = it.get('season_number')
            e = it.get('episode_number')
            ep = f" S{s}E{e}" if s and e else ''
            labels.append(f"{show}{ep}")
        else:
            title = it.get('title') or 'a movie'
            labels.append(title)

    count = len(items)
    if count <= 3:
        msg = f"On your continue-watching list: {_join(labels)}."
    else:
        msg = f"{count} on your continue-watching list — including {_join(labels)}."

    return {
        'type': 'continue_watching_list',
        'status': 'ok',
        'message': msg[:260],
        'data': {
            'count': count,
            'items': [{
                'type': it.get('type'),
                'title': it.get('title'),
                'show_title': it.get('show_title'),
                'season': it.get('season_number'),
                'episode': it.get('episode_number'),
                'progress_percent': it.get('progress_percent'),
                'rating_key': it.get('rating_key'),
                'tmdb_id': it.get('tmdb_id'),
            } for it in items[:20]],
        },
        'suggested_ui_action': {'action': 'navigate', 'path': '/'},
    }
