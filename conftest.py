"""Pytest configuration for Luma tests."""

import os
import tempfile
import pytest
from pathlib import Path


@pytest.fixture(scope="session", autouse=True)
def setup_test_cache_dir():
    """Set LUMA_CACHE_DIR to temp directory for all tests."""
    temp_dir = tempfile.mkdtemp(prefix='luma_test_cache_')
    os.environ['LUMA_CACHE_DIR'] = temp_dir
    yield
    # Cleanup
    import shutil
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass
    os.environ.pop('LUMA_CACHE_DIR', None)
