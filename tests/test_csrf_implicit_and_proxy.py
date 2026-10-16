"""CSRF and proxy-derived scheme/host (round 3, items 5 and 8)."""

import re

import pytest

import app.user_auth as user_auth

LAN = {'REMOTE_ADDR': '192.168.1.20'}


@pytest.fixture
def owner(test_app):
    return user_auth.create_user('owner', '1234', is_admin=True)


@pytest.fixture
def client(test_app, owner):
    return test_app.test_client()


@pytest.mark.parametrize('headers', [
    {'Origin': 'http://evil.com'},
    {'Referer': 'http://evil.com/attack'},
    {'Origin': 'http://127.0.0.1:5302.evil.com'},
    {'Origin': 'https://localhost'},                 # scheme mismatch
    {'Origin': 'http://localhost', 'Referer': 'http://evil.com/x'},
])
def test_implicit_lan_owner_cross_origin_rejected(client, headers):
    r = client.post('/api/settings', json={}, headers=headers, environ_base=LAN)
    assert r.status_code == 403
    assert 'CSRF' in r.get_json()['error']


def test_implicit_lan_owner_same_origin_or_none_allowed(client):
    for headers in ({}, {'Origin': 'http://localhost'}, {'Referer': 'http://localhost/settings'}):
        r = client.post('/api/settings', json={}, headers=headers, environ_base=LAN)
        assert not (r.status_code == 403 and 'CSRF' in (r.get_json() or {}).get('error', '')), headers


def test_same_origin_helper_exact():
    from flask import Flask
    from app.auth_routes import is_same_origin
    app = Flask(__name__)
    with app.test_request_context('/', base_url='http://127.0.0.1:5302'):
        assert is_same_origin('http://127.0.0.1:5302')
        assert is_same_origin('http://127.0.0.1:5302/settings?x=1')
        assert not is_same_origin('http://127.0.0.1:5302.evil.com')
        assert not is_same_origin('http://127.0.0.1:53021')
        assert not is_same_origin('http://127.0.0.1')
        assert not is_same_origin('https://127.0.0.1:5302')
        assert not is_same_origin('//127.0.0.1:5302')
        assert not is_same_origin('')
        assert not is_same_origin(None)


def test_logout_and_csrf_guard_share_the_helper():
    from pathlib import Path
    import app.auth_routes as ar
    src = Path(ar.__file__).read_text()
    main_src = (Path(ar.__file__).parent / 'main.py').read_text()
    assert src.count('def is_same_origin') == 1 and '_is_same_origin' not in src
    assert 'is_same_origin(' in src.split('def logout')[1]
    assert 'from app.auth_routes import is_same_origin' in main_src


def test_logout_rejects_prefix_spoof(client):
    r = client.post('/logout', headers={'Origin': 'http://127.0.0.1:5302.evil.com'},
                    base_url='http://127.0.0.1:5302')
    assert r.status_code == 403
    r = client.post('/logout', headers={'Origin': 'http://127.0.0.1:5302'},
                    base_url='http://127.0.0.1:5302')
    assert r.status_code == 302


def _form_login(c, **kw):
    page = c.get('/login', **kw)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    return c.post('/login', data={'username': 'owner', 'password': '1234', 'csrf_token': token}, **kw)


def test_forwarded_proto_trusted_only_from_trusted_proxy(test_app, client):
    import app.main
    hdrs = {'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'luma.example.com',
            'X-Forwarded-For': '203.0.113.9'}
    # Not a trusted proxy: forwarded scheme/host are ignored.
    r = _form_login(client, headers=hdrs, environ_base={'REMOTE_ADDR': '10.0.0.1'})
    (cookie,) = [c for c in r.headers.getlist('Set-Cookie') if c.startswith('luma_auth_session=')]
    assert 'Secure' not in cookie

    app.main.settings.update({'security': {'trusted_proxies': '10.0.0.1'}})
    c2 = test_app.test_client()
    r = _form_login(c2, headers=hdrs, environ_base={'REMOTE_ADDR': '10.0.0.1'})
    (cookie,) = [c for c in r.headers.getlist('Set-Cookie') if c.startswith('luma_auth_session=')]
    assert 'Secure' in cookie
    # Origin must now match the forwarded scheme://host.
    token = user_auth.create_session(user_auth.get_user_by_name('owner')['id'])
    c2.set_cookie('luma_auth_session', token)
    ok = c2.post('/api/settings', json={}, headers={**hdrs, 'Origin': 'https://luma.example.com'},
                 environ_base={'REMOTE_ADDR': '10.0.0.1'})
    assert not (ok.status_code == 403 and 'CSRF' in (ok.get_json() or {}).get('error', ''))
    bad = c2.post('/api/settings', json={}, headers={**hdrs, 'Origin': 'http://luma.example.com'},
                  environ_base={'REMOTE_ADDR': '10.0.0.1'})
    assert bad.status_code == 403


def test_spoofed_forwarded_host_from_lan_client_ignored(client):
    """A LAN client can't claim another host to pass the origin check."""
    r = client.post('/api/settings', json={},
                    headers={'X-Forwarded-Host': 'evil.com', 'Origin': 'http://evil.com'},
                    environ_base=LAN)
    assert r.status_code == 403
