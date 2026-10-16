"""Download action handler — adds movies/shows to Radarr/Sonarr."""

import logging
import os
import re
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

import requests

from config import (RADARR_URL, RADARR_API_KEY, SONARR_URL, SONARR_API_KEY,
                    PLEX_URL, PLEX_TOKEN)
from handlers.title_match import best_match

logger = logging.getLogger(__name__)


def _radarr_post(path: str, data: Dict, timeout: int = 15) -> Optional[Any]:
    if not RADARR_URL or not RADARR_API_KEY:
        return None
    try:
        resp = requests.post(
            f'{RADARR_URL}/api/v3{path}',
            json=data,
            headers={'X-Api-Key': RADARR_API_KEY},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            body = e.response.json() if e.response.headers.get('content-type', '').startswith('application/json') else {}
            # Movie already exists
            if any('already' in str(err).lower() for err in body):
                return {'already_exists': True}
        logger.warning('Radarr POST %s failed: %s', path, e)
        return None
    except Exception as e:
        logger.warning('Radarr POST %s failed: %s', path, e)
        return None


def _radarr_get(path: str, params: Optional[Dict] = None,
                timeout: int = 15) -> Optional[Any]:
    if not RADARR_URL or not RADARR_API_KEY:
        return None
    try:
        resp = requests.get(
            f'{RADARR_URL}/api/v3{path}',
            params=params,
            headers={'X-Api-Key': RADARR_API_KEY},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning('Radarr GET %s failed: %s', path, e)
        return None


def _sonarr_get(path: str, params: Optional[Dict] = None,
                timeout: int = 15) -> Optional[Any]:
    if not SONARR_URL or not SONARR_API_KEY:
        return None
    try:
        resp = requests.get(
            f'{SONARR_URL}/api/v3{path}',
            params=params,
            headers={'X-Api-Key': SONARR_API_KEY},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning('Sonarr GET %s failed: %s', path, e)
        return None


def _sonarr_post(path: str, data: Dict, timeout: int = 15) -> Optional[Any]:
    if not SONARR_URL or not SONARR_API_KEY:
        return None
    try:
        resp = requests.post(
            f'{SONARR_URL}/api/v3{path}',
            json=data,
            headers={'X-Api-Key': SONARR_API_KEY},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            return {'already_exists': True}
        logger.warning('Sonarr POST %s failed: %s', path, e)
        return None
    except Exception as e:
        logger.warning('Sonarr POST %s failed: %s', path, e)
        return None


FLASK_BASE = 'http://localhost:5001'

# ---- Quality mapping: spoken words → Radarr/Sonarr quality profile names ----
_QUALITY_TO_PROFILE: Dict[str, str] = {
    '4k': '2160p Remux',
    'uhd': '2160p Remux',
    'hdr': '2160p Remux',
    'dolby vision': '2160p Remux',
    '2160p': '2160p Remux',
    '1080p': '1080p Remux',
    '1080p remux': '1080p Remux',
    'hd': '1080p Remux',
    'blu-ray': '1080p Remux',
    'bluray': '1080p Remux',
    'full rip': '1080p Remux',
    'remux': '1080p Remux',
}

# ---- Plex library check ----

def _search_plex(query: str, timeout: int = 8) -> List[Dict]:
    """Search Plex library by title. Returns matching items with library info."""
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
            if vtype not in ('movie', 'show'):
                continue
            results.append({
                'title': el.get('title', ''),
                'year': el.get('year', ''),
                'type': vtype,
                'library_name': el.get('librarySectionTitle', ''),
            })
        return results
    except Exception as e:
        logger.warning('Plex search failed: %s', e)
        return []


def _check_plex_ownership(title: str) -> Optional[Dict]:
    """Check if title exists in Plex. Returns match with ALL libraries it appears in."""
    results = _search_plex(title)
    if not results:
        return None
    query_lower = title.lower()
    strip_the = lambda s: re.sub(r'^the\s+', '', s.lower())

    # Find all title-matching results across libraries
    title_matches: List[Dict] = []
    for r in results:
        t = r['title'].lower()
        if (t == query_lower
                or strip_the(r['title']) == strip_the(title)
                or strip_the(r['title']) in strip_the(title)):
            title_matches.append(r)

    if not title_matches:
        return None

    # Collect all unique library names
    libraries = list(dict.fromkeys(r['library_name'] for r in title_matches if r['library_name']))
    best = title_matches[0]
    best['all_libraries'] = libraries
    return best


def _radarr_status_suffix(title: str, tmdb_id: Optional[int]) -> str:
    """Return a human-readable status note for a movie already in Radarr.

    Checks the movie's status and release dates to explain *why* it isn't
    in the library yet (unreleased, awaiting download, etc.).
    Returns an empty string when nothing useful can be determined.
    """
    params = {'tmdbId': tmdb_id} if tmdb_id else {'term': f'tmdb:{tmdb_id}'}
    movies = _radarr_get('/movie', params={'tmdbId': tmdb_id}) if tmdb_id else None
    if not movies:
        # Fall back to lookup endpoint
        lookup = _radarr_get('/movie/lookup', params={'term': title})
        movies = lookup if isinstance(lookup, list) else None

    movie = None
    if isinstance(movies, list):
        for m in movies:
            if tmdb_id and m.get('tmdbId') == tmdb_id:
                movie = m
                break
        if not movie and movies:
            movie = movies[0]
    elif isinstance(movies, dict):
        movie = movies

    if not movie:
        return ''

    status = (movie.get('status') or '').lower()
    has_file = movie.get('hasFile', False)

    if has_file:
        return ''  # it IS in the library — Plex check will have caught this already

    # Collect the soonest future release date
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    release_dates = {}
    for key in ('digitalRelease', 'physicalRelease', 'inCinemas'):
        val = movie.get(key)
        if val:
            try:
                dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
                if dt > now:
                    release_dates[key] = dt
            except ValueError:
                pass

    if status == 'announced':
        if release_dates:
            nearest_key = min(release_dates, key=lambda k: release_dates[k])
            dt = release_dates[nearest_key]
            label = 'in theaters' if nearest_key == 'inCinemas' else 'digitally'
            return f"It hasn't been released yet — expected {label} around {dt.strftime('%B %Y')}. It will download automatically when it becomes available."
        return "It hasn't been released yet and will download automatically once it is."

    if status == 'incinemas':
        if 'digitalRelease' in release_dates:
            dt = release_dates['digitalRelease']
            return f"It's currently in theaters and due digitally around {dt.strftime('%B %Y')}. It will download automatically when available."
        return "It's currently in theaters. It will download automatically when the digital release is available."

    # released but no file — monitored and waiting for an indexer hit
    return "It's on the watchlist and will download as soon as a copy is available."


def _resolve_quality_profile(quality: Optional[str]) -> str:
    """Map spoken quality keyword to a Radarr/Sonarr profile name."""
    if not quality:
        return 'TFlix'
    return _QUALITY_TO_PROFILE.get(quality.lower().strip(), 'TFlix')


def handle_watchlist_add(title: str, tmdb_id: Optional[int] = None,
                         year: Optional[int] = None,
                         quality_profile: str = 'TFlix',
                         media_type: Optional[str] = None,
                         requester_chat_id: Optional[str] = None,
                         season: Optional[int] = None,
                         episode: Optional[int] = None) -> Dict[str, Any]:
    """Add a movie or TV show to the watchlist.

    Routes to /api/watchlist (Radarr) for movies, /api/tv/watchlist (Sonarr) for TV.
    season/episode are forwarded for TV to enable targeted season/episode monitoring.
    If requester_chat_id is set, registers a pending Telegram notification so the
    requester is messaged when the media downloads and appears in Plex.
    """
    is_tv = media_type == 'tv'
    endpoint = f'{FLASK_BASE}/api/tv/watchlist' if is_tv else f'{FLASK_BASE}/api/watchlist'

    payload: Dict[str, Any] = {'title': title, 'quality_profile': quality_profile}
    if tmdb_id:
        payload['tmdb_id'] = tmdb_id
    if year:
        payload['year'] = year
    if not is_tv and not tmdb_id:
        payload['media_type'] = 'movie'
    if is_tv and season is not None:
        payload['season'] = season
    if is_tv and episode is not None:
        payload['episode'] = episode

    # Build a human-readable target string for messages (e.g. "season 5" or "S4E3")
    if is_tv and season is not None:
        _target = f"S{season}E{episode}" if episode is not None else f"season {season}"
    else:
        _target = None

    try:
        resp = requests.post(endpoint, json=payload, timeout=20)
        data = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}

        if resp.ok and data.get('success'):
            already_exists = data.get('already_exists', False)
            action = data.get('action', '')
            # season_added / episode_added on an already-tracked series means we
            # actually did something (monitored + searched) — not a no-op.
            is_real_add = not already_exists or action in ('season_added', 'episode_added')

            if not is_real_add:
                _status_note = '' if _target else _radarr_status_suffix(title, tmdb_id)
                _status_str = f" {_status_note}" if _status_note else ''
                if _target:
                    return {
                        'type': 'watchlist_add', 'status': 'ok',
                        'message': f"{title} {_target} is already on your watchlist.",
                        'data': data,
                    }
                return {
                    'type': 'watchlist_add', 'status': 'ok',
                    'message': f"{title} is already on your watchlist.{_status_str}",
                    'data': data,
                }

            # Something was actually added/monitored — register notification and reply
            if requester_chat_id and tmdb_id:
                _resolved_tmdb_id = data.get('tmdb_id') or tmdb_id
                sonarr_series_id = data.get('sonarr_id') if is_tv else None
                _register_pending_notification(
                    tmdb_id=_resolved_tmdb_id,
                    title=title,
                    media_type='tv' if is_tv else 'movie',
                    telegram_chat_id=requester_chat_id,
                    sonarr_series_id=sonarr_series_id,
                    season=season,
                    episode=episode,
                )
                suffix = "I'll message you when it's ready."
            else:
                suffix = "It will show up in your library once available."
            if _target:
                return {
                    'type': 'watchlist_add', 'status': 'ok',
                    'message': f"I've added {title} {_target} to your watchlist. {suffix}",
                    'data': data,
                }
            return {
                'type': 'watchlist_add',
                'status': 'ok',
                'message': f"I've added {title} to your watchlist. {suffix}",
                'data': data,
            }
        elif resp.status_code == 409 or data.get('already_exists'):
            if _target:
                return {
                    'type': 'watchlist_add', 'status': 'ok',
                    'message': f"{title} {_target} is already on your watchlist.",
                    'data': data,
                }
            _status_note = _radarr_status_suffix(title, tmdb_id)
            _status_str = f" {_status_note}" if _status_note else ''
            return {
                'type': 'watchlist_add',
                'status': 'ok',
                'message': f"{title} is already on your watchlist.{_status_str}",
                'data': data,
            }
        else:
            logger.warning('Watchlist add failed: %s %s', resp.status_code, data)
            return {
                'type': 'watchlist_add',
                'status': 'error',
                'message': f"Sorry, I couldn't add {title} to your watchlist right now.",
                'data': {},
            }
    except Exception as e:
        logger.error('Watchlist add error: %s', e)
        return {
            'type': 'watchlist_add',
            'status': 'error',
            'message': f"Sorry, something went wrong adding {title} to your watchlist.",
            'data': {},
        }


def _register_pending_notification(tmdb_id: int, title: str, media_type: str,
                                    telegram_chat_id: str,
                                    sonarr_series_id: Optional[int] = None,
                                    season: Optional[int] = None,
                                    episode: Optional[int] = None) -> None:
    """Fire-and-forget: tell the backend to notify this Telegram user when media is ready."""
    try:
        payload: Dict[str, Any] = {
            'tmdb_id': tmdb_id,
            'title': title,
            'media_type': media_type,
            'telegram_chat_id': str(telegram_chat_id),
        }
        if sonarr_series_id is not None:
            payload['sonarr_series_id'] = sonarr_series_id
        if season is not None:
            payload['season'] = season
        if episode is not None:
            payload['episode'] = episode
        resp = requests.post(
            f'{FLASK_BASE}/api/pending-request',
            json=payload,
            timeout=5,
        )
        if resp.ok:
            logger.info('Pending notification registered: %s for chat %s', title, telegram_chat_id)
        else:
            logger.warning('Pending notification failed: %s %s', resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning('Pending notification error for %s: %s', title, e)


_NEWEST_QUALIFIER_RE = re.compile(
    r'\b(?:new(?:est)?|latest|most\s+recent|recent|remake|reboot|new\s+version)\b',
    re.I,
)


def _resolve_tmdb_multi_all(title: str, limit: int = 5,
                             year: Optional[int] = None,
                             prefer_newest: bool = False,
                             media_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Search TMDb for a title. Returns up to `limit` candidates sorted by match score.

    `year` is used as a ranking hint — year-matching results are surfaced first —
    but is never appended to the query string (which causes false matches like
    'Enemy 2014' → 'Arch Enemy: [2014] Krasnodar, Russia').
    """
    from handlers.title_match import score_result

    all_candidates: List[Dict[str, Any]] = []
    tmdb_key = os.environ.get('TMDB_API_KEY', '')
    if tmdb_key:
        try:
            resp = requests.get(
                'https://api.themoviedb.org/3/search/multi',
                params={'api_key': tmdb_key, 'query': title},
                timeout=8,
            )
            if resp.ok:
                for item in resp.json().get('results', []):
                    mt = item.get('media_type', '')
                    if mt == 'person':
                        continue
                    all_candidates.append({
                        'tmdb_id': item['id'],
                        'title': item.get('title') or item.get('name', title),
                        'name': item.get('title') or item.get('name', title),
                        'year': (item.get('release_date') or item.get('first_air_date') or '')[:4],
                        'release_date': item.get('release_date', ''),
                        'first_air_date': item.get('first_air_date', ''),
                        'media_type': 'tv' if mt == 'tv' else 'movie',
                        'popularity': item.get('popularity', 0),
                        'vote_count': item.get('vote_count', 0),
                    })
        except Exception as e:
            logger.warning('TMDb multi search failed: %s', e)

    # Fallback: Luma search (only if TMDb returned nothing)
    if not all_candidates:
        try:
            resp = requests.get(
                f'{FLASK_BASE}/search',
                params={'q': title, 'type': 'all'},
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                for m in data.get('movies', []):
                    all_candidates.append({
                        'tmdb_id': m.get('id') or m.get('tmdb_id'),
                        'title': m.get('title', title),
                        'year': str(m.get('release_date', '') or '')[:4],
                        'release_date': m.get('release_date', ''),
                        'media_type': 'movie',
                        'popularity': m.get('popularity', 0),
                        'vote_count': m.get('vote_count', 0),
                    })
                for s in data.get('tv_shows', []):
                    all_candidates.append({
                        'tmdb_id': s.get('id') or s.get('tmdb_id'),
                        'title': s.get('name') or s.get('title', title),
                        'name': s.get('name') or s.get('title', title),
                        'year': str(s.get('first_air_date', '') or '')[:4],
                        'first_air_date': s.get('first_air_date', ''),
                        'media_type': 'tv',
                        'popularity': s.get('popularity', 0),
                        'vote_count': s.get('vote_count', 0),
                    })
        except Exception as e:
            logger.warning('Luma search fallback failed: %s', e)

    if not all_candidates:
        return []

    # Score by title similarity, deduplicate by tmdb_id
    scored = [(score_result(title, c), c) for c in all_candidates]
    scored.sort(key=lambda x: x[0], reverse=True)

    # If caller provided a specific year, promote year-matching candidates to the front.
    if year:
        year_str = str(year)
        year_hits = [(sc, c) for sc, c in scored if c.get('year') == year_str]
        year_misses = [(sc, c) for sc, c in scored if c.get('year') != year_str]
        scored = year_hits + year_misses
    elif prefer_newest:
        # Add a recency bonus to the existing title-match score rather than
        # replacing it — title similarity stays the primary signal so "Little Big
        # Women (2022)" can't beat "Little Women (2019)" just by being newer.
        # Max bonus ≈ 55 pts (2030 release), well below the 100-pt exact-match
        # advantage, so a correctly-titled older film still wins over a
        # differently-titled newer one.
        boosted = []
        for sc, c in scored:
            try:
                yr = int(c.get('year') or 0)
            except (ValueError, TypeError):
                yr = 0
            recency = max(0, yr - 1975)  # 2019→44, 1994→19, 1978→3, max ~55
            type_bonus = 10 if (media_type and c.get('media_type') == media_type) else 0
            boosted.append((sc + recency + type_bonus, c))
        boosted.sort(key=lambda x: x[0], reverse=True)
        scored = boosted

    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for _, c in scored:
        tid = c['tmdb_id']
        if tid in seen:
            continue
        seen.add(tid)
        unique.append({
            'tmdb_id': c['tmdb_id'],
            'title': c['title'],
            'year': c['year'],
            'media_type': c['media_type'],
            'popularity': c.get('popularity', 0),
            'vote_count': c.get('vote_count', 0),
        })
        if len(unique) >= limit:
            break
    return unique


def _fetch_media_info(tmdb_id: int, media_type: str,
                      season: Optional[int] = None,
                      episode: Optional[int] = None) -> Dict[str, Any]:
    """Fetch cast names and poster/thumbnail URL from TMDb.

    - TV + episode: returns the episode still (16:9 screenshot) via
      /tv/{id}/season/{s}/episode/{e}
    - TV + season only: returns the season poster via /tv/{id}/season/{s}
    - Otherwise: returns the show/movie poster from the main detail endpoint
    """
    tmdb_key = os.environ.get('TMDB_API_KEY', '')
    if not tmdb_key:
        return {'cast': [], 'poster_url': None}
    endpoint = 'tv' if media_type == 'tv' else 'movie'
    try:
        resp = requests.get(
            f'https://api.themoviedb.org/3/{endpoint}/{tmdb_id}',
            params={'api_key': tmdb_key, 'append_to_response': 'credits'},
            timeout=10,
        )
        if resp.ok:
            detail = resp.json()
            cast = detail.get('credits', {}).get('cast', [])
            cast_names = [c.get('name', '') for c in cast[:3] if c.get('name')]
            poster_path = detail.get('poster_path')
            poster_url = f'https://image.tmdb.org/t/p/w500{poster_path}' if poster_path else None

            # Override with the most specific image available:
            # episode still > season poster > show poster (already set above)
            if media_type == 'tv' and season is not None:
                if episode is not None:
                    # Episode still (16:9 screenshot) for specific episode requests
                    try:
                        ep_resp = requests.get(
                            f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}',
                            params={'api_key': tmdb_key},
                            timeout=8,
                        )
                        if ep_resp.ok:
                            still = ep_resp.json().get('still_path')
                            if still:
                                poster_url = f'https://image.tmdb.org/t/p/w780{still}'
                    except Exception as ee:
                        logger.warning('Episode still fetch failed for %s s%se%s: %s', tmdb_id, season, episode, ee)
                else:
                    # Season poster for season-only requests
                    try:
                        s_resp = requests.get(
                            f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}',
                            params={'api_key': tmdb_key},
                            timeout=8,
                        )
                        if s_resp.ok:
                            s_poster = s_resp.json().get('poster_path')
                            if s_poster:
                                poster_url = f'https://image.tmdb.org/t/p/w500{s_poster}'
                    except Exception as se:
                        logger.warning('Season poster fetch failed for %s s%s: %s', tmdb_id, season, se)

            return {'cast': cast_names, 'poster_url': poster_url}
    except Exception as e:
        logger.warning('_fetch_media_info failed for %s/%s: %s', endpoint, tmdb_id, e)
    return {'cast': [], 'poster_url': None}


def _build_candidate_response(candidate: Dict, profile: str, index: int,
                               candidates: List[Dict], query: str,
                               check_and_add: bool = False,
                               quality: Optional[str] = None,
                               season: Optional[int] = None,
                               episode: Optional[int] = None) -> Dict[str, Any]:
    """Build a pending confirmation response for a single candidate."""
    found_title = candidate['title']
    tmdb_id = candidate['tmdb_id']
    year = candidate.get('year', '')
    media_type = candidate.get('media_type', 'movie')

    media_info = _fetch_media_info(tmdb_id, media_type, season=season, episode=episode) if tmdb_id else {'cast': [], 'poster_url': None}
    cast_names = media_info['cast']
    poster_url = media_info['poster_url']

    cast_str = f", starring {', '.join(cast_names)}" if cast_names else ""
    year_str = f" ({year})" if year else ""
    kind = "show" if media_type == 'tv' else "movie"

    if media_type == 'tv' and season is not None:
        target_str = f" — season {season}, episode {episode}" if episode is not None else f" — season {season}"
    else:
        target_str = ""

    if index == 0:
        msg = f"I found the {kind} {found_title}{year_str}{cast_str}{target_str}. Is this the one?"
    else:
        msg = f"How about {found_title}{year_str}{cast_str}{target_str}?"

    pending: Dict[str, Any] = {
        'title': found_title,
        'tmdb_id': tmdb_id,
        'year': year,
        'media_type': media_type,
        'quality_profile': profile,
        'candidates': candidates,
        'candidate_index': index,
        'original_query': query,
    }
    if check_and_add:
        pending['check_and_add'] = True
    if quality:
        pending['quality'] = quality
    if season is not None:
        pending['season'] = season
    if episode is not None:
        pending['episode'] = episode

    data: Dict[str, Any] = {'pending_watchlist': pending}
    if poster_url:
        data['poster_url'] = poster_url

    return {
        'type': 'download_request',
        'status': 'pending',
        'message': msg,
        'data': data,
    }


def _needs_disambiguation(candidates: List[Dict], query_year: Optional[int] = None) -> bool:
    """Return True only when the user specified a year and multiple candidates share it.

    Most searches should never show a list — the best TMDb match is returned directly.
    Disambiguation only fires for genuinely ambiguous cases like "Wrestlemania 2026"
    where multiple specials share the same year.
    """
    if len(candidates) < 2:
        return False
    if query_year:
        year_str = str(query_year)
        if sum(1 for c in candidates if c.get('year') == year_str) >= 2:
            return True
    return False


def handle_watchlist_disambiguate(candidates: List[Dict], query: str,
                                   profile: str, check_and_add: bool = False,
                                   quality: Optional[str] = None,
                                   season: Optional[int] = None,
                                   episode: Optional[int] = None,
                                   query_year: Optional[int] = None) -> Dict[str, Any]:
    """Present multiple candidates at once and ask the user to pick one."""
    if query_year:
        year_str = str(query_year)
        show = [c for c in candidates if c.get('year') == year_str]
        if len(show) < 2:
            show = candidates[:5]
    else:
        show = candidates[:5]

    lines = []
    for i, c in enumerate(show, 1):
        yr = f" ({c['year']})" if c.get('year') else ''
        lines.append(f"{i}. {c['title']}{yr}")

    msg = (
        "I found a few options — which one did you mean?\n"
        + "\n".join(lines)
        + "\nSay the number or part of the title."
    )

    pending: Dict[str, Any] = {
        'awaiting_disambiguation': True,
        'disambiguation_options': show,
        'original_query': query,
        'quality_profile': profile,
        'check_and_add': check_and_add,
    }
    if quality:
        pending['quality'] = quality
    if season is not None:
        pending['season'] = season
    if episode is not None:
        pending['episode'] = episode

    return {
        'type': 'download_request',
        'status': 'pending',
        'message': msg,
        'data': {'pending_watchlist': pending},
    }


def handle_download_movie(title: str, quality: Optional[str] = None,
                          year: Optional[int] = None,
                          prefer_newest: bool = False) -> Dict[str, Any]:
    """Confirm the TMDb match with the user, then check Plex and add if not owned.

    Confirm-first flow: never touches Plex until the user says yes. On confirmation
    the router checks ownership, reports "already available" if found, or auto-adds
    to Radarr with the resolved quality profile.
    """
    clean_title = title
    extracted_quality = quality
    quality_match = re.search(
        r'\s+in\s+(?:4[kK]|uhd|UHD|1080[pP]|720[pP]|[hH][dD]|remux|blu[\s-]?ray|full\s*rip)\s*$',
        clean_title, re.I,
    )
    if quality_match:
        extracted_quality = extracted_quality or re.sub(r'^in\s+', '', quality_match.group(0), flags=re.I).strip()
        clean_title = clean_title[:quality_match.start()].strip()

    # Detect "new/latest/newest/recent" qualifiers in the title as a fallback
    # (in case the classifier didn't extract prefer_newest).
    if not prefer_newest and _NEWEST_QUALIFIER_RE.search(clean_title):
        prefer_newest = True
    if prefer_newest:
        clean_title = _NEWEST_QUALIFIER_RE.sub('', clean_title).strip()
        clean_title = re.sub(r'\s+', ' ', clean_title)

    # If the title contains "season X" or "SxxEyy", delegate to TV handler.
    # Handles classifier routing "Curb Your Enthusiasm Season 10" to download_movie.
    _tv_redirect = re.search(r'\b(?:S\d{1,2}E\d{1,2}|season\s+\d{1,2})\b', clean_title, re.I)
    if _tv_redirect:
        return handle_download_tv(clean_title, quality=extracted_quality, year=year)

    profile = _resolve_quality_profile(extracted_quality)

    candidates = _resolve_tmdb_multi_all(clean_title, year=year, prefer_newest=prefer_newest,
                                          media_type='movie')
    if not candidates:
        return {
            'type': 'download_request',
            'status': 'error',
            'message': f"I couldn't find \"{clean_title}\".",
            'data': {},
        }

    if _needs_disambiguation(candidates, query_year=year):
        return handle_watchlist_disambiguate(
            candidates, clean_title, profile,
            check_and_add=True, quality=extracted_quality, query_year=year,
        )

    return _build_candidate_response(
        candidates[0], profile, 0, candidates, clean_title,
        check_and_add=True, quality=extracted_quality,
    )


def handle_watchlist_next(pending: Dict) -> Dict[str, Any]:
    """Handle 'no' on a watchlist candidate — advance to the next one or ask for help.

    Called by the router when the user rejects a candidate.
    """
    candidates = pending.get('candidates', [])
    index = pending.get('candidate_index', 0)
    query = pending.get('original_query', '')
    profile = pending.get('quality_profile', 'TFlix')
    check_and_add = pending.get('check_and_add', False)
    quality = pending.get('quality')
    season = pending.get('season')
    episode = pending.get('episode')
    next_index = index + 1

    if next_index < len(candidates):
        return _build_candidate_response(
            candidates[next_index], profile, next_index, candidates, query,
            check_and_add=check_and_add, quality=quality,
            season=season, episode=episode,
        )

    # No more candidates — ask for more details
    exhausted_pending: Dict[str, Any] = {
        'awaiting_refinement': True,
        'original_query': query,
        'quality_profile': profile,
    }
    if check_and_add:
        exhausted_pending['check_and_add'] = True
    if quality:
        exhausted_pending['quality'] = quality
    if season is not None:
        exhausted_pending['season'] = season
    if episode is not None:
        exhausted_pending['episode'] = episode

    return {
        'type': 'download_request',
        'status': 'pending',
        'message': (
            f"I'm out of results for \"{query}\". "
            "Can you give me more details — like the year it came out, or who stars in it?"
        ),
        'data': {'pending_watchlist': exhausted_pending},
    }


def handle_watchlist_refine(query: str, refinement: str,
                             profile: str = 'TFlix',
                             check_and_add: bool = False,
                             quality: Optional[str] = None,
                             season: Optional[int] = None,
                             episode: Optional[int] = None) -> Dict[str, Any]:
    """Re-search with the user's additional details appended to the original query."""
    combined = f"{query} {refinement}"
    logger.info('Watchlist refine: original=%s refinement=%s combined=%s', query, refinement, combined)

    candidates = _resolve_tmdb_multi_all(combined)
    if not candidates:
        return {
            'type': 'download_request',
            'status': 'error',
            'message': f"Still couldn't find it. Try telling me the exact title and I'll search again.",
            'data': {},
        }

    return _build_candidate_response(
        candidates[0], profile, 0, candidates, query,
        check_and_add=check_and_add, quality=quality,
        season=season, episode=episode,
    )


def _resolve_tmdb_tv(title: str) -> Optional[Dict[str, Any]]:
    """Search TMDb for a TV show. Scores results to pick best match."""
    tmdb_key = os.environ.get('TMDB_API_KEY', '')
    if tmdb_key:
        try:
            resp = requests.get(
                'https://api.themoviedb.org/3/search/tv',
                params={'api_key': tmdb_key, 'query': title},
                timeout=8,
            )
            if resp.ok:
                candidates = []
                for item in resp.json().get('results', []):
                    candidates.append({
                        'tmdb_id': item['id'],
                        'title': item.get('name', title),
                        'name': item.get('name', title),
                        'year': (item.get('first_air_date') or '')[:4],
                        'first_air_date': item.get('first_air_date', ''),
                    })
                pick = best_match(title, candidates)
                if pick:
                    return {
                        'tmdb_id': pick['tmdb_id'],
                        'title': pick['title'],
                        'year': pick['year'],
                    }
        except Exception as e:
            logger.warning('TMDb TV search failed: %s', e)

    # Fallback: use Luma's search endpoint
    try:
        resp = requests.get(
            f'{FLASK_BASE}/search',
            params={'q': title, 'type': 'tv'},
            timeout=10,
        )
        if resp.ok:
            candidates = []
            for s in resp.json().get('tv_shows', []):
                candidates.append({
                    'tmdb_id': s.get('id') or s.get('tmdb_id'),
                    'title': s.get('name') or s.get('title', title),
                    'name': s.get('name') or s.get('title', title),
                    'year': str(s.get('first_air_date', '') or '')[:4],
                    'first_air_date': s.get('first_air_date', ''),
                })
            pick = best_match(title, candidates)
            if pick:
                return {
                    'tmdb_id': pick['tmdb_id'],
                    'title': pick['title'],
                    'year': pick['year'],
                }
    except Exception as e:
        logger.warning('Luma TV search fallback failed: %s', e)
    return None


def handle_download_tv(title: str, season: Optional[int] = None,
                       episode: Optional[int] = None,
                       quality: Optional[str] = None,
                       year: Optional[int] = None,
                       prefer_newest: bool = False) -> Dict[str, Any]:
    """Confirm the TMDb match with the user, then check Plex and add to Sonarr if not owned.

    Same confirm-first flow as handle_download_movie.
    """
    clean_title = title
    extracted_quality = quality

    # Extract season/episode from title string when not passed as separate params.
    # Handles classifier putting "Curb Your Enthusiasm Season 10" in title rather
    # than title="Curb Your Enthusiasm" + season=10.
    if season is None:
        se_match = re.search(r'\bS(\d{1,2})E(\d{1,2})\b', clean_title, re.I)
        if se_match:
            season = int(se_match.group(1))
            episode = int(se_match.group(2))
            clean_title = clean_title[:se_match.start()].strip()
        else:
            season_ep_match = re.search(
                r'\bseason\s+(\d{1,2})(?:\s+episode\s+(\d{1,2}))?\b', clean_title, re.I,
            )
            if season_ep_match:
                season = int(season_ep_match.group(1))
                if season_ep_match.group(2):
                    episode = int(season_ep_match.group(2))
                clean_title = clean_title[:season_ep_match.start()].strip()

    quality_match = re.search(
        r'\s+in\s+(?:4[kK]|uhd|UHD|1080[pP]|720[pP]|[hH][dD]|remux|blu[\s-]?ray|full\s*rip)\s*$',
        clean_title, re.I,
    )
    if quality_match:
        extracted_quality = extracted_quality or re.sub(r'^in\s+', '', quality_match.group(0), flags=re.I).strip()
        clean_title = clean_title[:quality_match.start()].strip()

    profile = _resolve_quality_profile(extracted_quality)

    # Detect "new/latest" qualifiers in title as fallback
    if not prefer_newest and _NEWEST_QUALIFIER_RE.search(clean_title):
        prefer_newest = True
    if prefer_newest:
        clean_title = _NEWEST_QUALIFIER_RE.sub('', clean_title).strip()
        clean_title = re.sub(r'\s+', ' ', clean_title)

    candidates = _resolve_tmdb_multi_all(clean_title, year=year, prefer_newest=prefer_newest,
                                          media_type='tv')
    if not candidates:
        return {
            'type': 'download_request',
            'status': 'error',
            'message': f'I couldn\'t find "{clean_title}".',
            'data': {},
        }

    if _needs_disambiguation(candidates, query_year=year):
        return handle_watchlist_disambiguate(
            candidates, clean_title, profile,
            check_and_add=True, quality=extracted_quality,
            season=season, episode=episode, query_year=year,
        )

    return _build_candidate_response(
        candidates[0], profile, 0, candidates, clean_title,
        check_and_add=True, quality=extracted_quality,
        season=season, episode=episode,
    )
