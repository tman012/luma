#!/usr/bin/env python3
"""
Luma Indexer Manager
Clean-room Torznab/Newznab indexer manager to replace Prowlarr.

Licensing: MIT/BSD compatible libraries only (stdlib, requests, xml.etree).
No GPL code from Prowlarr, Jackett, Radarr, or Sonarr.
"""

import logging
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Default rate limits for AvistaZ-family trackers (configurable per indexer)
DEFAULT_CAPACITY = 5
DEFAULT_REFILL_SECONDS = 180.0
DEFAULT_MAX_PER_HOUR = 20

# Cache TTLs
QUERY_CACHE_TTL = 3600
INTERACTIVE_CACHE_TTL = 600


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


class IndexerState:
    """Per-indexer state: rate limiting, backoff, and cache."""

    def __init__(self, indexer_id: str, config: Dict[str, Any]):
        self.indexer_id = indexer_id
        self.config = config
        self.lock = threading.Lock()
        self.last_request_time = 0.0

        # Rate limiting
        capacity = config.get('rate_limit_capacity', DEFAULT_CAPACITY)
        refill = config.get('rate_limit_refill_seconds', DEFAULT_REFILL_SECONDS)
        self.bucket = TokenBucket(capacity, refill)
        self.max_per_hour = config.get('rate_limit_max_per_hour', DEFAULT_MAX_PER_HOUR)
        self.request_log: List[float] = []

        # Backoff
        self.disabled_until: Optional[float] = None
        self.backoff_seconds = 0

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

    def set_backoff(self, seconds: float, now: float) -> None:
        """Put indexer into backoff for the specified duration."""
        self.disabled_until = now + seconds
        self.backoff_seconds = seconds

    def clear_backoff(self) -> None:
        """Clear backoff state."""
        self.disabled_until = None
        self.backoff_seconds = 0

    def get_cached(self, cache_key: Tuple, ttl: float, now: float) -> Optional[List[Dict]]:
        """Get cached results if fresh enough."""
        entry = self.query_cache.get(cache_key)
        if entry and (now - entry[0]) < ttl:
            return entry[1]
        return None

    def set_cached(self, cache_key: Tuple, results: List[Dict], now: float) -> None:
        """Cache query results."""
        self.query_cache[cache_key] = (now, results)
        # Limit cache size
        if len(self.query_cache) > 500:
            oldest_keys = sorted(self.query_cache.keys(), key=lambda k: self.query_cache[k][0])[:100]
            for k in oldest_keys:
                self.query_cache.pop(k, None)


class IndexerManager:
    """Manages Torznab/Newznab indexers with rate limiting and caching."""

    def __init__(self, settings_manager):
        self.settings_manager = settings_manager
        self.states: Dict[str, IndexerState] = {}
        self.states_lock = threading.Lock()

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

    def _get_state(self, indexer_id: str) -> IndexerState:
        """Get or create state for an indexer."""
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

            # Test with caps request
            resp = requests.get(
                url,
                params={'t': 'caps', 'apikey': api_key},
                timeout=10,
            )
            resp.raise_for_status()

            # Check for valid XML response
            root = ET.fromstring(resp.text)
            if root.tag != 'caps':
                return False, 'Invalid response (missing <caps> element)'

            return True, 'Connection successful'

        except requests.exceptions.Timeout:
            return False, 'Connection timeout'
        except requests.exceptions.RequestException as e:
            return False, f'Request error: {str(e)[:100]}'
        except ET.ParseError:
            return False, 'Invalid XML response'
        except Exception as e:
            logger.exception(f'Indexer test failed: {e}')
            return False, f'Error: {str(e)[:100]}'

    def _parse_torznab_xml(self, xml_text: str) -> List[Dict[str, Any]]:
        """Parse Torznab/Newznab XML response into normalized results."""
        results = []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f'XML parse error: {e}')
            return results

        ns = {
            'torznab': 'http://torznab.com/schemas/2015/feed',
            'newznab': 'http://www.newznab.com/DTD/2010/feeds/attributes/',
        }

        channel = root.find('.//channel')
        if channel is None:
            return results

        for item in channel.findall('item'):
            title = item.findtext('title', '') or ''
            link = item.findtext('link', '') or ''
            guid = item.findtext('guid', '') or ''
            pub_date = item.findtext('pubDate', '') or ''
            size = item.findtext('size', '0') or '0'

            # Extract torznab/newznab attributes
            attrs = {}
            for attr_el in item.findall('torznab:attr', ns) + item.findall('newznab:attr', ns):
                name = attr_el.get('name', '')
                value = attr_el.get('value', '')
                if name:
                    attrs[name] = value

            seeders = int(attrs.get('seeders', 0) or 0)
            leechers = int(attrs.get('leechers', 0) or attrs.get('peers', 0) or 0)
            size_bytes = int(attrs.get('size', size) or 0)

            results.append({
                'title': title,
                'download_url': link,
                'guid': guid,
                'pub_date': pub_date,
                'size': size_bytes,
                'seeders': seeders,
                'leechers': leechers,
                'indexer': attrs.get('indexer', ''),
                'info_hash': attrs.get('infohash', ''),
                'imdb_id': attrs.get('imdb', ''),
            })

        return results

    def _search_one_indexer(
        self,
        indexer_id: str,
        query: str,
        categories: List[int],
        limit: int,
        interactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search a single indexer with rate limiting and caching."""
        config = self._get_indexer_by_id(indexer_id)
        if not config or not config.get('enabled', True):
            raise IndexerError(f'Indexer {indexer_id} not found or disabled')

        state = self._get_state(indexer_id)
        now = time.time()

        # Build cache key
        cache_key = (query, tuple(sorted(categories)), limit)

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
                cached_stale = state.query_cache.get(cache_key)
                if cached_stale:
                    return cached_stale[1]
                raise IndexerRateLimitError('Hourly rate limit exceeded')

            if not interactive and config.get('rate_limited', False):
                if not state.bucket.take(now):
                    logger.debug(f'Indexer {config.get("name")} token bucket empty')
                    cached_stale = state.query_cache.get(cache_key)
                    if cached_stale:
                        return cached_stale[1]
                    raise IndexerRateLimitError('Token bucket empty')

            # Enforce minimum interval for rate-limited indexers
            min_interval = config.get('rate_limit_min_interval', 2.0) if config.get('rate_limited') else 0
            if min_interval > 0:
                elapsed = now - state.last_request_time
                if elapsed < min_interval:
                    time.sleep(min_interval - elapsed)

            # Make request
            url = config.get('url', '').rstrip('/')
            api_key = config.get('api_key', '')
            cat_str = ','.join(str(c) for c in categories)

            try:
                state.request_log.append(time.time())
                resp = requests.get(
                    url,
                    params={
                        't': 'search',
                        'q': query,
                        'cat': cat_str,
                        'apikey': api_key,
                        'limit': limit,
                    },
                    timeout=60,
                )
                state.last_request_time = time.time()

                # Handle rate limit / errors
                if resp.status_code == 429:
                    retry_after = resp.headers.get('Retry-After')
                    backoff_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 60
                    state.set_backoff(backoff_seconds, now)
                    logger.warning(f'Indexer {config.get("name")} 429 - backing off {backoff_seconds}s')
                    raise IndexerBackoffError(f'Rate limited (429), backoff {backoff_seconds}s')

                if resp.status_code >= 500:
                    state.set_backoff(300, now)
                    logger.warning(f'Indexer {config.get("name")} {resp.status_code} - backing off 5min')
                    raise IndexerError(f'Server error {resp.status_code}')

                resp.raise_for_status()

                # Parse results
                results = self._parse_torznab_xml(resp.text)
                state.set_cached(cache_key, results, now)
                state.clear_backoff()

                logger.info(f'Indexer {config.get("name")} search "{query}": {len(results)} results')
                return results

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                logger.warning(f'Indexer {config.get("name")} connection error: {e}')
                state.set_backoff(60, now)
                raise IndexerError(f'Connection error: {e}')
            except requests.exceptions.RequestException as e:
                logger.error(f'Indexer {config.get("name")} request failed: {e}')
                state.set_backoff(60, now)
                raise IndexerError(f'Request failed: {e}')

    def search(
        self,
        query: str = '',
        categories: Optional[List[int]] = None,
        limit: int = 100,
        indexer_ids: Optional[List[str]] = None,
        interactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Search across indexers with parallel fan-out.

        Args:
            query: Search query string
            categories: List of Newznab category IDs (default: [2000])
            limit: Max results per indexer
            indexer_ids: Specific indexer IDs to search (None = all enabled)
            interactive: If True, bypass token bucket (but respect hourly cap)

        Returns:
            Merged results from all indexers
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

        # Fan out to indexers in parallel
        all_results = []
        errors = {}

        def _search_one(config: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
            indexer_id = config['id']
            try:
                results = self._search_one_indexer(indexer_id, query, categories, limit, interactive)
                # Tag results with indexer info
                for r in results:
                    r['indexer_id'] = indexer_id
                    r['indexer_name'] = config.get('name', indexer_id)
                    r['tracker'] = r.get('indexer') or config.get('name', indexer_id)
                    r['priority'] = config.get('priority', 100)
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
            for future in as_completed(futures):
                indexer_id, results = future.result()
                all_results.extend(results)

        # Sort by priority (lower first), then by seeders
        all_results.sort(key=lambda x: (x.get('priority', 100), -x.get('seeders', 0)))

        logger.info(f'Search "{query}" returned {len(all_results)} total results from {len(target_configs)} indexers')
        return all_results

    def import_from_prowlarr(self, prowlarr_url: str, prowlarr_api_key: str) -> Tuple[int, int, List[str]]:
        """
        Import indexer configurations from Prowlarr (read-only).

        Returns:
            (imported_count, cardigann_count, error_messages)
        """
        imported = []
        cardigann_indexers = []
        errors = []

        try:
            session = requests.Session()
            session.headers.update({
                'X-Api-Key': prowlarr_api_key,
                'Accept': 'application/json',
            })

            # Get indexers
            resp = session.get(f'{prowlarr_url.rstrip("/")}/api/v1/indexer', timeout=20)
            resp.raise_for_status()
            indexers = resp.json() or []

            for ix in indexers:
                indexer_id = str(ix.get('id', ''))
                name = ix.get('name', f'Indexer {indexer_id}')
                protocol = ix.get('protocol', 'torznab').lower()

                # Skip if not torznab/newznab
                if protocol not in ('torznab', 'newznab'):
                    continue

                # Detect Cardigann (custom scrapers, not native Torznab)
                implementation = ix.get('implementation', '').lower()
                is_cardigann = implementation == 'cardigann' or ix.get('configContract') == 'CardigannSettings'

                if is_cardigann:
                    cardigann_indexers.append(name)
                    continue

                # Extract config
                fields = {f.get('name'): f.get('value') for f in ix.get('fields', []) if f.get('name')}
                base_url = fields.get('baseUrl', '')
                api_key = fields.get('apiKey', '')

                if not base_url:
                    errors.append(f'{name}: no baseUrl')
                    continue

                # Build Torznab URL
                if '/api' not in base_url:
                    url = f"{base_url.rstrip('/')}/api"
                else:
                    url = base_url.rstrip('/')

                # Detect rate-limited trackers (AvistaZ family)
                rate_limited_names = ('avistaz', 'privatehd', 'cinemaz', 'exoticaz')
                rate_limited = any(n in name.lower() for n in rate_limited_names)

                imported.append({
                    'id': f'prowlarr-{indexer_id}',
                    'name': name,
                    'type': protocol,
                    'url': url,
                    'api_key': api_key,
                    'categories': '2000',
                    'priority': 100,
                    'enabled': bool(ix.get('enable', True)),
                    'rate_limited': rate_limited,
                    'rate_limit_capacity': DEFAULT_CAPACITY if rate_limited else 10,
                    'rate_limit_refill_seconds': DEFAULT_REFILL_SECONDS if rate_limited else 60,
                    'rate_limit_max_per_hour': DEFAULT_MAX_PER_HOUR if rate_limited else 200,
                    'rate_limit_min_interval': 2.0 if rate_limited else 0,
                })

        except requests.exceptions.RequestException as e:
            errors.append(f'Prowlarr API error: {e}')
            return 0, 0, errors
        except Exception as e:
            errors.append(f'Import error: {e}')
            return 0, 0, errors

        # Merge with existing indexers (don't overwrite user edits)
        existing = {ix.get('id'): ix for ix in self._get_indexers()}
        for new_ix in imported:
            if new_ix['id'] not in existing:
                existing[new_ix['id']] = new_ix

        # Save to settings
        self.settings_manager.update({'indexer': {'indexers': list(existing.values())}})

        logger.info(
            f'Imported {len(imported)} indexers from Prowlarr, '
            f'flagged {len(cardigann_indexers)} Cardigann-only'
        )
        return len(imported), len(cardigann_indexers), errors
