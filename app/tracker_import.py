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

logger = logging.getLogger(__name__)

PREVIEW_TTL_S = 600
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 20

READY, NEEDS_CREDENTIALS, EXISTS, UNSUPPORTED, INVALID = 'ready', 'needs_credentials', 'exists', 'unsupported', 'invalid'
NOT_CONFIGURED = 'Prowlarr isn’t set up'
UNREACHABLE = 'Couldn’t reach Prowlarr'
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


def fetch_indexers(base: str, api_key: str, session_factory=None) -> List[Dict[str, Any]]:
    """One GET of {base}/api/v1/indexer. Raises RuntimeError(UNREACHABLE) on any failure."""
    import requests
    s = (session_factory or _session_factory or requests.Session)()
    s.trust_env = False
    try:
        r = s.get(f'{base}/api/v1/indexer', headers={'X-Api-Key': api_key, 'Accept': 'application/json'},
                  timeout=TIMEOUT_S, allow_redirects=False, stream=True)
        try:
            if r.status_code != 200:
                raise RuntimeError(UNREACHABLE)
            body = b''
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > MAX_BYTES:
                    raise RuntimeError(UNREACHABLE)
        finally:
            r.close()
        import json
        data = json.loads(body.decode('utf-8'))
    except RuntimeError:
        raise
    except Exception as e:                             # noqa: BLE001 - never a URL, key or body in the log
        logger.info('Import from Prowlarr: read failed (%s)', type(e).__name__)
        raise RuntimeError(UNREACHABLE) from None
    finally:
        try:
            s.close()
        except Exception:                              # noqa: BLE001
            pass
    if not isinstance(data, list):
        raise RuntimeError(UNREACHABLE)
    return [ix for ix in data if isinstance(ix, dict)]


def _fields(ix: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for f in ix.get('fields') or []:
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
    url = str(f.get('baseurl') or '').strip() or (list(ix.get('indexerUrls') or []) or [''])[0]
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
    for c in ((ix.get('capabilities') or {}).get('categories') or []):
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
    pid = secrets.token_urlsafe(18)
    now = time.time()
    with _lock:
        for k in [k for k, (t, _) in _previews.items() if t < now]:
            _previews.pop(k, None)
        _previews.clear()                              # one preview at a time
        _previews[pid] = (now + PREVIEW_TTL_S, keep)
    logger.info('Import from Prowlarr: preview of %d indexer(s), %d importable', len(rows), len(keep))
    return {'preview_id': pid, 'indexers': rows}


def _backup_guard(guard) -> None:
    src = guard.path
    if src.exists():
        dst = src.with_name(f'{src.name}.bak-import-{int(time.time())}')
        shutil.copy2(src, dst)


def _fold(guard, gk: str, prowlarr_id: int) -> Dict[str, Any]:
    from app import indexer_api as ia
    now = time.time()
    with ia._budget_state_guard:
        ia._guard_load()
        log = [list(e) for e in ia._guard_log.get(prowlarr_id, [])]
        bucket = list(ia._guard_buckets[prowlarr_id]) if prowlarr_id in ia._guard_buckets else None
        cds = [ia._guard_cooldown.get(prowlarr_id, 0.0), ia._guard_cooldown.get(ia.UNSCOPED, 0.0),
               ia._guard_cooldown.get(ia.FAIL_CLOSED, 0.0)]
        streak = ia._guard_streak.get(prowlarr_id, 0)
    return guard.fold(gk, log, bucket, max(cds), streak, now)


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
