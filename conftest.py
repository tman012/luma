"""Pytest configuration for Luma tests."""

import os
import tempfile
import pytest
from pathlib import Path
import shutil


# Unit-test sandbox: every Luma data/cache path points at a private temp dir.
# Set in pytest_configure, before any test module imports app.* (app.main and
# app.hero_cache read them at import), so a bare `pytest` never touches the
# container-only /app paths or a LUMA_* dir set in the shell. Skipped when only
# tests/e2e is collected: e2e talks to a separately started server and must
# see that server's paths.
_REPO_ROOT = Path(__file__).resolve().parent
_E2E_DIR = _REPO_ROOT / 'tests' / 'e2e'
_TEMP_DIRS = []
_SANDBOX = {'data': None}      # the sandbox data dir once pytest_configure set it up


class UnsafeDatabaseReset(RuntimeError):
    """A destructive test reset was about to touch a database outside the sandbox."""


def sandbox_db_path(db_path=None) -> str:
    """The database path, if it is safe to wipe: the unit-test sandbox is
    active and the file lies inside its temp data dir. Raises
    UnsafeDatabaseReset otherwise, before any destructive SQL runs."""
    root = _SANDBOX['data']
    if db_path is None:
        import app.user_auth
        db_path = app.user_auth._get_db_path()
    if not root:
        raise UnsafeDatabaseReset(f'test sandbox is not active; refusing to reset {db_path}')
    real, base = os.path.realpath(db_path), os.path.realpath(root)
    tmp = os.path.realpath(tempfile.gettempdir())
    if os.path.commonpath([real, base]) != base or os.path.commonpath([base, tmp]) != tmp:
        raise UnsafeDatabaseReset(f'refusing to reset {db_path}: not inside the test sandbox {root}')
    return real


def _wipe_users(conn_factory=None) -> None:
    import app.user_auth
    sandbox_db_path()
    conn = (conn_factory or app.user_auth._get_db)()
    try:
        conn.execute('DELETE FROM users')
        conn.execute('DELETE FROM user_sessions')
        conn.commit()
    finally:
        conn.close()


def _is_e2e_item(item) -> bool:
    try:
        p = Path(str(item.fspath)).resolve()
    except Exception:
        return False
    return p == _E2E_DIR or _E2E_DIR in p.parents


def _only_e2e(config) -> bool:
    base = Path(config.invocation_params.dir)
    paths = [(base / str(a).split('::')[0]).resolve() for a in config.args] or [base.resolve()]
    return all(p == _E2E_DIR or _E2E_DIR in p.parents for p in paths)


def pytest_configure(config):
    if _only_e2e(config):
        return
    cache = tempfile.mkdtemp(prefix='luma_test_cache_')
    data = tempfile.mkdtemp(prefix='luma_test_data_')
    _TEMP_DIRS.extend([cache, data])
    _SANDBOX['data'] = data
    os.makedirs(os.path.join(cache, 'hero'), exist_ok=True)
    os.environ.update({
        'LUMA_DATA_DIR': data,
        'LUMA_CACHE_DIR': cache,
        'HERO_CACHE_DIR': os.path.join(cache, 'hero'),
        'DATABASE_PATH': os.path.join(data, 'media.db'),
        'SETTINGS_PATH': os.path.join(data, 'settings.json'),
        'LUMA_SKIP_BACKGROUND_THREADS': '1',
        'BASE_DIR': data,
    })
    os.environ.setdefault('LUMA_DISABLE_AUTOMATIONS', '1')
    os.environ.setdefault('LUMA_DISABLE_ADB', '1')
    os.environ.setdefault('FLASK_STATIC_FOLDER', str(_REPO_ROOT / 'static'))


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Create the auth tables up front (in the app, start_services does this),
    so tests that use app.main without the test_app fixture don't depend on
    running after one that does. Remove the temp directories at the end."""
    if _SANDBOX['data']:
        import app.user_auth
        sandbox_db_path()
        app.user_auth.init_db()
    yield
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_shared_state(request):
    """Reset shared state between tests to ensure test isolation.

    Only for pytest-based unit tests inside the sandbox: never for e2e items
    (they talk to a separately started server) or unittest.TestCase tests,
    and the user wipe first checks the database is inside the sandbox.
    """
    if not _SANDBOX['data'] or _is_e2e_item(request.node):
        yield
        return
    # Skip for unittest.TestCase tests
    if hasattr(request, 'instance') and hasattr(request.instance, '__class__'):
        if 'unittest' in str(type(request.instance)):
            yield
            return
    
    # Reset the sign-in and request-page rate limiters
    from app import login_limiter, request_portal
    login_limiter._fail.clear()
    request_portal._fails.clear()
    # Some tests swap in a mock settings object; put the real one back after
    saved_portal_settings = request_portal._settings
    # Global security settings (auth_required, trusted_proxies, hostnames...)
    # are restored after the test too, so a test that changes them cannot put
    # later tests behind sign-in.
    settings_snapshot = _snapshot_main_settings()
    
    # Clear user database between tests (sandbox only; UnsafeDatabaseReset
    # propagates and fails the test rather than touching another database)
    try:
        _wipe_users()
    except UnsafeDatabaseReset:
        raise
    except Exception:
        pass
    
    yield
    
    # Cleanup after test. Accounts are wiped on the way out as well: the
    # unittest.TestCase suites skip this fixture, so state left behind by the
    # previous pytest test (an account means "sign-in required") used to make
    # them return 302/401 depending on the random order.
    login_limiter._fail.clear()
    request_portal._fails.clear()
    request_portal._settings = saved_portal_settings
    _restore_main_settings(settings_snapshot)
    try:
        _wipe_users()
    except UnsafeDatabaseReset:
        raise
    except Exception:
        pass


def _snapshot_main_settings():
    import copy
    import sys
    main = sys.modules.get('app.main')
    sm = getattr(main, 'settings', None) if main else None
    if sm is None:
        return None
    with sm._lock:
        return sm, copy.deepcopy(sm._settings)


def _restore_main_settings(snapshot) -> None:
    if snapshot is None:
        return
    sm, saved = snapshot
    with sm._lock:
        if sm._settings != saved:
            sm._settings = saved
            try:
                sm._save()
            except Exception:
                pass


@pytest.fixture(autouse=True)
def isolate_auth_state(request):
    """Every unit test, unittest.TestCase ones included, leaves sign-in state
    as it found it: the security settings section is restored and users (and
    their sessions) created during the test are removed. Without this, a test
    that turned auth on or left a user behind made later tests that expect
    LAN no-sign-in fail with 302/401, depending on pytest-randomly's order."""
    if not _SANDBOX['data'] or _is_e2e_item(request.node):
        yield
        return
    import sys
    main = sys.modules.get('app.main')
    sec_before = main.settings.get_section('security') if main else None
    # API keys too: a TMDB key left behind sends later tests' hero and search
    # paths to the network (thread pools, real requests) in some orders.
    apis_before = main.settings.get_section('apis') if main else None
    max_uid = _max_user_id()
    yield
    main = sys.modules.get('app.main')
    if main is None:
        return
    if apis_before is not None and main.settings.get_section('apis') != apis_before:
        main.settings.update({'apis': apis_before})
    if sec_before is not None and main.settings.get_section('security') != sec_before:
        main.settings.update({'security': sec_before})
    elif sec_before is None:
        sec = main.settings.get_section('security')
        if sec.get('auth_required') != 'disabled_local' or sec.get('trusted_proxies'):
            main.settings.update({'security': {'auth_required': 'disabled_local', 'trusted_proxies': '',
                                               'trusted_proxy_header': ''}})
    _drop_users_after(max_uid)


def _max_user_id():
    try:
        import app.user_auth
        sandbox_db_path()
        conn = app.user_auth._get_db()
        try:
            return conn.execute('SELECT COALESCE(MAX(id), 0) FROM users').fetchone()[0]
        finally:
            conn.close()
    except UnsafeDatabaseReset:
        raise
    except Exception:
        return None


def _drop_users_after(max_uid):
    if max_uid is None:
        return
    try:
        import app.user_auth
        sandbox_db_path()
        conn = app.user_auth._get_db()
        try:
            conn.execute('DELETE FROM user_sessions WHERE user_id > ?', (max_uid,))
            conn.execute('DELETE FROM users WHERE id > ?', (max_uid,))
            conn.commit()
        finally:
            conn.close()
    except UnsafeDatabaseReset:
        raise
    except Exception:
        pass


@pytest.fixture
def test_app():
    """Create a real Flask app from app.main for integration tests."""
    # Ensure test environment is set up
    import app.main
    import app.user_auth
    
    flask_app = app.main.app
    flask_app.config['TESTING'] = True
    
    # Initialize database schema and clear users (sandbox only)
    sandbox_db_path()
    app.user_auth.init_db()
    _wipe_users()
    
    # Reset settings to defaults for each test
    app.main.settings.update({
        'security': {
            'auth_required': 'disabled_local',  # Default: LAN no sign-in
            'session_secret': '',  # Will be auto-generated
            'trusted_proxies': '',
            'trusted_proxy_header': '',
            'hostnames': '',
            'allow_signup': False,
        }
    })
    flask_app.config['URL_BASE'] = ''
    flask_app.config['START_BACKGROUND_THREADS'] = False
    
    return flask_app


@pytest.fixture(autouse=True)
def tv_plex_clean():
    """v171: the S02E03 calendar flake. tv_plex keeps a module-level 'in Plex' index; a Plex test
    that indexed Severance S02E03 (tests/test_tv_downloaded_v157 and friends) left it behind, so a
    later calendar test read S02E03 as downloaded, depending on order. Not date/time: start every
    test with an empty index."""
    from app import tv_plex

    def clear():
        with tv_plex._lock:
            tv_plex._have.clear()
            tv_plex._shows.clear()
            tv_plex._new.clear()
            tv_plex._state.update(built_at=0.0, down=False, server=None)
    cfg = dict(tv_plex._cfg)              # v173 (Tester FF-5): a test's FakePlex is unregistered after it
    clear()
    yield
    tv_plex._cfg.clear()
    tv_plex._cfg.update(cfg)
    clear()


@pytest.fixture(autouse=True)
def tracker_test_env(monkeypatch):
    """Tracker code never does real DNS or real pauses in tests: every name
    resolves to one public address and the AvistaZ sign-in gap doesn't
    sleep, unless a test swaps in its own."""
    from app import tracker_clients, tracker_http
    monkeypatch.setattr(tracker_http, 'resolve', lambda host, port: ['93.184.215.14'])
    monkeypatch.setattr(tracker_clients, '_sleep', lambda s: None)


@pytest.fixture(autouse=True)
def _no_tvmaze():
    """TVmaze (Calendar air times) is never asked for real in tests: no match, no wait."""
    from app import tv_airtimes
    tv_airtimes.configure(get=lambda path, params: None, sleep=lambda s: None)
    yield
    tv_airtimes.configure()


@pytest.fixture(autouse=True)
def _tv_monitor_search_inline():
    """v184: monitoring searches run on a background worker in the app; under test they stay queued (no
    stray threads touching another test's tv.db). A test that wants them runs tv_search.drain() itself.
    No monkeypatch here (requesting it reorders other fixtures' teardown)."""
    import sys
    tv_search = sys.modules.get('app.tv_search')     # never imports the app for tests that don't use it
    if tv_search is None:
        yield
        return
    real = tv_search._spawn
    tv_search._spawn = lambda: None
    tv_search._ms_queue.clear()
    tv_search._ms_bucket[:] = [float(tv_search.MS_BURST), 0.0]
    tv_search._ms_clicks.clear()
    tv_search._ms_bg_next[:] = [0.0]
    try:
        yield
    finally:
        tv_search._spawn = real
        tv_search._ms_queue.clear()


_TV_THREADS = ('tv-calendar-refresh', 'tv-scan', 'tv-rescan')


@pytest.fixture(autouse=True)
def _tv_background_isolated():
    """v225: TV work a test starts in the background must not outlive it. A Calendar-open
    refresh or scan thread left running wrote into the next test's tv.db (v158b search on add,
    test_calendar_open_refreshes_stale_air_dates: failing only in the full run)."""
    import sys
    import threading
    yield
    scan = sys.modules.get('app.tv_scan')
    if scan is not None:
        scan.stop_scheduler()
        for t in [t for t in threading.enumerate() if t.name in _TV_THREADS and t.is_alive()]:
            t.join(timeout=10)
        scan._refresh_lock = threading.Lock()
        scan._priority_lock = threading.Lock()
        scan._sched_stop = threading.Event()
        scan._sched_thread = None
    tmdb = sys.modules.get('app.tv_tmdb')
    if tmdb is not None:
        tmdb._local = threading.local()
