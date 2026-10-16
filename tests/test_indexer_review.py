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
    sm = _settings({'indexer.indexers': [DIRECT, PROXIED],
                    'indexer.base_url': 'http://localhost:9696',
                    'indexer.api_key': 'pk'})
    adapter = LumaBackendAdapter(IndexerManager(sm))
    main.app.config['TESTING'] = True
    stream = Mock()
    stream.status_code = 200
    stream.headers = {'Content-Type': 'application/x-bittorrent'}
    stream.iter_content.side_effect = lambda chunk_size=8192: iter([b'd8:announce'])
    stream.raise_for_status = Mock()
    with patch('app.indexer_api.get_indexer_client', return_value=adapter), \
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
    assert kwargs['headers'] == {'X-Api-Key': 'k'}


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
    assert pg.call_args.kwargs['headers'] == {'X-Api-Key': 'pk'}


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
    assert calls[-1] - calls[0] < interval * 3 + 0.5


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


def test_download_nzb_type(route_env):
    main, adapter, pg, _ = route_env
    pg.return_value.headers = {'Content-Type': 'application/x-nzb'}
    tok = adapter.manager.issue_download_token('https://tracker.example.com/get/1', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    assert r.headers['Content-Type'] == 'application/x-nzb'
    assert r.headers['Content-Disposition'] == 'attachment; filename="download.nzb"'


def test_download_size_cap_while_streaming(route_env, monkeypatch):
    from app import indexer_manager as im
    main, adapter, pg, _ = route_env
    monkeypatch.setattr(im, 'MAX_RESPONSE_SIZE', 20000)
    pg.return_value.iter_content.side_effect = lambda chunk_size=8192: iter([b'x' * 8192] * 10)
    tok = adapter.manager.issue_download_token('https://tracker.example.com/dl/1', 'ix1')
    with _as_user(main, {'id': 2, 'is_admin': False}):
        r = main.app.test_client().get(f'/api/indexer/download/{tok}')
        body = r.get_data()
        r.close()
    assert len(body) <= 20000
    pg.return_value.close.assert_called()


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
