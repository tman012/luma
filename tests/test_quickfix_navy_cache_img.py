"""Quick-fix round: navy on server pages, cache busting, the Plex image proxy
allowlist, minimal-mode sign-up off and image URLs under the URL base."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.main
from app import theme

ROOT = Path(__file__).resolve().parent.parent
LAN = {'REMOTE_ADDR': '192.168.1.20'}


# -- navy ---------------------------------------------------------------------

def test_admin_action_page_is_navy_with_blue_pill(test_app):
    with test_app.test_request_context('/x'):
        r = app.main._admin_action_page('Approved', 'Done', form={'a': '1'}, button='Confirm')
    html = r.get_data(as_text=True)
    assert theme.NAVY_BG in html and '#1a1a1c' not in html
    assert f'border-radius:999px;background:{theme.LUMA_BLUE}' in html


def test_manifest_and_static_colors_are_navy(test_app):
    body = test_app.test_client().get('/manifest.json', environ_base=LAN).json
    assert body['background_color'] == '#15102a' and body['theme_color'] == '#15102a'
    static = json.loads((ROOT / 'static/manifest.json').read_text())
    assert static['background_color'] == static['theme_color'] == '#15102a'
    html = (ROOT / 'frontend/index.html').read_text()
    assert '<meta name="theme-color" content="#15102a">' in html and '#000000' not in html


def test_request_portal_has_no_purples():
    css = (ROOT / 'static/request/request.css').read_text().lower()
    for purple in ('#7c6cff', '#9370db', '147,112,219', '#231e37'):
        assert purple not in css


# -- cache busting -------------------------------------------------------------

def test_sw_and_index_are_never_cached(test_app):
    c = test_app.test_client()
    for path in ('/sw.js', '/static/sw.js'):
        r = c.get(path, environ_base=LAN)
        assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-cache', path
    sw = (ROOT / 'static/sw.js').read_text()
    assert "CACHE_VERSION = 'media-kiosk-v115'" in sw
    assert "fetch(event.request, { cache: 'no-store' })" in sw
    assert 'self.skipWaiting()' in sw and 'self.clients.claim()' in sw


def test_hashed_assets_stay_immutable():
    src = (ROOT / 'app/main.py').read_text()
    block = src.split('def spa_assets')[1][:400]
    assert "'public, max-age=31536000, immutable'" in block


# -- image proxy -----------------------------------------------------------------

class _Resp:
    status_code = 200
    content = b'img'

    def __init__(self, ctype='image/jpeg'):
        self.headers = {'Content-Type': ctype}


@pytest.fixture
def proxy(test_app, monkeypatch):
    calls = []
    state = {'ctype': 'image/jpeg'}
    monkeypatch.setattr(app.main, 'get_plex_for_request',
                        lambda: SimpleNamespace(plex_url='http://plex.test', plex_token='t'))

    def fake_get(url, **k):
        calls.append(url)
        return _Resp(state['ctype'])
    monkeypatch.setattr(app.main.requests, 'get', fake_get)
    return test_app.test_client(), calls, state


@pytest.mark.parametrize('url', [
    'http://evil.example/x.jpg', '//evil.example/x.jpg', '/library/metadata/1/thumb/../../x',
    '/library/parts/5/file.mkv', '/library/metadata/1/media', '/photo/:/transcode?url=http://evil.example/x.jpg',
    '/photo/:/transcode?url=/library/parts/1/file', '/:/prefs',
])
def test_proxy_rejects_non_image_paths(proxy, url):
    c, calls, _ = proxy
    assert c.get('/api/img/plex', query_string={'url': url}, environ_base=LAN).status_code == 400
    assert calls == []


@pytest.mark.parametrize('url', [
    '/library/metadata/5/thumb', '/library/metadata/5/art/1700000000', '/library/collections/9/thumb/1',
    '/library/metadata/5/clearLogo',
])
def test_proxy_allows_library_images_privately(proxy, url):
    c, calls, _ = proxy
    r = c.get('/api/img/plex', query_string={'url': url}, environ_base=LAN)
    assert r.status_code == 200 and r.headers['Cache-Control'].startswith('private')
    assert r.headers['Content-Type'].startswith('image/')
    assert calls == [f'http://plex.test{url}']


def test_proxy_transcode_uses_only_the_inner_library_path(proxy):
    c, calls, _ = proxy
    r = c.get('/api/img/plex', query_string={'url': '/photo/:/transcode?url=/library/metadata/5/thumb/1&width=99'},
              environ_base=LAN)
    assert r.status_code == 200
    assert calls == ['http://plex.test/library/metadata/5/thumb/1']


@pytest.mark.parametrize('ctype', ['text/html', 'application/json', 'image/svg+xml'])
def test_proxy_refuses_non_images(proxy, ctype):
    c, _, state = proxy
    state['ctype'] = ctype
    r = c.get('/api/img/plex', query_string={'url': '/library/metadata/5/thumb'}, environ_base=LAN)
    assert r.status_code == 502 and r.headers['Cache-Control'].startswith('private')


# -- minimal mode: no sign-up ------------------------------------------------------

def test_minimal_mode_has_no_signup(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    c = test_app.test_client()
    r = c.post('/api/auth/register', json={'name': 'x', 'email': 'x@example.com', 'pin': 'pass-word-1'},
               environ_base=LAN)
    assert r.status_code == 404
    with test_app.test_request_context('/', environ_base=LAN):
        assert app.main._signup_open() is False


# -- image URLs carry the URL base once --------------------------------------------

def test_home_image_urls_include_url_base_once(test_app, monkeypatch):
    from app import home_rails
    monkeypatch.setitem(test_app.config, 'URL_BASE', '/luma')
    with test_app.app_context():
        url = home_rails._img('/library/metadata/1/thumb', 300)
    assert url == '/luma/api/img/plex?url=%2Flibrary%2Fmetadata%2F1%2Fthumb&w=300'
    assert not re.search(r'/luma/luma/', url)
