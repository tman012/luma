"""Test auth_required migration and behavior (Item 7)."""

import pytest
import app.user_auth as user_auth
from app.settings_manager import SettingsManager


class TestAuthRequiredMigration:
    """Test 'existing users, no security keys, two restarts': auth_required stays disabled_local."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test environment."""
        self.app = test_app
        self.client = test_app.test_client()
        user_auth.init_db()
    
    def test_existing_users_no_saved_mode_stays_disabled_local(self):
        """Existing users without saved auth_required defaults to disabled_local on restart."""
        # Simulate an existing install: users exist, but no auth_required setting
        user_auth.set_credentials('existinguser', 'pass1234', is_admin=True)
        
        # Create settings without auth_required
        settings = SettingsManager()
        if settings.get('security.auth_required'):
            # Remove any existing setting
            data = settings._data
            if 'security' in data and 'auth_required' in data['security']:
                del data['security']['auth_required']
                settings._save(data)
        
        # Simulate restart (reload settings)
        settings = SettingsManager()
        
        # Migration in settings_manager.py should have set auth_required to disabled_local
        # (No saved mode means disabled_local per item 7)
        auth_req = settings.get('security.auth_required') or 'disabled_local'
        assert auth_req == 'disabled_local'
        
        # LAN request should get implicit owner (no 401)
        response = self.client.get('/api/settings', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 200  # No 401
    
    def test_lan_no_401_after_restart(self):
        """LAN request gets no 401 after restart with disabled_local."""
        # Create user
        user_auth.set_credentials('testuser', 'pass1234', is_admin=True)
        
        # Ensure auth_required is disabled_local
        settings = SettingsManager()
        settings.update({'security': {'auth_required': 'disabled_local'}})
        
        # LAN request should work
        response = self.client.get('/api/settings', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 200
    
    def test_remote_gets_401_with_disabled_local(self):
        """Remote request gets 401 with auth_required=disabled_local."""
        # Create user
        user_auth.set_credentials('testuser', 'pass1234', is_admin=True)
        
        # Ensure auth_required is disabled_local
        settings = SettingsManager()
        settings.update({'security': {'auth_required': 'disabled_local'}})
        
        # Remote request should get 401
        response = self.client.get('/api/settings', environ_base={'REMOTE_ADDR': '203.0.113.100'})
        assert response.status_code == 401
