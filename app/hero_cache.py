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
import fcntl
import json
import logging
import requests
import shutil
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Dict, Optional

from app.paths import data_path, hero_cache_dir

logger = logging.getLogger(__name__)


class HeroCache:
    """Manages hero section content caching from user's library."""

    def __init__(self, settings_manager=None, cache_dir=None, clock: Callable[[], float] = time.monotonic):
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
        
        # Resolved once here (HERO_CACHE_DIR, else $LUMA_CACHE_DIR/hero);
        # main.py copies this value into app.config['HERO_CACHE_DIR'].
        self.cache_dir = Path(cache_dir) if cache_dir is not None else hero_cache_dir()
        self.cache_json = data_path('hero_content.json')
        self.session = requests.Session()

        # Healing state (healing runs only from the background scheduler)
        self._clock = clock
        self._scheduler_thread = None
        self._scheduler_stop = threading.Event()
        self._healing_lock = threading.Lock()
        self._refresh_lock = threading.Lock()  # single-flight refresh in this process
        self._last_heal_time = 0
        self._heal_cooldown = 600  # 10 minutes after successful heal
        self._heal_failed = False
        self._heal_backoff = 60  # Start with 60s after failed heal
        self._heal_backoff_max = 600  # Cap at 10 minutes

        # Ensure cache directory exists (fail-fast)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            error_msg = f"hero_cache: HERO_CACHE_DIR={self.cache_dir} not writable: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        
        try:
            self.cache_json.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            error_msg = f"hero_cache: data directory {self.cache_json.parent} not writable: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

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

    def _plex_download_url(self, plex_path: str) -> str:
        """The Plex URL for an image path. The token never goes in it; see
        _plex_headers()."""
        return f"{self.plex_url}{plex_path}"

    def _plex_headers(self) -> dict:
        return {'X-Plex-Token': self.plex_token}

    @staticmethod
    def _strip_tokens(movies: List[Dict]) -> bool:
        """Remove Plex tokens from stored URLs in place. Returns True if changed."""
        changed = False
        for movie in movies:
            for key, value in list(movie.items()):
                if not isinstance(value, str) or 'x-plex-token' not in value.lower():
                    continue
                parsed = urllib.parse.urlsplit(value)
                query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                         if k.lower() != 'x-plex-token']
                if key.endswith('_upstream_url') and parsed.path.startswith('/library/'):
                    movie[key] = parsed.path
                else:
                    movie[key] = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
                changed = True
        return changed

    def _sanitize_extension(self, url: str) -> str:
        """Extract and sanitize file extension from URL.
        
        Returns lowercase alphanumeric extension (1-5 chars), else 'jpg'.
        """
        path = urllib.parse.urlsplit(url or '').path
        name = path.rsplit('/', 1)[-1]
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''

        # Only keep alphanumeric, max 5 chars
        if re.match(r'^[a-z0-9]{1,5}$', ext):
            return ext
        return 'jpg'
    
    def download_image(self, url: str, filename: str, headers: Optional[dict] = None) -> Optional[str]:
        """Download an image into the cache directory.

        Writes to ``<filename>.tmp`` and atomically renames it into place.
        Returns the filename only; URLs are built at response time.
        """
        if not url or not filename or '/' in filename or filename.startswith('.'):
            return None

        filepath = self.cache_dir / filename
        tmp_filepath = self.cache_dir / f"{filename}.tmp"
        try:
            if headers:
                # Plex: the token rides in a header and a redirect (which would
                # carry it elsewhere) is a failure, not followed.
                response = self.session.get(url, stream=True, timeout=30, headers=headers, allow_redirects=False)
            else:
                response = self.session.get(url, stream=True, timeout=30)
            if response.status_code == 200:
                with open(tmp_filepath, 'wb') as f:
                    shutil.copyfileobj(response.raw, f)
                os.replace(tmp_filepath, filepath)
                logger.debug(f"Downloaded image: {filename}")
                return filename
        except Exception as e:
            safe_url = url.split('?', 1)[0]
            logger.error(f"Failed to download image {safe_url}: {e}")
        finally:
            try:
                tmp_filepath.unlink(missing_ok=True)
            except OSError:
                pass

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
        """Single-flight refresh: at most one runs at a time, across threads and
        processes sharing the cache dir. A caller that finds one already
        running skips (it would only repeat the same work) and gets the cache.
        """
        if not self._refresh_lock.acquire(blocking=False):
            logger.info("Hero refresh already running in this process; skipping")
            return self.load_cache()
        lock_fd = None
        try:
            try:
                # O_RDWR without O_TRUNC: never truncate a file another process holds
                lock_fd = os.open(self.cache_dir / '.refresh.lock', os.O_CREAT | os.O_RDWR, 0o644)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.info("Hero refresh already running in another process; skipping")
                return self.load_cache()
            except OSError as e:
                logger.error("Hero refresh skipped: cannot lock %s: %s",
                             self.cache_dir / '.refresh.lock', e)
                return self.load_cache()
            return self._refresh_hero_content_unlocked()
        finally:
            if lock_fd is not None:
                os.close(lock_fd)  # releases the flock
            self._refresh_lock.release()

    def _refresh_hero_content_unlocked(self) -> List[Dict]:
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
                clearlogo_path = f"plex:/library/metadata/{rating_key}/clearLogo"

            # Cache clearart from fanart.tv if available
            clearart_cache_file = None
            clearart_upstream_url = None
            clearart_path = None
            if fanart_images['clearart']:
                ext = self._sanitize_extension(fanart_images['clearart'])
                clearart_cache_file = f"movie_{rating_key}_clearart.{ext}"
                clearart_upstream_url = fanart_images['clearart']
                downloaded = self.download_image(
                    fanart_images['clearart'],
                    clearart_cache_file
                )
                if downloaded:
                    clearart_path = clearart_cache_file

            # Cache backdrop from fanart.tv if available
            backdrop_cache_file = None
            backdrop_upstream_url = None
            backdrop_path = None
            if fanart_images['backdrop']:
                ext = self._sanitize_extension(fanart_images['backdrop'])
                backdrop_cache_file = f"movie_{rating_key}_backdrop.{ext}"
                backdrop_upstream_url = fanart_images['backdrop']
                downloaded = self.download_image(
                    fanart_images['backdrop'],
                    backdrop_cache_file
                )
                if downloaded:
                    backdrop_path = backdrop_cache_file

            # Fallback to Plex backdrop if fanart.tv doesn't have it
            if not backdrop_path and movie.get('user_art_url'):
                backdrop_path = f"plex:/library/metadata/{rating_key}/art"

            # Cache poster from Plex if available
            poster_cache_file = None
            poster_upstream_url = None
            poster_url = None
            if movie.get('user_thumb_url'):
                # Store only the Plex path (no token) for security
                plex_poster_path = f"/library/metadata/{rating_key}/thumb"
                poster_cache_file = f"movie_{rating_key}_poster.jpg"
                poster_upstream_url = plex_poster_path
                downloaded = self.download_image(self._plex_download_url(plex_poster_path), poster_cache_file,
                                                 headers=self._plex_headers())
                if downloaded:
                    poster_url = poster_cache_file
                # Fallback to proxy if caching fails
                if not poster_url:
                    poster_url = f"plex:{plex_poster_path}"

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
            
            # Store cache filenames and upstream URLs for healing
            if clearart_cache_file:
                hero_entry['clearart_cache_file'] = clearart_cache_file
                hero_entry['clearart_upstream_url'] = clearart_upstream_url
            if backdrop_cache_file:
                hero_entry['backdrop_cache_file'] = backdrop_cache_file
                hero_entry['backdrop_upstream_url'] = backdrop_upstream_url
            if poster_cache_file:
                hero_entry['poster_cache_file'] = poster_cache_file
                hero_entry['poster_upstream_url'] = poster_upstream_url

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
            self._strip_tokens(content)
            tmp_json = self.cache_json.with_name(self.cache_json.name + '.tmp')
            with open(tmp_json, 'w') as f:
                json.dump({
                    'last_updated': datetime.now().isoformat(),
                    'movies': content
                }, f, indent=2)
            os.replace(tmp_json, self.cache_json)
            logger.info(f"Saved hero cache to {self.cache_json}")
        except Exception as e:
            logger.error(f"Failed to save hero cache: {e}")

    def load_cache(self) -> List[Dict]:
        """Load hero content from JSON file, stripping any Plex tokens for security."""
        try:
            if self.cache_json.exists():
                with open(self.cache_json, 'r') as f:
                    data = json.load(f)
                movies = data.get('movies', [])
                if self._strip_tokens(movies):
                    logger.info("Stripped Plex tokens from stored hero content")
                    self.save_cache(movies)
                return movies
        except Exception as e:
            logger.error(f"Failed to load hero cache: {e}")
        return []

    def get_missing_images(self, content: List[Dict]) -> List[tuple]:
        """Check for missing image files, return list of (item_index, field_type, cache_filename, upstream_url) tuples.
        
        Works with filenames stored in _url fields (no path prefix).
        """
        missing = []
        for idx, item in enumerate(content):
            # Check backdrop (new format: backdrop_cache_file + backdrop_upstream_url)
            if item.get('backdrop_cache_file'):
                cache_file = item['backdrop_cache_file']
                upstream_url = item.get('backdrop_upstream_url')
                if not (self.cache_dir / cache_file).exists():
                    missing.append((idx, 'backdrop', cache_file, upstream_url))
            # Check if backdrop_url is a filename (not a Plex proxy URL)
            elif item.get('backdrop_url') and not item['backdrop_url'].startswith(('/api/', 'plex:', 'http')):
                cache_file = item['backdrop_url']
                if not (self.cache_dir / cache_file).exists():
                    # No upstream URL in old format - signal full refresh needed
                    missing.append((idx, 'backdrop', cache_file, None))
            
            # Check poster (new format: poster_cache_file + poster_upstream_url)
            if item.get('poster_cache_file'):
                cache_file = item['poster_cache_file']
                upstream_url = item.get('poster_upstream_url')
                if not (self.cache_dir / cache_file).exists():
                    missing.append((idx, 'poster', cache_file, upstream_url))
            # Check if poster_url is a filename (not a Plex proxy URL)
            elif item.get('poster_url') and not item['poster_url'].startswith(('/api/', 'plex:', 'http')):
                cache_file = item['poster_url']
                if not (self.cache_dir / cache_file).exists():
                    missing.append((idx, 'poster', cache_file, None))
            
            # Check clearart (new format: clearart_cache_file + clearart_upstream_url)
            if item.get('clearart_cache_file'):
                cache_file = item['clearart_cache_file']
                upstream_url = item.get('clearart_upstream_url')
                if not (self.cache_dir / cache_file).exists():
                    missing.append((idx, 'clearart', cache_file, upstream_url))
            # Check if clearart_url is a filename (not a Plex proxy URL)
            elif item.get('clearart_url') and not item['clearart_url'].startswith(('/api/', 'plex:', 'http')):
                cache_file = item['clearart_url']
                if not (self.cache_dir / cache_file).exists():
                    missing.append((idx, 'clearart', cache_file, None))
        
        return missing
    
    def _heal_missing_images_background(self, content: List[Dict], missing: List[tuple]):
        """Re-download only the missing image files from upstream URLs (runs in background thread)."""
        success = False
        try:
            logger.info(f"Background healing: re-downloading {len(missing)} missing images")
            
            # Check if any missing images lack upstream URLs (old format)
            needs_full_refresh = any(upstream_url is None for _, _, _, upstream_url in missing)
            
            if needs_full_refresh:
                logger.warning("Some cached entries lack upstream URLs (old format), triggering full refresh")
                self.refresh_hero_content()
                success = True
                return
            
            # Heal by downloading from upstream URLs
            for idx, field_type, cache_filename, upstream_url in missing:
                if upstream_url:
                    # Add current Plex token if this is a Plex path
                    download_url, dl_headers = upstream_url, None
                    if upstream_url.startswith('/library/'):
                        download_url, dl_headers = self._plex_download_url(upstream_url), self._plex_headers()

                    logger.info(f"Healing {field_type}: {cache_filename} from {upstream_url[:80]}...")
                    result = self.download_image(download_url, cache_filename, headers=dl_headers)
                    if not result:
                        raise Exception(f"Failed to download {cache_filename}")
            
            logger.info("Background healing complete")
            success = True
        except Exception as e:
            logger.error(f"Background healing failed: {e}")
        finally:
            # Update healing state based on success/failure
            if success:
                self._heal_failed = False
                self._heal_backoff = 60  # Reset backoff
            else:
                self._heal_failed = True
                # Double the backoff, capped at max
                self._heal_backoff = min(self._heal_backoff * 2, self._heal_backoff_max)
    
    def heal_missing_images(self) -> bool:
        """Re-download missing images, honoring single-flight, cooldown and backoff.

        Called only from the background scheduler. Returns True if a heal ran.
        """
        if not self._healing_lock.acquire(blocking=False):
            return False
        try:
            now = self._clock()
            cooldown = self._heal_backoff if self._heal_failed else self._heal_cooldown
            if self._last_heal_time and now - self._last_heal_time < cooldown:
                return False
            content = self.load_cache()
            missing = self.get_missing_images(content) if content else []
            if not missing:
                return False
            self._last_heal_time = now
            self._heal_missing_images_background(content, missing)
            return True
        finally:
            self._healing_lock.release()

    def _seconds_until_daily_refresh(self, hour: int = 5) -> float:
        now = datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def startup_sync(self) -> Optional[str]:
        """One-time startup step, run by the scheduler before its first wait.

        No cached content -> one refresh. Cached content that references image
        files missing from the hero cache dir (e.g. a stale data/hero_content.json
        surviving a container recreate) -> heal now instead of after the first
        heal interval; entries without upstream URLs make that heal a single
        full refresh. Returns 'refresh', 'heal' or None.
        """
        content = self.load_cache()
        if not content:
            self._safe_refresh()
            return 'refresh'
        missing = self.get_missing_images(content)
        if not missing:
            return None
        with self._healing_lock:
            self._last_heal_time = self._clock()
            self._heal_missing_images_background(content, missing)
        return 'heal'

    def _scheduler_loop(self, heal_interval: float):
        try:
            self.startup_sync()
        except Exception as e:
            logger.error(f"Hero startup refresh/heal failed: {e}")
        next_refresh = time.monotonic() + self._seconds_until_daily_refresh()
        while not self._scheduler_stop.wait(heal_interval):
            if time.monotonic() >= next_refresh:
                self._safe_refresh()
                next_refresh = time.monotonic() + self._seconds_until_daily_refresh()
            else:
                try:
                    self.heal_missing_images()
                except Exception as e:
                    logger.error(f"Hero heal failed: {e}")

    def _safe_refresh(self):
        try:
            self.refresh_hero_content()
        except Exception as e:
            logger.error(f"Hero refresh failed: {e}")

    def start_scheduler(self, heal_interval: float = 300) -> threading.Thread:
        """Start the background refresh/heal scheduler (idempotent)."""
        # Started from main.start_services() (gunicorn post_worker_init and
        # the dev server) when background threads are on.
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return self._scheduler_thread
        self._scheduler_stop.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop, args=(heal_interval,),
            name='hero-cache-scheduler', daemon=True)
        self._scheduler_thread.start()
        return self._scheduler_thread

    def stop_scheduler(self):
        self._scheduler_stop.set()

# Singleton instance
hero_cache = HeroCache()
