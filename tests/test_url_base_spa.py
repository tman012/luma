"""The built SPA loads under a URL base (round 3, item 11).

Needs a build in static/spa (cd frontend && npm ci && npm run build, then copy
dist/ to static/spa, as the Dockerfile does). CI fails if this test skips.
"""
import os
import re
from pathlib import Path

import pytest
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.test import Client
from werkzeug.wrappers import Response

STATIC = Path(os.environ.get('FLASK_STATIC_FOLDER') or Path(__file__).resolve().parents[1] / 'static')
BUILT = (STATIC / 'spa' / 'index.html').exists()

pytestmark = pytest.mark.skipif(not BUILT, reason="Built SPA not found. Run 'npm run build' in frontend/")


def _not_found(environ, start_response):
    start_response('404 Not Found', [('Content-Type', 'text/plain')])
    return [b'Not Found']


@pytest.fixture
def client(test_app, monkeypatch):
    """The app mounted at /luma, the way startup mounts it when URL_BASE is set."""
    monkeypatch.setattr(test_app, 'static_folder', str(STATIC))
    test_app.config['URL_BASE'] = '/luma'
    yield Client(DispatcherMiddleware(_not_found, {'/luma': test_app.wsgi_app}), Response)
    test_app.config['URL_BASE'] = ''


def _lan(client, path):
    return client.get(path, environ_base={'REMOTE_ADDR': '192.168.1.20'})


@pytest.mark.parametrize('page', ['/luma/', '/luma/library', '/luma/settings'])
def test_index_and_bundle_load_under_base(client, page):
    r = _lan(client, page)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'window.__LUMA_URL_BASE__ = "/luma"' in html
    assets = re.findall(r'(?:src|href)="([^"]*assets/[^"]+)"', html)
    assert assets, 'index.html references no bundle'
    for url in assets:
        assert url.startswith('/luma/assets/'), url
        a = _lan(client, url)
        assert a.status_code == 200, url
        assert len(a.data) > 0
    assert 'href="/luma/manifest.json"' in html
    assert not re.search(r'(?:src|href)="/(?!luma/)(?:assets|static)/', html)


def test_js_chunks_have_no_root_relative_asset_urls(client):
    html = _lan(client, '/luma/').get_data(as_text=True)
    js = [u for u in re.findall(r'src="([^"]+\.js)"', html)]
    for url in js:
        body = _lan(client, url).get_data(as_text=True)
        assert '"/assets/' not in body and "'/assets/" not in body, url


def test_manifest_icons_prefixed(client):
    man = _lan(client, '/luma/manifest.json').get_json()
    assert man['start_url'] == '/luma/'
    assert man['scope'] == '/luma/'
    for icon in man['icons']:
        assert icon['src'].startswith('/luma/static/')
        assert _lan(client, icon['src']).status_code == 200


def test_outside_base_is_404(client):
    assert _lan(client, '/health').status_code == 404
    assert _lan(client, '/luma/health').status_code == 200
