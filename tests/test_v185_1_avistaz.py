"""v185.1 (PrivateHD still failing live after v184): the AvistaZ-family client re-audited against the
sites' public API behavior. Sign-in: POST form username/password/pid to <site root>/api/v1/jackett/auth,
Accept JSON, answer {token, expiry}; searches send Authorization: Bearer; a 401 or 412 signs in once
more. Only real network trouble is 'network'. Fakes only: no tracker is contacted."""
import time

import pytest
import requests

from app import tracker_clients as tc
from app import tracker_http
from tests.test_tracker_clients import R, FakeTracker, avz_cfg, _public_dns  # noqa: F401


@pytest.fixture(autouse=True)
def _clear():
    tc.clear_avistaz_tokens()
    yield
    tc.clear_avistaz_tokens()


TOK = R(body={'token': 'tok', 'expiry': 86400})
EMPTY = R(body={'data': []})


def test_sign_in_is_a_form_post_to_the_site_root_with_trimmed_pid():
    cfg = dict(avz_cfg(), url='http://phd.example/login/', username=' me ', pid=' pid_secret_1\n')
    t = FakeTracker(TOK, EMPTY)
    tc.search(cfg, 'x', [2000], 5, t)
    auth, srch = t.calls
    assert (auth['method'], auth['url']) == ('POST', 'https://phd.example/api/v1/jackett/auth')
    assert auth['data'] == {'username': 'me', 'password': 'pw_secret_1', 'pid': 'pid_secret_1'}
    assert 'json' not in auth or auth['json'] is None                   # a form, not a JSON body
    assert auth['headers']['Accept'] == 'application/json' and auth['follow_redirects'] is False
    assert srch['url'].startswith('https://phd.example/api/v1/jackett/torrents?')
    assert srch['headers']['Authorization'] == 'Bearer tok'


def test_the_password_is_sent_exactly_as_saved():
    t = FakeTracker(TOK, EMPTY)
    tc.search(dict(avz_cfg(), password=' p w '), 'x', [2000], 5, t)
    assert t.calls[0]['data']['password'] == ' p w '


@pytest.mark.parametrize('status', [401, 412])
def test_a_rejected_token_signs_in_once_more(status):
    t = FakeTracker(R(body={'token': 'a'}), R(status), R(body={'token': 'b'}), EMPTY)
    assert tc.search(avz_cfg(), 'x', [2000], 5, t) == []
    assert [c['method'] for c in t.calls] == ['POST', 'GET', 'POST', 'GET']
    assert t.calls[-1]['headers']['Authorization'] == 'Bearer b'


@pytest.mark.parametrize('status', [401, 422])
def test_wrong_credentials_are_auth_and_forget_the_token(status):
    t = FakeTracker(R(status, {'message': 'Invalid credentials'}))
    with pytest.raises(tracker_http.TrackerError) as e:
        tc.search(avz_cfg(), 'x', [2000], 5, t)
    assert e.value.kind == 'auth'


def test_a_cloudflare_block_is_not_unauthorized_and_not_network():
    page = '<html><title>Attention Required! | Cloudflare</title>Cloudflare Ray ID: 8a1b</html>'
    t = FakeTracker(R(403, page, {'Server': 'cloudflare'}))
    with pytest.raises(tracker_http.TrackerError) as e:
        tc.search(avz_cfg(), 'x', [2000], 5, t)
    assert (e.value.kind, str(e.value)) == ('http', tc.CF_BLOCKED)
    t = FakeTracker(R(403, 'x', {'cf-mitigated': 'challenge'}))
    with pytest.raises(tracker_http.TrackerError) as e:
        tc.search(avz_cfg(), 'x', [2000], 5, t)
    assert str(e.value) == tc.CF_BLOCKED


def test_a_redirected_sign_in_is_redirect():
    t = FakeTracker(R(302, '', {'Location': 'https://phd.example/login'}))
    with pytest.raises(tracker_http.TrackerError) as e:
        tc.search(avz_cfg(), 'x', [2000], 5, t)
    assert e.value.kind == 'redirect'


@pytest.mark.parametrize('body', ['<html>maintenance</html>', '', '{"token": ""}', '[]'])
def test_a_bad_sign_in_answer_is_never_network(body):
    t = FakeTracker(R(200, body))
    with pytest.raises(tracker_http.TrackerError) as e:
        tc.search(avz_cfg(), 'x', [2000], 5, t)
    assert e.value.kind in ('response', 'auth') and e.value.kind != 'network'


@pytest.mark.parametrize('expiry,want', [(3600, 3600), ('7200', 7200), (None, 3600), (5, 120), (10 ** 7, 86400)])
def test_token_lifetime_in_seconds(expiry, want):
    assert tc._avz_ttl(expiry, 1_000_000_000.0) == want


def test_token_lifetime_as_an_absolute_time():
    now = time.time()
    assert abs(tc._avz_ttl(int(now) + 1800, now) - 1800) < 2
    assert 1700 < tc._avz_ttl(time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now + 1800)), now) < 1900


def test_the_token_is_reused_until_it_nearly_expires():
    t = FakeTracker(TOK, EMPTY, EMPTY)
    tc.search(avz_cfg(), 'x', [2000], 5, t)
    tc.search(avz_cfg(), 'x', [2000], 5, t)
    assert [c['method'] for c in t.calls] == ['POST', 'GET', 'GET']
    t2 = FakeTracker(TOK, EMPTY)
    tc.search(dict(avz_cfg(), pid='pid_secret_1 '), 'x', [2000], 5, t2)   # same trimmed PID: same token
    assert [c['method'] for c in t2.calls] == ['GET']


# ---------------------------------------------------------------- tracker_http: what counts as 'network'

class _Sess:
    def __init__(self, exc=None, body_exc=None):
        self.exc, self.body_exc = exc, body_exc
        self.trust_env = False

    def mount(self, *a):
        pass

    def close(self):
        pass

    def request(self, *a, **kw):
        if self.exc:
            raise self.exc
        outer = self

        class Resp:
            status_code = 200
            headers = {'Content-Encoding': 'br'}

            def iter_content(self, n):
                raise outer.body_exc
                yield b''  # pragma: no cover

            def close(self):
                pass
        return Resp()


def test_an_undecodable_body_is_response_not_network(monkeypatch):
    monkeypatch.setattr(tracker_http, '_pinned_session',
                        lambda url, a: _Sess(body_exc=requests.exceptions.ContentDecodingError('bad brotli')))
    with pytest.raises(tracker_http.TrackerError) as e:
        tracker_http.request('GET', 'https://phd.example/api/v1/jackett/torrents')
    assert (e.value.kind, str(e.value)) == ('response', tracker_http.UNDECODABLE)


def test_an_invalid_url_or_header_is_response_not_network(monkeypatch):
    for exc in (requests.exceptions.InvalidURL('x'), requests.exceptions.InvalidHeader('x')):
        monkeypatch.setattr(tracker_http, '_pinned_session', lambda url, a, exc=exc: _Sess(exc=exc))
        with pytest.raises(tracker_http.TrackerError) as e:
            tracker_http.request('GET', 'https://phd.example/')
        assert (e.value.kind, str(e.value)) == ('response', tracker_http.INVALID_REQUEST)


def test_a_connection_reset_is_still_network(monkeypatch):
    monkeypatch.setattr(tracker_http, '_pinned_session', lambda url, a: _Sess(exc=requests.exceptions.ConnectionError('reset')))
    with pytest.raises(tracker_http.TrackerError) as e:
        tracker_http.request('POST', 'https://phd.example/api/v1/jackett/auth', data={'a': 1})
    assert e.value.kind == 'network'
