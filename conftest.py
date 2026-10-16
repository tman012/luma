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
    import app.user_auth
    app.user_auth.init_db()
    yield
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_shared_state(request):
    """Reset shared state between tests to ensure test isolation.
    
    Only applies to pytest-based tests (not unittest.TestCase tests).
    """
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
    
    # Clear user database between tests
    try:
        import app.user_auth as user_auth
        conn = user_auth._get_db()
        conn.execute('DELETE FROM users')
        conn.execute('DELETE FROM user_sessions')
        conn.commit()
        conn.close()
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
    
    # Initialize database schema
    app.user_auth.init_db()
    
    # Clear any existing users from previous tests
    conn = app.user_auth._get_db()
    conn.execute('DELETE FROM users')
    conn.execute('DELETE FROM user_sessions')
    conn.commit()
    conn.close()
    
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
