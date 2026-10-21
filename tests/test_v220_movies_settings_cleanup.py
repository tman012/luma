"""v220 Settings > Movies cleanup: the Import card is gone from the UI, but the backend still reads and writes the same
movies.db keys (library_root, naming{rename, folder, standard}); a Naming-only PUT leaves library_root and the stored
rename flag exactly as they were, and import routing is unchanged."""
from app import movie_import as mi, movie_settings
from tests.test_v204_movie_import import env, torrent, H, REL  # noqa: F401


def _meta(k):
    with movie_settings.connect() as conn:
        return movie_settings._meta(conn, k, None)


def test_naming_put_keeps_root_and_rename(env):
    dl, lib = env
    assert mi.save_settings({'naming': {'rename': False, 'folder': '{Movie Title}', 'standard': '{Movie Title} x'}}) == {}
    root_before = _meta('library_root')
    assert mi.save_settings({'naming': {'rename': False, 'folder': '{Movie Title} ({Release Year})', 'standard': '{Movie Title} y'}}) == {}
    assert _meta('library_root') == root_before == str(lib)
    assert _meta('naming') == {'rename': False, 'folder': '{Movie Title} ({Release Year})', 'standard': '{Movie Title} y'}
    assert mi.naming()['rename'] is False and mi.root() == str(lib)


def test_import_routing_unchanged(env):
    dl, lib = env
    from app import movie_grabs
    movie_grabs.record(693134, REL, H)
    assert mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p) == {'checked': 1, 'imported': 1}
    assert (lib / 'Dune- Part Two (2024)' / 'Dune- Part Two (2024).mkv').exists()

