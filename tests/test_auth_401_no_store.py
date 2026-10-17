"""A 401 from the sign-in guard is never kept by any cache."""

import app.main


def test_auth_guard_401_is_no_store(test_app, monkeypatch):
    monkeypatch.setattr(app.main._access, 'should_authenticate', lambda *a, **k: True)
    r = test_app.test_client().get('/api/library/recent', environ_base={'REMOTE_ADDR': '203.0.113.7'})
    assert r.status_code == 401
    assert 'no-store' in r.headers['Cache-Control']
