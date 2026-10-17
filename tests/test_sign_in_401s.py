"""Tester ab1c841 #2/#3: every 401 that a sign-in would fix says so with sign_in: true,
so the UI can offer a Sign in button: the global guard (remote, no session; minimal,
expired cookie), and the per-route checks behind it."""
import pytest

from tests.test_home_rails import REMOTE
from tests.test_file_management import HDR
from tests.test_signed_in_owner_guard import full  # noqa: F401  (fixture)

EXPIRED = 'expired-or-forged-session-token'


def _body(r):
    assert r.status_code == 401, r.get_data(as_text=True)
    return r.get_json()


@pytest.mark.parametrize('path', ['/api/trackers', '/api/settings', '/api/home/rails', '/api/downloads-guard/status'])
def test_remote_no_session_401_says_sign_in(full, path):
    import app.main
    app.main.settings.update({'security': {'auth_required': 'enabled'}})
    r = full.test_client().get(path, environ_base=REMOTE, headers=HDR)
    assert r.headers['Cache-Control'] == 'private, no-store'
    b = _body(r)
    assert b == {'error': 'Authentication required', 'sign_in': True}


def test_remote_expired_cookie_401_says_sign_in(full):
    import app.main
    app.main.settings.update({'security': {'auth_required': 'enabled'}})
    c = full.test_client()
    c.set_cookie('luma_auth_session', EXPIRED)
    assert _body(c.get('/api/trackers', environ_base=REMOTE, headers=HDR))['sign_in'] is True


@pytest.mark.parametrize('env', [{'REMOTE_ADDR': '192.168.1.20'}, REMOTE])
def test_minimal_expired_cookie_401_says_sign_in(test_app, monkeypatch, env):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    c = test_app.test_client()
    c.set_cookie('luma_auth_session', EXPIRED)
    for method, url in [('GET', '/api/trackers'), ('PUT', '/api/trackers/1/enabled'), ('POST', '/api/trackers/test')]:
        r = c.open(url, method=method, json={} if method != 'GET' else None, environ_base=env, headers=HDR)
        assert _body(r).get('sign_in') is True, (method, url)


def test_helpers_say_sign_in(test_app):
    import app.main
    from app import tracker_sources
    with test_app.test_request_context('/'):
        from flask import g
        g.current_user = None
        for fn in (app.main._require_auth, app.main._require_admin):
            resp, code = fn()
            assert code == 401 and resp.get_json()['sign_in'] is True
        resp, code = tracker_sources.member_denied(lambda: None, False, lambda u: False)
        assert code == 401 and resp.get_json()['sign_in'] is True
        resp, code = tracker_sources.member_denied(lambda: {'id': 1}, True, lambda u: True)
        assert code == 401 and resp.get_json()['sign_in'] is True


def test_403s_never_say_sign_in(full):
    from tests.test_home_rails import _client
    c = _client(full, admin=False, name='plain')
    r = c.get('/api/trackers', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 403 and 'sign_in' not in r.get_json()
