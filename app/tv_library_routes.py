"""Settings > TV: the scanned library (shows, unmatched folders), Scan.

GET  /api/settings/tv/library   {shows, unmatched, scan: {running, error, last_scan, summary}}
POST /api/settings/tv/scan      start a scan (202), 409 while one runs

Owner only, luma-test builds only (tv_settings._guard).
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from app import tv_scan, tv_settings

bp = Blueprint('tv_library', __name__)
SCAN_RUNNING = 'A scan is already running'


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


@bp.route('/api/settings/tv/scan', methods=['POST'])
def tv_scan_start():
    err = tv_settings._guard()
    if err:
        return err
    if not tv_scan.start(retry_unmatched=True):
        return _no_store(jsonify({'error': SCAN_RUNNING, 'scan': tv_scan.status()}), 409)
    return _no_store(jsonify({'scan': tv_scan.status()}), 202)
