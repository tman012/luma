"""TV's share of the tracker budget (automatic searches only).

Every automatic TV request (targeted episode and season searches; the recent-releases feed uses
the guard's 'feed' purpose) goes through tracker_guard.take(..., purpose='tv'). v204a (PM, Taylor):
Luma sets no daily or hourly per-tracker cap of its own. A request is held only by the guard's
limits (burst bucket, 120/h across trackers, spacing) and by backoff when a tracker signals it
(429, rate limit, network error), as Prowlarr does. Manual searches from TV Sources are charged
to the guard's interactive budget instead.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PURPOSE = 'tv'

# v204a (PM, Taylor): no Luma-side daily/hourly per-tracker cap on TV or movie searches and grabs.
# Luma searches on Sonarr's cadence and backs off only when a tracker says so (429, rate limit,
# network error) through the guard's backoff, as Prowlarr does.
AUTO_PER_TRACKER_DAILY = None
AUTO_MIN_GAP_S = 60             # v202a: 1 min between automatic TV requests to one tracker (was 10 min)

GUARD_LIMITS: Dict[str, float] = {
    'min_gap_s': AUTO_MIN_GAP_S,
}


SLOT_S = AUTO_MIN_GAP_S


def capped(cfg: Dict[str, Any]) -> bool:
    """Site trackers (shared accounts with prod) carry the TV cap; generic Torznab/Newznab
    indexers don't."""
    from app import tracker_types as tt
    return not tt.is_generic(cfg.get('type'))


def slot_free(cfg: Dict[str, Any], now: Optional[float] = None, guard=None) -> bool:
    """A site tracker may take an automatic TV request: AUTO_MIN_GAP_S since its last one (v204a: no
    daily cap). Always True for a generic indexer."""
    if not capped(cfg):
        return True
    from app.tracker_guard import guard_key
    g = guard or _guard()
    now = time.time() if now is None else now
    c = g.purpose_counts(guard_key(cfg), PURPOSE, now)
    return not c['last'] or now - c['last'] >= SLOT_S


def under_daily(cfg: Dict[str, Any], now: Optional[float] = None, guard=None) -> bool:
    """v204a (PM, Taylor): no daily cap, so always True (kept for callers)."""
    return True                 # v204a: no daily cap


def daily_reset(cfg: Dict[str, Any], now: Optional[float] = None, guard=None) -> Optional[float]:
    """v204a (PM, Taylor): no daily cap, so never a reset time (kept for callers). Always None."""
    return None                 # v204a: no daily cap


def next_slot(cfg: Dict[str, Any], now: Optional[float] = None, guard=None) -> Optional[float]:
    """When a site tracker may take its next automatic TV request (spacing only; None: generic indexer)."""
    if not capped(cfg):
        return None
    from app.tracker_guard import guard_key
    g = guard or _guard()
    now = time.time() if now is None else now
    c = g.purpose_counts(guard_key(cfg), PURPOSE, now)
    return max(now, (c['last'] or 0) + SLOT_S)


def _guard():
    from app.tracker_guard import get_guard
    return get_guard()


def take(cfg: Dict[str, Any], now: Optional[float] = None, guard=None) -> Optional[str]:
    """None when an automatic TV request to this tracker may go now (and it is
    counted), else why not."""
    from app import tracker_types as tt
    from app.tracker_guard import guard_key
    g = guard or _guard()
    strict = bool(tt.is_rate_strict(cfg.get('type')))
    return g.take(guard_key(cfg), interactive=False, strict=strict, now=now, purpose=PURPOSE)


def usage(cfg: Dict[str, Any], now: Optional[float] = None, guard=None) -> Dict[str, int]:
    """{'used', 'cap'}: automatic TV requests to this tracker in the last 24 h."""
    from app.tracker_guard import guard_key
    g = guard or _guard()
    c = g.purpose_counts(guard_key(cfg), PURPOSE, time.time() if now is None else now)
    return {'used': int(c['day']), 'cap': None}


# ------------------------------------------------------------------ v251: daily background search cap
# Every background TV search query (air-time and 15-min searches, the hourly catch-up, retries and each
# title/alias fallback) counts against a per-tracker cap per local day (America/New_York). Feed polls,
# the owner's searches (interactive or a monitor click's background lane) and grabs are not counted.
# At the cap the tracker is skipped until midnight ET. The cap is the TV setting
# automation.daily_search_cap (tv_settings, default DAILY_SEARCH_CAP).
DAILY_SEARCH_CAP = 50
ET = ZoneInfo('America/New_York')


def _et_day(now: float) -> str:
    return dt.datetime.fromtimestamp(now, ET).date().isoformat()


def search_cap() -> int:
    from app import tv_settings
    try:
        return int(tv_settings.load()['automation'].get('daily_search_cap', DAILY_SEARCH_CAP))
    except Exception:                       # noqa: BLE001 - unreadable settings: the default
        return DAILY_SEARCH_CAP


def _count_key(cfg: Dict[str, Any]) -> str:
    from app.tracker_guard import guard_key
    return f'tv_searchday:{guard_key(cfg)}'


def searches_today(cfg: Dict[str, Any], now: Optional[float] = None) -> int:
    from app import tv_store
    now = time.time() if now is None else now
    try:
        d = json.loads(tv_store.get_meta(_count_key(cfg)) or '{}')
    except ValueError:
        return 0
    return int(d.get('n') or 0) if d.get('day') == _et_day(now) else 0


def under_search_cap(cfg: Dict[str, Any], now: Optional[float] = None) -> bool:
    return searches_today(cfg, now) < search_cap()


def search_cap_reset(now: float) -> float:
    """The next midnight ET (when the count starts over)."""
    d = dt.datetime.fromtimestamp(now, ET).date() + dt.timedelta(days=1)
    return dt.datetime(d.year, d.month, d.day, tzinfo=ET).timestamp()


def count_search(cfg: Dict[str, Any], now: Optional[float] = None) -> int:
    """One background query sent to *cfg* today; the new count. Logs once (INFO) when it reaches the cap."""
    from app import tv_store
    now = time.time() if now is None else now
    n = searches_today(cfg, now) + 1
    tv_store.set_meta(_count_key(cfg), json.dumps({'day': _et_day(now), 'n': n}))
    cap = search_cap()
    if n == cap:
        logger.info('TV search: %s reached its daily cap of %d background searches; skipped until midnight ET',
                    cfg.get('name') or cfg.get('id'), cap)
    return n
