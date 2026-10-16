#!/usr/bin/env python3
"""
Generic Indexer API Integration
Supports Prowlarr and Torznab endpoints for populating the Explore cache.
"""

import re
import threading
import time
import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Any

import requests

logger = logging.getLogger(__name__)

# Newznab standard movie subcategories.  Prowlarr browse mode does NOT expand
# the parent 2000 category, so we must list every sub-category explicitly.
MOVIE_CATEGORIES_EXPANDED = [
    2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080,
]

# Newznab TV + Sports subcategories.  Appended to the movie-expanded set when
# a raw (broad) search targets TV content — never used by the movie-only
# Explore sweep path. Includes TV/Sport (5060) so wrestling PPVs etc. surface
# (IPT files AEW under TV Sports). 5010/5020/5050 fill standard TV gaps;
# 5070/5080 kept for older/nonstandard indexer mappings.
TV_SPORTS_CATEGORIES = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070, 5080]


# Tracker guard. Private trackers (the AvistaZ network first among them)
# enforce short burst limits and rolling per-account request caps; past them
# the tracker answers 429 and Prowlarr parks the indexer for an hour, so every
# later search comes back 400. Luma's background sweeps (Explore, Latest,
# REMUX sweeps, browse) easily exceed those caps if left unmetered.
#
# So, for EVERY indexer:
#   * identical queries are served from cache,
#   * user (interactive) searches are capped per hour,
#   * background searches go through a token bucket (spread out, small burst),
#   * all searches are capped per rolling 24 h and across all indexers per hour,
#   * requests to one indexer are serialised and spaced,
#   * Prowlarr's own backoff (/api/v1/indexerstatus disabledTill) is honored,
# and the counters persist in the data directory, so a restart can't reset them.
#
# All limits live in this one table.
GUARD_LIMITS = {
    'interactive_per_hour': 40,     # user searches per indexer per hour (no bucket)
    'background_burst': 5,          # background token bucket size per indexer
    'background_refill_s': 180.0,   # one background token per 180 s = 20/h
    'per_day': 300,                 # all searches per indexer per rolling 24 h
    'global_per_hour': 120,         # all searches, all indexers, per hour
    'query_ttl_background': 3 * 3600,   # identical background query cached 3 h
    'query_ttl_interactive': 900,       # identical user query cached 15 min
    'min_interval_s': 2.0,          # spacing between requests to one indexer
    'unscoped_cooldown_s': 3600,    # unscoped search after a 400/429, if Prowlarr gives no time
}
_INDEXER_STATUS_TTL = 60             # seconds to cache /api/v1/indexerstatus
UNSCOPED = -1                        # pseudo-indexer for a search across all indexers

# Persisted state (see _guard_save): send log per indexer as [ts, kind]
# (kind 'i' interactive, 'b' background), background buckets, unscoped cooldown.
_guard_log: Dict[int, List[list]] = {}
_guard_buckets: Dict[int, List[float]] = {}
_guard_cooldown: Dict[int, float] = {}
_guard_loaded = [False]

GUARD_FILENAME = 'indexer_guard.json'


def _guard_path() -> str:
    from app.first_run import data_dir
    return str(data_dir() / GUARD_FILENAME)


def _guard_reset_memory() -> None:
    _guard_log.clear()
    _guard_buckets.clear()
    _guard_cooldown.clear()
    _guard_loaded[0] = False


def _guard_load() -> None:
    """Load persisted guard state once per process (call under _budget_state_guard)."""
    if _guard_loaded[0]:
        return
    _guard_loaded[0] = True
    import json
    try:
        with open(_guard_path()) as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        logger.warning('Tracker guard state unreadable (%s); starting with full limits in force', e)
        raw = {}
    cutoff = time.time() - 86400
    try:
        for k, entries in (raw.get('log') or {}).items():
            kept = []
            for e in entries:
                ts, kind = (e[0], e[1]) if isinstance(e, list) else (e, 'b')
                if float(ts) > cutoff:
                    kept.append([float(ts), 'i' if kind == 'i' else 'b'])
            _guard_log[int(k)] = kept
        for k, v in (raw.get('buckets') or {}).items():
            _guard_buckets[int(k)] = [float(v[0]), float(v[1])]
        for k, v in (raw.get('cooldown') or {}).items():
            _guard_cooldown[int(k)] = float(v)
    except (TypeError, ValueError, IndexError) as e:
        logger.warning('Tracker guard state malformed (%s); ignoring part of it', e)


def _guard_save() -> None:
    """Write all guard state atomically: temp file in the same dir, fsync, os.replace."""
    import json
    import os
    import tempfile
    path = _guard_path()
    state = {
        'log': {str(k): v for k, v in _guard_log.items()},
        'buckets': {str(k): v for k, v in _guard_buckets.items()},
        'cooldown': {str(k): v for k, v in _guard_cooldown.items()},
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.indexer_guard.', dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, 'w') as fh:
                json.dump(state, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.warning('Tracker guard state not saved: %s', e)


def _guard_counts(ix: int, now: float) -> tuple:
    """(interactive this indexer last hour, this indexer last 24 h, all indexers
    last hour). Call under _budget_state_guard."""
    cutoff_day, cutoff_hr = now - 86400, now - 3600
    for k in list(_guard_log):
        _guard_log[k] = [e for e in _guard_log[k] if e[0] > cutoff_day]
    mine = _guard_log.get(ix, [])
    return (sum(1 for t, kind in mine if t > cutoff_hr and kind == 'i'), len(mine),
            sum(1 for v in _guard_log.values() for t, _ in v if t > cutoff_hr))


def _guard_tokens(ix: int, now: float) -> List[float]:
    burst = float(GUARD_LIMITS['background_burst'])
    b = _guard_buckets.get(ix)
    if b is None:
        b = _guard_buckets[ix] = [burst, now]
    b[0] = min(burst, b[0] + max(0.0, now - b[1]) / GUARD_LIMITS['background_refill_s'])
    b[1] = now
    return b


def _guard_decide(ix: int, interactive: bool, now: float) -> Optional[str]:
    """None when a search to *ix* may be sent now (and records it), else the
    reason it may not. Call under _budget_state_guard."""
    _guard_load()
    hr, day, glob = _guard_counts(ix, now)
    if day >= GUARD_LIMITS['per_day']:
        return f"daily cap {GUARD_LIMITS['per_day']}"
    if glob >= GUARD_LIMITS['global_per_hour']:
        return f"global hourly cap {GUARD_LIMITS['global_per_hour']}"
    if interactive:
        if hr >= GUARD_LIMITS['interactive_per_hour']:
            return f"interactive hourly cap {GUARD_LIMITS['interactive_per_hour']}"
    else:
        b = _guard_tokens(ix, now)
        if b[0] < 1.0:
            return 'background rate (token bucket empty)'
        b[0] -= 1.0
    _guard_log.setdefault(ix, []).append([now, 'i' if interactive else 'b'])
    _guard_save()
    return None


def _cache_ttl(interactive: bool) -> float:
    return GUARD_LIMITS['query_ttl_interactive' if interactive else 'query_ttl_background']


_indexer_locks: Dict[int, threading.Lock] = {}
_indexer_last_ts: Dict[int, float] = {}
_indexer_locks_guard = threading.Lock()

_query_cache: Dict[tuple, tuple] = {}          # (ix, params) -> (ts, items)
_budget_state_guard = threading.Lock()

# Prowlarr base_url -> (fetched_at, {indexerId: disabledTill epoch})
_status_cache: Dict[str, tuple] = {}

# One log line per backoff window / budget window, not per search.
_skip_logged: Dict[tuple, Any] = {}


class IndexerSkipped(Exception):
    """Raised when a search to one indexer is deliberately not sent."""


def _indexer_lock(ix: int) -> threading.Lock:
    with _indexer_locks_guard:
        lock = _indexer_locks.get(ix)
        if lock is None:
            lock = _indexer_locks[ix] = threading.Lock()
        return lock


def _parse_iso(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _build_search_query(query: str, year: Optional[int] = None) -> str:
    """Compose the indexer search string, appending *year* only when the title
    doesn't already contain it (avoids a redundant "Title 2026 2026" that some
    trackers match too strictly)."""
    query = (query or '').strip()
    if year and str(year) not in query:
        return f'{query} {year}'
    return query

# Compound sweep queries for "Hot recent" mode.  Year × format narrows
# results enough that Prowlarr returns high-seeder items (not just newest).
SWEEP_QUERIES = [
    'WEB-DL 2024', 'WEB-DL 2025', 'WEB-DL 2026',
    'BluRay 2024', 'BluRay 2025', 'BluRay 2026',
    'REMUX 2024',  'REMUX 2025',  'REMUX 2026',
    '2160p 2024',  '2160p 2025',  '2160p 2026',
    '1080p 2024',  '1080p 2025',  '1080p 2026',
    'h264 2024',   'h264 2025',   'h264 2026',
    'h265 2024',   'h265 2025',   'h265 2026',
]


def _expand_movie_categories(categories_str: str) -> List[int]:
    """Expand a comma-separated category string.

    If the parent category ``2000`` is present the full set of movie
    sub-categories is returned so that Prowlarr browse mode returns
    items filed under sub-categories like 2050 (BluRay) or 2080 (WEB-DL).
    """
    cats = set()
    for part in categories_str.split(','):
        part = part.strip()
        if part.isdigit():
            cats.add(int(part))
    if 2000 in cats:
        cats.update(MOVIE_CATEGORIES_EXPANDED)
    return sorted(cats)


# ---------------------------------------------------------------------------
# Title parser — extract metadata from torrent names
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r'[\.\s\(](\d{4})[\.\s\)]')
_RES_RE = re.compile(r'(2160p|1080p|720p|480p)', re.IGNORECASE)
_SOURCE_RE = re.compile(
    r'(REMUX|WEB-DL|WEB\.DL|WEBDL|WEBRip|WEB(?=[\s\.])|BluRay|Blu-Ray|BDRip|BRRip|HDRip|DVDRip|HDTV)',
    re.IGNORECASE,
)


def _parse_title(name: str) -> Dict[str, Any]:
    """Extract year, resolution, and source type from a torrent name."""
    result: Dict[str, Any] = {
        'year': None,
        'resolution': None,
        'source': None,
        'clean_title': name,
    }

    # Year
    m = _YEAR_RE.search(name)
    if m:
        year = int(m.group(1))
        if 1920 <= year <= 2030:
            result['year'] = str(year)
            # Clean title = everything before the year
            idx = name.index(m.group(0))
            result['clean_title'] = re.sub(r'[\.\-_]', ' ', name[:idx]).strip()

    # Resolution — normalize to lowercase (2160P → 2160p)
    m = _RES_RE.search(name)
    if m:
        result['resolution'] = m.group(1).lower()

    # Source — check REMUX first (torrent names like "BluRay.REMUX" must
    # classify as REMUX, but the regex captures the leftmost match which
    # would be "BluRay").  Matches torrent_api.py priority order.
    name_upper = name.upper()
    if 'REMUX' in name_upper:
        result['source'] = 'REMUX'
    else:
        m = _SOURCE_RE.search(name)
        if m:
            raw = m.group(1)
            upper = raw.upper().replace('.', '-')
            if upper in ('WEBDL', 'WEB-DL', 'WEB'):
                result['source'] = 'WEB-DL'
            elif upper in ('BLURAY', 'BLU-RAY', 'BDRIP', 'BRRIP'):
                result['source'] = 'BluRay'
            else:
                result['source'] = raw

    return result


# ---------------------------------------------------------------------------
# Prowlarr client
# ---------------------------------------------------------------------------

class ProwlarrClient:
    """Client for the Prowlarr API (v1)."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'X-Api-Key': api_key,
            'Accept': 'application/json',
        })

    def test_connection(self) -> bool:
        """Test connectivity to Prowlarr."""
        try:
            resp = self.session.get(
                f'{self.base_url}/api/v1/health', timeout=10
            )
            resp.raise_for_status()
            logger.info('Prowlarr connection test passed')
            return True
        except Exception as e:
            logger.error(f'Prowlarr connection test failed: {e}')
            return False

    # -- internal helpers ---------------------------------------------------

    def _build_cat_params(self, categories: str) -> List[tuple]:
        """Build repeated (categories, N) tuples for Prowlarr."""
        return [('categories', c) for c in _expand_movie_categories(categories)]

    def _build_cat_params_raw(self, categories: list) -> List[tuple]:
        """Build repeated (categories, N) tuples from an EXPLICIT list.

        Unlike :meth:`_build_cat_params`, this performs NO 2000-expansion —
        the caller supplies the exact category set to query.
        """
        return [('categories', c) for c in categories]

    def get_indexers(self) -> List[Dict]:
        """Configured indexers as [{'id', 'name', 'enabled'}].

        Lets callers restrict a search to specific trackers instead of fanning
        out to every one — a weekly automation shouldn't hammer a ratio-metered
        private tracker it doesn't need.
        """
        try:
            resp = self.session.get(f'{self.base_url}/api/v1/indexer', timeout=20)
            resp.raise_for_status()
            out = []
            for ix in resp.json() or []:
                if ix.get('id') is None:
                    continue
                out.append({
                    'id': int(ix['id']),
                    'name': ix.get('name') or f"Indexer {ix['id']}",
                    'enabled': bool(ix.get('enable', True)),
                })
            return sorted(out, key=lambda i: i['name'].lower())
        except Exception as e:
            logger.error(f'Prowlarr get_indexers failed: {e}')
            return []

    _INDEXER_IDS_TTL = 60

    def _enabled_indexer_ids(self) -> List[int]:
        """Enabled indexer ids, cached briefly so fan-out costs no extra call."""
        now = time.time()
        cached = getattr(self, '_ix_ids_cache', None)
        if cached and now - getattr(self, '_ix_ids_ts', 0) < self._INDEXER_IDS_TTL:
            return cached
        indexers = self.get_indexers()
        ids = [ix['id'] for ix in indexers if ix.get('enabled')]
        if ids:
            self._ix_ids_cache = ids
            self._ix_ids_ts = now
            self._ix_names = {ix['id']: ix['name'] for ix in indexers}
        return ids

    # -- Prowlarr backoff awareness ----------------------------------------

    def _indexer_backoffs(self) -> Dict[int, float]:
        """{indexerId: disabledTill epoch} for indexers Prowlarr has parked.

        Cached for _INDEXER_STATUS_TTL seconds. On error the last known value
        is kept (an empty dict if none) so a Prowlarr hiccup never blocks
        searching."""
        now = time.time()
        # Module-level (per Prowlarr URL): callers build a fresh client per
        # job, and the cache should span all of them.
        ent = _status_cache.get(self.base_url)
        cached = ent[1] if ent else None
        if ent and now - ent[0] < _INDEXER_STATUS_TTL:
            return cached
        out: Dict[int, float] = {}
        try:
            resp = self.session.get(f'{self.base_url}/api/v1/indexerstatus', timeout=10)
            resp.raise_for_status()
            for st in resp.json() or []:
                till = _parse_iso(st.get('disabledTill'))
                if st.get('indexerId') is not None and till:
                    out[int(st['indexerId'])] = till
        except Exception as e:
            logger.debug(f'Prowlarr indexerstatus fetch failed: {e}')
            out = cached if cached is not None else {}
        _status_cache[self.base_url] = (now, out)
        return out

    def _invalidate_backoffs(self) -> None:
        ent = _status_cache.get(self.base_url)
        if ent:
            _status_cache[self.base_url] = (0.0, ent[1])

    def _backed_off_until(self, ix: int) -> Optional[float]:
        till = self._indexer_backoffs().get(int(ix))
        if till and till > time.time():
            return till
        return None

    def _indexer_name(self, ix: int) -> str:
        names = getattr(self, '_ix_names', None) or {}
        return names.get(int(ix), f'indexer {ix}')

    def _log_skip_once(self, ix: int, kind: str, window_key: Any, msg: str) -> None:
        key = (int(ix), kind)
        with _budget_state_guard:
            if _skip_logged.get(key) == window_key:
                return
            _skip_logged[key] = window_key
        logger.info(msg)

    def _get_search(self, params: List[tuple],
                    indexer_ids: Optional[List[int]],
                    interactive: bool = False) -> List[dict]:
        """GET /api/v1/search for ONE indexer (or an unscoped request).

        The tracker guard (GUARD_LIMITS) applies to every indexer, and to an
        unscoped search under pseudo-indexer UNSCOPED:
        * identical queries are served from cache (3 h background, 15 min user);
        * an indexer Prowlarr has in backoff (indexerstatus.disabledTill) is
          skipped; an unscoped search is skipped while any indexer is in
          backoff, or during its own cooldown after a 400/429;
        * past the caps (40 user searches per indexer per hour, background
          bucket of 5 refilling one per 180 s, 300 per indexer per day, 120 per
          hour across all indexers) nothing is sent: a stale cached answer is
          returned if there is one, else IndexerSkipped;
        * requests to one indexer are serialised and spaced;
        * no blind retry: a 400/429 means Prowlarr just parked the indexer, so
          the status cache is refreshed and later calls skip it.
        """
        single = int(indexer_ids[0]) if indexer_ids and len(indexer_ids) == 1 else None
        ix = UNSCOPED if single is None else single
        cache_key = (ix, tuple(params))
        now = time.time()
        with _budget_state_guard:
            _guard_load()
            hit = _query_cache.get(cache_key)
            if hit and now - hit[0] < _cache_ttl(interactive):
                return hit[1]

        till = self._unscoped_blocked_until(now) if single is None else self._backed_off_until(single)
        if till:
            name = 'unscoped search' if single is None else f'{self._indexer_name(single)} (id {single})'
            self._log_skip_once(
                ix, 'backoff', round(till),
                f'Prowlarr backoff: {name} skipped until '
                f'{datetime.fromtimestamp(till).strftime("%H:%M:%S")}')
            if hit:
                return hit[1]
            raise IndexerSkipped(f'indexer {ix} backed off')

        with _indexer_lock(ix):
            # Re-check after waiting for the lock: the request ahead of us may
            # have just tripped the tracker limit, or answered this query.
            if (self._unscoped_blocked_until(time.time()) if single is None
                    else self._backed_off_until(single)):
                if hit:
                    return hit[1]
                raise IndexerSkipped(f'indexer {ix} backed off')
            now = time.time()
            with _budget_state_guard:
                fresh = _query_cache.get(cache_key)
                if fresh and now - fresh[0] < _cache_ttl(interactive):
                    return fresh[1]
                reason = _guard_decide(ix, interactive, now)
                if reason:
                    hr, day, glob = _guard_counts(ix, now)
            if reason:
                self._log_skip_once(
                    ix, 'guard', int(now // 900),
                    f'{self._indexer_name(ix)} (id {ix}) search not sent: {reason} '
                    f'({hr} user last hour, {day} last 24 h, {glob} all indexers last hour)')
                if hit:
                    return hit[1]
                raise IndexerSkipped(f'indexer {ix} guard: {reason}')
            wait = GUARD_LIMITS['min_interval_s'] - (time.time() - _indexer_last_ts.get(ix, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(f'{self.base_url}/api/v1/search', params=params, timeout=60)
            finally:
                _indexer_last_ts[ix] = time.time()

        if resp.status_code in (400, 429):
            # Prowlarr parked the indexer (tracker 429): pick that up now so
            # every following call skips it instead of collecting more 400s.
            self._invalidate_backoffs()
            if single is None:
                self._start_unscoped_cooldown()
        resp.raise_for_status()
        items = resp.json()
        with _budget_state_guard:
            _query_cache[cache_key] = (time.time(), items)
            if len(_query_cache) > 500:
                for k, _ in sorted(_query_cache.items(), key=lambda kv: kv[1][0])[:100]:
                    _query_cache.pop(k, None)
        return items

    def _unscoped_blocked_until(self, now: float) -> Optional[float]:
        """Until when an unscoped search must not be sent: its own cooldown
        after a 400/429, or the latest backoff of any indexer."""
        with _budget_state_guard:
            _guard_load()
            cool = _guard_cooldown.get(UNSCOPED, 0.0)
        tills = [t for t in self._indexer_backoffs().values() if t > now]
        till = max([cool] + tills)
        return till if till > now else None

    def _start_unscoped_cooldown(self) -> None:
        now = time.time()
        tills = [t for t in self._indexer_backoffs().values() if t > now]
        until = max(tills) if tills else now + GUARD_LIMITS['unscoped_cooldown_s']
        with _budget_state_guard:
            _guard_load()
            _guard_cooldown[UNSCOPED] = until
            _guard_save()

    def _search_request(self, query: str, categories: str, limit: int,
                        indexer_ids: Optional[List[int]] = None) -> List[dict]:
        """One HTTP search against Prowlarr."""
        params: List[tuple] = [
            ('query', query),
            ('type', 'search'),
            ('limit', limit),
        ] + self._build_cat_params(categories)

        for ix in (indexer_ids or []):
            params.append(('indexerIds', int(ix)))

        return self._get_search(params, indexer_ids)

    def _search_request_raw(self, query: str, categories: list, limit: int,
                            indexer_ids: Optional[List[int]] = None) -> List[dict]:
        """One HTTP search against Prowlarr with an EXPLICIT category list."""
        params: List[tuple] = [
            ('query', query),
            ('type', 'search'),
            ('limit', limit),
        ] + self._build_cat_params_raw(categories)

        for ix in (indexer_ids or []):
            params.append(('indexerIds', int(ix)))

        return self._get_search(params, indexer_ids, interactive=True)

    def _search_single(self, query: str, categories: str,
                       limit: int,
                       indexer_ids: Optional[List[int]] = None) -> List[dict]:
        """Run a Prowlarr search and return raw JSON items.

        With *indexer_ids*, only those indexers are queried — Prowlarr never
        contacts the others, so this is a real reduction in tracker traffic and
        not just a filter on the way back.

        Without them the search is fanned out one indexer at a time instead of
        letting Prowlarr aggregate it. A single failing indexer makes Prowlarr
        return an empty array for the WHOLE request rather than partial
        results: a dead MoreThanTV was silently zeroing every movie *and* TV
        search in the app, which reads exactly like "nothing new" and left both
        auto-grab automations idle. Per-indexer requests confine that to the
        indexer that is actually broken. Prowlarr applies `limit` per indexer
        either way, so a healthy fan-out returns the same set as the aggregate.
        """
        if indexer_ids:
            targets = [int(i) for i in indexer_ids]
        else:
            targets = self._enabled_indexer_ids()
        explicit = bool(indexer_ids)
        if len(targets) <= 1:
            try:
                return self._search_request(query, categories, limit, targets or None)
            except IndexerSkipped:
                # Callers that pinned specific indexers (Movie Night, Genre
                # Roulette) must see "couldn't search" (an exception), not
                # "searched, nothing matched", or they burn their run slot.
                if explicit:
                    raise
                return []

        skipped: List[int] = []

        def _one(ix: int):
            try:
                return ix, self._search_request(query, categories, limit, [ix])
            except IndexerSkipped:
                skipped.append(ix)
                return ix, []
            except Exception as e:
                logger.warning(f'Prowlarr search failed on indexer {ix}: {e}')
                return ix, []

        merged: List[dict] = []
        empty: List[int] = []
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            for ix, items in pool.map(_one, targets):
                if items:
                    merged.extend(items)
                else:
                    empty.append(ix)
        if explicit and skipped and len(skipped) == len(targets):
            raise IndexerSkipped(f'all requested indexers {skipped} backed off / over budget')
        empty = [ix for ix in empty if ix not in skipped]
        if empty:
            logger.info(f'Prowlarr: no results from indexer(s) {empty} for {query!r}')
        return merged

    @staticmethod
    def _parse_items(raw_items: list) -> List[Dict]:
        """Convert raw Prowlarr JSON items into normalised movie dicts."""
        movies: List[Dict] = []
        for item in raw_items:
            seeders = item.get('seeders', 0)
            if seeders is None:
                seeders = 0
            elif isinstance(seeders, str):
                seeders = int(seeders) if seeders.isdigit() else 0

            parsed = _parse_title(item.get('title', ''))
            movies.append({
                'title': parsed['clean_title'],
                'year': parsed['year'] or '',
                'seeders': seeders,
                'size_gb': round(item.get('size', 0) / (1024 ** 3), 2) if item.get('size') else 0,
                'resolution': parsed['resolution'] or '',
                'source': parsed['source'] or '',
                'tracker': item.get('indexer', ''),
                'pub_date': item.get('publishDate', ''),
                'download_url': item.get('downloadUrl', ''),
                'info_hash': item.get('infoHash', ''),
                'imdb_id': item.get('imdbId', '') or '',
                'torrent_name': item.get('title', ''),
                # Needed for Radarr's "grab specific release" endpoint —
                # without these the user can't trigger a direct download.
                'guid': item.get('guid', '') or '',
                'indexer_id': item.get('indexerId'),
            })
        return movies

    # -- public API ---------------------------------------------------------

    def search_movies(self, categories: str = '2000',
                      limit: int = 300) -> List[Dict]:
        """Browse Prowlarr indexer RSS feeds for recent movie torrents."""
        try:
            raw = self._search_single('', categories, limit)
            movies = self._parse_items(raw[:limit])
            max_s = max((m['seeders'] for m in movies), default=0)
            logger.info(f'Prowlarr browse returned {len(movies)} movies (max seeders={max_s})')
            return movies
        except Exception as e:
            logger.error(f'Prowlarr search_movies failed: {e}')
            return []

    def sweep_movies(self, categories: str = '2000',
                     queries: Optional[List[str]] = None,
                     max_workers: int = 3) -> List[Dict]:
        """Run broad keyword sweeps across Prowlarr to find high-seeder
        recent movies.

        Each query in *queries* is sent as a title-less keyword search
        (e.g. "2025", "1080p", "BluRay").  Results from all queries are
        merged and deduplicated by (indexer, guid).
        """
        if queries is None:
            queries = list(SWEEP_QUERIES)

        seen_guids: set = set()
        all_raw: list = []

        def _fetch(q: str) -> List[dict]:
            try:
                items = self._search_single(q, categories, 500)
                logger.info(f'Prowlarr sweep q={q!r}: {len(items)} results')
                return items
            except Exception as e:
                logger.warning(f'Prowlarr sweep q={q!r} failed: {e}')
                return []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch, q): q for q in queries}
            for fut in as_completed(futures):
                for item in fut.result():
                    key = (item.get('indexer', ''), item.get('guid', ''))
                    if key not in seen_guids:
                        seen_guids.add(key)
                        all_raw.append(item)

        movies = self._parse_items(all_raw)
        max_s = max((m['seeders'] for m in movies), default=0)
        logger.info(
            f'Prowlarr sweep total: {len(movies)} unique items from '
            f'{len(queries)} queries (max seeders={max_s})'
        )
        return movies

    def search_all(self, query: str, media_type: str,
                   year: Optional[int] = None) -> List[Dict]:
        """Run a RAW, broad-category title search with NO quality filtering.

        "All results" is intentionally CROSS-category: we always search movie
        AND TV/Sports categories so a wrong-category item (e.g. a PPV filed
        under TV Sports) surfaces regardless of how the modal classified the
        title. *media_type* only drives the later grab/rename identity, not the
        search scope.

        Fans out one indexer at a time (same rationale as `_search_single`):
        Prowlarr's aggregated search returns an empty array for the WHOLE
        request when any single indexer errors, which silently zeroed IPT
        Sports hits (e.g. AEW PPVs) whenever MoreThanTV/etc. was unhealthy.
        Per-indexer requests confine failures to the broken tracker.
        """
        categories = list(MOVIE_CATEGORIES_EXPANDED) + list(TV_SPORTS_CATEGORIES)
        q = _build_search_query(query, year)

        targets = self._enabled_indexer_ids()

        def _one(ix: Optional[int]):
            try:
                ids = [ix] if ix is not None else None
                return ix, self._search_request_raw(q, categories, 500, ids)
            except IndexerSkipped:
                return ix, []
            except Exception as e:
                logger.warning(
                    f'Prowlarr search_all failed on indexer {ix} (q={q!r}): {e}'
                )
                return ix, []

        if len(targets) <= 1:
            _, raw_items = _one(targets[0] if targets else None)
        else:
            raw_items = []
            empty: List[int] = []
            with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
                for ix, items in pool.map(_one, targets):
                    if items:
                        raw_items.extend(items)
                    elif ix is not None:
                        empty.append(ix)
            if empty:
                logger.info(
                    f'Prowlarr search_all: no results from indexer(s) {empty} '
                    f'for {q!r}'
                )

        results: List[Dict] = []
        for item in raw_items:
            seeders = item.get('seeders', 0)
            if seeders is None:
                seeders = 0
            elif isinstance(seeders, str):
                seeders = int(seeders) if seeders.isdigit() else 0

            leechers = item.get('leechers', 0) or 0

            parsed = _parse_title(item.get('title', ''))
            results.append({
                'title': item.get('title', '') or '',
                'clean_title': parsed['clean_title'],
                'year': parsed['year'] or '',
                'resolution': parsed['resolution'] or '',
                'source': parsed['source'] or '',
                'seeders': seeders,
                'leechers': leechers,
                'size': item.get('size', 0) or 0,
                'indexer': item.get('indexer', '') or '',
                'tracker': item.get('indexer', '') or '',
                'indexer_id': item.get('indexerId'),
                'pub_date': item.get('publishDate', '') or '',
                'download_url': item.get('downloadUrl', '') or '',
                'info_hash': item.get('infoHash', '') or '',
                'guid': item.get('guid', '') or '',
                'protocol': item.get('protocol') or 'torrent',
                'imdb_id': item.get('imdbId', '') or '',
            })

        logger.info(
            f'Prowlarr search_all q={q!r} media_type={media_type}: '
            f'{len(results)} results'
        )
        return results




# ---------------------------------------------------------------------------
# Torznab client
# ---------------------------------------------------------------------------

class TorznabClient:
    """Client for a generic Torznab endpoint."""

    def __init__(self, torznab_url: str, api_key: str):
        self.torznab_url = torznab_url.rstrip('/')
        self.api_key = api_key
        self.session = requests.Session()

    def test_connection(self) -> bool:
        """Test connectivity by requesting caps."""
        try:
            resp = self.session.get(
                self.torznab_url,
                params={'t': 'caps', 'apikey': self.api_key},
                timeout=10,
            )
            resp.raise_for_status()
            if '<caps>' in resp.text.lower() or '<caps ' in resp.text.lower():
                logger.info('Torznab connection test passed')
                return True
            logger.warning('Torznab caps response missing <caps> element')
            return False
        except Exception as e:
            logger.error(f'Torznab connection test failed: {e}')
            return False

    # -- internal helpers ---------------------------------------------------

    def _cat_str(self, categories: str) -> str:
        return ','.join(str(c) for c in _expand_movie_categories(categories))

    def _cat_str_raw(self, categories: list) -> str:
        """Comma-join an EXPLICIT category list (no 2000-expansion)."""
        return ','.join(str(c) for c in categories)

    def get_indexers(self) -> List[Dict]:
        """Torznab is a single aggregate feed — individual trackers aren't
        addressable, so there's nothing to choose from. Callers fall back to
        filtering results by tracker name."""
        return []

    def _search_single(self, query: str, categories: str,
                       limit: int,
                       indexer_ids: Optional[List[int]] = None) -> List[Dict]:
        """Run one Torznab search and return parsed movie dicts.

        *indexer_ids* is accepted for signature parity with ProwlarrClient and
        ignored: a Torznab endpoint is one feed with no per-indexer selector.
        """
        resp = self.session.get(
            self.torznab_url,
            params={
                't': 'search',
                'q': query,
                'cat': self._cat_str(categories),
                'apikey': self.api_key,
                'limit': limit,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return self._parse_rss(resp.text, limit)

    def _parse_rss(self, xml_text: str, limit: int) -> List[Dict]:
        """Parse Torznab RSS XML into normalised movie dicts."""
        movies: List[Dict] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f'Torznab XML parse error: {e}')
            return movies

        # Torznab uses newznab namespace for attributes
        ns = {'torznab': 'http://torznab.com/schemas/2015/feed',
              'newznab': 'http://www.newznab.com/DTD/2010/feeds/attributes/'}

        channel = root.find('.//channel')
        if channel is None:
            return movies

        for item in channel.findall('item')[:limit]:
            title = item.findtext('title', '')
            parsed = _parse_title(title)

            # Extract torznab/newznab attrs
            attrs = {}
            for attr_el in item.findall('torznab:attr', ns) + item.findall('newznab:attr', ns):
                attrs[attr_el.get('name', '')] = attr_el.get('value', '')

            seeders = int(attrs.get('seeders', 0) or 0)
            size_bytes = int(attrs.get('size', 0) or item.findtext('size', '0') or 0)

            movies.append({
                'title': parsed['clean_title'],
                'year': parsed['year'] or '',
                'seeders': seeders,
                'size_gb': round(size_bytes / (1024 ** 3), 2) if size_bytes else 0,
                'resolution': parsed['resolution'] or '',
                'source': parsed['source'] or '',
                'tracker': attrs.get('indexer', ''),
                'pub_date': item.findtext('pubDate', ''),
                'download_url': item.findtext('link', ''),
                'info_hash': attrs.get('infohash', ''),
                'imdb_id': attrs.get('imdb', '') or '',
                'torrent_name': title,
            })

        return movies

    # -- public API ---------------------------------------------------------

    def search_movies(self, categories: str = '2000',
                      limit: int = 300) -> List[Dict]:
        """Browse a Torznab endpoint for recent movie torrents."""
        try:
            movies = self._search_single('', categories, limit)
            logger.info(f'Torznab browse returned {len(movies)} movies')
            return movies
        except Exception as e:
            logger.error(f'Torznab search_movies failed: {e}')
            return []

    def sweep_movies(self, categories: str = '2000',
                     queries: Optional[List[str]] = None,
                     max_workers: int = 3) -> List[Dict]:
        """Run broad keyword sweeps via Torznab to find high-seeder movies."""
        if queries is None:
            queries = list(SWEEP_QUERIES)

        seen: set = set()
        all_movies: List[Dict] = []

        def _fetch(q: str) -> List[Dict]:
            try:
                items = self._search_single(q, categories, 500)
                logger.info(f'Torznab sweep q={q!r}: {len(items)} results')
                return items
            except Exception as e:
                logger.warning(f'Torznab sweep q={q!r} failed: {e}')
                return []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch, q): q for q in queries}
            for fut in as_completed(futures):
                for m in fut.result():
                    key = (m.get('tracker', ''), m.get('torrent_name', ''))
                    if key not in seen:
                        seen.add(key)
                        all_movies.append(m)

        max_s = max((m['seeders'] for m in all_movies), default=0)
        logger.info(
            f'Torznab sweep total: {len(all_movies)} unique items from '
            f'{len(queries)} queries (max seeders={max_s})'
        )
        return all_movies

    def search_all(self, query: str, media_type: str,
                   year: Optional[int] = None) -> List[Dict]:
        """Run a RAW, broad-category title search with NO quality filtering.

        "All results" is intentionally CROSS-category: always searches movie AND
        TV/Sports categories so wrong-category items surface regardless of the
        modal's media_type (which only drives the later grab/rename identity).
        """
        categories = list(MOVIE_CATEGORIES_EXPANDED) + list(TV_SPORTS_CATEGORIES)

        q = _build_search_query(query, year)

        try:
            resp = self.session.get(
                self.torznab_url,
                params={
                    't': 'search',
                    'q': q,
                    'cat': self._cat_str_raw(categories),
                    'apikey': self.api_key,
                },
                timeout=60,
            )
            resp.raise_for_status()
            xml_text = resp.text
        except Exception as e:
            logger.error(f'Torznab search_all failed (q={q!r}): {e}')
            return []

        results: List[Dict] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f'Torznab search_all XML parse error: {e}')
            return results

        ns = {'torznab': 'http://torznab.com/schemas/2015/feed',
              'newznab': 'http://www.newznab.com/DTD/2010/feeds/attributes/'}

        channel = root.find('.//channel')
        if channel is None:
            return results

        for item in channel.findall('item'):
            title = item.findtext('title', '') or ''
            parsed = _parse_title(title)

            attrs = {}
            for attr_el in item.findall('torznab:attr', ns) + item.findall('newznab:attr', ns):
                attrs[attr_el.get('name', '')] = attr_el.get('value', '')

            seeders = int(attrs.get('seeders', 0) or 0)
            leechers = int(attrs.get('leechers', 0) or attrs.get('peers', 0) or 0)
            size_bytes = int(attrs.get('size', 0) or item.findtext('size', '0') or 0)

            results.append({
                'title': title,
                'clean_title': parsed['clean_title'],
                'year': parsed['year'] or '',
                'resolution': parsed['resolution'] or '',
                'source': parsed['source'] or '',
                'seeders': seeders,
                'leechers': leechers,
                'size': size_bytes,
                'indexer': attrs.get('indexer', '') or '',
                'tracker': attrs.get('indexer', '') or '',
                'indexer_id': attrs.get('indexerId') or None,
                'pub_date': item.findtext('pubDate', '') or '',
                'download_url': item.findtext('link', '') or '',
                'info_hash': attrs.get('infohash', '') or '',
                'guid': item.findtext('guid', '') or '',
                'protocol': attrs.get('protocol') or 'torrent',
                'imdb_id': attrs.get('imdb', '') or '',
            })

        logger.info(
            f'Torznab search_all q={q!r} media_type={media_type}: '
            f'{len(results)} results'
        )
        return results


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_indexer_client(settings) -> Optional[Any]:
    """Return the appropriate indexer client based on settings, or None."""
    provider = settings.get('indexer.provider') or 'prowlarr'
    api_key = settings.get('indexer.api_key') or ''

    if not api_key:
        return None

    if provider == 'torznab':
        torznab_url = settings.get('indexer.torznab_url') or ''
        if not torznab_url:
            return None
        return TorznabClient(torznab_url, api_key)
    else:
        base_url = settings.get('indexer.base_url') or ''
        if not base_url:
            return None
        return ProwlarrClient(base_url, api_key)
