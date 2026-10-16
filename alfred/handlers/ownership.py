"""Ownership check handler — fast Plex library lookup, no LLM needed."""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree

import requests

from config import PLEX_URL, PLEX_TOKEN
from handlers.title_match import best_match

TMDB_API_KEY = os.getenv('TMDB_API_KEY', '')
FLASK_BASE = 'http://localhost:5001'

logger = logging.getLogger(__name__)

# Strip common filler at the start: "the movie X", "the show X"
_PREFIX_RE = re.compile(
    r'^(?:the\s+(?:movie|show|film|series|tv\s*show)\s+)?',
    re.I,
)

# Extract and strip quality/format qualifiers at the end
_QUALITY_RE = re.compile(
    r'\s+in\s+(?:4[kK]|uhd|UHD|1080[pP]|720[pP]|[hH][dD]|[hH][dD][rR]'
    r'|dolby\s*vision|atmos|blu[\s-]?ray|full\s*rip)\s*$',
    re.I,
)

# Map spoken quality words to library-name keywords (lowercase)
_QUALITY_KEYWORDS: Dict[str, List[str]] = {
    '4k': ['4k', 'uhd'],
    'uhd': ['4k', 'uhd'],
    '1080p': ['1080', 'hd'],
    '720p': ['720', 'hd'],
    'hd': ['hd', '1080', '720'],
    'hdr': ['hdr', '4k', 'uhd'],
    'dolby vision': ['dolby', '4k', 'uhd'],
    'atmos': ['atmos'],
    'blu-ray': ['blu', 'rip', 'full'],
    'bluray': ['blu', 'rip', 'full'],
    'full rip': ['rip', 'full'],
}


def _extract_quality(text: str) -> Tuple[str, Optional[str]]:
    """Strip quality qualifier from title, return (clean_title, quality_word)."""
    m = _QUALITY_RE.search(text)
    if m:
        quality_raw = m.group(0).strip()
        # Normalize: "in 4K" -> "4k"
        quality = re.sub(r'^in\s+', '', quality_raw, flags=re.I).strip().lower()
        clean = text[:m.start()].strip()
        return clean, quality
    return text, None


def _search_plex(query: str, timeout: int = 8) -> list:
    """Search Plex library by title. Returns list of matching items with library info."""
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
            timeout=timeout,
        )
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        results = []
        for el in list(root.iter('Video')) + list(root.iter('Directory')):
            vtype = el.get('type', '')
            if vtype not in ('movie', 'show', 'episode'):
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
                'library_id': el.get('librarySectionID', ''),
                'library_name': el.get('librarySectionTitle', ''),
                'tmdb_id': tmdb_id,
            })
        return results
    except Exception as e:
        logger.warning('Plex search failed: %s', e)
        return []


def _match_quality_to_library(quality: str, library_name: str) -> bool:
    """Check if a library name matches the requested quality."""
    lib_lower = library_name.lower()
    keywords = _QUALITY_KEYWORDS.get(quality, [quality])
    return any(kw in lib_lower for kw in keywords)


def _resolve_tmdb(query: str, media_type: str = 'multi') -> Tuple[Optional[int], Optional[str]]:
    """Resolve a title to (tmdb_id, media_type). Scores results for best match."""
    if TMDB_API_KEY:
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
                return pick['tmdb_id'], pick['media_type']
        except Exception as e:
            logger.warning('TMDb direct search failed: %s', e)

    # Fallback: use Luma's search endpoint
    try:
        search_type = 'movie' if media_type in ('movie', 'multi') else 'tv'
        resp = requests.get(
            f'{FLASK_BASE}/search',
            params={'q': query, 'type': search_type},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        key = 'movies' if search_type == 'movie' else 'tv_shows'
        candidates = []
        for item in data.get(key, []):
            candidates.append({
                'tmdb_id': item.get('id') or item.get('tmdb_id'),
                'title': item.get('title') or item.get('name', ''),
                'name': item.get('title') or item.get('name', ''),
                'year': (item.get('release_date') or item.get('first_air_date') or '')[:4],
                'release_date': item.get('release_date', ''),
                'media_type': search_type if search_type != 'movie' else 'movie',
            })
        pick = best_match(query, candidates)
        if pick:
            return pick['tmdb_id'], pick['media_type']
    except Exception as e:
        logger.warning('Luma search fallback failed: %s', e)
    return None, None


def _resolve_tmdb_id(query: str, media_type: str = 'multi') -> Optional[int]:
    """Resolve a title to a TMDb ID. Backward-compatible wrapper."""
    tid, _ = _resolve_tmdb(query, media_type)
    return tid


# Map spoken quality words to Radarr quality profile names
_QUALITY_TO_PROFILE: Dict[str, str] = {
    '4k': '2160p Remux',
    'uhd': '2160p Remux',
    'hdr': '2160p Remux',
    'dolby vision': '2160p Remux',
    '1080p': '1080p Remux',
    'hd': '1080p Remux',
    'blu-ray': '1080p Remux',
    'bluray': '1080p Remux',
    'full rip': '1080p Remux',
}


def library_name_to_quality(library_name: str) -> str:
    """Map a Plex library name to its quality tier.

    Convention used in this setup:
      - "Recently Released" / "Movies" (no 4K/rips qualifier) → 1080p encode
      - "Full Rips" / anything with "rip" → 1080p Remux
      - "4K" → 4K Remux
    """
    lib = (library_name or '').lower()
    if '4k' in lib:
        return '4K Remux'
    if 'rip' in lib:
        return '1080p Remux'
    return '1080p'


def _build_modal_action(best: Dict, clean: str) -> Dict[str, Any]:
    """Build an open_modal suggested_ui_action if tmdb_id is available, else show_search_results."""
    tmdb_id = best.get('tmdb_id')
    if not tmdb_id:
        # Try TMDb as fallback
        media_hint = 'tv' if best.get('type') == 'show' else 'movie'
        tmdb_id = _resolve_tmdb_id(best.get('title', clean), media_hint)

    if tmdb_id:
        media_type = 'tv' if best.get('type') == 'show' else 'movie'
        return {
            'action': 'open_modal',
            'media_type': media_type,
            'tmdb_id': tmdb_id,
        }
    return {
        'action': 'show_search_results',
        'query': clean,
    }


_TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/w500'


def _fetch_media_details(tmdb_id: int,
                         media_type: str) -> Tuple[List[str], Optional[str], Optional[str]]:
    """Fetch cast, director/creator, and poster URL for a title in one call.

    Returns (cast_names, director_name, poster_url).
    poster_url is a full public TMDb CDN URL suitable for Telegram sendPhoto.
    """
    detail_endpoint = f'/api/tv/{tmdb_id}' if media_type == 'tv' else f'/api/movie/{tmdb_id}'
    try:
        resp = requests.get(f'{FLASK_BASE}{detail_endpoint}', timeout=10)
        if resp.ok:
            detail = resp.json()
            credits = detail.get('credits', {}) or {}
            cast = [c.get('name', '') for c in (credits.get('cast', []) or [])[:3] if c.get('name')]
            director = None
            if media_type == 'tv':
                creators = detail.get('created_by') or []
                if creators:
                    director = creators[0].get('name')
            else:
                for c in credits.get('crew', []) or []:
                    if c.get('job') == 'Director' and c.get('name'):
                        director = c['name']
                        break
            poster_path = detail.get('poster_path') or ''
            poster_url = f'{_TMDB_IMAGE_BASE}{poster_path}' if poster_path else None
            return cast, director, poster_url
    except Exception:
        pass
    return [], None, None


def _fetch_cast_and_director(tmdb_id: int, media_type: str) -> Tuple[List[str], Optional[str]]:
    cast, director, _ = _fetch_media_details(tmdb_id, media_type)
    return cast, director


def _build_ownership_confirm(candidate: Dict, index: int, candidates: List[Dict],
                              query: str, quality: Optional[str]) -> Dict[str, Any]:
    """Build a pending confirmation response for an ownership candidate."""
    tmdb_id = candidate.get('tmdb_id')
    found_title = candidate.get('title') or query
    year = candidate.get('year', '')
    media_type = candidate.get('media_type', 'movie')

    if tmdb_id:
        cast_names, director, poster_url = _fetch_media_details(tmdb_id, media_type)
    else:
        cast_names, director, poster_url = [], None, None

    title_line = f"{found_title} ({year})" if year else found_title
    director_label = 'created by' if media_type == 'tv' else 'directed by'
    details = []
    if cast_names:
        details.append(f"starring {', '.join(cast_names)}")
    if director:
        details.append(f"{director_label} {director}")

    if index == 0:
        body = title_line + (", " + ", ".join(details) if details else "")
        msg = f"{body}. Is this the one?"
    else:
        body = title_line + (", " + ", ".join(details) if details else "")
        msg = f"How about {body}?"

    return {
        'type': 'ownership',
        'status': 'pending',
        'message': msg,
        'data': {
            'poster_url': poster_url,
            'pending_watchlist': {
                'title': found_title,
                'tmdb_id': tmdb_id,
                'year': year,
                'media_type': media_type,
                'quality': quality,
                'awaiting_ownership_confirm': True,
                'candidates': candidates,
                'candidate_index': index,
                'original_query': query,
            },
        },
    }


def handle_ownership_check(title: str, quality: Optional[str] = None) -> Dict[str, Any]:
    """Resolve title via TMDb and ask the user to confirm before the library lookup.

    Two-step flow: this handler never touches Plex. It returns a pending
    confirmation ("Inception (2010), starring ... directed by ... — is this the
    one?"). On "yes" the router calls handle_ownership_confirmed, which runs the
    actual Plex library check. On "no" the router cycles to the next TMDb
    candidate. This guarantees the user always sees the resolved title first.
    """
    # Clean prefix filler
    clean = _PREFIX_RE.sub('', title).strip().rstrip('?.')

    # Use classifier-provided quality, or try extracting from title as fallback
    if quality:
        quality = quality.lower().strip()
    else:
        clean, quality = _extract_quality(clean)

    if not clean:
        return {
            'type': 'ownership',
            'status': 'ok',
            'message': "I didn't catch the title. What are you looking for?",
            'data': {},
        }

    # Resolve candidates via TMDb FIRST — no Plex lookup until the user confirms
    from handlers.download_action import _resolve_tmdb_multi_all
    candidates = _resolve_tmdb_multi_all(clean)
    if not candidates:
        return {
            'type': 'ownership',
            'status': 'ok',
            'message': (
                f"I couldn't find a movie or show called \"{clean}\". "
                "Could you say the title again?"
            ),
            'data': {'query': clean},
        }

    return _build_ownership_confirm(candidates[0], 0, candidates, clean, quality)


def handle_ownership_confirmed(title: str, tmdb_id: Optional[int] = None,
                                year: Optional[str] = None,
                                media_type: Optional[str] = None,
                                quality: Optional[str] = None) -> Dict[str, Any]:
    """Actual Plex library lookup — only called AFTER the user confirms the title.

    Uses the TMDb-resolved canonical title to query Plex.
    """
    clean = (title or '').strip()
    if not clean:
        return {
            'type': 'ownership',
            'status': 'ok',
            'message': "I didn't catch the title. What are you looking for?",
            'data': {},
        }

    if quality:
        quality = quality.lower().strip() or None

    confirmed_tmdb_id = tmdb_id
    confirmed_media_type = media_type

    results = _search_plex(clean)

    if not results:
        resolved_tmdb_id = confirmed_tmdb_id
        resolved_type = confirmed_media_type
        if not resolved_tmdb_id:
            resolved_tmdb_id, resolved_type = _resolve_tmdb(clean)
        profile = _QUALITY_TO_PROFILE.get(quality, 'TFlix') if quality else 'TFlix'
        quality_label = (quality.upper() if quality and len(quality) <= 4
                         else quality.title() if quality else None)
        suffix = f" in {quality_label}" if quality_label else ""
        return {
            'type': 'ownership',
            'status': 'pending',
            'message': (f"No, {clean} is not in your library. "
                        f"Would you like me to add it to your watchlist{suffix}?"),
            'data': {
                'found': False,
                'query': clean,
                'pending_watchlist': {
                    'title': clean,
                    'tmdb_id': resolved_tmdb_id,
                    'year': year,
                    'media_type': resolved_type,
                    'quality_profile': profile,
                },
            },
        }

    # Filter to title matches (case-insensitive) — strict matching to avoid false positives
    query_lower = clean.lower()
    # 1. Exact match
    title_matches = [r for r in results if r['title'].lower() == query_lower]
    if not title_matches:
        # 2. Exact match ignoring leading "The"
        def strip_the(s: str) -> str:
            return re.sub(r'^the\s+', '', s.lower())
        title_matches = [r for r in results if strip_the(r['title']) == strip_the(clean)]
    if not title_matches:
        # 3. Plex title is fully contained in the query (e.g. query "the dark knight" matches title "Dark Knight")
        title_matches = [r for r in results if strip_the(r['title']) in strip_the(clean)]
    if not title_matches:
        # No good match — treat as not found
        resolved_tmdb_id = confirmed_tmdb_id
        resolved_type = confirmed_media_type
        if not resolved_tmdb_id:
            resolved_tmdb_id, resolved_type = _resolve_tmdb(clean)
        profile = _QUALITY_TO_PROFILE.get(quality, 'TFlix') if quality else 'TFlix'
        quality_label = (quality.upper() if quality and len(quality) <= 4
                         else quality.title() if quality else None)
        suffix = f" in {quality_label}" if quality_label else ""
        return {
            'type': 'ownership',
            'status': 'pending',
            'message': (f"No, {clean} is not in your library. "
                        f"Would you like me to add it to your watchlist{suffix}?"),
            'data': {
                'found': False,
                'query': clean,
                'pending_watchlist': {
                    'title': clean,
                    'tmdb_id': resolved_tmdb_id,
                    'year': year,
                    'media_type': resolved_type,
                    'quality_profile': profile,
                },
            },
        }

    # Collect all library names this title appears in, sorted by priority
    _LIB_PRIORITY = {'recently released': 0, 'movies': 0, 'full rips': 1, '4k': 2}
    libraries = list(dict.fromkeys(r['library_name'] for r in title_matches if r['library_name']))
    libraries.sort(key=lambda name: _LIB_PRIORITY.get(name.lower(), 1))

    # Derive the best quality tier from the highest-priority library found
    best = title_matches[0]
    title = best['title']

    base_data = {
        'found': True,
        'query': clean,
        'title': title,
        'year': best.get('year'),
        'media_type': 'show' if best['type'] == 'show' else 'movie',
        'rating_key': best.get('rating_key'),
        'library': best.get('library_name'),
        'all_libraries': libraries,
    }

    # Best quality across all copies (highest-tier library wins)
    best_quality = library_name_to_quality(
        max(libraries, key=lambda n: {'4k': 2, 'rip': 1}.get(
            next((k for k in ('4k', 'rip') if k in n.lower()), ''), 0
        )) if libraries else (best.get('library_name') or '')
    )

    # If a specific quality was requested, check whether we have it
    if quality:
        quality_matches = [r for r in title_matches
                           if _match_quality_to_library(quality, r['library_name'])]
        if quality_matches:
            quality_label = quality.upper() if len(quality) <= 4 else quality.title()
            return {
                'type': 'ownership',
                'status': 'ok',
                'message': f"Yes, you have {title} in {quality_label}.",
                'data': base_data,
                'suggested_ui_action': _build_modal_action(best, clean),
            }
        else:
            quality_label = quality.upper() if len(quality) <= 4 else quality.title()
            return {
                'type': 'ownership',
                'status': 'ok',
                'message': f"No, you don't have {title} in {quality_label}.",
                'data': base_data,
                'suggested_ui_action': _build_modal_action(best, clean),
            }

    # No specific quality requested — just confirm ownership
    msg = f"Yes, you have {title}."

    return {
        'type': 'ownership',
        'status': 'ok',
        'message': msg,
        'data': base_data,
        'suggested_ui_action': _build_modal_action(best, clean),
    }
