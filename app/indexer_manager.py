#!/usr/bin/env python3
"""
Luma Indexer Manager
Clean-room Torznab/Newznab indexer manager to replace Prowlarr.

Licensing: MIT/BSD compatible libraries only (stdlib, requests, defusedxml).
No GPL code from Prowlarr, Jackett, Radarr, or Sonarr.
"""

import hashlib
import ipaddress
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from defusedxml import ElementTree as ET

logger = logging.getLogger(__name__)

# SSRF protection: disallowed IP ranges
DISALLOWED_IP_RANGES = [
    ipaddress.ip_network('127.0.0.0/8'),      # Loopback
    ipaddress.ip_network('169.254.0.0/16'),   # Link-local
    ipaddress.ip_network('10.0.0.0/8'),       # Private RFC1918
    ipaddress.ip_network('172.16.0.0/12'),    # Private RFC1918
    ipaddress.ip_network('192.168.0.0/16'),   # Private RFC1918
    ipaddress.ip_network('::1/128'),          # IPv6 loopback
    ipaddress.ip_network('fe80::/10'),        # IPv6 link-local
]

# 5 MB response body limit
MAX_RESPONSE_SIZE = 5 * 1024 * 1024


def sanitize_api_key(text: str) -> str:
    """Strip API keys from logs/exceptions."""
    if not text:
        return text
    # Remove apikey=xxx and api_key=xxx patterns
    text = re.sub(r'(apikey|api_key)=[^&\s]+', r'\1=***', text, flags=re.IGNORECASE)
    # Remove X-Api-Key: xxx patterns
    text = re.sub(r'(X-Api-Key|apikey):\s*[^\s]+', r'\1: ***', text, flags=re.IGNORECASE)
    return text


def parse_retry_after(retry_after_value: str) -> Optional[float]:
    """
    Parse Retry-After header as either seconds (int) or HTTP-date.
    
    Returns number of seconds to wait, or None if invalid.
    """
    if not retry_after_value:
        return None
    
    # Try as integer seconds first
    if retry_after_value.isdigit():
        return float(retry_after_value)
    
    # Try parsing as HTTP-date
    try:
        from email.utils import parsedate_to_datetime
        retry_time = parsedate_to_datetime(retry_after_value)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        delta = (retry_time - now).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None
    
    return None


def validate_indexer_url(url: str, allow_lan: bool = False) -> Tuple[bool, str]:
    """
    Validate indexer URL for SSRF protection.
    
    Returns (valid, error_message).
    Only http/https allowed. Rejects loopback, link-local, and private IPs unless allow_lan=True.
    """
    try:
        parsed = urlparse(url)
        
        # Only allow http/https
        if parsed.scheme not in ('http', 'https'):
            return False, f'Only http and https schemes allowed, got {parsed.scheme}'
        
        hostname = parsed.hostname
        if not hostname:
            return False, 'Missing hostname'
        
        # Resolve to IP
        import socket
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror as e:
            return False, f'DNS resolution failed: {e}'
        
        if not addr_info:
            return False, 'DNS returned no addresses'
        
        # Check all resolved addresses
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            
            # Cloud metadata endpoint
            if ip_str == '169.254.169.254':
                return False, 'Cloud metadata endpoint blocked'
            
            # Check disallowed ranges
            for network in DISALLOWED_IP_RANGES:
                if ip in network:
                    if not allow_lan:
                        return False, f'Private/loopback IP blocked: {ip_str}'
        
        return True, ''
        
    except Exception as e:
        logger.exception(f'URL validation error: {e}')
        return False, f'Validation error: {sanitize_api_key(str(e))}'


# Default rate limits for AvistaZ-family trackers (configurable per indexer)
DEFAULT_CAPACITY = 5
DEFAULT_REFILL_SECONDS = 180.0
DEFAULT_MAX_PER_HOUR = 20

# Cache TTLs
QUERY_CACHE_TTL = 3600
INTERACTIVE_CACHE_TTL = 600

# Fan-out deadlines
INTERACTIVE_DEADLINE = 15.0  # seconds
BACKGROUND_DEADLINE = 60.0   # seconds

# Exponential backoff settings
INITIAL_BACKOFF = 60.0       # Start at 60s
MAX_BACKOFF = 3600.0         # Cap at 1 hour


class IndexerError(Exception):
    """Base exception for indexer errors."""
    pass


class IndexerRateLimitError(IndexerError):
    """Raised when an indexer is over budget or backed off."""
    pass


class IndexerBackoffError(IndexerError):
    """Raised when an indexer is temporarily disabled due to errors."""
    pass


class TokenBucket:
    """Token bucket rate limiter for background requests."""

    def __init__(self, capacity: int, refill_seconds: float):
        self.capacity = float(capacity)
        self.refill_seconds = refill_seconds
        self.tokens = float(capacity)
        self.last_refill = time.time()

    def take(self, now: float) -> bool:
        """Attempt to take one token. Returns True if successful."""
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed / self.refill_seconds)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for persistence."""
        return {
            'capacity': self.capacity,
            'refill_seconds': self.refill_seconds,
            'tokens': self.tokens,
            'last_refill': self.last_refill,
        }
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'TokenBucket':
        """Deserialize from dict."""
        bucket = TokenBucket(
            capacity=int(data.get('capacity', DEFAULT_CAPACITY)),
            refill_seconds=float(data.get('refill_seconds', DEFAULT_REFILL_SECONDS)),
        )
        bucket.tokens = float(data.get('tokens', bucket.capacity))
        bucket.last_refill = float(data.get('last_refill', time.time()))
        return bucket


class IndexerState:
    """Per-indexer state: rate limiting, backoff, and cache."""

    def __init__(self, indexer_id: str, config: Dict[str, Any]):
        self.indexer_id = indexer_id
        self.config = config
        self.lock = threading.Lock()
        self.last_request_time = 0.0

        # Rate limiting - default on for AvistaZ family
        is_avistaz = any(tracker in config.get('url', '').lower() 
                        for tracker in ['avistaz', 'cinemaz', 'privatehd', 'exoticaz', 'animez'])
        rate_limited_default = is_avistaz
        
        if config.get('rate_limited', rate_limited_default):
            capacity = config.get('rate_limit_capacity', DEFAULT_CAPACITY)
            refill = config.get('rate_limit_refill_seconds', DEFAULT_REFILL_SECONDS)
            self.bucket = TokenBucket(capacity, refill)
        else:
            self.bucket = None
            
        self.max_per_hour = config.get('rate_limit_max_per_hour', DEFAULT_MAX_PER_HOUR)
        self.request_log: List[float] = []

        # Exponential backoff
        self.disabled_until: Optional[float] = None
        self.backoff_seconds = 0
        self.backoff_level = 0  # Exponential backoff level (0, 1, 2, ...)

        # Cache: {query_key: (timestamp, results)}
        self.query_cache: Dict[Tuple, Tuple[float, List[Dict]]] = {}

    def requests_last_hour(self, now: float) -> int:
        """Count requests in the last hour."""
        cutoff = now - 3600
        self.request_log = [ts for ts in self.request_log if ts >= cutoff]
        return len(self.request_log)

    def is_backed_off(self, now: float) -> bool:
        """Check if indexer is currently in backoff."""
        if self.disabled_until and now < self.disabled_until:
            return True
        return False

    def set_backoff_exponential(self, now: float) -> None:
        """Set exponential backoff: 60s * 2^level, capped at 1 hour."""
        backoff_seconds = INITIAL_BACKOFF * (2 ** self.backoff_level)
        backoff_seconds = min(backoff_seconds, MAX_BACKOFF)
        self.disabled_until = now + backoff_seconds
        self.backoff_seconds = backoff_seconds
        self.backoff_level += 1
        logger.info(f'Indexer {self.indexer_id} exponential backoff level {self.backoff_level}: {backoff_seconds}s')

    def set_backoff(self, seconds: float, now: float) -> None:
        """Put indexer into backoff for the specified duration."""
        self.disabled_until = now + seconds
        self.backoff_seconds = seconds

    def clear_backoff(self) -> None:
        """Clear backoff state and reset exponential level on success."""
        self.disabled_until = None
        self.backoff_seconds = 0
        self.backoff_level = 0

    def get_cached(self, cache_key: Tuple, ttl: float, now: float) -> Optional[List[Dict]]:
        """Get cached results if fresh enough."""
        entry = self.query_cache.get(cache_key)
        if entry and (now - entry[0]) < ttl:
            # Deep copy to prevent mutation
            import copy
            return copy.deepcopy(entry[1])
        return None

    def set_cached(self, cache_key: Tuple, results: List[Dict], now: float) -> None:
        """Cache query results."""
        import copy
        # Deep copy results before caching
        self.query_cache[cache_key] = (now, copy.deepcopy(results))
        # Limit cache size
        if len(self.query_cache) > 500:
            oldest_keys = sorted(self.query_cache.keys(), key=lambda k: self.query_cache[k][0])[:100]
            for k in oldest_keys:
                self.query_cache.pop(k, None)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for persistence (excluding cache)."""
        return {
            'indexer_id': self.indexer_id,
            'last_request_time': self.last_request_time,
            'bucket': self.bucket.to_dict() if self.bucket else None,
            'request_log': self.request_log,
            'disabled_until': self.disabled_until,
            'backoff_seconds': self.backoff_seconds,
            'backoff_level': self.backoff_level,
        }
    
    @staticmethod
    def from_dict(data: Dict[str, Any], config: Dict[str, Any]) -> 'IndexerState':
        """Deserialize from dict."""
        state = IndexerState(data.get('indexer_id', ''), config)
        state.last_request_time = float(data.get('last_request_time', 0.0))
        
        bucket_data = data.get('bucket')
        if bucket_data:
            state.bucket = TokenBucket.from_dict(bucket_data)
        
        state.request_log = [float(t) for t in data.get('request_log', [])]
        state.disabled_until = data.get('disabled_until')
        state.backoff_seconds = float(data.get('backoff_seconds', 0))
        state.backoff_level = int(data.get('backoff_level', 0))
        return state


class IndexerManager:
    """Manages Torznab/Newznab indexers with rate limiting and caching."""

    def __init__(self, settings_manager):
        self.settings_manager = settings_manager
        self.states: Dict[str, IndexerState] = {}
        self.states_lock = threading.Lock()
        self.config_hash = None
        self._state_file = None
        self._load_state()

    def _get_state_file_path(self) -> Path:
        """Get path for state persistence."""
        if self._state_file:
            return self._state_file
        
        # Use data/ directory or fall back to /tmp
        data_dir = Path(os.environ.get('LUMA_DATA_DIR', './data'))
        data_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = data_dir / 'indexer_state.json'
        return self._state_file
    
    def _compute_config_hash(self) -> str:
        """Compute hash of current indexer config."""
        indexers = self._get_indexers()
        config_str = json.dumps(indexers, sort_keys=True)
        return hashlib.sha256(config_str.encode()).hexdigest()[:16]
    
    def _load_state(self) -> None:
        """Load persisted state from disk."""
        try:
            state_file = self._get_state_file_path()
            if not state_file.exists():
                return
            
            with open(state_file, 'r') as f:
                data = json.load(f)
            
            saved_hash = data.get('config_hash')
            states_data = data.get('states', {})
            
            current_hash = self._compute_config_hash()
            
            if saved_hash != current_hash:
                logger.info('Indexer config changed, discarding saved state')
                return
            
            # Restore states
            indexers = self._get_indexers()
            indexer_map = {ix['id']: ix for ix in indexers if 'id' in ix}
            
            for indexer_id, state_dict in states_data.items():
                config = indexer_map.get(indexer_id, {})
                if config:
                    self.states[indexer_id] = IndexerState.from_dict(state_dict, config)
            
            self.config_hash = current_hash
            logger.info(f'Loaded state for {len(self.states)} indexers')
            
        except Exception as e:
            logger.warning(f'Failed to load indexer state: {sanitize_api_key(str(e))}')
    
    def _save_state(self) -> None:
        """Persist state to disk."""
        try:
            state_file = self._get_state_file_path()
            states_data = {}
            
            with self.states_lock:
                for indexer_id, state in self.states.items():
                    states_data[indexer_id] = state.to_dict()
            
            data = {
                'config_hash': self.config_hash,
                'states': states_data,
            }
            
            # Atomic write
            temp_file = state_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(data, f)
            temp_file.replace(state_file)
            
        except Exception as e:
            logger.warning(f'Failed to save indexer state: {sanitize_api_key(str(e))}')

    def _get_indexers(self) -> List[Dict[str, Any]]:
        """Get indexer configurations from settings."""
        indexers = self.settings_manager.get('indexer.indexers') or []
        return [ix for ix in indexers if isinstance(ix, dict)]

    def _get_indexer_by_id(self, indexer_id: str) -> Optional[Dict[str, Any]]:
        """Get a single indexer config by ID."""
        indexers = self._get_indexers()
        for ix in indexers:
            if ix.get('id') == indexer_id:
                return ix
        return None

    def _rebuild_if_config_changed(self) -> None:
        """Rebuild state if config hash changed."""
        current_hash = self._compute_config_hash()
        if self.config_hash != current_hash:
            logger.info('Indexer config changed, rebuilding state')
            with self.states_lock:
                self.states.clear()
            self.config_hash = current_hash
            self._load_state()

    def _get_state(self, indexer_id: str) -> IndexerState:
        """Get or create state for an indexer."""
        self._rebuild_if_config_changed()
        
        with self.states_lock:
            if indexer_id not in self.states:
                config = self._get_indexer_by_id(indexer_id) or {}
                self.states[indexer_id] = IndexerState(indexer_id, config)
            return self.states[indexer_id]

    def list_indexers(self) -> List[Dict[str, Any]]:
        """List all configured indexers (for UI and consumer queries)."""
        indexers = self._get_indexers()
        result = []
        for ix in indexers:
            result.append({
                'id': ix.get('id'),
                'name': ix.get('name'),
                'type': ix.get('type'),
                'enabled': ix.get('enabled', True),
                'priority': ix.get('priority', 100),
                'categories': ix.get('categories', '2000'),
            })
        return sorted(result, key=lambda x: (x.get('priority', 100), x.get('name', '')))

    def get_enabled_indexers(self) -> List[Dict[str, Any]]:
        """Get enabled indexers sorted by priority."""
        indexers = self._get_indexers()
        enabled = [ix for ix in indexers if ix.get('enabled', True)]
        return sorted(enabled, key=lambda x: (x.get('priority', 100), x.get('name', '')))

    def test_indexer(self, indexer_id: str) -> Tuple[bool, str]:
        """Test connectivity to an indexer."""
        config = self._get_indexer_by_id(indexer_id)
        if not config:
            return False, 'Indexer not found'

        try:
            url = config.get('url', '').rstrip('/')
            api_key = config.get('api_key', '')
            indexer_type = config.get('type', 'torznab')

            if not url or not api_key:
                return False, 'Missing URL or API key'
            
            # SSRF protection
            allow_lan = self.settings_manager.get('indexer.allow_lan', False)
            valid, error = validate_indexer_url(url, allow_lan=allow_lan)
            if not valid:
                return False, f'URL validation failed: {error}'

            # Test with caps request - send both header and query param for compatibility
            # Many Torznab/Newznab servers only accept query param
            headers = {'X-Api-Key': api_key}
            resp = requests.get(
                f'{url}?t=caps&apikey={api_key}',
                headers=headers,
                timeout=10,
                stream=True,
            )
            
            # Cap response size
            content = resp.raw.read(MAX_RESPONSE_SIZE)
            if len(content) >= MAX_RESPONSE_SIZE:
                return False, 'Response too large (>5MB)'
            
            resp.raise_for_status()

            # Check for valid XML response
            root = ET.fromstring(content)
            if root.tag != 'caps':
                return False, 'Invalid response (missing <caps> element)'

            return True, 'Connection successful'

        except requests.exceptions.Timeout:
            return False, 'Connection timeout'
        except requests.exceptions.RequestException as e:
            error_msg = sanitize_api_key(str(e))
            return False, f'Request error: {error_msg[:100]}'
        except ET.ParseError:
            return False, 'Invalid XML response'
        except Exception as e:
            logger.exception(f'Indexer test failed: {sanitize_api_key(str(e))}')
            return False, f'Error: {sanitize_api_key(str(e))[:100]}'

    def _parse_torznab_xml(self, xml_text: str) -> List[Dict[str, Any]]:
        """Parse Torznab/Newznab XML response into normalized results. Tolerant of bad data."""
        results = []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f'XML parse error: {sanitize_api_key(str(e))}')
            return results

        ns = {
            'torznab': 'http://torznab.com/schemas/2015/feed',
            'newznab': 'http://www.newznab.com/DTD/2010/feeds/attributes/',
        }

        channel = root.find('.//channel')
        if channel is None:
            return results

        for item in channel.findall('item'):
            try:
                title = item.findtext('title', '') or ''
                link = item.findtext('link', '') or ''
                guid = item.findtext('guid', '') or ''
                pub_date = item.findtext('pubDate', '') or ''
                size_text = item.findtext('size', '0') or '0'

                # Extract torznab/newznab attributes
                attrs = {}
                for attr_el in item.findall('torznab:attr', ns) + item.findall('newznab:attr', ns):
                    name = attr_el.get('name', '')
                    value = attr_el.get('value', '')
                    if name:
                        attrs[name] = value

                # Check enclosure for magneturl and protocol
                enclosure = item.find('enclosure')
                enclosure_url = enclosure.get('url', '') if enclosure is not None else ''
                enclosure_type = enclosure.get('type', '') if enclosure is not None else ''
                
                # Get magneturl from attr or enclosure
                magneturl = attrs.get('magneturl', '')
                if not magneturl and enclosure_url and enclosure_url.startswith('magnet:'):
                    magneturl = enclosure_url
                
                # Derive protocol from enclosure type or magnet
                protocol = 'torrent'  # default
                if enclosure_type:
                    if 'application/x-nzb' in enclosure_type:
                        protocol = 'usenet'
                elif magneturl:
                    protocol = 'torrent'

                # Tolerant int parsing
                def safe_int(value, default=0):
                    if not value:
                        return default
                    try:
                        return int(value)
                    except (ValueError, TypeError):
                        return default

                seeders = safe_int(attrs.get('seeders'))
                peers = safe_int(attrs.get('peers'))
                leechers_attr = safe_int(attrs.get('leechers'))
                size_bytes = safe_int(attrs.get('size', size_text))
                
                # Calculate leechers: max(peers - seeders, leechers_attr, 0)
                if peers > 0:
                    leechers = max(peers - seeders, 0)
                else:
                    leechers = leechers_attr

                results.append({
                    'title': title,
                    'download_url': link,
                    'magneturl': magneturl,
                    'guid': guid,
                    'pub_date': pub_date,
                    'size': size_bytes,
                    'seeders': seeders,
                    'leechers': leechers,
                    'protocol': protocol,
                    'indexer': attrs.get('indexer', ''),
                    'info_hash': attrs.get('infohash', ''),
                    'imdb_id': attrs.get('imdb', ''),
                })
            except Exception as e:
                # Don't let one bad item kill the whole response
                logger.warning(f'Failed to parse item: {sanitize_api_key(str(e))}')
                continue

        return results

    def _search_one_indexer(
        self,
        indexer_id: str,
        query: str,
        categories: List[int],
        limit: int,
        interactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search a single indexer with rate limiting and caching. Lock NOT held during HTTP or sleeps."""
        config = self._get_indexer_by_id(indexer_id)
        if not config or not config.get('enabled', True):
            raise IndexerError(f'Indexer {indexer_id} not found or disabled')

        state = self._get_state(indexer_id)
        now = time.time()

        # Build cache key
        cache_key = (query, tuple(sorted(categories)), limit)

        # Phase 1: Check state and reserve request slot (acquire lock briefly)
        sleep_needed = 0.0
        with state.lock:
            # Check backoff
            if state.is_backed_off(now):
                until_str = time.strftime('%H:%M:%S', time.localtime(state.disabled_until))
                logger.info(f'Indexer {config.get("name")} backed off until {until_str}')
                raise IndexerBackoffError(f'Indexer backed off until {until_str}')

            # Check cache
            ttl = INTERACTIVE_CACHE_TTL if interactive else QUERY_CACHE_TTL
            cached = state.get_cached(cache_key, ttl, now)
            if cached is not None:
                logger.debug(f'Cache hit for {config.get("name")}: {query}')
                return cached

            # Rate limiting (skip for interactive queries, but enforce hourly cap)
            used = state.requests_last_hour(now)
            if used >= state.max_per_hour:
                logger.info(f'Indexer {config.get("name")} at hourly limit ({used} requests)')
                raise IndexerRateLimitError('Hourly rate limit exceeded')

            # Check bucket for rate-limited indexers
            if not interactive and state.bucket:
                if not state.bucket.take(now):
                    logger.debug(f'Indexer {config.get("name")} token bucket empty')
                    raise IndexerRateLimitError('Token bucket empty')

            # Reserve request slot
            state.request_log.append(time.time())
            
            # Calculate sleep needed for min interval
            min_interval = config.get('rate_limit_min_interval', 2.0) if state.bucket else 0
            if min_interval > 0:
                elapsed = now - state.last_request_time
                if elapsed < min_interval:
                    sleep_needed = min_interval - elapsed
        
        # Phase 2: Sleep OUTSIDE the lock if needed
        if sleep_needed > 0:
            time.sleep(sleep_needed)
        
        # Phase 3: Make HTTP request OUTSIDE the lock
        url = config.get('url', '').rstrip('/')
        api_key = config.get('api_key', '')
        cat_str = ','.join(str(c) for c in categories)
        
        # SSRF validation
        allow_lan = self.settings_manager.get('indexer.allow_lan', False)
        valid, error = validate_indexer_url(url, allow_lan=allow_lan)
        if not valid:
            raise IndexerError(f'URL validation failed: {error}')

        try:
            # Send both header and query param for maximum compatibility
            headers = {'X-Api-Key': api_key}
            params = {
                't': 'search',
                'q': query,
                'cat': cat_str,
                'limit': limit,
                'apikey': api_key,
            }
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=60,
                stream=True,
            )
            
            # Phase 4: Update last_request_time (acquire lock briefly)
            with state.lock:
                state.last_request_time = time.time()

            # Handle rate limit / errors
            if resp.status_code == 429:
                retry_after_header = resp.headers.get('Retry-After')
                retry_after_seconds = parse_retry_after(retry_after_header)
                if retry_after_seconds:
                    with state.lock:
                        state.set_backoff(retry_after_seconds, time.time())
                    logger.warning(f'Indexer {config.get("name")} 429 - Retry-After {retry_after_seconds}s')
                    raise IndexerBackoffError(f'Rate limited (429), retry after {retry_after_seconds}s')
                else:
                    with state.lock:
                        state.set_backoff_exponential(time.time())
                    logger.warning(f'Indexer {config.get("name")} 429 - exponential backoff')
                    raise IndexerBackoffError('Rate limited (429)')

            if resp.status_code >= 500:
                with state.lock:
                    state.set_backoff_exponential(time.time())
                logger.warning(f'Indexer {config.get("name")} {resp.status_code} - exponential backoff')
                raise IndexerError(f'Server error {resp.status_code}')

            resp.raise_for_status()

            # Cap response size at 5MB
            content = resp.raw.read(MAX_RESPONSE_SIZE)
            if len(content) >= MAX_RESPONSE_SIZE:
                logger.warning(f'Indexer {config.get("name")} response truncated at 5MB')

            # Parse results
            results = self._parse_torznab_xml(content.decode('utf-8', errors='replace'))
            
            # Phase 5: Cache and clear backoff (acquire lock briefly)
            with state.lock:
                state.set_cached(cache_key, results, time.time())
                state.clear_backoff()

            logger.info(f'Indexer {config.get("name")} search "{query}": {len(results)} results')
            return results

        except (IndexerError, IndexerBackoffError, IndexerRateLimitError):
            raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            error_msg = sanitize_api_key(str(e))
            logger.warning(f'Indexer {config.get("name")} connection error: {error_msg}')
            with state.lock:
                state.set_backoff_exponential(time.time())
            raise IndexerError(f'Connection error: {error_msg[:100]}')
        except requests.exceptions.RequestException as e:
            error_msg = sanitize_api_key(str(e))
            logger.error(f'Indexer {config.get("name")} request failed: {error_msg}')
            with state.lock:
                state.set_backoff_exponential(time.time())
            raise IndexerError(f'Request failed: {error_msg[:100]}')

    def search(
        self,
        query: str = '',
        categories: Optional[List[int]] = None,
        limit: int = 100,
        indexer_ids: Optional[List[str]] = None,
        interactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Search across indexers with parallel fan-out with deadline.

        Args:
            query: Search query string
            categories: List of Newznab category IDs (default: [2000])
            limit: Max results per indexer
            indexer_ids: Specific indexer IDs to search (None = all enabled)
            interactive: If True, bypass token bucket (but respect hourly cap)

        Returns:
            Merged results from all indexers (partial results if deadline exceeded)
        """
        if categories is None:
            categories = [2000]

        # Determine which indexers to search
        if indexer_ids:
            target_configs = [self._get_indexer_by_id(ix_id) for ix_id in indexer_ids]
            target_configs = [c for c in target_configs if c and c.get('enabled', True)]
        else:
            target_configs = self.get_enabled_indexers()

        if not target_configs:
            logger.warning('No enabled indexers available for search')
            return []

        # Set deadline
        deadline = INTERACTIVE_DEADLINE if interactive else BACKGROUND_DEADLINE
        config_deadline = self.settings_manager.get('indexer.search_deadline', None)
        if config_deadline is not None and isinstance(config_deadline, (int, float)):
            deadline = float(config_deadline)
        start_time = time.time()

        # Fan out to indexers in parallel
        all_results = []
        errors = {}

        def _search_one(config: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
            indexer_id = config['id']
            try:
                results = self._search_one_indexer(indexer_id, query, categories, limit, interactive)
                # Tag results with indexer info and create stable string IDs
                for r in results:
                    r['indexer_id'] = indexer_id
                    r['indexer_name'] = config.get('name', indexer_id)
                    r['tracker'] = r.get('indexer') or config.get('name', indexer_id)
                    r['priority'] = config.get('priority', 100)
                    
                    # Create stable string ID: hash of indexer_id + guid
                    guid = r.get('guid', '')
                    if guid:
                        id_input = f"{indexer_id}:{guid}"
                        r['id'] = hashlib.sha256(id_input.encode()).hexdigest()[:16]
                    else:
                        # Fallback: hash of indexer_id + title
                        id_input = f"{indexer_id}:{r.get('title', '')}"
                        r['id'] = hashlib.sha256(id_input.encode()).hexdigest()[:16]
                
                return indexer_id, results
            except (IndexerRateLimitError, IndexerBackoffError) as e:
                logger.debug(f'Indexer {config.get("name")} skipped: {e}')
                errors[indexer_id] = str(e)
                return indexer_id, []
            except Exception as e:
                logger.warning(f'Indexer {config.get("name")} failed: {e}')
                errors[indexer_id] = str(e)
                return indexer_id, []

        with ThreadPoolExecutor(max_workers=min(8, len(target_configs))) as executor:
            futures = {executor.submit(_search_one, cfg): cfg for cfg in target_configs}
            completed_count = 0
            
            for future in as_completed(futures, timeout=deadline):
                try:
                    indexer_id, results = future.result(timeout=0.1)
                    all_results.extend(results)
                    completed_count += 1
                except Exception as e:
                    logger.warning(f'Future failed: {sanitize_api_key(str(e))}')
                
                # Check if deadline exceeded
                if time.time() - start_time >= deadline:
                    logger.warning(f'Search deadline exceeded after {deadline}s, returning partial results')
                    break
            
            # Log partial results if we didn't wait for all
            if completed_count < len(target_configs):
                logger.info(f'Returning partial results from {completed_count}/{len(target_configs)} indexers')

        # Sort by priority (lower first), then by seeders
        all_results.sort(key=lambda x: (x.get('priority', 100), -x.get('seeders', 0)))

        logger.info(f'Search "{query}" returned {len(all_results)} total results')
        return all_results

    def import_from_prowlarr(self, prowlarr_url: str, prowlarr_api_key: str) -> Tuple[int, int, List[str]]:
        """
        Import indexer configurations from Prowlarr (read-only).
        
        Routing logic:
        - Torznab/Newznab implementation: search directly with own baseUrl + apiKey
        - Everything else (Cardigann, native C# like AvistaZ/PrivateHD): proxy via {prowlarr_url}/{id}/api
        
        Returns:
            (imported_count, proxied_count, error_messages)
        """
        imported = []
        proxied_indexers = []
        errors = []

        try:
            session = requests.Session()
            session.headers.update({
                'X-Api-Key': prowlarr_api_key,
                'Accept': 'application/json',
            })

            # Get indexers from Prowlarr API
            resp = session.get(f'{prowlarr_url.rstrip("/")}/api/v1/indexer', timeout=20)
            resp.raise_for_status()
            indexers = resp.json() or []

            for ix in indexers:
                indexer_id = str(ix.get('id', ''))
                name = ix.get('name', f'Indexer {indexer_id}')
                protocol = ix.get('protocol', 'torznab').lower()
                implementation = ix.get('implementation', '').lower()
                
                # Skip unsupported protocols
                if protocol not in ('torznab', 'newznab', 'usenet'):
                    continue

                # Detect if this is a direct Torznab/Newznab indexer
                # Implementation "torznab" or "newznab" means we can query it directly
                is_direct_torznab = implementation in ('torznab', 'newznab')
                
                # Extract Prowlarr fields
                fields = {f.get('name'): f.get('value') for f in ix.get('fields', []) if f.get('name')}
                
                # Detect rate-limited trackers (AvistaZ family)
                rate_limited_names = ('avistaz', 'privatehd', 'cinemaz', 'exoticaz', 'animez')
                is_avistaz_family = any(n in name.lower() for n in rate_limited_names)
                
                if is_direct_torznab:
                    # Direct Torznab/Newznab: use its own URL and key
                    base_url = fields.get('baseUrl', '')
                    api_key = fields.get('apiKey', '')
                    
                    if not base_url or not api_key:
                        errors.append(f'{name}: missing baseUrl or apiKey for direct Torznab')
                        continue
                    
                    # Build direct URL
                    if '/api' not in base_url:
                        url = f"{base_url.rstrip('/')}/api"
                    else:
                        url = base_url.rstrip('/')
                    
                    imported.append({
                        'id': f'direct-{indexer_id}',
                        'name': name,
                        'type': protocol,
                        'url': url,
                        'api_key': api_key,
                        'categories': '2000',
                        'priority': 100,
                        'enabled': bool(ix.get('enable', True)),
                        'prowlarr_proxy': False,
                        'rate_limited': is_avistaz_family,
                        'rate_limit_capacity': DEFAULT_CAPACITY if is_avistaz_family else 10,
                        'rate_limit_refill_seconds': DEFAULT_REFILL_SECONDS if is_avistaz_family else 60,
                        'rate_limit_max_per_hour': DEFAULT_MAX_PER_HOUR if is_avistaz_family else 200,
                        'rate_limit_min_interval': 2.0 if is_avistaz_family else 0,
                    })
                else:
                    # Everything else: Cardigann, native C# indexers (AvistaZ, PrivateHD, etc.)
                    # Route through Prowlarr proxy: {prowlarr_url}/{id}/api
                    proxy_url = f"{prowlarr_url.rstrip('/')}/{indexer_id}/api"
                    
                    proxied_indexers.append(name)
                    
                    imported.append({
                        'id': f'prowlarr-{indexer_id}',
                        'name': f'{name} (via Prowlarr)',
                        'type': protocol,
                        'url': proxy_url,
                        'api_key': prowlarr_api_key,  # Use Prowlarr's key for proxy
                        'categories': '2000',
                        'priority': 100,
                        'enabled': bool(ix.get('enable', True)),
                        'prowlarr_proxy': True,
                        'prowlarr_url': prowlarr_url,
                        'implementation': implementation,
                        'rate_limited': is_avistaz_family,
                        'rate_limit_capacity': DEFAULT_CAPACITY if is_avistaz_family else 10,
                        'rate_limit_refill_seconds': DEFAULT_REFILL_SECONDS if is_avistaz_family else 60,
                        'rate_limit_max_per_hour': DEFAULT_MAX_PER_HOUR if is_avistaz_family else 200,
                        'rate_limit_min_interval': 2.0 if is_avistaz_family else 0,
                    })

        except requests.exceptions.RequestException as e:
            errors.append(f'Prowlarr API error: {sanitize_api_key(str(e))}')
            return 0, 0, errors
        except Exception as e:
            errors.append(f'Import error: {sanitize_api_key(str(e))}')
            return 0, 0, errors

        # Merge with existing indexers (don't overwrite user edits)
        existing = {ix.get('id'): ix for ix in self._get_indexers()}
        for new_ix in imported:
            if new_ix['id'] not in existing:
                existing[new_ix['id']] = new_ix

        # Save to settings
        self.settings_manager.update({'indexer': {'indexers': list(existing.values())}})

        logger.info(
            f'Imported {len(imported)} indexers from Prowlarr: '
            f'{len(proxied_indexers)} proxied (Cardigann/native), '
            f'{len(imported) - len(proxied_indexers)} direct (Torznab/Newznab)'
        )
        return len(imported), len(proxied_indexers), errors


# Global singleton instance
_indexer_manager_instance: Optional[IndexerManager] = None
_indexer_manager_lock = threading.Lock()


def get_indexer_manager(settings_manager=None) -> IndexerManager:
    """
    Get or create the global IndexerManager singleton.
    
    Args:
        settings_manager: SettingsManager instance (required for first call)
    
    Returns:
        The global IndexerManager instance
    """
    global _indexer_manager_instance
    
    with _indexer_manager_lock:
        if _indexer_manager_instance is None:
            if settings_manager is None:
                raise ValueError('settings_manager required for first call to get_indexer_manager()')
            _indexer_manager_instance = IndexerManager(settings_manager)
        
        return _indexer_manager_instance
