"""Settings > Library paths for LUMA_MINIMAL builds (luma-test, owner only).

Named library folders, ordered rules (first enabled match wins) plus one
fallback, an optional qBittorrent-to-Luma path map and the transfer mode. The
library mover (app/library_mover.py) reads this to file each finished grab.

GET    /api/settings/library-paths            everything the section shows
POST   /api/settings/library-paths            add a path {name, path}
PUT    /api/settings/library-paths/<id>       rename or repoint a path
DELETE /api/settings/library-paths/<id>       409 while a rule or the fallback uses it
PUT    /api/settings/library-rules            {rules: [...], fallback_path_id}
PUT    /api/settings/library-paths/mapping    {path_map: [...], transfer}
GET    /api/settings/library-paths/browse?path=  folders for the picker (allowed roots only)

Its own store (data dir, file_management.json, 0600), empty on a fresh
install. It never reads or shows settings.json media.paths / media.types or
the *_ROOT defaults, which production's bootstrap seeds. Paths are checked
with realpath: an existing folder outside system folders (read-only is fine;
write destinations are checked as such), not the
same as or nested in another configured path (a mount parent such as /media
is not a configured path, so any /media/<sub> is fine, spaces included).
Rules and the release parser port prod's auto_rename routing
(detect_video_quality, RECENT_YEAR) with the conditions made per rule.
"""

from __future__ import annotations

import calendar
import copy
import json
import logging
import os
import re
import secrets
import threading
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from app import paths as _paths

logger = logging.getLogger(__name__)

bp = Blueprint('file_management', __name__)
_cfg: Dict[str, Any] = {}

STORE_NAME = 'file_management.json'
USED_BY_TV = 'This path is used by TV'
MAX_PATHS = 50
MAX_RULES = 50
MAX_MAPS = 20
NAME_MAX = 40
PATH_MAX = 1024
RESOLUTIONS = ('2160p', '1080p', '720p', 'sd')
SOURCES = ('remux', 'bluray', 'web', 'hdtv')
UNITS = {'months': 1, 'years': 12}
MAX_MONTHS = 600
TRANSFERS = ('copy', 'hardlink')
JOBS_KEEP = 100
# The folder qBittorrent's downloads are mounted at (compose: /downloads). The
# mover only ever reads from inside it, whatever save path qBittorrent reports.
DOWNLOADS_ROOT_ENV = 'LUMA_DOWNLOADS_ROOT'
DEFAULT_DOWNLOADS_ROOT = '/downloads'
# Mover errors that a corrected path map can fix: such jobs go back to waiting.
PATH_ERRORS = ('outside the downloads folder', 'download folder not available',
               'qBittorrent gave no download path', 'files not found')

# Never a library path (or a path map's local side), nor anything inside one.
BLOCKED_ROOTS = ('/proc', '/sys', '/dev', '/etc', '/app', '/bin', '/sbin', '/boot', '/lib',
                 '/lib32', '/lib64', '/libx32', '/usr', '/run', '/var/run', '/root')

REQUIRED = 'Required'
INVALID = 'Invalid'
INVALID_PATH = 'Invalid path'
NOT_ALLOWED = 'Not allowed'
NOT_FOUND = 'Folder not found'
NOT_WRITABLE = "Can't write to this folder"
DUPLICATE = 'Already added'
OVERLAPS = 'Overlaps another library path'
IN_DOWNLOADS = 'Overlaps the downloads folder'
NAME_TAKEN = 'Name already used'
IN_USE = 'In use by a rule or the fallback'
TOO_MANY = 'Too many'

_ID_RE = re.compile(r'^[pr]_[0-9a-f]{8}$')
_CTRL_RE = re.compile(r'[\x00-\x1f\x7f]')

_lock = threading.RLock()


# --------------------------------------------------------------------------- store

def empty() -> Dict[str, Any]:
    return {'version': 1, 'paths': [], 'rules': [], 'fallback_path_id': None,
            'path_map': [], 'transfer': 'copy', 'jobs': {}}


def store_path() -> Path:
    return Path(_cfg.get('store_path') or _paths.data_path(STORE_NAME))


def lock() -> threading.RLock:
    return _lock


def load() -> Dict[str, Any]:
    """The store, normalized. A missing or unreadable file reads as empty
    (an unreadable one is quarantined, never trusted)."""
    p = store_path()
    with _lock:
        try:
            raw = json.loads(p.read_text())
        except FileNotFoundError:
            return empty()
        except (OSError, ValueError) as e:
            logger.error('Library paths store unreadable (%s); starting empty', type(e).__name__)
            _paths.quarantine(p, 'unreadable', logger)
            return empty()
        return _normalize(raw)


def save(data: Dict[str, Any]) -> None:
    with _lock:
        _paths.atomic_write_json(store_path(), _normalize(data), mode=0o600)


def update(fn: Callable[[Dict[str, Any]], Any]) -> Any:
    """Load, apply *fn*, save; returns fn's result. One writer at a time."""
    with _lock:
        data = load()
        out = fn(data)
        save(data)
        return out


def _normalize(raw: Any) -> Dict[str, Any]:
    d = empty()
    if not isinstance(raw, dict):
        return d
    ids = set()
    for p in raw.get('paths') or []:
        if (isinstance(p, dict) and isinstance(p.get('id'), str) and _ID_RE.match(p['id'])
                and isinstance(p.get('name'), str) and isinstance(p.get('path'), str)
                and p['id'] not in ids):
            ids.add(p['id'])
            d['paths'].append({'id': p['id'], 'name': p['name'], 'path': p['path']})
    for r in raw.get('rules') or []:
        if isinstance(r, dict) and isinstance(r.get('id'), str) and r.get('path_id') in ids:
            d['rules'].append({
                'id': r['id'], 'enabled': r.get('enabled') is not False, 'path_id': r['path_id'],
                'released_within': r.get('released_within') if isinstance(r.get('released_within'), dict) else None,
                'resolution': [x for x in (r.get('resolution') or []) if x in RESOLUTIONS],
                'source': [x for x in (r.get('source') or []) if x in SOURCES],
            })
    fb = raw.get('fallback_path_id')
    d['fallback_path_id'] = fb if fb in ids else None
    for m in raw.get('path_map') or []:
        if isinstance(m, dict) and isinstance(m.get('remote'), str) and isinstance(m.get('local'), str):
            d['path_map'].append({'remote': m['remote'], 'local': m['local']})
    d['transfer'] = raw.get('transfer') if raw.get('transfer') in TRANSFERS else 'copy'
    jobs = raw.get('jobs')
    if isinstance(jobs, dict):
        d['jobs'] = {k: v for k, v in jobs.items() if isinstance(k, str) and isinstance(v, dict)}
    return d


def _new_id(prefix: str, taken) -> str:
    while True:
        i = f'{prefix}_{secrets.token_hex(4)}'
        if i not in taken:
            return i


# --------------------------------------------------------------------------- paths

def _under(path: str, root: str) -> bool:
    """path equals root or lies inside it (both already real and absolute)."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def downloads_root() -> str:
    """Real path of the downloads folder (LUMA_DOWNLOADS_ROOT, else /downloads)."""
    raw = (os.environ.get(DOWNLOADS_ROOT_ENV) or '').strip()
    if not raw.startswith('/'):
        raw = DEFAULT_DOWNLOADS_ROOT
    return os.path.realpath(raw)


def overlaps_downloads(real: str) -> bool:
    dl = downloads_root()
    return _under(real, dl) or _under(dl, real)


def blocked(real: str) -> bool:
    """A system or Luma-internal folder: never a library or download root."""
    if real == '/':
        return True
    for b in BLOCKED_ROOTS:
        if _under(real, b):
            return True
    for d in (_paths.data_dir(), _paths.cache_dir()):
        try:
            if _under(real, os.path.realpath(str(d))):
                return True
        except OSError:
            continue
    return False


def clean_abs_path(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    """(normalized absolute path, None) or (None, error). Spaces are fine;
    control characters, '..' segments and relative paths are not."""
    if not isinstance(raw, str):
        return None, INVALID_PATH
    p = raw.strip()
    if not p:
        return None, REQUIRED
    if len(p) > PATH_MAX or _CTRL_RE.search(p) or not p.startswith('/'):
        return None, INVALID_PATH
    if any(seg == '..' for seg in p.split('/')):
        return None, INVALID_PATH
    p = '/' + os.path.normpath(p).lstrip('/')
    return p, None


def writable(real: str) -> bool:
    """os.access plus a real create/delete probe (CIFS can say yes and fail)."""
    if not os.access(real, os.W_OK | os.X_OK):
        return False
    probe = os.path.join(real, f'.luma-write-test-{secrets.token_hex(6)}')
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
        os.unlink(probe)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- folder picker

BROWSE_ROOTS_ENV = 'LUMA_BROWSE_ROOTS'
DEFAULT_BROWSE_ROOTS = ('/media',)
BROWSE_MAX = 500


def _base_browse_roots() -> List[str]:
    raw = (os.environ.get(BROWSE_ROOTS_ENV) or '').strip()
    return [r.strip() for r in re.split(r'[:,]', raw) if r.strip()] if raw else list(DEFAULT_BROWSE_ROOTS)


def inside_browse_roots(real: str) -> bool:
    """*real* is inside LUMA_BROWSE_ROOTS (else /media): where the folder
    picker starts. Library paths themselves don't count, so a new path can't
    vouch for itself."""
    for c in _base_browse_roots():
        p, err = clean_abs_path(c)
        if err:
            continue
        root = os.path.realpath(p)
        if not blocked(root) and (real == root or _under(real, root)):
            return True
    return False


def browse_roots() -> List[str]:
    """Real paths the folder picker may show: LUMA_BROWSE_ROOTS (':' or ','
    separated absolute paths, else /media), the downloads folder, and every
    configured library path (TV selects from these). Missing or blocked ones
    are dropped, and a root inside another is folded into it."""
    cands = _base_browse_roots() + [downloads_root()] + [p['path'] for p in load()['paths']]
    reals: List[str] = []
    for c in cands:
        p, err = clean_abs_path(c)
        if err:
            continue
        real = os.path.realpath(p)
        if blocked(real) or not os.path.isdir(real) or real in reals:
            continue
        reals.append(real)
    return sorted(r for r in reals if not any(o != r and _under(r, o) for o in reals))


def browse(raw: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """({path, parent, dirs: [{name, path}]}, None) or (None, error). No path:
    the roots. Only folders whose real path stays inside a root are shown or
    opened (a symlink pointing out is neither). Reads only: scandir."""
    roots = browse_roots()
    if not raw:
        return {'path': None, 'parent': None, 'dirs': [{'name': r, 'path': r} for r in roots]}, None
    p, err = clean_abs_path(raw)
    if err:
        return None, err
    real = os.path.realpath(p)
    root = next((r for r in roots if _under(real, r)), None)
    if root is None or blocked(real):
        return None, NOT_ALLOWED
    if not os.path.isdir(real):
        return None, NOT_FOUND
    dirs = []
    try:
        with os.scandir(real) as it:
            for e in it:
                if e.name.startswith('.') or _CTRL_RE.search(e.name):
                    continue
                try:
                    if not e.is_dir(follow_symlinks=True):
                        continue
                    child_real = os.path.realpath(os.path.join(real, e.name))
                except OSError:
                    continue
                if not any(_under(child_real, r) for r in roots) or blocked(child_real):
                    continue
                dirs.append({'name': e.name, 'path': os.path.join(p, e.name)})
    except OSError:
        return None, NOT_FOUND
    dirs.sort(key=lambda d: d['name'].casefold())
    parent = None if real in roots else os.path.dirname(p)
    return {'path': p, 'parent': parent, 'dirs': dirs[:BROWSE_MAX]}, None


_removed_hooks: List[Callable[[str], None]] = []


def on_path_removed(fn: Callable[[str], None]) -> None:
    """Call *fn(path_id)* after a Library path is removed (sections that
    select Library paths, such as TV, drop it from their selection)."""
    if fn not in _removed_hooks:
        _removed_hooks.append(fn)


def _path_removed(pid: str) -> None:
    for fn in list(_removed_hooks):
        try:
            fn(pid)
        except Exception as e:
            logger.warning('A section did not drop a removed library path: %s', type(e).__name__)


_repointed_hooks: List[Callable[[str], None]] = []


def on_path_repointed(fn: Callable[[str], None]) -> None:
    """Call *fn(path_id)* after a Library path is pointed at another folder
    (TV hides what it found in the old one, keeping its history)."""
    if fn not in _repointed_hooks:
        _repointed_hooks.append(fn)


def _path_repointed(pid: str) -> None:
    for fn in list(_repointed_hooks):
        try:
            fn(pid)
        except Exception as e:
            logger.warning('A section did not follow a repointed library path: %s', type(e).__name__)


_resaved_hooks: List[Callable[[str], None]] = []


def on_path_resaved(fn: Callable[[str], None]) -> None:
    """v175 (PM): call *fn(path_id)* after the owner saves a Library path with the SAME folder (TV takes
    the folder's current shows as its new baseline at the next scan)."""
    if fn not in _resaved_hooks:
        _resaved_hooks.append(fn)


def _path_resaved(pid: str) -> None:
    for fn in list(_resaved_hooks):
        try:
            fn(pid)
        except Exception as e:
            logger.warning('A section did not follow a re-saved library path: %s', type(e).__name__)


def check_library_path(raw: Any, others: List[Dict[str, Any]], probe: bool = True,
                       write: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """(path to store, None) or (None, error) for a library folder. *others*
    are this store's other paths (TV selects from these, it has none of its own).
    v167 (M15): a Library path may be read-only (a TV share Luma only reads);
    the write check is only for a write destination (write=True: a path a
    Movies rule or the chosen fallback files into). probe=False never writes:
    os.access stands in for the create/delete probe."""
    p, err = clean_abs_path(raw)
    if err:
        return None, err
    real = os.path.realpath(p)
    if blocked(real):
        return None, NOT_ALLOWED
    if overlaps_downloads(real):
        return None, IN_DOWNLOADS
    if not os.path.isdir(real):
        return None, NOT_FOUND
    for o in others:
        other = os.path.realpath(o['path'])
        if other == real:
            return None, DUPLICATE
        if _under(real, other) or _under(other, real):
            return None, OVERLAPS
    if write and not (writable(real) if probe else os.access(real, os.W_OK | os.X_OK)):
        return None, NOT_WRITABLE
    return p, None


def _write_target_ok(path: Dict[str, Any]) -> bool:
    """A write destination (rule or fallback) can be written: probed (CIFS can say yes and fail).
    A folder that isn't there right now is left to the mover (it waits for it)."""
    try:
        real = os.path.realpath(path['path'])
        return not os.path.isdir(real) or writable(real)
    except OSError:
        return True


def check_name(raw: Any, others: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(raw, str):
        return None, INVALID
    n = re.sub(r'\s+', ' ', raw).strip()
    if not n:
        return None, REQUIRED
    if len(n) > NAME_MAX or _CTRL_RE.search(raw):
        return None, INVALID
    if any(o['name'].casefold() == n.casefold() for o in others):
        return None, NAME_TAKEN
    return n, None


def path_status(p: Dict[str, Any]) -> str:
    """'ok', 'missing' or 'read_only' (no probe write on a read)."""
    try:
        real = os.path.realpath(p['path'])
        if blocked(real) or not os.path.isdir(real):
            return 'missing'
        return 'ok' if os.access(real, os.W_OK | os.X_OK) else 'read_only'
    except OSError:
        return 'missing'


# --------------------------------------------------------------------------- rules

def check_rules(body: Dict[str, Any], data: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, str]]:
    """Validate {rules, fallback_path_id}. Errors keyed 'rules.<i>.<field>'."""
    errors: Dict[str, str] = {}
    ids = {p['id'] for p in data['paths']}
    raw_rules = body.get('rules')
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        return None, {'rules': INVALID}
    if len(raw_rules) > MAX_RULES:
        return None, {'rules': TOO_MANY}
    old_ids = {r['id'] for r in data['rules']}
    taken = set()
    out = []
    for i, r in enumerate(raw_rules):
        k = f'rules.{i}'
        if not isinstance(r, dict):
            errors[k] = INVALID
            continue
        rid = r.get('id')
        if not (isinstance(rid, str) and _ID_RE.match(rid) and rid.startswith('r_') and rid in old_ids
                and rid not in taken):
            rid = _new_id('r', taken | old_ids)
        taken.add(rid)
        enabled = r.get('enabled', True)
        if not isinstance(enabled, bool):
            errors[f'{k}.enabled'] = INVALID
        pid = r.get('path_id')
        if not pid:
            errors[f'{k}.path_id'] = REQUIRED
        elif pid not in ids:
            errors[f'{k}.path_id'] = INVALID
        rw = r.get('released_within')
        if rw is not None:
            if not isinstance(rw, dict) or rw.get('unit') not in UNITS:
                errors[f'{k}.released_within'] = INVALID
            else:
                n = rw.get('n')
                if isinstance(n, bool) or not isinstance(n, int) or n < 1 or n * UNITS[rw['unit']] > MAX_MONTHS:
                    errors[f'{k}.released_within'] = INVALID
                else:
                    rw = {'n': n, 'unit': rw['unit']}
        lists = {}
        for field, allowed in (('resolution', RESOLUTIONS), ('source', SOURCES)):
            v = r.get(field) or []
            if not isinstance(v, list) or any(x not in allowed for x in v):
                errors[f'{k}.{field}'] = INVALID
                v = []
            lists[field] = [x for x in allowed if x in v]   # canonical order, no duplicates
        out.append({'id': rid, 'enabled': enabled is not False, 'path_id': pid, 'released_within': rw,
                    'resolution': lists['resolution'], 'source': lists['source']})
    fb = body.get('fallback_path_id')
    if fb in ('', None):
        fb = None
        if out:
            errors['fallback_path_id'] = REQUIRED
    elif fb not in ids:
        errors['fallback_path_id'] = INVALID
    # A path is Movies or TV, never both (PM, M22): a rule destination or a
    # fallback newly pointed at a TV-selected path is refused. One that was
    # already there (before this rule) is left for the owner to change.
    tv = section_ids()
    if tv:
        was = {r['id']: r['path_id'] for r in data['rules']}
        for i, r in enumerate(out):
            if r['path_id'] in tv and was.get(r['id']) != r['path_id'] and f'rules.{i}.path_id' not in errors:
                errors[f'rules.{i}.path_id'] = USED_BY_TV
        if fb in tv and fb != data['fallback_path_id'] and 'fallback_path_id' not in errors:
            errors['fallback_path_id'] = USED_BY_TV
    # v167 (M15): Library paths may be read-only; a write destination may not. A rule or the
    # fallback newly pointed at a folder Luma can't write is refused (one already there is left).
    by_id = {p['id']: p for p in data['paths']}
    was_rule = {r['id']: r['path_id'] for r in data['rules']}
    for i, r in enumerate(out):
        k = f'rules.{i}.path_id'
        if k not in errors and r['path_id'] in by_id and was_rule.get(r['id']) != r['path_id'] \
                and not _write_target_ok(by_id[r['path_id']]):
            errors[k] = NOT_WRITABLE
    if fb in by_id and fb != data['fallback_path_id'] and 'fallback_path_id' not in errors \
            and not _write_target_ok(by_id[fb]):
        errors['fallback_path_id'] = NOT_WRITABLE
    if errors:
        return None, errors
    return {'rules': out, 'fallback_path_id': fb}, {}


def movies_uses(data: Dict[str, Any], taken: Optional[set] = None) -> set:
    """Library path ids Movies file into: rule destinations and the fallback,
    explicit or implicit (the single path Movies see; Reviewer F2, M31). The
    one exception: an implicit fallback that is the only Library path at all
    can be selected for TV (PM, Oct 18: a TV-only setup's lone path)."""
    out = {r['path_id'] for r in data['rules']}
    if data.get('fallback_path_id'):
        out.add(data['fallback_path_id'])
    fb = None if lone_path(data) else effective_fallback(data, taken)
    if fb:
        out.add(fb['id'])
    return out


def lone_path(data: Dict[str, Any]) -> bool:
    """One Library path and no chosen fallback: it may be TV's (F2)."""
    return len(data['paths']) == 1 and not data.get('fallback_path_id')


def pin_fallback(data: Dict[str, Any], taken: Optional[set] = None, *, adding: bool = False) -> bool:
    """Make an implicit fallback explicit before a change that would move or
    drop it (a path added, a TV selection changed), as the migration does
    (Reviewer F1). Always, except a selection change while the implicit
    fallback is the only Library path (F2: the lone path can be TV's; there is
    nothing for it to move to). True when *data* changed."""
    if data.get('fallback_path_id') or (lone_path(data) and not adding):
        return False
    fb = effective_fallback(data, taken)
    if not fb:
        return False
    if read_only(fb):                     # v167: a read-only Library path is never made a write destination
        return False
    data['fallback_path_id'] = fb['id']
    return True


def section_ids() -> set:
    """Library path ids another section (TV) selected: never Movie destinations."""
    from app import library_selection
    return library_selection.claimed_ids()


def movies_only(data: Dict[str, Any], taken: Optional[set] = None) -> Dict[str, Any]:
    """*data* as Movies see it: without the paths a section selected, rules
    pointing at them, or a fallback that is one of them. Movies then file
    exactly as they did before TV selected Library paths."""
    taken = section_ids() if taken is None else taken
    if not taken:
        return data
    out = dict(data)
    out['paths'] = [p for p in data['paths'] if p['id'] not in taken]
    out['rules'] = [r for r in data['rules'] if r.get('path_id') not in taken]
    if data.get('fallback_path_id') in taken:
        out['fallback_path_id'] = None
    return out


def read_only(path: Dict[str, Any]) -> bool:
    """A Library path that is there and can't be written (v169). A folder that isn't there right
    now is not read-only (the mover waits for it)."""
    try:
        real = os.path.realpath(path['path'])
        return os.path.isdir(real) and not os.access(real, os.W_OK | os.X_OK)
    except (OSError, KeyError, TypeError):
        return False


def effective_fallback(data: Dict[str, Any], taken: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """The fallback path; with exactly one Movies path and none chosen, that
    one, unless it is read-only (v169, Reviewer: a read-only Library path is
    never the implicit fallback). Paths a section (TV) selected are never it."""
    data = movies_only(data, taken)
    by_id = {p['id']: p for p in data['paths']}
    if data.get('fallback_path_id') in by_id:
        return by_id[data['fallback_path_id']]
    if len(data['paths']) == 1 and not read_only(data['paths'][0]):
        return data['paths'][0]
    return None


def months_before(today: date, months: int) -> date:
    y, m = divmod(today.year * 12 + (today.month - 1) - months, 12)
    m += 1
    return date(y, m, min(today.day, calendar.monthrange(y, m)[1]))


def rule_matches(rule: Dict[str, Any], info: Dict[str, Any], today: date) -> bool:
    rw = rule.get('released_within')
    if rw:
        released = info.get('release_date')
        if not isinstance(released, date):
            return False
        if released < months_before(today, rw['n'] * UNITS[rw['unit']]):
            return False
    if rule.get('resolution') and info.get('resolution') not in rule['resolution']:
        return False
    if rule.get('source') and info.get('source') not in rule['source']:
        return False
    return True


def pick_destination(data: Dict[str, Any], info: Dict[str, Any],
                     today: Optional[date] = None, taken: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """First enabled rule that matches, else the fallback; None when neither.
    Paths a section (TV) selected are left out (movies_only)."""
    today = today or date.today()
    taken = section_ids() if taken is None else taken
    data = movies_only(data, taken)
    by_id = {p['id']: p for p in data['paths']}
    for r in data['rules']:
        if r.get('enabled') and r.get('path_id') in by_id and rule_matches(r, info, today):
            return by_id[r['path_id']]
    return effective_fallback(data, taken)


def no_writable_destination(data: Dict[str, Any], taken: Optional[set] = None) -> bool:
    """pick_destination found nothing because the Movies paths there are read-only (v169): the
    mover flags the item 'No writable destination' instead of skipping it as 'no destination'."""
    # v171 (Tester FF-4): only when read-only is the true cause, i.e. the one Movies path would have been
    # the implicit fallback but is read-only. Mixed paths with no matching rule and no fallback chosen
    # are plain 'no destination'.
    d = movies_only(data, taken)
    if d.get('fallback_path_id') in {p['id'] for p in d['paths']}:
        return False
    return len(d['paths']) == 1 and read_only(d['paths'][0])


def needs_release_date(data: Dict[str, Any]) -> bool:
    return any(r.get('enabled') and r.get('released_within') for r in movies_only(data)['rules'])


# ------------------------------------------------------------------ release names
# Ported from prod: auto_rename.detect_video_quality (remux / 2160p|4k|uhd /
# 1080p) and indexer_api._SOURCE_RE, with SD, WEB, HDTV and BluRay encodes
# added as their own buckets.

# (label, pattern, tail_only): tail_only words also occur in titles
# ("Charlotte's Web", "Pal Joey"), so they count only after the year or
# resolution, where release tags sit.
_RES = (
    ('2160p', re.compile(r'\b2160p\b'), False),
    ('2160p', re.compile(r'\b(4k|uhd)\b'), True),
    ('1080p', re.compile(r'\b1080[pi]\b'), False),
    ('720p', re.compile(r'\b720p\b'), False),
    ('sd', re.compile(r'\b(480[pi]|576[pi]|360p)\b'), False),
    ('sd', re.compile(r'\b(sd|dvdrip|dvd|ntsc|pal)\b'), True),
)
_SRC = (
    ('remux', re.compile(r'\b(remux|bd ?remux)\b'), False),
    ('web', re.compile(r'\b(web[ -]?dl|webdl|web[ -]?rip|webrip)\b'), False),
    ('hdtv', re.compile(r'\b(hdtv|pdtv|sdtv)\b'), False),
    ('bluray', re.compile(r'\b(blu[ -]?ray|bluray|bdrip|brrip|bd25|bd50)\b'), False),
    ('web', re.compile(r'\bweb\b'), True),
)
_TAIL_START = re.compile(r'\b((19|20)\d{2}|2160p|1080[pi]|720p|480[pi]|576[pi]|s\d{1,2}e\d{1,3})\b')


def _norm_name(name: str) -> str:
    # No f-string: a backslash inside an f-string expression is a SyntaxError
    # before 3.12, and the image runs 3.11.
    return ' ' + re.sub(r'[._()\[\]]+', ' ', (name or '').lower()) + ' '


def classify(*names: str) -> Dict[str, Optional[str]]:
    """{'resolution', 'source'} from the release name, then the file name for
    whatever the release name doesn't say. None when neither says."""
    out: Dict[str, Optional[str]] = {'resolution': None, 'source': None}
    for name in names:
        t = _norm_name(name)
        m = _TAIL_START.search(t)
        tail = t[m.start():] if m else ''
        for key, table in (('resolution', _RES), ('source', _SRC)):
            if out[key] is None:
                for label, rx, tail_only in table:
                    if rx.search(tail if tail_only else t):
                        out[key] = label
                        break
    return out


# ------------------------------------------------------------------ naming
# Prod's naming: "Title (Year)/Title (Year).ext" (auto_rename
# generate_destination_path), with sanitize_filename hardened so a title can
# never be '.', '..', empty, a hidden name or carry a separator.

def safe_component(s: Any, fallback: str = 'Movie') -> str:
    s = s if isinstance(s, str) else ''
    s = re.sub(r'[<>"/\\|?*]', '', s.replace(':', '-'))   # prod's sanitize_filename
    s = _CTRL_RE.sub('', s)
    s = re.sub(r'\s+', ' ', s).strip().strip('.').strip()
    while len(s.encode('utf-8')) > 180:
        s = s[:-1]
    s = s.rstrip(' .')
    if not s or s in ('.', '..'):
        return fallback
    return s


def movie_names(title: Any, year: Any, ext: str, part: Optional[int] = None) -> Tuple[str, str]:
    """(folder, file name) for a movie file; part numbers use Plex's ' - pt1'."""
    clean = safe_component(title)
    y = str(year) if isinstance(year, (str, int)) and re.fullmatch(r'\d{4}', str(year)) else ''
    base = f'{clean} ({y})' if y else clean
    suffix = f' - pt{part}' if part else ''
    ext = ext.lower() if re.fullmatch(r'\.[a-z0-9]{1,5}', (ext or '').lower()) else ''
    return base, f'{base}{suffix}{ext}'


# ------------------------------------------------------------------ path map

def check_path_map(raw: Any) -> Tuple[Optional[List[Dict[str, str]]], Dict[str, str]]:
    if raw is None:
        return [], {}
    if not isinstance(raw, list):
        return None, {'path_map': INVALID}
    if len(raw) > MAX_MAPS:
        return None, {'path_map': TOO_MANY}
    errors: Dict[str, str] = {}
    out = []
    seen = set()
    for i, m in enumerate(raw):
        if not isinstance(m, dict):
            errors[f'path_map.{i}'] = INVALID
            continue
        remote, e1 = clean_abs_path(m.get('remote'))
        local, e2 = clean_abs_path(m.get('local'))
        if e1:
            errors[f'path_map.{i}.remote'] = e1
        elif remote in seen:
            errors[f'path_map.{i}.remote'] = DUPLICATE
        if e2:
            errors[f'path_map.{i}.local'] = e2
        else:
            real = os.path.realpath(local)
            if blocked(real):
                errors[f'path_map.{i}.local'] = NOT_ALLOWED
            elif not os.path.isdir(real):
                errors[f'path_map.{i}.local'] = NOT_FOUND
        if not e1 and not e2:
            seen.add(remote)
            out.append({'remote': remote, 'local': local})
    if errors:
        return None, errors
    return out, {}


def map_path(path: str, path_map: List[Dict[str, str]]) -> str:
    """qBittorrent's path as Luma sees it: longest matching remote prefix,
    whole segments only; unchanged when nothing matches."""
    best = None
    for m in path_map:
        r = m['remote'].rstrip('/') or '/'
        if r == '/' or path == r or path.startswith(r + '/'):
            if best is None or len(r) > len(best[0]):
                best = (r, m['local'].rstrip('/') or '/')
    if not best:
        return path
    r, local = best
    rest = path[len(r):] if r != '/' else path
    return (local.rstrip('/') + '/' + rest.lstrip('/')) if rest.strip('/') else local


# ------------------------------------------------------------------ routes

def init(*, settings, owner_or_error: Callable, store_path: Optional[str] = None) -> None:
    _cfg.update(settings=settings, owner_or_error=owner_or_error)
    if store_path:
        _cfg['store_path'] = store_path


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _guard():
    """None when the caller may use these routes; else an error response.
    Minimal builds only (prod keeps its own Media Paths), owner only."""
    from app import minimal
    if not minimal.enabled():
        return jsonify({'error': 'Not found'}), 404
    _user, err = _cfg['owner_or_error']()
    return err


def view(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """'used_by' lists the sections (TV) that selected a path: the Movies rule
    and fallback choices leave those out."""
    from app import library_selection
    data = data if data is not None else load()
    used = library_selection.claimed()
    return {
        'paths': [{**p, 'status': path_status(p), 'used_by': used.get(p['id'], [])} for p in data['paths']],
        'rules': copy.deepcopy(data['rules']),
        'fallback_path_id': data['fallback_path_id'],
        'path_map': copy.deepcopy(data['path_map']),
        'transfer': data['transfer'],
        'downloads': downloads_status(),
        'not_found': sorted(
            ({'title': j.get('title') or '', 'since': j.get('pending_since') or 0,
              'reason': 'not_mapped' if j.get('status') == 'path not mapped' else 'not_found'}
             for j in data['jobs'].values()
             if j.get('state') == 'waiting' and j.get('status') in ('files not found', 'path not mapped')),
            key=lambda x: x['since'])[:50],
        # grabs the mover gave up on for space: no timer retries them (Retry, or room on the destination)
        'failed': sorted(
            ({'hash': h, 'title': j.get('title') or '', 'since': j.get('finished') or 0, 'reason': 'no_space'}
             for h, j in data['jobs'].items()
             if j.get('state') == 'failed' and j.get('error') == 'insufficient space'),
            key=lambda x: x['since'])[:50],
    }


def downloads_status() -> Dict[str, str]:
    """{'path', 'status'}: the downloads folder the mover reads, 'ok' when it
    is a readable folder, else 'missing'."""
    root = downloads_root()
    try:
        ok = not blocked(root) and os.path.isdir(root) and os.access(root, os.R_OK | os.X_OK)
    except OSError:
        ok = False
    return {'path': root, 'status': 'ok' if ok else 'missing'}


def requeue_path_errors(data: Dict[str, Any]) -> int:
    """Put jobs skipped for a path problem back to waiting (after the path map
    changes). Returns how many."""
    n = 0
    for j in data['jobs'].values():
        if j.get('state') == 'skipped' and j.get('error') in PATH_ERRORS:
            j.update(state='waiting', error='', attempts=0, misses=0, status='', pending_since=None)
            j.pop('finished', None)
            n += 1
    return n


def _body() -> Dict[str, Any]:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


@bp.route('/api/settings/library-paths', methods=['GET'])
def library_paths_get():
    err = _guard()
    if err:
        return err
    return _no_store(jsonify(view()))


@bp.route('/api/settings/library-paths', methods=['POST'])
def library_paths_add():
    err = _guard()
    if err:
        return err
    body = _body()

    def apply(data):
        if len(data['paths']) >= MAX_PATHS:
            return None, {'path': TOO_MANY}
        name, e1 = check_name(body.get('name'), data['paths'])
        path, e2 = check_library_path(body.get('path'), data['paths'])
        errors = {k: v for k, v in (('name', e1), ('path', e2)) if v}
        if errors:
            return None, errors
        rec = {'id': _new_id('p', {p['id'] for p in data['paths']}), 'name': name, 'path': path}
        pin_fallback(data, adding=True)  # the single Movies path stays the fallback (F1)
        data['paths'].append(rec)
        return rec, {}

    with _lock:
        data = load()
        rec, errors = apply(data)
        if errors:
            return _no_store(jsonify({'errors': errors}), 400)
        save(data)
        logger.info('Library path added: %s', rec['name'])
        return _no_store(jsonify(view(data)), 201)


@bp.route('/api/settings/library-paths/browse', methods=['GET'])
def library_paths_browse():
    err = _guard()
    if err:
        return err
    out, e = browse(request.args.get('path'))
    if e:
        return _no_store(jsonify({'error': e}), 400)
    return _no_store(jsonify(out))


@bp.route('/api/settings/library-paths/<pid>', methods=['PUT'])
def library_paths_edit(pid):
    err = _guard()
    if err:
        return err
    body = _body()
    with _lock:
        data = load()
        rec = next((p for p in data['paths'] if p['id'] == pid), None)
        if not rec:
            return _no_store(jsonify({'error': 'Not found'}), 404)
        others = [p for p in data['paths'] if p['id'] != pid]
        errors = {}
        name, path = rec['name'], rec['path']
        if 'name' in body:
            name, e = check_name(body.get('name'), others)
            if e:
                errors['name'] = e
        if 'path' in body:
            # a Movies rule or the chosen fallback files into it: the new folder must be writable
            dest = data['fallback_path_id'] == pid or any(r['path_id'] == pid for r in data['rules'])
            path, e = check_library_path(body.get('path'), others, write=dest)
            if e:
                errors['path'] = e
        if errors:
            return _no_store(jsonify({'errors': errors}), 400)
        moved = os.path.realpath(path) != os.path.realpath(rec['path'])
        # v175 (PM): saved again with the same folder AND the same name clears the mount record (a
        # rebaseline); a rename (name changed, same path) is only a rename and never clears it.
        resaved = 'path' in body and not moved and name == rec['name']
        rec.update(name=name, path=path)
        save(data)
    if moved:
        _path_repointed(pid)
    elif resaved:
        _path_resaved(pid)
    return _no_store(jsonify(view(data)))


@bp.route('/api/settings/library-paths/<pid>', methods=['DELETE'])
def library_paths_delete(pid):
    err = _guard()
    if err:
        return err
    with _lock:
        data = load()
        if not any(p['id'] == pid for p in data['paths']):
            return _no_store(jsonify({'error': 'Not found'}), 404)
        if data['fallback_path_id'] == pid or any(r['path_id'] == pid for r in data['rules']):
            return _no_store(jsonify({'error': IN_USE}), 409)
        data['paths'] = [p for p in data['paths'] if p['id'] != pid]
        save(data)
    _path_removed(pid)
    return _no_store(jsonify(view(data)))


@bp.route('/api/settings/library-rules', methods=['PUT'])
def library_rules_put():
    err = _guard()
    if err:
        return err
    body = _body()
    with _lock:
        data = load()
        out, errors = check_rules(body, data)
        if errors:
            return _no_store(jsonify({'errors': errors}), 400)
        data.update(out)
        save(data)
        return _no_store(jsonify(view(data)))


@bp.route('/api/settings/library-paths/jobs/<h>/retry', methods=['POST'])
def library_job_retry(h):
    """Manual Retry for a grab the mover failed (e.g. not enough space)."""
    err = _guard()
    if err:
        return err
    from app import library_mover
    if not re.match(r'^([0-9a-fA-F]{40}|[0-9a-fA-F]{64})$', h or '') or not library_mover.retry(h):
        return _no_store(jsonify({'error': 'Not found'})), 404
    return _no_store(jsonify(view()))


@bp.route('/api/settings/library-paths/mapping', methods=['PUT'])
def library_mapping_put():
    err = _guard()
    if err:
        return err
    body = _body()
    with _lock:
        data = load()
        errors: Dict[str, str] = {}
        path_map = data['path_map']
        if 'path_map' in body:
            path_map, errors = check_path_map(body.get('path_map'))
        transfer = body.get('transfer', data['transfer'])
        if transfer not in TRANSFERS:
            errors['transfer'] = INVALID
        if errors:
            return _no_store(jsonify({'errors': errors}), 400)
        changed = path_map != data['path_map']
        data['path_map'], data['transfer'] = path_map, transfer
        if changed:
            n = requeue_path_errors(data)
            if n:
                logger.info('Library mover: %d skipped grab(s) back to waiting after a path map change', n)
        save(data)
        return _no_store(jsonify(view(data)))
