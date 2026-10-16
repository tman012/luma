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
    
    # Clear user database between tests (sandbox only; UnsafeDatabaseReset
    # propagates and fails the test rather than touching another database)
    try:
        _wipe_users()
    except UnsafeDatabaseReset:
        raise
    except Exception:
        pass
    
    yield
    
    # Cleanup after test
    login_limiter._fail.clear()
    request_portal._fails.clear()
    request_portal._settings = saved_portal_settings


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
