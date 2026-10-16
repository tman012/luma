"""Tests never reach the real /app/data, and telegram_bot.json permissions are checked."""
import logging
import os

from app import paths


def test_legacy_data_dir_is_sandboxed():
    assert paths.LEGACY_DATA_DIR != '/app/data'
    assert not paths.LEGACY_DATABASE_PATH.startswith('/app/data')
    assert not str(paths.telegram_bot_config_path()).startswith('/app/data')


def _setup(tmp_path, monkeypatch, mode):
    data = tmp_path / 'data'
    data.mkdir()
    monkeypatch.setenv('LUMA_DATA_DIR', str(data))
    monkeypatch.setattr(paths, 'LEGACY_DATA_DIR', str(tmp_path / 'legacy'))
    f = paths.telegram_bot_config_path()
    f.write_text('{}')
    os.chmod(f, mode)
    return f


def test_warns_when_group_or_world_readable(tmp_path, monkeypatch, caplog):
    for mode in (0o640, 0o604, 0o644):
        sub = tmp_path / oct(mode)
        sub.mkdir()
        _setup(sub, monkeypatch, mode)
        with caplog.at_level(logging.WARNING, logger='app.paths'):
            assert paths.warn_if_telegram_config_exposed() is True
        assert 'chmod 600' in caplog.text
        caplog.clear()


def test_quiet_when_private_or_missing(tmp_path, monkeypatch, caplog):
    f = _setup(tmp_path, monkeypatch, 0o600)
    with caplog.at_level(logging.WARNING, logger='app.paths'):
        assert paths.warn_if_telegram_config_exposed() is False
        f.unlink()
        assert paths.warn_if_telegram_config_exposed() is False
    assert caplog.text == ''
