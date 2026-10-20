"""v198 Settings > Movies (the native movie watchlist, piece 1): quality profiles (name, qualities, cutoff,
min/max size), tracker selection and priority, preferred release groups and the monitoring switch.

Store: movies.db in the install's own data dir (sqlite, 0600), separate from tv.db. Nothing here talks to
the old movie manager or any live server. A fresh store is seeded from app/data/movie_profiles_seed.json
(a sanitized copy of Taylor's saved export: profile names, qualities, cutoff, MB/min sizes and the tracker
order; no secrets or URLs) or, without it, gets one 'Any' profile.

Routes (luma-test builds, owner only; writes need the signed-in owner):
GET  /api/settings/movies                {monitoring, preferred_groups, trackers, profiles, qualities}
PUT  /api/settings/movies                {monitoring?, preferred_groups?, trackers?: [{id, enabled}] in order}
POST /api/settings/movies/profiles       PUT|DELETE /api/settings/movies/profiles/<id>
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from flask import Blueprint, jsonify, request

from app import paths as _paths

logger = logging.getLogger(__name__)
bp = Blueprint('movie_settings', __name__)

DB_NAME = 'movies.db'
MAX_GROUPS = 20
MAX_MB_PER_MIN = 10_000          # size limits are MB per minute of runtime (like TV and the export)
_lock = threading.Lock()
_cfg: Dict[str, Any] = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL COLLATE NOCASE,
  items TEXT NOT NULL DEFAULT '[]', cutoff TEXT,
  min_mb_per_min REAL, max_mb_per_min REAL,
  is_default INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Invalid(Exception):
    def __init__(self, errors: Dict[str, str]):
        super().__init__('invalid')
        self.errors = errors


# ------------------------------------------------------------------ store

def db_path() -> Path:
    return Path(_cfg.get('db_path') or _paths.data_path(DB_NAME))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    conn = sqlite3.connect(str(p), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if new:
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        conn.executescript(SCHEMA)
        with _lock:
            _seed(conn)
            yield conn
            conn.commit()
    finally:
        conn.close()


SEED_FILE = Path(__file__).resolve().parent / 'data' / 'movie_profiles_seed.json'


def _seed_data() -> Optional[Dict[str, Any]]:
    p = Path(_cfg.get('seed_path') or SEED_FILE)
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get('profiles'), list) and d['profiles'] else None


def _seed(conn: sqlite3.Connection) -> None:
    if conn.execute('SELECT 1 FROM meta WHERE key = ?', ('seeded',)).fetchone():
        return
    ids = quality_ids()
    seed = _seed_data()
    how = 'any'
    if not conn.execute('SELECT 1 FROM profiles').fetchone():
        rows = []
        for p in (seed or {}).get('profiles', []):
            allowed = [q for q in p.get('allowed') or [] if q in ids]
            if not isinstance(p.get('name'), str) or not allowed:
                continue
            items = [{'id': q, 'allowed': True} for q in allowed] + [{'id': q, 'allowed': False} for q in ids if q not in allowed]
            cut = p.get('cutoff') if p.get('cutoff') in allowed else allowed[0]
            rows.append((p['name'][:60], json.dumps(items), cut, p.get('min_mb_per_min'), p.get('max_mb_per_min'),
                         int(p['name'] == 'Any')))      # the export doesn't say which is the library default: 'Any'
        if rows:
            how = 'export'
            if not any(r[5] for r in rows):
                rows[0] = rows[0][:5] + (1,)
        else:
            rows = [('Any', json.dumps([{'id': q, 'allowed': True} for q in ids]), ids[0], None, None, 1)]
        conn.executemany('INSERT OR IGNORE INTO profiles (name, items, cutoff, min_mb_per_min, max_mb_per_min, is_default)'
                         ' VALUES (?, ?, ?, ?, ?, ?)', rows)
    if seed and isinstance(seed.get('trackers'), list):
        _set_meta(conn, 'tracker_seed', [t for t in seed['trackers'] if isinstance(t, dict) and isinstance(t.get('type'), str)])
    conn.execute("INSERT INTO meta (key, value) VALUES ('seeded', ?)", (json.dumps(how),))
    conn.commit()


def _meta(conn, key: str, default: Any) -> Any:
    r = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    try:
        return json.loads(r['value']) if r else default
    except ValueError:
        return default


def _set_meta(conn, key: str, value: Any) -> None:
    conn.execute('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                 (key, json.dumps(value)))


# ------------------------------------------------------------------ qualities (the movie side's own list)

def qualities() -> List[Dict[str, str]]:
    from app import tv_monitor
    return tv_monitor.qualities()


def quality_ids() -> List[str]:
    return [q['id'] for q in qualities()]


# ------------------------------------------------------------------ profiles

def _profile(r) -> Dict[str, Any]:
    return {'id': r['id'], 'name': r['name'], 'items': json.loads(r['items'] or '[]'), 'cutoff': r['cutoff'],
            'min_mb_per_min': r['min_mb_per_min'], 'max_mb_per_min': r['max_mb_per_min'], 'default': bool(r['is_default'])}


def profiles() -> List[Dict[str, Any]]:
    with connect() as conn:
        return [_profile(r) for r in conn.execute('SELECT * FROM profiles ORDER BY name COLLATE NOCASE')]


def _size(v: Any, key: str, errors: Dict[str, str]) -> Optional[float]:
    if v is None or v == '':
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= MAX_MB_PER_MIN:
        errors[key] = 'Invalid'
        return None
    return float(v)


def _check(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise Invalid({'_': 'Invalid'})
    errors: Dict[str, str] = {}
    name = body.get('name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
        errors['name'] = 'Enter a name'
    ids = quality_ids()
    clean: List[Dict[str, Any]] = []
    items = body.get('items')
    if not isinstance(items, list):
        errors['items'] = 'Pick at least one quality'
    else:
        seen = set()
        for it in items:
            if not isinstance(it, dict) or it.get('id') not in ids or it['id'] in seen:
                errors['items'] = 'Invalid'
                break
            seen.add(it['id'])
            clean.append({'id': it['id'], 'allowed': bool(it.get('allowed'))})
        else:
            clean += [{'id': q, 'allowed': False} for q in ids if q not in seen]
            if not any(i['allowed'] for i in clean):
                errors['items'] = 'Pick at least one quality'
    allowed = [i['id'] for i in clean if i['allowed']]
    cutoff = body.get('cutoff')
    if cutoff is None:
        cutoff = allowed[0] if allowed else None
    elif cutoff not in allowed:
        errors['cutoff'] = 'Pick an enabled quality'
    lo = _size(body.get('min_mb_per_min'), 'min_mb_per_min', errors)
    hi = _size(body.get('max_mb_per_min'), 'max_mb_per_min', errors)
    if lo is not None and hi is not None and lo > hi:
        errors['max_mb_per_min'] = 'Must be at least the minimum'
    if not isinstance(body.get('default', False), bool):
        errors['default'] = 'Invalid'
    if set(body) - {'name', 'items', 'cutoff', 'min_mb_per_min', 'max_mb_per_min', 'default', 'id'}:
        errors['_'] = 'Invalid'
    if errors:
        raise Invalid(errors)
    return {'name': name.strip(), 'items': json.dumps(clean), 'cutoff': cutoff, 'min_mb_per_min': lo,
            'max_mb_per_min': hi, 'is_default': int(bool(body.get('default', False)))}


def save_profile(body: Any, pid: Optional[int] = None) -> Dict[str, Any]:
    vals = _check(body)
    with connect() as conn:
        if vals['is_default']:
            conn.execute('UPDATE profiles SET is_default = 0')
        try:
            if pid is None:
                if not conn.execute('SELECT 1 FROM profiles').fetchone():
                    vals['is_default'] = 1
                pid = int(conn.execute(f'INSERT INTO profiles ({", ".join(vals)}) VALUES ({", ".join("?" * len(vals))})',
                                       tuple(vals.values())).lastrowid)
            else:
                if conn.execute('SELECT 1 FROM profiles WHERE id = ?', (pid,)).fetchone() is None:
                    raise KeyError(pid)
                conn.execute(f'UPDATE profiles SET {", ".join(f"{k} = ?" for k in vals)} WHERE id = ?', (*vals.values(), pid))
            if not conn.execute('SELECT 1 FROM profiles WHERE is_default = 1').fetchone():
                conn.execute('UPDATE profiles SET is_default = 1 WHERE id = ?', (pid,))   # always one default
        except sqlite3.IntegrityError:
            raise Invalid({'name': 'Name already used'})
        return _profile(conn.execute('SELECT * FROM profiles WHERE id = ?', (pid,)).fetchone())


def delete_profile(pid: int) -> None:
    with connect() as conn:
        r = conn.execute('SELECT is_default FROM profiles WHERE id = ?', (pid,)).fetchone()
        if r is None:
            raise KeyError(pid)
        if r['is_default']:
            raise Invalid({'profile': 'Default profile'})
        conn.execute('DELETE FROM profiles WHERE id = ?', (pid,))


# ------------------------------------------------------------------ settings

def groups(v: Any) -> Optional[List[str]]:
    """Same rules as TV (tv_settings.groups): at most 20, each 1-40 chars; None when invalid."""
    from app import tv_settings
    if isinstance(v, list) and len(v) > MAX_GROUPS:
        return None
    return tv_settings.groups(v)


def _tracker_records() -> List[Dict[str, Any]]:
    try:
        from app.tracker_search import get_search
        return [{'id': r['id'], 'name': r['name'], 'type': r.get('type')} for r in get_search().store.list()
                if isinstance(r.get('id'), int)]
    except Exception:                                # noqa: BLE001 - no trackers configured yet
        return []


def trackers(conn=None) -> List[Dict[str, Any]]:
    """Every tracker in the owner's movie order: [{id, name, enabled}] (new trackers last, off)."""
    recs = {r['id']: r for r in _tracker_records()}

    def go(c):
        saved = _meta(c, 'trackers', None)
        if saved is None:                       # never saved: the export's priority order (by tracker type)
            seed = _meta(c, 'tracker_seed', [])
            saved = [{'id': r['id'], 'enabled': bool(t.get('enabled'))} for t in seed for r in recs.values()
                     if r.get('type') == t.get('type')]
        out, seen = [], set()
        for t in saved if isinstance(saved, list) else []:
            if isinstance(t, dict) and t.get('id') in recs and t['id'] not in seen:
                seen.add(t['id'])
                out.append({'id': t['id'], 'name': recs[t['id']]['name'], 'enabled': bool(t.get('enabled'))})
        out += [{'id': i, 'name': r['name'], 'enabled': False} for i, r in recs.items() if i not in seen]
        return out
    if conn is not None:
        return go(conn)
    with connect() as c:
        return go(c)


def view() -> Dict[str, Any]:
    with connect() as conn:
        return {'monitoring': bool(_meta(conn, 'monitoring', False)),
                'preferred_groups': list(_meta(conn, 'preferred_groups', [])),
                'trackers': trackers(conn),
                'profiles': [_profile(r) for r in conn.execute('SELECT * FROM profiles ORDER BY name COLLATE NOCASE')],
                'qualities': qualities()}


def apply_put(body: Any) -> Dict[str, str]:
    if not isinstance(body, dict) or not body or set(body) - {'monitoring', 'preferred_groups', 'trackers'}:
        return {'_': 'Invalid'}
    errors: Dict[str, str] = {}
    vals: Dict[str, Any] = {}
    if 'monitoring' in body:
        if isinstance(body['monitoring'], bool):
            vals['monitoring'] = body['monitoring']
        else:
            errors['monitoring'] = 'Invalid'
    if 'preferred_groups' in body:
        g = groups(body['preferred_groups'])
        if g is None:
            errors['preferred_groups'] = 'Invalid'
        else:
            vals['preferred_groups'] = g
    if 'trackers' in body:
        known = {r['id'] for r in _tracker_records()}
        t = body['trackers']
        ok = isinstance(t, list) and all(isinstance(x, dict) and set(x) <= {'id', 'enabled'} and type(x.get('id')) is int
                                         and x['id'] in known and isinstance(x.get('enabled', False), bool) for x in t) \
            and len({x['id'] for x in t}) == len(t)
        if ok:
            vals['trackers'] = [{'id': x['id'], 'enabled': bool(x.get('enabled'))} for x in t]
        else:
            errors['trackers'] = 'Invalid'
    if errors:
        return errors
    with connect() as conn:
        for k, v in vals.items():
            _set_meta(conn, k, v)
    return {}


# ------------------------------------------------------------------ routes

def init(**cfg: Any) -> None:
    _cfg.update(cfg)


def _guard(write: bool = False):
    from app import tv_settings
    return tv_settings._guard() or (tv_settings._signed_in_owner() if write else None)


def _out(body: Any, status: int = 200):
    r = jsonify(body)
    r.headers['Cache-Control'] = 'no-store'
    return r, status


@bp.route('/api/settings/movies', methods=['GET'])
def movies_get():
    return _guard() or _out(view())


@bp.route('/api/settings/movies', methods=['PUT'])
def movies_put():
    err = _guard(write=True)
    if err:
        return err
    errors = apply_put(request.get_json(silent=True))
    return _out({'errors': errors}, 400) if errors else _out(view())


def _save(pid: Optional[int] = None):
    err = _guard(write=True)
    if err:
        return err
    try:
        return _out(save_profile(request.get_json(silent=True), pid), 201 if pid is None else 200)
    except Invalid as e:
        return _out({'error': 'Invalid', 'errors': e.errors}, 400)
    except KeyError:
        return _out({'error': 'Not found'}, 404)


@bp.route('/api/settings/movies/profiles', methods=['POST'])
def profile_post():
    return _save()


@bp.route('/api/settings/movies/profiles/<int:pid>', methods=['PUT'])
def profile_put(pid: int):
    return _save(pid)


@bp.route('/api/settings/movies/profiles/<int:pid>', methods=['DELETE'])
def profile_delete(pid: int):
    err = _guard(write=True)
    if err:
        return err
    try:
        delete_profile(pid)
    except Invalid as e:
        return _out({'error': 'In use', 'errors': e.errors}, 409)
    except KeyError:
        return _out({'error': 'Not found'}, 404)
    return _out({'ok': True})
