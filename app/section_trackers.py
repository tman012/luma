"""Per-section tracker lists (TV now, Movies later): which of the trackers in
Settings > Trackers a section searches, and in what order.

Store: section_trackers.json in the data dir (0600)
    {"version": 1, "sections": {"tv": {"order": [ids], "enabled": [ids]},
                                "movies": {"order": [], "enabled": []}}}

`enabled` is an allow-list: nothing is searched for a section until the owner
turns a tracker on for it. A tracker that isn't in `order` yet (added in
Settings > Trackers since) is listed last, switched off. A deleted tracker's
id is dropped (forget()). A section searches a tracker only when it is on
here, on in Settings > Trackers, and set up for the section's category.

GET /api/settings/sections/<section>/trackers   the list, in order
PUT /api/settings/sections/<section>/trackers   {"trackers": [{"id", "enabled"}, ...]} in order

Owner only, luma-test (minimal) builds only.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request

from app import paths as _paths

logger = logging.getLogger(__name__)

bp = Blueprint('section_trackers', __name__)
_cfg: Dict[str, Any] = {}
_lock = threading.RLock()

STORE_NAME = 'section_trackers.json'
SECTIONS = ('tv', 'movies')
CATEGORY = {'tv': 5000, 'movies': 2000}
MAX_IDS = 200
INVALID = 'Invalid'
UNKNOWN = 'Unknown tracker'


# --------------------------------------------------------------------------- store

def empty() -> Dict[str, Any]:
    return {'version': 1, 'sections': {s: {'order': [], 'enabled': []} for s in SECTIONS}}


def store_path() -> Path:
    return Path(_cfg.get('store_path') or _paths.data_path(STORE_NAME))


def _ids(raw: Any) -> List[int]:
    out: List[int] = []
    if not isinstance(raw, list):
        return out
    for x in raw[:MAX_IDS]:
        if isinstance(x, int) and not isinstance(x, bool) and x > 0 and x not in out:
            out.append(x)
    return out


def _normalize(raw: Any) -> Dict[str, Any]:
    d = empty()
    secs = raw.get('sections') if isinstance(raw, dict) else None
    if not isinstance(secs, dict):
        return d
    for s in SECTIONS:
        v = secs.get(s)
        if isinstance(v, dict):
            order = _ids(v.get('order'))
            d['sections'][s] = {'order': order, 'enabled': [i for i in _ids(v.get('enabled')) if i in order]}
    return d


def load() -> Dict[str, Any]:
    p = store_path()
    with _lock:
        try:
            raw = json.loads(p.read_text())
        except FileNotFoundError:
            return empty()
        except (OSError, ValueError) as e:
            logger.error('Section trackers store unreadable (%s); starting empty', type(e).__name__)
            _paths.quarantine(p, 'unreadable', logger)
            return empty()
        return _normalize(raw)


def save(data: Dict[str, Any]) -> None:
    with _lock:
        _paths.atomic_write_json(store_path(), _normalize(data), mode=0o600)


def forget(tracker_id: Any) -> None:
    """A tracker was deleted: drop it from every section."""
    with _lock:
        data = load()
        changed = False
        for sec in data['sections'].values():
            for k in ('order', 'enabled'):
                if tracker_id in sec[k]:
                    sec[k].remove(tracker_id)
                    changed = True
        if changed:
            save(data)


# --------------------------------------------------------------------------- views

def _records() -> List[Dict[str, Any]]:
    get = _cfg.get('list_trackers')
    if get:
        return list(get())
    from app.tracker_store import get_store
    return get_store().list()


def has_category(rec: Dict[str, Any], section: str) -> bool:
    from app.tracker_search import _match_categories
    return bool(_match_categories({'type': rec.get('type'), 'categories': rec.get('categories') or []},
                                  [CATEGORY[section]]))


def arrange(section: str, records: Optional[List[Dict[str, Any]]] = None,
            data: Optional[Dict[str, Any]] = None, budget: Optional[Callable] = None) -> List[Dict[str, Any]]:
    """Every tracker, in the section's order (new ones last, by their global
    priority), with its switch for the section and why it can't be searched."""
    from app import tracker_types as tt
    recs = _records() if records is None else records
    sec = (data or load())['sections'][section]
    by_id = {r['id']: r for r in recs if isinstance(r.get('id'), int)}
    rank = {i: n for n, i in enumerate(sec['order'])}
    known = [i for i in sec['order'] if i in by_id]
    rest = sorted((i for i in by_id if i not in rank),
                  key=lambda i: (by_id[i].get('priority', tt.PRIORITY_DEFAULT), str(by_id[i].get('name', '')).lower()))
    out = []
    for i in known + rest:
        r = by_id[i]
        t = tt.get_type(r.get('type')) or {}
        row = {'id': i, 'name': r.get('name', ''), 'type_name': t.get('name', r.get('type', '')),
               'enabled': i in sec['enabled'], 'global_enabled': bool(r.get('enabled', True)),
               'has_category': has_category(r, section)}
        if budget:
            row['budget'] = budget(r)
        out.append(row)
    return out


def ordered_ids(section: str, records: Optional[List[Dict[str, Any]]] = None) -> List[int]:
    """Tracker ids the section searches, in order: on here, on globally and
    set up for the section's category."""
    return [r['id'] for r in arrange(section, records)
            if r['enabled'] and r['global_enabled'] and r['has_category']]


def _budget_for(section: str) -> Optional[Callable]:
    if section != 'tv':
        return None

    def budget(rec):
        try:
            from app import tv_budget
            return tv_budget.usage({'type': rec.get('type'), 'url': rec.get('url', '')})
        except Exception:
            return None
    return budget


def check_put(section: str, body: Any, records: List[Dict[str, Any]]):
    """(new section record, None) or (None, error) for a PUT body."""
    items = body.get('trackers') if isinstance(body, dict) else None
    if not isinstance(items, list) or len(items) > MAX_IDS:
        return None, INVALID
    valid = {r['id'] for r in records if isinstance(r.get('id'), int)}
    order: List[int] = []
    enabled: List[int] = []
    for it in items:
        if not isinstance(it, dict):
            return None, INVALID
        i, on = it.get('id'), it.get('enabled')
        if not isinstance(i, int) or isinstance(i, bool) or not isinstance(on, bool):
            return None, INVALID
        if i not in valid:
            return None, UNKNOWN
        if i in order:
            continue                      # a duplicate: the first one counts
        order.append(i)
        if on:
            enabled.append(i)
    return {'order': order, 'enabled': enabled}, None


# --------------------------------------------------------------------------- routes

def init(*, owner_or_error: Callable, store_path: Optional[str] = None,
         list_trackers: Optional[Callable] = None) -> None:
    _cfg.update(owner_or_error=owner_or_error)
    if store_path:
        _cfg['store_path'] = store_path
    if list_trackers:
        _cfg['list_trackers'] = list_trackers


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _guard(section: str):
    from app import minimal
    if not minimal.enabled() or section not in SECTIONS:
        return jsonify({'error': 'Not found'}), 404
    _user, err = _cfg['owner_or_error']()
    return err


def _view(section: str, data: Optional[Dict[str, Any]] = None, records=None):
    return {'section': section, 'trackers': arrange(section, records, data, _budget_for(section))}


@bp.route('/api/settings/sections/<section>/trackers', methods=['GET'])
def section_trackers_get(section):
    err = _guard(section)
    if err:
        return err
    return _no_store(jsonify(_view(section)))


@bp.route('/api/settings/sections/<section>/trackers', methods=['PUT'])
def section_trackers_put(section):
    err = _guard(section)
    if err:
        return err
    records = _records()
    new, error = check_put(section, request.get_json(silent=True), records)
    if error:
        return _no_store(jsonify({'error': error}), 400)
    with _lock:
        data = load()
        # Trackers the client didn't list (added meanwhile) keep their place, off.
        old = data['sections'][section]
        tail = [i for i in old['order'] if i not in new['order']]
        data['sections'][section] = {'order': new['order'] + tail,
                                     'enabled': new['enabled']}
        save(data)
    logger.info('Section %s trackers saved (%d on)', section, len(new['enabled']))
    return _no_store(jsonify(_view(section, data, records)))
