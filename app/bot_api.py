"""Luma Bot convenience API.

Thin wrappers that resolve room-by-name / title-search, then invoke the SAME
underlying SPA routes in-process (via Flask test_client — not HTTP self-call,
which would deadlock the single-threaded app.run server):

  POST /api/plex/playqueue
  POST /api/player/play
  POST /api/watchlist          (movie download / ensure + search)
  POST /api/tv/watchlist       (TV download)
  GET  /api/settings/rooms
  GET  /api/library/search/fast  (preferred for title checks)
  GET  /api/library/search       (Plex live fallback)

Do not invent alternate playback/download behavior here.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from flask import Flask, g, jsonify, request

logger = logging.getLogger(__name__)

# Lazy-loaded from main at init time
_LUMA_SESSION_COOKIE = None
_INSTALL_SECRETS = None


def _forward_environ_headers() -> Dict[str, str]:
    """Preserve the caller's auth when fanning out to SPA routes via test_client.
    
    SECURITY: Never inject the server's SERVICE_API_KEY on behalf of a caller.
    Pass the caller's own identity (X-API-Key or session cookie) through unchanged.
    """
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    
    # Pass through caller's API key if present (do NOT add server's key)
    incoming_key = request.headers.get("X-API-Key")
    if incoming_key:
        headers["X-API-Key"] = incoming_key
    
    # Pass through caller's session cookie if present
    cookie_name = _LUMA_SESSION_COOKIE or "luma_session"
    token = request.cookies.get(cookie_name)
    if token:
        headers["Cookie"] = f"{cookie_name}={token}"
    
    return headers


def _local(app: Flask, method: str, path: str, json_body: Optional[dict] = None):
    """In-process call to an existing route (same view code as the SPA)."""
    client = app.test_client()
    headers = _forward_environ_headers()
    return client.open(
        path,
        method=method,
        json=json_body,
        headers=headers,
    )


def _require_user():
    user = getattr(g, "current_user", None)
    if not user:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    return None


def _fuzzy_room(rooms: List[Dict], name: str) -> Optional[Dict]:
    if not rooms or not name:
        return None
    q = name.lower().strip()
    for r in rooms:
        if (r.get("name") or "").lower() == q:
            return r
    for r in rooms:
        n = (r.get("name") or "").lower()
        if q in n or n in q:
            return r
    return None


def _prefer_plex_target(room: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Match SPA PlaybackModal preference: device with a plex service."""
    devices = room.get("devices") or []
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        for svc in (dev.get("services") or []):
            if isinstance(svc, dict) and svc.get("type") == "plex":
                return dev, svc
    if devices and isinstance(devices[0], dict):
        dev = devices[0]
        svcs = [s for s in (dev.get("services") or []) if isinstance(s, dict)]
        return dev, (svcs[0] if svcs else None)
    return None, None


def _fetch_rooms(app: Flask) -> List[Dict]:
    resp = _local(app, "GET", "/api/settings/rooms")
    if resp.status_code != 200:
        logger.warning("bot rooms fetch failed: %s %s", resp.status_code, resp.get_data(as_text=True)[:200])
        return []
    return (resp.get_json() or {}).get("rooms") or []


def _library_search(app: Flask, query: str) -> Dict[str, Any]:
    """Fast in-memory library lookup via /api/library/search/fast.

    Falls back to the slower Plex live /api/library/search only if the fast
    index is unavailable. Target: one quick round trip (<1s) for do-I-have-X.
    """
    resp = _local(app, "GET", f"/api/library/search/fast?q={quote(query)}&limit=50")
    if resp.status_code == 200:
        data = resp.get_json() or {}
        results = data.get("results") or []
        movies: List[Dict[str, Any]] = []
        tv_shows: List[Dict[str, Any]] = []
        for r in results:
            thumb = r.get("thumb")
            item = {
                "rating_key": r.get("rating_key"),
                "ratingKey": r.get("rating_key"),
                "title": r.get("title"),
                "year": r.get("year"),
                "type": "movie" if r.get("media_type") == "movie" else "show",
                "media_type": r.get("media_type"),
                "library_name": r.get("section_name"),
                "section_id": r.get("section_id"),
                "content_rating": r.get("content_rating"),
                "genres": r.get("genres") or [],
                "thumb": thumb,
                "poster_url": ("/api/img/plex?url=" + thumb) if thumb else None,
            }
            if r.get("media_type") == "show":
                tv_shows.append(item)
            else:
                movies.append(item)
        return {
            "movies": movies,
            "tv_shows": tv_shows,
            "elapsed_ms": data.get("elapsed_ms"),
            "index_size": data.get("index_size"),
            "search_time": "fast",
        }

    logger.warning(
        "fast library search unavailable (%s) -- falling back to /api/library/search",
        resp.status_code,
    )
    resp = _local(app, "GET", f"/api/library/search?q={quote(query)}")
    if resp.status_code != 200:
        return {"movies": [], "tv_shows": [], "error": f"search HTTP {resp.status_code}"}
    data = resp.get_json() or {}
    data["search_time"] = "plex"
    return data
def _pick_library_item(app: Flask, query: str, media_type: Optional[str] = None) -> Optional[Dict]:
    """Pick best Plex library hit for a spoken/typed title."""
    data = _library_search(app, query)
    movies = data.get("movies") or []
    shows = data.get("tv_shows") or []

    def score_pool(pool: List[Dict]) -> Optional[Dict]:
        if not pool:
            return None
        q = query.lower().strip()
        for item in pool:
            if (item.get("title") or "").lower().strip() == q:
                return item
        return pool[0]

    if media_type in ("show", "tv", "series"):
        return score_pool(shows) or score_pool(movies)
    if media_type == "movie":
        return score_pool(movies) or score_pool(shows)
    for pool in (shows, movies):
        q = query.lower().strip()
        for item in pool:
            if (item.get("title") or "").lower().strip() == q:
                return item
    return score_pool(movies) or score_pool(shows)


def register_bot_routes(app: Flask, session_cookie: str = "luma_session", install_secrets: dict = None) -> None:
    """Attach /api/bot/* routes onto the Flask app."""
    global _LUMA_SESSION_COOKIE, _INSTALL_SECRETS
    _LUMA_SESSION_COOKIE = session_cookie
    _INSTALL_SECRETS = install_secrets or {}

    @app.route("/api/bot/rooms", methods=["GET"])
    def bot_rooms():
        """List rooms/devices/services (same data as GET /api/settings/rooms)."""
        err = _require_user()
        if err:
            return err
        rooms = _fetch_rooms(app)
        return jsonify({"success": True, "rooms": rooms})

    @app.route("/api/bot/search", methods=["GET"])
    def bot_search():
        """Fast library title check -- wraps GET /api/library/search/fast.

        Query: q=title. Returns {success, movies, tv_shows, elapsed_ms, search_time}.
        Prefer this (or /api/bot/library) for do-I-have-X -- target <1s.
        """
        err = _require_user()
        if err:
            return err
        q = (request.args.get("q") or request.args.get("title") or "").strip()
        if not q:
            return jsonify({"success": False, "error": "q is required"}), 400
        data = _library_search(app, q)
        return jsonify({"success": True, **data})

    @app.route("/api/bot/library", methods=["GET"])
    def bot_library():
        """Thin alias for fast ownership lookup. Query: title= or q=.

        Same payload as /api/bot/search. Use for instant do-I-have-X.
        """
        err = _require_user()
        if err:
            return err
        q = (request.args.get("title") or request.args.get("q") or "").strip()
        if not q:
            return jsonify({"success": False, "error": "title or q is required"}), 400
        data = _library_search(app, q)
        owned = bool(data.get("movies") or data.get("tv_shows"))
        return jsonify({"success": True, "owned": owned, "title": q, **data})


    @app.route("/api/bot/play", methods=["POST"])
    def bot_play():
        """Play or shuffle media in a room — SPA-equivalent orchestration.

        Body:
          rating_key  int|str   (optional if title given)
          title       str       library search query (optional if rating_key)
          media_type  'movie'|'show'  hint for title resolve
          room        str       room name, e.g. "Living Room" (or room_id)
          room_id     str       optional explicit id
          device_id   str       optional; else prefer plex-capable device
          service_id  str       optional; else prefer plex service
          shuffle     bool      if true: POST /api/plex/playqueue then play with pq id
          resume      bool      forwarded to /api/player/play
          resume_offset int     ms

        Calls the same /api/plex/playqueue + /api/player/play the SPA uses.
        """
        err = _require_user()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        rating_key = body.get("rating_key")
        title = (body.get("title") or "").strip()
        media_type = body.get("media_type")
        room_name = (body.get("room") or body.get("room_name") or "").strip()
        room_id = body.get("room_id")
        device_id = body.get("device_id")
        service_id = body.get("service_id")
        shuffle = bool(body.get("shuffle", False))
        resume = bool(body.get("resume", False))
        resume_offset = int(body.get("resume_offset") or 0)

        resolved_title = title
        if not rating_key:
            if not title:
                return jsonify({"success": False, "error": "rating_key or title is required"}), 400
            hint = media_type or ("show" if shuffle else None)
            item = _pick_library_item(app, title, media_type=hint)
            if not item:
                return jsonify({"success": False, "error": f'No library match for "{title}"'}), 404
            rating_key = item.get("rating_key") or item.get("ratingKey")
            resolved_title = item.get("title") or title
            if not media_type:
                media_type = item.get("type")

        if not rating_key:
            return jsonify({"success": False, "error": "Could not resolve rating_key"}), 404

        rooms = _fetch_rooms(app)
        room = None
        if room_id:
            room = next((r for r in rooms if r.get("id") == room_id), None)
        if not room and room_name:
            room = _fuzzy_room(rooms, room_name)
        if not room and rooms:
            room = rooms[0]
        if not room:
            return jsonify({"success": False, "error": "No rooms configured / room not found"}), 404

        room_id = room.get("id")
        device, service = _prefer_plex_target(room)
        if device_id:
            device = next(
                (d for d in (room.get("devices") or []) if d.get("id") == device_id),
                device,
            )
            if device:
                for svc in (device.get("services") or []):
                    if service_id and svc.get("id") == service_id:
                        service = svc
                        break
                    if not service_id and svc.get("type") == "plex":
                        service = svc
        if not device_id and device:
            device_id = device.get("id")
        if not service_id and service:
            service_id = service.get("id")
        client_ip = (device or {}).get("host") or ""

        play_queue_id = None
        pq_meta = None
        if shuffle:
            rk_val = int(rating_key) if str(rating_key).isdigit() else rating_key
            pq_resp = _local(
                app,
                "POST",
                "/api/plex/playqueue",
                {"rating_key": rk_val, "shuffle": True},
            )
            pq_meta = pq_resp.get_json(silent=True) or {"success": False, "error": pq_resp.get_data(as_text=True)[:300]}
            if pq_resp.status_code >= 400 or not (pq_meta or {}).get("success"):
                return jsonify({
                    "success": False,
                    "error": (pq_meta or {}).get("error") or f"playqueue HTTP {pq_resp.status_code}",
                    "playqueue": pq_meta,
                }), 502
            play_queue_id = pq_meta.get("play_queue_id")

        rk_val = int(rating_key) if str(rating_key).isdigit() else rating_key
        play_body: Dict[str, Any] = {
            "rating_key": rk_val,
            "room_id": room_id,
            "client_ip": client_ip,
            "resume_offset": resume_offset,
        }
        if device_id:
            play_body["device_id"] = device_id
        if service_id:
            play_body["service_id"] = service_id
        if play_queue_id is not None:
            play_body["play_queue_id"] = play_queue_id
        if resume:
            play_body["resume"] = True

        play_resp = _local(app, "POST", "/api/player/play", play_body)
        play_meta = play_resp.get_json(silent=True) or {"success": False, "message": play_resp.get_data(as_text=True)[:300]}

        ok = play_resp.status_code < 400 and bool(play_meta.get("success", True))
        status = 200 if ok else (play_resp.status_code if play_resp.status_code >= 400 else 502)
        return jsonify({
            "success": ok,
            "title": resolved_title,
            "rating_key": str(rating_key),
            "media_type": media_type,
            "room": room.get("name"),
            "room_id": room_id,
            "device_id": device_id,
            "service_id": service_id,
            "client_ip": client_ip,
            "shuffle": shuffle,
            "play_queue_id": play_queue_id,
            "playqueue": pq_meta,
            "play": play_meta,
            "message": play_meta.get("message") or play_meta.get("error"),
        }), status

    @app.route("/api/bot/download", methods=["POST"])
    def bot_download():
        """Queue media into the library the same way the SPA Download button does.

        Movies → POST /api/watchlist
        TV     → POST /api/tv/watchlist when media_type is tv/show/series
        """
        err = _require_user()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        title = (body.get("title") or "").strip()
        tmdb_id = body.get("tmdb_id")
        year = body.get("year")
        profile = body.get("quality_profile") or body.get("profile") or "TFlix"
        media_type = (body.get("media_type") or "movie").lower()

        if not title and not tmdb_id:
            return jsonify({"success": False, "error": "title or tmdb_id is required"}), 400

        if media_type in ("tv", "show", "series"):
            payload: Dict[str, Any] = {
                "title": title or f"TMDb {tmdb_id}",
                "quality_profile": profile,
            }
            if tmdb_id is not None:
                payload["tmdb_id"] = tmdb_id
            if year is not None:
                payload["year"] = year
            if body.get("season") is not None:
                payload["season"] = body.get("season")
            if body.get("episode") is not None:
                payload["episode"] = body.get("episode")
            if body.get("tvdb_id") is not None:
                payload["tvdb_id"] = body.get("tvdb_id")
            resp = _local(app, "POST", "/api/tv/watchlist", payload)
        else:
            payload = {
                "title": title or f"TMDb {tmdb_id}",
                "media_type": "movie",
                "quality_profile": profile,
            }
            if tmdb_id is not None:
                payload["tmdb_id"] = tmdb_id
            if year is not None:
                payload["year"] = year
            resp = _local(app, "POST", "/api/watchlist", payload)

        meta = resp.get_json(silent=True) or {"error": resp.get_data(as_text=True)[:300]}
        ok = resp.status_code < 400 and bool(meta.get("success", resp.status_code < 400))
        return jsonify({
            "success": ok,
            "media_type": media_type,
            "request": payload,
            "result": meta,
            "message": meta.get("message") or meta.get("error"),
        }), (resp.status_code if resp.status_code >= 400 else 200)

    logger.info("Luma Bot API routes registered under /api/bot/*")
