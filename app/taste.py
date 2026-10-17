"""
Taste — explicit verdicts on what you actually watched, and what to get next.

Every other signal in this app is inferred. Watch history says you pressed play;
it cannot say whether you were glad you did. TMDb similarity can find films
adjacent to one you finished, but has no way to learn that a whole cluster
should be avoided. This module collects the one signal that carries both:
a verdict you gave on purpose.

Flow, deliberately confirm-first — nothing is acquired without a tap, matching
the manual add workflow rather than the fire-and-forget automations:

    finished a film -> push notification -> /rate/<tmdb_id> -> verdict
                    -> if positive, five unowned candidates -> pick one -> grab

The verdict store is the durable asset here, more than the recommendation. A
"disliked" is information nothing else in the stack can produce, and it
accumulates whether or not a grab ever follows.

Self-contained: own DB (data/taste.db), READ-ONLY reads of upgrade_library.db
for ownership and the rating_key -> tmdb_id bridge.
"""

import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TMDB_BASE = 'https://api.themoviedb.org/3'

# Binary on purpose. A middle option is the one most people reach for to avoid
# deciding, and a shrug is not a signal any automation can act on — it would
# just dilute the only deliberate preference data the app collects.
VERDICTS = ('liked', 'disliked')

# Candidate gates. Same shape as Genre Roulette's, and for the same reason: a
# recommendation is only useful if it is actually gettable and actually watchable.
_MIN_VOTES = 300
_MIN_RATING = 6.0
_ENGLISH = 'en'

# How many candidates the rate screen offers. Five is enough to feel like a
# choice and few enough to fit on a phone without scrolling past the fold.
_DEFAULT_PICKS = 5

# A single watch is the normal case for a 24-hour window, and one seed cannot be
# ranked by cross-seed agreement — every candidate would tie. TMDb returns
# recommendations in relevance order, so position is the signal that breaks it.
_RELEVANCE_DECAY = 0.12
_SIMILAR_WEIGHT = 0.5           # /similar is looser than /recommendations
_QUALITY_FLOOR = 0.65           # quality prior can only modulate, not dominate

# Grace period before asking. Firing at the moment the credits roll is asking
# while the user is still in the room with it.
_DEFAULT_PROMPT_DELAY_MINUTES = 30
# How far back a prompt is worth sending at all. Past this the film is not
# fresh enough to have an opinion about on demand.
_DEFAULT_WINDOW_HOURS = 24


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
    return _data_dir() / 'taste.db'


def _upgrade_db_path() -> Path:
    return _data_dir() / 'upgrade_library.db'


# ── Store ─────────────────────────────────────────────────────────────────

def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            account_id   INTEGER NOT NULL,
            tmdb_id      INTEGER NOT NULL,
            verdict      TEXT    NOT NULL,
            title        TEXT,
            year         INTEGER,
            rated_at     TEXT    NOT NULL,
            watched_at   INTEGER,
            PRIMARY KEY (account_id, tmdb_id)
        )
    """)
    # What a verdict led to, so the same suggestion isn't offered twice and the
    # log can say which rating produced which grab.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS suggestions (
            account_id   INTEGER NOT NULL,
            seed_tmdb_id INTEGER NOT NULL,
            tmdb_id      INTEGER NOT NULL,
            title        TEXT,
            year         INTEGER,
            offered_at   TEXT,
            grabbed_at   TEXT,
            detail       TEXT,
            PRIMARY KEY (account_id, tmdb_id)
        )
    """)
    # One row per prompt sent, so a notification is never sent twice for the
    # same film and the scheduler can be safely re-run.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompts (
            account_id INTEGER NOT NULL,
            tmdb_id    INTEGER NOT NULL,
            sent_at    TEXT    NOT NULL,
            PRIMARY KEY (account_id, tmdb_id)
        )
    """)
    # Asked and declined. Kept separate from `prompts` (which only records that
    # a push went out) and from `ratings` (a dismissal is not a verdict —
    # writing one would corrupt the only deliberate signal here). Suppresses
    # both channels: having waved the question away once, you should not get it
    # again on the other device.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dismissals (
            account_id   INTEGER NOT NULL,
            tmdb_id      INTEGER NOT NULL,
            dismissed_at TEXT    NOT NULL,
            PRIMARY KEY (account_id, tmdb_id)
        )
    """)
    conn.commit()
    return conn


def dismiss(account_id: int, tmdb_id: int) -> Dict[str, Any]:
    """Record that the question was asked and waved away."""
    conn = _init_db()
    try:
        conn.execute("""INSERT OR REPLACE INTO dismissals
                        (account_id, tmdb_id, dismissed_at) VALUES (?, ?, ?)""",
                     (int(account_id), int(tmdb_id),
                      datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return {'ok': True, 'tmdb_id': int(tmdb_id)}


def dismissed_ids(account_id: int) -> set:
    conn = _init_db()
    try:
        return {int(r['tmdb_id']) for r in conn.execute(
            'SELECT tmdb_id FROM dismissals WHERE account_id=?',
            (int(account_id),))}
    finally:
        conn.close()


def record_verdict(account_id: int, tmdb_id: int, verdict: str,
                   title: str = '', year: Optional[int] = None,
                   watched_at: Optional[int] = None) -> Dict[str, Any]:
    """Store (or replace) a verdict. Replacing is allowed — opinions change."""
    if verdict not in VERDICTS:
        return {'error': f'verdict must be one of {", ".join(VERDICTS)}'}
    conn = _init_db()
    try:
        conn.execute("""
            INSERT INTO ratings (account_id, tmdb_id, verdict, title, year,
                                 rated_at, watched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, tmdb_id) DO UPDATE SET
                verdict=excluded.verdict, rated_at=excluded.rated_at
        """, (int(account_id), int(tmdb_id), verdict, title, year,
              datetime.now(timezone.utc).isoformat(), watched_at))
        conn.commit()
    finally:
        conn.close()
    logger.info(f'Taste: {verdict} recorded for tmdb={tmdb_id} '
                f'({title}) by account {account_id}')
    return {'ok': True, 'verdict': verdict, 'tmdb_id': int(tmdb_id)}


def get_verdict(account_id: int, tmdb_id: int) -> Optional[str]:
    conn = _init_db()
    try:
        r = conn.execute(
            'SELECT verdict FROM ratings WHERE account_id=? AND tmdb_id=?',
            (int(account_id), int(tmdb_id))).fetchone()
        return r['verdict'] if r else None
    finally:
        conn.close()


def rated_ids(account_id: int) -> Dict[int, str]:
    conn = _init_db()
    try:
        return {int(r['tmdb_id']): r['verdict'] for r in conn.execute(
            'SELECT tmdb_id, verdict FROM ratings WHERE account_id=?',
            (int(account_id),))}
    finally:
        conn.close()


def stats(account_id: Optional[int] = None) -> Dict[str, Any]:
    conn = _init_db()
    try:
        where, args = ('WHERE account_id=?', (int(account_id),)) if account_id is not None else ('', ())
        rows = conn.execute(
            f'SELECT verdict, COUNT(*) n FROM ratings {where} GROUP BY verdict',
            args).fetchall()
        out = {v: 0 for v in VERDICTS}
        for r in rows:
            out[r['verdict']] = r['n']
        out['total'] = sum(out[v] for v in VERDICTS)
        grabbed = conn.execute(
            f"SELECT COUNT(*) n FROM suggestions WHERE grabbed_at IS NOT NULL"
        ).fetchone()['n']
        out['grabbed_from_ratings'] = grabbed
        return out
    finally:
        conn.close()


def history(account_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    conn = _init_db()
    try:
        return [dict(r) for r in conn.execute("""
            SELECT tmdb_id, verdict, title, year, rated_at
            FROM ratings WHERE account_id=? ORDER BY rated_at DESC LIMIT ?
        """, (int(account_id), limit))]
    finally:
        conn.close()


# ── Plex bridge ───────────────────────────────────────────────────────────

def _rating_key_map() -> Dict[str, tuple]:
    """rating_key -> (tmdb_id, title, year) from the library DB.

    tmdb_id is stored as TEXT there, so it is cast explicitly — comparing it
    against an int from TMDb otherwise silently never matches.
    """
    p = _upgrade_db_path()
    if not p.exists():
        return {}
    try:
        conn = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        rows = conn.execute("""
            SELECT rating_key, CAST(tmdb_id AS INTEGER), title, year
            FROM library_movies WHERE rating_key IS NOT NULL
        """).fetchall()
        conn.close()
        return {str(r[0]): (int(r[1]), r[2], r[3]) for r in rows if r[1]}
    except Exception as e:
        logger.warning(f'Taste: could not read library bridge: {e}')
        return {}


def owned_ids() -> set:
    from app import canon_index
    return canon_index.owned_tmdb_ids()


def recent_watches(account_id: int, hours: int = _DEFAULT_WINDOW_HOURS,
                   settings=None) -> List[Dict[str, Any]]:
    """Films this Plex account finished inside a rolling window, newest first.

    Rolling rather than "today" on purpose. There is no timezone configured
    anywhere in this app and the container runs UTC, while a late-evening view
    lands on the following UTC date — a calendar day would put most of an
    evening's viewing on the wrong side of the boundary.
    """
    import requests
    from app.settings_manager import SettingsManager
    settings = settings or SettingsManager()

    url = (settings.get('plex.url') or '').rstrip('/')
    token = settings.get('plex.token') or ''
    if not url or not token:
        return []

    cutoff = int(time.time()) - hours * 3600
    try:
        r = requests.get(f'{url}/status/sessions/history/all',
                         params={'accountID': int(account_id),
                                 'viewedAt>': cutoff},
                         headers={'Accept': 'application/json', 'X-Plex-Token': token},
                         timeout=60, allow_redirects=False)
        r.raise_for_status()
        recs = r.json().get('MediaContainer', {}).get('Metadata', []) or []
    except Exception as e:
        logger.warning(f'Taste: Plex history fetch failed: {e}')
        return []

    bridge = _rating_key_map()
    out: Dict[int, Dict[str, Any]] = {}
    for x in recs:
        if x.get('type') != 'movie':
            continue
        # Plex honours accountID/viewedAt> as hints only on some versions, so
        # both are re-checked here rather than trusted.
        if int(x.get('accountID') or -1) != int(account_id):
            continue
        viewed = int(x.get('viewedAt') or 0)
        if viewed < cutoff:
            continue
        hit = bridge.get(str(x.get('ratingKey')))
        if not hit:
            continue
        tmdb_id, title, year = hit
        try:
            # library_movies.year is TEXT; the client expects a number.
            year = int(year) if year else None
        except (TypeError, ValueError):
            year = None
        prev = out.get(tmdb_id)
        if not prev or viewed > prev['watched_at']:
            out[tmdb_id] = {
                'tmdb_id': tmdb_id,
                'title': title or x.get('title') or '',
                'year': year,
                'watched_at': viewed,
                'rating_key': x.get('ratingKey'),
            }
    return sorted(out.values(), key=lambda f: -f['watched_at'])


def pending(account_id: int, hours: int = _DEFAULT_WINDOW_HOURS,
            settings=None) -> List[Dict[str, Any]]:
    """Recent watches with no verdict and no dismissal. Diagnostics only —
    prompting uses `latest_unrated`, which is deliberately narrower."""
    skip = set(rated_ids(account_id)) | dismissed_ids(account_id)
    return [w for w in recent_watches(account_id, hours, settings)
            if w['tmdb_id'] not in skip]


def latest_unrated(account_id: int, hours: int = _DEFAULT_WINDOW_HOURS,
                   settings=None) -> Optional[Dict[str, Any]]:
    """The single most recent watch, if it still needs a verdict.

    Only ever the newest film, never a backlog. An unanswered prompt is not a
    queue to work through: if three films went by unrated, being asked about the
    oldest is noise, and the answer is least useful precisely because it is the
    least fresh. Ignoring a prompt has to mean the question goes away, not that
    it stacks.

    Returns None when the newest watch is already rated or dismissed — even if
    older unrated ones exist behind it.
    """
    watches = recent_watches(account_id, hours, settings)
    if not watches:
        return None
    newest = watches[0]
    skip = set(rated_ids(account_id)) | dismissed_ids(account_id)
    return None if newest['tmdb_id'] in skip else newest


# ── Recommendations ───────────────────────────────────────────────────────

def _tmdb_key(settings=None) -> str:
    from app.settings_manager import SettingsManager
    from app.config import Config
    settings = settings or SettingsManager()
    return settings.get('apis.tmdb.api_key') or Config.TMDB_API_KEY or ''


def film_brief(tmdb_id: int, settings=None) -> Dict[str, Any]:
    """Just enough about one film to head the rate screen."""
    import requests
    key = _tmdb_key(settings)
    if not key:
        return {'tmdb_id': int(tmdb_id), 'title': '', 'error': 'no TMDb key'}
    try:
        m = requests.get(f'{TMDB_BASE}/movie/{int(tmdb_id)}',
                         params={'api_key': key}, timeout=20).json()
    except Exception as e:
        logger.warning(f'Taste: TMDb lookup failed for {tmdb_id}: {e}')
        return {'tmdb_id': int(tmdb_id), 'title': '', 'error': str(e)}
    return {
        'tmdb_id': int(tmdb_id),
        'title': m.get('title') or '',
        'year': int((m.get('release_date') or '0000')[:4] or 0) or None,
        'overview': m.get('overview') or '',
        'poster_path': m.get('poster_path') or '',
        'backdrop_path': m.get('backdrop_path') or '',
        'runtime': m.get('runtime'),
        'rating': round(float(m.get('vote_average') or 0), 1),
        'genres': [g['name'] for g in (m.get('genres') or [])],
    }


def _quality_prior(m: Dict[str, Any]) -> float:
    """0..1 blend of score and how many people scored it."""
    votes = max(1, int(m.get('vote_count') or 0))
    return (float(m.get('vote_average') or 0) / 10.0) * min(
        1.0, math.log10(votes) / 4.0)


def recommend(seed_tmdb_ids: List[int], limit: int = _DEFAULT_PICKS,
              account_id: Optional[int] = None,
              settings=None) -> Dict[str, Any]:
    """Unowned English-language candidates similar to the seeds, best first.

    Scoring has to work for a single seed, which is the normal case for a
    24-hour window. Cross-seed agreement is the strongest signal when it exists
    but produces a flat tie when there is only one seed, so TMDb's own relevance
    ordering carries the ranking and the quality prior only nudges it.
    """
    import requests

    key = _tmdb_key(settings)
    if not key:
        return {'error': 'TMDb API key not configured', 'results': []}
    if not seed_tmdb_ids:
        return {'results': []}

    owned = owned_ids()
    if not owned:
        return {'error': 'Ownership data unavailable — cannot tell what you '
                         'already have', 'results': []}

    # Never suggest something already rated: a disliked film coming back as a
    # recommendation is the clearest way to look like nothing was learned.
    rated = rated_ids(account_id) if account_id is not None else {}

    ses = requests.Session()
    score: Dict[int, float] = {}
    meta: Dict[int, Dict[str, Any]] = {}
    why: Dict[int, List[str]] = {}
    seed_titles: Dict[int, str] = {}

    for si, seed in enumerate(seed_tmdb_ids):
        seed_weight = 1.0 / (1 + si * 0.15)
        try:
            d = ses.get(f'{TMDB_BASE}/movie/{int(seed)}',
                        params={'api_key': key}, timeout=20).json()
            seed_titles[int(seed)] = d.get('title') or ''
        except Exception:
            seed_titles[int(seed)] = ''

        for endpoint, src_weight in (('recommendations', 1.0),
                                     ('similar', _SIMILAR_WEIGHT)):
            try:
                d = ses.get(f'{TMDB_BASE}/movie/{int(seed)}/{endpoint}',
                            params={'api_key': key}, timeout=20).json()
                results = d.get('results') or []
            except Exception as e:
                logger.warning(f'Taste: TMDb {endpoint} failed for {seed}: {e}')
                continue

            for pos, m in enumerate(results):
                mid = m.get('id')
                if not mid or mid in owned or mid in rated:
                    continue
                if (m.get('original_language') or '') != _ENGLISH:
                    continue
                if (m.get('vote_count') or 0) < _MIN_VOTES:
                    continue
                if (m.get('vote_average') or 0) < _MIN_RATING:
                    continue
                relevance = 1.0 / (1 + pos * _RELEVANCE_DECAY)
                q = _QUALITY_FLOOR + (1 - _QUALITY_FLOOR) * _quality_prior(m)
                score[mid] = score.get(mid, 0.0) + (
                    seed_weight * src_weight * relevance * q)
                meta[mid] = m
                st = seed_titles.get(int(seed)) or ''
                if st and st not in why.setdefault(mid, []):
                    why[mid].append(st)

    ranked = sorted(score.items(), key=lambda kv: -kv[1])[:limit]
    results = [{
        'tmdb_id': mid,
        'title': meta[mid].get('title') or '',
        'year': int((meta[mid].get('release_date') or '0000')[:4] or 0) or None,
        'overview': meta[mid].get('overview') or '',
        'poster_path': meta[mid].get('poster_path') or '',
        'rating': round(float(meta[mid].get('vote_average') or 0), 1),
        'votes': int(meta[mid].get('vote_count') or 0),
        'score': round(sc, 3),
        'because': why.get(mid, []),
    } for mid, sc in ranked]
    try:
        from app.imdb_ratings import enrich_imdb_ratings
        enrich_imdb_ratings(results, settings=settings, max_items=limit or 40, replace_rating=True)
    except Exception:
        for r in results:
            r['rating'] = None
    return {
        'results': results,
        'seed_titles': seed_titles,
        'pool': len(score),
    }


# ── Acquisition ───────────────────────────────────────────────────────────

def grab(tmdb_id: int, title: str, year: Optional[int],
         seed_tmdb_id: Optional[int] = None,
         account_id: Optional[int] = None,
         settings=None) -> Dict[str, Any]:
    """Acquire one suggested film, reusing Genre Roulette's proven grab path.

    Same standard bar, English-audio filter, multi-film-pack rejection and
    size-cap retry walk — a suggestion is not a different kind of download, so
    it should not have a different kind of code behind it.
    """
    from app.settings_manager import SettingsManager
    from app.indexer_api import get_indexer_client
    from app.radarr_api import RadarrAPI
    from app import tracker_selection, genre_roulette as gr

    settings = settings or SettingsManager()

    profile_id = 0
    try:
        profile_id = int(settings.get(
            'automation.taste.quality_profile_id', 0) or 0)
    except (TypeError, ValueError):
        profile_id = 0
    if not profile_id:
        # Falls back to Genre Roulette's profile: same quality target, and one
        # less thing to configure before this works at all.
        profile_id = gr._profile_id(settings)
    if not profile_id:
        return {'ok': False, 'error': 'No quality profile configured'}

    radarr_url = settings.get('radarr.url', '')
    radarr_key = settings.get('radarr.api_key', '')
    if not radarr_url or not radarr_key:
        return {'ok': False, 'error': 'Radarr not configured'}
    radarr = RadarrAPI(radarr_url, radarr_key)
    roots = radarr.get_root_folders()
    if not roots:
        return {'ok': False, 'error': 'No Radarr root folders'}
    root = roots[0].get('path', '')

    client = get_indexer_client(settings)
    if not client:
        return {'ok': False, 'error': 'No indexer configured'}
    plan = tracker_selection.plan(settings, client, gr.TRACKERS,
                                  gr.TRACKERS_SETTING)
    if plan['error']:
        return {'ok': False, 'error': plan['error']}

    film = {'title': title, 'year': year, 'tmdb_id': int(tmdb_id),
            'genre': '', 'rating': 0, 'votes': 0}
    found = gr._search_releases(client, film, plan['ids'], plan['names'])
    if not found['ok']:
        return {'ok': False, 'error': 'Indexer search failed'}

    ranked = gr._ranked(found['usable'], 0)
    if not ranked:
        return {'ok': False, 'error': 'No usable release found',
                'releases_found': found['raw'], 'rejects': found['rejects']}

    size_cap = None
    pushes = 0
    detail = 'no release attempted'
    ok = False
    chosen = None
    for release in ranked:
        if pushes >= gr._MAX_RELEASE_ATTEMPTS:
            break
        size_gb = release.get('size_gb') or 0
        if size_cap is not None and size_gb and size_gb > size_cap:
            continue
        pushes += 1
        ok, detail = gr._grab(film, release, radarr, profile_id, root)
        chosen = release
        if ok:
            break
        cap = gr._parse_size_cap(detail)
        if cap is not None:
            size_cap = cap

    conn = _init_db()
    try:
        conn.execute("""
            INSERT INTO suggestions (account_id, seed_tmdb_id, tmdb_id, title,
                                     year, offered_at, grabbed_at, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, tmdb_id) DO UPDATE SET
                grabbed_at=excluded.grabbed_at, detail=excluded.detail
        """, (int(account_id or 0), int(seed_tmdb_id or 0), int(tmdb_id),
              title, year, datetime.now(timezone.utc).isoformat(),
              datetime.now(timezone.utc).isoformat() if ok else None, detail))
        conn.commit()
    finally:
        conn.close()

    logger.info(f'Taste: grab {"ok" if ok else "failed"} for {title} — {detail}')
    return {
        'ok': ok, 'detail': detail,
        'release': ({'torrent_name': chosen.get('torrent_name'),
                     'size_gb': chosen.get('size_gb'),
                     'seeders': chosen.get('seeders'),
                     'tracker': chosen.get('tracker'),
                     'quality': f"{chosen.get('resolution') or '?'} "
                                f"{chosen.get('source') or ''}".strip()}
                    if chosen else None),
    }


# ── Prompting ─────────────────────────────────────────────────────────────

def _mark_prompted(account_id: int, tmdb_id: int) -> None:
    conn = _init_db()
    try:
        conn.execute("""INSERT OR IGNORE INTO prompts (account_id, tmdb_id, sent_at)
                        VALUES (?, ?, ?)""",
                     (int(account_id), int(tmdb_id),
                      datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def already_prompted(account_id: int, tmdb_id: int) -> bool:
    conn = _init_db()
    try:
        return conn.execute(
            'SELECT 1 FROM prompts WHERE account_id=? AND tmdb_id=?',
            (int(account_id), int(tmdb_id))).fetchone() is not None
    finally:
        conn.close()


def send_prompt(film: Dict[str, Any], push_user_id: int,
                account_id: int, settings=None) -> bool:
    """Push one 'rate this' notification, deep-linked to the rate screen.

    iOS Safari ignores notification `actions`, so there is no in-notification
    verdict button to offer; the deep link is the interaction. `actions` are
    still sent for any Android subscriber, where they do work — the field is
    simply ignored on platforms that don't support it.
    """
    from app.push_service import send_push_to_user
    from app.settings_manager import SettingsManager
    settings = settings or SettingsManager()

    year = f" ({film['year']})" if film.get('year') else ''
    ok = send_push_to_user(
        user_id=int(push_user_id),
        title=f"Rate {film['title']}{year}",
        body='Tap to rate it — you can pick something similar to add next.',
        tag=f"taste-rate-{film['tmdb_id']}",
        url=f"/rate/{film['tmdb_id']}",
        settings_manager=settings,
        # Two buttons is the platform maximum where they render at all, so the
        # middle "neutral" option lives only on the rate screen.
        actions=[{'action': 'verdict:liked', 'title': 'Liked'},
                 {'action': 'verdict:disliked', 'title': 'Disliked'}],
        extra={'tmdb_id': int(film['tmdb_id'])},
    )
    if ok:
        _mark_prompted(account_id, int(film['tmdb_id']))
    return ok
