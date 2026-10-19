"""v184: PrivateHD 'Connection failed' on every pass. Luma pinned each request to the FIRST resolved
address and never tried another, and every failure (no DNS answer, a failed connect, a closed connection)
read the same. Now: IPv4 first, the next resolved address after a failed connect (nothing was sent),
and the reason in the message and the log (kind 'network' unchanged, so backoffs are as before)."""
import http.client

import pytest
import requests
from urllib3.exceptions import NewConnectionError, ProtocolError

from app import tracker_http
from app.tracker_http import TrackerError


class _Resp:
    status_code = 200
    headers = {'Content-Type': 'application/json'}

    def iter_content(self, n):
        yield b'{"ok": true}'

    def close(self):
        pass


@pytest.fixture
def net(monkeypatch):
    tried = []
    plan = {}

    def pinned(url, address):
        class S:
            def request(self, *a, **k):
                tried.append(address)
                x = plan.get(address)
                if x is None:
                    return _Resp()
                raise x

            def close(self):
                pass
        return S()
    monkeypatch.setattr(tracker_http, '_pinned_session', pinned)
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset())
    return tried, plan


def _refused():
    return requests.exceptions.ConnectionError(NewConnectionError(None, 'Connection refused'))


def test_a_failed_connect_tries_the_next_address_ipv4_first(net, monkeypatch):
    tried, plan = net
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['2606:4700:20::681a:aa', '104.26.0.170', '172.67.68.147'])
    plan['104.26.0.170'] = _refused()
    r = tracker_http.request('POST', 'https://privatehd.to/api/v1/jackett/auth', data={'username': 'u'})
    assert r.status == 200
    assert tried == ['104.26.0.170', '172.67.68.147']                    # IPv4 first; the IPv6 one never needed


def test_ipv6_is_the_last_fallback(net, monkeypatch):
    tried, plan = net
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['2606:4700:20::681a:aa', '104.26.0.170'])
    plan['104.26.0.170'] = _refused()
    assert tracker_http.request('GET', 'https://privatehd.to/x').status == 200
    assert tried == ['104.26.0.170', '2606:4700:20::681a:aa']


def test_every_connect_failing_says_couldnt_connect(net, monkeypatch, caplog):
    tried, plan = net
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['104.26.0.170', '172.67.68.147'])
    plan['104.26.0.170'] = plan['172.67.68.147'] = _refused()
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://privatehd.to/x?apikey=SECRET')
    assert (e.value.kind, str(e.value)) == ('network', tracker_http.NO_CONNECT)
    assert len(tried) == 2
    assert 'privatehd.to' in caplog.text and 'SECRET' not in caplog.text and '/x' not in caplog.text


def test_a_closed_connection_is_not_retried_elsewhere(net, monkeypatch):
    """The request may have been sent: never twice (a sign-in or a grab must not repeat)."""
    tried, plan = net
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['104.26.0.170', '172.67.68.147'])
    plan['104.26.0.170'] = requests.exceptions.ConnectionError(
        ProtocolError('Connection aborted.', http.client.RemoteDisconnected('closed')))
    with pytest.raises(TrackerError) as e:
        tracker_http.request('POST', 'https://privatehd.to/api/v1/jackett/auth', data={})
    assert (e.value.kind, str(e.value)) == ('network', tracker_http.NO_RESPONSE)
    assert tried == ['104.26.0.170']


def test_no_dns_answer_says_so(net, monkeypatch):
    tried, _ = net

    def nx(h, p):
        raise OSError('Name or service not known')
    monkeypatch.setattr(tracker_http, 'resolve', nx)
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://privatehd.to/x')
    assert (e.value.kind, str(e.value)) == ('network', tracker_http.NO_DNS) and tried == []


def test_a_blocked_address_among_them_still_blocks_everything(net, monkeypatch):
    tried, _ = net
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['104.26.0.170', '10.0.0.5'])
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://privatehd.to/x')
    assert e.value.kind == 'blocked' and tried == []


def test_the_cause_chain_is_logged_by_class_only(net, monkeypatch, caplog):
    tried, plan = net
    monkeypatch.setattr(tracker_http, 'resolve', lambda h, p: ['104.26.0.170'])
    plan['104.26.0.170'] = requests.exceptions.ConnectionError(
        ProtocolError('Connection aborted. https://privatehd.to/?pid=SECRET', http.client.RemoteDisconnected('closed')))
    with pytest.raises(TrackerError):
        tracker_http.request('POST', 'https://privatehd.to/api/v1/jackett/auth', data={'password': 'SECRET'})
    assert 'ConnectionError <- ProtocolError <- RemoteDisconnected' in caplog.text
    assert 'POST' in caplog.text and 'SECRET' not in caplog.text and 'jackett' not in caplog.text


def test_avistaz_sign_in_search_and_downloads_send_an_app_user_agent(monkeypatch):
    from app import tracker_clients as tc, tracker_sources
    sent = []

    class R:
        def __init__(self, status, body):
            self.status, self._b, self.headers = status, body, {}

        def text(self):
            return self._b.decode()

        def json(self):
            import json
            return json.loads(self._b)

    def request(method, url, headers=None, **kw):
        sent.append((method, url.split('?')[0], dict(headers or {})))
        if url.endswith('/auth'):
            return R(200, b'{"token": "t0k", "expiry": 3600}')
        return R(200, b'{"data": []}')
    monkeypatch.setattr(tc, '_sleep', lambda s: None)
    tc.clear_avistaz_tokens()
    cfg = {'type': 'privatehd', 'url': 'https://privatehd.to', 'username': 'u', 'password': 'p', 'pid': 'x'}
    tc.avistaz_search(cfg, 'Time', [5000], 5, request)
    assert [(m, u) for m, u, _ in sent] == [('POST', 'https://privatehd.to/api/v1/jackett/auth'), ('GET', 'https://privatehd.to/api/v1/jackett/torrents')]
    for _, _, h in sent:
        assert h['User-Agent'] == tc.AVZ_UA and h['User-Agent'] != tracker_http.USER_AGENT
        assert h['User-Agent'].startswith('Luma/') and '(' in h['User-Agent']
    assert sent[1][2]['Authorization'] == 'Bearer t0k'
    assert tracker_sources._download_headers(cfg) == {'User-Agent': tc.AVZ_UA}
    assert tracker_sources._download_headers({'type': 'beyondhd'}) == {}               # others unchanged
    tc.clear_avistaz_tokens()
