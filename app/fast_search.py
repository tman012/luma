#!/usr/bin/env python3
"""
Fast In-Memory Media Search Index
Preloads all Plex library metadata (movies + TV shows) on startup for millisecond search.
Auto-refreshes every 15 minutes in background.
Supports typo-tolerant fuzzy search with normalization and similarity matching.
"""

import logging
import re
import threading
import time
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Any, Set, Tuple

import requests

logger = logging.getLogger(__name__)

# Plex section type → (media_type, plex_type). plex_type: 1 = movies, 2 = shows
_SECTION_TYPES = {'movie': ('movie', 1), 'show': ('show', 2)}

REFRESH_INTERVAL = 900  # 15 minutes

# Leading articles to strip for normalization (case-insensitive)
_LEADING_ARTICLES = {'the', 'a', 'an', 'el', 'la', 'le', 'les', 'un', 'une', 'das', 'der', 'die'}


# ------------------------------------------------------------------
# Text normalization and fuzzy matching utilities
# ------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """
    Normalize text for fuzzy matching:
    - Lowercase
    - Remove accents/diacritics
    - Replace '&' with 'and'
    - Remove leading articles (the, a, an, etc.)
    - Remove all punctuation and extra whitespace
    """
    if not text:
        return ''
    
    # Lowercase
    text = text.lower()
    
    # Remove accents/diacritics
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    
    # Replace '&' with 'and'
    text = text.replace('&', 'and')
    
    # Remove punctuation (apostrophes and asterisks removed without leaving spaces)
    text = text.replace("'", '')
    text = text.replace('*', '')
    text = text.replace('-', ' ')  # Hyphens become spaces for "Spider-Man" -> "spider man"
    text = re.sub(r'[^\w\s]', '', text)  # Remove other punctuation without replacing
    
    # Normalize whitespace
    text = ' '.join(text.split())
    
    # Remove leading articles
    original_text = text
    words = text.split()
    if words and words[0] in _LEADING_ARTICLES:
        words = words[1:]
    
    result = ' '.join(words) if words else original_text
    return result


def extract_trigrams(text: str) -> Set[str]:
    """Extract character trigrams from text for similarity matching."""
    text = f"  {text} "  # Pad for edge trigrams
    return {text[i:i+3] for i in range(len(text) - 2)}


def trigram_similarity(text1: str, text2: str) -> float:
    """Calculate Sørensen-Dice coefficient for trigram similarity (0.0 to 1.0)."""
    if not text1 or not text2:
        return 0.0
    
    trigrams1 = extract_trigrams(text1)
    trigrams2 = extract_trigrams(text2)
    
    if not trigrams1 or not trigrams2:
        return 0.0
    
    intersection = len(trigrams1 & trigrams2)
    return 2.0 * intersection / (len(trigrams1) + len(trigrams2))


def token_similarity(query_tokens: List[str], title_tokens: List[str]) -> float:
    """
    Calculate token-level similarity.
    Returns 1.0 for exact match, high score for all tokens present, lower for partial.
    """
    if not query_tokens or not title_tokens:
        return 0.0
    
    # Check if all query tokens are present in title tokens
    title_set = set(title_tokens)
    matched = sum(1 for qt in query_tokens if qt in title_set)
    
    if matched == len(query_tokens):
        # All query tokens matched - bonus if title has same length
        if len(query_tokens) == len(title_tokens):
            return 1.0
        return 0.9
    
    # Partial token match
    return 0.6 * (matched / len(query_tokens))


def fuzzy_match_score(query: str, title: str, query_norm: str, title_norm: str) -> Tuple[float, str]:
    """
    Calculate fuzzy match score for a query against a title.
    Returns (score, match_type) where:
    - score: 0.0 to 1.0, higher is better
    - match_type: 'exact', 'prefix', 'fuzzy', 'none'
    """
    # Exact match (original case-insensitive)
    if query.lower() == title.lower():
        return (1.0, 'exact')
    
    # Prefix match
    if title.lower().startswith(query.lower()):
        return (0.95, 'prefix')
    
    # Check normalized exact match
    if query_norm == title_norm:
        return (0.9, 'exact')
    
    # Check normalized prefix
    if title_norm.startswith(query_norm):
        return (0.85, 'prefix')
    
    # Token-based matching
    query_tokens = query_norm.split()
    title_tokens = title_norm.split()
    token_score = token_similarity(query_tokens, title_tokens)
    
    if token_score >= 0.9:
        return (0.8, 'fuzzy')
    elif token_score >= 0.6:
        return (0.6 + token_score * 0.1, 'fuzzy')
    
    # Trigram similarity for typos
    trigram_score = trigram_similarity(query_norm, title_norm)
    
    # Sequence matcher for edit distance
    seq_score = SequenceMatcher(None, query_norm, title_norm).ratio()
    
    # Use the best score
    best_score = max(trigram_score, seq_score, token_score)
    
    if best_score >= 0.6:
        return (best_score * 0.8, 'fuzzy')
    
    return (best_score, 'none')


def calculate_popularity_score(item: Dict[str, Any]) -> float:
    """
    Calculate popularity score for ranking.
    Uses IMDb/TMDb ratings, audience ratings, and viewCount.
    Priority: rating > audienceRating > viewCount.
    """
    import math
    
    # Use IMDb/TMDb rating if available (0-10 scale)
    rating = item.get('rating', 0.0) or 0.0
    if rating > 0:
        # Normalize to 0-1 range and boost high ratings
        return (rating / 10.0) ** 1.5 * 10
    
    # Fall back to audience rating
    audience_rating = item.get('audienceRating', 0.0) or 0.0
    if audience_rating > 0:
        return (audience_rating / 10.0) ** 1.5 * 8
    
    # Fall back to viewCount (logarithmic scale)
    view_count = item.get('viewCount', 0)
    if view_count > 0:
        return math.log10(view_count + 1)
    
    return 0.0


class FastSearchIndex:
    """In-memory media search index backed by Plex library data (movies + TV shows)."""

    def __init__(self):
        self._index: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._last_refresh: float = 0.0
        self._refreshing = False
        self._initialized = False
        self._sections: List[int] = []
        self._title_dictionary: Set[str] = set()  # Normalized titles for spelling correction
        self._tmdb_titles_cached: bool = False
        self._tmdb_cache_time: float = 0.0

    # ------------------------------------------------------------------
    # Config (pulled lazily from settings each refresh so token changes work)
    # ------------------------------------------------------------------

    def _wanted_section_ids(self) -> List[int]:
        """``plex.search_section_ids`` from settings; empty = every movie/show section."""
        try:
            from app.settings_manager import SettingsManager
            ids = SettingsManager().get('plex.search_section_ids', []) or []
            return [int(x) for x in ids]
        except Exception:
            return []

    def _discover_sections(self, plex_url: str, plex_token: str):
        """Return [(section_id, title, media_type, plex_type)] for the sections to index."""
        try:
            resp = requests.get(f"{plex_url}/library/sections",
                                params={'X-Plex-Token': plex_token},
                                headers={'Accept': 'application/json'}, timeout=15)
            resp.raise_for_status()
            dirs = resp.json().get('MediaContainer', {}).get('Directory', []) or []
        except Exception as e:
            logger.error(f"fast_search: could not list Plex sections: {e}")
            return []
        found = {}
        for d in dirs:
            kind = _SECTION_TYPES.get(d.get('type'))
            try:
                sid = int(d.get('key'))
            except (TypeError, ValueError):
                continue
            if kind:
                found[sid] = (sid, d.get('title') or f'Section {sid}', kind[0], kind[1])
        wanted = self._wanted_section_ids()
        if wanted:
            return [found[s] for s in wanted if s in found]
        return list(found.values())

    def _get_plex_config(self):
        """Return (plex_url, plex_token) from settings manager."""
        try:
            from app.settings_manager import SettingsManager
            _s = SettingsManager()
            url = _s.get('plex.url', '') or ''
            token = _s.get('plex.token', '') or ''
            return url.rstrip('/'), token
        except Exception as e:
            logger.warning(f"fast_search: could not load settings ({e}), using defaults")
            return '', ''

    # ------------------------------------------------------------------
    # Index loading
    # ------------------------------------------------------------------

    def _fetch_section(self, plex_url: str, plex_token: str,
                       section_id: int, section_name: str,
                       media_type: str = 'movie',
                       plex_type: int = 1) -> List[Dict[str, Any]]:
        """Fetch all items from one Plex section using the JSON API.
        
        plex_type: 1 = movies, 2 = shows
        media_type: 'movie' or 'show'
        """
        items_out = []
        page_size = 500
        start = 0

        while True:
            url = f"{plex_url}/library/sections/{section_id}/all"
            params = {
                'type': plex_type,
                'X-Plex-Token': plex_token,
                'X-Plex-Container-Start': start,
                'X-Plex-Container-Size': page_size,
            }
            try:
                resp = requests.get(
                    url, params=params,
                    headers={'Accept': 'application/json'},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"fast_search: error fetching section {section_id} at offset {start}: {e}")
                break

            container = data.get('MediaContainer', {})
            items = container.get('Metadata', [])
            if not items:
                break

            for item in items:
                genres = [g.get('tag', '') for g in item.get('Genre', [])]
                thumb = item.get('thumb', '')
                title = item.get('title', '')
                title_normalized = normalize_text(title)

                items_out.append({
                    'rating_key': str(item.get('ratingKey', '')),
                    'title': title,
                    'title_lower': title.lower(),
                    'title_normalized': title_normalized,
                    'year': item.get('year'),
                    'content_rating': item.get('contentRating', ''),
                    'genres': genres,
                    'genres_lower': [g.lower() for g in genres],
                    'section_id': section_id,
                    'section_name': section_name,
                    'media_type': media_type,
                    'viewCount': item.get('viewCount', 0),
                    'viewOffset': item.get('viewOffset', 0),
                    'thumb': thumb,
                    'rating': item.get('rating', 0.0),  # IMDb/TMDb rating if available
                    'audienceRating': item.get('audienceRating', 0.0),  # Rotten Tomatoes audience
                })

            start += page_size
            total = int(container.get('totalSize', container.get('size', 0)))
            if start >= total:
                break

        logger.info(f"fast_search: loaded {len(items_out)} {media_type}s from section {section_id} ({section_name})")
        return items_out

    def _build_index(self):
        """Fetch all sections and rebuild the in-memory index."""
        plex_url, plex_token = self._get_plex_config()
        if not plex_url or not plex_token:
            logger.warning("fast_search: Plex not configured yet, skipping index build")
            return

        all_items: List[Dict[str, Any]] = []

        # Fetch sections in parallel
        results: Dict[int, List] = {}
        threads = []

        def fetch(sid, sname, media_type, plex_type):
            results[sid] = self._fetch_section(plex_url, plex_token, sid, sname, media_type, plex_type)

        sections = self._discover_sections(plex_url, plex_token)
        if not sections:
            logger.warning("fast_search: no Plex movie/show sections found, skipping index build")
            return
        for sid, sname, media_type, plex_type in sections:
            t = threading.Thread(target=fetch, args=(sid, sname, media_type, plex_type), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        for sid, *_ in sections:
            all_items.extend(results.get(sid, []))

        # Build title dictionary for spelling correction
        title_dict = {item['title_normalized'] for item in all_items if item.get('title_normalized')}

        with self._lock:
            self._sections = [sid for sid, *_ in sections]
            self._index = all_items
            self._title_dictionary = title_dict
            self._last_refresh = time.time()
            self._initialized = True

        movie_count = sum(1 for i in all_items if i.get('media_type') == 'movie')
        show_count = sum(1 for i in all_items if i.get('media_type') == 'show')
        logger.info(f"fast_search: index built with {len(all_items)} total items ({movie_count} movies, {show_count} shows)")
        
        # Start background TMDb popular titles cache refresh (non-blocking)
        if not self._tmdb_titles_cached or time.time() - self._tmdb_cache_time > 86400:  # 24h TTL
            threading.Thread(target=self._refresh_tmdb_titles, daemon=True, name="tmdb-titles-cache").start()

    def _refresh_tmdb_titles(self):
        """
        Background refresh of TMDb popular titles for spelling correction dictionary.
        Fetches popular movies and TV shows, adds normalized titles to the dictionary.
        Handles missing API key gracefully - never blocks requests.
        """
        try:
            from app.settings_manager import SettingsManager
            settings = SettingsManager()
            api_key = settings.get('apis.tmdb.api_key', '') or ''
            
            if not api_key:
                logger.info("fast_search: TMDb API key not configured, skipping popular titles cache")
                return
            
            tmdb_base = settings.get('apis.tmdb.base_url', '') or 'https://api.themoviedb.org/3'
            tmdb_titles = set()
            
            # Fetch popular movies (3 pages = ~60 titles)
            for page in range(1, 4):
                try:
                    resp = requests.get(
                        f'{tmdb_base}/movie/popular',
                        params={'api_key': api_key, 'language': 'en-US', 'page': page},
                        timeout=10
                    )
                    if resp.status_code == 200:
                        results = resp.json().get('results', [])
                        for movie in results:
                            title = movie.get('title', '').strip()
                            if title:
                                tmdb_titles.add(normalize_text(title))
                except Exception as e:
                    logger.warning(f"fast_search: failed to fetch TMDb popular movies page {page}: {e}")
                    break
            
            # Fetch popular TV shows (3 pages = ~60 titles)
            for page in range(1, 4):
                try:
                    resp = requests.get(
                        f'{tmdb_base}/tv/popular',
                        params={'api_key': api_key, 'language': 'en-US', 'page': page},
                        timeout=10
                    )
                    if resp.status_code == 200:
                        results = resp.json().get('results', [])
                        for show in results:
                            name = show.get('name', '').strip()
                            if name:
                                tmdb_titles.add(normalize_text(name))
                except Exception as e:
                    logger.warning(f"fast_search: failed to fetch TMDb popular TV page {page}: {e}")
                    break
            
            # Merge with existing dictionary
            with self._lock:
                self._title_dictionary.update(tmdb_titles)
                self._tmdb_titles_cached = True
                self._tmdb_cache_time = time.time()
            
            logger.info(f"fast_search: cached {len(tmdb_titles)} TMDb popular titles for spelling correction")
        
        except Exception as e:
            logger.warning(f"fast_search: TMDb popular titles cache refresh failed: {e}")

    def _refresh_worker(self):
        """Background thread: build index once, then refresh every 15 min."""
        # Initial build
        try:
            self._build_index()
        except Exception as e:
            logger.error(f"fast_search: initial index build failed: {e}")

        # Periodic refresh
        while True:
            time.sleep(REFRESH_INTERVAL)
            try:
                logger.info("fast_search: starting scheduled index refresh")
                self._build_index()
            except Exception as e:
                logger.error(f"fast_search: scheduled refresh failed: {e}")

    def start(self):
        """Start the background index refresh thread."""
        t = threading.Thread(target=self._refresh_worker, daemon=True, name="fast-search-index")
        t.start()
        logger.info("fast_search: background index thread started")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self,
               q: Optional[str] = None,
               genre: Optional[str] = None,
               content_rating: Optional[str] = None,
               year: Optional[int] = None,
               section: Optional[int] = None,
               media_type: Optional[str] = None,
               limit: int = 100,
               fuzzy: bool = True) -> List[Dict[str, Any]]:
        """
        Search the in-memory index with typo-tolerant fuzzy matching.
        All filters are ANDed together.
        Title search supports fuzzy matching with normalization.
        media_type: 'movie' or 'show' (optional filter).
        fuzzy: Enable typo-tolerant fuzzy search (default True).
        Returns list of result dicts sorted by relevance (internal keys stripped).
        """
        genre_lower = genre.lower().strip() if genre else None
        cr_lower = content_rating.lower().strip() if content_rating else None
        mt_lower = media_type.lower().strip() if media_type else None

        with self._lock:
            snapshot = list(self._index)  # copy under lock

        # If no query, use simple filtering (browse mode)
        if not q:
            results = []
            for item in snapshot:
                if genre_lower and genre_lower not in item['genres_lower']:
                    continue
                if cr_lower and cr_lower != item['content_rating'].lower():
                    continue
                if year is not None and item['year'] != year:
                    continue
                if section is not None and item['section_id'] != section:
                    continue
                if mt_lower and item.get('media_type', 'movie') != mt_lower:
                    continue
                results.append(item)
                if len(results) >= limit:
                    break
            return self._clean_results(results)

        # Query-based search with fuzzy matching
        q = q.strip()
        q_lower = q.lower()
        q_norm = normalize_text(q)

        scored_results = []
        
        for item in snapshot:
            # Apply non-title filters first
            if genre_lower and genre_lower not in item['genres_lower']:
                continue
            if cr_lower and cr_lower != item['content_rating'].lower():
                continue
            if year is not None and item['year'] != year:
                continue
            if section is not None and item['section_id'] != section:
                continue
            if mt_lower and item.get('media_type', 'movie') != mt_lower:
                continue

            # Calculate fuzzy match score
            title = item['title']
            title_norm = item.get('title_normalized', normalize_text(title))
            
            if fuzzy:
                match_score, match_type = fuzzy_match_score(q, title, q_norm, title_norm)
                
                # Filter out very weak matches
                if match_score < 0.4:
                    continue
            else:
                # Legacy substring matching
                if q_lower not in item['title_lower']:
                    continue
                match_score = 0.8 if item['title_lower'] == q_lower else 0.7
                match_type = 'exact' if item['title_lower'] == q_lower else 'fuzzy'

            # Calculate popularity score
            popularity = calculate_popularity_score(item)
            
            # Combined score: match score is primary, popularity is tiebreaker
            # Match score weighted 80%, popularity 20%
            combined_score = (match_score * 0.8) + (min(popularity / 10.0, 1.0) * 0.2)
            
            scored_results.append({
                'item': item,
                'score': combined_score,
                'match_score': match_score,
                'match_type': match_type,
                'popularity': popularity,
            })

        # Sort by score (descending), then by match type priority, then by popularity
        match_type_priority = {'exact': 0, 'prefix': 1, 'fuzzy': 2, 'none': 3}
        scored_results.sort(
            key=lambda x: (-x['score'], match_type_priority.get(x['match_type'], 3), -x['popularity'])
        )

        # Extract top results
        results = [r['item'] for r in scored_results[:limit]]
        
        return self._clean_results(results)

    def _clean_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Strip internal search helper fields before returning."""
        clean = []
        for m in results:
            clean.append({
                'rating_key': m['rating_key'],
                'title': m['title'],
                'year': m['year'],
                'content_rating': m['content_rating'],
                'genres': m['genres'],
                'section_id': m['section_id'],
                'section_name': m['section_name'],
                'media_type': m.get('media_type', 'movie'),
                'viewCount': m['viewCount'],
                'viewOffset': m['viewOffset'],
                'thumb': m['thumb'],
                'rating': m.get('rating', 0.0),
                'audienceRating': m.get('audienceRating', 0.0),
            })
        return clean

    def find_spelling_correction(self, query: str, threshold: float = 0.6) -> Optional[str]:
        """
        Find the best spelling correction for a query from the title dictionary.
        Returns the closest matching title if similarity >= threshold, else None.
        """
        query_norm = normalize_text(query)
        
        with self._lock:
            title_dict = self._title_dictionary

        if not query_norm or not title_dict:
            return None

        best_match = None
        best_score = threshold

        for title_norm in title_dict:
            # Quick trigram similarity check
            score = trigram_similarity(query_norm, title_norm)
            
            if score > best_score:
                best_score = score
                best_match = title_norm

        return best_match

    def status(self) -> Dict[str, Any]:
        """Return index status info."""
        with self._lock:
            total = len(self._index)
            movie_count = sum(1 for i in self._index if i.get('media_type') == 'movie')
            show_count = sum(1 for i in self._index if i.get('media_type') == 'show')
            return {
                'initialized': self._initialized,
                'total_items': total,
                'total_movies': movie_count,
                'total_shows': show_count,
                'last_refresh': self._last_refresh,
                'last_refresh_age_seconds': int(time.time() - self._last_refresh) if self._last_refresh else None,
                'sections': list(self._sections),
            }


# Global singleton
fast_search_index = FastSearchIndex()
