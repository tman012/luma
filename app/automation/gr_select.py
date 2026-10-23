"""v252 Genre Roulette: choosing films (ported from prod 1b4ea74 app/genre_roulette.py, Radarr-free).

TMDb discover, most-voted first, 20 a page, walked per genre from a saved cursor (:561-877); only films
whose PRIMARY genre (genre_ids[0]) is the spun genre (:637-640); least-compromise widening stages (:153);
best-first round robin per genre (:879). Ownership is the Plex movie sections plus in-flight grabs
(movie_grabs and the grab_origin store), not upgrade_library.db; an empty Plex answer refuses the run
(:927-931). IMDb numbers come from app/omdb.get_ratings (fail open; the gate applies only when an OMDb key
is saved and a floor is above 0, :1859).
"""
from __future__ import annotations

import logging
import random
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from app.automation import gr_store
from app.automation.gr_store import GENRES

logger = logging.getLogger(__name__)

TMDB_BASE = 'https://api.themoviedb.org/3'
MAX_DISCOVER_PAGES = 500
MAX_DISCOVER_PAGES_PER_GENRE = 10
TOPUP_PAGES = 4
MAX_TOPUP_ROUNDS = 3
FRESH_PER_WANTED = 10
MIN_FRESH_POOL = 12
PREFILTER_VOTES_RATIO = 100
PREFILTER_VOTES_MIN = 100
IMDB_RELAX_VOTE_STOPS = (180000, 120000, 80000, 50000, 25000)
IMDB_RELAX_VOTE_FLOOR = 25000
WIDE_YEAR_FROM = 1900
RECHECK_DAYS = 30
DETAILS_CACHE_MAX = 8000
WIDENING_STAGES = (
    {'name': 'configured'},
    {'name': 'all years', 'years': True},
    {'name': 'other enabled genres', 'years': True, 'extra_genres': 'enabled'},
    {'name': 'every genre', 'years': True, 'extra_genres': 'all'},
    {'name': 'every genre, 100+ votes', 'years': True, 'min_votes': 100, 'extra_genres': 'all'},
    {'name': 'every genre, 50+ votes', 'years': True, 'min_votes': 50, 'extra_genres': 'all'},
)


# ------------------------------------------------------------------ outside services (mocked in tests)

def tmdb_key() -> str:
    from app.digital_release_cache import _api_key
    return _api_key()


def tmdb_get(path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One TMDb GET; None on any failure."""
    import requests
    try:
        r = requests.get(f'{TMDB_BASE}{path}', params=params, timeout=20)
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, dict) else None
    except Exception as e:                       # noqa: BLE001
        logger.warning('Genre Roulette: TMDb %s failed: %s', path.split('?')[0], type(e).__name__)
        return None


def plex_index() -> Optional[Dict[str, Any]]:
    from app.plex_service import plex_service
    return plex_service.build_ownership_index()


def imdb_ratings(imdb_id: str) -> Optional[Dict[str, Any]]:
    from app import omdb
    return omdb.get_ratings(imdb_id)


def omdb_key_set() -> bool:
    from app import omdb
    return bool(omdb.api_key())


# ------------------------------------------------------------------ genres

def wheel(cfg: Dict[str, Any]) -> List[str]:
    """Genres the wheel may draw: the included ones (all when none), never an excluded one."""
    ex = set(cfg.get('exclude_genres') or [])
    inc = [g for g in (cfg.get('include_genres') or []) if g in GENRES and g not in ex]
    return inc or [g for g in GENRES if g not in ex]


def spin(cfg: Dict[str, Any], st: Dict[str, Any], rng: random.Random) -> List[str]:
    """This run's genres, avoiding the last run's while that leaves enough (prod :541)."""
    pool = wheel(cfg)
    count = min(int(cfg.get('genres_per_run') or 3), len(pool))
    recent = set(st.get('recent_genres') or [])
    fresh = [g for g in pool if g not in recent]
    if len(fresh) >= count:
        pool = fresh
    return rng.sample(pool, count)


# ------------------------------------------------------------------ discover

def discover_params(key: str, genre: str, year_from: int, year_to: int, min_votes: int,
                    language: str) -> Dict[str, Any]:
    p = {
        'api_key': key, 'with_genres': GENRES.get(genre, ''),
        'primary_release_date.gte': f'{year_from}-01-01', 'primary_release_date.lte': f'{year_to}-12-31',
        'vote_average.gte': 0.0, 'vote_count.gte': min_votes, 'include_adult': 'false',
        'with_runtime.gte': 60, 'sort_by': 'vote_count.desc',
    }
    if language and language != 'any':
        p['with_original_language'] = language
    return p


def discover_page(params: Dict[str, Any], genre: str, page: int) -> Optional[Tuple[List[Dict[str, Any]], int]]:
    """(films, total_pages) for one page; None when TMDb failed (not the same as an empty genre)."""
    if not params.get('with_genres'):
        return [], 1
    body = tmdb_get('/discover/movie', {**params, 'page': page})
    if body is None:
        return None
    total = min(int(body.get('total_pages') or 1), MAX_DISCOVER_PAGES)
    want = GENRES.get(genre)
    films = []
    for m in body.get('results') or []:
        tid, rel, ids = m.get('id'), str(m.get('release_date') or '')[:4], m.get('genre_ids') or []
        if not tid or not rel.isdigit() or not ids:
            continue
        try:
            if int(ids[0]) != want:                     # primary genre only
                continue
        except (TypeError, ValueError):
            continue
        films.append({'tmdb_id': int(tid), 'title': m.get('title') or m.get('original_title') or '',
                      'year': int(rel), 'votes': int(m.get('vote_count') or 0),
                      'rating': float(m.get('vote_average') or 0), 'poster_path': m.get('poster_path') or '',
                      'genre': genre})
    return films, total


def new_draw(start_page: int = 1) -> Dict[str, Any]:
    return {'next_page': max(1, start_page), 'total': None, 'reached': False, 'failed': False, 'done': False,
            'pages': 0, 'hold': None, 'last': 0}


def draw_genre(params, genre, exclude, hard, target, budget, ctx) -> List[Dict[str, Any]]:
    """Walk this genre's pages from its cursor until *target* unexcluded films turn up (prod :668)."""
    films, fresh, pages = [], 0, 0
    while not ctx['done'] and pages < budget and fresh < target:
        page = ctx['next_page']
        if page > (ctx['total'] or MAX_DISCOVER_PAGES):
            ctx['next_page'], ctx['done'] = 1, True
            break
        got = discover_page(params, genre, page)
        pages += 1
        if got is None:
            if not ctx['reached']:
                ctx['failed'] = True
            break
        page_films, total = got
        ctx.update(total=total, reached=True, next_page=page + 1, last=max(ctx['last'], page))
        for f in page_films:
            films.append(f)
            if f['tmdb_id'] not in exclude:
                fresh += 1
            if ctx['hold'] is None and f['tmdb_id'] not in hard:
                ctx['hold'] = page
    ctx['pages'] += pages
    return films


def draw_stage(params_for, s_genres, walk, target, pool, stage) -> Tuple[int, List[str]]:
    excluded = walk['excluded']

    def fresh_total() -> int:
        return len([t for t in pool if t not in excluded])

    def absorb(found):
        for f in found:
            pool.setdefault(f['tmdb_id'], f)

    share = max(1, -(-target // max(1, len(s_genres))))
    ctxs: Dict[str, Dict[str, Any]] = {}
    for genre in s_genres:
        if fresh_total() >= target:
            break
        key = (stage, genre)
        ctx = walk['ctxs'].get(key)
        if ctx is None:
            ctx = walk['ctxs'][key] = new_draw(cursor(walk['cursors'], stage, genre))
        ctxs[genre] = ctx
        absorb(draw_genre(params_for(genre), genre, excluded, walk['hard'], share, MAX_DISCOVER_PAGES_PER_GENRE, ctx))
    for _ in range(MAX_TOPUP_ROUNDS):
        if fresh_total() >= target:
            break
        progressed = False
        for genre, ctx in ctxs.items():
            if fresh_total() >= target:
                break
            if ctx['failed'] or ctx['done'] or not ctx['reached']:
                continue
            before = len(pool)
            absorb(draw_genre(params_for(genre), genre, excluded, walk['hard'], target, TOPUP_PAGES, ctx))
            progressed = progressed or len(pool) > before
        if not progressed:
            break
    return len([c for c in ctxs.values() if c['reached']]), [g for g, c in ctxs.items() if c['failed']]


def stage_genres(stage: Dict[str, Any], genres: List[str], cfg: Dict[str, Any], rng: random.Random) -> List[str]:
    scope = stage.get('extra_genres')
    if not scope:
        return genres
    ex = set(cfg.get('exclude_genres') or [])
    enabled = [g for g in wheel(cfg) if g not in genres]
    rng.shuffle(enabled)
    out = genres + enabled
    if scope == 'all':
        rest = [g for g in GENRES if g not in out and g not in ex]   # an excluded genre is never borrowed
        rng.shuffle(rest)
        out += rest
    return out


def prefilter_votes(configured: int, imdb_min_votes: int) -> int:
    """The TMDb vote floor, only ever looser than configured when the IMDb gate is on (prod :809)."""
    if imdb_min_votes <= 0:
        return configured
    derived = max(PREFILTER_VOTES_MIN, imdb_min_votes // PREFILTER_VOTES_RATIO)
    return min(configured, derived) if configured else derived


def cursor_sig(year_from, year_to, min_votes, language) -> str:
    return f'{year_from}-{year_to}|0|{min_votes}|{language}'


def read_cursors(st: Dict[str, Any], sig: str) -> Dict[str, int]:
    saved = st.get('cursors')
    if not isinstance(saved, dict) or saved.get('sig') != sig:
        return {}
    pages = saved.get('pages')
    return dict(pages) if isinstance(pages, dict) else {}


def cursor(cursors: Dict[str, int], stage: str, genre: str) -> int:
    try:
        return max(1, int(cursors.get(f'{stage}|{genre}', 1)))
    except (TypeError, ValueError):
        return 1


def next_cursors(walk: Dict[str, Any]) -> Dict[str, Any]:
    pages = dict(walk['cursors'])
    for (stage, genre), ctx in walk['ctxs'].items():
        if not ctx['reached']:
            continue
        page = 1 if ctx['done'] else ctx['hold'] if ctx['hold'] is not None else ctx['last'] + 1
        pages[f'{stage}|{genre}'] = max(1, page)
    return {'sig': walk['sig'], 'pages': pages}


def best_first(films: List[Dict[str, Any]], genres: List[str], rng: random.Random) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for f in films:
        groups.setdefault(f.get('genre') or '', []).append(f)
    for g in groups.values():
        g.sort(key=lambda f: -(f.get('votes') or 0))
    tonight = [g for g in genres if g in groups]
    borrowed = [g for g in groups if g not in tonight]
    rng.shuffle(tonight)
    rng.shuffle(borrowed)
    out: List[Dict[str, Any]] = []
    for tier in (tonight, borrowed):
        lists = [groups[g] for g in tier]
        for rank in range(max((len(x) for x in lists), default=0)):
            out.extend(x[rank] for x in lists if rank < len(x))
    return out


def active_parked(st: Dict[str, Any], today: Optional[date] = None) -> set:
    from datetime import timedelta
    cutoff = (today or date.today()) - timedelta(days=RECHECK_DAYS)
    out = set()
    for tid, when in (st.get('parked') or {}).items():
        try:
            if date.fromisoformat(str(when)[:10]) >= cutoff:
                out.add(int(tid))
        except (ValueError, TypeError):
            continue
    return out


# ------------------------------------------------------------------ ownership

def owned(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """{'ids', 'title_years', 'error'}: Plex movie sections (all, or the chosen ones) plus in-flight grabs."""
    try:
        index = plex_index() or {}
    except Exception as e:                       # noqa: BLE001
        logger.warning('Genre Roulette: Plex ownership unavailable: %s', type(e).__name__)
        index = {}
    sections = set(cfg.get('owned_sections') or [])
    ids, tys = set(), set()
    for entries in (index.get('movies_by_title') or {}).values():
        for e in entries or []:
            if sections and str(e.get('library_name') or '') not in sections:
                continue
            tid = e.get('tmdb_id')
            if tid is not None and str(tid).isdigit():
                ids.add(int(tid))
            if e.get('title') and str(e.get('year') or '').isdigit():
                tys.add((norm(e['title']), int(e['year'])))
    if not ids and not tys:
        return {'ids': set(), 'title_years': set(), 'error': 'Plex library unavailable'}
    ids |= in_flight()
    return {'ids': ids, 'title_years': tys, 'error': None}


def in_flight() -> set:
    """tmdb ids with an active movie grab (movies.db) or a movie in the grab_origin store."""
    out: set = set()
    try:
        from app import movie_grabs, movie_scanner
        with movie_grabs._Db() as conn:
            out |= {int(r['tmdb_id']) for r in conn.execute(
                f"SELECT tmdb_id FROM movie_grabs WHERE state IN ({','.join('?' * len(movie_scanner.ACTIVE))})",
                movie_scanner.ACTIVE)}
    except Exception as e:                       # noqa: BLE001
        logger.warning('Genre Roulette: movie grabs unreadable: %s', type(e).__name__)
    try:
        from app import grab_origin
        out |= {int(x['tmdb_id']) for x in grab_origin.load_store()
                if x.get('tmdb_id') and (x.get('media_type') or 'movie') == 'movie'}
    except Exception as e:                       # noqa: BLE001
        logger.warning('Genre Roulette: grab origins unreadable: %s', type(e).__name__)
    return out


def norm(title: str) -> str:
    from app import tv_match
    return tv_match.normalize(title)


# ------------------------------------------------------------------ candidates

def candidates(cfg: Dict[str, Any], st: Dict[str, Any], rng: random.Random, own: Dict[str, Any],
               genres: Optional[List[str]] = None) -> Dict[str, Any]:
    """Unowned films from this run's genres, best first (prod :912)."""
    key = tmdb_key()
    if not key:
        return {'error': 'TMDb key not set', 'films': [], 'genres': []}
    genres = genres or spin(cfg, st, rng)
    grabbed = set(int(x) for x in (st.get('grabbed_tmdb_ids') or []) if str(x).lstrip('-').isdigit())
    grabbed |= gr_store.grabbed_ids()
    parked = active_parked(st)
    year_from, year_to = int(cfg['year_from']), int(cfg['year_to'])
    language = cfg.get('language') or 'en'
    imdb_votes = int(cfg.get('imdb_min_votes') or 0) if imdb_gate_on(cfg) else 0
    min_votes = prefilter_votes(int(cfg.get('min_votes') or 0), imdb_votes)
    want = wanted(cfg, len(genres))
    excluded = set(own['ids']) | grabbed | parked
    target = max(want * FRESH_PER_WANTED, MIN_FRESH_POOL)
    if imdb_votes >= 200_000:
        target = max(target, want * 40)
    elif imdb_votes >= 120_000:
        target = max(target, want * 25)
    elif imdb_votes >= 80_000:
        target = max(target, want * 15)
    sig = cursor_sig(year_from, year_to, min_votes, language)
    walk = {'excluded': excluded, 'hard': set(own['ids']) | grabbed, 'cursors': read_cursors(st, sig),
            'ctxs': {}, 'sig': sig}
    pool: Dict[int, Dict[str, Any]] = {}
    failed: List[str] = []
    reached, widened, stages_run = 0, '', []
    this_year = datetime.now().year
    def params(stage: Dict[str, Any]):
        s_from, s_to = (WIDE_YEAR_FROM, this_year) if stage.get('years') else (year_from, year_to)
        s_votes = min(min_votes, stage.get('min_votes', min_votes))
        return lambda g: discover_params(key, g, s_from, s_to, s_votes, language)

    for stage in WIDENING_STAGES:
        if len([t for t in pool if t not in excluded]) >= target:
            break
        s_genres = stage_genres(stage, genres, cfg, rng)
        got, bad = draw_stage(params(stage), s_genres, walk, target, pool, stage['name'])
        stages_run.append(stage['name'])
        reached = max(reached, got)
        if stage is WIDENING_STAGES[0]:
            failed = bad
        elif got:
            widened = stage['name']
    if not reached:
        return {'error': 'TMDb discover failed', 'films': [], 'genres': genres}
    tys = own.get('title_years') or set()
    fresh = [f for f in pool.values() if f['tmdb_id'] not in excluded and (norm(f['title']), f['year']) not in tys]
    return {'films': best_first(fresh, genres, rng), 'genres': genres, 'genres_failed': failed,
            'cursors': next_cursors(walk), 'considered': len(pool),
            'pages_read': sum(c['pages'] for c in walk['ctxs'].values()), 'widened': widened,
            'stages_run': stages_run, 'excluded': excluded, 'min_votes': min_votes,
            'walk': walk, 'pool': pool, 'params': params}


def deeper(cand: Dict[str, Any], also_exclude: set, need: int, title_years: set) -> List[Dict[str, Any]]:
    """v252 (1b): more of this run's own genres from deeper pages (same queries, same cursors), so a run
    whose first pool ran dry keeps walking its genres before it borrows others. [] when they are walked out."""
    walk, pool = cand['walk'], cand['pool']
    walk['excluded'] = walk['excluded'] | also_exclude
    before = set(pool)
    for stage in WIDENING_STAGES[:2]:                       # the configured window, then all years
        draw_stage(cand['params'](stage), list(cand['genres']), walk, max(1, need), pool, stage['name'])
        new = [f for t, f in pool.items() if t not in before and t not in walk['excluded']
               and (norm(f['title']), f['year']) not in title_years]
        if new:
            return best_first(new, list(cand['genres']), random.Random(0))
    return []


def supplement(cfg: Dict[str, Any], genres: List[str], exclude: set, need: int, min_votes: int) -> List[Dict[str, Any]]:
    """More unowned discover hits for genres not tried this run (prod :1746)."""
    key = tmdb_key()
    if not genres or need <= 0 or not key:
        return []
    walk = {'excluded': set(exclude), 'hard': set(exclude), 'cursors': {}, 'ctxs': {}, 'sig': 'supplement'}
    pool: Dict[int, Dict[str, Any]] = {}
    lang = cfg.get('language') or 'en'
    draw_stage(lambda g: discover_params(key, g, WIDE_YEAR_FROM, datetime.now().year, min_votes, lang),
               list(genres), walk, need, pool, 'supplement')
    out = [f for f in pool.values() if f['tmdb_id'] not in exclude]
    out.sort(key=lambda f: -(f.get('votes') or 0))
    return out


def wanted(cfg: Dict[str, Any], genres: int) -> int:
    return max(1, genres * int(cfg.get('movies_per_genre') or 1))


# ------------------------------------------------------------------ details, IMDb gate, collections

def details(tid: int, cache: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """{'imdb_id', 'collection', 'runtime'} from one TMDb /movie call (cached); None when TMDb failed."""
    k = str(int(tid))
    hit = cache.get(k)
    if isinstance(hit, dict):
        return hit
    body = tmdb_get(f'/movie/{int(tid)}', {'api_key': tmdb_key()})
    if body is None:
        return None
    col = body.get('belongs_to_collection')
    out = {'imdb_id': str(body.get('imdb_id') or '').strip(),
           'collection': int(col['id']) if isinstance(col, dict) and str(col.get('id') or '').isdigit() else 0,
           'runtime': int(body['runtime']) if isinstance(body.get('runtime'), int) and body['runtime'] > 0 else None}
    if len(cache) >= DETAILS_CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[k] = out
    return out


def collection_parts(cid: int, cache: Dict[int, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """[{'id', 'title', 'year'}] of a TMDb collection ([] on failure: never blocks)."""
    if cid in cache:
        return cache[cid]
    body = tmdb_get(f'/collection/{int(cid)}', {'api_key': tmdb_key()})
    parts = []
    for p in (body or {}).get('parts') or []:
        if str(p.get('id') or '').isdigit():
            y = str(p.get('release_date') or '')[:4]
            parts.append({'id': int(p['id']), 'title': p.get('title') or '', 'year': int(y) if y.isdigit() else None})
    cache[cid] = parts
    return parts


def imdb_gate_on(cfg: Dict[str, Any]) -> bool:
    return omdb_key_set() and bool(int(cfg.get('imdb_min_votes') or 0) or float(cfg.get('imdb_min_rating') or 0))


def clears(stats: Optional[Dict[str, Any]], min_votes: int, min_rating: float) -> bool:
    """Whether known IMDb numbers clear the floors (prod :1729)."""
    if not isinstance(stats, dict):
        return False
    votes, rating = stats.get('votes'), stats.get('rating')
    if min_votes and votes is None:
        return False
    if votes is None and rating is None:
        return False
    if min_votes and (votes or 0) < min_votes:
        return False
    if min_rating and (rating or 0) < min_rating:
        return False
    return True


def relax_ladder(configured: int) -> List[int]:
    if configured <= IMDB_RELAX_VOTE_FLOOR:
        return []
    out = [s for s in IMDB_RELAX_VOTE_STOPS if IMDB_RELAX_VOTE_FLOOR <= s < configured]
    if IMDB_RELAX_VOTE_FLOOR not in out:
        out.append(IMDB_RELAX_VOTE_FLOOR)
    return out


def imdb_of(film: Dict[str, Any], cache: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """{'rating', 'votes'} or None when it could not ask (fail open)."""
    d = details(film['tmdb_id'], cache)
    if not d:
        return None
    if not d['imdb_id']:
        return {'rating': None, 'votes': None}
    return imdb_ratings(d['imdb_id'])

