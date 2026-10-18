"""v177 (Reviewer, REQUIRED): volume, OS and NAS folders (System Volume Information, $RECYCLE.BIN,
#snapshot, @Recycle, .snapshot, @eaDir, .Trashes, .Trash-1000, .AppleDouble, ...) are never show folders.
A library holding only one of them, with the mount up, is not a library with "real show folders": a
same-path save keeps the guard and nothing drops. The scan never lists them as shows. 'temp' stays a
show name. Fake TMDB, temp folders only."""
import logging
import os
import shutil

import pytest

from app import file_management as fm, tv_scan, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows  # noqa: F401
from tests.test_v171_m34 import _known, mount  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

JUNK = ['System Volume Information', '$RECYCLE.BIN', '#snapshot', '@Recycle', '.snapshot', '@eaDir', '@EADIR',
        '.Trashes', '.Trash-1000', '.AppleDouble', '.DS_Store', '@tmp', 'lost+found', '#recycle', 'RECYCLER',
        '@Recently-Snapshot', '@sharebin', '.zfs']


@pytest.fixture
def owner(minimal_app, tv_env):  # noqa: F811
    return _client(minimal_app)


def _put(owner, body):
    return owner.put(f'/api/settings/library-paths/{PID}', json=body, environ_base=REMOTE, headers=HDR)


def _junk(root, name):
    d = root / name
    d.mkdir()
    (d / 'Some.Show.S01E01.mkv').write_bytes(b'x' * 64)          # tempting: a video inside
    return d


def _state():
    return rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id')


def _detached():
    return rows("SELECT series_id FROM history WHERE event = 'detached'")


@pytest.mark.parametrize('name', JUNK)
def test_only_a_system_folder_on_a_live_mount_keeps_the_guard(owner, lib, mount, name, caplog):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before = _state()
    assert len(before) >= 6
    metas = {m: tv_store.get_meta(m + PID) for m in (tv_scan.MOUNT_META, tv_scan.DEV_META)}
    assert all(metas.values())
    for e in list(lib.iterdir()):                                 # every show gone, on the live mount...
        e.unlink() if e.is_symlink() or not e.is_dir() else shutil.rmtree(e)
    _junk(lib, name)                                              # ...only the volume folder left
    assert os.path.ismount(str(lib)) and str(os.stat(os.path.realpath(lib)).st_dev) == metas[tv_scan.DEV_META]
    tv_scan.scan_all(now=2000.0)
    assert tv_scan.skipped(PID)
    with caplog.at_level(logging.INFO, logger='app.tv_scan'):
        assert _put(owner, {'name': 'TV', 'path': fm.load()['paths'][0]['path']}).status_code == 200
    msgs = [x.getMessage() for x in caplog.records if x.name == 'app.tv_scan']
    assert any('it has no show folders' in m for m in msgs)
    assert not any('the next scan takes its current folders as the baseline' in m for m in msgs)
    assert {m: tv_store.get_meta(m + PID) for m in metas} == metas
    assert tv_scan.rebaseline(PID) is False
    for t in (2600.0, 6000.0, 2000.0 + 86400 * 2):
        tv_scan.scan_all(now=t)
        assert tv_scan.skipped(PID)                              # still Unavailable
    assert _state() == before and _detached() == []
    assert name not in _known()


def test_the_scan_never_lists_system_folders_as_shows(lib, mount):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    shows0, known0 = _state(), _known()
    for name in JUNK:
        _junk(lib, name)
    assert tv_scan.show_folders(os.path.realpath(lib)) == sorted(known0 & set(tv_scan.show_folders(os.path.realpath(lib))), key=str.casefold)
    tv_scan.scan_all(now=2000.0)
    assert not tv_scan.skipped(PID)
    assert _known() == known0 and _state() == shows0 and _detached() == []
    assert not set(JUNK) & set(tv_scan.show_folders(os.path.realpath(lib)))


def test_temp_and_lookalikes_are_still_shows(lib, mount):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    for name in ('temp', 'Snapshot', 'Recycle', 'Trash'):
        _junk(lib, name)
    tv_scan.scan_all(now=2000.0)
    assert {'temp', 'Snapshot', 'Recycle', 'Trash'} <= _known()


@pytest.mark.parametrize('name', JUNK + ['.anything-hidden'])
def test_system_dir_is_casefolded(name):
    assert tv_scan._system_dir(name) and tv_scan._system_dir(name.upper()) and tv_scan._system_dir(name.lower())


@pytest.mark.parametrize('name', ['temp', 'Temp', 'Snapshot', 'Recycle', 'Trash', 'tmp', 'System', 'Volume'])
def test_plain_words_are_not_system_dirs(name):
    assert not tv_scan._system_dir(name)
