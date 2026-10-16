"""
Kodi JSON-RPC service for playback control.

Communicates with Kodi over HTTP JSON-RPC (default port 8080).
"""

import logging
import os
import requests

logger = logging.getLogger(__name__)


def resolve_kodi_file_url(
    plex_file_path: str,
    media_paths: list[dict],
    mount_point: str = '',
) -> str | None:
    """Map a Plex file path to a Kodi-accessible file URL.

    **V2 (primary)**: Each media path entry may have ``plex_locations`` (list of
    Plex filesystem paths auto-synced from Plex) and ``kodi_path`` (full Kodi
    SMB/NFS path set by the user).  The function matches ``plex_file_path``
    against every location in ``plex_locations``, strips the matched prefix,
    and prepends ``kodi_path``.

    **V1 (fallback)**: If no entries have ``plex_locations``, falls back to the
    legacy ``plex_path`` + ``mount_point`` approach for backward compatibility.

    Args:
        plex_file_path: Absolute path as reported by Plex
            (e.g. "/movies/Recently Released/Mercy (2026)/Mercy.mkv")
        media_paths: List of media path dicts from settings
        mount_point: Legacy Kodi mount base (only used for V1 fallback)

    Returns:
        Full file URL or None if no media path matches.
    """
    if not plex_file_path:
        return None

    # ------------------------------------------------------------------
    # V2: match plex_locations → kodi_path
    # ------------------------------------------------------------------
    # Build (prefix, kodi_path) candidates from all plex_locations entries
    v2_candidates: list[tuple[str, str]] = []
    for mp in media_paths:
        if not isinstance(mp, dict):
            continue
        kodi_path = (mp.get('kodi_path') or '').rstrip('/')
        if not kodi_path:
            continue
        for loc in (mp.get('plex_locations') or []):
            if loc:
                v2_candidates.append((loc.rstrip('/'), kodi_path))

    if v2_candidates:
        # Sort by prefix length DESC (longest match first)
        v2_candidates.sort(key=lambda c: len(c[0]), reverse=True)

        for prefix, kodi_path in v2_candidates:
            if plex_file_path.startswith(prefix + '/') or plex_file_path == prefix:
                relative = plex_file_path[len(prefix):]
                return f"{kodi_path}{relative}"

    # ------------------------------------------------------------------
    # V1 fallback: match plex_path → mount_point + subfolder
    # ------------------------------------------------------------------
    if not mount_point:
        return None

    v1_candidates = [
        p for p in media_paths
        if isinstance(p, dict) and p.get('plex_path') and p.get('path')
    ]

    v1_candidates.sort(key=lambda p: len(p['plex_path']), reverse=True)

    for mp in v1_candidates:
        plex_prefix = mp['plex_path'].rstrip('/')
        if plex_file_path.startswith(plex_prefix + '/') or plex_file_path == plex_prefix:
            subfolder = os.path.basename(mp['path'].rstrip('/'))
            relative = plex_file_path[len(plex_prefix):]
            mount = mount_point.rstrip('/')
            return f"{mount}/{subfolder}{relative}"

    return None


DEFAULT_NAS_BASE = 'smb://192.168.1.25'


def kodi_url_for_nas_path(nas_path: str, sources: list | None = None,
                          default_base: str = DEFAULT_NAS_BASE) -> str | None:
    """Build a Kodi URL for a file given its real path on the NAS share.

    *nas_path* is the share-relative path File Info shows
    (e.g. "/Media/Video/Movies/X (1991)/X (1991).mkv").

    Prefers one of Kodi's own sources (Files.GetSources) whose path covers
    the file, so Kodi reuses that source's host name and saved credentials.
    A source may sit at any depth ("smb://NAS/Media/", "smb://NAS/Media/Video/
    Movies/") or under an export prefix ("nfs://NAS/mnt/user/Media/"): the
    longest trailing run of its path segments that is a prefix of *nas_path*
    wins. Falls back to *default_base* + *nas_path*.
    """
    from urllib.parse import urlsplit, unquote
    if not nas_path or not nas_path.startswith('/'):
        return None
    best: tuple[int, str] | None = None
    for src in sources or []:
        url = (src.get('file') if isinstance(src, dict) else src) or ''
        if not url.startswith(('smb://', 'nfs://')):
            continue
        parts = urlsplit(url)
        segs = [unquote(x) for x in parts.path.split('/') if x]
        for i in range(len(segs)):
            tail = '/' + '/'.join(segs[i:])
            if nas_path.startswith(tail + '/'):
                score = len(tail)
                if best is None or score > best[0]:
                    head = '/'.join(segs[:i])
                    base = f"{parts.scheme}://{parts.netloc}" + (f"/{head}" if head else '')
                    best = (score, base + nas_path)
                break
    if best:
        return best[1]
    base = (default_base or '').rstrip('/')
    return f"{base}{nas_path}" if base else None


class KodiService:
    """Thin wrapper around Kodi's JSON-RPC API."""

    def __init__(self, host: str, port: int = 8080, username: str = "", password: str = "", timeout: int = 10):
        self.base_url = f"http://{host}:{port}/jsonrpc"
        self.auth = (username, password) if username else None
        self.timeout = timeout

    def _call(self, method: str, params: dict | None = None) -> dict:
        """Execute a JSON-RPC call and return the result."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
        }
        if params:
            payload["params"] = params

        resp = requests.post(
            self.base_url,
            json=payload,
            auth=self.auth,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Kodi JSON-RPC error: {data['error']}")
        return data.get("result", {})

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    def play_file(self, file_url: str, resume_offset_ms: int = 0) -> dict:
        """Start playback of a file by its direct URL.

        Args:
            file_url: Full file URL (e.g. "smb://192.168.1.25/Media/Video/4k/Movie.mkv")
            resume_offset_ms: Resume position in milliseconds (0 = play from start)
        """
        params: dict = {"item": {"file": file_url}}
        if resume_offset_ms > 0:
            total_sec = resume_offset_ms / 1000.0
            hours = int(total_sec // 3600)
            minutes = int((total_sec % 3600) // 60)
            seconds = int(total_sec % 60)
            ms = int((total_sec % 1) * 1000)
            params["options"] = {
                "resume": {
                    "hours": hours,
                    "minutes": minutes,
                    "seconds": seconds,
                    "milliseconds": ms,
                }
            }
        return self._call("Player.Open", params)

    def get_active_player(self) -> dict | None:
        """Get the currently active player, or None if nothing is playing."""
        result = self._call("Player.GetActivePlayers")
        if isinstance(result, list) and result:
            return result[0]
        return None

    def stop(self, player_id: int = 1) -> dict:
        """Stop playback on the given player."""
        return self._call("Player.Stop", {"playerid": player_id})

    def pause_toggle(self, player_id: int = 1) -> dict:
        """Toggle pause/play on the given player."""
        return self._call("Player.PlayPause", {"playerid": player_id})

    def get_player_properties(self, player_id: int = 1) -> dict:
        """Get current player properties (time, totaltime, speed, etc.)."""
        return self._call("Player.GetProperties", {
            "playerid": player_id,
            "properties": ["time", "totaltime", "speed", "percentage"],
        })

    def get_playing_item(self, player_id: int = 1) -> dict | None:
        """Get info about the currently playing item (title, year, type, etc.)."""
        try:
            result = self._call("Player.GetItem", {
                "playerid": player_id,
                "properties": ["title", "year", "showtitle", "season", "episode", "runtime"],
            })
            return result.get("item")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Navigation / Input control
    # ------------------------------------------------------------------

    def input_up(self) -> dict:
        return self._call("Input.Up")

    def input_down(self) -> dict:
        return self._call("Input.Down")

    def input_left(self) -> dict:
        return self._call("Input.Left")

    def input_right(self) -> dict:
        return self._call("Input.Right")

    def input_select(self) -> dict:
        return self._call("Input.Select")

    def input_back(self) -> dict:
        return self._call("Input.Back")

    def input_home(self) -> dict:
        return self._call("Input.Home")

    def input_context_menu(self) -> dict:
        return self._call("Input.ContextMenu")

    def input_info(self) -> dict:
        return self._call("Input.Info")

    # ------------------------------------------------------------------
    # Seek
    # ------------------------------------------------------------------

    def seek_to(self, offset_ms: int, player_id: int = 1) -> dict:
        """Seek to a specific position in the currently playing item."""
        total_sec = offset_ms / 1000.0
        hours = int(total_sec // 3600)
        minutes = int((total_sec % 3600) // 60)
        seconds = int(total_sec % 60)
        ms = int((total_sec % 1) * 1000)
        return self._call("Player.Seek", {
            "playerid": player_id,
            "value": {"time": {"hours": hours, "minutes": minutes,
                                "seconds": seconds, "milliseconds": ms}},
        })

    def get_video_sources(self) -> list:
        """Kodi's video sources ([{file, label}]), or [] if unavailable."""
        try:
            res = self._call("Files.GetSources", {"media": "video"})
            return res.get("sources") or []
        except Exception as e:  # noqa: BLE001
            logger.debug("Kodi Files.GetSources failed: %s", e)
            return []

    def ping(self) -> bool:
        """Check if Kodi is reachable via JSON-RPC."""
        try:
            result = self._call("JSONRPC.Ping")
            return result == "pong"
        except Exception:
            return False
