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
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

PURPOSE = 'tv'

AUTO_PER_TRACKER_DAILY = 8      # PM ruling: 8/day per tracker while prod shares the accounts
AUTO_PER_TRACKER_HOURLY = 2
AUTO_ALL_TRACKERS_DAILY = 40
AUTO_MIN_GAP_S = 600            # between automatic TV requests to one tracker

GUARD_LIMITS: Dict[str, float] = {
    'per_tracker_day': AUTO_PER_TRACKER_DAILY,
    'per_tracker_hour': AUTO_PER_TRACKER_HOURLY,
    'all_trackers_day': AUTO_ALL_TRACKERS_DAILY,
    'min_gap_s': AUTO_MIN_GAP_S,
}


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
    return {'used': int(c['day']), 'cap': AUTO_PER_TRACKER_DAILY}
