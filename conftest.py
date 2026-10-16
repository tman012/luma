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


@pytest.fixture
def test_app():
    """Create a real Flask app from app.main for integration tests."""
    # Ensure test environment is set up
    import app.main
    import app.user_auth
    
    app = app.main.app
    app.config['TESTING'] = True
    
    # Initialize database schema
    app.user_auth.init_db()
    
    return app
