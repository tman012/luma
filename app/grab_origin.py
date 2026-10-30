"""grab_origin — who started each download, for the Activity feed badge.

Origin is decided server-side, per completed torrent, from real evidence:

1. Luma's own origin store (data/grab_origins.json), written at grab time by
   Luma itself and keyed by info-hash / release guid / download URL:
   Sources grabs ("manual"), Genre Roulette, request adds, and backfills.
2. Radarr/Sonarr grab history for the torrent (downloadId = info-hash), whose
   releaseSource says how the *arr picked it: ReleasePush (something pushed a
   chosen release), InteractiveSearch, UserInvokedSearch, Search or Rss.
3. Luma automation logs (Genre Roulette, Movie Night, Latest Feed, 4K Remux,
   Franchise Completer, Trending, Trending TV, Now Available, Anticipated),
   matched by tmdb id (or title + year) and time relative to the grab.

"Manual" is ONLY a release the user picked in Luma's Sources / Latest sheet /
All Sources. Anything that can't be attributed gets the neutral "Other".
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

LABELS = {
    'manual': 'Manual',
    'genre_roulette': 'Genre Roulette',      # v269 PM: ActivityFeed matches prod 1b4ea74 Title Case
    'movie_night': 'Movie night',
    'latest_feed': 'Latest feed',
    'upgrade_4k': '4K Remux',
    'franchise': 'Franchise completer',
    'trending': 'Trending',
    'trending_tv': 'Trending TV',
    'now_available': 'Now available',
    'anticipated': 'Anticipated',
    'requested': 'Requested',
    'upgrade': 'Upgrade',
    'auto': 'Auto',
    'tv_auto': 'TV Auto',
    'other': 'Other',
}

# Automations that pick a release and push it right away: their log row is
# written within minutes of the *arr grab.
_PUSHERS = {'genre_roulette', 'movie_night', 'latest_feed', 'upgrade_4k', 'franchise'}
# Automations that only add a title (with search); the *arr grabs it soon after.
_ADDERS = {'trending', 'trending_tv', 'now_available', 'anticipated'}
_ADDER_WINDOW = 3 * 86400

_lock = threading.Lock()
_cache: Dict[str, Any] = {}
_CACHE_TTL = 120


def _data_dir() -> Path:
    d = Path('/app/data')
    if d.is_dir():
        return d
    db = os.environ.get('DATABASE_PATH', '')
    if db:
        return Path(db).parent
    return Path(__file__).resolve().parent.parent / 'data'


def _store_path() -> Path:
    return _data_dir() / 'grab_origins.json'


def _read_json(name: str, default):
    try:
        p = _data_dir() / name
        if p.exists():
            with open(p) as f:
                return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.debug('grab_origin: could not read %s: %s', name, e)
    return default


def load_store() -> List[Dict[str, Any]]:
    d = _read_json('grab_origins.json', [])
    return d if isinstance(d, list) else []


def _app_category() -> str:
    """qBittorrent category used for downloads started from Luma."""
    try:
        from app.settings_manager import SettingsManager
        return ((SettingsManager().get('qbittorrent.app_category') or 'luma').strip() or 'luma').lower()
    except Exception:
        return 'luma'


def _norm_hash(h) -> str:
    h = (h or '').strip().lower()
    return h if re.fullmatch(r'[0-9a-f]{40}', h) else ''


def _btih(url: str) -> str:
    from app.magnet import btih            # v171+: the shared magnet parser
    return btih(url) or ''


def record(origin: str, *, info_hash: str = '', guid: str = '', download_url: str = '',
           tmdb_id=None, media_type: str = '', title: str = '', series_id=None,
           episode_ids=None, evidence: str = '', ts: Optional[float] = None,
           hash_only: bool = False) -> None:
    """Remember who started a grab. Never raises."""
    if origin not in LABELS:
        return
    try:
        entry = {
            'origin': origin,
            'hash': _norm_hash(info_hash) or _btih(download_url),
            'guid': (guid or '').strip(),
            'download_url': (download_url or '').strip(),
            'tmdb_id': int(tmdb_id) if str(tmdb_id or '').isdigit() else None,
            'media_type': media_type or '',
            'title': title or '',
            'series_id': series_id,
            'episode_ids': list(episode_ids or []),
            'ts': ts or time.time(),
        }
        if evidence:
            entry['evidence'] = evidence
        if hash_only:
            entry['hash_only'] = True
        with _lock:
            items = load_store()
            if entry['hash']:
                items = [x for x in items if x.get('hash') != entry['hash']]
            items.insert(0, entry)
            del items[3000:]
            p = _store_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix('.tmp')
            with open(tmp, 'w') as f:
                json.dump(items, f)
            os.replace(tmp, p)
            _cache.pop('ctx', None)
    except Exception as e:  # noqa: BLE001
        logger.warning('grab_origin: record failed: %s', e)


# ---------------------------------------------------------------------------
# Evidence gathering
# ---------------------------------------------------------------------------

def _to_ts(v) -> Optional[float]:
    if v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else float(v)
    try:
        s = str(v).replace('Z', '+00:00')
        d = datetime.fromisoformat(s)
        return d.timestamp()
    except Exception:  # noqa: BLE001
        return None


def _norm_title(t: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', (t or '').lower())


def _automation_events() -> Dict[str, list]:
    """{'by_tmdb': {tmdb: [(origin, ts)]}, 'by_title': {(title, year): [(origin, ts)]}}"""
    by_tmdb: Dict[int, list] = {}
    by_title: Dict[tuple, list] = {}

    def add(origin, tmdb, ts, title=None, year=None):
        ts = _to_ts(ts)
        if ts is None:
            return
        if tmdb:
            try:
                by_tmdb.setdefault(int(tmdb), []).append((origin, ts))
            except (TypeError, ValueError):
                pass
        if title:
            by_title.setdefault((_norm_title(title), str(year or '')), []).append((origin, ts))

    for name, origin, field in (
        ('latest_auto_grab_log.json', 'latest_feed', 'added_at'),
        ('upgrade_4k_auto_grab_log.json', 'upgrade_4k', 'added_at'),
        ('now_available_watchlist_log.json', 'now_available', 'added_at'),
        ('trending_watchlist_log.json', 'trending', 'added_at'),
        ('trending_tv_watchlist_log.json', 'trending_tv', 'added_at'),
        ('anticipated_watchlist_log.json', 'anticipated', 'added_at'),
    ):
        for e in _read_json(name, []) or []:
            if isinstance(e, dict):
                add(origin, e.get('tmdb_id'), e.get(field))
    for e in _read_json('movie_night_log.json', []) or []:
        if isinstance(e, dict) and e.get('tmdb_id') and e.get('outcome') in (None, 'grabbed'):
            add('movie_night', e.get('tmdb_id'), e.get('at'), e.get('title'), e.get('year'))
    for name, origin, key in (('genre_roulette_log.json', 'genre_roulette', 'titles'),
                              ('franchise_completer_log.json', 'franchise', 'picks')):
        for e in _read_json(name, []) or []:
            if not isinstance(e, dict):
                continue
            for t in e.get(key) or []:
                if isinstance(t, dict) and t.get('grabbed', True):
                    add(origin, t.get('tmdb_id'), e.get('at'), t.get('title'), t.get('year'))
    return {'by_tmdb': by_tmdb, 'by_title': by_title}


def _arr_get(base: str, key: str, path: str, params=None, timeout=15):
    r = requests.get(f"{base.rstrip('/')}/api/v3/{path}", params=params or {},
                     headers={'X-Api-Key': key}, timeout=timeout)
    r.raise_for_status()
    return r.json()


_LEAN = {'includeMovie': 'false', 'includeSeries': 'false', 'includeEpisode': 'false'}


def _arr_context(settings) -> Dict[str, Any]:
    out: Dict[str, Any] = {'grabs': {}, 'by_guid': {}, 'movie_grabs': {}, 'ep_imports': {},
                           'added': {}}
    from concurrent.futures import ThreadPoolExecutor
    jobs = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for kind, ev in (('radarr', 1), ('sonarr', 1), ('sonarr', 3)):
            base, key = settings.get(f'{kind}.url'), settings.get(f'{kind}.api_key')
            if base and key:
                jobs[(kind, ev)] = ex.submit(_arr_get, base, key, 'history', {
                    'pageSize': 300, 'sortKey': 'date', 'sortDirection': 'descending',
                    'eventType': ev, **_LEAN})
    fetched = {}
    for k, f in jobs.items():
        try:
            fetched[k] = f.result().get('records') or []
        except Exception as e:  # noqa: BLE001
            logger.warning('grab_origin: %s history unavailable: %s', k[0], e)
    for kind in ('radarr', 'sonarr'):
        base, key = settings.get(f'{kind}.url'), settings.get(f'{kind}.api_key')
        if not base or not key:
            continue
        out.setdefault('conn', {})[kind] = (base, key)
        if (kind, 1) not in fetched:
            continue
        for r in fetched[(kind, 1)]:
            data = r.get('data') or {}
            g = {
                'kind': kind,
                'ts': _to_ts(r.get('date')),
                'source': data.get('releaseSource') or '',
                'guid': data.get('guid') or '',
                'movie_id': r.get('movieId'),
                'series_id': r.get('seriesId'),
                'episode_id': r.get('episodeId'),
                'tmdb_id': data.get('tmdbId'),
                'download_url': data.get('downloadUrl') or '',
            }
            did = _norm_hash(r.get('downloadId')) or _norm_hash(data.get('torrentInfoHash'))
            if did:
                out['grabs'].setdefault(did, []).append(g)
            if g['guid']:
                out['by_guid'].setdefault(g['guid'], g)
            if kind == 'radarr' and g['movie_id']:
                out['movie_grabs'].setdefault(g['movie_id'], []).append((g['ts'], did))
        if kind == 'sonarr':
            for r in fetched.get(('sonarr', 3), []):
                if r.get('episodeId'):
                    out['ep_imports'].setdefault(r['episodeId'], []).append(
                        (_to_ts(r.get('date')), _norm_hash(r.get('downloadId'))))
    return out


def _added_ts(arr: Dict[str, Any], kind: str, lib_id) -> Optional[float]:
    """When a movie/series was added to Radarr/Sonarr (fetched on demand)."""
    k = (kind, lib_id)
    if k in arr['added']:
        return arr['added'][k]
    ts = None
    conn = (arr.get('conn') or {}).get(kind)
    if conn and lib_id:
        try:
            it = _arr_get(conn[0], conn[1], f"{'series' if kind == 'sonarr' else 'movie'}/{lib_id}", timeout=8)
            ts = _to_ts((it or {}).get('added'))
        except Exception as e:  # noqa: BLE001
            logger.debug('grab_origin: %s %s lookup failed: %s', kind, lib_id, e)
    arr['added'][k] = ts
    return ts


def _context(settings) -> Dict[str, Any]:
    now = time.time()
    c = _cache.get('ctx')
    if c and now - c['at'] < _CACHE_TTL:
        return c
    store = load_store()
    by_hash, by_guid, by_url, by_tmdb = {}, {}, {}, {}
    for e in store:
        if e.get('hash'):
            by_hash.setdefault(e['hash'], e)
        if e.get('guid'):
            by_guid.setdefault(e['guid'], e)
        if e.get('download_url'):
            by_url.setdefault(e['download_url'], e)
        if e.get('tmdb_id') and not e.get('hash_only'):
            by_tmdb.setdefault(int(e['tmdb_id']), []).append(e)
    legacy_manual = {}
    for e in _read_json('manual_grab_log.json', []) or []:
        if isinstance(e, dict) and e.get('tmdb_id'):
            legacy_manual.setdefault(int(e['tmdb_id']), []).append(_to_ts(e.get('ts')))
    c = {
        'at': now,
        'store': {'hash': by_hash, 'guid': by_guid, 'url': by_url, 'tmdb': by_tmdb},
        'legacy_manual': legacy_manual,
        'auto': _automation_events(),
        'arr': None,
        'settings': settings,
    }
    _cache['ctx'] = c
    return c


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _pick_automation(cands: list, grab_ts: float) -> Optional[str]:
    best = None
    for origin, ts in cands:
        if origin in _PUSHERS:
            ok = grab_ts - 6 * 3600 <= ts <= grab_ts + 3 * 3600
        else:
            ok = grab_ts - _ADDER_WINDOW <= ts <= grab_ts + 15 * 60
        if ok:
            d = abs(grab_ts - ts)
            if best is None or d < best[0]:
                best = (d, origin)
    return best[1] if best else None


def _is_upgrade(arr: Dict[str, Any], g: Dict[str, Any], did: str) -> bool:
    ts = g.get('ts') or 0
    if g['kind'] == 'radarr' and g.get('movie_id'):
        return any(t and t < ts - 6 * 3600 and h != did
                   for t, h in arr['movie_grabs'].get(g['movie_id'], []))
    if g['kind'] == 'sonarr' and g.get('episode_id'):
        return any(t and t < ts - 600 and h != did
                   for t, h in arr['ep_imports'].get(g['episode_id'], []))
    return False


def _test_instance() -> bool:
    try:
        from app import instance
        return instance.is_test()
    except Exception:  # noqa: BLE001
        return False


def _added_by(table: str, col: str, val, grab_ts: float, extra: str = '') -> bool:
    """An automation.db log row (ttv_log / antic_log) for *val* added before *grab_ts* (adder window)."""
    from app.automation import gr_store
    try:
        with gr_store.connect() as conn:
            return bool(conn.execute(
                f'SELECT 1 FROM {table} WHERE {col} = ?{extra} AND added_at BETWEEN ? AND ? LIMIT 1',
                (val, grab_ts - _ADDER_WINDOW, grab_ts + 15 * 60)).fetchone())
    except Exception:  # noqa: BLE001 - no table yet
        return False


def _native(did: str, item: Dict[str, Any]) -> Optional[tuple]:
    """luma-test's own grab rows (no Radarr/Sonarr): tv.db grabs.origin, movies.db movie_grabs."""
    if not did:
        return None
    try:
        from app import tv_store
        with tv_store.connect() as conn:
            r = conn.execute('SELECT series_id, origin, grabbed_at FROM grabs WHERE hash = ?', (did,)).fetchone()
    except Exception:  # noqa: BLE001
        r = None
    if r:
        if r['origin'] == 'manual':
            return 'manual', 'tv-grab:manual'
        if r['origin'] == 'upgrade':
            return 'upgrade', 'tv-grab:upgrade'
        ts = r['grabbed_at'] or 0
        if r['series_id'] is not None:
            if _added_by('ttv_log', 'series_id', r['series_id'], ts):
                return 'trending_tv', 'tv-grab:auto+ttv_log'
            if _added_by('antic_log', 'series_id', r['series_id'], ts):
                return 'anticipated', 'tv-grab:auto+antic_log'
        return 'tv_auto', 'tv-grab:auto'
    try:
        from app import movie_grabs
        m = movie_grabs.by_hash(did)
    except Exception:  # noqa: BLE001
        m = None
    if m:
        ts = m.get('grabbed_at') or 0
        if _added_by('antic_log', 'tmdb_id', m['tmdb_id'], ts, " AND kind = 'movies'"):
            return 'anticipated', 'movie-grab+antic_log'
    return None


def resolve_one(item: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, str]:
    did = _norm_hash(item.get('torrent_hash'))
    st = ctx['store']

    def out(origin, why):
        return {'origin': origin, 'origin_label': LABELS[origin], 'origin_evidence': why}

    hit = st['hash'].get(did) if did else None
    if hit:
        return out(hit['origin'], 'luma-store:hash')

    nat = _native(did, item)
    if nat:
        return out(*nat)

    if ctx['arr'] is None and _test_instance():
        ctx['arr'] = {'grabs': {}, 'by_guid': {}, 'movie_grabs': {}, 'ep_imports': {}, 'added': {}}  # no *arr on test
    if ctx['arr'] is None:
        a = _cache.get('arr')
        if not a or time.time() - a['at'] > _CACHE_TTL:
            a = {'at': time.time(), 'data': _arr_context(ctx['settings'])}
            _cache['arr'] = a
        ctx['arr'] = a['data']
    arr = ctx['arr']
    grabs = arr['grabs'].get(did) or []
    g = grabs[0] if grabs else None
    if g:
        e = st['guid'].get(g['guid']) or st['url'].get(g['download_url']) or (
            st['url'].get(g['guid'][5:]) if g['guid'].startswith('PUSH-') else None)
        if e:
            return out(e['origin'], 'luma-store:guid')

    grab_ts = (g or {}).get('ts') or item.get('completed_timestamp') or 0
    tmdb = item.get('tmdb_id')
    try:
        tmdb = int(tmdb) if tmdb else None
    except (TypeError, ValueError):
        tmdb = None

    # Luma's store, by tmdb id close to the grab (manual Sources grab whose
    # hash wasn't known at grab time, or a request add).
    if tmdb:
        best = None
        for e in st['tmdb'].get(tmdb, []):
            ts = e.get('ts') or 0
            if e['origin'] in _ADDERS | {'requested'}:
                win = 86400 if e.get('media_type') == 'tv' else 3 * 86400
            else:
                win = 30 * 60
            if grab_ts - win <= ts <= grab_ts + 15 * 60:
                if e['origin'] == 'manual' and g and g['source'] not in ('InteractiveSearch', 'ReleasePush'):
                    continue
                d = abs(grab_ts - ts)
                if best is None or d < best[0]:
                    best = (d, e['origin'])
        if best:
            return out(best[1], 'luma-store:tmdb+time')

    # Automation logs.
    cands = list(ctx['auto']['by_tmdb'].get(tmdb, [])) if tmdb else []
    cands += ctx['auto']['by_title'].get((_norm_title(item.get('title')), str(item.get('year') or '')), [])
    auto = _pick_automation(cands, grab_ts) if grab_ts else None
    if auto:
        return out(auto, 'automation-log')

    # Legacy manual log (written by Sources grabs AND watchlist adds): only a
    # Sources grab produces an interactive/pushed release within minutes.
    if tmdb and g and g['source'] in ('InteractiveSearch', 'ReleasePush'):
        if any(t and abs(grab_ts - t) <= 30 * 60 for t in ctx['legacy_manual'].get(tmdb, [])):
            return out('manual', 'manual-log+interactive-grab')

    if not g:
        if (item.get('qb_category') or '').lower() == _app_category():
            return out('manual', 'qbit-category')
        return out('other', 'no-arr-grab')

    src = g['source']
    if src in ('Rss', 'Search', 'UserInvokedSearch') and _is_upgrade(arr, g, did):
        return out('upgrade', f'{g["kind"]}:{src}+prior-file')
    if src == 'UserInvokedSearch':
        return out('requested', f'{g["kind"]}:UserInvokedSearch')
    if src == 'Search':
        lib_id = g.get('movie_id') if g['kind'] == 'radarr' else g.get('series_id')
        added = _added_ts(arr, g['kind'], lib_id)
        if added and 0 <= grab_ts - added <= 30 * 60:
            return out('requested', f'{g["kind"]}:Search right after add')
        return out('auto', f'{g["kind"]}:Search')
    if src == 'Rss':
        return out('auto', f'{g["kind"]}:Rss')
    return out('other', f'{g["kind"]}:{src or "unknown"}')


def resolve(items: List[Dict[str, Any]], settings) -> List[Dict[str, str]]:
    try:
        ctx = _context(settings)
    except Exception as e:  # noqa: BLE001
        logger.warning('grab_origin: context failed: %s', e)
        return [{'origin': 'other', 'origin_label': LABELS['other'], 'origin_evidence': 'error'}
                for _ in items]
    res = []
    for it in items:
        try:
            res.append(resolve_one(it, ctx))
        except Exception as e:  # noqa: BLE001
            logger.debug('grab_origin: resolve failed for %s: %s', it.get('torrent_hash'), e)
            res.append({'origin': 'other', 'origin_label': LABELS['other'], 'origin_evidence': 'error'})
    return res
