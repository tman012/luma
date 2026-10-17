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

GET /api/settings/tv     the settings (with the TV library paths)
PUT /api/settings/tv     {"naming": {...}, "upgrades": {...}, "automation": {...}} (any subset)
POST   /api/settings/tv/paths        add a TV library path {name, path}
PUT    /api/settings/tv/paths/<id>   rename or repoint it
DELETE /api/settings/tv/paths/<id>   409 while a rule or the fallback uses it; its
                                     scanned shows leave tv.db (files are never touched)

TV library paths get the same checks as Library paths (file_management), and
overlap is checked across both stores and the downloads folder. One
difference: no write probe. Phase 1 only reads TV folders, so nothing is
created in them; a folder Luma can't write to is accepted and shows
"Read-only".

Owner only, luma-test builds only.
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
MAX_PATHS = 20
_TV_ID_RE = re.compile(r'^t_[0-9a-f]{8}$')
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
# Number fields; the rest are text, and a format spec ('{title:02}') on text
# would raise at naming time, so it is refused when the template is saved.
INT_FIELDS = {'season', 'episode', 'year', 'tmdb_id'}
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
    characters; '{season:02}'-style zero padding only, and only on number
    fields."""
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
        if spec and field not in INT_FIELDS:
            return None, INVALID
        used.add(field)
    if not REQUIRED_FIELDS[key] <= used:
        return None, INVALID
    return v, None


def _normalize(raw: Any) -> Dict[str, Any]:
    d = empty()
    if not isinstance(raw, dict):
        return d
    if isinstance(raw.get('rules'), list):
        d['rules'] = [x for x in raw['rules'] if isinstance(x, dict)]
    if isinstance(raw.get('paths'), list):
        d['paths'] = [{'id': x['id'], 'name': x['name'], 'path': x['path']} for x in raw['paths']
                      if isinstance(x, dict) and isinstance(x.get('id'), str) and _TV_ID_RE.match(x['id'])
                      and isinstance(x.get('name'), str) and isinstance(x.get('path'), str)
                      and x['path'].startswith('/')][:MAX_PATHS]
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
    y = int(year) if isinstance(year, (str, int)) and not isinstance(year, bool) \
        and re.fullmatch(r'\d{4}', str(year)) else None
    tid = tmdb_id if isinstance(tmdb_id, int) and not isinstance(tmdb_id, bool) and tmdb_id > 0 else None
    for name, val in (('year', y), ('tmdb_id', tid)):
        if val is None:                   # drop the field, with its brackets
            tpl = re.sub(r'\s*(?:\{\{|[(\[])[A-Za-z_-]*\{%s(?::[^}]*)?\}(?:\}\}|[)\]])' % name, '', tpl)
            tpl = re.sub(r'\{%s(?::[^}]*)?\}' % name, '', tpl)
    t = title.replace('{', '{{').replace('}', '}}') if isinstance(title, str) else ''
    try:
        out = tpl.replace('{title}', t).format(year=y, tmdb_id=tid)
    except (ValueError, KeyError, IndexError):
        out = t
    return safe_component(out, fallback='Series')


def view(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from app import file_management as fm
    d = data if data is not None else load()
    out = {k: copy.deepcopy(d[k]) for k in ('naming', 'upgrades', 'automation', 'rescan_interval_h')}
    out['paths'] = [{**p, 'status': fm.path_status(p)} for p in d['paths']]
    return out


# --------------------------------------------------------------------------- TV library paths

def tv_paths() -> list:
    """The TV library paths (for the Movies side's cross-store overlap check)."""
    return copy.deepcopy(load()['paths'])


def check_tv_path(raw: Any, others: list, current: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(path, None) or (None, error): Library paths' checks against *others*
    (the other TV paths) plus every Movies library path and the downloads
    folder, and the folder picker's rule: inside the browse roots
    (LUMA_BROWSE_ROOTS, else /media). *current* (an edit's saved path) is kept
    even if it predates that rule. No write probe (see the module docstring)."""
    from app import file_management as fm
    p, err = fm.clean_abs_path(raw)
    if err:
        return None, err
    real = fm.os.path.realpath(p)
    if fm.blocked(real):
        return None, fm.NOT_ALLOWED
    if fm.overlaps_downloads(real):
        return None, fm.IN_DOWNLOADS
    if not fm.os.path.isdir(real):
        return None, fm.NOT_FOUND
    unchanged = current is not None and fm.os.path.realpath(current) == real
    if not unchanged and not fm.inside_browse_roots(real):
        return None, fm.NOT_ALLOWED
    for o in list(others) + fm.load()['paths']:
        other = fm.os.path.realpath(o['path'])
        if other == real:
            return None, fm.DUPLICATE
        if fm._under(real, other) or fm._under(other, real):
            return None, fm.OVERLAPS
    return p, None


def _new_tv_id(taken) -> str:
    import secrets
    while True:
        i = f't_{secrets.token_hex(4)}'
        if i not in taken:
            return i


def _forget_path(pid: str) -> None:
    """Drop a removed path's shows and unmatched folders from tv.db (rows only)."""
    from app import tv_store
    try:
        with tv_store.write() as conn:
            conn.execute('DELETE FROM series WHERE path_id = ?', (pid,))
            conn.execute('DELETE FROM unmatched WHERE path_id = ?', (pid,))
    except Exception as e:
        logger.warning('tv.db rows for a removed TV path not dropped: %s', type(e).__name__)


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


def _body() -> Dict[str, Any]:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


@bp.route('/api/settings/tv/paths', methods=['POST'])
def tv_paths_add():
    from app import file_management as fm
    err = _guard()
    if err:
        return err
    body = _body()
    with _lock, fm.lock():
        data = load()
        if len(data['paths']) >= MAX_PATHS:
            return _no_store(jsonify({'errors': {'path': fm.TOO_MANY}}), 400)
        name, e1 = fm.check_name(body.get('name'), data['paths'])
        path, e2 = check_tv_path(body.get('path'), data['paths'])
        errors = {k: v for k, v in (('name', e1), ('path', e2)) if v}
        if errors:
            return _no_store(jsonify({'errors': errors}), 400)
        data['paths'].append({'id': _new_tv_id({p['id'] for p in data['paths']}), 'name': name, 'path': path})
        save(data)
        logger.info('TV library path added: %s', name)
        return _no_store(jsonify(view(data)), 201)


@bp.route('/api/settings/tv/paths/<pid>', methods=['PUT'])
def tv_paths_edit(pid):
    from app import file_management as fm
    err = _guard()
    if err:
        return err
    body = _body()
    with _lock, fm.lock():
        data = load()
        rec = next((p for p in data['paths'] if p['id'] == pid), None)
        if not rec:
            return _no_store(jsonify({'error': 'Not found'}), 404)
        others = [p for p in data['paths'] if p['id'] != pid]
        errors = {}
        name, path = rec['name'], rec['path']
        if 'name' in body:
            name, e = fm.check_name(body.get('name'), others)
            if e:
                errors['name'] = e
        if 'path' in body:
            path, e = check_tv_path(body.get('path'), others, current=rec['path'])
            if e:
                errors['path'] = e
        if errors:
            return _no_store(jsonify({'errors': errors}), 400)
        moved = fm.os.path.realpath(path) != fm.os.path.realpath(rec['path'])
        rec.update(name=name, path=path)
        save(data)
    if moved:
        _forget_path(pid)                 # a new folder: rescan it from scratch
    return _no_store(jsonify(view(data)))


@bp.route('/api/settings/tv/paths/<pid>', methods=['DELETE'])
def tv_paths_delete(pid):
    from app import file_management as fm
    err = _guard()
    if err:
        return err
    with _lock:
        data = load()
        if not any(p['id'] == pid for p in data['paths']):
            return _no_store(jsonify({'error': 'Not found'}), 404)
        if data['fallback_path_id'] == pid or any(r.get('path_id') == pid for r in data['rules']):
            return _no_store(jsonify({'error': fm.IN_USE}), 409)
        data['paths'] = [p for p in data['paths'] if p['id'] != pid]
        save(data)
    _forget_path(pid)
    return _no_store(jsonify(view(data)))


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
