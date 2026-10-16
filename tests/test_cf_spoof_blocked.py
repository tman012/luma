"""Test that spoofing CF-Connecting-IP stays remote (Item 1)."""

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
def app_ctx():
    """Create a test Flask app."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    return app


class TestCFConnectingSpoofBlocked:
    """Test CF-Connecting-IP is only trusted from trusted_proxies."""
    
    def test_spoof_cf_connecting_ip_stays_remote(self, app_ctx, mock_settings):
        """Spoofing CF-Connecting-IP without trusted_proxies is rejected."""
        from app import access
        
        # No trusted proxies configured
        def mock_get(key, default=None):
            if key == 'security.trusted_proxies':
                return ''
            return default
        mock_settings.get.side_effect = mock_get
        
        # Attacker tries to spoof CF-Connecting-IP from a non-trusted proxy
        with app_ctx.test_request_context('/', headers={
            'CF-Connecting-IP': '192.168.1.100',  # Fake local IP
        }, environ_base={'REMOTE_ADDR': '203.0.113.50'}):  # Public IP
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        # Should be False because CF-Connecting-IP is not trusted
        # Presence of CF header marks request as relayed, so client_ip returns None
        assert is_local is False
    
    def test_xff_trusted_from_trusted_proxy(self, app_ctx, mock_settings):
        """X-Forwarded-For is honored when the peer is in trusted_proxies."""
        from app import access
        
        # Configure trusted proxies (loopback where CF connector runs)
        def mock_get(key, default=None):
            if key == 'security.trusted_proxies':
                return '127.0.0.1'
            return default
        mock_settings.get.side_effect = mock_get
        
        # Request relayed by a trusted proxy
        with app_ctx.test_request_context('/', headers={
            'X-Forwarded-For': '192.168.1.100',  # Private IP
        }, environ_base={'REMOTE_ADDR': '127.0.0.1'}):  # Trusted proxy
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        # Should be True because X-Forwarded-For shows private IP from trusted proxy
        assert is_local is True
    
    def test_no_forward_headers_direct_lan(self, app_ctx, mock_settings):
        """Direct LAN connection without forward headers works."""
        from app import access
        
        # No trusted proxies
        def mock_get(key, default=None):
            if key == 'security.trusted_proxies':
                return ''
            return default
        mock_settings.get.side_effect = mock_get
        
        # Direct connection from LAN
        with app_ctx.test_request_context('/', environ_base={'REMOTE_ADDR': '192.168.1.100'}):
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        # Should be True - direct private IP
        assert is_local is True
