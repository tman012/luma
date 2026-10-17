"""Kiosk image caching after the #16 merge: a served Plex image is kept by this
browser only (private, a day, Vary: Cookie); refusals, errors and the sign-in
wall are never stored; sign-out clears the browser cache; the service worker
never stores /api/ replies."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import app.main as main
import app.user_auth as user_auth

ROOT = Path(__file__).resolve().parent.parent
LAN = {'REMOTE_ADDR': '192.168.1.20'}
WAN = {'REMOTE_ADDR': '203.0.113.9'}
DAY = 'private, max-age=86400'
NO_STORE = 'private, no-store'
PEOPLE = 'https://metadata-static.plex.tv/b/people/b604d520903d1d1f445458008ab9d1f3.jpg'


class _Resp:
    def __init__(self, status=200, ctype='image/jpeg', body=b'img'):
        self.status_code = status
        self.headers = {'Content-Type': ctype}
        self.body = body

    def iter_content(self, chunk_size=1):
        yield self.body

    def close(self):
        pass


@pytest.fixture
def proxy(test_app, monkeypatch):
    state = {'resp': _Resp(), 'calls': 0}
    monkeypatch.setattr(main, 'get_plex_for_request',
                        lambda: SimpleNamespace(plex_url='http://plex.test', plex_token='t'))

    def fake_get(url, **k):
        state['calls'] += 1
        if isinstance(state['resp'], Exception):
            raise state['resp']
        return state['resp']
    monkeypatch.setattr(main.requests, 'get', fake_get)
    return test_app.test_client(), state


def _vary(r):
    return [v.strip().lower() for v in r.headers.get('Vary', '').split(',') if v.strip()]


@pytest.mark.parametrize('query', [
    {'url': '/library/metadata/5/thumb', 'r': '2'},
    {'url': '/library/metadata/5/thumb/1700000000'},
    {'url': '/library/metadata/5/art'},
    {'url': '/library/metadata/5/art/1700000000', 'w': '1280', 'h': '720'},
    {'url': '/library/metadata/5/clearLogo', 'w': '500'},
    {'url': '/library/sections/2/composite/1700000000'},
    {'url': PEOPLE, 'w': '120'},
])
def test_served_images_kept_by_this_browser_for_a_day(proxy, query):
    c, _ = proxy
    r = c.get('/api/img/plex', query_string=query, environ_base=LAN)
    assert r.status_code == 200 and r.data == b'img'
    assert r.headers['Cache-Control'] == DAY
    assert _vary(r) == ['cookie']
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'public' not in r.headers['Cache-Control']
    assert 'ETag' not in r.headers


@pytest.mark.parametrize('upstream', [_Resp(status=404), _Resp(status=500), _Resp(status=302),
                                      _Resp(ctype='text/html'), _Resp(ctype='image/svg+xml'),
                                      requests.ConnectionError('down')])
def test_upstream_failures_never_stored(proxy, upstream):
    c, state = proxy
    state['resp'] = upstream
    r = c.get('/api/img/plex', query_string={'url': '/library/metadata/5/art/1'}, environ_base=LAN)
    assert r.status_code in (404, 502)
    assert r.headers['Cache-Control'] == NO_STORE
    assert 'cookie' not in _vary(r)


@pytest.mark.parametrize('query', [{}, {'url': ''}, {'url': '/library/../:/prefs'},
                                   {'url': 'http://evil.test/x.jpg'}, {'url': '/accounts'}])
def test_refusals_never_stored(proxy, query):
    c, state = proxy
    r = c.get('/api/img/plex', query_string=query, environ_base=LAN)
    assert r.status_code == 400 and state['calls'] == 0
    assert r.headers['Cache-Control'] == NO_STORE


def test_unconfigured_plex_never_stored(test_app, monkeypatch):
    monkeypatch.setattr(main, 'get_plex_for_request', lambda: None)
    monkeypatch.setattr(main.settings, 'get', lambda *a, **k: None)
    r = test_app.test_client().get('/api/img/plex?url=/library/metadata/1/thumb', environ_base=LAN)
    assert r.status_code == 503 and r.headers['Cache-Control'] == NO_STORE


@pytest.fixture
def accounts(test_app):
    user_auth.init_db()
    user_auth.create_user('owner', '1234', is_admin=True)
    return test_app.test_client()


def test_signed_out_image_request_is_refused_and_never_stored(accounts):
    r = accounts.get('/api/img/plex?url=/library/metadata/1/thumb/2', environ_base=WAN)
    assert r.status_code == 401
    assert r.headers['Cache-Control'] == NO_STORE


def test_sign_out_clears_the_browser_cache(accounts):
    user = user_auth.authenticate('owner', '1234')
    accounts.set_cookie(main.AUTH_COOKIE, user_auth.create_session(user['id']))
    r = accounts.post('/api/auth/logout', headers={'Origin': 'http://localhost'}, environ_base=WAN)
    assert r.status_code == 200
    assert r.headers['Clear-Site-Data'] == '"cache"'


NODE = shutil.which('node')
O = 'http://192.0.2.10:5001'


def _sw(urls, spec):
    env = dict(os.environ, SW_SHELL_RESP=json.dumps(spec))
    out = subprocess.run([NODE, str(ROOT / 'tests/js/sw_harness.js'), str(ROOT / 'static/sw.js'), f'{O}/', *urls],
                         capture_output=True, text=True, timeout=30, check=True, env=env)
    return json.loads(out.stdout)


def test_sw_version_bumped():
    assert "const CACHE_VERSION = 'media-kiosk-v135';" in (ROOT / 'static/sw.js').read_text()


@pytest.mark.skipif(NODE is None, reason='node is not installed')
def test_sw_leaves_proxied_images_to_the_browser_even_when_public():
    urls = [f'{O}/api/img/plex?url=/library/metadata/1/thumb/2', f'{O}/api/img/tmdb?path=/a.jpg&size=w500']
    r = _sw(urls, {'ok': True, 'headers': {'Cache-Control': 'public, max-age=604800'}})
    assert not any(r['handled'][u] for u in urls)
    assert not any('/api/' in p for p in r['puts'])


@pytest.mark.skipif(NODE is None, reason='node is not installed')
def test_sw_never_stores_an_api_reply_reached_from_a_static_url():
    # A static request that ends up answered by /api/img (e.g. a redirect) is not kept.
    urls = [f'{O}/static/images/poster.png', f'{O}/assets/index-abc123.js']
    r = _sw(urls, {'ok': True, 'url': f'{O}/api/img/plex?url=/library/metadata/1/thumb/2', 'headers': {}})
    assert all(r['handled'][u] for u in urls)
    assert not [p for p in r['puts'] if p != '/']
