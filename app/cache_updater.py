"""
Cache Updater Module - Background tasks to update library cache
"""

import threading
import time
import logging
from typing import List, Dict, Optional
from app.library_cache import library_cache
from datetime import datetime

logger = logging.getLogger(__name__)


def update_continue_watching_cache():
    """Update the continue watching cache from Plex API"""
    try:
        logger.info("Updating continue watching cache...")

        # Get data from Plex API for perfect accuracy
        from app.plex_api_on_deck import get_plex_api_on_deck
        items = get_plex_api_on_deck(limit=50)  # Get more items for cache

        # Update cache
        library_cache.update_continue_watching(items)
        logger.info(f"Updated continue watching cache with {len(items)} items")

    except Exception as e:
        logger.error(f"Failed to update continue watching cache: {e}")


def update_library_browse_cache(library_name: str, section_id: int):
    """Update the library browse cache for a specific library via Plex HTTP API"""
    try:
        logger.info(f"Updating browse cache for library: {library_name}")
        from app.plex_service import plex_service

        movies = []
        offset = 0
        page_size = 500

        while True:
            result = plex_service.get_all_movies(
                section_id=section_id,
                sort='addedAt:desc',
                limit=page_size,
                offset=offset,
                timeout=30,
            )
            batch = result.get('movies', [])
            for item in batch:
                rk = item.get('rating_key')
                genres = item.get('genres', [])
                movie = {
                    'rating_key': rk,
                    'title': item.get('title'),
                    'year': item.get('year'),
                    'rating': item.get('rating'),
                    'duration': item.get('duration'),
                    'added_at': item.get('added_at', item.get('addedAt', 0)),
                    'content_rating': item.get('content_rating', item.get('contentRating', '')),
                    'summary': item.get('summary', ''),
                    'genre': genres[0] if genres else None,
                    'all_genres': genres,
                    'poster_url': f"/api/img/plex?url=/library/metadata/{rk}/thumb&r=2",
                    'backdrop_url': f"/api/img/plex?url=/library/metadata/{rk}/art",
                    'clearlogo_url': f"/api/img/plex?url=/library/metadata/{rk}/clearLogo",
                }
                movies.append(movie)

            offset += page_size
            if not result.get('has_more', False) or len(movies) >= 5000:
                break

        # Update cache
        library_cache.update_library_browse(library_name, movies)
        logger.info(f"Updated {library_name} browse cache with {len(movies)} movies")

    except Exception as e:
        logger.error(f"Failed to update library browse cache for {library_name}: {e}")


def update_recent_items_cache():
    """Update the recent items cache via Plex HTTP API"""
    try:
        logger.info("Updating recent items cache...")
        from app.plex_service import plex_service

        sections = plex_service.get_sections_lean()

        all_items = []

        # Get recent movies from all movie sections
        movie_sections = [s for s in sections if s['type'] == 'movie']
        for section in movie_sections:
            items = plex_service.get_recently_added(section_id=section['id'], limit=50)
            for item in items:
                rk = item.get('rating_key')
                added_at = item.get('added_at', item.get('addedAt', 0))
                movie = {
                    'rating_key': rk,
                    'title': item.get('title'),
                    'year': item.get('year'),
                    'added_at': added_at,
                    'duration': item.get('duration'),
                    'rating': item.get('rating'),
                    'library_name': section['title'],
                    'metadata_type': 1,
                    'tmdb_id': item.get('tmdb_id'),
                    'poster_url': f"/api/img/plex?url=/library/metadata/{rk}/thumb&r=2",
                    'backdrop_url': f"/api/img/plex?url=/library/metadata/{rk}/art",
                    'clearlogo_url': f"/api/img/plex?url=/library/metadata/{rk}/clearLogo",
                }
                if added_at:
                    try:
                        movie['added_date'] = datetime.fromtimestamp(added_at).strftime('%Y-%m-%d')
                    except (ValueError, OSError):
                        movie['added_date'] = None
                all_items.append(movie)

        # Get recent TV shows from all TV sections
        tv_sections = [s for s in sections if s['type'] == 'show']
        for section in tv_sections:
            items = plex_service.get_recently_added(section_id=section['id'], limit=50)
            for item in items:
                rk = item.get('rating_key')
                added_at = item.get('added_at', item.get('addedAt', 0))
                show = {
                    'rating_key': rk,
                    'title': item.get('title', item.get('show_title', '')),
                    'year': item.get('year', item.get('show_year')),
                    'added_at': added_at,
                    'duration': item.get('duration'),
                    'rating': item.get('rating'),
                    'library_name': section['title'],
                    'metadata_type': 2,
                    'poster_url': f"/api/img/plex?url=/library/metadata/{rk}/thumb&r=2",
                    'backdrop_url': f"/api/img/plex?url=/library/metadata/{rk}/art",
                    'clearlogo_url': f"/api/img/plex?url=/library/metadata/{rk}/clearLogo",
                }
                if added_at:
                    try:
                        show['added_date'] = datetime.fromtimestamp(added_at).strftime('%Y-%m-%d')
                    except (ValueError, OSError):
                        show['added_date'] = None
                all_items.append(show)

        # Sort all items by added_at
        all_items.sort(key=lambda x: x.get('added_at', 0), reverse=True)

        # Update cache
        library_cache.update_recent_items(all_items[:100])  # Keep top 100 most recent
        logger.info(f"Updated recent items cache with {len(all_items)} items")

    except Exception as e:
        logger.error(f"Failed to update recent items cache: {e}")


def update_all_library_caches():
    """Update browse cache for all movie libraries via Plex HTTP API"""
    try:
        from app.plex_service import plex_service

        sections = plex_service.get_sections_lean()
        movie_sections = [s for s in sections if s['type'] == 'movie']

        for section in movie_sections:
            update_library_browse_cache(section['title'], section['id'])
            time.sleep(0.5)  # Small delay between libraries

    except Exception as e:
        logger.error(f"Failed to update library caches: {e}")


def update_library_stats_cache():
    """Update the library stats cache via Plex HTTP API"""
    try:
        logger.info("Updating library stats cache...")
        from app.plex_service import plex_service

        stats = plex_service.get_library_stats()

        # Update cache
        library_cache.update_library_stats(stats)
        logger.info("Updated library stats cache")

    except Exception as e:
        logger.error(f"Failed to update library stats cache: {e}")


def update_recent_tv_episodes_cache():
    """Update the recent TV episodes cache via Plex HTTP API
    (shows sorted by most recent episode added)"""
    try:
        logger.info("Updating recent TV episodes cache...")
        from app.plex_service import plex_service

        sections = plex_service.get_sections_lean()
        tv_sections = [s for s in sections if s['type'] == 'show']

        # Collect recently added episodes across all TV sections
        all_episodes = []
        for section in tv_sections:
            items = plex_service.get_recently_added(section_id=section['id'], limit=50)
            for item in items:
                item['library_name'] = section['title']
                all_episodes.append(item)

        # Sort by addedAt descending
        all_episodes.sort(key=lambda x: x.get('added_at', x.get('addedAt', 0)), reverse=True)

        # Deduplicate by show — keep the most recent episode per show
        shows = []
        seen_show_ids = set()

        for item in all_episodes:
            # Episodes have show_rating_key; shows themselves have rating_key
            show_rk = item.get('show_rating_key') or item.get('rating_key')
            if show_rk in seen_show_ids:
                continue
            seen_show_ids.add(show_rk)

            added_at = item.get('added_at', item.get('addedAt', 0))
            show = {
                'show_rating_key': show_rk,
                'show_title': item.get('show_title') or item.get('title', ''),
                'show_year': item.get('show_year') or item.get('year'),
                'latest_added_at': added_at,
                'poster_url': f"/api/img/plex?url=/library/metadata/{show_rk}/thumb&r=2",
            }
            if added_at:
                try:
                    show['added_date'] = datetime.fromtimestamp(added_at).strftime('%Y-%m-%d')
                except (ValueError, OSError):
                    show['added_date'] = None

            shows.append(show)
            if len(shows) >= 30:
                break

        # Update cache
        library_cache.update_recent_tv_episodes(shows)
        logger.info(f"Updated recent TV episodes cache with {len(shows)} shows")

    except Exception as e:
        logger.error(f"Failed to update recent TV episodes cache: {e}")


def refresh_ownership_index():
    """Keep the Plex ownership index warm.

    Building it walks every movie and show section and takes ~9s, but it only
    lives for 5 minutes (PlexService._OWNERSHIP_TTL). Anything that resolves
    "is this in my library" — the home rails, Explore, the similar-movies
    panel — pays that full rebuild whenever it is the first caller after an
    expiry. Refreshing on a shorter interval than the TTL means the request
    path effectively always hits a warm index.
    """
    try:
        from app.plex_service import plex_service
        start = time.time()
        index = plex_service.build_ownership_index(force=True)
        logger.info(
            "Refreshed ownership index (%d movies, %d shows) in %.1fs",
            len(index.get('movies_by_title', {})), len(index.get('tv_by_title', {})),
            time.time() - start,
        )
    except Exception as e:
        logger.error(f"Failed to refresh ownership index: {e}")


class CacheUpdateScheduler:
    """Manages background cache updates"""

    def __init__(self):
        self.running = False
        self.threads = []

    def start(self):
        """Start the cache update scheduler"""
        if self.running:
            return

        self.running = True

        # Start update threads
        self.threads = [
            threading.Thread(target=self._update_loop, args=('continue_watching', 60, update_continue_watching_cache), daemon=True),
            threading.Thread(target=self._update_loop, args=('recent_items', 120, update_recent_items_cache), daemon=True),
            threading.Thread(target=self._update_loop, args=('recent_tv_episodes', 120, update_recent_tv_episodes_cache), daemon=True),
            threading.Thread(target=self._update_loop, args=('library_stats', 300, update_library_stats_cache), daemon=True),
            threading.Thread(target=self._update_loop, args=('libraries', 300, update_all_library_caches), daemon=True),
            # 240s, deliberately under the index's own 300s TTL — the point is
            # that it never expires while nobody is looking.
            threading.Thread(target=self._update_loop, args=('ownership_index', 240, refresh_ownership_index), daemon=True),
        ]

        for thread in self.threads:
            thread.start()

        logger.info("Cache update scheduler started")

    def _update_loop(self, name: str, interval: int, update_func):
        """Run update function in a loop"""
        logger.info(f"Starting {name} update loop with {interval}s interval")

        # Initial update
        update_func()

        while self.running:
            time.sleep(interval)
            if self.running:
                try:
                    update_func()
                except Exception as e:
                    logger.error(f"Error in {name} update loop: {e}")

    def stop(self):
        """Stop the cache update scheduler"""
        self.running = False
        for thread in self.threads:
            thread.join(timeout=5)
        logger.info("Cache update scheduler stopped")


# Create global scheduler instance
cache_scheduler = CacheUpdateScheduler()


def initialize_caches():
    """Initialize all caches on startup"""
    logger.info("Initializing library caches...")

    try:
        # Update all caches
        update_continue_watching_cache()
        update_recent_items_cache()
        update_recent_tv_episodes_cache()
        update_library_stats_cache()
        update_all_library_caches()

        # Start background updates
        cache_scheduler.start()

        logger.info("Library caches initialized successfully")

    except Exception as e:
        logger.error(f"Failed to initialize library caches: {e}")
