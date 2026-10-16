#!/usr/bin/env python3
"""
Radarr API Integration
Handles communication with Radarr for movie management
"""

import requests
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class RadarrAPI:
    """Interface for Radarr API"""

    def __init__(self, url: str, api_key: str):
        """
        Initialize Radarr API client

        Args:
            url: Radarr base URL (e.g., http://localhost:7878)
            api_key: Radarr API key
        """
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'X-Api-Key': api_key,
            'Content-Type': 'application/json'
        })

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict]:
        """Make a request to Radarr API"""
        try:
            url = f"{self.url}/api/v3/{endpoint}"
            kwargs.setdefault('timeout', 15)
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json() if response.text else None
        except requests.exceptions.RequestException as e:
            # Log response body on errors for diagnostics
            body = ''
            try:
                body = e.response.text[:500] if hasattr(e, 'response') and e.response is not None else ''
            except Exception:
                pass
            logger.error(f"Radarr API error ({method} {endpoint}): {e} | body: {body}")
            return None

    def test_connection(self) -> bool:
        """Test connection to Radarr"""
        try:
            result = self._make_request('GET', 'system/status')
            if result:
                logger.info(f"Connected to Radarr v{result.get('version', 'unknown')}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to connect to Radarr: {e}")
            return False

    def get_quality_profiles(self) -> List[Dict]:
        """Get available quality profiles"""
        profiles = self._make_request('GET', 'qualityprofile')
        return profiles if profiles else []

    def get_root_folders(self) -> List[Dict]:
        """Get available root folders"""
        folders = self._make_request('GET', 'rootfolder')
        return folders if folders else []

    def search_movie(self, tmdb_id: int) -> Optional[Dict]:
        """
        Search for a movie by TMDB ID

        Args:
            tmdb_id: The Movie Database ID

        Returns:
            Movie data from Radarr lookup or None
        """
        result = self._make_request('GET', f'movie/lookup/tmdb?tmdbId={tmdb_id}')
        return result if result else None

    def get_movie(self, radarr_id: int) -> Optional[Dict]:
        """Get movie by Radarr ID"""
        return self._make_request('GET', f'movie/{radarr_id}')

    def get_movies(self) -> List[Dict]:
        """Get all movies in Radarr"""
        movies = self._make_request('GET', 'movie')
        return movies if movies else []

    def add_movie(self,
                  tmdb_id: int,
                  title: str,
                  year: int,
                  quality_profile_id: int,
                  root_folder_path: str,
                  monitored: bool = True,
                  search_for_movie: bool = True,
                  **kwargs) -> Optional[Dict]:
        """
        Add a movie to Radarr

        Args:
            tmdb_id: TMDB ID of the movie
            title: Movie title
            year: Release year
            quality_profile_id: ID of quality profile to use
            root_folder_path: Root folder path for the movie
            monitored: Whether to monitor the movie
            search_for_movie: Whether to automatically search for the movie
            **kwargs: Additional movie data (titleSlug, images, etc.)

        Returns:
            Added movie data or None on failure
        """
        # First, lookup the movie to get full data
        lookup_result = self.search_movie(tmdb_id)

        if not lookup_result:
            logger.error(f"Could not find movie with TMDB ID {tmdb_id}")
            return None

        # Build the movie data
        movie_data = {
            'title': lookup_result.get('title', title),
            'year': lookup_result.get('year', year),
            'tmdbId': tmdb_id,
            'titleSlug': lookup_result.get('titleSlug', ''),
            'images': lookup_result.get('images', []),
            'qualityProfileId': quality_profile_id,
            'rootFolderPath': root_folder_path,
            'monitored': monitored,
            'minimumAvailability': 'announced',  # Search as soon as announced
            'addOptions': {
                'searchForMovie': search_for_movie
            }
        }

        # Add any additional data from kwargs
        movie_data.update(kwargs)

        # Add the movie
        result = self._make_request('POST', 'movie', json=movie_data)

        if result:
            logger.info(f"Added movie to Radarr: {title} ({year}) - TMDB: {tmdb_id}")
        else:
            logger.error(f"Failed to add movie to Radarr: {title} ({year})")

        return result

    def delete_movie(self, radarr_id: int, delete_files: bool = False) -> bool:
        """
        Delete a movie from Radarr

        Args:
            radarr_id: Radarr movie ID
            delete_files: Whether to delete movie files

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.url}/api/v3/movie/{radarr_id}"
            params = {'deleteFiles': str(delete_files).lower()}
            response = self.session.delete(url, params=params)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error deleting movie {radarr_id}: {e}")
            return False

    def update_movie(self, movie_data: Dict) -> Optional[Dict]:
        """Update a movie in Radarr.

        Sends the full movie object via PUT. Radarr requires the complete
        object (not partial) for updates.
        """
        radarr_id = movie_data.get('id')
        if not radarr_id:
            logger.error("Movie data missing 'id' field")
            return None

        return self._make_request('PUT', f'movie/{radarr_id}', json=movie_data)

    def get_movie_by_tmdb_id(self, tmdb_id: int) -> Optional[Dict]:
        """
        Get a movie from Radarr by TMDB ID.

        Uses the filtered endpoint GET /movie?tmdbId=X for efficiency,
        then falls back to scanning all movies if that returns nothing.

        Args:
            tmdb_id: TMDB ID (will be cast to int)

        Returns:
            Full movie data dict if found in Radarr, None otherwise
        """
        tmdb_id = int(tmdb_id)

        # Try filtered endpoint first (fast, supported in Radarr v3+)
        try:
            url = f"{self.url}/api/v3/movie"
            resp = self.session.get(url, params={'tmdbId': tmdb_id})
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                # Validate the result actually matches — some Radarr versions
                # ignore the tmdbId filter and return all movies
                for movie in data:
                    if movie.get('tmdbId') == tmdb_id:
                        return movie
            elif isinstance(data, dict) and data.get('tmdbId') == tmdb_id:
                return data
        except Exception as e:
            logger.debug(f"Filtered tmdbId lookup failed, falling back to scan: {e}")

        # Fallback: scan all movies
        all_movies = self.get_movies()
        for movie in all_movies:
            if movie.get('tmdbId') == tmdb_id:
                return movie
        return None

    def ensure_movie_monitored(
        self,
        tmdb_id: int,
        quality_profile_id: int,
        title: str = '',
        year: int = 0,
        root_folder_path: str = '',
    ) -> Dict:
        """
        Idempotent "ensure" operation: make sure a movie is in Radarr,
        monitored, with the given quality profile, and a search is triggered.

        Works for BOTH:
          A) Movie not yet in Radarr → add it
          B) Movie already in Radarr → update monitored + qualityProfileId

        Returns a result dict:
          {
            'success': bool,
            'action': 'added' | 'updated',
            'radarr_id': int | None,
            'error': str | None,
            'before': { 'monitored': bool, 'qualityProfileId': int } | None,
            'after': { 'monitored': bool, 'qualityProfileId': int } | None,
            'search_issued': bool,
          }
        """
        tmdb_id = int(tmdb_id)
        result: Dict = {
            'success': False,
            'action': None,
            'radarr_id': None,
            'error': None,
            'before': None,
            'after': None,
            'search_issued': False,
        }

        # ------- Check if movie exists -------
        existing = self.get_movie_by_tmdb_id(tmdb_id)

        if existing:
            radarr_id = existing.get('id')
            result['radarr_id'] = radarr_id
            result['action'] = 'updated'
            result['before'] = {
                'monitored': existing.get('monitored'),
                'qualityProfileId': existing.get('qualityProfileId'),
            }

            logger.info(
                f"ensure_movie_monitored: movie exists in Radarr (id={radarr_id}, "
                f"tmdb={tmdb_id}). Before: monitored={existing.get('monitored')}, "
                f"qualityProfileId={existing.get('qualityProfileId')}"
            )

            # Re-fetch via individual GET to ensure we have the complete object
            full_movie = self.get_movie(radarr_id)
            if not full_movie:
                result['error'] = f'Failed to fetch full movie object from Radarr (id={radarr_id})'
                logger.error(result['error'])
                return result

            # Apply changes
            full_movie['monitored'] = True
            full_movie['qualityProfileId'] = quality_profile_id

            # PUT the updated movie
            updated = self.update_movie(full_movie)
            if not updated:
                result['error'] = 'Radarr rejected the movie update (PUT returned error)'
                logger.error(f"ensure_movie_monitored: PUT failed for radarr_id={radarr_id}")
                return result

            result['after'] = {
                'monitored': updated.get('monitored'),
                'qualityProfileId': updated.get('qualityProfileId'),
            }

            logger.info(
                f"ensure_movie_monitored: updated radarr_id={radarr_id}. "
                f"After: monitored={updated.get('monitored')}, "
                f"qualityProfileId={updated.get('qualityProfileId')}"
            )

            # Verify the update actually applied
            if not updated.get('monitored') or updated.get('qualityProfileId') != quality_profile_id:
                result['error'] = (
                    f"Update appeared to succeed but values don't match: "
                    f"monitored={updated.get('monitored')}, "
                    f"qualityProfileId={updated.get('qualityProfileId')} "
                    f"(expected {quality_profile_id})"
                )
                logger.error(f"ensure_movie_monitored: {result['error']}")
                return result

        else:
            # ------- Movie not in Radarr: add it -------
            result['action'] = 'added'

            if not root_folder_path:
                root_folders = self.get_root_folders()
                if not root_folders:
                    result['error'] = 'No root folders configured in Radarr'
                    return result
                root_folder_path = root_folders[0].get('path', '')

            logger.info(
                f"ensure_movie_monitored: adding new movie to Radarr "
                f"(tmdb={tmdb_id}, title='{title}', profile={quality_profile_id})"
            )

            added = self.add_movie(
                tmdb_id=tmdb_id,
                title=title,
                year=year or datetime.now().year,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder_path,
                monitored=True,
                search_for_movie=True,  # Radarr searches on add
            )

            if not added:
                result['error'] = 'Failed to add movie to Radarr'
                return result

            radarr_id = added.get('id')
            result['radarr_id'] = radarr_id
            result['after'] = {
                'monitored': added.get('monitored'),
                'qualityProfileId': added.get('qualityProfileId'),
            }
            result['search_issued'] = True  # add_movie with searchForMovie=True
            result['success'] = True

            logger.info(f"ensure_movie_monitored: added radarr_id={radarr_id}")
            return result

        # ------- Trigger search for updated movie -------
        search_resp = self.command_search([radarr_id])
        result['search_issued'] = search_resp is not None
        if not search_resp:
            logger.warning(f"ensure_movie_monitored: search command failed for radarr_id={radarr_id}")

        result['success'] = True
        return result

    def command_search(self, movie_ids: List[int]) -> Optional[Dict]:
        """
        Trigger a search for specific movies

        Args:
            movie_ids: List of Radarr movie IDs to search for

        Returns:
            Command response
        """
        command_data = {
            'name': 'MoviesSearch',
            'movieIds': movie_ids
        }
        return self._make_request('POST', 'command', json=command_data)

    def get_queue(self, include_movie: bool = True) -> List[Dict]:
        """
        Get current download queue with movie details.

        The queue contains active downloads. Each item includes a 'downloadId'
        field which is the uppercase torrent hash, useful for matching with
        qBittorrent torrents.

        Args:
            include_movie: Whether to include full movie details in response

        Returns:
            List of queue items with download info and movie metadata
        """
        params = {
            'includeMovie': str(include_movie).lower(),
            'pageSize': 100
        }
        result = self._make_request('GET', 'queue', params=params)
        if result and 'records' in result:
            return result['records']
        return result if isinstance(result, list) else []

    # Radarr v5+/v6 rejects named eventType filters on GET /history
    # ("The value 'grabbed' is not valid.") — it only accepts the numeric
    # MovieHistoryEventType enum. Records still come back with the string name.
    _HISTORY_EVENT_TYPES = {
        'unknown': 0,
        'grabbed': 1,
        'downloadfolderimported': 3,
        'downloadfailed': 4,
        'moviefiledeleted': 6,
        'moviefolderimported': 7,
        'moviefilerenamed': 8,
        'downloadignored': 9,
    }

    @classmethod
    def _history_event_type(cls, event_type) -> int:
        if isinstance(event_type, int):
            return event_type
        raw = str(event_type).strip()
        if raw.isdigit():
            return int(raw)
        try:
            return cls._HISTORY_EVENT_TYPES[raw.lower()]
        except KeyError:
            raise ValueError(f'Unknown Radarr history eventType: {event_type!r}')

    def get_history(self, event_type: str = None, page_size: int = 50) -> List[Dict]:
        """
        Get download history, optionally filtered by event type.

        History includes 'downloadId' (uppercase torrent hash) for matching
        with qBittorrent torrents.

        Args:
            event_type: Filter by event type ('grabbed', 'downloadFolderImported', etc.)
            page_size: Number of records to return

        Returns:
            List of history records with movie metadata
        """
        params = {
            'pageSize': page_size,
            'includeMovie': 'true'
        }
        if event_type is not None and event_type != '':
            params['eventType'] = self._history_event_type(event_type)

        result = self._make_request('GET', 'history', params=params)
        if result and 'records' in result:
            return result['records']
        return result if isinstance(result, list) else []

    def remove_from_queue(self, queue_id: int, remove_from_client: bool = False,
                          blocklist: bool = False) -> bool:
        """
        Remove an item from the download queue.

        Args:
            queue_id: The queue item ID (not the movie ID)
            remove_from_client: Also remove from download client (qBittorrent)
            blocklist: Add the release to blocklist

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.url}/api/v3/queue/{queue_id}"
            params = {
                'removeFromClient': str(remove_from_client).lower(),
                'blocklist': str(blocklist).lower()
            }
            response = self.session.delete(url, params=params)
            response.raise_for_status()
            logger.info(f"Removed queue item {queue_id} from Radarr")
            return True
        except Exception as e:
            logger.error(f"Error removing queue item {queue_id}: {e}")
            return False

    def unmonitor_movie(self, radarr_id: int) -> bool:
        """
        Unmonitor a movie in Radarr (stop tracking for downloads).

        Args:
            radarr_id: The Radarr movie ID

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get current movie data
            movie = self.get_movie(radarr_id)
            if not movie:
                logger.error(f"Could not find movie with Radarr ID {radarr_id}")
                return False

            # Update monitored status
            movie['monitored'] = False
            result = self.update_movie(movie)

            if result:
                logger.info(f"Unmonitored movie in Radarr: {movie.get('title')} (ID: {radarr_id})")
                return True
            return False
        except Exception as e:
            logger.error(f"Error unmonitoring movie {radarr_id}: {e}")
            return False

    def search_movie_releases(self, movie_id: int) -> List[Dict]:
        """
        Interactive release search for a movie. Queries all configured
        indexers in Radarr and returns available releases.

        Args:
            movie_id: Radarr internal movie ID

        Returns:
            List of release dicts from Radarr
        """
        logger.info(f"Searching releases for Radarr movie_id={movie_id}")
        try:
            url = f"{self.url}/api/v3/release"
            response = self.session.get(
                url, params={'movieId': movie_id}, timeout=120
            )
            response.raise_for_status()
            releases = response.json()
            logger.info(f"Radarr returned {len(releases)} releases for movie_id={movie_id}")
            return releases if isinstance(releases, list) else []
        except requests.exceptions.Timeout:
            logger.error(f"Release search timed out for movie_id={movie_id}")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Release search failed for movie_id={movie_id}: {e}")
            return []

    def grab_release(self, guid: str, indexer_id, movie_id: int = None) -> Dict:
        """
        Grab a specific release via Radarr. Refreshes release cache first
        to avoid stale-guid errors.

        Args:
            guid: Release GUID from the search results
            indexer_id: Indexer ID from the search results (string or int)
            movie_id: Radarr movie ID (used to refresh cache)

        Returns:
            Dict with 'ok' bool and either the Radarr response or 'error' message
        """
        if movie_id:
            logger.info(f"Refreshing release cache for movie_id={movie_id}")
            try:
                url = f"{self.url}/api/v3/release"
                self.session.get(
                    url, params={'movieId': movie_id}, timeout=30
                )
            except Exception as e:
                logger.warning(f"Failed to refresh release cache: {e}")

        data = {"guid": guid, "indexerId": indexer_id}
        try:
            url = f"{self.url}/api/v3/release"
            response = self.session.post(url, json=data, timeout=30)
            response.raise_for_status()
            return {"ok": True, "data": response.json() if response.text else {}}
        except requests.exceptions.RequestException as e:
            error_msg = "Unknown error"
            try:
                if hasattr(e, 'response') and e.response is not None:
                    body = e.response.json()
                    error_msg = body.get('message', str(e))
                else:
                    error_msg = str(e)
            except Exception:
                error_msg = str(e)
            logger.error(f"Radarr grab failed: {error_msg}")
            return {"ok": False, "error": error_msg}

    def push_release(self, release: Dict) -> Dict:
        """
        Push a release directly into Radarr, bypassing its release cache.
        Used when the release came from an indexer aggregator Radarr itself
        doesn't talk to (e.g. our Prowlarr vs. Radarr's Jackett).

        Radarr will still apply quality-profile filtering — if the release is
        rejected ("not wanted in profile", "too few seeders", etc.) the
        response includes `rejections` and no grab happens.

        Required fields:
            title         – release torrent name
            downloadUrl   – direct torrent/magnet URL
            protocol      – "torrent" or "usenet" (we always send torrent)
            publishDate   – ISO 8601 timestamp
            indexer       – indexer display name (e.g. "PrivateHD")

        Optional:
            size, infoHash, indexerId

        Returns: {ok: bool, data?: response, error?: message, rejections?: [...]}
        """
        try:
            url = f"{self.url}/api/v3/release/push"
            response = self.session.post(url, json=release, timeout=60)
            response.raise_for_status()
            body = response.json() if response.text else {}
            # Radarr's push endpoint returns a list of release dicts, each
            # potentially carrying rejection reasons. If anything is rejected,
            # surface that to the caller so we can fall back or report.
            if isinstance(body, list):
                accepted = [r for r in body if not r.get('rejected')]
                rejected = [r for r in body if r.get('rejected')]
                if accepted:
                    return {'ok': True, 'data': accepted, 'rejected': rejected}
                rejections = []
                for r in rejected:
                    rejections.extend(r.get('rejections') or [])
                return {'ok': False, 'rejections': rejections,
                        'error': '; '.join(rejections) or 'All releases rejected by Radarr'}
            return {'ok': True, 'data': body}
        except requests.exceptions.RequestException as e:
            error_msg = "Unknown error"
            try:
                if hasattr(e, 'response') and e.response is not None:
                    body = e.response.json()
                    error_msg = body.get('message', str(e))
                else:
                    error_msg = str(e)
            except Exception:
                error_msg = str(e)
            logger.error(f"Radarr push_release failed: {error_msg}")
            return {'ok': False, 'error': error_msg}

    #: Reported instead of "grabbed" when Radarr takes a release but never
    #: fetches it. Phrased for the automation logs the user actually reads.
    PARKED_REASON = ('Radarr accepted the release but parked it as pending '
                     'instead of downloading it')
    #: Same class of failure as PARKED_REASON, but the queue is empty rather
    #: than stuck in delay — e.g. download client rejected the torrent after
    #: Radarr logged a grab. Without this, automations leave monitored-empty
    #: Radarr movies that pollute the Luma Watchlist rail.
    NO_DOWNLOAD_REASON = (
        'Radarr accepted the release but no download appeared in the queue')

    def push_release_verified(self, release: Dict,
                              movie_id: Optional[int]) -> Dict:
        """push_release() that only reports success when a download started.

        Every automation that pushes a release and then tells the user it
        "grabbed" something must go through here rather than push_release()
        directly — see parked_release() for why an approved push is not yet a
        download. A parked release is demoted to an ordinary rejection so the
        caller's existing failure handling (undo the Radarr add, record the
        skip, log the reason) runs unchanged.

        Success is queue/hasFile OR a fresh history "grabbed" event. Queue
        lag used to outrun a 5s poll window: Radarr had already handed the
        torrent to qBit, verification returned NO_DOWNLOAD_REASON, callers
        deleted the movie and retried another release — double downloads.
        History closes that race; a longer wait covers slow clients.

        Falls back to the raw push result when *movie_id* is unknown; that is
        no worse than the old behaviour.
        """
        import time as _time

        result = self.push_release(release)
        if not result.get('ok') or not movie_id:
            return result

        # Queue / client handoff can lag well past a couple of seconds —
        # especially under load — so poll longer and accept a fresh history
        # grab as proof the client was told to fetch.
        saw_history_grab = False
        for attempt in range(15):
            if attempt:
                _time.sleep(1.5)
            if self.parked_release(movie_id):
                return {'ok': False, 'parked': True,
                        'rejections': [self.PARKED_REASON]}
            if self._download_started(movie_id):
                return result
            if self._recent_grab_history(movie_id, within_seconds=180):
                saw_history_grab = True
                # History says grabbed — give the queue a bit more time to
                # catch up, but do not fail the push (that caused double grabs).
                if attempt >= 8:
                    logger.info(
                        "Radarr movie %s has fresh grab history but queue still "
                        "empty after %.1fs — treating push as success",
                        movie_id, attempt * 1.5,
                    )
                    return result

        if saw_history_grab:
            logger.info(
                "Radarr movie %s: grab history present after full wait — "
                "treating push as success", movie_id,
            )
            return result

        return {'ok': False, 'rejections': [self.NO_DOWNLOAD_REASON]}

    def _recent_grab_history(self, movie_id: int,
                             within_seconds: int = 180) -> bool:
        """True when Radarr logged a grab for *movie_id* in the last N seconds."""
        from datetime import datetime, timezone, timedelta
        try:
            records = self.get_history(event_type='grabbed', page_size=50) or []
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=within_seconds)
            for rec in records:
                if rec.get('movieId') != movie_id:
                    # history may embed movie instead of movieId
                    movie = rec.get('movie') or {}
                    if movie.get('id') != movie_id:
                        continue
                raw = rec.get('date') or ''
                try:
                    when = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if when >= cutoff:
                    return True
            return False
        except Exception as e:
            logger.warning(f"Radarr grab-history lookup failed: {e}")
            return False

    def movie_download_active(self, movie_id: int) -> bool:
        """True if queue/hasFile OR a very recent grab history event exists.

        Used by callers before undo/retry so a laggy queue cannot trigger a
        second release push for a film that already started downloading.
        """
        if not movie_id:
            return False
        if self._download_started(movie_id):
            return True
        return self._recent_grab_history(movie_id, within_seconds=300)

    def _queue_record(self, movie_id: int) -> Optional[Dict]:
        """First Radarr queue record for *movie_id*, or None."""
        try:
            resp = self.session.get(
                f"{self.url}/api/v3/queue",
                params={'pageSize': 200, 'includeMovie': 'false'},
                timeout=30,
            )
            resp.raise_for_status()
            for record in (resp.json() or {}).get('records', []):
                if record.get('movieId') == movie_id:
                    return record
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Radarr queue lookup failed: {e}")
            return None

    def _download_started(self, movie_id: int) -> bool:
        """True when the movie has an active queue item or already has a file.

        'delay' only counts when a torrent delay profile is configured —
        otherwise that status is a parked PendingRelease (see parked_release).
        """
        try:
            movie = None
            try:
                resp = self.session.get(
                    f"{self.url}/api/v3/movie/{movie_id}", timeout=30)
                resp.raise_for_status()
                movie = resp.json()
            except requests.exceptions.RequestException:
                movie = None
            if movie and movie.get('hasFile'):
                return True
        except Exception as e:
            logger.warning(f"Radarr hasFile check failed for {movie_id}: {e}")

        record = self._queue_record(movie_id)
        if not record:
            return False
        status = (record.get('status') or '').lower()
        if status == 'delay':
            return bool(self._torrent_delay_configured())
        # downloading / queued / paused / completed / warning / failed — any
        # presence means Radarr handed something to the download client.
        return True

    #: (fetched_at, has_delay) — the delay profile changes about never, and
    #: this is only consulted on the already-rare pending path.
    _delay_profile_cache: tuple = (0.0, False)

    def _torrent_delay_configured(self) -> bool:
        """True if any Radarr delay profile holds torrent releases back."""
        import time as _time
        ts, cached = RadarrAPI._delay_profile_cache
        if _time.time() - ts < 300:
            return cached
        has_delay = False
        try:
            resp = self.session.get(f"{self.url}/api/v3/delayprofile", timeout=15)
            resp.raise_for_status()
            has_delay = any(
                p.get('enableTorrent') and (p.get('torrentDelay') or 0) > 0
                for p in (resp.json() or [])
            )
        except requests.exceptions.RequestException as e:
            logger.warning(f"Radarr delay profile lookup failed: {e}")
            return False
        RadarrAPI._delay_profile_cache = (_time.time(), has_delay)
        return has_delay

    def parked_release(self, movie_id: int) -> Optional[Dict]:
        """The queue record if Radarr parked a release for *movie_id* rather
        than grabbing it, else None.

        push_release() answers with Radarr's *decision* — whether the release
        clears the quality profile — and that answer comes back before the
        torrent is actually fetched from the indexer. If the fetch then fails,
        the release drops into PendingReleases and silently never downloads,
        while the push response already said "approved". Anything that reports
        a grab to the user has to check this too, or it reports success for a
        film that will never arrive (see the Prowlarr/Radarr indexer-id
        collision that made every Genre Roulette push land here).
        """
        try:
            resp = self.session.get(
                f"{self.url}/api/v3/queue",
                params={'pageSize': 200, 'includeMovie': 'false'},
                timeout=30,
            )
            resp.raise_for_status()
            for record in (resp.json() or {}).get('records', []):
                if record.get('movieId') != movie_id:
                    continue
                # "delay" is how the queue surfaces a PendingRelease. A real
                # download reports downloading/queued/completed instead.
                if (record.get('status') or '').lower() == 'delay':
                    # A configured torrent delay produces the same "delay"
                    # status for a release that IS going to download, and the
                    # queue doesn't expose which reason applies. Rather than
                    # let callers undo a perfectly good grab, stay quiet when
                    # a delay is configured — we can't tell the two apart.
                    if self._torrent_delay_configured():
                        logger.info(
                            "Radarr movie %s is pending, but a torrent delay is "
                            "configured — not treating it as a failed grab",
                            movie_id,
                        )
                        return None
                    return record
            return None
        except requests.exceptions.RequestException as e:
            # Never fail a grab over the verification call itself.
            logger.warning(f"Radarr parked_release check failed: {e}")
            return None

    def unmonitor_movie_by_tmdb(self, tmdb_id: int) -> bool:
        """
        Unmonitor a movie in Radarr by TMDB ID.

        Args:
            tmdb_id: The TMDB ID

        Returns:
            True if successful, False otherwise
        """
        movie = self.get_movie_by_tmdb_id(tmdb_id)
        if movie:
            return self.unmonitor_movie(movie['id'])
        else:
            logger.warning(f"Movie with TMDB ID {tmdb_id} not found in Radarr")
            return False
