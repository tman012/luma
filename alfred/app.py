"""
Alfred API — structured command interface for Luma voice and Telegram.

POST /api/command   — execute a natural-language command, return structured JSON
GET  /api/health    — service health check
POST /api/telegram  — incoming Telegram message (OpenClaw hook compatibility)

All responses follow the AlfredResponse contract:
{
  "type": str,
  "status": "ok" | "error" | "started" | "pending",
  "message": str,
  "data": {},
  "suggested_ui_action": { "action": str, ... } | null
}
"""

import logging
import os
from functools import wraps

from flask import Flask, request, jsonify

import config
from router import dispatch
from telegram_bot import start_telegram_bot
from users import registry as user_registry

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger('alfred')

# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


def require_api_key(f):
    """Optional bearer-token auth gate. Skipped if ALFRED_API_KEY is empty."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not config.API_KEY:
            return f(*args, **kwargs)
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth[7:].strip()
            if token == config.API_KEY:
                return f(*args, **kwargs)
        return jsonify({'error': 'Unauthorized'}), 401
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route('/api/health', methods=['GET'])
def health():
    """Health check — used by Luma's bridge to verify Alfred is reachable."""
    return jsonify({
        'available': True,
        'service': 'alfred',
        'version': '1.0.0',
    })


@app.route('/api/command', methods=['POST'])
@require_api_key
def command():
    """Execute a natural-language command and return a structured response.

    Request body:
        {
            "command": "how much storage do I have?",
            "source": "luma_voice" | "telegram" | ...,
            "context": { ... }  // optional UI context from Luma
        }

    Response: AlfredResponse JSON
    """
    body = request.get_json(silent=True) or {}
    cmd = (body.get('command', '') or '').strip()
    source = body.get('source', 'unknown')
    context = body.get('context')
    history = body.get('history')
    user_id = body.get('user_id')  # optional — Luma can pass e.g. "taylor"

    if not cmd:
        return jsonify({'error': 'Command is required'}), 400

    if len(cmd) > 1000:
        cmd = cmd[:1000]

    # Resolve user: explicit user_id → lookup, else default home user (kiosk voice).
    user = user_registry.by_id(user_id) if user_id else user_registry.default_luma()

    logger.info('Command from %s (user=%s): %s', source, user.id, cmd[:100])

    result = dispatch(cmd, context, history=history, user=user)

    # Ensure all required fields
    result.setdefault('type', 'generic')
    result.setdefault('status', 'ok')
    result.setdefault('message', '')
    result.setdefault('data', {})

    return jsonify(result)


@app.route('/api/telegram', methods=['POST'])
@require_api_key
def telegram_bridge():
    """Telegram compatibility endpoint.

    OpenClaw can forward Telegram messages here. We route them through the
    same dispatcher and return a response that OpenClaw can relay back.

    Request body (from OpenClaw hook):
        {
            "message": "how much storage do I have?",
            "sender": "...",
            "channel": "telegram",
            ...
        }

    Response:
        {
            "reply": "You have 3.2 TB free.",
            "structured": { ... AlfredResponse ... }
        }
    """
    body = request.get_json(silent=True) or {}
    message = (body.get('message', '') or '').strip()

    if not message:
        return jsonify({'reply': 'No message received.', 'structured': None})

    # External bridge — default to EXTERNAL identity unless a known user_id is passed.
    user_id = body.get('user_id')
    user = user_registry.by_id(user_id) if user_id else user_registry.by_id(None)

    result = dispatch(message, context={'channel': 'telegram'}, user=user)

    return jsonify({
        'reply': result.get('message', ''),
        'structured': result,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Start Telegram bot at import time so it runs under gunicorn too.
# Guarded so it only starts once per process.
_telegram_started = False
if not _telegram_started:
    try:
        if start_telegram_bot():
            logger.info('Telegram bot thread started')
        _telegram_started = True
    except Exception:
        logger.exception('Failed to start Telegram bot')


if __name__ == '__main__':
    port = config.PORT
    logger.info('Alfred API starting on port %d', port)
    app.run(host='0.0.0.0', port=port, debug=False)
