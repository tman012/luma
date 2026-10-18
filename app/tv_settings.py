"""TV settings for luma-test (minimal) builds: tv_settings.json in the data
dir (0600), separate from file_management.json (whose path map and transfer
mode TV shares).

    {"version": 1,
     "libraries": ["p_..."], "rules": [], "fallback_path_id": null,
     "naming": {"series_folder": "{title} ({year})", ...},
     "upgrades": {"mode": "keep_both" | "replace_verified"},
     "automation": {"enabled": false, ...},
     "rescan_interval_h": 6}

* Libraries: TV has no folders of its own. Folders are added in Settings >
  Library paths; TV keeps the ids of the ones selected as TV libraries
  (app/library_selection). A Library path removed later leaves the selection;
  its shows stay in tv.db, hidden until it is selected again or a scan finds
  them in another selected library. Earlier builds' own TV paths ("paths")
  are moved over once by migrate().
* Naming: a scan accepts any existing folder name; only new series get a
  folder from `series_folder`, which starts at "Title (Year)".
* Upgrades: keep_both (the old file stays and is listed as superseded) until
  the owner picks replace_verified (the old file goes only after the new one
  is in place and verified).
* Automation is off. The tracker caps live in app/tv_budget (code, not here).

GET /api/settings/tv             the settings ("paths": the selected TV libraries)
PUT /api/settings/tv             {"naming": {...}, "upgrades": {...}, "automation": {...}} (any subset)
GET /api/settings/tv/libraries   every Library path, with "selected"
PUT /api/settings/tv/libraries   {"selected": [Library path ids]} (signed-in owner)

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
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from app import file_management as _fm
from app import library_selection
from app import paths as _paths

logger = logging.getLogger(__name__)

bp = Blueprint('tv_settings', __name__)
_cfg: Dict[str, Any] = {}
_lock = threading.RLock()

STORE_NAME = 'tv_settings.json'
MAX_PATHS = 20
_TV_ID_RE = re.compile(r'^t_[0-9a-f]{8}$')      # an earlier build's own TV paths (migrate)
_LIB_ID_RE = re.compile(r'^p_[0-9a-f]{8}$')     # Library path ids (file_management)
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
# import_enabled: the TV download watch on prod (off by default; on test it follows the instance's
# tv_import job, see tv_downloads.watch_enabled).
AUTOMATION_FLAGS = ('enabled', 'feed_enabled', 'backlog_enabled', 'upgrades_enabled', 'import_enabled')
DEFAULT_AUTOMATION = {'enabled': False, 'feed_enabled': False, 'backlog_enabled': False,
                      'upgrades_enabled': False, 'import_enabled': False, 'first_search_delay_h': 4}
TEMPLATE_MAX = 120
INVALID = 'Invalid'
_CTRL_RE = re.compile(r'[\x00-\x1f\x7f/\\]')
_FMT_RE = re.compile(r'^0?\d?$')


def empty() -> Dict[str, Any]:
    return {'version': 1, 'paths': [], 'libraries': [], 'rules': [], 'fallback_path_id': None,
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
    if isinstance(raw.get('libraries'), list):
        seen: List[str] = []
        for x in raw['libraries']:
            if isinstance(x, str) and _LIB_ID_RE.match(x) and x not in seen:
                seen.append(x)
        d['libraries'] = seen[:library_selection.MAX_SELECTED]
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
    # Library paths a migration interrupted mid-way added (only while it runs)
    added = raw.get('migration_added')
    if isinstance(added, list) and d['paths']:
        ids = [x for x in added if isinstance(x, str) and _LIB_ID_RE.match(x)]
        if ids:
            d['migration_added'] = ids[:MAX_PATHS]
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
    """The settings, with the selected TV libraries as 'paths' ({id, name, path, status})."""
    from app import file_management as fm
    d = data if data is not None else load()
    out = {k: copy.deepcopy(d[k]) for k in ('naming', 'upgrades', 'automation', 'rescan_interval_h')}
    out['paths'] = [{**p, 'status': fm.path_status(p)} for p in libraries(migrate_first=True)]
    return out


# --------------------------------------------------------------------------- TV libraries

def _drop_library(pid: str) -> None:
    """A Library path was removed: drop it from the selection. Its shows stay
    in tv.db, hidden while no selected library holds them; a scan that finds
    one of them in a selected library moves it there (tv_scan._resolve_duplicate)."""
    with _lock:
        data = load()
        if pid in data['libraries']:
            data['libraries'] = [i for i in data['libraries'] if i != pid]
            save(data)
            logger.info('TV library dropped: its Library path was removed')


def selected_ids(migrate_first: bool = True) -> List[str]:
    """The selected Library path ids. Settings reads first move an earlier
    build's TV paths over (migrate); the scan never writes, so it passes
    migrate_first=False (main migrates at start)."""
    data = load()
    if data['paths'] and migrate_first:
        migrate()
        data = load()
    return list(data['libraries'])


def libraries(migrate_first: bool = False) -> List[Dict[str, Any]]:
    """The TV libraries: the selected Library paths that still exist ({id, name, path})."""
    return library_selection.resolve(selected_ids(migrate_first), _fm.load()['paths'])


def _same_folder(a: str, b: str) -> bool:
    import os
    return os.path.realpath(os.path.normpath(a)) == os.path.realpath(os.path.normpath(b))


def _free_name(name: str, taken: List[str]) -> str:
    from app import file_management as fm
    used = {t.casefold() for t in taken}
    base = (name or 'TV').strip()[:fm.NAME_MAX] or 'TV'
    for n in [base] + [f'{base[:fm.NAME_MAX - 5]} (TV)'] + [f'{base[:fm.NAME_MAX - 8]} (TV {i})' for i in range(2, 100)]:
        if n.casefold() not in used:
            return n
    return fm._new_id('TV', used)


def _move_rows(old: str, new: str) -> None:
    """Point tv.db rows from an earlier TV path id at its Library path id.
    Rows a second copy of the same folder already holds are dropped (that
    folder is scanned once from now on)."""
    from app import tv_store
    with tv_store.write() as conn:
        for table in ('series', 'unmatched', 'episode_files'):
            conn.execute(f'UPDATE OR IGNORE {table} SET path_id = ? WHERE path_id = ?', (new, old))
            conn.execute(f'DELETE FROM {table} WHERE path_id = ?', (old,))


def migrate() -> Dict[str, str]:
    """Turn an earlier build's own TV paths into Library path selections.
    A TV path whose folder is already a Library path (same folder after
    normalizing) selects it; any other is added to Library paths (name kept,
    "(TV)" added if the name is taken) and selected. tv.db rows follow to the
    Library path's id. Running it again changes nothing. {old id: new id}."""
    from app import file_management as fm
    with _lock, fm.lock():
        data = load()
        if not data['paths']:
            return {}
        lib = fm.load()
        ours = set(data.get('migration_added') or [])          # a rerun after a crash
        movies_before = [p for p in fm.movies_only(lib)['paths'] if p['id'] not in ours]
        # Before adding any path, pin the fallback to the previous effective one
        # (an implicit single-path fallback would otherwise vanish or move).
        pinned = False
        adds = [r for r in data['paths'] if not any(_same_folder(p['path'], r['path']) for p in lib['paths'])]
        # A legacy TV folder that is the only Movies path stays with Movies: it
        # isn't selected for TV (Movies would silently stop); the owner decides (F4).
        movies_only_path = movies_before[0]['id'] if len(movies_before) == 1 else None
        if adds and lib['fallback_path_id'] is None and movies_only_path:
            lib['fallback_path_id'] = movies_only_path
            pinned = True
        added, mapping = 0, {}
        for rec in data['paths']:
            hit = next((p for p in lib['paths'] if _same_folder(p['path'], rec['path'])), None)
            if hit is None:
                # Library paths' checks, without the write probe; never blocks.
                _p, err = fm.check_library_path(rec['path'], lib['paths'], probe=False)
                logger.info('TV path %r added to Library paths (%s)', rec['name'], err or 'checks passed')
                hit = {'id': fm._new_id('p', {p['id'] for p in lib['paths']}),
                       'name': _free_name(rec['name'], [p['name'] for p in lib['paths']]), 'path': rec['path']}
                lib['paths'].append(hit)
                added += 1
            mapping[rec['id']] = hit['id']
        if added or pinned:
            if added:                     # remembered first: a rerun never takes them for Movies' own
                save({**data, 'migration_added': sorted(ours | {mapping[r['id']] for r in adds})})
            fm.save(lib)                  # then: a rerun after a crash matches these
        for old, new in mapping.items():
            if old != new:
                _move_rows(old, new)
        chosen = set(data['libraries']) | (set(mapping.values()) - {movies_only_path})
        data['libraries'] = [p['id'] for p in lib['paths'] if p['id'] in chosen]
        data['rules'] = [dict(r, path_id=mapping.get(r.get('path_id'), r.get('path_id'))) for r in data['rules']]
        if data['fallback_path_id'] in mapping:
            data['fallback_path_id'] = mapping[data['fallback_path_id']]
        data['paths'] = []
        data.pop('migration_added', None)
        save(data)
    logger.info('TV paths moved to Library paths: %d selected, %d added', len(set(mapping.values())), added)
    return mapping


def migrate_on_start() -> None:
    try:
        migrate()
    except Exception as e:            # the next read tries again
        logger.warning('TV paths not moved to Library paths yet: %s', type(e).__name__)


# --------------------------------------------------------------------------- routes

def init(*, owner_or_error: Callable, signed_in_owner: Optional[Callable] = None,
         store_path: Optional[str] = None) -> None:
    _cfg.update(owner_or_error=owner_or_error)
    if signed_in_owner is not None:
        _cfg['signed_in_owner'] = signed_in_owner
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


_fm.on_path_removed(lambda pid: _drop_library(pid))


def _repointed(pid: str) -> None:
    from app import tv_scan
    n = tv_scan.detach_path(pid)
    if n:
        logger.info('Library path %s repointed: %d TV shows hidden until a scan finds them (history kept)', pid, n)


_fm.on_path_repointed(_repointed)
library_selection.claim('tv', lambda: selected_ids(migrate_first=False))   # never Movie destinations


@bp.route('/api/settings/tv', methods=['GET'])
def tv_settings_get():
    err = _guard()
    if err:
        return err
    return _no_store(jsonify(view()))


def _signed_in_owner():
    """None, or the 404 (full builds), 401 (sign_in) or 403 for a change by
    anyone but the signed-in owner."""
    from app import minimal
    if not minimal.enabled():
        return jsonify({'error': 'Not found'}), 404
    fn = _cfg.get('signed_in_owner')
    if fn is None:
        from app import start_flow
        fn = start_flow.require_signed_in_owner
    return fn()[1]


def _set_libraries(ids: List[str]) -> None:
    data = load()
    data['libraries'] = ids
    save(data)


def _libraries_changed(before: List[str], after: List[str]) -> None:
    logger.info('TV libraries: %d selected', len(after))


library_selection.register(bp, '/api/settings/tv/libraries', endpoint='tv_libraries',
                           get_ids=selected_ids, set_ids=_set_libraries, read_guard=_guard,
                           write_guard=_signed_in_owner, section_lock=_lock, on_change=_libraries_changed)


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
