"""Series banners for the detail header: a paced TheTVDB backfill.

tv_airtimes only asks TheTVDB for a banner when a show's air times are due
(once a day, after a TV scan), so shows already in tv.db when banner_url was
added (v13) waited for that. This pass asks only for the banner (never
episodes) of shows with a tvdb_id and no banner_url, when a TheTVDB key is
saved in Connectors:

- one call per PACE_S, at most MAX_PER_PASS shows a pass;
- at startup (a background thread, after START_DELAY_S), then every
  SWEEP_EVERY_S, and soon after poke() (a show added or scanned in);
- a show TheTVDB has no banner for is stamped banner_checked_at and asked
  again only after RECHECK_S;
- a failed call (any exception, incl. TheTVDB non-200 or auth errors) stops the pass and
  backs off (1, 2, 4 ... 60 min), logged as one line without a traceback; it never stamps
  'none found';
- a write lands only if the row still holds the tmdb_id and tvdb_id it was
  read with (a rematch mid-pass can't get another show's banner).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

from app import tv_store

logger = logging.getLogger(__name__)

PACE_S = 1.0                    # one TheTVDB call a second
MAX_PER_PASS = 100              # 696 shows: about 7 passes
SWEEP_EVERY_S = 15 * 60
START_DELAY_S = 30.0            # after boot, so startup isn't slowed
POKE_DELAY_S = 5.0
RECHECK_S = 7 * 24 * 3600       # no banner on TheTVDB: ask again after a week
BACKOFF_S = 60.0
BACKOFF_MAX_S = 3600.0

_cfg: Dict[str, Callable] = {'sleep': time.sleep, 'clock': time.monotonic}
_last = [0.0]
_fail: Dict[str, float] = {'until': 0.0, 'n': 0}
_wake = threading.Event()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def _pace() -> None:
    wait = _last[0] + PACE_S - _cfg['clock']()
    if wait > 0:
        _cfg['sleep'](wait)
    _last[0] = _cfg['clock']()


def _due(now: float):
    with tv_store.connect() as conn:
        return [dict(r) for r in conn.execute(
            'SELECT id, tmdb_id, tvdb_id FROM series WHERE tvdb_id IS NOT NULL AND tmdb_id IS NOT NULL'
            ' AND path_id IS NOT NULL AND banner_url IS NULL'
            ' AND (banner_checked_at IS NULL OR banner_checked_at <= ?)'
            ' ORDER BY banner_checked_at IS NOT NULL, COALESCE(banner_checked_at, 0), id LIMIT ?',
            (now - RECHECK_S, MAX_PER_PASS))]


def _write(r: Dict, url: Optional[str], now: float) -> bool:
    with tv_store.write() as conn:
        cur = conn.execute(
            'UPDATE series SET banner_url = COALESCE(banner_url, ?), banner_checked_at = ?'
            ' WHERE id = ? AND tmdb_id = ? AND tvdb_id = ?', (url, now, r['id'], r['tmdb_id'], r['tvdb_id']))
        return cur.rowcount == 1


def run_pass(now: Optional[float] = None) -> Dict[str, int]:
    """One backfill pass. Returns {'asked', 'found', 'none', 'skipped'}."""
    out = {'asked': 0, 'found': 0, 'none': 0, 'skipped': 0}
    with _lock:
        now = time.time() if now is None else now
        if now < _fail['until']:
            return out
        from app import connectors, tv_tvdb
        creds = connectors.tvdb_credentials()
        if not creds[0]:
            return out
        rows = _due(now)
        if not rows:
            return out
        failed = None
        for r in rows:
            try:
                _pace()
                url = tv_tvdb.banner(r['tvdb_id'], creds)
            except Exception as e:      # noqa: BLE001 - any failure (expected or not) backs off, one line, no traceback
                _fail['n'] += 1
                wait = min(BACKOFF_S * 2 ** (_fail['n'] - 1), BACKOFF_MAX_S)
                _fail['until'] = now + wait
                failed = (type(e).__name__, int(wait // 60))
                break
            out['asked'] += 1
            if not _write(r, url, now):
                out['skipped'] += 1
            elif url:
                out['found'] += 1
            else:
                out['none'] += 1
        else:
            _fail.update(until=0.0, n=0)
        logger.info('Series banners: asked %d of %d shows, %d found, %d none, %d skipped%s',
                    out['asked'], len(rows), out['found'], out['none'], out['skipped'],
                    f'; TheTVDB unavailable ({failed[0]}), next try in {failed[1]} min' if failed else '')
        return out


def poke() -> None:
    """A show was added or scanned in: run a pass soon."""
    _wake.set()


def _loop() -> None:
    if _stop.wait(START_DELAY_S):
        return
    while not _stop.is_set():
        try:
            run_pass()
        except Exception as e:          # noqa: BLE001 - the next pass tries again
            logger.warning('Series banner pass failed (%s)', type(e).__name__)
        _wake.clear()
        if _wake.wait(SWEEP_EVERY_S):
            if _stop.wait(POKE_DELAY_S):  # a burst of adds runs as one pass
                return


def start() -> bool:
    global _thread
    if _thread is not None and _thread.is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name='tv-banners')
    _thread.start()
    return True


def stop() -> None:
    _stop.set()
    _wake.set()


def forget() -> None:
    """A TheTVDB key was saved or removed: shows without a banner are asked again."""
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET banner_checked_at = NULL WHERE banner_url IS NULL')
    _fail.update(until=0.0, n=0)
    poke()


def configure(sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
    """Tests: a fake clock."""
    _cfg.update(sleep=sleep, clock=clock)
    _last[0] = 0.0
    _fail.update(until=0.0, n=0)
