"""v175 (PM): when the owner saves a TV library path again with the SAME folder, its recorded mount,
device id and coverage streak are cleared (INFO line) and the next scan takes the folders there now as
the baseline. v176 (Reviewer, REQUIRED): never while the recorded mount is down (dropped, not mounted, or another
device): the records stay and the path stays Unavailable until it really remounts. Fake TMDB, temp folders only."""
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


def test_a_resave_while_the_mount_is_down_keeps_the_records(owner, lib, mount, tmp_path, caplog):  # noqa: F811
    """v176 (Reviewer, REQUIRED): the NAS retired, the same path now a local folder with 1 known show and
    1 new one. The recorded mount is down, so Save (which always sends the path) keeps the mount and
    device records and doesn't re-baseline: the save succeeds, the path stays Unavailable."""
    shows, kept = _retire_to_local_disk(lib, tmp_path)
    mount['on'] = False                                         # no longer a mount point
    before = rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id')
    tv_scan.scan_all(now=T0)
    assert tv_scan.skipped(PID)
    metas = {m: tv_store.get_meta(m + PID) for m in (tv_scan.MOUNT_META, tv_scan.DEV_META)}
    assert all(metas.values())
    path = fm.load()['paths'][0]['path']
    with caplog.at_level(logging.INFO, logger='app.tv_scan'):
        r = _put(owner, {'name': 'TV', 'path': path})            # the owner saves it again, same folder
    assert r.status_code == 200, r.get_json()
    msgs = [x for x in caplog.records if x.name == 'app.tv_scan' and PID in x.getMessage()]
    assert any('but its recorded mount is down' in x.getMessage() and x.levelno == logging.INFO for x in msgs)
    assert not any('the next scan takes its current folders as the baseline' in x.getMessage() for x in msgs)
    assert {m: tv_store.get_meta(m + PID) for m in metas} == metas         # kept
    for dt in (600, 3700, 86400 * 2):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID)                                         # still Unavailable
    assert rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id') == before and _detached_ids() == []


def test_a_resave_with_the_mount_up_takes_a_new_baseline(owner, lib, mount, tmp_path, caplog):  # noqa: F811
    """The mount is up (same mount, same device) and most shows were deleted outside Luma: the coverage
    guard holds the path; the owner's same-folder save clears the records and the next scan releases."""
    tv_scan.scan_all(now=1000.0)
    shows = _series()
    known = sorted(shows)
    assert len(known) >= 3
    for f in known[1:]:
        shutil.rmtree(str(lib / f))                             # gone, on the live mount
    (lib / NEW).mkdir()
    (lib / NEW / 'Local.Only.Show.S01E01.mkv').write_bytes(b'x' * 64)
    tv_scan.scan_all(now=T0)
    assert tv_scan.skipped(PID)                                 # coverage guard
    with caplog.at_level(logging.INFO, logger='app.tv_scan'):
        assert _put(owner, {'name': 'TV', 'path': fm.load()['paths'][0]['path']}).status_code == 200
    lines = [x for x in caplog.records if x.name == 'app.tv_scan' and 'saved again with the same folder:' in x.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    for meta in (tv_scan.MOUNT_META, tv_scan.DEV_META, tv_scan.COVERAGE_META):
        assert tv_store.get_meta(meta + PID) is None
    tv_scan.scan_all(now=T0 + 60)                               # the next scan: the new baseline
    assert not tv_scan.skipped(PID)
    after = _series()
    assert after.get(known[0]) == shows[known[0]]               # the present show keeps its id
    assert _detached_ids() == sorted(shows[f] for f in known[1:])   # the others detach, history kept
    assert NEW in _known()
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == tv_scan._mount_of(str(lib))
    assert tv_store.get_meta(tv_scan.DEV_META + PID) == str(os.stat(os.path.realpath(lib)).st_dev)


def test_reviewer_repro_a_save_on_a_dropped_mount_loses_nothing(owner, lib, mount, tmp_path):  # noqa: F811
    """Reviewer: record the mount, drop it, leave one folder on the bare mount point, save the path
    again (rebaseline), scan: no show lost. Remount: all of them back, same ids, no Unmatched duplicate."""
    tv_scan.scan_all(now=1000.0)
    shows = _series()
    unmatched0 = rows('SELECT folder FROM unmatched WHERE path_id = ? ORDER BY folder', PID)
    known = sorted(_known())
    assert len(shows) == 6                                      # the 6 shows (plus review rows)
    share = tmp_path / 'share'
    shutil.move(str(lib), str(share))
    lib.mkdir()
    shutil.copytree(str(share / known[0]), str(lib / known[0]))   # one folder on the bare mount point
    mount['on'] = False                                         # the mount dropped
    before = rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id')
    assert _put(owner, {'name': 'TV', 'path': fm.load()['paths'][0]['path']}).status_code == 200
    assert tv_scan.rebaseline(PID) is False                     # and called directly: still refused
    for dt in (0, 600, 3700, 86400 * 2):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID)
    assert rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id') == before   # no show lost
    assert _detached_ids() == []
    shutil.rmtree(str(lib))                                     # remount: everything back
    shutil.move(str(share), str(lib))
    mount['on'] = True
    tv_scan.scan_all(now=T0 + 86400 * 3)
    assert not tv_scan.skipped(PID)
    assert _series() == shows                                   # all back, same ids
    assert sorted(_known()) == known
    assert rows('SELECT folder FROM unmatched WHERE path_id = ? ORDER BY folder', PID) == unmatched0   # no duplicate
    dup = rows('SELECT folder, COUNT(*) AS n FROM unmatched WHERE path_id = ? GROUP BY folder HAVING n > 1', PID)
    assert dup == [] and not (set(shows) & {r['folder'] for r in rows('SELECT folder FROM unmatched WHERE path_id = ?', PID)})


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
    r = _put(owner, {'name': rec['name'] + ' renamed', 'path': rec['path']})   # name and path unchanged now...
    assert r.status_code == 200
    assert {m: tv_store.get_meta(m + PID) for m in metas} == metas         # ...but the mount is down (v176): kept


def test_a_resave_never_baselines_an_empty_folder(owner, lib, mount, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before = rows('SELECT id, path_id, folder, tmdb_id FROM series ORDER BY id')
    shutil.move(str(lib), str(tmp_path / 'share'))
    lib.mkdir()                                                 # the bare mount point, nothing on it
    mount['on'] = False
    tv_scan.scan_all(now=T0)
    metas = {m: tv_store.get_meta(m + PID) for m in (tv_scan.MOUNT_META, tv_scan.DEV_META)}
    assert _put(owner, {'name': 'TV', 'path': fm.load()['paths'][0]['path']}).status_code == 200
    assert {m: tv_store.get_meta(m + PID) for m in metas} == metas     # v176: nothing cleared
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


def test_reviewer_case_6_then_0_then_1_never_strands_a_show_in_unmatched(owner, lib, mount, tmp_path, caplog):  # noqa: F811
    """Reviewer's sequence: 6 shows; the share drops (0 folders on the
    bare mount point) and the owner saves; then 1 folder appears on the bare mount point and the owner
    saves again; scans in between. No show is lost and none ends up in Unmatched; on remount all 6 are
    back with the same ids."""
    tv_scan.scan_all(now=1000.0)
    shows = _series()
    assert len(shows) == 6
    unmatched0 = {r['folder'] for r in rows('SELECT folder FROM unmatched WHERE path_id = ?', PID)}
    share = tmp_path / 'share'
    shutil.move(str(lib), str(share))
    lib.mkdir()                                                 # 0 folders on the bare mount point
    mount['on'] = False
    path = fm.load()['paths'][0]['path']
    assert _put(owner, {'name': 'TV', 'path': path}).status_code == 200
    tv_scan.scan_all(now=T0)
    assert tv_scan.skipped(PID) and _series() == shows
    first = sorted(shows)[0]
    shutil.copytree(str(share / first), str(lib / first))      # then 1 on the bare mount point
    assert _put(owner, {'name': 'TV', 'path': path}).status_code == 200
    for dt in (600, 3700, 86400 * 2):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID)
    assert _series() == shows and _detached_ids() == []
    assert {r['folder'] for r in rows('SELECT folder FROM unmatched WHERE path_id = ?', PID)} == unmatched0
    shutil.rmtree(str(lib))
    shutil.move(str(share), str(lib))
    mount['on'] = True
    tv_scan.scan_all(now=T0 + 86400 * 3)
    assert not tv_scan.skipped(PID) and _series() == shows
    assert {r['folder'] for r in rows('SELECT folder FROM unmatched WHERE path_id = ?', PID)} == unmatched0
    assert not set(shows) & unmatched0


def test_an_empty_folder_never_rebaselines_even_without_records(owner, lib, mount, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    for m in (tv_scan.MOUNT_META, tv_scan.DEV_META):
        tv_store.set_meta(m + PID, None)                        # no proof either way
    shutil.move(str(lib), str(tmp_path / 'share'))
    lib.mkdir()
    assert tv_scan.rebaseline(PID) is False
    assert tv_store.get_meta(tv_scan.ROOT_META + PID) != tv_scan.REPOINTED
