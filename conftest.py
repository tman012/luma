"""Pytest configuration for Luma tests."""

import os
import tempfile
import pytest
from pathlib import Path
import shutil


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment with temp directories and test mode flags."""
    # Create temp directories
    temp_cache = tempfile.mkdtemp(prefix='luma_test_cache_')
    temp_data = tempfile.mkdtemp(prefix='luma_test_data_')
    
    # Set environment variables
    os.environ['LUMA_CACHE_DIR'] = temp_cache
    os.environ['DATABASE_PATH'] = os.path.join(temp_data, 'media.db')
    os.environ['SETTINGS_PATH'] = os.path.join(temp_data, 'settings.json')
    os.environ['LUMA_SKIP_BACKGROUND_THREADS'] = '1'
    os.environ['BASE_DIR'] = temp_data
    
    yield
    
    # Cleanup
    try:
        shutil.rmtree(temp_cache)
        shutil.rmtree(temp_data)
    except Exception:
        pass
    
    for key in ['LUMA_CACHE_DIR', 'DATABASE_PATH', 'SETTINGS_PATH', 'LUMA_SKIP_BACKGROUND_THREADS', 'BASE_DIR']:
        os.environ.pop(key, None)


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
    
    # Reset the sign-in rate limiter
    from app import login_limiter
    login_limiter._fail.clear()
    
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
        }
    })
    flask_app.config['URL_BASE'] = ''
    flask_app.config['START_BACKGROUND_THREADS'] = False
    
    return flask_app
