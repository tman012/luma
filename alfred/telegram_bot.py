"""Telegram long-polling bot for Alfred.

Runs in a background thread, pulls messages via getUpdates, routes them
through the existing dispatcher, and sends replies back via sendMessage.

Enabled when TELEGRAM_BOT_TOKEN env var is set. No public URL required.
"""

import logging
import os
import threading
import time
from typing import Optional, Set

import requests

from router import dispatch
from users import registry as user_registry

logger = logging.getLogger('alfred.telegram')

TELEGRAM_API = 'https://api.telegram.org/bot{token}/{method}'
POLL_TIMEOUT = 30  # long-poll seconds
MAX_MESSAGE_LEN = 4000  # Telegram hard limit is 4096

# Per-chat conversation memory. Each entry:
#   {'history': [{'role': 'user'|'assistant', 'content': str}, ...],
#    'pending_watchlist': dict | None,
#    'pending_free_space': bool,
#    'last_activity': float epoch seconds}
# Kept in-process (not persisted) so a container restart clears it.
_HISTORY_MAX_TURNS = 6          # last 6 turns (12 messages)
_HISTORY_TTL_SECONDS = 10 * 60  # drop idle conversations after 10 minutes
_chat_history: dict = {}
_history_lock = threading.Lock()


def _get_history(chat_id: int):
    """Return a copy of the rolling history for this chat, pruning stale sessions."""
    now = time.time()
    with _history_lock:
        entry = _chat_history.get(chat_id)
        if not entry:
            return []
        if now - entry['last_activity'] > _HISTORY_TTL_SECONDS:
            # Conversation expired — treat as fresh
            _chat_history.pop(chat_id, None)
            return []
        return list(entry['history'])


def _get_pending_watchlist(chat_id: int):
    """Return the pending watchlist dict for this chat, or None if expired/absent."""
    now = time.time()
    with _history_lock:
        entry = _chat_history.get(chat_id)
        if not entry:
            return None
        if now - entry['last_activity'] > _HISTORY_TTL_SECONDS:
            _chat_history.pop(chat_id, None)
            return None
        pending = entry.get('pending_watchlist')
        return dict(pending) if isinstance(pending, dict) else None


def _set_pending_watchlist(chat_id: int, pending) -> None:
    """Store (or clear) the pending watchlist for this chat."""
    now = time.time()
    with _history_lock:
        entry = _chat_history.setdefault(chat_id, {'history': [], 'last_activity': now})
        entry['pending_watchlist'] = pending
        entry['last_activity'] = now


def _get_pending_free_space(chat_id: int) -> bool:
    now = time.time()
    with _history_lock:
        entry = _chat_history.get(chat_id)
        if not entry:
            return False
        if now - entry['last_activity'] > _HISTORY_TTL_SECONDS:
            _chat_history.pop(chat_id, None)
            return False
        return bool(entry.get('pending_free_space'))


def _set_pending_free_space(chat_id: int, value: bool) -> None:
    now = time.time()
    with _history_lock:
        entry = _chat_history.setdefault(chat_id, {'history': [], 'last_activity': now})
        entry['pending_free_space'] = value
        entry['last_activity'] = now


def _append_history(chat_id: int, user_text: str, assistant_text: str) -> None:
    """Append a turn to this chat's history and trim to the last N turns."""
    now = time.time()
    with _history_lock:
        entry = _chat_history.setdefault(chat_id, {'history': [], 'last_activity': now})
        entry['history'].append({'role': 'user', 'content': user_text})
        entry['history'].append({'role': 'assistant', 'content': assistant_text})
        # Keep the last N turns (2 messages per turn)
        max_msgs = _HISTORY_MAX_TURNS * 2
        if len(entry['history']) > max_msgs:
            entry['history'] = entry['history'][-max_msgs:]
        entry['last_activity'] = now


def _send_message(token: str, chat_id: int, text: str) -> None:
    if not text:
        text = '(no response)'
    if len(text) > MAX_MESSAGE_LEN:
        text = text[:MAX_MESSAGE_LEN - 20] + '\n…(truncated)'
    try:
        requests.post(
            TELEGRAM_API.format(token=token, method='sendMessage'),
            json={'chat_id': chat_id, 'text': text},
            timeout=10,
        )
    except Exception as e:
        logger.warning('sendMessage failed: %s', e)


def _send_photo(token: str, chat_id: int, photo_url: str, caption: str) -> None:
    """Send a photo with a text caption. Falls back to sendMessage if Telegram rejects the photo."""
    if len(caption) > 1024:
        caption = caption[:1020] + '…'
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token, method='sendPhoto'),
            json={'chat_id': chat_id, 'photo': photo_url, 'caption': caption},
            timeout=15,
        )
        if not resp.ok:
            logger.warning('sendPhoto failed (%s) — falling back to sendMessage', resp.status_code)
            _send_message(token, chat_id, caption)
    except Exception as e:
        logger.warning('sendPhoto error: %s — falling back to sendMessage', e)
        _send_message(token, chat_id, caption)


def _handle_update(token: str, update: dict) -> None:
    message = update.get('message') or update.get('edited_message') or {}
    chat = message.get('chat') or {}
    chat_id = chat.get('id')
    text = (message.get('text') or '').strip()

    if not chat_id or not text:
        return

    # Identity resolution: known chat_ids from users.json → home user,
    # everyone else → EXTERNAL (restricted intents + guest persona).
    user = user_registry.by_telegram_chat(chat_id)

    # Log chat_id prominently on first contact so you can paste it into users.json
    logger.info('Telegram[chat=%s user=%s role=%s]: %s',
                chat_id, user.id, user.role, text[:100])

    # Handle /start and /help — also clears any conversation context
    if text in ('/start', '/help', '/reset', '/clear', '/cancel'):
        with _history_lock:
            _chat_history.pop(chat_id, None)
        if user.role == 'home':
            greeting = (
                f"Hi {user.name}, I'm Alfred. Ask me things like:\n"
                "• what's for dinner tomorrow\n"
                "• add The Batman to my watchlist\n"
                "• turn off the kitchen lights"
            )
        else:
            greeting = (
                "Hi, I'm Alfred. I can answer general questions, tell you the "
                "time, or share sports scores. How can I help?"
            )
        _send_message(token, chat_id, greeting)
        return

    history = _get_history(chat_id)
    pending_watchlist = _get_pending_watchlist(chat_id)
    pending_free_space = _get_pending_free_space(chat_id)

    ctx: dict = {'channel': 'telegram', 'chat_id': chat_id}
    if pending_watchlist:
        ctx['pendingWatchlist'] = pending_watchlist
    if pending_free_space:
        ctx['pendingFreeSpace'] = True

    result: dict = {}
    try:
        result = dispatch(text, context=ctx, history=history, user=user)
        reply = result.get('message') or '(no response)'
    except Exception as e:
        logger.exception('dispatch failed')
        reply = f'Error: {e}'

    # Update pending state based on the response.
    data = (result or {}).get('data') or {}
    next_pending_wl = None
    if result.get('status') == 'pending' and isinstance(data.get('pending_watchlist'), dict):
        next_pending_wl = data['pending_watchlist']
    _set_pending_watchlist(chat_id, next_pending_wl)

    next_pending_fs = result.get('status') == 'pending' and bool(data.get('pending_free_space'))
    _set_pending_free_space(chat_id, next_pending_fs)

    # Record the turn AFTER dispatch so next message has the updated context
    _append_history(chat_id, text, reply)

    # Use sendPhoto for candidate confirmations that include a poster URL
    poster_url = data.get('poster_url') if result.get('status') == 'pending' else None
    if poster_url:
        _send_photo(token, chat_id, poster_url, reply)
    else:
        _send_message(token, chat_id, reply)


def _poll_loop(token: str) -> None:
    logger.info('Telegram bot started (identity resolved per-message via users.json)')

    offset: Optional[int] = None
    url = TELEGRAM_API.format(token=token, method='getUpdates')

    while True:
        try:
            params = {'timeout': POLL_TIMEOUT}
            if offset is not None:
                params['offset'] = offset
            r = requests.get(url, params=params, timeout=POLL_TIMEOUT + 10)
            data = r.json()
            if not data.get('ok'):
                logger.warning('getUpdates not ok: %s', data)
                time.sleep(5)
                continue
            for update in data.get('result', []):
                offset = update['update_id'] + 1
                try:
                    _handle_update(token, update)
                except Exception:
                    logger.exception('handle_update failed')
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.warning('poll loop error: %s', e)
            time.sleep(5)


_LOCK_PATH = '/tmp/alfred_telegram.lock'
_lock_fh = None  # kept open for process lifetime


def _acquire_singleton_lock() -> bool:
    """Ensure only one process (one gunicorn worker) runs the poller."""
    global _lock_fh
    import fcntl
    try:
        _lock_fh = open(_LOCK_PATH, 'w')
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, BlockingIOError):
        if _lock_fh is not None:
            try:
                _lock_fh.close()
            except Exception:
                pass
            _lock_fh = None
        return False


def start_telegram_bot() -> bool:
    """Start the Telegram bot in a background thread if token is configured."""
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        logger.info('TELEGRAM_BOT_TOKEN not set — Telegram bot disabled')
        return False

    if not _acquire_singleton_lock():
        logger.info('Telegram bot already running in another worker — skipping')
        return False

    t = threading.Thread(target=_poll_loop, args=(token,), daemon=True, name='telegram-bot')
    t.start()
    return True
