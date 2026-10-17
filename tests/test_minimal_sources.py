"""Minimal builds (:5002) open the Prowlarr port's routes; nothing else."""

from app import minimal


def test_tracker_and_sources_paths_are_allowed():
    for path, method in [('/api/trackers', 'GET'), ('/api/trackers', 'POST'), ('/api/trackers/3', 'PUT'),
                         ('/api/trackers/3/test', 'POST'), ('/api/trackers/types', 'GET'),
                         ('/api/sources/all-results', 'GET'), ('/api/sources/config', 'GET'),
                         ('/api/downloads/add', 'POST'), ('/api/tmdb/match-search', 'GET'),
                         ('/settings/trackers', 'GET')]:
        assert minimal.path_allowed(path, method), (path, method)


def test_neighbours_stay_closed():
    for path in ['/api/downloads/remove/abc', '/api/downloads/active', '/api/indexer/grab',
                 '/api/radarr/movie/releases', '/api/sonarr/season/search', '/api/trackersx', '/api/sources/other']:
        assert not minimal.path_allowed(path, 'GET'), path
