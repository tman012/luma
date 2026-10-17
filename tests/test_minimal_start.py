"""LUMA_MINIMAL=1 fresh-install build and the Start flow's Set password step."""

import sys
from unittest import mock

import pytest

REMOTE = {'REMOTE_ADDR': '203.0.113.9'}
ORIGIN = {'Origin': 'http://localhost'}

AUTOMATION_MODULES = [
    'app.anticipated_watchlist', 'app.auto_rename', 'app.daily_missing', 'app.downloads_guard',
    'app.franchise_completer', 'app.genre_roulette', 'app.latest_auto_grab', 'app.movie_collections',
    'app.movie_night', 'app.now_available_watchlist', 'app.trending_tv_watchlist',
    'app.trending_watchlist', 'app.upgrade_4k_auto_grab', 'app.upgrade_scheduler',
    'app.watchlist_digital_dates',
]

DISABLED = [
    '/api/rails/what-youre-missing', '/api/automation/features', '/api/automation/latest-auto-grab/status',
    '/api/genre-roulette/status', '/api/request/home', '/api/bot/rooms', '/api/alfred/health',
    '/api/ha/areas', '/api/kiosk/settings', '/api/explore/cached', '/api/hero/content',
    '/api/sonarr/calendar', '/api/radarr/queue', '/api/settings', '/api/indexer/latest',
    '/api/library/recent', '/api/weather', '/explore', '/settings', '/request', '/watchlist',
]


@pytest.fixture
def minimal(monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')


@pytest.fixture
def start_owner(test_app, monkeypatch):
    """An owner just created by the Start flow (Plex step done, Done not tapped yet)."""
    import secrets
    from app import start_flow, user_auth
    monkeypatch.setenv('LUMA_NO_RESTART', '1')
    user = user_auth.create_user('plexowner', secrets.token_urlsafe(24), is_admin=True,
                                 status='active', email='owner@example.com')
    start_flow._save_state({'admin_via_start': True, 'completed': False, 'pin_set': False,
                            'server_name': 'Basement'})
    test_app.config['START_BACKGROUND_THREADS'] = False
    c = test_app.test_client()
    c.set_cookie('luma_auth_session', user_auth.create_session(user['id']))
    yield c, user
    start_flow._save_state({})
    import app.main
    app.main.settings.update({'security': {'auth_required': 'disabled_local'}})


# -- path rules ----------------------------------------------------------------

def test_flag_parsing(monkeypatch):
    from app import minimal
    for v in ('1', 'true', 'YES', 'on'):
        monkeypatch.setenv('LUMA_MINIMAL', v)
        assert minimal.enabled()
    for v in ('', '0', 'false', 'no'):
        monkeypatch.setenv('LUMA_MINIMAL', v)
        assert not minimal.enabled()
    monkeypatch.delenv('LUMA_MINIMAL')
    assert not minimal.enabled()


def test_path_rules():
    from app.minimal import path_allowed
    for p in ('/', '/start', '/login', '/logout', '/unlock', '/health', '/sw.js', '/manifest.json',
              '/api/auth/me', '/api/auth/login', '/api/start/status', '/api/start/password',
              '/api/security', '/api/version', '/assets/index.js', '/static/images/luma_text.webp',
              '/fonts/x.woff2', '/start/'):
        assert path_allowed(p), p
    for p in DISABLED + ['/static/request/app.js', '/static/hero_cache/a.jpg', '/api/authx', '/startx']:
        assert not path_allowed(p), p


# -- routes --------------------------------------------------------------------

def test_disabled_routes_404_in_minimal_mode(test_app, minimal):
    c = test_app.test_client()
    for path in DISABLED:
        r = c.get(path)
        assert r.status_code == 404, path
        if path.startswith('/api/'):
            assert r.is_json, path
    assert c.post('/api/alfred/command', json={}).status_code == 404
    assert c.post('/api/bot/download', json={}).status_code == 404
    assert c.post('/api/automation/latest-auto-grab/run', headers=ORIGIN).status_code == 404


def test_core_routes_stay_on_in_minimal_mode(test_app, minimal):
    c = test_app.test_client()
    assert c.get('/health').status_code == 200
    assert c.get('/api/start/status').json['needs_start'] is True
    assert c.get('/api/auth/me', environ_base=REMOTE).status_code == 401
    assert c.get('/login').status_code == 200
    assert c.get('/sw.js').status_code == 200


def test_spa_marks_minimal_mode(test_app, monkeypatch):
    import os
    if not os.path.exists(os.path.join(test_app.config['SPA_DIR'], 'index.html')):
        pytest.skip('SPA not built')
    c = test_app.test_client()
    assert '__LUMA_MINIMAL__' not in c.get('/').get_data(as_text=True)
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    assert 'window.__LUMA_MINIMAL__ = true' in c.get('/').get_data(as_text=True)


def test_flag_unset_leaves_routes_alone(test_app, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    c = test_app.test_client()
    for path in ('/api/automation/features', '/api/genre-roulette/status', '/explore', '/settings'):
        assert c.get(path).status_code != 404, path


# -- startup -------------------------------------------------------------------

@pytest.fixture
def patched_start(test_app, monkeypatch):
    import app.main
    monkeypatch.setattr(app.main, '_AUTOMATIONS_DISABLED', False)
    fakes = {name: mock.MagicMock(name=name) for name in AUTOMATION_MODULES}
    with mock.patch.dict(sys.modules, fakes), \
            mock.patch('app.main.init_database') as init_db, \
            mock.patch.object(app.main.user_auth, 'init_db') as ua_init, \
            mock.patch.object(app.main.fast_search_index, 'start') as search_start, \
            mock.patch.object(app.main.hero_cache, 'start_scheduler') as hero_start, \
            mock.patch('app.main.explore_cache_updater') as explore, \
            mock.patch('app.main.tunnel_auto_start') as tunnel, \
            mock.patch('threading.Thread') as thread:
        yield dict(init_db=init_db, ua_init=ua_init, search=search_start, hero=hero_start,
                   explore=explore, tunnel=tunnel, thread=thread, fakes=fakes)


def _started(fakes):
    return [n for n, m in fakes.items() if any(c[0].startswith('start_') for c in m.mock_calls)]


def test_minimal_mode_starts_no_background_threads(test_app, patched_start, minimal):
    from app.main import start_services
    test_app.config['START_BACKGROUND_THREADS'] = True
    start_services(test_app)
    p = patched_start
    p['init_db'].assert_called_once()
    p['ua_init'].assert_called_once()
    p['thread'].assert_not_called()
    for k in ('search', 'hero', 'explore', 'tunnel'):
        p[k].assert_not_called()
    assert _started(p['fakes']) == []


def test_flag_unset_starts_background_threads(test_app, patched_start, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    from app.main import start_services
    test_app.config['START_BACKGROUND_THREADS'] = True
    start_services(test_app)
    p = patched_start
    assert p['thread'].called
    p['search'].assert_called_once()
    assert sorted(_started(p['fakes'])) == sorted(AUTOMATION_MODULES)


# -- Set password --------------------------------------------------------------

def test_fresh_db_has_no_users(test_app, minimal):
    from app import user_auth
    assert user_auth.list_users() == []
    assert test_app.test_client().get('/api/start/status').json['step'] == 'welcome'


@pytest.mark.parametrize('use_minimal', [False, True])
def test_set_password_then_login(start_owner, test_app, monkeypatch, use_minimal):
    if use_minimal:
        monkeypatch.setenv('LUMA_MINIMAL', '1')
    from app import user_auth
    c, user = start_owner
    st = c.get('/api/start/status').json
    assert (st['in_progress'], st['password_set']) == (True, False)

    r = c.post('/api/start/password', json={'password': 'correct horse battery', 'confirm': 'correct horse battery'},
               headers=ORIGIN)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert c.get('/api/start/status').json['password_set'] is True
    assert user_auth.get_user_by_id(user['id'])['pin_hash'].startswith('scrypt:')

    # Sign-in uses /login from now on, not the PIN keypad.
    other = test_app.test_client()
    me = other.get('/api/auth/me', environ_base=REMOTE)
    assert me.status_code == 401 and me.json['pin_unlock'] is False

    bad = other.post('/api/auth/login', json={'name': 'plexowner', 'pin': 'wrong password'}, environ_base=REMOTE)
    assert bad.status_code in (400, 401)
    for ident in ('plexowner', 'owner@example.com'):
        ok = test_app.test_client().post('/api/auth/login', json={'name': ident, 'pin': 'correct horse battery'},
                                         environ_base=REMOTE)
        assert ok.status_code == 200 and ok.json.get('success') is True, ok.get_data(as_text=True)

    # The Access step can pick a mode without a PIN once a password exists.
    r = c.put('/api/security', json={'auth_required': 'enabled'}, headers=ORIGIN)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert c.post('/api/start/finish', headers=ORIGIN).status_code == 200


def test_login_form_after_set_password(start_owner, test_app, monkeypatch):
    import re
    c, _ = start_owner
    c.post('/api/start/password', json={'password': 'hunter2-but-longer', 'confirm': 'hunter2-but-longer'},
           headers=ORIGIN)
    f = test_app.test_client()
    page = f.get('/login', environ_base=REMOTE).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', page)
    data = {'username': 'plexowner', 'password': 'hunter2-but-longer'}
    if m:
        data['csrf_token'] = m.group(1)
    r = f.post('/login', data=data, environ_base=REMOTE, headers=ORIGIN)
    assert r.status_code in (302, 303), r.get_data(as_text=True)[:300]
    assert any(h.startswith('luma_auth_session=') for h in r.headers.getlist('Set-Cookie'))


@pytest.mark.parametrize('body,msg', [
    ({'password': 'short', 'confirm': 'short'}, 'at least'),
    ({'password': 'long enough 1', 'confirm': 'long enough 2'}, "don't match"),
    ({'password': 'x' * 257, 'confirm': 'x' * 257}, 'at most'),
    ({'password': 12345678, 'confirm': 12345678}, 'Enter a password'),
    ({}, 'Enter a password'),
])
def test_set_password_validation(start_owner, body, msg):
    c, _ = start_owner
    r = c.post('/api/start/password', json=body, headers=ORIGIN)
    assert r.status_code == 400 and msg in r.json['error']


def test_set_password_refused_outside_start(test_app, start_owner):
    from app import start_flow
    c, _ = start_owner
    anon = test_app.test_client()
    r = anon.post('/api/start/password', json={'password': 'abcdefgh', 'confirm': 'abcdefgh'},
                  headers=ORIGIN, environ_base=REMOTE)
    assert r.status_code in (401, 403, 409)
    start_flow._save_state({'admin_via_start': True, 'completed': True})
    r = c.post('/api/start/password', json={'password': 'abcdefgh', 'confirm': 'abcdefgh'}, headers=ORIGIN)
    assert r.status_code == 409
