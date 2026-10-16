"""Host comparisons ignore case, a trailing dot, a port and IPv6 brackets."""

from types import SimpleNamespace

import pytest

import app.main
import app.user_auth as user_auth
from app import access, dev_guard

PORTAL = 'requests.example.com'


def _req(host):
    return SimpleNamespace(headers={'Host': host})


@pytest.mark.parametrize('host', [
    'requests.example.com', 'requests.example.com.', 'REQUESTS.Example.COM',
    'Requests.Example.Com.', 'requests.example.com:443', 'requests.example.com.:8443',
])
def test_portal_host_variants_match(host):
    assert access.is_portal_traffic(_req(host), {'request_portal.hostname': PORTAL})


@pytest.mark.parametrize('configured', ['Requests.Example.com.', ' requests.example.com:443 '])
def test_configured_portal_name_is_normalized(configured):
    assert access.is_portal_traffic(_req('requests.example.com'),
                                    {'request_portal.hostname': configured})


@pytest.mark.parametrize('host', ['example.com', 'requests.example.com.evil.com', '', 'requests'])
def test_other_hosts_are_not_portal(host):
    assert not access.is_portal_traffic(_req(host), {'request_portal.hostname': PORTAL})


@pytest.mark.parametrize('host', ['[FD00::5]', '[fd00::5]:8443', 'fd00::5'])
def test_ipv6_portal_host(host):
    assert access.is_portal_traffic(_req(host), {'request_portal.hostname': '[fd00::5]'})


def test_no_portal_configured():
    assert not access.is_portal_traffic(_req(''), {'request_portal.hostname': ''})


@pytest.mark.parametrize('host', ['requests.example.com.', 'REQUESTS.EXAMPLE.COM.',
                                  'requests.example.com:5001'])
@pytest.mark.parametrize('path', ['/api/auth/me', '/api/automation/features', '/api/canon', '/'])
def test_portal_host_variants_cannot_reach_full_app(test_app, host, path):
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'request_portal': {'hostname': PORTAL, 'enabled': True}})
    client = test_app.test_client()
    with client.session_transaction() as s:
        s['requester'] = {'name': 'guest', 'sid': 'abc'}
    r = client.get(path, headers={'Host': host}, environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert r.status_code == 404


def test_dev_guard_ignores_trailing_dot_and_case():
    targets = dev_guard._parse_targets('http://prod.example:8080/api')
    assert dev_guard._matches('http://PROD.example.:8080/api/x', targets)
    assert not dev_guard._matches('http://other.example:8080/api/x', targets)


def test_multi_value_forwarded_host_is_untrusted(test_app):
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'security': {'trusted_proxies': '10.0.0.1'}})
    c = test_app.test_client()
    env = {'REMOTE_ADDR': '10.0.0.1'}
    lan = {'X-Forwarded-For': '192.168.1.30'}
    ok = c.get('/api/auth/me', environ_base=env,
               headers={'Host': 'docker-server', 'X-Forwarded-Host': 'docker-server', **lan})
    assert ok.status_code == 200
    for xfh in ('docker-server, evil.com', 'docker-server,docker-server'):
        r = c.get('/api/auth/me', environ_base=env,
                  headers={'Host': 'docker-server', 'X-Forwarded-Host': xfh, **lan})
        assert r.status_code == 401, xfh
