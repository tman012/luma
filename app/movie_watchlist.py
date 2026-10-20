"""v202 the native movie watchlist (piece 2): Add to watchlist / In watchlist on the test instance.

Store: the watchlist table in movies.db (movie_settings). Filled only from the owner's adds on the test
install; never seeded from, read from or written to Radarr. Off (404) unless LUMA_INSTANCE is test, so
prod keeps its Radarr watchlist unchanged.

GET    /api/movies/watchlist/tmdb-ids      {tmdb_ids}
GET    /api/movies/watchlist                {movies: [{tmdb_id, title, year, profile_id, added_at}]}
POST   /api/movies/watchlist                {tmdb_id, title, year?, profile_id?}  (default profile when omitted)
DELETE /api/movies/watchlist/<tmdb_id>
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from flask import Blueprint, request

from app import instance, movie_settings as ms

bp = Blueprint('movie_watchlist', __name__)
MAX_ID = 10 ** 9


def _off():
    if not instance.is_test():
        return ms._out({'error': 'Not found'}, 404)
    return None


def _guard(write: bool = False):
    return _off() or ms._guard(write)


def tmdb_ids() -> List[int]:
    with ms.connect() as conn:
        return [r['tmdb_id'] for r in conn.execute('SELECT tmdb_id FROM watchlist ORDER BY added_at')]


def movies() -> List[Dict[str, Any]]:
    with ms.connect() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM watchlist ORDER BY added_at DESC')]


def _int(v: Any, lo: int = 1, hi: int = MAX_ID) -> Optional[int]:
    return v if type(v) is int and lo <= v <= hi else None


def add(body: Any, now: Optional[float] = None) -> Dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {'tmdb_id', 'title', 'year', 'profile_id'}:
        raise ms.Invalid({'_': 'Invalid'})
    errors: Dict[str, str] = {}
    tid = _int(body.get('tmdb_id'))
    title = body.get('title')
    year = body.get('year')
    if tid is None:
        errors['tmdb_id'] = 'Invalid'
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        errors['title'] = 'Invalid'
    if year is not None and _int(year, 1870, 2200) is None:
        errors['year'] = 'Invalid'
    with ms.connect() as conn:
        pid = body.get('profile_id')
        if pid is None:
            r = conn.execute('SELECT id FROM profiles WHERE is_default = 1').fetchone()
            pid = r['id'] if r else None
        elif _int(pid) is None or not conn.execute('SELECT 1 FROM profiles WHERE id = ?', (pid,)).fetchone():
            errors['profile_id'] = 'Invalid'
        if errors:
            raise ms.Invalid(errors)
        conn.execute('INSERT INTO watchlist (tmdb_id, title, year, profile_id, added_at) VALUES (?, ?, ?, ?, ?)'
                     ' ON CONFLICT(tmdb_id) DO UPDATE SET profile_id = excluded.profile_id',
                     (tid, title.strip(), year, pid, time.time() if now is None else now))
        return dict(conn.execute('SELECT * FROM watchlist WHERE tmdb_id = ?', (tid,)).fetchone())


def remove(tid: int) -> bool:
    with ms.connect() as conn:
        return conn.execute('DELETE FROM watchlist WHERE tmdb_id = ?', (tid,)).rowcount > 0


@bp.route('/api/movies/watchlist/tmdb-ids', methods=['GET'])
def ids_get():
    return _guard() or ms._out({'tmdb_ids': tmdb_ids()})


@bp.route('/api/movies/watchlist/profiles', methods=['GET'])
def profiles_get():
    """v204: the Add to watchlist picker (prod's MobileUpgradeSheet) lists these instead of Radarr's."""
    return _guard() or ms._out([{'id': p['id'], 'name': p['name']} for p in ms.picker_profiles()])


@bp.route('/api/movies/watchlist', methods=['GET'])
def list_get():
    return _guard() or ms._out({'movies': movies()})


@bp.route('/api/movies/watchlist', methods=['POST'])
def add_post():
    err = _guard(write=True)
    if err:
        return err
    try:
        return ms._out({'success': True, 'movie': add(request.get_json(silent=True))}, 201)
    except ms.Invalid as e:
        return ms._out({'success': False, 'error': 'Invalid', 'errors': e.errors}, 400)


@bp.route('/api/movies/watchlist/<int:tid>', methods=['DELETE'])
def remove_delete(tid: int):
    err = _guard(write=True)
    if err:
        return err
    if not remove(tid):
        return ms._out({'success': False, 'error': 'Not found'}, 404)
    return ms._out({'success': True})


# ------------------------------------------------------------------ v203 the movie page's searches (test only)

def _search_out(r):
    st = int(r.pop('status', 200) or 200)
    return ms._out(r, st)


@bp.route('/api/movies/watchlist/<int:tid>/search', methods=['POST'])
def search_post(tid: int):
    from app import movie_scanner
    return _guard(write=True) or _search_out(movie_scanner.manual(tid))


@bp.route('/api/movies/watchlist/<int:tid>/interactive', methods=['POST'])
def interactive_post(tid: int):
    from app import movie_scanner
    return _guard(write=True) or _search_out(movie_scanner.interactive(tid))


@bp.route('/api/movies/watchlist/<int:tid>/interactive/grab', methods=['POST'])
def interactive_grab_post(tid: int):
    from app import movie_scanner
    err = _guard(write=True)
    if err:
        return err
    r = movie_scanner.grab_release(tid, request.get_json(silent=True))
    if not r.get('ok') and 'status' not in r:
        r['status'] = 200                      # a refused grab is an answer, like TV's
    return _search_out(r)
