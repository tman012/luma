"""Minimal builds (:5002) open the tracker store's routes (#16) and nothing next to them."""

from app import minimal


def test_tracker_paths_are_allowed():
    for path, method in [('/api/trackers', 'GET'), ('/api/trackers', 'POST'), ('/api/trackers/types', 'GET'),
                         ('/api/trackers/3', 'GET'), ('/api/trackers/3', 'PUT'), ('/api/trackers/3', 'DELETE'),
                         ('/api/trackers/3/enabled', 'PUT'), ('/api/trackers/3/test', 'POST'),
                         ('/api/trackers/test', 'POST'), ('/settings/trackers', 'GET')]:
        assert minimal.path_allowed(path, method), (path, method)


def test_neighbours_stay_closed():
    for path in ['/api/trackersx', '/api/tracker', '/api/indexer/grab', '/api/prowlarr/indexers']:
        assert not minimal.path_allowed(path, 'GET'), path


def test_tracker_routes_answer_in_minimal_mode(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    r = test_app.test_client().get('/api/trackers')
    assert r.status_code != 404
