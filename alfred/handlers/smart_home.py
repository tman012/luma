"""Smart home handler — dynamic Home Assistant area-based entity resolution."""

import logging
import time
import requests
from typing import Any, Dict, List, Optional, Tuple

from config import HA_URL, HA_TOKEN

logger = logging.getLogger(__name__)

_HEADERS = {
    'Authorization': f'Bearer {HA_TOKEN}',
    'Content-Type': 'application/json',
}
_TIMEOUT = 5  # seconds — keep it snappy

# -------------------------------------------------------------------------
# Dynamic area → entity cache (auto-discovers from Home Assistant)
# -------------------------------------------------------------------------
_area_cache: Dict[str, Any] = {}       # area_name_lower → {lights: [...], fans: [...]}
_area_cache_ts: float = 0
_AREA_CACHE_TTL = 300                  # 5 minutes — new entities appear quickly

# Room name aliases — maps spoken names to HA area names
ROOM_ALIASES: Dict[str, str] = {
    'living room':      'family room',
    'mikeys room':      'nursery',
    "mikey's room":     'nursery',
    'spare room':       'spare bedroom',
    'laundry':          'laundry room',
    'hallway':          'upstairs hallway',
}

DEFAULT_ROOM = 'family room'

# Areas to de-prioritize when doing a global device search. If the user says
# "turn on ceiling fan" with no room, we don't want to flip on the fan in a
# sleeping child's room by accident — we only fall back to these areas when
# they're the ONLY match.
_QUIET_AREAS = {
    'nursery', 'master bedroom', 'bedroom', 'spare bedroom',
    "mikey's room", 'mikeys room', "mikey\u2019s room",
    "emma's room", 'emmas room', "emma\u2019s room",
}

# Sentinel key for entities that live outside any HA area.
_UNASSIGNED_KEY = '__unassigned__'


def _fetch_areas() -> Dict[str, Dict[str, List[Dict[str, str]]]]:
    """Fetch all HA areas and their light/fan entities via the template API.

    Returns a nested structure:
        {
          area_name_lower: {
            'lights': [{'entity_id': '...', 'name': 'Ceiling Fan Light'}, ...],
            'fans':   [{'entity_id': '...', 'name': 'Ceiling Fan'}, ...],
          }
        }

    Entities with no area assignment are bucketed under `_UNASSIGNED_KEY`
    so the global device-search path can still find them.

    Each entity carries its Home Assistant friendly name so handlers can
    filter to a specific fixture when the user asks for one ("the ceiling
    fan light in the family room").
    """
    # One template call returns area + entity_id + friendly_name per row.
    # Format:  <area>||<entity_id>||<friendly_name>||<kind>
    # Iterating `states` directly (rather than `areas()`) guarantees we
    # also see entities that have no area assignment.
    template = """
{%- set ns = namespace(rows=[]) -%}
{%- for s in states -%}
  {%- set eid = s.entity_id -%}
  {%- if eid.startswith('light.') or eid.startswith('fan.') -%}
    {%- set kind = 'light' if eid.startswith('light.') else 'fan' -%}
    {%- set fname = s.attributes.friendly_name or eid -%}
    {%- set area = area_name(eid) or '__unassigned__' -%}
    {%- set ns.rows = ns.rows + [area ~ '||' ~ eid ~ '||' ~ fname ~ '||' ~ kind] -%}
  {%- endif -%}
{%- endfor -%}
{{ ns.rows | join('\\n') }}
"""
    try:
        resp = requests.post(
            f'{HA_URL}/api/template',
            json={'template': template.strip()},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.text.strip()
        if not text:
            return {}

        result: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split('||')
            if len(parts) != 4:
                continue
            area_name, eid, fname, kind = [p.strip() for p in parts]
            key = area_name.lower()
            if key not in result:
                result[key] = {'lights': [], 'fans': []}
            bucket = 'lights' if kind == 'light' else 'fans'
            result[key][bucket].append({'entity_id': eid, 'name': fname})

        logger.info('Discovered %d areas from HA: %s', len(result), list(result.keys()))
        return result
    except Exception:
        logger.exception('Failed to fetch areas from HA')
        return {}


def _get_area_cache() -> Dict[str, Dict[str, List[str]]]:
    """Get cached area data, refreshing if stale."""
    global _area_cache, _area_cache_ts
    now = time.time()
    if now - _area_cache_ts > _AREA_CACHE_TTL or not _area_cache:
        fresh = _fetch_areas()
        if fresh:
            _area_cache = fresh
            _area_cache_ts = now
        elif not _area_cache:
            _area_cache_ts = now  # Don't hammer HA if it's down
    return _area_cache


def _call_service(domain: str, service: str, entity_id: str,
                  data: Optional[Dict] = None) -> bool:
    """Call a Home Assistant service. Returns True on success."""
    url = f'{HA_URL}/api/services/{domain}/{service}'
    payload: Dict[str, Any] = {'entity_id': entity_id}
    if data:
        payload.update(data)
    try:
        resp = requests.post(url, json=payload, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, list) and len(result) == 0:
            logger.warning('HA returned empty result for %s/%s %s — entity may not exist',
                           domain, service, entity_id)
        return True
    except Exception:
        logger.exception('HA service call failed: %s/%s %s', domain, service, entity_id)
        return False


def _get_state(entity_id: str) -> Optional[Dict[str, Any]]:
    """Get current state of an entity."""
    try:
        resp = requests.get(f'{HA_URL}/api/states/{entity_id}',
                            headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception('HA state fetch failed: %s', entity_id)
        return None


def _device_words(device: str) -> List[str]:
    """Split a spoken device phrase into significant match words."""
    return [
        w for w in device.lower().strip().split()
        if len(w) > 2 and w not in _FILLER_WORDS
    ]


def _resolve_device_global(kind: str, device: str) -> Tuple[str, List[str]]:
    """Find entities across ALL areas whose friendly name matches `device`.

    Used when the user names a device without naming a room ("turn on
    ceiling fan"). Prefers matches outside sleep/kid areas so the nursery
    fan doesn't turn on uninvited — falls back to quiet areas only when
    there is no other match.

    Returns (display_label, entity_ids). display_label is a human-readable
    summary used in response messages.
    """
    areas = _get_area_cache()
    entity_key = 'lights' if kind == 'light' else 'fans'
    words = _device_words(device)
    if not words:
        return '', []

    preferred: List[Tuple[str, Dict[str, str]]] = []  # (area, entity)
    fallback: List[Tuple[str, Dict[str, str]]] = []

    for area_name, buckets in areas.items():
        for e in buckets.get(entity_key, []) or []:
            name_lower = (e.get('name') or '').lower()
            if not all(w in name_lower for w in words):
                continue
            if area_name in _QUIET_AREAS:
                fallback.append((area_name, e))
            else:
                preferred.append((area_name, e))

    chosen = preferred or fallback
    if not chosen:
        logger.info('Global device search "%s" (%s) → no matches', device, kind)
        return '', []

    logger.info(
        'Global device search "%s" (%s) → %s',
        device, kind, [f"{a}:{e['name']}" for a, e in chosen],
    )

    # If multiple areas matched, summarize them; otherwise use the one.
    unique_areas = sorted({a for a, _ in chosen if a != _UNASSIGNED_KEY})
    if len(unique_areas) == 1:
        display = unique_areas[0]
    elif unique_areas:
        display = ', '.join(unique_areas)
    else:
        display = ''  # only unassigned matches

    return display, [e['entity_id'] for _, e in chosen]


def _resolve_room(room: Optional[str], domain: str = 'light',
                  device: Optional[str] = None) -> Tuple[str, List[str]]:
    """Resolve a spoken room name to (display_name, entity_ids).

    Behavior:
    - `room` + `device` → look only inside that area, filter by name.
    - `room` alone      → whole-area toggle.
    - `device` alone    → global friendly-name search across all areas
                          (preferred over falling back to DEFAULT_ROOM,
                          which would miss devices owners keep unassigned
                          on purpose so whole-room commands don't toggle
                          them).
    - neither           → default room, whole-area toggle.

    Dynamically queries HA areas so new entities are picked up automatically.
    """
    kind = 'light' if domain == 'light' else 'fan'

    # Device named without a room → global search.
    if device and not room:
        return _resolve_device_global(kind, device)

    areas = _get_area_cache()

    if not room:
        room = DEFAULT_ROOM

    room_lower = room.lower().strip()

    # Apply aliases (spoken name → HA area name)
    room_lower = ROOM_ALIASES.get(room_lower, room_lower)

    entity_key = 'lights' if kind == 'light' else 'fans'

    # Find the matching area record — direct or fuzzy
    area_record = None
    display = room_lower
    if room_lower in areas:
        area_record = areas[room_lower]
        display = room_lower
    else:
        for area_name, entities in areas.items():
            if area_name == _UNASSIGNED_KEY:
                continue
            if room_lower in area_name or area_name in room_lower:
                area_record = entities
                display = area_name
                break

    if area_record is None:
        return room_lower, []

    entities = area_record.get(entity_key, []) or []

    if not device:
        # Whole-area toggle: return all entity IDs
        return display, [e['entity_id'] for e in entities]

    # Device filter: keep only entities whose friendly name contains
    # every significant word from the spoken device phrase. This lets
    # "ceiling fan light" match "Family Room Ceiling Fan Light".
    words = _device_words(device)
    if not words:
        return display, [e['entity_id'] for e in entities]

    matches: List[Dict[str, str]] = []
    for e in entities:
        name_lower = (e.get('name') or '').lower()
        if all(w in name_lower for w in words):
            matches.append(e)

    if matches:
        logger.info('Device filter "%s" in %s → %s',
                    device, display, [m['name'] for m in matches])
        return display, [m['entity_id'] for m in matches]

    # Nothing in this area matches the device name → fall back to global
    # search. Owners often leave fixtures unassigned to keep whole-room
    # toggles clean, so "living room ceiling fan" should still find a
    # ceiling fan that lives outside the Living Room area.
    logger.info('Device filter "%s" in %s → no in-area matches, trying global',
                device, display)
    global_display, global_ids = _resolve_device_global(kind, device)
    if global_ids:
        return global_display or display, global_ids

    logger.info('Device filter "%s" in %s → no matches (candidates: %s)',
                device, display, [e['name'] for e in entities])
    return display, []


_FILLER_WORDS = {'the', 'a', 'an', 'my', 'our', 'in', 'on', 'of', 'and', 'please'}


def _not_found_message(kind: str, room: Optional[str], device: Optional[str],
                       display_name: str) -> str:
    """Build a "can't find it" error message that reads naturally whether
    the user named a room, a device, both, or neither."""
    kind_noun = 'lights' if kind == 'light' else 'fan'
    if device and not room:
        return f"I couldn't find a \"{device}\" anywhere in Home Assistant."
    if device:
        return f"I don't know a \"{device}\" in the {display_name}."
    return f"I don't know any {kind_noun} in the {display_name}."


# -------------------------------------------------------------------------
# Handlers
# -------------------------------------------------------------------------

def handle_lights_on(room: Optional[str] = None,
                     brightness: Optional[int] = None,
                     device: Optional[str] = None) -> Dict[str, Any]:
    """Turn on lights in a room, optionally a specific fixture, with brightness."""
    display_name, entities = _resolve_room(room, 'light', device=device)

    if not entities:
        return {
            'type': 'smart_home',
            'status': 'error',
            'message': _not_found_message('light', room, device, display_name),
            'data': {},
        }

    data = {}
    if brightness is not None:
        data['brightness_pct'] = max(1, min(100, brightness))

    success = all(_call_service('light', 'turn_on', eid, data) for eid in entities)

    label = device if device else f'{display_name} lights'
    if brightness is not None:
        msg = f'{label} set to {brightness} percent.'
    else:
        msg = f'{label} on.' if device else f'{display_name} lights are on.'

    return {
        'type': 'smart_home',
        'status': 'ok' if success else 'error',
        'message': msg[0].upper() + msg[1:] if success else f"Had trouble with the {label}.",
        'data': {'room': display_name, 'device': device, 'action': 'on', 'brightness': brightness},
    }


def handle_lights_off(room: Optional[str] = None,
                      device: Optional[str] = None) -> Dict[str, Any]:
    """Turn off lights in a room, optionally a specific fixture."""
    display_name, entities = _resolve_room(room, 'light', device=device)

    if not entities:
        return {
            'type': 'smart_home',
            'status': 'error',
            'message': _not_found_message('light', room, device, display_name),
            'data': {},
        }

    success = all(_call_service('light', 'turn_off', eid) for eid in entities)

    label = device if device else f'{display_name} lights'
    msg = f'{label} off.' if device else f'{display_name} lights are off.'

    return {
        'type': 'smart_home',
        'status': 'ok' if success else 'error',
        'message': msg[0].upper() + msg[1:] if success else f"Had trouble with the {label}.",
        'data': {'room': display_name, 'device': device, 'action': 'off'},
    }


def handle_volume(level: Optional[int] = None,
                  direction: Optional[str] = None) -> Dict[str, Any]:
    """Set or adjust the Denon AVR volume."""
    entity = 'media_player.denon_avr_s760h'

    if direction:
        state = _get_state(entity)
        if not state:
            return {
                'type': 'smart_home',
                'status': 'error',
                'message': "Couldn't reach the receiver.",
                'data': {},
            }
        current = int(state.get('attributes', {}).get('volume_level', 0.3) * 100)
        step = 10
        if direction == 'up':
            level = min(100, current + step)
        else:
            level = max(0, current - step)

    if level is None:
        return {
            'type': 'smart_home',
            'status': 'error',
            'message': "What volume level?",
            'data': {},
        }

    ha_level = level / 100.0
    success = _call_service('media_player', 'volume_set', entity,
                            {'volume_level': ha_level})

    return {
        'type': 'smart_home',
        'status': 'ok' if success else 'error',
        'message': f'Volume set to {level}.' if success
                   else "Had trouble setting the volume.",
        'data': {'volume': level},
    }


def handle_fan_on(room: Optional[str] = None,
                  device: Optional[str] = None) -> Dict[str, Any]:
    """Turn on a fan, optionally a specific one by name."""
    display_name, entities = _resolve_room(room, 'fan', device=device)

    if not entities:
        return {
            'type': 'smart_home',
            'status': 'error',
            'message': _not_found_message('fan', room, device, display_name),
            'data': {},
        }

    success = all(_call_service('fan', 'turn_on', eid) for eid in entities)
    label = device if device else f'{display_name} fan'
    return {
        'type': 'smart_home',
        'status': 'ok' if success else 'error',
        'message': (f'{label} on.' if device else f'{display_name} fan is on.')
                   .capitalize() if success
                   else f"Had trouble with the {label}.",
        'data': {'room': display_name, 'device': device or 'fan', 'action': 'on'},
    }


def handle_fan_off(room: Optional[str] = None,
                   device: Optional[str] = None) -> Dict[str, Any]:
    """Turn off a fan, optionally a specific one by name."""
    display_name, entities = _resolve_room(room, 'fan', device=device)

    if not entities:
        return {
            'type': 'smart_home',
            'status': 'error',
            'message': _not_found_message('fan', room, device, display_name),
            'data': {},
        }

    success = all(_call_service('fan', 'turn_off', entity) for entity in entities)
    label = device if device else f'{display_name} fan'
    return {
        'type': 'smart_home',
        'status': 'ok' if success else 'error',
        'message': (f'{label} off.' if device else f'{display_name} fan is off.')
                   .capitalize() if success
                   else f"Had trouble with the {label}.",
        'data': {'room': display_name, 'device': device or 'fan', 'action': 'off'},
    }
