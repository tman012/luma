"""Settings > Connectors: qBittorrent (owner only).

GET  /api/settings/connectors/qbittorrent        url, username, category, password {set}
PUT  /api/settings/connectors/qbittorrent        save into the settings qbittorrent section
POST /api/settings/connectors/qbittorrent/test   login + app/version, a short specific error

Reads and writes the same qbittorrent section of settings.json the download
path uses (qbittorrent.url, .username, .password, .app_category), so a Save
here is what /api/downloads/add logs in with and the category it adds to.

The password never leaves the server: GET answers only whether one is saved,
and it is never logged. A stored password is only ever sent to the URL it was
saved with: a Test or Save for another URL needs the password typed again.
Nothing is preconfigured: with no URL saved, the card answers all empty.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('qbit_connector', __name__)
_cfg: Dict[str, Any] = {}

MASK = '********'
URL_MAX = 512
TEXT_MAX = 128
PASSWORD_MAX = 512
CATEGORY_RE = re.compile(r'^[^\\/\x00-\x1f\x7f]{1,64}$')
VERSION_RE = re.compile(r'^v?[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$')
TIMEOUT_S = 8
THROTTLE_MAX, THROTTLE_WINDOW_S = 10, 60

CONNECTED = 'Connected'
REQUIRED = 'Required'
INVALID_URL = 'Invalid URL'
INVALID = 'Invalid'
TIMED_OUT = 'Timed out'
UNREACHABLE = "Can't reach qBittorrent"
CERT = 'Certificate error'
BAD_LOGIN = 'Wrong username or password'
SIGN_IN_FAILED = 'qBittorrent sign-in failed'
BANNED = 'qBittorrent blocked this address after failed logins'
NOT_QBIT = 'Not a qBittorrent Web UI'
UNEXPECTED = 'Unexpected response'
RATE_LIMITED = 'Rate limited'

_tests: Dict[Tuple[Any, str], list] = {}


def init(*, settings, owner_or_error: Callable) -> None:
    _cfg.update(settings=settings, owner_or_error=owner_or_error)


def _no_store(resp, status=200):
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _s(key: str) -> str:
    v = _cfg['settings'].get(f'qbittorrent.{key}')
    return v.strip() if isinstance(v, str) else ''


def normalize_url(raw: Any) -> Optional[str]:
    """An http(s) Web UI base without a trailing slash, query, fragment or
    userinfo; None when it isn't one."""
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    if not url or len(url) > URL_MAX or any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port  # raises on a bad port
    except ValueError:
        return None
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return None
    host = parts.hostname
    if ':' in host:
        host = f'[{host}]'
    netloc = f'{host}:{port}' if port is not None else host
    path = parts.path.rstrip('/')
    return f'{parts.scheme.lower()}://{netloc}{path}'


def _same_url(a: str, b: str) -> bool:
    na, nb = normalize_url(a), normalize_url(b)
    return bool(na) and na == nb


def view() -> Dict[str, Any]:
    """What the card shows. All empty until a URL is saved."""
    url = _s('url')
    paused = _cfg['settings'].get('qbittorrent.add_paused') is True
    if not url:
        return {'url': '', 'username': '', 'category': '', 'password': {'set': False}, 'add_paused': paused}
    return {'url': url, 'username': _s('username'), 'category': _s('app_category'),
            'password': {'set': bool(_s('password'))}, 'add_paused': paused}


def _text(raw: Any, limit: int) -> Optional[str]:
    if raw is None:
        return ''
    if not isinstance(raw, str):
        return None
    v = raw.strip()
    if len(v) > limit or any(ord(c) < 32 or ord(c) == 127 for c in v):
        return None
    return v


def _password_in(body: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """(given, value): given is False when the field is absent or the mask
    (keep the stored one). value is None when it is invalid."""
    if 'password' not in body or body.get('password') in (None, MASK):
        return False, ''
    raw = body.get('password')
    if not isinstance(raw, str) or len(raw) > PASSWORD_MAX or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return True, None
    return True, raw


def _resolve(body: Dict[str, Any], *, require_category: bool) -> Tuple[Optional[Dict[str, str]], Dict[str, str]]:
    """The candidate config from a form body, filling the stored password
    only for the stored URL. Returns (cfg, field_errors)."""
    errors: Dict[str, str] = {}
    raw_url = body.get('url')
    url = normalize_url(raw_url) if raw_url not in (None, '') else None
    if raw_url in (None, ''):
        errors['url'] = REQUIRED
    elif url is None:
        errors['url'] = INVALID_URL
    username = _text(body.get('username'), TEXT_MAX)
    if username is None:
        errors['username'] = INVALID
    category = _text(body.get('category'), 64)
    if category is None or (category and not CATEGORY_RE.match(category)):
        errors['category'] = INVALID
    elif require_category and not category:
        errors['category'] = REQUIRED
    given, password = _password_in(body)
    if given and password is None:
        errors['password'] = INVALID
    elif not given:
        stored = _s('password')
        if stored and url and not _same_url(url, _s('url')):
            # A stored password goes only to the URL it was saved with.
            errors['password'] = REQUIRED
        password = stored if url and _same_url(url, _s('url')) else ''
    if errors:
        return None, errors
    return {'url': url or '', 'username': username or '', 'password': password or '',
            'category': category or ''}, {}


def _throttled(uid: Any, bucket: str) -> bool:
    now = time.time()
    k = (uid, bucket)
    log = [t for t in _tests.get(k, []) if t > now - THROTTLE_WINDOW_S]
    if len(log) >= THROTTLE_MAX:
        _tests[k] = log
        return True
    log.append(now)
    _tests[k] = log
    return False


def _text_of(r) -> str:
    """The body as UTF-8. qBittorrent's plain-text answers may carry no
    charset, and requests' guess can misread a short ASCII body."""
    raw = getattr(r, 'content', None)
    if isinstance(raw, bytes):
        return raw.decode('utf-8', 'replace').strip()
    return (r.text or '').strip()


text_of = _text_of


def new_session(factory: Optional[Callable] = None):
    """A session for qBittorrent: never via an environment proxy and never
    following a redirect, whatever the caller passes. Every request on it
    carries the password (login) or the SID cookie, so a 3xx must never send
    them on to another URL. Callers pass allow_redirects=False as well."""
    session = (factory or requests.Session)()
    try:
        session.trust_env = False
    except AttributeError:
        pass
    orig = getattr(session, 'request', None)
    if callable(orig):
        def request(method, url, *args, **kwargs):
            kwargs['allow_redirects'] = False
            return orig(method, url, *args, **kwargs)
        try:
            session.request = request
        except AttributeError:
            pass
    return session


_change_listeners: list = []


def on_change(fn: Callable[[], None]) -> None:
    """Call fn after the connector is saved or a test signs in."""
    if fn not in _change_listeners:
        _change_listeners.append(fn)


def _changed() -> None:
    for fn in list(_change_listeners):
        try:
            fn()
        except Exception:
            logger.exception('qBittorrent change listener failed')


_verified_listeners: list = []


def on_verified(fn: Callable[[], None]) -> None:
    """Call fn after a Test signs in with the saved credentials."""
    if fn not in _verified_listeners:
        _verified_listeners.append(fn)


def _verified() -> None:
    for fn in list(_verified_listeners):
        try:
            fn()
        except Exception:
            logger.exception('qBittorrent verified listener failed')


def _is_saved(cfg: Dict[str, Any]) -> bool:
    s = _cfg['settings']
    return (normalize_url(cfg.get('url') or '') == normalize_url(s.get('qbittorrent.url') or '')
            and (cfg.get('username') or '') == (s.get('qbittorrent.username') or '')
            and (cfg.get('password') or '') == (s.get('qbittorrent.password') or ''))


def check(url: str, username: str, password: str) -> Tuple[Optional[str], str]:
    """(None, version) when qBittorrent accepts the login, else (error, '').
    The error is short and never carries the URL, username or password."""
    session = new_session()
    try:
        try:
            r = session.post(f'{url}/api/v2/auth/login', data={'username': username, 'password': password},
                             timeout=TIMEOUT_S, allow_redirects=False)
        except requests.exceptions.SSLError:
            return CERT, ''
        except requests.exceptions.Timeout:
            return TIMED_OUT, ''
        except requests.exceptions.RequestException:
            return UNREACHABLE, ''
        body = _text_of(r)
        if 300 <= r.status_code < 400:
            return SIGN_IN_FAILED, ''           # a redirect is never a sign-in
        if r.status_code == 403:
            return BANNED, ''
        if r.status_code == 401:
            return BAD_LOGIN, ''
        if r.status_code == 404:
            return NOT_QBIT, ''
        if r.status_code != 200:
            return f'qBittorrent answered HTTP {int(r.status_code)}', ''
        if body == 'Fails.':
            return BAD_LOGIN, ''
        if body != 'Ok.':
            return NOT_QBIT, ''
        try:
            v = session.get(f'{url}/api/v2/app/version', timeout=TIMEOUT_S, allow_redirects=False)
        except requests.exceptions.Timeout:
            return TIMED_OUT, ''
        except requests.exceptions.RequestException:
            return UNREACHABLE, ''
        if v.status_code == 403:
            return BAD_LOGIN, ''
        if v.status_code != 200:
            return UNEXPECTED, ''
        version = _text_of(v)
        return None, version if VERSION_RE.match(version) else ''
    finally:
        session.close()


@bp.route('/api/settings/connectors/qbittorrent', methods=['GET'])
def qbit_get():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    return _no_store(jsonify(view()))


@bp.route('/api/settings/connectors/qbittorrent', methods=['PUT'])
def qbit_save():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _no_store(jsonify({'error': INVALID}), 400)
    cfg, errors = _resolve(body, require_category=False)
    if errors:
        return _no_store(jsonify({'error': INVALID, 'fields': errors}), 400)
    section = {'url': cfg['url'], 'username': cfg['username'], 'app_category': cfg['category']}
    if 'add_paused' in body:
        if not isinstance(body['add_paused'], bool):
            return _no_store(jsonify({'error': INVALID, 'fields': {'add_paused': INVALID}}), 400)
        section['add_paused'] = body['add_paused']
    given, password = _password_in(body)
    if given:
        section['password'] = password
    elif not _same_url(cfg['url'], _s('url')):
        section['password'] = ''
    ok, problems = _cfg['settings'].update({'qbittorrent': section})
    if not ok:
        logger.warning('qBittorrent settings rejected (%d problems)', len(problems))
        return _no_store(jsonify({'error': "Couldn't save"}), 400)
    logger.info('qBittorrent connector saved')
    _changed()
    return _no_store(jsonify(view()))


@bp.route('/api/settings/connectors/qbittorrent/test', methods=['POST'])
def qbit_test():
    user, err = _cfg['owner_or_error']()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    if not body:
        body = {'url': _s('url'), 'username': _s('username'), 'category': _s('app_category')}
    cfg, errors = _resolve(body, require_category=False)
    if errors:
        first = next(iter(errors.values()))
        return _no_store(jsonify({'ok': False, 'error': first, 'fields': errors}), 400)
    if _throttled(user.get('id'), 'qbit-test'):
        return _no_store(jsonify({'ok': False, 'error': RATE_LIMITED}))
    problem, version = check(cfg['url'], cfg['username'], cfg['password'])
    if problem:
        logger.info('qBittorrent test failed: %s', problem)
        return _no_store(jsonify({'ok': False, 'error': problem}))
    _changed()
    if _is_saved(cfg):
        _verified()
    return _no_store(jsonify({'ok': True, 'message': CONNECTED, 'version': version}))
