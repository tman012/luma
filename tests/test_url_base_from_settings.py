"""Test URL base resolution from settings (Item 3)."""

import pytest
import os
from unittest.mock import Mock


class TestURLBaseFromSettings:
    """Test URL base is resolved from settings at startup."""
    
    def test_url_base_from_settings_only(self, test_app, monkeypatch):
        """URL base is resolved from settings, not env var."""
        # Create a mock settings that returns a URL base
        from app.settings_manager import SettingsManager
        original_get = SettingsManager.get
        
        def mock_get(self, key, default=None):
            if key == 'general.url_base':
                return '/luma-test'
            return original_get(self, key, default)
        
        monkeypatch.setattr(SettingsManager, 'get', mock_get)
        
        # Clear env var
        monkeypatch.delenv('LUMA_URL_BASE', raising=False)
        
        # Reload app to pick up settings
        import importlib
        import app.main
        importlib.reload(app.main)
        
        # Check that URL_BASE config is set from settings
        from app.main import app
        assert app.config.get('URL_BASE') == '/luma-test'
    
    def test_manifest_uses_config_url_base(self, test_app):
        """Manifest.json uses app.config['URL_BASE']."""
        from app.main import app
        
        # Set URL base in config
        app.config['URL_BASE'] = '/test-base'
        
        client = test_app.test_client()
        response = client.get('/manifest.json')
        
        data = response.get_json()
        assert response.status_code == 200
        assert data['start_url'] == '/test-base'
        assert data['scope'] == '/test-base'
    
    def test_auth_routes_use_config_url_base(self, test_app):
        """Auth routes use app.config['URL_BASE'] for redirects."""
        from app.main import app
        import app.user_auth as user_auth
        
        # Set URL base in config
        app.config['URL_BASE'] = '/mybase'
        
        # Initialize DB and create a test user
        user_auth.init_db()
        user_auth.set_credentials('testuser', 'testpass123', is_admin=True)
        
        client = test_app.test_client()
        
        # Try to access protected API without auth (remote)
        response = client.get('/api/settings', environ_base={'REMOTE_ADDR': '203.0.113.100'}, follow_redirects=False)
        
        # Should redirect to login with URL base prefix
        assert response.status_code == 302
        location = response.headers.get('Location', '')
        assert location.startswith('/mybase/login')
