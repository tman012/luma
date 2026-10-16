"""Reviewer follow-ups on PR #6: sign-up identity clashes, one rate-limit
bucket per account, offline copy only for the real app page, API status only
after the password, single-use form token, small fixes."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import app.user_auth as user_auth

ROOT = Path(__file__).resolve().parent.parent
IP = '203.0.113.31'


def _signup_on(monkeypatch):
    import app.main
    monkeypatch.setattr(app.main, '_signup_open', lambda: True)
    monkeypatch.setattr(app.main, '_send_account_request_notification', lambda *a, **k: None)


# --- MEDIUM 1: names and emails share one namespace ---

def test_name_with_at_sign_rejected():
    with pytest.raises(ValueError, match='@'):
        user_auth.create_user('alex@example.com', 'pw-1234')


def test_new_email_cannot_match_existing_name():
    user_auth.create_user('carol', 'pw-1234')
    with pytest.raises(ValueError, match='email'):
        user_auth.create_user('carol2', 'pw-1234', email='CAROL')


def test_new_name_cannot_match_existing_email():
    user_auth.create_user('Alex', 'pw-1234', email='alex@example.com')
    with pytest.raises(ValueError):
        user_auth.create_user('ALEX@EXAMPLE.COM', 'pw-1234')


def test_duplicate_email_any_case_rejected():
    user_auth.create_user('Alex', 'pw-1234', email='alex@example.com')
    with pytest.raises(ValueError, match='email'):
        user_auth.create_user('Alexa', 'pw-1234', email='Alex@Example.COM')


def test_register_rejects_at_in_name_and_cross_clashes(test_app, monkeypatch):
    _signup_on(monkeypatch)
    user_auth.create_user('Owner', 'pw-1234', is_admin=True, email='owner@example.com')
    c = test_app.test_client()
    r = c.post('/api/auth/register', json={'name': 'owner@example.com', 'email': 'x@example.com', 'pin': 'pw-1234'})
    assert r.status_code == 400
    r = c.post('/api/auth/register', json={'name': 'Someone', 'email': 'Owner@Example.com', 'pin': 'pw-1234'})
    assert r.status_code == 409
    r = c.post('/api/auth/register', json={'name': 'Carol', 'email': 'carol@site.io', 'pin': 'pw-1234'})
    assert r.status_code == 200
    assert user_auth.get_user_by_email('carol@site.io')['status'] == 'pending'


def test_pending_signup_cannot_shadow_active_users_name(test_app, monkeypatch):
    _signup_on(monkeypatch)
    user_auth.create_user('Owner', 'pw-1234', is_admin=True)
    with user_auth._get_db() as conn:  # legacy row created before '@' was banned
        conn.execute("INSERT INTO users (name, pin_hash, is_admin, status) VALUES (?, ?, 0, 'active')",
                     ('carol@site.io', user_auth.generate_password_hash('carol-pw')))
    r = test_app.test_client().post('/api/auth/register',
                                    json={'name': 'Mallory', 'email': 'Carol@Site.io', 'pin': 'pw-1234'})
    assert r.status_code == 409
    assert user_auth.authenticate('carol@site.io', 'carol-pw') is not None


def _seed(monkeypatch, email):
    monkeypatch.setattr(user_auth, 'ADMIN_NAME', 'Owner')
    monkeypatch.setattr(user_auth, 'ADMIN_PIN', 'env-pin')
    monkeypatch.setattr(user_auth, 'ADMIN_EMAIL', email)
    user_auth.init_db()


def test_admin_email_not_duplicated_onto_admin(monkeypatch):
    user_auth.create_user('Owner', 'pw-1234', is_admin=True)
    user_auth.create_user('Other', 'pw-1234', email='shared@example.com')
    _seed(monkeypatch, 'shared@example.com')
    assert user_auth.get_user_by_name('Owner')['email'] in (None, '')
    assert user_auth.get_user_by_email('shared@example.com')['name'] == 'Other'


def test_admin_email_env_is_lowercased():
    import importlib
    old = os.environ.get('LUMA_ADMIN_EMAIL')
    os.environ['LUMA_ADMIN_EMAIL'] = '  Owner@Example.COM '
    try:
        src = (ROOT / 'app' / 'user_auth.py').read_text()
        line = [l for l in src.splitlines() if l.startswith('ADMIN_EMAIL =')][0]
        ns = {'os': os}
        exec(line, ns)
        assert ns['ADMIN_EMAIL'] == 'owner@example.com'
    finally:
        if old is None:
            os.environ.pop('LUMA_ADMIN_EMAIL', None)
        else:
            os.environ['LUMA_ADMIN_EMAIL'] = old


# --- MEDIUM 2: one bucket per account ---

def test_name_and_email_share_one_bucket():
    from app import login_limiter
    user_auth.create_user('Owner', 'pw-1234', email='owner@example.com')
    for i in range(login_limiter.MAX_FAILURES):
        login_limiter.record_failure('', 'owner' if i % 2 else 'OWNER@example.com')
    assert login_limiter.retry_after('', 'owner') > 0
    assert login_limiter.retry_after('', 'owner@example.com') > 0


def test_success_by_email_clears_name_bucket():
    from app import login_limiter
    user = user_auth.create_user('Owner', 'pw-1234', email='owner@example.com')
    login_limiter.record_failure('', 'owner')
    assert f"uid:{user['id']}" in login_limiter._fail
    login_limiter.record_success('', 'owner@example.com')
    assert f"uid:{user['id']}" not in login_limiter._fail


def test_unknown_identifier_uses_normalized_key():
    from app import login_limiter
    login_limiter.record_failure('', '  Nobody@Example.com ')
    assert 'id:nobody@example.com' in login_limiter._fail


def test_form_lockout_spans_name_and_email(test_app):
    from app import login_limiter
    user_auth.create_user('Owner', 'pw-1234', email='owner@example.com')
    c = test_app.test_client()
    for i in range(login_limiter.MAX_FAILURES):
        page = c.get('/login', environ_base={'REMOTE_ADDR': '127.0.0.1'})
        tok = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        c.post('/login', data={'username': 'owner' if i % 2 else 'owner@example.com',
                               'password': 'wrong', 'csrf_token': tok},
               environ_base={'REMOTE_ADDR': '127.0.0.1'})
    page = c.get('/login', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    tok = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    r = c.post('/login', data={'username': 'owner', 'password': 'pw-1234', 'csrf_token': tok},
               environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert 'rate_limited=1' in r.headers['Location']


# --- MEDIUM 3: offline copy only for the real app page ---

NODE = shutil.which('node')


def _sw(scope, spec):
    env = dict(os.environ, SW_SHELL_RESP=json.dumps(spec))
    out = subprocess.run([NODE, str(ROOT / 'tests/js/sw_harness.js'), str(ROOT / 'static/sw.js'), scope],
                         capture_output=True, text=True, timeout=30, check=True, env=env)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason='node is not installed')
def test_install_saves_real_app_page():
    o = 'http://192.0.2.10:5001'
    r = _sw(f'{o}/luma/', {'ok': True, 'url': f'{o}/luma/', 'headers': {'X-Luma-App-Shell': '1'}})
    assert {'url': '/luma/', 'redirect': 'manual'} in r['fetched']
    assert r['puts'] == ['/luma/']


@pytest.mark.skipif(NODE is None, reason='node is not installed')
@pytest.mark.parametrize('spec', [
    {'ok': False, 'type': 'opaqueredirect', 'url': 'http://192.0.2.10:5001/'},         # redirect to sign-in
    {'ok': True, 'redirected': True, 'url': 'http://192.0.2.10:5001/login?next=/',
     'headers': {'X-Luma-App-Shell': '1'}},
    {'ok': True, 'url': 'http://192.0.2.10:5001/login', 'headers': {'X-Luma-App-Shell': '1'}},
    {'ok': True, 'url': 'http://192.0.2.10:5001/'},                                    # no marker
    {'ok': False, 'url': 'http://192.0.2.10:5001/', 'headers': {'X-Luma-App-Shell': '1'}},
])
def test_install_never_saves_sign_in_or_redirect(spec):
    r = _sw('http://192.0.2.10:5001/', spec)
    assert r['puts'] == []


def test_sw_version_bumped():
    src = (ROOT / 'static/sw.js').read_text()
    n = int(re.search(r"media-kiosk-v(\d+)", src).group(1))
    assert n >= 106


def test_app_page_is_marked(test_app, tmp_path, monkeypatch):
    import app.main
    spa = tmp_path / 'spa'
    spa.mkdir()
    (spa / 'index.html').write_text('<!doctype html><title>Luma</title>')
    monkeypatch.setitem(app.main.app.config, 'SPA_DIR', str(spa))
    user_auth.create_user('Owner', 'pw-1234', is_admin=True)
    r = test_app.test_client().get('/', environ_base={'REMOTE_ADDR': '192.168.1.95'},
                                   base_url='http://192.168.1.62:5001')
    assert r.status_code == 200
    assert r.headers.get('X-Luma-App-Shell') == '1'
    login = test_app.test_client().get('/login')
    assert 'X-Luma-App-Shell' not in login.headers


# --- LOWS ---

def test_api_hides_status_without_password(test_app):
    user_auth.create_user('Pend', 'pw-1234', status='pending', email='pend@example.com')
    c = test_app.test_client()
    r = c.post('/api/auth/login', json={'email': 'pend@example.com', 'pin': 'wrong'},
               environ_base={'REMOTE_ADDR': IP})
    assert r.status_code == 401
    r = test_app.test_client().post('/api/auth/login', json={'email': 'pend@example.com', 'pin': 'pw-1234'},
                                    environ_base={'REMOTE_ADDR': IP})
    assert r.status_code == 403


def test_unknown_user_still_hashes(monkeypatch):
    calls = []
    real = user_auth.check_password_hash
    monkeypatch.setattr(user_auth, 'check_password_hash', lambda h, p: calls.append(1) or real(h, p))
    assert user_auth.authenticate('nobody', 'pw') is None
    assert calls


def test_csrf_token_single_use_after_sign_in(test_app):
    user_auth.create_user('Owner', 'pw-1234', is_admin=True)
    c = test_app.test_client()
    page = c.get('/login', environ_base={'REMOTE_ADDR': IP})
    tok = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    c.post('/login', data={'username': 'owner', 'password': 'pw-1234', 'csrf_token': tok},
           environ_base={'REMOTE_ADDR': IP})
    with c.session_transaction() as s:
        assert 'csrf_token' not in s


# --- NITS ---

def test_legacy_compare_uses_hmac():
    src = (ROOT / 'app/user_auth.py').read_text()
    assert 'hmac.compare_digest(pin_hash, _hash_pin(pin))' in src
    assert 'pin_hash != _hash_pin(pin)' not in src


def test_logo_alt_empty_and_error_text(test_app):
    user_auth.create_user('Owner', 'pw-1234', is_admin=True)
    c = test_app.test_client()
    assert 'alt=""' in c.get('/login').get_data(as_text=True)
    page = c.get('/login', environ_base={'REMOTE_ADDR': IP})
    tok = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    r = c.post('/login', data={'username': 'owner', 'password': 'bad', 'csrf_token': tok},
               environ_base={'REMOTE_ADDR': IP}, follow_redirects=True)
    assert 'Wrong email, username or password.' in r.get_data(as_text=True)


def test_logout_get_405_is_no_store(test_app):
    r = test_app.test_client().get('/logout')
    assert r.status_code == 405
    assert 'no-store' in r.headers.get('Cache-Control', '')
