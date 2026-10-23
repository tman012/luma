"""v260 Downloads guard settings (Settings > Automation), JSON in the data dir (0600, atomic replace).

Prod 1b4ea74's defaults (app/downloads_guard.py, app/storage_cleanup.py): off (:92), check every 15 minutes
(:59), free-space floor 150 GB (:57), reclaim 100 GB a run (:58), daily limit 500 GB with 0 for none (:60),
IPTorrents first (:62), the storage pill's 100 GB (storage_cleanup DEFAULT_TARGET_BYTES), 48 hours minimum age
(MIN_AGE_HOURS) and 2 TiB a run (MAX_TARGET_BYTES, here 2048 GB). New on test (PM): the per-run ceiling is a
setting, the tracker preference is an ordered list, minimum seed time and ratio (off), the scope (every
finished torrent, or only the ones this install added), and the folder (empty: media.downloads_root, as prod).

`widens(old, new)` names the changes that make the guard delete more, sooner, more often or somewhere else;
those need the owner signed in with a session (dg_routes). Nothing here talks to a network service.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from typing import Any, Dict, List

from app import paths as _paths

SETTINGS_FILE = 'downloads_guard_settings.json'
_lock = threading.RLock()
_cfg: Dict[str, Any] = {}       # tests: {'path': ...}

SCOPES = ('all', 'test_tagged')
MAX_TRACKERS = 20
_TRACKER_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,39}$')

DEFAULTS: Dict[str, Any] = {
    'enabled': False,
    'check_interval_minutes': 15,
    'min_free_gb': 150,
    'reclaim_gb': 100,
    'daily_cap_gb': 500,
    'max_run_gb': 2048,
    'trackers': ['ipt'],
    'pill_gb': 100,
    'min_age_hours': 48,
    'min_seed_hours': 0,          # 0: off
    'min_ratio': 0.0,             # 0: off
    'scope': 'all',
    'folder': '',                 # '': media.downloads_root (storage_cleanup.downloads_root)
}

# (low, high) for the whole-number settings
INT_RANGES: Dict[str, tuple] = {
    'check_interval_minutes': (1, 1440),
    'min_free_gb': (1, 10_000),
    'reclaim_gb': (1, 2_000),
    'daily_cap_gb': (0, 20_000),
    'max_run_gb': (1, 20_480),
    'pill_gb': (1, 2_000),
    'min_age_hours': (0, 8_760),
    'min_seed_hours': (0, 87_600),
}
RATIO_MAX = 100.0


class Invalid(Exception):
    def __init__(self, errors: Dict[str, str]):
        super().__init__('invalid')
        self.errors = errors


def path() -> str:
    return str(_cfg.get('path') or _paths.data_path(SETTINGS_FILE))


def defaults() -> Dict[str, Any]:
    return json.loads(json.dumps(DEFAULTS))


def _int(v: Any, lo: int, hi: int):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or isinstance(v, float) and not v.is_integer():
        return None
    v = int(v)
    return v if lo <= v <= hi else None


def _trackers(v: Any):
    if not isinstance(v, list) or len(v) > MAX_TRACKERS:
        return None
    out: List[str] = []
    for t in v:
        if not isinstance(t, str) or not _TRACKER_RE.match(t.strip()) or t.strip() in out:
            return None
        out.append(t.strip())
    return out


def _folder(v: Any):
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not v:
        return ''
    if not v.startswith('/') or '\x00' in v or len(v) > 1024:
        return None
    return os.path.normpath(v)


def check(body: Any, cur: Dict[str, Any]) -> Dict[str, Any]:
    """*cur* with *body*'s known keys applied, or Invalid({key: message}). Unknown keys are refused."""
    if not isinstance(body, dict):
        raise Invalid({'': 'Invalid request'})
    out = dict(cur)
    errors: Dict[str, str] = {}
    for k, v in body.items():
        if k not in DEFAULTS:
            errors[k] = 'Unknown setting'
        elif k == 'enabled':
            if isinstance(v, bool):
                out[k] = v
            else:
                errors[k] = 'Must be on or off'
        elif k in INT_RANGES:
            lo, hi = INT_RANGES[k]
            n = _int(v, lo, hi)
            if n is None:
                errors[k] = f'Must be a whole number from {lo} to {hi}'
            else:
                out[k] = n
        elif k == 'min_ratio':
            ok = not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= RATIO_MAX
            if ok:
                out[k] = round(float(v), 2)
            else:
                errors[k] = f'Must be a number from 0 to {RATIO_MAX:g}'
        elif k == 'trackers':
            t = _trackers(v)
            if t is None:
                errors[k] = 'Must be a list of trackers'
            else:
                out[k] = t
        elif k == 'scope':
            if v in SCOPES:
                out[k] = v
            else:
                errors[k] = 'Unknown scope'
        elif k == 'folder':
            f = _folder(v)
            if f is None:
                errors[k] = 'Must be a full path'
            elif f and not os.path.isdir(f):
                errors[k] = 'Folder not found'
            else:
                out[k] = f
    if errors:
        raise Invalid(errors)
    return out


def _normalize(raw: Any) -> Dict[str, Any]:
    base = defaults()
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    for k in DEFAULTS:
        if k not in raw:
            continue
        try:
            out = check({k: raw[k]}, out) if k != 'folder' else {**out, 'folder': _folder(raw[k]) or ''}
        except Invalid:
            pass
    return out


def settings() -> Dict[str, Any]:
    with _lock:
        try:
            with open(path(), 'r') as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = None
        return _normalize(raw)


def save(body: Any) -> Dict[str, Any]:
    with _lock:
        new = check(body, settings())
        _paths.atomic_write_json(path(), new, mode=0o600)
        return new


def widens(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """Settings in *new* that make the guard delete more, sooner, more often or elsewhere than *old*."""
    out: List[str] = []
    if new['enabled'] and not old['enabled']:
        out.append('enabled')
    for k in ('reclaim_gb', 'max_run_gb', 'pill_gb', 'min_free_gb'):
        if new[k] > old[k]:
            out.append(k)
    old_cap, new_cap = old['daily_cap_gb'], new['daily_cap_gb']
    if old_cap and (new_cap == 0 or new_cap > old_cap):
        out.append('daily_cap_gb')
    for k in ('check_interval_minutes', 'min_age_hours'):
        if new[k] < old[k]:
            out.append(k)
    for k in ('min_seed_hours', 'min_ratio'):
        if old[k] and (not new[k] or new[k] < old[k]):
            out.append(k)
    added = [t for t in new['trackers'] if t not in old['trackers']]
    kept_old = [t for t in old['trackers'] if t in new['trackers']]
    kept_new = [t for t in new['trackers'] if t in old['trackers']]
    if added or kept_old != kept_new:
        out.append('trackers')
    if new['scope'] != old['scope']:
        out.append('scope')
    if new['folder'] != old['folder']:
        out.append('folder')
    return out
