"""Luma cleans up its own stale '.luma-<hex16>.luma-partial' temp files (generic, Luma-named only)."""
import logging
import os
import time

import pytest

from app import library_mover as lm
from app import partial_cleanup as pc

P1 = '.luma-0123456789abcdef.luma-partial'
P2 = '.luma-fedcba9876543210.luma-partial'
OLD = 3 * 3600


def _mk(path, age_s, data=b'x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def _sweep(*roots):
    return pc.sweep([str(r) for r in roots], max_age=3600, settle_s=0)


@pytest.fixture(autouse=True)
def _clear():
    pc._inflight.clear()
    yield
    pc._inflight.clear()


def test_partial_name_matches_the_sweep_pattern():
    for _ in range(20):
        assert pc.is_partial_name(lm.partial_name())


def test_failed_copy_removes_its_own_partial_and_reraises(tmp_path, monkeypatch):
    monkeypatch.delenv('LUMA_REMOTE_MOVER', raising=False)
    from app import remote_mover
    monkeypatch.setattr(remote_mover, 'enabled', lambda: False)
    dl = tmp_path / 'dl'; dl.mkdir()
    src = dl / 'a.mkv'; src.write_bytes(b'data' * 100)
    lib = tmp_path / 'lib'; lib.mkdir()
    seen = {}

    def boom(sfd, tfd):
        seen['inflight'] = list(pc._inflight)
        os.write(tfd, b'half')
        raise OSError(5, 'EIO mid-copy')
    monkeypatch.setattr(lm, '_paced_copy', boom)
    monkeypatch.setattr(lm, '_check_space', lambda *a, **k: None)
    dfd = os.open(str(lib), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            lm.transfer(str(src), dfd, str(lib), 'a.mkv', 'copy', None, str(dl))
    finally:
        os.close(dfd)
    assert os.listdir(lib) == []
    assert len(seen['inflight']) == 1 and pc.is_partial_name(os.path.basename(seen['inflight'][0]))
    assert pc._inflight == set()           # unregistered after the failure


def test_successful_copy_leaves_no_partial_and_no_registry(tmp_path, monkeypatch):
    from app import remote_mover
    monkeypatch.setattr(remote_mover, 'enabled', lambda: False)
    monkeypatch.setattr(lm, '_check_space', lambda *a, **k: None)
    dl = tmp_path / 'dl'; dl.mkdir()
    src = dl / 'a.mkv'; src.write_bytes(b'data' * 100)
    lib = tmp_path / 'lib'; lib.mkdir()
    dfd = os.open(str(lib), os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert lm.transfer(str(src), dfd, str(lib), 'a.mkv', 'copy', None, str(dl)) == 'copied'
    finally:
        os.close(dfd)
    assert os.listdir(lib) == ['a.mkv'] and pc._inflight == set()


def test_sweep_deletes_only_old_luma_partials_and_logs(tmp_path, caplog):
    old = _mk(tmp_path / 'Show' / 'Season 01' / P1, OLD)
    caplog.set_level(logging.INFO, logger='app.partial_cleanup')
    got = _sweep(tmp_path)
    assert got == [str(old)] and not old.exists()
    assert any('removed stale Luma partial' in r.message and r.levelno == logging.INFO for r in caplog.records)


def test_sweep_spares_fresh_partials(tmp_path):
    fresh = _mk(tmp_path / 'M' / P1, 60)
    assert _sweep(tmp_path) == [] and fresh.exists()


def test_sweep_spares_active_writers_in_registry(tmp_path):
    p = _mk(tmp_path / 'M' / P1, OLD)
    with pc.tracking(str(p)):
        assert _sweep(tmp_path) == [] and p.exists()
    assert _sweep(tmp_path) == [str(p)]


def test_sweep_spares_a_file_still_changing(tmp_path):
    p = _mk(tmp_path / 'M' / P1, OLD)

    def writer(_s):
        with open(p, 'ab') as f:
            f.write(b'more')
    assert pc.sweep([str(tmp_path)], max_age=3600, settle_s=1, sleep=writer) == []
    assert p.exists()


@pytest.mark.parametrize('name', [
    'movie.mkv', '.luma-partial', 'x.luma-partial', '.luma-0123.luma-partial',
    '.luma-0123456789ABCDEF.luma-partial', '.luma-0123456789abcdef.luma-partial.bak',
    'a.mkv.lumapart', '.luma-write-test-abc', '.luma-test-instance', '.luma-0123456789abcdef.luma.partial',
    '.Luma-0123456789abcdef.luma-partial', 'luma-0123456789abcdef.luma-partial',
])
def test_sweep_never_touches_look_alikes(tmp_path, name):
    p = _mk(tmp_path / 'M' / name, OLD)
    assert _sweep(tmp_path) == [] and p.exists()


def test_sweep_never_follows_symlinks_out_of_root(tmp_path):
    root = tmp_path / 'lib'; root.mkdir()
    outside = tmp_path / 'outside'
    victim = _mk(outside / P1, OLD)
    os.symlink(str(outside), str(root / 'linkdir'))           # folder symlink out of the root
    os.symlink(str(victim), str(root / P2))                   # partial-named symlink to an outside file
    assert _sweep(root) == []
    assert victim.exists() and os.path.islink(root / P2)


def test_sweep_skips_partial_named_directories(tmp_path):
    d = tmp_path / P1; d.mkdir()
    os.utime(d, (time.time() - OLD,) * 2)
    assert _sweep(tmp_path) == [] and d.is_dir()


def test_max_age_is_configurable(monkeypatch):
    assert pc.max_age_s() == 3600
    monkeypatch.setenv('LUMA_PARTIAL_MAX_AGE_MIN', '15')
    assert pc.max_age_s() == 900
    monkeypatch.setenv('LUMA_PARTIAL_MAX_AGE_MIN', 'junk')
    assert pc.max_age_s() == 3600


def test_maybe_sweep_runs_at_start_then_on_interval(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(pc, '_last', {'t': 0.0})
    monkeypatch.setattr(pc, 'library_roots', lambda: [str(tmp_path)])
    monkeypatch.setattr(pc, 'sweep', lambda roots: calls.append(roots) or [])
    pc.maybe_sweep(now=1000.0)
    pc.maybe_sweep(now=1000.0 + pc.SWEEP_EVERY_S - 1)
    pc.maybe_sweep(now=1000.0 + pc.SWEEP_EVERY_S)
    assert calls == [[str(tmp_path)], [str(tmp_path)]]


def test_remote_transfer_marks_its_tmp_in_flight(monkeypatch):
    from app import remote_mover as rm
    seen = {}

    def fake(src, dest, op, rsrc, rdest, cfg, tmp):
        seen['in'] = pc.in_flight(tmp)
        raise rm.RemoteMoverError('boom')
    monkeypatch.setattr(rm, '_transfer_tracked', fake)
    monkeypatch.setattr(rm, 'config', lambda: {'timeout': 1, 'sock': 'x'})
    monkeypatch.setattr(rm, 'to_remote', lambda p, cfg: p)
    monkeypatch.setattr(rm, '_assert_remote_bounds', lambda *a: None)
    with pytest.raises(rm.RemoteMoverError):
        rm.transfer('/d/a.mkv', '/media/M/a.mkv')
    assert seen['in'] is True and pc._inflight == set()


def test_partial_name_rejects_trailing_newline():
    from app.partial_cleanup import is_partial_name
    assert is_partial_name('.luma-0123456789abcdef.luma-partial') is True
    assert is_partial_name('.luma-0123456789abcdef.luma-partial\n') is False
