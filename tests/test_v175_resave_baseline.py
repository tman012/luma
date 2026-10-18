"""v175 (PM): when the owner saves a TV library path again with the SAME folder, its recorded mount,
device id and coverage streak are cleared (INFO line) and the next scan takes the folders there now as
the baseline. The retire-to-local-disk case: the NAS is retired, the same path now holds 1 known show
and 1 new one on the local disk; it is Unavailable (v174 rule) until the re-save, then the next scan
keeps the present show (same id), detaches the gone ones (history kept), finds the new one and
records the new mount and device. Fake TMDB, temp folders only."""
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

T0 = 2000.0
NEW = 'Local Only Show (2024)'


@pytest.fixture
def owner(minimal_app, tv_env):  # noqa: F811
    return _client(minimal_app)


def _series():
    return {r['folder']: r['id'] for r in rows('SELECT id, folder FROM series WHERE path_id = ? AND folder IS NOT NULL', PID)}


def _detached_ids():
    return sorted({r['series_id'] for r in rows("SELECT series_id FROM history WHERE event = 'detached'")})


def _retire_to_local_disk(lib, tmp_path):  # noqa: F811
    """NAS retired: the same path is now a local folder with 1 known show and 1 new one."""
    tv_scan.scan_all(now=1000.0)
    shows = _series()
    known = sorted(shows)
    assert len(_known()) >= 3
    share = tmp_path / 'nas-share'
    shutil.move(str(lib), str(share))
    lib.mkdir()
    shutil.copytree(str(share / known[0]), str(lib / known[0]))
    (lib / NEW).mkdir()
    (lib / NEW / 'Local.Only.Show.S01E01.mkv').write_bytes(b'x' * 64)
    return shows, known[0]


def _put(owner, body):
    return owner.put(f'/api/settings/library-paths/{PID}', json=body, environ_base=REMOTE, headers=HDR)


def test_retire_then_resave_the_same_path_then_the_next_scan_releases(owner, lib, mount, tmp_path, caplog):  # noqa: F811
    shows, kept = _retire_to_local_disk(lib, tmp_path)
    mount['on'] = False                                         # no longer a mount point
    before = rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id')
    for dt in (0, 600, 3700, 86400 * 2):                        # v174: Unavailable, nothing changes
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID)
    assert rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id') == before

    path = fm.load()['paths'][0]['path']
    with caplog.at_level(logging.INFO, logger='app.tv_scan'):
        r = _put(owner, {'name': 'TV', 'path': path})            # the owner saves it again, same folder
    assert r.status_code == 200, r.get_json()
    lines = [x for x in caplog.records if x.name == 'app.tv_scan' and 'saved again with the same folder' in x.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO and PID in lines[0].getMessage()
    for meta in (tv_scan.MOUNT_META, tv_scan.DEV_META, tv_scan.COVERAGE_META):
        assert tv_store.get_meta(meta + PID) is None
    assert rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id') == before     # nothing until the scan

    tv_scan.scan_all(now=T0 + 86400 * 3)                        # the next scan: the new baseline
    assert not tv_scan.skipped(PID)
    after = _series()
    assert after.get(kept) == shows[kept]                       # the present show keeps its id
    gone = sorted(i for f, i in shows.items() if f != kept)
    assert all(f not in after for f in shows if f != kept)      # the others detach...
    assert _detached_ids() == gone                              # ...with history kept
    assert NEW in _known()                                      # the new local show is found
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == tv_scan._mount_of(str(lib))   # re-recorded
    assert tv_store.get_meta(tv_scan.DEV_META + PID) == str(os.stat(os.path.realpath(lib)).st_dev)

    tv_scan.scan_all(now=T0 + 86400 * 4)                        # and it stays a normal path
    assert not tv_scan.skipped(PID) and _series().get(kept) == shows[kept] and _detached_ids() == gone


def test_a_save_without_the_path_does_not_rebaseline(owner, lib, mount, tmp_path):  # noqa: F811
    _retire_to_local_disk(lib, tmp_path)
    mount['on'] = False
    tv_scan.scan_all(now=T0)
    assert tv_scan.skipped(PID)
    assert _put(owner, {'name': 'Shows'}).status_code == 200    # a rename only (no folder sent)
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) is not None
    tv_scan.scan_all(now=T0 + 600)
    assert tv_scan.skipped(PID) and _detached_ids() == []


def test_a_rename_with_the_same_path_never_clears_the_mount_record(owner, lib, mount, tmp_path, caplog):  # noqa: F811
    """PM (D): the UI sends {name, path}; a changed name with the same folder is a rename only. Only a
    save with both the name and the path unchanged clears the records."""
    _retire_to_local_disk(lib, tmp_path)
    mount['on'] = False
    tv_scan.scan_all(now=T0)
    assert tv_scan.skipped(PID)
    rec = fm.load()['paths'][0]
    metas = {m: tv_store.get_meta(m + PID) for m in (tv_scan.MOUNT_META, tv_scan.DEV_META)}
    assert all(metas.values())
    with caplog.at_level(logging.INFO, logger='app.tv_scan'):
        r = _put(owner, {'name': rec['name'] + ' renamed', 'path': rec['path']})
    assert r.status_code == 200, r.get_json()
    assert fm.load()['paths'][0]['name'] == rec['name'] + ' renamed'
    assert {m: tv_store.get_meta(m + PID) for m in metas} == metas         # kept
    assert not [x for x in caplog.records if 'saved again with the same folder' in x.getMessage()]
    tv_scan.scan_all(now=T0 + 600)
    assert tv_scan.skipped(PID) and _detached_ids() == []                   # still guarded
    r = _put(owner, {'name': rec['name'] + ' renamed', 'path': rec['path']})   # name and path unchanged now
    assert r.status_code == 200
    for meta in (tv_scan.MOUNT_META, tv_scan.DEV_META, tv_scan.COVERAGE_META):
        assert tv_store.get_meta(meta + PID) is None                        # cleared
    tv_scan.scan_all(now=T0 + 1200)
    assert not tv_scan.skipped(PID)


def test_a_resave_never_baselines_an_empty_folder(owner, lib, mount, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before = rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id')
    shutil.move(str(lib), str(tmp_path / 'share'))
    lib.mkdir()                                                 # the bare mount point, nothing on it
    mount['on'] = False
    tv_scan.scan_all(now=T0)
    assert _put(owner, {'name': 'TV', 'path': fm.load()['paths'][0]['path']}).status_code == 200
    tv_scan.scan_all(now=T0 + 600)
    assert tv_scan.skipped(PID)                                 # still Unavailable: empty
    assert rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id') == before and _detached_ids() == []


def test_moving_the_path_is_still_a_repoint_not_a_rebaseline(owner, lib, tmp_path, monkeypatch):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    called = []
    monkeypatch.setattr(tv_scan, 'rebaseline', lambda pid: called.append(pid))
    other = tmp_path / 'elsewhere'
    other.mkdir()
    (other / 'X (2020)').mkdir()
    assert _put(owner, {'name': 'TV', 'path': str(other)}).status_code == 200
    assert called == []
    assert tv_store.get_meta(tv_scan.ROOT_META + PID) == tv_scan.REPOINTED


def test_a_non_owner_cannot_trigger_it(minimal_app, lib, tmp_path):  # noqa: F811
    import app.user_auth
    tv_scan.scan_all(now=1000.0)
    mount_before = tv_store.get_meta(tv_scan.MOUNT_META + PID)
    app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    second = app.user_auth.create_user('second', 'pin-1234567', is_admin=True)
    c = minimal_app.test_client()
    import app.main
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(second['id']))
    r = c.put(f'/api/settings/library-paths/{PID}', json={'name': 'TV', 'path': fm.load()['paths'][0]['path']},
              environ_base=REMOTE, headers=HDR)
    assert r.status_code in (401, 403)
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == mount_before
