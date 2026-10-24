"""v270: Settings > Connectors Telegram. Owner only; token never returned; getMe mocked."""
import json

import pytest

import app.main
from app import telegram_connector
from tests.test_home_rails import REMOTE, _client

HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}
API = '/api/settings/connectors/telegram'
TOKEN = '123456789:AAHtesttoken_FOR_UNIT_TESTS_ONLY'


@pytest.fixture
def minimal(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    from app import start_flow
    monkeypatch.setattr(start_flow, '_load_state', lambda: {})
    cfg = tmp_path / 'telegram_bot.json'
    monkeypatch.setattr(telegram_connector, '_path', lambda: cfg)
    telegram_connector._tests.clear()
    telegram_connector._get_me = lambda tok: (
        {'ok': True, 'message': 'Connected'} if tok == TOKEN else {'ok': False, 'error': 'Invalid token'}
    )
    yield test_app
    telegram_connector._get_me = None
    telegram_connector._tests.clear()


def test_paths_allowlisted():
    from app import minimal
    assert minimal.path_allowed(API, 'PUT') and minimal.path_allowed(API + '/test', 'POST')


def test_get_masks_token(minimal):
    telegram_connector._save({'bot_token': TOKEN, 'admin_chat_id': '42'})
    c = _client(minimal)
    r = c.get(API, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    body = r.get_json()
    assert body['bot_token'] == {'set': True, 'last4': TOKEN[-4:]}
    assert body['chat_id'] == '42'
    assert TOKEN not in r.data.decode()


def test_save_and_test(minimal):
    c = _client(minimal)
    r = c.put(API, json={'bot_token': TOKEN, 'chat_id': '-1001'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    assert r.get_json()['bot_token']['set'] is True
    assert r.get_json()['chat_id'] == '-1001'
    stored = json.loads(telegram_connector._path().read_text())
    assert stored['bot_token'] == TOKEN
    assert 'AAH' in stored['bot_token']  # file has it
    # Response must not echo full token
    assert TOKEN not in r.data.decode()

    t = c.post(API + '/test', json={}, environ_base=REMOTE, headers=HDR)
    assert t.status_code == 200 and t.get_json()['ok'] is True


def test_remove(minimal):
    telegram_connector._save({'bot_token': TOKEN, 'admin_chat_id': '1'})
    c = _client(minimal)
    r = c.put(API, json={'bot_token': '', 'chat_id': ''}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    assert r.get_json()['bot_token']['set'] is False
