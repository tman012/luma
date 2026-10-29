"""v324c: movie_import watches luma-test plus legacy 'movie' (row-gated, skips library_mover's hashes);
library_mover marks a movie_grabs row imported; once-only mark_imported so exactly one notify."""
from __future__ import annotations

import pytest

from app import library_mover as lm, library_notify, movie_grabs, movie_import as mi, movie_settings
from tests.test_v204_movie_import import H, REL, env, torrent  # noqa: F401


@pytest.fixture
def pushes(monkeypatch):
    out = []
    library_notify.clear_local()
    monkeypatch.setattr(library_notify, 'notify_library_added', lambda **kw: out.append(kw))
    monkeypatch.setattr(mi, '_mover_owns', lambda h: False)
    yield out
    library_notify.clear_local()


def _gr(env, monkeypatch):
    from app.automation import gr_store as gs
    monkeypatch.setitem(gs._cfg, 'db_path', str(env[0].parent / 'automation.db'))
    gs.record_grab(H, 693134, 'Dune: Part Two', 2024, None, 50.0)
    movie_grabs.record(693134, REL, H)
    with movie_settings.connect() as conn:
        conn.execute('DELETE FROM watchlist WHERE tmdb_id = 693134')


def test_watch_names(monkeypatch):
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    monkeypatch.delenv('LUMA_MOVIE_CATEGORY', raising=False)
    assert mi.watch_names() == ['luma-test', 'movie']


def test_gr_movie_end_to_end_luma_test_tag(env, monkeypatch, pushes):
    """GR grab tagged luma-test -> imported into the Movies folder, row imported, one push with the name."""
    _gr(env, monkeypatch)
    dl, lib = env
    out = mi.tick(now=100, fetch=lambda: [torrent(dl, category='luma-test', tags='luma-test')], path_map=lambda p: p)
    assert out == {'checked': 1, 'imported': 1}
    row = movie_grabs.by_hash(H)
    assert row['state'] == 'imported' and row['path'].startswith(str(lib.parent))
    assert len(pushes) == 1 and pushes[0]['title'] == 'Dune: Part Two' and pushes[0]['source'] == 'movie_import'
    # a second pass never re-imports or re-notifies
    assert mi.tick(now=200, fetch=lambda: [torrent(dl, category='luma-test', tags='luma-test')], path_map=lambda p: p)['imported'] == 0
    assert len(pushes) == 1


def test_legacy_movie_tag_still_imports(env, monkeypatch, pushes):
    _gr(env, monkeypatch)
    dl, _ = env
    assert mi.tick(now=100, fetch=lambda: [torrent(dl, category='', tags='movie')], path_map=lambda p: p)['imported'] == 1
    assert len(pushes) == 1


def test_untagged_or_rowless_never_imported(env, monkeypatch, pushes):
    dl, _ = env
    assert mi.tick(now=100, fetch=lambda: [torrent(dl, category='luma-test', tags='luma-test')], path_map=lambda p: p)['checked'] == 0
    _gr(env, monkeypatch)
    assert mi.tick(now=100, fetch=lambda: [torrent(dl, category='radarr', tags='')], path_map=lambda p: p)['checked'] == 0
    assert pushes == []


def test_mover_owned_hash_skipped(env, monkeypatch, pushes):
    _gr(env, monkeypatch)
    monkeypatch.setattr(mi, '_mover_owns', lambda h: True)
    dl, _ = env
    assert mi.tick(now=100, fetch=lambda: [torrent(dl, tags='luma-test')], path_map=lambda p: p)['checked'] == 0
    assert movie_grabs.by_hash(H)['state'] == 'grabbed' and pushes == []


def test_mover_takes_job_after_claim_backs_off(env, monkeypatch, pushes):
    """Race: library_mover tracks the hash between tick's check and the claim -> import_one backs off."""
    _gr(env, monkeypatch)
    calls = iter([False, True])
    monkeypatch.setattr(mi, '_mover_owns', lambda h: next(calls))
    dl, _ = env
    assert mi.tick(now=100, fetch=lambda: [torrent(dl, tags='luma-test')], path_map=lambda p: p)['imported'] == 0
    assert movie_grabs.by_hash(H)['state'] == 'grabbed' and pushes == []


def test_mover_owns_reads_store(monkeypatch):
    from app import file_management as fm
    monkeypatch.setattr(fm, 'load', lambda: {'jobs': {H: {}}})
    assert mi._mover_owns(H.upper()) is True
    monkeypatch.setattr(fm, 'load', lambda: {'jobs': {}})
    assert mi._mover_owns(H) is False

    def boom():
        raise OSError
    monkeypatch.setattr(fm, 'load', boom)
    assert mi._mover_owns(H) is True          # fail safe


def test_mark_imported_once_and_claim(env):
    movie_grabs.record(693134, REL, H, now=1)
    assert movie_grabs.claim_importing(H, now=2) is True
    assert movie_grabs.claim_importing(H, now=3) is False         # held
    assert movie_grabs.claim_importing(H, now=3 + movie_grabs.STALE_IMPORTING_S) is True   # stale: retaken
    assert movie_grabs.mark_imported(H, '/m/a.mkv', now=4) is True
    assert movie_grabs.mark_imported(H, '/m/b.mkv', now=5) is False
    assert movie_grabs.by_hash(H)['path'] == '/m/a.mkv'
    assert movie_grabs.claim_importing(H, now=6) is False


def test_busy_row_left_untouched(env, monkeypatch, pushes):
    _gr(env, monkeypatch)
    movie_grabs.claim_importing(H, now=99)
    dl, _ = env
    assert mi.tick(now=100, fetch=lambda: [torrent(dl, tags='luma-test')], path_map=lambda p: p)['imported'] == 0
    assert movie_grabs.by_hash(H)['state'] == 'importing'


def _mover_run(monkeypatch, state='done'):
    notified = []
    monkeypatch.setattr(library_notify, 'notify_library_added', lambda **kw: notified.append(kw))
    monkeypatch.setattr(lm, '_set_job', lambda h, **f: None)
    monkeypatch.setattr(lm.qbit_owner, 'is_test_torrent', lambda t, tag=None: True)
    monkeypatch.setattr(lm, 'process', lambda job, t, data, now, lf: {
        'state': state, 'dest': '/media/Video/Movies/Dune - Part Two (2024)', 'files': [{'result': 'copied'}]})
    job = {'hash': H, 'media_type': 'movie', 'title': '', 'year': '2024', 'tmdb_id': 693134}
    t = {'progress': 1, 'state': sorted(lm.COMPLETED_STATES)[0]}
    summary = {'checked': 0, 'filed': 0, 'dropped': 0}
    lm._run_job(H, job, t, {}, 100.0, 'luma-test', None, summary)
    return notified, summary


def test_library_mover_marks_gr_row_and_notifies_once(env, monkeypatch):
    _gr(env, monkeypatch)
    notified, summary = _mover_run(monkeypatch)
    assert summary['filed'] == 1
    assert movie_grabs.by_hash(H)['state'] == 'imported'
    assert len(notified) == 1 and notified[0]['title'] == 'Dune: Part Two' and notified[0]['source'] == 'library_mover'
    notified2, summary2 = _mover_run(monkeypatch)        # row already imported: skipped, no second push
    assert notified2 == [] and summary2['filed'] == 0


def test_library_mover_loses_mark_no_notify(env, monkeypatch):
    _gr(env, monkeypatch)
    monkeypatch.setattr(movie_grabs, 'mark_imported', lambda *a, **k: False)
    notified, _ = _mover_run(monkeypatch)
    assert notified == []


def test_library_mover_waits_while_movie_import_holds_row(env, monkeypatch):
    _gr(env, monkeypatch)
    movie_grabs.claim_importing(H, now=99)
    notified, summary = _mover_run(monkeypatch)
    assert summary['filed'] == 0 and notified == []


def test_library_mover_rowless_job_still_notifies(env, monkeypatch):
    notified, _ = _mover_run(monkeypatch)
    assert len(notified) == 1
