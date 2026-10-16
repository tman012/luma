"""Tests for API CSRF protection (Batch B item 5b)."""

import pytest
import app.user_auth as user_auth


class TestAPICsrfProtection:
    """Test same-origin check for cookie-authenticated API POST/PUT/PATCH/DELETE."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test user and auth."""
        import app.main
        
        user_auth.init_db()
        
        # Create test user
        existing = user_auth.get_user_by_name('testuser')
        if not existing:
            user_auth.create_user('testuser', 'testpass123', is_admin=True)
        
        app.main.settings.update({
            'security': {
                'auth_required': 'disabled_local',
            }
        })
        
        self.client = test_app.test_client()
    
    def _login(self):
        """Helper to log in and get authenticated session."""
        # Get CSRF token
        get_resp = self.client.get('/login')
        import re
        csrf_match = re.search(r'<input type="hidden" name="csrf_token" value="([^"]+)"', get_resp.data.decode())
        csrf_token = csrf_match.group(1) if csrf_match else ''
        
        # Login
        self.client.post('/login',
                        data={'username': 'testuser', 'password': 'testpass123', 'csrf_token': csrf_token},
                        base_url='https://localhost',
                        environ_base={'REMOTE_ADDR': '192.168.1.100'})
    
    def test_api_post_with_origin_succeeds(self, test_app):
        """API POST with valid Origin header succeeds."""
        self._login()
        
        resp = self.client.post('/api/settings',
                               json={'test': 'data'},
                               headers={'Origin': 'https://localhost', 'Host': 'localhost'},
                               base_url='https://localhost')
        
        # Should not be blocked by CSRF (may fail for other reasons)
        assert resp.status_code != 403
    
    def test_api_post_with_referer_succeeds(self, test_app):
        """API POST with valid Referer header succeeds."""
        self._login()
        
        resp = self.client.post('/api/settings',
                               json={'test': 'data'},
                               headers={'Referer': 'https://localhost/settings', 'Host': 'localhost'},
                               base_url='https://localhost')
        
        # Should not be blocked by CSRF
        assert resp.status_code != 403
    
    def test_api_post_without_origin_or_referer_blocked(self, test_app):
        """API POST without Origin or Referer is blocked."""
        self._login()
        
        resp = self.client.post('/api/settings',
                               json={'test': 'data'},
                               headers={'Host': 'localhost'},
                               base_url='https://localhost')
        
        # Should be blocked by CSRF
        assert resp.status_code == 403
        data = resp.get_json()
        assert 'CSRF' in data['error']
    
    def test_api_post_with_wrong_origin_blocked(self, test_app):
        """API POST with wrong Origin is blocked."""
        self._login()
        
        resp = self.client.post('/api/settings',
                               json={'test': 'data'},
                               headers={'Origin': 'https://evil.example', 'Host': 'localhost'},
                               base_url='https://localhost')
        
        # Should be blocked by CSRF
        assert resp.status_code == 403
        data = resp.get_json()
        assert 'CSRF' in data['error']
    
    def test_api_put_requires_csrf(self, test_app):
        """API PUT also requires Origin/Referer."""
        self._login()
        
        # Without Origin/Referer
        resp = self.client.put('/api/settings',
                              json={'test': 'data'},
                              headers={'Host': 'localhost'},
                              base_url='https://localhost')
        
        assert resp.status_code == 403
    
    def test_api_patch_requires_csrf(self, test_app):
        """API PATCH also requires Origin/Referer."""
        self._login()
        
        # Without Origin/Referer
        resp = self.client.patch('/api/settings',
                                json={'test': 'data'},
                                headers={'Host': 'localhost'},
                                base_url='https://localhost')
        
        assert resp.status_code == 403
    
    def test_api_delete_requires_csrf(self, test_app):
        """API DELETE also requires Origin/Referer."""
        self._login()
        
        # Without Origin/Referer
        resp = self.client.delete('/api/bot/remove/123',
                                 headers={'Host': 'localhost'},
                                 base_url='https://localhost')
        
        assert resp.status_code == 403
    
    def test_api_get_not_checked(self, test_app):
        """API GET is not subject to CSRF check."""
        self._login()
        
        # GET without Origin/Referer
        resp = self.client.get('/api/settings',
                              headers={'Host': 'localhost'},
                              base_url='https://localhost')
        
        # Should not be blocked by CSRF (200 or other, but not 403)
        assert resp.status_code != 403
    
    def test_implicit_user_not_checked(self, test_app):
        """LAN implicit user (kiosk mode) not subject to CSRF check."""
        # Don't login - rely on implicit owner for local requests
        # with disabled_local setting
        
        resp = self.client.post('/api/settings',
                               json={'test': 'data'},
                               headers={'Host': 'localhost'},
                               environ_base={'REMOTE_ADDR': '192.168.1.100'},
                               base_url='http://localhost')
        
        # Should not be blocked by CSRF
        # (may fail for other reasons, but not 403 CSRF)
        assert resp.status_code != 403
    
    def test_unauthenticated_not_checked(self, test_app):
        """Unauthenticated requests not subject to CSRF check."""
        # Don't login, and use remote address (no implicit owner)
        
        resp = self.client.post('/api/settings',
                               json={'test': 'data'},
                               headers={'Host': 'localhost'},
                               environ_base={'REMOTE_ADDR': '203.0.113.100'},
                               base_url='https://localhost')
        
        # Should get 401 (not authenticated), not 403 (CSRF)
        assert resp.status_code == 401
    
    def test_request_portal_not_checked(self, test_app):
        """Request portal endpoints not subject to API CSRF check."""
        # They have their own CSRF protection
        self._login()
        
        resp = self.client.post('/api/request/unlock',
                               json={'token': 'test'},
                               headers={'Host': 'localhost'},
                               base_url='https://localhost')
        
        # Should not be blocked by API CSRF (may fail for other reasons)
        assert resp.status_code != 403 or 'CSRF' not in resp.get_json().get('error', '')
    
    def test_api_key_auth_not_checked(self, test_app):
        """API key authentication not subject to CSRF check."""
        import app.main
        
        # Set up API key
        app.main._SERVICE_API_KEY = 'test-key-123'
        
        resp = self.client.post('/api/settings',
                               json={'test': 'data'},
                               headers={'X-API-Key': 'test-key-123', 'Host': 'localhost'},
                               base_url='https://localhost')
        
        # Should not be blocked by CSRF
        assert resp.status_code != 403
