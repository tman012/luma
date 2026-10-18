"""Settings > TV: the scanned library (shows, review rows), Import.

GET  /api/settings/tv/library   {shows, unmatched (review rows: tier, pick), scan: {running, error, last_scan, summary}}
POST /api/settings/tv/import    start an import (202), 409 while one runs (/scan is the same)
GET  /api/settings/tv/import    the small progress the Import card polls
POST /api/settings/tv/import-all {items: [{id, tmdb_id}]}  review rows imported as the owner's picks
GET  /api/settings/tv/search?q=&year=          TMDB shows for the picker (q may be tmdb:/tvdb:/imdb:<id>)
PUT  /api/settings/tv/shows/<id>/match {tmdb_id}      "Wrong show?" (rematched)
POST /api/settings/tv/unmatched/<id>/match {tmdb_id}  "Match" (matched, manual)

Owner only, luma-test builds only (tv_settings._guard).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app import tv_scan, tv_settings, tv_tmdb

bp = Blueprint('tv_library', __name__)
SCAN_RUNNING = 'A scan is already running'
BAD_SHOW = 'Pick a show'
BAD_QUERY = 'Enter a show name'
MAX_Q = 200
SEARCH_LIMIT = 10


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.route('/api/settings/tv/library', methods=['GET'])
def tv_library_get():
    err = tv_settings._guard()
    if err:
        return err
    return _no_store(jsonify(tv_scan.library_view()))


@bp.route('/api/settings/tv/import', methods=['GET'])
def tv_import_progress():
    err = tv_settings._guard()
    if err:
        return err
    return _no_store(jsonify(tv_scan.progress()))


@bp.route('/api/settings/tv/import', methods=['POST'])
@bp.route('/api/settings/tv/scan', methods=['POST'])
def tv_scan_start():
    err = tv_settings._guard()
    if err:
        return err
    if not tv_scan.start(retry_unmatched=True):
        return _no_store(jsonify({'error': SCAN_RUNNING, 'scan': tv_scan.status()}), 409)
    return _no_store(jsonify({'scan': tv_scan.status()}), 202)


def _tmdb_id(body):
    v = body.get('tmdb_id') if isinstance(body, dict) else None
    if isinstance(v, bool) or not isinstance(v, int) or not 0 < v < 10 ** 9:
        return None
    return v


def _match_reply(out):
    if out['ok']:
        return _no_store(jsonify({'ok': True, 'series_id': out['series_id'], **tv_scan.library_view()}))
    return _no_store(jsonify({'error': out['error']}), out.get('status', 400))


@bp.route('/api/settings/tv/search', methods=['GET'])
def tv_search():
    err = tv_settings._guard()
    if err:
        return err
    q = (request.args.get('q') or '').strip()
    if not q or len(q) > MAX_Q:
        return _no_store(jsonify({'error': BAD_QUERY}), 400)
    year = request.args.get('year') or ''
    year = int(year) if year.isdigit() and 1900 <= int(year) <= 2100 else None
    try:
        results = tv_scan.lookup(q)
        if results is None:
            results = tv_tmdb.search(q, year)
    except tv_tmdb.TmdbError as e:
        return _no_store(jsonify({'error': str(e)}), 502)
    return _no_store(jsonify({'results': results[:SEARCH_LIMIT]}))


def _matched(fn, item_id):
    err = tv_settings._guard()
    if err:
        return err
    tid = _tmdb_id(request.get_json(silent=True))
    if tid is None:
        return _no_store(jsonify({'error': BAD_SHOW}), 400)
    try:
        return _match_reply(fn(item_id, tid))
    except tv_tmdb.TmdbError as e:
        return _no_store(jsonify({'error': str(e)}), 502)


@bp.route('/api/settings/tv/shows/<int:sid>/match', methods=['PUT'])
def tv_show_rematch(sid):
    return _matched(tv_scan.rematch, sid)


@bp.route('/api/settings/tv/unmatched/<int:uid>/match', methods=['POST'])
def tv_unmatched_match(uid):
    return _matched(tv_scan.match_unmatched, uid)


@bp.route('/api/settings/tv/import-all', methods=['POST'])
def tv_import_all():
    err = tv_settings._guard()
    if err:
        return err
    body = request.get_json(silent=True)
    raw = body.get('items') if isinstance(body, dict) else None
    if not isinstance(raw, list) or not 0 < len(raw) <= tv_scan.IMPORT_ALL_MAX:
        return _no_store(jsonify({'error': BAD_SHOW}), 400)
    items = []
    for it in raw:
        uid = it.get('id') if isinstance(it, dict) else None
        tid = _tmdb_id(it)
        if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0 or tid is None:
            return _no_store(jsonify({'error': BAD_SHOW}), 400)
        items.append((uid, tid))
    out = tv_scan.import_all(items)
    if not out['ok']:
        return _no_store(jsonify({'error': out['error']}), out.get('status', 409))
    return _no_store(jsonify({'imported': out['imported'], 'failed': out['failed'], **tv_scan.library_view()}))
