#!/usr/bin/env python3
"""
Indexer Backend Wrapper
Maintains compatibility with existing indexer_api interface while supporting both
Prowlarr and Luma backends.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def get_indexer_backend(settings):
    """
    Return the appropriate indexer backend based on settings.
    
    Returns either a Prowlarr/TorznabClient (legacy) or the new Luma IndexerManager,
    wrapped in a compatibility adapter.
    """
    backend = settings.get('indexer.backend', 'prowlarr')
    
    if backend == 'luma':
        from app.indexer_manager import get_indexer_client
        from app.settings_manager import SettingsManager
        
        # Get or create settings manager instance
        sm = SettingsManager() if not hasattr(settings, 'get_all') else settings
        return get_indexer_client(sm)
    else:
        # Legacy Prowlarr/Torznab backend
        from app import indexer_api
        return indexer_api.get_indexer_client(settings)


class LumaBackendAdapter:
    """
    Adapter that makes the new IndexerManager look like the old ProwlarrClient
    interface, ensuring all existing consumers continue to work unchanged.
    """
    
    def __init__(self, manager):
        self.manager = manager
    
    def test_connection(self) -> bool:
        """Test connection to at least one enabled indexer."""
        indexers = self.manager.get_enabled_indexers()
        if not indexers:
            logger.warning('No enabled indexers configured')
            return False
        
        # Test first enabled indexer
        success, msg = self.manager.test_indexer(indexers[0]['id'])
        if success:
            logger.info(f'Indexer connection test passed: {msg}')
        else:
            logger.error(f'Indexer connection test failed: {msg}')
        return success
    
    def test_indexer(self, indexer_id: str) -> Tuple[bool, str]:
        """Test a specific indexer by ID."""
        return self.manager.test_indexer(indexer_id)
    
    def import_from_prowlarr(self, prowlarr_url: str, prowlarr_api_key: str) -> Tuple[int, int, List[str]]:
        """Import indexers from Prowlarr."""
        return self.manager.import_from_prowlarr(prowlarr_url, prowlarr_api_key)
    
    def get_download_token(self, token: str) -> Optional[Tuple[str, str, bool]]:
        """Resolve an opaque download token to (url without apikey, indexer_id, needs_api_key)."""
        return self.manager.resolve_download_token(token)
    
    def get_indexers(self) -> List[Dict]:
        """List indexers in Prowlarr-compatible format."""
        indexers = self.manager.list_indexers()
        result = []
        for ix in indexers:
            result.append({
                'id': ix['id'],  # Keep as string for consistency
                'name': ix['name'],
                'enabled': ix['enabled'],
            })
        return result
    
    def search_movies(self, categories: str = '2000', limit: int = 300) -> List[Dict]:
        """Browse for recent movie torrents (Prowlarr-compatible API)."""
        try:
            cats = self._parse_categories(categories)
            results = self.manager.search(query='', categories=cats, limit=limit)
            return self._normalize_results(results)
        except Exception as e:
            logger.error(f'search_movies failed: {e}')
            return []
    
    def sweep_movies(self, categories: str = '2000',
                     queries: Optional[List[str]] = None,
                     max_workers: int = 3) -> List[Dict]:
        """Run broad keyword sweeps (Prowlarr-compatible API)."""
        from app.indexer_api import SWEEP_QUERIES
        
        if queries is None:
            queries = list(SWEEP_QUERIES)
        
        cats = self._parse_categories(categories)
        seen_guids: set = set()
        all_results: List[Dict] = []
        
        for query in queries:
            try:
                results = self.manager.search(query=query, categories=cats, limit=500)
                for r in results:
                    guid = r.get('guid', '')
                    if guid and guid not in seen_guids:
                        seen_guids.add(guid)
                        all_results.append(r)
            except Exception as e:
                logger.warning(f'sweep query {query!r} failed: {e}')
        
        normalized = self._normalize_results(all_results)
        logger.info(f'sweep_movies: {len(normalized)} unique results from {len(queries)} queries')
        self.manager.persist_download_tokens()
        return normalized
    
    def search_all(self, query: str, media_type: str,
                   year: Optional[int] = None) -> List[Dict]:
        """
        Raw broad-category search (Prowlarr-compatible API).
        Used by Sources panel, Roulette, etc.
        """
        from app.indexer_api import MOVIE_CATEGORIES_EXPANDED, TV_SPORTS_CATEGORIES, _build_search_query
        from email.utils import parsedate_to_datetime
        from datetime import timezone
        
        q = _build_search_query(query, year)
        categories = list(MOVIE_CATEGORIES_EXPANDED) + list(TV_SPORTS_CATEGORIES)
        
        try:
            results = self.manager.search(query=q, categories=categories, limit=500, interactive=True)
            
            # Convert to Prowlarr-compatible format and sign download URLs
            out = []
            for r in results:
                from app.indexer_api import _parse_title
                parsed = _parse_title(r.get('title', ''))
                
                # Convert RFC-822 pubDate to ISO 8601
                pub_date_rfc = r.get('pub_date', '')
                pub_date_iso = ''
                if pub_date_rfc:
                    try:
                        dt = parsedate_to_datetime(pub_date_rfc)
                        # Ensure UTC timezone
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        pub_date_iso = dt.isoformat()
                    except Exception:
                        pub_date_iso = pub_date_rfc  # Fallback to original
                
                # Sign download URLs to avoid exposing API keys
                download_url = r.get('download_url', '')
                indexer_id = r.get('indexer_id', '')
                if download_url and indexer_id:
                    download_url = self._sign_download_url(download_url, indexer_id)
                
                out.append({
                    'id': r.get('id', ''),  # Stable string ID
                    'title': r.get('title', ''),
                    'clean_title': parsed['clean_title'],
                    'year': parsed['year'] or '',
                    'resolution': parsed['resolution'] or '',
                    'source': parsed['source'] or '',
                    'seeders': r.get('seeders', 0),
                    'leechers': r.get('leechers', 0),
                    'size': r.get('size', 0),
                    'indexer': r.get('indexer_name', ''),
                    'tracker': r.get('tracker', ''),
                    'indexer_id': r.get('indexer_id', ''),  # String ID
                    'pub_date': pub_date_iso,
                    'publishDate': pub_date_iso,  # Sonarr/Radarr compatibility
                    'download_url': download_url,  # Signed URL
                    'magneturl': r.get('magneturl', ''),
                    'info_hash': r.get('info_hash', ''),
                    'guid': r.get('guid', ''),
                    'protocol': r.get('protocol', 'torrent'),
                    'imdb_id': r.get('imdb_id', ''),
                })
            
            self.manager.persist_download_tokens()
            logger.info(f'search_all q={q!r}: {len(out)} results')
            return out
        
        except Exception as e:
            logger.error(f'search_all failed: {e}')
            return []
    
    def _parse_categories(self, categories: str) -> List[int]:
        """Parse comma-separated category string into list of ints."""
        from app.indexer_api import _expand_movie_categories
        return _expand_movie_categories(categories)
    
    def _normalize_results(self, raw_results: List[Dict]) -> List[Dict]:
        """
        Normalize IndexerManager results to match ProwlarrClient format.
        This ensures existing consumers get the exact same data shape.
        
        IMPORTANT: Never expose raw download_url with API keys.
        Issue signed /api/indexer/download/<token> links instead.
        """
        from app.indexer_api import _parse_title
        from email.utils import parsedate_to_datetime
        from datetime import timezone
        
        normalized = []
        for r in raw_results:
            parsed = _parse_title(r.get('title', ''))
            
            # Convert RFC-822 pubDate to ISO 8601
            pub_date_rfc = r.get('pub_date', '')
            pub_date_iso = ''
            if pub_date_rfc:
                try:
                    dt = parsedate_to_datetime(pub_date_rfc)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    pub_date_iso = dt.isoformat()
                except Exception:
                    pub_date_iso = pub_date_rfc
            
            # Sign download URLs to avoid exposing API keys
            download_url = r.get('download_url', '')
            indexer_id = r.get('indexer_id', '')
            if download_url and indexer_id:
                download_url = self._sign_download_url(download_url, indexer_id)
            
            normalized.append({
                'id': r.get('id', ''),  # Stable string ID
                'title': parsed['clean_title'],
                'year': parsed['year'] or '',
                'seeders': r.get('seeders', 0),
                'size_gb': round(r.get('size', 0) / (1024 ** 3), 2) if r.get('size') else 0,
                'resolution': parsed['resolution'] or '',
                'source': parsed['source'] or '',
                'tracker': r.get('tracker', ''),
                'pub_date': pub_date_iso,
                'publishDate': pub_date_iso,
                'download_url': download_url,  # Signed URL
                'magneturl': r.get('magneturl', ''),
                'info_hash': r.get('info_hash', ''),
                'imdb_id': r.get('imdb_id', ''),
                'torrent_name': r.get('title', ''),
                'guid': r.get('guid', ''),
                'indexer_id': r.get('indexer_id', ''),  # String ID
                'protocol': r.get('protocol', 'torrent'),
            })
        
        self.manager.persist_download_tokens()
        return normalized
    
    def _sign_download_url(self, download_url: str, indexer_id: str) -> str:
        """Replace a raw download URL with an opaque, server-side-mapped token link."""
        token = self.manager.issue_download_token(download_url, indexer_id, persist=False)
        return f'/api/indexer/download/{token}'
