"""A fresh install ships with no trackers: zero indexers until an admin adds one."""
import pytest

pytestmark = pytest.mark.usefixtures('temp_data_dir')


def test_fresh_settings_have_zero_indexers():
    from app.settings_manager import SettingsManager, SETTINGS_SCHEMA
    assert SETTINGS_SCHEMA['indexer']['indexers']['default'] == []
    sm = SettingsManager()
    assert (sm.get('indexer.indexers') or []) == []


def test_fresh_manager_lists_zero_indexers():
    from app.settings_manager import SettingsManager
    from app.indexer_manager import IndexerManager
    mgr = IndexerManager(SettingsManager())
    assert mgr.list_indexers() == []
    assert mgr.get_enabled_indexers() == []
