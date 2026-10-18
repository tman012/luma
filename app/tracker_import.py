"""Import from Prowlarr (v170, tracker port step 2). Optional, owner-triggered, one-time per tracker.

Nothing here runs on its own: no boot hook, no timer, no seeding. The owner presses Import in
Settings > Trackers (offered only while a Prowlarr connection is configured):

1. preview(): ONE read-only GET of /api/v1/indexer on the exact Prowlarr URL in Settings > Indexer
   (no redirects, no environment proxy). Each indexer is mapped to a Luma tracker type (or "Not
   supported yet"; never proxied). The answer, credentials included, stays on the server for
   PREVIEW_TTL_S under a random id; the browser only sees names, types and states.
2. create(): the trackers the owner ticked are created DISABLED with a Test required before they
   can be enabled (tracker_store). Credentials go straight into the encrypted store and are never
   returned or logged; one Prowlarr hands over masked ('********') is left empty and the tracker
   says "Enter credentials". A tracker Luma already has (same site type, or same host for
   Torznab/Newznab) is skipped. Then the Prowlarr path's guard state for those indexers
   (indexer_guard.json: sends, bucket, cooldowns, streak) is folded into the native guard under the
   site key, the stricter value winning (tracker_guard.fold), after a backup of tracker_guard.json.
   v172: indexer_guard.json is read with legacy_guard_state (read-only; the old module is gone).

v172 (tracker port step 4): this is the only request Luma makes to a LAN address, and it is scoped
narrowly: owner-pressed only, GET only, exactly {base}/api/v1/indexer on the saved Prowlarr URL,
no redirects, no environment proxy, 2 MB and 20 s caps (v173: 20 s for the whole request), and never to a link-local / metadata,
multicast, unspecified or reserved address (check_target, before anything is sent).

Clean-room: Prowlarr's public API shape (indexer JSON with a fields list) is the only reference;
no Prowlarr code or definitions are used. Mapping is by definition/implementation name.
"""
from __future__ import annotations

import logging
import re
import secrets
import shutil
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from app import tracker_types as tt
from app.metadata_addrs import is_metadata

logger = logging.getLogger(__name__)

PREVIEW_TTL_S = 600
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 20            # connect and each read
TOTAL_S = 20              # v173 (Tester FF-B): the whole request, headers and body, so a Prowlarr that
                          # sends one byte a second can't hold a worker past it

READY, NEEDS_CREDENTIALS, EXISTS, UNSUPPORTED, INVALID = 'ready', 'needs_credentials', 'exists', 'unsupported', 'invalid'
NOT_CONFIGURED = 'Prowlarr isn’t set up'
UNREACHABLE = 'Couldn’t reach Prowlarr'
UNEXPECTED = 'Prowlarr returned an unexpected response'     # v173 (Tester FF-A): never a 500
EXPIRED = 'Preview expired. Start again.'

# Prowlarr definition/implementation name (letters and digits only, lower case, a trailing 'api'
# dropped) -> Luma type.
_SITE_TYPES = {
    'iptorrents': 'iptorrents', 'beyondhd': 'beyondhd', 'privatehd': 'privatehd', 'pixelhd': 'pixelhd',
    'avistaz': 'avistaz', 'cinemaz': 'cinemaz', 'exoticaz': 'exoticaz', 'morethantv': 'morethantv',
}
_GENERIC = {'torznab': 'torznab', 'newznab': 'newznab'}
# Luma field -> Prowlarr field names (last dotted segment, case-insensitive)
_FIELD_NAMES = {
    'cookie': ('cookie',), 'user_agent': ('useragent',), 'api_key': ('apikey',), 'rss_key': ('rsskey',),
    'username': ('username',), 'password': ('password',), 'pid': ('pid',),
}
_MASKED = re.compile(r'^\*+$')

_session_factory = None            # tests: a fake Prowlarr session
_previews: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_lock = threading.Lock()


def _norm(name: Any) -> str:
    n = re.sub(r'[^a-z0-9]', '', str(name or '').lower())
    return n[:-3] if n.endswith('api') and len(n) > 3 else n


def prowlarr_settings(get_setting: Callable[[str], Any]) -> Optional[Tuple[str, str]]:
    """(base URL, API key) of the configured Prowlarr, or None."""
    if (get_setting('indexer.provider') or 'prowlarr') != 'prowlarr':
        return None
    base, key = str(get_setting('indexer.base_url') or '').strip(), str(get_setting('indexer.api_key') or '').strip()
    try:
        u = urlsplit(base)
    except ValueError:
        return None
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or not key:
        return None
    return base.rstrip('/'), key


def _resolve(host: str) -> List[str]:
    import socket
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})


def check_target(base: str) -> str:
    """v172: the Prowlarr read may go to the LAN (that's where Prowlarr lives) or this host, but never
    to a link-local / cloud-metadata, multicast, unspecified or reserved address, whatever the name
    resolves to. Raises RuntimeError(UNREACHABLE) before anything is sent."""
    import ipaddress
    try:
        host = urlsplit(base).hostname or ''
        try:
            addrs = [str(ipaddress.ip_address(host.strip('[]')))]
        except ValueError:
            addrs = _resolve(host)
        if not addrs:
            raise ValueError('no address')
        for a in addrs:
            ip = ipaddress.ip_address(a.split('%')[0])
            if getattr(ip, 'ipv4_mapped', None):
                ip = ip.ipv4_mapped
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved or is_metadata(ip):
                raise ValueError('refused address')         # v173: + fd00:ec2::254, 100.100.100.200
    except Exception:                                  # noqa: BLE001 - never the URL in the log
        logger.info('Import from Prowlarr: the saved Prowlarr address is not allowed')
        raise RuntimeError(UNREACHABLE) from None
    return addrs[0]                                    # v173 (Reviewer): the GET goes to this address


class _DeadlineAdapter:
    """Built on first use (requests imported lazily): an HTTPAdapter whose connections are tracked so
    the deadline watchdog can shut their sockets, which ends any read, headers included."""
    _cls = None

    @classmethod
    def make(cls, conns: list, expired: threading.Event, pinned: Optional[str] = None):
        if cls._cls is None:
            from requests.adapters import HTTPAdapter

            class Adapter(HTTPAdapter):
                def __init__(self, conns, expired, pinned):
                    self._conns, self._expired, self._pinned = conns, expired, pinned
                    super().__init__(max_retries=0)

                def _track(self, pool):
                    base = getattr(pool, 'ConnectionCls', None)
                    if base is not None and not getattr(base, '_luma_tracked', False):
                        conns, expired, pinned = self._conns, self._expired, self._pinned

                        class Tracked(base):  # type: ignore[misc, valid-type]
                            _luma_tracked = True

                            def connect(self):
                                conns.append(self)
                                if pinned:
                                    # v173 (Reviewer): connect to the address check_target vetted, so
                                    # a second DNS answer can't move the GET; Host and TLS name stay
                                    self._dns_host = pinned
                                super().connect()
                                # the socket itself: http.client drops conn.sock once the headers
                                # are in on a closing response, while the body is still read from it
                                conns.append(self.sock)
                                if expired.is_set():
                                    _kill(conns)
                        pool.ConnectionCls = Tracked
                    return pool

                def get_connection(self, url, proxies=None):                      # requests < 2.32
                    return self._track(super().get_connection(url, proxies))

                def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
                    return self._track(super().get_connection_with_tls_context(request, verify, proxies, cert))
            cls._cls = Adapter
        return cls._cls(conns, expired, pinned)


def _kill(conns: list) -> None:
    import socket
    for c in list(conns):
        sock = c if isinstance(c, socket.socket) else getattr(c, 'sock', None)
        if isinstance(sock, socket.socket):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _chunks(r):
    """The body as it arrives. read1 returns whatever is there (one byte from a dribbling server), so
    the deadline is checked between reads; iter_content would wait for a full chunk."""
    raw = getattr(r, 'raw', None)
    read1 = getattr(raw, 'read1', None)
    if callable(read1) and hasattr(raw, 'release_conn'):
        while True:
            chunk = read1(65536, decode_content=True)
            if not chunk:
                return
            yield chunk
    else:
        yield from r.iter_content(65536)


def fetch_indexers(base: str, api_key: str, session_factory=None) -> List[Dict[str, Any]]:
    """One GET of {base}/api/v1/indexer. Raises RuntimeError(UNREACHABLE) when Prowlarr can't be
    read (refused, failing, too slow: TOTAL_S for the whole request) and RuntimeError(UNEXPECTED)
    when it answers something that isn't an indexer list."""
    import json

    import requests
    pinned = check_target(base)
    deadline = time.monotonic() + TOTAL_S
    s = (session_factory or _session_factory or requests.Session)()
    s.trust_env = False
    conns: list = []
    expired = threading.Event()
    if isinstance(s, requests.Session):
        adapter = _DeadlineAdapter.make(conns, expired, pinned)
        s.mount('http://', adapter)
        s.mount('https://', adapter)

    def expire():
        expired.set()
        _kill(conns)
    timer = threading.Timer(TOTAL_S, expire)
    timer.daemon = True
    timer.start()
    try:
        r = s.get(f'{base}/api/v1/indexer', headers={'X-Api-Key': api_key, 'Accept': 'application/json'},
                  timeout=TIMEOUT_S, allow_redirects=False, stream=True)
        try:
            if expired.is_set():
                raise RuntimeError(UNREACHABLE)
            if r.status_code != 200:
                raise RuntimeError(UNREACHABLE)
            body = bytearray()
            for chunk in _chunks(r):
                if expired.is_set() or time.monotonic() > deadline:
                    raise RuntimeError(UNREACHABLE)
                body += chunk
                if len(body) > MAX_BYTES:
                    raise RuntimeError(UNEXPECTED)
            if expired.is_set() or time.monotonic() > deadline:
                raise RuntimeError(UNREACHABLE)
        finally:
            r.close()
    except RuntimeError as e:
        if str(e) == UNREACHABLE and expired.is_set():
            logger.info('Import from Prowlarr: read took longer than %ds', TOTAL_S)
        raise
    except Exception as e:                             # noqa: BLE001 - never a URL, key or body in the log
        logger.info('Import from Prowlarr: read failed (%s)%s', type(e).__name__,
                    ' after the deadline' if expired.is_set() else '')
        raise RuntimeError(UNREACHABLE) from None
    finally:
        timer.cancel()
        try:
            s.close()
        except Exception:                              # noqa: BLE001
            pass
    try:
        data = json.loads(bytes(body).decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeError(UNEXPECTED) from None
    if not isinstance(data, list):
        raise RuntimeError(UNEXPECTED)
    return [ix for ix in data if isinstance(ix, dict)]


def _as_list(v: Any) -> list:
    """v173 (Tester FF-A): a list where Prowlarr should have sent one, else nothing."""
    return v if isinstance(v, list) else []


def _as_dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _fields(ix: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for f in _as_list(ix.get('fields')):
        if isinstance(f, dict) and isinstance(f.get('name'), str):
            out[f['name'].split('.')[-1].lower()] = f.get('value')
    return out


def _type_for(ix: Dict[str, Any]) -> Optional[str]:
    for k in ('definitionName', 'implementation'):
        n = _norm(ix.get(k))
        if n in _SITE_TYPES:
            return _SITE_TYPES[n]
    impl = _norm(ix.get('implementation'))
    return _GENERIC.get(impl)


def _payload(ix: Dict[str, Any], type_id: str) -> Tuple[Dict[str, Any], List[str]]:
    """Luma create payload (credentials in plain text: server side only) and the credential
    fields Prowlarr didn't hand over."""
    t = tt.get_type(type_id)
    f = _fields(ix)
    urls = [u for u in _as_list(ix.get('indexerUrls')) if isinstance(u, str)]
    url = str(f.get('baseurl') or '').strip() or (urls or [''])[0]
    default_url = next((x.get('default') for x in t['fields'] if x['key'] == 'url'), None)
    if tt.is_generic(type_id):
        path = str(f.get('apipath') or '/api').strip() or '/api'
        url = (url.rstrip('/') + '/' + path.lstrip('/')) if url else ''
    elif not url:
        url = default_url or ''
    p: Dict[str, Any] = {'type': type_id, 'name': ' '.join(str(ix.get('name') or t['name']).split())[:tt.NAME_MAX],
                         'url': url}
    pr = ix.get('priority')
    if tt.validate_priority(pr) is not None:
        p['priority'] = tt.validate_priority(pr)
    cats = []
    for c in _as_list(_as_dict(ix.get('capabilities')).get('categories')):
        cid = c.get('id') if isinstance(c, dict) else None
        if isinstance(cid, int) and not isinstance(cid, bool):
            cats.append(cid)
    allowed = {c for c, _ in t['categories']}
    picked = sorted({c for c in cats if c in allowed} & set(tt.DEFAULT_CATEGORIES)) or \
        sorted({c for c in cats if c in allowed})[:4]
    if picked:
        p['categories'] = picked
    missing = []
    creds = tt.secret_keys(type_id) + tt.setting_keys(type_id)
    required = {x['key'] for x in t['fields'] if x.get('required')}
    for k in creds:
        v = None
        for name in _FIELD_NAMES.get(k, (k.replace('_', ''),)):
            if name in f:
                v = f[name]
                break
        v = '' if v is None else str(v).strip()
        if not v or _MASKED.match(v):
            if k in required:
                missing.append(k)
            continue
        p[k] = v
    return p, missing


def _label(type_id: str, key: str) -> str:
    t = tt.get_type(type_id) or {}
    return next((f['label'] for f in t.get('fields', []) if f['key'] == key), key)


def _exists(type_id: str, url: str, have: List[Dict[str, Any]]) -> bool:
    from app.tracker_guard import guard_key
    gk = guard_key({'type': type_id, 'url': url})
    return any(guard_key(r) == gk for r in have)


def preview(get_setting, store, session_factory=None) -> Dict[str, Any]:
    """{'preview_id', 'indexers': [...]} for the UI (no credential values), or raises RuntimeError."""
    conf = prowlarr_settings(get_setting)
    if conf is None:
        raise RuntimeError(NOT_CONFIGURED)
    raw = fetch_indexers(*conf, session_factory=session_factory)
    have = store.list()
    try:
        rows, keep = _rows(raw, store, have)
    except RuntimeError:
        raise
    except Exception as e:                             # noqa: BLE001 - v173 (Tester FF-A): a 502, never a 500
        logger.info('Import from Prowlarr: unexpected indexer shape (%s)', type(e).__name__)
        raise RuntimeError(UNEXPECTED) from None
    pid = secrets.token_urlsafe(18)
    now = time.time()
    with _lock:
        for k in [k for k, (t, _) in _previews.items() if t < now]:
            _previews.pop(k, None)
        _previews.clear()                              # one preview at a time
        _previews[pid] = (now + PREVIEW_TTL_S, keep)
    logger.info('Import from Prowlarr: preview of %d indexer(s), %d importable', len(rows), len(keep))
    return {'preview_id': pid, 'indexers': rows}


def _rows(raw: List[Dict[str, Any]], store, have) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows, keep = [], []
    for ix in sorted(raw, key=lambda i: str(i.get('name') or '').lower()):
        pid = ix.get('id')
        if isinstance(pid, bool) or not isinstance(pid, int):
            continue
        type_id = _type_for(ix)
        row = {'key': pid, 'name': ' '.join(str(ix.get('name') or f'Indexer {pid}').split())[:tt.NAME_MAX],
               'type': type_id, 'type_name': (tt.get_type(type_id) or {}).get('name') if type_id else None,
               'missing': [], 'enabled_in_prowlarr': bool(ix.get('enable', True))}
        if type_id is None:
            row['state'] = UNSUPPORTED
        else:
            payload, missing = _payload(ix, type_id)
            row['missing'] = [_label(type_id, k) for k in missing]
            if _exists(type_id, payload['url'], have):
                row['state'] = EXISTS
            else:
                errors = store.import_errors(payload)
                row['state'] = INVALID if errors else (NEEDS_CREDENTIALS if missing else READY)
                if row['state'] != INVALID:
                    keep.append({'key': pid, 'payload': payload, 'type': type_id, 'prowlarr_id': pid})
        rows.append(row)
    return rows, keep


def _backup_guard(guard) -> None:
    src = guard.path
    if src.exists():
        dst = src.with_name(f'{src.name}.bak-import-{int(time.time())}')
        shutil.copy2(src, dst)


LEGACY_GUARD_FILE = 'indexer_guard.json'   # the Prowlarr path's guard (v172: module gone, file kept)
LEGACY_FAIL_CLOSED_S = 3600
LEGACY_COOLDOWN_MAX_S = 24 * 3600


def _ts(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if v == v and 0 < v < 1e11 else None


def legacy_guard_state(prowlarr_id: int, now: Optional[float] = None, path=None) -> Dict[str, Any]:
    """v172: the old Prowlarr guard's state for one indexer, read-only from indexer_guard.json (the
    module that wrote it is gone; the file stays on disk for one release, never written or moved).
    Missing file: nothing to fold. Unreadable or wrongly shaped: fail closed (a 1 h cooldown), like
    the old guard did, so an import can never start a tracker with less than it had."""
    import json
    now = time.time() if now is None else now
    out = {'log': [], 'bucket': None, 'cooldown': 0.0, 'streak': 0}
    if path is None:
        from app.first_run import data_dir
        path = data_dir() / LEGACY_GUARD_FILE
    try:
        with open(path, encoding='utf-8') as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return out
    except (OSError, ValueError, UnicodeDecodeError):
        out['cooldown'] = now + LEGACY_FAIL_CLOSED_S
        return out
    secs = ('log', 'buckets', 'cooldown', 'streak')
    if not isinstance(raw, dict) or any(not isinstance(raw.get(k, {}), dict) for k in secs):
        out['cooldown'] = now + LEGACY_FAIL_CLOSED_S
        return out
    key = str(int(prowlarr_id))
    for e in raw['log'].get(key, []) if isinstance(raw.get('log', {}).get(key), list) else []:
        if isinstance(e, list) and len(e) == 2 and e[1] in ('i', 'b'):
            ts, kind = _ts(e[0]), e[1]
        else:
            ts, kind = _ts(e), 'b'
        if ts is not None and ts > now - 86400:
            out['log'].append([ts, kind])
    b = raw.get('buckets', {}).get(key)
    if isinstance(b, list) and len(b) == 2 and not isinstance(b[0], bool) and isinstance(b[0], (int, float)) \
            and 0 <= b[0] <= 5 and _ts(b[1]) is not None:
        out['bucket'] = [float(b[0]), _ts(b[1])]
    elif b is not None:
        out['bucket'] = [0.0, now]                      # unknown: empty, not full
    cds = []
    for k, cap in ((key, LEGACY_COOLDOWN_MAX_S), ('-1', LEGACY_COOLDOWN_MAX_S), ('-2', LEGACY_FAIL_CLOSED_S)):
        t = _ts(raw.get('cooldown', {}).get(k))
        if t is not None:
            cds.append(min(t, now + cap))
    out['cooldown'] = max(cds or [0.0])
    st = raw.get('streak', {}).get(key)
    if isinstance(st, int) and not isinstance(st, bool) and 0 <= st <= 64:
        out['streak'] = st
    return out


def _fold(guard, gk: str, prowlarr_id: int) -> Dict[str, Any]:
    now = time.time()
    st = legacy_guard_state(prowlarr_id, now)
    return guard.fold(gk, st['log'], st['bucket'], st['cooldown'], st['streak'], now)


def create(preview_id: str, keys: List[int], store, guard=None) -> Dict[str, Any]:
    """Create the ticked trackers (disabled, Test required) and fold their guard state.
    Raises RuntimeError(EXPIRED) for an unknown or expired preview; a preview is used once."""
    from app.tracker_guard import get_guard, guard_key
    with _lock:
        hit = _previews.pop(str(preview_id or ''), None)
    if not hit or hit[0] < time.time():
        raise RuntimeError(EXPIRED)
    want = {k for k in keys if isinstance(k, int) and not isinstance(k, bool)}
    g = guard or get_guard()
    created, skipped = [], []
    backed_up = False
    for item in hit[1]:
        if item['key'] not in want:
            continue
        p = item['payload']
        if _exists(item['type'], p['url'], store.list()):
            skipped.append({'key': item['key'], 'reason': 'Already added'})
            continue
        rec, errors = store.create(p, imported={'from': 'prowlarr', 'at': time.time()})
        if errors:
            skipped.append({'key': item['key'], 'reason': 'Can’t import'})
            continue
        if not backed_up:
            _backup_guard(g)
            backed_up = True
        try:
            folded = _fold(g, guard_key(rec), item['prowlarr_id'])
        except Exception as e:                         # noqa: BLE001 - the tracker stays disabled anyway
            logger.warning('Import from Prowlarr: guard state not folded for %s (%s)', rec['name'], type(e).__name__)
            folded = {}
        logger.info('Import from Prowlarr: added %s (id %s, %s, disabled; folded %s)', rec['name'], rec['id'],
                    rec['type'], sorted(folded))
        created.append(rec)
    return {'created': created, 'skipped': skipped}


def reset_for_tests() -> None:
    with _lock:
        _previews.clear()
