"""Read-only Sonarr status for the TV modal (show / season / episode).

Everything here is GET-only against Sonarr's v3 API. Results are cached
briefly (series list and per-series episodes/files ~60s, queue ~20s) so the
modal stays fast when it's opened repeatedly.
"""
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TTL = 60
_QUEUE_TTL = 10
_last_queue_sig: Dict[Tuple, Tuple] = {}
_MAX_KEYS = 256
_cache: Dict[Tuple, Tuple[float, Any]] = {}
_lock = threading.Lock()


def _cached(key: Tuple, ttl: int, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    if val is None:  # don't cache failures
        return None
    with _lock:
        _cache[key] = (now, val)
        if len(_cache) > _MAX_KEYS:
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[: len(_cache) - _MAX_KEYS]:
                _cache.pop(k, None)
    return val


def invalidate(series_id: Optional[int] = None, include_series: bool = False) -> None:
    """Drop cached queue/episode data (all series, or one) after a change."""
    with _lock:
        for k in list(_cache.keys()):
            if k[0] in ('queue', 'episodes', 'files') and (series_id is None or k[2] == series_id):
                _cache.pop(k, None)
            elif include_series and k[0] == 'series':
                _cache.pop(k, None)


class MonitorError(Exception):
    pass


def _put(sonarr, endpoint: str, body: Any) -> bool:
    """PUT that treats any 2xx as success (some Sonarr PUTs return no body)."""
    import requests
    try:
        r = requests.put(f"{sonarr.url}/api/v3/{endpoint}", headers={"X-Api-Key": sonarr.api_key},
                         json=body, timeout=20)
        if 200 <= r.status_code < 300:
            return True
        logger.error(f"Sonarr PUT {endpoint} -> HTTP {r.status_code}")
    except Exception as e:
        logger.error(f"Sonarr PUT {endpoint} failed: {type(e).__name__}")
    return False


def _search(sonarr, body: Dict) -> Optional[int]:
    """POST /api/v3/command (EpisodeSearch / SeasonSearch / SeriesSearch).
    Sonarr only searches monitored, missing episodes for these commands."""
    res = sonarr._request('POST', 'command', body, timeout=20)
    if isinstance(res, dict):
        return res.get('id')
    logger.warning(f"Sonarr {body.get('name')} command was not accepted")
    return None


def _ensure_series_monitored(sonarr, series_id: int) -> bool:
    """Monitoring an episode/season of an unmonitored series does nothing in
    Sonarr, so flip the series flag on (seasons/episodes untouched)."""
    series = sonarr._request('GET', f'series/{series_id}', timeout=15)
    if not isinstance(series, dict):
        raise MonitorError('Series not found in Sonarr')
    if series.get('monitored'):
        return False
    series['monitored'] = True
    if not _put(sonarr, f'series/{series_id}', series):
        raise MonitorError('Sonarr rejected the series update')
    return True


def set_episodes_monitored(sonarr, series_id: int, episode_ids: List[int], monitored: bool) -> Dict:
    """PUT /api/v3/episode/monitor {episodeIds, monitored}. No search is triggered."""
    ids = [int(i) for i in episode_ids if i]
    if not ids:
        raise MonitorError('No episodes')
    series_flipped = _ensure_series_monitored(sonarr, series_id) if monitored else False
    if monitored:
        _ensure_seasons_monitored_only(sonarr, series_id, ids)
    if not _put(sonarr, 'episode/monitor', {'episodeIds': ids, 'monitored': bool(monitored)}):
        raise MonitorError('Sonarr rejected the episode update')
    invalidate(series_id, include_series=True)
    command_id = None
    if monitored:
        # Read back: series, season and every requested episode must be monitored before we search.
        series = sonarr._request('GET', f'series/{series_id}', timeout=15) or {}
        eps = {e['id']: e for e in (sonarr._request('GET', 'episode', {'seriesId': series_id}, timeout=20) or [])}
        seasons_on = {x.get('seasonNumber') for x in series.get('seasons') or [] if x.get('monitored')}
        bad = [i for i in ids if not (eps.get(i) or {}).get('monitored') or (eps.get(i) or {}).get('seasonNumber') not in seasons_on]
        if not series.get('monitored') or bad:
            logger.error(f"Sonarr episode monitor read-back failed for series {series_id}: {bad}")
            raise MonitorError('Sonarr did not keep the episode monitored')
        for _ in range(2):
            command_id = _search(sonarr, {'name': 'EpisodeSearch', 'episodeIds': ids})
            if command_id is not None:
                break
            time.sleep(1.5)
        logger.info(f"Sonarr EpisodeSearch for series {series_id} episodes {ids}: command {command_id}")
    return {'episodeIds': ids, 'monitored': bool(monitored), 'seriesMonitored': True if series_flipped else None,
            'searching': command_id is not None, 'commandId': command_id}


def _ensure_seasons_monitored_only(sonarr, series_id: int, episode_ids: List[int]) -> None:
    """Sonarr skips episodes whose SEASON is unmonitored (RSS + automatic search), so turn the season
    on for each requested episode - but Sonarr cascades a season toggle to all its episodes, so put
    every other episode of that season back the way it was (nothing unrequested gets monitored)."""
    eps = sonarr._request('GET', 'episode', {'seriesId': series_id}, timeout=20) or []
    by_id = {e['id']: e for e in eps}
    want = {by_id[i]['seasonNumber'] for i in episode_ids if i in by_id}
    series = sonarr._request('GET', f'series/{series_id}', timeout=15)
    if not isinstance(series, dict):
        raise MonitorError('Series not found in Sonarr')
    off = [x for x in series.get('seasons') or [] if x.get('seasonNumber') in want and not x.get('monitored')]
    if not off:
        return
    before = {e['id']: bool(e.get('monitored')) for e in eps if e.get('seasonNumber') in want}
    for x in off:
        x['monitored'] = True
    if not _put(sonarr, f'series/{series_id}', series):
        raise MonitorError('Sonarr rejected the season update')
    after = sonarr._request('GET', 'episode', {'seriesId': series_id}, timeout=20) or []
    revert = [e['id'] for e in after if e['id'] in before and e['id'] not in episode_ids
              and bool(e.get('monitored')) and not before[e['id']]]
    if revert and not _put(sonarr, 'episode/monitor', {'episodeIds': revert, 'monitored': False}):
        raise MonitorError('Sonarr rejected the episode update')
    logger.info(f"Sonarr series {series_id}: monitored season(s) {sorted(want)}, kept {len(revert)} other episode(s) unmonitored")


def set_season_monitored(sonarr, series_id: int, season: int, monitored: bool) -> Dict:
    """Series PUT with seasons[n].monitored changed (Sonarr cascades it to that
    season's episodes, same as its own season toggle). No search is triggered."""
    series = sonarr._request('GET', f'series/{series_id}', timeout=15)
    if not isinstance(series, dict):
        raise MonitorError('Series not found in Sonarr')
    found = False
    for s in series.get('seasons') or []:
        if s.get('seasonNumber') == season:
            s['monitored'] = bool(monitored)
            found = True
    if not found:
        raise MonitorError(f'Season {season} not found in Sonarr')
    if monitored and not series.get('monitored'):
        series['monitored'] = True
    if not _put(sonarr, f'series/{series_id}', series):
        raise MonitorError('Sonarr rejected the season update')
    # Match the season's episodes, like Sonarr's own season toggle.
    eps = sonarr._request('GET', 'episode', {'seriesId': series_id}, timeout=20) or []
    ids = [e['id'] for e in eps if e.get('seasonNumber') == season and bool(e.get('monitored')) != bool(monitored)]
    if ids and not _put(sonarr, 'episode/monitor', {'episodeIds': ids, 'monitored': bool(monitored)}):
        raise MonitorError('Sonarr rejected the episode update')
    invalidate(series_id, include_series=True)
    command_id = _search(sonarr, {'name': 'SeasonSearch', 'seriesId': series_id, 'seasonNumber': season}) if monitored else None
    return {'season': season, 'monitored': bool(monitored), 'seriesMonitored': bool(series.get('monitored')),
            'searching': command_id is not None, 'commandId': command_id}


def _aired(ep: Dict, now_iso: str) -> bool:
    d = ep.get('airDateUtc')
    return bool(d) and d <= now_iso


def set_series_monitored(sonarr, series_id: int, monitored: bool, all_seasons: bool = False) -> Dict:
    """Back-compat wrapper: on+all_seasons = 'all', on = series flag only, off = 'off'."""
    if not monitored:
        return set_series_monitor_mode(sonarr, series_id, 'off')
    if all_seasons:
        return set_series_monitor_mode(sonarr, series_id, 'all')
    series = sonarr._request('GET', f'series/{series_id}', timeout=15)
    if not isinstance(series, dict):
        raise MonitorError('Series not found in Sonarr')
    series['monitored'] = True
    if not _put(sonarr, f'series/{series_id}', series):
        raise MonitorError('Sonarr rejected the series update')
    invalidate(series_id, include_series=True)
    command_id = _search(sonarr, {'name': 'SeriesSearch', 'seriesId': series_id})
    return {'mode': 'series', 'monitored': True, 'searching': command_id is not None, 'commandIds': [command_id] if command_id else []}


def set_series_monitor_mode(sonarr, series_id: int, mode: str, seasons: Optional[List[int]] = None) -> Dict:
    """Show-level monitoring from the Luma prompt.

    all     - series on, every regular season (not Specials) + its episodes on,
              then SeriesSearch.
    future  - series on, monitorNewItems='all' (new seasons arrive monitored),
              episodes that haven't aired yet (or have no air date) monitored.
              Existing aired episodes and season flags are left alone. No search.
    seasons - exactly the given seasons monitored (episodes matched), others
              that were monitored get unmonitored; SeasonSearch for each
              newly enabled season.
    off     - series flag off only. No search.
    """
    series = sonarr._request('GET', f'series/{series_id}', timeout=15)
    if not isinstance(series, dict):
        raise MonitorError('Series not found in Sonarr')
    command_ids: List[int] = []
    out: Dict[str, Any] = {'mode': mode}

    def search(body: Dict) -> None:
        cid = _search(sonarr, body)
        if cid is not None:
            command_ids.append(cid)

    def episodes() -> List[Dict]:
        return sonarr._request('GET', 'episode', {'seriesId': series_id}, timeout=20) or []

    def put_eps(ids: List[int], on: bool) -> None:
        if ids and not _put(sonarr, 'episode/monitor', {'episodeIds': ids, 'monitored': on}):
            raise MonitorError('Sonarr rejected the episode update')

    if mode == 'off':
        series['monitored'] = False
        if not _put(sonarr, f'series/{series_id}', series):
            raise MonitorError('Sonarr rejected the series update')
        out['monitored'] = False

    elif mode == 'all':
        series['monitored'] = True
        on = []
        for s in series.get('seasons') or []:
            n = s.get('seasonNumber')
            if isinstance(n, int) and n > 0:
                s['monitored'] = True
                on.append(n)
        if not _put(sonarr, f'series/{series_id}', series):
            raise MonitorError('Sonarr rejected the series update')
        put_eps([e['id'] for e in episodes() if e.get('seasonNumber') in on and not e.get('monitored')], True)
        search({'name': 'SeriesSearch', 'seriesId': series_id})
        out.update(monitored=True, seasons=on)

    elif mode == 'future':
        series['monitored'] = True
        series['monitorNewItems'] = 'all'
        if not _put(sonarr, f'series/{series_id}', series):
            raise MonitorError('Sonarr rejected the series update')
        now_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        ids = [e['id'] for e in episodes()
               if (e.get('seasonNumber') or 0) > 0 and not _aired(e, now_iso) and not e.get('monitored')]
        put_eps(ids, True)
        out.update(monitored=True, futureEpisodes=len(ids))

    elif mode == 'seasons':
        want = {int(n) for n in (seasons or [])}
        series_was_on = bool(series.get('monitored'))
        newly_on: List[int] = []
        newly_off: List[int] = []
        for s in series.get('seasons') or []:
            n = s.get('seasonNumber')
            if not isinstance(n, int):
                continue
            # Season flags under an unmonitored series count as off.
            was = bool(s.get('monitored')) and series_was_on
            now = n in want
            if now and not was:
                newly_on.append(n)
            if was and not now:
                newly_off.append(n)
            s['monitored'] = now
        if want:
            series['monitored'] = True
        if not _put(sonarr, f'series/{series_id}', series):
            raise MonitorError('Sonarr rejected the season update')
        eps = episodes()
        put_eps([e['id'] for e in eps if e.get('seasonNumber') in want and not e.get('monitored')], True)
        put_eps([e['id'] for e in eps if e.get('seasonNumber') in newly_off and e.get('monitored')], False)
        for n in sorted(newly_on):
            search({'name': 'SeasonSearch', 'seriesId': series_id, 'seasonNumber': n})
        out.update(monitored=bool(want), seasons=sorted(want), enabled=sorted(newly_on), disabled=sorted(newly_off))
    else:
        raise MonitorError('Unknown monitor option')

    invalidate(series_id, include_series=True)
    out.update(searching=bool(command_ids), commandIds=command_ids)
    return out


def _parse_utc(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None


def _quality_name(f: Dict) -> Optional[str]:
    try:
        return f.get('quality', {}).get('quality', {}).get('name')
    except Exception:
        return None


def _fetch_queue(sonarr, sid: int) -> Optional[List[Dict]]:
    """Queue items for one series (downloading, queued, import pending)."""
    q = sonarr._request('GET', 'queue/details',
                        {'seriesId': sid, 'includeEpisode': 'true'}, timeout=10)
    if isinstance(q, list):
        return q
    # Fallback: paged /queue, filtered to this series.
    q = sonarr._request('GET', 'queue', {'pageSize': 500, 'includeEpisode': 'true',
                                         'includeUnknownSeriesItems': 'false'}, timeout=10)
    if isinstance(q, dict):
        return [r for r in (q.get('records') or []) if r.get('seriesId') == sid]
    return None


def _queue_state(q: Dict) -> Dict[str, Any]:
    status = (q.get('status') or '').lower()
    tds = (q.get('trackedDownloadState') or '').lower()
    if tds == 'importblocked' or tds == 'failedpending' or status == 'failed':
        state = 'blocked'
    elif tds in ('importpending', 'importing') or status == 'completed':
        state = 'importing'
    elif status == 'downloading' or tds == 'downloading':
        state = 'downloading'
    else:  # queued, paused, delay, warning, unknown
        state = 'queued'
    rec: Dict[str, Any] = {'state': state}
    size = q.get('size') or 0
    left = q.get('sizeleft') or 0
    if size > 0:
        rec['progress'] = round(max(0.0, min(1.0, 1 - left / size)), 3)
    eta = q.get('estimatedCompletionTime')
    if eta and state == 'downloading':
        rec['eta'] = eta
    if status in ('paused', 'delay', 'warning'):
        rec['detail'] = status
    if state != 'blocked' and (q.get('trackedDownloadStatus') or '').lower() == 'warning':
        rec['detail'] = 'warning'
    return rec


def series_status(sonarr, tvdb_id: Optional[int], tmdb_id: Optional[int] = None) -> Dict:
    inst = getattr(sonarr, 'url', '') or ''
    series_list = _cached(('series', inst), _TTL,
                          lambda: sonarr._request('GET', 'series', timeout=20)) or []
    series = None
    if tvdb_id:
        series = next((s for s in series_list if s.get('tvdbId') == tvdb_id), None)
    if series is None and tmdb_id:
        series = next((s for s in series_list if s.get('tmdbId') == tmdb_id), None)
    if series is None:
        return {'enabled': True, 'exists': False}

    sid = series['id']
    episodes: List[Dict] = _cached(('episodes', inst, sid), _TTL,
                                   lambda: sonarr._request('GET', 'episode', {'seriesId': sid}, timeout=20)) or []
    files: List[Dict] = _cached(('files', inst, sid), _TTL,
                                lambda: sonarr._request('GET', 'episodefile', {'seriesId': sid}, timeout=20)) or []
    queue = _cached(('queue', inst, sid), _QUEUE_TTL, lambda: _fetch_queue(sonarr, sid)) or []

    # When the set of queue items changes (a download finished/imported or a
    # new grab appeared), drop the cached episode/file lists so on-disk state
    # catches up immediately instead of waiting out the 60s cache.
    sig = tuple(sorted((q.get('id'), (q.get('trackedDownloadState') or '')) for q in queue if isinstance(q, dict)))
    with _lock:
        prev = _last_queue_sig.get((inst, sid))
        _last_queue_sig[(inst, sid)] = sig
    if prev is not None and prev != sig:
        with _lock:
            _cache.pop(('episodes', inst, sid), None)
            _cache.pop(('files', inst, sid), None)
        episodes = sonarr._request('GET', 'episode', {'seriesId': sid}, timeout=20) or episodes
        files = sonarr._request('GET', 'episodefile', {'seriesId': sid}, timeout=20) or files
        now_ts = time.time()
        with _lock:
            _cache[('episodes', inst, sid)] = (now_ts, episodes)
            _cache[('files', inst, sid)] = (now_ts, files)

    files_by_id = {f.get('id'): f for f in files if isinstance(f, dict)}
    queue_by_ep: Dict[int, Dict] = {}
    for q in queue:
        if not isinstance(q, dict):
            continue
        ep_ids = [q.get('episodeId')] if q.get('episodeId') else []
        ep_ids += [e.get('id') for e in (q.get('episodes') or []) if isinstance(e, dict)]
        for eid in ep_ids:
            if eid:
                queue_by_ep[eid] = q

    now = datetime.now(timezone.utc)
    out_eps = []
    season_agg: Dict[int, Dict[str, int]] = {}
    for ep in episodes:
        sn = ep.get('seasonNumber')
        en = ep.get('episodeNumber')
        if sn is None or en is None:
            continue
        aired_dt = _parse_utc(ep.get('airDateUtc'))
        aired = aired_dt is not None and aired_dt <= now
        has_file = bool(ep.get('hasFile'))
        monitored = bool(ep.get('monitored'))
        q = queue_by_ep.get(ep.get('id'))
        rec: Dict[str, Any] = {'id': ep.get('id'), 's': sn, 'e': en, 'monitored': monitored}
        if q is not None:
            rec.update(_queue_state(q))
            if has_file:
                rec['onDisk'] = True
        elif has_file:
            rec['state'] = 'downloaded'
            f = files_by_id.get(ep.get('episodeFileId')) or ep.get('episodeFile') or {}
            rec['quality'] = _quality_name(f)
            rec['size'] = f.get('size')
        elif not aired:
            rec['state'] = 'unaired'
        elif monitored:
            rec['state'] = 'missing'
        else:
            rec['state'] = 'unmonitored'
        out_eps.append(rec)

        agg = season_agg.setdefault(sn, {'downloaded': 0, 'total': 0, 'downloading': 0, 'queued': 0, 'importing': 0, 'blocked': 0})
        if rec['state'] in ('downloading', 'queued', 'importing', 'blocked'):
            agg[rec['state']] += 1
        if has_file:
            agg['downloaded'] += 1
        if aired or has_file:
            agg['total'] += 1

    seasons = []
    for s in series.get('seasons') or []:
        sn = s.get('seasonNumber')
        agg = season_agg.get(sn, {'downloaded': 0, 'total': 0, 'downloading': 0, 'queued': 0, 'importing': 0, 'blocked': 0})
        seasons.append({
            'season': sn,
            'monitored': bool(s.get('monitored')),
            'downloaded': agg['downloaded'],
            'total': agg['total'],
            'downloading': agg['downloading'],
            'queued': agg['queued'],
            'importing': agg['importing'],
            'blocked': agg['blocked'],
        })

    regular = [v for k, v in season_agg.items() if k != 0]
    return {
        'enabled': True,
        'exists': True,
        'series': {
            'id': sid,
            'title': series.get('title'),
            'monitored': bool(series.get('monitored')),
            'status': series.get('status'),  # continuing | ended | upcoming
            'downloaded': sum(v['downloaded'] for v in regular),
            'total': sum(v['total'] for v in regular),
            'sizeOnDisk': (series.get('statistics') or {}).get('sizeOnDisk'),
            'downloading': sum(v['downloading'] for v in season_agg.values()),
            'queued': sum(v['queued'] for v in season_agg.values()),
            'importing': sum(v['importing'] for v in season_agg.values()),
            'blocked': sum(v['blocked'] for v in season_agg.values()),
        },
        'seasons': seasons,
        'episodes': out_eps,
        'queueCount': len({id(q) for q in queue_by_ep.values()}),
        # Poll fast only while something is actually moving.
        'active': any(e['state'] in ('downloading', 'queued', 'importing') for e in out_eps),
    }
