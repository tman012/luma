"""Auto-grab jobs read /api/indexer/latest in-process (no HTTP, no loopback
trust); LAN and kiosk clients get open access; tunnel and WAN stay remote."""

import pytest
from flask import jsonify

import app.user_auth as user_auth

PROXIES = '127.0.0.1/32, ::1/128'


def _prod_like(auth_required='disabled_local'):
    import app.main
    app.main.settings.update({'security': {
        'trusted_proxies': PROXIES,
        'hostnames': 'docker-server, docker-server.local, docker-server.lan',
        'auth_required': auth_required}})


# --- latest_releases: the in-process path ---

def test_latest_releases_runs_view_without_auth(test_app, monkeypatch):
    import app.main
    _prod_like(auth_required='enabled')
    seen = {}

    def fake_view():
        from flask import request
        seen['args'] = dict(request.args)
        return jsonify({'results': [{'guid': 'g1'}]})

    monkeypatch.setattr(app.main, 'indexer_latest', fake_view)
    assert app.main.latest_releases({'limit': 600, 'sweep': 'remux2160p'}) == [{'guid': 'g1'}]
    assert seen['args'] == {'limit': '600', 'sweep': 'remux2160p'}


def test_latest_releases_raises_on_view_error(test_app, monkeypatch):
    import app.main
    monkeypatch.setattr(app.main, 'indexer_latest', lambda: (jsonify({'error': 'No indexer'}), 503))
    with pytest.raises(RuntimeError, match='No indexer'):
        app.main.latest_releases({'limit': 300})


def test_http_route_still_requires_sign_in_from_loopback(test_app):
    _prod_like()
    r = test_app.test_client().get('/api/indexer/latest?limit=1', base_url='http://localhost:5001',
                                   environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert r.status_code == 401


# --- both auto-grab jobs use it, never HTTP ---

class _Radarr:
    def __init__(self, *a, **k):
        pass

    def get_root_folders(self):
        return [{'path': '/movies'}]

    def get_quality_profiles(self):
        return [{'id': 1, 'name': 'Any'}]


class _Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _no_http(*a, **k):
    raise AssertionError('auto-grab must not make HTTP calls to Luma')


@pytest.fixture
def jobs_env(test_app, monkeypatch):
    import requests
    import app.main
    import app.radarr_api
    calls = []

    def fake_latest(params=None):
        calls.append(dict(params or {}))
        return []

    monkeypatch.setattr(app.main, 'latest_releases', fake_latest)
    monkeypatch.setattr(app.radarr_api, 'RadarrAPI', _Radarr)
    monkeypatch.setattr(requests, 'get', _no_http)
    return calls


def test_latest_auto_grab_fetches_in_process(jobs_env):
    from app import latest_auto_grab
    cfg = _Cfg({'radarr.url': 'http://radarr.invalid', 'radarr.api_key': 'k',
                'automation.latest_auto_grab.quality_profile_id': 1,
                'automation.upgrade_4k_auto_grab.plex_4k_section_id': 0})
    out = latest_auto_grab._do_cycle(cfg)
    assert jobs_env == [{'limit': 300}, {'limit': 600, 'sweep': 'remux2160p'}]
    assert '401' not in str(out.get('error', ''))


def test_upgrade_4k_auto_grab_fetches_in_process(jobs_env):
    from app import upgrade_4k_auto_grab
    cfg = _Cfg({'radarr.url': 'http://radarr.invalid', 'radarr.api_key': 'k',
                'automation.upgrade_4k_auto_grab.quality_profile_id': 1,
                'automation.upgrade_4k_auto_grab.plex_4k_section_id': 7})
    out = upgrade_4k_auto_grab._do_cycle(cfg)
    assert jobs_env == [{'limit': 600, 'sweep': 'remux2160p'}]
    assert '401' not in str(out.get('error', ''))


def test_no_job_calls_luma_over_http():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / 'app'
    for p in root.glob('*.py'):
        src = p.read_text()
        assert 'localhost:5001/api' not in src and '127.0.0.1:5001/api' not in src, p.name


# --- who gets open access ---

def _get(test_app, peer, host, headers=None):
    return test_app.test_client().get('/api/auth/me', base_url='http://' + host,
                                      headers=headers or {}, environ_base={'REMOTE_ADDR': peer})


@pytest.mark.parametrize('peer,host,headers', [
    ('192.168.1.95', '192.168.1.62:5001', {}),                                # LAN kiosk, direct
    ('192.168.1.95', 'docker-server:5001', {}),                                # LAN by host name
    ('127.0.0.1', '192.168.1.62', {'X-Forwarded-For': '192.168.1.95'}),       # LAN via Caddy
    ('::1', '192.168.1.62', {'X-Forwarded-For': '192.168.1.95'}),
])
def test_lan_gets_open_access(test_app, peer, host, headers):
    user_auth.create_user('Owner', 'x-pass-1', is_admin=True)
    _prod_like()
    r = _get(test_app, peer, host, headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    r = test_app.test_client().get('/api/watchlist', base_url='http://' + host,
                                   headers=headers, environ_base={'REMOTE_ADDR': peer})
    assert r.status_code != 401


@pytest.mark.parametrize('peer,host,headers', [
    ('203.0.113.9', '192.168.1.62:5001', {}),                                  # WAN direct
    ('127.0.0.1', 'localhost:5001', {}),                                       # loopback, no client given
    ('127.0.0.1', 'tflixapp.com', {'X-Forwarded-For': '192.168.1.95',
                                   'CF-Connecting-IP': '198.51.100.7', 'CF-Ray': 'x'}),  # tunnel spoof
    ('127.0.0.1', 'tflixapp.com', {'X-Forwarded-For': '198.51.100.7'}),        # tunnel, public client
    ('192.168.1.95', '192.168.1.62:5001', {'X-Forwarded-For': '192.168.1.96'}),  # untrusted relay
])
def test_remote_requires_sign_in(test_app, peer, host, headers):
    user_auth.create_user('Owner', 'x-pass-1', is_admin=True)
    _prod_like()
    assert _get(test_app, peer, host, headers).status_code == 401
    r = test_app.test_client().get('/api/watchlist', base_url='http://' + host,
                                   headers=headers, environ_base={'REMOTE_ADDR': peer})
    assert r.status_code == 401
