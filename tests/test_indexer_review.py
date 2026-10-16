"""Review checks for the Luma indexer manager: download tokens, the download
route, DNS pinning, min-interval spacing, 429 handling and test hygiene."""

import http.server
import json
import os
import shutil
import socket
import ssl
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fake_ssrf_validation(request):
    """Skip real DNS unless a test is marked no_ssrf_mock."""
    if 'no_ssrf_mock' in request.keywords:
        yield
    else:
        with patch('app.indexer_manager.validate_indexer_url', return_value=(True, '', '203.0.113.9')):
            yield


def _settings(values):
    sm = Mock()
    store = dict(values)
    sm.get.side_effect = lambda k, d=None: store.get(k, d)

    def update(payload):
        ix = payload.get('indexer', {})
        for k, v in ix.items():
            store[f'indexer.{k}'] = v
        return True, []
    sm.update.side_effect = update
    sm.store = store
    return sm


def _ok_xml_resp():
    resp = Mock()
    resp.status_code = 200
    resp.headers = {}
    resp.raw = Mock()
    resp.raw.stream.return_value = [b'<?xml version="1.0"?><rss><channel>'
                                    b'<item><title>T</title><link>http://x/1</link><guid>g</guid></item>'
                                    b'</channel></rss>']
    resp.raise_for_status = Mock()
    return resp


DIRECT = {'id': 'ix1', 'name': 'Direct', 'url': 'https://tracker.example.com/api',
          'api_key': 'k', 'type': 'torznab', 'enabled': True}
NZB = {'id': 'ix3', 'name': 'Usenet', 'url': 'https://nzb.example.com/api',
       'api_key': 'nk', 'type': 'newznab', 'enabled': True}
PROXIED = {'id': 'ix2', 'name': 'AvistaZ', 'url': '', 'api_key': '', 'type': 'torznab',
           'enabled': True, 'prowlarr_proxy': True, 'prowlarr_id': '7', 'rate_limited': True}


# ---- 1. Opaque download tokens ----

def test_tokens_are_opaque_and_resolve(temp_data_dir):
    from app.indexer_manager import IndexerManager
    m = IndexerManager(_settings({'indexer.indexers': [DIRECT]}))
    url = 'https://tracker.example.com/dl?id=5&apikey=secret'
    tok = m.issue_download_token(url, 'ix1')
    assert len(tok) >= 40 and 'secret' not in tok and 'tracker' not in tok
    assert all(c.isalnum() or c in '-_' for c in tok)
    assert m.resolve_download_token(tok) == ('https://tracker.example.com/dl?id=5', 'ix1', True)
    assert m.resolve_download_token('not-a-token') is None
    assert m.issue_download_token(url, 'ix1') != tok


def test_tokens_expire_and_are_pruned(temp_data_dir):
    from app import indexer_manager as im
    m = im.IndexerManager(_settings({'indexer.indexers': [DIRECT]}))
    now = [1000.0]
    with patch('app.indexer_manager.time.time', side_effect=lambda: now[0]):
        old = m.issue_download_token('https://tracker.example.com/a', 'ix1', ttl=60)
        now[0] += 61
        assert m.resolve_download_token(old) is None
        m.issue_download_token('https://tracker.example.com/b', 'ix1', ttl=60)
        assert old not in m._download_tokens
        assert len(m._download_tokens) == 1


def test_token_store_is_capped(temp_data_dir, monkeypatch):
    from app import indexer_manager as im
    monkeypatch.setattr(im, 'MAX_DOWNLOAD_TOKENS', 5)
    m = im.IndexerManager(_settings({'indexer.indexers': [DIRECT]}))
    toks = [m.issue_download_token(f'https://tracker.example.com/{i}', 'ix1', persist=False) for i in range(8)]
    assert len(m._download_tokens) == 5
    assert m.resolve_download_token(toks[0]) is None
    assert m.resolve_download_token(toks[-1]) is not None


def test_tokens_persist_in_data_dir(temp_data_dir):
    from app.indexer_manager import IndexerManager
    sm = _settings({'indexer.indexers': [DIRECT]})
    tok = IndexerManager(sm).issue_download_token('https://tracker.example.com/a', 'ix1')
    assert (temp_data_dir / 'download_tokens.json').exists()
    assert IndexerManager(sm).resolve_download_token(tok) == ('https://tracker.example.com/a', 'ix1', False)


# ---- 2. Download route ----

@pytest.fixture
def route_env():
    from app import main
    from app.indexer_backend import LumaBackendAdapter
    from app.indexer_manager import IndexerManager
    sm = _settings({'indexer.indexers': [DIRECT, PROXIED, NZB],
                    'indexer.base_url': 'http://localhost:9696',
                    'indexer.api_key': 'pk',
                    'qbittorrent.url': 'http://qbit.test:8080',
                    'qbittorrent.username': 'u', 'qbittorrent.password': 'p'})
    adapter = LumaBackendAdapter(IndexerManager(sm))
    main.app.config['TESTING'] = True
    stream = Mock()
    stream.status_code = 200
    stream.headers = {'Content-Type': 'application/x-bittorrent'}
    stream.iter_content.side_effect = lambda chunk_size=8192: iter([b'd8:announce'])
    stream.raise_for_status = Mock()
    with patch('app.indexer_api.get_indexer_client', return_value=adapter), \
         patch.object(main, '_token_store', return_value=adapter.manager), \
         patch.object(main, 'settings', sm), \
         patch('app.indexer_manager.pinned_get', return_value=stream) as pg, \
         patch('app.indexer_manager.validate_indexer_url', return_value=(True, '', '203.0.113.9')) as val:
        yield main, adapter, pg, val
        stream.status_code = 200


def _as_user(main, user):
    return patch.object(main, '_get_current_user', return_value=user)


def test_download_anonymous_refused(route_env):
    main, adapter, pg, _ = route_env
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, None):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.status_code == 401
    pg.assert_not_called()


def test_download_open_to_non_admin_user(route_env):
    main, adapter, pg, _ = route_env
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, {'id': 2, 'username': 'kid', 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.status_code == 200
    assert r.data == b'd8:announce'
    args, kwargs = pg.call_args
    assert args == ('https://tracker.example.com/dl/1', '203.0.113.9')
    assert kwargs['headers'] == {}  # the stored URL carried no key, so none is sent


def test_download_bad_token_refused(route_env):
    main, *_ = route_env
    with _as_user(main, {'id': 2, 'is_admin': False}):
        assert main.app.test_client().get('/api/indexer/download/nope').status_code == 403


@pytest.mark.parametrize('url', ['https://evil.example.com/dl/1',
                                 'https://tracker.example.com:8443/dl/1',
                                 'http://localhost:9696/7/download'])
def test_direct_entry_host_checked_against_indexer_url(route_env, url):
    main, adapter, pg, _ = route_env
    tok = adapter.manager.issue_download_token(url, 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        assert main.app.test_client().get(f'/api/indexer/download/{tok}').status_code == 403
    pg.assert_not_called()


def test_proxied_entry_host_checked_against_prowlarr_url(route_env):
    main, adapter, pg, val = route_env
    bad = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix2')
    good = adapter.manager.issue_download_token('http://LOCALHOST:9696/7/download?link=x', 'ix2')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        c = main.app.test_client()
        assert c.get(f'/api/indexer/download/{bad}').status_code == 403
        assert c.get(f'/api/indexer/download/{good}').status_code == 200
    assert val.call_args.kwargs['allow_loopback_for_host'] == 'localhost'
    assert pg.call_args.kwargs['headers'] == {}
    keyed = adapter.manager.issue_download_token('http://localhost:9696/7/download?apikey=old', 'ix2')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        assert main.app.test_client().get(f'/api/indexer/download/{keyed}').status_code == 200
    assert pg.call_args.kwargs['headers'] == {'X-Api-Key': 'pk'}
    assert pg.call_args.args[0] == 'http://localhost:9696/7/download?apikey=pk'


# ---- 3. DNS pinning ----

class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'host': self.headers.get('Host')}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_server():
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _no_dns_for(name):
    real = socket.getaddrinfo

    def fake(host, *a, **k):
        if host == name:
            raise AssertionError(f'DNS lookup for {name} at connect time')
        return real(host, *a, **k)
    return patch('socket.getaddrinfo', side_effect=fake)


def test_pinned_http_connects_to_ip_with_original_host(local_server):
    from app.indexer_manager import pinned_get
    port = local_server.server_address[1]
    with _no_dns_for('indexer.invalid'):
        r = pinned_get(f'http://indexer.invalid:{port}/api', '127.0.0.1', timeout=5)
    assert r.status_code == 200
    assert r.json()['host'] == f'indexer.invalid:{port}'


def test_pinned_adapter_refuses_other_hosts():
    from app.indexer_manager import PinnedIPHTTPAdapter
    s = requests.Session()
    s.mount('http://', PinnedIPHTTPAdapter('127.0.0.1', 'indexer.invalid', 'http'))
    with pytest.raises(requests.exceptions.ConnectionError):
        s.get('http://other.invalid/', timeout=2)


@pytest.fixture
def tls_server(tmp_path):
    openssl = shutil.which('openssl')
    assert openssl, 'openssl is required for the TLS pinning test'
    key, cert = tmp_path / 'k.pem', tmp_path / 'c.pem'
    subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=indexer.test', '-addext', 'subjectAltName=DNS:indexer.test',
                    '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    seen_sni = []
    ctx.sni_callback = lambda sock, name, c: seen_sni.append(name)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, cert, seen_sni
    srv.shutdown()
    srv.server_close()


def test_pinned_https_uses_sni_and_verifies_hostname(tls_server):
    from app.indexer_manager import pinned_get
    srv, cert, seen_sni = tls_server
    port = srv.server_address[1]
    with _no_dns_for('indexer.test'):
        r = pinned_get(f'https://indexer.test:{port}/api', '127.0.0.1', verify=str(cert), timeout=5)
    assert r.status_code == 200
    assert r.json()['host'] == f'indexer.test:{port}'
    assert seen_sni[-1] == 'indexer.test'
    # The certificate is for indexer.test; another name pinned to the same IP must fail.
    with _no_dns_for('other.test'):
        with pytest.raises(requests.exceptions.SSLError):
            pinned_get(f'https://other.test:{port}/api', '127.0.0.1', verify=str(cert), timeout=5)


@pytest.mark.no_ssrf_mock
def test_rebinding_to_private_ip_rejected():
    from app.indexer_manager import validate_indexer_url
    answers = [(2, 1, 6, '', ('8.8.8.8', 0)), (2, 1, 6, '', ('10.0.0.5', 0))]
    with patch('socket.getaddrinfo', return_value=answers):
        ok, err, ip = validate_indexer_url('https://tracker.example.com', allow_lan=False)
    assert not ok and ip is None and '10.0.0.5' in err


@pytest.mark.no_ssrf_mock
def test_loopback_only_for_configured_prowlarr_host():
    from app.indexer_manager import validate_indexer_url
    lo = [(2, 1, 6, '', ('127.0.0.1', 0))]
    with patch('socket.getaddrinfo', return_value=lo):
        assert validate_indexer_url('http://prowlarr.lan:9696/7/api', allow_lan=False,
                                    allow_loopback_for_host='prowlarr.lan') == (True, '', '127.0.0.1')
        ok, err, _ = validate_indexer_url('http://evil.example.com/', allow_lan=True,
                                          allow_loopback_for_host='prowlarr.lan')
        assert not ok and 'oopback' in err
        ok, _, _ = validate_indexer_url('http://evil.example.com/', allow_lan=True)
        assert not ok


@pytest.mark.no_ssrf_mock
def test_search_pins_validated_ip_for_proxied_prowlarr():
    from app.indexer_manager import IndexerManager
    sm = _settings({'indexer.indexers': [dict(PROXIED, rate_limited=False)],
                    'indexer.base_url': 'http://prowlarr.lan:9696', 'indexer.api_key': 'pk'})
    with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 0))]), \
         patch('app.indexer_manager.pinned_get', return_value=_ok_xml_resp()) as pg:
        out = IndexerManager(sm).search(query='q', categories=[2000], limit=5)
    assert len(out) == 1
    assert pg.call_args.args == ('http://prowlarr.lan:9696/7/api', '127.0.0.1')


# ---- 4. Min-interval spacing ----

def test_concurrent_callers_are_spaced_by_min_interval():
    from app.indexer_manager import IndexerManager
    interval = 0.3
    cfg = dict(DIRECT, url='https://avistaz.example.com', rate_limited=True,
               rate_limit_capacity=100, rate_limit_refill_seconds=1,
               rate_limit_min_interval=interval)
    sm = _settings({'indexer.indexers': [cfg]})
    calls, lock = [], threading.Lock()

    def fake_get(*a, **k):
        with lock:
            calls.append(time.monotonic())
        return _ok_xml_resp()

    with patch('app.indexer_manager.pinned_get', side_effect=fake_get):
        m = IndexerManager(sm)
        barrier = threading.Barrier(4)

        def run(i):
            barrier.wait()
            m._search_one_indexer('ix1', f'q{i}', [2000], 10, interactive=False)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    calls.sort()
    assert len(calls) == 4
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    assert all(g >= interval - 0.03 for g in gaps), gaps
    assert calls[-1] - calls[0] < interval * 3 + 2.0  # generous bound for loaded machines


# ---- 5. 429 with HTTP-date through the search path ----

def test_429_http_date_end_to_end_avistaz():
    from app.indexer_backend import LumaBackendAdapter
    from app.indexer_manager import IndexerManager
    sm = _settings({'indexer.indexers': [], 'indexer.base_url': 'http://prowlarr.example.com:9696',
                    'indexer.api_key': 'pk'})
    listing = Mock()
    listing.raise_for_status = Mock()
    listing.json.return_value = [{'id': 7, 'name': 'AvistaZ', 'implementation': 'Cardigann',
                                  'protocol': 'torrent', 'enable': True, 'priority': 25, 'fields': []}]
    with patch('app.indexer_manager.requests.Session') as sess:
        sess.return_value.get.return_value = listing
        m = IndexerManager(sm)
        assert m.import_from_prowlarr('http://prowlarr.example.com:9696', 'pk')[0] == 1
    avistaz = next(ix for ix in sm.store['indexer.indexers'] if ix['name'].startswith('AvistaZ'))
    assert avistaz['rate_limited'] is True

    resp = Mock()
    resp.status_code = 429
    resp.headers = {'Retry-After': formatdate(
        (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp(), usegmt=True)}
    with patch('app.indexer_manager.pinned_get', return_value=resp) as pg:
        results = LumaBackendAdapter(m).search_all('Some Movie', 'movie', 2020)
    assert pg.called
    assert results == []
    state = m._get_state(avistaz['id'])
    assert state.is_backed_off(time.time())
    assert 295 <= state.backoff_seconds <= 305


# ---- 6. Test hygiene ----

def test_requirements_pin_aiohttp():
    lines = (ROOT / 'requirements.txt').read_text().splitlines()
    assert 'aiohttp>=3.11,<4' in [l.strip() for l in lines]


def test_state_files_go_to_per_test_data_dir(temp_data_dir):
    from app import indexer_manager as im
    assert im.data_dir() == temp_data_dir
    m = im.IndexerManager(_settings({'indexer.indexers': [DIRECT]}))
    assert m._get_state_file_path().parent == temp_data_dir
    m.issue_download_token('https://tracker.example.com/a', 'ix1')
    assert (temp_data_dir / 'download_tokens.json').exists()


def test_indexer_code_has_one_data_dir_helper():
    src = (ROOT / 'app' / 'indexer_manager.py').read_text()
    assert src.count("os.environ.get('LUMA_DATA_DIR'") == 1


def test_indexer_tests_do_not_hardcode_app_or_workspace_paths():
    for name in ('test_indexer_manager.py', 'test_indexer_routes.py'):
        text = (ROOT / 'tests' / name).read_text()
        assert '/app/data' not in text and '/workspace' not in text, name


# ---- 7. Removed names ----

def test_no_get_indexer_manager_anywhere():
    hits = [str(p) for d in ('app', 'tests') for p in (ROOT / d).rglob('*.py')
            if p.name != 'test_indexer_review.py' and 'get_indexer_' + 'manager' in p.read_text()]
    assert hits == []


def test_no_zinc_classes_in_indexer_ui():
    hits = [str(p) for p in (ROOT / 'frontend' / 'src' / 'pages' / 'settings').rglob('*.tsx')
            if 'zinc-' in p.read_text()]
    assert hits == []


# ---- Round 2, item 1: SSRF address policy ----

SSRF_CASES = [
    # (address, allow_lan, allowed)
    ('::', False, False), ('::', True, False),
    ('0.0.0.0', False, False), ('0.0.0.0', True, False), ('0.1.2.3', True, False),
    ('127.0.0.1', True, False), ('::1', True, False), ('::ffff:127.0.0.1', True, False),
    ('64:ff9b::7f00:1', False, False), ('64:ff9b::7f00:1', True, False),
    ('64:ff9b::a00:1', False, False), ('64:ff9b::a00:1', True, True),
    ('64:ff9b::808:808', False, True),
    ('2002:7f00:1::', True, False), ('2002:c0a8:101::', False, False), ('2002:808:808::', False, True),
    ('::ffff:192.168.1.1', False, False), ('::ffff:192.168.1.1', True, True), ('::ffff:8.8.8.8', False, True),
    ('198.18.0.1', False, False), ('198.18.0.1', True, False), ('198.19.255.1', True, False),
    ('224.0.0.1', True, False), ('239.255.255.250', True, False), ('ff02::1', True, False),
    ('240.0.0.1', True, False), ('255.255.255.255', True, False),
    ('169.254.169.254', True, False), ('169.254.170.2', True, False), ('169.254.1.1', True, False),
    ('fe80::1', True, False), ('fd00:ec2::254', True, False),
    ('192.0.2.1', True, False), ('203.0.113.5', False, False), ('192.0.0.8', True, False),
    ('10.1.2.3', False, False), ('10.1.2.3', True, True),
    ('172.16.0.1', True, True), ('192.168.1.10', True, True),
    ('100.64.0.1', False, False), ('100.64.0.1', True, True),
    ('fd12:3456::1', False, False), ('fd12:3456::1', True, True),
    ('8.8.8.8', False, True), ('2606:4700:4700::1111', False, True),
    ('100.100.100.200', False, False), ('100.100.100.200', True, False),
    ('192.88.99.1', False, False), ('192.88.99.1', True, False), ('2002:c058:6301::', False, False),
    ('::ffff:192.88.99.1', True, False),
]


@pytest.mark.no_ssrf_mock
@pytest.mark.parametrize('addr,allow_lan,allowed', SSRF_CASES)
def test_ssrf_address_policy(addr, allow_lan, allowed):
    from app.indexer_manager import validate_indexer_url
    fam = socket.AF_INET6 if ':' in addr else socket.AF_INET
    sockaddr = (addr, 0, 0, 0) if fam == socket.AF_INET6 else (addr, 0)
    with patch('socket.getaddrinfo', return_value=[(fam, 1, 6, '', sockaddr)]):
        ok, err, ip = validate_indexer_url('http://indexer.example.com:9696/', allow_lan=allow_lan)
    assert ok is allowed, (addr, allow_lan, err)
    assert (ip is not None) is allowed


@pytest.mark.no_ssrf_mock
@pytest.mark.parametrize('url,allow_lan', [('http://[::]:9696/', False), ('http://[::]:9696/', True),
                                           ('http://0.0.0.0:9696/', True), ('http://[::1]:9696/', True),
                                           ('http://[64:ff9b::7f00:1]/', True)])
def test_ssrf_literal_addresses_rejected_without_mocks(url, allow_lan):
    from app.indexer_manager import validate_indexer_url
    ok, _, ip = validate_indexer_url(url, allow_lan=allow_lan)
    assert not ok and ip is None


@pytest.mark.no_ssrf_mock
def test_prowlarr_loopback_exception_is_narrow():
    from app.indexer_manager import validate_indexer_url
    v = validate_indexer_url
    assert v('http://localhost:9696/', allow_loopback_for_host='localhost')[0] is True
    assert v('http://127.0.0.1:9696/', allow_loopback_for_host='127.0.0.1')[0] is True
    assert v('http://[::]:9696/', allow_loopback_for_host='::')[0] is False
    assert v('http://0.0.0.0:9696/', allow_lan=True, allow_loopback_for_host='0.0.0.0')[0] is False
    assert v('http://127.0.0.1:9696/', allow_lan=True, allow_loopback_for_host='localhost')[0] is False


# ---- Round 2, item 3: no API keys on disk ----

def test_token_file_has_no_api_keys_and_is_private(temp_data_dir):
    from app.indexer_manager import IndexerManager
    m = IndexerManager(_settings({'indexer.indexers': [DIRECT]}))
    m.issue_download_token('https://tracker.example.com/dl?id=1&apikey=SECRETKEY&x=2', 'ix1')
    m.issue_download_token('https://tracker.example.com/dl?API_KEY=SECRETKEY', 'ix1')
    f = temp_data_dir / 'download_tokens.json'
    assert 'SECRETKEY' not in f.read_text()
    assert (f.stat().st_mode & 0o777) == 0o600


def test_legacy_token_file_is_scrubbed_on_load(temp_data_dir):
    from app.indexer_manager import IndexerManager
    f = temp_data_dir / 'download_tokens.json'
    f.write_text(json.dumps({'tok': ['https://tracker.example.com/dl?id=1&apikey=OLDKEY', 'ix1', time.time() + 600]}))
    m = IndexerManager(_settings({'indexer.indexers': [DIRECT]}))
    assert m.resolve_download_token('tok') == ('https://tracker.example.com/dl?id=1', 'ix1', True)
    assert 'OLDKEY' not in f.read_text()


def test_download_readds_current_key_from_settings(route_env):
    main, adapter, pg, _ = route_env
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl?id=1&apikey=STALE', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        assert main.app.test_client().get(f'/api/indexer/download/{tok}').status_code == 200
    url = pg.call_args.args[0]
    assert 'STALE' not in url and url.endswith('apikey=k')
    assert pg.call_args.kwargs['headers'] == {'X-Api-Key': 'k'}
    plain = adapter.manager.issue_download_token('https://tracker.example.com/dl?id=2', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        main.app.test_client().get(f'/api/indexer/download/{plain}')
    assert pg.call_args.args[0] == 'https://tracker.example.com/dl?id=2'
    assert pg.call_args.kwargs['headers'] == {}


# ---- Round 2, item 4: concurrent saves ----

def test_concurrent_state_saves_no_warnings_no_lost_counters(temp_data_dir, caplog):
    import logging
    from app.indexer_manager import IndexerManager
    cfgs = [dict(DIRECT, id=f'ix{i}', name=f'I{i}') for i in range(4)]
    m = IndexerManager(_settings({'indexer.indexers': cfgs}))
    m._save_throttle_seconds = 0
    per_thread = 50
    caplog.set_level(logging.WARNING, logger='app.indexer_manager')

    def work(i):
        st = m._get_state(f'ix{i}')
        for _ in range(per_thread):
            with st.lock:
                st.background_request_log.append(time.time())
            m._save_state()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    m._last_save_time = 0
    m._save_state()
    assert not [r for r in caplog.records if 'Failed to save' in r.getMessage()]
    assert not list(temp_data_dir.glob('*.tmp'))
    f = temp_data_dir / 'indexer_state.json'
    assert (f.stat().st_mode & 0o777) == 0o600
    saved = json.loads(f.read_text())['states']
    assert all(len(saved[f'ix{i}']['background_request_log']) == per_thread for i in range(4))


# ---- Round 2, item 5: download response hardening ----

@pytest.mark.parametrize('status', [301, 302, 204, 404, 500])
def test_download_requires_200(route_env, status):
    main, adapter, pg, _ = route_env
    pg.return_value.status_code = status
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.status_code == 502
    pg.return_value.close.assert_called()


def test_download_headers_forced_and_sanitized(route_env):
    main, adapter, pg, _ = route_env
    pg.return_value.headers = {'Content-Type': 'text/html',
                               'Content-Disposition': 'attachment; filename="../../evil\r\n<script>.html"'}
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.status_code == 200
    assert r.headers['Content-Type'] == 'application/x-bittorrent'
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    cd = r.headers['Content-Disposition']
    name = cd.split('filename="', 1)[1].rstrip('"')
    assert name.endswith('.torrent')
    assert '/' not in name and '\\' not in name and '<' not in name and '..' not in name


def test_download_size_cap_while_streaming(route_env, monkeypatch):
    """A body that grows past the cap without a Content-Length is a 502, not a truncated 200."""
    from app import indexer_manager as im
    main, adapter, pg, _ = route_env
    monkeypatch.setattr(im, 'MAX_RESPONSE_SIZE', 20000)
    pulled = []

    def chunks(chunk_size=8192):
        for _ in range(100):
            pulled.append(1)
            yield b'd' + b'x' * 8191
    pg.return_value.iter_content.side_effect = chunks
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.status_code == 502
    assert len(pulled) == 3  # stops reading right after crossing the cap
    pg.return_value.close.assert_called()


def test_download_at_cap_is_served_with_length(route_env, monkeypatch):
    from app import indexer_manager as im
    main, adapter, pg, _ = route_env
    monkeypatch.setattr(im, 'MAX_RESPONSE_SIZE', 20000)
    body = b'd' + b'x' * 19999
    pg.return_value.iter_content.side_effect = lambda chunk_size=8192: iter([body[:8192], body[8192:]])
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.status_code == 200
    assert r.data == body
    assert r.headers['Content-Length'] == '20000'
    pg.return_value.close.assert_called()


@pytest.mark.parametrize('payload', [b'<html>login</html>', b'', b'{"error":1}'])
def test_download_torrent_must_be_bencoded(route_env, payload):
    main, adapter, pg, _ = route_env
    pg.return_value.iter_content.side_effect = lambda chunk_size=8192: iter([payload] if payload else [])
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.status_code == 502
    pg.return_value.close.assert_called()


def test_download_nzb_skips_bencode_check(route_env):
    """A newznab indexer serves NZB XML (BOM and leading whitespace allowed)."""
    main, adapter, pg, _ = route_env
    pg.return_value.headers = {'Content-Type': 'text/plain'}
    pg.return_value.iter_content.side_effect = lambda chunk_size=8192: iter(
        [b'\xef\xbb\xbf \n<?xml version="1.0"?><nzb/>'])
    tok = adapter.manager.issue_download_token('https://nzb.example.com/get/1', 'ix3')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.status_code == 200
    assert r.headers['Content-Type'] == 'application/x-nzb'
    assert r.headers['Content-Disposition'] == 'attachment; filename="download.nzb"'


def test_download_nzb_bare_root_and_stored_protocol(route_env):
    main, adapter, pg, _ = route_env
    main.settings.store['indexer.indexers'].append(dict(DIRECT, id='ix4', type='torznab', protocol='usenet'))
    pg.return_value.iter_content.side_effect = lambda chunk_size=8192: iter([b'<nzb xmlns="x"/>'])
    tok = adapter.manager.issue_download_token('https://tracker.example.com/get/1', 'ix4')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.status_code == 200 and r.headers['Content-Type'] == 'application/x-nzb'


@pytest.mark.parametrize('payload', [b'<html><body>login</body></html>', b'd8:announce'])
def test_newznab_body_must_be_nzb(route_env, payload):
    main, adapter, pg, _ = route_env
    pg.return_value.iter_content.side_effect = lambda chunk_size=8192: iter([payload])
    tok = adapter.manager.issue_download_token('https://nzb.example.com/get/1', 'ix3')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        assert main.app.test_client().get(f'/api/indexer/download/{tok}').status_code == 502


@pytest.mark.parametrize('url', ['https://tracker.example.com/get/1', 'https://tracker.example.com/x.nzb'])
def test_nzb_cannot_be_spoofed_by_content_type_or_url(route_env, url):
    """A torznab indexer claiming NZB (header or .nzb URL) still has to serve a torrent."""
    main, adapter, pg, _ = route_env
    pg.return_value.headers = {'Content-Type': 'application/x-nzb'}
    pg.return_value.iter_content.side_effect = lambda chunk_size=8192: iter([b'<?xml version="1.0"?><html/>'])
    tok = adapter.manager.issue_download_token(url, 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        assert main.app.test_client().get(f'/api/indexer/download/{tok}').status_code == 502


@pytest.mark.parametrize('url', ['http://tracker.example.com:443/dl/1', 'http://tracker.example.com/dl/1'])
def test_download_scheme_must_match_indexer(route_env, url):
    main, adapter, pg, _ = route_env
    tok = adapter.manager.issue_download_token(url, 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        assert main.app.test_client().get(f'/api/indexer/download/{tok}').status_code == 403
    pg.assert_not_called()


def test_url_host_port_includes_scheme():
    from app.main import _url_host_port
    assert _url_host_port('http://h:443/') == ('http', 'h', 443)
    assert _url_host_port('HTTPS://H/') == ('https', 'h', 443)
    assert _url_host_port('http://h:443/') != _url_host_port('https://h/')


def test_load_prunes_expired_before_persisting(temp_data_dir):
    from app.indexer_manager import IndexerManager
    f = temp_data_dir / 'download_tokens.json'
    now = time.time()
    f.write_text(json.dumps({'old': ['https://tracker.example.com/a', 'ix1', now - 10, False],
                             'live': ['https://tracker.example.com/b', 'ix1', now + 600, False]}))
    m = IndexerManager(_settings({'indexer.indexers': [DIRECT]}))
    assert set(json.loads(f.read_text())) == {'live'}
    assert set(m._download_tokens) == {'live'}


def test_eviction_drops_expired_first_then_soonest_expiring(temp_data_dir, monkeypatch):
    from app import indexer_manager as im
    monkeypatch.setattr(im, 'MAX_DOWNLOAD_TOKENS', 3)
    m = im.IndexerManager(_settings({'indexer.indexers': [DIRECT]}))
    now = time.time()
    m._download_tokens = {'expired': ('u', 'ix1', now - 1, False),
                          'soon': ('u', 'ix1', now + 10, False),
                          'mid': ('u', 'ix1', now + 100, False),
                          'late': ('u', 'ix1', now + 1000, False)}
    with patch('app.indexer_manager.heapq.nsmallest', wraps=im.heapq.nsmallest) as ns:
        newest = m.issue_download_token('https://tracker.example.com/z', 'ix1', persist=False)
    assert set(m._download_tokens) == {'mid', 'late', newest}
    assert ns.call_args.args[0] == 1  # only the one over the cap is selected
    m.issue_download_token('https://tracker.example.com/y', 'ix1', persist=False)  # at cap: one evicted
    assert len(m._download_tokens) == 3


def test_download_declared_oversize_rejected(route_env, monkeypatch):
    from app import indexer_manager as im
    main, adapter, pg, _ = route_env
    monkeypatch.setattr(im, 'MAX_RESPONSE_SIZE', 1000)
    pg.return_value.headers = {'Content-Length': '5000'}
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        assert main.app.test_client().get(f'/api/indexer/download/{tok}').status_code == 502


def test_download_closes_upstream_on_close(route_env):
    main, adapter, pg, _ = route_env
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
        r.get_data()
        r.close()
    pg.return_value.close.assert_called()


# ---- Round 2, item 6: pinned_get closes its session ----

def _count_session_closes(monkeypatch):
    closes = []
    real = requests.Session.close
    monkeypatch.setattr(requests.Session, 'close', lambda self: (closes.append(1), real(self))[1])
    return closes


def test_pinned_get_closes_session_without_stream(local_server, monkeypatch):
    from app.indexer_manager import pinned_get
    closes = _count_session_closes(monkeypatch)
    r = pinned_get(f'http://indexer.invalid:{local_server.server_address[1]}/', '127.0.0.1', timeout=5)
    assert r.status_code == 200 and closes == [1]


def test_pinned_get_closes_session_with_response(local_server, monkeypatch):
    from app.indexer_manager import pinned_get
    closes = _count_session_closes(monkeypatch)
    r = pinned_get(f'http://indexer.invalid:{local_server.server_address[1]}/', '127.0.0.1',
                   timeout=5, stream=True)
    assert closes == []
    r.close()
    assert closes == [1]


def test_pinned_get_closes_session_on_error(monkeypatch):
    from app.indexer_manager import pinned_get
    closes = _count_session_closes(monkeypatch)
    with pytest.raises(requests.exceptions.ConnectionError):
        pinned_get('http://indexer.invalid:1/', '127.0.0.1', timeout=2)
    assert closes == [1]


# ---- Round 4, item 1: magnets and redirects ----

MAGNET = 'magnet:?xt=urn:btih:' + 'ab' * 20 + '&dn=Some.Movie'
TORRENT = b'd8:announce3:url4:infod4:name1:xee'


def _resp(status=200, headers=None, body=b''):
    r = Mock()
    r.status_code = status
    r.headers = headers or {}
    r.iter_content.side_effect = lambda chunk_size=8192: iter([body] if body else [])
    return r


def _get(main, tok):
    with _as_user(main, {'id': 2, 'is_admin': False}):
        return main.app.test_client().get(f'/api/indexer/download/{tok}')


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_magnet_location_is_returned_not_fetched(route_env, status):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(status, {'Location': MAGNET})]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.status_code == 200 and r.get_json() == {'magnet': MAGNET}
    assert pg.call_count == 1


def test_magnet_body_is_returned(route_env):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(200, {'Content-Type': 'text/plain'}, ('  ' + MAGNET + '\n').encode())]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.get_json() == {'magnet': MAGNET}


def test_redirect_to_other_host_revalidates_and_drops_key(route_env):
    main, adapter, pg, val = route_env
    first = _resp(302, {'Location': 'https://cdn.example.net/files/1.torrent'})
    pg.side_effect = [first, _resp(200, {}, TORRENT)]
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl?id=1&apikey=x', 'ix1')
    r = _get(main, tok)
    assert r.status_code == 200 and r.data == TORRENT
    assert [c.args[0] for c in val.call_args_list] == ['https://tracker.example.com/dl?id=1',
                                                       'https://cdn.example.net/files/1.torrent']
    (u1, ip1), k1 = pg.call_args_list[0].args, pg.call_args_list[0].kwargs
    (u2, ip2), k2 = pg.call_args_list[1].args, pg.call_args_list[1].kwargs
    assert u1.endswith('apikey=k') and k1['headers'] == {'X-Api-Key': 'k'}
    assert u2 == 'https://cdn.example.net/files/1.torrent' and k2['headers'] == {}
    first.close.assert_called()


def test_redirect_same_origin_keeps_key_scheme_change_drops_it(route_env):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(302, {'Location': '/dl/2'}),
                      _resp(302, {'Location': 'http://tracker.example.com/dl/3'}),
                      _resp(200, {}, TORRENT)]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1?apikey=x', 'ix1'))
    assert r.status_code == 200
    calls = pg.call_args_list
    assert calls[1].args[0] == 'https://tracker.example.com/dl/2?apikey=k'
    assert calls[1].kwargs['headers'] == {'X-Api-Key': 'k'}
    assert calls[2].args[0] == 'http://tracker.example.com/dl/3' and calls[2].kwargs['headers'] == {}


def test_redirects_capped_at_three_hops(route_env):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(302, {'Location': f'https://tracker.example.com/r{i}'}) for i in range(5)]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.status_code == 502 and 'too many' in r.get_json()['error']
    assert pg.call_count == 4


def test_three_redirects_then_file_is_allowed(route_env):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(302, {'Location': f'https://tracker.example.com/r{i}'}) for i in range(3)] + \
        [_resp(200, {}, TORRENT)]
    assert _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')).status_code == 200


def test_redirect_hop_failing_validation_is_refused(route_env):
    main, adapter, pg, val = route_env
    val.side_effect = [(True, '', '203.0.113.9'), (False, 'Private/restricted IP blocked: 10.0.0.1', None)]
    pg.side_effect = [_resp(302, {'Location': 'http://internal.example.com/'})]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.status_code == 403 and pg.call_count == 1


@pytest.mark.parametrize('loc', ['ftp://tracker.example.com/x', 'file:///etc/passwd', ''])
def test_redirect_to_unsupported_or_missing_location(route_env, loc):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(302, {'Location': loc} if loc else {})]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.status_code == 502 and pg.call_count == 1


# ---- Round 4, item 2: grab to library through /api/downloads/add ----

def _no_auto_rename():
    """Stand in for app.auto_rename, whose import creates directories under /app."""
    import sys
    return patch.dict(sys.modules, {'app.auto_rename': Mock(auto_renamer=Mock())})


class _FakeQbit:
    def __init__(self, hash_after_add):
        self.added = []
        self.hash_after_add = hash_after_add

    def post(self, url, data=None, files=None, timeout=None):
        r = Mock(status_code=200, text='Ok.')
        if url.endswith('/torrents/add'):
            self.added.append({'data': data, 'files': files})
        return r

    def get(self, url, params=None, timeout=None):
        if not url.startswith('http://qbit.test:8080/'):
            raise AssertionError(f'unexpected fetch {url}')
        items = [{'hash': self.hash_after_add, 'added_on': 1}] if self.added else []
        return Mock(status_code=200, json=Mock(return_value=items))


@pytest.mark.parametrize('prefix', ['', '/luma'])
def test_grab_to_library_with_proxy_torrent(route_env, prefix):
    import hashlib
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(200, {}, TORRENT)]
    info_hash = hashlib.sha1(b'd4:name1:xe').hexdigest()
    qbit = _FakeQbit(info_hash)
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with patch('requests.Session', return_value=qbit), patch('time.sleep'), _no_auto_rename(), \
         patch.object(main, '_record_origin'), _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().post('/api/downloads/add',
                                        json={'torrent_url': f'{prefix}/api/indexer/download/{tok}'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['hash'] == info_hash
    assert qbit.added[0]['files']['torrents'][1] == TORRENT
    assert pg.call_count == 1


def test_grab_to_library_with_proxy_magnet(route_env):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(302, {'Location': MAGNET})]
    qbit = _FakeQbit('ab' * 20)
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with patch('requests.Session', return_value=qbit), patch('time.sleep'), _no_auto_rename(), \
         patch.object(main, '_record_origin'), _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().post('/api/downloads/add',
                                        json={'torrent_url': f'/api/indexer/download/{tok}'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['hash'] == 'ab' * 20
    assert qbit.added[0]['data']['urls'] == MAGNET and qbit.added[0]['files'] is None


def test_grab_to_library_proxy_errors_surface(route_env):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(200, {}, b'<html>expired</html>')]
    qbit = _FakeQbit('00' * 20)
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with patch('requests.Session', return_value=qbit), patch('time.sleep'), _no_auto_rename(), \
         _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().post('/api/downloads/add',
                                        json={'torrent_url': f'/api/indexer/download/{tok}'})
    assert r.status_code == 502 and qbit.added == []


@pytest.mark.parametrize('value,token', [
    ('/api/indexer/download/abc_-1', 'abc_-1'),
    ('/luma/api/indexer/download/abc', 'abc'),
    ('https://evil.example.com/api/indexer/download/abc', None),
    ('/api/indexer/download/abc?x=1', None),
    ('api/indexer/download/abc', None),
    ('magnet:?xt=urn:btih:abc', None),
])
def test_indexer_download_token_detection(value, token):
    from app.main import _indexer_download_token
    assert _indexer_download_token(value) == token


def test_prowlarr_import_stores_protocol():
    from app.indexer_manager import IndexerManager
    sm = _settings({'indexer.indexers': []})
    listing = Mock()
    listing.raise_for_status = Mock()
    listing.json.return_value = [
        {'id': 1, 'name': 'Geek', 'implementation': 'Newznab', 'protocol': 'usenet', 'enable': True,
         'fields': [{'name': 'baseUrl', 'value': 'https://api.nzbgeek.info'}, {'name': 'apiKey', 'value': 'k'}]},
        {'id': 2, 'name': 'Cardi', 'implementation': 'Cardigann', 'protocol': 'torrent', 'enable': True, 'fields': []},
    ]
    with patch('app.indexer_manager.requests.Session') as sess:
        sess.return_value.get.return_value = listing
        IndexerManager(sm).import_from_prowlarr('http://prowlarr.example.com:9696', 'pk')
    protos = {ix['name']: ix['protocol'] for ix in sm.store['indexer.indexers']}
    assert protos == {'Geek': 'usenet', 'Cardi (via Prowlarr)': 'torrent'}


# ---- Pre-rebase items ----

from app.indexer_manager import validate_indexer_url as _REAL_VALIDATE  # noqa: E402

# Each test gets a private LUMA_DATA_DIR (see conftest.temp_data_dir).
pytestmark = pytest.mark.usefixtures('temp_data_dir')



def test_downloads_add_requires_auth(route_env):
    main, adapter, pg, _ = route_env
    qbit = _FakeQbit('00' * 20)
    with patch('requests.Session', return_value=qbit), _no_auto_rename(), _as_user(main, None):
        r = main.app.test_client().post('/api/downloads/add', json={'torrent_url': MAGNET})
    assert r.status_code == 401
    assert qbit.added == [] and pg.call_count == 0


@pytest.mark.parametrize('bad', [
    'magnet:?xt=urn:btih:' + 'a' * 40 + '&tr=' + 'x' * 9000,
    'magnet:?xt=urn:btih:' + 'a' * 40 + '\x00&dn=x',
    'magnet:?xt=urn:btih:' + 'a' * 40 + '&dn=a\x1bb',
    'magnet:?dn=no-topic',
    'magnet:?xt=urn:sha1:' + 'a' * 32,
])
def test_bad_magnet_from_redirect_rejected(route_env, bad):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(302, {'Location': bad})]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.status_code == 502


@pytest.mark.parametrize('bad', [
    b'magnet:?xt=urn:btih:' + b'a' * 40 + b'&tr=' + b'x' * 9000,
    b'magnet:?xt=urn:btih:' + b'a' * 40 + b'\x07',
    b'magnet:?dn=no-topic\n',
])
def test_bad_magnet_from_body_rejected(route_env, bad):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(200, {}, bad)]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.status_code == 502


@pytest.mark.parametrize('good', [MAGNET, 'magnet:?dn=x&xt=urn:btmh:1220' + 'ab' * 32])
def test_good_magnets_accepted(route_env, good):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(302, {'Location': good + '  '})]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.status_code == 200 and r.get_json() == {'magnet': good}


def test_user_supplied_magnet_validated_in_downloads_add(route_env):
    main, adapter, pg, _ = route_env
    qbit = _FakeQbit('00' * 20)
    with patch('requests.Session', return_value=qbit), _no_auto_rename(), \
         _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().post('/api/downloads/add', json={'torrent_url': 'magnet:?dn=x\x00'})
    assert r.status_code == 400 and qbit.added == []


def test_redirect_to_other_loopback_port_blocked(route_env):
    """Only the exact configured Prowlarr origin may be loopback, even on redirect hops."""
    main, adapter, pg, val = route_env
    val.side_effect = _REAL_VALIDATE
    pg.side_effect = [_resp(302, {'Location': 'http://localhost:9697/admin'})]
    r = _get(main, adapter.manager.issue_download_token('http://localhost:9696/7/download', 'ix2'))
    assert r.status_code == 403 and pg.call_count == 1
    assert val.call_args_list[1].kwargs['allow_loopback_for_host'] is None


def test_redirect_within_prowlarr_origin_allowed(route_env):
    main, adapter, pg, val = route_env
    val.side_effect = _REAL_VALIDATE
    pg.side_effect = [_resp(302, {'Location': '/7/download?file=x'}), _resp(200, {}, TORRENT)]
    r = _get(main, adapter.manager.issue_download_token('http://localhost:9696/7/download', 'ix2'))
    assert r.status_code == 200 and pg.call_count == 2


@pytest.mark.parametrize('exc', [requests.exceptions.ConnectionError('refused'),
                                 requests.exceptions.ReadTimeout('slow'),
                                 requests.exceptions.SSLError('bad cert')])
def test_network_errors_are_502(route_env, exc):
    main, adapter, pg, _ = route_env
    pg.side_effect = exc
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.status_code == 502 and r.get_json() == {'error': 'Could not reach indexer'}


def test_network_error_mid_body_is_502(route_env):
    main, adapter, pg, _ = route_env
    broken = _resp(200, {})

    def boom(chunk_size=8192):
        yield b'd'
        raise requests.exceptions.ChunkedEncodingError('reset')
    broken.iter_content.side_effect = boom
    pg.side_effect = [broken]
    r = _get(main, adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1'))
    assert r.status_code == 502
    broken.close.assert_called()


# Generic grab links (Prowlarr backend): validated, pinned, manual redirects.

def _grab(main, url):
    return main.app.test_client().post('/api/downloads/add', json={'torrent_url': url})


def test_generic_grab_link_is_validated_and_pinned(route_env):
    import hashlib
    main, adapter, pg, val = route_env
    pg.side_effect = [_resp(200, {}, TORRENT)]
    qbit = _FakeQbit(hashlib.sha1(b'd4:name1:xe').hexdigest())
    url = 'http://localhost:9696/12/download?apikey=prowlarr-key&link=abc'
    with patch('requests.Session', return_value=qbit), patch('time.sleep'), _no_auto_rename(), \
         patch.object(main, '_record_origin'), _as_user(main, {'id': 2, 'is_admin': False}):
        r = _grab(main, url)
    assert r.status_code == 200, r.get_json()
    assert val.call_args.args[0] == url
    assert val.call_args.kwargs == {'allow_lan': True, 'allow_loopback_for_host': 'localhost'}
    assert pg.call_args.args[0] == url and pg.call_args.kwargs['headers'] == {}
    assert qbit.added[0]['files']['torrents'][1] == TORRENT


def test_generic_grab_redirect_to_magnet(route_env):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(301, {'Location': MAGNET})]
    qbit = _FakeQbit('ab' * 20)
    with patch('requests.Session', return_value=qbit), patch('time.sleep'), _no_auto_rename(), \
         patch.object(main, '_record_origin'), _as_user(main, {'id': 2, 'is_admin': False}):
        r = _grab(main, 'http://localhost:9696/12/download?link=abc')
    assert r.status_code == 200, r.get_json()
    assert qbit.added[0]['data']['urls'] == MAGNET


@pytest.mark.parametrize('url', ['http://169.254.169.254/latest/meta-data/', 'http://127.0.0.1:8080/',
                                 'http://localhost:9697/x', 'http://10.0.0.5/x.torrent'])
def test_generic_grab_blocks_internal_targets(route_env, url):
    main, adapter, pg, val = route_env
    val.side_effect = _REAL_VALIDATE
    qbit = _FakeQbit('00' * 20)
    with patch('requests.Session', return_value=qbit), _no_auto_rename(), \
         _as_user(main, {'id': 2, 'is_admin': False}):
        r = _grab(main, url)
    assert r.status_code == 403
    assert pg.call_count == 0 and qbit.added == []


def test_generic_grab_redirect_into_lan_blocked(route_env):
    main, adapter, pg, val = route_env
    val.side_effect = [(True, '', '203.0.113.9'), (False, 'Private/restricted IP blocked: 10.0.0.5', None)]
    pg.side_effect = [_resp(302, {'Location': 'http://10.0.0.5/x'})]
    qbit = _FakeQbit('00' * 20)
    with patch('requests.Session', return_value=qbit), _no_auto_rename(), \
         _as_user(main, {'id': 2, 'is_admin': False}):
        r = _grab(main, 'https://tracker.example.com/dl/1')
    assert r.status_code == 403 and pg.call_count == 1 and qbit.added == []
    assert val.call_args_list[1].kwargs['allow_loopback_for_host'] is None


def test_generic_grab_html_is_502_and_nothing_added(route_env):
    main, adapter, pg, _ = route_env
    pg.side_effect = [_resp(200, {}, b'<html>expired</html>')]
    qbit = _FakeQbit('00' * 20)
    with patch('requests.Session', return_value=qbit), _no_auto_rename(), \
         _as_user(main, {'id': 2, 'is_admin': False}):
        r = _grab(main, 'https://tracker.example.com/dl/1')
    assert r.status_code == 502 and 'invalid or expired' in r.get_json()['error'] and qbit.added == []


# ---- Round 6, item 1: Prowlarr-origin trust ----

def _admin(main):
    return _as_user(main, {'id': 1, 'username': 'owner', 'is_admin': True})


def _grab_as_user(main, url, qbit=None):
    qbit = qbit or _FakeQbit('00' * 20)
    with patch('requests.Session', return_value=qbit), patch('time.sleep'), _no_auto_rename(), \
         patch.object(main, '_record_origin'), _as_user(main, {'id': 2, 'is_admin': False}):
        return main.app.test_client().post('/api/downloads/add', json={'torrent_url': url}), qbit


def test_public_redirect_to_prowlarr_api_refused(route_env):
    main, adapter, pg, val = route_env
    main.settings.store['indexer.allow_lan'] = True  # the path rule holds even with LAN allowed
    pg.side_effect = [_resp(302, {'Location': 'http://localhost:9696/api/v1/indexer'})]
    r, qbit = _grab_as_user(main, 'https://tracker.example.com/dl/1')
    assert r.status_code == 403 and pg.call_count == 1 and qbit.added == []


def test_user_supplied_prowlarr_api_url_refused(route_env):
    main, adapter, pg, _ = route_env
    r, qbit = _grab_as_user(main, 'http://localhost:9696/api/v1/system/status?apikey=pk')
    assert r.status_code == 403 and pg.call_count == 0 and qbit.added == []


def test_public_redirect_back_to_prowlarr_download_gets_no_trust(route_env):
    """After a cross-host hop, the Prowlarr origin is no longer trusted (loopback blocked)."""
    main, adapter, pg, val = route_env
    val.side_effect = lambda url, **kw: (True, '', '203.0.113.9') if 'tracker' in url else _REAL_VALIDATE(url, **kw)
    pg.side_effect = [_resp(302, {'Location': 'http://localhost:9696/3/download?link=x'})]
    r, qbit = _grab_as_user(main, 'https://tracker.example.com/dl/1')
    assert r.status_code == 403 and pg.call_count == 1
    assert val.call_args_list[-1].kwargs == {'allow_lan': False, 'allow_loopback_for_host': None}


def test_normal_prowlarr_download_link_still_works(route_env):
    import hashlib
    main, adapter, pg, val = route_env
    val.side_effect = _REAL_VALIDATE
    pg.side_effect = [_resp(200, {}, TORRENT)]
    r, qbit = _grab_as_user(main, 'http://localhost:9696/3/download?apikey=pk&link=abc&file=Movie',
                            _FakeQbit(hashlib.sha1(b'd4:name1:xe').hexdigest()))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['hash'] == hashlib.sha1(b'd4:name1:xe').hexdigest()
    assert val.call_args.kwargs == {'allow_lan': True, 'allow_loopback_for_host': 'localhost'}


def test_generic_link_gets_no_prowlarr_trust_on_luma_backend(route_env):
    main, adapter, pg, val = route_env
    main.settings.store['indexer.backend'] = 'luma'
    val.side_effect = _REAL_VALIDATE
    r, qbit = _grab_as_user(main, 'http://localhost:9696/3/download?link=abc')
    assert r.status_code == 403 and pg.call_count == 0


@pytest.mark.parametrize('base,path,ok', [
    ('http://p:9696', '/3/download', True),
    ('http://p:9696/', '/12/download', True),
    ('http://p:9696/prowlarr', '/prowlarr/3/download', True),
    ('http://p:9696/prowlarr', '/3/download', False),
    ('http://p:9696', '/api/v1/indexer', False),
    ('http://p:9696', '/3/api', False),
    ('http://p:9696', '/3/download/extra', False),
    ('http://p:9696', '/x/download', False),
])
def test_prowlarr_download_path_pattern(route_env, base, path, ok):
    main, *_ = route_env
    main.settings.store['indexer.base_url'] = base
    assert main._is_prowlarr_download_path(f'http://p:9696{path}?link=a') is ok


# ---- Round 6, item 2: magnet topics ----

@pytest.mark.parametrize('xt,ok', [
    ('urn:btih:' + 'a1' * 20, True),
    ('urn:btih:' + 'A1' * 20, True),
    ('urn:btih:' + 'a' * 39, False),
    ('urn:btih:' + 'a' * 41, False),
    ('urn:btih:' + 'g' * 40, False),
    ('urn:btih:' + 'abcdefghijklmnopqrstuvwxyz234567', True),
    ('urn:btih:' + 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567', True),
    ('urn:btih:' + 'abcdefghijklmnopqrstuvwxyz234561', False),
    ('urn:btih:' + 'a' * 31, False),
    ('urn:btmh:1220' + 'ab' * 32, True),
    ('urn:btmh:1220' + 'ab' * 31 + 'a', False),
    ('urn:btmh:1221' + 'ab' * 32, False),
    ('urn:btmh:' + 'ab' * 34, False),
    ('urn:btmh:1220' + 'zz' * 32, False),
])
def test_magnet_topic_formats(xt, ok):
    from app.main import IndexerDownloadError, _clean_magnet
    uri = f'magnet:?dn=x&xt={xt}&tr=udp://t'
    if ok:
        assert _clean_magnet(uri) == uri
    else:
        with pytest.raises(IndexerDownloadError):
            _clean_magnet(uri)


# ---- Round 6, item 3: Prowlarr backend results carry tokens ----

class _FakeLegacyClient:
    def __init__(self, items):
        self.items = items

    def search_all(self, title, media_type, year=None):
        return [dict(i) for i in self.items]


PROWLARR_ITEMS = [
    {'title': 'Movie.2024.1080p', 'protocol': 'torrent', 'indexer': 'A', 'indexer_id': 3,
     'download_url': 'http://localhost:9696/3/download?apikey=pk&link=abc&file=Movie'},
    {'title': 'Movie.2024.720p', 'protocol': 'torrent', 'indexer': 'B', 'indexer_id': 4,
     'download_url': MAGNET},
    {'title': 'Movie.2024.nzb', 'protocol': 'usenet', 'indexer': 'C', 'indexer_id': 5,
     'download_url': 'http://localhost:9696/5/download?apikey=pk&link=def'},
]


@pytest.fixture
def all_results(route_env, monkeypatch):
    main, adapter, pg, val = route_env
    monkeypatch.setattr(main, '_all_results_cache', {})
    with patch('app.indexer_api.get_indexer_client', return_value=_FakeLegacyClient(PROWLARR_ITEMS)):
        yield route_env


def _search(main):
    with _as_user(main, {'id': 2, 'is_admin': False}):
        return main.app.test_client().get('/api/sources/all-results?title=Movie&media_type=movie&year=2024')


def test_prowlarr_results_return_tokens_not_keys(all_results, temp_data_dir):
    main, adapter, pg, val = all_results
    r = _search(main)
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert 'apikey' not in text and 'localhost:9696' not in text
    urls = [rel['download_url'] for rel in r.get_json()['releases']]
    assert urls[0].startswith('/api/indexer/download/') and urls[2].startswith('/api/indexer/download/')
    assert urls[1] == MAGNET
    assert '=pk' not in (temp_data_dir / 'download_tokens.json').read_text()


def test_prowlarr_result_token_downloads_with_key_server_side(all_results):
    main, adapter, pg, val = all_results
    tok_url = _search(main).get_json()['releases'][0]['download_url']
    pg.side_effect = [_resp(200, {}, TORRENT)]
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(tok_url)
    assert r.status_code == 200 and r.data == TORRENT
    assert pg.call_args.args[0] == 'http://localhost:9696/3/download?link=abc&file=Movie&apikey=pk'
    assert pg.call_args.kwargs['headers'] == {'X-Api-Key': 'pk'}
    assert val.call_args.kwargs['allow_loopback_for_host'] == 'localhost'


def test_prowlarr_usenet_token_is_nzb(all_results):
    main, adapter, pg, _ = all_results
    tok_url = _search(main).get_json()['releases'][2]['download_url']
    pg.side_effect = [_resp(200, {}, b'<?xml version="1.0"?><nzb/>')]
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(tok_url)
    assert r.status_code == 200 and r.headers['Content-Type'] == 'application/x-nzb'


def test_grab_from_prowlarr_result_token(all_results):
    import hashlib
    main, adapter, pg, _ = all_results
    tok_url = _search(main).get_json()['releases'][0]['download_url']
    pg.side_effect = [_resp(200, {}, TORRENT)]
    r, qbit = _grab_as_user(main, tok_url, _FakeQbit(hashlib.sha1(b'd4:name1:xe').hexdigest()))
    assert r.status_code == 200, r.get_json()
    assert qbit.added[0]['files']['torrents'][1] == TORRENT


def test_torznab_backend_results_return_tokens(route_env, monkeypatch):
    main, adapter, pg, _ = route_env
    monkeypatch.setattr(main, '_all_results_cache', {})
    main.settings.store.update({'indexer.provider': 'torznab',
                                'indexer.torznab_url': 'https://jackett.example.com/api/v2.0/indexers/all/results/torznab'})
    items = [{'title': 'X', 'protocol': 'torrent',
              'download_url': 'https://jackett.example.com/dl/x?jackett_apikey=1&apikey=pk&path=a'}]
    with patch('app.indexer_api.get_indexer_client', return_value=_FakeLegacyClient(items)):
        r = _search(main)
    url = r.get_json()['releases'][0]['download_url']
    assert url.startswith('/api/indexer/download/') and 'apikey=pk' not in r.get_data(as_text=True)
    pg.side_effect = [_resp(200, {}, TORRENT)]
    with _as_user(main, {'id': 2, 'is_admin': False}):
        assert main.app.test_client().get(url).status_code == 200
    assert pg.call_args.args[0].endswith('apikey=pk')


def test_luma_backend_results_unchanged(route_env, monkeypatch):
    main, adapter, pg, _ = route_env
    monkeypatch.setattr(main, '_all_results_cache', {})
    main.settings.store['indexer.backend'] = 'luma'
    items = [{'title': 'X', 'protocol': 'torrent', 'download_url': '/api/indexer/download/already-a-token'}]
    with patch('app.indexer_api.get_indexer_client', return_value=_FakeLegacyClient(items)):
        r = _search(main)
    assert r.get_json()['releases'][0]['download_url'] == '/api/indexer/download/already-a-token'


# ---- Round 7, item 1: Torznab/Jackett on LAN gets the same scoped trust ----

JACKETT = 'http://192.168.1.50:9117'


@pytest.fixture
def torznab_lan(route_env):
    main, adapter, pg, val = route_env
    main.settings.store.update({'indexer.provider': 'torznab', 'indexer.allow_lan': False,
                                'indexer.torznab_url': JACKETT + '/api/v2.0/indexers/all/results/torznab'})
    val.side_effect = _REAL_VALIDATE
    return route_env


def _torrent_hash():
    import hashlib
    return hashlib.sha1(b'd4:name1:xe').hexdigest()


def test_torznab_lan_token_download_allowed(torznab_lan):
    main, adapter, pg, val = torznab_lan
    tok = adapter.manager.issue_download_token(JACKETT + '/dl/idx/?path=a&file=M', 'backend:torznab')
    pg.side_effect = [_resp(200, {}, TORRENT)]
    r = _get(main, tok)
    assert r.status_code == 200 and r.data == TORRENT
    assert val.call_args.kwargs['allow_lan'] is True


def test_torznab_lan_generic_grab_allowed(torznab_lan):
    main, adapter, pg, val = torznab_lan
    pg.side_effect = [_resp(200, {}, TORRENT)]
    r, qbit = _grab_as_user(main, JACKETT + '/dl/idx/?jackett_apikey=k&path=a', _FakeQbit(_torrent_hash()))
    assert r.status_code == 200, r.get_json()
    assert val.call_args.kwargs['allow_lan'] is True


@pytest.mark.parametrize('path', ['/api/v2.0/server/config', '/api/v2.0/indexers/all/config', '/UI/Dashboard'])
def test_torznab_api_paths_blocked(torznab_lan, path):
    main, adapter, pg, _ = torznab_lan
    main.settings.store['indexer.allow_lan'] = True  # the path rule holds even with LAN allowed
    r, qbit = _grab_as_user(main, JACKETT + path)
    assert r.status_code == 403 and pg.call_count == 0 and qbit.added == []


def test_torznab_redirect_to_jackett_api_blocked(torznab_lan):
    main, adapter, pg, _ = torznab_lan
    tok = adapter.manager.issue_download_token(JACKETT + '/dl/idx/?path=a', 'backend:torznab')
    pg.side_effect = [_resp(302, {'Location': '/api/v2.0/server/config'})]
    assert _get(main, tok).status_code == 403 and pg.call_count == 1


def test_torznab_cross_origin_redirect_loses_trust(torznab_lan):
    main, adapter, pg, val = torznab_lan
    tok = adapter.manager.issue_download_token(JACKETT + '/dl/idx/?path=a', 'backend:torznab')
    pg.side_effect = [_resp(302, {'Location': 'http://192.168.1.60:8080/file.torrent'})]
    r = _get(main, tok)
    assert r.status_code == 403 and pg.call_count == 1
    assert val.call_args.kwargs == {'allow_lan': False, 'allow_loopback_for_host': None}


def test_torznab_results_path_allowed(torznab_lan):
    assert torznab_lan[0]._is_torznab_download_path(JACKETT + '/api/v2.0/indexers/x/results/torznab/api?t=search')


def test_torznab_lan_needs_torznab_backend(torznab_lan):
    main, adapter, pg, _ = torznab_lan
    main.settings.store['indexer.provider'] = 'prowlarr'
    r, qbit = _grab_as_user(main, JACKETT + '/dl/idx/?path=a')
    assert r.status_code == 403 and pg.call_count == 0


# ---- Round 7, items 3 and 4: backend token state ----

def test_prowlarr_token_needs_prowlarr_backend(route_env):
    main, adapter, pg, _ = route_env
    tok = adapter.manager.issue_download_token('http://localhost:9696/3/download?link=a', 'backend:prowlarr')
    main.settings.store['indexer.backend'] = 'luma'
    r = _get(main, tok)
    assert r.status_code == 409 and pg.call_count == 0


def test_prowlarr_host_change_is_409(route_env):
    main, adapter, pg, _ = route_env
    tok = adapter.manager.issue_download_token('http://localhost:9696/3/download?link=a', 'backend:prowlarr')
    main.settings.store['indexer.base_url'] = 'http://prowlarr.example.com:9696'
    r = _get(main, tok)
    assert r.status_code == 409 and r.get_json()['error'] == 'Indexer configuration changed'
    assert pg.call_count == 0


# ---- Round 7, item 2: auth on result listings ----

AUTH_ROUTES = ['/api/sources/all-results?title=Movie&media_type=movie&year=2024',
               '/api/indexer/latest', '/api/radarr/movie/releases?movieId=1']


@pytest.mark.parametrize('route', AUTH_ROUTES)
def test_result_routes_refuse_anonymous(all_results, route):
    main = all_results[0]
    with _as_user(main, None):
        r = main.app.test_client().get(route)
    assert r.status_code == 401


@pytest.mark.parametrize('route', AUTH_ROUTES)
def test_result_routes_open_to_owner_on_open_access(all_results, route):
    main = all_results[0]
    owner = {'id': 1, 'name': 'owner', 'is_admin': 1}
    with patch.object(main._access, 'should_authenticate', return_value=False), \
         patch.object(main._access, 'owner', return_value=owner), \
         patch.object(main, 'get_radarr_for_request', return_value=None):
        r = main.app.test_client().get(route)
    assert r.status_code != 401
    if 'all-results' in route:
        assert r.status_code == 200 and r.get_json()['releases']


def test_result_routes_refuse_when_pin_required(all_results):
    main = all_results[0]
    with patch.object(main._access, 'should_authenticate', return_value=True):
        for route in AUTH_ROUTES:
            assert main.app.test_client().get(route).status_code == 401


# ---- Round 8: Torznab path normalization, token state, rule gating ----

@pytest.mark.parametrize('path,ok', [
    ('/jackett/dl/idx/', True),
    ('/jackett/dl/idx', True),
    ('/jackett/api/v2.0/indexers/x/results/torznab/api', True),
    ('/jackett/dl/..', False),
    ('/jackett/dl/.', False),
    ('/jackett/dl/%2e%2e', False),
    ('/jackett/dl/%2E%2E/', False),
    ('/jackett/dl/%252e%252e', False),
    ('/jackett/dl/%25252e%25252e', False),
    ('/jackett/dl/a%2fb', False),
    ('/jackett/dl/a%2Fb', False),
    ('/jackett/dl/a%252fb', False),
    ('/jackett/dl/a%25252Fb', False),
    ('/jackett/dl/a%5cb', False),
    ('/jackett/dl/a%5Cb', False),
    ('/jackett/dl/a%255cb', False),
    ('/jackett/dl/a\\b', False),
    ('/jackett/dl/..\\api', False),
    ('/jackett/api/v2.0/indexers/..%2f..%2fserver/results', False),
])
def test_torznab_path_normalization(route_env, path, ok):
    main = route_env[0]
    main.settings.store['indexer.torznab_url'] = 'http://192.168.1.50:9117/jackett/api/v2.0/indexers/all/results/torznab'
    assert main._is_torznab_download_path('http://192.168.1.50:9117' + path) is ok


def test_torznab_dotdot_grab_refused(torznab_lan):
    main, adapter, pg, _ = torznab_lan
    main.settings.store['indexer.allow_lan'] = True
    r, qbit = _grab_as_user(main, JACKETT + '/dl/%2e%2e/api/v2.0/server/config')
    assert r.status_code == 403 and pg.call_count == 0


def test_torznab_token_needs_torznab_backend(torznab_lan):
    main, adapter, pg, _ = torznab_lan
    tok = adapter.manager.issue_download_token(JACKETT + '/dl/idx/?path=a', 'backend:torznab')
    main.settings.store['indexer.provider'] = 'prowlarr'
    r = _get(main, tok)
    assert r.status_code == 409 and pg.call_count == 0


def test_torznab_token_on_luma_backend_is_409(torznab_lan):
    main, adapter, pg, _ = torznab_lan
    tok = adapter.manager.issue_download_token(JACKETT + '/dl/idx/?path=a', 'backend:torznab')
    main.settings.store['indexer.backend'] = 'luma'
    assert _get(main, tok).status_code == 409 and pg.call_count == 0


def test_torznab_host_change_is_409(torznab_lan):
    main, adapter, pg, _ = torznab_lan
    tok = adapter.manager.issue_download_token(JACKETT + '/dl/idx/?path=a', 'backend:torznab')
    main.settings.store['indexer.torznab_url'] = 'http://192.168.1.51:9117/api/v2.0/indexers/all/results/torznab'
    r = _get(main, tok)
    assert r.status_code == 409 and r.get_json()['error'] == 'Indexer configuration changed'
    assert pg.call_count == 0


def test_torznab_path_rule_applies_when_backend_inactive(torznab_lan):
    """Like Prowlarr's, the Torznab path rule holds whenever the URL is configured."""
    main, adapter, pg, _ = torznab_lan
    main.settings.store.update({'indexer.backend': 'luma', 'indexer.allow_lan': True})
    r, qbit = _grab_as_user(main, JACKETT + '/api/v2.0/server/config')
    assert r.status_code == 403 and pg.call_count == 0
    assert 'Torznab' in r.get_json()['error']
