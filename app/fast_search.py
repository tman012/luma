#!/usr/bin/env python3
"""
Fast In-Memory Media Search Index
Preloads all Plex library metadata (movies + TV shows) on startup for millisecond search.
Auto-refreshes every 15 minutes in background.
"""

import logging
import threading
import time
from typing import List, Dict, Optional, Any

import requests

logger = logging.getLogger(__name__)

# Plex section type → (media_type, plex_type). plex_type: 1 = movies, 2 = shows
_SECTION_TYPES = {'movie': ('movie', 1), 'show': ('show', 2)}

REFRESH_INTERVAL = 900  # 15 minutes


class FastSearchIndex:
    """In-memory media search index backed by Plex library data (movies + TV shows)."""

    def __init__(self):
        self._index: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._last_refresh: float = 0.0
        self._refreshing = False
        self._initialized = False
        self._sections: List[int] = []

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

                items_out.append({
                    'rating_key': str(item.get('ratingKey', '')),
                    'title': item.get('title', ''),
                    'title_lower': item.get('title', '').lower(),
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

        with self._lock:
            self._sections = [sid for sid, *_ in sections]
            self._index = all_items
            self._last_refresh = time.time()
            self._initialized = True

        movie_count = sum(1 for i in all_items if i.get('media_type') == 'movie')
        show_count = sum(1 for i in all_items if i.get('media_type') == 'show')
        logger.info(f"fast_search: index built with {len(all_items)} total items ({movie_count} movies, {show_count} shows)")

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
               limit: int = 100) -> List[Dict[str, Any]]:
        """
        Search the in-memory index.  All filters are ANDed together.
        Title search is case-insensitive substring match.
        media_type: 'movie' or 'show' (optional filter).
        Returns list of result dicts (internal '_lower' keys stripped).
        """
        q_lower = q.lower().strip() if q else None
        genre_lower = genre.lower().strip() if genre else None
        cr_lower = content_rating.lower().strip() if content_rating else None
        mt_lower = media_type.lower().strip() if media_type else None

        with self._lock:
            snapshot = self._index  # reference copy under lock

        results = []
        for item in snapshot:
            # Title filter
            if q_lower and q_lower not in item['title_lower']:
                continue
            # Genre filter
            if genre_lower and genre_lower not in item['genres_lower']:
                continue
            # Content rating filter
            if cr_lower and cr_lower != item['content_rating'].lower():
                continue
            # Year filter
            if year is not None and item['year'] != year:
                continue
            # Section filter
            if section is not None and item['section_id'] != section:
                continue
            # Media type filter
            if mt_lower and item.get('media_type', 'movie') != mt_lower:
                continue

            results.append(item)
            if len(results) >= limit:
                break

        # Strip internal search helpers before returning
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
            })

        return clean

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
