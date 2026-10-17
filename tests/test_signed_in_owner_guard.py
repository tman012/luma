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


# ------------------------------------------------- /api/downloads/remove/<hash>

H = 'a' * 40


class _Qbit:
    def __init__(self):
        self.posts = []

    def __call__(self):
        q = self

        class R:
            def __init__(self, text='Ok.'):
                self.status_code, self.text = 200, text

        class S:
            def post(self, url, data=None, **kw):
                q.posts.append((url.rsplit('/api/v2/', 1)[-1], dict(data or {})))
                return R()
        return S()


@pytest.fixture
def qbit(full, monkeypatch):
    import app.main
    q = _Qbit()
    monkeypatch.setattr(app.main, '_qbit_new_session', q)
    real_get = app.main.settings.get
    monkeypatch.setattr(app.main.settings, 'get',
                        lambda k, d=None: 'http://qbit.test:8080' if k == 'qbittorrent.url' else real_get(k, d))
    return q


def _remove(c, delete, env=LAN, headers=HDR, how='query'):
    url = f'/api/downloads/remove/{H}'
    if how == 'query':
        return c.delete(f'{url}?deleteFiles={"true" if delete else "false"}', environ_base=env, headers=headers)
    return c.post(url, json={'delete_files': delete}, environ_base=env, headers=headers)


@pytest.mark.parametrize('how', ['query', 'body'])
def test_deleting_files_needs_the_signed_in_owner(full, qbit, how):
    owner = _client(full)
    r = _remove(full.test_client(), True, how=how)                     # implicit LAN owner
    assert r.status_code == 401 and r.get_json() == {'error': 'Sign in first', 'sign_in': True}
    assert qbit.posts == []                                            # qBit never contacted
    r = _remove(owner, True, how=how)
    assert r.status_code == 200 and r.get_json()['deleteFiles'] is True
    assert qbit.posts[-1] == ('torrents/delete', {'hashes': H, 'deleteFiles': 'true'})


def test_deleting_files_denies_everyone_else(full, qbit):
    _client(full)
    for c in (_client(full, name='admin2'), _client(full, admin=False, name='viewer'),
              _client(full, admin=False, name='guest')):
        assert _remove(c, True).status_code == 403
    for env in (LAN, REMOTE):
        r = _remove(full.test_client(), True, env, dict(HDR, **{'X-API-Key': KEY}))   # kiosk / companion apps
        assert r.status_code == 401 and r.get_json()['sign_in'] is True
    assert _remove(full.test_client(), True, REMOTE).status_code == 401
    assert qbit.posts == []


def test_removing_without_files_keeps_todays_access(full, qbit):
    _client(full)
    r = _remove(full.test_client(), False)                             # implicit LAN owner, as today
    assert r.status_code == 200 and r.get_json()['deleteFiles'] is False
    assert qbit.posts[-1] == ('torrents/delete', {'hashes': H, 'deleteFiles': 'false'})
    r = _remove(full.test_client(), False, how='body')
    assert r.status_code == 200
    r = _remove(full.test_client(), False, REMOTE, dict(HDR, **{'X-API-Key': KEY}))   # service key, as today
    assert r.status_code == 200


# ------------------------------------------------- /api/downloads-guard/run (prod blocker)

GUARD_RUN = '/api/downloads-guard/run'


@pytest.fixture
def guard(full, monkeypatch):
    from app import downloads_guard
    runs = []
    monkeypatch.setattr(downloads_guard, 'trigger_manual_run', lambda dry_run=True: runs.append(dry_run))
    full.runs = runs
    return full


def _run(c, dry, env=LAN, headers=HDR):
    body = {} if dry is None else {'dry_run': dry}
    return c.post(GUARD_RUN, json=body, environ_base=env, headers=headers)


@pytest.mark.parametrize('env', [LAN, REMOTE])
def test_guard_real_run_signed_in_owner_may(guard, env):
    owner = _client(guard)
    r = _run(owner, False, env)
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'dry_run': False}
    assert guard.runs == [False]


def test_guard_real_run_implicit_lan_owner_gets_401(guard):
    _client(guard)
    r = _run(guard.test_client(), False)
    assert r.status_code == 401 and r.get_json() == {'error': 'Sign in first', 'sign_in': True}
    assert r.headers['Cache-Control'] == 'no-store'
    assert guard.runs == []


@pytest.mark.parametrize('env', [LAN, REMOTE])
def test_guard_real_run_service_key_gets_401(guard, env):
    _client(guard)
    r = _run(guard.test_client(), False, env, dict(HDR, **{'X-API-Key': KEY}))
    assert r.status_code == 401 and r.get_json()['sign_in'] is True
    assert guard.runs == []


def test_guard_real_run_remote_no_session_gets_401(guard):
    _client(guard)
    r = _run(guard.test_client(), False, REMOTE)
    assert r.status_code == 401
    assert guard.runs == []


@pytest.mark.parametrize('who', ['admin', 'viewer', 'guest'])
def test_guard_real_run_others_get_403(guard, who):
    _client(guard)
    c = _client(guard, admin=(who == 'admin'), name=who)
    r = _run(c, False)
    assert r.status_code == 403 and r.get_json() == {'error': 'Owner only'}
    assert guard.runs == []


def test_guard_real_run_plex_approved_viewer_gets_403(guard, monkeypatch):
    from app import user_auth
    _client(guard)
    c = _client(guard, admin=False, name='plexer')
    monkeypatch.setattr(user_auth, 'is_plex_approved', lambda u: True)
    assert _run(c, False).status_code == 403 and guard.runs == []


@pytest.mark.parametrize('dry', [True, None])
def test_guard_dry_run_keeps_todays_access(guard, dry):
    """Dry runs delete nothing: the implicit LAN owner still may, as today."""
    _client(guard)
    r = _run(guard.test_client(), dry)
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'dry_run': True}
    owner = _client(guard, name='owner2')
    assert _run(owner, dry).status_code == 200
    assert guard.runs == [True, True]


def test_guard_dry_run_remote_no_session_is_unchanged(guard):
    _client(guard)
    assert _run(guard.test_client(), True, REMOTE).status_code == 401
    assert guard.runs == []


# ------------------------------------------------- tracker routes' 401

def test_tracker_routes_401_says_sign_in(full):
    _client(full)
    r = full.test_client().get('/api/trackers', environ_base=LAN, headers=HDR)
    assert r.status_code == 401 and r.get_json()['sign_in'] is True


# ------------------------------------------------- full boot smoke

def test_full_boot_with_init_db_guards_the_real_run(tmp_path):
    """A fresh interpreter: user_auth.init_db(), start_services, then the
    destructive routes answer 401 sign_in for the implicit LAN owner."""
    import json
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    data = tmp_path / 'data'
    data.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ('LUMA_URL_BASE', 'SETTINGS_PATH', 'LUMA_MINIMAL')}
    env.update({'DATABASE_PATH': str(data / 'media.db'), 'LUMA_STATE_DIR': str(data), 'LUMA_DATA_DIR': str(data),
                'BASE_DIR': str(data), 'LUMA_CACHE_DIR': str(tmp_path / 'cache'),
                'HERO_CACHE_DIR': str(tmp_path / 'hero'), 'LUMA_SKIP_BACKGROUND_THREADS': '1'})
    script = textwrap.dedent('''
        import json
        from werkzeug.test import Client
        import app.user_auth as ua
        ua.init_db()
        ua.create_user('owner', '1234', is_admin=True)
        import app.main as m
        m.app.config['START_BACKGROUND_THREADS'] = False
        m.start_services(m.app)
        c = Client(m.app.wsgi_app)
        lan = {'REMOTE_ADDR': '192.168.1.20'}
        h = {'X-Requested-With': 'XMLHttpRequest'}
        out = {}
        for name, url, body in (('run', '/api/downloads-guard/run', {'dry_run': False}),
                                ('cleanup', '/api/storage/downloads/cleanup', {'target_bytes': 5}),
                                ('remove', '/api/downloads/remove/' + 'a' * 40 + '?deleteFiles=true', {})):
            r = c.post(url, json=body, environ_base=lan, headers=h)
            out[name] = [r.status_code, json.loads(r.get_data() or b'null')]
        me = c.get('/api/auth/me', environ_base=lan)
        out['me'] = json.loads(me.get_data())
        print(json.dumps(out))
    ''')
    p = subprocess.run([sys.executable, '-c', script], cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-3000:]
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out['me']['implicit'] is True
    for name in ('run', 'cleanup', 'remove'):
        assert out[name] == [401, {'error': 'Sign in first', 'sign_in': True}], (name, out[name])
