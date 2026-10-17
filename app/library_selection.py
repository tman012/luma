"""Library paths picked for a section: TV now, Radarr-style Movies later.

Folders are only ever added in Settings > Library paths (app/file_management).
A section keeps a list of references to those paths (their ids), never paths
of its own:

    GET  <url>   {"libraries": [{id, name, path, status, selected}], "selected": [ids]}
    PUT  <url>   {"selected": [ids]}   -> the same view; 400 {"errors": {"selected": ...}}

Every id must be a current Library path (checked under the Library paths lock,
so a path removed at the same moment can't be selected). Reads follow the
section's own guard; changes need the signed-in owner (401 with
"sign_in": true, 403 "Owner only"), the same as other owner-only changes.

When a Library path is removed, every section's selection drops it
(file_management.on_path_removed); the section keeps whatever it learned
about that folder (TV keeps its tv.db rows) so selecting it again restores it.

register() wires the two routes for a section; it holds no state itself.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

MAX_SELECTED = 20
INVALID = 'Invalid'
NOT_A_LIBRARY = 'Not a library path'
TOO_MANY = 'Too many'


def check_selection(raw: Any, paths: List[Dict[str, Any]]) -> Tuple[Optional[List[str]], Optional[str]]:
    """(ids in Library paths order, None) or (None, error). Duplicates count once."""
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        return None, INVALID
    known = [p['id'] for p in paths]
    if any(x not in known for x in raw):
        return None, NOT_A_LIBRARY
    chosen = set(raw)
    if len(chosen) > MAX_SELECTED:
        return None, TOO_MANY
    return [i for i in known if i in chosen], None


def resolve(ids: List[str], paths: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The selected Library path records ({id, name, path}) that still exist."""
    chosen = set(ids)
    return [dict(p) for p in paths if p['id'] in chosen]


def view(ids: List[str], paths: List[Dict[str, Any]]) -> Dict[str, Any]:
    from app import file_management as fm
    chosen = set(ids)
    return {'libraries': [{'id': p['id'], 'name': p['name'], 'path': p['path'], 'status': fm.path_status(p),
                           'selected': p['id'] in chosen} for p in paths],
            'selected': [p['id'] for p in paths if p['id'] in chosen]}


def register(bp: Blueprint, url: str, *, endpoint: str,
             get_ids: Callable[[], List[str]], set_ids: Callable[[List[str]], None],
             read_guard: Callable[[], Any], write_guard: Callable[[], Any],
             section_lock: Any, on_change: Optional[Callable[[List[str], List[str]], None]] = None) -> None:
    """GET/PUT *url* on *bp* for one section's selection. *read_guard* (GET)
    and *write_guard* (PUT) return None or an error response ((body, status)
    or a response). *on_change(before, after)* runs after a save that changed
    the selection."""
    from app import file_management as fm

    def _no_store(resp, status=200):
        resp.status_code = status
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    def _err(err):
        if isinstance(err, tuple):
            resp, status = err
            return _no_store(resp, status)
        return err

    def get_view():
        err = read_guard()
        if err:
            return err
        return _no_store(jsonify(view(get_ids(), fm.load()['paths'])))

    def put_view():
        err = write_guard()
        if err:
            return _err(err)
        body = request.get_json(silent=True)
        raw = body.get('selected') if isinstance(body, dict) and set(body) == {'selected'} else None
        with section_lock, fm.lock():
            paths = fm.load()['paths']
            ids, e = check_selection(raw, paths)
            if e:
                return _no_store(jsonify({'errors': {'selected': e}}), 400)
            before = get_ids()
            set_ids(ids)
        if on_change and before != ids:
            on_change(before, ids)
        return _no_store(jsonify(view(ids, paths)))

    bp.add_url_rule(url, endpoint=f'{endpoint}_get', view_func=get_view, methods=['GET'])
    bp.add_url_rule(url, endpoint=f'{endpoint}_put', view_func=put_view, methods=['PUT'])
