"""Start flow for a brand-new install: Welcome, Sign in with Plex, Access, Done.

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
PRODUCT = 'Luma'

bp = Blueprint('start_flow', __name__)

_cfg: Dict[str, Any] = {}
_pending: Dict[str, Dict[str, Any]] = {}   # start nonce -> {pin_id, token, servers}
_lock = threading.Lock()


def init(*, settings, user_auth, session_cookie: str, client_id: str,
         on_plex_saved: Callable[[str, str, str], int], get_current_user: Callable[[], Optional[dict]]):
    _cfg.update(settings=settings, user_auth=user_auth, session_cookie=session_cookie,
                client_id=client_id, on_plex_saved=on_plex_saved, get_current_user=get_current_user)


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


def needs_start() -> bool:
    return not _cfg['user_auth'].list_users()


def _in_progress(user: Optional[dict]) -> bool:
    st = _load_state()
    return bool(user and user.get('is_admin') and st.get('admin_via_start') and not st.get('completed'))


def _nonce() -> str:
    return request.cookies.get(START_COOKIE, '')


def _require_fresh():
    if not needs_start():
        return jsonify({'error': 'Luma is already set up'}), 409
    st = _load_state()
    if not _nonce() or _nonce() != st.get('nonce'):
        return jsonify({'error': 'Start again', 'restart': True}), 409
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


def auth_url(code: str) -> str:
    q = urllib.parse.urlencode({
        'clientID': _cfg['client_id'],
        'code': code,
        'context[device][product]': PRODUCT,
    })
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
        out['step'] = 'welcome'
    elif out['in_progress']:
        out['step'] = 'done'
        out['server'] = _load_state().get('server_name', '')
    return jsonify(out)


@bp.route('/api/start/plex/pin', methods=['POST'])
def start_plex_pin():
    if not needs_start():
        return jsonify({'error': 'Luma is already set up'}), 409
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
    resp = make_response(jsonify({'id': pin['id'], 'auth_url': auth_url(pin['code'])}))
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
    try:
        user = ua.create_user(account.get('name') or 'Owner', secrets.token_urlsafe(24), is_admin=True,
                              status='active', email=account.get('email') or None)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    _cfg['settings'].update({'security': {'access_mode': 'pin_remote'}})
    st.update(server_name=server['name'], admin_via_start=True, completed=False, pin_set=False)
    st.pop('nonce', None)
    _save_state(st)
    with _lock:
        _pending.pop(_nonce(), None)
    session = ua.create_session(user['id'])
    resp = make_response(jsonify({'ok': True, 'server': server['name'], 'connection': conn['kind'],
                                  'libraries': libraries}))
    resp.set_cookie(_cfg['session_cookie'], session, max_age=60 * 60 * 24 * 30,
                    httponly=False, samesite='Lax', secure=False, path='/')
    resp.delete_cookie(START_COOKIE, path='/')
    logger.info('Start: owner account created')
    return resp


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


def created_by_start() -> bool:
    """True when the owner account was created by the Start flow."""
    return bool(_load_state().get('admin_via_start'))
