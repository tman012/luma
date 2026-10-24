"""Settings > Connectors: Home Assistant. Owner only, access token never
returned, Test/Save go through ha_service.test_connection (mocked; never a
real HA host)."""

import logging

import pytest
import requests as _rq

import app.main
from app import ha_connector, ha_service
from tests.test_home_rails import REMOTE, _client

HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}
API = '/api/settings/connectors/homeassistant'
# Test-only placeholder; never a real token.
TOKEN = 'ha-test-token-abcdefghij'
URL = 'http://ha.example:8123'


@pytest.fixture
def minimal(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    from app import start_flow
    monkeypatch.setattr(start_flow, '_load_state', lambda: {})
    saved = {
        'url': app.main.settings.get('homeassistant.url') or '',
        'access_token': app.main.settings.get('homeassistant.access_token') or '',
    }
    app.main.settings.update({'homeassistant': {'url': '', 'access_token': ''}})
    yield test_app
    app.main.settings.update({'homeassistant': saved})
    ha_connector._tests.clear()


class FakeHA:
    """Stands in for requests inside ha_service: never the network."""

    exceptions = _rq.exceptions

    def __init__(self):
        self.calls = []
        self.fail = None
        self.status = 200
        self.body = {'message': 'API running.'}

    def get(self, url, headers=None, **kw):
        auth = (headers or {}).get('Authorization', '')
        self.calls.append({'url': url, 'auth': auth, 'kw': kw})
        if self.fail:
            raise self.fail
        # Never echo a token into a response body.
        if 'Bearer ' not in auth or auth.split(' ', 1)[1] != TOKEN:
            return _Resp(401, {'message': 'Unauthorized'})
        return _Resp(self.status, self.body)

    def post(self, *a, **kw):
        raise AssertionError('HA connector must not POST during Test/Save')


class _Resp:
    def __init__(self, code, body):
        self.status_code = code
        self._body = body
        self.text = str(body)
        self.headers = {'content-type': 'application/json'}

    def json(self):
        return self._body


@pytest.fixture
def fake(monkeypatch):
    f = FakeHA()
    monkeypatch.setattr(ha_service, 'requests', f)
    return f


def _put(c, body):
    return c.put(API, json=body, environ_base=REMOTE, headers=HDR)


def _test(c, body):
    return c.post(API + '/test', json=body, environ_base=REMOTE, headers=HDR)


def _get(c):
    return c.get(API, environ_base=REMOTE, headers=HDR)


def _set(url=URL, token=TOKEN):
    app.main.settings.update({'homeassistant': {'url': url, 'access_token': token}})


def test_paths_allowlisted_in_minimal_and_not_signed_out():
    from app import minimal
    for p in (API, API + '/test'):
        assert minimal.path_allowed(p, 'PUT') and minimal.path_allowed(p, 'POST')
        assert p not in minimal.SIGNED_OUT_PATHS


def test_owner_only(minimal, fake):
    c = app.main.app.test_client()
    r = c.put(API, json={'url': URL, 'access_token': TOKEN}, headers=HDR)
    assert r.status_code in (401, 403)
    assert fake.calls == []


def test_fresh_install_starts_empty(minimal):
    c = _client(minimal)
    assert _get(c).get_json() == {'url': '', 'access_token': {'set': False, 'last4': ''}}


def test_prefills_from_settings_and_never_returns_token(minimal):
    _set()
    c = _client(minimal)
    body = _get(c).get_json()
    assert body == {'url': URL, 'access_token': {'set': True, 'last4': TOKEN[-4:]}}
    raw = _get(c).get_data(as_text=True)
    assert TOKEN not in raw


def test_save_writes_homeassistant_section_rooms_power_reads(minimal, fake, caplog):
    caplog.set_level(logging.DEBUG)
    c = _client(minimal)
    r = _put(c, {'url': URL, 'access_token': TOKEN})
    assert r.status_code == 200
    assert r.get_json() == {'url': URL, 'access_token': {'set': True, 'last4': TOKEN[-4:]}}
    assert app.main.settings.get('homeassistant.url') == URL
    assert app.main.settings.get('homeassistant.access_token') == TOKEN
    assert TOKEN not in caplog.text
    assert fake.calls and fake.calls[-1]['url'] == f'{URL}/api/'


def test_save_keeps_stored_token_when_left_blank_or_masked(minimal, fake):
    _set()
    c = _client(minimal)
    r = _put(c, {'url': URL})
    assert r.status_code == 200
    assert app.main.settings.get('homeassistant.access_token') == TOKEN
    r = _put(c, {'url': URL, 'access_token': '********'})
    assert r.status_code == 200
    assert app.main.settings.get('homeassistant.access_token') == TOKEN


def test_new_url_needs_the_token_again(minimal, fake):
    _set()
    c = _client(minimal)
    r = _put(c, {'url': 'http://other.example:8123'})
    assert r.status_code == 400
    assert r.get_json()['fields']['access_token'] == 'Required'
    assert app.main.settings.get('homeassistant.url') == URL


def test_save_validates(minimal, fake):
    c = _client(minimal)
    r = _put(c, {'url': '', 'access_token': TOKEN})
    # empty URL + token is treated as remove only when BOTH empty; with token only → required url
    assert r.status_code == 400
    r = _put(c, {'url': 'not-a-url', 'access_token': TOKEN})
    assert r.status_code == 400
    assert r.get_json()['fields']['url'] == 'Invalid URL'


def test_test_ok_and_wrong_token(minimal, fake):
    c = _client(minimal)
    r = _test(c, {'url': URL, 'access_token': TOKEN})
    assert r.get_json() == {'ok': True, 'message': 'Connected'}
    r = _test(c, {'url': URL, 'access_token': 'wrong-token-zzzz'})
    assert r.get_json() == {'ok': False, 'error': 'Invalid token'}
    assert TOKEN not in r.get_data(as_text=True)


def test_test_uses_saved_config(minimal, fake):
    _set()
    c = _client(minimal)
    assert _test(c, {}).get_json() == {'ok': True, 'message': 'Connected'}
    assert fake.calls[-1]['url'] == f'{URL}/api/'


def test_test_errors(minimal, fake):
    c = _client(minimal)
    fake.fail = _rq.exceptions.Timeout()
    assert _test(c, {'url': URL, 'access_token': TOKEN}).get_json() == {
        'ok': False, 'error': 'Timed out',
    }
    fake.fail = _rq.exceptions.ConnectionError()
    assert _test(c, {'url': URL, 'access_token': TOKEN}).get_json() == {
        'ok': False, 'error': "Can't reach Home Assistant",
    }


def test_remove_clears_section(minimal, fake, caplog):
    caplog.set_level(logging.DEBUG)
    _set()
    c = _client(minimal)
    r = _put(c, {'url': '', 'access_token': ''})
    assert r.status_code == 200
    assert r.get_json() == {'url': '', 'access_token': {'set': False, 'last4': ''}}
    assert app.main.settings.get('homeassistant.url') == ''
    assert app.main.settings.get('homeassistant.access_token') == ''
    assert TOKEN not in caplog.text


def test_ha_creds_used_by_rooms_and_panel_follow_the_connector(minimal, fake):
    """main._ha_creds (smart-home panel) and the Remote power path read the same keys Save writes."""
    c = _client(minimal)
    assert app.main._ha_creds() == (None, None)
    assert _put(c, {'url': URL, 'access_token': TOKEN}).status_code == 200
    assert app.main._ha_creds() == (URL, TOKEN)
