#!/usr/bin/env python3
"""
Explore movies cache management
Caches popular/seeded movies for fast loading
"""

import sqlite3
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class ExploreCache:
    """Manages cached explore movies for fast loading
    
    Cache storage: LUMA_CACHE_DIR environment variable sets the cache root (defaults to /app/cache).
    Explore cache uses the 'explore' subdirectory within the root.
    """

    def __init__(self, cache_root: Optional[str] = None):
        if cache_root is None:
            from app.paths import cache_dir
            cache_root = str(cache_dir())
        # Use 'explore' subdirectory within cache root
        self.cache_dir = Path(cache_root) / 'explore'
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            error_msg = (f"explore_cache: LUMA_CACHE_DIR={cache_root} not writable "
                        f"(tried to create {self.cache_dir}): {e}")
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        self.db_path = self.cache_dir / "explore_cache.db"
        self.update_interval = 6 * 3600  # was 15 min (21-query tracker sweep ~100x/day)
        self.last_update = None
        self.updating = False
        self.lock = threading.Lock()

        # Initialize database
        self._init_database()

    def _init_database(self):
        """Initialize the cache database"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Create movies table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS explore_movies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    year TEXT,
                    poster_url TEXT,
                    tmdb_id TEXT,
                    imdb_id TEXT,
                    seeders INTEGER DEFAULT 0,
                    size_gb REAL,
                    resolution TEXT,
                    source TEXT,
                    tracker TEXT,
                    torrent_name TEXT,
                    download_url TEXT,
                    info_hash TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data_json TEXT
                )
            """)

            # Migration: add pub_date column if missing (older DBs)
            cursor.execute("PRAGMA table_info(explore_movies)")
            columns = {row[1] for row in cursor.fetchall()}
            if 'pub_date' not in columns:
                cursor.execute("ALTER TABLE explore_movies ADD COLUMN pub_date TEXT DEFAULT ''")
                logger.info("Migrated explore_movies: added pub_date column")

            # Create indexes for faster queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_seeders
                ON explore_movies(seeders DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_pub_date
                ON explore_movies(pub_date DESC)
            """)

            # Create metadata table for tracking updates
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()
            conn.close()
            logger.info(f"Explore cache database initialized at {self.db_path}")

        except Exception as e:
            logger.error(f"Error initializing explore cache database: {e}")

    def update_cache(self, movies: List[Dict]) -> bool:
        """Update the cache with new movie data"""
        did_start = False
        try:
            with self.lock:
                if self.updating:
                    logger.warning("Cache update already in progress")
                    return False

                self.updating = True
                did_start = True

            conn = sqlite3.connect(str(self.db_path))
            try:
                cursor = conn.cursor()

                # Clear old data
                cursor.execute("DELETE FROM explore_movies")

                # Insert ALL movies (no truncation — frontend handles limits/filters)
                for movie in movies:
                    cursor.execute("""
                        INSERT INTO explore_movies (
                            title, year, poster_url, tmdb_id, imdb_id,
                            seeders, size_gb, resolution, source, tracker,
                            torrent_name, download_url, info_hash, pub_date, data_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        movie.get('title', ''),
                        movie.get('year', ''),
                        movie.get('poster', ''),
                        movie.get('tmdb_id', ''),
                        movie.get('imdb_id', ''),
                        movie.get('seeders', 0),
                        movie.get('size_gb', 0),
                        movie.get('resolution', ''),
                        movie.get('source', ''),
                        movie.get('tracker', ''),
                        movie.get('torrent_name', ''),
                        movie.get('download_url', ''),
                        movie.get('info_hash', ''),
                        movie.get('pub_date', ''),
                        json.dumps(movie)
                    ))

                # Update metadata
                cursor.execute("""
                    INSERT OR REPLACE INTO cache_metadata (key, value, updated_at)
                    VALUES ('last_update', ?, CURRENT_TIMESTAMP)
                """, (datetime.now().isoformat(),))

                cursor.execute("""
                    INSERT OR REPLACE INTO cache_metadata (key, value, updated_at)
                    VALUES ('movie_count', ?, CURRENT_TIMESTAMP)
                """, (len(movies),))

                conn.commit()
                self.last_update = datetime.now()
                logger.info(f"Explore cache updated with {len(movies)} movies")
                return True

            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error updating explore cache: {e}")
            return False

        finally:
            if did_start:
                with self.lock:
                    self.updating = False

    def get_cached_movies(self, limit: int = 100) -> List[Dict]:
        """Get cached movies from the database"""
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get movies ordered by seeders
            cursor.execute("""
                SELECT data_json FROM explore_movies
                ORDER BY seeders DESC
                LIMIT ?
            """, (limit,))

            rows = cursor.fetchall()

            # Parse JSON data
            movies = []
            for row in rows:
                try:
                    movie_data = json.loads(row['data_json'])
                    movies.append(movie_data)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse movie JSON data")

            return movies

        except Exception as e:
            logger.error(f"Error getting cached movies: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def query_top_movies(self, quality: Optional[str] = None,
                         sort: str = 'seeders', limit: int = 25,
                         min_seeders: int = 0,
                         tracker: Optional[str] = None) -> List[Dict]:
        """Fast filtered query against the cache for AI agent use.

        Args:
            quality: '4k', '4k_remux', '4k_web', '1080p', '1080p_remux',
                     '1080p_web', 'remux', or None for all.
            sort: 'seeders' (default) or 'newest'.
            limit: Max results (default 25).
            min_seeders: Minimum seeder count filter.
            tracker: Filter to a specific tracker name (case-insensitive).
        """
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            conditions = []
            params: list = []

            if min_seeders > 0:
                conditions.append("seeders >= ?")
                params.append(min_seeders)

            if tracker:
                conditions.append("LOWER(tracker) = LOWER(?)")
                params.append(tracker)

            # Quality filters use indexed columns + torrent_name patterns
            if quality:
                q = quality.lower().replace('-', '_')
                if q == '4k':
                    conditions.append("resolution = '2160p'")
                elif q == '4k_remux':
                    conditions.append("resolution = '2160p'")
                    conditions.append("UPPER(torrent_name) LIKE '%REMUX%'")
                elif q == '4k_web':
                    conditions.append("resolution = '2160p'")
                    conditions.append(
                        "(UPPER(torrent_name) LIKE '%WEB-DL%' OR "
                        "UPPER(torrent_name) LIKE '%WEBDL%' OR "
                        "UPPER(torrent_name) LIKE '%WEB.DL%')")
                elif q in ('1080p', '1080'):
                    conditions.append("resolution = '1080p'")
                elif q in ('1080p_remux', '1080_remux'):
                    conditions.append("resolution = '1080p'")
                    conditions.append("UPPER(torrent_name) LIKE '%REMUX%'")
                elif q in ('1080p_web', '1080_web'):
                    conditions.append("resolution = '1080p'")
                    conditions.append(
                        "(UPPER(torrent_name) LIKE '%WEB-DL%' OR "
                        "UPPER(torrent_name) LIKE '%WEBDL%' OR "
                        "UPPER(torrent_name) LIKE '%WEB.DL%')")
                elif q == 'remux':
                    conditions.append("UPPER(torrent_name) LIKE '%REMUX%'")

            where = ""
            if conditions:
                where = "WHERE " + " AND ".join(conditions)

            order = "seeders DESC" if sort != 'newest' else "pub_date DESC"

            sql = f"""
                SELECT title, year, resolution, source, tracker,
                       seeders, size_gb, pub_date, tmdb_id, imdb_id,
                       torrent_name, poster_url
                FROM explore_movies
                {where}
                ORDER BY {order}
                LIMIT ?
            """
            params.append(limit)

            cursor.execute(sql, params)
            rows = cursor.fetchall()

            return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error in query_top_movies: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_available_trackers(self) -> List[Dict]:
        """Return tracker names and their movie counts."""
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT tracker, COUNT(*) as count
                FROM explore_movies
                WHERE tracker != ''
                GROUP BY tracker
                ORDER BY count DESC
            """)
            return [{'name': row[0], 'count': row[1]}
                    for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting trackers: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def needs_update(self) -> bool:
        """Check if cache needs updating"""
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute("""
                SELECT value FROM cache_metadata
                WHERE key = 'last_update'
            """)

            row = cursor.fetchone()

            if not row:
                return True

            last_update = datetime.fromisoformat(row[0])
            time_since_update = datetime.now() - last_update

            return time_since_update.total_seconds() > self.update_interval

        except Exception as e:
            logger.error(f"Error checking cache status: {e}")
            return True
        finally:
            if conn:
                conn.close()

    def get_cache_info(self) -> Dict:
        """Get information about the cache"""
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute("""
                SELECT key, value, updated_at FROM cache_metadata
            """)

            metadata = {}
            for row in cursor.fetchall():
                metadata[row[0]] = {
                    'value': row[1],
                    'updated_at': row[2]
                }

            cursor.execute("SELECT COUNT(*) FROM explore_movies")
            movie_count = cursor.fetchone()[0]

            return {
                'movie_count': movie_count,
                'metadata': metadata,
                'needs_update': self.needs_update(),
                'db_path': str(self.db_path)
            }

        except Exception as e:
            logger.error(f"Error getting cache info: {e}")
            return {
                'error': str(e),
                'movie_count': 0,
                'needs_update': True
            }
        finally:
            if conn:
                conn.close()

# Global instance (lazy initialization)
_explore_cache_instance = None

def _get_explore_cache():
    """Get or create the global explore cache instance."""
    global _explore_cache_instance
    if _explore_cache_instance is None:
        _explore_cache_instance = ExploreCache()
    return _explore_cache_instance


class _ExploreCacheLazy:
    """Lazy wrapper for explore cache to defer initialization until first use."""
    def __getattr__(self, name):
        return getattr(_get_explore_cache(), name)
    
    def __call__(self, *args, **kwargs):
        return _get_explore_cache()(*args, **kwargs)


explore_cache = _ExploreCacheLazy()