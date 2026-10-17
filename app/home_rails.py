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
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
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
TMDB_POSTER = 'https://image.tmdb.org/t/p/w342'


def init(*, get_plex: Callable[[], Any], get_current_user: Callable[[], Optional[dict]],
         trakt_connected: Callable[[], bool] = lambda: False,
         fetch_trakt: Optional[Callable[[str], List[Dict[str, Any]]]] = None):
    _cfg.update(get_plex=get_plex, get_current_user=get_current_user,
                trakt_connected=trakt_connected, fetch_trakt=fetch_trakt)


def trakt_rails() -> List[Dict[str, Any]]:
    """Trakt rails as list entries, only while Trakt is connected."""
    try:
        on = bool(_cfg.get('trakt_connected', lambda: False)())
    except Exception:
        on = False
    if not on:
        return []
    return [{'id': rid, 'title': title, 'type': kind, 'source': 'trakt'} for rid, title, kind, _ in TRAKT_RAILS]


def trakt_items(rail_id: str) -> List[Dict[str, Any]]:
    """Cards for a Trakt rail from prod's endpoint output. Tapping one opens the
    TMDB detail popup (tmdb_id); no Plex rating key."""
    fetch = _cfg.get('fetch_trakt')
    kind = next((k for rid, _, k, _ in TRAKT_RAILS if rid == rail_id), None)
    if not fetch or not kind:
        return []
    try:
        raw = fetch(rail_id) or []
    except Exception as e:  # noqa: BLE001 - a rail failing must not break Home
        logger.warning('Trakt rail %s failed: %s', rail_id, type(e).__name__)
        return []
    out = []
    for it in raw[:RAIL_LIMIT]:
        tmdb_id = it.get('tmdb_id') if isinstance(it, dict) else None
        if not isinstance(tmdb_id, int) or tmdb_id <= 0:
            continue
        poster = it.get('poster_path')
        out.append({'key': f'tmdb-{kind}-{tmdb_id}', 'type': kind, 'title': str(it.get('title') or ''),
                    'subtitle': str(it.get('year') or ''), 'tmdb_id': tmdb_id,
                    'poster': f'{TMDB_POSTER}{poster}' if isinstance(poster, str) and poster.startswith('/') else None})
    return out


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
        return None, (jsonify({'error': 'Authentication required'}), 401)
    return user, None


@bp.route('/api/home', methods=['GET'])
def home():
    _, err = _need_user()
    if err:
        return err
    plex = _plex()
    sections = _sections_or_none(plex) if plex is not None else None
    if sections is None:
        return jsonify({'continue_watching': [], 'rails': [], 'plex': False})
    libs = [l for l in arrange(sections, load_prefs(), trakt_rails()) if l['enabled']]
    from flask import current_app
    flask_app = current_app._get_current_object()

    def in_app(fn, *args):
        # Worker threads need the app context (image URLs read URL_BASE).
        with flask_app.app_context():
            return fn(*args)

    with ThreadPoolExecutor(max_workers=min(6, len(libs) + 1)) as pool:
        cw = pool.submit(in_app, continue_watching, plex)
        rails = [(l, pool.submit(in_app, trakt_items, l['id']) if l['source'] == 'trakt'
                  else pool.submit(in_app, recently_added, plex, l['id'])) for l in libs]
        out_rails = [{'id': l['id'], 'title': l['title'], 'type': l['type'], 'items': f.result()}
                     for l, f in rails]
        cw_items = cw.result()
    resp = jsonify({'continue_watching': cw_items, 'rails': out_rails, 'plex': True})
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.route('/api/home/libraries', methods=['GET'])
def libraries():
    _, err = _need_user()
    if err:
        return err
    plex = _plex()
    libs = arrange(_sections(plex) if plex else [], load_prefs(), trakt_rails())
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
        # Trakt rails keep their place while Trakt is disconnected.
        order = [s for s in order if s in known or s in TRAKT_IDS]
        hidden = [s for s in hidden if s in known or s in TRAKT_IDS]
    with _lock:
        save_prefs({'order': order, 'hidden': hidden})
    return jsonify({'libraries': arrange(_sections(plex) if plex else [], load_prefs(), trakt_rails())})
