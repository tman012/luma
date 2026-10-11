"""
ha_service.py — Home Assistant REST API client.

Provides helpers for firing entities (scenes, switches, etc.),
testing HA connectivity, and reading/controlling the state of
entities grouped by HA areas (rooms).
"""

import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _render_template(ha_url: str, access_token: str, template: str, timeout: int = 8) -> str | None:
    """Render a Jinja template via HA's REST ``/api/template`` and return the
    raw string body. Used to enumerate areas/entity-registry data that is not
    otherwise exposed on the REST surface.
    """
    if not ha_url or not access_token:
        return None
    url = f"{ha_url.rstrip('/')}/api/template"
    try:
        resp = requests.post(
            url,
            json={"template": template},
            headers=_auth_headers(access_token),
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.text
        logger.warning("HA template render failed %s: %s", resp.status_code, resp.text[:200])
        return None
    except Exception as e:
        logger.warning("HA template render error: %s", e)
        return None


def list_areas(ha_url: str, access_token: str) -> list[dict]:
    """Return all HA areas as ``[{"id": str, "name": str}]``.

    Uses a single template render — HA's REST surface has no dedicated area
    endpoint, so we ask Home Assistant to serialize its own area registry as
    JSON via the ``areas()`` / ``area_name()`` template functions.
    """
    template = (
        "{% set ns = namespace(items=[]) %}"
        "{% for a in areas() %}"
        "{% set ns.items = ns.items + [{'id': a, 'name': area_name(a)}] %}"
        "{% endfor %}"
        "{{ ns.items | tojson }}"
    )
    body = _render_template(ha_url, access_token, template)
    if not body:
        return []
    try:
        parsed = json.loads(body)
        if isinstance(parsed, list):
            # Sort by name for stable UI ordering.
            return sorted(parsed, key=lambda x: (x.get('name') or '').lower())
        return []
    except Exception as e:
        logger.warning("list_areas parse failed: %s (body=%r)", e, body[:200])
        return []


def _fetch_all_states(ha_url: str, access_token: str, timeout: int = 10) -> list[dict]:
    url = f"{ha_url.rstrip('/')}/api/states"
    try:
        resp = requests.get(url, headers=_auth_headers(access_token), timeout=timeout)
        if resp.status_code == 200 and isinstance(resp.json(), list):
            return resp.json()
        logger.warning("HA /api/states returned %s", resp.status_code)
        return []
    except Exception as e:
        logger.warning("HA /api/states error: %s", e)
        return []


# Domains the smart-home panel is allowed to show. Anything outside this list
# is noise (sun, persistent_notification, weather sensors, etc.) and would
# clutter the UI without being controllable in a meaningful way.
CONTROLLABLE_DOMAINS = {
    "light",
    "fan",
    "cover",
    "climate",
    "scene",
    "script",
    "input_boolean",
    "lock",
}


def _resolve_area_name(ha_url: str, access_token: str, area_id: str) -> str:
    """Best-effort area human name lookup. Falls back to the slug."""
    template = f"{{{{ area_name('{area_id}') or '{area_id}' }}}}"
    body = _render_template(ha_url, access_token, template)
    return (body or area_id).strip()


def get_area_entities(ha_url: str, access_token: str, area_id: str) -> list[dict]:
    """Return controllable entities belonging to an area.

    Membership is broader than HA's own area assignment: an entity is
    considered part of a room if any of the following is true —
      1. Home Assistant has it registered to that area (``area_entities()``)
      2. Its ``entity_id`` slug starts with or contains the area slug
         (e.g. ``light.office_desk`` for area ``office``)
      3. Its friendly name contains the area's human-readable name
         (e.g. "Office Desk" for area "Office")

    This covers entities the user forgot to assign in HA's UI but clearly
    belong to a room by name, which matches expectations for a kiosk panel.
    """
    if not area_id:
        return []

    # Collect the canonical HA-assigned set first.
    template = (
        "{% set ns = namespace(items=[]) %}"
        f"{{% for eid in area_entities('{area_id}') %}}"
        "{% set ns.items = ns.items + [eid] %}"
        "{% endfor %}"
        "{{ ns.items | tojson }}"
    )
    body = _render_template(ha_url, access_token, template)
    try:
        assigned = set(json.loads(body) or [])
    except Exception:
        assigned = set()

    area_name = _resolve_area_name(ha_url, access_token, area_id)
    area_slug = area_id.strip().lower()
    area_name_lc = area_name.strip().lower()

    # Name-match tokens. We require word boundaries so "bed" in area "Bed"
    # doesn't claim "bedroom.x" incorrectly; we also allow the slug form that
    # appears in entity IDs (``office_desk``, ``living_room_lamp``).
    slug_token = area_slug.replace('-', '_')
    name_token = area_name_lc

    def matches_by_name(entity_id: str, friendly: str) -> bool:
        eid_lc = entity_id.lower()
        slug_part = eid_lc.split('.', 1)[1] if '.' in eid_lc else eid_lc
        # Entity-id slug must contain the area slug as a whole word-ish chunk
        # (bounded by underscores or start/end).
        parts = slug_part.split('_')
        if slug_token and slug_token in parts:
            return True
        # Or multi-word slug tokens like "living_room" match as a substring
        # with underscore boundaries.
        if '_' in slug_token and slug_token in slug_part:
            return True
        # Friendly name case-insensitive substring — "Office Desk" for "Office".
        if name_token and friendly and name_token in friendly.lower():
            return True
        return False

    all_states = _fetch_all_states(ha_url, access_token)
    out = []
    seen = set()
    for state in all_states:
        eid = state.get("entity_id")
        if not eid or eid in seen:
            continue
        domain = eid.split(".", 1)[0]
        if domain not in CONTROLLABLE_DOMAINS:
            continue
        attrs = state.get("attributes") or {}
        friendly = attrs.get("friendly_name") or eid
        in_area = eid in assigned or matches_by_name(eid, friendly)
        if not in_area:
            continue
        seen.add(eid)
        out.append({
            "entity_id": eid,
            "domain": domain,
            "name": friendly,
            "state": state.get("state"),
            "attributes": attrs,
        })

    # Group order: lights → fans → covers → climate → scenes → scripts → helpers → locks
    order = {d: i for i, d in enumerate([
        "light", "fan", "cover", "climate",
        "scene", "script", "input_boolean", "lock",
    ])}
    out.sort(key=lambda e: (order.get(e["domain"], 99), e["name"].lower()))
    return out


def call_service(
    ha_url: str,
    access_token: str,
    domain: str,
    service: str,
    service_data: dict[str, Any] | None = None,
) -> dict:
    """Call an arbitrary HA service. Returns ``{"ok": bool, "detail": str}``.

    ``service_data`` is the full payload posted to HA, typically including an
    ``entity_id`` plus any parameters (``brightness_pct``, ``temperature``,
    ``position``, etc.). Validation lives on the HA side — we just forward.
    """
    if not ha_url or not access_token:
        return {"ok": False, "detail": "Home Assistant is not configured"}
    if not domain or not service:
        return {"ok": False, "detail": "domain and service are required"}

    url = f"{ha_url.rstrip('/')}/api/services/{domain}/{service}"
    try:
        resp = requests.post(
            url,
            json=service_data or {},
            headers=_auth_headers(access_token),
            timeout=10,
        )
        if resp.status_code == 200:
            return {"ok": True, "detail": f"{domain}.{service} called"}
        return {"ok": False, "detail": f"HA returned {resp.status_code}: {resp.text[:200]}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "detail": "HA request timed out (10s)"}
    except requests.exceptions.ConnectionError as e:
        return {"ok": False, "detail": f"Cannot connect to HA: {e}"}
    except Exception as e:
        logger.warning("call_service(%s.%s) error: %s", domain, service, e)
        return {"ok": False, "detail": str(e)}


def fire_entity(ha_url: str, access_token: str, entity_id: str) -> dict:
    """Activate a Home Assistant entity via the services API.

    Parses the domain from *entity_id* (e.g. ``scene.living_room`` → ``scene``)
    and calls ``POST /api/services/{domain}/turn_on``.

    Returns ``{"ok": bool, "detail": str}``.
    """
    if not ha_url or not access_token or not entity_id:
        return {"ok": False, "detail": "Missing HA URL, token, or entity_id"}

    domain = entity_id.split('.', 1)[0] if '.' in entity_id else ''
    if not domain:
        return {"ok": False, "detail": f"Cannot parse domain from entity_id '{entity_id}'"}

    url = f"{ha_url.rstrip('/')}/api/services/{domain}/turn_on"
    try:
        resp = requests.post(
            url,
            json={"entity_id": entity_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return {"ok": True, "detail": f"Fired {entity_id}"}
        return {"ok": False, "detail": f"HA returned {resp.status_code}: {resp.text[:200]}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "detail": "HA request timed out (10s)"}
    except requests.exceptions.ConnectionError as e:
        return {"ok": False, "detail": f"Cannot connect to HA: {e}"}
    except Exception as e:
        logger.warning("fire_entity(%s) error: %s", entity_id, e)
        return {"ok": False, "detail": str(e)}


def test_connection(ha_url: str, access_token: str) -> dict:
    """Validate Home Assistant connectivity via ``GET /api/``.

    Returns ``{"ok": bool, "detail": str}``.
    """
    if not ha_url:
        return {"ok": False, "detail": "Home Assistant URL is required"}
    if not access_token:
        return {"ok": False, "detail": "Access token is required"}

    url = f"{ha_url.rstrip('/')}/api/"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
            msg = data.get('message', 'Connected')
            return {"ok": True, "detail": msg}
        elif resp.status_code == 401:
            return {"ok": False, "detail": "Invalid access token (401)"}
        return {"ok": False, "detail": f"HA returned status {resp.status_code}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "detail": "Connection timed out (5s)"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "detail": f"Cannot connect to {ha_url}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
