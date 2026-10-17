"""#16 review: secrets bound to their saved URL, sign-in required, Test through
the persisted guard (429 / Retry-After), no stale reads, clean key errors."""

import json
import threading
import time

import pytest

import app.main
import app.user_auth
from app import tracker_clients, tracker_guard, tracker_http, tracker_routes, tracker_store
from app.tracker_guard import LIMITS, TrackerGuard, guard_key, host_key
from app.tracker_http import Response, TrackerError
from app.tracker_store import SECRET_MASK, TrackerStore

LAN = {'REMOTE_ADDR': '192.168.1.20'}
COOKIE = 'uid=1234567; pass=abcdefabcdefabcdefabcdefabcdef12'
APIKEY = 'ak_0123456789abcdef0123456789abcdef'


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
    body = {'type': 'torznab', 'name': 'Jack', 'url': 'http://jackett.example/api', 'api_key': APIKEY,
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
    for body in ({'url': 'http://169.254.169.254', 'cookie': SECRET_MASK}, {'url': 'https://new.example'}):
        r = _put(owner, f'/api/trackers/{tid}', body)
        assert r.status_code == 400 and r.get_json()['fields'] == {'cookie': 'Required'}
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
    until, kind = fresh.blocked(tid)
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
    until, kind = tracker_guard.get_guard().blocked(tid)
    assert kind == 'banned' and until - time.time() > 23 * 3600


def test_test_counts_toward_hourly_cap(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    g = tracker_guard.get_guard()
    now = time.time()
    g.log[tid] = [[now - 60, 'i'] for _ in range(LIMITS['interactive_per_hour'])]
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json() == {'ok': False, 'error': 'Rate limited'}
    assert calls == []


def test_test_counts_toward_global_cap(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    g = tracker_guard.get_guard()
    now = time.time()
    g.log[999] = [[now - 60, 'b'] for _ in range(LIMITS['global_per_hour'])]
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json()['error'] == 'Rate limited'
    assert calls == []


def test_successful_test_is_recorded_against_the_tracker(owner, store, sent):
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json()['ok'] is True
    g = tracker_guard.get_guard()
    assert len(g.log[tid]) == 1 and g.log[tid][0][1] == 'i'


def test_unsaved_tests_count_by_host(owner, store, sent):
    calls, _ = sent
    g = tracker_guard.get_guard()
    form = _ipt(url='https://new-site.example')
    assert _post(owner, '/api/trackers/test', form).get_json()['ok'] is True
    assert len(g.log[host_key('https://www.new-site.example')]) == 1


def test_credential_backoff_does_not_block_test(owner, store, sent):
    calls, _ = sent
    tid = _post(owner, '/api/trackers', _ipt()).get_json()['tracker']['id']
    tracker_guard.get_guard().failure(tid, 'auth')
    assert _post(owner, f'/api/trackers/{tid}/test', {}).get_json()['ok'] is True
    assert tracker_guard.get_guard().blocked(tid) is None


def test_guard_key_rules():
    assert guard_key({'id': 4, 'type': 'iptorrents', 'url': 'https://a.example'}) == 4
    assert guard_key({'id': None, 'type': 'iptorrents', 'url': 'https://a.example'}) == host_key('https://a.example')
    assert guard_key({'id': 4, 'type': 'privatehd', 'url': 'https://www.phd.example'}) == host_key('https://phd.example')


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
    ('http://t.example:9117/x', 'http://t.example:9118/y', False),
    ('http://t.example:9117/x', 'https://t.example/y', False),
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
