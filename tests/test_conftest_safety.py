"""The destructive per-test reset only ever touches the sandbox database."""
import os
import sqlite3
import tempfile

import pytest

import conftest


def test_reset_refuses_a_database_outside_the_sandbox(tmp_path):
    for path in ('/app/data/media.db', os.path.expanduser('~/media.db'), str(tmp_path / 'media.db')):
        with pytest.raises(conftest.UnsafeDatabaseReset):
            conftest.sandbox_db_path(path)


def test_reset_refuses_a_symlink_escaping_the_sandbox(tmp_path):
    outside = tmp_path / 'real.db'
    outside.write_bytes(b'')
    link = os.path.join(conftest._SANDBOX['data'], 'escape.db')
    os.symlink(outside, link)
    try:
        with pytest.raises(conftest.UnsafeDatabaseReset):
            conftest.sandbox_db_path(link)
    finally:
        os.unlink(link)


def test_reset_refuses_when_sandbox_inactive(monkeypatch):
    monkeypatch.setitem(conftest._SANDBOX, 'data', None)
    with pytest.raises(conftest.UnsafeDatabaseReset):
        conftest.sandbox_db_path(os.path.join(tempfile.gettempdir(), 'x.db'))


def test_wipe_runs_no_sql_on_a_foreign_database(tmp_path, monkeypatch):
    foreign = tmp_path / 'prod.db'
    with sqlite3.connect(foreign) as c:
        c.execute('CREATE TABLE users (id INTEGER)')
        c.execute('CREATE TABLE user_sessions (id INTEGER)')
        c.execute('INSERT INTO users VALUES (1)')
    import app.user_auth
    monkeypatch.setattr(app.user_auth, '_get_db_path', lambda: str(foreign))
    with pytest.raises(conftest.UnsafeDatabaseReset):
        conftest._wipe_users(lambda: sqlite3.connect(foreign))
    with sqlite3.connect(foreign) as c:
        assert c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1


def test_sandbox_database_is_allowed():
    import app.user_auth
    assert conftest.sandbox_db_path() == os.path.realpath(app.user_auth._get_db_path())


def test_e2e_items_are_recognized():
    class Item:
        fspath = conftest._E2E_DIR / 'test_x.py'
    assert conftest._is_e2e_item(Item())
    Item.fspath = conftest._REPO_ROOT / 'tests' / 'test_x.py'
    assert not conftest._is_e2e_item(Item())
