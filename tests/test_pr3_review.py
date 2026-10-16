"""Merge of #3 into #2: startup probe, scheduler hook, hero file containment,
compose fallback, refresh lock and e2e docs."""

import errno
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent


# 1) startup probe in gunicorn on_starting; scheduler in start_services ----------

def _load_gunicorn_conf():
    ns = {'__name__': 'gunicorn_conf_test'}
    exec(compile((ROOT / 'gunicorn.conf.py').read_text(), 'gunicorn.conf.py', 'exec'), ns)
    return ns


def _server():
    import logging
    return types.SimpleNamespace(log=logging.getLogger('gunicorn-test'))


class _NoSettings:
    def get(self, key, default=None):
        return default


def test_config_load_runs_probe_before_settings():
    order = []
    with patch('app.paths.ensure_writable_or_exit', lambda: order.append('probe')), \
            patch('app.settings_manager.SettingsManager', lambda *a, **k: order.append('settings') or _NoSettings()):
        ns = _load_gunicorn_conf()
    assert order[:2] == ['probe', 'settings']
    ns['on_starting'](_server())


def test_config_load_exits_for_read_only_dir(tmp_path):
    if os.geteuid() == 0:
        pytest.skip('root can write read-only folders')
    ro = tmp_path / 'ro'
    ro.mkdir()
    os.chmod(ro, 0o555)
    env = dict(os.environ, LUMA_DATA_DIR=str(ro), LUMA_STATE_DIR=str(ro))
    env.pop('SETTINGS_PATH', None)
    code = "import runpy; runpy.run_path('gunicorn.conf.py')"
    out = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True,
                         env=env, timeout=60)
    os.chmod(ro, 0o755)
    assert out.returncode == 1
    assert 'not writable' in out.stderr
    assert not (ro / 'settings.json').exists()


def test_config_load_does_not_import_app_main():
    """Workers import app.main later; the master must not."""
    code = ("import sys, types, runpy, logging; ns = runpy.run_path('gunicorn.conf.py'); "
            "ns['on_starting'](types.SimpleNamespace(log=logging.getLogger('g'))); "
            "print('app.main' in sys.modules)")
    out = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True,
                         text=True, env=dict(os.environ), timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == 'False'


@pytest.mark.parametrize('start_bg,expected', [(True, 1), (False, 0)])
def test_start_services_starts_hero_scheduler(monkeypatch, start_bg, expected):
    import app.main as m
    calls = []
    monkeypatch.setattr(m.hero_cache, 'start_scheduler', lambda *a, **k: calls.append(1))
    for name in ('init_database', 'explore_cache_updater', 'update_explore_cache', 'initialize_caches'):
        if hasattr(m, name):
            monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m.fast_search_index, 'start', lambda *a, **k: None)
    monkeypatch.setattr(m, '_AUTOMATIONS_DISABLED', True)
    monkeypatch.delenv('FLASK_ENV', raising=False)
    monkeypatch.setitem(m.app.config, 'START_BACKGROUND_THREADS', start_bg)
    with patch('threading.Thread'):
        m.start_services(m.app)
    assert len(calls) == expected


def test_reloader_parent_defers_scheduler(monkeypatch):
    import app.main as m
    calls = []
    monkeypatch.setattr(m.hero_cache, 'start_scheduler', lambda *a, **k: calls.append(1))
    for name in ('init_database', 'explore_cache_updater', 'update_explore_cache', 'initialize_caches'):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m.fast_search_index, 'start', lambda *a, **k: None)
    monkeypatch.setattr(m, '_AUTOMATIONS_DISABLED', True)
    monkeypatch.setenv('FLASK_ENV', 'development')
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    monkeypatch.setitem(m.app.config, 'START_BACKGROUND_THREADS', True)
    with patch('threading.Thread'):
        m.start_services(m.app)
    assert calls == []


def test_data_dir_resolvers_use_app_paths(tmp_path, monkeypatch):
    from app import first_run, settings_manager
    monkeypatch.setenv('LUMA_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('LUMA_STATE_DIR', raising=False)
    assert first_run.data_dir() == tmp_path
    assert settings_manager._infer_data_dir() == tmp_path


# 2) hero image containment ----------------------------------------------------

@pytest.fixture
def hero_client(tmp_path, monkeypatch):
    import app.main as m
    import app.user_auth
    app.user_auth.init_db()   # load_current_user reads users; passes when run alone
    hero = tmp_path / 'hero'
    hero.mkdir()
    (hero / 'ok.jpg').write_bytes(b'ok')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.txt').write_text('secret')
    os.symlink(outside / 'secret.txt', hero / 'link.jpg')
    os.symlink(outside, hero / 'dir')
    os.symlink(hero / 'ok.jpg', hero / 'inside.jpg')
    monkeypatch.setitem(m.app.config, 'HERO_CACHE_DIR', str(hero))
    return m.app.test_client()


def test_hero_regular_file_served(hero_client):
    r = hero_client.get('/static/hero_cache/ok.jpg')
    assert (r.status_code, r.data) == (200, b'ok')


@pytest.mark.parametrize('name', ['link.jpg', 'dir/secret.txt', '../outside/secret.txt'])
def test_hero_symlink_out_of_base_rejected(hero_client, name):
    r = hero_client.get('/static/hero_cache/' + name)
    assert r.status_code == 404 and b'secret' not in r.data


def test_hero_symlink_inside_base_allowed(hero_client):
    assert hero_client.get('/static/hero_cache/inside.jpg').data == b'ok'


def test_contained_file_helper(tmp_path):
    from app.main import _contained_file
    (tmp_path / 'a').write_text('x')
    assert _contained_file(str(tmp_path), 'a') == os.path.realpath(tmp_path / 'a')
    assert _contained_file(str(tmp_path), '.') is None
    assert _contained_file(str(tmp_path), 'missing') is None


# 3) compose fallback -----------------------------------------------------------

def test_compose_host_dir_fallbacks():
    src = (ROOT / 'docker-compose.yml').read_text()
    assert '${LUMA_DATA_HOST_DIR:-${LUMA_DATA_DIR:-./data}}:/app/data' in src
    assert '${LUMA_CACHE_HOST_DIR:-${LUMA_CACHE_DIR:-./cache}}:/app/cache' in src
    assert re.search(r'^\s*- LUMA_DATA_DIR=/app/data$', src, re.M)
    assert re.search(r'^\s*- LUMA_CACHE_DIR=/app/cache$', src, re.M)


def test_dockerfile_spa_and_cache_dir():
    src = (ROOT / 'Dockerfile').read_text()
    assert '/app/spa' in src and re.search(r'mkdir -p [^\n]*/app/cache', src)


# 4) refresh lock -----------------------------------------------------------------

def _hero(tmp_path):
    from app.hero_cache import HeroCache
    with patch.dict(os.environ, {'HERO_CACHE_DIR': str(tmp_path / 'h'),
                                 'LUMA_DATA_DIR': str(tmp_path / 'd')}):
        return HeroCache()


def test_lock_file_opened_without_truncation(tmp_path):
    h = _hero(tmp_path)
    lock = h.cache_dir / '.refresh.lock'
    lock.write_text('keep')
    with patch.object(h, '_refresh_hero_content_unlocked', return_value=[]):
        h.refresh_hero_content()
    assert lock.read_text() == 'keep'
    src = (ROOT / 'app' / 'hero_cache.py').read_text()
    assert 'os.O_CREAT | os.O_RDWR' in src


def test_lock_open_error_skips_refresh(tmp_path):
    h = _hero(tmp_path)
    real_open = os.open

    def boom(path, *a, **k):
        if str(path).endswith('.refresh.lock'):
            raise OSError(errno.EACCES, 'denied')
        return real_open(path, *a, **k)

    with patch('app.hero_cache.os.open', side_effect=boom), \
            patch.object(h, '_refresh_hero_content_unlocked') as run, \
            patch.object(h, 'load_cache', return_value=['cached']):
        assert h.refresh_hero_content() == ['cached']
    run.assert_not_called()
    assert h._refresh_lock.acquire(blocking=False)  # released after the error


# 5) e2e docs ------------------------------------------------------------------------

def test_e2e_readme_exports_spa_dir():
    src = (ROOT / 'tests' / 'e2e' / 'README.md').read_text()
    assert 'export LUMA_SPA_DIR=$STATIC/spa' in src


def test_single_conftest_sandbox():
    assert not (ROOT / 'tests' / 'conftest.py').exists()
    data = os.environ['LUMA_DATA_DIR']
    assert not data.startswith('/app') and os.path.dirname(os.environ['DATABASE_PATH']) == data
