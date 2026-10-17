"""TV settings for luma-test (minimal) builds: tv_settings.json in the data
dir (0600), separate from file_management.json (whose path map and transfer
mode TV shares).

    {"version": 1,
     "paths": [], "rules": [], "fallback_path_id": null,
     "naming": {"series_folder": "{title} ({year})", ...},
     "upgrades": {"mode": "keep_both" | "replace_verified"},
     "automation": {"enabled": false, ...},
     "rescan_interval_h": 6}

* Naming: a scan accepts any existing folder name; only new series get a
  folder from `series_folder`, which starts at "Title (Year)".
* Upgrades: keep_both (the old file stays and is listed as superseded) until
  the owner picks replace_verified (the old file goes only after the new one
  is in place and verified).
* Automation is off. The tracker caps live in app/tv_budget (code, not here).

GET /api/settings/tv     the settings
PUT /api/settings/tv     {"naming": {...}, "upgrades": {...}, "automation": {...}} (any subset)

Owner only, luma-test builds only. TV library paths and rules arrive with
Phase 1 (their own routes).
"""

from __future__ import annotations

import copy
import json
import logging
import re
import string
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from app import paths as _paths

logger = logging.getLogger(__name__)

bp = Blueprint('tv_settings', __name__)
_cfg: Dict[str, Any] = {}
_lock = threading.RLock()

STORE_NAME = 'tv_settings.json'
UPGRADE_MODES = ('keep_both', 'replace_verified')
DEFAULT_NAMING = {
    'series_folder': '{title} ({year})',
    'season_folder': 'Season {season:02}',
    'episode': '{series} - S{season:02}E{episode:02} - {episode_title}',
    'daily': '{series} - {air_date} - {episode_title}',
    'specials_folder': 'Specials',
}
# Fields each template may use (and only these).
NAMING_FIELDS = {
    'series_folder': {'title', 'year', 'tmdb_id'},
    'season_folder': {'season'},
    'episode': {'series', 'season', 'episode', 'episode_title', 'year', 'resolution', 'source'},
    'daily': {'series', 'air_date', 'episode_title', 'year', 'resolution', 'source'},
    'specials_folder': set(),
}
REQUIRED_FIELDS = {'series_folder': {'title'}, 'season_folder': set(),
                   'episode': {'season', 'episode'}, 'daily': {'air_date'}, 'specials_folder': set()}
AUTOMATION_FLAGS = ('enabled', 'feed_enabled', 'backlog_enabled', 'upgrades_enabled')
DEFAULT_AUTOMATION = {'enabled': False, 'feed_enabled': False, 'backlog_enabled': False,
                      'upgrades_enabled': False, 'first_search_delay_h': 4}
TEMPLATE_MAX = 120
INVALID = 'Invalid'
_CTRL_RE = re.compile(r'[\x00-\x1f\x7f/\\]')
_FMT_RE = re.compile(r'^0?\d?$')


def empty() -> Dict[str, Any]:
    return {'version': 1, 'paths': [], 'rules': [], 'fallback_path_id': None,
            'naming': dict(DEFAULT_NAMING), 'upgrades': {'mode': 'keep_both'},
            'automation': dict(DEFAULT_AUTOMATION), 'rescan_interval_h': 6}


def store_path() -> Path:
    return Path(_cfg.get('store_path') or _paths.data_path(STORE_NAME))


def check_template(key: str, value: Any) -> Tuple[Optional[str], Optional[str]]:
    """(template, None) or (None, error). Placeholders must be known for the
    template; required ones must be there; no path separators or control
    characters; '{season:02}'-style zero padding only."""
    if not isinstance(value, str):
        return None, INVALID
    v = value.strip()
    if not v or len(v) > TEMPLATE_MAX or _CTRL_RE.search(v) or v in ('.', '..'):
        return None, INVALID
    try:
        parts = list(string.Formatter().parse(v))
    except ValueError:
        return None, INVALID
    used = set()
    for _lit, field, spec, conv in parts:
        if field is None:
            continue
        if field not in NAMING_FIELDS[key] or conv or not _FMT_RE.match(spec or ''):
            return None, INVALID
        used.add(field)
    if not REQUIRED_FIELDS[key] <= used:
        return None, INVALID
    return v, None


def _normalize(raw: Any) -> Dict[str, Any]:
    d = empty()
    if not isinstance(raw, dict):
        return d
    for k in ('paths', 'rules'):
        if isinstance(raw.get(k), list):
            d[k] = [x for x in raw[k] if isinstance(x, dict)]
    if isinstance(raw.get('fallback_path_id'), str):
        d['fallback_path_id'] = raw['fallback_path_id']
    naming = raw.get('naming') if isinstance(raw.get('naming'), dict) else {}
    for k in DEFAULT_NAMING:
        v, err = check_template(k, naming.get(k)) if k in naming else (None, 'missing')
        if not err:
            d['naming'][k] = v
    up = raw.get('upgrades') if isinstance(raw.get('upgrades'), dict) else {}
    if up.get('mode') in UPGRADE_MODES:
        d['upgrades']['mode'] = up['mode']
    auto = raw.get('automation') if isinstance(raw.get('automation'), dict) else {}
    for k in AUTOMATION_FLAGS:
        if isinstance(auto.get(k), bool):
            d['automation'][k] = auto[k]
    delay = auto.get('first_search_delay_h')
    if isinstance(delay, int) and not isinstance(delay, bool) and 0 <= delay <= 72:
        d['automation']['first_search_delay_h'] = delay
    r = raw.get('rescan_interval_h')
    if isinstance(r, int) and not isinstance(r, bool) and 1 <= r <= 168:
        d['rescan_interval_h'] = r
    return d


def load() -> Dict[str, Any]:
    p = store_path()
    with _lock:
        try:
            raw = json.loads(p.read_text())
        except FileNotFoundError:
            return empty()
        except (OSError, ValueError) as e:
            logger.error('TV settings unreadable (%s); starting from defaults', type(e).__name__)
            _paths.quarantine(p, 'unreadable', logger)
            return empty()
        return _normalize(raw)


def save(data: Dict[str, Any]) -> None:
    with _lock:
        _paths.atomic_write_json(store_path(), _normalize(data), mode=0o600)


def apply_put(data: Dict[str, Any], body: Any) -> Dict[str, str]:
    """Apply a PUT body to *data* in place; {field: error} when anything is
    wrong (then nothing should be saved)."""
    errors: Dict[str, str] = {}
    if not isinstance(body, dict):
        return {'_': INVALID}
    if 'naming' in body:
        n = body['naming']
        if not isinstance(n, dict) or not set(n) <= set(DEFAULT_NAMING):
            errors['naming'] = INVALID
        else:
            for k, v in n.items():
                t, e = check_template(k, v)
                if e:
                    errors[f'naming.{k}'] = e
                else:
                    data['naming'][k] = t
    if 'upgrades' in body:
        u = body['upgrades']
        if not isinstance(u, dict) or set(u) != {'mode'} or u['mode'] not in UPGRADE_MODES:
            errors['upgrades.mode'] = INVALID
        else:
            data['upgrades']['mode'] = u['mode']
    if 'automation' in body:
        a = body['automation']
        allowed = set(AUTOMATION_FLAGS) | {'first_search_delay_h'}
        if not isinstance(a, dict) or not set(a) <= allowed:
            errors['automation'] = INVALID
        else:
            for k, v in a.items():
                if k == 'first_search_delay_h':
                    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 72:
                        errors[f'automation.{k}'] = INVALID
                        continue
                elif not isinstance(v, bool):
                    errors[f'automation.{k}'] = INVALID
                    continue
                data['automation'][k] = v
    unknown = set(body) - {'naming', 'upgrades', 'automation'}
    if unknown:
        errors['_'] = INVALID
    return errors


def series_folder(title: Any, year: Any, data: Optional[Dict[str, Any]] = None, tmdb_id: Any = None) -> str:
    """Folder name for a new series from the naming setting ("Title (Year)"
    by default). An empty year drops its parentheses. Safe as one path part."""
    from app.file_management import safe_component
    tpl = (data or load())['naming']['series_folder']
    y = str(year) if isinstance(year, (str, int)) and re.fullmatch(r'\d{4}', str(year)) else ''
    if not y:
        tpl = re.sub(r'\s*[(\[]\{year\}[)\]]', '', tpl).replace('{year}', '')
    t = title.replace('{', '{{').replace('}', '}}') if isinstance(title, str) else ''
    out = tpl.replace('{title}', t).format(year=y, tmdb_id=tmdb_id if isinstance(tmdb_id, int) else '')
    return safe_component(out, fallback='Series')


def view(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d = data if data is not None else load()
    return {k: copy.deepcopy(d[k]) for k in ('naming', 'upgrades', 'automation', 'rescan_interval_h')}


# --------------------------------------------------------------------------- routes

def init(*, owner_or_error: Callable, store_path: Optional[str] = None) -> None:
    _cfg.update(owner_or_error=owner_or_error)
    if store_path:
        _cfg['store_path'] = store_path


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _guard():
    from app import minimal
    if not minimal.enabled():
        return jsonify({'error': 'Not found'}), 404
    _user, err = _cfg['owner_or_error']()
    return err


@bp.route('/api/settings/tv', methods=['GET'])
def tv_settings_get():
    err = _guard()
    if err:
        return err
    return _no_store(jsonify(view()))


@bp.route('/api/settings/tv', methods=['PUT'])
def tv_settings_put():
    err = _guard()
    if err:
        return err
    with _lock:
        data = load()
        errors = apply_put(data, request.get_json(silent=True))
        if errors:
            return _no_store(jsonify({'errors': errors}), 400)
        save(data)
    return _no_store(jsonify(view(data)))
