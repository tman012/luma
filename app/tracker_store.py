"""Storage for the owner's trackers.

Trackers live in $LUMA_DATA_DIR/trackers.json (mode 0600). Secret fields
(API keys, passkeys, PIDs, passwords, cookies) are encrypted at rest with a
per-install key in $LUMA_DATA_DIR/tracker_secrets.key (mode 0600, created on
first use) and are only decrypted server-side when a tracker is contacted.

The client never receives a secret: public() replaces every stored secret
with SECRET_MASK. On an edit, a secret field sent back as SECRET_MASK keeps
the stored value. Secrets are never logged.

A stored secret only ever goes to the URL it was saved with. Changing a
tracker's URL clears its stored secrets (the owner re-enters them), and a
Test of a form whose URL differs from the saved one never uses them.

A fresh install has no trackers file and therefore zero trackers; nothing is
seeded.
"""

from __future__ import annotations

import copy
import fcntl
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import tracker_types as tt

logger = logging.getLogger(__name__)

SECRET_MASK = '********'      # same sentinel as app.settings_manager
STORE_FILENAME = 'trackers.json'
KEY_FILENAME = 'tracker_secrets.key'
STATUS_OK, STATUS_ERROR, STATUS_UNTESTED = 'ok', 'error', 'untested'
STATUS_MESSAGE_MAX = 80
MAX_TRACKERS = 100


class CredentialsUnreadable(Exception):
    """A stored secret can't be decrypted (the key file was lost or replaced)."""


def _data_dir() -> Path:
    from app.first_run import data_dir
    return Path(data_dir())


GENERIC_TYPES = ('torznab', 'newznab')


def _url_allowed(type_id: str, url: str) -> bool:
    """Site types need https; no type may name localhost, a blocked IP
    literal or a blocked port (host names are checked again on every request)."""
    from urllib.parse import urlsplit
    from app.tracker_http import host_literal_blocked
    if type_id not in GENERIC_TYPES and urlsplit(url).scheme != 'https':
        return False
    return not host_literal_blocked(url)


def _has_control(s: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in s)


class TrackerStore:
    def __init__(self, path: Optional[os.PathLike] = None, key_path: Optional[os.PathLike] = None):
        self._path = Path(path) if path else None
        self._key_path = Path(key_path) if key_path else None
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {'version': 1, 'next_id': 1, 'trackers': []}
        self._stamp: Optional[Tuple[int, ...]] = None
        self._fernet = None

    # ------------------------------------------------------------ files

    @property
    def path(self) -> Path:
        return self._path or (_data_dir() / STORE_FILENAME)

    @property
    def key_path(self) -> Path:
        return self._key_path or (_data_dir() / KEY_FILENAME)

    @contextmanager
    def _file_lock(self):
        """Serialize read-modify-write across threads and worker processes."""
        with self._lock:
            lock_path = self.path.with_name('.' + self.path.name + '.lock')
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    def _reload(self) -> None:
        """Re-read the file (another worker may have written it). Always a
        full read: an mtime/size check can miss a same-size write within the
        filesystem's timestamp granularity. The file is small (at most
        MAX_TRACKERS records) and callers hold the file lock."""
        import json
        from app.paths import quarantine
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._data = {'version': 1, 'next_id': 1, 'trackers': []}
            self._stamp = None
            return
        stamp = (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)
        try:
            with open(self.path, encoding='utf-8') as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict) or not isinstance(raw.get('trackers'), list):
                raise ValueError('wrong shape')
        except (OSError, ValueError, UnicodeDecodeError) as e:
            quarantine(self.path, f'unreadable: {type(e).__name__}', logger)
            self._data = {'version': 1, 'next_id': 1, 'trackers': []}
            self._stamp = None
            return
        trackers = [t for t in raw['trackers'] if self._valid_record(t)]
        if len(trackers) != len(raw['trackers']):
            logger.warning('Trackers: dropped %d invalid entr%s', len(raw['trackers']) - len(trackers),
                           'y' if len(raw['trackers']) - len(trackers) == 1 else 'ies')
        next_id = raw.get('next_id')
        top = max([t['id'] for t in trackers] or [0])
        if isinstance(next_id, bool) or not isinstance(next_id, int) or next_id <= top:
            next_id = top + 1
        self._data = {'version': 1, 'next_id': next_id, 'trackers': trackers}
        self._stamp = stamp

    @staticmethod
    def _valid_record(t: Any) -> bool:
        return (isinstance(t, dict) and isinstance(t.get('id'), int) and not isinstance(t.get('id'), bool)
                and t['id'] > 0 and tt.get_type(t.get('type')) is not None
                and isinstance(t.get('name'), str) and isinstance(t.get('secrets', {}), dict)
                and isinstance(t.get('settings', {}), dict))

    def _save(self) -> None:
        from app.paths import atomic_write_json
        atomic_write_json(self.path, self._data, mode=0o600)
        st = os.stat(self.path)
        self._stamp = (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)

    # ------------------------------------------------------------ crypto

    def _cipher(self, create: bool):
        if self._fernet is not None:
            return self._fernet
        from cryptography.fernet import Fernet
        kp = self.key_path
        try:
            key = kp.read_bytes().strip()
        except FileNotFoundError:
            if not create:
                return None
            kp.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            try:
                fd = os.open(str(kp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                key = kp.read_bytes().strip()      # another worker just made it
            else:
                with os.fdopen(fd, 'wb') as fh:
                    fh.write(key)
                    fh.flush()
                    os.fsync(fh.fileno())
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError):
            logger.error('Tracker secret key file is invalid; stored credentials are unreadable')
            return None
        return self._fernet

    def _encrypt(self, value: str) -> str:
        f = self._cipher(create=True)
        if f is None:
            raise CredentialsUnreadable()
        return f.encrypt(value.encode('utf-8')).decode('ascii')

    def _decrypt(self, token: str) -> str:
        from cryptography.fernet import InvalidToken
        f = self._cipher(create=False)
        if f is None:
            raise CredentialsUnreadable()
        try:
            return f.decrypt(str(token).encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError, UnicodeError):
            raise CredentialsUnreadable() from None

    # ------------------------------------------------------------ views

    @staticmethod
    def public(rec: Dict[str, Any]) -> Dict[str, Any]:
        """A tracker as the client may see it: secrets masked, never the values."""
        t = tt.get_type(rec['type']) or {}
        out: Dict[str, Any] = {
            'id': rec['id'], 'type': rec['type'], 'type_name': t.get('name', rec['type']),
            'protocol': t.get('protocol', 'torrent'),
            'name': rec['name'], 'enabled': bool(rec.get('enabled', True)), 'url': rec.get('url', ''),
            'categories': list(rec.get('categories') or []), 'priority': rec.get('priority', tt.PRIORITY_DEFAULT),
        }
        for k in tt.setting_keys(rec['type']):
            out[k] = (rec.get('settings') or {}).get(k, '')
        for k in tt.secret_keys(rec['type']):
            out[k] = SECRET_MASK if (rec.get('secrets') or {}).get(k) else ''
        st = rec.get('status') or {}
        out['status'] = {'state': st.get('state', STATUS_UNTESTED), 'message': st.get('message', ''),
                         'checked_at': st.get('checked_at')}
        return out

    def list(self) -> List[Dict[str, Any]]:
        with self._file_lock():
            self._reload()
            return copy.deepcopy(self._data['trackers'])

    def list_public(self) -> List[Dict[str, Any]]:
        recs = sorted(self.list(), key=lambda r: (r.get('priority', tt.PRIORITY_DEFAULT), r['name'].lower()))
        return [self.public(r) for r in recs]

    def get(self, tracker_id: int) -> Optional[Dict[str, Any]]:
        for r in self.list():
            if r['id'] == tracker_id:
                return r
        return None

    def config(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """Full connection config with decrypted secrets, for server-side use only.
        Raises CredentialsUnreadable when a stored secret can't be decrypted."""
        cfg = {'id': rec.get('id'), 'type': rec['type'], 'name': rec['name'], 'url': rec.get('url', ''),
               'categories': list(rec.get('categories') or []),
               'priority': rec.get('priority', tt.PRIORITY_DEFAULT), 'enabled': bool(rec.get('enabled', True))}
        cfg.update(rec.get('settings') or {})
        for k, token in (rec.get('secrets') or {}).items():
            if token:
                cfg[k] = self._decrypt(token)
        return cfg

    def enabled_configs(self) -> List[Dict[str, Any]]:
        """Configs of all enabled trackers whose secrets can be read."""
        out = []
        for rec in self.list():
            if not rec.get('enabled', True):
                continue
            try:
                out.append(self.config(rec))
            except CredentialsUnreadable:
                logger.warning('Tracker %s (id %s): stored credentials are unreadable', rec['name'], rec['id'])
        return sorted(out, key=lambda c: (c['priority'], c['name'].lower()))

    # ------------------------------------------------------------ validation

    def _build(self, payload: Dict[str, Any], existing: Optional[Dict[str, Any]],
               others: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, Optional[str]]]:
        """Validate *payload* against the type schema.

        Returns (record without secrets, field errors, secret changes) where a
        secret change maps key -> new plaintext value, or None to clear it.
        Secret fields sent as SECRET_MASK keep the stored value."""
        errors: Dict[str, str] = {}
        if not isinstance(payload, dict):
            return {}, {'_': tt.INVALID}, {}
        type_id = str(payload.get('type') or (existing or {}).get('type') or '').strip().lower()
        if existing and 'type' in payload and type_id != existing['type']:
            errors['type'] = tt.INVALID
            type_id = existing['type']
        tdef = tt.get_type(type_id)
        if tdef is None:
            return {}, {'type': tt.REQUIRED if not type_id else tt.INVALID}, {}
        base = existing or {}

        def present(k):
            return k in payload

        rec: Dict[str, Any] = {'type': type_id}
        # name
        name = ' '.join(str(payload.get('name', base.get('name', '')) or '').split())
        if not name:
            errors['name'] = tt.REQUIRED
        elif len(name) > tt.NAME_MAX:
            errors['name'] = tt.TOO_LONG
        elif _has_control(name):
            errors['name'] = tt.INVALID
        elif any(o['name'].lower() == name.lower() and o['id'] != base.get('id') for o in others):
            errors['name'] = 'Name in use'
        rec['name'] = name
        # url
        raw_url = payload.get('url', base.get('url', ''))
        if not str(raw_url or '').strip():
            errors['url'] = tt.REQUIRED
            rec['url'] = ''
        else:
            url = tt.normalize_url(raw_url)
            if url is None or not _url_allowed(type_id, url):
                errors['url'] = tt.INVALID_URL
            rec['url'] = url or ''
        # Stored secrets are bound to the saved URL: a new URL drops them.
        url_changed = existing is not None and rec['url'] != existing.get('url')
        # enabled
        enabled = payload.get('enabled', base.get('enabled', True))
        if not isinstance(enabled, bool):
            errors['enabled'] = tt.INVALID
            enabled = True
        rec['enabled'] = enabled
        # categories
        if present('categories'):
            cats = tt.validate_categories(type_id, payload['categories'])
            if cats is None:
                errors['categories'] = tt.REQUIRED if not payload['categories'] else tt.INVALID
        else:
            cats = list(base.get('categories') or tt.DEFAULT_CATEGORIES)
            cats = tt.validate_categories(type_id, cats) or [c for c, _ in tdef['categories']][:2]
        rec['categories'] = cats or []
        # priority
        if present('priority'):
            pr = tt.validate_priority(payload['priority'])
            if pr is None:
                errors['priority'] = tt.INVALID
        else:
            pr = base.get('priority', tt.PRIORITY_DEFAULT)
        rec['priority'] = pr if pr is not None else tt.PRIORITY_DEFAULT
        # per-type text settings
        fields = {f['key']: f for f in tdef['fields']}
        settings: Dict[str, str] = {}
        for k in tt.setting_keys(type_id):
            v = payload.get(k, (base.get('settings') or {}).get(k, ''))
            v = '' if v is None else v
            if not isinstance(v, str):
                errors[k] = tt.INVALID
                continue
            v = v.strip()
            if len(v) > tt.VALUE_MAX:
                errors[k] = tt.TOO_LONG
            elif _has_control(v):
                errors[k] = tt.INVALID
            elif not v and fields[k]['required']:
                errors[k] = tt.REQUIRED
            if v:
                settings[k] = v
        rec['settings'] = settings
        # secrets
        changes: Dict[str, Optional[str]] = {}
        stored = base.get('secrets') or {}
        for k in tt.secret_keys(type_id):
            has_stored = bool(stored.get(k)) and not url_changed
            if url_changed and stored.get(k):
                changes[k] = None           # cleared unless a new value follows
            if k not in payload or payload[k] == SECRET_MASK:
                if not has_stored and fields[k]['required']:
                    errors[k] = tt.REQUIRED
                continue
            v = payload[k]
            v = '' if v is None else v
            if not isinstance(v, str):
                errors[k] = tt.INVALID
                continue
            v = v.strip()
            if not v:
                if fields[k]['required']:
                    errors[k] = tt.REQUIRED
                elif has_stored or (url_changed and stored.get(k)):
                    changes[k] = None
                continue
            if len(v) > tt.VALUE_MAX:
                errors[k] = tt.TOO_LONG
            elif _has_control(v):
                errors[k] = tt.INVALID
            else:
                changes[k] = v
        unknown = set(payload) - set(fields) - {'type', 'enabled', 'id', 'status', 'type_name', 'protocol'}
        for k in unknown:
            errors[k] = tt.INVALID
        return rec, errors, changes

    def _connection_changed(self, old: Dict[str, Any], new: Dict[str, Any], changes) -> bool:
        return (old.get('url') != new.get('url') or (old.get('settings') or {}) != (new.get('settings') or {})
                or bool(changes))

    # ------------------------------------------------------------ CRUD

    def create(self, payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, str]]:
        with self._file_lock():
            self._reload()
            others = self._data['trackers']
            if len(others) >= MAX_TRACKERS:
                return None, {'_': 'Too many trackers'}
            rec, errors, changes = self._build(payload, None, others)
            if errors:
                return None, errors
            secrets: Dict[str, str] = {}
            for k, v in changes.items():
                if v is not None:
                    secrets[k] = self._encrypt(v)
            now = time.time()
            rec.update(id=self._data['next_id'], secrets=secrets, created_at=now, updated_at=now,
                       status={'state': STATUS_UNTESTED, 'message': '', 'checked_at': None})
            self._data['next_id'] += 1
            self._data['trackers'].append(rec)
            self._save()
        logger.info('Tracker added: %s (id %s, %s)', rec['name'], rec['id'], rec['type'])
        return copy.deepcopy(rec), {}

    def update(self, tracker_id: int, payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, str]]:
        with self._file_lock():
            self._reload()
            cur = next((t for t in self._data['trackers'] if t['id'] == tracker_id), None)
            if cur is None:
                return None, {'_': 'Not found'}
            rec, errors, changes = self._build(payload, cur, self._data['trackers'])
            if errors:
                return None, errors
            secrets = {k: v for k, v in (cur.get('secrets') or {}).items() if k in tt.secret_keys(rec['type'])}
            for k, v in changes.items():
                if v is None:
                    secrets.pop(k, None)
                else:
                    secrets[k] = self._encrypt(v)
            # A secret re-sent unchanged in plain text is not a change.
            real_changes = {}
            for k, v in changes.items():
                old = (cur.get('secrets') or {}).get(k)
                try:
                    same = old is not None and v is not None and self._decrypt(old) == v
                except CredentialsUnreadable:
                    same = False
                if not same:
                    real_changes[k] = v
            status = cur.get('status') or {}
            if self._connection_changed(cur, rec, real_changes):
                status = {'state': STATUS_UNTESTED, 'message': '', 'checked_at': None}
            rec.update(id=cur['id'], secrets=secrets, created_at=cur.get('created_at'),
                       updated_at=time.time(), status=status)
            idx = self._data['trackers'].index(cur)
            self._data['trackers'][idx] = rec
            self._save()
        logger.info('Tracker updated: %s (id %s)', rec['name'], rec['id'])
        return copy.deepcopy(rec), {}

    def set_enabled(self, tracker_id: int, enabled: bool) -> Optional[Dict[str, Any]]:
        with self._file_lock():
            self._reload()
            cur = next((t for t in self._data['trackers'] if t['id'] == tracker_id), None)
            if cur is None:
                return None
            cur['enabled'] = bool(enabled)
            cur['updated_at'] = time.time()
            self._save()
            out = copy.deepcopy(cur)
        logger.info('Tracker %s: %s (id %s)', 'enabled' if enabled else 'disabled', out['name'], out['id'])
        return out

    def delete(self, tracker_id: int) -> bool:
        with self._file_lock():
            self._reload()
            cur = next((t for t in self._data['trackers'] if t['id'] == tracker_id), None)
            if cur is None:
                return False
            self._data['trackers'].remove(cur)
            self._save()
        logger.info('Tracker deleted: %s (id %s)', cur['name'], cur['id'])
        return True

    def set_status(self, tracker_id: int, state: str, message: str = '') -> None:
        if state not in (STATUS_OK, STATUS_ERROR, STATUS_UNTESTED):
            raise ValueError(state)
        with self._file_lock():
            self._reload()
            cur = next((t for t in self._data['trackers'] if t['id'] == tracker_id), None)
            if cur is None:
                return
            new = {'state': state, 'message': str(message or '')[:STATUS_MESSAGE_MAX], 'checked_at': time.time()}
            old = cur.get('status') or {}
            if old.get('state') == new['state'] and old.get('message') == new['message'] \
                    and (old.get('checked_at') or 0) > new['checked_at'] - 60:
                return          # no write per search while nothing changed
            cur['status'] = new
            self._save()

    def candidate_config(self, payload: Dict[str, Any], tracker_id: Optional[int] = None
                         ) -> Tuple[Optional[Dict[str, Any]], Dict[str, str]]:
        """Config for testing an unsaved form: *payload* validated like a
        create (or an edit of *tracker_id*, whose stored secrets fill masked
        fields). Nothing is written."""
        with self._file_lock():
            self._reload()
            others = self._data['trackers']
            cur = None
            if tracker_id is not None:
                cur = next((t for t in others if t['id'] == tracker_id), None)
                if cur is None:
                    return None, {'_': 'Not found'}
            rec, errors, changes = self._build(payload, cur, others)
            if errors.get('name') == 'Name in use':
                errors.pop('name')          # testing an unsaved copy is fine
            if errors:
                return None, errors
            same_url = cur is not None and rec['url'] == cur.get('url')
            # A form pointing somewhere else is a different tracker: no stored
            # secret, and its Test counts against that host, not this id.
            cfg = {'id': tracker_id if same_url else None, 'type': rec['type'], 'name': rec['name'],
                   'url': rec['url'],
                   'categories': rec['categories'], 'priority': rec['priority'], 'enabled': rec['enabled']}
            cfg.update(rec['settings'])
            stored = (cur or {}).get('secrets') or {}
            for k in tt.secret_keys(rec['type']):
                if k in changes:
                    if changes[k] is not None:
                        cfg[k] = changes[k]
                elif same_url and stored.get(k):
                    cfg[k] = self._decrypt(stored[k])
            return cfg, {}


_store: Optional[TrackerStore] = None
_store_lock = threading.Lock()


def get_store() -> TrackerStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = TrackerStore()
        return _store


def reset_store_for_tests(store: Optional[TrackerStore] = None) -> None:
    global _store
    with _store_lock:
        _store = store
