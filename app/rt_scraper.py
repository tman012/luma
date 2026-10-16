#!/usr/bin/env python3
"""
Rotten Tomatoes Review Scraper
Fetches critic and audience reviews from Rotten Tomatoes using the RTCF API
"""

import copy
import re
import threading
import time
import logging
import requests
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)



# RTCF review API backoff: after a 401/403 RT wants credentials we don't have,
# so stop calling it for this process for RTCF_BACKOFF_S and log one line.
RTCF_BACKOFF_S = 3600
_rtcf_state = {'until': 0.0}
_rtcf_lock = threading.Lock()


def _rtcf_backed_off() -> bool:
    return time.time() < _rtcf_state['until']


def _rtcf_note_status(status: int) -> None:
    if status not in (401, 403):
        logger.debug(f"RTCF API returned status {status}")
        return
    with _rtcf_lock:
        if time.time() < _rtcf_state['until']:
            return
        _rtcf_state['until'] = time.time() + RTCF_BACKOFF_S
    logger.warning(f"RTCF review API answered {status}; review fetches paused for "
                   f"{RTCF_BACKOFF_S // 60} min")

class RTScraper:
    """Scraper for Rotten Tomatoes movie and TV show reviews using RTCF API"""

    BASE_URL = "https://www.rottentomatoes.com"

    # In-memory cache with 1-hour TTL
    _cache: Dict[str, dict] = {}
    _cache_ttl = 3600  # 1 hour in seconds

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        })

    def title_to_slug(self, title: str, year: Optional[int] = None) -> List[str]:
        """
        Convert movie title to RT URL slug format(s).
        Returns multiple possible slugs to try for better matching.
        Year-suffixed slugs are tried first for newer movies to avoid matching older films.
        """
        # Normalize title: lowercase, replace spaces with underscores
        slug = title.lower().strip()

        # Remove special characters except alphanumeric and spaces
        slug = re.sub(r'[^\w\s]', '', slug)

        # Replace multiple spaces with single space, then spaces with underscores
        slug = re.sub(r'\s+', '_', slug)

        # Remove trailing underscores
        slug = slug.strip('_')

        slugs = []

        # Try with year suffix FIRST for better matching of remakes/new versions
        if year:
            slugs.append(f"{slug}_{year}")
            if slug.startswith('the_'):
                slugs.append(f"{slug[4:]}_{year}")
            if not slug.startswith('the_'):
                slugs.append(f"the_{slug}_{year}")

        # Then try without year
        slugs.append(slug)

        # Try without "the" prefix
        if slug.startswith('the_'):
            slugs.append(slug[4:])

        # Try with "the" prefix added
        if not slug.startswith('the_'):
            slugs.append(f"the_{slug}")

        return slugs

    def _get_cached(self, cache_key: str) -> Optional[dict]:
        """Get cached result if still valid.
        Returns a deep copy so callers can never mutate the canonical cache entry.
        """
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if time.time() - entry['timestamp'] < self._cache_ttl:
                return copy.deepcopy(entry['data'])
            else:
                del self._cache[cache_key]
        return None

    def _set_cache(self, cache_key: str, data: dict):
        """Store result in cache"""
        self._cache[cache_key] = {
            'data': data,
            'timestamp': time.time()
        }

    def _extract_ems_id(self, html: str) -> Optional[str]:
        """Extract the emsId (UUID) from a movie or TV season page HTML"""
        patterns = [
            # Common JSON patterns
            r'"emsId"\s*:\s*"([a-f0-9-]{36})"',
            r'"titleId"\s*:\s*"([a-f0-9-]{36})"',
            r'"id"\s*:\s*"([a-f0-9-]{36})"',
            r'"movieId"\s*:\s*"([a-f0-9-]{36})"',
            # Data attributes
            r'data-ems-id="([a-f0-9-]{36})"',
            r'data-movie-id="([a-f0-9-]{36})"',
            # Script/JSON patterns
            r'scoreboard[^}]*"([a-f0-9-]{36})"',
            r'mediaId["\s:]+([a-f0-9-]{36})',
            # URL patterns in page
            r'/napi/movie/([a-f0-9-]{36})',
            r'/cnapi/movie/([a-f0-9-]{36})',
            r'/napi/tv/seasons/([a-f0-9-]{36})',
            r'/cnapi/tv/seasons/([a-f0-9-]{36})',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                ems_id = match.group(1)
                logger.debug(f"Found emsId: {ems_id} using pattern: {pattern}")
                return ems_id

        logger.debug("Could not extract emsId from page")
        return None

    def _extract_critic_score(self, html: str) -> Optional[int]:
        """Extract the Tomatometer (critic) score"""
        patterns = [
            # New RT JSON format: "criticsScore":{"score":"81"
            r'"criticsScore"\s*:\s*\{[^}]*"score"\s*:\s*"(\d+)"',
            # Alternate JSON format
            r'"criticsScore"[^}]*"score"\s*:\s*(\d+)',
            # score-board attributes
            r'<score-board[^>]*?tomatometerscore="(\d+)"',
            # Legacy patterns
            r'"tomatometerScore"\s*:\s*(\d+)',
            r'data-tomatometer="(\d+)"',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue

        return None

    def _extract_audience_score(self, html: str) -> Optional[int]:
        """Extract the audience score"""
        patterns = [
            # New RT JSON format: "audienceScore":{"score":"96"
            r'"audienceScore"\s*:\s*\{[^}]*"score"\s*:\s*"(\d+)"',
            # Alternate JSON format
            r'"audienceScore"[^}]*"score"\s*:\s*(\d+)',
            # score-board attributes
            r'<score-board[^>]*?audiencescore="(\d+)"',
            # Legacy patterns
            r'"audienceScore"\s*:\s*(\d+)',
            r'data-audiencescore="(\d+)"',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue

        return None

    def _discover_season_urls(self, series_html: str, slug: str) -> List[str]:
        """Extract available season URLs from a TV series page.
        Returns list of season paths like ['/tv/breaking_bad/s05', '/tv/breaking_bad/s04', ...].
        Ordered newest-first so we fetch the most-reviewed season.
        """
        # Look for season links in the HTML (e.g., /tv/breaking_bad/s01)
        season_pattern = rf'/tv/{re.escape(slug)}/s(\d+)'
        matches = re.findall(season_pattern, series_html)
        if not matches:
            return []

        # Deduplicate, sort descending (newest season first — typically has most reviews)
        season_nums = sorted(set(int(m) for m in matches), reverse=True)
        return [f"/tv/{slug}/s{str(n).zfill(2)}" for n in season_nums]

    def _fetch_season_ems_id(self, season_path: str) -> Optional[str]:
        """Fetch a TV season page and extract its season-level emsId."""
        try:
            url = f"{self.BASE_URL}{season_path}"
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                ems_id = self._extract_ems_id(resp.text)
                if ems_id:
                    logger.info(f"Got season emsId {ems_id} from {season_path}")
                    return ems_id
        except requests.RequestException as e:
            logger.debug(f"Failed to fetch season page {season_path}: {e}")
        return None

    def _fetch_critic_reviews(self, ems_id: str, max_reviews: int, media_type: str = 'movie') -> List[dict]:
        """Fetch critic reviews from RT's RTCF API endpoint with cursor pagination"""
        reviews = []

        # RTCF API paths: movies → /movies/{id}/reviews, TV seasons → /tv/seasons/{id}/reviews
        api_type = 'tv/seasons' if media_type == 'tv' else 'movies'
        base_url = f"{self.BASE_URL}/napi/rtcf/v1/{api_type}/{ems_id}/reviews"

        try:
            # Make sure we have proper headers for the request
            headers = dict(self.session.headers)
            headers['Referer'] = f'{self.BASE_URL}/tv/' if media_type == 'tv' else f'{self.BASE_URL}/m/'
            headers['Accept'] = 'application/json, text/plain, */*'

            cursor = None
            pages_fetched = 0
            max_pages = (max_reviews // 10) + 1  # API returns ~10 per page

            while len(reviews) < max_reviews and pages_fetched < max_pages:
                # Build URL with type=critic and optional cursor
                url = f"{base_url}?type=critic"
                if cursor:
                    url += f"&after={cursor}"

                if _rtcf_backed_off():
                    break
                logger.debug(f"Fetching RT critic reviews: {url}")

                response = self.session.get(url, timeout=15, headers=headers)
                logger.debug(f"RT RTCF response status: {response.status_code}")

                if response.status_code != 200:
                    _rtcf_note_status(response.status_code)
                    break

                try:
                    data = response.json()

                    # Extract reviews from response
                    review_list = data.get('reviews', [])
                    logger.info(f"Found {len(review_list)} critic reviews in page {pages_fetched + 1}")

                    for review_data in review_list:
                        if len(reviews) >= max_reviews:
                            break

                        # Clean up HTML entities in quote
                        quote = review_data.get('reviewQuote', '')
                        quote = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), quote)
                        quote = re.sub(r'&[a-z]+;', ' ', quote)

                        # Extract nested critic and publication info
                        critic_info = review_data.get('critic', {})
                        publication_info = review_data.get('publication', {})

                        # Extract original score if available
                        original_score = review_data.get('originalScore', '')
                        if not original_score:
                            original_score = review_data.get('score', '')

                        review = {
                            'critic': critic_info.get('displayName', 'Unknown Critic'),
                            'publication': publication_info.get('name', ''),
                            'quote': quote.strip(),
                            'fresh': review_data.get('scoreSentiment', 'POSITIVE') == 'POSITIVE',
                            'score': original_score,
                            'is_top_critic': critic_info.get('isTopCritic', False),
                            'date': review_data.get('createDate', ''),
                            'review_url': review_data.get('publicationReviewUrl', '')
                        }

                        if review['quote'] and len(review['quote']) > 10:
                            reviews.append(review)

                    # Check pagination
                    page_info = data.get('pageInfo', {})
                    if not page_info.get('hasNextPage'):
                        break

                    cursor = page_info.get('endCursor')
                    if not cursor:
                        break

                    pages_fetched += 1

                except ValueError as e:
                    logger.warning(f"Failed to parse RTCF JSON response: {e}")
                    break

            if reviews:
                # Sort reviews: top critics first, then by date (newest first)
                reviews.sort(key=lambda r: (not r.get('is_top_critic', False), r.get('date', '')), reverse=False)
                # Actually we want top critics first (True before False when reversed)
                reviews.sort(key=lambda r: (0 if r.get('is_top_critic', False) else 1, r.get('date', '') or ''), reverse=False)
                logger.info(f"Successfully got {len(reviews)} critic reviews from RTCF API (top critics prioritized)")

        except requests.RequestException as e:
            logger.warning(f"Failed to fetch critic reviews from RTCF API: {e}")
        except Exception as e:
            logger.warning(f"Error fetching critic reviews: {e}")

        return reviews

    # Map RTCF STAR_N rating strings to numeric values
    _STAR_RATINGS = {
        'STAR_0': 0, 'STAR_0_5': 0.5, 'STAR_1': 1, 'STAR_1_5': 1.5,
        'STAR_2': 2, 'STAR_2_5': 2.5, 'STAR_3': 3, 'STAR_3_5': 3.5,
        'STAR_4': 4, 'STAR_4_5': 4.5, 'STAR_5': 5,
    }

    def _fetch_audience_reviews(self, ems_id: str, max_reviews: int, media_type: str = 'movie') -> List[dict]:
        """Fetch audience reviews from RT's RTCF API with cursor pagination"""
        reviews = []

        api_type = 'tv/seasons' if media_type == 'tv' else 'movies'
        base_url = f"{self.BASE_URL}/napi/rtcf/v1/{api_type}/{ems_id}/reviews"

        try:
            headers = dict(self.session.headers)
            headers['Referer'] = f'{self.BASE_URL}/tv/' if media_type == 'tv' else f'{self.BASE_URL}/m/'
            headers['Accept'] = 'application/json, text/plain, */*'

            cursor = None
            pages_fetched = 0
            max_pages = (max_reviews // 10) + 1  # API returns ~10 per page

            while len(reviews) < max_reviews and pages_fetched < max_pages:
                url = f"{base_url}?type=audience"
                if cursor:
                    url += f"&after={cursor}"

                if _rtcf_backed_off():
                    break
                logger.debug(f"Fetching RT audience reviews: {url}")
                response = self.session.get(url, timeout=15, headers=headers)
                logger.debug(f"RT RTCF audience response status: {response.status_code}")

                if response.status_code != 200:
                    _rtcf_note_status(response.status_code)
                    break

                try:
                    data = response.json()
                    review_list = data.get('reviews', [])
                    logger.info(f"Found {len(review_list)} audience reviews in page {pages_fetched + 1}")

                    for review_data in review_list:
                        if len(reviews) >= max_reviews:
                            break

                        quote = review_data.get('review', '')
                        quote = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), quote)
                        quote = re.sub(r'&[a-z]+;', ' ', quote)

                        # Parse STAR_N rating format
                        raw_rating = review_data.get('rating', '')
                        rating = self._STAR_RATINGS.get(raw_rating)
                        if rating is None and isinstance(raw_rating, (int, float)):
                            rating = float(raw_rating)

                        review = {
                            'user': review_data.get('displayName', 'Anonymous'),
                            'quote': quote.strip(),
                            'rating': rating,
                            'date': review_data.get('createDate', ''),
                            'is_super_reviewer': review_data.get('isSuperReviewer', False)
                        }

                        if review['quote'] and len(review['quote']) > 10:
                            reviews.append(review)

                    # Check pagination
                    page_info = data.get('pageInfo', {})
                    if not page_info.get('hasNextPage'):
                        break

                    cursor = page_info.get('endCursor')
                    if not cursor:
                        break

                    pages_fetched += 1

                except ValueError as e:
                    logger.warning(f"Failed to parse RTCF audience JSON: {e}")
                    break

            if reviews:
                logger.info(f"Successfully got {len(reviews)} audience reviews from RTCF API")

        except requests.RequestException as e:
            logger.warning(f"Failed to fetch audience reviews from RTCF API: {e}")
        except Exception as e:
            logger.warning(f"Error fetching audience reviews: {e}")

        return reviews

    def _extract_reviews_from_html(self, html: str, max_reviews: int) -> tuple:
        """Extract reviews from embedded JSON in HTML page"""
        import json
        critic_reviews = []
        audience_reviews = []

        # Look for embedded reviewsData JSON
        reviews_data_match = re.search(
            r'<script[^>]*data-json="reviewsData"[^>]*>([^<]+)</script>',
            html,
            re.IGNORECASE
        )

        if reviews_data_match:
            try:
                data = json.loads(reviews_data_match.group(1))
                logger.debug(f"Found embedded reviewsData with keys: {list(data.keys())}")

                # Extract audience reviews from embedded data
                if 'reviews' in data:
                    for review_data in data['reviews']:
                        quote = review_data.get('review', '')
                        if quote and len(quote) > 10:
                            rating = review_data.get('rating')
                            audience_reviews.append({
                                'user': review_data.get('displayName', 'Anonymous'),
                                'quote': quote.strip(),
                                'rating': rating,
                                'date': review_data.get('displayDate', ''),
                                'is_super_reviewer': False
                            })
                            if len(audience_reviews) >= max_reviews:
                                break
            except json.JSONDecodeError as e:
                logger.debug(f"Failed to parse reviewsData JSON: {e}")

        # Look for critic reviews in scorecardData or similar
        scorecard_match = re.search(
            r'"criticsScore":\s*\{[^}]+\}.*?"reviews":\s*\[([^\]]+)\]',
            html,
            re.DOTALL
        )

        # Also try to get critic reviews from the reviews page
        return critic_reviews, audience_reviews

    def _fetch_critic_reviews_from_page(self, slug: str, max_reviews: int, path_prefix: str = '/m/') -> List[dict]:
        """Fetch critic reviews by scraping the reviews page HTML"""
        import json
        reviews = []

        try:
            url = f"{self.BASE_URL}{path_prefix}{slug}/reviews"
            response = self.session.get(url, timeout=10)

            if response.status_code == 200:
                html = response.text

                # Look for critic reviews in embedded JSON
                # RT embeds review data in script tags
                patterns = [
                    r'<script[^>]*type="application/json"[^>]*>(\{[^<]*"reviews"[^<]*\})</script>',
                    r'"reviews"\s*:\s*(\[[^\]]*\])',
                ]

                for pattern in patterns:
                    matches = re.findall(pattern, html, re.DOTALL)
                    for match in matches:
                        try:
                            # Try to parse as JSON
                            if match.startswith('['):
                                review_list = json.loads(match)
                            else:
                                data = json.loads(match)
                                review_list = data.get('reviews', [])

                            for review_data in review_list:
                                quote = review_data.get('quote', review_data.get('review', ''))
                                # Clean HTML entities
                                quote = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), quote)
                                quote = re.sub(r'&[a-z]+;', ' ', quote)

                                if quote and len(quote) > 10:
                                    # Check if this is a critic review (has criticName) or audience
                                    if 'criticName' in review_data:
                                        reviews.append({
                                            'critic': review_data.get('criticName', 'Unknown'),
                                            'publication': review_data.get('publicationName', ''),
                                            'quote': quote.strip(),
                                            'fresh': review_data.get('isFresh', True),
                                            'score': review_data.get('originalScore', ''),
                                            'is_top_critic': review_data.get('isTopCritic', False),
                                            'date': review_data.get('creationDate', ''),
                                            'review_url': review_data.get('reviewUrl', '')
                                        })
                                        if len(reviews) >= max_reviews:
                                            break

                            if reviews:
                                logger.debug(f"Got {len(reviews)} critic reviews from HTML")
                                return reviews

                        except json.JSONDecodeError:
                            continue

        except Exception as e:
            logger.debug(f"Failed to fetch critic reviews from page: {e}")

        return reviews

    def get_reviews(self, title: str, year: Optional[int] = None, max_reviews: int = 100, media_type: str = 'movie') -> dict:
        """
        Main method to fetch RT reviews for a movie or TV show.
        media_type: 'movie' or 'tv'
        Returns aggregated review data or empty dict if not found.
        """
        cache_key = f"{media_type}_{title.lower()}_{year}_{max_reviews}"

        # Check cache first
        cached = self._get_cached(cache_key)
        if cached:
            logger.debug(f"RT cache hit for: {title}")
            return cached

        # Try different slug formats
        slugs = self.title_to_slug(title, year)

        # RT uses /m/ for movies and /tv/ for TV shows
        path_prefix = '/tv/' if media_type == 'tv' else '/m/'

        for slug in slugs:
            url = f"{self.BASE_URL}{path_prefix}{slug}"
            try:
                response = self.session.get(url, timeout=10)

                if response.status_code == 200:
                    html = response.text

                    # Verify this is actually a valid RT page with scores
                    if 'Tomatometer' not in html and 'tomatometer' not in html.lower():
                        continue

                    result = {
                        'success': True,
                        'slug': slug,
                        'url': url,
                        'critic_score': self._extract_critic_score(html),
                        'audience_score': self._extract_audience_score(html),
                        'critic_reviews': [],
                        'audience_reviews': [],
                        'reviews_url': f"{url}/reviews"
                    }

                    if media_type == 'tv':
                        # --- TV shows: reviews are per-season on RT ---
                        # The series page has scores but the emsId is series-level.
                        # We need a season-level emsId for the RTCF reviews API.
                        season_ems_id = None
                        season_paths = self._discover_season_urls(html, slug)

                        # Try seasons newest-first to find one with a valid emsId
                        for season_path in season_paths[:3]:  # Try up to 3 seasons
                            season_ems_id = self._fetch_season_ems_id(season_path)
                            if season_ems_id:
                                # Update reviews_url to point to the season reviews page
                                result['reviews_url'] = f"{self.BASE_URL}{season_path}/reviews"
                                break

                        if not season_ems_id and not season_paths:
                            # Fallback: try /tv/{slug}/s01 directly
                            season_ems_id = self._fetch_season_ems_id(f"/tv/{slug}/s01")

                        if season_ems_id:
                            result['critic_reviews'] = self._fetch_critic_reviews(season_ems_id, max_reviews, media_type)
                            if not result['critic_reviews']:
                                # HTML fallback: try the season reviews page
                                for sp in season_paths[:2]:
                                    result['critic_reviews'] = self._fetch_critic_reviews_from_page(
                                        slug + '/' + sp.split('/')[-1], max_reviews, '/tv/')
                                    if result['critic_reviews']:
                                        break

                            result['audience_reviews'] = self._fetch_audience_reviews(season_ems_id, max_reviews, media_type)
                        else:
                            logger.warning(f"Could not find season emsId for TV show: {title}")

                    else:
                        # --- Movies ---
                        ems_id = self._extract_ems_id(html)

                        # Critic reviews: API first, HTML scrape fallback
                        if ems_id:
                            result['critic_reviews'] = self._fetch_critic_reviews(ems_id, max_reviews, media_type)
                        if not result['critic_reviews']:
                            result['critic_reviews'] = self._fetch_critic_reviews_from_page(slug, max_reviews, path_prefix)

                        # Audience reviews: API first, HTML embedded fallback
                        if ems_id:
                            result['audience_reviews'] = self._fetch_audience_reviews(ems_id, max_reviews, media_type)
                        if not result['audience_reviews']:
                            _, audience_from_html = self._extract_reviews_from_html(html, max_reviews)
                            if audience_from_html:
                                result['audience_reviews'] = audience_from_html

                    # Mark as successful if we got at least a score or reviews
                    if (result['critic_score'] is None and
                        result['audience_score'] is None and
                        not result['critic_reviews'] and
                        not result['audience_reviews']):
                        result['success'] = False
                        result['error'] = 'Could not extract review data'
                    else:
                        self._set_cache(cache_key, result)
                        return result

            except requests.RequestException as e:
                logger.debug(f"RT request failed for {url}: {e}")
                continue
            except Exception as e:
                logger.debug(f"RT parse failed for {url}: {e}")
                continue

        # Not found - cache empty result to avoid repeated lookups
        label = 'TV show' if media_type == 'tv' else 'Movie'
        empty_result = {'success': False, 'error': f'{label} not found on Rotten Tomatoes'}
        self._set_cache(cache_key, empty_result)
        return empty_result


# Singleton instance
rt_scraper = RTScraper()
