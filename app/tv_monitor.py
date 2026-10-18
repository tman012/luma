"""TV monitoring (v158a, Sonarr-style), tv.db only.

Series:   monitored, monitor (the option last applied), monitor_new_seasons (1 All seasons / 0 None),
          monitor_specials, season_folder, series_type, profile_id.
Episodes: monitored. Seasons: monitored (any of its episodes is).

apply(conn, sid, option, specials) sets every episode's monitored flag from a Monitor option:
  all, future, missing, existing, recent, pilot, first_season, last_season, none.
Specials (season 0) are only monitored with monitor_specials on (and an option that takes them).
new_episodes(conn, sid, before) gives episodes TMDB adds later the series' rule: a new season follows
Monitor new seasons; a new episode in a known season is monitored when the option looks forward
(all, future, missing, recent, last season of it). Nothing here grabs; grabs only ever target
monitored, aired, missing episodes (wanted()).

Quality profiles: an ordered list of allowed qualities (resolution x source, the movie side's ids
from file_management.RESOLUTIONS / SOURCES), a stored upgrade cutoff (upgrades come later), and one
default for new adds. No presets.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app import file_management as fm
from app import tv_store

OPTIONS = ('all', 'future', 'missing', 'existing', 'recent', 'pilot', 'first_season', 'last_season', 'none')
ADD_DEFAULT = 'all'            # Add from Search
IMPORT_DEFAULT = 'future'      # a library folder (and the v7 migration): no back catalog
RECENT_DAYS = 90
SERIES_TYPES = ('standard', 'daily', 'anime')
FORWARD = ('all', 'future', 'missing', 'recent')

_LINKED = ("EXISTS (SELECT 1 FROM episode_file_links l JOIN episode_files f ON f.id = l.file_id"
           " WHERE l.episode_id = e.id AND f.state = 'linked')")


def _today(today: Optional[str] = None) -> str:
    return today or dt.date.today().isoformat()


def _wants(option: str, e: Dict[str, Any], today: str, first: int, last: int, has_file: bool) -> bool:
    s, n, air = e['season_number'], e['episode_number'], e['air_date']
    future = air is None or air >= today
    if option == 'all':
        return True
    if option == 'future':
        return future
    if option == 'missing':
        return not has_file
    if option == 'existing':
        return has_file
    if option == 'recent':
        return future or air >= (dt.date.fromisoformat(today) - dt.timedelta(days=RECENT_DAYS)).isoformat()
    if option == 'pilot':
        return s == first and n == 1
    if option == 'first_season':
        return s == first
    if option == 'last_season':
        return s == last
    return False


def apply(conn, sid: int, option: str, specials: Optional[bool] = None, today: Optional[str] = None) -> int:
    """Set the series' episodes from *option*; returns how many are monitored."""
    if option not in OPTIONS:
        raise ValueError('option')
    today = _today(today)
    row = conn.execute('SELECT monitor_specials FROM series WHERE id = ?', (sid,)).fetchone()
    if row is None:
        return 0
    sp = bool(row['monitor_specials']) if specials is None else bool(specials)
    eps = [dict(r) for r in conn.execute(
        f'SELECT e.id, e.season_number, e.episode_number, e.air_date, {_LINKED} AS has_file FROM episodes e'
        ' WHERE e.series_id = ?', (sid,))]
    regular = sorted({e['season_number'] for e in eps if e['season_number'] > 0})
    first, last = (regular[0], regular[-1]) if regular else (1, 1)
    on = 0
    for e in eps:
        want = _wants(option, e, today, first, last, bool(e['has_file']))
        if e['season_number'] == 0:
            want = want and sp and option not in ('pilot', 'first_season', 'last_season')
        conn.execute('UPDATE episodes SET monitored = ? WHERE id = ?', (int(want), e['id']))
        on += int(want)
    conn.execute('UPDATE series SET monitor = ?, monitored = ?, monitor_specials = ?, monitor_chosen = 1 WHERE id = ?',
                 (option, int(option != 'none'), int(sp), sid))
    sync_seasons(conn, sid)
    return on


def sync_seasons(conn, sid: int) -> None:
    conn.execute('UPDATE seasons SET monitored = CASE WHEN EXISTS (SELECT 1 FROM episodes e WHERE e.series_id = seasons.series_id'
                 ' AND e.season_number = seasons.season_number AND e.monitored = 1) THEN 1 ELSE 0 END WHERE series_id = ?', (sid,))


def known(conn, sid: int) -> Tuple[Set[Tuple[int, int]], Set[int]]:
    """(episodes, seasons) the series has now: pass to new_episodes() after a refresh."""
    eps = {(r['season_number'], r['episode_number']) for r in conn.execute(
        'SELECT season_number, episode_number FROM episodes WHERE series_id = ?', (sid,))}
    return eps, {s for s, _ in eps}


def new_episodes(conn, sid: int, before: Tuple[Set[Tuple[int, int]], Set[int]], today: Optional[str] = None) -> int:
    """Episodes added since *before* inherit the series' rule; returns how many were monitored."""
    s = conn.execute('SELECT monitored, monitor, monitor_new_seasons, monitor_specials FROM series WHERE id = ?', (sid,)).fetchone()
    if s is None or not s['monitored']:
        return 0
    eps, seasons = before
    today = _today(today)
    last = max(seasons) if seasons else 0
    on = 0
    for r in conn.execute('SELECT id, season_number, episode_number, air_date FROM episodes WHERE series_id = ?', (sid,)).fetchall():
        key = (r['season_number'], r['episode_number'])
        if key in eps:
            continue
        n = r['season_number']
        if n == 0:
            want = bool(s['monitor_specials'])
        elif n not in seasons:
            want = bool(s['monitor_new_seasons'])
        else:
            want = s['monitor'] in FORWARD or (s['monitor'] == 'last_season' and n == last)
            if s['monitor'] == 'future':
                want = r['air_date'] is None or r['air_date'] >= today
        if want:
            conn.execute('UPDATE episodes SET monitored = 1 WHERE id = ?', (r['id'],))
            on += 1
    if on:
        sync_seasons(conn, sid)
    return on


def set_episodes(conn, sid: int, ids: Iterable[int], on: bool) -> int:
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    n = conn.execute(f'UPDATE episodes SET monitored = ? WHERE series_id = ? AND id IN ({",".join("?" * len(ids))})',
                     (int(on), sid, *ids)).rowcount
    conn.execute('UPDATE series SET monitor_chosen = 1' + (', monitored = 1' if on else '') + ' WHERE id = ?', (sid,))
    sync_seasons(conn, sid)
    return n


def set_season(conn, sid: int, season: int, on: bool) -> int:
    n = conn.execute('UPDATE episodes SET monitored = ? WHERE series_id = ? AND season_number = ?', (int(on), sid, season)).rowcount
    conn.execute('UPDATE series SET monitor_chosen = 1' + (', monitored = 1' if on else '') + ' WHERE id = ?', (sid,))
    sync_seasons(conn, sid)
    return n


def set_series(conn, sid: int, on: bool) -> None:
    """The series switch: off stops everything (episodes keep their flags for when it's back on)."""
    conn.execute('UPDATE series SET monitored = ?, monitor_chosen = 1 WHERE id = ?', (int(on), sid))


def wanted(conn, today: Optional[str] = None) -> List[int]:
    """Episode ids a grab may target: series and episode monitored, aired, no linked file."""
    today = _today(today)
    return [r['id'] for r in conn.execute(
        f'SELECT e.id FROM episodes e JOIN series s ON s.id = e.series_id WHERE s.monitored = 1 AND e.monitored = 1'
        f' AND e.air_date IS NOT NULL AND e.air_date < ? AND NOT {_LINKED}', (today,))]


# ------------------------------------------------------------------ the series view

def series_view(sid: int) -> Optional[Dict[str, Any]]:
    with tv_store.connect() as conn:
        r = conn.execute('SELECT id, title, year, tmdb_id, path_id, folder, monitored, monitor, monitor_new_seasons, monitor_specials,'
                         ' season_folder, series_type, profile_id FROM series WHERE id = ?', (sid,)).fetchone()
        if r is None:
            return None
        seasons = [{'season': x['season_number'], 'monitored': bool(x['monitored'])} for x in conn.execute(
            'SELECT season_number, monitored FROM seasons WHERE series_id = ? ORDER BY season_number', (sid,))]
        eps = [{'id': x['id'], 'season': x['season_number'], 'episode': x['episode_number'], 'monitored': bool(x['monitored'])}
               for x in conn.execute('SELECT id, season_number, episode_number, monitored FROM episodes WHERE series_id = ?'
                                     ' ORDER BY season_number, episode_number', (sid,))]
    root = _root(r['path_id'])
    return {'id': r['id'], 'title': r['title'], 'year': r['year'], 'tmdb_id': r['tmdb_id'], 'monitored': bool(r['monitored']),
            'monitor': r['monitor'], 'monitor_new_seasons': 'all' if r['monitor_new_seasons'] else 'none',
            'monitor_specials': bool(r['monitor_specials']), 'season_folder': bool(r['season_folder']),
            'series_type': r['series_type'], 'profile_id': r['profile_id'], 'path_id': r['path_id'], 'folder': r['folder'],
            'path': os.path.join(root, r['folder']) if root and r['folder'] else root,
            'seasons': seasons, 'episodes': eps}


def _root(path_id: Optional[str]) -> Optional[str]:
    from app import tv_settings
    for rec in tv_settings.libraries():
        if rec['id'] == path_id:
            return os.path.realpath(rec['path'])
    return None


class Invalid(ValueError):
    def __init__(self, errors: Dict[str, str]):
        super().__init__('invalid')
        self.errors = errors


def _path_to_folder(path: str) -> Tuple[str, str]:
    """An absolute folder in a selected TV library -> (path_id, folder name). One level under the root."""
    from app import tv_settings
    real = os.path.realpath(path)
    for rec in tv_settings.libraries():
        root = os.path.realpath(rec['path'])
        if os.path.dirname(real) == root and os.path.basename(real) not in ('', '.', '..'):
            return rec['id'], os.path.basename(real)
    raise Invalid({'path': 'Pick a folder in a TV library'})


def edit(sid: int, body: Dict[str, Any], today: Optional[str] = None) -> Dict[str, Any]:
    """The Edit series sheet's Save. Unknown keys are ignored; a bad value is a field error."""
    errors: Dict[str, str] = {}
    upd: Dict[str, Any] = {}
    if 'monitored' in body:
        if not isinstance(body['monitored'], bool):
            errors['monitored'] = 'Invalid'
        else:
            upd['monitored'] = int(body['monitored'])
    if 'monitor_new_seasons' in body:
        if body['monitor_new_seasons'] not in ('all', 'none'):
            errors['monitor_new_seasons'] = 'Invalid'
        else:
            upd['monitor_new_seasons'] = int(body['monitor_new_seasons'] == 'all')
    if 'season_folder' in body:
        if not isinstance(body['season_folder'], bool):
            errors['season_folder'] = 'Invalid'
        else:
            upd['season_folder'] = int(body['season_folder'])
    if 'series_type' in body:
        if body['series_type'] not in SERIES_TYPES:
            errors['series_type'] = 'Invalid'
        else:
            upd['series_type'] = body['series_type']
    if 'profile_id' in body:
        pid = body['profile_id']
        if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool) or get_profile(pid) is None):
            errors['profile_id'] = 'Pick a profile'
        else:
            upd['profile_id'] = pid
    option = body.get('monitor')
    if option is not None and option not in OPTIONS:
        errors['monitor'] = 'Invalid'
    specials = body.get('monitor_specials')
    if specials is not None and not isinstance(specials, bool):
        errors['monitor_specials'] = 'Invalid'
    if 'path' in body:
        try:
            upd['path_id'], upd['folder'] = _path_to_folder(str(body['path'] or ''))
        except Invalid as e:
            errors.update(e.errors)
    if errors:
        raise Invalid(errors)
    with tv_store.write() as conn:
        if conn.execute('SELECT 1 FROM series WHERE id = ?', (sid,)).fetchone() is None:
            raise KeyError(sid)
        if 'folder' in upd:
            clash = conn.execute('SELECT id FROM series WHERE path_id = ? AND folder = ? AND id != ?',
                                 (upd['path_id'], upd['folder'], sid)).fetchone()
            if clash:
                raise Invalid({'path': 'Another show uses this folder'})
        if 'monitored' in upd or 'monitor_new_seasons' in upd:
            upd['monitor_chosen'] = 1
        if upd:
            conn.execute(f'UPDATE series SET {", ".join(f"{k} = ?" for k in upd)} WHERE id = ?', (*upd.values(), sid))
        if specials is not None:
            conn.execute('UPDATE series SET monitor_specials = ? WHERE id = ?', (int(specials), sid))
        if option is not None or specials is not None:
            cur = conn.execute('SELECT monitor FROM series WHERE id = ?', (sid,)).fetchone()['monitor']
            apply(conn, sid, option or cur, specials, today)
            if 'monitored' in upd:            # an explicit switch wins over the option's implied one
                conn.execute('UPDATE series SET monitored = ? WHERE id = ?', (upd['monitored'], sid))
    return series_view(sid)  # type: ignore[return-value]


def bulk_missing(ids: Iterable[int], today: Optional[str] = None) -> int:
    """Series grid bulk select: opt shows in to Missing episodes (back catalog)."""
    n = 0
    with tv_store.write() as conn:
        for sid in ids:
            if conn.execute('SELECT 1 FROM series WHERE id = ?', (int(sid),)).fetchone():
                apply(conn, int(sid), 'missing', None, today)
                n += 1
    return n


def delete(sid: int, delete_files: bool) -> Dict[str, Any]:
    """Remove the show from tv.db (history row kept as a log line); with *delete_files*, its folder too.
    The caller has checked the owner (files: a signed-in owner) and the instance gate."""
    with tv_store.connect() as conn:
        r = conn.execute('SELECT path_id, folder, title, tmdb_id FROM series WHERE id = ?', (sid,)).fetchone()
    if r is None:
        raise KeyError(sid)
    removed = None
    if delete_files and r['folder']:
        root = _root(r['path_id'])
        target = os.path.realpath(os.path.join(root, r['folder'])) if root else None
        if (not target or os.path.dirname(target) != root or fm.blocked(target) or os.path.islink(os.path.join(root, r['folder']))):
            raise Invalid({'delete_files': 'Folder not found'})
        if os.path.isdir(target):
            shutil.rmtree(target)
            removed = target
    with tv_store.write() as conn:
        conn.execute('DELETE FROM series WHERE id = ?', (sid,))
        conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, NULL, ?, ?, ?)',
                     (__import__('time').time(), '[]', 'deleted', json.dumps({'title': r['title'], 'tmdb_id': r['tmdb_id'],
                                                                              'files': bool(removed)})))
    return {'ok': True, 'files_removed': bool(removed)}


# ------------------------------------------------------------------ quality profiles

SOURCE_LABEL = {'remux': 'Remux', 'bluray': 'Blu-ray', 'web': 'WEB-DL', 'hdtv': 'HDTV'}
RES_LABEL = {'2160p': '2160p', '1080p': '1080p', '720p': '720p', 'sd': 'SD'}


def qualities() -> List[Dict[str, str]]:
    """Every quality a profile can list: the movie side's resolutions x sources (no 720p/SD remux)."""
    out = []
    for res in fm.RESOLUTIONS:
        for src in fm.SOURCES:
            if src == 'remux' and res in ('720p', 'sd'):
                continue
            out.append({'id': f'{res}-{src}', 'name': f'{RES_LABEL[res]} {SOURCE_LABEL[src]}', 'resolution': res, 'source': src})
    return out


QUALITY_IDS = tuple(q['id'] for q in qualities())


def quality_of(name: str) -> Optional[str]:
    """A release or file name's quality id, by the movie side's parser (file_management.classify)."""
    c = dict(fm.classify(name))
    if c.get('source') == 'hdtv' and not c.get('resolution'):
        c['resolution'] = 'sd'                      # bare HDTV is SDTV
    if c.get('resolution') and c.get('source'):
        q = f"{c['resolution']}-{c['source']}"
        return q if q in QUALITY_IDS else None
    return None


def _profile(r) -> Dict[str, Any]:
    items = json.loads(r['items'] or '[]')
    return {'id': r['id'], 'name': r['name'], 'items': items, 'cutoff': r['cutoff'], 'upgrades': bool(r['upgrades']),
            'default': bool(r['is_default'])}


def profiles() -> List[Dict[str, Any]]:
    with tv_store.connect() as conn:
        used = {x['profile_id'] for x in conn.execute('SELECT DISTINCT profile_id FROM series WHERE profile_id IS NOT NULL')}
        return [{**_profile(r), 'in_use': r['id'] in used}
                for r in conn.execute('SELECT * FROM quality_profiles ORDER BY name COLLATE NOCASE')]


def get_profile(pid: int) -> Optional[Dict[str, Any]]:
    with tv_store.connect() as conn:
        r = conn.execute('SELECT * FROM quality_profiles WHERE id = ?', (pid,)).fetchone()
    return _profile(r) if r else None


def default_profile_id() -> Optional[int]:
    with tv_store.connect() as conn:
        r = conn.execute('SELECT id FROM quality_profiles WHERE is_default = 1').fetchone()
    return r['id'] if r else None


def _check_profile(body: Dict[str, Any], pid: Optional[int]) -> Dict[str, Any]:
    errors: Dict[str, str] = {}
    name = body.get('name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
        errors['name'] = 'Enter a name'
    items = body.get('items')
    clean: List[Dict[str, Any]] = []
    if not isinstance(items, list):
        errors['items'] = 'Pick at least one quality'
    else:
        seen = set()
        for it in items:
            if not isinstance(it, dict) or it.get('id') not in QUALITY_IDS or it['id'] in seen:
                errors['items'] = 'Invalid'
                break
            seen.add(it['id'])
            clean.append({'id': it['id'], 'allowed': bool(it.get('allowed'))})
        else:
            for q in QUALITY_IDS:                   # every quality is listed, in the owner's order
                if q not in seen:
                    clean.append({'id': q, 'allowed': False})
            if not any(i['allowed'] for i in clean):
                errors['items'] = 'Pick at least one quality'
    upgrades = body.get('upgrades', False)
    cutoff = body.get('cutoff')
    if not isinstance(upgrades, bool):
        errors['upgrades'] = 'Invalid'
    allowed = [i['id'] for i in clean if i['allowed']]
    if cutoff is None:
        cutoff = allowed[0] if allowed else None    # stored either way (upgrades come later)
    elif cutoff not in allowed:
        errors['cutoff'] = 'Pick an enabled quality'
    if not isinstance(body.get('default', False), bool):
        errors['default'] = 'Invalid'
    if errors:
        raise Invalid(errors)
    return {'name': name.strip(), 'items': json.dumps(clean), 'cutoff': cutoff, 'upgrades': int(upgrades),
            'is_default': int(bool(body.get('default', False)))}


def save_profile(body: Dict[str, Any], pid: Optional[int] = None) -> Dict[str, Any]:
    vals = _check_profile(body, pid)
    import sqlite3
    with tv_store.write() as conn:
        first = conn.execute('SELECT COUNT(*) AS n FROM quality_profiles').fetchone()['n'] == 0
        if first:
            vals['is_default'] = 1                  # the first profile is the default for new adds
        if vals['is_default']:
            conn.execute('UPDATE quality_profiles SET is_default = 0')
        try:
            if pid is None:
                pid = int(conn.execute(f'INSERT INTO quality_profiles ({", ".join(vals)}) VALUES ({", ".join("?" * len(vals))})',
                                       tuple(vals.values())).lastrowid)
            else:
                if conn.execute('SELECT is_default FROM quality_profiles WHERE id = ?', (pid,)).fetchone() is None:
                    raise KeyError(pid)
                conn.execute(f'UPDATE quality_profiles SET {", ".join(f"{k} = ?" for k in vals)} WHERE id = ?', (*vals.values(), pid))
                if not conn.execute('SELECT 1 FROM quality_profiles WHERE is_default = 1').fetchone():
                    conn.execute('UPDATE quality_profiles SET is_default = 1 WHERE id = ?', (pid,))   # always one default
        except sqlite3.IntegrityError:
            raise Invalid({'name': 'Name already used'})
    return get_profile(pid)  # type: ignore[return-value]


def delete_profile(pid: int) -> None:
    with tv_store.write() as conn:
        r = conn.execute('SELECT is_default FROM quality_profiles WHERE id = ?', (pid,)).fetchone()
        if r is None:
            raise KeyError(pid)
        if r['is_default'] or conn.execute('SELECT 1 FROM series WHERE profile_id = ?', (pid,)).fetchone():
            raise Invalid({'profile': 'In use'})
        conn.execute('DELETE FROM quality_profiles WHERE id = ?', (pid,))
