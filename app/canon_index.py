"""
Canon Index — a durable "great films" ranking, built once and refreshed weekly.

Every existing discovery input in this app is recency- or heat-biased (Trakt
trending, Trakt anticipated, the Prowlarr latest feed). None of them will ever
surface Seven Samurai. This module is the missing counterweight: a stable,
opinionated list of films that are worth owning regardless of what dropped
this week.

Two sources, blended:

  1. A curated static seed (below) tagged by which canon lists a film appears
     on — AFI 100, Sight & Sound, Best Picture, Palme d'Or, National Film
     Registry, Criterion. Static beats scraping here: these lists barely move
     year to year, and a committed list can't break because a page redesigned.
  2. TMDb ratings/vote counts, pulled per film at build time, which separate
     "on one list" from "universally regarded".

Scoring deliberately rewards age. The user's read is that most new films are
not worth premium disk, so a pre-1980 title gets a bonus that a 2019 title
doesn't — otherwise TMDb's popularity skew drags the whole ranking modern.

Storage is its own SQLite file (data/canon.db). Nothing here writes to
upgrade_library.db or any automation's state; ownership is read from
upgrade_library.db READ-ONLY.
"""

import json
import logging
import math
import os
import re
import sqlite3
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

TMDB_BASE = 'https://api.themoviedb.org/3'

# Weight per canon list, tuned for "great films you'd actually put on" rather
# than "films critics rank highest". Those are different lists: the Sight &
# Sound poll's #1 is Jeanne Dielman, a 3.5-hour structuralist film nobody
# picks for movie night. So the critic-poll lists (sight_sound, palme,
# criterion) are treated as weak corroborating signals, while the
# audience-facing lists (imdb_top, afi100) carry the weight.
LIST_WEIGHTS = {
    'imdb_top': 26,       # audience canon — closest proxy for "watchable great"
    'afi100': 24,         # mainstream American canon
    'letterboxd': 20,     # modern cinephile-audience canon
    'best_picture': 16,
    'afi_genre': 14,      # AFI's Westerns/Sci-Fi/Gangster/etc. top tens
    'nfr': 10,
    'sight_sound': 10,    # critic polls skew hard to arthouse — kept weak
    'oscar_director': 10,
    'bp_nominee': 8,      # nominee is a much weaker signal than winner
    'tspdt': 8,           # aggregated critical canon; arthouse-leaning
    'palme': 6,
    'criterion': 5,
}

# Accessibility gate. TMDb vote count is the best available proxy for "people
# actually watch this". A film under this floor is a cinephile deep cut, not a
# Friday pick, and gets pushed down hard.
LOW_VOTE_FLOOR = 3000
LOW_VOTE_PENALTY = 40

# TMDb's ISO-639-1 code for English. The canon is full of foreign-language
# greats, and consumers that grab into an English-only Radarr profile have to
# be able to filter on this.
ENGLISH_LANG = 'en'

# Quality tiers, mirroring upgrade_finder's ladder so the two agree.
TIER_720P = 1
TIER_1080P = 2
TIER_1080P_REMUX = 3
TIER_2160P = 4
TIER_2160P_REMUX = 5


# ── Curated seed ──────────────────────────────────────────────────────────
# Lives in canon_seed.py — pure data, and long enough that keeping it here
# would bury the scoring logic.
from app.canon_seed import CANON_SEED  # noqa: E402


# ── Paths ─────────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent
    return Path(__file__).resolve().parent.parent / 'data'


def _db_path() -> Path:
    return _data_dir() / 'canon.db'


def _upgrade_db_path() -> Path:
    """upgrade_finder's DB. Opened READ-ONLY — this module never writes it."""
    return _data_dir() / 'upgrade_library.db'


# ── Schema ────────────────────────────────────────────────────────────────

def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS canon_films (
            tmdb_id       INTEGER PRIMARY KEY,
            title         TEXT NOT NULL,
            year          INTEGER,
            lists         TEXT,
            tmdb_rating   REAL,
            tmdb_votes    INTEGER,
            runtime       INTEGER,
            poster_path   TEXT,
            canon_score   REAL,
            original_language TEXT,
            resolved_at   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS unresolved (
            title TEXT, year INTEGER, reason TEXT
        )
    """)
    # Added after the first builds shipped. An install created before this has
    # the column missing rather than NULL, and every read below would raise.
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(canon_films)')}
    if 'original_language' not in cols:
        conn.execute('ALTER TABLE canon_films ADD COLUMN original_language TEXT')
        logger.info('canon.db: added original_language column')
    conn.commit()
    return conn


# ── Scoring ───────────────────────────────────────────────────────────────

RATING_WEIGHT = 4.0
VOTE_WEIGHT = 7.0
AGE_BONUS_PRE_1970 = 5.0
AGE_BONUS_PRE_1990 = 3.0


def compute_score(lists: List[str], rating: float, votes: int,
                  year: Optional[int]) -> float:
    """Blend list membership, TMDb consensus, reach, and a modest age bonus.

    Reach (vote count) is weighted heavily. Critical acclaim alone ranks Jeanne
    Dielman above Mulholland Drive; acclaim x reach does not. The age bonus
    stays small — the goal is "classics people watch", so a 1975 art film
    shouldn't outrank a 2001 one just for being older.
    """
    score = sum(LIST_WEIGHTS.get(l, 0) for l in lists)

    if rating:
        score += rating * RATING_WEIGHT
    if votes:
        # log10 keeps a blockbuster's vote count from bulldozing list
        # membership while still separating "widely seen" from "festival only".
        score += math.log10(max(10, votes)) * VOTE_WEIGHT
        if votes < LOW_VOTE_FLOOR:
            score -= LOW_VOTE_PENALTY

    if year:
        if year < 1970:
            score += AGE_BONUS_PRE_1970
        elif year < 1990:
            score += AGE_BONUS_PRE_1990

    return round(score, 2)


# ── Build ─────────────────────────────────────────────────────────────────

def _norm_title(t: str) -> str:
    """Lowercase, strip punctuation and leading articles for comparison."""
    t = (t or '').lower().strip()
    t = re.sub(r'[^a-z0-9 ]+', '', t)
    t = re.sub(r'^(the|a|an) ', '', t)
    return re.sub(r'\s+', ' ', t).strip()


# Making-of docs and companion pieces share a year and most of a title with the
# film they're about, and TMDb happily returns them. Never a canon entry.
_COMPANION_SUBSTRINGS = ('making of', 'behind the scenes', 'documentary about')
# Prefix-only: "Around Jeanne Dielman" is a companion piece, but
# "The Shop Around the Corner" is not — substring matching conflates them.
_COMPANION_PREFIXES = ('around ', 'notes on ', 'a look at ', 'revisiting ')


def _tmdb_lookup(session: requests.Session, api_key: str,
                 title: str, year: int) -> Optional[Dict[str, Any]]:
    """Resolve a (title, year) to a TMDb movie record.

    Title agreement is checked, not assumed. Picking the most-voted result in
    the right year is not enough: searching "Moon" (2009) returns The Twilight
    Saga: New Moon, which has vastly more votes and the same year. So
    candidates are scored on how closely the title actually matches, with vote
    count acting only as a tiebreak between equally good matches.
    """
    try:
        r = session.get(f'{TMDB_BASE}/search/movie', params={
            'api_key': api_key, 'query': title, 'include_adult': 'false',
        }, timeout=20)
        r.raise_for_status()
        results = r.json().get('results') or []
    except Exception as e:
        logger.warning(f'TMDb lookup failed for {title} ({year}): {e}')
        return None

    if not results:
        return None

    want = _norm_title(title)

    def title_ratio(m: Dict[str, Any]) -> float:
        """Best title agreement between the query and a candidate."""
        cand = _norm_title(m.get('title') or '')
        orig = _norm_title(m.get('original_title') or '')
        r = max(SequenceMatcher(None, want, cand).ratio(),
                SequenceMatcher(None, want, orig).ratio())
        # Subtitled releases ("Lagaan: Once Upon a Time in India") score badly
        # on whole-string ratio, so also compare against the candidate's
        # leading segment. Discounted, so a genuine full match still wins —
        # otherwise "Brick" would resolve to "Brick Vision".
        if want:
            r = max(r,
                    SequenceMatcher(None, want, cand[:len(want)]).ratio() * 0.85,
                    SequenceMatcher(None, want, orig[:len(want)]).ratio() * 0.85)
        return r

    def score(m: Dict[str, Any]) -> tuple:
        cand = _norm_title(m.get('title') or '')
        raw = (m.get('title') or '').lower()

        if any(mark in raw for mark in _COMPANION_SUBSTRINGS) or \
                any(cand.startswith(mark) for mark in _COMPANION_PREFIXES):
            return (-100.0, 0)

        # Fuzzy, not containment: containment can't tell "Colour"/"Color"
        # apart from a mismatch, and it wrongly accepts a short title swallowed
        # by a long one ("Moon" inside "The Twilight Saga: New Moon").
        title_pts = title_ratio(m) * 100

        d = (m.get('release_date') or '')[:4]
        cy = int(d) if d.isdigit() else 0
        delta = abs(cy - year) if cy else 99
        # A curated list knows the year exactly, so a candidate far off it is
        # simply a different film with the same name — searching "Intolerance"
        # returns six unrelated modern shorts before Griffith's 1916 epic.
        # This has to outweigh a perfect title match, not merely offset it.
        year_pts = 30 if delta == 0 else 20 if delta == 1 else 5 if delta <= 3 else -200

        return (title_pts + year_pts, m.get('vote_count') or 0)

    best = max(results, key=score)
    best_pts, _ = score(best)
    best_ratio = title_ratio(best)
    # Both gates must pass: a great title match in the wrong decade is as wrong
    # as a right-year match on a different film.
    if best_ratio < 0.6 or best_pts < 60:
        return None
    return best


def build_index(api_key: str, force: bool = False,
                progress_cb=None) -> Dict[str, Any]:
    """Resolve the seed list against TMDb and (re)write the canon table."""
    conn = _init_db()
    try:
        if force:
            # INSERT OR REPLACE is keyed on tmdb_id, so a row that resolved to
            # the WRONG film keeps its old id and survives a rebuild untouched.
            # A forced rebuild has to clear the table to drop those.
            conn.execute('DELETE FROM canon_films')
            conn.commit()

        # Collapse duplicate seed entries, unioning their list memberships.
        merged: Dict[tuple, set] = {}
        for title, year, lists in CANON_SEED:
            key = (title, year)
            merged.setdefault(key, set()).update(lists)

        session = requests.Session()
        resolved = 0
        skipped = 0
        unresolved: List[tuple] = []
        seen_ids = set()

        for i, ((title, year), lists) in enumerate(sorted(merged.items())):
            if progress_cb and i % 25 == 0:
                progress_cb(i, len(merged))

            hit = _tmdb_lookup(session, api_key, title, year)
            if not hit or not hit.get('id'):
                unresolved.append((title, year, 'no TMDb match'))
                continue

            tmdb_id = int(hit['id'])
            if tmdb_id in seen_ids:
                # Two seed rows resolved to the same film — merge memberships.
                row = conn.execute(
                    'SELECT lists FROM canon_films WHERE tmdb_id = ?', (tmdb_id,)
                ).fetchone()
                if row:
                    lists = set(lists) | set(json.loads(row['lists'] or '[]'))
            seen_ids.add(tmdb_id)

            rating = float(hit.get('vote_average') or 0)
            votes = int(hit.get('vote_count') or 0)
            rel_year = (hit.get('release_date') or '')[:4]
            rel_year_i = int(rel_year) if rel_year.isdigit() else year

            score = compute_score(sorted(lists), rating, votes, rel_year_i)

            conn.execute("""
                INSERT OR REPLACE INTO canon_films
                (tmdb_id, title, year, lists, tmdb_rating, tmdb_votes,
                 runtime, poster_path, canon_score, original_language,
                 resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                tmdb_id, hit.get('title') or title, rel_year_i,
                json.dumps(sorted(lists)), rating, votes, 0,
                hit.get('poster_path') or '', score,
                (hit.get('original_language') or '').strip().lower(),
            ))
            resolved += 1
            time.sleep(0.06)  # stay well inside TMDb's rate limit

        conn.execute('DELETE FROM unresolved')
        conn.executemany('INSERT INTO unresolved (title, year, reason) VALUES (?,?,?)',
                         unresolved)
        conn.commit()

        logger.info(f'Canon index built: {resolved} resolved, '
                    f'{len(unresolved)} unresolved, {skipped} skipped')
        return {
            'resolved': resolved,
            'unresolved': len(unresolved),
            'unresolved_titles': [f'{t} ({y})' for t, y, _ in unresolved],
            'total_seed': len(merged),
        }
    finally:
        conn.close()


def backfill_languages(api_key: str, limit: int = 2000) -> Dict[str, Any]:
    """Fill in `original_language` for rows that predate the column.

    A full `build_index` would also populate it, but that re-runs the whole
    fuzzy title match against TMDb — slow, and it can re-resolve a row that
    currently points at the right film. This asks TMDb for the ids we already
    hold, which cannot change what a row resolves to.

    Idempotent: rows with a language already set are skipped, so this is cheap
    to call on every run once it has completed once.
    """
    p = _db_path()
    if not p.exists():
        return {'updated': 0, 'remaining': 0, 'error': 'canon.db not built'}

    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(canon_films)')}
        if 'original_language' not in cols:
            conn.execute('ALTER TABLE canon_films ADD COLUMN original_language TEXT')
            conn.commit()

        rows = conn.execute("""
            SELECT tmdb_id, title FROM canon_films
            WHERE original_language IS NULL OR original_language = ''
            LIMIT ?
        """, (limit,)).fetchall()
        if not rows:
            return {'updated': 0, 'remaining': 0}

        logger.info(f'Canon: backfilling original_language for {len(rows)} films')
        session = requests.Session()
        updated = 0
        failed = 0
        for r in rows:
            try:
                resp = session.get(f"{TMDB_BASE}/movie/{int(r['tmdb_id'])}",
                                   params={'api_key': api_key}, timeout=20)
                resp.raise_for_status()
                lang = (resp.json().get('original_language') or '').strip().lower()
            except Exception as e:
                logger.warning(f"Canon: language lookup failed for {r['title']}: {e}")
                failed += 1
                lang = ''
            if lang:
                conn.execute(
                    'UPDATE canon_films SET original_language = ? WHERE tmdb_id = ?',
                    (lang, int(r['tmdb_id'])))
                updated += 1
            time.sleep(0.06)  # stay well inside TMDb's rate limit
        conn.commit()

        remaining = conn.execute("""
            SELECT COUNT(*) FROM canon_films
            WHERE original_language IS NULL OR original_language = ''
        """).fetchone()[0]
        logger.info(f'Canon: language backfill wrote {updated}, '
                    f'{failed} lookups failed, {remaining} still unknown')
        return {'updated': updated, 'failed': failed, 'remaining': int(remaining)}
    finally:
        conn.close()


# ── Read API ──────────────────────────────────────────────────────────────

def _owned_tiers() -> Dict[int, Dict[str, Any]]:
    """tmdb_id → best owned tier, read READ-ONLY from upgrade_finder's DB.

    Returns {} if that DB isn't present or hasn't been synced yet, in which
    case every canon film reads as unowned — the caller decides what to do
    with that (movie_night refuses to run rather than mass-grabbing).
    """
    p = _upgrade_db_path()
    if not p.exists():
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT tmdb_id,
                   MAX(current_tier) AS best_tier,
                   COUNT(*)          AS copies
            FROM library_movies
            GROUP BY tmdb_id
        """).fetchall()
        for r in rows:
            try:
                out[int(r['tmdb_id'])] = {
                    'tier': int(r['best_tier'] or 0),
                    'copies': int(r['copies'] or 0),
                }
            except (TypeError, ValueError):
                continue
        conn.close()
    except Exception as e:
        logger.warning(f'Could not read ownership from upgrade_library.db: {e}')
    return out


def owned_tmdb_ids() -> set:
    """Every tmdb_id in the Plex movie library, per upgrade_finder's DB.

    Despite living in canon_index this is the *whole* library, not the canon
    subset — it is simply where the READ-ONLY reader for that DB lives. Other
    automations use it to answer "do I already have this film".

    An empty set means the DB is missing or unsynced, not that the library is
    empty. Callers that decide what to download from this must tell those apart
    or they will try to grab everything.
    """
    return set(_owned_tiers().keys())


def get_canon(limit: int = 2000, with_ownership: bool = True) -> List[Dict[str, Any]]:
    """Canon films ranked best-first, optionally annotated with owned tier."""
    p = _db_path()
    if not p.exists():
        return []
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT * FROM canon_films
            ORDER BY canon_score DESC, tmdb_votes DESC
            LIMIT ?
        """, (limit,)).fetchall()
    finally:
        conn.close()

    owned = _owned_tiers() if with_ownership else {}
    out = []
    for rank, r in enumerate(rows, start=1):
        d = dict(r)
        d['lists'] = json.loads(d.get('lists') or '[]')
        d['rank'] = rank
        # '' means "not looked up yet", which is not the same as "not English" —
        # callers that gate on language have to tell those apart.
        d['original_language'] = (d.get('original_language') or '').strip().lower()
        o = owned.get(int(d['tmdb_id']))
        d['owned'] = bool(o)
        d['owned_tier'] = o['tier'] if o else 0
        out.append(d)
    return out


def get_stats() -> Dict[str, Any]:
    """Coverage summary — how much of the canon is owned, and at what tier."""
    films = get_canon()
    if not films:
        return {'built': False, 'total': 0}
    owned = [f for f in films if f['owned']]
    missing = [f for f in films if not f['owned']]
    return {
        'built': True,
        'total': len(films),
        'owned': len(owned),
        'missing': len(missing),
        # Movie Night only grabs English-audio films, so "missing" on its own
        # overstates what it will ever pick up. Surface the real target count.
        'missing_english': len([f for f in missing
                                if f['original_language'] == ENGLISH_LANG]),
        'language_unknown': len([f for f in films if not f['original_language']]),
        'at_remux_or_better': len([f for f in owned if f['owned_tier'] >= TIER_1080P_REMUX]),
        'at_4k_remux': len([f for f in owned if f['owned_tier'] >= TIER_2160P_REMUX]),
        'below_remux': len([f for f in owned if 0 < f['owned_tier'] < TIER_1080P_REMUX]),
    }
