"""v175 (PM): when the library path is a LIVE mount (mounted, not dropped) with real show folders and
its device id changes (a remount, a replaced share), the coverage guard's release streak starts again on
the new device instead of blocking forever. A bare mount point on its parent's device never counts, and
the owner's same-folder re-save clear (rebaseline) stays. Temp folders and fake TMDB only."""
import logging
import os

from app import tv_scan, tv_store
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_m34 import _state
from tests.test_tv_scan import PID, lib, rows  # noqa: F401
from tests.test_v171_m34 import mount  # noqa: F401
from tests.test_v174_reviewer_c669f76 import T0, _coverage, _one_known_on_bare_mount_point
from tests.tv_fake import fake_tmdb  # noqa: F401


def _detached():
    return rows("SELECT 1 FROM history WHERE event = 'detached'")


def _new_device_for(root, monkeypatch, bump=7):
    """The folder at *root* (and below) is on another device now; its parent keeps the real one."""
    real_dev = tv_scan._dev_of
    root = os.path.realpath(root)

    def fake(p):
        d = real_dev(p)
        rp = os.path.realpath(p)
        return d + bump if d is not None and (rp == root or rp.startswith(root + '/')) else d
    monkeypatch.setattr(tv_scan, '_dev_of', fake)
    return real_dev


def test_a_live_mount_on_a_new_device_restarts_the_streak_and_then_releases(lib, mount, tmp_path, monkeypatch, caplog):  # noqa: F811
    _one_known_on_bare_mount_point(lib, tmp_path)               # lib stays mounted (mount['on'] True)
    old_dev = tv_store.get_meta(tv_scan.DEV_META + PID)
    for dt in (0, 600):                                         # a streak builds on the old device
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID)
    assert _coverage()['scans'] == 2 and _coverage()['first'] == T0
    before = _state()
    _new_device_for(lib, monkeypatch)
    caplog.set_level(logging.INFO, logger='app.tv_scan')
    tv_scan.scan_all(now=T0 + 1200)                             # the device changed: the streak restarts here
    assert tv_scan.skipped(PID)
    assert _coverage()['scans'] == 1 and _coverage()['first'] == T0 + 1200
    new_dev = tv_store.get_meta(tv_scan.DEV_META + PID)
    assert new_dev != old_dev and new_dev == str(tv_scan._dev_of(os.path.realpath(lib)))
    assert sum('live mount on a new device' in r.getMessage() and r.levelno == logging.INFO
               for r in caplog.records) == 1
    tv_scan.scan_all(now=T0 + 3700)                             # the old streak would have released here
    assert tv_scan.skipped(PID) and _state() == before and _detached() == []
    assert _coverage()['scans'] == 2
    tv_scan.scan_all(now=T0 + 1200 + 3600)                      # 3 scans over 1 h on the new device
    assert not tv_scan.skipped(PID)
    assert _detached() != []                                    # the gone shows detach now (history kept)
    assert sum('live mount on a new device' in r.getMessage() for r in caplog.records) == 1   # once


def test_a_bare_mount_point_on_its_parents_device_never_counts(lib, mount, tmp_path, monkeypatch):  # noqa: F811
    """ismount says yes, but the folder sits on its parent's device (a bare mount point; or retired to
    the local disk): the device differs from the last good scan's, it is not live, never released."""
    _one_known_on_bare_mount_point(lib, tmp_path)
    recorded = tv_store.get_meta(tv_scan.DEV_META + PID)
    real_dev = tv_scan._dev_of
    monkeypatch.setattr(tv_scan, '_dev_of', lambda p: (real_dev(p) or 0) + 1)   # root and parent alike
    assert not tv_scan._live_on_new_device(PID, os.path.realpath(lib))
    before = _state()
    for dt in (0, 600, 1200, 3700, 86400 * 2):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID) and _coverage() is None
    assert _state() == before and _detached() == []
    assert tv_store.get_meta(tv_scan.DEV_META + PID) == recorded  # not re-recorded


def test_a_dropped_mount_on_a_new_device_never_counts(lib, mount, tmp_path, monkeypatch):  # noqa: F811
    _one_known_on_bare_mount_point(lib, tmp_path)
    _new_device_for(lib, monkeypatch)
    mount['on'] = False                                          # not mounted: dropped
    assert not tv_scan._live_on_new_device(PID, os.path.realpath(lib))
    for dt in (0, 600, 1200, 3700, 86400 * 2):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID) and _coverage() is None
    assert _detached() == []


def test_the_owner_re_save_clear_still_works_after_a_new_device(lib, mount, tmp_path, monkeypatch):  # noqa: F811
    _one_known_on_bare_mount_point(lib, tmp_path)
    _new_device_for(lib, monkeypatch)
    tv_scan.scan_all(now=T0)
    assert tv_scan.skipped(PID) and _coverage() is not None
    tv_scan.rebaseline(PID)
    for meta in (tv_scan.MOUNT_META, tv_scan.DEV_META, tv_scan.COVERAGE_META):
        assert tv_store.get_meta(meta + PID) is None
    tv_scan.scan_all(now=T0 + 60)                                # the next scan is the baseline
    assert not tv_scan.skipped(PID)
    assert tv_store.get_meta(tv_scan.DEV_META + PID) == str(tv_scan._dev_of(os.path.realpath(lib)))
