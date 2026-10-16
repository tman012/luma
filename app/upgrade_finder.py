"""Library Upgrade Finder — searches indexers for higher-quality versions
of movies across ALL Plex library sections. Compares quality tiers across
libraries (encodes < 1080p remux < 4K remux) and finds what's available
on indexers above the user's current best copy."""

import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Quality tiers (higher = better)
QUALITY_TIERS = {
    '720p_encode': 1,
    '1080p_web': 2,
    '1080p_encode': 2,
    '1080p_remux': 3,
    '2160p_web': 4,
    '2160p_encode': 4,
    '2160p_remux': 5,
}

BATCH_SIZE = 50  # movies to search per batch run

# Section names mapped to their known minimum quality tier.
# Movies in these sections are assumed to be at least this quality.
SECTION_QUALITY_FLOOR: Dict[str, Tuple[str, int]] = {
    'full rips': ('1080p Remux', QUALITY_TIERS['1080p_remux']),
    '4k movies': ('2160p Remux', QUALITY_TIERS['2160p_remux']),
    '4k': ('2160p Remux', QUALITY_TIERS['2160p_remux']),
}


def _normalize_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r'^(the|a|an)\s+', '', t)
    t = re.sub(r'[^a-z0-9\s]', '', t)
    return re.sub(r'\s+', ' ', t).strip()


def _classify_plex_quality(item: Dict[str, Any]) -> Tuple[str, int]:
    """Determine quality label and tier from Plex media info."""
    res = item.get('videoResolution', '')
    bitrate = item.get('bitrate', 0)
    file_path = (item.get('filePath') or '').upper()

    if res in ('4k', '2160') or item.get('height', 0) >= 2000:
        res_label = '2160p'
    elif res == '1080' or item.get('height', 0) >= 900:
        res_label = '1080p'
    else:
        res_label = '720p'

    is_remux = 'REMUX' in file_path
    is_web = any(x in file_path for x in ('WEB-DL', 'WEBDL', 'WEB.DL'))

    if is_remux:
        source = 'Remux'
    elif is_web:
        source = 'WEB-DL'
    elif res_label == '2160p' and bitrate > 40000:
        source = 'Remux'
    elif res_label == '1080p' and bitrate > 20000:
        source = 'Remux'
    else:
        source = 'Encode'

    label = f'{res_label} {source}'
    tier_key = f'{res_label}_{source.lower().replace("-", "_")}'
    tier = QUALITY_TIERS.get(tier_key, 1)
    return label, tier


def _classify_tracker_quality(resolution: str, torrent_name: str) -> Tuple[str, int]:
    """Determine quality label and tier from tracker/indexer result."""
    tn = torrent_name.upper()

    if '2160' in (resolution or ''):
        res_label = '2160p'
    elif '1080' in (resolution or ''):
        res_label = '1080p'
    else:
        res_label = '720p'

    if 'REMUX' in tn:
        source = 'Remux'
    elif 'WEB-DL' in tn or 'WEBDL' in tn or 'WEB.DL' in tn:
        source = 'WEB-DL'
    else:
        source = 'Encode'

    label = f'{res_label} {source}'
    tier_key = f'{res_label}_{source.lower().replace("-", "_")}'
    tier = QUALITY_TIERS.get(tier_key, 1)
    return label, tier


class UpgradeFinder:
    """Cross-library upgrade finder that searches indexers in daily batches.

    Syncs ALL movie libraries, tracks the highest quality copy of each movie
    across all sections, then searches indexers for upgrades above that level.
    """

    def __init__(self, plex_service, settings, cache_dir: str = '/app/data'):
        self.plex = plex_service
        self.settings = settings
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.db_path = self.cache_dir / 'upgrade_library.db'
        self.lock = threading.Lock()
        self._running = False
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript("""
                -- Each row = one movie in one section.
                -- A movie can appear in multiple sections at different qualities.
                CREATE TABLE IF NOT EXISTS library_movies (
                    tmdb_id TEXT NOT NULL,
                    section_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    year TEXT,
                    poster_path TEXT,
                    rating_key INTEGER,
                    section_name TEXT,
                    current_quality TEXT,
                    current_tier INTEGER,
                    current_size_gb REAL,
                    current_codec TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tmdb_id, section_id)
                );

                CREATE TABLE IF NOT EXISTS upgrade_results (
                    tmdb_id TEXT PRIMARY KEY,
                    best_quality TEXT,
                    best_tier INTEGER,
                    best_seeders INTEGER,
                    best_size_gb REAL,
                    best_tracker TEXT,
                    best_torrent_name TEXT,
                    upgrade_type TEXT,
                    has_upgrade INTEGER DEFAULT 0,
                    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    grabbed_at TIMESTAMP,
                    dismissed_at TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS batch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    movies_searched INTEGER,
                    upgrades_found INTEGER,
                    duration_seconds REAL,
                    run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- New releases: recent remuxes from trackers matched to library
                CREATE TABLE IF NOT EXISTS new_upgrades (
                    tmdb_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    year TEXT,
                    poster_path TEXT,
                    tmdb_poster TEXT,
                    rating_key INTEGER,
                    sections_owned TEXT,
                    current_quality TEXT,
                    current_tier INTEGER,
                    best_quality TEXT,
                    best_tier INTEGER,
                    best_seeders INTEGER,
                    best_size_gb REAL,
                    best_tracker TEXT,
                    best_torrent_name TEXT,
                    upgrade_type TEXT,
                    pub_date TEXT,
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tmdb_id, best_quality)
                );

                CREATE INDEX IF NOT EXISTS idx_lib_section
                    ON library_movies(section_id);
                CREATE INDEX IF NOT EXISTS idx_lib_tmdb
                    ON library_movies(tmdb_id);
                CREATE INDEX IF NOT EXISTS idx_upgrade_has
                    ON upgrade_results(has_upgrade);
                CREATE INDEX IF NOT EXISTS idx_upgrade_searched
                    ON upgrade_results(searched_at);
                CREATE INDEX IF NOT EXISTS idx_new_discovered
                    ON new_upgrades(discovered_at);
            """)
            # Migrate: if library_movies has old single-column PK, recreate
            self._maybe_migrate(conn)
            # Migrate: add tmdb_poster column to new_upgrades if missing
            self._maybe_migrate_new_upgrades(conn)
            # Migrate: add grabbed_at column to upgrade_results if missing
            self._maybe_migrate_grabbed(conn)
        finally:
            conn.close()

    def _maybe_migrate(self, conn: sqlite3.Connection):
        """Migrate from old single-PK schema to composite PK if needed."""
        cursor = conn.execute("PRAGMA table_info(library_movies)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'section_id' not in cols:
            return  # table doesn't exist yet or already correct

        # Check if PK is composite by looking at index structure
        cursor = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='library_movies'")
        row = cursor.fetchone()
        if not row:
            return
        create_sql = row[0] or ''
        if 'PRIMARY KEY (tmdb_id, section_id)' in create_sql:
            return  # already migrated

        # Old schema has tmdb_id as sole PK — rebuild with composite PK
        logger.info("Migrating upgrade_library.db to cross-library schema...")
        try:
            conn.executescript("""
                ALTER TABLE library_movies RENAME TO library_movies_old;

                CREATE TABLE library_movies (
                    tmdb_id TEXT NOT NULL,
                    section_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    year TEXT,
                    poster_path TEXT,
                    rating_key INTEGER,
                    section_name TEXT,
                    current_quality TEXT,
                    current_tier INTEGER,
                    current_size_gb REAL,
                    current_codec TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tmdb_id, section_id)
                );

                INSERT OR IGNORE INTO library_movies
                    SELECT tmdb_id, section_id, title, year, poster_path,
                           rating_key, section_name, current_quality,
                           current_tier, current_size_gb, current_codec,
                           added_at
                    FROM library_movies_old;

                DROP TABLE library_movies_old;

                -- Clear old search results since tier logic changed
                DELETE FROM upgrade_results;

                CREATE INDEX IF NOT EXISTS idx_lib_section
                    ON library_movies(section_id);
                CREATE INDEX IF NOT EXISTS idx_lib_tmdb
                    ON library_movies(tmdb_id);
            """)
            logger.info("Migration complete — old search results cleared for re-scan")
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    def _maybe_migrate_new_upgrades(self, conn: sqlite3.Connection):
        """Add tmdb_poster column if missing (table gets repopulated on scan)."""
        try:
            cursor = conn.execute("PRAGMA table_info(new_upgrades)")
            cols = {row[1] for row in cursor.fetchall()}
            if cols and 'tmdb_poster' not in cols:
                conn.execute("DROP TABLE new_upgrades")
                conn.execute("""
                    CREATE TABLE new_upgrades (
                        tmdb_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        year TEXT,
                        poster_path TEXT,
                        tmdb_poster TEXT,
                        rating_key INTEGER,
                        sections_owned TEXT,
                        current_quality TEXT,
                        current_tier INTEGER,
                        best_quality TEXT,
                        best_tier INTEGER,
                        best_seeders INTEGER,
                        best_size_gb REAL,
                        best_tracker TEXT,
                        best_torrent_name TEXT,
                        upgrade_type TEXT,
                        pub_date TEXT,
                        discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (tmdb_id, best_quality)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_new_discovered "
                    "ON new_upgrades(discovered_at)")
                conn.commit()
                logger.info("Migrated new_upgrades table: added tmdb_poster column")
        except Exception:
            pass  # table doesn't exist yet, will be created by _init_db

    def _maybe_migrate_grabbed(self, conn: sqlite3.Connection):
        """Add grabbed_at, dismissed_at, and RT rating columns to upgrade_results if missing."""
        try:
            cursor = conn.execute("PRAGMA table_info(upgrade_results)")
            cols = {row[1] for row in cursor.fetchall()}
            if not cols:
                return
            if 'grabbed_at' not in cols:
                conn.execute(
                    "ALTER TABLE upgrade_results ADD COLUMN grabbed_at TIMESTAMP"
                )
                logger.info("Migrated upgrade_results: added grabbed_at column")
            if 'dismissed_at' not in cols:
                conn.execute(
                    "ALTER TABLE upgrade_results ADD COLUMN dismissed_at TIMESTAMP"
                )
                logger.info("Migrated upgrade_results: added dismissed_at column")
            if 'rt_critic' not in cols:
                conn.execute(
                    "ALTER TABLE upgrade_results ADD COLUMN rt_critic INTEGER"
                )
                logger.info("Migrated upgrade_results: added rt_critic column")
            if 'rt_audience' not in cols:
                conn.execute(
                    "ALTER TABLE upgrade_results ADD COLUMN rt_audience INTEGER"
                )
                logger.info("Migrated upgrade_results: added rt_audience column")
            if 'rt_fetched_at' not in cols:
                conn.execute(
                    "ALTER TABLE upgrade_results ADD COLUMN rt_fetched_at TIMESTAMP"
                )
                logger.info("Migrated upgrade_results: added rt_fetched_at column")
            conn.commit()
        except Exception:
            pass

    def mark_grabbed(self, tmdb_id: str) -> bool:
        """Mark an upgrade as grabbed (sent to Radarr). Returns True if updated."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.execute(
                "UPDATE upgrade_results SET grabbed_at = CURRENT_TIMESTAMP "
                "WHERE tmdb_id = ? AND has_upgrade = 1 AND grabbed_at IS NULL",
                (tmdb_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def dismiss_upgrade(self, tmdb_id: str) -> bool:
        """Dismiss an upgrade so it no longer appears in results."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.execute(
                "UPDATE upgrade_results SET dismissed_at = CURRENT_TIMESTAMP "
                "WHERE tmdb_id = ? AND has_upgrade = 1 AND dismissed_at IS NULL",
                (tmdb_id,),
            )
            # Also remove from new_upgrades table
            conn.execute(
                "DELETE FROM new_upgrades WHERE tmdb_id = ?", (tmdb_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_grabbed_count(self) -> int:
        """Return total number of upgrades that have been grabbed."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM upgrade_results WHERE grabbed_at IS NOT NULL"
            ).fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_sections(self) -> List[Dict]:
        """Return movie-type Plex library sections."""
        try:
            sections = self.plex.get_sections_lean()
            return [s for s in sections if s.get('type') == 'movie']
        except Exception as e:
            logger.error(f"Error getting Plex sections: {e}")
            return []

    def sync_all_libraries(self):
        """Sync ALL movie library sections into the local database.
        Each movie gets one row per section it appears in."""
        sections = self.get_sections()
        for sec in sections:
            self.sync_library(sec['id'], sec.get('title', ''))

    def sync_library(self, section_id: int, section_name: str = ''):
        """Sync all movies from a Plex section into the local database."""
        logger.info(f"Syncing library section {section_id} ({section_name})")
        conn = sqlite3.connect(str(self.db_path))
        try:
            existing = set()
            for row in conn.execute(
                    "SELECT tmdb_id FROM library_movies WHERE section_id = ?",
                    (section_id,)):
                existing.add(row[0])

            offset = 0
            added = 0
            while True:
                result = self.plex.get_all_movies(
                    section_id, sort='titleSort:asc',
                    limit=500, offset=offset, timeout=30)
                movies = result.get('movies', [])

                # Use section-level quality floor if known
                section_key = section_name.lower().strip()
                floor_label, floor_tier = SECTION_QUALITY_FLOOR.get(
                    section_key, (None, None))

                for m in movies:
                    tid = str(m.get('tmdb_id', '')) if m.get('tmdb_id') else ''
                    if not tid or tid in existing:
                        continue

                    if floor_label and floor_tier:
                        label, tier = floor_label, floor_tier
                    else:
                        label, tier = _classify_plex_quality(m)
                    file_size = m.get('fileSize', 0)

                    conn.execute("""
                        INSERT OR REPLACE INTO library_movies
                        (tmdb_id, section_id, title, year, poster_path,
                         rating_key, section_name, current_quality,
                         current_tier, current_size_gb, current_codec)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        tid, section_id, m.get('title', ''),
                        str(m.get('year', '')),
                        m.get('thumbPath', ''), m.get('ratingKey', 0),
                        section_name, label, tier,
                        round(file_size / (1024**3), 2) if file_size else 0,
                        m.get('videoCodec', ''),
                    ))
                    added += 1

                if not result.get('has_more', False) or len(movies) < 500:
                    break
                offset += 500

            conn.commit()
            logger.info(f"Synced {added} new movies from section {section_id}")
        finally:
            conn.close()

    def run_batch(self, batch_size: int = BATCH_SIZE) -> Dict:
        """Search indexers for the next batch of unchecked movies.

        Works across all libraries. For each unique movie, uses the
        highest quality tier found across all sections as the baseline.
        """
        with self.lock:
            if self._running:
                return {'status': 'already_running'}
            self._running = True

        try:
            return self._do_batch(batch_size)
        finally:
            with self.lock:
                self._running = False

    def _do_batch(self, batch_size: int) -> Dict:
        start = time.time()
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row

        try:
            # Get indexer client
            from app.indexer_api import IndexerSkipped, get_indexer_client
            client = get_indexer_client(self.settings)
            if not client:
                return {'status': 'error', 'message': 'No indexer configured'}

            # Get unique movies with their best current tier across all sections.
            # Pick movies never searched first, then oldest searched (>7 days).
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()
            rows = conn.execute("""
                SELECT m.tmdb_id, m.title, m.year, m.best_tier
                FROM (
                    SELECT tmdb_id, title, year,
                           MAX(current_tier) AS best_tier
                    FROM library_movies
                    GROUP BY tmdb_id
                ) m
                LEFT JOIN upgrade_results ur ON m.tmdb_id = ur.tmdb_id
                WHERE ur.searched_at IS NULL OR ur.searched_at < ?
                ORDER BY m.title ASC
                LIMIT ?
            """, (cutoff, batch_size)).fetchall()

            if not rows:
                return {
                    'status': 'complete',
                    'message': 'All movies have been searched recently',
                    'movies_searched': 0,
                    'upgrades_found': 0,
                }

            upgrades_found = 0
            movies_searched = 0

            for row in rows:
                tid = row['tmdb_id']
                title = row['title']
                year = row['year']
                current_tier = row['best_tier']

                # Search indexer for this movie
                query = f"{title} {year}" if year else title
                try:
                    results = client._search_single(query, '2000', 100)
                    parsed = client._parse_items(results)
                except Exception as e:
                    if isinstance(e, IndexerSkipped):
                        # Tracker guard or backoff: stop here and leave the
                        # rest unsearched so the next run picks them up.
                        logger.info(f"Upgrade batch paused at '{title}': {e}")
                        break
                    logger.warning(f"Indexer search failed for '{title}': {e}")
                    conn.execute("""
                        INSERT OR REPLACE INTO upgrade_results
                        (tmdb_id, has_upgrade, searched_at)
                        VALUES (?, 0, ?)
                    """, (tid, datetime.now().isoformat()))
                    movies_searched += 1
                    continue

                # Find best upgrade from results
                best = None
                best_tier = current_tier

                for item in parsed:
                    item_title = _normalize_title(item.get('title', ''))
                    if item_title and _normalize_title(title) not in item_title \
                            and item_title not in _normalize_title(title):
                        continue

                    t_label, t_tier = _classify_tracker_quality(
                        item.get('resolution', ''),
                        item.get('torrent_name', ''))

                    if t_tier > best_tier:
                        best_tier = t_tier
                        best = {**item, '_label': t_label, '_tier': t_tier}
                    elif t_tier == best_tier and best \
                            and item.get('seeders', 0) > best.get('seeders', 0):
                        best = {**item, '_label': t_label, '_tier': t_tier}

                if best:
                    upgrade_type = 'other'
                    bl = best['_label']
                    if '2160p' in bl and 'Remux' in bl:
                        upgrade_type = '4k_remux'
                    elif '2160p' in bl:
                        upgrade_type = '4k'
                    elif '1080p' in bl and 'Remux' in bl:
                        upgrade_type = '1080p_remux'

                    conn.execute("""
                        INSERT OR REPLACE INTO upgrade_results
                        (tmdb_id, best_quality, best_tier, best_seeders,
                         best_size_gb, best_tracker, best_torrent_name,
                         upgrade_type, has_upgrade, searched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """, (
                        tid, best['_label'], best['_tier'],
                        best.get('seeders', 0), best.get('size_gb', 0),
                        best.get('tracker', ''), best.get('torrent_name', ''),
                        upgrade_type, datetime.now().isoformat(),
                    ))
                    upgrades_found += 1
                else:
                    conn.execute("""
                        INSERT OR REPLACE INTO upgrade_results
                        (tmdb_id, has_upgrade, searched_at)
                        VALUES (?, 0, ?)
                    """, (tid, datetime.now().isoformat()))

                movies_searched += 1
                time.sleep(1)

            conn.commit()
            elapsed = round(time.time() - start, 1)

            conn.execute("""
                INSERT INTO batch_log
                (movies_searched, upgrades_found, duration_seconds)
                VALUES (?, ?, ?)
            """, (movies_searched, upgrades_found, elapsed))
            conn.commit()

            logger.info(
                f"Upgrade batch complete: searched {movies_searched}, "
                f"found {upgrades_found} upgrades ({elapsed}s)")

            return {
                'status': 'ok',
                'movies_searched': movies_searched,
                'upgrades_found': upgrades_found,
                'duration_seconds': elapsed,
            }

        finally:
            conn.close()

    def get_upgrades(self, quality: Optional[str] = None,
                     sort: str = 'title',
                     limit: int = 500) -> Dict:
        """Get all found upgrades from the persistent database.

        Joins against the best copy across all sections for each movie.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            conditions = [
                "ur.has_upgrade = 1",
                "ur.grabbed_at IS NULL",
                "ur.dismissed_at IS NULL",
            ]
            params: list = []

            if quality:
                conditions.append("ur.upgrade_type = ?")
                params.append(quality)

            where = " AND ".join(conditions)

            order = {
                'seeders': 'ur.best_seeders DESC',
                'title': 'best.title ASC',
                'size': 'ur.best_size_gb ASC',
                'newest': 'ur.searched_at DESC',
            }.get(sort, 'best.title ASC')

            rows = conn.execute(f"""
                SELECT best.tmdb_id, best.title, best.year, best.poster_path,
                       best.rating_key, best.section_name,
                       best.current_quality, best.current_tier,
                       best.current_size_gb, best.current_codec,
                       best.sections_owned,
                       ur.best_quality, ur.best_tier, ur.best_seeders,
                       ur.best_size_gb, ur.best_tracker, ur.best_torrent_name,
                       ur.upgrade_type, ur.searched_at,
                       ur.rt_critic, ur.rt_audience
                FROM (
                    SELECT tmdb_id, title, year, poster_path, rating_key,
                           current_quality, current_tier, current_size_gb,
                           current_codec, section_name,
                           GROUP_CONCAT(DISTINCT section_name) AS sections_owned
                    FROM library_movies
                    WHERE (tmdb_id, current_tier) IN (
                        SELECT tmdb_id, MAX(current_tier)
                        FROM library_movies
                        GROUP BY tmdb_id
                    )
                    GROUP BY tmdb_id
                ) best
                JOIN upgrade_results ur ON best.tmdb_id = ur.tmdb_id
                WHERE {where}
                ORDER BY {order}
                LIMIT ?
            """, params + [limit]).fetchall()

            upgrades = [dict(r) for r in rows]

            # Stats: count unique movies across all sections
            total_unique = conn.execute(
                "SELECT COUNT(DISTINCT tmdb_id) FROM library_movies"
            ).fetchone()[0]

            total_searched = conn.execute(
                "SELECT COUNT(*) FROM upgrade_results").fetchone()[0]

            total_upgrades = conn.execute(
                "SELECT COUNT(*) FROM upgrade_results WHERE has_upgrade = 1"
            ).fetchone()[0]

            total_grabbed = conn.execute(
                "SELECT COUNT(*) FROM upgrade_results WHERE grabbed_at IS NOT NULL"
            ).fetchone()[0]

            return {
                'upgrades': upgrades,
                'stats': {
                    'total_in_library': total_unique,
                    'total_searched': total_searched,
                    'total_upgrades': total_upgrades,
                    'total_grabbed': total_grabbed,
                    'remaining': max(0, total_unique - total_searched),
                    'progress_pct': round(
                        total_searched / total_unique * 100, 1
                    ) if total_unique else 0,
                },
            }

        finally:
            conn.close()

    def get_progress(self) -> Dict:
        """Quick progress check without fetching full upgrade list."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            total = conn.execute(
                "SELECT COUNT(DISTINCT tmdb_id) FROM library_movies"
            ).fetchone()[0]

            searched = conn.execute(
                "SELECT COUNT(*) FROM upgrade_results").fetchone()[0]

            upgrades = conn.execute(
                "SELECT COUNT(*) FROM upgrade_results WHERE has_upgrade = 1"
            ).fetchone()[0]

            return {
                'total_in_library': total,
                'total_searched': searched,
                'total_upgrades': upgrades,
                'remaining': max(0, total - searched),
                'progress_pct': round(searched / total * 100, 1) if total else 0,
                'is_running': self._running,
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # New Releases scan
    # ------------------------------------------------------------------

    def scan_new_releases(self, explore_cache=None) -> Dict:
        """Pull all remuxes from the explore cache (which already has the
        full Prowlarr sweep) and cross-reference against the library.

        Finds recently uploaded remuxes for movies the user already owns
        at a lower quality tier. Sorted by pub_date (newest first).
        """
        with self.lock:
            if self._running:
                return {'status': 'already_running'}
            self._running = True

        try:
            return self._do_new_releases_scan(explore_cache)
        finally:
            with self.lock:
                self._running = False

    def _do_new_releases_scan(self, explore_cache=None) -> Dict:
        """Scan recent 4K remuxes from trackers (via explore cache) and find
        upgrade opportunities in the library.

        Logic:
        - Pull 4K remuxes uploaded in the last 7 days from the explore cache
        - Check if each one is already in the library at a lower quality
        - Present those as upgrade candidates
        - Only 4K remuxes — that's the point of this tab
        - Max 12 months old to prevent stale results
        """
        start = time.time()

        if not explore_cache:
            return {'status': 'error', 'message': 'No explore cache available'}

        # Determine lookback window:
        #   Initial scan (no existing results) → last 2 months
        #   Subsequent scans → last 24 hours (just pick up new uploads)
        conn_check = sqlite3.connect(str(self.db_path))
        try:
            existing = conn_check.execute("SELECT COUNT(*) FROM new_upgrades").fetchone()[0]
        finally:
            conn_check.close()

        if existing == 0:
            lookback_days = 60  # initial scan: 2 months
            label = '2 months'
        else:
            lookback_days = 1   # subsequent: previous day
            label = '24 hours'

        # Pull 4K remuxes sorted newest first
        all_remuxes = explore_cache.query_top_movies(
            quality='4k_remux', sort='newest', limit=500, min_seeders=1)

        # Filter to lookback window
        cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()
        recent_remuxes = [r for r in all_remuxes
                          if r.get('pub_date', '') >= cutoff]

        logger.info(f"New releases scan: {len(recent_remuxes)} 4K remuxes in last {label} "
                     f"(from {len(all_remuxes)} total in cache)")

        if not recent_remuxes and existing == 0:
            return {
                'status': 'ok',
                'remuxes_found': 0,
                'matches': 0,
            }

        # Build library lookup
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            lib_rows = conn.execute("""
                SELECT lm.tmdb_id, lm.title, lm.year, lm.poster_path,
                       lm.rating_key, lm.current_quality, lm.current_tier,
                       GROUP_CONCAT(DISTINCT lm.section_name) AS sections_owned
                FROM library_movies lm
                WHERE (lm.tmdb_id, lm.current_tier) IN (
                    SELECT tmdb_id, MAX(current_tier)
                    FROM library_movies
                    GROUP BY tmdb_id
                )
                GROUP BY lm.tmdb_id
            """).fetchall()

            # tmdb_id -> movie dict (primary match)
            lib_by_tmdb: Dict[str, Dict] = {}
            # normalized title -> list of movies (fallback)
            lib_by_title: Dict[str, List[Dict]] = {}
            for row in lib_rows:
                d = dict(row)
                lib_by_tmdb[d['tmdb_id']] = d
                key = _normalize_title(d['title'])
                if key:
                    lib_by_title.setdefault(key, []).append(d)

            # Initial scan: clear and rebuild. Subsequent: keep existing, add new.
            if existing == 0:
                conn.execute("DELETE FROM new_upgrades")

            matches = 0
            now = datetime.now().isoformat()

            for remux in recent_remuxes:
                t_label, t_tier = _classify_tracker_quality(
                    remux.get('resolution', ''),
                    remux.get('torrent_name', ''))

                # Match by tmdb_id first (most reliable)
                r_tmdb = str(remux.get('tmdb_id', '')) if remux.get('tmdb_id') else ''
                matched_movie = None

                if r_tmdb and r_tmdb in lib_by_tmdb:
                    lm = lib_by_tmdb[r_tmdb]
                    if t_tier > lm['current_tier']:
                        matched_movie = lm

                # Fallback: title matching
                if not matched_movie:
                    r_title = _normalize_title(remux.get('title', ''))
                    r_year = remux.get('year', '')
                    if r_title:
                        for lib_key, lib_movies in lib_by_title.items():
                            if r_title in lib_key or lib_key in r_title:
                                for lm in lib_movies:
                                    if r_year and lm['year'] and r_year != lm['year']:
                                        continue
                                    if t_tier > lm['current_tier']:
                                        matched_movie = lm
                                        break
                            if matched_movie:
                                break

                if not matched_movie:
                    continue

                upgrade_type = '4k_remux'

                tmdb_poster = remux.get('poster_url', '') or ''

                conn.execute("""
                    INSERT OR REPLACE INTO new_upgrades
                    (tmdb_id, title, year, poster_path, tmdb_poster,
                     rating_key, sections_owned, current_quality,
                     current_tier, best_quality, best_tier, best_seeders,
                     best_size_gb, best_tracker, best_torrent_name,
                     upgrade_type, pub_date, discovered_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    matched_movie['tmdb_id'],
                    matched_movie['title'],
                    matched_movie['year'],
                    matched_movie['poster_path'],
                    tmdb_poster,
                    matched_movie['rating_key'],
                    matched_movie['sections_owned'],
                    matched_movie['current_quality'],
                    matched_movie['current_tier'],
                    t_label, t_tier,
                    remux.get('seeders', 0),
                    remux.get('size_gb', 0),
                    remux.get('tracker', ''),
                    remux.get('torrent_name', ''),
                    upgrade_type,
                    remux.get('pub_date', ''),
                    now,
                ))
                matches += 1

            conn.commit()

            # Enrich RT scores for new_upgrades that don't have them
            self._enrich_new_upgrades_rt(conn)

            conn.commit()
            elapsed = round(time.time() - start, 1)
            logger.info(
                f"New releases scan complete: {len(recent_remuxes)} recent remuxes checked, "
                f"{matches} upgrade matches ({elapsed}s)")

            return {
                'status': 'ok',
                'remuxes_found': len(recent_remuxes),
                'matches': matches,
                'duration_seconds': elapsed,
            }
        finally:
            conn.close()

    def _enrich_new_upgrades_rt(self, conn):
        """Fetch RT scores for new_upgrades that are missing them."""
        try:
            from app.rt_scraper import rt_scraper
        except Exception:
            return

        # Ensure rt columns exist (migration)
        try:
            conn.execute("ALTER TABLE new_upgrades ADD COLUMN rt_critic INTEGER")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE new_upgrades ADD COLUMN rt_audience INTEGER")
        except Exception:
            pass

        rows = conn.execute(
            "SELECT tmdb_id, title, year FROM new_upgrades WHERE rt_critic IS NULL"
        ).fetchall()

        for tmdb_id, title, year in rows:
            try:
                year_int = int(year) if year and str(year).isdigit() else None
                rt_data = rt_scraper.get_reviews(title, year=year_int, max_reviews=0)
                critic = rt_data.get('critic_score')
                audience = rt_data.get('audience_score')
                conn.execute(
                    "UPDATE new_upgrades SET rt_critic = ?, rt_audience = ? WHERE tmdb_id = ?",
                    (critic, audience, tmdb_id),
                )
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"RT lookup failed for new upgrade '{title}': {e}")
                # Set to 0 so we don't retry
                conn.execute(
                    "UPDATE new_upgrades SET rt_critic = 0, rt_audience = 0 WHERE tmdb_id = ?",
                    (tmdb_id,),
                )

    # ------------------------------------------------------------------
    # Rotten Tomatoes enrichment
    # ------------------------------------------------------------------

    def enrich_rt_scores(self, limit: int = 50) -> Dict:
        """Fetch RT critic + audience scores for upgrades that don't have them yet.

        Uses the existing RTScraper singleton. Processes up to `limit` movies
        per call to avoid hammering RT. Scores are cached in upgrade_results.
        """
        from app.rt_scraper import rt_scraper
        conn = sqlite3.connect(str(self.db_path))
        try:
            # Get upgrades missing RT scores (never fetched or >7 days stale)
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()
            rows = conn.execute("""
                SELECT tmdb_id, best_quality
                FROM upgrade_results
                WHERE has_upgrade = 1
                  AND grabbed_at IS NULL
                  AND dismissed_at IS NULL
                  AND (rt_fetched_at IS NULL OR rt_fetched_at < ?)
                LIMIT ?
            """, (cutoff, limit)).fetchall()

            if not rows:
                return {'status': 'ok', 'enriched': 0, 'message': 'All upgrades have RT scores'}

            # Get title/year for each tmdb_id from library_movies
            tmdb_ids = [r[0] for r in rows]
            placeholders = ','.join('?' * len(tmdb_ids))
            title_rows = conn.execute(f"""
                SELECT tmdb_id, title, year FROM library_movies
                WHERE tmdb_id IN ({placeholders})
                GROUP BY tmdb_id
            """, tmdb_ids).fetchall()
            title_map = {r[0]: (r[1], r[2]) for r in title_rows}

            enriched = 0
            for tmdb_id, _ in rows:
                info = title_map.get(tmdb_id)
                if not info:
                    continue
                title, year = info
                year_int = int(year) if year and year.isdigit() else None

                try:
                    rt_data = rt_scraper.get_reviews(
                        title, year=year_int, max_reviews=0)
                    critic = rt_data.get('critic_score')
                    audience = rt_data.get('audience_score')

                    conn.execute("""
                        UPDATE upgrade_results
                        SET rt_critic = ?, rt_audience = ?,
                            rt_fetched_at = CURRENT_TIMESTAMP
                        WHERE tmdb_id = ?
                    """, (critic, audience, tmdb_id))
                    enriched += 1
                    # Be polite to RT
                    time.sleep(0.5)
                except Exception as e:
                    logger.warning(f"RT lookup failed for '{title}': {e}")
                    # Mark as fetched so we don't retry immediately
                    conn.execute("""
                        UPDATE upgrade_results
                        SET rt_fetched_at = CURRENT_TIMESTAMP
                        WHERE tmdb_id = ?
                    """, (tmdb_id,))

            conn.commit()
            logger.info(f"RT enrichment complete: {enriched}/{len(rows)} movies scored")
            return {'status': 'ok', 'enriched': enriched, 'total_processed': len(rows)}
        finally:
            conn.close()

    def get_top_rated_upgrades(self, quality: str = '4k_remux',
                               min_rt_score: int = 70,
                               limit: int = 20) -> Dict:
        """Get upgrades sorted by RT score.

        Returns upgrades that have RT scores above the threshold,
        sorted by combined RT score (critic + audience average).
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT best.tmdb_id, best.title, best.year, best.poster_path,
                       best.rating_key, best.section_name,
                       best.current_quality, best.current_tier,
                       best.sections_owned,
                       ur.best_quality, ur.best_tier, ur.best_seeders,
                       ur.best_size_gb, ur.best_tracker, ur.best_torrent_name,
                       ur.upgrade_type, ur.rt_critic, ur.rt_audience,
                       ur.rt_fetched_at
                FROM (
                    SELECT tmdb_id, title, year, poster_path, rating_key,
                           current_quality, current_tier, section_name,
                           GROUP_CONCAT(DISTINCT section_name) AS sections_owned
                    FROM library_movies
                    WHERE (tmdb_id, current_tier) IN (
                        SELECT tmdb_id, MAX(current_tier)
                        FROM library_movies
                        GROUP BY tmdb_id
                    )
                    GROUP BY tmdb_id
                ) best
                JOIN upgrade_results ur ON best.tmdb_id = ur.tmdb_id
                WHERE ur.has_upgrade = 1
                  AND ur.grabbed_at IS NULL
                  AND ur.dismissed_at IS NULL
                  AND ur.upgrade_type = ?
                  AND ur.rt_fetched_at IS NOT NULL
                  AND (
                      COALESCE(ur.rt_critic, 0) >= ?
                      OR COALESCE(ur.rt_audience, 0) >= ?
                  )
                ORDER BY (COALESCE(ur.rt_critic, 0) + COALESCE(ur.rt_audience, 0)) / 2 DESC,
                         ur.best_seeders DESC
                LIMIT ?
            """, (quality, min_rt_score, min_rt_score, limit)).fetchall()

            return {
                'upgrades': [dict(r) for r in rows],
                'total': len(rows),
                'filters': {
                    'quality': quality,
                    'min_rt_score': min_rt_score,
                },
            }
        finally:
            conn.close()

    def get_new_upgrades(self, limit: int = 100) -> Dict:
        """Get newly discovered upgrades sorted by upload date (newest first)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT tmdb_id, title, year, poster_path, tmdb_poster,
                       rating_key, sections_owned, current_quality,
                       current_tier, best_quality, best_tier, best_seeders,
                       best_size_gb, best_tracker, best_torrent_name,
                       upgrade_type, pub_date, discovered_at,
                       rt_critic, rt_audience
                FROM new_upgrades
                ORDER BY pub_date DESC, title ASC
                LIMIT ?
            """, (limit,)).fetchall()

            return {
                'upgrades': [dict(r) for r in rows],
                'total': len(rows),
            }
        finally:
            conn.close()


# Global instance -- initialized lazily from main.py
upgrade_finder: Optional[UpgradeFinder] = None
