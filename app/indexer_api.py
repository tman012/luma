#!/usr/bin/env python3
"""
Release search entry point (v172, tracker port step 4: the Prowlarr bridge is out).

Searches go to Luma's own trackers (Settings > Trackers, app.tracker_search) and nowhere else.
The Prowlarr and Torznab-proxy clients, their LAN download bridge and their separate guard
(indexer_guard.json) are gone. What is left here is shared by the callers: category tables,
the sweep queries, the release-name parser and the get_indexer_client() factory, which now
returns the native client or None.

Existing installs: the old `indexer.*` settings (provider, base_url, torznab_url, api_key) are
left as they are in settings.json (no data loss; Import from Prowlarr in Settings > Trackers
reads base_url/api_key on demand) but are never used to search or grab. indexer_guard.json is
left on disk untouched for one release (rollback); Import from Prowlarr reads it, read-only.
"""

import logging
import re
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Newznab standard movie subcategories.  Prowlarr browse mode does NOT expand
# the parent 2000 category, so we must list every sub-category explicitly.
MOVIE_CATEGORIES_EXPANDED = [
    2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080,
]

# Newznab TV + Sports subcategories.  Appended to the movie-expanded set when
# a raw (broad) search targets TV content — never used by the movie-only
# Explore sweep path. Includes TV/Sport (5060) so wrestling PPVs etc. surface
# (IPT files AEW under TV Sports). 5010/5020/5050 fill standard TV gaps;
# 5070/5080 kept for older/nonstandard indexer mappings.
TV_SPORTS_CATEGORIES = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070, 5080]


class IndexerSkipped(Exception):
    """Raised when a search to one indexer is deliberately not sent."""


def _build_search_query(query: str, year: Optional[int] = None) -> str:
    """Compose the indexer search string, appending *year* only when the title
    doesn't already contain it (avoids a redundant "Title 2026 2026" that some
    trackers match too strictly)."""
    query = (query or '').strip()
    if year and str(year) not in query:
        return f'{query} {year}'
    return query

# Compound sweep queries for "Hot recent" mode.  Year × format narrows
# results enough that Prowlarr returns high-seeder items (not just newest).
SWEEP_QUERIES = [
    'WEB-DL 2024', 'WEB-DL 2025', 'WEB-DL 2026',
    'BluRay 2024', 'BluRay 2025', 'BluRay 2026',
    'REMUX 2024',  'REMUX 2025',  'REMUX 2026',
    '2160p 2024',  '2160p 2025',  '2160p 2026',
    '1080p 2024',  '1080p 2025',  '1080p 2026',
    'h264 2024',   'h264 2025',   'h264 2026',
    'h265 2024',   'h265 2025',   'h265 2026',
]


def _expand_movie_categories(categories_str: str) -> List[int]:
    """Expand a comma-separated category string.

    If the parent category ``2000`` is present the full set of movie
    sub-categories is returned so that Prowlarr browse mode returns
    items filed under sub-categories like 2050 (BluRay) or 2080 (WEB-DL).
    """
    cats = set()
    for part in categories_str.split(','):
        part = part.strip()
        if part.isascii() and part.isdigit() and len(part) <= 6:   # v174 (FF-8): never int() a huge string
            cats.add(int(part))
    if 2000 in cats:
        cats.update(MOVIE_CATEGORIES_EXPANDED)
    return sorted(cats)


# ---------------------------------------------------------------------------
# Title parser — extract metadata from torrent names
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r'[\.\s\(](\d{4})[\.\s\)]')
_RES_RE = re.compile(r'(2160p|1080p|720p|480p)', re.IGNORECASE)
_SOURCE_RE = re.compile(
    r'(REMUX|WEB-DL|WEB\.DL|WEBDL|WEBRip|WEB(?=[\s\.])|BluRay|Blu-Ray|BDRip|BRRip|HDRip|DVDRip|HDTV)',
    re.IGNORECASE,
)


def _parse_title(name: str) -> Dict[str, Any]:
    """Extract year, resolution, and source type from a torrent name."""
    result: Dict[str, Any] = {
        'year': None,
        'resolution': None,
        'source': None,
        'clean_title': name,
    }

    # Year
    m = _YEAR_RE.search(name)
    if m:
        year = int(m.group(1))
        if 1920 <= year <= 2030:
            result['year'] = str(year)
            # Clean title = everything before the year
            idx = name.index(m.group(0))
            result['clean_title'] = re.sub(r'[\.\-_]', ' ', name[:idx]).strip()

    # Resolution — normalize to lowercase (2160P → 2160p)
    m = _RES_RE.search(name)
    if m:
        result['resolution'] = m.group(1).lower()

    # Source — check REMUX first (torrent names like "BluRay.REMUX" must
    # classify as REMUX, but the regex captures the leftmost match which
    # would be "BluRay").  Matches torrent_api.py priority order.
    name_upper = name.upper()
    if 'REMUX' in name_upper:
        result['source'] = 'REMUX'
    else:
        m = _SOURCE_RE.search(name)
        if m:
            raw = m.group(1)
            upper = raw.upper().replace('.', '-')
            if upper in ('WEBDL', 'WEB-DL', 'WEB'):
                result['source'] = 'WEB-DL'
            elif upper in ('BLURAY', 'BLU-RAY', 'BDRIP', 'BRRIP'):
                result['source'] = 'BluRay'
            else:
                result['source'] = raw

    return result


def parse_items(raw_items: list) -> List[Dict]:
    """Convert raw release items (the native trackers' item shape) into normalised movie dicts."""
    movies: List[Dict] = []
    for item in raw_items:
        seeders = item.get('seeders', 0)
        if seeders is None:
            seeders = 0
        elif isinstance(seeders, str):
            seeders = int(seeders) if seeders.isdigit() else 0

        parsed = _parse_title(item.get('title', ''))
        movies.append({
            'title': parsed['clean_title'],
            'year': parsed['year'] or '',
            'seeders': seeders,
            'size_gb': round(item.get('size', 0) / (1024 ** 3), 2) if item.get('size') else 0,
            'resolution': parsed['resolution'] or '',
            'source': parsed['source'] or '',
            'tracker': item.get('indexer', ''),
            'pub_date': item.get('publishDate', ''),
            'download_url': item.get('downloadUrl', ''),
            'info_hash': item.get('infoHash', ''),
            'imdb_id': item.get('imdbId', '') or '',
            'torrent_name': item.get('title', ''),
            # Needed for Radarr's "grab specific release" endpoint —
            # without these the user can't trigger a direct download.
            'guid': item.get('guid', '') or '',
            'indexer_id': item.get('indexerId'),
        })
    return movies


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_legacy_logged = [False]
_legacy_lock = threading.Lock()


def legacy_configured(settings) -> bool:
    """An old Prowlarr/Torznab-proxy search setup is still saved (it is no longer used)."""
    try:
        return bool(str(settings.get('indexer.api_key') or '').strip()
                    and (str(settings.get('indexer.base_url') or '').strip()
                         or str(settings.get('indexer.torznab_url') or '').strip()))
    except Exception:  # noqa: BLE001 - a broken settings object is "not configured"
        return False


def get_indexer_client(settings) -> Optional[Any]:
    """The native trackers client when at least one tracker is on in Settings > Trackers, else None.

    v172: never a Prowlarr or Torznab-proxy client. A saved Prowlarr/Torznab setup from an older
    version is left alone and logged once; searches then simply find nothing until a tracker is on."""
    from app.tracker_search import LumaTrackersClient, has_enabled_trackers
    if has_enabled_trackers():
        return LumaTrackersClient()
    if legacy_configured(settings):
        with _legacy_lock:
            if not _legacy_logged[0]:
                _legacy_logged[0] = True
                logger.warning('Search: a Prowlarr/Torznab setup is still saved but is no longer used; '
                               'turn on a tracker in Settings > Trackers (Import from Prowlarr can copy them)')
    return None
