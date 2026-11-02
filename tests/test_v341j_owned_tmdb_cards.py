"""v341j: an owned title on a TMDb-keyed Home card (Trakt etc.) or opened by its TMDb id is owned from the server's
answer: /api/home marks the card (rating_key, art_path) and /api/movie|tv/<tmdb> carry plex_rating_key and
plex_art_path, from the in-memory library index (no Plex call). Never a token or a full URL."""
import json

import pytest

from app import home_rails
from app.fast_search import FastSearchIndex

TOK = 'X-Plex-Token'


def _index(items):
    ix = FastSearchIndex()
    ix._index = items
    return ix


def test_find_by_tmdb_matches_kind_and_id_first_wins():
    ix = _index([
        {'rating_key': '39580', 'media_type': 'show', 'tmdb_id': 153312, 'art': '/library/metadata/39580/art/1699'},
        {'rating_key': '40000', 'media_type': 'show', 'tmdb_id': 153312, 'art': ''},
        {'rating_key': '92145', 'media_type': 'movie', 'tmdb_id': 10061, 'art': ''},
    ])
    assert ix.find_by_tmdb(153312, 'show') == {'rating_key': 39580, 'art': '/library/metadata/39580/art/1699'}
    assert ix.find_by_tmdb('153312', 'movie') is None          # a movie with that id is another title
    assert ix.find_by_tmdb(10061, 'movie') == {'rating_key': 92145, 'art': ''}
    assert ix.find_by_tmdb(None, 'show') is None and ix.find_by_tmdb('x', 'show') is None
    ix._index = []                                              # a rebuilt index is read fresh
    assert ix.find_by_tmdb(153312, 'show') is None


@pytest.fixture
def lookup(monkeypatch):
    lib = {(153312, 'show'): {'rating_key': 39580, 'art': '/library/metadata/39580/art/1699'},
           (92145, 'movie'): {'rating_key': 92145, 'art': ''},
           (7, 'movie'): {'rating_key': 7, 'art': f'/library/metadata/7/art?{TOK}=abc'}}
    monkeypatch.setitem(home_rails._cfg, 'owned_lookup', lambda tid, kind: lib.get((tid, kind)))


def test_owned_tmdb_card_gets_plex_key_and_art(lookup):
    card = {'key': 'tmdb-show-153312', 'type': 'show', 'title': 'Tulsa King', 'subtitle': '2022', 'tmdb_id': 153312}
    out = home_rails.owned_card(card)
    assert out == {**card, 'rating_key': 39580, 'owned': True, 'art_path': '/library/metadata/39580/art/1699'}
    assert 'rating_key' not in card                              # the saved card is untouched
    # No art in the index: the plain library art path (the proxy's pattern).
    m = home_rails.owned_card({'key': 'tmdb-movie-92145', 'type': 'movie', 'tmdb_id': 92145})
    assert m['art_path'] == '/library/metadata/92145/art'
    # Anything off the proxy pattern (a token, a query) is never passed on.
    t = home_rails.owned_card({'key': 'tmdb-movie-7', 'type': 'movie', 'tmdb_id': 7})
    assert t['art_path'] == '/library/metadata/7/art' and TOK not in json.dumps(t)


def test_unowned_plex_and_failing_lookups_are_unchanged(lookup, monkeypatch):
    un = {'key': 'tmdb-show-1', 'type': 'show', 'tmdb_id': 1}
    assert home_rails.owned_card(un) is un
    px = {'key': '5003', 'type': 'movie', 'tmdb_id': 92145}       # a Plex card already has its key
    assert home_rails.owned_card(px) is px
    def boom(*_a):
        raise RuntimeError('index')
    monkeypatch.setitem(home_rails._cfg, 'owned_lookup', boom)
    c = {'key': 'tmdb-show-153312', 'type': 'show', 'tmdb_id': 153312}
    assert home_rails.owned_card(c) is c


def test_home_serves_cards_through_owned_card():
    import inspect
    src = inspect.getsource(home_rails)
    assert 'items = [owned_card(c) for c in items]' in src


def test_detail_answer_carries_plex_key_and_art(test_app, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.fast_search_index, 'find_by_tmdb',
                        lambda tid, kind: {'rating_key': 39580, 'art': '/library/metadata/39580/art/1699'} if (tid, kind) == (153312, 'show') else None)
    d = {'id': 153312, 'name': 'Tulsa King'}
    out = m._with_plex_owned(d, 'show', 153312)
    assert out['plex_rating_key'] == 39580 and out['plex_art_path'] == '/library/metadata/39580/art/1699'
    assert 'plex_rating_key' not in d                             # the cached TMDb answer is untouched
    assert m._with_plex_owned(d, 'movie', 153312) is d            # not owned: unchanged
    monkeypatch.setattr(m.fast_search_index, 'find_by_tmdb', lambda *_a: {'rating_key': 5, 'art': f'http://plex/x?{TOK}=1'})
    assert m._with_plex_owned(d, 'show', 1)['plex_art_path'] == '/library/metadata/5/art'


def test_detail_routes_use_it():
    import inspect
    import app.main as m
    assert "_with_plex_owned(data, 'movie', tmdb_id)" in inspect.getsource(m.movie_details)
    assert "_with_plex_owned(details, 'show', tmdb_id)" in inspect.getsource(m.tv_details)
