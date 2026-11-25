"""v203 the movie scanner and grab (native movie watchlist, piece 3): test install only.

Every TICK_S, when LUMA_INSTANCE is test and Settings > Movies monitoring is on (on by default on test):
  * feed: every FEED_S (5 min; the guard's 'feed' purpose: no cap or slot, backoff and 429/Retry-After only).
    A release matching a watchlist movie is grabbed when it passes the movie's profile and the movie is
    available (v256: Settings > Movies availability). A title match that is not grabbed is logged with why.
  * searches: due as soon as a movie becomes available, is added to the watchlist, or the availability
    setting changes. After a real search with nothing to grab: 15 min, then 30, then 60. A tracker skip
    (spacing, backoff, 429, empty bucket) does not count as an attempt; the retry is the earliest
    tracker ready_at (movie bucket refill or purpose gap), not a tight 60 s loop. Automatic title
    searches use the guard's 'movie' purpose and their own token bucket (same burst/refill as TV,
    plus a 60 s min-gap) so they do not drain TV's budget; 429s, backoff, the global 120/h cap and
    each strict tracker's 20/h cap still hold. search_log still labels them 'backlog'.
  * grab: the best release by the movie's profile (allowed qualities in order, MB/min limits by TMDB runtime),
    then tracker priority, preferred groups (empty: ranking unchanged), PROPER, freeleech, seeders; the
    blocklist is skipped. Sent through TV's download client path (tv_grab.fetch_link, the same scoped
    qBittorrent add) with the movie category and tag ('movie'; LUMA_MOVIE_CATEGORY). Recorded in movies.db
    movie_grabs; a movie with a grab is never grabbed again.

Only test's own watchlist (movies.db) is read; Radarr is never read or written. 4K and Franchise Completer
stay off (instance.MOVIE_AUTOMATIONS); Roulette follows LUMA_TEST_ALLOW. No import or rename here (piece 4).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app import instance, movie_settings as ms, tv_match, tv_monitor, tv_release

logger = logging.getLogger(__name__)

TICK_S = 60
FEED_S = 5 * 60                   # Radarr-style RSS; the guard's 'feed' purpose has no cap, slot or bucket
FAST_S = 15 * 60
FAST_WINDOW_S = 6 * 3600
SLOW_S = 3600
SKIP_RETRY_S = 60                 # floor for a skip retry when the guard has no ready_at
RETRY_STEPS_S = (15 * 60, 30 * 60, 60 * 60)   # after a real search with nothing to grab
DATES_S = 12 * 3600
DEFAULT_RUNTIME = 110
MOVIE_CATEGORIES = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080]
SECTION = 'movies'
AUTO_PURPOSE = 'backlog'            # search_log's label (LOG_PURPOSE); guard purpose is 'movie' via section
JOB = 'movie_grab'                  # the instance job for a movie add on test (instance.scoped_add)
CATEGORY_ENV = 'LUMA_MOVIE_CATEGORY'              # the same name piece 4's import watches
DEFAULT_CATEGORY = 'movie'
TZ = ZoneInfo('America/New_York')
ACTIVE = ('grabbed', 'importing', 'imported')    # movie_grabs states; 'failed' may be grabbed again
NO_TRACKERS = 'No movie trackers are on'
OFF = 'Not available'

SCHEMA = """
CREATE TABLE IF NOT EXISTS movie_blocklist (
  id INTEGER PRIMARY KEY, tmdb_id INTEGER, release_title TEXT, info_hash TEXT, added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS movie_scan (
  tmdb_id INTEGER PRIMARY KEY, runtime INTEGER, digital_date TEXT, physical_date TEXT, dates_at REAL,
  released_at REAL, next_search_at REAL, last_search_at REAL, search_attempts INTEGER NOT NULL DEFAULT 0
);
"""


def ensure(conn) -> None:
    from app.movie_grabs import SCHEMA as GRABS     # piece 4's movie_grabs (one table, shared)
    conn.executescript(GRABS + SCHEMA)
    # v256: the cinema date (Radarr's In cinemas), for the availability setting; filled by refresh_dates
    if 'cinema_date' not in {r[1] for r in conn.execute('PRAGMA table_info(movie_scan)')}:
        conn.execute('ALTER TABLE movie_scan ADD COLUMN cinema_date TEXT')


def on_test() -> bool:
    """LUMA_INSTANCE is exactly 'test' (a fail-closed 'test' from a typo or an unset value does not count)."""
    return instance.is_test() and instance.opt_ins_honored()


def enabled() -> bool:
    """Test only, and Settings > Movies monitoring on."""
    if not on_test():
        return False
    try:
        return bool(ms.view()['monitoring'])
    except Exception:                                 # noqa: BLE001
        return False


def category() -> str:
    """v324: the qBit category AND tag for every movie grab (Genre Roulette, monitoring, manual release):
    exactly the one normal luma-test movie grabs use (qbit_owner.tag_name(), the tag the library mover
    imports). The old default 'movie' (LUMA_MOVIE_CATEGORY) was watched by nothing, so GR grabs sat unimported."""
    from app import qbit_owner
    return qbit_owner.tag_name()


# ------------------------------------------------------------------ trackers and profiles

def trackers() -> List[Dict[str, Any]]:
    """The trackers on for movies, in the movie priority order."""
    try:
        from app.tracker_search import get_search
        by_id = {c['id']: c for c in get_search().store.enabled_configs()}
    except Exception:                                 # noqa: BLE001
        return []
    return [by_id[t['id']] for t in ms.trackers() if t['enabled'] and t['id'] in by_id]


def _profile(conn, pid: Optional[int]) -> Optional[Dict[str, Any]]:
    r = None
    if pid is not None:
        r = conn.execute('SELECT * FROM profiles WHERE id = ?', (pid,)).fetchone()
    if r is None:
        r = conn.execute('SELECT * FROM profiles WHERE is_default = 1').fetchone()
    return ms._profile(r) if r else None


def _groups() -> Dict[str, int]:
    try:
        return {g.casefold(): i for i, g in enumerate(ms.view()['preferred_groups'])}
    except Exception:                                 # noqa: BLE001
        return {}


# ------------------------------------------------------------------ release names

_YEAR = re.compile(r'(?<![0-9])(19[0-9]{2}|20[0-9]{2})(?![0-9])')


def parse(title: str) -> Tuple[str, Optional[int]]:
    """(normalized movie title, year) of a release name: the text before its last year."""
    s = re.sub(r'[._]+', ' ', str(title or ''))
    years = list(_YEAR.finditer(s))
    for m in reversed(years):
        if m.start() > 0:
            return tv_match.normalize(s[:m.start()].strip(' ([-')), int(m.group(1))
    return tv_match.normalize(s), None


def matches(title: str, movie: Dict[str, Any]) -> bool:
    name, year = parse(title)
    if not name or name != tv_match.normalize(movie['title']):
        return False
    if movie.get('year') is None:
        return True
    # v208 (PM): a release with no year can't be told apart from another movie of the same name: never grabbed
    return year is not None and abs(int(year) - int(movie['year'])) <= 1


def candidates(items: List[Dict[str, Any]], movie: Dict[str, Any], profile: Optional[Dict[str, Any]],
               blocked: Optional[set] = None, order: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """The releases for *movie* its profile allows, best first; each {item, rank, ...}."""
    allowed = [i['id'] for i in (profile or {}).get('items') or [] if i.get('allowed')]
    lo, hi = (profile or {}).get('min_mb_per_min'), (profile or {}).get('max_mb_per_min')
    mins = int(movie.get('runtime') or DEFAULT_RUNTIME)
    pref = _groups()
    prio = {t: i for i, t in enumerate(order or [])}
    out = []
    for it in items:
        title = str(it.get('title') or '')
        if not title or title in (blocked or set()) or not matches(title, movie):
            continue
        if int(it.get('seeders') or 0) < 1:
            continue
        q = tv_monitor.quality_of(title)
        if q not in allowed:
            continue
        mb = (int(it.get('size') or 0) / 1048576) / max(1, mins)
        if (lo and mb < lo) or (hi and mb > hi):
            continue
        rel = tv_release.parse(title)
        out.append({'item': {**it, 'quality': q}, 'rank': allowed.index(q),
                    'key': (allowed.index(q), prio.get(it.get('indexerId'), len(prio)), -int(bool(rel.proper)),
                            pref.get((rel.group or '').casefold(), len(pref)), -int(bool(it.get('freeleech'))),
                            -int(it.get('seeders') or 0))})
    return sorted(out, key=lambda c: c['key'])


# ------------------------------------------------------------------ release dates (TMDB, never Radarr)

def _tmdb(tmdb_id: int) -> Optional[Dict[str, Any]]:
    import requests
    from app.digital_release_cache import TMDB_BASE, _api_key
    key = _api_key()
    if not key:
        return None
    try:
        r = requests.get(f'{TMDB_BASE}/movie/{tmdb_id}', params={'api_key': key, 'append_to_response': 'release_dates'},
                         timeout=10)
        return r.json() if r.ok else None
    except Exception:                                 # noqa: BLE001
        return None


def dates_of(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """{runtime, digital_date, physical_date, cinema_date}: the earliest US type 4 (digital), 5 (physical)
    and 2/3 (limited or wide theatrical) dates; the cinema date falls back to TMDB's release date."""
    out: Dict[str, Any] = {'runtime': None, 'digital_date': None, 'physical_date': None, 'cinema_date': None}
    if not payload:
        return out
    if isinstance(payload.get('runtime'), int) and payload['runtime'] > 0:
        out['runtime'] = payload['runtime']
    for c in (payload.get('release_dates') or {}).get('results') or []:
        if c.get('iso_3166_1') != 'US':
            continue
        for d in c.get('release_dates') or []:
            day = str(d.get('release_date') or '')[:10]
            k = {2: 'cinema_date', 3: 'cinema_date', 4: 'digital_date', 5: 'physical_date'}.get(d.get('type'))
            if k and len(day) == 10 and (out[k] is None or day < out[k]):
                out[k] = day
    if out['cinema_date'] is None:
        day = str(payload.get('release_date') or '')[:10]
        if len(day) == 10 and _day_start(day) is not None:
            out['cinema_date'] = day
    return out


def _day_start(day: Optional[str]) -> Optional[float]:
    try:
        return datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=TZ).timestamp() if day else None
    except ValueError:
        return None


def released_at(row: Dict[str, Any]) -> Optional[float]:
    """When the movie counts as released: the earlier of its digital and physical dates (start of that day, ET)."""
    got = [t for t in (_day_start(row.get('digital_date')), _day_start(row.get('physical_date'))) if t is not None]
    return min(got) if got else None


CINEMA_TO_RELEASE_S = 90 * 86400                  # Radarr: no digital or physical date: cinema + 90 days


def available_at(row: Dict[str, Any], mode: Optional[str] = None) -> Optional[float]:
    """v256 (PM, Radarr's minimum availability): when the movie may be grabbed or searched under *mode*
    (Settings > Movies availability). announced: always (0); released: the earlier of its digital and
    physical dates, else its cinema date + 90 days, else never (None); in_cinemas: its cinema date, or the
    released rule when that is earlier."""
    mode = mode or ms.availability()
    if mode == 'announced':
        return 0.0
    rel = released_at(row)
    cinema = _day_start(row.get('cinema_date'))
    released = rel if rel is not None else (cinema + CINEMA_TO_RELEASE_S if cinema is not None else None)
    if mode == 'in_cinemas':
        got = [t for t in (cinema, released) if t is not None]
        return min(got) if got else None
    return released


def available(row: Dict[str, Any], now: float, mode: Optional[str] = None) -> bool:
    at = available_at(row, mode)
    return at is not None and at <= now


def refresh_dates(now: float, fetch=None, limit: int = 20, only: Optional[List[int]] = None) -> int:
    """Read each watchlist movie's dates and runtime again every DATES_S (a few per pass).
    *only*: just these tmdb ids, ignoring the DATES_S age filter (a watchlist add)."""
    fetch = fetch or _tmdb
    with ms.connect() as conn:
        ensure(conn)
        if only:
            rows = [{'tmdb_id': tid} for tid in only]
        else:
            rows = [dict(r) for r in conn.execute(
                'SELECT w.tmdb_id FROM watchlist w LEFT JOIN movie_scan s ON s.tmdb_id = w.tmdb_id'
                ' WHERE s.dates_at IS NULL OR s.dates_at <= ? ORDER BY s.dates_at LIMIT ?', (now - DATES_S, limit))]
    n = 0
    for r in rows:
        d = dates_of(fetch(r['tmdb_id']))
        with ms.connect() as conn:
            conn.execute('INSERT INTO movie_scan (tmdb_id) VALUES (?) ON CONFLICT(tmdb_id) DO NOTHING', (r['tmdb_id'],))
            conn.execute('UPDATE movie_scan SET runtime = COALESCE(?, runtime), digital_date = COALESCE(?, digital_date),'
                         ' physical_date = COALESCE(?, physical_date), cinema_date = COALESCE(?, cinema_date),'
                         ' dates_at = ? WHERE tmdb_id = ?',
                         (d['runtime'], d['digital_date'], d['physical_date'], d['cinema_date'], now, r['tmdb_id']))
            row = dict(conn.execute('SELECT * FROM movie_scan WHERE tmdb_id = ?', (r['tmdb_id'],)).fetchone())
            at = released_at(row)
            if at is not None and (row['released_at'] is None or at < row['released_at']):
                conn.execute('UPDATE movie_scan SET released_at = ? WHERE tmdb_id = ?', (at, r['tmdb_id']))
                row['released_at'] = at
            row['tmdb_id'] = r['tmdb_id']
            if available(row, now) and int(row.get('search_attempts') or 0) == 0:
                nxt = row.get('next_search_at')
                if nxt is None:
                    _set(conn, r['tmdb_id'], next_search_at=now)
        n += 1
    return n


# ------------------------------------------------------------------ the watchlist's state

def wanted(conn, ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Watchlist movies with no active grab (with their scan row)."""
    ensure(conn)
    rows = [dict(r) for r in conn.execute(
        'SELECT w.*, s.runtime, s.digital_date, s.physical_date, s.cinema_date, s.released_at, s.next_search_at,'
        ' s.last_search_at,'
        ' s.search_attempts FROM watchlist w LEFT JOIN movie_scan s ON s.tmdb_id = w.tmdb_id'
        f" WHERE NOT EXISTS (SELECT 1 FROM movie_grabs g WHERE g.tmdb_id = w.tmdb_id AND g.state IN ({','.join('?' * len(ACTIVE))}))",
        ACTIVE)]
    return [r for r in rows if ids is None or r['tmdb_id'] in ids]


def _set(conn, tid: int, **vals: Any) -> None:
    conn.execute('INSERT INTO movie_scan (tmdb_id) VALUES (?) ON CONFLICT(tmdb_id) DO NOTHING', (tid,))
    conn.execute(f"UPDATE movie_scan SET {', '.join(f'{k} = ?' for k in vals)} WHERE tmdb_id = ?", (*vals.values(), tid))


def schedule(now: float, ids: Optional[List[int]] = None) -> int:
    """An available, never-successfully-searched movie is due now.
    A skip retry (next_search_at already set, including a bucket refill or 429 backoff)
    is left alone so the 60 s floor cannot loop while the bucket is empty. A time set
    under an earlier availability rule is kept; run_due skips it until it is available."""
    n = 0
    mode = ms.availability()
    with ms.connect() as conn:
        for m in wanted(conn, ids):
            at = available_at(m, mode)
            if at is None or at > now:
                continue
            nxt = m.get('next_search_at')
            if m['last_search_at'] is None and nxt is None:
                _set(conn, m['tmdb_id'], next_search_at=max(at, now - FAST_S))
                n += 1
    return n


def next_retry(m: Dict[str, Any], now: float, attempts: Optional[int] = None) -> float:
    """After a real search with nothing to grab: 15 min, then 30, then 60."""
    n = int(attempts if attempts is not None else (m.get('search_attempts') or 0))
    if n <= 1:
        return now + RETRY_STEPS_S[0]
    if n == 2:
        return now + RETRY_STEPS_S[1]
    return now + RETRY_STEPS_S[2]


def mark_due(tids: List[int], now: Optional[float] = None) -> int:
    """Set next_search_at to now for available movies in *tids* that have no active grab."""
    now = time.time() if now is None else now
    mode = ms.availability()
    n = 0
    with ms.connect() as conn:
        for m in wanted(conn, tids):
            if available(m, now, mode):
                _set(conn, m['tmdb_id'], next_search_at=now)
                n += 1
    return n


def _kick_if_running() -> None:
    """If the background loop is on, run a pass now (availability change, a watchlist add)."""
    if not _started['on'] or not enabled():
        return

    def go():
        try:
            tick()
        except Exception:  # noqa: BLE001
            logger.exception('Movie scanner kick failed')

    threading.Thread(target=go, name='movie-scanner-kick', daemon=True).start()


def on_availability_changed() -> int:
    """Settings > Movies availability changed: never-searched and skip-backoff movies that are now
    available become due, and a pass runs if the scanner loop is on."""
    n = schedule(time.time())
    _kick_if_running()
    return n


def on_watchlist_added(tid: int, now: Optional[float] = None) -> None:
    """A movie was added: refresh its dates and, if it is already available, search it now."""
    if not enabled() or not _started['on']:
        return
    now = time.time() if now is None else now
    try:
        refresh_dates(now, limit=1, only=[tid])
    except Exception:  # noqa: BLE001
        logger.exception('Movie dates refresh on add failed for %s', tid)
    if mark_due([tid], now):
        _kick_if_running()


def _existing_trackers(session_factory, kind, payload, h, now):
    """Repair trackers on a hash already recorded. None keeps the 'Already grabbed' result."""
    import requests
    from app import library_mover as mover, torrent_submit as submit
    info = submit.inspect(kind, payload)
    if info.private is not True and not info.has_usable:
        return None
    sess, url = mover._qbit_session(session_factory or requests.Session, now)
    if not sess:
        return submit.failure(submit.PENDING)
    try:
        with instance.scoped_add(JOB, category()) as scope:
            outcome = submit.settle(sess, url, h, kind, payload, duplicate=True, sent=False,
                                    permit=scope.allow_trackers)
    except requests.RequestException:
        return submit.failure(submit.PENDING)
    finally:
        mover._close(sess)
    if info.private is True and outcome != submit.CONFIRMED:
        return submit.failure(submit.PENDING if outcome == submit.SKIP else outcome)
    return submit.failure(outcome)


# ------------------------------------------------------------------ grab

def grab(item: Dict[str, Any], tmdb_id: int, origin: str, now: Optional[float] = None, session_factory=None,
         fetch=None, movie: Optional[Dict[str, Any]] = None, pending: bool = True) -> Dict[str, Any]:
    """Send *item* to qBittorrent with the movie category and tag, through TV's client path. Never twice.
    v252: *movie* ({title, year}) for a film not on the watchlist (Genre Roulette); *pending* False: a
    rate-limited link fails now instead of being queued for grab_retry."""
    import requests
    from app import library_mover as mover, tracker_sources, tv_grab
    now = time.time() if now is None else now
    if not on_test() or not instance.writes_allowed(JOB):
        return {'ok': False, 'error': OFF, 'blocked': True}
    with ms.connect() as conn:
        ensure(conn)
    from app import movie_grabs
    if any(g['state'] in ACTIVE for g in movie_grabs.for_movie(tmdb_id)):
        return {'ok': False, 'error': 'Already grabbed', 'existing': True}
    m = movie or _movie(tmdb_id) or {}
    from app import movie_dupes               # v204c (PM): never grab a movie the library or qBittorrent already has
    dup = movie_dupes.check(m.get('title'), m.get('year'), session_factory or requests.Session, now)   # v206: no title fails closed
    if dup:
        return {'ok': False, 'error': dup, 'duplicate': True, 'blocked': True}
    try:
        kind, payload = (fetch or (lambda u: tv_grab.fetch_link(u, automatic=origin == 'auto',
                                                                tokens_only=origin != 'auto')))(item.get('downloadUrl'))
    except tracker_sources.GrabError as e:
        if origin == 'auto' and pending and getattr(e, 'retry_at', None):
            from app import grab_retry               # v204b (PM): rate-limited automatic grab: pending, retried
            grab_retry.add('movie', item, e.retry_at, tmdb_id=tmdb_id)
            return {'ok': False, 'error': str(e), 'link': True, 'pending': True, 'blocked': True}
        return {'ok': False, 'error': str(e), 'link': True}
    h = tv_grab.info_hash(kind, payload)
    if not h:
        return {'ok': False, 'error': tv_grab.NO_HASH, 'link': True}
    with ms.connect() as conn:
        if conn.execute('SELECT 1 FROM movie_blocklist WHERE info_hash = ? OR (release_title = ? AND tmdb_id = ?)',
                        (h, item.get('title'), tmdb_id)).fetchone():
            return {'ok': False, 'error': 'Blocklisted'}
    prior = movie_grabs.by_hash(h)
    if prior and prior['state'] != 'failed':   # Ops: a failed/lost grab of the same hash is sent again (row revived)
        bad = _existing_trackers(session_factory or requests.Session, kind, payload, h, now)
        if bad:
            return bad
        return {'ok': False, 'error': 'Already grabbed', 'existing': True}
    from app import grab_cache                 # import_reconciler re-adds a grab qBittorrent lost (Reviewer:
    grab_cache.save(h, kind, payload, now)     # only after the blocklist and dedupe checks let it through)
    from app import torrent_submit as submit
    from app.qbit_connector import ok_status, add_ok, add_pending
    name = category()
    sess, url = mover._qbit_session(session_factory or requests.Session, now)
    if not sess:
        return {'ok': False, 'error': tv_grab.NO_CLIENT, 'client': True}
    refused = None
    outcome = submit.SKIP
    try:
        why = tv_grab.ensure_category(sess, url, name, job=JOB)
        if why:
            return {'ok': False, 'error': OFF if why == tv_grab.OFF else why}
        pre = submit.precheck(sess, url, h, kind, payload)
        if pre != submit.SKIP:
            outcome = pre
        else:
            with instance.scoped_add(JOB, name) as scope:
                if kind == 'torrent':
                    r = sess.post(f'{url}/api/v2/torrents/add', data=tv_grab._fields(name), timeout=30,
                                  allow_redirects=False,
                                  files={'torrents': ('release.torrent', payload, 'application/x-bittorrent')})
                else:
                    r = sess.post(f'{url}/api/v2/torrents/add', data={'urls': payload, **tv_grab._fields(name)},
                                  timeout=30, allow_redirects=False)
                if add_pending(r):
                    outcome = submit.PENDING
                elif not add_ok(r):
                    info = submit.inspect(kind, payload)
                    if info.private is True or info.has_usable:
                        outcome = submit.settle(sess, url, h, kind, payload, duplicate=True, sent=True,
                                                permit=scope.allow_trackers)
                        if outcome == submit.SKIP:
                            refused = r
                    else:
                        refused = r
                else:
                    outcome = submit.settle(sess, url, h, kind, payload, duplicate=False, sent=True,
                                            permit=scope.allow_trackers)
    except instance.WritesBlocked:
        return {'ok': False, 'error': OFF, 'blocked': True}
    except requests.RequestException as e:
        logger.warning('Movie grab: qBittorrent add failed (%s)', type(e).__name__)
        return {'ok': False, 'error': tv_grab.NO_CLIENT, 'client': True}
    finally:
        mover._close(sess)
    if submit.inspect(kind, payload).private is True and outcome != submit.CONFIRMED:
        outcome = submit.PENDING if outcome == submit.SKIP else outcome
    bad = submit.failure(outcome)
    if bad:
        return bad
    if refused is not None:
        return {'ok': False, 'error': f'qBittorrent returned HTTP {refused.status_code}'
                if not ok_status(refused.status_code) else 'qBittorrent refused the torrent'}
    with ms.connect() as conn:
        _set(conn, tmdb_id, next_search_at=None, last_search_at=now)
    movie_grabs.record(tmdb_id, str(item.get('title') or ''), h, now)
    if prior:
        movie_grabs.set_state(h, 'grabbed', now=now)
    if origin == 'manual':                       # the Activity badge (Genre Roulette notes its own after this)
        from app import grab_origin
        grab_origin.record('manual', info_hash=h, tmdb_id=tmdb_id, media_type='movie',
                           title=str(item.get('title') or ''), evidence='movie-grab:manual', hash_only=True)
    logger.info('Movie grab: %s (%s, category %s)', item.get('title'), origin, name)
    return {'ok': True, 'hash': h}


# ------------------------------------------------------------------ search

def _blocked(conn, tid: int) -> set:
    return {r['release_title'] for r in conn.execute('SELECT release_title FROM movie_blocklist WHERE tmdb_id = ?', (tid,))}


def search_movie(tid: int, origin: str = 'auto', now: Optional[float] = None) -> Dict[str, Any]:
    """Search the movie's trackers once and grab the best release. {picks, statuses, answered, grabbed}."""
    from app import tv_search
    now = time.time() if now is None else now
    with ms.connect() as conn:
        m = next(iter(wanted(conn, [tid])), None)
        if m is None:
            return {'ok': False, 'error': 'Not found', 'grabbed': False}
        prof = _profile(conn, m['profile_id'])
        blocked = _blocked(conn, tid)
    cfgs = trackers()
    if not cfgs:
        return {'ok': False, 'error': NO_TRACKERS, 'grabbed': False, 'statuses': []}
    automatic = origin == 'auto'
    q = f"{m['title']} {m['year']}" if m.get('year') else m['title']
    items, st = tv_search.query_trackers(q, AUTO_PURPOSE if automatic else 'manual', None, not automatic, now, cfgs,
                                         categories=MOVIE_CATEGORIES, section=SECTION)
    out = {'ok': True, 'statuses': st, 'answered': any(s['status'] == 'ok' for s in st), 'grabbed': False,
           'picks': []}
    for c in candidates(items, m, prof, blocked, [x['id'] for x in cfgs]):
        g = grab(c['item'], tid, 'auto' if automatic else 'manual', now)
        out['picks'].append({'title': c['item'].get('title'), 'quality': c['item'].get('quality'), 'size': c['item'].get('size'),
                             'grabbed': bool(g.get('ok')), 'error': g.get('error')})
        if g.get('ok') or g.get('blocked'):
            out['grabbed'] = bool(g.get('ok'))
            break
    _after(m, out, now, automatic)
    return out


def _skip_retry_at(statuses: List[Dict[str, Any]], now: float) -> float:
    """When every tracker skipped: retry at the earliest guard ready_at (bucket refill,
    purpose gap, 429 backoff). Fall back to SKIP_RETRY_S only when no tracker gave a time."""
    times = [float(s['reset_at']) for s in (statuses or []) if s.get('reset_at')]
    if not times:
        return now + SKIP_RETRY_S
    return max(min(times), now + 1)


def _after(m: Dict[str, Any], out: Dict[str, Any], now: float, automatic: bool) -> None:
    from app import tv_search
    with ms.connect() as conn:
        tid = m['tmdb_id']
        if out['grabbed']:
            _set(conn, tid, next_search_at=None, last_search_at=now)
        elif automatic and out['statuses'] and all(s['status'] == tv_search.SLOT_BUSY for s in out['statuses']):
            reset = min(s.get('reset_at') or now for s in out['statuses'])      # every tracker at its daily cap
            _set(conn, tid, next_search_at=max(reset, now), last_search_at=m.get('last_search_at') or now)
        elif out.get('answered'):
            attempts = int(m.get('search_attempts') or 0) + 1
            _set(conn, tid, last_search_at=now, search_attempts=attempts,
                 **({'next_search_at': next_retry(m, now, attempts)} if automatic else {}))
        elif automatic:                                     # nobody answered (spacing, backoff, empty bucket)
            nxt = _skip_retry_at(out.get('statuses') or [], now)
            why = ', '.join(f"{s.get('name')}:{s.get('status')}" for s in (out.get('statuses') or []) if s.get('status') != 'ok') or 'no answer'
            logger.info('Movie search skipped %s (%s): %s; retry in %ss', m.get('title'), tid, why, int(max(0, nxt - now)))
            _set(conn, tid, next_search_at=nxt)
            # last_search_at and search_attempts stay as they were: this was not a real search


def run_due(now: float, max_movies: int = 5) -> Dict[str, int]:
    with ms.connect() as conn:
        mode = ms.availability(conn)
        due = [m for m in wanted(conn) if m['next_search_at'] is not None and m['next_search_at'] <= now
               and available(m, now, mode)]                 # v256: never searched before it is available
    due.sort(key=lambda m: (m['next_search_at'], m.get('search_attempts') or 0, m['tmdb_id']))
    out = {'movies': 0, 'grabbed': 0}
    for m in due[:max_movies]:
        r = search_movie(m['tmdb_id'], 'auto', now)
        out['movies'] += 1
        out['grabbed'] += int(bool(r.get('grabbed')))
        if r.get('error') == NO_TRACKERS:
            break
        if not r.get('answered') and not r.get('grabbed'):
            # this movie used no slot or was spaced; leave the rest due for the next tick
            break
    return out


# ------------------------------------------------------------------ feed

def feed(now: float) -> Dict[str, int]:
    """Every FEED_S per tracker (guard purpose 'feed': no cap or slot). A match is grabbed only when the movie
    is available (v256: Settings > Movies availability; a feed release no longer marks it released)."""
    from app import tv_search
    out = {'trackers': 0, 'grabbed': 0}
    cfgs = trackers()
    order = [c['id'] for c in cfgs]
    for cfg in cfgs:
        key = f"movie_feed:{cfg['id']}"
        with ms.connect() as conn:
            if now - float(ms._meta(conn, key, 0) or 0) < FEED_S:
                continue
        items, st = tv_search.query_trackers('', 'feed', None, False, now, [cfg], categories=MOVIE_CATEGORIES,
                                             section=SECTION)
        if not st or st[0]['status'] != 'ok':
            continue
        with ms.connect() as conn:
            ms._set_meta(conn, key, now)
            movies = wanted(conn)
            profs = {m['tmdb_id']: _profile(conn, m['profile_id']) for m in movies}
            blocked = {m['tmdb_id']: _blocked(conn, m['tmdb_id']) for m in movies}
            mode = ms.availability(conn)
        out['trackers'] += 1
        for m in movies:
            hits = [it for it in items if matches(str(it.get('title') or ''), m)]
            if not hits:
                continue
            if not available(m, now, mode):
                at = available_at(m, mode)
                when = datetime.fromtimestamp(at, TZ).strftime('%Y-%m-%d %H:%M %Z') if at else 'unknown'
                logger.info('Movie feed skipped %s (%s): not available until %s', m.get('title'), m['tmdb_id'], when)
                continue
            picked = False
            for c in candidates(hits, m, profs[m['tmdb_id']], blocked[m['tmdb_id']], order):
                g = grab(c['item'], m['tmdb_id'], 'auto', now)
                if g.get('ok'):
                    out['grabbed'] += 1
                    picked = True
                if g.get('ok') or g.get('blocked'):
                    if g.get('blocked'):
                        logger.info('Movie feed skipped %s (%s): %s', c['item'].get('title'), m['tmdb_id'], g.get('error'))
                    picked = True
                    break
            if not picked:
                for it in hits:
                    rel = _release(it, m, profs[m['tmdb_id']], blocked[m['tmdb_id']], False)
                    why = ', '.join(rel['rejections']) or 'no candidate'
                    logger.info('Movie feed skipped %s (%s): %s', rel['title'], rel.get('tracker') or '?', why)
    return out


# ------------------------------------------------------------------ the loop

_running = threading.Lock()
_started = {'on': False}


def tick(now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    if not enabled():
        return {'skipped': 'off'}
    if not _running.acquire(blocking=False):
        return {'skipped': 'busy'}
    try:
        out: Dict[str, Any] = {'dates': refresh_dates(now)}
        from app import grab_retry
        out['pending'] = grab_retry.run_due(now, 'movie')
        out['feed'] = feed(now)
        out['scheduled'] = schedule(now)
        out['due'] = run_due(now)
        return out
    finally:
        _running.release()


def start() -> bool:
    """The background loop (test only). Prod never starts it."""
    if _started['on'] or not on_test():
        return False
    _started['on'] = True

    def loop():
        while True:
            try:
                tick()
            except Exception:                                # noqa: BLE001
                logger.exception('Movie scanner pass failed')
            time.sleep(TICK_S)
    threading.Thread(target=loop, name='movie-scanner', daemon=True).start()
    logger.info('Movie scanner started (test install)')
    return True


# ------------------------------------------------------------------ the owner's search (movie page)

NOT_THIS = 'Not this movie'
HARD = (NOT_THIS, 'Blocklisted', 'Already grabbed')
GRAB_KEYS = {'download_url', 'title', 'size', 'quality', 'indexer'}


def _release(it: Dict[str, Any], m: Dict[str, Any], prof: Optional[Dict[str, Any]], blocked: set, grabbed: bool) -> Dict[str, Any]:
    from app import tv_search
    title = str(it.get('title') or '')
    q = tv_monitor.quality_of(title)
    why: List[str] = []
    if not matches(title, m):
        why.append(NOT_THIS)
    if title in blocked:
        why.append('Blocklisted')
    if grabbed:
        why.append('Already grabbed')
    allowed = [i['id'] for i in (prof or {}).get('items') or [] if i.get('allowed')]
    if q not in allowed:
        why.append(tv_search.NOT_IN_PROFILE)
    lo, hi = (prof or {}).get('min_mb_per_min'), (prof or {}).get('max_mb_per_min')
    mb = (int(it.get('size') or 0) / 1048576) / max(1, int(m.get('runtime') or DEFAULT_RUNTIME))
    if (lo and mb < lo) or (hi and mb > hi):
        why.append(tv_search.BAD_SIZE)
    if int(it.get('seeders') or 0) < 1:
        why.append(tv_search.FEW_SEEDERS)
    return {'title': title, 'tracker': it.get('indexer'), 'size': int(it.get('size') or 0),
            'seeders': int(it.get('seeders') or 0), 'leechers': int(it.get('leechers') or 0), 'quality': q,
            'quality_label': None, 'published': it.get('publishDate'), 'freeleech': bool(it.get('freeleech')),
            'download_url': it.get('downloadUrl'), 'season': None, 'episodes': [], 'pack': False,
            'rejections': why, 'grabbable': not any(w in HARD for w in why)}


def _movie(tid: int) -> Optional[Dict[str, Any]]:
    with ms.connect() as conn:
        ensure(conn)
        r = conn.execute('SELECT w.*, s.runtime, s.released_at, s.last_search_at, s.search_attempts FROM watchlist w'
                         ' LEFT JOIN movie_scan s ON s.tmdb_id = w.tmdb_id WHERE w.tmdb_id = ?', (tid,)).fetchone()
        return dict(r) if r else None


def interactive(tid: int, now: Optional[float] = None) -> Dict[str, Any]:
    """Search the movie's trackers (interactive budget), grab nothing, list every release with its rejections."""
    from app import tv_search
    now = time.time() if now is None else now
    m = _movie(tid)
    if m is None:
        return {'ok': False, 'error': 'Not found', 'status': 404}
    out: Dict[str, Any] = {'ok': True, 'searched': 0, 'releases': [], 'error': None,
                           'grabs_allowed': instance.writes_allowed(JOB)}
    cfgs = trackers()
    if not cfgs:
        return {**out, 'ok': False, 'error': NO_TRACKERS}
    q = f"{m['title']} {m['year']}" if m.get('year') else m['title']
    items, st = tv_search.query_trackers(q, 'manual', None, True, now, cfgs, categories=MOVIE_CATEGORIES, section=SECTION)
    out['searched'], out['statuses'] = 1, st
    with ms.connect() as conn:
        prof = _profile(conn, m['profile_id'])
        blocked = _blocked(conn, tid)
        grabbed = bool(conn.execute(f"SELECT 1 FROM movie_grabs WHERE tmdb_id = ? AND state IN ({','.join('?' * len(ACTIVE))})",
                                    (tid, *ACTIVE)).fetchone())
    seen, rel = set(), []
    for it in items:
        k = (it.get('indexerId'), it.get('title'), it.get('size'))
        if k not in seen:
            seen.add(k)
            rel.append(_release(it, m, prof, blocked, grabbed))
    out['releases'] = rel
    if not any(s['status'] == 'ok' for s in st):
        out['error'] = "Couldn't search"
    return out


def manual(tid: int, now: Optional[float] = None) -> Dict[str, Any]:
    """The owner's Search automatically: search now (interactive budget) and grab the best pick."""
    if _movie(tid) is None:
        return {'ok': False, 'error': 'Not found', 'status': 404}
    r = search_movie(tid, 'manual', now)
    if r.get('error') == 'Not found':
        return {'ok': True, 'picks': [], 'error': 'Already grabbed', 'grabs_allowed': instance.writes_allowed(JOB)}
    picks = r.get('picks') or []
    return {'ok': r.get('ok', False), 'picks': picks, 'error': r.get('error'), 'statuses': r.get('statuses', []),
            'grabs_allowed': instance.writes_allowed(JOB)}


def grab_release(tid: int, body: Any, now: Optional[float] = None) -> Dict[str, Any]:
    if not isinstance(body, dict) or set(body) - GRAB_KEYS:
        return {'ok': False, 'error': 'Invalid', 'status': 400}
    url, title, size = body.get('download_url'), body.get('title'), body.get('size', 0)
    if not isinstance(url, str) or not url.strip() or len(url) > 8192 or not isinstance(title, str) or not title.strip() \
            or len(title) > 1000 or type(size) is not int or size < 0:
        return {'ok': False, 'error': 'Invalid', 'status': 400}
    m = _movie(tid)
    if m is None:
        return {'ok': False, 'error': 'Not found', 'status': 404}
    if not matches(title, m):
        return {'ok': False, 'error': NOT_THIS, 'status': 400}
    item = {'downloadUrl': url, 'title': title, 'size': size, 'quality': tv_monitor.quality_of(title),
            'indexer': body.get('indexer') if isinstance(body.get('indexer'), str) else None}
    return grab(item, tid, 'manual', now)
