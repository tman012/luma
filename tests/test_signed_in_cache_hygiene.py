"""Signed-in browser caching: private /api replies vary by the session cookie,
and sign-out clears the browser's HTTP cache (Clear-Site-Data: "cache")."""

from unittest.mock import patch

import pytest
from flask import Response

import app.main as main
import app.user_auth as user_auth

WAN = {'REMOTE_ADDR': '203.0.113.9'}


@pytest.fixture
def client(test_app):
    user_auth.init_db()
    user_auth.create_user('owner', '1234', is_admin=True)
    return test_app.test_client()


def _session(client):
    user = user_auth.authenticate('owner', '1234')
    token = user_auth.create_session(user['id'])
    client.set_cookie(main.AUTH_COOKIE, token)
    return token


def _process(path, cache_control, method='GET', status=200):
    with main.app.test_request_context(path, method=method):
        resp = Response(b'x', status=status, headers={'Cache-Control': cache_control})
        return main.app.process_response(resp)


@pytest.mark.parametrize('path', ['/api/img/plex?url=/library/metadata/1/thumb/2', '/api/img/tmdb?path=/a.jpg'])
def test_private_cacheable_image_reply_varies_by_cookie(path):
    resp = _process(path, 'private, max-age=86400')
    assert 'cookie' in [v.strip().lower() for v in resp.headers.get('Vary', '').split(',')]
    assert resp.headers['Cache-Control'] == 'private, max-age=86400'  # the cache itself is untouched


@pytest.mark.parametrize('cc', ['private, no-store', 'public, max-age=604800'])
def test_no_vary_when_not_private_or_not_stored(cc):
    assert 'Cookie' not in _process('/api/img/plex?url=/x', cc).headers.get('Vary', '')


def test_non_api_paths_untouched():
    assert 'Cookie' not in _process('/static/images/a.png', 'private, max-age=60').headers.get('Vary', '')


def test_private_json_reply_varies_by_cookie(client):
    _session(client)
    with patch('app.rt_top_reviews.get_top_reviews', return_value={'source': None, 'reviews': [], 'url': None}):
        r = client.get('/api/reviews/top?title=Heat&year=1995', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert r.status_code == 200
    assert r.headers['Cache-Control'] == 'private, max-age=600'
    assert 'Cookie' in r.headers.get('Vary', '')


def test_every_signed_in_plex_image_reply_is_safe(client):
    # Whatever the proxy answers, a stored copy is never shared across sessions.
    _session(client)
    r = client.get('/api/img/plex?url=/library/metadata/1/thumb/2', environ_base=WAN)
    cc = r.headers.get('Cache-Control', '')
    assert 'no-store' in cc or 'Cookie' in r.headers.get('Vary', '')


def test_api_logout_clears_browser_cache(client):
    token = _session(client)
    r = client.post('/api/auth/logout', headers={'Origin': 'http://localhost'}, environ_base=WAN)
    assert r.status_code == 200
    assert r.headers.get('Clear-Site-Data') == '"cache"'
    assert user_auth.validate_session(token) is None


def test_form_logout_clears_browser_cache(client):
    _session(client)
    r = client.post('/logout', headers={'Origin': 'http://localhost'}, environ_base=WAN)
    assert r.status_code == 302
    assert r.headers.get('Clear-Site-Data') == '"cache"'


def test_refused_logout_does_not_clear(client):
    _session(client)
    r = client.post('/logout', headers={'Origin': 'http://evil.example'}, environ_base=WAN)
    assert r.status_code == 403
    assert 'Clear-Site-Data' not in r.headers


def test_other_auth_calls_do_not_clear(client):
    _session(client)
    r = client.get('/api/auth/me', environ_base=WAN)
    assert 'Clear-Site-Data' not in r.headers
