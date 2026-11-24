"""Settings > Trackers in the web-kiosk-home build: the SPA routes and the
tracker API stay on in minimal mode, signed in, and the API stays owner-only."""

import pytest

from tests.test_home_rails import REMOTE, _client  # noqa: F401

PAGES = ['/settings/trackers', '/settings/trackers/new', '/settings/trackers/12']
API = [('/api/trackers', 'GET'), ('/api/trackers', 'POST'), ('/api/trackers/types', 'GET'),
       ('/api/trackers/12', 'GET'), ('/api/trackers/12', 'PUT'), ('/api/trackers/12', 'DELETE'),
       ('/api/trackers/12/enabled', 'PUT'), ('/api/trackers/12/test', 'POST'), ('/api/trackers/test', 'POST')]


@pytest.fixture
def minimal_on(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><head></head><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))


@pytest.mark.parametrize('path', PAGES)
def test_tracker_pages_allowed_in_minimal(path):
    from app import minimal
    assert minimal.path_allowed(path, 'GET')
    assert path not in minimal.SIGNED_OUT_PATHS


@pytest.mark.parametrize('path,method', API)
def test_tracker_api_allowed_in_minimal(path, method):
    from app import minimal
    assert minimal.path_allowed(path, method)
    assert path not in minimal.SIGNED_OUT_PATHS
    assert not path.startswith(minimal.SIGNED_OUT_PREFIXES)


@pytest.mark.parametrize('path', PAGES)
def test_tracker_pages_reload_into_the_spa(test_app, minimal_on, path):
    c = _client(test_app)
    assert c.get(path, environ_base=REMOTE).status_code == 200
    assert test_app.test_client().get(path, environ_base=REMOTE).status_code in (401, 302)


@pytest.mark.parametrize('path,method', API)
def test_tracker_api_needs_sign_in_in_minimal(test_app, minimal_on, path, method):
    r = test_app.test_client().open(path, method=method, json={}, environ_base=REMOTE)
    assert r.status_code in (401, 302)
    assert b'********' not in r.data
