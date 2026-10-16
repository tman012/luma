"""Tests for home WAN IP timestamps and eviction (Item 6)."""

import pytest
import time
from unittest.mock import Mock, patch
from pathlib import Path


class TestHomeWANTimestamps:
    """Test home WAN IPs stored with timestamps and proper eviction."""
    
    def test_home_ips_stored_with_timestamps(self, tmp_path):
        """Home IPs are stored with last-seen timestamps."""
        from app import access
        
        # Clear and add IPs with timestamps
        with access._home_lock:
            access._home_ips.clear()
            access._home_ips['203.0.113.50'] = time.time()
            access._home_ips['203.0.113.51'] = time.time() - 100
        
        # Check structure
        with access._home_lock:
            assert isinstance(access._home_ips, dict)
            assert '203.0.113.50' in access._home_ips
            assert isinstance(access._home_ips['203.0.113.50'], float)
    
    def test_eviction_removes_oldest(self, tmp_path):
        """Eviction removes the oldest IP when over limit."""
        from app import access
        
        # Set small limit for testing
        old_max = access._HOME_MAX_SIZE
        access._HOME_MAX_SIZE = 3
        
        try:
            with access._home_lock:
                access._home_ips.clear()
                # Add 4 IPs with different timestamps
                now = time.time()
                access._home_ips['203.0.113.1'] = now - 300  # Oldest
                access._home_ips['203.0.113.2'] = now - 200
                access._home_ips['203.0.113.3'] = now - 100
                access._home_ips['203.0.113.4'] = now  # Newest
            
            # Trigger eviction
            with patch.object(access, '_lookup_wan_ip', return_value='203.0.113.5'):
                access._refresh_home_wan_ip()
            
            # Check that oldest was evicted
            with access._home_lock:
                assert len(access._home_ips) == 3
                assert '203.0.113.1' not in access._home_ips  # Oldest removed
                assert '203.0.113.5' in access._home_ips  # New one added
        finally:
            access._HOME_MAX_SIZE = old_max
    
    def test_save_and_reload_with_timestamps(self, tmp_path):
        """Home IPs with timestamps persist and reload correctly."""
        from app import access
        
        # Set up state path
        state_file = tmp_path / "home_wan_ips.json"
        access._state_path = state_file
        
        # Add IPs with timestamps
        now = time.time()
        with access._home_lock:
            access._home_ips.clear()
            access._home_ips['203.0.113.50'] = now
            access._home_ips['203.0.113.51'] = now - 100
        
        # Save
        access._save_home_ips()
        
        # Clear and reload
        with access._home_lock:
            access._home_ips.clear()
        
        access._load_home_ips()
        
        # Check reloaded with timestamps
        with access._home_lock:
            assert len(access._home_ips) == 2
            assert '203.0.113.50' in access._home_ips
            assert '203.0.113.51' in access._home_ips
            assert isinstance(access._home_ips['203.0.113.50'], float)
            assert isinstance(access._home_ips['203.0.113.51'], float)
    
    def test_cloudflare_fallback_lookup(self):
        """WAN IP lookup falls back to cloudflare.com if 1.1.1.1 fails."""
        from app import access
        
        def mock_get(url, **kwargs):
            mock_resp = Mock()
            if '1.1.1.1' in url:
                raise Exception("1.1.1.1 failed")
            elif 'cloudflare.com' in url:
                mock_resp.text = "ip=203.0.113.100\nother=data\n"
                return mock_resp
            raise Exception("Unexpected URL")
        
        with patch('requests.get', side_effect=mock_get):
            ip = access._lookup_wan_ip()
        
        assert ip == '203.0.113.100'
    
    def test_legacy_list_format_migrated(self, tmp_path):
        """Legacy list format is migrated to dict with timestamps."""
        from app import access
        import json
        
        # Create legacy format state file
        state_file = tmp_path / "home_wan_ips.json"
        access._state_path = state_file
        
        # Write legacy list format
        state_file.write_text(json.dumps({
            'home_ips': ['203.0.113.50', '203.0.113.51']
        }))
        
        # Load
        access._load_home_ips()
        
        # Check migrated to dict with timestamps
        with access._home_lock:
            assert isinstance(access._home_ips, dict)
            assert '203.0.113.50' in access._home_ips
            assert '203.0.113.51' in access._home_ips
            assert isinstance(access._home_ips['203.0.113.50'], float)
