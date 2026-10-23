"""v252 Genre Roulette schedule (prod 1b4ea74 :2621-2690, ET instead of the server clock).

Once a day at the set time (default 10:50 PM ET) on the chosen days, when on. The slot is keyed on date and
time, so moving the time after today's run gives the new time its own run. A run that comes up short (or
fails) is retried up to `retries` times, `retry_gap_min` apart, the same night. Test install only, like the
movie scanner it grabs through.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from app.automation import gr_engine, gr_store

logger = logging.getLogger(__name__)
TZ = ZoneInfo('America/New_York')
CHECK_S = 300
_started = {'on': False}


def _now(now: Optional[datetime]) -> datetime:
    return now.astimezone(TZ) if now else datetime.now(TZ)


def day_ok(cfg: Dict[str, Any], d: datetime) -> bool:
    days = cfg.get('days') or 'daily'
    wd = d.weekday()
    return days == 'daily' or (days == 'weekdays' and wd < 5) or (days == 'weekends' and wd >= 5)


def slot(cfg: Dict[str, Any], d: datetime) -> datetime:
    h, m = (int(x) for x in str(cfg.get('time') or '22:50').split(':'))
    return d.replace(hour=h, minute=m, second=0, microsecond=0)


def retry_due(cfg: Dict[str, Any], st: Dict[str, Any], now: datetime) -> bool:
    short = st.get('short_night') or {}
    if short.get('date') != now.date().isoformat() or int(short.get('retries') or 0) >= int(cfg.get('retries') or 0):
        return False
    last = st.get('last_scheduled_at')
    return not last or now.timestamp() - float(last) >= int(cfg.get('retry_gap_min') or 20) * 60


def spent(cfg: Dict[str, Any], st: Dict[str, Any], now: datetime) -> bool:
    return st.get('last_scheduled_date') == now.date().isoformat() and st.get('last_scheduled_time') == cfg.get('time')


def due(cfg: Dict[str, Any], st: Dict[str, Any], now: datetime) -> bool:
    if not cfg.get('enabled') or not day_ok(cfg, now) or now < slot(cfg, now):
        return False
    return not spent(cfg, st, now) or retry_due(cfg, st, now)


def next_run(cfg: Optional[Dict[str, Any]] = None, st: Optional[Dict[str, Any]] = None,
             now: Optional[datetime] = None) -> Optional[datetime]:
    cfg = cfg or gr_store.settings()
    if not cfg.get('enabled'):
        return None
    st = gr_store.state() if st is None else st
    now = _now(now)
    if day_ok(cfg, now) and now >= slot(cfg, now):
        if not spent(cfg, st, now):
            return now.replace(second=0, microsecond=0)
        short = st.get('short_night') or {}
        if short.get('date') == now.date().isoformat() and int(short.get('retries') or 0) < int(cfg.get('retries') or 0):
            at = float(st.get('last_scheduled_at') or now.timestamp()) + int(cfg.get('retry_gap_min') or 20) * 60
            return max(now, datetime.fromtimestamp(at, TZ)).replace(second=0, microsecond=0)
    d = now if now < slot(cfg, now) and day_ok(cfg, now) else now + timedelta(days=1)
    for _ in range(8):
        if day_ok(cfg, d):
            return slot(cfg, d)
        d += timedelta(days=1)
    return None


def tick(now: Optional[datetime] = None, run=None) -> Optional[Dict[str, Any]]:
    """Run if due. Returns the run result, or None."""
    cfg = gr_store.settings()
    st = gr_store.state()
    now = _now(now)
    if not due(cfg, st, now):
        return None
    retry = spent(cfg, st, now)
    logger.info('Genre Roulette: %s (scheduled %s ET)', 'retrying tonight' if retry else 'starting', cfg['time'])
    st.update(last_scheduled_date=now.date().isoformat(), last_scheduled_time=cfg['time'],
              last_scheduled_at=now.timestamp())
    gr_store.save_state(st)
    result = (run or gr_engine.run)('scheduled', now=now.timestamp())
    if result.get('status') == 'busy':
        return result
    st = gr_store.state()
    prev = st.get('short_night') or {}
    if result.get('error') or int(result.get('grabbed') or 0) < int(result.get('wanted') or 0):
        today = now.date().isoformat()
        st['short_night'] = {'date': today,
                             'retries': int(prev.get('retries') or 0) + 1 if retry and prev.get('date') == today else 0,
                             'grabbed': int(result.get('grabbed') or 0), 'wanted': int(result.get('wanted') or 0)}
    else:
        st['short_night'] = {}
    gr_store.save_state(st)
    return result


def sleep_s(now: Optional[datetime] = None) -> float:
    try:
        nxt = next_run(now=now)
        if nxt:
            delta = (nxt - _now(now)).total_seconds()
            if 0 < delta < CHECK_S:
                return delta + 1
    except Exception:                                    # noqa: BLE001
        logger.exception('Genre Roulette: next run unknown')
    return CHECK_S


def status(now: Optional[datetime] = None) -> Dict[str, Any]:
    cfg = gr_store.settings()
    st = gr_store.state()
    runs = gr_store.runs(limit=200)
    real = [r for r in runs if r['status'] != 'running']
    last = next((r for r in real if not r['dry']), None) or (real[0] if real else None)
    nowts = _now(now).timestamp()
    failures = [r for r in real if not r['dry'] and r['started_at'] >= nowts - 7 * 86400
                and (r['status'] == 'error' or r['grabbed'] < r['wanted'])]
    nxt = next_run(cfg, st, now)
    live = gr_engine.running()
    return {'running': bool(live.get('running')), 'enabled': bool(cfg.get('enabled')),
            'next_run': nxt.isoformat() if nxt else None,
            'last_run': {k: last.get(k) for k in ('id', 'started_at', 'finished_at', 'grabbed', 'wanted', 'status',
                                                  'stop_reason', 'error', 'dry')} if last else None,
            'failures_7d': len(failures)}


def start() -> bool:
    from app import movie_scanner
    if _started['on'] or not movie_scanner.on_test():
        return False
    _started['on'] = True
    try:
        gr_store.interrupted()
    except Exception:                                    # noqa: BLE001
        logger.exception('Genre Roulette: could not close interrupted runs')

    def loop():
        time.sleep(120)                                  # after the other automations' startup burst
        while True:
            try:
                tick()
            except Exception:                            # noqa: BLE001
                logger.exception('Genre Roulette scheduler pass failed')
            time.sleep(sleep_s())
    threading.Thread(target=loop, name='genre-roulette-native', daemon=True).start()
    logger.info('Genre Roulette scheduler started (off until turned on in Settings > Automation)')
    return True
