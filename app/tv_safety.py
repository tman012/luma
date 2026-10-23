"""v177 (Taylor's TV grab safety rules, before TV opt-ins go on). One central check, server-side, run
by tv_grab.grab before anything is fetched or sent to qBittorrent:

1. (v253, Taylor: replaces v177's 24 h air-date floor) Automatic grabs: every episode a release covers
   must be in a monitored show, be monitored itself and have an air date. A monitored, missing
   episode is searched and grabbed however long ago it aired (tv_search: until it is grabbed).
   floor_at / in_floor are kept for the dry run's report only; nothing refuses by them now.
2. No quality-cutoff upgrades (every grab, manual too): an episode that already has a linked file is
   never grabbed, nor a release that covers one.
3. (v253) Missing episodes are searched automatically (tv_search); backlog_enabled is still ignored.
4. (v204b, PM: removed) No daily ceiling on automatic grabs; only the tracker guard's burst bucket,
   120/h total and backoff hold a grab back, and a grab they hold is retried (app.grab_retry).

Manual grabs (the owner's Search or the Sources popup) only get rule 2. An 'upgrade' grab also needs TV
automation's upgrades_enabled (off by default; nothing issues one today), and rule 2 refuses it anyway.
TV upgrade mode replace_verified is stored only: no code reads it and nothing deletes an old episode
file (the mover only removes its own partial files). Every refusal is logged at INFO.
Reads tv.db only.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from app import tv_store

logger = logging.getLogger(__name__)

FLOOR_S = 24 * 3600
ET = ZoneInfo('America/New_York')
WINDOW_S = 24 * 3600            # the ceiling's window: rolling 24 h
AUTOMATIC = ('auto', 'upgrade')

UNMONITORED = 'not monitored'
NO_DATE = 'no air date'
HAS_FILE = 'already has a file'
UPGRADES_OFF = 'upgrades are off'
NO_EPISODES = 'no episodes'
UNKNOWN = 'unknown episode'

_LINKED = ("EXISTS (SELECT 1 FROM episode_file_links l JOIN episode_files f ON f.id = l.file_id"
           " WHERE l.episode_id = e.id AND f.state = 'linked')")


def aired_start(e: Dict[str, Any]) -> Optional[float]:
    """When the episode airs (epoch s): its airstamp (its start; parsed like tv_search.aired_at), else, with
    only an air date, the end of that day in local time (the after-air search's own estimate: a date-only
    episode is eligible through the next day). None: no air date."""
    stamp = e.get('airstamp')
    if stamp:
        try:
            return dt.datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).timestamp()
        except ValueError:
            pass
    if e.get('air_date'):
        try:
            d = dt.date.fromisoformat(str(e['air_date'])) + dt.timedelta(days=1)
            return dt.datetime(d.year, d.month, d.day).timestamp()
        except ValueError:
            return None
    return None


def floor_at(now: float) -> float:
    """Taylor (v177): the earlier of the start of today (America/New_York) and now - FLOOR_S."""
    midnight = dt.datetime.fromtimestamp(now, ET).replace(hour=0, minute=0, second=0, microsecond=0)
    return min(midnight.timestamp(), now - FLOOR_S)


def in_floor(e: Dict[str, Any], now: float) -> bool:
    """Rule 1: aired at or after floor_at(now), or still to come. Unknown air date: no."""
    at = aired_start(e)
    return at is not None and at >= floor_at(now)


def automatic_today(conn, now: float) -> int:
    return conn.execute(f"SELECT COUNT(*) AS n FROM grabs WHERE origin IN ({','.join('?' * len(AUTOMATIC))})"
                        ' AND grabbed_at > ?', (*AUTOMATIC, now - WINDOW_S)).fetchone()['n']


def check(series_id: Any, episode_ids: Iterable[Any], origin: str, now: Optional[float] = None,
          title: Any = None) -> Optional[str]:
    """None when the grab may go ahead, else why not (logged at INFO)."""
    now = time.time() if now is None else now
    why = _why(series_id, episode_ids, origin, now)
    if why:
        logger.info('TV grab refused (%s): %s, show %s, episodes %s (%s)', origin, str(title or '')[:200],
                    series_id, sorted(_ids(episode_ids))[:20], why)
    return why


def _upgrades_on() -> bool:
    try:
        from app import tv_settings
        return bool(tv_settings.load()['automation'].get('upgrades_enabled'))
    except Exception:                    # noqa: BLE001 - unreadable settings: off
        return False


def _ids(episode_ids: Iterable[Any]) -> List[int]:
    out = []
    for i in episode_ids or ():
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            continue
    return out


def _why(series_id: Any, episode_ids: Iterable[Any], origin: str, now: float) -> Optional[str]:
    ids = _ids(episode_ids)
    if not ids:
        return NO_EPISODES
    auto = origin != 'manual'
    if origin == 'upgrade' and not _upgrades_on():
        return UPGRADES_OFF                  # Ops: the switch is read (and rule 2 refuses upgrades anyway)
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f'SELECT e.id, e.series_id, e.air_date, e.airstamp, e.monitored AS ep_monitored, s.monitored AS show_monitored,'
            f' {_LINKED} AS has_file FROM episodes e JOIN series s ON s.id = e.series_id'
            f" WHERE e.id IN ({','.join('?' * len(ids))})", ids)]
        if len(rows) != len(set(ids)) or any(r['series_id'] != series_id for r in rows):
            return UNKNOWN
        if any(r['has_file'] for r in rows):
            return HAS_FILE
        if auto:
            if any(not r['air_date'] and not r['airstamp'] for r in rows):
                return NO_DATE
            if not all(r['show_monitored'] and r['ep_monitored'] for r in rows):
                return UNMONITORED          # v253: replaces the 24 h floor (OLD); age no longer refuses
    return None


def eligible(conn, now: float, until: float) -> List[Dict[str, Any]]:
    """Episodes an automatic grab could take by *until*: monitored show and episode, no file, an air
    date, starting before *until* (v253: however long ago). Reads only."""
    return [r for r in _all(conn) if not explain(r, now, until)]


def _all(conn) -> List[Dict[str, Any]]:
    linked = {r[0] for r in conn.execute("SELECT DISTINCT l.episode_id FROM episode_file_links l"
                                         " JOIN episode_files f ON f.id = l.file_id WHERE f.state = 'linked'")}
    rows = [dict(r) for r in conn.execute(
        'SELECT e.id, e.series_id, s.title AS show, e.season_number, e.episode_number, e.title, e.air_date, e.airstamp,'
        ' s.monitored AS show_monitored, e.monitored AS ep_monitored FROM episodes e JOIN series s ON s.id = e.series_id')]
    for r in rows:
        r['start'] = aired_start(r)
        r['has_file'] = r['id'] in linked
    rows.sort(key=lambda r: (r['start'] or 0, str(r['show']).casefold(), r['season_number'] or 0, r['episode_number'] or 0))
    return rows


def explain(r: Dict[str, Any], now: float, until: float) -> Optional[str]:
    """None when an automatic grab could take *r* (a row from _all) by *until*, else why not."""
    if r.get('start') is None:
        return NO_DATE
    if r['has_file']:
        return HAS_FILE
    if r['start'] > until:
        return 'airs after the window'
    if not r['show_monitored']:
        return 'show not monitored'
    if not r['ep_monitored']:
        return 'episode not monitored'
    return None


def airing_between(conn, lo: float, hi: float) -> List[Dict[str, Any]]:
    """Every episode (monitored or not) whose air time falls in [lo, hi], for the dry run's call-outs,
    plus that day's date-only episodes (no airstamp yet: the time is unknown, so they're listed too)."""
    day = dt.datetime.fromtimestamp((lo + hi) / 2, ET).date().isoformat()
    return [r for r in _all(conn) if (r['airstamp'] and r['start'] is not None and lo <= r['start'] <= hi)
            or (not r['airstamp'] and r['air_date'] == day)]
