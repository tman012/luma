"""Pytest configuration for Luma tests."""

import os
import tempfile
import pytest
from pathlib import Path
import shutil


# Paths are set at import time, before any test module imports app.main or
# app.hero_cache (both read them at import), so a bare `pytest` never touches
# the container-only /app paths.
_REPO_ROOT = Path(__file__).resolve().parent
_TEMP_CACHE = tempfile.mkdtemp(prefix='luma_test_cache_')
_TEMP_DATA = tempfile.mkdtemp(prefix='luma_test_data_')
_TEST_ENV = {
    'LUMA_CACHE_DIR': _TEMP_CACHE,
    'HERO_CACHE_DIR': os.path.join(_TEMP_CACHE, 'hero'),
    'DATABASE_PATH': os.path.join(_TEMP_DATA, 'media.db'),
    'SETTINGS_PATH': os.path.join(_TEMP_DATA, 'settings.json'),
    'LUMA_SKIP_BACKGROUND_THREADS': '1',
    'BASE_DIR': _TEMP_DATA,
}
os.environ.update(_TEST_ENV)
os.environ.setdefault('FLASK_STATIC_FOLDER', str(_REPO_ROOT / 'static'))


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Remove the temp directories when the session ends."""
    yield
    shutil.rmtree(_TEMP_CACHE, ignore_errors=True)
    shutil.rmtree(_TEMP_DATA, ignore_errors=True)


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
