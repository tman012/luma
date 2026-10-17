#!/usr/bin/env python3
"""
Fast In-Memory Media Search Index
Preloads all Plex library metadata (movies + TV shows) on startup for millisecond search.
Auto-refreshes every 15 minutes in background.
Supports typo-tolerant fuzzy search with normalization and similarity matching.
"""

import logging
import math
import re
import threading
import time
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional, Any, Set, Tuple

import requests

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False
    process = None

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
    - Casefold (aggressive lowercase for i18n)
    - Transliterate special characters (Æ→ae, ß→ss, Ø→o)
    - Remove accents/diacritics
    - Replace '&' with 'and'
    - Remove leading articles (the, a, an, etc.)
    - Remove all punctuation and extra whitespace
    """
    if not text:
        return ''
    
    # Casefold for better international support
    text = text.casefold()
    
    # Transliterate special characters before NFD normalization
    transliterations = {
        'æ': 'ae', 'ß': 'ss', 'ø': 'o',
        'œ': 'oe', 'ð': 'd', 'þ': 'th',
    }
    for old, new in transliterations.items():
        text = text.replace(old, new)
    
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


def _one_edit(a: str, b: str) -> bool:
    """True when two words of 3+ letters differ by exactly one substituted,
    inserted or deleted letter (same first letter, so 'cat' != 'bat')."""
    if len(a) < 3 or len(b) < 3 or a == b or abs(len(a) - len(b)) > 1 or a[0] != b[0]:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return True
    return False


def fuzzy_match_score(query: str, title: str, query_norm: str, title_norm: str) -> Tuple[float, str]:
    """
    Calculate fuzzy match score for a query against a title.
    Returns (score, match_type) where:
    - score: similarity within tier (0.0 to 1.0)
    - match_type: 'exact', 'prefix', 'substring', 'token', 'fuzzy', 'none'
    
    Ranking is tier-first: exact > prefix > substring > token > fuzzy,
    with score used as tiebreaker within each tier.
    """
    q_lower = query.lower()
    t_lower = title.lower()
    
    # Tier 1: Exact match
    if q_lower == t_lower:
        return (1.0, 'exact')
    if query_norm == title_norm:
        return (1.0, 'exact')
    
    # Tier 2: Prefix match
    if t_lower.startswith(q_lower):
        return (1.0, 'prefix')
    if title_norm.startswith(query_norm):
        return (0.95, 'prefix')
    
    # Tier 3: Substring match
    if q_lower in t_lower:
        # Score by position (earlier is better)
        position_score = 1.0 - (t_lower.index(q_lower) / len(t_lower))
        return (position_score, 'substring')
    if query_norm in title_norm:
        position_score = 1.0 - (title_norm.index(query_norm) / len(title_norm))
        return (position_score * 0.95, 'substring')
    
    # Tier 4: Token-based matching
    query_tokens = query_norm.split()
    title_tokens = title_norm.split()
    token_score = token_similarity(query_tokens, title_tokens)
    
    if token_score >= 0.6:
        return (token_score, 'token')
    
    # Tier 5: Fuzzy matching (typos) with stricter token-aware scoring
    # Require: (1) good overall ratio AND (2) good per-token similarity
    if HAS_RAPIDFUZZ:
        # Overall similarity
        overall_ratio = fuzz.ratio(query_norm, title_norm) / 100.0
        
        # Per-token similarity: each query token must match at least one title token well
        if len(query_tokens) > 0:
            min_token_scores = []
            for q_token in query_tokens:
                best_match = 0.0
                for t_token in title_tokens:
                    token_ratio = fuzz.ratio(q_token, t_token) / 100.0
                    if token_ratio < 0.7 and _one_edit(q_token, t_token):
                        # Short words: one wrong letter ('nit' for 'nut')
                        # scores 0.67 by ratio but is a plain typo.
                        token_ratio = 0.7
                    best_match = max(best_match, token_ratio)
                min_token_scores.append(best_match)
            
            # Require each token to have at least 0.7 match with some title token
            min_token_match = min(min_token_scores) if min_token_scores else 0.0
            
            # Combined score: overall ratio weighted with minimum token similarity
            # This prevents "amelie" from matching "dream fire" (no token overlap)
            if min_token_match < 0.7:
                return (0.0, 'none')
            
            fuzzy_score = (overall_ratio * 0.7) + (min_token_match * 0.3)
        else:
            fuzzy_score = overall_ratio
    else:
        # Fallback to difflib
        trigram_score = trigram_similarity(query_norm, title_norm)
        fuzzy_score = max(trigram_score, SequenceMatcher(None, query_norm, title_norm).ratio())
    
    if fuzzy_score >= 0.6:
        return (fuzzy_score, 'fuzzy')
    
    return (fuzzy_score, 'none')


def calculate_popularity_score(item: Dict[str, Any]) -> float:
    """
    Calculate popularity score for ranking as a tiebreaker within match tiers.
    Uses Plex viewCount (logarithmic scale) as primary signal.
    """
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
        self._tmdb_refreshing = False
        self._initialized = False
        self._sections: List[int] = []
        self._library_titles: Set[str] = set()  # Normalized library titles
        self._tmdb_titles: Set[str] = set()  # Normalized TMDb popular titles
        self._title_map: Dict[str, str] = {}  # Normalized -> display title map (library + TMDb)
        self._tmdb_titles_cached: bool = False
        self._tmdb_cache_time: float = 0.0
        self._tmdb_retry_after: float = 0.0  # Unix timestamp when we can retry after 429
        self._tmdb_backoff_count: int = 0  # Number of consecutive failures for exponential backoff

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
                                headers={'Accept': 'application/json', 'X-Plex-Token': plex_token},
                                timeout=15, allow_redirects=False)
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

        # Build library title dictionary and normalized->display map for spelling correction
        library_titles = {item['title_normalized'] for item in all_items if item.get('title_normalized')}
        library_title_map = {item['title_normalized']: item['title'] 
                            for item in all_items if item.get('title_normalized')}

        with self._lock:
            self._sections = [sid for sid, *_ in sections]
            self._index = all_items
            self._library_titles = library_titles
            # Rebuild title map from scratch: library + current TMDb titles
            # Get current TMDb title map before clearing
            tmdb_title_map = {norm: title for norm, title in self._title_map.items() 
                            if norm in self._tmdb_titles}
            self._title_map = {**library_title_map, **tmdb_title_map}
            self._last_refresh = time.time()
            self._initialized = True

        movie_count = sum(1 for i in all_items if i.get('media_type') == 'movie')
        show_count = sum(1 for i in all_items if i.get('media_type') == 'show')
        logger.info(f"fast_search: index built with {len(all_items)} total items ({movie_count} movies, {show_count} shows)")
        
        # Start background TMDb popular titles cache refresh (non-blocking)
        # Use shorter TTL (1 hour) after failures, normal 24h TTL after success
        with self._lock:
            cached = self._tmdb_titles_cached
            cache_age = time.time() - self._tmdb_cache_time if self._tmdb_cache_time else float('inf')
        
        ttl = 86400 if cached else 3600  # 24h if cached successfully, 1h if not
        if not cached or cache_age > ttl:
            self._maybe_start_tmdb_refresh()

    def _maybe_start_tmdb_refresh(self) -> bool:
        """Start the background TMDb titles refresh unless TMDb has us backed off."""
        if self._is_tmdb_rate_limited():
            logger.info("fast_search: TMDb rate limited, not starting popular titles refresh")
            return False
        threading.Thread(target=self._refresh_tmdb_titles, daemon=True, name="tmdb-titles-cache").start()
        return True

    def _parse_retry_after(self, response) -> float:
        """Parse Retry-After header and return Unix timestamp when we can retry.
        
        Args:
            response: requests.Response object from a 429 error
            
        Returns:
            Unix timestamp when we can retry, or 0.0 if no header present
        """
        retry_after = response.headers.get('Retry-After')
        if not retry_after:
            return 0.0
        
        # Try parsing as seconds (integer)
        try:
            seconds = int(retry_after)
            return time.time() + seconds
        except ValueError:
            pass
        
        # Try parsing as HTTP-date
        try:
            dt = parsedate_to_datetime(retry_after)
            return dt.timestamp()
        except (TypeError, ValueError):
            pass
        
        return 0.0
    
    def _calculate_backoff(self) -> float:
        """Calculate exponential backoff delay in seconds.
        
        Returns:
            Delay in seconds (60s * 2^count, capped at 3600s)
        """
        # Start at 60s, double each time, cap at 1h
        delay = 60 * (2 ** self._tmdb_backoff_count)
        return min(delay, 3600)
    
    def _handle_tmdb_429(self, response) -> None:
        """Handle TMDb 429 rate limit response.
        
        Sets retry_after timestamp from header or exponential backoff.
        """
        with self._lock:
            # Try to get Retry-After from header
            retry_after_ts = self._parse_retry_after(response)
            
            if retry_after_ts > 0:
                # Use header value
                self._tmdb_retry_after = retry_after_ts
            else:
                # Use exponential backoff
                backoff_seconds = self._calculate_backoff()
                self._tmdb_retry_after = time.time() + backoff_seconds
                self._tmdb_backoff_count += 1
    
    def _reset_tmdb_backoff(self) -> None:
        """Reset backoff state after successful request."""
        with self._lock:
            self._tmdb_backoff_count = 0
    
    def _is_tmdb_rate_limited(self) -> bool:
        """Check if we're currently rate limited by TMDb.
        
        Returns:
            True if we should wait before making requests
        """
        with self._lock:
            return time.time() < self._tmdb_retry_after
    
    def _refresh_tmdb_titles(self):
        """
        Background refresh of TMDb popular titles for spelling correction dictionary.
        Fetches popular movies and TV shows, adds normalized titles to the dictionary.
        Handles missing API key gracefully - never blocks requests.
        Thread-safe: only one refresh runs at a time.
        """
        # Honor an active Retry-After / backoff window
        if self._is_tmdb_rate_limited():
            logger.info("fast_search: TMDb rate limited, skipping popular titles refresh")
            return

        # Check if refresh is already running
        with self._lock:
            if self._tmdb_refreshing:
                logger.debug("fast_search: TMDb titles refresh already in progress")
                return
            self._tmdb_refreshing = True
        
        try:
            from app.settings_manager import SettingsManager
            settings = SettingsManager()
            api_key = settings.get('apis.tmdb.api_key', '') or ''
            
            if not api_key:
                logger.info("fast_search: TMDb API key not configured, skipping popular titles cache")
                return
            
            tmdb_base = settings.get('apis.tmdb.base_url', '') or 'https://api.themoviedb.org/3'
            tmdb_titles_list = []
            tmdb_title_map = {}  # normalized -> display for TMDb titles
            failed = False
            
            # Fetch popular movies (10 pages = ~200 titles)
            for page in range(1, 11):
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
                                normalized = normalize_text(title)
                                tmdb_titles_list.append(normalized)
                                tmdb_title_map[normalized] = title
                        # Reset backoff on successful request
                        self._reset_tmdb_backoff()
                    elif resp.status_code == 429:
                        logger.warning(f"fast_search: TMDb rate limit hit on movies page {page}")
                        self._handle_tmdb_429(resp)
                        failed = True
                        break
                except Exception as e:
                    logger.warning(f"fast_search: failed to fetch TMDb popular movies page {page}: {e}")
                    failed = True
                    break
            
            # Fetch popular TV shows (10 pages = ~200 titles)
            if not failed:
                for page in range(1, 11):
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
                                    normalized = normalize_text(name)
                                    tmdb_titles_list.append(normalized)
                                    tmdb_title_map[normalized] = name
                            # Reset backoff on successful request
                            self._reset_tmdb_backoff()
                        elif resp.status_code == 429:
                            logger.warning(f"fast_search: TMDb rate limit hit on TV page {page}")
                            self._handle_tmdb_429(resp)
                            failed = True
                            break
                    except Exception as e:
                        logger.warning(f"fast_search: failed to fetch TMDb popular TV page {page}: {e}")
                        failed = True
                        break
            
            # Build new frozenset and swap under lock
            if tmdb_titles_list and not failed:
                new_tmdb_titles = frozenset(tmdb_titles_list)
                with self._lock:
                    self._tmdb_titles = new_tmdb_titles
                    # Rebuild title map from scratch: current library + new TMDb titles
                    library_title_map = {norm: title for norm, title in self._title_map.items() 
                                       if norm in self._library_titles}
                    self._title_map = {**library_title_map, **tmdb_title_map}
                    self._tmdb_titles_cached = True
                    self._tmdb_cache_time = time.time()
                logger.info(f"fast_search: cached {len(new_tmdb_titles)} TMDb popular titles for spelling correction")
            elif failed:
                # Don't mark as cached on failure, retry sooner
                logger.warning("fast_search: TMDb cache refresh incomplete, will retry sooner")
        
        except Exception as e:
            logger.warning(f"fast_search: TMDb popular titles cache refresh failed: {e}")
        
        finally:
            with self._lock:
                self._tmdb_refreshing = False

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

        # Strip query first
        if q:
            q = q.strip()
        
        # If no query after stripping, use simple filtering (browse mode)
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

        # Query-based search
        q_lower = q.lower()
        q_norm = normalize_text(q)
        
        # For very short queries (1-2 chars), use only prefix/substring matching
        use_fuzzy = len(q) > 2 and fuzzy
        
        # Prefilter by first letter + substring for performance (only for longer queries)
        # For 5+ char queries, keep items where any word starts with query's first letter
        # OR the title contains the query as substring (for cases like 'atrix' → 'The Matrix')
        if len(q) >= 5:
            first_letter = q_norm[0] if q_norm else ''
            filtered = []
            for item in snapshot:
                title_norm = item.get('title_normalized', '')
                title_lower = item.get('title_lower', '')
                # Check if any word starts with query first letter OR title contains query substring
                words = title_norm.split()
                if any(word.startswith(first_letter) for word in words) or q_lower in title_lower:
                    filtered.append(item)
            snapshot = filtered if filtered else snapshot  # Fallback to full snapshot if no matches
        
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

            # Calculate match score
            title = item['title']
            title_norm = item['title_normalized']  # always set at index build; no per-item normalize
            
            if use_fuzzy:
                match_score, match_type = fuzzy_match_score(q, title, q_norm, title_norm)
                
                # Tighten threshold for 3-4 char queries to avoid false matches
                min_threshold = 0.75 if len(q) <= 4 else 0.4
                if match_score < min_threshold:
                    continue
            else:
                # For short queries (1-2 chars): prefix-only on word starts or title start
                # For fuzzy disabled: exact/prefix/substring
                t_lower = item['title_lower']
                if q_lower == t_lower:
                    match_score, match_type = 1.0, 'exact'
                elif t_lower.startswith(q_lower):
                    match_score, match_type = 1.0, 'prefix'
                elif len(q) <= 2:
                    # For 1-2 char queries: only match at word boundaries
                    # Check if query starts any word in the title
                    words = t_lower.split()
                    found = False
                    for word in words:
                        if word.startswith(q_lower):
                            found = True
                            break
                    if found:
                        match_score, match_type = 0.9, 'prefix'
                    else:
                        continue
                elif q_lower in t_lower:
                    position_score = 1.0 - (t_lower.index(q_lower) / len(t_lower))
                    match_score, match_type = position_score, 'substring'
                else:
                    continue

            # Calculate popularity for tiebreaking
            popularity = calculate_popularity_score(item)
            
            # Calculate length difference for ranking (prefer shorter or exact-length)
            title_len = len(title)
            query_len = len(q)
            len_diff = abs(title_len - query_len)
            
            scored_results.append({
                'item': item,
                'match_score': match_score,
                'match_type': match_type,
                'popularity': popularity,
                'len_diff': len_diff,
                'title_len': title_len,
            })

        # Sort by: tier, match_score, length_difference (prefer exact or shorter), popularity
        tier_priority = {'exact': 0, 'prefix': 1, 'substring': 2, 'token': 3, 'fuzzy': 4, 'none': 5}
        scored_results.sort(
            key=lambda x: (tier_priority.get(x['match_type'], 5), -x['match_score'], x['len_diff'], x['title_len'], -x['popularity'])
        )

        # Extract top results
        results = [r['item'] for r in scored_results[:limit]]
        
        return self._clean_results(results)

    def _clean_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Strip internal search helper fields before returning."""
        clean = []
        for m in results:
            clean.append({
                'rating_key': m.get('rating_key', ''),
                'title': m.get('title', ''),
                'year': m.get('year'),
                'content_rating': m.get('content_rating', ''),
                'genres': m.get('genres', []),
                'section_id': m.get('section_id'),
                'section_name': m.get('section_name', ''),
                'media_type': m.get('media_type', 'movie'),
                'viewCount': m.get('viewCount', 0),
                'viewOffset': m.get('viewOffset', 0),
                'thumb': m.get('thumb', ''),
            })
        return clean

    def find_spelling_correction(self, query: str, threshold: float = 0.6) -> Optional[str]:
        """
        Find the best spelling correction for a query from the combined title dictionary.
        Returns the original (pre-normalized) closest matching title if similarity >= threshold, else None.
        Uses the title map for O(1) lookup (no linear scan).
        """
        query_norm = normalize_text(query)
        
        # Get combined dictionary and title map under lock
        with self._lock:
            combined_titles = self._library_titles | self._tmdb_titles
            title_map = self._title_map.copy()

        if not query_norm or not combined_titles:
            return None

        best_match_norm = None
        best_score = threshold

        for title_norm in combined_titles:
            # Use rapidfuzz if available, else trigram similarity
            if HAS_RAPIDFUZZ:
                score = fuzz.ratio(query_norm, title_norm) / 100.0
            else:
                score = trigram_similarity(query_norm, title_norm)
            
            if score > best_score:
                best_score = score
                best_match_norm = title_norm

        # Return the original title from the map (O(1) lookup)
        if best_match_norm and best_match_norm in title_map:
            return title_map[best_match_norm]
        
        return None

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
