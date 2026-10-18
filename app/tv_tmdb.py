"""TMDB calls for TV import (luma-test builds): show details, seasons, search
and /find by an outside id. The base URL is LUMA_TMDB_BASE_URL or TMDB
(app/connectors.tmdb_base_url), so tests and the Connectors Test use the same
server. The key comes from Settings > Connectors (apis.tmdb.api_key) and is
never logged.

Every call raises TmdbError on a transport error, a 5xx, a rate limit or an
unreadable answer, and returns None for a 404 (no such id).
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from app.connectors import tmdb_base_url

logger = logging.getLogger(__name__)

_cfg: Dict[str, Any] = {}
TIMEOUT_S = 10
RATE_PER_S = 35.0           # one budget shared by every worker (TMDB allows about 50)
BURST = 4                   # requests that may go at once after a quiet spell
MAX_RETRY_AFTER_S = 10
ATTEMPTS = 3                # a 429 waits Retry-After (the whole pool waits) and tries again
CACHE_TTL_S = 24 * 3600
CACHE_MAX_BYTES = 16 * 1024 * 1024    # the cache's answers in tv.db, at most (oldest go first)
# what TV never reads: cast and crew are most of a season's size
_SLIM_SHOW = ('credits', 'aggregate_credits', 'images', 'videos', 'production_companies', 'production_countries',
              'spoken_languages', 'created_by', 'keywords', 'recommendations', 'similar', 'content_ratings')
_SLIM_EP = ('crew', 'guest_stars', 'production_code', 'vote_average', 'vote_count')
_puts = [0]
SEASONS_PER_CALL = 20       # TMDB's append_to_response limit
_pace_lock = threading.Lock()
_bucket = {'tokens': float(BURST), 'ts': 0.0, 'pause_until': 0.0}
# Background work (the Calendar-open refresh / stills backfill) also takes a
# token from this slower bucket, so it stays well under the budget, burst
# included: at most BACKGROUND_RATE_PER_S + 1 requests in any second.
BACKGROUND_RATE_PER_S = 25.0
_bg_lock = threading.Lock()
_bg_bucket = {'tokens': 1.0, 'ts': 0.0}
_local = threading.local()          # .fresh: skip cache reads in this thread (see fresh())


class TmdbError(Exception):
    """TMDB couldn't be asked (no key, unreachable, rate limited, bad answer)."""


NO_KEY = 'Add a TMDB key in Connectors'
UNREACHABLE = "Couldn't reach TMDB"
BUSY = 'TMDB is busy. Try again later.'


def init(*, get_key: Callable[[], str], session_factory: Callable = requests.Session, cache: bool = False) -> None:
    """*cache*: keep answers in tv.db for 24 h (the app; tests opt in)."""
    _cfg.update(get_key=get_key, session_factory=session_factory, cache=cache)


def has_key() -> bool:
    return bool(_key())


def _key() -> str:
    try:
        return str((_cfg.get('get_key') or (lambda: ''))() or '').strip()
    except Exception:
        return ''


def _wait_background() -> None:
    while True:
        with _bg_lock:
            if BACKGROUND_RATE_PER_S <= 0 or RATE_PER_S <= 0:     # pacing off (tests)
                return
            now = time.monotonic()
            _bg_bucket['tokens'] = min(1.0, _bg_bucket['tokens'] + (now - _bg_bucket['ts']) * BACKGROUND_RATE_PER_S)
            _bg_bucket['ts'] = now
            if _bg_bucket['tokens'] >= 1:
                _bg_bucket['tokens'] -= 1
                return
            wait = (1 - _bg_bucket['tokens']) / BACKGROUND_RATE_PER_S
        time.sleep(max(wait, 0.001))


@contextlib.contextmanager
def background():
    """Within this block (this thread), requests are paced at
    BACKGROUND_RATE_PER_S as well as the shared budget."""
    prev = getattr(_local, 'background', False)
    _local.background = True
    try:
        yield
    finally:
        _local.background = prev


def wait_turn() -> None:
    """Block until the shared token bucket (RATE_PER_S) has a request for us
    and no 429 pause is in force. Every worker thread goes through here;
    background work also waits for the slower background bucket."""
    if getattr(_local, 'background', False):
        _wait_background()
    while True:
        with _pace_lock:
            now = time.monotonic()
            if RATE_PER_S <= 0:
                return
            if now < _bucket['pause_until']:
                wait = _bucket['pause_until'] - now
            else:
                _bucket['tokens'] = min(float(BURST), _bucket['tokens'] + (now - _bucket['ts']) * RATE_PER_S)
                _bucket['ts'] = now
                if _bucket['tokens'] >= 1:
                    _bucket['tokens'] -= 1
                    return
                wait = (1 - _bucket['tokens']) / RATE_PER_S
        time.sleep(max(wait, 0.001))


def note_retry_after(seconds: float) -> None:
    """A 429: the whole pool waits *seconds* (capped) before its next request."""
    with _pace_lock:
        until = time.monotonic() + min(max(seconds, 0.0), MAX_RETRY_AFTER_S)
        _bucket['pause_until'] = max(_bucket['pause_until'], until)
        _bucket['tokens'] = 0.0


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    key = _key()
    if not key:
        raise TmdbError(NO_KEY)
    q = dict(params or {})
    q['api_key'] = key
    sess = (_cfg.get('session_factory') or requests.Session)()
    sess.trust_env = False          # no proxy or .netrc from the environment: the key goes to TMDB only
    try:
        for attempt in range(1, ATTEMPTS + 1):
            wait_turn()
            try:
                r = sess.get(f'{tmdb_base_url()}{path}', params=q, timeout=TIMEOUT_S, allow_redirects=False)
            except requests.RequestException as e:
                logger.warning('TMDB %s failed: %s', path.split('?')[0], type(e).__name__)
                raise TmdbError(UNREACHABLE) from None
            if r.status_code == 429:
                if attempt == ATTEMPTS:
                    raise TmdbError(BUSY)       # still rate limited after waiting
                try:
                    wait = float(r.headers.get('Retry-After') or 2)
                except ValueError:
                    wait = 2
                note_retry_after(wait)
                time.sleep(min(max(wait, 0), MAX_RETRY_AFTER_S))
                continue
            if r.status_code == 404:
                return None
            if r.status_code in (401, 403):
                raise TmdbError('TMDB refused the key')
            if r.status_code != 200:
                raise TmdbError(UNREACHABLE)
            try:
                data = r.json()
            except ValueError:
                raise TmdbError(UNREACHABLE) from None
            if not isinstance(data, dict):
                raise TmdbError(UNREACHABLE)
            return data
        raise TmdbError(UNREACHABLE)
    finally:
        try:
            sess.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- 24 h cache (tv.db)

def _cache_get(key: str) -> Any:
    if not _cfg.get('cache') or getattr(_local, 'fresh', False):
        return None
    from app import tv_store
    try:
        with tv_store.connect() as conn:
            row = conn.execute('SELECT ts, body FROM tmdb_cache WHERE key = ?', (key,)).fetchone()
    except Exception:
        return None
    if not row or time.time() - row['ts'] > CACHE_TTL_S:
        return None
    try:
        return json.loads(row['body'])
    except ValueError:
        return None


def slim(value: Any) -> Any:
    """Only what TV reads: no cast, crew, images or videos (a season drops ~90% of its size)."""
    if not isinstance(value, dict):
        return value
    out = {k: v for k, v in value.items() if k not in _SLIM_SHOW}
    for k in ('last_episode_to_air', 'next_episode_to_air'):
        if isinstance(out.get(k), dict):
            out[k] = {a: b for a, b in out[k].items() if a not in _SLIM_EP}
    if isinstance(out.get('episodes'), list):
        out['episodes'] = [{a: b for a, b in e.items() if a not in _SLIM_EP} if isinstance(e, dict) else e
                           for e in out['episodes']]
    return out


def _cache_put(key: str, value: Any) -> None:
    if not _cfg.get('cache') or value is None:
        return
    value = slim(value)
    from app import tv_store
    try:
        with tv_store.write() as conn:
            conn.execute('INSERT OR REPLACE INTO tmdb_cache (key, ts, body) VALUES (?, ?, ?)',
                         (key, time.time(), json.dumps(value, separators=(',', ':'))))
    except Exception as e:          # a cache that can't write is just slower
        logger.debug('TMDB cache not written: %s', type(e).__name__)
    _puts[0] += 1
    if _puts[0] % 200 == 0:
        prune_cache()


@contextlib.contextmanager
def fresh():
    """Within this block (this thread), ask TMDB even when the cache has an
    answer; the answer still refreshes the cache. For the daily episode refresh."""
    _local.fresh = True
    try:
        yield
    finally:
        _local.fresh = False


def prune_cache() -> int:
    """Drop answers older than CACHE_TTL_S, then the oldest beyond CACHE_MAX_BYTES. Returns rows dropped."""
    if not _cfg.get('cache'):
        return 0
    from app import tv_store
    try:
        with tv_store.write() as conn:
            n = conn.execute('DELETE FROM tmdb_cache WHERE ts < ?', (time.time() - CACHE_TTL_S,)).rowcount
            n += conn.execute(
                'DELETE FROM tmdb_cache WHERE key IN (SELECT key FROM (SELECT key, SUM(length(body))'
                ' OVER (ORDER BY ts DESC, key) AS run FROM tmdb_cache) WHERE run > ?)', (CACHE_MAX_BYTES,)).rowcount
        if n:
            logger.info('TMDB cache: %d old answer(s) dropped', n)
        return n
    except Exception as e:
        logger.debug('TMDB cache not pruned: %s', type(e).__name__)
        return 0


def _cached(key: str, fetch: Callable[[], Any]) -> Any:
    hit = _cache_get(key)
    if hit is not None:
        return hit
    value = fetch()
    _cache_put(key, value)
    return value


def clear_cache() -> None:
    from app import tv_store
    with tv_store.write() as conn:
        conn.execute('DELETE FROM tmdb_cache')


def _int(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _year(date_str: Any) -> Optional[int]:
    if isinstance(date_str, str) and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None


def brief(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A search or /find result as {tmdb_id, title, original_title, year,
    poster_path, overview, popularity, vote_count, origin_country}."""
    tid = _int(item.get('id'))
    if not tid or tid <= 0:
        return None
    pop = item.get('popularity')
    return {'tmdb_id': tid, 'title': str(item.get('name') or item.get('original_name') or ''),
            'original_title': str(item.get('original_name') or ''),
            'year': _year(item.get('first_air_date')),
            'poster_path': item.get('poster_path') if isinstance(item.get('poster_path'), str) else None,
            'overview': str(item.get('overview') or '')[:500],
            'popularity': float(pop) if isinstance(pop, (int, float)) and not isinstance(pop, bool) else 0.0,
            'vote_count': _int(item.get('vote_count')) or 0,
            'origin_country': [c for c in item.get('origin_country') or [] if isinstance(c, str)][:5]}


def search(title: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {'query': title[:200], 'include_adult': 'false'}
    if year:
        params['first_air_date_year'] = year

    def fetch():
        data = _get('/search/tv', params) or {}
        out = []
        for item in data.get('results') or []:
            if isinstance(item, dict):
                b = brief(item)
                if b:
                    out.append(b)
        return out
    return _cached(f'search|{title[:200].casefold()}|{year or ""}', fetch)


def find(external_id: str, source: str) -> List[Dict[str, Any]]:
    """TV results for an outside id (source 'tvdb_id' or 'imdb_id')."""
    if source not in ('tvdb_id', 'imdb_id'):
        raise ValueError(source)
    data = _get(f'/find/{requests.utils.quote(str(external_id), safe="")}', {'external_source': source}) or {}
    return [b for b in (brief(i) for i in data.get('tv_results') or [] if isinstance(i, dict)) if b]


def show(tmdb_id: int, seasons: Optional[List[int]] = None) -> Optional[Dict[str, Any]]:
    """Show details with external ids and alternative titles, or None. With
    *seasons*, up to 18 of them ride along ('season/N' keys) in the same call."""
    tid = int(tmdb_id)
    extra = [f'season/{int(n)}' for n in (seasons or [])][:SEASONS_PER_CALL - 2]
    cached = _cache_get(f'show|{tid}')
    if cached is not None and not extra:
        return cached
    data = _get(f'/tv/{tid}', {'append_to_response': ','.join(['external_ids', 'alternative_titles'] + extra)})
    if data is None or _int(data.get('id')) != tid:
        return None
    base = {k: v for k, v in data.items() if not k.startswith('season/')}
    _cache_put(f'show|{tid}', base)
    for k, v in data.items():
        if k.startswith('season/') and isinstance(v, dict):
            _cache_put(f'season|{tid}|{k[7:]}', v)
    return data


def season(tmdb_id: int, number: int) -> Optional[Dict[str, Any]]:
    return _cached(f'season|{int(tmdb_id)}|{int(number)}',
                   lambda: _get(f'/tv/{int(tmdb_id)}/season/{int(number)}'))


def seasons(tmdb_id: int, numbers: List[int]) -> Dict[int, Dict[str, Any]]:
    """{n: season} for *numbers*, SEASONS_PER_CALL at a time through
    append_to_response (cached ones aren't asked again)."""
    tid = int(tmdb_id)
    out: Dict[int, Dict[str, Any]] = {}
    want = []
    for n in numbers:
        hit = _cache_get(f'season|{tid}|{int(n)}')
        if hit is not None:
            out[int(n)] = hit
        else:
            want.append(int(n))
    for i in range(0, len(want), SEASONS_PER_CALL):
        chunk = want[i:i + SEASONS_PER_CALL]
        data = _get(f'/tv/{tid}', {'append_to_response': ','.join(f'season/{n}' for n in chunk)})
        if data is None:
            break
        for n in chunk:
            v = data.get(f'season/{n}')
            if isinstance(v, dict):
                out[n] = v
                _cache_put(f'season|{tid}|{n}', v)
    return out


def episode_groups(tmdb_id: int) -> List[Dict[str, Any]]:
    """[{id, name, type, episode_count, group_count}] for a show."""
    def fetch():
        data = _get(f'/tv/{int(tmdb_id)}/episode_groups') or {}
        return [g for g in data.get('results') or [] if isinstance(g, dict) and isinstance(g.get('id'), str)]
    return _cached(f'groups|{int(tmdb_id)}', fetch)


def episode_group(group_id: str) -> Optional[Dict[str, Any]]:
    gid = requests.utils.quote(str(group_id), safe='')
    return _cached(f'group|{gid}', lambda: _get(f'/tv/episode_group/{gid}'))
