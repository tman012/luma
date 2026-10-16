#!/usr/bin/env python3
"""
Hero Content Cache System
Caches hero section content from Recently Released library with images from fanart.tv and RT ratings.
Runs daily at 5am to refresh content.

Selection criteria:
1. Movies are sorted by most recently added first
2. Movie must have an RT rating from Plex metadata
3. Movie must have HD clearart available on fanart.tv (hdmovieclearart)
4. Top 5 most recently added movies that meet both criteria are selected
"""

import os
import re
import json
import logging
import requests
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Configuration defaults (will be overridden by settings when available)
# Hero cache storage: Images are served from /static/hero_cache (Flask static files).
# Content JSON is stored in /app/data (the in-container persisted volume).
# Hero cache is NOT part of LUMA_CACHE_DIR.
CACHE_DIR = Path('/app/static/hero_cache')
CACHE_JSON = Path('/app/data/hero_content.json')


class HeroCache:
    """Manages hero section content caching from user's library."""

    def __init__(self, settings_manager=None):
        if settings_manager is not None:
            self.tmdb_api_key = settings_manager.get('apis.tmdb.api_key', '')
            self.fanart_api_key = settings_manager.get('apis.fanart.api_key', '')
            self.plex_url = settings_manager.get('plex.url', '')
            self.plex_token = settings_manager.get('plex.token', '')
            self.excluded_genres = settings_manager.get('ui.hero.excluded_genres', ['Sport', 'Sports'])
        else:
            self.tmdb_api_key = os.environ.get('TMDB_API_KEY', '')
            self.fanart_api_key = os.environ.get('FANART_API_KEY', '')
            self.plex_url = os.environ.get('PLEX_URL', '')
            self.plex_token = os.environ.get('PLEX_TOKEN', '')
            self.excluded_genres = ['Sport', 'Sports']
        self.cache_dir = CACHE_DIR
        self.cache_json = CACHE_JSON
        self.session = requests.Session()
        
        # Background healing state
        self._healing_lock = threading.Lock()
        self._last_heal_time = 0
        self._heal_cooldown = 600  # 10 minutes

        # Ensure cache directories exist
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.warning(f"hero_cache: static directory {self.cache_dir} not writable: {e}")
        
        try:
            self.cache_json.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.warning(f"hero_cache: data directory {self.cache_json.parent} not writable: {e}")

    def init_from_settings(self, settings_manager):
        """Re-initialise API keys and config from a SettingsManager instance.

        Called after the settings system is available (after module import time).
        """
        self.tmdb_api_key = settings_manager.get('apis.tmdb.api_key', '') or self.tmdb_api_key
        self.fanart_api_key = settings_manager.get('apis.fanart.api_key', '') or self.fanart_api_key
        self.plex_url = settings_manager.get('plex.url', '') or self.plex_url
        self.plex_token = settings_manager.get('plex.token', '') or self.plex_token
        self.excluded_genres = settings_manager.get('ui.hero.excluded_genres', ['Sport', 'Sports']) or self.excluded_genres

    def get_recently_released_movies(self) -> List[Dict]:
        """Fetch movies from the 'Recently Released' library via Plex API."""
        movies = []
        try:
            from app.plex_service import plex_service

            # Find the "Recently Released" library section
            sections = plex_service.get_library_sections()
            rr_section = next((s for s in sections if s['title'] == 'Recently Released'), None)

            if not rr_section:
                logger.warning("No 'Recently Released' library section found")
                return []

            # Fetch all movies from this section
            result = plex_service.get_all_movies(
                section_id=rr_section['id'],
                sort='addedAt:desc',
                limit=100,
            )

            for item in result.get('movies', []):
                movie = {
                    'rating_key': item['rating_key'],
                    'title': item['title'],
                    'year': item.get('year'),
                    'release_date': None,
                    'rating': item.get('rating'),
                    'duration': item.get('duration'),
                    'added_at': item.get('added_at'),
                    'guid': item.get('guid', ''),
                    'content_rating': item.get('content_rating', ''),
                    'summary': item.get('summary', ''),
                    'user_thumb_url': item.get('thumbPath'),
                    'user_art_url': item.get('artPath'),
                    'user_clear_logo_url': item.get('clearLogoPath'),
                    'genres': item.get('genres', []),
                    'tmdb_id': item.get('tmdb_id'),
                    'imdb_id': item.get('imdb_id'),
                    'library_name': 'Recently Released',
                }
                movies.append(movie)

            logger.info(f"Fetched {len(movies)} movies from Recently Released library via API")

        except Exception as e:
            logger.error(f"Error fetching movies from Plex API: {e}")

        return movies

    def _extract_tmdb_id(self, guid: str) -> Optional[int]:
        """Extract TMDB ID from Plex guid."""
        if not guid:
            return None
        # Format: plex://movie/5d776b...  or com.plexapp.agents.themoviedb://12345
        match = re.search(r'themoviedb://(\d+)', guid)
        if match:
            return int(match.group(1))
        return None

    def _extract_imdb_id(self, guid: str) -> Optional[str]:
        """Extract IMDB ID from Plex guid."""
        if not guid:
            return None
        match = re.search(r'imdb://(tt\d+)', guid)
        if match:
            return match.group(1)
        return None

    def get_tmdb_id_from_title(self, title: str, year: int) -> Optional[int]:
        """Look up TMDB ID by title and year."""
        try:
            url = "https://api.themoviedb.org/3/search/movie"
            params = {
                'api_key': self.tmdb_api_key,
                'query': title,
                'year': year,
                'language': 'en-US'
            }
            response = self.session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('results'):
                    return data['results'][0]['id']
        except Exception as e:
            logger.debug(f"TMDB lookup failed for {title}: {e}")
        return None

    def get_tmdb_external_ids(self, tmdb_id: int) -> Dict:
        """Get external IDs (including IMDB) from TMDB."""
        try:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/external_ids"
            params = {'api_key': self.tmdb_api_key}
            response = self.session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.debug(f"Failed to get external IDs for TMDB {tmdb_id}: {e}")
        return {}

    def filter_movies(self, movies: List[Dict]) -> List[Dict]:
        """Filter out sports genre movies."""
        filtered = []
        for movie in movies:
            genres = movie.get('genres', [])
            if any(excluded in genres for excluded in self.excluded_genres):
                logger.debug(f"Skipping {movie['title']} - excluded genre: {genres}")
                continue
            filtered.append(movie)
        logger.info(f"Filtered to {len(filtered)} movies after genre exclusions")
        return filtered

    def get_rt_score_from_plex(self, rating_key: int) -> Optional[int]:
        """Get Rotten Tomatoes critic score from Plex metadata."""
        try:
            import xml.etree.ElementTree as ET

            url = f"{self.plex_url}/library/metadata/{rating_key}"
            headers = {'X-Plex-Token': self.plex_token}
            response = self.session.get(url, headers=headers, timeout=10)

            if response.status_code != 200:
                return None

            root = ET.fromstring(response.content)
            video = root.find('.//Video')

            if not video:
                return None

            # Parse Rating elements for RT critic score
            for rating_elem in video.findall('.//Rating'):
                rating_image = rating_elem.get('image', '')
                rating_value = rating_elem.get('value', '')
                rating_type = rating_elem.get('type', '')

                if not rating_value:
                    continue

                # Look for RT critic score
                if 'rottentomatoes' in rating_image.lower() and rating_type == 'critic':
                    try:
                        value_float = float(rating_value)
                        # Plex stores RT as 0-10 scale, convert to percentage
                        return int(value_float * 10)
                    except:
                        continue

            return None

        except Exception as e:
            logger.debug(f"Could not get RT score from Plex for {rating_key}: {e}")
            return None

    def has_hd_clearart(self, tmdb_id: int = None, imdb_id: str = None) -> bool:
        """
        Check if a movie has HD clearart available on fanart.tv.
        Returns True only if hdmovieclearart is available.
        """
        if not self.fanart_api_key:
            return False

        # Try with TMDB ID first, then IMDB ID
        identifiers = []
        if tmdb_id:
            identifiers.append(tmdb_id)
        if imdb_id:
            identifiers.append(imdb_id)

        for id_value in identifiers:
            try:
                url = f"https://webservice.fanart.tv/v3/movies/{id_value}"
                params = {'api_key': self.fanart_api_key}

                response = self.session.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    # Only check for HD clearart specifically
                    hd_cleararts = data.get('hdmovieclearart', [])
                    if hd_cleararts:
                        return True

            except Exception as e:
                logger.debug(f"Fanart.tv HD clearart check failed for {id_value}: {e}")

        return False

    def get_fanart_images(self, tmdb_id: int = None, imdb_id: str = None) -> Dict[str, str]:
        """
        Fetch clearlogo, clearart, and backdrop from fanart.tv.
        Returns dict with 'clearlogo', 'clearart', and 'backdrop' URLs.
        """
        images = {'clearlogo': None, 'clearart': None, 'backdrop': None}

        if not self.fanart_api_key:
            logger.warning("No fanart.tv API key configured")
            return images

        # Try with TMDB ID first, then IMDB ID
        identifiers = []
        if tmdb_id:
            identifiers.append(('tmdb_id', tmdb_id))
        if imdb_id:
            identifiers.append(('imdb_id', imdb_id))

        for id_type, id_value in identifiers:
            try:
                url = f"https://webservice.fanart.tv/v3/movies/{id_value}"
                params = {'api_key': self.fanart_api_key}

                response = self.session.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()

                    # Get HD clearlogo (prefer hdmovielogo, fallback to movielogo)
                    logos = data.get('hdmovielogo', []) or data.get('movielogo', [])
                    if logos:
                        # Prefer English logos
                        en_logos = [l for l in logos if l.get('lang') == 'en']
                        logo = en_logos[0] if en_logos else logos[0]
                        images['clearlogo'] = logo.get('url')

                    # Get HD clearart (prefer hdmovieclearart, fallback to movieclearart)
                    cleararts = data.get('hdmovieclearart', []) or data.get('movieclearart', [])
                    if cleararts:
                        # Prefer English clearart
                        en_cleararts = [c for c in cleararts if c.get('lang') == 'en']
                        clearart = en_cleararts[0] if en_cleararts else cleararts[0]
                        images['clearart'] = clearart.get('url')

                    # Get movie backdrop (moviebackground)
                    backdrops = data.get('moviebackground', [])
                    if backdrops:
                        images['backdrop'] = backdrops[0].get('url')

                    if images['clearlogo'] or images['clearart'] or images['backdrop']:
                        logger.debug(f"Found fanart.tv images for {id_value}")
                        return images

            except Exception as e:
                logger.debug(f"Fanart.tv lookup failed for {id_value}: {e}")

        return images

    def download_image(self, url: str, filename: str) -> Optional[str]:
        """Download an image and save to cache directory."""
        if not url:
            return None

        try:
            filepath = self.cache_dir / filename
            response = self.session.get(url, stream=True, timeout=30)

            if response.status_code == 200:
                with open(filepath, 'wb') as f:
                    shutil.copyfileobj(response.raw, f)
                logger.debug(f"Downloaded image: {filename}")
                return f"/static/hero_cache/{filename}"

        except Exception as e:
            logger.error(f"Failed to download image {url}: {e}")

        return None

    def clear_old_cache(self, keep_ids: List[int]):
        """Remove cached images for movies no longer in hero rotation."""
        try:
            keep_prefixes = {f"movie_{id}_" for id in keep_ids}

            for filepath in self.cache_dir.glob("*"):
                if filepath.is_file():
                    should_keep = any(filepath.name.startswith(prefix) for prefix in keep_prefixes)
                    if not should_keep:
                        filepath.unlink()
                        logger.debug(f"Removed old cache file: {filepath.name}")

        except Exception as e:
            logger.error(f"Error clearing old cache: {e}")

    def refresh_hero_content(self) -> List[Dict]:
        """
        Main method to refresh hero content cache from Recently Released library.

        Selection criteria:
        1. Movies are sorted by most recently added first
        2. Movie must have an RT rating from Plex
        3. Movie must have HD clearart available on fanart.tv (hdmovieclearart)
        4. Top 5 most recently added movies that meet both criteria are selected

        Returns list of cached hero movies.
        """
        logger.info("Starting hero content refresh from Recently Released library...")

        # Step 1: Get all movies from Recently Released library (no time limit)
        movies = self.get_recently_released_movies()
        if not movies:
            logger.error("No movies found in Recently Released library")
            return []

        logger.info(f"Found {len(movies)} total movies in Recently Released library")

        # Step 2: Filter out excluded genres
        movies = self.filter_movies(movies)
        if not movies:
            logger.error("No movies left after filtering")
            return []

        # Step 3: For each movie, check RT score and HD clearart availability
        # Only keep movies that have BOTH
        logger.info("Checking RT scores and HD clearart availability for each movie...")
        qualified_movies = []

        for movie in movies:
            # Get RT score from Plex
            rt_score = self.get_rt_score_from_plex(movie['rating_key'])
            if rt_score is None:
                logger.debug(f"Skipping {movie['title']} - no RT rating")
                continue

            movie['rt_score'] = rt_score

            # Ensure we have TMDB/IMDB IDs for fanart.tv lookup
            tmdb_id = movie.get('tmdb_id')
            imdb_id = movie.get('imdb_id')

            if not tmdb_id and movie.get('title') and movie.get('year'):
                tmdb_id = self.get_tmdb_id_from_title(movie['title'], movie['year'])
                movie['tmdb_id'] = tmdb_id

            if tmdb_id and not imdb_id:
                ext_ids = self.get_tmdb_external_ids(tmdb_id)
                imdb_id = ext_ids.get('imdb_id')
                movie['imdb_id'] = imdb_id

            # Check for HD clearart on fanart.tv
            if not self.has_hd_clearart(tmdb_id, imdb_id):
                logger.debug(f"Skipping {movie['title']} - no HD clearart on fanart.tv")
                continue

            # Movie qualifies - has both RT rating and HD clearart
            qualified_movies.append(movie)
            logger.debug(f"Qualified: {movie['title']} ({movie.get('year')}) - RT={rt_score}")

        logger.info(f"Found {len(qualified_movies)} movies with both RT rating and HD clearart")

        if not qualified_movies:
            logger.error("No movies found with both RT rating and HD clearart")
            return []

        # Step 4: Take top 5 most recently added (already sorted by added_at DESC from query)
        top_movies = qualified_movies[:5]

        logger.info(f"Selected top {len(top_movies)} most recently added movies with RT rating and HD clearart")

        # Step 5: Fetch and cache images from fanart.tv
        hero_content = []

        for movie in top_movies:
            rating_key = movie['rating_key']
            tmdb_id = movie.get('tmdb_id')
            imdb_id = movie.get('imdb_id')

            # Get fanart.tv images (IDs already resolved in Step 3)
            fanart_images = self.get_fanart_images(tmdb_id, imdb_id)

            # Download and cache images
            clearlogo_path = None
            clearart_path = None
            backdrop_path = None

            # Always use Plex clearlogo directly (not cached) via proxy
            if movie.get('user_clear_logo_url'):
                clearlogo_path = f"/api/img/plex?url=/library/metadata/{rating_key}/clearLogo"

            # Cache clearart from fanart.tv if available
            if fanart_images['clearart']:
                ext = fanart_images['clearart'].split('.')[-1].split('?')[0]
                clearart_path = self.download_image(
                    fanart_images['clearart'],
                    f"movie_{rating_key}_clearart.{ext}"
                )

            # Cache backdrop from fanart.tv if available
            if fanart_images['backdrop']:
                ext = fanart_images['backdrop'].split('.')[-1].split('?')[0]
                backdrop_path = self.download_image(
                    fanart_images['backdrop'],
                    f"movie_{rating_key}_backdrop.{ext}"
                )

            # Fallback to Plex backdrop if fanart.tv doesn't have it
            if not backdrop_path and movie.get('user_art_url'):
                backdrop_path = f"/api/img/plex?url=/library/metadata/{rating_key}/art"

            # Build poster URL from Plex via proxy
            poster_url = None
            if movie.get('user_thumb_url'):
                poster_url = f"/api/img/plex?url=/library/metadata/{rating_key}/thumb"

            # Build hero content entry
            hero_entry = {
                'rating_key': rating_key,
                'tmdb_id': tmdb_id,
                'imdb_id': imdb_id,
                'title': movie['title'],
                'year': movie.get('year'),
                'summary': movie.get('summary', ''),
                'rt_score': movie.get('rt_score'),
                'duration': movie.get('duration'),
                'content_rating': movie.get('content_rating', ''),
                'genres': movie.get('genres', []),
                'clearlogo_url': clearlogo_path,
                'clearart_url': clearart_path,
                'backdrop_url': backdrop_path,
                'poster_url': poster_url,
                'is_owned': True
            }

            hero_content.append(hero_entry)
            rt_display = movie.get('rt_score') if movie.get('rt_score') is not None else 'N/A'
            logger.info(f"Cached: {movie['title']} ({movie.get('year')}) - RT: {rt_display}")

        # Step 6: Clear old cached images
        keep_ids = [m['rating_key'] for m in hero_content]
        self.clear_old_cache(keep_ids)

        # Step 7: Save hero content JSON
        self.save_cache(hero_content)

        logger.info(f"Hero content refresh complete. Cached {len(hero_content)} movies.")
        return hero_content

    def save_cache(self, content: List[Dict]):
        """Save hero content to JSON file."""
        try:
            self.cache_json.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_json, 'w') as f:
                json.dump({
                    'last_updated': datetime.now().isoformat(),
                    'movies': content
                }, f, indent=2)
            logger.info(f"Saved hero cache to {self.cache_json}")
        except Exception as e:
            logger.error(f"Failed to save hero cache: {e}")

    def load_cache(self) -> List[Dict]:
        """Load hero content from JSON file."""
        try:
            if self.cache_json.exists():
                with open(self.cache_json, 'r') as f:
                    data = json.load(f)
                    return data.get('movies', [])
        except Exception as e:
            logger.error(f"Failed to load hero cache: {e}")
        return []

    def get_missing_images(self, content: List[Dict]) -> List[tuple]:
        """Check for missing image files, return list of (item_index, field, path) tuples."""
        missing = []
        for idx, item in enumerate(content):
            if item.get('backdrop_cache'):
                backdrop_path = self.cache_dir / item['backdrop_cache']
                if not backdrop_path.exists():
                    missing.append((idx, 'backdrop_cache', item['backdrop_cache']))
            if item.get('poster_cache'):
                poster_path = self.cache_dir / item['poster_cache']
                if not poster_path.exists():
                    missing.append((idx, 'poster_cache', item['poster_cache']))
        return missing
    
    def _heal_missing_images_background(self, content: List[Dict], missing: List[tuple]):
        """Re-download only the missing image files (runs in background thread)."""
        try:
            logger.info(f"Background healing: re-downloading {len(missing)} missing images")
            for idx, field, filename in missing:
                item = content[idx]
                # Re-download the image using the original URL from cache
                if field == 'backdrop_cache' and item.get('backdrop_url'):
                    self.download_image(item['backdrop_url'], filename)
                elif field == 'poster_cache' and item.get('poster_url'):
                    self.download_image(item['poster_url'], filename)
            logger.info("Background healing complete")
        except Exception as e:
            logger.error(f"Background healing failed: {e}")
    
    def get_hero_content(self) -> List[Dict]:
        """Get cached hero content, triggering background heal for missing images.
        
        Returns current cache immediately, never blocks the request.
        Missing images trigger background re-download with 10min cooldown and single-flight lock.
        """
        content = self.load_cache()
        
        if not content:
            logger.info("No cached content found, returning empty list")
            return []
        
        # Check for missing images
        missing = self.get_missing_images(content)
        
        if missing:
            # Try to acquire healing lock (non-blocking)
            if self._healing_lock.acquire(blocking=False):
                try:
                    now = time.time()
                    # Check cooldown
                    if now - self._last_heal_time >= self._heal_cooldown:
                        logger.warning(f"Found {len(missing)} missing hero images, starting background heal")
                        self._last_heal_time = now
                        # Start healing in background thread
                        heal_thread = threading.Thread(
                            target=self._heal_missing_images_background,
                            args=(content, missing),
                            daemon=True
                        )
                        heal_thread.start()
                    else:
                        cooldown_remaining = int(self._heal_cooldown - (now - self._last_heal_time))
                        logger.debug(f"Healing in cooldown, {cooldown_remaining}s remaining")
                finally:
                    self._healing_lock.release()
        
        return content


# Singleton instance
hero_cache = HeroCache()


def refresh_hero_cache():
    """CLI entry point for refreshing hero cache."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    hero_cache.refresh_hero_content()


if __name__ == '__main__':
    refresh_hero_cache()
