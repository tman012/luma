"""v173 (Tester, FF-C): the M34 scenario from fastgate ab030b7 x/m170, exactly.

Library M is a mount holding 1883, Adolescence and Ahsoka. Under it, the bare mount-point folder
still holds a stale 'Alone' show folder. When M drops, the path reads Unavailable, the three shows
stay (no detach), 'Alone' is not taken over; with the bare folder emptied it is still Unavailable;
remounted, the same series ids and monitor settings are back. Fake TMDB, temp folders only."""
import os
import shutil

import pytest

from app import tv_scan, tv_settings, tv_store
from tests import tv_fake
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import rows, select
from tests.tv_fake import fake_tmdb  # noqa: F401

PM = 'p_385ab47d'
M_SHOWS = {800004: ('1883', '2021-12-19'), 800019: ('Adolescence', '2025-03-13'), 800021: ('Ahsoka', '2023-08-22')}
ALONE = 63726


@pytest.fixture
def m_lib(tv_env, fake_tmdb, tmp_path, monkeypatch):  # noqa: F811
    for tid, (name, aired) in M_SHOWS.items():
        monkeypatch.setitem(tv_fake.SHOWS, tid, {'name': name, 'first_air_date': aired, 'seasons': {1: 4}})
    monkeypatch.setitem(tv_fake.SHOWS, ALONE, {'name': 'Alone', 'first_air_date': '2015-06-18', 'seasons': {1: 10}})
    bare = tv_env / 'TV' / 'M'
    (bare / 'Alone').mkdir(parents=True)                     # stale, under the mount
    (bare / 'Alone' / 'Alone.S01E01.mkv').write_bytes(b'x' * 64)
    share = tmp_path / 'nas-share'                           # what the mount shows
    for tid, (name, _) in M_SHOWS.items():
        (share / name).mkdir(parents=True)
        (share / name / f'{name}.S01E01.mkv').write_bytes(b'x' * 64)
    hidden = tmp_path / 'hidden-bare'
    m = {'on': False, 'bare': bare, 'share': share, 'hidden': hidden}

    def mount():
        shutil.move(str(bare), str(hidden))
        shutil.copytree(str(share), str(bare))
        m['on'] = True

    def umount():
        shutil.rmtree(str(bare))
        shutil.move(str(hidden), str(bare))
        m['on'] = False

    real = os.path.ismount
    root = os.path.realpath(bare)
    monkeypatch.setattr(os.path, 'ismount', lambda p: (m['on'] and os.path.realpath(p) == root) or real(p))
    m.update(mount=mount, umount=umount)
    select(PM, bare, name='M')
    tv_scan._state.update(running=False, error=None, summary=None)
    tv_scan._backoff.update(fails=0, until=0.0)
    return m


def _m_series():
    return rows('SELECT id, folder, tmdb_id, monitor, monitored, monitor_new_seasons FROM series '
                'WHERE path_id = ? ORDER BY id', PM)


def _m_state():
    return (_m_series(),
            rows('SELECT f.id, f.series_id, f.rel_path FROM episode_files f JOIN series s ON s.id = f.series_id '
                 'WHERE s.path_id = ? ORDER BY f.id', PM),
            rows('SELECT id, folder FROM unmatched WHERE path_id = ? ORDER BY id', PM))


def _status():
    p = next(p for p in tv_settings.libraries() if p['id'] == PM)
    return tv_settings.tv_path_status(p)


def test_tester_m170_dropped_mount_over_a_stale_show_folder(m_lib):
    m_lib['mount']()
    tv_scan.scan_all(now=1000.0)
    before = _m_state()
    assert [(r['folder'], r['tmdb_id']) for r in before[0]] == [
        ('1883', 800004), ('Adolescence', 800019), ('Ahsoka', 800021)]
    assert _status() == 'ok'

    m_lib['umount']()                                        # the bare folder holds only 'Alone'
    assert os.listdir(m_lib['bare']) == ['Alone']
    for t in (2000.0, 3000.0):
        out = tv_scan.scan_all(now=t)
        assert PM in out['missing']
    assert _status() == 'unavailable'                        # Settings reads Unavailable
    assert _m_state() == before                              # no detach: rows, files, review unchanged
    assert not rows('SELECT 1 FROM series WHERE tmdb_id = ?', ALONE)            # no takeover
    assert not rows("SELECT 1 FROM unmatched WHERE folder = 'Alone'")

    shutil.rmtree(str(m_lib['bare'] / 'Alone'))              # bare folder emptied
    tv_scan.scan_all(now=4000.0)
    assert _status() == 'unavailable' and _m_state() == before

    (m_lib['bare'] / 'Alone').mkdir()                        # put it back so the remount hides it
    m_lib['mount']()
    out = tv_scan.scan_all(now=5000.0)
    assert PM not in out['missing'] and _status() == 'ok'
    after = _m_series()
    assert after == before[0]                                # same series ids and monitor settings
    assert not rows('SELECT 1 FROM series WHERE tmdb_id = ?', ALONE)
