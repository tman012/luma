"""v320: media.downloads_root settings win over LUMA_DOWNLOADS_ROOT for import/watch."""
import os

import pytest

from app import file_management as fm
from app import storage_cleanup as sc


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv('LUMA_DOWNLOADS_ROOT', raising=False)


def _fake_settings(monkeypatch, value):
    class Fake:
        def get(self, k, default=None):
            if k == 'media.downloads_root':
                return value
            return default
    monkeypatch.setattr('app.settings_manager.SettingsFile', Fake)


def test_settings_absolute_wins_over_env(monkeypatch, tmp_path, no_env):
    chosen = tmp_path / 'from-settings'
    chosen.mkdir()
    env_dir = tmp_path / 'from-env'
    env_dir.mkdir()
    _fake_settings(monkeypatch, str(chosen))
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(env_dir))
    assert fm.downloads_root() == os.path.realpath(chosen)
    assert sc._media_root() == os.path.realpath(chosen)


def test_empty_settings_falls_back_to_env(monkeypatch, tmp_path, no_env):
    env_dir = tmp_path / 'from-env'
    env_dir.mkdir()
    _fake_settings(monkeypatch, '')
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(env_dir))
    assert fm.downloads_root() == os.path.realpath(env_dir)
    assert sc._media_root() == os.path.realpath(env_dir)


def test_missing_settings_falls_back_to_env(monkeypatch, tmp_path, no_env):
    env_dir = tmp_path / 'from-env'
    env_dir.mkdir()
    _fake_settings(monkeypatch, None)
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(env_dir))
    assert fm.downloads_root() == os.path.realpath(env_dir)


def test_empty_settings_and_env_default_slash_downloads(monkeypatch, no_env):
    _fake_settings(monkeypatch, '   ')
    assert fm.downloads_root() == os.path.realpath('/downloads')


def test_relative_settings_ignored_uses_env(monkeypatch, tmp_path, no_env):
    env_dir = tmp_path / 'from-env'
    env_dir.mkdir()
    _fake_settings(monkeypatch, 'relative/dir')
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(env_dir))
    assert fm.downloads_root() == os.path.realpath(env_dir)


def test_library_mover_and_remote_container_root_use_resolver(monkeypatch, tmp_path, no_env):
    chosen = tmp_path / 'settings-root'
    chosen.mkdir()
    _fake_settings(monkeypatch, str(chosen))
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(tmp_path / 'env-root'))
    from app import library_mover as lm
    from app import remote_mover as rm
    assert lm._downloads_root() == os.path.realpath(chosen)
    assert rm._container_downloads_root() == (os.path.realpath(chosen).rstrip('/') or '/downloads')


def test_remote_host_map_env_untouched(monkeypatch):
    """LUMA_REMOTE_DOWNLOADS_ROOT stays the .72 host map — not media.downloads_root."""
    monkeypatch.setenv('LUMA_REMOTE_DOWNLOADS_ROOT', '/scratch/elsewhere')
    from app import remote_mover as rm
    assert rm.ENV_DOWNLOADS == 'LUMA_REMOTE_DOWNLOADS_ROOT'
    assert rm.config()['downloads_root'] == '/scratch/elsewhere'
