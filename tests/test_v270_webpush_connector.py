"""v273: Settings > Connectors Web Push (prod 1b4ea74 Services keys). Owner only; Test mocked."""
import pytest

import app.main
from app import webpush_connector
from tests.test_home_rails import REMOTE, _client

HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}
API = '/api/settings/connectors/webpush'


@pytest.fixture
def minimal(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    from app import start_flow
    monkeypatch.setattr(start_flow, '_load_state', lambda: {})
    saved = {
        'enabled': bool(app.main.settings.get('notifications.web_push.enabled')),
        'vapid_email': app.main.settings.get('notifications.web_push.vapid_email') or '',
    }
    app.main.settings.update({'notifications': {'web_push': {'enabled': False, 'vapid_email': ''}}})
    webpush_connector._tests.clear()
    yield test_app
    app.main.settings.update({'notifications': {'web_push': saved}})
    webpush_connector._tests.clear()


def test_paths_allowlisted():
    from app import minimal
    assert minimal.path_allowed(API, 'PUT') and minimal.path_allowed(API + '/test', 'POST')


def test_save_enabled_and_email(minimal):
    c = _client(minimal)
    r = c.put(API, json={'enabled': True, 'vapid_email': 'mailto:you@example.com'},
              environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    body = r.get_json()
    assert body['enabled'] is True
    assert body['vapid_email'] == 'mailto:you@example.com'


def test_test_with_no_subscribers(minimal, monkeypatch):
    monkeypatch.setattr('app.push_service.get_subscription_count', lambda: 0)
    c = _client(minimal)
    r = c.post(API + '/test', json={}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    assert r.get_json()['ok'] is False
    assert r.get_json()['error'] == 'No subscribers'
