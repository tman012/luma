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

OMDB_URL = 'http://www.omdbapi.com/'
TEST_IMDB_ID = 'tt0162222'          # Cast Away: the Settings Test lookup
CACHE_DAYS = 90
CACHE_MAX = 5000
_IMDB_ID = re.compile(r'^tt\d{5,10}$')
_cache: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def api_key() -> str:
    """The saved OMDb key ('' when none). Minimal builds count only a key saved in Settings."""
    try:
        from app.settings_manager import SettingsManager
        key = str(SettingsManager().get('apis.omdb.api_key') or '').strip()
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
    try:
        o = requests.get(OMDB_URL, params={'apikey': key, 'i': imdb_id}, timeout=timeout)
        o.raise_for_status()
        body = o.json() or {}
    except Exception as e:             # noqa: BLE001 - fail open
        logger.warning('OMDb lookup for %s failed: %s', imdb_id, type(e).__name__)
        return None
    if body.get('Response') != 'True':
        return {'rating': None, 'votes': None, 'at': today}
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
    if r.status_code == 401:
        return 'Invalid key'
    if r.status_code == 429:
        return 'Rate limited'
    if r.status_code != 200:
        return 'Unexpected response'
    try:
        body = r.json() or {}
    except ValueError:
        return 'Unexpected response'
    if body.get('Response') != 'True' or body.get('imdbID') != TEST_IMDB_ID:
        err = str(body.get('Error') or '')
        return 'Invalid key' if 'key' in err.lower() else 'Unexpected response'
    return None


def forget() -> None:
    with _lock:
        _cache.clear()
