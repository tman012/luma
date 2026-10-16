"""Startup work runs in start_services(app), under gunicorn and in dev (round 3, item 4)."""

import importlib.util
import re
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]

AUTOMATION_MODULES = [
    'app.anticipated_watchlist', 'app.auto_rename', 'app.daily_missing', 'app.downloads_guard',
    'app.franchise_completer', 'app.genre_roulette', 'app.latest_auto_grab', 'app.movie_collections',
    'app.movie_night', 'app.now_available_watchlist', 'app.trending_tv_watchlist',
    'app.trending_watchlist', 'app.upgrade_4k_auto_grab', 'app.upgrade_scheduler',
    'app.watchlist_digital_dates', 'app.push_service',
]


@pytest.fixture
def patched(test_app, monkeypatch):
    """start_services with every side effect replaced by mocks (no real threads)."""
    import sys
    import app.main
    monkeypatch.setattr(app.main, '_AUTOMATIONS_DISABLED', False)
    fakes = {name: mock.MagicMock(name=name) for name in AUTOMATION_MODULES}
    with mock.patch.dict(sys.modules, fakes), \
            mock.patch('app.main.init_database') as init_db, \
            mock.patch.object(app.main.user_auth, 'init_db') as ua_init, \
            mock.patch.object(app.main.fast_search_index, 'start') as search_start, \
            mock.patch('app.main.explore_cache_updater') as explore, \
            mock.patch('threading.Thread') as thread:
        starters = {n: m for n, m in fakes.items() if n != 'app.push_service'}
        yield init_db, ua_init, thread, {'search': search_start, 'explore': explore}, starters


def test_db_init_always_runs_threads_off(test_app, patched):
    from app.main import start_services
    init_db, ua_init, thread, mocks, starters = patched
    test_app.config['START_BACKGROUND_THREADS'] = False
    start_services(test_app)
    init_db.assert_called_once()
    ua_init.assert_called_once()
    thread.assert_not_called()
    for m in mocks.values():
        m.assert_not_called()
    for name, mod in starters.items():
        started = [c for c in mod.mock_calls if c[0].startswith('start_')]
        assert not started, name


def test_flag_read_at_call_time(test_app, patched):
    from app.main import start_services
    init_db, ua_init, thread, mocks, starters = patched
    test_app.config['START_BACKGROUND_THREADS'] = True
    start_services(test_app)
    assert thread.called
    for m in mocks.values():
        m.assert_called_once()
    for name, mod in starters.items():
        started = [c for c in mod.mock_calls if c[0].startswith('start_')]
        assert len(started) == 1, name


def test_not_in_main_block():
    src = (ROOT / 'app' / 'main.py').read_text()
    main_block = src.split("if __name__ == '__main__':", 1)[1]
    assert 'start_services(app)' in main_block
    for call in ('init_database()', 'user_auth.init_db()', 'fast_search_index.start()',
                 'start_monitor_thread()', 'threading.Thread('):
        assert call not in main_block, call


def test_no_env_only_background_switch():
    """start_services reads app.config at call time, not an env var; access.py has no switch."""
    src = (ROOT / 'app' / 'main.py').read_text()
    body = src.split('def start_services', 1)[1].split('\ndef ', 1)[0]
    assert "app_instance.config.get('START_BACKGROUND_THREADS'" in body
    assert 'os.environ' not in body
    assert 'BACKGROUND' not in (ROOT / 'app' / 'access.py').read_text()


def _load_gunicorn_conf():
    spec = importlib.util.spec_from_file_location('gunicorn_conf', ROOT / 'gunicorn.conf.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gunicorn_conf():
    conf = _load_gunicorn_conf()
    assert conf.workers == 1
    assert conf.threads == 8
    assert conf.worker_class == 'gthread'
    with mock.patch('app.main.start_services') as start:
        conf.post_worker_init(mock.Mock(pid=123))
    import app.main
    start.assert_called_once_with(app.main.app)


def test_entrypoint_execs_gunicorn():
    sh = (ROOT / 'entrypoint.sh').read_text()
    execs = re.findall(r'^\s*exec\s+(.+)$', sh, re.M)
    assert execs, 'entrypoint must exec the server'
    for line in execs:
        assert line.strip() == 'gunicorn -c gunicorn.conf.py app.main:app', line
    assert 'python -m app.main' not in sh and 'flask run' not in sh


def test_dockerfile_uses_entrypoint():
    df = (ROOT / 'Dockerfile').read_text()
    assert 'entrypoint.sh' in df
    assert 'gunicorn' in (ROOT / 'requirements.txt').read_text()
