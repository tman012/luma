"""PM ruling: destructive file actions need the owner signed in with a session,
even with LAN sign-in off (start_flow.require_signed_in_owner). Everything
else keeps today's LAN behavior."""
import pytest
from flask import Flask

from app import start_flow, storage_cleanup
from tests.test_file_management import HDR
from tests.test_home_rails import REMOTE, _client

CLEANUP = '/api/storage/downloads/cleanup'
LAN = {'REMOTE_ADDR': '192.168.1.20'}
KEY = 'svc-' + 'k' * 20


@pytest.fixture
def full(test_app, monkeypatch):
    import app.main
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    calls = []
    monkeypatch.setattr(storage_cleanup, 'run_cleanup',
                        lambda target, **kw: (calls.append(target), {'success': True})[1])
    monkeypatch.setattr(app.main, '_SERVICE_API_KEY', KEY)
    app.main.settings.update({'security': {'auth_required': 'disabled_local'}})
    test_app.calls = calls
    return test_app


def _post(c, env=LAN, headers=HDR):
    return c.post(CLEANUP, json={'target_bytes': 5}, environ_base=env, headers=headers)


@pytest.mark.parametrize('env', [LAN, REMOTE])
def test_the_signed_in_owner_may(full, env):
    owner = _client(full)
    r = _post(owner, env)
    assert r.status_code == 200 and full.calls == [5]


def test_the_implicit_lan_owner_gets_a_sign_in_401(full):
    _client(full)
    r = _post(full.test_client())
    assert r.status_code == 401
    assert r.get_json() == {'error': 'Sign in first', 'sign_in': True}
    assert r.headers['Cache-Control'] == 'no-store'
    assert full.calls == []


@pytest.mark.parametrize('env', [LAN, REMOTE])
def test_the_service_key_gets_401(full, env):
    _client(full)
    r = _post(full.test_client(), env, dict(HDR, **{'X-API-Key': KEY}))   # kiosk and companion apps
    assert r.status_code == 401 and r.get_json()['sign_in'] is True
    assert full.calls == []


def test_no_session_from_outside_gets_401(full):
    _client(full)
    assert _post(full.test_client(), REMOTE).status_code == 401


@pytest.mark.parametrize('who', ['admin', 'viewer', 'guest'])
def test_others_get_403(full, who):
    _client(full)                                             # the owner
    c = _client(full, admin=(who == 'admin'), name=who)       # another admin, a viewer, a guest
    r = _post(c)
    assert r.status_code == 403 and r.get_json() == {'error': 'Owner only'}
    assert full.calls == []


def test_a_plex_approved_viewer_gets_403(full, monkeypatch):
    from app import user_auth
    _client(full)
    c = _client(full, admin=False, name='plexer')
    monkeypatch.setattr(user_auth, 'is_plex_approved', lambda u: True)
    assert _post(c).status_code == 403 and full.calls == []


def test_non_destructive_routes_keep_lan_access(full):
    """Implicit LAN still reaches the full build's admin routes."""
    _client(full)
    c = full.test_client()
    for url in ('/api/admin/users', '/api/security'):
        r = c.get(url, environ_base=LAN, headers=HDR)
        assert r.status_code == 200, url
    me = c.get('/api/auth/me', environ_base=LAN, headers=HDR).get_json()
    assert me['authenticated'] is True and me['implicit'] is True


def test_the_decorator_on_any_route(full, monkeypatch):
    """require_signed_in_owner is reusable: a future TV or movie delete gets
    the same answers by decorating its route."""
    hit = []

    @start_flow.signed_in_owner_required
    def delete_something():
        hit.append(1)
        return 'deleted'
    owner = _client(full)
    with full.test_request_context('/x', environ_base=LAN):
        import app.main
        app.main.load_current_user()
        resp = delete_something()
        assert resp[1] == 401 and hit == []
    with full.test_request_context('/x', environ_base=LAN,
                                   headers={'Cookie': owner.get_cookie('luma_auth_session').key + '='
                                            + owner.get_cookie('luma_auth_session').value}):
        app.main.load_current_user()
        assert delete_something() == 'deleted' and hit == [1]
