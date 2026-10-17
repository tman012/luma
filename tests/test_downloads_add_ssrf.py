"""Prod hotfix: /api/downloads/add is owner-only and its pasted-link fetch
goes through app.outbound_guard (public addresses only, pinned IP, every
redirect hop re-checked, hop limit, http/https only)."""

import socket

import pytest

import app.main
import app.user_auth as user_auth
from app import outbound_guard as og

LAN = {'REMOTE_ADDR': '192.168.1.20'}
REMOTE = {'REMOTE_ADDR': '198.51.100.7'}
ORIGIN = {'Origin': 'http://localhost'}
PUBLIC = '93.184.216.34'
TORRENT = b'd4:infod6:lengthi1e4:name4:testee'

_real_getaddrinfo = socket.getaddrinfo


def _ai(ip, port):
    fam = socket.AF_INET6 if ':' in ip else socket.AF_INET
    return (fam, socket.SOCK_STREAM, 6, '', (ip, port))


class Net:
    """Fake DNS + fake upstream. dns maps host -> list of answers (one list
    per lookup, the last repeats); routes maps (ip, host, target) -> reply."""

    def __init__(self, monkeypatch):
        self.dns, self.lookups, self.opens, self.routes = {}, [], [], {}

        def getaddrinfo(host, port, *a, **k):
            self.lookups.append(host)
            if host in self.dns:
                seq = self.dns[host]
                n = sum(1 for h in self.lookups if h == host) - 1
                return [_ai(ip, port) for ip in seq[min(n, len(seq) - 1)]]
            return _real_getaddrinfo(host, port, *a, flags=socket.AI_NUMERICHOST,
                                     type=socket.SOCK_STREAM)

        def _open(scheme, ip, port, host, target, headers, timeout, max_bytes):
            self.opens.append((scheme, ip, port, host, target, headers['Host']))
            return self.routes.get((ip, host, target), (404, {}, b''))

        monkeypatch.setattr(og.socket, 'getaddrinfo', getaddrinfo)
        monkeypatch.setattr(og, '_open', _open)


@pytest.fixture
def net(monkeypatch):
    return Net(monkeypatch)


class _QResp:
    def __init__(self, status=200, text='Ok.', js=None):
        self.status_code, self.text, self._js = status, text, js

    def json(self):
        return self._js


class FakeQbit:
    def __init__(self):
        self.torrents, self.added, self.logins = [], [], 0

    def post(self, url, data=None, files=None, timeout=None):
        if url.endswith('/auth/login'):
            self.logins += 1
            return _QResp()
        if url.endswith('/torrents/add'):
            self.added.append({'data': data, 'files': files})
            if files:
                h = app.main._torrent_info_hash(files['torrents'][1])
                self.torrents.append({'hash': h, 'added_on': 1})
            else:
                h = app.main._magnet_info_hash(data['urls'])
                self.torrents.append({'hash': h, 'added_on': 1})
            return _QResp()
        return _QResp(404, '')

    def get(self, url, params=None, timeout=None, **k):
        if url.endswith('/torrents/info'):
            return _QResp(js=list(self.torrents))
        raise AssertionError(f'unexpected qBit/session GET {url}')


@pytest.fixture
def qbit(test_app, monkeypatch):
    q = FakeQbit()
    import sys
    import types
    import requests
    # auto_rename creates its container-only folders at import; not needed here
    fake = types.ModuleType('app.auto_rename')
    fake.auto_renamer = types.SimpleNamespace(track_download=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, 'app.auto_rename', fake)
    monkeypatch.setattr(requests, 'Session', lambda: q)
    monkeypatch.setattr(app.main, '_record_origin', lambda *a, **k: None)
    import time
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    app.main.settings.update({'qbittorrent': {
        'url': 'http://qbit.test', 'username': 'u', 'password': 'p'}})
    return q


@pytest.fixture
def client(test_app):
    return test_app.test_client()


def _owner_client(client):
    user_auth.create_user('owner', '1234', is_admin=True)
    tok = user_auth.create_session(user_auth.get_user_by_name('owner')['id'])
    client.set_cookie('luma_auth_session', tok)
    return client


def _post(client, url, env=LAN):
    return client.post('/api/downloads/add', json={'torrent_url': url},
                       headers=ORIGIN, environ_base=env)


def _require_auth_everywhere():
    app.main.settings.update({'security': {'auth_required': 'enabled'}})


# ── Roles ────────────────────────────────────────────────────────────────

def test_owner_can_add_public_torrent(client, net, qbit):
    _require_auth_everywhere()
    _owner_client(client)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/dl/1?passkey=x')] = (
        200, {'Content-Type': 'application/x-bittorrent'}, TORRENT)
    r = _post(client, 'https://tracker.example/dl/1?passkey=x', env=REMOTE)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['success'] is True
    assert qbit.added and qbit.added[0]['files']['torrents'][1] == TORRENT
    assert net.opens == [('https', PUBLIC, 443, 'tracker.example',
                          '/dl/1?passkey=x', 'tracker.example')]


def test_owner_magnet_goes_straight_to_qbit(client, net, qbit):
    _require_auth_everywhere()
    _owner_client(client)
    r = _post(client, 'magnet:?xt=urn:btih:' + 'a' * 40, env=REMOTE)
    assert qbit.added and qbit.added[0]['data']['urls'].startswith('magnet:')
    assert net.opens == [] and net.lookups == []
    assert r.status_code == 200, r.get_json()


@pytest.mark.parametrize('mode,env', [('enabled', LAN), ('enabled', REMOTE),
                                      ('disabled_local', REMOTE)])
def test_anonymous_gets_401_and_nothing_is_fetched(client, net, qbit, mode, env):
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'security': {'auth_required': mode}})
    r = _post(client, 'http://169.254.169.254/latest/meta-data/', env=env)
    assert r.status_code == 401
    assert net.opens == [] and net.lookups == [] and qbit.logins == 0


def test_approved_user_gets_403(client, net, qbit):
    _require_auth_everywhere()
    user_auth.create_user('owner', '1234', is_admin=True)
    u = user_auth.create_user('friend', '5678', is_admin=False)
    user_auth.save_user_settings(u['id'], {'use_shared_server': True,
                                           'plex_access_status': 'approved'})
    client.set_cookie('luma_auth_session', user_auth.create_session(u['id']))
    net.dns['tracker.example'] = [[PUBLIC]]
    r = _post(client, 'https://tracker.example/dl/1', env=REMOTE)
    assert r.status_code == 403
    assert net.opens == [] and net.lookups == [] and qbit.logins == 0


# ── Address checks ───────────────────────────────────────────────────────

@pytest.mark.parametrize('url', [
    'http://169.254.169.254/latest/meta-data/',
    'http://[::ffff:169.254.169.254]/latest/meta-data/',
    'http://[fd00:ec2::254]/latest/meta-data/',
    'http://metadata.example/computeMetadata/v1/',
    'http://127.0.0.1:5001/api/settings',
    'http://localhost.example/',
    'http://192.168.1.62:5001/',
    'http://10.0.0.1/',
    'http://172.16.0.1/',
    'http://100.64.0.1/',
    'http://0.0.0.0/',
    'http://[::1]/',
    'http://[fe80::1]/',
    'http://[fc00::1]/',
    'http://[64:ff9b::a9fe:a9fe]/',
    'http://[2002:a9fe:a9fe::1]/',
    'http://240.0.0.1/',
    'http://224.0.0.1/',
    'http://198.18.0.1/',
])
def test_blocked_addresses_refused(client, net, qbit, url):
    _owner_client(client)
    net.dns['metadata.example'] = [['169.254.169.254']]
    net.dns['localhost.example'] = [['127.0.0.1']]
    r = _post(client, url)
    assert r.status_code == 400
    assert r.get_json() == {'error': 'Link not allowed'}
    assert net.opens == [] and qbit.added == []


@pytest.mark.parametrize('url', [
    'file:///etc/passwd', 'ftp://tracker.example/x.torrent', 'gopher://tracker.example/',
    'dict://tracker.example:11211/', 'https://user:pw@tracker.example/x',
    'https:///nohost', 'http://tracker.example:99999/', '//tracker.example/x',
])
def test_bad_schemes_and_shapes_refused(client, net, qbit, url):
    _owner_client(client)
    net.dns['tracker.example'] = [[PUBLIC]]
    r = _post(client, url)
    assert r.status_code == 400
    assert net.opens == [] and qbit.added == []


def test_mixed_dns_answer_with_private_address_refused(client, net, qbit):
    _owner_client(client)
    net.dns['tracker.example'] = [[PUBLIC, '10.0.0.5']]
    r = _post(client, 'https://tracker.example/dl/1')
    assert r.status_code == 400 and net.opens == []


# ── Redirects ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('location', [
    'http://192.168.1.62:5001/api/settings',
    'http://169.254.169.254/latest/meta-data/iam/',
    'http://internal.example/',
    'file:///etc/passwd',
])
def test_redirect_to_private_or_bad_scheme_blocked(client, net, qbit, location):
    _owner_client(client)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.dns['internal.example'] = [['10.1.2.3']]
    net.routes[(PUBLIC, 'tracker.example', '/dl/1')] = (302, {'Location': location}, b'')
    r = _post(client, 'https://tracker.example/dl/1')
    assert r.status_code == 400
    assert len(net.opens) == 1 and qbit.added == []


def test_public_redirect_chain_followed_and_each_hop_pinned(client, net, qbit):
    _owner_client(client)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.dns['cdn.example'] = [['151.101.1.1']]
    net.routes[(PUBLIC, 'tracker.example', '/dl/1')] = (
        301, {'Location': 'https://cdn.example:8443/f/1.torrent'}, b'')
    net.routes[('151.101.1.1', 'cdn.example', '/f/1.torrent')] = (200, {}, TORRENT)
    r = _post(client, 'https://tracker.example/dl/1')
    assert r.status_code == 200, r.get_json()
    assert [o[1] for o in net.opens] == [PUBLIC, '151.101.1.1']
    assert net.opens[1][5] == 'cdn.example:8443'


def test_redirect_to_magnet_handed_to_qbit(client, net, qbit):
    _owner_client(client)
    net.dns['tracker.example'] = [[PUBLIC]]
    mag = 'magnet:?xt=urn:btih:' + 'b' * 40
    net.routes[(PUBLIC, 'tracker.example', '/dl/1')] = (302, {'Location': mag}, b'')
    r = _post(client, 'https://tracker.example/dl/1')
    assert r.status_code == 200, r.get_json()
    assert qbit.added and qbit.added[0]['data']['urls'] == mag


@pytest.mark.parametrize('mag', [
    'magnet:?xt=urn:btih:' + 'c' * 40 + '\nhttp://169.254.169.254/',
    'magnet:?xt=urn:btih:' + 'c' * 40 + '\r\nhttp://10.0.0.1/',
])
def test_multiline_magnet_refused(client, net, qbit, mag):
    _owner_client(client)
    r = _post(client, mag)
    assert r.status_code == 400 and qbit.added == []


def test_redirect_loop_hits_hop_limit(client, net, qbit):
    _owner_client(client)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/loop')] = (302, {'Location': '/loop'}, b'')
    r = _post(client, 'https://tracker.example/loop')
    assert r.status_code == 400
    assert len(net.opens) == og.MAX_HOPS + 1 and qbit.added == []


# ── DNS rebinding ────────────────────────────────────────────────────────

def test_dns_rebinding_cannot_swap_address_after_check(net):
    # First answer is public, every later answer is loopback: the request must
    # go to the address that was checked, with one lookup per hop.
    net.dns['rebind.example'] = [[PUBLIC], ['127.0.0.1']]
    net.routes[(PUBLIC, 'rebind.example', '/t')] = (200, {}, TORRENT)
    resp = og.fetch('https://rebind.example/t')
    assert resp.content == TORRENT
    assert net.lookups == ['rebind.example']
    assert net.opens[0][1] == PUBLIC


def test_dns_rebinding_on_redirect_hop_rechecked(net):
    net.dns['rebind.example'] = [[PUBLIC], ['127.0.0.1']]
    net.routes[(PUBLIC, 'rebind.example', '/t')] = (302, {'Location': '/u'}, b'')
    with pytest.raises(og.BlockedURL):
        og.fetch('https://rebind.example/t')
    assert len(net.opens) == 1


class _Pool:
    made = []

    def __init__(self, host, **kw):
        _Pool.made.append((host, kw))

    def urlopen(self, method, target, **kw):
        self.kw = kw

        class R:
            status = 200
            headers = {}
            _chunks = [TORRENT, b'']

            def read(self, n):
                return self._chunks.pop(0)

            def release_conn(self):
                pass
        assert kw['redirect'] is False and kw['retries'] is False
        return R()

    def close(self):
        pass


def test_connection_is_made_to_pinned_ip_with_tls_name(monkeypatch):
    _Pool.made = []
    monkeypatch.setattr(og.socket, 'getaddrinfo',
                        lambda h, p, *a, **k: [_ai(PUBLIC, p)])
    monkeypatch.setattr(og.urllib3, 'HTTPSConnectionPool', _Pool)
    monkeypatch.setattr(og.urllib3, 'HTTPConnectionPool', _Pool)
    assert og.fetch('https://tracker.example/x').content == TORRENT
    host, kw = _Pool.made[0]
    assert host == PUBLIC
    assert kw['server_hostname'] == 'tracker.example'
    assert kw['assert_hostname'] == 'tracker.example'
    assert kw['cert_reqs'] == 'CERT_REQUIRED'
    og.fetch('http://tracker.example:8080/x')
    assert _Pool.made[1][0] == PUBLIC and _Pool.made[1][1]['port'] == 8080


def test_oversized_body_refused(monkeypatch):
    class Big(_Pool):
        def urlopen(self, method, target, **kw):
            class R:
                status, headers = 200, {}

                def read(self, n):
                    return b'x' * n

                def release_conn(self):
                    pass
            return R()
    monkeypatch.setattr(og.socket, 'getaddrinfo',
                        lambda h, p, *a, **k: [_ai(PUBLIC, p)])
    monkeypatch.setattr(og.urllib3, 'HTTPSConnectionPool', Big)
    with pytest.raises(og.FetchError):
        og.fetch('https://tracker.example/x', max_bytes=1000)


@pytest.mark.parametrize('ip,blocked', [
    ('8.8.8.8', False), ('2606:4700:4700::1111', False), (PUBLIC, False),
    ('169.254.169.254', True), ('127.0.0.1', True), ('10.0.0.1', True),
    ('100.64.0.1', True), ('100.127.255.255', True), ('192.168.0.1', True),
    ('172.31.255.255', True), ('0.0.0.0', True), ('255.255.255.255', True),
    ('::', True), ('::1', True), ('fe80::1', True), ('fd12:3456::1', True),
    ('::ffff:127.0.0.1', True), ('::ffff:8.8.8.8', False), ('::127.0.0.1', True),
    ('64:ff9b::7f00:1', True), ('2002:7f00:1::', True), ('ff02::1', True),
    ('203.0.113.5', True), ('fe80::1%eth0', True), ('not-an-ip', True),
])
def test_is_blocked_ip(ip, blocked):
    assert og.is_blocked_ip(ip) is blocked


def test_endpoint_has_no_direct_url_fetch():
    import inspect
    src = inspect.getsource(app.main.add_torrent_to_qbittorrent)
    assert 'allow_redirects=True' not in src
    assert 'session.get(torrent_url' not in src
    assert '_fetch_pasted_torrent(torrent_url)' in src
