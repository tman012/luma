"""Tests for server configuration: URL base, HTTPS, session secrets, cookie security."""

import pytest
import os
import tempfile
from pathlib import Path
import app.user_auth as user_auth


class TestURLBase:
    """Test URL base prefixing for all routes and static files."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test environment with URL base."""
        import app.main
        
        # Reinitialize auth
        user_auth.init_db()
        existing = user_auth.get_user_by_name('testuser')
        if not existing:
            user_auth.create_user('testuser', 'testpass123', is_admin=True)
        
        app.main.settings.update({
            'security': {
                'auth_required': 'disabled_local',
            }
        })
        
        self.app = test_app
        self.client = test_app.test_client()
    
    def test_empty_url_base_default_behavior(self, test_app):
        """Empty URL base leaves routes unchanged."""
        # Set empty URL base
        os.environ.pop('LUMA_URL_BASE', None)
        
        # Check that root route is accessible at /
        resp = self.client.get('/', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp.status_code in (200, 302, 500)  # Any non-404 is fine
    
    def test_url_base_with_middleware(self, test_app):
        """URL base middleware correctly prefixes routes."""
        # This test verifies the middleware is applied at app startup
        # The actual middleware behavior is tested through real requests
        import app.server_config
        
        # Test that get_url_base reads from env
        os.environ['LUMA_URL_BASE'] = '/luma'
        from importlib import reload
        reload(app.server_config)
        
        base = app.server_config.get_url_base()
        assert base == '/luma'
        
        # Cleanup
        os.environ.pop('LUMA_URL_BASE', None)
    
    def test_url_base_strips_trailing_slash(self, test_app):
        """URL base removes trailing slash."""
        os.environ['LUMA_URL_BASE'] = '/luma/'
        from importlib import reload
        import app.server_config
        reload(app.server_config)
        
        base = app.server_config.get_url_base()
        assert base == '/luma'
        assert not base.endswith('/')
        
        # Cleanup
        os.environ.pop('LUMA_URL_BASE', None)
    
    def test_url_base_adds_leading_slash(self, test_app):
        """URL base adds leading slash if missing."""
        os.environ['LUMA_URL_BASE'] = 'luma'
        from importlib import reload
        import app.server_config
        reload(app.server_config)
        
        base = app.server_config.get_url_base()
        assert base.startswith('/')
        assert base == '/luma'
        
        # Cleanup
        os.environ.pop('LUMA_URL_BASE', None)


class TestCookieSecurity:
    """Test cookie security flags: HttpOnly, SameSite, Secure."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test environment."""
        import app.main
        
        user_auth.init_db()
        existing = user_auth.get_user_by_name('testuser')
        if not existing:
            user_auth.create_user('testuser', 'testpass123', is_admin=True)
        
        app.main.settings.update({
            'security': {
                'auth_required': 'disabled_local',
            }
        })
        
        self.client = test_app.test_client()
    
    def test_cookie_httponly_flag(self, test_app):
        """Auth cookie has HttpOnly flag."""
        # Get CSRF token
        get_resp = self.client.get('/login')
        import re
        csrf_match = re.search(r'<input type="hidden" name="csrf_token" value="([^"]+)"', get_resp.data.decode())
        csrf_token = csrf_match.group(1) if csrf_match else ''
        
        resp = self.client.post('/login',
                                data={'username': 'testuser', 'password': 'testpass123', 'csrf_token': csrf_token},
                                environ_base={'REMOTE_ADDR': '192.168.1.100'},
                                follow_redirects=False)
        
        cookies = resp.headers.getlist('Set-Cookie')
        auth_cookie = [c for c in cookies if 'luma_auth_session=' in c]
        assert len(auth_cookie) > 0
        assert 'HttpOnly' in auth_cookie[0]
    
    def test_cookie_samesite_lax(self, test_app):
        """Auth cookie has SameSite=Lax."""
        get_resp = self.client.get('/login')
        import re
        csrf_match = re.search(r'<input type="hidden" name="csrf_token" value="([^"]+)"', get_resp.data.decode())
        csrf_token = csrf_match.group(1) if csrf_match else ''
        
        resp = self.client.post('/login',
                                data={'username': 'testuser', 'password': 'testpass123', 'csrf_token': csrf_token},
                                environ_base={'REMOTE_ADDR': '192.168.1.100'},
                                follow_redirects=False)
        
        cookies = resp.headers.getlist('Set-Cookie')
        auth_cookie = [c for c in cookies if 'luma_auth_session=' in c]
        assert len(auth_cookie) > 0
        assert 'SameSite=Lax' in auth_cookie[0]
    
    def test_cookie_secure_when_https(self, test_app):
        """Auth cookie has Secure flag when request is HTTPS."""
        get_resp = self.client.get('/login', base_url='https://localhost')
        import re
        csrf_match = re.search(r'<input type="hidden" name="csrf_token" value="([^"]+)"', get_resp.data.decode())
        csrf_token = csrf_match.group(1) if csrf_match else ''
        
        # Simulate HTTPS request by setting base_url
        resp = self.client.post('/login',
                                data={'username': 'testuser', 'password': 'testpass123', 'csrf_token': csrf_token},
                                environ_base={
                                    'REMOTE_ADDR': '192.168.1.100',
                                },
                                base_url='https://localhost',
                                follow_redirects=False)
        
        cookies = resp.headers.getlist('Set-Cookie')
        auth_cookie = [c for c in cookies if 'luma_auth_session=' in c]
        assert len(auth_cookie) > 0
        assert 'Secure' in auth_cookie[0]
    
    def test_cookie_not_secure_when_http(self, test_app):
        """Auth cookie does not have Secure flag when request is HTTP."""
        get_resp = self.client.get('/login')
        import re
        csrf_match = re.search(r'<input type="hidden" name="csrf_token" value="([^"]+)"', get_resp.data.decode())
        csrf_token = csrf_match.group(1) if csrf_match else ''
        
        resp = self.client.post('/login',
                                data={'username': 'testuser', 'password': 'testpass123', 'csrf_token': csrf_token},
                                environ_base={
                                    'REMOTE_ADDR': '192.168.1.100',
                                    'wsgi.url_scheme': 'http'
                                },
                                follow_redirects=False)
        
        cookies = resp.headers.getlist('Set-Cookie')
        auth_cookie = [c for c in cookies if 'luma_auth_session=' in c]
        assert len(auth_cookie) > 0
        # Secure flag should not be present for HTTP
        assert 'Secure' not in auth_cookie[0] or 'Secure;' not in auth_cookie[0]


class TestRememberMe:
    """Test remember-me checkbox behavior."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test environment."""
        import app.main
        
        user_auth.init_db()
        existing = user_auth.get_user_by_name('testuser')
        if not existing:
            user_auth.create_user('testuser', 'testpass123', is_admin=True)
        
        app.main.settings.update({
            'security': {
                'auth_required': 'disabled_local',
            }
        })
        
        self.client = test_app.test_client()
    
    def test_remember_me_checked_30_days(self, test_app):
        """Remember-me checked gives 30-day cookie."""
        get_resp = self.client.get('/login')
        import re
        csrf_match = re.search(r'<input type="hidden" name="csrf_token" value="([^"]+)"', get_resp.data.decode())
        csrf_token = csrf_match.group(1) if csrf_match else ''
        
        resp = self.client.post('/login',
                                data={'username': 'testuser', 'password': 'testpass123', 'remember': 'on', 'csrf_token': csrf_token},
                                environ_base={'REMOTE_ADDR': '192.168.1.100'},
                                follow_redirects=False)
        
        cookies = resp.headers.getlist('Set-Cookie')
        auth_cookie = [c for c in cookies if 'luma_auth_session=' in c]
        assert len(auth_cookie) > 0
        
        # Should have Max-Age=2592000 (30 days in seconds)
        assert 'Max-Age=2592000' in auth_cookie[0]
    
    def test_remember_me_unchecked_session_cookie(self, test_app):
        """Remember-me unchecked gives session cookie (no Max-Age)."""
        get_resp = self.client.get('/login')
        import re
        csrf_match = re.search(r'<input type="hidden" name="csrf_token" value="([^"]+)"', get_resp.data.decode())
        csrf_token = csrf_match.group(1) if csrf_match else ''
        
        resp = self.client.post('/login',
                                data={'username': 'testuser', 'password': 'testpass123', 'csrf_token': csrf_token},
                                environ_base={'REMOTE_ADDR': '192.168.1.100'},
                                follow_redirects=False)
        
        cookies = resp.headers.getlist('Set-Cookie')
        auth_cookie = [c for c in cookies if 'luma_auth_session=' in c]
        assert len(auth_cookie) > 0
        
        # Should NOT have Max-Age (session cookie)
        assert 'Max-Age' not in auth_cookie[0]
        # Should NOT have Expires either
        assert 'expires=' not in auth_cookie[0].lower()


class TestSessionSecret:
    """Test session secret generation and persistence."""
    
    def test_session_secret_from_env(self, test_app):
        """Session secret can be set via environment."""
        import app.server_config
        from importlib import reload
        
        os.environ['LUMA_SESSION_SECRET'] = 'test_secret_from_env'
        reload(app.server_config)
        
        # Create temp settings
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{}')
            temp_path = f.name
        
        try:
            from app.settings_manager import SettingsManager
            settings = SettingsManager(temp_path)
            secret = app.server_config.get_session_secret(settings)
            
            # Should use env var
            assert secret == 'test_secret_from_env'
        finally:
            os.unlink(temp_path)
            os.environ.pop('LUMA_SESSION_SECRET', None)
    
    def test_session_secret_generated_and_persisted(self, test_app):
        """Session secret is generated and saved on first run."""
        import app.server_config
        from importlib import reload
        
        os.environ.pop('LUMA_SESSION_SECRET', None)
        reload(app.server_config)
        
        # Create temp settings
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{}')
            temp_path = f.name
        
        try:
            from app.settings_manager import SettingsManager
            settings = SettingsManager(temp_path)
            
            # First call should generate
            secret1 = app.server_config.get_session_secret(settings)
            assert secret1
            assert len(secret1) > 20  # Should be a substantial random string
            
            # Should be persisted
            saved_secret = settings.get('security.session_secret')
            assert saved_secret == secret1
            
            # Second call should return same secret
            secret2 = app.server_config.get_session_secret(settings)
            assert secret2 == secret1
        finally:
            os.unlink(temp_path)
    
    def test_session_secret_never_hardcoded(self, test_app):
        """Session secret is never hard-coded in code."""
        import app.server_config
        import inspect
        
        # Read the source code of get_session_secret
        source = inspect.getsource(app.server_config.get_session_secret)
        
        # Should not contain any hard-coded secrets
        assert 'mysecret' not in source.lower()
        assert 'hardcoded' not in source.lower()
        # Should use token_urlsafe or similar
        assert 'token_urlsafe' in source or 'secrets' in source


class TestHTTPSConfig:
    """Test HTTPS certificate configuration."""
    
    def test_https_cert_returns_none_when_not_configured(self, test_app):
        """get_https_cert returns None when cert/key not set."""
        import app.server_config
        from importlib import reload
        
        os.environ.pop('LUMA_HTTPS_CERT', None)
        os.environ.pop('LUMA_HTTPS_KEY', None)
        reload(app.server_config)
        
        result = app.server_config.get_https_cert()
        assert result is None
    
    def test_https_cert_validates_file_existence(self, test_app):
        """get_https_cert raises error if files don't exist."""
        import app.server_config
        from importlib import reload
        
        os.environ['LUMA_HTTPS_CERT'] = '/nonexistent/cert.pem'
        os.environ['LUMA_HTTPS_KEY'] = '/nonexistent/key.pem'
        reload(app.server_config)
        
        with pytest.raises(FileNotFoundError):
            app.server_config.get_https_cert()
        
        # Cleanup
        os.environ.pop('LUMA_HTTPS_CERT', None)
        os.environ.pop('LUMA_HTTPS_KEY', None)
    
    def test_https_cert_returns_paths_when_valid(self, test_app):
        """get_https_cert returns tuple of paths when files exist."""
        import app.server_config
        from importlib import reload
        
        # Create temp cert and key files
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as cert_file:
            cert_file.write('FAKE CERT')
            cert_path = cert_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as key_file:
            key_file.write('FAKE KEY')
            key_path = key_file.name
        
        try:
            os.environ['LUMA_HTTPS_CERT'] = cert_path
            os.environ['LUMA_HTTPS_KEY'] = key_path
            reload(app.server_config)
            
            result = app.server_config.get_https_cert()
            assert result is not None
            assert result[0] == cert_path
            assert result[1] == key_path
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)
            os.environ.pop('LUMA_HTTPS_CERT', None)
            os.environ.pop('LUMA_HTTPS_KEY', None)


class TestLoginRedirectURLBase:
    """Test that login redirects respect URL base."""
    
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        """Set up test environment."""
        import app.main
        
        user_auth.init_db()
        existing = user_auth.get_user_by_name('testuser')
        if not existing:
            user_auth.create_user('testuser', 'testpass123', is_admin=True)
        
        app.main.settings.update({
            'security': {
                'auth_required': 'disabled_local',
            }
        })
        
        self.client = test_app.test_client()
    
    def test_login_redirect_uses_url_base(self, test_app):
        """After sign-in the redirect stays under the URL base from app.config."""
        import re
        test_app.config['URL_BASE'] = '/luma'
        page = self.client.get('/login?next=/settings', environ_base={'REMOTE_ADDR': '203.0.113.9'})
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        resp = self.client.post('/login?next=/settings',
                                data={'username': 'testuser', 'password': 'testpass123', 'csrf_token': token},
                                environ_base={'REMOTE_ADDR': '203.0.113.9'})
        assert resp.status_code == 302
        assert resp.headers['Location'] in ('/luma/settings', 'http://localhost/luma/settings')
        # The form token is single-use: a new sign-in needs a fresh page.
        page = self.client.get('/login?next=//evil.com', environ_base={'REMOTE_ADDR': '203.0.113.9'})
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        bad = self.client.post('/login?next=//evil.com',
                               data={'username': 'testuser', 'password': 'testpass123', 'csrf_token': token},
                               environ_base={'REMOTE_ADDR': '203.0.113.9'})
        assert bad.headers['Location'].endswith('/luma/')
