#!/usr/bin/env python3
"""
Screensaver Service - Fetches movies from Plex via plex_service
with backdrops from TMDb and clearlogos from Plex for the Movie Orb Screensaver.
"""

import os
import time
import logging
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

logger = logging.getLogger(__name__)


class ScreensaverService:
    """Service for fetching movies with backdrops and clearlogo for the orb screensaver."""

    def __init__(self, tmdb_api_key: str,
                 tmdb_base_url: str = "https://api.themoviedb.org/3",
                 cache_ttl_seconds: int = 1800,
                 library_name: str = "Recently Released",
                 library_section_id: int = 0):
        self.tmdb_api_key = tmdb_api_key
        self.TMDB_BASE_URL = tmdb_base_url
        self.TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
        self.CACHE_TTL_SECONDS = cache_ttl_seconds
        self.library_name = library_name
        self.library_section_id = library_section_id
        self._cache: Optional[List[Dict]] = None
        self._cache_timestamp: float = 0

    def get_movies(self, limit: int = 12, plex_svc=None) -> List[Dict]:
        """
        Get movies for the screensaver from configured Plex library.
        Sorted by most recently added first, clearlogo movies prioritized.

        Args:
            limit: Max items to return.
            plex_svc: Optional PlexService instance (for per-user scoping).
                      When provided, cache is bypassed and data is fetched
                      fresh from the given Plex server.

        Returns list of movie objects with:
            id, title, year, backdropUrl, posterUrl, clearlogoUrl
        """
        # If a specific plex_svc is provided, fetch fresh (no singleton cache)
        if plex_svc is not None:
            movies = self._fetch_from_recently_added(plex_svc=plex_svc)
            return movies[:limit] if movies else []

        if self._is_cache_valid():
            return self._cache[:limit]

        movies = self._fetch_from_recently_added()
        if movies:
            self._cache = movies
            self._cache_timestamp = time.time()
            return movies[:limit]

        # Fallback to stale cache
        if self._cache:
            logger.warning("Screensaver: fetch failed, using stale cache")
            return self._cache[:limit]

        return []

    def _is_cache_valid(self) -> bool:
        if not self._cache:
            return False
        return (time.time() - self._cache_timestamp) < self.CACHE_TTL_SECONDS

    def _fetch_from_recently_added(self, plex_svc=None) -> List[Dict]:
        """Fetch movies from configured Plex library via plex_service and enrich with images.

        Args:
            plex_svc: Optional PlexService instance.  When None, falls back
                      to the global ``plex_service`` singleton.
        """
        try:
            if plex_svc is None:
                from app.plex_service import plex_service as plex_svc

            # Resolve section ID
            section_id = self.library_section_id if self.library_section_id and self.library_section_id > 0 else None
            if not section_id:
                try:
                    health = plex_svc.health_check()
                    section_id = health.get('default_movie_section_id')
                except Exception:
                    pass

            # Fetch recently added movies from Plex
            raw_movies = plex_svc.get_recently_added(section_id=section_id, limit=100)

            if not raw_movies:
                logger.warning("Screensaver: No movies from Plex")
                return []

            logger.info(f"Screensaver: Got {len(raw_movies)} items from Plex")

            # Normalize movie data - preserve order (most recently added first)
            movies = []
            for m in raw_movies:
                if m.get('type', 'movie') != 'movie':
                    continue

                tmdb_id = m.get('tmdb_id')
                if not tmdb_id:
                    logger.debug(f"Screensaver: Skipping '{m.get('title')}' - no TMDb ID")
                    continue

                # Use Plex clearlogo if available (proxy URL)
                clearlogo_url = None
                if m.get('clearLogoPath'):
                    clearlogo_url = f"/api/img/plex?url={m['clearLogoPath']}"

                normalized = {
                    'id': tmdb_id,
                    'rating_key': m.get('rating_key'),
                    'title': m.get('title', 'Unknown'),
                    'year': str(m.get('year', '')),
                    'posterUrl': f"/api/img/plex?url={m['thumbPath']}" if m.get('thumbPath') else None,
                    'backdropUrl': f"/api/img/plex?url={m['artPath']}" if m.get('artPath') else None,
                    'clearlogoUrl': clearlogo_url,
                    'added_at': m.get('added_at', 0),
                }
                movies.append(normalized)

            if not movies:
                logger.warning("Screensaver: No movies with TMDb IDs")
                return []

            logger.info(f"Screensaver: {len(movies)} movies with TMDb IDs")

            # Fetch higher-quality TMDb backdrops in parallel
            self._fetch_tmdb_backdrops(movies)

            # Require backdrop; clearlogo is optional (Orb uses text fallback)
            movies_with_backdrop = [
                m for m in movies
                if m.get('backdropUrl')
            ]

            # Sort: movies WITH clearlogo first, then by most recently added
            movies_with_backdrop.sort(
                key=lambda m: (0 if m.get('clearlogoUrl') else 1, -m.get('added_at', 0))
            )

            logger.info(
                f"Screensaver: {len(movies_with_backdrop)} movies with backdrop "
                f"({sum(1 for m in movies_with_backdrop if m.get('clearlogoUrl'))} with clearlogo)"
            )

            return movies_with_backdrop

        except Exception as e:
            logger.error(f"Screensaver: Error fetching from recently added: {e}")
            return []

    def _fetch_tmdb_backdrops(self, movies: List[Dict]):
        """Fetch higher-quality backdrop images from TMDb in parallel."""

        def fetch_backdrop(movie):
            tmdb_id = movie.get('id')
            if not tmdb_id or not self.tmdb_api_key:
                return

            try:
                url = f"{self.TMDB_BASE_URL}/movie/{tmdb_id}"
                response = requests.get(
                    url,
                    params={"api_key": self.tmdb_api_key},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    backdrop_path = data.get('backdrop_path')
                    if backdrop_path:
                        movie['backdropUrl'] = f"{self.TMDB_IMAGE_BASE}/w780{backdrop_path}"
                    movie['overview'] = data.get('overview', '')
            except Exception as e:
                logger.debug(f"Screensaver: TMDb error for {tmdb_id}: {e}")

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(fetch_backdrop, m) for m in movies]
            for future in as_completed(futures, timeout=20):
                try:
                    future.result()
                except Exception:
                    pass


# Singleton
_service: Optional[ScreensaverService] = None


def get_screensaver_service(settings_manager=None) -> ScreensaverService:
    """Return (or create) the singleton ScreensaverService.

    When *settings_manager* is supplied its values take precedence.
    Falls back to environment variables for backward compatibility.
    """
    global _service
    if _service is None:
        if settings_manager is not None:
            tmdb_key = settings_manager.get('apis.tmdb.api_key', '')
            tmdb_base = settings_manager.get('apis.tmdb.base_url', 'https://api.themoviedb.org/3')
            cache_ttl = settings_manager.get('ui.screensaver.cache_ttl_seconds', 1800)
            library_name = settings_manager.get('ui.screensaver.library_name', 'Recently Released')
            library_section_id = settings_manager.get('ui.screensaver.library_section_id', 0)
        else:
            tmdb_key = os.environ.get('TMDB_API_KEY', '')
            tmdb_base = 'https://api.themoviedb.org/3'
            cache_ttl = 1800
            library_name = 'Recently Released'
            library_section_id = 0
        _service = ScreensaverService(
            tmdb_api_key=tmdb_key,
            tmdb_base_url=tmdb_base,
            cache_ttl_seconds=cache_ttl,
            library_name=library_name,
            library_section_id=library_section_id,
        )
    return _service
