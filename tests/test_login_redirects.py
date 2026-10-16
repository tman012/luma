"""Sign-in redirects and cookies (round 4, items 2, 8 and 10)."""

import re
from urllib.parse import quote

import pytest

import app.main
import app.user_auth as user_auth
from app.auth_routes import _is_safe_redirect

WAN = {'REMOTE_ADDR': '203.0.113.9'}


@pytest.fixture
def client(test_app):
    user_auth.create_user('owner', '1234', is_admin=True)
    return test_app.test_client()


@pytest.mark.parametrize('value', [
    '', 'movies', 'http://evil.com', '//evil.com', '/\\evil.com', '/\t/evil.com', '/\n/evil.com',
    '/\r/evil.com', '/ /evil.com', '/\x00x', '/\x7fx', '/%09/evil.com', '/%0A/evil.com',
    '/%2F/evil.com', '/%5Cevil.com', 'javascript:alert(1)', '/a\\b', '\\\\evil.com',
])
def test_unsafe_next_rejected(value):
    assert _is_safe_redirect(value) is False


@pytest.mark.parametrize('value', ['/', '/movies', '/tv/1?tab=cast', '/search?q=a%20b', '/a:b'])
def test_safe_next_allowed(value):
    assert _is_safe_redirect(value) is True


@pytest.mark.parametrize('raw', ['/%09/evil.com', '/%0A/evil.com', '/%0D%0A/evil.com', '/%5C/evil.com'])
def test_login_page_never_redirects_off_site(client, raw):
    page = client.get(f'/login?next={raw}', environ_base=WAN)
    assert page.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    r = client.post(f'/login?next={raw}', data={'username': 'owner', 'password': '1234', 'csrf_token': token},
                    environ_base=WAN)
    assert r.status_code == 302
    assert r.headers['Location'] == '/'


def test_login_redirects_to_safe_next_with_base(client, test_app):
    test_app.config['URL_BASE'] = '/luma'
    try:
        page = client.get('/login?next=/lumafoo', environ_base=WAN)
        assert 'next=/luma/lumafoo' in page.get_data(as_text=True).replace('%2F', '/')
    finally:
        test_app.config['URL_BASE'] = ''


def test_remember_me_on_by_default(client):
    page = client.get('/login', environ_base=WAN).get_data(as_text=True)
    assert re.search(r'<input type="checkbox" id="remember" name="remember" checked>', page)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = client.post('/login', data={'username': 'owner', 'password': '1234', 'csrf_token': token,
                                    'remember': 'on'}, environ_base=WAN)
    (cookie,) = [c for c in r.headers.getlist('Set-Cookie') if c.startswith('luma_auth_session=')]
    assert 'Max-Age=2592000' in cookie


def test_api_login_cookie_is_30_days(client):
    r = client.post('/api/auth/login', json={'name': 'owner', 'pin': '1234'}, environ_base=WAN)
    (cookie,) = [c for c in r.headers.getlist('Set-Cookie') if c.startswith('luma_auth_session=')]
    assert 'Max-Age=2592000' in cookie


@pytest.mark.parametrize('path,expected', [
    ('/settings', '/settings'),
    ('/settings?tab=security', '/settings?tab=security'),
])
def test_guard_next_has_no_trailing_question_mark(client, path, expected):
    r = client.get(path, environ_base=WAN)
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/login?next=' + quote(expected))
    assert not r.headers['Location'].endswith('%3F')


def test_legacy_cookie_deleted_in_one_place():
    import inspect
    from app import auth_routes
    main_src = open(app.main.__file__).read()
    assert main_src.count('delete_cookie(LEGACY_SESSION_COOKIE') == 2  # after_request hook: '/' and base
    assert "'luma_session'" not in inspect.getsource(auth_routes.logout)
    assert app.main.LEGACY_SESSION_COOKIE is auth_routes.LEGACY_SESSION_COOKIE


def test_logout_expires_legacy_cookie_via_hook(client):
    client.set_cookie('luma_session', 'old')
    r = client.post('/logout', headers={'Origin': 'http://localhost'}, environ_base=WAN)
    assert any(c.startswith('luma_session=;') for c in r.headers.getlist('Set-Cookie'))
