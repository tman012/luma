"""Tests for request portal password handling (Item 1)."""

import pytest
import json
from unittest.mock import Mock, patch
from werkzeug.security import check_password_hash


@pytest.fixture
def app():
    """Create test Flask app with request_portal."""
    from flask import Flask
    from app import request_portal
    
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.secret_key = 'test-secret-key'
    
    # Mock settings
    mock_settings = Mock()
    mock_settings.get.return_value = {}
    mock_settings.path = '/tmp/test_settings.json'
    
    def mock_cfg(key, default=None):
        data = {
            'enabled': True,
            'hostname': '',
            'movie_profile': '',
            'tv_profile': '',
            'exclude_libraries': [],
        }
        return data.get(key, default)
    
    with patch.object(request_portal, 'cfg', side_effect=mock_cfg):
        with patch.object(request_portal, '_settings', mock_settings):
            request_portal.init_app(app, mock_settings)
    
    return app


class TestRequestPortalPasswordHashing:
    """Test that passwords are hashed, not stored plaintext."""
    
    def test_password_hashing_works_correctly(self):
        """Password hashing and verification works correctly (unit test)."""
        from werkzeug.security import generate_password_hash
        
        # Test the hash generation directly
        password = 'test1234'
        hashed = generate_password_hash(password)
        
        # Verify it's properly hashed (werkzeug 3.x uses scrypt, 2.x uses pbkdf2)
        assert hashed.startswith('scrypt:') or hashed.startswith('pbkdf2:')
        assert len(hashed) > 50
        assert check_password_hash(hashed, password)
        assert not check_password_hash(hashed, 'wrongpass')
    
    def test_unlock_with_hashed_password(self, app):
        """Unlock works with hashed password."""
        from werkzeug.security import generate_password_hash
        from app import request_portal
        
        # Set a hashed password
        hashed = generate_password_hash('test1234')
        
        def mock_cfg(key, default=None):
            if key == 'password_hash':
                return hashed
            if key == 'enabled':
                return True
            return default
        
        with patch.object(request_portal, 'cfg', side_effect=mock_cfg):
            client = app.test_client()
            resp = client.post('/api/request/unlock',
                              json={'password': 'test1234'})
            
            assert resp.status_code == 200
            data = resp.get_json()
            assert data.get('ok') is True  # request_portal returns 'ok' not 'success'
    
    def test_get_never_returns_password(self, app):
        """GET /api/request/admin/settings never returns the password."""
        from werkzeug.security import generate_password_hash
        from app import request_portal
        
        # Set a hashed password
        hashed = generate_password_hash('secretpass')
        
        def mock_cfg(key, default=None):
            if key == 'password_hash':
                return hashed
            if key == 'enabled':
                return True
            return default
        
        with app.test_request_context():
            from flask import g
            g.current_user = {'id': 1, 'name': 'admin', 'is_admin': 1}
            
            with patch.object(request_portal, 'cfg', side_effect=mock_cfg):
                client = app.test_client()
                resp = client.get('/api/request/admin/settings')
                
                assert resp.status_code == 200
                data = resp.get_json()
                
                # Should have has_password but not the password itself
                assert data.get('has_password') is True
                assert 'password' not in data
                assert 'password_hash' not in data


class TestRequestPortalMigration:
    """Test password migration strips plaintext passwords."""
    
    def test_migration_strips_plaintext_password(self):
        """Migration strips any plaintext 'password' field."""
        from app.settings_manager import _migrate_strip_plaintext_request_password
        
        data = {
            'request_portal': {
                'enabled': True,
                'password': 'plaintext_secret',  # Should be removed
                'password_hash': 'pbkdf2:sha256:....',
                'hostname': 'requests.example.com',
            }
        }
        
        changed = _migrate_strip_plaintext_request_password(data)
        
        assert changed is True
        assert 'password' not in data['request_portal']
        assert 'password_hash' in data['request_portal']
    
    def test_migration_parses_exclude_libraries(self):
        """Migration converts exclude_libraries string to list."""
        from app.settings_manager import _migrate_strip_plaintext_request_password
        
        data = {
            'request_portal': {
                'enabled': True,
                'exclude_libraries': 'Full Rips, 4K, Private',  # String, should be list
            }
        }
        
        changed = _migrate_strip_plaintext_request_password(data)
        
        assert changed is True
        assert isinstance(data['request_portal']['exclude_libraries'], list)
        assert data['request_portal']['exclude_libraries'] == ['Full Rips', '4K', 'Private']
    
    def test_migration_handles_missing_portal(self):
        """Migration handles missing request_portal section."""
        from app.settings_manager import _migrate_strip_plaintext_request_password
        
        data = {'other_section': {}}
        
        changed = _migrate_strip_plaintext_request_password(data)
        
        assert changed is False
