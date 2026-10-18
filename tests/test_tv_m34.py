"""M34 (v167): an empty library path that had shows (an unmounted share's mount point) is skipped like a
missing one: every show, file row and review row stays as last seen until its folders are back. A missing
(unmounted, gone) path is unchanged: skipped. A library that never had anything scans normally."""
import os
import shutil

from app import tv_scan
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def _state():
    return (rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id'),
            rows('SELECT id, series_id, rel_path, state FROM episode_files ORDER BY id'),
            rows('SELECT id, path_id, folder, reason FROM unmatched ORDER BY id'))


def _visible():
    v = tv_scan.library_view()
    return sorted(s['id'] for s in v['shows']), sorted(u['id'] for u in v['unmatched'])


def test_an_empty_mount_point_keeps_every_show_until_the_folders_are_back(lib, tmp_path, caplog):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before, seen = _state(), _visible()
    assert before[0] and seen[0]
    away = tmp_path / 'share-contents'
    shutil.move(str(lib), str(away))
    lib.mkdir()                                                     # the bare mount point
    with caplog.at_level('WARNING', logger='app.tv_scan'):
        out = tv_scan.scan_all(now=2000.0)
        tv_scan.scan_all(now=3000.0)
    assert out['missing'] == [PID] and out['paths'] == 0
    assert _state() == before and _visible() == seen                 # nothing hidden, nothing dropped
    assert caplog.text.count('is empty but had shows') == 1          # said once, not every scan
    assert tv_scan.last_scan() == 1000.0                             # the next tick tries again
    os.rmdir(lib)
    shutil.move(str(away), str(lib))                                 # remounted
    out = tv_scan.scan_all(now=4000.0)
    assert out['missing'] == [] and _state()[0] == before[0] and _visible() == seen


def test_an_unmounted_path_keeps_every_show(lib, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before, seen = _state(), _visible()
    os.rename(lib, tmp_path / 'gone')
    assert tv_scan.scan_all(now=2000.0)['missing'] == [PID]
    assert _state() == before and _visible() == seen
    os.rename(tmp_path / 'gone', lib)
    tv_scan.scan_all(now=3000.0)
    assert _state()[0] == before[0]


def test_one_folder_back_is_a_real_scan_again(lib, tmp_path):  # noqa: F811
    """Content returns (even partly): normal rules, so a folder that is really gone loses its rows."""
    tv_scan.scan_all(now=1000.0)
    keep = 'Severance (2022)'
    folders = sorted(r['folder'] for r in rows('SELECT folder FROM series WHERE path_id = ?', PID))
    away = tmp_path / 'away'
    away.mkdir()
    for f in os.listdir(lib):
        if f != keep:
            shutil.move(str(lib / f), str(away / f))
    tv_scan.scan_all(now=2000.0)
    left = sorted(r['folder'] for r in rows('SELECT folder FROM series WHERE path_id = ?', PID))
    assert left == [keep] and len(folders) > 1


def test_a_library_that_never_had_shows_scans_normally(tv_env):  # noqa: F811
    root = tv_env / 'Empty'
    root.mkdir()
    select('p_0000eeee', root, name='Empty')
    out = tv_scan.scan_all(now=1000.0)
    assert out['missing'] == [] and out['paths'] == 1


def test_review_rows_alone_also_count_as_last_known_state(lib, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    with __import__('app.tv_store', fromlist=['write']).write() as conn:
        conn.execute('DELETE FROM series')
    un = rows('SELECT id FROM unmatched')
    assert un
    shutil.move(str(lib), str(tmp_path / 'x'))
    lib.mkdir()
    assert tv_scan.scan_all(now=2000.0)['missing'] == [PID]
    assert rows('SELECT id FROM unmatched') == un
