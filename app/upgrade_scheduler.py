"""
Daily Upgrade Pipeline Scheduler
Background daemon that runs once per day at a configurable hour:
  1. Sync all Plex libraries into the upgrade DB
  2. Run a full batch search against indexers
  3. Enrich results with Rotten Tomatoes scores

Designed to finish before any downstream upgrade-grab job runs.
"""

from app.err_text import public as _err_public
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from app.automation_features import is_enabled as _automation_enabled

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────
_last_status: Dict[str, Any] = {
    'running': False,
    'last_run': None,
    'last_result': None,
}
_lock = threading.Lock()
_DEFAULT_RUN_HOUR = 3       # 3 AM local time
_INITIAL_DELAY = 90         # seconds before first check (let other services start)
_BATCH_SIZE = 100           # movies per batch (larger than manual since it runs at night)
_RT_ENRICH_LIMIT = 100      # RT scores to fetch per run


def _log_path() -> Path:
    docker_data = Path('/app/data')
    if docker_data.is_dir():
        return docker_data / 'upgrade_scheduler_log.json'
    return Path(__file__).resolve().parent.parent / 'data' / 'upgrade_scheduler_log.json'


def _read_log():
    try:
        p = _log_path()
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return []


def _write_log(entries):
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries[-50:], indent=None))
    except Exception as e:
        logger.warning(f'Failed to write upgrade scheduler log: {e}')



# PERSIST_LAST_RUN:upgrade_scheduler
def _state_path() -> Path:
    return _log_path().parent / 'upgrade_scheduler_state.json'


def _read_cycle_state() -> Dict[str, Any]:
    try:
        p = _state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f'Failed to read upgrade scheduler state: {e}')
    return {}


def _write_cycle_state(state: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception as e:
        logger.warning(f'Failed to write upgrade scheduler state: {e}')


_hydrated = False


def _hydrate_status() -> None:
    global _hydrated
    with _lock:
        if _hydrated:
            return
        _hydrated = True
        state = _read_cycle_state()
        if state.get('last_run') and not _last_status.get('last_run'):
            _last_status['last_run'] = state['last_run']
            _last_status['last_result'] = state.get('last_result')
        elif not _last_status.get('last_run'):
            # Seed from the newest log row until a cycle writes state.
            log = _read_log()
            if log and isinstance(log[-1], dict) and log[-1].get('run_at'):
                _last_status['last_run'] = log[-1]['run_at']


def get_status() -> Dict[str, Any]:
    _hydrate_status()
    with _lock:
        return dict(_last_status)


def get_log():
    entries = _read_log()
    entries.reverse()
    return entries


def trigger_manual_run() -> None:
    """Fire a one-off pipeline run in a background thread."""
    t = threading.Thread(target=lambda: _run_pipeline(force=True), daemon=True)
    t.start()


def start_upgrade_scheduler_thread() -> None:
    from app import instance as _instance
    if not _instance.writes_allowed('upgrade_scheduler'):   # LUMA_INSTANCE=test: off unless opted in
        return None
    t = threading.Thread(target=_scheduler_loop, daemon=True, name='upgrade-scheduler')
    t.start()
    logger.info('Daily upgrade scheduler started')


# ── Internal ──────────────────────────────────────────────────────

def _scheduler_loop() -> None:
    """Sleep until the configured hour, run the pipeline, repeat daily."""
    time.sleep(_INITIAL_DELAY)

    while True:
        try:
            from app.settings_manager import SettingsManager
            sm = SettingsManager()
            run_hour = sm.get('automation.upgrade_scheduler.run_hour', _DEFAULT_RUN_HOUR)
        except Exception:
            run_hour = _DEFAULT_RUN_HOUR

        # Calculate seconds until next run
        now = datetime.now()
        target = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()

        logger.info(
            f"Upgrade scheduler: next run at {target.strftime('%Y-%m-%d %H:%M')}"
            f" ({wait_seconds / 3600:.1f}h from now)")
        time.sleep(wait_seconds)

        try:
            _run_pipeline()
        except Exception as e:
            logger.error(f'Upgrade scheduler pipeline error: {e}', exc_info=True)


def _run_pipeline(force: bool = False) -> None:
    from app.settings_manager import SettingsManager
    from app.plex_service import plex_service

    settings = SettingsManager()

    if not force:
        enabled = _automation_enabled(settings, 'upgrade_scheduler')
        if not enabled:
            logger.debug('Upgrade scheduler disabled, skipping')
            return

    with _lock:
        if _last_status['running']:
            logger.warning('Upgrade pipeline already running, skipping')
            return
        _last_status['running'] = True

    start = time.time()
    result: Dict[str, Any] = {'steps': {}}

    try:
        from app.upgrade_finder import UpgradeFinder
        finder = UpgradeFinder(plex_service, settings)

        # Step 1: Sync all Plex libraries
        logger.info('[upgrade-scheduler] Step 1: Syncing all libraries...')
        finder.sync_all_libraries()
        progress = finder.get_progress()
        result['steps']['sync'] = {
            'total_in_library': progress.get('total_in_library', 0),
        }
        logger.info(
            f"[upgrade-scheduler] Sync done — "
            f"{progress.get('total_in_library', 0)} movies in library")

        # Step 2: Run batch searches until all movies are covered or we hit a cap
        logger.info('[upgrade-scheduler] Step 2: Running batch searches...')
        total_searched = 0
        total_upgrades = 0
        max_batches = 2  # 200 titles a night keeps per-title tracker searches low

        for i in range(max_batches):
            batch_result = finder.run_batch(batch_size=_BATCH_SIZE)
            status = batch_result.get('status', '')

            if status == 'complete':
                logger.info('[upgrade-scheduler] All movies searched')
                break
            if status == 'already_running':
                logger.info('[upgrade-scheduler] Batch already running, waiting...')
                time.sleep(30)
                continue

            total_searched += batch_result.get('movies_searched', 0)
            total_upgrades += batch_result.get('upgrades_found', 0)
            logger.info(
                f"[upgrade-scheduler] Batch {i + 1}: "
                f"searched {batch_result.get('movies_searched', 0)}, "
                f"found {batch_result.get('upgrades_found', 0)} upgrades")

            # Brief pause between batches to be kind to indexers
            time.sleep(2)

        result['steps']['batch'] = {
            'total_searched': total_searched,
            'total_upgrades': total_upgrades,
        }

        # Step 3: Enrich RT scores
        logger.info('[upgrade-scheduler] Step 3: Enriching RT scores...')
        rt_result = finder.enrich_rt_scores(limit=_RT_ENRICH_LIMIT)
        result['steps']['rt_enrich'] = rt_result
        logger.info(
            f"[upgrade-scheduler] RT enrichment: "
            f"{rt_result.get('enriched', 0)} movies scored")

        # Step 4: New releases scan (recent 4K remuxes from trackers)
        logger.info('[upgrade-scheduler] Step 4: Scanning new 4K remux releases...')
        try:
            from app.explore_cache import explore_cache
            new_result = finder.scan_new_releases(explore_cache=explore_cache)
            result['steps']['new_releases'] = new_result
            logger.info(
                f"[upgrade-scheduler] New releases: "
                f"{new_result.get('matches', 0)} upgrade matches "
                f"from {new_result.get('remuxes_found', 0)} remuxes")
        except Exception as e:
            logger.warning(f'[upgrade-scheduler] New releases scan failed: {e}')
            result['steps']['new_releases'] = {'status': 'error', 'error': _err_public(e, 'Step failed', 'upgrade_scheduler')}

        elapsed = round(time.time() - start, 1)
        result['status'] = 'ok'
        result['duration_seconds'] = elapsed

        with _lock:
            _last_status['last_run'] = datetime.utcnow().isoformat() + 'Z'
            _last_status['last_result'] = result
            snap = {
                'last_run': _last_status['last_run'],
                'last_result': {
                    k: v for k, v in (result or {}).items()
                    if k != 'steps'
                },
            }
        _write_cycle_state(snap)

        # Append to persistent log
        log_entry = {
            'run_at': datetime.utcnow().isoformat() + 'Z',
            'duration_seconds': elapsed,
            'library_size': progress.get('total_in_library', 0),
            'searched': total_searched,
            'upgrades_found': total_upgrades,
            'rt_enriched': rt_result.get('enriched', 0),
        }
        existing = _read_log()
        existing.append(log_entry)
        _write_log(existing)

        logger.info(
            f"[upgrade-scheduler] Pipeline complete in {elapsed}s — "
            f"searched {total_searched}, found {total_upgrades} upgrades, "
            f"enriched {rt_result.get('enriched', 0)} RT scores")

    except Exception as e:
        result['status'] = 'error'
        result['error'] = _err_public(e, 'Run failed', 'upgrade_scheduler')
        logger.error(f'[upgrade-scheduler] Pipeline failed: {e}', exc_info=True)
    finally:
        with _lock:
            _last_status['running'] = False
