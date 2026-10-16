"""Home-network host names on a default install (round 5, item 1 + PM constraint).

Implicit owner needs BOTH a local client address AND a known Host; the Host
header alone never grants access.
"""

import pytest

import app.main
import app.user_auth as user_auth
from app import access


@pytest.fixture
def client(test_app, monkeypatch):
    monkeypatch.setattr(access.socket, 'gethostname', lambda: 'mybox.example.net')
    user_auth.create_user('owner', '1234', is_admin=True)
    return test_app.test_client()


def _me(client, host, peer, **headers):
    return client.get('/api/auth/me', headers={'Host': host, **headers}, environ_base={'REMOTE_ADDR': peer})


@pytest.mark.parametrize('host', ['docker-server:5001', 'docker-server', 'luma.local', 'luma.lan:5001',
                                  'luma.home.arpa', 'box.internal', 'mybox.example.net', 'mybox:5001'])
def test_lan_ip_with_home_name_gets_implicit_owner(client, host):
    r = _me(client, host, '192.168.1.20')
    assert r.status_code == 200
    assert r.get_json()['implicit'] is True


@pytest.mark.parametrize('host', ['evil.com', 'luma.local.evil.com', 'docker-server.example.com'])
def test_lan_ip_with_public_name_gets_sign_in(client, host):
    assert _me(client, host, '192.168.1.20').status_code == 401


def test_public_peer_with_home_name_gets_sign_in(client):
    assert _me(client, 'docker-server', '203.0.113.9').status_code == 401
    assert client.get('/settings', headers={'Host': 'docker-server'},
                      environ_base={'REMOTE_ADDR': '203.0.113.9'}).status_code == 302


def test_trusted_proxy_with_public_client_gets_sign_in(client):
    app.main.settings.update({'security': {'trusted_proxies': '10.0.0.1'}})
    assert _me(client, 'luma.local', '10.0.0.1', **{'X-Forwarded-For': '203.0.113.9'}).status_code == 401
    # same proxy, LAN client: allowed
    assert _me(client, 'luma.local', '10.0.0.1', **{'X-Forwarded-For': '192.168.1.30'}).status_code == 200


def test_tunnel_request_with_home_name_gets_sign_in(client):
    # cloudflared on the LAN, not a trusted proxy
    assert _me(client, 'docker-server', '172.21.0.1', **{'CF-Connecting-IP': '192.168.1.30'}).status_code == 401
    # cloudflared listed as a trusted proxy and forwarding a private-looking XFF
    app.main.settings.update({'security': {'trusted_proxies': '172.21.0.0/16'}})
    for extra in ({'CF-Ray': '8a1b2c3d4e5f-SJC'}, {'CF-Connecting-IP': '192.168.1.30'},
                  {'CDN-Loop': 'cloudflare'}):
        r = _me(client, 'docker-server', '172.21.0.1', **{'X-Forwarded-For': '192.168.1.30', **extra})
        assert r.status_code == 401, extra


def test_is_home_name():
    for name in ('docker-server', 'luma.local', 'a.lan:80', 'x.home.arpa', 'y.internal'):
        assert access.is_home_name(name)
    for name in ('evil.com', '192.168.1.62', '[::1]', '', 'luma.local.evil.com'):
        assert not access.is_home_name(name)
