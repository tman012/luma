"""US digital (TMDb type-4) release dates for watchlist movies.

Fetches GET /3/movie/{id}/release_dates, picks iso_3166_1 == "US" and
type == 4 (Digital), earliest date if multiple. Persists under
data/digital_release_cache.json keyed by tmdb_id. Refreshes entries older
than CACHE_TTL_SEC (daily). Used by /api/watchlist to enrich + sort.
TV is out of scope — do not call for series.

Fallback + stickiness: when TMDb has no US type-4 date (or the fetch fails)
we fall back to Radarr's own digitalRelease for the movie, and a previously
known date is never replaced by None. TMDb pulled Resident Evil's US digital
row after we had seen Nov 3 and the next refresh blanked it; Verity only ever
had a date in Radarr.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
TMDB_BASE = "https://api.themoviedb.org/3"
CACHE_TTL_SEC = 24 * 3600
FETCH_WORKERS = 4
REQUEST_PAUSE = 0.05

_lock = threading.Lock()
_mem: Optional[Dict[str, Any]] = None


def _data_dir() -> Path:
    docker_data = Path("/app/data")
    if docker_data.is_dir():
        return docker_data
    db_path = os.environ.get("DATABASE_PATH", "")
    if db_path:
        return Path(db_path).parent
    return Path(__file__).resolve().parent.parent / "data"


def _cache_path() -> Path:
    return _data_dir() / "digital_release_cache.json"


def _api_key() -> str:
    key = (os.environ.get("TMDB_API_KEY") or "").strip()
    if key:
        return key
    try:
        from app.settings_manager import SettingsManager
        return (SettingsManager().get("apis.tmdb.api_key") or "").strip()
    except Exception:
        return ""


def _empty_cache() -> Dict[str, Any]:
    return {"entries": {}, "updated_at": 0}


def _load_cache() -> Dict[str, Any]:
    global _mem
    with _lock:
        if _mem is not None:
            return _mem
    path = _cache_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                with _lock:
                    _mem = data
                return data
    except Exception as e:
        logger.warning("digital_release_cache: bad cache, starting fresh: %s", e)
    empty = _empty_cache()
    with _lock:
        _mem = empty
    return empty


def _save_cache(data: Dict[str, Any]) -> None:
    global _mem
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.time()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)
    with _lock:
        _mem = data


def _tmdb_release_dates(tmdb_id: int, api_key: str) -> Optional[Dict[str, Any]]:
    try:
        resp = requests.get(
            f"{TMDB_BASE}/movie/{tmdb_id}/release_dates",
            params={"api_key": api_key},
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", "2"))
            time.sleep(min(retry, 15))
            resp = requests.get(
                f"{TMDB_BASE}/movie/{tmdb_id}/release_dates",
                params={"api_key": api_key},
                timeout=10,
            )
        if not resp.ok:
            logger.debug("TMDb release_dates %s -> %s", tmdb_id, resp.status_code)
            return None
        return resp.json()
    except Exception as e:
        logger.debug("TMDb release_dates %s failed: %s", tmdb_id, e)
        return None


def _parse_us_digital(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return earliest US Digital (type 4) date as YYYY-MM-DD, or None."""
    if not payload:
        return None
    dates: List[str] = []
    for country in payload.get("results") or []:
        if country.get("iso_3166_1") != "US":
            continue
        for rel in country.get("release_dates") or []:
            if rel.get("type") != 4:
                continue
            raw = (rel.get("release_date") or "")[:10]
            if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
                dates.append(raw)
    if not dates:
        return None
    return min(dates)


def format_digital_label(iso_date: Optional[str]) -> Optional[str]:
    """Compact label: uppercase 3-letter month + day, e.g. 'JAN 9'.

    No year and no 'Digital release' prefix — shown centered above the title under the poster (not as an on-poster overlay).
    """
    if not iso_date or len(iso_date) < 10:
        return None
    try:
        dt = datetime.strptime(iso_date[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return f"{dt.strftime('%b').upper()} {dt.day}"

def _fetch_one(tmdb_id: int, api_key: str) -> Tuple[int, Optional[str]]:
    payload = _tmdb_release_dates(tmdb_id, api_key)
    time.sleep(REQUEST_PAUSE)
    return tmdb_id, _parse_us_digital(payload)


_RADARR_TTL_SEC = 10 * 60
_radarr_mem: Dict[str, Any] = {"ts": 0.0, "dates": {}}


def _radarr_digital_dates() -> Dict[int, str]:
    """{tmdb_id: YYYY-MM-DD} from Radarr movie.digitalRelease (our own data).

    Cached briefly; returns {} when Radarr is unreachable/unconfigured.
    """
    now = time.time()
    with _lock:
        if now - _radarr_mem["ts"] < _RADARR_TTL_SEC and _radarr_mem["dates"]:
            return dict(_radarr_mem["dates"])
    out: Dict[int, str] = {}
    try:
        from app.radarr_api import RadarrAPI
        from app.settings_manager import SettingsManager
        settings = SettingsManager()
        url = settings.get("radarr.url", "")
        key = settings.get("radarr.api_key", "")
        if not url or not key:
            return {}
        for m in RadarrAPI(url, key).get_movies() or []:
            tid = m.get("tmdbId")
            raw = (m.get("digitalRelease") or "")[:10]
            if tid and len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
                out[int(tid)] = raw
    except Exception as e:
        logger.debug("digital_release_cache: radarr fallback unavailable: %s", e)
        return {}
    with _lock:
        _radarr_mem["ts"] = now
        _radarr_mem["dates"] = out
    return dict(out)


def get_digital_dates(tmdb_ids: List[int], *, force_refresh: bool = False) -> Dict[int, Optional[str]]:
    """Return {tmdb_id: YYYY-MM-DD|None} for the given ids, using cache.

    Stale or missing entries are fetched from TMDb in a small thread pool.
    Negative cache (None) is stored so we do not re-hit every open.
    """
    ids = [int(i) for i in tmdb_ids if i]
    if not ids:
        return {}

    cache = _load_cache()
    entries: Dict[str, Any] = cache.setdefault("entries", {})
    now = time.time()
    result: Dict[int, Optional[str]] = {}
    to_fetch: List[int] = []

    for tid in ids:
        key = str(tid)
        ent = entries.get(key)
        if (
            not force_refresh
            and isinstance(ent, dict)
            and (now - float(ent.get("fetched_at") or 0)) < CACHE_TTL_SEC
            and "date" in ent
        ):
            result[tid] = ent.get("date")  # may be None
        else:
            to_fetch.append(tid)

    if to_fetch:
        api_key = _api_key()
        fetched: Dict[int, Optional[str]] = {}
        if not api_key:
            # Still fall through to the Radarr fallback / known-date keep below.
            logger.warning("digital_release_cache: no TMDB API key")
        else:
            with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(to_fetch))) as pool:
                futs = {pool.submit(_fetch_one, tid, api_key): tid for tid in to_fetch}
                for fut in as_completed(futs):
                    tid = futs[fut]
                    try:
                        _, date = fut.result()
                        fetched[tid] = date
                    except Exception as e:
                        logger.debug("digital_release_cache fetch %s: %s", tid, e)
                        prior = entries.get(str(tid))
                        if isinstance(prior, dict) and "date" in prior:
                            fetched[tid] = prior.get("date")
                        else:
                            continue

        # Ids TMDb gave us no US digital date for (empty, failed or errored).
        missing = [tid for tid in to_fetch if not fetched.get(tid)]
        radarr_dates = _radarr_digital_dates() if missing else {}

        for tid in to_fetch:
            prior = entries.get(str(tid))
            prior_date = prior.get("date") if isinstance(prior, dict) else None
            tmdb_date = fetched.get(tid)
            if tmdb_date:
                date, source = tmdb_date, "tmdb"
            elif radarr_dates.get(tid):
                date, source = radarr_dates[tid], "radarr"
            elif prior_date:
                # Never let a failed/empty fetch clobber a date we already knew.
                date = prior_date
                source = (prior.get("source") if isinstance(prior, dict) else None) or "sticky"
                logger.info(
                    "digital_release_cache: no date from TMDb/Radarr for %s — "
                    "keeping known %s", tid, prior_date)
            else:
                date, source = None, None
            fetched[tid] = date
            entry = {"date": date, "fetched_at": now}
            if source:
                entry["source"] = source
            entries[str(tid)] = entry
            result[tid] = date

        if to_fetch:
            try:
                _save_cache(cache)
            except Exception as e:
                logger.warning("digital_release_cache: save failed: %s", e)

    return result


def enrich_watchlist_items(items: List[Dict[str, Any]]) -> None:
    """Mutate watchlist movie dicts with digital_release_date + label.

    Adds:
      digital_release_date: ISO YYYY-MM-DD or None
      digital_release_label: human string or None
    """
    ids = [m.get("tmdb_id") for m in items if m.get("tmdb_id")]
    dates = get_digital_dates(ids)
    for m in items:
        tid = m.get("tmdb_id")
        iso = dates.get(int(tid)) if tid else None
        m["digital_release_date"] = iso
        m["digital_release_label"] = format_digital_label(iso)
