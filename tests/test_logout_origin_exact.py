"""Test logout Origin validation uses exact scheme://netloc compare (Item 5)."""

import pytest
import app.user_auth as user_auth


class TestLogoutOriginValidation:
    """Test logout endpoint validates Origin/Referer exactly."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test user and client."""
        user_auth.init_db()
        user_auth.set_credentials('testuser', 'testpass123', is_admin=True)
        self.app = test_app
        self.client = test_app.test_client()
    
    def test_logout_with_valid_origin(self):
        """Logout succeeds with valid Origin."""
        # Create a session
        user = user_auth.authenticate('testuser', 'testpass123')
        token = user_auth.create_session(user['id'])
        
        # Make logout request with valid Origin
        response = self.client.post('/auth/logout',
                                    headers={
                                        'Origin': 'http://localhost',
                                        'Cookie': f'luma_auth_session={token}'
                                    },
                                    environ_base={'REMOTE_ADDR': '127.0.0.1'})
        
        assert response.status_code == 302  # Redirect to login
    
    def test_logout_blocks_subdomain_spoof(self):
        """Logout blocks http://127.0.0.1:5302.evil.com (startswith would pass)."""
        # Create a session
        user = user_auth.authenticate('testuser', 'testpass123')
        token = user_auth.create_session(user['id'])
        
        # Make logout request with spoofed Origin
        response = self.client.post('/auth/logout',
                                    headers={
                                        'Origin': 'http://127.0.0.1:5302.evil.com',
                                        'Cookie': f'luma_auth_session={token}'
                                    },
                                    environ_base={'REMOTE_ADDR': '127.0.0.1'})
        
        # Should be blocked (403)
        assert response.status_code == 403
        data = response.get_json()
        assert 'error' in data
    
    def test_logout_with_valid_referer(self):
        """Logout succeeds with valid Referer."""
        # Create a session
        user = user_auth.authenticate('testuser', 'testpass123')
        token = user_auth.create_session(user['id'])
        
        # Make logout request with valid Referer
        response = self.client.post('/auth/logout',
                                    headers={
                                        'Referer': 'http://localhost/settings',
                                        'Cookie': f'luma_auth_session={token}'
                                    },
                                    environ_base={'REMOTE_ADDR': '127.0.0.1'})
        
        assert response.status_code == 302  # Redirect to login
    
    def test_logout_blocks_cross_origin(self):
        """Logout blocks completely different origin."""
        # Create a session
        user = user_auth.authenticate('testuser', 'testpass123')
        token = user_auth.create_session(user['id'])
        
        # Make logout request from different origin
        response = self.client.post('/auth/logout',
                                    headers={
                                        'Origin': 'http://evil.com',
                                        'Cookie': f'luma_auth_session={token}'
                                    },
                                    environ_base={'REMOTE_ADDR': '127.0.0.1'})
        
        # Should be blocked
        assert response.status_code == 403
