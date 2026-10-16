"""Tests for app/bot_api.py"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from flask import Flask, g


@pytest.fixture
def app():
    """Create a test Flask app."""
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-secret'
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


class TestBotApiSecurity:
    """Test security aspects of bot_api module."""

    def test_forward_headers_does_not_inject_server_key(self, app):
        """SECURITY: _forward_environ_headers must not inject server's API key."""
        from app.bot_api import _forward_environ_headers, register_bot_routes
        
        # Register routes with session cookie name and install secrets
        install_secrets = {"service_api_key": "server-secret-key"}
        register_bot_routes(app, "luma_session", install_secrets)
        
        with app.test_request_context('/', headers={}):
            headers = _forward_environ_headers()
            # Should NOT contain the server's API key when caller didn't provide one
            assert "X-API-Key" not in headers
    
    def test_forward_headers_preserves_caller_key(self, app):
        """_forward_environ_headers passes through caller's own API key."""
        from app.bot_api import _forward_environ_headers, register_bot_routes
        
        register_bot_routes(app, "luma_session", {})
        
        with app.test_request_context('/', headers={"X-API-Key": "caller-key"}):
            headers = _forward_environ_headers()
            assert headers.get("X-API-Key") == "caller-key"
    
    def test_forward_headers_preserves_session_cookie(self, app):
        """_forward_environ_headers passes through caller's session cookie."""
        from app.bot_api import _forward_environ_headers, register_bot_routes
        
        register_bot_routes(app, "luma_session", {})
        
        with app.test_request_context('/', headers={"Cookie": "luma_session=test-token"}):
            # Set up request.cookies
            from flask import request
            with patch.object(request, 'cookies', {"luma_session": "test-token"}):
                headers = _forward_environ_headers()
                assert "Cookie" in headers
                assert "luma_session=test-token" in headers["Cookie"]


class TestBotApiAuth:
    """Test authentication and authorization."""
    
    def test_require_user_blocks_unauthenticated(self, app):
        """_require_user returns 401 when no user is authenticated."""
        from app.bot_api import _require_user, register_bot_routes
        
        register_bot_routes(app, "luma_session", {})
        
        with app.test_request_context('/'):
            g.current_user = None
            result = _require_user()
            assert result is not None
            response, code = result
            assert code == 401
    
    def test_require_user_allows_authenticated(self, app):
        """_require_user returns None when user is authenticated."""
        from app.bot_api import _require_user, register_bot_routes
        
        register_bot_routes(app, "luma_session", {})
        
        with app.test_request_context('/'):
            g.current_user = {"id": 1, "name": "test", "is_admin": 1}
            result = _require_user()
            assert result is None
    
    def test_non_admin_does_not_get_admin_rights(self, app):
        """Non-admin users do not get elevated privileges through bot API."""
        from app.bot_api import register_bot_routes
        
        install_secrets = {"service_api_key": "server-key"}
        register_bot_routes(app, "luma_session", install_secrets)
        
        @app.route("/test-endpoint")
        def test_endpoint():
            from flask import jsonify
            user = getattr(g, 'current_user', None)
            if not user:
                return jsonify({"error": "no user"}), 401
            return jsonify({"is_admin": user.get("is_admin", 0)})
        
        # Set up a non-admin user
        with app.test_request_context('/'):
            g.current_user = {"id": 2, "name": "regular", "is_admin": 0}
            
            # Call through bot API's _local helper
            from app.bot_api import _local
            with patch("flask.g.current_user", g.current_user):
                response = _local(app, "GET", "/test-endpoint")
                data = response.get_json()
                # Non-admin should remain non-admin
                assert data.get("is_admin") == 0
