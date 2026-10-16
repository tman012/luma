"""Round 6: forwarded host via trusted proxies, proxy range limits, LAN host record."""

import json
import logging
from types import SimpleNamespace

import pytest

import app.main
import app.user_auth as user_auth
from app import access, lan_hosts

PROXY = {'REMOTE_ADDR': '10.0.0.1'}
LAN_XFF = {'X-Forwarded-For': '192.168.1.30'}


@pytest.fixture
def client(test_app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.main, '_LAN_HOSTS_DIR', tmp_path)
    lan_hosts._last_write.clear()
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'security': {'trusted_proxies': '10.0.0.1'}})
    return test_app.test_client()


def _me(client, env=PROXY, **headers):
    return client.get('/api/auth/me', headers=headers, environ_base=env)


# --- item 1 -----------------------------------------------------------------

@pytest.mark.parametrize('host,xfh', [
    ('docker-server', 'evil.com'),          # live case 1: unknown XFH, no Host fallback
    ('evil.com', 'docker-server'),          # live case 2: rebinding page set XFH
    ('docker-server', 'luma.local'),        # both known but they disagree
    ('docker-server', 'evil.com, docker-server'),   # only the first XFH value counts
])
def test_untrusted_forwarded_host_gets_sign_in(client, host, xfh):
    r = _me(client, Host=host, **{'X-Forwarded-Host': xfh, **LAN_XFF})
    assert r.status_code == 401


def test_proxy_with_matching_known_xfh_allowed(client):
    r = _me(client, Host='docker-server:5001', **{'X-Forwarded-Host': 'docker-server:5001', **LAN_XFF})
    assert r.status_code == 200 and r.get_json()['implicit'] is True


def test_proxy_without_xfh_uses_host(client):
    assert _me(client, Host='docker-server', **LAN_XFF).status_code == 200
    assert _me(client, Host='evil.com', **LAN_XFF).status_code == 401


def test_untrusted_peer_xfh_ignored(client):
    r = _me(client, env={'REMOTE_ADDR': '192.168.1.20'}, Host='docker-server', **{'X-Forwarded-Host': 'evil.com'})
    assert r.status_code == 200   # XFH from a non-proxy is ignored; Host decides


def test_flag_checked_by_should_authenticate():
    req = SimpleNamespace(remote_addr='192.168.1.20', headers={}, host='docker-server',
                          environ={access.HOST_UNTRUSTED: True})
    assert access.should_authenticate(SimpleNamespace(get=lambda k, d=None: d), req) is True


# --- item 2 -----------------------------------------------------------------

@pytest.mark.parametrize('value', ['0.0.0.0/0', '::/0', '10.0.0.0/7', '2001::/16', 'nope'])
def test_broad_or_bad_ranges_rejected_on_save(client, value):
    r = client.put('/api/security', json={'trusted_proxies': value}, environ_base={'REMOTE_ADDR': '192.168.1.20'})
    assert r.status_code == 400
    assert r.get_json()['error']


@pytest.mark.parametrize('value', ['10.0.0.0/8', '172.20.0.0/16', '2001:db8::/32', '192.168.1.5'])
def test_reasonable_ranges_saved(client, value):
    r = client.put('/api/security', json={'trusted_proxies': value}, environ_base={'REMOTE_ADDR': '192.168.1.20'})
    assert r.status_code == 200


def test_broad_ranges_ignored_on_parse_and_logged(caplog):
    access._warned.clear()
    s = SimpleNamespace(get=lambda k, d=None: '0.0.0.0/0, ::/0, 172.20.0.0/16, 203.0.113.0/24'
                        if k == 'security.trusted_proxies' else d)
    with caplog.at_level(logging.WARNING, logger='app.access'):
        nets = access.trusted_proxies(s)
    assert [str(n) for n in nets] == ['172.20.0.0/16', '203.0.113.0/24']
    assert 'ignoring trusted proxy 0.0.0.0/0' in caplog.text
    assert 'public address range' in caplog.text


def test_all_hops_trusted_gives_no_client():
    s = SimpleNamespace(get=lambda k, d=None: '172.20.0.0/16' if k == 'security.trusted_proxies' else d)
    req = SimpleNamespace(remote_addr='172.20.0.2', headers={'X-Forwarded-For': '172.20.0.5, 172.20.0.3'})
    assert access.client_ip(req, s) is None


# --- item 3 -----------------------------------------------------------------

LAN = {'REMOTE_ADDR': '192.168.1.20'}


def test_only_implicit_requests_with_home_names_recorded(client, tmp_path):
    for host in ('evil.com', 'localhost.evil.com', 'localhost', '192.168.1.62'):
        _me(client, env=LAN, Host=host)
    _me(client, env={'REMOTE_ADDR': '203.0.113.9'}, Host='remote.lan')   # remote: no implicit
    _me(client, Host='proxied.lan', **{'X-Forwarded-For': '203.0.113.9'})  # proxied public client
    assert lan_hosts.load(tmp_path) == {}
    _me(client, env=LAN, Host='docker-server:5001')
    assert set(lan_hosts.load(tmp_path)) == {'docker-server'}


def test_junk_cannot_evict(client, tmp_path):
    _me(client, env=LAN, Host='docker-server')
    for i in range(300):
        _me(client, env=LAN, Host=f'junk{i}.evil.com')
    assert set(lan_hosts.load(tmp_path)) == {'docker-server'}
    assert len(lan_hosts._last_write) <= 4 * lan_hosts.MAX_NAMES


def test_exposed_names_are_seedable_only(client, tmp_path):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    (tmp_path / lan_hosts.FILENAME).write_text(json.dumps({'names': {'evil.com': now, 'tv.lan': now}}))
    got = client.get('/api/security', environ_base=LAN).get_json()
    assert got['seen_hostnames'] == ['tv.lan']


def test_last_write_capped(tmp_path):
    lan_hosts._last_write.clear()
    for i in range(10 * lan_hosts.MAX_NAMES):
        lan_hosts.record(f'h{i}.lan', tmp_path)
    assert len(lan_hosts._last_write) <= 4 * lan_hosts.MAX_NAMES + 1


# --- item 5 -----------------------------------------------------------------

def test_one_suffix_list():
    assert not hasattr(lan_hosts, 'PRIVATE_SUFFIXES')
    for name in ('a.local', 'a.lan', 'a.home.arpa', 'a.internal', 'a.localdomain', 'a.home', 'tv'):
        assert lan_hosts.is_seedable(name) and access.is_home_name(name)
    for name in ('a.corp', 'a.private', 'a.intranet', 'a.example.com'):
        assert not lan_hosts.is_seedable(name) and not access.is_home_name(name)
