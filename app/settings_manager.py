#!/usr/bin/env python3
"""
Settings Manager for Luma
Handles persistent JSON-based settings with schema validation, thread-safe I/O,
deep merge for partial updates, secret masking, and bootstrap from env vars.

Phase 2.5: List-driven schemas for entities, rooms, and media paths.
"""

import os
import json
import copy
import stat
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# v174 (Ops on c669f76): settings load (and migrate) while app.main is still importing, before its
# logging.basicConfig and luma.log handler exist; an INFO line logged then reaches no handler (Python's
# last-resort handler shows WARNING and up only). Migration lines logged before logging is configured
# wait here and app.main flushes them, once, right after its handlers are attached.
_pending_migration_logs: List[Tuple[str, tuple]] = []
_pending_lock = threading.Lock()


def _logging_configured() -> bool:
    lg: Optional[logging.Logger] = logger
    while lg is not None:
        if lg.handlers:
            return True
        if not lg.propagate:
            break
        lg = lg.parent
    return False


def _log_migration(msg: str, *args) -> None:
    """INFO line for a settings migration: now if logging is set up, else after app.main sets it up."""
    if _logging_configured():
        logger.info(msg, *args)
        return
    with _pending_lock:
        if (msg, args) not in _pending_migration_logs:
            _pending_migration_logs.append((msg, args))


def flush_migration_logs() -> int:
    """Log the migration lines that ran before logging was configured (each once). Returns how many."""
    with _pending_lock:
        pending = list(_pending_migration_logs)
        _pending_migration_logs.clear()
    for msg, args in pending:
        logger.info(msg, *args)
    return len(pending)

# ---------------------------------------------------------------------------
# Mask sentinel -- when a GET returns this value the real secret is hidden
# ---------------------------------------------------------------------------
SECRET_MASK = "********"

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------
# Each leaf node has: type, default, sensitive (bool), validation constraints.
# "sensitive" fields are masked on GET and skipped on PUT when value == SECRET_MASK.
#
# Phase 2.5 changes:
#   - homeassistant section re-added for TV power control via HA scenes
#   - devices.shield removed; rooms carry adb_port and type per-room
#   - media.paths is an array (replaces individual *_root fields)
# ---------------------------------------------------------------------------

SETTINGS_SCHEMA: Dict[str, Any] = {
    "plex": {
        "_type": "object",
        "url":              {"type": "string",  "default": "", "sensitive": False, "description": "Plex Media Server base URL"},
        "token":            {"type": "string",  "default": "",  "sensitive": True,  "description": "Plex authentication token"},
        "machine_id":       {"type": "string",  "default": "",  "sensitive": False, "description": "Plex server machine identifier (auto-detected)"},
        "client_port":      {"type": "integer", "default": 32500, "sensitive": False, "description": "Plex client HTTP control port", "min": 1, "max": 65535},
        "search_section_ids": {"type": "array", "default": [], "sensitive": False, "description": "Plex section ids the quick search indexes (empty = every movie and TV section)", "items": {"type": "integer"}},
    },
    "qbittorrent": {
        "_type": "object",
        "url":             {"type": "string", "default": "", "sensitive": False, "description": "qBittorrent Web UI URL"},
        "username":        {"type": "string", "default": "", "sensitive": False, "description": "qBittorrent login username"},
        "password":        {"type": "string", "default": "", "sensitive": True,  "description": "qBittorrent login password"},
        "app_category":    {"type": "string", "default": "luma", "sensitive": False, "description": "Category for manual app downloads"},
        "add_paused":      {"type": "boolean", "default": False, "sensitive": False, "description": "Add downloads paused"},
        "radarr_category": {"type": "string", "default": "radarr", "sensitive": False, "description": "Category for Radarr downloads"},
    },
    "sonarr": {
        "_type": "object",
        "url":                        {"type": "string",  "default": "", "sensitive": False, "description": "Sonarr API base URL"},
        "api_key":                    {"type": "string",  "default": "", "sensitive": True,  "description": "Sonarr API key"},
        "preferred_profile_name":     {"type": "string",  "default": "", "sensitive": False, "description": "Quality profile name to use when no default profile id is set (matched case-insensitively)"},
        "default_quality_profile_id": {"type": "integer", "default": 0, "sensitive": False, "description": "Default quality profile for added shows (0 = use first available)", "min": 0},
    },
    "radarr": {
        "_type": "object",
        "url":                        {"type": "string",  "default": "", "sensitive": False, "description": "Radarr API base URL"},
        "api_key":                    {"type": "string",  "default": "", "sensitive": True,  "description": "Radarr API key"},
        "preferred_profile_name":     {"type": "string",  "default": "", "sensitive": False, "description": "Quality profile name to use when no default profile id is set (matched case-insensitively)"},
        "default_quality_profile_id": {"type": "integer", "default": 0, "sensitive": False, "description": "Default quality profile for added movies (0 = use first available)", "min": 0},
        "monitoring_enabled":         {"type": "boolean", "default": True,  "sensitive": False, "description": "Enable Radarr download monitoring"},
        "minimum_availability":       {"type": "string",  "default": "announced", "sensitive": False, "description": "Minimum availability for added movies", "enum": ["announced", "inCinemas", "released"]},
        "upgrade_quality_profiles": {
            "type": "array",
            "default": [],
            "sensitive": False,
            "description": "Quality profiles shown in upgrade/watchlist modal",
            "items": {
                "type": "object",
                "properties": {
                    "id":   {"type": "integer", "description": "Radarr quality profile ID"},
                    "name": {"type": "string",  "description": "Quality profile display name"},
                },
                "required": ["id", "name"],
            },
        },
    },
    "sources": {
        "_type": "object",
        "movie_provider": {
            "type": "string",
            "default": "radarr",
            "sensitive": False,
            "description": "Movie sources provider (radarr)",
            "enum": ["radarr"],
        },
    },
    "indexer": {
        # v172 (tracker port step 4): searching and grabbing use Settings > Trackers only. The old
        # Prowlarr/Torznab connection is kept as saved (deprecated, never used to search or grab, no
        # UI) so an upgrade loses nothing: api_key stays sensitive (masked, in secrets), and
        # Import from Prowlarr reads base_url/api_key when the owner asks for it.
        "_type": "object",
        "provider":         {"type": "string",  "default": "prowlarr", "sensitive": False, "deprecated": True, "description": "Deprecated (v172): no longer used", "enum": ["prowlarr", "torznab"]},
        "base_url":         {"type": "string",  "default": "",         "sensitive": False, "deprecated": True, "description": "Deprecated (v172): read only by Import from Prowlarr"},
        "torznab_url":      {"type": "string",  "default": "",         "sensitive": False, "deprecated": True, "description": "Deprecated (v172): no longer used"},
        "api_key":          {"type": "string",  "default": "",         "sensitive": True,  "deprecated": True, "description": "Deprecated (v172): read only by Import from Prowlarr"},
    },
    "tracker_search": {
        # v173 (PM): Explore's movie categories, moved from indexer.movie_categories (no UI since v172)
        # to Settings > Trackers > Search; the value is carried over by _migrate_tracker_search_settings.
        # v174 (PM): only the wired setting stays. indexer/tracker_search max_age_days, min_seeders
        # and limit were never used by any search; a stale saved value is tolerated and ignored.
        "_type": "object",
        "movie_categories": {"type": "string",  "default": "2000",     "sensitive": False, "description": "Movie category IDs (comma-separated)"},
    },
    "apis": {
        "_type": "object",
        "tmdb": {
            "_type": "object",
            "api_key":        {"type": "string", "default": "", "sensitive": True,  "description": "TMDb API key"},
            "base_url":       {"type": "string", "default": "https://api.themoviedb.org/3", "sensitive": False, "description": "TMDb API base URL"},
            "image_base_url": {"type": "string", "default": "https://image.tmdb.org/t/p", "sensitive": False, "description": "TMDb image CDN base URL"},
            "region":         {"type": "string", "default": "US", "sensitive": False, "description": "TMDb region filter for releases"},
            "connector_saved": {"type": "boolean", "default": False, "sensitive": False, "description": "Key saved in Settings > Connectors (minimal builds use only that key)"},
        },
        "omdb": {
            "_type": "object",
            "api_key": {"type": "string", "default": "", "sensitive": True, "description": "OMDb API key"},
            "connector_saved": {"type": "boolean", "default": False, "sensitive": False, "description": "Key saved in Settings > Connectors (minimal builds use only that key)"},
        },
        "tvdb": {
            "_type": "object",
            "api_key":       {"type": "string", "default": "", "sensitive": True, "description": "TheTVDB v4 API key (Calendar air times)"},
            "pin":           {"type": "string", "default": "", "sensitive": True, "description": "TheTVDB subscriber PIN (optional)"},
        },
        "trakt": {
            "_type": "object",
            "enabled":       {"type": "boolean", "default": False, "sensitive": False, "description": "Enable Trakt integration"},
            "client_id":     {"type": "string",  "default": "",    "sensitive": True,  "description": "Trakt API client ID"},
            "client_secret": {"type": "string",  "default": "",    "sensitive": True,  "description": "Trakt API client secret (optional)"},
            "connector_saved": {"type": "boolean", "default": False, "sensitive": False, "description": "Client ID saved in Settings > Connectors (minimal builds use only that one)"},
        },
        "openai": {
            "_type": "object",
            "enabled":  {"type": "boolean", "default": False, "sensitive": False, "description": "Enable AI-powered natural language search"},
            "api_key":  {"type": "string",  "default": "",    "sensitive": True,  "description": "OpenAI API key"},
            "model":    {"type": "string",  "default": "gpt-4o-mini", "sensitive": False, "description": "OpenAI model for structured extraction"},
            "custom_instructions": {"type": "string", "default": "", "sensitive": False, "description": "Custom instructions appended to AI prompt (e.g. content preferences)"},
            "temperature": {"type": "number", "default": 0.7, "sensitive": False, "description": "AI creativity (0.0 = focused, 1.0 = creative)"},
            "max_tokens": {"type": "integer", "default": 1500, "sensitive": False, "description": "Maximum response length in tokens"},
            "voice_enabled": {"type": "boolean", "default": False, "sensitive": False, "description": "Enable voice assistant for playback control"},
            "wake_word": {"type": "string", "default": "alfred", "sensitive": False, "description": "Wake word to activate voice assistant (e.g. alfred, jarvis, luma)"},
            "tts_provider": {"type": "string", "default": "openai", "sensitive": False, "description": "TTS provider (openai or elevenlabs)"},
            "tts_voice": {"type": "string", "default": "nova", "sensitive": False, "description": "OpenAI TTS voice (nova, alloy, echo, fable, onyx, shimmer)"},
        },
        "elevenlabs": {
            "_type": "object",
            "api_key":  {"type": "string",  "default": "",    "sensitive": True,  "description": "ElevenLabs API key"},
            "voice_id": {"type": "string",  "default": "",    "sensitive": False, "description": "ElevenLabs voice ID"},
            "model_id": {"type": "string",  "default": "eleven_multilingual_v2", "sensitive": False, "description": "ElevenLabs model ID"},
            "stability":    {"type": "number", "default": 0.5, "sensitive": False, "description": "Voice stability (0.0-1.0)"},
            "similarity":   {"type": "number", "default": 0.75, "sensitive": False, "description": "Similarity boost (0.0-1.0)"},
        },
        "alfred": {
            "_type": "object",
            "enabled":  {"type": "boolean", "default": False,  "sensitive": False, "description": "Enable Alfred agent for complex voice commands"},
            "base_url": {"type": "string",  "default": "", "sensitive": False, "description": "Alfred API base URL"},
            "api_key":  {"type": "string",  "default": "",     "sensitive": True,  "description": "Alfred API key (optional)"},
            "timeout":  {"type": "integer", "default": 90,     "sensitive": False, "description": "Request timeout in seconds"},
        },
    },
    "request_portal": {
        "_type": "object",
        "enabled":           {"type": "boolean", "default": False, "sensitive": False, "description": "Enable the request page (/request)"},
        "password_hash":     {"type": "string",  "default": "",    "sensitive": True,  "description": "Hash of the shared request-page password"},
        "password_version":  {"type": "integer", "default": 1,     "sensitive": False, "description": "Bumped when the password changes (signs requesters out)", "min": 1},
        "movie_profile":     {"type": "string",  "default": "",    "sensitive": False, "description": "Radarr quality profile name for requests (blank = app's default)"},
        "tv_profile":        {"type": "string",  "default": "",    "sensitive": False, "description": "Sonarr quality profile name for requests (blank = app's default)"},
        "hostname":          {"type": "string",  "default": "",    "sensitive": False, "description": "Dedicated hostname that serves only the request page"},
        "exclude_libraries": {"type": "array",   "default": [],    "sensitive": False, "description": "Plex libraries hidden from requester availability checks"},
    },
    "security": {
        "_type": "object",
        "auth_method": {"type": "string", "default": "forms", "sensitive": False,
                        "description": "Authentication method",
                        "enum": ["forms"]},
        "auth_required": {"type": "string", "default": "disabled_local", "sensitive": False,
                          "description": "Authentication requirement: disabled_local (LAN no sign-in, remote must) or enabled (everyone must)",
                          "enum": ["disabled_local", "enabled"]},
        "session_secret": {"type": "string", "default": "", "sensitive": True,
                          "description": "Secret key for session signing (auto-generated on first run)"},
        "trusted_proxies": {"type": "string",
                            "default": "127.0.0.1/32, ::1/128, 172.16.0.0/12, 192.168.32.0/20",
                            "sensitive": False,
                            "description": "Reverse proxies whose X-Forwarded-For and X-Forwarded-Proto headers are believed (comma-separated IPs or CIDRs). Default covers loopback and Docker bridges used when Caddy (host network) reverse_proxies to a published container port."},
        "hostnames": {"type": "string", "default": "", "sensitive": False,
                      "description": "Host names Luma is reached by besides IP addresses and localhost (comma-separated). Needed for no-sign-in LAN access by name and for a trusted proxy's X-Forwarded-Host"},
        "allow_signup": {"type": "boolean", "default": False, "sensitive": False,
                         "description": "Let visitors outside the local network request an account from the sign-in page"},
    },
    "homeassistant": {
        "_type": "object",
        "url":          {"type": "string", "default": "", "sensitive": False, "description": "Home Assistant base URL (e.g. http://homeassistant.local:8123)"},
        "access_token": {"type": "string", "default": "", "sensitive": True,  "description": "Home Assistant long-lived access token"},
    },
    "devices": {
        "_type": "object",
        "rooms": {
            "type": "array",
            "default": [],
            "sensitive": False,
            "description": "Room/device configuration array",
            "items": {
                "type": "object",
                "properties": {
                    "name":             {"type": "string"},
                    "ip":               {"type": "string"},
                    "adb_port":         {"type": "integer"},
                    "plex_client_port": {"type": "integer"},
                    "type":             {"type": "string"},
                    "plex_machine_id":  {"type": "string"},
                },
            },
        },
    },
    "media": {
        "_type": "object",
        "paths": {
            "type": "array",
            "default": [],
            "sensitive": False,
            "description": "Media library paths — sync from Plex or add manually",
            "items": {
                "type": "object",
                "properties": {
                    "name":            {"type": "string", "description": "Display name (from Plex section title, editable)"},
                    "path":            {"type": "string", "description": "Local/container path for media management"},
                    "kodi_path":       {"type": "string", "description": "Full Kodi SMB/NFS path for playback"},
                    "type":            {"type": "string", "description": "Free-text tag (movie, tv, 4k, recent, etc.)"},
                    "plex_section_id": {"type": "integer", "description": "Plex library section ID (auto from sync)"},
                    "plex_locations":  {"type": "array", "description": "Plex filesystem locations (auto from sync)"},
                },
            },
        },
        "types": {
            "type": "array",
            "default": [],
            "sensitive": False,
            "description": "Media type definitions for routing and labeling library paths",
            "items": {
                "type": "object",
                "properties": {
                    "id":             {"type": "string",  "description": "Unique identifier (e.g. movies, recent, 4k)"},
                    "label":          {"type": "string",  "description": "Human-readable display name"},
                    "media_category": {"type": "string",  "description": "Content category: movie or tv"},
                    "description":    {"type": "string",  "description": "What this type means (user-editable)"},
                    "rules": {
                        "type": "object",
                        "description": "Quality/year detection criteria for auto-routing",
                        "properties": {
                            "quality_keywords": {"type": "array",  "description": "Regex-ready quality strings (e.g. 2160p, 4k, uhd)"},
                            "year_rule":        {"type": "string", "description": "Year rule: recent or empty"},
                            "is_default":       {"type": "boolean","description": "Fallback type for its media_category"},
                        },
                    },
                },
                "required": ["id", "label", "media_category"],
            },
        },
        "downloads_root":   {"type": "string", "default": "",               "sensitive": False, "description": "Downloads folder (in-container path); empty uses LUMA_DOWNLOADS_ROOT or /downloads"},
        "database_path":    {"type": "string", "default": "", "sensitive": False, "description": "Application SQLite database path (blank: media.db in the data folder)"},
        "video_extensions":  {
            "type": "array",
            "default": [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".m2ts"],
            "sensitive": False,
            "description": "Recognized video file extensions",
        },
    },
    "ui": {
        "_type": "object",
        "theme_color":    {"type": "string", "default": "#15102a", "sensitive": False, "description": "App theme color (PWA)"},
        "placeholder_poster_url": {"type": "string", "default": "https://via.placeholder.com/342x513/1a1a1a/808080?text=No+Poster", "sensitive": False, "description": "Fallback poster image URL"},
        "kiosk_mode":     {"type": "string", "default": "movie", "sensitive": False, "description": "Kiosk home page mode (movie or daily)"},
        "daily_kiosk": {
            "_type": "object",
            "widget_seconds": {"type": "integer", "default": 8, "sensitive": False, "description": "Seconds each widget stays on screen before rotating", "min": 3, "max": 60},
            "weather": {
                "_type": "object",
                "api_key":  {"type": "string", "default": "", "sensitive": True, "description": "OpenWeatherMap API key"},
                "zip_code": {"type": "string", "default": "", "sensitive": False, "description": "ZIP/postal code for weather"},
                "country_code": {"type": "string", "default": "US", "sensitive": False, "description": "Country code (ISO 3166)"},
                "units":    {"type": "string", "default": "imperial", "sensitive": False, "description": "Temperature units (imperial, metric, standard)"},
                "state":    {"type": "string", "default": "", "sensitive": False, "description": "State/region abbreviation (e.g. MD, CA) shown after city name"},
            },
            "sports": {
                "_type": "object",
                "enabled":   {"type": "boolean", "default": False, "sensitive": False, "description": "Enable sports widget on Daily Kiosk"},
                "team_id":   {"type": "integer", "default": 0, "sensitive": False, "description": "MLB team ID"},
                "team_name": {"type": "string", "default": "", "sensitive": False, "description": "MLB team display name"},
            },
            "news": {
                "_type": "object",
                "enabled":  {"type": "boolean", "default": False, "sensitive": False, "description": "Enable news widget on Daily Kiosk"},
                "feed_url": {"type": "string", "default": "", "sensitive": False, "description": "RSS feed URL"},
                "feed_name": {"type": "string", "default": "", "sensitive": False, "description": "Display name for the feed"},
            },
        },
        "screensaver": {
            "_type": "object",
            "enabled":           {"type": "boolean", "default": True, "sensitive": False, "description": "Enable the orb screensaver"},
            "idle_timeout_ms":   {"type": "integer", "default": 10000, "sensitive": False, "description": "Idle timeout before screensaver (ms)", "min": 5000, "max": 600000},
            "orb_count":         {"type": "integer", "default": 9, "sensitive": False, "description": "Number of orbs on screensaver", "min": 1, "max": 20},
            "cache_ttl_seconds": {"type": "integer", "default": 1800, "sensitive": False, "description": "Screensaver movie data cache TTL", "min": 300, "max": 7200},
            "library_name":      {"type": "string",  "default": "Recently Released", "sensitive": False, "description": "Plex library name for screensaver movies"},
        },
        "hero": {
            "_type": "object",
            "library_section_id": {"type": "integer", "default": 0, "sensitive": False, "description": "Plex library section ID for hero content (0 = first movie library)"},
            "limit":              {"type": "integer", "default": 10, "sensitive": False, "description": "Number of movies to show in hero rotation", "min": 1, "max": 50},
            "max_age_days":       {"type": "integer", "default": 30, "sensitive": False, "description": "Only show movies added within this many days", "min": 1, "max": 365},
            "excluded_genres":    {"type": "array", "default": [], "sensitive": False, "description": "Genres to exclude from hero content"},
            "boost_popular_titles": {"type": "boolean", "default": False, "sensitive": False, "description": "Sort hero by popularity instead of recently added"},
            "popularity_source":  {"type": "string", "default": "tmdb", "sensitive": False, "description": "Popularity data source (tmdb or imdb)"},
            "backdrop_source":    {"type": "string", "default": "plex", "sensitive": False, "description": "Hero backdrop image source (plex or tmdb)"},
        },
        "kiosk": {
            "_type": "object",
            "home_area_id": {"type": "string", "default": "", "sensitive": False, "description": "Home Assistant area this device lives in (e.g. 'living_room'). Smart-home panel opens to this area by default."},
        },
    },
    "notifications": {
        "_type": "object",
        "web_push": {
            "_type": "object",
            "enabled":           {"type": "boolean", "default": False,  "sensitive": False, "description": "Enable Web Push notifications"},
            "vapid_public_key":  {"type": "string",  "default": "",     "sensitive": False, "description": "VAPID public key (auto-generated)"},
            "vapid_private_key": {"type": "string",  "default": "",     "sensitive": True,  "description": "VAPID private key (auto-generated)"},
            "vapid_email":       {"type": "string",  "default": "", "sensitive": False, "description": "VAPID contact (mailto:you@example.com); blank = LUMA_ADMIN_EMAIL"},
        },
    },
    "network": {
        "_type": "object",
        "flask_port":       {"type": "integer", "default": 5001, "sensitive": False, "description": "Flask application port", "min": 1, "max": 65535},
        "flask_secret_key": {"type": "string",  "default": "",   "sensitive": True,  "description": "Flask session secret key"},
        "is_docker":        {"type": "boolean", "default": True,  "sensitive": False, "description": "Whether running in Docker"},
        "tunnel": {
            "_type": "object",
            "enabled": {"type": "boolean", "default": False, "sensitive": False, "description": "Enable Cloudflare Tunnel for remote access"},
            "token":   {"type": "string",  "default": "",    "sensitive": True,  "description": "Cloudflare Tunnel token from Zero Trust dashboard"},
        },
    },
    "automation": {
        "_type": "object",
        "interval_hours":     {"type": "integer", "default": 2,     "sensitive": False, "description": "Global hours between automation checks", "min": 1, "max": 24},
        # Feature switches (see app/automation_features.py). A grouped job runs
        # only when its group switch and its own "enabled" flag are both on.
        "auto_grab": {
            "_type": "object",
            "enabled": {"type": "boolean", "default": False, "sensitive": False, "description": "Auto-grab: add/grab new titles from the sources switched on below"},
        },
        "upgrades": {
            "_type": "object",
            "enabled": {"type": "boolean", "default": False, "sensitive": False, "description": "4K & upgrades: daily upgrade scan and 4K Remux auto-grab"},
        },
        "upgrade_scheduler": {
            "_type": "object",
            "enabled":  {"type": "boolean", "default": True, "sensitive": False, "description": "Daily upgrade scan (sync Plex, search indexers, enrich ratings)"},
            "run_hour": {"type": "integer", "default": 3,    "sensitive": False, "description": "Hour (0-23) for the daily upgrade scan", "min": 0, "max": 23},
        },
        "trending_watchlist": {
            "_type": "object",
            "enabled":            {"type": "boolean", "default": False, "sensitive": False, "description": "Automatically add trending Trakt movies to Radarr"},
            "quality_profile_id": {"type": "integer", "default": 0,     "sensitive": False, "description": "Radarr quality profile (0 = use first available)", "min": 0},
            "limit":              {"type": "integer", "default": 15,    "sensitive": False, "description": "Number of trending movies to check per cycle", "min": 1, "max": 40},
            "interval_hours":     {"type": "integer", "default": 2,     "sensitive": False, "description": "Hours between trending watchlist checks (legacy, use global)", "min": 1, "max": 24},
            "search_on_add":      {"type": "boolean", "default": True,  "sensitive": False, "description": "Trigger Radarr search when adding a movie"},
        },
        "trending_tv_watchlist": {
            "_type": "object",
            "enabled":            {"type": "boolean", "default": False, "sensitive": False, "description": "Automatically add trending Trakt first-season TV shows to Sonarr"},
            "quality_profile_id": {"type": "integer", "default": 0,     "sensitive": False, "description": "Sonarr quality profile (0 = use first available)", "min": 0},
            "limit":              {"type": "integer", "default": 10,    "sensitive": False, "description": "Number of trending shows to check per cycle", "min": 1, "max": 40},
            "search_on_add":      {"type": "boolean", "default": True,  "sensitive": False, "description": "Trigger Sonarr search when adding a show"},
            "allowed_languages":  {"type": "array",   "default": ["en"], "sensitive": False, "description": "Only auto-add shows whose TMDb original language is in this list (ISO 639-1 codes, e.g. en). Empty = allow all languages."},
        },
        "now_available_watchlist": {
            "_type": "object",
            "enabled":            {"type": "boolean", "default": False, "sensitive": False, "description": "Automatically add top available indexer movies to Radarr"},
            "quality_profile_id": {"type": "integer", "default": 0,     "sensitive": False, "description": "Radarr quality profile (0 = use first available)", "min": 0},
            "limit":              {"type": "integer", "default": 15,    "sensitive": False, "description": "Number of available movies to check per cycle", "min": 1, "max": 100},
            "search_on_add":      {"type": "boolean", "default": True,  "sensitive": False, "description": "Trigger Radarr search when adding a movie"},
        },
        "anticipated_watchlist": {
            "_type": "object",
            "enabled":                  {"type": "boolean", "default": False, "sensitive": False, "description": "Pre-order Trakt anticipated (unreleased) titles to Radarr/Sonarr monitored, so they auto-download on release"},
            "movies_enabled":           {"type": "boolean", "default": True,  "sensitive": False, "description": "Include anticipated movies (add to Radarr)"},
            "shows_enabled":            {"type": "boolean", "default": True,  "sensitive": False, "description": "Include anticipated shows (add to Sonarr)"},
            "movie_quality_profile_id": {"type": "integer", "default": 0,     "sensitive": False, "description": "Radarr quality profile for anticipated movies (0 = use first available)", "min": 0},
            "tv_quality_profile_id":    {"type": "integer", "default": 0,     "sensitive": False, "description": "Sonarr quality profile for anticipated shows (0 = use first available)", "min": 0},
            "limit":                    {"type": "integer", "default": 20,    "sensitive": False, "description": "Number of anticipated titles to check per cycle (per media type)", "min": 1, "max": 40},
            "search_on_add":            {"type": "boolean", "default": True,  "sensitive": False, "description": "Trigger a search on add (harmless for unreleased titles; catches any already-available)"},
            "tv_allowed_languages":     {"type": "array",   "default": ["en"], "sensitive": False, "description": "Only auto-add shows whose TMDb original language is in this list (ISO 639-1). Empty = allow all."},
        },
        "latest_auto_grab": {
            "_type": "object",
            "enabled":                {"type": "boolean", "default": False, "sensitive": False, "description": "Auto-grab high-seeder releases from the Latest feed before they enter Trending/Now Available"},
            "min_seeders":            {"type": "integer", "default": 60,    "sensitive": False, "description": "Minimum seeders required to auto-grab", "min": 1, "max": 5000},
            "max_age_hours":          {"type": "integer", "default": 24,    "sensitive": False, "description": "Only consider releases published within this many hours", "min": 1, "max": 168},
            "scan_interval_minutes":  {"type": "integer", "default": 15,    "sensitive": False, "description": "Minutes between automatic scans", "min": 5, "max": 120},
            "quality_profile_id":     {"type": "integer", "default": 0,     "sensitive": False, "description": "Radarr quality profile (0 = use Radarr default). Should NOT include 2160p Remux — that's the 4K auto-grab's job", "min": 0},
        },
        "franchise_completer": {
            "_type": "object",
            "enabled":              {"type": "boolean", "default": False, "sensitive": False, "description": "Auto-grab missing parts of super-popular almost-complete TMDb franchises"},
            "hour":                 {"type": "integer", "default": 23,    "sensitive": False, "description": "Hour (0-23, server clock) for nightly franchise completer run", "min": 0, "max": 23},
            "minute":               {"type": "integer", "default": 30,    "sensitive": False, "description": "Minute (0-59) for nightly franchise completer run", "min": 0, "max": 59},
            "quality_profile_id":   {"type": "integer", "default": 0,     "sensitive": False, "description": "Radarr quality profile (0 = Radarr default profile from Settings)", "min": 0},
            "min_fame":             {"type": "integer", "default": 500000,"sensitive": False, "description": "Minimum collection fame_score (max IMDb votes across released parts)", "min": 0, "max": 5000000},
            "max_missing_parts":    {"type": "integer", "default": 1,     "sensitive": False, "description": "Only consider franchises missing at most this many released parts", "min": 1, "max": 10},
            "movies_per_night":     {"type": "integer", "default": 1,     "sensitive": False, "description": "Max movies to grab per nightly run", "min": 1, "max": 10},
            "min_year":             {"type": "integer", "default": 0,     "sensitive": False, "description": "Oldest release year to consider (0 = no floor)", "min": 0, "max": 2100},
        },
        "watchlist_digital_dates": {
            "_type": "object",
            "enabled": {"type": "boolean", "default": False, "sensitive": False, "description": "Morning check for newly dated watchlist movies (US digital TMDb type-4)"},
            "hour":    {"type": "integer", "default": 6,    "sensitive": False, "description": "Hour (0-23, America/New_York) for morning digital-date check", "min": 0, "max": 23},
            "minute":  {"type": "integer", "default": 0,    "sensitive": False, "description": "Minute (0-59, America/New_York) for morning digital-date check", "min": 0, "max": 59},
        },
        "upgrade_4k_auto_grab": {
            "_type": "object",
            "enabled":                {"type": "boolean", "default": False, "sensitive": False, "description": "Auto-grab 2160p Remux releases for any movie not already in your 4K library"},
            "min_seeders":            {"type": "integer", "default": 100,   "sensitive": False, "description": "Minimum seeders required to auto-grab", "min": 1, "max": 5000},
            "max_age_hours":          {"type": "integer", "default": 24,    "sensitive": False, "description": "Only consider releases published within this many hours", "min": 1, "max": 168},
            "scan_interval_minutes":  {"type": "integer", "default": 15,    "sensitive": False, "description": "Minutes between automatic scans", "min": 5, "max": 120},
            "quality_profile_id":     {"type": "integer", "default": 0,     "sensitive": False, "description": "Radarr quality profile to use for 4K grabs (must accept 2160p Remux)", "min": 0},
            "plex_4k_section_id":     {"type": "integer", "default": 0,     "sensitive": False, "description": "Plex library section that contains your 4K movies — movies in this section are skipped", "min": 0},
        },
        "genre_roulette": {
            "_type": "object",
            "enabled":              {"type": "boolean", "default": False, "sensitive": False, "description": "Nightly Genre Roulette: one well-known film from each spun genre"},
            "hour":                 {"type": "integer", "default": 3,     "sensitive": False, "description": "Hour (0-23, server clock) for nightly Genre Roulette run", "min": 0, "max": 23},
            "minute":               {"type": "integer", "default": 0,     "sensitive": False, "description": "Minute (0-59) for nightly Genre Roulette run", "min": 0, "max": 59},
            "genres_per_night":     {"type": "integer", "default": 3,     "sensitive": False, "description": "How many genres to spin each night", "min": 1, "max": 18},
            "movies_per_night":     {"type": "integer", "default": 3,     "sensitive": False, "description": "How many movies to grab each night (prod: one per spun genre)", "min": 1, "max": 20},
            "year_from":            {"type": "integer", "default": 0,     "sensitive": False, "description": "Earliest release year (0 = this year - 40)", "min": 0, "max": 2100},
            "year_to":              {"type": "integer", "default": 0,     "sensitive": False, "description": "Latest release year (0 = this year - 3)", "min": 0, "max": 2100},
            "min_votes":            {"type": "integer", "default": 800,   "sensitive": False, "description": "TMDb votes prefilter", "min": 0, "max": 1000000},
            "imdb_min_votes":       {"type": "integer", "default": 120000,"sensitive": False, "description": "IMDb vote fame floor", "min": 0, "max": 10000000},
            "imdb_min_rating":      {"type": "number",  "default": 0.0,   "sensitive": False, "description": "IMDb rating floor (0 = none)", "min": 0, "max": 10},
            "min_seeders":          {"type": "integer", "default": 5,     "sensitive": False, "description": "Minimum seeders required to grab", "min": 0, "max": 1000},
            "quality_profile_id":   {"type": "integer", "default": 0,     "sensitive": False, "description": "Radarr quality profile (0 = first available)", "min": 0},
            "genres":               {"type": "array",   "default": [],    "sensitive": False, "description": "Genres in play (empty = all)"},
            "trackers":             {"type": "array",   "default": [],    "sensitive": False, "description": "Tracker names to search (empty = all movie trackers)"},
        },
        "movie_night": {
            "_type": "object",
            "enabled":              {"type": "boolean", "default": False, "sensitive": False, "description": "Weekly canon film grab in 2160p Remux"},
            "weekday":              {"type": "integer", "default": 4,     "sensitive": False, "description": "Weekday to run (0=Mon … 6=Sun)", "min": 0, "max": 6},
            "hour":                 {"type": "integer", "default": 18,    "sensitive": False, "description": "Hour (0-23) to run", "min": 0, "max": 23},
            "minute":               {"type": "integer", "default": 0,     "sensitive": False, "description": "Minute (0-59) to run", "min": 0, "max": 59},
            "quality_profile_id":   {"type": "integer", "default": 0,     "sensitive": False, "description": "Radarr quality profile (must accept 2160p Remux)", "min": 0},
            "min_year":             {"type": "integer", "default": 0,     "sensitive": False, "description": "Oldest release year to consider (0 = no floor)", "min": 0, "max": 2100},
            "trackers":             {"type": "array",   "default": [],    "sensitive": False, "description": "Tracker names to search (empty = all movie trackers)"},
            "imdb_min_votes":       {"type": "integer", "default": 200000,"sensitive": False, "description": "IMDb vote fame floor for weekly remux grabs", "min": 0, "max": 10000000},
            "imdb_min_rating":      {"type": "number",  "default": 7.0,   "sensitive": False, "description": "IMDb rating quality floor for weekly remux grabs", "min": 0, "max": 10},
        },
    },
}

# ---------------------------------------------------------------------------
# Mapping: env var name -> settings dotted path
# ---------------------------------------------------------------------------
# Used during bootstrap to read existing environment variables into settings.
# ---------------------------------------------------------------------------
ENV_VAR_MAP: Dict[str, str] = {
    # Plex
    "PLEX_URL":         "plex.url",
    "PLEX_TOKEN":       "plex.token",
    "PLEX_MACHINE_ID":  "plex.machine_id",
    "PLEX_ACCOUNT_ID":  "plex.account_id",
    "PLEX_DB":          "plex.db_remote_path",
    # qBittorrent
    "QBITTORRENT_URL":  "qbittorrent.url",
    "QBITTORRENT_USER": "qbittorrent.username",
    "QBITTORRENT_PASS": "qbittorrent.password",
    # Sonarr
    "SONARR_URL":     "sonarr.url",
    "SONARR_API_KEY": "sonarr.api_key",
    # Radarr
    "RADARR_URL":                "radarr.url",
    "RADARR_API_KEY":            "radarr.api_key",
    "RADARR_MONITORING_ENABLED": "radarr.monitoring_enabled",
    "RADARR_QBIT_CATEGORY":     "qbittorrent.radarr_category",
    # APIs
    "TMDB_API_KEY":       "apis.tmdb.api_key",
    "OMDB_API_KEY":       "apis.omdb.api_key",
    "OPENAI_API_KEY":     "apis.openai.api_key",
    # Indexer
    "INDEXER_PROVIDER": "indexer.provider",
    "INDEXER_BASE_URL": "indexer.base_url",
    "INDEXER_API_KEY":  "indexer.api_key",
    # Network
    "PORT":        "network.flask_port",
    "SECRET_KEY":  "network.flask_secret_key",
    "IS_DOCKER":   "network.is_docker",
}

# Env vars that feed into migration (old-style paths not in new schema)
_ENV_VAR_MIGRATION_MAP: Dict[str, str] = {
    "SHIELD_PLEX_IP":     "_migrate.shield_ip",
    "DOWNLOADS_ROOT":     "media.downloads_root",
    "MOVIES_ROOT":        "_migrate.movies_root",
    "RECENT_ROOT":        "_migrate.recent_root",
    "TV_ROOT":            "_migrate.tv_root",
    "TV_4K_ROOT":         "_migrate.tv_4k_root",
    "TV_REMUX_ROOT":      "_migrate.tv_remux_root",
    "FULLRIPS_ROOT":      "_migrate.fullrips_root",
    "FOURKAY_ROOT":       "_migrate.fourkay_root",
    "DATABASE_PATH":      "media.database_path",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _infer_data_dir() -> Path:
    """Determine the data directory for settings.json.

    In Docker the canonical path is LUMA_DATA_DIR (default /app/data).
    Outside Docker we fall back to the ``data/`` directory relative to the
    project root.
    """
    from app.paths import data_dir
    # Try the configured (Docker) path first
    docker_data = data_dir()
    if docker_data.is_dir():
        return docker_data

    # Fall back: same directory as DATABASE_PATH
    db_path = os.environ.get("DATABASE_PATH", "")
    if db_path:
        p = Path(db_path).parent
        if p.is_dir():
            return p

    # Last resort: data/ relative to this file's grandparent (project root)
    project_data = Path(__file__).resolve().parent.parent / "data"
    project_data.mkdir(parents=True, exist_ok=True)
    return project_data


def _get_schema_node(schema: dict, dotted_path: str) -> Optional[dict]:
    """Walk the schema tree to find the leaf definition for *dotted_path*.

    Returns ``None`` if the path does not resolve to a leaf node.
    """
    parts = dotted_path.split(".")
    node = schema
    for part in parts:
        if part not in node:
            return None
        node = node[part]
    # If we arrived at a dict that has a "type" key it is a leaf definition
    if isinstance(node, dict) and "type" in node:
        return node
    return None


def _build_defaults(schema: dict) -> dict:
    """Recursively build a complete defaults object from the schema."""
    result: Dict[str, Any] = {}
    for key, value in schema.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and value.get("_type") == "object":
            result[key] = _build_defaults(value)
        elif isinstance(value, dict) and "type" in value:
            result[key] = copy.deepcopy(value.get("default", _type_default(value["type"])))
        # else: skip metadata keys
    return result


def _type_default(type_name: str) -> Any:
    """Return a sensible empty default for a JSON schema type."""
    return {
        "string":  "",
        "integer": 0,
        "number":  0.0,
        "boolean": False,
        "array":   [],
        "object":  {},
    }.get(type_name, "")


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict.

    - dict values are merged recursively.
    - All other values in *override* replace the corresponding value in *base*.
    """
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _coerce_env_value(raw: str, schema_node: dict) -> Any:
    """Convert a raw environment-variable string to the correct Python type
    based on the schema node definition.
    """
    type_name = schema_node.get("type", "string")
    if type_name == "integer":
        try:
            return int(raw)
        except (ValueError, TypeError):
            return schema_node.get("default", 0)
    elif type_name == "number":
        try:
            return float(raw)
        except (ValueError, TypeError):
            return schema_node.get("default", 0.0)
    elif type_name == "boolean":
        return raw.lower() in ("true", "1", "yes")
    elif type_name == "array":
        # Env vars that are arrays are unlikely but handle comma-separated
        return [s.strip() for s in raw.split(",") if s.strip()]
    return raw  # string


def _collect_sensitive_paths(schema: dict, prefix: str = "") -> List[str]:
    """Return a list of dotted paths that are marked sensitive."""
    paths: List[str] = []
    for key, value in schema.items():
        if key.startswith("_"):
            continue
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value.get("_type") == "object":
            paths.extend(_collect_sensitive_paths(value, full))
        elif isinstance(value, dict) and value.get("sensitive"):
            paths.append(full)
    return paths


def _set_nested(data: dict, dotted_path: str, value: Any) -> None:
    """Set a value in a nested dict using a dotted path."""
    parts = dotted_path.split(".")
    node = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _get_nested(data: dict, dotted_path: str, default: Any = None) -> Any:
    """Get a value from a nested dict using a dotted path."""
    parts = dotted_path.split(".")
    node = data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _mask_secrets(data: dict, schema: dict, prefix: str = "") -> dict:
    """Return a deep copy of *data* with sensitive fields replaced by SECRET_MASK.

    Handles both object-schema nodes and top-level array nodes whose items
    have sensitive properties.
    """
    result = {}
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        schema_entry = schema.get(key)
        if schema_entry is None:
            # Key not in schema -- pass through as-is
            result[key] = copy.deepcopy(value)
            continue
        if isinstance(schema_entry, dict) and schema_entry.get("_type") == "object":
            if isinstance(value, dict):
                result[key] = _mask_secrets(value, schema_entry, full)
            else:
                result[key] = copy.deepcopy(value)
        elif isinstance(schema_entry, dict) and schema_entry.get("type") == "array" and isinstance(value, list):
            # Mask sensitive fields inside array items
            items_schema = schema_entry.get("items", {})
            item_props = items_schema.get("properties", {})
            sensitive_keys = {k for k, v in item_props.items() if isinstance(v, dict) and v.get("sensitive")}
            if sensitive_keys:
                masked_list = []
                for item in value:
                    if isinstance(item, dict):
                        masked_item = copy.deepcopy(item)
                        for sk in sensitive_keys:
                            if sk in masked_item and masked_item[sk] and str(masked_item[sk]).strip():
                                masked_item[sk] = SECRET_MASK
                        masked_list.append(masked_item)
                    else:
                        masked_list.append(copy.deepcopy(item))
                result[key] = masked_list
            else:
                result[key] = copy.deepcopy(value)
        elif isinstance(schema_entry, dict) and schema_entry.get("sensitive"):
            # Replace with mask
            if value and str(value).strip():
                result[key] = SECRET_MASK
            else:
                result[key] = ""
        else:
            result[key] = copy.deepcopy(value)
    return result


def _strip_mask_values(data: dict, schema: dict) -> dict:
    """Remove any key whose value is SECRET_MASK so it won't overwrite the real
    secret during a PUT / deep-merge.  Works recursively.

    Also handles arrays: if a list item dict has SECRET_MASK values for
    sensitive keys, those keys are stripped from the item so the merge
    preserves existing secrets.
    """
    result = {}
    for key, value in data.items():
        schema_entry = schema.get(key)
        if isinstance(value, dict):
            child_schema = schema_entry if isinstance(schema_entry, dict) else {}
            cleaned = _strip_mask_values(value, child_schema)
            if cleaned:
                result[key] = cleaned
        elif isinstance(value, list):
            # Handle arrays -- strip SECRET_MASK from item dicts
            cleaned_list = []
            items_schema = {}
            if isinstance(schema_entry, dict):
                items_schema = schema_entry.get("items", {}).get("properties", {})
            for item in value:
                if isinstance(item, dict):
                    cleaned_item = {}
                    for ik, iv in item.items():
                        if iv == SECRET_MASK:
                            continue  # skip masked sentinel
                        cleaned_item[ik] = iv
                    cleaned_list.append(cleaned_item)
                else:
                    cleaned_list.append(item)
            result[key] = cleaned_list
        elif value == SECRET_MASK:
            # Skip -- do not include masked sentinel
            continue
        else:
            result[key] = value
    return result


def _validate_value(value: Any, schema_node: dict) -> Tuple[bool, str]:
    """Validate a single value against its schema node.

    Returns (is_valid, error_message).
    """
    type_name = schema_node.get("type", "string")

    # Type check
    type_map = {
        "string":  str,
        "integer": int,
        "number":  (int, float),
        "boolean": bool,
        "array":   list,
        "object":  dict,
    }
    expected = type_map.get(type_name)
    if expected and not isinstance(value, expected):
        return False, f"Expected type {type_name}, got {type(value).__name__}"

    # Min / max for numbers
    if type_name in ("integer", "number"):
        if "min" in schema_node and value < schema_node["min"]:
            return False, f"Value {value} is below minimum {schema_node['min']}"
        if "max" in schema_node and value > schema_node["max"]:
            return False, f"Value {value} is above maximum {schema_node['max']}"

    # Enum
    if "enum" in schema_node and value not in schema_node["enum"]:
        return False, f"Value '{value}' not in allowed values: {schema_node['enum']}"

    # Max length for strings
    if type_name == "string" and "max_length" in schema_node:
        if len(value) > schema_node["max_length"]:
            return False, f"String length {len(value)} exceeds max {schema_node['max_length']}"

    return True, ""


def _coerce_types(data: dict, schema: dict) -> dict:
    """Walk *data* and coerce string values to the type declared in *schema*
    where safe (e.g. ``"1.5"`` -> ``1.5`` for ``number`` fields).
    Mutates *data* in place and returns it.
    """
    for key, value in list(data.items()):
        schema_entry = schema.get(key)
        if schema_entry is None:
            continue
        if isinstance(schema_entry, dict) and schema_entry.get("_type") == "object":
            if isinstance(value, dict):
                _coerce_types(value, schema_entry)
        elif isinstance(schema_entry, dict) and "type" in schema_entry:
            st = schema_entry["type"]
            if st == "number" and isinstance(value, str) and value.strip():
                try:
                    data[key] = float(value)
                except (ValueError, TypeError):
                    pass
            elif st == "integer" and isinstance(value, str) and value.strip():
                try:
                    data[key] = int(value)
                except (ValueError, TypeError):
                    pass
    return data


def _validate_settings(data: dict, schema: dict, prefix: str = "") -> List[str]:
    """Validate *data* against *schema*.  Returns a list of error strings."""
    errors: List[str] = []
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        schema_entry = schema.get(key)
        if schema_entry is None:
            # Extra key not in schema -- tolerate but warn
            continue
        if isinstance(schema_entry, dict) and schema_entry.get("_type") == "object":
            if isinstance(value, dict):
                errors.extend(_validate_settings(value, schema_entry, full))
            else:
                errors.append(f"{full}: expected object, got {type(value).__name__}")
        elif isinstance(schema_entry, dict) and "type" in schema_entry:
            ok, msg = _validate_value(value, schema_entry)
            if not ok:
                errors.append(f"{full}: {msg}")
    return errors


def _schema_to_json_schema(schema: dict) -> dict:
    """Convert our internal schema format to a JSON Schema (draft-07) object
    suitable for returning from the /api/settings/schema endpoint.
    """
    properties: Dict[str, Any] = {}
    for key, value in schema.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and value.get("_type") == "object":
            properties[key] = _schema_to_json_schema(value)
            properties[key]["type"] = "object"
        elif isinstance(value, dict) and "type" in value:
            prop: Dict[str, Any] = {"type": value["type"]}
            if "default" in value:
                prop["default"] = value["default"]
            if "description" in value:
                prop["description"] = value["description"]
            if value.get("sensitive"):
                prop["sensitive"] = True
            if "min" in value:
                prop["minimum"] = value["min"]
            if "max" in value:
                prop["maximum"] = value["max"]
            if "enum" in value:
                prop["enum"] = value["enum"]
            if "max_length" in value:
                prop["maxLength"] = value["max_length"]
            if value["type"] == "array" and "items" in value:
                prop["items"] = value["items"]
            properties[key] = prop
    result: Dict[str, Any] = {"type": "object", "properties": properties}
    return result


# ---------------------------------------------------------------------------
# Migration helpers -- convert Phase 1 data to Phase 2.5 list formats
# ---------------------------------------------------------------------------

def _migrate_harmony_direct(data: dict) -> bool:
    """Migrate rooms from HA-based harmonyEntityId to direct harmonyHubIp.
    Returns True if migration occurred.
    """
    changed = False

    # Convert rooms: harmonyEntityId -> harmonyHubIp (user must fill in IP)
    devices = data.get("devices")
    if isinstance(devices, dict):
        rooms = devices.get("rooms", [])
        if isinstance(rooms, list):
            for room in rooms:
                if not isinstance(room, dict):
                    continue
                if "harmonyEntityId" in room:
                    room.pop("harmonyEntityId", None)
                    room.setdefault("harmonyHubIp", "")
                    changed = True

    if changed:
        _log_migration("Migrated HA -> direct Harmony Hub (harmonyHubIp)")
    return changed


def _migrate_devices(data: dict) -> bool:
    """Migrate old devices.shield into rooms[0] and remove devices.shield.
    Returns True if migration occurred.
    """
    devices = data.get("devices")
    if not isinstance(devices, dict):
        return False

    old_shield = devices.pop("shield", None)
    if old_shield is None or not isinstance(old_shield, dict):
        return False

    rooms = devices.get("rooms", [])
    if not isinstance(rooms, list):
        rooms = []

    shield_ip = old_shield.get("ip", "")
    adb_port = old_shield.get("adb_port", 5555)

    # Check if shield IP already exists in rooms
    ip_exists = any(r.get("ip") == shield_ip for r in rooms if isinstance(r, dict))

    if rooms and not ip_exists:
        # Merge shield info into first room
        rooms[0]["ip"] = shield_ip
        rooms[0]["adb_port"] = adb_port
        rooms[0]["type"] = rooms[0].get("type", "shield")
    elif not rooms:
        rooms.append({
            "name": "TV",
            "ip": shield_ip,
            "adb_port": adb_port,
            "plex_client_port": 32500,
            "type": "shield",
            "plex_machine_id": "",
        })
    elif ip_exists:
        # Update the matching room with ADB port
        for r in rooms:
            if isinstance(r, dict) and r.get("ip") == shield_ip:
                r.setdefault("adb_port", adb_port)
                r.setdefault("type", "shield")
                break

    # Ensure all rooms have the new fields
    for r in rooms:
        if isinstance(r, dict):
            r.setdefault("adb_port", 5555)
            r.setdefault("plex_client_port", r.pop("port", 32500))
            r.setdefault("type", "shield")

    devices["rooms"] = rooms
    _log_migration("Migrated devices.shield -> rooms list")
    return True


def _migrate_rooms_shape(data: dict) -> bool:
    """Ensure all rooms have the new shape (adb_port, plex_client_port, type).
    Renames old 'port' to 'plex_client_port' if needed.
    """
    devices = data.get("devices")
    if not isinstance(devices, dict):
        return False
    rooms = devices.get("rooms")
    if not isinstance(rooms, list):
        return False

    changed = False
    for r in rooms:
        if not isinstance(r, dict):
            continue
        if "port" in r and "plex_client_port" not in r:
            r["plex_client_port"] = r.pop("port")
            changed = True
        r.setdefault("adb_port", 5555)
        r.setdefault("plex_client_port", 32500)
        r.setdefault("type", "shield")
        r.setdefault("plex_machine_id", "")
    return changed


def _migrate_media_paths(data: dict) -> bool:
    """Migrate old media.{movies_root, recent_root, ...} into media.paths[].
    Returns True if migration occurred.
    """
    media = data.get("media")
    if not isinstance(media, dict):
        return False

    # Map of old field -> (name, type)
    old_fields = {
        "movies_root":   ("Movies",            "movies"),
        "recent_root":   ("Recently Released",  "recent"),
        "tv_root":       ("TV Shows",           "tv"),
        "fourkay_root":  ("4K Movies",          "4k"),
        "fullrips_root": ("Full Rips",          "fullrips"),
        "tv_4k_root":    ("4K TV",              "tv_4k"),
        "tv_remux_root": ("Remux TV",           "tv_remux"),
    }

    found_old = any(k in media for k in old_fields)
    if not found_old:
        return False

    paths = media.get("paths", [])
    if not isinstance(paths, list):
        paths = []

    existing_types = {p.get("type") for p in paths if isinstance(p, dict)}

    for old_key, (name, ptype) in old_fields.items():
        old_val = media.pop(old_key, None)
        if old_val and ptype not in existing_types:
            paths.append({"name": name, "path": old_val, "type": ptype})

    media["paths"] = paths
    _log_migration("Migrated media individual path fields -> paths list")
    return True


def _migrate_remove_trackers(data: dict) -> bool:
    """Remove legacy tracker and jackett keys; fix deprecated movie_provider.
    Tracker APIs (PHD/BHD) and Jackett were removed — strip stale config.
    Returns True if migration occurred.
    """
    changed = False
    for key in ("jackett", "trackers"):
        if key in data:
            del data[key]
            changed = True
    sources = data.get("sources")
    if isinstance(sources, dict) and sources.get("movie_provider") in ("auto", "internal"):
        sources["movie_provider"] = "radarr"
        changed = True
    if changed:
        _log_migration("Migrated: removed legacy trackers/jackett, set sources.movie_provider=radarr")
    return changed


def _migrate_room_ids(data: dict) -> bool:
    """Ensure every room in devices.rooms has a stable ``id`` field.
    Generates an 8-char hex ID (uuid4) for rooms that lack one.
    Returns True if any IDs were added.
    """
    import uuid
    devices = data.get("devices")
    if not isinstance(devices, dict):
        return False
    rooms = devices.get("rooms")
    if not isinstance(rooms, list):
        return False
    changed = False
    for r in rooms:
        if isinstance(r, dict) and not r.get("id"):
            r["id"] = uuid.uuid4().hex[:8]
            changed = True
    if changed:
        _log_migration("Migrated room IDs: assigned stable IDs to rooms missing them")
    return changed


def _migrate_media_paths_v2(data: dict) -> bool:
    """Migrate media.paths entries to V2 shape.

    - Removes ``plex_path`` (replaced by ``plex_locations`` from Plex sync).
    - Adds ``kodi_path``, ``plex_section_id``, ``plex_locations`` where missing.

    Returns True if any entries were changed.
    """
    media = data.get("media")
    if not isinstance(media, dict):
        return False
    paths = media.get("paths")
    if not isinstance(paths, list):
        return False

    changed = False
    for entry in paths:
        if not isinstance(entry, dict):
            continue
        # Remove old plex_path field
        if "plex_path" in entry:
            entry.pop("plex_path")
            changed = True
        # Add new fields with defaults
        if "kodi_path" not in entry:
            entry["kodi_path"] = ""
            changed = True
        if "plex_section_id" not in entry:
            entry["plex_section_id"] = None
            changed = True
        if "plex_locations" not in entry:
            entry["plex_locations"] = []
            changed = True

    if changed:
        _log_migration("Migrated media.paths to V2 shape (kodi_path, plex_section_id, plex_locations)")
    return changed


def _migrate_media_types_from_paths(data: dict) -> bool:
    """Populate ``media.types`` from existing ``media.paths`` type values.

    Only runs if ``media.types`` is empty/missing AND ``media.paths`` has
    entries with type values.  Seeds the types array with definitions
    matching the hardcoded auto_rename.py routing rules.
    """
    media = data.get("media")
    if not isinstance(media, dict):
        return False

    existing_types = media.get("types")
    if isinstance(existing_types, list) and len(existing_types) > 0:
        return False  # already populated

    paths = media.get("paths")
    if not isinstance(paths, list) or not paths:
        return False

    used_types = set()
    for p in paths:
        if isinstance(p, dict) and p.get("type"):
            used_types.add(p["type"])

    if not used_types:
        return False

    # Known type definitions matching current auto_rename.py rules
    known = {
        "4k":       {"label": "4K Movies",         "media_category": "movie", "description": "4K / UHD / 2160p movies (remux quality)",      "rules": {"quality_keywords": ["2160p", "4k", "uhd"], "year_rule": None, "is_default": False}},
        "fullrips": {"label": "Full Rips",          "media_category": "movie", "description": "Blu-ray remux / full disc rips",                "rules": {"quality_keywords": ["remux", "blu-?ray\\s*remux", "bd\\s*remux"], "year_rule": None, "is_default": False}},
        "tv_4k":    {"label": "4K TV",              "media_category": "tv",    "description": "4K / UHD / 2160p TV shows",                     "rules": {"quality_keywords": ["2160p", "4k", "uhd"], "year_rule": None, "is_default": False}},
        "tv_remux": {"label": "Remux TV",           "media_category": "tv",    "description": "1080p remux TV shows",                          "rules": {"quality_keywords": ["remux"], "year_rule": None, "is_default": False}},
        "recent":   {"label": "Recently Released",  "media_category": "movie", "description": "Movies from current year or last year",         "rules": {"quality_keywords": [], "year_rule": "recent", "is_default": False}},
        "movies":   {"label": "Movies",             "media_category": "movie", "description": "Default movie library (older / standard)",      "rules": {"quality_keywords": [], "year_rule": None, "is_default": True}},
        "tv":       {"label": "TV Shows",           "media_category": "tv",    "description": "Default TV show library",                       "rules": {"quality_keywords": [], "year_rule": None, "is_default": True}},
    }

    # Order = priority (first match wins): quality-based first, then year, then defaults last
    priority_order = ["4k", "fullrips", "tv_4k", "tv_remux", "recent", "movies", "tv"]
    types_list = []

    for type_id in priority_order:
        if type_id in used_types:
            defn = known.get(type_id, {})
            types_list.append({"id": type_id, **defn})
            used_types.discard(type_id)

    # Any remaining unknown types get minimal definitions
    for type_id in sorted(used_types):
        types_list.append({
            "id": type_id,
            "label": type_id.replace("_", " ").title(),
            "media_category": "tv" if "tv" in type_id else "movie",
            "description": "",
            "rules": {"quality_keywords": [], "year_rule": None, "is_default": False},
        })

    media["types"] = types_list
    _log_migration("Migrated %d media types from existing paths", len(types_list))
    return True


def _migrate_plex_services_to_kodi(data: dict) -> bool:
    """Convert all plex services to kodi services across all rooms/devices.

    Walks devices.rooms[*].devices[*].services[*] and converts type: 'plex'
    to type: 'kodi' with empty kodi fields + kodi_mount_point.
    Removes plex-specific fields. Preserves service id.
    Returns True if any migration occurred.
    """
    devices = data.get("devices")
    if not isinstance(devices, dict):
        return False
    rooms = devices.get("rooms")
    if not isinstance(rooms, list):
        return False

    changed = False
    for room in rooms:
        if not isinstance(room, dict):
            continue
        for device in (room.get("devices") or []):
            if not isinstance(device, dict):
                continue
            for svc in (device.get("services") or []):
                if not isinstance(svc, dict):
                    continue
                if svc.get("type") != "plex":
                    continue
                # New ADB-based plex service — do not migrate
                if not svc.get("plex_client_port") and not svc.get("plex_launch_delay"):
                    continue
                # Convert legacy plex → kodi
                svc["type"] = "kodi"
                svc.setdefault("kodi_host", "")
                svc.setdefault("kodi_port", 8080)
                svc.setdefault("kodi_username", "")
                svc.setdefault("kodi_password", "")
                svc.setdefault("kodi_mount_point", "")
                # Remove plex-specific fields
                svc.pop("plex_client_port", None)
                svc.pop("plex_machine_id", None)
                svc.pop("plex_launch_delay", None)
                svc.pop("plex_launch_delay_hot", None)
                changed = True

    if changed:
        _log_migration("Migrated plex services -> kodi services")
    return changed


def _migrate_remove_features(data: dict) -> bool:
    """Remove the retired 'features' section."""
    if "features" in data:
        del data["features"]
        logger.info("Removed retired 'features' section from settings")
        return True
    return False


def _migrate_remove_twilio(data: dict) -> bool:
    """Remove the retired 'twilio' sub-key from apis."""
    apis = data.get("apis")
    if isinstance(apis, dict) and "twilio" in apis:
        del apis["twilio"]
        logger.info("Removed retired 'apis.twilio' from settings")
        return True
    return False


def _migrate_remove_fanart(data: dict) -> bool:
    """Remove the retired 'fanart' sub-key from apis."""
    apis = data.get("apis")
    if isinstance(apis, dict) and "fanart" in apis:
        del apis["fanart"]
        logger.info("Removed retired 'apis.fanart' from settings")
        return True
    return False


def _migrate_hero_schema(data: dict) -> bool:
    """Remove retired hero fields (schedule_hour)."""
    hero = (data.get("ui") or {}).get("hero")
    if not isinstance(hero, dict):
        return False
    changed = False
    if "schedule_hour" in hero:
        del hero["schedule_hour"]
        changed = True
    if changed:
        logger.info("Removed retired hero field: schedule_hour")
    return changed


def _migrate_remove_dead_plex_fields(data: dict) -> bool:
    """Remove retired Plex fields (account_id, db_remote_path, db_local_dir, db_sync_interval)."""
    plex = data.get("plex")
    if not isinstance(plex, dict):
        return False
    dead_keys = ["account_id", "db_remote_path", "db_local_dir", "db_sync_interval"]
    removed = [k for k in dead_keys if k in plex]
    for k in removed:
        del plex[k]
    if removed:
        logger.info(f"Removed retired Plex fields: {', '.join(removed)}")
        return True
    return False


def _migrate_automation_features(data: dict) -> bool:
    """Introduce the Auto-grab / 4K & upgrades feature switches.

    Before this, every job had only its own ``enabled`` flag, and two jobs
    defaulted ON when the key was missing (the daily upgrade scan and the
    watchlist release-date check). New installs now start with everything
    OFF, so for an existing settings file this pins each job's effective
    state explicitly and turns a group switch on when any of its jobs was
    running. Net effect: exactly the same jobs keep running after upgrade.

    Runs only on settings that predate the switches (no ``auto_grab`` /
    ``upgrades`` block yet). A fresh bootstrap already has both blocks.
    """
    from app.automation_features import GROUPS, MEMBER_DEFAULTS

    auto = data.get("automation")
    if not isinstance(auto, dict):
        return False
    if "auto_grab" in auto and "upgrades" in auto:
        return False

    # Defaults the jobs used before the switches existed.
    old_defaults = dict(MEMBER_DEFAULTS)
    changed = []
    for group, members in GROUPS.items():
        if group in auto:
            continue
        any_on = False
        for job in members:
            block = auto.get(job)
            if not isinstance(block, dict):
                block = {}
                auto[job] = block
            on = bool(block.get("enabled", old_defaults.get(job, False)))
            if "enabled" not in block:
                block["enabled"] = on
            any_on = any_on or on
        auto[group] = {"enabled": any_on}
        changed.append(f"{group}={'on' if any_on else 'off'}")

    wdd = auto.get("watchlist_digital_dates")
    if not isinstance(wdd, dict):
        wdd = {}
        auto["watchlist_digital_dates"] = wdd
    if "enabled" not in wdd:
        wdd["enabled"] = True  # was default-on
        changed.append("watchlist_digital_dates=on (kept)")

    logger.info(f"Automation feature switches added: {', '.join(changed)}")
    return True


def _migrate_genre_roulette_block(data: dict) -> bool:
    """v276: ensure automation.genre_roulette exists on disk (Settings card + legacy engine).

    The block was never in SETTINGS_SCHEMA, so sparse installs never persisted it and the
    nightly engine fell through to code defaults (movies_per_night was wrongly 2). Seed the
    schema defaults once when missing; do not overwrite an existing block.
    """
    auto = data.get("automation")
    if not isinstance(auto, dict):
        return False
    if isinstance(auto.get("genre_roulette"), dict):
        return False
    defaults = _build_defaults(SETTINGS_SCHEMA).get("automation", {}).get("genre_roulette")
    if not isinstance(defaults, dict):
        return False
    auto["genre_roulette"] = copy.deepcopy(defaults)
    logger.info(
        "automation.genre_roulette seeded (movies_per_night=%s, genres_per_night=%s)",
        defaults.get("movies_per_night"), defaults.get("genres_per_night"),
    )
    return True


def _migrate_rename_requests_to_portal(data: dict) -> bool:
    """Rename top-level 'requests' section to 'request_portal'."""
    if 'requests' in data:
        data['request_portal'] = data.pop('requests')
        logger.info("Renamed 'requests' section to 'request_portal'")
        return True
    return False


def _migrate_remove_remote_lock(data: dict) -> bool:
    """Remove security.remote_lock setting (merged into access.py)."""
    security = data.get('security')
    if isinstance(security, dict) and 'remote_lock' in security:
        del security['remote_lock']
        logger.info("Removed security.remote_lock (merged into access.py)")
        return True
    return False


def _migrate_access_mode_to_auth_required(data: dict) -> bool:
    """Replace the retired security.access_mode with security.auth_required.

    Mapping (only when auth_required isn't already saved):
    - pin -> enabled (everyone must authenticate)
    - pin_remote, open -> disabled_local (remote never gets implicit access)
    - empty -> nothing saved (the auth_required default applies)
    - anything else -> enabled

    The access_mode key is always deleted. Idempotent.
    """
    security = data.get('security')
    if not isinstance(security, dict) or 'access_mode' not in security:
        return False

    old_mode = str(security.pop('access_mode') or '').strip()
    if old_mode and 'auth_required' not in security:
        new_mode = {'pin': 'enabled', 'pin_remote': 'disabled_local',
                    'open': 'disabled_local'}.get(old_mode, 'enabled')
        security['auth_required'] = new_mode
        _log_migration("Migrated access_mode '%s' -> auth_required '%s'", old_mode, new_mode)
    else:
        logger.info("Removed security.access_mode")
    return True


def _migrate_strip_plaintext_request_password(data: dict) -> bool:
    """Strip any plaintext 'password' field from request_portal.
    
    password_hash is the only valid field; plaintext password should never be saved.
    Also ensures exclude_libraries is a list.
    """
    portal = data.get('request_portal')
    if not isinstance(portal, dict):
        return False
    
    changed = False
    
    # Remove plaintext password if present
    if 'password' in portal:
        del portal['password']
        logger.info("Stripped plaintext password from request_portal")
        changed = True
    
    # Ensure exclude_libraries is a list
    excl = portal.get('exclude_libraries')
    if excl is not None and not isinstance(excl, list):
        if isinstance(excl, str):
            portal['exclude_libraries'] = [x.strip() for x in excl.split(',') if x.strip()]
        else:
            portal['exclude_libraries'] = []
        logger.info("Converted exclude_libraries to list")
        changed = True
    
    return changed


def _migrate_seed_hostnames(data: dict, data_dir=None) -> bool:
    """Introduce security.hostnames, seeded once so LAN devices that open Luma
    by name keep working without sign-in after the upgrade.

    Runs only while the key is absent. Sources (see app.lan_hosts): names LAN
    clients used in the last 30 days when that record has any; otherwise
    private names from APP_URL plus this machine's host name and its .local /
    .lan forms. The request-portal host name is never seeded.
    """
    security = data.setdefault('security', {})
    if not isinstance(security, dict) or 'hostnames' in security:
        return False
    from app import lan_hosts

    portal_cfg = data.get('request_portal')
    portal = portal_cfg.get('hostname') if isinstance(portal_cfg, dict) else ''
    sources = lan_hosts.seed_candidates(
        data_dir if data_dir is not None else _infer_data_dir(),
        configured_urls=[os.environ.get('APP_URL', '')],
        exclude=[portal or ''],
    )
    names = []
    for group in sources.values():
        names.extend(n for n in group if n not in names)
    security['hostnames'] = ', '.join(names)
    logger.info("Seeded security.hostnames: %s (sources: %s)", security['hostnames'] or '(none)',
                ', '.join(f"{k}={v}" for k, v in sources.items()))
    return True


# Defaults the live install used before these settings had their current
# defaults. Upgraded installs keep them (see the migrations below).
LEGACY_REQUEST_EXCLUDES = ['Full Rips', '4K Movies', '4K TV', '3D', 'Remux TV']
LEGACY_QBIT_APP_CATEGORY = 'media-search-app'


def _migrate_legacy_request_excludes(data: dict, existing: bool) -> bool:
    """Keep the old hidden-libraries default on an upgraded install.

    Before the rename to request_portal, an unset or empty
    requests.exclude_libraries meant LEGACY_REQUEST_EXCLUDES. A settings file
    from that era (it has a 'requests' section, or no request-page section at
    all) gets that list written out when its own is absent or empty, so 4K,
    Remux and Full Rips copies still don't count as available. Must run
    before the rename. Fresh installs keep the new empty default.
    """
    if not existing or ('requests' not in data and 'request_portal' in data):
        return False
    section = data.setdefault('requests' if 'requests' in data else 'request_portal', {})
    if not isinstance(section, dict):
        return False
    current = section.get('exclude_libraries')
    if current not in (None, [], ''):
        return False
    section['exclude_libraries'] = list(LEGACY_REQUEST_EXCLUDES)
    logger.info("Kept the previous hidden-libraries default for the request page: %s",
                ', '.join(LEGACY_REQUEST_EXCLUDES))
    return True


QBIT_CATEGORY_MARKER = 'legacy_qbit_category'


def _migrate_legacy_qbit_category(data: dict, existing: bool) -> bool:
    """Keep qBittorrent's old app category on an upgraded install, once.

    The category default changed to 'luma'. An existing settings file with no
    qbittorrent.app_category key used LEGACY_QBIT_APP_CATEGORY, and its
    torrents carry that category, so it is written out explicitly. Otherwise
    those torrents would drop out of Activity, origin badges and auto-rename.
    Runs once (a marker under _migrations persists), and a category the user
    cleared on purpose (key present, empty) is left alone.
    """
    done = data.setdefault('_migrations', {})
    if not isinstance(done, dict) or done.get(QBIT_CATEGORY_MARKER):
        return False
    done[QBIT_CATEGORY_MARKER] = True
    qbit = data.setdefault('qbittorrent', {})
    if existing and isinstance(qbit, dict) and 'app_category' not in qbit:
        qbit['app_category'] = LEGACY_QBIT_APP_CATEGORY
        logger.info("Kept the previous qBittorrent app category: %s", LEGACY_QBIT_APP_CATEGORY)
    return True


TRACKER_SEARCH_KEYS = ('movie_categories',)
# v174 (Tester FF-8): the Newznab movie categories a saved value may name (2000 = all movies, which the
# search expands to the sub-categories). Anything else, junk or a huge string is refused with a 400.
KNOWN_MOVIE_CATEGORIES = (2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080)
MOVIE_CATEGORIES_MAX_CHARS = 80


def validate_tracker_search(patch: Any) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """(clean patch, field errors) for a tracker_search write. Only movie_categories is known: a string of
    comma-separated known category numbers (spaces allowed, duplicates dropped, order kept), capped in
    length. Unknown keys, other types, junk and huge strings are errors."""
    if not isinstance(patch, dict):
        return {}, {'tracker_search': 'Must be an object'}
    errors: Dict[str, str] = {}
    clean: Dict[str, Any] = {}
    for k in patch:
        if k not in TRACKER_SEARCH_KEYS:
            errors[str(k)[:40]] = 'Unknown setting'
    if 'movie_categories' in patch:
        v = patch['movie_categories']
        msg = 'Enter movie category numbers separated by commas'
        if not isinstance(v, str) or not v.strip() or len(v) > MOVIE_CATEGORIES_MAX_CHARS:
            errors['movie_categories'] = msg
        else:
            out: List[int] = []
            for part in v.split(','):
                part = part.strip()
                if not (part.isascii() and part.isdigit() and len(part) <= 6) or int(part) not in KNOWN_MOVIE_CATEGORIES:
                    errors['movie_categories'] = msg
                    break
                if int(part) not in out:
                    out.append(int(part))
            if 'movie_categories' not in errors:
                clean['movie_categories'] = ','.join(str(c) for c in out)
    return clean, errors


# v175 (Ops on c1f337b): every legacy search key, wherever an earlier build left it.
LEGACY_INDEXER_SEARCH_KEYS = ('movie_categories', 'max_age_days', 'min_seeders', 'limit')   # 1b4ea74 and before
REMOVED_TRACKER_SEARCH_KEYS = ('max_age_days', 'min_seeders', 'limit')                     # c669f76 (v173)


def _migrate_tracker_search_settings(data: dict) -> bool:
    """v173 (PM): indexer.movie_categories moves to tracker_search.movie_categories (Settings >
    Trackers > Search); a saved value is carried over as is and a value already under tracker_search
    wins.

    v175 (Ops on c1f337b): idempotent. It used to run only while indexer.movie_categories existed, so a
    c669f76 file (the four keys already under tracker_search) kept max_age_days, min_seeders and limit
    there on v174, with no backup and no log. Now it runs whenever ANY legacy key is present
    (indexer.movie_categories/max_age_days/min_seeders/limit, or tracker_search.max_age_days/
    min_seeders/limit): the caller backs settings.json up first (_load -> _backup_before_migration), the
    unused keys are stripped, and one INFO line names what changed. A clean file is a no-op (False: no
    backup, no save, no log). Returns True if anything changed."""
    old = data.get('indexer')
    ts = data.get('tracker_search')
    in_indexer = [k for k in LEGACY_INDEXER_SEARCH_KEYS if isinstance(old, dict) and k in old]
    in_ts = [k for k in REMOVED_TRACKER_SEARCH_KEYS if isinstance(ts, dict) and k in ts]
    if not in_indexer and not in_ts:
        return False
    moved = False
    if 'movie_categories' in in_indexer:
        v = old.pop('movie_categories')
        if not isinstance(ts, dict):
            ts = data['tracker_search'] = {}
        if 'movie_categories' not in ts:
            ts['movie_categories'] = v
            moved = True
    for k in in_indexer:
        old.pop(k, None)
    for k in in_ts:
        ts.pop(k, None)
    removed = [f'indexer.{k}' for k in in_indexer if k != 'movie_categories'] + [f'tracker_search.{k}' for k in in_ts]
    if 'movie_categories' in in_indexer and not moved:
        removed.insert(0, 'indexer.movie_categories (tracker_search already set)')
    _log_migration('Migrated tracker search settings: %s; removed %s',
                   'indexer.movie_categories -> tracker_search.movie_categories' if moved else 'nothing moved',
                   ', '.join(removed) if removed else 'nothing')
    return True



# Prod Caddy-on-host trusts loopback; Docker publishes the app on a bridge
# (default 172.16/12, this host also uses 192.168.32.0/20). Empty trusted_proxies
# left Flask scheme=http behind HTTPS Caddy → CSRF Origin mismatch on POST
# /api/push/subscribe (and every other cookie-auth state-changing API).
_DEFAULT_TRUSTED_PROXIES = '127.0.0.1/32, ::1/128, 172.16.0.0/12, 192.168.32.0/20'


def _migrate_seed_trusted_proxies(data: dict, existing: bool = False) -> bool:
    """Seed empty security.trusted_proxies so HTTPS reverse proxies work.

    Runs once (marker under _migrations). Never overwrites a non-empty value
    (prod already sets loopback; Ops may have set a workaround). Fresh and
    upgraded installs with an empty string get the Caddy/Docker default.
    """
    done = data.setdefault('_migrations', {})
    if done.get('seed_trusted_proxies_v282'):
        return False
    security = data.setdefault('security', {})
    if not isinstance(security, dict):
        done['seed_trusted_proxies_v282'] = True
        return True
    current = security.get('trusted_proxies')
    if current is None or (isinstance(current, str) and not str(current).strip()):
        security['trusted_proxies'] = _DEFAULT_TRUSTED_PROXIES
        done['seed_trusted_proxies_v282'] = True
        logger.info("Seeded security.trusted_proxies for Caddy/Docker reverse proxy")
        return True
    done['seed_trusted_proxies_v282'] = True
    return True


def _run_all_migrations(data: dict, data_dir=None, existing: bool = False) -> bool:
    """Run all migrations on *data* (mutated in place).
    *existing* is True when *data* came from a settings file on disk (an
    upgrade), False for a first-run bootstrap.
    Returns True if any migration occurred.
    """
    changed = False
    changed |= _migrate_harmony_direct(data)
    changed |= _migrate_remove_trackers(data)
    changed |= _migrate_devices(data)
    changed |= _migrate_rooms_shape(data)
    changed |= _migrate_room_ids(data)
    changed |= _migrate_media_paths(data)
    changed |= _migrate_media_paths_v2(data)
    changed |= _migrate_media_types_from_paths(data)
    changed |= _migrate_plex_services_to_kodi(data)
    changed |= _migrate_remove_features(data)
    changed |= _migrate_remove_twilio(data)
    changed |= _migrate_remove_fanart(data)
    changed |= _migrate_hero_schema(data)
    changed |= _migrate_remove_dead_plex_fields(data)
    changed |= _migrate_automation_features(data)
    changed |= _migrate_genre_roulette_block(data)
    changed |= _migrate_legacy_request_excludes(data, existing)
    changed |= _migrate_legacy_qbit_category(data, existing)
    changed |= _migrate_rename_requests_to_portal(data)
    changed |= _migrate_strip_plaintext_request_password(data)
    changed |= _migrate_remove_remote_lock(data)
    changed |= _migrate_access_mode_to_auth_required(data)
    changed |= _migrate_seed_hostnames(data, data_dir)
    changed |= _migrate_seed_trusted_proxies(data, existing)
    changed |= _migrate_tracker_search_settings(data)
    return changed


# ---------------------------------------------------------------------------
# SettingsManager
# ---------------------------------------------------------------------------

def _minimal_tmdb_locked(data: dict) -> bool:
    from app import minimal
    return minimal.enabled() and _get_nested(data, 'apis.tmdb.connector_saved', False) is not True


# Minimal builds: values that count only once saved in Settings > Connectors.
_MINIMAL_CONNECTOR_GATED = {
    'apis.tmdb.api_key': ('apis.tmdb.connector_saved', ''),
    'apis.omdb.api_key': ('apis.omdb.connector_saved', ''),
    'apis.trakt.client_id': ('apis.trakt.connector_saved', ''),
    'apis.trakt.client_secret': ('apis.trakt.connector_saved', ''),
    'apis.trakt.enabled': ('apis.trakt.connector_saved', False),
}


def _minimal_gated(data: dict, dotted_path: str):
    """(True, blank) when *dotted_path* is a connector value a minimal build
    must ignore because the owner hasn't saved it in Connectors."""
    gate = _MINIMAL_CONNECTOR_GATED.get(dotted_path)
    if not gate:
        return False, None
    from app import minimal
    if minimal.enabled() and _get_nested(data, gate[0], False) is not True:
        return True, gate[1]
    return False, None


def settings_file_path() -> Path:
    """settings.json where SettingsManager finds it (_infer_data_dir), never creating a directory."""
    from app.paths import data_dir
    d = data_dir()
    if d.is_dir():
        return d / "settings.json"
    db_path = os.environ.get("DATABASE_PATH", "")
    if db_path and Path(db_path).parent.is_dir():
        return Path(db_path).parent / "settings.json"
    return Path(__file__).resolve().parent.parent / "data" / "settings.json"


class SettingsFile:
    """v259 (Tester LOW): settings.json read exactly as it is on disk, for readers that only look up a few
    keys (Genre roulette's and Anticipated's one-time prod import, What you're missing's connector keys).
    Unlike SettingsManager() it never bootstraps, migrates, merges defaults or writes, so a sparse
    settings.json is never rewritten by a read. Missing or unreadable: empty. get() applies the minimal
    build's connector gate as SettingsManager.get does."""

    def __init__(self, settings_path: Optional[str] = None):
        p = Path(settings_path) if settings_path else settings_file_path()
        try:
            with open(p, "r") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self._settings: Dict[str, Any] = data if isinstance(data, dict) else {}

    def get(self, dotted_path: str, default: Any = None) -> Any:
        locked, blank = _minimal_gated(self._settings, dotted_path)
        if locked:
            return blank
        return _get_nested(self._settings, dotted_path, default)


class SettingsVeto(Exception):
    """SettingsManager.update(veto=...) refused the write; ``answer`` is what
    the veto returned (for a route, its error response)."""

    def __init__(self, answer):
        super().__init__('settings update vetoed')
        self.answer = answer


class SettingsManager:
    """Thread-safe, file-backed settings store with schema validation.

    Usage:
        sm = SettingsManager()        # auto-loads or bootstraps
        val = sm.get("plex.url")      # dotted-path access
        sm.update({"plex": {"url": "http://new:32400"}})  # partial merge
    """

    def __init__(self, settings_path: Optional[str] = None):
        self._lock = threading.Lock()
        if settings_path:
            self._path = Path(settings_path)
        else:
            self._path = _infer_data_dir() / "settings.json"

        self._settings: Dict[str, Any] = {}
        self._load_or_bootstrap()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, dotted_path: str, default: Any = None) -> Any:
        """Return a single settings value by dotted path (e.g. ``plex.url``).

        Minimal builds: the TMDB key and Trakt client ID count only once the
        owner saved them in Settings > Connectors (never env or bootstrapped)."""
        with self._lock:
            locked, blank = _minimal_gated(self._settings, dotted_path)
            if locked:
                return blank
            return _get_nested(self._settings, dotted_path, default)

    def get_all(self) -> Dict[str, Any]:
        """Return a deep copy of the full settings dict."""
        with self._lock:
            return copy.deepcopy(self._settings)

    def get_all_masked(self) -> Dict[str, Any]:
        """Return settings with sensitive values replaced by SECRET_MASK."""
        with self._lock:
            return _mask_secrets(self._settings, SETTINGS_SCHEMA)

    def get_section(self, section: str) -> Dict[str, Any]:
        """Return a deep copy of a top-level section."""
        with self._lock:
            return copy.deepcopy(self._settings.get(section, {}))

    def update(self, partial: Dict[str, Any], veto=None) -> Tuple[bool, List[str]]:
        """Deep-merge *partial* into the current settings.

        Masked sentinel values (``"********"``) are silently stripped so they
        do not overwrite real secrets.

        For array fields (rooms, entities, paths), if the incoming
        partial has masked items, the existing secret values are preserved
        by merging per-item based on index.

        Returns ``(success, errors)`` where *errors* is a list of validation
        error strings (empty on success).

        *veto*, when given, is called as ``veto(current, candidate)`` under the
        same lock as the write, with the settings as they are and as they
        would be. A non-None answer aborts the write with
        ``SettingsVeto(answer)``. Two concurrent saves can't slip a change past
        a check made against settings the other one is about to replace.
        The veto must not call back into this SettingsManager (the lock isn't
        reentrant).
        """
        # v175 (Tester): an unknown top-level key (a typo, or a dotted path like a literal
        # 'tracker_search.movie_categories') is refused, never stored next to the real settings.
        if not isinstance(partial, dict):
            return False, ['Settings must be an object']
        unknown = [k for k in partial if not isinstance(k, str) or k not in SETTINGS_SCHEMA]
        if unknown:
            return False, [f'Unknown setting: {str(k)[:60]}' for k in unknown[:10]]
        cleaned = _strip_mask_values(partial, SETTINGS_SCHEMA)
        if not cleaned:
            return True, []

        with self._lock:
            # Special handling for array fields with sensitive items:
            # merge per-item secrets from current settings into cleaned data
            self._restore_array_secrets(cleaned)

            candidate = _deep_merge(self._settings, cleaned)
            _coerce_types(candidate, SETTINGS_SCHEMA)
            errors = _validate_settings(candidate, SETTINGS_SCHEMA)
            if errors:
                return False, errors
            if veto is not None:
                answer = veto(self._settings, candidate)
                if answer is not None:
                    raise SettingsVeto(answer)
            self._settings = candidate
            self._save()
        return True, []

    def get_defaults(self) -> Dict[str, Any]:
        """Return the complete default settings object from the schema."""
        return _build_defaults(SETTINGS_SCHEMA)

    def get_schema(self) -> Dict[str, Any]:
        """Return a JSON-Schema representation of the settings schema."""
        schema = _schema_to_json_schema(SETTINGS_SCHEMA)
        schema["$schema"] = "http://json-schema.org/draft-07/schema#"
        return schema

    def reset_to_defaults(self) -> None:
        """Reset all settings to schema defaults and save."""
        with self._lock:
            self._settings = _build_defaults(SETTINGS_SCHEMA)
            self._save()

    @property
    def path(self) -> Path:
        """Return the path to the settings file."""
        return self._path

    # ------------------------------------------------------------------
    # Convenience helpers for list-based data
    # ------------------------------------------------------------------

    def get_media_path(self, path_type: str, default: str = "") -> str:
        """Return the filesystem path for a media path type (e.g. 'movies', 'tv', 'recent')."""
        paths = self.get("media.paths") or []
        for p in paths:
            if isinstance(p, dict) and p.get("type") == path_type:
                return p.get("path", default)
        return default

    def get_media_types(self) -> List[Dict[str, Any]]:
        """Return a copy of all media type definitions."""
        return copy.deepcopy(self.get("media.types") or [])

    def get_media_type(self, type_id: str) -> Optional[Dict[str, Any]]:
        """Return the media type definition matching *type_id*, or ``None``."""
        types = self.get("media.types") or []
        for t in types:
            if isinstance(t, dict) and t.get("id") == type_id:
                return copy.deepcopy(t)
        return None

    def get_media_path_by_section_id(self, section_id: int) -> Optional[Dict[str, Any]]:
        """Return the media path entry whose ``plex_section_id`` matches *section_id*."""
        paths = self.get("media.paths") or []
        for p in paths:
            if isinstance(p, dict) and p.get("plex_section_id") == section_id:
                return copy.deepcopy(p)
        return None

    def get_room(self, index: int = 0) -> Optional[Dict[str, Any]]:
        """Return a room by index (default first room)."""
        rooms = self.get("devices.rooms") or []
        if 0 <= index < len(rooms):
            return copy.deepcopy(rooms[index])
        return None

    def get_room_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the first room whose name matches (case-insensitive)."""
        rooms = self.get("devices.rooms") or []
        for r in rooms:
            if isinstance(r, dict) and r.get("name", "").lower() == name.lower():
                return copy.deepcopy(r)
        return None

    def get_default_device_ip(self) -> str:
        """Return the IP of the first room (replaces devices.shield.ip)."""
        room = self.get_room(0)
        if not room:
            return ""
        host = room.get("host") or room.get("ip")
        if not host:
            for dev in room.get("devices") or []:
                if isinstance(dev, dict) and dev.get("host"):
                    host = dev["host"]
                    break
        return host or ""

    def get_default_adb_port(self) -> int:
        """Return the ADB port of the first room (replaces devices.shield.adb_port)."""
        room = self.get_room(0)
        if room:
            return room.get("adb_port", 5555)
        return 5555

    def get_room_by_id(self, room_id: str) -> Optional[Dict[str, Any]]:
        """Return the room whose ``id`` matches *room_id*, or ``None``."""
        rooms = self.get("devices.rooms") or []
        for r in rooms:
            if isinstance(r, dict) and r.get("id") == room_id:
                return copy.deepcopy(r)
        return None

    def add_room(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Append a new room to ``devices.rooms``, generating a stable ID.

        Returns the room dict (with ``id`` set).
        """
        import uuid
        data["id"] = uuid.uuid4().hex[:8]
        with self._lock:
            rooms = self._settings.setdefault("devices", {}).setdefault("rooms", [])
            rooms.append(data)
            self._save()
        return copy.deepcopy(data)

    def update_room(self, room_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find the room with *room_id*, merge *updates* into it, and save.

        Returns the updated room dict, or ``None`` if not found.
        """
        with self._lock:
            rooms = self._settings.get("devices", {}).get("rooms", [])
            for r in rooms:
                if isinstance(r, dict) and r.get("id") == room_id:
                    r.update(updates)
                    r["id"] = room_id  # prevent id overwrite
                    self._save()
                    return copy.deepcopy(r)
        return None

    def delete_room(self, room_id: str) -> bool:
        """Remove the room with *room_id* from ``devices.rooms``.

        Returns ``True`` if found and removed, ``False`` otherwise.
        """
        with self._lock:
            rooms = self._settings.get("devices", {}).get("rooms", [])
            for i, r in enumerate(rooms):
                if isinstance(r, dict) and r.get("id") == room_id:
                    rooms.pop(i)
                    # Fix defaultRoomId if it pointed to the deleted room
                    if self._settings.get("devices", {}).get("defaultRoomId") == room_id:
                        self._settings["devices"]["defaultRoomId"] = ""
                    # Fix defaultPlaybackTarget if it pointed to this room
                    dpt = self._settings.get("devices", {}).get("defaultPlaybackTarget", {})
                    if isinstance(dpt, dict) and dpt.get("roomId") == room_id:
                        self._settings["devices"]["defaultPlaybackTarget"] = {}
                    self._save()
                    return True
        return False

    # ------------------------------------------------------------------
    # Device CRUD (nested inside a room)
    # ------------------------------------------------------------------

    def _find_room(self, room_id: str):
        """Return the live room dict (not a copy) inside self._settings, or None."""
        rooms = self._settings.get("devices", {}).get("rooms", [])
        for r in rooms:
            if isinstance(r, dict) and r.get("id") == room_id:
                return r
        return None

    def add_device(self, room_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Add a device to a room. Returns the device dict with generated ID, or None if room not found."""
        import uuid
        data["id"] = uuid.uuid4().hex[:8]
        data.setdefault("services", [])
        with self._lock:
            room = self._find_room(room_id)
            if not room:
                return None
            room.setdefault("devices", []).append(data)
            self._save()
        return copy.deepcopy(data)

    def get_device(self, room_id: str, device_id: str) -> Optional[Dict[str, Any]]:
        """Return a device by room+device ID, or None."""
        room = self.get_room_by_id(room_id)
        if not room:
            return None
        for d in room.get("devices", []):
            if isinstance(d, dict) and d.get("id") == device_id:
                return d  # already a deepcopy from get_room_by_id
        return None

    def update_device(self, room_id: str, device_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a device inside a room. Returns updated device or None."""
        with self._lock:
            room = self._find_room(room_id)
            if not room:
                return None
            for d in room.get("devices", []):
                if isinstance(d, dict) and d.get("id") == device_id:
                    # Preserve services array — don't let updates overwrite it
                    updates.pop("services", None)
                    d.update(updates)
                    d["id"] = device_id
                    self._save()
                    return copy.deepcopy(d)
        return None

    def delete_device(self, room_id: str, device_id: str) -> bool:
        """Remove a device from a room. Returns True if found and removed."""
        with self._lock:
            room = self._find_room(room_id)
            if not room:
                return False
            devices = room.get("devices", [])
            for i, d in enumerate(devices):
                if isinstance(d, dict) and d.get("id") == device_id:
                    devices.pop(i)
                    self._save()
                    return True
        return False

    # ------------------------------------------------------------------
    # Service CRUD (nested inside a device inside a room)
    # ------------------------------------------------------------------

    def _find_device(self, room_id: str, device_id: str):
        """Return the live device dict inside self._settings, or None."""
        room = self._find_room(room_id)
        if not room:
            return None
        for d in room.get("devices", []):
            if isinstance(d, dict) and d.get("id") == device_id:
                return d
        return None

    def add_service(self, room_id: str, device_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Add a service to a device. Returns the service dict with generated ID."""
        import uuid
        data["id"] = uuid.uuid4().hex[:8]
        with self._lock:
            device = self._find_device(room_id, device_id)
            if not device:
                return None
            device.setdefault("services", []).append(data)
            self._save()
        return copy.deepcopy(data)

    def update_service(self, room_id: str, device_id: str, service_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a service. Returns updated service or None."""
        with self._lock:
            device = self._find_device(room_id, device_id)
            if not device:
                return None
            for s in device.get("services", []):
                if isinstance(s, dict) and s.get("id") == service_id:
                    s.update(updates)
                    s["id"] = service_id
                    self._save()
                    return copy.deepcopy(s)
        return None

    def delete_service(self, room_id: str, device_id: str, service_id: str) -> bool:
        """Remove a service from a device. Returns True if found and removed."""
        with self._lock:
            device = self._find_device(room_id, device_id)
            if not device:
                return False
            services = device.get("services", [])
            for i, s in enumerate(services):
                if isinstance(s, dict) and s.get("id") == service_id:
                    services.pop(i)
                    self._save()
                    return True
        return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _restore_array_secrets(self, cleaned: dict) -> None:
        """For array fields that have sensitive item properties, restore
        real secret values from current settings where the cleaned data
        has the field missing (stripped because it was SECRET_MASK).

        Must be called while holding self._lock.
        """
        # Define which array fields have sensitive item keys
        array_sensitive_map = {
        }
        for array_key, sensitive_keys in array_sensitive_map.items():
            if array_key not in cleaned:
                continue
            new_items = cleaned[array_key]
            old_items = self._settings.get(array_key, [])
            if not isinstance(new_items, list) or not isinstance(old_items, list):
                continue
            for i, new_item in enumerate(new_items):
                if not isinstance(new_item, dict):
                    continue
                # Try to find the matching old item by index or by name+type
                old_item = None
                if i < len(old_items) and isinstance(old_items[i], dict):
                    old_item = old_items[i]
                else:
                    # Fallback: match by name+type
                    for oi in old_items:
                        if (isinstance(oi, dict) and
                                oi.get("name") == new_item.get("name") and
                                oi.get("type") == new_item.get("type")):
                            old_item = oi
                            break
                if old_item is None:
                    continue
                for sk in sensitive_keys:
                    if sk not in new_item and sk in old_item:
                        new_item[sk] = old_item[sk]

    def _load_or_bootstrap(self) -> None:
        """Load settings from disk, or bootstrap from env vars + defaults."""
        if self._path.exists():
            logger.info(f"Loading settings from {self._path}")
            self._load()
        else:
            logger.info(f"No settings file found at {self._path} -- bootstrapping")
            self._bootstrap()

    def _load(self) -> None:
        """Read settings.json and merge with defaults to fill any missing keys.
        Then run migrations to convert Phase 1 data to Phase 2.5 format.
        """
        try:
            with open(self._path, "r") as f:
                on_disk = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(f"Failed to read {self._path}: {exc} -- falling back to defaults")
            on_disk = {}

        # Safety: if the read failed (on_disk is empty) but the file exists
        # on disk, do NOT overwrite it with defaults — the file may contain
        # valid data that we couldn't read due to a transient error.
        read_failed = not on_disk and self._path.exists() and self._path.stat().st_size > 2

        # Run migrations BEFORE merging with defaults so old fields are
        # converted into new list fields that will survive the merge.
        migrated = _run_all_migrations(on_disk, self._path.parent, existing=bool(on_disk))

        defaults = _build_defaults(SETTINGS_SCHEMA)
        with self._lock:
            self._settings = _deep_merge(defaults, on_disk)
            if read_failed:
                logger.warning("Settings read failed but file exists — NOT overwriting with defaults")
            elif migrated:
                # Only save when migrations actually changed data — avoids
                # race conditions when multiple SettingsManager instances
                # load/save concurrently (e.g. auto_rename, device_wake).
                self._backup_before_migration()
                self._save()
                logger.info("Phase 2.5 migrations applied and saved")

    def _bootstrap(self) -> None:
        """First-run bootstrap: env vars -> Config class -> schema defaults."""
        defaults = _build_defaults(SETTINGS_SCHEMA)

        # Layer 1: schema defaults (already in *defaults*)

        # Layer 2: read from the existing Config class in app/config.py
        config_overrides = self._read_config_class()
        if config_overrides:
            defaults = _deep_merge(defaults, config_overrides)

        # Layer 3: environment variables (highest priority)
        env_overrides = self._read_env_vars()
        if env_overrides:
            defaults = _deep_merge(defaults, env_overrides)

        # Layer 4: bootstrap migration env vars for old-format fields
        self._apply_env_migration(defaults)

        # Run migrations on the final result
        _run_all_migrations(defaults)

        with self._lock:
            self._settings = defaults
            self._save()
        logger.info(f"Bootstrapped settings to {self._path}")

    def _read_env_vars(self) -> Dict[str, Any]:
        """Read all known env vars and return a partial settings dict."""
        partial: Dict[str, Any] = {}
        for env_name, dotted_path in ENV_VAR_MAP.items():
            raw = os.environ.get(env_name)
            if raw is None or raw == "":
                continue
            schema_node = _get_schema_node(SETTINGS_SCHEMA, dotted_path)
            if schema_node is None:
                continue
            value = _coerce_env_value(raw, schema_node)
            _set_nested(partial, dotted_path, value)
        return partial

    def _apply_env_migration(self, data: dict) -> None:
        """Read env vars that map to old-format fields and inject them into
        data so the migration layer can convert them.
        """
        old_format: Dict[str, Any] = {}

        # Shield IP -> old devices.shield.ip
        shield_ip = os.environ.get("SHIELD_PLEX_IP")
        if shield_ip:
            old_format.setdefault("devices", {}).setdefault("shield", {})["ip"] = shield_ip

        # Old media path env vars -> old media.{field}
        media_env = {
            "MOVIES_ROOT":    "movies_root",
            "RECENT_ROOT":    "recent_root",
            "TV_ROOT":        "tv_root",
            "TV_4K_ROOT":     "tv_4k_root",
            "TV_REMUX_ROOT":  "tv_remux_root",
            "FULLRIPS_ROOT":  "fullrips_root",
            "FOURKAY_ROOT":   "fourkay_root",
        }
        for env_name, field in media_env.items():
            raw = os.environ.get(env_name)
            if raw:
                old_format.setdefault("media", {})[field] = raw

        if old_format:
            for k, v in old_format.items():
                if k in data and isinstance(data[k], dict) and isinstance(v, dict):
                    data[k] = _deep_merge(data[k], v)
                else:
                    data[k] = v

    def _read_config_class(self) -> Dict[str, Any]:
        """Try to import the existing Config class from app.config and extract
        values that differ from empty-string defaults.

        This is a best-effort fallback for installs that have a populated Config
        class but no env vars set.
        """
        partial: Dict[str, Any] = {}
        try:
            from app.config import Config as LegacyConfig
        except ImportError:
            return partial

        # Map Config attributes to new settings paths (those still valid)
        config_map = {
            "PLEX_URL":            "plex.url",
            "PLEX_TOKEN":          "plex.token",
            "PLEX_ACCOUNT_ID":     "plex.account_id",
            "TMDB_API_KEY":        "apis.tmdb.api_key",
            "QBITTORRENT_URL":     "qbittorrent.url",
            "QBITTORRENT_USER":    "qbittorrent.username",
            "QBITTORRENT_PASS":    "qbittorrent.password",
            "SONARR_URL":          "sonarr.url",
            "SONARR_API_KEY":      "sonarr.api_key",
            "RADARR_URL":          "radarr.url",
            "RADARR_API_KEY":      "radarr.api_key",
            # media.downloads_root is not seeded from Config.DOWNLOADS_ROOT (/downloads):
            # empty settings fall back to LUMA_DOWNLOADS_ROOT (v320). Explicit DOWNLOADS_ROOT
            # env still migrates via _ENV_VAR_MIGRATION_MAP.
            "DATABASE_PATH":       "media.database_path",
            "RADARR_QBIT_CATEGORY": "qbittorrent.radarr_category",
            "RADARR_MONITORING_ENABLED": "radarr.monitoring_enabled",
        }

        for attr, dotted_path in config_map.items():
            value = getattr(LegacyConfig, attr, None)
            if value is None or value == "":
                continue
            schema_node = _get_schema_node(SETTINGS_SCHEMA, dotted_path)
            if schema_node is None:
                continue
            if schema_node["type"] == "integer" and not isinstance(value, int):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    continue
            elif schema_node["type"] == "boolean" and isinstance(value, str):
                value = value.lower() in ("true", "1", "yes")
            _set_nested(partial, dotted_path, value)

        # Old media path fields
        old_media_map = {
            "MOVIES_ROOT":    "movies_root",
            "RECENT_ROOT":    "recent_root",
            "TV_ROOT":        "tv_root",
            "TV_4K_ROOT":     "tv_4k_root",
            "TV_REMUX_ROOT":  "tv_remux_root",
            "FULLRIPS_ROOT":  "fullrips_root",
            "FOURKAY_ROOT":   "fourkay_root",
        }
        for attr, field in old_media_map.items():
            value = getattr(LegacyConfig, attr, None)
            if value and value != "":
                partial.setdefault("media", {})[field] = value

        return partial

    MIGRATION_BACKUPS = 5

    def _backup_before_migration(self) -> Optional[Path]:
        """v174 (Tester FF-8e): copy settings.json as it was on disk to settings.json.bak-<UTC time>
        (0600, newest MIGRATION_BACKUPS kept) before a migration rewrites it, so a rollback can
        restore the old shape. Never fatal. Must be called while holding ``self._lock``."""
        try:
            if not self._path.is_file() or self._path.stat().st_size == 0:
                return None
            stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S-%f')
            dst = self._path.with_name(f'{self._path.name}.bak-{stamp}')
            fd = os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as out, open(self._path, 'rb') as src:
                out.write(src.read())
                out.flush()
                os.fsync(out.fileno())
            olds = sorted(self._path.parent.glob(f'{self._path.name}.bak-*'))
            for old in olds[:-self.MIGRATION_BACKUPS]:
                try:
                    old.unlink()
                except OSError:
                    pass
            _log_migration('settings.json backed up to %s before a migration', dst.name)
            return dst
        except Exception as e:                       # noqa: BLE001 - a backup never blocks the upgrade
            logger.warning('settings.json not backed up before a migration (%s)', type(e).__name__)
            return None

    def _save(self) -> None:
        """Write current settings to disk with restricted permissions.

        Must be called while holding ``self._lock``.
        """
        try:
            # Ensure parent directory exists
            self._path.parent.mkdir(parents=True, exist_ok=True)

            # Write to a temp file then rename for atomicity
            tmp_path = self._path.with_suffix(".tmp")
            try:
                with open(tmp_path, "w") as f:
                    json.dump(self._settings, f, indent=2, sort_keys=True)
                    f.write("\n")

                # Set permissions to 0600 before moving into place
                try:
                    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass

                # Atomic rename
                tmp_path.replace(self._path)
            except OSError:
                # Atomic rename can fail on cross-filesystem Docker volumes;
                # fall back to direct write (still safe under our lock).
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                with open(self._path, "w") as f:
                    json.dump(self._settings, f, indent=2, sort_keys=True)
                    f.write("\n")
                try:
                    os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass

        except OSError as exc:
            logger.error(f"Failed to write settings to {self._path}: {exc}")
