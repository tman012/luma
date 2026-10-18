"""v174 (Reviewer on c669f76).

1) REQUIRED: the coverage guard's exit never releases, and resets its streak, while the mount the
   path was on is dropped or the recorded mount is not mounted now: one known folder on the bare mount
   point stays Unavailable across scans at +0/+600/+1200/+3700 s, nothing detached. PM's rule as
   stated, no exception: a path retired from the NAS to local disk at the same path with under half its
   known folders stays Unavailable until the owner edits the path to another folder or removes it
   (both clear the recorded mount).
2) A streak whose first timestamp is in the future restarts now.
3) own_addresses(): each name resolves in its own try; one failing name never drops the rest.
4) save_radarr_processed: a unique temp file (mkstemp) and an atomic replace; a saved
   indexer.movie_categories migrates to tracker_search.movie_categories.
Fake TMDB, temp folders, fake DNS only."""
import hashlib
import json
import os
import shutil
import socket
import unittest.mock as um

import pytest

from app import tv_scan, tv_store
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_m34 import _state, _visible
from tests.test_tv_scan import PID, lib, rows  # noqa: F401
from tests.test_v171_m34 import _known, mount  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_v173_auto_rename_giveup import ar, _old_hashes  # noqa: F401

T0 = 2000.0


def _coverage():
    raw = tv_store.get_meta(tv_scan.COVERAGE_META + PID)
    return json.loads(raw) if raw else None


def _one_known_on_bare_mount_point(lib, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == os.path.realpath(lib)
    known = sorted(_known())
    assert len(known) >= 3                                        # one folder is under half
    share = tmp_path / 'share'
    shutil.move(str(lib), str(share))
    lib.mkdir()                                                   # the bare mount point...
    shutil.copytree(str(share / known[0]), str(lib / known[0]))   # ...with one known folder on it
    return share, known


def test_a_dropped_mount_never_releases_and_nothing_detaches(lib, mount, tmp_path):  # noqa: F811
    _one_known_on_bare_mount_point(lib, tmp_path)
    before, seen, known = _state(), _visible(), _known()
    mount['on'] = False                                           # the mount drops
    assert tv_scan._dropped_mount(PID, os.path.realpath(lib))
    for dt in (0, 600, 1200, 3700):
        out = tv_scan.scan_all(now=T0 + dt)
        assert out['missing'] == [PID] and tv_scan.skipped(PID)    # Unavailable every time
        assert _coverage() is None                                 # no streak builds up
    assert _state() == before and _visible() == seen and _known() == known   # nothing detached
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == os.path.realpath(lib)   # not re-recorded


def test_the_recorded_mount_not_mounted_is_enough(lib, mount, tmp_path, monkeypatch):  # noqa: F811
    _one_known_on_bare_mount_point(lib, tmp_path)
    before = _state()
    mount['on'] = False
    monkeypatch.setattr(tv_scan, '_dropped_mount', lambda pid, root: False)   # only the ismount signal
    for dt in (0, 600, 1200, 3700, 86400 * 2):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID) and _coverage() is None
    assert _state() == before


def test_a_streak_built_while_mounted_resets_when_the_mount_drops(lib, mount, tmp_path):  # noqa: F811
    _one_known_on_bare_mount_point(lib, tmp_path)
    for n, dt in enumerate((0, 600), 1):                          # mounted, coverage 1/N: counting
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID) and _coverage()['scans'] == n
    mount['on'] = False
    tv_scan.scan_all(now=T0 + 3700)                               # would have been the releasing scan
    assert tv_scan.skipped(PID) and _coverage() is None
    mount['on'] = True                                            # back up, same set: count from scratch
    tv_scan.scan_all(now=T0 + 3800)
    assert tv_scan.skipped(PID) and _coverage()['scans'] == 1 and _coverage()['first'] == T0 + 3800


def test_a_retire_to_local_disk_at_the_same_path_stays_unavailable_until_the_path_is_edited(lib, mount, tmp_path):  # noqa: F811
    """PM's rule, no exception: the old mount is not mounted, so the exit never counts it as up."""
    _one_known_on_bare_mount_point(lib, tmp_path)                # NAS gone, a partial local copy
    mount['on'] = False                                           # same path, no longer a mount point
    before = _state()
    for dt in (0, 600, 3700, 86400 * 3):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID)
    assert _state() == before
    tv_scan.forget_mount(PID)                                     # what an edit to another folder / removal does
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) is None and tv_store.get_meta(tv_scan.DEV_META + PID) is None


def test_device_id_rule_another_device_at_the_same_path_is_not_up(lib, mount, tmp_path, monkeypatch):  # noqa: F811
    """PM's device-id rule: still a mount point (or no mount at all), but the folder's st_dev differs
    from the last good scan's: never counts as up, so the exit never releases."""
    _one_known_on_bare_mount_point(lib, tmp_path)
    dev = tv_store.get_meta(tv_scan.DEV_META + PID)
    assert dev == str(os.stat(os.path.realpath(lib)).st_dev)     # recorded at the good scan
    real_dev = tv_scan._dev_of
    monkeypatch.setattr(tv_scan, '_dev_of', lambda p: (real_dev(p) or 0) + 1)   # a different device now
    before = _state()
    for dt in (0, 600, 1200, 3700, 86400 * 2):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID) and _coverage() is None
    assert _state() == before
    monkeypatch.setattr(tv_scan, '_dev_of', real_dev)             # same device again: the exit counts as usual
    for dt in (90000, 90600):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID)
    tv_scan.scan_all(now=T0 + 90000 + 3600)
    assert not tv_scan.skipped(PID)


def test_no_device_record_yet_is_no_signal(lib, mount, tmp_path):  # noqa: F811
    """A path last scanned before v174 has no device id: the exit works as in v173 (mount up) and the
    next good scan records it."""
    _one_known_on_bare_mount_point(lib, tmp_path)
    tv_store.set_meta(tv_scan.DEV_META + PID, None)
    for dt in (0, 600):
        tv_scan.scan_all(now=T0 + dt)
        assert tv_scan.skipped(PID)
    tv_scan.scan_all(now=T0 + 3700)
    assert not tv_scan.skipped(PID)
    assert tv_store.get_meta(tv_scan.DEV_META + PID) == str(os.stat(os.path.realpath(lib)).st_dev)


def test_a_future_first_timestamp_restarts_now(lib, mount, tmp_path):  # noqa: F811
    _one_known_on_bare_mount_point(lib, tmp_path)
    shows = [f for f in tv_scan.show_folders(os.path.realpath(lib)) if not tv_scan._system_dir(f)]
    sig = hashlib.sha256('\x00'.join(sorted(shows)).encode('utf-8', 'surrogatepass')).hexdigest()[:32]
    tv_store.set_meta(tv_scan.COVERAGE_META + PID, json.dumps({'sig': sig, 'first': T0 + 10 ** 7, 'scans': 9}))
    before = _state()
    tv_scan.scan_all(now=T0)
    assert tv_scan.skipped(PID) and _state() == before
    assert _coverage() == {'sig': sig, 'first': T0, 'scans': 1}
    tv_scan.scan_all(now=T0 + 600)
    tv_scan.scan_all(now=T0 + 3700)                               # a normal hour from *now* releases
    assert not tv_scan.skipped(PID)


@pytest.mark.parametrize('bad', ['', None, 'x', [1]])
def test_an_unreadable_first_timestamp_restarts_now(bad):
    sig = hashlib.sha256(b'A').hexdigest()[:32]
    meta = {tv_scan.COVERAGE_META + 'p_x': json.dumps({'sig': sig, 'first': bad, 'scans': 2})}
    with um.patch.object(tv_store, 'get_meta', lambda k: meta.get(k)), \
            um.patch.object(tv_store, 'set_meta', lambda k, v: meta.__setitem__(k, v)):
        assert tv_scan._coverage_released('p_x', ['A'], 'coverage 1/3', T0) is False
    st = json.loads(meta[tv_scan.COVERAGE_META + 'p_x'])
    assert float(st['first']) == T0


# 3) own_addresses

def test_one_failing_name_never_drops_the_others(monkeypatch):
    from app import tracker_http
    monkeypatch.setattr(tracker_http, 'interface_addresses', lambda: frozenset())
    monkeypatch.setattr(tracker_http, '_public_behind_tunnel', lambda: False)
    monkeypatch.setattr(tracker_http, 'public_hosts',
                        lambda: frozenset({'luma.example.test', 'broken.example.test', 'bad.example.test'}))
    monkeypatch.setattr(socket, 'gethostname', lambda: 'kiosk-host')
    monkeypatch.setattr(socket, 'getfqdn', lambda: 'kiosk-host.lan')
    table = {'kiosk-host': '192.168.1.40', 'kiosk-host.lan': '192.168.1.41', 'luma.example.test': '203.0.113.9'}

    def fake(name, port, *a, **k):
        if name == 'broken.example.test':
            raise socket.gaierror(-2, 'Name or service not known')
        if name == 'bad.example.test':
            raise UnicodeError('label too long')
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (table[name], 0))]
    monkeypatch.setattr(socket, 'getaddrinfo', fake)
    tracker_http.reset_own_addresses_cache()
    try:
        assert tracker_http.own_addresses() >= {'192.168.1.40', '192.168.1.41', '203.0.113.9'}
    finally:
        tracker_http.reset_own_addresses_cache()


# 4) save_radarr_processed

def _bare(ar, tmp_path):  # noqa: F811
    r = ar.AutoRenamer.__new__(ar.AutoRenamer)
    r.radarr_processed_file = str(tmp_path / 'radarr_processed.json')
    r.load_radarr_processed()
    return r


def test_save_uses_a_unique_temp_name_and_an_atomic_replace(ar, tmp_path, monkeypatch):  # noqa: F811
    import tempfile
    made, replaced = [], []
    real_mkstemp, real_replace = tempfile.mkstemp, os.replace

    def mkstemp(*a, **k):
        fd, p = real_mkstemp(*a, **k)
        made.append(p)
        return fd, p

    def replace(src, dst):
        replaced.append((src, dst))
        return real_replace(src, dst)
    monkeypatch.setattr(tempfile, 'mkstemp', mkstemp)
    monkeypatch.setattr(os, 'replace', replace)
    a, b = _bare(ar, tmp_path), _bare(ar, tmp_path)               # two savers on one store
    for i, h in enumerate(_old_hashes(4)):
        (a if i % 2 else b).radarr_processed.add(h)
        (a if i % 2 else b).save_radarr_processed()
    assert len(made) == 4 and len(set(made)) == 4                 # never a shared '<file>.tmp'
    assert all(os.path.dirname(p) == str(tmp_path) for p in made)  # same filesystem: replace is atomic
    assert [d for _, d in replaced] == [str(tmp_path / 'radarr_processed.json')] * 4
    assert [s for s, _ in replaced] == made
    assert sorted(os.listdir(tmp_path)) == ['radarr_processed.json']   # no temp file left behind
    assert isinstance(json.loads((tmp_path / 'radarr_processed.json').read_text()), list)


def test_a_failed_write_leaves_the_store_and_no_temp_file(ar, tmp_path, monkeypatch):  # noqa: F811
    r = _bare(ar, tmp_path)
    r.radarr_processed.add(_old_hashes(1)[0])
    r.save_radarr_processed()
    good = (tmp_path / 'radarr_processed.json').read_text()
    r.radarr_processed.add(_old_hashes(2)[1])

    def boom(*a, **k):
        raise OSError(28, 'No space left on device')
    monkeypatch.setattr(ar.json, 'dump', boom)
    r.save_radarr_processed()                                     # logged, never raised
    assert (tmp_path / 'radarr_processed.json').read_text() == good
    assert sorted(os.listdir(tmp_path)) == ['radarr_processed.json']


def test_a_saved_indexer_movie_categories_value_migrates(tmp_path):
    from app.settings_manager import SettingsManager
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'indexer': {'provider': 'prowlarr', 'movie_categories': '2000,2040,2045'}}))
    sm = SettingsManager(str(p))
    assert sm.get('tracker_search.movie_categories') == '2000,2040,2045'
    assert not sm.get('indexer.movie_categories')
    ok, errors = sm.update({'tracker_search': {'movie_categories': '2000,2040,2045'}})
    assert ok, errors
    saved = json.loads(p.read_text())
    assert saved['tracker_search']['movie_categories'] == '2000,2040,2045'
    assert 'movie_categories' not in saved.get('indexer', {})
    assert SettingsManager(str(p)).get('tracker_search.movie_categories') == '2000,2040,2045'


# Ops on c669f76: the migration line never reached docker logs or luma.log (it ran during app.main's
# imports, before logging was configured). It logs once, at INFO, on logger app.settings_manager.

MIG = 'Migrated: indexer.movie_categories -> tracker_search'


def _old_settings(tmp_path):
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'indexer': {'provider': 'prowlarr', 'movie_categories': '2000,2040', 'limit': 50}}))
    return p


def _mig_records(caplog):
    return [r for r in caplog.records if r.name == 'app.settings_manager' and r.getMessage().startswith(MIG)]


def test_the_migration_logs_once_at_info(tmp_path, caplog):
    import logging
    from app.settings_manager import SettingsManager
    caplog.set_level(logging.INFO, logger='app.settings_manager')
    p = _old_settings(tmp_path)
    SettingsManager(str(p))
    recs = _mig_records(caplog)
    assert len(recs) == 1 and recs[0].levelno == logging.INFO
    SettingsManager(str(p))                                       # migrated and saved: never again
    assert len(_mig_records(caplog)) == 1


def test_before_logging_is_configured_the_line_waits_and_is_flushed_once(tmp_path, caplog, monkeypatch):
    import logging
    from app import settings_manager as sm
    caplog.set_level(logging.INFO, logger='app.settings_manager')
    monkeypatch.setattr(sm, '_logging_configured', lambda: False)  # app.main still importing
    sm.SettingsManager(str(_old_settings(tmp_path)))
    assert _mig_records(caplog) == []
    monkeypatch.undo()
    assert sm.flush_migration_logs() >= 1
    recs = _mig_records(caplog)
    assert len(recs) == 1 and recs[0].levelno == logging.INFO
    assert sm.flush_migration_logs() == 0 and len(_mig_records(caplog)) == 1


def test_a_real_boot_logs_it_to_stdout_and_luma_log_once(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    _old_settings(tmp_path)
    (tmp_path / 'logs').mkdir()
    env = {'HOME': os.environ.get('HOME', '/tmp'), 'PATH': '/usr/bin:/bin', 'LUMA_MINIMAL': '1',
           'DATABASE_PATH': str(tmp_path / 'luma.db'), 'LUMA_DATA_DIR': str(tmp_path),
           'LUMA_CACHE_DIR': str(tmp_path / 'cache'), 'LUMA_LOG_DIR': str(tmp_path / 'logs'),
           'LUMA_SKIP_BACKGROUND_THREADS': '1', 'FLASK_STATIC_FOLDER': str(repo / 'static')}
    out = subprocess.run([sys.executable, '-c', 'import app.main'], cwd=str(repo), env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = [ln for ln in (out.stdout + out.stderr).splitlines() if MIG in ln]
    assert len(lines) == 1 and ' - app.settings_manager - INFO - ' in lines[0]
    assert (tmp_path / 'logs' / 'luma.log').read_text().count(MIG) == 1
    assert json.loads((tmp_path / 'settings.json').read_text())['tracker_search'] == {'movie_categories': '2000,2040'}


# Tester FF-9 on c669f76 (same bug as Reviewer's item 1): 4 known shows, the mount drops, the bare mount
# point holds 1 of the 4. It released after 3 scans over 1 h and detached 3 shows; now it never does.

FF9 = 'p_ff9ff9ff'
FOUR = {910001 + i: f'Mount Show {chr(65 + i)}' for i in range(4)}


def test_ff9_one_of_four_on_a_dropped_mount_never_releases(tv_env, fake_tmdb, tmp_path, monkeypatch):  # noqa: F811
    from tests import tv_fake
    from tests.test_tv_scan import select
    for tid, n in FOUR.items():
        monkeypatch.setitem(tv_fake.SHOWS, tid, {'name': n, 'first_air_date': '2020-01-01', 'seasons': {1: 2}})
    root = tv_env / 'TV' / 'NAS'
    for n in FOUR.values():
        (root / n).mkdir(parents=True)
        (root / n / f'{n}.S01E01.mkv').write_bytes(b'x' * 64)
    real, m = os.path.ismount, {'on': True}
    monkeypatch.setattr(os.path, 'ismount', lambda p: (m['on'] and os.path.realpath(p) == os.path.realpath(root)) or real(p))
    select(FF9, root, name='NAS')
    tv_scan._state.update(running=False, error=None, summary=None)
    tv_scan._backoff.update(fails=0, until=0.0)
    tv_scan.scan_all(now=1000.0)
    attached = {r['folder']: r['tmdb_id'] for r in rows('SELECT folder, tmdb_id FROM series WHERE path_id = ?', FF9)}
    assert sorted(attached) == sorted(FOUR.values())
    share = tmp_path / 'nas-share'
    shutil.move(str(root), str(share))
    root.mkdir()
    first = sorted(FOUR.values())[0]
    shutil.copytree(str(share / first), str(root / first))     # 1 of 4 on the bare mount point
    m['on'] = False                                             # the mount dropped
    for t in (2000.0, 2600.0, 3200.0, 2000.0 + 3700, 2000.0 + 86400, 2000.0 + 3 * 86400):
        tv_scan.scan_all(now=t)
        assert tv_scan.skipped(FF9)                             # Unavailable
    now_att = {r['folder']: r['tmdb_id'] for r in rows('SELECT folder, tmdb_id FROM series WHERE path_id = ?', FF9)}
    assert now_att == attached                                  # nothing detached
    assert rows("SELECT 1 FROM history WHERE event = 'detached'") == []
    shutil.rmtree(str(root))                                    # remount: all 4 back, same ids
    shutil.move(str(share), str(root))
    m['on'] = True
    tv_scan.scan_all(now=2000.0 + 4 * 86400)
    assert not tv_scan.skipped(FF9)
    assert {r['folder']: r['tmdb_id'] for r in rows('SELECT folder, tmdb_id FROM series WHERE path_id = ?', FF9)} == attached
