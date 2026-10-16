"""
Command Router — classifies incoming commands and dispatches to handlers.

Two-tier routing:
  1. Fast-path regex for latency-sensitive commands (smart home, time/date)
  2. LLM classifier for everything else — no more giant regex table

Returns structured AlfredResponse dicts.
"""

import re
import logging
from typing import Any, Dict, Optional, Tuple

from handlers.storage import handle_storage
from handlers.downloads import handle_downloads, handle_queue
from handlers.free_space import handle_free_space
from handlers.search import handle_search_movie, handle_search_tv
from handlers.ownership import handle_ownership_check
from handlers.download_action import (handle_download_movie, handle_download_tv,
                                      handle_watchlist_add, handle_watchlist_next,
                                      handle_watchlist_refine,
                                      _build_candidate_response)
from handlers.smart_home import (handle_lights_on, handle_lights_off,
                                  handle_volume, handle_fan_on, handle_fan_off)
from handlers.describe import handle_describe
from handlers.show_media import handle_show_media
from handlers.openclaw_fallback import handle_fallback
from handlers.playback import handle_play
from handlers.quick import handle_time, handle_date
from handlers.sports import handle_score, handle_next_game, handle_event
from handlers.watch_history import handle_watch_progress, handle_watch_history
from handlers.kitchen import (
    handle_kitchen_grocery_add,
    handle_kitchen_grocery_list,
    handle_kitchen_grocery_remove,
    handle_kitchen_grocery_check,
    handle_kitchen_grocery_clear,
    handle_kitchen_meal_today,
    handle_kitchen_meal_week,
    handle_kitchen_meal_plan_next_week,
    handle_kitchen_meal_cooked,
    handle_kitchen_pantry_out,
    handle_kitchen_recipe_lookup,
    handle_kitchen_recipe_to_grocery,
)
# Phase 1: library / watchlist / indexer / discovery / vibe
from handlers.library_recent import handle_library_recent
from handlers.library_quality import handle_library_quality, handle_library_stats
from handlers.watchlist import (handle_watchlist_list, handle_watchlist_check,
                                handle_watchlist_remove)
from handlers.indexer_top import handle_indexer_top
from handlers.discovery import handle_trending, handle_popular, handle_in_theaters
from handlers.vibe import handle_vibe_mood, handle_vibe_pick_for_me
# Phase 2: TV calendar / ownership + playback control + continue watching
from handlers.tv import (handle_tv_airing, handle_tv_next_episode,
                         handle_tv_season_ownership)
from handlers.playback_control import (handle_playback_pause,
                                       handle_playback_resume_session,
                                       handle_playback_stop,
                                       handle_playback_seek,
                                       handle_playback_subtitles,
                                       handle_playback_now_playing)
from handlers.continue_watching import handle_continue_watching_list
from users import User, registry as user_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fast-path regex — ONLY for latency-sensitive commands where <200ms matters
# Smart home and time/date skip the LLM entirely.
# ---------------------------------------------------------------------------

_ROOMS = (r'(?:family\s+room|living\s+room|kitchen(?:\s+island)?|office|basement'
          r"|dining\s+room|mikey'?s?\s+room|nursery|laundry(?:\s+room)?|porch|lawn"
          r'|deck|garage|foyer|(?:upstairs\s+)?hallway|spare\s+(?:bed)?room)')

_FAST_PATTERNS: list[Tuple[re.Pattern, str, Any]] = [
    # --- Brightness ---
    (re.compile(r'(?:set|dim|brighten)\s+(?:the\s+)?(.+?)\s+(?:lights?\s+)?(?:to\s+)?(\d+)\s*(?:percent|%)?\s*\.?$', re.I),
     'lights_brightness', lambda m: {'room': m.group(1), 'brightness': int(m.group(2))}),
    (re.compile(r'(?:(.+?)\s+)?lights?\s+(?:to\s+)?(\d+)\s*(?:percent|%)?\s*\.?$', re.I),
     'lights_brightness', lambda m: {'room': m.group(1), 'brightness': int(m.group(2))}),
    (re.compile(r'(' + _ROOMS + r')\s+(?:to\s+)?(\d+)\s*(?:percent|%)?\s*\.?$', re.I),
     'lights_brightness', lambda m: {'room': m.group(1), 'brightness': int(m.group(2))}),

    # --- Lights off ---
    (re.compile(r'(?:turn\s+off|shut\s+off|kill|cut)\s+(?:the\s+)?(?:(.+?)\s+)?lights?', re.I),
     'lights_off', lambda m: {'room': m.group(1)}),
    (re.compile(r'(?:(.+?)\s+)?lights?\s+off\b', re.I),
     'lights_off', lambda m: {'room': m.group(1)}),
    (re.compile(r'(?:turn\s+off|shut\s+off)\s+(?:the\s+)?(' + _ROOMS + r')\s*\.?$', re.I),
     'lights_off', lambda m: {'room': m.group(1)}),

    # --- Lights on ---
    (re.compile(r'(?:turn\s+on)\s+(?:the\s+)?(?:(.+?)\s+)?lights?', re.I),
     'lights_on', lambda m: {'room': m.group(1)}),
    (re.compile(r'(?:(.+?)\s+)?lights?\s+on\b', re.I),
     'lights_on', lambda m: {'room': m.group(1)}),
    (re.compile(r'(?:turn\s+on)\s+(?:the\s+)?(' + _ROOMS + r')\s*\.?$', re.I),
     'lights_on', lambda m: {'room': m.group(1)}),

    # --- Volume ---
    (re.compile(r'(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+)?(\d+)', re.I),
     'volume_set', lambda m: {'level': int(m.group(1))}),
    (re.compile(r'(?:turn\s+)?(?:the\s+)?volume\s+(up|down)', re.I),
     'volume_adjust', lambda m: {'direction': m.group(1).lower()}),

    # --- Fans ---
    (re.compile(r'(?:turn\s+on)\s+(?:the\s+)?(?:(.+?)\s+)?fan', re.I),
     'fan_on', lambda m: {'room': m.group(1)}),
    (re.compile(r'(?:turn\s+off|shut\s+off|kill|cut)\s+(?:the\s+)?(?:(.+?)\s+)?fan', re.I),
     'fan_off', lambda m: {'room': m.group(1)}),
    (re.compile(r'(?:(.+?)\s+)?fan\s+(on|off)\b', re.I),
     'fan_toggle', lambda m: {'room': m.group(1), 'action': m.group(2).lower()}),

    # --- Time / Date (instant, no API needed) ---
    (re.compile(r'\b(?:what(?:\'s|\s+is)\s+the\s+time|what\s+time\s+is\s+it|current\s+time)\b', re.I),
     'time', None),
    (re.compile(r'\b(?:what(?:\'s|\s+is)\s+(?:today(?:\'s)?|the)\s+date|what\s+day\s+is\s+it|today\'s\s+date)\b', re.I),
     'date', None),

    # --- Playback control (sub-200ms, bypass LLM) ---
    # "pause", "pause it", "pause playback"
    (re.compile(r'^(?:pause|pause\s+it|pause\s+playback)\s*\.?$', re.I),
     'playback_pause', None),
    # "stop", "stop it", "stop watching", "turn it off" (media context)
    (re.compile(r'^(?:stop|stop\s+it|stop\s+watching|stop\s+playback|turn\s+it\s+off)\s*\.?$', re.I),
     'playback_stop', None),
    # "resume" / "unpause" / "keep watching" / bare "play it"
    (re.compile(r'^(?:resume|unpause|keep\s+watching|keep\s+going|continue|play\s+it)\s*\.?$', re.I),
     'playback_resume_session', None),
    # "rewind 10 seconds", "skip ahead 30 seconds", "back 2 minutes", "forward 1 minute"
    (re.compile(r'^(?:(rewind|back)|(skip(?:\s+ahead)?|forward|jump(?:\s+ahead)?))\s+(\d+)\s*(second|sec|minute|min|s|m)s?\s*\.?$', re.I),
     'playback_seek', lambda m: {
         'direction': 'back' if m.group(1) else 'forward',
         'seconds': int(m.group(3)) * (60 if m.group(4).lower().startswith('min') or m.group(4).lower() == 'm' else 1),
     }),
    # "subtitles on/off", "captions on/off", bare "subtitles"
    (re.compile(r'^(?:subtitles?|captions?)(?:\s+(on|off))?\s*\.?$', re.I),
     'playback_subtitles', lambda m: {'enabled': m.group(1).lower() == 'on'} if m.group(1) else {}),
]


# Words that, if they appear inside the captured "room" fragment of a
# smart-home fast-path match, suggest the regex is over-capturing — the
# user is naming a specific fixture rather than a whole-room toggle. We
# bail to the LLM classifier in that case so `device` gets extracted.
_DEVICE_HINT_WORDS = {
    'lamp', 'light', 'fixture', 'sconce', 'chandelier', 'bulb', 'strip',
    'ceiling', 'under', 'cabinet', 'desk', 'floor', 'table', 'wall',
    'accent', 'pendant', 'island',
}


def _fast_match(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Try fast-path regex patterns. Returns (handler_key, args) or None.

    Bails out (returning None) for smart-home matches when the captured
    room fragment looks like it actually contains a device name — more
    than two words, or contains device-hint words like "lamp" or
    "ceiling". These fall through to the LLM classifier where `device`
    can be properly split from `room`.
    """
    for pattern, key, extractor in _FAST_PATTERNS:
        match = pattern.search(text)
        if match:
            args = extractor(match) if extractor else {}
            room = (args.get('room') or '').strip().lower()
            if room and key in ('lights_on', 'lights_off', 'lights_brightness',
                                'fan_on', 'fan_off', 'fan_toggle'):
                words = room.split()
                if len(words) > 2 or any(w in _DEVICE_HINT_WORDS for w in words):
                    return None
            return (key, args)
    return None


# ---------------------------------------------------------------------------
# Handler dispatch — shared by fast-path and classifier
# ---------------------------------------------------------------------------

def _execute_handler(handler_key: str, args: Dict[str, Any],
                     user: Optional[User] = None) -> Dict[str, Any]:
    """Execute a single handler by key, gated by the user's role."""
    if user is None:
        user = user_registry.default_luma()
    if not user.can(handler_key):
        logger.info('Auth denied: user=%s role=%s intent=%s', user.id, user.role, handler_key)
        return {
            'type': 'unauthorized',
            'status': 'error',
            'message': "Sorry, I can't help with that one.",
            'data': {},
        }
    try:
        if handler_key == 'lights_on':
            return handle_lights_on(room=args.get('room'), device=args.get('device'))
        elif handler_key == 'lights_off':
            return handle_lights_off(room=args.get('room'), device=args.get('device'))
        elif handler_key == 'lights_brightness':
            return handle_lights_on(room=args.get('room'),
                                    device=args.get('device'),
                                    brightness=args.get('brightness'))
        elif handler_key == 'volume_set':
            return handle_volume(level=args.get('level'))
        elif handler_key == 'volume_adjust':
            return handle_volume(direction=args.get('direction'))
        elif handler_key == 'fan_on':
            return handle_fan_on(room=args.get('room'), device=args.get('device'))
        elif handler_key == 'fan_off':
            return handle_fan_off(room=args.get('room'), device=args.get('device'))
        elif handler_key == 'fan_toggle':
            if args.get('action') == 'on':
                return handle_fan_on(room=args.get('room'), device=args.get('device'))
            else:
                return handle_fan_off(room=args.get('room'), device=args.get('device'))
        elif handler_key == 'time':
            return handle_time()
        elif handler_key == 'date':
            return handle_date()
        elif handler_key == 'describe':
            return handle_describe(**args)
        elif handler_key == 'show_media':
            return handle_show_media(**args)
        elif handler_key == 'navigate':
            path = args.get('path', '/')
            # Map path to a friendly TTS name
            _PAGE_NAMES = {
                '/': 'home',
                '/explore': 'Explore',
                '/library': 'Luma AI',
                '/watchlist': 'your watchlist',
                '/now-available': 'Now Available',
                '/browse': 'Browse Library',
                '/daily': 'the Daily Kiosk',
                '/settings': 'Settings',
            }
            page_name = _PAGE_NAMES.get(path, path)
            return {
                'type': 'navigate',
                'status': 'ok',
                'message': f"Taking you to {page_name}.",
                'data': {'path': path},
                'suggested_ui_action': {
                    'action': 'navigate',
                    'path': path,
                },
            }
        elif handler_key == 'ownership_check':
            return handle_ownership_check(**args)
        elif handler_key == 'score':
            return handle_score(**args)
        elif handler_key == 'next_game':
            return handle_next_game(**args)
        elif handler_key == 'event':
            return handle_event(**args)
        elif handler_key == 'play':
            return handle_play(**args)
        elif handler_key == 'storage':
            return handle_storage()
        elif handler_key == 'free_space':
            return handle_free_space()
        elif handler_key == 'downloads':
            return handle_downloads()
        elif handler_key == 'queue':
            return handle_queue()
        elif handler_key == 'download_movie':
            return handle_download_movie(**args)
        elif handler_key == 'download_tv':
            return handle_download_tv(**args)
        elif handler_key == 'watch_progress':
            return handle_watch_progress(**args)
        elif handler_key == 'watch_history':
            return handle_watch_history(**args)
        elif handler_key == 'search_movie':
            return handle_search_movie(**args)
        elif handler_key == 'search_tv':
            return handle_search_tv(**args)
        # ── Phase 1: library / watchlist / indexer / discovery / vibe ──
        elif handler_key == 'library_recent':
            return handle_library_recent(**args)
        elif handler_key == 'library_quality':
            return handle_library_quality(**args)
        elif handler_key == 'library_stats':
            return handle_library_stats(**args)
        elif handler_key == 'watchlist_list':
            return handle_watchlist_list()
        elif handler_key == 'watchlist_check':
            return handle_watchlist_check(**args)
        elif handler_key == 'watchlist_remove':
            return handle_watchlist_remove(**args)
        elif handler_key == 'indexer_top':
            return handle_indexer_top(**args)
        elif handler_key == 'trending':
            return handle_trending(**args)
        elif handler_key == 'popular':
            return handle_popular(**args)
        elif handler_key == 'in_theaters':
            return handle_in_theaters()
        elif handler_key == 'vibe_mood':
            return handle_vibe_mood(**args)
        elif handler_key == 'vibe_pick_for_me':
            return handle_vibe_pick_for_me()
        # ── Phase 2: TV / playback control / continue watching ──────
        elif handler_key == 'tv_airing':
            return handle_tv_airing(**args)
        elif handler_key == 'tv_next_episode':
            return handle_tv_next_episode(**args)
        elif handler_key == 'tv_season_ownership':
            return handle_tv_season_ownership(**args)
        elif handler_key == 'playback_pause':
            return handle_playback_pause()
        elif handler_key == 'playback_resume_session':
            return handle_playback_resume_session()
        elif handler_key == 'playback_stop':
            return handle_playback_stop()
        elif handler_key == 'playback_seek':
            return handle_playback_seek(**args)
        elif handler_key == 'playback_subtitles':
            return handle_playback_subtitles(**args)
        elif handler_key == 'playback_now_playing':
            return handle_playback_now_playing()
        elif handler_key == 'continue_watching_list':
            return handle_continue_watching_list()
        # ── Bosley Kitchen ──────────────────────────────────────────
        elif handler_key == 'kitchen_grocery_add':
            return handle_kitchen_grocery_add(**args)
        elif handler_key == 'kitchen_grocery_list':
            return handle_kitchen_grocery_list()
        elif handler_key == 'kitchen_grocery_remove':
            return handle_kitchen_grocery_remove(**args)
        elif handler_key == 'kitchen_grocery_check':
            return handle_kitchen_grocery_check(**args)
        elif handler_key == 'kitchen_grocery_clear':
            return handle_kitchen_grocery_clear()
        elif handler_key == 'kitchen_meal_today':
            return handle_kitchen_meal_today(**args)
        elif handler_key == 'kitchen_meal_week':
            return handle_kitchen_meal_week()
        elif handler_key == 'kitchen_meal_plan_next_week':
            return handle_kitchen_meal_plan_next_week()
        elif handler_key == 'kitchen_meal_cooked':
            return handle_kitchen_meal_cooked(**args)
        elif handler_key == 'kitchen_pantry_out':
            return handle_kitchen_pantry_out(**args)
        elif handler_key == 'kitchen_recipe_lookup':
            return handle_kitchen_recipe_lookup(**args)
        elif handler_key == 'kitchen_recipe_to_grocery':
            return handle_kitchen_recipe_to_grocery(**args)
        else:
            return {
                'type': 'error',
                'status': 'error',
                'message': f'Handler "{handler_key}" is not implemented yet.',
                'data': {},
            }
    except Exception as e:
        logger.exception('Handler %s failed', handler_key)
        return {
            'type': 'error',
            'status': 'error',
            'message': f'Something went wrong: {e}',
            'data': {},
        }


# ---------------------------------------------------------------------------
# Main dispatch — fast path → LLM classifier → handler
# ---------------------------------------------------------------------------

def dispatch(command: str, context: Optional[Dict[str, Any]] = None,
             history: Optional[list] = None,
             user: Optional[User] = None) -> Dict[str, Any]:
    """Route a command and execute the matching handler.

    1. Fast-path regex for smart home + time/date (instant, no LLM)
    2. LLM classifier for everything else
    3. Fallback to old Claude handler if classifier fails

    `user` identifies the speaker for authorization and persona. If None,
    defaults to the configured home user (Luma voice on the kiosk).

    Returns a structured response dict.
    """
    if user is None:
        user = user_registry.default_luma()

    text = re.sub(r'[.,!?;:]+$', '', command.strip()).strip()
    # Strip polite preambles for routing decisions only ("Thank you! Can you add X" → "Can you add X")
    routing_text = _PREAMBLE_RE.sub('', text).strip()

    # -----------------------------------------------------------------
    # Tier 0a: Pending free-space confirmation (yes/no follow-up)
    # -----------------------------------------------------------------
    if (context or {}).get('pendingFreeSpace'):
        text_lower = routing_text.lower().strip()
        if re.search(r'\b(yes|yea|yeah|yep|sure|do it|go ahead|please|ok|okay|yup|absolutely)\b', text_lower):
            return handle_free_space()
        return {
            'type': 'storage_status',
            'status': 'ok',
            'message': 'No problem, leaving everything as is.',
            'data': {},
        }

    # -----------------------------------------------------------------
    # Tier 0: Pending watchlist confirmation (yes/no/refine follow-up)
    # -----------------------------------------------------------------
    pending = (context or {}).get('pendingWatchlist')
    # chat_id is present only when the request came via Telegram; None for kiosk voice
    _requester_chat_id = (context or {}).get('chat_id')
    if pending and isinstance(pending, dict):
        text_lower = text.lower().strip()

        # --- User is picking from a disambiguation list ---
        if pending.get('awaiting_disambiguation'):
            options = pending.get('disambiguation_options', [])
            _dis_query = pending.get('original_query', '')
            _dis_profile = pending.get('quality_profile', 'TFlix')
            _dis_check = pending.get('check_and_add', False)
            _dis_quality = pending.get('quality')
            _dis_season = pending.get('season')
            _dis_episode = pending.get('episode')

            chosen = None

            # "number 2", "the second one", bare digit, "#3"
            num_match = re.search(r'\b([1-5])\b', text_lower)
            if num_match:
                idx = int(num_match.group(1)) - 1
                if 0 <= idx < len(options):
                    chosen = options[idx]

            # Fallback: substring match against option titles
            if chosen is None:
                for opt in options:
                    opt_title_words = opt.get('title', '').lower().split()
                    for word in text_lower.split():
                        if len(word) >= 4 and word in opt_title_words:
                            chosen = opt
                            break
                    if chosen:
                        break

            if chosen:
                return _build_candidate_response(
                    chosen, _dis_profile, 0, [chosen], _dis_query,
                    check_and_add=_dis_check, quality=_dis_quality,
                    season=_dis_season, episode=_dis_episode,
                )

            # Cannot parse — re-prompt with numbered list
            re_lines = []
            for i, o in enumerate(options, 1):
                yr = f" ({o.get('year')})" if o.get('year') else ''
                re_lines.append(f"{i}. {o.get('title', '?')}{yr}")
            return {
                'type': 'download_request',
                'status': 'pending',
                'message': "Sorry, which one? " + ", ".join(re_lines),
                'data': {'pending_watchlist': dict(pending)},
            }

        # --- User is providing refinement details (year, actor, etc.) ---
        if pending.get('awaiting_refinement'):
            logger.info('Watchlist refine: query=%s details=%s', pending.get('original_query'), text)
            return handle_watchlist_refine(
                query=pending.get('original_query', ''),
                refinement=text,
                profile=pending.get('quality_profile', 'TFlix'),
                check_and_add=pending.get('check_and_add', False),
                quality=pending.get('quality'),
                season=pending.get('season'),
                episode=pending.get('episode'),
            )

        # --- TV show confirmed, waiting for season selection ---
        if pending.get('awaiting_season_selection'):
            _title = pending.get('title', '')
            _tmdb_id = pending.get('tmdb_id')
            _year = pending.get('year')
            _profile = pending.get('quality_profile', 'TFlix')

            # "all", "whole show", "everything", etc.
            if re.search(r'\b(?:all|whole|everything|all\s+of\s+it|all\s+seasons|entire|every)\b',
                         text_lower):
                logger.info('Season selection: all seasons for %s', _title)
                from handlers.download_action import _check_plex_ownership
                _owned = _check_plex_ownership(_title)
                if _owned:
                    _libraries = _owned.get('all_libraries',
                                            [_owned.get('library_name') or 'your library'])
                    if len(_libraries) == 1:
                        _libs_note = f"your {_libraries[0]} library"
                    elif len(_libraries) == 2:
                        _libs_note = f"{_libraries[0]} and {_libraries[1]}"
                    else:
                        _libs_note = ', '.join(_libraries[:-1]) + f', and {_libraries[-1]}'
                    return {
                        'type': 'ownership', 'status': 'ok',
                        'message': f"{_title} is already available in {_libs_note}.",
                        'data': {'found': True, 'title': _title, 'all_libraries': _libraries},
                    }
                return handle_watchlist_add(
                    title=_title, tmdb_id=_tmdb_id, year=_year,
                    quality_profile=_profile, media_type='tv',
                    requester_chat_id=_requester_chat_id,
                )

            # "season 3", "3", "the third", etc.
            _season_match = re.search(r'\b(?:season\s+)?(\d{1,2})\b', text_lower)
            if _season_match:
                _season_num = int(_season_match.group(1))
                logger.info('Season selection: season %d for %s', _season_num, _title)
                # Ownership check for the specific season
                from handlers.tv import _sonarr_lookup, _fetch_episode_ownership
                _series_rec = _sonarr_lookup(_title)
                if _series_rec and _series_rec.get('tvdbId'):
                    _tvdb_check = int(_series_rec['tvdbId'])
                    _ownership = _fetch_episode_ownership(_tvdb_check)
                    _owned_keys = set((_ownership.get('episodes') or {}).keys())
                    _sonarr_seas = _series_rec.get('seasons') or []
                    _expected = 0
                    for _s in _sonarr_seas:
                        if _s.get('seasonNumber') == _season_num:
                            _expected = int(
                                (_s.get('statistics') or {}).get('totalEpisodeCount') or 0
                            )
                            break
                    _season_eps = [k for k in _owned_keys if k.startswith(f's{_season_num}e')]
                    if _expected and len(_season_eps) >= _expected:
                        return {
                            'type': 'ownership', 'status': 'ok',
                            'message': (
                                f"All {_expected} episodes of {_title} season {_season_num} "
                                "are already in your library."
                            ),
                            'data': {'found': True, 'title': _title},
                        }
                return handle_watchlist_add(
                    title=_title, tmdb_id=_tmdb_id, year=_year,
                    quality_profile=_profile, media_type='tv',
                    requester_chat_id=_requester_chat_id,
                    season=_season_num,
                )

            # Didn't understand — re-prompt with the pending state unchanged
            return {
                'type': 'download_request',
                'status': 'pending',
                'message': (
                    "Which season? Say a number like 'season 3', "
                    "or 'all' to add the whole show."
                ),
                'data': {'pending_watchlist': dict(pending)},
            }

        # --- Ownership check: Alfred echoed the TMDb match, awaiting yes/no ---
        # Guarantees the user confirms the resolved title BEFORE we hit Plex.
        if pending.get('awaiting_ownership_confirm'):
            from handlers.ownership import handle_ownership_confirmed, _build_ownership_confirm

            # Hard cancel
            if re.match(r'^(?:never\s*mind|cancel|forget\s*it|stop|nevermind)\b', text_lower):
                return {
                    'type': 'ownership', 'status': 'ok',
                    'message': "Okay, cancelled.", 'data': {},
                }

            # Yes → proceed to actual library lookup with the confirmed title
            if re.match(r'^(?:yes|yea|yeah|yep|yup|sure|go ahead|please|absolutely|ok|okay|do it|that\'s? (?:it|the one)|correct|right)\b', text_lower):
                logger.info('Ownership confirm: checking library for %s (tmdb=%s)',
                            pending.get('title'), pending.get('tmdb_id'))
                return handle_ownership_confirmed(
                    title=pending.get('title', ''),
                    tmdb_id=pending.get('tmdb_id'),
                    year=pending.get('year'),
                    media_type=pending.get('media_type'),
                    quality=pending.get('quality'),
                )

            # No / not that → cycle to next TMDb candidate
            if re.match(r'^(?:no|nah|nope|not that|wrong|that\'s not|next|different)\b', text_lower):
                candidates = pending.get('candidates', []) or []
                index = pending.get('candidate_index', 0)
                next_index = index + 1
                query = pending.get('original_query', '')
                quality = pending.get('quality')
                if next_index < len(candidates):
                    logger.info('Ownership confirm: cycling to candidate %d for %s', next_index, query)
                    return _build_ownership_confirm(
                        candidates[next_index], next_index, candidates, query, quality,
                    )
                # Out of candidates
                return {
                    'type': 'ownership', 'status': 'ok',
                    'message': (
                        f"I'm out of matches for \"{query}\". "
                        "Can you give me more details — like the year or who stars in it?"
                    ),
                    'data': {},
                }

            # Unclear reply — re-prompt
            title = pending.get('title', '')
            year = pending.get('year', '')
            label = f"{title} ({year})" if year else title
            return {
                'type': 'ownership', 'status': 'pending',
                'message': f"Sorry, did you mean {label}? Yes or no.",
                'data': {'pending_watchlist': dict(pending)},
            }

        # --- Already owned: user picking a quality profile ---
        if pending.get('awaiting_quality'):
            # Map spoken quality to profile name
            from handlers.download_action import _resolve_quality_profile, _QUALITY_TO_PROFILE
            # Check if they said a profile name directly (e.g. "TFlix")
            spoken = text.lower().strip()
            profile = None
            # Direct profile name match
            for q, p in _QUALITY_TO_PROFILE.items():
                if q in spoken:
                    profile = p
                    break
            if not profile and 'tflix' in spoken:
                profile = 'TFlix'
            if not profile:
                profile = _resolve_quality_profile(spoken)
            logger.info('Watchlist quality selection: %s → profile %s', spoken, profile)
            return handle_watchlist_add(
                title=pending.get('title', ''),
                tmdb_id=pending.get('tmdb_id'),
                year=pending.get('year'),
                quality_profile=profile,
                media_type=pending.get('media_type'),
                requester_chat_id=_requester_chat_id,
            )

        # --- Already owned: Nova offered an upgrade (4K / Full Rips) ---
        if pending.get('awaiting_upgrade'):
            options = pending.get('upgrade_options', []) or []
            from handlers.download_action import _QUALITY_TO_PROFILE

            # Hard decline
            if re.match(r'^(?:no|nah|nope|never\s*mind|cancel|forget\s*it|don\'t|nevermind|skip|no thanks)\b', text_lower):
                logger.info('Upgrade declined for %s', pending.get('title'))
                return {
                    'type': 'ownership',
                    'status': 'ok',
                    'message': "",
                    'data': {},
                }

            # Detect a specific quality in the speech
            profile = None
            if '4k' in text_lower or 'uhd' in text_lower or '2160' in text_lower or 'four k' in text_lower:
                profile = _QUALITY_TO_PROFILE.get('4k', '2160p Remux')
            elif 'full rip' in text_lower or 'full-rip' in text_lower or 'remux' in text_lower or 'bluray' in text_lower or 'blu-ray' in text_lower or '1080' in text_lower:
                profile = _QUALITY_TO_PROFILE.get('full rip', '1080p Remux')

            # Plain yes with a single option — auto-select that option
            if not profile and re.match(r'^(?:yes|yea|yeah|yep|yup|sure|please|go ahead|absolutely|ok|okay|do it)\b', text_lower):
                if len(options) == 1:
                    only_opt = options[0].lower()
                    if '4k' in only_opt:
                        profile = _QUALITY_TO_PROFILE.get('4k', '2160p Remux')
                    elif 'full rip' in only_opt or 'remux' in only_opt:
                        profile = _QUALITY_TO_PROFILE.get('full rip', '1080p Remux')
                else:
                    # Ambiguous yes with multiple options — ask which
                    opts_str = ' or '.join(options)
                    return {
                        'type': 'download_request',
                        'status': 'pending',
                        'message': f"Which quality — {opts_str}?",
                        'data': {'pending_watchlist': dict(pending)},
                    }

            if profile:
                logger.info('Upgrade accepted: %s → %s', pending.get('title'), profile)
                return handle_watchlist_add(
                    title=pending.get('title', ''),
                    tmdb_id=pending.get('tmdb_id'),
                    year=pending.get('year'),
                    quality_profile=profile,
                    media_type=pending.get('media_type', 'movie'),
                    requester_chat_id=_requester_chat_id,
                )

            # Didn't understand — re-prompt with the same options
            opts_str = ' or '.join(options) if options else '4K or Full Rips'
            return {
                'type': 'download_request',
                'status': 'pending',
                'message': f"Sorry, which would you like — {opts_str}?",
                'data': {'pending_watchlist': dict(pending)},
            }

        # --- Already owned: "yes I want a different quality" → ask which ---
        if pending.get('already_owned'):
            if re.match(r'^(?:yes|yea|yeah|yep|yup|sure|please|go ahead|absolutely|ok|okay)\b', text_lower):
                owned_libs = pending.get('owned_libraries', [])
                libs_note = ', '.join(owned_libs) if owned_libs else 'your library'
                return {
                    'type': 'download_request',
                    'status': 'pending',
                    'message': (
                        f"You currently have it in {libs_note}. "
                        "What quality would you like? You can say TFlix, 4K, 1080p Remux, or Full Rip."
                    ),
                    'data': {
                        'pending_watchlist': {
                            'title': pending.get('title', ''),
                            'tmdb_id': pending.get('tmdb_id'),
                            'year': pending.get('year'),
                            'media_type': pending.get('media_type'),
                            'awaiting_quality': True,
                        },
                    },
                }
            elif re.match(r'^(?:no|nah|nope|never\s*mind|cancel|forget\s*it|don\'t|nevermind)\b', text_lower):
                return {
                    'type': 'ownership',
                    'status': 'ok',
                    'message': "",
                    'data': {},
                }

        # --- Yes: add the current candidate ---
        if re.match(r'^(?:yes|yea|yeah|yep|yup|sure|do it|add it|please|go ahead|absolutely|ok|okay|that\'s? (?:it|the one))\b', text_lower):
            _title = pending.get('title', '')
            _tmdb_id = pending.get('tmdb_id')
            _year = pending.get('year')
            _profile = pending.get('quality_profile', 'TFlix')
            _media_type = pending.get('media_type')
            _season = pending.get('season')
            _episode = pending.get('episode')

            # confirm-first flow: check ownership AFTER the user confirms the title
            if pending.get('check_and_add'):
                _quality = pending.get('quality')

                if _media_type == 'tv' and _season is not None:
                    # Season-aware ownership check via Sonarr/ownership API
                    from handlers.tv import _sonarr_lookup, _fetch_episode_ownership
                    _series_record = _sonarr_lookup(_title)
                    if _series_record and _series_record.get('tvdbId'):
                        _tvdb_id_check = int(_series_record['tvdbId'])
                        _ownership = _fetch_episode_ownership(_tvdb_id_check)
                        _owned_keys = set((_ownership.get('episodes') or {}).keys())

                        if _episode is not None:
                            # Specific episode check
                            _ep_key = f's{_season}e{_episode}'
                            if _ep_key in _owned_keys:
                                return {
                                    'type': 'ownership', 'status': 'ok',
                                    'message': (
                                        f"Episode {_episode} of {_title} season {_season} "
                                        "is already in your library."
                                    ),
                                    'data': {'found': True, 'title': _title},
                                }
                            # Not owned — fall through to add
                        else:
                            # Whole season check
                            _sonarr_seasons = _series_record.get('seasons') or []
                            _expected = 0
                            for _s in _sonarr_seasons:
                                if _s.get('seasonNumber') == _season:
                                    _expected = int(
                                        (_s.get('statistics') or {}).get('totalEpisodeCount') or 0
                                    )
                                    break
                            _season_eps = [k for k in _owned_keys if k.startswith(f's{_season}e')]
                            if _expected and len(_season_eps) >= _expected:
                                return {
                                    'type': 'ownership', 'status': 'ok',
                                    'message': (
                                        f"All {_expected} episodes of {_title} season {_season} "
                                        "are already in your library."
                                    ),
                                    'data': {'found': True, 'title': _title},
                                }
                            # Partial or missing — fall through to add/fill gaps

                elif _media_type == 'tv' and _season is None:
                    # TV confirmed but no season specified — ask which season
                    return {
                        'type': 'download_request',
                        'status': 'pending',
                        'message': (
                            f"Which season of {_title} would you like? "
                            "Say a number like 'season 3', or 'all' to add the whole show."
                        ),
                        'data': {
                            'pending_watchlist': {
                                **{k: v for k, v in pending.items()},
                                'awaiting_season_selection': True,
                                'check_and_add': True,
                            },
                        },
                    }

                else:
                    # Movie — title-level Plex check
                    from handlers.download_action import _check_plex_ownership
                    _owned = _check_plex_ownership(_title)
                    if _owned:
                        _libraries = _owned.get('all_libraries',
                                                 [_owned.get('library_name') or 'your library'])
                        if _quality and _media_type != 'tv':
                            from handlers.ownership import _match_quality_to_library
                            _qlabel = _quality.upper() if len(_quality) <= 4 else _quality.title()
                            _quality_libs = [lib for lib in _libraries
                                             if _match_quality_to_library(_quality, lib)]
                            if _quality_libs:
                                return {
                                    'type': 'ownership', 'status': 'ok',
                                    'message': (
                                        f"{_owned['title']} is already in your library in {_qlabel}."
                                    ),
                                    'data': {
                                        'found': True,
                                        'title': _owned['title'],
                                        'all_libraries': _libraries,
                                    },
                                }
                            # Have it but not in requested quality — fall through to add in that quality
                        else:
                            if len(_libraries) == 1:
                                _libs_note = f"your {_libraries[0]} library"
                            elif len(_libraries) == 2:
                                _libs_note = f"{_libraries[0]} and {_libraries[1]}"
                            else:
                                _libs_note = ', '.join(_libraries[:-1]) + f', and {_libraries[-1]}'
                            return {
                                'type': 'ownership', 'status': 'ok',
                                'message': f"{_owned['title']} is already available in {_libs_note}.",
                                'data': {
                                    'found': True,
                                    'title': _owned['title'],
                                    'all_libraries': _libraries,
                                },
                            }

            logger.info('Watchlist confirmation: adding %s (type=%s) with profile %s season=%s ep=%s',
                        _title, _media_type or 'movie', _profile, _season, _episode)
            return handle_watchlist_add(
                title=_title,
                tmdb_id=_tmdb_id,
                year=_year,
                quality_profile=_profile,
                media_type=_media_type,
                requester_chat_id=_requester_chat_id,
                season=_season,
                episode=_episode,
            )

        # --- Hard cancel ---
        if re.match(r'^(?:never\s*mind|cancel|forget\s*it|stop|don\'t|nevermind)\b', text_lower):
            return {
                'type': 'ownership',
                'status': 'ok',
                'message': "",
                'data': {},
            }

        # --- No: either decline outright, or cycle to next candidate ---
        if re.match(r'^(?:no|nah|nope|not that|wrong|that\'s not|next)\b', text_lower):
            # Cycling only applies when Alfred offered a specific candidate
            # from a multi-result search (original_query is set in that flow).
            # For a plain "add to watchlist?" prompt (ownership flow), "no"
            # means the user is declining — just say okay and close.
            if pending.get('original_query'):
                logger.info('Watchlist rejection: cycling to next candidate for %s',
                            pending.get('original_query'))
                return handle_watchlist_next(pending)
            logger.info('Watchlist declined for %s', pending.get('title'))
            return {
                'type': 'ownership',
                'status': 'ok',
                'message': "",
                'data': {},
            }

        # --- Unrecognized input while in pending state — re-prompt instead of falling through ---
        _pending_title = pending.get('title', 'that')
        logger.info('Pending state: unrecognized reply %r for %s — re-prompting', text[:50], _pending_title)
        return {
            'type': 'download_request',
            'status': 'pending',
            'message': f"Sorry, did you want to add {_pending_title}? Say yes or no.",
            'data': {'pending_watchlist': dict(pending)},
        }

    # -----------------------------------------------------------------
    # Tier 1: Fast-path — smart home and time/date (no LLM latency)
    # -----------------------------------------------------------------

    # Check for compound smart home commands ("X and Y and Z")
    _SPLIT_RE = re.compile(r'\s+(?:and|then|also)\s+', re.I)
    parts = _SPLIT_RE.split(text)
    if len(parts) > 1:
        matches = [(part, _fast_match(part)) for part in parts]
        all_smart_home = all(
            m is not None and m[0] in (
                'lights_on', 'lights_off', 'lights_brightness',
                'volume_set', 'volume_adjust',
                'fan_on', 'fan_off', 'fan_toggle',
            )
            for _, m in matches
        )
        if all_smart_home:
            results = []
            messages = []
            for part, matched in matches:
                handler_key, args = matched
                logger.info('Fast-path compound: handler=%s args=%s', handler_key, args)
                result = _execute_handler(handler_key, args, user=user)
                results.append(result)
                messages.append(result.get('message', ''))
            return {
                'type': 'smart_home',
                'status': 'ok',
                'message': ' '.join(messages),
                'data': {'compound': True, 'results': results},
            }

    # Single fast-path match
    fast = _fast_match(text)
    if fast:
        handler_key, args = fast
        logger.info('Fast-path: handler=%s args=%s', handler_key, args)
        return _execute_handler(handler_key, args, user=user)

    # -----------------------------------------------------------------
    # Tier 2: Write-intent & kitchen keyword detection → classifier path
    # -----------------------------------------------------------------
    # Tier 2a: Ownership queries — bypass LLM, extract title directly.
    # "do I have X" / "have I got X" phrases are structurally unambiguous
    # but LLMs misread temporal titles like "The Day After Tomorrow" as
    # calendar queries. We own the routing here.
    # -----------------------------------------------------------------
    ownership_match = _OWNERSHIP_TITLE_RE.search(routing_text)
    if ownership_match:
        raw_title = ownership_match.group(1).strip().rstrip('?.!')
        logger.info('Ownership fast-path: title=%r', raw_title)
        return _execute_handler('ownership_check', {'title': raw_title}, user=user)

    # -----------------------------------------------------------------
    # Tier 2b: Write-verb and kitchen intents — route through classifier.
    # The classifier handles side-effect intents that need confirmation flows
    # (download_movie/tv, play) and kitchen intents with their own state machines.
    if _WRITE_VERB_RE.search(routing_text) or _KITCHEN_RE.search(routing_text):
        classifier_result = _run_classifier_path(routing_text, command, context, history, user)
        if classifier_result is not None:
            return classifier_result

    # -----------------------------------------------------------------
    # Tier 3: Nova agent (tool-use loop)
    # -----------------------------------------------------------------
    try:
        from agent import run_agent

        agent_result = run_agent(command=text, history=history, context=context)
        logger.info('Agent: tools=%s msg=%s',
                    [t.get('tool') for t in agent_result.tool_trace],
                    agent_result.message[:80])

        resp: Dict[str, Any] = {
            'type': 'agent',
            'status': agent_result.status,
            'message': agent_result.message,
            'data': {'tool_trace': agent_result.tool_trace},
        }
        if agent_result.ui_action:
            resp['suggested_ui_action'] = agent_result.ui_action
        return resp
    except Exception as e:
        logger.warning('Agent failed (%s) — falling back to classifier', e)

    # -----------------------------------------------------------------
    # Tier 4: classifier fallback (only if agent errored)
    # -----------------------------------------------------------------
    classifier_result = _run_classifier_path(text, command, context, history, user)
    if classifier_result is not None:
        return classifier_result

    # -----------------------------------------------------------------
    # Tier 5: Direct Claude fallback (classifier unavailable)
    # -----------------------------------------------------------------
    logger.info('Fallback to Claude: %s', command[:100])
    return handle_fallback(command, context, history=history, user=user)


# Polite preambles users prepend to commands ("Thank you! Can you add...")
# These are stripped before routing so the write-verb check still fires.
_PREAMBLE_RE = re.compile(
    r'^(?:'
    r'thank(?:s|\s+you)[!.,]?\s+'
    r'|hey[!.,]?\s+'
    r'|hi[!.,]?\s+'
    r'|ok(?:ay)?[!.,]?\s+'
    r'|great[!.,]?\s+'
    r'|awesome[!.,]?\s+'
    r'|perfect[!.,]?\s+'
    r'|cool[!.,]?\s+'
    r'|sounds?\s+good[!.,]?\s+'
    r'|sure[!.,]?\s+'
    r'|got\s+it[!.,]?\s+'
    r')+',
    re.I,
)

# Commands that start with (or open with a polite prefix before) write verbs are
# routed through the classifier so confirmation flows keep working.
_WRITE_VERB_RE = re.compile(
    r'^(?:'
    r'(?:can|could|would|please)\s+(?:you\s+)?'      # "can you add", "please download"
    r'|i(?:\'d)?\s+(?:like|want)(?:\s+(?:you\s+)?to)?\s+'  # "I'd like to add", "I want you to grab"
    r')?'
    r'(?:add|download|grab|play|watch|put\s+on|remove|delete|'
    r'take\s+\w+\s+off|put\s+(?:it|the|a|an|\w+)\s+on)\b',
    re.I,
)

# Kitchen-domain keywords — these carry their own pending flows.
_KITCHEN_RE = re.compile(
    r'\b(?:grocery|shopping\s*list|meal\s*plan|pantry|recipe|'
    r'(?:breakfast|lunch|dinner)\s+(?:today|tonight|tomorrow)?|'
    r'(?:what\'?s\s+)?for\s+(?:breakfast|lunch|dinner)|'
    r'we\s+(?:need|ran\s+out|finished))\b',
    re.I,
)

# Ownership query detection — used to fast-path directly to handle_ownership_check,
# bypassing the LLM classifier entirely (prevents temporal-phrase misreads).
_OWNERSHIP_QUERY_RE = re.compile(
    r'\b(?:do\s+(?:i|we)\s+have|have\s+(?:i|we)\s+got|'
    r'(?:i|we)\s+own\b|do\s+(?:i|we)\s+own)\b',
    re.I,
)

# Extracts the title portion after the ownership verb phrase.
_OWNERSHIP_TITLE_RE = re.compile(
    r'^(?:do\s+(?:i|we)\s+have|have\s+(?:i|we)\s+got|'
    r'(?:i|we)\s+own|do\s+(?:i|we)\s+own)\s+(.+)',
    re.I,
)


def _run_classifier_path(text: str, command: str,
                          context: Optional[Dict[str, Any]],
                          history: Optional[list],
                          user: Optional[User]) -> Optional[Dict[str, Any]]:
    """Run the classifier → handler dispatch path. Returns None on failure."""
    try:
        from classifier import classify

        result = classify(command=text, history=history)

        if len(result.intents) == 1:
            intent = result.intents[0]
            logger.info('Classifier: intent=%s params=%s', intent.key, intent.params)

            if intent.key == 'general_question':
                logger.info('Classifier: general_question → Claude fallback')
                return handle_fallback(command, context, history=history, user=user)

            return _execute_handler(intent.key, intent.params, user=user)

        elif len(result.intents) > 1:
            results = []
            messages = []
            for intent in result.intents:
                logger.info('Classifier compound: intent=%s params=%s', intent.key, intent.params)
                r = _execute_handler(intent.key, intent.params, user=user)
                results.append(r)
                messages.append(r.get('message', ''))
            return {
                'type': 'compound',
                'status': 'ok',
                'message': ' '.join(messages),
                'data': {'compound': True, 'results': results},
            }
    except Exception as e:
        logger.error('Classifier failed: %s', e)

    return None
