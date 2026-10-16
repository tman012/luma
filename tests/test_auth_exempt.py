"""What a signed-out visitor can reach (round 4, item 7)."""

import pytest

import app.main
import app.user_auth as user_auth

WAN = {'REMOTE_ADDR': '203.0.113.9'}
LAN = {'REMOTE_ADDR': '192.168.1.20'}
NEW = {'name': 'guest', 'email': 'guest@example.com', 'pin': 'abcd1234'}


@pytest.fixture
def client(test_app, monkeypatch):
    user_auth.create_user('owner', '1234', is_admin=True)
    monkeypatch.setattr(app.main, '_send_account_request_notification', lambda *a, **k: None)
    return test_app.test_client()


def test_check_user_removed(client):
    for env in (WAN, LAN):
        r = client.post('/api/auth/check-user', json={'name': 'owner'}, environ_base=env)
        assert r.status_code in (401, 404, 405)
        assert b'owner' not in r.data


def test_remote_register_off_by_default(client):
    r = client.post('/api/auth/register', json=NEW, environ_base=WAN)
    assert r.status_code == 403
    assert user_auth.get_user_by_email('guest@example.com') is None
    me = client.get('/api/auth/me', environ_base=WAN)
    assert me.status_code == 401 and me.get_json()['signup'] is False


def test_remote_register_when_allowed(client):
    app.main.settings.update({'security': {'allow_signup': True}})
    try:
        assert client.get('/api/auth/me', environ_base=WAN).get_json()['signup'] is True
        r = client.post('/api/auth/register', json=NEW, environ_base=WAN)
        assert r.status_code == 200
        assert user_auth.get_user_by_email('guest@example.com')['status'] == 'pending'
    finally:
        app.main.settings.update({'security': {'allow_signup': False}})


def test_local_register_always_allowed(client):
    # Restore the global setting so later tests (e.g. hero file serving)
    # are not left behind an auth wall; this leaked under some random orders.
    previous = app.main.settings.get('security.auth_required')
    app.main.settings.update({'security': {'auth_required': 'enabled'}})
    try:
        r = client.post('/api/auth/register', json=NEW, environ_base=LAN)
        assert r.status_code == 200
    finally:
        app.main.settings.update({'security': {'auth_required': previous}})


def test_portal_host_never_registers(client):
    app.main.settings.update({'security': {'allow_signup': True},
                              'request_portal': {'hostname': 'req.example.com'}})
    try:
        r = client.post('/api/auth/register', json=NEW, headers={'Host': 'req.example.com'}, environ_base=WAN)
        assert r.status_code in (403, 404)
    finally:
        app.main.settings.update({'security': {'allow_signup': False}, 'request_portal': {'hostname': ''}})


@pytest.mark.parametrize('path,method,exempt', [
    ('/api/request/session', 'GET', True),
    ('/api/request/unlock', 'POST', True),
    ('/api/request/movie/603', 'GET', True),
    ('/api/request/movie/603', 'POST', True),
    ('/api/request/admin/settings', 'GET', False),
    ('/api/request/admin/log', 'GET', False),
    ('/api/request/anything-new', 'GET', False),
    ('/api/request/look/movie/603', 'POST', False),
    ('/static/request/request.js', 'GET', True),
    ('/static/images/luma_text.png', 'GET', True),
    ('/static/js/movie-orb-screensaver.js', 'GET', False),
    ('/static/hero_cache/x.jpg', 'GET', False),
    ('/static/spa/index.html', 'GET', True),
    ('/api/auth/check-user', 'POST', False),
    ('/api/auth/register', 'POST', True),
    ('/login', 'GET', True),
    ('/request', 'GET', True),
    ('/request/', 'GET', True),
    ('/api/settings', 'GET', False),
])
def test_exempt_list(path, method, exempt):
    assert app.main._is_auth_exempt(path, method) is exempt


def test_unlisted_request_path_needs_auth(client):
    r = client.get('/api/request/admin/settings', environ_base=WAN)
    assert r.status_code == 401


def test_security_hostnames_validated(client):
    r = client.put('/api/security', json={'hostnames': 'luma.lan, bad_host!'}, environ_base=LAN)
    assert r.status_code == 400
    r = client.put('/api/security', json={'hostnames': 'Luma.LAN docker-server', 'allow_signup': True},
                   environ_base=LAN)
    assert r.status_code == 200
    got = client.get('/api/security', environ_base=LAN).get_json()
    assert got['hostnames'] == 'luma.lan, docker-server'
    assert got['allow_signup'] is True
    client.put('/api/security', json={'hostnames': '', 'allow_signup': False}, environ_base=LAN)


def test_request_page_reachable_signed_out(client):
    app.main.settings.update({'request_portal': {'hostname': 'req.example.com'}})
    try:
        for headers in ({}, {'Host': 'req.example.com'}):
            r = client.get('/request', headers=headers, environ_base=WAN)
            assert r.status_code == 200, headers
            assert b'gate-form' in r.data
    finally:
        app.main.settings.update({'request_portal': {'hostname': ''}})


def test_spa_no_longer_calls_check_user():
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / 'frontend' / 'src'
    for f in src.rglob('*.ts*'):
        assert 'check-user' not in f.read_text(encoding='utf-8'), f
