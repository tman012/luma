"""Luma-side library/import notifications (prod AutoRenamer path).

Fires only when Luma's own pipeline has placed a file in the library
(library_mover for movies, tv_downloads for TV) — never on grab/download, and
never from Sonarr/Radarr webhooks. Copy matches prod AutoRenamer:

  title: "{Title} ({Year}) added!"   (year omitted when empty)
  body:  "Tap to view details"

Also: pending-request Telegram (check_and_notify) and an in-app bell record.
Tokens are never logged.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_bell_hook: Optional[Callable[[dict], None]] = None
_lock = threading.Lock()
# In-process records when main has not registered the bell hook yet (tests).
_local: List[dict] = []
_LOCAL_CAP = 200


def set_bell_hook(fn: Optional[Callable[[dict], None]]) -> None:
    """main registers `_add_webhook_notification` so the Activity/bell list sees imports."""
    global _bell_hook
    _bell_hook = fn


def _display(title: str, year: Any) -> str:
    t = (title or 'Unknown').strip() or 'Unknown'
    y = str(year or '').strip()
    return f'{t} ({y})' if y else t


def _backdrop(tmdb_id: Optional[int], media_type: str) -> str:
    """Best-effort TMDb backdrop for the push image; empty on any failure."""
    if not tmdb_id:
        return ''
    try:
        import requests
        from app.settings_manager import SettingsManager
        key = str(SettingsManager().get('apis.tmdb.api_key') or '').strip()
        if not key:
            return ''
        kind = 'tv' if media_type == 'tv' else 'movie'
        resp = requests.get(
            f'https://api.themoviedb.org/3/{kind}/{int(tmdb_id)}',
            params={'api_key': key},
            timeout=5,
        )
        if resp.status_code != 200:
            return ''
        path = (resp.json() or {}).get('backdrop_path') or ''
        return f'https://image.tmdb.org/t/p/w780{path}' if path else ''
    except Exception:
        return ''


def _push(title: str, body: str, tag: str, url: str, image: str) -> None:
    try:
        from app.push_service import send_push_notification
        send_push_notification(title=title, body=body, tag=tag, url=url, image=image)
    except Exception as e:
        logger.warning('Library notify: web push failed (%s)', type(e).__name__)


def _telegram_pending(media_type: str, tmdb_id: Optional[int],
                      season: Optional[int], episode: Optional[int],
                      episode_title: Optional[str]) -> None:
    if not tmdb_id:
        return
    try:
        from app.pending_requests import check_and_notify
        check_and_notify(
            media_type=media_type,
            tmdb_id=int(tmdb_id),
            season=season,
            episode=episode,
            episode_title=episode_title,
        )
    except Exception as e:
        logger.warning('Library notify: pending Telegram check failed (%s)', type(e).__name__)


def _bell(record: dict) -> None:
    hook = _bell_hook
    if hook is not None:
        try:
            hook(record)
            return
        except Exception as e:
            logger.warning('Library notify: bell hook failed (%s)', type(e).__name__)
    with _lock:
        _local.insert(0, record)
        del _local[_LOCAL_CAP:]


def local_records(since: float = 0.0) -> List[dict]:
    """Records held only in this module (when the bell hook is unset)."""
    with _lock:
        return [r for r in _local if float(r.get('completed_timestamp') or 0) > since]


def clear_local() -> None:
    with _lock:
        _local.clear()


def notify_library_added(
    *,
    media_type: str,
    title: str,
    year: Any = '',
    tmdb_id: Optional[int] = None,
    dest_path: str = '',
    season: Optional[int] = None,
    episode: Optional[int] = None,
    episode_title: Optional[str] = None,
    source: str = 'luma',
) -> Dict[str, Any]:
    """Notify that media is in the library. Safe to call from any worker thread."""
    kind = 'tv' if media_type == 'tv' else 'movie'
    display = _display(title, year)
    # Prod AutoRenamer push copy (Luma-side import-complete).
    push_title = f'{display} added!'
    push_body = 'Tap to view details'
    if kind == 'tv' and tmdb_id and season is not None and episode is not None:
        modal_url = f'/?modal=tv&tmdb={int(tmdb_id)}&season={int(season)}&episode={int(episode)}'
    elif kind == 'tv' and tmdb_id and season is not None:
        modal_url = f'/?modal=tv&tmdb={int(tmdb_id)}&season={int(season)}'
    elif tmdb_id:
        modal_url = f'/?modal={kind}&tmdb={int(tmdb_id)}'
    else:
        modal_url = '/'
    tag = f'{kind}-{title}' if title else kind
    image = _backdrop(tmdb_id, kind)

    _push(push_title, push_body, tag, modal_url, image)
    _telegram_pending(kind, tmdb_id, season, episode, episode_title)

    record = {
        'title': (title or 'Unknown').strip() or 'Unknown',
        'year': str(year or ''),
        'media_type': kind,
        'tmdb_id': int(tmdb_id) if tmdb_id else None,
        'dest_path': dest_path or '',
        'completed_timestamp': time.time(),
        'source': source,
        'season_number': season,
        'episode_number': episode,
        'episode_title': episode_title,
    }
    _bell(record)
    logger.info('Library notify: %s (%s)', push_title, kind)
    return record
