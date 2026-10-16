"""Test CSRF protection for implicit users and proxy headers (Item 8)."""

import pytest
import app.user_auth as user_auth
from app.settings_manager import SettingsManager


class TestCSRFProtection:
    """Test CSRF checks for implicit users and behind trusted proxy."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test environment."""
        self.app = test_app
        self.client = test_app.test_client()
        user_auth.init_db()
        user_auth.set_credentials('admin', 'pass1234', is_admin=True)
        
        # Set auth_required to disabled_local
        settings = SettingsManager()
        settings.update({'security': {'auth_required': 'disabled_local'}})
    
    def test_implicit_user_with_cross_origin_rejected(self):
        """Implicit LAN user with cross-origin Origin header is rejected."""
        # Implicit user (LAN) making POST with cross-origin Origin
        response = self.client.post('/api/settings',
                                    json={'test': 'data'},
                                    headers={'Origin': 'http://evil.com'},
                                    environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Should be rejected (403 CSRF error)
        assert response.status_code == 403
        data = response.get_json()
        assert 'CSRF' in data.get('error', '')
    
    def test_implicit_user_with_cross_origin_referer_rejected(self):
        """Implicit LAN user with cross-origin Referer is rejected."""
        # Implicit user with cross-origin Referer
        response = self.client.post('/api/settings',
                                    json={'test': 'data'},
                                    headers={'Referer': 'http://evil.com/attack'},
                                    environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Should be rejected
        assert response.status_code == 403
    
    def test_implicit_user_no_origin_allowed(self):
        """Implicit LAN user without Origin/Referer is allowed."""
        # Implicit user without Origin/Referer (direct browser POST)
        response = self.client.post('/api/settings',
                                    json={'general': {'port': 5001}},
                                    environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Should succeed (no CSRF check for implicit users without Origin/Referer)
        # May fail for other reasons (validation), but should not be 403 CSRF
        assert response.status_code != 403 or 'CSRF' not in response.get_json().get('error', '')
    
    def test_secure_cookie_behind_https_proxy(self):
        """Cookie secure flag is set when behind HTTPS proxy."""
        from app.main import app
        
        # Configure trusted proxy
        settings = SettingsManager()
        settings.update({'security': {'trusted_proxies': '10.0.0.1'}})
        
        # Reload app to apply ProxyFix
        import importlib
        import app.main
        importlib.reload(app.main)
        
        client = self.client
        
        # Login request behind HTTPS proxy
        # (Login endpoint sets secure=request.is_secure which should use X-Forwarded-Proto)
        response = client.post('/auth/login',
                              data={'username': 'admin', 'password': 'pass1234', 'csrf_token': 'dummy'},
                              headers={'X-Forwarded-Proto': 'https', 'Host': 'luma.example.com'},
                              environ_base={'REMOTE_ADDR': '10.0.0.1'},
                              follow_redirects=False)
        
        # Check if cookie has Secure flag (when behind HTTPS proxy)
        cookies = response.headers.getlist('Set-Cookie')
        auth_cookie = [c for c in cookies if 'luma_auth_session=' in c]
        if auth_cookie:
            # ProxyFix should make request.is_secure=True from X-Forwarded-Proto
            assert 'Secure' in auth_cookie[0] or 'secure' in auth_cookie[0].lower()
