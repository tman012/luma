"""v267 Activity: Home ActivitySheet APIs stay reachable in minimal mode."""
from app import minimal


def test_activity_queue_and_history_allowed():
    assert minimal.path_allowed('/api/downloads/queue', 'GET')
    assert minimal.path_allowed('/api/downloads/history', 'GET')
    assert minimal.path_allowed('/api/downloads/history', 'HEAD')


def test_activity_remove_allowed_methods_only():
    path = '/api/downloads/remove/' + ('a' * 40)
    assert minimal.path_allowed(path, 'DELETE')
    assert minimal.path_allowed(path, 'POST')
    assert not minimal.path_allowed(path, 'GET')
    assert not minimal.path_allowed(path, 'PUT')


def test_activity_does_not_open_broad_downloads_prefix():
    # Only queue/history (exact) and remove/<hash> — not arbitrary /api/downloads/*
    assert not minimal.path_allowed('/api/downloads/secret', 'GET')
    assert not minimal.path_allowed('/api/downloads/', 'GET')
