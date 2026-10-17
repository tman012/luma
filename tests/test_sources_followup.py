"""Sources follow-up (prod blockers): passkey redaction, opaque indexer
links, qBit 503, add fields, total fetch deadline, magnet control
characters, IPv6-mapped backoff keys, indexer page gating."""

import json

import pytest
import requests

import app.main
import app.user_auth
from app import outbound_guard as og
from app import tracker_guard, tracker_sources
from tests.test_downloads_add_ssrf import (  # noqa: F401  (fixtures)
    net, qbit, client, _owner_client, _post, LAN, ORIGIN, PUBLIC, TORRENT)

PK = 'pk9f8e7d6c5b4a39281706f5e4d3c2b1a0'


# ── passkey redaction in every URL field ────────────────────────────────

@pytest.mark.parametrize('url,leak', [
    (f'https://t.example/details.php?id=5&passkey={PK}', PK),
    (f'https://t.example/details.php?id=5&apikey={PK}#comments', PK),
    (f'https://user:{PK}@t.example/d?id=5', PK),
    (f'https://t.example/rss/{PK}/5', PK),
])
def test_strip_link_secrets_removes_keys(url, leak):
    out = app.main._strip_link_secrets(url)
    assert leak not in out and out


def test_strip_link_secrets_keeps_harmless_parts():
    assert app.main._strip_link_secrets('https://t.example/details.php?id=5') == 'https://t.example/details.php?id=5'
    assert app.main._strip_link_secrets('magnet:?xt=urn:btih:' + 'a' * 40).startswith('magnet:')


def test_every_url_field_is_redacted_and_keyed_download_link_is_opaque():
    payload = {'releases': [{
        'guid': f'https://t.example/d?id=1&passkey={PK}', 'info_url': f'https://t.example/i?apikey={PK}',
        'comments': f'https://t.example/c?id=1&passkey={PK}#c', 'link': f'https://t.example/l?passkey={PK}',
        'download_url': f'https://prowlarr.example/1/download?apikey={PK}&link=x'}]}
    out = app.main._all_results_public(payload)
    assert PK not in json.dumps(out)
    dl = out['releases'][0]['download_url']
    assert dl.startswith(app.main.SOURCE_LINK_PREFIX)
    assert app.main._resolve_source_link(dl) == (True, payload['releases'][0]['download_url'])
    assert app.main._resolve_source_link(app.main.SOURCE_LINK_PREFIX + 'nope') == (True, None)
    assert app.main._resolve_source_link('https://x.example/a.torrent') == (False, None)


def test_opaque_link_is_swapped_back_and_still_guarded(client, net, qbit):
    _owner_client(client)
    net.dns['idx.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'idx.example', f'/t?apikey={PK}')] = (200, {}, TORRENT)
    link = app.main._public_download_link(f'https://idx.example/t?apikey={PK}')
    r = _post(client, link)
    assert r.status_code == 200, r.get_json()
    assert len(qbit.added) == 1 and net.opens[0][1] == PUBLIC
    # An opaque link to an internal address is still refused before connecting.
    bad = app.main._public_download_link(f'http://127.0.0.1:9696/t?apikey={PK}')
    net.opens.clear()
    assert _post(client, bad).status_code == 400 and net.opens == []
    assert _post(client, app.main.SOURCE_LINK_PREFIX + 'expired').status_code == 410


# ── qBit unreachable / add fields ───────────────────────────────────────

def test_qbit_unreachable_is_generic_503(client, net, qbit, monkeypatch):
    _owner_client(client)

    def down(*a, **k):
        raise requests.exceptions.ConnectionError('HTTPConnectionPool(host=qbit.test, port=8080)')
    monkeypatch.setattr(qbit, 'post', down)
    r = _post(client, 'magnet:?xt=urn:btih:' + 'ab' * 20)
    assert r.status_code == 503
    assert r.get_json() == {'error': 'Download client unavailable'}
    assert 'qbit.test' not in r.get_data(as_text=True)


@pytest.mark.parametrize('paused', [False, True])
def test_add_uses_configured_category_and_optional_paused(client, net, qbit, paused):
    _owner_client(client)
    app.main.settings.update({'qbittorrent': {'url': 'http://qbit.test', 'username': 'u', 'password': 'p',
                                              'app_category': 'luma-test', 'add_paused': paused}})
    assert _post(client, 'magnet:?xt=urn:btih:' + 'cd' * 20).status_code == 200
    data = qbit.added[0]['data']
    assert data['category'] == 'luma-test'
    assert (data.get('paused'), data.get('stopped')) == (('true', 'true') if paused else (None, None))


def test_add_paused_defaults_off_and_nothing_preconfigures_qbit():
    from pathlib import Path
    from app import settings_manager as sm
    assert sm.SETTINGS_SCHEMA['qbittorrent']['add_paused']['default'] is False
    assert 'QBITTORRENT_APP_CATEGORY' not in sm.ENV_VAR_MAP
    text = (Path(app.main.__file__).resolve().parent.parent / 'docker-compose.test.yml').read_text()
    assert 'QBITTORRENT' not in text


# ── outbound_guard total deadline ───────────────────────────────────────

def test_default_total_deadline_is_capped_at_30s(monkeypatch):
    seen = {}

    def fake_open(scheme, ip, port, host, target, headers, timeout, max_bytes, deadline=None):
        import time
        seen['left'] = deadline - time.monotonic()
        return 200, {}, TORRENT
    monkeypatch.setattr(og.socket, 'getaddrinfo', lambda h, p, *a, **k: [(2, 1, 6, '', (PUBLIC, p))])
    monkeypatch.setattr(og, '_open', fake_open)
    og.fetch('https://tracker.example/x')
    assert 25 < seen['left'] <= og.TOTAL_TIMEOUT == 30.0


def test_deadline_already_spent_on_a_redirect_hop_stops(monkeypatch):
    import time
    clock = iter([0.0, 1.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(og.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(og.socket, 'getaddrinfo', lambda h, p, *a, **k: [(2, 1, 6, '', (PUBLIC, p))])
    monkeypatch.setattr(og, '_open', lambda *a, **k: (302, {'Location': 'https://tracker.example/y'}, b''))
    with pytest.raises(og.FetchError):
        og.fetch('https://tracker.example/x')


# ── magnets ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('m', ['magnet:?xt=a\nb', 'magnet:?xt=a\rb', 'magnet:?xt=a\tb', 'magnet:?xt=a\x00b',
                               'magnet:?xt=a\x7fb', 'http://x/'])
def test_safe_magnet_refuses_control_characters(m):
    assert tracker_sources.safe_magnet(m) is None
    assert app.main._safe_magnet(m) is None


def test_safe_magnet_accepts_one_line():
    m = 'magnet:?xt=urn:btih:' + 'a' * 40 + '&dn=x'
    assert tracker_sources.safe_magnet('  ' + m + ' ') == m


# ── backoff keys ────────────────────────────────────────────────────────

@pytest.mark.parametrize('spelling', ['::ffff:93.184.216.34', '[::ffff:93.184.216.34]', '::ffff:5db8:d822',
                                      '93.184.216.34', '::93.184.216.34'])
def test_ip_key_folds_ipv4_in_ipv6(spelling):
    assert tracker_guard.ip_key(spelling) == 'ip:93.184.216.34'


def test_normalize_host_folds_mapped_literal():
    assert tracker_guard.normalize_host('https://[::ffff:93.184.216.34]:9117/api') == '93.184.216.34'
    assert tracker_guard.ip_key('::1') == 'ip:::1'


def test_mapped_spelling_cannot_bypass_a_backoff(tmp_path):
    g = tracker_guard.TrackerGuard(tmp_path / 'g.json')
    g.failure(tracker_guard.ip_key('93.184.216.34'), 'rate', 600)
    assert g.blocked(tracker_guard.ip_key('::ffff:93.184.216.34'))


# ── indexer page gating ─────────────────────────────────────────────────

def _member(test_app, approved, admin=False):
    app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    u = app.user_auth.create_user('m' + str(approved) + str(admin), 'pin-7654321', is_admin=admin)
    if approved:
        app.user_auth.save_user_settings(u['id'], {'plex_access_status': 'approved'})
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(u['id']))
    return c


@pytest.mark.parametrize('trackers', [True, False])
@pytest.mark.parametrize('path', ['/api/indexer/browse', '/api/indexer/latest'])
def test_unapproved_user_never_triggers_indexer_or_tracker_calls(test_app, monkeypatch, trackers, path):
    calls = []
    monkeypatch.setattr(tracker_sources, 'trackers_in_use', lambda: trackers)
    import app.indexer_api as ia
    monkeypatch.setattr(ia, 'get_indexer_client', lambda s: calls.append(1))
    app.main._LATEST_TORRENTS_CACHE.clear()
    c = _member(test_app, approved=False)
    r = c.get(path)
    assert r.status_code == 403, r.get_data(as_text=True)
    assert calls == []


def test_unapproved_user_does_not_get_cached_latest(test_app, monkeypatch):
    monkeypatch.setattr(tracker_sources, 'trackers_in_use', lambda: True)
    app.main._LATEST_TORRENTS_CACHE['browse'] = {'results': [{'title': 'cached'}], 'fetched_at': 9e18}
    try:
        r = _member(test_app, approved=False).get('/api/indexer/latest')
        assert r.status_code == 403 and 'cached' not in r.get_data(as_text=True)
    finally:
        app.main._LATEST_TORRENTS_CACHE.clear()


def test_approved_user_and_internal_jobs_pass_the_gate(test_app, monkeypatch):
    monkeypatch.setattr(tracker_sources, 'trackers_in_use', lambda: True)
    app.main._LATEST_TORRENTS_CACHE['browse'] = {'results': [{'title': 'cached'}], 'fetched_at': 9e18}
    try:
        r = _member(test_app, approved=True).get('/api/indexer/latest')
        assert r.status_code == 200
        assert app.main.latest_releases() == [{'title': 'cached'}]
    finally:
        app.main._LATEST_TORRENTS_CACHE.clear()


# ── /api/indexer/grab is admin-only ─────────────────────────────────────

@pytest.mark.parametrize('approved', [False, True])
def test_indexer_grab_is_admin_only(test_app, monkeypatch, approved):
    called = []
    monkeypatch.setattr(app.main, 'get_radarr_for_request', lambda: called.append(1))
    c = _member(test_app, approved=approved)
    r = c.post('/api/indexer/grab', headers=ORIGIN, json={'guid': 'g', 'indexer_id': 1})
    assert r.status_code == 403 and r.get_json()['error'] == 'Admin access required'
    assert called == []


def test_indexer_grab_admin_passes_the_gate(test_app, monkeypatch):
    monkeypatch.setattr(app.main, 'get_radarr_for_request', lambda: None)
    c = _member(test_app, approved=False, admin=True)
    r = c.post('/api/indexer/grab', headers=ORIGIN, json={'guid': 'g', 'indexer_id': 1})
    assert r.status_code == 503       # past the gate: Radarr isn't configured here


# ── qBit login body without a charset ───────────────────────────────────

class _Raw:
    def __init__(self, content, status=200):
        self.status_code, self.content = status, content

    @property
    def text(self):            # what requests may guess for a charset-less body
        return self.content.decode('utf-16', 'replace')

    def json(self):
        return []


def test_downloads_add_reads_qbit_login_as_utf8(client, net, qbit, monkeypatch):
    _owner_client(client)
    real_post = qbit.post

    def post(url, **kw):
        if url.endswith('/auth/login'):
            qbit.logins += 1
            return _Raw(b'Ok.')
        return real_post(url, **kw)
    monkeypatch.setattr(qbit, 'post', post)
    r = _post(client, 'magnet:?xt=urn:btih:' + 'ef' * 20)
    assert r.status_code == 200, r.get_json()
    assert app.main._qbit_body(_Raw(b'Ok.\n')) == 'Ok.'


# ── saved qBit password stays bound to the saved URL ────────────────────

def _saved_qbit():
    app.main.settings.update({'qbittorrent': {'url': 'http://qbit.saved:8080', 'username': 'u',
                                              'password': 'saved-pw-for-tests'}})


@pytest.fixture
def checks(monkeypatch):
    from app import qbit_connector
    seen = []
    monkeypatch.setattr(qbit_connector, 'check', lambda url, user, pw: (seen.append((url, user, pw)) or (None, 'v4.6.5')))
    return seen


def test_old_test_endpoint_uses_saved_password_only_for_saved_url(client, checks):
    _owner_client(client)
    _saved_qbit()
    r = client.post('/api/settings/test/qbittorrent', headers=ORIGIN, environ_base=LAN,
                    json={'url': 'http://qbit.saved:8080/', 'password': '********'})
    assert r.get_json()['success'] is True
    assert checks[-1] == ('http://qbit.saved:8080', 'u', 'saved-pw-for-tests')
    r = client.post('/api/settings/test/qbittorrent', headers=ORIGIN, environ_base=LAN,
                    json={'url': 'http://evil.example:8080', 'password': '********'})
    assert r.status_code == 400 and len(checks) == 1
    r = client.post('/api/settings/test/qbittorrent', headers=ORIGIN, environ_base=LAN,
                    json={'url': 'http://evil.example:8080', 'password': 'typed'})
    assert checks[-1] == ('http://evil.example:8080', 'u', 'typed')
    assert 'saved-pw-for-tests' not in r.get_data(as_text=True)


def test_put_settings_with_new_url_drops_the_saved_password(client):
    _owner_client(client)
    _saved_qbit()
    r = client.put('/api/settings', headers=ORIGIN, environ_base=LAN,
                   json={'qbittorrent': {'url': 'http://qbit.saved:8080', 'password': '********'}})
    assert r.status_code == 200
    assert app.main.settings.get('qbittorrent.password') == 'saved-pw-for-tests'
    r = client.put('/api/settings', headers=ORIGIN, environ_base=LAN,
                   json={'qbittorrent': {'url': 'http://other.example:8080', 'password': '********'}})
    assert r.status_code == 200
    assert app.main.settings.get('qbittorrent.password') == ''
    _saved_qbit()
    client.put('/api/settings', headers=ORIGIN, environ_base=LAN,
               json={'qbittorrent': {'url': 'http://other.example:8080', 'password': 'new-typed'}})
    assert app.main.settings.get('qbittorrent.password') == 'new-typed'


def test_qbit_connector_check_ignores_env_proxies(monkeypatch):
    from app import qbit_connector
    made = []

    class S:
        trust_env = True

        def __init__(self):
            made.append(self)

        def post(self, *a, **k):
            raise requests.exceptions.ConnectionError()

        def close(self):
            pass
    monkeypatch.setattr(qbit_connector.requests, 'Session', S)
    qbit_connector.check('http://qbit.test', 'u', 'p')
    assert made and made[0].trust_env is False


# ── No "Connect Radarr" sheet over Sources when trackers are set up ─────

@pytest.mark.parametrize('trackers', [True, False])
def test_movie_releases_without_radarr(test_app, monkeypatch, trackers):
    monkeypatch.setattr(tracker_sources, 'trackers_in_use', lambda: trackers)
    monkeypatch.setattr(app.main, 'get_radarr_for_request', lambda: None)
    c = _member(test_app, approved=False, admin=True)
    r = c.get('/api/radarr/movie/releases?tmdb_id=603')
    body = r.get_json()
    if trackers:
        assert r.status_code == 200 and 'needs_setup' not in body
        assert body['releases'] == [] and body['radarr_configured'] is False
    else:
        assert r.status_code == 503 and body['needs_setup'] == 'radarr'


# ── downloads/add 502s never name the host (QA ceed5e5 item 4) ──────────

@pytest.mark.parametrize('exc', [
    requests.exceptions.ConnectionError("HTTPConnectionPool(host='169.254.169.254', port=80): Max retries"),
    og.FetchError("connect to 10.0.0.7:8080 failed"),
    OSError("[Errno 111] Connection refused: tracker.internal.lan"),
])
def test_torrent_fetch_failure_is_generic_502(client, net, qbit, monkeypatch, exc):
    _owner_client(client)

    def boom(url):
        raise exc
    monkeypatch.setattr(app.main, '_fetch_pasted_torrent', boom)
    r = _post(client, 'https://tracker.example/dl/1')
    body = r.get_data(as_text=True)
    assert r.status_code == 502 and r.get_json() == {'error': 'Couldn’t get the torrent from that link'}
    for leak in ('169.254', '10.0.0.7', 'tracker.internal', 'HTTPConnectionPool', 'Errno'):
        assert leak not in body
    assert qbit.logins == 0


@pytest.mark.parametrize('url', ['', 'http://qbit.test:8080'])
def test_sources_config_says_whether_qbit_is_set_up(test_app, url):
    app.main.settings.update({'qbittorrent': {'url': url}})
    c = _member(test_app, approved=False, admin=True)
    j = c.get('/api/sources/config').get_json()
    assert j['download_client_configured'] is bool(url)
    assert 'qbit.test' not in json.dumps(j)
