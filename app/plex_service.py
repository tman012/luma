#!/usr/bin/env python3
"""
Plex Service Module — API-first access to Plex Media Server.

Replaces direct SQLite database access for owned media operations.
All image paths are returned as relative paths (e.g. /library/metadata/123/thumb/456)
so the frontend can route them through /api/img/plex without ever seeing PLEX_TOKEN.
"""

from app import safe_xml as _safe_xml   # v170 (Reviewer): every Plex XML parse is defused
import re
import time
import threading
import logging
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# get_metadata() is hit on every owned-item modal open (incl. reopening the same
# item); Plex metadata changes rarely, so cache it briefly. Keyed by rating_key.
_metadata_cache: Dict[int, tuple] = {}
_METADATA_CACHE_TTL = 600   # 10 minutes
_METADATA_CACHE_MAX = 500
_metadata_cache_lock = threading.Lock()

# Regex to strip characters illegal in XML 1.0
_ILLEGAL_XML_RE = re.compile(
    '[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f'
    '\ud800-\udfff\ufdd0-\ufdef\ufffe\uffff]'
)


class PlexService:
    """Unified Plex Media Server API client."""

    def __init__(self, plex_url: str = '', plex_token: str = ''):
        # If no explicit args, try to read from settings
        if not plex_url and not plex_token:
            try:
                from app.settings_manager import SettingsManager
                sm = SettingsManager()
                plex_url = sm.get('plex.url', '') or ''
                plex_token = sm.get('plex.token', '') or ''
            except Exception:
                pass
        self.plex_url = plex_url.rstrip('/')
        self.plex_token = plex_token
        self._session = requests.Session()
        self._session.headers.update({
            'Accept': 'application/xml',
            'X-Plex-Token': self.plex_token,
        })

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def update_config(self, plex_url: str, plex_token: str):
        """Update connection details (called when settings change)."""
        self.plex_url = plex_url.rstrip('/')
        self.plex_token = plex_token
        self._session.headers['X-Plex-Token'] = plex_token

    @property
    def is_configured(self) -> bool:
        return bool(self.plex_url and self.plex_token)

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None,
             timeout: int = 15) -> Optional[ET.Element]:
        """GET *path* from Plex and return parsed XML root, or None."""
        if not self.is_configured:
            logger.warning('PlexService not configured (missing URL or token)')
            return None
        url = f'{self.plex_url}{path}'
        try:
            resp = self._session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            # Decode tolerantly first: Plex can return invalid UTF-8 (seen on
            # /library/sections/<id>/all), which must cost a character, not
            # the whole response. Bad bytes become U+FFFD.
            text = resp.content.decode('utf-8', errors='replace')
            text = _ILLEGAL_XML_RE.sub('', text)
            if '\ufffd' in text:
                logger.info('Plex returned invalid UTF-8 for %s; replaced bad bytes', path)
            try:
                return _safe_xml.fromstring(text.encode('utf-8'))
            except ET.ParseError:
                # Plex sometimes emits undeclared namespace prefixes (e.g. at:image)
                # or unescaped ampersands. Sanitize and retry.
                # Strip undeclared namespace-prefixed attributes (e.g. at:image="...")
                # Plex sometimes emits a stray quote before the attr: "at:image="..."
                text = re.sub(r'\s+"?\w+:\w+="[^"]*"', '', text)
                # Escape bare ampersands (not part of a valid XML entity)
                text = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', text)
                return _safe_xml.fromstring(text.encode('utf-8'))
        except requests.RequestException as exc:
            logger.error('Plex API request failed: %s %s — %s', 'GET', path, exc)
            return None
        except ET.ParseError as exc:
            logger.error('Plex XML parse error for %s: %s', path, exc)
            return None

    def _get_raw(self, path: str, params: Optional[dict] = None,
                 timeout: int = 15) -> Optional[requests.Response]:
        """GET *path* and return the raw Response (for image streaming)."""
        if not self.is_configured:
            return None
        url = f'{self.plex_url}{path}'
        try:
            resp = self._session.get(url, params=params, timeout=timeout, stream=True)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.error('Plex raw request failed: %s — %s', path, exc)
            return None

    # ------------------------------------------------------------------
    # Health / discovery
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        """Validate connection and return server info + library listing.

        Returns dict with:
            success (bool)
            server_name (str)
            libraries (list[dict])  — each has id, title, type
            default_movie_section_id (int|None)
        """
        result: Dict[str, Any] = {
            'success': False,
            'server_name': None,
            'libraries': [],
            'default_movie_section_id': None,
        }

        # 1. Validate connection
        root = self._get('/')
        if root is None:
            return result

        result['server_name'] = root.get('friendlyName', 'Unknown')

        # 2. List libraries
        sections_root = self._get('/library/sections')
        if sections_root is None:
            return result

        first_movie_section: Optional[int] = None
        for directory in sections_root.findall('.//Directory'):
            sec_type = directory.get('type', '')
            sec_id = int(directory.get('key', '0'))
            title = directory.get('title', '')

            locations = [
                loc.get('path', '')
                for loc in directory.findall('Location')
                if loc.get('path')
            ]

            result['libraries'].append({
                'id': sec_id,
                'title': title,
                'type': sec_type,           # "movie", "show", "artist", …
                'agent': directory.get('agent', ''),
                'scanner': directory.get('scanner', ''),
                'locations': locations,
            })

            if sec_type == 'movie' and first_movie_section is None:
                first_movie_section = sec_id

        result['default_movie_section_id'] = first_movie_section
        result['success'] = True
        return result

    def get_library_sections(self) -> List[Dict[str, Any]]:
        """Return all library sections."""
        info = self.health_check()
        return info.get('libraries', [])

    def get_sections_lean(self) -> List[Dict[str, Any]]:
        """Return all library sections without the extra ``GET /`` round-trip.

        Unlike :meth:`get_library_sections` (which calls :meth:`health_check`),
        this goes straight to ``/library/sections``.
        """
        root = self._get('/library/sections')
        if root is None:
            return []

        sections: List[Dict[str, Any]] = []
        for directory in root.findall('.//Directory'):
            sections.append({
                'id': int(directory.get('key', '0')),
                'title': directory.get('title', ''),
                'type': directory.get('type', ''),
                'agent': directory.get('agent', ''),
                'scanner': directory.get('scanner', ''),
            })
        return sections

    def get_sections_with_locations(self) -> List[Dict[str, Any]]:
        """Return all library sections with their filesystem locations.

        Goes straight to ``/library/sections`` and parses ``<Location>``
        child elements for each section.  Used by the media-paths sync
        endpoint so the UI can auto-discover Plex library paths.
        """
        root = self._get('/library/sections')
        if root is None:
            return []

        sections: List[Dict[str, Any]] = []
        for directory in root.findall('.//Directory'):
            locations = [
                loc.get('path', '')
                for loc in directory.findall('Location')
                if loc.get('path')
            ]
            sections.append({
                'id': int(directory.get('key', '0')),
                'title': directory.get('title', ''),
                'type': directory.get('type', ''),
                'locations': locations,
            })
        return sections

    # ------------------------------------------------------------------
    # Video XML parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_video(video: ET.Element) -> Dict[str, Any]:
        """Extract a normalised dict from a Plex <Video> element.

        Image paths are *relative* — e.g. ``/library/metadata/123/thumb/456``.
        """
        rating_key = int(video.get('ratingKey', 0))

        # Parse genres from <Genre> child elements
        genres = [g.get('tag', '') for g in video.findall('.//Genre') if g.get('tag')]

        # Parse cast from <Role> child elements
        cast = []
        for role in video.findall('.//Role'):
            actor: Dict[str, Any] = {
                'tag': role.get('tag', ''),
                'role': role.get('role', ''),
                'thumb': role.get('thumb'),   # relative path or None
            }
            # actor_id: Plex uses 'id' attr on Role
            role_id = role.get('id')
            if role_id:
                actor['actor_id'] = int(role_id)
            cast.append(actor)

        # Parse directors from <Director> child elements
        directors = [{'name': d.get('tag', '')} for d in video.findall('.//Director')]

        duration_ms = int(video.get('duration', 0))

        # Library section info (present when fetched via section endpoints)
        lib_section_id_raw = video.get('librarySectionID')
        lib_section_id = int(lib_section_id_raw) if lib_section_id_raw else None

        item: Dict[str, Any] = {
            'ratingKey': rating_key,
            'rating_key': rating_key,       # alias for backward compat
            'title': video.get('title', ''),
            'year': int(video.get('year', 0)) if video.get('year') else None,
            'thumbPath': video.get('thumb'),      # relative path
            'artPath': video.get('art'),          # relative path
            'clearLogoPath': video.get('clearLogo'),  # attribute (legacy agents)

            'summary': video.get('summary', ''),
            'content_rating': video.get('contentRating', ''),
            'contentRating': video.get('contentRating', ''),
            'duration': duration_ms,
            'duration_ms': duration_ms,
            'addedAt': int(video.get('addedAt', 0)),
            'added_at': int(video.get('addedAt', 0)),
            'guid': video.get('guid', ''),
            'rating': float(video.get('rating', 0)) if video.get('rating') else None,
            'audienceRating': float(video.get('audienceRating', 0)) if video.get('audienceRating') else None,
            'genres': genres,
            'cast': cast,
            'directors': directors,
            'type': video.get('type', 'movie'),
            'librarySectionID': lib_section_id,
            'librarySectionTitle': video.get('librarySectionTitle', ''),
            'originallyAvailableAt': video.get('originallyAvailableAt', ''),
            'tagline': video.get('tagline', ''),
            'studio': video.get('studio', ''),
        }

        # Media quality info from first <Media> element
        media_el = video.find('.//Media')
        if media_el is not None:
            item['videoCodec'] = media_el.get('videoCodec', '')
            item['videoResolution'] = media_el.get('videoResolution', '')
            item['container'] = media_el.get('container', '')
            item['bitrate'] = int(media_el.get('bitrate') or 0)
            item['width'] = int(media_el.get('width') or 0)
            item['height'] = int(media_el.get('height') or 0)
        else:
            item['videoCodec'] = ''
            item['videoResolution'] = ''
            item['container'] = ''
            item['bitrate'] = 0
            item['width'] = 0
            item['height'] = 0

        # File path and size from first Media/Part element
        part_el = video.find('.//Part') if media_el is None else media_el.find('.//Part')
        if part_el is None:
            part_el = video.find('.//Part')
        item['filePath'] = part_el.get('file') if part_el is not None else None
        item['fileSize'] = int(part_el.get('size') or 0) if part_el is not None else 0

        item['viewCount'] = int(video.get('viewCount', 0))
        item['lastViewedAt'] = int(video.get('lastViewedAt', 0))
        item['viewOffset'] = int(video.get('viewOffset', 0))
        item['originallyAvailableAt'] = video.get('originallyAvailableAt')  # e.g. "2024-01-15"

        # Episode-specific fields
        if video.get('type') == 'episode':
            item['episode_number'] = int(video.get('index', 0))
            item['season_number'] = int(video.get('parentIndex', 0))
            item['show_title'] = video.get('grandparentTitle', '')
            item['show_rating_key'] = int(video.get('grandparentRatingKey', 0)) if video.get('grandparentRatingKey') else None
            item['show_year'] = int(video.get('year', 0)) if video.get('year') else None
            item['showThumbPath'] = video.get('grandparentThumb')
            item['showArtPath'] = video.get('grandparentArt')

        # Extract external IDs from guid
        guid = item['guid']
        tmdb_match = re.search(r'themoviedb://(\d+)', guid)
        imdb_match = re.search(r'imdb://(tt\d+)', guid)
        item['tmdb_id'] = int(tmdb_match.group(1)) if tmdb_match else None
        item['imdb_id'] = imdb_match.group(1) if imdb_match else None

        # Also check Guid child elements (modern Plex agents)
        if not item['tmdb_id'] or not item['imdb_id']:
            for guid_el in video.findall('.//Guid'):
                guid_id = guid_el.get('id', '')
                if not item['tmdb_id']:
                    m = re.search(r'tmdb://(\d+)', guid_id)
                    if m:
                        item['tmdb_id'] = int(m.group(1))
                if not item['imdb_id']:
                    m = re.search(r'imdb://(tt\d+)', guid_id)
                    if m:
                        item['imdb_id'] = m.group(1)

        # Extract clearLogo from <Image type="clearLogo"> child elements
        # (modern Plex agents store logos here, not as a Video attribute)
        if not item['clearLogoPath']:
            for img_el in video.findall('.//Image'):
                if img_el.get('type') == 'clearLogo':
                    item['clearLogoPath'] = img_el.get('url')
                    break

        # Parse RT scores from <Rating> child elements
        rt_critic = None
        rt_audience = None
        for rating_el in video.findall('.//Rating'):
            img = (rating_el.get('image') or '').lower()
            rtype = rating_el.get('type', '')
            val = rating_el.get('value')
            if not val:
                continue
            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue
            if 'rottentomatoes' in img and rtype == 'critic':
                rt_critic = int(fval * 10)
            elif 'rottentomatoes' in img and rtype == 'audience':
                rt_audience = int(fval * 10)
        item['rtCriticScore'] = rt_critic
        item['rtAudienceScore'] = rt_audience

        return item

    # ------------------------------------------------------------------
    # Cross-library discovery
    # ------------------------------------------------------------------

    def find_movie_copies(self, title: str,
                          tmdb_id: Optional[int] = None,
                          imdb_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Find all copies of a movie across every movie library section.

        Searches by *title*, then filters by TMDB or IMDB ID match so that
        only true duplicates (same movie, different libraries) are returned.
        Each result dict includes ``library_name`` and ``library_section_id``.
        """
        if not tmdb_id and not imdb_id:
            return []

        sections = self.get_sections_lean()
        movie_sections = [s for s in sections if s['type'] == 'movie']

        copies: List[Dict[str, Any]] = []
        seen_keys: set = set()

        for section in movie_sections:
            results = self.search_library(title, section_id=section['id'],
                                          media_type=1, limit=10)
            for item in results:
                rk = item.get('ratingKey') or item.get('rating_key')
                if rk in seen_keys:
                    continue
                if (tmdb_id and item.get('tmdb_id') == tmdb_id) or \
                   (imdb_id and item.get('imdb_id') == imdb_id):
                    item['library_name'] = section['title']
                    item['library_section_id'] = section['id']
                    copies.append(item)
                    seen_keys.add(rk)

        logger.info('find_movie_copies(%r, tmdb=%s, imdb=%s) → %d copies',
                     title, tmdb_id, imdb_id, len(copies))
        return copies

    # ------------------------------------------------------------------
    # Library content endpoints
    # ------------------------------------------------------------------

    def get_recently_added(self, section_id: Optional[int] = None,
                           limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch recently added items. If *section_id* is None fetch globally."""
        if section_id:
            path = f'/library/sections/{section_id}/recentlyAdded'
        else:
            path = '/library/recentlyAdded'

        root = self._get(path, params={
            'X-Plex-Container-Start': 0,
            'X-Plex-Container-Size': limit,
            'includeGuids': 1,
        })
        if root is None:
            return []

        items = [self._parse_video(v) for v in root.findall('.//Video')]
        logger.info('get_recently_added(section=%s) → %d items', section_id, len(items))
        return items

    def get_all_movies(self, section_id: int, sort: str = 'addedAt:desc',
                       limit: int = 50, offset: int = 0,
                       genre: Optional[str] = None,
                       timeout: int = 15) -> Dict[str, Any]:
        """Browse all movies in a section with pagination and sorting.

        Returns dict with keys: movies, total, offset, limit, has_more
        """
        params: Dict[str, Any] = {
            'type': 1,  # movies
            'sort': sort,
            'X-Plex-Container-Start': offset,
            'X-Plex-Container-Size': limit,
            'includeGuids': 1,
        }
        if genre:
            params['genre'] = genre

        root = self._get(f'/library/sections/{section_id}/all', params=params,
                         timeout=timeout)
        if root is None:
            return {'movies': [], 'total': 0, 'offset': offset, 'limit': limit, 'has_more': False}

        total = int(root.get('totalSize', 0))
        movies = [self._parse_video(v) for v in root.findall('.//Video')]

        return {
            'movies': movies,
            'total': total,
            'offset': offset,
            'limit': limit,
            'has_more': (offset + limit) < total,
        }

    def hub_search(self, query: str, limit: int = 10,
                   media_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Cross-library fuzzy search via Plex Hub Search.

        Better for voice queries where titles may be approximate.
        Returns flat list: {type, ratingKey, title, year, thumbPath, key}
        """
        params: Dict[str, Any] = {
            'query': query,
            'limit': limit,
            'includeGuids': 1,
        }
        root = self._get('/hubs/search', params=params, timeout=15)
        if root is None:
            return []

        results: List[Dict[str, Any]] = []
        for hub in root.findall('.//Hub'):
            hub_type = hub.get('type', '')
            # Filter by media type if specified
            if media_type == 'movie' and hub_type not in ('movie',):
                continue
            if media_type == 'show' and hub_type not in ('show',):
                continue

            for el in hub:
                tag_name = el.tag  # Video or Directory
                rk = int(el.get('ratingKey', 0))
                if not rk:
                    continue

                item_type = el.get('type', hub_type)
                entry: Dict[str, Any] = {
                    'type': item_type,
                    'ratingKey': rk,
                    'title': el.get('title', ''),
                    'year': int(el.get('year', 0)) if el.get('year') else None,
                    'thumbPath': el.get('thumb'),
                    'key': el.get('key', ''),
                }

                # Extract external IDs
                guid = el.get('guid', '')
                tmdb_match = re.search(r'themoviedb://(\d+)', guid)
                imdb_match = re.search(r'imdb://(tt\d+)', guid)
                entry['tmdb_id'] = int(tmdb_match.group(1)) if tmdb_match else None
                entry['imdb_id'] = imdb_match.group(1) if imdb_match else None

                # Also check Guid child elements
                if not entry['tmdb_id'] or not entry['imdb_id']:
                    for guid_el in el.findall('.//Guid'):
                        gid = guid_el.get('id', '')
                        if not entry['tmdb_id']:
                            m = re.search(r'tmdb://(\d+)', gid)
                            if m:
                                entry['tmdb_id'] = int(m.group(1))
                        if not entry['imdb_id']:
                            m = re.search(r'imdb://(tt\d+)', gid)
                            if m:
                                entry['imdb_id'] = m.group(1)

                results.append(entry)

        logger.info('hub_search(%r, type=%s) → %d results', query, media_type, len(results))
        return results

    def search_library(self, query: str,
                       section_id: Optional[int] = None,
                       media_type: int = 1,
                       limit: int = 30) -> List[Dict[str, Any]]:
        """Search a library section. media_type: 1=movie, 2=show, 4=episode."""
        if section_id:
            path = f'/library/sections/{section_id}/search'
        else:
            path = '/library/search'

        root = self._get(path, params={
            'type': media_type,
            'query': query,
            'X-Plex-Container-Size': limit,
            'includeGuids': 1,
        })
        if root is None:
            return []

        tag = './/Video' if media_type in (1, 4) else './/Directory'
        results = []
        for el in root.findall(tag):
            if media_type in (1, 4):
                results.append(self._parse_video(el))
            else:
                # TV show Directory elements
                results.append({
                    'ratingKey': int(el.get('ratingKey', 0)),
                    'rating_key': int(el.get('ratingKey', 0)),
                    'title': el.get('title', ''),
                    'year': int(el.get('year', 0)) if el.get('year') else None,
                    'thumbPath': el.get('thumb'),
                    'artPath': el.get('art'),
                    'summary': el.get('summary', ''),
                    'contentRating': el.get('contentRating', ''),
                    'addedAt': int(el.get('addedAt', 0)),
                    'guid': el.get('guid', ''),
                    'type': 'show',
                })

        logger.info('search_library(%r, section=%s, type=%d) → %d results',
                     query, section_id, media_type, len(results))
        return results

    def get_metadata(self, rating_key: int) -> Optional[Dict[str, Any]]:
        """Fetch full metadata for a single item by rating key (10-min TTL cache)."""
        now = time.time()
        with _metadata_cache_lock:
            ent = _metadata_cache.get(rating_key)
            if ent and now - ent[0] < _METADATA_CACHE_TTL:
                return ent[1]
        result = self._get_metadata_uncached(rating_key)
        if result is not None:
            with _metadata_cache_lock:
                _metadata_cache[rating_key] = (time.time(), result)
                if len(_metadata_cache) > _METADATA_CACHE_MAX:
                    oldest = min(_metadata_cache.items(), key=lambda kv: kv[1][0])[0]
                    del _metadata_cache[oldest]
        return result

    def _get_metadata_uncached(self, rating_key: int) -> Optional[Dict[str, Any]]:
        """Fetch full metadata for a single item by rating key."""
        root = self._get(f'/library/metadata/{rating_key}', params={'includeGuids': 1})
        if root is None:
            return None

        video = root.find('.//Video')
        if video is None:
            # Could be a Directory (show)
            directory = root.find('.//Directory')
            if directory is not None:
                item: Dict[str, Any] = {
                    'ratingKey': int(directory.get('ratingKey', 0)),
                    'rating_key': int(directory.get('ratingKey', 0)),
                    'title': directory.get('title', ''),
                    'year': int(directory.get('year', 0)) if directory.get('year') else None,
                    'thumbPath': directory.get('thumb'),
                    'artPath': directory.get('art'),
                    'summary': directory.get('summary', ''),
                    'content_rating': directory.get('contentRating', ''),
                    'contentRating': directory.get('contentRating', ''),
                    'rating': float(directory.get('rating', 0)) if directory.get('rating') else None,
                    'type': 'show',
                    'genres': [g.get('tag', '') for g in directory.findall('.//Genre') if g.get('tag')],
                    'cast': [
                        {'tag': r.get('tag', ''), 'role': r.get('role', ''), 'thumb': r.get('thumb')}
                        for r in directory.findall('.//Role')
                    ],
                    'directors': [{'name': d.get('tag', '')} for d in directory.findall('.//Director')],
                    'audienceRating': float(directory.get('audienceRating', 0)) if directory.get('audienceRating') else None,
                    'originallyAvailableAt': directory.get('originallyAvailableAt', ''),
                    'tagline': directory.get('tagline', ''),
                    'studio': directory.get('studio', ''),
                    'leafCount': int(directory.get('leafCount', 0) or 0),
                    'childCount': int(directory.get('childCount', 0) or 0),
                }
                # Extract external IDs from guid attr
                guid = directory.get('guid', '')
                tmdb_match = re.search(r'themoviedb://(\d+)', guid)
                imdb_match = re.search(r'imdb://(tt\d+)', guid)
                item['tmdb_id'] = int(tmdb_match.group(1)) if tmdb_match else None
                item['imdb_id'] = imdb_match.group(1) if imdb_match else None
                # Also check Guid child elements (modern Plex agents)
                if not item['tmdb_id'] or not item['imdb_id']:
                    for guid_el in directory.findall('.//Guid'):
                        guid_id = guid_el.get('id', '')
                        if not item['tmdb_id']:
                            m = re.search(r'tmdb://(\d+)', guid_id)
                            if m:
                                item['tmdb_id'] = int(m.group(1))
                        if not item['imdb_id']:
                            m = re.search(r'imdb://(tt\d+)', guid_id)
                            if m:
                                item['imdb_id'] = m.group(1)
                # Also look for tvdb ID
                tvdb_id = None
                tvdb_match = re.search(r'thetvdb://(\d+)', guid)
                if tvdb_match:
                    tvdb_id = int(tvdb_match.group(1))
                if not tvdb_id:
                    for guid_el in directory.findall('.//Guid'):
                        guid_id = guid_el.get('id', '')
                        m = re.search(r'tvdb://(\d+)', guid_id)
                        if m:
                            tvdb_id = int(m.group(1))
                            break
                item['tvdb_id'] = tvdb_id
                return item
            return None

        return self._parse_video(video)

    def get_library_stats(self) -> Dict[str, Any]:
        """Get library statistics via API (replaces DB count queries)."""
        stats: Dict[str, Any] = {
            'movies': {},
            'tv_shows': {},
            'total_movies': 0,
            'total_shows': 0,
            'total_episodes': 0,
        }

        sections_root = self._get('/library/sections')
        if sections_root is None:
            return stats

        for directory in sections_root.findall('.//Directory'):
            sec_type = directory.get('type', '')
            sec_id = directory.get('key', '')
            title = directory.get('title', '')

            if sec_type == 'movie':
                # Get count by fetching with container size 0
                count_root = self._get(f'/library/sections/{sec_id}/all', params={
                    'type': 1,
                    'X-Plex-Container-Size': 0,
                })
                count = int(count_root.get('totalSize', 0)) if count_root is not None else 0
                stats['movies'][title] = count
                stats['total_movies'] += count

            elif sec_type == 'show':
                # Get show count
                show_root = self._get(f'/library/sections/{sec_id}/all', params={
                    'type': 2,
                    'X-Plex-Container-Size': 0,
                })
                show_count = int(show_root.get('totalSize', 0)) if show_root is not None else 0
                stats['tv_shows'][title] = {'shows': show_count}
                stats['total_shows'] += show_count

                # Get episode count
                ep_root = self._get(f'/library/sections/{sec_id}/all', params={
                    'type': 4,
                    'X-Plex-Container-Size': 0,
                })
                ep_count = int(ep_root.get('totalSize', 0)) if ep_root is not None else 0
                stats['total_episodes'] += ep_count

        return stats

    def get_on_deck(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Fetch on-deck (continue watching) items via API."""
        root = self._get('/library/onDeck', params={
            'X-Plex-Container-Size': limit,
        })
        if root is None:
            return []

        items = []
        for video in root.findall('.//Video'):
            item = self._parse_video(video)
            # Add progress info
            view_offset = int(video.get('viewOffset', 0))
            duration = int(video.get('duration', 0))
            item['view_offset'] = view_offset
            item['progress_percent'] = int((view_offset / duration) * 100) if duration > 0 else 0
            item['last_viewed_at'] = int(video.get('lastViewedAt', 0))
            items.append(item)

        logger.info('get_on_deck() → %d items', len(items))
        return items

    def get_watch_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch the watch history (newest first) via Plex's history API.

        History rows are much leaner than library metadata — no <Media>, no
        <Guid>, so no tmdb_id. That is fine for our use: every row carries a
        ratingKey (or grandparentRatingKey for episodes), which is all the
        detail modals need to open. Returns raw-ish rows; collapsing episodes
        to their show is the caller's job.
        """
        root = self._get('/status/sessions/history/all', params={
            'sort': 'viewedAt:desc',
            'X-Plex-Container-Start': 0,
            'X-Plex-Container-Size': limit,
        })
        if root is None:
            return []

        rows: List[Dict[str, Any]] = []
        for video in root.findall('.//Video'):
            vtype = video.get('type', '')
            if vtype not in ('movie', 'episode'):
                continue
            rating_key = int(video.get('ratingKey') or 0)
            if not rating_key:
                continue

            row: Dict[str, Any] = {
                'rating_key': rating_key,
                'type': vtype,
                'title': video.get('title', ''),
                'year': int(video.get('year')) if video.get('year') else None,
                'viewed_at': int(video.get('viewedAt') or 0),
                'account_id': int(video.get('accountID')) if video.get('accountID') else None,
                'thumb_path': video.get('thumb'),
            }
            if vtype == 'episode':
                # History rows expose the show as grandparentKey
                # ("/library/metadata/85831") and omit grandparentRatingKey
                # entirely, unlike library metadata. Read the attribute if a
                # future Plex adds it, otherwise dig the id out of the path.
                show_rk = video.get('grandparentRatingKey')
                if not show_rk:
                    m = re.search(r'/(\d+)$', video.get('grandparentKey') or '')
                    show_rk = m.group(1) if m else None
                row['show_rating_key'] = int(show_rk) if show_rk else None
                row['show_title'] = video.get('grandparentTitle', '')
                row['season_number'] = int(video.get('parentIndex') or 0) or None
                row['episode_number'] = int(video.get('index') or 0) or None
                row['show_thumb_path'] = video.get('grandparentThumb')
            rows.append(row)

        logger.info('get_watch_history() → %d rows', len(rows))
        return rows

    def get_titles_lean(self, rating_keys: List[int]) -> Dict[int, Dict[str, Any]]:
        """Map ``rating_key -> {title, year}`` for many items in ONE request.

        Plex accepts a comma-separated key list on /library/metadata, so this is
        a single round-trip regardless of how many keys are asked for. Exists
        because the history API returns no year and the alternative was one
        metadata call per row. Shows come back as <Directory>, movies as
        <Video>, so both are walked.
        """
        keys = [k for k in dict.fromkeys(rating_keys) if k]
        if not keys:
            return {}

        root = self._get('/library/metadata/' + ','.join(str(k) for k in keys))
        if root is None:
            return {}

        out: Dict[int, Dict[str, Any]] = {}
        for el in list(root.findall('.//Video')) + list(root.findall('.//Directory')):
            rk_raw = el.get('ratingKey')
            if not rk_raw:
                continue
            try:
                rk = int(rk_raw)
            except (TypeError, ValueError):
                continue
            out[rk] = {
                'title': el.get('title', ''),
                'year': int(el.get('year')) if el.get('year') else None,
            }
        return out

    def get_all_shows(self, section_id: int, sort: str = 'addedAt:desc',
                      limit: int = 50, offset: int = 0,
                      genre: Optional[str] = None,
                      timeout: int = 15) -> Dict[str, Any]:
        """Browse all TV shows in a section with pagination.

        Returns dict with keys: shows, total, offset, limit, has_more
        """
        params: Dict[str, Any] = {
            'type': 2,  # shows
            'sort': sort,
            'X-Plex-Container-Start': offset,
            'X-Plex-Container-Size': limit,
            'includeGuids': 1,
        }
        if genre:
            params['genre'] = genre

        root = self._get(f'/library/sections/{section_id}/all', params=params,
                         timeout=timeout)
        if root is None:
            return {'shows': [], 'total': 0, 'offset': offset, 'limit': limit, 'has_more': False}

        total = int(root.get('totalSize', 0))
        shows = []
        for directory in root.findall('.//Directory'):
            show: Dict[str, Any] = {
                'ratingKey': int(directory.get('ratingKey', 0)),
                'rating_key': int(directory.get('ratingKey', 0)),
                'title': directory.get('title', ''),
                'year': int(directory.get('year', 0)) if directory.get('year') else None,
                'thumbPath': directory.get('thumb'),
                'artPath': directory.get('art'),
                'summary': directory.get('summary', ''),
                'content_rating': directory.get('contentRating', ''),
                'contentRating': directory.get('contentRating', ''),
                'duration': int(directory.get('duration', 0)),
                'addedAt': int(directory.get('addedAt', 0)),
                'added_at': int(directory.get('addedAt', 0)),
                'guid': directory.get('guid', ''),
                'rating': float(directory.get('rating', 0)) if directory.get('rating') else None,
                'audienceRating': float(directory.get('audienceRating', 0)) if directory.get('audienceRating') else None,
                'viewCount': int(directory.get('viewCount', 0)),
                'lastViewedAt': int(directory.get('lastViewedAt', 0)),
                'type': 'show',
                'genres': [g.get('tag', '') for g in directory.findall('.//Genre') if g.get('tag')],
                'cast': [],
                'directors': [],
            }
            # External IDs
            guid = show['guid']
            tmdb_match = re.search(r'themoviedb://(\d+)', guid)
            imdb_match = re.search(r'imdb://(tt\d+)', guid)
            tvdb_match = re.search(r'thetvdb://(\d+)', guid)
            show['tmdb_id'] = int(tmdb_match.group(1)) if tmdb_match else None
            show['imdb_id'] = imdb_match.group(1) if imdb_match else None
            show['tvdb_id'] = int(tvdb_match.group(1)) if tvdb_match else None
            # A Plex show can carry SEVERAL tvdb://  / tmdb:// guids when its
            # match merges multiple upstream series (e.g. Hard Knocks lists both
            # tvdb://442173 and tvdb://82402). Keeping only the first one meant
            # a lookup by the other id missed the show entirely, so collect all
            # of them — scalars stay as the primary id for existing callers.
            tmdb_ids: List[int] = []
            imdb_ids: List[str] = []
            tvdb_ids: List[int] = []
            for guid_el in directory.findall('.//Guid'):
                guid_id = guid_el.get('id', '')
                m = re.search(r'tmdb://(\d+)', guid_id)
                if m and int(m.group(1)) not in tmdb_ids:
                    tmdb_ids.append(int(m.group(1)))
                m = re.search(r'imdb://(tt\d+)', guid_id)
                if m and m.group(1) not in imdb_ids:
                    imdb_ids.append(m.group(1))
                m = re.search(r'tvdb://(\d+)', guid_id)
                if m and int(m.group(1)) not in tvdb_ids:
                    tvdb_ids.append(int(m.group(1)))
            if show['tmdb_id'] and show['tmdb_id'] not in tmdb_ids:
                tmdb_ids.insert(0, show['tmdb_id'])
            if show['imdb_id'] and show['imdb_id'] not in imdb_ids:
                imdb_ids.insert(0, show['imdb_id'])
            if show['tvdb_id'] and show['tvdb_id'] not in tvdb_ids:
                tvdb_ids.insert(0, show['tvdb_id'])
            show['tmdb_id'] = show['tmdb_id'] or (tmdb_ids[0] if tmdb_ids else None)
            show['imdb_id'] = show['imdb_id'] or (imdb_ids[0] if imdb_ids else None)
            show['tvdb_id'] = show['tvdb_id'] or (tvdb_ids[0] if tvdb_ids else None)
            show['tmdb_ids'] = tmdb_ids
            show['imdb_ids'] = imdb_ids
            show['tvdb_ids'] = tvdb_ids
            shows.append(show)

        return {
            'shows': shows,
            'total': total,
            'offset': offset,
            'limit': limit,
            'has_more': (offset + limit) < total,
        }

    def get_show_children(self, rating_key: int) -> List[Dict[str, Any]]:
        """Fetch season children for a TV show by its rating key.

        Calls ``GET /library/metadata/{ratingKey}/children`` which returns
        ``<Directory>`` elements for each season.

        Returns list of dicts with:
            ratingKey, title, index (season number), thumbPath,
            leafCount, viewedLeafCount
        """
        root = self._get(f'/library/metadata/{rating_key}/children')
        if root is None:
            return []

        seasons: List[Dict[str, Any]] = []
        for directory in root.findall('.//Directory'):
            index_val = directory.get('index')
            if index_val is None:
                continue
            seasons.append({
                'ratingKey': int(directory.get('ratingKey', 0)),
                'title': directory.get('title', ''),
                'index': int(index_val),
                'thumbPath': directory.get('thumb'),
                'leafCount': int(directory.get('leafCount', 0)),
                'viewedLeafCount': int(directory.get('viewedLeafCount', 0)),
            })

        logger.info('get_show_children(%d) → %d seasons', rating_key, len(seasons))
        return seasons

    def get_genres(self, section_id: int) -> List[str]:
        """Get all genre tags for a library section."""
        root = self._get(f'/library/sections/{section_id}/genre')
        if root is None:
            return []

        genres = []
        for directory in root.findall('.//Directory'):
            tag = directory.get('title', '')
            if tag:
                genres.append(tag)

        return sorted(genres)

    # ------------------------------------------------------------------
    # Ownership index (replaces CIFS/SQLite queries)
    # ------------------------------------------------------------------

    _ownership_index: Optional[Dict[str, Any]] = None
    _ownership_index_ts: float = 0
    _episodes_cache: Dict[int, tuple] = {}   # show_rating_key → (ts, episodes)
    _EPISODES_TTL = 60
    _EPISODES_CACHE_MAX = 200
    _OWNERSHIP_TTL: int = 300  # 5 minutes

    def build_ownership_index(self, force: bool = False) -> Dict[str, Any]:
        """Build an in-memory ownership index from Plex HTTP API.

        Returns dict with keys:
            movies: {title_lower|year: {rating_key, title, year, library_name}, ...}
            movies_by_title: {title_lower: [{...}], ...}
            tv: {title_lower|year: {rating_key, title, year, library_name, tmdb_id, tvdb_id}, ...}
            tv_by_title: {title_lower: [{...}], ...}
            tv_by_tmdb: {tmdb_id: {...}, ...}
            tv_by_tvdb: {tvdb_id: {...}, ...}
        """
        now = time.time()
        if not force and self._ownership_index and (now - self._ownership_index_ts) < self._OWNERSHIP_TTL:
            return self._ownership_index

        logger.info('Building ownership index from Plex HTTP API...')
        start = time.time()

        sections = self.get_sections_lean()
        movie_sections = [s for s in sections if s['type'] == 'movie']
        tv_sections = [s for s in sections if s['type'] == 'show']

        movies: Dict[str, Any] = {}          # title_lower|year -> data
        movies_by_title: Dict[str, list] = {}  # title_lower -> [data, ...]
        movies_by_tmdb: Dict[int, Any] = {}    # tmdb_id -> data
        tv: Dict[str, Any] = {}              # title_lower|year -> data
        tv_by_title: Dict[str, list] = {}
        tv_by_tmdb: Dict[int, Any] = {}
        tv_by_tvdb: Dict[int, Any] = {}

        # --- Movies ---
        for section in movie_sections:
            offset = 0
            page_size = 200
            while True:
                result = self.get_all_movies(section['id'], limit=page_size,
                                             offset=offset, timeout=30)
                for item in result.get('movies', []):
                    title = item.get('title', '')
                    year = item.get('year')
                    rk = item.get('ratingKey') or item.get('rating_key', 0)
                    if not title:
                        continue
                    entry = {
                        'rating_key': rk,
                        'title': title,
                        'year': year,
                        'library_name': section['title'],
                        'library_section_id': section['id'],
                        'tmdb_id': item.get('tmdb_id'),
                        'imdb_id': item.get('imdb_id'),
                        'rating': item.get('rating'),
                    }
                    key = f"{title.lower()}|{year}"
                    if key not in movies:
                        movies[key] = entry
                    tl = title.lower()
                    movies_by_title.setdefault(tl, []).append(entry)
                    if item.get('tmdb_id'):
                        # First-wins, consistent with the title|year map above, so
                        # a title present in two libraries resolves to the same
                        # rating_key/library regardless of which match path hits.
                        tmdb_key = int(item['tmdb_id'])
                        if tmdb_key not in movies_by_tmdb:
                            movies_by_tmdb[tmdb_key] = entry

                if not result.get('has_more', False):
                    break
                offset += page_size

        # --- TV Shows ---
        for section in tv_sections:
            offset = 0
            page_size = 200
            while True:
                result = self.get_all_shows(section['id'], limit=page_size,
                                            offset=offset, timeout=30)
                for item in result.get('shows', []):
                    title = item.get('title', '')
                    year = item.get('year')
                    rk = item.get('ratingKey') or item.get('rating_key', 0)
                    if not title:
                        continue

                    # Use IDs already extracted by get_all_shows() (includes Guid child elements)
                    tvdb_id = item.get('tvdb_id')
                    tmdb_id = item.get('tmdb_id')

                    entry = {
                        'rating_key': rk,
                        'title': title,
                        'year': year,
                        'library_name': section['title'],
                        'library_section_id': section['id'],
                        'tmdb_id': tmdb_id,
                        'tvdb_id': tvdb_id,
                        'imdb_id': item.get('imdb_id'),
                    }
                    key = f"{title.lower()}|{year}"
                    if key not in tv:
                        tv[key] = entry
                    tl = title.lower()
                    tv_by_title.setdefault(tl, []).append(entry)
                    # First-wins, consistent with the title|year map above.
                    # Index EVERY guid the show carries, not just the primary —
                    # a merged Plex match exposes several tvdb/tmdb ids and
                    # callers may arrive with any of them (TMDb external_ids
                    # hands the frontend one, Plex's own guid order another).
                    for mid in (item.get('tmdb_ids') or ([tmdb_id] if tmdb_id else [])):
                        if mid not in tv_by_tmdb:
                            tv_by_tmdb[mid] = entry
                    for vid in (item.get('tvdb_ids') or ([tvdb_id] if tvdb_id else [])):
                        if vid not in tv_by_tvdb:
                            tv_by_tvdb[vid] = entry

                if not result.get('has_more', False):
                    break
                offset += page_size

        index = {
            'movies': movies,
            'movies_by_title': movies_by_title,
            'movies_by_tmdb': movies_by_tmdb,
            'tv': tv,
            'tv_by_title': tv_by_title,
            'tv_by_tmdb': tv_by_tmdb,
            'tv_by_tvdb': tv_by_tvdb,
        }
        self._ownership_index = index
        self._ownership_index_ts = time.time()
        elapsed = time.time() - start
        logger.info('Ownership index built in %.2fs: %d movies, %d TV shows',
                     elapsed, len(movies), len(tv))
        return index

    # ------------------------------------------------------------------
    # TV show episode listing (replaces plex_db.get_tv_episodes_by_tvdb)
    # ------------------------------------------------------------------

    def get_show_episodes(self, show_rating_key: int) -> List[Dict[str, Any]]:
        """Fetch all episodes for a TV show via HTTP API.

        Calls get_show_children() to list seasons, then fetches episode
        children for ALL seasons in parallel (threaded) for speed.

        Returns flat list of episode dicts with:
            rating_key, title, episode_number, season_number,
            show_rating_key, duration, viewOffset
        """
        import threading

        # The season grid and the ownership check both need this and fire
        # together on modal open, so a short TTL keeps a 38-season show from
        # paying for two full fan-outs. Well under the 5-minute ownership TTL,
        # so a freshly imported episode still surfaces just as quickly.
        cached = self._episodes_cache.get(show_rating_key)
        if cached and (time.time() - cached[0]) < self._EPISODES_TTL:
            return cached[1]

        seasons = self.get_show_children(show_rating_key)

        # Fetch episodes for each season in parallel
        results_by_season: Dict[int, List[Dict[str, Any]]] = {}
        lock = threading.Lock()

        def fetch_season_episodes(season_rk: int, season_num: int):
            eps = self._fetch_season_episodes(show_rating_key, season_rk, season_num)
            with lock:
                results_by_season[season_num] = eps

        threads = []
        for season in seasons:
            season_rk = season['ratingKey']
            season_num = season['index']
            t = threading.Thread(target=fetch_season_episodes,
                                 args=(season_rk, season_num), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=30)

        # Flatten results in season order
        episodes: List[Dict[str, Any]] = []
        for season_num in sorted(results_by_season.keys()):
            episodes.extend(results_by_season[season_num])

        logger.info('get_show_episodes(%d) → %d episodes across %d seasons',
                     show_rating_key, len(episodes), len(seasons))
        self._episodes_cache[show_rating_key] = (time.time(), episodes)
        if len(self._episodes_cache) > self._EPISODES_CACHE_MAX:
            oldest = min(self._episodes_cache.items(), key=lambda kv: kv[1][0])[0]
            del self._episodes_cache[oldest]
        return episodes

    def _fetch_season_episodes(self, show_rating_key: int,
                                season_rk: int, season_num: int) -> List[Dict[str, Any]]:
        """Fetch episodes for a single season (used by get_show_episodes)."""
        episodes: List[Dict[str, Any]] = []
        root = self._get(f'/library/metadata/{season_rk}/children',
                         params={'includeGuids': 1})
        if root is None:
            return episodes

        for video in root.findall('.//Video'):
            ep_rk = int(video.get('ratingKey', 0))
            ep_index = int(video.get('index', 0))
            duration_ms = int(video.get('duration', 0))
            episodes.append({
                'rating_key': ep_rk,
                'title': video.get('title', ''),
                'episode_number': ep_index,
                'season_number': season_num,
                'show_rating_key': show_rating_key,
                'duration': duration_ms,
                'runtime': round(duration_ms / 60000) if duration_ms else None,
                'viewOffset': int(video.get('viewOffset', 0)),
                'originally_available_at': video.get('originallyAvailableAt'),
                'overview': video.get('summary', ''),
            })

        return episodes

    # ------------------------------------------------------------------
    # Find show copies across libraries
    # ------------------------------------------------------------------

    def find_show_copies(self, title: str,
                         tmdb_id: Optional[int] = None,
                         tvdb_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Find all copies of a TV show across every show library section.

        Searches by *title*, then filters by TMDB or TVDB ID match.
        Each result dict includes ``library_name`` and ``library_section_id``.
        """
        sections = self.get_sections_lean()
        tv_sections = [s for s in sections if s['type'] == 'show']

        copies: List[Dict[str, Any]] = []
        seen_keys: set = set()

        for section in tv_sections:
            results = self.search_library(title, section_id=section['id'],
                                          media_type=2, limit=10)
            for item in results:
                rk = item.get('ratingKey') or item.get('rating_key')
                if rk in seen_keys:
                    continue

                # Try to match by external ID if provided
                matched = False
                if tmdb_id or tvdb_id:
                    # For show Directory results, get full metadata to check IDs
                    meta = self.get_metadata(rk)
                    if meta:
                        if tmdb_id and meta.get('tmdb_id') == tmdb_id:
                            matched = True
                        elif tvdb_id and meta.get('tvdb_id') == tvdb_id:
                            matched = True
                else:
                    # No IDs provided, title match is all we have
                    matched = True

                if matched:
                    item['library_name'] = section['title']
                    item['library_section_id'] = section['id']
                    copies.append(item)
                    seen_keys.add(rk)

        logger.info('find_show_copies(%r, tmdb=%s, tvdb=%s) → %d copies',
                     title, tmdb_id, tvdb_id, len(copies))
        return copies


    def get_related_movies(self, rating_key: int) -> List[Dict[str, Any]]:
        """Return Plex's own Related Movies hub for *rating_key*.

        Hits ``/library/metadata/{rating_key}/related`` and keeps only the
        ``Related Movies`` hub — the same list the Plex app shows under
        Related. Sibling hubs (``More with …``) are ignored on purpose.
        """
        if not rating_key:
            return []
        root = self._get(
            f'/library/metadata/{int(rating_key)}/related',
            params={'includeGuids': 1},
        )
        if root is None:
            return []

        items: List[Dict[str, Any]] = []
        for hub in root.findall('.//Hub'):
            title = (hub.get('title') or '').strip().lower()
            hub_id = (hub.get('hubIdentifier') or '').strip().lower()
            is_related = (
                title == 'related movies'
                or hub_id in ('movie.related', 'movies.related')
                or ('related' in hub_id and 'movie' in hub_id)
            )
            if not is_related:
                continue
            for video in hub.findall('./Video'):
                # Hub children are videos; parse with the shared normaliser so
                # Guid → tmdb_id / imdb_id extraction stays consistent.
                item = self._parse_video(video)
                if item.get('ratingKey') or item.get('rating_key'):
                    items.append(item)
            # Only the first matching Related Movies hub (Plex returns one).
            break

        logger.info(
            'get_related_movies(%s) → %d items', rating_key, len(items)
        )
        return items



# ---------------------------------------------------------------------------
# Module-level singleton (initialised from settings in main.py)
# ---------------------------------------------------------------------------

plex_service = PlexService()
