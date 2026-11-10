"""The settings.json.bak-* pruning keeps the newest backups by age, not by name: older bak-pre-* files sort after the
dated names, and a name sort used to delete the backup written a moment ago."""
import json
import os

from app.settings_manager import SettingsManager


def _manager(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'network': {'flask_port': 5000}}))
    sm = SettingsManager(str(path))
    for f in path.parent.glob('settings.json.bak-*'):   # a first load may have backed up itself
        f.unlink()
    return sm, path


def _bak(path, name, age_s):
    f = path.with_name(f'{path.name}.bak-{name}')
    f.write_text('{}')
    t = 1_700_000_000 - age_s
    os.utime(f, (t, t))
    return f


def test_new_backup_survives_old_bak_pre_names(tmp_path):
    sm, path = _manager(tmp_path)
    old = [
        _bak(path, 'pre-v170', 900), _bak(path, 'pre-roulette', 800), _bak(path, 'pre-tv', 700),
        _bak(path, '20260101-010101-000001', 600), _bak(path, '20260102-010101-000001', 500),
        _bak(path, '20260103-010101-000001', 400),
    ]
    dst = None
    with sm._lock:
        dst = sm._backup_before_migration()
    assert dst is not None and dst.exists(), 'the backup just written was pruned'
    left = sorted(p.name for p in path.parent.glob('settings.json.bak-*'))
    assert len(left) == SettingsManager.MIGRATION_BACKUPS
    assert dst.name in left
    survivors = {p.name for p in old if p.exists()}
    assert survivors == {old[5].name, old[4].name, old[3].name, old[2].name}   # the newest four of the old ones


def test_only_the_oldest_beyond_the_limit_go(tmp_path):
    sm, path = _manager(tmp_path)
    names = ['pre-a', '20260101-000000-000000', 'pre-b', '20260102-000000-000000']
    files = [_bak(path, n, 1000 - i * 100) for i, n in enumerate(names)]
    with sm._lock:
        dst = sm._backup_before_migration()
    assert dst.exists()
    assert all(f.exists() for f in files)            # 4 old + 1 new = 5, nothing to prune


def test_mtime_ties_never_delete_the_new_backup(tmp_path):
    sm, path = _manager(tmp_path)
    for i in range(7):
        f = path.with_name(f'{path.name}.bak-pre-{i}')
        f.write_text('{}')
        os.utime(f, (2_000_000_000, 2_000_000_000))   # as new as (or newer than) anything just written
    with sm._lock:
        dst = sm._backup_before_migration()
    assert dst.exists()
    assert len(list(path.parent.glob('settings.json.bak-*'))) == SettingsManager.MIGRATION_BACKUPS


def test_below_the_limit_nothing_is_pruned(tmp_path):
    sm, path = _manager(tmp_path)
    files = [_bak(path, n, 500 - i * 100) for i, n in enumerate(['pre-a', '20260101-000000-000000', 'pre-b'])]
    with sm._lock:
        dst = sm._backup_before_migration()
    assert dst.exists() and all(f.exists() for f in files)
