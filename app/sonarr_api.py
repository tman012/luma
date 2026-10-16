#!/usr/bin/env python3
"""
Sonarr API integration for media-search-app
Handles TV show management through Sonarr
"""

import requests
import logging
from typing import Dict, List, Optional
from app.config import Config

logger = logging.getLogger(__name__)

class SonarrAPI:
    """Interface for Sonarr API operations"""

    def __init__(self, url: str = None, api_key: str = None):
        if not url or not api_key:
            try:
                from app.settings_manager import SettingsManager
                sm = SettingsManager()
                url = url or sm.get('sonarr.url') or Config.SONARR_URL
                api_key = api_key or sm.get('sonarr.api_key') or Config.SONARR_API_KEY
            except Exception:
                url = url or Config.SONARR_URL
                api_key = api_key or Config.SONARR_API_KEY
        self.url = url
        self.api_key = api_key
        self.enabled = bool(self.api_key)

        if self.enabled:
            logger.info(f"✅ Sonarr integration enabled: {self.url}")
        else:
            logger.warning("⚠️ Sonarr integration disabled (missing API key)")

    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None, timeout: int = 10) -> Optional[Dict]:
        """Make a request to Sonarr API"""
        if not self.enabled:
            logger.error("Sonarr API key not configured")
            return None

        url = f"{self.url}/api/v3/{endpoint}"
        headers = {"X-Api-Key": self.api_key}

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, params=data, timeout=timeout)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
            elif method == "PUT":
                response = requests.put(url, headers=headers, json=data, timeout=timeout)
            else:
                logger.error(f"Unsupported HTTP method: {method}")
                return None

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Sonarr API request failed: {e}")
            return None

    def search_series(self, term: str) -> List[Dict]:
        """Search for TV series in Sonarr's database.

        Uses a generous timeout: Sonarr's series/lookup performs a live
        TheTVDB metadata fetch that can take well over 10s for shows whose
        metadata isn't cached yet.
        """
        result = self._request("GET", "series/lookup", {"term": term}, timeout=45)
        return result if result else []

    def get_root_folders(self) -> List[Dict]:
        """Get configured root folders"""
        result = self._request("GET", "rootfolder")
        return result if result else []

    def get_quality_profiles(self) -> List[Dict]:
        """Get available quality profiles"""
        result = self._request("GET", "qualityprofile")
        return result if result else []

    def add_series(self, tvdb_id: int, title: str, quality_profile_id: int = 1,
                   root_folder_path: str = None, monitor: str = "all",
                   season_folder: bool = True, search_for_missing: bool = True,
                   monitored: bool = True) -> Optional[Dict]:
        """
        Add a series to Sonarr

        Args:
            tvdb_id: TVDB ID of the series
            title: Title of the series
            quality_profile_id: Quality profile to use (default: 1)
            root_folder_path: Root folder path (default: first available)
            monitor: Monitoring option ('all', 'future', 'missing', 'existing', 'none')
            season_folder: Create season folders
            search_for_missing: Automatically search for missing episodes
            monitored: Series-level monitored flag. Set False to add a fully
                inert series (e.g. just to browse interactive release sources
                without Sonarr ever searching or grabbing).
        """
        if not root_folder_path:
            # Get first root folder
            root_folders = self.get_root_folders()
            if not root_folders:
                logger.error("No root folders configured in Sonarr")
                return None
            root_folder_path = root_folders[0]['path']

        # Search for the series to get full metadata
        search_results = self.search_series(f"tvdb:{tvdb_id}")
        if not search_results:
            logger.error(f"Series not found in Sonarr lookup: tvdb:{tvdb_id}")
            return None

        series_data = search_results[0]

        # Prepare the add request
        add_data = {
            "title": series_data.get("title", title),
            "qualityProfileId": quality_profile_id,
            "tvdbId": tvdb_id,
            "titleSlug": series_data.get("titleSlug"),
            "images": series_data.get("images", []),
            "seasons": series_data.get("seasons", []),
            "rootFolderPath": root_folder_path,
            "seasonFolder": season_folder,
            "monitored": monitored,
            "addOptions": {
                "monitor": monitor,
                "searchForMissingEpisodes": search_for_missing,
                "searchForCutoffUnmetEpisodes": False
            }
        }

        logger.info(f"📺 Adding series to Sonarr: {title}")
        # Adding a series triggers a synchronous metadata import in Sonarr that
        # can exceed the default 10s, so allow more headroom.
        result = self._request("POST", "series", add_data, timeout=45)

        if result:
            logger.info(f"✅ Successfully added series: {title}")
        else:
            logger.error(f"❌ Failed to add series: {title}")

        return result

    def get_series(self, tvdb_id: Optional[int] = None, tmdb_id: Optional[int] = None) -> List[Dict]:
        """Get all series or a specific series by TVDB ID or TMDB ID"""
        series_list = self._request("GET", "series")
        if not series_list:
            return []

        if tvdb_id:
            # First try exact TVDB match
            matches = [s for s in series_list if s.get('tvdbId') == tvdb_id]
            if matches:
                return matches

        if tmdb_id:
            # Try TMDB ID match as fallback
            return [s for s in series_list if s.get('tmdbId') == tmdb_id]

        if tvdb_id:
            # Return empty if TVDB was provided but no match found
            return []

        return series_list

    def series_exists(self, tvdb_id: int, tmdb_id: Optional[int] = None) -> bool:
        """Check if a series already exists in Sonarr by TVDB or TMDB ID"""
        existing = self.get_series(tvdb_id, tmdb_id)
        return len(existing) > 0

    def search_episodes(self, series_id: int) -> bool:
        """Trigger a search for all missing episodes of a series"""
        data = {"name": "SeriesSearch", "seriesId": series_id}
        result = self._request("POST", "command", data)
        return result is not None

    def ensure_season_monitored(self, series_id: int, season_number: int) -> bool:
        """Ensure a specific season AND all its episodes are monitored in Sonarr.
        Sonarr's SeasonSearch only grabs individually-monitored episodes."""
        # 1) Monitor the season on the series object
        series = self._request("GET", f"series/{series_id}")
        if not series:
            return False

        changed = False
        for s in series.get('seasons', []):
            if s.get('seasonNumber') == season_number and not s.get('monitored'):
                s['monitored'] = True
                changed = True

        # The series may have been added unmonitored (for browsing). Sonarr won't
        # grab releases for a monitored season while the series itself is unmonitored,
        # so ensure the series-level flag is enabled too.
        if not series.get('monitored'):
            series['monitored'] = True
            changed = True

        if changed:
            result = self._request("PUT", f"series/{series_id}", series, timeout=15)
            if not result:
                logger.error(f"Failed to monitor season {season_number} for series {series_id}")
                return False
            logger.info(f"Enabled monitoring for season {season_number} on series {series_id}")

        # 2) Monitor all individual episodes in the season
        all_episodes = self._request("GET", "episode", {"seriesId": series_id})
        if all_episodes:
            to_monitor = [
                ep['id'] for ep in all_episodes
                if ep.get('seasonNumber') == season_number and not ep.get('monitored')
            ]
            if to_monitor:
                result = self._request("PUT", "episode/monitor",
                                       {"episodeIds": to_monitor, "monitored": True},
                                       timeout=15)
                if result is not None:
                    logger.info(f"Monitored {len(to_monitor)} episodes in season {season_number}")
                else:
                    logger.error(f"Failed to monitor episodes in season {season_number}")
                    return False

        return True

    def ensure_episode_monitored(self, episode_id: int) -> bool:
        """Ensure a specific episode is monitored in Sonarr. Returns True if successful."""
        ep = self._request("GET", f"episode/{episode_id}")
        if not ep:
            return False

        if not ep.get('monitored'):
            ep['monitored'] = True
            result = self._request("PUT", f"episode/{episode_id}", ep, timeout=15)
            if not result:
                logger.error(f"Failed to monitor episode {episode_id}")
                return False
            logger.info(f"Enabled monitoring for episode {episode_id}")

        # The series may have been added unmonitored (for browsing). Sonarr won't
        # grab future releases for a monitored episode while the series itself is
        # unmonitored, so ensure the series-level flag is enabled too.
        series_id = ep.get('seriesId')
        if series_id:
            self.ensure_series_monitored(series_id)

        return True

    def ensure_series_monitored(self, series_id: int) -> bool:
        """Ensure the series-level monitored flag is True (no change to seasons).

        Used when monitoring an individual season/episode so Sonarr's RSS sync
        will grab releases as they become available; a monitored season on an
        unmonitored series is never grabbed automatically.
        """
        series = self._request("GET", f"series/{series_id}")
        if not series:
            return False
        if series.get('monitored'):
            return True
        series['monitored'] = True
        result = self._request("PUT", f"series/{series_id}", series, timeout=15)
        if not result:
            logger.error(f"Failed to enable series-level monitoring for series {series_id}")
            return False
        logger.info(f"Enabled series-level monitoring for series {series_id}")
        return True

    def monitor_all_seasons(self, series_id: int) -> bool:
        """Set the series and all real (non-special) seasons to monitored.

        Used to promote an inert (browse-only) series into a fully monitored one
        when the user explicitly adds it to the watchlist.
        """
        series = self._request("GET", f"series/{series_id}")
        if not series:
            return False
        series['monitored'] = True
        for s in series.get('seasons', []):
            if s.get('seasonNumber', 0) > 0:
                s['monitored'] = True
        result = self._request("PUT", f"series/{series_id}", series, timeout=15)
        if not result:
            logger.error(f"Failed to monitor all seasons for series {series_id}")
            return False
        logger.info(f"Monitored all seasons for series {series_id}")
        return True

    def rescan_series(self, series_id: int) -> bool:
        """Tell Sonarr to rescan disk for a series (detects deleted/moved files)."""
        result = self._request("POST", "command",
                               {"name": "RescanSeries", "seriesId": series_id},
                               timeout=15)
        if result:
            logger.info(f"RescanSeries triggered for series {series_id}")
        return result is not None

    def search_season_command(self, series_id: int, season_number: int) -> Optional[Dict]:
        """Trigger Sonarr's automatic season search (picks best release by quality profile)."""
        data = {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number}
        return self._request("POST", "command", data, timeout=30)

    def search_episode_command(self, episode_ids: List[int]) -> Optional[Dict]:
        """Trigger Sonarr's automatic episode search (picks best release by quality profile)."""
        data = {"name": "EpisodeSearch", "episodeIds": episode_ids}
        return self._request("POST", "command", data, timeout=30)

    def get_episodes(self, series_id: int) -> List[Dict]:
        """Get every episode Sonarr knows about for a series (TVDB numbering)."""
        return self._request("GET", "episode", {"seriesId": series_id}) or []

    def get_episode_by_series_and_numbers(self, series_id: int, season: int, episode: int) -> Optional[Dict]:
        """Get a specific episode by series ID, season, and episode number"""
        episodes = self._request("GET", "episode", {"seriesId": series_id})
        if not episodes:
            return None

        for ep in episodes:
            if ep.get('seasonNumber') == season and ep.get('episodeNumber') == episode:
                return ep

        return None

    def search_episode_releases(self, episode_id: int) -> List[Dict]:
        """Get available releases for a specific episode (interactive search)"""
        # Use longer timeout for release searches - Sonarr queries all indexers which can take 60-120s
        result = self._request("GET", f"release", {"episodeId": episode_id}, timeout=120)
        return result if result else []

    def search_season_releases(self, series_id: int, season: int, include_all: bool = False) -> List[Dict]:
        """Get available releases for an entire season (interactive search).

        Returns only full-season packs (no individual episodes) and excludes
        rejected releases.  Uses Sonarr's ``fullSeason`` flag which is the
        most reliable indicator.
        """
        logger.info(f"Searching for season {season} releases for series_id={series_id}")

        url = f"{self.url}/api/v3/release"
        headers = {"X-Api-Key": self.api_key}
        params = {
            "seriesId": series_id,
            "seasonNumber": season
        }

        try:
            # Longer timeout - Sonarr queries all indexers which can take 60-120s
            response = requests.get(url, headers=headers, params=params, timeout=120)
            response.raise_for_status()
            all_releases = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to search for season releases: {e}")
            return []

        if not all_releases:
            logger.warning(f"No releases returned from Sonarr for series_id={series_id}, season={season}")
            return []

        if include_all:
            # Interactive browser: everything Sonarr found (packs + single
            # episodes, accepted + rejected); the UI groups and labels them.
            return all_releases

        # Keep only full-season packs, exclude rejected and individual episodes
        season_packs = [
            r for r in all_releases
            if not r.get('rejected', False) and r.get('fullSeason', False)
        ]
        logger.info(f"Sonarr returned {len(all_releases)} releases, {len(season_packs)} are full-season packs")
        return season_packs

    def download_release(self, guid: str, indexer_id: int, series_id: int = None, season: int = None, episode_id: int = None) -> Optional[Dict]:
        """
        Download a specific release.

        Sonarr caches search results temporarily. We need to refresh the cache
        before attempting to grab a release, otherwise it may have expired.

        Args:
            guid: Release GUID
            indexer_id: Indexer ID
            series_id: Sonarr series ID (for season pack downloads)
            season: Season number (for season pack downloads)
            episode_id: Episode ID (for single episode downloads)
        """
        # First, refresh the cache by doing a fresh search
        # This ensures the release is in Sonarr's cache when we try to grab it
        if series_id and season is not None:
            logger.info(f"Refreshing release cache for series {series_id} season {season}")
            url = f"{self.url}/api/v3/release"
            headers = {"X-Api-Key": self.api_key}
            params = {"seriesId": series_id, "seasonNumber": season}
            try:
                response = requests.get(url, headers=headers, params=params, timeout=30)
                response.raise_for_status()
                logger.info(f"Release cache refreshed with {len(response.json())} releases")
            except Exception as e:
                logger.warning(f"Failed to refresh release cache: {e}")
        elif episode_id:
            logger.info(f"Refreshing release cache for episode {episode_id}")
            try:
                self._request("GET", "release", {"episodeId": episode_id}, timeout=30)
            except Exception as e:
                logger.warning(f"Failed to refresh release cache: {e}")

        # Now grab the release
        data = {
            "guid": guid,
            "indexerId": indexer_id
        }
        result = self._request("POST", "release", data)
        return result

    def get_calendar(self, days: int = 14, include_series: bool = True,
                     start: Optional[str] = None, end: Optional[str] = None) -> List[Dict]:
        """Fetch episodes scheduled in a date window.

        Sonarr's GET /api/v3/calendar returns episodes whose airDateUtc falls
        between start and end (inclusive of the day). `includeSeries=true`
        embeds each episode's series record so the widget can read title,
        network, images, and runtime without an extra lookup per row.

        By default the window is today → today + `days`. Callers that need an
        explicit range (e.g. a calendar grid for the current week, which starts
        before today) can pass ISO `start`/`end` dates instead.
        """
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        if not start:
            start = now.date().isoformat()
        if not end:
            end = (now + timedelta(days=max(1, days))).date().isoformat()
        params = {
            "start": start,
            "end": end,
            "includeSeries": "true" if include_series else "false",
            "unmonitored": "false",
        }
        result = self._request("GET", "calendar", params, timeout=15)
        return result if isinstance(result, list) else []

# Singleton instance
sonarr = SonarrAPI()
