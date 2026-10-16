"""
downloads_guard — keep free space on /downloads above a floor, unattended.

Every other automation in this app *adds* to the downloads folder. Nothing took
anything out except a human noticing the storage pill was red and pressing the
cleanup button, which means all of them ultimately depend on somebody watching a
number. This one closes that loop: it watches free space and, when it drops
below the configured floor, reclaims a fixed amount by deleting the oldest
downloads — preferring the trackers the user nominated (IPTorrents by default)
and widening to everything else only once those run dry.

Why prefer a tracker at all: the downloads folder is also the seeding pool, and
the trackers in it are not interchangeable. Spending the one whose back
catalogue is most expendable first, oldest release first, is the cheapest
possible way to buy space — and "oldest" is also the release that has already
banked the most seeding time, so it is the one whose deletion costs least.

The floor and the reclaim amount are separate numbers on purpose. Freeing
exactly enough to reach the floor would leave the disk sitting on the trigger,
re-firing on every check; reclaiming a slab past it buys quiet. If one slab
isn't enough to clear the floor (space was already far below it), the next check
simply takes another — the loop is self-correcting rather than trying to compute
the perfect single delete.

Deletion is PERMANENT. The guards live in `storage_cleanup` (48h minimum age,
in-flight torrents skipped, path-escape refused, downloads-root only) and are
not repeated here. What this module adds on top:

  * disabled by default, like every other automation here
  * a daily ceiling on total bytes deleted, so a wrong setting or a runaway
    writer can cost at most one slab per day rather than the whole folder before
    anyone notices
  * a refusal to run tracker-scoped when qBittorrent is unreachable, because
    "that tracker is empty" and "I can't tell the trackers apart" would
    otherwise produce the same widened delete for very different reasons

Note that nothing here is safe if the library ever shares a filesystem with the
downloads folder via hardlinks — deleting a download would then free nothing
while still destroying the copy. It doesn't here (the library is a separate SMB
mount and link counts are 1), which is why this automation can be trusted to
free the space it says it freed.
"""

import json
import logging
import os
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GIB = 1024 ** 3

_DEFAULT_MIN_FREE_GB = 150
_DEFAULT_RECLAIM_GB = 100
_DEFAULT_CHECK_MINUTES = 15
_DEFAULT_DAILY_CAP_GB = 500
# IPTorrents. Keys, not labels — see tracker_files.TRACKERS.
_DEFAULT_TRACKERS = ('ipt',)

_MAX_LOG_ENTRIES = 200

_lock = threading.Lock()
_hydrated = False
_last_status: Dict[str, Any] = {
    'running': False,
    'last_run': None,
    'last_result': None,
    'last_check': None,
    'last_check_free_bytes': None,
}


# ── Settings ──────────────────────────────────────────────────────────────

def _int_setting(settings, key: str, default: int,
                 low: int, high: int) -> int:
    try:
        v = int(settings.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(v, high))


def enabled(settings=None) -> bool:
    if settings is None:
        from app.settings_manager import SettingsManager
        settings = SettingsManager()
    return bool(settings.get('automation.downloads_guard.enabled', False))


def min_free_bytes(settings) -> int:
    return _int_setting(settings, 'automation.downloads_guard.min_free_gb',
                        _DEFAULT_MIN_FREE_GB, 1, 10_000) * GIB


def reclaim_bytes(settings) -> int:
    return _int_setting(settings, 'automation.downloads_guard.reclaim_gb',
                        _DEFAULT_RECLAIM_GB, 1, 2_000) * GIB


def daily_cap_bytes(settings) -> int:
    """Bytes this automation may delete per calendar day. 0 disables the cap."""
    return _int_setting(settings, 'automation.downloads_guard.daily_cap_gb',
                        _DEFAULT_DAILY_CAP_GB, 0, 20_000) * GIB


def check_interval_seconds(settings) -> int:
    return _int_setting(settings, 'automation.downloads_guard.check_interval_minutes',
                        _DEFAULT_CHECK_MINUTES, 1, 1_440) * 60


def preferred_trackers(settings) -> List[str]:
    """Tracker keys to spend first. Empty means "no preference — pure oldest".

    Unlike the tracker pickers on the acquisition automations, empty here does
    NOT mean "all of them": with no preference there is only one pass, straight
    oldest-first across the whole folder. Both readings delete something; this
    one is what the setting literally says.
    """
    raw = settings.get('automation.downloads_guard.trackers', None)
    if raw is None:
        return list(_DEFAULT_TRACKERS)
    if isinstance(raw, str):
        raw = raw.split(',')
    if not isinstance(raw, (list, tuple)):
        return list(_DEFAULT_TRACKERS)
    return [str(t).strip() for t in raw if str(t).strip()]


# ── State / log ───────────────────────────────────────────────────────────

def _data_dir() -> Path:
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data
    db_path = os.environ.get('DATABASE_PATH', '')
    if db_path:
        return Path(db_path).parent
    return Path(__file__).resolve().parent.parent / 'data'


def _state_path() -> Path:
    return _data_dir() / 'downloads_guard_state.json'


def _log_path() -> Path:
    return _data_dir() / 'downloads_guard_log.json'


def _read_state() -> Dict[str, Any]:
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                data.setdefault('last_run', None)
                data.setdefault('last_result', None)
                # Deleted-bytes tally for one calendar day, behind the daily cap.
                # Dated so a stale tally from days ago can't hold today's run.
                data.setdefault('freed_date', None)
                data.setdefault('freed_bytes_today', 0)
                return data
    except Exception as e:
        logger.warning('Downloads Guard: failed to read state: %s', e)
    return {'last_run': None, 'last_result': None,
            'freed_date': None, 'freed_bytes_today': 0}


def _write_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning('Downloads Guard: failed to write state: %s', e)


def _freed_today(state: Dict[str, Any]) -> int:
    """Bytes deleted so far today, treating a tally from another day as zero."""
    if state.get('freed_date') != date.today().isoformat():
        return 0
    try:
        return max(0, int(state.get('freed_bytes_today') or 0))
    except (TypeError, ValueError):
        return 0


def _append_log(entry: Optional[Dict[str, Any]]) -> None:
    if not entry:
        return
    try:
        p = _log_path()
        existing = json.loads(p.read_text()) if p.exists() else []
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(existing[-_MAX_LOG_ENTRIES:]))
    except Exception as e:
        logger.warning('Downloads Guard: failed to write log: %s', e)


def _hydrate_status() -> None:
    """Seed in-memory status from disk once, so a restart doesn't erase it."""
    global _hydrated
    with _lock:
        if _hydrated:
            return
        _hydrated = True
        state = _read_state()
        if state.get('last_run'):
            _last_status['last_run'] = state['last_run']
            _last_status['last_result'] = state.get('last_result')
        # PERSIST_LAST_CHECK
        if state.get('last_check'):
            _last_status['last_check'] = state['last_check']
            _last_status['last_check_free_bytes'] = state.get('last_check_free_bytes')


# ── Pending downloads ─────────────────────────────────────────────────────

# A torrent idle longer than this stops counting toward pending space. One
# stalled at 5% would otherwise reserve its whole remaining size forever,
# holding the guard permanently triggered and deleting a slab a day to make room
# for bytes that are never going to arrive.
_PENDING_IDLE_SECONDS = 3600

# States where the remaining bytes are still genuinely on their way.
# 'pausedDL' is excluded outright: paused is a decision, not a delay.
_PENDING_STATES = frozenset({
    'downloading', 'forcedDL', 'metaDL', 'allocating', 'checkingDL',
    'stalledDL', 'queuedDL',
})

_pending_cache: Dict[str, Any] = {'bytes': None, 'at': None}


def pending_bytes(torrents: Optional[List[Dict[str, Any]]] = None,
                  allow_fetch: bool = True) -> Optional[int]:
    """Bytes the in-flight downloads still have to write, or None if unknown.

    qBittorrent here does not preallocate (`preallocate_all=False`), so a 90GB
    grab eats disk gradually instead of reserving it up front. Free space
    therefore says where the disk *is*, not where it is heading — and a guard
    reading only that reacts after the fact, with up to one poll interval of lag.
    Grabs can land inside that lag: four automations taking 60-90GB remuxes can
    commit more than the free space before a single check happens.

    Counting what is still owed closes that gap, at the cost of trusting an
    intention rather than a fact — hence the idle cutoff above.
    """
    if torrents is None:
        if not allow_fetch:
            # Serve the scheduler's last figure: torrents/info can take the
            # better part of a minute under load, and the status endpoint that
            # backs the settings card must not block on it.
            return _pending_cache.get('bytes')
        from app import tracker_files
        torrents = tracker_files.fetch_torrents()
    if torrents is None:
        return None

    now = time.time()
    total = 0
    for t in torrents:
        if t.get('state') not in _PENDING_STATES:
            continue
        left = int(t.get('amount_left') or 0)
        if left <= 0:
            continue
        # Moving now, or moved recently enough to believe it will again. A
        # missing last_activity counts as pending — erring toward reserving
        # space is the safe direction for a guard.
        if not (t.get('dlspeed') or 0):
            last = t.get('last_activity') or 0
            if last and (now - last) > _PENDING_IDLE_SECONDS:
                continue
        total += left

    _pending_cache['bytes'] = total
    _pending_cache['at'] = now
    return total


# ── Evaluation ────────────────────────────────────────────────────────────

def evaluate(settings=None, allow_fetch: bool = True) -> Dict[str, Any]:
    """Should a cleanup run right now, and why — without deleting anything.

    The whole trigger decision in one place so the settings card can show
    exactly what the scheduler is about to conclude, rather than the user having
    to infer it from two numbers.
    """
    if settings is None:
        from app.settings_manager import SettingsManager
        settings = SettingsManager()
    from app.storage_cleanup import usage

    use = usage()
    floor = min_free_bytes(settings)
    want = reclaim_bytes(settings)
    cap = daily_cap_bytes(settings)
    spent = _freed_today(_read_state())

    free = use.get('free_bytes', 0)
    pending = pending_bytes(allow_fetch=allow_fetch)
    # The number the decision is made on. Falls back to plain free space when
    # qBittorrent can't be asked — reactive is worse than predictive, but far
    # better than not running at all, and the delete stage refuses separately if
    # attribution is genuinely unavailable.
    projected = max(0, free - pending) if pending else free

    out = {
        'enabled': enabled(settings),
        'available': use.get('available', False),
        'path': use.get('path'),
        'free_bytes': free,
        'pending_bytes': pending,
        'projected_free_bytes': projected,
        'total_bytes': use.get('total_bytes', 0),
        'min_free_bytes': floor,
        'reclaim_bytes': want,
        'daily_cap_bytes': cap,
        'freed_today_bytes': spent,
        'trackers': preferred_trackers(settings),
        'triggered': False,
        'reason': '',
    }

    # "228 GB free" and "178 GB once the queue lands" are different facts and the
    # log has to say which one it acted on, or a delete at 228 GB free reads as a
    # guard firing above its own floor.
    def _where() -> str:
        if pending:
            return (f"{projected / GIB:.0f} GB free once the "
                    f"{pending / GIB:.0f} GB in flight lands "
                    f"({free / GIB:.0f} GB free right now)")
        return f"{free / GIB:.0f} GB free"

    if not out['available']:
        out['reason'] = f"downloads folder not readable ({use.get('path')})"
        return out
    if not out['enabled']:
        out['reason'] = 'disabled'
        return out
    if projected >= floor:
        out['reason'] = f"{_where()} is at or above the {floor / GIB:.0f} GB floor"
        return out
    if cap and spent >= cap:
        out['reason'] = (f"daily ceiling reached — {spent / GIB:.0f} GB already "
                         f"deleted today of {cap / GIB:.0f} GB allowed")
        return out

    out['triggered'] = True
    # The cap trims the slab rather than cancelling the run: a partial reclaim
    # still buys space, and cancelling outright would leave the disk on the
    # trigger with nothing done and only a log line to show for it.
    allowed = want if not cap else min(want, cap - spent)
    out['target_bytes'] = allowed
    out['reason'] = (f"{_where()} is below the {floor / GIB:.0f} GB floor — "
                     f"reclaiming {allowed / GIB:.0f} GB")
    return out


def preview() -> Dict[str, Any]:
    """What a run would delete right now. Deletes nothing.

    Always plans a slab, even when free space is above the floor, so the user
    can see what the automation *would* take before trusting it with the
    decision. `evaluate` is what says whether it would fire.
    """
    from app.settings_manager import SettingsManager
    from app.storage_cleanup import plan_cleanup

    settings = SettingsManager()
    verdict = evaluate(settings)
    target = verdict.get('target_bytes') or reclaim_bytes(settings)
    plan = plan_cleanup(target, prefer_trackers=preferred_trackers(settings))

    return {
        'evaluation': verdict,
        'error': plan.get('error'),
        'target_bytes': plan.get('target_bytes', target),
        'planned_bytes': plan.get('planned_bytes', 0),
        'preferred_bytes': plan.get('preferred_bytes', 0),
        'fallback_bytes': plan.get('fallback_bytes', 0),
        'preferred_available': plan.get('preferred_available', 0),
        'eligible_count': plan.get('eligible_count', 0),
        'short': plan.get('short', False),
        'entries': [
            {
                'name': e['name'],
                'size_bytes': e['size_bytes'],
                'mtime': e['mtime'],
                'tracker_label': e.get('tracker_label'),
                'phase': e.get('phase', 'preferred'),
            }
            for e in plan.get('entries', [])
        ],
    }


# ── Run ───────────────────────────────────────────────────────────────────

def run_cycle(dry_run: bool = False, force: bool = False) -> Dict[str, Any]:
    """One check, and a cleanup if the floor is breached.

    `force` skips only the free-space check — it is the "Run now" button, and it
    still respects the daily ceiling and every guard in `storage_cleanup`. There
    is deliberately no way to bypass those from the UI.
    """
    from app.settings_manager import SettingsManager
    from app.storage_cleanup import run_cleanup

    settings = SettingsManager()
    verdict = evaluate(settings)

    result: Dict[str, Any] = {
        'at': datetime.now().isoformat(timespec='seconds'),
        'dry_run': dry_run,
        'forced': force,
        'triggered': verdict['triggered'],
        'reason': verdict['reason'],
        'free_before_bytes': verdict['free_bytes'],
        'free_after_bytes': verdict['free_bytes'],
        'pending_bytes': verdict.get('pending_bytes'),
        'projected_free_bytes': verdict.get('projected_free_bytes'),
        'min_free_bytes': verdict['min_free_bytes'],
        'freed_bytes': 0,
        'freed_preferred_bytes': 0,
        'freed_fallback_bytes': 0,
        'deleted': [],
        'failed': [],
        'short': False,
        'error': None,
    }

    if not verdict['available']:
        result['error'] = verdict['reason']
        return result

    # A forced run past the floor still needs a target and still obeys the cap.
    if force and not verdict['triggered']:
        cap = verdict['daily_cap_bytes']
        spent = verdict['freed_today_bytes']
        if cap and spent >= cap:
            result['error'] = verdict['reason'] if 'ceiling' in verdict['reason'] else (
                f"daily ceiling reached — {spent / GIB:.0f} GB already deleted today")
            return result
        want = verdict['reclaim_bytes']
        verdict['target_bytes'] = want if not cap else min(want, cap - spent)
        result['triggered'] = True
        result['reason'] = (f"manual run — reclaiming "
                            f"{verdict['target_bytes'] / GIB:.0f} GB")

    if not result['triggered']:
        return result

    target = verdict['target_bytes']
    prefer = preferred_trackers(settings)

    if dry_run:
        from app.storage_cleanup import plan_cleanup
        plan = plan_cleanup(target, prefer_trackers=prefer)
        result['error'] = plan.get('error')
        result['freed_bytes'] = plan.get('planned_bytes', 0)
        result['freed_preferred_bytes'] = plan.get('preferred_bytes', 0)
        result['freed_fallback_bytes'] = plan.get('fallback_bytes', 0)
        result['short'] = plan.get('short', False)
        result['deleted'] = [
            {'name': e['name'], 'size_bytes': e['size_bytes'],
             'tracker_label': e.get('tracker_label'), 'phase': e.get('phase')}
            for e in plan.get('entries', [])
        ]
        return result

    outcome = run_cleanup(target, prefer_trackers=prefer)
    if not outcome.get('success'):
        result['error'] = outcome.get('error') or 'cleanup failed'
        return result

    result['freed_bytes'] = outcome.get('freed_bytes', 0)
    result['freed_preferred_bytes'] = outcome.get('freed_preferred_bytes', 0)
    result['freed_fallback_bytes'] = outcome.get('freed_fallback_bytes', 0)
    result['deleted'] = outcome.get('deleted', [])
    result['failed'] = outcome.get('failed', [])
    result['short'] = outcome.get('short', False)
    result['free_after_bytes'] = (outcome.get('after') or {}).get(
        'free_bytes', verdict['free_bytes'])

    # Tally against the daily ceiling on the way out, keyed to today's date so
    # it resets on its own rather than needing anything to clear it.
    state = _read_state()
    today = date.today().isoformat()
    state['freed_bytes_today'] = _freed_today(state) + result['freed_bytes']
    state['freed_date'] = today
    _write_state(state)

    return result


def summarise(r: Dict[str, Any]) -> str:
    """One line describing a run, matching what the settings card renders."""
    if r.get('error'):
        return r['error']
    if not r.get('triggered'):
        return r.get('reason') or 'nothing to do'

    freed = r.get('freed_bytes', 0)
    if not freed:
        return f"nothing deleted — {r.get('reason', 'no eligible entries')}"

    verb = 'would free' if r.get('dry_run') else 'freed'
    parts = [f"{verb} {freed / GIB:.0f} GB across {len(r.get('deleted', []))} entries"]
    pref = r.get('freed_preferred_bytes', 0)
    fall = r.get('freed_fallback_bytes', 0)
    # Named whenever the run had to reach past the preferred trackers — that is
    # the signal the preferred pool has run dry, and it should never be silent.
    if fall:
        parts.append(f"{pref / GIB:.0f} GB preferred + {fall / GIB:.0f} GB other")
    if r.get('short'):
        parts.append('short of target — ran out of eligible entries')
    if r.get('failed'):
        parts.append(f"{len(r['failed'])} failed to delete")
    if not r.get('dry_run'):
        parts.append(f"{r.get('free_after_bytes', 0) / GIB:.0f} GB free now")
    return ' · '.join(parts)


def _outcome(r: Dict[str, Any]) -> str:
    if r.get('error'):
        return 'error'
    # Its own outcome, not 'freed'. A dry run reports the same byte count as a
    # real one, so sharing the success styling would show a preview as a
    # completed 100 GB delete in the history.
    if r.get('dry_run'):
        return 'preview'
    if r.get('freed_bytes'):
        return 'freed' if not r.get('short') else 'partial'
    if r.get('triggered'):
        return 'partial'
    return 'nothing'


def _log_entry(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'at': r.get('at') or datetime.now().isoformat(timespec='seconds'),
        'outcome': _outcome(r),
        'summary': summarise(r),
        'freed_bytes': r.get('freed_bytes', 0),
        'free_after_bytes': r.get('free_after_bytes', 0),
        'dry_run': bool(r.get('dry_run')),
    }


def _guarded(dry_run: bool = False, force: bool = False,
             log_quiet: bool = False) -> None:
    """Run once, recording status and log. Never raises to the caller.

    `log_quiet=False` keeps a check that found nothing to do out of the run
    history — the scheduler checks every 15 minutes, and 96 "nothing to do"
    rows a day would bury the handful of entries that matter. Status still
    carries the last check, so a quiet guard is distinguishable from a dead one.
    """
    _hydrate_status()
    with _lock:
        if _last_status['running']:
            logger.info('Downloads Guard: a run is already in progress')
            return
        _last_status['running'] = True

    try:
        result = run_cycle(dry_run=dry_run, force=force)
        with _lock:
            _last_status['last_check'] = result['at']
            _last_status['last_check_free_bytes'] = result['free_before_bytes']
            if result['triggered'] or result.get('error') or log_quiet:
                _last_status['last_run'] = result['at']
                _last_status['last_result'] = result

        # Always persist last_check so "Last checked" survives restarts even when
        # the guard found nothing to delete.
        state = _read_state()
        state['last_check'] = result['at']
        state['last_check_free_bytes'] = result['free_before_bytes']
        if result['triggered'] or result.get('error') or log_quiet:
            state['last_run'] = result['at']
            state['last_result'] = result
            _append_log(_log_entry(result))
        _write_state(state)

        if result.get('error'):
            logger.error('Downloads Guard: %s', result['error'])
        elif result['triggered']:
            logger.info('Downloads Guard: %s', summarise(result))
    except Exception as e:
        logger.error('Downloads Guard run failed: %s', e, exc_info=True)
    finally:
        with _lock:
            _last_status['running'] = False


def trigger_manual_run(dry_run: bool = True) -> None:
    """Fire a run in the background. Manual runs always appear in the log."""
    threading.Thread(
        target=lambda: _guarded(dry_run=dry_run, force=True, log_quiet=True),
        daemon=True, name='downloads-guard-manual').start()


# ── Status ────────────────────────────────────────────────────────────────

def get_status() -> Dict[str, Any]:
    _hydrate_status()
    with _lock:
        out = dict(_last_status)
    try:
        # allow_fetch=False: serve the scheduler's cached pending figure rather
        # than making the settings card wait on torrents/info.
        out['evaluation'] = evaluate(allow_fetch=False)
    except Exception as e:
        logger.warning('Downloads Guard: could not evaluate: %s', e)
        out['evaluation'] = None
    try:
        out['server_tz'] = datetime.now().astimezone().tzname()
    except Exception:
        out['server_tz'] = None
    return out


def get_log() -> List[Dict[str, Any]]:
    """Run history, newest first."""
    try:
        p = _log_path()
        entries = json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []
    out = [e for e in entries if isinstance(e, dict)]
    out.reverse()
    return out


# ── Scheduler ─────────────────────────────────────────────────────────────

def start_downloads_guard_thread() -> None:
    _hydrate_status()
    t = threading.Thread(target=_scheduler_loop, daemon=True,
                         name='downloads-guard')
    t.start()
    logger.info('Downloads Guard started (disabled until enabled in settings)')


def _scheduler_loop() -> None:
    # After the other automations' startup burst, and after qBittorrent has had
    # a chance to come up — a check that fires before it is reachable would
    # refuse the tracker-scoped run for no real reason.
    time.sleep(180)
    while True:
        interval = _DEFAULT_CHECK_MINUTES * 60
        try:
            from app.settings_manager import SettingsManager
            settings = SettingsManager()
            interval = check_interval_seconds(settings)
            if enabled(settings):
                _guarded()
        except Exception as e:
            logger.error('Downloads Guard scheduler error: %s', e, exc_info=True)
        time.sleep(interval)
