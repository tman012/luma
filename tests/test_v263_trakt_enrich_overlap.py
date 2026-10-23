"""v263: a Trakt rail's OMDb (IMDb rating) lookups run beside its TMDb poster lookups instead of after them,
so a cold anticipated rail costs Trakt + max(TMDb, OMDb), not Trakt + TMDb + OMDb. Trakt, TMDb, OMDb mocked."""
import threading
import time

import pytest

import app.main
from app import imdb_ratings, home_rails
from tests.test_minimal_trakt import REMOTE, _client, _connect, minimal, _key_tests_via_requests_get  # noqa: F401


class R:
    def __init__(self, code, body):
        self.status_code, self._body, self.headers, self.ok = code, body, {}, code < 400

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


@pytest.fixture
def services(minimal, monkeypatch, _key_tests_via_requests_get):  # noqa: F811
    st = {'tmdb_live': 0, 'omdb_during_tmdb': 0, 'lat': {'trakt': 0.05, 'tmdb': 0.3, 'omdb': 0.3}}
    lk = threading.Lock()

    def get(url, params=None, headers=None, **kw):
        if 'api.trakt.tv' in url:
            if str((params or {}).get('limit')) == '1':
                return R(200, [])
            time.sleep(st['lat']['trakt'])
            kind = 'movie' if '/movies/' in url else 'show'
            return R(200, [{kind: {'title': f'{kind} {i}', 'year': 2027,
                                   'ids': {'tmdb': 700 + i, 'trakt': i, 'imdb': f'tt{700 + i}'}}} for i in range(20)])
        if 'themoviedb' in url:
            with lk:
                st['tmdb_live'] += 1
            time.sleep(st['lat']['tmdb'])
            with lk:
                st['tmdb_live'] -= 1
            return R(200, {'poster_path': '/p.jpg', 'backdrop_path': '/b.jpg'})
        if 'omdbapi' in url:
            with lk:
                if st['tmdb_live']:
                    st['omdb_during_tmdb'] += 1
            time.sleep(st['lat']['omdb'])
            return R(200, {'Response': 'True', 'imdbRating': '7.4', 'imdbVotes': '2,000', 'imdbID': (params or {}).get('i')})
        return R(404, {})
    monkeypatch.setattr(app.main.requests, 'get', get)
    monkeypatch.setattr(imdb_ratings.requests, 'get', get)
    c = _client(minimal)
    _connect(c)
    apis = app.main.settings.get_section('apis')
    app.main.settings.update({'apis': {**apis, 'tmdb': {'api_key': 'tmdb-test', 'connector_saved': True},
                                       'omdb': {'api_key': 'omdb-test', 'connector_saved': True}}})
    app.main._trakt_cache.clear()
    imdb_ratings._CACHE.clear()
    home_rails.clear_trakt_last()
    st['client'] = c
    return st


@pytest.mark.parametrize('rail', ['trakt_anticipated_shows', 'trakt_anticipated_movies', 'trakt_movies', 'trakt_shows'])
def test_omdb_runs_beside_tmdb(services, rail):
    t = time.monotonic()
    items = app.main._fetch_trakt_rail(rail)
    took = time.monotonic() - t
    assert services['omdb_during_tmdb'] > 0                         # OMDb started while TMDb was in flight
    assert took < 0.05 + 3 * 0.3 + 3 * 0.3 - 0.4                     # well under Trakt + TMDb + OMDb in series
    assert all(i['poster_path'] == '/p.jpg' and i['rating'] == 7.4 for i in items)
