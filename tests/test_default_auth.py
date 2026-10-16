"""Test that fresh install defaults to disabled_local auth."""

import pytest


class TestFreshInstallDefaults:
    """Test default auth behavior for fresh install."""
    
    def test_fresh_install_defaults_to_disabled_local(self, test_app):
        """Fresh install defaults to disabled_local: LAN no sign-in, remote must authenticate."""
        import app.main
        
        # Don't set any auth_required - test the default
        app.main.settings.update({
            'security': {}
        })
        
        client = test_app.test_client()
        
        # LAN request should NOT get 401 (auth guard should pass)
        # Use '/' which redirects but doesn't return 401
        resp = client.get('/', environ_base={'REMOTE_ADDR': '192.168.1.100'}, follow_redirects=False)
        assert resp.status_code != 401, f"LAN should not get 401 with default auth, got {resp.status_code}"
        assert resp.status_code in (200, 302, 500), f"LAN should get 200/302/500, got {resp.status_code}"
        
        # Remote request to API should get 401 (authentication required)
        resp = client.get('/api/library/movies', environ_base={'REMOTE_ADDR': '203.0.113.100'})
        assert resp.status_code == 401, f"Remote should get 401 with default auth, got {resp.status_code}"
