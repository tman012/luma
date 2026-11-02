"""v341k: an owned library search row carries its TMDb id and its Plex art path (proxy pattern only, never a token),
so it opens like its Home card: both ids, the Plex art from the tap."""
import inspect

from app.fast_search import FastSearchIndex


def test_clean_keeps_art_and_tmdb_id():
    ix = FastSearchIndex()
    out = ix._clean_results([{'rating_key': '92145', 'title': 'Escape from L.A.', 'media_type': 'movie',
                              'art': '/library/metadata/92145/art/1793430969', 'tmdb_id': 10061}]) \
        if hasattr(ix, '_clean_results') else None
    if out is None:   # the stripping helper's name: find it by its docstring
        fn = next(f for n, f in inspect.getmembers(FastSearchIndex, inspect.isfunction)
                  if (f.__doc__ or '').startswith('Strip internal search helper fields'))
        out = fn(ix, [{'rating_key': '92145', 'title': 'Escape from L.A.', 'media_type': 'movie',
                       'art': '/library/metadata/92145/art/1793430969', 'tmdb_id': 10061}])
    assert out[0]['art'] == '/library/metadata/92145/art/1793430969' and out[0]['tmdb_id'] == 10061


def test_library_search_emits_art_path_on_the_proxy_pattern_only():
    import app.main as m
    src = inspect.getsource(m.library_search)
    assert "if _PLEX_ART_PATH_RE.fullmatch(_art):" in src and "formatted['art_path'] = _art" in src
    assert m._PLEX_ART_PATH_RE.fullmatch('/library/metadata/92145/art/1793430969')
    assert not m._PLEX_ART_PATH_RE.fullmatch('/library/metadata/1/art?X-Plex-Token=a')
