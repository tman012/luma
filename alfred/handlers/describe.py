"""Describe handler — identify top pick via AI, announce it, and open the carousel."""

import logging
import os
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

FLASK_BASE = 'http://localhost:5001'
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')


def handle_describe(description: str) -> Dict[str, Any]:
    """Search for matching movies/shows, announce the top pick, and open the carousel.

    The TTS announces what Alfred thinks it is, while simultaneously navigating
    to the Describe It carousel which shows the full ranked results.
    """
    # Try to get the top pick for TTS announcement
    top_title = None
    top_year = None
    try:
        headers = {'Content-Type': 'application/json'}
        if SERVICE_API_KEY:
            headers['X-API-Key'] = SERVICE_API_KEY
        resp = requests.post(
            f'{FLASK_BASE}/api/search/natural',
            headers=headers,
            json={'description': description, 'media_type': 'any'},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        results = (data.get('owned') or []) + (data.get('unowned') or [])
        results.sort(key=lambda r: r.get('ai_rank', 999))

        if results:
            top_title = results[0].get('title')
            top_year = results[0].get('year')
    except Exception as e:
        logger.error('Describe search failed (will still open carousel): %s', e)

    # Build TTS message — announce the pick if we got one
    if top_title:
        year_str = f" from {top_year}" if top_year else ''
        message = f"I think that's \"{top_title}\"{year_str}. Pulling up results now."
    else:
        message = "Let me find that for you."

    return {
        'type': 'describe',
        'status': 'ok',
        'message': message,
        'data': {'description': description},
        'suggested_ui_action': {
            'action': 'open_describe_results',
            'description': description,
        },
    }
