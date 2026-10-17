#!/usr/bin/env python3
"""
Auto-rename and move system for Luma
Monitors qBittorrent for completed downloads and automatically renames/moves them
"""

import os
import re
import json
import time
import shutil
import logging
import requests
import queue
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, asdict, fields
from app.config import Config
from app.movie_library import MovieLibrary  # Import MovieLibrary for real-time updates
# The paced copy moved to app/paced_copy.py unchanged; same names here.
from app.paced_copy import COPY_CHUNK_BYTES, COPY_FLUSH_BYTES, _drop_cache, _paced_copy  # noqa: F401
from app.qbit_owner import is_test_torrent

logger = logging.getLogger('AutoRenamer')


@dataclass
class DownloadInfo:
    """Information about a download and its TMDb match"""
    torrent_hash: str
    tmdb_id: int
    media_type: str  # 'movie' or 'tv'
    title: str
    year: str
    imdb_id: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    added_timestamp: float = 0
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'DownloadInfo':
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

@dataclass
class HistoryItem:
    """Information about completed downloads"""
    torrent_hash: str
    torrent_name: str
    tmdb_title: str
    tmdb_year: str
    media_type: str
    source_path: str
    destination_path: str
    completed_timestamp: float
    success: bool
    error_message: Optional[str] = None
    # Persisted so Activity does not re-search TMDb by release-group year
    # (which often disagrees with primary_release_year and yields no poster).
    tmdb_id: Optional[int] = None
    poster_path: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'HistoryItem':
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

class AutoRenamer:
    """Monitors qBittorrent and automatically renames/moves completed downloads"""

    def _radarr_settings(self):
        """Resolve Radarr URL + API key: SettingsManager first, Config env fallback."""
        try:
            from app.settings_manager import SettingsManager
            sm = SettingsManager()
            url = sm.get('radarr.url') or Config.RADARR_URL
            api_key = sm.get('radarr.api_key') or Config.RADARR_API_KEY
        except Exception:
            url = Config.RADARR_URL
            api_key = Config.RADARR_API_KEY
        return url, api_key

    def _tmdb_api_key(self):
        """Resolve TMDb API key: SettingsManager first, Config env fallback."""
        try:
            from app.settings_manager import SettingsManager
            sm = SettingsManager()
            return sm.get('apis.tmdb.api_key') or Config.TMDB_API_KEY
        except Exception:
            return Config.TMDB_API_KEY

    def _radarr_qbit_category(self):
        """Resolve Radarr qBittorrent category: SettingsManager first, Config env fallback."""
        try:
            from app.settings_manager import SettingsManager
            sm = SettingsManager()
            return sm.get('radarr.qbit_category') or Config.RADARR_QBIT_CATEGORY
        except Exception:
            return Config.RADARR_QBIT_CATEGORY

    def _radarr_monitoring_enabled(self):
        """Resolve Radarr monitoring enabled flag: SettingsManager first, Config env fallback."""
        try:
            from app.settings_manager import SettingsManager
            sm = SettingsManager()
            val = sm.get('radarr.monitoring_enabled')
            if val is not None:
                return bool(val)
            return Config.RADARR_MONITORING_ENABLED
        except Exception:
            return Config.RADARR_MONITORING_ENABLED

    def _qbit_settings(self):
        """Resolve qBittorrent URL + credentials: SettingsManager first, Config env fallback."""
        try:
            from app.settings_manager import SettingsManager
            sm = SettingsManager()
            url = sm.get('qbittorrent.url') or Config.QBITTORRENT_URL
            user = sm.get('qbittorrent.username') or Config.QBITTORRENT_USER
            password = sm.get('qbittorrent.password') or Config.QBITTORRENT_PASS
        except Exception:
            url = Config.QBITTORRENT_URL
            user = Config.QBITTORRENT_USER
            password = Config.QBITTORRENT_PASS
        return url, user, password

    def __init__(self):
        # Use Windows-compatible paths - store in logs directory
        if os.name == 'nt':  # Windows
            # Store in the logs directory which already exists
            self.monitor_file = os.path.join('logs', 'download_monitor.json')
            self.history_file = os.path.join('logs', 'download_history.json')
            self.copy_progress_file = os.path.join('logs', 'copy_progress.json')
            self.radarr_processed_file = os.path.join('logs', 'radarr_processed.json')
        else:  # Docker/Linux
            self.monitor_file = '/app/data/download_monitor.json'
            self.history_file = '/app/data/download_history.json'
            self.copy_progress_file = '/app/data/copy_progress.json'
            self.radarr_processed_file = '/app/data/radarr_processed.json'

        self._copy_progress_lock = threading.Lock()
        self.ensure_data_dir()
        self.load_monitor_data()
        self.load_history()
        self.load_radarr_processed()
        # Cached qBittorrent session (re-authenticates on 403)
        self._qbit_cached_session: Optional[requests.Session] = None

        # Check for incomplete copies on startup and remove stale entries
        self.check_incomplete_copies_on_startup()
        
    def ensure_data_dir(self):
        """Ensure data directory exists"""
        os.makedirs(os.path.dirname(self.monitor_file), exist_ok=True)
    
    def load_monitor_data(self):
        """Load tracked downloads from file"""
        self.tracked_downloads = {}
        if os.path.exists(self.monitor_file):
            try:
                with open(self.monitor_file, 'r') as f:
                    data = json.load(f)
                    for hash_id, info in data.items():
                        self.tracked_downloads[hash_id] = DownloadInfo.from_dict(info)
                logger.info(f"📄 Loaded {len(self.tracked_downloads)} tracked downloads")
            except Exception as e:
                logger.error(f"Error loading monitor data: {e}")
                self.tracked_downloads = {}
    
    def save_monitor_data(self):
        """Save tracked downloads to file"""
        try:
            data = {k: v.to_dict() for k, v in self.tracked_downloads.items()}
            with open(self.monitor_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving monitor data: {e}")
    
    def load_history(self):
        """Load download history from file"""
        self.history = []
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    data = json.load(f)
                    for item in data:
                        self.history.append(HistoryItem.from_dict(item))
                logger.info(f"📚 Loaded {len(self.history)} history items")
            except Exception as e:
                logger.error(f"Error loading history: {e}")
                self.history = []
    
    def save_history(self):
        """Save download history to file"""
        try:
            # Keep only last 100 items
            if len(self.history) > 100:
                self.history = self.history[-100:]

            data = [item.to_dict() for item in self.history]
            with open(self.history_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    def load_radarr_processed(self):
        """Load set of already-processed Radarr download hashes"""
        self.radarr_processed = set()
        if os.path.exists(self.radarr_processed_file):
            try:
                with open(self.radarr_processed_file, 'r') as f:
                    data = json.load(f)
                    self.radarr_processed = set(data)
                logger.info(f"📄 Loaded {len(self.radarr_processed)} processed Radarr downloads")
            except Exception as e:
                logger.error(f"Error loading radarr processed data: {e}")
                self.radarr_processed = set()

    def save_radarr_processed(self):
        """Save processed Radarr download hashes to file"""
        try:
            # Keep only last 500 hashes to prevent unbounded growth
            if len(self.radarr_processed) > 500:
                # Convert to list, keep last 500, convert back
                self.radarr_processed = set(list(self.radarr_processed)[-500:])

            with open(self.radarr_processed_file, 'w') as f:
                json.dump(list(self.radarr_processed), f, indent=2)
        except Exception as e:
            logger.error(f"Error saving radarr processed data: {e}")

    def update_copy_progress(self, torrent_hash: str, torrent_name: str, progress: int, source: str, destination: str, size: int):
        """Update copy progress for a torrent"""
        try:
            with self._copy_progress_lock:
                # Load current progress
                copy_progress = {}
                if os.path.exists(self.copy_progress_file):
                    try:
                        with open(self.copy_progress_file, 'r') as f:
                            copy_progress = json.load(f)
                    except:
                        pass

                # Update progress
                copy_progress[torrent_hash] = {
                    'torrent_name': torrent_name,
                    'progress': progress,
                    'source': source,
                    'destination': destination,
                    'size': size,
                    'updated_at': time.time()
                }

                # Save
                with open(self.copy_progress_file, 'w') as f:
                    json.dump(copy_progress, f, indent=2)

        except Exception as e:
            logger.error(f"Error updating copy progress: {e}")

    def clear_copy_progress(self, torrent_hash: str):
        """Clear copy progress for a torrent"""
        try:
            with self._copy_progress_lock:
                if os.path.exists(self.copy_progress_file):
                    with open(self.copy_progress_file, 'r') as f:
                        copy_progress = json.load(f)

                    if torrent_hash in copy_progress:
                        del copy_progress[torrent_hash]

                        with open(self.copy_progress_file, 'w') as f:
                            json.dump(copy_progress, f, indent=2)
        except Exception as e:
            logger.error(f"Error clearing copy progress: {e}")

    def get_copy_progress(self):
        """Get all current copy progress"""
        try:
            with self._copy_progress_lock:
                if os.path.exists(self.copy_progress_file):
                    with open(self.copy_progress_file, 'r') as f:
                        return json.load(f)
        except Exception as e:
            logger.error(f"Error reading copy progress: {e}")
        return {}

    def check_incomplete_copies_on_startup(self):
        """Check for incomplete copies on startup and allow them to be reprocessed.

        IMPORTANT: This runs on the main thread during __init__, so it must NOT
        touch network mounts (os.path.exists on /media/* or /downloads/*) which
        can block indefinitely on unresponsive CIFS/NFS mounts and prevent Flask
        from starting.  We simply clear the progress tracking and remove hashes
        from radarr_processed so the background monitor_loop will pick them up.
        """
        try:
            copy_progress = self.get_copy_progress()
            if not copy_progress:
                return

            logger.info(f"🔍 Found {len(copy_progress)} incomplete copies from previous session")

            for torrent_hash, progress_info in copy_progress.items():
                torrent_name = progress_info.get('torrent_name', 'Unknown')
                progress = progress_info.get('progress', 0)
                source = progress_info.get('source', '')
                destination = progress_info.get('destination', '')

                logger.info(f"📋 Incomplete copy: {torrent_name}")
                logger.info(f"   Progress: {progress}%")
                logger.info(f"   Source: {source}")
                logger.info(f"   Destination: {destination}")

                # Remove from radarr_processed so monitor_loop can reprocess
                hash_upper = torrent_hash.upper()
                if hash_upper in self.radarr_processed:
                    self.radarr_processed.remove(hash_upper)
                    logger.info(f"   🔄 Removed from processed list for reprocessing")

                hash_lower = torrent_hash.lower()
                if hash_lower in self.radarr_processed:
                    self.radarr_processed.remove(hash_lower)

            # Clear all copy progress entries (they'll be recreated during reprocessing)
            if os.path.exists(self.copy_progress_file):
                with open(self.copy_progress_file, 'w') as f:
                    json.dump({}, f)
                logger.info("📝 Cleared stale copy progress entries")

            # Save updated radarr_processed
            self.save_radarr_processed()

        except Exception as e:
            logger.error(f"Error checking incomplete copies on startup: {e}")

    def add_to_history(self, torrent_hash: str, torrent_name: str, download_info: DownloadInfo, 
                      source: str, destination: str, success: bool, error: Optional[str] = None):
        """Add an item to history"""
        poster_path = None
        tmdb_id = getattr(download_info, 'tmdb_id', None)
        # Resolve poster at write time so Activity never depends on a later
        # title+year re-search (release-group years often mismatch TMDb).
        if tmdb_id:
            try:
                kind = 'tv' if download_info.media_type == 'tv' else 'movie'
                resp = requests.get(
                    f'https://api.themoviedb.org/3/{kind}/{int(tmdb_id)}',
                    params={'api_key': self._tmdb_api_key()},
                    timeout=8,
                )
                if resp.status_code == 200:
                    poster_path = (resp.json() or {}).get('poster_path')
            except Exception as e:
                logger.debug(f'poster fetch at history write failed: {e}')
        history_item = HistoryItem(
            torrent_hash=torrent_hash,
            torrent_name=torrent_name,
            tmdb_title=download_info.title,
            tmdb_year=download_info.year,
            media_type=download_info.media_type,
            source_path=source,
            destination_path=destination,
            completed_timestamp=time.time(),
            success=success,
            error_message=error,
            tmdb_id=int(tmdb_id) if tmdb_id else None,
            poster_path=poster_path,
        )

        self.history.append(history_item)
        self.save_history()
    
    def get_tracking_data(self) -> Dict[str, DownloadInfo]:
        """Get current tracking data"""
        return dict(self.tracked_downloads)
    
    def get_history(self, limit: int = 30) -> List[Dict]:
        """Get download history as list of dicts"""
        # Sort by timestamp, most recent first
        sorted_history = sorted(self.history, key=lambda x: x.completed_timestamp, reverse=True)
        return [item.to_dict() for item in sorted_history[:limit]]
    
    def remove_from_tracking(self, torrent_hash: str):
        """Remove a torrent from tracking"""
        if torrent_hash in self.tracked_downloads:
            del self.tracked_downloads[torrent_hash]
            self.save_monitor_data()
            logger.info(f"🗑️ Removed {torrent_hash} from tracking")
    
    def track_download(self, torrent_hash: str, tmdb_data: Dict):
        """Add a download to tracking"""
        # Check if the title indicates a screener
        if 'title' in tmdb_data and self.is_screener_file(tmdb_data['title']):
            logger.warning(f"🚫 Rejecting screener release: {tmdb_data['title']}")
            return
        
        download_info = DownloadInfo(
            torrent_hash=torrent_hash,
            tmdb_id=tmdb_data['tmdb_id'],
            media_type=tmdb_data['media_type'],
            title=tmdb_data['title'],
            year=tmdb_data.get('year', ''),
            imdb_id=tmdb_data.get('imdb_id'),
            added_timestamp=time.time()
        )
        
        self.tracked_downloads[torrent_hash] = download_info
        self.save_monitor_data()
        logger.info(f"📥 Now tracking download: {download_info.title} ({download_info.year})")
    
    def _do_qbit_login(self) -> Optional[requests.Session]:
        """Perform a fresh qBittorrent login and return the session."""
        try:
            qbit_url, qbit_user, qbit_pass = self._qbit_settings()
            session = requests.Session()
            response = session.post(
                f"{qbit_url}/api/v2/auth/login",
                data={'username': qbit_user, 'password': qbit_pass},
                timeout=10,
            )
            if response.status_code == 200 and response.text.strip() == 'Ok.':
                return session
            else:
                logger.error(f"❌ qBittorrent login failed: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"qBittorrent login error: {e}")
            return None

    def get_authenticated_session(self) -> Optional[requests.Session]:
        """Return a cached qBittorrent session, re-authenticating only when needed."""
        if self._qbit_cached_session is not None:
            # Validate the cached session with a lightweight call
            try:
                qbit_url = self._qbit_settings()[0]
                r = self._qbit_cached_session.get(
                    f"{qbit_url}/api/v2/app/version", timeout=5
                )
                if r.status_code == 200:
                    return self._qbit_cached_session
            except Exception:
                pass
            # Session expired or unreachable — fall through to re-auth
            self._qbit_cached_session = None

        self._qbit_cached_session = self._do_qbit_login()
        return self._qbit_cached_session
    
    def find_torrent_by_name(self, torrent_name: str) -> Optional[Dict]:
        """Find a torrent by its name (Sonarr-style fresh auth)"""
        session = self.get_authenticated_session()
        if not session:
            logger.error("Failed to authenticate with qBittorrent")
            return None

        try:
            response = session.get(
                f"{self._qbit_settings()[0]}/api/v2/torrents/info",
                timeout=10
            )
            if response.status_code == 200:
                torrents = response.json()
                for torrent in torrents:
                    if torrent.get('name') == torrent_name:
                        return torrent
        except Exception as e:
            logger.error(f"❌ Error finding torrent by name: {e}")
        return None

    def get_torrent_info(self, torrent_hash: str) -> Optional[Dict]:
        """Get information about a specific torrent (Sonarr-style fresh auth)"""
        max_retries = 3

        for attempt in range(max_retries):
            # Get a fresh authenticated session for this request (like Sonarr does)
            session = self.get_authenticated_session()
            if not session:
                logger.error(f"Failed to authenticate with qBittorrent (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return None

            try:
                response = session.get(
                    f"{self._qbit_settings()[0]}/api/v2/torrents/info",
                    params={'hashes': torrent_hash},
                    timeout=10
                )

                if response.status_code == 200:
                    torrents = response.json()
                    return torrents[0] if torrents else None
                else:
                    # Any non-200 response, just retry with fresh session
                    logger.error(f"Unexpected status code {response.status_code} (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    return None

            except requests.exceptions.RequestException as e:
                logger.error(f"Request error getting torrent info: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
            except Exception as e:
                logger.error(f"Unexpected error getting torrent info: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue

        logger.error(f"Failed to get torrent info after {max_retries} attempts")
        return None
    
    def get_torrent_files(self, torrent_hash: str) -> List[Dict]:
        """Get list of files in a torrent (Sonarr-style fresh auth)"""
        max_retries = 3

        for attempt in range(max_retries):
            # Get a fresh authenticated session for this request (like Sonarr does)
            session = self.get_authenticated_session()
            if not session:
                logger.error(f"Failed to authenticate with qBittorrent (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return []

            try:
                response = session.get(
                    f"{self._qbit_settings()[0]}/api/v2/torrents/files",
                    params={'hash': torrent_hash},
                    timeout=10
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    # Any non-200 response, just retry with fresh session
                    logger.error(f"Unexpected status code {response.status_code} (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    return []

            except requests.exceptions.RequestException as e:
                logger.error(f"Request error getting torrent files: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
            except Exception as e:
                logger.error(f"Unexpected error getting torrent files: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue

        logger.error(f"Failed to get torrent files after {max_retries} attempts")
        return []
    
    def sanitize_filename(self, s: str) -> str:
        """Sanitize filename for filesystem"""
        return re.sub(r'[<>"/\\|?*]', '', s.replace(':', '-'))
        

    def get_tmdb_backdrop(self, tmdb_id: int, media_type: str = 'movie') -> str:
        """Fetch backdrop URL from TMDb for push notification image."""
        try:
            kind = 'tv' if media_type == 'tv' else 'movie'
            url = f"https://api.themoviedb.org/3/{kind}/{tmdb_id}"
            resp = requests.get(url, params={'api_key': self._tmdb_api_key()}, timeout=5)
            if resp.status_code == 200:
                path = resp.json().get('backdrop_path', '')
                if path:
                    full = f"https://image.tmdb.org/t/p/w780{path}"
                    logger.info(f"📸 TMDb backdrop for {kind}/{tmdb_id}: {full}")
                    return full
                else:
                    logger.warning(f"📸 No backdrop_path for {kind}/{tmdb_id}")
            else:
                logger.warning(f"📸 TMDb API returned {resp.status_code} for {kind}/{tmdb_id}")
        except Exception as e:
            logger.error(f"📸 TMDb backdrop fetch error: {e}")
        return ''

    def get_episode_title(self, tmdb_id: int, season: int, episode: int) -> Optional[str]:
        """Fetch episode title from TMDb API"""
        try:
            # Construct the API URL
            url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            params = {
                'api_key': self._tmdb_api_key()
            }

            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                episode_name = data.get('name', '')
                if episode_name:
                    logger.info(f"📺 Found episode title: S{season:02d}E{episode:02d} - {episode_name}")
                    return self.sanitize_filename(episode_name)
            else:
                logger.warning(f"⚠️ Could not fetch episode title: {response.status_code}")
                
        except Exception as e:
            logger.error(f"❌ Error fetching episode title: {e}")
        
        return None

    def generate_destination_path(self, download_info: DownloadInfo, filename: str) -> str:
        """Generate the destination path for a file"""
        base_name = os.path.splitext(os.path.basename(filename))[0]
        extension = os.path.splitext(filename)[1]
    
        # Get dynamic directory paths
        from app.movie_library import MovieLibrary
        movie_lib = MovieLibrary()
        
        # Use environment variables from Config if available
        paths = {
            'movies_root': Config.MOVIES_ROOT,
            'recent_root': Config.RECENT_ROOT,
            'tv_root': Config.TV_ROOT,
            'tv_4k_root': Config.TV_4K_ROOT,
            'tv_remux_root': Config.TV_REMUX_ROOT,
            'fullrips_root': Config.FULLRIPS_ROOT,
            'fourkay_root': Config.FOURKAY_ROOT,
        }
        recent_year = int(Config.RECENT_YEAR)
        
        logger.info(f"🔧 Destination paths: movies={paths['movies_root']}, tv={paths['tv_root']}")
        logger.info(f"🔧 Media type: {download_info.media_type}")
        
        if download_info.media_type == 'movie':
            # Movie naming: "Title (Year).ext"
            clean_title = self.sanitize_filename(download_info.title)
            if download_info.year:
                new_name = f"{clean_title} ({download_info.year}){extension}"
                folder_name = f"{clean_title} ({download_info.year})"
            else:
                new_name = f"{clean_title}{extension}"
                folder_name = clean_title
            
            # Detect video quality
            quality = self.detect_video_quality(filename)
            
            # Determine destination folder based on quality and year
            if quality['is_4k']:
                dest_root = paths.get('fourkay_root', paths['movies_root'])
                logger.info(f"🎬 4K movie detected -> {dest_root}")
            elif quality['is_remux']:
                dest_root = paths.get('fullrips_root', paths['movies_root'])
                logger.info(f"🎬 Remux movie detected -> {dest_root}")
            elif download_info.year and str(download_info.year).isdigit() and int(download_info.year) >= recent_year:
                dest_root = paths['recent_root']
                logger.info(f"🎬 Recent movie ({download_info.year} >= {recent_year}) -> {dest_root}")
            else:
                dest_root = paths['movies_root']
                logger.info(f"🎬 Regular movie -> {dest_root}")
            
            return os.path.join(dest_root, folder_name, new_name)
        
        else:  # TV show
            # TV show organization
            clean_title = self.sanitize_filename(download_info.title)
            
            # Detect video quality for TV shows
            quality = self.detect_video_quality(filename)
            
            # Determine TV root based on quality
            if quality['is_4k']:
                tv_root = paths.get('tv_4k_root', paths['tv_root'])
                logger.info(f"📺 4K TV show detected -> {tv_root}")
            elif quality['is_remux'] and quality['is_1080p']:
                tv_root = paths.get('tv_remux_root', paths['tv_root'])
                logger.info(f"📺 1080p Remux TV show detected -> {tv_root}")
            else:
                tv_root = paths['tv_root']
                logger.info(f"📺 Regular TV show -> {tv_root}")
            
            show_dir = os.path.join(tv_root, clean_title)
            
            # Try to detect season and episode from filename
            season_match = re.search(r'[sS](\d{1,2})[eE](\d{1,2})', base_name)
            if season_match:
                season_num = int(season_match.group(1))
                episode_num = int(season_match.group(2))
                season_dir = os.path.join(show_dir, f"Season {season_num:02d}")
                os.makedirs(season_dir, exist_ok=True)
                
                # Try to get episode title from TMDb
                episode_title = None
                if download_info.tmdb_id:
                    episode_title = self.get_episode_title(download_info.tmdb_id, season_num, episode_num)
                
                # Format: "Show Name - S01E01 - Episode Title.ext"
                if episode_title:
                    new_name = f"{clean_title} - S{season_num:02d}E{episode_num:02d} - {episode_title}{extension}"
                else:
                    new_name = f"{clean_title} - S{season_num:02d}E{episode_num:02d}{extension}"
                
                return os.path.join(season_dir, new_name)
            else:
                # Check if it's a full season pack
                season_only_match = re.search(r'[sS](\d{1,2})(?![eE]\d)', base_name)
                if season_only_match:
                    season_num = int(season_only_match.group(1))
                    season_dir = os.path.join(show_dir, f"Season {season_num:02d}")
                    os.makedirs(season_dir, exist_ok=True)
                    
                    # For season packs, keep original filename but in proper directory
                    return os.path.join(season_dir, os.path.basename(filename))
                else:
                    # Can't detect season/episode, just put in show folder
                    os.makedirs(show_dir, exist_ok=True)
                    return os.path.join(show_dir, os.path.basename(filename))

    def process_tv_season_pack(self, torrent_hash: str, download_info: DownloadInfo, video_files: List[str]):
        """Special handling for TV season packs - rename each episode with title"""
        logger.info(f"📺 Processing TV season pack with {len(video_files)} files")
        
        success_count = 0
        failed_files = []
        successful_destinations = []
        
        for video_file in video_files:
            try:
                # Generate destination with episode title
                destination = self.generate_destination_path(download_info, video_file)
                logger.info(f"📁 Episode destination: {destination}")

                if self.copy_and_rename_file(video_file, destination, torrent_hash, getattr(download_info, "torrent_name", None)):
                    success_count += 1
                    successful_destinations.append(destination)
                    logger.info(f"✅ Successfully processed: {os.path.basename(video_file)}")
                else:
                    failed_files.append(os.path.basename(video_file))
                    logger.error(f"❌ Failed to process: {os.path.basename(video_file)}")
                    
            except Exception as e:
                logger.error(f"❌ Error processing episode {video_file}: {e}")
                failed_files.append(os.path.basename(video_file))
        
        return success_count, failed_files, successful_destinations
    
    def detect_video_quality(self, filename: str) -> Dict[str, bool]:
        """Detect video quality from filename"""
        filename_lower = filename.lower()
        
        # Check for remux
        is_remux = bool(re.search(r'\b(remux|blu-?ray\s*remux|bd\s*remux)\b', filename_lower))
        
        # Check for 4K/2160p
        is_4k = bool(re.search(r'\b(2160p|4k|uhd)\b', filename_lower))
        
        # Check for 1080p
        is_1080p = bool(re.search(r'\b1080p\b', filename_lower))
        
        return {
            'is_remux': is_remux,
            'is_4k': is_4k,
            'is_1080p': is_1080p
        }
    
    def map_download_path(self, qbittorrent_path: str) -> str:
        """Map qBittorrent internal path to actual filesystem path"""
        # qBittorrent reports paths like /downloads/filename
        # But actual files might be at different locations

        # Extract just the filename/folder from the path
        path_parts = qbittorrent_path.replace('\\', '/').split('/')
        torrent_folder = path_parts[-1] if path_parts else qbittorrent_path

        # Check if we're in Docker
        if Config.IS_DOCKER:
            # In Docker, qBittorrent paths should match our container paths
            # The container sees downloads under /downloads
            possible_mappings = [
                # Direct mapping should work as both containers see /downloads
                qbittorrent_path,
                # Try container download paths with the torrent folder
                os.path.join('/downloads', torrent_folder),
                # Handle case where qBittorrent reports just filename
                os.path.join('/downloads', torrent_folder) if not qbittorrent_path.startswith('/') else qbittorrent_path,
                # Replace common qBittorrent paths
                qbittorrent_path.replace('/data/downloads/', '/downloads/'),
                # Fallback to config path
                os.path.join(Config.DOWNLOADS_ROOT, torrent_folder),
            ]
        else:
            # Windows/Local paths
            possible_mappings = [
                # Direct mapping (if running on same system)
                qbittorrent_path,

                # Windows D: drive mapping (primary location)
                os.path.join('D:\\', torrent_folder),
                qbittorrent_path.replace('/downloads/', 'D:\\'),

                # Common Docker mappings (for mixed environments)
                qbittorrent_path.replace('/downloads/', Config.DOWNLOADS_ROOT + '/'),

                # Handle case where qBittorrent path is just the filename
                os.path.join(Config.DOWNLOADS_ROOT, torrent_folder),
            ]
        
        # Test each possible path
        for test_path in possible_mappings:
            if os.path.exists(test_path):
                logger.debug(f"✅ Found file at: {test_path}")
                return test_path

        logger.warning(f"⚠️ Could not find file at any of these locations:")
        for test_path in possible_mappings[:5]:  # Just show first 5 to avoid spam
            logger.warning(f"  - {test_path}")

        # CRITICAL FIX: Return the most likely path instead of qbittorrent_path
        # Fall back to the first (most likely) mapping
        return possible_mappings[0] if possible_mappings else qbittorrent_path
    
    def is_file_stable(self, filepath: str, check_duration: int = 3) -> bool:
        """Check if a file's size is stable (not being written to)"""
        try:
            if not os.path.exists(filepath):
                logger.warning(f"⚠️ File does not exist for stability check: {os.path.basename(filepath)}")
                return False
            
            initial_size = os.path.getsize(filepath)
            initial_mtime = os.path.getmtime(filepath)
            
            time.sleep(check_duration)
            
            final_size = os.path.getsize(filepath)
            final_mtime = os.path.getmtime(filepath)
            
            # File is stable if size and modification time haven't changed
            is_stable = (initial_size == final_size) and (initial_mtime == final_mtime)
            
            if not is_stable:
                logger.info(f"📁 File {os.path.basename(filepath)} is not stable - Size: {initial_size} -> {final_size}")
            
            return is_stable
            
        except Exception as e:
            logger.error(f"Error checking file stability for {os.path.basename(filepath)}: {e}")
            return False
    
    def find_video_files(self, torrent_info: Dict, torrent_files: List[Dict], download_info: DownloadInfo) -> Optional[List[str]]:
        """Find video files in the torrent - handles both single file and multi-file torrents"""
        video_files = []
        torrent_path = torrent_info['content_path']

        logger.info(f"🔍 Finding video files for torrent: {torrent_info['name']}")
        logger.info(f"📂 Content path from qBittorrent: {torrent_path}")
        
        # Map the path to actual filesystem location
        actual_path = self.map_download_path(torrent_path)
        
        # CHECK FOR EXTRACTION IN PROGRESS - RECURSIVELY
        # Check main directory and all subdirectories for extraction lock files
        if os.path.isdir(actual_path):
            # Check for any lock files recursively
            for root, dirs, files in os.walk(actual_path):
                if '.extraction_in_progress' in files:
                    logger.warning(f"⏳ Extraction in progress in {root}, skipping...")
                    return None  # Return None to indicate extraction pending
        
        # Also check parent directory for lock file
        parent_dir = os.path.dirname(actual_path) if not os.path.isdir(actual_path) else actual_path
        lock_file = os.path.join(parent_dir, '.extraction_in_progress')
        if os.path.exists(lock_file):
            logger.warning(f"⏳ Extraction in progress in parent directory, skipping...")
            return None
        
        logger.info(f"📄 Number of files in torrent: {len(torrent_files)}")

        # Check if this is a single file torrent
        if len(torrent_files) == 1:
            logger.info("📁 Single file torrent detected")
            file_info = torrent_files[0]
            filename = file_info['name']
            
            logger.info(f"📄 Single file: {filename}")
            
            # Check if it's a video file and not a sample
            # Check if it's a video file and not a sample or screener
            if any(filename.lower().endswith(ext) for ext in Config.VIDEO_EXTS) and not self.is_sample_file(filename) and not self.is_screener_file(filename):
                # For single file torrents, the content_path IS the file
                logger.info(f"🔍 Checking stability of single video file: {actual_path}")
                if os.path.exists(actual_path) and self.is_file_stable(actual_path):
                    video_files.append(actual_path)
                    logger.info(f"✅ Found single video file: {filename}")
                else:
                    logger.warning(f"❌ Single video file not found or not stable at: {actual_path}")
            else:
                logger.info(f"ℹ️ Single file is not a valid video or is a sample: {filename}")
        else:
            # Multi-file torrent - need to handle different directory structures
            logger.info("📁 Multi-file torrent detected")
            
            # First, check if actual_path is the directory containing the files
            if os.path.isdir(actual_path):
                base_dir = actual_path
            else:
                base_dir = os.path.dirname(actual_path)
            
            logger.info(f"📂 Base directory: {base_dir}")
            
            # IMPROVED LOGIC: First collect all video files, THEN check if we need to wait for extraction
            logger.info("🔍 Performing comprehensive recursive search for video files...")
            
            # Track directories with RAR files but no videos
            dirs_with_only_rars = []
            
            # Track all files we're checking
            files_checked = []
            files_skipped = []
            
            # Collect all video files recursively
            for root, dirs, files in os.walk(base_dir):
                has_rar = False
                has_video = False
                
                logger.debug(f"🔍 Checking directory: {root}")
                logger.debug(f"  Files: {len(files)}")
                
                # Check what's in this directory
                for file in sorted(files):  # Sort files for consistent ordering
                    if file.endswith('.rar') or re.match(r'.*\.r\d+$', file):
                        has_rar = True
                        
                    if any(file.lower().endswith(ext) for ext in Config.VIDEO_EXTS):
                        files_checked.append(file)
                        
                        if self.is_sample_file(file):
                            logger.info(f"⭕️ Skipping sample file: {file}")
                            files_skipped.append(f"{file} (sample)")
                            continue
                            
                        if self.is_screener_file(file):
                            logger.info(f"🚫 Skipping screener file: {file}")
                            files_skipped.append(f"{file} (screener)")
                            continue
                            
                        full_path = os.path.join(root, file)
                        file_size = os.path.getsize(full_path)
                        
                        # Skip files smaller than 100MB (likely samples or extras)
                        if file_size < 100 * 1024 * 1024:
                            logger.info(f"⭕️ Skipping small file ({self.format_bytes(file_size)}): {file}")
                            files_skipped.append(f"{file} (too small: {self.format_bytes(file_size)})")
                            continue
                        
                        # Check if file is stable
                        logger.info(f"🔍 Checking stability of: {file}")
                        if self.is_file_stable(full_path):
                            video_files.append(full_path)
                            has_video = True
                            logger.info(f"✅ Found stable video file: {full_path} (Size: {self.format_bytes(file_size)})")
                            
                            # Try to extract season/episode info from path
                            relative_path = os.path.relpath(full_path, base_dir)
                            season_match = re.search(r'[sS]eason\s*(\d+)', relative_path)
                            episode_match = re.search(r'[sS](\d{1,2})[eE](\d{1,2})', file)
                            
                            if season_match:
                                logger.info(f"  📺 Detected Season {season_match.group(1)}")
                            if episode_match:
                                season_num = int(episode_match.group(1))
                                ep_num = int(episode_match.group(2))
                                logger.info(f"  📺 Detected S{season_num:02d}E{ep_num:02d}")
                        else:
                            logger.warning(f"⏳ Found unstable video file (still being written): {full_path}")
                            files_skipped.append(f"{file} (unstable)")
                            # If any video file is unstable, return None to wait
                            return None
                
                # Track directories that have RAR files but no videos
                if has_rar and not has_video:
                    dirs_with_only_rars.append(root)
                    logger.info(f"📦 Directory has RAR files but no videos: {root}")
            
            # Log summary of files checked/skipped
            logger.info(f"📊 File scan summary:")
            logger.info(f"  - Total video files checked: {len(files_checked)}")
            logger.info(f"  - Files skipped: {len(files_skipped)}")
            if files_skipped:
                for skipped in files_skipped[:5]:  # Show first 5
                    logger.info(f"    • {skipped}")
                if len(files_skipped) > 5:
                    logger.info(f"    • ... and {len(files_skipped) - 5} more")
            
            # NEW LOGIC: Only return None if we found NO video files at all
            if not video_files and dirs_with_only_rars:
                logger.warning(f"⏳ Found {len(dirs_with_only_rars)} directories with RAR files but no videos extracted yet")
                # Check if this is a new torrent that might still be extracting
                torrent_age = time.time() - download_info.added_timestamp
                if torrent_age < 3600:  # Less than 1 hour old
                    logger.info(f"⏳ Torrent is only {int(torrent_age/60)} minutes old, waiting for extraction...")
                    return None
                else:
                    logger.warning(f"⚠️ Torrent is {int(torrent_age/3600)} hours old but no videos found - extraction may have failed")
                    # Return empty list to indicate no videos found (will be removed from tracking)
                    return []
            
            # Sort video files by path to maintain order
            video_files.sort()
        
        logger.info(f"📊 Total video files found: {len(video_files)}")
        if video_files:
            logger.info(f"🎬 Video files found:")
            for i, vf in enumerate(video_files):
                logger.info(f"   {i+1}. {os.path.basename(vf)}")
        
        return video_files if video_files else []

    def is_sample_file(self, filename: str) -> bool:
        """Check if a file is likely a sample file based on its name"""
        filename_lower = filename.lower()
        sample_indicators = [
            'sample',
            'trailer',
            'preview',
            'promo',
            'featurette',
            'deleted.scene',
            'deleted_scene',
            'extra',
            'behind.the.scenes',
            'behind_the_scenes',
            'making.of',
            'making_of'
        ]
    
        # Check if any sample indicator is in the filename
        for indicator in sample_indicators:
            if indicator in filename_lower:
                return True
    
        # Check for common sample file patterns
        if re.search(r'[\[\(]sample[\]\)]', filename_lower):
            return True
    
        return False
    
    def is_screener_file(self, filename: str) -> bool:
        """Check if a file is a screener release"""
        filename_lower = filename.lower()
        screener_indicators = [
            'screener',
            'scr',
            'dvdscr', 
            'dvdscreener',
            'bdscr',
            'webscreener',
            'hdscreener'
        ]
        
        # Check if any screener indicator is in the filename
        for indicator in screener_indicators:
            # Use word boundaries to avoid false positives (like "screen" or "script")
            if re.search(r'\b' + indicator + r'\b', filename_lower):
                return True
        
        # Also check common patterns like [SCREENER] or .SCREENER.
        if re.search(r'[\[\(\.]screener[\]\)\.]', filename_lower):
            return True
            
        return False
    
    def select_main_video_files(self, potential_videos: List[Dict], download_info: DownloadInfo) -> List[str]:
        """Select the main video file(s) from a list of potential videos"""
        if not potential_videos:
            return []
    
        # Filter out files that are too small (less than 100MB)
        min_size = 100 * 1024 * 1024  # 100MB
        valid_videos = [v for v in potential_videos if v['size'] >= min_size]
    
        if not valid_videos:
            logger.warning("⚠️ All video files are smaller than 100MB, selecting largest anyway")
            valid_videos = potential_videos
    
        # For movies, select the largest file
        if download_info.media_type == 'movie':
            # Sort by size descending
            valid_videos.sort(key=lambda x: x['size'], reverse=True)
        
            # If there's a clear main file (significantly larger than others), select it
            if len(valid_videos) > 1:
                largest = valid_videos[0]
                second_largest = valid_videos[1]
            
                # If the largest is at least 5x bigger than the second, it's likely the main file
                if largest['size'] > second_largest['size'] * 5:
                    logger.info(f"📦 Selected main video file: {largest['filename']} ({self.format_bytes(largest['size'])})")
                    return [largest['path']]
        
            # Otherwise, select all files above minimum size (might be multi-part)
            selected = []
            for video in valid_videos:
                # Skip if this looks like a duplicate of an already selected file
                if any(self.is_likely_duplicate(video['filename'], sel) for sel in selected):
                    continue
                selected.append(video['path'])
                logger.info(f"📦 Selected video file: {video['filename']} ({self.format_bytes(video['size'])})")
        
            return selected
    
        # For TV shows, return all valid episodes
        else:
            return [v['path'] for v in valid_videos]

    def is_likely_duplicate(self, filename1: str, filename2: str) -> bool:
        """Check if two filenames are likely the same content (different quality/format)"""
        # Remove extension and quality indicators
        clean1 = re.sub(r'\.(mkv|mp4|avi|mov|wmv|flv|webm)$', '', filename1.lower())
        clean2 = re.sub(r'\.(mkv|mp4|avi|mov|wmv|flv|webm)$', '', filename2.lower())
    
        # Remove quality indicators
        quality_patterns = [
            r'720p', r'1080p', r'2160p', r'4k',
            r'bluray', r'webrip', r'web-dl', r'hdtv',
            r'x264', r'x265', r'h264', r'h265',
            r'remux', r'hdr', r'10bit'
        ]
    
        for pattern in quality_patterns:
            clean1 = re.sub(pattern, '', clean1, flags=re.IGNORECASE)
            clean2 = re.sub(pattern, '', clean2, flags=re.IGNORECASE)
    
        # Remove extra spaces and dots
        clean1 = re.sub(r'[\s\.\-_]+', ' ', clean1).strip()
        clean2 = re.sub(r'[\s\.\-_]+', ' ', clean2).strip()
    
        # Check if they're essentially the same
        return clean1 == clean2

    def format_bytes(self, size_in_bytes: int) -> str:
        """Format bytes to human readable string"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_in_bytes < 1024.0:
                return f"{size_in_bytes:.1f} {unit}"
            size_in_bytes /= 1024.0
        return f"{size_in_bytes:.1f} PB"
    
    def copy_and_rename_file(self, source: str, destination: str, torrent_hash: str = None, torrent_name: str = None) -> bool:
        """Copy file from source to destination, paced so it does not stall the host."""
        try:
            file_size = os.path.getsize(source)
            # Create destination directory if needed
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            
            # If destination already exists, remove it to allow overwrite
            if os.path.exists(destination):
                logger.info(f"🔄 Destination exists, will overwrite: {destination}")
                try:
                    os.remove(destination)
                    logger.info(f"🗑️ Removed existing file for overwrite")
                except Exception as e:
                    logger.error(f"❌ Could not remove existing file: {e}")
                    return False
            
            # Initialize progress tracking
            if torrent_hash and torrent_name:
                self.update_copy_progress(torrent_hash, torrent_name, 0, source, destination, file_size)

            # Paced copy. Progress comes from the bytes we have actually written
            # rather than a thread polling getsize() on the destination — that
            # poll is itself a round trip to the CIFS share every 500ms.
            logger.info(f"📋 Copying: {source} -> {destination}")

            last_progress = -1

            def report(copied: int):
                nonlocal last_progress
                if not (torrent_hash and torrent_name and file_size):
                    return
                progress = int((copied / file_size) * 100)
                if progress != last_progress:
                    self.update_copy_progress(torrent_hash, torrent_name, progress, source, destination, file_size)
                    logger.debug(f"Copy progress: {progress}%")
                    last_progress = progress

            _paced_copy(source, destination, on_progress=report)

            # Best-effort — the library share forces its own file_mode, and
            # set_file_permissions() runs after this anyway.
            try:
                shutil.copymode(source, destination)
            except OSError as e:
                logger.debug(f"copymode skipped for {destination}: {e}")

            # Verify the copy
            if os.path.exists(destination):
                source_size = os.path.getsize(source)
                dest_size = os.path.getsize(destination)
                if source_size == dest_size:
                    logger.info(f"✅ Successfully copied: {os.path.basename(destination)}")
                    if torrent_hash:
                        self.clear_copy_progress(torrent_hash)
                    return True
                else:
                    logger.error(f"❌ Size mismatch after copy: {source_size} != {dest_size}")
                    os.remove(destination)
                    if torrent_hash:
                        self.clear_copy_progress(torrent_hash)
                    return False
            
        except Exception as e:
            logger.error(f"❌ Error copying file: {e}")
            return False
        
        return False

    def set_file_permissions(self, filepath: str):
        """Set file permissions compatible with Unraid/Samba shares"""
        try:
            if os.path.isfile(filepath):
                # Set files to 664 (rw-rw-r--) - readable/writable by owner and group
                os.chmod(filepath, 0o664)
                logger.debug(f"Set permissions to 664 for file: {filepath}")
            elif os.path.isdir(filepath):
                # Set directories to 775 (rwxrwxr-x) - accessible by owner and group
                os.chmod(filepath, 0o775)
                logger.debug(f"Set permissions to 775 for directory: {filepath}")
            
            # Set ownership to nobody:users (only works if running as root or with sudo)
            if Config.IS_DOCKER or os.name != 'nt':
                try:
                    # Try to set ownership
                    # Note: This will fail if not running as root, but that's okay
                    import pwd
                    import grp
                    
                    # Get UID for 'nobody' user
                    try:
                        nobody_uid = pwd.getpwnam('nobody').pw_uid
                    except KeyError:
                        # 'nobody' might not exist, try common alternatives
                        nobody_uid = 65534  # Common UID for nobody
                    
                    # Get GID for 'users' group
                    try:
                        users_gid = grp.getgrnam('users').gr_gid
                    except KeyError:
                        # 'users' might not exist, try common alternatives
                        users_gid = 100  # Common GID for users
                    
                    os.chown(filepath, nobody_uid, users_gid)
                    logger.debug(f"✅ Set ownership to nobody:users for: {filepath}")
                except Exception as e:
                    logger.debug(f"ℹ️ Could not set ownership (this is normal if not running as root): {e}")
            
            # Also set permissions on the parent directory to ensure access
            parent_dir = os.path.dirname(filepath)
            try:
                current_perms = os.stat(parent_dir).st_mode
                # Add write and execute permissions for all users if not already set
                new_perms = current_perms | 0o777
                if new_perms != current_perms:
                    os.chmod(parent_dir, new_perms)
                    logger.debug(f"✅ Set directory permissions to 777 for: {parent_dir}")
            except Exception as e:
                logger.debug(f"ℹ️ Could not modify directory permissions: {e}")
                
        except Exception as e:
            logger.error(f"❌ Error setting file permissions: {e}")
        
    def process_completed_torrent(self, torrent_hash: str, download_info: DownloadInfo):
        """Process a completed torrent - rename and move files"""
        logger.info(f"========== PROCESSING COMPLETED TORRENT ==========")
        logger.info(f"Processing: {download_info.title} ({download_info.year})")
            
        # Get torrent info
        torrent_info = self.get_torrent_info(torrent_hash)
        if not torrent_info:
            logger.error("❌ Could not get torrent info")
            self.add_to_history(torrent_hash, "Unknown", download_info, "", "", False, "Could not get torrent info")
            # Remove from tracking to prevent repeated processing
            if torrent_hash in self.tracked_downloads:
                del self.tracked_downloads[torrent_hash]
                self.save_monitor_data()
            return False
        
        torrent_name = torrent_info['name']
        logger.info(f"📁 Torrent name: {torrent_name}")
        logger.info(f"📊 Torrent state: {torrent_info['state']}")
        logger.info(f"📂 Content path: {torrent_info['content_path']}")
        
        # Get files in torrent
        torrent_files = self.get_torrent_files(torrent_hash)
        if not torrent_files:
            logger.error("❌ Could not get torrent files")
            self.add_to_history(torrent_hash, torrent_name, download_info, "", "", False, "Could not get torrent files")
            # Remove from tracking to prevent repeated processing
            if torrent_hash in self.tracked_downloads:
                del self.tracked_downloads[torrent_hash]
                self.save_monitor_data()
            return False
        
        logger.info(f"📄 Files in torrent: {len(torrent_files)}")
        for i, file_info in enumerate(torrent_files):
            logger.info(f"  📄 File {i+1}: {file_info['name']}")
        
        # Find video files
        video_files = self.find_video_files(torrent_info, torrent_files, download_info)
        
        if video_files is None:
            # Extraction is pending, don't remove from tracking
            logger.info("⏳ Extraction appears to be pending, will check again later")
            return None  # Pending, not success or failure yet
        
        if not video_files:
            logger.warning("⚠️ No video files found in torrent")
            logger.info("This might be due to path mapping issues or the files not being video files")
            self.add_to_history(torrent_hash, torrent_name, download_info, "", "", False, "No video files found")
            # Remove from tracking to prevent repeated processing of failed torrents
            if torrent_hash in self.tracked_downloads:
                del self.tracked_downloads[torrent_hash]
                self.save_monitor_data()
                logger.info(f"🗑️ Removed failed torrent from tracking: {download_info.title}")
            return False
        
        logger.info(f"Processing {len(video_files)} video files:")
        
        # Process each video file
        # Initialize MovieLibrary for real-time updates
        movie_lib = MovieLibrary()

        if download_info.media_type == 'tv' and len(video_files) > 1:
            # TV season pack - process all episodes
            success_count, failed_files, successful_destinations = self.process_tv_season_pack(
                torrent_hash, download_info, video_files
            )

            # Remove from tracking IMMEDIATELY after copy so bell notification fires
            if torrent_hash in self.tracked_downloads:
                del self.tracked_downloads[torrent_hash]
                self.save_monitor_data()
                logger.info(f"🗑️ Removed {download_info.title} from tracking (copy done)")

            # Notify once for the TV season pack
            if success_count > 0:
                # Web push notification
                try:
                    from app.push_service import send_push_notification
                    _display = f'{download_info.title} ({download_info.year})' if download_info.year else download_info.title
                    _backdrop = self.get_tmdb_backdrop(download_info.tmdb_id, download_info.media_type)
                    _modal_url = f'/?modal={download_info.media_type}&tmdb={download_info.tmdb_id}'
                    send_push_notification(
                        title=f'{_display} added!',
                        body='Tap to view details',
                        tag=f'{"tv" if download_info.media_type == "tv" else "movie"}-{download_info.title}',
                        url=_modal_url,
                        image=_backdrop,
                    )
                except Exception:
                    pass

        else:
            # Single file processing (movie or single TV episode)
            success_count = 0
            failed_files = []
            successful_destinations = []
            
            for video_file in video_files:
                logger.info(f"📄 Processing: {os.path.basename(video_file)}")
                destination = self.generate_destination_path(download_info, video_file)
                logger.info(f"📁 Destination: {destination}")

                if self.copy_and_rename_file(video_file, destination, torrent_hash, torrent_name):
                    success_count += 1
                    successful_destinations.append(destination)
                    logger.info(f"✅ Successfully processed: {os.path.basename(video_file)}")

                    # Remove from tracking after first successful copy
                    # so bell notification fires before post-copy work
                    if torrent_hash in self.tracked_downloads:
                        del self.tracked_downloads[torrent_hash]
                        self.save_monitor_data()
                        logger.info(f"🗑️ Removed {download_info.title} from tracking (copy done)")

                    # Handle movie-specific tasks
                    if download_info.media_type == 'movie':
                        try:
                            # Determine if movie is recent based on destination path
                            is_recent = False
                            if Config.RECENT_ROOT in destination:
                                is_recent = True
                            
                            # Add to library database
                            if movie_lib.add_single_movie_file(destination, is_recent=is_recent):
                                logger.info(f"📚 Added to library database: {os.path.basename(destination)}")

                                # Web push notification
                                try:
                                    from app.push_service import send_push_notification
                                    _display = f'{download_info.title} ({download_info.year})' if download_info.year else download_info.title
                                    _backdrop = self.get_tmdb_backdrop(download_info.tmdb_id, download_info.media_type)
                                    _modal_url = f'/?modal={download_info.media_type}&tmdb={download_info.tmdb_id}'
                                    send_push_notification(
                                        title=f'{_display} added!',
                                        body='Tap to view details',
                                        tag=f'{"tv" if download_info.media_type == "tv" else "movie"}-{download_info.title}',
                                        url=_modal_url,
                                        image=_backdrop,
                                    )
                                except Exception:
                                    pass

                                # Check for pending Telegram notification requests
                                try:
                                    from app.pending_requests import check_and_notify
                                    check_and_notify(
                                        media_type='movie',
                                        tmdb_id=download_info.tmdb_id,
                                        season=None,
                                        episode=None,
                                        episode_title=None,
                                    )
                                except Exception as e:
                                    logger.warning(f"Pending request check error: {e}")
                            else:
                                logger.warning(f"⚠️ Could not add to library database: {os.path.basename(destination)}")
                        except Exception as e:
                            logger.error(f"❌ Error updating library for {destination}: {e}")
                    
                    # Handle single TV episode
                    elif download_info.media_type == 'tv':
                        # Web push notification
                        try:
                            from app.push_service import send_push_notification
                            _display = f'{download_info.title} ({download_info.year})' if download_info.year else download_info.title
                            _backdrop = self.get_tmdb_backdrop(download_info.tmdb_id, download_info.media_type)
                            _modal_url = f'/?modal={download_info.media_type}&tmdb={download_info.tmdb_id}'
                            send_push_notification(
                                title=f'{_display} added!',
                                body='Tap to view details',
                                tag=f'{"tv" if download_info.media_type == "tv" else "movie"}-{download_info.title}',
                                url=_modal_url,
                                image=_backdrop,
                            )
                        except Exception:
                            pass
                else:
                    failed_files.append(os.path.basename(video_file))
                    logger.error(f"❌ Failed to process: {os.path.basename(video_file)}")
        
        logger.info(f"🎉 ========== PROCESSING COMPLETE ==========")
        logger.info(f"📊 Successfully processed {success_count}/{len(video_files)} files for {download_info.title}")
        
        # Always remove from tracking after processing attempt (success or failure)
        if torrent_hash in self.tracked_downloads:
            del self.tracked_downloads[torrent_hash]
            self.save_monitor_data()
            logger.info(f"🗑️ Removed {download_info.title} from tracking")
        
        # Add to history
        if success_count > 0:
            # Add successful history entry
            main_destination = successful_destinations[0] if successful_destinations else ""
            self.add_to_history(
                torrent_hash,
                torrent_name,
                download_info,
                video_files[0] if video_files else "",
                main_destination,
                True,
                None if success_count == len(video_files) else f"Failed files: {', '.join(failed_files)}"
            )
            return True  # Success
        else:
            # Add failed history entry
            self.add_to_history(
                torrent_hash,
                torrent_name,
                download_info,
                video_files[0] if video_files else "",
                "",
                False,
                f"Failed to process any files"
            )
            logger.warning(f"⚠️ No files were successfully processed for {download_info.title}")
            return False  # Failure
    
    def get_radarr_category_torrents(self) -> List[Dict]:
        """Get all torrents in the Radarr category from qBittorrent"""
        session = self.get_authenticated_session()
        if not session:
            logger.error("Failed to authenticate with qBittorrent for Radarr category check")
            return []

        try:
            response = session.get(
                f"{self._qbit_settings()[0]}/api/v2/torrents/info",
                params={'category': self._radarr_qbit_category()},
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get Radarr category torrents: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error getting Radarr category torrents: {e}")
            return []

    def lookup_radarr_movie(self, download_id: str) -> Optional[Dict]:
        """
        Look up movie info from Radarr using the download ID (uppercase torrent hash).

        Args:
            download_id: The uppercase torrent hash used by Radarr as downloadId

        Returns:
            Dict with tmdbId, imdbId, title, year if found, None otherwise
        """
        from app.radarr_api import RadarrAPI

        try:
            url, api_key = self._radarr_settings()
            radarr = RadarrAPI(url, api_key)

            # First check the queue (active downloads)
            queue = radarr.get_queue()
            for item in queue:
                item_download_id = item.get('downloadId', '').upper()
                if item_download_id == download_id:
                    movie = item.get('movie', {})
                    if movie:
                        logger.debug(f"Found movie in Radarr queue: {movie.get('title')}")
                        return {
                            'tmdbId': movie.get('tmdbId'),
                            'imdbId': movie.get('imdbId'),
                            'title': movie.get('title'),
                            'year': movie.get('year')
                        }

            # If not in queue, check history (recently grabbed)
            # Note: Don't pass event_type as query param - Radarr v3 API returns 400 error
            # Filter by eventType in Python instead
            history = radarr.get_history(page_size=100)
            for item in history:
                # Only look at 'grabbed' events (not 'downloadFolderImported', etc.)
                if item.get('eventType') != 'grabbed':
                    continue
                item_download_id = item.get('downloadId', '').upper()
                if item_download_id == download_id:
                    movie = item.get('movie', {})
                    if movie:
                        logger.debug(f"Found movie in Radarr history: {movie.get('title')}")
                        return {
                            'tmdbId': movie.get('tmdbId'),
                            'imdbId': movie.get('imdbId'),
                            'title': movie.get('title'),
                            'year': movie.get('year')
                        }

            logger.warning(f"Could not find movie in Radarr for download ID: {download_id}")
            return None

        except Exception as e:
            logger.error(f"Error looking up Radarr movie: {e}")
            return None

    def check_radarr_downloads(self):
        """Check Radarr category in qBittorrent for completed downloads and process them.

        IMPORTANT: only permanently mark a hash as processed after a TRUE success.
        Returning None from process_completed_torrent means extraction/pending —
        we must retry on the next monitor loop (Junior 1994 RAR incident).
        Failed attempts and Radarr lookup misses use soft retry counters so a
        transient miss cannot permanently orphan a completed torrent.
        """
        if not self._radarr_monitoring_enabled():
            return

        _, api_key = self._radarr_settings()
        if not api_key:
            logger.debug("Radarr API key not configured, skipping Radarr download check")
            return

        # Soft-retry bookkeeping (in-memory; resets on process restart which is fine)
        if not hasattr(self, "_radarr_lookup_misses"):
            self._radarr_lookup_misses = {}  # hash_upper -> count
        if not hasattr(self, "_radarr_process_fails"):
            self._radarr_process_fails = {}  # hash_upper -> count

        LOOKUP_MISS_LIMIT = 20   # ~10 min at 30s interval
        PROCESS_FAIL_LIMIT = 10  # ~5 min of hard failures after extract

        # Get all torrents in the Radarr category
        radarr_torrents = self.get_radarr_category_torrents()
        if not radarr_torrents:
            return

        # Filter to completed torrents only
        completed_states = ["completed", "pausedUP", "stalledUP", "uploading", "queuedUP"]
        # Never the test install's torrents (shared qBittorrent, see app/qbit_owner).
        completed_torrents = [
            t for t in radarr_torrents
            if t.get("progress", 0) == 1.0 and t.get("state") in completed_states
            and not is_test_torrent(t)
        ]

        if not completed_torrents:
            return

        logger.debug(f"Found {len(completed_torrents)} completed Radarr downloads to check")

        for torrent in completed_torrents:
            torrent_hash = torrent.get("hash", "")
            if not torrent_hash:
                continue
            hash_upper = torrent_hash.upper()

            # Skip if already processed successfully
            if hash_upper in self.radarr_processed:
                continue

            # Also skip if this torrent is already being tracked manually
            tracked_lower = {h.lower() for h in self.tracked_downloads}
            if torrent_hash in self.tracked_downloads or torrent_hash.lower() in tracked_lower:
                continue

            # Skip only if history already recorded a SUCCESS for this hash.
            # Failed / empty history entries must NOT block reprocessing.
            already_succeeded = any(
                (h.torrent_hash or "").upper() == hash_upper and h.success
                for h in self.history
            )
            if already_succeeded:
                self.radarr_processed.add(hash_upper)
                self.save_radarr_processed()
                self._radarr_lookup_misses.pop(hash_upper, None)
                self._radarr_process_fails.pop(hash_upper, None)
                continue

            # Look up movie info from Radarr
            logger.info(f"Processing Radarr download: {torrent.get('name', 'unknown')}")
            movie_info = self.lookup_radarr_movie(hash_upper)

            if not movie_info:
                misses = self._radarr_lookup_misses.get(hash_upper, 0) + 1
                self._radarr_lookup_misses[hash_upper] = misses
                logger.warning(
                    f"Could not find Radarr movie info for: {torrent.get('name')} "
                    f"(miss {misses}/{LOOKUP_MISS_LIMIT})"
                )
                if misses >= LOOKUP_MISS_LIMIT:
                    logger.warning(
                        f"Giving up on Radarr lookup for {torrent.get('name')} "
                        f"after {misses} misses — marking processed"
                    )
                    self.radarr_processed.add(hash_upper)
                    self.save_radarr_processed()
                    self._radarr_lookup_misses.pop(hash_upper, None)
                continue

            # Lookup succeeded — clear miss counter
            self._radarr_lookup_misses.pop(hash_upper, None)

            # Create DownloadInfo and process
            download_info = DownloadInfo(
                torrent_hash=torrent_hash,
                tmdb_id=movie_info.get("tmdbId"),
                media_type="movie",
                title=movie_info.get("title", ""),
                year=str(movie_info.get("year", "")),
                imdb_id=movie_info.get("imdbId"),
                added_timestamp=time.time()
            )

            logger.info(f"Processing Radarr movie: {download_info.title} ({download_info.year})")

            # Process using existing logic
            success = self.process_completed_torrent(torrent_hash, download_info)

            if success is True:
                # Permanent success — mark processed + unmonitor
                self.radarr_processed.add(hash_upper)
                self.save_radarr_processed()
                self._radarr_process_fails.pop(hash_upper, None)

                if movie_info.get("tmdbId"):
                    try:
                        from app.radarr_api import RadarrAPI
                        url, api_key = self._radarr_settings()
                        radarr = RadarrAPI(url, api_key)
                        if radarr.unmonitor_movie_by_tmdb(movie_info["tmdbId"]):
                            logger.info(f"Unmonitored '{download_info.title}' in Radarr")
                        else:
                            logger.warning(f"Could not unmonitor '{download_info.title}' in Radarr")
                    except Exception as e:
                        logger.error(f"Error unmonitoring movie in Radarr: {e}")

            elif success is None:
                # Extraction / file-stability pending — MUST retry next loop.
                # Do NOT touch radarr_processed (Junior 1994 RAR bug).
                logger.info(
                    f"Pending move/import for '{download_info.title}' "
                    f"(extraction or unstable file) — will retry"
                )

            else:
                # Hard failure this pass — soft-retry, then give up
                fails = self._radarr_process_fails.get(hash_upper, 0) + 1
                self._radarr_process_fails[hash_upper] = fails
                logger.warning(
                    f"Move/import failed for '{download_info.title}' "
                    f"(attempt {fails}/{PROCESS_FAIL_LIMIT})"
                )
                if fails >= PROCESS_FAIL_LIMIT:
                    logger.error(
                        f"Giving up on '{download_info.title}' after {fails} failures — "
                        f"marking processed so we stop looping"
                    )
                    self.radarr_processed.add(hash_upper)
                    self.save_radarr_processed()
                    self._radarr_process_fails.pop(hash_upper, None)
    def check_downloads(self):
        """Check all tracked downloads for completion"""
        if not self.tracked_downloads:
            return

        logger.debug(f"🔍 Checking {len(self.tracked_downloads)} tracked downloads")

        # First, check for pending torrents that need to be matched with actual torrents
        for torrent_hash, download_info in list(self.tracked_downloads.items()):
            # Check if this is a pending torrent (not yet visible in qBittorrent)
            if hasattr(download_info, 'pending') and download_info.pending:
                # Try to find the actual torrent by name
                if hasattr(download_info, 'torrent_name'):
                    actual_torrent = self.find_torrent_by_name(download_info.torrent_name)
                    if actual_torrent:
                        # Found it! Update the hash and remove pending status
                        actual_hash = actual_torrent['hash']
                        logger.info(f"✅ Found pending torrent: {download_info.title} -> {actual_hash}")

                        # Remove old tracking with temp hash
                        del self.tracked_downloads[torrent_hash]

                        # Add with real hash
                        download_info.pending = False
                        self.tracked_downloads[actual_hash] = download_info
                        self.save_monitor_data()
                        continue

        for torrent_hash, download_info in list(self.tracked_downloads.items()):
            # Skip pending torrents in main check
            if hasattr(download_info, 'pending') and download_info.pending:
                age = time.time() - download_info.added_timestamp
                if age > 3600:  # Remove pending torrents after 1 hour
                    logger.warning(f"⚠️ Removing pending torrent that never appeared: {download_info.title}")
                    del self.tracked_downloads[torrent_hash]
                    self.save_monitor_data()
                continue

            torrent_info = self.get_torrent_info(torrent_hash)

            if not torrent_info:
                # Torrent might have been removed
                age = time.time() - download_info.added_timestamp
                if age > 86400:  # Remove after 24 hours
                    logger.info(f"🗑️ Removing stale tracking: {download_info.title}")
                    del self.tracked_downloads[torrent_hash]
                    self.save_monitor_data()
                continue
            
            # Check if completed
            if torrent_info['progress'] == 1.0 and torrent_info['state'] in ['completed', 'pausedUP', 'stalledUP', 'uploading', 'queuedUP']:
                logger.info(f"🎉 Torrent completed: {download_info.title}")
                self.process_completed_torrent(torrent_hash, download_info)
            else:
                progress = torrent_info['progress'] * 100
                state = torrent_info['state'] 
                logger.debug(f"📊 Torrent {download_info.title}: {progress:.1f}% - {state}")
    
    def monitor_loop(self):
        """Main monitoring loop"""
        logger.info("Starting auto-rename monitor")
        logger.info(f"Monitoring for completed downloads every 30 seconds...")
        _, api_key = self._radarr_settings()
        if self._radarr_monitoring_enabled() and api_key:
            logger.info(f"📺 Radarr category monitoring enabled (category: {self._radarr_qbit_category()})")

        while True:
            try:
                # Check manually tracked downloads
                self.check_downloads()
                # Check Radarr category downloads
                self.check_radarr_downloads()
            except Exception as e:
                logger.error(f"❌ Monitor loop error: {e}")

            # Check every 30 seconds
            time.sleep(30)

# Singleton instance
auto_renamer = AutoRenamer()

def start_monitor_thread():
    """Start the monitoring thread"""
    monitor_thread = threading.Thread(target=auto_renamer.monitor_loop, daemon=True)
    monitor_thread.start()
    logger.info("Auto-rename monitor thread started")