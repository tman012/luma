#!/usr/bin/env python3
"""
Configuration module for media-search-app
Loads configuration from environment variables
"""

import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    """Configuration settings for the application"""

    # TMDB API
    TMDB_API_KEY = os.environ.get('TMDB_API_KEY', '')

    # qBittorrent settings (managed via settings.json / UI)
    QBITTORRENT_URL = os.environ.get('QBITTORRENT_URL', '')
    QBITTORRENT_USER = os.environ.get('QBITTORRENT_USER', '')
    QBITTORRENT_PASS = os.environ.get('QBITTORRENT_PASS', '')

    # Sonarr settings
    SONARR_URL = os.environ.get('SONARR_URL', 'http://192.168.1.62:8989')
    SONARR_API_KEY = os.environ.get('SONARR_API_KEY', '')

    # Radarr settings
    RADARR_URL = os.environ.get('RADARR_URL', 'http://192.168.1.62:7878')
    RADARR_API_KEY = os.environ.get('RADARR_API_KEY', '')

    # Radarr download monitoring - monitors qBittorrent category for Radarr downloads
    RADARR_QBIT_CATEGORY = os.environ.get('RADARR_QBIT_CATEGORY', 'radarr')
    RADARR_MONITORING_ENABLED = os.environ.get('RADARR_MONITORING_ENABLED', 'true').lower() == 'true'

    # Media paths - Docker environment
    IS_DOCKER = os.environ.get('IS_DOCKER', 'true').lower() == 'true'
    DOWNLOADS_ROOT = os.environ.get('DOWNLOADS_ROOT', '/downloads')
    MOVIES_ROOT = os.environ.get('MOVIES_ROOT', '/media/movies')
    RECENT_ROOT = os.environ.get('RECENT_ROOT', '/media/recent')
    TV_ROOT = os.environ.get('TV_ROOT', '/media/tv')
    TV_4K_ROOT = os.environ.get('TV_4K_ROOT', '/media/tv-4k')
    TV_REMUX_ROOT = os.environ.get('TV_REMUX_ROOT', '/media/tv-remux')
    FULLRIPS_ROOT = os.environ.get('FULLRIPS_ROOT', '/media/fullrips')
    FOURKAY_ROOT = os.environ.get('FOURKAY_ROOT', '/media/4k')

    # Recent year threshold - includes current year and previous year
    # Movies from current year or last year will go to RECENT_ROOT
    RECENT_YEAR = str(datetime.now().year - 1)

    # Video file extensions
    VIDEO_EXTS = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.m2ts']

    # Plex settings (from existing config)
    PLEX_URL = os.environ.get('PLEX_URL', 'http://192.168.1.25:32400')
    PLEX_TOKEN = os.environ.get('PLEX_TOKEN', '')
    PLEX_ACCOUNT_ID = int(os.environ.get('PLEX_ACCOUNT_ID', '1'))  # Default to account 1 (server owner)

    # Database path
    DATABASE_PATH = os.environ.get('DATABASE_PATH', '/app/data/media.db')

    @classmethod
    def validate(cls):
        """Validate required configuration"""
        required = {
            'TMDB_API_KEY': cls.TMDB_API_KEY,
        }

        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        return True
