"""
Dev/test safety net: make selected upstream services read-only.

When LUMA_READ_ONLY_URLS is set (comma-separated base URLs, e.g. the Plex,
Radarr, Sonarr and qBittorrent URLs a dev instance shares with production),
every outgoing `requests` call to one of those URLs with a method other than
GET/HEAD/OPTIONS is refused locally and answered with a synthetic 403. Nothing
is sent upstream, so a dev instance can browse real services without adding,
grabbing, deleting or re-matching anything.

Unset (the default) = no effect. Installed from app/__init__.py so it is active
before any service client is created.
"""

import json
import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_installed = False


def _parse_targets(raw: str):
    targets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "://" not in part:
            part = "http://" + part
        u = urlsplit(part)
        host = (u.hostname or "").lower().rstrip(".")
        port = u.port or (443 if u.scheme == "https" else 80)
        if host:
            targets.append((host, port, (u.path or "").rstrip("/")))
    return targets


def _matches(url: str, targets) -> bool:
    try:
        u = urlsplit(url)
    except Exception:
        return False
    host = (u.hostname or "").lower().rstrip(".")
    port = u.port or (443 if u.scheme == "https" else 80)
    path = u.path or ""
    return any(host == h and port == p and path.startswith(pref) for h, p, pref in targets)


def install() -> None:
    global _installed
    raw = os.environ.get("LUMA_READ_ONLY_URLS", "").strip()
    if _installed or not raw:
        return
    targets = _parse_targets(raw)
    if not targets:
        return
    try:
        import requests
        from requests.models import Response
    except Exception:  # pragma: no cover - requests is a hard dependency
        return

    original = requests.sessions.Session.request

    def guarded(self, method, url, *args, **kwargs):
        if str(method).upper() not in _SAFE_METHODS and _matches(str(url), targets):
            logger.warning("dev_guard: blocked %s %s (LUMA_READ_ONLY_URLS)", str(method).upper(),
                           urlsplit(str(url))._replace(query="").geturl())
            resp = Response()
            resp.status_code = 403
            resp.reason = "Blocked by LUMA_READ_ONLY_URLS"
            resp.url = str(url)
            resp._content = json.dumps({"error": "write blocked on this dev instance"}).encode()
            resp.headers["Content-Type"] = "application/json"
            resp.encoding = "utf-8"
            return resp
        return original(self, method, url, *args, **kwargs)

    requests.sessions.Session.request = guarded
    _installed = True
    logger.warning("dev_guard: write requests blocked for %d upstream URL(s)", len(targets))
