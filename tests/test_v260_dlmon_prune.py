"""v260: one-time download_monitor.json prune (dlmon_prune_v260). Everything mocked: tmp data dir, tmp
BT_backup, in-memory meta, settings lookup patched."""
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
    bt = tmp_path / 'BT_backup'
    data.mkdir()
    bt.mkdir()
    monkeypatch.setenv('LUMA_DATA_DIR', str(data))
    meta = {}
    from app import tv_store
    monkeypatch.setattr(tv_store, 'get_meta', lambda k: meta.get(k))
    monkeypatch.setattr(tv_store, 'set_meta', lambda k, v: meta.__setitem__(k, v))
    folder = {'v': str(bt)}
    monkeypatch.setattr(dlmon_prune, 'bt_backup_dir', lambda: folder['v'])
    monkeypatch.setitem(dlmon_prune._cfg, 'now', lambda: 1792800000.0)
    monitor = {H_KEEP: {'name': 'AHS'}, H_GONE: {'name': 'The Manchurian Candidate'},
               H_UPPER: {'name': 'Upper'}, 'not-a-hash': {'name': 'odd'}}
    (data / 'download_monitor.json').write_text(json.dumps(monitor))
    origins = json.dumps({H_GONE: {'origin': 'grab'}})
    (data / 'grab_origins.json').write_text(origins)
    for h in (H_KEEP, H_UPPER.lower()):
        (bt / f'{h}.fastresume').write_text('x')
        (bt / f'{h}.torrent').write_text('x')
    return types.SimpleNamespace(data=data, bt=bt, meta=meta, folder=folder, monitor=monitor, origins=origins)


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


@pytest.mark.parametrize('how', ['unset', 'missing', 'empty', 'relative'])
def test_unreadable_dir_does_nothing(env, caplog, how):
    if how == 'unset':
        env.folder['v'] = ''
    elif how == 'missing':
        env.folder['v'] = str(env.bt / 'nope')
    elif how == 'relative':
        env.folder['v'] = 'BT_backup'
    else:
        for p in env.bt.iterdir():
            p.unlink()
    with caplog.at_level(logging.WARNING, logger='app.dlmon_prune'):
        r = dlmon_prune.run_once()
    assert r['status'] == 'unreadable'
    assert _monitor(env) == env.monitor
    assert not [p for p in os.listdir(env.data) if p.endswith('.bak')]
    assert dlmon_prune.META_KEY not in env.meta                 # tried again next start
    assert any(m.levelno == logging.WARNING for m in caplog.records)


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


def test_bt_backup_read_only(env):
    before = sorted((p.name, p.stat().st_mtime_ns) for p in env.bt.iterdir())
    dlmon_prune.run_once()
    assert sorted((p.name, p.stat().st_mtime_ns) for p in env.bt.iterdir()) == before


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


def test_setting_is_read_from_settings_file(tmp_path, monkeypatch):
    import importlib
    mod = importlib.reload(dlmon_prune)
    from app import settings_manager
    seen = {}

    class Fake:
        def get(self, k, default=None):
            seen['k'] = k
            return '/bt'
    monkeypatch.setattr(settings_manager, 'SettingsFile', Fake)
    assert mod.bt_backup_dir() == '/bt' and seen['k'] == 'qbittorrent.bt_backup_dir'


def test_startup_calls_prune_before_seed():
    src = open(os.path.join(os.path.dirname(__file__), '..', 'app', 'main.py')).read()
    assert 0 < src.index('dlmon_prune.run_once()') < src.index('startup_seed_and_log()')
