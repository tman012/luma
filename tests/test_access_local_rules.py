"""Who counts as local (round 3, item 1): LAN peers, or XFF from trusted proxies only."""

from types import SimpleNamespace

import pytest

from app import access


class _S:
    def __init__(self, **kv):
        self.kv = {f'security.{k}': v for k, v in kv.items()}

    def get(self, key, default=None):
        return self.kv.get(key, default)


def _req(peer, **headers):
    return SimpleNamespace(remote_addr=peer, headers={k.replace('_', '-'): v for k, v in headers.items()})


@pytest.mark.parametrize('peer,headers,proxies,local', [
    ('192.168.1.20', {}, '', True),                                    # direct LAN
    ('203.0.113.9', {}, '', False),                                    # direct WAN
    ('203.0.113.9', {'CF_Connecting_IP': '192.168.1.20'}, '', False),  # spoofed CF header
    ('172.21.0.1', {'CF_Connecting_IP': '192.168.1.20'}, '', False),   # tunnel connector on LAN
    ('172.21.0.1', {'CF_Connecting_IP': '192.168.1.20'}, '172.21.0.0/16', False),  # CF header never trusted
    ('192.168.1.20', {'X_Forwarded_For': '192.168.1.30'}, '', False),  # untrusted relay
    ('172.20.0.2', {'X_Forwarded_For': '192.168.1.30'}, '172.20.0.0/16', True),
    ('172.20.0.2', {'X_Forwarded_For': '192.168.1.30, 198.51.100.7'}, '172.20.0.0/16', False),
    ('172.20.0.2', {'X_Forwarded_For': '198.51.100.7, 172.20.0.3'}, '172.20.0.0/16', False),
    ('172.20.0.2', {}, '172.20.0.0/16', False),                        # proxy gave no client
    ('172.20.0.2', {'X_Real_IP': '192.168.1.30'}, '172.20.0.0/16', False),  # X-Real-IP not used
])
def test_is_local(peer, headers, proxies, local):
    assert access.is_local_request(_req(peer, **headers), _S(trusted_proxies=proxies)) is local


def test_spoofed_cf_connecting_ip_stays_remote_in_app(test_app):
    import app.main
    import app.user_auth as user_auth
    user_auth.create_user('owner', '1234', is_admin=True)
    c = test_app.test_client()
    r = c.get('/api/auth/me', headers={'CF-Connecting-IP': '192.168.1.20'},
              environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert r.status_code == 401
    r = c.get('/api/auth/me', headers={'CF-Connecting-IP': '192.168.1.20'},
              environ_base={'REMOTE_ADDR': '172.21.0.1'})
    assert r.status_code == 401


def test_trusted_proxy_header_off_by_default():
    s = _S(trusted_proxies='172.20.0.0/16')
    r = _req('172.20.0.2', X_Client='192.168.1.30', X_Forwarded_For='198.51.100.7')
    assert access.client_ip(r, s) == '198.51.100.7'


def test_trusted_proxy_header_when_set():
    s = _S(trusted_proxies='172.20.0.0/16', trusted_proxy_header='X-Client')
    assert access.client_ip(_req('172.20.0.2', X_Client='192.168.1.30'), s) == '192.168.1.30'
    assert access.client_ip(_req('172.20.0.2'), s) is None
    # From a peer that isn't a trusted proxy the header is ignored (and marks nothing local).
    assert access.client_ip(_req('203.0.113.9', X_Client='192.168.1.30'), s) == '203.0.113.9'


def test_no_home_wan_logic_left():
    for name in ('_home_lock', '_home_ips', 'init_home_wan_tracking', 'mode', 'needs_pin'):
        assert not hasattr(access, name), name
    src = open(access.__file__).read()
    assert '1.1.1.1' not in src


@pytest.mark.parametrize('auth_required,peer,expected', [
    ('disabled_local', '192.168.1.20', False),
    ('disabled_local', '203.0.113.9', True),
    ('enabled', '192.168.1.20', True),
    (None, '192.168.1.20', False),   # nothing saved: disabled_local
    (None, '203.0.113.9', True),
    ('open', '192.168.1.20', True),  # legacy/unknown values fail closed
])
def test_should_authenticate(auth_required, peer, expected):
    s = _S(auth_required=auth_required) if auth_required else _S()
    assert access.should_authenticate(s, _req(peer, Host='luma.lan')) is expected


def test_portal_hostname_never_implicit():
    s = _S()
    s.kv['request_portal.hostname'] = 'requests.example.com'
    assert access.should_authenticate(s, _req('192.168.1.20', Host='requests.example.com')) is True
