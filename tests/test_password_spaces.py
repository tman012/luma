"""Passwords are used exactly as typed: leading or trailing whitespace is
rejected when one is set, and sign-in never strips it."""

import pytest

import app.user_auth as user_auth

SPACE = "Password can't start or end with a space"
LAN = dict(environ_base={'REMOTE_ADDR': '192.168.1.95'}, base_url='http://192.168.1.62:5001')


@pytest.mark.parametrize('pw,problem', [
    (' pass-word-1', SPACE), ('pass-word-1 ', SPACE), ('\tpass-word-1', SPACE),
    ('pass word 1', ''), ('pass-word-1', ''), ('', ''),
])
def test_password_space_problem(pw, problem):
    assert user_auth.password_space_problem(pw) == problem


def _admin(monkeypatch):
    import app.main
    monkeypatch.setattr(app.main, '_require_admin', lambda: None)


@pytest.mark.parametrize('pw', [' new-pass-5678', 'new-pass-5678 '])
def test_admin_reset_rejects_spaces(test_app, monkeypatch, pw):
    _admin(monkeypatch)
    user = user_auth.create_user('qauser', 'pw-1234')
    r = test_app.test_client().put(f"/api/admin/users/{user['id']}/pin", json={'pin': pw}, **LAN)
    assert r.status_code == 400 and r.json['error'] == SPACE
    assert user_auth.authenticate('qauser', 'pw-1234') is not None


def test_admin_create_rejects_spaces(test_app, monkeypatch):
    _admin(monkeypatch)
    r = test_app.test_client().post('/api/admin/users', json={'name': 'newbie', 'pin': 'pass-word-1 '}, **LAN)
    assert r.status_code == 400 and r.json['error'] == SPACE
    assert user_auth.find_user('newbie') is None


def test_register_rejects_spaces(test_app, monkeypatch):
    import app.main
    monkeypatch.setattr(app.main, '_signup_open', lambda: True)
    monkeypatch.setattr(app.main, '_send_account_request_notification', lambda *a, **k: None)
    user_auth.create_user('Owner', 'pw-1234', is_admin=True)
    r = test_app.test_client().post('/api/auth/register',
                                    json={'name': 'Carol', 'email': 'carol@site.io', 'pin': ' pass-word-1'})
    assert r.status_code == 400 and r.json['error'] == SPACE


def _api_login(test_app, ident, pin):
    return test_app.test_client().post('/api/auth/login', json={'email': ident, 'name': ident, 'pin': pin},
                                       environ_base={'REMOTE_ADDR': '203.0.113.40'})


def test_api_login_does_not_strip_password(test_app):
    user_auth.create_user('carol', 'pass-word-1')
    assert _api_login(test_app, 'carol', 'pass-word-1').status_code == 200
    r = _api_login(test_app, 'carol', ' pass-word-1 ')
    assert r.status_code == 401 and r.json['error'] == 'Invalid email, username, or password'


def test_api_login_messages(test_app):
    r = _api_login(test_app, '', 'x')
    assert r.status_code == 400 and r.json['error'] == 'Enter your email or username and password'
    r = _api_login(test_app, 'nobody', 'wrong-pass')
    assert r.status_code == 401 and r.json['error'] == 'Invalid email, username, or password'
