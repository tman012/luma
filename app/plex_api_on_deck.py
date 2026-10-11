"""
Plex On Deck using the actual Plex API instead of database queries
"""

import re
import requests
import xml.etree.ElementTree as ET
from app.config import Config
import logging

logger = logging.getLogger(__name__)


def _extract_external_ids(element):
    """Extract tmdb_id, imdb_id, tvdb_id from a Plex XML element's guid attr + Guid children."""
    ids = {'tmdb_id': None, 'imdb_id': None, 'tvdb_id': None}

    # Check top-level guid attribute (e.g. 'com.plexapp.agents.thetvdb://73320?lang=en')
    guid = element.get('guid', '')
    m = re.search(r'themoviedb://(\d+)', guid)
    if m:
        ids['tmdb_id'] = int(m.group(1))
    m = re.search(r'imdb://(tt\d+)', guid)
    if m:
        ids['imdb_id'] = m.group(1)
    m = re.search(r'thetvdb://(\d+)', guid)
    if m:
        ids['tvdb_id'] = int(m.group(1))

    # Check Guid child elements (modern Plex agents)
    for guid_el in element.findall('.//Guid'):
        guid_id = guid_el.get('id', '')
        if not ids['tmdb_id']:
            m = re.search(r'tmdb://(\d+)', guid_id)
            if m:
                ids['tmdb_id'] = int(m.group(1))
        if not ids['imdb_id']:
            m = re.search(r'imdb://(tt\d+)', guid_id)
            if m:
                ids['imdb_id'] = m.group(1)
        if not ids['tvdb_id']:
            m = re.search(r'tvdb://(\d+)', guid_id)
            if m:
                ids['tvdb_id'] = int(m.group(1))

    return ids


def get_plex_api_on_deck(limit=25):
    """
    Get On Deck items directly from Plex API
    """
    try:
        try:
            from app.settings_manager import SettingsManager
            sm = SettingsManager()
            plex_url = sm.get('plex.url') or Config.PLEX_URL
            plex_token = sm.get('plex.token') or Config.PLEX_TOKEN
        except Exception:
            plex_url = Config.PLEX_URL
            plex_token = Config.PLEX_TOKEN
        url = f"{plex_url}/library/onDeck"
        headers = {"X-Plex-Token": plex_token}
        params = {"includeGuids": 1}

        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        # Parse XML response
        root = ET.fromstring(response.content)

        items = []
        for video in root.findall('Video')[:limit]:
            # Get basic info
            rating_key = video.get('ratingKey')
            title = video.get('title')
            video_type = video.get('type')

            # Extract external IDs from this video element
            ext_ids = _extract_external_ids(video)

            # Build item dict
            item = {
                'id': int(rating_key),
                'rating_key': int(rating_key),
                'title': title,
                'type': 'episode' if video_type == 'episode' else 'movie',
                'view_offset': int(video.get('viewOffset', 0)),
                'duration': int(video.get('duration', 0)),
                'last_viewed_at': video.get('lastViewedAt'),
                'year': video.get('year'),
                'content_rating': video.get('contentRating'),
                'summary': video.get('summary'),
                'reason': 'resume' if int(video.get('viewOffset', 0)) > 0 else 'next_up',
                'tmdb_id': ext_ids['tmdb_id'],
                'imdb_id': ext_ids['imdb_id'],
                'tvdb_id': ext_ids['tvdb_id'],
            }

            # Add poster/backdrop URLs (proxied — never expose PLEX_TOKEN)
            item['poster_url'] = f"/api/img/plex?url={video.get('thumb')}" if video.get('thumb') else None
            item['backdrop_url'] = f"/api/img/plex?url={video.get('art')}" if video.get('art') else None

            # Handle episodes
            if video_type == 'episode':
                item['show_title'] = video.get('grandparentTitle')
                item['show_rating_key'] = int(video.get('grandparentRatingKey')) if video.get('grandparentRatingKey') else None
                item['season_number'] = int(video.get('parentIndex')) if video.get('parentIndex') else None
                item['episode_number'] = int(video.get('index')) if video.get('index') else None
                item['episode_title'] = title

                # Preserve episode's own rating key before overwriting with show key
                item['episode_rating_key'] = int(rating_key)

                # Use show poster for episodes (proxied — never expose PLEX_TOKEN)
                if video.get('grandparentThumb'):
                    item['poster_url'] = f"/api/img/plex?url={video.get('grandparentThumb')}"

                # For TV shows, use the show's rating key for poster/art
                item['rating_key'] = int(video.get('grandparentRatingKey')) if video.get('grandparentRatingKey') else int(rating_key)

                # Clear episode-level external IDs — they belong to the episode,
                # not the show, and would cause 404s when used as show IDs.
                item['tmdb_id'] = None
                item['imdb_id'] = None
                item['tvdb_id'] = None

            # Calculate progress
            progress = 0
            if item['view_offset'] and item['duration']:
                progress = round((item['view_offset'] / item['duration']) * 100, 1)
            item['progress'] = progress
            item['progress_percent'] = progress

            # Add metadata type for compatibility
            item['metadata_type'] = 4 if video_type == 'episode' else 1

            items.append(item)

        logger.info(f"Retrieved {len(items)} items from Plex On Deck API")
        return items

    except Exception as e:
        logger.error(f"Error getting Plex API on deck: {e}")
        return []