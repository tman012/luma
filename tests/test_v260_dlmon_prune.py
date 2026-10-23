"""v260: one-time download_monitor.json prune (dlmon_prune_v260). Everything mocked: tmp data dir, in-memory
meta, qBittorrent's torrents/info (storage_cleanup.fetch_torrents) patched; qBit writes and BT_backup reads trip."""
import json
import logging
import os
import sys
import types

import pytest

from app import dlmon_prune

H_KEEP = '7224bfad84076c0a76c36fb891d3b91ac226efb6'
H_GONE = 'aaaabbbbccccddddeeeeffff000011112222201e'
H_UPPER = 'ABCDEF0123456789ABCDEF0123456789ABCD9999'


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    monkeypatch.setenv('LUMA_DATA_DIR', str(data))
    meta = {}
    from app import storage_cleanup, tv_store
    monkeypatch.setattr(tv_store, 'get_meta', lambda k: meta.get(k))
    monkeypatch.setattr(tv_store, 'set_meta', lambda k, v: meta.__setitem__(k, v))
    qbit = {'torrents': [{'hash': H_KEEP.upper(), 'name': 'AHS'}, {'hash': H_UPPER.lower(), 'name': 'Upper'}],
            'calls': 0}

    def fetch():
        qbit['calls'] += 1
        t = qbit['torrents']
        if isinstance(t, Exception):
            raise t
        return t
    monkeypatch.setattr(storage_cleanup, 'fetch_torrents', fetch)

    def no_write(*a, **k):
        raise AssertionError('qBittorrent write')
    monkeypatch.setattr(storage_cleanup, 'delete_with_files', no_write)
    real_listdir = os.listdir

    def listdir(p='.'):
        assert 'bt_backup' not in str(p).lower(), 'BT_backup read'
        return real_listdir(p)
    monkeypatch.setattr(os, 'listdir', listdir)
    monkeypatch.setitem(dlmon_prune._cfg, 'now', lambda: 1792800000.0)
    monitor = {H_KEEP: {'name': 'AHS'}, H_GONE: {'name': 'The Manchurian Candidate'},
               H_UPPER: {'name': 'Upper'}, 'not-a-hash': {'name': 'odd'}}
    (data / 'download_monitor.json').write_text(json.dumps(monitor))
    origins = json.dumps({H_GONE: {'origin': 'grab'}})
    (data / 'grab_origins.json').write_text(origins)
    return types.SimpleNamespace(data=data, meta=meta, qbit=qbit, monitor=monitor, origins=origins)


def _monitor(env):
    return json.loads((env.data / 'download_monitor.json').read_text())


def test_backup_written_before_prune(env):
    r = dlmon_prune.run_once()
    assert r['status'] == 'pruned'
    baks = [p for p in os.listdir(env.data) if p.endswith('.bak')]
    assert len(baks) == 1 and baks[0].startswith('download_monitor.json.') and baks[0].count('-') == 1
    assert json.loads((env.data / baks[0]).read_text()) == env.monitor


def test_only_missing_entries_dropped(env, caplog):
    with caplog.at_level(logging.INFO, logger='app.dlmon_prune'):
        r = dlmon_prune.run_once()
    assert r['dropped'] == [H_GONE]
    assert set(_monitor(env)) == {H_KEEP, H_UPPER, 'not-a-hash'}
    lines = [m.getMessage() for m in caplog.records if m.levelno == logging.INFO]
    assert len(lines) == 1 and 'dropped 1 entry: …201e' in lines[0] and H_GONE not in lines[0]


@pytest.mark.parametrize('how', ['down', 'empty', 'error', 'not a list'])
def test_qbit_down_or_empty_does_nothing(env, caplog, how):
    env.qbit['torrents'] = {'down': None, 'empty': [], 'error': RuntimeError('boom'), 'not a list': [None, 'x']}[how]
    with caplog.at_level(logging.WARNING, logger='app.dlmon_prune'):
        r = dlmon_prune.run_once()
    assert r['status'] == ('error' if how == 'error' else 'unreadable')
    assert _monitor(env) == env.monitor
    assert not [p for p in os.listdir(env.data) if p.endswith('.bak')]
    assert dlmon_prune.META_KEY not in env.meta                 # tried again next start
    assert any(m.levelno >= logging.WARNING for m in caplog.records)


def test_matches_case_insensitively_and_v2_hashes(env):
    env.qbit['torrents'] = [{'hash': H_KEEP[:40].upper()}, {'hash': 'f' * 40, 'infohash_v1': H_UPPER}]
    dlmon_prune.run_once()
    assert set(_monitor(env)) == {H_KEEP, H_UPPER, 'not-a-hash'}


def test_runs_once(env):
    assert dlmon_prune.run_once()['status'] == 'pruned'
    assert env.meta[dlmon_prune.META_KEY]
    (env.data / 'download_monitor.json').write_text(json.dumps(env.monitor))
    assert dlmon_prune.run_once()['status'] == 'done before'
    assert _monitor(env) == env.monitor
    assert len([p for p in os.listdir(env.data) if p.endswith('.bak')]) == 1


def test_grab_origins_untouched(env):
    before = os.stat(env.data / 'grab_origins.json').st_mtime_ns
    dlmon_prune.run_once()
    assert (env.data / 'grab_origins.json').read_text() == env.origins
    assert os.stat(env.data / 'grab_origins.json').st_mtime_ns == before


def test_nothing_reads_bt_backup():
    src = open(dlmon_prune.__file__).read()
    code = src.split('"""', 2)[2]                                  # past the module docstring
    assert 'fastresume' not in code.lower() and 'bt_backup' not in code.lower() and 'listdir' not in code
    assert not hasattr(dlmon_prune, 'BT_BACKUP_SETTING') and not hasattr(dlmon_prune, 'bt_backup_dir')


def test_uses_guard_read_only_torrent_list(env):
    dlmon_prune.run_once()
    assert env.qbit['calls'] == 1


def test_errors_are_caught(env, monkeypatch):
    (env.data / 'download_monitor.json').write_text('{broken')
    assert dlmon_prune.run_once()['status'] == 'error'
    assert dlmon_prune.META_KEY not in env.meta


def test_no_monitor_file(env):
    (env.data / 'download_monitor.json').unlink()
    assert dlmon_prune.run_once()['status'] == 'no file'
    assert env.meta[dlmon_prune.META_KEY] == 'no file'


def test_loaded_auto_renamer_forgets_dropped(env, monkeypatch):
    renamer = types.SimpleNamespace(tracked_downloads={H_KEEP: {}, H_GONE: {}})
    monkeypatch.setitem(sys.modules, 'app.auto_rename', types.SimpleNamespace(auto_renamer=renamer))
    dlmon_prune.run_once()
    assert set(renamer.tracked_downloads) == {H_KEEP}


def test_monitor_path_uses_data_dir(env):
    assert dlmon_prune.monitor_path() == str(env.data / 'download_monitor.json')


def test_boot_runs_prune_in_the_guard_boot_thread(monkeypatch):
    import threading
    from app import downloads_guard, storage_cleanup
    from app.automation import dg_store
    seen = []
    monkeypatch.setattr(dg_store, 'seed', lambda: {'protected_tags': ['luma-keep']})
    monkeypatch.setattr(dlmon_prune, 'run_once', lambda: seen.append(('prune', threading.current_thread().name)))
    monkeypatch.setattr(storage_cleanup, 'log_never_delete_now', lambda cfg: seen.append(('log', threading.current_thread().name)))
    downloads_guard.startup_seed_and_log()
    for t in threading.enumerate():
        if t.name == 'downloads-guard-never-delete':
            t.join(2)
    assert seen == [('prune', 'downloads-guard-never-delete'), ('log', 'downloads-guard-never-delete')]


def test_prune_failure_still_logs(monkeypatch):
    from app import downloads_guard, storage_cleanup
    seen = []

    def boom():
        raise RuntimeError('x')
    monkeypatch.setattr(dlmon_prune, 'run_once', boom)
    monkeypatch.setattr(storage_cleanup, 'log_never_delete_now', lambda cfg: seen.append(cfg))
    downloads_guard._boot_reads({'a': 1})
    assert seen == [{'a': 1}]
