"""Vibe Engine handlers: mood-based picks + surprise-me roulette."""

import logging
import os
import random
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

FLASK_BASE = os.getenv('FLASK_BASE', 'http://localhost:5001')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')


def _auth_headers() -> Dict[str, str]:
    return {'X-API-Key': SERVICE_API_KEY} if SERVICE_API_KEY else {}


# Spoken mood word → Vibe Engine mood ID (from services/vibeEngine.ts)
_MOOD_ALIASES: Dict[str, str] = {
    # cozy-comfort
    'cozy': 'cozy-comfort',
    'comfort': 'cozy-comfort',
    'cozy comfort': 'cozy-comfort',
    'warm': 'cozy-comfort',
    'comfort food': 'cozy-comfort',
    # dark-moody
    'dark': 'dark-moody',
    'moody': 'dark-moody',
    'dark moody': 'dark-moody',
    'brooding': 'dark-moody',
    'gritty': 'dark-moody',
    # mind-bending
    'mind bending': 'mind-bending',
    'mind-bending': 'mind-bending',
    'mindbending': 'mind-bending',
    'cerebral': 'mind-bending',
    'trippy': 'mind-bending',
    'thought provoking': 'mind-bending',
    # big-adventure
    'adventure': 'big-adventure',
    'big adventure': 'big-adventure',
    'epic': 'big-adventure',
    # laugh-out-loud
    'funny': 'laugh-out-loud',
    'comedy': 'laugh-out-loud',
    'laugh': 'laugh-out-loud',
    'laughs': 'laugh-out-loud',
    'a laugh': 'laugh-out-loud',
    'laugh out loud': 'laugh-out-loud',
    'something funny': 'laugh-out-loud',
    # classic-throwback
    'throwback': 'classic-throwback',
    'classic': 'classic-throwback',
    'retro': 'classic-throwback',
    'old school': 'classic-throwback',
    'nostalgia': 'classic-throwback',
    # edge-of-seat
    'edge of seat': 'edge-of-seat',
    'edge of my seat': 'edge-of-seat',
    'edge-of-seat': 'edge-of-seat',
    'thriller': 'edge-of-seat',
    'tense': 'edge-of-seat',
    'suspense': 'edge-of-seat',
    # easy-background
    'easy': 'easy-background',
    'background': 'easy-background',
    'easy watch': 'easy-background',
    'easy watching': 'easy-background',
    'light': 'easy-background',
    'lightweight': 'easy-background',
    'sleep': 'easy-background',
    'fall asleep': 'easy-background',
    'nothing too heavy': 'easy-background',
}


_MOOD_LABELS: Dict[str, str] = {
    'cozy-comfort': 'cozy',
    'dark-moody': 'dark and moody',
    'mind-bending': 'mind-bending',
    'big-adventure': 'big adventure',
    'laugh-out-loud': 'laugh-out-loud',
    'classic-throwback': 'throwback',
    'edge-of-seat': 'edge-of-seat',
    'easy-background': 'easy watching',
}


def _resolve_mood_id(mood: Optional[str]) -> Optional[str]:
    if not mood:
        return None
    key = mood.lower().strip()
    if key in _MOOD_ALIASES:
        return _MOOD_ALIASES[key]
    # Partial contains check — e.g. "laughter" → "laugh-out-loud"
    for alias, mood_id in _MOOD_ALIASES.items():
        if alias in key or key in alias:
            return mood_id
    return None


def _fetch_vibe_items() -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/library/vibe-engine',
            headers=_auth_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json() or {}
        return body.get('items') or []
    except Exception as e:
        logger.warning('Vibe-engine fetch failed: %s', e)
        return []


# Simple per-mood heuristic that mirrors frontend vibeEngine.ts so the
# handler can score items server-side without needing to pull the full
# scoring module over to Python.
_MOOD_RULES: Dict[str, Dict[str, Any]] = {
    'cozy-comfort': {
        'genres': {'family', 'romance', 'comedy', 'adventure'},
        'avoid': {'horror', 'war', 'thriller'},
        'runtime_max': 130,
        'year_min': 1995,
    },
    'dark-moody': {
        'genres': {'crime', 'thriller', 'mystery', 'drama', 'noir'},
        'avoid': {'family', 'animation', 'romance'},
    },
    'mind-bending': {
        'genres': {'science fiction', 'mystery', 'thriller'},
        'avoid': {'family'},
        'rating_min': 7.0,
    },
    'big-adventure': {
        'genres': {'adventure', 'action', 'fantasy', 'science fiction'},
        'runtime_min': 110,
    },
    'laugh-out-loud': {
        'genres': {'comedy'},
        'avoid': {'drama', 'horror'},
    },
    'classic-throwback': {
        'year_max': 2005,
    },
    'edge-of-seat': {
        'genres': {'thriller', 'horror', 'mystery', 'crime', 'action'},
        'rating_min': 6.5,
    },
    'easy-background': {
        'genres': {'comedy', 'family', 'animation', 'romance'},
        'runtime_max': 115,
    },
}


def _score_for_mood(item: Dict[str, Any], mood_id: str) -> float:
    rule = _MOOD_RULES.get(mood_id, {})
    score = 0.0
    genres = {str(g).lower() for g in (item.get('genres') or [])}
    rating = float(item.get('rating') or 0)
    runtime = int(item.get('runtimeMinutes') or item.get('runtime') or 0)
    year = int(item.get('year') or 0)

    want = rule.get('genres') or set()
    avoid = rule.get('avoid') or set()
    if want:
        overlap = len(genres & want)
        if overlap == 0:
            score -= 4
        else:
            score += overlap * 3
    if avoid and (genres & avoid):
        score -= 5

    if 'runtime_max' in rule and runtime:
        if runtime <= rule['runtime_max']:
            score += 1
        else:
            score -= 1
    if 'runtime_min' in rule and runtime:
        if runtime >= rule['runtime_min']:
            score += 1

    if 'year_max' in rule and year and year <= rule['year_max']:
        score += 2
    if 'year_min' in rule and year and year >= rule['year_min']:
        score += 0.5

    if 'rating_min' in rule and rating >= rule['rating_min']:
        score += 1.5

    # Slight bump for higher-rated, mildly penalize very low-rated
    if rating:
        score += (rating - 6.0) * 0.4
    return score


def _ui_action_for_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tmdb_id = item.get('tmdb_id') or item.get('tmdbId')
    mt = item.get('type') or item.get('mediaType') or 'movie'
    if tmdb_id:
        return {
            'action': 'open_modal',
            'media_type': 'tv' if mt in ('tv', 'show', 'series') else 'movie',
            'tmdb_id': int(tmdb_id),
        }
    return None


def _pick_top_scoring(items: List[Dict[str, Any]], mood_id: str,
                     n: int = 5) -> List[Tuple[float, Dict[str, Any]]]:
    # Prefer unwatched items
    unwatched = [i for i in items if not (i.get('viewCount') or 0)]
    pool = unwatched if unwatched else items
    scored = [(_score_for_mood(i, mood_id), i) for i in pool]
    scored.sort(key=lambda s: s[0], reverse=True)
    return scored[:n]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_vibe_mood(mood: Optional[str] = None) -> Dict[str, Any]:
    """Pick a match for a specific mood."""
    mood_id = _resolve_mood_id(mood)
    if not mood_id:
        return {
            'type': 'vibe_mood',
            'status': 'ok',
            'message': ("Want something cozy, dark, funny, mind-bending, edge-of-seat, "
                        "a throwback, a big adventure, or easy background?"),
            'data': {'mood': mood, 'known_moods': list(_MOOD_LABELS.values())},
            'suggested_ui_action': {'action': 'navigate', 'path': '/library'},
        }

    items = _fetch_vibe_items()
    if not items:
        return {
            'type': 'vibe_mood',
            'status': 'error',
            'message': "I couldn't reach the library right now.",
            'data': {'mood_id': mood_id},
        }

    top = _pick_top_scoring(items, mood_id, n=5)
    if not top or top[0][0] <= -5:
        label = _MOOD_LABELS.get(mood_id, mood_id)
        return {
            'type': 'vibe_mood',
            'status': 'ok',
            'message': f"Nothing jumped out for a {label} pick tonight — opening the Vibe Engine so you can browse.",
            'data': {'mood_id': mood_id},
            'suggested_ui_action': {'action': 'navigate', 'path': f'/library?mood={mood_id}'},
        }

    best = top[0][1]
    label = _MOOD_LABELS.get(mood_id, mood_id)
    title = best.get('title', 'a pick')
    year = best.get('year')
    runtime = best.get('runtimeMinutes') or best.get('runtime')
    runtime_hint = f", about {int(runtime)} minutes" if runtime else ''
    year_hint = f" ({year})" if year else ''
    msg = f"Trying {title}{year_hint} — {label}{runtime_hint}."
    ui_action = _ui_action_for_item(best) or {'action': 'navigate',
                                              'path': f'/library?mood={mood_id}'}

    return {
        'type': 'vibe_mood',
        'status': 'ok',
        'message': msg,
        'data': {
            'mood_id': mood_id,
            'mood_label': label,
            'pick': {
                'title': best.get('title'),
                'year': best.get('year'),
                'tmdb_id': best.get('tmdb_id') or best.get('tmdbId'),
                'rating': best.get('rating'),
                'runtime': runtime,
                'type': best.get('type') or 'movie',
            },
            'runners_up': [{
                'title': item.get('title'),
                'year': item.get('year'),
                'tmdb_id': item.get('tmdb_id') or item.get('tmdbId'),
                'rating': item.get('rating'),
            } for _, item in top[1:5]],
        },
        'suggested_ui_action': ui_action,
    }


def handle_vibe_pick_for_me() -> Dict[str, Any]:
    """Surprise roulette — pick a highly-rated unwatched item at random."""
    items = _fetch_vibe_items()
    if not items:
        return {
            'type': 'vibe_pick_for_me',
            'status': 'error',
            'message': "I couldn't reach the library right now.",
            'data': {},
        }

    # Prefer unwatched, rating >= 7
    pool = [i for i in items if not (i.get('viewCount') or 0) and float(i.get('rating') or 0) >= 7.0]
    if not pool:
        pool = [i for i in items if float(i.get('rating') or 0) >= 6.5]
    if not pool:
        pool = items
    pool = pool[:400]  # cap the random sample space

    if not pool:
        return {
            'type': 'vibe_pick_for_me',
            'status': 'ok',
            'message': "I couldn't find a good pick right now.",
            'data': {},
            'suggested_ui_action': {'action': 'navigate', 'path': '/library'},
        }

    best = random.choice(pool)
    title = best.get('title', 'something')
    year = best.get('year')
    runtime = best.get('runtimeMinutes') or best.get('runtime')
    rating = best.get('rating')
    year_hint = f" ({year})" if year else ''
    runtime_hint = f", {int(runtime)} minutes" if runtime else ''
    rating_hint = f", rated {rating:.1f}" if isinstance(rating, (int, float)) and rating else ''
    msg = f"Trying {title}{year_hint}{runtime_hint}{rating_hint}. Want it?"

    ui_action = _ui_action_for_item(best) or {'action': 'navigate', 'path': '/library'}

    return {
        'type': 'vibe_pick_for_me',
        'status': 'ok',
        'message': msg,
        'data': {
            'pick': {
                'title': best.get('title'),
                'year': best.get('year'),
                'tmdb_id': best.get('tmdb_id') or best.get('tmdbId'),
                'rating': rating,
                'runtime': runtime,
                'type': best.get('type') or 'movie',
            },
        },
        'suggested_ui_action': ui_action,
    }
