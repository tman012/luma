"""v172 (tracker port step 4, PM): no tracker or grab code can reach the LAN.

With the Prowlarr bridge gone, every tracker request (search, Test, sign-in, download) goes
through tracker_http (every hop resolved, checked and pinned), and every pasted or redirected
grab link through outbound_guard. Neither may reach RFC 1918 / unique-local, loopback,
link-local or cloud metadata, CGNAT, unspecified, multicast or this host's own addresses, also
not via a redirect or by DNS rebinding (a name that answers public at the check and private
later is never re-resolved for the connection: the checked address is the one connected to).

Fake DNS and a fake transport only: nothing here opens a socket (a socket connect fails the test).

The one documented exception is outside tracker and grab code: the owner-pressed Import from
Prowlarr (tracker_import, Settings > Trackers) reads the saved Prowlarr's indexer list once,
from the LAN by design; it refuses link-local/metadata, multicast, unspecified and reserved
addresses (tests/test_tracker_import_v170.py) and nothing in search or grab code calls it.
"""
import ast
import pathlib
import socket
from urllib.parse import urlsplit

import pytest
import requests
from requests.adapters import HTTPAdapter

import app.main
from app import outbound_guard as og
from app import tracker_clients, tracker_http, tracker_sources, tracker_types, tv_grab
from app.tracker_http import TrackerError
from tests.test_downloads_add_ssrf import _owner_client, _post, _require_auth_everywhere, client, net, qbit  # noqa: F401
from tests.test_tracker_sources import APIKEY, env  # noqa: F401

REAL_SESSION = requests.Session
APP = pathlib.Path(__file__).resolve().parents[1] / 'app'
PUBLIC = '93.184.216.34'
PUBLIC_CDN = '93.184.216.50'
OWN = '93.184.216.99'                       # stands in for this host's own public address

BLOCKED = {
    'rfc1918-10': '10.0.0.5',
    'rfc1918-172': '172.16.3.4',
    'rfc1918-192': '192.168.1.5',
    'loopback': '127.0.0.1',
    'loopback-other': '127.8.9.10',
    'loopback-v6': '::1',
    'metadata': '169.254.169.254',
    'metadata-v6': 'fd00:ec2::254',
    'link-local-v6': 'fe80::1',
    'unique-local': 'fc00::7',
    'cgnat': '100.64.0.1',
    'unspecified': '0.0.0.0',
    'unspecified-v6': '::',
    'multicast': '224.0.0.1',
    'v4-mapped-lan': '::ffff:192.168.1.5',
    'v4-mapped-loopback': '::ffff:127.0.0.1',
    'nat64-lan': '64:ff9b::a00:5',
    'this-host': OWN,
}
IDS = list(BLOCKED)


# ------------------------------------------------------------------ fakes

class Dns:
    """host -> list of answers, one list per lookup (the last repeats)."""

    def __init__(self):
        self.map, self.lookups = {}, []

    def __call__(self, host, port=None):
        h = host.strip('[]')
        self.lookups.append(h)
        if h in self.map:
            seq = self.map[h]
            if callable(seq):
                return list(seq())
            n = sum(1 for x in self.lookups if x == h) - 1
            return list(seq[min(n, len(seq) - 1)])
        lit = tracker_http.ip_literal(h)
        if lit is not None:
            return [lit]
        raise socket.gaierror('no such host')


class Wire(HTTPAdapter):
    """The fake transport under tracker_http's pinned sessions: records what would be sent."""

    def __init__(self):
        super().__init__()
        self.sent = []          # (connected address, Host header, path)
        self.replies = []

    def send(self, request, **kw):
        u = urlsplit(request.url)
        self.sent.append((u.hostname, request.headers.get('Host'), u.path))
        status, headers, body = self.replies.pop(0) if len(self.replies) > 1 else (self.replies or [(200, {}, b'ok')])[0]
        r = requests.Response()
        r.status_code, r.url, r.request = status, request.url, request
        r.headers.update(headers)
        r._content = body
        r.raw = None
        return r


@pytest.fixture
def wire(monkeypatch):
    dns = Dns()
    w = Wire()
    monkeypatch.setattr(requests, 'Session', REAL_SESSION)
    monkeypatch.setattr(tracker_http, 'resolve', dns)
    monkeypatch.setattr(tracker_sources, '_resolve', lambda host: _safe(dns, host))
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset({OWN}))
    monkeypatch.setattr(HTTPAdapter, 'send', lambda self, request, **kw: w.send(request, **kw))

    def no_socket(*a, **k):
        raise AssertionError('a real socket was opened')
    monkeypatch.setattr(socket.socket, 'connect', no_socket)
    monkeypatch.setattr(socket, 'create_connection', no_socket)
    w.dns = dns
    return w


def _safe(dns, host):
    try:
        return dns(host)
    except socket.gaierror:
        return []


def _iter_content_fix(monkeypatch):
    """requests.Response.iter_content on a body set by hand."""
    monkeypatch.setattr(requests.Response, 'iter_content',
                        lambda self, n=1, decode_unicode=False: iter([self._content] if self._content else []))
    monkeypatch.setattr(requests.Response, 'close', lambda self: None)


@pytest.fixture
def http(wire, monkeypatch):
    _iter_content_fix(monkeypatch)
    return wire


# ------------------------------------------------------------------ tracker_http: every request

@pytest.mark.parametrize('kind', IDS)
def test_a_tracker_name_resolving_to_a_blocked_address_is_never_sent(http, kind):
    http.dns.map['t.example'] = [[BLOCKED[kind]]]
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://t.example/api?t=search')
    assert e.value.kind == 'blocked'
    assert http.sent == []


@pytest.mark.parametrize('kind', IDS)
def test_a_blocked_literal_is_never_sent(http, kind):
    a = BLOCKED[kind]
    host = f'[{a}]' if ':' in a else a
    with pytest.raises(TrackerError):
        tracker_http.request('GET', f'http://{host}/api')
    assert http.sent == []


@pytest.mark.parametrize('url', ['http://localhost/api', 'http://tracker.localhost/api', 'http://0x7f000001/api',
                                 'http://2130706433/api', 'http://017700000001/api', 'http://127.1/api'])
def test_localhost_names_and_odd_loopback_spellings_are_never_sent(http, url):
    with pytest.raises(TrackerError):
        tracker_http.request('GET', url)
    assert http.sent == []


@pytest.mark.parametrize('kind', ['rfc1918-192', 'loopback', 'metadata', 'this-host'])
def test_one_blocked_answer_among_public_ones_refuses_the_name(http, kind):
    http.dns.map['t.example'] = [[PUBLIC, BLOCKED[kind]]]
    with pytest.raises(TrackerError):
        tracker_http.request('GET', 'https://t.example/api')
    assert http.sent == []


def test_dns_rebinding_connects_to_the_checked_address_only(http):
    """Public at the check, private a moment later: the connection is pinned to the checked address."""
    http.dns.map['t.example'] = [[PUBLIC], ['127.0.0.1']]
    r = tracker_http.request('GET', 'https://t.example/api?t=caps')
    assert r.status == 200
    assert http.sent == [(PUBLIC, 't.example', '/api')]
    assert http.dns.lookups.count('t.example') == 1               # never re-resolved for the connection


@pytest.mark.parametrize('kind', IDS)
def test_a_same_host_redirect_that_rebinds_to_a_blocked_address_is_refused(http, kind):
    http.dns.map['t.example'] = [[PUBLIC], [BLOCKED[kind]]]
    http.replies = [(302, {'Location': '/next'}, b''), (200, {}, b'secret')]
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://t.example/api')
    assert e.value.kind == 'blocked'
    assert http.sent == [(PUBLIC, 't.example', '/api')]           # the second hop never left


@pytest.mark.parametrize('kind', IDS)
def test_a_redirect_to_another_host_is_not_followed(http, kind):
    a = BLOCKED[kind]
    http.dns.map['t.example'] = [[PUBLIC]]
    http.dns.map['lan.example'] = [[a]]
    for loc in ('http://lan.example/x', f"http://{f'[{a}]' if ':' in a else a}/x"):
        http.sent.clear()
        http.replies = [(302, {'Location': loc}, b'')]
        r = tracker_http.request('GET', 'https://t.example/api')
        assert r.status == 302                                     # handed back, not followed
        assert http.sent == [(PUBLIC, 't.example', '/api')]


@pytest.mark.parametrize('type_id', sorted(tracker_types.TRACKER_TYPES))
def test_every_tracker_type_search_and_test_stay_off_the_lan(http, type_id):
    """Search and Test of every tracker type, its URL resolving to the LAN: nothing is sent."""
    http.dns.map['lan.example'] = [['192.168.1.30']]
    cfg = {'id': 1, 'type': type_id, 'name': 'x', 'url': 'https://lan.example', 'api_key': APIKEY, 'rss_key': APIKEY,
           'username': 'me', 'password': 'pw_secret_1', 'pid': 'pid_secret_1',
           'cookie': 'uid=1; pass=abcdefabcdefabcdefabcdefabcdef12', 'categories': [2000]}
    tracker_clients.clear_avistaz_tokens()
    for call in (lambda: tracker_clients.search(cfg, 'Heat', [2000], 10, request=tracker_http.request),
                 lambda: tracker_clients.test(cfg, request=tracker_http.request)):
        with pytest.raises(TrackerError):
            call()
    assert http.sent == []
    tracker_clients.clear_avistaz_tokens()


# ------------------------------------------------------------------ grabs: release tokens (fetch_release)

@pytest.fixture
def grab(env, http):  # noqa: F811
    """The real tracker_http under the Sources grab path (env's mocked tracker swapped out)."""
    http.dns.map['t.example'] = [[PUBLIC]]
    http.dns.map['cdn.example'] = [[PUBLIC_CDN]]
    rec, errors = env['store'].create({'type': 'torznab', 'name': 'Indexer', 'url': 'https://t.example/api',
                                       'api_key': APIKEY, 'categories': [2000], 'priority': 25})
    assert not errors, errors
    env['engine'].request = tracker_http.request
    env['rec'] = rec
    return env


def _token(env, url):
    return env['engine'].tokens.issue(env['rec']['id'], url)


@pytest.mark.parametrize('kind', IDS)
def test_a_release_link_on_a_blocked_address_is_never_fetched(grab, http, kind):
    http.dns.map['lan.example'] = [[BLOCKED[kind]]]
    with pytest.raises(tracker_sources.GrabError) as e:
        tracker_sources.fetch_release(_token(grab, 'https://lan.example/dl/1.torrent'))
    assert e.value.status == 400
    assert http.sent == []


@pytest.mark.parametrize('kind', IDS)
def test_a_tracker_redirect_to_a_blocked_address_is_never_followed(grab, http, kind):
    a = BLOCKED[kind]
    http.dns.map['lan.example'] = [[a]]
    for loc in ('https://lan.example/dl.torrent', f"http://{f'[{a}]' if ':' in a else a}/dl.torrent"):
        http.sent.clear()
        http.replies = [(302, {'Location': loc}, b'')]
        with pytest.raises(tracker_sources.GrabError):
            tracker_sources.fetch_release(_token(grab, 'https://t.example/dl/1.torrent'))
        assert http.sent == [(PUBLIC, 't.example', '/dl/1.torrent')]


@pytest.mark.parametrize('kind', ['rfc1918-10', 'loopback', 'metadata', 'this-host'])
def test_a_tracker_that_rebinds_mid_grab_is_refused(grab, http, kind):
    """t.example answers public until the first request has gone, private after: the same-site redirect stops."""
    http.dns.map['t.example'] = lambda: [BLOCKED[kind]] if http.sent else [PUBLIC]
    http.replies = [(302, {'Location': '/dl/real.torrent'}, b''), (200, {}, b'd4:infod6:lengthi1e4:name1:aee')]
    with pytest.raises(tracker_sources.GrabError):
        tracker_sources.fetch_release(_token(grab, 'https://t.example/dl/1.torrent'))
    assert http.sent == [(PUBLIC, 't.example', '/dl/1.torrent')]


def test_a_public_release_still_downloads(grab, http):
    body = b'd4:infod6:lengthi1e4:name1:aee'
    http.replies = [(302, {'Location': 'https://cdn.example/f.torrent'}, b''), (200, {}, body)]
    kind, got = tracker_sources.fetch_release(_token(grab, 'https://t.example/dl/1.torrent'))
    assert (kind, got) == ('torrent', body)
    assert [s[0] for s in http.sent] == [PUBLIC, PUBLIC_CDN]


# ------------------------------------------------------------------ grabs: pasted / non-token links (outbound_guard)

@pytest.fixture
def own(monkeypatch):
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset({OWN}))


@pytest.mark.parametrize('kind', IDS)
def test_a_pasted_link_to_a_blocked_address_is_never_opened(net, own, kind):  # noqa: F811
    net.dns['lan.example'] = [[BLOCKED[kind]]]
    with pytest.raises(og.BlockedURL):
        og.fetch('https://lan.example/x.torrent')
    assert net.opens == []


@pytest.mark.parametrize('kind', IDS)
def test_a_pasted_link_redirecting_to_a_blocked_address_stops(net, own, kind):  # noqa: F811
    net.dns['pub.example'] = [[PUBLIC]]
    net.dns['lan.example'] = [[BLOCKED[kind]]]
    net.routes[(PUBLIC, 'pub.example', '/x')] = (302, {'Location': 'http://lan.example/y'}, b'')
    with pytest.raises(og.BlockedURL):
        og.fetch('http://pub.example/x')
    assert [o[1] for o in net.opens] == [PUBLIC]


def test_a_pasted_link_that_rebinds_is_pinned_then_refused(net, own):  # noqa: F811
    net.dns['pub.example'] = [[PUBLIC], ['10.0.0.5']]
    net.routes[(PUBLIC, 'pub.example', '/x')] = (302, {'Location': '/y'}, b'')
    with pytest.raises(og.BlockedURL):
        og.fetch('http://pub.example/x')
    assert [(o[1], o[5]) for o in net.opens] == [(PUBLIC, 'pub.example')]


def test_this_hosts_own_address_is_refused_for_a_pasted_movie_link(client, net, qbit, own):  # noqa: F811
    _require_auth_everywhere()
    _owner_client(client)
    net.dns['me.example'] = [[OWN]]
    r = _post(client, 'https://me.example/x.torrent')
    assert r.status_code == 400 and r.get_json()['error'] == 'Link not allowed'
    assert net.opens == [] and qbit.added == [] and qbit.logins == 0


@pytest.mark.parametrize('kind', ['rfc1918-192', 'loopback', 'metadata', 'this-host'])
def test_a_tv_grab_link_off_the_lan(net, own, kind):  # noqa: F811
    net.dns['lan.example'] = [[BLOCKED[kind]]]
    for tokens_only in (False, True):
        with pytest.raises(tracker_sources.GrabError) as e:
            tv_grab.fetch_link('https://lan.example/x.torrent', tokens_only=tokens_only)
        assert e.value.status == 400
    assert net.opens == []


# ------------------------------------------------------------------ static: the bridge is gone, no side doors

REMOVED = ('ProwlarrClient', 'TorznabClient', 'lan_prowlarr_fetch', 'indexer_guard')
# Tracker and grab modules: no network primitive of their own (tracker_http / outbound_guard do it).
TRACKER_GRAB = ('indexer_api.py', 'tracker_clients.py', 'tracker_search.py', 'tracker_sources.py', 'tracker_store.py',
                'tracker_routes.py', 'tracker_guard.py', 'tracker_files.py', 'tracker_types.py', 'tv_search.py',
                'tv_grab.py', 'tv_budget.py')
NET_CALLS = {('requests', 'get'), ('requests', 'post'), ('requests', 'put'), ('requests', 'head'),
             ('requests', 'request'), ('requests', 'Session'), ('urllib', 'request'), ('http', 'client'),
             ('socket', 'socket'), ('socket', 'create_connection'), ('urllib3', 'PoolManager'),
             ('urllib3', 'HTTPConnectionPool'), ('urllib3', 'HTTPSConnectionPool')}


def test_no_app_module_still_uses_the_bridge():
    hits = []
    for f in APP.rglob('*.py'):
        tree = ast.parse(f.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            name = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                    else None)
            if name in REMOVED:
                hits.append(f'{f.name}:{node.lineno}:{name}')
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    if a.name.split('.')[-1] in REMOVED or (getattr(node, 'module', '') or '').endswith('indexer_guard'):
                        hits.append(f'{f.name}:{node.lineno}:import {a.name}')
    assert hits == []
    assert not (APP / 'indexer_guard.py').exists()
    from app import indexer_api
    for gone in ('ProwlarrClient', 'TorznabClient', 'GUARD_LIMITS', 'guard_state'):
        assert not hasattr(indexer_api, gone), gone              # the Prowlarr path's own guard went with it
    assert not hasattr(tv_grab, 'lan_prowlarr_fetch')


def test_tracker_and_grab_modules_have_no_network_side_door():
    """Only tracker_http (trackers) and outbound_guard (links) open connections. tv_grab's one
    requests.Session is its qBittorrent session factory (the download client, not a tracker)."""
    hits = []
    for name in TRACKER_GRAB:
        f = APP / name
        if not f.exists():
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if (node.value.id, node.attr) in NET_CALLS:
                    hits.append(f'{name}:{node.lineno}:{node.value.id}.{node.attr}')
            if isinstance(node, ast.ImportFrom) and node.module in ('urllib.request', 'http.client', 'socket', 'urllib3'):
                hits.append(f'{name}:{node.lineno}:from {node.module}')
    assert hits == ['tv_grab.py:%d:requests.Session' % _qbit_factory_line()]


def _qbit_factory_line():
    src = (APP / 'tv_grab.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for d in fn.args.defaults + fn.args.kw_defaults:
                if isinstance(d, ast.Attribute) and getattr(d.value, 'id', '') == 'requests' and d.attr == 'Session':
                    assert fn.args.kwonlyargs or fn.args.args
                    return d.lineno
    return -1


def test_the_prowlarr_import_is_only_reachable_from_its_owner_routes():
    users = set()
    for f in APP.rglob('*.py'):
        if f.name == 'tracker_import.py':
            continue
        if 'tracker_import' in f.read_text(encoding='utf-8'):
            users.add(f.name)
    assert users == {'tracker_routes.py'}
