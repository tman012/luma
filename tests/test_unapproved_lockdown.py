"""Prod hotfix: signed-in users without approved or shared Plex access (pending,
denied, still on /setup, inactive) can't read the owner's library, search or
images. Admins, the LAN implicit owner and alfred are unaffected."""

import pytest

import app.main
import app.user_auth as ua

REMOTE = {'REMOTE_ADDR': '203.0.113.7'}
LAN = {'REMOTE_ADDR': '127.0.0.1'}
LAN_HOST = {'Host': 'localhost:5001'}

DATA = ['/api/library/recent', '/api/library/recent/cached', '/api/library/search/fast?q=heat',
        '/api/library/search?q=heat', '/api/library/browse', '/api/library/stats',
        '/api/img/plex?url=/library/metadata/5/thumb/1700000000', '/api/img/tmdb?path=/a.jpg',
        '/api/hero/content', '/api/plex/continue_watching', '/api/plex/sessions/active',
        '/api/check-ownership?tmdb_id=1', '/api/watchlist', '/api/screensaver/movies',
        '/api/collections/incomplete', '/api/explore/top', '/api/tv/show?title=x',
        '/static/hero_cache/movie_1_poster.jpg']
SETUP = ['/api/auth/me', '/api/user/settings', '/api/version', '/api/start/status',
         '/api/push/vapid-key', '/api/notifications/unseen']


@pytest.fixture
def people(test_app):
    ids = {'owner': ua.create_user('owner', '1234', is_admin=True)['id']}
    ids['approved'] = ua.create_user('approved', '1234')['id']
    ua.save_user_settings(ids['approved'], {'use_shared_server': False, 'plex_access_status': 'approved'})
    ids['shared'] = ua.create_user('shared', '1234')['id']
    ua.save_user_settings(ids['shared'], {'use_shared_server': True})
    ids['unapproved'] = ua.create_user('unapproved', '1234')['id']
    ids['pending'] = ua.create_user('pending', '1234')['id']
    ua.save_user_settings(ids['pending'], {'plex_access_status': 'pending'})
    ids['denied'] = ua.create_user('denied', '1234')['id']
    ua.save_user_settings(ids['denied'], {'use_shared_server': False, 'plex_access_status': 'denied'})
    ids['denied_shared'] = ua.create_user('deniedshared', '1234')['id']
    ua.save_user_settings(ids['denied_shared'], {'use_shared_server': True, 'plex_access_status': 'denied'})
    ids['inactive'] = ua.create_user('inactive', '1234', status='pending')['id']
    ua.save_user_settings(ids['inactive'], {'use_shared_server': True, 'plex_access_status': 'approved'})
    return test_app, ids


def _as(test_app, ids, kind):
    client = test_app.test_client()
    client.set_cookie('luma_auth_session', ua.create_session(ids[kind]))
    return client


def _no_handlers(monkeypatch):
    """Fail loudly if a refused request reaches any library code."""
    hit = []
    for name in ('get_plex_for_request',):
        monkeypatch.setattr(app.main, name, lambda *a, **k: hit.append(name) or (_ for _ in ()).throw(AssertionError(name)))
    return hit


@pytest.mark.parametrize('kind', ['unapproved', 'pending', 'denied', 'denied_shared', 'inactive'])
@pytest.mark.parametrize('path', DATA)
def test_unapproved_users_get_403(people, monkeypatch, kind, path):
    test_app, ids = people
    hit = _no_handlers(monkeypatch)
    r = _as(test_app, ids, kind).get(path, environ_base=REMOTE)
    assert r.status_code == 403, (kind, path, r.status_code)
    assert hit == []
    assert b'MediaContainer' not in r.data


@pytest.mark.parametrize('path', DATA)
def test_anonymous_remote_refused(people, path):
    test_app, _ = people
    r = test_app.test_client().get(path, environ_base=REMOTE)
    # APIs answer 401 (the SPA shows sign-in); pages redirect to /login.
    assert r.status_code in (401, 403) or (r.status_code == 302 and '/login' in r.headers['Location'])


@pytest.mark.parametrize('kind', ['owner', 'approved', 'shared'])
@pytest.mark.parametrize('path', DATA)
def test_approved_shared_and_admin_pass_the_gate(people, kind, path):
    test_app, ids = people
    r = _as(test_app, ids, kind).get(path, environ_base=REMOTE)
    assert r.status_code != 403 or r.get_json(silent=True) != {'error': 'Not allowed'}, (kind, path)


@pytest.mark.parametrize('kind', ['unapproved', 'pending', 'denied'])
@pytest.mark.parametrize('path', SETUP)
def test_unapproved_users_keep_setup_endpoints(people, kind, path):
    test_app, ids = people
    r = _as(test_app, ids, kind).get(path, environ_base=REMOTE)
    assert r.get_json(silent=True) != {'error': 'Not allowed'}, (kind, path, r.status_code)


def test_unapproved_user_can_still_request_shared_access(people):
    test_app, ids = people
    r = _as(test_app, ids, 'unapproved').post('/api/user/request-shared-plex', json={},
                                              environ_base=REMOTE, headers={'Origin': 'http://localhost'})
    assert r.get_json(silent=True) != {'error': 'Not allowed'}


@pytest.mark.parametrize('path', DATA)
def test_lan_implicit_owner_unaffected(people, path):
    test_app, _ = people
    r = test_app.test_client().get(path, environ_base=LAN, headers=LAN_HOST)
    assert r.get_json(silent=True) != {'error': 'Not allowed'}, path


@pytest.mark.parametrize('path', DATA)
def test_alfred_service_key_unaffected(people, path):
    test_app, _ = people
    r = test_app.test_client().get(path, environ_base=REMOTE,
                                   headers={'X-API-Key': app.main._SERVICE_API_KEY, **LAN_HOST})
    assert r.get_json(silent=True) != {'error': 'Not allowed'}, path


def test_lan_without_owner_unaffected(test_app):
    r = test_app.test_client().get('/api/library/recent/cached', environ_base=LAN, headers=LAN_HOST)
    assert r.get_json(silent=True) != {'error': 'Not allowed'}


def test_unapproved_lan_session_still_refused(people):
    """A signed-in unapproved user on the LAN is that user, not the owner."""
    test_app, ids = people
    r = _as(test_app, ids, 'unapproved').get('/api/library/recent', environ_base=LAN, headers=LAN_HOST)
    assert r.status_code == 403


def test_approval_takes_effect_without_new_session(people):
    test_app, ids = people
    client = _as(test_app, ids, 'pending')
    assert client.get('/api/library/recent/cached', environ_base=REMOTE).status_code == 403
    ua.save_user_settings(ids['pending'], {'use_shared_server': True, 'plex_access_status': 'approved'})
    r = client.get('/api/library/recent/cached', environ_base=REMOTE)
    assert r.get_json(silent=True) != {'error': 'Not allowed'}

@pytest.mark.parametrize('path', DATA)
def test_own_server_user_without_approval_refused(people, path):
    """PM decision: a user's own Plex server doesn't unlock the owner's data
    (anyone can enter an address); only approved or shared access does."""
    test_app, ids = people
    uid = ua.create_user('ownserver', '1234')['id']
    ua.save_user_settings(uid, {'plex_url': 'http://203.0.113.50:32400', 'plex_token': 'their-own'})
    client = test_app.test_client()
    client.set_cookie('luma_auth_session', ua.create_session(uid))
    assert client.get(path, environ_base=REMOTE).status_code == 403