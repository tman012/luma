"""Existing installs must not flip to auth-required on LAN (round 3, items 6 and 7)."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import app.user_auth as user_auth
from app.settings_manager import SettingsManager

ROOT = Path(__file__).resolve().parents[1]

RESTART = textwrap.dedent('''
    import json
    from werkzeug.test import Client
    import app.main as m
    m.app.config['START_BACKGROUND_THREADS'] = False
    m.start_services(m.app)
    c = Client(m.app.wsgi_app)
    lan = c.get('/api/settings', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    wan = c.get('/api/settings', environ_base={'REMOTE_ADDR': '203.0.113.9'})
    me = c.get('/api/auth/me', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    print(json.dumps({'lan': lan.status_code, 'wan': wan.status_code,
                      'me': json.loads(me.get_data()), 'mode': m.settings.get('security.auth_required')}))
''')


def test_existing_users_no_security_keys_two_restarts(tmp_path):
    """An install with users and no saved security keys stays disabled_local
    across two real restarts (fresh interpreter, start_services) and LAN never gets 401."""
    data = tmp_path / 'data'
    data.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ('LUMA_URL_BASE', 'SETTINGS_PATH')}
    env.update({'DATABASE_PATH': str(data / 'media.db'), 'LUMA_STATE_DIR': str(data), 'LUMA_DATA_DIR': str(data),
                'BASE_DIR': str(data), 'LUMA_CACHE_DIR': str(tmp_path / 'cache'),
                'HERO_CACHE_DIR': str(tmp_path / 'hero'), 'LUMA_SKIP_BACKGROUND_THREADS': '1'})
    # Existing install: an admin and a regular user, settings with no security section.
    seed = ("import app.user_auth as ua; ua.init_db(); "
            "ua.create_user('owner', '1234', is_admin=True); ua.create_user('guest', '5678')")
    subprocess.run([sys.executable, '-c', seed], cwd=ROOT, env=env, check=True, timeout=60)
    (data / 'settings.json').write_text(json.dumps({'plex': {'url': 'http://plex.invalid:32400'},
                                                    'media': {'database_path': str(data / 'library.db')}}))

    for restart in (1, 2):
        out = subprocess.run([sys.executable, '-c', RESTART], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-3000:]
        r = json.loads(out.stdout.strip().splitlines()[-1])
        assert r['lan'] == 200, (restart, r)
        assert r['wan'] == 401, (restart, r)
        assert r['me']['implicit'] is True and r['me']['auth_required'] == 'disabled_local', r
        saved = json.loads((data / 'settings.json').read_text()).get('security', {})
        assert saved.get('access_mode') in (None, ''), saved
        assert saved.get('auth_required') in (None, 'disabled_local'), saved


def test_main_never_writes_access_mode():
    src = (ROOT / 'app' / 'main.py').read_text()
    assert 'access_mode' not in src
    src = (ROOT / 'app' / 'start_flow.py').read_text()
    assert "'access_mode'" not in src


def test_access_mode_helpers_removed():
    import app.access as access
    assert not hasattr(access, 'mode') and not hasattr(access, 'needs_pin')


def test_lan_no_401_and_remote_401(test_app):
    user_auth.create_user('owner', '1234', is_admin=True)
    c = test_app.test_client()
    assert c.get('/api/settings', environ_base={'REMOTE_ADDR': '192.168.1.100'}).status_code == 200
    assert c.get('/api/settings', environ_base={'REMOTE_ADDR': '203.0.113.100'}).status_code == 401


def test_security_api_uses_auth_required(test_app):
    import app.main
    owner = user_auth.create_user('owner', '1234', is_admin=True)
    c = test_app.test_client()
    lan = {'REMOTE_ADDR': '192.168.1.20'}
    g = c.get('/api/security', environ_base=lan).get_json()
    assert g['auth_required'] == 'disabled_local' and 'mode' not in g
    assert c.put('/api/security', json={'auth_required': 'pin'}, environ_base=lan).status_code == 400
    assert c.put('/api/security', json={'mode': 'pin'}, environ_base=lan).get_json()['auth_required'] == 'disabled_local'
    r = c.put('/api/security', json={'auth_required': 'enabled'}, environ_base=lan)
    assert r.status_code == 200 and r.get_json()['auth_required'] == 'enabled'
    assert app.main.settings.get('security.auth_required') == 'enabled'
    me = c.get('/api/auth/me', environ_base=lan).get_json()
    assert me['auth_required'] == 'enabled' and 'access_mode' not in me
    assert test_app.test_client().get('/api/settings', environ_base=lan).status_code == 401


def test_frontend_uses_auth_required():
    src = (ROOT / 'frontend' / 'src' / 'pages' / 'settings' / 'SecurityTab.tsx').read_text()
    assert 'auth_required' in src and 'pin_remote' not in src and "'open'" not in src
    start = (ROOT / 'frontend' / 'src' / 'pages' / 'StartPage.tsx').read_text()
    assert 'pin_remote' not in start and "{ mode" not in start
