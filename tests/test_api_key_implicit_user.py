"""Remote X-API-Key requests must not 500 (round 3, item 2)."""

import pytest
from flask import g, jsonify


@pytest.fixture
def api_key(test_app, monkeypatch):
    import app.main
    monkeypatch.setattr(app.main, '_SERVICE_API_KEY', 'test-service-key')
    return 'test-service-key'


def test_remote_api_key_request_ok(test_app, api_key):
    r = test_app.test_client().get('/api/auth/me', headers={'X-API-Key': api_key},
                                   environ_base={'REMOTE_ADDR': '203.0.113.100'})
    assert r.status_code == 200
    assert r.get_json()['implicit'] is False


def test_remote_api_key_html_route_ok(test_app, api_key):
    """_auth_guard runs on non-API pages too; it must read g safely."""
    r = test_app.test_client().get('/settings', headers={'X-API-Key': api_key},
                                   environ_base={'REMOTE_ADDR': '203.0.113.100'})
    assert r.status_code != 500
    assert '/login' not in r.headers.get('Location', '')


def test_api_key_sets_service_user_and_implicit_false(test_app, api_key):
    import app.main
    with test_app.test_request_context('/api/auth/me', headers={'X-API-Key': api_key},
                                       environ_base={'REMOTE_ADDR': '203.0.113.100'}):
        app.main.load_current_user()
        assert g.current_user['name'] == 'Service'
        assert g.implicit_user is False
        assert app.main._auth_guard() is None


def test_auth_guard_without_load_current_user(test_app):
    """Even if g was never populated, _auth_guard must not raise."""
    import app.main
    app.main.settings.update({'security': {'auth_required': 'enabled'}})
    with test_app.test_request_context('/api/settings', environ_base={'REMOTE_ADDR': '203.0.113.100'}):
        resp = app.main._auth_guard()
        assert resp[1] == 401


def test_invalid_api_key_rejected(test_app, api_key):
    r = test_app.test_client().get('/api/settings', headers={'X-API-Key': 'wrong'},
                                   environ_base={'REMOTE_ADDR': '203.0.113.100'})
    assert r.status_code == 401
