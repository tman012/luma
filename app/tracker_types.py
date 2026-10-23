"""Tracker type definitions for Luma's built-in tracker manager.

A type is a definition only: its name, protocol, the fields an instance needs
and the categories it can search. Instances (URL, credentials, categories,
priority) are added by the owner in Settings. Nothing is preconfigured: a
fresh install has zero trackers.

Clean-room code written from the sites' public API behavior. No code or
definitions from Prowlarr, Jackett or Cardigann.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

# Field kinds the UI knows how to render.
TEXT, URL, SECRET, NUMBER, CATEGORIES = 'text', 'url', 'secret', 'number', 'categories'

PRIORITY_MIN, PRIORITY_MAX, PRIORITY_DEFAULT = 1, 50, 25
NAME_MAX = 60
VALUE_MAX = 4096           # longest accepted text or secret value (cookies can be long)

# Newznab standard categories offered to Torznab/Newznab trackers.
NEWZNAB_CATEGORIES = [
    (2000, 'Movies'), (2030, 'Movies/SD'), (2040, 'Movies/HD'), (2045, 'Movies/UHD'),
    (2050, 'Movies/BluRay'), (2060, 'Movies/3D'), (2070, 'Movies/DVD'), (2080, 'Movies/WEB-DL'),
    (5000, 'TV'), (5030, 'TV/SD'), (5040, 'TV/HD'), (5045, 'TV/UHD'), (5060, 'TV/Sport'),
    (5070, 'TV/Anime'), (5080, 'TV/Documentary'),
]
SITE_CATEGORIES = [(2000, 'Movies'), (5000, 'TV')]
IPT_CATEGORIES = [(2000, 'Movies'), (5000, 'TV'), (5060, 'TV/Sport')]
DEFAULT_CATEGORIES = [2000, 5000]


def _f(key: str, label: str, kind: str, required: bool = False, default: Any = None) -> Dict[str, Any]:
    return {'key': key, 'label': label, 'kind': kind, 'required': required,
            'secret': kind == SECRET, 'default': default}


def _common(url_default: Optional[str]) -> List[Dict[str, Any]]:
    return [_f('name', 'Name', TEXT, True), _f('url', 'URL', URL, True, url_default)]


def _tail() -> List[Dict[str, Any]]:
    return [_f('categories', 'Categories', CATEGORIES, True, list(DEFAULT_CATEGORIES)),
            _f('priority', 'Priority', NUMBER, True, PRIORITY_DEFAULT)]


def _avistaz_family(name: str, url: str) -> Dict[str, Any]:
    return {
        'name': name, 'protocol': 'torrent', 'client': 'avistaz', 'family': 'avistaz',
        'fields': _common(url) + [
            _f('username', 'Username', TEXT, True),
            _f('password', 'Password', SECRET, True),
            _f('pid', 'PID', SECRET, True),
        ] + _tail(),
        'categories': SITE_CATEGORIES,
    }


# type id -> definition. 'client' picks the search/test code in tracker_clients.
# 'family' 'avistaz' marks the AvistaZ network sites: every request to them
# (user or background) goes through tracker_guard's strict token bucket.
# Budgets and backoffs are keyed per type for known sites and per host for
# Torznab/Newznab (tracker_guard.guard_key), not per family or record.
TRACKER_TYPES: Dict[str, Dict[str, Any]] = {
    'torznab': {
        'name': 'Torznab', 'protocol': 'torrent', 'client': 'torznab', 'family': '',
        'fields': _common(None) + [_f('api_key', 'API key', SECRET)] + _tail(),
        'categories': NEWZNAB_CATEGORIES,
    },
    'newznab': {
        'name': 'Newznab', 'protocol': 'usenet', 'client': 'torznab', 'family': '',
        'fields': _common(None) + [_f('api_key', 'API key', SECRET)] + _tail(),
        'categories': NEWZNAB_CATEGORIES,
    },
    'avistaz': _avistaz_family('AvistaZ', 'https://avistaz.to'),
    'beyondhd': {
        'name': 'BeyondHD', 'protocol': 'torrent', 'client': 'beyondhd', 'family': '',
        'fields': _common('https://beyond-hd.me') + [
            _f('api_key', 'API key', SECRET, True),
            _f('rss_key', 'RSS key', SECRET, True),
        ] + _tail(),
        'categories': SITE_CATEGORIES,
    },
    'cinemaz': _avistaz_family('CinemaZ', 'https://cinemaz.to'),
    'exoticaz': _avistaz_family('ExoticaZ', 'https://exoticaz.to'),
    'iptorrents': {
        'name': 'IPTorrents', 'protocol': 'torrent', 'client': 'iptorrents', 'family': '',
        'fields': _common('https://iptorrents.com') + [
            _f('cookie', 'Cookie', SECRET, True),
            _f('user_agent', 'User agent', TEXT),
        ] + _tail(),
        'categories': IPT_CATEGORIES,
    },
    'morethantv': {
        'name': 'MoreThanTV', 'protocol': 'torrent', 'client': 'torznab', 'family': '',
        'torznab_path': '/api/torznab',
        'fields': _common('https://www.morethantv.me') + [_f('api_key', 'API key', SECRET, True)] + _tail(),
        'categories': NEWZNAB_CATEGORIES,
    },
    'pixelhd': _avistaz_family('PixelHD', 'https://pixelhd.me'),            # v169: AvistaZ network
    'privatehd': _avistaz_family('PrivateHD', 'https://privatehd.to'),
}

# Torznab and Newznab are for trackers that serve their own Torznab or
# Newznab API on a public host (never a Jackett or Prowlarr proxy; see
# tracker_store). They come first, then the site definitions alphabetically.
GENERIC_TYPES = ('torznab', 'newznab')
_GENERIC = GENERIC_TYPES


def is_generic(type_id: str) -> bool:
    return str(type_id or '').strip().lower() in GENERIC_TYPES


def type_ids() -> List[str]:
    rest = sorted((t for t in TRACKER_TYPES if t not in _GENERIC), key=lambda t: TRACKER_TYPES[t]['name'].lower())
    return list(_GENERIC) + rest


def get_type(type_id: str) -> Optional[Dict[str, Any]]:
    return TRACKER_TYPES.get(str(type_id or '').strip().lower())


def secret_keys(type_id: str) -> List[str]:
    t = get_type(type_id) or {}
    return [f['key'] for f in t.get('fields', []) if f['secret']]


def setting_keys(type_id: str) -> List[str]:
    """Per-type non-secret fields stored under 'settings' (not name/url/categories/priority)."""
    t = get_type(type_id) or {}
    return [f['key'] for f in t.get('fields', [])
            if not f['secret'] and f['key'] not in ('name', 'url', 'categories', 'priority')]


def public_types() -> List[Dict[str, Any]]:
    """The type list for the UI: id, name, protocol and field schemas."""
    out = []
    for tid in type_ids():
        t = TRACKER_TYPES[tid]
        out.append({
            'id': tid, 'name': t['name'], 'protocol': t['protocol'],
            'fields': [dict(f) for f in t['fields']],
            'categories': [{'id': c, 'name': n} for c, n in t['categories']],
        })
    return out


def searches_by_id(type_id: str) -> bool:
    """Types whose client searches by TMDB id when Sources has one (BeyondHD)."""
    return (get_type(type_id) or {}).get('client') == 'beyondhd'


def pages_feed(type_id: str) -> bool:
    """v256: Torznab/Newznab APIs page by offset, so the feed can page back to the last release seen.
    The site clients answer one page (their newest) and are never paged."""
    return (get_type(type_id) or {}).get('client') == 'torznab'


def is_rate_strict(type_id: str) -> bool:
    return (get_type(type_id) or {}).get('family') == 'avistaz'


# v169 (tracker port step 1): the strict 400 cooldown, ported from indexer_api's
# GUARD_LIMITS['strict_400_definitions'] (AvistaZ, PrivateHD, PixelHD, CinemaZ,
# ExoticaZ, IPTorrents). A 400 from one of these is treated as the site
# throttling us: a rate backoff (1 h, doubling, max 6 h), as the Prowlarr path does.
STRICT_400_TYPES = ('avistaz', 'cinemaz', 'exoticaz', 'iptorrents', 'pixelhd', 'privatehd')


def is_strict_400(type_id: str) -> bool:
    t = str(type_id or '').strip().lower()
    return t in STRICT_400_TYPES and get_type(t) is not None


# ---------------------------------------------------------------- validation

REQUIRED, INVALID_URL, INVALID, TOO_LONG = 'Required', 'Invalid URL', 'Invalid', 'Too long'


def normalize_url(value: Any) -> Optional[str]:
    """http(s) URL with a host and no credentials, query or fragment, without
    a trailing slash; None if it is not one."""
    s = str(value or '').strip()
    if not s or len(s) > 2048 or any(c.isspace() for c in s):
        return None
    try:
        parts = urlsplit(s)
        port = parts.port      # raises on a bad port
    except ValueError:
        return None
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        return None
    if parts.username or parts.password or parts.query or parts.fragment or '@' in parts.netloc:
        return None
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not all(lbl and len(lbl) <= 63 for lbl in host.rstrip('.').split('.')):
            return None
    del port
    return f"{parts.scheme.lower()}://{parts.netloc}{parts.path}".rstrip('/')


def validate_categories(type_id: str, value: Any) -> Optional[List[int]]:
    t = get_type(type_id)
    if t is None or not isinstance(value, list) or not value:
        return None
    allowed = {c for c, _ in t['categories']}
    out: List[int] = []
    for v in value:
        if isinstance(v, bool):
            return None
        try:
            c = int(v)
        except (TypeError, ValueError):
            return None
        if c not in allowed:
            return None
        if c not in out:
            out.append(c)
    return sorted(out)


def validate_priority(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != int(f):
        return None
    p = int(f)
    return p if PRIORITY_MIN <= p <= PRIORITY_MAX else None
