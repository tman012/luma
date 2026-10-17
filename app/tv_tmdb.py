"""TMDB calls for TV import (luma-test builds): show details, seasons, search
and /find by an outside id. The base URL is LUMA_TMDB_BASE_URL or TMDB
(app/connectors.tmdb_base_url), so tests and the Connectors Test use the same
server. The key comes from Settings > Connectors (apis.tmdb.api_key) and is
never logged.

Every call raises TmdbError on a transport error, a 5xx, a rate limit or an
unreadable answer, and returns None for a 404 (no such id).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from app.connectors import tmdb_base_url

logger = logging.getLogger(__name__)

_cfg: Dict[str, Any] = {}
TIMEOUT_S = 10
MIN_GAP_S = 0.05            # at most ~20 requests a second (TMDB allows about 50)
MAX_RETRY_AFTER_S = 10
_pace_lock = threading.Lock()
_last = [0.0]


class TmdbError(Exception):
    """TMDB couldn't be asked (no key, unreachable, rate limited, bad answer)."""


NO_KEY = 'Add a TMDB key in Connectors'
UNREACHABLE = "Couldn't reach TMDB"
BUSY = 'TMDB is busy. Try again later.'


def init(*, get_key: Callable[[], str], session_factory: Callable = requests.Session) -> None:
    _cfg.update(get_key=get_key, session_factory=session_factory)


def has_key() -> bool:
    return bool(_key())


def _key() -> str:
    try:
        return str((_cfg.get('get_key') or (lambda: ''))() or '').strip()
    except Exception:
        return ''


def _pace() -> None:
    with _pace_lock:
        wait = _last[0] + MIN_GAP_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    key = _key()
    if not key:
        raise TmdbError(NO_KEY)
    q = dict(params or {})
    q['api_key'] = key
    sess = (_cfg.get('session_factory') or requests.Session)()
    sess.trust_env = False          # no proxy or .netrc from the environment: the key goes to TMDB only
    try:
        for attempt in (1, 2):
            _pace()
            try:
                r = sess.get(f'{tmdb_base_url()}{path}', params=q, timeout=TIMEOUT_S, allow_redirects=False)
            except requests.RequestException as e:
                logger.warning('TMDB %s failed: %s', path.split('?')[0], type(e).__name__)
                raise TmdbError(UNREACHABLE) from None
            if r.status_code == 429 and attempt == 1:
                try:
                    wait = min(float(r.headers.get('Retry-After') or 2), MAX_RETRY_AFTER_S)
                except ValueError:
                    wait = 2
                time.sleep(max(wait, 0))
                continue
            if r.status_code == 429:
                raise TmdbError(BUSY)       # still rate limited after one wait
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


def _int(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _year(date_str: Any) -> Optional[int]:
    if isinstance(date_str, str) and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None


def brief(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A search or /find result as {tmdb_id, title, original_title, year, poster_path, overview}."""
    tid = _int(item.get('id'))
    if not tid or tid <= 0:
        return None
    return {'tmdb_id': tid, 'title': str(item.get('name') or item.get('original_name') or ''),
            'original_title': str(item.get('original_name') or ''),
            'year': _year(item.get('first_air_date')),
            'poster_path': item.get('poster_path') if isinstance(item.get('poster_path'), str) else None,
            'overview': str(item.get('overview') or '')[:500]}


def search(title: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {'query': title[:200], 'include_adult': 'false'}
    if year:
        params['first_air_date_year'] = year
    data = _get('/search/tv', params) or {}
    out = []
    for item in data.get('results') or []:
        if isinstance(item, dict):
            b = brief(item)
            if b:
                out.append(b)
    return out


def find(external_id: str, source: str) -> List[Dict[str, Any]]:
    """TV results for an outside id (source 'tvdb_id' or 'imdb_id')."""
    if source not in ('tvdb_id', 'imdb_id'):
        raise ValueError(source)
    data = _get(f'/find/{requests.utils.quote(str(external_id), safe="")}', {'external_source': source}) or {}
    return [b for b in (brief(i) for i in data.get('tv_results') or [] if isinstance(i, dict)) if b]


def show(tmdb_id: int) -> Optional[Dict[str, Any]]:
    """Show details with external ids and alternative titles, or None."""
    data = _get(f'/tv/{int(tmdb_id)}', {'append_to_response': 'external_ids,alternative_titles'})
    if data is None or _int(data.get('id')) != int(tmdb_id):
        return None
    return data


def season(tmdb_id: int, number: int) -> Optional[Dict[str, Any]]:
    return _get(f'/tv/{int(tmdb_id)}/season/{int(number)}')
