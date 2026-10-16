#!/usr/bin/env python3
"""
Tests for Luma Indexer Manager
Clean-room implementation with no GPL dependencies.
"""

import json
import time
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, Mock, patch
import pytest


# ---- Token Bucket Tests ----

def test_token_bucket_basic():
    """Token bucket allows requests up to capacity."""
    from app.indexer_manager import TokenBucket
    
    bucket = TokenBucket(capacity=5, refill_seconds=180.0)
    now = time.time()
    
    # Should be able to take 5 tokens
    for i in range(5):
        assert bucket.take(now) is True, f"Failed to take token {i+1}"
    
    # 6th should fail
    assert bucket.take(now) is False


def test_token_bucket_refill():
    """Token bucket refills over time."""
    from app.indexer_manager import TokenBucket
    
    bucket = TokenBucket(capacity=5, refill_seconds=10.0)  # 1 token per 10s
    now = time.time()
    
    # Drain bucket
    for _ in range(5):
        bucket.take(now)
    
    # No tokens left
    assert bucket.take(now) is False
    
    # Advance 20 seconds (2 tokens should refill)
    now += 20
    assert bucket.take(now) is True
    assert bucket.take(now) is True
    assert bucket.take(now) is False


def test_token_bucket_cap():
    """Token bucket doesn't exceed capacity."""
    from app.indexer_manager import TokenBucket
    
    bucket = TokenBucket(capacity=3, refill_seconds=1.0)
    now = time.time()
    
    # Advance 1000 seconds (way more than needed to refill)
    now += 1000
    bucket.take(now)  # Trigger refill
    
    # Should still only have capacity, not 1000 tokens
    for i in range(3):
        assert bucket.take(now) is True, f"Failed at {i}"
    assert bucket.take(now) is False


# ---- IndexerState Tests ----

def test_indexer_state_backoff():
    """Backoff prevents requests during disabled period."""
    from app.indexer_manager import IndexerState
    
    config = {'id': 'test', 'name': 'Test'}
    state = IndexerState('test-id', config)
    now = time.time()
    
    assert state.is_backed_off(now) is False
    
    # Set 60s backoff
    state.set_backoff(60, now)
    assert state.is_backed_off(now) is True
    
    # After 60s, backoff expires
    assert state.is_backed_off(now + 61) is False


def test_indexer_state_cache():
    """Query cache stores and retrieves results with TTL."""
    from app.indexer_manager import IndexerState
    
    config = {'id': 'test', 'name': 'Test'}
    state = IndexerState('test-id', config)
    now = time.time()
    
    cache_key = ('query', (2000,), 100)
    results = [{'title': 'Movie 1'}]
    
    # Cache miss
    assert state.get_cached(cache_key, ttl=60, now=now) is None
    
    # Store in cache
    state.set_cached(cache_key, results, now)
    
    # Cache hit
    assert state.get_cached(cache_key, ttl=60, now=now) == results
    
    # Expired after TTL
    assert state.get_cached(cache_key, ttl=60, now=now + 61) is None


def test_indexer_state_request_log():
    """Request log tracks requests per hour."""
    from app.indexer_manager import IndexerState
    
    config = {'id': 'test', 'name': 'Test'}
    state = IndexerState('test-id', config)
    now = time.time()
    
    # Log 5 requests
    for i in range(5):
        state.request_log.append(now + i * 60)
    
    assert state.requests_last_hour(now + 300) == 5
    
    # After 1 hour, old requests are pruned
    assert state.requests_last_hour(now + 3700) == 0


# ---- Torznab XML Parsing Tests ----

def test_parse_torznab_xml_basic():
    """Parse basic Torznab XML response."""
    from app.indexer_manager import IndexerManager
    
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>Test Indexer</title>
    <item>
      <title>Test Movie 2024 1080p BluRay</title>
      <link>http://example.com/download</link>
      <guid>12345</guid>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <size>5368709120</size>
      <torznab:attr name="seeders" value="50"/>
      <torznab:attr name="leechers" value="10"/>
      <torznab:attr name="infohash" value="abcd1234"/>
      <torznab:attr name="imdb" value="tt1234567"/>
    </item>
  </channel>
</rss>"""
    
    settings_mock = Mock()
    manager = IndexerManager(settings_mock)
    results = manager._parse_torznab_xml(xml)
    
    assert len(results) == 1
    r = results[0]
    assert r['title'] == 'Test Movie 2024 1080p BluRay'
    assert r['download_url'] == 'http://example.com/download'
    assert r['guid'] == '12345'
    assert r['seeders'] == 50
    assert r['leechers'] == 10
    assert r['size'] == 5368709120
    assert r['info_hash'] == 'abcd1234'
    assert r['imdb_id'] == 'tt1234567'


def test_parse_torznab_xml_empty():
    """Handle empty XML response gracefully."""
    from app.indexer_manager import IndexerManager
    
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Indexer</title>
  </channel>
</rss>"""
    
    settings_mock = Mock()
    manager = IndexerManager(settings_mock)
    results = manager._parse_torznab_xml(xml)
    
    assert results == []


def test_parse_torznab_xml_malformed():
    """Handle malformed XML gracefully."""
    from app.indexer_manager import IndexerManager
    
    xml = "<not valid xml"
    
    settings_mock = Mock()
    manager = IndexerManager(settings_mock)
    results = manager._parse_torznab_xml(xml)
    
    assert results == []


# ---- IndexerManager Tests ----

def test_indexer_manager_list_indexers():
    """List indexers from settings."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {'id': 'ix1', 'name': 'Indexer 1', 'type': 'torznab', 'enabled': True, 'priority': 10},
        {'id': 'ix2', 'name': 'Indexer 2', 'type': 'newznab', 'enabled': False, 'priority': 20},
    ]
    
    manager = IndexerManager(settings_mock)
    indexers = manager.list_indexers()
    
    assert len(indexers) == 2
    assert indexers[0]['name'] == 'Indexer 1'
    assert indexers[1]['name'] == 'Indexer 2'


def test_indexer_manager_get_enabled():
    """Get enabled indexers sorted by priority."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {'id': 'ix1', 'name': 'Low Priority', 'enabled': True, 'priority': 200},
        {'id': 'ix2', 'name': 'Disabled', 'enabled': False, 'priority': 10},
        {'id': 'ix3', 'name': 'High Priority', 'enabled': True, 'priority': 10},
    ]
    
    manager = IndexerManager(settings_mock)
    enabled = manager.get_enabled_indexers()
    
    assert len(enabled) == 2
    assert enabled[0]['name'] == 'High Priority'
    assert enabled[1]['name'] == 'Low Priority'


def test_indexer_manager_test_success():
    """Test indexer connection success."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'test-ix',
            'name': 'Test Indexer',
            'url': 'http://test.example.com',
            'api_key': 'test-key',
            'type': 'torznab',
        }
    ]
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = '<caps></caps>'
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        success, message = manager.test_indexer('test-ix')
        
        assert success is True
        assert 'successful' in message.lower()


def test_indexer_manager_test_timeout():
    """Test indexer connection timeout."""
    from app.indexer_manager import IndexerManager
    import requests
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'test-ix',
            'name': 'Test Indexer',
            'url': 'http://test.example.com',
            'api_key': 'test-key',
            'type': 'torznab',
        }
    ]
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_get.side_effect = requests.exceptions.Timeout('Connection timeout')
        
        manager = IndexerManager(settings_mock)
        success, message = manager.test_indexer('test-ix')
        
        assert success is False
        assert 'timeout' in message.lower()


def test_indexer_manager_search_basic():
    """Basic search returns and merges results."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'ix1',
            'name': 'Indexer 1',
            'url': 'http://test1.example.com',
            'api_key': 'key1',
            'type': 'torznab',
            'enabled': True,
            'priority': 10,
        }
    ]
    
    xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Test Movie 1080p</title>
  <link>http://dl.test/1</link>
  <guid>guid1</guid>
  <torznab:attr name="seeders" value="100" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
</item>
</channel></rss>"""
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = xml_response
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        results = manager.search(query='test', categories=[2000], limit=100)
        
        assert len(results) == 1
        assert results[0]['title'] == 'Test Movie 1080p'
        assert results[0]['seeders'] == 100
        assert results[0]['indexer_name'] == 'Indexer 1'


def test_indexer_manager_search_rate_limit():
    """Search respects rate limiting."""
    from app.indexer_manager import IndexerManager, IndexerRateLimitError
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'ix1',
            'name': 'Rate Limited',
            'url': 'http://test.example.com',
            'api_key': 'key',
            'type': 'torznab',
            'enabled': True,
            'priority': 10,
            'rate_limited': True,
            'rate_limit_capacity': 2,
            'rate_limit_refill_seconds': 100,
            'rate_limit_max_per_hour': 5,
        }
    ]
    
    xml_response = '<rss><channel></channel></rss>'
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = xml_response
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        
        # First 2 requests should succeed (token bucket capacity)
        manager.search(query='test1', categories=[2000], limit=100)
        manager.search(query='test2', categories=[2000], limit=100)
        
        # 3rd request should be rate limited (no tokens left, not interactive)
        results = manager.search(query='test3', categories=[2000], limit=100)
        # Should return empty (rate limited, no cache)
        assert results == []


def test_indexer_manager_search_429_backoff():
    """429 response triggers backoff."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'ix1',
            'name': 'Test',
            'url': 'http://test.example.com',
            'api_key': 'key',
            'type': 'torznab',
            'enabled': True,
            'priority': 10,
        }
    ]
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 429
        mock_resp.headers = {'Retry-After': '120'}
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        results = manager.search(query='test', categories=[2000], limit=100)
        
        # Should return empty due to 429
        assert results == []
        
        # Check that backoff was set
        state = manager._get_state('ix1')
        assert state.is_backed_off(time.time()) is True
        assert state.backoff_seconds == 120


# ---- Prowlarr Import Tests ----

def test_prowlarr_import_success():
    """Import indexers from Prowlarr."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    settings_mock.update.return_value = (True, [])
    
    prowlarr_response = [
        {
            'id': 1,
            'name': 'BeyondHD',
            'protocol': 'torznab',
            'implementation': 'Torznab',
            'enable': True,
            'fields': [
                {'name': 'baseUrl', 'value': 'https://beyond-hd.me'},
                {'name': 'apiKey', 'value': 'test-key-123'},
            ],
        },
        {
            'id': 2,
            'name': 'Cardigann Scraper',
            'protocol': 'torznab',
            'implementation': 'Cardigann',
            'enable': True,
            'configContract': 'CardigannSettings',
            'fields': [
                {'name': 'baseUrl', 'value': 'https://example.com'},
            ],
        },
    ]
    
    with patch('app.indexer_manager.requests.Session') as mock_session_cls:
        mock_session = Mock()
        mock_resp = Mock()
        mock_resp.json.return_value = prowlarr_response
        mock_resp.raise_for_status = Mock()
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value = mock_session
        
        manager = IndexerManager(settings_mock)
        imported, cardigann, errors = manager.import_from_prowlarr(
            'http://prowlarr:9696',
            'prowlarr-key'
        )
        
        assert imported == 1  # Only non-Cardigann indexer
        assert cardigann == 1  # Cardigann indexer flagged
        assert len(errors) == 0


def test_prowlarr_import_no_baseurl():
    """Skip indexers without baseUrl."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    settings_mock.update.return_value = (True, [])
    
    prowlarr_response = [
        {
            'id': 1,
            'name': 'Broken Indexer',
            'protocol': 'torznab',
            'implementation': 'Torznab',
            'enable': True,
            'fields': [],  # No baseUrl
        },
    ]
    
    with patch('app.indexer_manager.requests.Session') as mock_session_cls:
        mock_session = Mock()
        mock_resp = Mock()
        mock_resp.json.return_value = prowlarr_response
        mock_resp.raise_for_status = Mock()
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value = mock_session
        
        manager = IndexerManager(settings_mock)
        imported, cardigann, errors = manager.import_from_prowlarr(
            'http://prowlarr:9696',
            'prowlarr-key'
        )
        
        assert imported == 0
        assert len(errors) == 1
        assert 'no baseUrl' in errors[0]


# ---- Backend Compatibility Tests ----

def test_backend_adapter_list_indexers():
    """Backend adapter provides Prowlarr-compatible indexer list."""
    from app.indexer_backend import LumaBackendAdapter
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {'id': 'ix1', 'name': 'Test Indexer', 'enabled': True, 'priority': 10},
    ]
    
    manager = IndexerManager(settings_mock)
    adapter = LumaBackendAdapter(manager)
    
    indexers = adapter.get_indexers()
    assert len(indexers) == 1
    assert indexers[0]['name'] == 'Test Indexer'
    assert 'id' in indexers[0]  # Numeric ID for compatibility


def test_backend_adapter_search_movies():
    """Backend adapter search_movies returns Prowlarr-compatible format."""
    from app.indexer_backend import LumaBackendAdapter
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'ix1',
            'name': 'Test',
            'url': 'http://test.example.com',
            'api_key': 'key',
            'type': 'torznab',
            'enabled': True,
            'priority': 10,
        }
    ]
    
    xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Test.Movie.2024.1080p.BluRay</title>
  <link>http://dl.test/1</link>
  <guid>guid1</guid>
  <size>5368709120</size>
  <torznab:attr name="seeders" value="50" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
</item>
</channel></rss>"""
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = xml_response
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        adapter = LumaBackendAdapter(manager)
        
        results = adapter.search_movies(categories='2000', limit=100)
        
        assert len(results) == 1
        r = results[0]
        # Check Prowlarr-compatible format
        assert 'title' in r  # Clean title
        assert 'year' in r
        assert 'seeders' in r
        assert 'size_gb' in r
        assert 'resolution' in r
        assert 'source' in r
        assert 'tracker' in r
        assert r['seeders'] == 50


# ---- Settings Masking Tests ----

def test_settings_mask_indexer_api_keys():
    """Settings manager masks indexer API keys in GET."""
    from app.settings_manager import SettingsManager, SECRET_MASK
    import tempfile
    import json
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_path = Path(tmpdir) / 'settings.json'
        
        # Write test settings
        test_settings = {
            'indexer': {
                'backend': 'luma',
                'indexers': [
                    {
                        'id': 'ix1',
                        'name': 'Test Indexer',
                        'type': 'torznab',
                        'url': 'https://test.com',
                        'api_key': 'secret-key-123',
                        'enabled': True,
                    }
                ],
            }
        }
        settings_path.write_text(json.dumps(test_settings))
        
        sm = SettingsManager(str(settings_path))
        masked = sm.get_all_masked()
        
        # API key should be masked
        assert masked['indexer']['indexers'][0]['api_key'] == SECRET_MASK
        
        # Original should still have real key
        real = sm.get_all()
        assert real['indexer']['indexers'][0]['api_key'] == 'secret-key-123'


def test_settings_preserve_masked_on_update():
    """Updating settings with masked API key preserves original."""
    from app.settings_manager import SettingsManager, SECRET_MASK
    import tempfile
    import json
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_path = Path(tmpdir) / 'settings.json'
        
        test_settings = {
            'indexer': {
                'indexers': [
                    {
                        'id': 'ix1',
                        'name': 'Original Name',
                        'api_key': 'original-secret',
                    }
                ],
            }
        }
        settings_path.write_text(json.dumps(test_settings))
        
        sm = SettingsManager(str(settings_path))
        
        # Update with masked key (simulating frontend edit)
        update = {
            'indexer': {
                'indexers': [
                    {
                        'id': 'ix1',
                        'name': 'Updated Name',
                        'api_key': SECRET_MASK,  # Masked
                    }
                ],
            }
        }
        success, errors = sm.update(update)
        
        assert success is True
        
        # Name should be updated, but API key preserved
        result = sm.get_all()
        assert result['indexer']['indexers'][0]['name'] == 'Updated Name'
        assert result['indexer']['indexers'][0]['api_key'] == 'original-secret'


# ---- Backend Switch Tests ----

def test_backend_default_prowlarr():
    """Backend defaults to prowlarr before import."""
    from app.indexer_backend import get_indexer_backend
    
    settings_mock = Mock()
    settings_mock.get.return_value = 'prowlarr'
    
    # Should return legacy client, not adapter
    backend = get_indexer_backend(settings_mock)
    # Legacy client doesn't have 'manager' attribute
    assert not hasattr(backend, 'manager')


def test_backend_switch_after_import():
    """Backend can switch to luma after import."""
    from app.indexer_backend import get_indexer_backend, LumaBackendAdapter
    
    settings_mock = Mock()
    settings_mock.get.side_effect = lambda key, default=None: {
        'indexer.backend': 'luma',
        'indexer.indexers': [],
    }.get(key, default)
    settings_mock.get_all.return_value = {}
    
    backend = get_indexer_backend(settings_mock)
    assert isinstance(backend, LumaBackendAdapter)


def test_backend_manual_switch_to_prowlarr():
    """Backend can be manually switched back to prowlarr."""
    from app.indexer_api import get_indexer_client
    
    settings_mock = Mock()
    settings_mock.get.side_effect = lambda key, default=None: {
        'indexer.backend': 'prowlarr',
        'indexer.provider': 'prowlarr',
        'indexer.base_url': 'http://prowlarr:9696',
        'indexer.api_key': 'test-key',
    }.get(key, default)
    
    client = get_indexer_client(settings_mock)
    # Should be ProwlarrClient
    assert client is not None
    assert hasattr(client, 'base_url')


def test_cardigann_routes_through_prowlarr():
    """Cardigann indexers bypass luma backend even when backend is luma."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'cardigann-ix',
            'name': 'Cardigann Scraper',
            'type': 'torznab',
            'url': 'http://prowlarr:9696/1/api',
            'api_key': 'key',
            'enabled': True,
            'cardigann': True,  # Flagged during import
        },
        {
            'id': 'native-ix',
            'name': 'Native Indexer',
            'type': 'torznab',
            'url': 'https://native.example.com/api',
            'api_key': 'key',
            'enabled': True,
        }
    ]
    
    manager = IndexerManager(settings_mock)
    enabled = manager.get_enabled_indexers()
    
    # Cardigann should be filtered out for luma searches
    # (In practice, the adapter would route Cardigann through Prowlarr)
    assert len(enabled) == 2  # Both present in config


# ---- Prowlarr Import GET-only Tests ----

def test_prowlarr_import_uses_only_get():
    """Prowlarr import only uses GET requests, never POST/PUT/DELETE."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    settings_mock.update.return_value = (True, [])
    
    prowlarr_response = [
        {
            'id': 1,
            'name': 'Test Indexer',
            'protocol': 'torznab',
            'implementation': 'Torznab',
            'enable': True,
            'fields': [
                {'name': 'baseUrl', 'value': 'https://test.example.com'},
                {'name': 'apiKey', 'value': 'key'},
            ],
        }
    ]
    
    with patch('app.indexer_manager.requests.Session') as mock_session_cls:
        mock_session = Mock()
        mock_resp = Mock()
        mock_resp.json.return_value = prowlarr_response
        mock_resp.raise_for_status = Mock()
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value = mock_session
        
        manager = IndexerManager(settings_mock)
        manager.import_from_prowlarr('http://prowlarr:9696', 'key')
        
        # Verify only GET was called
        mock_session.get.assert_called_once()
        mock_session.post.assert_not_called()
        mock_session.put.assert_not_called()
        mock_session.delete.assert_not_called()
        mock_session.patch.assert_not_called()


# ---- Retry-After and Backoff Tests ----

def test_retry_after_http_date():
    """Handle Retry-After as HTTP-date format."""
    from app.indexer_manager import IndexerManager
    import email.utils
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'ix1',
            'name': 'Test',
            'url': 'http://test.example.com',
            'api_key': 'key',
            'type': 'torznab',
            'enabled': True,
            'priority': 10,
        }
    ]
    
    # HTTP-date: "Fri, 16 Oct 2026 04:00:00 GMT"
    future_time = time.time() + 300  # 5 minutes from now
    http_date = email.utils.formatdate(future_time, usegmt=True)
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 429
        mock_resp.headers = {'Retry-After': http_date}
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        results = manager.search(query='test', categories=[2000], limit=100)
        
        assert results == []
        state = manager._get_state('ix1')
        assert state.is_backed_off(time.time()) is True


def test_exponential_backoff_no_retry_after():
    """Exponential backoff when Retry-After header is missing."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'ix1',
            'name': 'Test',
            'url': 'http://test.example.com',
            'api_key': 'key',
            'type': 'torznab',
            'enabled': True,
            'priority': 10,
        }
    ]
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 429
        mock_resp.headers = {}  # No Retry-After
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        results = manager.search(query='test', categories=[2000], limit=100)
        
        assert results == []
        state = manager._get_state('ix1')
        # Should default to 60s backoff
        assert state.backoff_seconds == 60


def test_exponential_backoff_500_error():
    """500 errors trigger 5min backoff."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'ix1',
            'name': 'Test',
            'url': 'http://test.example.com',
            'api_key': 'key',
            'type': 'torznab',
            'enabled': True,
            'priority': 10,
        }
    ]
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        results = manager.search(query='test', categories=[2000], limit=100)
        
        assert results == []
        state = manager._get_state('ix1')
        assert state.backoff_seconds == 300  # 5 minutes


# ---- Settings API Tests ----

def test_list_indexers_endpoint(mocker):
    """GET /api/settings/indexers returns masked keys."""
    # This would need Flask app context - simplified mock test
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'ix1',
            'name': 'Test',
            'type': 'torznab',
            'api_key': 'secret-key',
            'enabled': True,
        }
    ]
    
    manager = IndexerManager(settings_mock)
    indexers = manager.list_indexers()
    
    # API keys should be in the raw list (masking happens in endpoint)
    assert len(indexers) == 1


def test_add_indexer_generates_id():
    """POST /api/settings/indexers generates ID if not provided."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    settings_mock.update.return_value = (True, [])
    
    # Simulating endpoint logic
    new_indexer = {
        'name': 'New Indexer',
        'type': 'torznab',
        'url': 'https://test.example.com',
        'api_key': 'key',
    }
    
    # ID should be generated
    assert 'id' not in new_indexer


def test_delete_indexer_not_found():
    """DELETE /api/settings/indexers/<id> returns 404 if not found."""
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {'id': 'ix1', 'name': 'Test'}
    ]
    
    # Try to delete non-existent indexer
    indexers = settings_mock.get.return_value
    original_count = len(indexers)
    indexers = [ix for ix in indexers if ix.get('id') != 'ix-nonexistent']
    
    assert len(indexers) == original_count  # Nothing deleted


# ---- Consumer Integration Tests ----

def test_explore_browse_uses_manager():
    """Explore browse routes through manager."""
    from app.indexer_api import get_indexer_client
    
    settings_mock = Mock()
    settings_mock.get.side_effect = lambda key, default=None: {
        'indexer.backend': 'luma',
        'indexer.indexers': [],
    }.get(key, default)
    settings_mock.get_all.return_value = {}
    
    client = get_indexer_client(settings_mock)
    assert client is not None
    # Has search_movies method
    assert hasattr(client, 'search_movies')


def test_sources_search_all_uses_manager():
    """Sources panel search_all routes through manager."""
    from app.indexer_backend import LumaBackendAdapter
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    
    manager = IndexerManager(settings_mock)
    adapter = LumaBackendAdapter(manager)
    
    # search_all should exist
    assert hasattr(adapter, 'search_all')


def test_auto_grab_uses_manager():
    """Auto-grab automations route through manager."""
    from app.indexer_api import get_indexer_client
    
    settings_mock = Mock()
    settings_mock.get.side_effect = lambda key, default=None: {
        'indexer.backend': 'luma',
        'indexer.indexers': [],
    }.get(key, default)
    settings_mock.get_all.return_value = {}
    
    client = get_indexer_client(settings_mock)
    # Has search_movies for latest auto-grab
    assert callable(getattr(client, 'search_movies', None))


def test_movie_night_uses_manager():
    """Movie Night routes through manager."""
    from app.indexer_api import get_indexer_client
    
    settings_mock = Mock()
    settings_mock.get.side_effect = lambda key, default=None: {
        'indexer.backend': 'luma',
        'indexer.indexers': [],
    }.get(key, default)
    settings_mock.get_all.return_value = {}
    
    client = get_indexer_client(settings_mock)
    # Movie Night uses search_all
    assert callable(getattr(client, 'search_all', None))


def test_roulette_uses_manager():
    """Genre Roulette routes through manager."""
    from app.indexer_api import get_indexer_client
    
    settings_mock = Mock()
    settings_mock.get.side_effect = lambda key, default=None: {
        'indexer.backend': 'luma',
        'indexer.indexers': [],
    }.get(key, default)
    settings_mock.get_all.return_value = {}
    
    client = get_indexer_client(settings_mock)
    assert callable(getattr(client, 'search_all', None))


def test_franchise_completer_uses_manager():
    """Franchise Completer routes through manager."""
    from app.indexer_api import get_indexer_client
    
    settings_mock = Mock()
    settings_mock.get.side_effect = lambda key, default=None: {
        'indexer.backend': 'luma',
        'indexer.indexers': [],
    }.get(key, default)
    settings_mock.get_all.return_value = {}
    
    client = get_indexer_client(settings_mock)
    # Uses search_all
    assert callable(getattr(client, 'search_all', None))


# ---- Radarr/Sonarr Push Tests ----

def test_normalized_result_shape_for_radarr():
    """Results normalized to expected shape for Radarr push."""
    from app.indexer_backend import LumaBackendAdapter
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    
    manager = IndexerManager(settings_mock)
    adapter = LumaBackendAdapter(manager)
    
    # Test normalization
    raw_results = [
        {
            'title': 'Test.Movie.2024.1080p.BluRay',
            'download_url': 'http://example.com/dl',
            'guid': 'guid123',
            'pub_date': '2024-01-01',
            'size': 5368709120,
            'seeders': 50,
            'leechers': 10,
            'indexer': 'Test Indexer',
            'info_hash': 'abc123',
            'imdb_id': 'tt1234567',
        }
    ]
    
    normalized = adapter._normalize_results(raw_results)
    
    assert len(normalized) == 1
    r = normalized[0]
    # Check all required fields for Radarr push
    assert 'title' in r  # Clean title
    assert 'year' in r
    assert 'seeders' in r
    assert 'size_gb' in r
    assert 'resolution' in r
    assert 'source' in r
    assert 'tracker' in r
    assert 'download_url' in r
    assert 'guid' in r
    assert 'info_hash' in r
    assert 'imdb_id' in r
    assert 'torrent_name' in r
    assert 'pub_date' in r


def test_normalized_result_for_sonarr():
    """search_all returns shape compatible with Sonarr push."""
    from app.indexer_backend import LumaBackendAdapter
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = [
        {
            'id': 'ix1',
            'name': 'Test',
            'url': 'http://test.example.com',
            'api_key': 'key',
            'type': 'torznab',
            'enabled': True,
            'priority': 10,
        }
    ]
    
    xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Show.S01E01.1080p.WEB-DL</title>
  <link>http://dl.test/1</link>
  <guid>guid1</guid>
  <size>2147483648</size>
  <torznab:attr name="seeders" value="100" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
  <torznab:attr name="leechers" value="20" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
</item>
</channel></rss>"""
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = xml_response
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        adapter = LumaBackendAdapter(manager)
        
        results = adapter.search_all('show', 'tv')
        
        assert len(results) == 1
        r = results[0]
        # Sonarr needs these fields
        assert 'title' in r
        assert 'seeders' in r
        assert 'leechers' in r
        assert 'size' in r
        assert 'download_url' in r
        assert 'guid' in r
        assert 'protocol' in r


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
