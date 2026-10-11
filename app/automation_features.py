"""Automation features — the user-facing switches over Luma's background jobs.

Settings → Automation shows a handful of features. Two of them group several
background jobs that do the same kind of work, so the user gets one switch
plus per-source options instead of a wall of cards:

    Auto-grab (automation.auto_grab.enabled)
        sources: latest_auto_grab, trending_watchlist, trending_tv_watchlist,
                 anticipated_watchlist, now_available_watchlist
    4K & upgrades (automation.upgrades.enabled)
        jobs:    upgrade_4k_auto_grab, upgrade_scheduler

A grouped job runs only when the group switch AND its own ``enabled`` flag are
on. Genre Roulette, Movie Night, Franchise Completer and Watchlist release
dates are single-job features: their own ``enabled`` flag is the switch.

Everything is OFF on a new install. Existing installs keep running exactly
what they ran before: settings_manager._migrate_automation_features turns a
group on when any of its jobs was effectively enabled.

The background threads themselves are unchanged (own schedules, state files
and Activity tags); only the on/off decision is routed through here.
"""

from typing import Any, Dict, List

GROUPS: Dict[str, List[str]] = {
    'auto_grab': [
        'latest_auto_grab',
        'trending_watchlist',
        'trending_tv_watchlist',
        'anticipated_watchlist',
        'now_available_watchlist',
    ],
    'upgrades': [
        'upgrade_4k_auto_grab',
        'upgrade_scheduler',
    ],
}

SINGLE_FEATURES: List[str] = [
    'genre_roulette',
    'movie_night',
    'franchise_completer',
    'watchlist_digital_dates',
]

# Default of each job's own ``enabled`` flag when the key is missing. The
# upgrade scan was always on unless switched off; the group switch now gates it.
MEMBER_DEFAULTS: Dict[str, bool] = {
    'latest_auto_grab': False,
    'trending_watchlist': False,
    'trending_tv_watchlist': False,
    'anticipated_watchlist': False,
    'now_available_watchlist': False,
    'upgrade_4k_auto_grab': False,
    'upgrade_scheduler': True,
}

_MEMBER_GROUP = {m: g for g, members in GROUPS.items() for m in members}


def group_of(job: str) -> str | None:
    return _MEMBER_GROUP.get(job)


def is_enabled(settings: Any, job: str, default: bool | None = None) -> bool:
    """True when *job* should run: its own flag is on and, for grouped jobs,
    the group's switch is on too."""
    if default is None:
        default = MEMBER_DEFAULTS.get(job, False)
    if not settings.get(f'automation.{job}.enabled', default):
        return False
    group = _MEMBER_GROUP.get(job)
    if group:
        return bool(settings.get(f'automation.{group}.enabled', False))
    return True


def summary(settings: Any) -> Dict[str, Any]:
    """On/off state of every feature and job (for the status endpoint)."""
    out: Dict[str, Any] = {}
    for group, members in GROUPS.items():
        out[group] = {
            'enabled': bool(settings.get(f'automation.{group}.enabled', False)),
            'jobs': {m: is_enabled(settings, m) for m in members},
        }
    for feature in SINGLE_FEATURES:
        out[feature] = {'enabled': bool(settings.get(f'automation.{feature}.enabled', False))}
    return out
