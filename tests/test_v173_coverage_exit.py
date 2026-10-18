"""v173 (Reviewer on 17ecf51, REQUIRED; PM's exit): the coverage guard releases once the same folder
set has been seen, mount up, for 3 scans spanning 1 h, or for 24 h.

8 known folders, 7 deleted outside Luma, 1 new show: after the release the new show is found and only
those 7 are detached. Also a path with a single show renamed or replaced. Fake TMDB, temp folders."""
import shutil

import pytest

from app import tv_scan
from tests import tv_fake
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import rows, select
from tests.tv_fake import fake_tmdb  # noqa: F401

PC = 'p_c0ffee08'
KNOWN = {900000 + i: f'Coverage Show {chr(64 + i)}' for i in range(1, 9)}     # A..H
NEW = (900100, 'Brand New Arrival')


@pytest.fixture
def eight(tv_env, fake_tmdb, tmp_path, monkeypatch):  # noqa: F811
    for tid, name in list(KNOWN.items()) + [NEW]:
        monkeypatch.setitem(tv_fake.SHOWS, tid, {'name': name, 'first_air_date': '2020-01-01', 'seasons': {1: 2}})
    root = tv_env / 'TV' / 'C'
    for name in KNOWN.values():
        (root / name).mkdir(parents=True)
        (root / name / f'{name}.S01E01.mkv').write_bytes(b'x' * 64)
    select(PC, root, name='C')
    tv_scan._state.update(running=False, error=None, summary=None)
    tv_scan._backoff.update(fails=0, until=0.0)
    tv_scan.scan_all(now=1000.0)
    assert sorted(r['tmdb_id'] for r in rows('SELECT tmdb_id FROM series WHERE path_id = ?', PC)) == sorted(KNOWN)
    names = sorted(KNOWN.values())
    for name in names[1:]:                                   # 7 deleted outside Luma
        shutil.rmtree(str(root / name))
    (root / NEW[1]).mkdir()                                  # 1 new show
    (root / NEW[1] / f'{NEW[1]}.S01E01.mkv').write_bytes(b'x' * 64)
    return {'root': root, 'kept': names[0], 'gone': names[1:]}


def _attached():
    return {r['folder']: r['tmdb_id'] for r in rows('SELECT folder, tmdb_id FROM series WHERE path_id = ?', PC)}


def test_before_any_exit_the_guard_holds(eight):
    before = _attached()
    for t in (2000.0, 3000.0, 4000.0):
        out = tv_scan.scan_all(now=t)
        assert PC in out['missing'] and tv_scan.skipped(PC)
    assert _attached() == before                              # nothing detached, the new show not taken
    assert NEW[1] not in _attached()


def _detached():
    return sorted(r['tmdb_id'] for r in rows(
        "SELECT s.tmdb_id FROM history h JOIN series s ON s.id = h.series_id WHERE h.event = 'detached'"))


def _check_released(eight):
    assert not tv_scan.skipped(PC)
    kept = next(t for t, n in KNOWN.items() if n == eight['kept'])
    assert _attached() == {eight['kept']: kept, NEW[1]: NEW[0]}
    assert _detached() == sorted(t for t, n in KNOWN.items() if n in eight['gone'])


def test_after_3_scans_spanning_an_hour_the_new_show_is_found_and_only_the_seven_detach(eight):
    for t in (2000.0, 3000.0):
        tv_scan.scan_all(now=t)
        assert tv_scan.skipped(PC)
    tv_scan.scan_all(now=2000.0 + 3600)                     # 3rd scan, an hour after the 1st
    _check_released(eight)
    tv_scan.scan_all(now=9000.0)                            # and it stays a normal path
    _check_released(eight)


def test_three_quick_scans_dont_release_but_24h_does(eight):
    for t in (2000.0, 2100.0, 2200.0, 2300.0):
        tv_scan.scan_all(now=t)
        assert tv_scan.skipped(PC)
    tv_scan.scan_all(now=2000.0 + 86400)
    _check_released(eight)


def test_two_scans_a_day_apart_release_on_24h(eight):
    tv_scan.scan_all(now=2000.0)
    assert tv_scan.skipped(PC)
    tv_scan.scan_all(now=2000.0 + 86400)
    _check_released(eight)


def test_a_change_in_the_folder_set_starts_the_count_again(eight):
    tv_scan.scan_all(now=2000.0)
    tv_scan.scan_all(now=3000.0)
    (eight['root'] / 'Half Synced').mkdir()                  # the share is still syncing
    (eight['root'] / 'Half Synced' / 'x.S01E01.mkv').write_bytes(b'x' * 64)
    tv_scan.scan_all(now=2000.0 + 3600)
    assert tv_scan.skipped(PC) and NEW[1] not in _attached()
    tv_scan.scan_all(now=2000.0 + 3600 + 600)
    assert tv_scan.skipped(PC)
    tv_scan.scan_all(now=2000.0 + 3600 + 3600)              # 3rd scan of this set, an hour after its 1st
    assert not tv_scan.skipped(PC) and NEW[1] in _attached()


def test_a_dropped_mount_never_releases(eight, monkeypatch):
    monkeypatch.setattr(tv_scan, '_dropped_mount', lambda pid, root: True)
    for name in [eight['kept']]:
        shutil.rmtree(str(eight['root'] / name))             # none of the known folders: the bare mount point
    before = _attached()
    for t in (2000.0, 3000.0, 6000.0, 2000.0 + 3 * 86400):
        tv_scan.scan_all(now=t)
        assert tv_scan.skipped(PC)
    assert _attached() == before and _detached() == []


SINGLE = 'p_c0ffee09'


@pytest.mark.parametrize('replacement', ['renamed', 'replaced'])
def test_a_single_show_path_renamed_or_replaced_releases(tv_env, fake_tmdb, monkeypatch, replacement):  # noqa: F811
    old, new = (900200, 'Solo Show'), (900201 if replacement == 'replaced' else 900200,
                                         'Another Show' if replacement == 'replaced' else 'Solo Show (2020)')
    for tid, name in (old, new):
        monkeypatch.setitem(tv_fake.SHOWS, tid, {'name': name.replace(' (2020)', ''),
                                                 'first_air_date': '2020-01-01', 'seasons': {1: 2}})
    root = tv_env / 'TV' / 'S'
    (root / old[1]).mkdir(parents=True)
    (root / old[1] / 'Solo.Show.S01E01.mkv').write_bytes(b'x' * 64)
    select(SINGLE, root, name='S')
    tv_scan._state.update(running=False, error=None, summary=None)
    tv_scan._backoff.update(fails=0, until=0.0)
    tv_scan.scan_all(now=1000.0)
    shutil.move(str(root / old[1]), str(root / new[1]))
    for t in (2000.0, 3000.0):
        tv_scan.scan_all(now=t)
        assert tv_scan.skipped(SINGLE)                       # coverage 0/1: held
    tv_scan.scan_all(now=2000.0 + 3600)
    assert not tv_scan.skipped(SINGLE)
    att = {r['folder']: r['tmdb_id'] for r in rows('SELECT folder, tmdb_id FROM series WHERE path_id = ?', SINGLE)}
    assert att == {new[1]: new[0]}
