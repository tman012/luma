"""Trackers: owner-only CRUD/Test API, masked secrets, per-type schemas."""

import json
import logging
import os
import stat

import pytest

import app.main
import app.user_auth
from app import tracker_clients, tracker_guard, tracker_routes, tracker_store, tracker_types
from app.tracker_guard import TrackerGuard
from app.tracker_http import TrackerError
from app.tracker_store import SECRET_MASK, TrackerStore

LAN = {'REMOTE_ADDR': '192.168.1.20'}
REMOTE = {'REMOTE_ADDR': '203.0.113.9'}
ORIGIN = {'Origin': 'http://localhost', 'Host': 'localhost'}

PASSKEY = 'pk_9f8e7d6c5b4a39281706f5e4d3c2b1a0'
COOKIE = 'uid=1234567; pass=abcdefabcdefabcdefabcdefabcdef12'
APIKEY = 'ak_0123456789abcdef0123456789abcdef'


@pytest.fixture
def store(tmp_path):
    s = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key')
    tracker_store.reset_store_for_tests(s)
    tracker_guard.reset_guard_for_tests(TrackerGuard(tmp_path / 'tracker_guard.json'))
    tracker_routes._test_log.clear()
    old_spacing = tracker_guard.LIMITS['min_interval_s']
    tracker_guard.LIMITS['min_interval_s'] = 0.0
    yield s
    tracker_guard.LIMITS['min_interval_s'] = old_spacing
    tracker_guard.reset_guard_for_tests(None)
    tracker_store.reset_store_for_tests(None)


@pytest.fixture
def tested(monkeypatch):
    """Replace the network test with a recorder."""
    calls = []
    state = {'err': None}

    def fake(cfg):
        calls.append(dict(cfg))
        if state['err']:
            raise state['err']
    monkeypatch.setitem(tracker_routes._cfg, 'tester', fake)
    return calls, state


@pytest.fixture
def owner_client(test_app, store):
    """The owner on the LAN, signed in: tracker routes need a session."""
    owner = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.environ_base.update({'HTTP_ORIGIN': 'http://localhost', 'HTTP_HOST': 'localhost'})
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(owner['id']))
    return c, owner


def _ipt(**over):
    body = {'type': 'iptorrents', 'name': 'IPT', 'url': 'https://iptorrents.example',
            'cookie': COOKIE, 'categories': [2000, 5000], 'priority': 10}
    body.update(over)
    return body


def _post(c, path, body):
    return c.post(path, data=json.dumps(body), content_type='application/json')


def _put(c, path, body):
    return c.put(path, data=json.dumps(body), content_type='application/json')


# ------------------------------------------------------------------ fresh install

def test_fresh_install_has_zero_trackers(owner_client):
    c, _ = owner_client
    r = c.get('/api/trackers')
    assert r.status_code == 200
    assert r.get_json() == {'trackers': []}
    assert r.headers['Cache-Control'] == 'no-store'


def test_nothing_seeded_on_disk(owner_client, store):
    c, _ = owner_client
    c.get('/api/trackers')
    c.get('/api/trackers/types')
    assert not store.path.exists()
    assert not store.key_path.exists()


# ------------------------------------------------------------------ types

def test_type_list_covers_prowlarr_types_with_schemas(owner_client):
    c, _ = owner_client
    types = c.get('/api/trackers/types').get_json()['types']
    ids = [t['id'] for t in types]
    assert ids[:2] == ['torznab', 'newznab']
    names = [t['name'] for t in types[2:]]
    assert names == sorted(names, key=str.lower)
    for need in ('privatehd', 'beyondhd', 'iptorrents', 'morethantv', 'avistaz'):
        assert need in ids
    by = {t['id']: t for t in types}
    ipt = {f['key']: f for f in by['iptorrents']['fields']}
    assert ipt['cookie']['secret'] and ipt['cookie']['required'] and ipt['cookie']['kind'] == 'secret'
    assert not ipt['user_agent']['secret']
    phd = {f['key']: f for f in by['privatehd']['fields']}
    assert phd['password']['secret'] and phd['pid']['secret'] and not phd['username']['secret']
    bhd = {f['key'] for f in by['beyondhd']['fields'] if f['secret']}
    assert bhd == {'api_key', 'rss_key'}
    for t in types:
        keys = [f['key'] for f in t['fields']]
        assert keys[:2] == ['name', 'url'] and 'categories' in keys and 'priority' in keys
        assert t['categories'] and all({'id', 'name'} <= set(cat) for cat in t['categories'])
    assert by['newznab']['protocol'] == 'usenet'


def test_labels_are_sentence_case():
    for t in tracker_types.public_types():
        for f in t['fields']:
            words = f['label'].split()
            assert words[0][0].isupper()
            for w in words[1:]:
                assert w.islower() or w.isupper(), f['label']   # 'API key', 'RSS key', 'PID'


# ------------------------------------------------------------------ owner only

def test_non_owner_admin_is_refused(test_app, store):
    app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    second = app.user_auth.create_user('second', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(REMOTE)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(second['id']))
    assert c.get('/api/trackers').status_code == 403
    assert c.get('/api/trackers/types').status_code == 403
    r = c.post('/api/trackers', data=json.dumps(_ipt()), content_type='application/json', headers=ORIGIN)
    assert r.status_code == 403
    assert store.list() == []


def test_regular_user_is_refused(test_app, store):
    app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    u = app.user_auth.create_user('kid', 'pin-1234567')
    c = test_app.test_client()
    c.environ_base.update(REMOTE)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(u['id']))
    assert c.get('/api/trackers').status_code == 403


def test_signed_in_owner_remote(test_app, store, tested):
    o = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(REMOTE)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(o['id']))
    r = c.post('/api/trackers', data=json.dumps(_ipt()), content_type='application/json', headers=ORIGIN)
    assert r.status_code == 201


def test_remote_without_session_is_refused(test_app, store):
    app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(REMOTE)
    assert c.get('/api/trackers').status_code == 401


def test_service_api_key_is_not_the_owner(test_app, store):
    app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(REMOTE)
    r = c.get('/api/trackers', headers={'X-API-Key': app.main._SERVICE_API_KEY})
    assert r.status_code == 403


# ------------------------------------------------------------------ CRUD + masking

def test_create_masks_secrets_and_encrypts_at_rest(owner_client, store):
    c, _ = owner_client
    r = _post(c, '/api/trackers', _ipt())
    assert r.status_code == 201
    t = r.get_json()['tracker']
    assert t['cookie'] == SECRET_MASK
    assert t['status']['state'] == 'untested' and t['enabled'] is True
    assert t['type_name'] == 'IPTorrents' and t['protocol'] == 'torrent'
    raw = store.path.read_text()
    assert COOKIE not in raw and 'abcdefabcdef' not in raw
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.key_path).st_mode) == 0o600
    listed = c.get('/api/trackers').get_json()['trackers']
    assert COOKIE not in json.dumps(listed)
    assert store.config(store.get(t['id']))['cookie'] == COOKIE


def test_secret_never_in_any_response(owner_client, tested):
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    bodies = [
        c.get('/api/trackers').data, c.get(f'/api/trackers/{tid}').data,
        _put(c, f'/api/trackers/{tid}', {'name': 'IPT 2', 'cookie': SECRET_MASK}).data,
        _put(c, f'/api/trackers/{tid}/enabled', {'enabled': False}).data,
        _post(c, f'/api/trackers/{tid}/test', {}).data,
    ]
    for b in bodies:
        assert b'abcdefabcdef' not in b and COOKIE.encode() not in b


def test_edit_with_masked_secret_keeps_it(owner_client, store, tested):
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    _post(c, f'/api/trackers/{tid}/test', {})
    assert store.get(tid)['status']['state'] == 'ok'
    r = _put(c, f'/api/trackers/{tid}', {'name': 'IPTorrents', 'cookie': SECRET_MASK, 'priority': 5})
    assert r.status_code == 200
    rec = store.get(tid)
    assert store.config(rec)['cookie'] == COOKIE
    assert rec['name'] == 'IPTorrents' and rec['priority'] == 5
    assert rec['status']['state'] == 'ok'          # name/priority aren't connection changes


def test_edit_with_new_secret_replaces_it_and_resets_status(owner_client, store, tested):
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    _post(c, f'/api/trackers/{tid}/test', {})
    r = _put(c, f'/api/trackers/{tid}', {'cookie': 'uid=1; pass=new'})
    assert r.status_code == 200
    assert store.config(store.get(tid))['cookie'] == 'uid=1; pass=new'
    assert store.get(tid)['status']['state'] == 'untested'


def test_required_secret_cannot_be_cleared(owner_client, store):
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    r = _put(c, f'/api/trackers/{tid}', {'cookie': ''})
    assert r.status_code == 400 and r.get_json()['fields'] == {'cookie': 'Required'}
    assert store.config(store.get(tid))['cookie'] == COOKIE


def test_optional_secret_can_be_cleared(owner_client, store):
    c, _ = owner_client
    body = {'type': 'torznab', 'name': 'Jackett', 'url': 'http://jackett.example:8117/api/v2.0/indexers/x/results/torznab',
            'api_key': APIKEY}
    tid = _post(c, '/api/trackers', body).get_json()['tracker']['id']
    assert _put(c, f'/api/trackers/{tid}', {'api_key': ''}).status_code == 200
    assert 'api_key' not in store.config(store.get(tid))
    assert c.get(f'/api/trackers/{tid}').get_json()['tracker']['api_key'] == ''


def test_validation_errors_per_field(owner_client, store):
    c, _ = owner_client
    r = _post(c, '/api/trackers', {'type': 'privatehd', 'name': '', 'url': 'ftp://x', 'username': 'u',
                                   'categories': [9999], 'priority': 0})
    assert r.status_code == 400
    f = r.get_json()['fields']
    assert f == {'name': 'Required', 'url': 'Invalid URL', 'password': 'Required', 'pid': 'Required',
                 'categories': 'Invalid', 'priority': 'Invalid'}
    assert store.list() == []


@pytest.mark.parametrize('url', ['https://user:pw@site.example', 'https://site.example/?passkey=1',
                                 'javascript:alert(1)', 'https://', 'site.example', 'https://a b.example',
                                 'https://site.example:99999'])
def test_bad_urls_rejected(owner_client, url):
    c, _ = owner_client
    r = _post(c, '/api/trackers', _ipt(url=url))
    assert r.status_code == 400 and r.get_json()['fields']['url'] == 'Invalid URL'


def test_unknown_type_and_type_change(owner_client):
    c, _ = owner_client
    assert _post(c, '/api/trackers', {'type': 'cardigann', 'name': 'x'}).get_json()['fields'] == {'type': 'Invalid'}
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    r = _put(c, f'/api/trackers/{tid}', {'type': 'beyondhd'})
    assert r.status_code == 400 and r.get_json()['fields']['type'] == 'Invalid'


def test_unknown_fields_rejected(owner_client):
    c, _ = owner_client
    r = _post(c, '/api/trackers', _ipt(passkey='x'))
    assert r.status_code == 400 and r.get_json()['fields'] == {'passkey': 'Invalid'}


def test_header_injection_in_secret_rejected(owner_client):
    c, _ = owner_client
    r = _post(c, '/api/trackers', _ipt(cookie='uid=1\r\nX-Evil: 1'))
    assert r.status_code == 400 and r.get_json()['fields'] == {'cookie': 'Invalid'}


def test_duplicate_names_rejected(owner_client):
    c, _ = owner_client
    assert _post(c, '/api/trackers', _ipt()).status_code == 201
    r = _post(c, '/api/trackers', _ipt(name='ipt'))
    assert r.status_code == 400 and r.get_json()['fields'] == {'name': 'Name in use'}


def test_enable_toggle_without_full_edit(owner_client, store):
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    r = _put(c, f'/api/trackers/{tid}/enabled', {'enabled': False})
    assert r.status_code == 200 and r.get_json()['tracker']['enabled'] is False
    assert store.get(tid)['enabled'] is False and store.config(store.get(tid))['cookie'] == COOKIE
    assert _put(c, f'/api/trackers/{tid}/enabled', {'enabled': 'no'}).status_code == 400
    assert _put(c, '/api/trackers/999/enabled', {'enabled': True}).status_code == 404
    assert store.enabled_configs() == []


def test_delete(owner_client, store):
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    assert c.delete(f'/api/trackers/{tid}').status_code == 200
    assert c.delete(f'/api/trackers/{tid}').status_code == 404
    assert c.get(f'/api/trackers/{tid}').status_code == 404
    assert store.list() == []


def test_ids_are_never_reused(owner_client):
    c, _ = owner_client
    a = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    c.delete(f'/api/trackers/{a}')
    b = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    assert b > a


def test_list_sorted_by_priority_then_name(owner_client):
    c, _ = owner_client
    _post(c, '/api/trackers', _ipt(name='Zed', priority=1))
    _post(c, '/api/trackers', _ipt(name='Bee', priority=30))
    _post(c, '/api/trackers', _ipt(name='Ann', priority=30))
    assert [t['name'] for t in c.get('/api/trackers').get_json()['trackers']] == ['Zed', 'Ann', 'Bee']


def test_bad_json_body(owner_client):
    c, _ = owner_client
    r = c.post('/api/trackers', data='not json', content_type='application/json')
    assert r.status_code == 400


# ------------------------------------------------------------------ Test

def test_test_saved_ok_and_error_update_status(owner_client, store, tested):
    calls, state = tested
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    r = _post(c, f'/api/trackers/{tid}/test', {})
    assert r.get_json() == {'ok': True, 'message': 'Connected'}
    assert calls[-1]['cookie'] == COOKIE        # server-side config has the real secret
    tracker_routes._test_log.clear()
    state['err'] = TrackerError('auth', 401)
    r = _post(c, f'/api/trackers/{tid}/test', {})
    assert r.get_json() == {'ok': False, 'error': 'Unauthorized'}
    st = c.get(f'/api/trackers/{tid}').get_json()['tracker']['status']
    assert st['state'] == 'error' and st['message'] == 'Unauthorized'


def test_test_unsaved_form_uses_stored_secret_for_mask(owner_client, store, tested):
    calls, _ = tested
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    r = _post(c, '/api/trackers/test', dict(_ipt(cookie=SECRET_MASK), id=tid))
    assert r.get_json()['ok'] is True and calls[-1]['cookie'] == COOKIE
    assert store.get(tid)['status']['state'] == 'untested'     # unsaved test doesn't touch status


def test_test_unsaved_new_tracker(owner_client, store, tested):
    calls, _ = tested
    c, _ = owner_client
    r = _post(c, '/api/trackers/test', _ipt())
    assert r.get_json()['ok'] is True and calls[-1]['cookie'] == COOKIE
    assert store.list() == [] and not store.path.exists()


def test_test_unsaved_validation(owner_client, tested):
    calls, _ = tested
    c, _ = owner_client
    r = _post(c, '/api/trackers/test', _ipt(cookie=''))
    assert r.status_code == 400 and calls == []


def test_test_unexpected_exception_is_generic(owner_client, monkeypatch):
    c, _ = owner_client

    def boom(cfg):
        raise RuntimeError(f"secret {cfg['cookie']}")
    monkeypatch.setitem(tracker_routes._cfg, 'tester', boom)
    r = _post(c, '/api/trackers/test', _ipt())
    assert r.get_json() == {'ok': False, 'error': 'Unexpected response'}


def test_test_is_throttled(owner_client, tested):
    calls, _ = tested
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    assert _post(c, f'/api/trackers/{tid}/test', {}).get_json()['ok'] is True
    r = _post(c, f'/api/trackers/{tid}/test', {})
    assert r.get_json() == {'ok': False, 'error': 'Rate limited'}
    assert len(calls) == 1


def test_unreadable_credentials(owner_client, store, tested):
    c, _ = owner_client
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    store.key_path.unlink()
    store._fernet = None
    r = _post(c, f'/api/trackers/{tid}/test', {})
    assert r.get_json() == {'ok': False, 'error': 'Credentials unreadable'}
    assert c.get('/api/trackers').status_code == 200
    assert store.enabled_configs() == []


def test_no_secret_in_logs(owner_client, tested, caplog):
    calls, state = tested
    c, _ = owner_client
    caplog.set_level(logging.DEBUG)
    tid = _post(c, '/api/trackers', _ipt()).get_json()['tracker']['id']
    state['err'] = TrackerError('auth', 401)
    _post(c, f'/api/trackers/{tid}/test', {})
    _put(c, f'/api/trackers/{tid}', {'cookie': 'uid=9; pass=zzzzzzzzzzzzzzzzzzzzzzzz'})
    c.delete(f'/api/trackers/{tid}')
    assert 'abcdefabcdef' not in caplog.text and 'zzzzzzzz' not in caplog.text


def test_csrf_still_applies(test_app, store):
    o = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(REMOTE)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(o['id']))
    r = c.post('/api/trackers', data=json.dumps(_ipt()), content_type='application/json',
               headers={'Origin': 'https://evil.example', 'Host': 'localhost'})
    assert r.status_code == 403 and store.list() == []
