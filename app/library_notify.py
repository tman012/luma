"""Luma-side library/import notifications (prod AutoRenamer Web Push path).

Fires only when Luma's own pipeline has placed a file in the library
(library_mover / movie_import for movies, tv_downloads for TV) — never on grab/download, and
never from Sonarr/Radarr webhooks.

Copy (v302 — TV episode detail; movies unchanged):
  movie / TV show-level: "{Title} ({Year}) added!"
  TV single episode:     "{Show} ({Year}) · SxxExx · {Episode Title} added!"
                         (episode title omitted when missing or generic Episode N / Ep. N)
  TV multi-ep range:     "{Show} ({Year}) · SxxEyy–Ezz added!"
  body always:           "Tap to view details"

Channels: PWA Web Push + in-app bell only (v273 PM: no Telegram, no email).
Tokens are never logged.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_bell_hook: Optional[Callable[[dict], None]] = None
_lock = threading.Lock()
# In-process records when main has not registered the bell hook yet (tests).
_local: List[dict] = []
_LOCAL_CAP = 200

# Continue Watching / request_portal: drop placeholder episode titles.
_GENERIC_EP_TITLE = re.compile(r'(episode|ep\.?)\s*\d+', re.I)


def set_bell_hook(fn: Optional[Callable[[dict], None]]) -> None:
    """main registers `_add_webhook_notification` so the Activity/bell list sees imports."""
    global _bell_hook
    _bell_hook = fn


def _display(title: str, year: Any) -> str:
    t = (title or 'Unknown').strip() or 'Unknown'
    y = str(year or '').strip()
    return f'{t} ({y})' if y else t


def _usable_episode_title(episode_title: Optional[str]) -> Optional[str]:
    t = (episode_title or '').strip()
    if not t or _GENERIC_EP_TITLE.fullmatch(t):
        return None
    return t


def _ep_code(season: int, episode: int) -> str:
    return f'S{int(season):02d}E{int(episode):02d}'


def _ep_range_code(season: int, episodes: Sequence[int]) -> str:
    """S01E01 or S01E01–E02 (en dash) for a contiguous same-season range."""
    nums = sorted(int(n) for n in episodes)
    if not nums:
        return f'S{int(season):02d}'
    if len(nums) == 1:
        return _ep_code(season, nums[0])
    return f'S{int(season):02d}E{nums[0]:02d}\u2013E{nums[-1]:02d}'


def push_copy(
    *,
    media_type: str,
    title: str,
    year: Any = '',
    season: Optional[int] = None,
    episode: Optional[int] = None,
    episode_title: Optional[str] = None,
    episodes: Optional[Sequence[int]] = None,
) -> Tuple[str, str]:
    """Pure push title/body builder. Body is always 'Tap to view details'.

    Movies and TV show-level (season pack / no episode): '{Title} ({Year}) added!'.
    TV single episode (season + episode): show · SxxExx [· title] added!.
    TV multi-ep small range (season + episodes list, len>1): show · SxxEyy–Ezz added!.
    """
    kind = 'tv' if media_type == 'tv' else 'movie'
    display = _display(title, year)
    body = 'Tap to view details'
    if kind != 'tv' or season is None:
        return f'{display} added!', body

    nums: List[int] = []
    if episodes:
        nums = sorted({int(n) for n in episodes})
    elif episode is not None:
        nums = [int(episode)]

    if not nums:
        # Season set but no episode numbers → show-level (full-season / pack).
        return f'{display} added!', body

    if len(nums) == 1:
        code = _ep_code(int(season), nums[0])
        ep_t = _usable_episode_title(episode_title)
        if ep_t:
            return f'{display} \u00b7 {code} \u00b7 {ep_t} added!', body
        return f'{display} \u00b7 {code} added!', body

    # Multi-episode single file / small contiguous set — range, no forced episode title.
    return f'{display} \u00b7 {_ep_range_code(int(season), nums)} added!', body


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
    episodes: Optional[Sequence[int]] = None,
    source: str = 'luma',
) -> Dict[str, Any]:
    """Notify that media is in the library. Safe to call from any worker thread."""
    kind = 'tv' if media_type == 'tv' else 'movie'
    push_title, push_body = push_copy(
        media_type=kind, title=title, year=year,
        season=season, episode=episode, episode_title=episode_title,
        episodes=episodes,
    )
    # Deep-link: single episode → episode modal; season-only / multi → season modal; else title.
    ep_for_url = int(episode) if episode is not None else (
        int(sorted(episodes)[0]) if episodes and len(list(episodes)) == 1 else None
    )
    if kind == 'tv' and tmdb_id and season is not None and ep_for_url is not None and not (
        episodes and len(list(episodes)) > 1
    ):
        modal_url = f'/?modal=tv&tmdb={int(tmdb_id)}&season={int(season)}&episode={ep_for_url}'
    elif kind == 'tv' and tmdb_id and season is not None:
        modal_url = f'/?modal=tv&tmdb={int(tmdb_id)}&season={int(season)}'
    elif tmdb_id:
        modal_url = f'/?modal={kind}&tmdb={int(tmdb_id)}'
    else:
        modal_url = '/'
    tag = f'{kind}-{title}' if title else kind
    image = _backdrop(tmdb_id, kind)

    _push(push_title, push_body, tag, modal_url, image)

    record = {
        'title': (title or 'Unknown').strip() or 'Unknown',
        'year': str(year or ''),
        'media_type': kind,
        'tmdb_id': int(tmdb_id) if tmdb_id else None,
        'dest_path': dest_path or '',
        'completed_timestamp': time.time(),
        'source': source,
        'season_number': season,
        'episode_number': episode if episode is not None else (
            int(sorted(episodes)[0]) if episodes and len(list(episodes)) == 1 else None
        ),
        'episode_title': episode_title,
    }
    _bell(record)
    logger.info('Library notify: %s (%s)', push_title, kind)
    return record
