"""Tester's #16 QA follow-ups (I-A..I-H, ride with PR2): backoff on the
resolved IP, guard file mode, save-time literal and DNS checks, fec0::/10,
per-type Test debounce, the AvistaZ sign-in gap and interface addresses."""

import json
import os
import stat
import time

import pytest

import app.main
import app.user_auth
from app import tracker_clients, tracker_guard, tracker_http, tracker_routes, tracker_store
from app.tracker_guard import LIMITS, TrackerGuard, guard_key, guarded_test, ip_key
from app.tracker_http import TrackerError, raise_for_status
from app.tracker_search import TrackerSearch
from app.tracker_store import TrackerStore

IP1, IP2 = '93.184.215.14', '93.184.215.15'
APIKEY = 'ak_0123456789abcdef0123456789abcdef'
DNS = {'idx.example': [IP1], 'alias.example': [IP1], 'other.example': [IP2], 'cdn-site.example': [IP1],
       'iptorrents.com': [IP1]}


@pytest.fixture(autouse=True)
def _dns(monkeypatch):
    def resolve(host, port):       # like getaddrinfo: a literal resolves to itself
        lit = tracker_http.ip_literal(host)
        return [lit] if lit else DNS.get(host.lower().rstrip('.'), [IP2])
    monkeypatch.setattr(tracker_http, 'resolve', resolve)
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset())


@pytest.fixture
def guard(tmp_path):
    g = TrackerGuard(tmp_path / 'tracker_guard.json')
    tracker_guard.reset_guard_for_tests(g)
    old = LIMITS['min_interval_s']
    LIMITS['min_interval_s'] = 0.0
    yield g
    LIMITS['min_interval_s'] = old
    tracker_guard.reset_guard_for_tests(None)


@pytest.fixture
def store(tmp_path, guard):
    s = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key')
    tracker_store.reset_store_for_tests(s)
    tracker_routes._test_log.clear()
    yield s
    tracker_store.reset_store_for_tests(None)


@pytest.fixture
def owner(test_app, store, monkeypatch):
    calls = []
    monkeypatch.setitem(tracker_routes._cfg, 'tester', lambda cfg: calls.append(dict(cfg)))
    user = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update({'REMOTE_ADDR': '192.168.1.20', 'HTTP_ORIGIN': 'http://localhost', 'HTTP_HOST': 'localhost'})
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(user['id']))
    c.calls = calls
    return c


def _post(c, path, body):
    return c.post(path, data=json.dumps(body), content_type='application/json')


def _torznab(url, **over):
    body = {'type': 'torznab', 'name': 'Idx', 'url': url, 'api_key': APIKEY, 'categories': [2000], 'priority': 20}
    body.update(over)
    return body


class _Resp:
    def __init__(self, status, headers=None, body=b'<rss/>'):
        self.status_code, self.headers, self._body, self.url = status, headers or {}, body, ''

    def iter_content(self, n):
        return iter([self._body])

    def close(self):
        pass


class _Server:
    """One fake server per address; counts the requests that reach it."""

    def __init__(self):
        self.hits = []
        self.answer = _Resp(200)

    def session_for(self):
        server = self

        class S:
            def request(self, method, url, **kw):
                server.hits.append(url)
                return server.answer

            def close(self):
                pass
        return S()


def _runner(server):
    def run(cfg):
        resp = tracker_http.request('GET', cfg['url'], session=server.session_for())
        raise_for_status(resp)
    return run


# ------------------------------------------------------------------ I-A backoff on the resolved IP

def test_rate_limit_covers_other_names_and_the_ip_literal_of_the_server(guard):
    server = _Server()
    server.answer = _Resp(429, {'Retry-After': '600'})
    run = _runner(server)
    with pytest.raises(TrackerError) as e:
        guarded_test({'type': 'torznab', 'url': 'https://idx.example/api'}, run)
    assert e.value.kind == 'rate' and len(server.hits) == 1
    assert guard.blocked(ip_key(IP1))[1] == 'rate'
    server.answer = _Resp(200)
    for url in ('https://alias.example/api', f'http://{IP1}:8080/api', 'https://alias.example/other/api'):
        with pytest.raises(TrackerError) as e:
            guarded_test({'type': 'newznab', 'url': url}, run)
        assert e.value.kind == 'rate', url
    assert len(server.hits) == 1                      # none of them reached the server
    guarded_test({'type': 'torznab', 'url': 'https://other.example/api'}, run)   # another server is fine
    assert len(server.hits) == 2


def test_ban_on_the_ip_survives_a_restart(guard, tmp_path):
    server = _Server()
    server.answer = _Resp(403, body=b'Your account is banned')
    with pytest.raises(TrackerError):
        guarded_test({'type': 'torznab', 'url': 'https://idx.example/api'}, _runner(server))
    kind = guard.blocked(ip_key(IP1))
    fresh = TrackerGuard(tmp_path / 'tracker_guard.json')
    assert fresh.blocked(ip_key(IP1)) == kind and fresh.fail_closed_until == 0
    tracker_guard.reset_guard_for_tests(fresh)
    server.answer = _Resp(200)
    with pytest.raises(TrackerError):
        guarded_test({'type': 'torznab', 'url': 'https://alias.example/api'}, _runner(server))
    assert len(server.hits) == 1


def test_site_types_on_a_shared_address_are_not_blocked(guard):
    """A CDN address shared with a backed-off Torznab server never blocks a
    site type (they are keyed by type)."""
    server = _Server()
    server.answer = _Resp(429, {'Retry-After': '600'})
    with pytest.raises(TrackerError):
        guarded_test({'type': 'torznab', 'url': 'https://idx.example/api'}, _runner(server))
    server.answer = _Resp(200)
    guarded_test({'type': 'iptorrents', 'url': 'https://iptorrents.com'}, _runner(server))
    assert len(server.hits) == 2


def test_site_type_rate_limit_does_not_key_the_ip(guard):
    server = _Server()
    server.answer = _Resp(429, {'Retry-After': '600'})
    with pytest.raises(TrackerError):
        guarded_test({'type': 'iptorrents', 'url': 'https://iptorrents.com'}, _runner(server))
    assert guard.blocked(ip_key(IP1)) is None
    server.answer = _Resp(200)
    guarded_test({'type': 'torznab', 'url': 'https://cdn-site.example/api'}, _runner(server))
    assert len(server.hits) == 2


def test_auth_failures_do_not_key_the_ip(guard):
    server = _Server()
    server.answer = _Resp(401)
    with pytest.raises(TrackerError):
        guarded_test({'type': 'torznab', 'url': 'https://idx.example/api'}, _runner(server))
    assert guard.blocked(ip_key(IP1)) is None


def test_search_keys_the_ip_too(store, guard):
    server = _Server()
    server.answer = _Resp(429, {'Retry-After': '600'})
    a, _ = store.create(_torznab('https://idx.example/api', name='A'))
    b, _ = store.create(_torznab('https://alias.example/api', name='B'))

    def req(method, url, **kw):
        return tracker_http.request(method, url, session=server.session_for(), **kw)
    eng = TrackerSearch(store=store, guard=guard, request=req)
    with pytest.raises(TrackerError):
        eng.search_one(store.config(store.get(a['id'])), 'x', [2000], 10, True)
    server.answer = _Resp(200)
    with pytest.raises(TrackerError) as e:
        eng.search_one(store.config(store.get(b['id'])), 'x', [2000], 10, True)
    assert e.value.kind == 'rate' and len(server.hits) == 1


def test_ip_keys_are_valid_guard_keys():
    assert ip_key('2606:4700:4700:0:0:0:0:1111') == 'ip:2606:4700:4700::1111'
    assert tracker_guard._KEY_RE.match(ip_key(IP1))


# ------------------------------------------------------------------ I-C guard file mode

def test_guard_file_is_owner_only(guard):
    guard.failure('host:idx.example', 'rate', 60)
    assert stat.S_IMODE(os.stat(guard.path).st_mode) == 0o600


# ------------------------------------------------------------------ I-D save-time literals and names

@pytest.mark.parametrize('host', ['127.1', '2130706433', '0x7f.1', '0x7f000001', '017700000001', '0177.0.0.1',
                                  '127.000.000.001', '10.1', '0xa9.254.169.254', 'LOCALHOST.', 'localhost..',
                                  'Foo.LocalHost.', '0', '0.0.0.0'])
def test_encoded_literals_and_localhost_are_refused_at_save(owner, store, host):
    r = _post(owner, '/api/trackers', _torznab(f'http://{host}:7801/api'))
    assert r.status_code == 400 and r.get_json()['fields']['url'] == 'Invalid URL', host
    assert store.list() == []


@pytest.mark.parametrize('addr', ['127.0.0.1', '10.0.0.5', '169.254.169.254', '100.64.0.9', 'fd00::1', '::1',
                                  '::ffff:127.0.0.1', 'fec0::1'])
def test_names_resolving_to_blocked_addresses_are_refused_at_save(owner, store, monkeypatch, addr):
    monkeypatch.setitem(DNS, 'priv.example', [addr])
    r = _post(owner, '/api/trackers', _torznab('https://priv.example/api'))
    assert r.status_code == 400 and r.get_json()['fields']['url'] == 'Address not allowed'
    assert store.list() == []


def test_mixed_answers_are_refused_at_save(owner, store, monkeypatch):
    monkeypatch.setitem(DNS, 'mixed.example', [IP1, '127.0.0.1'])
    assert _post(owner, '/api/trackers', _torznab('https://mixed.example/api')).get_json()['fields']['url'] \
        == 'Address not allowed'


def test_edit_to_a_private_name_is_refused(owner, store, monkeypatch):
    t = _post(owner, '/api/trackers', _torznab('https://idx.example/api')).get_json()['tracker']
    monkeypatch.setitem(DNS, 'priv.example', ['192.168.1.10'])
    r = owner.put(f"/api/trackers/{t['id']}", data=json.dumps({'url': 'https://priv.example/api', 'api_key': APIKEY}),
                  content_type='application/json')
    assert r.status_code == 400 and r.get_json()['fields']['url'] == 'Address not allowed'
    assert store.get(t['id'])['url'] == 'https://idx.example/api'


def test_unresolvable_names_still_save(owner, store, monkeypatch):
    def nx(host, port):
        raise OSError('no such host')
    monkeypatch.setattr(tracker_http, 'resolve', nx)
    assert _post(owner, '/api/trackers', _torznab('https://new.example/api')).status_code == 201


def test_public_names_and_literals_save(owner, store):
    assert _post(owner, '/api/trackers', _torznab('https://idx.example/api', name='A')).status_code == 201
    assert _post(owner, '/api/trackers', _torznab(f'http://{IP1}:9696/x/api', name='B')).status_code == 201


def test_ip_literal_spellings():
    assert tracker_http.ip_literal('127.1') == '127.0.0.1'
    assert tracker_http.ip_literal('0x7f.1') == '127.0.0.1'
    assert tracker_http.ip_literal('2130706433') == '127.0.0.1'
    assert tracker_http.ip_literal('0177.0.0.1') == '127.0.0.1'
    assert tracker_http.ip_literal('fec0::1') == 'fec0::1'
    assert tracker_http.ip_literal('1e100.net') is None
    assert tracker_http.ip_literal('deadbeef') is None
    assert tracker_http.ip_literal('idx.example') is None


def test_trailing_dot_localhost_is_blocked_on_connect():
    with pytest.raises(TrackerError) as e:
        tracker_http.check_target('http://LOCALHOST.:7801/api')
    assert e.value.kind == 'blocked'


# ------------------------------------------------------------------ I-E fec0::/10

@pytest.mark.parametrize('addr', ['fec0::1', 'feff::1', 'fed0:1::5', '64:ff9b::7f00:1', '64:ff9b::a00:5'])
def test_site_local_and_nat64_private_are_blocked(addr):
    assert not tracker_http.address_allowed(addr)


def test_nat64_of_a_public_address_is_allowed():
    assert tracker_http.address_allowed('64:ff9b::5db8:d70e')       # 93.184.215.14


def test_fec0_literal_is_refused_at_save(owner, store):
    r = _post(owner, '/api/trackers', _torznab('http://[fec0::1]:7801/api'))
    assert r.status_code == 400 and r.get_json()['fields']['url'] == 'Invalid URL'


# ------------------------------------------------------------------ I-F per-type Test debounce

def test_torznab_tests_on_a_host_do_not_debounce_a_site_type_test(owner, store, monkeypatch):
    monkeypatch.setitem(DNS, 'iptorrents.com', [IP2])
    for _ in range(tracker_routes.TEST_MAX):
        _post(owner, '/api/trackers/test', _torznab('https://iptorrents.com/api'))
        tracker_routes._test_log.update({k: [t - tracker_routes.TEST_SPACING_S - 1 for t in v]
                                         for k, v in tracker_routes._test_log.items()})
    assert _post(owner, '/api/trackers/test', _torznab('https://iptorrents.com/api')).get_json()['error'] \
        == 'Rate limited'
    ipt = {'type': 'iptorrents', 'name': 'IPT', 'url': 'https://iptorrents.com', 'cookie': 'uid=1; pass=x',
           'categories': [2000], 'priority': 20}
    assert _post(owner, '/api/trackers/test', ipt).get_json() == {'ok': True, 'message': 'Connected'}


def test_debounce_is_still_shared_within_a_type(owner, store):
    _post(owner, '/api/trackers/test', _torznab('https://idx.example/api'))
    assert _post(owner, '/api/trackers/test', _torznab('https://IDX.example/other/api')).get_json()['error'] \
        == 'Rate limited'


def test_throttle_keys_are_per_type():
    assert tracker_routes._throttle_keys({'type': 'torznab', 'url': 'https://www.Idx.example/api'}) == \
        ['torznab|host:idx.example']
    assert tracker_routes._throttle_keys({'type': 'iptorrents', 'url': 'https://iptorrents.com'}) == \
        ['iptorrents|site:iptorrents', 'iptorrents|host:iptorrents.com']


# ------------------------------------------------------------------ I-G AvistaZ sign-in gap

def _avz_cfg():
    return {'id': 3, 'type': 'privatehd', 'name': 'PHD', 'url': 'https://phd.example', 'username': 'me',
            'password': 'pw_secret_1', 'pid': 'pid_secret_1', 'categories': [2000]}


class _AvzResp:
    def __init__(self, status, body):
        self.status, self.body, self.headers = status, json.dumps(body).encode(), {}

    def text(self):
        return self.body.decode()


def test_avistaz_test_waits_two_seconds_between_sign_in_and_search(monkeypatch):
    tracker_clients.clear_avistaz_tokens()
    events = []
    monkeypatch.setattr(tracker_clients, '_sleep', lambda s: events.append(('sleep', s)))
    answers = [_AvzResp(200, {'token': 'tok', 'expiry': 3600}), _AvzResp(200, {'data': []}),
               _AvzResp(200, {'data': []})]

    def req(method, url, **kw):
        events.append(('req', url.split('?')[0].rsplit('/', 1)[-1]))
        return answers.pop(0)
    tracker_clients.test(_avz_cfg(), req)
    assert events == [('req', 'auth'), ('sleep', 2.0), ('req', 'torrents')]
    assert tracker_clients.AVZ_LOGIN_GAP_S >= LIMITS['min_interval_s']
    events.clear()
    tracker_clients.test(_avz_cfg(), req)              # cached token: no sign-in, no pause
    assert events == [('req', 'torrents')]
    tracker_clients.clear_avistaz_tokens()


def test_avistaz_re_sign_in_also_pauses(monkeypatch):
    tracker_clients.clear_avistaz_tokens()
    events = []
    monkeypatch.setattr(tracker_clients, '_sleep', lambda s: events.append('sleep'))
    answers = [_AvzResp(200, {'token': 'a'}), _AvzResp(401, {}), _AvzResp(200, {'token': 'b'}),
               _AvzResp(200, {'data': []})]

    def req(method, url, **kw):
        events.append(url.split('?')[0].rsplit('/', 1)[-1])
        return answers.pop(0)
    tracker_clients.test(_avz_cfg(), req)
    assert events == ['auth', 'sleep', 'torrents', 'auth', 'sleep', 'torrents']
    tracker_clients.clear_avistaz_tokens()


# ------------------------------------------------------------------ I-H interface addresses

FIB = """Main:
  +-- 0.0.0.0/0 3 0 5
     |-- 0.0.0.0
        /0 universe UNICAST
     +-- 44.0.0.0/24 2 0 2
        |-- 44.0.0.77
           /32 host LOCAL
        |-- 44.0.0.255
           /32 link BROADCAST
     +-- 172.30.0.0/16 2 0 2
        |-- 172.30.0.2
           /32 host LOCAL
Local:
  +-- 127.0.0.0/8 2 0 2
     |-- 127.0.0.1
        /32 host LOCAL
"""
IF6 = """00000000000000000000000000000001 01 80 10 80       lo
2606470047000000000000000000abcd 02 40 00 00     eth0
fe800000000000000000000000000001 02 40 20 80     eth0
"""


def test_interface_addresses_from_kernel_tables(tmp_path, monkeypatch):
    (tmp_path / 'fib').write_text(FIB)
    (tmp_path / 'if6').write_text(IF6)
    monkeypatch.setattr(tracker_http, 'PROC_FIB_TRIE', str(tmp_path / 'fib'))
    monkeypatch.setattr(tracker_http, 'PROC_IF_INET6', str(tmp_path / 'if6'))
    assert tracker_http.interface_addresses() == frozenset(
        {'44.0.0.77', '172.30.0.2', '127.0.0.1', '::1', '2606:4700:4700::abcd', 'fe80::1'})


def test_own_public_interface_address_is_refused(tmp_path, monkeypatch):
    monkeypatch.undo()          # the real own_addresses, not the autouse stub
    monkeypatch.setattr(tracker_http, 'resolve', lambda host, port: ['44.0.0.77'])
    (tmp_path / 'fib').write_text(FIB)
    (tmp_path / 'if6').write_text(IF6)
    monkeypatch.setattr(tracker_http, 'PROC_FIB_TRIE', str(tmp_path / 'fib'))
    monkeypatch.setattr(tracker_http, 'PROC_IF_INET6', str(tmp_path / 'if6'))
    tracker_http.reset_own_addresses_cache()
    try:
        assert not tracker_http.address_allowed('44.0.0.77')
        assert not tracker_http.address_allowed('2606:4700:4700::abcd')
        assert tracker_http.address_allowed('44.0.0.78')
        with pytest.raises(TrackerError) as e:
            tracker_http.check_target('https://pub.example/api')
        assert e.value.kind == 'blocked'
    finally:
        tracker_http.reset_own_addresses_cache()


def test_missing_kernel_tables_are_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker_http, 'PROC_FIB_TRIE', str(tmp_path / 'none'))
    monkeypatch.setattr(tracker_http, 'PROC_IF_INET6', str(tmp_path / 'none6'))
    assert tracker_http.interface_addresses() == frozenset()
