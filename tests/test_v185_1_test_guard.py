"""v185.1 (PM ruling, PrivateHD on live :5002): the cause was the test instance's write guard. It
treated every POST/PUT/DELETE under /api/v1|v3 on ANY host as a Radarr/Sonarr/Prowlarr write, so
PrivateHD's sign-in (POST https://privatehd.to/api/v1/jackett/auth) was refused inside requests; the
refusal (a RequestException) was reported as 'network' ('Connection failed') and started a backoff.
Now the guard is scoped to the configured *arr base URLs (plus LAN/loopback hosts as extra safety);
tracker hosts pass; *arr writes stay blocked; a refusal is logged 'blocked by test guard' and never
starts a backoff. Fakes only: nothing leaves the box."""
import io
import json
import logging
import socket

import pytest
import requests

from app import instance, tracker_clients as tc, tracker_guard, tracker_http
from tests.test_v175_instance_prod_conflict import prod_shape  # noqa: F401

PUBLIC = '93.184.215.14'


@pytest.fixture
def test_box(prod_shape, monkeypatch):  # noqa: F811
    (prod_shape / 'settings.json').write_text(json.dumps({
        'radarr': {'url': 'http://10.0.0.5:7878'}, 'sonarr': {'url': 'https://sonarr.example.com/sonarr'},
        'indexer': {'base_url': 'http://prowlarr.example.net:9696'}}))
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, *a, **k: [(2, 1, 6, '', (PUBLIC, 0))])
    monkeypatch.setattr(tracker_http, 'resolve', lambda host, port: [PUBLIC])
    monkeypatch.setattr(tracker_http, 'own_addresses', lambda: frozenset())
    instance.clear_arr_cache()
    yield prod_shape
    instance.clear_arr_cache()
    instance.uninstall_write_guard()


@pytest.mark.parametrize('url', ['https://privatehd.to/api/v1/jackett/auth', 'https://avistaz.to/api/v1/jackett/auth',
                                 'https://tracker.example.org/api/v3/anything'])
def test_tracker_hosts_are_not_arr_writes(test_box, url):
    assert instance.write_kind('POST', url) is None


@pytest.mark.parametrize('url', [
    'http://10.0.0.5:7878/api/v3/command',                    # configured Radarr
    'https://sonarr.example.com/sonarr/api/v3/series',       # configured Sonarr (public name, with a path)
    'http://prowlarr.example.net:9696/api/v1/indexer',       # configured Prowlarr
    'http://192.168.1.9:8989/api/v3/series',                 # unconfigured, LAN: extra safety
    'http://127.0.0.1:8989/api/v3/series', 'http://[::1]:7878/api/v3/movie', 'http://sonarr:8989/api/v3/series',
    'http://nas.local:7878/api/v3/movie'])
def test_arr_writes_stay_arr(test_box, url):
    assert instance.write_kind('POST', url) == 'arr' and instance.write_kind('DELETE', url) == 'arr'
    assert instance.write_kind('GET', url) is None


def test_a_name_that_resolves_to_the_lan_is_arr(test_box, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, *a, **k: [(2, 1, 6, '', ('192.168.1.20', 0))])
    assert instance.write_kind('POST', 'https://radarr.myhome.example/api/v3/command') == 'arr'


def test_another_port_on_a_configured_public_host_is_not_that_arr(test_box):
    assert instance.write_kind('POST', 'https://sonarr.example.com:8443/api/v3/series') is None


def test_qbit_writes_unchanged(test_box):
    assert instance.write_kind('POST', 'https://privatehd.to/api/v2/torrents/delete') == 'qbit'


def _fake_send(status=200, body=b'{"token": "tok", "expiry": 3600}'):
    sent = []

    def send(self, request, **kw):
        sent.append((request.method, request.url))
        r = requests.models.Response()
        r.status_code, r.raw, r.request = status, io.BytesIO(body), request
        r.headers['Content-Type'] = 'application/json'
        return r
    return send, sent


def test_privatehd_sign_in_passes_the_installed_guard(test_box, monkeypatch):
    send, sent = _fake_send()
    monkeypatch.setattr(requests.Session, 'send', send)       # the "network" under the guard
    assert instance.install_write_guard()
    cfg = {'id': 3, 'type': 'privatehd', 'name': 'PHD', 'url': 'https://privatehd.to', 'username': 'me',
           'password': 'pw_secret_1', 'pid': 'pid_secret_1', 'categories': [2000]}
    tc.clear_avistaz_tokens()
    assert tc._avz_token(cfg, tracker_http.request) == 'tok'
    assert sent and sent[0][0] == 'POST' and '/api/v1/jackett/auth' in sent[0][1]


def test_an_arr_write_is_blocked_by_test_guard_and_starts_no_backoff(test_box, monkeypatch, caplog, tmp_path):
    send, sent = _fake_send()
    monkeypatch.setattr(requests.Session, 'send', send)
    assert instance.install_write_guard()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(tracker_http.TrackerError) as e:
            tracker_http.request('POST', 'https://sonarr.example.com/sonarr/api/v3/command', json={'name': 'x'})
    assert sent == []                                          # nothing left the box
    assert (e.value.kind, str(e.value), e.value.test_guard) == ('blocked', 'Blocked by test guard', True)
    msgs = [r.getMessage() for r in caplog.records]
    assert any('blocked by test guard' in m for m in msgs)
    assert not any('connection failed' in m.lower() or ' failed (' in m for m in msgs if 'Tracker request' in m)
    g = tracker_guard.TrackerGuard(tmp_path / 'guard.json')
    assert tracker_guard.record_failure(g, 'site:privatehd', e.value) == 0.0
    assert not g.blocked('site:privatehd')


def test_prod_is_untouched(prod_shape, monkeypatch):  # noqa: F811
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    assert instance.install_write_guard() is False


def test_a_guard_refusal_reads_blocked_by_test_guard_in_the_sources_status():
    from app import tracker_search
    from app.tracker_http import TEST_GUARD, TrackerError
    e = TrackerError('blocked', message=TEST_GUARD)
    assert str(e) == 'Blocked by test guard' and e.test_guard
    assert tracker_search.search_status(e) == 'guard'
    assert tracker_search.search_status(TrackerError('network')) == 'unavailable'
