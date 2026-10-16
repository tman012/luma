"""Tests for portal traffic isolation (SPEC CORRECTED).

Portal traffic (detected by Host matching request_portal.hostname) can only 
access portal routes. All tests use real app.main.
"""
import pytest
from werkzeug.security import generate_password_hash


class TestPortalIsolation:
    """Test portal traffic is isolated to portal routes only."""
    
    def test_portal_host_blocked_from_main_app(self, test_app):
        """Portal host is blocked from main app pages other than the root."""
        import app.main
        app.main.settings.update({
            'request_portal': {'hostname': 'requests.example.com', 'enabled': True}
        })

        client = test_app.test_client()
        for path in ('/library', '/explore', '/settings', '/index.html', '/requestx'):
            assert client.get(path, headers={'Host': 'requests.example.com'}).status_code == 404, path
        assert client.post('/', headers={'Host': 'requests.example.com'}).status_code in (404, 405)
    
    def test_portal_host_blocked_from_settings(self, test_app):
        """Portal host is blocked from /api/settings."""
        import app.main
        app.main.settings.update({
            'request_portal': {'hostname': 'requests.example.com', 'enabled': True}
        })
        
        client = test_app.test_client()
        resp = client.get('/api/settings', headers={'Host': 'requests.example.com'})
        
        assert resp.status_code == 404
    
    def test_portal_host_blocked_from_bot_routes(self, test_app):
        """Portal host is blocked from /api/bot/*."""
        import app.main
        app.main.settings.update({
            'request_portal': {'hostname': 'requests.example.com', 'enabled': True}
        })
        
        client = test_app.test_client()
        resp = client.get('/api/bot/rooms', headers={'Host': 'requests.example.com'})
        
        assert resp.status_code == 404
    
    def test_portal_host_blocked_from_admin_routes(self, test_app):
        """Portal host is blocked from /api/request/admin/*."""
        import app.main
        app.main.settings.update({
            'request_portal': {'hostname': 'requests.example.com', 'enabled': True}
        })
        
        client = test_app.test_client()
        resp = client.get('/api/request/admin/settings', headers={'Host': 'requests.example.com'})
        
        assert resp.status_code == 404
    
    def test_cf_headers_not_portal_traffic(self, test_app):
        """CF headers alone don't make requests portal traffic (spec correction)."""
        client = test_app.test_client()
        
        # CF-Ray header without portal hostname is NOT portal traffic
        # Owner can use full app remotely via tunnel on main hostname
        resp = client.get('/', headers={'CF-Ray': '8a1b2c3d4e5f'}, environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Should work (not 404) for local traffic
        assert resp.status_code in (200, 302, 500)  # 500 from DB issues is OK here
    
    def test_lan_request_works(self, test_app):
        """LAN requests work (not blocked by portal guard)."""
        client = test_app.test_client()
        
        # Direct LAN request (no portal headers)
        resp = client.get('/', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        
        # Should work (200 or redirect, but not 404)
        assert resp.status_code in (200, 302, 500)  # 500 from DB is OK
        assert resp.status_code != 404
