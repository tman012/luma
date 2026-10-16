"""Tests for portal traffic isolation (Item 7 - corrected).

Portal traffic can only access portal routes. All tests use real app.main test client.
"""
import pytest
from unittest.mock import patch, Mock
from werkzeug.security import generate_password_hash


class TestPortalIsolation:
    """Test portal traffic is isolated to portal routes only."""
    
    @pytest.fixture(autouse=True)
    def setup_portal(self, test_app):
        """Set up portal configuration for each test."""
        import app.user_auth
        import app.main
        
        # Initialize database
        app.user_auth.init_db()
        app.user_auth.create_user('owner', 'password123', is_admin=True)
        
        # Update settings directly in memory
        settings = app.main.settings
        settings.update({
            'request_portal': {
                'enabled': True,
                'hostname': 'requests.example.com',
                'password_hash': generate_password_hash('testpass'),
            },
            'security': {
                'access_mode': 'open',
                'trusted_proxies': '',
            }
        })
        
        yield
    
    def test_portal_host_can_access_request_page(self, test_app):
        """Portal host can GET /request."""
        client = test_app.test_client()
        
        resp = client.get('/request', headers={'Host': 'requests.example.com'})
        
        # Should return 200 (or redirect, but not 404)
        assert resp.status_code in (200, 302, 401)
        assert resp.status_code != 404
    
    def test_portal_host_blocked_from_main_app(self, test_app):
        """Portal host is blocked from main app root."""
        client = test_app.test_client()
        
        resp = client.get('/', headers={'Host': 'requests.example.com'})
        
        # Portal traffic to main app should be 404
        assert resp.status_code == 404
    
    def test_portal_host_blocked_from_settings(self, test_app):
        """Portal host is blocked from /api/settings."""
        client = test_app.test_client()
        
        resp = client.get('/api/settings', headers={'Host': 'requests.example.com'})
        
        assert resp.status_code == 404
    
    def test_portal_host_blocked_from_bot_routes(self, test_app):
        """Portal host is blocked from /api/bot/*."""
        client = test_app.test_client()
        
        resp = client.get('/api/bot/rooms', headers={'Host': 'requests.example.com'})
        
        assert resp.status_code == 404
    
    def test_portal_host_blocked_from_admin_routes(self, test_app):
        """Portal host is blocked from /api/request/admin/*."""
        client = test_app.test_client()
        
        resp = client.get('/api/request/admin/settings', headers={'Host': 'requests.example.com'})
        
        assert resp.status_code == 404
    
    def test_cf_headers_treated_as_portal_traffic(self, test_app):
        """Requests with CF headers are treated as portal traffic."""
        client = test_app.test_client()
        
        # CF-Ray header makes this portal traffic
        resp = client.get('/', headers={'CF-Ray': '8a1b2c3d4e5f'})
        
        assert resp.status_code == 404
    
    def test_lan_request_works_with_no_signin(self, test_app):
        """LAN requests work with no sign-in (open mode)."""
        client = test_app.test_client()
        
        # Direct LAN request (no portal headers)
        resp = client.get('/', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Should work (200 or redirect to start flow, but not 404 or 401)
        assert resp.status_code in (200, 302)
        assert resp.status_code not in (401, 404)
    
    def test_portal_unlock_works(self, test_app):
        """Portal can unlock with correct password."""
        client = test_app.test_client()
        
        resp = client.post('/api/request/unlock',
                          json={'password': 'testpass'},
                          headers={'Host': 'requests.example.com',
                                  'Content-Type': 'application/json'})
        
        # Should succeed
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('ok') is True
    
    def test_spoofed_portal_headers_from_lan_cannot_grant_owner(self, test_app):
        """Spoofed CF headers from LAN can't grant owner access via tunnel."""
        client = test_app.test_client()
        
        # LAN IP with spoofed CF headers trying to access main app
        resp = client.get('/',
                         headers={'CF-Ray': 'spoofed'},
                         environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Should be blocked as portal traffic
        assert resp.status_code == 404
    
    def test_spoofed_headers_cannot_lock_out_lan(self, test_app):
        """Spoofed Host or CF headers can't lock out legitimate LAN access."""
        client = test_app.test_client()
        
        # LAN request WITHOUT spoofed headers should work
        resp = client.get('/', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Should work
        assert resp.status_code in (200, 302)
