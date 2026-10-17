"""Library search results carry tmdb_id (movies and shows) so the modal can start
/api/movie or /api/tv alongside /api/plex/*, and the TMDb type check cache is bounded."""

import os
import tempfile

os.environ.setdefault('LUMA_CACHE_DIR', tempfile.mkdtemp(prefix='luma_test_cache_'))

from unittest.mock import Mock, patch

import pytest

from tests.local_access import local_client
from app import main as app_main
from app.fast_search import FastSearchIndex, tmdb_id_from_guids


@pytest.fixture
def client():
    app_main.app.config['TESTING'] = True
    return local_client(app_main.app)


@pytest.fixture(autouse=True)
def clean_type_cache():
    app_main._tmdb_type_validation_cache.clear()
    yield
    app_main._tmdb_type_validation_cache.clear()


def _index(items):
    idx = Mock()
    idx.status.return_value = {'initialized': True}
    idx.search.return_value = items
    return idx


def _movie(**kw):
    base = {'title': 'Interstellar', 'year': 2014, 'rating_key': '7', 'media_type': 'movie',
            'section_name': 'Movies', 'thumb': '/t.jpg'}
    base.update(kw)
    return base


def test_guid_parsing():
    assert tmdb_id_from_guids([{'id': 'imdb://tt0816692'}, {'id': 'tmdb://157336'}]) == 157336
    assert tmdb_id_from_guids(None, 'com.plexapp.agents.themoviedb://157336?lang=en') == 157336
    assert tmdb_id_from_guids([{'id': 'tmdb://x'}, {'id': 'tvdb://1'}]) is None
    assert tmdb_id_from_guids([], None) is None


def test_index_requests_guids_and_stores_tmdb_id():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {'MediaContainer': {'totalSize': 1, 'Metadata': [{
        'ratingKey': 7, 'title': 'Interstellar', 'year': 2014,
        'Guid': [{'id': 'imdb://tt0816692'}, {'id': 'tmdb://157336'}]}]}}
    with patch('app.fast_search.requests.get', return_value=resp) as get:
        items = FastSearchIndex()._fetch_section('http://plex', 'tok', 1, 'Movies')
    assert get.call_args.kwargs['params']['includeGuids'] == 1
    assert items[0]['tmdb_id'] == 157336


def test_library_search_includes_movie_tmdb_id(client):
    items = [_movie(tmdb_id=157336),
             {'title': 'Breaking Bad', 'year': 2008, 'rating_key': '9', 'media_type': 'show',
              'section_name': 'TV', 'tmdb_id': 1396}]
    with patch.object(app_main, 'fast_search_index', _index(items)), \
            patch.object(app_main, 'requests') as net:
        data = client.get('/api/library/search?q=inter').get_json()
    assert data['movies'][0]['tmdb_id'] == 157336
    assert data['movies'][0]['rating_key'] == '7'
    assert data['tv_shows'][0]['tmdb_id'] == 1396
    # The search itself never calls TMDb.
    net.get.assert_not_called()


def test_library_search_drops_tv_id_known_to_be_wrong_type(client):
    # The id is a movie, not a show: leave it out, but keep it for the movie.
    app_main._tmdb_type_validation_cache['tv:157336'] = (app_main.time.time(), False)
    items = [_movie(tmdb_id=157336),
             {'title': 'Interstellar', 'year': 2014, 'rating_key': '9', 'media_type': 'show',
              'section_name': 'TV', 'tmdb_id': 157336}]
    with patch.object(app_main, 'fast_search_index', _index(items)):
        data = client.get('/api/library/search?q=inter').get_json()
    assert data['movies'][0]['tmdb_id'] == 157336
    assert 'tmdb_id' not in data['tv_shows'][0]


def test_library_search_drops_id_known_to_be_wrong_type(client):
    app_main._tmdb_type_validation_cache['movie:1396'] = (app_main.time.time(), False)
    with patch.object(app_main, 'fast_search_index', _index([_movie(tmdb_id=1396)])):
        data = client.get('/api/library/search?q=inter').get_json()
    assert 'tmdb_id' not in data['movies'][0]


def test_library_search_without_tmdb_id(client):
    with patch.object(app_main, 'fast_search_index', _index([_movie()])):
        data = client.get('/api/library/search?q=inter').get_json()
    assert 'tmdb_id' not in data['movies'][0]


def _ok(status):
    r = Mock()
    r.status_code = status
    r.ok = 200 <= status < 300
    return r


def test_type_check_cached_once(monkeypatch):
    monkeypatch.setattr(app_main.settings, 'get', lambda k, d=None: 'k' if k == 'apis.tmdb.api_key' else d)
    with patch.object(app_main.requests, 'get', return_value=_ok(200)) as get:
        assert app_main._validate_tmdb_id_type(157336, 'movie') is True
        assert app_main._validate_tmdb_id_type(157336, 'movie') is True
    assert get.call_count == 1
    assert app_main._cached_tmdb_type(157336, 'movie') is True
    assert app_main._cached_tmdb_type(157336, 'tv') is None


def test_type_check_transient_errors_not_cached(monkeypatch):
    monkeypatch.setattr(app_main.settings, 'get', lambda k, d=None: 'k' if k == 'apis.tmdb.api_key' else d)
    with patch.object(app_main.requests, 'get', return_value=_ok(503)) as get:
        assert app_main._validate_tmdb_id_type(5, 'movie') is None
        assert app_main._validate_tmdb_id_type(5, 'movie') is None
    assert get.call_count == 2


def test_type_check_ttl_expires(monkeypatch):
    monkeypatch.setattr(app_main.settings, 'get', lambda k, d=None: 'k' if k == 'apis.tmdb.api_key' else d)
    old = app_main.time.time() - app_main._TMDB_TYPE_VALIDATION_TTL - 1
    app_main._tmdb_type_validation_cache['movie:5'] = (old, False)
    assert app_main._cached_tmdb_type(5, 'movie') is None
    with patch.object(app_main.requests, 'get', return_value=_ok(200)) as get:
        assert app_main._validate_tmdb_id_type(5, 'movie') is True
    assert get.call_count == 1


def test_type_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(app_main, '_TMDB_TYPE_VALIDATION_MAX', 3)
    now = app_main.time.time()
    with app_main._tmdb_type_validation_lock:
        app_main._tmdb_type_validation_cache['movie:1'] = (now - app_main._TMDB_TYPE_VALIDATION_TTL - 5, True)
        for i in range(2, 7):
            app_main._store_tmdb_type_validation(f'movie:{i}', now + i, True)
    cache = app_main._tmdb_type_validation_cache
    assert len(cache) == 3
    assert list(cache) == ['movie:4', 'movie:5', 'movie:6']
