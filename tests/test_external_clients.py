"""Endpoints external clients call keep working with the same access rules.

Inventory (from the alfred agent source, the Android kiosk app and the
routes the PM asked to keep). Each entry: method, example path, caller.
"""

import pytest
import requests

import app.main

ALFRED = 'alfred'
KIOSK = 'android-kiosk'
KEEP = 'keep (PM list)'

ENDPOINTS = [
    # alfred agent (agent.py, handlers/*, openclaw fallback)
    ('GET', '/api/activity', ALFRED),
    ('GET', '/api/adb/shield_activity', ALFRED),
    ('GET', '/api/downloads/history', ALFRED),
    ('GET', '/api/explore/top', ALFRED),
    ('GET', '/api/ha/area/kitchen/entities', ALFRED),
    ('GET', '/api/ha/areas', ALFRED),
    ('GET', '/api/in-theaters', ALFRED),
    ('GET', '/api/kiosk/settings', ALFRED),
    ('GET', '/api/library/episodes', ALFRED),
    ('GET', '/api/library/recent', ALFRED),
    ('GET', '/api/library/stats/cached', ALFRED),
    ('GET', '/api/library/vibe-engine', ALFRED),
    ('GET', '/api/movie/603', ALFRED),
    ('GET', '/api/news', ALFRED),
    ('POST', '/api/pending-request', ALFRED),
    ('POST', '/api/player/play', ALFRED),
    ('GET', '/api/plex/clients', ALFRED),
    ('GET', '/api/plex/continue_watching', ALFRED),
    ('POST', '/api/plex/playback/control', ALFRED),
    ('POST', '/api/plex/playback/seek', ALFRED),
    ('POST', '/api/plex/playback/stop', ALFRED),
    ('POST', '/api/plex/playback/subtitles', ALFRED),
    ('GET', '/api/plex/sessions/active', ALFRED),
    ('GET', '/api/popular', ALFRED),
    ('GET', '/api/radarr/movie/releases', ALFRED),
    ('POST', '/api/search/natural', ALFRED),
    ('GET', '/api/settings/rooms', ALFRED),
    ('GET', '/api/sonarr/calendar', ALFRED),
    ('GET', '/api/trending', ALFRED),
    ('GET', '/api/tv/1399', ALFRED),
    ('GET', '/api/tv/ownership/121361/cached', ALFRED),
    ('POST', '/api/tv/watchlist', ALFRED),
    ('GET', '/api/watchlist', ALFRED),
    ('POST', '/api/watchlist', ALFRED),
    ('DELETE', '/api/watchlist/603', ALFRED),
    ('GET', '/api/weather', ALFRED),
    # Android kiosk app
    ('GET', '/api/version', KIOSK),
    # Kept on request (legacy pages, voice, Alfred bridge)
    ('GET', '/api/tv/show?title=Example', KEEP),
    ('GET', '/api/tv/episodes?title=Example&season=1', KEEP),
    ('GET', '/api/player/status/123', KEEP),
    ('GET', '/api/library/stats', KEEP),
    ('POST', '/api/adb/control', KEEP),
    ('GET', '/api/plex/ratings/resolve?tmdb_id=603', KEEP),
    ('GET', '/api/plex/ultrablur/resolve?tmdb_id=603', KEEP),
    ('GET', '/api/plex/episode_libraries/123', KEEP),
    ('GET', '/api/tmdb/discover', KEEP),
    ('POST', '/api/alfred/command', KEEP),
    ('GET', '/api/alfred/health', KEEP),
    ('POST', '/api/alfred/reset', KEEP),
    ('POST', '/api/voice/command', KEEP),
    ('POST', '/api/voice/tts', KEEP),
    ('POST', '/api/voice/transcribe', KEEP),
    ('POST', '/api/upgrades/auto-grab', KEEP),
]

IDS = [f'{m} {p}' for m, p, _ in ENDPOINTS]
LOCAL = {'REMOTE_ADDR': '127.0.0.1'}         # alfred runs with host networking
LAN = {'REMOTE_ADDR': '192.168.1.40'}
REMOTE = {'REMOTE_ADDR': '203.0.113.7'}


@pytest.fixture
def client(test_app, monkeypatch):
    import app.user_auth as user_auth
    user_auth.create_user('owner', '1234', is_admin=True)
    def offline(*a, **k):
        raise requests.ConnectionError('offline in tests')
    monkeypatch.setattr(requests.Session, 'request', offline)
    monkeypatch.setattr(requests, 'request', offline)
    for verb in ('get', 'post', 'put', 'delete'):
        monkeypatch.setattr(requests, verb, offline)
    import subprocess

    def no_adb(*a, **k):
        raise FileNotFoundError('adb not available in tests')
    monkeypatch.setattr(subprocess, 'run', no_adb)
    return test_app.test_client()


def _call(client, method, path, env, headers=None):
    return client.open(path, method=method, json={}, environ_base=env,
                       headers={'Host': 'localhost:5001', **(headers or {})})


@pytest.mark.parametrize('method,path,who', ENDPOINTS, ids=IDS)
def test_route_exists(test_app, method, path, who):
    adapter = test_app.url_map.bind('localhost')
    endpoint, _ = adapter.match(path.split('?')[0], method=method)
    assert endpoint


@pytest.mark.parametrize('method,path,who', ENDPOINTS, ids=IDS)
def test_local_caller_needs_no_sign_in(client, method, path, who):
    for env in (LOCAL, LAN):
        r = _call(client, method, path, env)
        assert r.status_code not in (401, 403, 405), (path, r.status_code, r.get_data(as_text=True)[:200])


@pytest.mark.parametrize('method,path,who', ENDPOINTS, ids=IDS)
def test_service_key_works_from_anywhere(client, method, path, who):
    r = _call(client, method, path, REMOTE, {'X-API-Key': app.main._SERVICE_API_KEY})
    assert r.status_code not in (401, 403, 405), (path, r.status_code)


@pytest.mark.parametrize('method,path,who', [e for e in ENDPOINTS if e[1] != '/api/version'],
                         ids=[i for i in IDS if not i.endswith('/api/version')])
def test_remote_caller_without_key_signs_in(client, method, path, who):
    assert _call(client, method, path, REMOTE).status_code == 401


def test_wrong_service_key_rejected(client):
    r = _call(client, 'GET', '/api/library/stats', REMOTE, {'X-API-Key': 'wrong'})
    assert r.status_code == 401
