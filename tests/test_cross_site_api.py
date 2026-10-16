"""Cross-site reads, forwarded host and DNS rebinding (round 4, item 1)."""

from types import SimpleNamespace

import pytest

import app.main
import app.user_auth as user_auth
from app import access

LAN = {'REMOTE_ADDR': '192.168.1.20'}
PROXY = {'REMOTE_ADDR': '10.0.0.1'}


@pytest.fixture
def client(test_app):
    user_auth.create_user('owner', '1234', is_admin=True)
    return test_app.test_client()


def test_flask_cors_removed():
    assert 'flask_cors' not in open(app.main.__file__).read()
    reqs = open(app.main.__file__.replace('app/main.py', 'requirements.txt')).read().lower()
    assert 'flask-cors' not in reqs


def test_foreign_origin_read_refused_and_never_reflected(client):
    r = client.get('/api/settings', headers={'Origin': 'http://evil.com'}, environ_base=LAN)
    assert r.status_code == 403
    assert 'Access-Control-Allow-Origin' not in r.headers
    r = client.get('/api/auth/me', headers={'Origin': 'http://evil.com'}, environ_base=LAN)
    assert r.status_code == 403
    assert 'Access-Control-Allow-Origin' not in r.headers


def test_preflight_grants_nothing(client):
    r = client.options('/api/security', headers={
        'Origin': 'http://evil.com', 'Access-Control-Request-Method': 'PUT',
        'Access-Control-Request-Headers': 'X-Forwarded-Host, Content-Type'}, environ_base=LAN)
    for h in ('Access-Control-Allow-Origin', 'Access-Control-Allow-Headers',
              'Access-Control-Allow-Methods', 'Access-Control-Allow-Credentials'):
        assert h not in r.headers


@pytest.mark.parametrize('site', ['cross-site', 'same-site'])
def test_cross_site_fetch_refused_for_implicit_user(client, site):
    r = client.get('/api/settings', headers={'Sec-Fetch-Site': site}, environ_base=LAN)
    assert r.status_code == 403


@pytest.mark.parametrize('site', ['same-origin', 'none', None])
def test_same_origin_or_direct_fetch_allowed(client, site):
    headers = {'Sec-Fetch-Site': site} if site else {}
    r = client.get('/api/settings', headers=headers, environ_base=LAN)
    assert r.status_code == 200


def test_api_key_unaffected_by_fetch_metadata(client):
    r = client.get('/api/settings', headers={'X-API-Key': app.main._SERVICE_API_KEY,
                                             'Sec-Fetch-Site': 'cross-site'},
                   environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert r.status_code == 200


def test_forwarded_host_not_honored_unless_listed(client):
    app.main.settings.update({'security': {'trusted_proxies': '10.0.0.1', 'hostnames': ''}})
    token = user_auth.create_session(user_auth.get_user_by_name('owner')['id'])
    client.set_cookie('luma_auth_session', token)
    hdrs = {'X-Forwarded-Host': 'evil.com', 'X-Forwarded-For': '192.168.1.20',
            'Origin': 'http://evil.com'}
    r = client.put('/api/security', json={'auth_required': 'disabled_local'},
                   headers=hdrs, environ_base=PROXY)
    assert r.status_code == 403

    app.main.settings.update({'security': {'hostnames': 'luma.example.com'}})
    hdrs = {'X-Forwarded-Host': 'luma.example.com', 'X-Forwarded-For': '192.168.1.20',
            'Origin': 'http://luma.example.com'}
    r = client.put('/api/security', json={'auth_required': 'disabled_local'},
                   headers=hdrs, environ_base=PROXY)
    assert r.status_code == 200


def test_forwarded_host_ignored_from_untrusted_peer(client):
    app.main.settings.update({'security': {'trusted_proxies': '', 'hostnames': 'luma.example.com'}})
    r = client.get('/api/auth/me', headers={'X-Forwarded-Host': 'luma.example.com',
                                            'Origin': 'http://luma.example.com'}, environ_base=LAN)
    assert r.status_code == 403  # Host stays localhost, so that Origin is foreign


@pytest.mark.parametrize('host,implicit', [
    ('192.168.1.62:5001', True),
    ('[fe80::1]:5001', True),
    ('localhost:5001', True),
    ('luma.lan', True),           # listed in security.hostnames
    ('LUMA.LAN.:5001', True),
    ('rebind.evil.com', False),   # DNS rebinding to a LAN address
    ('luma.lan.evil.com', False),
])
def test_implicit_owner_needs_known_host(client, host, implicit):
    app.main.settings.update({'security': {'hostnames': 'luma.lan'}})
    r = client.get('/api/auth/me', headers={'Host': host}, environ_base=LAN)
    assert (r.status_code == 200) is implicit
    if implicit:
        assert r.get_json()['implicit'] is True


def test_unknown_host_can_still_sign_in(client):
    app.main.settings.update({'security': {'hostnames': ''}})
    r = client.post('/api/auth/login', json={'name': 'owner', 'pin': '1234'},
                    headers={'Host': 'kiosk.home'}, environ_base=LAN)
    assert r.status_code == 200


@pytest.mark.parametrize('value,name', [
    ('Luma.Lan:5001', 'luma.lan'), ('[::1]:80', '::1'), ('::1', '::1'),
    ('10.0.0.5', '10.0.0.5'), ('example.com.', 'example.com'), ('', ''),
])
def test_split_host(value, name):
    assert access.split_host(value) == name


def test_configured_hostnames_parsing():
    s = SimpleNamespace(get=lambda k, d=None: ' a.lan, B.lan;c.lan  d.lan:5001 ' if k == 'security.hostnames' else d)
    assert access.configured_hostnames(s) == {'a.lan', 'b.lan', 'c.lan', 'd.lan'}
    assert access.is_known_host('b.lan:443', s)
    assert not access.is_known_host('e.lan', s)
    assert access.is_known_host('127.0.0.1', None)
    assert not access.is_known_host('', None)
