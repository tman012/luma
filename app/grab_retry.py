"""v204b (PM, Taylor): an automatic grab held back by the tracker guard (burst bucket, 120/h total,
backoff or pause) is never dropped. It is stored as pending (tv.db meta, JSON) with the time the guard
says capacity is back, and retried from the TV and movie loops then; held again, it is rescheduled.
A pending TV grab is dropped only once its episodes are grabbed another way; a movie one once the
movie has an active grab."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from app import tv_store

logger = logging.getLogger(__name__)

KEY = 'pending_grabs'
_lock = threading.Lock()


def _load() -> List[Dict[str, Any]]:
    try:
        v = json.loads(tv_store.get_meta(KEY) or '[]')
        return v if isinstance(v, list) else []
    except ValueError:
        return []


def _save(rows: List[Dict[str, Any]]) -> None:
    tv_store.set_meta(KEY, json.dumps(rows))


def _same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return (a['kind'], a['item'].get('downloadUrl'), a.get('series_id'), a.get('tmdb_id')) == \
           (b['kind'], b['item'].get('downloadUrl'), b.get('series_id'), b.get('tmdb_id'))


def add(kind: str, item: Dict[str, Any], due_at: float, **args: Any) -> None:
    row = {'kind': kind, 'item': dict(item), 'due_at': float(due_at), **args}
    with _lock:
        rows = [r for r in _load() if not _same(r, row)]
        rows.append(row)
        _save(rows)
    logger.info('Grab pending: %s, retried at %s', item.get('title'), time.strftime('%H:%M:%S', time.localtime(due_at)))


def pending(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    return [r for r in _load() if kind is None or r['kind'] == kind]


def _take_due(kind: str, now: float) -> List[Dict[str, Any]]:
    with _lock:
        rows = _load()
        due = [r for r in rows if r['kind'] == kind and r['due_at'] <= now]
        _save([r for r in rows if r not in due])
    return due


def _done_elsewhere(r: Dict[str, Any]) -> bool:
    if r['kind'] == 'tv':
        from app import tv_search
        with tv_store.connect() as conn:
            got = tv_search._grabbed(conn, r['series_id'])
        return set(r.get('episode_ids') or []) <= got
    from app import movie_grabs, movie_scanner
    return any(g['state'] in movie_scanner.ACTIVE for g in movie_grabs.for_movie(r['tmdb_id']))


def run_due(now: Optional[float] = None, kind: str = 'tv') -> Dict[str, int]:
    """Retry every due pending grab of *kind*. A grab held again goes back to pending by itself."""
    now = time.time() if now is None else now
    out = {'retried': 0, 'grabbed': 0}
    for r in _take_due(kind, now):
        try:
            if _done_elsewhere(r):
                continue
        except Exception as e:                       # noqa: BLE001 - unsure: retry; grab() still dedupes by hash
            logger.debug('Pending grab check failed: %s', type(e).__name__)
        out['retried'] += 1
        if kind == 'tv':
            from app import tv_grab
            g = tv_grab.grab(r['item'], r['series_id'], r.get('season'), r.get('episode_ids') or [], 'auto', now)
            if g.get('ok'):
                with tv_store.write() as conn:
                    conn.executemany('UPDATE episodes SET next_search_at = NULL, last_search_at = ? WHERE id = ?',
                                     [(now, i) for i in r.get('episode_ids') or []])
        else:
            from app import movie_scanner
            g = movie_scanner.grab(r['item'], r['tmdb_id'], 'auto', now)
        if g.get('ok'):
            out['grabbed'] += 1
        logger.info('Pending grab %s: %s', r['item'].get('title'), 'sent' if g.get('ok') else g.get('error'))
    return out
