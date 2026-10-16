"""Bosley Kitchen API client.

Thin wrapper around the bosley-kitchen Flask API. Authenticates with the
shared SERVICE_API_KEY (bypasses the session-cookie login). All methods
return parsed JSON or raise on error — handlers are responsible for
user-facing error messages.

The kitchen runs on host port 5062 (container :5060). Alfred is on
host networking so localhost:5062 is direct.
"""

import logging
import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

KITCHEN_BASE = os.getenv('KITCHEN_BASE', 'http://localhost:5062')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')
_TIMEOUT = 10


def _headers() -> Dict[str, str]:
    return {
        'X-API-Key': SERVICE_API_KEY,
        'Content-Type': 'application/json',
    }


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    r = requests.get(f'{KITCHEN_BASE}{path}', headers=_headers(),
                     params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: Optional[Dict[str, Any]] = None) -> Any:
    r = requests.post(f'{KITCHEN_BASE}{path}', headers=_headers(),
                      json=body or {}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _patch(path: str, body: Dict[str, Any]) -> Any:
    r = requests.patch(f'{KITCHEN_BASE}{path}', headers=_headers(),
                       json=body, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _delete(path: str) -> Any:
    r = requests.delete(f'{KITCHEN_BASE}{path}', headers=_headers(), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json() if r.content else {}


# ---------------------------------------------------------------------------
# Grocery
# ---------------------------------------------------------------------------

def grocery_list(include_checked: bool = False) -> List[Dict[str, Any]]:
    params = {'checked': 'all'} if include_checked else {'checked': '0'}
    data = _get('/api/grocery', params=params)
    return data.get('items', [])


def grocery_add(name: str, quantity: float = 1, unit: str = '') -> Dict[str, Any]:
    return _post('/api/grocery', {'name': name, 'quantity': quantity, 'unit': unit})


def grocery_find(name: str, unchecked_only: bool = True) -> Optional[Dict[str, Any]]:
    """Fuzzy find a grocery item by name. Returns the best match or None."""
    items = grocery_list(include_checked=not unchecked_only)
    q = name.lower().strip()

    # Exact match first
    for item in items:
        if item.get('name', '').lower() == q:
            return item
    # Substring match (either direction)
    for item in items:
        n = item.get('name', '').lower()
        if q in n or n in q:
            return item
    return None


def grocery_check(item_id: str) -> Dict[str, Any]:
    return _post(f'/api/grocery/{item_id}/check')


def grocery_delete(item_id: str) -> Dict[str, Any]:
    return _delete(f'/api/grocery/{item_id}')


def grocery_clear() -> Dict[str, Any]:
    return _post('/api/grocery/clear')


# ---------------------------------------------------------------------------
# Meals
# ---------------------------------------------------------------------------

def meals_range(start: str, end: str, draft: bool = False) -> List[Dict[str, Any]]:
    data = _get('/api/meals', params={
        'start': start,
        'end': end,
        'draft': '1' if draft else '0',
    })
    return data.get('meals', [])


def meals_today(meal_type: Optional[str] = None) -> List[Dict[str, Any]]:
    return meals_on(date.today(), meal_type=meal_type)


def meals_on(target: date, meal_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return meals on a specific date, optionally filtered by meal_type.

    Checks both live and draft plans so "what's for dinner next Wednesday"
    works for draft weeks that haven't been promoted yet.
    """
    iso = target.isoformat()
    live = meals_range(iso, iso, draft=False)
    draft = meals_range(iso, iso, draft=True)
    meals = live + draft
    if meal_type:
        meals = [m for m in meals if m.get('meal_type') == meal_type]
    return meals


def meals_this_week() -> List[Dict[str, Any]]:
    today = date.today()
    start = today.isoformat()
    end = (today + timedelta(days=6)).isoformat()
    return meals_range(start, end)


def meals_next_week_draft() -> List[Dict[str, Any]]:
    data = _get('/api/meals/next-week')
    return data.get('meals', [])


def meal_mark_cooked(meal_id: str) -> Dict[str, Any]:
    return _post(f'/api/meals/{meal_id}/cooked')


# ---------------------------------------------------------------------------
# Pantry
# ---------------------------------------------------------------------------

def pantry_list(status: Optional[str] = None) -> List[Dict[str, Any]]:
    params = {'status': status} if status else None
    data = _get('/api/pantry', params=params)
    return data.get('items', [])


def pantry_find(name: str) -> Optional[Dict[str, Any]]:
    items = pantry_list()
    q = name.lower().strip()
    for item in items:
        if item.get('name', '').lower() == q:
            return item
    for item in items:
        n = item.get('name', '').lower()
        if q in n or n in q:
            return item
    return None


def pantry_mark_out(item_id: str) -> Dict[str, Any]:
    return _patch(f'/api/pantry/{item_id}', {'status': 'out', 'quantity': 0})


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------

def recipes_search(query: str) -> List[Dict[str, Any]]:
    data = _get('/api/recipes', params={'q': query})
    return data.get('recipes', [])


def recipe_to_grocery(recipe_id: str) -> Dict[str, Any]:
    return _post(f'/api/recipes/{recipe_id}/to-grocery')
