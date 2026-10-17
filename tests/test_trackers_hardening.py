"""#16 review: secrets bound to their saved URL, sign-in required, Test through
the persisted guard (429 / Retry-After), no stale reads, clean key errors."""

import io
import json
import threading
import time

import pytest
import requests

import app.main
import app.user_auth
from app import tracker_clients, tracker_guard, tracker_http, tracker_routes, tracker_store
from app.tracker_guard import LIMITS, TrackerGuard, guard_key, normalize_host
from app.tracker_http import Response, TrackerError
from app.tracker_store import SECRET_MASK, TrackerStore

LAN = {'REMOTE_ADDR': '192.168.1.20'}
COOKIE = 'uid=1234567; pass=abcdefabcdefabcdefabcdefabcdef12'
APIKEY = 'ak_0123456789abcdef0123456789abcdef'
PUBLIC_IP = '93.184.215.14'
IPT_KEY = 'site:iptorrents'       # guard key of every IPTorrents tracker


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(tracker_http, 'resolve', lambda host, port: [PUBLIC_IP])


@pytest.fixture
def store(tmp_path):
    s = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key')
    tracker_store.reset_store_for_tests(s)
    tracker_guard.reset_guard_for_tests(TrackerGuard(tmp_path / 'tracker_guard.json'))
    tracker_routes._test_log.clear()
    old = LIMITS['min_interval_s']
    LIMITS['min_interval_s'] = 0.0
    yield s
    LIMITS['min_interval_s'] = old
    tracker_guard.reset_guard_for_tests(None)
    tracker_store.reset_store_for_tests(None)


@pytest.fixture
def sent(monkeypatch):
    """Every config handed to the network Test, and a switch to make it fail."""
    calls, state = [], {'err': None}

    def fake(cfg):
        calls.append(dict(cfg))
        if state['err']:
            raise state['err']
    monkeypatch.setitem(tracker_routes._cfg, 'tester', fake)
    return calls, state


def _client(test_app, signed_in=True):
    owner = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.environ_base.update({'HTTP_ORIGIN': 'http://localhost', 'HTTP_HOST': 'localhost'})
    if signed_in:
        c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(owner['id']))
    return c


@pytest.fixture
def owner(test_app, store):
    return _client(test_app)


def _post(c, path, body):
    return c.post(path, data=json.dumps(body), content_type='application/json')


def _put(c, path, body):
    return c.put(path, data=json.dumps(body), content_type='application/json')


def _ipt(**over):
    body = {'type': 'iptorrents', 'name': 'IPT', 'url': 'https://iptorrents.example', 'cookie': COOKIE,
            'categories': [2000], 'priority': 10}
    body.update(over)
    return body


def _torznab(**over):
    body = {'type': 'torznab', 'name': 'Idx', 'url': 'http://idx.example/api', 'api_key': APIKEY,
            'categories': [2000], 'priority': 20}
    body.update(over)
    return body


# ------------------------------------------------------------------ sign-in required

def test_anonymous_lan_gets_401_on_every_tracker_route(test_app, store, sent):
    calls, _ = sent
    c = _client(test_app, signed_in=False)
    rec, _ = store.create(_ipt())
    tid = rec['id']
    for method, path, body in [
        ('GET', '/api/trackers/types', None), ('GET', '/api/trackers', None),
        ('POST', '/api/trackers', _ipt(name='Other')), ('GET', f'/api/trackers/{tid}', None),
        ('PUT', f'/api/trackers/{tid}', {'name': 'X'}), ('PUT', f'/api/trackers/{tid}/enabled', {'enabled': False}),
        ('DELETE', f'/api/trackers/{tid}', None), ('POST', f'/api/trackers/{tid}/test', {}),
        ('POST', '/api/trackers/test', {'id': tid, 'cookie': SECRET_MASK}),
    ]:
        r = c.open(path, method=method, data=json.dumps(body) if body is not None else None,
                   content_type='application/json')
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.headers['Cache-Control'] == 'no-store'
    assert calls == []
    assert [t['name'] for t in store.list()] == ['IPT'] and store.list()[0]['enabled'] is True


def test_signed_in_owner_on_lan_is_allowed(owner, sent):
    assert owner.get('/api/trackers').status_code == 200
    assert _post(owner, '/api/trackers', _ipt()).status_code == 201


# ------------------------------------------------------------------ B1: secrets stay with their URL

def test_unsaved_test_with_other_url_never_gets_stored_secret(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    r = _post(owner, '/api/trackers/test', {'id': tid, 'url': 'https://attacker.example', 'cookie': SECRET_MASK})
    assert r.status_code == 400 and r.get_json()['fields'] == {'cookie': 'Required'}
    r = _post(owner, '/api/trackers/test', {'id': tid, 'url': 'https://attacker.example'})
    assert r.status_code == 400
    assert calls == []


def test_unsaved_test_with_other_url_drops_optional_secret(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _torznab()).get_json()['tracker']['id']
    r = _post(owner, '/api/trackers/test', {'id': tid, 'url': 'http://elsewhere.example/api', 'api_key': SECRET_MASK})
    assert r.status_code == 200
    assert len(calls) == 1 and 'api_key' not in calls[0] and calls[0]['id'] is None
    assert APIKEY not in json.dumps(calls)


def test_unsaved_test_with_same_url_uses_stored_secret(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    r = _post(owner, '/api/trackers/test', {'id': tid, 'cookie': SECRET_MASK, 'name': 'IPT'})
    assert r.get_json()['ok'] is True
    assert calls[0]['cookie'] == COOKIE and calls[0]['id'] == tid


def test_url_change_clears_stored_secrets(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    # Masked (or missing) required secret with a new URL: refused, nothing changes.
    for body in ({'url': 'https://new.example', 'cookie': SECRET_MASK}, {'url': 'https://new.example'}):
        r = _put(owner, f'/api/trackers/{tid}', body)
        assert r.status_code == 400 and r.get_json()['fields'] == {'cookie': 'Required'}
    r = _put(owner, f'/api/trackers/{tid}', {'url': 'http://169.254.169.254', 'cookie': SECRET_MASK})
    assert r.status_code == 400 and r.get_json()['fields'] == {'url': 'Invalid URL', 'cookie': 'Required'}
    assert store.get(tid)['url'] == 'https://iptorrents.example'
    assert store.config(store.get(tid))['cookie'] == COOKIE
    # A saved-tracker Test still goes only to the saved URL.
    _post(owner, f'/api/trackers/{tid}/test', {})
    assert [c['url'] for c in calls] == ['https://iptorrents.example']
    # New URL with a re-entered secret: only the new secret is stored.
    r = _put(owner, f'/api/trackers/{tid}', {'url': 'https://new.example', 'cookie': 'uid=9; pass=fresh'})
    assert r.status_code == 200 and r.get_json()['tracker']['cookie'] == SECRET_MASK
    cfg = store.config(store.get(tid))
    assert cfg['url'] == 'https://new.example' and cfg['cookie'] == 'uid=9; pass=fresh'


def test_url_change_clears_optional_secret(owner, store, sent):
    tid = _post(owner, '/api/trackers', _torznab()).get_json()['tracker']['id']
    r = _put(owner, f'/api/trackers/{tid}', {'url': 'http://other.example/api', 'api_key': SECRET_MASK})
    assert r.status_code == 200
    assert r.get_json()['tracker']['api_key'] == ''
    assert store.get(tid)['secrets'] == {}
    assert 'api_key' not in store.config(store.get(tid))


def test_same_url_edit_keeps_masked_secret(owner, store, sent):
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    r = _put(owner, f'/api/trackers/{tid}', {'name': 'IPT 2', 'url': 'https://iptorrents.example/', 'cookie': SECRET_MASK})
    assert r.status_code == 200
    assert store.config(store.get(tid))['cookie'] == COOKIE


# ------------------------------------------------------------------ B2: Test through the guard

def test_test_429_starts_persisted_backoff_with_retry_after(owner, store, sent, tmp_path):
    calls, state = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    state['err'] = TrackerError('rate', 429, retry_after=900)
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json() == {'ok': False, 'error': 'Rate limited'}
    state['err'] = None
    tracker_routes._test_log.clear()
    # A fresh guard (a restart) reads the backoff back from disk and refuses.
    fresh = TrackerGuard(tmp_path / 'tracker_guard.json')
    until, kind = fresh.blocked(IPT_KEY)
    assert kind == 'rate' and 890 < until - time.time() <= 900
    tracker_guard.reset_guard_for_tests(fresh)
    r = _post(owner, f'/api/trackers/{tid}/test', {})
    assert r.get_json() == {'ok': False, 'error': 'Rate limited'}
    # The unsaved-form Test of the same tracker is refused too.
    r = _post(owner, '/api/trackers/test', {'id': tid, 'cookie': SECRET_MASK})
    assert r.get_json() == {'ok': False, 'error': 'Rate limited'}
    assert len(calls) == 1


def test_test_ban_backs_off_a_day(owner, store, sent):
    calls, state = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    state['err'] = TrackerError('banned', 403)
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json()['error'] == 'Banned'
    until, kind = tracker_guard.get_guard().blocked(IPT_KEY)
    assert kind == 'banned' and until - time.time() > 23 * 3600


def test_test_counts_toward_hourly_cap(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    g = tracker_guard.get_guard()
    now = time.time()
    g.log[IPT_KEY] = [[now - 60, 'i'] for _ in range(LIMITS['interactive_per_hour'])]
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json() == {'ok': False, 'error': 'Rate limited'}
    assert calls == []


def test_test_counts_toward_global_cap(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    g = tracker_guard.get_guard()
    now = time.time()
    g.log['host:other.example'] = [[now - 60, 'b'] for _ in range(LIMITS['global_per_hour'])]
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json()['error'] == 'Rate limited'
    assert calls == []


def test_successful_test_is_recorded_against_the_tracker(owner, store, sent):
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json()['ok'] is True
    g = tracker_guard.get_guard()
    assert len(g.log[IPT_KEY]) == 1 and g.log[IPT_KEY][0][1] == 'i'


def test_unsaved_tests_count_by_site(owner, store, sent):
    calls, _ = sent
    g = tracker_guard.get_guard()
    assert _post(owner, '/api/trackers/test', _ipt(url='https://new-site.example')).get_json()['ok'] is True
    assert len(g.log[IPT_KEY]) == 1
    assert _post(owner, '/api/trackers/test', _torznab(url='https://WWW.Idx2.example/x/api')).get_json()['ok'] is True
    assert len(g.log['host:idx2.example']) == 1


def test_credential_backoff_does_not_block_test(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    tracker_guard.get_guard().failure(IPT_KEY, 'auth')
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json()['ok'] is True
    assert tracker_guard.get_guard().blocked(IPT_KEY) is None


def test_guard_key_rules():
    # Known sites: by type, whatever the id or mirror domain.
    for cfg in ({'id': 4, 'type': 'iptorrents', 'url': 'https://a.example'},
                {'id': None, 'type': 'iptorrents', 'url': 'https://b.example/x'},
                {'id': 9, 'type': 'IPTorrents', 'url': 'https://www.a.example'}):
        assert guard_key(cfg) == IPT_KEY
    assert guard_key({'id': 4, 'type': 'privatehd', 'url': 'https://www.phd.example'}) == 'site:privatehd'
    # Torznab/Newznab: by normalized host; id, scheme, port, path and case don't matter.
    keys = {guard_key({'id': i, 'type': t, 'url': u}) for i, t, u in [
        (1, 'torznab', 'https://idx.example/api'), (None, 'torznab', 'http://WWW.IDX.example./other/api'),
        (7, 'newznab', 'https://idx.example:8443/api')]}
    assert keys == {'host:idx.example'}
    assert guard_key({'id': 1, 'type': 'torznab', 'url': 'https://idx-two.example/api'}) == 'host:idx-two.example'
    assert normalize_host('https://bücher.example/x') == 'xn--bcher-kva.example'
    assert normalize_host('not a url') == '' and guard_key({'type': 'torznab', 'url': ''}) == 'host:-'


# ------------------------------------------------------------------ B3: no stale reads

def test_other_writer_seen_every_time_even_same_size(tmp_path):
    a = TrackerStore(tmp_path / 't.json', tmp_path / 'k')
    b = TrackerStore(tmp_path / 't.json', tmp_path / 'k')
    rec, _ = a.create(_ipt(name='AAAA'))
    for i in range(300):
        name = f'N{i % 10}{(i // 10) % 10}{(i // 100) % 10}'      # same length every time
        assert b.list()[0]['name'] != name
        a.update(rec['id'], {'name': name})
        assert b.list()[0]['name'] == name, i
        assert b.get(rec['id'])['name'] == name


def test_concurrent_writers_lose_nothing(tmp_path):
    stores = [TrackerStore(tmp_path / 't.json', tmp_path / 'k') for _ in range(4)]
    errors = []

    def add(s, n):
        for i in range(10):
            _, e = s.create(_torznab(name=f'S{n}-{i}', url=f'http://h{n}-{i}.example/api'))
            if e:
                errors.append(e)
    threads = [threading.Thread(target=add, args=(s, n)) for n, s in enumerate(stores)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    names = [t['name'] for t in TrackerStore(tmp_path / 't.json', tmp_path / 'k').list()]
    assert len(names) == 40 and len(set(names)) == 40


# ------------------------------------------------------------------ smaller follow-ups

def test_corrupt_key_file_is_a_clean_error(owner, store, sent):
    store.key_path.write_bytes(b'not a fernet key')
    store._fernet = None
    r = _post(owner, '/api/trackers', _ipt())
    assert r.status_code == 409 and r.get_json() == {'error': 'Credentials unreadable'}
    assert store.list() == []


def test_delete_drops_avistaz_tokens(owner, store, sent):
    tid = _post(owner, '/api/trackers', {'type': 'privatehd', 'name': 'PHD', 'url': 'https://phd.example',
                                         'username': 'me', 'password': 'pw_secret_1', 'pid': 'pid_secret_1',
                                         'categories': [2000], 'priority': 5}).get_json()['tracker']['id']
    tracker_clients._avz_tokens['x'] = ('tok', time.time() + 3600)
    assert owner.delete(f'/api/trackers/{tid}').status_code == 200
    assert tracker_clients._avz_tokens == {}


@pytest.mark.parametrize('a,b,ok', [
    ('https://t.example/x', 'https://t.example/y', True),
    ('https://t.example/x', 'https://www.t.example:443/y', True),
    ('http://t.example/x', 'https://t.example/y', True),
    ('https://t.example/x', 'http://t.example/y', False),
    ('https://t.example/x', 'https://t.example:8443/y', False),
    ('http://t.example:8080/x', 'http://t.example:8081/y', False),
    ('http://t.example:8080/x', 'https://t.example/y', False),
    ('https://t.example/x', 'ftp://t.example/y', False),
    ('https://t.example/x', 'https://evil.example/y', False),
])
def test_redirects_must_keep_host_and_port(a, b, ok):
    assert tracker_http._same_origin(a, b) is ok


def test_redirect_to_other_port_is_not_followed():
    class R:
        def __init__(self, status, headers, url):
            self.status_code, self.headers, self.url = status, headers, url

        def iter_content(self, n):
            return iter([b''])

        def close(self):
            pass

    class S:
        calls = []

        def request(self, method, url, **kw):
            S.calls.append(url)
            return R(302, {'Location': 'https://t.example:8443/admin'}, url)

        def close(self):
            pass
    r = tracker_http.request('GET', 'https://t.example/api', session=S())
    assert r.status == 302 and S.calls == ['https://t.example/api']


# ------------------------------------------------------------------ SSRF: address policy

@pytest.mark.parametrize('addr', [
    '127.0.0.1', '127.8.9.10', '10.0.0.5', '172.16.0.1', '172.31.255.255', '192.168.1.62',
    '169.254.169.254', '169.254.0.1', '100.64.0.1', '100.127.255.254', '0.0.0.0', '224.0.0.1',
    '239.255.255.250', '255.255.255.255', '198.18.0.1', '192.0.2.1', '::1', '::', 'fe80::1', 'fc00::1',
    'fd12:3456::1', 'ff02::1', '::ffff:10.0.0.1', '::ffff:127.0.0.1', '2002:a00:1::', '64:ff9b::a00:1',
    'not-an-ip',
])
def test_blocked_addresses(addr):
    assert tracker_http.address_allowed(addr) is False


@pytest.mark.parametrize('addr', ['93.184.215.14', '1.1.1.1', '2606:4700:4700::1111'])
def test_public_addresses_allowed(addr, monkeypatch):
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset())
    assert tracker_http.address_allowed(addr) is True


def test_own_public_address_is_blocked(monkeypatch):
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset({'93.184.215.14'}))
    assert tracker_http.address_allowed('93.184.215.14') is False


def test_any_blocked_answer_blocks_the_host(monkeypatch):
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['93.184.215.14', '127.0.0.1'])
    with pytest.raises(TrackerError) as e:
        tracker_http.check_target('https://mixed.example/x')
    assert str(e.value) == 'Address not allowed'


@pytest.mark.parametrize('url', [
    'http://localhost/', 'http://a.localhost/', 'ftp://t.example/', 'https://user:pw@t.example/',
])
def test_blocked_targets(url):
    with pytest.raises(TrackerError) as e:
        tracker_http.check_target(url)
    assert e.value.kind == 'blocked'


@pytest.mark.parametrize('port', [9696, 9117, 5001, 5002, 8443, 6701])
def test_any_port_is_fine_on_a_public_host(port, monkeypatch):
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset())
    monkeypatch.setenv('LUMA_PORT', '6701')
    assert tracker_http.check_target(f'https://t.example:{port}/') == (PUBLIC_IP, port)


def test_luma_ports_are_refused_on_luma_addresses(monkeypatch):
    monkeypatch.setenv('LUMA_PORT', '6701')
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset({'203.0.113.7'}))
    for addr in ('127.0.0.1', '::1', '::ffff:127.0.0.1', '0.0.0.0', '203.0.113.7'):
        for port in (5001, 5002, 6701):
            assert tracker_http.port_allowed(addr, port) is False, (addr, port)
    assert tracker_http.port_allowed(PUBLIC_IP, 6701) is True
    assert tracker_http.port_allowed('127.0.0.1', 9696) is True       # the address itself is refused anyway
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['203.0.113.7'])
    with pytest.raises(TrackerError) as e:
        tracker_http.check_target('https://own-name.example:6701/')
    assert e.value.kind == 'blocked'


def test_unresolvable_host_is_a_connection_error(monkeypatch):
    def boom(h, p):
        raise OSError('nx')
    monkeypatch.setattr(tracker_http, 'resolve', boom)
    with pytest.raises(TrackerError) as e:
        tracker_http.check_target('https://nx.example/')
    assert e.value.kind == 'network'


class _Resp:
    def __init__(self, status, headers=None, body=b''):
        self.status_code, self.headers, self._body, self.url = status, headers or {}, body, ''

    def iter_content(self, n):
        return iter([self._body])

    def close(self):
        pass


class _Sess:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kw):
        self.calls.append(url)
        return self.responses.pop(0)

    def close(self):
        pass


def test_redirect_hop_is_rechecked_before_it_is_sent(monkeypatch):
    answers = iter([['93.184.215.14'], ['10.0.0.9']])     # rebinding between hops
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: next(answers))
    s = _Sess(_Resp(302, {'Location': '/next'}), _Resp(200, body=b'secret-internal'))
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://t.example/start', session=s)
    assert e.value.kind == 'blocked' and s.calls == ['https://t.example/start']


def test_first_hop_to_blocked_address_sends_nothing(monkeypatch):
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['169.254.169.254'])
    s = _Sess(_Resp(200))
    with pytest.raises(TrackerError):
        tracker_http.request('GET', 'http://metadata.example/latest', session=s)
    assert s.calls == []


def test_connection_is_pinned_to_the_checked_address(monkeypatch):
    seen = {}

    def fake_send(self, request, **kw):
        seen['url'], seen['host'], seen['proxies'] = request.url, request.headers.get('Host'), kw.get('proxies')
        r = requests.Response()
        r.status_code, r.raw, r.url = 200, io.BytesIO(b'ok'), request.url
        return r
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', fake_send)
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['93.184.215.14'])
    r = tracker_http.request('GET', 'https://tracker.example:8443/api?q=1')
    assert r.status == 200 and r.body == b'ok'
    assert seen['url'] == 'https://93.184.215.14:8443/api?q=1'
    assert seen['host'] == 'tracker.example:8443' and seen['proxies'] == {}
    assert r.url == 'https://tracker.example:8443/api?q=1'


def test_pinned_https_pool_verifies_the_host_name():
    a = tracker_http.PinnedAdapter('tracker.example', '93.184.215.14')
    req = requests.Request('GET', 'https://93.184.215.14/x').prepare()
    try:
        pool = a.get_connection_with_tls_context(req, True)
    except AttributeError:
        pool = a.get_connection('https://93.184.215.14/x')
    assert pool.host == '93.184.215.14'
    assert pool.assert_hostname == 'tracker.example'
    assert pool.conn_kw['server_hostname'] == 'tracker.example'


def test_adapter_refuses_a_different_host():
    a = tracker_http.PinnedAdapter('tracker.example', '93.184.215.14')
    req = requests.Request('GET', 'https://other.example/x').prepare()
    with pytest.raises(TrackerError):
        a.send(req)


def test_total_deadline(monkeypatch):
    monkeypatch.setattr(tracker_http, 'TOTAL_DEADLINE_S', -1.0)
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://t.example/', session=_Sess(_Resp(200)))
    assert e.value.kind == 'timeout'


# ------------------------------------------------------------------ SSRF through the API

@pytest.mark.parametrize('url', [
    'http://127.0.0.1:6701/api/adb', 'http://169.254.169.254/latest', 'http://10.0.0.5:8080/api',
    'http://192.168.1.62:9696/api', 'http://[::1]/api', 'http://localhost/api', 'http://0.0.0.0/api',
    'http://100.64.0.1/api', 'http://[fd00::5]/api', 'http://172.20.0.2:8117/api', 'http://224.0.0.1/api',
])
def test_api_refuses_blocked_urls_on_save_and_test(owner, store, sent, url):
    calls, _ = sent
    r = _post(owner, '/api/trackers', _torznab(url=url))
    assert r.status_code == 400 and r.get_json()['fields'] == {'url': 'Invalid URL'}
    r = _post(owner, '/api/trackers/test', _torznab(url=url))
    assert r.status_code == 400
    assert calls == [] and store.list() == []


def test_site_types_need_https(owner, store, sent):
    r = _post(owner, '/api/trackers', _ipt(url='http://iptorrents.example'))
    assert r.status_code == 400 and r.get_json()['fields'] == {'url': 'Invalid URL'}
    assert _post(owner, '/api/trackers', _torznab(url='http://idx.example/api')).status_code == 201
    assert _post(owner, '/api/trackers', _torznab(name='Pub', url='https://pub.example:9696/api')).status_code == 201


def test_host_name_resolving_inside_is_refused_at_test(owner, store, monkeypatch):
    """The real Test path: a public-looking name that resolves to loopback."""
    monkeypatch.setitem(tracker_routes._cfg, 'tester', lambda cfg: tracker_clients.test(cfg))
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['127.0.0.1'])
    sent_reqs = []
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: sent_reqs.append(a) or None)
    r = _post(owner, '/api/trackers/test', _torznab(url='http://sneaky.example/api'))
    assert r.get_json() == {'ok': False, 'error': 'Address not allowed'}
    assert sent_reqs == []


# ------------------------------------------------------------------ I1 / I2

def test_ban_is_persisted_and_refuses_test_after_restart(owner, store, sent, tmp_path):
    calls, state = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    state['err'] = TrackerError('banned', 403)
    _post(owner, f'/api/trackers/{tid}/test', {})
    state['err'] = None
    tracker_routes._test_log.clear()
    tracker_guard.reset_guard_for_tests(TrackerGuard(tmp_path / 'tracker_guard.json'))
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json() == {'ok': False, 'error': 'Banned'}
    assert len(calls) == 1
    assert store.get(tid)['status']['message'] == 'Banned'


def test_click_throttle_is_per_host_not_per_url_spelling(owner, store, sent):
    calls, _ = sent
    assert _post(owner, '/api/trackers/test', _torznab(url='http://idx.example/api')).get_json()['ok'] is True
    for variant in ('http://IDX.example/api/', 'http://www.idx.example/other', 'https://idx.example/x'):
        r = _post(owner, '/api/trackers/test', _torznab(url=variant))
        assert r.get_json() == {'ok': False, 'error': 'Rate limited'}, variant
    assert len(calls) == 1


def test_avistaz_test_reuses_a_valid_token(monkeypatch):
    tracker_clients.clear_avistaz_tokens()
    calls = []

    def fake(method, url, **kw):
        calls.append(url.split('?')[0])
        if url.endswith('/auth'):
            return Response(200, {}, b'{"token": "tok", "expiry": 3600}')
        return Response(200, {}, b'{"data": []}')
    cfg = {'id': 3, 'type': 'privatehd', 'name': 'PHD', 'url': 'https://phd.example', 'username': 'me',
           'password': 'pw_secret_1', 'pid': 'pid_secret_1', 'categories': [2000]}
    tracker_clients.test(cfg, fake)
    tracker_clients.test(cfg, fake)
    assert calls.count('https://phd.example/api/v1/jackett/auth') == 1
    tracker_clients.test(dict(cfg, pid='pid_changed_2'), fake)      # new credentials: new sign-in
    assert calls.count('https://phd.example/api/v1/jackett/auth') == 2
    tracker_clients.clear_avistaz_tokens()


# ------------------------------------------------------------------ PM rule: state keyed by site, not record id

def _ban(owner, sent, body):
    calls, state = sent
    tid = _post(owner, '/api/trackers', body).get_json()['tracker']['id']
    state['err'] = TrackerError('rate', 429, retry_after=1800)
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json()['error'] == 'Rate limited'
    state['err'] = None
    tracker_routes._test_log.clear()        # only the persisted guard is left to refuse
    return tid


@pytest.mark.parametrize('body', [_ipt(), _torznab()])
def test_readding_a_tracker_keeps_its_backoff(owner, store, sent, body):
    calls, _ = sent
    tid = _ban(owner, sent, body)
    assert owner.delete(f'/api/trackers/{tid}').status_code == 200
    new = _post(owner, '/api/trackers', body).get_json()['tracker']['id']
    assert new != tid
    assert _post(owner, f'/api/trackers/{new}/test', {}).get_json() == {'ok': False, 'error': 'Rate limited'}
    assert len(calls) == 1


@pytest.mark.parametrize('body,edit', [
    (_ipt(), {'name': 'Renamed', 'cookie': 'uid=2; pass=other'}),
    (_ipt(), {'url': 'https://iptorrents-mirror.example', 'cookie': 'uid=2; pass=other'}),
    (_torznab(), {'url': 'https://www.idx.example/v2/api', 'api_key': 'ak_another_key_000000000000'}),
])
def test_editing_a_tracker_keeps_its_backoff(owner, store, sent, body, edit):
    calls, _ = sent
    tid = _ban(owner, sent, body)
    assert _put(owner, f'/api/trackers/{tid}', edit).status_code == 200
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json() == {'ok': False, 'error': 'Rate limited'}
    assert len(calls) == 1


@pytest.mark.parametrize('body,form', [
    (_ipt(), _ipt(url='https://iptorrents-other.example', cookie='uid=3; pass=x')),
    (_torznab(), _torznab(url='https://IDX.example/another/path/api', api_key='')),
])
def test_unsaved_form_and_other_path_hit_the_same_backoff(owner, store, sent, body, form):
    calls, _ = sent
    _ban(owner, sent, body)
    assert _post(owner, '/api/trackers/test', form).get_json() == {'ok': False, 'error': 'Rate limited'}
    assert len(calls) == 1


def test_one_budget_per_site_across_records_paths_and_forms(owner, store, sent):
    calls, _ = sent
    g = tracker_guard.get_guard()
    a = _post(owner, '/api/trackers', _torznab(name='A', url='https://idx.example/api')).get_json()['tracker']['id']
    b = _post(owner, '/api/trackers', _torznab(name='B', url='https://idx.example/other/api')).get_json()['tracker']['id']
    for path, body in ((f'/api/trackers/{a}/test', {}), (f'/api/trackers/{b}/test', {}),
                       ('/api/trackers/test', _torznab(url='http://www.idx.example/third/api'))):
        tracker_routes._test_log.clear()
        assert _post(owner, path, body).get_json()['ok'] is True
    assert len(g.log['host:idx.example']) == 3
    assert not any(isinstance(k, int) or k.startswith('id:') for k in g.log)
    # Fill the shared hourly budget: every record and form of that host is refused.
    now = time.time()
    g.log['host:idx.example'] = [[now - 60, 'i'] for _ in range(LIMITS['interactive_per_hour'])]
    for path, body in ((f'/api/trackers/{a}/test', {}), (f'/api/trackers/{b}/test', {}),
                       ('/api/trackers/test', _torznab(url='https://idx.example/fresh/api'))):
        tracker_routes._test_log.clear()
        assert _post(owner, path, body).get_json() == {'ok': False, 'error': 'Rate limited'}, path
    assert len(calls) == 3
    # Another host has its own budget.
    tracker_routes._test_log.clear()
    assert _post(owner, '/api/trackers/test', _torznab(url='https://idx-two.example/api')).get_json()['ok'] is True


def test_ipt_mirrors_share_one_budget_and_ban(owner, store, sent):
    calls, _ = sent
    _ban(owner, sent, _ipt())
    tid2 = _post(owner, '/api/trackers', _ipt(name='IPT mirror', url='https://ipt-mirror.example')).get_json()['tracker']['id']
    assert _post(owner, f'/api/trackers/{tid2}/test', {}).get_json() == {'ok': False, 'error': 'Rate limited'}
    assert len(calls) == 1


def test_old_id_keyed_guard_state_is_dropped(tmp_path):
    p = tmp_path / 'tracker_guard.json'
    p.write_text(json.dumps({'log': {'3': [[time.time(), 'i']]}, 'buckets': {}, 'streak': {'3': 1},
                             'cooldown': {'3': [time.time() + 600, 'rate'],
                                          'host:idx.example': [time.time() + 600, 'rate']}}))
    g = TrackerGuard(p)
    assert g.blocked('host:idx.example')[1] == 'rate'
    assert set(g.cooldown) == {'host:idx.example'} and g.log == {} and g.streak == {}
    assert g.fail_closed_until == 0


# ------------------------------------------------------------------ PM rule: no Jackett or Prowlarr

@pytest.mark.parametrize('url', [
    'https://jackett.example/api/v2.0/indexers/iptorrents/results/torznab',
    'https://jackett.example/api/v2.0/indexers/all/results/torznab/api',
    'https://prowlarr.example/5/api', 'https://prowlarr.example/prowlarr/5/api',
    'https://media.example/prowlarr/api', 'https://prowlarr.example/api/v1/indexer/5',
])
def test_jackett_and_prowlarr_endpoints_are_refused(owner, store, sent, url):
    calls, _ = sent
    for t in ('torznab', 'newznab'):
        r = _post(owner, '/api/trackers', _torznab(type=t, url=url))
        assert r.status_code == 400 and r.get_json()['fields'] == {'url': 'Invalid URL'}, (t, url)
        assert _post(owner, '/api/trackers/test', _torznab(type=t, url=url)).status_code == 400
    assert calls == [] and store.list() == []


def test_own_torznab_endpoints_are_accepted(owner, store, sent):
    for i, url in enumerate(('https://tracker.example/api', 'https://tracker.example/api/torznab',
                             'https://tracker.example/torznab/api', 'https://tracker.example/rss/api')):
        assert _post(owner, '/api/trackers', _torznab(name=f'T{i}', url=url)).status_code == 201, url
    tid = store.list()[0]['id']
    assert _put(owner, f'/api/trackers/{tid}', {'url': 'https://tracker.example/7/api'}).status_code == 400
