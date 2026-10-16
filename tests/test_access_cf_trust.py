"""Tests for CF-Connecting-IP trust requirements (Item 6)."""

import pytest
from unittest.mock import Mock
from flask import Flask


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


class TestCFConnectingIPTrust:
    """Test CF-Connecting-IP is only trusted from trusted_proxies."""
    
    def test_kiosk_with_trusted_proxies_is_local(self, app, mock_settings):
        """Kiosk behind CF tunnel with trusted_proxies configured is treated as local."""
        from app import access
        
        # Configure trusted proxies
        def mock_get(key, default=None):
            if key == 'security.trusted_proxies':
                return '127.0.0.1'  # Trust loopback (where CF connector runs)
            return default
        mock_settings.get.side_effect = mock_get
        
        # Request from kiosk via CF tunnel (private IP via CF-Connecting-IP)
        with app.test_request_context('/', headers={
            'CF-Connecting-IP': '192.168.1.100',  # Kiosk's LAN IP
        }, environ_base={'REMOTE_ADDR': '127.0.0.1'}):  # CF connector on loopback
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        # Should be True because the CF-Connecting-IP is private and trusted
        assert is_local is True
    
    def test_kiosk_without_trusted_proxies_not_lockout(self, app, mock_settings):
        """Kiosk without trusted_proxies won't be locked out if accessing directly."""
        from app import access
        
        # No trusted proxies
        def mock_get(key, default=None):
            if key == 'security.trusted_proxies':
                return ''
            return default
        mock_settings.get.side_effect = mock_get
        
        # Direct connection from kiosk (no CF headers)
        with app.test_request_context('/', environ_base={'REMOTE_ADDR': '192.168.1.100'}):
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        # Should be True because it's a direct connection from private IP
        assert is_local is True
    
    def test_remote_via_cf_without_trusted_proxies_blocked(self, app, mock_settings):
        """Remote visitor via CF without trusted_proxies can't spoof local."""
        from app import access
        
        # No trusted proxies
        def mock_get(key, default=None):
            if key == 'security.trusted_proxies':
                return ''
            return default
        mock_settings.get.side_effect = mock_get
        
        # Remote attacker trying to spoof local IP via CF-Connecting-IP
        with app.test_request_context('/', headers={
            'CF-Connecting-IP': '192.168.1.100',  # Fake local IP
        }, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        # Should be False because CF headers aren't trusted without trusted_proxies
        # Presence of CF header means relayed, so client_ip returns None
        assert is_local is False
