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

register() wires the two routes for a section. claim() records which ids a
section holds; Movies filing (file_management.pick_destination, the implicit
fallback) and the Movies rule and fallback choices leave those out, so Movies
behave as if TV's folders weren't Library paths at all.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

MAX_SELECTED = 20
_claims: Dict[str, Callable[[], List[str]]] = {}


def claim(section: str, get_ids: Callable[[], List[str]]) -> None:
    """*section* holds the Library path ids *get_ids()* returns (never writes)."""
    _claims[section] = get_ids


def claimed() -> Dict[str, List[str]]:
    """{Library path id: [sections that selected it]}. A section whose store
    can't be read claims nothing (its store reads as empty anyway)."""
    out: Dict[str, List[str]] = {}
    for section, fn in sorted(_claims.items()):
        try:
            ids = list(fn())
        except Exception as e:
            logger.warning('Library selection for %s unreadable: %s', section, type(e).__name__)
            continue
        for i in ids:
            out.setdefault(i, []).append(section)
    return out


def claimed_ids() -> set:
    return set(claimed())
INVALID = 'Invalid'
NOT_A_LIBRARY = 'Not a library path'
USED_BY_MOVIES = 'This path is used by Movies'
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


def view(ids: List[str], paths: List[Dict[str, Any]],
         status_of: Optional[Callable[[Dict[str, Any]], str]] = None) -> Dict[str, Any]:
    from app import file_management as fm
    chosen = set(ids)
    status_of = status_of or fm.path_status
    return {'libraries': [{'id': p['id'], 'name': p['name'], 'path': p['path'], 'status': status_of(p),
                           'selected': p['id'] in chosen} for p in paths],
            'selected': [p['id'] for p in paths if p['id'] in chosen]}


def register(bp: Blueprint, url: str, *, endpoint: str,
             get_ids: Callable[[], List[str]], set_ids: Callable[[List[str]], None],
             read_guard: Callable[[], Any], write_guard: Callable[[], Any],
             section_lock: Any, on_change: Optional[Callable[[List[str], List[str]], None]] = None,
             status_of: Optional[Callable[[Dict[str, Any]], str]] = None) -> None:
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
        return _no_store(jsonify(view(get_ids(), fm.load()['paths'], status_of)))

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
            # A path is Movies or TV, never both (PM, M22): a newly selected
            # path can't be a Movies rule destination or the chosen fallback.
            # That includes the implicit fallback, the single path Movies see (F2).
            lib = fm.load()
            if set(ids) - set(before) & fm.movies_uses(lib):
                return _no_store(jsonify({'errors': {'selected': USED_BY_MOVIES}}), 409)
            # Selecting or releasing a path changes which paths Movies see; pin
            # the fallback they file into now so it doesn't move or vanish (F1).
            if before != ids and fm.pin_fallback(lib):
                fm.save(lib)
            set_ids(ids)
        if on_change and before != ids:
            on_change(before, ids)
        return _no_store(jsonify(view(ids, paths, status_of)))

    bp.add_url_rule(url, endpoint=f'{endpoint}_get', view_func=get_view, methods=['GET'])
    bp.add_url_rule(url, endpoint=f'{endpoint}_put', view_func=put_view, methods=['PUT'])
