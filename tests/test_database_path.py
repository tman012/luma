"""Database path: env wins, then a saved setting, then $LUMA_DATA_DIR/media.db,
but an existing database at the old default is never replaced by a new one."""

import json
import logging
import os
import sqlite3

import pytest

from app import paths


@pytest.fixture
def clean(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_PATH', raising=False)
    monkeypatch.setenv('LUMA_DATA_DIR', str(tmp_path / 'data'))
    paths._db_logged.clear()
    return tmp_path


def _legacy(monkeypatch, tmp_path, exists=True):
    legacy = tmp_path / 'old' / 'media.db'
    legacy.parent.mkdir(parents=True)
    if exists:
        sqlite3.connect(legacy).execute('CREATE TABLE marker (x)').connection.close()
    monkeypatch.setattr(paths, 'LEGACY_DATABASE_PATH', str(legacy))
    return legacy


def test_fresh_install_uses_data_dir(clean, monkeypatch):
    _legacy(monkeypatch, clean, exists=False)
    assert paths.resolve_database_path() == (clean / 'data' / 'media.db', 'data dir')


def test_existing_old_db_used_and_logged(clean, monkeypatch, caplog):
    legacy = _legacy(monkeypatch, clean)
    with caplog.at_level(logging.WARNING, logger='app.paths'):
        assert paths.database_path() == legacy
    msg = ' '.join(r.getMessage() for r in caplog.records)
    assert str(legacy) in msg and str(clean / 'data' / 'media.db') in msg
    assert not (clean / 'data' / 'media.db').exists()     # no new empty DB


def test_existing_db_in_data_dir_wins_over_old(clean, monkeypatch):
    _legacy(monkeypatch, clean)
    (clean / 'data').mkdir()
    (clean / 'data' / 'media.db').write_bytes(b'')
    assert paths.resolve_database_path()[1] == 'data dir'


def test_explicit_setting_wins(clean, monkeypatch):
    _legacy(monkeypatch, clean)
    assert paths.resolve_database_path('/srv/luma/custom.db') == (paths.Path('/srv/luma/custom.db'), 'setting')


def test_env_wins_over_setting(clean, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', '/env/media.db')
    assert paths.resolve_database_path('/srv/x.db') == (paths.Path('/env/media.db'), 'env')


def test_prod_shape_resolves_same_file(clean, monkeypatch):
    """Prod sets DATABASE_PATH=/app/data/media.db in compose: unchanged."""
    monkeypatch.setenv('DATABASE_PATH', '/app/data/media.db')
    assert str(paths.database_path()) == '/app/data/media.db'


def test_user_auth_and_export_follow_resolver(clean, monkeypatch):
    legacy = _legacy(monkeypatch, clean)
    from app import first_run, user_auth
    monkeypatch.setenv('LUMA_STATE_DIR', str(clean / 'data'))
    assert user_auth._get_db_path() == str(legacy)
    first_run._export_database_path({})
    assert os.environ['DATABASE_PATH'] == str(legacy)


def test_saved_setting_exported(clean, monkeypatch):
    from app import first_run
    first_run._export_database_path({'media': {'database_path': str(clean / 'pinned.db')}})
    assert os.environ['DATABASE_PATH'] == str(clean / 'pinned.db')


def test_schema_default_is_blank_and_config_has_no_fixed_path():
    from app.settings_manager import SETTINGS_SCHEMA
    assert SETTINGS_SCHEMA['media']['database_path']['default'] == ''
    import inspect
    from app import config
    assert "'/app/data/media.db'" not in inspect.getsource(config)


def test_atomic_write_json(tmp_path, monkeypatch):
    p = tmp_path / 'x.json'
    paths.atomic_write_json(p, {'a': 1})
    assert json.loads(p.read_text()) == {'a': 1}

    def fail(*a):
        raise OSError('full')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError):
        paths.atomic_write_json(p, {'a': 2})
    assert json.loads(p.read_text()) == {'a': 1}
    assert [f.name for f in tmp_path.iterdir()] == ['x.json']


def test_quarantine(tmp_path):
    p = tmp_path / 's.json'
    p.write_text('bad')
    b = paths.quarantine(p, 'test')
    assert b and b.name.startswith('s.json.bad-') and not p.exists()
