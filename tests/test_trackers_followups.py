"""#16 review follow-ups (ride with PR2): Jackett/Prowlarr endpoint detection
on the path a server would see (and on every redirect hop), and IPv6-literal
hosts keeping their persisted backoff."""

import json
import time

import pytest

import app.main
import app.user_auth
from app import tracker_guard, tracker_http, tracker_routes, tracker_store
from app.tracker_guard import LIMITS, TrackerGuard, guard_key, normalize_host
from app.tracker_http import TrackerError, is_proxy_url, normalized_path
from app.tracker_store import TrackerStore

PUBLIC_IP = '93.184.215.14'
APIKEY = 'ak_0123456789abcdef0123456789abcdef'


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(tracker_http, 'resolve', lambda host, port: [PUBLIC_IP])
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset())


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
def owner(test_app, store, monkeypatch):
    calls, state = [], {'err': None}

    def fake(cfg):
        calls.append(dict(cfg))
        if state['err']:
            raise state['err']
    monkeypatch.setitem(tracker_routes._cfg, 'tester', fake)
    user = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update({'REMOTE_ADDR': '192.168.1.20', 'HTTP_ORIGIN': 'http://localhost', 'HTTP_HOST': 'localhost'})
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(user['id']))
    c.calls, c.state = calls, state
    return c


def _post(c, path, body):
    return c.post(path, data=json.dumps(body), content_type='application/json')


def _torznab(url, **over):
    body = {'type': 'torznab', 'name': 'Idx', 'url': url, 'api_key': APIKEY, 'categories': [2000], 'priority': 20}
    body.update(over)
    return body


# ------------------------------------------------------------------ (1) proxy endpoints, normalized

PROXY_PATHS = [
    '/api/v2.0/indexers/x/results/torznab', '/%61pi/v2.0/indexers/x', '/api/v2%2E0/indexers/x/results/torznab',
    '/API/V2.0/Indexers/x', '/api/v2%252E0/indexers/x', '/jackett/api/v2.0/indexers/all/results/torznab',
    '/%31/api', '/1/api', '/1/api/', '/1/api/../api', '/1/x/../api', '/a/b/1/api', '/tools/prowlarr/12/api',
    '//1//api', '//api//v2.0//indexers//x', '/a//b///1/api', '/./1/./api', '/x/../1/api', '/1\\api',
    '/prowlarr', '/media/prowlarr/api', '/api/v1/indexer/5/newznab', '/%2F1%2Fapi',
]
OWN_PATHS = [
    '/api', '/api/torznab', '/torznab/api', '/rss/api', '/2024/api', '/1999/api', '/a/2024/api', '/0/api',
    '/12345/api', '/api/1', '/v2.0/indexers', '/apiv2.0/indexers', '/my-prowlarr-notes/api',
]


@pytest.mark.parametrize('path', PROXY_PATHS)
def test_proxy_paths_are_detected_after_normalizing(path):
    assert is_proxy_url(f'https://idx.example{path}') is True, path


@pytest.mark.parametrize('path', OWN_PATHS)
def test_own_api_paths_are_not_proxies(path):
    assert is_proxy_url(f'https://idx.example{path}') is False, path


def test_normalized_path_matches_what_a_server_sees():
    assert normalized_path('/%61pi//v2%2E0/./x/../indexers/') == '/api/v2.0/indexers'
    assert normalized_path('/../../etc') == '/etc'
    assert normalized_path('') == '/'


@pytest.mark.parametrize('path', ['/%61pi/v2.0/indexers/x', '/api/v2%2E0/indexers/x', '/%31/api', '/1/api/../api',
                                  '/a/b/1/api', '//1//api'])
def test_api_refuses_encoded_proxy_urls_on_save_test_and_edit(owner, store, path):
    url = f'https://idx.example{path}'
    r = _post(owner, '/api/trackers', _torznab(url))
    assert r.status_code == 400 and r.get_json()['fields'] == {'url': 'Invalid URL'}, path
    assert _post(owner, '/api/trackers/test', _torznab(url)).status_code == 400
    tid = _post(owner, '/api/trackers', _torznab('https://idx.example/api')).get_json()['tracker']['id']
    r = owner.put(f'/api/trackers/{tid}', data=json.dumps({'url': url}), content_type='application/json')
    assert r.status_code == 400
    assert owner.calls == []


def test_year_path_is_still_accepted(owner, store):
    assert _post(owner, '/api/trackers', _torznab('https://idx.example/2024/api')).status_code == 201


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


@pytest.mark.parametrize('loc', ['/1/api', '/%31/api', '/x/../2/api?t=caps', '/.//api//v2.0//indexers//all',
                                 'https://idx.example/prowlarr/3/api'])
def test_same_site_redirect_to_a_proxy_is_refused(loc):
    s = _Sess(_Resp(302, {'Location': loc}), _Resp(200, body=b'<rss/>'))
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://idx.example/api', session=s)
    assert e.value.kind == 'blocked' and s.calls == ['https://idx.example/api']


def test_protocol_relative_redirect_is_another_host_and_not_followed():
    s = _Sess(_Resp(302, {'Location': '//api//v2.0//indexers//all'}), _Resp(200))
    r = tracker_http.request('GET', 'https://idx.example/api', session=s)
    assert r.status == 302 and s.calls == ['https://idx.example/api']


def test_first_hop_to_a_proxy_is_refused_without_sending():
    s = _Sess(_Resp(200))
    with pytest.raises(TrackerError):
        tracker_http.request('GET', 'https://idx.example/%31/api?t=caps', session=s)
    assert s.calls == []


def test_same_site_redirect_to_an_own_path_is_followed():
    s = _Sess(_Resp(302, {'Location': '/2024/api'}), _Resp(200, body=b'ok'))
    r = tracker_http.request('GET', 'https://idx.example/api', session=s)
    assert r.status == 200 and s.calls == ['https://idx.example/api', 'https://idx.example/2024/api']


# ------------------------------------------------------------------ (2) IPv6 literal keys survive a restart

@pytest.mark.parametrize('url,key', [
    ('https://[2606:4700:4700::1111]/api', 'host:2606:4700:4700::1111'),
    ('https://[2606:4700:4700:0:0:0:0:1111]:8443/x/api', 'host:2606:4700:4700::1111'),
    ('https://[2606:4700:4700::1111%25eth0]/api', 'host:2606:4700:4700::1111'),
    ('http://93.184.215.14:8080/api', 'host:93.184.215.14'),
])
def test_ip_literal_keys(url, key):
    assert guard_key({'type': 'torznab', 'url': url}) == key
    assert tracker_guard._KEY_RE.match(key)


def test_ipv6_literal_backoff_survives_restart(tmp_path):
    cfg = {'type': 'torznab', 'url': 'https://[2606:4700:4700::1111]/api'}
    g = TrackerGuard(tmp_path / 'tracker_guard.json')
    gk = guard_key(cfg)
    g.failure(gk, 'rate', 1800)
    g.take(gk, True, False)
    fresh = TrackerGuard(tmp_path / 'tracker_guard.json')
    until, kind = fresh.blocked(gk)
    assert kind == 'rate' and until - time.time() > 1790
    assert fresh.fail_closed_until == 0
    # Another spelling of the same address hits the same saved backoff.
    other = guard_key({'type': 'newznab', 'url': 'https://[2606:4700:4700:0::1111]:9443/other/api'})
    assert fresh.blocked(other)[1] == 'rate'


def test_ipv6_literal_test_refused_after_restart(owner, store, tmp_path):
    url = 'https://[2606:4700:4700::1111]/api'
    owner.state['err'] = TrackerError('rate', 429, retry_after=900)
    assert _post(owner, '/api/trackers/test', _torznab(url)).get_json()['error'] == 'Rate limited'
    owner.state['err'] = None
    tracker_routes._test_log.clear()
    tracker_guard.reset_guard_for_tests(TrackerGuard(tmp_path / 'tracker_guard.json'))
    assert _post(owner, '/api/trackers/test', _torznab(url)).get_json() == {'ok': False, 'error': 'Rate limited'}
    assert len(owner.calls) == 1


def test_normalize_host_keeps_names_as_before():
    assert normalize_host('https://WWW.Idx.Example./x') == 'idx.example'
    assert normalize_host('https://bücher.example') == 'xn--bcher-kva.example'


# ------------------------------------------------------------------ (3) family comment matches the code

def test_family_comment_matches_guard_keys():
    src = open(tracker_store.__file__.replace('tracker_store', 'tracker_types'), encoding='utf-8').read()
    assert 'share one account rate limit' not in src
    assert 'strict token bucket' in src
    assert guard_key({'type': 'privatehd', 'url': 'https://privatehd.example'}) == 'site:privatehd'
    assert guard_key({'type': 'cinemaz', 'url': 'https://cinemaz.example'}) == 'site:cinemaz'
