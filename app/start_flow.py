"""Start flow for a brand-new install: Welcome, Sign in with Plex (or Skip,
same network only), Create account, Access, Done. Plex can be connected
later from Settings > Plex (owner only, /api/settings/plex*).

The Plex account that signs in becomes the owner (admin). The Access step
(PUT /api/security) picks when a PIN is required, like Sonarr/Radarr v4's
first-run "Authentication Required" choice.

Shown only while the users table is empty. Every endpoint here refuses to
work once a user exists, except /api/start/status (which then reports
``needs_start: false``) and /api/start/finish (admin only, ends the flow).

The browser that starts the Plex sign-in gets a ``luma_start`` cookie; the
pending Plex token is kept server-side and never sent to the browser.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

import requests
from flask import Blueprint, jsonify, make_response, request

from app import first_run

logger = logging.getLogger(__name__)

PLEX_TV = 'https://plex.tv'
START_COOKIE = 'luma_start'
SETUP_COOKIE = 'luma_setup'          # Plex-verified browser may set the owner password
SETUP_SALT = 'luma-start-password'
SETUP_TTL = 3600
PRODUCT = 'Luma'

bp = Blueprint('start_flow', __name__)

_cfg: Dict[str, Any] = {}
_pending: Dict[str, Dict[str, Any]] = {}   # start nonce -> {pin_id, token, servers}
_lock = threading.Lock()
# Held while the first (owner) account is created, by either path (Plex or
# Skip): the check "no users yet" and the insert happen under it, so two
# racing requests can never both become the owner. Luma runs one gunicorn
# worker (gunicorn.conf.py), so a process lock covers every request.
_first_account = threading.Lock()

# The setup window: a fresh install can be claimed for 24 hours after this
# process started. After that every setup endpoint refuses until Luma (the
# container) restarts, so a forgotten install isn't claimable forever.
SETUP_WINDOW = 24 * 3600
_boot = time.monotonic()


def setup_window_open() -> bool:
    return time.monotonic() - _boot < SETUP_WINDOW


def _window_closed():
    """The refusal once the setup window has closed (None while it's open)."""
    if needs_start() and not setup_window_open():
        return jsonify({'error': 'Setup closed. Restart Luma to try again.', 'closed': True}), 403
    return None


# Headers that mean a proxy, tunnel or CDN relayed the request.
_PROXY_HEADER_NAMES = ('forwarded', 'x-real-ip', 'via', 'true-client-ip', 'x-client-ip',
                       'cdn-loop', 'x-original-forwarded-for')


def _proxied(headers) -> bool:
    for name in headers.keys():
        n = name.lower()
        if n.startswith(('x-forwarded-', 'cf-')) or n in _PROXY_HEADER_NAMES:
            return True
    return False


def skip_allowed(req=None) -> bool:
    """Skipping Plex (owner created by username and password alone) is only
    for someone on the same network as Luma.

    Judged by the real TCP peer. Forwarding headers count only from a
    configured trusted proxy (security.trusted_proxies), and then the
    forwarded client must itself be a LAN address. Anything relayed from an
    untrusted peer (Caddy, a tunnel, any X-Forwarded-*/Forwarded/Via header)
    and anything that came through Cloudflare (any cf-* header) is not LAN.
    The Host must also be one of this install's own names, so a public page
    rebinding its DNS to a LAN address can't claim the install from a
    visitor's browser."""
    from app import access
    req = req or request
    settings = _cfg.get('settings')
    nets = access.trusted_proxies(settings) if settings is not None else []
    peer = req.remote_addr or ''
    if any(k.lower().startswith('cf-') for k in req.headers.keys()) or access.via_cloudflare(req):
        return False
    if _proxied(req.headers) and not access._in(nets, peer):
        return False
    if not access.is_known_host(req.host, settings):
        return False
    return access.is_local_request(req, settings)


def init(*, settings, user_auth, session_cookie: str, client_id: str,
         on_plex_saved: Callable[[str, str, str], int], get_current_user: Callable[[], Optional[dict]],
         secret: Optional[str] = None):
    _cfg.update(settings=settings, user_auth=user_auth, session_cookie=session_cookie,
                client_id=client_id, on_plex_saved=on_plex_saved, get_current_user=get_current_user,
                secret=secret)
    clear_stale_claim()


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _state_path():
    return first_run.data_dir() / 'start_state.json'


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(_state_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    p = _state_path()
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


CLAIM_TTL = 60  # seconds a password claim blocks a second attempt


def _claim_active(st: Dict[str, Any]) -> bool:
    """A password write is in flight. Claims older than CLAIM_TTL (a crash
    mid-write) or from an older format (True) no longer block."""
    claimed = st.get('password_claimed')
    if isinstance(claimed, bool) or not isinstance(claimed, (int, float)):
        return False
    return 0 <= time.time() - claimed < CLAIM_TTL


def clear_stale_claim() -> None:
    """At startup no password write can be in flight: drop any claim."""
    try:
        st = _load_state()
    except Exception:
        return
    if 'password_claimed' in st:
        st.pop('password_claimed', None)
        try:
            _save_state(st)
        except OSError as e:
            logger.warning(f'Start: could not clear password claim: {e}')


def needs_start() -> bool:
    """True only while no account exists. Once any user (the owner) exists,
    setup is closed for good: every claim endpoint (Plex PIN, poll, server,
    Skip) checks this first and refuses, whatever the setup window says and
    across any number of restarts. Only the 24 h window resets on restart,
    and it only matters while this is True."""
    return not _cfg['user_auth'].list_users()


def _in_progress(user: Optional[dict]) -> bool:
    st = _load_state()
    return bool(user and user.get('is_admin') and st.get('admin_via_start') and not st.get('completed'))


def _nonce() -> str:
    return request.cookies.get(START_COOKIE, '')


def _require_fresh():
    if not needs_start():
        return jsonify({'error': 'Luma is already set up'}), 409
    closed = _window_closed()
    if closed:
        return closed
    st = _load_state()
    if not _nonce() or _nonce() != st.get('nonce'):
        # No (or a stale) start cookie: this browser didn't begin the sign-in.
        return jsonify({'error': 'Start again', 'restart': True}), 401
    return None


# ---------------------------------------------------------------------------
# plex.tv
# ---------------------------------------------------------------------------

def _plex_headers(token: str = '') -> Dict[str, str]:
    h = {
        'Accept': 'application/json',
        'X-Plex-Client-Identifier': _cfg['client_id'],
        'X-Plex-Product': PRODUCT,
        'X-Plex-Version': '1.0',
        'X-Plex-Device-Name': PRODUCT,
        'X-Plex-Platform': 'Web',
    }
    if token:
        h['X-Plex-Token'] = token
    return h


def create_pin() -> Dict[str, Any]:
    r = requests.post(f'{PLEX_TV}/api/v2/pins', params={'strong': 'true'},
                      headers=_plex_headers(), timeout=10)
    r.raise_for_status()
    d = r.json()
    return {'id': d['id'], 'code': d['code']}


START_PATH = '/start'   # same-tab sign-in returns here; the page resumes the PIN

PLEX_AUTH_MODES = ('popup', 'redirect')
_HOST_RE = re.compile(
    r'^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*'
    r'|\[[0-9A-Fa-f:.]+\])(?::(?P<port>\d{1,5}))?$')


def _allowed_hosts() -> List[str]:
    raw = os.environ.get('LUMA_ALLOWED_HOSTS', '')
    return [h.strip().lower() for h in raw.split(',') if h.strip()]


def valid_host(host: str) -> Optional[str]:
    """The request host when it is a well-formed host[:port] with a port in
    1-65535 that names this server: listed in LUMA_ALLOWED_HOSTS, or one of
    its own known addresses (access.is_known_host). Otherwise None."""
    host = (host or '').strip()
    m = _HOST_RE.match(host) if host and len(host) <= 255 else None
    if not m:
        return None
    port = m.group('port')
    if port is not None and not 1 <= int(port) <= 65535:
        return None
    if host.lower() in _allowed_hosts():
        return host
    from app import access
    try:
        known = access.is_known_host(host, _cfg.get('settings'))
    except Exception:
        known = False
    return host if known else None


def forward_url(mode: str = 'popup', path: str = START_PATH) -> str:
    """Where Plex sends the browser after sign-in. Pop-up mode: nowhere (the
    opener polls the PIN and closes the pop-up). Same-tab mode: this server's
    validated origin plus the URL base and /start. Empty when the host can't
    be validated: an arbitrary Host header is never reflected."""
    if mode != 'redirect':
        return ''
    from flask import current_app
    host = valid_host(request.host)
    if not host:
        return ''
    scheme = 'https' if request.scheme == 'https' else 'http'
    base = (current_app.config.get('URL_BASE') or '').rstrip('/')
    return f"{scheme}://{host}{base}{path}"


def auth_url(code: str, forward: str = '') -> str:
    params = {
        'clientID': _cfg['client_id'],
        'code': code,
        'context[device][product]': PRODUCT,
    }
    if forward:
        params['forwardUrl'] = forward
    q = urllib.parse.urlencode(params)
    return f'https://app.plex.tv/auth#?{q}'


def check_pin(pin_id: int) -> Optional[str]:
    r = requests.get(f'{PLEX_TV}/api/v2/pins/{int(pin_id)}', headers=_plex_headers(), timeout=10)
    r.raise_for_status()
    return r.json().get('authToken') or None


def get_account(token: str) -> Dict[str, str]:
    r = requests.get(f'{PLEX_TV}/api/v2/user', headers=_plex_headers(token), timeout=10)
    r.raise_for_status()
    d = r.json()
    return {
        'id': str(d.get('id') or d.get('uuid') or ''),
        'name': (d.get('username') or d.get('title') or d.get('friendlyName') or '').strip(),
        'email': (d.get('email') or '').strip(),
    }


def get_owned_servers(token: str) -> List[Dict[str, Any]]:
    r = requests.get(f'{PLEX_TV}/api/v2/resources',
                     params={'includeHttps': 1, 'includeRelay': 1},
                     headers=_plex_headers(token), timeout=15)
    r.raise_for_status()
    return owned_servers(r.json())


def owned_servers(resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Owned Plex Media Servers from a /api/v2/resources response."""
    out = []
    for res in resources or []:
        provides = (res.get('provides') or '').split(',')
        if 'server' not in provides or not res.get('owned'):
            continue
        out.append({
            'machine_id': res.get('clientIdentifier', ''),
            'name': res.get('name') or 'Plex Media Server',
            'access_token': res.get('accessToken') or '',
            'connections': res.get('connections') or [],
            'presence': bool(res.get('presence')),
        })
    out.sort(key=lambda s: (not s['presence'], s['name'].lower()))
    return out


def ordered_connections(connections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Local first, then remote, then relay; https before http within a kind."""
    def kind(c):
        if c.get('relay'):
            return 2
        return 0 if c.get('local') else 1
    return sorted(connections, key=lambda c: (kind(c), 0 if c.get('protocol') == 'https' else 1))


def connection_kind(c: Dict[str, Any]) -> str:
    if c.get('relay'):
        return 'relay'
    return 'local' if c.get('local') else 'remote'


def probe(uri: str, token: str, machine_id: str) -> bool:
    try:
        r = requests.get(f"{uri.rstrip('/')}/identity", headers=_plex_headers(token), timeout=4)
        if r.status_code != 200:
            return False
        mc = r.json().get('MediaContainer', {})
        return not machine_id or mc.get('machineIdentifier') in (None, machine_id)
    except Exception:
        return False


def pick_connection(server: Dict[str, Any], token: str,
                    tester: Callable[[str, str, str], bool] = None) -> Optional[Dict[str, Any]]:
    tester = tester or probe
    for c in ordered_connections(server.get('connections') or []):
        uri = c.get('uri')
        if uri and tester(uri, token, server.get('machine_id', '')):
            return {'uri': uri.rstrip('/'), 'kind': connection_kind(c)}
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route('/api/start/status')
def start_status():
    user = _cfg['get_current_user']()
    fresh = needs_start()
    out = {'needs_start': fresh, 'in_progress': (not fresh) and _in_progress(user)}
    if fresh:
        out['skip'] = skip_allowed()
        if not setup_window_open():
            out['closed'] = True
        out['step'] = 'welcome'
        # This browser started a Plex sign-in that's still open (e.g. it
        # went to Plex in the same window): resume polling, no new PIN.
        nonce = _nonce()
        if nonce and nonce == _load_state().get('nonce'):
            with _lock:
                p = _pending.get(nonce) or {}
            if p.get('pin_id') and time.time() - p.get('created', 0) <= 15 * 60:
                out['step'] = 'plex'
    elif out['in_progress']:
        out['step'] = 'done'
        out['server'] = _load_state().get('server_name', '')
        out['password_set'] = password_set()
    return jsonify(out)


PASSWORD_MIN = 8
PASSWORD_MAX = 256
USERNAME_MAX = 64


_fallback_secret = secrets.token_bytes(32)


def _serializer():
    from flask import current_app
    from itsdangerous import URLSafeTimedSerializer
    secret = _cfg.get('secret') or current_app.secret_key or _fallback_secret
    return URLSafeTimedSerializer(secret, salt=SETUP_SALT)


def setup_token(uid: int, plex_id: str) -> str:
    """Signed proof that this browser completed the Plex step for owner *uid*."""
    return _serializer().dumps({'uid': int(uid), 'plex': str(plex_id or '')})


def _setup_cookie_ok(st: Dict[str, Any], uid: int) -> bool:
    raw = request.cookies.get(SETUP_COOKIE, '')
    if not raw:
        return False
    from itsdangerous import BadData
    try:
        data = _serializer().loads(raw, max_age=SETUP_TTL)
    except BadData:
        return False
    if not isinstance(data, dict):
        return False
    return (data.get('uid') == uid == st.get('owner_uid')
            and str(data.get('plex') or '') == str(st.get('owner_plex_id') or ''))


PASSWORD_LIMIT_ID = 'start-password'


def _setup_cookie_path() -> str:
    from flask import current_app
    return (current_app.config.get('URL_BASE') or '').rstrip('/') + '/api/start/'


@bp.route('/api/start/account', methods=['POST'])
def start_account():
    """Create Account: the owner picks the username and password for the
    account the Plex step created (hashed the same way /login verifies it).
    The Plex email stays on the account only if Plex gave one.

    Only the browser that completed the Plex step (signed SameSite=Strict
    setup cookie plus its real session, never an implicit local owner) may
    call it, once; afterwards changes go through normal sign-in."""
    from flask import g
    from app import login_limiter

    # Signed-out, implicit, cookie-less or forged requests get a plain refusal
    # and never touch the limiter, so nobody else can lock the owner out.
    user = _cfg['get_current_user']()
    if not user or getattr(g, 'implicit_user', False) or not user.get('token'):
        return jsonify({'error': 'Sign in first'}), 401
    st = _load_state()
    if not _in_progress(user) or not user.get('id'):
        return jsonify({'error': 'Start again', 'restart': True}), 409
    if user['id'] != st.get('owner_uid') or not _setup_cookie_ok(st, user['id']):
        return jsonify({'error': 'Start again', 'restart': True}), 403

    # Only the owner's own Plex-verified browser gets here. Typing mistakes
    # (blank, too short, too long) never count; only probes of existing names
    # do, so the owner can't lock themselves out by fixing a form.
    limit_id = f"{PASSWORD_LIMIT_ID}:uid:{user['id']}"
    wait = login_limiter.retry_after('', limit_id)
    if wait:
        resp = jsonify({'error': 'Too many attempts. Try again later.', 'retry_after': wait})
        resp.headers['Retry-After'] = str(wait)
        return resp, 429

    def bad(message):
        return jsonify({'error': message}), 400

    body = request.get_json(silent=True) or {}
    username = body.get('username')
    password = body.get('password')
    if not isinstance(username, str) or not username.strip():
        return bad('Enter a username')
    username = _cfg['user_auth'].normalize_name(username) if hasattr(_cfg['user_auth'], 'normalize_name') \
        else username.strip()
    if not username:
        return bad('Enter a username')
    if len(username) > USERNAME_MAX:
        return bad(f'Use at most {USERNAME_MAX} characters for the username')
    if '@' in username:
        return bad("Username can't contain @")
    if not isinstance(password, str) or not password.strip():
        return bad('Enter a password')
    space = _cfg['user_auth'].password_space_problem(password) \
        if hasattr(_cfg['user_auth'], 'password_space_problem') else ''
    if space:
        return bad(space)
    if len(password) < PASSWORD_MIN:
        return bad(f'Use at least {PASSWORD_MIN} characters')
    if len(password) > PASSWORD_MAX:
        return bad(f'Use at most {PASSWORD_MAX} characters')

    with _lock:
        st = _load_state()
        if st.get('password_set') or _claim_active(st):
            return jsonify({'error': 'Password already set'}), 409
        st['password_claimed'] = time.time()
        _save_state(st)
        try:
            _cfg['user_auth'].set_name_and_password(user['id'], username, password)
        except ValueError as e:
            st.pop('password_claimed', None)
            _save_state(st)
            login_limiter.record_failure('', limit_id)
            return jsonify({'error': str(e)}), 400
        except Exception:
            st.pop('password_claimed', None)
            _save_state(st)
            raise
        st.pop('password_claimed', None)
        st['password_set'] = True
        _save_state(st)
    login_limiter.record_success('', limit_id)
    logger.info('Start: owner account created')
    resp = make_response(jsonify({'ok': True}))
    resp.delete_cookie(SETUP_COOKIE, path=_setup_cookie_path())
    return resp


@bp.route('/api/start/plex/pin', methods=['POST'])
def start_plex_pin():
    if not needs_start():
        return jsonify({'error': 'Luma is already set up'}), 409
    closed = _window_closed()
    if closed:
        return closed
    try:
        pin = create_pin()
    except Exception as e:
        logger.warning(f'Start: could not create Plex PIN: {e}')
        return jsonify({'error': "Couldn't reach Plex. Try again."}), 502
    nonce = _nonce()
    st = _load_state()
    if not nonce or nonce != st.get('nonce'):
        nonce = secrets.token_urlsafe(24)
        st = {'nonce': nonce}
        _save_state(st)
    with _lock:
        _pending[nonce] = {'pin_id': pin['id'], 'created': time.time()}
    mode = (request.get_json(silent=True) or {}).get('mode')
    if mode not in PLEX_AUTH_MODES:
        mode = 'popup'
    forward = forward_url(mode)
    resp = make_response(jsonify({'id': pin['id'], 'auth_url': auth_url(pin['code'], forward)}))
    resp.set_cookie(START_COOKIE, nonce, max_age=3600, httponly=True, samesite='Lax', path='/')
    return resp


@bp.route('/api/start/plex/pin', methods=['GET'])
def start_plex_poll():
    err = _require_fresh()
    if err:
        return err
    with _lock:
        p = dict(_pending.get(_nonce()) or {})
    if not p.get('pin_id'):
        return jsonify({'error': 'Start again', 'restart': True}), 409
    if not p.get('token'):
        if time.time() - p.get('created', 0) > 15 * 60:
            return jsonify({'status': 'expired'})
        try:
            token = check_pin(p['pin_id'])
        except Exception as e:
            logger.warning(f'Start: Plex PIN check failed: {e}')
            return jsonify({'status': 'waiting'})
        if not token:
            return jsonify({'status': 'waiting'})
        try:
            account = get_account(token)
            servers = get_owned_servers(token)
        except Exception as e:
            logger.warning(f'Start: Plex account lookup failed: {e}')
            return jsonify({'error': "Couldn't reach Plex. Try again."}), 502
        p.update(token=token, servers=servers)
        with _lock:
            _pending[_nonce()] = p
        st = _load_state()
        st['account'] = account
        _save_state(st)
    servers = p.get('servers') or []
    return jsonify({
        'status': 'authorized',
        'account': {'name': _load_state().get('account', {}).get('name', '')},
        'servers': [{'machine_id': s['machine_id'], 'name': s['name']} for s in servers],
    })


def _owner_name(ua, name) -> str:
    """The Plex account name when it is a usable username, else 'Owner'."""
    norm = getattr(ua, 'normalize_name', lambda n: (n or '').strip())(name if isinstance(name, str) else '')
    check = getattr(ua, 'name_problem', lambda n: None)
    if not norm or '@' in norm or check(norm):
        return 'Owner'
    return norm


@bp.route('/api/start/plex/server', methods=['POST'])
def start_plex_server():
    err = _require_fresh()
    if err:
        return err
    machine_id = ((request.get_json(silent=True) or {}).get('machine_id') or '').strip()
    with _lock:
        p = dict(_pending.get(_nonce()) or {})
    server = next((s for s in p.get('servers') or [] if s['machine_id'] == machine_id), None)
    if not p.get('token') or not server:
        return jsonify({'error': 'Start again', 'restart': True}), 409
    token = server.get('access_token') or p['token']
    conn = pick_connection(server, token)
    if not conn:
        return jsonify({'error': f"Couldn't connect to {server['name']}"}), 502
    libraries = _cfg['on_plex_saved'](conn['uri'], token, machine_id)
    logger.info(f"Start: connected to Plex server via {conn['kind']} connection ({libraries} libraries)")

    # The Plex account becomes the owner. Until the Access step saves a choice,
    # local addresses are open and everyone else is refused (no PIN exists yet).
    st = _load_state()
    account = st.get('account') or {}
    ua = _cfg['user_auth']
    with _first_account:
        if not needs_start():
            return jsonify({'error': 'Luma is already set up'}), 409
        try:
            user = ua.create_user(_owner_name(ua, account.get('name')), secrets.token_urlsafe(24), is_admin=True,
                                  status='active', email=account.get('email') or None)
        except ValueError as e:
            return jsonify({'error': str(e)}), 409
    from app import minimal
    # A minimal (test) build never opens local addresses: every request signs in.
    _cfg['settings'].update({'security': {'auth_required': 'enabled' if minimal.enabled() else 'disabled_local'}})
    plex_id = str(account.get('id') or '')
    st.update(server_name=server['name'], admin_via_start=True, completed=False, pin_set=False,
              password_set=False, owner_uid=user['id'], owner_plex_id=plex_id)
    st.pop('nonce', None)
    _save_state(st)
    with _lock:
        _pending.pop(_nonce(), None)
    session = ua.create_session(user['id'])
    resp = make_response(jsonify({'ok': True, 'server': server['name'], 'connection': conn['kind'],
                                  'libraries': libraries}))
    from flask import current_app
    resp.set_cookie(_cfg['session_cookie'], session, max_age=60 * 60 * 24 * 30,
                    httponly=True, samesite='Lax', secure=request.is_secure,
                    path=current_app.config.get('URL_BASE') or '/')
    resp.delete_cookie(START_COOKIE, path='/')
    resp.set_cookie(SETUP_COOKIE, setup_token(user['id'], plex_id), max_age=SETUP_TTL, httponly=True,
                    samesite='Strict', secure=request.is_secure, path=_setup_cookie_path())
    logger.info('Start: owner account created')
    return resp


def _same_origin_browser_request() -> bool:
    """A same-origin request: never cross-site (Sec-Fetch-Site), and an
    Origin or Referer that names this server is required. Signed-out
    requests skip the global CSRF guard, so Skip checks this itself."""
    from app.auth_routes import is_same_origin
    if request.headers.get('Sec-Fetch-Site', '').lower() in ('cross-site', 'same-site'):
        return False
    origin = request.headers.get('Origin')
    referer = request.headers.get('Referer')
    if origin:
        return is_same_origin(origin)
    return bool(referer) and is_same_origin(referer)


def _validate_new_account(body) -> tuple:
    """(username, password, error) with the same rules as Create account."""
    ua = _cfg['user_auth']
    username = body.get('username')
    password = body.get('password')
    if not isinstance(username, str) or not username.strip():
        return None, None, 'Enter a username'
    username = ua.normalize_name(username) if hasattr(ua, 'normalize_name') else username.strip()
    if not username:
        return None, None, 'Enter a username'
    if len(username) > USERNAME_MAX:
        return None, None, f'Use at most {USERNAME_MAX} characters for the username'
    if '@' in username:
        return None, None, "Username can't contain @"
    problem = getattr(ua, 'name_problem', lambda n: None)(username)
    if problem:
        return None, None, problem
    if not isinstance(password, str) or not password.strip():
        return None, None, 'Enter a password'
    space = ua.password_space_problem(password) if hasattr(ua, 'password_space_problem') else ''
    if space:
        return None, None, space
    if len(password) < PASSWORD_MIN:
        return None, None, f'Use at least {PASSWORD_MIN} characters'
    if len(password) > PASSWORD_MAX:
        return None, None, f'Use at most {PASSWORD_MAX} characters'
    return username, password, None


@bp.route('/api/start/skip', methods=['POST'])
def start_skip():
    """Skip Plex: the owner is created from a username and password alone.

    Only while the install is fresh and the setup window is open, only from
    the same network as Luma (skip_allowed: real LAN peer, no relay or
    Cloudflare headers, one of this install's own host names), only from a
    same-origin page, and only once: the "no users yet" check and the insert
    happen under the first-account lock. Plex is connected later from
    Settings > Plex."""
    if not needs_start():
        return jsonify({'error': 'Luma is already set up'}), 409
    closed = _window_closed()
    if closed:
        return closed
    if not skip_allowed() or not _same_origin_browser_request():
        return jsonify({'error': 'Continue with Plex to set up Luma from here'}), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'Enter a username'}), 400
    username, password, problem = _validate_new_account(body)
    if problem:
        return jsonify({'error': problem}), 400
    ua = _cfg['user_auth']
    with _first_account:
        if not needs_start():
            return jsonify({'error': 'Luma is already set up'}), 409
        try:
            user = ua.create_user(username, password, is_admin=True, status='active')
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        from app import minimal
        _cfg['settings'].update({'security': {'auth_required': 'enabled' if minimal.enabled() else 'disabled_local'}})
        st = {'admin_via_start': True, 'completed': False, 'pin_set': False, 'password_set': True,
              'owner_uid': user['id'], 'owner_plex_id': '', 'server_name': '', 'plex_skipped': True}
        _save_state(st)
    with _lock:
        _pending.pop(_nonce(), None)
    from app import access
    settings = _cfg.get('settings')
    client = access.client_ip(request, settings) if settings is not None else None
    logger.info('Start: owner account created (Plex skipped) from %s (peer %s, host %s)',
                client or 'unknown', request.remote_addr or 'unknown', request.host)
    session = ua.create_session(user['id'])
    resp = make_response(jsonify({'ok': True}))
    from flask import current_app
    resp.set_cookie(_cfg['session_cookie'], session, max_age=60 * 60 * 24 * 30,
                    httponly=True, samesite='Lax', secure=request.is_secure,
                    path=current_app.config.get('URL_BASE') or '/')
    resp.delete_cookie(START_COOKIE, path='/')
    return resp


# ---------------------------------------------------------------------------
# Settings > Plex: connect (or switch) the Plex server later. Owner only.
# ---------------------------------------------------------------------------

SETTINGS_PLEX_PATH = '/settings/plex'
_owner_pending: Dict[int, Dict[str, Any]] = {}   # owner uid -> {pin_id, created, token, servers}


def _owner_or_error():
    """(user, None) for the signed-in owner; else (None, error response).
    The owner is the account setup created; installs set up before the
    Start flow fall back to the first admin."""
    from flask import g
    user = _cfg['get_current_user']()
    if not user or getattr(g, 'implicit_user', False) or not user.get('id'):
        return None, (jsonify({'error': 'Sign in first'}), 401)
    if not user.get('is_admin'):
        return None, (jsonify({'error': 'Owner only'}), 403)
    owner = _load_state().get('owner_uid')
    if not owner:
        admins = [u for u in _cfg['user_auth'].list_users() if u.get('is_admin')]
        owner = min((u['id'] for u in admins), default=None)
    if user['id'] != owner:
        return None, (jsonify({'error': 'Owner only'}), 403)
    return user, None


def _plex_configured() -> bool:
    s = _cfg['settings']
    return bool(s.get('plex.url') and s.get('plex.token'))


@bp.route('/api/settings/plex', methods=['GET'])
def settings_plex_status():
    user, err = _owner_or_error()
    if err:
        return err
    st = _load_state()
    return jsonify({'connected': _plex_configured(), 'server': st.get('server_name') or ''})


@bp.route('/api/settings/plex/pin', methods=['POST'])
def settings_plex_pin():
    user, err = _owner_or_error()
    if err:
        return err
    try:
        pin = create_pin()
    except Exception as e:
        logger.warning(f'Settings: could not create Plex PIN: {e}')
        return jsonify({'error': "Couldn't reach Plex. Try again."}), 502
    with _lock:
        _owner_pending[user['id']] = {'pin_id': pin['id'], 'created': time.time()}
    mode = (request.get_json(silent=True) or {}).get('mode')
    if mode not in PLEX_AUTH_MODES:
        mode = 'popup'
    return jsonify({'id': pin['id'], 'auth_url': auth_url(pin['code'], forward_url(mode, SETTINGS_PLEX_PATH))})


@bp.route('/api/settings/plex/pin', methods=['GET'])
def settings_plex_poll():
    user, err = _owner_or_error()
    if err:
        return err
    with _lock:
        p = dict(_owner_pending.get(user['id']) or {})
    if not p.get('pin_id'):
        return jsonify({'status': 'none'})
    if time.time() - p.get('created', 0) > 15 * 60:
        # A sign-in (and any Plex token it got) lives 15 minutes at most.
        with _lock:
            _owner_pending.pop(user['id'], None)
        return jsonify({'status': 'expired'})
    if not p.get('token'):
        try:
            token = check_pin(p['pin_id'])
        except Exception as e:
            logger.warning(f'Settings: Plex PIN check failed: {e}')
            return jsonify({'status': 'waiting'})
        if not token:
            return jsonify({'status': 'waiting'})
        try:
            servers = get_owned_servers(token)
        except Exception as e:
            logger.warning(f'Settings: Plex server lookup failed: {e}')
            return jsonify({'error': "Couldn't reach Plex. Try again."}), 502
        p.update(token=token, servers=servers)
        with _lock:
            _owner_pending[user['id']] = p
    return jsonify({'status': 'authorized',
                    'servers': [{'machine_id': s['machine_id'], 'name': s['name']} for s in p.get('servers') or []]})


@bp.route('/api/settings/plex/server', methods=['POST'])
def settings_plex_server():
    user, err = _owner_or_error()
    if err:
        return err
    machine_id = ((request.get_json(silent=True) or {}).get('machine_id') or '')
    machine_id = machine_id.strip() if isinstance(machine_id, str) else ''
    with _lock:
        p = dict(_owner_pending.get(user['id']) or {})
    server = next((s for s in p.get('servers') or [] if s['machine_id'] == machine_id), None)
    if not p.get('token') or not server or time.time() - p.get('created', 0) > 15 * 60:
        return jsonify({'error': 'Sign in with Plex again'}), 409
    token = server.get('access_token') or p['token']
    conn = pick_connection(server, token)
    if not conn:
        return jsonify({'error': f"Couldn't connect to {server['name']}"}), 502
    libraries = _cfg['on_plex_saved'](conn['uri'], token, machine_id)
    with _lock:
        _owner_pending.pop(user['id'], None)
        st = _load_state()
        st['server_name'] = server['name']
        st.pop('plex_skipped', None)
        _save_state(st)
    logger.info(f"Settings: connected to Plex server via {conn['kind']} connection ({libraries} libraries)")
    return jsonify({'ok': True, 'server': server['name'], 'libraries': libraries})


@bp.route('/api/start/finish', methods=['POST'])
def start_finish():
    user = _cfg['get_current_user']()
    if not _in_progress(user):
        return jsonify({'ok': True, 'restarting': False})
    st = _load_state()
    st['completed'] = True
    st.pop('account', None)
    _save_state(st)
    restart = os.environ.get('LUMA_NO_RESTART', '') not in ('1', 'true')
    if restart:
        threading.Thread(target=_restart_soon, daemon=True).start()
    return jsonify({'ok': True, 'restarting': restart})


def _restart_soon():
    """Re-exec the server so every module picks up the new configuration."""
    time.sleep(1.0)
    logger.info('Start: setup finished, restarting Luma')
    try:
        sys.stdout.flush(); sys.stderr.flush()
    except Exception:
        pass
    os.execv(sys.executable, [sys.executable, '-m', 'app.main'])


def password_set() -> bool:
    """True when the owner chose a password in the Start flow."""
    return bool(_load_state().get('password_set'))


def created_by_start() -> bool:
    """True when the owner account was created by the Start flow."""
    return bool(_load_state().get('admin_via_start'))
