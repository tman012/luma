"""Tests for open mode remote access restrictions (Item 7)."""

import pytest
from unittest.mock import Mock, patch
from flask import Flask, g


@pytest.fixture
def mock_settings():
    """Mock settings manager."""
    mock = Mock()
    mock.get.return_value = ''
    return mock


@pytest.fixture
def app():
    """Create a test Flask app."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    return app


class TestOpenModeRemoteAccess:
    """Test that open mode requires sign-in for remote visitors."""
    
    def test_remote_visitor_in_open_mode_needs_signin(self, app, mock_settings):
        """Remote visitor in open mode gets sign-in requirement (Item 7)."""
        from app import access
        
        # Configure open mode
        def mock_get(key, default=None):
            if key == 'security.access_mode':
                return 'open'
            if key == 'security.trusted_proxies':
                return '127.0.0.1'
            return default
        mock_settings.get.side_effect = mock_get
        
        # Remote visitor (not in home WAN list)
        with access._home_lock:
            access._home_ips.clear()
        
        # Request from remote IP via CF tunnel
        with app.test_request_context('/', headers={
            'CF-Connecting-IP': '198.51.100.25',  # Remote IP
        }, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            from flask import request
            
            # Verify mode is open
            assert access.mode(mock_settings) == 'open'
            
            # Verify not local
            assert access.is_local_request(request, mock_settings) is False
            
            # Remote visitor needs PIN even in open mode
            assert access.needs_pin(mock_settings, request) is True
    
    def test_local_visitor_in_open_mode_gets_open_access(self, app, mock_settings):
        """Local visitor in open mode gets open access (Item 7)."""
        from app import access
        
        # Configure open mode
        def mock_get(key, default=None):
            if key == 'security.access_mode':
                return 'open'
            if key == 'security.trusted_proxies':
                return ''
            return default
        mock_settings.get.side_effect = mock_get
        
        # Local visitor (direct connection from private IP)
        with app.test_request_context('/', environ_base={'REMOTE_ADDR': '192.168.1.100'}):
            from flask import request
            
            # Verify mode is open
            assert access.mode(mock_settings) == 'open'
            
            # Verify is local
            assert access.is_local_request(request, mock_settings) is True
            
            # Local visitor doesn't need PIN in open mode
            assert access.needs_pin(mock_settings, request) is False
    
    def test_remote_visitor_no_implicit_owner_session(self, app, mock_settings):
        """Remote visitor in open mode doesn't get implicit owner session (Item 7)."""
        from app import access, user_auth
        
        # Configure open mode
        def mock_get(key, default=None):
            if key == 'security.access_mode':
                return 'open'
            if key == 'security.trusted_proxies':
                return '127.0.0.1'
            return default
        mock_settings.get.side_effect = mock_get
        
        # Mock owner user
        mock_user_auth = Mock()
        owner_user = {'id': 1, 'name': 'admin', 'is_admin': 1, 'status': 'active'}
        mock_user_auth.list_users.return_value = [owner_user]
        
        # Remote request
        with app.test_request_context('/', headers={
            'CF-Connecting-IP': '198.51.100.25',
        }, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            from flask import request
            
            # Mock UserAuth
            with patch.object(user_auth, 'UserAuth') as MockUserAuth:
                instance = MockUserAuth.return_value
                instance.list_users.return_value = [owner_user]
                
                auth = user_auth.UserAuth(mock_settings, None)
                session = auth.implicit_owner_session(mock_settings, request)
                
                # Remote visitor should NOT get implicit owner session
                assert session is None
    
    def test_local_visitor_gets_implicit_owner_session(self, app, mock_settings):
        """Local visitor in open mode gets implicit owner session (Item 7)."""
        from app import access, user_auth
        
        # Configure open mode
        def mock_get(key, default=None):
            if key == 'security.access_mode':
                return 'open'
            return default
        mock_settings.get.side_effect = mock_get
        
        # Mock owner user
        owner_user = {'id': 1, 'name': 'admin', 'is_admin': 1, 'status': 'active'}
        
        # Local request
        with app.test_request_context('/', environ_base={'REMOTE_ADDR': '192.168.1.100'}):
            from flask import request
            
            # Mock UserAuth
            with patch.object(user_auth, 'UserAuth') as MockUserAuth:
                instance = MockUserAuth.return_value
                instance.list_users.return_value = [owner_user]
                
                auth = user_auth.UserAuth(mock_settings, None)
                with patch.object(access, 'owner', return_value=owner_user):
                    session = auth.implicit_owner_session(mock_settings, request)
                
                # Local visitor should get implicit owner session
                assert session is not None
                assert session['user_id'] == 1
                assert session['implicit'] is True
