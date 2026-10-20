"""TV's share of the tracker budget (automatic searches only).

Every automatic TV request (the recent-releases feed, targeted episode and
season searches, backlog) goes through tracker_guard.take(..., purpose='tv')
and is held to these caps on top of the guard's own limits (300/day per
tracker, the background bucket, 120/h across trackers, backoff). Manual
searches from TV Sources are interactive and are charged to the guard's
interactive budget instead.

The caps are code constants, not settings. While production's Sonarr and
Prowlarr use the same tracker accounts from the same IP, the test install
(:5002) makes at most AUTO_PER_TRACKER_DAILY automatic requests per tracker
per day.

v158c (PM): the cap is not raised. A capped tracker (a site tracker; capped()) gets
AUTO_PER_TRACKER_DAILY slots a day, one every SLOT_S (spread evenly): the feed polls in a free
slot, and a post-air (or other automatic) search may take a slot when one is free; either way the
next slot is SLOT_S later. An indexer with no cap (a generic Torznab/Newznab entry) is polled every
15 min and searched freely, held only to the guard's own limits (background bucket, 300/day).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

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
    """A capped tracker has a free slot: under the daily cap and SLOT_S since its last automatic
    TV request. Always True for an indexer with no cap."""
    if not capped(cfg):
        return True
    from app.tracker_guard import guard_key
    g = guard or _guard()
    now = time.time() if now is None else now
    c = g.purpose_counts(guard_key(cfg), PURPOSE, now)
    return not c['last'] or now - c['last'] >= SLOT_S


def under_daily(cfg: Dict[str, Any], now: Optional[float] = None, guard=None) -> bool:
    """v196c (Taylor): a post-air episode search needs only the tracker's daily cap, not a 3 h slot."""
    return True                 # v204a: no daily cap


def daily_reset(cfg: Dict[str, Any], now: Optional[float] = None, guard=None) -> Optional[float]:
    """v201 (PM): when a capped tracker that's at its daily cap gets a request back (the rolling 24 h window
    drops enough of today's automatic TV requests to go under AUTO_PER_TRACKER_DAILY). None: no cap, or under it."""
    return None                 # v204a: no daily cap


def next_slot(cfg: Dict[str, Any], now: Optional[float] = None, guard=None) -> Optional[float]:
    """When a capped tracker's next slot opens (None: no cap)."""
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
