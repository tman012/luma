"""
Voice Assistant Service — GPT-powered voice command orchestration.

Uses OpenAI function calling to interpret voice commands and execute
actions against Plex (search, playback, control) via existing services.
"""

from app.err_text import public as _err_public
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions for OpenAI function calling
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_library",
            "description": "Search the Plex media library for movies or TV shows by title or keywords. Returns top results with rating keys for playback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (movie or show title, keywords)",
                    },
                    "media_type": {
                        "type": "string",
                        "enum": ["movie", "show"],
                        "description": "Filter by media type. Omit to search all.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_media",
            "description": "Start playing a specific media item in a room. Use the rating_key from search results. For TV shows, use the episode rating key.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rating_key": {
                        "type": "integer",
                        "description": "Plex rating key of the item to play",
                    },
                    "room_name": {
                        "type": "string",
                        "description": "Name of the room to play in (e.g. 'Family Room', 'Bedroom')",
                    },
                    "resume": {
                        "type": "boolean",
                        "description": "Whether to resume from where the user left off",
                    },
                },
                "required": ["rating_key", "room_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_playback",
            "description": "Control current playback: pause, play/resume, stop, skip next, skip previous.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play", "pause", "stop", "next", "previous"],
                        "description": "Playback control action",
                    },
                    "room_name": {
                        "type": "string",
                        "description": "Room name. If omitted, controls the first active session.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "now_playing",
            "description": "Get information about what is currently playing across all rooms.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_rooms",
            "description": "List all available rooms and their devices.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def _build_system_prompt(rooms: List[Dict], sessions: List[Dict]) -> str:
    """Build the system prompt with current context."""
    room_list = ', '.join(r.get('name', 'Unknown') for r in rooms) if rooms else 'No rooms configured'

    session_info = 'Nothing currently playing.'
    if sessions:
        parts = []
        for s in sessions:
            title = s.get('media_title', 'Unknown')
            state = s.get('state', 'unknown')
            client = s.get('name', 'Unknown')
            parts.append(f"- {title} ({state}) on {client}")
        session_info = 'Currently playing:\n' + '\n'.join(parts)

    return f"""You are a voice assistant for a home media system powered by Plex.
You help users find and play movies and TV shows, and control playback.

Available rooms: {room_list}
{session_info}

Rules:
- Be concise. Keep responses to 1-2 sentences.
- When the user asks to play something, search first, then play the best match.
- For TV shows, find the next unwatched episode. Search first, then check on-deck for the show.
- Match room names flexibly (case-insensitive, partial match is OK).
- If a search returns multiple results, play the most likely match. If ambiguous, ask.
- Confirm actions you take (e.g. "Playing Cast Away in Family Room").
- If something fails, explain briefly and suggest alternatives.
- When controlling playback without a room specified, use the first active session.

Voice transcription fixes — the input comes from speech-to-text which often
mangles movie titles. Apply these corrections BEFORE searching:
- Words that run together: "castaway" → search "Cast Away", "diehard" → "Die Hard",
  "topgun" → "Top Gun", "starwars" → "Star Wars", "ironman" → "Iron Man".
- If a search returns 0 results or only wrong matches, try:
  1. Add spaces to compound words (e.g. "darknight" → "dark night" → "The Dark Knight").
  2. Remove common filler words the user might have said ("the movie", "that film", "a film called").
  3. Try the title with "The" prepended.
- The user's intent matters more than the literal transcription. Use your knowledge of
  real movie/show titles to interpret what they meant."""


def _fuzzy_match_room(room_name: str,
                      rooms: List[Dict]) -> Optional[Dict]:
    """Fuzzy-match a room name against configured rooms."""
    if not rooms:
        return None
    name_lower = room_name.lower().strip()
    # Exact match first
    for r in rooms:
        if r.get('name', '').lower() == name_lower:
            return r
    # Substring match
    for r in rooms:
        rn = r.get('name', '').lower()
        if name_lower in rn or rn in name_lower:
            return r
    return None


def _tmdb_title_lookup(query: str, settings) -> Optional[str]:
    """Search TMDb for the correct title when Plex can't find it.

    Tries the original query first, then attempts splitting compound words
    (e.g. "castaway" → "cast away") since speech-to-text often merges words.
    """
    api_key = settings.get('apis.tmdb.api_key', '')
    if not api_key:
        return None

    import requests

    def _search(q: str) -> Optional[str]:
        try:
            resp = requests.get(
                'https://api.themoviedb.org/3/search/movie',
                params={'api_key': api_key, 'query': q, 'page': 1},
                timeout=5,
            )
            resp.raise_for_status()
            results = resp.json().get('results', [])
            if results:
                return results[0].get('title')
        except Exception:
            pass
        return None

    # 1. Try the query as-is
    title = _search(query)
    if title:
        return title

    # 2. Try splitting single-word queries at common word boundaries
    #    e.g. "castaway" → "cast away", "diehard" → "die hard"
    words = query.split()
    if len(words) == 1 and len(words[0]) > 4:
        word = words[0]
        # Try inserting a space at each position
        for i in range(2, len(word) - 1):
            spaced = f'{word[:i]} {word[i:]}'
            title = _search(spaced)
            if title:
                return title

    # 3. Try prepending "The"
    title = _search(f'The {query}')
    if title:
        return title

    return None


def _enrich_search_results(results: List[Dict], plex_service,
                           note: str = '') -> Dict[str, Any]:
    """Enrich Plex search results with TV on-deck info."""
    enriched = []
    for r in results[:5]:
        entry = {
            'ratingKey': r['ratingKey'],
            'title': r['title'],
            'year': r.get('year'),
            'type': r['type'],
        }
        if r['type'] == 'show':
            on_deck = plex_service.get_on_deck(limit=50)
            show_rk = r['ratingKey']
            next_ep = None
            for od in on_deck:
                if od.get('show_rating_key') == show_rk:
                    next_ep = od
                    break
            if next_ep:
                entry['next_episode'] = {
                    'ratingKey': next_ep['ratingKey'],
                    'title': next_ep.get('title', ''),
                    'season': next_ep.get('season_number'),
                    'episode': next_ep.get('episode_number'),
                }
            else:
                try:
                    episodes = plex_service.get_show_episodes(show_rk)
                    for ep in episodes:
                        entry['next_episode'] = {
                            'ratingKey': ep['rating_key'],
                            'title': ep.get('title', ''),
                            'season': ep.get('season_number'),
                            'episode': ep.get('episode_number'),
                        }
                        break
                except Exception:
                    pass
        enriched.append(entry)
    result: Dict[str, Any] = {'results': enriched}
    if note:
        result['note'] = note
    return result


def _execute_tool(tool_name: str, args: Dict[str, Any],
                  plex_service, settings, resolve_playback_fn) -> Dict[str, Any]:
    """Execute a tool call and return the result."""
    try:
        if tool_name == 'search_library':
            query = args.get('query', '')
            media_type = args.get('media_type')
            results = plex_service.hub_search(query, limit=5, media_type=media_type)

            # If no results, try TMDb to get the correct title and retry
            if not results:
                corrected = _tmdb_title_lookup(query, settings)
                if corrected and corrected.lower() != query.lower():
                    results = plex_service.hub_search(corrected, limit=5, media_type=media_type)
                    if results:
                        return _enrich_search_results(results, plex_service,
                                                      note=f'Searched as "{corrected}"')

            if not results:
                return {'results': [], 'message': f'No results found for "{query}"'}

            return _enrich_search_results(results, plex_service)

        elif tool_name == 'play_media':
            rating_key = args.get('rating_key')
            room_name = args.get('room_name', '')
            resume = args.get('resume', False)

            rooms = settings.get('devices.rooms') or []
            room = _fuzzy_match_room(room_name, rooms)
            if not room:
                return {'success': False, 'error': f'Room "{room_name}" not found. Available: {", ".join(r.get("name", "?") for r in rooms)}'}

            # Use the playback resolution helper
            result = resolve_playback_fn(
                rating_key=rating_key,
                room_id=room.get('id'),
                resume=resume,
            )
            return result

        elif tool_name == 'control_playback':
            action = args.get('action', '')
            room_name = args.get('room_name')

            plex_url = settings.get('plex.url', '')
            plex_token = settings.get('plex.token', '')

            from app.plex_client_service import get_sessions, control_client

            # Find the client to control
            client_name = None
            if room_name:
                rooms = settings.get('devices.rooms') or []
                room = _fuzzy_match_room(room_name, rooms)
                if room:
                    # Find active session matching this room's devices
                    sessions = get_sessions(plex_url, plex_token)
                    for s in sessions:
                        client_name = s.get('name')
                        break  # Use first session in room for now
            if not client_name:
                # Use first active session
                sessions = get_sessions(plex_url, plex_token)
                if sessions:
                    client_name = sessions[0].get('name')

            if not client_name:
                return {'success': False, 'error': 'No active playback session found'}

            result = control_client(plex_url, plex_token, client_name, action)
            return result

        elif tool_name == 'now_playing':
            plex_url = settings.get('plex.url', '')
            plex_token = settings.get('plex.token', '')
            from app.plex_client_service import get_sessions
            sessions = get_sessions(plex_url, plex_token)
            if not sessions:
                return {'sessions': [], 'message': 'Nothing is currently playing'}
            return {'sessions': [
                {
                    'title': s.get('media_title', ''),
                    'year': s.get('media_year'),
                    'type': s.get('media_type', ''),
                    'state': s.get('state', ''),
                    'client': s.get('name', ''),
                    'progress': f"{s.get('progress', 0):.0f}%",
                    'position': s.get('media_position_str', ''),
                    'duration': s.get('media_duration_str', ''),
                }
                for s in sessions
            ]}

        elif tool_name == 'list_rooms':
            rooms = settings.get('devices.rooms') or []
            return {'rooms': [
                {
                    'name': r.get('name', 'Unknown'),
                    'id': r.get('id', ''),
                    'devices': [
                        {'name': d.get('name', ''), 'platform': d.get('platform', '')}
                        for d in (r.get('devices') or [])
                        if isinstance(d, dict)
                    ],
                }
                for r in rooms if isinstance(r, dict)
            ]}

        else:
            return {'error': f'Unknown tool: {tool_name}'}

    except Exception as e:
        logger.error('Tool %s execution failed: %s', tool_name, e)
        return {'error': _err_public(e, 'Tool failed', 'voice')}


def process_voice_command(
    transcript: str,
    history: List[Dict[str, str]],
    settings,
    plex_service,
    resolve_playback_fn,
) -> Dict[str, Any]:
    """Process a voice command transcript using GPT with function calling.

    Returns: {reply: str, actions_taken: [{tool, args, result}]}
    """
    # Load OpenAI config
    openai_key = settings.get('apis.openai.api_key', '')
    model = settings.get('apis.openai.model', 'gpt-4o-mini')

    if not openai_key:
        return {'reply': 'OpenAI is not configured. Please add your API key in Settings.', 'actions_taken': []}

    # Get current context
    rooms = settings.get('devices.rooms') or []
    plex_url = settings.get('plex.url', '')
    plex_token = settings.get('plex.token', '')

    from app.plex_client_service import get_sessions
    try:
        sessions = get_sessions(plex_url, plex_token)
    except Exception:
        sessions = []

    system_prompt = _build_system_prompt(rooms, sessions)

    # Build messages
    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history (last 10 turns)
    for h in history[-10:]:
        role = h.get('role', 'user')
        content = h.get('content', '')
        if role in ('user', 'assistant') and content:
            messages.append({"role": role, "content": content})

    # Add current transcript
    messages.append({"role": "user", "content": transcript})

    try:
        from app import openai_client as openai_lib
        client = openai_lib.OpenAI(api_key=openai_key, max_retries=0)

        actions_taken = []
        max_rounds = 3
        deadline = time.time() + 45

        for _ in range(max_rounds):
            if time.time() > deadline:
                break

            # Determine API kwargs based on model
            is_new_model = model.startswith('gpt-5') or model.startswith('o')
            api_kwargs: Dict[str, Any] = {
                'model': model,
                'messages': messages,
                'tools': TOOL_DEFINITIONS,
                'tool_choice': 'auto',
                'timeout': 30,
            }
            if is_new_model:
                api_kwargs['max_completion_tokens'] = 1000
            else:
                api_kwargs['max_tokens'] = 500

            response = client.chat.completions.create(**api_kwargs)
            choice = response.choices[0]

            if choice.finish_reason == 'tool_calls' and choice.message.tool_calls:
                # Append assistant message with tool calls
                messages.append(choice.message)

                # Execute each tool call
                for tool_call in choice.message.tool_calls:
                    fn_name = tool_call.function.name
                    try:
                        fn_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        fn_args = {}

                    result = _execute_tool(fn_name, fn_args, plex_service, settings, resolve_playback_fn)
                    actions_taken.append({
                        'tool': fn_name,
                        'args': fn_args,
                        'result': result,
                    })

                    # Add tool result to messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    })
            else:
                # Final text response
                reply = choice.message.content or "Done."
                return {'reply': reply, 'actions_taken': actions_taken}

        # If we exhausted rounds, get a final response without tools
        api_kwargs_final: Dict[str, Any] = {
            'model': model,
            'messages': messages,
            'timeout': 15,
        }
        if is_new_model:
            api_kwargs_final['max_completion_tokens'] = 500
        else:
            api_kwargs_final['max_tokens'] = 300

        final = client.chat.completions.create(**api_kwargs_final)
        reply = final.choices[0].message.content or "I completed the requested actions."
        return {'reply': reply, 'actions_taken': actions_taken}

    except Exception as e:
        error_type = type(e).__name__
        logger.error('Voice command failed (%s): %s', error_type, e)

        if 'quota' in str(e).lower() or 'billing' in str(e).lower():
            return {'reply': 'OpenAI quota exceeded. Please check your billing.', 'actions_taken': [], 'error': _err_public(e, 'Voice command failed', 'voice')}
        if 'rate' in str(e).lower():
            return {'reply': 'Too many requests. Please try again in a moment.', 'actions_taken': [], 'error': _err_public(e, 'Voice command failed', 'voice')}

        return {'reply': 'Sorry, something went wrong.', 'actions_taken': [], 'error': _err_public(e, 'Voice command failed', 'voice')}
