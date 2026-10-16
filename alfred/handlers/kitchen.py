"""Bosley Kitchen handlers.

Voice/text skills for the Bosley Kitchen app — grocery list, meal plan,
pantry, and recipes. All handlers return the AlfredResponse contract
({type, status, message, data}).

Handlers accept a structured argument dict from the classifier. They do
not take `user` directly; authorization lives at the router level (see
`router._execute_handler`).
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from kitchen_api import (
    grocery_add,
    grocery_check,
    grocery_clear,
    grocery_delete,
    grocery_find,
    grocery_list,
    meal_mark_cooked,
    meals_next_week_draft,
    meals_on,
    meals_this_week,
    meals_today,
    pantry_find,
    pantry_mark_out,
    recipe_to_grocery,
    recipes_search,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MEAL_TYPE_ALIASES = {
    'breakfast': 'breakfast',
    'brunch': 'breakfast',
    'morning': 'breakfast',
    'lunch': 'lunch',
    'midday': 'lunch',
    'dinner': 'dinner',
    'supper': 'dinner',
    'tonight': 'dinner',
    'evening': 'dinner',
}


def _normalize_meal_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.lower().strip()
    return _MEAL_TYPE_ALIASES.get(v, v if v in ('breakfast', 'lunch', 'dinner') else None)


def _meal_display_name(meal: Dict[str, Any]) -> str:
    return (
        meal.get('recipe_name')
        or meal.get('custom_name')
        or (f"{meal.get('location', '').strip()}".strip() or 'Unknown')
    )


def _day_label(iso_date: str) -> str:
    try:
        d = datetime.strptime(iso_date, '%Y-%m-%d').date()
    except Exception:
        return iso_date
    today = date.today()
    if d == today:
        return 'today'
    if d == today + timedelta(days=1):
        return 'tomorrow'
    return d.strftime('%A')


# Map loose day references ("tomorrow", "wednesday", "next friday") to an
# absolute date relative to today. Returns (target_date, spoken_label) or
# (None, None) if we can't parse it.
_WEEKDAY_INDEX = {
    'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
    'friday': 4, 'saturday': 5, 'sunday': 6,
    'mon': 0, 'tue': 1, 'tues': 1, 'wed': 2, 'weds': 2, 'thu': 3,
    'thur': 3, 'thurs': 3, 'fri': 4, 'sat': 5, 'sun': 6,
}


def _resolve_day(day: Optional[str], day_offset: Optional[int]) -> tuple:
    today = date.today()

    # Numeric offset wins if provided
    if day_offset is not None:
        try:
            target = today + timedelta(days=int(day_offset))
            return target, _day_label(target.isoformat())
        except (TypeError, ValueError):
            pass

    if not day:
        return today, 'today'

    d = day.lower().strip()

    if d in ('today', 'tonight', 'this evening'):
        return today, 'today'
    if d in ('tomorrow', 'tomorrow night', 'tomorrow evening'):
        target = today + timedelta(days=1)
        return target, 'tomorrow'

    # "next <weekday>" or bare "<weekday>" — pick the next occurrence
    next_prefix = d.startswith('next ')
    key = d[5:].strip() if next_prefix else d
    if key in _WEEKDAY_INDEX:
        target_idx = _WEEKDAY_INDEX[key]
        days_ahead = (target_idx - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7 if next_prefix else 0
        target = today + timedelta(days=days_ahead)
        label = 'today' if days_ahead == 0 else (
            'tomorrow' if days_ahead == 1 else target.strftime('%A')
        )
        return target, label

    return today, 'today'


def _ok(type_: str, message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {'type': type_, 'status': 'ok', 'message': message, 'data': data or {}}


def _err(type_: str, message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {'type': type_, 'status': 'error', 'message': message, 'data': data or {}}


def _kitchen_error(e: Exception, action: str) -> Dict[str, Any]:
    logger.exception('Kitchen API error during %s', action)
    return _err('kitchen_error',
                f"I couldn't reach the kitchen app to {action}. Try again in a moment.")


# ---------------------------------------------------------------------------
# Grocery
# ---------------------------------------------------------------------------

def handle_kitchen_grocery_add(items: Optional[List[str]] = None,
                               item: Optional[str] = None,
                               quantity: Optional[float] = None,
                               unit: Optional[str] = None) -> Dict[str, Any]:
    """Add one or more items to the grocery list.

    Classifier may pass either `items: [..]` (preferred for "add milk
    and eggs") or a single `item` string. Quantity/unit apply to the
    single-item form only.
    """
    names: List[str] = []
    if items:
        names = [n.strip() for n in items if n and n.strip()]
    elif item:
        names = [item.strip()]

    if not names:
        return _err('kitchen_grocery', "What would you like to add?")

    added: List[str] = []
    exists: List[str] = []
    failed: List[str] = []

    for name in names:
        try:
            result = grocery_add(
                name,
                quantity=quantity if quantity is not None else 1,
                unit=unit or '',
            )
            if result.get('status') == 'exists':
                exists.append(name)
            else:
                added.append(name)
        except Exception:
            logger.exception('grocery_add failed for %s', name)
            failed.append(name)

    parts: List[str] = []
    if added:
        parts.append(f"Added {_join(added)} to the grocery list.")
    if exists:
        parts.append(f"{_join(exists)} {'is' if len(exists) == 1 else 'are'} already on it.")
    if failed:
        parts.append(f"I couldn't add {_join(failed)}.")

    msg = ' '.join(parts) if parts else "Nothing to add."
    return _ok('kitchen_grocery_add', msg, {
        'added': added, 'already_on_list': exists, 'failed': failed,
    })


def handle_kitchen_grocery_list() -> Dict[str, Any]:
    try:
        items = grocery_list(include_checked=False)
    except Exception as e:
        return _kitchen_error(e, 'check the grocery list')

    if not items:
        return _ok('kitchen_grocery_list', "The grocery list is empty.", {'count': 0})

    count = len(items)
    # Speak a compact summary — first few items, then "and N more"
    names = [i.get('name', '') for i in items if i.get('name')]
    preview = names[:5]
    more = count - len(preview)
    if more > 0:
        msg = f"You have {count} items on the list: {_join(preview)}, and {more} more."
    else:
        msg = f"You have {count} items on the list: {_join(preview)}."
    return _ok('kitchen_grocery_list', msg, {'count': count, 'items': names})


def handle_kitchen_grocery_remove(item: str) -> Dict[str, Any]:
    if not item:
        return _err('kitchen_grocery', "What should I remove?")
    try:
        match = grocery_find(item, unchecked_only=True)
        if not match:
            return _ok('kitchen_grocery_remove',
                       f"{item.title()} isn't on the list.", {'found': False})
        grocery_delete(match['id'])
        return _ok('kitchen_grocery_remove',
                   f"Removed {match.get('name', item)} from the list.",
                   {'removed': match.get('name')})
    except Exception as e:
        return _kitchen_error(e, 'remove that item')


def handle_kitchen_grocery_check(item: str) -> Dict[str, Any]:
    if not item:
        return _err('kitchen_grocery', "Which item should I mark as bought?")
    try:
        match = grocery_find(item, unchecked_only=True)
        if not match:
            return _ok('kitchen_grocery_check',
                       f"{item.title()} isn't on the list.", {'found': False})
        grocery_check(match['id'])
        return _ok('kitchen_grocery_check',
                   f"Marked {match.get('name', item)} as bought.",
                   {'checked': match.get('name')})
    except Exception as e:
        return _kitchen_error(e, 'update the grocery list')


def handle_kitchen_grocery_clear() -> Dict[str, Any]:
    try:
        grocery_clear()
        return _ok('kitchen_grocery_clear', "Cleared the grocery list.")
    except Exception as e:
        return _kitchen_error(e, 'clear the grocery list')


# ---------------------------------------------------------------------------
# Meals
# ---------------------------------------------------------------------------

def handle_kitchen_meal_today(meal_type: Optional[str] = None,
                              day: Optional[str] = None,
                              day_offset: Optional[int] = None) -> Dict[str, Any]:
    """Look up a meal for a specific day.

    Despite the name, this handles any day (today, tomorrow, a weekday)
    via the `day` string or `day_offset` int from the classifier. Defaults
    to today / dinner when nothing is specified.
    """
    mt = _normalize_meal_type(meal_type) or 'dinner'
    target_date, label = _resolve_day(day, day_offset)

    try:
        meals = meals_on(target_date, meal_type=mt)
    except Exception as e:
        return _kitchen_error(e, f"check the meal plan for {label}")

    # Phrasing helpers
    if mt == 'dinner' and label == 'today':
        day_phrase = 'tonight'
    elif mt == 'dinner' and label == 'tomorrow':
        day_phrase = 'tomorrow night'
    elif label == 'today':
        day_phrase = 'today'
    else:
        day_phrase = label

    if not meals:
        return _ok('kitchen_meal_today',
                   f"There's nothing on the plan for {mt} {day_phrase}.",
                   {'meal_type': mt, 'date': target_date.isoformat(), 'meals': []})

    names = [_meal_display_name(m) for m in meals]
    if mt == 'dinner':
        if day_phrase == 'tonight':
            msg = f"Dinner tonight is {_join(names)}."
        elif day_phrase == 'tomorrow night':
            msg = f"Dinner tomorrow night is {_join(names)}."
        else:
            msg = f"Dinner on {day_phrase} is {_join(names)}."
    else:
        msg = f"{mt.title()} {day_phrase} is {_join(names)}."
    return _ok('kitchen_meal_today', msg, {
        'meal_type': mt,
        'date': target_date.isoformat(),
        'meals': names,
    })


def handle_kitchen_meal_week() -> Dict[str, Any]:
    try:
        meals = meals_this_week()
    except Exception as e:
        return _kitchen_error(e, "check this week's meal plan")

    if not meals:
        return _ok('kitchen_meal_week', "There's nothing planned for this week yet.",
                   {'count': 0})

    # Group dinners by day (most common ask)
    dinners = [m for m in meals if m.get('meal_type') == 'dinner']
    if not dinners:
        return _ok('kitchen_meal_week',
                   f"You have {len(meals)} meals planned this week, but no dinners yet.",
                   {'count': len(meals)})

    lines = []
    for m in dinners:
        day = _day_label(m.get('date', ''))
        name = _meal_display_name(m)
        lines.append(f"{day} {name}")

    msg = "This week for dinner: " + '; '.join(lines) + '.'
    return _ok('kitchen_meal_week', msg, {
        'dinner_count': len(dinners),
        'dinners': [{'date': m.get('date'), 'name': _meal_display_name(m)} for m in dinners],
    })


def handle_kitchen_meal_plan_next_week() -> Dict[str, Any]:
    try:
        drafts = meals_next_week_draft()
    except Exception as e:
        return _kitchen_error(e, "check next week's meal draft")

    if not drafts:
        return _ok('kitchen_meal_plan_next_week',
                   "There's no draft plan for next week yet. You can build one in the kitchen app.",
                   {'count': 0})

    dinners = [m for m in drafts if m.get('meal_type') == 'dinner']
    lines = []
    for m in dinners:
        day = _day_label(m.get('date', ''))
        name = _meal_display_name(m)
        lines.append(f"{day} {name}")
    if lines:
        msg = "Next week's draft dinners: " + '; '.join(lines) + '.'
    else:
        msg = f"Next week has {len(drafts)} meals drafted, but no dinners yet."
    return _ok('kitchen_meal_plan_next_week', msg, {
        'dinner_count': len(dinners),
        'total': len(drafts),
    })


def handle_kitchen_meal_cooked(meal_type: Optional[str] = None) -> Dict[str, Any]:
    """Mark today's meal as cooked (deducts ingredients from pantry)."""
    mt = _normalize_meal_type(meal_type) or 'dinner'
    try:
        meals = meals_today(meal_type=mt)
    except Exception as e:
        return _kitchen_error(e, "find today's meal")

    if not meals:
        return _ok('kitchen_meal_cooked',
                   f"There's no {mt} on the plan for today.",
                   {'found': False})

    # Find the first meal that has a recipe (cooked endpoint requires recipe_id)
    target = next((m for m in meals if m.get('recipe_id')), None)
    if not target:
        return _ok('kitchen_meal_cooked',
                   f"Today's {mt} doesn't have a recipe attached, so I can't deduct ingredients.",
                   {'found': False})

    try:
        result = meal_mark_cooked(target['id'])
        deducted = result.get('deducted', [])
        name = _meal_display_name(target)
        if deducted:
            items_used = ', '.join(d.get('name', '') for d in deducted[:3])
            extra = '' if len(deducted) <= 3 else f" and {len(deducted) - 3} more"
            msg = f"Marked {name} as cooked. Deducted {items_used}{extra} from the pantry."
        else:
            msg = f"Marked {name} as cooked."
        return _ok('kitchen_meal_cooked', msg, {'meal': name, 'deducted': deducted})
    except Exception as e:
        return _kitchen_error(e, 'mark the meal as cooked')


# ---------------------------------------------------------------------------
# Pantry
# ---------------------------------------------------------------------------

def handle_kitchen_pantry_out(item: str) -> Dict[str, Any]:
    """Mark a pantry item as out AND add it to the grocery list."""
    if not item:
        return _err('kitchen_pantry', "What are you out of?")
    try:
        match = pantry_find(item)
        if match:
            pantry_mark_out(match['id'])
            pantry_name = match.get('name', item)
        else:
            pantry_name = item

        # Also add to grocery list so it gets replenished
        try:
            grocery_add(pantry_name)
        except Exception:
            logger.warning('Failed to add %s to grocery after marking out', pantry_name)

        return _ok('kitchen_pantry_out',
                   f"Got it — marked {pantry_name} as out and added it to the grocery list.",
                   {'item': pantry_name, 'in_pantry': match is not None})
    except Exception as e:
        return _kitchen_error(e, 'update the pantry')


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------

def handle_kitchen_recipe_lookup(recipe: str) -> Dict[str, Any]:
    """Describe a recipe by name — returns a short spoken summary."""
    if not recipe:
        return _err('kitchen_recipe', "Which recipe?")
    try:
        matches = recipes_search(recipe)
    except Exception as e:
        return _kitchen_error(e, 'look up the recipe')

    if not matches:
        return _ok('kitchen_recipe_lookup',
                   f"I couldn't find a recipe called {recipe}.",
                   {'found': False})

    target = matches[0]
    name = target.get('name') or recipe
    prep = target.get('prep_time') or 0
    cook = target.get('cook_time') or 0
    ingredients = target.get('ingredients') or []
    description = (target.get('description') or '').strip()

    # Build a compact spoken summary
    parts = [f"{name}."]
    if description:
        parts.append(description if description.endswith('.') else description + '.')
    timing = []
    if prep:
        timing.append(f"{int(prep)} minutes prep")
    if cook:
        timing.append(f"{int(cook)} minutes cook")
    if timing:
        parts.append(" and ".join(timing) + '.')
    if ingredients:
        top_ing = [i.get('name', '') for i in ingredients[:6] if i.get('name')]
        if top_ing:
            extra = '' if len(ingredients) <= 6 else f" and {len(ingredients) - 6} more"
            parts.append(f"Ingredients: {_join(top_ing)}{extra}.")

    return _ok('kitchen_recipe_lookup', ' '.join(parts), {
        'recipe': name,
        'recipe_id': target.get('id'),
        'prep_time': prep,
        'cook_time': cook,
        'ingredient_count': len(ingredients),
    })


def handle_kitchen_recipe_to_grocery(recipe: str) -> Dict[str, Any]:
    """Find a recipe by name and add its missing ingredients to the grocery list."""
    if not recipe:
        return _err('kitchen_recipe', "Which recipe?")
    try:
        matches = recipes_search(recipe)
        if not matches:
            return _ok('kitchen_recipe_to_grocery',
                       f"I couldn't find a recipe called {recipe}.",
                       {'found': False})
        # Take the best (first) match
        target = matches[0]
        result = recipe_to_grocery(target['id'])
        added = result.get('added', [])
        skipped = result.get('skipped', [])
        name = target.get('name', recipe)
        if added:
            preview = ', '.join(added[:4])
            extra = '' if len(added) <= 4 else f" and {len(added) - 4} more"
            msg = f"Added {len(added)} ingredients for {name} to the grocery list: {preview}{extra}."
        else:
            msg = f"Everything for {name} is already stocked or on the list."
        return _ok('kitchen_recipe_to_grocery', msg, {
            'recipe': name,
            'added': added,
            'skipped_count': len(skipped),
        })
    except Exception as e:
        return _kitchen_error(e, 'add recipe ingredients')


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _join(items: List[str]) -> str:
    """Oxford-comma join for natural-sounding TTS."""
    items = [i for i in items if i]
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ', '.join(items[:-1]) + f", and {items[-1]}"
