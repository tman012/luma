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

Deletion is PERMANENT. v260 (test install): every delete is qBittorrent's
torrents/delete with deleteFiles=true (the downloads mount is read-only), and the
guards live in `storage_cleanup` (finished only, minimum age, scope, imported,
not playing in Plex, never a library folder, nothing at all while qBittorrent or
Plex can't be read) and are not repeated here. Settings: app/automation/dg_store. What this module adds on top:

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
# v260: the Downloads guard settings live in app/automation/dg_store (Settings > Automation), with prod's
# defaults; `settings` below is that dict (dg_store.settings()).

def _int_setting(settings, key: str, default: int, low: int, high: int) -> int:
    try:
        v = int(settings.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(v, high))


def _legacy(settings) -> Dict[str, Any]:
    """Prod's settings.json keys (automation.downloads_guard.*), read with prod's clamping. Only
    auto_delete_guard (the full app's /api/settings widening check) still reads these."""
    from app.automation import dg_store
    p = 'automation.downloads_guard.'
    raw = settings.get(p + 'trackers', None)
    if isinstance(raw, str):
        raw = raw.split(',')
    trackers = [str(t).strip() for t in raw if str(t).strip()] if isinstance(raw, (list, tuple)) else ['ipt']
    return {**dg_store.defaults(),
            'enabled': bool(settings.get(p + 'enabled', False)),
            'min_free_gb': _int_setting(settings, p + 'min_free_gb', 150, 1, 10_000),
            'reclaim_gb': _int_setting(settings, p + 'reclaim_gb', 100, 1, 2_000),
            'daily_cap_gb': _int_setting(settings, p + 'daily_cap_gb', 500, 0, 20_000),
            'check_interval_minutes': _int_setting(settings, p + 'check_interval_minutes', 15, 1, 1_440),
            'trackers': trackers}


def _cfg(settings=None) -> Dict[str, Any]:
    if isinstance(settings, dict):
        return settings
    if settings is not None and hasattr(settings, 'get'):
        return _legacy(settings)
    from app.automation import dg_store
    return dg_store.settings()


def enabled(settings=None) -> bool:
    return bool(_cfg(settings)['enabled'])


def min_free_bytes(settings) -> int:
    return int(_cfg(settings)['min_free_gb']) * GIB


def reclaim_bytes(settings) -> int:
    return int(_cfg(settings)['reclaim_gb']) * GIB


def daily_cap_bytes(settings) -> int:
    """Bytes this automation may delete per calendar day. 0 disables the cap."""
    return int(_cfg(settings)['daily_cap_gb']) * GIB


def check_interval_seconds(settings) -> int:
    return int(_cfg(settings)['check_interval_minutes']) * 60


def preferred_trackers(settings) -> List[str]:
    """Tracker keys to spend first, in order. Empty means "no preference — pure oldest"."""
    return list(_cfg(settings)['trackers'])


# ── State / log ───────────────────────────────────────────────────────────

def _data_dir() -> Path:
    from app import paths as _paths
    return Path(_paths.data_dir())


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
        from app import storage_cleanup
        torrents = storage_cleanup.fetch_torrents()
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
    settings = _cfg(settings)
    from app.storage_cleanup import usage

    use = usage(settings)
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
            return (f"{_size(projected)} free once the "
                    f"{_size(pending)} in flight lands "
                    f"({_size(free)} free right now)")
        return f"{_size(free)} free"

    if not out['available']:
        out['reason'] = f"downloads folder not readable ({use.get('path')})"
        return out
    if not out['enabled']:
        out['reason'] = 'disabled'
        return out
    if projected >= floor:
        out['reason'] = f"{_where()} is at or above the {_size(floor)} floor"
        return out
    if cap and spent >= cap:
        out['reason'] = (f"daily ceiling reached — {_size(spent)} already "
                         f"deleted today of {_size(cap)} allowed")
        return out

    out['triggered'] = True
    # The cap trims the slab rather than cancelling the run: a partial reclaim
    # still buys space, and cancelling outright would leave the disk on the
    # trigger with nothing done and only a log line to show for it.
    allowed = want if not cap else min(want, cap - spent)
    out['target_bytes'] = allowed
    out['reason'] = (f"{_where()} is below the {_size(floor)} floor — "
                     f"reclaiming {_size(allowed)}")
    return out


def preview() -> Dict[str, Any]:
    """What a run would delete right now. Deletes nothing.

    Always plans a slab, even when free space is above the floor, so the user
    can see what the automation *would* take before trusting it with the
    decision. `evaluate` is what says whether it would fire.
    """
    from app.storage_cleanup import plan_cleanup

    settings = _cfg()
    verdict = evaluate(settings)
    target = verdict.get('target_bytes') or reclaim_bytes(settings)
    plan = plan_cleanup(target, cfg=settings)
    from app.storage_cleanup import log_never_delete
    log_never_delete(plan.get('protected_hashes'), list(settings.get('protected_tags') or []))   # Ops: every Preview
    _note_names(plan.get('torrent_names') or {})

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
        # v260 (PM): the list Preview shows, in deletion order, the kept (Never delete) downloads in place,
        # excluded. Keep / Unkeep act on `hashes`; `kept_by_tags` can only be lifted in settings.
        'rows': [
            {
                'name': e['name'],
                'size_bytes': e['size_bytes'],
                'mtime': e['mtime'],
                'tracker_label': e.get('tracker_label'),
                'phase': e.get('phase', 'preferred'),
                'hashes': list(e.get('hashes') or []),
                'kept': bool(e.get('kept')),
                'kept_hashes': list(e.get('kept_hashes') or []),
                'kept_by_tags': list(e.get('kept_tags') or []),
                'torrent_name': e.get('torrent_name') or '',
            }
            for e in plan.get('rows', [])
        ],
    }


# ── Run ───────────────────────────────────────────────────────────────────

def run_cycle(dry_run: bool = False, force: bool = False) -> Dict[str, Any]:
    """One check, and a cleanup if the floor is breached.

    `force` skips only the free-space check — it is the "Run now" button, and it
    still respects the daily ceiling and every guard in `storage_cleanup`. There
    is deliberately no way to bypass those from the UI.
    """
    from app.storage_cleanup import run_cleanup

    settings = _cfg()
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
                f"daily ceiling reached — {_size(spent)} already deleted today")
            return result
        want = verdict['reclaim_bytes']
        verdict['target_bytes'] = want if not cap else min(want, cap - spent)
        result['triggered'] = True
        result['reason'] = (f"manual run — reclaiming "
                            f"{_size(verdict['target_bytes'])}")

    if not result['triggered']:
        return result

    target = verdict['target_bytes']

    if dry_run:
        from app.storage_cleanup import plan_cleanup
        plan = plan_cleanup(target, cfg=settings)
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

    # Through qBittorrent (deleteFiles=true) only; no qBittorrent or no Plex, no delete.
    outcome = run_cleanup(target, cfg=settings)
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


def _size(n: Any) -> str:
    """Bytes as the settings card shows them (formatBytes): TB, GB or MB, so a
    few MB never reads as "0 GB"."""
    n = n or 0
    if n >= 1024 ** 4:
        tb = n / 1024 ** 4
        return f"{tb:.0f} TB" if tb >= 10 else f"{tb:.1f} TB"
    if n >= GIB:
        return f"{round(n / GIB)} GB"
    return f"{round(n / 1024 ** 2)} MB"


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
    parts = [f"{verb} {_size(freed)} across {len(r.get('deleted', []))} entries"]
    pref = r.get('freed_preferred_bytes', 0)
    fall = r.get('freed_fallback_bytes', 0)
    # Named whenever the run had to reach past the preferred trackers — that is
    # the signal the preferred pool has run dry, and it should never be silent.
    if fall:
        parts.append(f"{_size(pref)} from chosen trackers + {_size(fall)} other" if pref
                     else f"{_size(fall)} from other trackers")
    if r.get('short'):
        parts.append('short of target — ran out of eligible entries')
    if r.get('failed'):
        parts.append(f"{len(r['failed'])} failed to delete")
    if not r.get('dry_run'):
        parts.append(f"{_size(r.get('free_after_bytes', 0))} free now")
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
    from app import storage_cleanup
    out['running'] = bool(out['running'] or storage_cleanup.running())
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

def startup_seed_and_log() -> None:
    """Boot (minimal builds): seed the settings file (defaults, off), then, in the background so boot doesn't
    wait on qBittorrent, the one-time download_monitor.json prune (dlmon_prune_v260) and the Never delete
    log line."""
    from app.automation import dg_store
    cfg = dg_store.seed()
    threading.Thread(target=lambda: _boot_reads(cfg), daemon=True, name='downloads-guard-never-delete').start()


def _boot_reads(cfg) -> None:
    from app import dlmon_prune, storage_cleanup
    _quiet(dlmon_prune.run_once)
    _quiet(storage_cleanup.log_never_delete_now, cfg)
    _quiet(fill_names)                                   # v261: the seeded hash's name, read lazily from qBit


# ── v261: Never delete names (qBit's, read-only; display only, the logs stay last-4) ──

_names_cache: Dict[str, str] = {}
_names_state = {'running': False, 'at': 0.0}
_names_lock = threading.Lock()
NAMES_RETRY_SECONDS = 300


def _note_names(found: Dict[str, str]) -> None:
    """Remember every name qBit just listed (for Keep) and store the Never delete ones."""
    from app.automation import dg_store
    if not found:
        return
    with _names_lock:
        _names_cache.clear()
        _names_cache.update(found)
    dg_store.remember_names(found)


def known_names(hashes: List[str]) -> Dict[str, str]:
    with _names_lock:
        return {h: _names_cache[h] for h in hashes if h in _names_cache}


def fill_names() -> None:
    """Read qBit's list (torrents/info, read-only) when a Never delete hash has no name yet."""
    from app.automation import dg_store
    from app import storage_cleanup
    with _names_lock:
        if _names_state['running']:
            return
        _names_state['running'] = True
        _names_state['at'] = time.time()
    try:
        missing = [h for h in dg_store.settings()['protected_hashes'] if h not in dg_store.names()]
        if missing:
            _note_names(storage_cleanup.torrent_names(storage_cleanup.fetch_torrents()))
    finally:
        with _names_lock:
            _names_state['running'] = False


def fill_names_later() -> None:
    """Settings view: a missing name is read in the background, at most every NAMES_RETRY_SECONDS."""
    from app.automation import dg_store
    with _names_lock:
        if _names_state['running'] or time.time() - _names_state['at'] < NAMES_RETRY_SECONDS:
            return
    hashes = dg_store.settings()['protected_hashes']
    if hashes and any(h not in dg_store.names() for h in hashes):
        threading.Thread(target=lambda: _quiet(fill_names), daemon=True, name='downloads-guard-names').start()


def _quiet(fn, *a) -> None:
    try:
        fn(*a)
    except Exception as e:                                   # noqa: BLE001
        logger.warning('Downloads guard: never-delete check failed (%s)', type(e).__name__)


def start_downloads_guard_thread() -> None:
    from app import instance as _instance
    if not _instance.writes_allowed('downloads_guard'):   # LUMA_INSTANCE=test: off unless opted in
        return None
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
            settings = _cfg()
            interval = check_interval_seconds(settings)
            if enabled(settings):
                _guarded()
        except Exception as e:
            logger.error('Downloads Guard scheduler error: %s', e, exc_info=True)
        time.sleep(interval)
