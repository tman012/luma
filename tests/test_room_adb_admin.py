"""The room ADB test and pair endpoints run adb against the network, so only
admins (including the LAN implicit owner and alfred's service key) may call
them; approved and shared members get 403 and adb is never run."""

import pytest

import app.adb_utils
import app.main
import app.user_auth as ua

REMOTE = {'REMOTE_ADDR': '203.0.113.7'}
LAN = {'REMOTE_ADDR': '127.0.0.1'}
LAN_HOST = {'Host': 'localhost:5001'}
ORIGIN = {'Origin': 'http://localhost'}

DEVICE = {'id': 'd1', 'host': '192.168.1.40', 'connectPort': 5555, 'pairPort': 37000,
          'platform': 'android_tv'}
ROOM = {'id': 'r1', 'name': 'Den', 'devices': [DEVICE]}
PAIR = {'pairPort': 37000, 'pairCode': '123456'}
ROUTES = [
    ('/api/rooms/r1/devices/d1/test', {}),
    ('/api/rooms/r1/devices/d1/pair', PAIR),
    ('/api/rooms/r1/test', {}),
    ('/api/rooms/r1/pair', PAIR),
]
OK = {'ok': True, 'detail': 'ok', 'stdout': '', 'stderr': '', 'needsPairing': False, 'steps': []}


@pytest.fixture
def adb(test_app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.main.settings, 'get_device',
                        lambda room_id, device_id: dict(DEVICE) if (room_id, device_id) == ('r1', 'd1') else None)
    monkeypatch.setattr(app.main.settings, 'get_room_by_id',
                        lambda room_id: dict(ROOM) if room_id == 'r1' else None)
    monkeypatch.setattr(app.adb_utils, 'test_room_connection',
                        lambda *a, **k: calls.append(('test', a, k)) or dict(OK))
    monkeypatch.setattr(app.adb_utils, 'adb_pair', lambda *a, **k: calls.append(('pair', a, k)) or dict(OK))
    monkeypatch.setattr(app.adb_utils, 'adb_connect', lambda *a, **k: calls.append(('connect', a, k)) or dict(OK))
    return test_app, calls


def _member(kind):
    uid = ua.create_user(kind, '1234')['id']
    if kind == 'approved':
        ua.save_user_settings(uid, {'use_shared_server': False, 'plex_access_status': 'approved'})
    elif kind == 'shared':
        ua.save_user_settings(uid, {'use_shared_server': True})
    return uid


def _session(test_app, uid):
    client = test_app.test_client()
    client.set_cookie('luma_auth_session', ua.create_session(uid))
    return client


@pytest.mark.parametrize('kind', ['approved', 'shared', 'unapproved'])
@pytest.mark.parametrize('path,body', ROUTES)
def test_members_get_403_and_adb_never_runs(adb, kind, path, body):
    test_app, calls = adb
    ua.create_user('owner', '1234', is_admin=True)
    r = _session(test_app, _member(kind)).post(path, json=body, environ_base=REMOTE, headers=ORIGIN)
    assert r.status_code == 403, (kind, path, r.status_code)
    assert calls == []


@pytest.mark.parametrize('path,body', ROUTES)
def test_anonymous_remote_refused(adb, path, body):
    test_app, calls = adb
    ua.create_user('owner', '1234', is_admin=True)
    r = test_app.test_client().post(path, json=body, environ_base=REMOTE, headers=ORIGIN)
    assert r.status_code in (401, 403), (path, r.status_code)
    assert calls == []


@pytest.mark.parametrize('path,body', ROUTES)
def test_admin_runs_adb_against_the_configured_device(adb, path, body):
    test_app, calls = adb
    uid = ua.create_user('owner', '1234', is_admin=True)['id']
    r = _session(test_app, uid).post(path, json=body, environ_base=REMOTE, headers=ORIGIN)
    assert r.status_code == 200, (path, r.status_code, r.get_data(as_text=True))
    assert r.get_json()['ok'] is True
    assert calls
    for _, args, kwargs in calls:
        assert DEVICE['host'] in (*args, *kwargs.values())


@pytest.mark.parametrize('path,body', ROUTES)
def test_lan_implicit_owner_allowed(adb, path, body):
    test_app, calls = adb
    ua.create_user('owner', '1234', is_admin=True)
    r = test_app.test_client().post(path, json=body, environ_base=LAN, headers={**LAN_HOST, 'Origin': 'http://localhost:5001'})
    assert r.status_code == 200, (path, r.status_code)
    assert calls


@pytest.mark.parametrize('path,body', ROUTES)
def test_alfred_service_key_allowed(adb, path, body):
    test_app, calls = adb
    ua.create_user('owner', '1234', is_admin=True)
    r = test_app.test_client().post(path, json=body, environ_base=LAN,
                                    headers={'X-API-Key': app.main._SERVICE_API_KEY, **LAN_HOST})
    assert r.status_code == 200, (path, r.status_code)
    assert calls


def test_unknown_room_still_404_for_admin(adb):
    test_app, calls = adb
    uid = ua.create_user('owner', '1234', is_admin=True)['id']
    r = _session(test_app, uid).post('/api/rooms/nope/test', json={}, environ_base=REMOTE, headers=ORIGIN)
    assert r.status_code == 404
    assert calls == []
