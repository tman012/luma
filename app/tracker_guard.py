"""Rate limits and backoff for Luma's own trackers (app.tracker_store).

Counters, budgets and backoffs are keyed by the tracker's site, never by the
saved record id (see guard_key): known site types by type ('site:iptorrents',
so mirror domains share one budget), Torznab/Newznab by normalized host
('host:example.org'). Re-adding, editing, testing an unsaved form or using a
different path for the same site all hit the same persisted state.

Rules:

* user (interactive) searches and Tests: 40 per tracker per hour;
* background searches: token bucket, burst 5, one token per 180 s (20/h);
* AvistaZ family (AvistaZ, PrivateHD, CinemaZ, ExoticaZ): every request,
  user or background, goes through the bucket, so 20/h with a burst of 5;
* 300 per tracker per rolling 24 h, 120 per hour across all trackers;
* at least 2 s between requests to one tracker;
* backoff after a failure, longer on each consecutive failure:
  429/rate limit: Retry-After, else 1 h (doubling, max 6 h);
  ban: 24 h; rejected credentials: 1 h (cleared when the credentials change);
  network/timeouts/5xx: 5 min (doubling, max 1 h); bad answers: 15 min.

State persists in $LUMA_DATA_DIR/tracker_guard.json so a restart can't reset
the counters. An unreadable state file is moved aside and searches pause for
an hour (fail closed).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

LIMITS = {
    'interactive_per_hour': 40,
    'burst': 5,
    'refill_s': 180.0,
    'per_day': 300,
    'global_per_hour': 120,
    'min_interval_s': 2.0,
    'rate_s': 3600, 'rate_max_s': 6 * 3600,
    'banned_s': 24 * 3600,
    'auth_s': 3600, 'auth_max_s': 6 * 3600,
    'network_s': 300, 'network_max_s': 3600,
    'response_s': 900, 'response_max_s': 6 * 3600,
}
FAIL_CLOSED_S = 3600
MAX_COOLDOWN_S = 24 * 3600
FILENAME = 'tracker_guard.json'
_FAIL = 'fail_closed'


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if v == v and 0 <= v < 1e11 else None


class TrackerGuard:
    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else None
        self._lock = threading.RLock()
        self._loaded = False
        # Keys are guard_key() strings ('site:<type>' or 'host:<host>').
        self.log: Dict[str, List[list]] = {}          # key -> [[ts, 'i'|'b'], ...]
        self.buckets: Dict[str, List[float]] = {}     # key -> [tokens, ts]
        self.cooldown: Dict[str, Tuple[float, str]] = {}   # key -> (until, kind)
        self.streak: Dict[str, int] = {}
        self.fail_closed_until = 0.0
        self.last_sent: Dict[str, float] = {}
        self._tracker_locks: Dict[str, threading.Lock] = {}

    @property
    def path(self) -> Path:
        if self._path:
            return self._path
        from app.first_run import data_dir
        return Path(data_dir()) / FILENAME

    # ------------------------------------------------------------ persistence

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        from app.paths import quarantine
        try:
            with open(self.path, encoding='utf-8') as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict) or any(not isinstance(raw.get(k, {}), dict)
                                                for k in ('log', 'buckets', 'cooldown', 'streak')):
                raise ValueError('wrong shape')
        except FileNotFoundError:
            return
        except (OSError, ValueError, UnicodeDecodeError) as e:
            quarantine(self.path, f'unreadable: {type(e).__name__}', logger)
            self.fail_closed_until = time.time() + FAIL_CLOSED_S
            logger.error('Tracker guard state lost: tracker searches paused for %d min', FAIL_CLOSED_S // 60)
            self._save()
            return
        now = time.time()

        def key(k):
            # Only site/host keys; anything else (an older id-keyed file) is dropped.
            return k if isinstance(k, str) and _KEY_RE.match(k) else None

        for k, entries in raw.get('log', {}).items():
            i = key(k)
            if i is None or not isinstance(entries, list):
                continue
            self.log[i] = [[_num(e[0]), e[1]] for e in entries
                           if isinstance(e, list) and len(e) == 2 and e[1] in ('i', 'b')
                           and _num(e[0]) and _num(e[0]) > now - 86400]
        for k, v in raw.get('buckets', {}).items():
            i = key(k)
            if i is None:
                continue
            ok = isinstance(v, list) and len(v) == 2 and _num(v[0]) is not None and _num(v[1])
            self.buckets[i] = [min(float(v[0]), LIMITS['burst']), float(v[1])] if ok else [0.0, now]
        for k, v in raw.get('cooldown', {}).items():
            i = key(k)
            if i is None or not isinstance(v, list) or len(v) != 2 or not _num(v[0]) or not isinstance(v[1], str):
                continue
            self.cooldown[i] = (min(float(v[0]), now + MAX_COOLDOWN_S), v[1])
        for k, v in raw.get('streak', {}).items():
            i = key(k)
            if i is not None and isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 64:
                self.streak[i] = v
        fc = _num(raw.get(_FAIL))
        if fc:
            self.fail_closed_until = min(fc, now + FAIL_CLOSED_S)

    def _save(self) -> None:
        from app.paths import atomic_write_json
        state = {
            'log': {str(k): v for k, v in self.log.items() if v},
            'buckets': {str(k): v for k, v in self.buckets.items()},
            'cooldown': {str(k): [u, kind] for k, (u, kind) in self.cooldown.items()},
            'streak': {str(k): v for k, v in self.streak.items() if v},
            _FAIL: self.fail_closed_until,
        }
        try:
            atomic_write_json(self.path, state)
        except OSError as e:
            logger.warning('Tracker guard state not saved: %s', type(e).__name__)

    # ------------------------------------------------------------ decisions

    def tracker_lock(self, tid: str) -> threading.Lock:
        with self._lock:
            lk = self._tracker_locks.get(tid)
            if lk is None:
                lk = self._tracker_locks[tid] = threading.Lock()
            return lk

    def blocked(self, tid: str, now: Optional[float] = None) -> Optional[Tuple[float, str]]:
        """(until, kind) when *tid* is in backoff or the guard failed closed."""
        now = time.time() if now is None else now
        with self._lock:
            self._load()
            if self.fail_closed_until > now:
                return self.fail_closed_until, 'paused'
            cd = self.cooldown.get(tid)
            if cd and cd[0] > now:
                return cd
            return None

    def _tokens(self, tid: str, now: float) -> List[float]:
        b = self.buckets.get(tid)
        if b is None:
            b = self.buckets[tid] = [float(LIMITS['burst']), now]
        b[0] = min(float(LIMITS['burst']), b[0] + max(0.0, now - b[1]) / LIMITS['refill_s'])
        b[1] = now
        return b

    def counts(self, tid: str, now: float) -> Tuple[int, int, int]:
        day, hr = now - 86400, now - 3600
        for k in list(self.log):
            self.log[k] = [e for e in self.log[k] if e[0] > day]
        mine = self.log.get(tid, [])
        return (sum(1 for t, kind in mine if t > hr and kind == 'i'), len(mine),
                sum(1 for v in self.log.values() for t, _ in v if t > hr))

    def take(self, tid: str, interactive: bool, strict: bool, now: Optional[float] = None) -> Optional[str]:
        """None when a request to *tid* may be sent now (and records it), else
        the reason it may not."""
        now = time.time() if now is None else now
        with self._lock:
            self._load()
            if self.fail_closed_until > now:
                return 'paused'
            cd = self.cooldown.get(tid)
            if cd and cd[0] > now:
                return f'backoff ({cd[1]})'
            hr, day, glob = self.counts(tid, now)
            if day >= LIMITS['per_day']:
                return f"daily cap {LIMITS['per_day']}"
            if glob >= LIMITS['global_per_hour']:
                return f"global hourly cap {LIMITS['global_per_hour']}"
            if interactive and not strict:
                if hr >= LIMITS['interactive_per_hour']:
                    return f"hourly cap {LIMITS['interactive_per_hour']}"
            else:
                b = self._tokens(tid, now)
                if b[0] < 1.0:
                    return 'rate limit (bucket empty)'
                b[0] -= 1.0
            self.log.setdefault(tid, []).append([now, 'i' if interactive else 'b'])
            self._save()
            return None

    def wait_spacing(self, tid: str) -> None:
        """Sleep so requests to one tracker are at least min_interval_s apart
        (call while holding tracker_lock(tid))."""
        wait = LIMITS['min_interval_s'] - (time.time() - self.last_sent.get(tid, 0.0))
        if wait > 0:
            time.sleep(wait)
        self.last_sent[tid] = time.time()

    def success(self, tid: str) -> None:
        with self._lock:
            self._load()
            changed = bool(self.streak.pop(tid, 0)) or tid in self.cooldown
            self.cooldown.pop(tid, None)
            if changed:
                self._save()

    def failure(self, tid: str, kind: str, retry_after: Optional[float] = None,
                now: Optional[float] = None) -> float:
        """Start a backoff for *tid* after a failed request; returns its end."""
        now = time.time() if now is None else now
        with self._lock:
            self._load()
            n = self.streak.get(tid, 0)
            self.streak[tid] = n + 1
            if kind == 'banned':
                secs = LIMITS['banned_s']
            elif kind == 'rate':
                secs = retry_after if retry_after else min(LIMITS['rate_s'] * 2 ** min(n, 16), LIMITS['rate_max_s'])
            elif kind == 'auth':
                secs = min(LIMITS['auth_s'] * 2 ** min(n, 16), LIMITS['auth_max_s'])
            elif kind in ('timeout', 'network', 'tls', 'http'):
                secs = min(LIMITS['network_s'] * 2 ** min(n, 16), LIMITS['network_max_s'])
            else:
                secs = min(LIMITS['response_s'] * 2 ** min(n, 16), LIMITS['response_max_s'])
            if retry_after and kind != 'banned':
                secs = max(secs, retry_after)
            until = now + min(secs, MAX_COOLDOWN_S)
            old = self.cooldown.get(tid)
            if old and old[0] > until:
                until = old[0]
            self.cooldown[tid] = (until, kind)
            self._save()
            return until

    def clear(self, tid: str, kinds: Tuple[str, ...] = ('auth', 'response', 'timeout', 'network', 'tls', 'http')) -> None:
        """Forget a backoff of the given kinds (credentials or URL changed).
        Rate limits and bans are never cleared early."""
        with self._lock:
            self._load()
            cd = self.cooldown.get(tid)
            if cd and cd[1] in kinds:
                self.cooldown.pop(tid, None)
                self.streak.pop(tid, None)
                self._save()



_guard: Optional[TrackerGuard] = None
_guard_lock = threading.Lock()


def get_guard() -> TrackerGuard:
    global _guard
    with _guard_lock:
        if _guard is None:
            _guard = TrackerGuard()
        return _guard


def reset_guard_for_tests(guard: Optional[TrackerGuard] = None) -> None:
    global _guard
    with _guard_lock:
        _guard = guard


# ------------------------------------------------------------ keys and Test

# Host part: a DNS name in ASCII form, or an IP literal (IPv6 has colons).
_KEY_RE = __import__('re').compile(r'^(site|host):[a-z0-9._:-]{1,253}$')


def normalize_host(url: str) -> str:
    """Lowercase host name without a trailing dot or a leading 'www.' (IDNA
    names in their ASCII form; IP literals in their compressed form, IPv6
    without brackets); scheme, port, path and query are ignored."""
    try:
        host = (urlsplit(str(url or '').strip()).hostname or '').lower().rstrip('.')
    except ValueError:
        return ''
    try:
        import ipaddress
        return str(ipaddress.ip_address(host.split('%')[0]))      # one spelling per IP literal
    except ValueError:
        pass
    try:
        host = host.encode('idna').decode('ascii')
    except UnicodeError:
        pass
    if host.startswith('www.'):
        host = host[4:]
    return host


def guard_key(cfg: Dict) -> str:
    """Guard key for a tracker config, saved or not. Known site types count
    by type (every mirror domain of a site shares one budget); Torznab and
    Newznab count by normalized host. The saved record id never matters, so
    deleting and re-adding, editing, an unsaved Test form or another path on
    the same host can't reset a budget or a backoff."""
    from app import tracker_types as tt
    type_id = str(cfg.get('type') or '').strip().lower()
    if tt.get_type(type_id) is not None and not tt.is_generic(type_id):
        return f'site:{type_id}'
    return f'host:{normalize_host(cfg.get("url")) or "-"}'


def guarded_test(cfg: Dict, run, guard: Optional[TrackerGuard] = None) -> None:
    """Run a connection test through the same limiter as searches.

    A Test counts as a user request against the per-tracker hourly and daily
    caps and the global hourly cap (AvistaZ family: the strict bucket). A
    rate-limit or ban backoff, or a failed-closed guard, refuses the Test
    without contacting the tracker; other backoffs (rejected credentials,
    network) are cleared since the owner may just have fixed them. A 429 or
    ban answer starts the persisted backoff, honoring Retry-After.
    Raises TrackerError; returns None when the tracker answered correctly."""
    from app import tracker_types as tt
    from app.tracker_http import TrackerError
    g = guard or get_guard()
    gk = guard_key(cfg)
    strict = tt.is_rate_strict(cfg.get('type'))
    blocked = g.blocked(gk)
    if blocked and blocked[1] in ('rate', 'banned', 'paused'):
        raise TrackerError('banned' if blocked[1] == 'banned' else 'rate')
    if blocked:
        g.clear(gk)
    if g.take(gk, True, strict):
        raise TrackerError('rate')
    with g.tracker_lock(gk):
        g.wait_spacing(gk)
        try:
            run(cfg)
        except TrackerError as e:
            if e.kind in ('rate', 'banned'):
                g.failure(gk, e.kind, e.retry_after)
            raise
    g.success(gk)
