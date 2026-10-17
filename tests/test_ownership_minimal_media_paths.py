"""Minimal build: /api/check-ownership-batch ignores legacy settings media.paths
(use_media_paths from MobileSearchOverlay). The settings data is untouched."""
import copy
import json

import pytest

import app.main
from tests.test_home_rails import REMOTE, _client

ENTRY = {'rating_key': '42', 'title': 'Inception', 'year': 2010, 'library_name': 'Films 4K', 'rating': 8.8}
SHOW = {'rating_key': '43', 'title': 'Severance', 'year': 2022, 'library_name': 'Series'}
LEGACY = [{'name': 'Movies', 'path': '/old/movies'}, {'name': 'TV Shows', 'path': '/old/tv'}]
BODY = {'movies': [{'title': 'Inception', 'year': 2010, 'tmdb_id': 27205}],
        'tv': [{'title': 'Severance', 'year': 2022, 'tmdb_id': 95396}], 'use_media_paths': True}


class _Plex:
    def build_ownership_index(self):
        return {'movies': {'inception|2010': ENTRY}, 'movies_by_title': {'inception': [ENTRY]},
                'movies_by_tmdb': {27205: ENTRY}, 'tv': {'severance|2022': SHOW},
                'tv_by_title': {'severance': [SHOW]}, 'tv_by_tmdb': {95396: SHOW}, 'tv_by_tvdb': {}}


@pytest.fixture
def legacy(test_app, monkeypatch):
    monkeypatch.setattr(app.main, 'get_plex_for_request', lambda *a, **k: _Plex())
    real_get = app.main.settings.get
    legacy_paths = copy.deepcopy(LEGACY)
    monkeypatch.setattr(app.main.settings, 'get',
                        lambda key, *a, **k: legacy_paths if key == 'media.paths' else real_get(key, *a, **k))
    test_app.legacy_paths = legacy_paths
    return test_app


def _ask(test_app):
    c = _client(test_app)
    return c.post('/api/check-ownership-batch', json=BODY, environ_base=REMOTE,
                  headers={'Origin': 'http://localhost', 'Host': 'localhost'})


def test_minimal_ignores_legacy_media_paths(legacy, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    before = json.dumps(app.main.settings.get('media.paths'), sort_keys=True)
    r = _ask(legacy)
    assert r.status_code == 200, r.get_data()
    res = r.get_json()['results']
    assert res['movie:inception_2010']['owned'] is True
    assert res['movie:inception_2010']['library_name'] == 'Films 4K'
    assert res['tv:severance_2022']['owned'] is True
    # settings.json data is neither modified nor deleted
    assert json.dumps(app.main.settings.get('media.paths'), sort_keys=True) == before
    assert legacy.legacy_paths == LEGACY


def test_full_build_still_filters_by_media_paths(legacy, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    r = _ask(legacy)
    assert r.status_code == 200, r.get_data()
    res = r.get_json()['results']
    assert res['movie:inception_2010'] == {'owned': False}
    assert res['tv:severance_2022'] == {'owned': False}
