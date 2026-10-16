"""Tests for access.py home WAN tracking (Item 5 & 6)."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from flask import Flask, g


@pytest.fixture
def mock_settings():
    """Mock settings manager."""
    mock = Mock()
    mock.get.return_value = ''
    mock.path = '/tmp/test_settings.json'
    return mock


@pytest.fixture
def app():
    """Create a test Flask app."""
    from flask import Flask
    app = Flask(__name__)
    app.config['TESTING'] = True
    return app


class TestHomeWANTracking:
    """Test home WAN IP tracking."""
    
    def test_home_wan_ip_treated_as_local(self, app, mock_settings):
        """Home WAN IP via Cloudflare tunnel is treated as local."""
        from app import access
        
        # Set up trusted proxies
        mock_settings.get.return_value = '127.0.0.1'
        
        # Add a home WAN IP with timestamp
        with access._home_lock:
            access._home_ips.clear()
            import time
            access._home_ips['203.0.113.50'] = time.time()
        
        with app.test_request_context('/', headers={
            'CF-Connecting-IP': '203.0.113.50',
        }, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        assert is_local is True
    
    def test_other_remote_ip_not_local(self, app, mock_settings):
        """Other remote IPs are not treated as local."""
        from app import access
        
        # Set up trusted proxies
        mock_settings.get.return_value = '127.0.0.1'
        
        # Home WAN is different
        with access._home_lock:
            access._home_ips.clear()
            import time
            access._home_ips['203.0.113.50'] = time.time()
        
        with app.test_request_context('/', headers={
            'CF-Connecting-IP': '198.51.100.25',  # Different IP
        }, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        assert is_local is False
    
    def test_cf_ip_not_trusted_without_trusted_proxies(self, app, mock_settings):
        """CF-Connecting-IP not trusted when trusted_proxies is empty (Item 6).
        
        When trusted_proxies is empty, the presence of a CF header means the request
        was relayed, so client_ip returns None. This is correct behavior - we can't
        trust the IP. The kiosk should configure trusted_proxies.
        """
        from app import access
        
        # No trusted proxies configured
        def mock_get(key, default=None):
            if key == 'security.trusted_proxies':
                return ''
            return default
        mock_settings.get.side_effect = mock_get
        
        # Request with CF header but no trusted proxies
        with app.test_request_context('/', headers={
            'CF-Connecting-IP': '203.0.113.50',
        }, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        # Should be False because client_ip returns None (untrusted relay)
        # The kiosk behind CF tunnel MUST configure trusted_proxies
        assert is_local is False
    
    def test_cf_ip_ignored_from_untrusted_peer(self, app, mock_settings):
        """CF-Connecting-IP ignored when peer is not in trusted_proxies (Item 6)."""
        from app import access
        
        # Trusted proxies: only 10.0.0.1
        def mock_get(key, default=None):
            if key == 'security.trusted_proxies':
                return '10.0.0.1'
            return default
        mock_settings.get.side_effect = mock_get
        
        # Request from untrusted peer with CF header
        with app.test_request_context('/', headers={
            'CF-Connecting-IP': '203.0.113.50',
        }, environ_base={'REMOTE_ADDR': '192.168.1.10'}):
            from flask import request
            is_local = access.is_local_request(request, mock_settings)
        
        # Should be False - CF header present means relayed, but peer not trusted
        # client_ip returns None for untrusted relay
        assert is_local is False
    
    def test_save_and_reload_home_ips(self, app, mock_settings, tmp_path):
        """Home IPs are persisted and reloaded (Item 4 - if persistence survives)."""
        from app import access
        
        # Set up state path
        state_file = tmp_path / "home_wan_ips.json"
        mock_settings.path = str(tmp_path / "settings.json")
        access._state_path = state_file
        
        # Add some IPs with timestamps and save
        import time
        now = time.time()
        with access._home_lock:
            access._home_ips.clear()
            access._home_ips['203.0.113.50'] = now
            access._home_ips['203.0.113.51'] = now - 100
        
        access._save_home_ips()
        
        # Clear and reload
        with access._home_lock:
            access._home_ips.clear()
        
        access._load_home_ips()
        
        # Check reloaded
        with access._home_lock:
            assert '203.0.113.50' in access._home_ips
            assert '203.0.113.51' in access._home_ips
            assert len(access._home_ips) == 2
            assert isinstance(access._home_ips['203.0.113.50'], (int, float))


class TestOpenModeRemoteAccess:
    """Test that open mode + remote request doesn't get owner session (Item 5)."""
    
    def test_open_mode_remote_not_owner(self, app, mock_settings):
        """Open mode remote visitor is not treated as local or owner."""
        from app import access
        
        # Set access_mode to open
        def get_setting(key, default=None):
            if key == 'security.access_mode':
                return 'open'
            if key == 'security.trusted_proxies':
                return '127.0.0.1'
            return default
        mock_settings.get.side_effect = get_setting
        
        # Remote IP (not in home WAN list)
        with access._home_lock:
            access._home_ips.clear()
        
        # Request from remote IP via CF tunnel
        with app.test_request_context('/', headers={
            'CF-Connecting-IP': '198.51.100.25',
        }, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            from flask import request
            
            # Check mode
            mode = access.mode(mock_settings)
            assert mode == 'open'
            
            # Check not local
            is_local = access.is_local_request(request, mock_settings)
            assert is_local is False
            
            # In open mode, needs_pin should be False
            # But the visitor shouldn't be treated as local
            needs_pin = access.needs_pin(mock_settings, request)
            assert needs_pin is False
            
            # The important part: remote visitor shouldn't get owner privileges
            # (that's checked at the auth layer, not here, but we verify they're not "local")
            assert is_local is False
