"""v204 movie piece 4: completed movie downloads matched through movie_grabs, copied (never moved) into the
Movies root under Radarr's naming, verified, then the grab is imported and the movie leaves the watchlist."""
import os

import pytest

from app import instance, movie_grabs, movie_import as mi, movie_settings
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req

H = 'a' * 40
REL = 'Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.H.265-FLUX'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(movie_settings.SEED_FILE))
    monkeypatch.setattr(instance, 'is_test', lambda: True)
    dl, lib = tmp_path / 'downloads', tmp_path / 'Movies'
    (dl / REL).mkdir(parents=True)
    lib.mkdir()
    (dl / REL / f'{REL}.mkv').write_bytes(b'x' * 4096)
    (dl / REL / 'sample.mkv').write_bytes(b's' * 10)
    with movie_settings.connect() as conn:
        conn.execute("INSERT INTO watchlist (tmdb_id, title, year, added_at) VALUES (693134, 'Dune: Part Two', 2024, 1)")
    assert mi.save_settings({'root': str(lib)}) == {}
    return dl, lib


def torrent(dl, **k):
    return {'hash': H, 'name': REL, 'category': 'movie', 'tags': '', 'progress': 1, 'amount_left': 0,
            'content_path': str(dl / REL), **k}


def test_grabs_helper_api(env):
    g = movie_grabs.record(693134, REL, H.upper(), now=10)
    assert g['hash'] == H and g['state'] == 'grabbed' and g['grabbed_at'] == 10
    assert movie_grabs.record(693134, 'other', H)['release_title'] == REL       # same hash: unchanged
    assert movie_grabs.by_hash(H)['tmdb_id'] == 693134 and movie_grabs.by_hash('zz') is None
    assert [r['hash'] for r in movie_grabs.for_movie(693134)] == [H] and len(movie_grabs.pending()) == 1
    assert movie_grabs.set_state(H, 'failed', error='x') and movie_grabs.by_hash(H)['error'] == 'x'
    for bad in ((0, REL, H), (1, '', H), (1, REL, 'nothex')):
        with pytest.raises(ValueError):
            movie_grabs.record(*bad)


def test_import_copies_renames_verifies_then_imports_and_unwatchlists(env):
    dl, lib = env
    movie_grabs.record(693134, REL, H)
    out = mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)
    assert out == {'checked': 1, 'imported': 1}
    dest = lib / 'Dune- Part Two (2024)' / 'Dune- Part Two (2024).mkv'          # v203 (PM): Luma's naming
    assert dest.read_bytes() == b'x' * 4096
    assert (dl / REL / f'{REL}.mkv').exists()                                     # copied, never moved
    assert not list(lib.rglob('*' + mi.PART))
    g = movie_grabs.by_hash(H)
    assert g['state'] == 'imported' and g['path'] == str(dest)
    with movie_settings.connect() as conn:
        assert conn.execute('SELECT 1 FROM watchlist WHERE tmdb_id = 693134').fetchone() is None
    assert mi.tick(now=200, fetch=lambda: [torrent(dl)], path_map=lambda p: p) == {'checked': 0, 'imported': 0}


def test_never_overwrites_a_different_file_and_keeps_the_grab_unimported(env):
    dl, lib = env
    movie_grabs.record(693134, REL, H)
    d = lib / 'Dune - Part Two (2024)'
    d.mkdir()
    (d / 'Dune- Part Two (2024).mkv').write_bytes(b'mine')       # v203: the existing folder is reused as spelled
    mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)
    assert (d / 'Dune- Part Two (2024).mkv').read_bytes() == b'mine'
    assert sorted(p.name for p in lib.iterdir()) == ['Dune - Part Two (2024)']
    g = movie_grabs.by_hash(H)
    assert g['state'] == 'failed' and 'already there' in g['error']
    with movie_settings.connect() as conn:
        assert conn.execute('SELECT 1 FROM watchlist WHERE tmdb_id = 693134').fetchone()   # still wanted


def test_a_failed_verify_leaves_nothing_and_does_not_import(env, monkeypatch):
    dl, lib = env
    movie_grabs.record(693134, REL, H)
    real = mi._sha256
    calls = {'n': 0}

    def bad(p):
        calls['n'] += 1
        return 'nope' if p.endswith(mi.PART) else real(p)
    monkeypatch.setattr(mi, '_sha256', bad)
    mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)
    assert movie_grabs.by_hash(H)['state'] == 'grabbed' and 'file error' in movie_grabs.by_hash(H)['error']
    assert not [p for p in lib.rglob('*') if p.is_file()]


def test_only_movie_category_or_tag_completed_and_grabbed(env):
    dl, lib = env
    movie_grabs.record(693134, REL, H)
    for t in (torrent(dl, category='tv'), torrent(dl, progress=0.5), torrent(dl, hash='b' * 40)):
        assert mi.tick(now=100, fetch=lambda t=t: [t], path_map=lambda p: p)['imported'] == 0
    assert mi.tick(now=100, fetch=lambda: [torrent(dl, category='', tags='x, movie')], path_map=lambda p: p)['imported'] == 1


def test_off_unless_test(env, monkeypatch):
    dl, lib = env
    movie_grabs.record(693134, REL, H)
    monkeypatch.setattr(instance, 'is_test', lambda: False)
    assert mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p) == {'checked': 0, 'imported': 0}


def test_naming_defaults_to_radarr_and_saved_naming_wins(env):
    assert mi.render(mi.DEFAULT_NAMING['standard'], 'Mission: Impossible', 1996, 'M.I.1996.1080p.BluRay.x264-GRP') == \
        'Mission- Impossible (1996)'
    assert mi.render(mi.DEFAULT_NAMING['folder'], 'Alien', None, '') == 'Alien'
    assert mi.save_settings({'naming': {'standard': '{Movie Title} {Quality Title}'}}) == {}
    assert mi.naming()['standard'] == '{Movie Title} {Quality Title}' and mi.naming()['folder'] == mi.DEFAULT_NAMING['folder']
    assert mi.save_settings({'naming': {'standard': 'a/b'}}) == {'naming': 'Invalid'}
    assert 'root' in mi.save_settings({'root': 'relative'})


def test_routes_test_only_and_owner_for_changes(env, owner, monkeypatch):  # noqa: F811
    dl, lib = env
    r = _req(owner, 'get', '/api/settings/movies/import')
    assert r.status_code == 200 and r.get_json()['root'] == str(lib)
    assert _req(owner, 'put', '/api/settings/movies/import', {'root': 'x'}).status_code == 400
    assert _req(owner, 'post', '/api/settings/movies/import/run').status_code == 200
    from flask import jsonify
    from app import tv_settings
    tv_settings._cfg['signed_in_owner'] = lambda: (None, (jsonify({'error': 'Sign in'}), 401))
    try:
        assert _req(owner, 'put', '/api/settings/movies/import', {'root': str(lib)}).status_code == 401
        assert _req(owner, 'post', '/api/settings/movies/import/run').status_code == 401
    finally:
        tv_settings._cfg.pop('signed_in_owner', None)
    monkeypatch.setattr(instance, 'is_test', lambda: False)
    assert _req(owner, 'get', '/api/settings/movies/import').status_code == 404


def test_v203_luma_naming_rename_off_keeps_the_release_name_and_existing_folders_are_reused(env):
    dl, lib = env
    assert mi.DEFAULT_NAMING['rename'] is True and mi.naming()['colon_replacement'] == 'dash'
    assert mi.save_settings({'naming': {'rename': 'yes'}}) == {'naming': 'Invalid'}
    assert mi.save_settings({'naming': {'rename': False}}) == {} and mi.naming()['rename'] is False
    (lib / 'dune part two (2024)').mkdir()
    movie_grabs.record(693134, REL, H)
    assert mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)['imported'] == 1
    assert (lib / 'dune part two (2024)' / f'{REL}.mkv').exists()
    assert sorted(p.name for p in lib.iterdir()) == ['dune part two (2024)']


def test_v203_library_path_never_falls_back_to_lumas_movies_path(env, monkeypatch):
    from app import settings_manager
    with movie_settings.connect() as conn:
        conn.execute("DELETE FROM meta WHERE key = 'library_root'")
    monkeypatch.setattr(settings_manager.SettingsManager, 'get', lambda self, k, d=None:
                        [{'type': 'tv', 'path': '/tv'}, {'type': 'movies', 'path': '/media/Movies'}] if k == 'media.paths' else d)
    assert mi.root() == ''
    assert mi.tick(now=100, fetch=lambda: [], path_map=lambda p: p)['imported'] == 0
