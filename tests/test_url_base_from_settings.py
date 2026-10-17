"""URL base resolved once at startup into app.config['URL_BASE'] (round 3, item 3).

Startup is exercised in a fresh interpreter so the real import-time
resolution runs (no module reloads inside the test process).
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PROBE = textwrap.dedent('''
    import json
    from werkzeug.test import Client
    import app.main as m
    import app.user_auth as ua
    ua.init_db()
    if not ua.list_users():
        ua.create_user('owner', '1234', is_admin=True)
    m.settings.update({'security': {'auth_required': 'enabled'}})
    c = Client(m.app.wsgi_app)
    base = m.app.config['URL_BASE']
    man = c.get(base + '/manifest.json', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    page = c.get(base + '/settings', environ_base={'REMOTE_ADDR': '203.0.113.9'})
    outside = c.get('/manifest.json', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    print(json.dumps({
        'base': base,
        'manifest': json.loads(man.get_data()),
        'login_location': page.headers.get('Location'),
        'outside_status': outside.status_code,
    }))
''')


def _run(tmp_path, settings, env_base=None):
    data = tmp_path / 'data'
    data.mkdir()
    settings = {**settings, 'media': {'database_path': str(data / 'library.db')}}
    (data / 'settings.json').write_text(json.dumps(settings))
    env = {k: v for k, v in os.environ.items() if k not in ('LUMA_URL_BASE', 'SETTINGS_PATH')}
    env.update({
        'DATABASE_PATH': str(data / 'media.db'),
        'LUMA_CACHE_DIR': str(tmp_path / 'cache'),
        'HERO_CACHE_DIR': str(tmp_path / 'hero'),
        'LUMA_SKIP_BACKGROUND_THREADS': '1',
        'LUMA_STATE_DIR': str(data),
        'LUMA_DATA_DIR': str(data),
        'BASE_DIR': str(data),
    })
    if env_base is not None:
        env['LUMA_URL_BASE'] = env_base
    out = subprocess.run([sys.executable, '-c', PROBE], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_url_base_set_only_in_settings(tmp_path):
    r = _run(tmp_path, {'general': {'url_base': 'luma-test/'}})
    assert r['base'] == '/luma-test'
    assert r['manifest']['start_url'] == '/luma-test/'
    assert r['manifest']['scope'] == '/luma-test/'
    assert all(i['src'].startswith('/luma-test/static/') for i in r['manifest']['icons'])
    assert r['login_location'].startswith('/luma-test/login?next=')
    assert r['outside_status'] == 404


def test_env_overrides_settings(tmp_path):
    r = _run(tmp_path, {'general': {'url_base': '/from-settings'}}, env_base='/from-env')
    assert r['base'] == '/from-env'
    assert r['manifest']['start_url'] == '/from-env/'


def test_no_base(tmp_path):
    r = _run(tmp_path, {})
    assert r['base'] == ''
    assert r['manifest']['start_url'] == '/'
    assert r['manifest']['icons'][0]['src'].startswith('/static/')


def test_readers_use_app_config(test_app, monkeypatch):
    """Manifest, auth guard and login read app.config['URL_BASE'] at request time."""
    import app.main
    import app.user_auth as user_auth
    user_auth.create_user('owner', '1234', is_admin=True)
    monkeypatch.setitem(test_app.config, 'URL_BASE', '/mybase')  # restored after the test
    c = test_app.test_client()
    man = c.get('/manifest.json').get_json()
    assert man['start_url'] == '/mybase/'
    assert man['icons'][0]['src'].startswith('/mybase/static/')
    r = c.get('/settings?tab=x', environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert r.status_code == 302
    assert r.headers['Location'] == '/mybase/login?next=/settings%3Ftab%3Dx'
    assert 'get_url_base' not in open(Path(app.main.__file__).parent / 'auth_routes.py').read()
