"""Watchlist Digital Dates — morning check for newly dated watchlist movies.

Loads the current movie watchlist (Radarr monitored, no file — same as
/api/watchlist), force-refreshes US digital (TMDb type-4) dates via
digital_release_cache, compares to the previous-run snapshot, and pushes
when an item newly gains a date or its date changes.

Settings under automation.watchlist_digital_dates.*:
    enabled (default False), hour (default 6), minute (default 0)
Schedule uses America/New_York.

State:    data/watchlist_digital_dates_state.json
Snapshot: data/watchlist_digital_dates_snapshot.json
Log:      data/watchlist_digital_dates_log.json
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
_DEFAULT_HOUR = 6
_DEFAULT_MINUTE = 0
_MAX_LOG_ENTRIES = 90
_CHECK_INTERVAL_SECONDS = 5 * 60
_SETTING_PREFIX = "automation.watchlist_digital_dates"

_last_status: Dict[str, Any] = {
    "running": False,
    "last_run": None,
    "last_result": None,
    "cycle_count": 0,
}
_lock = threading.Lock()
_hydrated = False


def _int_setting(settings, key: str, default: int, low: int, high: int) -> int:
    try:
        raw = settings.get(key, default)
        v = default if raw is None or raw == "" else int(raw)
    except (TypeError, ValueError):
        v = default
    return max(low, min(high, v))


def _scheduled_time(settings) -> str:
    hour = _int_setting(settings, f"{_SETTING_PREFIX}.hour", _DEFAULT_HOUR, 0, 23)
    minute = _int_setting(settings, f"{_SETTING_PREFIX}.minute", _DEFAULT_MINUTE, 0, 59)
    return f"{hour:02d}:{minute:02d}"


def _enabled(settings) -> bool:
    # Default OFF on new installs (existing installs were migrated explicitly).
    raw = settings.get(f"{_SETTING_PREFIX}.enabled", False)
    if raw is None:
        return True
    return bool(raw)


def _data_dir() -> Path:
    docker_data = Path("/app/data")
    if docker_data.is_dir():
        return docker_data
    db_path = os.environ.get("DATABASE_PATH", "")
    if db_path:
        return Path(db_path).parent
    return Path(__file__).resolve().parent.parent / "data"


def _state_path() -> Path:
    return _data_dir() / "watchlist_digital_dates_state.json"


def _snapshot_path() -> Path:
    return _data_dir() / "watchlist_digital_dates_snapshot.json"


def _log_path() -> Path:
    return _data_dir() / "watchlist_digital_dates_log.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data
    except Exception as e:
        logger.warning("watchlist_digital_dates: bad %s: %s", path.name, e)
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_state() -> Dict[str, Any]:
    data = _read_json(_state_path(), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("last_run_date", None)
    data.setdefault("last_scheduled_date", None)
    data.setdefault("last_scheduled_time", None)
    data.setdefault("last_run", None)
    data.setdefault("last_result", None)
    return data


def _write_state(state: Dict[str, Any]) -> None:
    try:
        _write_json(_state_path(), state)
    except Exception as e:
        logger.warning("watchlist_digital_dates: failed to write state: %s", e)


def _read_snapshot() -> Dict[str, Optional[str]]:
    """tmdb_id(str) -> ISO date or None."""
    data = _read_json(_snapshot_path(), {})
    if not isinstance(data, dict):
        return {}
    dates = data.get("dates") if "dates" in data else data
    if not isinstance(dates, dict):
        return {}
    out: Dict[str, Optional[str]] = {}
    for k, v in dates.items():
        out[str(k)] = v if v else None
    return out


def _write_snapshot(dates: Dict[str, Optional[str]], at: str) -> None:
    _write_json(
        _snapshot_path(),
        {"updated_at": at, "dates": dates},
    )


def upsert_snapshot_entry(tmdb_id: int, date: Optional[str]) -> None:
    """Upsert one tmdb_id into the morning-job snapshot.

    Called on watchlist add after digital_release_cache prefetch so the next
    scheduled run does not treat an already-known digital date as newly dated.
    No-ops quietly if the snapshot file is missing (first run will seed).
    """
    try:
        key = str(int(tmdb_id))
        prev = _read_snapshot()
        if not prev and not _snapshot_path().exists():
            # No baseline yet — leave first morning run to seed everything.
            return
        if not date and prev.get(key):
            return  # never blank a known date
        prev[key] = date if date else None
        at = _now_ny().strftime("%Y-%m-%dT%H:%M:%S%z")
        _write_snapshot(prev, at)
        logger.debug(
            "watchlist_digital_dates: snapshot upsert tmdb=%s date=%s",
            key,
            date,
        )
    except Exception as e:
        logger.warning("watchlist_digital_dates: snapshot upsert failed: %s", e)


def _append_log(entry: Optional[Dict[str, Any]]) -> None:
    if not entry:
        return
    try:
        existing = _read_json(_log_path(), [])
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        _write_json(_log_path(), existing[-_MAX_LOG_ENTRIES:])
    except Exception as e:
        logger.warning("watchlist_digital_dates: failed to write log: %s", e)


def _hydrate_status() -> None:
    global _hydrated
    if _hydrated:
        return
    with _lock:
        if _hydrated:
            return
        state = _read_state()
        if state.get("last_run"):
            _last_status["last_run"] = state["last_run"]
        if state.get("last_result") is not None:
            _last_status["last_result"] = state["last_result"]
        _hydrated = True


def _now_ny() -> datetime:
    return datetime.now(NY)


def _next_run_dt() -> Optional[datetime]:
    from app.settings_manager import SettingsManager
    settings = SettingsManager()
    if not _enabled(settings):
        return None
    at = _scheduled_time(settings)
    hour, minute = (int(p) for p in at.split(":"))
    now = _now_ny()
    state = _read_state()
    today = now.strftime("%Y-%m-%d")
    done = (
        state.get("last_scheduled_date") == today
        and state.get("last_scheduled_time") == at
    )
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if done:
        return slot + timedelta(days=1)
    if now >= slot:
        return now.replace(second=0, microsecond=0)
    return slot


def _next_run() -> Optional[str]:
    try:
        nxt = _next_run_dt()
        return nxt.isoformat() if nxt else None
    except Exception as e:
        logger.warning("watchlist_digital_dates: could not compute next run: %s", e)
        return None


def get_status() -> Dict[str, Any]:
    _hydrate_status()
    with _lock:
        out = dict(_last_status)
    out["next_run"] = _next_run()
    out["tz"] = "America/New_York"
    out["server_tz"] = "America/New_York"
    return out


def get_log() -> List[Dict[str, Any]]:
    try:
        entries = _read_json(_log_path(), [])
    except Exception:
        return []
    out = [e for e in entries if isinstance(e, dict)]
    out.reverse()
    return out


def _load_watchlist_movies() -> List[Dict[str, Any]]:
    """Same set as /api/watchlist: Radarr monitored, no file."""
    from app.radarr_api import RadarrAPI
    from app.settings_manager import SettingsManager

    settings = SettingsManager()
    radarr_url = settings.get("radarr.url", "")
    radarr_key = settings.get("radarr.api_key", "")
    if not radarr_url or not radarr_key:
        raise RuntimeError("Radarr not configured")

    radarr = RadarrAPI(radarr_url, radarr_key)
    movies = radarr.get_movies() or []
    out: List[Dict[str, Any]] = []
    for movie in movies:
        if not movie.get("monitored"):
            continue
        if movie.get("hasFile"):
            continue
        tmdb_id = movie.get("tmdbId")
        if not tmdb_id:
            continue
        images = movie.get("images") or []
        poster = ""
        if images:
            poster = (images[0].get("remoteUrl") or "").replace(
                "https://image.tmdb.org/t/p/original", ""
            )
        out.append({
            "tmdb_id": int(tmdb_id),
            "title": movie.get("title") or "Unknown",
            "year": movie.get("year"),
            "poster_path": poster,
        })
    return out


def _tmdb_backdrop(tmdb_id: int) -> str:
    try:
        from app.digital_release_cache import _api_key
        key = _api_key()
        if not key:
            return ""
        import requests
        resp = requests.get(
            f"https://api.themoviedb.org/3/movie/{tmdb_id}",
            params={"api_key": key},
            timeout=8,
        )
        if not resp.ok:
            return ""
        path = resp.json().get("backdrop_path") or ""
        if path:
            return f"https://image.tmdb.org/t/p/w780{path}"
    except Exception as e:
        logger.debug("watchlist_digital_dates: backdrop %s: %s", tmdb_id, e)
    return ""


def _notify_change(item: Dict[str, Any], kind: str, old_date: Optional[str], new_date: str) -> None:
    from app.digital_release_cache import format_digital_label
    from app.push_service import send_push_notification

    title = item.get("title") or "Movie"
    year = item.get("year")
    display = f"{title} ({year})" if year else title
    label = format_digital_label(new_date) or new_date
    if kind == "new":
        push_title = f"{display} digital date set"
        body = f"US digital: {label}"
    else:
        old_label = format_digital_label(old_date) if old_date else "—"
        push_title = f"{display} digital date updated"
        body = f"{old_label} → {label}"

    backdrop = _tmdb_backdrop(int(item["tmdb_id"]))
    send_push_notification(
        title=push_title,
        body=body,
        tag=f"digital-date-{item['tmdb_id']}",
        url=f"/?modal=movie&tmdb={item['tmdb_id']}",
        image=backdrop,
    )


def run_cycle(*, dry_run: bool = False, scheduled: bool = False,
              notify: bool = True) -> Dict[str, Any]:
    """Refresh watchlist digital dates, diff against snapshot, notify on changes.

    notify=False rewrites the snapshot from current data without pushing —
    used to re-baseline after a data fix so the next 6am run stays quiet.
    """
    from app.digital_release_cache import format_digital_label, get_digital_dates
    from app.settings_manager import SettingsManager

    settings = SettingsManager()
    started = datetime.now(timezone.utc).isoformat()
    result: Dict[str, Any] = {
        "started_at": started,
        "dry_run": dry_run,
        "checked": 0,
        "with_date": 0,
        "without_date": 0,
        "changes": [],
        "new_count": 0,
        "changed_count": 0,
        "notified": 0,
        "reason": None,
        "error": None,
    }

    try:
        items = _load_watchlist_movies()
    except Exception as e:
        result["error"] = str(e)
        result["reason"] = f"Failed to load watchlist: {e}"
        logger.error("watchlist_digital_dates: load watchlist failed: %s", e)
        if scheduled and not dry_run:
            _stamp_scheduled(settings)
        return result

    result["checked"] = len(items)
    ids = [m["tmdb_id"] for m in items]
    # Force refresh so the morning run sees TMDb updates even if cache is warm.
    dates = get_digital_dates(ids, force_refresh=True)

    prev = _read_snapshot()
    new_snapshot: Dict[str, Optional[str]] = {}
    changes: List[Dict[str, Any]] = []

    for m in items:
        tid = int(m["tmdb_id"])
        key = str(tid)
        new_date = dates.get(tid)  # may be None
        old_known = prev.get(key) if key in prev else None
        if not new_date and old_known:
            # A failed/empty refresh must not blank a known date — otherwise
            # the date "reappearing" later fires a false "newly dated" push.
            new_date = old_known
        new_snapshot[key] = new_date
        if new_date:
            result["with_date"] += 1
        else:
            result["without_date"] += 1

        old_date = prev.get(key) if key in prev else None
        # First-ever snapshot: don't treat everything as "new".
        if not prev:
            continue
        if new_date and not old_date:
            kind = "new"
        elif new_date and old_date and new_date != old_date:
            kind = "changed"
        else:
            continue
        entry = {
            "tmdb_id": tid,
            "title": m.get("title"),
            "year": m.get("year"),
            "kind": kind,
            "old_date": old_date,
            "new_date": new_date,
            "label": format_digital_label(new_date),
        }
        changes.append(entry)

    result["changes"] = changes
    result["new_count"] = sum(1 for c in changes if c["kind"] == "new")
    result["changed_count"] = sum(1 for c in changes if c["kind"] == "changed")

    if not prev:
        result["reason"] = (
            f"Seeded snapshot for {len(items)} watchlist title(s); "
            "no notifications on first run"
        )
        logger.info("watchlist_digital_dates: %s", result["reason"])
    elif not changes:
        result["reason"] = (
            f"Checked {len(items)} title(s); no new or changed digital dates"
        )
        logger.info("watchlist_digital_dates: %s", result["reason"])
    else:
        result["reason"] = (
            f"Checked {len(items)}: {result['new_count']} newly dated, "
            f"{result['changed_count']} date changed"
        )
        logger.info(
            "watchlist_digital_dates: %s — %s",
            result["reason"],
            "; ".join(
                f"{c['title']} ({c['kind']}: {c.get('old_date')}→{c['new_date']})"
                for c in changes
            ),
        )

    if dry_run:
        result["reason"] = (result.get("reason") or "") + " (dry run — no notify/snapshot)"
        return result

    # Persist snapshot even when empty so we have a baseline.
    _write_snapshot(new_snapshot, started)

    if changes and not notify:
        result["reason"] = (result.get("reason") or "") + " (re-baselined — no pushes)"
    if changes and notify:
        by_id = {int(m["tmdb_id"]): m for m in items}
        for c in changes:
            try:
                _notify_change(by_id[c["tmdb_id"]], c["kind"], c.get("old_date"), c["new_date"])
                result["notified"] += 1
            except Exception as e:
                logger.warning(
                    "watchlist_digital_dates: push failed for %s: %s",
                    c.get("title"), e,
                )

    if scheduled:
        _stamp_scheduled(settings)

    return result


def summarise(result: Dict[str, Any]) -> str:
    if result.get("error"):
        return result["error"]
    if result.get("reason"):
        return result["reason"]
    n = result.get("new_count") or 0
    c = result.get("changed_count") or 0
    checked = result.get("checked") or 0
    if n or c:
        return f"Checked {checked}: {n} newly dated, {c} changed"
    return f"Checked {checked}; nothing new"


def _log_entry(result: Dict[str, Any], at: str) -> Dict[str, Any]:
    if result.get("error"):
        outcome = "error"
    elif (result.get("new_count") or 0) or (result.get("changed_count") or 0):
        outcome = "found"
    else:
        outcome = "nothing"
    return {
        "at": at,
        "outcome": outcome,
        "summary": summarise(result),
        "checked": result.get("checked"),
        "with_date": result.get("with_date"),
        "without_date": result.get("without_date"),
        "new_count": result.get("new_count") or 0,
        "changed_count": result.get("changed_count") or 0,
        "notified": result.get("notified") or 0,
        "changes": result.get("changes") or [],
        "dry_run": bool(result.get("dry_run")),
    }


def preview() -> Dict[str, Any]:
    return run_cycle(dry_run=True)


def rebaseline() -> Dict[str, Any]:
    """Refresh dates and rewrite the snapshot without sending pushes."""
    result = run_cycle(notify=False)
    logger.info("watchlist_digital_dates: rebaseline — %s", summarise(result))
    return result


def trigger_manual_run(dry_run: bool = False) -> None:
    threading.Thread(target=lambda: _guarded(dry_run), daemon=True).start()


def _stamp_scheduled(settings) -> None:
    state = _read_state()
    now = _now_ny()
    state["last_scheduled_date"] = now.strftime("%Y-%m-%d")
    state["last_scheduled_time"] = _scheduled_time(settings)
    _write_state(state)


def _guarded(dry_run: bool, scheduled: bool = False) -> None:
    _hydrate_status()
    with _lock:
        if _last_status["running"]:
            logger.warning("watchlist_digital_dates already running, skipping")
            return
        _last_status["running"] = True
    try:
        result = run_cycle(dry_run=dry_run, scheduled=scheduled)
    except Exception as e:
        logger.error("watchlist_digital_dates cycle error: %s", e, exc_info=True)
        result = {
            "error": f"Run failed: {e}",
            "dry_run": dry_run,
            "checked": 0,
            "changes": [],
            "new_count": 0,
            "changed_count": 0,
            "notified": 0,
        }

    at = datetime.now(timezone.utc).isoformat()
    with _lock:
        _last_status["last_result"] = result
        if not dry_run:
            _last_status["last_run"] = at
            _last_status["cycle_count"] += 1
        _last_status["running"] = False

    logger.info("watchlist_digital_dates: %s", summarise(result))

    if dry_run:
        return
    try:
        state = _read_state()
        state["last_run"] = at
        # Keep log-friendly slim result in state (full changes still in log).
        slim = dict(result)
        if len(slim.get("changes") or []) > 40:
            slim["changes"] = slim["changes"][:40]
        state["last_result"] = slim
        _write_state(state)
    except Exception as e:
        logger.warning("watchlist_digital_dates: could not persist last run: %s", e)
    _append_log(_log_entry(result, at))


def start_watchlist_digital_dates_thread() -> None:
    _hydrate_status()
    t = threading.Thread(
        target=_scheduler_loop, daemon=True, name="watchlist-digital-dates"
    )
    t.start()
    logger.info(
        "Watchlist Digital Dates scheduler started "
        "(America/New_York; default 06:00)"
    )


def _scheduler_loop() -> None:
    time.sleep(180)  # after Franchise Completer's startup burst
    while True:
        try:
            _maybe_run()
        except Exception as e:
            logger.error(
                "watchlist_digital_dates scheduler error: %s", e, exc_info=True
            )
        time.sleep(_sleep_seconds())


def _sleep_seconds() -> float:
    try:
        nxt = _next_run_dt()
        if nxt:
            # Compare in NY-aware space
            now = _now_ny()
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=NY)
            delta = (nxt - now).total_seconds()
            if 0 < delta < _CHECK_INTERVAL_SECONDS:
                return delta + 1
    except Exception as e:
        logger.warning("watchlist_digital_dates: falling back to poll: %s", e)
    return _CHECK_INTERVAL_SECONDS


def _maybe_run() -> None:
    from app.settings_manager import SettingsManager
    settings = SettingsManager()

    if not _enabled(settings):
        return

    at = _scheduled_time(settings)
    hour, minute = (int(p) for p in at.split(":"))
    now = _now_ny()
    if now < now.replace(hour=hour, minute=minute, second=0, microsecond=0):
        return

    state = _read_state()
    today = now.strftime("%Y-%m-%d")
    if (
        state.get("last_scheduled_date") == today
        and state.get("last_scheduled_time") == at
    ):
        return

    logger.info("watchlist_digital_dates: starting morning run (scheduled %s ET)", at)
    _guarded(dry_run=False, scheduled=True)

