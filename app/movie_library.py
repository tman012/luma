#!/usr/bin/env python3
"""
Movie Library module for managing the media database
Integrates with the existing SQLite database to track movies
"""

import os
import re
import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict
import requests

from app.config import Config

logger = logging.getLogger('MovieLibrary')

class MovieLibrary:
    """Manages the movie library database"""

    def __init__(self):
        self.db_path = Config.DATABASE_PATH
        self.ensure_database()

    def ensure_database(self):
        """Ensure database and tables exist"""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

    def get_connection(self):
        """Get a database connection"""
        return sqlite3.connect(self.db_path)

    def add_single_movie_file(self, file_path: str, is_recent: bool = False) -> bool:
        """
        Add a single movie file to the library database

        Args:
            file_path: Full path to the movie file
            is_recent: Whether this is a recent movie

        Returns:
            True if successfully added, False otherwise
        """
        try:
            # Extract movie info from filename
            filename = os.path.basename(file_path)
            name_without_ext = os.path.splitext(filename)[0]

            # Try to extract title and year
            # Format: "Movie Title (2024).mkv"
            match = re.match(r'^(.+?)\s*\((\d{4})\)', name_without_ext)
            if match:
                title = match.group(1).strip()
                year = int(match.group(2))
            else:
                # Fallback: use filename as title
                title = name_without_ext
                year = None

            logger.info(f"Adding movie to database: {title} ({year})")

            # Get file size
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

            # Detect quality from filename
            quality = self.detect_quality(filename)

            # Try to fetch metadata from TMDB
            tmdb_id = None
            imdb_id = None
            overview = None
            poster_path = None
            backdrop_path = None
            rating = None
            runtime = None
            genres = None

            if year:
                metadata = self.fetch_tmdb_metadata(title, year)
                if metadata:
                    tmdb_id = metadata.get('id')
                    imdb_id = metadata.get('imdb_id')
                    overview = metadata.get('overview')
                    poster_path = metadata.get('poster_path')
                    backdrop_path = metadata.get('backdrop_path')
                    rating = metadata.get('vote_average')
                    runtime = metadata.get('runtime')

                    # Convert genres list to comma-separated string
                    if metadata.get('genres'):
                        genres = ','.join([g['name'] for g in metadata['genres']])

            # Insert into database
            conn = self.get_connection()
            cursor = conn.cursor()

            # Check if movie already exists
            cursor.execute(
                "SELECT id FROM movies WHERE file_path = ?",
                (file_path,)
            )

            if cursor.fetchone():
                logger.info(f"Movie already in database: {title}")
                conn.close()
                return True

            # Insert new movie
            cursor.execute('''
                INSERT INTO movies (
                    title, year, tmdb_id, imdb_id, overview,
                    poster_path, backdrop_path, rating, runtime,
                    genres, file_path, quality, size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                title, year, tmdb_id, imdb_id, overview,
                poster_path, backdrop_path, rating, runtime,
                genres, file_path, quality, file_size
            ))

            conn.commit()
            conn.close()

            logger.info(f"✅ Successfully added movie to database: {title} ({year})")
            return True

        except Exception as e:
            logger.error(f"Error adding movie to database: {e}")
            return False

    def detect_quality(self, filename: str) -> str:
        """Detect video quality from filename"""
        filename_lower = filename.lower()

        if any(x in filename_lower for x in ['2160p', '4k', 'uhd']):
            if 'remux' in filename_lower:
                return '4K REMUX'
            elif any(x in filename_lower for x in ['web-dl', 'webdl', 'webrip']):
                return '4K WEB-DL'
            else:
                return '4K'
        elif '1080p' in filename_lower:
            if 'remux' in filename_lower:
                return '1080p REMUX'
            elif any(x in filename_lower for x in ['web-dl', 'webdl', 'webrip']):
                return '1080p WEB-DL'
            else:
                return '1080p'
        elif '720p' in filename_lower:
            return '720p'
        else:
            return 'HD'

    def _tmdb_api_key(self):
        """Resolve TMDb API key: SettingsManager first, Config env fallback."""
        try:
            from app.settings_manager import SettingsManager
            sm = SettingsManager()
            return sm.get('apis.tmdb.api_key') or Config.TMDB_API_KEY
        except Exception:
            return Config.TMDB_API_KEY

    def fetch_tmdb_metadata(self, title: str, year: int) -> Optional[Dict]:
        """Fetch movie metadata from TMDB"""
        try:
            tmdb_key = self._tmdb_api_key()
            # Search for the movie
            search_url = "https://api.themoviedb.org/3/search/movie"
            params = {
                'api_key': tmdb_key,
                'query': title,
                'year': year
            }

            response = requests.get(search_url, params=params, timeout=10)
            if response.status_code != 200:
                return None

            data = response.json()
            if not data.get('results'):
                return None

            # Get the first result
            movie_id = data['results'][0]['id']

            # Fetch full movie details
            details_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
            params = {
                'api_key': tmdb_key,
                'append_to_response': 'external_ids'
            }

            response = requests.get(details_url, params=params, timeout=10)
            if response.status_code != 200:
                return None

            movie_data = response.json()

            # Add IMDb ID from external_ids
            if 'external_ids' in movie_data:
                movie_data['imdb_id'] = movie_data['external_ids'].get('imdb_id')

            return movie_data

        except Exception as e:
            logger.error(f"Error fetching TMDB metadata: {e}")
            return None

    def get_movie_by_path(self, file_path: str) -> Optional[Dict]:
        """Get movie from database by file path"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM movies WHERE file_path = ?",
                (file_path,)
            )

            row = cursor.fetchone()
            conn.close()

            if row:
                columns = [col[0] for col in cursor.description]
                return dict(zip(columns, row))

            return None

        except Exception as e:
            logger.error(f"Error getting movie from database: {e}")
            return None

    def get_movie_by_tmdb_id(self, tmdb_id: int) -> Optional[Dict]:
        """Get movie from database by TMDB ID"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM movies WHERE tmdb_id = ?",
                (tmdb_id,)
            )

            row = cursor.fetchone()
            conn.close()

            if row:
                columns = [col[0] for col in cursor.description]
                return dict(zip(columns, row))

            return None

        except Exception as e:
            logger.error(f"Error getting movie by TMDB ID: {e}")
            return None
