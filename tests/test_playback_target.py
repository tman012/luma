"""Tests for _resolve_playback_target with rooms/devices add-flow"""

import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from pathlib import Path

# Mock the cache directory path before any imports
import sys
import tempfile
temp_cache = tempfile.mkdtemp()

# Patch Path.mkdir to avoid the /app/cache directory issue  
original_mkdir = Path.mkdir
def mock_mkdir(self, *args, **kwargs):
    try:
        return original_mkdir(self, *args, **kwargs)
    except FileNotFoundError:
        # If parent doesn't exist and we're trying to create /app/cache, use temp
        pass

Path.mkdir = mock_mkdir


@pytest.fixture
def mock_settings():
    """Mock SettingsManager."""
    mock = Mock()
    mock.get_default_adb_port.return_value = 5555
    return mock


@pytest.fixture
def mock_plex_service():
    """Mock plex_service."""
    mock = Mock()
    mock.get_metadata.return_value = {'viewOffset': 12000}
    return mock


class TestResolvePlaybackTargetNestedDevices:
    """Test _resolve_playback_target with nested devices format (add-flow)."""
    
    def test_resolve_with_plex_service_in_device(self, mock_settings, mock_plex_service):
        """Resolves device with Plex service from nested devices list."""
        from app.main import _resolve_playback_target
        
        # Room with nested devices list (new add-flow format)
        room = {
            'id': 'room1',
            'name': 'Living Room',
            'devices': [
                {
                    'id': 'dev1',
                    'host': '192.168.1.100',
                    'connectPort': 5555,
                    'platform': 'shield',
                    'services': [
                        {
                            'id': 'svc1',
                            'type': 'plex',
                            'plex_client_port': 32500,
                        }
                    ]
                }
            ]
        }
        
        mock_settings.get_room_by_id.return_value = room
        
        with patch('app.main.settings', mock_settings):
            with patch('app.main.plex_service', mock_plex_service):
                with patch('app.main._play_via_plex') as mock_play:
                    with patch('app.device_wake.ensure_device_ready', return_value={'ok': True}):
                        mock_play.return_value = Mock(get_json=lambda: {'success': True})
                        
                        result = _resolve_playback_target(rating_key=123, room_id='room1')
        
        assert result['success'] is True
        # Verify _play_via_plex was called with correct params
        assert mock_play.called
        call_args = mock_play.call_args[0]
        assert call_args[1] == 123  # rating_key
        assert call_args[3] == 'Living Room'  # room_name
        assert call_args[4] == '192.168.1.100'  # client_ip
        assert call_args[5] == 5555  # adb_port
    
    def test_resolve_fallback_to_first_device_without_plex(self, mock_settings, mock_plex_service):
        """Falls back to first device when no Plex service present."""
        from app.main import _resolve_playback_target
        
        # Room with device but no Plex service
        room = {
            'id': 'room2',
            'name': 'Bedroom',
            'devices': [
                {
                    'id': 'dev1',
                    'host': '192.168.1.101',
                    'connectPort': 5555,
                    'platform': 'shield',
                    'services': [
                        {
                            'id': 'svc1',
                            'type': 'kodi',
                            'kodi_host': '192.168.1.101',
                            'kodi_port': 8080,
                        }
                    ]
                }
            ]
        }
        
        mock_settings.get_room_by_id.return_value = room
        
        with patch('app.main.settings', mock_settings):
            with patch('app.main.plex_service', mock_plex_service):
                with patch('app.main._play_via_kodi') as mock_play:
                    with patch('app.device_wake.ensure_device_ready', return_value={'ok': True}):
                        mock_play.return_value = Mock(get_json=lambda: {'success': True})
                        
                        result = _resolve_playback_target(rating_key=456, room_id='room2')
        
        assert result['success'] is True
        # Verify _play_via_kodi was called
        assert mock_play.called
    
    def test_resolve_prefers_device_with_plex_service(self, mock_settings, mock_plex_service):
        """Prefers device with Plex service over others."""
        from app.main import _resolve_playback_target
        
        # Room with multiple devices, second has Plex
        room = {
            'id': 'room3',
            'name': 'Theater',
            'devices': [
                {
                    'id': 'dev1',
                    'host': '192.168.1.102',
                    'connectPort': 5555,
                    'services': [{'id': 'svc1', 'type': 'kodi'}]
                },
                {
                    'id': 'dev2',
                    'host': '192.168.1.103',
                    'connectPort': 5556,
                    'services': [
                        {'id': 'svc2', 'type': 'plex', 'plex_client_port': 32500}
                    ]
                }
            ]
        }
        
        mock_settings.get_room_by_id.return_value = room
        
        with patch('app.main.settings', mock_settings):
            with patch('app.main.plex_service', mock_plex_service):
                with patch('app.main._play_via_plex') as mock_play:
                    with patch('app.device_wake.ensure_device_ready', return_value={'ok': True}):
                        mock_play.return_value = Mock(get_json=lambda: {'success': True})
                        
                        result = _resolve_playback_target(rating_key=789, room_id='room3')
        
        # Should use dev2 (the one with Plex)
        call_args = mock_play.call_args[0]
        assert call_args[4] == '192.168.1.103'  # client_ip from dev2
        assert call_args[5] == 5556  # adb_port from dev2


class TestResolvePlaybackTargetLegacyFormat:
    """Test _resolve_playback_target with legacy room-level format."""
    
    def test_resolve_with_room_level_ip(self, mock_settings, mock_plex_service):
        """Resolves using room-level ip and adb_port (legacy format)."""
        from app.main import _resolve_playback_target
        
        # Room with legacy format (no nested devices)
        room = {
            'id': 'room4',
            'name': 'Guest Room',
            'ip': '192.168.1.110',
            'adb_port': 5555,
        }
        
        mock_settings.get_room_by_id.return_value = room
        
        with patch('app.main.settings', mock_settings):
            with patch('app.main.plex_service', mock_plex_service):
                with patch('app.main._play_via_kodi') as mock_play:
                    with patch('app.device_wake.ensure_device_ready', return_value={'ok': True}):
                        mock_play.return_value = Mock(get_json=lambda: {'success': True})
                        
                        result = _resolve_playback_target(rating_key=111, room_id='room4')
        
        assert result['success'] is True
        call_args = mock_play.call_args[0]
        assert call_args[3] == 'Guest Room'  # room_name
        assert call_args[4] == '192.168.1.110'  # client_ip from room.ip
        assert call_args[5] == 5555  # adb_port from room
    
    def test_resolve_with_host_connectport_legacy(self, mock_settings, mock_plex_service):
        """Resolves using room-level host/connectPort (legacy alternative names)."""
        from app.main import _resolve_playback_target
        
        # Room with host/connectPort instead of ip/adb_port
        room = {
            'id': 'room5',
            'name': 'Office',
            'host': '192.168.1.111',
            'connectPort': 5556,
        }
        
        mock_settings.get_room_by_id.return_value = room
        
        with patch('app.main.settings', mock_settings):
            with patch('app.main.plex_service', mock_plex_service):
                with patch('app.main._play_via_kodi') as mock_play:
                    with patch('app.device_wake.ensure_device_ready', return_value={'ok': True}):
                        mock_play.return_value = Mock(get_json=lambda: {'success': True})
                        
                        result = _resolve_playback_target(rating_key=222, room_id='room5')
        
        assert result['success'] is True
        call_args = mock_play.call_args[0]
        assert call_args[4] == '192.168.1.111'  # client_ip from room.host
        assert call_args[5] == 5556  # adb_port from room.connectPort
    
    def test_error_when_room_not_found(self, mock_settings):
        """Returns error when room ID doesn't exist."""
        from app.main import _resolve_playback_target
        
        mock_settings.get_room_by_id.return_value = None
        
        with patch('app.main.settings', mock_settings):
            result = _resolve_playback_target(rating_key=333, room_id='nonexistent')
        
        assert result['success'] is False
        assert 'Room not found' in result['error']
    
    def test_error_when_no_ip_resolvable(self, mock_settings):
        """Returns error when room has no IP address."""
        from app.main import _resolve_playback_target
        
        # Room with no IP fields
        room = {'id': 'room6', 'name': 'Bad Room'}
        mock_settings.get_room_by_id.return_value = room
        
        with patch('app.main.settings', mock_settings):
            result = _resolve_playback_target(rating_key=444, room_id='room6')
        
        assert result['success'] is False
        assert 'Could not resolve device IP' in result['error']
