"""Tests for settings migrations (Item 5)."""

import pytest


class TestMigrations:
    """Test settings migrations."""
    
    def test_migrate_remove_remote_lock(self):
        """Migration removes security.remote_lock setting."""
        from app.settings_manager import _migrate_remove_remote_lock
        
        data = {
            'security': {
                'access_mode': 'open',
                'remote_lock': 'enforce',  # Should be removed
                'trusted_proxies': '',
            }
        }
        
        changed = _migrate_remove_remote_lock(data)
        
        assert changed is True
        assert 'remote_lock' not in data['security']
        assert 'access_mode' in data['security']  # Other settings preserved
    
    def test_migrate_remove_remote_lock_missing_section(self):
        """Migration handles missing security section."""
        from app.settings_manager import _migrate_remove_remote_lock
        
        data = {'other_section': {}}
        
        changed = _migrate_remove_remote_lock(data)
        
        assert changed is False
    
    def test_migrate_remove_remote_lock_already_removed(self):
        """Migration handles already-removed setting."""
        from app.settings_manager import _migrate_remove_remote_lock
        
        data = {
            'security': {
                'access_mode': 'open',
                'trusted_proxies': '',
            }
        }
        
        changed = _migrate_remove_remote_lock(data)
        
        assert changed is False
