"""/login uses app.login_limiter; no duplicate limiter in auth_routes (round 3, item 10)."""

import re
from pathlib import Path

import app.user_auth as user_auth


def _post(c, password, ip='203.0.113.9'):
    page = c.get('/login', environ_base={'REMOTE_ADDR': ip})
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    return c.post('/login', data={'username': 'owner', 'password': password, 'csrf_token': token},
                  environ_base={'REMOTE_ADDR': ip})


def test_form_login_locks_after_five_failures(test_app):
    from app import login_limiter
    user_auth.create_user('owner', '1234', is_admin=True)
    c = test_app.test_client()
    for _ in range(login_limiter.MAX_FAILURES):
        r = _post(c, 'wrong')
        assert 'rate_limited' not in r.headers['Location']
    assert login_limiter.retry_after('203.0.113.9', 'owner') > 0
    r = _post(c, '1234')
    assert 'rate_limited=1' in r.headers['Location']
    assert not [h for h in r.headers.getlist('Set-Cookie') if h.startswith('luma_auth_session=')]


def test_success_clears_shared_limiter(test_app):
    from app import login_limiter
    user_auth.create_user('owner', '1234', is_admin=True)
    c = test_app.test_client()
    _post(c, 'wrong')
    assert login_limiter._fail
    _post(c, '1234')
    assert login_limiter.retry_after('203.0.113.9', 'owner') == 0


def test_no_duplicate_limiter_in_auth_routes():
    import app.auth_routes as ar
    src = Path(ar.__file__).read_text()
    assert 'defaultdict' not in src
    assert 'login_limiter.retry_after' in src and 'login_limiter.record_failure' in src
    for name in ('_login_attempts', 'reset_rate_limiter', '_check_rate_limit', 'RATE_LIMIT'):
        assert name not in src, name


# -- only wrong credentials count --------------------------------------------

def test_form_blank_password_not_counted(test_app):
    from app import login_limiter
    user_auth.create_user('owner', '1234', is_admin=True)
    c = test_app.test_client()
    for _ in range(login_limiter.MAX_FAILURES * 2):
        _post(c, '')
    assert login_limiter._fail == {}


def test_api_blank_pin_not_counted(test_app):
    from app import login_limiter
    user_auth.create_user('owner', '1234', is_admin=True)
    c = test_app.test_client()
    for _ in range(login_limiter.MAX_FAILURES * 2):
        r = c.post('/api/auth/login', json={'name': 'owner', 'pin': ''},
                   environ_base={'REMOTE_ADDR': '203.0.113.9'})
        assert r.status_code == 400
    assert login_limiter._fail == {}
    r = c.post('/api/auth/login', json={'name': 'owner', 'pin': 'nope'},
               environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert r.status_code == 401 and login_limiter._fail


def test_unlock_blank_pin_not_counted(test_app, monkeypatch):
    import app.main as main
    from app import login_limiter
    monkeypatch.setattr(main._start_flow, 'created_by_start', lambda: True)
    monkeypatch.setattr(main, '_pin_state', lambda: True)
    c = test_app.test_client()
    for _ in range(login_limiter.MAX_FAILURES * 2):
        r = c.post('/api/auth/unlock', json={'pin': ''}, environ_base={'REMOTE_ADDR': '203.0.113.9'})
        assert r.status_code == 400 and r.json['error'] == 'Enter your PIN'
    assert login_limiter._fail == {}
