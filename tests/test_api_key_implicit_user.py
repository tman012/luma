"""Test X-API-Key requests work without g.implicit_user error (Item 2)."""

import pytest
import os
import secrets


@pytest.fixture(autouse=True)
def setup_test_env(test_app, monkeypatch):
    """Set up test environment with API key."""
    api_key = secrets.token_urlsafe(32)
    monkeypatch.setenv('SERVICE_API_KEY', api_key)
    
    # Reload main module to pick up new API key
    import importlib
    import app.main
    importlib.reload(app.main)
    
    return api_key


class TestAPIKeyAuth:
    """Test X-API-Key authentication works correctly."""
    
    def test_api_key_request_no_error(self, test_app, setup_test_env):
        """X-API-Key request returns 200, not 500 from unset g.implicit_user."""
        api_key = setup_test_env
        client = test_app.test_client()
        
        # Make a request with X-API-Key header
        response = client.get('/api/settings', headers={
            'X-API-Key': api_key
        }, environ_base={'REMOTE_ADDR': '203.0.113.100'})
        
        # Should return 200, not 500
        assert response.status_code == 200
    
    def test_api_key_sets_service_user(self, test_app, setup_test_env):
        """X-API-Key request sets g.current_user to Service user."""
        api_key = setup_test_env
        client = test_app.test_client()
        
        # Create a test route that returns g.current_user
        @test_app.route('/test/current_user')
        def test_current_user():
            from flask import g, jsonify
            return jsonify({
                'user': g.current_user,
                'implicit': getattr(g, 'implicit_user', None)
            })
        
        response = client.get('/test/current_user', headers={
            'X-API-Key': api_key
        })
        
        data = response.get_json()
        assert response.status_code == 200
        assert data['user']['name'] == 'Service'
        assert data['user']['is_admin'] == 1
        assert data['implicit'] is False  # Should be set, not missing
    
    def test_invalid_api_key_rejected(self, test_app):
        """Invalid X-API-Key is rejected."""
        client = test_app.test_client()
        
        response = client.get('/api/settings', headers={
            'X-API-Key': 'invalid_key_12345'
        }, environ_base={'REMOTE_ADDR': '203.0.113.100'})
        
        # Should return 401 (no auth)
        assert response.status_code == 401
