"""OMDb: IMDb rating and votes by IMDb id (one reusable helper).

Mirrors prod genre_roulette (1b4ea74): lookups by id, never by title; a dict of
nulls means "asked, and IMDb has nothing" (cached as a verdict); None means
"could not ask" (no key, network/HTTP error), which callers treat as no verdict
(fail open). Failures are never cached. Verdicts are reused for 90 days, at most
5,000 kept. The key is never logged.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

OMDB_URL = 'https://www.omdbapi.com/'
TEST_IMDB_ID = 'tt0162222'          # Cast Away: the Settings Test lookup
CACHE_DAYS = 90
CACHE_MAX = 5000
_IMDB_ID = re.compile(r'^tt\d{5,10}$')
_NO_TITLE = frozenset({'Movie not found!', 'Incorrect IMDb ID.'})
_cache: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def api_key() -> str:
    """The saved OMDb key ('' when none). Minimal builds count only a key saved in Settings."""
    try:
        from app.settings_manager import SettingsFile      # v259: read as on disk; never rewrites settings.json
        key = str(SettingsFile().get('apis.omdb.api_key') or '').strip()
    except Exception:                  # noqa: BLE001 - no settings: no key
        key = ''
    if key:
        return key
    from app import minimal
    if minimal.enabled():
        return ''
    try:
        from app.config import Config
        return str(getattr(Config, 'OMDB_API_KEY', '') or '').strip()
    except Exception:                  # noqa: BLE001
        return ''


def lookup(imdb_id: str, key: str, timeout: float = 15) -> Optional[Dict[str, Any]]:
    """One OMDb call: {'rating', 'votes', 'at'} (nulls when IMDb has nothing), None on failure."""
    today = datetime.now().strftime('%Y-%m-%d')
    count_call()
    try:
        o = requests.get(OMDB_URL, params={'apikey': key, 'i': imdb_id}, timeout=timeout)
        o.raise_for_status()
        body = o.json() or {}
    except Exception as e:             # noqa: BLE001 - fail open
        logger.warning('OMDb lookup for %s failed: %s', imdb_id, type(e).__name__)
        return None
    if body.get('Response') != 'True':
        # Only "IMDb has nothing" is a verdict; a limit, bad key or anything else is "could not ask".
        if str(body.get('Error') or '').strip() in _NO_TITLE:
            return {'rating': None, 'votes': None, 'at': today}
        logger.warning('OMDb lookup for %s refused: %s', imdb_id, str(body.get('Error') or 'unknown')[:80])
        return None
    votes = str(body.get('imdbVotes') or '').replace(',', '')
    rating = str(body.get('imdbRating') or '').strip()
    try:
        r = float(rating) if rating not in ('', 'N/A') else None
    except ValueError:
        r = None
    return {'rating': r, 'votes': int(votes) if votes.isdigit() else None, 'at': today}


def _fresh(hit: Any) -> bool:
    if not isinstance(hit, dict) or 'at' not in hit:
        return False
    cutoff = (datetime.now() - timedelta(days=CACHE_DAYS)).strftime('%Y-%m-%d')
    return str(hit.get('at') or '') >= cutoff


def _prune() -> None:
    fresh = {k: v for k, v in _cache.items() if _fresh(v)}
    if len(fresh) > CACHE_MAX:
        fresh = dict(sorted(fresh.items(), key=lambda kv: str(kv[1].get('at') or ''), reverse=True)[:CACHE_MAX])
    _cache.clear()
    _cache.update(fresh)


def get_ratings(imdb_id: str) -> Optional[Dict[str, Any]]:
    """IMDb rating and votes for *imdb_id*: {'rating': float|None, 'votes': int|None}.
    None when it could not ask (no key, bad id, OMDb error): fail open."""
    imdb_id = str(imdb_id or '').strip()
    if not _IMDB_ID.match(imdb_id):
        return None
    with _lock:
        hit = _cache.get(imdb_id)
    if _fresh(hit):
        return {'rating': hit['rating'], 'votes': hit['votes']}
    key = api_key()
    if not key:
        return None
    hit = lookup(imdb_id, key)
    if hit is None:
        return None
    with _lock:
        _cache[imdb_id] = hit
        if len(_cache) > CACHE_MAX:
            _prune()
    return {'rating': hit['rating'], 'votes': hit['votes']}


def test_key(key: str) -> Optional[str]:
    """None when a real lookup of tt0162222 succeeds with *key*, else a short error."""
    try:
        with requests.Session() as sess:
            sess.trust_env = False
            r = sess.get(OMDB_URL, params={'apikey': key, 'i': TEST_IMDB_ID}, timeout=8, allow_redirects=False)
    except requests.exceptions.Timeout:
        return 'Timed out'
    except Exception:                  # noqa: BLE001
        return "Can't reach OMDb"
    try:
        body = r.json() or {}
    except ValueError:
        body = {}
    err = str(body.get('Error') or '').lower() if isinstance(body, dict) else ''
    if r.status_code == 429 or 'limit' in err:
        return 'Rate limited'
    if r.status_code == 401 or 'key' in err:
        return 'Invalid key'
    if r.status_code != 200 or body.get('Response') != 'True' or body.get('imdbID') != TEST_IMDB_ID:
        return 'Unexpected response'
    return None


def forget() -> None:
    with _lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Quota protection for title lookups (/api/ratings and inline detail ratings):
# - a negative cache: a real OMDb "not found" for NEG_TTL_S; an error (network, 5xx, 429, bad key, request limit)
#   for ERR_TTL_S and only for background warm-ups, so a user's open always asks again;
# - a daily call budget (UTC date, persisted in the data dir) that warm-ups may only use up to WARM_BUDGET_SHARE of;
# - a per-load / per-query warm cap, per source (OMDb and the RT scrape count separately).
# ---------------------------------------------------------------------------

NEG_TTL_S = 6 * 3600
ERR_TTL_S = 90
NEG_MAX = 5000
DEFAULT_DAILY_QUOTA = 1000
WARM_BUDGET_SHARE = 0.7
WARMS_PER_LOAD = 12
WARMS_PER_QUERY = 4                 # a search query's warm id is '<load>/<query>'
WARM_LOADS_MAX = 500
WARM_HEADER = 'X-Luma-Warm'

_neg: Dict[str, tuple] = {}          # key -> (at, ttl, is_error)
_day = {'date': '', 'calls': 0}
_warm_loads: Dict[str, int] = {}
_guard_lock = threading.Lock()
_BUDGET_PATH_OVERRIDE: Optional[str] = None


def _now() -> float:
    import time
    return time.time()


def is_not_found(body: Any) -> bool:
    """OMDb's own verdict that the title doesn't exist (Response False, Error '... not found!')."""
    return isinstance(body, dict) and body.get('Response') == 'False' and 'not found' in str(body.get('Error') or '').lower()


def negative_hit(key: str, warm: bool = True) -> bool:
    """True when this lookup should not be asked again yet: a not-found within NEG_TTL_S, or (warm-ups only) an
    error within ERR_TTL_S."""
    with _guard_lock:
        hit = _neg.get(key)
        if hit is None:
            return False
        at, ttl, err = hit
        if _now() - at >= ttl:
            del _neg[key]
            return False
        return warm or not err


def remember_miss(key: str, error: bool = False) -> None:
    """A not-found (NEG_TTL_S) or, with `error`, a failed ask (ERR_TTL_S; never blocks a user's open)."""
    with _guard_lock:
        if len(_neg) >= NEG_MAX:
            del _neg[min(_neg, key=lambda k: _neg[k][0])]
        _neg[key] = (_now(), ERR_TTL_S if error else NEG_TTL_S, error)


def forget_miss(key: str) -> None:
    with _guard_lock:
        _neg.pop(key, None)


def daily_quota() -> int:
    """The OMDb plan's daily request limit: Settings apis.omdb.daily_quota, else OMDB_DAILY_QUOTA, else 1000."""
    val: Any = None
    try:
        from app.settings_manager import SettingsFile
        val = SettingsFile().get('apis.omdb.daily_quota')
    except Exception:                  # noqa: BLE001
        val = None
    if not val:
        try:
            from app.config import Config
            val = getattr(Config, 'OMDB_DAILY_QUOTA', None)
        except Exception:              # noqa: BLE001
            val = None
    try:
        n = int(val)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else DEFAULT_DAILY_QUOTA


def _utc_today() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _budget_path() -> Optional[str]:
    """The budget DB (data dir / omdb_budget.db); None under pytest unless a test sets the override."""
    if _BUDGET_PATH_OVERRIDE:
        return _BUDGET_PATH_OVERRIDE
    import os
    if 'PYTEST_CURRENT_TEST' in os.environ:
        return None
    try:
        from app.canon_index import _data_dir
        d = _data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return str(d / 'omdb_budget.db')
    except Exception:                  # noqa: BLE001
        return None


BUDGET_DB_TIMEOUT_S = 0.1


def _budget_db(fn):
    path = _budget_path()
    if not path:
        return None
    import sqlite3
    try:
        conn = sqlite3.connect(path, timeout=BUDGET_DB_TIMEOUT_S)
        try:
            conn.execute('CREATE TABLE IF NOT EXISTS omdb_budget (date TEXT PRIMARY KEY, calls INTEGER NOT NULL)')
            out = fn(conn)
            conn.commit()
            return out
        finally:
            conn.close()
    except Exception as e:             # noqa: BLE001 - the budget falls back to memory
        logger.warning('OMDb budget store unavailable: %s', type(e).__name__)
        return None


def _today_calls() -> int:
    today = _utc_today()
    if _day['date'] != today:
        row = _budget_db(lambda c: c.execute('SELECT calls FROM omdb_budget WHERE date = ?', (today,)).fetchone())
        _day['date'], _day['calls'] = today, int(row[0]) if row else 0
    return _day['calls']


def count_call() -> None:
    with _guard_lock:
        _today_calls()
        _day['calls'] += 1
        today, n = _day['date'], _day['calls']

    # The write happens outside the lock (BUDGET_DB_TIMEOUT_S at most), so a busy DB never stalls other lookups.
    def save(c):
        c.execute('INSERT INTO omdb_budget (date, calls) VALUES (?, ?) ON CONFLICT(date) DO UPDATE SET calls = '
                  'MAX(calls + 1, excluded.calls)', (today, n))
        c.execute('DELETE FROM omdb_budget WHERE date < ?', ((datetime.strptime(today, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d'),))
    _budget_db(save)


def warm_allowed(load_id: str, source: str = 'omdb') -> bool:
    """A background warm-up may call `source`: under WARM_BUDGET_SHARE of today's OMDb quota and under
    WARMS_PER_LOAD (WARMS_PER_QUERY for a search query) for its load, per source. Counts the warm when allowed.
    User opens never go through this."""
    load, _, query = str(load_id).partition('/')
    pre = '' if source == 'omdb' else f'{source}:'
    load_k, query_k = pre + load, pre + str(load_id)
    with _guard_lock:
        if _today_calls() >= daily_quota() * WARM_BUDGET_SHARE:
            return False
        if _warm_loads.get(load_k, 0) >= WARMS_PER_LOAD:
            return False
        if query and _warm_loads.get(query_k, 0) >= WARMS_PER_QUERY:
            return False
        for k in ([load_k, query_k] if query else [load_k]):
            if k not in _warm_loads and len(_warm_loads) >= WARM_LOADS_MAX:
                del _warm_loads[next(iter(_warm_loads))]
            _warm_loads[k] = _warm_loads.get(k, 0) + 1
        return True


def _reset_guard() -> None:
    with _guard_lock:
        _neg.clear()
        _warm_loads.clear()
        _day['date'], _day['calls'] = '', 0
