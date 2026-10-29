"""v324c: luma-test-tagged movie grabs (Genre Roulette) import, mark movie_grabs imported, push once;
legacy 'movie'-tagged grabs still import; library_mover and movie_import never double mark or double push."""
from __future__ import annotations

import pytest

from app import library_mover as lm, library_notify, movie_grabs, movie_import as mi, movie_settings, movie_scanner as sc
from tests.test_v204_movie_import import H, REL, env, torrent  # noqa: F401
from tests.test_v283_movie_import_notify import _clean_notify, captured  # noqa: F401


@pytest.fixture
def gr(env, monkeypatch):
    """A Genre Roulette movie: not on the watchlist, title from gr_store."""
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    with movie_settings.connect() as conn:
        conn.execute('DELETE FROM watchlist')
    from app.automation import gr_store
    monkeypatch.setattr(gr_store, 'title_for',
                        lambda tid: {'title': 'Max Payne', 'year': 2008} if tid == 13051 else None)
    return env


def test_gr_grab_tagged_luma_test_imports_marks_and_pushes_once(gr, captured):
    pushes, _ = captured
    dl, lib = gr
    assert sc.category() == 'luma-test'
    movie_grabs.record(13051, REL, H)
    t = torrent(dl, category='luma-test', tags='luma-test')
    assert mi.tick(now=100, fetch=lambda: [t], path_map=lambda p: p) == {'checked': 1, 'imported': 1}
    g = movie_grabs.by_hash(H)
    assert g['state'] == 'imported' and g['path'].startswith(str(lib) + '/')
    assert (lib / 'Max Payne (2008)').is_dir()
    assert len(pushes) == 1 and pushes[0]['title'] == 'Max Payne (2008) added!'
    # seen again (either importer): no second mark, no second push
    assert mi.tick(now=200, fetch=lambda: [t], path_map=lambda p: p)['imported'] == 0
    assert lm._claim_movie_grab(H, {'title': 'Max Payne'}, g['path'], 300) is False
    assert len(pushes) == 1


def test_luma_test_torrent_without_a_grab_row_is_left_to_library_mover(gr, captured):
    pushes, _ = captured
    dl, _lib = gr
    t = torrent(dl, category='luma-test', tags='luma-test')
    assert mi.tick(now=100, fetch=lambda: [t], path_map=lambda p: p) == {'checked': 0, 'imported': 0}
    assert pushes == []


def test_legacy_movie_tag_still_imports(env, captured):
    pushes, _ = captured
    dl, _lib = env
    movie_grabs.record(693134, REL, H)
    assert mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)['imported'] == 1
    assert movie_grabs.by_hash(H)['state'] == 'imported' and len(pushes) == 1


def test_mover_owned_hash_is_skipped_by_movie_import(gr, captured, monkeypatch):
    pushes, _ = captured
    dl, _lib = gr
    movie_grabs.record(13051, REL, H)
    from app import file_management as fm
    monkeypatch.setattr(fm, 'load', lambda: {'jobs': {H: {'state': 'waiting'}}, 'paths': {}, 'path_map': []})
    t = torrent(dl, category='luma-test', tags='luma-test')
    assert mi.tick(now=100, fetch=lambda: [t], path_map=lambda p: p)['imported'] == 0
    assert movie_grabs.by_hash(H)['state'] == 'grabbed' and pushes == []


def test_library_mover_marks_row_once_and_drops_watchlist(env):
    movie_grabs.record(693134, REL, H)
    assert lm._claim_movie_grab(H, {'title': 'Dune'}, '/m/Dune.mkv', 10) is True
    g = movie_grabs.by_hash(H)
    assert g['state'] == 'imported' and g['path'] == '/m/Dune.mkv'
    with movie_settings.connect() as conn:
        assert conn.execute('SELECT 1 FROM watchlist WHERE tmdb_id=693134').fetchone() is None
    assert lm._claim_movie_grab(H, {'title': 'Dune'}, '/m/Dune.mkv', 11) is False
    assert lm._claim_movie_grab('b' * 40, {'title': 'x'}, '/m/x.mkv', 12) is True   # no row: normal notify


def test_mark_imported_is_atomic_once(env):
    movie_grabs.record(693134, REL, H)
    assert movie_grabs.mark_imported(H, '/p', 1) is True
    assert movie_grabs.mark_imported(H, '/p', 2) is False
    assert movie_grabs.mark_imported('c' * 40, '/p', 3) is False
