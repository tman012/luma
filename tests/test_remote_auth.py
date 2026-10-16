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
        
        # Create test user if doesn't exist
        existing = user_auth.get_user_by_name('testuser')
        if not existing:
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


class TestAuthenticationDetails:
    """Test detailed authentication behavior."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test environment."""
        import app.main
        import app.user_auth as user_auth
        
        user_auth.init_db()
        existing = user_auth.get_user_by_name('testuser')
        if not existing:
            user_auth.create_user('testuser', 'testpass123', is_admin=True)
        
        app.main.settings.update({
            'security': {
                'auth_required': 'disabled_local',
            }
        })
        
        self.client = test_app.test_client()
    
    def test_wrong_password_rejected(self, test_app):
        """Wrong password is rejected."""
        resp = self.client.post('/login',
                                data={'username': 'testuser', 'password': 'wrongpass'},
                                environ_base={'REMOTE_ADDR': '203.0.113.100'},
                                follow_redirects=False)
        
        # Should redirect back to login with error
        assert resp.status_code == 302
        assert '/login' in resp.location
        assert 'error=' in resp.location
    
    def test_sixth_attempt_rate_limited(self, test_app):
        """6th failed attempt within 5 minutes is rate limited."""
        # Make 5 failed attempts
        for i in range(5):
            self.client.post('/login',
                           data={'username': 'testuser', 'password': 'wrongpass'},
                           environ_base={'REMOTE_ADDR': '203.0.113.200'})
        
        # 6th attempt should be rate limited
        resp = self.client.post('/login',
                                data={'username': 'testuser', 'password': 'testpass123'},
                                environ_base={'REMOTE_ADDR': '203.0.113.200'},
                                follow_redirects=False)
        
        assert resp.status_code == 302
        assert 'rate_limited=1' in resp.location or 'Too many' in resp.location
    
    def test_no_credentials_remote_blocked_lan_works(self, test_app):
        """With no credentials, remote can't authenticate but LAN works."""
        import app.user_auth as ua
        
        # Delete all users
        conn = ua._get_db()
        conn.execute('DELETE FROM users')
        conn.commit()
        
        # Remote should redirect to login
        resp = self.client.get('/', environ_base={'REMOTE_ADDR': '203.0.113.100'})
        assert resp.status_code == 302
        assert '/login' in resp.location
        
        # LAN should work
        resp = self.client.get('/', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp.status_code in (200, 302, 500)
        assert '/login' not in (resp.location or '')
    
    def test_logout_clears_session(self, test_app):
        """Logout clears the session."""
        # Login first
        self.client.post('/login',
                        data={'username': 'testuser', 'password': 'testpass123'},
                        environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Logout
        resp = self.client.post('/logout', follow_redirects=False)
        
        # Should redirect and clear cookie
        assert resp.status_code == 302
        cookies = resp.headers.getlist('Set-Cookie')
        assert any('luma_auth_session=' in c and ('Max-Age=0' in c or 'expires=' in c.lower()) 
                  for c in cookies)
    
    def test_portal_host_404_on_login_and_main(self, test_app):
        """Portal hostname gets 404 on /login and main app."""
        import app.main
        app.main.settings.update({
            'request_portal': {
                'enabled': True,
                'hostname': 'requests.example.com',
            }
        })
        
        # Portal host trying to access login
        resp = self.client.get('/login', headers={'Host': 'requests.example.com'})
        assert resp.status_code == 404
        
        # Portal host trying to access main app
        resp = self.client.get('/', headers={'Host': 'requests.example.com'})
        assert resp.status_code == 404
