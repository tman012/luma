"""Tests for server config from settings with env overrides (Batch B item 8)."""

import pytest
import os
import tempfile


class TestServerConfigFromSettings:
    """Test reading port, bind, URL base, and cert/key from settings."""
    
    def test_port_default(self):
        """Port defaults to 5001 when not configured."""
        from app.server_config import get_port
        
        # No env var, no settings
        result = get_port(None)
        assert result == 5001
    
    def test_port_from_settings(self):
        """Port reads from settings.general.port."""
        from app.server_config import get_port
        
        # Mock settings object
        class MockSettings:
            def get(self, path, default=None):
                if path == 'general.port':
                    return 8080
                return default
        
        result = get_port(MockSettings())
        assert result == 8080
    
    def test_port_env_overrides_settings(self):
        """LUMA_PORT env var overrides settings."""
        from app.server_config import get_port
        
        os.environ['LUMA_PORT'] = '9000'
        
        class MockSettings:
            def get(self, path, default=None):
                if path == 'general.port':
                    return 8080
                return default
        
        result = get_port(MockSettings())
        assert result == 9000
        
        # Cleanup
        del os.environ['LUMA_PORT']
    
    def test_bind_default(self):
        """Bind defaults to 0.0.0.0 when not configured."""
        from app.server_config import get_bind_address
        
        result = get_bind_address(None)
        assert result == '0.0.0.0'
    
    def test_bind_from_settings(self):
        """Bind reads from settings.general.bind."""
        from app.server_config import get_bind_address
        
        class MockSettings:
            def get(self, path, default=None):
                if path == 'general.bind':
                    return '127.0.0.1'
                return default
        
        result = get_bind_address(MockSettings())
        assert result == '127.0.0.1'
    
    def test_bind_env_overrides_settings(self):
        """LUMA_BIND env var overrides settings."""
        from app.server_config import get_bind_address
        
        os.environ['LUMA_BIND'] = '192.168.1.100'
        
        class MockSettings:
            def get(self, path, default=None):
                if path == 'general.bind':
                    return '127.0.0.1'
                return default
        
        result = get_bind_address(MockSettings())
        assert result == '192.168.1.100'
        
        # Cleanup
        del os.environ['LUMA_BIND']
    
    def test_url_base_from_settings(self):
        """URL base reads from settings.general.url_base."""
        from app.server_config import get_url_base
        
        class MockSettings:
            def get(self, path, default=None):
                if path == 'general.url_base':
                    return '/myapp'
                return default
        
        result = get_url_base(MockSettings())
        assert result == '/myapp'
    
    def test_url_base_env_overrides_settings(self):
        """LUMA_URL_BASE env var overrides settings."""
        from app.server_config import get_url_base
        
        os.environ['LUMA_URL_BASE'] = '/override'
        
        class MockSettings:
            def get(self, path, default=None):
                if path == 'general.url_base':
                    return '/myapp'
                return default
        
        result = get_url_base(MockSettings())
        assert result == '/override'
        
        # Cleanup
        del os.environ['LUMA_URL_BASE']
    
    def test_https_from_settings(self):
        """HTTPS cert/key read from settings.security.https_cert/key."""
        from app.server_config import get_https_cert
        
        # Create temp cert files
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.crt') as cert_file:
            cert_file.write('CERT')
            cert_path = cert_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.key') as key_file:
            key_file.write('KEY')
            key_path = key_file.name
        
        try:
            class MockSettings:
                def get(self, path, default=None):
                    if path == 'security.https_cert':
                        return cert_path
                    elif path == 'security.https_key':
                        return key_path
                    return default
            
            result = get_https_cert(MockSettings())
            assert result == (cert_path, key_path)
        finally:
            # Cleanup
            os.unlink(cert_path)
            os.unlink(key_path)
    
    def test_https_env_overrides_settings(self):
        """LUMA_HTTPS_CERT/KEY env vars override settings."""
        from app.server_config import get_https_cert
        
        # Create temp cert files
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.crt') as cert_file:
            cert_file.write('CERT')
            cert_path = cert_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.key') as key_file:
            key_file.write('KEY')
            key_path = key_file.name
        
        try:
            os.environ['LUMA_HTTPS_CERT'] = cert_path
            os.environ['LUMA_HTTPS_KEY'] = key_path
            
            class MockSettings:
                def get(self, path, default=None):
                    if path == 'security.https_cert':
                        return '/wrong/cert'
                    elif path == 'security.https_key':
                        return '/wrong/key'
                    return default
            
            result = get_https_cert(MockSettings())
            assert result == (cert_path, key_path)
        finally:
            # Cleanup
            del os.environ['LUMA_HTTPS_CERT']
            del os.environ['LUMA_HTTPS_KEY']
            os.unlink(cert_path)
            os.unlink(key_path)
    
    def test_https_none_when_not_configured(self):
        """HTTPS returns None when not configured."""
        from app.server_config import get_https_cert
        
        result = get_https_cert(None)
        assert result is None
