"""Login hotfix: email or name sign-in, admin seed never replaces a password,
rehash round trip, CSRF retry, no-store auth pages, service worker rules."""

import re
from pathlib import Path

import app.user_auth as user_auth

ROOT = Path(__file__).resolve().parent.parent
IP = '203.0.113.21'


def _token(resp):
    return re.search(r'name="csrf_token" value="([^"]+)"', resp.get_data(as_text=True)).group(1)


def _form_login(c, username, password):
    page = c.get('/login', environ_base={'REMOTE_ADDR': IP})
    return c.post('/login', data={'username': username, 'password': password,
                                  'csrf_token': _token(page)},
                  environ_base={'REMOTE_ADDR': IP})


def _signed_in(resp):
    return any(h.startswith('luma_auth_session=') and 'luma_auth_session=;' not in h
               for h in resp.headers.getlist('Set-Cookie'))


def _owner():
    user_auth.create_user('Owner', 'correct-horse', is_admin=True)
    with user_auth._get_db() as conn:
        conn.execute("UPDATE users SET email = ? WHERE name = ?", ('Owner@Example.com', 'Owner'))


def test_form_accepts_email_any_case(test_app):
    _owner()
    c = test_app.test_client()
    assert _signed_in(_form_login(c, 'owner@example.COM', 'correct-horse'))


def test_form_accepts_name_any_case(test_app):
    _owner()
    c = test_app.test_client()
    assert _signed_in(_form_login(c, 'OWNER', 'correct-horse'))


def test_form_rejects_wrong_password(test_app):
    _owner()
    c = test_app.test_client()
    r = _form_login(c, 'owner@example.com', 'nope')
    assert not _signed_in(r)


def test_api_accepts_email_in_name_field_and_name(test_app):
    _owner()
    for body in ({'name': 'owner@example.com', 'pin': 'correct-horse'},
                 {'email': 'OWNER@example.com', 'pin': 'correct-horse'},
                 {'name': 'owner', 'pin': 'correct-horse'}):
        r = test_app.test_client().post('/api/auth/login', json=body, environ_base={'REMOTE_ADDR': IP})
        assert r.status_code == 200, body


def test_find_user_prefers_email_for_at_sign():
    _owner()
    assert user_auth.find_user('OWNER@EXAMPLE.COM')['name'] == 'Owner'
    assert user_auth.find_user('owner')['name'] == 'Owner'
    assert user_auth.find_user('nobody@example.com') is None


def test_legacy_rehash_then_login_round_trip(test_app):
    _owner()
    with user_auth._get_db() as conn:
        conn.execute("UPDATE users SET pin_hash = ? WHERE name = ?",
                     (user_auth._hash_pin('correct-horse'), 'Owner'))
    c = test_app.test_client()
    assert _signed_in(_form_login(c, 'owner', 'correct-horse'))
    stored = user_auth.get_user_by_name('Owner')['pin_hash']
    assert not user_auth._is_legacy_hash(stored)
    from werkzeug.security import check_password_hash
    assert check_password_hash(stored, 'correct-horse')
    c2 = test_app.test_client()
    assert _signed_in(_form_login(c2, 'owner@example.com', 'correct-horse'))


def test_failed_legacy_check_does_not_rehash():
    _owner()
    legacy = user_auth._hash_pin('correct-horse')
    with user_auth._get_db() as conn:
        conn.execute("UPDATE users SET pin_hash = ? WHERE name = ?", (legacy, 'Owner'))
    assert user_auth.authenticate('owner', 'wrong') is None
    assert user_auth.get_user_by_name('Owner')['pin_hash'] == legacy


def _seed(monkeypatch, pin='env-admin-pin'):
    monkeypatch.setattr(user_auth, 'ADMIN_NAME', 'Owner')
    monkeypatch.setattr(user_auth, 'ADMIN_PIN', pin)
    monkeypatch.setattr(user_auth, 'ADMIN_EMAIL', 'owner@example.com')
    user_auth.init_db()


def test_admin_seed_never_replaces_existing_password(monkeypatch):
    _owner()
    before = user_auth.get_user_by_name('Owner')['pin_hash']
    _seed(monkeypatch)
    after = user_auth.get_user_by_name('Owner')
    assert after['pin_hash'] == before
    assert after['status'] == 'active'
    assert user_auth.authenticate('owner', 'correct-horse') is not None
    assert user_auth.authenticate('owner', 'env-admin-pin') is None


def test_admin_seed_creates_missing_admin(monkeypatch):
    _seed(monkeypatch)
    assert user_auth.authenticate('owner@example.com', 'env-admin-pin') is not None


def test_admin_seed_fills_empty_password_only(monkeypatch):
    _owner()
    with user_auth._get_db() as conn:
        conn.execute("UPDATE users SET pin_hash = '' WHERE name = ?", ('Owner',))
    _seed(monkeypatch)
    assert user_auth.authenticate('owner', 'env-admin-pin') is not None


def test_csrf_failure_rerenders_form_with_fresh_token(test_app):
    _owner()
    c = test_app.test_client()
    c.get('/login', environ_base={'REMOTE_ADDR': IP})
    r = c.post('/login', data={'username': 'owner', 'password': 'correct-horse',
                               'csrf_token': 'stale'}, environ_base={'REMOTE_ADDR': IP})
    assert r.status_code == 400
    assert not _signed_in(r)
    body = r.get_data(as_text=True)
    assert 'value="owner"' in body
    r2 = c.post('/login', data={'username': 'owner', 'password': 'correct-horse',
                                'csrf_token': _token(r)}, environ_base={'REMOTE_ADDR': IP})
    assert _signed_in(r2)


def test_login_responses_are_no_store(test_app):
    _owner()
    c = test_app.test_client()
    assert 'no-store' in c.get('/login').headers.get('Cache-Control', '')
    r = _form_login(c, 'owner', 'wrong')
    assert 'no-store' in r.headers.get('Cache-Control', '')


def test_login_page_theme_and_logo(test_app):
    body = test_app.test_client().get('/login').get_data(as_text=True)
    for token in ('#15102a', '#3b82f6', 'images/luma_text.webp'):
        assert token in body
    for old in ('#111318', '#282a2f', '#adc7ff', 'Google Sans'):
        assert old not in body
    assert 'Email or username' in body and 'Sign in' in body


def test_service_worker_never_caches_auth_pages():
    sw = (ROOT / 'static' / 'sw.js').read_text()
    assert "path === '/login'" in sw and "path === '/logout'" in sw
    assert 'resp.redirected' in sw and "'X-Luma-App-Shell'" in sw


def test_auth_api_responses_are_no_store(test_app):
    _owner()
    r = test_app.test_client().post('/api/auth/login', json={'name': 'owner', 'pin': 'correct-horse'})
    assert 'no-store' in r.headers.get('Cache-Control', '')


# --- Tunnel spoof guard: a LAN X-Forwarded-For never makes a tunnel request local ---

from types import SimpleNamespace as _NS

import pytest as _pytest

from app import access as _access


class _Cfg:
    def __init__(self, **kv):
        self.kv = {f'security.{k}': v for k, v in kv.items()}

    def get(self, key, default=None):
        return self.kv.get(key, default)


_PROXIES = '127.0.0.1/32, ::1/128'


def _via(peer, host='luma.lan', **headers):
    hdrs = {k.replace('_', '-'): v for k, v in headers.items()}
    hdrs.setdefault('Host', host)
    from werkzeug.datastructures import Headers
    return _NS(remote_addr=peer, headers=Headers(hdrs), host=host, environ={})


@_pytest.mark.parametrize('headers', [
    # cloudflared -> Caddy -> app, attacker sent XFF: 192.168.1.95
    {'X_Forwarded_For': '192.168.1.95', 'Cf_Connecting_Ip': '198.51.100.7', 'Cf_Ray': 'abc-LAX'},
    {'X_Forwarded_For': '192.168.1.95', 'Cf_Ray': 'abc-LAX'},
    {'X_Forwarded_For': '192.168.1.95', 'CDN_Loop': 'cloudflare'},
    # appended chain: the real (public) hop is nearest
    {'X_Forwarded_For': '192.168.1.95, 198.51.100.7'},
    {'X_Forwarded_For': '192.168.1.95, 198.51.100.7, 127.0.0.1'},
    # X-Real-IP is never used to decide locality
    {'X_Real_IP': '192.168.1.95'},
])
def test_tunnel_request_with_spoofed_lan_xff_requires_sign_in(headers):
    cfg = _Cfg(trusted_proxies=_PROXIES, hostnames='luma.lan', auth_required='disabled_local')
    for peer in ('127.0.0.1', '::1'):
        req = _via(peer, **headers)
        assert _access.is_local_request(req, cfg) is False
        assert _access.should_authenticate(cfg, req) is True


def test_spoofed_lan_xff_from_untrusted_peer_requires_sign_in():
    cfg = _Cfg(trusted_proxies='', hostnames='luma.lan', auth_required='disabled_local')
    req = _via('127.0.0.1', X_Forwarded_For='192.168.1.95')
    assert _access.client_ip(req, cfg) is None
    assert _access.should_authenticate(cfg, req) is True


def test_tunnel_spoof_requires_sign_in_in_app(test_app):
    import app.main
    app.main.settings.update({'security': {'trusted_proxies': _PROXIES,
                                           'auth_required': 'disabled_local'}})
    r = test_app.test_client().get(
        '/api/auth/me',
        headers={'X-Forwarded-For': '192.168.1.95', 'CF-Connecting-IP': '198.51.100.7',
                 'CF-Ray': 'abc-LAX'},
        environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert r.status_code == 401
