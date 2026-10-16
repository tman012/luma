"""
pending_requests.py — Telegram notification registry for TFlix

Tracks user requests (movie/TV) so that when Radarr/Sonarr fires a Download
webhook, we can immediately notify the requester via Telegram.

File: /app/data/pending_requests.json
Schema per entry:
  {
    "id": "<uuid>",
    "tmdb_id": 12345,
    "title": "Movie Title",
    "media_type": "movie" | "tv",
    "season": null | 1,
    "episode": null | 4,
    "episode_title": null | "Episode Title",
    "sonarr_series_id": null | 123,
    "telegram_chat_id": "8574665585",
    "requested_at": 1710000000.0
  }

Matching rules:
  - Movies:  media_type==movie AND tmdb_id match
  - TV:      media_type==tv AND tmdb_id match
             If pending has season=None  → any import for that show matches
             If pending has season=X, episode=None → import for that season matches
             If pending has season=X, episode=Y → import must match both
"""

import json
import os
import threading
import time
import uuid
import logging

logger = logging.getLogger(__name__)

PENDING_FILE = '/app/data/pending_requests.json'
_lock = threading.Lock()

SONARR_URL = 'http://localhost:8989'
SONARR_API_KEY = os.environ.get('SONARR_API_KEY', '')


def _load() -> list:
    """Load pending requests from disk. Returns list (empty on error)."""
    try:
        if os.path.exists(PENDING_FILE):
            with open(PENDING_FILE, 'r') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"[pending_requests] load error: {e}")
    return []


def _save(entries: list):
    """Save pending requests list to disk."""
    try:
        os.makedirs(os.path.dirname(PENDING_FILE), exist_ok=True)
        with open(PENDING_FILE, 'w') as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        logger.error(f"[pending_requests] save error: {e}")


def add_request(tmdb_id: int, title: str, media_type: str,
                telegram_chat_id: str,
                season: int = None, episode: int = None,
                episode_title: str = None,
                sonarr_series_id: int = None) -> dict:
    """
    Register a pending notification request.
    Returns the created entry dict.
    """
    entry = {
        'id': str(uuid.uuid4()),
        'tmdb_id': int(tmdb_id) if tmdb_id else None,
        'title': title,
        'media_type': media_type,
        'season': season,
        'episode': episode,
        'episode_title': episode_title,
        'sonarr_series_id': int(sonarr_series_id) if sonarr_series_id is not None else None,
        'telegram_chat_id': str(telegram_chat_id),
        'requested_at': time.time(),
    }
    with _lock:
        entries = _load()
        entries.append(entry)
        _save(entries)
    logger.info(f"[pending_requests] registered: {title} (tmdb={tmdb_id}, "
                f"type={media_type}, s={season}, e={episode}, "
                f"ep_title={episode_title!r}, sonarr_id={sonarr_series_id}, "
                f"chat={telegram_chat_id})")
    return entry


def _matches(pending: dict, media_type: str, tmdb_id: int,
             season: int = None, episode: int = None) -> bool:
    """Return True if a pending entry matches this import event."""
    if pending.get('media_type') != media_type:
        return False
    if pending.get('tmdb_id') is None or tmdb_id is None:
        return False
    if int(pending['tmdb_id']) != int(tmdb_id):
        return False

    if media_type == 'movie':
        return True  # tmdb_id match is sufficient for movies

    # TV: check season/episode constraints
    p_season = pending.get('season')
    p_episode = pending.get('episode')

    if p_season is None:
        # No season constraint → any import for this show matches
        return True

    if season is None:
        # Import has no season info but pending wants a specific season → no match
        return False

    if int(p_season) != int(season):
        return False

    if p_episode is None:
        # Season matches, no episode constraint
        return True

    if episode is None:
        return False

    return int(p_episode) == int(episode)


def _fetch_sonarr_episode_title(sonarr_series_id: int, season: int, episode: int) -> str:
    """
    Fetch the episode title from Sonarr API.
    GET /api/v3/episode?seriesId=X&apikey=...
    Returns the title string or empty string on failure.
    """
    import urllib.request
    import urllib.parse

    if not sonarr_series_id or season is None or episode is None:
        return ''

    try:
        params = urllib.parse.urlencode({
            'seriesId': sonarr_series_id,
            'apikey': SONARR_API_KEY,
        })
        url = f"{SONARR_URL}/api/v3/episode?{params}"
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=10) as resp:
            episodes = json.loads(resp.read().decode('utf-8'))
        for ep in episodes:
            if ep.get('seasonNumber') == int(season) and ep.get('episodeNumber') == int(episode):
                title = ep.get('title', '')
                logger.info(f"[pending_requests] Sonarr episode title for "
                            f"s{season}e{episode} (series {sonarr_series_id}): {title!r}")
                return title
    except Exception as e:
        logger.warning(f"[pending_requests] Sonarr episode title lookup failed: {e}")
    return ''


def _build_message(pending: dict, season: int = None, episode: int = None,
                   episode_title: str = None) -> str:
    """Build the exact Telegram notification message."""
    title = pending.get('title', 'Unknown')
    media_type = pending.get('media_type', 'movie')

    if media_type == 'movie':
        return f"{title} is ready on TFlix!"

    # TV — determine effective season/episode
    p_season = pending.get('season')
    p_episode = pending.get('episode')

    eff_season = season if season is not None else p_season
    eff_episode = episode if episode is not None else p_episode

    # No specific episode → plain show notification
    if eff_season is None or eff_episode is None:
        return f"{title} is ready on TFlix!"

    # We have a specific episode — try to get episode title
    # Priority: 1) caller-provided, 2) stored in pending record, 3) Sonarr API lookup
    ep_title = episode_title
    if not ep_title:
        ep_title = pending.get('episode_title') or ''
    if not ep_title:
        sonarr_series_id = pending.get('sonarr_series_id')
        if sonarr_series_id:
            ep_title = _fetch_sonarr_episode_title(sonarr_series_id, eff_season, eff_episode)

    ep_code = f"S{eff_season:02d}E{eff_episode:02d}"
    if ep_title:
        return f"{title} - {ep_code} - {ep_title} is ready on TFlix!"

    # Fallback: season/episode numbers only
    return f"{title} - {ep_code} is ready on TFlix!"


def _get_telegram_bot_token() -> str:
    """Read the Telegram bot token from /app/data/telegram_bot.json."""
    config_path = '/app/data/telegram_bot.json'
    try:
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
            token = cfg.get('bot_token', '')
            if token:
                return token
    except Exception as e:
        logger.warning(f"[pending_requests] could not read telegram_bot.json: {e}")
    return ''


def send_telegram(chat_id: str, message: str):
    """Send a Telegram message via Bot API (direct HTTP, no CLI dependency)."""
    import urllib.request
    import urllib.parse

    token = _get_telegram_bot_token()
    if not token:
        logger.error("[pending_requests] No Telegram bot token found in OpenClaw config")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        'chat_id': str(chat_id),
        'text': message,
    }).encode('utf-8')

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode('utf-8')
            result = json.loads(body)
            if result.get('ok'):
                logger.info(f"[pending_requests] Telegram sent to {chat_id}: {message!r}")
            else:
                logger.error(f"[pending_requests] Telegram API error: {result}")
    except Exception as e:
        logger.error(f"[pending_requests] Telegram send error: {e}")


def check_and_notify(media_type: str, tmdb_id: int,
                     season: int = None, episode: int = None,
                     episode_title: str = None):
    """
    Called from webhook handlers after successful import.
    Finds all matching pending requests, sends Telegram notifications,
    and removes the matched entries from the store.

    episode_title — if the webhook payload already contains the episode title
                    (e.g. from Sonarr's episode[] data), pass it here so we
                    skip the Sonarr API lookup.
    """
    if not tmdb_id:
        logger.debug("[pending_requests] check_and_notify: no tmdb_id, skipping")
        return

    matched = []
    with _lock:
        entries = _load()
        remaining = []
        for entry in entries:
            if _matches(entry, media_type, tmdb_id, season, episode):
                matched.append(entry)
            else:
                remaining.append(entry)
        if matched:
            _save(remaining)

    for entry in matched:
        msg = _build_message(entry, season=season, episode=episode,
                             episode_title=episode_title)
        chat_id = entry.get('telegram_chat_id')
        logger.info(f"[pending_requests] match found → notifying {chat_id}: {msg!r}")
        send_telegram(chat_id, msg)
