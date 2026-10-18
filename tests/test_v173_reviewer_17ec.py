"""v173 (Reviewer on 17ecf51, suggested 2-4) + Tester FF-7 (shared metadata block list).

2) own_addresses() adds the configured public app URL's host (APP_URL), and a pasted link to that
   name is refused by name; 3) the Import from Prowlarr GET is pinned to the vetted address;
4) the kiosk news feed goes through outbound_guard.fetch. Fake DNS, a loopback server of the test's
own; nothing leaves the box."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import app.main
from app import outbound_guard, tracker_http, tracker_import, tracker_sources
from app.metadata_addrs import METADATA_ADDRESSES, is_metadata
from tests.test_downloads_add_ssrf import _owner_client, client  # noqa: F401
from tests.test_tracker_import_v170 import _post, env, owner  # noqa: F401


# ------------------------------------------------------------------ FF-7: metadata addresses

@pytest.mark.parametrize('addr', ['169.254.169.254', 'fd00:ec2::254', '100.100.100.200', '::ffff:100.100.100.200',
                                  '169.254.170.2', '[fd00:ec2::254]'])
def test_metadata_addresses_are_on_the_shared_list(addr):
    assert is_metadata(addr)
    assert tracker_sources._blocked_address(addr.strip('[]'))
    assert outbound_guard.is_blocked_ip(addr.strip('[]'))
    assert not tracker_http.address_allowed(addr.strip('[]'))


@pytest.mark.parametrize('addr', ['fd00:ec2::254', '100.100.100.200', '::ffff:100.100.100.200', '169.254.169.254'])
def test_import_from_prowlarr_refuses_metadata(addr, monkeypatch):
    monkeypatch.setattr(tracker_import, '_resolve', lambda host: [addr])
    with pytest.raises(RuntimeError):
        tracker_import.check_target('http://prowlarr.lan:9696')
    lit = f'[{addr}]' if ':' in addr else addr
    with pytest.raises(RuntimeError):
        tracker_import.check_target(f'http://{lit}:9696')


def test_lan_prowlarr_is_still_fine(monkeypatch):
    for a in ('192.168.1.30', 'fd12:3456::30', '10.0.0.5', '100.64.1.2'):
        monkeypatch.setattr(tracker_import, '_resolve', lambda host, a=a: [a])
        assert tracker_import.check_target('http://prowlarr.lan:9696') == a
    assert {'169.254.169.254', 'fd00:ec2::254', '100.100.100.200'} <= METADATA_ADDRESSES


# ------------------------------------------------------------------ 2) the public app URL

@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv('APP_URL', 'https://luma.example.net/')
    real = socket.getaddrinfo

    def gai(host, *a, **k):
        if host == 'luma.example.net':
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.77', 0))]
        return real(host, *a, **k)
    monkeypatch.setattr(socket, 'getaddrinfo', gai)
    tracker_http.reset_own_addresses_cache()
    yield
    tracker_http.reset_own_addresses_cache()


def test_the_public_host_is_one_of_ours(public, monkeypatch):
    assert tracker_http.public_hosts() == frozenset({'luma.example.net'})
    assert '203.0.113.77' in tracker_http.own_addresses()
    monkeypatch.setattr(tracker_http, 'public_behind_tunnel', lambda: True)
    tracker_http.reset_own_addresses_cache()
    assert '203.0.113.77' not in tracker_http.own_addresses()     # the tunnel's edge isn't this host


def test_a_pasted_link_to_lumas_public_name_is_refused_by_name(public, monkeypatch):
    monkeypatch.setattr(tracker_http, 'public_behind_tunnel', lambda: True)
    tracker_http.reset_own_addresses_cache()
    for url in ('https://luma.example.net/api/x', 'http://LUMA.example.net./', 'https://luma.example.net:8443/'):
        with pytest.raises(outbound_guard.BlockedURL):
            outbound_guard.check_url(url)


def test_no_app_url_no_extra_hosts(monkeypatch):
    monkeypatch.delenv('APP_URL', raising=False)
    assert tracker_http.public_hosts() == frozenset()


# ------------------------------------------------------------------ 3) the Prowlarr GET is pinned

class Hosted(BaseHTTPRequestHandler):
    hosts = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Hosted.hosts.append(self.headers.get('Host'))
        body = json.dumps([]).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_the_import_get_goes_to_the_vetted_address_not_a_second_lookup(owner, env, monkeypatch):  # noqa: F811
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Hosted)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        env['settings']['indexer.base_url'] = f'http://prowlarr.lan:{port}'
        monkeypatch.setattr(tracker_import, '_session_factory', None)
        monkeypatch.setattr(tracker_import, '_resolve', lambda host: ['127.0.0.1'])   # the vetted answer
        real = socket.getaddrinfo

        def rebound(host, *a, **k):                       # any later lookup would point elsewhere
            if host == 'prowlarr.lan':
                raise AssertionError('a second DNS lookup for the Prowlarr host')
            return real(host, *a, **k)
        monkeypatch.setattr(socket, 'getaddrinfo', rebound)
        Hosted.hosts = []
        r = _post(owner, '/api/trackers/import/prowlarr/preview')
        assert r.status_code == 200, r.get_json()
        assert Hosted.hosts == [f'prowlarr.lan:{port}']    # Host header keeps the name
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------------ 4) the news feed via outbound_guard

def test_the_news_feed_to_a_private_address_is_refused(client, monkeypatch):  # noqa: F811
    import requests
    app.main._news_cache.clear()
    app.main.settings.update({'ui': {'daily_kiosk': {'news': {'enabled': True, 'feed_url': 'http://127.0.0.1:5002/api/settings',
                                                               'feed_name': 'N'}}}})
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('news must not use requests.get'))
    try:
        r = _owner_client(client).get('/api/news', environ_base={'REMOTE_ADDR': '192.168.1.20'})
        assert r.status_code == 502 and r.get_json()['error'] == 'Feed address not allowed'
    finally:
        app.main.settings.update({'ui': {'daily_kiosk': {'news': {'enabled': False, 'feed_url': ''}}}})
        app.main._news_cache.clear()
