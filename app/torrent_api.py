#!/usr/bin/env python3
"""
Torrent API — title parsing and deduplication helpers for the explore cache pipeline.
"""

import re
from typing import List, Dict, Optional, Tuple


class TorrentAPI:
    """Base class — retained for static helper methods used by combine_and_dedupe_movies."""

    @staticmethod
    def extract_movie_info(file_name: str) -> Tuple[str, Optional[str]]:
        """Extract movie title and year from torrent name"""
        # Remove quality info and everything after year
        patterns_to_remove = [
            r'\b(1080p|720p|2160p|4K|480p|576p|HDR|HDR10|HDR10\+|Dolby Vision|DV|DoVi)\b',
            r'\b(BluRay|Blu-Ray|BRRip|WEB-DL|WEBRip|HDTV|DVDRip|CAM|TS|TC|SCR|R5|DVDScr|REMUX)\b',
            r'\b(x264|x265|h264|h265|HEVC|AVC|DivX|XviD)\b',
            r'\b(DTS|DTS-HD|TrueHD|Atmos|AC3|AAC|MP3|FLAC)\b',
            r'\b(MA|HR|ES|EX|X|Plus|5\.1|7\.1|2\.0)\b',
            r'\b(REPACK|PROPER|RERIP|EXTENDED|UNRATED|DIRECTORS?\.?CUT|THEATRICAL)\b',
            r'\b(iNTERNAL|LiMiTED|MULTi|DUAL|SUBBED|DUBBED)\b',
            r'\[.*?\]',  # Remove anything in square brackets
            r'\b(AMZN|NF|HULU|DSNP|ATVP|HMAX|PCOK|WEB|MAX|iT)\b',
            r'-[A-Za-z0-9]+$',  # Remove release group at end
        ]

        clean_name = file_name
        for pattern in patterns_to_remove:
            clean_name = re.sub(pattern, ' ', clean_name, flags=re.IGNORECASE)

        # Extract year
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', clean_name)
        year = year_match.group(1) if year_match else None

        # Extract title (everything before the year)
        if year:
            title = clean_name.split(year)[0].strip()
        else:
            title = clean_name.strip()

        # Clean up the title
        title = re.sub(r'[\.\-\_]+', ' ', title).strip()
        title = re.sub(r'\s+', ' ', title)

        return title, year

    @staticmethod
    def is_low_quality_release(torrent_name: str) -> bool:
        """Check if torrent is a low-quality release (CAM, TS, Telesync, etc.)"""
        torrent_upper = torrent_name.upper()
        # List of low-quality source indicators
        low_quality_patterns = [
            'CAM', 'CAMRIP', 'CAM-RIP', 'HDCAM', 'HD-CAM', 'HQCAM',
            'TELESYNC', 'TS', 'HDTS', 'HD-TS', 'PDVD', 'PREDVDRIP',
            'TELECINE', 'TC', 'HDTC', 'HD-TC',
            'SCR', 'SCREENER', 'DVDSCR', 'DVD-SCR', 'DVDSCREENER', 'BDSCR',
            'R5', 'R5.LINE', 'R5LINE',
            'WORKPRINT', 'WP',
            'DCP', 'HDCAM-RIP'
        ]

        # Check for exact word matches (surrounded by non-alphanumeric or at start/end)
        for pattern in low_quality_patterns:
            # Match pattern as a whole word
            if re.search(r'(?:^|[.\-_\s\[\]])' + re.escape(pattern) + r'(?:[.\-_\s\[\]]|$)', torrent_upper):
                return True
        return False


def combine_and_dedupe_movies(movies: List[Dict]) -> List[Dict]:
    """Combine movies from multiple sources and remove duplicates, keeping highest seeders"""
    movie_map = {}

    for movie in movies:
        torrent_name = movie.get('torrent_name', '') or movie.get('name', '')

        # Filter out low-quality releases (CAM, TS, Telesync, etc.)
        if TorrentAPI.is_low_quality_release(torrent_name):
            continue

        # Filter out TV shows - check for season/episode patterns
        if any(pattern in torrent_name.upper() for pattern in ['S01E', 'S02E', 'S03E', 'S04E', 'S05E',
                                                                 'S06E', 'S07E', 'S08E', 'S09E', 'S10E',
                                                                 'S11E', 'S12E', 'S13E', 'S14E', 'S15E',
                                                                 'SEASON', 'EPISODE']):
            continue

        # Also check if it matches common TV show patterns
        if re.search(r'S\d{1,2}E\d{1,2}', torrent_name, re.IGNORECASE):
            continue
        if re.search(r'Season\s*\d+', torrent_name, re.IGNORECASE):
            continue

        # Get the title and year - clean it up if it contains quality info
        title = movie.get('title', '').strip()
        year = movie.get('year', '')

        # If title looks like it contains quality info (full torrent name), extract clean title
        if any(qual in title.upper() for qual in ['1080P', '720P', '2160P', '4K', 'WEB', 'BLURAY', 'HDTV']):
            # Extract clean title and year from the torrent-style title
            extracted_title, extracted_year = TorrentAPI.extract_movie_info(title)
            if extracted_title:
                title = extracted_title
                if extracted_year and not year:
                    year = extracted_year

        # Normalize the title for deduplication
        title_normalized = title.lower().strip()

        # Remove common articles from beginning for better matching
        for article in ['the ', 'a ', 'an ']:
            if title_normalized.startswith(article):
                title_normalized = title_normalized[len(article):]
                break

        # Remove parenthetical content like (2025) or ( for better matching
        title_normalized = re.sub(r'\s*\([^)]*\)', '', title_normalized).strip()
        # Also remove any trailing parenthesis without closing
        title_normalized = re.sub(r'\s*\($', '', title_normalized).strip()

        # Remove all punctuation and extra spaces for better deduplication
        title_normalized = re.sub(r'[^\w\s]', '', title_normalized)
        # Normalize whitespace
        title_normalized = re.sub(r'\s+', ' ', title_normalized).strip()

        # Normalize tmdb_id (strip BHD "movie/" prefix)
        tmdb_id = str(movie.get('tmdb_id', '') or '')
        if '/' in tmdb_id:
            tmdb_id = tmdb_id.rsplit('/', 1)[-1]
        if tmdb_id:
            movie['tmdb_id'] = tmdb_id

        # Skip if no title
        if not title_normalized:
            continue

        # Store clean title/year back
        movie['title'] = title
        movie['year'] = year
        movie['_title_key'] = title_normalized

        # Use tmdb_id as primary key, title as fallback
        if tmdb_id:
            key = f"tmdb_{tmdb_id}"
        else:
            key = title_normalized

        if key not in movie_map or movie.get('seeders', 0) > movie_map[key].get('seeders', 0):
            movie_map[key] = movie

    # Second pass: merge title-only entries that match a tmdb entry
    # Build a lookup from normalized title → tmdb key
    title_to_tmdb = {}
    for key, m in movie_map.items():
        if key.startswith('tmdb_'):
            tk = m.get('_title_key', '')
            if tk:
                title_to_tmdb[tk] = key

    merged_keys = set()
    for key in list(movie_map.keys()):
        if not key.startswith('tmdb_') and key in title_to_tmdb:
            tmdb_key = title_to_tmdb[key]
            # Keep the one with more seeders
            if movie_map[key].get('seeders', 0) > movie_map[tmdb_key].get('seeders', 0):
                movie_map[tmdb_key] = movie_map[key]
            merged_keys.add(key)

    for key in merged_keys:
        del movie_map[key]

    # Clean up internal key
    for m in movie_map.values():
        m.pop('_title_key', None)

    # Convert back to list and sort by seeders
    combined = list(movie_map.values())
    combined.sort(key=lambda x: x.get('seeders', 0), reverse=True)

    return combined
