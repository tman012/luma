"""v252 Genre Roulette: one run (ported from prod 1b4ea74 run_cycle, :1772-2500, without Radarr).

Spin the wheel, draw unowned films (gr_select), gate on IMDb when an OMDb key is saved, then per spun genre
try its most famous passers until one grabs (one per genre; a failed genre's slot is never handed to more
of the same genre before the others have had their turn), then fill by fame, relax the IMDb vote floor and
borrow enabled genres. Each try searches the trackers on in Settings > Movies (tv_search.query_trackers, the
movie scanner's automatic budget), ranks by the chosen Settings > Movies quality profile and minimum seeders,
and grabs through the movie scanner's qBittorrent path (movie_scanner.grab: duplicate check, movie category,
movie_grabs row). The movie importer files it under Settings > Library Paths / Movies naming.

Rules kept from prod: primary genre only; one pick per spun genre; per-genre retry through the bucket;
collection dedupe against recent grabs and this run (:2148); a run that fails early does not advance the
wheel or cursors (:2468). v252 fixes: (1b) no fixed 25-search exit: the run keeps filling until the target is
met, the attempt or time budget is spent, the pool is exhausted, or trackers/qBittorrent are unreachable, and
records that stop_reason; (2b) a film whose TMDb collection already has a part in the library is skipped
(logged with the owned title). A grab that reports the film already in the library or in qBittorrent is
never retried with another release (the old duplicate/undo loop).
"""
from __future__ import annotations

import logging
import random
import threading
import time
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from app.automation import gr_select as sel, gr_store

logger = logging.getLogger(__name__)

SEARCH_PAUSE = 1.0
MAX_RELEASE_ATTEMPTS = 4
MAX_SEARCH_FAILURES = 3
IMDB_MAX_LOOKUPS = 200
IMDB_MAX_FAILURES = 3
FILL_EXTRA_GENRES = 3
# v303: when every tracker is paced (tv spacing / SLOT_BUSY / rate), wait for the
# next Luma spacing slot (~AUTO_MIN_GAP_S) and keep searching remaining films
# instead of aborting the night at 1/3. Cap waits so a real long rate backoff
# still ends the run as trackers_capped (label kept from v297).
MAX_PACE_WAITS = 12            # ~12 min of pure spacing waits; enough for movies_per_night=3 overnight
PICK_KEYS = ('tmdb_id', 'title', 'year', 'genre', 'poster_path', 'votes')

STOP_TEXT = {
    'target_met': 'Target met', 'attempt_budget': 'Search limit reached', 'time_budget': 'Time limit reached',
    'pool_exhausted': 'No more candidates', 'tracker_unreachable': "Trackers couldn't be reached",
    'trackers_capped': 'Tracker limits reached', 'qbit_unreachable': "qBittorrent couldn't be reached",
    'grabs_off': 'Grabs are off', 'error': 'Error', 'interrupted': 'Interrupted',
}


def pace_wait_seconds(sts: List[Dict[str, Any]], now: float, cfgs: Optional[List[Dict[str, Any]]] = None) -> float:
    """How long to sleep when every tracker is paced.

    Prefers the earliest status ``reset_at`` when present. Otherwise waits a full
    ``AUTO_MIN_GAP_S`` (Luma tv spacing). Returns > AUTO_MIN_GAP_S when reset_at is
    farther out so the caller aborts as trackers_capped instead of sleeping through
    a multi-hour rate/ban backoff. ``cfgs`` kept for call-site compatibility.
    """
    from app import tv_budget
    gap = float(tv_budget.AUTO_MIN_GAP_S)
    resets = [float(s['reset_at']) for s in sts if s.get('reset_at') is not None]
    if not resets:
        return gap
    raw = min(resets) - now
    if raw > gap:
        return raw
    if raw <= 0:
        return 1.0
    return max(1.0, min(gap, raw))


_status: Dict[str, Any] = {'running': False, 'run_id': None, 'kind': None}
_lock = threading.Lock()


def running() -> Dict[str, Any]:
    with _lock:
        return dict(_status)


# ------------------------------------------------------------------ outside services (mocked in tests)

def trackers() -> List[Dict[str, Any]]:
    from app import movie_scanner
    return movie_scanner.trackers()


def profile(pid: Optional[int]) -> Optional[Dict[str, Any]]:
    from app import movie_scanner, movie_settings as ms
    with ms.connect() as conn:
        return movie_scanner._profile(conn, pid)


def grabs_allowed() -> bool:
    from app import instance, movie_scanner
    return movie_scanner.on_test() and instance.writes_allowed(movie_scanner.JOB)


def search(film: Dict[str, Any], cfgs: List[Dict[str, Any]], now: float):
    from app import movie_scanner as sc, tv_search
    # its own purpose (logged as 'backlog' in the movies section); no daily search cap, only the guard's pacing
    return tv_search.query_trackers(f"{film['title']} {film['year']}", tv_search.AUTOMATION, None, False, now, cfgs,
                                    categories=sc.MOVIE_CATEGORIES, section=sc.SECTION)


def rank(items, movie, prof, cfgs, min_seeders):
    from app import movie_scanner as sc
    return [c for c in sc.candidates(items, movie, prof, set(), [x['id'] for x in cfgs])
            if int(c['item'].get('seeders') or 0) >= min_seeders]


def grab(item: Dict[str, Any], movie: Dict[str, Any], now: float) -> Dict[str, Any]:
    from app import movie_scanner as sc
    return sc.grab(item, int(movie['tmdb_id']), 'auto', now, movie=movie, pending=False)


def note_origin(info_hash: str, film: Dict[str, Any], item: Dict[str, Any]) -> None:
    from app import grab_origin
    grab_origin.record('genre_roulette', info_hash=info_hash, tmdb_id=film['tmdb_id'], media_type='movie',
                       title=film['title'], download_url='', evidence='genre_roulette')


def notify(title: str, body: str) -> None:
    """Legacy admin Web Push hook (kept for tests/monkeypatches).

    v296: Genre Roulette no longer pushes at run finish/grab. Movie-name Web Push
    matches prod AutoRenamer via library_notify on movie_import (import-complete only).
    """
    try:
        from app import user_auth
        from app.push_service import send_push_to_user
        for u in user_auth.list_users():
            if u.get('is_admin') and u.get('id') is not None:
                send_push_to_user(int(u['id']), title, body, tag='genre-roulette', url='/settings/automation')
    except Exception as e:                       # noqa: BLE001
        logger.warning('Genre Roulette: push failed: %s', type(e).__name__)


def push_body(r: Dict[str, Any]) -> str:
    """Movie-name body for a run (Title (Year) list). Kept for logs/cards; not sent at grab.

    Grabbed picks only. Year omitted when empty. Shortfall appends " · N of M".
    Errors / zero grabs keep the reason text (same ideas as summary()).
    """
    if r.get('error'):
        return str(r['error'])
    picks = r.get('picks') or []
    got = [p for p in picks if p.get('grabbed')]
    wanted = int(r.get('wanted') or 0)
    grabbed = int(r.get('grabbed') or 0)
    if got:
        names = ', '.join(
            f"{p['title']} ({p['year']})" if p.get('year') not in (None, '') else str(p.get('title') or 'Unknown')
            for p in got
        )
        if wanted and grabbed < wanted:
            return f'{names} · {grabbed} of {wanted}'
        return names
    stop = r.get('stop_reason')
    reason = STOP_TEXT.get(stop, '') if stop else ''
    if reason:
        return f'Nothing added · {reason}'
    return summary(r)


# ------------------------------------------------------------------ the run

def fame_key(f: Dict[str, Any], this_year: int):
    iv = (f.get('imdb') or {}).get('votes') if isinstance(f.get('imdb'), dict) else None
    return (1 if (f.get('year') or 0) >= this_year - 1 else 0, -(iv or 0), -(f.get('votes') or 0))


def run(kind: str = 'manual', dry: Optional[bool] = None, cfg: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None, rng: Optional[random.Random] = None, record: bool = True,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """One run. kind: 'scheduled' | 'manual' | 'preview' (always dry, never recorded)."""
    cfg = cfg or gr_store.settings()
    dry = True if kind == 'preview' else (bool(cfg.get('dry_run')) if dry is None else dry)
    record = record and kind != 'preview'
    with _lock:
        if _status['running'] and kind != 'preview':
            return {'error': 'Already running', 'status': 'busy'}
        if kind != 'preview':
            _status.update(running=True, kind=kind)
    run_id = gr_store.start_run(kind, dry, now) if record else None
    if run_id:
        with _lock:
            _status['run_id'] = run_id
    try:
        result = _run(cfg, dry, kind, now, rng or random.Random(), run_id, sleep, clock)
    except Exception as e:                       # noqa: BLE001
        logger.exception('Genre Roulette run failed')
        result = {'error': f'Run failed ({type(e).__name__})', 'stop_reason': 'error', 'wanted': 0, 'grabbed': 0}
    finally:
        if kind != 'preview':
            with _lock:
                _status.update(running=False, run_id=None, kind=None)
    result['dry'] = dry
    result['kind'] = kind
    result['status'] = 'error' if result.get('error') else 'done'
    if run_id:
        gr_store.finish_run(run_id, result, None if now is None else now + 1)
        result['run_id'] = run_id
        try:
            gr_store.prune(int(cfg.get('history_days') or 90), now)
        except Exception:                        # noqa: BLE001
            logger.exception('Genre Roulette: history prune failed')
    # v296: no Web Push at grab/run-finish (standing rule: library import only).
    # Movie names go out via library_notify on movie_import import-complete (prod copy).
    # push_body() keeps a movie-name line for logs when picks exist; summary() stays counts.
    if record and not dry and (result.get('picks') or result.get('error')):
        logger.info('Genre Roulette push_body: %s', push_body(result))
    logger.info('Genre Roulette: %s', summary(result))
    return result


def summary(r: Dict[str, Any]) -> str:
    if r.get('error'):
        return str(r['error'])
    verb = 'Would add' if r.get('dry') else 'Added'
    return f"{verb} {int(r.get('grabbed') or 0)} of {int(r.get('wanted') or 0)}"


def _run(cfg, dry, kind, now, rng, run_id, sleep, clock) -> Dict[str, Any]:
    now = time.time() if now is None else now
    started = clock()
    st = gr_store.state()
    base: Dict[str, Any] = {'started_at': now, 'genres': [], 'picks': [], 'skips': {}, 'failures': [],
                            'grabbed': 0, 'wanted': 0, 'stats': {}}

    def fail(msg: str, stop: str = 'error') -> Dict[str, Any]:
        return {**base, 'error': msg, 'stop_reason': stop}

    if not dry and not grabs_allowed():
        return fail('Grabs are off on this install', 'grabs_off')
    cfgs = trackers()
    if not cfgs:
        return fail('No movie trackers are on')
    prof = profile(cfg.get('profile_id'))
    if not prof:
        return fail('No quality profile')
    own = sel.owned(cfg)
    if own['error']:
        return fail(own['error'])
    cand = sel.candidates(cfg, st, rng, own)
    base['genres'] = cand.get('genres') or []
    if cand.get('error'):
        return fail(cand['error'])
    spun: List[str] = list(cand['genres'])
    per_genre = int(cfg.get('movies_per_genre') or 1)
    want = sel.wanted(cfg, len(spun))
    base['wanted'] = want
    min_seeders = int(cfg.get('min_seeders') or 0)
    attempt_budget = int(cfg.get('attempt_budget') or 60)
    time_budget = int(cfg.get('time_budget_min') or 45) * 60
    this_year = datetime.now().year

    # IMDb gate (fail open: a lookup that can't be made admits the film)
    gate = sel.imdb_gate_on(cfg)
    imdb_votes = int(cfg.get('imdb_min_votes') or 0) if gate else 0
    imdb_rating = float(cfg.get('imdb_min_rating') or 0) if gate else 0.0
    details_cache: Dict[str, Any] = dict(st.get('details_cache') or {})
    parts_cache: Dict[int, List[Dict[str, Any]]] = {}
    ctr = {'lookups': 0, 'imdb_fail': 0, 'imdb_open': 0, 'attempts': 0, 'search_fail': 0, 'tried': 0}
    imdb_down = [False]

    def lookup(film: Dict[str, Any]) -> None:
        if 'imdb' in film or film.get('imdb_unknown') or imdb_down[0] or ctr['lookups'] >= IMDB_MAX_LOOKUPS:
            return
        ctr['lookups'] += 1
        got = sel.imdb_of(film, details_cache)
        if got is None:                                       # OMDb couldn't answer
            ctr['imdb_fail'] += 1
            imdb_down[0] = ctr['imdb_fail'] >= IMDB_MAX_FAILURES
            film['imdb_unknown'] = True
        else:
            ctr['imdb_fail'] = 0
            film['imdb'] = got

    def verdict(film: Dict[str, Any], floor: int) -> bool:
        if not gate:
            return True
        lookup(film)
        if film.get('imdb_unknown') or (imdb_down[0] and 'imdb' not in film):
            if not film.get('imdb_open'):
                film['imdb_open'] = True
                ctr['imdb_open'] += 1
            return True                                       # fail open
        if 'imdb' not in film:
            return False                                      # past the lookup budget: not admitted
        return sel.clears(film['imdb'], floor, imdb_rating)

    films: List[Dict[str, Any]] = list(cand['films'])
    passers = [f for f in films if verdict(f, imdb_votes)]
    held = [f for f in films if f not in passers]
    skips: Dict[str, List[Dict[str, Any]]] = {}
    picks: List[Dict[str, Any]] = []
    parked_now: List[int] = []
    got = {'n': 0}
    by_genre_got: Dict[str, int] = {}
    genre_notes: Dict[str, Dict[str, int]] = {}
    tried: set = set()
    grabbed_now: set = set()
    recent = set(int(x) for x in (st.get('grabbed_tmdb_ids') or []) if str(x).lstrip('-').isdigit()) \
        | gr_store.grabbed_ids()
    abort: List[Optional[str]] = [None]
    pace_waits = {'n': 0}   # v303: spacing waits this run (not a hard fail counter)
    now_box = [now]         # advanced after a pace wait so the next search sees a fresh slot

    def note(genre: str, what: str) -> None:
        genre_notes.setdefault(genre, {}).setdefault(what, 0)
        genre_notes[genre][what] += 1

    def skip(film: Dict[str, Any], reason: str, **extra: Any) -> None:
        skips.setdefault(reason, []).append({k: film.get(k) for k in ('tmdb_id', 'title', 'year', 'genre')} | extra)
        note(film.get('genre') or '', reason)

    def budget_left() -> Optional[str]:
        if abort[0]:
            return abort[0]
        if got['n'] >= want:
            return 'target_met'
        if ctr['attempts'] >= attempt_budget:
            return 'attempt_budget'
        if clock() - started >= time_budget:
            return 'time_budget'
        return None

    def collection_block(film: Dict[str, Any]) -> bool:
        if not cfg.get('skip_same_collection', True):
            return False
        d = sel.details(film['tmdb_id'], details_cache)
        if d is None:
            logger.info('Genre Roulette: collection lookup failed for %s; not blocking', film['title'])
            return False
        film['runtime'] = d.get('runtime')
        if not d['collection']:
            return False
        parts = sel.collection_parts(d['collection'], parts_cache)
        if not parts:
            logger.info('Genre Roulette: collection %s parts unavailable; not blocking', d['collection'])
            return False
        tid = int(film['tmdb_id'])
        tys = own.get('title_years') or set()
        mine = recent | grabbed_now
        for p in parts:
            if p['id'] == tid:
                continue
            if p['id'] in mine:
                logger.info('Genre Roulette: skipping %s (%s): same collection as %s', film['title'], film['year'],
                            p['title'])
                skip(film, 'same_collection', other_title=p['title'], other_year=p['year'])
                return True
            if p['id'] in own['ids'] or (p['title'] and p['year'] and (sel.norm(p['title']), p['year']) in tys):
                logger.info('Genre Roulette: skipping %s (%s): collection already in the library (%s)',
                            film['title'], film['year'], p['title'])
                skip(film, 'owned_collection', owned_title=p['title'], owned_year=p['year'])
                return True
        return False

    def pick_row(film, c=None, grabbed=False) -> Dict[str, Any]:
        it = (c or {}).get('item') or {}
        iv = film.get('imdb') if isinstance(film.get('imdb'), dict) else {}
        return {**{k: film.get(k) for k in PICK_KEYS}, 'borrowed': film.get('genre') not in spun,
                'imdb_rating': iv.get('rating'), 'imdb_votes': iv.get('votes'),
                'release': it.get('title'), 'quality': it.get('quality'), 'size': it.get('size'),
                'seeders': it.get('seeders'), 'tracker': it.get('indexer'), 'grabbed': grabbed}

    def attempt(film: Dict[str, Any]) -> str:
        tid = int(film['tmdb_id'])
        if tid in tried:
            return 'none'
        tried.add(tid)
        ctr['tried'] += 1
        if collection_block(film):
            return 'skip'
        if film.get('runtime') is None:
            d = sel.details(tid, details_cache)
            film['runtime'] = (d or {}).get('runtime')
        from app import tv_search
        paced = {tv_search.SLOT_BUSY, 'rate'}
        items: List[Dict[str, Any]] = []
        sts: List[Dict[str, Any]] = []
        while True:
            if budget_left():
                return 'abort' if abort[0] else 'none'
            ctr['attempts'] += 1
            items, sts = search(film, cfgs, now_box[0])
            if any(s.get('status') == 'ok' for s in sts):
                break
            # Slot/daily cap, purpose spacing, and rate backoff are pacing — never
            # "Trackers couldn't be reached" (v297). v303: wait for next_slot and
            # continue the same run instead of aborting mid-night when films remain.
            if sts and all(s.get('status') in paced for s in sts):
                from app import tv_budget as _tvb
                wait_s = pace_wait_seconds(sts, now_box[0], cfgs)
                remaining = time_budget - (clock() - started)
                if (wait_s > float(_tvb.AUTO_MIN_GAP_S)
                        or pace_waits['n'] >= MAX_PACE_WAITS
                        or wait_s > remaining):
                    abort[0] = 'trackers_capped'
                    return 'abort'
                pace_waits['n'] += 1
                logger.info(
                    'Genre Roulette: all trackers paced; waiting %.0fs for next slot (%s/%s)',
                    wait_s, pace_waits['n'], MAX_PACE_WAITS,
                )
                sleep(wait_s)
                now_box[0] = now_box[0] + wait_s
                continue
            ctr['search_fail'] += 1
            if ctr['search_fail'] >= MAX_SEARCH_FAILURES:
                abort[0] = 'tracker_unreachable'
                return 'abort'
            note(film['genre'], 'search_failed')
            sleep(SEARCH_PAUSE)
            return 'none'
        ctr['search_fail'] = 0
        movie = {'tmdb_id': tid, 'title': film['title'], 'year': film['year'], 'runtime': film.get('runtime')}
        ranked = rank(items, movie, prof, cfgs, min_seeders)
        if not ranked:
            if items:
                parked_now.append(tid)
            skip(film, 'no_release')
            sleep(SEARCH_PAUSE)
            return 'none'
        if dry:
            picks.append(pick_row(film, ranked[0], False))
            got['n'] += 1
            sleep(SEARCH_PAUSE)
            return 'grabbed'
        for c in ranked[:MAX_RELEASE_ATTEMPTS]:
            g = grab(c['item'], movie, now)
            if g.get('ok'):
                h = str(g.get('hash') or '')
                gr_store.record_grab(h, tid, film['title'], film['year'], run_id, now)
                note_origin(h, film, c['item'])
                picks.append(pick_row(film, c, True))
                got['n'] += 1
                grabbed_now.add(tid)
                sleep(SEARCH_PAUSE)
                return 'grabbed'
            if g.get('duplicate') or g.get('existing'):   # already in the library or qBittorrent: never retried
                skip(film, 'owned', detail=str(g.get('error') or ''))
                return 'skip'
            if g.get('client'):
                abort[0] = 'qbit_unreachable'
                return 'abort'
            if g.get('blocked'):
                abort[0] = 'grabs_off'
                return 'abort'
        parked_now.append(tid)
        skip(film, 'refused')
        sleep(SEARCH_PAUSE)
        return 'none'

    def drain(bucket: List[Dict[str, Any]], genre: Optional[str]) -> None:
        for film in bucket:
            if budget_left():
                return
            if genre and by_genre_got.get(genre, 0) >= per_genre:
                return
            if attempt(film) == 'grabbed':
                by_genre_got[film['genre']] = by_genre_got.get(film['genre'], 0) + 1

    def fill(pool: List[Dict[str, Any]]) -> None:
        drain(sorted([f for f in pool if int(f['tmdb_id']) not in tried], key=lambda f: fame_key(f, this_year)), None)

    def buckets(pool: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for f in pool:
            out.setdefault(f['genre'], []).append(f)
        for b in out.values():
            b.sort(key=lambda f: fame_key(f, this_year))
        return out

    # Phase 1: a fair shot per spun genre
    by_genre = buckets(passers)
    for g in spun:
        drain(by_genre.get(g) or [], g)
    # Phase 2: leftover slots by fame among the passers
    if not budget_left():
        fill(passers)
    # Phase 3: relax the IMDb vote floor and keep filling
    floors = sel.relax_ladder(imdb_votes) if gate and imdb_votes else []
    for floor in floors:
        if budget_left():
            break
        newly = [f for f in held if int(f['tmdb_id']) not in tried and verdict(f, floor)]
        held = [f for f in held if f not in newly]
        if not newly:
            continue
        nb = buckets(newly)
        for g in spun:
            if by_genre_got.get(g, 0) < per_genre:
                drain(nb.get(g) or [], g)
        if not budget_left():
            fill(newly)
    # Phase 3b (1b): walk this run's genres deeper (next pages from the cursors) until they run out
    while not budget_left():
        more = sel.deeper(cand, tried | grabbed_now, max((want - got['n']) * sel.FRESH_PER_WANTED, sel.MIN_FRESH_POOL),
                          own.get('title_years') or set())
        if not more:
            break
        films += more
        lowest = (floors[-1] if floors else imdb_votes)
        admitted = [f for f in more if verdict(f, lowest)]
        held += [f for f in more if f not in admitted]
        nb = buckets(admitted)
        for g in spun:
            if by_genre_got.get(g, 0) < per_genre:
                drain(nb.get(g) or [], g)
        if not budget_left():
            fill(admitted)
        if not admitted and not [f for f in more if int(f['tmdb_id']) not in tried]:
            break
    # Phase 4 (1b): borrow more enabled genres, in rounds, until a stop condition
    borrowed: List[str] = []
    extras = [g for g in sel.wheel(cfg) if g not in spun]
    rng.shuffle(extras)
    while not budget_left() and extras:
        batch, extras = extras[:FILL_EXTRA_GENRES], extras[FILL_EXTRA_GENRES:]
        borrowed += batch
        exclude = cand['excluded'] | tried | grabbed_now | {int(f['tmdb_id']) for f in films}
        floor_v = sel.prefilter_votes(int(cfg.get('min_votes') or 0), imdb_votes)
        more = sel.supplement(cfg, batch, exclude, max((want - got['n']) * sel.FRESH_PER_WANTED, sel.MIN_FRESH_POOL),
                              floor_v)
        tys = own.get('title_years') or set()
        more = [f for f in more if (sel.norm(f['title']), f['year']) not in tys]
        films += more
        lowest = (floors[-1] if floors else imdb_votes)
        admitted = [f for f in more if verdict(f, lowest)]
        nb = buckets(admitted)
        for g in batch:
            drain(nb.get(g) or [], g)
        if not budget_left():
            fill(admitted)

    stop = budget_left() or 'pool_exhausted'
    failures = []
    for g in spun:
        if by_genre_got.get(g, 0) >= per_genre:
            continue
        n = genre_notes.get(g, {})
        reason = ('No release found' if n.get('no_release') or n.get('refused')
                  else 'Same collection' if n.get('owned_collection') or n.get('same_collection')
                  else 'Already in the library' if n.get('owned')
                  else 'Below IMDb floors' if gate and not by_genre.get(g) and any(f['genre'] == g for f in films)
                  else 'No candidates' if not any(f['genre'] == g for f in films)
                  else 'Not reached')
        failures.append({'genre': g, 'reason': reason})
    result = {**base, 'picks': picks, 'grabbed': got['n'], 'skips': skips, 'failures': failures,
              'stop_reason': stop, 'borrowed_genres': borrowed,
              'stats': {'attempts': ctr['attempts'], 'tried': ctr['tried'], 'imdb_lookups': ctr['lookups'],
                        'imdb_fail_open': ctr['imdb_open'], 'imdb_gate': gate, 'considered': cand.get('considered'),
                        'pages_read': cand.get('pages_read'), 'widened': cand.get('widened'),
                        'elapsed_s': round(clock() - started, 1),
                        'skipped': {k: len(v) for k, v in skips.items()}}}
    if stop in ('tracker_unreachable', 'qbit_unreachable', 'trackers_capped', 'grabs_off') and not picks:
        result['error'] = STOP_TEXT[stop]
    if dry:
        return result
    if result.get('error'):
        return result                            # an outage leaves the wheel and cursors where they were
    st = gr_store.state()
    today = date.today().isoformat()
    st['grabbed_tmdb_ids'] = sorted(set(st.get('grabbed_tmdb_ids') or []) | grabbed_now)[-5000:]
    parked = dict(st.get('parked') or {})
    for tid in parked_now:
        parked[str(tid)] = today
    st['parked'] = parked
    st['recent_genres'] = spun
    st['cursors'] = sel.next_cursors(cand['walk']) if cand.get('walk') else st.get('cursors')
    st['details_cache'] = dict(list(details_cache.items())[-sel.DETAILS_CACHE_MAX:])
    gr_store.save_state(st)
    return result
