"""Tests for access_mode to auth_required migration."""

import pytest
import tempfile
import json
import os


class TestAccessModeMigration:
    """Test migration from access_mode to auth_required."""
    
    def test_pin_to_enabled(self):
        """Migrate pin -> enabled."""
        from app.settings_manager import _migrate_access_mode_to_auth_required
        
        data = {
            'security': {
                'access_mode': 'pin'
            }
        }
        
        result = _migrate_access_mode_to_auth_required(data)
        
        assert result is True
        assert data['security']['auth_required'] == 'enabled'
    
    def test_pin_remote_to_disabled_local(self):
        """Migrate pin_remote -> disabled_local."""
        from app.settings_manager import _migrate_access_mode_to_auth_required
        
        data = {
            'security': {
                'access_mode': 'pin_remote'
            }
        }
        
        result = _migrate_access_mode_to_auth_required(data)
        
        assert result is True
        assert data['security']['auth_required'] == 'disabled_local'
    
    def test_open_to_disabled_local(self):
        """Migrate open -> disabled_local (open mode deprecated)."""
        from app.settings_manager import _migrate_access_mode_to_auth_required
        
        data = {
            'security': {
                'access_mode': 'open'
            }
        }
        
        result = _migrate_access_mode_to_auth_required(data)
        
        assert result is True
        assert data['security']['auth_required'] == 'disabled_local'
    
    def test_existing_auth_required_kept_and_access_mode_deleted(self):
        """A saved auth_required wins; the retired access_mode key is deleted."""
        from app.settings_manager import _migrate_access_mode_to_auth_required
        
        data = {
            'security': {
                'access_mode': 'pin',
                'auth_required': 'disabled_local'
            }
        }
        
        result = _migrate_access_mode_to_auth_required(data)
        
        assert result is True
        assert data['security'] == {'auth_required': 'disabled_local'}
        # Second run: nothing left to do
        assert _migrate_access_mode_to_auth_required(data) is False
    
    def test_no_migration_when_no_access_mode(self):
        """No migration when access_mode doesn't exist."""
        from app.settings_manager import _migrate_access_mode_to_auth_required
        
        data = {
            'security': {}
        }
        
        result = _migrate_access_mode_to_auth_required(data)
        
        assert result is False
        assert 'auth_required' not in data['security']
    
    def test_unknown_mode_defaults_to_enabled(self):
        """Unknown access_mode values default to enabled for safety."""
        from app.settings_manager import _migrate_access_mode_to_auth_required
        
        data = {
            'security': {
                'access_mode': 'unknown_value'
            }
        }
        
        result = _migrate_access_mode_to_auth_required(data)
        
        assert result is True
        assert data['security']['auth_required'] == 'enabled'
    
    def test_empty_string_mode_deleted_without_auth_required(self):
        """An empty access_mode (the old schema default) is deleted, nothing else saved."""
        from app.settings_manager import _migrate_access_mode_to_auth_required
        
        data = {
            'security': {
                'access_mode': ''
            }
        }
        
        result = _migrate_access_mode_to_auth_required(data)
        
        assert result is True
        assert data['security'] == {}
    
    def test_migration_in_settings_manager(self):
        """Migration runs automatically when loading settings."""
        from app.settings_manager import SettingsManager
        
        # Create temp settings file with old format
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                'security': {
                    'access_mode': 'pin_remote'
                }
            }, f)
            temp_path = f.name
        
        try:
            # Load settings (should trigger migration)
            settings = SettingsManager(temp_path)
            
            # Check migration happened
            auth_required = settings.get('security.auth_required')
            assert auth_required == 'disabled_local'
            
            # Reload to verify persistence
            settings2 = SettingsManager(temp_path)
            auth_required2 = settings2.get('security.auth_required')
            assert auth_required2 == 'disabled_local'
            # The retired key is gone from memory and from the saved file
            assert settings2.get('security.access_mode') is None
            with open(temp_path) as fh:
                assert 'access_mode' not in json.load(fh).get('security', {})
        finally:
            os.unlink(temp_path)
    
    def test_all_three_modes_in_sequence(self):
        """Test all three modes migrate correctly."""
        from app.settings_manager import _migrate_access_mode_to_auth_required
        
        test_cases = [
            ('pin', 'enabled'),
            ('pin_remote', 'disabled_local'),
            ('open', 'disabled_local'),
        ]
        
        for old_mode, expected_new in test_cases:
            data = {
                'security': {
                    'access_mode': old_mode
                }
            }
            
            result = _migrate_access_mode_to_auth_required(data)
            
            assert result is True, f"Failed for mode: {old_mode}"
            assert data['security']['auth_required'] == expected_new, \
                f"Wrong mapping for {old_mode}: got {data['security']['auth_required']}, expected {expected_new}"
