"""Show media handler — resolve a title to a modal deep link."""

import logging
import os
import re
from typing import Any, Dict, Optional
from xml.etree import ElementTree

import requests

from config import PLEX_URL, PLEX_TOKEN
from handlers.title_match import best_match

logger = logging.getLogger(__name__)

FLASK_BASE = 'http://localhost:5001'
TMDB_API_KEY = os.getenv('TMDB_API_KEY', '')


def _search_plex_with_guids(query: str) -> list:
    """Search Plex and extract tmdb_id from GUID metadata."""
    if not PLEX_URL or not PLEX_TOKEN:
        return []
    try:
        resp = requests.get(
            f'{PLEX_URL}/search',
            params={'query': query, 'includeGuids': 1},
            headers={
                'X-Plex-Token': PLEX_TOKEN,
                'Accept': 'application/xml',
            },
            timeout=8,
        )
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        results = []
        for el in list(root.iter('Video')) + list(root.iter('Directory')):
            vtype = el.get('type', '')
            if vtype not in ('movie', 'show'):
                continue

            # Extract tmdb_id from Guid elements or guid attribute
            tmdb_id = None
            guid_attr = el.get('guid', '')
            tmdb_match = re.search(r'themoviedb://(\d+)', guid_attr)
            if tmdb_match:
                tmdb_id = int(tmdb_match.group(1))
            else:
                for guid_el in el.findall('.//Guid'):
                    gid = guid_el.get('id', '')
                    m = re.search(r'tmdb://(\d+)', gid)
                    if m:
                        tmdb_id = int(m.group(1))
                        break

            results.append({
                'title': el.get('title', ''),
                'year': el.get('year', ''),
                'type': vtype,
                'rating_key': el.get('ratingKey', ''),
                'tmdb_id': tmdb_id,
            })
        return results
    except Exception as e:
        logger.warning('Plex search failed: %s', e)
        return []


def _search_tmdb(query: str, media_type: str = 'multi') -> Optional[Dict]:
    """Search TMDb for a title. Scores results to pick the best match."""
    if not TMDB_API_KEY:
        return None
    try:
        endpoint = {
            'movie': 'search/movie',
            'tv': 'search/tv',
            'multi': 'search/multi',
        }.get(media_type, 'search/multi')

        resp = requests.get(
            f'https://api.themoviedb.org/3/{endpoint}',
            params={'api_key': TMDB_API_KEY, 'query': query},
            timeout=8,
        )
        resp.raise_for_status()
        candidates = []
        for item in resp.json().get('results', []):
            mt = item.get('media_type', media_type)
            if mt == 'person':
                continue
            candidates.append({
                'tmdb_id': item['id'],
                'title': item.get('title') or item.get('name', ''),
                'name': item.get('title') or item.get('name', ''),
                'year': (item.get('release_date') or item.get('first_air_date') or '')[:4],
                'release_date': item.get('release_date', ''),
                'first_air_date': item.get('first_air_date', ''),
                'media_type': 'tv' if mt == 'tv' else 'movie',
                'popularity': item.get('popularity', 0),
                'vote_count': item.get('vote_count', 0),
            })
        pick = best_match(query, candidates)
        if pick:
            return {
                'tmdb_id': pick['tmdb_id'],
                'title': pick['title'],
                'year': pick['year'],
                'media_type': pick['media_type'],
            }
    except Exception as e:
        logger.warning('TMDb search failed: %s', e)
    return None


def handle_show_media(title: str, media_type: Optional[str] = None,
                      season: Optional[int] = None) -> Dict[str, Any]:
    """Resolve a title to tmdbId and return a modal deep-link action.

    Search order: Plex (owned content first) → TMDb (unowned).
    """
    clean = re.sub(r'^(?:the\s+(?:movie|show|film|series)\s+)?', '', title, flags=re.I).strip()
    if not clean:
        return {
            'type': 'show_media',
            'status': 'ok',
            'message': "What would you like me to show you?",
            'data': {},
        }

    # 1. Search Plex first
    plex_results = _search_plex_with_guids(clean)
    query_lower = clean.lower()

    # Prefer exact title match — strict to avoid false positives
    def strip_the(s: str) -> str:
        return re.sub(r'^the\s+', '', s.lower())

    best_plex = None
    # 1. Exact match
    for r in plex_results:
        if r['title'].lower() == query_lower:
            best_plex = r
            break
    # 2. Exact match ignoring leading "The"
    if not best_plex:
        for r in plex_results:
            if strip_the(r['title']) == strip_the(clean):
                best_plex = r
                break
    # 3. Plex title is fully contained in query (e.g. query "the dark knight" matches title "Dark Knight")
    if not best_plex:
        for r in plex_results:
            if strip_the(r['title']) in strip_the(clean):
                best_plex = r
                break

    if best_plex and best_plex.get('tmdb_id'):
        resolved_type = 'tv' if best_plex['type'] == 'show' else 'movie'
        year_str = f" ({best_plex['year']})" if best_plex.get('year') else ''
        season_str = f" Season {season}" if season else ''

        message = f"Here's {best_plex['title']}{year_str}{season_str}."
        action: Dict[str, Any] = {
            'action': 'open_modal',
            'media_type': resolved_type,
            'tmdb_id': best_plex['tmdb_id'],
        }
        if season:
            action['season'] = season

        return {
            'type': 'show_media',
            'status': 'ok',
            'message': message,
            'data': {
                'title': best_plex['title'],
                'tmdb_id': best_plex['tmdb_id'],
                'media_type': resolved_type,
            },
            'suggested_ui_action': action,
        }

    # 2. Fall back to Luma search (movies, TV, and people in one call)
    tmdb_result = _search_tmdb(clean, media_type if media_type in ('movie', 'tv') else 'multi')
    person_result = None

    if not tmdb_result:
        try:
            resp = requests.get(
                f'{FLASK_BASE}/search',
                params={'q': clean, 'type': 'all'},
                timeout=10,
            )
            if resp.ok:
                data = resp.json()

                # Check person first — but only exact name match
                people = data.get('people', [])
                if people:
                    person = people[0]
                    pname = (person.get('name') or '').lower()
                    if pname and (pname == query_lower or strip_the(pname) == strip_the(clean)):
                        person_result = person

                # If no exact person match, check movie/TV
                if not person_result:
                    for key, mt in [('movies', 'movie'), ('tv_shows', 'tv')]:
                        items = data.get(key, [])
                        if items:
                            item = items[0]
                            tmdb_result = {
                                'tmdb_id': item.get('id') or item.get('tmdb_id'),
                                'title': item.get('title') or item.get('name', ''),
                                'year': str(item.get('release_date', '') or item.get('first_air_date', ''))[:4],
                                'media_type': mt,
                            }
                            break
        except Exception as e:
            logger.warning('Luma search fallback failed: %s', e)

    # Person match — show search results in people tab
    if person_result:
        person_name = person_result.get('name', clean)
        person_id = person_result.get('id')
        return {
            'type': 'show_media',
            'status': 'ok',
            'message': f"Here's {person_name}.",
            'data': {'query': clean, 'person': True},
            'suggested_ui_action': {
                'action': 'show_search_results',
                'query': person_name,
                'tab': 'people',
                'person_id': person_id,
            },
        }

    # Movie/TV match — open modal directly
    if tmdb_result:
        resolved_type = tmdb_result['media_type']
        season_str = f" Season {season}" if season else ''

        message = f"Here's {tmdb_result['title']}{season_str}."
        action = {
            'action': 'open_modal',
            'media_type': resolved_type,
            'tmdb_id': tmdb_result['tmdb_id'],
        }
        if season:
            action['season'] = season

        return {
            'type': 'show_media',
            'status': 'ok',
            'message': message,
            'data': {
                'title': tmdb_result['title'],
                'tmdb_id': tmdb_result['tmdb_id'],
                'media_type': resolved_type,
            },
            'suggested_ui_action': action,
        }

    # 4. Nothing found
    return {
        'type': 'show_media',
        'status': 'ok',
        'message': f"Here are the results for {clean}.",
        'data': {'query': clean},
        'suggested_ui_action': {
            'action': 'show_search_results',
            'query': clean,
        },
    }
