"""Claude fallback handler — forwards unmatched commands to Claude API."""

import logging
import re
import time
from typing import Any, Dict, Optional

import requests

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    'You are Alfred, a voice assistant. Your reply is spoken aloud via TTS.\n\n'
    'RULES:\n'
    '- Reply in ONLY 1-2 sentences, max 30 words.\n'
    '- NEVER say "based on search results", "let me search", "I can see", '
    '"from the search results", or narrate your process.\n'
    '- NEVER tell the user to check another app or website.\n'
    '- Just state the answer directly as if you know it.\n'
    '- Use web search when you need current data (scores, weather, news).\n\n'
    'GOOD: "The Orioles are up 8-3 over the Rangers in the 7th."\n'
    'BAD: "Based on the search results, I found that the Orioles are..."'
)

_API_URL = 'https://api.anthropic.com/v1/messages'

# Keywords that suggest the question needs live/current data
_NEEDS_SEARCH_RE = re.compile(
    r'\b(?:weather|forecast|temperature|rain|snow'
    r'|stock|market|price|trading'
    r'|news|headline|happening|current|latest|today|tonight|right now|live'
    r'|score|game|playing|playoff|championship'
    r'|release date|come out|coming out|premier|opening'
    r'|who won|who is winning|results)\b',
    re.I,
)

_WEB_SEARCH_TOOL = {
    'type': 'web_search_20250305',
    'name': 'web_search',
    'max_uses': 5,
    'user_location': {
        'type': 'approximate',
        'region': 'Maryland',
        'country': 'US',
        'timezone': 'America/New_York',
    },
}

# Location cache from settings — refreshed periodically
_location_cache: Dict[str, Any] = {}
_location_cache_ts: float = 0
_LOCATION_CACHE_TTL = 300  # 5 minutes


def _get_user_location() -> Dict[str, str]:
    """Fetch location from main app settings (zip code, state, country)."""
    global _location_cache_ts
    import time as _time
    now = _time.time()
    if _location_cache and (now - _location_cache_ts) < _LOCATION_CACHE_TTL:
        return _location_cache

    try:
        resp = requests.get('http://localhost:5001/api/kiosk/settings', timeout=5)
        if resp.ok:
            data = resp.json()
            weather = data.get('ui', {}).get('daily_kiosk', {}).get('weather', {})
            zip_code = weather.get('zip_code', '')
            state = weather.get('state', '')
            country = weather.get('country_code', 'US')

            # Look up city name from zip if we have weather configured
            city = ''
            if zip_code and state:
                city = f'{zip_code}, {state}'

            loc = {
                'type': 'approximate',
                'city': city,
                'region': state or 'Maryland',
                'country': country or 'US',
                'timezone': 'America/New_York',
            }
            _location_cache.update(loc)
            _location_cache_ts = now
            return loc
    except Exception as e:
        logger.debug('Failed to fetch location from settings: %s', e)

    return _WEB_SEARCH_TOOL['user_location']


def _call_claude(messages: list, use_search: bool,
                 system_prompt: Optional[str] = None) -> Dict[str, Any]:
    """Make a Claude API call, with optional web search and retry."""
    headers = {
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': 'claude-haiku-4-5-20251001',
        'max_tokens': 256,
        'system': system_prompt or _SYSTEM_PROMPT,
        'messages': messages,
    }
    if use_search:
        # Build tool with dynamic location from settings
        location = _get_user_location()
        search_tool = {
            'type': 'web_search_20250305',
            'name': 'web_search',
            'max_uses': 5,
            'user_location': location,
        }
        payload['tools'] = [search_tool]

    resp = requests.post(_API_URL, headers=headers, json=payload, timeout=30)

    # Retry once on 429/529
    if resp.status_code in (429, 529):
        wait = min(int(resp.headers.get('retry-after', 3)), 10)
        logger.info('Claude API %d, retrying in %ds', resp.status_code, wait)
        time.sleep(wait)
        resp = requests.post(_API_URL, headers=headers, json=payload, timeout=30)

    # If web search is causing 529, fall back to no-search call
    if resp.status_code in (429, 529) and use_search:
        logger.info('Web search overloaded, falling back to no-search')
        payload.pop('tools', None)
        resp = requests.post(_API_URL, headers=headers, json=payload, timeout=30)

    resp.raise_for_status()
    return resp.json()


def _extract_answer(data: Dict) -> str:
    """Extract the final answer text from Claude's response."""
    blocks = data.get('content', [])
    # Find text after the last search result (if any)
    last_search_idx = -1
    for i, block in enumerate(blocks):
        if block.get('type') in ('web_search_tool_result', 'server_tool_use'):
            last_search_idx = i
    content = ''
    for block in blocks[last_search_idx + 1:]:
        if block.get('type') == 'text':
            content += block.get('text', '')
    return content


def handle_fallback(command: str, context: Optional[Dict[str, Any]] = None,
                    history: Optional[list] = None,
                    user: Optional[Any] = None) -> Dict[str, Any]:
    """Forward an unmatched command to Claude API, with per-user persona."""
    if not ANTHROPIC_API_KEY:
        return {
            'type': 'error',
            'status': 'error',
            'message': "I can only handle downloads, storage, and search right now.",
            'data': {},
        }

    # Inject per-user persona into the system prompt so tone adapts to the
    # speaker (home user vs. external guest) without changing any handler logic.
    system_prompt = _SYSTEM_PROMPT

    # Add location context so Nova can answer weather/local questions accurately
    location = _get_user_location()
    loc_parts = []
    if location.get('city'):
        loc_parts.append(location['city'])
    elif location.get('region'):
        loc_parts.append(location['region'])
    if loc_parts:
        system_prompt += f"\n\nThe user is located in {', '.join(loc_parts)}. Use this for weather and local questions."

    if user is not None and getattr(user, 'persona', ''):
        system_prompt += '\n\n' + user.persona

    # Build messages from conversation history
    messages = []
    if history:
        for h in history[-4:]:
            role = h.get('role', 'user')
            content = h.get('content', '')
            if role in ('user', 'assistant') and content:
                messages.append({'role': role, 'content': content})
    messages.append({'role': 'user', 'content': command})

    # Only attach web search tool when the question likely needs current data
    use_search = bool(_NEEDS_SEARCH_RE.search(command))

    try:
        data = _call_claude(messages, use_search, system_prompt=system_prompt)
        content = _extract_answer(data)

        if not content:
            return {
                'type': 'error',
                'status': 'error',
                'message': 'Alfred had nothing to say.',
                'data': {},
            }

        # Strip markdown formatting that doesn't work in TTS
        clean = content.strip()
        for prefix in ('**', '__', '# ', '## ', '### '):
            clean = clean.replace(prefix, '')
        clean = clean.replace('**', '').replace('__', '')

        return {
            'type': 'answer',
            'status': 'ok',
            'message': clean,
            'data': {},
        }

    except requests.Timeout:
        return {
            'type': 'error',
            'status': 'error',
            'message': 'Alfred took too long to think. Try again.',
            'data': {},
        }
    except Exception as e:
        logger.error('Claude fallback failed: %s', e)
        return {
            'type': 'error',
            'status': 'error',
            'message': 'Something went wrong reaching Alfred.',
            'data': {},
        }
