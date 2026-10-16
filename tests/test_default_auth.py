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
        
        import app.user_auth as user_auth
        user_auth.create_user('owner', '1234', is_admin=True)
        client = test_app.test_client()
        
        # LAN request: implicit owner, no sign-in
        resp = client.get('/api/auth/me', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp.status_code == 200, f"LAN should be signed in implicitly, got {resp.status_code}"
        assert resp.get_json()['implicit'] is True
        
        # Remote request to API should get 401 (authentication required)
        resp = client.get('/api/library/movies', environ_base={'REMOTE_ADDR': '203.0.113.100'})
        assert resp.status_code == 401, f"Remote should get 401 with default auth, got {resp.status_code}"
