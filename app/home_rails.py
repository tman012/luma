"""Home dashboard (#11): Continue Watching from the owner's Plex On Deck and
a recently-added rail for each selected library, plus the Libraries choice
(which libraries get a rail, in what order).

Once Trakt is connected (Settings > Connectors), prod's Trakt rails (Trending
Movies, Trending Shows, Anticipated Movies, Anticipated Shows) join the same
list: same switch, same drag order, one saved order/visibility for both.

Everything is fetched server-side with the owner's Plex token; the browser
only sees titles and proxied image paths (/api/img/plex, no token).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from flask import Blueprint, jsonify, request

from app import first_run

logger = logging.getLogger(__name__)
bp = Blueprint('home_rails', __name__)

RAIL_TYPES = ('movie', 'show')
CONTINUE_LIMIT = 20
RAIL_LIMIT = 20
POSTER_W = 300
WIDE_W = 480
# v251: /api/home answers within this budget (from the request's start) with every rail that is ready; a rail
# that fails or is still loading is left out, never held for. The phone gives up at 7 s (MinimalHomePage), so
# 6 s leaves a second for the response to reach it.
HOME_DEADLINE_S = 6.0
# Rail fetches run side by side, one worker each (with fewer workers, the network-bound Trakt rails queued
# behind the Plex rails and missed the 6 s budget); more rails than this share workers.
HOME_MAX_WORKERS = 12

_cfg: Dict[str, Any] = {}
_lock = threading.Lock()


# Prod's Trakt rails (exploreRails.ts): id, title, card type, prod endpoint.
TRAKT_RAILS = (
    ('trakt_movies', 'Trending Movies', 'movie', '/api/trakt/movies/trending'),
    ('trakt_shows', 'Trending Shows', 'show', '/api/trakt/shows/trending'),
    ('trakt_anticipated_movies', 'Anticipated Movies', 'movie', '/api/trakt/movies/anticipated'),
    ('trakt_anticipated_shows', 'Anticipated Shows', 'show', '/api/trakt/shows/anticipated'),
)
TRAKT_IDS = frozenset(r[0] for r in TRAKT_RAILS)
# v208: prod's Home Watchlist rail (homeRails.ts 'Watchlist'), from the native watchlist on test.
WATCHLIST_ID = 'watchlist'
# v254: What you're missing (app/daily_missing), a regular rail: same switch, same drag order, last by default.
WYM_ID = 'what_youre_missing'
WYM_TITLE = "What you're missing"
KEEP_IDS = TRAKT_IDS | {WATCHLIST_ID, WYM_ID}
WATCHLIST_LIMIT = 25  # prod useHomeRails: slice(0, 25)
TMDB_POSTER = 'https://image.tmdb.org/t/p/w342'


def init(*, get_plex: Callable[[], Any], get_current_user: Callable[[], Optional[dict]],
         trakt_connected: Callable[[], bool] = lambda: False,
         fetch_trakt: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
         fetch_poster: Optional[Callable[[int], Optional[str]]] = None):
    _cfg.update(get_plex=get_plex, get_current_user=get_current_user,
                trakt_connected=trakt_connected, fetch_trakt=fetch_trakt, fetch_poster=fetch_poster)


def extra_rails() -> List[Dict[str, Any]]:
    """Trakt rails plus, on test, the native Watchlist rail and (v254) What you're missing, which comes last so
    a saved arrangement picks it up at the end without moving the other rails."""
    from app import instance
    out = trakt_rails()
    if instance.is_test():
        out.append({'id': WATCHLIST_ID, 'title': 'Watchlist', 'type': 'movie', 'source': 'watchlist'})
        out.append({'id': WYM_ID, 'title': WYM_TITLE, 'type': 'movie', 'source': 'wym'})
    return out


def wym_items() -> List[Dict[str, Any]]:
    """Today's What you're missing slate as Home cards (the pool is built in the background, never here; the
    Plex ownership check uses its cached index so the rail fits the Home budget)."""
    try:
        from app import daily_missing
        rail = daily_missing.get_rail(cached_owned=True)
    except Exception as e:  # noqa: BLE001 - a rail failing must not break Home
        logger.warning("What you're missing rail failed: %s", type(e).__name__)
        return []
    out = []
    for m in rail.get('movies') or []:
        tid = m.get('tmdb_id')
        if not isinstance(tid, int) or tid <= 0:
            continue
        poster = m.get('poster_path')
        out.append({'key': f'missing-{tid}', 'type': 'movie', 'title': str(m.get('title') or ''),
                    'subtitle': str(m.get('year') or ''), 'tmdb_id': tid,
                    'poster': f'{TMDB_POSTER}{poster}' if isinstance(poster, str) and poster.startswith('/') else None})
    return out


def _can_dismiss() -> bool:
    """The rail's long-press dismiss is the signed-in owner's (the implicit LAN owner gets 401 from the route)."""
    try:
        from app import tv_settings
        return tv_settings._signed_in_owner() is None
    except Exception:  # noqa: BLE001
        return False


def watchlist_items() -> List[Dict[str, Any]]:
    """Prod's rail: soonest digital release first (none last), 25 max, the
    digital-date label under the poster. Dates come from the native watchlist rows."""
    try:
        from app import movie_watchlist, digital_release_cache as drc
        rows = movie_watchlist.movies()
        drc.enrich_watchlist_items(rows)
    except Exception as e:  # noqa: BLE001 - a rail failing must not break Home
        logger.warning('Watchlist rail failed: %s', type(e).__name__)
        return []
    rows.sort(key=lambda m: (m.get('digital_release_date') is None, m.get('digital_release_date') or ''))
    poster_of = _cfg.get('fetch_poster')
    out = []
    for m in rows[:WATCHLIST_LIMIT]:
        tid = m.get('tmdb_id')
        poster = None
        if poster_of:
            try:
                poster = poster_of(int(tid))
            except Exception:  # noqa: BLE001
                poster = None
        out.append({'key': f'watchlist-{tid}', 'type': 'movie', 'title': str(m.get('title') or ''),
                    'subtitle': str(m.get('year') or ''), 'label': m.get('digital_release_label') or None, 'tmdb_id': tid,
                    'poster': f'{TMDB_POSTER}{poster}' if isinstance(poster, str) and poster.startswith('/') else None})
    return out


def trakt_rails() -> List[Dict[str, Any]]:
    """Trakt rails as list entries, only while Trakt is connected."""
    try:
        on = bool(_cfg.get('trakt_connected', lambda: False)())
    except Exception:
        on = False
    if not on:
        return []
    return [{'id': rid, 'title': title, 'type': kind, 'source': 'trakt'} for rid, title, kind, _ in TRAKT_RAILS]


# v263: each Trakt rail's cards live on disk (home_trakt_rails.json in the data dir), so a restart or a cold Trakt
# cache never starts Home from nothing. Fresh for RAIL_FRESH_S (prod's 15-minute Trakt cache); after that the
# saved cards are still served at once while one background refresh per rail fetches new ones. A rail with no
# saved cards yet is fetched in the background too: Home waits for it only up to its 6 s budget, and a fetch
# that misses the budget keeps going and fills the rail for the next load. Cards only; never the client ID.
RAIL_FRESH_S = 15 * 60
TRAKT_FILE = 'home_trakt_rails.json'
WARM_DELAY_S = 8                    # boot: the same startup delay as What you're missing's pool refresher

_trakt_saved: Dict[str, Dict[str, Any]] = {}       # rail id -> {'at': epoch seconds, 'cards': [...]}
_trakt_state = {'loaded': False, 'gen': 0, 'warm_started': False}
_trakt_inflight: Dict[str, Future] = {}


def _trakt_path():
    return first_run.data_dir() / TRAKT_FILE


def _load_trakt_locked() -> None:
    if _trakt_state['loaded']:
        return
    _trakt_state['loaded'] = True
    try:
        raw = json.loads(_trakt_path().read_text())
    except (FileNotFoundError, ValueError, OSError):
        return
    if not isinstance(raw, dict):
        return
    for rid, entry in raw.items():
        if rid not in TRAKT_IDS or not isinstance(entry, dict):
            continue
        cards, at = entry.get('cards'), entry.get('at')
        if isinstance(cards, list) and cards and isinstance(at, (int, float)):
            _trakt_saved[rid] = {'at': float(at), 'cards': [c for c in cards if isinstance(c, dict)]}


def _save_trakt_locked() -> None:
    p = _trakt_path()
    tmp = p.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(_trakt_saved))
        os.chmod(tmp, 0o600)
        tmp.replace(p)
    except OSError as e:
        logger.warning('Home: Trakt rails not saved (%s)', type(e).__name__)


def clear_trakt_last() -> None:
    """A Connectors save or remove: no old cards survive a changed or removed client ID (memory and disk); a
    refresh still running for the old ID is discarded."""
    with _lock:
        _trakt_state['gen'] += 1
        _trakt_state['loaded'] = True
        _trakt_saved.clear()
        _trakt_inflight.clear()
        try:
            _trakt_path().unlink()
        except OSError:
            pass


def _entry(rail_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        _load_trakt_locked()
        got = _trakt_saved.get(rail_id)
        return {'at': got['at'], 'cards': list(got['cards'])} if got else None


def trakt_last(rail_id: str) -> Optional[List[Dict[str, Any]]]:
    got = _entry(rail_id)
    return got['cards'] if got else None


def _fetch_cards(rail_id: str) -> List[Dict[str, Any]]:
    """Cards for a Trakt rail from prod's endpoint output. Tapping one opens the TMDB detail popup (tmdb_id);
    no Plex rating key. Raises when the fetch fails."""
    fetch = _cfg.get('fetch_trakt')
    kind = next((k for rid, _, k, _ in TRAKT_RAILS if rid == rail_id), None)
    if not fetch or not kind:
        return []
    raw = fetch(rail_id) or []
    if not isinstance(raw, list):
        logger.warning('Trakt rail %s: unexpected answer (%s)', rail_id, type(raw).__name__)
        raw = []
    out = []
    for it in raw[:RAIL_LIMIT]:
        tmdb_id = it.get('tmdb_id') if isinstance(it, dict) else None
        if not isinstance(tmdb_id, int) or tmdb_id <= 0:
            continue
        poster = it.get('poster_path')
        out.append({'key': f'tmdb-{kind}-{tmdb_id}', 'type': kind, 'title': str(it.get('title') or ''),
                    'subtitle': str(it.get('year') or ''), 'tmdb_id': tmdb_id,
                    'poster': f'{TMDB_POSTER}{poster}' if isinstance(poster, str) and poster.startswith('/') else None})
    if not out and raw:
        logger.warning('Trakt rail %s: %d item(s), none with a TMDB id', rail_id, len(raw))
    return out


def refresh_trakt(rail_id: str) -> Future:
    """One background fetch per rail at a time (a second caller gets the same future). Good cards are saved
    (memory and disk); a failure is logged and keeps the saved cards. The future answers the new cards, or
    raises the fetch's error."""
    with _lock:
        running = _trakt_inflight.get(rail_id)
        if running is not None and not running.done():
            return running
        fut: Future = Future()
        _trakt_inflight[rail_id] = fut
        gen = _trakt_state['gen']

    def done() -> None:
        with _lock:
            if _trakt_inflight.get(rail_id) is fut:
                _trakt_inflight.pop(rail_id, None)

    def work():
        try:
            cards = _fetch_cards(rail_id)
        except Exception as e:  # noqa: BLE001 - a rail failing must not break Home
            last = trakt_last(rail_id)
            logger.warning('Trakt rail %s failed: %s%s', rail_id, type(e).__name__,
                           f'; showing its last {len(last)} cards' if last else '')
            done()
            fut.set_exception(e)
            return
        if cards:
            with _lock:
                if _trakt_state['gen'] == gen:                  # not from a client ID removed meanwhile
                    _load_trakt_locked()
                    _trakt_saved[rail_id] = {'at': time.time(), 'cards': list(cards)}
                    _save_trakt_locked()
        done()
        fut.set_result(cards)

    threading.Thread(target=work, name=f'trakt-rail-{rail_id}', daemon=True).start()
    return fut


def trakt_for_home(rail_id: str):
    """(cards, None) when the rail has saved cards (a stale set also starts a background refresh), else
    (None, future) for the background fetch Home may wait on until its deadline."""
    got = _entry(rail_id)
    if got and time.time() - got['at'] < RAIL_FRESH_S:
        return got['cards'], None
    fut = refresh_trakt(rail_id)
    if got:
        return got['cards'], None
    return None, fut


def trakt_items(rail_id: str) -> List[Dict[str, Any]]:
    """A Trakt rail's cards, waiting for a fetch when nothing is saved yet. A failed fetch answers the rail's
    last good cards (none yet: empty)."""
    cards, fut = trakt_for_home(rail_id)
    if cards is not None:
        return cards
    try:
        return fut.result()
    except Exception:  # noqa: BLE001 - logged by refresh_trakt
        return trakt_last(rail_id) or []


def warm_trakt() -> List[str]:
    """Boot: start a background refresh for each shown Trakt rail with no fresh saved cards."""
    try:
        on = bool(_cfg.get('trakt_connected', lambda: False)())
    except Exception:  # noqa: BLE001
        on = False
    if not on or not _cfg.get('fetch_trakt'):
        return []
    hidden = set(load_prefs().get('hidden') or [])
    started = []
    for rid, _, _, _ in TRAKT_RAILS:
        got = _entry(rid)
        if rid in hidden or (got and time.time() - got['at'] < RAIL_FRESH_S):
            continue
        refresh_trakt(rid)
        started.append(rid)
    if started:
        logger.info('Home: warming Trakt rails %s', ', '.join(started))
    return started


def start_trakt_warm() -> bool:
    """Once per process, after WARM_DELAY_S: warm_trakt(). Reads Trakt, TMDb and OMDb and writes only its JSON
    in the data dir (instance.DATA_DIR_ONLY), like What you're missing's pool refresher."""
    from app import instance
    with _lock:
        if _trakt_state['warm_started'] or not instance.data_dir_only('home_trakt_rails'):
            return False
        _trakt_state['warm_started'] = True

    def run():
        time.sleep(WARM_DELAY_S)
        try:
            warm_trakt()
        except Exception as e:  # noqa: BLE001
            logger.warning('Home: Trakt warm-up failed: %s', type(e).__name__)
    threading.Thread(target=run, name='home-trakt-warm', daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# Saved choice: {"order": [section ids], "hidden": [section ids]}
# ---------------------------------------------------------------------------

def _prefs_path():
    return first_run.data_dir() / 'home_libraries.json'


def load_prefs() -> Dict[str, List[str]]:
    try:
        raw = json.loads(_prefs_path().read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {'order': [], 'hidden': []}
    if not isinstance(raw, dict):
        return {'order': [], 'hidden': []}
    clean = lambda v: [str(x) for x in v if isinstance(x, (str, int))] if isinstance(v, list) else []
    return {'order': clean(raw.get('order')), 'hidden': clean(raw.get('hidden'))}


def save_prefs(prefs: Dict[str, List[str]]) -> None:
    p = _prefs_path()
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(prefs, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def arrange(sections: List[Dict[str, Any]], prefs: Dict[str, List[str]],
            extra: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Movie and show libraries plus any *extra* rails (Trakt) in one saved
    order (new ones last: Plex's order, then the extras), each with
    enabled = not hidden. All on by default."""
    usable = [s for s in sections if s.get('type') in RAIL_TYPES] + list(extra or [])
    rank = {sid: i for i, sid in enumerate(prefs.get('order') or [])}
    hidden = set(prefs.get('hidden') or [])
    indexed = list(enumerate(usable))
    indexed.sort(key=lambda t: (rank.get(str(t[1]['id']), len(rank) + t[0])))
    return [{'id': str(s['id']), 'title': s.get('title', ''), 'type': s['type'],
             'source': s.get('source', 'plex'), 'enabled': str(s['id']) not in hidden} for _, s in indexed]


# ---------------------------------------------------------------------------
# Plex
# ---------------------------------------------------------------------------

def _img(path: Optional[str], width: int) -> Optional[str]:
    if not path or not str(path).startswith(('/library/', '/photo/')):
        return None
    # Full path under the URL base, so the browser can use it as is.
    from flask import current_app
    base = (current_app.config.get('URL_BASE') or '').rstrip('/')
    return f"{base}/api/img/plex?url={quote(path, safe='')}&w={width}"


def _sections(plex) -> List[Dict[str, Any]]:
    return _sections_or_none(plex) or []


def _sections_or_none(plex) -> Optional[List[Dict[str, Any]]]:
    """None when Plex doesn't answer, so Home can say "Can't reach Plex"."""
    root = plex._get('/library/sections', timeout=8)
    if root is None:
        return None
    return [{'id': d.get('key', ''), 'title': d.get('title', ''), 'type': d.get('type', '')}
            for d in root.findall('./Directory') if d.get('key')]


def _card(el: ET.Element, wide: bool = False) -> Optional[Dict[str, Any]]:
    """A rail card from a Plex <Video> or <Directory>."""
    kind = el.get('type', '')
    key = el.get('ratingKey')
    if not key:
        return None
    title = el.get('title', '')
    show_key = None
    subtitle = el.get('year', '') or ''
    poster = el.get('thumb')
    if kind == 'episode':
        title = el.get('grandparentTitle') or title
        s, e = el.get('parentIndex'), el.get('index')
        subtitle = f'S{s} E{e}' if s and e else el.get('title', '')
        poster = el.get('grandparentThumb') or el.get('parentThumb') or poster
        show_key = el.get('grandparentRatingKey')
    elif kind == 'season':
        title = el.get('parentTitle') or title
        subtitle = el.get('title', '')
        poster = el.get('parentThumb') or poster
        show_key = el.get('parentRatingKey')
    card: Dict[str, Any] = {
        'key': key, 'type': kind, 'title': title, 'subtitle': subtitle,
        'poster': _img(poster, POSTER_W),
    }
    # Tapping a card opens the detail popup: episodes and seasons open their show.
    if show_key:
        card['show_key'] = show_key
    if kind == 'episode' and el.get('parentIndex') and el.get('index'):
        card['season'], card['episode'] = int(el.get('parentIndex')), int(el.get('index'))
    if wide:
        card['art'] = _img(el.get('art') or el.get('grandparentArt'), WIDE_W)
        duration = int(el.get('duration') or 0)
        offset = int(el.get('viewOffset') or 0)
        card['progress'] = round(offset / duration, 3) if duration > 0 else 0
    return card


def _items(root: Optional[ET.Element], wide: bool = False) -> List[Dict[str, Any]]:
    if root is None:
        return []
    out = []
    for el in list(root):
        if el.tag in ('Video', 'Directory'):
            c = _card(el, wide)
            if c:
                out.append(c)
    return out


def continue_watching(plex) -> List[Dict[str, Any]]:
    return _items(plex._get('/library/onDeck', params={'X-Plex-Container-Start': 0,
                                                       'X-Plex-Container-Size': CONTINUE_LIMIT}, timeout=10),
                  wide=True)


def recently_added(plex, section_id: str) -> List[Dict[str, Any]]:
    return _items(plex._get(f'/library/sections/{quote(str(section_id), safe="")}/recentlyAdded',
                            params={'X-Plex-Container-Start': 0, 'X-Plex-Container-Size': RAIL_LIMIT},
                            timeout=10))


def _plex():
    plex = _cfg['get_plex']()
    if plex is None or not getattr(plex, 'is_configured', False):
        return None
    return plex


# ---------------------------------------------------------------------------
# Routes (sign-in required: the app's auth gate runs first; checked again here)
# ---------------------------------------------------------------------------

def _need_user():
    user = _cfg['get_current_user']()
    if not user:
        return None, (jsonify({'error': 'Authentication required', 'sign_in': True}), 401)
    return user, None


@bp.route('/api/home', methods=['GET'])
def home():
    _, err = _need_user()
    if err:
        return err
    start = time.monotonic()
    plex = _plex()
    sections = _sections_or_none(plex) if plex is not None else None
    if sections is None:
        return jsonify({'continue_watching': [], 'rails': [], 'plex': False})
    libs = [l for l in arrange(sections, load_prefs(), extra_rails()) if l['enabled']]
    from flask import current_app
    flask_app = current_app._get_current_object()

    def in_app(fn, *args):
        # Worker threads need the app context (image URLs read URL_BASE).
        with flask_app.app_context():
            return fn(*args)

    deadline = start + HOME_DEADLINE_S
    dropped: List[str] = []

    def take(rid: str, fut, source: str = 'plex'):
        try:
            return fut.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception as e:  # noqa: BLE001 - a failed or slow rail is dropped, never fails Home
            last = trakt_last(rid) if source == 'trakt' else None
            if source == 'trakt' and not isinstance(e, TimeoutError):
                return last or []                           # a failed Trakt fetch (logged): last cards or empty
            if last:                                        # a slow Trakt fetch: its last good cards
                logger.info('Home: rail %s still loading (%s); showing its last cards', rid, type(e).__name__ or 'error')
                return last
            dropped.append(rid)
            logger.info('Home: rail %s dropped (%s)', rid, type(e).__name__ or 'error')
            return None

    def _trakt_future(rid: str) -> Future:
        # v263: saved cards at once (a stale set refreshes in the background); none saved: the background
        # fetch, which keeps going past the deadline and fills the rail for the next load
        cards, fut = trakt_for_home(rid)
        if fut is None:
            fut = Future()
            fut.set_result(cards)
        return fut

    pool = ThreadPoolExecutor(max_workers=min(HOME_MAX_WORKERS, len(libs) + 1))
    try:
        cw = pool.submit(in_app, continue_watching, plex)
        rails = [(l, _trakt_future(l['id']) if l['source'] == 'trakt'
                  else pool.submit(in_app, watchlist_items) if l['source'] == 'watchlist'
                  else pool.submit(in_app, wym_items) if l['source'] == 'wym'
                  else pool.submit(in_app, recently_added, plex, l['id'])) for l in libs]
        cw_items = take('continue_watching', cw)
        out_rails = []
        for l, f in rails:
            items = take(l['id'], f, l['source'])
            if items is not None:
                rail = {'id': l['id'], 'title': l['title'], 'type': l['type'], 'items': items}
                if l['source'] == 'wym' and items and _can_dismiss():
                    rail['dismiss'] = True
                out_rails.append(rail)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)     # a slow Plex call finishes in the background
    if cw_items is None and not out_rails and dropped:
        return jsonify({'continue_watching': [], 'rails': [], 'plex': False})
    cw_items = cw_items or []
    resp = jsonify({'continue_watching': cw_items, 'rails': out_rails, 'plex': True})
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.route('/api/home/libraries', methods=['GET'])
def libraries():
    _, err = _need_user()
    if err:
        return err
    plex = _plex()
    libs = arrange(_sections(plex) if plex else [], load_prefs(), extra_rails())
    return jsonify({'libraries': libs})


@bp.route('/api/home/libraries', methods=['PUT'])
def save_libraries():
    user, err = _need_user()
    if err:
        return err
    if not user.get('is_admin'):
        return jsonify({'error': 'Admin access required'}), 403
    body = request.get_json(silent=True) or {}
    rows = body.get('libraries')
    if not isinstance(rows, list) or len(rows) > 500:
        return jsonify({'error': 'Invalid libraries'}), 400
    order: List[str] = []
    hidden: List[str] = []
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get('id'), (str, int)) or not isinstance(r.get('enabled'), bool):
            return jsonify({'error': 'Invalid libraries'}), 400
        sid = str(r['id'])[:32]
        if sid in order:
            continue
        order.append(sid)
        if not r['enabled']:
            hidden.append(sid)
    plex = _plex()
    known = {l['id'] for l in arrange(_sections(plex), {'order': [], 'hidden': []})} if plex else set()
    if known:
        # Trakt rails keep their place while Trakt is disconnected; so does Watchlist.
        order = [s for s in order if s in known or s in KEEP_IDS]
        hidden = [s for s in hidden if s in known or s in KEEP_IDS]
    with _lock:
        before = WYM_ID in set(load_prefs().get('hidden') or [])
        save_prefs({'order': order, 'hidden': hidden})
    if before != (WYM_ID in hidden):
        try:
            from app import daily_missing
            daily_missing.wake()        # shown again: the pool refresher looks now, not at its next check
        except Exception:  # noqa: BLE001
            pass
    return jsonify({'libraries': arrange(_sections(plex) if plex else [], load_prefs(), extra_rails())})
