"""Settings > TV monitoring (v158a). Owner only, luma-test builds only (tv_settings._guard).

GET    /api/settings/tv/shows/<id>                       the Edit series sheet + detail toggles (series_view)
GET    /api/settings/tv/shows/by-tmdb/<tmdb_id>          the same, for the TV detail modal
PUT    /api/settings/tv/shows/<id>                       Save {monitored, monitor, monitor_new_seasons, monitor_specials,
                                                         season_folder, series_type, profile_id, path}
DELETE /api/settings/tv/shows/<id>?delete_files=1        remove from Luma; files only for a signed-in owner (and the
                                                         instance gate: off on LUMA_INSTANCE=test unless opted in)
PUT    /api/settings/tv/shows/<id>/monitored {monitored}
PUT    /api/settings/tv/shows/<id>/seasons/<n>/monitored {monitored}
PUT    /api/settings/tv/episodes/<id>/monitored {monitored}
POST   /api/settings/tv/shows/monitor-missing {ids}      Series grid bulk opt-in to Missing episodes
GET    /api/settings/tv/profiles                         {profiles, qualities}
POST   /api/settings/tv/grab {download_url, title, tmdb_id, season, episode?, size?, quality?, indexer?}
                                                         the TV Sources popup's grab (v158c), Luma's own path
POST   /api/settings/tv/profiles  PUT|DELETE /api/settings/tv/profiles/<id>
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app import instance, tv_monitor, tv_settings, tv_store

bp = Blueprint('tv_monitor', __name__)
MAX_BULK = 2000


def _out(body, status=200):
    resp = jsonify(body)
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _flag(body):
    v = body.get('monitored') if isinstance(body, dict) else None
    return v if isinstance(v, bool) else None


def _missing():
    return _out({'error': 'Not found'}, 404)


@bp.route('/api/settings/tv/shows/<int:sid>', methods=['GET'])
def show_get(sid):
    err = tv_settings._guard()
    if err:
        return err
    v = tv_monitor.series_view(sid)
    return _out(v) if v else _missing()


@bp.route('/api/settings/tv/shows/by-tmdb/<int:tmdb_id>', methods=['GET'])
def show_by_tmdb(tmdb_id):
    err = tv_settings._guard()
    if err:
        return err
    with tv_store.connect() as conn:
        r = conn.execute('SELECT id FROM series WHERE tmdb_id = ? AND path_id IS NOT NULL', (tmdb_id,)).fetchone()
    v = tv_monitor.series_view(r['id']) if r else None
    return _out(v) if v else _missing()


@bp.route('/api/settings/tv/shows/<int:sid>', methods=['PUT'])
def show_put(sid):
    err = tv_settings._guard() or tv_settings._signed_in_owner()     # an Edit (path, monitoring): the signed-in owner
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _out({'error': 'Invalid', 'errors': {}}, 400)
    try:
        return _out(tv_monitor.edit(sid, body))
    except tv_monitor.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    except KeyError:
        return _missing()


@bp.route('/api/settings/tv/shows/<int:sid>', methods=['DELETE'])
def show_delete(sid):
    err = tv_settings._guard()
    if err:
        return err
    files = request.args.get('delete_files') in ('1', 'true')
    if files:
        err = tv_settings._signed_in_owner()          # deleting media needs the signed-in owner
        if err:
            return err
        if not instance.writes_allowed('tv_delete_files'):
            return _out({'error': 'Off on the test instance', 'job': 'tv_delete_files'}, 403)
    try:
        return _out(tv_monitor.delete(sid, files))
    except tv_monitor.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    except KeyError:
        return _missing()


def _toggle(fn):
    err = tv_settings._guard()
    if err:
        return err
    on = _flag(request.get_json(silent=True))
    if on is None:
        return _out({'error': 'Invalid'}, 400)
    with tv_store.write() as conn:
        sid = fn(conn, on)
    if sid is None:
        return _missing()
    return _out(tv_monitor.series_view(sid))


@bp.route('/api/settings/tv/shows/<int:sid>/monitored', methods=['PUT'])
def series_monitored(sid):
    def go(conn, on):
        if not conn.execute('SELECT 1 FROM series WHERE id = ?', (sid,)).fetchone():
            return None
        tv_monitor.set_series(conn, sid, on)
        return sid
    return _toggle(go)


@bp.route('/api/settings/tv/shows/<int:sid>/seasons/<int:n>/monitored', methods=['PUT'])
def season_monitored(sid, n):
    def go(conn, on):
        if not conn.execute('SELECT 1 FROM seasons WHERE series_id = ? AND season_number = ?', (sid, n)).fetchone():
            return None
        tv_monitor.set_season(conn, sid, n, on)
        return sid
    return _toggle(go)


@bp.route('/api/settings/tv/episodes/<int:eid>/monitored', methods=['PUT'])
def episode_monitored(eid):
    def go(conn, on):
        r = conn.execute('SELECT series_id FROM episodes WHERE id = ?', (eid,)).fetchone()
        if r is None:
            return None
        tv_monitor.set_episodes(conn, r['series_id'], [eid], on)
        return r['series_id']
    return _toggle(go)


@bp.route('/api/settings/tv/shows/monitor-missing', methods=['POST'])
def monitor_missing():
    err = tv_settings._guard()
    if err:
        return err
    body = request.get_json(silent=True)
    ids = body.get('ids') if isinstance(body, dict) else None
    if (not isinstance(ids, list) or not ids or len(ids) > MAX_BULK
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
        return _out({'error': 'Invalid'}, 400)
    return _out({'ok': True, 'updated': tv_monitor.bulk_missing(ids)})


@bp.route('/api/settings/tv/profiles', methods=['GET'])
def profiles_get():
    err = tv_settings._guard()
    if err:
        return err
    return _out({'profiles': tv_monitor.profiles(), 'qualities': tv_monitor.qualities()})


def _save(pid=None):
    err = tv_settings._guard()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _out({'error': 'Invalid', 'errors': {}}, 400)
    try:
        return _out(tv_monitor.save_profile(body, pid), 201 if pid is None else 200)
    except tv_monitor.Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    except KeyError:
        return _missing()


@bp.route('/api/settings/tv/profiles', methods=['POST'])
def profile_post():
    return _save()


@bp.route('/api/settings/tv/profiles/<int:pid>', methods=['PUT'])
def profile_put(pid):
    return _save(pid)


@bp.route('/api/settings/tv/profiles/<int:pid>', methods=['DELETE'])
def profile_delete(pid):
    err = tv_settings._guard()
    if err:
        return err
    try:
        tv_monitor.delete_profile(pid)
    except tv_monitor.Invalid as e:
        return _out({'error': 'In use', 'errors': e.errors}, 409)
    except KeyError:
        return _missing()
    return _out({'ok': True})


# ------------------------------------------------------------------ v158b search and downloads

@bp.route('/api/settings/tv/shows/<int:sid>/search', methods=['POST'])
def show_search(sid):
    """Search for an episode ({episode_id}) or a season ({season}); grabs what it picks (when grabs
    are allowed on this instance). Interactive tracker budget."""
    err = tv_settings._guard() or tv_settings._signed_in_owner()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _out({'error': 'Invalid'}, 400)
    season, eid = body.get('season'), body.get('episode_id')
    ok_int = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0  # noqa: E731
    if (season is None) == (eid is None) or (season is not None and not ok_int(season)) or (eid is not None and not ok_int(eid)):
        return _out({'error': 'Invalid'}, 400)
    from app import tv_search
    out = tv_search.manual(sid, season=season, episode_id=eid)
    if out.get('error') == 'Not found':
        return _missing()
    return _out(out)


@bp.route('/api/settings/tv/search/status', methods=['GET'])
def search_status():
    err = tv_settings._guard()
    if err:
        return err
    from app import tv_search
    return _out(tv_search.status())


@bp.route('/api/settings/tv/downloads', methods=['GET'])
def downloads_get():
    err = tv_settings._guard()
    if err:
        return err
    from app import tv_downloads
    return _out({'failed': tv_downloads.failures(), 'category': tv_downloads.category()})


@bp.route('/api/settings/tv/downloads/<h>/retry', methods=['POST'])
def downloads_retry(h):
    err = tv_settings._guard() or tv_settings._signed_in_owner()
    if err:
        return err
    from app import tv_downloads
    if not tv_downloads.retry(h):
        return _missing()
    return _out({'ok': True})


# ------------------------------------------------------------------ v158c: the TV Sources popup grab

SHOW_NOT_ADDED = 'Add this show to TV first'


def _series_for_tmdb(tmdb_id: int, now: float):
    """(series_id, error). A show not in Luma's TV yet is added (monitoring none) when there's one
    TV library; with several, the owner picks one by adding the show first."""
    with tv_store.connect() as conn:
        r = conn.execute('SELECT id FROM series WHERE tmdb_id = ? AND path_id IS NOT NULL', (tmdb_id,)).fetchone()
    if r:
        return r['id'], None
    from app import tv_calendar
    out = tv_calendar.add(tmdb_id, None, now=now, monitor='none')
    if out.get('ok'):
        return out['series_id'], None
    if out.get('series_id'):
        return out['series_id'], None
    return None, out.get('error') or SHOW_NOT_ADDED


@bp.route('/api/settings/tv/grab', methods=['POST'])
def popup_grab():
    """Grab one release from the TV Sources popup through Luma's own path (tv_grab: the TV category
    and tag; on test the instance guard). {ok} or {error, fallback}: fallback true when Sonarr may
    still take it (the link or the download client failed, or the show isn't in Luma's TV)."""
    import time as _time
    err = tv_settings._guard() or tv_settings._signed_in_owner()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _out({'error': 'Invalid'}, 400)
    ok_int = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0  # noqa: E731
    tmdb_id, season, episode = body.get('tmdb_id'), body.get('season'), body.get('episode')
    url, title = body.get('download_url'), body.get('title')
    if not ok_int(tmdb_id) or not tmdb_id or not ok_int(season) or (episode is not None and not ok_int(episode)) \
            or not isinstance(url, str) or not url.strip() or len(url) > 8192 or not isinstance(title, str) or len(title) > 1000:
        return _out({'error': 'Invalid'}, 400)
    from app import tv_grab
    if not tv_grab.allowed():
        return _out({'ok': False, 'error': tv_grab.OFF, 'blocked': True, 'fallback': False})
    now = _time.time()
    sid, why = _series_for_tmdb(tmdb_id, now)
    if sid is None:
        return _out({'ok': False, 'error': why, 'fallback': True})
    with tv_store.connect() as conn:
        if episode is None:
            ids = [r['id'] for r in conn.execute('SELECT id FROM episodes WHERE series_id = ? AND season_number = ?',
                                                 (sid, season))]
        else:
            ids = [r['id'] for r in conn.execute('SELECT id FROM episodes WHERE series_id = ? AND season_number = ?'
                                                 ' AND episode_number = ?', (sid, season, episode))]
    size = body.get('size')
    item = {'title': title, 'downloadUrl': url, 'indexer': str(body.get('indexer') or '')[:200] or None,
            'quality': str(body.get('quality') or '')[:100] or None,
            'size': size if ok_int(size) else 0, 'indexerId': None}
    g = tv_grab.grab(item, sid, season, ids, 'manual', now)
    if g.get('ok'):
        return _out({'ok': True, 'series_id': sid, 'existing': bool(g.get('existing'))})
    return _out({'ok': False, 'error': g.get('error'), 'blocked': bool(g.get('blocked')),
                 'fallback': bool(g.get('link') or g.get('client'))})
