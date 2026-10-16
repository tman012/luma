"""Pytest configuration for Luma tests."""

import os
import tempfile
import pytest


@pytest.fixture(scope="session", autouse=True)
def set_test_mode():
    """Set LUMA_TEST_MODE environment variable for all tests."""
    os.environ['LUMA_TEST_MODE'] = '1'
    yield
    os.environ.pop('LUMA_TEST_MODE', None)


@pytest.fixture(autouse=True)
def temp_data_dir(tmp_path, monkeypatch):
    """Provide a temporary data directory for each test."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('LUMA_DATA_DIR', str(data_dir))
    yield data_dir
