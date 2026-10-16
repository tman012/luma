"""telegram_bot.json follows LUMA_DATA_DIR, with the same keep-the-existing-file
rule as the database."""
import json

import pytest

from app import paths


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    data, legacy = tmp_path / 'data', tmp_path / 'legacy'
    data.mkdir(); legacy.mkdir()
    monkeypatch.setenv('LUMA_DATA_DIR', str(data))
    monkeypatch.delenv('LUMA_STATE_DIR', raising=False)
    monkeypatch.setattr(paths, 'LEGACY_DATA_DIR', str(legacy))
    paths._file_logged.clear()
    return data, legacy


def test_fresh_install_uses_data_dir(dirs):
    data, _ = dirs
    assert paths.telegram_bot_config_path() == data / 'telegram_bot.json'


def test_existing_legacy_file_kept_and_logged(dirs, caplog):
    data, legacy = dirs
    (legacy / 'telegram_bot.json').write_text('{}')
    with caplog.at_level('WARNING'):
        assert paths.telegram_bot_config_path() == legacy / 'telegram_bot.json'
    msg = ' '.join(r.getMessage() for r in caplog.records)
    assert str(legacy / 'telegram_bot.json') in msg and str(data / 'telegram_bot.json') in msg


def test_data_dir_file_wins_when_both_exist(dirs):
    data, legacy = dirs
    (legacy / 'telegram_bot.json').write_text('{}')
    (data / 'telegram_bot.json').write_text('{}')
    assert paths.telegram_bot_config_path() == data / 'telegram_bot.json'


def test_readers_use_the_resolved_file(dirs, monkeypatch):
    data, _ = dirs
    (data / 'telegram_bot.json').write_text(json.dumps({'bot_token': 'T0K', 'admin_chat_id': '42'}))
    monkeypatch.delenv('TELEGRAM_ADMIN_CHAT_ID', raising=False)
    from app import pending_requests
    assert pending_requests._get_telegram_bot_token() == 'T0K'
    import app.main
    assert app.main._telegram_admin_chat_id() == '42'


def test_no_hard_coded_path_left():
    import inspect
    import app.main
    from app import pending_requests
    for mod in (app.main, pending_requests):
        assert "'/app/data/telegram_bot.json'" not in inspect.getsource(mod)
