"""
plex_client_service.py — Plex client discovery and playback control.

Uses the plexapi library (already installed) to discover active Plex
clients and control playback directly, replacing the Home Assistant
media_player middleman.
"""

import logging
from typing import Optional

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

logger = logging.getLogger(__name__)


def _plex_server(plex_url: str, plex_token: str) -> PlexServer:
    """Create a PlexServer connection."""
    return PlexServer(plex_url, plex_token, timeout=10)


def _format_time(ms: Optional[int]) -> str:
    """Format milliseconds as H:MM:SS or M:SS."""
    if not ms:
        return "0:00"
    total_seconds = int(ms / 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


# ---------------------------------------------------------------------------
# Client discovery
# ---------------------------------------------------------------------------

def _proxied_image(path) -> str:
    """A Plex image path as a token-free /api/img/plex URL ('' if unusable)."""
    if not isinstance(path, str) or not path.startswith('/') or 'token' in path.lower():
        return ""
    from urllib.parse import quote
    return "/api/img/plex?url=" + quote(path, safe="/")


def get_active_clients(plex_url: str, plex_token: str) -> list[dict]:
    """Discover active Plex clients via PlexServer.clients().

    Returns a list of dicts matching the shape the frontend expects
    (backwards-compatible with the old HA media_player response).
    """
    try:
        server = _plex_server(plex_url, plex_token)
        clients = server.clients()
        result = []
        for client in clients:
            result.append({
                "name": client.title,
                "machine_id": getattr(client, "machineIdentifier", ""),
                "product": getattr(client, "product", ""),
                "platform": getattr(client, "platform", ""),
                "state": getattr(client, "state", "idle"),
                "address": getattr(client, "address", ""),
                "local": getattr(client, "local", True),
            })
        return result
    except Exception as e:
        logger.error("get_active_clients failed: %s", e)
        return []


def get_sessions(plex_url: str, plex_token: str) -> list[dict]:
    """Get active playback sessions from /status/sessions.

    Richer media info than the client API — includes title, year,
    duration, progress, player info, etc.
    """
    try:
        server = _plex_server(plex_url, plex_token)
        sessions = server.sessions()
        result = []
        for session in sessions:
            player = session.players[0] if session.players else None
            duration = getattr(session, "duration", 0) or 0
            view_offset = getattr(session, "viewOffset", 0) or 0
            progress = round((view_offset / duration) * 100, 1) if duration else 0

            result.append({
                "name": player.title if player else "Unknown",
                "machine_id": player.machineIdentifier if player else "",
                "platform": player.platform if player else "",
                "address": player.address if player else "",
                "state": player.state if player else "playing",
                "media_title": getattr(session, "title", ""),
                "media_year": getattr(session, "year", None),
                "media_type": session.type if hasattr(session, "type") else "",
                "media_duration": duration,
                "media_position": view_offset,
                "media_duration_str": _format_time(duration),
                "media_position_str": _format_time(view_offset),
                "progress": progress,
                # thumbUrl/artUrl carry X-Plex-Token; hand out proxy URLs instead.
                "thumb": _proxied_image(getattr(session, "thumb", "")),
                "art": _proxied_image(getattr(session, "art", "")),
            })
        return result
    except Exception as e:
        logger.error("get_sessions failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Client control
# ---------------------------------------------------------------------------

def control_client(plex_url: str, plex_token: str,
                   client_name: str, action: str,
                   **kwargs) -> dict:
    """Control a named Plex client.

    Actions: play, pause, stop, skipNext, skipPrevious, seekTo,
             setVolume, stepForward, stepBack
    """
    try:
        server = _plex_server(plex_url, plex_token)
        client = None
        for c in server.clients():
            if c.title.lower() == client_name.lower():
                client = c
                break
        if client is None:
            return {"success": False, "error": "Client not found"}
        # Send commands through the Plex server (X-Plex-Target-Client-Identifier)
        # so the owner token never goes to the player's own address.
        client.proxyThroughServer(True, server)

        action_map = {
            "play": lambda: client.play(),
            "pause": lambda: client.pause(),
            "stop": lambda: client.stop(),
            "next": lambda: client.skipNext(),
            "previous": lambda: client.skipPrevious(),
            "step_forward": lambda: client.stepForward(),
            "step_back": lambda: client.stepBack(),
        }

        if action == "volume_set":
            level = kwargs.get("volume_level")
            if level is not None:
                client.setVolume(int(level))
            else:
                return {"success": False, "error": "volume_level required for volume_set"}
        elif action == "seek":
            offset = kwargs.get("offset")
            if offset is not None:
                client.seekTo(int(offset))
            else:
                return {"success": False, "error": "offset required for seek"}
        elif action in action_map:
            action_map[action]()
        else:
            return {"success": False, "error": "Unknown action"}

        return {"success": True, "action": action, "client": client_name}
    except NotFound:
        return {"success": False, "error": "Client not found"}
    except Exception as e:
        logger.error("control_client(%s) failed: %s", action, type(e).__name__)
        return {"success": False, "error": "Client control failed"}
