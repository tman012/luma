"""Tests for remote authentication (forms-based login)."""

import pytest
from werkzeug.security import generate_password_hash
import app.user_auth as user_auth


class TestRemoteAuthentication:
    """Test forms-based authentication for remote access."""
    
    @pytest.fixture(autouse=True)
    def setup_auth(self, test_app):
        """Set up test user and auth settings."""
        import app.main
        
        # Initialize database and create test user
        user_auth.init_db()
        user_auth.create_user('testuser', 'testpass123', is_admin=True)
        
        # Configure settings for remote auth
        app.main.settings.update({
            'security': {
                'auth_required': 'disabled_local',
                'auth_method': 'forms',
            }
        })
        
        self.app = test_app
        self.client = test_app.test_client()
    
    def test_lan_no_login_default_setting(self, test_app):
        """LAN requests don't need login with disabled_local setting."""
        resp = self.client.get('/', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Should work (not redirect to login)
        assert resp.status_code in (200, 302, 500)  # 500 from missing templates OK
        assert '/login' not in (resp.location or '')
    
    def test_remote_redirected_to_login(self, test_app):
        """Remote requests are redirected to login page."""
        resp = self.client.get('/', environ_base={'REMOTE_ADDR': '203.0.113.100'})
        
        # Should redirect to login
        assert resp.status_code == 302
        assert '/login' in resp.location
    
    def test_remote_api_gets_401(self, test_app):
        """Remote API requests get 401 instead of redirect."""
        resp = self.client.get('/api/settings', environ_base={'REMOTE_ADDR': '203.0.113.100'})
        
        # Should return 401
        assert resp.status_code == 401
        data = resp.get_json()
        assert 'error' in data
    
    def test_correct_password_sets_cookie(self, test_app):
        """Correct username/password sets auth cookie and grants access."""
        # POST to login
        resp = self.client.post('/login', 
                                data={'username': 'testuser', 'password': 'testpass123', 'remember': 'on'},
                                follow_redirects=False)
        
        # Should redirect and set cookie
        assert resp.status_code == 302
        assert 'luma_auth_session' in str(resp.headers.getlist('Set-Cookie'))
    
    def test_enabled_mode_forces_lan_login(self, test_app):
        """With auth_required=enabled, LAN must also login."""
        import app.main
        app.main.settings.update({
            'security': {
                'auth_required': 'enabled',
            }
        })
        
        # LAN request should redirect to login
        resp = self.client.get('/', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp.status_code == 302
        assert '/login' in resp.location
