"""Rate limits and backoff for Luma's own trackers (app.tracker_store).

Counters, budgets and backoffs are keyed by the tracker's site, never by the
saved record id (see guard_key): known site types by type ('site:iptorrents',
so mirror domains share one budget), Torznab/Newznab by normalized host
('host:example.org'). Re-adding, editing, testing an unsaved form or using a
different path for the same site all hit the same persisted state.

Torznab/Newznab rate limits and bans also cover the server's resolved IP
('ip:203.0.113.7'): another hostname or the IP literal of a backed-off
server is refused too (see address_scope). Site types are keyed by type
already, and a CDN address shared with other sites never blocks them.

Rules:

* user (interactive) searches and Tests: 40 per tracker per hour;
* background searches: token bucket, burst 5, one token per 180 s (20/h);
* AvistaZ family (AvistaZ, PrivateHD, PixelHD, CinemaZ, ExoticaZ): every request,
  user or background, goes through the bucket, so 20/h with a burst of 5;
* 300 per tracker per rolling 24 h, 120 per hour across all trackers;
* at least 2 s between requests to one tracker;
* backoff after a failure, longer on each consecutive failure:
  429/rate limit: 1 h (doubling, max 6 h), or its Retry-After when longer (v170: never shorter);
  400 from a strict site (AvistaZ family, IPTorrents; tracker_types.STRICT_400_TYPES):
  the same as a rate limit (v169, ported from the Prowlarr path's strict 400 cooldown);
  400 from any other tracker: 15 min (doubling, max 6 h), as on the Prowlarr path (v170);
  ban: 24 h; rejected credentials: 1 h (cleared when the credentials change);
  network/timeouts/5xx: 5 min (doubling, max 1 h); bad answers: 15 min.

Purpose budgets: a background request may name a purpose ('tv'); it is then
also held to that purpose's caps (PURPOSE_LIMITS: per tracker per day and per
hour, across trackers per day, and a minimum gap per tracker), on top of all
the limits above. The log records it as 'b:<purpose>'.

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
    # v170 (PM): a 400 from a non-strict tracker backs off as on the Prowlarr path (GUARD_LIMITS
    # cooldown_400_s / cooldown_max_s): 15 min doubling to 6 h, or the native http backoff if longer.
    'http400_s': 900, 'http400_max_s': 6 * 3600,
}
# Per-purpose caps for background requests, on top of LIMITS. The TV caps are
# fixed in code (app.tv_budget), not settings.
def _purpose_limits() -> Dict[str, Dict[str, float]]:
    from app import tv_budget
    return {tv_budget.PURPOSE: tv_budget.GUARD_LIMITS}


FEED_PURPOSE = 'feed'
_PURPOSE_RE = __import__('re').compile(r'^[a-z]{1,16}$')
_KIND_RE = __import__('re').compile(r'^[ib](:[a-z]{1,16})?$')
# Rollback safety: `log` keeps only [ts, 'i'|'b'] pairs, the shape every
# earlier guard reads, so a rolled-back build still counts TV's requests
# against its per-tracker and global caps. Which purpose a background request
# was for is kept beside it in `purposes` ({key: [[ts, purpose], ...]}), which
# an earlier guard ignores (and drops on its next save; only the TV caps forget
# that day's count, and those caps are a subset of the shared ones). Files
# written by 78d7f1e with [ts, 'b:tv'] in `log` are read as both.
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
        self.purposes: Dict[str, List[list]] = {}     # key -> [[ts, purpose], ...] (background only)
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
            kept = [[_num(e[0]), e[1]] for e in entries
                    if isinstance(e, list) and len(e) == 2 and isinstance(e[1], str)
                    and _KIND_RE.match(e[1])
                    and _num(e[0]) and _num(e[0]) > now - 86400]
            self.log[i] = [[t, k[:1]] for t, k in kept]
            legacy = [[t, k[2:]] for t, k in kept if k.startswith('b:')]
            if legacy:
                self.purposes.setdefault(i, []).extend(legacy)
        purposes = raw.get('purposes')
        for k, entries in (purposes.items() if isinstance(purposes, dict) else ()):
            i = key(k)
            if i is None or not isinstance(entries, list):
                continue
            self.purposes.setdefault(i, []).extend(
                [_num(e[0]), e[1]] for e in entries
                if isinstance(e, list) and len(e) == 2 and isinstance(e[1], str) and _PURPOSE_RE.match(e[1])
                and _num(e[0]) and _num(e[0]) > now - 86400)
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
            'purposes': {str(k): v for k, v in self.purposes.items() if v},
            'buckets': {str(k): v for k, v in self.buckets.items()},
            'cooldown': {str(k): [u, kind] for k, (u, kind) in self.cooldown.items()},
            'streak': {str(k): v for k, v in self.streak.items() if v},
            _FAIL: self.fail_closed_until,
        }
        try:
            atomic_write_json(self.path, state, mode=0o600)   # lists tracker hosts
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
        for k in list(self.purposes):
            self.purposes[k] = [e for e in self.purposes[k] if e[0] > day]
        mine = self.log.get(tid, [])
        return (sum(1 for t, kind in mine if t > hr and kind[:1] == 'i'), len(mine),
                sum(1 for v in self.log.values() for t, _ in v if t > hr))

    def purpose_counts(self, tid: str, purpose: str, now: Optional[float] = None) -> Dict[str, float]:
        """{'hour', 'day', 'all_day', 'last'} for *purpose*: this tracker's
        requests in the last hour and 24 h, every tracker's in 24 h, and the
        time of this tracker's latest (0 when none)."""
        now = time.time() if now is None else now
        with self._lock:
            self._load()
            day, hr = now - 86400, now - 3600
            mine = [t for t, k in self.purposes.get(tid, []) if k == purpose and t > day]
            return {'hour': sum(1 for t in mine if t > hr), 'day': len(mine),
                    'all_day': sum(1 for v in self.purposes.values() for t, k in v if k == purpose and t > day),
                    'last': max(mine, default=0.0)}

    def _purpose_refusal(self, tid: str, purpose: str, now: float, spacing: bool = True) -> Optional[str]:
        lim = _purpose_limits().get(purpose)
        if lim is None:
            return f'unknown purpose {purpose}'
        c = self.purpose_counts(tid, purpose, now)
        if c['day'] >= lim['per_tracker_day']:
            return f"{purpose} daily cap {int(lim['per_tracker_day'])}"
        if c['hour'] >= lim['per_tracker_hour']:
            return f"{purpose} hourly cap {int(lim['per_tracker_hour'])}"
        if c['all_day'] >= lim['all_trackers_day']:
            return f"{purpose} daily cap across trackers {int(lim['all_trackers_day'])}"
        if spacing and c['last'] and now - c['last'] < lim['min_gap_s']:
            return f'{purpose} spacing'
        return None

    def take(self, tid: str, interactive: bool, strict: bool, now: Optional[float] = None,
             purpose: Optional[str] = None, spacing: bool = True) -> Optional[str]:
        """None when a request to *tid* may be sent now (and records it), else
        the reason it may not. A background request with a *purpose* is also
        held to that purpose's caps; an interactive one is charged to the
        interactive budget as before. spacing=False (a download right after its search) skips
        only the purpose's minimum gap; every cap still holds."""
        now = time.time() if now is None else now
        if purpose is not None and (interactive or not isinstance(purpose, str) or not _PURPOSE_RE.match(purpose)):
            purpose = None if interactive else '?'
        if purpose == '?':
            return 'invalid purpose'
        with self._lock:
            self._load()
            if self.fail_closed_until > now:
                return 'paused'
            cd = self.cooldown.get(tid)
            if cd and cd[0] > now:
                return f'backoff ({cd[1]})'
            if purpose == FEED_PURPOSE:
                # v185.2a (PM, Tester BLOCK): a TV feed poll (Sonarr-style 15-min RSS) consumes no cap, slot or
                # bucket and is never recorded; only the pause and the tracker's backoff / 429 Retry-After stop it
                return None
            hr, day, glob = self.counts(tid, now)
            if purpose:
                why = self._purpose_refusal(tid, purpose, now, spacing)
                if why:
                    return why
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
            if purpose:
                self.purposes.setdefault(tid, []).append([now, purpose])
            self._save()
            return None

    def note_purpose(self, tid: str, purpose: str, now: Optional[float] = None) -> None:
        """v173 (PM, Tester FF-6): count a request toward *purpose*'s tally without holding it to that
        purpose's caps. A manual grab on a capped tracker counts toward the automation day (8/day),
        so automation backs off; the manual grab itself is never refused by that cap (it went through
        take() as interactive: rate limits and backoff still apply)."""
        if not isinstance(purpose, str) or not _PURPOSE_RE.match(purpose):
            return
        now = time.time() if now is None else now
        with self._lock:
            self._load()
            self.purposes.setdefault(tid, []).append([now, purpose])
            self._save()

    def wait_spacing(self, tid: str) -> None:
        """Sleep so requests to one tracker are at least min_interval_s apart
        (call while holding tracker_lock(tid))."""
        wait = min(LIMITS['min_interval_s'],             # v169: a clock step back never parks it longer
                   LIMITS['min_interval_s'] - (time.time() - self.last_sent.get(tid, 0.0)))
        if wait > 0:
            time.sleep(wait)
        self.last_sent[tid] = time.time()

    def fold(self, tid: str, log: List[list], bucket: Optional[List[float]], cooldown_until: float,
             streak: int, now: Optional[float] = None) -> Dict[str, float]:
        """v170 (Import from Prowlarr): fold the Prowlarr path's guard state for one indexer into *tid*,
        taking the stricter value everywhere, so switching paths can't reset a budget:
        sends are the union (deduplicated by time, so folding twice changes nothing), the bucket the
        emptier one, the cooldown the later one, the streak the longer one. Returns what changed."""
        now = time.time() if now is None else now
        with self._lock:
            self._load()
            mine = self.log.setdefault(tid, [])
            seen = {(round(float(t), 3), k) for t, k in mine}
            added = 0
            for t, k in log:
                key = (round(float(t), 3), k if k in ('i', 'b') else 'b')
                if t > now - 86400 and key not in seen:
                    mine.append([float(t), key[1]])
                    seen.add(key)
                    added += 1
            mine.sort(key=lambda e: e[0])
            out = {'sends': added}
            if bucket is not None:
                theirs = min(float(LIMITS['burst']), float(bucket[0]) + max(0.0, now - float(bucket[1])) / LIMITS['refill_s'])
                b = self._tokens(tid, now)
                if theirs < b[0]:
                    out['bucket'] = theirs
                    b[0] = max(0.0, theirs)
            if cooldown_until and cooldown_until > now:
                cd = self.cooldown.get(tid)
                if not cd or cd[0] < cooldown_until:
                    self.cooldown[tid] = (min(float(cooldown_until), now + MAX_COOLDOWN_S), 'rate')
                    out['cooldown'] = self.cooldown[tid][0]
            if streak and streak > self.streak.get(tid, 0):
                self.streak[tid] = int(streak)
                out['streak'] = streak
            self._save()
            return out

    def success(self, tid: str) -> None:
        with self._lock:
            self._load()
            changed = bool(self.streak.pop(tid, 0)) or tid in self.cooldown
            self.cooldown.pop(tid, None)
            if changed:
                self._save()

    def failure(self, tid: str, kind: str, retry_after: Optional[float] = None,
                now: Optional[float] = None, status: Optional[int] = None) -> float:
        """Start a backoff for *tid* after a failed request; returns its end."""
        now = time.time() if now is None else now
        with self._lock:
            self._load()
            n = self.streak.get(tid, 0)
            self.streak[tid] = n + 1
            if kind == 'banned':
                secs = LIMITS['banned_s']
            elif kind == 'rate':
                # v170 (Reviewer): the schedule is a floor; a longer Retry-After wins below, a shorter one can't shrink it
                secs = min(LIMITS['rate_s'] * 2 ** min(n, 16), LIMITS['rate_max_s'])
            elif kind == 'auth':
                secs = min(LIMITS['auth_s'] * 2 ** min(n, 16), LIMITS['auth_max_s'])
            elif kind in ('timeout', 'network', 'tls', 'http'):
                secs = min(LIMITS['network_s'] * 2 ** min(n, 16), LIMITS['network_max_s'])
                if kind == 'http' and status == 400:          # v170: the stricter value wins
                    secs = max(secs, min(LIMITS['http400_s'] * 2 ** min(n, 16), LIMITS['http400_max_s']))
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
_KEY_RE = __import__('re').compile(r'^(site|host|ip):[a-z0-9._:-]{1,253}$')


def canonical_ip(value: str) -> str:
    """One spelling per address: compressed form, no zone, and an IPv4
    address wrapped in IPv6 (::ffff:a.b.c.d mapped, ::a.b.c.d compatible)
    as plain IPv4, so a mapped spelling can't dodge a backoff.
    Raises ValueError for a non-address."""
    import ipaddress
    ip = ipaddress.ip_address(str(value).strip().strip('[]').split('%')[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        if int(ip) >> 32 == 0 and int(ip) >= 1 << 24:
            return str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return str(ip)


def normalize_host(url: str) -> str:
    """Lowercase host name without a trailing dot or a leading 'www.' (IDNA
    names in their ASCII form; IP literals in their compressed form, IPv6
    without brackets); scheme, port, path and query are ignored."""
    try:
        host = (urlsplit(str(url or '').strip()).hostname or '').lower().rstrip('.')
    except ValueError:
        return ''
    try:
        return canonical_ip(host)      # one spelling per IP literal
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


_IP_KINDS = ('rate', 'banned')


def ip_key(address: str) -> str:
    return f'ip:{canonical_ip(address)}'


def address_scope(cfg: Dict, guard: Optional[TrackerGuard] = None):
    """An AddressScope for one request to *cfg*'s tracker. For Torznab and
    Newznab it refuses (TrackerError, nothing sent) any resolved address in
    a rate-limit or ban backoff; record_failure then extends a new one to
    the addresses contacted. Site types get a plain scope."""
    from app import tracker_types as tt
    from app.tracker_http import AddressScope, TrackerError
    g = guard or get_guard()
    if not tt.is_generic(str(cfg.get('type') or '').strip().lower()):
        return AddressScope()

    def gate(address: str) -> None:
        cd = g.blocked(ip_key(address))
        if cd and cd[1] in _IP_KINDS:
            raise TrackerError(cd[1])
    scope = AddressScope(gate)
    scope.by_ip = True
    return scope


def strict_400(gk: str, err) -> bool:
    """A 400 answer from a strict site (AvistaZ family, IPTorrents): backed off as a rate limit."""
    from app import tracker_types as tt
    return (getattr(err, 'status', None) == 400 and getattr(err, 'kind', '') == 'http'
            and gk.startswith('site:') and tt.is_strict_400(gk[5:]))


def record_failure(g: TrackerGuard, gk: str, err, scope=None) -> float:
    """Start the site's backoff after *err*; a rate limit or ban on a
    Torznab/Newznab server also covers the addresses it was reached at. A 400
    from a strict site starts a rate backoff (1 h doubling to 6 h, or its
    Retry-After when longer), so the next request waits as on the Prowlarr path."""
    kind = 'rate' if strict_400(gk, err) else err.kind
    until = g.failure(gk, kind, err.retry_after, status=getattr(err, 'status', None))
    if scope is not None and getattr(scope, 'by_ip', False) and err.kind in _IP_KINDS:
        for a in scope.addresses:
            g.failure(ip_key(a), err.kind, err.retry_after)
    return until


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
        scope = address_scope(cfg, g)
        try:
            with scope:
                run(cfg)
        except TrackerError as e:
            if e.kind in ('rate', 'banned') or strict_400(gk, e):
                record_failure(g, gk, e, scope)
            raise
    g.success(gk)
