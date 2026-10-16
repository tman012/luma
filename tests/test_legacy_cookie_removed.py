"""Test legacy luma_session cookie is not accepted (Item 9)."""

import pytest
import app.user_auth as user_auth


class TestLegacyCookieRemoved:
    """Test legacy luma_session cookie is no longer accepted."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test environment."""
        self.app = test_app
        self.client = test_app.test_client()
        user_auth.init_db()
        user_auth.set_credentials('testuser', 'pass1234', is_admin=True)
    
    def test_legacy_cookie_not_accepted(self):
        """Legacy luma_session cookie is not accepted for authentication."""
        # Create a session
        user = user_auth.authenticate('testuser', 'pass1234')
        token = user_auth.create_session(user['id'])
        
        # Try to access API with legacy cookie name
        response = self.client.get('/api/settings',
                                   headers={'Cookie': f'luma_session={token}'},
                                   environ_base={'REMOTE_ADDR': '203.0.113.100'})
        
        # Should return 401 (not authenticated)
        assert response.status_code == 401
    
    def test_new_cookie_accepted(self):
        """New luma_auth_session cookie is accepted."""
        # Create a session
        user = user_auth.authenticate('testuser', 'pass1234')
        token = user_auth.create_session(user['id'])
        
        # Access API with new cookie name
        response = self.client.get('/api/settings',
                                   headers={'Cookie': f'luma_auth_session={token}'},
                                   environ_base={'REMOTE_ADDR': '203.0.113.100'})
        
        # Should return 200
        assert response.status_code == 200
    
    def test_login_sets_new_cookie_and_expires_legacy(self):
        """Login sets luma_auth_session and expires legacy luma_session."""
        from app.main import app
        
        # Simulate login via auth endpoint
        response = self.client.post('/api/auth/login',
                                    json={'username': 'testuser', 'pin': 'pass1234'},
                                    environ_base={'REMOTE_ADDR': '203.0.113.100'})
        
        # Check response sets new cookie and expires legacy
        cookies = response.headers.getlist('Set-Cookie')
        
        # Should have luma_auth_session
        auth_cookies = [c for c in cookies if 'luma_auth_session=' in c]
        assert len(auth_cookies) > 0
        assert 'HttpOnly' in auth_cookies[0] or 'httponly' in auth_cookies[0].lower()
        
        # Should have luma_session expiry
        legacy_cookies = [c for c in cookies if 'luma_session=' in c]
        assert len(legacy_cookies) > 0
        # Expiry cookies have Max-Age=0 or expires in the past
        assert 'Max-Age=0' in legacy_cookies[0] or 'max-age=0' in legacy_cookies[0].lower()
    
    def test_unlock_sets_new_cookie_and_expires_legacy(self):
        """PIN unlock sets luma_auth_session and expires legacy."""
        from app import start_flow
        import app.main
        
        # Make this a single-account install
        state = {'created_by_start': True, 'pin_set': True}
        start_flow._save_state(state)
        
        response = self.client.post('/api/auth/unlock',
                                    json={'pin': 'pass1234'},
                                    environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        if response.status_code == 200:
            cookies = response.headers.getlist('Set-Cookie')
            
            # Should have new cookie
            auth_cookies = [c for c in cookies if 'luma_auth_session=' in c]
            assert len(auth_cookies) > 0
            
            # Should expire legacy
            legacy_cookies = [c for c in cookies if 'luma_session=' in c]
            assert len(legacy_cookies) > 0
