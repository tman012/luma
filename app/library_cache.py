"""
Library Cache Module - Caches frequently accessed library data for fast loading
Similar to the ExploreCache but for library browsing, continue watching, and recent items.
"""

import sqlite3
import json
import os
import threading
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import time

logger = logging.getLogger(__name__)


class LibraryCache:
    """Manages cached library data for lightning-fast loading"""

    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is None:
            cache_dir = os.environ.get('LUMA_CACHE_DIR', '/app/cache')
        self.cache_dir = Path(cache_dir)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.warning(f"library_cache: cache directory {self.cache_dir} not writable: {e}")
        self.db_path = self.cache_dir / "library_cache.db"
        self.update_interval = 60  # 1 minute for library data
        self.last_update = {}
        self.updating = {}
        self.lock = threading.Lock()

        # Initialize database
        self._init_database()

    def _connect(self):
        """Get a DB connection with row_factory set."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


    def _init_database(self):
        """Initialize the cache database with optimized schema"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Create continue_watching cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS continue_watching_cache (
                    rating_key INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    metadata_type INTEGER,
                    show_title TEXT,
                    episode_title TEXT,
                    season_number INTEGER,
                    episode_number INTEGER,
                    year INTEGER,
                    duration INTEGER,
                    view_offset INTEGER,
                    progress_percent REAL,
                    last_viewed_at INTEGER,
                    show_rating_key INTEGER,
                    poster_url TEXT,
                    backdrop_url TEXT,
                    clearlogo_url TEXT,
                    data_json TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create browse_library cache table (for each library)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS browse_library_cache (
                    rating_key INTEGER,
                    library_name TEXT,
                    title TEXT NOT NULL,
                    year INTEGER,
                    rating REAL,
                    duration INTEGER,
                    added_at INTEGER,
                    content_rating TEXT,
                    summary TEXT,
                    poster_url TEXT,
                    backdrop_url TEXT,
                    clearlogo_url TEXT,
                    genre TEXT,
                    data_json TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (rating_key, library_name)
                )
            """)

            # Create recent_items cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recent_items_cache (
                    rating_key INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    metadata_type INTEGER,  -- 1=movie, 2=show
                    year INTEGER,
                    added_at INTEGER,
                    library_name TEXT,
                    poster_url TEXT,
                    backdrop_url TEXT,
                    clearlogo_url TEXT,
                    data_json TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for fast queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_continue_watching_last_viewed
                ON continue_watching_cache(last_viewed_at DESC)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_browse_library_combined
                ON browse_library_cache(library_name, added_at DESC, title ASC, year DESC, rating DESC)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_browse_library_genre
                ON browse_library_cache(library_name, genre)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_recent_items_added
                ON recent_items_cache(added_at DESC)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_recent_items_type
                ON recent_items_cache(metadata_type, added_at DESC)
            """)

            # Create recent_tv_episodes cache table (shows sorted by most recent episode added)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recent_tv_episodes_cache (
                    show_rating_key INTEGER PRIMARY KEY,
                    show_title TEXT NOT NULL,
                    show_year INTEGER,
                    show_guid TEXT,
                    latest_added_at INTEGER,
                    added_date TEXT,
                    poster_url TEXT,
                    data_json TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_recent_tv_episodes_added
                ON recent_tv_episodes_cache(latest_added_at DESC)
            """)

            # Create library_stats cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS library_stats_cache (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    stats_json TEXT NOT NULL,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create ownership cache table for fast lookups
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ownership_cache (
                    cache_key TEXT PRIMARY KEY,
                    is_owned INTEGER,
                    tvdb_id TEXT,
                    imdb_id TEXT,
                    rating_key INTEGER,
                    metadata_type INTEGER,  -- 1=movie, 2=show, 4=episode
                    data_json TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create index for fast ownership lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_ownership_tvdb
                ON ownership_cache(tvdb_id)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_ownership_imdb
                ON ownership_cache(imdb_id)
            """)

            # Create TV episode ownership cache
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tv_episode_ownership (
                    show_key TEXT,
                    season INTEGER,
                    episode INTEGER,
                    is_owned INTEGER,
                    rating_key INTEGER,
                    runtime INTEGER,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (show_key, season, episode)
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tv_episode_show
                ON tv_episode_ownership(show_key)
            """)

            # Create metadata table for tracking cache updates
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache_metadata (
                    cache_key TEXT PRIMARY KEY,
                    last_update TIMESTAMP,
                    item_count INTEGER,
                    update_duration_ms INTEGER
                )
            """)

            conn.commit()
            conn.close()
            logger.info("Library cache database initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize library cache database: {e}")
            try:
                conn.close()
            except Exception:
                pass
            raise

    def needs_update(self, cache_key: str) -> bool:
        """Check if a specific cache needs updating"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute("""
                SELECT last_update
                FROM cache_metadata
                WHERE cache_key = ?
            """, (cache_key,))

            row = cursor.fetchone()
            conn.close()

            if not row:
                return True

            last_update = datetime.fromisoformat(row[0])
            age = (datetime.now() - last_update).total_seconds()

            return age > self.update_interval

        except Exception as e:
            logger.error(f"Error checking cache update status: {e}")
            try:
                conn.close()
            except Exception:
                pass
            return True

    def get_continue_watching(self, limit: int = 25) -> List[Dict]:
        """Get cached continue watching items"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT *
                FROM continue_watching_cache
                ORDER BY last_viewed_at DESC
                LIMIT ?
            """, (limit,))

            items = []
            for row in cursor.fetchall():
                item = dict(row)
                # Parse JSON data if present
                if item.get('data_json'):
                    try:
                        additional_data = json.loads(item['data_json'])
                        item.update(additional_data)
                    except:
                        pass
                    del item['data_json']
                del item['cached_at']
                items.append(item)

            conn.close()
            return items

        except Exception as e:
            logger.error(f"Error getting cached continue watching: {e}")
            try:
                conn.close()
            except Exception:
                pass
            return []

    def get_browse_library(self, library_name: str, offset: int = 0, limit: int = 50,
                          sort_by: str = 'added_at', direction: str = 'desc',
                          genre_filter: str = '') -> Dict:
        """Get cached library browse data"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get total count
            if genre_filter:
                cursor.execute("""
                    SELECT COUNT(*) as total
                    FROM browse_library_cache
                    WHERE library_name = ? AND genre = ?
                """, (library_name, genre_filter))
            else:
                cursor.execute("""
                    SELECT COUNT(*) as total
                    FROM browse_library_cache
                    WHERE library_name = ?
                """, (library_name,))

            total = cursor.fetchone()['total']

            # Build order clause
            order_map = {
                'added_at': 'added_at',
                'title': 'title',
                'year': 'year',
                'rating': 'rating'
            }
            order_column = order_map.get(sort_by, 'added_at')
            order_dir = 'DESC' if direction == 'desc' else 'ASC'

            # Get items
            if genre_filter:
                query = f"""
                    SELECT *
                    FROM browse_library_cache
                    WHERE library_name = ? AND genre = ?
                    ORDER BY {order_column} {order_dir}
                    LIMIT ? OFFSET ?
                """
                cursor.execute(query, (library_name, genre_filter, limit, offset))
            else:
                query = f"""
                    SELECT *
                    FROM browse_library_cache
                    WHERE library_name = ?
                    ORDER BY {order_column} {order_dir}
                    LIMIT ? OFFSET ?
                """
                cursor.execute(query, (library_name, limit, offset))

            items = []
            for row in cursor.fetchall():
                item = dict(row)
                # Parse JSON data if present
                if item.get('data_json'):
                    try:
                        additional_data = json.loads(item['data_json'])
                        item.update(additional_data)
                    except:
                        pass
                    del item['data_json']
                del item['cached_at']
                items.append(item)

            # Get genres for filtering (only on first page)
            genres = []
            if offset == 0:
                cursor.execute("""
                    SELECT DISTINCT genre
                    FROM browse_library_cache
                    WHERE library_name = ? AND genre IS NOT NULL
                    ORDER BY genre
                """, (library_name,))
                genres = [row['genre'] for row in cursor.fetchall()]

            conn.close()

            return {
                'movies': items,
                'total': total,
                'genres': genres,
                'from_cache': True
            }

        except Exception as e:
            logger.error(f"Error getting cached library browse: {e}")
            return {'movies': [], 'total': 0, 'genres': [], 'error': str(e)}

    def get_recent_items(self, limit: int = 25, metadata_type: Optional[int] = None) -> List[Dict]:
        """Get cached recent items (movies or TV shows)"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if metadata_type is not None:
                cursor.execute("""
                    SELECT *
                    FROM recent_items_cache
                    WHERE metadata_type = ?
                    ORDER BY added_at DESC
                    LIMIT ?
                """, (metadata_type, limit))
            else:
                cursor.execute("""
                    SELECT *
                    FROM recent_items_cache
                    ORDER BY added_at DESC
                    LIMIT ?
                """, (limit,))

            items = []
            for row in cursor.fetchall():
                item = dict(row)
                # Parse JSON data if present
                if item.get('data_json'):
                    try:
                        additional_data = json.loads(item['data_json'])
                        item.update(additional_data)
                    except:
                        pass
                    del item['data_json']
                del item['cached_at']
                items.append(item)

            conn.close()
            return items

        except Exception as e:
            logger.error(f"Error getting cached recent items: {e}")
            return []

    def update_continue_watching(self, items: List[Dict]):
        """Update continue watching cache"""
        try:
            start_time = time.time()
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Clear existing cache
            cursor.execute("DELETE FROM continue_watching_cache")

            # Insert new items
            for item in items:
                # Extract core fields
                data_copy = item.copy()
                core_fields = [
                    'rating_key', 'title', 'metadata_type', 'show_title', 'episode_title',
                    'season_number', 'episode_number', 'year', 'duration', 'view_offset',
                    'progress_percent', 'last_viewed_at', 'show_rating_key',
                    'poster_url', 'backdrop_url', 'clearlogo_url'
                ]

                # Store extra data as JSON
                extra_data = {k: v for k, v in data_copy.items() if k not in core_fields}
                data_json = json.dumps(extra_data) if extra_data else None

                cursor.execute("""
                    INSERT INTO continue_watching_cache (
                        rating_key, title, metadata_type, show_title, episode_title,
                        season_number, episode_number, year, duration, view_offset,
                        progress_percent, last_viewed_at, show_rating_key,
                        poster_url, backdrop_url, clearlogo_url, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.get('rating_key'), item.get('title'), item.get('metadata_type'),
                    item.get('show_title'), item.get('episode_title'),
                    item.get('season_number'), item.get('episode_number'),
                    item.get('year'), item.get('duration'), item.get('view_offset'),
                    item.get('progress_percent'), item.get('last_viewed_at'),
                    item.get('show_rating_key'), item.get('poster_url'),
                    item.get('backdrop_url'), item.get('clearlogo_url'), data_json
                ))

            # Update metadata
            duration_ms = int((time.time() - start_time) * 1000)
            cursor.execute("""
                INSERT OR REPLACE INTO cache_metadata (cache_key, last_update, item_count, update_duration_ms)
                VALUES (?, ?, ?, ?)
            """, ('continue_watching', datetime.now().isoformat(), len(items), duration_ms))

            conn.commit()
            conn.close()

            logger.info(f"Updated continue watching cache with {len(items)} items in {duration_ms}ms")

        except Exception as e:
            logger.error(f"Error updating continue watching cache: {e}")
            try:
                conn.close()
            except Exception:
                pass

    def update_library_browse(self, library_name: str, items: List[Dict]):
        """Update library browse cache for a specific library"""
        try:
            start_time = time.time()
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Clear existing cache for this library
            cursor.execute("DELETE FROM browse_library_cache WHERE library_name = ?", (library_name,))

            # Insert new items
            for item in items:
                # Extract core fields
                data_copy = item.copy()
                core_fields = [
                    'rating_key', 'title', 'year', 'rating', 'duration', 'added_at',
                    'content_rating', 'summary', 'poster_url', 'backdrop_url',
                    'clearlogo_url', 'genre'
                ]

                # Store extra data as JSON
                extra_data = {k: v for k, v in data_copy.items() if k not in core_fields}
                data_json = json.dumps(extra_data) if extra_data else None

                cursor.execute("""
                    INSERT INTO browse_library_cache (
                        rating_key, library_name, title, year, rating, duration,
                        added_at, content_rating, summary, poster_url, backdrop_url,
                        clearlogo_url, genre, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.get('rating_key'), library_name, item.get('title'),
                    item.get('year'), item.get('rating'), item.get('duration'),
                    item.get('added_at'), item.get('content_rating'),
                    item.get('summary'), item.get('poster_url'),
                    item.get('backdrop_url'), item.get('clearlogo_url'),
                    item.get('genre'), data_json
                ))

            # Update metadata
            duration_ms = int((time.time() - start_time) * 1000)
            cache_key = f"browse_{library_name}"
            cursor.execute("""
                INSERT OR REPLACE INTO cache_metadata (cache_key, last_update, item_count, update_duration_ms)
                VALUES (?, ?, ?, ?)
            """, (cache_key, datetime.now().isoformat(), len(items), duration_ms))

            conn.commit()
            conn.close()

            logger.info(f"Updated {library_name} browse cache with {len(items)} items in {duration_ms}ms")

        except Exception as e:
            logger.error(f"Error updating library browse cache: {e}")
            try:
                conn.close()
            except Exception:
                pass

    def update_recent_items(self, items: List[Dict]):
        """Update recent items cache"""
        try:
            start_time = time.time()
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Clear existing cache
            cursor.execute("DELETE FROM recent_items_cache")

            # Insert new items
            for item in items:
                # Extract core fields
                data_copy = item.copy()
                core_fields = [
                    'rating_key', 'title', 'metadata_type', 'year', 'added_at',
                    'library_name', 'poster_url', 'backdrop_url', 'clearlogo_url'
                ]

                # Store extra data as JSON
                extra_data = {k: v for k, v in data_copy.items() if k not in core_fields}
                data_json = json.dumps(extra_data) if extra_data else None

                cursor.execute("""
                    INSERT INTO recent_items_cache (
                        rating_key, title, metadata_type, year, added_at,
                        library_name, poster_url, backdrop_url, clearlogo_url, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.get('rating_key'), item.get('title'), item.get('metadata_type'),
                    item.get('year'), item.get('added_at'), item.get('library_name'),
                    item.get('poster_url'), item.get('backdrop_url'),
                    item.get('clearlogo_url'), data_json
                ))

            # Update metadata
            duration_ms = int((time.time() - start_time) * 1000)
            cursor.execute("""
                INSERT OR REPLACE INTO cache_metadata (cache_key, last_update, item_count, update_duration_ms)
                VALUES (?, ?, ?, ?)
            """, ('recent_items', datetime.now().isoformat(), len(items), duration_ms))

            conn.commit()
            conn.close()

            logger.info(f"Updated recent items cache with {len(items)} items in {duration_ms}ms")

        except Exception as e:
            logger.error(f"Error updating recent items cache: {e}")
            try:
                conn.close()
            except Exception:
                pass

    def check_ownership(self, title: str = None, tvdb_id: str = None, imdb_id: str = None) -> Optional[Dict]:
        """Check if a movie/show is owned (cached lookup)"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Build cache key from available identifiers
            cache_key = None
            if tvdb_id:
                cache_key = f"tvdb_{tvdb_id}"
            elif imdb_id:
                cache_key = f"imdb_{imdb_id}"
            elif title:
                cache_key = f"title_{title.lower()}"
            else:
                return None

            cursor.execute("""
                SELECT * FROM ownership_cache
                WHERE cache_key = ?
            """, (cache_key,))

            row = cursor.fetchone()
            conn.close()

            if row:
                result = dict(row)
                if result.get('data_json'):
                    try:
                        additional_data = json.loads(result['data_json'])
                        result.update(additional_data)
                    except:
                        pass
                    del result['data_json']
                return result
            return None

        except Exception as e:
            logger.error(f"Error checking ownership cache: {e}")
            return None

    def get_tv_episode_ownership(self, show_key: str, max_age_seconds: int = 300) -> Dict:
        """Get cached TV episode ownership for a show.

        Returns empty dict if cache is missing or older than max_age_seconds (default 5 min).
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Check cache freshness first
            cursor.execute("""
                SELECT MAX(cached_at) as latest
                FROM tv_episode_ownership
                WHERE show_key = ?
            """, (show_key,))
            age_row = cursor.fetchone()
            if age_row and age_row['latest']:
                cache_time = datetime.fromisoformat(age_row['latest'])
                age = (datetime.now() - cache_time).total_seconds()
                if age > max_age_seconds:
                    conn.close()
                    return {}  # Cache expired, caller will re-fetch from Plex

            cursor.execute("""
                SELECT season, episode, is_owned, rating_key, runtime
                FROM tv_episode_ownership
                WHERE show_key = ?
            """, (show_key,))

            ownership = {}
            for row in cursor.fetchall():
                key = f"s{row['season']}e{row['episode']}"
                ownership[key] = {
                    'owned': bool(row['is_owned']),
                    'rating_key': row['rating_key'],
                    'runtime': row['runtime']
                }

            conn.close()
            return ownership

        except Exception as e:
            logger.error(f"Error getting TV episode ownership: {e}")
            try:
                conn.close()
            except Exception:
                pass
            return {}

    def update_ownership_cache(self, items: List[Dict]):
        """Update ownership cache with movie/show ownership data"""
        try:
            start_time = time.time()
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            for item in items:
                cache_key = item.get('cache_key')
                if not cache_key:
                    continue

                # Extract core fields
                data_copy = item.copy()
                core_fields = ['cache_key', 'is_owned', 'tvdb_id', 'imdb_id', 'rating_key', 'metadata_type']
                extra_data = {k: v for k, v in data_copy.items() if k not in core_fields}
                data_json = json.dumps(extra_data) if extra_data else None

                cursor.execute("""
                    INSERT OR REPLACE INTO ownership_cache (
                        cache_key, is_owned, tvdb_id, imdb_id, rating_key,
                        metadata_type, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    cache_key,
                    1 if item.get('is_owned') else 0,
                    item.get('tvdb_id'),
                    item.get('imdb_id'),
                    item.get('rating_key'),
                    item.get('metadata_type'),
                    data_json
                ))

            duration_ms = int((time.time() - start_time) * 1000)
            cursor.execute("""
                INSERT OR REPLACE INTO cache_metadata (cache_key, last_update, item_count, update_duration_ms)
                VALUES (?, ?, ?, ?)
            """, ('ownership', datetime.now().isoformat(), len(items), duration_ms))

            conn.commit()
            conn.close()

            logger.info(f"Updated ownership cache with {len(items)} items in {duration_ms}ms")

        except Exception as e:
            logger.error(f"Error updating ownership cache: {e}")
            try:
                conn.close()
            except Exception:
                pass

    def update_tv_episode_ownership(self, show_key: str, episodes: Dict):
        """Update TV episode ownership cache for a specific show"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Clear existing entries for this show
            cursor.execute("DELETE FROM tv_episode_ownership WHERE show_key = ?", (show_key,))

            # Insert new episodes
            for ep_key, ep_data in episodes.items():
                # Parse s1e1 format
                if ep_key.startswith('s') and 'e' in ep_key:
                    parts = ep_key[1:].split('e')
                    if len(parts) == 2:
                        season = int(parts[0])
                        episode = int(parts[1])

                        cursor.execute("""
                            INSERT INTO tv_episode_ownership (
                                show_key, season, episode, is_owned, rating_key, runtime
                            ) VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            show_key, season, episode,
                            1 if ep_data.get('owned') else 0,
                            ep_data.get('rating_key'),
                            ep_data.get('runtime')
                        ))

            conn.commit()
            conn.close()

            logger.info(f"Updated TV episode ownership for {show_key} with {len(episodes)} episodes")

        except Exception as e:
            logger.error(f"Error updating TV episode ownership: {e}")
            try:
                conn.close()
            except Exception:
                pass

    def get_recent_tv_episodes(self, limit: int = 20) -> List[Dict]:
        """Get cached recently added TV shows (sorted by most recent episode added)"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT *
                FROM recent_tv_episodes_cache
                ORDER BY latest_added_at DESC
                LIMIT ?
            """, (limit,))

            items = []
            for row in cursor.fetchall():
                item = dict(row)
                # Parse JSON data if present
                if item.get('data_json'):
                    try:
                        additional_data = json.loads(item['data_json'])
                        item.update(additional_data)
                    except:
                        pass
                    del item['data_json']
                if 'cached_at' in item:
                    del item['cached_at']
                items.append(item)

            conn.close()
            return items

        except Exception as e:
            logger.error(f"Error getting cached recent TV episodes: {e}")
            try:
                conn.close()
            except Exception:
                pass
            return []

    def update_recent_tv_episodes(self, items: List[Dict]):
        """Update recent TV episodes cache (shows sorted by most recent episode added)"""
        try:
            start_time = time.time()
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Clear existing cache
            cursor.execute("DELETE FROM recent_tv_episodes_cache")

            # Insert new items
            for item in items:
                # Extract core fields
                data_copy = item.copy()
                core_fields = [
                    'show_rating_key', 'show_title', 'show_year', 'show_guid',
                    'latest_added_at', 'added_date', 'poster_url'
                ]

                # Store extra data as JSON
                extra_data = {k: v for k, v in data_copy.items() if k not in core_fields}
                data_json = json.dumps(extra_data) if extra_data else None

                cursor.execute("""
                    INSERT OR REPLACE INTO recent_tv_episodes_cache (
                        show_rating_key, show_title, show_year, show_guid,
                        latest_added_at, added_date, poster_url, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.get('show_rating_key'), item.get('show_title'),
                    item.get('show_year'), item.get('show_guid'),
                    item.get('latest_added_at'), item.get('added_date'),
                    item.get('poster_url'), data_json
                ))

            # Update metadata
            duration_ms = int((time.time() - start_time) * 1000)
            cursor.execute("""
                INSERT OR REPLACE INTO cache_metadata (cache_key, last_update, item_count, update_duration_ms)
                VALUES (?, ?, ?, ?)
            """, ('recent_tv_episodes', datetime.now().isoformat(), len(items), duration_ms))

            conn.commit()
            conn.close()

            logger.info(f"Updated recent TV episodes cache with {len(items)} items in {duration_ms}ms")

        except Exception as e:
            logger.error(f"Error updating recent TV episodes cache: {e}")
            try:
                conn.close()
            except Exception:
                pass

    def get_cache_info(self) -> Dict:
        """Get information about all caches"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM cache_metadata")

            info = {}
            for row in cursor.fetchall():
                cache_key = row['cache_key']
                last_update = datetime.fromisoformat(row['last_update'])
                age = (datetime.now() - last_update).total_seconds()

                info[cache_key] = {
                    'last_update': row['last_update'],
                    'age_seconds': int(age),
                    'item_count': row['item_count'],
                    'update_duration_ms': row['update_duration_ms']
                }

            conn.close()
            return info

        except Exception as e:
            logger.error(f"Error getting cache info: {e}")
            try:
                conn.close()
            except Exception:
                pass
            return {}


# Create global instance
library_cache = LibraryCache()