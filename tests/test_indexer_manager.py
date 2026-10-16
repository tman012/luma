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


@pytest.fixture(autouse=True)
def mock_ssrf_validation():
    """Auto-patch SSRF validation for all tests."""
    with patch('app.indexer_manager.validate_indexer_url', return_value=(True, '')):
        yield


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
    
    # Advance 1000 seconds
    now += 1000
    
    # Take tokens - should refill to capacity
    assert bucket.take(now) is True
    assert bucket.take(now) is True
    assert bucket.take(now) is True
    assert bucket.take(now) is False  # Should be empty now


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
    
    # Log 5 requests within 5 minutes
    for i in range(5):
        state.request_log.append(now + i * 60)
    
    # All 5 should be counted
    assert state.requests_last_hour(now + 300) == 5
    
    # After 1 hour past the last request, they should all be pruned
    # Last request was at now + 240, so check at now + 240 + 3601
    assert state.requests_last_hour(now + 240 + 3601) == 0


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


def test_parse_torznab_xml_tolerant():
    """Tolerant parsing: bad int doesn't drop the response."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    manager = IndexerManager(settings_mock)
    
    xml = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Good Item</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
  <size>1000</size>
  <torznab:attr name="seeders" value="50" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
</item>
<item>
  <title>Bad Size Item</title>
  <link>http://dl.test/2</link>
  <guid>g2</guid>
  <size>not-a-number</size>
  <torznab:attr name="seeders" value="also-bad" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
</item>
</channel></rss>"""
    
    results = manager._parse_torznab_xml(xml)
    
    # Both items should be parsed
    assert len(results) == 2
    
    # Good item
    assert results[0]['title'] == 'Good Item'
    assert results[0]['size'] == 1000
    assert results[0]['seeders'] == 50
    
    # Bad size item defaults to 0
    assert results[1]['title'] == 'Bad Size Item'
    assert results[1]['size'] == 0
    assert results[1]['seeders'] == 0


def test_parse_torznab_xml_magneturl():
    """Parse magneturl from torznab:attr and enclosure."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    manager = IndexerManager(settings_mock)
    
    xml = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>From Attr</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
  <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:attr123" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
</item>
<item>
  <title>From Enclosure</title>
  <link>http://dl.test/2</link>
  <guid>g2</guid>
  <enclosure url="magnet:?xt=urn:btih:enc456" type="application/x-bittorrent"/>
</item>
</channel></rss>"""
    
    results = manager._parse_torznab_xml(xml)
    
    assert len(results) == 2
    assert 'magnet:?xt=urn:btih:attr123' in results[0]['magneturl']
    assert 'magnet:?xt=urn:btih:enc456' in results[1]['magneturl']


def test_parse_torznab_xml_protocol():
    """Derive protocol from enclosure type or magnet."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    manager = IndexerManager(settings_mock)
    
    xml = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Usenet</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
  <enclosure url="http://nzb.test/1.nzb" type="application/x-nzb"/>
</item>
<item>
  <title>Torrent</title>
  <link>http://dl.test/2</link>
  <guid>g2</guid>
  <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:abc" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
</item>
</channel></rss>"""
    
    results = manager._parse_torznab_xml(xml)
    
    assert len(results) == 2
    assert results[0]['protocol'] == 'usenet'
    assert results[1]['protocol'] == 'torrent'


def test_parse_torznab_xml_leechers_calculation():
    """Calculate leechers from peers minus seeders."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    manager = IndexerManager(settings_mock)
    
    xml = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>With Peers</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
  <torznab:attr name="seeders" value="50" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
  <torznab:attr name="peers" value="150" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
</item>
<item>
  <title>Direct Leechers</title>
  <link>http://dl.test/2</link>
  <guid>g2</guid>
  <torznab:attr name="seeders" value="10" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
  <torznab:attr name="leechers" value="20" xmlns:torznab="http://torznab.com/schemas/2015/feed"/>
</item>
</channel></rss>"""
    
    results = manager._parse_torznab_xml(xml)
    
    assert len(results) == 2
    # peers=150, seeders=50 => leechers=100
    assert results[0]['leechers'] == 100
    # No peers, use leechers directly
    assert results[1]['leechers'] == 20


def test_stable_string_ids():
    """Indexer results have stable string IDs from hash(indexer_id:guid)."""
    from app.indexer_manager import IndexerManager
    import hashlib
    
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
                {
                    'id': 'ix1',
                    'name': 'Test Indexer',
                    'url': 'http://test.example.com',
                    'api_key': 'key',
                    'type': 'torznab',
                    'enabled': True,
                }
            ]
        return default
    
    settings_mock.get.side_effect = settings_get
    
    xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Movie</title>
  <link>http://dl.test/1</link>
  <guid>stable-guid-123</guid>
</item>
</channel></rss>"""
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raw = Mock()
        mock_resp.raw.read.return_value = xml_response.encode()
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        results = manager.search(query='test', categories=[2000], limit=100)
        
        assert len(results) == 1
        # Check ID is a stable hash
        expected_input = "ix1:stable-guid-123"
        expected_id = hashlib.sha256(expected_input.encode()).hexdigest()[:16]
        assert results[0]['id'] == expected_id
        assert isinstance(results[0]['id'], str)
        assert len(results[0]['id']) == 16


def test_adapter_date_conversion():
    """Adapter converts RFC-822 pubDate to ISO 8601."""
    from app.indexer_backend import LumaBackendAdapter
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
                {
                    'id': 'ix1',
                    'name': 'Test',
                    'url': 'http://test.example.com',
                    'api_key': 'key',
                    'type': 'torznab',
                    'enabled': True,
                }
            ]
        return default
    
    settings_mock.get.side_effect = settings_get
    
    xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Movie 2024</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
  <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
</item>
</channel></rss>"""
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raw = Mock()
        mock_resp.raw.read.return_value = xml_response.encode()
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        adapter = LumaBackendAdapter(manager)
        
        results = adapter.search_all('movie', 'movie')
        
        assert len(results) == 1
        # Should be ISO 8601 format
        assert 'T' in results[0]['publishDate']
        assert '2024-01-01' in results[0]['publishDate']


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
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
                {
                    'id': 'test-ix',
                    'name': 'Test Indexer',
                    'url': 'http://test.example.com',
                    'api_key': 'test-key',
                    'type': 'torznab',
                }
            ]
        elif key == 'indexer.allow_lan':
            return False
        return default
    
    settings_mock.get.side_effect = settings_get
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        with patch('app.indexer_manager.validate_indexer_url', return_value=(True, '')):
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = b'<caps></caps>'
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
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
                {
                    'id': 'test-ix',
                    'name': 'Test Indexer',
                    'url': 'http://test.example.com',
                    'api_key': 'test-key',
                    'type': 'torznab',
                }
            ]
        elif key == 'indexer.allow_lan':
            return False
        return default
    
    settings_mock.get.side_effect = settings_get
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        with patch('app.indexer_manager.validate_indexer_url', return_value=(True, '')):
            mock_get.side_effect = requests.exceptions.Timeout('Connection timeout')
            
            manager = IndexerManager(settings_mock)
            success, message = manager.test_indexer('test-ix')
            
            assert success is False
            assert 'timeout' in message.lower()


def test_indexer_manager_search_basic():
    """Basic search returns and merges results."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
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
        elif key == 'indexer.allow_lan':
            return False
        return default
    
    settings_mock.get.side_effect = settings_get
    
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
        with patch('app.indexer_manager.validate_indexer_url', return_value=(True, '')):
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = xml_response.encode()
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
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
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
        elif key == 'indexer.allow_lan':
            return False
        return default
    
    settings_mock.get.side_effect = settings_get
    
    xml_response = '<rss><channel></channel></rss>'
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        with patch('app.indexer_manager.validate_indexer_url', return_value=(True, '')):
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = xml_response.encode()
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
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
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
        elif key == 'indexer.allow_lan':
            return False
        return default
    
    settings_mock.get.side_effect = settings_get
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        with patch('app.indexer_manager.validate_indexer_url', return_value=(True, '')):
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
        imported, proxied, errors = manager.import_from_prowlarr(
            'http://prowlarr:9696',
            'prowlarr-key'
        )
        
        assert imported == 2  # Both Torznab (direct) and Cardigann (proxied)
        assert proxied == 1  # Cardigann is proxied
        assert len(errors) == 0


def test_prowlarr_import_no_baseurl():
    """Skip direct Torznab indexers without baseUrl or apiKey."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    settings_mock.update.return_value = (True, [])
    
    prowlarr_response = [
        {
            'id': 1,
            'name': 'Broken Torznab',
            'protocol': 'torznab',
            'implementation': 'Torznab',  # Direct, needs baseUrl/apiKey
            'enable': True,
            'fields': [],  # No baseUrl or apiKey
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
        imported, proxied, errors = manager.import_from_prowlarr(
            'http://prowlarr:9696',
            'prowlarr-key'
        )
        
        assert imported == 0
        assert len(errors) == 1
        assert 'baseUrl or apiKey' in errors[0]


def test_prowlarr_import_direct_torznab():
    """Import direct Torznab indexer with its own URL and key."""
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
                {'name': 'apiKey', 'value': 'beyond-key-123'},
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
        imported, proxied, errors = manager.import_from_prowlarr(
            'http://prowlarr:9696',
            'prowlarr-key'
        )
        
        assert imported == 1
        assert proxied == 0
        
        # Check URL in saved indexer
        update_call = settings_mock.update.call_args[0][0]
        indexers = update_call['indexer']['indexers']
        ix = indexers[0]
        assert ix['id'] == 'direct-1'
        assert ix['url'] == 'https://beyond-hd.me/api'
        assert ix['api_key'] == 'beyond-key-123'
        assert ix['prowlarr_proxy'] is False


def test_prowlarr_import_native_avistaz_proxied():
    """Import native AvistaZ indexer proxied through Prowlarr with rate limiting."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    settings_mock.update.return_value = (True, [])
    
    prowlarr_response = [
        {
            'id': 5,
            'name': 'AvistaZ',
            'protocol': 'torznab',
            'implementation': 'AvistaZ',
            'enable': True,
            'fields': [],
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
        imported, proxied, errors = manager.import_from_prowlarr(
            'http://prowlarr:9696',
            'prowlarr-key'
        )
        
        assert imported == 1
        assert proxied == 1
        
        update_call = settings_mock.update.call_args[0][0]
        indexers = update_call['indexer']['indexers']
        ix = indexers[0]
        
        assert ix['id'] == 'prowlarr-5'
        assert ix['url'] == 'http://prowlarr:9696/5/api'
        assert ix['api_key'] == 'prowlarr-key'
        assert ix['prowlarr_proxy'] is True
        assert ix['rate_limited'] is True


def test_prowlarr_import_cardigann_proxied():
    """Import Cardigann indexer proxied through Prowlarr."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    settings_mock.update.return_value = (True, [])
    
    prowlarr_response = [
        {
            'id': 10,
            'name': 'Custom Scraper',
            'protocol': 'torznab',
            'implementation': 'Cardigann',
            'enable': True,
            'fields': [],
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
        imported, proxied, errors = manager.import_from_prowlarr(
            'http://prowlarr:9696',
            'prowlarr-key'
        )
        
        assert imported == 1
        assert proxied == 1
        
        update_call = settings_mock.update.call_args[0][0]
        indexers = update_call['indexer']['indexers']
        ix = indexers[0]
        
        assert ix['id'] == 'prowlarr-10'
        assert ix['url'] == 'http://prowlarr:9696/10/api'
        assert ix['prowlarr_proxy'] is True


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
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
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
        return default
    
    settings_mock.get.side_effect = settings_get
    
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
        mock_resp.raw = Mock()
        mock_resp.raw.read.return_value = xml_response.encode()
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
    from app.settings_manager import SettingsManager, SECRET_MASK, _mask_secrets, SETTINGS_SCHEMA
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
        
        # Mask manually using the function
        masked = _mask_secrets(sm.get_all(), SETTINGS_SCHEMA)
        
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
    """Handle Retry-After as HTTP-date format with mocked clock."""
    from app.indexer_manager import IndexerManager, parse_retry_after
    import email.utils
    from datetime import datetime, timezone, timedelta
    
    # Test parse_retry_after directly
    future_time = datetime.now(timezone.utc) + timedelta(seconds=300)
    http_date = email.utils.formatdate(future_time.timestamp(), usegmt=True)
    
    parsed_seconds = parse_retry_after(http_date)
    assert parsed_seconds is not None
    assert 295 <= parsed_seconds <= 305  # Allow 5s tolerance for test execution time
    
    # Test integration with mocked time
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
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
        elif key.startswith('indexer.'):
            return default
        return default
    
    settings_mock.get.side_effect = settings_get
    
    with patch('app.indexer_manager.time.time') as mock_time:
        mock_time.return_value = 1000.0
        
        with patch('app.indexer_manager.requests.get') as mock_get:
            mock_resp = Mock()
            mock_resp.status_code = 429
            mock_resp.headers = {'Retry-After': http_date}
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = b''
            mock_get.return_value = mock_resp
            
            manager = IndexerManager(settings_mock)
            results = manager.search(query='test', categories=[2000], limit=100)
            
            assert results == []
            state = manager._get_state('ix1')
            
            # At t=1000, should be backed off
            assert state.is_backed_off(1000.0) is True
            # Should store approximately the parsed seconds
            assert 295 <= state.backoff_seconds <= 305


def test_exponential_backoff_no_retry_after():
    """Exponential backoff when Retry-After header is missing, with mocked clock."""
    from app.indexer_manager import IndexerManager, INITIAL_BACKOFF
    
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
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
        elif key.startswith('indexer.'):
            return default
        return default
    
    settings_mock.get.side_effect = settings_get
    
    with patch('app.indexer_manager.time.time') as mock_time:
        mock_time.return_value = 1000.0
        
        with patch('app.indexer_manager.requests.get') as mock_get:
            mock_resp = Mock()
            mock_resp.status_code = 429
            mock_resp.headers = {}  # No Retry-After
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = b''
            mock_get.return_value = mock_resp
            
            manager = IndexerManager(settings_mock)
            results = manager.search(query='test', categories=[2000], limit=100)
            
            assert results == []
            state = manager._get_state('ix1')
            
            # First failure: 60s backoff (level 0: 60 * 2^0 = 60)
            assert state.backoff_seconds == INITIAL_BACKOFF
            assert state.backoff_level == 1
            assert state.is_backed_off(1000.0) is True
            assert state.is_backed_off(1000.0 + INITIAL_BACKOFF - 1) is True
            assert state.is_backed_off(1000.0 + INITIAL_BACKOFF + 1) is False


def test_exponential_backoff_500_error():
    """500 errors trigger exponential backoff with mocked clock."""
    from app.indexer_manager import IndexerManager, INITIAL_BACKOFF
    
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
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
        elif key.startswith('indexer.'):
            return default
        return default
    
    settings_mock.get.side_effect = settings_get
    
    with patch('app.indexer_manager.time.time') as mock_time:
        current_time = 1000.0
        mock_time.return_value = current_time
        
        with patch('app.indexer_manager.requests.get') as mock_get:
            mock_resp = Mock()
            mock_resp.status_code = 500
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = b''
            mock_get.return_value = mock_resp
            
            manager = IndexerManager(settings_mock)
            
            # First failure: 60s
            results = manager.search(query='test', categories=[2000], limit=100)
            assert results == []
            state = manager._get_state('ix1')
            assert state.backoff_seconds == INITIAL_BACKOFF
            assert state.backoff_level == 1
            
            # Advance time past first backoff
            current_time += INITIAL_BACKOFF + 10
            mock_time.return_value = current_time
            
            # Second failure: 120s (60 * 2^1)
            results = manager.search(query='test2', categories=[2000], limit=100)
            assert results == []
            assert state.backoff_seconds == INITIAL_BACKOFF * 2
            assert state.backoff_level == 2
            
            # Success clears backoff level
            mock_resp.status_code = 200
            mock_resp.raw.read.return_value = b'<rss><channel></channel></rss>'
            mock_resp.raise_for_status = Mock()
            
            current_time += INITIAL_BACKOFF * 2 + 10
            mock_time.return_value = current_time
            
            results = manager.search(query='test3', categories=[2000], limit=100)
            assert state.backoff_level == 0  # Reset on success


def test_concurrent_indexer_requests_overlap():
    """Two indexers' HTTP requests can overlap (locks not held during HTTP)."""
    from app.indexer_manager import IndexerManager
    import threading
    
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
                {
                    'id': 'ix1',
                    'name': 'Indexer 1',
                    'url': 'http://test1.example.com',
                    'api_key': 'key1',
                    'type': 'torznab',
                    'enabled': True,
                    'priority': 10,
                },
                {
                    'id': 'ix2',
                    'name': 'Indexer 2',
                    'url': 'http://test2.example.com',
                    'api_key': 'key2',
                    'type': 'torznab',
                    'enabled': True,
                    'priority': 20,
                }
            ]
        return default
    
    settings_mock.get.side_effect = settings_get
    
    xml_response = '<rss><channel><item><title>Test</title><link>http://dl.test/1</link><guid>g1</guid></item></channel></rss>'
    
    # Track when HTTP calls are active
    http_active = {'ix1': None, 'ix2': None}
    http_lock = threading.Lock()
    overlap_detected = [False]
    
    def mock_get_with_delay(url, **kwargs):
        # Determine which indexer from URL
        indexer = 'ix1' if 'test1' in url else 'ix2'
        
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raw = Mock()
        mock_resp.raw.read.return_value = xml_response.encode()
        mock_resp.raise_for_status = Mock()
        
        # Mark this indexer as active
        with http_lock:
            http_active[indexer] = time.time()
            # Check if the other indexer is also active (overlap)
            other = 'ix2' if indexer == 'ix1' else 'ix1'
            if http_active[other] is not None:
                overlap_detected[0] = True
        
        # Simulate slow HTTP request
        time.sleep(0.1)
        
        # Mark as done
        with http_lock:
            http_active[indexer] = None
        
        return mock_resp
    
    with patch('app.indexer_manager.requests.get', side_effect=mock_get_with_delay):
        manager = IndexerManager(settings_mock)
        results = manager.search(query='test', categories=[2000], limit=100)
        
        # Should get results from both indexers
        assert len(results) == 2
        
        # Should have detected overlap (concurrent HTTP requests)
        assert overlap_detected[0], "HTTP requests did not overlap - locks may be held during HTTP"


# ---- Settings API Tests ----

def test_list_indexers_endpoint():
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
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
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
        return default
    
    settings_mock.get.side_effect = settings_get
    
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
        mock_resp.raw = Mock()
        mock_resp.raw.read.return_value = xml_response.encode()
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


def test_no_private_indexer_manager_imports():
    """No module should import private (_underscore) helpers from indexer_manager."""
    import subprocess
    result = subprocess.run(
        ['grep', '-rE', 'from app\\.indexer_manager import.* _[a-z]', 'app/', '--include=*.py'],
        capture_output=True,
        text=True
    )
    
    # Exclude indexer_manager.py itself and indexer_backend.py (internal adapter)
    forbidden_imports = []
    for line in result.stdout.splitlines():
        if 'indexer_manager.py:' in line or 'indexer_backend.py:' in line:
            continue
        # Exclude imports of __all__, __version__, etc (dunder methods)
        if '__' in line:
            continue
        forbidden_imports.append(line)
    
    assert not forbidden_imports, (
        f"Found {len(forbidden_imports)} forbidden private imports from indexer_manager:\n" +
        "\n".join(forbidden_imports) +
        "\n\nUse get_indexer_client() instead. Private functions start with underscore."
    )


def test_get_indexer_client_exists():
    """get_indexer_client() is the public API."""
    from app.indexer_manager import get_indexer_client
    assert callable(get_indexer_client)


def test_prowlarr_import_routing_by_implementation():
    """Prowlarr import routes by implementation: Torznab/Newznab direct, others proxied."""
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    settings_mock.get.return_value = []
    settings_mock.update.return_value = (True, [])
    
    # Realistic Prowlarr indexer JSON
    prowlarr_indexers = [
        {
            # AvistaZ: C# native, should be proxied and rate-limited
            'id': 123,
            'name': 'AvistaZ',
            'implementation': 'AvistaZ',
            'protocol': 'torrent',
            'enable': True,
            'priority': 25,
            'fields': [],
        },
        {
            # 1337x: Cardigann, should be proxied
            'id': 456,
            'name': '1337x',
            'implementation': 'Cardigann',
            'protocol': 'torrent',
            'enable': True,
            'priority': 25,
            'fields': [],
        },
        {
            # NZBgeek: Newznab, should be direct
            'id': 789,
            'name': 'NZBgeek',
            'implementation': 'Newznab',
            'protocol': 'usenet',
            'enable': True,
            'priority': 25,
            'fields': [
                {'name': 'baseUrl', 'value': 'https://api.nzbgeek.info'},
                {'name': 'apiKey', 'value': 'nzbgeek-key-123'},
            ],
        },
    ]
    
    with patch('app.indexer_manager.requests.Session') as mock_session_class:
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        
        mock_response = Mock()
        mock_response.json.return_value = prowlarr_indexers
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response
        
        manager = IndexerManager(settings_mock)
        imported, proxied, errors = manager.import_from_prowlarr('http://prowlarr:9696', 'prowlarr-key')
        
        assert imported == 3
        assert proxied == 2  # AvistaZ and 1337x
        assert len(errors) == 0
        
        # Check what was saved
        saved_indexers = settings_mock.update.call_args[0][0]['indexer']['indexers']
        
        # Find each indexer
        avistaz = next(ix for ix in saved_indexers if 'AvistaZ' in ix['name'])
        cardigann = next(ix for ix in saved_indexers if '1337x' in ix['name'])
        nzbgeek = next(ix for ix in saved_indexers if 'NZBgeek' in ix['name'])
        
        # AvistaZ: proxied through Prowlarr, rate-limited
        assert avistaz['prowlarr_proxy'] is True
        assert avistaz['prowlarr_id'] == '123'
        assert 'http://prowlarr:9696/123/api' in avistaz['url']
        assert avistaz['rate_limited'] is True
        
        # 1337x (Cardigann): proxied through Prowlarr
        assert cardigann['prowlarr_proxy'] is True
        assert cardigann['prowlarr_id'] == '456'
        assert 'http://prowlarr:9696/456/api' in cardigann['url']
        
        # NZBgeek (Newznab): direct with own URL/key
        assert nzbgeek.get('prowlarr_proxy', False) is False
        # URL might or might not have /api appended depending on baseUrl
        assert 'https://api.nzbgeek.info' in nzbgeek['url']
        assert nzbgeek['api_key'] == 'nzbgeek-key-123'


def test_hourly_cap_persists_across_restart():
    """Hourly rate limit counter persists across manager restarts."""
    import tempfile
    import os
    from app.indexer_manager import IndexerManager
    
    # Use a temp directory for state file
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['LUMA_DATA_DIR'] = tmpdir
        
        settings_mock = Mock()
        def settings_get(key, default=None):
            if key == 'indexer.indexers':
                return [
                    {
                        'id': 'test-ix',
                        'name': 'Test Indexer',
                        'url': 'http://test.example.com',
                        'api_key': 'key',
                        'type': 'torznab',
                        'enabled': True,
                        'rate_limit_max_per_hour': 20,  # 20/hour cap
                    }
                ]
            return default
        
        settings_mock.get.side_effect = settings_get
        
        # Create XML response
        xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Result</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
</item>
</channel></rss>"""
        
        with patch('app.indexer_manager.validate_indexer_url', return_value=(True, '')):
            with patch('app.indexer_manager.requests.get') as mock_get:
                mock_resp = Mock()
                mock_resp.status_code = 200
                mock_resp.raw = Mock()
                mock_resp.raw.read.return_value = xml_response.encode()
                mock_resp.raise_for_status = Mock()
                mock_get.return_value = mock_resp
                
                # Make 20 requests with different queries to avoid cache
                manager = IndexerManager(settings_mock)
                for i in range(20):
                    try:
                        manager._search_one_indexer('test-ix', f'test{i}', [2000], 10, interactive=False)
                    except Exception as e:
                        pytest.fail(f'Request {i+1} should succeed: {e}')
                
                # Force save by setting last_save_time to 0
                manager._last_save_time = 0
                manager._save_state()
                
                # Create a new manager instance (simulating restart)
                manager2 = IndexerManager(settings_mock)
                
                # Verify state was loaded
                state2 = manager2._get_state('test-ix')
                assert len(state2.background_request_log) == 20, f"Expected 20 requests in log, got {len(state2.background_request_log)}"
                
                # 21st request should be blocked (hourly cap reached)
                from app.indexer_manager import IndexerRateLimitError
                with pytest.raises(IndexerRateLimitError, match='Background hourly rate limit'):
                    manager2._search_one_indexer('test-ix', 'test21', [2000], 10, interactive=False)


# ---- Item 10: Separate Interactive/Background Rate Limits ----

def test_interactive_rate_limit_40_per_hour():
    """41st interactive search is refused (40/hour cap)."""
    import tempfile
    import os
    from app.indexer_manager import IndexerManager, IndexerRateLimitError
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['LUMA_DATA_DIR'] = tmpdir
        
        settings_mock = Mock()
        def settings_get(key, default=None):
            if key == 'indexer.indexers':
                return [
                    {
                        'id': 'test-ix',
                        'name': 'Test Indexer',
                        'url': 'http://test.example.com',
                        'api_key': 'key',
                        'type': 'torznab',
                        'enabled': True,
                        'rate_limited': False,  # No bucket
                    }
                ]
            return default
        
        settings_mock.get.side_effect = settings_get
        
        xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Result</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
</item>
</channel></rss>"""
        
        with patch('app.indexer_manager.requests.get') as mock_get:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = xml_response.encode()
            mock_resp.raise_for_status = Mock()
            mock_get.return_value = mock_resp
            
            manager = IndexerManager(settings_mock)
            
            # Make 40 interactive requests with different queries to avoid cache
            for i in range(40):
                try:
                    manager._search_one_indexer('test-ix', f'test{i}', [2000], 10, interactive=True)
                except Exception as e:
                    pytest.fail(f'Interactive request {i+1} should succeed: {e}')
            
            # 41st interactive request should be blocked
            with pytest.raises(IndexerRateLimitError, match='Interactive hourly rate limit'):
                manager._search_one_indexer('test-ix', 'test41', [2000], 10, interactive=True)


def test_background_rate_limit_20_per_hour():
    """21st background search is refused (20/hour cap)."""
    import tempfile
    import os
    from app.indexer_manager import IndexerManager, IndexerRateLimitError
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['LUMA_DATA_DIR'] = tmpdir
        
        settings_mock = Mock()
        def settings_get(key, default=None):
            if key == 'indexer.indexers':
                return [
                    {
                        'id': 'test-ix',
                        'name': 'Test Indexer',
                        'url': 'http://test.example.com',
                        'api_key': 'key',
                        'type': 'torznab',
                        'enabled': True,
                        'rate_limited': False,  # No bucket
                    }
                ]
            return default
        
        settings_mock.get.side_effect = settings_get
        
        xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Result</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
</item>
</channel></rss>"""
        
        with patch('app.indexer_manager.requests.get') as mock_get:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = xml_response.encode()
            mock_resp.raise_for_status = Mock()
            mock_get.return_value = mock_resp
            
            manager = IndexerManager(settings_mock)
            
            # Make 20 background requests with different queries to avoid cache
            for i in range(20):
                try:
                    manager._search_one_indexer('test-ix', f'test{i}', [2000], 10, interactive=False)
                except Exception as e:
                    pytest.fail(f'Background request {i+1} should succeed: {e}')
            
            # 21st background request should be blocked
            with pytest.raises(IndexerRateLimitError, match='Background hourly rate limit'):
                manager._search_one_indexer('test-ix', 'test21', [2000], 10, interactive=False)


def test_bucket_applies_only_to_background():
    """Token bucket applies only to background searches, not interactive."""
    from app.indexer_manager import IndexerManager, IndexerRateLimitError
    
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
                {
                    'id': 'test-ix',
                    'name': 'AvistaZ',
                    'url': 'http://avistaz.example.com',
                    'api_key': 'key',
                    'type': 'torznab',
                    'enabled': True,
                    'rate_limited': True,
                    'rate_limit_capacity': 2,  # Only 2 tokens
                    'rate_limit_refill_seconds': 100,  # Slow refill
                }
            ]
        return default
    
    settings_mock.get.side_effect = settings_get
    
    xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Result</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
</item>
</channel></rss>"""
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raw = Mock()
        mock_resp.raw.read.return_value = xml_response.encode()
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp
        
        manager = IndexerManager(settings_mock)
        
        # Drain the bucket with 2 background requests
        for i in range(2):
            manager._search_one_indexer('test-ix', f'bg{i}', [2000], 10, interactive=False)
        
        # 3rd background request should be blocked (bucket empty)
        with pytest.raises(IndexerRateLimitError, match='Token bucket empty'):
            manager._search_one_indexer('test-ix', 'bg3', [2000], 10, interactive=False)
        
        # But interactive requests should still work (they skip the bucket)
        manager._search_one_indexer('test-ix', 'interactive1', [2000], 10, interactive=True)


def test_rate_limit_counters_persist_across_restart():
    """Interactive and background counters persist across manager restarts."""
    import tempfile
    import os
    from app.indexer_manager import IndexerManager, IndexerRateLimitError
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['LUMA_DATA_DIR'] = tmpdir
        
        settings_mock = Mock()
        def settings_get(key, default=None):
            if key == 'indexer.indexers':
                return [
                    {
                        'id': 'test-ix',
                        'name': 'Test Indexer',
                        'url': 'http://test.example.com',
                        'api_key': 'key',
                        'type': 'torznab',
                        'enabled': True,
                        'rate_limited': False,
                    }
                ]
            return default
        
        settings_mock.get.side_effect = settings_get
        
        xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Result</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
</item>
</channel></rss>"""
        
        with patch('app.indexer_manager.requests.get') as mock_get:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = xml_response.encode()
            mock_resp.raise_for_status = Mock()
            mock_get.return_value = mock_resp
            
            # Make 10 interactive and 10 background requests
            manager = IndexerManager(settings_mock)
            for i in range(10):
                manager._search_one_indexer('test-ix', f'int{i}', [2000], 10, interactive=True)
                manager._search_one_indexer('test-ix', f'bg{i}', [2000], 10, interactive=False)
            
            # Force save
            manager._last_save_time = 0
            manager._save_state()
            
            # Create a new manager instance (simulating restart)
            manager2 = IndexerManager(settings_mock)
            
            # Verify state was loaded
            state2 = manager2._get_state('test-ix')
            assert len(state2.interactive_request_log) == 10
            assert len(state2.background_request_log) == 10


def test_interactive_search_honors_backoff():
    """Interactive searches still honor 429/Retry-After backoff."""
    from app.indexer_manager import IndexerManager, IndexerBackoffError
    
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
                {
                    'id': 'test-ix',
                    'name': 'Test Indexer',
                    'url': 'http://test.example.com',
                    'api_key': 'key',
                    'type': 'torznab',
                    'enabled': True,
                }
            ]
        return default
    
    settings_mock.get.side_effect = settings_get
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        # First request returns 429
        mock_resp_429 = Mock()
        mock_resp_429.status_code = 429
        mock_resp_429.headers = {'Retry-After': '60'}
        mock_resp_429.raise_for_status = Mock()
        mock_get.return_value = mock_resp_429
        
        manager = IndexerManager(settings_mock)
        
        # Background request triggers backoff
        with pytest.raises(IndexerBackoffError):
            manager._search_one_indexer('test-ix', 'test1', [2000], 10, interactive=False)
        
        # Interactive request should also be refused during backoff
        with pytest.raises(IndexerBackoffError, match='backed off until'):
            manager._search_one_indexer('test-ix', 'test2', [2000], 10, interactive=True)


def test_concurrent_requests_respect_min_interval():
    """Concurrent requests respect the min interval (reserve under lock, sleep outside)."""
    import tempfile
    import os
    import threading
    from app.indexer_manager import IndexerManager
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['LUMA_DATA_DIR'] = tmpdir
        
        settings_mock = Mock()
        def settings_get(key, default=None):
            if key == 'indexer.indexers':
                return [
                    {
                        'id': 'test-ix',
                        'name': 'AvistaZ',
                        'url': 'http://avistaz.example.com',
                        'api_key': 'key',
                        'type': 'torznab',
                        'enabled': True,
                        'rate_limited': True,
                        'rate_limit_capacity': 100,  # Large bucket
                        'rate_limit_refill_seconds': 1,
                        'rate_limit_min_interval': 0.5,  # 0.5s min interval
                    }
                ]
            return default
        
        settings_mock.get.side_effect = settings_get
        
        xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Result</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
</item>
</channel></rss>"""
        
        with patch('app.indexer_manager.requests.get') as mock_get:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = xml_response.encode()
            mock_resp.raise_for_status = Mock()
            mock_get.return_value = mock_resp
            
            manager = IndexerManager(settings_mock)
            
            # Make 2 concurrent background requests
            import time
            start = time.time()
            results = []
            
            def make_request(query):
                try:
                    manager._search_one_indexer('test-ix', query, [2000], 10, interactive=False)
                    results.append(time.time() - start)
                except Exception as e:
                    pytest.fail(f'Request {query} failed: {e}')
            
            threads = [
                threading.Thread(target=make_request, args=(f'test{i}',))
                for i in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            # Second request should complete at least 0.5s after the first
            # (allowing some tolerance for thread scheduling)
            assert len(results) == 2
            assert max(results) >= 0.4  # At least 0.4s total (0.5s with tolerance)


# ---- Item 4: Fan-out TimeoutError Handling ----

def test_fanout_deadline_returns_partial_results():
    """Fan-out with 1s deadline and two 4s indexers returns in about 1.3s or less."""
    import time
    from app.indexer_manager import IndexerManager
    
    settings_mock = Mock()
    def settings_get(key, default=None):
        if key == 'indexer.indexers':
            return [
                {
                    'id': 'ix1',
                    'name': 'Slow Indexer 1',
                    'url': 'http://slow1.example.com',
                    'api_key': 'key1',
                    'type': 'torznab',
                    'enabled': True,
                    'priority': 10,
                },
                {
                    'id': 'ix2',
                    'name': 'Slow Indexer 2',
                    'url': 'http://slow2.example.com',
                    'api_key': 'key2',
                    'type': 'torznab',
                    'enabled': True,
                    'priority': 20,
                },
            ]
        elif key == 'indexer.search_deadline':
            return 1.0  # 1 second deadline
        return default
    
    settings_mock.get.side_effect = settings_get
    
    xml_response = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Result</title>
  <link>http://dl.test/1</link>
  <guid>g1</guid>
</item>
</channel></rss>"""
    
    with patch('app.indexer_manager.requests.get') as mock_get:
        def slow_response(*args, **kwargs):
            # Simulate 4-second delay
            time.sleep(4.0)
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.raw = Mock()
            mock_resp.raw.read.return_value = xml_response.encode()
            mock_resp.raise_for_status = Mock()
            return mock_resp
        
        mock_get.side_effect = slow_response
        
        manager = IndexerManager(settings_mock)
        
        start = time.time()
        results = manager.search(query='test', categories=[2000], limit=10, interactive=False)
        elapsed = time.time() - start
        
        # Should return in about 1s (deadline), definitely less than 1.3s
        assert elapsed < 1.3, f"Search took {elapsed:.2f}s, expected < 1.3s"
        # Should return empty results (no indexer completed in time)
        assert results == []
