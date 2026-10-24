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


# --- Tester BLOCK on e055c3d: a partial qBittorrent list must not prune (sanity limit) ---------------------

def _h(i, tail='0000'):
    return f'{i:036x}{tail}'


def _seed(env, n_entries, listed_present, extra_listed=0):
    """Monitor with n_entries hash keys (+ 'not-a-hash'); qBit lists the first listed_present of them plus
    extra_listed torrents the monitor doesn't track."""
    keys = [_h(i) for i in range(n_entries)]
    monitor = {k: {'name': f't{i}'} for i, k in enumerate(keys)}
    monitor['not-a-hash'] = {'name': 'odd'}
    text = json.dumps(monitor)
    (env.data / 'download_monitor.json').write_text(text)
    env.qbit['torrents'] = ([{'hash': k} for k in keys[:listed_present]]
                            + [{'hash': _h(10_000 + i, 'ffff')} for i in range(extra_listed)])
    return text


def _baks(env):
    return [p for p in os.listdir(env.data) if p.endswith('.bak')]


def test_partial_list_1_of_600_is_refused(env, caplog):
    text = _seed(env, 600, 1)
    with caplog.at_level(logging.INFO, logger='app.dlmon_prune'):
        r = dlmon_prune.run_once()
    assert r['status'] == 'refused' and r['would_drop'] == 599 and r['listed'] == 1
    assert (env.data / 'download_monitor.json').read_text() == text          # byte-identical
    assert not _baks(env) and not (env.data / 'download_monitor.json.tmp').exists()
    assert dlmon_prune.META_KEY not in env.meta                                # retried next start
    warns = [m.getMessage() for m in caplog.records if m.levelno == logging.WARNING]
    assert len(warns) == 1 and 'refused' in warns[0] and '599' in warns[0]
    assert not [m for m in caplog.records if 'dropped' in m.getMessage()]
    # next start with qBit's full list goes through (not stuck)
    env.qbit['torrents'] = [{'hash': _h(i)} for i in range(600)]
    r = dlmon_prune.run_once()
    assert r['status'] == 'pruned' and r['dropped'] == [] and env.meta[dlmon_prune.META_KEY]


def test_refusal_warns_once_then_info(env, caplog):
    # v264 (Tester LOW): a refused prune uses warn-once too
    _seed(env, 600, 1)
    with caplog.at_level(logging.INFO, logger='app.dlmon_prune'):
        for _ in range(3):
            assert dlmon_prune.run_once()['status'] == 'refused'
    assert [m.levelno for m in caplog.records] == [logging.WARNING, logging.INFO, logging.INFO]
    assert env.meta[dlmon_prune.RETRY_KEY] == 'refused'
    assert dlmon_prune.META_KEY not in env.meta


@pytest.mark.parametrize('n, drop, ok', [
    (50, 10, True), (50, 11, False),          # under 100 entries the floor is 10
    (100, 10, True), (100, 11, False),        # 10% of 100 == 10
    (200, 20, True), (200, 21, False),        # 10% of 200 == 20
    (5, 5, True),                             # small file: dropping all 5 is within the floor of 10
])
def test_threshold_edges(env, n, drop, ok):
    text = _seed(env, n, n - drop, extra_listed=drop)        # qBit lists enough torrents in total
    r = dlmon_prune.run_once()
    if ok:
        assert r['status'] == 'pruned' and len(r['dropped']) == drop
        assert len(_monitor(env)) == n - drop + 1
        assert env.meta[dlmon_prune.META_KEY]
    else:
        assert r['status'] == 'refused' and r['would_drop'] == drop
        assert (env.data / 'download_monitor.json').read_text() == text and not _baks(env)
        assert dlmon_prune.META_KEY not in env.meta


def test_drop_limit():
    assert [dlmon_prune.drop_limit(n) for n in (0, 1, 99, 100, 109, 110, 600)] == [10, 10, 10, 10, 10, 11, 60]


@pytest.mark.parametrize('listed, ok', [(2, False), (3, True)])
def test_qbit_lists_fewer_torrents_than_would_drop(env, listed, ok):
    # 6 entries, qBit lists `listed` of them: drop 6 - listed (under the limit of 10 either way), so only the
    # "fewer torrents listed than would be dropped" rule decides.
    text = _seed(env, 6, listed)
    r = dlmon_prune.run_once()
    if ok:
        assert r['status'] == 'pruned' and len(r['dropped']) == 3                 # listed 3 == drop 3
    else:
        assert r['status'] == 'refused' and r['listed'] == 2 and r['would_drop'] == 4 and r['limit'] == 10
        assert (env.data / 'download_monitor.json').read_text() == text and not _baks(env)
        assert dlmon_prune.META_KEY not in env.meta


def test_v2_hash_fields_count_as_one_torrent(env):
    env.qbit['torrents'] = [{'hash': 'f' * 40, 'infohash_v1': 'f' * 40, 'infohash_v2': 'e' * 64}]
    assert dlmon_prune.known_torrents() == ({'f' * 40, 'e' * 64}, 1)


def test_expected_real_case_still_drops_one(env, caplog):
    """Taylor's real case: only The Manchurian Candidate (…201e) is gone from qBittorrent."""
    with caplog.at_level(logging.INFO, logger='app.dlmon_prune'):
        r = dlmon_prune.run_once()
    assert r['status'] == 'pruned' and r['dropped'] == [H_GONE]
    assert env.meta[dlmon_prune.META_KEY]
    assert any('dropped 1 entry: …201e' in m.getMessage() for m in caplog.records)
    assert not [m for m in caplog.records if m.levelno >= logging.WARNING]


# --- Tester LOW: qBit down warns on the first start only, INFO after -------------------------------------

def test_qbit_down_warns_once_then_info(env, caplog):
    env.qbit['torrents'] = None
    with caplog.at_level(logging.INFO, logger='app.dlmon_prune'):
        for _ in range(3):
            assert dlmon_prune.run_once()['status'] == 'unreadable'
    levels = [m.levelno for m in caplog.records]
    assert levels == [logging.WARNING, logging.INFO, logging.INFO]
    assert env.meta[dlmon_prune.RETRY_KEY] == 'unreadable'
    assert dlmon_prune.META_KEY not in env.meta and _monitor(env) == env.monitor


def test_new_reason_warns_again(env, caplog):
    env.qbit['torrents'] = None
    dlmon_prune.run_once()
    env.qbit['torrents'] = [{'hash': H_KEEP}]
    (env.data / 'download_monitor.json').write_text('[]')
    caplog.clear()
    with caplog.at_level(logging.INFO, logger='app.dlmon_prune'):
        assert dlmon_prune.run_once()['status'] == 'unexpected'
    assert [m.levelno for m in caplog.records] == [logging.WARNING]


# --- v264 (Tester LOW): refused shares warn-once; the memory follows each start --------------------------

def _levels(caplog):
    return [m.levelno for m in caplog.records]


def test_unreadable_refused_unreadable_warns_each_time(env, caplog):
    _seed(env, 600, 1)
    full = env.qbit['torrents']
    with caplog.at_level(logging.INFO, logger='app.dlmon_prune'):
        env.qbit['torrents'] = None
        assert dlmon_prune.run_once()['status'] == 'unreadable'
        assert env.meta[dlmon_prune.RETRY_KEY] == 'unreadable'
        env.qbit['torrents'] = full
        assert dlmon_prune.run_once()['status'] == 'refused'
        assert env.meta[dlmon_prune.RETRY_KEY] == 'refused'
        env.qbit['torrents'] = None
        assert dlmon_prune.run_once()['status'] == 'unreadable'
        assert env.meta[dlmon_prune.RETRY_KEY] == 'unreadable'
    assert _levels(caplog) == [logging.WARNING] * 3


def test_successful_prune_clears_the_warned_reason(env, caplog):
    env.qbit['torrents'] = None
    dlmon_prune.run_once()
    assert env.meta[dlmon_prune.RETRY_KEY] == 'unreadable'
    env.qbit['torrents'] = [{'hash': H_KEEP}, {'hash': H_UPPER}]
    assert dlmon_prune.run_once()['status'] == 'pruned'
    assert not env.meta[dlmon_prune.RETRY_KEY]


def test_no_file_start_clears_the_warned_reason(env):
    _seed(env, 600, 1)
    dlmon_prune.run_once()
    assert env.meta[dlmon_prune.RETRY_KEY] == 'refused'
    (env.data / 'download_monitor.json').unlink()
    assert dlmon_prune.run_once()['status'] == 'no file'
    assert not env.meta[dlmon_prune.RETRY_KEY]


def test_refused_after_refused_after_success_is_new(env, caplog):
    """Memory cleared by a successful start: a later refusal (after the meta key is reset) warns again."""
    _seed(env, 600, 1)
    dlmon_prune.run_once()
    env.meta[dlmon_prune.RETRY_KEY] = ''           # as a successful start leaves it
    caplog.clear()
    with caplog.at_level(logging.INFO, logger='app.dlmon_prune'):
        assert dlmon_prune.run_once()['status'] == 'refused'
    assert _levels(caplog) == [logging.WARNING]
