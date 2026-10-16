"""Pytest configuration for Luma tests."""

import os
import pytest


@pytest.fixture(scope="session", autouse=True)
def set_test_mode():
    """Set LUMA_TEST_MODE environment variable for all tests."""
    os.environ['LUMA_TEST_MODE'] = '1'
    yield
    os.environ.pop('LUMA_TEST_MODE', None)
