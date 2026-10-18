"""Direct search across the owner's enabled trackers (no Prowlarr, no Jackett).

LumaTrackersClient (get_indexers, _search_single, search_movies, sweep_movies,
search_all) serves the Explore, Sources, Latest, Movie Night, Genre Roulette and
upgrade code paths. get_indexer_client() returns it as soon as at least one
tracker is enabled; v172: it is the only search client (the Prowlarr and
Torznab-proxy clients are gone).

Every request goes through app.tracker_guard (rate limits, backoff on 429,
bans, rejected credentials and failures) and a query cache (15 min for user
searches, 3 h for background ones; empty answers are never cached). When the
guard refuses a request, a stale cached answer is used if there is one.

Results never carry a tracker secret. Download links and magnets (which
embed passkeys) are swapped for opaque release tokens
(/api/trackers/release/<token>) that are resolved server-side only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import tracker_clients, tracker_types as tt
from app.tracker_guard import TrackerGuard, address_scope, get_guard, guard_key, guarded_test, record_failure
from app.tracker_http import TrackerError
from app.tracker_store import STATUS_ERROR, STATUS_OK, TrackerStore, get_store

logger = logging.getLogger(__name__)

TTL_INTERACTIVE = 900
# Negative cache (PM): a signed-in tracker that answered with zero raw rows.
NEG_TTL = 120
TTL_BACKGROUND = 3 * 3600
CACHE_MAX = 500
RELEASE_TTL = 14 * 86400
RELEASE_MAX = 20000
RELEASE_FILENAME = 'tracker_releases.json'
RELEASE_PATH = '/api/trackers/release/'


class TrackerSkipped(Exception):
    """A request to a tracker was deliberately not sent (rate limit or backoff)."""


def search_status(err: Exception) -> str:
    """Short per-tracker status for the Sources UI: 'auth' (sign-in failed,
    banned, or in an auth/ban backoff), 'rate' (rate limit, caps, rate backoff)
    or 'unavailable' (anything else)."""
    if isinstance(err, TrackerSkipped):
        reason = str(err)
        if reason.startswith('backoff ('):
            kind = reason[len('backoff ('):].rstrip(')')
        elif 'cap' in reason or 'rate limit' in reason:
            kind = 'rate'
        else:
            kind = 'unavailable'
    else:
        kind = getattr(err, 'kind', '')
    if kind in ('auth', 'banned'):
        return 'auth'
    if kind == 'rate':
        return 'rate'
    return 'unavailable'


# ------------------------------------------------------------------ release tokens

class ReleaseTokens:
    """token -> (tracker id, real download URL or magnet). The URLs embed
    passkeys, so the file is encrypted with the tracker store's key."""

    def __init__(self, store: TrackerStore, path: Optional[Path] = None):
        self._store = store
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._map: Dict[str, Tuple[int, str, float]] = {}
        self._by_url: Dict[Tuple[int, str], str] = {}
        self._loaded = False
        self._dirty = False

    @property
    def path(self) -> Path:
        if self._path:
            return self._path
        from app.first_run import data_dir
        return Path(data_dir()) / RELEASE_FILENAME

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            blob = self.path.read_text(encoding='ascii').strip()
        except (FileNotFoundError, UnicodeDecodeError):
            return
        except OSError:
            return
        try:
            raw = json.loads(self._store._decrypt(blob))
        except Exception:   # unreadable or key changed: old links simply expire
            logger.warning('Tracker release links could not be read; older links will not resolve')
            return
        now = time.time()
        for tok, v in (raw.items() if isinstance(raw, dict) else []):
            if isinstance(v, list) and len(v) == 3 and isinstance(v[0], int) and isinstance(v[1], str) \
                    and isinstance(v[2], (int, float)) and v[2] > now:
                self._map[tok] = (v[0], v[1], float(v[2]))
                self._by_url[(v[0], v[1])] = tok

    def _prune(self, now: float) -> None:
        for tok, (tid, url, exp) in list(self._map.items()):
            if exp <= now:
                self._map.pop(tok, None)
                self._by_url.pop((tid, url), None)
        if len(self._map) > RELEASE_MAX:
            for tok, (tid, url, _) in sorted(self._map.items(), key=lambda kv: kv[1][2])[:len(self._map) - RELEASE_MAX]:
                self._map.pop(tok, None)
                self._by_url.pop((tid, url), None)

    def issue(self, tracker_id: int, url: str) -> str:
        now = time.time()
        with self._lock:
            self._load()
            tok = self._by_url.get((tracker_id, url))
            if tok and tok in self._map:
                tid, u, _ = self._map[tok]
                self._map[tok] = (tid, u, now + RELEASE_TTL)
                return tok
            tok = secrets.token_urlsafe(24)
            self._map[tok] = (tracker_id, url, now + RELEASE_TTL)
            self._by_url[(tracker_id, url)] = tok
            self._dirty = True
            return tok

    def resolve(self, token: str) -> Optional[Tuple[int, str]]:
        with self._lock:
            self._load()
            hit = self._map.get(str(token or ''))
            if not hit or hit[2] <= time.time():
                return None
            return hit[0], hit[1]

    def forget_tracker(self, tracker_id: int) -> None:
        """Drop every link of a tracker (deleted, or its URL changed)."""
        with self._lock:
            self._load()
            for tok, (tid, url, _) in list(self._map.items()):
                if tid == tracker_id:
                    self._map.pop(tok, None)
                    self._by_url.pop((tid, url), None)
                    self._dirty = True
        self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._prune(time.time())
            data = json.dumps({t: [tid, u, exp] for t, (tid, u, exp) in self._map.items()})
            try:
                blob = self._store._encrypt(data)
                import os
                import tempfile
                p = self.path
                p.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(prefix=f'.{p.name}.', dir=str(p.parent))
                try:
                    with os.fdopen(fd, 'w', encoding='ascii') as fh:
                        fh.write(blob)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, p)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
                self._dirty = False
            except Exception as e:
                logger.warning('Tracker release links not saved: %s', type(e).__name__)


def release_path(token: str) -> str:
    """Our /api/trackers/release/<token> link under the configured URL base."""
    base = ''
    try:
        from flask import current_app
        base = (current_app.config.get('URL_BASE') or '').rstrip('/')
    except RuntimeError:       # outside a request (background job)
        try:
            from app.main import app as _app
            base = (_app.config.get('URL_BASE') or '').rstrip('/')
        except Exception:
            base = ''
    return f'{base}{RELEASE_PATH}{token}'


# ------------------------------------------------------------------ manager

def _secret_values(cfg: Dict[str, Any]) -> List[str]:
    return [str(cfg[k]) for k in tt.secret_keys(cfg.get('type')) if cfg.get(k) and len(str(cfg[k])) >= 4]


def _has_secret(value: str, secret_values: List[str]) -> bool:
    return any(s in value for s in secret_values)




def _match_categories(cfg: Dict[str, Any], requested: List[int]) -> List[int]:
    """Categories to send this tracker for a request: the requested ones it is
    set up for (a parent like 2000 covers its subcategories). Site types
    search by kind, so they get the parent of each matching kind. Empty: the
    tracker is not searched."""
    t = tt.get_type(cfg.get('type'))
    if t is None:
        return []
    mine = {int(c) for c in cfg.get('categories') or []}

    def covers(c: int) -> bool:
        return c in mine or (c // 1000 * 1000) in mine

    hits = {int(c) for c in requested if covers(int(c))}
    if t['client'] == 'torznab':
        return sorted(hits)
    return sorted({c // 1000 * 1000 for c in hits})


class TrackerSearch:
    def __init__(self, store: Optional[TrackerStore] = None, guard: Optional[TrackerGuard] = None,
                 request=None, tokens: Optional[ReleaseTokens] = None):
        self._store = store
        self._guard = guard
        self.request = request or tracker_clients.http_request
        self._tokens = tokens
        self._tokens_for: Optional[TrackerStore] = None
        self._cache: Dict[tuple, Tuple[float, list]] = {}
        self._cache_lock = threading.Lock()
        self._skip_logged: Dict[tuple, Any] = {}

    # The process-wide store and guard are looked up on use, so a store
    # swapped in later (tests, a reloaded data dir) is the one searched.
    @property
    def store(self) -> TrackerStore:
        return self._store or get_store()

    @property
    def guard(self) -> TrackerGuard:
        return self._guard or get_guard()

    @property
    def tokens(self) -> ReleaseTokens:
        if self._tokens is not None:
            return self._tokens
        store = self.store
        with self._cache_lock:
            if self._tokens_for is not store:
                self._auto_tokens = ReleaseTokens(store)
                self._tokens_for = store
            return self._auto_tokens

    # -- one tracker -------------------------------------------------------

    def _log_once(self, key: tuple, window: Any, msg: str, *args, level: int = logging.INFO) -> None:
        with self._cache_lock:
            if self._skip_logged.get(key) == window:
                return
            self._skip_logged[key] = window
        logger.log(level, msg, *args)

    @staticmethod
    def _log_steps(cfg: Dict[str, Any], st: Dict[str, Any]) -> None:
        """One line per attempt of a multi-step search (BHD): step name, HTTP
        status, the API's success/status_code, rows or the error kind. Never
        the query, a URL or a key."""
        for i, s in enumerate(st.get('steps') or []):
            logger.info('Tracker %s (id %s) search step %d %s: HTTP %s, success %s, status_code %s, %s',
                        cfg['name'], cfg['id'], i + 1, s.get('step'),
                        s.get('http') if s.get('http') is not None else '-',
                        s.get('success') if s.get('success') is not None else '-',
                        s.get('status_code') if s.get('status_code') is not None else '-',
                        f"error {s['error']}" if s.get('error') else f"{s.get('rows', 0)} rows")

    def _record_failure(self, cfg: Dict[str, Any], gk: str, err: TrackerError, scope=None) -> None:
        until = record_failure(self.guard, gk, err, scope)
        self.store.set_status(cfg['id'], STATUS_ERROR, err.message)
        self._log_once((gk, 'fail'), round(until),
                       'Tracker %s (id %s) %s: no requests until %s', cfg['name'], cfg['id'], err.kind,
                       time.strftime('%H:%M:%S', time.localtime(until)))

    def _neg_store(self) -> Dict[tuple, float]:
        neg = getattr(self, '_neg', None)
        if neg is None:
            neg = self._neg = {}
        return neg

    def search_one(self, cfg: Dict[str, Any], query: str, categories: List[int], limit: int,
                   interactive: bool, imdb_id: str = '', tmdb_id: str = '', media_type: str = '',
                   refresh: bool = False, purpose: Optional[str] = None) -> List[Dict[str, Any]]:
        """Guarded, cached search of one tracker; raw client results.
        Raises TrackerSkipped (guard) or TrackerError (the tracker failed).
        A background search with a *purpose* ('tv') is also held to that
        purpose's budget in the guard (every page it sends is counted).

        Negative cache: a signed-in answer with zero raw rows is remembered
        for NEG_TTL per tracker and exact query. Never after an error (auth,
        parse, timeout, 429) or when rows were parsed and then dropped;
        refresh=True (an explicit user retry) bypasses and clears it."""
        gk = guard_key(cfg)
        if not tt.searches_by_id(cfg.get('type')):
            tmdb_id, media_type = '', ''
        ckey = (cfg['id'], (query or '').strip().lower(), tuple(categories), imdb_id or '', int(limit),
                str(tmdb_id or ''), media_type or '')
        nkey = (cfg['id'], query or '', tuple(categories), imdb_id or '', int(limit),
                str(tmdb_id or ''), media_type or '')
        ttl = TTL_INTERACTIVE if interactive else TTL_BACKGROUND
        now = time.time()
        with self._cache_lock:
            neg = self._neg_store()
            if refresh:
                neg.pop(nkey, None)
            else:
                ts = neg.get(nkey)
                if ts is not None and now - ts < NEG_TTL:
                    logger.info('Tracker %s (id %s) search: cached empty answer', cfg['name'], cfg['id'])
                    return []
                if ts is not None:
                    neg.pop(nkey, None)
            hit = self._cache.get(ckey)
        if hit and now - hit[0] < ttl:
            logger.info('Tracker %s (id %s) search: cached answer, %d results', cfg['name'], cfg['id'], len(hit[1]))
            return hit[1]
        strict = tt.is_rate_strict(cfg['type'])
        with self.guard.tracker_lock(gk):
            with self._cache_lock:
                fresh = self._cache.get(ckey)
            if fresh and time.time() - fresh[0] < ttl:
                return fresh[1]
            reason = self.guard.take(gk, interactive, strict, purpose=purpose)
            if reason:
                self._log_once((gk, 'skip'), int(time.time() // 900),
                               'Tracker %s (id %s) search not sent: %s', cfg['name'], cfg['id'], reason,
                               level=logging.WARNING)
                if hit:
                    return hit[1]
                raise TrackerSkipped(reason)
            self.guard.wait_spacing(gk)
            scope = address_scope(cfg, self.guard)
            sent = [0]

            def charged(*a, **kw):
                # Caps count HTTP requests: the first was taken above, every page and
                # fallback after it is taken here, and none is sent once refused.
                if sent[0]:
                    why = self.guard.take(gk, interactive, strict, purpose=purpose)
                    if why:
                        raise tracker_clients.OutOfBudget(why)
                sent[0] += 1
                return self.request(*a, **kw)
            # Stats are per thread: clear the last tracker's, so a client that sets
            # none (Torznab) is never judged or logged by another's numbers.
            tracker_clients._set_stats()
            try:
                with scope:
                    items = tracker_clients.search(cfg, query, categories, limit, charged, imdb_id=imdb_id,
                                                   tmdb_id=str(tmdb_id or ''), media_type=media_type or '')
            except tracker_clients.OutOfBudget as e:
                self._log_steps(cfg, tracker_clients.last_stats())
                self._log_once((gk, 'skip'), int(time.time() // 900),
                               'Tracker %s (id %s) search not sent: %s', cfg['name'], cfg['id'], e.reason,
                               level=logging.WARNING)
                if hit:
                    return hit[1]
                raise TrackerSkipped(e.reason) from None
            except TrackerError as e:
                st = tracker_clients.last_stats()
                self._log_steps(cfg, st)
                # One redacted line per failure: name, stage, reason, HTTP status. Never a URL or header.
                logger.warning('Tracker %s (id %s) search failed: %s (HTTP %s, %s bytes, %s rows parsed)',
                               cfg['name'], cfg['id'], e.kind,
                               e.status if e.status is not None else st.get('status') or '-',
                               st.get('bytes', '-'), st.get('rows', '-'))
                self._record_failure(cfg, gk, e, scope)
                with self._cache_lock:
                    self._neg_store().pop(nkey, None)
                raise
            except Exception as e:       # parser bug: treat as a bad answer, never leak details
                logger.warning('Tracker %s (id %s) search failed: parse (%s)', cfg['name'], cfg['id'],
                               type(e).__name__)
                err = TrackerError('response')
                self._record_failure(cfg, gk, err)
                raise err from None
        self.guard.success(gk)
        self.store.set_status(cfg['id'], STATUS_OK, '')
        st = tracker_clients.last_stats()
        self._log_steps(cfg, st)
        kinds = st.get('kinds') or {}
        if not kinds:
            for it in items:
                k = tracker_clients.release_kind(it.get('title'))
                kinds[k] = kinds.get(k, 0) + 1
        logger.info('Tracker %s (id %s) search: HTTP %s, %s bytes, %s rows parsed, %d results, %s dropped, '
                    'pages %s, by id %s, types %s',
                    cfg['name'], cfg['id'], st.get('status', '-'), st.get('bytes', '-'), st.get('rows', '-'),
                    len(items), (st['rows'] - len(items)) if isinstance(st.get('rows'), int) else '-',
                    st.get('pages', '-'),
                    ('yes' if st.get('by_id') else 'no') + (f", fallback {st['fallback']}" if st.get('fallback') else ''),
                    ', '.join(f'{k} {v}' for k, v in sorted(kinds.items())) or '-')
        # A multi-step chain (BHD) that hit an error on any step, or that the
        # time budget cut short, is not a clean "nothing here": never cached.
        chain_clean = (not any(x.get('error') for x in (st.get('steps') or []))
                       and not st.get('stopped'))
        if not items and not st.get('rows') and chain_clean:
            # Signed in (no auth error) and zero raw rows: remember it briefly.
            with self._cache_lock:
                neg = self._neg_store()
                neg[nkey] = time.time()
                if len(neg) > CACHE_MAX:
                    for k, _ in sorted(neg.items(), key=lambda kv: kv[1])[:100]:
                        neg.pop(k, None)
        if items:          # an empty answer is never cached as a positive one
            with self._cache_lock:
                self._cache[ckey] = (time.time(), items)
                if len(self._cache) > CACHE_MAX:
                    for k, _ in sorted(self._cache.items(), key=lambda kv: kv[1][0])[:100]:
                        self._cache.pop(k, None)
        return items

    def test(self, cfg: Dict[str, Any]) -> None:
        """Connection test through the same guard as searches (see
        tracker_guard.guarded_test)."""
        guarded_test(cfg, lambda c: tracker_clients.test(c, self.request), self.guard)

    # -- results -----------------------------------------------------------

    def to_item(self, cfg: Dict[str, Any], r: Dict[str, Any], secret_values: List[str]) -> Optional[Dict[str, Any]]:
        """A client result in the shared raw item shape (indexer_api.parse_items), secrets removed."""
        link = r.get('download_url') or ''
        magnet = r.get('magnet_url') or ''
        target = link or magnet
        if not target:
            return None
        title = r.get('title') or ''
        if not title or _has_secret(title, secret_values):
            return None
        token = self.tokens.issue(int(cfg['id']), target)
        # Always the hashed id: a tracker's details/comments link often
        # carries a passkey that isn't one of the configured secret values.
        guid = f"luma-tracker:{cfg['id']}:{hashlib.sha256(target.encode('utf-8')).hexdigest()[:24]}"
        return {
            'title': title, 'size': r.get('size') or 0, 'seeders': r.get('seeders') or 0,
            'leechers': r.get('leechers') or 0, 'indexer': cfg['name'], 'indexerId': int(cfg['id']),
            'publishDate': r.get('pub_date') or '', 'downloadUrl': release_path(token),
            'infoHash': r.get('info_hash') or '', 'imdbId': r.get('imdb_id') or '', 'guid': guid,
            'protocol': r.get('protocol') or 'torrent', 'freeleech': bool(r.get('freeleech')),
            'tracker_type': cfg['type'],
        }

    def search(self, query: str, categories: List[int], limit: int, interactive: bool,
               tracker_ids: Optional[List[int]] = None, imdb_id: str = '', tmdb_id: str = '',
               media_type: str = '', refresh: bool = False,
               statuses: Optional[List[Dict[str, Any]]] = None,
               ) -> Tuple[List[Dict[str, Any]], List[int], List[int]]:
        """Fan out to enabled trackers (or *tracker_ids*). Returns
        (items in raw Prowlarr shape, ids searched, ids skipped). When
        *statuses* is a list, each tracker that was skipped or failed adds
        {'id', 'name', 'status'} (see search_status)."""
        cfgs = self.store.enabled_configs()
        if tracker_ids:
            want = {int(i) for i in tracker_ids}
            cfgs = [c for c in cfgs if c['id'] in want]
        jobs = []
        for c in cfgs:
            cats = _match_categories(c, categories)
            if cats:
                jobs.append((c, cats))
        skipped: List[int] = []
        failed: List[int] = []

        def one(job):
            c, cats = job
            try:
                return c, self.search_one(c, query, cats, limit, interactive, imdb_id, tmdb_id, media_type,
                                          refresh=refresh)
            except TrackerSkipped as e:
                skipped.append(c['id'])
                if statuses is not None:
                    statuses.append({'id': c['id'], 'name': c['name'], 'status': search_status(e)})
            except TrackerError as e:
                failed.append(c['id'])
                if statuses is not None:
                    statuses.append({'id': c['id'], 'name': c['name'], 'status': search_status(e)})
            return c, []

        items: List[Dict[str, Any]] = []
        if jobs:
            with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
                for c, results in pool.map(one, jobs):
                    sv = _secret_values(c)
                    kept = 0
                    for r in results:
                        it = self.to_item(c, r, sv)
                        if it:
                            items.append(it)
                            kept += 1
                    if results and not kept:
                        # Every row was dropped: never serve that answer again from the cache.
                        logger.warning('Tracker %s (id %s) search: %d results, 0 kept', c['name'], c['id'],
                                       len(results))
                        with self._cache_lock:
                            for k in [k for k in self._cache if k[0] == c['id']]:
                                self._cache.pop(k, None)
            self.tokens.flush()
        prio = {c['id']: c['priority'] for c, _ in jobs}
        items.sort(key=lambda i: (prio.get(i['indexerId'], 99), -int(i.get('seeders') or 0)))
        return items, [c['id'] for c, _ in jobs], skipped + failed


class LumaTrackersClient:
    """The search client: Luma's own trackers (the only one since v172)."""

    def __init__(self, search: Optional[TrackerSearch] = None):
        self.engine = search or get_search()

    def test_connection(self) -> bool:
        return bool(self.engine.store.enabled_configs())

    def get_indexers(self) -> List[Dict[str, Any]]:
        return sorted(({'id': c['id'], 'name': c['name'], 'enabled': True}
                       for c in self.engine.store.enabled_configs()), key=lambda i: i['name'].lower())

    def _search_single(self, query: str, categories: str, limit: int,
                       indexer_ids: Optional[List[int]] = None) -> List[dict]:
        from app.indexer_api import IndexerSkipped, _expand_movie_categories
        items, searched, skipped = self.engine.search(query, _expand_movie_categories(categories), limit,
                                                      interactive=False, tracker_ids=indexer_ids)
        if indexer_ids and searched and set(searched) <= set(skipped):
            # Pinned callers must see "couldn't search", not "no results".
            raise IndexerSkipped(f'all requested trackers {skipped} skipped or failed')
        return items

    @staticmethod
    def _parse_items(raw_items: list) -> List[Dict]:
        from app.indexer_api import parse_items
        return parse_items(raw_items)

    def search_movies(self, categories: str = '2000', limit: int = 300) -> List[Dict]:
        try:
            return self._parse_items(self._search_single('', categories, limit)[:limit])
        except Exception as e:
            logger.error('Tracker browse failed: %s', type(e).__name__)
            return []

    def sweep_movies(self, categories: str = '2000', queries: Optional[List[str]] = None,
                     max_workers: int = 3) -> List[Dict]:
        from app.indexer_api import SWEEP_QUERIES
        seen, raw = set(), []
        for q in (queries if queries is not None else list(SWEEP_QUERIES)):
            try:
                items = self._search_single(q, categories, 500)
            except Exception:
                continue
            for it in items:
                key = (it.get('indexerId'), it.get('guid'))
                if key not in seen:
                    seen.add(key)
                    raw.append(it)
        return self._parse_items(raw)

    supports_ids = True

    def search_all(self, query: str, media_type: str, year: Optional[int] = None,
                   tmdb_id: Optional[int] = None, refresh: bool = False,
                   statuses: Optional[List[Dict[str, Any]]] = None) -> List[Dict]:
        from app.indexer_api import (MOVIE_CATEGORIES_EXPANDED, TV_SPORTS_CATEGORIES,
                                     _build_search_query, _parse_title)
        # TV release names carry SxxEyy, not the first-air year: never add it for TV.
        q = (query or '').strip() if media_type == 'tv' else _build_search_query(query, year)
        cats = list(MOVIE_CATEGORIES_EXPANDED) + list(TV_SPORTS_CATEGORIES)
        tmdb = str(tmdb_id) if tmdb_id else ''
        st: List[Dict[str, Any]] = []
        items, _, _ = self.engine.search(q, cats, 500, interactive=True, tmdb_id=tmdb, media_type=media_type,
                                         refresh=refresh, statuses=st)
        bare = (query or '').strip()
        if not items and bare and q != bare:
            # Release names often leave the year out: one retry without it. BHD already
            # tried the title without the year (and the TMDB id) in the same budget unit,
            # so it is not asked again.
            again = [c['id'] for c in self.engine.store.enabled_configs()
                     if not tt.searches_by_id(c.get('type'))]
            if again:
                logger.info('Tracker search_all media_type=%s: no results with the year, retrying without it',
                            media_type)
                st2: List[Dict[str, Any]] = []
                items, _, _ = self.engine.search(bare, cats, 500, interactive=True, tracker_ids=again,
                                                 tmdb_id=tmdb, media_type=media_type,
                                                 refresh=refresh, statuses=st2)
                # Retried trackers report their second answer; BHD keeps its first.
                st = [x for x in st if x['id'] not in set(again)] + st2
        if statuses is not None:
            statuses.extend(sorted(st, key=lambda x: x['name'].lower()))
        out = []
        for it in items:
            parsed = _parse_title(it['title'])
            out.append({
                'title': it['title'], 'clean_title': parsed['clean_title'], 'year': parsed['year'] or '',
                'resolution': parsed['resolution'] or '', 'source': parsed['source'] or '',
                'seeders': int(it.get('seeders') or 0), 'leechers': int(it.get('leechers') or 0),
                'size': it.get('size') or 0, 'indexer': it['indexer'], 'tracker': it['indexer'],
                'indexer_id': it['indexerId'], 'pub_date': it.get('publishDate') or '',
                'download_url': it['downloadUrl'], 'info_hash': it.get('infoHash') or '',
                'guid': it['guid'], 'protocol': it.get('protocol') or 'torrent',
                'imdb_id': it.get('imdbId') or '', 'freeleech': it.get('freeleech', False),
                'tracker_type': it.get('tracker_type') or '',
            })
        logger.info('Tracker search_all media_type=%s: %d results', media_type, len(out))
        return out


_search: Optional[TrackerSearch] = None
_search_lock = threading.Lock()


def get_search() -> TrackerSearch:
    global _search
    with _search_lock:
        if _search is None:
            _search = TrackerSearch()
        return _search


def reset_search_for_tests(s: Optional[TrackerSearch] = None) -> None:
    global _search
    with _search_lock:
        _search = s


def on_tracker_changed(rec: Dict[str, Any], url_changed: bool = False) -> None:
    """After an edit: forget credential/failure backoffs (never rate limits
    or bans) and cached answers of that tracker. Links issued under an older
    URL are dropped (credentials stay with the URL they were saved with)."""
    s = get_search()
    s.guard.clear(guard_key(rec))
    if url_changed:
        s.tokens.forget_tracker(int(rec['id']))
    with s._cache_lock:
        for k in [k for k in s._cache if k[0] == rec['id']]:
            s._cache.pop(k, None)


def on_tracker_deleted(rec: Dict[str, Any]) -> None:
    """After a delete: drop the tracker's release links and cached answers.
    Guard state belongs to the site, not the record, so it stays (re-adding
    the tracker can't reset a budget or a backoff)."""
    s = get_search()
    s.tokens.forget_tracker(int(rec['id']))
    with s._cache_lock:
        for k in [k for k in s._cache if k[0] == rec['id']]:
            s._cache.pop(k, None)


def has_enabled_trackers() -> bool:
    try:
        return any(r.get('enabled', True) for r in get_store().list())
    except Exception:
        return False


# ------------------------------------------------------------------ one-time migrations

BHD_ID_RESET_MARKER = '.migrated-bhd-id-backoff-v1'
# BHD's answer to a TMDB id it can't use, as a stored status message would
# carry it. Generic 'Unauthorized' (bad key, 401/403) never matches.
_ID_REJECTION_RE = __import__('re').compile(
    r'\b(tmdb|imdb)(_id)?\b.{0,40}\b(invalid|not valid|not found|rejected|unknown|malformed)\b'
    r'|\b(invalid|not valid|rejected|unknown|malformed)\b.{0,40}\b(tmdb|imdb)(_id)?\b', __import__('re').I)


def is_id_rejection(message: str) -> bool:
    return bool(_ID_REJECTION_RE.search(str(message or '')))


def reset_bhd_id_backoffs_once(store: Optional[TrackerStore] = None, guard: Optional[TrackerGuard] = None,
                               marker: Optional[Path] = None) -> int:
    """Once per install (marker file, created exclusively so one worker runs
    it): clear a BeyondHD 'auth' backoff that came from BHD rejecting a TMDB
    id (builds d02df0a/a534e21 sent ids BHD could refuse), identified only by
    a stored status message matching the id-rejection pattern. A real auth
    failure (bad key, 401/403: stored as 'Unauthorized') is never cleared.
    Returns how many trackers were reset."""
    import os
    from app.tracker_types import searches_by_id
    store = store or get_store()
    guard = guard or get_guard()
    if marker is None:
        from app.first_run import data_dir
        marker = Path(data_dir()) / BHD_ID_RESET_MARKER
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return 0
    os.close(fd)
    reset = 0
    for rec in store.list():
        if not searches_by_id(rec.get('type')):
            continue
        st = rec.get('status') or {}
        if st.get('state') != STATUS_ERROR or not is_id_rejection(st.get('message', '')):
            continue
        gk = guard_key(rec)
        cd = guard.blocked(gk)
        if cd and cd[1] == 'auth':
            guard.clear(gk, kinds=('auth',))
        store.set_status(rec['id'], 'untested', '')
        reset += 1
        logger.warning('Tracker %s (id %s): cleared a backoff from a rejected TMDB id (one-time reset)',
                       rec['name'], rec['id'])
    return reset
