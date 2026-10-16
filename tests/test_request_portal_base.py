"""The request page follows the URL base (round 4, item 5)."""

import json
import re
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app.main

ROOT = Path(__file__).resolve().parent.parent
WAN = {'REMOTE_ADDR': '203.0.113.9'}
PROXY = {'REMOTE_ADDR': '10.0.0.1'}


@pytest.fixture
def portal(test_app):
    app.main.settings.update({'request_portal': {
        'enabled': True, 'password_hash': generate_password_hash('letmein'), 'hostname': 'req.example.com'}})
    yield test_app
    app.main.settings.update({'request_portal': {'enabled': False, 'password_hash': '', 'hostname': ''}})
    test_app.config['URL_BASE'] = ''


def _set_cookie(resp, name):
    return next(c for c in resp.headers.getlist('Set-Cookie') if c.startswith(name + '='))


def test_page_at_root(portal):
    page = portal.test_client().get('/request', environ_base=WAN).get_data(as_text=True)
    assert '<meta name="url-base" content="">' in page
    assert 'src="/static/request/request.js' in page


def test_page_under_base(portal):
    portal.config['URL_BASE'] = '/luma'
    page = portal.test_client().get('/request/', environ_base=WAN).get_data(as_text=True)
    assert '<meta name="url-base" content="/luma">' in page
    for asset in ('/static/request/manifest.json', '/static/images/apple-touch-icon.png',
                  '/static/request/request.css', '/static/images/luma_text.png', '/static/request/request.js'):
        assert f'"/luma{asset}' in page
    assert not re.search(r'="/static/', page)


def test_unlock_cookie_secure_from_trusted_scheme_only_and_scoped_to_base(portal):
    portal.config['URL_BASE'] = '/luma'
    c = portal.test_client()
    hdrs = {'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '203.0.113.9'}
    r = c.post('/api/request/unlock', json={'name': 'Sam', 'password': 'letmein'}, headers=hdrs, environ_base=PROXY)
    cookie = _set_cookie(r, 'luma_req')
    assert 'Secure' not in cookie            # 10.0.0.1 isn't a trusted proxy
    assert 'Path=/luma' in cookie

    app.main.settings.update({'security': {'trusted_proxies': '10.0.0.1'}})
    r = c.post('/api/request/unlock', json={'name': 'Sam', 'password': 'letmein'}, headers=hdrs, environ_base=PROXY)
    assert 'Secure' in _set_cookie(r, 'luma_req')
    r = c.post('/api/request/signout', headers=hdrs, environ_base=PROXY)
    assert 'Path=/luma' in _set_cookie(r, 'luma_req')


@pytest.mark.parametrize('path,status', [
    ('/static/images/apple-touch-icon.png', 200),
    ('/static/images/luma_text.png', 200),
    ('/static/request/request.js', 200),
    ('/static/images/plex.png', 404),
    ('/api/settings', 404),
])
def test_portal_host_assets(portal, path, status):
    r = portal.test_client().get(path, headers={'Host': 'req.example.com'}, environ_base=WAN)
    assert r.status_code == status


def test_request_js_uses_base_for_every_url():
    src = (ROOT / 'static/request/request.js').read_text()
    assert re.search(r"meta\[name=\"url-base\"\]", src)
    assert not re.search(r"fetch\(\s*['\"`]/", src)
    assert not re.search(r"(?<!url\()['\"`]/static/", src)


def test_manifest_is_relative():
    m = json.loads((ROOT / 'static/request/manifest.json').read_text())
    for value in (m['start_url'], m['scope'], *[i['src'] for i in m['icons']]):
        assert not value.startswith('/')
