"""Only luma_auth_session is issued or accepted (round 3, item 9)."""

import pytest
import app.user_auth as user_auth


def _cookies(resp, name):
    return [c for c in resp.headers.getlist('Set-Cookie') if c.startswith(f'{name}=')]


@pytest.fixture
def owner(test_app):
    return user_auth.create_user('owner', '1234', is_admin=True)


def test_legacy_cookie_not_accepted(test_app, owner):
    token = user_auth.create_session(owner['id'])
    c = test_app.test_client()
    c.set_cookie('luma_session', token)
    r = c.get('/api/auth/me', environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert r.status_code == 401
    expired = _cookies(r, 'luma_session')
    assert expired and 'Max-Age=0' in expired[0]


def test_new_cookie_accepted(test_app, owner):
    token = user_auth.create_session(owner['id'])
    c = test_app.test_client()
    c.set_cookie('luma_auth_session', token)
    r = c.get('/api/auth/me', environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert r.status_code == 200
    assert r.get_json()['implicit'] is False


@pytest.mark.parametrize('scheme,secure', [('http', False), ('https', True)])
def test_api_login_cookie_flags(test_app, owner, scheme, secure):
    test_app.config['URL_BASE'] = '/luma'
    r = test_app.test_client().post('/api/auth/login', json={'name': 'owner', 'pin': '1234'},
                                    base_url=f'{scheme}://luma.example',
                                    environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert r.status_code == 200
    (cookie,) = _cookies(r, 'luma_auth_session')
    assert 'HttpOnly' in cookie
    assert ('Secure' in cookie) is secure
    assert 'Path=/luma' in cookie
    assert not [c for c in _cookies(r, 'luma_session') if 'Max-Age=0' not in c]


def test_form_login_cookie_secure_per_scheme(test_app, owner):
    import re
    for scheme, secure in (('http', False), ('https', True)):
        c = test_app.test_client()
        page = c.get('/login', base_url=f'{scheme}://luma.example',
                     environ_base={'REMOTE_ADDR': '203.0.113.9'})
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        r = c.post('/login', data={'username': 'owner', 'password': '1234', 'csrf_token': token},
                   base_url=f'{scheme}://luma.example', environ_base={'REMOTE_ADDR': '203.0.113.9'})
        assert r.status_code == 302
        (cookie,) = _cookies(r, 'luma_auth_session')
        assert 'HttpOnly' in cookie
        assert ('Secure' in cookie) is secure


def test_no_code_issues_luma_session():
    from pathlib import Path
    app_dir = Path(user_auth.__file__).parent
    for f in app_dir.glob('*.py'):
        src = f.read_text()
        assert "set_cookie('luma_session'" not in src and 'set_cookie("luma_session"' not in src, f
        assert "cookies.get('luma_session')" not in src and 'cookies.get("luma_session")' not in src, f
