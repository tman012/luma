"""Settings > TV: the scanned library (shows, review rows), Import.

GET  /api/settings/tv/library   {shows, unmatched (review rows: tier, pick), scan: {running, error, last_scan, summary}}
POST /api/settings/tv/import    start an import (202), 409 while one runs (/scan is the same)
GET  /api/settings/tv/import    the small progress the Import card polls
POST /api/settings/tv/import-all {items: [{id, tmdb_id}]}  review rows imported as the owner's picks
GET  /api/settings/tv/search?q=&year=          TMDB shows for the picker (q may be tmdb:/tvdb:/imdb:<id>)
GET  /api/settings/tv/calendar?start=YYYY-MM-DD&days=N   episodes by air date (tv_calendar)
GET  /api/settings/tv/find?q=                   Search: library shows first, then TMDB
POST /api/settings/tv/shows {tmdb_id, path_id?}  Add from Search (tv.db only; no folders, no files)
PUT  /api/settings/tv/shows/<id>/match {tmdb_id}      "Wrong show?" (rematched)
POST /api/settings/tv/unmatched/<id>/match {tmdb_id}  "Match" (matched, manual)

Owner only, luma-test builds only (tv_settings._guard).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app import tv_calendar, tv_scan, tv_settings, tv_tmdb

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


BAD_DATE = 'Pick a date'


@bp.route('/api/settings/tv/calendar', methods=['GET'])
def tv_calendar_get():
    err = tv_settings._guard()
    if err:
        return err
    start = tv_calendar.parse_day(request.args.get('start'))
    days = request.args.get('days') or '7'
    if start is None or not days.isdigit() or not 1 <= int(days) <= tv_calendar.MAX_DAYS:
        return _no_store(jsonify({'error': BAD_DATE}), 400)
    data = tv_calendar.calendar(start, int(days))
    # stale air dates (24h) and never-read stills / air times refresh in the background, this week's shows first
    tv_scan.refresh_on_open({e['series_id'] for e in data['episodes']})
    return _no_store(jsonify(data))


@bp.route('/api/settings/tv/find', methods=['GET'])
def tv_find():
    err = tv_settings._guard()
    if err:
        return err
    q = (request.args.get('q') or '').strip()
    if len(q) < 2 or len(q) > MAX_Q:
        return _no_store(jsonify({'error': BAD_QUERY}), 400)
    try:
        return _no_store(jsonify(tv_calendar.find(q)))
    except tv_tmdb.TmdbError as e:
        return _no_store(jsonify({'error': str(e)}), 502)


@bp.route('/api/settings/tv/shows', methods=['POST'])
def tv_show_add():
    err = tv_settings._guard()
    if err:
        return err
    body = request.get_json(silent=True)
    tid = _tmdb_id(body)
    pid = body.get('path_id') if isinstance(body, dict) else None
    if tid is None or (pid is not None and (not isinstance(pid, str) or len(pid) > 64)):
        return _no_store(jsonify({'error': BAD_SHOW}), 400)
    try:
        out = tv_calendar.add(tid, pid)
    except tv_tmdb.TmdbError as e:
        return _no_store(jsonify({'error': str(e)}), 502)
    if not out['ok']:
        extra = {'series_id': out['series_id']} if out.get('series_id') else {}
        return _no_store(jsonify({'error': out['error'], **extra}), out.get('status', 400))
    return _no_store(jsonify({'ok': True, 'series_id': out['series_id']}), 201)
