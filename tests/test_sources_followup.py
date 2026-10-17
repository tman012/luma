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


def test_add_paused_defaults_off_and_category_env_is_mapped():
    from app import settings_manager as sm
    assert sm.SETTINGS_SCHEMA['qbittorrent']['add_paused']['default'] is False
    assert sm.ENV_VAR_MAP['QBITTORRENT_APP_CATEGORY'] == 'qbittorrent.app_category'


def test_test_build_compose_sets_luma_test_category():
    from pathlib import Path
    text = (Path(app.main.__file__).resolve().parent.parent / 'docker-compose.test.yml').read_text()
    assert 'QBITTORRENT_APP_CATEGORY=luma-test' in text


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
