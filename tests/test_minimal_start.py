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


def _setup_cookie(test_app, client, uid, plex_id='plex-1'):
    from app import start_flow
    with test_app.test_request_context():
        tok = start_flow.setup_token(uid, plex_id)
    client.set_cookie(start_flow.SETUP_COOKIE, tok, path='/api/start/')


@pytest.fixture
def start_owner(test_app, monkeypatch):
    """An owner just created by the Start flow (Plex step done, Done not tapped yet),
    in the browser that holds the signed setup cookie."""
    import secrets
    from app import start_flow, user_auth
    monkeypatch.setenv('LUMA_NO_RESTART', '1')
    user = user_auth.create_user('plexowner', secrets.token_urlsafe(24), is_admin=True,
                                 status='active', email='owner@example.com')
    start_flow._save_state({'admin_via_start': True, 'completed': False, 'pin_set': False,
                            'password_set': False, 'server_name': 'Basement',
                            'owner_uid': user['id'], 'owner_plex_id': 'plex-1'})
    test_app.config['START_BACKGROUND_THREADS'] = False
    c = test_app.test_client()
    c.set_cookie('luma_auth_session', user_auth.create_session(user['id']))
    _setup_cookie(test_app, c, user['id'])
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


GOOD = {'password': 'abcdefgh-1', 'confirm': 'abcdefgh-1'}


def test_set_password_refused_outside_start(test_app, start_owner):
    from app import start_flow
    c, _ = start_owner
    start_flow._save_state({**start_flow._load_state(), 'completed': True})
    assert c.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 409


@pytest.mark.parametrize('peer', ['127.0.0.1', '192.168.1.20', '10.0.0.7'])
def test_implicit_local_owner_cannot_set_password(test_app, start_owner, peer):
    """disabled_local gives LAN/loopback an implicit owner; it must never set the password."""
    import app.main
    app.main.settings.update({'security': {'auth_required': 'disabled_local'}})
    anon = test_app.test_client()
    r = anon.post('/api/start/password', json=GOOD, headers={**ORIGIN, 'Host': 'localhost'},
                  environ_base={'REMOTE_ADDR': peer})
    assert r.status_code == 401
    # Even with a stolen setup cookie but no real session.
    _setup_cookie(test_app, anon, start_owner[1]['id'])
    r = anon.post('/api/start/password', json=GOOD, headers=ORIGIN, environ_base={'REMOTE_ADDR': peer})
    assert r.status_code == 401


def test_session_without_setup_cookie_rejected(test_app, start_owner):
    from app import start_flow, user_auth
    _, user = start_owner
    other = test_app.test_client()
    other.set_cookie('luma_auth_session', user_auth.create_session(user['id']))
    assert other.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 403
    other.set_cookie(start_flow.SETUP_COOKIE, 'forged.value', path='/api/start/')
    assert other.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 403


def test_setup_cookie_for_another_uid_or_plex_account_rejected(test_app, start_owner):
    c, user = start_owner
    _setup_cookie(test_app, c, user['id'] + 1)
    assert c.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 403
    _setup_cookie(test_app, c, user['id'], plex_id='someone-else')
    assert c.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 403
    _setup_cookie(test_app, c, user['id'])
    assert c.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 200


def test_set_password_is_one_shot(test_app, start_owner):
    from app import start_flow, user_auth
    c, user = start_owner
    r = c.post('/api/start/password', json=GOOD, headers=ORIGIN)
    assert r.status_code == 200
    assert any(h.startswith(start_flow.SETUP_COOKIE + '=;') for h in r.headers.getlist('Set-Cookie'))
    before = user_auth.get_user_by_id(user['id'])['pin_hash']
    _setup_cookie(test_app, c, user['id'])
    again = {'password': 'another-pass-2', 'confirm': 'another-pass-2'}
    assert c.post('/api/start/password', json=again, headers=ORIGIN).status_code == 409
    assert user_auth.get_user_by_id(user['id'])['pin_hash'] == before


def test_signed_out_spam_never_locks_out_owner(test_app, start_owner):
    from app import login_limiter, start_flow, user_auth
    c, user = start_owner
    other = test_app.test_client()
    forged = test_app.test_client()
    forged.set_cookie('luma_auth_session', user_auth.create_session(user['id']))
    forged.set_cookie(start_flow.SETUP_COOKIE, 'forged.value', path='/api/start/')
    for _ in range(login_limiter.MAX_FAILURES * 3):
        assert other.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 401
        assert forged.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 403
    assert login_limiter._fail == {}
    assert c.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 200


def test_owner_bad_attempts_hit_limit(test_app, start_owner):
    from app import login_limiter
    c, _ = start_owner
    for _ in range(login_limiter.MAX_FAILURES):
        assert c.post('/api/start/password', json={'password': 'short', 'confirm': 'short'},
                      headers=ORIGIN).status_code == 400
    r = c.post('/api/start/password', json=GOOD, headers=ORIGIN)
    assert r.status_code == 429 and int(r.headers['Retry-After']) > 0


def test_parallel_valid_posts_set_once(test_app, start_owner):
    import threading
    from app import start_flow, user_auth
    c, user = start_owner
    n = 6
    clients = []
    for _ in range(n):
        k = test_app.test_client()
        k.set_cookie('luma_auth_session', user_auth.create_session(user['id']))
        _setup_cookie(test_app, k, user['id'])
        clients.append(k)
    barrier = threading.Barrier(n)
    codes = []

    def go(k, i):
        barrier.wait()
        pw = f'parallel-pass-{i}'
        codes.append(k.post('/api/start/password', json={'password': pw, 'confirm': pw},
                            headers=ORIGIN).status_code)
    threads = [threading.Thread(target=go, args=(k, i)) for i, k in enumerate(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(codes) == [200] + [409] * (n - 1)
    st = start_flow._load_state()
    assert st['password_set'] is True and not st.get('password_claimed')


def test_claim_released_when_hashing_fails(test_app, start_owner):
    from app import start_flow, user_auth
    c, _ = start_owner
    with mock.patch.object(user_auth, 'update_pin', side_effect=RuntimeError('boom')):
        test_app.config['PROPAGATE_EXCEPTIONS'] = False
        try:
            assert c.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 500
        finally:
            test_app.config['PROPAGATE_EXCEPTIONS'] = None
    st = start_flow._load_state()
    assert not st.get('password_claimed') and not st.get('password_set')
    assert c.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 200


@pytest.mark.parametrize('base,expected', [('', '/api/start/'), ('/luma', '/luma/api/start/'),
                                           ('/luma/', '/luma/api/start/')])
def test_setup_cookie_path_follows_url_base(test_app, start_owner, base, expected):
    from app import start_flow
    c, _ = start_owner
    old = test_app.config.get('URL_BASE')
    test_app.config['URL_BASE'] = base
    try:
        with test_app.test_request_context():
            assert start_flow._setup_cookie_path() == expected
        r = c.post('/api/start/password', json=GOOD, headers=ORIGIN)
        assert r.status_code == 200
        dels = [h for h in r.headers.getlist('Set-Cookie') if h.startswith(start_flow.SETUP_COOKIE + '=;')]
        assert dels and f'Path={expected}' in dels[0]
    finally:
        test_app.config['URL_BASE'] = old


def test_setup_cookie_non_dict_payload_rejected(test_app, start_owner):
    from app import start_flow
    c, _ = start_owner
    with test_app.test_request_context():
        tok = start_flow._serializer().dumps(['not', 'a', 'dict'])
    c.set_cookie(start_flow.SETUP_COOKIE, tok, path='/api/start/')
    assert c.post('/api/start/password', json=GOOD, headers=ORIGIN).status_code == 403


def test_security_reports_password(start_owner):
    c, _ = start_owner
    assert c.get('/api/security').json['pin_set'] is False
    c.post('/api/start/password', json=GOOD, headers=ORIGIN)
    body = c.get('/api/security').json
    assert body['pin_set'] is True and body['password_set'] is True


# -- minimal mode: no implicit owner, Docker bridge NAT -----------------------

@pytest.mark.parametrize('peer,headers', [
    ('192.168.1.20', {}),
    ('127.0.0.1', {}),
    ('172.17.0.1', {}),                                   # Docker bridge gateway
    ('172.17.0.1', {'X-Forwarded-For': '192.168.1.20'}),  # spoofed hop through NAT
])
def test_minimal_mode_never_implicit_owner(test_app, minimal, peer, headers):
    import secrets
    import app.main
    from app import user_auth
    user_auth.create_user('owner', secrets.token_urlsafe(16), is_admin=True)
    app.main.settings.update({'security': {'auth_required': 'disabled_local', 'trusted_proxies': ''}})
    c = test_app.test_client()
    r = c.get('/api/auth/me', headers={**headers, 'Host': 'localhost'}, environ_base={'REMOTE_ADDR': peer})
    assert r.status_code == 401
    assert c.put('/api/security', json={'auth_required': 'disabled_local'}, headers=ORIGIN,
                 environ_base={'REMOTE_ADDR': peer}).status_code == 401


def test_bridge_gateway_client_ip(test_app):
    """Behind Docker NAT the peer is the gateway; forwarded headers from an
    untrusted peer are never believed, and a trusted gateway is honored."""
    from app import access

    class S(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)
    with test_app.test_request_context('/', environ_base={'REMOTE_ADDR': '172.17.0.1'}):
        from flask import request
        assert access.client_ip(request, S()) == '172.17.0.1'
    with test_app.test_request_context('/', environ_base={'REMOTE_ADDR': '172.17.0.1'},
                                       headers={'X-Forwarded-For': '203.0.113.9'}):
        from flask import request
        assert access.client_ip(request, S()) is None
        assert access.client_ip(request, S({'security.trusted_proxies': '172.16.0.0/12'})) == '203.0.113.9'
        assert not access.is_local_request(request, S({'security.trusted_proxies': '172.16.0.0/12'}))


def test_flag_unset_lan_keeps_implicit_owner(test_app, monkeypatch):
    import secrets
    import app.main
    from app import user_auth
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    user_auth.create_user('owner', secrets.token_urlsafe(16), is_admin=True)
    app.main.settings.update({'security': {'auth_required': 'disabled_local'}})
    r = test_app.test_client().get('/api/auth/me', headers={'Host': 'localhost'},
                                   environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert r.status_code == 200 and r.json['implicit'] is True
