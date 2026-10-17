#!/usr/bin/env python3
"""
Media Search Application - Clean and focused media search interface
"""

import os
import re
import json
import io
import secrets
import sqlite3
import logging
import urllib.parse
import subprocess
import random
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, request, jsonify, send_from_directory, send_file, redirect, g, make_response, Response
from dotenv import load_dotenv
import requests

# First-run bootstrap: install secrets in the data dir, and settings saved in
# the UI exported to the environment before modules that read it are imported.
from app import first_run
from app import access as _access
from app.paths import telegram_bot_config_path
first_run.export_settings_to_env()
_INSTALL_SECRETS = first_run.load_or_create_secrets()

# Import explore cache
if __name__ == '__main__':
    # Fail fast (exit 1) before the cache modules below touch the disk, if
    # LUMA_DATA_DIR / LUMA_CACHE_DIR / HERO_CACHE_DIR or a database in them is
    # not writable (e.g. a populated read-only mount).
    # Under gunicorn the same check runs from gunicorn.conf.py on_starting.
    from app.paths import ensure_writable_or_exit
    ensure_writable_or_exit()
from app.explore_cache import explore_cache
from app.fast_search import fast_search_index
from app import search_correct


from app.upgrade_finder import UpgradeFinder, BATCH_SIZE

# Import library cache
from app.library_cache import library_cache
from app.cache_updater import initialize_caches, update_continue_watching_cache, update_library_browse_cache, update_recent_items_cache, update_recent_tv_episodes_cache, update_library_stats_cache

# Import RT scraper
from app.rt_scraper import rt_scraper

# Import hero cache
from app.hero_cache import hero_cache

# Import Plex service
from app.plex_service import plex_service
from app.imdb_ratings import enrich_imdb_ratings

# Import screensaver service
from app.screensaver_service import get_screensaver_service

# Import settings manager
from app.settings_manager import SettingsManager, SECRET_MASK
import app.user_auth as user_auth

# Import indexer API
from app.indexer_api import get_indexer_client

# Import tunnel service
from app.tunnel_service import (
    start_tunnel, stop_tunnel,
    get_status as get_tunnel_status,
    auto_start_if_enabled as tunnel_auto_start,
    get_cloudflared_version,
)

# Import tracker dedup helper (legacy PHD/BHD direct APIs removed — Prowlarr sweep covers all indexers)
from app.torrent_api import combine_and_dedupe_movies

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


_SECRET_QS = re.compile(r'((?:api_?key|access_token|token|apikey|passkey)=)[^&\s\'"]+', re.I)


def _redact_secrets(value) -> str:
    """Text for a log line with credential query params masked (requests puts
    the full URL, api_key included, in HTTPError messages)."""
    return _SECRET_QS.sub(r'\1***', str(value))


class _RedactSecretsFilter(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = _redact_secrets(msg)
        if red != msg:
            record.msg, record.args = red, ()
        return True


for _h in logging.getLogger().handlers:
    _h.addFilter(_RedactSecretsFilter())

# Flask app setup
static_folder = os.environ.get('FLASK_STATIC_FOLDER') or '/app/static'
# static_url_path is pinned: Flask otherwise derives it from the folder's
# basename, so FLASK_STATIC_FOLDER=/x/assets would move /static/* to /assets/*.
app = Flask(__name__, static_folder=static_folder, static_url_path='/static')


def _resolve_spa_dir(static_dir: str, image_spa_dir: str = '/app/spa') -> str:
    """Directory holding the built React SPA (index.html, assets/, fonts/, weather/).

    LUMA_SPA_DIR wins; else the image path /app/spa (outside ./static, so a
    bind-mounted ./static cannot hide it); else <static>/spa for dev builds.
    """
    explicit = os.environ.get('LUMA_SPA_DIR')
    if explicit:
        return explicit
    if os.path.isfile(os.path.join(image_spa_dir, 'index.html')):
        return image_spa_dir
    return os.path.join(static_dir, 'spa')


app.config['SPA_DIR'] = _resolve_spa_dir(static_folder)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or _INSTALL_SECRETS['flask_secret_key']
# HERO_CACHE_DIR is resolved once in HeroCache.__init__
app.config['HERO_CACHE_DIR'] = str(hero_cache.cache_dir)

# Initialize settings (needed for URL base resolution)
from app.settings_manager import SettingsManager
settings = SettingsManager()

# Resolve URL base once at startup and store in app.config
from app.server_config import get_url_base
_URL_BASE = get_url_base(settings)
app.config['URL_BASE'] = _URL_BASE

# Scheme and host come from X-Forwarded-Proto / X-Forwarded-Host only when the
# direct peer is a trusted proxy (read per request, so settings changes apply).
# REMOTE_ADDR is left alone; access.client_ip() resolves the client address.
app.wsgi_app = _access.TrustedProxyMiddleware(app.wsgi_app, lambda: settings)

# Background threads (caches, search index, automations) start from
# start_services(); tests and tools can turn them off with
# START_BACKGROUND_THREADS=False (read when start_services runs).
app.config['START_BACKGROUND_THREADS'] = not (
    os.environ.get('LUMA_SKIP_BACKGROUND_THREADS', '').strip().lower() in ('1', 'true', 'yes', 'on')
    or bool(settings.get('general.skip_background_threads'))
)

if _URL_BASE:
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    
    # Proper WSGI 404 app (not a lambda)
    def _404_app(environ, start_response):
        """WSGI app that returns 404 for requests outside the URL base."""
        status = '404 Not Found'
        headers = [('Content-Type', 'text/plain; charset=utf-8')]
        start_response(status, headers)
        return [b'Not Found']
    
    # Wrap the app so all routes are prefixed with URL base
    app.wsgi_app = DispatcherMiddleware(_404_app, {_URL_BASE: app.wsgi_app})

# Lazy import after constants are defined
def _register_bot_api():
    """Register bot API routes after AUTH_COOKIE and _INSTALL_SECRETS are available."""
    try:
        from app.bot_api import register_bot_routes
        register_bot_routes(app, AUTH_COOKIE, _INSTALL_SECRETS)
    except Exception as _bot_api_err:
        logger.error("Failed to register bot API routes: %s", _bot_api_err)

# ---------------------------------------------------------------------------
# Cache-Control headers for static files
# ---------------------------------------------------------------------------
# Flask's built-in static handler serves /static/ but sets no Cache-Control.
# Android WebView (Fully Kiosk) heuristic-caches files for days/weeks.
# This after_request hook ensures proper headers on ALL responses:
#   /static/images/*  → 5-min cache + revalidate (icons change without hash)
#   /static/*         → 5-min cache + revalidate (unhashed files)
#   /assets/*         → handled by spa_assets() route (immutable, 1yr)

# ---------------------------------------------------------------------------
# Auth middleware — read session cookie on every request
# ---------------------------------------------------------------------------

# The only session cookie Luma issues or accepts. The pre-2026 "luma_session"
# cookie is no longer read; any copy a browser still sends is expired by
# _expire_legacy_session_cookie (the one place that deletes it).
from app.auth_routes import AUTH_COOKIE, LEGACY_SESSION_COOKIE
from app import lan_hosts as _lan_hosts
# lan_hosts.json lives next to settings.json
from app.settings_manager import _infer_data_dir  # resolves via app.paths
_LAN_HOSTS_DIR = _infer_data_dir()
# Exact paths reachable without signing in. Request-portal API paths come from
# portal_guard's explicit allowlist (see _is_auth_exempt).
_AUTH_EXEMPT = {
    "/health", "/login", "/logout", "/request", "/request/",
    "/api/auth/login", "/api/auth/me", "/api/auth/register", "/api/auth/unlock",
    "/manifest.json", "/sw.js", "/ca.crt",
}
# Static files a signed-out browser needs: the sign-in page, PWA shell and
# request portal. Everything else under /static/ requires access.
_AUTH_EXEMPT_STATIC_PREFIXES = ("/static/request/", "/static/images/")
_AUTH_EXEMPT_STATIC = {"/static/manifest.json", "/static/sw.js"}

# Dev/test switch: when set, no automations, auto-grabs or scheduled jobs are
# started (read-only caches still run). See docker-compose.dev.yml.
_AUTOMATIONS_DISABLED = os.environ.get('LUMA_DISABLE_AUTOMATIONS', '').strip().lower() in ('1', 'true', 'yes', 'on')

# Service-to-service API key (used by companion apps)
_SERVICE_API_KEY = os.environ.get("SERVICE_API_KEY") or _INSTALL_SECRETS["service_api_key"]

# Register bot API now that constants are available
_register_bot_api()

from app import minimal as _minimal  # noqa: E402


def _note_lan_host():
    """Remember the host name of a request that just got implicit owner access
    (so: a LAN client on a known host, never remote, proxied-public or portal)."""
    try:
        _lan_hosts.record(request.host, _LAN_HOSTS_DIR)
    except Exception as exc:  # never let bookkeeping break a request
        logger.debug("lan host record failed: %s", exc)


@app.before_request
def minimal_mode_gate():
    """LUMA_MINIMAL=1: every route outside the core answers 404."""
    if _minimal.enabled() and not _minimal.path_allowed(request.path, request.method):
        from flask import abort
        abort(404)


@app.before_request
def load_current_user():
    """Attach the current user to g.current_user (or None if not logged in).
    Also accepts X-API-Key header for service-to-service auth (companion apps).
    """
    # Default g.implicit_user to False for all request types
    g.implicit_user = False
    
    # Check for API key first
    api_key = request.headers.get("X-API-Key")
    if api_key and _SERVICE_API_KEY and secrets.compare_digest(api_key, _SERVICE_API_KEY):
        g.current_user = {
            "id": 0, "name": "Service", "is_admin": 1, "created_at": None,
            "token": None, "expires_at": None,
        }
        return

    # Read session cookie
    token = request.cookies.get(AUTH_COOKIE)
    g.current_user = user_auth.validate_session(token) if token else None
    if g.current_user is None and not _access.should_authenticate(settings, request):
        # Implicit owner for local requests when auth_required=disabled_local
        owner = _access.owner(user_auth)
        if owner:
            g.current_user = {
                "id": owner["id"], "name": owner["name"], "is_admin": owner["is_admin"],
                "created_at": owner.get("created_at"), "token": None, "expires_at": None,
            }
            g.implicit_user = True
            _note_lan_host()


def _get_current_user() -> Optional[Dict]:
    """Return the validated user dict for the current request, or None."""
    return getattr(g, "current_user", None)


def _require_auth():
    """Return an error response if the request is not authenticated.
    Returns None if auth is OK."""
    if not _get_current_user():
        return jsonify({"error": "Authentication required"}), 401
    return None


def _require_admin():
    """Return an error response if the current user is not admin."""
    user = _get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    if not user.get("is_admin"):
        return jsonify({"error": "Admin access required"}), 403
    return None


def get_plex_for_request():
    """Resolve the correct PlexService for the current request.

    Returns the user's personal PlexService if they have one configured,
    otherwise falls back to the global plex_service singleton.
    """
    from app.plex_service_registry import get_user_plex
    user = _get_current_user()
    if user:
        user_settings = user_auth.get_user_settings(user['id']) or {}
        plex_url = user_settings.get('plex_url')
        plex_token = user_settings.get('plex_token')
        if plex_url and plex_token:
            return get_user_plex(user['id'], plex_url, plex_token)
        # If user has shared access or is admin, use global
        if user_settings.get('use_shared_server') or user.get('is_admin'):
            return plex_service
    # Fallback to global
    return plex_service


def get_radarr_for_request():
    """Resolve the correct RadarrAPI for the current request.

    Returns the user's personal RadarrAPI if they have one configured,
    otherwise falls back to constructing one from global settings.
    Returns None if neither user nor global config exists.
    """
    from app.radarr_api import RadarrAPI
    from app.radarr_service_registry import get_user_radarr
    user = _get_current_user()
    if user:
        user_settings = user_auth.get_user_settings(user['id']) or {}
        radarr_url = user_settings.get('radarr_url')
        radarr_api_key = user_settings.get('radarr_api_key')
        if radarr_url and radarr_api_key:
            return get_user_radarr(user['id'], radarr_url, radarr_api_key)
    # Fallback to global settings
    url = settings.get('radarr.url')
    api_key = settings.get('radarr.api_key')
    if url and api_key:
        return RadarrAPI(url, api_key)
    return None


def _resolve_radarr_profile_id(profiles: list) -> int:
    """Quality profile for app-initiated Radarr adds.

    Precedence: configured default (radarr.default_quality_profile_id) →
    radarr.preferred_profile_name → first profile. Never silently lands a movie on 'Any'
    just because no default is set.
    """
    if not profiles:
        return 1
    saved = settings.get('radarr.default_quality_profile_id') or 0
    if saved:
        for p in profiles:
            if p['id'] == saved:
                return saved
    preferred = (settings.get('radarr.preferred_profile_name') or '').strip().lower()
    if preferred:
        for p in profiles:
            if (p.get('name') or '').lower() == preferred:
                return p['id']
    return profiles[0]['id']


def get_sonarr_for_request():
    """Resolve the correct SonarrAPI for the current request.

    Returns the user's personal SonarrAPI if they have one configured,
    otherwise falls back to the global sonarr singleton.
    Returns None if neither user nor global config exists.
    """
    from app.sonarr_api import sonarr as global_sonarr
    from app.sonarr_service_registry import get_user_sonarr
    user = _get_current_user()
    if user:
        user_settings = user_auth.get_user_settings(user['id']) or {}
        sonarr_url = user_settings.get('sonarr_url')
        sonarr_api_key = user_settings.get('sonarr_api_key')
        if sonarr_url and sonarr_api_key:
            return get_user_sonarr(user['id'], sonarr_url, sonarr_api_key)
    # Fallback to global singleton
    if global_sonarr.enabled:
        return global_sonarr
    return None


def _resolve_sonarr_profile_id(profiles: list) -> int:
    """Quality profile for app-initiated Sonarr adds.

    Precedence: configured default (sonarr.default_quality_profile_id) →
    sonarr.preferred_profile_name → first profile. Mirrors the Radarr resolver so TV can't
    silently land on Sonarr's first profile either.
    """
    if not profiles:
        return 1
    saved = settings.get('sonarr.default_quality_profile_id') or 0
    if saved:
        for p in profiles:
            if p['id'] == saved:
                return saved
    preferred = (settings.get('sonarr.preferred_profile_name') or '').strip().lower()
    if preferred:
        for p in profiles:
            if (p.get('name') or '').lower() == preferred:
                return p['id']
    return profiles[0]['id']


def _resolve_users_for_arr_url(arr_url: str) -> list[int]:
    """Find which user(s) have a Radarr or Sonarr URL matching the given URL.

    Returns a list of user IDs.  If a specific user has configured this exact
    Arr URL as their personal instance, only those users are returned.
    Otherwise the URL is the shared/global instance, so every user that has a
    push subscription is notified (a home library is shared) — falling back to
    [0] only when nobody is subscribed.
    """
    def _shared_targets() -> list[int]:
        try:
            from app.push_service import get_subscribed_user_ids
            ids = get_subscribed_user_ids()
            return ids if ids else [0]
        except Exception:
            return [0]

    if not arr_url:
        return _shared_targets()
    from app.user_auth import list_users, get_user_settings
    normalized = arr_url.rstrip('/')
    matching_users: list[int] = []
    for u in list_users():
        us = get_user_settings(u['id']) or {}
        for key in ('radarr_url', 'sonarr_url'):
            val = us.get(key, '')
            if val and val.rstrip('/') == normalized:
                matching_users.append(u['id'])
                break
    return matching_users if matching_users else _shared_targets()


def set_auth_cookie(resp, token, max_age=60 * 60 * 24 * 30):
    """Issue the session cookie: httponly, secure per scheme, scoped to the URL base."""
    resp.set_cookie(AUTH_COOKIE, token, max_age=max_age, httponly=True, samesite='Lax',
                    secure=request.is_secure, path=app.config.get('URL_BASE') or '/')
    return resp


def clear_auth_cookie(resp):
    resp.delete_cookie(AUTH_COOKIE, path=app.config.get('URL_BASE') or '/')
    return resp


@app.after_request
def _expire_legacy_session_cookie(response):
    """Expire the retired luma_session cookie whenever a browser still sends it."""
    if LEGACY_SESSION_COOKIE in request.cookies:
        response.delete_cookie(LEGACY_SESSION_COOKIE, path='/')
        base = app.config.get('URL_BASE') or ''
        if base:
            response.delete_cookie(LEGACY_SESSION_COOKIE, path=base)
    return response


_IMG_PREFIX = '/api/img/'


def _with_base_img(value, base):
    """Prefix root-relative /api/img/ strings in a JSON value with the URL base."""
    if isinstance(value, str):
        return base + value if value.startswith(_IMG_PREFIX) else value
    if isinstance(value, list):
        return [_with_base_img(v, base) for v in value]
    if isinstance(value, dict):
        return {k: _with_base_img(v, base) for k, v in value.items()}
    return value


@app.after_request
def _prefix_image_urls(response):
    """Image proxy URLs are built and cached root-relative ('/api/img/...').
    Under a URL base, prefix them in /api JSON responses at send time, so
    cached data stays base-independent. Already-prefixed URLs don't match."""
    base = request.script_root or app.config.get('URL_BASE') or ''
    if (not base or response.direct_passthrough or response.is_streamed
            or response.mimetype != 'application/json' or not request.path.startswith('/api/')):
        return response
    raw = response.get_data(as_text=True)
    if '"' + _IMG_PREFIX not in raw:
        return response
    try:
        payload = json.loads(raw)
    except ValueError:
        return response
    response.set_data(json.dumps(_with_base_img(payload, base), separators=(',', ':')))
    return response


@app.after_request
def _auth_api_no_store(response):
    """Sign-in, sign-out and session responses are never cached."""
    if request.path.startswith('/api/auth/') or request.path in ('/login', '/logout'):
        response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response


@app.after_request
def set_static_cache_headers(response):
    path = request.path
    if path == '/static/sw.js':
        response.headers['Cache-Control'] = 'no-cache'
    elif path.startswith('/static/'):
        # Don't override if a specific route already set Cache-Control
        # (e.g. sw.js sets max-age=0)
        if 'Cache-Control' not in response.headers or response.headers.get('Cache-Control') == 'no-cache':
            response.headers['Cache-Control'] = 'public, max-age=300, must-revalidate'
    return response


# JSON error handlers for API routes (prevent HTML error pages)
@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found', 'path': request.path}), 404
    return e  # Let non-API 404s fall through to default handler

@app.errorhandler(405)
def handle_405(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Method not allowed', 'path': request.path, 'method': request.method}), 405
    return e

# Configuration

class Config:
    TMDB_API_KEY = os.environ.get('TMDB_API_KEY', '')
    OMDB_API_KEY = os.environ.get('OMDB_API_KEY', '')
    PLEX_URL = os.environ.get('PLEX_URL', '')
    PLEX_TOKEN = os.environ.get('PLEX_TOKEN', '')

    # qBittorrent settings
    QBITTORRENT_URL = os.environ.get('QBITTORRENT_URL', '')
    QBITTORRENT_USER = os.environ.get('QBITTORRENT_USER', '')
    QBITTORRENT_PASS = os.environ.get('QBITTORRENT_PASS', '')

    # Radarr settings
    RADARR_URL = os.environ.get('RADARR_URL', '')
    RADARR_API_KEY = os.environ.get('RADARR_API_KEY', '')


# Minimal builds: no env TMDB key. Only a key saved in Settings > Connectors
# counts (settings.get gates it), so TMDB stays off on a fresh install.
if _minimal.enabled():
    Config.TMDB_API_KEY = settings.get('apis.tmdb.api_key') or ''
    import app.config as _legacy_config
    _legacy_config.Config.TMDB_API_KEY = Config.TMDB_API_KEY
    if Config.TMDB_API_KEY:
        os.environ['TMDB_API_KEY'] = Config.TMDB_API_KEY
    else:
        os.environ.pop('TMDB_API_KEY', None)

config = Config()

# Note: Settings already initialized at startup for URL base resolution
logger.info(f"Settings loaded from {settings.path}")


def _is_reloader_parent() -> bool:
    """True in the Werkzeug reloader's watcher process (FLASK_ENV=development)."""
    return (os.environ.get('FLASK_ENV') == 'development'
            and os.environ.get('WERKZEUG_RUN_MAIN') != 'true')


def start_services(app_instance):
    """Initialize database, caches, and background services.
    
    Called from:
    - gunicorn.conf.py post_worker_init (production)
    - __main__ (development)
    """
    # Check if background threads should be skipped (for testing)
    # Read from app.config at call time, not import time
    skip_bg = not app_instance.config.get('START_BACKGROUND_THREADS', True)
    
    # Initialize database
    init_database()
    
    # Initialize user auth tables
    try:
        user_auth.init_db()
        logger.info("✅ User auth database initialized")
    except Exception as e:
        logger.error(f"❌ User auth DB init failed: {e}")
    
    # First run: generate the Web Push (VAPID) key pair up front
    try:
        from app.push_service import ensure_vapid_keys
        ensure_vapid_keys(settings)
    except Exception as e:
        logger.warning(f"VAPID key setup skipped: {e}")
    
    # LUMA_MINIMAL=1: no caches, automations, schedulers or tunnel; only the
    # library search index (it reads Plex, and TMDB only with a Connectors key).
    if _minimal.enabled():
        logger.warning('LUMA_MINIMAL is set: background threads are NOT started (except the search index)')
        if not skip_bg:
            try:
                fast_search_index.start()
            except Exception as e:
                logger.error(f"Failed to start fast search index: {e}")
        return

    # Initialize library caches
    if not skip_bg:
        try:
            import threading
            threading.Thread(target=initialize_caches, daemon=True).start()
            logger.info("✅ Library cache initialization started")
        except Exception as e:
            logger.error(f"❌ Failed to start library cache initialization: {e}")
    
    # Start fast search index
    if not skip_bg:
        try:
            fast_search_index.start()
            logger.info("✅ Fast search index started")
        except Exception as e:
            logger.error(f"❌ Failed to start fast search index: {e}")
    
    # Start explore cache updater
    if not skip_bg:
        try:
            explore_cache_updater()
            import threading
            threading.Thread(target=update_explore_cache, daemon=True).start()
            logger.info("✅ Explore cache updater started")
        except Exception as e:
            logger.error(f"❌ Failed to start explore cache updater: {e}")
    
    # Hero cache refresh/heal runs only from this background scheduler, never
    # from a request. With FLASK_ENV=development the Werkzeug reloader runs
    # __main__ in both the watcher and the serving child; start it only in the
    # child so the startup refresh runs once.
    if not skip_bg:
        try:
            if _is_reloader_parent():
                logger.info("Hero cache scheduler deferred to the reloader child process")
            else:
                hero_cache.start_scheduler()
                logger.info("✅ Hero cache scheduler started")
        except Exception as e:
            logger.error(f"❌ Failed to start hero cache scheduler: {e}")
    
    # Automations and scheduled jobs
    if _AUTOMATIONS_DISABLED:
        logger.warning('LUMA_DISABLE_AUTOMATIONS is set: auto-rename, watchlist/auto-grab automations, schedulers, Downloads Guard and tunnel auto-start are NOT started')
    elif not skip_bg:
        # Auto-rename monitor
        try:
            from app.auto_rename import start_monitor_thread
            start_monitor_thread()
            logger.info("✅ Auto-rename monitor started")
        except Exception as e:
            logger.error(f"❌ Failed to start auto-rename monitor: {e}")
        
        # Trending watchlist automation
        try:
            from app.trending_watchlist import start_trending_watchlist_thread
            start_trending_watchlist_thread()
            logger.info("✅ Trending watchlist automation started")
        except Exception as e:
            logger.error(f"❌ Failed to start trending watchlist automation: {e}")
        
        # Trending TV watchlist automation
        try:
            from app.trending_tv_watchlist import start_trending_tv_watchlist_thread
            start_trending_tv_watchlist_thread()
            logger.info("✅ Trending TV watchlist automation started")
        except Exception as e:
            logger.error(f"❌ Failed to start trending TV watchlist automation: {e}")
        
        # Now available watchlist automation
        try:
            from app.now_available_watchlist import start_now_available_watchlist_thread
            start_now_available_watchlist_thread()
            logger.info("✅ Now available watchlist automation started")
        except Exception as e:
            logger.error(f"❌ Failed to start now available watchlist automation: {e}")
        
        # Anticipated watchlist automation
        try:
            from app.anticipated_watchlist import start_anticipated_watchlist_thread
            start_anticipated_watchlist_thread()
            logger.info("✅ Anticipated watchlist automation started")
        except Exception as e:
            logger.error(f"❌ Failed to start anticipated watchlist automation: {e}")
        
        # Latest auto-grab automation
        try:
            from app.latest_auto_grab import start_latest_auto_grab_thread
            start_latest_auto_grab_thread()
            logger.info("✅ Latest auto-grab automation started")
        except Exception as e:
            logger.error(f"❌ Failed to start Latest auto-grab automation: {e}")
        
        # 4K Remux auto-grab automation
        try:
            from app.upgrade_4k_auto_grab import start_upgrade_4k_auto_grab_thread
            start_upgrade_4k_auto_grab_thread()
            logger.info("✅ 4K Remux auto-grab automation started")
        except Exception as e:
            logger.error(f"❌ Failed to start 4K Remux auto-grab automation: {e}")
        
        # Daily upgrade scheduler
        try:
            from app.upgrade_scheduler import start_upgrade_scheduler_thread
            start_upgrade_scheduler_thread()
            logger.info("✅ Daily upgrade scheduler started")
        except Exception as e:
            logger.error(f"❌ Failed to start upgrade scheduler: {e}")
        
        # Movie Night weekly canon scheduler
        try:
            from app.movie_night import start_movie_night_thread
            start_movie_night_thread()
            logger.info("✅ Movie Night scheduler started")
        except Exception as e:
            logger.error(f"❌ Failed to start Movie Night scheduler: {e}")
        
        # Genre Roulette nightly scheduler
        try:
            from app.genre_roulette import start_genre_roulette_thread
            start_genre_roulette_thread()
            logger.info("✅ Genre Roulette scheduler started")
        except Exception as e:
            logger.error(f"❌ Failed to start Genre Roulette scheduler: {e}")
        
        # Franchise Completer nightly scheduler
        try:
            from app.franchise_completer import start_franchise_completer_thread
            start_franchise_completer_thread()
            logger.info("✅ Franchise Completer scheduler started")
        except Exception as e:
            logger.error(f"❌ Failed to start Franchise Completer scheduler: {e}")
        
        # Watchlist digital dates scheduler
        try:
            from app.watchlist_digital_dates import start_watchlist_digital_dates_thread
            start_watchlist_digital_dates_thread()
            logger.info("✅ Watchlist Digital Dates scheduler started")
        except Exception as e:
            logger.error(f"❌ Failed to start Watchlist Digital Dates scheduler: {e}")
        
        # What You're Missing pool refresher
        try:
            from app.daily_missing import start_daily_missing_thread
            start_daily_missing_thread()
            logger.info("✅ What You're Missing pool refresher started")
        except Exception as e:
            logger.error(f"❌ Failed to start What You're Missing refresher: {e}")
        
        # Movie Collections refresher
        try:
            from app.movie_collections import start_movie_collections_thread
            start_movie_collections_thread()
            logger.info("✅ Movie Collections refresher started")
        except Exception as e:
            logger.error(f"❌ Failed to start Movie Collections refresher: {e}")
        
        # Downloads Guard
        try:
            from app.downloads_guard import start_downloads_guard_thread
            start_downloads_guard_thread()
            logger.info("✅ Downloads Guard started")
        except Exception as e:
            logger.error(f"❌ Failed to start Downloads Guard: {e}")
        
        # Auto-start Cloudflare Tunnel if enabled
        try:
            tunnel_auto_start(settings)
        except Exception as e:
            logger.error(f"Cloudflare Tunnel auto-start failed: {e}")


# Note: Background thread initialization moved to start_services()

# Re-initialise hero_cache with values from settings (it was created at
# module-import time before the settings object existed).
hero_cache.init_from_settings(settings)

# Initialize Plex service from settings
plex_service.update_config(
    settings.get('plex.url', ''),
    settings.get('plex.token', ''),
)

# Ownership cache - stores ownership check results to avoid slow network DB queries
# Cache entries expire after 5 minutes
_ownership_cache = {}
_ownership_cache_ttl = 300  # 5 minutes

def get_ownership_cache_key(title, year, tmdb_id=None, imdb_id=None):
    """Generate a cache key for ownership checks."""
    return f"{title.lower().strip()}|{year}|{tmdb_id}|{imdb_id}"

def get_cached_ownership(title, year, tmdb_id=None, imdb_id=None):
    """Get cached ownership data if available and not expired."""
    key = get_ownership_cache_key(title, year, tmdb_id, imdb_id)
    if key in _ownership_cache:
        cached_time, data = _ownership_cache[key]
        if time.time() - cached_time < _ownership_cache_ttl:
            return data
        else:
            # Expired, remove from cache
            del _ownership_cache[key]
    return None

def set_cached_ownership(title, year, data, tmdb_id=None, imdb_id=None):
    """Cache ownership data."""
    key = get_ownership_cache_key(title, year, tmdb_id, imdb_id)
    _ownership_cache[key] = (time.time(), data)


def fetch_imdb_votes_batch(items, max_items=30):
    """Fetch IMDb vote counts for multiple items via OMDB API.

    Args:
        items: List of dicts with 'title' and 'year' keys
        max_items: Maximum number of items to fetch (to avoid rate limits)

    Returns:
        Dict mapping 'title_year' keys to vote counts
    """
    import concurrent.futures

    if not items or not settings.get('apis.omdb.api_key'):
        logger.warning("No items or no OMDB API key configured")
        return {}

    def fetch_votes(item):
        title = str(item.get('title', '') or '')
        year = str(item.get('year', '') or '')
        key = f"{title.lower()}_{year}"

        try:
            omdb_url = f"http://www.omdbapi.com/?apikey={settings.get('apis.omdb.api_key')}&t={title}"
            if year:
                omdb_url += f"&y={year}"

            response = requests.get(omdb_url, timeout=5)
            if response.ok:
                omdb_data = response.json()
                if omdb_data.get('Response') == 'True':
                    votes_str = omdb_data.get('imdbVotes', '0').replace(',', '')
                    votes = int(votes_str) if votes_str.isdigit() else 0
                    logger.debug(f"OMDB votes for '{title}' ({year}): {votes}")
                    return (key, votes)
                else:
                    logger.debug(f"OMDB not found: '{title}' ({year}) - {omdb_data.get('Error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"Error fetching OMDB votes for {title}: {e}")

        return (key, 0)

    # Limit items and fetch in parallel
    votes_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(fetch_votes, items[:max_items])
        for key, votes in results:
            votes_map[key] = votes

    logger.info(f"Fetched OMDB votes for {len(votes_map)} items")
    return votes_map


# ---------------------------------------------------------------------------
# Hero popularity enrichment cache
# ---------------------------------------------------------------------------
# Key = "imdb:{imdb_id}" or "tmdb:{tmdb_id}", value = (timestamp, {votes, rating})
_hero_popularity_cache: Dict[str, tuple] = {}
_HERO_POPULARITY_TTL = 12 * 3600  # 12 hours


def _get_cached_popularity(key: str) -> Optional[Dict]:
    """Return cached popularity data if still valid, else None."""
    entry = _hero_popularity_cache.get(key)
    if entry:
        ts, data = entry
        if time.time() - ts < _HERO_POPULARITY_TTL:
            return data
        del _hero_popularity_cache[key]
    return None


_HERO_POPULARITY_MAX = 2000


def _set_cached_popularity(key: str, data: Dict):
    _hero_popularity_cache[key] = (time.time(), data)
    # Bound growth over long uptimes — evict the oldest entry when over cap.
    if len(_hero_popularity_cache) > _HERO_POPULARITY_MAX:
        oldest = min(_hero_popularity_cache.items(), key=lambda kv: kv[1][0])[0]
        del _hero_popularity_cache[oldest]


def enrich_hero_imdb(items: List[Dict]) -> List[Dict]:
    """Enrich Plex items with IMDb votes + rating via OMDb.

    For each item, adds '_pop_votes' (int) and '_pop_rating' (float).
    Uses in-memory cache with 12h TTL to avoid hammering OMDb.
    """
    import concurrent.futures

    omdb_key = settings.get('apis.omdb.api_key', '')
    if not omdb_key:
        logger.warning("Hero boost: No OMDb API key configured, all items get score 0")
        for item in items:
            item['_pop_votes'] = 0
            item['_pop_rating'] = 0.0
        return items

    # Separate cached vs uncached
    uncached = []
    for item in items:
        imdb_id = item.get('imdb_id')
        if imdb_id:
            cached = _get_cached_popularity(f"imdb:{imdb_id}")
            if cached:
                item['_pop_votes'] = cached['votes']
                item['_pop_rating'] = cached['rating']
                continue
        uncached.append(item)

    if not uncached:
        return items

    def _fetch_omdb(item):
        imdb_id = item.get('imdb_id')
        title = item.get('title', '')
        year = item.get('year', '')
        try:
            params = {'apikey': omdb_key}
            if imdb_id:
                params['i'] = imdb_id
            else:
                params['t'] = title
                if year:
                    params['y'] = str(year)
            resp = requests.get('http://www.omdbapi.com/', params=params, timeout=5)
            if resp.ok:
                data = resp.json()
                if data.get('Response') == 'True':
                    votes_str = data.get('imdbVotes', '0').replace(',', '')
                    votes = int(votes_str) if votes_str.isdigit() else 0
                    rating_str = data.get('imdbRating', '0')
                    try:
                        rating = float(rating_str)
                    except (ValueError, TypeError):
                        rating = 0.0
                    result = {'votes': votes, 'rating': rating}
                    cache_key = f"imdb:{imdb_id}" if imdb_id else f"imdb:{title}_{year}"
                    _set_cached_popularity(cache_key, result)
                    return result
        except Exception as e:
            logger.debug(f"Hero boost OMDb error for '{item.get('title')}': {e}")
        return {'votes': 0, 'rating': 0.0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_fetch_omdb, uncached))
    for item, result in zip(uncached, results):
        item['_pop_votes'] = result['votes']
        item['_pop_rating'] = result['rating']

    enriched_count = sum(1 for item in items if item.get('_pop_votes', 0) > 0)
    logger.info(f"Hero boost IMDb: enriched {enriched_count}/{len(items)} items with vote data")
    return items


def enrich_hero_tmdb(items: List[Dict]) -> List[Dict]:
    """Enrich Plex items with TMDb vote_count + vote_average.

    For each item, adds '_pop_votes' (int) and '_pop_rating' (float).
    Uses in-memory cache with 12h TTL.
    """
    tmdb_key = settings.get('apis.tmdb.api_key', '')
    if not tmdb_key:
        logger.warning("Hero boost: No TMDb API key configured, all items get score 0")
        for item in items:
            item['_pop_votes'] = 0
            item['_pop_rating'] = 0.0
        return items

    import concurrent.futures

    uncached = []
    for item in items:
        tmdb_id = item.get('tmdb_id')
        if tmdb_id:
            cached = _get_cached_popularity(f"tmdb:{tmdb_id}")
            if cached:
                item['_pop_votes'] = cached['votes']
                item['_pop_rating'] = cached['rating']
                continue
        uncached.append(item)

    if not uncached:
        return items

    def _fetch_tmdb(item):
        tmdb_id = item.get('tmdb_id')
        if not tmdb_id:
            return {'votes': 0, 'rating': 0.0}
        try:
            resp = requests.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                params={'api_key': tmdb_key},
                timeout=5,
            )
            if resp.ok:
                data = resp.json()
                votes = int(data.get('vote_count', 0) or 0)
                rating = float(data.get('vote_average', 0) or 0)
                result = {'votes': votes, 'rating': rating}
                _set_cached_popularity(f"tmdb:{tmdb_id}", result)
                return result
        except Exception as e:
            logger.debug(f"Hero boost TMDb error for '{item.get('title')}': {e}")
        return {'votes': 0, 'rating': 0.0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_fetch_tmdb, uncached))
    for item, result in zip(uncached, results):
        item['_pop_votes'] = result['votes']
        item['_pop_rating'] = result['rating']

    enriched_count = sum(1 for item in items if item.get('_pop_votes', 0) > 0)
    logger.info(f"Hero boost TMDb: enriched {enriched_count}/{len(items)} items with vote data")
    return items


def _database_file() -> Path:
    """The application database (app.paths.database_path; a saved
    media.database_path wins over the data-folder default)."""
    from app.paths import database_path
    return database_path(settings.get('media.database_path'))


# Database setup
def init_database():
    """Initialize the SQLite database with required tables."""
    db_file = _database_file()
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    # Create movies table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            year INTEGER,
            tmdb_id INTEGER,
            imdb_id TEXT,
            overview TEXT,
            poster_path TEXT,
            backdrop_path TEXT,
            rating REAL,
            runtime INTEGER,
            genres TEXT,
            file_path TEXT,
            quality TEXT,
            size INTEGER,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create search history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            results_count INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_movies_title ON movies(title)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_movies_year ON movies(year)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_movies_tmdb_id ON movies(tmdb_id)')

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

def get_db_connection():
    """Get a connection to the SQLite database."""
    conn = sqlite3.connect(str(_database_file()))
    conn.row_factory = sqlite3.Row
    return conn

# Module-level helper for TMDb spelling correction logic
def _tmdb_needs_spelling_correction(results, query_str):
    """
    Check if TMDb results are weak enough to warrant spelling correction.
    Triggers on: 0-2 results OR no top-3 title contains/starts-with query.
    """
    if isinstance(results, dict):
        # For /search endpoint (dict with multi/movies/tv_shows)
        total = sum(len(results.get(k, [])) for k in ['multi', 'movies', 'tv_shows'])
        if total <= 2:
            return True
        query_lower = query_str.lower()
        for result_list in [results.get('multi', []), results.get('movies', []), results.get('tv_shows', [])]:
            for item in result_list[:3]:
                title = (item.get('title') or item.get('name', '')).lower()
                if query_lower in title or title.startswith(query_lower):
                    return False
        return True
    elif isinstance(results, list):
        # For /tmdb_match_search endpoint (list of results)
        if len(results) <= 2:
            return True
        query_lower = query_str.lower()
        for item in results[:3]:
            title = (item.get('title') or item.get('name', '')).lower()
            if query_lower in title or title.startswith(query_lower):
                return False
        return True
    return False

# TMDB API functions
class TMDBClient:
    """Client for interacting with TMDB API."""

    IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

    @property
    def BASE_URL(self) -> str:  # noqa: N802 - kept for existing callers
        # LUMA_TMDB_BASE_URL override, shared with the Connectors Test.
        from app.connectors import tmdb_base_url
        return tmdb_base_url()

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search_movies(self, query: str, year: Optional[int] = None) -> List[Dict]:
        """Search for movies on TMDB."""
        if not self.api_key:
            logger.debug("TMDB API key not configured")
            return []

        params = {
            'api_key': self.api_key,
            'query': query,
            'include_adult': False
        }
        if year:
            params['primary_release_year'] = year

        if fast_search_index._is_tmdb_rate_limited():
            logger.info("TMDB search_movies skipped: rate limited (Retry-After active)")
            return []

        try:
            response = requests.get(f"{self.BASE_URL}/search/movie", params=params, timeout=10)
            if response.status_code == 429:
                logger.warning("TMDB search_movies hit rate limit")
                fast_search_index._handle_tmdb_429(response)
                return []
            response.raise_for_status()
            fast_search_index._reset_tmdb_backoff()
            data = response.json()
            return data.get('results', [])
        except Exception as e:
            logger.error(f"TMDB search error: {_redact_secrets(e)}")
            return []

    def search_tv(self, query: str, year: Optional[int] = None) -> List[Dict]:
        """Search for TV shows on TMDB."""
        if not self.api_key:
            logger.debug("TMDB API key not configured")
            return []

        params = {
            'api_key': self.api_key,
            'query': query,
            'include_adult': False
        }
        if year:
            params['first_air_date_year'] = year

        if fast_search_index._is_tmdb_rate_limited():
            logger.info("TMDB search_tv skipped: rate limited (Retry-After active)")
            return []

        try:
            response = requests.get(f"{self.BASE_URL}/search/tv", params=params, timeout=10)
            if response.status_code == 429:
                logger.warning("TMDB search_tv hit rate limit")
                fast_search_index._handle_tmdb_429(response)
                return []
            response.raise_for_status()
            fast_search_index._reset_tmdb_backoff()
            data = response.json()
            return data.get('results', [])
        except Exception as e:
            logger.error(f"TMDB TV search error: {_redact_secrets(e)}")
            return []

    def search_multi(self, query: str) -> List[Dict]:
        """Combined movie + TV + person search, ranked the way TMDb's own site
        ranks it.

        This is what /search/movie + /search/tv cannot reproduce: each of those
        ranks only WITHIN its own type, so merging them means every movie sorts
        ahead of every TV show regardless of how well it matches. Searching
        "Severance" put 12 movies above the show. /search/multi scores all types
        against each other in one ranking.

        Returns raw results, each carrying TMDb's own `media_type`.
        """
        if not self.api_key:
            logger.debug("TMDB API key not configured")
            return []

        params = {
            'api_key': self.api_key,
            'query': query,
            'include_adult': False,
        }
        if fast_search_index._is_tmdb_rate_limited():
            logger.info("TMDB search_multi skipped: rate limited (Retry-After active)")
            return []

        try:
            response = requests.get(f"{self.BASE_URL}/search/multi", params=params, timeout=10)
            if response.status_code == 429:
                logger.warning("TMDB search_multi hit rate limit")
                fast_search_index._handle_tmdb_429(response)
                return []
            response.raise_for_status()
            fast_search_index._reset_tmdb_backoff()
            return response.json().get('results', [])
        except Exception as e:
            logger.error(f"TMDB multi search error: {_redact_secrets(e)}")
            return []

    def search_people(self, query: str) -> List[Dict]:
        """Search for people (actors/directors) on TMDB.
        Fetches multiple pages in parallel for short queries so popular
        actors (Tom Hanks, Tom Cruise) appear even when searching just a first name.
        Results sorted by popularity DESC, people with profile images first."""
        import concurrent.futures

        if not self.api_key:
            logger.debug("TMDB API key not configured")
            return []

        def fetch_page(page: int) -> List[Dict]:
            # Every page request honors an active Retry-After window.
            if fast_search_index._is_tmdb_rate_limited():
                return []
            params = {
                'api_key': self.api_key,
                'query': query,
                'include_adult': False,
                'page': page,
            }
            resp = requests.get(f"{self.BASE_URL}/search/person", params=params, timeout=8)
            if resp.status_code == 429:
                logger.warning(f"TMDB search_people page {page} hit rate limit")
                fast_search_index._handle_tmdb_429(resp)
                return []
            resp.raise_for_status()
            fast_search_index._reset_tmdb_backoff()
            return resp.json().get('results', [])

        try:
            # For short queries (< 8 chars), fetch 3 pages in parallel to
            # capture popular actors beyond TMDb's exact-match first page.
            # For longer queries, 1 page is usually sufficient.
            max_pages = 3 if len(query.strip()) < 8 else 1

            all_results: List[Dict] = []
            seen_ids: set = set()

            if max_pages == 1:
                all_results = fetch_page(1)
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    page_futures = {executor.submit(fetch_page, p): p for p in range(1, max_pages + 1)}
                    for future in concurrent.futures.as_completed(page_futures):
                        try:
                            page_results = future.result(timeout=8)
                            for person in page_results:
                                pid = person.get('id')
                                if pid and pid not in seen_ids:
                                    seen_ids.add(pid)
                                    all_results.append(person)
                        except Exception:
                            pass  # skip failed pages

            # Format results with profile URL
            for person in all_results:
                if person.get('profile_path'):
                    person['profile_url'] = f"{self.IMAGE_BASE_URL}/w185{person['profile_path']}"
                else:
                    person['profile_url'] = None

            # Sort: people with profile images first, then by popularity DESC
            all_results.sort(key=lambda p: (
                1 if p.get('profile_path') else 0,
                p.get('popularity', 0),
            ), reverse=True)
            return all_results
        except Exception as e:
            logger.error(f"TMDB people search error: {_redact_secrets(e)}")
            return []

    def get_movie_details(self, tmdb_id: int) -> Optional[Dict]:
        """Get detailed information about a movie."""
        if not self.api_key:
            return None

        try:
            response = requests.get(
                f"{self.BASE_URL}/movie/{tmdb_id}",
                params={'api_key': self.api_key, 'append_to_response': 'credits,videos,release_dates'},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"TMDB movie details error: {_redact_secrets(e)}")
            return None

    def get_movie_similar(self, tmdb_id: int) -> List[Dict]:
        """Movies to recommend alongside *tmdb_id*, best matches first.

        Merges two TMDb endpoints because neither alone is good enough:
        `/recommendations` is the curated, behaviour-driven list TMDb shows on
        its own site, but it thins out fast for obscure titles; `/similar` is
        purely keyword/genre derived, so it always returns something but is
        noisier. Recommendations lead, similar backfills, deduped by id.
        """
        if not self.api_key:
            return []

        def _fetch(kind: str) -> List[Dict]:
            try:
                response = requests.get(
                    f"{self.BASE_URL}/movie/{tmdb_id}/{kind}",
                    params={'api_key': self.api_key, 'page': 1},
                    timeout=10,
                )
                response.raise_for_status()
                return response.json().get('results', []) or []
            except Exception as e:
                logger.warning(f"TMDB movie {kind} error for {tmdb_id}: {_redact_secrets(e)}")
                return []

        with ThreadPoolExecutor(max_workers=2) as pool:
            recommended, similar = pool.map(_fetch, ('recommendations', 'similar'))

        merged: List[Dict] = []
        seen = {tmdb_id}
        for result in list(recommended) + list(similar):
            rid = result.get('id')
            if not rid or rid in seen:
                continue
            seen.add(rid)
            merged.append(result)
        return merged

    def get_tv_details(self, tmdb_id: int) -> Optional[Dict]:
        """Get detailed information about a TV show."""
        if not self.api_key:
            return None

        try:
            response = requests.get(
                f"{self.BASE_URL}/tv/{tmdb_id}",
                params={'api_key': self.api_key, 'append_to_response': 'credits,external_ids,content_ratings,videos'},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"TMDB TV details error: {_redact_secrets(e)}")
            return None

    def get_popular(self, page: int = 1) -> List[Dict]:
        """Get popular movies from TMDB."""
        if not self.api_key:
            return []

        try:
            response = requests.get(
                f"{self.BASE_URL}/movie/popular",
                params={'api_key': self.api_key, 'page': page},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data.get('results', [])
        except Exception as e:
            logger.error(f"TMDB popular error: {_redact_secrets(e)}")
            return []

    def get_trending(self, media_type: str = 'movie', time_window: str = 'week') -> List[Dict]:
        """Get trending content from TMDB."""
        if not self.api_key:
            return []

        try:
            response = requests.get(
                f"{self.BASE_URL}/trending/{media_type}/{time_window}",
                params={'api_key': self.api_key},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data.get('results', [])
        except Exception as e:
            logger.error(f"TMDB trending error: {_redact_secrets(e)}")
            return []

# Initialize TMDB client
tmdb = TMDBClient(settings.get('apis.tmdb.api_key'))

# Request page for friends and family
from app import request_portal as _request_portal
from app import portal_guard
_request_portal.init_app(app, settings)

# Authentication routes
from app import auth_routes
auth_routes.init_app(app)

# Portal traffic guard (must be registered before other routes)
@app.before_request
def _portal_traffic_guard():
    return portal_guard.guard_portal_traffic(settings)

def _is_auth_exempt(path: str, method: str) -> bool:
    """True for the paths a signed-out visitor may reach (see _AUTH_EXEMPT)."""
    if path in _AUTH_EXEMPT or path in _AUTH_EXEMPT_STATIC:
        return True
    if path.startswith(_AUTH_EXEMPT_STATIC_PREFIXES):
        return True
    if path.startswith('/static/spa/'):
        return True  # SPA bundle (hashed assets, no data)
    if _minimal.enabled() and (path in _minimal.SIGNED_OUT_PATHS
                               or path.startswith(_minimal.SIGNED_OUT_PREFIXES)):
        # Minimal builds sign in everyone, LAN included, so the app shell,
        # its bundle and Get Started (whose endpoints check the setup state,
        # session and cookies themselves) must load signed out.
        return True
    if path.startswith('/api/request/'):
        # Requester endpoints check the request-page cookie themselves.
        return portal_guard.is_portal_allowed(path, method)
    if path == '/api/version' and method in ('GET', 'HEAD'):
        return True  # kiosks poll it to know when to reload; it holds no data
    if path == '/api/admin/telegram-action' and method in ('GET', 'POST'):
        # Approve/deny buttons open in Telegram's browser with no Luma
        # session. Only a link Luma signed skips sign-in for this one route:
        # a valid one gets the confirm page (GET) or acts once (POST); an
        # expired, used or revoked one gets the HTML error page. The route
        # re-checks the link itself and grants nothing else.
        return _telegram_action_link_signed()
    return False


_LINK_EXEMPT_REASONS = (None, 'expired', 'used', 'revoked')


def _telegram_action_link_signed() -> bool:
    from app import action_tokens
    fields = request.form if request.method == 'POST' else request.args
    _, reason = action_tokens.check(app.config['SECRET_KEY'], fields, _action_token_dir())
    return reason in _LINK_EXEMPT_REASONS


def _action_token_dir():
    from app.first_run import data_dir
    return data_dir()


# Authentication guard (must run after portal guard and load_current_user)
@app.before_request
def _auth_guard():
    """Redirect to login if authentication is required and user not authenticated."""
    if _is_auth_exempt(request.path, request.method):
        return None

    if _access.should_authenticate(settings, request):
        # Check if user is authenticated (not implicit)
        if not getattr(g, 'current_user', None) or getattr(g, 'implicit_user', False):
            # For API calls, return 401
            if request.path.startswith('/api/'):
                return jsonify({"error": "Authentication required"}), 401
            # For HTML pages, redirect to login
            url_base = app.config.get('URL_BASE', '')
            from urllib.parse import quote
            target = request.path + (f'?{request.query_string.decode("latin-1")}' if request.query_string else '')
            return redirect(f'{url_base}/login?next={quote(target)}')

# CSRF protection for API state-changing requests (must run after load_current_user)
@app.before_request
def _csrf_api_guard():
    """Check Origin/Referer for cookie-authenticated POST/PUT/PATCH/DELETE on /api/*.
    
    Protects against CSRF attacks by validating same-origin on state-changing requests.
    When Origin or Referer is present, validates it even for implicit users.
    """
    # Only check API routes
    if not request.path.startswith('/api/'):
        return None

    # Implicit (no-cookie) LAN access must never be usable from another site,
    # reads included: a cross-site fetch or a foreign Origin is refused.
    if getattr(g, 'implicit_user', False):
        from app.auth_routes import is_same_origin as _same_origin
        if request.headers.get('Sec-Fetch-Site', '').lower() in ('cross-site', 'same-site'):
            return jsonify({"error": "Cross-site request refused"}), 403
        _origin = request.headers.get('Origin')
        if _origin and not _same_origin(_origin):
            return jsonify({"error": "CSRF validation failed: invalid Origin"}), 403

    # Only check state-changing methods
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    
    # Skip request portal endpoints (they have their own CSRF protection)
    if request.path.startswith('/api/request/'):
        return None
    
    # Skip if not authenticated (the route itself will refuse)
    if not getattr(g, 'current_user', None):
        return None
    
    # Skip if authenticated via a valid API key (not cookie-based)
    if g.current_user.get('id') == 0 and request.headers.get("X-API-Key"):
        return None
    
    origin = request.headers.get('Origin')
    referer = request.headers.get('Referer')
    
    # Scheme/host already reflect X-Forwarded-* only for trusted proxies.
    from app.auth_routes import is_same_origin
    
    # A cross-origin Origin or Referer is refused for everyone, including
    # implicit LAN-owner users.
    if origin and not is_same_origin(origin):
        return jsonify({"error": "CSRF validation failed: invalid Origin"}), 403
    if referer and not is_same_origin(referer):
        return jsonify({"error": "CSRF validation failed: invalid Referer"}), 403
    
    # Skip CSRF check for implicit users when no Origin/Referer (LAN direct access)
    if getattr(g, 'implicit_user', False):
        if not origin and not referer:
            return None
    
    # For explicit session users, require Origin or Referer
    if not origin and not referer:
        return jsonify({"error": "CSRF validation failed: missing Origin/Referer"}), 403
    
    return None
def _serve_spa():
    """Serve the React SPA index.html (from SPA_DIR) with runtime URL base injection."""
    spa_index = os.path.join(app.config['SPA_DIR'], 'index.html')
    if os.path.exists(spa_index):
        # Read the index.html
        with open(spa_index, 'r', encoding='utf-8') as f:
            html = f.read()
        
        # Inject URL base as a meta tag and inline script
        url_base = app.config.get('URL_BASE', '')
        injection = f'''
    <meta name="url-base" content="{url_base}">
    <script>window.__LUMA_URL_BASE__ = "{url_base}";</script>
'''
        if _minimal.enabled():
            injection += '    <script>window.__LUMA_MINIMAL__ = true;</script>\n'
        # Insert after <head> tag
        html = html.replace('<head>', f'<head>{injection}', 1)
        
        # The bundle is built with a relative base (./assets/...). Anchor it
        # to the URL base so deep links like /luma/library/1 still load it.
        for attr in ('src', 'href'):
            html = html.replace(f'{attr}="./assets/', f'{attr}="{url_base}/assets/')
            html = html.replace(f'{attr}="./manifest.json"', f'{attr}="{url_base}/manifest.json"')
            html = html.replace(f'{attr}="./static/', f'{attr}="{url_base}/static/')
        
        # Prefix root-relative paths with the URL base
        if url_base:
            # Fix manifest.json reference
            html = html.replace('href="/manifest.json"', f'href="{url_base}/manifest.json"')
            # Fix apple-touch-icon reference
            html = html.replace('href="/static/', f'href="{url_base}/static/')
            html = html.replace('src="/static/', f'src="{url_base}/static/')
        
        resp = Response(html, mimetype='text/html')
        # Marks the real app page: the only response the service worker may
        # keep as its offline copy.
        resp.headers['X-Luma-App-Shell'] = '1'
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    # Dev fallback: redirect to Vite dev server
    return redirect('http://localhost:5173/')

@app.route('/')
@app.route('/explore')
@app.route('/library')
@app.route('/now-available')
@app.route('/watchlist')
@app.route('/rooms')
@app.route('/browse')
@app.route('/settings')
@app.route('/upgrades')
@app.route('/start')
@app.route('/unlock')
@app.route('/settings/libraries')
@app.route('/admin')
@app.route('/setup')
def spa_route():
    """Serve React SPA for all frontend routes."""
    return _serve_spa()


@app.route('/settings/<path:section>')
def spa_settings_route(section):
    """Any Settings section (/settings/plex, /settings/connectors, a Plex
    sign-in returning in the same tab, ...) reloads into the SPA."""
    return _serve_spa()

@app.route('/rate/<int:tmdb_id>')
def spa_rate_route(tmdb_id):
    """SPA shell for the rate screen.

    Needs its own rule because it is parameterised, and it matters more than the
    other shell routes: a push notification deep link is *always* a cold direct
    load, so a 404 here means the notification does nothing at all.
    """
    return _serve_spa()

@app.route('/kiosk/')
@app.route('/kiosk/<path:path>')
def kiosk_redirect(path=''):
    """Backwards-compat: redirect /kiosk/ to /."""
    return redirect('/')


# The pre-React Jinja pages were removed; keep their old URLs working for
# bookmarks by sending them to the SPA equivalent.
_LEGACY_PAGE_REDIRECTS = {
    '/activity': '/',
    '/browse_libraries': '/browse',
    '/browse_media': '/browse',
    '/legacy/explore': '/explore',
}


@app.route('/activity')
@app.route('/browse_libraries')
@app.route('/browse_media')
@app.route('/legacy/explore')
def legacy_page_redirect():
    """Backwards-compat: redirect retired legacy pages to the SPA."""
    return redirect(_LEGACY_PAGE_REDIRECTS.get(request.path, '/'))



@app.route('/api/radarr/quality-profiles', methods=['GET'])
def get_radarr_quality_profiles():
    """Get Radarr quality profiles."""
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured', 'needs_setup': 'radarr'}), 400
        profiles = radarr.get_quality_profiles()
        return jsonify(profiles if profiles else [])
    except Exception as e:
        logger.error(f"Error fetching Radarr quality profiles: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/radarr/upgrade-profiles', methods=['GET'])
def get_radarr_upgrade_profiles():
    """Return quality profiles configured for the upgrade/watchlist modal.
    Falls back to all Radarr profiles if none configured in settings."""
    configured = settings.get('radarr.upgrade_quality_profiles') or []
    if configured:
        return jsonify(configured)
    # Fallback: return all Radarr quality profiles
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured', 'needs_setup': 'radarr'}), 400
        profiles = radarr.get_quality_profiles()
        return jsonify([{'id': p['id'], 'name': p['name']} for p in (profiles or [])])
    except Exception as e:
        logger.error(f"Error fetching Radarr upgrade profiles: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/radarr/root-folders', methods=['GET'])
def get_radarr_root_folders():
    """Get Radarr root folders."""
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured', 'needs_setup': 'radarr'}), 400
        folders = radarr.get_root_folders()
        return jsonify(folders if folders else [])
    except Exception as e:
        logger.error(f"Error fetching Radarr root folders: {e}")
        return jsonify({'error': str(e)}), 500

_radarr_wl_cache: Dict[str, tuple] = {}  # key → (timestamp, data)
_RADARR_WL_TTL = 120  # 2 minutes

@app.route('/api/radarr/watchlist/tmdb-ids', methods=['GET'])
def get_radarr_watchlist_tmdb_ids():
    """Return TMDb IDs of all monitored movies in Radarr (cached 2m)."""
    cache_key = 'radarr_wl_ids'
    cached = _radarr_wl_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _RADARR_WL_TTL:
            return jsonify(data)

    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'tmdb_ids': []})
        movies = radarr.get_movies()
        tmdb_ids = [
            m['tmdbId'] for m in movies
            if m.get('monitored') and m.get('tmdbId')
        ]
        payload = {'tmdb_ids': tmdb_ids}
        _radarr_wl_cache[cache_key] = (time.time(), payload)
        return jsonify(payload)
    except Exception as e:
        logger.error(f"Error fetching Radarr watchlist IDs: {e}")
        return jsonify({'tmdb_ids': []})

_sonarr_wl_cache: Dict[str, tuple] = {}  # key → (timestamp, data)
_SONARR_WL_TTL = 120  # 2 minutes

@app.route('/api/sonarr/watchlist/tmdb-ids', methods=['GET'])
def get_sonarr_watchlist_tmdb_ids():
    """Return TMDb IDs of all monitored series in Sonarr (cached 2m).

    Mirrors the Radarr watchlist endpoint so TV tiles get the same
    'in watchlist' (blue) orb treatment. Only monitored series count —
    inert browse-added (unmonitored) series are intentionally excluded.
    """
    cache_key = 'sonarr_wl_ids'
    cached = _sonarr_wl_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _SONARR_WL_TTL:
            return jsonify(data)

    try:
        sonarr = get_sonarr_for_request()
        if not sonarr:
            return jsonify({'tmdb_ids': []})
        series = sonarr.get_series() or []
        tmdb_ids = [
            s['tmdbId'] for s in series
            if s.get('monitored') and s.get('tmdbId')
        ]
        payload = {'tmdb_ids': tmdb_ids}
        _sonarr_wl_cache[cache_key] = (time.time(), payload)
        return jsonify(payload)
    except Exception as e:
        logger.error(f"Error fetching Sonarr watchlist IDs: {e}")
        return jsonify({'tmdb_ids': []})

@app.route('/api/sources/config')
def get_sources_config():
    """Return the effective movie sources provider for the frontend."""
    try:
        radarr_configured = bool(settings.get('radarr.api_key'))
        return jsonify({
            'movie_provider': 'radarr',
            'radarr_configured': radarr_configured,
        })
    except Exception as e:
        logger.error(f"Error getting sources config: {e}")
        return jsonify({'movie_provider': 'radarr', 'radarr_configured': False})

@app.route('/api/radarr/movie/releases')
def radarr_movie_releases():
    """Search for available releases for a movie via Radarr interactive search."""
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr is not configured', 'needs_setup': 'radarr'}), 503

        tmdb_id = request.args.get('tmdb_id', type=int)
        title = request.args.get('title', '')
        year = request.args.get('year', type=int)

        # Resolve tmdb_id from title+year via TMDb if not provided
        if not tmdb_id and title:
            try:
                tmdb_api_key = settings.get('apis.tmdb.api_key')
                if tmdb_api_key:
                    import requests as http_requests
                    search_url = f"https://api.themoviedb.org/3/search/movie"
                    params = {'api_key': tmdb_api_key, 'query': title}
                    if year:
                        params['year'] = year
                    resp = http_requests.get(search_url, params=params, timeout=10)
                    if resp.ok:
                        results = resp.json().get('results', [])
                        if results:
                            tmdb_id = results[0]['id']
                            logger.info(f"Resolved tmdb_id={tmdb_id} from title='{title}' year={year}")
            except Exception as e:
                logger.warning(f"TMDb lookup failed for '{title}': {e}")

        if not tmdb_id:
            return jsonify({'error': 'Could not determine TMDB ID. Provide tmdb_id or a valid title.'}), 400

        # Look up movie in Radarr
        movie = radarr.get_movie_by_tmdb_id(tmdb_id)
        auto_added = False

        if not movie:
            # Movie not in Radarr — add it minimally (unmonitored, no auto-search)
            logger.info(f"Movie tmdb_id={tmdb_id} not in Radarr, adding for release search")
            profiles = radarr.get_quality_profiles()
            root_folders = radarr.get_root_folders()

            if not profiles or not root_folders:
                return jsonify({'error': 'Radarr has no quality profiles or root folders configured'}), 500

            # Use the configured default profile (never silently Radarr's "Any")
            profile_id = _resolve_radarr_profile_id(profiles)

            movie = radarr.add_movie(
                tmdb_id=tmdb_id,
                title=title or f"Movie {tmdb_id}",
                year=year or datetime.now().year,
                quality_profile_id=profile_id,
                root_folder_path=root_folders[0]['path'],
                monitored=False,
                search_for_movie=False,
            )
            if not movie:
                return jsonify({'error': 'Failed to add movie to Radarr for release search'}), 500
            auto_added = True

        movie_id = movie['id']

        # Override movie quality profile to Settings-selected profile for search
        settings_profile_id = settings.get('radarr.default_quality_profile_id') or 0
        original_profile_id = movie.get('qualityProfileId')
        profile_changed = False

        if settings_profile_id and settings_profile_id != original_profile_id:
            movie['qualityProfileId'] = settings_profile_id
            updated = radarr.update_movie(movie)
            if updated:
                profile_changed = True
                logger.info(f"Temporarily set movie {movie_id} profile to {settings_profile_id} for release search")

        try:
            # Interactive release search — Radarr queries all indexers
            all_releases = radarr.search_movie_releases(movie_id)

            # Format releases, including rejected ones with rejection metadata
            formatted = []
            rejected_count = 0
            for rel in all_releases:
                if rel.get('rejected', False):
                    rejected_count += 1

                quality_obj = rel.get('quality', {}).get('quality', {})
                custom_formats = rel.get('customFormats', [])
                cf_names = [cf.get('name', '') for cf in custom_formats] if custom_formats else []

                formatted.append({
                    'guid': rel.get('guid'),
                    'indexer_id': rel.get('indexerId'),
                    'indexer': rel.get('indexer', 'Unknown'),
                    'title': rel.get('title', ''),
                    'size': rel.get('size', 0),
                    'quality': quality_obj.get('name', 'Unknown'),
                    'quality_source': quality_obj.get('source', ''),
                    'quality_resolution': quality_obj.get('resolution', 0),
                    'custom_formats': cf_names,
                    'custom_format_score': rel.get('customFormatScore', 0),
                    'seeders': rel.get('seeders'),
                    'leechers': rel.get('leechers'),
                    'proper': rel.get('isProper', False),
                    'age': rel.get('age'),
                    'ageMinutes': rel.get('ageMinutes'),
                    'publishDate': rel.get('publishDate'),
                    'protocol': rel.get('protocol', 'unknown'),
                    # download_url + info_hash needed so rejected rows can be
                    # grabbed via the grab-to-library pipeline in all-results mode.
                    'download_url': rel.get('downloadUrl', '') or '',
                    'info_hash': rel.get('infoHash', '') or '',
                    'rejected': rel.get('rejected', False),
                    'rejection_reasons': [
                        rej.get('reason', '') if isinstance(rej, dict) else str(rej)
                        for rej in rel.get('rejections', [])
                        if (rej.get('reason', '') if isinstance(rej, dict) else str(rej))
                    ],
                })

            logger.info(f"Radarr releases for tmdb_id={tmdb_id}: {len(formatted)} total, {rejected_count} rejected")

            return jsonify({
                'success': True,
                'releases': formatted,
                'movie_id': movie_id,
                'auto_added': auto_added,
                'total_releases': len(formatted),
            })
        finally:
            # Restore original profile — never permanently change movie in Radarr
            if profile_changed:
                movie['qualityProfileId'] = original_profile_id
                radarr.update_movie(movie)
                logger.info(f"Restored movie {movie_id} to original profile {original_profile_id}")

    except Exception as e:
        logger.error(f"Error searching Radarr releases: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/indexer/browse')
def indexer_browse():
    """Browse top releases across all configured indexers.

    Query params:
        quality  – filter by quality substring (e.g. "2160p", "Remux", "WEBDL-2160p")
        sort     – sort field: seeders (default), size, date
        limit    – max results to return (default 25)
        query    – optional search term to narrow results
    """
    try:
        from app.indexer_api import get_indexer_client

        client = get_indexer_client(settings)
        if not client:
            return jsonify({'error': 'No indexer configured. Set up Prowlarr or Torznab in settings.'}), 503

        quality_filter = request.args.get('quality', '').strip()
        sort_by = request.args.get('sort', 'seeders').strip().lower()
        limit = request.args.get('limit', 25, type=int)
        query = request.args.get('query', '').strip()

        if sort_by not in ('seeders', 'size', 'date'):
            sort_by = 'seeders'
        if limit < 1:
            limit = 25
        if limit > 200:
            limit = 200

        # Fetch releases — use sweep for broader coverage, or search if query given
        if query:
            raw = client.sweep_movies(queries=[query], max_workers=1)
        else:
            raw = client.sweep_movies(max_workers=3)

        # Build a set of titles already downloaded in Radarr library
        library_titles = set()
        try:
            radarr = get_radarr_for_request()
            if radarr:
                movies = radarr.get_movies()
                if movies:
                    for m in movies:
                        if m.get('hasFile'):
                            clean = (m.get('title', '') or '').strip().lower()
                            if clean:
                                library_titles.add(clean)
                            # Also add cleanTitle if available
                            ct = (m.get('cleanTitle', '') or '').strip().lower()
                            if ct:
                                library_titles.add(ct)
        except Exception as e:
            logger.warning(f"indexer_browse: couldn't check Radarr library: {e}")

        # Parse pub_date helper
        def _age_days(pub_date_str):
            if not pub_date_str:
                return None
            try:
                # Handle ISO format with or without timezone
                clean = pub_date_str.replace('Z', '+00:00')
                from datetime import timezone
                dt = datetime.fromisoformat(clean)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = datetime.now(timezone.utc) - dt
                return max(0, delta.days)
            except Exception:
                return None

        # Build result list
        results = []
        for item in raw:
            title_clean = (item.get('title', '') or '').strip().lower()
            torrent_name = item.get('torrent_name', '') or ''

            # Skip items already in library
            if title_clean and title_clean in library_titles:
                continue

            # Determine quality label from resolution + source
            resolution = (item.get('resolution', '') or '').upper()
            source = (item.get('source', '') or '').upper()
            if source == 'REMUX' and resolution:
                quality_label = f"{resolution} Remux"
            elif source == 'WEB-DL' and resolution:
                quality_label = f"WEBDL-{resolution}"
            elif source and resolution:
                quality_label = f"{source}-{resolution}"
            elif resolution:
                quality_label = resolution
            elif source:
                quality_label = source
            else:
                quality_label = 'Unknown'

            # Apply quality filter
            if quality_filter:
                filter_upper = quality_filter.upper()
                if filter_upper not in quality_label.upper() and filter_upper not in torrent_name.upper():
                    continue

            age_days = _age_days(item.get('pub_date'))

            results.append({
                'title': torrent_name,
                'quality': quality_label,
                'size_gb': item.get('size_gb', 0),
                'seeders': item.get('seeders', 0),
                'leechers': 0,  # Not available from indexer_api; included for schema
                'indexer': item.get('tracker', ''),
                'age_days': age_days,
                'clean_title': item.get('title', ''),
                'year': item.get('year', ''),
                'resolution': item.get('resolution', ''),
                'source': item.get('source', ''),
            })

        # Sort
        if sort_by == 'seeders':
            results.sort(key=lambda x: x.get('seeders', 0), reverse=True)
        elif sort_by == 'size':
            results.sort(key=lambda x: x.get('size_gb', 0), reverse=True)
        elif sort_by == 'date':
            results.sort(key=lambda x: x.get('age_days') if x.get('age_days') is not None else 9999)

        # Limit
        results = results[:limit]

        return jsonify({
            'success': True,
            'results': results,
            'total': len(results),
            'sort': sort_by,
            'quality_filter': quality_filter or None,
        })

    except Exception as e:
        logger.error(f"Error in indexer_browse: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


# In-memory cache for raw "View all results" indexer searches.
_all_results_cache: Dict[tuple, tuple] = {}   # (media_type, title_lower, year) → (timestamp, data)
_ALL_RESULTS_CACHE_TTL = 300                   # 5 minutes
# An empty result is almost always a transient tracker problem (Prowlarr
# rate-limit ban, indexer down) rather than "this title has no sources", so it
# gets a much shorter life — caching a zero for 5 minutes turns a 30-second
# tracker blip into five minutes of an empty modal.
_ALL_RESULTS_EMPTY_TTL = 20

# In-memory cache for TMDb manual-match searches.
_tmdb_match_cache: Dict[tuple, tuple] = {}     # (query, media_type) → (timestamp, data)
_TMDB_MATCH_CACHE_TTL = 600                    # 10 minutes


@app.route('/api/sources/all-results')
def sources_all_results():
    """Raw, broad-category indexer search with NO quality filtering.

    Query params:
        title       – required. Torrent search title.
        media_type  – required. 'movie' | 'tv'.
        year        – optional int, appended to the search query.
        tmdb_id     – optional int (accepted; not required for the search).
    """
    try:
        from app.indexer_api import get_indexer_client

        title = (request.args.get('title', '') or '').strip()
        media_type = (request.args.get('media_type', '') or '').strip().lower()
        year = request.args.get('year', type=int)

        if not title or media_type not in ('movie', 'tv'):
            return jsonify({'error': 'title and media_type are required'}), 400

        client = get_indexer_client(settings)
        if not client:
            return jsonify({'error': 'No indexer configured. Set up Prowlarr or Torznab in settings.'}), 503

        cache_key = (media_type, title.lower(), year)
        cached = _all_results_cache.get(cache_key)
        if cached:
            ts, data = cached
            ttl = (_ALL_RESULTS_CACHE_TTL if data.get('releases')
                   else _ALL_RESULTS_EMPTY_TTL)
            if time.time() - ts < ttl:
                return jsonify(data)
            _all_results_cache.pop(cache_key, None)

        raw = client.search_all(title, media_type, year)

        releases = []
        for item in raw:
            resolution = (item.get('resolution', '') or '').upper()
            source = (item.get('source', '') or '').upper()
            if source == 'REMUX' and resolution:
                quality_label = f"{resolution} Remux"
            elif source == 'WEB-DL' and resolution:
                quality_label = f"WEBDL-{resolution}"
            elif source and resolution:
                quality_label = f"{source}-{resolution}"
            elif resolution:
                quality_label = resolution
            elif source:
                quality_label = source
            else:
                quality_label = 'Unknown'

            releases.append({
                'guid': item.get('guid', '') or '',
                'indexer': item.get('indexer', '') or '',
                'indexer_id': item.get('indexer_id'),
                'title': item.get('title', '') or '',
                'size': int(item.get('size', 0) or 0),
                'seeders': item.get('seeders', 0) or 0,
                'leechers': item.get('leechers', 0) or 0,
                'quality': quality_label,
                'protocol': item.get('protocol', 'torrent') or 'torrent',
                'download_url': item.get('download_url', '') or '',
                'info_hash': item.get('info_hash', '') or '',
                'pub_date': item.get('pub_date', '') or '',
                'origin': 'indexer',
                'rejected': False,
                'rejection_reasons': [],
            })

        payload = {
            'success': True,
            'total': len(releases),
            'releases': releases,
        }
        _all_results_cache[cache_key] = (time.time(), payload)
        return jsonify(payload)

    except Exception as e:
        logger.error(f"Error in sources_all_results: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/tmdb/match-search')
def tmdb_match_search():
    """TMDb multi-search for the manual-match picker.

    Query params:
        query       – required. Search text.
        media_type  – optional. 'movie' | 'tv'. Omit to search both.
    """
    try:
        query = (request.args.get('query', '') or '').strip()
        media_type = (request.args.get('media_type', '') or '').strip().lower()

        if not query:
            return jsonify({'error': 'query is required'}), 400

        api_key = settings.get('apis.tmdb.api_key')
        if not api_key:
            return jsonify({'error': 'TMDb not configured', 'needs_setup': 'tmdb'}), 503

        if media_type not in ('movie', 'tv'):
            media_type = ''  # search both

        cache_key = (query.lower(), media_type)
        cached = _tmdb_match_cache.get(cache_key)
        if cached:
            ts, data = cached
            if time.time() - ts < _TMDB_MATCH_CACHE_TTL:
                return jsonify(data)
            _tmdb_match_cache.pop(cache_key, None)

        tmdb_base = settings.get('apis.tmdb.base_url', '') or 'https://api.themoviedb.org/3'
        merged = []

        def _fetch(mtype, search_query=None):
            if search_query is None:
                search_query = query
            if fast_search_index._is_tmdb_rate_limited():
                return mtype, []  # honor active Retry-After
            endpoint = '/search/movie' if mtype == 'movie' else '/search/tv'
            try:
                resp = requests.get(
                    f'{tmdb_base}{endpoint}',
                    params={'api_key': api_key, 'query': search_query, 'language': 'en-US', 'page': 1},
                    timeout=10,
                )
                if resp.status_code == 200:
                    fast_search_index._reset_tmdb_backoff()
                    return mtype, resp.json().get('results', [])
                elif resp.status_code == 429:
                    logger.warning(f"tmdb_match_search {mtype} hit rate limit")
                    fast_search_index._handle_tmdb_429(resp)
            except Exception as e:
                logger.warning(f"tmdb_match_search {mtype} lookup failed: {e}")
            return mtype, []

        types = [media_type] if media_type else ['movie', 'tv']
        for mtype in types:
            _, items = _fetch(mtype)
            
            # If results are weak, try spelling correction (silent - no UI text)
            # Skip correction retry if we're currently rate limited
            if (len(query) > 3 and 
                not fast_search_index._is_tmdb_rate_limited() and 
                _tmdb_needs_spelling_correction(items, query)):
                try:
                    corrected = fast_search_index.find_spelling_correction(query, threshold=0.65)
                    if corrected and corrected.lower() != query.lower():
                        logger.info(f"tmdb_match_search: trying correction '{corrected}' for '{query}'")
                        _, corrected_items = _fetch(mtype, corrected)
                        # Merge: direct matches first, then dedupe corrected results after
                        existing_ids = {item.get('id') for item in items if item.get('id')}
                        for item in corrected_items:
                            if item.get('id') and item.get('id') not in existing_ids:
                                items.append(item)
                                existing_ids.add(item.get('id'))
                except Exception as e:
                    logger.warning(f"Spelling correction in tmdb_match_search failed: {e}")
            
            for it in items:
                if mtype == 'movie':
                    title = it.get('title', '') or ''
                    date = it.get('release_date', '') or ''
                else:
                    title = it.get('name', '') or ''
                    date = it.get('first_air_date', '') or ''
                merged.append({
                    'tmdb_id': it.get('id'),
                    'media_type': mtype,
                    'title': title,
                    'year': (date[:4] if date else ''),
                    'poster_path': it.get('poster_path'),
                    '_popularity': it.get('popularity', 0) or 0,
                })

        merged.sort(key=lambda x: x.get('_popularity', 0), reverse=True)
        results = []
        for m in merged[:20]:
            m.pop('_popularity', None)
            results.append(m)

        payload = {'results': results}
        _tmdb_match_cache[cache_key] = (time.time(), payload)
        return jsonify(payload)

    except Exception as e:
        logger.error(f"Error in tmdb_match_search: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


# Module-level caches for /api/indexer/latest.
# - results cache: keyed by sweep-mode, each value is {results, fetched_at}.
#   Default "browse" mode is the UI's fast empty-search. Other modes (e.g.
#   "remux2160p") are targeted Prowlarr keyword sweeps used by the 4K
#   auto-grab automation — a single browse misses these when high-volume
#   indexers (IPT) flood out the slower-moving remux uploads.
# - tmdb cache: per (title|year) → tmdb metadata, 24h TTL (titles don't move)
_LATEST_TORRENTS_CACHE: Dict[str, Dict[str, Any]] = {}
_LATEST_TORRENTS_TTL_SEC = 300

# Sweep query sets — defined here so they can be tuned without redeploying
# the daemon module. Each mode maps to a list of Prowlarr keyword searches.
_LATEST_SWEEP_QUERIES = {
    'remux2160p': [
        '2160p REMUX',
        'UHD REMUX',
        '2160p UHD BluRay REMUX',
        'REMUX HDR',
        'REMUX DV',
    ],
}
_LATEST_TMDB_CACHE: Dict[str, Any] = {}  # key → (timestamp, payload)
_LATEST_TMDB_TTL_SEC = 24 * 3600          # successful TMDb lookups
_LATEST_TMDB_NEG_TTL_SEC = 3600           # *negative* (empty) lookups — short
                                          # so transient TMDb hiccups self-heal

# Tracker-name normalization for the Latest sheet — mirrors the explore
# pipeline's mapping so users see compact, consistent indexer labels.
_LATEST_TRACKER_NORMALIZE = {
    'privatehd': 'PHD',
    'beyondhd': 'BHD',
    'beyond-hd': 'BHD',
    'iptorrents': 'IPT',
}

# Language tags that show up in scene/p2p release names. If any of these
# appear as their own word in the torrent name, we treat the release as
# non-English and drop it. 'MULTI' / 'DUAL' usually means a foreign track
# is included alongside English — also worth excluding from Latest.
_FOREIGN_LANG_TAGS = re.compile(
    r'\b(GERMAN|FRENCH|SPANISH|ITALIAN|RUSSIAN|POLISH|TURKISH|DUTCH|DANISH|'
    r'SWEDISH|NORWEGIAN|FINNISH|HINDI|TAMIL|TELUGU|BENGALI|MARATHI|PUNJABI|'
    r'ARABIC|JAPANESE|KOREAN|CHINESE|MANDARIN|CANTONESE|PORTUGUESE|HEBREW|'
    r'CZECH|HUNGARIAN|ROMANIAN|GREEK|UKRAINIAN|BULGARIAN|CROATIAN|SERBIAN|'
    r'SLOVAK|SLOVENIAN|MULTI|MULTi|DUAL|VOSTFR|TRUEFRENCH)\b',
    re.IGNORECASE,
)


def _normalize_latest_tracker(name: str) -> str:
    t = (name or '').strip()
    key = t.lower().replace(' ', '')
    return _LATEST_TRACKER_NORMALIZE.get(key, t)


def latest_releases(params=None) -> list:
    """The /api/indexer/latest results for in-process jobs (auto-grab).

    Runs the view function directly in a synthetic request context, so no
    HTTP request, auth check, Host or loopback trust is involved. Raises
    RuntimeError when the view reports an error.
    """
    with app.test_request_context('/api/indexer/latest', query_string=dict(params or {})):
        rv = indexer_latest()
    status = 200
    if isinstance(rv, tuple):
        rv, status = rv[0], rv[1]
    resp = app.make_response(rv)
    if status >= 400 or resp.status_code >= 400:
        payload = resp.get_json(silent=True) or {}
        raise RuntimeError(payload.get('error') or f'status {max(status, resp.status_code)}')
    return (resp.get_json(silent=True) or {}).get('results') or []


@app.route('/api/indexer/latest')
def indexer_latest():
    """Return the most recently published torrents across all configured
    indexers, lightweight enough for the Latest sheet to open quickly.

    Query params:
        limit  – max results to return (default 200, max 600)
        sweep  – optional named sweep mode. Default is "browse" (single empty
                 search per indexer). Use "remux2160p" to run a targeted
                 keyword sweep that surfaces 2160p Remux uploads even when a
                 high-volume indexer floods the global browse window.

    Each result is enriched with a TMDb poster + tmdb_id when available.
    """
    try:
        import time as _time

        sweep_mode = (request.args.get('sweep') or 'browse').strip().lower()
        sweep_queries = _LATEST_SWEEP_QUERIES.get(sweep_mode)
        # The 4K sweep can produce 1000+ items across indexers, so allow a
        # higher cap when sweep mode is on. UI default stays at 300.
        max_limit = 600 if sweep_queries else 300
        default_limit = 200 if sweep_mode == 'browse' else max_limit
        limit = request.args.get('limit', default_limit, type=int)
        if limit < 1:
            limit = default_limit
        if limit > max_limit:
            limit = max_limit

        now = _time.time()
        cache_bucket = _LATEST_TORRENTS_CACHE.setdefault(sweep_mode, {})
        cached = cache_bucket.get('results')
        cached_at = cache_bucket.get('fetched_at', 0.0)
        cache_fresh = cached is not None and (now - cached_at) < _LATEST_TORRENTS_TTL_SEC

        if cache_fresh:
            results = cached[:limit]
            return jsonify({
                'success': True,
                'results': results,
                'total': len(results),
                'cached': True,
                'age_sec': int(now - cached_at),
                'sweep': sweep_mode,
            })

        client = get_indexer_client(settings)
        if not client:
            return jsonify({'error': 'No indexer configured. Set up Prowlarr or Torznab in settings.'}), 503

        if sweep_queries:
            # Targeted Prowlarr sweep — runs the configured queries in
            # parallel, dedups by (indexer, guid). Slower (5–10s) but
            # comprehensive for low-volume verticals like 2160p Remux.
            raw = client.sweep_movies(
                categories=settings.get('indexer.movie_categories') or '2000',
                queries=sweep_queries,
                max_workers=3,
            )
        else:
            # search_movies issues a single empty browse per indexer — much
            # faster than sweep_movies, and Prowlarr orders newest-first.
            raw = client.search_movies(categories=settings.get('indexer.movie_categories') or '2000', limit=300)

        def _age_days(pub_date_str):
            if not pub_date_str:
                return None
            try:
                from datetime import timezone
                clean = pub_date_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(clean)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = datetime.now(timezone.utc) - dt
                return max(0, delta.days)
            except Exception:
                return None

        def _pub_sort_key(pub_date_str):
            """Negative epoch seconds → newest first when sort=ASC.
            Items without a parseable date sink to the bottom."""
            if not pub_date_str:
                return float('inf')
            try:
                from datetime import timezone
                clean = pub_date_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(clean)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return -dt.timestamp()
            except Exception:
                return float('inf')

        normalized = []
        skipped_foreign = 0
        for item in raw:
            # Foreign-language filter — drop any release tagged with a non-
            # English language token. Operates on the raw torrent name so we
            # catch both naming conventions (`Title GERMAN 1080p` and
            # `[German Turkish] Title`).
            raw_name = item.get('torrent_name', '') or ''
            if _FOREIGN_LANG_TAGS.search(raw_name):
                skipped_foreign += 1
                continue

            resolution = (item.get('resolution', '') or '').upper()
            source = (item.get('source', '') or '').upper()
            if source == 'REMUX' and resolution:
                quality_label = f"{resolution} Remux"
            elif source == 'WEB-DL' and resolution:
                quality_label = f"WEBDL-{resolution}"
            elif source and resolution:
                quality_label = f"{source}-{resolution}"
            elif resolution:
                quality_label = resolution
            elif source:
                quality_label = source
            else:
                quality_label = 'Unknown'

            normalized.append({
                'title': item.get('title', ''),
                'year': item.get('year', ''),
                'torrent_name': item.get('torrent_name', ''),
                'quality': quality_label,
                'resolution': item.get('resolution', ''),
                'source': item.get('source', ''),
                'size_gb': item.get('size_gb', 0),
                'seeders': item.get('seeders', 0),
                'indexer': _normalize_latest_tracker(item.get('tracker', '')),
                'pub_date': item.get('pub_date', ''),
                'age_days': _age_days(item.get('pub_date')),
                'imdb_id': item.get('imdb_id', ''),
                # Identifiers needed to grab this specific release via Radarr.
                # download_url + info_hash let the backend fall back to matching
                # against Radarr's release cache when guids don't line up.
                'guid': item.get('guid', ''),
                'indexer_id': item.get('indexer_id'),
                'info_hash': item.get('info_hash', ''),
                'download_url': item.get('download_url', ''),
            })

        if skipped_foreign:
            logger.info(f"indexer_latest: dropped {skipped_foreign} items tagged as foreign-language")

        # Sort newest-first by full pub_date timestamp (not day granularity).
        normalized.sort(key=lambda x: _pub_sort_key(x.get('pub_date')))

        # ---------- TMDb enrichment (poster + tmdb_id) ----------
        # Three-stage resolver: imdb_id lookup (highest hit rate) → title+year
        # search → cleaned title fallback. Cache by a composite key so repeated
        # fetches across results dedupe correctly.
        tmdb_api_key = settings.get('apis.tmdb.api_key', '')

        def _normalize_imdb(raw) -> str:
            """Coerce Prowlarr's imdb value (sometimes int, sometimes 'tt...',
            sometimes bare digit string) to the tt0000000 form TMDb expects.
            Returns '' if the value is clearly invalid."""
            if raw is None:
                return ''
            s = str(raw).strip()
            if not s:
                return ''
            if s.startswith('tt'):
                return s
            if not s.isdigit():
                return ''
            n = int(s)
            # IDs < 100 are almost always garbage placeholders
            if n < 100:
                return ''
            return f'tt{s.zfill(7)}'

        def _clean_title(raw: str) -> str:
            """Strip noise that confuses TMDb search:
            - Leading bracket tags like '[English Turkish]' (language metadata)
            - 'AKA <alt title>' segments (foreign-language alternates)
            - Trailing parentheticals
            - Multiple internal spaces
            """
            import re as _re
            s = raw or ''
            # Strip leading [...] brackets (one or more)
            while True:
                stripped = _re.sub(r'^\s*\[[^\]]*\]\s*', '', s)
                if stripped == s:
                    break
                s = stripped
            # Cut at first 'AKA' (case-insensitive, word-boundary)
            s = _re.split(r'\s+aka\s+', s, maxsplit=1, flags=_re.IGNORECASE)[0]
            # Drop trailing parentheticals like "(2013)"
            s = _re.sub(r'\s*\([^)]*\)\s*$', '', s)
            # Collapse whitespace
            s = _re.sub(r'\s+', ' ', s).strip()
            return s

        if tmdb_api_key:
            # Build a stable per-item cache key from imdb (preferred) or title|year.
            def _key(item):
                imdb = _normalize_imdb(item.get('imdb_id', ''))
                if imdb:
                    return f"imdb:{imdb}"
                t = _clean_title(item.get('title') or '').lower()
                y = str(item.get('year') or '').strip()
                return f"q:{t}|{y}"

            unique_keys: Dict[str, Dict[str, str]] = {}
            for item in normalized[:limit]:  # only enrich what we'll return
                t = (item.get('title') or '').strip()
                if not t and not item.get('imdb_id'):
                    continue
                k = _key(item)
                if k not in unique_keys:
                    unique_keys[k] = {
                        'title': _clean_title(t),
                        'year': str(item.get('year') or '').strip(),
                        'imdb': _normalize_imdb(item.get('imdb_id', '')),
                    }

            # Drop cache hits from the fetch list. Use a shorter TTL for empty
            # (negative) results so a failed lookup retries after an hour
            # instead of being stuck for a day.
            resolved: Dict[str, Dict[str, Any]] = {}
            to_fetch: List[tuple] = []
            for k, meta in unique_keys.items():
                cached_tmdb = _LATEST_TMDB_CACHE.get(k)
                if cached_tmdb:
                    age = now - cached_tmdb[0]
                    had_hit = bool(cached_tmdb[1].get('tmdb_id'))
                    ttl = _LATEST_TMDB_TTL_SEC if had_hit else _LATEST_TMDB_NEG_TTL_SEC
                    if age < ttl:
                        resolved[k] = cached_tmdb[1]
                        continue
                to_fetch.append((k, meta['title'], meta['year'], meta['imdb']))

            def _resolve_tmdb(args):
                k, title, year, imdb = args
                payload = None

                # Strategy 1 — imdb lookup. Deterministic; handles every release
                # with a valid imdbId regardless of how mangled the title is.
                if imdb:
                    try:
                        resp = requests.get(
                            f'https://api.themoviedb.org/3/find/{imdb}',
                            params={'api_key': tmdb_api_key, 'external_source': 'imdb_id'},
                            timeout=5,
                        )
                        if resp.status_code == 200:
                            data = resp.json() or {}
                            mr = data.get('movie_results') or []
                            tr = data.get('tv_results') or []
                            if mr:
                                top = mr[0]
                                payload = {
                                    'tmdb_id': top.get('id'),
                                    'poster_path': top.get('poster_path'),
                                    'media_type': 'movie',
                                    'tmdb_title': top.get('title'),
                                    'tmdb_year': (top.get('release_date') or '')[:4],
                                    'original_language': top.get('original_language'),
                                }
                            elif tr:
                                top = tr[0]
                                payload = {
                                    'tmdb_id': top.get('id'),
                                    'poster_path': top.get('poster_path'),
                                    'media_type': 'tv',
                                    'tmdb_title': top.get('name'),
                                    'tmdb_year': (top.get('first_air_date') or '')[:4],
                                    'original_language': top.get('original_language'),
                                }
                    except Exception:
                        pass

                # Strategy 2 — cleaned title + year search.
                if not payload and title:
                    try:
                        params = {'api_key': tmdb_api_key, 'query': title, 'include_adult': 'false'}
                        if year and year.isdigit():
                            params['year'] = year
                        resp = requests.get(
                            'https://api.themoviedb.org/3/search/movie',
                            params=params,
                            timeout=5,
                        )
                        if resp.status_code == 200:
                            results = resp.json().get('results') or []
                            if results:
                                top = results[0]
                                payload = {
                                    'tmdb_id': top.get('id'),
                                    'poster_path': top.get('poster_path'),
                                    'media_type': 'movie',
                                    'tmdb_title': top.get('title'),
                                    'tmdb_year': (top.get('release_date') or '')[:4],
                                    'original_language': top.get('original_language'),
                                }
                    except Exception:
                        pass

                # Strategy 3 — title-only (drop the year). Catches releases where
                # the year on the torrent doesn't match TMDb's release_date (e.g.
                # late-festival releases dated the following year).
                if not payload and title and year:
                    try:
                        resp = requests.get(
                            'https://api.themoviedb.org/3/search/movie',
                            params={'api_key': tmdb_api_key, 'query': title, 'include_adult': 'false'},
                            timeout=5,
                        )
                        if resp.status_code == 200:
                            results = resp.json().get('results') or []
                            if results:
                                top = results[0]
                                payload = {
                                    'tmdb_id': top.get('id'),
                                    'poster_path': top.get('poster_path'),
                                    'media_type': 'movie',
                                    'tmdb_title': top.get('title'),
                                    'tmdb_year': (top.get('release_date') or '')[:4],
                                    'original_language': top.get('original_language'),
                                }
                    except Exception:
                        pass

                if not payload:
                    payload = {'tmdb_id': None, 'poster_path': None, 'media_type': None,
                               'tmdb_title': None, 'tmdb_year': None,
                               'original_language': None}
                _LATEST_TMDB_CACHE[k] = (now, payload)
                return k, payload

            if to_fetch:
                # 12 workers vs 8 — three sequential API calls per miss makes
                # the wall-clock dominated by the slowest item, so widen the pool.
                with ThreadPoolExecutor(max_workers=12) as pool:
                    for k, payload in pool.map(_resolve_tmdb, to_fetch):
                        resolved[k] = payload

            # Drop items whose resolved TMDb entry isn't English. We keep
            # unmatched items (no TMDb data) because we can't classify them —
            # they fall through to the gradient placeholder.
            filtered_normalized = []
            skipped_lang = 0
            for item in normalized:
                k = _key(item)
                meta = resolved.get(k)
                if meta:
                    olang = (meta.get('original_language') or '').lower()
                    if meta.get('tmdb_id') and olang and olang != 'en':
                        skipped_lang += 1
                        continue
                    item['tmdb_id'] = meta.get('tmdb_id')
                    item['poster_path'] = meta.get('poster_path')
                    item['media_type'] = meta.get('media_type')
                    if meta.get('tmdb_title'):
                        item['display_title'] = meta.get('tmdb_title')
                else:
                    item['tmdb_id'] = None
                    item['poster_path'] = None
                    item['media_type'] = None
                filtered_normalized.append(item)
            if skipped_lang:
                logger.info(f"indexer_latest: dropped {skipped_lang} non-English items by TMDb original_language")
            normalized = filtered_normalized

        cache_bucket['results'] = normalized
        cache_bucket['fetched_at'] = now

        results = normalized[:limit]
        return jsonify({
            'success': True,
            'results': results,
            'total': len(results),
            'cached': False,
            'sweep': sweep_mode,
        })

    except Exception as e:
        logger.error(f"Error in indexer_latest: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/indexer/grab', methods=['POST'])
def indexer_grab():
    """Grab a specific release surfaced by the Latest sheet.

    Flow:
      1. If a tmdb_id is supplied and the movie isn't yet in Radarr, add it
         unmonitored so Radarr has a movie record to associate with.
      2. Call Radarr's release grab with the supplied guid + indexerId.

    Body params:
        guid        – release GUID (from /api/indexer/latest)
        indexer_id  – Prowlarr/Radarr indexer ID
        tmdb_id     – optional, used to add-if-missing
        title, year – fallback metadata if Radarr add is needed
    """
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr is not configured', 'needs_setup': 'radarr'}), 503

        data = request.get_json() or {}
        guid = data.get('guid')
        indexer_id = data.get('indexer_id')
        tmdb_id = data.get('tmdb_id')
        title = data.get('title') or ''
        year = data.get('year')

        if not guid or indexer_id is None:
            return jsonify({'error': 'guid and indexer_id are required'}), 400

        movie_id = None

        # If we know the tmdb_id, make sure Radarr has the movie. Otherwise
        # the release grab works but Radarr can't import the resulting file.
        if tmdb_id:
            try:
                tmdb_id_int = int(tmdb_id)
                movie = radarr.get_movie_by_tmdb_id(tmdb_id_int)
                if not movie:
                    profiles = radarr.get_quality_profiles()
                    root_folders = radarr.get_root_folders()
                    if not profiles or not root_folders:
                        return jsonify({'error': 'Radarr has no quality profiles or root folders'}), 500
                    # Use the configured default profile (never silently Radarr's "Any")
                    profile_id = _resolve_radarr_profile_id(profiles)
                    year_int = None
                    try:
                        if year:
                            year_int = int(str(year))
                    except (ValueError, TypeError):
                        year_int = None
                    movie = radarr.add_movie(
                        tmdb_id=tmdb_id_int,
                        title=title or f'Movie {tmdb_id_int}',
                        year=year_int or datetime.now().year,
                        quality_profile_id=profile_id,
                        root_folder_path=root_folders[0]['path'],
                        # User clicked Download — they want this movie. Add as
                        # monitored, otherwise Radarr rejects any grab with
                        # "Movie is not monitored".
                        monitored=True,
                        search_for_movie=False,
                    )
                    if not movie:
                        return jsonify({'error': 'Failed to add movie to Radarr'}), 500
                elif not movie.get('monitored'):
                    # Movie exists in Radarr but is unmonitored (e.g. added by
                    # an earlier Sources/Latest flow). Flip it to monitored so
                    # Radarr will accept the grab.
                    movie['monitored'] = True
                    updated = radarr.update_movie(movie)
                    if updated:
                        movie = updated
                        logger.info(f"indexer_grab: flipped movie tmdb_id={tmdb_id_int} to monitored")
                movie_id = movie.get('id')
            except Exception as e:
                logger.warning(f"indexer_grab: tmdb lookup/add failed (proceeding without movie_id): {e}")

        # Radarr's grab-by-guid requires the release to be in its release
        # cache. The cache only fills when Radarr itself does an interactive
        # search — so trigger one for this movie. Radarr queries the same
        # Prowlarr instance, so the guids should match what we showed the user.
        radarr_releases: List[Dict[str, Any]] = []
        if movie_id is not None:
            try:
                radarr_releases = radarr.search_movie_releases(movie_id) or []
                logger.info(f"indexer_grab: populated Radarr cache with {len(radarr_releases)} releases for movie_id={movie_id}")
            except Exception as e:
                logger.warning(f"indexer_grab: search to populate cache failed: {e}")

        result = radarr.grab_release(guid=guid, indexer_id=indexer_id, movie_id=movie_id)

        # First-pass failed (cache miss). Try matching by infoHash, download
        # URL, or torrent name against what Radarr just returned — Radarr
        # sometimes assigns its own guid scheme rather than passing Prowlarr's
        # through. We try several normalizations because Radarr can rewrite
        # download URLs and slightly normalize titles.
        if not result.get('ok') and radarr_releases:
            info_hash = (data.get('info_hash') or '').strip().lower()
            download_url = (data.get('download_url') or '').strip()
            tn = (data.get('torrent_name') or '').strip()
            tn_lower = tn.lower()

            def _alnum(s: str) -> str:
                """Strip everything but alphanumerics, lowercase. Survives the
                punctuation/whitespace normalization Radarr sometimes applies."""
                return ''.join(ch for ch in (s or '').lower() if ch.isalnum())
            tn_alnum = _alnum(tn)

            match = None

            # 1. info_hash — exact, intrinsic to the torrent file.
            for rel in radarr_releases:
                rel_hash = (rel.get('infoHash') or '').strip().lower()
                if info_hash and rel_hash and rel_hash == info_hash:
                    match = rel
                    break

            # 2. download_url — exact match.
            if not match and download_url:
                for rel in radarr_releases:
                    if (rel.get('downloadUrl') or '') == download_url:
                        match = rel
                        break

            # 3. download_url — substring match (Radarr sometimes wraps the URL
            # with its own proxy prefix, e.g. /api/v3/indexer/.../proxy/...).
            if not match and download_url:
                for rel in radarr_releases:
                    rdu = rel.get('downloadUrl') or ''
                    if download_url and (download_url in rdu or rdu in download_url):
                        match = rel
                        break

            # 4. torrent_name — exact (lowercased).
            if not match and tn_lower:
                for rel in radarr_releases:
                    if (rel.get('title') or '').strip().lower() == tn_lower:
                        match = rel
                        break

            # 5. torrent_name — alphanumeric-only (collapses dots, dashes,
            # underscores, spaces. Catches `Name.With.Dots-GROUP` vs
            # `Name With Dots-GROUP`).
            if not match and tn_alnum:
                for rel in radarr_releases:
                    if _alnum(rel.get('title') or '') == tn_alnum:
                        match = rel
                        break

            if match:
                logger.info(f"indexer_grab: retrying with Radarr's guid for matching release")
                result = radarr.grab_release(
                    guid=match.get('guid'),
                    indexer_id=match.get('indexerId'),
                    movie_id=movie_id,
                )

        # Final fallback — push the release directly into Radarr. This handles
        # the case where Radarr's release cache simply doesn't contain our
        # release (e.g. Radarr's indexers ≠ Prowlarr's indexers). Radarr will
        # still apply quality-profile filtering, so a "rejected" outcome is
        # legitimate — surface it as the error in that case.
        push_dl = (data.get('download_url') or '').strip()
        if not result.get('ok') and push_dl:
            logger.info(f"indexer_grab: falling back to push_release for movie_id={movie_id}")
            push_payload = {
                'title': data.get('torrent_name') or data.get('title') or '',
                'downloadUrl': push_dl,
                'protocol': 'torrent',
                'publishDate': data.get('pub_date') or datetime.utcnow().isoformat() + 'Z',
                'indexer': data.get('indexer') or 'Prowlarr',
            }
            ih = (data.get('info_hash') or '').strip()
            if ih:
                push_payload['infoHash'] = ih
            size_gb = data.get('size_gb')
            if size_gb:
                try:
                    push_payload['size'] = int(float(size_gb) * (1024 ** 3))
                except (ValueError, TypeError):
                    pass
            # NOT indexerId: that field is a *Radarr* indexer id, but the id we
            # have is Prowlarr's, and the two are unrelated numbering spaces.
            # They collided here — Prowlarr 3 = IPTorrents, Radarr 3 = PHD — so
            # every push was filed under PHD, and the night PHD went into
            # failure backoff Radarr stopped fetching the torrents and parked
            # them in PendingReleases instead. The `indexer` name above is what
            # shows in Radarr's history; omitting the id makes Radarr fetch
            # downloadUrl directly, which is what we want for a Prowlarr link.

            push_result = radarr.push_release(push_payload)
            if push_result.get('ok'):
                result = push_result
            else:
                # Push failed — log diagnostics and forward the rejection
                # reasons so the user sees something actionable.
                logger.warning(
                    f"indexer_grab: push_release also failed for movie_id={movie_id}. "
                    f"rejections={push_result.get('rejections')} "
                    f"error={push_result.get('error')!r}"
                )
                logger.warning(
                    f"  payload: title={push_payload['title']!r} indexer={push_payload['indexer']!r}"
                )
                result = push_result

        if result.get('ok'):
            # User-initiated release grab from the Latest sheet → "Manual".
            _record_origin('manual', guid=guid or '', info_hash=data.get('info_hash') or '',
                           download_url=data.get('download_url') or '', tmdb_id=tmdb_id,
                           media_type='movie', title=title)
            try:
                _record_manual_grab(tmdb_id, title, 'movie')
            except Exception as exc:
                logger.warning(f"Manual-grab record failed: {exc}")
            return jsonify({'success': True, 'movie_id': movie_id})

        # Build a user-readable error
        err = result.get('error') or 'Radarr could not grab this release'
        rejections = result.get('rejections')
        if rejections:
            err = f"Radarr rejected this release: {'; '.join(rejections)}"
        return jsonify({'error': err}), 500

    except Exception as e:
        logger.error(f"Error in indexer_grab: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/radarr/movie/grab', methods=['POST'])
def radarr_movie_grab():
    """Grab a specific release via Radarr."""
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr is not configured', 'needs_setup': 'radarr'}), 503

        data = request.get_json()
        if not data:
            return jsonify({'error': 'Missing request body'}), 400
        guid = data.get('guid')
        indexer_id = data.get('indexer_id')
        movie_id = data.get('movie_id')

        if not guid or indexer_id is None:
            return jsonify({'error': 'guid and indexer_id are required'}), 400

        logger.info(f"Grabbing release: guid={guid}, indexer_id={indexer_id}, movie_id={movie_id}")
        result = radarr.grab_release(guid=guid, indexer_id=indexer_id, movie_id=movie_id)

        if result.get('ok'):
            # This endpoint is only hit from the Sources panel → user-initiated.
            # Record it so the activity log can badge it "Manual" with certainty.
            try:
                tmdb_id = data.get('tmdb_id')
                title = data.get('title', '')
                if not tmdb_id and movie_id:
                    mv = radarr.get_movie(movie_id) or {}
                    tmdb_id = mv.get('tmdbId')
                    title = title or mv.get('title', '')
                _record_manual_grab(tmdb_id, title, 'movie')
                _record_origin('manual', guid=guid, info_hash=data.get('info_hash') or '',
                               download_url=data.get('download_url') or '', tmdb_id=tmdb_id,
                               media_type='movie', title=title)
            except Exception as exc:
                logger.warning(f"Manual-grab record failed: {exc}")
            return jsonify({'success': True, 'message': 'Release sent to download client'})
        else:
            error_msg = result.get('error', 'Radarr failed to grab release')
            logger.error(f"Grab failed for guid={guid}: {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 502

    except Exception as e:
        logger.error(f"Error grabbing release via Radarr: {e}")
        return jsonify({'error': str(e)}), 500

# Cache qBit torrent data to avoid hammering qBit on rapid 1s polls
import threading
_qbit_cache = {'data': {}, 'ts': 0}
_qbit_cache_lock = threading.Lock()
# Cache movie metadata (title/year/poster) by hash so it persists after
# tracked_downloads is cleared at end of copy
_dl_meta_cache = {}  # lowercase hash → {title, year, tmdb_id, poster_path, _ts}
_dl_meta_cache_lock = threading.Lock()
_DL_META_CACHE_MAX = 500

@app.route('/api/radarr/queue')
def radarr_queue():
    """Get active Radarr downloads with real-time progress from qBittorrent."""
    try:
        import requests as req

        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'downloads': []})

        queue = radarr.get_queue(include_movie=True)

        # Fetch real-time progress from qBittorrent (cached for 2s to
        # avoid hammering qBit on rapid 1-second polls)
        now = time.time()
        with _qbit_cache_lock:
            if now - _qbit_cache['ts'] < 2 and _qbit_cache['data']:
                qbit_progress = _qbit_cache['data']
            else:
                qbit_progress = None  # signal to fetch outside lock

        if qbit_progress is None:
            qbit_progress = {}
            try:
                qbit_url = settings.get('qbittorrent.url')
                if qbit_url:
                    qs = req.Session()
                    lr = qs.post(f"{qbit_url}/api/v2/auth/login", data={
                        'username': settings.get('qbittorrent.username'),
                        'password': settings.get('qbittorrent.password'),
                    }, timeout=8)
                    if lr.status_code == 200 and lr.text.strip() == 'Ok.':
                        tr = qs.get(f"{qbit_url}/api/v2/torrents/info", timeout=8)
                        if tr.status_code == 200:
                            for t in tr.json():
                                h = t.get('hash', '').lower()
                                if h:
                                    eta_secs = t.get('eta', 0)
                                    if eta_secs and eta_secs < 8640000:
                                        h_val = eta_secs // 3600
                                        m_val = (eta_secs % 3600) // 60
                                        eta_str = f"{h_val}h {m_val}m" if h_val > 0 else f"{m_val}m" if m_val > 0 else "<1m"
                                    else:
                                        eta_str = None
                                    qbit_progress[h] = {
                                        'progress': t.get('progress', 0),
                                        'eta': eta_str,
                                        'size': t.get('total_size', 0),
                                        'remaining': t.get('amount_left', 0),
                                        'state': t.get('state', ''),
                                        'tracker': _normalize_tracker_url(t.get('tracker', '')),
                                    }
            except Exception:
                pass
            with _qbit_cache_lock:
                _qbit_cache['data'] = qbit_progress
                _qbit_cache['ts'] = time.time()
                if not qbit_progress and _qbit_cache['data']:
                    qbit_progress = _qbit_cache['data']

        # Get copy progress from auto_renamer (read ONCE, reuse below)
        copy_data = {}   # hash (original case) → {progress: 0-100, size, ...}
        copy_lookup = {}  # lowercase hash → copy_data value
        tracked = {}
        try:
            from app.auto_rename import auto_renamer
            if auto_renamer:
                copy_data = auto_renamer.get_copy_progress()
                tracked = auto_renamer.get_tracking_data()
                for k, v in copy_data.items():
                    copy_lookup[k.lower()] = v
        except Exception:
            pass

        downloads = []
        seen_hashes = set()
        for item in queue:
            movie = item.get('movie') or {}
            dl_hash = (item.get('downloadId') or '').lower()

            # Use qBit real-time progress if available, else Radarr's
            qb = qbit_progress.get(dl_hash)
            if qb:
                progress = qb['progress']
                eta = qb['eta']
                size = qb['size']
                sizeleft = qb['remaining']
                qb_done = progress >= 1.0
            else:
                size = item.get('size', 0) or 0
                sizeleft = item.get('sizeleft', 0) or 0
                progress = (size - sizeleft) / size if size > 0 else 0
                qb_done = progress >= 1.0
                # Parse timeleft "HH:MM:SS" into human-readable
                eta = None
                timeleft = item.get('timeleft')
                if timeleft:
                    try:
                        parts = timeleft.split(':')
                        h, m = int(parts[0]), int(parts[1])
                        if h > 0:
                            eta = f"{h}h {m}m"
                        elif m > 0:
                            eta = f"{m}m"
                        else:
                            eta = "<1m"
                    except (ValueError, IndexError):
                        eta = timeleft

            # Derive display status from qBit state (Radarr often reports
            # 'warning'/stalled even while qBit is actively downloading)
            cp = copy_lookup.get(dl_hash) if dl_hash else None
            if qb_done:
                if cp and cp.get('progress', 100) < 100:
                    status = 'copying'
                    progress = cp['progress'] / 100
                    size = cp.get('size', size)
                    eta = None
                else:
                    status = 'importing'
                    progress = 1.0
                    eta = None
            elif qb:
                # qBit has this torrent and it's still downloading —
                # always show "downloading" regardless of Radarr status
                status = 'downloading'
            else:
                status = item.get('status', 'unknown')

            # Extract poster path from movie images
            poster_path = None
            for img in movie.get('images', []):
                if img.get('coverType') == 'poster':
                    remote = img.get('remoteUrl', '')
                    if '/t/p/' in remote:
                        poster_path = '/' + remote.split('/t/p/')[1].split('/', 1)[-1]
                    break

            if dl_hash:
                seen_hashes.add(dl_hash)

            dl_title = movie.get('title', item.get('title', ''))
            dl_year = movie.get('year', 0)
            dl_tmdb = movie.get('tmdbId')

            # Cache metadata so it persists after tracked_downloads is cleared
            if dl_hash and dl_title:
                with _dl_meta_cache_lock:
                    _dl_meta_cache[dl_hash] = {
                        'title': dl_title, 'year': dl_year,
                        'tmdb_id': dl_tmdb, 'poster_path': poster_path,
                    }

            downloads.append({
                'title': dl_title,
                'year': dl_year,
                'tmdb_id': dl_tmdb,
                'poster_path': poster_path,
                'progress': round(progress, 3),
                'size_total': size,
                'size_remaining': sizeleft if status not in ('copying', 'importing') else 0,
                'eta': eta,
                'status': status,
                'queue_id': item.get('id'),
                'tracker': qb['tracker'] if qb else None,
            })

        # Also cache metadata from tracked_downloads (before they get cleared)
        for t_hash, t_info in tracked.items():
            h = t_hash.lower()
            with _dl_meta_cache_lock:
                if h not in _dl_meta_cache and t_info.title:
                    # Evict oldest entries if over cap
                    if len(_dl_meta_cache) >= _DL_META_CACHE_MAX:
                        sorted_keys = sorted(_dl_meta_cache, key=lambda k: _dl_meta_cache[k].get('_ts', 0))
                        for k in sorted_keys[:len(_dl_meta_cache) - _DL_META_CACHE_MAX + 1]:
                            del _dl_meta_cache[k]
                    _dl_meta_cache[h] = {
                        'title': t_info.title,
                        'year': int(t_info.year) if t_info.year and str(t_info.year).isdigit() else 0,
                        'tmdb_id': t_info.tmdb_id,
                        'poster_path': None,
                        '_ts': time.time(),
                    }

        def _resolve_meta(h):
            """Look up cached metadata for a hash, return (title, year, tmdb_id, poster)."""
            # 1. In-memory meta cache (survives tracked_downloads clearing)
            with _dl_meta_cache_lock:
                meta = _dl_meta_cache.get(h)
            if meta:
                return meta['title'], meta['year'], meta['tmdb_id'], meta['poster_path']
            # 2. tracked_downloads
            info = tracked.get(h) or tracked.get(h.upper())
            if info:
                return info.title, int(info.year) if info.year and str(info.year).isdigit() else 0, info.tmdb_id, None
            return None, 0, None, None

        # Add copy-in-progress items that already left Radarr queue
        for cp_hash, cp_info in copy_data.items():
            h = cp_hash.lower()
            if h in seen_hashes or cp_info.get('progress', 100) >= 100:
                continue
            title, year, tmdb_id, poster = _resolve_meta(h)
            if not title:
                title = cp_info.get('torrent_name', 'Unknown')
            downloads.append({
                'title': title,
                'year': year,
                'tmdb_id': tmdb_id,
                'poster_path': poster,
                'progress': round(cp_info.get('progress', 0) / 100, 3),
                'size_total': cp_info.get('size', 0),
                'size_remaining': 0,
                'eta': None,
                'status': 'copying',
                'queue_id': None,
            })

        # Show tracked items that left Radarr queue but copy hasn't started
        # yet → "Processing..." bridges the gap until copy_progress appears.
        # Skip if qBit says download isn't done yet (still downloading).
        copy_hashes = {k.lower() for k in copy_data}
        for t_hash, t_info in tracked.items():
            h = t_hash.lower()
            if h in seen_hashes or h in copy_hashes:
                continue
            qb = qbit_progress.get(h)
            # Only show if qBit confirms download is complete
            if not qb or qb.get('progress', 0) < 1.0:
                continue
            with _dl_meta_cache_lock:
                _poster = (_dl_meta_cache.get(h) or {}).get('poster_path')
            downloads.append({
                'title': t_info.title,
                'year': int(t_info.year) if t_info.year and str(t_info.year).isdigit() else 0,
                'tmdb_id': t_info.tmdb_id,
                'poster_path': _poster,
                'progress': 1.0,
                'size_total': qb.get('size', 0) if qb else 0,
                'size_remaining': 0,
                'eta': None,
                'status': 'importing',
                'queue_id': None,
            })

        return jsonify({'downloads': downloads})

    except Exception as e:
        logger.error(f"Error fetching Radarr queue: {e}")
        return jsonify({'downloads': []})

@app.route('/api/watchlist', methods=['GET'])
def get_watchlist():
    """Get all monitored movies in Radarr that haven't been downloaded yet."""
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured', 'needs_setup': 'radarr', 'watchlist': []}), 400
        movies = radarr.get_movies()

        # Cache quality profiles to avoid repeated calls
        quality_profiles = radarr.get_quality_profiles()

        # Transform to match our watchlist format, filtering out downloaded movies
        watchlist = []
        for movie in movies:
            # Skip unmonitored movies — watchlist only shows monitored items
            if not movie.get('monitored'):
                continue

            # Skip movies that Radarr confirms have been downloaded
            if movie.get('hasFile'):
                continue

            tmdb_id = movie.get('tmdbId')

            watchlist.append({
                'tmdb_id': tmdb_id,
                'radarr_id': movie.get('id'),
                'title': movie.get('title'),
                'year': movie.get('year'),
                'poster_path': (images[0].get('remoteUrl', '').replace('https://image.tmdb.org/t/p/original', '') if (images := movie.get('images')) else ''),
                'quality_profile': movie.get('qualityProfileId'),
                'quality_profile_name': next((p.get('name') for p in quality_profiles if p.get('id') == movie.get('qualityProfileId')), ''),
                'monitored': movie.get('monitored'),
                'has_file': movie.get('hasFile'),
                'downloaded': movie.get('hasFile'),
                'path': movie.get('path'),
                'added_date': movie.get('added')
            })

        # Enrich with US digital (TMDb type-4) dates; cached daily under data/.
        try:
            from app.digital_release_cache import enrich_watchlist_items
            enrich_watchlist_items(watchlist)
        except Exception as enrich_err:
            logger.warning(f"digital release enrich failed: {enrich_err}")
            for m in watchlist:
                m.setdefault('digital_release_date', None)
                m.setdefault('digital_release_label', None)

        # Soonest US digital first; missing dates last (stable title tiebreak).
        watchlist.sort(key=lambda m: (
            m.get('digital_release_date') or '9999-99-99',
            (m.get('title') or '').lower(),
        ))

        return jsonify(watchlist)
    except Exception as e:
        logger.error(f"Error fetching Radarr watchlist: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

_tv_watchlist_cache = {'ts': 0.0, 'data': None}
_TV_WATCHLIST_TTL = 120  # 2 minutes — matches the Sonarr watchlist-IDs cache


def _sonarr_series_poster(series: dict) -> str:
    """Best poster URL for a Sonarr series record.

    Sonarr embeds TMDb or TVDB artwork in `images[].remoteUrl`. TMDb URLs get
    trimmed back to a bare poster_path so the frontend's posterUrl() can pick
    the size it needs; anything else is passed through absolute.
    """
    for img in (series.get('images') or []):
        if img.get('coverType') != 'poster':
            continue
        remote = img.get('remoteUrl') or img.get('url') or ''
        if 'image.tmdb.org' in remote:
            idx = remote.find('/t/p/')
            if idx != -1:
                # .../t/p/<size>/abc.jpg → /abc.jpg
                tail = remote[idx + 5:]
                slash = tail.find('/')
                if slash != -1:
                    return tail[slash:]
        return remote
    return ''


@app.route('/api/watchlist/tv', methods=['GET'])
def get_tv_watchlist():
    """Shows we're waiting on — the TV mirror of the movie watchlist.

    Same semantics as /api/watchlist: monitored, don't have it, waiting for it.
    For TV that means series that haven't aired at all yet, which Sonarr marks
    `status == 'upcoming'`. Shows already airing are deliberately excluded even
    when episodes are missing — chasing missing episodes of a running show is
    Activity's job, not the watchlist's.

    Filtering on `statistics.episodeCount == 0` instead would drag in long-dead
    series with quirky metadata (no aired episodes recorded despite a premiere
    decades ago), so status is the signal.
    """
    cached = _tv_watchlist_cache
    if cached['data'] is not None and time.time() - cached['ts'] < _TV_WATCHLIST_TTL:
        return jsonify(cached['data'])

    try:
        sonarr = get_sonarr_for_request()
        if not sonarr or not sonarr.enabled:
            return jsonify({'enabled': False, 'series': []})

        all_series = sonarr.get_series() or []
        profiles = sonarr.get_quality_profiles() or []
        profile_names = {p.get('id'): p.get('name') for p in profiles}

        rows = []
        for s in all_series:
            if not s.get('monitored') or s.get('status') != 'upcoming':
                continue

            stats = s.get('statistics') or {}
            rows.append({
                'sonarr_id': s.get('id'),
                # Sonarr reports 0 when a series has no TMDb match — send null so
                # the client can render the row as non-tappable instead of
                # opening a modal for tmdb id 0.
                'tmdb_id': s.get('tmdbId') or None,
                'tvdb_id': s.get('tvdbId'),
                'title': s.get('title') or '',
                'year': s.get('year'),
                'poster_path': _sonarr_series_poster(s),
                # nextAiring is a precise UTC timestamp; firstAired is date-only
                # and can land a day off once localized. Prefer the former.
                'premiere_date': s.get('nextAiring') or s.get('firstAired'),
                'episode_order': stats.get('totalEpisodeCount') or 0,
                'quality_profile_name': profile_names.get(s.get('qualityProfileId')) or '',
                'added_date': s.get('added'),
            })

        # Soonest premiere first; undated ("date TBA") sinks to the bottom.
        rows.sort(key=lambda r: (r['premiere_date'] or '9999', r['title'].lower()))

        payload = {'enabled': True, 'series': rows}
        _tv_watchlist_cache.update({'ts': time.time(), 'data': payload})
        return jsonify(payload)

    except Exception as e:
        logger.error(f"Error fetching Sonarr TV watchlist: {e}")
        return jsonify({'enabled': True, 'series': [], 'error': str(e)}), 200


@app.route('/api/canon/build', methods=['POST'])
def canon_build():
    """Resolve the curated canon seed against TMDb and (re)write canon.db."""
    from app import canon_index
    api_key = settings.get('apis.tmdb.api_key') or Config.TMDB_API_KEY
    if not api_key:
        return jsonify({'error': 'TMDb API key not configured', 'needs_setup': 'tmdb'}), 400
    force = bool((request.get_json(silent=True) or {}).get('force'))
    try:
        return jsonify(canon_index.build_index(api_key, force=force))
    except Exception as e:
        logger.error(f'Canon build failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/canon')
def canon_list():
    """Ranked canon with ownership annotation. `missing=1` filters to gaps."""
    from app import canon_index
    limit = request.args.get('limit', default=500, type=int)
    films = canon_index.get_canon(limit=limit)
    if request.args.get('missing') == '1':
        films = [f for f in films if not f['owned']]
    if request.args.get('below_remux') == '1':
        films = [f for f in films if 0 < f['owned_tier'] < 3]
    return jsonify({'stats': canon_index.get_stats(), 'films': films})


@app.route('/api/movie-night/trackers')
def movie_night_trackers():
    """Trackers the Movie Night picker may offer — the movie trackers only.

    `selectable` is false for Torznab, which exposes one aggregate feed with no
    per-tracker addressing; the UI hides the picker in that case.
    """
    from app.indexer_api import get_indexer_client
    from app import movie_night
    client = get_indexer_client(settings)
    if not client:
        return jsonify({'trackers': [], 'selectable': False,
                        'error': 'No indexer configured'})
    trackers = movie_night.eligible_trackers(client)
    return jsonify({'trackers': trackers, 'selectable': bool(trackers)})


@app.route('/api/movie-night/preview')
def movie_night_preview():
    """Dry run — what the next weekly run would grab. Grabs nothing."""
    from app import movie_night
    try:
        return jsonify(movie_night.preview())
    except Exception as e:
        logger.error(f'Movie Night preview failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/movie-night/status')
def movie_night_status():
    from app import movie_night
    return jsonify({'status': movie_night.get_status(), 'log': movie_night.get_log()})


@app.route('/api/movie-night/run', methods=['POST'])
def movie_night_run():
    """Fire a run now. Defaults to dry_run — pass {"dry_run": false} to grab."""
    from app import movie_night
    dry = (request.get_json(silent=True) or {}).get('dry_run', True)
    movie_night.trigger_manual_run(dry_run=bool(dry))
    return jsonify({'ok': True, 'dry_run': bool(dry)})


def _taste_account_id() -> int:
    """Plex accountID whose viewing drives the rate prompts.

    App users and Plex accounts are separate identity spaces with no link in the
    user table, so this is configured rather than derived. Phase 1 is the owner
    only (Plex accountID 1); multi-user needs a real per-user mapping, not a
    coincidence that both happen to be 1.
    """
    try:
        return int(settings.get('automation.taste.plex_account_id', 1) or 1)
    except (TypeError, ValueError):
        return 1


def _taste_push_user_id() -> int:
    """App user id whose push subscriptions receive the prompts."""
    try:
        return int(settings.get('automation.taste.push_user_id', 1) or 1)
    except (TypeError, ValueError):
        return 1


@app.route('/api/taste/pending')
def taste_pending():
    """What to prompt about.

    `latest` is the one the UI acts on — the newest watch, or null if it has
    already been answered. `pending` is the fuller unrated list, kept for
    diagnostics only; prompting from it would let an ignored question stack up
    behind newer ones.
    """
    from app import taste
    hours = request.args.get('hours', default=24, type=int)
    account_id = _taste_account_id()
    try:
        return jsonify({
            'account_id': account_id,
            'window_hours': hours,
            'latest': taste.latest_unrated(account_id, hours, settings),
            'pending': taste.pending(account_id, hours, settings),
        })
    except Exception as e:
        logger.error(f'Taste pending failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/taste/film/<int:tmdb_id>')
def taste_film(tmdb_id):
    """Everything the rate screen needs: the film, any existing verdict, and
    — only once a positive verdict exists — the candidate list."""
    from app import taste
    account_id = _taste_account_id()
    try:
        tmdb = taste.film_brief(tmdb_id, settings)
        verdict = taste.get_verdict(account_id, tmdb_id)
        payload = {
            'tmdb_id': tmdb_id,
            'film': tmdb,
            'verdict': verdict,
            'suggestions': [],
        }
        # Candidates are fetched lazily: a couple of TMDb round trips are wasted
        # work until we know the verdict was positive.
        if verdict == 'liked':
            rec = taste.recommend([tmdb_id], account_id=account_id,
                                  settings=settings)
            payload['suggestions'] = rec.get('results', [])
            payload['pool'] = rec.get('pool')
        return jsonify(payload)
    except Exception as e:
        logger.error(f'Taste film failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/taste/rate', methods=['POST'])
def taste_rate():
    """Record a verdict. Returns candidates immediately when positive."""
    from app import taste
    body = request.get_json(silent=True) or {}
    tmdb_id = body.get('tmdb_id')
    verdict = (body.get('verdict') or '').strip().lower()
    if not tmdb_id:
        return jsonify({'error': 'tmdb_id required'}), 400
    account_id = _taste_account_id()
    try:
        res = taste.record_verdict(
            account_id, int(tmdb_id), verdict,
            title=body.get('title') or '', year=body.get('year'),
            watched_at=body.get('watched_at'))
        if res.get('error'):
            return jsonify(res), 400
        if verdict == 'liked':
            rec = taste.recommend([int(tmdb_id)], account_id=account_id,
                                  settings=settings)
            res['suggestions'] = rec.get('results', [])
            res['pool'] = rec.get('pool')
        else:
            res['suggestions'] = []
        return jsonify(res)
    except Exception as e:
        logger.error(f'Taste rate failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/taste/grab', methods=['POST'])
def taste_grab():
    """Acquire one suggested film."""
    from app import taste
    body = request.get_json(silent=True) or {}
    tmdb_id = body.get('tmdb_id')
    if not tmdb_id:
        return jsonify({'error': 'tmdb_id required'}), 400
    try:
        return jsonify(taste.grab(
            int(tmdb_id), body.get('title') or '', body.get('year'),
            seed_tmdb_id=body.get('seed_tmdb_id'),
            account_id=_taste_account_id(), settings=settings))
    except Exception as e:
        logger.error(f'Taste grab failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/taste/dismiss', methods=['POST'])
def taste_dismiss():
    """Stop asking about this film without recording a verdict."""
    from app import taste
    body = request.get_json(silent=True) or {}
    tmdb_id = body.get('tmdb_id')
    if not tmdb_id:
        return jsonify({'error': 'tmdb_id required'}), 400
    return jsonify(taste.dismiss(_taste_account_id(), int(tmdb_id)))


@app.route('/api/taste/stats')
def taste_stats():
    from app import taste
    account_id = _taste_account_id()
    return jsonify({'stats': taste.stats(account_id),
                    'history': taste.history(account_id)})


@app.route('/api/taste/prompt', methods=['POST'])
def taste_prompt():
    """Send the rate prompt for the most recent unrated watch.

    `tmdb_id` targets a specific film; `force` re-sends one already prompted.
    """
    from app import taste
    body = request.get_json(silent=True) or {}
    account_id = _taste_account_id()
    hours = int(body.get('hours') or 24)
    try:
        if body.get('tmdb_id'):
            # Explicit target, for testing a specific film.
            items = [f for f in taste.pending(account_id, hours, settings)
                     if f['tmdb_id'] == int(body['tmdb_id'])]
            film = items[0] if items else None
        else:
            # Only ever the newest watch — see latest_unrated.
            film = taste.latest_unrated(account_id, hours, settings)
        if not film:
            return jsonify({'sent': 0, 'reason': 'nothing unrated in window'})
        if not body.get('force') and taste.already_prompted(account_id, film['tmdb_id']):
            return jsonify({'sent': 0, 'reason': 'already prompted',
                            'film': film})
        ok = taste.send_prompt(film, _taste_push_user_id(), account_id, settings)
        return jsonify({'sent': 1 if ok else 0, 'film': film,
                        'reason': None if ok else 'push dispatch failed'})
    except Exception as e:
        logger.error(f'Taste prompt failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/genre-roulette/genres')
def genre_roulette_genres():
    """The genre wheel the picker may offer, and the current selection."""
    from app import genre_roulette
    return jsonify({
        'genres': sorted(genre_roulette.GENRES.keys()),
        'selected': genre_roulette.selected_genres(settings),
    })


@app.route('/api/genre-roulette/trackers')
def genre_roulette_trackers():
    """Trackers the Genre Roulette picker may offer.

    `selectable` is false for Torznab, which exposes one aggregate feed with no
    per-tracker addressing; the UI hides the picker in that case.
    """
    from app.indexer_api import get_indexer_client
    from app import genre_roulette
    client = get_indexer_client(settings)
    if not client:
        return jsonify({'trackers': [], 'selectable': False,
                        'error': 'No indexer configured'})
    trackers = genre_roulette.eligible_trackers(client)
    return jsonify({'trackers': trackers, 'selectable': bool(trackers)})


@app.route('/api/genre-roulette/preview')
def genre_roulette_preview():
    """Dry run — tonight's genres and what they would grab. Grabs nothing."""
    from app import genre_roulette
    try:
        return jsonify(genre_roulette.preview())
    except Exception as e:
        logger.error(f'Genre Roulette preview failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/genre-roulette/status')
def genre_roulette_status():
    from app import genre_roulette
    return jsonify({'status': genre_roulette.get_status(),
                    'log': genre_roulette.get_log()})


@app.route('/api/genre-roulette/run', methods=['POST'])
def genre_roulette_run():
    """Fire a run now. Defaults to dry_run — pass {"dry_run": false} to grab."""
    from app import genre_roulette
    dry = (request.get_json(silent=True) or {}).get('dry_run', True)
    genre_roulette.trigger_manual_run(dry_run=bool(dry))
    return jsonify({'ok': True, 'dry_run': bool(dry)})



@app.route('/api/franchise-completer/preview')
def franchise_completer_preview():
    """Dry run — popular almost-complete franchise gaps. Grabs nothing."""
    from app import franchise_completer
    try:
        return jsonify(franchise_completer.preview())
    except Exception as e:
        logger.error(f'Franchise Completer preview failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/franchise-completer/status')
def franchise_completer_status():
    from app import franchise_completer
    return jsonify({'status': franchise_completer.get_status(),
                    'log': franchise_completer.get_log()})


@app.route('/api/franchise-completer/run', methods=['POST'])
def franchise_completer_run():
    """Fire a run now. Defaults to dry_run — pass {"dry_run": false} to grab."""
    from app import franchise_completer
    dry = (request.get_json(silent=True) or {}).get('dry_run', True)
    franchise_completer.trigger_manual_run(dry_run=bool(dry))
    return jsonify({'ok': True, 'dry_run': bool(dry)})




@app.route('/api/watchlist-digital-dates/status')
def watchlist_digital_dates_status():
    from app import watchlist_digital_dates
    return jsonify({'status': watchlist_digital_dates.get_status(),
                    'log': watchlist_digital_dates.get_log()})


@app.route('/api/watchlist-digital-dates/run', methods=['POST'])
def watchlist_digital_dates_run():
    from app import watchlist_digital_dates
    data = request.get_json(silent=True) or {}
    dry = data.get('dry_run', False)
    watchlist_digital_dates.trigger_manual_run(dry_run=bool(dry))
    return jsonify({'ok': True, 'dry_run': bool(dry)})


@app.route('/api/watchlist', methods=['POST'])
def add_to_watchlist():
    """Idempotent 'ensure' endpoint: add or update a movie in Radarr.

    Works for BOTH cases:
      A) Movie not in Radarr → add with profile + monitored + search
      B) Movie already in Radarr → update monitored + profile + search

    Payload:
      { tmdb_id, title, year, quality_profile (name string) }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Missing request body'}), 400
        tmdb_id = data.get('tmdb_id')
        title = data.get('title', '')
        year = data.get('year')
        quality_profile_name = data.get('profile') or data.get('quality_profile') or ''

        if not title:
            return jsonify({'error': 'title is required'}), 400

        tmdb_id = int(tmdb_id) if tmdb_id else None

        # ----- Resolve tmdb_id from title if not provided -----
        if tmdb_id is None:
            try:
                import requests as _req
                _radarr_url = (settings.get('radarr.url') or '').rstrip('/')
                _radarr_key = settings.get('radarr.api_key', '')
                _lookup_resp = _req.get(
                    f"{_radarr_url}/api/v3/movie/lookup",
                    params={'term': title, 'apikey': _radarr_key},
                    timeout=10,
                )
                if _lookup_resp.status_code == 200:
                    _lookup_results = _lookup_resp.json()
                    if _lookup_results:
                        tmdb_id = _lookup_results[0].get('tmdbId')
                        logger.info(f"[watchlist] resolved tmdb_id={tmdb_id} for '{title}' via Radarr lookup")
                    else:
                        return jsonify({'error': f"Could not resolve tmdb_id for '{title}' — no Radarr lookup results"}), 404
                else:
                    return jsonify({'error': f"Could not resolve tmdb_id for '{title}' — Radarr lookup failed"}), 502
            except Exception as _lookup_err:
                return jsonify({'error': f"Could not resolve tmdb_id for '{title}': {_lookup_err}"}), 502
        else:
            logger.info(f"[watchlist] using client-provided tmdb_id={tmdb_id} for '{title}'")

        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured', 'needs_setup': 'radarr'}), 400

        # ----- Resolve quality profile name → ID -----
        profiles = radarr.get_quality_profiles()
        if not profiles:
            return jsonify({'error': 'Could not fetch quality profiles from Radarr (check connection)'}), 502

        quality_profile = None
        if quality_profile_name:
            quality_profile = next(
                (p for p in profiles if p.get('name', '').lower() == quality_profile_name.lower()),
                None,
            )
        if not quality_profile:
            _default_id = _resolve_radarr_profile_id(profiles)
            quality_profile = next((p for p in profiles if p.get('id') == _default_id), None)
        if not quality_profile:
            return jsonify({'error': 'No quality profiles available in Radarr'}), 500

        profile_id = quality_profile['id']
        profile_name = quality_profile.get('name', quality_profile_name)

        logger.info(
            f"[watchlist] ensure: tmdb={tmdb_id}, title='{title}', "
            f"year={year}, profile='{profile_name}' (id={profile_id})"
        )

        # ----- Delegate to idempotent ensure method -----
        result = radarr.ensure_movie_monitored(
            tmdb_id=tmdb_id,
            quality_profile_id=profile_id,
            title=title,
            year=int(year) if year else 0,
        )

        if not result['success']:
            logger.error(f"[watchlist] ensure failed: {result['error']}")
            return jsonify({
                'error': result['error'] or 'Unknown error',
                'action': result['action'],
            }), 500

        action = result['action']  # 'added' or 'updated'
        status_code = 201 if action == 'added' else 200

        logger.info(
            f"[watchlist] ensure success: action={action}, "
            f"radarr_id={result['radarr_id']}, "
            f"search_issued={result['search_issued']}, "
            f"before={result['before']}, after={result['after']}"
        )

        # The source that adds it to the watchlist owns the activity badge.
        # A fresh user add → "Manual"; 'updated' means something already had it
        # in Radarr, so don't override that origin.
        if action == 'added':
            _record_origin('requested', tmdb_id=tmdb_id, media_type='movie', title=title)

        # Prefetch US digital (TMDb type-4) date into cache immediately so the
        # under-title badge is ready without waiting for the morning job or a
        # full /api/watchlist load. Also seed the morning-job snapshot so the
        # next 6am run does not false-positive notify for this title.
        digital_date = None
        if tmdb_id:
            try:
                from app.digital_release_cache import get_digital_dates
                dates = get_digital_dates([int(tmdb_id)], force_refresh=True)
                digital_date = dates.get(int(tmdb_id))
                logger.info(
                    f"[watchlist] digital date prefetch tmdb={tmdb_id}: {digital_date}"
                )
            except Exception as dig_exc:
                logger.warning(f"[watchlist] digital date prefetch failed: {dig_exc}")
            try:
                from app.watchlist_digital_dates import upsert_snapshot_entry
                upsert_snapshot_entry(int(tmdb_id), digital_date)
            except Exception as snap_exc:
                logger.warning(f"[watchlist] snapshot upsert failed: {snap_exc}")

        return jsonify({
            'success': True,
            'action': action,
            'already_exists': action == 'updated',
            'radarr_id': result['radarr_id'],
            'profile_name': profile_name,
            'search_issued': result['search_issued'],
            'digital_release_date': digital_date,
            'message': (
                f"Monitoring enabled, profile set to {profile_name}, searching..."
                if action == 'updated'
                else f"Added to Radarr with {profile_name} profile, searching..."
            ),
        }), status_code

    except Exception as e:
        logger.error(f"[watchlist] unhandled error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

def _grab_season_pack_bg(sonarr_inst, series_id: int, season: int, title: str) -> None:
    """Background thread: try explicit season pack grab before falling back to SeasonSearch.

    SeasonSearch can grab individual episodes when season packs score lower in Sonarr's
    quality profile.  This function queries the indexers for full-season releases first
    and grabs the best-seeded one; falls back to SeasonSearch only if none are found.
    """
    import threading as _thr
    logger.info("[tv-bg] starting season pack search for '%s' s%s in thread %s",
                title, season, _thr.current_thread().name)
    try:
        packs = sonarr_inst.search_season_releases(series_id, season)
        if packs:
            best = max(packs, key=lambda r: r.get('seeders', 0) or 0)
            logger.info("[tv-bg] grabbing season pack for '%s' s%s: '%s' (%s seeders)",
                        title, season, best.get('title', '?'), best.get('seeders', 0))
            sonarr_inst.download_release(
                guid=best['guid'],
                indexer_id=best['indexerId'],
                series_id=series_id,
                season=season,
            )
        else:
            logger.info("[tv-bg] no season packs found for '%s' s%s — falling back to SeasonSearch",
                        title, season)
            sonarr_inst.search_season_command(series_id, season)
    except Exception as exc:
        logger.error("[tv-bg] season grab error for '%s' s%s: %s", title, season, exc)
        try:
            sonarr_inst.search_season_command(series_id, season)
        except Exception:
            pass


@app.route('/api/tv/watchlist', methods=['POST'])
def add_tv_to_watchlist():
    """Idempotent endpoint: add a TV show (or specific season/episode) to Sonarr.

    Payload:
      {
        tmdb_id, title,
        quality_profile (name string, optional; default = configured Sonarr profile),
        season (int, optional),   # target a specific season
        episode (int, optional),  # target a specific episode within season
      }

    Behavior:
      - No season/episode: add full show with monitor=all, search missing
      - season only: ensure that season is monitored, trigger season search
      - season + episode: ensure that specific episode is monitored, trigger episode search
      For shows already in Sonarr the series is NOT re-added; season/episode monitoring
      is applied directly.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Missing request body'}), 400

        tmdb_id = data.get('tmdb_id')
        title = data.get('title', '')
        quality_profile_name = data.get('quality_profile') or ''
        season = data.get('season')
        episode = data.get('episode')

        if season is not None:
            season = int(season)
        if episode is not None:
            episode = int(episode)

        if not title:
            return jsonify({'error': 'title is required'}), 400
        if not tmdb_id:
            return jsonify({'error': 'tmdb_id is required'}), 400

        tmdb_id = int(tmdb_id)

        # ----- Resolve TVDB ID from TMDb -----
        tv_details = tmdb.get_tv_details(tmdb_id)
        if not tv_details:
            return jsonify({'error': f"Could not find TV show with TMDb ID {tmdb_id}"}), 404

        external_ids = tv_details.get('external_ids', {})
        tvdb_id = external_ids.get('tvdb_id')
        if not tvdb_id:
            return jsonify({'error': f"No TVDB ID found for '{title}' (TMDb {tmdb_id})"}), 404

        logger.info(
            f"[tv-watchlist] resolved tvdb_id={tvdb_id} for tmdb_id={tmdb_id} "
            f"('{title}') season={season} episode={episode}"
        )

        # ----- Get Sonarr -----
        sonarr = get_sonarr_for_request()
        if not sonarr:
            return jsonify({'error': 'Sonarr not configured', 'needs_setup': 'sonarr'}), 503

        # ----- Resolve quality profile -----
        profiles = sonarr.get_quality_profiles()
        quality_profile_id = None
        if profiles:
            for p in profiles:
                if quality_profile_name and p.get('name', '').lower() == quality_profile_name.lower():
                    quality_profile_id = p['id']
                    break
            if not quality_profile_id:
                quality_profile_id = _resolve_sonarr_profile_id(profiles)
        if not quality_profile_id:
            quality_profile_id = 1

        profile_name = next(
            (p.get('name') for p in (profiles or []) if p.get('id') == quality_profile_id),
            quality_profile_name,
        )

        # ----- Check if already in Sonarr -----
        existing_series = sonarr.get_series(tvdb_id)
        series_in_sonarr = len(existing_series) > 0
        series_id = existing_series[0].get('id') if series_in_sonarr else None

        if series_in_sonarr and season is None:
            # Full-show request and already tracked — nothing to do
            logger.info(f"[tv-watchlist] series already in Sonarr: tvdb={tvdb_id} ('{title}')")
            return jsonify({
                'success': True,
                'action': 'already_exists',
                'already_exists': True,
                'sonarr_id': series_id,
                'message': f'{title} is already in Sonarr',
            }), 200

        if not series_in_sonarr:
            # Add the series; use monitor='none' for targeted requests so we don't
            # kick off a full-show download before narrowing to the right season/episode.
            monitor_mode = 'all' if season is None else 'none'
            search_missing = (season is None)
            result = sonarr.add_series(
                tvdb_id=tvdb_id,
                title=title,
                quality_profile_id=quality_profile_id,
                monitor=monitor_mode,
                search_for_missing=search_missing,
            )
            if not result:
                return jsonify({'error': 'Failed to add series to Sonarr'}), 500
            series_id = result.get('id')
            logger.info(
                f"[tv-watchlist] added '{title}' to Sonarr: tvdb={tvdb_id}, "
                f"sonarr_id={series_id}, profile='{profile_name}', monitor='{monitor_mode}'"
            )
            if season is None:
                # Full-show add with search — done. User added it to the
                # watchlist, so the activity log badges it "Manual".
                _record_origin('requested', tmdb_id=tmdb_id, media_type='tv', title=title,
                               series_id=series_id)
                return jsonify({
                    'success': True,
                    'action': 'added',
                    'already_exists': False,
                    'sonarr_id': series_id,
                    'tvdb_id': tvdb_id,
                    'profile_name': profile_name,
                    'search_issued': True,
                    'message': f"Added {title} to Sonarr with {profile_name} profile, searching...",
                }), 201

        # ----- Season or episode targeting -----
        # series_id is guaranteed to be set here (either from existing or fresh add)
        if episode is not None:
            # Specific episode — find it in Sonarr and monitor + search
            ep = sonarr.get_episode_by_series_and_numbers(series_id, season, episode)
            if ep:
                sonarr.ensure_episode_monitored(ep['id'])
                sonarr.search_episode_command([ep['id']])
                action = 'episode_added'
                msg = (f"Added S{season}E{episode} of {title} to Sonarr "
                       f"with {profile_name} profile, searching...")
                logger.info(f"[tv-watchlist] monitoring/searching S{season}E{episode} "
                            f"for series_id={series_id}")
            else:
                # Episodes may not be indexed yet after a fresh add; fall back to season
                sonarr.ensure_season_monitored(series_id, season)
                sonarr.search_season_command(series_id, season)
                action = 'season_added'
                msg = (f"Added {title} season {season} to Sonarr (searching for episode {episode}).")
                logger.info(f"[tv-watchlist] S{season}E{episode} not yet indexed, "
                            f"falling back to season search for series_id={series_id}")
        else:
            # Whole season — monitor individual episodes, then search for a season pack first.
            # Using a background thread so the endpoint returns immediately; the thread tries
            # an explicit season pack grab (prefers packs over individual episode releases)
            # and falls back to SeasonSearch only if no packs are found.
            sonarr.ensure_season_monitored(series_id, season)
            import threading as _threading
            _threading.Thread(
                target=_grab_season_pack_bg,
                args=(sonarr, series_id, season, title),
                daemon=True,
            ).start()
            action = 'season_added'
            msg = (f"Added {title} season {season} to Sonarr "
                   f"with {profile_name} profile, searching...")
            logger.info(f"[tv-watchlist] monitoring season {season} for series_id={series_id}, "
                        f"background season-pack search started")

        # A season/episode add is a request (Sonarr searches), not a Sources grab.
        _record_origin('requested', tmdb_id=tmdb_id, media_type='tv', title=title,
                       series_id=series_id)

        status_code = 201 if not series_in_sonarr else 200
        return jsonify({
            'success': True,
            'action': action,
            'already_exists': series_in_sonarr,
            'sonarr_id': series_id,
            'tvdb_id': tvdb_id,
            'profile_name': profile_name,
            'search_issued': True,
            'message': msg,
        }), status_code

    except Exception as e:
        logger.error(f"[tv-watchlist] error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/<int:tmdb_id>', methods=['DELETE'])
def remove_from_watchlist(tmdb_id):
    """Remove a movie from the watchlist by unmonitoring it in Radarr.

    Matches Luma conventions elsewhere (AutoRenamer / Genre Roulette): keep the
    Radarr record, just stop monitoring so it drops off the monitored no-file
    watchlist. Does not delete the movie or its files.
    """
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured', 'needs_setup': 'radarr'}), 400

        movie = radarr.get_movie_by_tmdb_id(tmdb_id)

        if not movie:
            return jsonify({'message': 'Movie not found in Radarr', 'success': True}), 200

        if not movie.get('monitored'):
            return jsonify({
                'message': 'Already unmonitored',
                'success': True,
                'already_unmonitored': True,
            }), 200

        success = radarr.unmonitor_movie(movie.get('id'))

        if success:
            logger.info(
                f"[watchlist] unmonitored (removed from watchlist): "
                f"{movie.get('title')} tmdb={tmdb_id}"
            )
            return jsonify({
                'message': 'Removed from watchlist',
                'success': True,
                'action': 'unmonitored',
            }), 200
        else:
            return jsonify({'error': 'Failed to unmonitor in Radarr'}), 500

    except Exception as e:
        logger.error(f"Error removing from watchlist: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/watchlist/<int:tmdb_id>/check', methods=['GET'])
def check_watchlist(tmdb_id):
    """Check if a movie is in Radarr and/or local library."""
    try:
        from app.movie_library import MovieLibrary

        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured', 'needs_setup': 'radarr'}), 400
        movie = radarr.get_movie_by_tmdb_id(tmdb_id)

        # Also check local library
        movie_lib = MovieLibrary()
        in_library = movie_lib.get_movie_by_tmdb_id(tmdb_id) is not None

        # Movie is "in watchlist" if it's in Radarr but NOT downloaded/in library yet
        in_watchlist = movie is not None and not movie.get('hasFile') and not in_library

        return jsonify({
            'in_watchlist': in_watchlist,
            'in_radarr': movie is not None,
            'in_library': in_library,
            'has_file': movie.get('hasFile') if movie else False,
            'radarr_id': movie.get('id') if movie else None
        }), 200

    except Exception as e:
        logger.error(f"Error checking Radarr: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/watchlist/<int:tmdb_id>/search', methods=['POST'])
def trigger_watchlist_search(tmdb_id):
    """Manually trigger a search in Radarr for a movie."""
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured', 'needs_setup': 'radarr'}), 400
        movie = radarr.get_movie_by_tmdb_id(tmdb_id)

        if not movie:
            return jsonify({'message': 'Movie not found in Radarr'}), 404

        # Trigger search
        result = radarr.command_search([movie.get('id')])

        if result:
            return jsonify({'message': 'Search triggered in Radarr', 'success': True}), 200
        else:
            return jsonify({'error': 'Failed to trigger search'}), 500

    except Exception as e:
        logger.error(f"Error triggering Radarr search: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/watchlist/<int:tmdb_id>/quality-profile', methods=['PUT'])
def update_watchlist_quality_profile(tmdb_id):
    """Update the quality profile for a movie in Radarr.

    Body JSON:
        quality_profile_id (int): The new quality profile ID

    Returns the updated movie info.
    """
    try:
        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured', 'needs_setup': 'radarr'}), 400

        data = request.get_json(force=True) if request.data else {}
        new_profile_id = data.get('quality_profile_id')
        if new_profile_id is None:
            return jsonify({'error': 'quality_profile_id is required'}), 400
        new_profile_id = int(new_profile_id)

        # Validate profile exists
        profiles = radarr.get_quality_profiles()
        valid_ids = {p['id'] for p in profiles}
        if new_profile_id not in valid_ids:
            profile_list = [{'id': p['id'], 'name': p['name']} for p in profiles]
            return jsonify({'error': f'Invalid quality_profile_id {new_profile_id}',
                            'available_profiles': profile_list}), 400

        # Find movie in Radarr
        movie = radarr.get_movie_by_tmdb_id(tmdb_id)
        if not movie:
            return jsonify({'error': f'Movie with TMDB ID {tmdb_id} not found in Radarr'}), 404

        radarr_id = movie['id']
        old_profile_id = movie.get('qualityProfileId')

        # Fetch full movie object for PUT
        full_movie = radarr.get_movie(radarr_id)
        if not full_movie:
            return jsonify({'error': 'Failed to fetch movie from Radarr'}), 500

        full_movie['qualityProfileId'] = new_profile_id
        updated = radarr.update_movie(full_movie)

        if not updated:
            return jsonify({'error': 'Radarr rejected the update'}), 500

        new_profile_name = next((p['name'] for p in profiles if p['id'] == new_profile_id), '')
        old_profile_name = next((p['name'] for p in profiles if p['id'] == old_profile_id), '')

        logger.info(f"Updated quality profile for '{movie.get('title')}' (tmdb={tmdb_id}): "
                     f"{old_profile_name} ({old_profile_id}) → {new_profile_name} ({new_profile_id})")

        return jsonify({
            'success': True,
            'tmdb_id': tmdb_id,
            'radarr_id': radarr_id,
            'title': movie.get('title'),
            'old_quality_profile': {'id': old_profile_id, 'name': old_profile_name},
            'new_quality_profile': {'id': new_profile_id, 'name': new_profile_name},
        })
    except Exception as e:
        logger.error(f"Error updating quality profile for tmdb={tmdb_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/downloads')
def get_downloads_storage():
    """Free/used/total bytes for the downloads filesystem (compact-home pill)."""
    try:
        from app.storage_cleanup import usage, DEFAULT_TARGET_BYTES
        data = usage()
        data['cleanup_target_bytes'] = DEFAULT_TARGET_BYTES
        return jsonify(data)
    except Exception as e:
        logger.error(f"Error reading downloads storage: {e}")
        return jsonify({'available': False, 'error': str(e)}), 500


@app.route('/api/storage/downloads/cleanup', methods=['POST'])
def cleanup_downloads_storage():
    """PERMANENTLY delete the oldest downloads until the target is freed.

    Body: {"target_bytes": <int>} — defaults to 100 GiB. Destructive and not
    undoable; the UI gates it behind an explicit confirm.
    """
    try:
        from app.storage_cleanup import run_cleanup, DEFAULT_TARGET_BYTES
        body = request.get_json(silent=True) or {}
        target = body.get('target_bytes') or DEFAULT_TARGET_BYTES
        try:
            target = int(target)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'target_bytes must be an integer'}), 400
        if target <= 0:
            return jsonify({'success': False, 'error': 'target_bytes must be positive'}), 400

        result = run_cleanup(target)
        return jsonify(result), (200 if result.get('success') else 500)
    except Exception as e:
        logger.error(f"Error cleaning up downloads storage: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/downloads-guard/status')
def downloads_guard_status():
    """Current free space, the trigger verdict, and the run history."""
    from app import downloads_guard
    return jsonify({'status': downloads_guard.get_status(),
                    'log': downloads_guard.get_log()})


@app.route('/api/downloads-guard/trackers')
def downloads_guard_trackers():
    """Trackers present in qBittorrent, with the space behind each.

    Sizes are the point: they say whether a tracker-scoped cleanup has any
    runway left. `unknown_hosts` names announce hosts nothing could classify —
    a tracker that has moved domain shows up there rather than silently
    dropping out of its own row.
    """
    from app import tracker_files
    try:
        return jsonify(tracker_files.summarise())
    except Exception as e:
        logger.error(f'Downloads Guard tracker summary failed: {e}', exc_info=True)
        return jsonify({'available': False, 'trackers': [], 'error': str(e)}), 500


@app.route('/api/downloads-guard/preview')
def downloads_guard_preview():
    """What a cleanup would delete right now. Deletes nothing."""
    from app import downloads_guard
    try:
        return jsonify(downloads_guard.preview())
    except Exception as e:
        logger.error(f'Downloads Guard preview failed: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/downloads-guard/run', methods=['POST'])
def downloads_guard_run():
    """Fire a run now, past the free-space floor.

    Defaults to dry_run — pass {"dry_run": false} to actually delete. The daily
    ceiling and every guard in storage_cleanup still apply either way.
    """
    from app import downloads_guard
    dry = (request.get_json(silent=True) or {}).get('dry_run', True)
    downloads_guard.trigger_manual_run(dry_run=bool(dry))
    return jsonify({'ok': True, 'dry_run': bool(dry)})


@app.route('/api/downloads/active')
def get_active_downloads_count():
    """Get count of active downloads."""
    try:
        import requests as req

        # Login to qBittorrent
        session = req.Session()
        login_response = session.post(
            f"{settings.get('qbittorrent.url')}/api/v2/auth/login",
            data={
                'username': settings.get('qbittorrent.username'),
                'password': settings.get('qbittorrent.password')
            },
            timeout=10
        )

        if login_response.status_code != 200 or login_response.text.strip() != 'Ok.':
            return jsonify({'count': 0})

        # Get torrents
        torrents_response = session.get(
            f"{settings.get('qbittorrent.url')}/api/v2/torrents/info",
            timeout=10
        )

        if torrents_response.status_code == 200:
            torrents = torrents_response.json()
            # Count torrents that are downloading
            active_count = sum(1 for t in torrents if t.get('progress', 1) < 1.0)
            return jsonify({'count': active_count})
        else:
            return jsonify({'count': 0})

    except Exception as e:
        logger.error(f"Error getting active downloads count: {e}")
        return jsonify({'count': 0})

_queue_cache: dict = {'ts': 0.0, 'data': None}
_QUEUE_CACHE_TTL = 3   # seconds — collapse the burst of concurrent pollers


@app.route('/api/downloads/queue')
def get_downloads_queue():
    """Every currently-active torrent in qBittorrent, enriched with title /
    poster / tmdb where we can resolve it.

    Unlike /api/radarr/queue (which only lists what Radarr's queue knows about),
    this reads qBittorrent directly, so it also surfaces torrents added manually
    or by any client Radarr/Sonarr never saw. Unknown torrents fall back to
    their torrent name. Powers the "downloading now" section of the Activity
    feed. Sorted most-complete first.
    """
    import requests as req

    # Micro-cache: this endpoint is polled by several always-mounted components
    # every 3-5s and does 2+ upstream round-trips (qBit login+scan, Radarr queue,
    # 500-row history iteration) per call. A short shared cache collapses the
    # concurrent-poller burst into one real computation.
    if _queue_cache['data'] is not None and time.time() - _queue_cache['ts'] < _QUEUE_CACHE_TTL:
        return jsonify(_queue_cache['data'])

    # 1) Pull all torrents straight from qBittorrent.
    try:
        qbit_url = settings.get('qbittorrent.url')
        if not qbit_url:
            return jsonify({'downloads': []})
        qs = req.Session()
        lr = qs.post(f"{qbit_url}/api/v2/auth/login", data={
            'username': settings.get('qbittorrent.username'),
            'password': settings.get('qbittorrent.password'),
        }, timeout=8)
        if lr.status_code != 200 or lr.text.strip() != 'Ok.':
            return jsonify({'downloads': []})
        tr = qs.get(f"{qbit_url}/api/v2/torrents/info", timeout=8)
        if tr.status_code != 200:
            return jsonify({'downloads': []})
        torrents = tr.json()
    except Exception as e:
        logger.error(f"Error fetching qBittorrent queue: {e}")
        return jsonify({'downloads': []})

    # 2) Build a hash → metadata map so known items get nice titles/posters.
    meta_by_hash = {}
    try:
        radarr = get_radarr_for_request()
        if radarr:
            for item in radarr.get_queue(include_movie=True):
                h = (item.get('downloadId') or '').lower()
                if not h:
                    continue
                movie = item.get('movie') or {}
                poster_path = None
                for img in movie.get('images', []):
                    if img.get('coverType') == 'poster':
                        remote = img.get('remoteUrl', '')
                        if '/t/p/' in remote:
                            poster_path = '/' + remote.split('/t/p/')[1].split('/', 1)[-1]
                        break
                meta_by_hash[h] = {
                    'title': movie.get('title') or item.get('title', ''),
                    'year': movie.get('year', 0),
                    'tmdb_id': movie.get('tmdbId'),
                    'poster_path': poster_path,
                    'media_type': 'movie',
                    'queue_id': item.get('id'),
                }
    except Exception:
        pass

    # In-memory meta cache (populated by /api/radarr/queue polls + grab-to-library).
    try:
        with _dl_meta_cache_lock:
            for h, m in _dl_meta_cache.items():
                if h not in meta_by_hash:
                    meta_by_hash[h] = {
                        'title': m.get('title', ''), 'year': m.get('year', 0),
                        'tmdb_id': m.get('tmdb_id'), 'poster_path': m.get('poster_path'),
                        'media_type': m.get('media_type') or 'movie',
                    }
    except Exception:
        pass

    # auto_renamer tracking (covers Sonarr TV + the app's own grab-to-library)
    # AND the post-download copy-into-library progress.
    copy_data = {}
    managed_cats = set()     # qBit categories whose completed torrents WE copy into the library
    history_hashes = set()   # lowercase hashes already finished (in our history) — don't re-show
    try:
        from app.auto_rename import auto_renamer
        if auto_renamer:
            copy_data = auto_renamer.get_copy_progress() or {}
            managed_cats = {
                (settings.get('qbittorrent.radarr_category') or 'radarr').lower(),
                _qbit_app_category().lower(),
            }
            for hi in auto_renamer.get_history(500):
                hh = (hi.get('torrent_hash') or '').lower()
                if hh:
                    history_hashes.add(hh)
            for t_hash, info in auto_renamer.get_tracking_data().items():
                h = t_hash.lower()
                if h not in meta_by_hash and getattr(info, 'title', None):
                    yr = getattr(info, 'year', 0)
                    meta_by_hash[h] = {
                        'title': info.title,
                        'year': int(yr) if yr and str(yr).isdigit() else 0,
                        'tmdb_id': getattr(info, 'tmdb_id', None),
                        'poster_path': None,
                        'media_type': getattr(info, 'media_type', None) or 'movie',
                    }
    except Exception:
        pass

    # 3) Keep torrents that are still downloading (progress < 100%), skipping
    #    paused/errored ones. Everything actively working counts as "downloading".
    _SKIP_STATES = {'pausedDL', 'pausedUP', 'error', 'missingFiles'}
    downloads = []
    seen_hashes = set()
    for t in torrents:
        progress = t.get('progress', 0) or 0
        state = t.get('state', '')
        if progress >= 1.0:
            continue
        if state in _SKIP_STATES:
            continue
        h = (t.get('hash') or '').lower()
        # Only mark ACTIVE (still-downloading) torrents as seen. A completed
        # torrent that's now seeding stays in qBit at 100% — if we marked it
        # seen here, the copy-progress loop below would skip it and the
        # copy-into-library phase would never show.
        if h:
            seen_hashes.add(h)
        eta_secs = t.get('eta', 0)
        if eta_secs and eta_secs < 8640000:
            hv = eta_secs // 3600
            mv = (eta_secs % 3600) // 60
            eta_str = f"{hv}h {mv}m" if hv > 0 else f"{mv}m" if mv > 0 else "<1m"
        else:
            eta_str = None

        meta = meta_by_hash.get(h, {})
        category = (t.get('category') or '').lower()
        media_type = meta.get('media_type') or ('tv' if ('tv' in category or 'sonarr' in category) else 'movie')

        downloads.append({
            'title': meta.get('title') or t.get('name', 'Unknown'),
            'name': t.get('name', ''),
            'hash': h,
            'year': meta.get('year', 0),
            'tmdb_id': meta.get('tmdb_id'),
            'poster_path': meta.get('poster_path'),
            'media_type': media_type,
            'progress': round(progress, 3),
            'size_total': t.get('total_size', 0),
            'size_remaining': t.get('amount_left', 0),
            'speed': t.get('dlspeed', 0) or 0,
            'eta': eta_str,
            'status': 'stalled' if 'stalled' in state.lower() else 'downloading',
            'state': state,
            'tracker': _normalize_tracker_url(t.get('tracker', '')),
            'queue_id': meta.get('queue_id'),
        })

    # 4) File-copy-into-library items (post-download). These have left the qBit
    #    "downloading" set (progress 100%) and are now being copied by the
    #    auto-renamer — surface them with their copy progress so the user sees
    #    the move into the library, not a gap.
    copy_hashes = set()
    for cp_hash, cp in copy_data.items():
        h = cp_hash.lower()
        cp_pct = cp.get('progress', 0)
        if h in seen_hashes or cp_pct >= 100:
            continue
        copy_hashes.add(h)
        meta = meta_by_hash.get(h, {})
        cp_size = cp.get('size', 0) or 0
        downloads.append({
            'title': meta.get('title') or cp.get('torrent_name') or 'Unknown',
            'name': cp.get('torrent_name', ''),
            'hash': h,
            'year': meta.get('year', 0),
            'tmdb_id': meta.get('tmdb_id'),
            'poster_path': meta.get('poster_path'),
            'media_type': meta.get('media_type', 'movie'),
            'progress': round(cp_pct / 100, 3),
            'size_total': cp_size,
            'size_remaining': int(cp_size * (1 - cp_pct / 100)),
            'speed': 0,
            'eta': None,
            'status': 'copying',
            'state': 'copying',
            'tracker': None,
            'queue_id': None,
        })

    # 5) Bridge the gap: a torrent in one of OUR managed categories (Radarr /
    #    app grab-to-library) that JUST finished downloading but whose copy
    #    hasn't started yet (auto-renamer polls every 30s) — show it
    #    "Processing" so the file never disappears between download-complete and
    #    copy-start. Gated on RECENT completion (completion_on within the bridge
    #    window) so the whole seeding library isn't flagged. Stops once it lands
    #    in history (copy done → moves to the completed list) or copy begins.
    _GAP_WINDOW = 600  # seconds — a fresh completion still in the pre-copy gap
    _now = time.time()
    for t in torrents:
        if (t.get('progress', 0) or 0) < 1.0:
            continue
        if t.get('state', '') in _SKIP_STATES:
            continue
        if (t.get('category') or '').lower() not in managed_cats:
            continue
        completed_on = t.get('completion_on', 0) or 0
        if completed_on <= 0 or (_now - completed_on) > _GAP_WINDOW:
            continue
        h = (t.get('hash') or '').lower()
        if h in seen_hashes or h in copy_hashes or h in history_hashes:
            continue
        meta = meta_by_hash.get(h, {})
        downloads.append({
            'title': meta.get('title') or t.get('name', 'Unknown'),
            'name': t.get('name', ''),
            'hash': h,
            'year': meta.get('year', 0),
            'tmdb_id': meta.get('tmdb_id'),
            'poster_path': meta.get('poster_path'),
            'media_type': meta.get('media_type', 'movie'),
            'progress': 1.0,
            'size_total': t.get('total_size', 0),
            'size_remaining': 0,
            'speed': 0,
            'eta': None,
            'status': 'importing',
            'state': 'importing',
            'tracker': _normalize_tracker_url(t.get('tracker', '')),
            'queue_id': meta.get('queue_id'),
        })

    # 6) Poster backfill. The meta sources above only carry artwork while
    #    Radarr's live queue still knows the item — Radarr drops it the moment
    #    the download finishes, so copying/importing rows lose their poster.
    #    Sonarr TV and manual/app grabs come from auto_renamer tracking, which
    #    has no poster at all. Both render "No art" in the Activity feed without
    #    this. Resolve from TMDb here rather than at each meta source: it's
    #    bounded to the handful of rows we actually return, and
    #    _resolve_tmdb_info caches hits AND misses for an hour, so the 3-5s
    #    poll costs nothing after the first resolve.
    def _backfill_poster(d):
        title = (d.get('title') or '').strip()
        year = d.get('year') or ''
        media_type = d.get('media_type') or 'movie'
        # No metadata match — the title IS the raw release name, which TMDb
        # can't search on. Parse a real title/year/type out of it first.
        if not title or title == d.get('name'):
            # media_type already folds in the qBit category hint, and doubles
            # as the category arg so a TV item isn't downgraded to a movie
            # search when the name carries no SxxExx.
            parsed = _parse_torrent_name(d.get('name') or '', media_type)
            title = parsed.get('title') or title
            year = parsed.get('year') or year
            media_type = parsed.get('media_type') or media_type
        if not title:
            return
        try:
            info = _resolve_tmdb_info(title, str(year or ''), media_type)
        except Exception:
            return
        if info.get('poster_path'):
            d['poster_path'] = info['poster_path']
        # Backfill the id too so the row becomes tappable into the detail
        # modal. Title is intentionally left alone — the raw release name is
        # what the user is tracking.
        if not d.get('tmdb_id') and info.get('tmdb_id'):
            d['tmdb_id'] = info['tmdb_id']

    # Resolve in parallel: on a cold cache each miss is a TMDb round-trip, and
    # several concurrent downloads would otherwise stack them into one request.
    _needs_art = [d for d in downloads if not d.get('poster_path')]
    if _needs_art:
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(6, len(_needs_art))) as ex:
                list(ex.map(_backfill_poster, _needs_art))
        except Exception:
            pass

    downloads.sort(key=lambda d: d['progress'], reverse=True)
    payload = {'downloads': downloads}
    _queue_cache['data'] = payload
    _queue_cache['ts'] = time.time()
    return jsonify(payload)

@app.route('/search')
def search():
    """Search endpoint for movies, TV shows, and people."""
    import concurrent.futures

    raw_query = request.args.get('q', '').strip()
    media_type = request.args.get('type', 'all')  # all, movie, tv, person

    if not raw_query:
        return jsonify({'error': 'No search query provided'}), 400

    # Extract a 4-digit year from the query (e.g. "Wrestlemania 2026" → year=2026)
    _year_match = re.search(r'\b((?:19|20)\d{2})\b', raw_query)
    search_year: Optional[int] = int(_year_match.group(1)) if _year_match else None
    # Strip the year from the search string so TMDb gets a clean title query
    query = re.sub(r'\s*\b(?:19|20)\d{2}\b\s*', ' ', raw_query).strip() if _year_match else raw_query

    results = {
        'movies': [],
        'tv_shows': [],
        'people': [],
        'query': raw_query,
        'timestamp': datetime.now().isoformat()
    }

    # Run TMDb searches in parallel for speed.
    #
    # `multi` supplies the RANKING (it scores movies and TV against each other,
    # which per-type search can't do); the per-type searches supply DEPTH (multi
    # returns 20 mixed results, per-type returns 20 each). Every item carries a
    # `relevance_rank` so the client can render one correctly-ordered list
    # instead of re-sorting and destroying the order.
    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        want_movies = media_type in ['all', 'movie']
        want_tv = media_type in ['all', 'tv']
        if want_movies or want_tv:
            futures['multi'] = executor.submit(tmdb.search_multi, query)
        if want_movies:
            futures['movies'] = executor.submit(tmdb.search_movies, query, search_year)
        if want_tv:
            futures['tv_shows'] = executor.submit(tmdb.search_tv, query, search_year)
        if media_type in ['all', 'person']:
            futures['people'] = executor.submit(tmdb.search_people, query)

        raw = {}
        for key, future in futures.items():
            try:
                raw[key] = future.result(timeout=10)
            except Exception as e:
                logger.error(f"TMDb parallel search error for {key}: {e}")
                raw[key] = []

    results['people'] = raw.get('people', [])

    # Spelling correction fallback: retry only when results are weak
    # Skip correction retry if we're currently rate limited
    corrected_raw = {}
    corrected = None
    if (len(query) > 3 and 
        not fast_search_index._is_tmdb_rate_limited() and 
        _tmdb_needs_spelling_correction(raw, query)):
        try:
            corrected = fast_search_index.find_spelling_correction(query, threshold=0.65)
            if corrected and corrected.lower() != query.lower():
                logger.info(f"search: trying spelling correction '{corrected}' for '{query}'")
                # Retry TMDb searches with corrected query (silent correction - no UI text)
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    corr_futures = {}
                    if want_movies or want_tv:
                        corr_futures['multi'] = executor.submit(tmdb.search_multi, corrected)
                    if want_movies:
                        corr_futures['movies'] = executor.submit(tmdb.search_movies, corrected, search_year)
                    if want_tv:
                        corr_futures['tv_shows'] = executor.submit(tmdb.search_tv, corrected, search_year)
                    
                    for key, future in corr_futures.items():
                        try:
                            corrected_raw[key] = future.result(timeout=10)
                            # Tag corrected items
                            for item in corrected_raw[key]:
                                item['_corrected'] = True
                        except Exception as e:
                            logger.warning(f"Spelling-corrected search for {key} failed: {e}")
                            corrected_raw[key] = []
        except Exception as e:
            logger.warning(f"Spelling correction failed: {e}")

    # Typo rescue (search_correct): TMDb's literal search returns The Bank Job
    # for 'the nit job', and The Nut Job isn't in the popular-titles
    # dictionary. When neither the direct nor the dictionary-corrected results
    # read like the query, try one-letter word variants and keep the one TMDb
    # answers with a title that reads exactly like it.
    direct_items = raw.get('multi', []) + raw.get('movies', []) + raw.get('tv_shows', [])
    corrected_items = corrected_raw.get('multi', []) + corrected_raw.get('movies', []) + corrected_raw.get('tv_shows', [])
    direct_strong = search_correct.strong(query, raw.get('people', [])) or \
        search_correct.strong(query, raw.get('multi', [])) or \
        search_correct.strong(query, raw.get('movies', [])) or search_correct.strong(query, raw.get('tv_shows', []))
    prefer_corrected = False
    if corrected_items and not direct_strong and search_correct.strong(corrected or '', corrected_items, top=5):
        prefer_corrected = True
    if (not direct_strong and not prefer_corrected and (want_movies or want_tv) and len(query) > 3
            and not fast_search_index._is_tmdb_rate_limited()):
        variants = search_correct.query_variants(query)
        if variants:
            found = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                vf = {v: executor.submit(tmdb.search_multi, v) for v in variants}
                for v, f in vf.items():
                    try:
                        found[v] = [it for it in (f.result(timeout=10) or []) if isinstance(it, dict)]
                    except Exception as e:
                        logger.warning(f"Typo-variant search failed: {type(e).__name__}")
                        found[v] = []
            hit = search_correct.pick(found)
            if hit:
                corrected, items = hit
                logger.info("search: typo variant '%s' for '%s'", corrected, query)
                for it in items:
                    it['_corrected'] = True
                corrected_raw = {'multi': items}
                prefer_corrected = True

    ranked_movies, ranked_tv = [], []
    seen = {'movie': set(), 'tv': set()}
    _pos = 0

    def _take(mt, item):
        """Assign the next relevance rank, skipping ids already placed."""
        nonlocal _pos
        if not item.get('id') or item['id'] in seen[mt]:
            return
        seen[mt].add(item['id'])
        item['relevance_rank'] = _pos
        _pos += 1
        (ranked_movies if mt == 'movie' else ranked_tv).append(item)

    # Only keep the types the caller asked for — multi returns all of them, and
    # a type=movie query must not come back with TV shows in tv_shows.
    wanted_types = {t for t, ok in (('movie', want_movies), ('tv', want_tv)) if ok}
    
    # Separate direct and corrected results
    multi_direct = [(it['media_type'], it) for it in raw.get('multi', [])
                    if it.get('media_type') in wanted_types and it.get('id') and not it.get('_corrected')]
    multi_corrected = [(it['media_type'], it) for it in corrected_raw.get('multi', [])
                       if it.get('media_type') in wanted_types and it.get('id')]

    # Per-type results interleaved round-robin rather than movies-then-TV
    mv_direct = [it for it in raw.get('movies', []) if not it.get('_corrected')]
    tv_direct = [it for it in raw.get('tv_shows', []) if not it.get('_corrected')]
    mv_corrected = corrected_raw.get('movies', [])
    tv_corrected = corrected_raw.get('tv_shows', [])
    
    per_type_direct = []
    for i in range(max(len(mv_direct), len(tv_direct))):
        if i < len(mv_direct):
            per_type_direct.append(('movie', mv_direct[i]))
        if i < len(tv_direct):
            per_type_direct.append(('tv', tv_direct[i]))
    
    per_type_corrected = []
    for i in range(max(len(mv_corrected), len(tv_corrected))):
        if i < len(mv_corrected):
            per_type_corrected.append(('movie', mv_corrected[i]))
        if i < len(tv_corrected):
            per_type_corrected.append(('tv', tv_corrected[i]))

    # /search/multi takes no year parameter, and when the user typed one TMDb's
    # year-FILTERED per-type search is far more precise than anything we can
    # recover by re-sorting multi. So a year query leads with the filtered results.
    # Rank direct results first, then corrected results after ALL direct results.
    direct_order = (per_type_direct + multi_direct) if search_year else (multi_direct + per_type_direct)
    corrected_order = (per_type_corrected + multi_corrected) if search_year else (multi_corrected + per_type_corrected)
    
    # Direct results first, then corrected ones (deduped by _take), unless the
    # direct results don't read like the query and the corrected ones do.
    for mt, item in (corrected_order + direct_order) if prefer_corrected else (direct_order + corrected_order):
        _take(mt, item)

    results['movies'] = ranked_movies
    results['tv_shows'] = ranked_tv

    # Remove internal _corrected markers before returning
    for item in results['movies']:
        item.pop('_corrected', None)
    for item in results['tv_shows']:
        item.pop('_corrected', None)
    # Lets the UI say TMDb is busy instead of showing nothing (Tester e).
    results['rate_limited'] = bool(fast_search_index._is_tmdb_rate_limited())

    # Log search to history
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO search_history (query, results_count) VALUES (?, ?)',
            (query, len(results['movies']) + len(results['tv_shows']) + len(results.get('people', [])))
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to log search history: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return jsonify(results)

# ---------------------------------------------------------------------------
# AI Natural Language Search
# ---------------------------------------------------------------------------

_natural_search_cache: Dict[str, tuple] = {}  # md5 → (timestamp, response_data)
_NATURAL_SEARCH_TTL = 5 * 60  # 5 minutes
_NATURAL_SEARCH_CACHE_MAX = 200


@app.route('/api/search/natural', methods=['POST'])
def natural_search():
    """AI-powered natural language search.

    Strategy: Ask the AI to recommend specific titles (like ChatGPT would),
    then look each title up in Plex (owned) and TMDb (unowned).
    """
    body = request.get_json(silent=True) or {}
    description = (body.get('description', '') or '').strip()
    media_type = body.get('media_type', 'any') or 'any'
    if media_type not in ('movie', 'tv', 'any'):
        media_type = 'any'

    if not description:
        return jsonify({'error': 'Description is required'}), 400
    if len(description) > 500:
        description = description[:500]

    # Check OpenAI configuration
    openai_enabled = settings.get('apis.openai.enabled', False)
    openai_key = settings.get('apis.openai.api_key', '')
    if not openai_enabled or not openai_key:
        return jsonify({'error': 'OpenAI is not configured. Enable it in Settings > AI.'}), 503

    # Read configurable AI parameters
    custom_instructions = settings.get('apis.openai.custom_instructions', '')
    temperature = float(settings.get('apis.openai.temperature', 0.7))
    max_tokens = int(settings.get('apis.openai.max_tokens', 1500))

    # Cache check (include media_type + custom_instructions so results update when preferences change)
    import hashlib
    cache_raw = f"{description.lower()}|{media_type}|{custom_instructions}"
    cache_key = hashlib.md5(cache_raw.encode()).hexdigest()
    cached = _natural_search_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _NATURAL_SEARCH_TTL:
            return jsonify(data)

    # --- Step 1: Ask AI for specific title recommendations ---
    model = settings.get('apis.openai.model', 'gpt-4o-mini')
    try:
        from app import openai_client as openai_lib
        client = openai_lib.OpenAI(api_key=openai_key, max_retries=0)

        system_prompt = """You are a movie and TV show identification and recommendation engine. The user will describe what they want to watch.

CRITICAL FIRST STEP — Read the user's description and decide:
A) Are they describing a SPECIFIC movie/show they're trying to identify? (plot details, character descriptions, specific scenes, "that movie where...")
B) Are they asking for general recommendations matching a vibe/mood/theme?

If (A) — IDENTIFICATION MODE:
- Your #1 job is to figure out EXACTLY which title they mean
- Put the most likely exact match as recommendation #1 with category "The One"
- Then include 7-11 similar titles they'd also enjoy
- Think hard about plot clues, character details, settings, and time periods

If (B) — RECOMMENDATION MODE:
- Provide 8-12 titles that best match the described vibe/mood
- Include a mix of well-known and hidden gems

Output ONLY valid JSON with these fields:
- "recommendations": array of 8-12 objects, each with:
  - "title": exact title as it appears on TMDb/IMDb
  - "year": release year (integer)
  - "type": "movie" or "tv"
  - "category": a short 2-4 word VIBE label describing the feeling or mood of this pick. NEVER just name the genre. Good examples: "The One", "Cozy classic", "Chill dramedy", "Warm & feel-good", "Easy comedy pick", "Pure sarcastic energy", "Effortlessly cool", "Dark slow-burn", "Nonstop laughs", "Hidden gem", "Sleeper hit", "Cult favorite", "Peak 80s fun". Bad examples: "Comedy", "Thriller", "Action", "Drama", "Sci-Fi".
  - "reason": 1-2 sentences explaining the vibe and why it matches. Be specific about tone and feel.
- "genres": array of up to 3 genre labels that describe the request (e.g. "Dark Comedy", "Thriller")
- "moods": array of up to 3 mood keywords (e.g. "dark", "quirky", "intense")
- "media_type": "movie", "tv", or "any" based on what the user asked for

Be specific and accurate with titles and years. Quality over quantity — every pick should be a strong match. Include a mix of well-known and hidden gems. Every category MUST be unique — never repeat the same category label across recommendations. If the user mentions an actor, director, or creator, every recommendation MUST feature that person."""

        # Append user's custom content preferences (if any)
        if custom_instructions.strip():
            system_prompt += f"\n\nADDITIONAL INSTRUCTIONS FROM USER:\n{custom_instructions.strip()}"

        # Append media type instruction so the AI only recommends the requested type
        user_message = description
        if media_type == 'movie':
            user_message += '\n\n(Looking for movies only, no TV shows)'
        elif media_type == 'tv':
            user_message += '\n\n(Looking for TV shows only, no movies)'

        # gpt-5+ and o-series: max_completion_tokens, no temperature control
        is_new_model = model.startswith('gpt-5') or model.startswith('o')
        api_kwargs = {
            'model': model,
            'messages': [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            'response_format': {"type": "json_object"},
            'timeout': 120 if is_new_model else 45,
        }
        if is_new_model:
            api_kwargs['max_completion_tokens'] = max(max_tokens, 4000)
            api_kwargs['extra_body'] = {'reasoning_effort': 'low'}
        else:
            api_kwargs['max_tokens'] = max_tokens
            api_kwargs['temperature'] = temperature
        completion = client.chat.completions.create(**api_kwargs)

        usage = completion.usage
        if usage:
            logger.info(f"Natural search: model={model}, tokens: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, total={usage.total_tokens}")
        else:
            logger.info(f"Natural search: model={model}, tokens: usage data not available")

        import json as json_lib
        raw_content = completion.choices[0].message.content or ''
        finish_reason = completion.choices[0].finish_reason
        logger.info(f"Natural search: finish_reason={finish_reason}, content_length={len(raw_content)}")
        if finish_reason == 'length':
            logger.warning(f"Natural search: response truncated! Last 200 chars: ...{raw_content[-200:]}")
        parsed = json_lib.loads(raw_content)
    except Exception as e:
        logger.error(f"OpenAI natural search error: {e}")
        err_msg = str(e)
        if 'insufficient_quota' in err_msg:
            return jsonify({'error': 'OpenAI quota exceeded. Add credits at platform.openai.com/settings/organization/billing.'}), 502
        if 'rate_limit' in err_msg.lower() or 'RateLimitError' in type(e).__name__:
            return jsonify({'error': 'OpenAI rate limit reached. Please try again shortly.'}), 502
        if 'timeout' in err_msg.lower():
            return jsonify({'error': 'OpenAI request timed out. Please try again.'}), 502
        return jsonify({'error': f'AI processing failed: {err_msg}'}), 502

    recommendations = parsed.get('recommendations', [])
    genres = parsed.get('genres', [])[:3]
    moods = parsed.get('moods', [])[:3]
    ai_media_type = parsed.get('media_type', 'any')

    logger.info(f"Natural search: AI returned {len(recommendations)} recommendations for '{description}'")
    for rec in recommendations[:5]:
        logger.info(f"  - {rec.get('title')} ({rec.get('year')}) [{rec.get('type')}]: {rec.get('reason', '')}")

    # --- Step 2: Resolve recommendations via TMDb (parallel) ---
    tmdb_api_key = settings.get('apis.tmdb.api_key', '') or Config.TMDB_API_KEY
    tmdb_base = settings.get('apis.tmdb.base_url', '') or 'https://api.themoviedb.org/3'
    image_base = settings.get('apis.tmdb.image_base_url', '') or 'https://image.tmdb.org/t/p'

    seen_tmdb_ids = set()

    def _lookup_tmdb(rec):
        """Search TMDb for a recommended title. Returns (tmdb_item, media_type) or None."""
        title = rec.get('title', '')
        year = rec.get('year')
        mtype = rec.get('type', 'movie')
        if not title:
            return None

        endpoint = '/search/movie' if mtype == 'movie' else '/search/tv'
        params = {'api_key': tmdb_api_key, 'query': title, 'language': 'en-US', 'page': 1}
        if year:
            params['year' if mtype == 'movie' else 'first_air_date_year'] = year

        try:
            resp = requests.get(f'{tmdb_base}{endpoint}', params=params, timeout=8)
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                if results:
                    return (results[0], mtype)
                # Retry without year constraint
                if year:
                    params.pop('year', None)
                    params.pop('first_air_date_year', None)
                    resp2 = requests.get(f'{tmdb_base}{endpoint}', params=params, timeout=8)
                    if resp2.status_code == 200:
                        results2 = resp2.json().get('results', [])
                        if results2:
                            return (results2[0], mtype)
        except Exception as e:
            logger.warning(f"Natural search TMDb lookup for '{title}': {e}")
        return None

    # Parallel TMDb lookups (8 workers to resolve all titles quickly)
    results_list = []  # (index, item)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_lookup_tmdb, rec): (i, rec) for i, rec in enumerate(recommendations)}
        for future in as_completed(futures):
            idx, rec = futures[future]
            reason = rec.get('reason', '')
            category = rec.get('category', '')
            try:
                result = future.result(timeout=15)
            except Exception:
                result = None

            if result:
                tmdb_item, mtype = result
                tmdb_id = tmdb_item.get('id')
                if tmdb_id and tmdb_id not in seen_tmdb_ids:
                    seen_tmdb_ids.add(tmdb_id)
                    title = tmdb_item.get('title') or tmdb_item.get('name') or ''
                    year_str = (tmdb_item.get('release_date') or tmdb_item.get('first_air_date') or '')[:4]
                    year_val = int(year_str) if year_str.isdigit() else None
                    poster_path = tmdb_item.get('poster_path')
                    backdrop_path = tmdb_item.get('backdrop_path')

                    results_list.append((idx, {
                        'title': title,
                        'year': year_val,
                        'type': 'tv' if mtype == 'tv' else 'movie',
                        'tmdb_id': tmdb_id,
                        'rating': tmdb_item.get('vote_average'),
                        'vote_count': tmdb_item.get('vote_count', 0),
                        'overview': tmdb_item.get('overview', ''),
                        'poster_url': f'{image_base}/w500{poster_path}' if poster_path else None,
                        'backdrop_url': f'{image_base}/w1280{backdrop_path}' if backdrop_path else None,
                        'genres': [],
                        'runtime_minutes': 0,
                        'content_rating': '',
                        'ai_reason': reason,
                        'ai_category': category,
                        'ai_rank': idx,
                    }))

    # Preserve AI recommendation order
    results_list.sort(key=lambda x: x[0])
    all_results = [item for _, item in results_list]
    enrich_imdb_ratings(all_results, settings=settings, max_items=40, replace_rating=True)

    # Post-filter by media_type as safety net
    if media_type == 'movie':
        all_results = [r for r in all_results if r.get('type') != 'tv']
    elif media_type == 'tv':
        all_results = [r for r in all_results if r.get('type') == 'tv']

    logger.info(f"Natural search results: {len(all_results)} results")

    # Build response — ownership determined client-side when modal opens
    response_data = {
        'owned': [],
        'unowned': all_results[:20],
        'parsed': {
            'genres': genres,
            'moods': moods,
            'media_type': ai_media_type,
        },
        'description': description,
    }

    # Cache — evict expired entries and enforce max size
    now_ts = time.time()
    expired = [k for k, (ts, _) in list(_natural_search_cache.items())
               if now_ts - ts >= _NATURAL_SEARCH_TTL]
    for k in expired:
        del _natural_search_cache[k]
    if len(_natural_search_cache) >= _NATURAL_SEARCH_CACHE_MAX:
        sorted_keys = sorted(_natural_search_cache, key=lambda k: _natural_search_cache[k][0])
        for k in sorted_keys[:len(_natural_search_cache) - _NATURAL_SEARCH_CACHE_MAX + 1]:
            del _natural_search_cache[k]
    _natural_search_cache[cache_key] = (now_ts, response_data)

    return jsonify(response_data)

# ---------------------------------------------------------------------------
# Voice Assistant
# ---------------------------------------------------------------------------

def _resolve_playback_target(rating_key: int, room_id: str,
                              resume: bool = False) -> Dict[str, Any]:
    """Resolve room → device → service and trigger playback.

    Shared helper used by both the player_play endpoint and voice assistant.
    Returns a dict with success/error info.
    """
    room = settings.get_room_by_id(room_id)
    if not room:
        return {'success': False, 'error': f'Room not found: {room_id}'}

    device = None
    service = None
    client_ip = ''
    adb_port = settings.get_default_adb_port()

    if room and isinstance(room.get('devices'), list):
        # Prefer a device with a Plex service
        for dev in room['devices']:
            if not isinstance(dev, dict):
                continue
            for svc in (dev.get('services') or []):
                if isinstance(svc, dict) and svc.get('type') == 'plex':
                    device = dev
                    break
            if device:
                break
        if not device and room['devices']:
            device = room['devices'][0] if isinstance(room['devices'][0], dict) else None

        if device:
            client_ip = device.get('host', '')
            adb_port = device.get('connectPort', adb_port)
            # Prefer Plex service
            for svc in (device.get('services') or []):
                if isinstance(svc, dict) and svc.get('type') == 'plex':
                    service = svc
                    break
            if not service and device.get('services'):
                service = device['services'][0] if isinstance(device['services'][0], dict) else None
    elif room:
        client_ip = room.get('host') or room.get('ip', '')
        adb_port = room.get('connectPort') or room.get('adb_port', adb_port)

    if not client_ip:
        return {'success': False, 'error': 'Could not resolve device IP for room'}

    resume_offset = 0
    if resume:
        metadata = plex_service.get_metadata(rating_key)
        if metadata:
            resume_offset = metadata.get('viewOffset', 0)

    # Wake device
    try:
        from app.device_wake import ensure_device_ready
        wake = ensure_device_ready(device or {}, room or {})
        logger.info("Voice playback wake: ok=%s", wake.get('ok'))
    except Exception as we:
        logger.warning("Device wake failed: %s", we)

    # Route to playback
    platform = device.get('platform', '') if device else ''
    room_name = room.get('name', room_id)
    svc_type = (service or {}).get('type', 'kodi')

    try:
        if svc_type == 'plex':
            result = _play_via_plex(service or {}, rating_key, resume_offset, room_name, client_ip, adb_port, platform)
        else:
            result = _play_via_kodi(service or {}, rating_key, resume_offset, room_name, client_ip, adb_port, platform)
        # Extract JSON response
        if hasattr(result, 'get_json'):
            return result.get_json()
        if isinstance(result, tuple):
            resp, code = result
            if hasattr(resp, 'get_json'):
                return resp.get_json()
        return {'success': True, 'message': f'Playing in {room_name}'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


@app.route('/api/voice/command', methods=['POST'])
def voice_command():
    """Process a voice command via GPT function calling."""
    body = request.get_json(silent=True) or {}
    transcript = (body.get('transcript', '') or '').strip()
    history = body.get('history', [])

    if not transcript:
        return jsonify({'error': 'Transcript is required'}), 400
    if len(transcript) > 500:
        transcript = transcript[:500]

    # Check voice is enabled
    voice_enabled = settings.get('apis.openai.voice_enabled', False)
    openai_enabled = settings.get('apis.openai.enabled', False)
    openai_key = settings.get('apis.openai.api_key', '')

    if not voice_enabled or not openai_enabled or not openai_key:
        return jsonify({'error': 'Voice assistant is not enabled. Enable it in Settings > AI.'}), 503

    from app.voice_service import process_voice_command
    result = process_voice_command(
        transcript=transcript,
        history=history,
        settings=settings,
        plex_service=plex_service,
        resolve_playback_fn=_resolve_playback_target,
    )
    return jsonify(result)


@app.route('/api/voice/tts', methods=['POST'])
def voice_tts():
    """Convert text to speech. Supports OpenAI and ElevenLabs providers."""
    body = request.get_json(silent=True) or {}
    text = (body.get('text', '') or '').strip()
    speed = body.get('speed', 1.0)

    if not text:
        return jsonify({'error': 'Text is required'}), 400
    if len(text) > 4096:
        text = text[:4096]

    provider = settings.get('apis.openai.tts_provider', 'openai')

    from flask import Response as FlaskResponse

    # ----- ElevenLabs -----
    if provider == 'elevenlabs':
        el_key = settings.get('apis.elevenlabs.api_key', '')
        # Allow voice_id override from request body (for preview before saving)
        el_voice = body.get('elevenlabs_voice_id') or settings.get('apis.elevenlabs.voice_id', '')
        logger.info('ElevenLabs TTS — voice_id=%s', el_voice)
        if not el_key or not el_voice:
            return jsonify({'error': 'ElevenLabs API key and voice ID are required'}), 503

        el_model = settings.get('apis.elevenlabs.model_id', 'eleven_multilingual_v2')
        stability = settings.get('apis.elevenlabs.stability', 0.5)
        similarity = settings.get('apis.elevenlabs.similarity', 0.75)

        try:
            import requests as req
            resp = req.post(
                f'https://api.elevenlabs.io/v1/text-to-speech/{el_voice}',
                headers={
                    'xi-api-key': el_key,
                    'Content-Type': 'application/json',
                    'Accept': 'audio/mpeg',
                },
                json={
                    'text': text,
                    'model_id': el_model,
                    'voice_settings': {
                        'stability': stability,
                        'similarity_boost': similarity,
                    },
                },
                timeout=20,
                stream=True,
            )
            resp.raise_for_status()

            return FlaskResponse(
                resp.iter_content(chunk_size=4096),
                content_type='audio/mpeg',
                headers={'Cache-Control': 'no-cache'},
            )
        except Exception as e:
            logger.error('ElevenLabs TTS failed: %s', e)
            return jsonify({'error': 'ElevenLabs TTS generation failed'}), 500

    # ----- OpenAI (default) -----
    openai_key = settings.get('apis.openai.api_key', '')
    if not openai_key:
        return jsonify({'error': 'OpenAI is not configured'}), 503

    default_voice = settings.get('apis.openai.tts_voice', 'nova')
    voice = body.get('voice', default_voice)

    try:
        import requests as req
        resp = req.post(
            'https://api.openai.com/v1/audio/speech',
            headers={
                'Authorization': f'Bearer {openai_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'tts-1',
                'input': text,
                'voice': voice,
                'speed': speed,
                'response_format': 'mp3',
            },
            timeout=15,
            stream=True,
        )
        resp.raise_for_status()

        return FlaskResponse(
            resp.iter_content(chunk_size=4096),
            content_type='audio/mpeg',
            headers={'Cache-Control': 'no-cache'},
        )
    except Exception as e:
        logger.error('TTS failed: %s', e)
        return jsonify({'error': 'TTS generation failed'}), 500


@app.route('/api/voice/transcribe', methods=['POST'])
def voice_transcribe():
    """Transcribe audio using OpenAI Whisper API."""
    openai_key = settings.get('apis.openai.api_key', '')
    if not openai_key:
        return jsonify({'error': 'OpenAI is not configured'}), 503

    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    audio_file = request.files['audio']
    if not audio_file.filename:
        return jsonify({'error': 'No audio file provided'}), 400

    # Whisper API limit is 25 MB
    audio_file.seek(0, 2)
    size = audio_file.tell()
    audio_file.seek(0)
    if size > 25 * 1024 * 1024:
        return jsonify({'error': 'Audio file exceeds 25 MB limit'}), 400

    language = request.form.get('language', 'en')

    try:
        import requests as req
        resp = req.post(
            'https://api.openai.com/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {openai_key}'},
            files={'file': (audio_file.filename, audio_file.stream, audio_file.content_type or 'audio/webm')},
            data={
                'model': 'whisper-1',
                'language': language,
                'response_format': 'json',
                'prompt': 'This is a voice command to a media assistant called Luma. The user may ask about movies, TV shows, playback, downloads, or navigation.',
            },
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        return jsonify({'text': result.get('text', '')})
    except Exception as e:
        logger.error('Whisper transcription failed: %s', e)
        return jsonify({'error': 'Transcription failed'}), 500


@app.route('/api/elevenlabs/voices', methods=['GET'])
def elevenlabs_voices():
    """List available ElevenLabs voices."""
    el_key = settings.get('apis.elevenlabs.api_key', '')
    if not el_key:
        return jsonify({'voices': [], 'error': 'ElevenLabs API key not configured'}), 200
    try:
        import requests as req
        resp = req.get(
            'https://api.elevenlabs.io/v1/voices',
            headers={'xi-api-key': el_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        voices = [
            {'voice_id': v['voice_id'], 'name': v.get('name', ''), 'category': v.get('category', '')}
            for v in data.get('voices', [])
        ]
        return jsonify({'voices': voices})
    except Exception as e:
        logger.error('ElevenLabs voices fetch failed: %s', e)
        return jsonify({'voices': [], 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Alfred agent bridge
# ---------------------------------------------------------------------------

_alfred_bridge = None  # lazy singleton

def _get_alfred_bridge():
    """Lazy-init the Alfred bridge from settings."""
    global _alfred_bridge
    enabled = settings.get('apis.alfred.enabled', False)
    base_url = settings.get('apis.alfred.base_url', '')
    if not enabled or not base_url:
        return None
    # Re-create if base_url changed
    if _alfred_bridge is None or _alfred_bridge.base_url != base_url.rstrip('/'):
        from app.alfred_bridge import AlfredBridge
        api_key = settings.get('apis.alfred.api_key', '') or None
        timeout = settings.get('apis.alfred.timeout', 25)
        _alfred_bridge = AlfredBridge(base_url, api_key=api_key, timeout=timeout)
    return _alfred_bridge


@app.route('/api/alfred/command', methods=['POST'])
def alfred_command():
    """Forward a voice command to the Alfred agent."""
    body = request.get_json(silent=True) or {}
    transcript = (body.get('transcript', '') or '').strip()
    context = body.get('context')
    history = body.get('history')

    if not transcript:
        return jsonify({'error': 'Transcript is required'}), 400
    if len(transcript) > 1000:
        transcript = transcript[:1000]

    bridge = _get_alfred_bridge()
    if bridge is None:
        return jsonify({
            'error': 'Alfred is not configured. Enable it in Settings > APIs.',
        }), 503

    result = bridge.send_command(transcript, context=context, history=history)
    return jsonify(result)


@app.route('/api/alfred/health', methods=['GET'])
def alfred_health():
    """Check if Alfred agent is reachable."""
    bridge = _get_alfred_bridge()
    if bridge is None:
        return jsonify({'available': False, 'reason': 'not_configured'})
    return jsonify(bridge.health_check())


@app.route('/api/alfred/reset', methods=['POST'])
def alfred_reset():
    """Reset the Alfred bridge — forces re-init on next request."""
    global _alfred_bridge
    _alfred_bridge = None
    logger.info('Alfred bridge reset')
    return jsonify({'ok': True, 'message': 'Alfred bridge reset. Will reconnect on next command.'})


_tmdb_detail_cache: Dict[str, tuple] = {}  # key → (timestamp, data)
_tmdb_detail_cache_lock = threading.Lock()
_TMDB_DETAIL_TTL = 30 * 60  # 30 minutes
_TMDB_DETAIL_CACHE_MAX = 500


# Guards against Plex guids that point to the wrong TMDb entity (e.g. a TV
# show tagged with a movie tmdb_id from a bad agent match). A 404 from TMDb
# for the expected type means the id does not exist there.
_tmdb_type_validation_cache: Dict[str, tuple] = {}  # "{type}:{id}" → (ts, bool)
_tmdb_type_validation_lock = threading.Lock()
_TMDB_TYPE_VALIDATION_TTL = 7 * 24 * 3600  # 7 days


def _validate_tmdb_id_type(tmdb_id: Optional[int], expected_type: str) -> Optional[bool]:
    """Check that tmdb_id resolves to expected_type ('movie'|'tv') on TMDb.

    Returns True/False when we got a definitive answer, None when we can't
    tell (missing API key, transient network error) — callers should treat
    None as "trust what we have" to avoid punishing users for TMDb outages.
    """
    if not tmdb_id or expected_type not in ('movie', 'tv'):
        return None
    tmdb_api_key = settings.get('apis.tmdb.api_key', '') or Config.TMDB_API_KEY
    if not tmdb_api_key:
        return None

    cache_key = f"{expected_type}:{tmdb_id}"
    now = time.time()
    with _tmdb_type_validation_lock:
        cached = _tmdb_type_validation_cache.get(cache_key)
        if cached:
            ts, ok = cached
            if now - ts < _TMDB_TYPE_VALIDATION_TTL:
                return ok

    try:
        resp = requests.get(
            f'https://api.themoviedb.org/3/{expected_type}/{tmdb_id}',
            params={'api_key': tmdb_api_key},
            timeout=6,
        )
        if resp.status_code == 404:
            result = False
        elif resp.ok:
            result = True
        else:
            return None
    except Exception as exc:
        logger.warning('TMDb type validation %s/%s failed: %s',
                       expected_type, tmdb_id, exc)
        return None

    with _tmdb_type_validation_lock:
        _tmdb_type_validation_cache[cache_key] = (now, result)
    return result


def _evict_tmdb_detail_cache():
    """Purge expired entries; if still over cap, drop oldest by insertion time."""
    now = time.time()
    with _tmdb_detail_cache_lock:
        # Remove expired
        expired_keys = [k for k, (ts, _) in _tmdb_detail_cache.items()
                        if now - ts >= _TMDB_DETAIL_TTL]
        for k in expired_keys:
            del _tmdb_detail_cache[k]
        # If still over cap, evict oldest
        if len(_tmdb_detail_cache) > _TMDB_DETAIL_CACHE_MAX:
            sorted_keys = sorted(_tmdb_detail_cache, key=lambda k: _tmdb_detail_cache[k][0])
            for k in sorted_keys[:len(_tmdb_detail_cache) - _TMDB_DETAIL_CACHE_MAX]:
                del _tmdb_detail_cache[k]

def _plex_img(path):
    return f'/api/img/plex?url={urllib.parse.quote(path, safe="/")}' if path else None


def _plex_details(kind: str, tmdb_id: int) -> Optional[Dict]:
    """TMDb-shaped details for an owned title, built only from Plex.

    Used when no TMDB key is set (or TMDb fails) so owned movies and shows
    still open with full artwork, cast and seasons.
    """
    try:
        index = plex_service.build_ownership_index() or {}
    except Exception as e:
        logger.debug(f"Plex details: ownership index unavailable: {e}")
        return None
    entry = (index.get('movies_by_tmdb' if kind == 'movie' else 'tv_by_tmdb') or {}).get(int(tmdb_id))
    rk = (entry or {}).get('rating_key')
    if not rk:
        return None
    meta = plex_service.get_metadata(int(rk))
    if not meta:
        return None
    date = meta.get('originallyAvailableAt') or (f"{meta['year']}" if meta.get('year') else '')
    rating = meta.get('audienceRating') or meta.get('rating')
    out: Dict[str, Any] = {
        'id': int(tmdb_id),
        'overview': meta.get('summary', ''),
        'tagline': meta.get('tagline', ''),
        'poster_path': _plex_img(meta.get('thumbPath')),
        'backdrop_path': _plex_img(meta.get('artPath')),
        'genres': [{'id': 0, 'name': g} for g in meta.get('genres') or []],
        'vote_average': rating or 0,
        'vote_count': 0,
        'imdb_id': meta.get('imdb_id'),
        'credits': {
            'cast': [{'id': c.get('actor_id') or 0, 'name': c.get('tag', ''), 'character': c.get('role', ''),
                      'profile_path': _plex_img(c.get('thumb')) if (c.get('thumb') or '').startswith('/') else c.get('thumb')}
                     for c in (meta.get('cast') or [])[:30]],
            'crew': [{'id': 0, 'name': d.get('name', ''), 'job': 'Director', 'department': 'Directing'}
                     for d in meta.get('directors') or []],
        },
        'videos': {'results': []},
        'production_companies': [{'id': 0, 'name': meta['studio']}] if meta.get('studio') else [],
        'plex_rating_key': int(rk),
        '_source': 'plex',
    }
    if kind == 'movie':
        out.update(title=meta.get('title', ''), release_date=date,
                   runtime=round((meta.get('duration_ms') or 0) / 60000) or None,
                   release_dates={'results': [{'iso_3166_1': 'US', 'release_dates': [
                       {'certification': meta.get('content_rating', ''), 'type': 3}]}]})
    else:
        seasons = []
        try:
            for se in plex_service.get_show_children(int(rk)):
                seasons.append({'season_number': se.get('index'), 'name': se.get('title', ''),
                                'episode_count': se.get('leafCount', 0),
                                'poster_path': _plex_img(se.get('thumbPath')), 'id': se.get('ratingKey')})
        except Exception:
            pass
        out.update(name=meta.get('title', ''), first_air_date=date,
                   number_of_seasons=len([x for x in seasons if x.get('season_number')]),
                   number_of_episodes=meta.get('leafCount') or sum(x.get('episode_count') or 0 for x in seasons),
                   seasons=seasons, episode_run_time=[],
                   external_ids={'tvdb_id': meta.get('tvdb_id'), 'imdb_id': meta.get('imdb_id')},
                   content_ratings={'results': [{'iso_3166_1': 'US', 'rating': meta.get('content_rating', '')}]})
    return out


@app.route('/api/movie/<int:tmdb_id>')
def movie_details(tmdb_id):
    """Get detailed information about a specific movie (cached 30m)."""
    cache_key = f"movie:{tmdb_id}"
    _evict_tmdb_detail_cache()
    with _tmdb_detail_cache_lock:
        cached = _tmdb_detail_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _TMDB_DETAIL_TTL:
            return jsonify(data)

    details = tmdb.get_movie_details(tmdb_id) or _plex_details('movie', tmdb_id)
    if not details:
        if not tmdb.api_key:
            return jsonify({'error': 'TMDb not configured', 'needs_setup': 'tmdb'}), 404
        return jsonify({'error': 'Movie not found'}), 404

    with _tmdb_detail_cache_lock:
        _tmdb_detail_cache[cache_key] = (time.time(), details)
    return jsonify(details)

@app.route('/api/movie/<int:tmdb_id>/similar')
def movie_similar(tmdb_id):
    """Similar movies split for the detail modal (cached 30m).

    Preferred UX:
      - owned  = Plex Related Movies hub (exact same list as the Plex app)
      - unowned = Trakt /movies/{id}/related, minus anything already owned
    TMDb recommendations+similar is only a fallback when Trakt fails/empty.

    Optional query params:
      rating_key — Plex ratingKey when the source title is in the library
      imdb_id    — preferred Trakt identifier (tt…)
      limit      — max items per section (default 24)
    """
    limit = request.args.get('limit', 24, type=int) or 24
    limit = max(1, min(limit, 40))
    rating_key_arg = request.args.get('rating_key', type=int)
    imdb_id_arg = (request.args.get('imdb_id') or '').strip() or None

    cache_key = (
        f"movie_similar_v2:{tmdb_id}:{rating_key_arg or 0}:"
        f"{imdb_id_arg or ''}:{limit}"
    )
    _evict_tmdb_detail_cache()
    with _tmdb_detail_cache_lock:
        cached = _tmdb_detail_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _TMDB_DETAIL_TTL:
            return jsonify(data)

    # --- resolve Plex ratingKey for owned Related Movies -----------------
    rating_key = rating_key_arg
    if not rating_key:
        # Ownership index first-wins can land on a 4K/secondary copy whose
        # Related Movies hub is junk. Prefer the primary "Movies" library
        # copy when the caller did not pass an explicit ratingKey.
        try:
            index_early = plex_service.build_ownership_index()
            by_tmdb = index_early.get('movies_by_tmdb') or {}
            entry = by_tmdb.get(int(tmdb_id))
            candidates = []
            if entry:
                title_key = (entry.get('title') or '').lower()
                candidates = list(
                    (index_early.get('movies_by_title') or {}).get(title_key) or []
                )
                if not candidates:
                    candidates = [entry]

            def _rk_score(c):
                name = (c.get('library_name') or '').lower()
                score = 0
                if name == 'movies':
                    score += 100
                if '4k' in name:
                    score -= 50
                if c.get('rating'):
                    score += 5
                return score

            candidates = [c for c in candidates if c.get('rating_key')]
            candidates.sort(key=_rk_score, reverse=True)
            if candidates:
                rating_key = int(candidates[0]['rating_key'])
        except Exception as e:
            logger.warning(
                'similar: ownership index resolve failed for tmdb=%s: %s', tmdb_id, e
            )
        if not rating_key:
            try:
                rating_key = _resolve_rating_key_by_guid('tmdb', str(tmdb_id))
            except Exception as e:
                logger.warning('similar: guid resolve failed for tmdb=%s: %s', tmdb_id, e)
                rating_key = None

    # --- owned: Plex Related Movies hub ----------------------------------
    owned_raw = []
    if rating_key:
        try:
            owned_raw = plex_service.get_related_movies(int(rating_key)) or []
        except Exception as e:
            logger.warning('similar: plex related failed rk=%s: %s', rating_key, e)
            owned_raw = []

    owned = []
    owned_tmdb_ids = {int(tmdb_id)}
    for item in owned_raw:
        tid = item.get('tmdb_id')
        if tid:
            try:
                owned_tmdb_ids.add(int(tid))
            except (TypeError, ValueError):
                pass
        rk = item.get('ratingKey') or item.get('rating_key')
        thumb = item.get('thumbPath')
        # Prefer Plex proxy poster so tiles render without a TMDb round-trip;
        # posterUrl() on the frontend passes /api/ paths through unchanged.
        poster_path = f'/api/img/plex?url={thumb}' if thumb else (
            f'/api/img/plex?url=/library/metadata/{rk}/thumb&r=2' if rk else None
        )
        art = item.get('artPath')
        backdrop_path = f'/api/img/plex?url={art}' if art else None
        owned.append({
            'tmdb_id': tid,
            'title': item.get('title') or '',
            'year': item.get('year'),
            'poster_path': poster_path,
            'backdrop_path': backdrop_path,
            'overview': item.get('summary') or '',
            'vote_average': item.get('rating'),
            'rating': item.get('rating'),
            'imdb_id': item.get('imdb_id'),
            'rating_key': rk,
            'owned': True,
            'media_type': 'movie',
            'source': 'plex',
        })

    # Also treat anything in the library ownership index as "owned" so Trakt
    # recommendations already on disk never land in the unowned rail.
    try:
        index = plex_service.build_ownership_index()
        lib_by_tmdb = index.get('movies_by_tmdb') or {}
        for tid in list(lib_by_tmdb.keys()):
            try:
                owned_tmdb_ids.add(int(tid))
            except (TypeError, ValueError):
                pass
    except Exception as e:
        logger.warning('similar: ownership index unavailable: %s', e)
        lib_by_tmdb = {}

    # --- resolve imdb id for Trakt ---------------------------------------
    imdb_id = imdb_id_arg
    if not imdb_id and rating_key:
        try:
            meta = plex_service.get_metadata(int(rating_key)) or {}
            imdb_id = meta.get('imdb_id') or None
        except Exception:
            pass
    if not imdb_id:
        try:
            details = tmdb.get_movie_details(int(tmdb_id)) or {}
            imdb_id = details.get('imdb_id') or None
            if not imdb_id:
                ext = details.get('external_ids') or {}
                if isinstance(ext, dict):
                    imdb_id = ext.get('imdb_id') or None
        except Exception as e:
            logger.warning('similar: tmdb details for imdb failed: %s', e)

    # --- unowned: Trakt related, TMDb fallback ---------------------------
    unowned = []
    unowned_source = None
    trakt_movies = _fetch_trakt_movie_related(imdb_id=imdb_id, tmdb_id=tmdb_id, limit=max(limit * 3, 40))
    if trakt_movies:
        unowned_source = 'trakt'
        unowned = _format_trakt_related_movies(
            trakt_movies, owned_tmdb_ids=owned_tmdb_ids, lib_by_tmdb=lib_by_tmdb, limit=limit,
        )
    if not unowned:
        unowned_source = 'tmdb'
        unowned = _format_tmdb_similar_as_unowned(
            tmdb_id, owned_tmdb_ids=owned_tmdb_ids, lib_by_tmdb=lib_by_tmdb, limit=limit,
        )

    # IMDb display scores for both rails
    enrich_imdb_ratings(owned, settings=settings, max_items=40,
                        replace_rating=True, rating_field='vote_average')
    for m in owned:
        m['rating'] = m.get('vote_average')
    enrich_imdb_ratings(unowned, settings=settings, max_items=40,
                        replace_rating=True, rating_field='vote_average')
    for m in unowned:
        m['rating'] = m.get('vote_average')

    payload = {
        'owned': owned[:limit],
        'unowned': unowned[:limit],
        'movies': (owned + unowned)[:limit],  # back-compat flat list
        'total_owned': len(owned),
        'total_unowned': len(unowned),
        'total': len(owned) + len(unowned),
        'sources': {
            'owned': 'plex' if owned else None,
            'unowned': unowned_source,
        },
        'rating_key': rating_key,
        'imdb_id': imdb_id,
    }
    with _tmdb_detail_cache_lock:
        _tmdb_detail_cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


def _fetch_trakt_movie_related(imdb_id=None, tmdb_id=None, limit: int = 24):
    """Fetch Trakt related movies. Prefer imdb id; resolve via tmdb search else.

    Bare numeric ids are Trakt ids (wrong for TMDb 603 → French comedies), and
    ``tmdb:N`` / ``tmdb-N`` returned empty in practice, so we never pass a bare
    TMDb id straight to /related.
    """
    enabled = settings.get('apis.trakt.enabled', False)
    client_id = settings.get('apis.trakt.client_id', '')
    if not enabled or not client_id:
        return []

    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': client_id,
    }

    ident = None
    if imdb_id:
        ident = imdb_id if str(imdb_id).startswith('tt') else f'tt{imdb_id}'
    elif tmdb_id:
        cache_key = f'trakt_resolve_tmdb:{tmdb_id}'
        cached = _trakt_cache.get(cache_key)
        if cached and time.time() - cached[0] < _TRAKT_CACHE_TTL:
            ident = cached[1]
        else:
            try:
                resp = requests.get(
                    f'https://api.trakt.tv/search/tmdb/{int(tmdb_id)}',
                    headers=headers,
                    params={'type': 'movie'},
                    timeout=10,
                )
                if resp.status_code == 200:
                    results = resp.json() or []
                    if results:
                        ids = (results[0].get('movie') or {}).get('ids') or {}
                        ident = ids.get('imdb') or (
                            str(ids['trakt']) if ids.get('trakt') else None
                        )
                _set_trakt_cache(cache_key, ident)
            except Exception as e:
                logger.warning('similar: trakt tmdb search failed: %s', e)
                ident = None

    if not ident:
        return []

    cache_key = f'trakt_movie_related:{ident}:{limit}'
    cached = _trakt_cache.get(cache_key)
    if cached and time.time() - cached[0] < _TRAKT_CACHE_TTL:
        return cached[1]

    try:
        resp = requests.get(
            f'https://api.trakt.tv/movies/{ident}/related',
            headers=headers,
            params={'limit': limit, 'extended': 'full'},
            timeout=10,
        )
        if resp.status_code == 429:
            logger.warning('similar: trakt related rate-limited')
            return []
        resp.raise_for_status()
        data = resp.json() or []
        _set_trakt_cache(cache_key, data)
        return data
    except Exception as e:
        logger.warning('similar: trakt related failed for %s: %s', ident, e)
        return []


def _resolve_trakt_movie_images(tmdb_ids):
    """Map tmdb_id → (poster_path, backdrop_path) using the shared trakt image cache."""
    tmdb_api_key = settings.get('apis.tmdb.api_key', '')
    img_map = {}
    ids_to_fetch = []
    for tid in tmdb_ids:
        if not tid:
            continue
        tmdb_cache_key = f'trakt_tmdb_movie_img:{tid}'
        img_cached = _trakt_cache.get(tmdb_cache_key)
        if img_cached and time.time() - img_cached[0] < _TRAKT_CACHE_TTL:
            img_map[tid] = (
                img_cached[1].get('poster_path'),
                img_cached[1].get('backdrop_path'),
            )
        else:
            ids_to_fetch.append(tid)

    def _one(tid):
        if not tid or not tmdb_api_key:
            return tid, None, None
        try:
            tmdb_resp = requests.get(
                f'https://api.themoviedb.org/3/movie/{tid}',
                params={'api_key': tmdb_api_key},
                timeout=5,
            )
            if tmdb_resp.status_code == 200:
                tmdb_data = tmdb_resp.json()
                poster = tmdb_data.get('poster_path')
                backdrop = tmdb_data.get('backdrop_path')
                _set_trakt_cache(
                    f'trakt_tmdb_movie_img:{tid}',
                    {'poster_path': poster, 'backdrop_path': backdrop},
                )
                return tid, poster, backdrop
        except Exception:
            pass
        return tid, None, None

    if ids_to_fetch:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for tid, poster, backdrop in pool.map(_one, ids_to_fetch):
                if tid:
                    img_map[tid] = (poster, backdrop)
    return img_map


def _format_trakt_related_movies(raw_movies, owned_tmdb_ids, lib_by_tmdb, limit: int):
    """Shape Trakt related rows for the modal; drop owned / no-poster titles."""
    tmdb_ids = []
    for movie in raw_movies:
        ids = movie.get('ids') or {}
        tid = ids.get('tmdb')
        if tid:
            tmdb_ids.append(tid)
    img_map = _resolve_trakt_movie_images(tmdb_ids)

    out = []
    seen = set(owned_tmdb_ids)
    for movie in raw_movies:
        ids = movie.get('ids') or {}
        tid = ids.get('tmdb')
        if not tid:
            continue
        try:
            tid_i = int(tid)
        except (TypeError, ValueError):
            continue
        if tid_i in seen:
            continue
        # Double-check library membership (index may lag Guid parse)
        if tid_i in (lib_by_tmdb or {}):
            continue
        poster_path, backdrop_path = img_map.get(tid_i, (None, None))
        if not poster_path:
            continue
        seen.add(tid_i)
        out.append({
            'tmdb_id': tid_i,
            'title': movie.get('title') or '',
            'year': movie.get('year'),
            'poster_path': poster_path,
            'backdrop_path': backdrop_path,
            'overview': movie.get('overview') or '',
            'vote_average': movie.get('rating'),
            'rating': movie.get('rating'),
            'imdb_id': ids.get('imdb'),
            'trakt_id': ids.get('trakt'),
            'rating_key': None,
            'owned': False,
            'media_type': 'movie',
            'source': 'trakt',
        })
        if len(out) >= limit:
            break
    return out


def _format_tmdb_similar_as_unowned(tmdb_id, owned_tmdb_ids, lib_by_tmdb, limit: int):
    """TMDb recommendations+similar fallback shaped like the unowned rail."""
    results = tmdb.get_movie_similar(tmdb_id) or []
    out = []
    seen = set(owned_tmdb_ids)
    for r in results:
        rid = r.get('id')
        if not rid or not r.get('poster_path'):
            continue
        try:
            rid_i = int(rid)
        except (TypeError, ValueError):
            continue
        if rid_i in seen or rid_i in (lib_by_tmdb or {}):
            continue
        seen.add(rid_i)
        release = r.get('release_date') or ''
        out.append({
            'tmdb_id': rid_i,
            'title': r.get('title') or r.get('original_title') or '',
            'year': int(release[:4]) if release[:4].isdigit() else None,
            'poster_path': r.get('poster_path'),
            'backdrop_path': r.get('backdrop_path'),
            'overview': r.get('overview') or '',
            'vote_average': r.get('vote_average'),
            'rating': r.get('vote_average'),
            'vote_count': r.get('vote_count') or 0,
            'popularity': r.get('popularity') or 0.0,
            'rating_key': None,
            'owned': False,
            'media_type': 'movie',
            'source': 'tmdb',
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda m: (m.get('popularity') or 0, m.get('vote_count') or 0), reverse=True)
    return out[:limit]


@app.route('/api/tv/<int:tmdb_id>')
def tv_details(tmdb_id):
    """Get detailed information about a specific TV show from TMDb (cached 30m)."""
    cache_key = f"tv:{tmdb_id}"
    _evict_tmdb_detail_cache()
    with _tmdb_detail_cache_lock:
        cached = _tmdb_detail_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _TMDB_DETAIL_TTL:
            # Seasons light warm can populate this cache without credits — refetch
            # so Cast & Crew always gets TMDb tv credits when available.
            if isinstance(data, dict) and data.get("credits") is not None:
                return jsonify(data)

    details = tmdb.get_tv_details(tmdb_id) or _plex_details('tv', tmdb_id)
    if not details:
        if not tmdb.api_key:
            return jsonify({'error': 'TMDb not configured', 'needs_setup': 'tmdb'}), 404
        return jsonify({'error': 'TV show not found'}), 404

    with _tmdb_detail_cache_lock:
        _tmdb_detail_cache[cache_key] = (time.time(), details)
    return jsonify(details)

@app.route('/api/plex/tv/<int:rating_key>/seasons')
def plex_tv_seasons(rating_key):
    """Get season children for a TV show from Plex."""
    try:
        seasons = plex_service.get_show_children(rating_key)
        return jsonify({'seasons': seasons})
    except Exception as e:
        logger.error(f"Error fetching Plex TV seasons for {rating_key}: {e}")
        return jsonify({'error': str(e), 'seasons': []}), 500


@app.route('/api/plex/tv/<int:rating_key>')
def plex_tv_metadata(rating_key):
    """Get TV show metadata from Plex by rating key (includes guids for tmdb_id lookup).

    If the Plex agent doesn't embed a TMDb ID we attempt to resolve one via:
    1. TMDb Find API using the embedded TVDb ID
    2. TMDb search by title + year as a last resort
    """
    try:
        data = plex_service.get_metadata(rating_key)
        if not data:
            return jsonify({'error': 'Show not found'}), 404

        # Discard a Plex-supplied tmdb_id that points to the wrong media type
        # (e.g. a bad agent match where a show's guid resolves to a movie id).
        # Clearing it lets the fallback strategies below re-resolve correctly.
        plex_tmdb_id = data.get('tmdb_id')
        if plex_tmdb_id and _validate_tmdb_id_type(plex_tmdb_id, 'tv') is False:
            logger.warning(
                'Plex show ratingKey=%s has tmdb_id=%s that is not a TV show on TMDb — discarding',
                rating_key, plex_tmdb_id,
            )
            data['tmdb_id'] = None

        # Resolve missing tmdb_id for older Plex agents
        if not data.get('tmdb_id'):
            tmdb_api_key = settings.get('apis.tmdb.api_key', '') or Config.TMDB_API_KEY
            resolved = False

            # Strategy 1: TMDb Find by tvdb_id
            tvdb_id = data.get('tvdb_id')
            if tvdb_id and tmdb_api_key:
                try:
                    resp = requests.get(
                        f'https://api.themoviedb.org/3/find/{tvdb_id}',
                        params={'api_key': tmdb_api_key, 'external_source': 'tvdb_id'},
                        timeout=8,
                    )
                    if resp.ok:
                        find_data = resp.json()
                        tv_results = find_data.get('tv_results', [])
                        if tv_results:
                            data['tmdb_id'] = tv_results[0]['id']
                            resolved = True
                            logger.info('Resolved tmdb_id=%s for tvdb_id=%s via TMDb Find', data['tmdb_id'], tvdb_id)
                except Exception as exc:
                    logger.warning('TMDb Find by tvdb_id=%s failed: %s', tvdb_id, exc)

            # Strategy 2: TMDb search by title + year
            if not resolved and tmdb_api_key and data.get('title'):
                try:
                    params = {'api_key': tmdb_api_key, 'query': data['title']}
                    if data.get('year'):
                        params['first_air_date_year'] = data['year']
                    resp = requests.get(
                        'https://api.themoviedb.org/3/search/tv',
                        params=params,
                        timeout=8,
                    )
                    if resp.ok:
                        results = resp.json().get('results', [])
                        if results:
                            data['tmdb_id'] = results[0]['id']
                            logger.info('Resolved tmdb_id=%s for "%s" via TMDb search', data['tmdb_id'], data['title'])
                except Exception as exc:
                    logger.warning('TMDb search for "%s" failed: %s', data.get('title'), exc)

        return jsonify(data)
    except Exception as e:
        logger.error(f"Error fetching Plex TV metadata for {rating_key}: {e}")
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Plex episode metadata cache — short TTL because watch state can change
# ---------------------------------------------------------------------------
_plex_episode_cache: Dict[str, tuple] = {}   # key → (timestamp, data)
_PLEX_EPISODE_CACHE_TTL = 5 * 60             # 5 minutes
_PLEX_EPISODE_CACHE_MAX = 200                # max entries


@app.route('/api/plex/episode/<int:rating_key>')
def plex_episode_metadata(rating_key):
    """Get episode metadata from Plex by rating key.

    Returns episode details including title, summary, duration, ratings,
    watch state, season/episode numbers, and parent show information.
    All image URLs use the proxy format (no token exposure).
    Cached in-memory for 5 minutes (watch state can change).
    """
    cache_key = f"plex_ep:{rating_key}"

    # Check cache
    cached = _plex_episode_cache.get(cache_key)
    if cached:
        ts, cached_data = cached
        if time.time() - ts < _PLEX_EPISODE_CACHE_TTL:
            return jsonify(cached_data)
        del _plex_episode_cache[cache_key]

    try:
        data = plex_service.get_metadata(rating_key)
        if not data:
            return jsonify({'error': 'Episode not found'}), 404

        # Verify this is actually an episode
        if data.get('type') != 'episode':
            return jsonify({'error': 'Item is not an episode', 'type': data.get('type')}), 400

        response = {
            'title': data.get('title', ''),
            'summary': data.get('summary', ''),
            'duration': data.get('duration', 0),
            'rating': data.get('rating'),
            'audience_rating': data.get('audienceRating'),
            'content_rating': data.get('contentRating', ''),
            'originally_available_at': data.get('originallyAvailableAt'),
            'view_count': data.get('viewCount', 0),
            'view_offset': data.get('viewOffset', 0),
            'thumb_path': data.get('thumbPath'),
            'art_path': data.get('artPath'),
            'show_thumb_path': data.get('showThumbPath'),
            'season_number': data.get('season_number'),
            'episode_number': data.get('episode_number'),
            'show_title': data.get('show_title', ''),
            'show_rating_key': data.get('show_rating_key'),
            'rating_key': data.get('ratingKey'),
        }

        # Cache with max-size eviction
        _plex_episode_cache[cache_key] = (time.time(), response)
        if len(_plex_episode_cache) > _PLEX_EPISODE_CACHE_MAX:
            # Evict oldest entry
            oldest_key = min(_plex_episode_cache, key=lambda k: _plex_episode_cache[k][0])
            del _plex_episode_cache[oldest_key]

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error fetching Plex episode metadata for {rating_key}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/popular')
def popular_movies():
    """Get popular movies."""
    page = request.args.get('page', 1, type=int)
    movies = tmdb.get_popular(page)
    return jsonify({'results': movies, 'page': page})

@app.route('/api/trending')
def trending_content():
    """Get trending movies and TV shows."""
    media_type = request.args.get('type', 'movie')
    time_window = request.args.get('window', 'week')
    content = tmdb.get_trending(media_type, time_window)
    return jsonify({'results': content, 'type': media_type, 'window': time_window})


# ---------------------------------------------------------------------------
# Trakt trending shows — cached proxy (keeps client_id server-side)
# ---------------------------------------------------------------------------
_trakt_cache: Dict[str, tuple] = {}   # key → (timestamp, data)
_TRAKT_CACHE_TTL = 15 * 60            # 15 minutes
_TRAKT_CACHE_MAX = 1000               # cap total entries (per-tmdb image keys never expire otherwise)


def _set_trakt_cache(key: str, value) -> None:
    _trakt_cache[key] = (time.time(), value)
    if len(_trakt_cache) > _TRAKT_CACHE_MAX:
        oldest = min(_trakt_cache.items(), key=lambda kv: kv[1][0])[0]
        del _trakt_cache[oldest]


@app.route('/api/trakt/shows/trending')
def trakt_trending_shows():
    """Proxy Trakt trending TV shows with in-memory caching + rate-limit handling."""
    # Gate on enabled + client_id
    enabled = settings.get('apis.trakt.enabled', False)
    client_id = settings.get('apis.trakt.client_id', '')
    if not enabled or not client_id:
        return jsonify({'shows': [], 'enabled': False})

    limit = request.args.get('limit', '20')
    page = request.args.get('page', '1')
    cache_key = f"trakt_trending_shows:{page}:{limit}"

    # Check cache
    cached = _trakt_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _TRAKT_CACHE_TTL:
            return jsonify(data)
        _trakt_cache.pop(cache_key, None)

    try:
        resp = requests.get(
            'https://api.trakt.tv/shows/trending',
            headers={
                'Content-Type': 'application/json',
                'trakt-api-version': '2',
                'trakt-api-key': client_id,
            },
            params={'page': page, 'limit': limit, 'extended': 'full'},
            timeout=10,
        )

        # Handle rate limiting (429)
        if resp.status_code == 429:
            retry_after = resp.headers.get('Retry-After', '30')
            logger.warning(f"Trakt 429 rate-limited, retry after {retry_after}s")
            return jsonify({
                'shows': [],
                'enabled': True,
                'error': f'Trakt rate limited — retry in {retry_after}s',
            }), 429

        resp.raise_for_status()
        raw_items = resp.json()  # list of {watchers, show: {...}}

        # Normalize to our rail shape, including tmdb_id for poster resolution
        tmdb_api_key = settings.get('apis.tmdb.api_key', '')

        # Resolve TMDb images in parallel (was serial — 20 sequential HTTP calls)
        def _resolve_show_images(tmdb_id):
            if not tmdb_id or not tmdb_api_key:
                return tmdb_id, None, None
            tmdb_cache_key = f"trakt_tmdb_img:{tmdb_id}"
            img_cached = _trakt_cache.get(tmdb_cache_key)
            if img_cached and time.time() - img_cached[0] < _TRAKT_CACHE_TTL:
                return tmdb_id, img_cached[1].get('poster_path'), img_cached[1].get('backdrop_path')
            try:
                tmdb_resp = requests.get(
                    f'https://api.themoviedb.org/3/tv/{tmdb_id}',
                    params={'api_key': tmdb_api_key},
                    timeout=5,
                )
                if tmdb_resp.status_code == 200:
                    tmdb_data = tmdb_resp.json()
                    poster = tmdb_data.get('poster_path')
                    backdrop = tmdb_data.get('backdrop_path')
                    _set_trakt_cache(tmdb_cache_key, {'poster_path': poster, 'backdrop_path': backdrop})
                    return tmdb_id, poster, backdrop
            except Exception:
                pass
            return tmdb_id, None, None

        # Collect IDs that need fetching, skip cache hits
        img_map = {}  # tmdb_id → (poster, backdrop)
        ids_to_fetch = []
        for entry in raw_items:
            tmdb_id = entry.get('show', {}).get('ids', {}).get('tmdb')
            if not tmdb_id or not tmdb_api_key:
                continue
            tmdb_cache_key = f"trakt_tmdb_img:{tmdb_id}"
            img_cached = _trakt_cache.get(tmdb_cache_key)
            if img_cached and time.time() - img_cached[0] < _TRAKT_CACHE_TTL:
                img_map[tmdb_id] = (img_cached[1].get('poster_path'), img_cached[1].get('backdrop_path'))
            else:
                ids_to_fetch.append(tmdb_id)

        if ids_to_fetch:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for tid, poster, backdrop in pool.map(_resolve_show_images, ids_to_fetch):
                    if tid:
                        img_map[tid] = (poster, backdrop)

        shows = []
        for entry in raw_items:
            show = entry.get('show', {})
            ids = show.get('ids', {})
            tmdb_id = ids.get('tmdb')
            poster_path, backdrop_path = img_map.get(tmdb_id, (None, None))

            shows.append({
                'title': show.get('title', ''),
                'year': show.get('year'),
                'overview': show.get('overview', ''),
                'trakt_id': ids.get('trakt'),
                'tmdb_id': tmdb_id,
                'imdb_id': ids.get('imdb'),
                'tvdb_id': ids.get('tvdb'),
                'poster_path': poster_path,
                'backdrop_path': backdrop_path,
                'watchers': entry.get('watchers', 0),
                'rating': show.get('rating'),
                'media_type': 'tv',
            })

        enrich_imdb_ratings(shows, settings=settings, max_items=40, replace_rating=True)

        payload = {'shows': shows, 'enabled': True, 'total': len(shows)}
        _set_trakt_cache(cache_key, payload)
        return jsonify(payload)

    except requests.exceptions.Timeout:
        logger.error("Trakt API timed out (10s)")
        return jsonify({'shows': [], 'enabled': True, 'error': 'Trakt API timed out'}), 504
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to Trakt API")
        return jsonify({'shows': [], 'enabled': True, 'error': 'Could not connect to Trakt'}), 502
    except Exception as e:
        logger.error(f"Trakt trending error: {e}")
        return jsonify({'shows': [], 'enabled': True, 'error': str(e)}), 500


@app.route('/api/rails/what-youre-missing')
def rails_what_youre_missing():
    """Daily 'What You're Missing' shelf — popular unowned movies (manual grab only)."""
    try:
        from app import daily_missing
        limit = request.args.get('limit', daily_missing.RAIL_SIZE, type=int)
        limit = max(1, min(limit or daily_missing.RAIL_SIZE, 40))
        return jsonify(daily_missing.get_rail(limit=limit))
    except Exception as e:
        logger.error(f"what-youre-missing rail error: {e}")
        return jsonify({
            'rail_id': 'what_youre_missing',
            'title': "What You're Missing",
            'movies': [],
            'enabled': True,
            'empty': True,
            'error': str(e),
        }), 500


@app.route('/api/rails/what-youre-missing/refresh', methods=['POST'])
def rails_what_youre_missing_refresh():
    """Force rebuild the candidate pool (ops / smoke)."""
    try:
        from app import daily_missing
        payload = daily_missing.refresh_now()
        return jsonify({
            'ok': True,
            'pool_size': len(payload.get('candidates') or []),
            'built_at_iso': payload.get('built_at_iso'),
            'note': payload.get('note'),
        })
    except Exception as e:
        logger.error(f"what-youre-missing refresh error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/rails/what-youre-missing/dismiss', methods=['POST'])
def rails_what_youre_missing_dismiss():
    """Permanently dismiss a title from What You're Missing (by tmdb_id)."""
    try:
        from app import daily_missing
        body = request.get_json(silent=True) or {}
        raw = body.get('tmdb_id')
        if raw is None:
            return jsonify({'ok': False, 'error': 'tmdb_id required'}), 400
        try:
            tmdb_id = int(raw)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'tmdb_id must be an int'}), 400
        if tmdb_id <= 0:
            return jsonify({'ok': False, 'error': 'tmdb_id must be positive'}), 400
        result = daily_missing.dismiss(tmdb_id)
        # Return refreshed slate so clients can top-up immediately.
        rail = daily_missing.get_rail(limit=daily_missing.RAIL_SIZE)
        return jsonify({**result, 'rail': rail})
    except Exception as e:
        logger.error(f"what-youre-missing dismiss error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/rails/what-youre-missing/dismissed')
def rails_what_youre_missing_dismissed():
    """Debug: list permanently dismissed What You're Missing tmdb_ids."""
    try:
        from app import daily_missing
        return jsonify(daily_missing.list_dismissed())
    except Exception as e:
        logger.error(f"what-youre-missing dismissed list error: {e}")
        return jsonify({'tmdb_ids': [], 'count': 0, 'error': str(e)}), 500


@app.route('/api/collections/incomplete')
def collections_incomplete():
    """Incomplete TMDb movie collections for Movies + Recently Released."""
    try:
        from app import movie_collections
        movie_collections.ensure_warm()
        return jsonify(movie_collections.get_incomplete_list())
    except Exception as e:
        logger.error(f"collections incomplete error: {e}")
        return jsonify({
            'collections': [],
            'building': False,
            'error': str(e),
        }), 500


@app.route('/api/collections/<int:collection_id>')
def collections_detail(collection_id: int):
    """Collection detail with ordered parts (owned / missing / upcoming)."""
    try:
        from app import movie_collections
        detail = movie_collections.get_collection_detail(collection_id)
        if not detail:
            return jsonify({'error': 'Collection not found or complete'}), 404
        return jsonify(detail)
    except Exception as e:
        logger.error(f"collections detail error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/collections/refresh', methods=['POST'])
def collections_refresh():
    """Kick a background rescan of incomplete collections."""
    try:
        from app import movie_collections
        import threading

        def _run():
            try:
                movie_collections.refresh_now()
            except Exception as err:
                logger.error(f"collections refresh worker error: {err}")

        threading.Thread(target=_run, name='collections-refresh', daemon=True).start()
        return jsonify({'ok': True, 'started': True})
    except Exception as e:
        logger.error(f"collections refresh error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/trakt/movies/trending')
def trakt_trending_movies():
    """Proxy Trakt trending movies with in-memory caching + rate-limit handling."""
    enabled = settings.get('apis.trakt.enabled', False)
    client_id = settings.get('apis.trakt.client_id', '')
    if not enabled or not client_id:
        return jsonify({'movies': [], 'enabled': False})

    limit = request.args.get('limit', '20')
    page = request.args.get('page', '1')
    cache_key = f"trakt_trending_movies:{page}:{limit}"

    cached = _trakt_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _TRAKT_CACHE_TTL:
            return jsonify(data)
        _trakt_cache.pop(cache_key, None)

    try:
        resp = requests.get(
            'https://api.trakt.tv/movies/trending',
            headers={
                'Content-Type': 'application/json',
                'trakt-api-version': '2',
                'trakt-api-key': client_id,
            },
            params={'page': page, 'limit': limit, 'extended': 'full'},
            timeout=10,
        )

        if resp.status_code == 429:
            retry_after = resp.headers.get('Retry-After', '30')
            logger.warning(f"Trakt movies 429 rate-limited, retry after {retry_after}s")
            return jsonify({
                'movies': [],
                'enabled': True,
                'error': f'Trakt rate limited — retry in {retry_after}s',
            }), 429

        resp.raise_for_status()
        raw_items = resp.json()  # list of {watchers, movie: {...}}

        tmdb_api_key = settings.get('apis.tmdb.api_key', '')

        # Resolve TMDb images in parallel (was serial — 20 sequential HTTP calls)
        def _resolve_movie_images(tmdb_id):
            if not tmdb_id or not tmdb_api_key:
                return tmdb_id, None, None
            tmdb_cache_key = f"trakt_tmdb_movie_img:{tmdb_id}"
            img_cached = _trakt_cache.get(tmdb_cache_key)
            if img_cached and time.time() - img_cached[0] < _TRAKT_CACHE_TTL:
                return tmdb_id, img_cached[1].get('poster_path'), img_cached[1].get('backdrop_path')
            try:
                tmdb_resp = requests.get(
                    f'https://api.themoviedb.org/3/movie/{tmdb_id}',
                    params={'api_key': tmdb_api_key},
                    timeout=5,
                )
                if tmdb_resp.status_code == 200:
                    tmdb_data = tmdb_resp.json()
                    poster = tmdb_data.get('poster_path')
                    backdrop = tmdb_data.get('backdrop_path')
                    _set_trakt_cache(tmdb_cache_key, {'poster_path': poster, 'backdrop_path': backdrop})
                    return tmdb_id, poster, backdrop
            except Exception:
                pass
            return tmdb_id, None, None

        img_map = {}
        ids_to_fetch = []
        for entry in raw_items:
            tmdb_id = entry.get('movie', {}).get('ids', {}).get('tmdb')
            if not tmdb_id or not tmdb_api_key:
                continue
            tmdb_cache_key = f"trakt_tmdb_movie_img:{tmdb_id}"
            img_cached = _trakt_cache.get(tmdb_cache_key)
            if img_cached and time.time() - img_cached[0] < _TRAKT_CACHE_TTL:
                img_map[tmdb_id] = (img_cached[1].get('poster_path'), img_cached[1].get('backdrop_path'))
            else:
                ids_to_fetch.append(tmdb_id)

        if ids_to_fetch:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for tid, poster, backdrop in pool.map(_resolve_movie_images, ids_to_fetch):
                    if tid:
                        img_map[tid] = (poster, backdrop)

        movies = []
        for entry in raw_items:
            movie = entry.get('movie', {})
            ids = movie.get('ids', {})
            tmdb_id = ids.get('tmdb')
            poster_path, backdrop_path = img_map.get(tmdb_id, (None, None))

            movies.append({
                'title': movie.get('title', ''),
                'year': movie.get('year'),
                'overview': movie.get('overview', ''),
                'trakt_id': ids.get('trakt'),
                'tmdb_id': tmdb_id,
                'imdb_id': ids.get('imdb'),
                'poster_path': poster_path,
                'backdrop_path': backdrop_path,
                'watchers': entry.get('watchers', 0),
                'rating': movie.get('rating'),
                'media_type': 'movie',
            })

        enrich_imdb_ratings(movies, settings=settings, max_items=40, replace_rating=True)

        payload = {'movies': movies, 'enabled': True, 'total': len(movies)}
        _set_trakt_cache(cache_key, payload)
        return jsonify(payload)

    except requests.exceptions.Timeout:
        logger.error("Trakt movies API timed out (10s)")
        return jsonify({'movies': [], 'enabled': True, 'error': 'Trakt API timed out'}), 504
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to Trakt API")
        return jsonify({'movies': [], 'enabled': True, 'error': 'Could not connect to Trakt'}), 502
    except Exception as e:
        logger.error(f"Trakt trending movies error: {e}")
        return jsonify({'movies': [], 'enabled': True, 'error': str(e)}), 500


def _fetch_trakt_anticipated(media):
    """Shared implementation for Trakt anticipated movies/shows.

    `media` is 'movies' or 'shows'. Returns (payload_dict, status_code).
    Anticipated entries rank by `list_count` (how many users have it listed)
    rather than `watchers`, and cover unreleased titles.
    """
    key = media  # response key: 'movies' or 'shows'
    enabled = settings.get('apis.trakt.enabled', False)
    client_id = settings.get('apis.trakt.client_id', '')
    if not enabled or not client_id:
        return {key: [], 'enabled': False}, 200

    limit = request.args.get('limit', '20')
    page = request.args.get('page', '1')
    cache_key = f"trakt_anticipated_{media}:{page}:{limit}"

    cached = _trakt_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _TRAKT_CACHE_TTL:
            return data, 200
        _trakt_cache.pop(cache_key, None)

    item_key = 'show' if media == 'shows' else 'movie'
    tmdb_type = 'tv' if media == 'shows' else 'movie'
    mtype = 'tv' if media == 'shows' else 'movie'
    tmdb_api_key = settings.get('apis.tmdb.api_key', '')

    def _resolve_images(tmdb_id):
        if not tmdb_id or not tmdb_api_key:
            return tmdb_id, None, None
        tmdb_cache_key = f"trakt_tmdb_{tmdb_type}_img:{tmdb_id}"
        img_cached = _trakt_cache.get(tmdb_cache_key)
        if img_cached and time.time() - img_cached[0] < _TRAKT_CACHE_TTL:
            return tmdb_id, img_cached[1].get('poster_path'), img_cached[1].get('backdrop_path')
        try:
            tmdb_resp = requests.get(
                f'https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}',
                params={'api_key': tmdb_api_key},
                timeout=5,
            )
            if tmdb_resp.status_code == 200:
                tmdb_data = tmdb_resp.json()
                poster = tmdb_data.get('poster_path')
                backdrop = tmdb_data.get('backdrop_path')
                _set_trakt_cache(tmdb_cache_key, {'poster_path': poster, 'backdrop_path': backdrop})
                return tmdb_id, poster, backdrop
        except Exception:
            pass
        return tmdb_id, None, None

    try:
        resp = requests.get(
            f'https://api.trakt.tv/{media}/anticipated',
            headers={
                'Content-Type': 'application/json',
                'trakt-api-version': '2',
                'trakt-api-key': client_id,
            },
            params={'page': page, 'limit': limit, 'extended': 'full'},
            timeout=10,
        )
        if resp.status_code == 429:
            retry_after = resp.headers.get('Retry-After', '30')
            logger.warning(f"Trakt anticipated {media} 429 rate-limited, retry after {retry_after}s")
            return {key: [], 'enabled': True, 'error': f'Trakt rate limited — retry in {retry_after}s'}, 429

        resp.raise_for_status()
        raw_items = resp.json()  # [{list_count, movie/show: {...}}]

        # Resolve TMDb images in parallel, skipping cache hits
        img_map = {}
        ids_to_fetch = []
        for entry in raw_items:
            tmdb_id = entry.get(item_key, {}).get('ids', {}).get('tmdb')
            if not tmdb_id or not tmdb_api_key:
                continue
            tmdb_cache_key = f"trakt_tmdb_{tmdb_type}_img:{tmdb_id}"
            img_cached = _trakt_cache.get(tmdb_cache_key)
            if img_cached and time.time() - img_cached[0] < _TRAKT_CACHE_TTL:
                img_map[tmdb_id] = (img_cached[1].get('poster_path'), img_cached[1].get('backdrop_path'))
            else:
                ids_to_fetch.append(tmdb_id)

        if ids_to_fetch:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for tid, poster, backdrop in pool.map(_resolve_images, ids_to_fetch):
                    if tid:
                        img_map[tid] = (poster, backdrop)

        results = []
        for entry in raw_items:
            obj = entry.get(item_key, {})
            ids = obj.get('ids', {})
            tmdb_id = ids.get('tmdb')
            poster_path, backdrop_path = img_map.get(tmdb_id, (None, None))
            item = {
                'title': obj.get('title', ''),
                'year': obj.get('year'),
                'overview': obj.get('overview', ''),
                'trakt_id': ids.get('trakt'),
                'tmdb_id': tmdb_id,
                'imdb_id': ids.get('imdb'),
                'poster_path': poster_path,
                'backdrop_path': backdrop_path,
                'list_count': entry.get('list_count', 0),
                'watchers': 0,
                'rating': obj.get('rating'),
                'media_type': mtype,
            }
            if media == 'shows':
                item['tvdb_id'] = ids.get('tvdb')
            results.append(item)

        enrich_imdb_ratings(results, settings=settings, max_items=40, replace_rating=True)

        payload = {key: results, 'enabled': True, 'total': len(results)}
        _set_trakt_cache(cache_key, payload)
        return payload, 200

    except requests.exceptions.Timeout:
        logger.error(f"Trakt anticipated {media} API timed out (10s)")
        return {key: [], 'enabled': True, 'error': 'Trakt API timed out'}, 504
    except requests.exceptions.ConnectionError:
        logger.error(f"Could not connect to Trakt API ({media} anticipated)")
        return {key: [], 'enabled': True, 'error': 'Could not connect to Trakt'}, 502
    except Exception as e:
        logger.error(f"Trakt anticipated {media} error: {e}")
        return {key: [], 'enabled': True, 'error': str(e)}, 500


@app.route('/api/trakt/movies/anticipated')
def trakt_anticipated_movies():
    """Proxy Trakt anticipated (unreleased) movies, ranked by list_count."""
    payload, code = _fetch_trakt_anticipated('movies')
    return jsonify(payload), code


@app.route('/api/trakt/shows/anticipated')
def trakt_anticipated_shows():
    """Proxy Trakt anticipated (unreleased) shows, ranked by list_count."""
    payload, code = _fetch_trakt_anticipated('shows')
    return jsonify(payload), code


import threading as _threading
_explore_lock = _threading.Lock()

def update_explore_cache():
    """Populate explore cache via generic indexer (Prowlarr or Torznab)."""
    if not _explore_lock.acquire(blocking=False):
        logger.debug("Explore cache update skipped (already running)")
        return
    try:
        _update_explore_cache_inner()
    finally:
        _explore_lock.release()

def _update_explore_cache_inner():
    # Prowlarr/Torznab sweep — all indexers (PHD, BHD, IPT, etc.) via single Prowlarr instance
    client = get_indexer_client(settings)
    if client is None:
        logger.warning("No indexer configured — explore cache update skipped")
        return

    categories = settings.get('indexer.movie_categories') or '2000'
    try:
        movies = client.sweep_movies(categories=categories)
        logger.info(f"Indexer sweep: {len(movies)} movies")
    except Exception as e:
        logger.error(f"Indexer sweep_movies failed: {e}")
        return

    if not movies:
        logger.info("No movies from indexer sweep")
        return

    # 1. Normalize tracker names (Prowlarr returns full indexer names)
    _tracker_normalize = {
        'privatehd': 'PHD',
        'beyondhd': 'BHD',
        'beyond-hd': 'BHD',
        'iptorrents': 'IPT',
    }
    for m in movies:
        t = (m.get('tracker') or '').strip()
        t_lower = t.lower().replace(' ', '')
        if t_lower in _tracker_normalize:
            m['tracker'] = _tracker_normalize[t_lower]

    # Diagnostic logging
    indexer_counts = {}
    for m in movies:
        t = m.get('tracker', 'unknown')
        indexer_counts[t] = indexer_counts.get(t, 0) + 1
    logger.info(f"Explore raw: {len(movies)} items, indexers={indexer_counts}")

    # 2. Filter + dedup (filters TV shows, CAM/TS, normalizes titles, dedupes by title)
    deduped = combine_and_dedupe_movies(movies)
    logger.info(f"After combine_and_dedupe_movies: {len(deduped)} movies")

    # 3. TMDb poster enrichment
    # Two paths: movies WITH tmdb_id get a cheap /movie/{id} call for poster;
    # movies WITHOUT tmdb_id get a search call for tmdb_id + poster.
    tmdb_api_key = settings.get('apis.tmdb.api_key') or ''
    tmdb_base = settings.get('apis.tmdb.base_url') or 'https://api.themoviedb.org/3'
    img_base = settings.get('apis.tmdb.image_base_url') or 'https://image.tmdb.org/t/p'

    def _set_poster(movie, poster_path):
        """Set poster fields from a TMDb poster_path."""
        if poster_path:
            movie['poster'] = f'{img_base}/w342{poster_path}'
            movie['poster_url'] = movie['poster']
            movie['poster_path'] = poster_path

    def enrich_movie(movie):
        if not tmdb_api_key:
            return movie
        has_poster = movie.get('poster') or movie.get('poster_url') or movie.get('poster_path')
        tmdb_id = movie.get('tmdb_id')

        # BHD returns tmdb_id as "movie/123456" — strip the prefix
        if tmdb_id and '/' in str(tmdb_id):
            tmdb_id = str(tmdb_id).rsplit('/', 1)[-1]
            movie['tmdb_id'] = tmdb_id

        if tmdb_id and has_poster:
            return movie  # Already complete

        try:
            if tmdb_id:
                # Have tmdb_id but no poster — cheap details call
                resp = requests.get(f'{tmdb_base}/movie/{tmdb_id}',
                                    params={'api_key': tmdb_api_key}, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    _set_poster(movie, data.get('poster_path', ''))
                    if not movie.get('overview') and data.get('overview'):
                        movie['overview'] = data['overview']
                    if not movie.get('backdrop_path') and data.get('backdrop_path'):
                        movie['backdrop_path'] = data['backdrop_path']
            else:
                # No tmdb_id — search by title
                title = movie.get('title', '')
                year = movie.get('year', '')
                if not title:
                    return movie
                params = {'api_key': tmdb_api_key, 'query': title}
                if year:
                    params['year'] = year
                resp = requests.get(f'{tmdb_base}/search/movie', params=params, timeout=5)
                if resp.status_code == 200:
                    results = resp.json().get('results', [])
                    if results:
                        best = results[0]
                        _set_poster(movie, best.get('poster_path', ''))
                        movie['tmdb_id'] = str(best.get('id', ''))
                        if not movie.get('year') and best.get('release_date'):
                            movie['year'] = best['release_date'][:4]
                        if not movie.get('overview') and best.get('overview'):
                            movie['overview'] = best['overview']
                        if not movie.get('backdrop_path') and best.get('backdrop_path'):
                            movie['backdrop_path'] = best['backdrop_path']
        except Exception:
            pass
        return movie

    needs_poster = [m for m in deduped if not (m.get('poster') or m.get('poster_url') or m.get('poster_path'))]
    has_poster = [m for m in deduped if m.get('poster') or m.get('poster_url') or m.get('poster_path')]
    logger.info(f"TMDb enrichment: {len(needs_poster)} need poster, {len(has_poster)} already have poster")

    with ThreadPoolExecutor(max_workers=5) as executor:
        enriched_batch = list(executor.map(enrich_movie, needs_poster))

    final = sorted(has_poster + enriched_batch,
                   key=lambda x: (x.get('seeders', 0), x.get('pub_date', '')), reverse=True)

    # Log top 10 after full pipeline
    for i, m in enumerate(final[:10], 1):
        logger.info(f"  {i}. {m.get('title', '?')} ({m.get('year', '?')}) | seeders={m.get('seeders', 0)} | tracker={m.get('tracker', '?')} | pub={m.get('pub_date', '?')[:16]}")

    explore_cache.update_cache(final)
    logger.info(f"Explore cache updated with {len(final)} movies")

def explore_cache_updater():
    """Background thread to update explore cache periodically"""
    import threading

    def update_loop():
        while True:
            try:
                if explore_cache.needs_update():
                    logger.info("Starting explore cache update...")
                    update_explore_cache()
                time.sleep(300)  # Check every 5 minutes
            except Exception as e:
                logger.error(f"Explore cache updater error: {e}")
                time.sleep(300)

    thread = threading.Thread(target=update_loop, daemon=True)
    thread.start()
    logger.info("Explore cache updater thread started")

@app.route('/api/explore/cached')
def get_cached_explore_movies():
    """Get cached explore movies for fast loading"""
    try:
        # Check if cache needs updating (but don't block)
        if explore_cache.needs_update():
            # Start update in background
            import threading
            threading.Thread(target=update_explore_cache, daemon=True).start()

        # Return cached movies immediately
        limit = request.args.get('limit', 1000, type=int)
        movies = explore_cache.get_cached_movies(limit=limit)

        return jsonify({
            'movies': movies,
            'total': len(movies),
            'cached': True,
            'cache_info': explore_cache.get_cache_info()
        })

    except Exception as e:
        logger.error(f"Error getting cached explore movies: {e}")
        return jsonify({'error': str(e), 'movies': []}), 500

@app.route('/api/explore/top')
def get_top_explore_movies():
    """Fast pre-filtered endpoint for AI agent queries.

    Query params:
        quality: 4k, 4k_remux, 4k_web, 1080p, 1080p_remux, 1080p_web, remux
        sort:    seeders (default) | newest
        limit:   number of results (default 25, max 100)
        min_seeders: minimum seeder count (default 0)
        tracker: filter to a specific tracker name
    """
    try:
        quality = request.args.get('quality')
        sort = request.args.get('sort', 'seeders')
        limit = min(request.args.get('limit', 25, type=int), 100)
        min_seeders = request.args.get('min_seeders', 0, type=int)
        tracker = request.args.get('tracker')

        movies = explore_cache.query_top_movies(
            quality=quality, sort=sort, limit=limit,
            min_seeders=min_seeders, tracker=tracker
        )

        cache_info = explore_cache.get_cache_info()
        last_updated = None
        if cache_info.get('metadata', {}).get('last_update'):
            last_updated = cache_info['metadata']['last_update'].get('value')

        return jsonify({
            'movies': movies,
            'count': len(movies),
            'filters': {
                'quality': quality,
                'sort': sort,
                'min_seeders': min_seeders,
                'tracker': tracker,
            },
            'cache_age_minutes': round(
                (datetime.now() - datetime.fromisoformat(last_updated)).total_seconds() / 60, 1
            ) if last_updated else None,
            'trackers': explore_cache.get_available_trackers(),
        })

    except Exception as e:
        logger.error(f"Error in /api/explore/top: {e}")
        return jsonify({'error': str(e), 'movies': []}), 500


# ---------------------------------------------------------------------------
# Upgrade Finder endpoints
# ---------------------------------------------------------------------------

_upgrade_finder = None

def _get_upgrade_finder():
    global _upgrade_finder
    if _upgrade_finder is None:
        _upgrade_finder = UpgradeFinder(plex_service, settings)
    return _upgrade_finder


@app.route('/api/upgrades/sections')
def get_upgrade_sections():
    """Return Plex movie library sections (informational)."""
    try:
        finder = _get_upgrade_finder()
        sections = finder.get_sections()
        return jsonify({'sections': sections})
    except Exception as e:
        logger.error(f"Error in /api/upgrades/sections: {e}")
        return jsonify({'error': str(e), 'sections': []}), 500


@app.route('/api/upgrades/list')
def list_upgrades():
    """Get discovered upgrades across all libraries.

    Query params:
        quality: 4k_remux, 4k, 1080p_remux (optional)
        sort: title (default), seeders, size, newest
        limit: max results (default 500)
    """
    try:
        finder = _get_upgrade_finder()
        quality = request.args.get('quality')
        sort = request.args.get('sort', 'title')
        limit = request.args.get('limit', 500, type=int)

        result = finder.get_upgrades(
            quality=quality, sort=sort, limit=limit)
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in /api/upgrades/list: {e}")
        return jsonify({'error': str(e), 'upgrades': []}), 500


@app.route('/api/upgrades/sync', methods=['POST'])
def sync_library_section():
    """Sync ALL Plex movie sections into the upgrade library database."""
    try:
        finder = _get_upgrade_finder()
        finder.sync_all_libraries()
        progress = finder.get_progress()
        return jsonify({'status': 'ok', **progress})

    except Exception as e:
        logger.error(f"Error in /api/upgrades/sync: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/upgrades/batch', methods=['POST'])
def run_upgrade_batch():
    """Run a batch of indexer searches (~50 movies) across all libraries.
    Syncs all sections first, then picks movies never searched or >7 days old."""
    try:
        data = request.get_json(silent=True) or {}
        batch_size = data.get('batch_size', BATCH_SIZE)
        finder = _get_upgrade_finder()

        # Sync all libraries first to pick up new movies
        finder.sync_all_libraries()

        # Run in background thread so request doesn't block
        import threading
        def _run():
            try:
                finder.run_batch(batch_size)
            except Exception as exc:
                logger.error(f"Upgrade batch thread error: {exc}", exc_info=True)
        threading.Thread(target=_run, daemon=True).start()

        progress = finder.get_progress()
        return jsonify({'status': 'started', **progress})

    except Exception as e:
        logger.error(f"Error in /api/upgrades/batch: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/upgrades/progress')
def upgrade_progress():
    """Quick progress check across all libraries."""
    try:
        finder = _get_upgrade_finder()
        return jsonify(finder.get_progress())
    except Exception as e:
        logger.error(f"Error in /api/upgrades/progress: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/upgrades/new/scan', methods=['POST'])
def scan_new_releases():
    """Pull remuxes from the explore cache (already has full Prowlarr sweep)
    and cross-reference against the library for upgrade opportunities."""
    try:
        finder = _get_upgrade_finder()

        # Sync all libraries first
        finder.sync_all_libraries()

        # Run in background thread, pass explore_cache
        import threading
        def _run():
            try:
                finder.scan_new_releases(explore_cache=explore_cache)
            except Exception as exc:
                logger.error(f"New releases scan error: {exc}", exc_info=True)
        threading.Thread(target=_run, daemon=True).start()

        return jsonify({'status': 'started'})

    except Exception as e:
        logger.error(f"Error in /api/upgrades/new/scan: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/upgrades/new/list')
def list_new_upgrades():
    """Get newly discovered upgrades from recent tracker uploads."""
    try:
        finder = _get_upgrade_finder()
        limit = request.args.get('limit', 100, type=int)
        result = finder.get_new_upgrades(limit=limit)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /api/upgrades/new/list: {e}")
        return jsonify({'error': str(e), 'upgrades': []}), 500


@app.route('/api/upgrades/mark-grabbed', methods=['POST'])
def mark_upgrade_grabbed():
    """Mark an upgrade as grabbed (sent to Radarr for download)."""
    try:
        data = request.get_json() or {}
        tmdb_id = str(data.get('tmdb_id', ''))
        if not tmdb_id:
            return jsonify({'error': 'tmdb_id required'}), 400
        finder = _get_upgrade_finder()
        updated = finder.mark_grabbed(tmdb_id)
        return jsonify({'ok': True, 'updated': updated})
    except Exception as e:
        logger.error(f"Error marking upgrade grabbed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/upgrades/dismiss', methods=['POST'])
def dismiss_upgrade():
    """Dismiss an upgrade so it no longer appears in results."""
    try:
        data = request.get_json() or {}
        tmdb_id = str(data.get('tmdb_id', ''))
        if not tmdb_id:
            return jsonify({'error': 'tmdb_id required'}), 400
        finder = _get_upgrade_finder()
        updated = finder.dismiss_upgrade(tmdb_id)
        return jsonify({'ok': True, 'updated': updated})
    except Exception as e:
        logger.error(f"Error dismissing upgrade: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/upgrades/enrich-ratings', methods=['POST'])
def enrich_upgrade_ratings():
    """Fetch Rotten Tomatoes scores for upgrades that don't have them yet.
    Processes up to `limit` movies per call (default 50)."""
    try:
        data = request.get_json(silent=True) or {}
        limit = data.get('limit', 50)
        finder = _get_upgrade_finder()

        import threading
        def _run():
            try:
                finder.enrich_rt_scores(limit=limit)
            except Exception as exc:
                logger.error(f"RT enrichment thread error: {exc}", exc_info=True)
        threading.Thread(target=_run, daemon=True).start()

        return jsonify({'status': 'started', 'batch_limit': limit})
    except Exception as e:
        logger.error(f"Error in /api/upgrades/enrich-ratings: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/upgrades/top-rated')
def top_rated_upgrades():
    """Get upgrades sorted by Rotten Tomatoes score.

    Query params:
        quality: upgrade_type filter (default: 4k_remux)
        min_score: minimum RT score to include (default: 70)
        limit: max results (default: 20)
    """
    try:
        finder = _get_upgrade_finder()
        quality = request.args.get('quality', '4k_remux')
        min_score = request.args.get('min_score', 70, type=int)
        limit = request.args.get('limit', 20, type=int)

        result = finder.get_top_rated_upgrades(
            quality=quality, min_rt_score=min_score, limit=limit)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /api/upgrades/top-rated: {e}")
        return jsonify({'error': str(e), 'upgrades': []}), 500


@app.route('/api/upgrades/auto-grab', methods=['POST'])
def auto_grab_upgrades():
    """Alfred endpoint: grab top-rated upgrades and send them to Radarr.

    Payload:
        quality: upgrade_type (default: 4k_remux)
        min_score: minimum RT score (default: 75)
        max_grabs: max movies to grab per run (default: 3)
        profile: Radarr quality profile name (default: 4K)

    Returns list of movies grabbed with RT scores and reasons.
    """
    try:
        data = request.get_json(silent=True) or {}
        quality = data.get('quality', '4k_remux')
        min_score = data.get('min_score', 75)
        max_grabs = data.get('max_grabs', 3)
        profile_name = data.get('profile', '4K')

        finder = _get_upgrade_finder()
        top = finder.get_top_rated_upgrades(
            quality=quality, min_rt_score=min_score, limit=max_grabs)

        if not top['upgrades']:
            return jsonify({
                'status': 'ok',
                'grabbed': [],
                'message': 'No upgrades meet the rating threshold',
            })

        radarr = get_radarr_for_request()
        if not radarr:
            return jsonify({'error': 'Radarr not configured'}), 503

        # Resolve quality profile
        profiles = radarr.get_quality_profiles()
        qp = next(
            (p for p in profiles if p.get('name', '').lower() == profile_name.lower()),
            profiles[0] if profiles else None,
        )
        if not qp:
            return jsonify({'error': 'No quality profiles in Radarr'}), 500

        grabbed = []
        for movie in top['upgrades']:
            tmdb_id = movie['tmdb_id']
            title = movie['title']
            year = movie.get('year', '')
            rt_c = movie.get('rt_critic')
            rt_a = movie.get('rt_audience')
            avg_score = ((rt_c or 0) + (rt_a or 0)) // 2

            try:
                result = radarr.ensure_movie_monitored(
                    tmdb_id=int(tmdb_id),
                    quality_profile_id=qp['id'],
                    title=title,
                    year=int(year) if year and str(year).isdigit() else 0,
                )
                if result['success']:
                    finder.mark_grabbed(tmdb_id)
                    grabbed.append({
                        'tmdb_id': tmdb_id,
                        'title': title,
                        'year': year,
                        'rt_critic': rt_c,
                        'rt_audience': rt_a,
                        'avg_score': avg_score,
                        'current_quality': movie.get('current_quality', ''),
                        'upgrade_quality': movie.get('best_quality', ''),
                        'seeders': movie.get('best_seeders', 0),
                        'size_gb': movie.get('best_size_gb', 0),
                        'action': result['action'],
                        'reason': (
                            f"RT {rt_c}% critic / {rt_a}% audience "
                            f"({avg_score}% avg). "
                            f"Upgrading from {movie.get('current_quality', '?')} "
                            f"to {movie.get('best_quality', '?')}. "
                            f"{movie.get('best_seeders', 0)} seeders, "
                            f"{movie.get('best_size_gb', 0):.1f} GB."
                        ),
                    })
                    logger.info(
                        f"[auto-grab] Grabbed '{title}' ({year}) — "
                        f"RT {rt_c}/{rt_a}, profile={profile_name}")
            except Exception as e:
                logger.error(f"[auto-grab] Failed to grab '{title}': {e}")

        return jsonify({
            'status': 'ok',
            'grabbed': grabbed,
            'total_grabbed': len(grabbed),
            'profile': profile_name,
        })

    except Exception as e:
        logger.error(f"Error in /api/upgrades/auto-grab: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/upgrades/scheduler/status')
def upgrade_scheduler_status():
    """Get daily upgrade scheduler status and run log."""
    try:
        from app.upgrade_scheduler import get_status, get_log
        return jsonify({
            'status': get_status(),
            'log': get_log()[:10],
        })
    except Exception as e:
        logger.error(f"Error in /api/upgrades/scheduler/status: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/upgrades/scheduler/trigger', methods=['POST'])
def trigger_upgrade_scheduler():
    """Manually trigger a full upgrade pipeline run (sync + batch + RT)."""
    try:
        from app.upgrade_scheduler import trigger_manual_run
        trigger_manual_run()
        return jsonify({'status': 'started'})
    except Exception as e:
        logger.error(f"Error triggering upgrade scheduler: {e}")
        return jsonify({'error': str(e)}), 500


_in_theaters_cache: Dict[str, tuple] = {}  # key → (timestamp, data)
_IN_THEATERS_TTL = 30 * 60  # 30 minutes
_IN_THEATERS_CACHE_MAX = 50

@app.route('/api/in-theaters')
def get_in_theaters():
    """Get movies currently in theaters from TMDB (cached 30m)."""
    cache_key = 'in_theaters'
    cached = _in_theaters_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _IN_THEATERS_TTL:
            return jsonify(data)

    try:
        tmdb_url = "https://api.themoviedb.org/3/movie/now_playing"
        params = {
            'api_key': settings.get('apis.tmdb.api_key'),
            'language': 'en-US',
            'page': 1,
            'region': 'US'
        }

        response = requests.get(tmdb_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        movies = []
        for movie in data.get('results', [])[:20]:
            movies.append({
                'tmdb_id': movie.get('id'),
                'title': movie.get('title'),
                'year': movie.get('release_date', '')[:4] if movie.get('release_date') else None,
                'poster_path': movie.get('poster_path'),
                'poster_url': f"https://image.tmdb.org/t/p/w342{movie.get('poster_path')}" if movie.get('poster_path') else None,
                'backdrop_path': movie.get('backdrop_path'),
                'overview': movie.get('overview'),
                'rating': movie.get('vote_average'),
                'vote_average': movie.get('vote_average'),
                'release_date': movie.get('release_date'),
                'media_type': 'movie'
            })

        enrich_imdb_ratings(movies, settings=settings, max_items=40, replace_rating=True)
        for m in movies:
            m['vote_average'] = m.get('rating')

        payload = {'movies': movies, 'total': len(movies)}
        # Evict expired entries; enforce max size
        now_ts = time.time()
        expired = [k for k, (ts, _) in list(_in_theaters_cache.items())
                   if now_ts - ts >= _IN_THEATERS_TTL]
        for k in expired:
            del _in_theaters_cache[k]
        if len(_in_theaters_cache) >= _IN_THEATERS_CACHE_MAX:
            oldest = sorted(_in_theaters_cache, key=lambda k: _in_theaters_cache[k][0])
            for k in oldest[:len(_in_theaters_cache) - _IN_THEATERS_CACHE_MAX + 1]:
                del _in_theaters_cache[k]
        _in_theaters_cache[cache_key] = (now_ts, payload)
        return jsonify(payload)

    except Exception as e:
        logger.error(f"Error getting in-theaters movies: {e}")
        return jsonify({'error': str(e), 'movies': []}), 500

@app.route('/api/library/stats')
def library_stats():
    """Get library statistics from Plex API."""
    try:
        stats = get_plex_for_request().get_library_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting library stats: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/library/stats/cached')
def library_stats_cached():
    """Get cached library statistics for ultra-fast loading."""
    try:
        # Check if cache needs updating (but don't block)
        if library_cache.needs_update('library_stats'):
            # Start update in background
            import threading
            threading.Thread(target=update_library_stats_cache, daemon=True).start()

        # Return cached stats immediately
        stats = library_cache.get_library_stats()

        if stats:
            stats['from_cache'] = True
            stats['cache_info'] = library_cache.get_cache_info().get('library_stats', {})
            return jsonify(stats)
        else:
            # No cache yet, return empty
            return jsonify({
                'movies': {},
                'tv_shows': {},
                'total_movies': 0,
                'total_shows': 0,
                'total_episodes': 0,
                'from_cache': True,
                'cache_info': {'status': 'initializing'}
            })

    except Exception as e:
        logger.error(f"Error getting cached library stats: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/library/browse/cached')
def library_browse_cached():
    """Browse movies from library cache for ultra-fast loading."""
    try:
        library_name = request.args.get('library', '')
        if not library_name:
            return jsonify({'error': 'Library name is required'}), 400

        offset = request.args.get('offset', 0, type=int)
        limit = request.args.get('limit', 50, type=int)
        sort_by = request.args.get('sort', 'added_at')
        direction = request.args.get('direction', 'desc')
        genre_filter = request.args.get('genre', '')

        cache_key = f"browse_{library_name}"

        # Check if cache needs updating (but don't block)
        if library_cache.needs_update(cache_key):
            # Resolve section_id for the library name
            _sections = plex_service.get_sections_lean()
            _match = next((s for s in _sections if s['title'] == library_name), None)
            if _match:
                import threading
                _sid = _match['id']
                threading.Thread(target=lambda: update_library_browse_cache(library_name, _sid), daemon=True).start()

        # Get cached data
        result = library_cache.get_browse_library(
            library_name=library_name,
            offset=offset,
            limit=limit,
            sort_by=sort_by,
            direction=direction,
            genre_filter=genre_filter
        )

        result['cache_info'] = library_cache.get_cache_info().get(cache_key, {})
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting cached library browse: {e}")
        # Fall back to non-cached version
        return library_browse()

@app.route('/api/library/genres')
def library_genres():
    """Get all genres for a library section via Plex API."""
    library_name = request.args.get('library', '')
    if not library_name:
        return jsonify({'genres': []})

    try:
        plex = get_plex_for_request()
        sections = plex.get_library_sections()
        section = next((s for s in sections if s['title'] == library_name), None)
        if not section:
            return jsonify({'genres': []})

        genres = plex.get_genres(section['id'])
        return jsonify({'genres': genres})
    except Exception as e:
        logger.error(f"Error getting genres: {e}")
        return jsonify({'genres': []})

@app.route('/api/library/browse')
def library_browse():
    """Browse movies from a specific library with pagination, sorting, and filtering."""
    library_name = request.args.get('library', '')
    offset = request.args.get('offset', 0, type=int)
    limit = request.args.get('limit', 50, type=int)
    sort_by = request.args.get('sort', 'added_date')
    sort_direction = request.args.get('direction', 'desc')
    genre_filter = request.args.get('genre', '')

    if not library_name:
        return jsonify({'error': 'Library name is required'}), 400

    try:
        plex = get_plex_for_request()
        # Find the section ID for this library name
        sections = plex.get_library_sections()
        section = next((s for s in sections if s['title'] == library_name), None)

        if not section:
            return jsonify({'error': f'Library "{library_name}" not found'}), 404

        # Map sort param to Plex API sort string
        sort_map = {
            'added_date': 'addedAt',
            'title': 'titleSort',
            'year': 'year',
            'rating': 'rating',
        }
        plex_sort_field = sort_map.get(sort_by, 'addedAt')
        plex_sort = f'{plex_sort_field}:{sort_direction}'

        # Get genres (only on first page)
        genres = []
        if offset == 0:
            genres = plex.get_genres(section['id'])

        # Fetch items — use get_all_shows for TV sections, get_all_movies otherwise
        is_show_section = section.get('type') == 'show'
        if is_show_section:
            result = plex.get_all_shows(
                section_id=section['id'],
                sort=plex_sort,
                limit=limit,
                offset=offset,
                genre=genre_filter or None,
            )
            raw_items = result['shows']
        else:
            result = plex.get_all_movies(
                section_id=section['id'],
                sort=plex_sort,
                limit=limit,
                offset=offset,
                genre=genre_filter or None,
            )
            raw_items = result['movies']

        # Build response with proxy image URLs
        movies = []
        for m in raw_items:
            movie = dict(m)
            movie['library_name'] = library_name
            movie['poster_url'] = f"/api/img/plex?url={m['thumbPath']}" if m.get('thumbPath') else None
            movie['backdrop_url'] = f"/api/img/plex?url={m['artPath']}" if m.get('artPath') else None
            movie['clearlogo_url'] = f"/api/img/plex?url={m['clearLogoPath']}" if m.get('clearLogoPath') else None
            if m.get('added_at'):
                movie['added_date'] = datetime.fromtimestamp(m['added_at']).strftime('%Y-%m-%d')
            movies.append(movie)

        return jsonify({
            'success': True,
            'library': library_name,
            'movies': movies,
            'total': result['total'],
            'offset': offset,
            'limit': limit,
            'has_more': result['has_more'],
            'genres': genres,
            'current_genre': genre_filter,
            'sort_by': sort_by,
        })

    except Exception as e:
        logger.error(f"Error browsing library '{library_name}': {e}")
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Vibe Engine — full library index for mood-based browsing
# ---------------------------------------------------------------------------

_vibe_cache: dict = {}  # keyed by plex_url → {'data': ..., 'ts': float}
_VIBE_CACHE_TTL = 600  # 10 minutes


_VIBE_STALE_MAX = 24 * 3600  # serve a stale index (and refresh in background) for up to 24h
_vibe_refreshing: set = set()
_vibe_refresh_lock = threading.Lock()


def _vibe_build(plex, _vibe_key, now):
    """Walk every Plex section and (re)build the Vibe Engine index.

    Returns (payload, None) on success or (None, (error_body, status)).
    No Flask request/app context needed, so it can run in a background thread.
    """
    if not plex.is_configured:
        logger.warning('Vibe Engine: plex_service not configured')
        return None, ({'error': 'Plex not configured', 'items': [], 'total': 0}, 503)

    sections = plex.get_sections_lean()
    if not sections:
        logger.warning('Vibe Engine: no library sections returned from Plex')
        return None, ({'error': 'No Plex libraries found', 'items': [], 'total': 0}, 503)

    excluded_libraries = settings.get('ui.vibe_engine.excluded_libraries', []) or []

    # ----- helper: fetch all items for one section -----
    def _fetch_section(section):
        sec_id = section['id']
        sec_type = section['type']
        sec_title = section['title']
        section_items = []
        page_size = 500
        timeout = 30

        try:
            if sec_type == 'movie':
                offset = 0
                while True:
                    result = plex.get_all_movies(
                        section_id=sec_id, sort='titleSort:asc',
                        limit=page_size, offset=offset, timeout=timeout,
                    )
                    for m in result['movies']:
                        section_items.append({
                            'rating_key': m['ratingKey'],
                            'title': m['title'],
                            'year': m.get('year'),
                            'type': 'movie',
                            'runtime_minutes': round(m.get('duration', 0) / 60000),
                            'genres': m.get('genres', []),
                            'summary': m.get('summary', ''),
                            'content_rating': m.get('contentRating', ''),
                            'rating': m.get('rating'),
                            'audience_rating': m.get('audienceRating'),
                            'poster_url': f"/api/img/plex?url={m['thumbPath']}" if m.get('thumbPath') else None,
                            'backdrop_url': f"/api/img/plex?url={m['artPath']}" if m.get('artPath') else None,
                            'tmdb_id': m.get('tmdb_id'),
                            'imdb_id': m.get('imdb_id'),
                            'view_count': m.get('viewCount', 0),
                            'last_viewed_at': m.get('lastViewedAt', 0),
                            'added_at': m.get('addedAt', 0),
                            'library_name': sec_title,
                        })
                    if not result['has_more']:
                        break
                    offset += page_size

            elif sec_type == 'show':
                offset = 0
                while True:
                    result = plex.get_all_shows(
                        section_id=sec_id, sort='titleSort:asc',
                        limit=page_size, offset=offset, timeout=timeout,
                    )
                    for s in result['shows']:
                        section_items.append({
                            'rating_key': s['ratingKey'],
                            'title': s['title'],
                            'year': s.get('year'),
                            'type': 'tv',
                            'runtime_minutes': round(s.get('duration', 0) / 60000),
                            'genres': s.get('genres', []),
                            'summary': s.get('summary', ''),
                            'content_rating': s.get('contentRating', ''),
                            'rating': s.get('rating'),
                            'audience_rating': s.get('audienceRating'),
                            'poster_url': f"/api/img/plex?url={s['thumbPath']}" if s.get('thumbPath') else None,
                            'backdrop_url': f"/api/img/plex?url={s['artPath']}" if s.get('artPath') else None,
                            'tmdb_id': s.get('tmdb_id'),
                            'imdb_id': s.get('imdb_id'),
                            'view_count': s.get('viewCount', 0),
                            'last_viewed_at': s.get('lastViewedAt', 0),
                            'added_at': s.get('addedAt', 0),
                            'library_name': sec_title,
                        })
                    if not result['has_more']:
                        break
                    offset += page_size

        except Exception as exc:
            logger.error('Vibe Engine: failed to fetch section "%s" (id=%s): %s',
                         sec_title, sec_id, exc)

        if not section_items and sec_type in ('movie', 'show'):
            logger.warning('Vibe Engine: section "%s" (%s, id=%s) returned 0 items '
                           '— possible timeout or error', sec_title, sec_type, sec_id)
        else:
            logger.info('Vibe Engine: section "%s" (%s, id=%s) → %d items',
                        sec_title, sec_type, sec_id, len(section_items))

        return section_items

    # ----- filter sections and fetch in parallel -----
    from concurrent.futures import ThreadPoolExecutor, as_completed

    active_sections = [
        s for s in sections
        if s['title'] not in excluded_libraries and s['type'] in ('movie', 'show')
    ]
    for s in sections:
        if s['title'] in excluded_libraries:
            logger.debug('Vibe Engine: skipping excluded library "%s"', s['title'])

    items = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_section, s): s for s in active_sections}
        for future in as_completed(futures):
            items.extend(future.result())

    payload = {
        'items': items,
        'total': len(items),
        'cached_at': int(now),
    }

    # Only cache non-empty results so retries work when Plex was temporarily down.
    # An older (stale) entry is kept otherwise, so it can still be served.
    if items:
        _vibe_cache[_vibe_key] = {'data': payload, 'ts': now}

    logger.info('Vibe Engine: indexed %d items across %d sections',
                 len(items), len(active_sections))
    return payload, None


def _vibe_refresh_async(plex, _vibe_key):
    """Rebuild the index in the background (one refresh per key at a time)."""
    with _vibe_refresh_lock:
        if _vibe_key in _vibe_refreshing:
            return
        _vibe_refreshing.add(_vibe_key)

    def _run():
        try:
            _vibe_build(plex, _vibe_key, time.time())
        except Exception as exc:  # keep serving the stale copy
            logger.error(f"Vibe Engine background refresh error: {exc}")
        finally:
            with _vibe_refresh_lock:
                _vibe_refreshing.discard(_vibe_key)

    threading.Thread(target=_run, name='vibe-refresh', daemon=True).start()


def _vibe_warm_on_startup(delay: float = 45.0):
    """Build the shared (global Plex) Vibe index shortly after boot so the first
    Pick For Me → "In Your Library" after a restart doesn't wait on a cold
    ~15-20s Plex walk. Runs once, in the background."""
    def _run():
        time.sleep(delay)
        try:
            if getattr(plex_service, 'is_configured', False):
                key = getattr(plex_service, 'plex_url', '') or 'global'
                if not _vibe_cache.get(key):
                    _vibe_refresh_async(plex_service, key)
        except Exception as exc:
            logger.error(f"Vibe Engine warm-up error: {exc}")
    threading.Thread(target=_run, name='vibe-warm', daemon=True).start()



@app.route('/api/library/vibe-engine')
def library_vibe_engine():
    """Return all movies + shows from Plex for the Vibe Engine UI.

    Response includes watch state, genres, ratings, runtime — everything
    the frontend needs to compute mood-based shelves client-side.
    Cached in-memory for 10 minutes; after that the stale copy is served
    instantly while a background refresh rebuilds it (a cold Plex walk takes
    ~10s, which used to stall Pick For Me → "In Your Library").
    """
    import time as _time

    now = _time.time()

    try:
        plex = get_plex_for_request()
        _vibe_key = getattr(plex, 'plex_url', '') or 'global'
        _vibe_entry = _vibe_cache.get(_vibe_key)
        if _vibe_entry and _vibe_entry.get('data'):
            _age = now - _vibe_entry.get('ts', 0)
            if _age < _VIBE_CACHE_TTL:
                return jsonify(_vibe_entry['data'])
            if _age < _VIBE_STALE_MAX:
                _vibe_refresh_async(plex, _vibe_key)
                return jsonify(_vibe_entry['data'])

        payload, err = _vibe_build(plex, _vibe_key, now)
        if err:
            return jsonify(err[0]), err[1]
        return jsonify(payload)

    except Exception as e:
        logger.error(f"Vibe Engine error: {e}")
        return jsonify({'error': str(e), 'items': [], 'total': 0}), 500


@app.route('/api/library/recent/cached')
def library_recent_cached():
    """Get cached recently added items for ultra-fast loading."""
    try:
        limit = request.args.get('limit', 25, type=int)
        media_type = request.args.get('type', '')  # 'movie', 'tv', or empty for all

        # Check if cache needs updating (but don't block)
        if library_cache.needs_update('recent_items'):
            # Start update in background
            import threading
            threading.Thread(target=update_recent_items_cache, daemon=True).start()

        # Get cached items
        metadata_type = None
        if media_type == 'movie':
            metadata_type = 1
        elif media_type == 'tv':
            metadata_type = 2

        items = library_cache.get_recent_items(limit=limit, metadata_type=metadata_type)

        # Separate into movies and TV shows if needed
        if not media_type:
            movies = [item for item in items if item.get('metadata_type') == 1][:limit]
            tv_shows = [item for item in items if item.get('metadata_type') == 2][:limit]
            result = {'movies': movies, 'tv_shows': tv_shows}
        else:
            result = {'items': items}

        result['from_cache'] = True
        result['cache_info'] = library_cache.get_cache_info().get('recent_items', {})

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting cached recent items: {e}")
        # Fall back to non-cached version
        return library_recent()

@app.route('/api/library/recent')
def library_recent():
    """Get recently added movies from Plex API."""
    try:
        plex = get_plex_for_request()
        # Get all movie library sections
        sections = plex.get_library_sections()
        movie_sections = [s for s in sections if s['type'] == 'movie']

        if not movie_sections:
            return jsonify({'movies': []})

        # Fetch recently added from all movie sections
        all_movies = []
        for section in movie_sections:
            items = plex.get_recently_added(section_id=section['id'], limit=20)
            for item in items:
                if item.get('type') == 'movie':
                    item['library_name'] = section['title']
                    all_movies.append(item)

        # Sort by added_at desc and take top 20
        all_movies.sort(key=lambda m: m.get('added_at', 0), reverse=True)
        all_movies = all_movies[:20]

        # Build response with proxy image URLs
        movies = []
        for m in all_movies:
            movie = {
                'rating_key': m['rating_key'],
                'title': m['title'],
                'year': m.get('year'),
                'summary': m.get('summary', ''),
                'content_rating': m.get('content_rating', ''),
                'duration': m.get('duration', 0),
                'added_at': m.get('added_at', 0),
                'guid': m.get('guid', ''),
                'rating': m.get('rating'),
                'genres': m.get('genres', []),
                'cast': m.get('cast', []),
                'library_name': m.get('library_name', ''),
                'poster_url': f"/api/img/plex?url={m['thumbPath']}" if m.get('thumbPath') else None,
                'backdrop_url': f"/api/img/plex?url={m['artPath']}" if m.get('artPath') else None,
                'clearlogo_url': f"/api/img/plex?url={m['clearLogoPath']}" if m.get('clearLogoPath') else None,
                'tmdb_id': m.get('tmdb_id'),
                'imdb_id': m.get('imdb_id'),
            }
            if movie['added_at']:
                movie['added_date'] = datetime.fromtimestamp(movie['added_at']).strftime('%Y-%m-%d')
            movies.append(movie)

        return jsonify({'movies': movies})

    except Exception as e:
        logger.error(f"Error fetching recent movies: {e}")
        return jsonify({'error': str(e), 'movies': []}), 500

@app.route('/api/library/episodes')
def library_recent_episodes():
    """Get recently added TV shows (unique shows from recently added episodes) via Plex HTTP API."""
    try:
        plex = get_plex_for_request()
        sections = plex.get_sections_lean()
        tv_sections = [s for s in sections if s['type'] == 'show']

        all_episodes = []
        for section in tv_sections:
            items = plex.get_recently_added(section_id=section['id'], limit=50)
            for item in items:
                if item.get('type') == 'episode':
                    item['_library'] = section['title']
                    all_episodes.append(item)

        # Sort by addedAt descending
        all_episodes.sort(key=lambda x: x.get('addedAt', 0), reverse=True)

        # Deduplicate by show (keep most recent episode per show)
        shows = []
        seen_show_keys = set()
        for ep in all_episodes:
            show_rk = ep.get('show_rating_key', 0)
            show_title = ep.get('show_title', ep.get('title', ''))
            key = show_rk or show_title
            if key in seen_show_keys:
                continue
            seen_show_keys.add(key)

            poster_url = None
            if show_rk:
                poster_url = f"/api/img/plex?url=/library/metadata/{show_rk}/thumb&r=2"
            elif ep.get('showThumbPath'):
                poster_url = f"/api/img/plex?url={ep['showThumbPath']}"

            shows.append({
                'show_rating_key': show_rk,
                'show_title': show_title,
                'show_year': ep.get('show_year', ep.get('year')),
                'latest_added_at': ep.get('addedAt', 0),
                'added_date': datetime.fromtimestamp(ep.get('addedAt', 0)).strftime('%Y-%m-%d') if ep.get('addedAt') else None,
                'poster_url': poster_url or 'https://via.placeholder.com/200x300/1a1a1a/808080?text=No+Poster',
            })
            if len(shows) >= 20:
                break

        return jsonify({'episodes': shows})

    except Exception as e:
        logger.error(f"Error fetching recent TV episodes: {e}")
        return jsonify({'error': str(e), 'episodes': []}), 500


@app.route('/api/library/episodes/cached')
def library_recent_episodes_cached():
    """Get cached recently added TV shows (sorted by most recent episode added) for ultra-fast loading."""
    try:
        limit = request.args.get('limit', 20, type=int)

        # Check if cache needs updating (but don't block)
        if library_cache.needs_update('recent_tv_episodes'):
            # Start update in background
            import threading
            threading.Thread(target=update_recent_tv_episodes_cache, daemon=True).start()

        # Return cached items immediately
        items = library_cache.get_recent_tv_episodes(limit=limit)

        return jsonify({
            'episodes': items,
            'from_cache': True,
            'cache_info': library_cache.get_cache_info().get('recent_tv_episodes', {})
        })

    except Exception as e:
        logger.error(f"Error getting cached recent TV episodes: {e}")
        return jsonify({'error': str(e), 'episodes': []}), 500


@app.route('/api/tmdb/person/<int:plex_actor_id>/credits')
def get_tmdb_person_credits(plex_actor_id):
    """Get TMDB credits for an actor.

    Accepts ?person_name=... query param (preferred).
    The plex_actor_id path param is kept for backwards compat but is ignored
    since CIFS Plex DB access has been removed.
    """
    try:
        actor_name = request.args.get('person_name', '').strip()

        if not actor_name:
            return jsonify({'credits': [], 'error': 'person_name query param required'}), 400

        # Search TMDB for this person
        search_response = requests.get(
            'https://api.themoviedb.org/3/search/person',
            params={
                'api_key': settings.get('apis.tmdb.api_key'),
                'query': actor_name
            },
            timeout=10
        )

        if not search_response.ok:
            return jsonify({'credits': []}), 500

        search_data = search_response.json()

        if not search_data.get('results'):
            return jsonify({'credits': []})

        # Find the most popular person with the exact name match (case-insensitive)
        # This prevents "Leonardo DiCaprio" from matching "The Lost Leonardo" documentary
        tmdb_person_id = None
        person_profile_path = None
        person_name = actor_name
        for person in search_data['results']:
            if person.get('name', '').lower() == actor_name.lower():
                tmdb_person_id = person['id']
                person_profile_path = person.get('profile_path')
                person_name = person.get('name', actor_name)
                break

        # If no exact match, use the most popular result
        if not tmdb_person_id:
            # Sort by popularity descending
            sorted_results = sorted(search_data['results'], key=lambda x: x.get('popularity', 0), reverse=True)
            if sorted_results:
                tmdb_person_id = sorted_results[0]['id']
                person_profile_path = sorted_results[0].get('profile_path')
                person_name = sorted_results[0].get('name', actor_name)
            else:
                return jsonify({'credits': []})

        # Get combined credits for this person
        credits_response = requests.get(
            f'https://api.themoviedb.org/3/person/{tmdb_person_id}/combined_credits',
            params={'api_key': settings.get('apis.tmdb.api_key')},
            timeout=10
        )

        if not credits_response.ok:
            return jsonify({'credits': []}), 500

        credits_data = credits_response.json()

        # Format the credits
        formatted_credits = []

        # Process both cast and crew
        all_credits = credits_data.get('cast', []) + credits_data.get('crew', [])

        # Deduplicate by ID
        seen_ids = set()

        for credit in all_credits:
            credit_id = credit.get('id')
            if credit_id in seen_ids:
                continue
            seen_ids.add(credit_id)

            media_type = credit.get('media_type', 'movie')

            # Get title and year based on media type
            if media_type == 'movie':
                title = credit.get('title', credit.get('original_title', ''))
                release_date = credit.get('release_date', '')
                year = release_date[:4] if release_date else None
            else:  # TV show
                title = credit.get('name', credit.get('original_name', ''))
                first_air_date = credit.get('first_air_date', '')
                year = first_air_date[:4] if first_air_date else None

            if not title:
                continue

            poster_path = credit.get('poster_path')
            poster_url = f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else None

            formatted_credits.append({
                'tmdb_id': credit_id,
                'title': title,
                'year': int(year) if year else None,
                'rating': credit.get('vote_average'),
                'vote_count': credit.get('vote_count', 0),
                'poster_url': poster_url,
                'media_type': media_type,
                'imdb_id': credit.get('imdb_id', '')
            })

        # Fetch IMDb votes for sorting
        items_for_votes = [{'title': c['title'], 'year': c['year'] or ''} for c in formatted_credits]
        imdb_votes = fetch_imdb_votes_batch(items_for_votes, max_items=50)

        # Add IMDb votes to credits and sort by IMDb votes descending
        for credit in formatted_credits:
            key = f"{credit['title'].lower()}_{credit['year'] or ''}"
            credit['imdb_votes'] = imdb_votes.get(key, 0)

        # Display rating must be IMDb (not TMDb vote_average).
        enrich_imdb_ratings(formatted_credits, settings=settings, max_items=50, replace_rating=True)
        for credit in formatted_credits:
            # Prefer votes from the batch votes helper when enrich left them empty.
            if not credit.get('imdb_votes'):
                key = f"{credit['title'].lower()}_{credit['year'] or ''}"
                credit['imdb_votes'] = imdb_votes.get(key, 0)

        formatted_credits.sort(key=lambda x: (-(x['imdb_votes'] or 0), x['title']))

        # Build person profile URL
        profile_url = f"https://image.tmdb.org/t/p/w185{person_profile_path}" if person_profile_path else None

        return jsonify({
            'credits': formatted_credits,
            'person': {
                'name': person_name,
                'profile_url': profile_url
            }
        })

    except Exception as e:
        logger.error(f"Error getting TMDB credits: {e}")
        return jsonify({'credits': []}), 500


_person_credits_cache: Dict[str, tuple] = {}  # person_id → (timestamp, data)
_PERSON_CREDITS_TTL = 10 * 60  # 10 minutes


@app.route('/api/tmdb/person_by_id/<int:tmdb_person_id>/credits')
def get_tmdb_person_credits_by_id(tmdb_person_id):
    """Get TMDB credits for an actor using their TMDB person ID directly."""
    # Check cache
    cache_key = str(tmdb_person_id)
    cached = _person_credits_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _PERSON_CREDITS_TTL:
            return jsonify(data)

    try:
        api_key = settings.get('apis.tmdb.api_key')

        # Parallelize the two TMDb API calls
        def fetch_person():
            return requests.get(
                f'https://api.themoviedb.org/3/person/{tmdb_person_id}',
                params={'api_key': api_key},
                timeout=10
            )

        def fetch_credits():
            return requests.get(
                f'https://api.themoviedb.org/3/person/{tmdb_person_id}/combined_credits',
                params={'api_key': api_key},
                timeout=10
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            person_future = pool.submit(fetch_person)
            credits_future = pool.submit(fetch_credits)
            person_response = person_future.result()
            credits_response = credits_future.result()

        person_name = None
        person_profile_path = None
        person_biography = None
        person_birthday = None
        person_deathday = None
        person_birthplace = None
        person_known_for = None
        if person_response.ok:
            person_data = person_response.json()
            person_name = person_data.get('name')
            person_profile_path = person_data.get('profile_path')
            person_biography = person_data.get('biography') or None
            person_birthday = person_data.get('birthday') or None
            person_deathday = person_data.get('deathday') or None
            person_birthplace = person_data.get('place_of_birth') or None
            person_known_for = person_data.get('known_for_department') or None

        if not credits_response.ok:
            return jsonify({'credits': []}), 500

        credits_data = credits_response.json()

        # Format the credits
        formatted_credits = []

        # Process cast and crew separately to tag the role
        cast_credits = [(c, 'cast') for c in credits_data.get('cast', [])]
        crew_credits = [(c, 'crew') for c in credits_data.get('crew', [])]
        all_tagged = cast_credits + crew_credits

        # Deduplicate by ID (prefer cast entry over crew)
        seen_ids = set()

        for credit, credit_type in all_tagged:
            credit_id = credit.get('id')
            if credit_id in seen_ids:
                continue
            seen_ids.add(credit_id)

            media_type = credit.get('media_type', 'movie')

            # Get title and year based on media type
            if media_type == 'movie':
                title = credit.get('title', credit.get('original_title', ''))
                release_date = credit.get('release_date', '')
                year = release_date[:4] if release_date else None
            else:  # TV show
                title = credit.get('name', credit.get('original_name', ''))
                first_air_date = credit.get('first_air_date', '')
                year = first_air_date[:4] if first_air_date else None

            if not title:
                continue

            poster_path = credit.get('poster_path')
            poster_url = f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else None

            # Role info: character for cast, job for crew
            role = credit.get('character') or credit.get('job') or None
            department = credit.get('department') or ('Acting' if credit_type == 'cast' else None)

            formatted_credits.append({
                'tmdb_id': credit_id,
                'title': title,
                'year': int(year) if year else None,
                'rating': credit.get('vote_average'),
                'vote_count': credit.get('vote_count', 0),
                'poster_url': poster_url,
                'media_type': media_type,
                'imdb_id': credit.get('imdb_id', ''),
                'credit_type': credit_type,
                'department': department,
                'role': role,
            })

        # Display rating must be IMDb (not TMDb vote_average).
        enrich_imdb_ratings(formatted_credits, settings=settings, max_items=50, replace_rating=True)

        # Sort by TMDb vote_count descending, then title ascending
        formatted_credits.sort(key=lambda x: (-(x['vote_count'] or 0), x['title']))

        # Build person profile URL
        profile_url = f"https://image.tmdb.org/t/p/w185{person_profile_path}" if person_profile_path else None

        result = {
            'credits': formatted_credits,
            'person': {
                'name': person_name,
                'profile_url': profile_url,
                'biography': person_biography,
                'birthday': person_birthday,
                'deathday': person_deathday,
                'birthplace': person_birthplace,
                'known_for': person_known_for,
            }
        }

        # Cache the result
        _person_credits_cache[cache_key] = (time.time(), result)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting TMDB credits by ID: {e}")
        return jsonify({'credits': []}), 500


@app.route('/api/library/search/fast')
def library_search_fast():
    """Fast in-memory media search across all Plex sections (sub-millisecond after index load).

    Query params:
      q             - title substring search (case-insensitive)
      genre         - filter by genre tag (case-insensitive)
      content_rating - filter by content rating (e.g. PG-13)
      year          - filter by release year (integer)
      section       - filter by section id (integer)
      media_type    - filter by media type: 'movie' or 'show'
      limit         - max results (default 100)
    """
    try:
        import time as _time
        t0 = _time.monotonic()

        q = request.args.get('q', '').strip() or None
        genre = request.args.get('genre', '').strip() or None
        content_rating = request.args.get('content_rating', '').strip() or None
        year = request.args.get('year', type=int)
        section = request.args.get('section', type=int)
        media_type = request.args.get('media_type', '').strip() or None
        limit = request.args.get('limit', 100, type=int)

        results = fast_search_index.search(
            q=q,
            genre=genre,
            content_rating=content_rating,
            year=year,
            section=section,
            media_type=media_type,
            limit=limit,
        )

        elapsed_ms = round((_time.monotonic() - t0) * 1000, 2)
        status = fast_search_index.status()

        return jsonify({
            'results': results,
            'total': len(results),
            'elapsed_ms': elapsed_ms,
            'index_size': status['total_items'],
            'index_initialized': status['initialized'],
        })

    except Exception as e:
        logger.error(f"Error in fast search: {e}")
        return jsonify({'error': str(e), 'results': []}), 500


@app.route('/api/library/search')
def library_search():
    """Search library for movies and TV shows with typo tolerance via fast_search index.
    Falls back to Plex API if index isn't ready."""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'movies': [], 'tv_shows': []})

    try:
        # Try fast_search if index is initialized (typo-tolerant)
        status = fast_search_index.status()
        if status.get('initialized'):
            # Use fast search with fuzzy matching
            results = fast_search_index.search(q=query, limit=100, fuzzy=True)
            
            # Separate by media type and deduplicate across library sections
            all_movies = []
            all_tv = []
            seen_movies = {}  # key: (title_lower, year) -> item
            seen_tv = {}      # key: (title_lower, year) -> item
            
            # Build image proxy URLs relative to script root (for URL base support)
            script_root = request.script_root or ''
            
            for item in results:
                title_lower = item.get('title', '').lower()
                year = item.get('year')
                section_name = item.get('section_name', 'Library')
                key = (title_lower, year)
                
                # Format for frontend (matches Plex format)
                formatted = {
                    'title': item.get('title'),
                    'year': year,
                    'rating_key': item.get('rating_key'),
                    'library_name': section_name,
                    'libraries': [section_name],
                    'poster_url': f"{script_root}/api/img/plex?url={urllib.parse.quote(item['thumb'], safe='/')}" if item.get('thumb') else None,
                    'backdrop_url': f"{script_root}/api/img/plex?url={urllib.parse.quote(item['art'], safe='/')}" if item.get('art') else None,
                    'clearlogo_url': None,
                }
                if item.get('added_at'):
                    formatted['added_date'] = datetime.fromtimestamp(item['added_at']).strftime('%Y-%m-%d')
                
                if item.get('media_type') == 'movie':
                    if key in seen_movies:
                        # Add library to existing entry
                        if section_name not in seen_movies[key]['libraries']:
                            seen_movies[key]['libraries'].append(section_name)
                    else:
                        seen_movies[key] = formatted
                        all_movies.append(formatted)
                elif item.get('media_type') == 'show':
                    if key in seen_tv:
                        # Add library to existing entry
                        if section_name not in seen_tv[key]['libraries']:
                            seen_tv[key]['libraries'].append(section_name)
                    else:
                        seen_tv[key] = formatted
                        all_tv.append(formatted)
            
            return jsonify({'movies': all_movies, 'tv_shows': all_tv})
        
        # Fallback to Plex API if index not ready
        plex = get_plex_for_request()
        sections = plex.get_library_sections()
        movie_sections = [s for s in sections if s['type'] == 'movie']
        tv_sections = [s for s in sections if s['type'] == 'show']

        # Search movies across all movie libraries
        all_movies = []
        seen_titles = set()
        for section in movie_sections:
            results = plex.search_library(query, section_id=section['id'], media_type=1, limit=30)
            for m in results:
                # Deduplicate by title+year
                key = f"{m['title'].lower()}_{m.get('year', '')}"
                if key not in seen_titles:
                    seen_titles.add(key)
                    m['library_name'] = section['title']
                    m['libraries'] = [section['title']]
                    m['poster_url'] = f"/api/img/plex?url={m['thumbPath']}" if m.get('thumbPath') else None
                    m['backdrop_url'] = f"/api/img/plex?url={m['artPath']}" if m.get('artPath') else None
                    m['clearlogo_url'] = f"/api/img/plex?url={m['clearLogoPath']}" if m.get('clearLogoPath') else None
                    if m.get('added_at'):
                        m['added_date'] = datetime.fromtimestamp(m['added_at']).strftime('%Y-%m-%d')
                    all_movies.append(m)
                else:
                    # Add library to existing entry
                    for existing in all_movies:
                        if f"{existing['title'].lower()}_{existing.get('year', '')}" == key:
                            if section['title'] not in existing.get('libraries', []):
                                existing['libraries'].append(section['title'])
                            break

        # Search TV shows
        all_tv = []
        seen_shows = set()
        for section in tv_sections:
            results = plex.search_library(query, section_id=section['id'], media_type=2, limit=30)
            for s in results:
                key = f"{s['title'].lower()}_{s.get('year', '')}"
                if key not in seen_shows:
                    seen_shows.add(key)
                    s['library_name'] = section['title']
                    s['libraries'] = [section['title']]
                    s['poster_url'] = f"/api/img/plex?url={s['thumbPath']}" if s.get('thumbPath') else None
                    all_tv.append(s)
                else:
                    for existing in all_tv:
                        if f"{existing['title'].lower()}_{existing.get('year', '')}" == key:
                            if section['title'] not in existing.get('libraries', []):
                                existing['libraries'].append(section['title'])
                            break

        # Search for people (actors/directors) -- Plex API doesn't have a direct people search,
        # so we skip this for now (was DB-only feature)
        people = []

        return jsonify({
            'movies': all_movies[:30],
            'tv_shows': all_tv[:30],
            'people': people,
            'search_time': 'api',
        })

    except Exception as e:
        logger.error(f"Error searching library: {e}")
        return jsonify({'error': str(e), 'movies': [], 'tv_shows': []}), 500


@app.route('/api/check-ownership')
def check_ownership():
    """Check if a movie is owned in the Plex library with flexible matching."""
    title = request.args.get('title', '').strip()
    year = request.args.get('year', '').strip()
    year = int(year) if year and year.isdigit() else None
    tmdb_id = request.args.get('tmdb_id', '').strip()
    imdb_id = request.args.get('imdb_id', '').strip()

    if not title and not imdb_id:
        return jsonify({'owned': False, 'error': 'Title or IMDB ID required'})

    force = request.args.get('force', '0') == '1'

    # Check cache first (skip when force=1)
    if not force:
        cached = get_cached_ownership(title, year, tmdb_id, imdb_id)
        if cached is not None:
            logger.info(f"Ownership cache hit for: {title} ({year})")
            return jsonify(cached)

    try:
        import re as _re_clean
        index = get_plex_for_request().build_ownership_index(force=force)
        lib_movies = index['movies']
        lib_movies_by_title = index['movies_by_title']
        lib_movies_by_tmdb = index.get('movies_by_tmdb') or {}

        def _clean_title(t):
            t = t.lower().strip()
            t = _re_clean.sub(r'[^\w\s]', '', t).strip()
            return _re_clean.sub(r'\s+', ' ', t)

        def _strip_article(t):
            """Additional fallback only — The Runner ↔ Runner. Does not replace _clean_title."""
            t = _clean_title(t)
            return _re_clean.sub(r'^(the|a|an)\s+', '', t).strip()

        found_entry = None

        # Strategy 1: IMDB ID match
        if imdb_id and not found_entry:
            for entry in lib_movies.values():
                if entry.get('imdb_id') == imdb_id:
                    found_entry = entry
                    logger.info(f"Found movie by IMDB ID: {imdb_id}")
                    break

        # Strategy 1b: TMDb ID match (index already keyed by tmdb; unused until now)
        if tmdb_id and not found_entry:
            try:
                tmdb_i = int(tmdb_id)
            except (TypeError, ValueError):
                tmdb_i = None
            if tmdb_i is not None and tmdb_i in lib_movies_by_tmdb:
                found_entry = lib_movies_by_tmdb[tmdb_i]
                logger.info(f"Found movie by TMDb ID: {tmdb_i}")

        # Strategy 2: Exact title|year
        if title and year and not found_entry:
            key = f"{title.lower()}|{int(year)}"
            if key in lib_movies:
                found_entry = lib_movies[key]
                logger.info(f"Found movie by exact title + year: {title} ({year})")

        # Strategy 3: Title-only with year tolerance
        if title and not found_entry:
            candidates = lib_movies_by_title.get(title.lower(), [])
            for c in candidates:
                if not year or not c['year'] or abs(int(year) - c['year']) <= 1:
                    found_entry = c
                    logger.info(f"Found movie by title match: {title}")
                    break

        # Strategy 4: Cleaned title match (same year)
        if title and year and not found_entry:
            clean_search = _clean_title(title)
            for entries in lib_movies_by_title.values():
                for c in entries:
                    if c.get('year') == int(year) and _clean_title(c['title']) == clean_search:
                        found_entry = c
                        logger.info(f"Found movie by cleaned title: {title}")
                        break
                if found_entry:
                    break

        # Strategy 5: leading-article strip fallback (The X ↔ X), same year only
        if title and year and not found_entry:
            bare_search = _strip_article(title)
            if bare_search:
                for entries in lib_movies_by_title.values():
                    for c in entries:
                        if c.get('year') == int(year) and _strip_article(c['title']) == bare_search:
                            found_entry = c
                            logger.info(f"Found movie by article-stripped title: {title}")
                            break
                    if found_entry:
                        break

        if found_entry:
            rk = found_entry['rating_key']
            # Fetch full metadata from Plex HTTP API
            meta = plex_service.get_metadata(rk)
            movie = {}
            if meta:
                movie = {
                    'rating_key': rk,
                    'title': meta.get('title', found_entry['title']),
                    'year': meta.get('year', found_entry['year']),
                    'summary': meta.get('summary', ''),
                    'rating': meta.get('rating'),
                    'duration': meta.get('duration', 0),
                    'content_rating': meta.get('content_rating', meta.get('contentRating', '')),
                    'library_name': found_entry['library_name'],
                    'genres': meta.get('genres', []),
                    'cast': [
                        {'tag': c.get('tag', ''), 'role': c.get('role', ''), 'thumb': None}
                        for c in meta.get('cast', [])[:15]
                    ],
                    'directors': meta.get('directors', []),
                }
            else:
                movie = {
                    'rating_key': rk,
                    'title': found_entry['title'],
                    'year': found_entry['year'],
                    'library_name': found_entry['library_name'],
                    'genres': [], 'cast': [], 'directors': [],
                }

            # Find copies across libraries via HTTP API
            copies = plex_service.find_movie_copies(
                movie['title'],
                tmdb_id=int(found_entry.get('tmdb_id')) if found_entry.get('tmdb_id') else None,
                imdb_id=found_entry.get('imdb_id'),
            )
            library_keys = {}
            for copy in copies:
                lib_name = copy.get('library_name', '')
                copy_rk = copy.get('ratingKey') or copy.get('rating_key')
                if lib_name and copy_rk and lib_name not in library_keys:
                    library_keys[lib_name] = copy_rk
            if not library_keys:
                library_keys = {found_entry['library_name']: rk}
            movie['library_keys'] = library_keys
            movie['libraries'] = list(library_keys.keys())

            # Enrich cast/directors with TMDB photos
            if (movie['cast'] or movie['directors']) and settings.get('apis.tmdb.api_key'):
                try:
                    search_response = requests.get(
                        "https://api.themoviedb.org/3/search/movie",
                        params={'api_key': settings.get('apis.tmdb.api_key'), 'query': movie['title'], 'year': movie['year']},
                        timeout=5
                    )
                    if search_response.ok:
                        search_data = search_response.json()
                        if search_data.get('results'):
                            _tmdb_id = search_data['results'][0]['id']
                            credits_response = requests.get(
                                f"https://api.themoviedb.org/3/movie/{_tmdb_id}/credits",
                                params={'api_key': settings.get('apis.tmdb.api_key')}, timeout=5
                            )
                            if credits_response.ok:
                                credits_data = credits_response.json()
                                tmdb_cast_map = {a['name'].lower(): {'profile_path': a.get('profile_path'), 'tmdb_person_id': a.get('id')} for a in credits_data.get('cast', [])}
                                for actor in movie['cast']:
                                    td = tmdb_cast_map.get(actor['tag'].lower())
                                    if td:
                                        actor['profile_path'] = td.get('profile_path')
                                        actor['tmdb_person_id'] = td.get('tmdb_person_id')
                                tmdb_dirs = {p['name'].lower(): {'profile_path': p.get('profile_path'), 'tmdb_person_id': p.get('id')} for p in credits_data.get('crew', []) if p.get('job') == 'Director'}
                                for d in movie['directors']:
                                    td = tmdb_dirs.get(d['name'].lower())
                                    if td:
                                        d['profile_path'] = td.get('profile_path')
                                        d['tmdb_person_id'] = td.get('tmdb_person_id')
                except Exception as e:
                    logger.warning(f"Failed to enrich cast/directors with TMDB photos: {e}")

            # Proxy image URLs
            movie['poster_url'] = f"/api/img/plex?url=/library/metadata/{rk}/thumb&r=2"
            movie['clearlogo_url'] = f"/api/img/plex?url=/library/metadata/{rk}/clearLogo"
            movie['backdrop_url'] = f"/api/img/plex?url=/library/metadata/{rk}/art"

            result = {'owned': True, 'movie': movie}
            set_cached_ownership(title, year, result, tmdb_id, imdb_id)
            return jsonify(result)
        else:
            result = {'owned': False}
            set_cached_ownership(title, year, result, tmdb_id, imdb_id)
            return jsonify(result)

    except Exception as e:
        logger.error(f"Error checking ownership: {e}")
        return jsonify({'owned': False, 'error': str(e)})


@app.route('/api/check-ownership-batch', methods=['POST'])
def check_ownership_batch():
    """Check ownership for multiple movies/TV shows at once (HTTP API based)."""
    data = request.get_json()
    if not data or ('movies' not in data and 'tv' not in data):
        return jsonify({'error': 'movies or tv array required'}), 400

    movies_to_check = data.get('movies', [])
    tv_to_check = data.get('tv', [])
    use_media_paths = data.get('use_media_paths', False)

    if not movies_to_check and not tv_to_check:
        return jsonify({'results': {}})

    # Build allowed library names from configured media.paths
    allowed_libraries = None
    if use_media_paths:
        media_paths = settings.get('media.paths') or []
        names = {p['name'].lower() for p in media_paths if isinstance(p, dict) and p.get('name')}
        if names:
            allowed_libraries = names

    try:
        import re as _re_batch
        try:
            index = get_plex_for_request().build_ownership_index()
        except Exception as idx_err:
            # Plex unavailable — degrade gracefully: report everything as
            # not-owned (with namespaced keys) rather than 500-ing the whole
            # page and stripping every ownership badge at once.
            logger.warning(f"Ownership index unavailable, returning all-unowned: {idx_err}")
            degraded = {}
            for m in movies_to_check:
                degraded[f"movie:{(m.get('title') or '').lower()}_{m.get('year') or ''}"] = {'owned': False}
            for s in tv_to_check:
                degraded[f"tv:{(s.get('title') or '').lower()}_{s.get('year') or ''}"] = {'owned': False}
            return jsonify({'results': degraded, 'degraded': True})
        lib_movies = index['movies']
        lib_movies_by_title = index['movies_by_title']
        lib_movies_by_tmdb = index.get('movies_by_tmdb', {})
        tv_shows = index['tv']
        tv_by_title = index['tv_by_title']
        tv_by_tmdb = index['tv_by_tmdb']
        tv_by_tvdb = index['tv_by_tvdb']

        def _in_library(entry):
            """Check if entry belongs to a configured library."""
            if allowed_libraries is None:
                return True
            return entry.get('library_name', '').lower() in allowed_libraries

        def _clean(t):
            t = t.lower().strip()
            t = _re_batch.sub(r'[^\w\s]', '', t).strip()
            return _re_batch.sub(r'\s+', ' ', t)

        def _to_int(v):
            """Coerce to int or None — a single bad id/year must not 500 the batch."""
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        results = {}

        # ---- Match movies ----
        # Keys are namespaced by media type ("movie:"/"tv:") so a movie and a
        # show sharing title+year (reboots/adaptations) don't overwrite each other.
        for movie in movies_to_check:
            title = movie.get('title', '') or ''
            year = movie.get('year')
            year_i = _to_int(year)
            tmdb_i = _to_int(movie.get('tmdb_id'))
            movie_key = f"movie:{title.lower()}_{year or ''}"

            found = False
            lib_movie = None

            # TMDb ID match (most reliable)
            if not found and tmdb_i is not None and tmdb_i in lib_movies_by_tmdb and _in_library(lib_movies_by_tmdb[tmdb_i]):
                lib_movie = lib_movies_by_tmdb[tmdb_i]
                found = True

            # Exact title|year
            if not found:
                lookup_key = f"{title.lower()}|{year}" if year else title.lower()
                if lookup_key in lib_movies and _in_library(lib_movies[lookup_key]):
                    lib_movie = lib_movies[lookup_key]
                    found = True

            # Title-only with year tolerance
            if not found:
                candidates = lib_movies_by_title.get(title.lower(), [])
                for c in candidates:
                    if _in_library(c) and (year_i is None or not c['year'] or abs(year_i - c['year']) <= 1):
                        lib_movie = c
                        found = True
                        break

            # Cleaned title match (same year)
            if not found and year_i is not None:
                clean_search = _clean(title)
                for entries in lib_movies_by_title.values():
                    for c in entries:
                        if _in_library(c) and c.get('year') == year_i and _clean(c['title']) == clean_search:
                            lib_movie = c
                            found = True
                            break
                    if found:
                        break

            # Leading-article strip fallback (The X ↔ X), after existing strategies
            if not found and year_i is not None:
                bare = _re_batch.sub(r'^(the|a|an)\s+', '', _clean(title)).strip()
                if bare:
                    for entries in lib_movies_by_title.values():
                        for c in entries:
                            if not _in_library(c) or c.get('year') != year_i:
                                continue
                            if _re_batch.sub(r'^(the|a|an)\s+', '', _clean(c['title'])).strip() == bare:
                                lib_movie = c
                                found = True
                                break
                        if found:
                            break

            if found and lib_movie:
                results[movie_key] = {
                    'owned': True,
                    'rating_key': lib_movie['rating_key'],
                    'plex_rating': lib_movie.get('rating'),
                    'library_name': lib_movie['library_name']
                }
            else:
                results[movie_key] = {'owned': False}

        # ---- Match TV shows ----
        for show_item in tv_to_check:
            title = show_item.get('title', '') or ''
            year = show_item.get('year')
            year_i = _to_int(year)
            tmdb_i = _to_int(show_item.get('tmdb_id'))
            tvdb_i = _to_int(show_item.get('tvdb_id'))
            show_key = f"tv:{title.lower()}_{year or ''}"

            found = False
            lib_show = None

            # tmdb_id match
            if not found and tmdb_i is not None and tmdb_i in tv_by_tmdb and _in_library(tv_by_tmdb[tmdb_i]):
                lib_show = tv_by_tmdb[tmdb_i]
                found = True

            # tvdb_id match
            if not found and tvdb_i is not None and tvdb_i in tv_by_tvdb and _in_library(tv_by_tvdb[tvdb_i]):
                lib_show = tv_by_tvdb[tvdb_i]
                found = True

            # Exact title|year
            if not found:
                lookup_key = f"{title.lower()}|{year}" if year else title.lower()
                if lookup_key in tv_shows and _in_library(tv_shows[lookup_key]):
                    lib_show = tv_shows[lookup_key]
                    found = True

            # Title-only with year tolerance
            if not found:
                candidates = tv_by_title.get(title.lower(), [])
                for c in candidates:
                    if _in_library(c) and (year_i is None or not c['year'] or abs(year_i - c['year']) <= 1):
                        lib_show = c
                        found = True
                        break

            # Cleaned title match
            if not found and year_i is not None:
                clean_search = _clean(title)
                for entries in tv_by_title.values():
                    for c in entries:
                        if _in_library(c) and c.get('year') == year_i and _clean(c['title']) == clean_search:
                            lib_show = c
                            found = True
                            break
                    if found:
                        break

            if found and lib_show:
                results[show_key] = {
                    'owned': True,
                    'rating_key': lib_show['rating_key'],
                    'library_name': lib_show['library_name']
                }
            else:
                results[show_key] = {'owned': False}

        return jsonify({'results': results})

    except Exception as e:
        logger.error(f"Error in batch ownership check: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})


# =============================================================================
# Auth API routes
# =============================================================================

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    """Authenticate with email/name + PIN. Sets a session cookie on success."""
    try:
        data = request.get_json(force=True) or {}
        email = (data.get('email') or '').strip()
        name = (data.get('name') or '').strip()
        pin = str(data.get('pin') or '')

        identifier = email or name
        if not identifier or not pin:
            return jsonify({'success': False, 'error': 'Enter your email or username and password'}), 400

        from app import login_limiter
        client_ip = _access.client_ip(request, settings) or (request.remote_addr or '')
        wait = login_limiter.retry_after(client_ip, identifier)
        if wait:
            mins = max(1, (wait + 59) // 60)
            return jsonify({'success': False, 'error': f'Too many attempts. Try again in {mins} min.',
                            'retry_after': wait}), 429

        # Check user status before full auth — try email first, then name
        # Password first; the account status is only revealed to its owner.
        user = user_auth.authenticate(identifier, pin, email=email, any_status=True)
        if user and user.get('status', 'active') == 'pending':
            return jsonify({'success': False, 'error': 'Your account is pending admin approval. Please try again later.'}), 403
        if user and user.get('status', 'active') == 'denied':
            return jsonify({'success': False, 'error': 'Your account request was denied.'}), 403
        if user and user.get('status', 'active') != 'active':
            user = None
        if not user:
            login_limiter.record_failure(client_ip, identifier)
            return jsonify({'success': False, 'error': 'Invalid email, username, or password'}), 401
        login_limiter.record_success(client_ip, identifier)

        token = user_auth.create_session(user['id'])

        resp = make_response(jsonify({
            'success': True,
            'user': {
                'id': user['id'],
                'name': user['name'],
                'is_admin': bool(user['is_admin']),
            }
        }))
        
        
        set_auth_cookie(resp, token)
        
        
        return resp

    except Exception as e:
        logger.error(f"auth_login error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _signup_open() -> bool:
    """Account requests: always from the local network, from elsewhere only
    when security.allow_signup is on. Never on the request-portal host. Never in minimal mode."""
    if _minimal.enabled():
        return False
    if _access.is_portal_traffic(request, settings):
        return False
    return bool(settings.get('security.allow_signup')) or _access.is_local_request(request, settings)


@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    """Self-service account registration.

    Creates a user with status='pending' and notifies the admin via Telegram.
    Body: { name, email, pin }
    """
    import urllib.request as _urllib_req
    import json as _json

    try:
        data = request.get_json(force=True) or {}
        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        pin = str(data.get('pin') or '')

        if not user_auth.list_users():
            return jsonify({'success': False, 'error': 'Luma is not set up yet', 'needs_start': True}), 409
        if not _signup_open():
            return jsonify({'success': False, 'error': 'Sign-up is off'}), 403
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'}), 400
        if not email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        if user_auth.password_space_problem(pin):
            return jsonify({'success': False, 'error': user_auth.PASSWORD_SPACE_ERROR}), 400
        if not pin or len(pin) < 4:
            return jsonify({'success': False, 'error': 'Password must be at least 4 characters'}), 400

        # Basic email validation
        if '@' not in email or '.' not in email.split('@')[-1]:
            return jsonify({'success': False, 'error': 'Please enter a valid email address'}), 400

        # Names and emails share one sign-in namespace (case-insensitive)
        problem = user_auth.identity_conflict(name, email)
        if problem:
            code = 400 if '@' in problem else 409
            return jsonify({'success': False, 'error': problem}), code

        user = user_auth.create_user(name, pin, is_admin=False, status='pending', email=email)
        user_id = user['id']

        # Send Telegram notification with approve/deny buttons
        _send_account_request_notification(user_id, name, email)

        return jsonify({'success': True, 'message': 'Account request submitted'})

    except Exception as e:
        logger.error(f"auth_register error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _public_app_url() -> str:
    """Public base URL used in e-mails and Telegram links.

    APP_URL from the environment wins; otherwise the URL the current request
    came in on; otherwise empty.
    """
    url = (os.environ.get('APP_URL') or '').strip().rstrip('/')
    if url:
        return url
    try:
        from flask import has_request_context
        if has_request_context():
            return request.host_url.rstrip('/')
    except Exception:
        pass
    return ''


def _qbit_app_category() -> str:
    """qBittorrent category for downloads started from Luma (Settings → qBittorrent)."""
    return (settings.get('qbittorrent.app_category') or 'luma').strip() or 'luma'


def _telegram_admin_chat_id() -> str:
    """Telegram chat that receives admin approve/deny requests.

    TELEGRAM_ADMIN_CHAT_ID from the environment, else ``admin_chat_id`` in
    $LUMA_DATA_DIR/telegram_bot.json. Empty means admin Telegram alerts are off.
    """
    chat_id = (os.environ.get('TELEGRAM_ADMIN_CHAT_ID') or '').strip()
    if chat_id:
        return chat_id
    try:
        with open(telegram_bot_config_path()) as f:
            return str(__import__('json').load(f).get('admin_chat_id') or '').strip()
    except Exception:
        return ''


def _make_admin_action_url(action: str, user_id: int) -> str:
    """Signed, single-use link (24 h) for an admin action button in Telegram.

    Opening it shows a confirm page; only the button on that page (a POST)
    changes anything. See app.action_tokens.
    """
    from urllib.parse import urlencode
    from app import action_tokens
    fields = action_tokens.sign(app.config['SECRET_KEY'], action, user_id)
    return f"{_public_app_url()}/api/admin/telegram-action?{urlencode(fields)}"


def _send_account_request_notification(user_id: int, name: str, email: str = None) -> None:
    """Send Telegram message to admin with approve/deny URL buttons."""
    import urllib.request as _urllib_req

    ADMIN_CHAT_ID = _telegram_admin_chat_id()
    if not ADMIN_CHAT_ID:
        logger.info("Admin Telegram alert skipped: TELEGRAM_ADMIN_CHAT_ID is not set")
        return

    bot_token = ''
    try:
        with open(telegram_bot_config_path()) as f:
            cfg = __import__('json').load(f)
        bot_token = cfg.get('bot_token', '')
    except Exception as e:
        logger.error(f"_send_account_request_notification: could not read bot token: {e}")
        return

    if not bot_token:
        logger.error("_send_account_request_notification: no bot token")
        return

    email_line = f"\nEmail: {email}" if email else ""
    message_text = f"🆕 New account request: *{name}*{email_line}\nWants access to Luma. Approve or deny?"
    payload = {
        'chat_id': ADMIN_CHAT_ID,
        'text': message_text,
        'parse_mode': 'Markdown',
        'reply_markup': {
            'inline_keyboard': [[
                {'text': '✅ Approve', 'url': _make_admin_action_url('approve_user', user_id)},
                {'text': '❌ Deny',    'url': _make_admin_action_url('deny_user', user_id)},
            ]]
        }
    }

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        req = _urllib_req.Request(
            url,
            data=__import__('json').dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _urllib_req.urlopen(req, timeout=15) as resp:
            result = __import__('json').loads(resp.read().decode('utf-8'))
            if result.get('ok'):
                logger.info(f"Sent account request notification for user {user_id} ({name})")
            else:
                logger.error(f"Telegram API error sending account request notification: {result}")
    except Exception as e:
        logger.error(f"_send_account_request_notification send error: {e}")


def _send_approval_email(to_email: str, display_name: str) -> None:
    """Send a Gmail notification to the user that their account has been approved."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    gmail_address = os.environ.get('GMAIL_ADDRESS', '')
    gmail_app_password = os.environ.get('GMAIL_APP_PASSWORD', '')

    if not gmail_address or not gmail_app_password:
        logger.warning("_send_approval_email: GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set, skipping email")
        return

    if not to_email:
        logger.warning("_send_approval_email: no email address for user, skipping")
        return

    # Build the app URL from request context or fall back to env
    app_url = _public_app_url()

    subject = "Your Luma account has been approved!"
    html_body = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 40px 20px;">
  <div style="max-width: 480px; margin: 0 auto; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 40px 36px;">
    <h1 style="color: #ffffff; font-size: 22px; margin: 0 0 16px;">Welcome to Luma!</h1>
    <p style="color: #a0a0a0; font-size: 15px; line-height: 1.6;">
      Hi {display_name},
    </p>
    <p style="color: #a0a0a0; font-size: 15px; line-height: 1.6;">
      Your account has been approved. You can now sign in and start exploring.
    </p>
    <a href="{app_url}/login" style="display: inline-block; margin-top: 20px; padding: 13px 32px; background: rgba(255,255,255,0.12); color: #ffffff; text-decoration: none; border-radius: 50px; font-weight: 700; font-size: 16px;">
      Sign In
    </a>
    <p style="color: #606060; font-size: 12px; margin-top: 32px;">
      If you didn't request this account, you can ignore this email.
    </p>
  </div>
</body>
</html>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = gmail_address
    msg['To'] = to_email

    # Plain text fallback
    text_body = (
        f"Hi {display_name},\n\n"
        f"Your Luma account has been approved! You can now sign in at {app_url}/login\n\n"
        f"If you didn't request this account, you can ignore this email."
    )
    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=15) as server:
            server.starttls()
            server.login(gmail_address, gmail_app_password)
            server.sendmail(gmail_address, to_email, msg.as_string())
        logger.info(f"Sent approval email to {to_email} ({display_name})")
    except Exception as e:
        logger.error(f"_send_approval_email error: {e}")


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """Clear the session cookie and invalidate the session."""
    try:
        token = request.cookies.get(AUTH_COOKIE)
        if token:
            user_auth.delete_session(token)
        resp = make_response(jsonify({'success': True}))
        
        
        clear_auth_cookie(resp)
        return resp
    except Exception as e:
        logger.error(f"auth_logout error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    """Return current user info, or 401 if not logged in."""
    user = _get_current_user()
    if not user:
        return jsonify({
            'authenticated': False,
            'needs_start': _start_flow.needs_start(),
            'pin_unlock': _start_flow.created_by_start() and not _start_flow.password_set(),
            'signup': _signup_open(),
        # Minimal builds: being signed out is an expected state, not an error
        # (no red 401 in the browser console on every signed-out load).
        }), (200 if _minimal.enabled() else 401)
    # Include user settings
    user_settings_data = user_auth.get_user_settings(user['id'])
    # Fetch full user record to get email
    full_user = user_auth.get_user_by_id(user['id'])
    # Non-admin users need setup if they have no usable Plex config yet.
    # "pending" with no own server also counts as needs_setup (waiting screen).
    is_admin = bool(user['is_admin'])
    has_shared = user_settings_data.get('use_shared_server') is True
    has_own = bool(user_settings_data.get('plex_url'))
    plex_pending = user_settings_data.get('plex_access_status') == 'pending'
    # Needs setup if: no approved shared access AND no own server configured
    needs_setup = not is_admin and not has_shared and not has_own
    plex_access_status = user_settings_data.get('plex_access_status', None)
    auth_required = (settings.get('security.auth_required') or 'disabled_local').strip()
    return jsonify({
        'authenticated': True,
        'user': {
            'id': user['id'],
            'name': user['name'],
            'is_admin': is_admin,
            'email': full_user.get('email') if full_user else None,
        },
        'user_settings': user_settings_data,
        'needs_setup': needs_setup,
        'plex_access_status': plex_access_status,
        'implicit': bool(getattr(g, 'implicit_user', False)),
        'auth_required': auth_required,
        'start_in_progress': _start_flow._in_progress(user),
    })


# ---------------------------------------------------------------------------
# Security: access mode and the owner PIN (Settings → Security)
# ---------------------------------------------------------------------------

def _pin_state() -> bool:
    """Whether a sign-in PIN exists (Start-flow installs begin without one)."""
    if not _start_flow.created_by_start():
        return True
    return bool(_start_flow._load_state().get('pin_set'))


@app.route('/api/auth/unlock', methods=['POST'])
def auth_unlock():
    """PIN-only sign-in for the owner of a single-account install."""
    from app import login_limiter
    if not _start_flow.created_by_start() or not _pin_state():
        return jsonify({'success': False, 'error': 'PIN sign-in is not available'}), 400
    pin = str((request.get_json(silent=True) or {}).get('pin') or '')
    if not pin:
        return jsonify({'success': False, 'error': 'Enter your PIN'}), 400
    ip = _access.client_ip(request, settings) or (request.remote_addr or "")
    wait = login_limiter.retry_after(ip, 'owner')
    if wait:
        mins = max(1, (wait + 59) // 60)
        return jsonify({'success': False, 'error': f'Too many attempts. Try again in {mins} min.',
                        'retry_after': wait}), 429
    owner = _access.owner(user_auth)
    user = user_auth.authenticate(owner['name'], pin) if owner and pin else None
    if not user:
        login_limiter.record_failure(ip, 'owner')
        return jsonify({'success': False, 'error': 'Wrong PIN'}), 401
    login_limiter.record_success(ip, 'owner')
    token = user_auth.create_session(user['id'])
    resp = make_response(jsonify({'success': True}))
    
    
    set_auth_cookie(resp, token)
    
    
    return resp


@app.route('/api/security', methods=['GET'])
def security_get():
    err = _require_admin()
    if err:
        return err
    auth_required = (settings.get('security.auth_required') or 'disabled_local').strip()
    return jsonify({
        'auth_required': auth_required,
        'pin_set': _pin_state() or _start_flow.password_set(),
        'password_set': _start_flow.password_set(),
        'single_account': _start_flow.created_by_start(),
        'local_request': not _access.should_authenticate(settings, request),
        'trusted_proxies': settings.get('security.trusted_proxies') or '',
        'hostnames': settings.get('security.hostnames') or '',
        # Names LAN clients used in the last 30 days that aren't listed yet
        'seen_hostnames': [n for n in _lan_hosts.recent(_LAN_HOSTS_DIR, 30)
                           if n not in _access.configured_hostnames(settings)],
        'allow_signup': bool(settings.get('security.allow_signup')),
    })


_HOSTNAME_RE = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.?$', re.I)


@app.route('/api/security', methods=['PUT'])
def security_put():
    err = _require_admin()
    if err:
        return err
    if _minimal.enabled() and (getattr(g, 'implicit_user', False) or not (g.current_user or {}).get('token')):
        return jsonify({'error': 'Sign in first'}), 401
    body = request.get_json(silent=True) or {}
    auth_required = str(body.get('auth_required') or '').strip()
    pin = str(body.get('pin') or '')
    
    # Validate auth_required
    if auth_required and auth_required not in ('enabled', 'disabled_local'):
        return jsonify({'error': 'Invalid auth_required value'}), 400
    
    # Validate PIN
    if pin and (len(pin) != 4 or not pin.isdigit()):
        return jsonify({'error': 'Enter 4 digits'}), 400
    if pin and 'confirm' in body and str(body.get('confirm') or '') != pin:
        return jsonify({'error': "PINs don't match"}), 400
    
    # Update trusted_proxies
    if 'trusted_proxies' in body:
        proxies = str(body.get('trusted_proxies') or '').strip()
        parts = [p.strip() for p in proxies.replace(';', ',').split(',') if p.strip()]
        for part in parts:
            problem = _access.proxy_range_problem(part)
            if problem:
                return jsonify({'error': problem}), 400
        for part in parts:
            if _access.is_public_range(part):
                logger.warning("[security] trusted proxy %s is a public address range", part)
        settings.update({'security': {'trusted_proxies': proxies}})

    if 'hostnames' in body:
        names = [n for n in re.split(r'[,;\s]+', str(body.get('hostnames') or '')) if n]
        bad = [n for n in names if not _HOSTNAME_RE.match(n)]
        if bad:
            return jsonify({'error': f'Not a host name: {bad[0]}'}), 400
        settings.update({'security': {'hostnames': ', '.join(n.lower() for n in names)}})

    if 'allow_signup' in body:
        settings.update({'security': {'allow_signup': body.get('allow_signup') is True}})
    
    user = _get_current_user()
    if not user or not user.get('id'):
        return jsonify({'error': 'Sign in first'}), 400
    
    # Require PIN to be set for enabled mode
    if auth_required == 'enabled' and not pin and not _pin_state() and not _start_flow.password_set():
        return jsonify({'error': 'Set a PIN first'}), 400
    
    # Update PIN if provided
    if pin:
        user_auth.update_pin(user['id'], pin)
        if _start_flow.created_by_start():
            st = _start_flow._load_state()
            st['pin_set'] = True
            _start_flow._save_state(st)
    
    # Update auth_required
    if auth_required:
        settings.update({'security': {'auth_required': auth_required}})
    
    resp = make_response(jsonify({'ok': True, 'auth_required': auth_required or (settings.get('security.auth_required') or 'disabled_local'), 'pin_set': _pin_state() or _start_flow.password_set()}))
    
    # Keep this browser signed in after enabling authentication
    if auth_required == 'enabled' and getattr(g, 'implicit_user', False):
        
        token = user_auth.create_session(user['id'])
        set_auth_cookie(resp, token)
        
    
    return resp


# ---------------------------------------------------------------------------
# Per-user settings (plex config, preferred room, etc.)
# ---------------------------------------------------------------------------

@app.route('/api/user/settings', methods=['GET'])
def get_user_settings_route():
    """Get settings for the current logged-in user."""
    err = _require_auth()
    if err:
        return err
    user = _get_current_user()
    data = user_auth.get_user_settings(user['id'])
    # Also include the global plex URL so the frontend can show it
    data['_shared_plex_url'] = settings.get('plex.url', '')
    return jsonify({'success': True, 'settings': data})


@app.route('/api/user/settings', methods=['PUT'])
def update_user_settings_route():
    """Update settings for the current logged-in user."""
    err = _require_auth()
    if err:
        return err
    user = _get_current_user()
    data = request.get_json(force=True) or {}
    # Only allow user-scoped keys
    allowed = {
        'plex_url', 'plex_token', 'use_shared_server',
        'preferred_room_id', 'preferred_device_id', 'preferred_service_id',
        'radarr_url', 'radarr_api_key', 'radarr_quality_profile_id',
        'sonarr_url', 'sonarr_api_key',
        'rooms',
    }
    filtered = {k: v for k, v in data.items() if k in allowed}
    user_auth.save_user_settings(user['id'], filtered)
    # Invalidate cached PlexService when Plex credentials change
    if 'plex_url' in filtered or 'plex_token' in filtered:
        from app.plex_service_registry import invalidate_user
        invalidate_user(user['id'])
    if 'radarr_url' in filtered or 'radarr_api_key' in filtered:
        from app.radarr_service_registry import invalidate_user as invalidate_radarr
        invalidate_radarr(user['id'])
    if 'sonarr_url' in filtered or 'sonarr_api_key' in filtered:
        from app.sonarr_service_registry import invalidate_user as invalidate_sonarr
        invalidate_sonarr(user['id'])
    return jsonify({'success': True})


@app.route('/api/user/plex/health')
def user_plex_health():
    """Validate the current user's personal Plex connection."""
    err = _require_auth()
    if err:
        return err
    user = _get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    us = user_auth.get_user_settings(user['id']) or {}
    url = us.get('plex_url')
    token = us.get('plex_token')
    if not url or not token:
        return jsonify({'success': False, 'error': 'No personal Plex server configured'}), 400
    try:
        from app.plex_service import PlexService
        svc = PlexService(url, token)
        health = svc.health_check()
        return jsonify({'success': True, **health})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 200


@app.route('/api/user/test/plex', methods=['POST'])
def user_test_plex():
    """Test a personal Plex connection (any authenticated user)."""
    err = _require_auth()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        url = body.get('url', '').rstrip('/')
        token = body.get('token', '')
        if not url:
            return jsonify({'success': False, 'message': 'Plex URL is required'}), 400
        if not token:
            return jsonify({'success': False, 'message': 'Plex token is required'}), 400

        from app.plex_service import PlexService
        svc = PlexService(url, token)
        health = svc.health_check()
        return jsonify({
            'success': True,
            'message': f"Connected to Plex server: {health.get('server_name', 'Unknown')}",
            'details': health,
        })
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (5s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to Plex URL'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/user/test/radarr', methods=['POST'])
def user_test_radarr():
    """Test a personal Radarr connection (any authenticated user)."""
    err = _require_auth()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        url = body.get('url', '').rstrip('/')
        api_key = body.get('api_key', '')
        if not url:
            return jsonify({'success': False, 'message': 'Radarr URL is required'}), 400
        if not api_key:
            return jsonify({'success': False, 'message': 'Radarr API key is required'}), 400

        resp = requests.get(f"{url}/api/v3/system/status", headers={'X-Api-Key': api_key}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            version = data.get('version', 'unknown')
            return jsonify({'success': True, 'message': f'Connected to Radarr v{version}', 'details': {'version': version}})
        return jsonify({'success': False, 'message': f'Radarr returned status {resp.status_code}'}), 200
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (5s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to Radarr URL'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/user/test/sonarr', methods=['POST'])
def user_test_sonarr():
    """Test a personal Sonarr connection (any authenticated user)."""
    err = _require_auth()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        url = body.get('url', '').rstrip('/')
        api_key = body.get('api_key', '')
        if not url:
            return jsonify({'success': False, 'message': 'Sonarr URL is required'}), 400
        if not api_key:
            return jsonify({'success': False, 'message': 'Sonarr API key is required'}), 400

        resp = requests.get(f"{url}/api/v3/system/status", headers={'X-Api-Key': api_key}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            version = data.get('version', 'unknown')
            return jsonify({'success': True, 'message': f'Connected to Sonarr v{version}', 'details': {'version': version}})
        return jsonify({'success': False, 'message': f'Sonarr returned status {resp.status_code}'}), 200
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (5s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to Sonarr URL'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/user/request-shared-plex', methods=['POST'])
def request_shared_plex():
    """Request access to the admin's shared Plex server.
    Sets plex_access_status=pending and sends Telegram notification to admin."""
    err = _require_auth()
    if err:
        return err
    user = _get_current_user()
    user_id = user['id']

    # Don't allow admins or users who already have shared access
    us = user_auth.get_user_settings(user_id)
    if us.get('use_shared_server') is True:
        return jsonify({'success': True, 'message': 'Already approved'})
    if us.get('plex_access_status') == 'pending':
        return jsonify({'success': True, 'message': 'Request already pending'})

    # Save pending status
    user_auth.save_user_settings(user_id, {'plex_access_status': 'pending'})

    # Send Telegram notification to admin
    full_user = user_auth.get_user_by_id(user_id)
    name = full_user.get('name', '?') if full_user else '?'
    email = full_user.get('email', '') if full_user else ''
    _send_plex_access_request_notification(user_id, name, email)

    return jsonify({'success': True, 'message': 'Request submitted'})


def _send_plex_access_request_notification(user_id: int, name: str, email: str = None) -> None:
    """Send Telegram message to admin with approve/deny buttons for Plex server access."""
    import urllib.request as _urllib_req

    ADMIN_CHAT_ID = _telegram_admin_chat_id()
    if not ADMIN_CHAT_ID:
        logger.info("Admin Telegram alert skipped: TELEGRAM_ADMIN_CHAT_ID is not set")
        return

    bot_token = ''
    try:
        with open(telegram_bot_config_path()) as f:
            cfg = __import__('json').load(f)
        bot_token = cfg.get('bot_token', '')
    except Exception as e:
        logger.error(f"_send_plex_access_request_notification: could not read bot token: {e}")
        return

    if not bot_token:
        logger.error("_send_plex_access_request_notification: no bot token")
        return

    email_line = f"\nEmail: {email}" if email else ""
    message_text = (
        f"🔑 Plex Server Access Request\n\n"
        f"*{name}*{email_line}\n"
        f"is requesting access to your shared Plex server."
    )
    payload = {
        'chat_id': ADMIN_CHAT_ID,
        'text': message_text,
        'parse_mode': 'Markdown',
        'reply_markup': {
            'inline_keyboard': [[
                {'text': '✅ Grant Access', 'url': _make_admin_action_url('approve_plex', user_id)},
                {'text': '❌ Deny',         'url': _make_admin_action_url('deny_plex', user_id)},
            ]]
        }
    }

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        req = _urllib_req.Request(
            url,
            data=__import__('json').dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _urllib_req.urlopen(req, timeout=15) as resp:
            result = __import__('json').loads(resp.read().decode('utf-8'))
            if result.get('ok'):
                logger.info(f"Sent Plex access request notification for user {user_id} ({name})")
            else:
                logger.error(f"Telegram API error sending Plex access notification: {result}")
    except Exception as e:
        logger.error(f"_send_plex_access_request_notification send error: {e}")


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route('/api/admin/users', methods=['GET'])
def admin_list_users():
    err = _require_admin()
    if err:
        return err
    try:
        return jsonify({'users': user_auth.list_users()})
    except Exception as e:
        logger.error(f"admin_list_users error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/users', methods=['POST'])
def admin_create_user():
    err = _require_admin()
    if err:
        return err
    try:
        data = request.get_json(force=True) or {}
        name = (data.get('name') or '').strip()
        pin = str(data.get('pin') or '')
        is_admin = bool(data.get('is_admin', False))

        if not name:
            return jsonify({'error': 'Name is required'}), 400
        if user_auth.password_space_problem(pin):
            return jsonify({'error': user_auth.PASSWORD_SPACE_ERROR}), 400
        if not pin or len(pin) < 4:
            return jsonify({'error': 'Password must be at least 4 characters'}), 400

        user = user_auth.create_user(name, pin, is_admin=is_admin)
        return jsonify({'success': True, 'user': user}), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        logger.error(f"admin_create_user error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/users/<int:user_id>/pin', methods=['PUT'])
def admin_reset_pin(user_id: int):
    err = _require_admin()
    if err:
        return err
    try:
        data = request.get_json(force=True) or {}
        pin = str(data.get('pin') or '')
        if user_auth.password_space_problem(pin):
            return jsonify({'error': user_auth.PASSWORD_SPACE_ERROR}), 400
        if not pin or len(pin) < 4:
            return jsonify({'error': 'Password must be at least 4 characters'}), 400
        user = user_auth.get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        user_auth.update_pin(user_id, pin)
        user_auth.delete_all_sessions(user_id)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"admin_reset_pin error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
def admin_delete_user(user_id: int):
    err = _require_admin()
    if err:
        return err
    try:
        current = _get_current_user()
        if current and current['id'] == user_id:
            return jsonify({'error': 'Cannot delete your own account'}), 400
        user_auth.delete_user(user_id)
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"admin_delete_user error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/users/status')
def admin_users_status():
    """Return service connectivity status for every user (admin only).

    For each user, checks whether their configured Plex, Radarr, and Sonarr
    instances are reachable.  Checks run in parallel for speed.
    """
    err = _require_admin()
    if err:
        return err
    from app.user_auth import list_users, get_user_settings
    from concurrent.futures import ThreadPoolExecutor, as_completed

    users = list_users()
    results = []

    def check_user(u):
        us = get_user_settings(u['id']) or {}
        services = {}

        # Plex
        plex_url = us.get('plex_url')
        plex_token = us.get('plex_token')
        if plex_url and plex_token:
            try:
                from app.plex_service import PlexService
                svc = PlexService(plex_url, plex_token)
                health = svc.health_check()
                services['plex'] = {'configured': True, 'online': True, 'server_name': health.get('server_name', '')}
            except Exception:
                services['plex'] = {'configured': True, 'online': False}
        else:
            services['plex'] = {'configured': False, 'online': False}

        # Radarr
        radarr_url = us.get('radarr_url')
        radarr_key = us.get('radarr_api_key')
        if radarr_url and radarr_key:
            try:
                import requests as _req
                resp = _req.get(f"{radarr_url.rstrip('/')}/api/v3/system/status",
                              headers={'X-Api-Key': radarr_key}, timeout=5)
                services['radarr'] = {'configured': True, 'online': resp.status_code == 200}
            except Exception:
                services['radarr'] = {'configured': True, 'online': False}
        else:
            services['radarr'] = {'configured': False, 'online': False}

        # Sonarr
        sonarr_url = us.get('sonarr_url')
        sonarr_key = us.get('sonarr_api_key')
        if sonarr_url and sonarr_key:
            try:
                import requests as _req
                resp = _req.get(f"{sonarr_url.rstrip('/')}/api/v3/system/status",
                              headers={'X-Api-Key': sonarr_key}, timeout=5)
                services['sonarr'] = {'configured': True, 'online': resp.status_code == 200}
            except Exception:
                services['sonarr'] = {'configured': True, 'online': False}
        else:
            services['sonarr'] = {'configured': False, 'online': False}

        return {
            'user_id': u['id'],
            'name': u['name'],
            'email': u.get('email', ''),
            'is_admin': u.get('is_admin', False),
            'use_shared_server': us.get('use_shared_server', False),
            'services': services,
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(check_user, u): u for u in users}
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception:
                pass

    return jsonify({'users': sorted(results, key=lambda x: x['user_id'])})


def _send_plex_access_email(to_email: str, display_name: str) -> None:
    """Send a Gmail notification that the user has been granted Plex server access."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    gmail_address = os.environ.get('GMAIL_ADDRESS', '')
    gmail_app_password = os.environ.get('GMAIL_APP_PASSWORD', '')

    if not gmail_address or not gmail_app_password:
        logger.warning("_send_plex_access_email: GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set")
        return

    if not to_email:
        logger.warning("_send_plex_access_email: no email address for user")
        return

    app_url = _public_app_url()

    subject = "You now have access to the shared Plex server!"
    html_body = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 40px 20px;">
  <div style="max-width: 480px; margin: 0 auto; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 40px 36px;">
    <h1 style="color: #ffffff; font-size: 22px; margin: 0 0 16px;">Plex Access Granted</h1>
    <p style="color: #a0a0a0; font-size: 15px; line-height: 1.6;">
      Hi {display_name},
    </p>
    <p style="color: #a0a0a0; font-size: 15px; line-height: 1.6;">
      Your request to access the shared Plex server has been approved. You can now browse and stream the full library.
    </p>
    <a href="{app_url}" style="display: inline-block; margin-top: 20px; padding: 13px 32px; background: rgba(255,255,255,0.12); color: #ffffff; text-decoration: none; border-radius: 50px; font-weight: 700; font-size: 16px;">
      Open Luma
    </a>
    <p style="color: #666; font-size: 12px; margin-top: 32px;">
      — Luma
    </p>
  </div>
</body>
</html>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = gmail_address
    msg['To'] = to_email
    msg.attach(MIMEText(f"Hi {display_name}, your access to the shared Plex server has been approved. Visit {app_url} to start browsing.", 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(gmail_address, gmail_app_password)
            smtp.send_message(msg)
        logger.info(f"Sent Plex access email to {to_email}")
    except Exception as e:
        logger.error(f"_send_plex_access_email error: {e}")


# ── Telegram admin actions via signed URL buttons ─────────────────────────────
# Instead of callback_data (which requires a webhook), we use URL buttons with HMAC-signed links.
# Clicking Approve/Deny in Telegram opens the URL in a browser, which hits this
# endpoint directly — no webhook needed.

_ADMIN_ACTION_CSP = ("default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                     "frame-ancestors 'none'; base-uri 'none'")
_ADMIN_ACTION_LABELS = {
    'approve_user': ('Approve account', 'Approve {name} so they can sign in?', 'Approve'),
    'deny_user': ('Deny account', 'Deny {name} and remove the account?', 'Deny'),
    'approve_plex': ('Grant Plex access', 'Give {name} access to the shared Plex server?', 'Grant access'),
    'deny_plex': ('Deny Plex access', 'Deny {name} shared Plex access?', 'Deny'),
}


def _admin_action_page(title: str, message: str, success: bool = True, status: int = 200,
                       form: dict = None, button: str = '') -> Response:
    """Minimal HTML page for a Telegram admin action. Every interpolated value
    is HTML-escaped; the response forbids scripts (CSP) and MIME sniffing."""
    import html as _html
    esc = lambda v: _html.escape(str(v), quote=True)
    from app.theme import LUMA_BLUE, NAVY, NAVY_BG
    color = '#22c55e' if success else '#ef4444'
    form_html = ''
    if form:
        hidden = ''.join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in form.items())
        action_url = esc(request.script_root + request.path)
        form_html = (f'<form method="post" action="{action_url}" style="margin-top:24px;">{hidden}'
                     f'<button type="submit" style="font-size:16px;font-weight:600;padding:12px 28px;'
                     f'border:0;border-radius:999px;background:{LUMA_BLUE};color:#fff;">{esc(button)}</button></form>')
    page = f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{esc(title)} - Luma</title></head>
<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:{NAVY_BG} fixed {NAVY};
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#fff;">
<div style="text-align:center;padding:40px;max-width:400px;">
<h1 style="font-size:20px;font-weight:700;margin:0 0 8px;color:{color};">{esc(title)}</h1>
<p style="font-size:14px;color:rgba(255,255,255,0.7);line-height:1.6;margin:0;">{esc(message)}</p>
{form_html}
</div></body></html>"""
    resp = Response(page, status=status, mimetype='text/html')
    resp.headers['Content-Security-Policy'] = _ADMIN_ACTION_CSP
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp


_ADMIN_LINK_ERRORS = {
    'malformed': ('Invalid Link', 'This link is missing required parameters.', 400),
    'not configured': ('Invalid Link', 'Admin links are not configured on this server.', 403),
    'bad signature': ('Invalid Link', 'This link has an invalid signature.', 403),
    'expired': ('Link Expired', 'This link has expired. Open the request in Luma instead.', 410),
    'used': ('Link Already Used', 'This link has already been used.', 410),
    'revoked': ('Link No Longer Valid', 'This link can no longer be used. Open the request in Luma instead.', 410),
}


def _admin_link_error(reason: str) -> Response:
    title, message, status = _ADMIN_LINK_ERRORS.get(reason, _ADMIN_LINK_ERRORS['malformed'])
    return _admin_action_page(title, message, False, status)


@app.route('/api/admin/telegram-action', methods=['GET', 'POST'])
def admin_telegram_action():
    """Admin approve/deny from Telegram URL buttons.

    GET (opening the link) never changes state: it checks the link and shows a
    confirm page whose button POSTs the same signed fields. The POST consumes
    the single-use link and performs the action.
    """
    from app import action_tokens
    secret = app.config['SECRET_KEY']
    if request.method == 'GET':
        tok, reason = action_tokens.check(secret, request.args, _action_token_dir())
        if tok is None:
            return _admin_link_error(reason)
        u = user_auth.get_user_by_id(tok['uid'])
        if not u:
            return _admin_action_page('User Not Found', 'This user no longer exists.', False, 404)
        if not _admin_action_pending(tok['action'], u):
            return _admin_already_handled(u)
        title, question, button = _ADMIN_ACTION_LABELS[tok['action']]
        fields = {k: request.args.get(k, '') for k in ('action', 'uid', 'exp', 'nonce', 'sig')}
        return _admin_action_page(title, question.format(name=u['name']), tok['action'].startswith('approve'),
                                  form=fields, button=button)

    from app.auth_routes import is_same_origin
    origin = request.headers.get('Origin')
    if (origin and origin != 'null' and not is_same_origin(origin)) or \
            request.headers.get('Sec-Fetch-Site', '').lower() == 'cross-site':
        return _admin_action_page('Invalid Request', 'This request came from another site.', False, 403)
    try:
        tok, reason = action_tokens.consume(secret, request.form, _action_token_dir())
    except OSError as e:
        logger.error(f"admin_telegram_action: could not record the link: {e}")
        return _admin_action_page('Error', 'Nothing was changed. Try the link again.', False, 500)
    if tok is None:
        return _admin_link_error(reason)
    action, uid = tok['action'], tok['uid']
    # The link is released for a retry only when the failure came before any
    # write, or the stored state is provably unchanged. Otherwise it stays
    # used: releasing it would let an old approve link undo a later deny.
    attempted, before = False, None
    try:
        u = user_auth.get_user_by_id(uid)
        if not u:
            return _admin_action_page('User Not Found', 'This user no longer exists.', False, 404)
        if not _admin_action_pending(action, u):
            return _admin_already_handled(u)
        name = u['name']
        before = _admin_action_state(uid)
        attempted = True
        if action == 'approve_user':
            user_auth.approve_user(uid)
            if u.get('email'):
                try:
                    _send_approval_email(u['email'], name)
                except Exception:
                    pass
            logger.info(f"User {uid} approved via Telegram link")
            return _admin_action_page('Account Approved', f'{name} has been approved and can now sign in.')

        if action == 'deny_user':
            user_auth.deny_user(uid)
            logger.info(f"User {uid} denied via Telegram link")
            return _admin_action_page('Account Denied', f'{name} has been denied and removed.', False)

        if action == 'approve_plex':
            user_auth.save_user_settings(uid, {'use_shared_server': True, 'plex_access_status': 'approved'})
            if u.get('email'):
                try:
                    _send_plex_access_email(u['email'], name)
                except Exception:
                    pass
            logger.info(f"User {uid} granted Plex access via Telegram link")
            return _admin_action_page('Access Granted', f'{name} now has access to the shared Plex server.')
        user_auth.save_user_settings(uid, {'use_shared_server': False, 'plex_access_status': 'denied'})
        logger.info(f"User {uid} denied Plex access via Telegram link")
        return _admin_action_page('Access Denied', f'{name} was denied shared Plex access.', False)
    except Exception as e:
        unchanged = not attempted
        if attempted:
            try:
                unchanged = _admin_action_state(uid) == before
            except Exception:
                unchanged = False
        logger.error(f"admin_telegram_action error (state unchanged: {unchanged}): {e}")
        if not unchanged:
            return _admin_action_page('Error', 'The change was saved, but a later step failed. '
                                      'Check the request in Luma.', False, 500)
        try:
            action_tokens.release(_action_token_dir(), tok['nonce'])
            retry = 'Try the link again.'
        except OSError:
            retry = 'Open the request in Luma instead.'
        return _admin_action_page('Error', f'Nothing was changed. {retry}', False, 500)


def _admin_action_state(uid: int):
    """What a Telegram action can change: the account row and user settings."""
    return user_auth.get_user_by_id(uid), user_auth.get_user_settings(uid)


def _admin_action_pending(action: str, user: dict) -> bool:
    """Whether a Telegram link's action still applies to this user.

    Approvals and account denials need a pending request. Denying Plex access
    also works after an approval (it revokes it), but not twice."""
    if action in ('approve_user', 'deny_user'):
        return user.get('status') == 'pending'
    plex = user_auth.get_user_settings(user['id']).get('plex_access_status')
    if action == 'approve_plex':
        return plex == 'pending'
    return plex != 'denied'


def _admin_already_handled(user: dict):
    return _admin_action_page('Already Handled',
                              f"This request for {user['name']} was already handled.", False, 409)


# ── App version endpoint (used by KioskHub wrapper for auto-reload) ──────────
_app_version = None

def _get_app_version():
    """Return a version string that changes on each deploy/build."""
    global _app_version
    if _app_version is None:
        # The build's git SHA (baked in or read from .git; never a subprocess)
        from app import build_info
        sha = build_info.git_sha()
        if sha != build_info.UNKNOWN:
            _app_version = sha[:7]
        # Fallback: use process start time as a stable build stamp
        if _app_version is None:
            _app_version = datetime.now().strftime('%Y%m%d%H%M%S')
    return _app_version

def _short_sha(value) -> str:
    return str(value or '')[:7]


def _build_info_sha():
    from app import build_info
    return build_info.git_sha()


@app.route('/api/version')
def api_version():
    """Lightweight version endpoint for KioskHub auto-reload.
    Returns a version that changes on each deploy/restart."""
    version = _get_app_version()
    if _get_current_user():
        resp = jsonify({
            'version': version,
            'git': _build_info_sha(),
            'built_at': datetime.now().isoformat()
        })
    else:
        # Signed out: only the short build id kiosks need to notice a deploy.
        resp = jsonify({'version': _short_sha(version)})
    resp.headers['Cache-Control'] = 'no-store'
    return resp

_omdb_ratings_cache: Dict[str, tuple] = {}   # "title:year" → (timestamp, response_dict)
_OMDB_RATINGS_CACHE_TTL = 3600               # 1 hour
_OMDB_RATINGS_CACHE_MAX = 500

@app.route('/api/ratings/<title>')
def get_ratings(title):
    """Get movie ratings from OMDB API."""
    year = request.args.get('year', '')

    try:
        # Check cache
        now = time.time()
        cache_key = f"{title}:{year}"
        cached = _omdb_ratings_cache.get(cache_key)
        if cached and (now - cached[0]) < _OMDB_RATINGS_CACHE_TTL:
            return jsonify(cached[1])

        # Query OMDB API (settings → env var fallback)
        omdb_key = settings.get('apis.omdb.api_key') or Config.OMDB_API_KEY
        if not omdb_key:
            return jsonify({'error': 'No OMDb API key configured'}), 503
        omdb_url = f"http://www.omdbapi.com/?apikey={omdb_key}&t={title}"
        if year:
            omdb_url += f"&y={year}"

        response = requests.get(omdb_url, timeout=5)

        if response.ok:
            data = response.json()

            if data.get('Response') == 'True':
                ratings_data = {
                    'imdb': None,
                    'rotten_tomatoes_critic': None,
                    'rotten_tomatoes_audience': None,
                    'metacritic': None,
                    'imdb_id': data.get('imdbID')
                }

                # Extract IMDB rating
                if data.get('imdbRating') and data.get('imdbRating') != 'N/A':
                    ratings_data['imdb'] = {
                        'score': data['imdbRating'],
                        'votes': data.get('imdbVotes', '0').replace(',', '')
                    }

                # Extract Metacritic rating
                if data.get('Metascore') and data.get('Metascore') != 'N/A':
                    ratings_data['metacritic'] = {
                        'score': int(data['Metascore'])
                    }

                # Extract Rotten Tomatoes ratings
                for rating in data.get('Ratings', []):
                    if rating['Source'] == 'Rotten Tomatoes':
                        # This is the critic score
                        score = int(rating['Value'].replace('%', ''))
                        ratings_data['rotten_tomatoes_critic'] = {
                            'score': score,
                            'fresh': score >= 60
                        }

                # Cache successful result
                if len(_omdb_ratings_cache) >= _OMDB_RATINGS_CACHE_MAX:
                    oldest_key = min(_omdb_ratings_cache, key=lambda k: _omdb_ratings_cache[k][0])
                    del _omdb_ratings_cache[oldest_key]
                _omdb_ratings_cache[cache_key] = (now, ratings_data)

                return jsonify(ratings_data)
            else:
                return jsonify({'error': 'Movie not found'}), 404
        else:
            return jsonify({'error': 'Failed to fetch ratings'}), 500

    except Exception as e:
        logger.error(f"Error fetching ratings from OMDB: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/imdb/votes', methods=['POST'])
def get_imdb_votes_batch():
    """Get IMDb vote counts for multiple items via OMDB API."""
    data = request.get_json() or {}
    items = data.get('items', [])  # List of {title, year} objects

    if not items:
        return jsonify({'votes': {}})

    # Increase limit to handle more search results
    votes_map = fetch_imdb_votes_batch(items, max_items=50)
    return jsonify({'votes': votes_map})


def _parse_plex_ratings(rating_key):
    """Parse ratings from Plex metadata for a given ratingKey. Returns ratings dict or None on error."""
    import xml.etree.ElementTree as ET

    url = f"{settings.get('plex.url')}/library/metadata/{rating_key}"
    headers = {'X-Plex-Token': settings.get('plex.token')}
    response = requests.get(url, headers=headers, timeout=10)

    if response.status_code != 200:
        return None

    root = ET.fromstring(response.content)
    # Check for both Video (movies) and Directory (TV shows) elements
    item = root.find('.//Video')
    if item is None:
        item = root.find('.//Directory')
    if item is None:
        return None

    ratings_data = {
        'imdb': None,
        'rotten_tomatoes_critic': None,
        'rotten_tomatoes_audience': None,
        'metacritic': None,
        'tmdb': None
    }

    for rating_elem in item.findall('.//Rating'):
        rating_image = rating_elem.get('image', '')
        rating_value = rating_elem.get('value', '')
        rating_type = rating_elem.get('type', '')

        if not rating_value:
            continue

        try:
            value_float = float(rating_value)
        except:
            continue

        if 'imdb' in rating_image.lower():
            ratings_data['imdb'] = {
                'score': round(value_float, 1)
            }
        elif 'rottentomatoes' in rating_image.lower() and rating_type == 'critic':
            score_percent = int(value_float * 10)
            ratings_data['rotten_tomatoes_critic'] = {
                'score': score_percent,
                'fresh': score_percent >= 60
            }
        elif 'rottentomatoes' in rating_image.lower() and rating_type == 'audience':
            score_percent = int(value_float * 10)
            ratings_data['rotten_tomatoes_audience'] = {
                'score': score_percent,
                'upright': score_percent >= 60
            }
        elif 'themoviedb' in rating_image.lower():
            ratings_data['tmdb'] = {
                'score': round(value_float, 1)
            }
        elif 'metacritic' in rating_image.lower():
            ratings_data['metacritic'] = {
                'score': int(value_float)
            }

    return ratings_data


_plex_ratings_cache: Dict[str, tuple] = {}   # rating_key → (timestamp, response_dict)
_PLEX_RATINGS_CACHE_TTL = 900                # 15 minutes
_PLEX_RATINGS_CACHE_MAX = 300

@app.route('/api/plex/ratings/<rating_key>')
def get_plex_ratings(rating_key):
    """Get ratings from Plex API for a movie or TV show by ratingKey."""
    try:
        # Check cache
        now = time.time()
        cached = _plex_ratings_cache.get(rating_key)
        if cached and (now - cached[0]) < _PLEX_RATINGS_CACHE_TTL:
            return jsonify(cached[1])

        ratings_data = _parse_plex_ratings(rating_key)
        if ratings_data is None:
            return jsonify({'error': 'Item not found in Plex'}), 404

        # Cache successful result
        if len(_plex_ratings_cache) >= _PLEX_RATINGS_CACHE_MAX:
            oldest_key = min(_plex_ratings_cache, key=lambda k: _plex_ratings_cache[k][0])
            del _plex_ratings_cache[oldest_key]
        _plex_ratings_cache[rating_key] = (now, ratings_data)

        return jsonify(ratings_data)

    except Exception as e:
        logger.error(f"Error fetching ratings from Plex API: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/plex/ratings/resolve')
def resolve_plex_ratings():
    """Resolve tmdb_id to a Plex ratingKey, then return ratings."""
    null_result = {
        'imdb': None,
        'rotten_tomatoes_critic': None,
        'rotten_tomatoes_audience': None,
        'metacritic': None,
        'tmdb': None
    }
    try:
        tmdb_id = request.args.get('tmdb_id')
        if not tmdb_id:
            return jsonify(null_result)

        rating_key = _resolve_rating_key_by_guid('tmdb', tmdb_id)
        if not rating_key:
            return jsonify(null_result)

        ratings_data = _parse_plex_ratings(rating_key)
        if ratings_data is None:
            return jsonify(null_result)

        logger.info(f"Plex ratings resolved for tmdb={tmdb_id} (rk={rating_key}): {ratings_data}")
        return jsonify(ratings_data)

    except Exception as e:
        logger.error(f"Error resolving Plex ratings: {e}")
        return jsonify(null_result)

@app.route('/api/rt/reviews')
def get_rt_reviews():
    """Get Rotten Tomatoes reviews for a movie or TV show."""
    try:
        title = request.args.get('title')
        if not title:
            return jsonify({'success': False, 'error': 'Title is required'}), 400

        year = request.args.get('year')
        if year:
            try:
                year = int(year)
            except ValueError:
                year = None

        limit = request.args.get('limit', 20)
        try:
            limit = int(limit)
            limit = max(1, min(limit, 100))  # Clamp between 1 and 100
        except ValueError:
            limit = 20

        media_type = request.args.get('media_type', 'movie')
        if media_type not in ('movie', 'tv'):
            media_type = 'movie'

        # Fetch reviews from RT scraper
        result = rt_scraper.get_reviews(title, year, limit, media_type)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error fetching RT reviews: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ---------------------------------------------------------------------------
# UltraBlurColors — 6-hour memory cache
# ---------------------------------------------------------------------------
import re as _re_ultrablur
_ultrablur_cache: dict = {}          # ratingKey → {colors, ts}
_ultrablur_guid_cache: dict = {}     # "tmdb:123" / "imdb:tt123" → ratingKey
_ULTRABLUR_TTL = 6 * 3600           # 6 hours
_HEX6 = _re_ultrablur.compile(r'^[0-9a-fA-F]{6}$')


def _fetch_ultrablur_colors(rating_key: int) -> dict:
    """Fetch UltraBlurColors from Plex for a given ratingKey.

    Returns palette-format dict with blobs array and legacy corner fields.
    The 4 Plex corners are mapped to spatial positions, and a 5th center
    blob is synthesized by averaging the 4 corner colors.
    """
    import time
    null_result = _make_null_palette_result()

    # Check cache — invalidate legacy entries missing 'format' key
    cached = _ultrablur_cache.get(rating_key)
    if cached and (time.time() - cached['ts']) < _ULTRABLUR_TTL:
        if cached['colors'].get('format') == 'palette':
            return cached['colors']
        # Legacy cached entry — fall through to re-fetch

    # Fetch via plex_service
    root = plex_service._get(f'/library/metadata/{rating_key}')
    if root is None:
        _ultrablur_cache[rating_key] = {'colors': null_result, 'ts': time.time()}
        return null_result

    ultrablur = root.find('.//UltraBlurColors')
    if ultrablur is None:
        _ultrablur_cache[rating_key] = {'colors': null_result, 'ts': time.time()}
        return null_result

    # Parse the 4 corner hex values
    corner_hexes = {}
    for key in ('topLeft', 'topRight', 'bottomRight', 'bottomLeft'):
        raw = ultrablur.get(key, '')
        if _HEX6.match(raw):
            corner_hexes[key] = f'#{raw}'
        else:
            corner_hexes[key] = None

    # Build blobs from the 4 corners with spatial positions
    corner_positions = {
        'topLeft':     (0.1, 0.15),
        'topRight':    (0.9, 0.15),
        'bottomRight': (0.85, 0.85),
        'bottomLeft':  (0.15, 0.85),
    }
    blobs = []
    rgb_values = []
    for key, (bx, by) in corner_positions.items():
        hex_color = corner_hexes.get(key)
        if hex_color:
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            rgb_values.append((r, g, b))
            blobs.append({
                'color': hex_color,
                'x': bx,
                'y': by,
                'weight': 0.11,
            })

    # Synthesize 5th center blob by averaging the corner colors
    if rgb_values:
        avg_r = sum(c[0] for c in rgb_values) // len(rgb_values)
        avg_g = sum(c[1] for c in rgb_values) // len(rgb_values)
        avg_b = sum(c[2] for c in rgb_values) // len(rgb_values)
        blobs.append({
            'color': '#{:02x}{:02x}{:02x}'.format(avg_r, avg_g, avg_b),
            'x': 0.5,
            'y': 0.55,
            'weight': 0.11,
        })

    colors = {
        'format': 'palette',
        'blobs': blobs,
        **corner_hexes,
    }

    _ultrablur_cache[rating_key] = {'colors': colors, 'ts': time.time()}
    return colors


def _resolve_rating_key_by_guid(guid_type: str, guid_id: str):
    """Resolve a tmdb/imdb GUID to a Plex ratingKey by searching library sections."""
    import time
    cache_key = f'{guid_type}:{guid_id}'
    cached = _ultrablur_guid_cache.get(cache_key)
    if cached and (time.time() - cached['ts']) < _ULTRABLUR_TTL:
        return cached['rk']

    # Build the guid prefix Plex expects
    if guid_type == 'tmdb':
        plex_guid = f'tmdb://{guid_id}'
    elif guid_type == 'imdb':
        plex_guid = f'imdb://{guid_id}'
    else:
        return None

    # Search all movie library sections
    sections = plex_service.get_library_sections()
    for section in sections:
        if section.get('type') != 'movie':
            continue
        root = plex_service._get(
            f"/library/sections/{section['id']}/all",
            params={'type': 1, 'guid': plex_guid, 'includeGuids': 1},
        )
        if root is not None:
            video = root.find('.//Video')
            if video is not None:
                rk = int(video.get('ratingKey', 0))
                if rk:
                    _ultrablur_guid_cache[cache_key] = {'rk': rk, 'ts': time.time()}
                    return rk

    # Search all TV show library sections
    for section in sections:
        if section.get('type') != 'show':
            continue
        root = plex_service._get(
            f"/library/sections/{section['id']}/all",
            params={'type': 2, 'guid': plex_guid, 'includeGuids': 1},
        )
        if root is not None:
            directory = root.find('.//Directory')
            if directory is not None:
                rk = int(directory.get('ratingKey', 0))
                if rk:
                    _ultrablur_guid_cache[cache_key] = {'rk': rk, 'ts': time.time()}
                    return rk

    _ultrablur_guid_cache[cache_key] = {'rk': None, 'ts': time.time()}
    return None


@app.route('/api/plex/ultrablur/<int:rating_key>')
def get_plex_ultrablur(rating_key):
    """Get UltraBlurColors from Plex API for gradient backgrounds.

    Returns palette format with blobs array plus legacy corner fields.
    """
    try:
        colors = _fetch_ultrablur_colors(rating_key)
        return jsonify(colors)
    except Exception as e:
        logger.error(f"Error fetching UltraBlurColors: {e}")
        return jsonify(_make_null_palette_result())


@app.route('/api/plex/ultrablur/resolve')
def resolve_plex_ultrablur():
    """Resolve tmdb_id or imdb_id to ratingKey, then return UltraBlurColors.

    Returns palette format with blobs array plus legacy corner fields.
    """
    try:
        tmdb_id = request.args.get('tmdb_id')
        imdb_id = request.args.get('imdb_id')

        rating_key = None
        if tmdb_id:
            rating_key = _resolve_rating_key_by_guid('tmdb', tmdb_id)
        elif imdb_id:
            rating_key = _resolve_rating_key_by_guid('imdb', imdb_id)

        if not rating_key:
            return jsonify(_make_null_palette_result())

        colors = _fetch_ultrablur_colors(rating_key)
        return jsonify(colors)
    except Exception as e:
        logger.error(f"Error resolving UltraBlurColors: {e}")
        return jsonify(_make_null_palette_result())

_tmdb_colors_cache: dict = {}   # tmdb_id → {colors, ts}
_TMDB_COLORS_TTL = 24 * 3600   # 24 hours


def _extract_colors_from_image(image_bytes: bytes) -> dict:
    """Extract dominant palette colors from image bytes using Pillow.

    Uses median-cut quantization with hue-diversity selection: quantize to
    many colors, group by hue family, then pick the most dominant color from
    each distinct hue group.  This ensures vivid greens, blues, and warm
    tones all get representation instead of 5 shades of the same hue.

    Returns dict with:
      - format: "palette"
      - blobs: list of {color, x, y, weight} dicts (up to 5)
      - topLeft/topRight/bottomRight/bottomLeft: legacy hex strings mapped
        from the blob closest to each corner
    """
    import colorsys
    from collections import Counter
    from PIL import Image
    null_result = _make_null_palette_result()
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        quant_img = img.resize((120, 80), Image.LANCZOS)

        # --- Step 1: quantize to 24 colors for fine-grained hue coverage ---
        quantized = quant_img.quantize(colors=24, method=Image.Quantize.MEDIANCUT)
        palette_data = quantized.getpalette()
        if not palette_data:
            return null_result

        quant_pixels = list(quantized.getdata())
        counts = Counter(quant_pixels)
        qw, qh = 120, 80
        total_pixels = qw * qh

        # Build color records with HSV info
        colors = []
        for idx, cnt in counts.most_common(24):
            r = palette_data[idx * 3]
            g = palette_data[idx * 3 + 1]
            b = palette_data[idx * 3 + 2]
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            colors.append({
                'rgb': (r, g, b),
                'count': cnt,
                'idx': idx,
                'hue': h,        # 0..1
                'sat': s,        # 0..1
                'val': v,        # 0..1
                'pct': cnt / total_pixels,
            })

        # --- Step 2: group by hue family ---
        # 6 hue buckets: red, yellow, green, cyan, blue, magenta
        # Plus a neutral bucket for low-saturation colors (grays/browns)
        def hue_bucket(c):
            if c['sat'] < 0.15 or c['val'] < 0.10:
                return 'neutral'
            h = c['hue']
            if h < 0.05 or h >= 0.92:
                return 'red'
            elif h < 0.12:
                return 'orange'
            elif h < 0.19:
                return 'yellow'
            elif h < 0.44:
                return 'green'
            elif h < 0.56:
                return 'cyan'
            elif h < 0.73:
                return 'blue'
            elif h < 0.84:
                return 'purple'
            else:
                return 'magenta'

        buckets: dict = {}
        for c in colors:
            b = hue_bucket(c)
            if b not in buckets:
                buckets[b] = []
            buckets[b].append(c)

        # Within each bucket, pick the best representative:
        # score by pixel count boosted by saturation and brightness
        for bucket_colors in buckets.values():
            for c in bucket_colors:
                c['score'] = c['count'] * (1.0 + c['sat'] * 1.5) * (0.5 + c['val'] * 0.5)

        # --- Step 3: pick 5 colors with hue diversity ---
        # Sort buckets by total pixel coverage (biggest families first)
        bucket_list = []
        for name, bucket_colors in buckets.items():
            total = sum(c['count'] for c in bucket_colors)
            # Best candidate from this bucket (highest score)
            best = max(bucket_colors, key=lambda c: c['score'])
            bucket_list.append({
                'name': name,
                'total': total,
                'best': best,
                'colors': sorted(bucket_colors, key=lambda c: c['score'], reverse=True),
            })
        bucket_list.sort(key=lambda b: b['total'], reverse=True)

        # Pick one color per hue bucket (up to 5), largest buckets first
        picked = []
        for bucket in bucket_list:
            if len(picked) >= 5:
                break
            picked.append(bucket['best'])

        # If < 5, fill with second-best from largest buckets
        if len(picked) < 5:
            for bucket in bucket_list:
                if len(picked) >= 5:
                    break
                for c in bucket['colors']:
                    if c not in picked:
                        # Ensure it's visually distinct from already-picked
                        too_close = False
                        for existing in picked:
                            dist = sum((a - b) ** 2 for a, b in zip(c['rgb'], existing['rgb'])) ** 0.5
                            if dist < 40:
                                too_close = True
                                break
                        if not too_close:
                            picked.append(c)
                            break

        # --- Step 4: spatial position — find where each color lives ---
        weight = round(1.0 / max(len(picked), 1), 2)
        blobs = []
        for p in picked:
            cx_sum, cy_sum, match_count = 0.0, 0.0, 0
            for py_i in range(qh):
                for px_i in range(qw):
                    if quant_pixels[py_i * qw + px_i] == p['idx']:
                        cx_sum += px_i
                        cy_sum += py_i
                        match_count += 1
            if match_count > 0:
                bx = round((cx_sum / match_count) / qw, 2)
                by = round((cy_sum / match_count) / qh, 2)
            else:
                bx, by = 0.5, 0.5

            blobs.append({
                'color': '#{:02x}{:02x}{:02x}'.format(*p['rgb']),
                'x': bx,
                'y': by,
                'weight': weight,
            })

        legacy = _map_blobs_to_corners(blobs)
        return {
            'format': 'palette',
            'blobs': blobs,
            **legacy,
        }
    except Exception as e:
        logger.error(f"Color extraction error: {e}")
        return null_result


def _map_blobs_to_corners(blobs: list) -> dict:
    """Map blobs to legacy corner fields by finding closest blob to each corner."""
    corner_positions = {
        'topLeft': (0.0, 0.0),
        'topRight': (1.0, 0.0),
        'bottomRight': (1.0, 1.0),
        'bottomLeft': (0.0, 1.0),
    }
    result = {}
    for corner, (cx, cy) in corner_positions.items():
        best = None
        best_dist = float('inf')
        for blob in blobs:
            d = ((blob['x'] - cx) ** 2 + (blob['y'] - cy) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best = blob
        result[corner] = best['color'] if best else None
    return result


def _make_null_palette_result() -> dict:
    """Return a null result in the new palette format."""
    return {
        'format': 'palette',
        'blobs': [],
        'topLeft': None,
        'topRight': None,
        'bottomRight': None,
        'bottomLeft': None,
    }


@app.route('/api/tmdb/colors/<tmdb_id>')
def get_tmdb_colors(tmdb_id):
    """Extract dominant palette colors from a TMDb backdrop image.

    Returns palette format with blobs array plus legacy corner fields:
    {format, blobs: [{color, x, y, weight}], topLeft, topRight, bottomRight, bottomLeft}
    """
    null_result = _make_null_palette_result()
    try:
        media_type = request.args.get('type', 'movie')
        cache_key = f"{media_type}:{tmdb_id}"

        # Check cache — invalidate legacy entries missing 'format' key
        cached = _tmdb_colors_cache.get(cache_key)
        if cached and (time.time() - cached['ts']) < _TMDB_COLORS_TTL:
            if cached['colors'].get('format') == 'palette':
                return jsonify(cached['colors'])
            # Legacy cached entry — fall through to re-extract

        tmdb_api_key = settings.get('apis.tmdb.api_key', '')
        if not tmdb_api_key:
            return jsonify(null_result)

        # Fetch movie/TV details for backdrop_path
        url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}"
        resp = requests.get(url, params={'api_key': tmdb_api_key}, timeout=10)
        if resp.status_code != 200:
            return jsonify(null_result)

        data = resp.json()
        image_path = data.get('backdrop_path') or data.get('poster_path')
        if not image_path:
            _tmdb_colors_cache[cache_key] = {'colors': null_result, 'ts': time.time()}
            return jsonify(null_result)

        # Fetch the image (w300 is plenty for color sampling — much faster to download)
        img_resp = requests.get(
            f"https://image.tmdb.org/t/p/w300{image_path}",
            timeout=10,
        )
        if img_resp.status_code != 200:
            return jsonify(null_result)

        colors = _extract_colors_from_image(img_resp.content)
        _tmdb_colors_cache[cache_key] = {'colors': colors, 'ts': time.time()}
        return jsonify(colors)

    except Exception as e:
        logger.error(f"Error extracting TMDb colors: {e}")
        return jsonify(null_result)


@app.route('/api/clearlogo/<tmdb_id>')
def get_clearlogo(tmdb_id):
    """Fetch clearlogo from TMDB images API (uses shared 24h cache)."""
    try:
        media_type = request.args.get('type', 'movie')
        tmdb_id_int = int(tmdb_id)
        logo_path = _fetch_one_logo(tmdb_id_int, media_type)
        if logo_path:
            clearlogo_url = f"https://image.tmdb.org/t/p/original{logo_path}"
            return jsonify({'clearlogo_url': clearlogo_url})
        return jsonify({'clearlogo_url': None})  # no logo isn't an error (no console 404)
    except Exception as e:
        logger.error(f"Error fetching clearlogo from TMDB: {e}")
        return jsonify({'clearlogo_url': None}), 500


# ---------------------------------------------------------------------------
# Batch clearlogo endpoint — for Explore tiles
# ---------------------------------------------------------------------------

_logo_cache: Dict[str, Optional[str]] = {}
_logo_cache_ts: Dict[str, float] = {}
_LOGO_CACHE_TTL = 86400  # 24 hours
_LOGO_CACHE_MAX = 500


def _fetch_one_logo(tmdb_id: int, media_type: str) -> Optional[str]:
    """Fetch best logo file_path for a single TMDb ID (with 24h cache)."""
    import time as _time
    cache_key = f"{media_type}:{tmdb_id}"
    now = _time.time()
    if cache_key in _logo_cache and (now - _logo_cache_ts.get(cache_key, 0)) < _LOGO_CACHE_TTL:
        return _logo_cache[cache_key]

    api_key = settings.get('apis.tmdb.api_key') or Config.TMDB_API_KEY
    if not api_key:
        return None
    try:
        resp = requests.get(
            f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/images",
            params={'api_key': api_key, 'include_image_language': 'en,null'},
            timeout=8,
        )
        resp.raise_for_status()
        logos = resp.json().get('logos', [])

        # Prefer English PNG, then English any, then any PNG, then any
        en = [l for l in logos if l.get('iso_639_1') == 'en']
        pool = en if en else logos
        png = [l for l in pool if (l.get('file_path') or '').lower().endswith('.png')]
        png.sort(key=lambda x: x.get('vote_average', 0), reverse=True)
        others = [l for l in pool if not (l.get('file_path') or '').lower().endswith('.png')]
        others.sort(key=lambda x: x.get('vote_average', 0), reverse=True)
        best = (png + others)[0] if (png or others) else None
        path = best['file_path'] if best else None
    except Exception:
        path = None

    # Evict expired entries; enforce max size
    if len(_logo_cache) >= _LOGO_CACHE_MAX:
        expired = [k for k, ts in list(_logo_cache_ts.items()) if now - ts >= _LOGO_CACHE_TTL]
        for k in expired:
            _logo_cache.pop(k, None)
            _logo_cache_ts.pop(k, None)
        if len(_logo_cache) >= _LOGO_CACHE_MAX:
            oldest = sorted(_logo_cache_ts, key=_logo_cache_ts.get)
            for k in oldest[:len(_logo_cache) - _LOGO_CACHE_MAX + 1]:
                _logo_cache.pop(k, None)
                _logo_cache_ts.pop(k, None)
    _logo_cache[cache_key] = path
    _logo_cache_ts[cache_key] = now
    return path


@app.route('/api/tmdb/logos', methods=['POST'])
def get_logos_batch():
    """Batch-fetch clearlogo paths for multiple TMDb IDs.
    Body: { "items": [ { "id": 123, "type": "movie" }, ... ] }
    Returns: { "logos": { "123": "/path.png", "456": null, ... } }
    """
    from concurrent.futures import ThreadPoolExecutor
    try:
        body = request.get_json(force=True) or {}
        items = body.get('items', [])
        if not items or len(items) > 40:
            return jsonify({'logos': {}})

        def _fetch(item):
            tid = int(item.get('id', 0))
            mtype = item.get('type', 'movie')
            return str(tid), _fetch_one_logo(tid, mtype)

        result = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            for key, path in pool.map(_fetch, items):
                result[key] = path
        return jsonify({'logos': result})
    except Exception as e:
        logger.error(f"Batch logo fetch error: {e}")
        return jsonify({'logos': {}}), 500


# ---------------------------------------------------------------------------
# TMDb Discover — paginated discover proxy for Vibe Engine "Something New"
# ---------------------------------------------------------------------------
_tmdb_discover_cache: Dict[str, tuple] = {}   # cache_key → (timestamp, data)
_TMDB_DISCOVER_CACHE_TTL = 15 * 60            # 15 minutes

@app.route('/api/tmdb/discover')
def tmdb_discover():
    """Proxy TMDb /discover/movie and /discover/tv with pagination + caching.

    Query params:
        type          – "movie", "tv", or "both" (default "movie")
        pages         – number of pages to fetch (capped at 20)
        with_genres   – comma-separated genre IDs
        primary_release_date.gte / .lte – movie date filters
        first_air_date.gte / .lte       – TV date filters

    Returns: { "results": [ { ...tmdb item, media_type: "movie"|"tv" }, ... ] }
    """
    tmdb_api_key = settings.get('apis.tmdb.api_key', '') or Config.TMDB_API_KEY
    if not tmdb_api_key:
        return jsonify({'results': [], 'error': 'TMDb API key not configured'}), 500

    # Parse query params
    media_type = request.args.get('type', 'movie')
    if media_type not in ('movie', 'tv', 'both'):
        media_type = 'movie'

    raw_pages = request.args.get('pages', '5')
    try:
        total_pages = min(int(raw_pages), 500)  # TMDb has up to 500 pages
    except (ValueError, TypeError):
        total_pages = 5
    total_pages = max(total_pages, 1)

    with_genres = request.args.get('with_genres', '')
    release_gte = request.args.get('primary_release_date.gte', '')
    release_lte = request.args.get('primary_release_date.lte', '')
    air_gte = request.args.get('first_air_date.gte', '')
    air_lte = request.args.get('first_air_date.lte', '')

    # Build cache key from all relevant params
    cache_key = f"tmdb_discover:{media_type}:{total_pages}:{with_genres}:{release_gte}:{release_lte}:{air_gte}:{air_lte}"

    # Check cache
    cached = _tmdb_discover_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _TMDB_DISCOVER_CACHE_TTL:
            return jsonify(data)
        del _tmdb_discover_cache[cache_key]

    base_url = "https://api.themoviedb.org/3"

    def _fetch_page(endpoint, page_num, extra_params):
        """Fetch a single discover page from TMDb. Returns list of results."""
        params = {
            'api_key': tmdb_api_key,
            'sort_by': 'vote_count.desc',
            'page': page_num,
        }
        if with_genres:
            params['with_genres'] = with_genres
        params.update(extra_params)
        try:
            resp = requests.get(
                f"{base_url}{endpoint}",
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get('results', [])
            logger.warning(f"TMDb discover {endpoint} page {page_num}: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"TMDb discover {endpoint} page {page_num} failed: {e}")
        return []

    all_results = []

    try:
        if media_type == 'both':
            # Split pages evenly between movie and TV
            movie_pages = total_pages // 2
            tv_pages = total_pages - movie_pages
            # Ensure at least 1 page each
            movie_pages = max(movie_pages, 1)
            tv_pages = max(tv_pages, 1)
        else:
            movie_pages = total_pages if media_type == 'movie' else 0
            tv_pages = total_pages if media_type == 'tv' else 0

        # Build work items for thread pool
        work_items = []

        movie_extra = {}
        if release_gte:
            movie_extra['primary_release_date.gte'] = release_gte
        if release_lte:
            movie_extra['primary_release_date.lte'] = release_lte

        tv_extra = {}
        if air_gte:
            tv_extra['first_air_date.gte'] = air_gte
        if air_lte:
            tv_extra['first_air_date.lte'] = air_lte

        for p in range(1, movie_pages + 1):
            work_items.append(('/discover/movie', p, movie_extra, 'movie'))
        for p in range(1, tv_pages + 1):
            work_items.append(('/discover/tv', p, tv_extra, 'tv'))

        # Fetch pages in parallel
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(_fetch_page, endpoint, page_num, extra): mtype
                for endpoint, page_num, extra, mtype in work_items
            }
            for future in as_completed(futures):
                mtype = futures[future]
                try:
                    page_results = future.result()
                    for item in page_results:
                        item['media_type'] = mtype
                    all_results.extend(page_results)
                except Exception as e:
                    logger.warning(f"TMDb discover future error: {e}")

        payload = {'results': all_results}
        _tmdb_discover_cache[cache_key] = (time.time(), payload)
        return jsonify(payload)

    except Exception as e:
        logger.error(f"TMDb discover error: {e}")
        return jsonify({'results': []}), 500


# Hero Content — shared logic
_hero_result_cache: dict = {}   # keyed by plex_url → {'movies': [...], 'boost': bool, 'ts': float}
_HERO_RESULT_TTL = 120          # 2-minute cache — Plex data doesn't change rapidly

def _build_hero_movies():
    """Core hero content logic — shared by /api/hero/content and screensaver.

    Returns (movies_list, boost_mode_bool).
    Raises on error (caller should catch).
    """
    # Read hero settings
    plex = get_plex_for_request()

    # Cache keyed by Plex server URL so different users' servers don't collide
    cache_key = getattr(plex, 'plex_url', '') or 'global'
    cached = _hero_result_cache.get(cache_key)
    if cached and (time.time() - cached['ts']) < _HERO_RESULT_TTL:
        return cached['movies'], cached['boost']
    section_id = settings.get('ui.hero.library_section_id', 0)
    limit = settings.get('ui.hero.limit', 10)
    boost = settings.get('ui.hero.boost_popular_titles', False)
    pop_source = settings.get('ui.hero.popularity_source', 'tmdb')

    # Minimal mode, nothing picked: the movie library with the most items
    # (Plex's "default" movie section can be a tiny one, e.g. '3D').
    if not section_id and _minimal.enabled():
        section_id = _largest_movie_section_id(plex)

    # If no section configured, use default movie section
    if not section_id:
        health = plex.health_check()
        section_id = health.get('default_movie_section_id')
        if not section_id:
            return [], False

    # Parse excluded genres (comma/newline separated, case-insensitive)
    raw_excluded = settings.get('ui.hero.excluded_genres', [])
    if isinstance(raw_excluded, str):
        raw_excluded = [g for g in raw_excluded.replace('\n', ',').split(',')]
    excluded_genres = {g.strip().lower() for g in raw_excluded if g.strip()} if raw_excluded else set()
    if excluded_genres:
        logger.debug(f"Hero excluded genres: {excluded_genres}")

    # Max-age date filter (always applied)
    max_age_days = settings.get('ui.hero.max_age_days', 30)
    minimal_kiosk = _minimal.enabled()
    if minimal_kiosk:
        # Web kiosk (#12): the selected library's newest items, however old,
        # so a library with items never shows an empty screen.
        max_age_days = 0

    # Fetch from Plex — boost mode needs a larger pool
    if boost:
        fetch_limit = max(limit * 5, 100)
    else:
        fetch_limit = limit * 3 if (excluded_genres or max_age_days) else limit

    if minimal_kiosk:
        # Web kiosk (#12): the library's newest movies by date added. Plex's
        # /recentlyAdded applies its own recency window (it returned 2 items on
        # :5002, so the slideshow had almost nothing to rotate through). Only
        # items with backdrop art can be shown.
        fetch_limit = max(limit * 3, 30)
        try:
            items = (plex.get_all_movies(int(section_id), sort='addedAt:desc', limit=fetch_limit) or {}).get('movies') or []
        except Exception as e:
            logger.warning(f"Hero: newest movies lookup failed: {e}")
            items = []
        items = [it for it in items if it.get('artPath')]
    else:
        items = plex.get_recently_added(section_id=section_id, limit=fetch_limit)
    logger.debug(f"Hero fetched {len(items)} items from Plex (boost={boost})")

    # Date filter: only keep movies added within max_age_days
    if max_age_days and max_age_days > 0:
        cutoff = time.time() - (max_age_days * 24 * 3600)
        before_count = len(items)
        items = [it for it in items if (it.get('added_at') or it.get('addedAt') or 0) >= cutoff]
        logger.info(f"Hero {max_age_days}-day filter: {before_count} -> {len(items)}")

    # Filter out excluded genres (applies in both modes)
    if excluded_genres:
        filtered = []
        for item in items:
            item_genres = {g.lower() for g in item.get('genres', [])}
            matched = item_genres & excluded_genres
            if matched:
                logger.debug(f"Hero excluded: '{item['title']}' matched genre(s): {matched}")
                continue
            filtered.append(item)
        logger.info(f"Hero genre filter: {len(items)} -> {len(filtered)} after excluding {excluded_genres}")
        items = filtered

    # Boost mode: enrich + sort by popularity
    if boost and items:
        if pop_source == 'imdb':
            enrich_hero_imdb(items)
        else:
            enrich_hero_tmdb(items)

        items.sort(key=lambda it: (
            -(it.get('_pop_votes') or 0),
            -(it.get('_pop_rating') or 0),
            -(it.get('added_at') or it.get('addedAt') or 0),
            (it.get('title') or '').lower(),
        ))

        logger.info(f"Hero boost final pool ({pop_source}, {len(items)} candidates):")
        for i, it in enumerate(items[:limit]):
            logger.info(
                f"  #{i+1} {it['title']} ({it.get('year')}) — "
                f"votes={it.get('_pop_votes', 0)}, rating={it.get('_pop_rating', 0)}, "
                f"addedAt={it.get('added_at') or it.get('addedAt', 0)}"
            )

    # Trim to requested limit
    items = items[:limit]

    # Fetch TMDb backdrop paths for movies that have tmdb_ids (parallel)
    tmdb_api_key = settings.get('apis.tmdb.api_key', '')
    tmdb_backdrops = {}
    if tmdb_api_key:
        needs_fetch = []
        for item in items:
            tid = item.get('tmdb_id')
            if tid:
                pop_cached = _get_cached_popularity(f"tmdb:{tid}")
                if pop_cached and pop_cached.get('backdrop_path'):
                    tmdb_backdrops[tid] = pop_cached['backdrop_path']
                else:
                    needs_fetch.append(tid)

        def _fetch_tmdb_backdrop(tid):
            try:
                resp = requests.get(
                    f"https://api.themoviedb.org/3/movie/{tid}",
                    params={'api_key': tmdb_api_key},
                    timeout=5,
                )
                if resp.status_code == 200:
                    bp = resp.json().get('backdrop_path')
                    if bp:
                        return tid, bp
            except Exception:
                pass
            return tid, None

        if needs_fetch:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=5) as pool:
                for tid, bp in pool.map(_fetch_tmdb_backdrop, needs_fetch):
                    if bp:
                        tmdb_backdrops[tid] = bp

    # Build response with proxy image URLs
    movies = []
    for item in items:
        if item.get('type', 'movie') != 'movie' and not minimal_kiosk:
            continue
        movie = {
            'rating_key': item['rating_key'],
            'title': item['title'],
            'year': item.get('year'),
            'tmdb_id': item.get('tmdb_id'),
            'imdb_id': item.get('imdb_id'),
            'summary': item.get('summary', ''),
            'content_rating': item.get('content_rating', ''),
            'duration': item.get('duration', 0),
            'rating': item.get('rating'),
            'audience_rating': item.get('audienceRating'),
            'genres': item.get('genres', []),
            'added_at': item.get('added_at', 0),
            'poster_url': f"/api/img/plex?url={item['thumbPath']}" if item.get('thumbPath') else None,
            'backdrop_url': (f"/api/img/plex?url={item['artPath']}" + ('&w=1920' if minimal_kiosk else ''))
                            if item.get('artPath') else None,
            'clearlogo_url': f"/api/img/plex?url={item['clearLogoPath']}" if item.get('clearLogoPath') else None,
            'tmdb_backdrop_url': f"/api/img/tmdb?path={tmdb_backdrops[item.get('tmdb_id')]}&size=original" if item.get('tmdb_id') and item.get('tmdb_id') in tmdb_backdrops else None,
        }
        movies.append(movie)

    # Cache the result for subsequent requests (keyed by plex_url)
    _hero_result_cache[cache_key] = {'movies': movies, 'boost': boost, 'ts': time.time()}

    return movies, boost


_HERO_URL_FIELDS = ('poster_url', 'backdrop_url', 'clearlogo_url', 'tmdb_backdrop_url')


def _hero_movies_for_request(movies):
    """Copy hero movies with root-relative image URLs prefixed by request.script_root.

    _build_hero_movies() caches root-relative paths so the cache is request
    independent; the prefix is added per response so sub-path installs work.
    """
    root = (request.script_root or '').rstrip('/')
    out = []
    for m in movies:
        m = dict(m)
        for key in _HERO_URL_FIELDS:
            val = m.get(key)
            if isinstance(val, str) and val.startswith('/') and not val.startswith('//'):
                m[key] = f"{root}{val}"
        out.append(m)
    return out


_largest_section_cache: Dict[str, Any] = {}
_LARGEST_SECTION_TTL = 600


def _largest_movie_section_id(plex) -> int:
    """The id of the movie library with the most items (0 if none answers).
    Cached for 10 minutes per Plex server."""
    key = getattr(plex, 'plex_url', '') or 'global'
    hit = _largest_section_cache.get(key)
    if hit and time.time() - hit['ts'] < _LARGEST_SECTION_TTL:
        return hit['id']
    from app import home_rails as _hr
    best, best_n = 0, -1
    try:
        movie_secs = [sec for sec in (_hr._sections(plex) or []) if sec.get('type') == 'movie']
    except Exception as e:
        logger.warning(f"Hero: library list failed: {e}")
        movie_secs = []
    for sec in movie_secs:
        try:
            n = int((plex.get_all_movies(int(sec['id']), limit=1) or {}).get('total') or 0)
        except Exception:
            continue
        if n > best_n:
            best, best_n = int(sec['id']), n
    if best:
        _largest_section_cache[key] = {'id': best, 'ts': time.time()}
    return best


# Web kiosk library (Settings > Kiosk): which movie library the slideshow
# uses. Stored in prod's own setting (ui.hero.library_section_id).
@app.route('/api/home/kiosk', methods=['GET', 'PUT'])
def home_kiosk_library():
    err = _require_auth()
    if err:
        return err
    user = _get_current_user()
    if not user or not user.get('is_admin'):
        return jsonify({'error': 'Admin access required'}), 403
    from app import home_rails as _hr
    plex = get_plex_for_request()
    libs = []
    if plex is not None and getattr(plex, 'is_configured', False):
        libs = [{'id': str(s['id']), 'title': s.get('title', '')}
                for s in (_hr._sections(plex) or []) if s.get('type') == 'movie']
    if request.method == 'PUT':
        body = request.get_json(silent=True) or {}
        sid = str(body.get('id') or '').strip()[:32]
        if not sid or sid not in {l['id'] for l in libs}:
            return jsonify({'error': 'Choose a movie library'}), 400
        settings.update({'ui': {'hero': {'library_section_id': int(sid)}}})
        _hero_result_cache.clear()
    current = settings.get('ui.hero.library_section_id', 0) or 0
    if not current and libs and _minimal.enabled():
        current = _largest_movie_section_id(plex)
    if not current and libs:
        try:
            current = (plex.health_check() or {}).get('default_movie_section_id') or 0
        except Exception:
            current = 0
    return jsonify({'id': str(current) if current else '', 'libraries': libs})


# Hero Content Endpoints
@app.route('/api/hero/content')
def get_hero_content():
    """Get hero content for the kiosk home page.
    
    Returns dynamic content from _build_hero_movies().
    Background refresh runs only from the scheduler, never from a request.
    """
    try:
        # Return dynamic content (no refresh or healing from request)
        movies, boost = _build_hero_movies()
        return jsonify({
            'success': True,
            'movies': _hero_movies_for_request(movies),
            'boost_mode': boost
        })
    except Exception as e:
        logger.error(f"Error building hero content: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Kiosk Settings
# =============================================================================

@app.route('/api/kiosk/settings', methods=['GET'])
def get_kiosk_settings():
    """Get kiosk-specific settings (hero + screensaver)."""
    err = _require_auth()
    if err:
        return err
    try:
        return jsonify({
            'kiosk_mode': settings.get('ui.kiosk_mode', 'movie'),
            'hero_library_section_id': settings.get('ui.hero.library_section_id', 0),
            'hero_limit': settings.get('ui.hero.limit', 10),
            'backdrop_source': settings.get('ui.hero.backdrop_source', 'plex'),
            'boost_popular_titles': settings.get('ui.hero.boost_popular_titles', False),
            'popularity_source': settings.get('ui.hero.popularity_source', 'tmdb'),
            'screensaver_enabled': settings.get('ui.screensaver.enabled', True),
            'screensaver_type': settings.get('ui.screensaver.type', 'orb'),
            'screensaver_idle_timeout_minutes': settings.get('ui.screensaver.idle_timeout_minutes', 2),
            'screensaver_orb_count': settings.get('ui.screensaver.orb_count', 9),
            'screensaver_tablet_orb_count': settings.get('ui.screensaver.tablet_orb_count', 4),
            'screensaver_source': settings.get('ui.screensaver.source', 'plex_library'),
            'vibe_newer_than_years': settings.get('ui.vibe_engine.newer_than_years', 1),
            'vibe_excluded_libraries': settings.get('ui.vibe_engine.excluded_libraries', []),
            'vibe_genres': settings.get('ui.vibe_engine.genres', ['Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Drama', 'Fantasy', 'Horror', 'Romance', 'Sci-Fi', 'Thriller']),
            'vibe_tonight_max_runtime': settings.get('ui.vibe_engine.tonight_max_runtime', 100),
            'vibe_premium_rating_threshold': settings.get('ui.vibe_engine.premium_rating_threshold', 7.0),
            'vibe_unwatched_boost_weight': settings.get('ui.vibe_engine.unwatched_boost_weight', 3),
            'ai_search_available': bool(settings.get('apis.openai.enabled', False)
                                        and settings.get('apis.openai.api_key', '')),
            'voice_enabled': bool(
                (settings.get('apis.openai.enabled', False) and settings.get('apis.openai.voice_enabled', False))
                or settings.get('apis.alfred.enabled', False)
            ),
            'wake_word': settings.get('apis.openai.wake_word', 'alfred'),
            'home_area_id': settings.get('ui.kiosk.home_area_id', ''),
            'daily_kiosk_widget_seconds': settings.get('ui.daily_kiosk.widget_seconds', 8),
        })
    except Exception as e:
        logger.error(f"Error getting kiosk settings: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/kiosk/settings', methods=['POST'])
def update_kiosk_settings():
    """Update kiosk-specific settings."""
    err = _require_admin()
    if err:
        return err
    def _safe_int(val, default=0):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _safe_float(val, default=0.0):
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        update = {'ui': {'hero': {}, 'screensaver': {}, 'vibe_engine': {}, 'kiosk': {}, 'daily_kiosk': {}}}
        if 'home_area_id' in data:
            val = data['home_area_id']
            if isinstance(val, str):
                update['ui']['kiosk']['home_area_id'] = val.strip()
        if 'daily_kiosk_widget_seconds' in data and data['daily_kiosk_widget_seconds'] != '':
            secs = _safe_int(data['daily_kiosk_widget_seconds'], 8)
            update['ui']['daily_kiosk']['widget_seconds'] = max(3, min(60, secs))
        if 'kiosk_mode' in data:
            val = data['kiosk_mode']
            if val in ('movie', 'daily'):
                update['ui']['kiosk_mode'] = val
        if 'hero_library_section_id' in data:
            update['ui']['hero']['library_section_id'] = _safe_int(data['hero_library_section_id'])
        if 'hero_limit' in data:
            update['ui']['hero']['limit'] = _safe_int(data['hero_limit'])
        if 'backdrop_source' in data:
            val = data['backdrop_source']
            if val in ('plex', 'tmdb'):
                update['ui']['hero']['backdrop_source'] = val
        if 'boost_popular_titles' in data:
            update['ui']['hero']['boost_popular_titles'] = bool(data['boost_popular_titles'])
        if 'popularity_source' in data:
            val = data['popularity_source']
            if val in ('imdb', 'tmdb'):
                update['ui']['hero']['popularity_source'] = val
        if 'screensaver_source' in data:
            val = data['screensaver_source']
            if val in ('hero', 'plex_library'):
                update['ui']['screensaver']['source'] = val
        if 'vibe_newer_than_years' in data and data['vibe_newer_than_years'] != '':
            update['ui']['vibe_engine']['newer_than_years'] = _safe_int(data['vibe_newer_than_years'])
        if 'vibe_excluded_libraries' in data:
            val = data['vibe_excluded_libraries']
            if isinstance(val, list):
                update['ui']['vibe_engine']['excluded_libraries'] = val
        if 'vibe_genres' in data:
            val = data['vibe_genres']
            if isinstance(val, list):
                update['ui']['vibe_engine']['genres'] = val
        if 'vibe_tonight_max_runtime' in data and data['vibe_tonight_max_runtime'] != '':
            update['ui']['vibe_engine']['tonight_max_runtime'] = _safe_int(data['vibe_tonight_max_runtime'])
        if 'vibe_premium_rating_threshold' in data and data['vibe_premium_rating_threshold'] != '':
            update['ui']['vibe_engine']['premium_rating_threshold'] = _safe_float(data['vibe_premium_rating_threshold'])
        if 'vibe_unwatched_boost_weight' in data and data['vibe_unwatched_boost_weight'] != '':
            update['ui']['vibe_engine']['unwatched_boost_weight'] = _safe_int(data['vibe_unwatched_boost_weight'])

        # Clean empty sub-dicts to avoid unnecessary writes
        if not update['ui']['hero']:
            del update['ui']['hero']
        if not update['ui']['screensaver']:
            del update['ui']['screensaver']
        if not update['ui']['vibe_engine']:
            del update['ui']['vibe_engine']
        if not update['ui']['kiosk']:
            del update['ui']['kiosk']

        success, errors = settings.update(update)
        if not success:
            return jsonify({'error': 'Validation failed', 'details': errors}), 400

        # Invalidate vibe cache if vibe_engine settings changed
        if 'vibe_engine' in update.get('ui', {}):
            _vibe_cache.clear()
            logger.info('Vibe cache invalidated due to kiosk settings change')

        # Hero settings (section/limit/boost/source) are saved here too, but the
        # hero cache is keyed only by plex_url — so clear it on any hero change,
        # otherwise stale hero content serves for up to the TTL.
        if 'hero' in update.get('ui', {}):
            _hero_result_cache.clear()
            logger.info('Hero cache invalidated due to kiosk settings change')

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error updating kiosk settings: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Weather API
# =============================================================================

_weather_cache: dict = {}  # {"data": ..., "ts": float}
_WEATHER_CACHE_TTL = 900   # 15 minutes

@app.route('/api/weather')
def get_weather():
    """Fetch current weather + 5-day forecast from OpenWeatherMap."""
    err = _require_auth()
    if err:
        return err
    try:
        api_key = settings.get('ui.daily_kiosk.weather.api_key', '')
        zip_code = settings.get('ui.daily_kiosk.weather.zip_code', '')
        country = settings.get('ui.daily_kiosk.weather.country_code', 'US')
        units = settings.get('ui.daily_kiosk.weather.units', 'imperial')
        state_abbr = settings.get('ui.daily_kiosk.weather.state', '')

        if not api_key or not zip_code:
            return jsonify({'error': 'Weather not configured', 'configured': False}), 200

        # Check cache
        import time as _time
        now = _time.time()
        cache_key = f"{zip_code}:{country}:{units}"
        cached = _weather_cache.get(cache_key)
        if cached and (now - cached['ts']) < _WEATHER_CACHE_TTL:
            return jsonify(cached['data'])

        import requests as _req

        # Current weather
        current_resp = _req.get(
            'https://api.openweathermap.org/data/2.5/weather',
            params={'zip': f'{zip_code},{country}', 'appid': api_key, 'units': units},
            timeout=10,
        )
        current_resp.raise_for_status()
        current = current_resp.json()

        # 5-day / 3-hour forecast
        forecast_resp = _req.get(
            'https://api.openweathermap.org/data/2.5/forecast',
            params={'zip': f'{zip_code},{country}', 'appid': api_key, 'units': units},
            timeout=10,
        )
        forecast_resp.raise_for_status()
        forecast = forecast_resp.json()

        # Build daily summary from forecast (group by date, prefer midday icon, always day variant)
        daily_map = {}  # date_str -> dict
        for entry in forecast.get('list', []):
            dt_txt = entry.get('dt_txt', '')
            date_str = dt_txt.split(' ')[0] if dt_txt else ''
            hour_str = dt_txt.split(' ')[1][:2] if ' ' in dt_txt else '00'
            if not date_str:
                continue
            icon_raw = entry['weather'][0]['icon'] if entry.get('weather') else '01d'
            # Force day variant for daily forecast
            icon = icon_raw.replace('n', 'd')
            desc = entry['weather'][0]['description'] if entry.get('weather') else ''
            if date_str not in daily_map:
                daily_map[date_str] = {
                    'date': date_str,
                    'temp_min': entry['main']['temp_min'],
                    'temp_max': entry['main']['temp_max'],
                    'icon': icon,
                    'description': desc,
                    'icon_hour': int(hour_str),
                }
            else:
                d = daily_map[date_str]
                d['temp_min'] = min(d['temp_min'], entry['main']['temp_min'])
                d['temp_max'] = max(d['temp_max'], entry['main']['temp_max'])
                # Prefer icon closest to midday (12:00)
                cur_dist = abs(d['icon_hour'] - 12)
                new_dist = abs(int(hour_str) - 12)
                if new_dist < cur_dist:
                    d['icon'] = icon
                    d['description'] = desc
                    d['icon_hour'] = int(hour_str)
        daily = []
        for date_str in sorted(daily_map.keys()):
            d = daily_map[date_str]
            del d['icon_hour']
            daily.append(d)
        daily = daily[:5]

        # Build hourly forecast — next 5 three-hour intervals (regardless of day boundary)
        hourly = []
        for entry in forecast.get('list', [])[:5]:
            dt_txt = entry.get('dt_txt', '')
            hour_str = dt_txt.split(' ')[1][:5] if ' ' in dt_txt else ''
            hourly.append({
                'time': hour_str,
                'temp': round(entry['main']['temp']),
                'icon': entry['weather'][0]['icon'] if entry.get('weather') else '01d',
                'description': entry['weather'][0]['description'] if entry.get('weather') else '',
            })

        unit_label = 'F' if units == 'imperial' else ('C' if units == 'metric' else 'K')

        result = {
            'configured': True,
            'location': (current.get('name', zip_code) + (', ' + state_abbr if state_abbr else '')),
            'units': unit_label,
            'current': {
                'temp': round(current['main']['temp']),
                'feels_like': round(current['main']['feels_like']),
                'humidity': current['main']['humidity'],
                'description': current['weather'][0]['description'] if current.get('weather') else '',
                'icon': current['weather'][0]['icon'] if current.get('weather') else '01d',
                'wind_speed': round(current.get('wind', {}).get('speed', 0)),
            },
            'forecast': daily,
            'hourly': hourly,
        }

        _weather_cache[cache_key] = {'data': result, 'ts': now}
        return jsonify(result)
    except Exception as e:
        logger.error(f"Weather API error: {e}")
        return jsonify({'error': str(e), 'configured': True}), 500


# =============================================================================
# Sports API (MLB Stats)
# =============================================================================

_sports_cache: dict = {}   # {cache_key: {"data": ..., "ts": float}}
_SPORTS_CACHE_TTL_IDLE = 900   # 15 min when no live game
_SPORTS_CACHE_TTL_LIVE = 10    # 10 seconds during a live game (teamtracker uses 5s)

# ESPN scoreboard API — same source HA TeamTracker uses. Free, no auth.
# Map user-facing league codes to the (sport, league) path segments used
# by the ESPN site API.
_ESPN_LEAGUE_MAP: dict = {
    'MLB':   ('baseball', 'mlb'),
    'NFL':   ('football', 'nfl'),
    'NBA':   ('basketball', 'nba'),
    'WNBA':  ('basketball', 'wnba'),
    'NHL':   ('hockey', 'nhl'),
    'NCAAF': ('football', 'college-football'),
    'NCAAM': ('basketball', 'mens-college-basketball'),
    'NCAAW': ('basketball', 'womens-college-basketball'),
    'MLS':   ('soccer', 'usa.1'),
    'EPL':   ('soccer', 'eng.1'),
    'UCL':   ('soccer', 'uefa.champions'),
}

_ESPN_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 11_6) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/15.0 Safari/605.1.15'
)


def _sports_match_competitor(competitor: dict, needles: list) -> bool:
    """True if any of the provided needles matches this ESPN competitor.
    Needles may be team IDs, abbreviations, short display names, or full display names."""
    team = competitor.get('team') or {}
    tid = str(team.get('id', '')).lower()
    abbr = (team.get('abbreviation') or '').lower()
    short = (team.get('shortDisplayName') or '').lower()
    full = (team.get('displayName') or '').lower()
    for raw in needles:
        if not raw:
            continue
        n = str(raw).strip().lower()
        if not n:
            continue
        if n == tid or n == abbr or n == short or n == full:
            return True
    return False


def _sports_normalize_score(raw: Any) -> str:
    """ESPN is inconsistent about the `score` field: the SCOREBOARD endpoint
    returns a plain string, but the TEAM SCHEDULE endpoint returns a dict
    like {"value": 2.0, "displayValue": "2"} for completed games. The kiosk
    widget interpolates the value directly into JSX, so we MUST flatten it
    here — an object child blows up React with error #31.
    """
    if raw is None:
        return ''
    if isinstance(raw, dict):
        dv = raw.get('displayValue')
        if dv is not None:
            return str(dv)
        v = raw.get('value')
        if v is not None:
            # Many sports store integer scores as floats in `value`
            if isinstance(v, float) and v.is_integer():
                return str(int(v))
            return str(v)
        return ''
    return str(raw)


def _sports_extract_logo(team: dict) -> str:
    """ESPN is inconsistent about logos: the scoreboard endpoint returns a
    flat `logo` string, but the team-schedule endpoint returns a `logos`
    array of variants. Prefer the scoreboard-sized default, then default,
    then whatever the first entry is."""
    flat = team.get('logo')
    if flat:
        return flat
    logos = team.get('logos') or []
    if not logos:
        return ''
    # Prefer a simple, square default variant without 'dark' mode
    preferred_rels = [
        {'full', 'default'},
        {'full', 'scoreboard'},
    ]
    for wanted in preferred_rels:
        for entry in logos:
            rels = set(entry.get('rel') or [])
            if wanted.issubset(rels) and 'dark' not in rels:
                href = entry.get('href')
                if href:
                    return href
    # Fallback: first logo with a usable href
    for entry in logos:
        href = entry.get('href')
        if href:
            return href
    return ''


def _sports_build_team(comp: dict) -> dict:
    """Normalize one ESPN competitor into the shape the kiosk widget expects."""
    team = comp.get('team') or {}
    records = comp.get('records') or []
    record = None
    for r in records:
        if r.get('type') == 'total' or r.get('name') == 'overall':
            record = r.get('summary')
            break
    if record is None and records:
        record = records[0].get('summary')
    return {
        'id': str(team.get('id', '')),
        'name': team.get('shortDisplayName') or team.get('name') or team.get('abbreviation') or '',
        'longName': team.get('displayName') or '',
        'abbreviation': team.get('abbreviation') or '',
        'homeAway': comp.get('homeAway') or '',
        'score': _sports_normalize_score(comp.get('score')),
        'isWinner': bool(comp.get('winner')),
        'record': record or '',
        'logo': _sports_extract_logo(team),
        'primaryColor': team.get('color') or '',
        'altColor': team.get('alternateColor') or '',
    }


def _sports_build_game(event: dict, needles: list, sport: str) -> Optional[dict]:
    """Build a normalized game dict for ONE ESPN event, or None if it doesn't
    involve the configured team. Includes live situational data that mirrors
    HA TeamTracker's sensor attributes."""
    comps_list = event.get('competitions') or []
    if not comps_list:
        return None
    comp = comps_list[0]
    competitors = comp.get('competitors') or []
    if len(competitors) < 2:
        return None

    # Identify which competitor is "our" team
    if _sports_match_competitor(competitors[0], needles):
        our_idx, their_idx = 0, 1
    elif _sports_match_competitor(competitors[1], needles):
        our_idx, their_idx = 1, 0
    else:
        return None

    our_comp = competitors[our_idx]
    their_comp = competitors[their_idx]

    status = comp.get('status') or event.get('status') or {}
    status_type = status.get('type') or {}
    raw_state = (status_type.get('state') or '').lower()  # pre | in | post
    state_label = {'pre': 'Upcoming', 'in': 'Live', 'post': 'Final'}.get(raw_state, raw_state.title())

    situation = comp.get('situation') or {}
    last_play_obj = situation.get('lastPlay') or {}
    last_play_text = last_play_obj.get('text')
    probability = last_play_obj.get('probability') or {}

    # TV broadcasts
    broadcasts = comp.get('broadcasts') or []
    tv_names: list = []
    for b in broadcasts:
        tv_names.extend(b.get('names') or [])
    tv_network = '/'.join(tv_names) if tv_names else None

    # Venue + location
    venue_obj = comp.get('venue') or {}
    venue = venue_obj.get('fullName')
    addr = venue_obj.get('address') or {}
    loc_parts = [p for p in (addr.get('city'), addr.get('state'), addr.get('country')) if p]
    location = ', '.join(loc_parts) if loc_parts else None

    # ── Sport-specific situation (matches teamtracker sensor attributes) ──
    baseball = None
    football = None
    win_prob_team = None
    win_prob_opponent = None

    our_home = (our_comp.get('homeAway') or '').lower() == 'home'

    if sport == 'baseball':
        # Who's at bat: teamtracker derives from the inning text prefix.
        # "Top 5th" → away at bat, "Bot 5th" / "Mid 5th" → home at bat.
        clock_text = status_type.get('detail') or status_type.get('shortDetail') or ''
        prefix = clock_text[:3].lower()
        at_bat = None
        if prefix in ('bot', 'mid'):
            at_bat = 'home'
        elif prefix == 'top':
            at_bat = 'away'
        baseball = {
            'outs': situation.get('outs'),
            'balls': situation.get('balls'),
            'strikes': situation.get('strikes'),
            'onFirst': bool(situation.get('onFirst')),
            'onSecond': bool(situation.get('onSecond')),
            'onThird': bool(situation.get('onThird')),
            'atBat': at_bat,  # 'home' | 'away' | None
        }

    if sport == 'football':
        if our_home:
            our_to = situation.get('homeTimeouts')
            their_to = situation.get('awayTimeouts')
            win_prob_team = probability.get('homeWinPercentage')
            win_prob_opponent = probability.get('awayWinPercentage')
        else:
            our_to = situation.get('awayTimeouts')
            their_to = situation.get('homeTimeouts')
            win_prob_team = probability.get('awayWinPercentage')
            win_prob_opponent = probability.get('homeWinPercentage')
        football = {
            'downDistanceText': situation.get('downDistanceText'),
            'possessionTeamId': str(situation.get('possession') or ''),
            'teamTimeouts': our_to,
            'opponentTimeouts': their_to,
        }
    elif raw_state == 'in':
        # Basketball / hockey / etc. — also expose win probability when present
        if our_home:
            win_prob_team = probability.get('homeWinPercentage')
            win_prob_opponent = probability.get('awayWinPercentage')
        else:
            win_prob_team = probability.get('awayWinPercentage')
            win_prob_opponent = probability.get('homeWinPercentage')

    our_team = _sports_build_team(our_comp)
    their_team = _sports_build_team(their_comp)
    our_team['winProbability'] = win_prob_team
    their_team['winProbability'] = win_prob_opponent

    return {
        'label': state_label,
        'state': raw_state,
        'gameDate': comp.get('date') or event.get('date') or '',
        'detail': status_type.get('detail') or '',
        'shortDetail': status_type.get('shortDetail') or '',
        'clock': status.get('displayClock') or '',
        'period': status.get('period'),
        'venue': venue or '',
        'location': location or '',
        'tvNetwork': tv_network or '',
        'seriesSummary': (comp.get('series') or {}).get('summary') or '',
        'team': our_team,
        'opponent': their_team,
        'lastPlay': last_play_text or '',
        'baseball': baseball,
        'football': football,
    }


def _espn_fetch_scoreboard(sport: str, league: str, dates: Optional[str] = None) -> dict:
    """Fetch the league scoreboard. Omit `dates` for "today's games" — that's
    where the full competition.situation (balls/strikes/outs/lastPlay) lives.
    A wide date range still works but caps at 50 events per call."""
    import requests as _req
    url = f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard'
    params: Dict[str, Any] = {'limit': 50}
    if dates:
        params['dates'] = dates
    resp = _req.get(
        url,
        params=params,
        headers={'User-Agent': _ESPN_UA, 'Accept': 'application/json'},
        timeout=12,
    )
    resp.raise_for_status()
    return resp.json()


# Teams-list cache: maps "{sport}:{league}" to a list of {id, abbr, slug, display}
# records pulled once from ESPN and reused forever.
_espn_teams_cache: dict = {}


def _espn_fetch_teams(sport: str, league: str) -> list:
    """Return all teams for a league — cached in-memory forever.

    Used to resolve a user-configured team string (id / abbreviation / full
    name) to the ESPN abbreviation the schedule endpoint expects.
    """
    key = f'{sport}:{league}'
    if key in _espn_teams_cache:
        return _espn_teams_cache[key]
    import requests as _req
    url = f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams'
    try:
        resp = _req.get(
            url,
            headers={'User-Agent': _ESPN_UA, 'Accept': 'application/json'},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f'Failed to load ESPN teams list for {key}: {e}')
        return []
    result: list = []
    for sport_entry in (data.get('sports') or []):
        for league_entry in (sport_entry.get('leagues') or []):
            for t in (league_entry.get('teams') or []):
                team = t.get('team') or {}
                result.append({
                    'id': str(team.get('id', '')),
                    'abbr': team.get('abbreviation') or '',
                    'slug': team.get('slug') or '',
                    'short': team.get('shortDisplayName') or '',
                    'display': team.get('displayName') or '',
                    'location': team.get('location') or '',
                    'name': team.get('name') or '',
                })
    _espn_teams_cache[key] = result
    return result


def _espn_resolve_team(sport: str, league: str, needles: list) -> Optional[dict]:
    """Return the ESPN team record matching any of the given needles, or None.
    Needles may be id, abbreviation, slug, full display name, city, or team name."""
    teams = _espn_fetch_teams(sport, league)
    if not teams:
        return None
    for raw in needles:
        if not raw:
            continue
        n = str(raw).strip().lower()
        if not n:
            continue
        for t in teams:
            if (n == t['id']
                or n == t['abbr'].lower()
                or n == t['slug']
                or n == t['display'].lower()
                or n == t['short'].lower()
                or n == t['name'].lower()
                or (t['display'] and n in t['display'].lower())):
                return t
    return None


def _espn_fetch_team_schedule(sport: str, league: str, team_abbr: str) -> dict:
    """Fetch the full season schedule for one team. Returns events in chrono
    order; includes past/live/future games but live events do NOT include
    the full competition.situation — for that we separately enrich from
    the scoreboard endpoint."""
    import requests as _req
    url = f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{team_abbr.lower()}/schedule'
    resp = _req.get(
        url,
        headers={'User-Agent': _ESPN_UA, 'Accept': 'application/json'},
        timeout=12,
    )
    resp.raise_for_status()
    return resp.json()


def _espn_find_event_in_scoreboard(event_id: str, sport: str, league: str) -> Optional[dict]:
    """Find an event in today's scoreboard by id — used to enrich a live game
    with the full competition.situation block (balls/strikes/outs, lastPlay,
    etc.) that the schedule endpoint omits."""
    try:
        data = _espn_fetch_scoreboard(sport, league)
    except Exception as e:
        logger.warning(f'Scoreboard enrichment fetch failed: {e}')
        return None
    for event in data.get('events') or []:
        if str(event.get('id')) == str(event_id):
            return event
    return None


@app.route('/api/sports')
def get_sports():
    """Fetch last completed + current live + next upcoming game from ESPN.

    Strategy (same source HA TeamTracker uses):
      1. Resolve the configured team to an ESPN abbreviation via the teams
         list endpoint (cached forever, 30-team list).
      2. Fetch the team's full season schedule — a compact list of every
         game the team plays with past/live/future states. No 50-event cap.
      3. Scan that schedule to pick last POST, current IN, next PRE.
      4. If there's a live game, hit the league scoreboard endpoint once
         to enrich it with `competition.situation` (balls/strikes/outs,
         lastPlay text, possession, etc.) — the schedule endpoint omits
         those fields, so this is the only way to get real-time data.
    """
    err = _require_auth()
    if err:
        return err
    try:
        enabled = settings.get('ui.daily_kiosk.sports.enabled', False)
        if not enabled:
            return jsonify({'enabled': False})

        league_code = str(
            settings.get('ui.daily_kiosk.sports.league', 'MLB') or 'MLB'
        ).upper()
        team_id_setting = settings.get('ui.daily_kiosk.sports.team_id', '')
        team_name = settings.get('ui.daily_kiosk.sports.team_name', '')

        if league_code not in _ESPN_LEAGUE_MAP:
            return jsonify({
                'enabled': True,
                'error': f'Unknown league code: {league_code}. '
                         f'Supported: {sorted(_ESPN_LEAGUE_MAP.keys())}',
            }), 400

        sport, league_path = _ESPN_LEAGUE_MAP[league_code]

        import time as _time
        now = _time.time()
        cache_key = f'{league_code}:{team_id_setting or team_name}'
        cached = _sports_cache.get(cache_key)
        if cached:
            max_age = _SPORTS_CACHE_TTL_LIVE if cached['data'].get('live') else _SPORTS_CACHE_TTL_IDLE
            if (now - cached['ts']) < max_age:
                return jsonify(cached['data'])

        # Step 1 — resolve team string (id / abbr / name) to an ESPN record
        needles = [team_id_setting, team_name]
        resolved = _espn_resolve_team(sport, league_path, needles)
        if not resolved:
            return jsonify({
                'enabled': True,
                'error': (
                    f'Could not find team for {league_code}: '
                    f'{team_name or team_id_setting}. Check settings.'
                ),
                'league': league_code,
                'sport': sport,
                'team_name': team_name,
                'last_game': None,
                'next_game': None,
                'live': False,
            })

        team_abbr = resolved['abbr']
        resolved_display = resolved['display']

        # Step 2 — fetch the team's schedule and walk every game
        try:
            schedule = _espn_fetch_team_schedule(sport, league_path, team_abbr)
        except Exception as e:
            logger.warning(f'ESPN schedule fetch failed for {team_abbr}: {e}')
            schedule = {'events': []}
        sched_events = schedule.get('events') or []
        # The schedule endpoint uses "displayName" not "shortDisplayName" for
        # competitors; feed the matcher the resolved team identifiers so it
        # finds "our" competitor inside each event.
        match_needles = [resolved['abbr'], resolved['id'], resolved_display]

        our_games: list = []
        for event in sched_events:
            g = _sports_build_game(event, match_needles, sport)
            if g:
                our_games.append(g)

        our_games.sort(key=lambda g: g.get('gameDate') or '')

        last_game = None
        live_game = None
        next_game = None
        for g in our_games:
            s = g.get('state')
            if s == 'post':
                last_game = g  # sorted ascending — keeps rolling to the latest
            elif s == 'in':
                live_game = g
            elif s == 'pre' and next_game is None:
                next_game = g

        # Step 3 — enrich the live game with full situation data from today's
        # scoreboard (schedule endpoint omits competition.situation).
        if live_game:
            # Find the event id from the schedule entry we matched
            live_event_id = None
            for event in sched_events:
                comp = (event.get('competitions') or [{}])[0]
                status_state = (
                    ((comp.get('status') or {}).get('type') or {}).get('state') or ''
                ).lower()
                if status_state == 'in':
                    live_event_id = str(event.get('id') or '')
                    break
            if live_event_id:
                rich_event = _espn_find_event_in_scoreboard(live_event_id, sport, league_path)
                if rich_event is not None:
                    rich_game = _sports_build_game(rich_event, match_needles, sport)
                    if rich_game is not None:
                        live_game = rich_game

        primary_next = live_game or next_game

        result = {
            'enabled': True,
            'league': league_code,
            'sport': sport,
            'team_name': team_name or resolved_display,
            'last_game': last_game,
            'next_game': primary_next,
            'live': live_game is not None,
        }

        _sports_cache[cache_key] = {'data': result, 'ts': now}
        return jsonify(result)
    except Exception as e:
        logger.error(f"Sports API error: {e}", exc_info=True)
        return jsonify({'error': str(e), 'enabled': True}), 500


# =============================================================================
# News API (RSS)
# =============================================================================

_news_cache: dict = {}   # {feed_url: {"data": ..., "ts": float}}
_NEWS_CACHE_TTL = 900    # 15 minutes

@app.route('/api/news')
def get_news():
    """Fetch news headlines from configured RSS feed."""
    err = _require_auth()
    if err:
        return err
    try:
        enabled = settings.get('ui.daily_kiosk.news.enabled', False)
        feed_url = settings.get('ui.daily_kiosk.news.feed_url', '')
        feed_name = settings.get('ui.daily_kiosk.news.feed_name', 'News')

        if not enabled or not feed_url:
            return jsonify({'enabled': False})

        import time as _time
        now = _time.time()
        cached = _news_cache.get(feed_url)
        if cached and (now - cached['ts']) < _NEWS_CACHE_TTL:
            return jsonify(cached['data'])

        import requests as _req
        import xml.etree.ElementTree as ET

        resp = _req.get(feed_url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; LumaKiosk/1.0)',
        })
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        ns = {'media': 'http://search.yahoo.com/mrss/'}

        items = []
        for item in root.findall('.//item'):
            if len(items) >= 10:
                break
            title_el = item.find('title')
            desc_el = item.find('description')
            link_el = item.find('link')
            pub_el = item.find('pubDate')
            media_el = item.find('media:content', ns)

            title = title_el.text.strip() if title_el is not None and title_el.text else ''
            if not title:
                continue

            image_url = ''
            if media_el is not None:
                image_url = media_el.get('url', '')
                # Request highest quality — swap CDN resize to 1920x1080
                import re as _re
                image_url = _re.sub(r'/\d{3,4}/\d{3,4}/', '/1920/1080/', image_url, count=1)

            # Parse pubDate to relative time
            pub_date = pub_el.text.strip() if pub_el is not None and pub_el.text else ''
            time_ago = ''
            if pub_date:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub_date)
                    from datetime import datetime, timezone
                    diff = datetime.now(timezone.utc) - dt
                    mins = int(diff.total_seconds() / 60)
                    if mins < 60:
                        time_ago = f'{mins}m ago'
                    elif mins < 1440:
                        time_ago = f'{mins // 60}h ago'
                    else:
                        time_ago = f'{mins // 1440}d ago'
                except Exception:
                    pass

            items.append({
                'title': title,
                'description': (desc_el.text.strip()[:200] if desc_el is not None and desc_el.text else ''),
                'link': link_el.text.strip() if link_el is not None and link_el.text else '',
                'image': image_url,
                'timeAgo': time_ago,
                'pubDate': pub_date,
            })

        result = {
            'enabled': True,
            'feed_name': feed_name,
            'items': items,
        }

        _news_cache[feed_url] = {'data': result, 'ts': now}
        return jsonify(result)
    except Exception as e:
        logger.error(f"News API error: {e}")
        return jsonify({'error': str(e), 'enabled': True}), 500


# =============================================================================
# Plex Health & Image Proxy
# =============================================================================

@app.route('/api/plex/health')
def plex_health():
    """Validate Plex connection and return server info + libraries."""
    try:
        result = plex_service.health_check()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Plex health check failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


_PLEX_IMG_PATH = re.compile(
    r'/library/(?:metadata|collections)/\d+/(?:thumb|art|clearLogo|banner|composite|poster)(?:/\d+)?'
    r'|/library/sections/\d+/composite/\d+', re.ASCII)


def _plex_image_path(raw: str):
    """The Plex library image path to fetch, or None. A photo-transcode URL is
    accepted only when its inner url is itself a library image path."""
    raw = (raw or '').strip()
    if raw.startswith('/photo/:/transcode?'):
        from urllib.parse import parse_qs, urlsplit
        inner = parse_qs(urlsplit(raw).query).get('url', [''])[0]
        return inner if _PLEX_IMG_PATH.fullmatch(inner) else None
    return raw if _PLEX_IMG_PATH.fullmatch(raw) else None


@app.route('/api/img/plex')
def img_plex_proxy():
    """Secure proxy for Plex images. Never exposes PLEX_TOKEN to the browser.

    Usage: /api/img/plex?url=/library/metadata/12345/thumb/1234567890

    Optional w/h params request a resized copy via Plex's photo transcoder.
    Plex serves full-resolution originals (~1MB per poster) otherwise, which
    is wasteful for small thumbnails/grid cards — transcoded copies are ~15KB.
    """
    plex_path = request.args.get('url', '')

    if not plex_path:
        return 'Missing url parameter', 400

    # Security: only Plex's own image paths, never an outside URL.
    plex_path = _plex_image_path(plex_path)
    if not plex_path:
        return 'Not a Plex image path', 400

    # Optional resize via Plex photo transcoder (clamped to sane bounds)
    def _clamp_dim(raw):
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return None
        return max(16, min(v, 2000)) if v > 0 else None

    req_w = _clamp_dim(request.args.get('w'))
    req_h = _clamp_dim(request.args.get('h'))

    # Try user's Plex first, fall back to global
    _user_plex = get_plex_for_request()
    plex_url = _user_plex.plex_url if _user_plex else settings.get('plex.url')
    plex_token = _user_plex.plex_token if _user_plex else settings.get('plex.token')

    if not plex_url or not plex_token:
        return 'Plex not configured', 503

    try:
        original_url = f"{plex_url}{plex_path}"
        if req_w or req_h:
            # Plex's photo transcoder needs both width and height. With only
            # one given, fit inside a box twice as long the other way
            # (minSize=0), so the requested side is the one that limits.
            from urllib.parse import quote
            fit = 0 if not (req_w and req_h) else 1
            w = req_w or min(2000, req_h * 2)
            h = req_h or min(2000, req_w * 2)
            full_url = (f"{plex_url}/photo/:/transcode?minSize={fit}&upscale=0"
                        f"&width={w}&height={h}&url={quote(plex_path, safe='')}")
        else:
            full_url = original_url
        resp = requests.get(
            full_url,
            headers={'X-Plex-Token': plex_token},
            timeout=15,
            stream=True,
        )
        if resp.status_code != 200 and full_url != original_url:
            # Transcoder refused: serve the original rather than no image.
            resp = requests.get(original_url, headers={'X-Plex-Token': plex_token}, timeout=15, stream=True)

        if resp.status_code != 200:
            # Errors are never cached: a transient Plex miss must not stick a
            # blank backdrop or logo in the browser (images cache a day).
            from flask import Response
            return Response('Image not found', status=404, headers={'Cache-Control': 'no-store'})

        from flask import Response
        import re as _re, hashlib as _hl
        ctype = (resp.headers.get('Content-Type') or 'image/jpeg').split(';')[0].strip().lower()
        if not ctype.startswith('image/') or ctype == 'image/svg+xml':
            return Response('Not an image', status=502, headers={'Cache-Control': 'no-store'})
        body = resp.content
        # Versioned Plex paths (/thumb/<updatedAt>) change URL when the art changes -> cache a day.
        # Unversioned ones (/library/metadata/<rk>/thumb) keep the same URL after a Fix Match or
        # poster change, so make the browser revalidate (cheap 304 via ETag) instead of showing old art.
        versioned = bool(_re.search(r'/(thumb|art|clearLogo|banner|theme)/\d+', plex_path))
        etag = '"' + _hl.md5(body).hexdigest() + '"'
        if not versioned and request.headers.get('If-None-Match') == etag:
            return Response(status=304, headers={'ETag': etag, 'Cache-Control': 'private, no-cache'})
        return Response(
            body,
            content_type=ctype,
            headers={'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff'} if versioned
            else {'Cache-Control': 'private, no-cache', 'ETag': etag, 'X-Content-Type-Options': 'nosniff'},
        )
    except requests.RequestException as e:
        logger.error(f"Plex image proxy error: {_redact_secrets(e)}")
        from flask import Response
        return Response('Error fetching image', status=502, headers={'Cache-Control': 'no-store'})

@app.route('/api/img/tmdb')
def img_tmdb_proxy():
    """Proxy for TMDb images. Caches aggressively since TMDb images rarely change.

    Usage: /api/img/tmdb?path=/xYz123.jpg&size=w500
    """
    tmdb_path = request.args.get('path', '')
    size = request.args.get('size', 'w500')

    if not tmdb_path:
        return 'Missing path parameter', 400

    # Security: only allow image-like paths
    if not tmdb_path.startswith('/') or not any(tmdb_path.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp')):
        return 'Invalid path', 400

    # Validate size parameter
    valid_sizes = {'w92', 'w154', 'w185', 'w342', 'w500', 'w780', 'w1280', 'original'}
    if size not in valid_sizes:
        size = 'w500'

    try:
        full_url = f"https://image.tmdb.org/t/p/{size}{tmdb_path}"
        resp = requests.get(full_url, timeout=15, stream=True)

        if resp.status_code != 200:
            return 'Image not found', 404

        from flask import Response
        return Response(
            resp.content,
            content_type=resp.headers.get('Content-Type', 'image/jpeg'),
            headers={
                'Cache-Control': 'public, max-age=604800',  # 7-day cache
            },
        )
    except requests.RequestException as e:
        logger.error(f"TMDb image proxy error: {e}")
        return 'Error fetching image', 502


# Playback Endpoints
@app.route('/api/player/play', methods=['POST'])
def player_play():
    """Trigger Kodi playback on a room device.

    Resolves the target device + service from the v3 room hierarchy,
    wakes the device (ADB/WoL/Harmony), then plays via Kodi JSON-RPC.
    """
    try:
        data = request.get_json() or {}
        rating_key = data.get('rating_key')
        client_ip = data.get('client_ip')
        room_id = data.get('room_id')
        device_id = data.get('device_id')
        service_id = data.get('service_id')
        resume_offset = data.get('resume_offset', 0)
        play_queue_id = data.get('play_queue_id') or data.get('playQueueID') or None
        if play_queue_id is not None:
            try:
                play_queue_id = int(play_queue_id)
            except (TypeError, ValueError):
                play_queue_id = None

        # Auto-resolve resume offset from Plex viewOffset when resume=true
        if data.get('resume') and rating_key and not resume_offset:
            try:
                metadata = plex_service.get_metadata(rating_key)
                if metadata:
                    resume_offset = metadata.get('viewOffset', 0)
            except Exception as e:
                logger.warning(f"Failed to fetch viewOffset for resume: {e}")

        # ── Resolve room / device / service from v3 hierarchy ──
        room = None
        device = None
        service = None
        adb_port = settings.get_default_adb_port()

        if room_id:
            room = settings.get_room_by_id(room_id)

        if room and isinstance(room.get('devices'), list):
            # Find the specific device by ID
            for dev in room['devices']:
                if not isinstance(dev, dict):
                    continue
                if device_id and dev.get('id') == device_id:
                    device = dev
                    break
            if not device and room['devices']:
                # No device_id specified — prefer a device with a Plex service
                # (rating_key is always Plex content, so route to Plex when possible)
                for dev in room['devices']:
                    if not isinstance(dev, dict):
                        continue
                    for svc in (dev.get('services') or []):
                        if isinstance(svc, dict) and svc.get('type') == 'plex':
                            device = dev
                            break
                    if device:
                        break
                # Fall back to first device if no Plex service found
                if not device:
                    device = room['devices'][0] if isinstance(room['devices'][0], dict) else None

            if device:
                # Device host takes precedence over request body client_ip
                # (legacy UI may send wrong IP from first-device normalization)
                client_ip = device.get('host', '') or client_ip
                adb_port = device.get('connectPort', adb_port)

                # Find the specific service by ID, or prefer Plex service
                for svc in (device.get('services') or []):
                    if not isinstance(svc, dict):
                        continue
                    if service_id and svc.get('id') == service_id:
                        service = svc
                        break
                if not service and device.get('services'):
                    # Prefer Plex service since we're playing Plex content
                    for svc in (device.get('services') or []):
                        if isinstance(svc, dict) and svc.get('type') == 'plex':
                            service = svc
                            break
                    if not service:
                        service = device['services'][0] if isinstance(device['services'][0], dict) else None
        elif room:
            # Flat room fallback (no devices array)
            client_ip = client_ip or room.get('host') or room.get('ip', '')
            adb_port = room.get('connectPort') or room.get('adb_port', adb_port)

        if not rating_key or not client_ip:
            return jsonify({'error': 'rating_key and client_ip/room_id are required'}), 400

        dev_name = device.get('name', '?') if device else 'none'
        dev_harmony = device.get('harmonyActivity', '') if device else ''
        logger.info(f"Triggering Kodi playback: rating_key={rating_key}, client={client_ip}:{adb_port}, "
                     f"device='{dev_name}', harmonyActivity='{dev_harmony}', "
                     f"room_useHarmony={room.get('useHarmonyHub') if room else False}, offset={resume_offset}ms")

        # ── Wake device (ADB, WoL, Harmony) ──
        from app.device_wake import ensure_device_ready
        wake = ensure_device_ready(device or {}, room or {})
        logger.info("Device wake result: ok=%s steps=%s", wake['ok'], wake['steps'])

        # ── Route to service-specific playback ──
        platform = device.get('platform', '') if device else ''
        room_name = room.get('name', room_id) if room else (room_id or 'unknown')
        svc_type = (service or {}).get('type', 'kodi')
        if svc_type == 'plex':
            return _play_via_plex(service or {}, rating_key, resume_offset, room_name, client_ip, adb_port, platform, play_queue_id=play_queue_id)
        return _play_via_kodi(service or {}, rating_key, resume_offset, room_name, client_ip, adb_port, platform)

    except Exception as e:
        logger.error(f"Error triggering playback: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _arp_ip_for_mac(mac):
    """Current LAN IP for *mac* from the host ARP table (Luma runs host-networked)."""
    mac = (mac or '').strip().lower().replace('-', ':')
    if not mac:
        return None
    try:
        with open('/proc/net/arp') as f:
            for line in f.read().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and parts[3].lower() == mac and parts[2] != '0x0':
                    return parts[0]
    except OSError:
        pass
    return None


def _kodi_rediscover(old_ip, port, user, password):
    """Kodi stopped answering at *old_ip*: if the device's MAC now maps to a
    different IP where Kodi answers a JSON-RPC ping, save that IP as the
    device host and return it."""
    from app.kodi_service import KodiService
    try:
        for room in settings.get('devices.rooms') or []:
            for dev in room.get('devices') or []:
                if dev.get('host') != old_ip or not dev.get('macAddress'):
                    continue
                if not any((sv or {}).get('type') == 'kodi' for sv in dev.get('services') or []):
                    continue
                new_ip = _arp_ip_for_mac(dev['macAddress'])
                if not new_ip or new_ip == old_ip:
                    return None
                if not KodiService(host=new_ip, port=port, username=user, password=password, timeout=3).ping():
                    return None
                settings.update_device(room.get('id'), dev.get('id'), {'host': new_ip})
                logger.warning("Kodi: %s moved %s -> %s (by MAC); device host updated",
                               dev.get('name'), old_ip, new_ip)
                return new_ip
    except Exception as e:  # noqa: BLE001
        logger.debug("Kodi rediscovery failed: %s", e)
    return None


def _play_via_kodi(service, rating_key, resume_offset, room_name, device_ip, adb_port, platform=''):
    """Handle playback via Kodi JSON-RPC using direct file URLs.

    If Kodi is already running, uses JSON-RPC directly (no splash screen).
    If Kodi is not running and ADB is available, launches Kodi with the
    file URL as an intent so it opens straight to playback (no splash).

    Device wake is handled by the caller via device_wake.ensure_device_ready().
    """
    from app.kodi_service import KodiService, resolve_kodi_file_url, kodi_url_for_nas_path, DEFAULT_NAS_BASE
    from app.plex_file_info import get_path_map, map_path
    from app.adb_utils import run_adb

    kodi_host = service.get('kodi_host') or device_ip
    kodi_port = int(service.get('kodi_port') or 8080)
    kodi_user = service.get('kodi_username', '')
    kodi_pass = service.get('kodi_password', '')
    mount_point = service.get('kodi_mount_point', '')  # V1 fallback only

    # Get metadata from Plex (including filePath)
    metadata = plex_service.get_metadata(rating_key)
    if not metadata:
        return jsonify({'success': False, 'message': 'Could not resolve metadata from Plex'}), 500

    file_path = metadata.get('filePath')
    title = metadata.get('title', '?')
    if not file_path:
        return jsonify({'success': False, 'message': f'No file path found in Plex metadata for "{title}"'}), 500

    # Send Kodi the file's real NAS location: Plex's container path
    # (/movies/Movies/...) → NAS share path (/Media/Video/Movies/...) via the
    # same map File Info uses, then onto one of Kodi's own sources (or the
    # NAS SMB share). The old Plex-synced Library Paths table is only a
    # fallback.
    kodi = KodiService(host=kodi_host, port=kodi_port, username=kodi_user, password=kodi_pass)
    probe = KodiService(host=kodi_host, port=kodi_port, username=kodi_user, password=kodi_pass, timeout=3)
    kodi_up = probe.ping()
    if not kodi_up and not service.get('kodi_host'):
        # DHCP may have moved the box: find it again by its MAC address.
        new_ip = _kodi_rediscover(device_ip, kodi_port, kodi_user, kodi_pass)
        if new_ip:
            kodi_host = device_ip = new_ip
            kodi = KodiService(host=kodi_host, port=kodi_port, username=kodi_user, password=kodi_pass)
            probe = KodiService(host=kodi_host, port=kodi_port, username=kodi_user, password=kodi_pass, timeout=3)
            kodi_up = probe.ping()
    nas_path = map_path(file_path, get_path_map(settings))
    sources = probe.get_video_sources() if kodi_up else []
    file_url = None
    if nas_path != file_path or nas_path.startswith('/Media/'):
        file_url = kodi_url_for_nas_path(nas_path, sources, settings.get('kodi.nas_base') or DEFAULT_NAS_BASE)
    if not file_url:
        file_url = resolve_kodi_file_url(file_path, settings.get('media.paths') or [], mount_point)
    if not file_url:
        logger.warning("Kodi: no URL for Plex path %s (nas path %s)", file_path, nas_path)
        return jsonify({'success': False, 'message': "Couldn't find this file on the NAS"}), 400

    logger.info("Kodi: playing '%s' via file URL: %s (kodi %s)", title,
                re.sub(r'://[^/@]+@', '://***@', file_url), 'up' if kodi_up else 'down')

    _ADB_PLATFORMS = {'android_tv', 'chromecast_gtv', ''}
    has_adb = device_ip and adb_port and platform in _ADB_PLATFORMS

    try:
        # Fast check: is Kodi already running?
        if kodi_up:
            # Kodi is up — play via JSON-RPC (supports resume, no splash)
            logger.info("Kodi: JSON-RPC already responsive, playing via JSON-RPC")
            kodi.play_file(file_url, resume_offset_ms=resume_offset)
            logger.info(f"Kodi: playback started for '{title}'")
            return jsonify({
                'success': True,
                'message': f'Kodi playback initiated in {room_name}',
                'rating_key': rating_key,
                'resume_offset': resume_offset
            })

        # Kodi not running — launch via ADB intent with file URL (straight to playback)
        if has_adb:
            logger.info("Kodi: not running, launching via ADB intent with file URL")
            target = f"{device_ip}:{adb_port}"
            launch = run_adb(["adb", "-s", target, "shell",
                              "am", "start", "-a", "android.intent.action.VIEW",
                              "-d", file_url, "-t", "video/*",
                              "-n", "org.xbmc.kodi/.Splash"], timeout=5)
            logger.info(f"Kodi ADB intent: ok={launch['ok']}, "
                        f"out={launch['stdout'].strip()}, err={launch['stderr'].strip()}")

            if launch['ok']:
                # Give Kodi a moment to start, then try JSON-RPC for resume if needed
                if resume_offset > 0:
                    import time as _t
                    for attempt in range(15):
                        _t.sleep(1)
                        if kodi.ping():
                            logger.info(f"Kodi: JSON-RPC ready after {attempt + 1}s, seeking to resume point")
                            try:
                                kodi.seek_to(resume_offset)
                            except Exception:
                                logger.warning("Kodi: resume seek failed, playback continues from start")
                            break

                return jsonify({
                    'success': True,
                    'message': f'Kodi playback initiated via ADB intent in {room_name}',
                    'rating_key': rating_key,
                    'resume_offset': resume_offset
                })

        if has_adb:
            return jsonify({'success': False, 'message': f"Couldn't start Kodi in {room_name}"}), 409
        logger.warning("Kodi: not responding at %s:%s and no ADB to launch it", kodi_host, kodi_port)
        return jsonify({'success': False, 'message': f"Kodi isn't responding in {room_name} ({kodi_host}:{kodi_port})"}), 409

    except Exception as e:
        logger.error(f"Kodi playback failed: {e}")
        return jsonify({'success': False, 'message': f'Kodi playback error: {e}'}), 500


def _resolve_playqueue_start(play_queue_id, fallback_rating_key):
    """Return (episode_rating_key, play_queue_id) for shuffle/play-queue playback.

    Plex playMedia requires key= to point at a playable Video (episode), not a
    show/season. The shuffled order lives in containerKey=/playQueues/{id}.
    """
    if not play_queue_id:
        return fallback_rating_key, None

    plex_url = settings.get('plex.url')
    plex_token = settings.get('plex.token')
    if not plex_url or not plex_token:
        return fallback_rating_key, play_queue_id

    try:
        import xml.etree.ElementTree as ET
        resp = requests.get(
            f"{plex_url}/playQueues/{play_queue_id}",
            params={'window': 500, 'own': 0},
            headers={'X-Plex-Token': plex_token, 'Accept': 'application/xml'},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning("playQueue %s fetch failed: %s", play_queue_id, resp.status_code)
            return fallback_rating_key, play_queue_id

        root = ET.fromstring(resp.content)
        # Prefer the selected item (shuffled start), else first Video
        selected_id = root.get('playQueueSelectedItemID')
        first = None
        for video in root.findall('.//Video'):
            if first is None:
                first = video
            if selected_id and video.get('playQueueItemID') == str(selected_id):
                first = video
                break
        if first is not None:
            ep_rk = first.get('ratingKey') or fallback_rating_key
            logger.info(
                "Shuffle: playQueue %s -> start episode rk=%s (title=%r)",
                play_queue_id, ep_rk, first.get('title'),
            )
            return ep_rk, play_queue_id
        logger.warning("playQueue %s had no Video items", play_queue_id)
    except Exception as e:
        logger.warning("Could not resolve playQueue %s: %s", play_queue_id, e)

    return fallback_rating_key, play_queue_id


def _play_via_plex(service, rating_key, resume_offset, room_name, device_ip, adb_port, platform='', play_queue_id=None):
    """Handle Plex playback via platform-specific method.

    Shield (android_tv):  HTTP Client API at port 32500 (has player capability).
    Google TV (chromecast_gtv):  ADB intent with plex://server:// URI.

    When play_queue_id is set (series/season Shuffle), playback uses the
    shuffled Plex playQueue (containerKey) — not the show/season metadata key
    which would start at S01E01.
    """
    from app.adb_utils import run_adb
    import time as _t
    from urllib.parse import quote, urlparse  # noqa: F401 — kept for parity / callers

    _ADB_PLATFORMS = {'android_tv', 'chromecast_gtv', ''}
    has_adb = device_ip and adb_port and platform in _ADB_PLATFORMS

    if not has_adb:
        return jsonify({'success': False, 'message': 'Plex playback requires ADB (Android TV / Chromecast GTV device)'}), 400

    # Resolve Plex machine ID
    machine_id = settings.get('plex.machine_id') or ''
    if not machine_id:
        try:
            root = plex_service._get('/')
            if root is not None:
                machine_id = root.get('machineIdentifier', '')
                if machine_id:
                    settings.update({'plex': {'machine_id': machine_id}})
                    logger.info(f"Auto-saved Plex machine_id: {machine_id}")
        except Exception as mid_err:
            logger.warning(f"Could not fetch Plex machine_id: {mid_err}")

    if not machine_id:
        return jsonify({'success': False, 'message': 'Plex machine_id not configured and could not be auto-detected'}), 500

    play_rk, play_queue_id = _resolve_playqueue_start(play_queue_id, rating_key)

    metadata = plex_service.get_metadata(play_rk)
    title = metadata.get('title', '?') if metadata else '?'
    offset_ms = int(resume_offset) if resume_offset else 0
    target = f"{device_ip}:{adb_port}"

    logger.info(
        f"Plex play: '{title}' (rk={play_rk}, source_rk={rating_key}, pq={play_queue_id}) "
        f"on {target}, platform={platform}, offset={offset_ms}ms"
    )

    try:
        # Plex for Android TV 2026.x ("New Plex Experience") removed the
        # Companion receiver: nothing listens on :32500 and the app never
        # appears in PMS /clients, so HTTP playMedia (direct or PMS-proxied)
        # cannot work. Only use the legacy HTTP path when :32500 is actually
        # open (older app build); otherwise start playback with an ADB deep link.
        client_port = int(service.get('plex_client_port') or settings.get('plex.client_port', 32500) or 32500)
        legacy_http = platform != 'chromecast_gtv' and _tcp_port_open(device_ip, client_port)
        logger.info(
            "Plex play route: %s (platform=%s, :%s %s)",
            'legacy_http' if legacy_http else 'adb_deeplink', platform or '?',
            client_port, 'open' if legacy_http else 'closed',
        )
        if legacy_http:
            return _plex_play_shield(
                run_adb, _t, target, device_ip, machine_id, play_rk, offset_ms, room_name, service,
                play_queue_id=play_queue_id, source_rating_key=rating_key,
            )
        return _plex_play_deeplink(
            run_adb, target, device_ip, machine_id, play_rk, offset_ms, room_name,
            play_queue_id=play_queue_id, source_rating_key=rating_key,
        )

    except Exception as e:
        logger.error(f"Plex playback failed: {e}")
        return jsonify({'success': False, 'message': f'Plex playback error: {e}'}), 500



def _tcp_port_open(host, port, timeout=0.8):
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _plex_session_on(device_ip):
    """Return the Plex session dict playing on device_ip (by Player address), or None."""
    import xml.etree.ElementTree as ET
    plex_url = (settings.get('plex.url') or '').rstrip('/')
    plex_token = settings.get('plex.token') or ''
    try:
        resp = requests.get(
            f"{plex_url}/status/sessions",
            headers={'X-Plex-Token': plex_token, 'Accept': 'application/xml'},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        root = ET.fromstring(resp.content)
        for v in root.findall('Video'):
            p = v.find('Player')
            if p is None or p.get('address') != device_ip:
                continue
            return {
                'title': v.get('title'),
                'grandparentTitle': v.get('grandparentTitle'),
                'ratingKey': v.get('ratingKey'),
                'state': p.get('state'),
                'viewOffset': int(v.get('viewOffset') or 0),
            }
    except Exception as e:
        logger.debug("Plex session poll: %s", e)
    return None


def _plex_playqueue_start(play_queue_id, default_rk):
    """(ratingKey, title) of the playQueue's selected item."""
    import xml.etree.ElementTree as ET
    plex_url = (settings.get('plex.url') or '').rstrip('/')
    plex_token = settings.get('plex.token') or ''
    for own in (1, 0):
        try:
            resp = requests.get(
                f"{plex_url}/playQueues/{play_queue_id}",
                params={'own': own, 'window': 10},
                headers={'X-Plex-Token': plex_token, 'Accept': 'application/xml'},
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            videos = root.findall('Video')
            sel = root.get('playQueueSelectedItemID')
            chosen = next((v for v in videos if sel and v.get('playQueueItemID') == sel), None)
            if chosen is None and videos:
                chosen = videos[0]
            if chosen is not None:
                return str(chosen.get('ratingKey') or default_rk), chosen.get('title')
        except Exception as e:
            logger.debug("playQueue start lookup: %s", e)
    return str(default_rk), None




def _plex_deeplink_candidates(machine_id, rating_key, offset_ms, play_queue_id=None):
    """Deep links for Plex for Android TV, current format first.

    Plex 2026.x: plex://watch/video?uri=<server:// item or playQueue>&autoPlay=1
    with resume as &offset=<seconds> (viewOffset/autoPlay on the legacy
    plex://server:// form are ignored by the new app). The legacy form is
    kept last for TVs still on an older Plex build.
    Each entry: (label, uri, start_playback_extra).
    """
    from urllib.parse import quote
    lib = f"server://{machine_id}/com.plexapp.plugins.library"
    target = f"{lib}/playQueues/{play_queue_id}" if play_queue_id else f"{lib}/library/metadata/{rating_key}"
    offset_s = int(offset_ms // 1000) if (offset_ms and offset_ms > 0 and not play_queue_id) else 0
    watch = f"plex://watch/video?uri={quote(target, safe='')}&autoPlay=1"
    if offset_s:
        watch += f"&offset={offset_s}"
    if play_queue_id:
        legacy = f"plex://{target}?own=1&window=200&autoPlay=1"
    else:
        legacy = f"plex://{target}?autoPlay=1" + (f"&viewOffset={int(offset_ms)}" if offset_s else '')
    return [('watch', watch, False), ('watch_retry', watch, False), ('legacy', legacy, True)]


def _plex_play_deeplink(run_adb, target, device_ip, machine_id, rating_key, offset_ms, room_name,
                        play_queue_id=None, source_rating_key=None):
    """Plex for Android TV (Shield + Google TV) via ADB deep link.

    Plex 2026.x dropped the Companion HTTP receiver, so playback is started
    with a VIEW intent. The app must already be running and connected to the
    server (a cold-start deep link shows "Can't Reach Server"), so Plex is
    restarted, given a few seconds to connect, then sent the link. Success is
    confirmed through PMS /status/sessions.
    """
    import time as _t

    report_rk = source_rating_key or rating_key

    run_adb(["adb", "connect", target], timeout=5)
    shell_ready = False
    for probe_attempt in range(5):
        probe = run_adb(["adb", "-s", target, "shell", "echo", "ok"], timeout=3)
        if probe.get('ok') and 'ok' in (probe.get('stdout') or ''):
            shell_ready = True
            break
        logger.info("Plex deep-link readiness probe %d: %s — reconnecting",
                    probe_attempt + 1, (probe.get('stderr') or '').strip())
        run_adb(["adb", "disconnect", target], timeout=3)
        _t.sleep(1)
        run_adb(["adb", "connect", target], timeout=3)
        _t.sleep(2)
    if not shell_ready:
        return jsonify({
            'success': False,
            'message': f"The TV in {room_name} didn't come online in time",
            'rating_key': report_rk,
        }), 409

    start_rk, start_title = str(rating_key), None
    if play_queue_id:
        start_rk, start_title = _plex_playqueue_start(play_queue_id, rating_key)

    # Restart Plex so whatever was on screen is closed and the app reconnects
    # to the server, then wait for it to be ready for a deep link.
    logger.info("Plex deep-link: home → force-stop → launch Plex on %s", target)
    run_adb(["adb", "-s", target, "shell", "input", "keyevent", "KEYCODE_HOME"], timeout=3)
    _t.sleep(0.3)
    run_adb(["adb", "-s", target, "shell", "am", "force-stop", "com.plexapp.android"], timeout=5)
    _t.sleep(0.3)
    run_adb(["adb", "-s", target, "shell", "monkey", "-p", "com.plexapp.android",
             "-c", "android.intent.category.LAUNCHER", "1"], timeout=8)
    _t.sleep(float(settings.get('plex.deeplink_warmup_s', 8) or 8))

    attempts = []
    session = None
    used = None
    for label, uri, extra in _plex_deeplink_candidates(machine_id, start_rk, offset_ms, play_queue_id):
        cmd = "am start " + ("--ez android.intent.extra.START_PLAYBACK true " if extra else "") + \
              f"-a android.intent.action.VIEW -d '{uri}' com.plexapp.android"
        result = run_adb(["adb", "-s", target, "shell", cmd], timeout=15)
        out = ((result.get('stdout') or '') + (result.get('stderr') or '')).strip().replace('\n', ' | ')
        logger.info("Plex deep-link %s on %s: ok=%s %s", label, target, result.get('ok'), out[:200])
        if not result.get('ok') or 'Error:' in out:
            attempts.append(f'{label}: am start failed')
            _t.sleep(2)
            continue
        seen = None
        for _ in range(16):
            _t.sleep(1)
            seen = _plex_session_on(device_ip)
            if seen and str(seen.get('ratingKey')) == str(start_rk) and seen.get('state') in ('playing', 'buffering', 'paused'):
                session = seen
                break
        if session:
            used = label
            break
        attempts.append(f'{label}: no session (saw {seen})')
        logger.warning("Plex deep-link %s did not start rk=%s on %s (saw %s)", label, start_rk, device_ip, seen)

    if not session:
        logger.error("Plex deep-link: nothing started rk=%s pq=%s on %s: %s", start_rk, play_queue_id, device_ip, attempts)
        return jsonify({
            'success': False,
            'message': f'Plex playback failed in {room_name}',
            'rating_key': report_rk,
            'play_queue_id': play_queue_id,
            'method': 'adb_deeplink',
            'error': '; '.join(attempts)[:500],
        }), 409

    resume_applied = None
    if offset_ms > 0 and not play_queue_id:
        resume_applied = int(session.get('viewOffset') or 0) >= offset_ms - 30000
        if not resume_applied:
            _t.sleep(4)
            now = _plex_session_on(device_ip) or session
            resume_applied = int(now.get('viewOffset') or 0) >= offset_ms - 30000
        logger.info("Plex deep-link resume: wanted %sms, applied=%s", offset_ms, resume_applied)

    logger.info("Plex deep-link OK via %s rk=%s pq=%s on %s session=%s", used, start_rk, play_queue_id, device_ip, session)
    return jsonify({
        'success': True,
        'message': f'Plex {"shuffle" if play_queue_id else "playback"} started in {room_name}',
        'rating_key': report_rk,
        'play_queue_id': play_queue_id,
        'resume_offset': 0 if play_queue_id else offset_ms,
        'resume_applied': resume_applied,
        'method': 'adb_deeplink',
        'deeplink_format': used,
        'start_rating_key': start_rk,
        'start_title': start_title or session.get('title'),
    })

def _plex_play_shield(run_adb, _t, target, device_ip, machine_id, rating_key, offset_ms, room_name, service,
                      play_queue_id=None, source_rating_key=None):
    """Shield (android_tv): HTTP Client API at port 32500.

    Shuffle uses containerKey=/playQueues/{id} so Plex plays the shuffled
    queue (not S01E01 from the show/season key). key= must be the start
    episode inside that queue.
    """
    from urllib.parse import quote, urlparse

    report_rk = source_rating_key or rating_key
    plex_token = settings.get('plex.token') or ''
    client_port = service.get('plex_client_port') or settings.get('plex.client_port', 32500)

    plex_url = settings.get('plex.url') or ''
    parsed = urlparse(plex_url) if plex_url else None
    server_protocol = parsed.scheme if parsed else 'http'
    server_address = parsed.hostname if parsed else ''
    server_port = parsed.port or (443 if server_protocol == 'https' else 32400) if parsed else 32400

    # Send to home screen first so the user doesn't see the underlying app flash,
    # then force-stop Plex and cold-launch it
    logger.info("Shield: home → force-stop Plex → relaunch")
    run_adb(["adb", "-s", target, "shell", "input", "keyevent", "KEYCODE_HOME"], timeout=3)
    _t.sleep(0.3)
    run_adb(["adb", "-s", target, "shell", "am", "force-stop", "com.plexapp.android"], timeout=5)
    _t.sleep(0.3)
    run_adb(["adb", "-s", target, "shell",
             "monkey", "-p", "com.plexapp.android",
             "-c", "android.intent.category.LAUNCHER", "1"], timeout=5)
    _t.sleep(5)

    # HTTP playMedia with retries
    key = quote(f"/library/metadata/{rating_key}")
    container = ''
    if play_queue_id:
        container = quote(f"/playQueues/{play_queue_id}?own=1&window=200", safe='')
    url = (
        f"http://{device_ip}:{client_port}/player/playback/playMedia"
        f"?key={key}"
        f"&offset={0 if play_queue_id else offset_ms}"
        f"&machineIdentifier={machine_id}"
        f"&address={server_address}"
        f"&port={server_port}"
        f"&protocol={server_protocol}"
        f"&type=video"
        f"&X-Plex-Token={plex_token}"
    )
    if container:
        url += f"&containerKey={container}"

    # For play queues, avoid re-sending playMedia after success-ish start —
    # re-POSTing containerKey restarts the shuffled queue mid-play.
    max_attempts = 1 if play_queue_id else 3
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, timeout=5)
            logger.info(f"Plex playMedia: status={resp.status_code} pq={play_queue_id}")
            if resp.status_code == 200:
                logger.info(f"Plex playMedia succeeded on attempt {attempt + 1}")
                return jsonify({
                    'success': True,
                    'message': f'Plex {"shuffle" if play_queue_id else "playback"} started in {room_name}',
                    'rating_key': report_rk,
                    'play_queue_id': play_queue_id,
                    'resume_offset': 0 if play_queue_id else offset_ms,
                })
        except Exception as e:
            logger.debug(f"Plex playMedia failed: {e}")
        if play_queue_id:
            break
        _t.sleep(2)

    if play_queue_id:
        # One 200 was enough historically even when status poll lagged; if we
        # never got 200, still report failure.
        logger.warning("Plex playMedia playQueue attempt finished without confirmed 200")
    else:
        logger.warning("Plex playMedia retries exhausted")
    return jsonify({
        'success': False,
        'message': f'Plex playback failed in {room_name}',
        'rating_key': report_rk,
    }), 500

def _plex_status(rating_key, device_ip=None):
    """Check if content is currently playing via Plex sessions API.

    Fetches /status/sessions from Plex, finds a Video element with matching
    ratingKey, optionally filtered by device IP (Player address).
    """
    import xml.etree.ElementTree as ET

    plex_url = settings.get('plex.url')
    plex_token = settings.get('plex.token')

    stopped = {
        'playing': False,
        'state': 'stopped',
        'rating_key': str(rating_key),
        'viewOffset': 0,
        'duration': 0
    }

    if not plex_url or not plex_token:
        return stopped

    try:
        resp = requests.get(
            f"{plex_url}/status/sessions",
            params={'X-Plex-Token': plex_token},
            timeout=5
        )
        if resp.status_code != 200:
            return stopped

        root = ET.fromstring(resp.content)
        for video in root.findall('./Video'):
            if video.get('ratingKey') != str(rating_key):
                continue

            # Optionally match by device IP
            player = video.find('.//Player')
            if device_ip and player is not None:
                player_addr = player.get('address', '')
                if player_addr and player_addr != device_ip:
                    continue

            state = player.get('state', 'stopped') if player is not None else 'stopped'
            view_offset = int(video.get('viewOffset', 0))
            duration = int(video.get('duration', 0))

            return {
                'playing': state == 'playing',
                'state': state,
                'rating_key': str(rating_key),
                'viewOffset': view_offset,
                'duration': duration
            }

        return stopped

    except Exception as e:
        logger.error(f"Error checking Plex session status: {e}")
        return stopped


@app.route('/api/player/status/<rating_key>')
def player_status(rating_key):
    """Check if content is currently playing via Kodi or Plex.

    Accepts optional query params room_id, device_id, and service_id to
    resolve which service to poll. Falls back to first room/device.
    """
    from app.kodi_service import KodiService

    try:
        room_id = request.args.get('room_id', '')
        device_id = request.args.get('device_id', '')
        service_id = request.args.get('service_id', '')

        # Resolve the service to poll
        service = None
        device = None
        if room_id:
            room = settings.get_room_by_id(room_id)
            if room and isinstance(room.get('devices'), list):
                for dev in room['devices']:
                    if not isinstance(dev, dict):
                        continue
                    if device_id and dev.get('id') == device_id:
                        device = dev
                        break
                if not device and room['devices']:
                    # Prefer device with Plex service (rating_key is Plex content)
                    for dev in room['devices']:
                        if not isinstance(dev, dict):
                            continue
                        for svc in (dev.get('services') or []):
                            if isinstance(svc, dict) and svc.get('type') == 'plex':
                                device = dev
                                break
                        if device:
                            break
                    if not device:
                        device = room['devices'][0] if isinstance(room['devices'][0], dict) else None

        # Fall back to first room's first device (prefer Plex service)
        if not device:
            rooms = settings.get('devices.rooms') or []
            for r in rooms:
                if isinstance(r, dict) and isinstance(r.get('devices'), list):
                    for dev in r['devices']:
                        if isinstance(dev, dict) and dev.get('host'):
                            for svc in (dev.get('services') or []):
                                if isinstance(svc, dict) and svc.get('type') == 'plex':
                                    device = dev
                                    break
                            if device:
                                break
                    if device:
                        break
            # Final fallback: any device with a host
            if not device:
                for r in rooms:
                    if isinstance(r, dict) and isinstance(r.get('devices'), list):
                        for dev in r['devices']:
                            if isinstance(dev, dict) and dev.get('host'):
                                device = dev
                                break
                        if device:
                            break

        if device:
            # If service_id provided, find that specific service
            if service_id:
                for svc in (device.get('services') or []):
                    if isinstance(svc, dict) and svc.get('id') == service_id:
                        service = svc
                        break
            # Prefer Plex service since we're checking Plex content status
            if not service:
                for svc in (device.get('services') or []):
                    if isinstance(svc, dict) and svc.get('type') == 'plex':
                        service = svc
                        break
            # Fall back to any kodi/plex service
            if not service:
                for svc in (device.get('services') or []):
                    if isinstance(svc, dict) and svc.get('type') in ('kodi', 'plex'):
                        service = svc
                        break

        if not service or not device:
            return jsonify({
                'playing': False,
                'state': 'stopped',
                'rating_key': rating_key,
                'viewOffset': 0,
                'duration': 0
            })

        # Route to Plex session polling if service type is plex
        if service.get('type') == 'plex':
            device_ip = device.get('host', '')
            return jsonify(_plex_status(rating_key, device_ip))

        kodi_host = service.get('kodi_host', '') or device.get('host', '')
        kodi_port = int(service.get('kodi_port') or 8080)
        kodi_user = service.get('kodi_username', '')
        kodi_pass = service.get('kodi_password', '')

        if not kodi_host:
            return jsonify({
                'playing': False,
                'state': 'stopped',
                'rating_key': rating_key,
                'viewOffset': 0,
                'duration': 0
            })

        kodi = KodiService(host=kodi_host, port=kodi_port, username=kodi_user, password=kodi_pass)
        player = kodi.get_active_player()

        if not player:
            return jsonify({
                'playing': False,
                'state': 'stopped',
                'rating_key': rating_key,
                'viewOffset': 0,
                'duration': 0
            })

        player_id = player.get('playerid', 1)
        props = kodi.get_player_properties(player_id)

        # Convert Kodi time format to milliseconds
        time_obj = props.get('time', {})
        total_obj = props.get('totaltime', {})
        view_offset = (
            time_obj.get('hours', 0) * 3600000
            + time_obj.get('minutes', 0) * 60000
            + time_obj.get('seconds', 0) * 1000
            + time_obj.get('milliseconds', 0)
        )
        duration = (
            total_obj.get('hours', 0) * 3600000
            + total_obj.get('minutes', 0) * 60000
            + total_obj.get('seconds', 0) * 1000
            + total_obj.get('milliseconds', 0)
        )

        speed = props.get('speed', 0)
        state = 'playing' if speed > 0 else 'paused'

        return jsonify({
            'playing': speed > 0,
            'state': state,
            'rating_key': rating_key,
            'viewOffset': view_offset,
            'duration': duration
        })

    except Exception as e:
        logger.error(f"Error checking Kodi playback status: {e}")
        return jsonify({
            'playing': False,
            'state': 'stopped',
            'rating_key': rating_key,
            'viewOffset': 0,
            'duration': 0
        })


# ---------------------------------------------------------------------------
# Kodi now-playing helpers (for sessions endpoint)
# ---------------------------------------------------------------------------
_kodi_poster_cache: dict = {}  # key: "title:year" → {"url": str|None, "art": str|None, "ts": float}
_KODI_POSTER_TTL = 3600  # 1 hour


def _lookup_kodi_artwork(title: str, year: int | None = None) -> dict:
    """Look up poster and backdrop artwork for a Kodi-playing title.

    First tries to match against the local Plex library (fast in-memory search)
    for best-quality artwork.  Falls back to TMDb poster search.

    Returns ``{'thumb': str|None, 'art': str|None}``
    """
    import time
    cache_key = f"{title}:{year or ''}"
    cached = _kodi_poster_cache.get(cache_key)
    if cached and (time.time() - cached['ts']) < _KODI_POSTER_TTL:
        return {'thumb': cached.get('url'), 'art': cached.get('art')}

    thumb = None
    art = None

    # --- Strategy 1: match against Plex library (best artwork) ---
    try:
        results = fast_search_index.search(q=title, limit=5)
        matched_key = None
        for r in results:
            r_title = (r.get('title') or '').lower().strip()
            r_year = r.get('year')
            if r_title == title.lower().strip():
                # Year match (if available) for extra confidence
                if year and r_year and str(r_year) != str(year):
                    continue
                if r.get('thumb'):
                    thumb = f"/api/img/plex?url={r['thumb']}"
                    matched_key = r.get('rating_key')
                    break
        # If exact match failed, accept first result with matching title (ignore year)
        if not thumb:
            for r in results:
                r_title = (r.get('title') or '').lower().strip()
                if r_title == title.lower().strip() and r.get('thumb'):
                    thumb = f"/api/img/plex?url={r['thumb']}"
                    matched_key = r.get('rating_key')
                    break
        # Fetch backdrop art from Plex metadata (not in fast search index)
        if matched_key:
            plex_url = settings.get('plex.url')
            plex_token = settings.get('plex.token')
            if plex_url and plex_token:
                try:
                    import xml.etree.ElementTree as ET
                    meta_resp = requests.get(
                        f"{plex_url}/library/metadata/{matched_key}",
                        params={'X-Plex-Token': plex_token}, timeout=3,
                    )
                    if meta_resp.status_code == 200:
                        root = ET.fromstring(meta_resp.content)
                        video = root.find('.//Video')
                        if video is not None and video.get('art'):
                            art = f"/api/img/plex?url={video.get('art')}"
                except Exception:
                    pass  # art stays None — thumb is still good
    except Exception as e:
        logger.debug(f"Plex library lookup failed for '{title}': {e}")

    # --- Strategy 2: TMDb poster search (fallback) ---
    if not thumb:
        tmdb_api_key = settings.get('apis.tmdb.api_key') or ''
        if tmdb_api_key:
            try:
                params = {'api_key': tmdb_api_key, 'query': title}
                if year:
                    params['year'] = year
                resp = requests.get('https://api.themoviedb.org/3/search/movie', params=params, timeout=5)
                if resp.status_code == 200:
                    tmdb_results = resp.json().get('results', [])
                    if not tmdb_results and year:
                        # Retry without year filter
                        params.pop('year', None)
                        resp = requests.get('https://api.themoviedb.org/3/search/movie', params=params, timeout=5)
                        if resp.status_code == 200:
                            tmdb_results = resp.json().get('results', [])
                    if tmdb_results:
                        poster_path = tmdb_results[0].get('poster_path')
                        backdrop_path = tmdb_results[0].get('backdrop_path')
                        if poster_path:
                            thumb = f"/api/img/tmdb?path={poster_path}&size=w300"
                        if backdrop_path:
                            art = f"/api/img/tmdb?path={backdrop_path}&size=w780"
            except Exception as e:
                logger.debug(f"TMDb poster lookup failed for '{title}': {e}")

    _kodi_poster_cache[cache_key] = {'url': thumb, 'art': art, 'ts': time.time()}
    return {'thumb': thumb, 'art': art}


def _query_kodi_activity(device: dict, service: dict) -> dict | None:
    """Query a Kodi device for now-playing info.

    Returns an activity dict compatible with the sessions endpoint, or None.
    """
    from app.kodi_service import KodiService
    kodi_host = service.get('kodi_host', '') or device.get('host', '')
    kodi_port = int(service.get('kodi_port') or 8080)
    kodi_user = service.get('kodi_username', '')
    kodi_pass = service.get('kodi_password', '')

    if not kodi_host:
        return None

    try:
        kodi = KodiService(kodi_host, kodi_port, kodi_user, kodi_pass, timeout=2)
        player = kodi.get_active_player()
        if not player:
            return None

        player_id = player.get('playerid', 1)
        item = kodi.get_playing_item(player_id)
        props = kodi.get_player_properties(player_id)

        if not item:
            return None

        # Kodi returns empty 'title' for files not in its library;
        # 'label' always contains the display name (derived from filename).
        title = item.get('title') or item.get('label') or 'Unknown'
        year = item.get('year') if item.get('year') else None
        speed = props.get('speed', 0)
        state = 'playing' if speed > 0 else 'paused'

        # Calculate offset and duration in ms
        t = props.get('time', {})
        offset_ms = ((t.get('hours', 0) * 3600 + t.get('minutes', 0) * 60 +
                       t.get('seconds', 0)) * 1000 + t.get('milliseconds', 0))
        tt = props.get('totaltime', {})
        duration_ms = ((tt.get('hours', 0) * 3600 + tt.get('minutes', 0) * 60 +
                         tt.get('seconds', 0)) * 1000 + tt.get('milliseconds', 0))

        # Artwork lookup (Plex library first, then TMDb fallback)
        artwork = _lookup_kodi_artwork(title, year)

        progress_pct = round((offset_ms / duration_ms) * 100) if duration_ms > 0 else 0
        return {
            'appName': 'Kodi',
            'title': title,
            'year': str(year) if year else '',
            'state': state,
            'position': offset_ms,
            'duration': duration_ms,
            'progressPercent': progress_pct,
            'thumb': artwork.get('thumb'),
            'art': artwork.get('art'),
            'deviceId': device.get('id', ''),
        }
    except Exception as e:
        logger.debug(f"Kodi activity query failed for {kodi_host}:{kodi_port}: {e}")
        return None


_yt_thumb_cache: dict = {}  # {title: {'url': str|None, 'ts': float}}
_YT_THUMB_CACHE_TTL = 3600  # 1 hour
_YT_INNERTUBE_KEY = os.environ.get('YT_INNERTUBE_KEY', '')


def _get_youtube_thumbnail(title: str, device_ip: str, adb_port: int) -> str | None:
    """Resolve a YouTube thumbnail URL via InnerTube search, with caching."""
    import time as _time

    # Check cache first
    cached = _yt_thumb_cache.get(title)
    if cached and (_time.time() - cached['ts']) < _YT_THUMB_CACHE_TTL:
        return cached['url']

    try:
        resp = requests.post(
            f'https://www.youtube.com/youtubei/v1/search?key={_YT_INNERTUBE_KEY}',
            json={
                'context': {'client': {'clientName': 'WEB', 'clientVersion': '2.20240101'}},
                'query': title,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            contents = (data.get('contents', {})
                        .get('twoColumnSearchResultsRenderer', {})
                        .get('primaryContents', {})
                        .get('sectionListRenderer', {})
                        .get('contents', []))
            for section in contents:
                for item in section.get('itemSectionRenderer', {}).get('contents', []):
                    vr = item.get('videoRenderer')
                    if vr and vr.get('videoId'):
                        thumb_url = f'https://img.youtube.com/vi/{vr["videoId"]}/hqdefault.jpg'
                        _yt_thumb_cache[title] = {'url': thumb_url, 'ts': _time.time()}
                        return thumb_url
        # No result — cache to avoid re-querying
        _yt_thumb_cache[title] = {'url': None, 'ts': _time.time()}
    except Exception as e:
        logger.debug(f"YouTube InnerTube search failed for '{title}': {e}")

    return None


def _query_adb_activity(device: dict) -> dict | None:
    """Query an Android TV / Google TV device for now-playing info via ADB.

    Returns an activity dict compatible with the sessions endpoint, or None.
    """
    device_ip = device.get('host', '')
    adb_port = device.get('connectPort') or 5555
    if not device_ip:
        return None

    try:
        import re as _re

        # Validate device_ip to prevent command injection
        import re as _re_ip
        if not _re_ip.match(r'^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$', device_ip):
            logger.warning(f"Invalid device_ip rejected: {device_ip!r}")
            return None

        # Quick TCP check — skip unreachable devices instead of letting ADB hang
        import socket as _socket
        _sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        _sock.settimeout(1.5)
        try:
            _sock.connect((device_ip, int(adb_port)))
            _sock.close()
        except (OSError, _socket.timeout):
            _sock.close()
            return None

        # Ensure ADB is connected (idempotent — instant if already connected)
        subprocess.run(
            ['adb', 'connect', f'{device_ip}:{adb_port}'],
            capture_output=True, text=True, timeout=5,
        )

        # Get current foreground app
        app_result = subprocess.run(
            ['adb', '-s', f'{device_ip}:{adb_port}', 'shell', 'dumpsys', 'window'],
            capture_output=True, text=True, timeout=5,
        )

        current_package = ''
        current_app = 'Unknown'
        _app_names = {
            'com.plexapp.android': 'Plex',
            'com.google.android.youtube.tv': 'YouTube',
            'com.netflix.ninja': 'Netflix',
            'com.amazon.avod.thirdpartyclient': 'Prime Video',
            'com.hulu.plus': 'Hulu',
            'com.disney.disneyplus': 'Disney+',
            'com.hbo.hbonow': 'HBO Max',
            'tv.twitch.android.app': 'Twitch',
            'com.spotify.tv.android': 'Spotify',
            'ar.tvplayer.tv': 'TiViMate',
        }
        _skip_packages = {
            'com.google.android.tvlauncher',
            'com.nvidia.shield.apps',
            'com.google.android.apps.tv.launcherx',
        }

        if app_result.returncode == 0:
            # Parse mCurrentFocus or mFocusedApp for the actual foreground package
            focus_match = _re.search(r'mCurrentFocus=.*?([a-z][a-z0-9\.]*)/[a-zA-Z0-9\.]+', app_result.stdout)
            if not focus_match:
                focus_match = _re.search(r'mFocusedApp=.*?([a-z][a-z0-9\.]*)/[a-zA-Z0-9\.]+', app_result.stdout)
            if focus_match:
                current_package = focus_match.group(1)
                current_app = _app_names.get(current_package, current_package)

        # Skip if on home screen / launcher
        if current_package in _skip_packages or not current_package:
            return None

        # Note: Plex is NOT skipped here — it serves as a fallback when
        # the Plex sessions API doesn't report the session (e.g. after
        # force-stop + relaunch).  ADB activity is only queried for rooms
        # that have no matched Plex session, so there's no duplication.

        # Get media session info
        media_cmd = f'adb -s {device_ip}:{adb_port} shell dumpsys media_session'
        media_result = subprocess.run(media_cmd, shell=True, capture_output=True, text=True, timeout=5)

        state = 'stopped'
        title = None
        position = 0
        duration = 0
        is_active = False

        if media_result.returncode == 0:
            lines = media_result.stdout.split('\n')
            for i, line in enumerate(lines):
                if 'package=' not in line or i == 0:
                    continue
                pkg_match = _re.search(r'package=([a-z0-9\.]+)', line)
                session_pkg = pkg_match.group(1) if pkg_match else None
                if not session_pkg:
                    continue

                # Check active=true
                session_active = False
                for j in range(i, min(len(lines), i + 15)):
                    if 'active=true' in lines[j]:
                        session_active = True
                        break

                if not session_active or session_pkg in _skip_packages:
                    continue

                app_name = _app_names.get(session_pkg, session_pkg)
                if app_name != current_app and session_pkg != current_package:
                    continue  # only care about the foreground app

                for k in range(i, min(len(lines), i + 30)):
                    if 'state=PlaybackState' in lines[k]:
                        sm = _re.search(r'state=(\d+)', lines[k])
                        if sm:
                            sc = int(sm.group(1))
                            if sc == 3:
                                state = 'playing'
                                is_active = True
                            elif sc == 2:
                                state = 'paused'
                                is_active = True
                        pm = _re.search(r'position=(\d+)', lines[k])
                        if pm:
                            position = int(pm.group(1))
                        bm = _re.search(r'buffered position=(\d+)', lines[k])
                        if bm and int(bm.group(1)) > 0:
                            duration = int(bm.group(1))

                    if 'metadata:' in lines[k] and 'description=' in lines[k]:
                        dm = _re.search(r'description=([^,]+)', lines[k])
                        if dm:
                            t = dm.group(1).strip()
                            if t and t != 'null':
                                title = t
                break  # first active match

            # Fallback: audio playback section (e.g. TiViMate)
            if not is_active and current_package:
                audio_section = False
                for line in lines:
                    if 'Audio playback' in line:
                        audio_section = True
                    if audio_section and f'packages={current_package}' in line:
                        is_active = True
                        state = 'playing'
                        if current_package == 'ar.tvplayer.tv':
                            title = 'Watching Live TV'
                        elif not title:
                            title = f'Playing in {current_app}'
                        break

        if not is_active:
            return None

        # Artwork lookup per app
        thumb = None
        art = None
        if current_package == 'com.google.android.youtube.tv' and is_active and title:
            thumb = _get_youtube_thumbnail(title, device_ip, adb_port)
        elif is_active and title and title not in (f'Playing in {current_app}',):
            # For Plex, Kodi, and other media apps detected via ADB —
            # look up artwork from Plex library / TMDb by title
            artwork = _lookup_kodi_artwork(title)
            thumb = artwork.get('thumb')
            art = artwork.get('art')

        # Sanitize duration — buffered position is sometimes smaller than
        # the actual playback position (e.g. Plex), making it unusable.
        if duration > 0 and duration < position:
            duration = 0

        progress_pct = round((position / duration) * 100) if duration > 0 else 0
        return {
            'appName': current_app,
            'title': title,
            'state': state,
            'position': position,
            'duration': duration,
            'progressPercent': progress_pct,
            'thumb': thumb,
            'art': art,
            'deviceId': device.get('id', ''),
        }
    except Exception as e:
        logger.debug(f"ADB activity query failed for {device_ip}:{adb_port}: {e}")
        return None


_sessions_cache: dict = {}  # {'data': response_dict, 'ts': float}
_SESSIONS_CACHE_TTL = 1     # 1-second cache — prevents duplicate Plex API calls while keeping updates snappy

@app.route('/api/plex/sessions/active')
def plex_active_sessions():
    """Return rooms with matched Plex sessions for Now Playing / RoomsPage.

    Response: ``{rooms: [{roomId, roomName, deviceCount, session, activity}]}``
    Sessions are matched to rooms by comparing the Plex Player address/machineIdentifier
    with the room's host/ip/plex_machine_id.
    """
    try:
        # Return cached result if fresh (prevents N devices * 1req/5s all hitting Plex)
        force = request.args.get('force') == '1'
        cached = _sessions_cache.get('data')
        if not force and cached and (time.time() - cached['ts']) < _SESSIONS_CACHE_TTL:
            return jsonify(cached['result'])
        plex_url = settings.get('plex.url')
        plex_token = settings.get('plex.token')
        user = _get_current_user()
        if user and not user.get('is_admin'):
            _us = user_auth.get_user_settings(user['id']) or {}
            configured_rooms = _us.get('rooms') or settings.get('devices.rooms') or []
        else:
            configured_rooms = settings.get('devices.rooms') or []

        # -- Parse active Plex sessions --------------------------------
        plex_sessions = []
        if plex_url and plex_token:
            try:
                sessions_url = f"{plex_url}/status/sessions"
                response = requests.get(sessions_url, params={'X-Plex-Token': plex_token}, timeout=5)
                if response.status_code == 200:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(response.content)
                    for video in root.findall('.//Video'):
                        player = video.find('.//Player')
                        state = player.get('state') if player is not None else 'stopped'
                        if state in ('playing', 'paused'):
                            user_el = video.find('.//User')
                            sd = {
                                'ratingKey': video.get('ratingKey'),
                                'title': video.get('title', ''),
                                'year': video.get('year', ''),
                                'type': video.get('type', ''),
                                'state': state,
                                'viewOffset': int(video.get('viewOffset', 0)),
                                'duration': int(video.get('duration', 0)),
                                'thumb': video.get('thumb', ''),
                                'art': video.get('art', ''),
                                'contentRating': video.get('contentRating', ''),
                                'userName': user_el.get('title', '') if user_el is not None else '',
                                'playerName': player.get('title', '') if player is not None else '',
                                '_playerAddress': player.get('address', '') if player is not None else '',
                                '_playerMachineId': player.get('machineIdentifier', '') if player is not None else '',
                                '_userId': user_el.get('id', '') if user_el is not None else '',
                            }
                            if video.get('grandparentTitle'):
                                sd['grandparentTitle'] = video.get('grandparentTitle', '')
                                sd['parentIndex'] = video.get('parentIndex', '')
                                sd['index'] = video.get('index', '')
                                sd['grandparentThumb'] = video.get('grandparentThumb', '')
                            plex_sessions.append(sd)
            except Exception as sess_err:
                logger.warning(f"Failed to fetch Plex sessions: {sess_err}")

        # -- Match sessions to rooms -----------------------------------
        used_sessions = set()
        rooms_out = []
        kodi_tasks = []  # (room_index_in_rooms_out, device, service)
        adb_tasks = []   # (room_index_in_rooms_out, device)
        for idx, room in enumerate(configured_rooms):
            if not isinstance(room, dict):
                continue
            room_id = room.get('id', f'room-{idx}')
            room_name = room.get('name', f'Room {idx + 1}')
            # Collect ALL device IPs and plex_machine_ids from the room
            # (a room may have multiple devices, e.g. a streaming box and a TV)
            room_ips = set()
            room_machine_ids = set()
            room_flat_ip = room.get('ip') or room.get('host') or ''
            if room_flat_ip:
                room_ips.add(room_flat_ip)
            room_flat_mid = room.get('plex_machine_id', '')
            if room_flat_mid:
                room_machine_ids.add(room_flat_mid)
            if isinstance(room.get('devices'), list):
                for dev in room['devices']:
                    if not isinstance(dev, dict):
                        continue
                    if dev.get('host'):
                        room_ips.add(dev['host'])
                    for svc in (dev.get('services') or []):
                        if isinstance(svc, dict) and svc.get('plex_machine_id'):
                            room_machine_ids.add(svc['plex_machine_id'])

            matched_session = None
            for si, sess in enumerate(plex_sessions):
                if si in used_sessions:
                    continue
                player_addr = sess.get('_playerAddress', '')
                player_mid = sess.get('_playerMachineId', '')
                if (room_machine_ids and player_mid and player_mid in room_machine_ids) or \
                   (room_ips and player_addr and player_addr in room_ips):
                    matched_session = {k: v for k, v in sess.items() if not k.startswith('_')}
                    used_sessions.add(si)
                    break

            # Collect Kodi + ADB devices to query if no Plex session matched
            room_kodi_tasks = []
            room_adb_devs = []
            if not matched_session and isinstance(room.get('devices'), list):
                for dev in room['devices']:
                    if not isinstance(dev, dict):
                        continue
                    for svc in (dev.get('services') or []):
                        if isinstance(svc, dict) and svc.get('type') == 'kodi':
                            room_kodi_tasks.append((dev, svc))
                    # ADB-capable devices (Android TV / Chromecast GTV)
                    if dev.get('platform') in ('android_tv', 'chromecast_gtv') and dev.get('host'):
                        room_adb_devs.append(dev)

            ri = len(rooms_out)
            rooms_out.append({
                'roomId': room_id,
                'roomName': room_name,
                'deviceCount': 1,
                'session': matched_session,
                'activity': None,
            })
            for dev, svc in room_kodi_tasks:
                kodi_tasks.append((ri, dev, svc))
            for dev in room_adb_devs:
                adb_tasks.append((ri, dev))

        # -- Query all Kodi + ADB devices in parallel -------------------------
        all_tasks = len(kodi_tasks) + len(adb_tasks)
        if all_tasks:
            with ThreadPoolExecutor(max_workers=max(all_tasks, 1)) as pool:
                future_to_ri = {}
                for ri, dev, svc in kodi_tasks:
                    future_to_ri[pool.submit(_query_kodi_activity, dev, svc)] = ri
                for ri, dev in adb_tasks:
                    future_to_ri[pool.submit(_query_adb_activity, dev)] = ri
                for future in as_completed(future_to_ri):
                    ri = future_to_ri[future]
                    if rooms_out[ri]['activity']:
                        continue  # already got result from another device in this room
                    try:
                        result = future.result()
                        if result:
                            rooms_out[ri]['activity'] = result
                    except Exception:
                        pass

        # Collect unmatched sessions from other users (Friends & Family)
        owner_id = str(config.PLEX_ACCOUNT_ID) if hasattr(config, 'PLEX_ACCOUNT_ID') else '1'
        unmatched = []
        for si, sess in enumerate(plex_sessions):
            if si not in used_sessions and sess.get('_userId') != owner_id:
                unmatched.append({k: v for k, v in sess.items() if not k.startswith('_')})

        result = {'rooms': rooms_out, 'unmatched': unmatched}
        _sessions_cache['data'] = {'result': result, 'ts': time.time()}
        resp = jsonify(result)
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    except Exception as e:
        logger.error(f"Error fetching active sessions: {e}")
        resp = jsonify({'rooms': []})
        resp.headers['Cache-Control'] = 'no-store'
        return resp, 200


# Legacy image proxy — prefer /api/img/plex for new code
# Playback Control Endpoints for Now Playing Modal
@app.route('/api/plex/playback/control', methods=['POST'])
def plex_playback_control():
    """Send play/pause command to Shield Plex client."""
    try:
        data = request.get_json() or {}
        client_ip = data.get('client_ip')
        command = data.get('command')  # 'play' or 'pause'

        if not client_ip or not command:
            return jsonify({'error': 'client_ip and command are required'}), 400

        plex_token = settings.get('plex.token')
        client_port = settings.get('plex.client_port', 32500)

        # Map command to Plex endpoint
        endpoint = 'play' if command == 'play' else 'pause'
        url = f"http://{client_ip}:{client_port}/player/playback/{endpoint}?X-Plex-Token={plex_token}"

        response = requests.get(url, timeout=5)
        logger.info(f"Playback control {command} sent to {client_ip}: {response.status_code}")

        return jsonify({'success': True, 'command': command})

    except Exception as e:
        logger.error(f"Error sending playback control: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/plex/playback/stop', methods=['POST'])
def plex_playback_stop():
    """Stop playback on Shield Plex client."""
    try:
        data = request.get_json() or {}
        client_ip = data.get('client_ip')

        if not client_ip:
            return jsonify({'error': 'client_ip is required'}), 400

        plex_token = settings.get('plex.token')
        client_port = settings.get('plex.client_port', 32500)

        url = f"http://{client_ip}:{client_port}/player/playback/stop?X-Plex-Token={plex_token}"

        response = requests.get(url, timeout=5)
        logger.info(f"Playback stop sent to {client_ip}: {response.status_code}")

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Error stopping playback: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/plex/playback/seek', methods=['POST'])
def plex_playback_seek():
    """Seek forward/backward by offset on Shield Plex client."""
    try:
        data = request.get_json() or {}
        client_ip = data.get('client_ip')
        offset_seconds = data.get('offset_seconds', 0)

        if not client_ip:
            return jsonify({'error': 'client_ip is required'}), 400

        plex_token = settings.get('plex.token')
        client_port = settings.get('plex.client_port', 32500)

        # Use skipNext for forward, skipPrevious for backward (relative seek)
        # For now, use stepForward/stepBack which is 30 seconds
        if offset_seconds > 0:
            url = f"http://{client_ip}:{client_port}/player/playback/stepForward?X-Plex-Token={plex_token}"
        else:
            url = f"http://{client_ip}:{client_port}/player/playback/stepBack?X-Plex-Token={plex_token}"

        response = requests.get(url, timeout=5)
        logger.info(f"Playback seek {offset_seconds}s sent to {client_ip}: {response.status_code}")

        return jsonify({'success': True, 'offset': offset_seconds})

    except Exception as e:
        logger.error(f"Error seeking playback: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/plex/playback/seekTo', methods=['POST'])
def plex_playback_seek_to():
    """Seek to specific position on Shield Plex client."""
    try:
        data = request.get_json() or {}
        client_ip = data.get('client_ip')
        position_ms = data.get('position_ms', 0)

        if not client_ip:
            return jsonify({'error': 'client_ip is required'}), 400

        plex_token = settings.get('plex.token')
        client_port = settings.get('plex.client_port', 32500)

        url = f"http://{client_ip}:{client_port}/player/playback/seekTo?offset={position_ms}&X-Plex-Token={plex_token}"

        response = requests.get(url, timeout=5)
        logger.info(f"Playback seekTo {position_ms}ms sent to {client_ip}: {response.status_code}")

        return jsonify({'success': True, 'position_ms': position_ms})

    except Exception as e:
        logger.error(f"Error seeking to position: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/plex/playback/subtitles', methods=['POST'])
def plex_playback_subtitles():
    """Toggle subtitles using ADB KEYCODE_CAPTIONS."""
    try:
        data = request.get_json() or {}
        client_ip = data.get('client_ip')
        room_id = data.get('room_id')

        # Resolve ADB port from room or fall back to default
        adb_port = settings.get_default_adb_port()
        if room_id:
            room = settings.get_room_by_id(room_id)
            if room:
                client_ip = client_ip or room.get('host', '')
                adb_port = room.get('connectPort', adb_port)

        if not client_ip:
            return jsonify({'error': 'client_ip or room_id is required'}), 400

        import subprocess
        import os

        env = os.environ.copy()
        env['HOME'] = '/app/home'

        subprocess.run(['adb', 'connect', f'{client_ip}:{adb_port}'], env=env, timeout=5)
        result = subprocess.run(
            ['adb', '-s', f'{client_ip}:{adb_port}', 'shell', 'input', 'keyevent', '175'],
            env=env,
            capture_output=True,
            text=True,
            timeout=5
        )

        logger.info(f"Subtitle toggle (KEYCODE_CAPTIONS) sent to {client_ip}:{adb_port}: {result.returncode}")

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Error toggling subtitles: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/plex/resume_info/<rating_key>')
def plex_resume_info(rating_key):
    """Get resume information for a given rating key via Plex HTTP API."""
    try:
        meta = plex_service.get_metadata(int(rating_key))
        offset_ms = meta.get('viewOffset', 0) if meta else 0

        # Convert to human-readable format
        offset_seconds = offset_ms / 1000
        hours = int(offset_seconds // 3600)
        minutes = int((offset_seconds % 3600) // 60)
        seconds = int(offset_seconds % 60)

        if hours > 0:
            time_string = f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            time_string = f"{minutes}:{seconds:02d}"

        return jsonify({
            'has_resume_point': offset_ms > 0,
            'offset_ms': offset_ms,
            'offset_seconds': int(offset_seconds),
            'time_string': time_string,
            'rating_key': rating_key
        })

    except Exception as e:
        logger.error(f"Error getting resume info: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/plex/lookup')
def plex_movie_lookup():
    """Fast single-movie ownership check using Plex search (no full index build).

    Query params: title (required), year (optional), tmdb_id (optional), imdb_id (optional)
    Returns: { owned, rating_key, libraries, library_keys }

    Strategy:
      1. Try find_movie_copies (ID-based matching) if tmdb_id or imdb_id provided
      2. Fallback: search Plex by title, match by year (±1) — handles libraries
         without external IDs stored
    """
    title = request.args.get('title', '').strip()
    if not title:
        return jsonify({'owned': False, 'libraries': [], 'library_keys': {}})
    year = request.args.get('year', type=int)
    tmdb_id = request.args.get('tmdb_id', type=int)
    imdb_id = request.args.get('imdb_id', '')

    not_found = {'owned': False, 'libraries': [], 'library_keys': {}}

    try:
        # Strategy 1: ID-based match (most reliable when IDs are available)
        if tmdb_id or imdb_id:
            copies = plex_service.find_movie_copies(
                title,
                tmdb_id=tmdb_id if tmdb_id else None,
                imdb_id=imdb_id if imdb_id else None,
            )
            if copies:
                return jsonify(_copies_to_response(copies))

        # Strategy 2: Title + year search fallback
        sections = plex_service.get_sections_lean()
        movie_sections = [s for s in sections if s['type'] == 'movie']

        libraries = []
        library_keys = {}
        first_rk = None
        title_lower = title.lower().strip()

        for section in movie_sections:
            results = plex_service.search_library(
                title, section_id=section['id'], media_type=1, limit=5,
            )
            for item in results:
                item_title = (item.get('title') or '').lower().strip()
                item_year = item.get('year')
                rk = item.get('ratingKey') or item.get('rating_key')

                # Exact title match + year within ±1 (or no year to compare)
                if item_title == title_lower and rk:
                    if not year or not item_year or abs(item_year - year) <= 1:
                        if first_rk is None:
                            first_rk = rk
                        lib_name = section['title']
                        if lib_name not in library_keys:
                            libraries.append(lib_name)
                            library_keys[lib_name] = rk

        if first_rk:
            return jsonify({
                'owned': True,
                'rating_key': first_rk,
                'libraries': libraries,
                'library_keys': library_keys,
            })

        return jsonify(not_found)
    except Exception as e:
        logger.warning(f"plex_movie_lookup failed: {e}")
        return jsonify(not_found)


def _copies_to_response(copies):
    """Convert find_movie_copies result to API response dict."""
    libraries = []
    library_keys = {}
    first_rk = None
    for copy in copies:
        lib_name = copy.get('library_name', '')
        rk = copy.get('ratingKey') or copy.get('rating_key')
        if first_rk is None and rk:
            first_rk = rk
        if lib_name and rk and lib_name not in library_keys:
            libraries.append(lib_name)
            library_keys[lib_name] = rk
    return {
        'owned': True,
        'rating_key': first_rk,
        'libraries': libraries,
        'library_keys': library_keys,
    }


@app.route('/api/plex/movie/<int:rating_key>')
def plex_movie_metadata(rating_key):
    """Get movie metadata from Plex via HTTP API (no CIFS/SQLite dependency)."""
    try:
        meta = plex_service.get_metadata(rating_key)
        if not meta:
            return jsonify({'error': 'Movie not found'}), 404

        # Discard a Plex-supplied tmdb_id that points to the wrong media type
        # (e.g. a bad agent match that gave a movie a TV show's id). Without
        # this, the modal would load TMDb details for an unrelated title.
        plex_tmdb_id = meta.get('tmdb_id')
        if plex_tmdb_id and _validate_tmdb_id_type(plex_tmdb_id, 'movie') is False:
            logger.warning(
                'Plex movie ratingKey=%s has tmdb_id=%s that is not a movie on TMDb — discarding',
                rating_key, plex_tmdb_id,
            )
            meta['tmdb_id'] = None

        # Build cast list matching frontend expectations
        raw_cast = meta.get('cast', [])
        cast = []
        for c in raw_cast[:15]:
            entry = {
                'name': c.get('tag', ''),
                'tag': c.get('tag', ''),
                'character': c.get('role', ''),
                'role': c.get('role', ''),
            }
            # Proxy actor thumb through image proxy (never expose token)
            thumb = c.get('thumb')
            if thumb:
                if thumb.startswith('/'):
                    entry['thumb'] = f"/api/img/plex?url={thumb}"
                elif thumb.startswith('http'):
                    entry['thumb'] = thumb
            cast.append(entry)

        # Build directors list
        raw_directors = meta.get('directors', [])
        directors = [{'name': d.get('name', '')} for d in raw_directors]

        # Build genres as list of {tag: ...} dicts
        raw_genres = meta.get('genres', [])
        genres = [{'tag': g} if isinstance(g, str) else g for g in raw_genres]

        tmdb_id = meta.get('tmdb_id')
        imdb_id = meta.get('imdb_id')

        # Find copies across libraries (HTTP API based)
        libraries = []
        library_keys = {}
        if tmdb_id or imdb_id:
            try:
                copies = plex_service.find_movie_copies(
                    meta.get('title', ''),
                    tmdb_id=int(tmdb_id) if tmdb_id else None,
                    imdb_id=imdb_id,
                )
                for copy in copies:
                    lib_name = copy.get('library_name', '')
                    rk = copy.get('ratingKey') or copy.get('rating_key')
                    if lib_name and rk and lib_name not in library_keys:
                        libraries.append(lib_name)
                        library_keys[lib_name] = rk
            except Exception as e:
                logger.warning(f"Library copies search failed: {e}")

        # Build proxy URLs for images
        clearlogo_url = None
        backdrop_url = None
        poster_url = None

        if meta.get('clearLogoPath'):
            clearlogo_url = f"/api/img/plex?url={meta['clearLogoPath']}"
        if meta.get('artPath'):
            backdrop_url = f"/api/img/plex?url={meta['artPath']}"
        if meta.get('thumbPath'):
            poster_url = f"/api/img/plex?url={meta['thumbPath']}"

        result = {
            'rating_key': rating_key,
            'title': meta.get('title', ''),
            'year': meta.get('year'),
            'summary': meta.get('summary', ''),
            'content_rating': meta.get('content_rating', meta.get('contentRating', '')),
            'studio': meta.get('studio', ''),
            'rating': meta.get('rating'),
            'duration': meta.get('duration', meta.get('duration_ms', 0)),
            'cast': cast,
            'genres': genres,
            'directors': directors,
            'tmdb_id': tmdb_id,
            'imdb_id': imdb_id,
            'libraries': libraries if libraries else ['Library'],
            'library_keys': library_keys if library_keys else {},
            'library_name': libraries[0] if libraries else 'Library',
            'clearlogo_url': clearlogo_url,
            'backdrop_url': backdrop_url,
            'poster_url': poster_url,
        }

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting movie metadata for rating_key {rating_key}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/plex/file-info/<int:rating_key>')
def plex_file_info(rating_key):
    """File Info sheet (movie modal ⓘ button): versions/parts/streams + NAS path.

    Requires a signed-in session — it exposes real NAS file paths.
    Path mapping lives in app/plex_file_info.py (PLEX_PATH_MAP override).
    """
    err = _require_auth()
    if err:
        return err
    from app.plex_file_info import build_file_info
    try:
        plex = get_plex_for_request()
        info = build_file_info(plex, rating_key, settings,
                               apply_path_map=plex is plex_service)
        if not info:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(info)
    except Exception as e:
        logger.error(f"Error building file info for rating_key {rating_key}: {e}")
        return jsonify({'error': 'Failed to load file info'}), 500


# ---------------------------------------------------------------------------
# Fix Match — owner-only Plex rematch from the movie/show popup (app/plex_match.py)
# ---------------------------------------------------------------------------

def _fix_match_invalidate(plex, rating_key, items):
    """Drop every Luma cache that could still show the old match for this item."""
    import app.plex_service as _ps_mod
    try:
        with _ps_mod._metadata_cache_lock:
            _ps_mod._metadata_cache.pop(rating_key, None)
    except Exception as exc:
        logger.warning(f"Fix Match: metadata cache clear failed: {exc}")
    for key in (rating_key, str(rating_key)):
        _plex_ratings_cache.pop(key, None)
        _ultrablur_cache.pop(key, None)
    _ultrablur_guid_cache.clear()
    _ownership_cache.clear()
    try:
        from app import rt_top_reviews as _rt
        with _rt._lock:
            for it in items:
                if not it or not it.get('title'):
                    continue
                mt = 'tv' if it.get('type') == 'show' else 'movie'
                for y in (it.get('year'), None):
                    _rt._cache.pop(f"{mt}|{it['title'].strip().lower()}|{y or ''}", None)
    except Exception as exc:
        logger.warning(f"Fix Match: RT cache clear failed: {exc}")

    def _rebuild():
        try:
            plex.build_ownership_index(force=True)
        except Exception as exc:
            logger.warning(f"Fix Match: ownership rebuild failed: {exc}")
    threading.Thread(target=_rebuild, name='fixmatch-ownership', daemon=True).start()
    try:
        _vibe_refresh_async(plex, getattr(plex, 'plex_url', '') or 'global')
    except Exception as exc:
        logger.warning(f"Fix Match: vibe refresh failed: {exc}")


def _fix_match_followup(plex, rating_key, out):
    """Plex finishes the external-id/artwork refresh a few seconds after the match call.
    Re-check then, and invalidate + rebuild again so ownership/detail never keep the old tmdb id."""
    def _later():
        from app import plex_match as _pm
        for delay in (10, 45):
            time.sleep(delay)
            try:
                cur = _pm.get_item(plex, rating_key)
            except Exception as exc:
                logger.warning(f"Fix Match: follow-up read failed: {exc}")
                continue
            _fix_match_invalidate(plex, rating_key, [out['before'], out['after'], cur])
            logger.info(f"Fix Match: follow-up {rating_key} now {cur['title']!r} ({cur['year']}) tmdb {cur.get('tmdb_id')}")
    threading.Thread(target=_later, name='fixmatch-followup', daemon=True).start()


def _fix_match_error(exc):
    from app.plex_match import MatchError
    if isinstance(exc, MatchError):
        return jsonify({'error': str(exc)}), exc.status
    logger.error(f"Fix Match error: {exc}")
    return jsonify({'error': 'Something went wrong'}), 500


@app.route('/api/plex/match/<int:rating_key>/search')
def plex_match_search(rating_key):
    err = _require_admin()
    if err:
        return err
    from app import plex_match
    title = (request.args.get('title') or '').strip()[:200]
    try:
        year = int(request.args.get('year', ''))
    except ValueError:
        year = None
    try:
        resp = jsonify(plex_match.search(get_plex_for_request(), rating_key, title, year))
        resp.headers['Cache-Control'] = 'no-store'
        return resp
    except Exception as exc:
        return _fix_match_error(exc)


@app.route('/api/plex/match/<int:rating_key>/apply', methods=['POST'])
def plex_match_apply(rating_key):
    err = _require_admin()
    if err:
        return err
    from app import plex_match
    data = request.get_json(silent=True) or {}
    guid = str(data.get('guid') or '').strip()
    name = str(data.get('name') or '').strip()[:300]
    try:
        year = int(data.get('year')) if data.get('year') not in (None, '') else None
    except (TypeError, ValueError):
        year = None
    plex = get_plex_for_request()
    try:
        out = plex_match.apply(plex, rating_key, guid, name, year)
    except Exception as exc:
        return _fix_match_error(exc)
    _fix_match_invalidate(plex, rating_key, [out['before'], out['after']])
    _fix_match_followup(plex, rating_key, out)
    a = out['after']
    return jsonify({'ok': True, 'rating_key': rating_key, 'type': a['type'], 'title': a['title'],
                    'year': a['year'], 'guid': a['guid'], 'tmdb_id': a['tmdb_id'],
                    'changed': out['before']['guid'] != a['guid']})


@app.route('/api/plex/match/<int:rating_key>/unmatch', methods=['POST'])
def plex_match_unmatch(rating_key):
    err = _require_admin()
    if err:
        return err
    from app import plex_match
    plex = get_plex_for_request()
    try:
        out = plex_match.unmatch(plex, rating_key)
    except Exception as exc:
        return _fix_match_error(exc)
    _fix_match_invalidate(plex, rating_key, [out['before'], out['after']])
    _fix_match_followup(plex, rating_key, out)
    a = out['after']
    return jsonify({'ok': True, 'rating_key': rating_key, 'type': a['type'], 'title': a['title'],
                    'year': a['year'], 'guid': a['guid'], 'tmdb_id': a['tmdb_id']})


@app.route('/api/reviews/top')
def top_reviews():
    """Rotten Tomatoes reviews for the movie modal's "Ratings & Reviews" row.

    Query: title (required), year, media_type=movie|tv.
    Critic reviews (Top Critics first); falls back to audience reviews
    (verified first) when RT has no critic reviews. See app/rt_top_reviews.py
    (cached server-side).
    """
    from app.rt_top_reviews import get_top_reviews
    title = (request.args.get('title') or '').strip()
    try:
        year = int(request.args.get('year', ''))
    except ValueError:
        year = None
    media_type = 'tv' if request.args.get('media_type') == 'tv' else 'movie'
    if not title:
        return jsonify({'source': None, 'reviews': [], 'url': None})
    try:
        resp = jsonify(get_top_reviews(title[:200], year, media_type))
        resp.headers['Cache-Control'] = 'private, max-age=600'
        return resp
    except Exception as e:
        logger.error(f"Error fetching top reviews for {title!r} ({year}): {e}")
        return jsonify({'source': None, 'reviews': [], 'url': None})


@app.route('/api/plex/show/<int:rating_key>')
def plex_show_metadata(rating_key):
    """Get show metadata including TVDB ID, TMDB ID, cast, and genres via HTTP API."""
    try:
        meta = plex_service.get_metadata(rating_key)
        if not meta:
            return jsonify({'error': 'Show not found'}), 404

        # get_metadata returns all external IDs from Directory elements
        tvdb_id = meta.get('tvdb_id')
        tmdb_id = meta.get('tmdb_id')
        imdb_id = meta.get('imdb_id')

        # Discard tmdb_id that does not resolve to a TV show on TMDb so
        # downstream artwork/detail fetches don't pick up a movie entry.
        if tmdb_id and _validate_tmdb_id_type(tmdb_id, 'tv') is False:
            logger.warning(
                'Plex show ratingKey=%s has tmdb_id=%s that is not a TV show on TMDb — discarding',
                rating_key, tmdb_id,
            )
            tmdb_id = None

        # Build cast list
        cast = []
        for c in meta.get('cast', [])[:15]:
            entry = {
                'name': c.get('tag', ''),
                'tag': c.get('tag', ''),
                'role': c.get('role', ''),
                'character': c.get('role', ''),
                'thumb': None,
            }
            thumb = c.get('thumb')
            if thumb:
                if thumb.startswith('/'):
                    entry['thumb'] = f"/api/img/plex?url={thumb}"
                elif thumb.startswith('http'):
                    entry['thumb'] = thumb
            cast.append(entry)

        # Build genres
        raw_genres = meta.get('genres', [])
        genres = [{'tag': g} if isinstance(g, str) else g for g in raw_genres]

        # Season count via HTTP API
        seasons = plex_service.get_show_children(rating_key)
        season_count = len(seasons)

        result = {
            'rating_key': rating_key,
            'title': meta.get('title', ''),
            'name': meta.get('title', ''),
            'year': meta.get('year'),
            'summary': meta.get('summary', ''),
            'content_rating': meta.get('content_rating', meta.get('contentRating', '')),
            'studio': meta.get('studio', ''),
            'tvdb_id': tvdb_id,
            'tmdb_id': tmdb_id,
            'cast': cast,
            'genres': genres,
            'number_of_seasons': season_count,
            'clearlogo_url': f"/api/img/plex?url=/library/metadata/{rating_key}/clearLogo" if meta.get('clearLogoPath') else None,
            'backdrop_url': f"/api/img/plex?url={meta['artPath']}" if meta.get('artPath') else None,
            'poster_url': f"/api/img/plex?url={meta['thumbPath']}" if meta.get('thumbPath') else None,
        }

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting show metadata for rating_key {rating_key}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/plex/episode_libraries/<int:rating_key>')
def plex_episode_libraries(rating_key):
    """Get all libraries where a specific episode exists via HTTP API."""
    try:
        meta = plex_service.get_metadata(rating_key)
        if not meta:
            return jsonify({'owned': False, 'libraries': [], 'library_keys': {}})

        # For episodes, get show title and search across TV libraries
        show_title = meta.get('show_title', meta.get('title', ''))
        show_rk = meta.get('show_rating_key')

        if show_rk:
            # Use show metadata to find copies
            show_meta = plex_service.get_metadata(show_rk)
            if show_meta:
                show_title = show_meta.get('title', show_title)
                copies = plex_service.find_show_copies(
                    show_title,
                    tmdb_id=show_meta.get('tmdb_id'),
                    tvdb_id=show_meta.get('tvdb_id'),
                )
                library_keys = {}
                for copy in copies:
                    lib_name = copy.get('library_name', '')
                    rk = copy.get('ratingKey') or copy.get('rating_key')
                    if lib_name and rk and lib_name not in library_keys:
                        library_keys[lib_name] = rating_key  # Use episode rating key
                if library_keys:
                    return jsonify({
                        'owned': True,
                        'libraries': list(library_keys.keys()),
                        'library_keys': library_keys
                    })

        return jsonify({'owned': True, 'libraries': ['Library'], 'library_keys': {'Library': rating_key}})

    except Exception as e:
        logger.error(f"Error getting episode libraries for rating_key {rating_key}: {e}")
        return jsonify({'error': str(e), 'owned': False}), 500

@app.route('/api/plex/continue_watching/cached')
def plex_continue_watching_cached():
    """Get cached continue watching items for ultra-fast loading."""
    try:
        limit = request.args.get('limit', 25, type=int)

        # Check if cache needs updating (but don't block)
        if library_cache.needs_update('continue_watching'):
            # Start update in background
            import threading
            threading.Thread(target=update_continue_watching_cache, daemon=True).start()

        # Return cached items immediately
        items = library_cache.get_continue_watching(limit=limit)

        return jsonify({
            'success': True,
            'count': len(items),
            'items': items,
            'from_cache': True,
            'cache_info': library_cache.get_cache_info().get('continue_watching', {})
        })

    except Exception as e:
        logger.error(f"Error getting cached continue watching: {e}")
        # Fall back to non-cached version
        return plex_continue_watching()

@app.route('/api/plex/continue_watching')
def plex_continue_watching():
    """Get continue watching items (movies and episodes in progress)."""
    try:
        from app.plex_api_on_deck import get_plex_api_on_deck

        limit = request.args.get('limit', 25, type=int)

        # Use Plex's actual On Deck API for perfect accuracy
        items = get_plex_api_on_deck(limit=limit)
        logger.info(f"Retrieved {len(items)} continue watching items from Plex API")

        # Poster URLs are already set in the API response, no need to rebuild them

        return jsonify({
            'success': True,
            'count': len(items),
            'items': items
        })

    except Exception as e:
        logger.error(f"Error getting continue watching items: {e}")
        return jsonify({'error': str(e)}), 500


# Recently Watched — Plex history, collapsed to one tile per title.
# Short TTL: this rail is the one place in the app that should visibly react to
# something you finished five minutes ago.
_recently_watched_cache: Dict[str, tuple] = {}
_RECENTLY_WATCHED_TTL = 120


@app.route('/api/plex/recently_watched')
def plex_recently_watched():
    """Recently watched movies and shows, newest first, one tile per title.

    Episodes collapse to their parent show — watching six episodes of one show
    should occupy one slot in the rail, not six.
    """
    try:
        limit = request.args.get('limit', 25, type=int)

        # An owner token sees EVERY shared user's history, so an unfiltered
        # rail puts the rest of the household on your home screen. Default to
        # the same "which Plex account is you" setting the rate prompts use;
        # `?account_id=all` opts back into the whole server.
        account_arg = (request.args.get('account_id') or '').strip().lower()
        if account_arg == 'all':
            account_filter = None
        elif account_arg.isdigit():
            account_filter = int(account_arg)
        else:
            account_filter = _taste_account_id()

        cache_key = f'items:{account_filter if account_filter is not None else "all"}'
        cached = _recently_watched_cache.get(cache_key)
        if cached and time.time() - cached[0] < _RECENTLY_WATCHED_TTL:
            items = cached[1]
            return jsonify({
                'success': True,
                'count': len(items[:limit]),
                'items': items[:limit],
                'from_cache': True,
            })

        plex = get_plex_for_request()
        # Over-fetch: a binge of one show can eat a hundred history rows, and
        # on a shared server most rows belong to other accounts — both shrink
        # 300 rows down to far fewer tiles.
        rows = plex.get_watch_history(limit=600)

        items = []
        seen = set()
        for row in rows:
            if account_filter is not None and row.get('account_id') != account_filter:
                continue
            is_episode = row['type'] == 'episode'
            # Collapse on the show for episodes so a binge is one tile.
            key = ('show', row.get('show_rating_key')) if is_episode else ('movie', row['rating_key'])
            if key[1] is None or key in seen:
                continue
            seen.add(key)

            if is_episode:
                poster_rk = row['show_rating_key']
                season = row.get('season_number')
                episode = row.get('episode_number')
                subtitle_parts = []
                if season and episode:
                    subtitle_parts.append(f"S{season}E{episode}")
                if row.get('title'):
                    subtitle_parts.append(row['title'])
                items.append({
                    'rating_key': row['rating_key'],
                    'metadata_type': 4,
                    'title': row.get('show_title') or row.get('title') or '',
                    'episode_title': row.get('title'),
                    'show_title': row.get('show_title'),
                    'show_rating_key': row['show_rating_key'],
                    'season_number': season,
                    'episode_number': episode,
                    'year': None,
                    'poster_url': f"/api/img/plex?url=/library/metadata/{poster_rk}/thumb&r=2",
                    'subtitle': ' · '.join(subtitle_parts) or None,
                    'viewed_at': row.get('viewed_at'),
                })
            else:
                items.append({
                    'rating_key': row['rating_key'],
                    'metadata_type': 1,
                    'title': row.get('title') or '',
                    'year': row.get('year'),
                    'poster_url': f"/api/img/plex?url=/library/metadata/{row['rating_key']}/thumb&r=2",
                    'subtitle': f"({row['year']})" if row.get('year') else None,
                    'viewed_at': row.get('viewed_at'),
                })

            if len(items) >= 50:
                break

        # History rows carry no year (and for episodes, no show year), so fill
        # it in with one batched metadata call rather than N per-item lookups.
        meta = plex.get_titles_lean([
            it['show_rating_key'] if it['metadata_type'] == 4 else it['rating_key']
            for it in items
        ])
        for it in items:
            key = it['show_rating_key'] if it['metadata_type'] == 4 else it['rating_key']
            info = meta.get(key)
            if not info:
                continue
            if info.get('year'):
                it['year'] = info['year']
                if it['metadata_type'] == 1:
                    it['subtitle'] = f"({info['year']})"
            # Plex's canonical show title beats the history row's copy of it.
            if it['metadata_type'] == 4 and info.get('title'):
                it['title'] = info['title']

        _recently_watched_cache[cache_key] = (time.time(), items)

        return jsonify({
            'success': True,
            'count': len(items[:limit]),
            'items': items[:limit],
            'from_cache': False,
        })

    except Exception as e:
        logger.error(f"Error getting recently watched items: {e}")
        return jsonify({'error': str(e), 'items': []}), 500


# Download tracking endpoints
@app.route('/api/downloads/track', methods=['POST'])
def track_download():
    """Track a new download with TMDB metadata"""
    try:
        from app.auto_rename import auto_renamer

        data = request.get_json() or {}
        torrent_hash = data.get('torrent_hash')
        tmdb_data = data.get('tmdb_data')

        if not torrent_hash or not tmdb_data:
            return jsonify({'error': 'Missing torrent_hash or tmdb_data'}), 400

        # Add to tracking
        auto_renamer.track_download(torrent_hash, tmdb_data)

        return jsonify({'success': True, 'message': 'Download tracked successfully'})

    except Exception as e:
        logger.error(f"Error tracking download: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/downloads/tracking')
def get_tracking_status():
    """Get current tracking status for all downloads"""
    try:
        from app.auto_rename import auto_renamer

        tracking_data = auto_renamer.get_tracking_data()

        # Convert to list format for JSON
        tracked_list = []
        for torrent_hash, download_info in tracking_data.items():
            tracked_list.append({
                'torrent_hash': torrent_hash,
                'title': download_info.title,
                'year': download_info.year,
                'media_type': download_info.media_type,
                'tmdb_id': download_info.tmdb_id,
                'added_timestamp': download_info.added_timestamp
            })

        return jsonify({'tracking': tracked_list})

    except Exception as e:
        logger.error(f"Error getting tracking status: {e}")
        return jsonify({'error': str(e)}), 500

def _parse_torrent_name(name: str, category: str = '') -> dict:
    """Extract title, year, and media_type from a torrent filename."""
    import re
    media_type = 'movie'
    year = ''

    # Strip file extensions (.mp4, .mkv, .avi, etc.)
    name = re.sub(r'\.(mp4|mkv|avi|m4v|ts|wmv|flv|webm)$', '', name, flags=re.IGNORECASE)

    title = name

    ep_match = re.search(r'S(\d{2})E(\d{2})', name, re.IGNORECASE)
    if ep_match or 'tv' in category.lower() or 'sonarr' in category.lower():
        media_type = 'tv'
        show = re.split(r'S\d{2}E\d{2}|S\d{2}(?!E)|[._ ](?:19|20)\d{2}[._ ]', name, flags=re.IGNORECASE)[0]
        title = show.replace('.', ' ').replace('_', ' ').strip()
    else:
        yr = re.search(r'[._ ]((?:19|20)\d{2})[._ ]', name)
        if yr:
            year = yr.group(1)
            title = name[:yr.start()].replace('.', ' ').replace('_', ' ').strip()
        else:
            # Also try year at end of string (no trailing separator)
            yr_end = re.search(r'[._ ]((?:19|20)\d{2})$', name)
            if yr_end:
                year = yr_end.group(1)
                title = name[:yr_end.start()].replace('.', ' ').replace('_', ' ').strip()
            else:
                title = re.split(r'[._ ](?:1080p|720p|2160p|4K|BluRay|WEB|HDTV|REMUX|x264|x265|H\.?264|H\.?265|AAC|DTS)', name, flags=re.IGNORECASE)[0]
                title = title.replace('.', ' ').replace('_', ' ').strip()

    return {'title': title, 'year': year, 'media_type': media_type}


def _parse_season_episode(name: str) -> tuple:
    """Pull (season, episode) from a torrent name. Episode is None for season
    packs. Returns (None, None) when nothing matches."""
    import re
    m = re.search(r'[sS](\d{1,2})[eE](\d{1,2})', name)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'(\d{1,2})x(\d{2})', name)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'[sS](\d{1,2})(?![eE\d])', name)   # season pack, e.g. "Show S02"
    if m:
        return int(m.group(1)), None
    return None, None


_history_cache: Dict[str, tuple] = {}  # key → (timestamp, data)
_HISTORY_CACHE_TTL = 180  # 3 minutes

@app.route('/api/downloads/history')
def get_download_history():
    """Get download history from qBittorrent completed torrents + auto_renamer,
    enriched with TMDb poster/id.  Default limit 30, max 100."""
    try:
        from concurrent.futures import ThreadPoolExecutor
        import requests as req
        from app.auto_rename import auto_renamer

        limit = min(int(request.args.get('limit', 30)), 100)

        # --- Endpoint-level cache (3 min TTL) ---
        cache_key = f'history_{limit}'
        cached = _history_cache.get(cache_key)
        if cached:
            ts, data = cached
            if time.time() - ts < _HISTORY_CACHE_TTL:
                return jsonify(data)

        # --- Build lookup from auto_renamer history (keyed by hash) ---
        renamer_by_hash = {}
        for item in auto_renamer.get_history(limit=100):
            h = (item.get('torrent_hash') or '').lower()
            if h:
                renamer_by_hash[h] = item

        # --- Fetch completed torrents from qBittorrent ---
        qb_items = []
        try:
            session = req.Session()
            login_resp = session.post(
                f"{settings.get('qbittorrent.url')}/api/v2/auth/login",
                data={
                    'username': settings.get('qbittorrent.username'),
                    'password': settings.get('qbittorrent.password'),
                },
                timeout=10,
            )
            if login_resp.status_code == 200 and login_resp.text.strip() == 'Ok.':
                torrents_resp = session.get(
                    f"{settings.get('qbittorrent.url')}/api/v2/torrents/info",
                    timeout=10,
                )
                if torrents_resp.status_code == 200:
                    qb_items = [
                        t for t in torrents_resp.json()
                        if t.get('progress', 0) >= 1.0
                    ]
        except Exception as e:
            logger.warning(f"qBittorrent unavailable for history: {e}")

        # --- Union qB completed torrents with the persistent auto_renamer
        # history. qBittorrent removes torrents once Radarr/Sonarr import them,
        # so qB alone silently drops recently-imported downloads — the
        # auto_renamer log keeps them. Key by hash; the live qB torrent (when
        # present) supplies tracker/category, the persistent record supplies
        # title/metadata. This is why the feed could read "no downloads" even
        # when titles had just finished. ---
        qb_by_hash = {}
        for t in qb_items:
            h = (t.get('hash') or '').lower()
            if h:
                qb_by_hash[h] = t

        def _completion_ts(h):
            t = qb_by_hash.get(h)
            if t:
                return t.get('completion_on', t.get('added_on', 0)) or 0
            return (renamer_by_hash.get(h) or {}).get('completed_timestamp', 0) or 0

        union_hashes = list(qb_by_hash.keys())
        for h in renamer_by_hash:
            if h not in qb_by_hash:
                union_hashes.append(h)
        union_hashes.sort(key=_completion_ts, reverse=True)
        union_hashes = union_hashes[:limit]

        items_meta = []
        for t_hash in union_hashes:
            t = qb_by_hash.get(t_hash)
            renamer = renamer_by_hash.get(t_hash)

            if renamer:
                title = renamer.get('tmdb_title', '')
                year = str(renamer.get('tmdb_year', ''))
                media_type = renamer.get('media_type', 'movie')
                success = renamer.get('success', True)
                error_msg = renamer.get('error_message')
                dest = renamer.get('destination_path', '')
            elif t:
                parsed = _parse_torrent_name(t.get('name', ''), t.get('category', ''))
                title = parsed['title']
                year = parsed['year']
                media_type = parsed['media_type']
                success = True
                error_msg = None
                dest = t.get('save_path', '')
            else:
                continue

            name = (t.get('name', '') if t else '') or (renamer.get('torrent_name', '') if renamer else '')
            # tracker/source come from the live qB torrent's category; a record
            # qB has already removed has no category → fall back by media type.
            if t:
                tracker_name = _normalize_tracker_url(t.get('tracker', ''))
                category = (t.get('category') or '').lower()
                if 'sonarr' in category:
                    source = 'sonarr'
                elif 'radarr' in category:
                    source = 'radarr'
                else:
                    source = 'manual'
            else:
                tracker_name = ''
                source = 'sonarr' if media_type == 'tv' else 'manual'

            season_num, episode_num = (None, None)
            if media_type == 'tv':
                season_num, episode_num = _parse_season_episode(name)
            stored_tmdb = (renamer or {}).get('tmdb_id')
            stored_poster = (renamer or {}).get('poster_path')
            items_meta.append({
                'qb_category': (t.get('category') or '').lower() if t else '',
                'torrent_hash': t_hash,
                'torrent_name': name,
                'title': title,
                'year': year,
                'media_type': media_type,
                'completed_timestamp': _completion_ts(t_hash),
                'success': success,
                'error_message': error_msg,
                'destination_path': dest,
                'tracker': tracker_name,
                'source': source,
                'season': season_num,
                'episode': episode_num,
                'tmdb_id': stored_tmdb,
                'poster_path': stored_poster,
            })

        # --- Resolve TMDb info in parallel (8 workers) ---
        def _resolve_one(item):
            # Prefer art/id persisted at grab/history-write time.
            if item.get('poster_path') and item.get('tmdb_id'):
                return item
            info = _resolve_tmdb_info(
                item['title'], item['year'], item['media_type'],
                tmdb_id=item.get('tmdb_id'),
            )
            return {
                **item,
                'poster_path': item.get('poster_path') or info.get('poster_path'),
                'tmdb_id': item.get('tmdb_id') or info.get('tmdb_id'),
            }

        with ThreadPoolExecutor(max_workers=8) as pool:
            history_enriched = list(pool.map(_resolve_one, items_meta))

        # Who started each download (see app/grab_origin.py).
        from app import grab_origin
        for h, o in zip(history_enriched, grab_origin.resolve(history_enriched, settings)):
            h.update(o)

        # Episode titles come from the Sonarr import webhooks (authoritative),
        # keyed by (series tmdb_id, season, episode).
        ep_title_index = {}
        for n in (_webhook_notifications or []):
            if n.get('media_type') != 'tv':
                continue
            tid, s, e, t_title = (n.get('tmdb_id'), n.get('season_number'),
                                  n.get('episode_number'), n.get('episode_title'))
            if tid and s is not None and e is not None and t_title:
                ep_title_index[(int(tid), int(s), int(e))] = t_title

        def _ep_title(h):
            tid, s, e = h.get('tmdb_id'), h.get('season'), h.get('episode')
            if tid and s is not None and e is not None:
                return ep_title_index.get((int(tid), int(s), int(e)))
            return None

        # Remap keys to match expected frontend format
        history = [{
            'torrent_hash': h['torrent_hash'],
            'torrent_name': h['torrent_name'],
            'tmdb_title': h['title'],
            'tmdb_year': h['year'],
            'media_type': h['media_type'],
            'completed_timestamp': h['completed_timestamp'],
            'success': h['success'],
            'error_message': h['error_message'],
            'destination_path': h['destination_path'],
            'poster_path': h['poster_path'],
            'tmdb_id': h['tmdb_id'],
            'tracker': h['tracker'],
            'source': h['source'],
            'season': h.get('season'),
            'episode': h.get('episode'),
            'episode_title': _ep_title(h),
            'origin': h.get('origin', 'other'),
            'origin_label': h.get('origin_label', 'Other'),
        } for h in history_enriched]

        result = {'history': history}
        _history_cache[cache_key] = (time.time(), result)
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting download history: {e}")
        return jsonify({'error': str(e)}), 500

def _normalize_tracker_url(tracker_url: str) -> str | None:
    """Extract a short tracker name from a qBittorrent tracker URL."""
    if not tracker_url:
        return None
    try:
        from urllib.parse import urlparse
        host = urlparse(tracker_url).hostname or ''
        host = host.lower()
        _map = {
            'privatehd': 'PHD',
            'beyondhd': 'BHD',
            'beyond-hd': 'BHD',
            'iptorrents': 'IPT',
            'morethantv': 'MTV',
            'torrentleech': 'TL',
            'filelist': 'FL',
            'passthepopcorn': 'PTP',
            'broadcasthenet': 'BTN',
            'hdbits': 'HDB',
            'blutopia': 'BLU',
            'aither': 'ATH',
            'reelflix': 'RF',
            'cathode-ray.tube': 'CRT',
            'empornium': 'EMP',
            'anthelion': 'ANT',
            'uhdbits': 'UHD',
            'pixelhd': 'PxHD',
            'empirehost': 'IPT',
            'stackoverflow.tech': 'IPT',
            'bgp.technology': 'IPT',
        }
        for key, short in _map.items():
            if key in host:
                return short
        # Fallback: use domain name minus common prefixes
        parts = host.replace('tracker.', '').replace('async.', '').replace('routing.', '').split('.')
        name = parts[0] if parts else ''
        # Skip numeric-only segments (e.g. 127.0.0.1)
        if name and not name.replace('.', '').isdigit():
            return name.upper()[:6]
    except Exception:
        pass
    return None


def _dest_path_to_library_name(dest_path: str) -> str:
    """Map a destination file path to a human-readable Plex library name."""
    from app.config import Config
    _map = {
        Config.RECENT_ROOT:   'Recently Released',
        Config.MOVIES_ROOT:   'Movies',
        Config.FOURKAY_ROOT:  '4K Movies',
        Config.FULLRIPS_ROOT: 'Full Rips',
        Config.TV_ROOT:       'TV Shows',
        Config.TV_4K_ROOT:    '4K TV',
    }
    if hasattr(Config, 'TV_REMUX_ROOT'):
        _map[Config.TV_REMUX_ROOT] = 'Remux TV'
    for root, name in _map.items():
        if dest_path.startswith(root):
            return name
    return 'Library'

# Lightweight TMDb cache for notification items: {(title,year,type): (timestamp, {id, poster_path})}
_notification_tmdb_cache: dict = {}
_notification_tmdb_cache_lock = threading.Lock()
_NOTIFICATION_TMDB_TTL = 60 * 60  # 1 hour
_NOTIFICATION_TMDB_CACHE_MAX = 500


def _evict_notification_tmdb_cache():
    """Purge expired entries; if still over cap, drop oldest by insertion time.
    Must be called while holding _notification_tmdb_cache_lock."""
    now = time.time()
    expired_keys = [k for k, (ts, _) in list(_notification_tmdb_cache.items())
                    if now - ts >= _NOTIFICATION_TMDB_TTL]
    for k in expired_keys:
        del _notification_tmdb_cache[k]
    if len(_notification_tmdb_cache) > _NOTIFICATION_TMDB_CACHE_MAX:
        sorted_keys = sorted(_notification_tmdb_cache, key=lambda k: _notification_tmdb_cache[k][0])
        for k in sorted_keys[:len(_notification_tmdb_cache) - _NOTIFICATION_TMDB_CACHE_MAX]:
            del _notification_tmdb_cache[k]

# Webhook notification store — persisted to JSON, capped at 200
_webhook_notifications: list = []
_webhook_notifications_lock = _threading.Lock()
_WEBHOOK_NOTIFICATIONS_FILE = (
    os.path.join('logs', 'webhook_notifications.json') if os.name == 'nt'
    else '/app/data/webhook_notifications.json'
)

def _load_webhook_notifications():
    global _webhook_notifications
    try:
        if os.path.exists(_WEBHOOK_NOTIFICATIONS_FILE):
            with open(_WEBHOOK_NOTIFICATIONS_FILE, 'r') as f:
                _webhook_notifications = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load webhook notifications: {e}")
        _webhook_notifications = []

def _save_webhook_notifications():
    """Write current list to disk (caller must hold _webhook_notifications_lock)."""
    try:
        os.makedirs(os.path.dirname(_WEBHOOK_NOTIFICATIONS_FILE), exist_ok=True)
        with open(_WEBHOOK_NOTIFICATIONS_FILE, 'w') as f:
            json.dump(_webhook_notifications, f)
    except Exception as e:
        logger.warning(f"Could not save webhook notifications: {e}")

def _add_webhook_notification(record: dict):
    with _webhook_notifications_lock:
        _webhook_notifications.insert(0, record)
        # Cap at 200 entries
        del _webhook_notifications[200:]
        _save_webhook_notifications()

# Load persisted webhook notifications on startup
_load_webhook_notifications()


# ---------------------------------------------------------------------------
# Manual-grab log — authoritative record of acquisitions the USER initiated
# (via the Sources panel grab). Lets the activity log show "Manual" vs the
# automation that grabbed something, with certainty instead of inference.
# ---------------------------------------------------------------------------
_manual_grab_lock = _threading.Lock()
_MANUAL_GRAB_FILE = (
    os.path.join('logs', 'manual_grab_log.json') if os.name == 'nt'
    else '/app/data/manual_grab_log.json'
)


def _load_manual_grabs() -> list:
    try:
        if os.path.exists(_MANUAL_GRAB_FILE):
            with open(_MANUAL_GRAB_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load manual grab log: {e}")
    return []


def _record_manual_grab(tmdb_id, title: str = '', media_type: str = 'movie'):
    """Append a user-initiated grab so the activity log can badge it 'Manual'."""
    if not tmdb_id:
        return
    try:
        with _manual_grab_lock:
            items = _load_manual_grabs()
            items.insert(0, {
                'tmdb_id': int(tmdb_id),
                'title': title or '',
                'media_type': media_type,
                'ts': time.time(),
            })
            del items[500:]
            os.makedirs(os.path.dirname(_MANUAL_GRAB_FILE), exist_ok=True)
            with open(_MANUAL_GRAB_FILE, 'w') as f:
                json.dump(items, f)
        logger.info(f"Recorded manual grab: tmdb_id={tmdb_id} '{title}' ({media_type})")
    except Exception as e:
        logger.warning(f"Could not record manual grab: {e}")


def _record_origin(origin: str, **kw):
    """Record who started a grab for the Activity badge. Never raises."""
    try:
        from app import grab_origin
        from flask import has_app_context
        if 'evidence' not in kw and has_app_context() and getattr(g, 'origin_evidence', ''):
            kw['evidence'] = g.origin_evidence  # e.g. 'request page: <name>'
        grab_origin.record(origin, **kw)
        _history_cache.clear()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Origin record failed: {exc}")


@app.route('/api/automation/manual-grab/log')
def manual_grab_log():
    """Return recent user-initiated (manual) grabs."""
    return jsonify(_load_manual_grabs()[:100])


def _wait_for_plex(title: str, media_type: str, year: str = '',
                   tmdb_id: int = None, season: int = None,
                   episode: int = None, max_wait: int = 120,
                   poll_interval: int = 5) -> bool:
    """Poll Plex until the media appears in the library.

    Returns True if found within *max_wait* seconds, False on timeout.
    Uses lightweight Plex search (no full index rebuild).
    """
    import time as _t
    deadline = _t.time() + max_wait

    while _t.time() < deadline:
        try:
            if media_type == 'movie':
                # ID-based match is most reliable
                if tmdb_id:
                    copies = plex_service.find_movie_copies(title, tmdb_id=tmdb_id)
                    if copies:
                        return True
                # Fallback: title search across all movie sections
                sections = plex_service.get_sections_lean()
                for sec in sections:
                    if sec['type'] != 'movie':
                        continue
                    results = plex_service.search_library(title, section_id=sec['id'],
                                                          media_type=1, limit=5)
                    for r in results:
                        r_year = r.get('year')
                        if r.get('title', '').lower() == title.lower() and \
                                (not year or not r_year or abs(int(year) - int(r_year or 0)) <= 1):
                            return True

            elif media_type == 'tv':
                # Search for the show, then optionally verify the specific episode
                sections = plex_service.get_sections_lean()
                for sec in sections:
                    if sec['type'] != 'show':
                        continue
                    shows = plex_service.search_library(title, section_id=sec['id'],
                                                        media_type=2, limit=5)
                    for show in shows:
                        if show.get('title', '').lower() != title.lower():
                            continue
                        show_rk = show.get('ratingKey') or show.get('rating_key')
                        if not show_rk:
                            continue
                        # Show found — if no specific episode requested, that's enough
                        if season is None:
                            return True
                        # Check for the specific episode
                        episodes = plex_service.get_show_episodes(show_rk)
                        for ep in episodes:
                            if ep.get('season_number') == season:
                                if episode is None or ep.get('episode_number') == episode:
                                    return True
        except Exception as exc:
            logger.debug(f"_wait_for_plex poll error: {exc}")

        _t.sleep(poll_interval)

    return False


def _delayed_webhook(record: dict, notify_fn, plex_check_kwargs: dict):
    """Background worker: wait for Plex scan, then fire notifications.

    *record* — the notification dict to store.
    *notify_fn* — callable that sends the web push (called after Plex ready).
    *plex_check_kwargs* — kwargs for ``_wait_for_plex()``.
    """
    found = _wait_for_plex(**plex_check_kwargs)
    if found:
        logger.info(f"Plex confirmed media: {plex_check_kwargs.get('title')}")
        # Bust the ownership index cache so the modal gets fresh data
        try:
            plex_service.build_ownership_index(force=True)
        except Exception:
            pass
        # Refresh fast search index so search sees the movie immediately
        try:
            fast_search_index._build_index()
            logger.info("Fast search index refreshed after new media confirmed")
        except Exception:
            pass
    else:
        logger.warning(f"Plex did not confirm media within timeout: "
                       f"{plex_check_kwargs.get('title')}")

    # Store the in-app notification (even on timeout — user should still know)
    _add_webhook_notification(record)

    # Check for pending Telegram notification requests and fire them
    try:
        from app.pending_requests import check_and_notify as _check_pending
        _check_pending(
            media_type=record.get('media_type', 'movie'),
            tmdb_id=record.get('tmdb_id'),
            season=record.get('season_number'),
            episode=record.get('episode_number'),
            episode_title=record.get('episode_title'),
        )
    except Exception as exc:
        logger.warning(f"Pending request check error: {exc}")

    # Fire the web push notification
    try:
        notify_fn()
    except Exception as exc:
        logger.warning(f"Delayed notification send error: {exc}")

def _clean_title_for_tmdb(title: str) -> str:
    """Strip junk that breaks TMDb search (e.g. 'Show (2004)' → 'Show')."""
    import re
    t = (title or '').strip()
    # Trailing parenthetical year — common in Sonarr/folder names
    t = re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', t).strip()
    t = re.sub(r'\s+', ' ', t)
    return t


def _tmdb_poster_by_id(tmdb_id: int, media_type: str) -> dict:
    """Fetch poster_path (+ id) directly from TMDb by id."""
    result = {'tmdb_id': int(tmdb_id), 'poster_path': None}
    try:
        kind = 'tv' if media_type == 'tv' else 'movie'
        resp = requests.get(
            f'{tmdb.BASE_URL}/{kind}/{int(tmdb_id)}',
            params={'api_key': tmdb.api_key},
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json() or {}
            result['poster_path'] = data.get('poster_path')
    except Exception:
        pass
    return result


def _resolve_tmdb_info(title: str, year: str, media_type: str,
                       tmdb_id: int | None = None) -> dict:
    """Resolve TMDb id + poster path for Activity/notification rows (cached).

    Release-group years often disagree with TMDb primary_release_year
    (e.g. Sound of Metal tagged 2019, TMDb 2020). A strict year search that
    misses was cached for an hour and left Activity with no art.
    Prefer a known tmdb_id; else search with year, then year±1, then no-year.
    Titles are cleaned of trailing (YYYY).
    """
    clean = _clean_title_for_tmdb(title)
    key = (clean, str(year or ''), media_type, int(tmdb_id) if tmdb_id else None)
    with _notification_tmdb_cache_lock:
        _evict_notification_tmdb_cache()
        cached = _notification_tmdb_cache.get(key)
        if cached:
            ts, data = cached
            age = time.time() - ts
            if age < _NOTIFICATION_TMDB_TTL:
                # Trust hits; only serve pure misses for 5 minutes so a bad
                # year miss can be retried after the fallback logic lands.
                if data.get('poster_path') or data.get('tmdb_id') or age < 300:
                    return data

    if tmdb_id:
        result = _tmdb_poster_by_id(int(tmdb_id), media_type)
        with _notification_tmdb_cache_lock:
            _notification_tmdb_cache[key] = (time.time(), result)
        return result

    # Check download metadata cache first (populated from Radarr queue)
    for meta in list(_dl_meta_cache.values()):
        meta_title = (meta.get('title') or '').lower()
        if not meta.get('tmdb_id') or meta_title != clean.lower():
            continue
        meta_year = str(meta.get('year', '') or '')
        year_ok = (not year) or meta_year == str(year)
        if not year_ok:
            try:
                year_ok = abs(int(meta_year or 0) - int(year or 0)) <= 1
            except Exception:
                year_ok = False
        if not year_ok:
            continue
        result = {'tmdb_id': meta['tmdb_id'], 'poster_path': meta.get('poster_path')}
        if result.get('tmdb_id') and not result.get('poster_path'):
            filled = _tmdb_poster_by_id(int(result['tmdb_id']), media_type)
            if filled.get('poster_path'):
                result['poster_path'] = filled['poster_path']
        with _notification_tmdb_cache_lock:
            _notification_tmdb_cache[key] = (time.time(), result)
        return result

    result = {'tmdb_id': None, 'poster_path': None}
    try:
        yr = int(year) if year and str(year).isdigit() else None
        results = []
        if media_type == 'tv':
            if yr:
                results = tmdb.search_tv(clean, year=yr) or []
            if not results:
                results = tmdb.search_tv(clean) or []
        else:
            if yr:
                results = tmdb.search_movies(clean, year=yr) or []
                if not results:
                    for alt in (yr - 1, yr + 1):
                        results = tmdb.search_movies(clean, year=alt) or []
                        if results:
                            break
            if not results:
                results = tmdb.search_movies(clean) or []
        if results:
            pick = results[0]
            cl = clean.lower()
            for r in results:
                rt = (r.get('title') or r.get('name') or '').lower()
                if rt == cl:
                    pick = r
                    break
            result['tmdb_id'] = pick.get('id')
            result['poster_path'] = pick.get('poster_path')
        with _notification_tmdb_cache_lock:
            _notification_tmdb_cache[key] = (time.time(), result)
    except Exception:
        pass
    return result

@app.route('/api/pending-request', methods=['POST'])
def add_pending_request():
    """Register a pending Telegram notification for a requested movie/TV show.

    Payload:
      {
        "tmdb_id": 12345,
        "title": "Show Title",
        "media_type": "movie" | "tv",
        "season": null | 1,
        "episode": null | 4,
        "episode_title": null | "Episode Title",   (optional — stored for richer notification)
        "sonarr_series_id": null | 123,            (optional — used for episode title lookup)
        "telegram_chat_id": "123456789"
      }
    """
    try:
        from app.pending_requests import add_request as _add_pending

        data = request.get_json() or {}
        tmdb_id = data.get('tmdb_id')
        title = data.get('title', '')
        media_type = data.get('media_type', 'movie')
        season = data.get('season')
        episode = data.get('episode')
        episode_title = data.get('episode_title')
        sonarr_series_id = data.get('sonarr_series_id')
        telegram_chat_id = data.get('telegram_chat_id', '')

        if not tmdb_id:
            return jsonify({'error': 'tmdb_id is required'}), 400
        if not title:
            return jsonify({'error': 'title is required'}), 400
        if not telegram_chat_id:
            return jsonify({'error': 'telegram_chat_id is required'}), 400
        if media_type not in ('movie', 'tv'):
            return jsonify({'error': 'media_type must be "movie" or "tv"'}), 400

        entry = _add_pending(
            tmdb_id=int(tmdb_id),
            title=title,
            media_type=media_type,
            telegram_chat_id=str(telegram_chat_id),
            season=int(season) if season is not None else None,
            episode=int(episode) if episode is not None else None,
            episode_title=str(episode_title) if episode_title else None,
            sonarr_series_id=int(sonarr_series_id) if sonarr_series_id is not None else None,
        )
        return jsonify({'success': True, 'id': entry['id']}), 201

    except Exception as e:
        logger.error(f"[pending-request] error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/notifications/unseen')
def get_unseen_notifications():
    """Get unseen download notifications (successful downloads since last seen)"""
    try:
        from app.auto_rename import auto_renamer

        # Determine seen-at file path (same Docker-vs-Windows pattern as AutoRenamer)
        if os.name == 'nt':
            seen_file = os.path.join('logs', 'notifications_seen_at.json')
        else:
            seen_file = '/app/data/notifications_seen_at.json'

        # Read last-seen timestamp
        seen_at = 0.0
        if os.path.exists(seen_file):
            try:
                with open(seen_file, 'r') as f:
                    seen_at = json.load(f).get('seen_at', 0.0)
            except Exception:
                seen_at = 0.0

        # --- Source 1: AutoRenamer history ---
        history = auto_renamer.get_history()
        renamer_items = [
            item for item in history
            if item.get('success') and item.get('completed_timestamp', 0) > seen_at
        ]

        # --- Source 2: Webhook notifications ---
        with _webhook_notifications_lock:
            webhook_items = [
                item for item in _webhook_notifications
                if item.get('completed_timestamp', 0) > seen_at
            ]

        # Build unified list from renamer items
        unified = []
        seen_keys: set = set()  # (title_lower, year) for dedup

        for item in renamer_items:
            title = item.get('tmdb_title', '')
            year = item.get('tmdb_year', '')
            ts = item.get('completed_timestamp', 0)
            key = (title.lower(), str(year))
            seen_keys.add((key, ts))
            tmdb_info = _resolve_tmdb_info(
                title, str(year), item.get('media_type', 'movie')
            )
            unified.append({
                'tmdb_title': title,
                'tmdb_year': year,
                'media_type': item.get('media_type', 'movie'),
                'completed_timestamp': ts,
                'library_name': _dest_path_to_library_name(item.get('destination_path', '')),
                'poster_path': tmdb_info.get('poster_path'),
                'tmdb_id': tmdb_info.get('tmdb_id'),
                'season_number': item.get('season_number'),
                'episode_number': item.get('episode_number'),
            })

        # Merge webhook items, deduplicating against renamer items
        for item in webhook_items:
            title = item.get('title', '')
            year = item.get('year', '')
            ts = item.get('completed_timestamp', 0)
            key = (title.lower(), str(year))
            # Skip if renamer already has this title+year within 5 minutes
            is_dupe = any(
                k == key and abs(t - ts) < 300
                for k, t in seen_keys
            )
            if is_dupe:
                continue
            # Webhook items may already have tmdb_id from the Radarr/Sonarr payload
            tmdb_id = item.get('tmdb_id')
            tmdb_info = _resolve_tmdb_info(title, str(year), item.get('media_type', 'movie'))
            poster_path = tmdb_info.get('poster_path')
            if not tmdb_id:
                tmdb_id = tmdb_info.get('tmdb_id')
            unified.append({
                'tmdb_title': title,
                'tmdb_year': year,
                'media_type': item.get('media_type', 'movie'),
                'completed_timestamp': ts,
                'library_name': _dest_path_to_library_name(item.get('dest_path', '')),
                'poster_path': poster_path,
                'tmdb_id': tmdb_id,
                'season_number': item.get('season_number'),
                'episode_number': item.get('episode_number'),
            })

        # Sort by timestamp descending, cap at 20
        unified.sort(key=lambda x: x.get('completed_timestamp', 0), reverse=True)
        unseen = unified[:20]

        return jsonify({'count': len(unseen), 'items': unseen})

    except Exception as e:
        logger.error(f"Error getting unseen notifications: {e}")
        return jsonify({'count': 0, 'items': []})

@app.route('/api/notifications/mark-seen', methods=['POST'])
def mark_notifications_seen():
    """Mark all notifications as seen"""
    try:
        import time as _time

        if os.name == 'nt':
            seen_file = os.path.join('logs', 'notifications_seen_at.json')
        else:
            seen_file = '/app/data/notifications_seen_at.json'

        os.makedirs(os.path.dirname(seen_file), exist_ok=True)
        with open(seen_file, 'w') as f:
            json.dump({'seen_at': _time.time()}, f)

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Error marking notifications seen: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

@app.route('/api/downloads/remove/<torrent_hash>', methods=['DELETE', 'POST'])
def remove_from_tracking(torrent_hash):
    """Remove a torrent from qBittorrent (and optionally delete files on disk).

    Accepts deleteFiles / delete_files via query string or JSON body (default
    false = remove torrent only, keep files). Also drops app tracking + busts
    the downloads-queue micro-cache so Activity refreshes immediately.
    """
    try:
        import requests as req

        body = request.get_json(silent=True) or {} if request.method == 'POST' else {}
        raw = (
            request.args.get('deleteFiles')
            or request.args.get('delete_files')
            or body.get('deleteFiles')
            or body.get('delete_files')
            or 'false'
        )
        delete_files = str(raw).lower() in ('1', 'true', 'yes')

        h = (torrent_hash or '').strip().lower()
        if not h or len(h) < 16:
            return jsonify({'success': False, 'error': 'Invalid torrent hash'}), 400

        qbit_url = settings.get('qbittorrent.url')
        if not qbit_url:
            return jsonify({'success': False, 'error': 'qBittorrent not configured', 'needs_setup': 'qbittorrent'}), 500

        qs = req.Session()
        lr = qs.post(
            f"{qbit_url}/api/v2/auth/login",
            data={
                'username': settings.get('qbittorrent.username'),
                'password': settings.get('qbittorrent.password'),
            },
            timeout=8,
        )
        if lr.status_code != 200 or lr.text.strip() != 'Ok.':
            return jsonify({'success': False, 'error': 'qBittorrent login failed'}), 502

        dr = qs.post(
            f"{qbit_url}/api/v2/torrents/delete",
            data={
                'hashes': h,
                'deleteFiles': 'true' if delete_files else 'false',
            },
            timeout=15,
        )
        if dr.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'qBittorrent delete failed ({dr.status_code})',
            }), 502

        try:
            from app.auto_rename import auto_renamer
            if auto_renamer:
                auto_renamer.remove_from_tracking(h)
                if torrent_hash != h:
                    auto_renamer.remove_from_tracking(torrent_hash)
        except Exception as te:
            logger.warning(f"remove tracking after qBit delete: {te}")

        # Bust queue micro-cache so Activity / Downloads pollers see the removal.
        try:
            _queue_cache['data'] = None
            _queue_cache['ts'] = 0.0
        except Exception:
            pass

        return jsonify({
            'success': True,
            'hash': h,
            'deleteFiles': delete_files,
            'message': (
                'Deleted torrent and files'
                if delete_files
                else 'Removed from qBittorrent'
            ),
        })

    except Exception as e:
        logger.error(f"Error removing download {torrent_hash}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def _bencode_element_end(data: bytes, idx: int) -> int:
    """Return the index just past the bencoded element starting at *idx*."""
    c = data[idx:idx + 1]
    if c == b'i':                       # integer: i<num>e
        return data.index(b'e', idx) + 1
    if c == b'l' or c == b'd':          # list / dict
        idx += 1
        while data[idx:idx + 1] != b'e':
            idx = _bencode_element_end(data, idx)
        return idx + 1
    colon = data.index(b':', idx)       # string: <len>:<bytes>
    length = int(data[idx:colon])
    return colon + 1 + length


def _torrent_info_hash(data: bytes):
    """Compute the v1 info-hash (sha1 of the raw 'info' dict) of a .torrent.

    Returns a lowercase hex digest, or None if the bytes aren't a bencoded
    torrent. Used to positively identify the exact torrent we handed qBit so we
    NEVER track an unrelated one.
    """
    try:
        import hashlib
        if data[:1] != b'd':
            return None
        idx = 1
        while data[idx:idx + 1] != b'e':
            colon = data.index(b':', idx)
            klen = int(data[idx:colon])
            kstart = colon + 1
            key = data[kstart:kstart + klen]
            idx = kstart + klen
            vstart = idx
            idx = _bencode_element_end(data, idx)
            if key == b'info':
                return hashlib.sha1(data[vstart:idx]).hexdigest().lower()
        return None
    except Exception:
        return None


def _magnet_info_hash(magnet: str):
    """Extract a lowercase v1 hex info-hash from a magnet link, or None."""
    try:
        import re
        m = re.search(r'xt=urn:btih:([a-zA-Z0-9]+)', magnet or '')
        if not m:
            return None
        h = m.group(1)
        if len(h) == 40:
            return h.lower()
        if len(h) == 32:                # base32-encoded hash
            import base64
            return base64.b32decode(h.upper()).hex().lower()
        return None
    except Exception:
        return None


@app.route('/api/downloads/add', methods=['POST'])
def add_torrent_to_qbittorrent():
    """Add a torrent to qBittorrent and track it.

    ROBUSTNESS CONTRACT (do not regress — see the "Obsession 2026" incident):
    a 200 from this endpoint means the torrent is CONFIRMED present in
    qBittorrent. We never report success on a bare qBit 'Ok.' (that only means
    the add *request* was accepted, not that the .torrent link resolved — a dead
    Jackett/Prowlarr link silently produces no torrent), and we never track a
    torrent we didn't positively identify by hash. The old code did both, which
    reported a phantom "added" and tracked an unrelated torrent as the movie.

    Strategy: fetch + validate the .torrent server-side (so we detect dead links
    BEFORE lying about success and so qBit never has to reach the indexer), then
    upload the bytes, then poll qBit until the exact hash appears.
    """
    try:
        from app.auto_rename import auto_renamer
        import requests as req
        import time as _time

        data = request.get_json() or {}
        torrent_url = (data.get('torrent_url') or '').strip()
        tmdb_data = data.get('tmdb_data')
        torrent_name = data.get('torrent_name')

        if not torrent_url:
            return jsonify({'error': 'Missing torrent_url'}), 400

        qbit_url = settings.get('qbittorrent.url')
        qbit_user = settings.get('qbittorrent.username')
        qbit_pass = settings.get('qbittorrent.password')

        session = req.Session()
        login_response = session.post(
            f"{qbit_url}/api/v2/auth/login",
            data={'username': qbit_user, 'password': qbit_pass},
            timeout=10,
        )
        if login_response.status_code != 200 or login_response.text.strip() != 'Ok.':
            logger.error(f"qBittorrent login failed: {login_response.status_code}")
            return jsonify({'error': 'Failed to login to qBittorrent'}), 500

        logger.info(f"Grab-to-library: {torrent_name or '(unnamed)'}")

        is_magnet = torrent_url.lower().startswith('magnet:')
        expected_hash = None
        torrent_bytes = None

        if is_magnet:
            expected_hash = _magnet_info_hash(torrent_url)
        else:
            # Fetch the .torrent ourselves. This is the crux of the fix: if the
            # indexer link is dead/expired/HTML we find out NOW and fail loudly,
            # instead of handing qBit a URL it silently fails to fetch.
            try:
                dl = session.get(torrent_url, timeout=30, allow_redirects=True)
            except Exception as e:
                logger.error(f"Failed to fetch torrent from indexer: {e}")
                return jsonify({'error': f'Could not fetch the torrent from the indexer: {e}'}), 502

            final_url = (dl.url or '').lower()
            body_head = dl.content[:8].lower() if dl.content else b''

            if final_url.startswith('magnet:'):
                is_magnet = True
                torrent_url = dl.url
                expected_hash = _magnet_info_hash(torrent_url)
            elif body_head.startswith(b'magnet:'):
                is_magnet = True
                torrent_url = dl.content.decode('utf-8', 'ignore').strip().splitlines()[0]
                expected_hash = _magnet_info_hash(torrent_url)
            elif dl.status_code == 200 and dl.content[:1] == b'd':
                torrent_bytes = dl.content
                expected_hash = _torrent_info_hash(torrent_bytes)
            else:
                ct = dl.headers.get('content-type', '')
                logger.error(
                    f"Indexer did not return a valid torrent "
                    f"(status={dl.status_code}, content-type={ct}, "
                    f"head={dl.content[:80]!r})"
                )
                return jsonify({
                    'error': 'The indexer returned an invalid or expired download link. '
                             'Nothing was added — re-search for a fresh source and try again.'
                }), 502

        def _qbit_category_torrents():
            try:
                tr = session.get(
                    f"{qbit_url}/api/v2/torrents/info",
                    params={'category': _qbit_app_category()},
                    timeout=10,
                )
                return tr.json() if tr.status_code == 200 else []
            except Exception as e:
                logger.warning(f"qBit torrents/info failed: {e}")
                return []

        # Snapshot existing hashes so a brand-new torrent is detectable even if
        # we couldn't compute the expected hash up front (e.g. hashless magnet).
        before_hashes = {t.get('hash', '').lower() for t in _qbit_category_torrents()}

        # If this exact torrent is already present, treat it as success (dedup)
        # and track the existing hash rather than erroring out.
        if expected_hash and expected_hash in before_hashes:
            logger.info(f"Torrent already in qBittorrent (hash {expected_hash}) — reusing")
            if tmdb_data:
                try:
                    auto_renamer.track_download(expected_hash, tmdb_data)
                except Exception as e:
                    logger.error(f"Failed to track existing download: {e}")
            return jsonify({'success': True, 'message': 'Already downloading', 'hash': expected_hash})

        # Add to qBit — upload the validated bytes when we have them, otherwise
        # hand over the magnet URI (qBit resolves magnets via DHT itself).
        try:
            if torrent_bytes is not None:
                add_response = session.post(
                    f"{qbit_url}/api/v2/torrents/add",
                    files={'torrents': ('release.torrent', torrent_bytes, 'application/x-bittorrent')},
                    data={'category': _qbit_app_category(), 'autoTMM': 'false'},
                    timeout=30,
                )
            else:
                add_response = session.post(
                    f"{qbit_url}/api/v2/torrents/add",
                    data={'urls': torrent_url, 'category': _qbit_app_category(), 'autoTMM': 'false'},
                    timeout=30,
                )
        except Exception as e:
            logger.error(f"qBittorrent add request failed: {e}")
            return jsonify({'error': f'qBittorrent add request failed: {e}'}), 502

        logger.info(f"qBittorrent add response: status={add_response.status_code}, text='{add_response.text.strip()}'")
        if add_response.status_code != 200:
            return jsonify({'error': f'qBittorrent returned HTTP {add_response.status_code}'}), 502

        # Verify the torrent ACTUALLY landed. qBit's 'Ok.' proves nothing about
        # the URL resolving — poll for the concrete torrent before claiming success.
        confirmed_hash = None
        for _ in range(10):  # up to ~15s
            _time.sleep(1.5)
            current = _qbit_category_torrents()
            if expected_hash:
                for t in current:
                    if t.get('hash', '').lower() == expected_hash:
                        confirmed_hash = t['hash']
                        break
            if not confirmed_hash:
                # New hash since our snapshot == the torrent we just added.
                new = [t for t in current if t.get('hash', '').lower() not in before_hashes]
                if new:
                    new.sort(key=lambda x: x.get('added_on', 0), reverse=True)
                    confirmed_hash = new[0]['hash']
            if confirmed_hash:
                break

        if not confirmed_hash:
            logger.error("Torrent was accepted by qBit but never appeared — treating as FAILED (no track).")
            return jsonify({
                'error': 'qBittorrent accepted the request but the torrent never appeared — '
                         'the link is likely dead. Nothing was added or tracked.'
            }), 502

        logger.info(f"✅ Torrent confirmed in qBittorrent (hash {confirmed_hash})")
        _record_origin('manual', info_hash=confirmed_hash, download_url=torrent_url,
                       tmdb_id=(tmdb_data or {}).get('tmdb_id') or (tmdb_data or {}).get('id'),
                       media_type=(tmdb_data or {}).get('media_type') or '',
                       title=(tmdb_data or {}).get('title') or torrent_name or '')

        # Positively identified — NOW it is safe to track for rename/library move.
        if tmdb_data:
            try:
                auto_renamer.track_download(confirmed_hash, tmdb_data)
                logger.info(f"Download tracked for: {tmdb_data.get('title')}")
            except Exception as e:
                logger.error(f"Failed to track download: {e}")

        return jsonify({
            'success': True,
            'message': 'Download added successfully',
            'hash': confirmed_hash,
        })

    except Exception as e:
        logger.error(f"Error adding torrent: {e}")
        return jsonify({'error': str(e)}), 500

# Sonarr Integration Endpoints
@app.route('/api/sonarr/add', methods=['POST'])
def sonarr_add_series():
    """Add a TV series to Sonarr"""
    try:
        sonarr = get_sonarr_for_request()

        data = request.get_json() or {}
        tvdb_id = data.get('tvdb_id')
        title = data.get('title', 'Unknown')
        # Monitor modes: all, future, missing, existing, none, firstSeason,
        # latestSeason, or 'seasons' (custom per-season selection via `seasons`).
        monitor = data.get('monitor', 'all')
        seasons_req = data.get('seasons') or []  # list of season numbers when monitor=='seasons'
        try:
            seasons_req = [int(s) for s in seasons_req]
        except (TypeError, ValueError):
            seasons_req = []
        quality_profile_id = data.get('quality_profile_id')
        search_missing = data.get('search_missing', True)

        # ── Helpers for per-season / preset monitoring on an existing series ──
        def _real_season_numbers(series_dict):
            return sorted(
                s.get('seasonNumber')
                for s in (series_dict.get('seasons') or [])
                if s.get('seasonNumber', 0) > 0
            )

        def _resolve_target_seasons(series_dict):
            """Translate the requested monitor mode into concrete season numbers."""
            real = _real_season_numbers(series_dict)
            if monitor == 'firstSeason':
                return real[:1]
            if monitor == 'latestSeason':
                return real[-1:]
            if monitor == 'seasons':
                # Keep only valid, real seasons the user actually selected.
                return [n for n in seasons_req if n in real]
            return []

        def _apply_season_selection(series_id, season_numbers):
            """Monitor exactly the given seasons (+ series) and search each."""
            applied = []
            for sn in season_numbers:
                if sonarr.ensure_season_monitored(series_id, sn):
                    applied.append(sn)
                    if search_missing:
                        sonarr.search_season_command(series_id, sn)
            return applied

        if not tvdb_id:
            return jsonify({'error': 'Missing tvdb_id'}), 400

        if not sonarr:
            return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503

        # Resolve quality profile: explicit ID, else settings default → preferred name → first
        if not quality_profile_id:
            profiles = sonarr.get_quality_profiles()
            quality_profile_id = _resolve_sonarr_profile_id(profiles)
            logger.info(f"Using quality profile (id={quality_profile_id})")

        # Check if series already exists. A series may already be present but
        # INERT (monitored=False, monitor='none') because it was auto-added just
        # for interactive release browsing. In that case a watchlist "add" must
        # promote it — enable monitoring and kick off a search — rather than
        # report a no-op "already exists".
        existing = sonarr.get_series(tvdb_id)
        if existing:
            series = existing[0]
            series_id = series.get('id')
            if series.get('monitored'):
                # Genuinely already tracked and monitored — nothing to do.
                return jsonify({
                    'success': True,
                    'already_exists': True,
                    'message': 'Series already exists in Sonarr'
                }), 200

            # Inert series → promote, honoring the requested monitor mode.
            if monitor in ('firstSeason', 'latestSeason', 'seasons'):
                targets = _resolve_target_seasons(series)
                applied = _apply_season_selection(series_id, targets)
                msg = (f'Monitoring season(s) {", ".join(map(str, applied))} for {title}'
                       if applied else f'Enabled monitoring for {title}')
            elif monitor in ('future', 'none'):
                # Series-level only — no back-search of already-aired episodes.
                sonarr.ensure_series_monitored(series_id)
                msg = f'Enabled monitoring for {title}'
            else:  # all / missing / existing
                sonarr.monitor_all_seasons(series_id)
                if search_missing:
                    sonarr.search_episodes(series_id)
                msg = f'Enabled monitoring and searching for {title}'
            logger.info(f"Promoted inert series (monitor={monitor}): tvdb={tvdb_id} ('{title}')")
            return jsonify({
                'success': True,
                'already_exists': True,
                'promoted': True,
                'message': msg
            }), 200

        if monitor in ('firstSeason', 'latestSeason', 'seasons'):
            # No native Sonarr add-mode for arbitrary season sets (and the
            # firstSeason/latestSeason enum names vary across Sonarr versions),
            # so add inert then monitor + search exactly the resolved seasons.
            result = sonarr.add_series(
                tvdb_id=tvdb_id,
                title=title,
                quality_profile_id=quality_profile_id,
                monitor='none',
                search_for_missing=False,
                monitored=True,
            )
            if not result:
                return jsonify({'success': False, 'message': 'Failed to add series to Sonarr'}), 500

            series_id = result['id']
            targets = _resolve_target_seasons(result)
            # Sonarr populates episodes via an async RefreshSeries after add;
            # poll briefly so ensure_season_monitored can monitor real episodes.
            if targets:
                for _ in range(12):
                    if sonarr.get_episode_by_series_and_numbers(series_id, targets[0], 1):
                        break
                    time.sleep(0.5)
            applied = _apply_season_selection(series_id, targets)
            msg = (f'Added {title} — monitoring season(s) {", ".join(map(str, applied))}'
                   if applied else f'Added {title} to Sonarr')
            return jsonify({'success': True, 'message': msg, 'series': result})

        # Standard modes (all / future / missing / none) — let Sonarr's
        # addOptions.monitor decide, with an immediate scoped search.
        result = sonarr.add_series(
            tvdb_id=tvdb_id,
            title=title,
            quality_profile_id=quality_profile_id,
            monitor=monitor,
            search_for_missing=search_missing
        )

        if result:
            return jsonify({
                'success': True,
                'message': f'Successfully added {title} to Sonarr',
                'series': result
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to add series to Sonarr'
            }), 500

    except Exception as e:
        logger.error(f"Error adding series to Sonarr: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sonarr/status/<int:tvdb_id>')
def sonarr_series_status(tvdb_id):
    """Check if a series exists in Sonarr"""
    try:
        sonarr = get_sonarr_for_request()

        if not sonarr:
            return jsonify({'exists': False, 'enabled': False})

        # Get TMDB ID from request args as fallback
        tmdb_id = request.args.get('tmdb_id', type=int)

        logger.info(f"Checking Sonarr status - TVDB: {tvdb_id}, TMDB: {tmdb_id}")

        # Single get_series call — series_exists() internally called get_series()
        # too, so the old code fetched the whole Sonarr library TWICE per request.
        series_data = sonarr.get_series(tvdb_id, tmdb_id)

        return jsonify({
            'exists': len(series_data) > 0,
            'enabled': True,
            'series': series_data[0] if series_data else None
        })

    except Exception as e:
        logger.error(f"Error checking Sonarr status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sonarr/series-status')
def sonarr_series_status_detail():
    """Owner-only, read-only Sonarr status for the TV modal (show/season/episode)."""
    denied = _require_admin()
    if denied:
        return denied
    try:
        sonarr = get_sonarr_for_request()
        if not sonarr:
            return jsonify({'enabled': False, 'exists': False})
        tvdb_id = request.args.get('tvdb_id', type=int)
        tmdb_id = request.args.get('tmdb_id', type=int)
        if not tvdb_id and not tmdb_id:
            return jsonify({'error': 'Missing tvdb_id or tmdb_id'}), 400
        from app.sonarr_status import series_status
        return jsonify(series_status(sonarr, tvdb_id, tmdb_id))
    except Exception as e:
        logger.error(f"Error building Sonarr series status: {e}")
        return jsonify({'error': 'Sonarr status unavailable'}), 502

@app.route('/api/sonarr/monitor/episodes', methods=['POST'])
def sonarr_monitor_episodes():
    """Owner-only: set monitored on specific episodes; turning on searches."""
    denied = _require_admin()
    if denied:
        return denied
    sonarr = get_sonarr_for_request()
    if not sonarr:
        return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503
    data = request.get_json() or {}
    series_id = data.get('series_id')
    episode_ids = data.get('episode_ids') or []
    monitored = data.get('monitored')
    if not isinstance(series_id, int) or not isinstance(monitored, bool) or not isinstance(episode_ids, list) or not episode_ids:
        return jsonify({'error': 'series_id, episode_ids and monitored are required'}), 400
    from app.sonarr_status import set_episodes_monitored, MonitorError
    try:
        return jsonify({'success': True, **set_episodes_monitored(sonarr, series_id, episode_ids, monitored)})
    except MonitorError as e:
        return jsonify({'success': False, 'error': str(e)}), 502
    except Exception as e:
        logger.error(f"Episode monitor update failed: {e}")
        return jsonify({'success': False, 'error': 'Sonarr update failed'}), 502

@app.route('/api/sonarr/monitor/series', methods=['POST'])
def sonarr_monitor_series():
    """Owner-only show-level monitoring: mode all | future | seasons | off."""
    denied = _require_admin()
    if denied:
        return denied
    sonarr = get_sonarr_for_request()
    if not sonarr:
        return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503
    data = request.get_json() or {}
    series_id = data.get('series_id')
    mode = data.get('mode')
    seasons = data.get('seasons')
    if not isinstance(series_id, int):
        return jsonify({'error': 'series_id is required'}), 400
    if mode is None:
        monitored = data.get('monitored')
        if not isinstance(monitored, bool):
            return jsonify({'error': 'mode or monitored is required'}), 400
        mode = ('all' if data.get('all_seasons') else 'series') if monitored else 'off'
    if mode not in ('all', 'future', 'seasons', 'off', 'series'):
        return jsonify({'error': 'Unknown mode'}), 400
    if mode == 'seasons' and (not isinstance(seasons, list) or not all(isinstance(n, int) for n in seasons)):
        return jsonify({'error': 'seasons must be a list of season numbers'}), 400
    from app.sonarr_status import set_series_monitor_mode, set_series_monitored, MonitorError
    try:
        if mode == 'series':
            result = set_series_monitored(sonarr, series_id, True)
        else:
            result = set_series_monitor_mode(sonarr, series_id, mode, seasons)
        return jsonify({'success': True, **result})
    except MonitorError as e:
        return jsonify({'success': False, 'error': str(e)}), 502
    except Exception as e:
        logger.error(f"Series monitor update failed: {e}")
        return jsonify({'success': False, 'error': 'Sonarr update failed'}), 502

@app.route('/api/sonarr/monitor/season', methods=['POST'])
def sonarr_monitor_season():
    """Owner-only: set monitored on one season (no search)."""
    denied = _require_admin()
    if denied:
        return denied
    sonarr = get_sonarr_for_request()
    if not sonarr:
        return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503
    data = request.get_json() or {}
    series_id = data.get('series_id')
    season = data.get('season')
    monitored = data.get('monitored')
    if not isinstance(series_id, int) or not isinstance(season, int) or not isinstance(monitored, bool):
        return jsonify({'error': 'series_id, season and monitored are required'}), 400
    from app.sonarr_status import set_season_monitored, MonitorError
    try:
        return jsonify({'success': True, **set_season_monitored(sonarr, series_id, season, monitored)})
    except MonitorError as e:
        return jsonify({'success': False, 'error': str(e)}), 502
    except Exception as e:
        logger.error(f"Season monitor update failed: {e}")
        return jsonify({'success': False, 'error': 'Sonarr update failed'}), 502

@app.route('/api/sonarr/quality-profiles')
def sonarr_quality_profiles():
    """Get available Sonarr quality profiles"""
    try:
        sonarr = get_sonarr_for_request()

        if not sonarr:
            return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503

        profiles = sonarr.get_quality_profiles()
        return jsonify(profiles)

    except Exception as e:
        logger.error(f"Error getting quality profiles: {e}")
        return jsonify({'error': str(e)}), 500

# In-memory cache for Sonarr calendar. Kiosk polls every 15 min; cache evens
# out bursts of client reloads without going out of date on the upcoming window.
# Keyed by window (rolling `days` or an explicit start:end range) so the
# daily-kiosk widget, the watchlist page and the TV Calendar week pager don't
# evict each other's entry and re-hit Sonarr on every switch.
_sonarr_calendar_cache: Dict[str, tuple] = {}  # key → (timestamp, episodes)
_SONARR_CALENDAR_TTL = 10 * 60  # 10 minutes
_SONARR_CALENDAR_MAX_KEYS = 16

@app.route('/api/sonarr/calendar')
def sonarr_calendar():
    """Upcoming TV episodes from Sonarr, shaped for the daily-kiosk widget.

    Query params:
      days: lookahead window from today (default 14, max 30)
      start/end: explicit ISO dates (YYYY-MM-DD); override `days`. The TV
        Calendar sheet uses these because a week grid starts before today.
      limit: cap on returned episodes (default 10, max 200)
    Response:
      { enabled: bool, episodes: [...] }
      Each episode carries: series_title, season, episode, episode_title,
      air_date_utc, air_date_local (ISO), network, runtime, overview, poster_url,
      tvdb_id, tmdb_id, has_file, monitored.
    """
    import time as _t
    import re as _re
    try:
        days = request.args.get('days', default=14, type=int) or 14
        days = max(1, min(30, days))
        limit = request.args.get('limit', default=10, type=int) or 10
        limit = max(1, min(200, limit))

        # Explicit range wins over `days`; ignore anything not ISO YYYY-MM-DD
        # so a malformed param degrades to the rolling window instead of 500ing.
        _iso = _re.compile(r'^\d{4}-\d{2}-\d{2}$')
        start = (request.args.get('start') or '').strip()
        end = (request.args.get('end') or '').strip()
        if not (_iso.match(start) and _iso.match(end)):
            start = end = None

        cache_key = f"{start}:{end}" if start else f"days:{days}"
        now_ts = _t.time()
        cached = _sonarr_calendar_cache.get(cache_key)
        if cached and now_ts - cached[0] < _SONARR_CALENDAR_TTL:
            episodes = cached[1]
        else:
            sonarr = get_sonarr_for_request()
            if not sonarr or not sonarr.enabled:
                return jsonify({'enabled': False, 'episodes': []})

            raw = sonarr.get_calendar(days=days, include_series=True, start=start, end=end) or []
            episodes = []
            for ep in raw:
                series = ep.get('series') or {}
                images = series.get('images') or []
                poster_url = None
                for img in images:
                    if img.get('coverType') == 'poster':
                        # Prefer remoteUrl (absolute); fall back to url joined to Sonarr host
                        poster_url = img.get('remoteUrl') or img.get('url')
                        break

                episodes.append({
                    'series_title': series.get('title') or ep.get('seriesTitle') or '',
                    'season': ep.get('seasonNumber'),
                    'episode': ep.get('episodeNumber'),
                    'episode_title': ep.get('title') or '',
                    'air_date_utc': ep.get('airDateUtc'),
                    'air_date_local': ep.get('airDate'),
                    'network': series.get('network') or '',
                    'runtime': series.get('runtime') or ep.get('runtime') or 0,
                    'overview': (ep.get('overview') or '').strip(),
                    'poster_url': poster_url,
                    'tvdb_id': series.get('tvdbId'),
                    'tmdb_id': series.get('tmdbId') or None,
                    'has_file': bool(ep.get('hasFile')),
                    'monitored': bool(ep.get('monitored')),
                })

            # Sonarr normally returns chronologically; enforce it anyway.
            episodes.sort(key=lambda e: e.get('air_date_utc') or '')

            # Bounded: drop the oldest entry once the pager has wandered far.
            if len(_sonarr_calendar_cache) >= _SONARR_CALENDAR_MAX_KEYS:
                oldest = min(_sonarr_calendar_cache, key=lambda k: _sonarr_calendar_cache[k][0])
                _sonarr_calendar_cache.pop(oldest, None)
            _sonarr_calendar_cache[cache_key] = (now_ts, episodes)

        return jsonify({'enabled': True, 'episodes': episodes[:limit]})

    except Exception as e:
        logger.error(f"Error fetching Sonarr calendar: {e}")
        return jsonify({'enabled': True, 'episodes': [], 'error': str(e)}), 200

# ── Home Assistant: smart-home panel endpoints ────────────────────────────
# Used by the kiosk smart-home modal. All endpoints gate on HA being
# configured in settings; when it isn't, they respond with enabled=false so
# the UI can hide itself silently instead of erroring out.

def _ha_creds():
    """Return (url, token) or (None, None) if HA isn't configured."""
    url = (settings.get('homeassistant.url') or '').strip()
    token = (settings.get('homeassistant.access_token') or '').strip()
    if not url or not token:
        return None, None
    return url, token


@app.route('/api/ha/areas')
def ha_areas():
    """List Home Assistant areas for the smart-home panel and Settings UI."""
    from app.ha_service import list_areas
    url, token = _ha_creds()
    if not url or not token:
        return jsonify({'enabled': False, 'areas': []})
    try:
        return jsonify({'enabled': True, 'areas': list_areas(url, token)})
    except Exception as e:
        logger.error("HA areas fetch failed: %s", e)
        return jsonify({'enabled': True, 'areas': [], 'error': str(e)}), 200


@app.route('/api/ha/area/<area_id>/entities')
def ha_area_entities(area_id):
    """Controllable entities in an area with their current state snapshot."""
    from app.ha_service import get_area_entities
    url, token = _ha_creds()
    if not url or not token:
        return jsonify({'enabled': False, 'entities': []})
    try:
        return jsonify({'enabled': True, 'entities': get_area_entities(url, token, area_id)})
    except Exception as e:
        logger.error("HA area entities fetch failed (%s): %s", area_id, e)
        return jsonify({'enabled': True, 'entities': [], 'error': str(e)}), 200


@app.route('/api/ha/service', methods=['POST'])
def ha_service_call():
    """Forward a service call to Home Assistant.

    Expects JSON body ``{"domain": "light", "service": "turn_on",
    "data": {"entity_id": "light.living_room", "brightness_pct": 80}}``.
    """
    from app.ha_service import call_service
    url, token = _ha_creds()
    if not url or not token:
        return jsonify({'ok': False, 'detail': 'Home Assistant is not configured'}), 200

    payload = request.get_json(silent=True) or {}
    domain = (payload.get('domain') or '').strip()
    service = (payload.get('service') or '').strip()
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    if not domain or not service:
        return jsonify({'ok': False, 'detail': 'domain and service are required'}), 400

    result = call_service(url, token, domain, service, data)
    return jsonify(result)


def _sonarr_rejection_reasons(release):
    out = []
    for rej in release.get('rejections') or []:
        reason = rej.get('reason', '') if isinstance(rej, dict) else str(rej)
        if reason:
            out.append(reason)
    return out


def _ensure_sonarr_series_for_browse(sonarr, tvdb_id, tmdb_id=None, title=None):
    """Resolve the Sonarr series for a tvdb_id, auto-adding it if absent.

    When the series isn't in Sonarr yet, it's added fully INERT — series-level
    monitored=False, monitor='none', no search — purely so the interactive
    release browser has a series_id to query. Sonarr will never search or grab
    anything from this add; the user can later choose to monitor it explicitly.

    Mirrors the Radarr "movie not in Radarr" auto-add used by movie sources.

    Returns (series_dict_or_None, auto_added_bool).
    """
    series_list = sonarr.get_series(tvdb_id, tmdb_id)
    if series_list:
        return series_list[0], False

    profiles = sonarr.get_quality_profiles()
    profile_id = _resolve_sonarr_profile_id(profiles)

    logger.info(f"Series tvdb_id={tvdb_id} not in Sonarr, adding unmonitored for release browsing")
    added = sonarr.add_series(
        tvdb_id=tvdb_id,
        title=title or f"Series {tvdb_id}",
        quality_profile_id=profile_id,
        monitor='none',
        search_for_missing=False,
        monitored=False,
    )
    if not added:
        return None, False
    return added, True


@app.route('/api/sonarr/episode/search')
def sonarr_search_episode():
    """Search for releases for a specific episode"""
    denied = _require_admin()
    if denied:
        return denied
    try:
        sonarr = get_sonarr_for_request()

        if not sonarr:
            return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503

        tvdb_id = request.args.get('tvdb_id', type=int)
        season = request.args.get('season', type=int)
        episode = request.args.get('episode', type=int)
        tmdb_id = request.args.get('tmdb_id', type=int)

        if not all([tvdb_id, season is not None, episode is not None]):
            return jsonify({'error': 'Missing required parameters'}), 400

        # Get series from Sonarr — auto-add unmonitored if absent so we can browse
        series, auto_added = _ensure_sonarr_series_for_browse(sonarr, tvdb_id, tmdb_id)
        if not series:
            return jsonify({
                'success': False,
                'message': 'Series could not be added to Sonarr for release search.'
            }), 502

        series_id = series['id']

        # Get the episode. After a fresh add Sonarr populates episodes via an
        # async RefreshSeries, so poll briefly for the record to appear.
        episode_data = sonarr.get_episode_by_series_and_numbers(series_id, season, episode)
        if not episode_data and auto_added:
            for _ in range(8):
                time.sleep(0.5)
                episode_data = sonarr.get_episode_by_series_and_numbers(series_id, season, episode)
                if episode_data:
                    break
        if not episode_data:
            return jsonify({
                'success': False,
                'message': f'Episode S{season:02d}E{episode:02d} not found'
            }), 404

        episode_id = episode_data['id']

        # Search for releases
        releases = sonarr.search_episode_releases(episode_id)

        # Format releases for frontend. Rejected releases are kept (with
        # reasons) so a manual grab is still possible, same as Sonarr's UI;
        # full-season packs are skipped (season Sources shows those).
        formatted_releases = []
        rejected_count = 0
        season_pack_count = 0
        for release in releases:
            if release.get('rejected', False):
                rejected_count += 1
            # Skip full-season packs — episode search should only show episode-level results
            if release.get('fullSeason', False):
                season_pack_count += 1
                continue
            formatted_releases.append({
                'guid': release.get('guid'),
                'indexer_id': release.get('indexerId'),
                'indexer': release.get('indexer'),
                'title': release.get('title'),
                'size': release.get('size', 0),
                'quality': release.get('quality', {}).get('quality', {}).get('name'),
                'seeders': release.get('seeders'),
                'leechers': release.get('leechers'),
                'proper': release.get('isProper', False),
                'age': release.get('age'),
                'publishDate': release.get('publishDate'),
                'ageMinutes': release.get('ageMinutes'),
                'protocol': release.get('protocol', 'torrent'),
                'rejected': release.get('rejected', False),
                'rejection_reasons': _sonarr_rejection_reasons(release),
            })

        logger.info(f"Found {len(formatted_releases)} episode releases for S{season:02d}E{episode:02d} (rejected={rejected_count}, season_packs={season_pack_count})")

        return jsonify({
            'success': True,
            'releases': formatted_releases,
            'episode_id': episode_id,
            'series_id': series_id,
            'auto_added': auto_added,
            'total_releases': len(formatted_releases)
        })

    except Exception as e:
        logger.error(f"Error searching for episode: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sonarr/episode/download', methods=['POST'])
def sonarr_download_episode():
    """Download a specific release for an episode or season"""
    denied = _require_admin()
    if denied:
        return denied
    try:
        sonarr = get_sonarr_for_request()

        if not sonarr:
            return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503

        data = request.get_json() or {}
        guid = data.get('guid')
        indexer_id = data.get('indexer_id')
        # Optional parameters for cache refresh
        series_id = data.get('series_id')
        season = data.get('season')
        episode_id = data.get('episode_id')

        if not all([guid, indexer_id is not None]):
            return jsonify({'error': 'Missing required parameters (guid, indexer_id)'}), 400

        logger.info(f"Downloading release: guid={guid}, indexer_id={indexer_id}, series_id={series_id}, season={season}")

        # Download the release (with cache refresh if series_id/season provided)
        result = sonarr.download_release(
            guid=guid,
            indexer_id=indexer_id,
            series_id=series_id,
            season=season,
            episode_id=episode_id
        )

        if result:
            logger.info(f"Download started successfully")
            try:
                from app import sonarr_status as _ss
                _ss.invalidate(series_id)
            except Exception:
                pass
            # Sources-panel download → user-initiated. Record for the activity log.
            try:
                _record_manual_grab(data.get('tmdb_id'), data.get('title', ''), 'tv')
            except Exception as exc:
                logger.warning(f"Manual-grab record failed: {exc}")
            _record_origin('manual', guid=guid, info_hash=data.get('info_hash') or '',
                           download_url=data.get('download_url') or '',
                           tmdb_id=data.get('tmdb_id'), media_type='tv',
                           title=data.get('title', ''), series_id=series_id,
                           episode_ids=[episode_id] if episode_id else None)
            return jsonify({
                'success': True,
                'message': 'Download started successfully'
            })
        else:
            logger.error(f"Failed to start download")
            return jsonify({
                'success': False,
                'message': 'Failed to start download'
            }), 500

    except Exception as e:
        logger.error(f"Error downloading release: {e}")
        return jsonify({'error': str(e)}), 500

# ---------------------------------------------------------------------------
# Web Push notification endpoints
# ---------------------------------------------------------------------------

@app.route('/api/push/vapid-key')
def push_vapid_key():
    """Return VAPID public key for push subscription."""
    try:
        from app.push_service import ensure_vapid_keys, get_subscription_count
        sm = SettingsManager()
        enabled = sm.get('notifications.web_push.enabled') or False
        pub, _ = ensure_vapid_keys(sm)
        return jsonify({
            'publicKey': pub,
            'enabled': enabled,
            'subscriptionCount': get_subscription_count(),
        })
    except Exception as e:
        logger.error(f"Error getting VAPID key: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    """Store a browser push subscription. Auto-enables web push on first subscribe."""
    try:
        from app.push_service import add_subscription
        info = request.get_json()
        if not info or not info.get('endpoint'):
            return jsonify({'error': 'Invalid subscription'}), 400
        user = _get_current_user()
        user_id = user['id'] if user else 0
        add_subscription(user_id, info)
        # Auto-enable web push when first device subscribes
        sm = SettingsManager()
        if not sm.get('notifications.web_push.enabled'):
            sm.update({"notifications": {"web_push": {"enabled": True}}})
            logger.info("Web Push auto-enabled on first subscription")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error saving push subscription: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    """Remove a push subscription."""
    try:
        from app.push_service import remove_subscription
        data = request.get_json()
        endpoint = data.get('endpoint', '') if data else ''
        remove_subscription(endpoint)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error removing push subscription: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/push/test', methods=['POST'])
def push_test():
    """Send a test push notification to all subscribers."""
    try:
        from app.push_service import send_push_notification, get_subscription_count
        count = get_subscription_count()
        if count == 0:
            return jsonify({'success': False, 'message': 'No subscribers'}), 200
        sent = send_push_notification(
            title='Test Movie (2025) added!',
            body='Tap to view details',
            tag='test',
            url='/',
        )
        return jsonify({'success': sent, 'message': f'Sent to {count} subscriber(s)' if sent else 'Push disabled'})
    except Exception as e:
        logger.error(f"Error sending test push: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sonarr/webhook', methods=['POST'])
def sonarr_webhook():
    """
    Handle Sonarr webhook events for episode imports
    Sonarr sends webhook when episodes are downloaded and imported
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid request body'}), 400
        event_type = data.get('eventType')

        logger.info(f"Received Sonarr webhook: eventType={event_type}")
        logger.debug(f"Webhook payload: {json.dumps(data, indent=2)}")

        # Handle test events — fire a Luma push so the user can confirm delivery
        if event_type == 'Test':
            logger.info("Test webhook received from Sonarr — sending Luma push test")
            try:
                from app.push_service import send_push_notification
                send_push_notification(
                    title='Sonarr connected',
                    body='Luma will notify you when episodes finish downloading.',
                    tag='sonarr-test',
                    url='/',
                )
            except Exception as exc:
                logger.warning(f"Sonarr test push failed: {exc}")
            return jsonify({'success': True, 'message': 'Test push sent'}), 200

        # We only care about successful imports (when file is added to library)
        if event_type == 'Download':
            # Extract series information
            series = data.get('series', {})
            series_title = series.get('title', 'Unknown Show')
            series_year = series.get('year', '')

            # Sonarr sometimes includes year in the title for disambiguation
            # (e.g. "Paradise (2025)"). Strip it to avoid double-year display.
            if series_year:
                import re
                series_title = re.sub(r'\s*\(' + str(series_year) + r'\)\s*$', '', series_title)
            series_tvdb_id = series.get('tvdbId', '')
            series_imdb_id = series.get('imdbId', '')
            series_path = series.get('path', '')

            # Get episode information (Sonarr sends info about all episodes in the download)
            episodes = data.get('episodes', [])
            episode_count = len(episodes)
            ep_season = episodes[0].get('seasonNumber') if episodes else None
            # Single episode → store episode number for deep linking to episode view
            ep_number = episodes[0].get('episodeNumber') if episode_count == 1 else None
            # Episode title (only meaningful for single-episode imports)
            ep_title = episodes[0].get('title') if episode_count == 1 else None
            series_tmdb = series.get('tmdbId')
            sonarr_series_id = series.get('id')

            logger.info(f"📺 Series: {series_title} ({series_year})")
            logger.info(f"📦 Episodes imported: {episode_count}")

            # Build notification record
            record = {
                'title': series_title,
                'year': str(series_year) if series_year else '',
                'media_type': 'tv',
                'dest_path': series_path,
                'completed_timestamp': time.time(),
                'source': 'sonarr',
                'season_number': ep_season,
                'episode_number': ep_number,
                'episode_title': ep_title,
                'tmdb_id': series_tmdb,
                'sonarr_series_id': sonarr_series_id,
            }

            # Build external notification sender (Luma web push)
            imdb_id = f"tt{series_imdb_id}" if series_imdb_id and not series_imdb_id.startswith('tt') else series_imdb_id
            # Capture values for the closure
            _series_images = series.get('images', [])
            _display = series_title  # TV shows omit year in notifications
            if ep_number and ep_season:
                _season_label = f'S{ep_season}E{ep_number} added!'
            elif ep_season:
                _season_label = f'S{ep_season} added!'
            else:
                _season_label = 'New episodes added!'
            if series_tmdb and ep_season and ep_number:
                _push_url = f'/?modal=tv&tmdb={series_tmdb}&season={ep_season}&episode={ep_number}'
            elif series_tmdb and ep_season:
                _push_url = f'/?modal=tv&tmdb={series_tmdb}&season={ep_season}'
            elif series_tmdb:
                _push_url = f'/?modal=tv&tmdb={series_tmdb}'
            else:
                _push_url = '/'
            _push_body = 'Tap to view episode' if ep_number else 'Tap to view season details'

            def _send_sonarr_notifications():
                try:
                    from app.push_service import send_push_to_user
                    backdrop_url = ''
                    for img in _series_images:
                        if img.get('coverType') == 'fanart' and img.get('remoteUrl'):
                            url = img.get('remoteUrl', '')
                            # Validate image URL: must start with http, not too long
                            if url and url.startswith(('http://', 'https://')) and len(url) < 2048:
                                backdrop_url = url
                            break

                    # Build push notification body - multi-episode imports need proper context
                    if ep_number and ep_season:
                        if ep_title:
                            _push_notification_body = f'S{ep_season:02d}E{ep_number:02d} - {ep_title} is ready on Luma!'
                        else:
                            _push_notification_body = f'S{ep_season:02d}E{ep_number:02d} is ready on Luma!'
                    elif ep_season and episode_count > 1:
                        # Multi-episode import: include season and episode count for context
                        _push_notification_body = f'Season {ep_season} ({episode_count} episodes) is ready on Luma!'
                    elif ep_season:
                        _push_notification_body = f'Season {ep_season} is ready on Luma!'
                    else:
                        _push_notification_body = _push_body

                    # Use sanitized tag (remove special chars that might cause issues)
                    safe_tag = f"tv-{series_title.replace(' ', '_')}" if series_title else "tv-show"

                    # Send per-user push: resolve which users own this Sonarr instance
                    sonarr_url = settings.get('sonarr.url', '')
                    target_users = _resolve_users_for_arr_url(sonarr_url)
                    for uid in target_users:
                        send_push_to_user(
                            user_id=uid,
                            title=_display,
                            body=_push_notification_body,
                            tag=safe_tag,
                            url=_push_url,
                            image=backdrop_url,
                        )
                except Exception as exc:
                    logger.warning(f"Failed to send push notification for {series_title}: {exc}")

            # Wait for Plex to scan the media before notifying
            plex_kwargs = {
                'title': series_title,
                'media_type': 'tv',
                'year': str(series_year) if series_year else '',
                'season': ep_season,
                'episode': ep_number,
            }
            t = _threading.Thread(
                target=_delayed_webhook,
                args=(record, _send_sonarr_notifications, plex_kwargs),
                daemon=True,
            )
            t.start()

            return jsonify({'success': True, 'message': 'Webhook processed'}), 200
        else:
            # Acknowledge other event types but don't process them
            logger.debug(f"Ignoring webhook event type: {event_type}")
            return jsonify({'success': True, 'message': f'Event type {event_type} not processed'}), 200

    except Exception as e:
        logger.error(f"Error processing Sonarr webhook: {e}")
        # Return 200 anyway so Sonarr doesn't think the webhook failed
        return jsonify({'success': False, 'error': str(e)}), 200

@app.route('/api/radarr/webhook', methods=['POST'])
def radarr_webhook():
    """
    Handle Radarr webhook events for movie imports.
    Configure in Radarr: Settings > Connect > Webhook, event "On Import".
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid request body'}), 400
        event_type = data.get('eventType')

        logger.info(f"Received Radarr webhook: eventType={event_type}")
        logger.debug(f"Radarr webhook payload: {json.dumps(data, indent=2)}")

        if event_type == 'Test':
            logger.info("Test webhook received from Radarr — sending Luma push test")
            try:
                from app.push_service import send_push_notification
                send_push_notification(
                    title='Radarr connected',
                    body='Luma will notify you when movies finish downloading.',
                    tag='radarr-test',
                    url='/',
                )
            except Exception as exc:
                logger.warning(f"Radarr test push failed: {exc}")
            return jsonify({'success': True, 'message': 'Test push sent'}), 200

        if event_type == 'Download':
            movie = data.get('movie', {})
            movie_title = movie.get('title', 'Unknown Movie')
            movie_year = str(movie.get('year', ''))
            tmdb_id = movie.get('tmdbId')
            imdb_id = movie.get('imdbId', '')
            movie_file = data.get('movieFile', {})
            dest_path = movie_file.get('path', movie.get('folderPath', ''))

            logger.info(f"Movie imported: {movie_title} ({movie_year})")

            # Build notification record
            record = {
                'title': movie_title,
                'year': movie_year,
                'media_type': 'movie',
                'tmdb_id': tmdb_id,
                'imdb_id': imdb_id,
                'dest_path': dest_path,
                'completed_timestamp': time.time(),
                'source': 'radarr',
            }

            # Build external notification sender (Luma web push)
            _movie_images = movie.get('images', [])

            def _send_radarr_notifications():
                try:
                    from app.push_service import send_push_to_user
                    backdrop_url = ''
                    for img in _movie_images:
                        if img.get('coverType') == 'fanart' and img.get('remoteUrl'):
                            backdrop_url = img['remoteUrl']
                            break
                    # Send per-user push: resolve which users own this Radarr instance
                    radarr_url = settings.get('radarr.url', '')
                    target_users = _resolve_users_for_arr_url(radarr_url)
                    for uid in target_users:
                        send_push_to_user(
                            user_id=uid,
                            title=f'{movie_title} ({movie_year}) added!',
                            body='Tap to view details',
                            tag=f'movie-{tmdb_id or movie_title}',
                            url=f'/?modal=movie&tmdb={tmdb_id}' if tmdb_id else '/',
                            image=backdrop_url,
                        )
                except Exception:
                    pass

            # Wait for Plex to scan the media before notifying
            plex_kwargs = {
                'title': movie_title,
                'media_type': 'movie',
                'year': movie_year,
                'tmdb_id': tmdb_id,
            }
            t = _threading.Thread(
                target=_delayed_webhook,
                args=(record, _send_radarr_notifications, plex_kwargs),
                daemon=True,
            )
            t.start()

            return jsonify({'success': True, 'message': 'Webhook processed'}), 200

        # Acknowledge other event types
        logger.debug(f"Ignoring Radarr webhook event type: {event_type}")
        return jsonify({'success': True, 'message': f'Event type {event_type} not processed'}), 200

    except Exception as e:
        logger.error(f"Error processing Radarr webhook: {e}")
        return jsonify({'success': False, 'error': str(e)}), 200

@app.route('/api/sonarr/season/search')
def sonarr_search_season():
    """Interactive search for season releases (like Sonarr's UI)"""
    denied = _require_admin()
    if denied:
        return denied
    try:
        sonarr = get_sonarr_for_request()

        if not sonarr:
            return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503

        tvdb_id = request.args.get('tvdb_id', type=int)
        season = request.args.get('season', type=int)
        tmdb_id = request.args.get('tmdb_id', type=int)  # Get TMDB ID as fallback

        if not all([tvdb_id, season is not None]):
            return jsonify({'error': 'Missing required parameters'}), 400

        # Get series from Sonarr — auto-add unmonitored if absent so we can browse
        series, auto_added = _ensure_sonarr_series_for_browse(sonarr, tvdb_id, tmdb_id)
        if not series:
            return jsonify({
                'success': False,
                'message': 'Series could not be added to Sonarr for release search.',
                'releases': []
            }), 502

        series_id = series['id']

        # Get available releases for the season (interactive search)
        raw_releases = sonarr.search_season_releases(series_id, season, include_all=True)

        # Normalize to match episode search format
        formatted_releases = []
        for release in raw_releases:
            formatted_releases.append({
                'guid': release.get('guid'),
                'indexer_id': release.get('indexerId'),
                'indexer': release.get('indexer'),
                'title': release.get('title'),
                'size': release.get('size', 0),
                'quality': release.get('quality', {}).get('quality', {}).get('name'),
                'seeders': release.get('seeders'),
                'leechers': release.get('leechers'),
                'proper': release.get('isProper', False),
                'age': release.get('age'),
                'publishDate': release.get('publishDate'),
                'ageMinutes': release.get('ageMinutes'),
                'fullSeason': release.get('fullSeason', False),
                'protocol': release.get('protocol', 'torrent'),
                'rejected': release.get('rejected', False),
                'rejection_reasons': _sonarr_rejection_reasons(release),
            })

        return jsonify({
            'success': True,
            'releases': formatted_releases,
            'series_id': series_id,
            'season': season,
            'auto_added': auto_added,
        })

    except Exception as e:
        logger.error(f"Error searching for season: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sonarr/search/season', methods=['POST'])
def sonarr_auto_search_season():
    """Trigger Sonarr automatic season search (lets Sonarr pick best release by
    quality profile and indexer priority via RSS/auto logic)."""
    try:
        sonarr = get_sonarr_for_request()

        if not sonarr:
            return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503

        data = request.get_json() or {}
        tvdb_id = data.get('tvdb_id')
        tmdb_id = data.get('tmdb_id')
        season = data.get('season')

        if not tvdb_id or season is None:
            return jsonify({'error': 'Missing required parameters (tvdb_id, season)'}), 400

        # Get series from Sonarr — auto-add if missing
        series_list = sonarr.get_series(tvdb_id, tmdb_id)
        if not series_list:
            logger.info(f"Series tvdb={tvdb_id} not in Sonarr, auto-adding for season search")
            profiles = sonarr.get_quality_profiles()
            if not profiles:
                return jsonify({'error': 'Sonarr has no quality profiles configured'}), 500

            profile_id = _resolve_sonarr_profile_id(profiles)

            title = data.get('title', 'Unknown')
            add_result = sonarr.add_series(
                tvdb_id=tvdb_id,
                title=title,
                quality_profile_id=profile_id,
                monitor='none',
                search_for_missing=False,
            )
            if not add_result:
                return jsonify({'error': 'Failed to auto-add series to Sonarr'}), 500

            series_list = [add_result]
            logger.info(f"Auto-added series '{title}' to Sonarr (id={add_result['id']})")

        series_id = series_list[0]['id']

        # Rescan disk first so Sonarr detects any files deleted outside its UI
        sonarr.rescan_series(series_id)
        time.sleep(2)  # give Sonarr a moment to finish the rescan

        # Ensure the season is monitored
        if not sonarr.ensure_season_monitored(series_id, season):
            return jsonify({
                'success': False,
                'message': f'Failed to monitor season {season}'
            }), 500

        # Let Sonarr handle the search — it uses indexer priority + quality profile
        result = sonarr.search_season_command(series_id, season)
        if result:
            logger.info(f"SeasonSearch command triggered: series={series_id}, season={season}")
            return jsonify({
                'success': True,
                'message': f'Season {season} search started',
                'command_id': result.get('id')
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to trigger season search'
            }), 500

    except Exception as e:
        logger.error(f"Error in auto season search: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sonarr/search/episode', methods=['POST'])
def sonarr_auto_search_episode():
    """Trigger Sonarr automatic episode search (picks best release by quality profile)."""
    try:
        sonarr = get_sonarr_for_request()

        if not sonarr:
            return jsonify({'error': 'Sonarr is not configured', 'needs_setup': 'sonarr'}), 503

        data = request.get_json() or {}
        tvdb_id = data.get('tvdb_id')
        tmdb_id = data.get('tmdb_id')
        season = data.get('season')
        episode = data.get('episode')

        if not all([tvdb_id, season is not None, episode is not None]):
            return jsonify({'error': 'Missing required parameters (tvdb_id, season, episode)'}), 400

        # Get series from Sonarr — auto-add if missing
        series_list = sonarr.get_series(tvdb_id, tmdb_id)
        if not series_list:
            logger.info(f"Series tvdb={tvdb_id} not in Sonarr, auto-adding for episode search")
            profiles = sonarr.get_quality_profiles()
            if not profiles:
                return jsonify({'error': 'Sonarr has no quality profiles configured'}), 500

            profile_id = _resolve_sonarr_profile_id(profiles)

            title = data.get('title', 'Unknown')
            add_result = sonarr.add_series(
                tvdb_id=tvdb_id,
                title=title,
                quality_profile_id=profile_id,
                monitor='none',
                search_for_missing=False,
            )
            if not add_result:
                return jsonify({'error': 'Failed to auto-add series to Sonarr'}), 500

            series_list = [add_result]
            logger.info(f"Auto-added series '{title}' to Sonarr (id={add_result['id']})")

        series_id = series_list[0]['id']

        # Find the episode
        episode_data = sonarr.get_episode_by_series_and_numbers(series_id, season, episode)
        if not episode_data:
            return jsonify({
                'success': False,
                'message': f'Episode S{season:02d}E{episode:02d} not found in Sonarr'
            }), 404

        episode_id = episode_data['id']

        # Ensure the episode is monitored
        if not sonarr.ensure_episode_monitored(episode_id):
            return jsonify({
                'success': False,
                'message': f'Failed to monitor episode S{season:02d}E{episode:02d}'
            }), 500

        # Trigger the auto search
        result = sonarr.search_episode_command([episode_id])
        if result:
            logger.info(f"Auto episode search triggered: episode_id={episode_id}")
            return jsonify({
                'success': True,
                'message': f'S{season:02d}E{episode:02d} search started',
                'command_id': result.get('id')
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to trigger episode search'
            }), 500

    except Exception as e:
        logger.error(f"Error in auto episode search: {e}")
        return jsonify({'error': str(e)}), 500

# TV Show Endpoints
_tv_seasons_cache: Dict[int, tuple] = {}   # tmdb_id → (timestamp, result)
_TV_SEASONS_CACHE_TTL = 3600               # 1 hour — season/episode metadata is near-static
_TV_SEASONS_CACHE_MAX = 300


def _library_episodes(tvdb_id, tmdb_id):
    """Every episode your library knows about, keyed (season, episode).

    Plex and Sonarr are both TVDB-numbered — Sonarr writes the files Plex
    imports — so their episode lists union cleanly with no reconciliation.
    Neither is complete alone: 31 shows here are in Plex but not Sonarr, and
    28 more have episodes Plex holds that Sonarr has never heard of, so a
    single-source grid either loses whole shows or hides owned episodes.

    Plex contributes ownership + the rating_key that makes Play work; Sonarr
    contributes episodes that exist but aren't downloaded yet, so gaps stay
    visible and searchable.
    """
    lib = {}

    # --- Sonarr first: the "what exists" skeleton, incl. not-yet-downloaded ---
    try:
        sonarr = get_sonarr_for_request()
        if sonarr and sonarr.enabled:
            matches = sonarr.get_series(tvdb_id=tvdb_id, tmdb_id=tmdb_id)
            if matches:
                for ep in sonarr.get_episodes(matches[0]['id']):
                    s, e = ep.get('seasonNumber'), ep.get('episodeNumber')
                    if s is None or e is None:
                        continue
                    lib[(s, e)] = {
                        'air_date': ep.get('airDate'),
                        'title': ep.get('title'),
                        'overview': ep.get('overview') or '',
                        'runtime': ep.get('runtime') or None,
                        'rating_key': None,
                    }
    except Exception as e:
        logger.warning(f"Season grid: Sonarr unavailable for tmdb={tmdb_id}: {e}")

    # --- Plex second: authoritative for what is actually on disk ---
    try:
        index = plex_service.build_ownership_index()
        entry = (index['tv_by_tvdb'].get(tvdb_id) if tvdb_id else None) \
            or (index['tv_by_tmdb'].get(tmdb_id) if tmdb_id else None)
        if entry:
            for ep in plex_service.get_show_episodes(entry['rating_key']):
                s, e = ep.get('season_number'), ep.get('episode_number')
                if s is None or e is None:
                    continue
                row = lib.setdefault((s, e), {'air_date': None, 'title': None,
                                              'overview': '', 'runtime': None,
                                              'rating_key': None})
                row['rating_key'] = ep.get('rating_key')
                row['air_date'] = row['air_date'] or ep.get('originally_available_at')
                row['title'] = row['title'] or ep.get('title')
                row['overview'] = row['overview'] or ep.get('overview') or ''
                row['runtime'] = row['runtime'] or ep.get('runtime')
    except Exception as e:
        logger.warning(f"Season grid: Plex unavailable for tmdb={tmdb_id}: {e}")

    return lib


def _build_season_grid(tmdb_seasons, tvdb_id, tmdb_id):
    """Build the episode grid from the library, decorated with TMDb metadata.

    The grid used to come from TMDb with ownership painted on top, which breaks
    whenever TMDb partitions a show differently from TVDB: Hard Knocks is 21
    seasons on TMDb but 27 in Plex and Sonarr, so the newest episode had no row
    to live in and read as missing. Inverting it — library defines structure,
    TMDb decorates — removes that whole class of bug, because the grid is now
    in the same numbering as the ownership data and the playback rating_keys.

    TMDb episodes are matched by air date when that date is unique. When an
    entire binge-drop season shares one air date (e.g. The Secret Lives of
    Mormon Wives), unique-date matching would otherwise discard every still and
    season poster — fall back to (season, episode) coordinates, then to same
    episode_number among same-date candidates. Season posters fall back to the
    TMDb season with the same season_number when date mapping is empty.

    Falls back to the TMDb grid untouched for shows in neither Plex nor Sonarr.
    """
    lib = _library_episodes(tvdb_id, tmdb_id)
    if not lib:
        return tmdb_seasons

    # Index TMDb episodes for decoration. Unique air dates are preferred for
    # renumbered shows (Hard Knocks); colliding dates keep a candidate list so
    # binge drops can still resolve via season/episode coordinates.
    by_date_unique = {}
    by_date_all = {}
    by_coord = {}
    for season in tmdb_seasons:
        for ep in (season.get('episodes') or []):
            coord = (season.get('season_number'), ep.get('episode_number'))
            by_coord[coord] = (season, ep)
            air = ep.get('air_date')
            if not air:
                continue
            by_date_all.setdefault(air, []).append((season, ep))
    for air, items in by_date_all.items():
        if len(items) == 1:
            by_date_unique[air] = items[0]

    def _match_tmdb(s_num, e_num, air_date):
        if air_date and air_date in by_date_unique:
            return by_date_unique[air_date]
        if (s_num, e_num) in by_coord:
            return by_coord[(s_num, e_num)]
        if air_date and air_date in by_date_all:
            cands = by_date_all[air_date]
            same = [c for c in cands
                    if c[0].get('season_number') == s_num
                    and c[1].get('episode_number') == e_num]
            if len(same) == 1:
                return same[0]
            same_ep = [c for c in cands if c[1].get('episode_number') == e_num]
            if len(same_ep) == 1:
                return same_ep[0]
        return (None, None)

    grid = {}
    for (s_num, e_num), row in lib.items():
        t_season, t_ep = _match_tmdb(s_num, e_num, row.get('air_date'))
        t_ep = t_ep or {}
        grid.setdefault(s_num, []).append({
            'episode_number': e_num,
            'name': t_ep.get('name') or row['title'] or f'Episode {e_num}',
            'overview': t_ep.get('overview') or row['overview'] or '',
            'air_date': row['air_date'],
            'still_path': t_ep.get('still_path'),
            'runtime': t_ep.get('runtime') or row['runtime'],
            '_tmdb_season': (t_season or {}).get('season_number'),
        })

    # Append TMDb-only episodes — genuinely upcoming ones the library hasn't
    # learned about yet. A dated episode is safe to place whenever its date
    # isn't already covered. A *dateless* one (TBA, common for unannounced
    # episodes of an upcoming season) has no key to match on, so it's only safe
    # where TMDb and the library agree on numbering: if this TMDb season's
    # dated episodes landed on a different library season, the season has been
    # re-numbered and its coordinates mean something else entirely.
    covered_dates = {r['air_date'] for r in lib.values() if r['air_date']}
    lib_season_by_date = {}
    for (s_num, _), row in lib.items():
        if row['air_date']:
            # Prefer a unique library season for this date; collisions stay ambiguous.
            if row['air_date'] in lib_season_by_date and lib_season_by_date[row['air_date']] != s_num:
                lib_season_by_date[row['air_date']] = None
            else:
                lib_season_by_date.setdefault(row['air_date'], s_num)

    for season in tmdb_seasons:
        s_no = season.get('season_number')
        episodes = season.get('episodes') or []
        landed = {lib_season_by_date[e['air_date']] for e in episodes
                  if e.get('air_date') and e['air_date'] in lib_season_by_date
                  and lib_season_by_date[e['air_date']] is not None}
        renumbered = bool(landed) and landed != {s_no}
        for ep in episodes:
            air = ep.get('air_date')
            coord = (s_no, ep.get('episode_number'))
            if coord in lib:
                continue
            if (air in covered_dates) if air else renumbered:
                continue
            grid.setdefault(coord[0], []).append({
                'episode_number': coord[1],
                'name': ep.get('name') or f'Episode {coord[1]}',
                'overview': ep.get('overview') or '',
                'air_date': air,
                'still_path': ep.get('still_path'),
                'runtime': ep.get('runtime'),
                '_tmdb_season': season.get('season_number'),
            })

    out = []
    for s_num in sorted(grid):
        episodes = sorted(grid[s_num], key=lambda x: x['episode_number'])
        # Borrow the TMDb season's name/poster when this season's episodes map
        # onto exactly one TMDb season. If date matching found nothing (common
        # for same-day binge drops before coord fallback), use same-number.
        mapped = {e.pop('_tmdb_season') for e in episodes}
        mapped.discard(None)
        if len(mapped) == 1:
            src = next((s for s in tmdb_seasons
                        if s.get('season_number') == next(iter(mapped))), None)
        elif len(mapped) == 0:
            src = next((s for s in tmdb_seasons
                        if s.get('season_number') == s_num), None)
        else:
            src = None
        dated = [e['air_date'] for e in episodes if e.get('air_date')]
        out.append({
            'season_number': s_num,
            'name': (src or {}).get('name') or f'Season {s_num}',
            'overview': (src or {}).get('overview') or '',
            'air_date': min(dated) if dated else None,
            'poster_path': (src or {}).get('poster_path'),
            'episode_count': len(episodes),
            'episodes': episodes,
            'partial': False,
        })
    return out



def _plex_tv_seasons(tmdb_id: int, light: bool = False) -> Optional[Dict]:
    """Seasons/episodes payload (same shape as the TMDb one) built from Plex."""
    try:
        index = plex_service.build_ownership_index() or {}
    except Exception:
        return None
    entry = (index.get('tv_by_tmdb') or {}).get(int(tmdb_id)) or {}
    rk = entry.get('rating_key')
    if not rk:
        return None
    rk = int(rk)
    by_season: Dict[int, List[Dict]] = {}
    if not light:
        for ep in plex_service.get_show_episodes(rk) or []:
            by_season.setdefault(ep.get('season_number'), []).append({
                'episode_number': ep.get('episode_number'),
                'name': ep.get('title'),
                'overview': ep.get('overview') or '',
                'air_date': ep.get('originally_available_at'),
                'still_path': _plex_img(f"/library/metadata/{ep['rating_key']}/thumb") if ep.get('rating_key') else None,
                'runtime': ep.get('runtime'),
            })
    seasons_info = []
    for se in plex_service.get_show_children(rk) or []:
        num = se.get('index')
        eps = by_season.get(num, [])
        seasons_info.append({
            'season_number': num,
            'name': se.get('title') or (f'Season {num}' if num else 'Specials'),
            'overview': '',
            'air_date': None,
            'poster_path': _plex_img(se.get('thumbPath')),
            'episode_count': len(eps) or se.get('leafCount', 0),
            'episodes': eps,
            'partial': False,
        })
    tvdb_id = entry.get('tvdb_id')
    if not light and seasons_info:
        try:
            seasons_info = _build_season_grid(seasons_info, tvdb_id, tmdb_id)
        except Exception as e:
            logger.debug(f"Plex seasons: grid decoration skipped: {e}")
    return {'success': True, 'tmdb_id': tmdb_id, 'tvdb_id': tvdb_id, 'seasons': seasons_info,
            'light': light, 'source': 'plex'}


@app.route('/api/tv/seasons/<int:tmdb_id>')
def get_tv_seasons(tmdb_id):
    """Get TV show seasons and episodes from TMDB (cached 1h).

    A single TV modal open triggers 1+N TMDb calls; caching avoids re-fetching
    on every open and reduces the rate-limiting that made whole seasons vanish.

    Pass ?light=1 for season posters/names/counts only (no per-season episode
    fetches, no library grid). Used to unblock the Seasons button immediately.
    """
    try:
        import requests
        from concurrent.futures import ThreadPoolExecutor

        def _light_payload(tv_data):
            seasons_info = []
            for s in tv_data.get('seasons', []) or []:
                seasons_info.append({
                    'season_number': s.get('season_number'),
                    'name': s.get('name'),
                    'overview': s.get('overview') or '',
                    'air_date': s.get('air_date'),
                    'poster_path': s.get('poster_path'),
                    'episode_count': s.get('episode_count', 0),
                    'episodes': [],
                    'partial': False,
                })
            ext = tv_data.get('external_ids') or {}
            return {
                'success': True,
                'tmdb_id': tmdb_id,
                'tvdb_id': ext.get('tvdb_id'),
                'seasons': seasons_info,
                'light': True,
            }

        light = request.args.get('light') == '1'

        # Full cache hit — light callers can use it too (already has summaries).
        cached = _tv_seasons_cache.get(tmdb_id)
        if cached:
            ts, data = cached
            if time.time() - ts < _TV_SEASONS_CACHE_TTL:
                return jsonify(data)

        # Light: prefer the already-warmed /api/tv/<id> detail cache (30m) so
        # opening the modal after show details is essentially free.
        if light:
            detail_key = f"tv:{tmdb_id}"
            with _tmdb_detail_cache_lock:
                detail_cached = _tmdb_detail_cache.get(detail_key)
            if detail_cached:
                dts, ddata = detail_cached
                if time.time() - dts < _TMDB_DETAIL_TTL and ddata.get('seasons') is not None:
                    return jsonify(_light_payload(ddata))

        # Fetch TV show details with all seasons
        tmdb_key = settings.get('apis.tmdb.api_key', '') or Config.TMDB_API_KEY
        if not tmdb_key:
            # No TMDB: owned shows get their seasons and episodes from Plex.
            plex_payload = _plex_tv_seasons(tmdb_id, light)
            if plex_payload:
                return jsonify(plex_payload)
            return jsonify({'error': 'TMDb not configured', 'needs_setup': 'tmdb'}), 404
        url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={tmdb_key}&append_to_response=external_ids"
        response = requests.get(url, timeout=10)

        if response.status_code == 429:
            return jsonify({'error': 'TMDb rate limited',
                            'retry_after': response.headers.get('Retry-After', '5')}), 429
        if not response.ok:
            return jsonify({'error': 'Failed to fetch TV show data'}), 502

        tv_data = response.json()

        # Warm shared detail cache only when it would not clobber a fuller entry.
        # This seasons fetch appends external_ids only — writing it blindly made
        # /api/tv/<id> return show data with no credits (empty Cast & Crew).
        with _tmdb_detail_cache_lock:
            cache_key = f"tv:{tmdb_id}"
            existing = _tmdb_detail_cache.get(cache_key)
            keep_existing = False
            if existing:
                ets, edata = existing
                if time.time() - ets < _TMDB_DETAIL_TTL and isinstance(edata, dict):
                    # Prefer any cached payload that already has credits/videos.
                    if edata.get("credits") is not None or edata.get("videos") is not None:
                        keep_existing = True
            if not keep_existing:
                _tmdb_detail_cache[cache_key] = (time.time(), tv_data)

        if light:
            return jsonify(_light_payload(tv_data))

        # Fetch each season's detail in parallel. A rate-limited/failed season is
        # retried once, then falls back to the show-level summary so it is never
        # silently dropped from the response (it renders flagged `partial`).
        def fetch_season(season_summary):
            season_number = season_summary.get('season_number')
            season_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_number}?api_key={tmdb_key}"
            season_data = None
            for attempt in range(2):
                try:
                    r = requests.get(season_url, timeout=10)
                except requests.exceptions.RequestException:
                    r = None
                if r is not None and r.ok:
                    season_data = r.json()
                    break
                if r is not None and r.status_code == 429 and attempt == 0:
                    time.sleep(1)
                    continue
                break

            partial = season_data is None
            season_data = season_data or {}
            episodes = []
            # Skip guest_stars/crew — unused by the TV modal season/episode grids
            # and dominate payload size on long-running shows.
            for ep in season_data.get('episodes', []):
                episodes.append({
                    'episode_number': ep.get('episode_number'),
                    'name': ep.get('name'),
                    'overview': ep.get('overview'),
                    'air_date': ep.get('air_date'),
                    'still_path': ep.get('still_path'),
                    'runtime': ep.get('runtime'),
                })
            return {
                'season_number': season_number,
                'name': season_data.get('name') or season_summary.get('name'),
                'overview': season_data.get('overview') or season_summary.get('overview'),
                'air_date': season_data.get('air_date') or season_summary.get('air_date'),
                'poster_path': season_data.get('poster_path') or season_summary.get('poster_path'),
                'episode_count': len(episodes) or season_summary.get('episode_count', 0),
                'episodes': episodes,
                'partial': partial,
            }

        seasons_info = []
        if 'seasons' in tv_data:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(fetch_season, tv_data['seasons']))
            seasons_info = [r for r in results if r is not None]

        resolved_tvdb_id = tv_data.get('external_ids', {}).get('tvdb_id')

        # Only build off a complete TMDb response — a rate-limited partial
        # would leave real episodes undecorated (and cache that way for an
        # hour), so fall back to the plain TMDb grid until it resolves.
        if seasons_info and not any(s.get('partial') for s in seasons_info):
            seasons_info = _build_season_grid(
                seasons_info, resolved_tvdb_id, tmdb_id)

        result = {
            'success': True,
            'tmdb_id': tmdb_id,
            'tvdb_id': resolved_tvdb_id,
            'seasons': seasons_info
        }

        # Only cache a fully-resolved response so transient rate-limiting doesn't
        # freeze partial data for an hour.
        if not any(s.get('partial') for s in seasons_info):
            _tv_seasons_cache[tmdb_id] = (time.time(), result)
            if len(_tv_seasons_cache) > _TV_SEASONS_CACHE_MAX:
                oldest = min(_tv_seasons_cache.items(), key=lambda kv: kv[1][0])[0]
                del _tv_seasons_cache[oldest]

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error fetching TV seasons: {e}")
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# TMDb episode detail — cached proxy
# ---------------------------------------------------------------------------
_tmdb_episode_cache: Dict[str, tuple] = {}   # key → (timestamp, data)
_TMDB_EPISODE_CACHE_TTL = 3600               # 1 hour
_TMDB_EPISODE_CACHE_MAX = 500                # max entries


@app.route('/api/tv/<int:tmdb_show_id>/season/<int:season>/episode/<int:episode>')
def tmdb_episode_detail(tmdb_show_id, season, episode):
    """Get episode metadata from TMDb.

    Fetches detailed episode info including credits and external IDs.
    Cached in-memory for 1 hour since episode metadata rarely changes.
    """
    cache_key = f"tmdb_ep:{tmdb_show_id}:s{season}e{episode}"

    # Check cache
    cached = _tmdb_episode_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _TMDB_EPISODE_CACHE_TTL:
            return jsonify(data)
        del _tmdb_episode_cache[cache_key]

    tmdb_api_key = settings.get('apis.tmdb.api_key', '') or Config.TMDB_API_KEY
    if not tmdb_api_key:
        return jsonify({'error': 'TMDb API key not configured', 'needs_setup': 'tmdb'}), 503

    try:
        resp = requests.get(
            f'https://api.themoviedb.org/3/tv/{tmdb_show_id}/season/{season}/episode/{episode}',
            params={
                'api_key': tmdb_api_key,
                'append_to_response': 'credits,external_ids',
            },
            timeout=10,
        )

        if resp.status_code == 404:
            return jsonify({'error': 'Episode not found'}), 404

        resp.raise_for_status()
        ep_data = resp.json()

        # Build a clean response with only the fields the frontend needs
        payload = {
            'name': ep_data.get('name'),
            'overview': ep_data.get('overview'),
            'air_date': ep_data.get('air_date'),
            'runtime': ep_data.get('runtime'),
            'vote_average': ep_data.get('vote_average'),
            'vote_count': ep_data.get('vote_count'),
            'still_path': ep_data.get('still_path'),
            'season_number': ep_data.get('season_number', season),
            'episode_number': ep_data.get('episode_number', episode),
            'crew': [
                {
                    'id': c.get('id'),
                    'name': c.get('name'),
                    'job': c.get('job'),
                    'department': c.get('department'),
                    'profile_path': c.get('profile_path'),
                }
                for c in (ep_data.get('crew') or (ep_data.get('credits') or {}).get('crew') or [])
            ],
            # Regular episode cast (credits.cast) + guest stars. Episode modal
            # prefers these over series-level credits so special guests show up.
            'cast': [
                {
                    'id': c.get('id'),
                    'name': c.get('name'),
                    'character': c.get('character'),
                    'profile_path': c.get('profile_path'),
                }
                for c in ((ep_data.get('credits') or {}).get('cast') or [])
            ],
            'guest_stars': [
                {
                    'id': gs.get('id'),
                    'name': gs.get('name'),
                    'character': gs.get('character'),
                    'profile_path': gs.get('profile_path'),
                }
                for gs in ep_data.get('guest_stars', [])
            ],
        }

        # Include external IDs if present
        external_ids = ep_data.get('external_ids')
        if external_ids:
            payload['imdb_id'] = external_ids.get('imdb_id')
            payload['tvdb_id'] = external_ids.get('tvdb_id')

        # Cache with max-size eviction
        _tmdb_episode_cache[cache_key] = (time.time(), payload)
        if len(_tmdb_episode_cache) > _TMDB_EPISODE_CACHE_MAX:
            oldest_key = min(_tmdb_episode_cache, key=lambda k: _tmdb_episode_cache[k][0])
            del _tmdb_episode_cache[oldest_key]

        return jsonify(payload)

    except requests.exceptions.HTTPError as e:
        logger.error(f"TMDb episode API error for {tmdb_show_id} S{season}E{episode}: {e}")
        return jsonify({'error': 'Failed to fetch episode data from TMDb'}), 502
    except Exception as e:
        logger.error(f"Error fetching TMDb episode {tmdb_show_id} S{season}E{episode}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/tv/ownership/<int:tvdb_id>/cached')
def get_tv_ownership_cached(tvdb_id):
    """Check which episodes are owned in Plex library (uses HTTP API + cache)."""
    try:
        title = request.args.get('title')
        year = request.args.get('year')
        show_rating_key = request.args.get('show_rating_key', type=int)

        # Key the cache by the authoritative tvdb_id, never by title — two shows
        # sharing a title (e.g. The Office US/UK) would otherwise collide and
        # return each other's owned-episode maps.
        cache_key = f"tvdb_{tvdb_id}"

        force = request.args.get('force', '0') == '1'

        logger.info(f"TV ownership check: tvdb={tvdb_id}, title={title}, year={year}, "
                     f"show_rk={show_rating_key}, force={force}")

        # Check cache first (5-minute TTL) — mirrors the movie get_cached_ownership pattern
        if not force:
            cached_ownership = library_cache.get_tv_episode_ownership(cache_key, max_age_seconds=300)
            if cached_ownership:
                logger.info(f"TV ownership: cache HIT for {cache_key} ({len(cached_ownership)} episodes)")
                return jsonify({
                    'success': True, 'tvdb_id': tvdb_id,
                    'episodes': cached_ownership,
                    'total_owned': len(cached_ownership),
                    'is_owned': len(cached_ownership) > 0,
                    'from_cache': True
                })
            logger.info(f"TV ownership: cache MISS for {cache_key}, fetching from Plex")

        # Find show rating_key. The explicit show_rating_key (passed by the
        # frontend for owned shows) is authoritative; otherwise resolve by the
        # authoritative tvdb_id via the ownership index (cached) BEFORE any
        # title-based lookup, so same-title shows never cross-resolve.
        show_rk = show_rating_key
        if not show_rk:
            try:
                index = plex_service.build_ownership_index(force=force)
                show_entry = index['tv_by_tvdb'].get(tvdb_id)
                if show_entry:
                    show_rk = show_entry['rating_key']
                    logger.info(f"TV ownership: tvdb_id={tvdb_id} resolved → rk={show_rk}")
            except Exception as idx_err:
                logger.warning(f"TV ownership: ownership index unavailable: {idx_err}")
        if not show_rk and title:
            # Title-only fallback (last resort) — used only when the tvdb_id
            # isn't found in the library index.
            fast_results = fast_search_index.search(q=title, media_type='show', limit=10)
            for r in fast_results:
                if r['title'].lower() == title.lower():
                    show_rk = int(r['rating_key'])
                    logger.info(f"TV ownership: title fallback resolved '{title}' → rk={show_rk}")
                    break

        if not show_rk:
            logger.warning(f"TV ownership: no Plex show found for tvdb={tvdb_id}, title={title}")
            return jsonify({
                'success': True, 'tvdb_id': tvdb_id,
                'episodes': {}, 'total_owned': 0, 'is_owned': False, 'from_cache': False
            })

        # Get all episodes via HTTP API
        logger.info(f"TV ownership: fetching episodes for show_rk={show_rk}")
        episodes = plex_service.get_show_episodes(show_rk)
        ownership = {}
        for ep in episodes:
            s = ep.get('season_number')
            e = ep.get('episode_number')
            if s is not None and e is not None:
                key = f"s{s}e{e}"
                ownership[key] = {
                    'owned': True,
                    'rating_key': ep.get('rating_key'),
                    'runtime': ep.get('runtime')
                }

        # Log season breakdown for debugging
        season_counts = {}
        for k in ownership:
            s_num = k.split('e')[0][1:]  # extract season number from "s6e1"
            season_counts[s_num] = season_counts.get(s_num, 0) + 1
        logger.info(f"TV ownership: Plex returned {len(episodes)} episodes, "
                     f"{len(ownership)} owned, seasons: {season_counts}")

        if ownership:
            library_cache.update_tv_episode_ownership(cache_key, ownership)

        return jsonify({
            'success': True, 'tvdb_id': tvdb_id,
            'episodes': ownership,
            'total_owned': len(ownership),
            'is_owned': len(ownership) > 0,
            'from_cache': False
        })

    except Exception as e:
        logger.error(f"Error checking TV ownership (cached): {e}")
        return get_tv_ownership(tvdb_id)

@app.route('/api/tv/ownership/<int:tvdb_id>')
def get_tv_ownership(tvdb_id):
    """Check which episodes are owned in Plex library via HTTP API."""
    try:
        title = request.args.get('title')
        year = request.args.get('year')

        # Find show via ownership index
        index = plex_service.build_ownership_index()
        show_entry = index['tv_by_tvdb'].get(tvdb_id)
        if not show_entry and title:
            key = f"{title.lower()}|{year}" if year else title.lower()
            show_entry = index['tv'].get(key)
            if not show_entry:
                candidates = index['tv_by_title'].get(title.lower(), [])
                show_entry = candidates[0] if candidates else None

        if not show_entry:
            return jsonify({
                'success': True, 'tvdb_id': tvdb_id,
                'episodes': {}, 'total_owned': 0, 'is_owned': False
            })

        episodes = plex_service.get_show_episodes(show_entry['rating_key'])
        ownership = {}
        for ep in episodes:
            s = ep.get('season_number')
            e = ep.get('episode_number')
            if s is not None and e is not None:
                ownership[f"s{s}e{e}"] = {
                    'owned': True,
                    'rating_key': ep.get('rating_key'),
                    'runtime': ep.get('runtime')
                }

        return jsonify({
            'success': True, 'tvdb_id': tvdb_id,
            'episodes': ownership,
            'total_owned': len(ownership),
            'is_owned': len(ownership) > 0
        })

    except Exception as e:
        logger.error(f"Error checking TV ownership: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/tv/show')
def get_tv_show():
    """Get TV show info from Plex by title via ownership index."""
    try:
        title = request.args.get('title')
        year = request.args.get('year')

        if not title:
            return jsonify({'error': 'Missing title parameter'}), 400

        index = plex_service.build_ownership_index()
        key = f"{title.lower()}|{year}" if year else title.lower()
        show_entry = index['tv'].get(key)
        if not show_entry:
            candidates = index['tv_by_title'].get(title.lower(), [])
            show_entry = candidates[0] if candidates else None

        if show_entry:
            return jsonify({
                'success': True,
                'show': {
                    'rating_key': show_entry['rating_key'],
                    'title': show_entry['title'],
                    'year': show_entry['year'],
                }
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Show not found in Plex library'
            }), 404

    except Exception as e:
        logger.error(f"Error getting TV show: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/tv/episodes')
def get_tv_episode():
    """Get specific episode from Plex via HTTP API."""
    try:
        show_rating_key = request.args.get('show_rating_key', type=int)
        season = request.args.get('season', type=int)
        episode = request.args.get('episode', type=int)

        if not all([show_rating_key, season is not None, episode is not None]):
            return jsonify({'error': 'Missing required parameters'}), 400

        # Fetch all episodes for show, find the matching one
        episodes = plex_service.get_show_episodes(show_rating_key)
        matched = None
        for ep in episodes:
            if ep.get('season_number') == season and ep.get('episode_number') == episode:
                matched = ep
                break

        if matched:
            show_meta = plex_service.get_metadata(show_rating_key)
            episode_data = {
                'rating_key': matched['rating_key'],
                'episode_title': matched['title'],
                'episode_number': matched['episode_number'],
                'season_number': matched['season_number'],
                'show_rating_key': show_rating_key,
                'show_title': show_meta.get('title', '') if show_meta else '',
                'show_year': show_meta.get('year') if show_meta else None,
            }

            # Find copies across libraries
            copies = plex_service.find_show_copies(
                episode_data['show_title'],
                tmdb_id=show_meta.get('tmdb_id') if show_meta else None,
                tvdb_id=show_meta.get('tvdb_id') if show_meta else None,
            )
            library_keys = {}
            for copy in copies:
                lib_name = copy.get('library_name', '')
                rk = copy.get('ratingKey') or copy.get('rating_key')
                if lib_name and rk and lib_name not in library_keys:
                    library_keys[lib_name] = matched['rating_key']
            if not library_keys:
                library_keys = {'Library': matched['rating_key']}

            episode_data['libraries'] = list(library_keys.keys())
            episode_data['library_keys'] = library_keys

            return jsonify({'success': True, 'episode': episode_data})
        else:
            return jsonify({
                'success': False,
                'message': f'Episode S{season:02d}E{episode:02d} not found'
            }), 404

    except Exception as e:
        logger.error(f"Error getting TV episode: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ACTIVITY / PLAYBACK MONITORING ENDPOINTS
# ============================================================================

@app.route('/api/plex/clients')
def plex_clients():
    """Get active Plex clients via Plex Server API."""
    try:
        plex_url = settings.get('plex.url')
        plex_token = settings.get('plex.token')
        if not plex_url or not plex_token:
            return jsonify({'error': 'Plex URL/token not configured'}), 500

        from app.plex_client_service import get_active_clients, get_sessions
        clients = get_active_clients(plex_url, plex_token)
        sessions = get_sessions(plex_url, plex_token)
        return jsonify({'clients': clients, 'sessions': sessions,
                        'count': len(clients), 'active_sessions': len(sessions)})
    except Exception as e:
        logger.error(f"Error getting Plex clients: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/plex/client/control', methods=['POST'])
def plex_client_control():
    """Control a Plex client (play/pause/stop/seek/volume)."""
    try:
        data = request.json or {}
        client_name = data.get('client_name', '')
        action = data.get('action', '')
        if not client_name or not action:
            return jsonify({'success': False, 'error': 'client_name and action required'}), 400

        plex_url = settings.get('plex.url')
        plex_token = settings.get('plex.token')
        if not plex_url or not plex_token:
            return jsonify({'success': False, 'error': 'Plex URL/token not configured'}), 500

        # For 'back' action, route to ADB (simpler, works regardless)
        if action == 'back':
            device_ip = data.get('device_ip', '')
            adb_port = int(data.get('adb_port', 5555))
            if device_ip:
                from app.adb_utils import send_key_event
                result = send_key_event(device_ip, adb_port, 'back')
                return jsonify(result)
            return jsonify({'success': False, 'error': 'device_ip required for back action'}), 400

        from app.plex_client_service import control_client
        kwargs = {}
        if action == 'volume_set':
            kwargs['volume_level'] = data.get('volume_level')
        elif action == 'seek':
            kwargs['offset'] = data.get('offset')

        result = control_client(plex_url, plex_token, client_name, action, **kwargs)
        status_code = 200 if result.get('success') else 400
        return jsonify(result), status_code
    except Exception as e:
        logger.error(f"Error controlling Plex client: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/activity')  # alfred asks here first, then falls back to the ADB path
@app.route('/api/adb/shield_activity')
def adb_shield_activity():
    """Get current activity/media playing on a room device via ADB."""
    try:
        room_id = request.args.get('room_id')
        device_ip = request.args.get('device_ip')
        adb_port = settings.get_default_adb_port()

        if room_id:
            room = settings.get_room_by_id(room_id)
            if room:
                device_ip = device_ip or room.get('host', '')
                adb_port = room.get('connectPort', adb_port)
        if not device_ip:
            device_ip = settings.get_default_device_ip()

        # Get current foreground app
        app_cmd = f'adb -s {device_ip}:{adb_port} shell dumpsys window | grep -E "mCurrentFocus|mFocusedApp"'
        app_result = subprocess.run(
            app_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5
        )

        # Get media session info (works for YouTube, Netflix, Plex, Spotify, etc.)
        media_cmd = f'adb -s {device_ip}:{adb_port} shell dumpsys media_session'
        media_result = subprocess.run(
            media_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5
        )

        # Parse current app
        current_app = 'Unknown'
        current_package = ''
        if app_result.returncode == 0:
            output = app_result.stdout
            # Extract package name from mCurrentFocus or mFocusedApp
            import re
            match = re.search(r'([a-z0-9\.]+)/([a-z0-9\.]+)', output)
            if match:
                current_package = match.group(1)
                # Map package names to friendly names
                app_names = {
                    'com.plexapp.android': 'Plex',
                    'com.google.android.youtube.tv': 'YouTube',
                    'com.netflix.ninja': 'Netflix',
                    'com.amazon.avod.thirdpartyclient': 'Prime Video',
                    'com.hulu.plus': 'Hulu',
                    'com.disney.disneyplus': 'Disney+',
                    'com.hbo.hbonow': 'HBO Max',
                    'tv.twitch.android.app': 'Twitch',
                    'com.spotify.tv.android': 'Spotify',
                    'ar.tvplayer.tv': 'TiViMate',
                    'com.google.android.tvlauncher': 'Home Screen',
                    'com.nvidia.shield.apps': 'Shield',
                }
                current_app = app_names.get(current_package, current_package)

        # Parse media session
        media_info = {
            'is_active': False,
            'state': 'stopped',
            'title': None,
            'artist': None,
            'album': None,
            'duration': 0,
            'position': 0,
            'package': current_package,
            'app_name': current_app
        }

        if media_result.returncode == 0:
            lines = media_result.stdout.split('\n')

            # Find active sessions
            current_session = None
            for i, line in enumerate(lines):
                # Look for session header with package name
                if 'package=' in line and i > 0:
                    # Check if this session is active
                    is_active = False
                    session_pkg = None

                    pkg_match = re.search(r'package=([a-z0-9\.]+)', line)
                    if pkg_match:
                        session_pkg = pkg_match.group(1)

                    # Look ahead for active=true
                    for j in range(i, min(len(lines), i+15)):
                        if 'active=true' in lines[j]:
                            is_active = True
                            break

                    if is_active and session_pkg and session_pkg != 'com.google.android.tvlauncher':
                        current_session = session_pkg
                        media_info['package'] = session_pkg
                        media_info['app_name'] = {
                            'com.plexapp.android': 'Plex',
                            'com.google.android.youtube.tv': 'YouTube',
                            'com.netflix.ninja': 'Netflix',
                            'com.amazon.avod.thirdpartyclient': 'Prime Video',
                            'com.hulu.plus': 'Hulu',
                            'com.disney.disneyplus': 'Disney+',
                            'com.spotify.tv.android': 'Spotify',
                        }.get(session_pkg, session_pkg)

                        # Now look for state and metadata in the lines after this
                        for k in range(i, min(len(lines), i+30)):
                            # Parse playback state
                            if 'state=PlaybackState' in lines[k]:
                                state_match = re.search(r'state=(\d+)', lines[k])
                                if state_match:
                                    state_code = int(state_match.group(1))
                                    if state_code == 3:
                                        media_info['state'] = 'playing'
                                        media_info['is_active'] = True
                                    elif state_code == 2:
                                        media_info['state'] = 'paused'
                                        media_info['is_active'] = True

                                # Parse position
                                pos_match = re.search(r'position=(\d+)', lines[k])
                                if pos_match:
                                    media_info['position'] = int(pos_match.group(1))

                                # Parse duration (buffered position in YouTube)
                                buf_match = re.search(r'buffered position=(\d+)', lines[k])
                                if buf_match and int(buf_match.group(1)) > 0:
                                    media_info['duration'] = int(buf_match.group(1))

                            # Parse metadata description (YouTube uses this for title)
                            if 'metadata:' in lines[k] and 'description=' in lines[k]:
                                desc_match = re.search(r'description=([^,]+)', lines[k])
                                if desc_match:
                                    title = desc_match.group(1).strip()
                                    if title and title != 'null':
                                        media_info['title'] = title

                        break  # Found active session, stop looking

            # If no active session found, check Audio playback section for apps like TiViMate
            if not media_info['is_active'] and current_package:
                audio_playback_section = False
                for line in lines:
                    if 'Audio playback' in line:
                        audio_playback_section = True
                    if audio_playback_section and f'packages={current_package}' in line:
                        # This app is actively playing audio
                        media_info['is_active'] = True
                        media_info['state'] = 'playing'
                        # Set a generic title for apps without media sessions
                        if current_package == 'ar.tvplayer.tv':
                            media_info['title'] = 'Watching Live TV'
                        else:
                            media_info['title'] = f'Playing in {current_app}'
                        break

        # Calculate progress percentage
        if media_info['duration'] > 0:
            media_info['progress_percent'] = (media_info['position'] / media_info['duration']) * 100
        else:
            media_info['progress_percent'] = 0

        # Try to get metadata from Plex sessions API if Plex is playing
        media_info['artwork_url'] = None
        media_info['backdrop_url'] = None
        if media_info['package'] == 'com.plexapp.android' and media_info['is_active']:
            try:
                plex_url = settings.get('plex.url')
                plex_token = settings.get('plex.token')
                if plex_token:
                    # Get active Plex sessions
                    sessions_url = f"{plex_url}/status/sessions?X-Plex-Token={plex_token}"
                    headers = {'Accept': 'application/json'}
                    plex_response = requests.get(sessions_url, headers=headers, timeout=3)
                    if plex_response.status_code == 200:
                        sessions_data = plex_response.json()
                        metadata_list = sessions_data.get('MediaContainer', {}).get('Metadata', [])

                        # Find the active session (there should usually be one for this device)
                        for session in metadata_list:
                            player = session.get('Player', {})
                            state = player.get('state', '')

                            # Check if this session is playing/paused
                            if state in ['playing', 'paused']:
                                # Update state from Plex (more accurate)
                                media_info['state'] = state
                                media_info['is_active'] = True

                                # Get title based on media type
                                media_type = session.get('type', '')
                                if media_type == 'episode':
                                    show_title = session.get('grandparentTitle', '')
                                    episode_title = session.get('title', '')
                                    season = session.get('parentIndex', '')
                                    episode = session.get('index', '')
                                    media_info['title'] = f"{show_title} - S{season}E{episode}"
                                    media_info['subtitle'] = episode_title
                                elif media_type == 'movie':
                                    title = session.get('title', '')
                                    year = session.get('year', '')
                                    media_info['title'] = f"{title} ({year})" if year else title
                                else:
                                    media_info['title'] = session.get('title', 'Unknown')

                                # Get duration and position
                                media_info['duration'] = session.get('duration', 0) // 1000  # Convert ms to seconds
                                view_offset = session.get('viewOffset', 0)
                                media_info['position'] = view_offset // 1000  # Convert ms to seconds

                                # Calculate progress
                                if media_info['duration'] > 0:
                                    media_info['progress_percent'] = (media_info['position'] / media_info['duration']) * 100

                                # Get artwork URLs
                                thumb = session.get('thumb', '')
                                art = session.get('art', '') or session.get('grandparentArt', '')
                                if thumb:
                                    media_info['artwork_url'] = f"{plex_url}{thumb}?X-Plex-Token={plex_token}"
                                if art:
                                    media_info['backdrop_url'] = f"{plex_url}{art}?X-Plex-Token={plex_token}"

                                # Get player info
                                media_info['player_name'] = player.get('title', 'Unknown')

                                break  # Found active session
            except Exception as e:
                logger.debug(f"Could not fetch Plex metadata from sessions API: {e}")

        return jsonify({
            'device_ip': device_ip,
            'current_app': current_app,
            'current_package': current_package,
            'media': media_info
        })

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'ADB command timed out'}), 500
    except Exception as e:
        logger.error(f"Error getting Shield activity: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/adb/control', methods=['POST'])
def adb_control():
    """Send ADB commands to control playback on a room device."""
    try:
        data = request.get_json() or {}
        room_id = data.get('room_id')
        device_ip = data.get('device_ip')
        command = data.get('command')  # play_pause, stop, rewind, forward, home

        # Resolve device from room_id
        adb_port = settings.get_default_adb_port()
        if room_id:
            room = settings.get_room_by_id(room_id)
            if room:
                device_ip = device_ip or room.get('host', '')
                adb_port = room.get('connectPort', adb_port)
        if not device_ip:
            device_ip = settings.get_default_device_ip()

        if not command:
            return jsonify({'error': 'Command required'}), 400

        # ADB keycode mappings
        keycodes = {
            'play_pause': 'KEYCODE_MEDIA_PLAY_PAUSE',
            'play': 'KEYCODE_MEDIA_PLAY',
            'pause': 'KEYCODE_MEDIA_PAUSE',
            'stop': 'KEYCODE_MEDIA_STOP',
            'rewind': 'KEYCODE_MEDIA_REWIND',
            'forward': 'KEYCODE_MEDIA_FAST_FORWARD',
            'skip_back': 'KEYCODE_MEDIA_SKIP_BACKWARD',
            'skip_forward': 'KEYCODE_MEDIA_SKIP_FORWARD',
            'previous': 'KEYCODE_MEDIA_PREVIOUS',
            'next': 'KEYCODE_MEDIA_NEXT',
            'home': 'KEYCODE_HOME',
            'back': 'KEYCODE_BACK',
            'up': 'KEYCODE_DPAD_UP',
            'down': 'KEYCODE_DPAD_DOWN',
            'left': 'KEYCODE_DPAD_LEFT',
            'right': 'KEYCODE_DPAD_RIGHT',
            'select': 'KEYCODE_DPAD_CENTER'
        }

        if command not in keycodes:
            return jsonify({'error': f'Unknown command: {command}'}), 400

        keycode = keycodes[command]

        # Send ADB command
        target = f'{device_ip}:{adb_port}'
        env = {**os.environ, 'HOME': '/app/home'}
        logger.info(f"Sending ADB keyevent {keycode} to {target}")

        result = subprocess.run(
            ['adb', '-s', target, 'shell', 'input', 'keyevent', keycode],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )

        if result.returncode == 0:
            return jsonify({
                'success': True,
                'command': command,
                'device': device_ip,
                'message': f'Command {command} sent successfully'
            })
        else:
            logger.error(f"ADB command failed: {result.stderr}")
            return jsonify({
                'success': False,
                'error': result.stderr or 'Command failed'
            }), 500

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Command timed out'}), 500
    except Exception as e:
        logger.error(f"Error sending ADB command: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Screensaver API - Movie Orb Screensaver data
# =============================================================================

@app.route('/api/screensaver/movies', methods=['GET'])
def get_screensaver_movies():
    """
    Get movies for the Movie Orb Screensaver.

    Source is determined by ui.screensaver.source setting:
      - "hero": uses the same movies/filters/logic as the Hero section
      - "plex_library": fetches from configured Plex library (default)

    Query params:
        limit: Max items to return (default 12)

    Returns list of movie objects with:
        id, title, year, backdropUrl, posterUrl, clearlogoUrl
    """
    try:
        limit = request.args.get('limit', 12, type=int)
        source = settings.get('ui.screensaver.source', 'plex_library')

        if source == 'hero':
            # Use the same movies as the Hero section
            hero_movies, _ = _build_hero_movies()
            hero_movies = _hero_movies_for_request(hero_movies)
            movies = []
            for m in hero_movies[:limit]:
                movies.append({
                    'id': m.get('tmdb_id'),
                    'rating_key': m.get('rating_key'),
                    'title': m['title'],
                    'year': str(m.get('year', '')),
                    'backdropUrl': m.get('tmdb_backdrop_url') or m.get('backdrop_url'),
                    'posterUrl': m.get('poster_url'),
                    'clearlogoUrl': m.get('clearlogo_url'),
                    'overview': m.get('summary', ''),
                })
            return jsonify({
                'success': True,
                'movies': movies,
                'count': len(movies)
            })

        # Default: plex_library source — use per-user Plex when available
        service = get_screensaver_service(settings)
        plex = get_plex_for_request()
        movies = service.get_movies(limit=limit, plex_svc=plex)

        return jsonify({
            'success': True,
            'movies': movies,
            'count': len(movies)
        })
    except Exception as e:
        logger.error(f"Screensaver API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'movies': []}), 500


@app.route('/api/screensaver/image-proxy')
def screensaver_image_proxy():
    """
    Proxy external images (fanart.tv) to avoid CORS issues.
    Only allows fanart.tv URLs for security.
    """
    url = request.args.get('url', '')
    try:
        parsed = urllib.parse.urlparse(url)
        if not url or not parsed.hostname or not parsed.hostname.endswith('.fanart.tv'):
            return jsonify({'error': 'Invalid URL'}), 400
    except Exception:
        return jsonify({'error': 'Invalid URL'}), 400

    try:
        response = requests.get(url, timeout=10, stream=True)
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', 'image/png')
            return send_file(
                io.BytesIO(response.content),
                mimetype=content_type
            )
        return jsonify({'error': 'Failed to fetch image'}), response.status_code
    except Exception as e:
        logger.error(f"Image proxy error: {e}")
        return jsonify({'error': str(e)}), 500


# Static file serving — Flask's built-in handler at /static/ does the heavy
# lifting. The after_request hook (above) sets Cache-Control headers.
# Only manifest.json needs a custom route (served at root /manifest.json).

@app.route('/manifest.json')
def manifest():
    """PWA manifest — app is always Luma."""
    sm = SettingsManager()
    theme = sm.get("ui.theme_color") or "#15102a"
    prefix = app.config.get('URL_BASE', '') or ''
    data = {
        "name": "Luma",
        "short_name": "Luma",
        "description": "Your personal media library and search",
        "start_url": f"{prefix}/",
        "scope": f"{prefix}/",
        "display": "standalone",
        "background_color": "#15102a",
        "theme_color": theme,
        "orientation": "portrait",
        "icons": [
            {"src": f"{prefix}/static/images/ic_launcher_round.png", "sizes": "72x72", "type": "image/png"},
            {"src": f"{prefix}/static/images/ic_launcher.png", "sizes": "72x72", "type": "image/png", "purpose": "any"},
        ],
    }
    return jsonify(data)

@app.route('/sw.js')
def service_worker():
    """Service Worker file — short cache so updates propagate quickly."""
    resp = send_from_directory(app.static_folder, 'sw.js')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = (app.config.get('URL_BASE', '') or '') + '/'
    return resp

@app.route('/ca.crt')
def serve_ca_cert():
    """Serve the Caddy internal CA root certificate for iOS trust installation."""
    cert_path = os.environ.get('LUMA_CA_CERT_PATH') or '/caddy-data/caddy/pki/authorities/local/root.crt'
    if os.path.exists(cert_path):
        resp = send_file(cert_path, mimetype='application/x-x509-ca-cert',
                         as_attachment=True, download_name='Luma-CA.crt')
        resp.headers['Cache-Control'] = 'no-cache'
        return resp
    return jsonify({'error': 'CA certificate not found — Caddy may not have started yet'}), 404

# =============================================================================
# React SPA Assets
# =============================================================================

@app.route('/assets/<path:filename>')
def spa_assets(filename):
    """Serve Vite-built assets (JS/CSS) for the React SPA.
    Hashed filenames → immutable cache (1 year)."""
    resp = send_from_directory(os.path.join(app.config['SPA_DIR'], 'assets'), filename)
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


@app.route('/fonts/<path:filename>')
def spa_fonts(filename):
    """Serve font files from the SPA public/fonts directory."""
    resp = send_from_directory(os.path.join(app.config['SPA_DIR'], 'fonts'), filename)
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


@app.route('/weather/<path:filename>')
def spa_weather(filename):
    """Serve weather icon SVGs from the SPA public/weather directory."""
    resp = send_from_directory(os.path.join(app.config['SPA_DIR'], 'weather'), filename)
    resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
    return resp

@app.route('/static/hero_cache/<path:filename>')
def hero_cache_files(filename):
    """Serve hero images from HERO_CACHE_DIR.

    Falls back to the legacy <static>/hero_cache dir (where images lived before
    they moved to $LUMA_CACHE_DIR/hero) so URLs stored before the move keep
    working. _contained_file rejects traversal and symlinks that leave either
    directory.
    """
    for base in (app.config['HERO_CACHE_DIR'], os.path.join(app.static_folder, 'hero_cache')):
        path = _contained_file(base, filename)
        if path:
            resp = send_file(path)
            resp.headers['Cache-Control'] = 'public, max-age=86400'
            return resp
    return jsonify({'error': 'not found'}), 404


def _contained_file(base: str, filename: str):
    """Real path of base/filename when it is a regular file inside base, else None.

    safe_join rejects '..' and absolute names; the realpath check also rejects
    symlinks (or symlinked folders) that point outside base.
    """
    from werkzeug.security import safe_join
    path = safe_join(base, filename)
    if not path:
        return None
    root = os.path.realpath(base)
    real = os.path.realpath(path)
    if os.path.commonpath([root, real]) != root or real == root or not os.path.isfile(real):
        return None
    return real

# =============================================================================
# Settings API — Phase 1
# =============================================================================

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Return all settings with sensitive values masked."""
    err = _require_admin()
    if err:
        return err
    try:
        return jsonify(settings.get_all_masked())
    except Exception as e:
        logger.error(f"GET /api/settings error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings', methods=['PUT'])
def update_settings():
    """Partial-update settings via deep merge.

    Accepts a JSON body with any subset of settings keys.  Keys whose value
    is the mask sentinel ("********") are silently ignored so the frontend
    can round-trip a GET response back to PUT without destroying secrets.
    """
    err = _require_admin()
    if err:
        return err
    try:
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'errors': ['Request body must be a JSON object']}), 400

        success, errors = settings.update(data)
        if not success:
            return jsonify({'success': False, 'errors': errors}), 400

        # Invalidate caches when relevant settings change
        ui_data = data.get('ui') if isinstance(data.get('ui'), dict) else {}
        if 'vibe_engine' in ui_data:
            _vibe_cache.clear()
            logger.info('Vibe cache invalidated due to settings change')
        if 'hero' in ui_data:
            _hero_result_cache.clear()
            logger.info('Hero cache invalidated due to settings change')

        # Handle tunnel toggle on settings save
        network_data = data.get('network') if isinstance(data.get('network'), dict) else {}
        if 'tunnel' in network_data:
            enabled = settings.get('network.tunnel.enabled', False)
            token = settings.get('network.tunnel.token', '')
            if enabled and token:
                start_tunnel(token)
            else:
                stop_tunnel()

        return jsonify({'success': True, 'settings': settings.get_all_masked()})
    except Exception as e:
        logger.error(f"PUT /api/settings error: {e}")
        return jsonify({'success': False, 'errors': [str(e)]}), 500


@app.route('/api/settings/schema', methods=['GET'])
def get_settings_schema():
    """Return the JSON schema for frontend validation."""
    err = _require_admin()
    if err:
        return err
    try:
        return jsonify(settings.get_schema())
    except Exception as e:
        logger.error(f"GET /api/settings/schema error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/rooms', methods=['GET'])
def get_rooms():
    """Return rooms in the nested shape expected by PlaybackModal + defaultPlaybackTarget.

    Rooms may already be stored in the full nested format (with ``devices`` array)
    from the frontend RoomsPage, or in the older flat format (``ip``, ``adb_port``).
    This endpoint normalises both into the shape PlaybackModal expects.
    """
    # Admins use global rooms; non-admin users fall back to per-user rooms
    user = _get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    is_admin = user.get('is_admin')
    try:
        if is_admin:
            raw_rooms = settings.get('devices.rooms') or []
        else:
            user_settings = user_auth.get_user_settings(user['id']) or {}
            raw_rooms = user_settings.get('rooms') or []
        frontend_rooms = []
        for idx, r in enumerate(raw_rooms):
            if not isinstance(r, dict):
                continue
            room_id = r.get('id', f'room-{idx}')

            # Nested rooms (including empty devices: []) pass through as-is.
            # Only invent a nested shape for true legacy flat rooms that never
            # had a devices array — empty [] must NOT become a phantom Kodi device.
            if isinstance(r.get('devices'), list):
                frontend_rooms.append({
                    'id': room_id,
                    'name': r.get('name', f'Room {idx + 1}'),
                    'useHarmonyHub': r.get('useHarmonyHub', False),
                    'harmonyHubIp': r.get('harmonyHubIp', ''),
                    'harmonyVolumeDevice': r.get('harmonyVolumeDevice', ''),
                    'devices': r['devices'],
                })
            else:
                # Legacy flat room — build the nested shape once for consumers
                dev_id = f"{room_id}-dev-0"
                svc_id = f"{room_id}-svc-kodi"
                platform = r.get('platform') or r.get('type') or 'android_tv'
                host = r.get('host') or r.get('ip') or ''
                connect_port = r.get('connectPort') or r.get('adb_port') or 5555
                frontend_rooms.append({
                    'id': room_id,
                    'name': r.get('name', f'Room {idx + 1}'),
                    'useHarmonyHub': r.get('useHarmonyHub', False),
                    'harmonyHubIp': r.get('harmonyHubIp', ''),
                    'harmonyVolumeDevice': r.get('harmonyVolumeDevice', ''),
                    'devices': [{
                        'id': dev_id,
                        'name': r.get('name', f'Room {idx + 1}'),
                        'platform': platform,
                        'host': host,
                        'connectPort': connect_port,
                        'pairPort': r.get('pairPort'),
                        'paired': r.get('paired', False),
                        'services': [{
                            'id': svc_id,
                            'type': 'kodi',
                            'kodi_host': '',
                            'kodi_port': 8080,
                            'kodi_username': '',
                            'kodi_password': '',
                        }],
                    }],
                })
        # Build default playback target from first room
        default_target = {'roomId': '', 'deviceId': '', 'serviceId': ''}
        if frontend_rooms:
            first = frontend_rooms[0]
            devs = first.get('devices', [])
            if devs:
                svcs = devs[0].get('services', [])
                default_target = {
                    'roomId': first['id'],
                    'deviceId': devs[0].get('id', ''),
                    'serviceId': svcs[0].get('id', '') if svcs else '',
                }
        return jsonify({'rooms': frontend_rooms, 'defaultPlaybackTarget': default_target})
    except Exception as e:
        logger.error(f"GET /api/settings/rooms error: {e}")
        return jsonify({'rooms': [], 'defaultPlaybackTarget': {}, 'error': str(e)}), 500


@app.route('/api/settings/rooms', methods=['POST'])
def create_room():
    """Create a new room. Body: {name, useHarmonyHub, harmonyHubIp, ...}."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        name = body.get('name', '').strip()
        if not name:
            return jsonify({'error': 'Room name is required'}), 400

        room = settings.add_room({
            'name': name,
            'useHarmonyHub': bool(body.get('useHarmonyHub', False)),
            'harmonyHubIp': body.get('harmonyHubIp', ''),
            'harmonyVolumeDevice': body.get('harmonyVolumeDevice', ''),
            'devices': [],
        })
        return jsonify({'room': room}), 201
    except Exception as e:
        logger.error(f"POST /api/settings/rooms error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/rooms/<room_id>', methods=['PUT'])
def update_room_endpoint(room_id):
    """Update an existing room by ID (room-level fields only)."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        body.pop('id', None)
        body.pop('devices', None)  # Don't overwrite devices array from room form

        room = settings.update_room(room_id, body)
        if room is None:
            return jsonify({'error': 'Room not found'}), 404
        return jsonify({'room': room})
    except Exception as e:
        logger.error(f"PUT /api/settings/rooms/{room_id} error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/rooms/<room_id>', methods=['DELETE'])
def delete_room_endpoint(room_id):
    """Delete a room by ID."""
    err = _require_admin()
    if err:
        return err
    try:
        deleted = settings.delete_room(room_id)
        if not deleted:
            return jsonify({'error': 'Room not found'}), 404
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"DELETE /api/settings/rooms/{room_id} error: {e}")
        return jsonify({'error': str(e)}), 500


# --- Device CRUD ---

@app.route('/api/settings/rooms/<room_id>/devices', methods=['POST'])
def create_device(room_id):
    """Add a device to a room."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        name = body.get('name', '').strip()
        if not name:
            return jsonify({'error': 'Device name is required'}), 400

        platform = body.get('platform', 'android_tv') or 'android_tv'
        raw_connect = body.get('connectPort')
        if raw_connect not in (None, ''):
            connect_port = int(raw_connect)
        elif platform in ('android_tv', 'shield'):
            connect_port = 5555  # classic ADB TCP mode
        else:
            # Google TV wireless debugging uses a dynamic port shown on the TV —
            # never invent 5555 here; client should send the on-screen port.
            connect_port = None
        device_data = {
            'name': name,
            'platform': platform,
            'host': body.get('host', '').strip(),
            'connectPort': connect_port,
            'pairPort': int(body['pairPort']) if body.get('pairPort') not in (None, '') else None,
            'paired': False,
            'harmonyActivity': body.get('harmonyActivity', ''),
            'harmonyNavigationDevice': body.get('harmonyNavigationDevice', ''),
            'macAddress': body.get('macAddress', '').strip(),
            'powerMethod': body.get('powerMethod') or ('adb' if platform != 'network' else ''),
            'haPowerEntity': body.get('haPowerEntity', ''),
            'services': [],
        }

        device = settings.add_device(room_id, device_data)
        if device is None:
            return jsonify({'error': 'Room not found'}), 404
        return jsonify({'device': device}), 201
    except Exception as e:
        logger.error(f"POST /api/settings/rooms/{room_id}/devices error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/rooms/<room_id>/devices/<device_id>', methods=['PUT'])
def update_device_endpoint(room_id, device_id):
    """Update a device."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        body.pop('id', None)
        if 'connectPort' in body and body['connectPort'] is not None:
            body['connectPort'] = int(body['connectPort'])
        if 'pairPort' in body and body['pairPort'] is not None:
            body['pairPort'] = int(body['pairPort'])

        device = settings.update_device(room_id, device_id, body)
        if device is None:
            return jsonify({'error': 'Device not found'}), 404
        return jsonify({'device': device})
    except Exception as e:
        logger.error(f"PUT /api/settings/rooms/{room_id}/devices/{device_id} error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/rooms/<room_id>/devices/<device_id>', methods=['DELETE'])
def delete_device_endpoint(room_id, device_id):
    """Delete a device from a room."""
    err = _require_admin()
    if err:
        return err
    try:
        deleted = settings.delete_device(room_id, device_id)
        if not deleted:
            return jsonify({'error': 'Device not found'}), 404
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"DELETE devices error: {e}")
        return jsonify({'error': str(e)}), 500


# --- Service CRUD ---

@app.route('/api/settings/rooms/<room_id>/devices/<device_id>/services', methods=['POST'])
def create_service(room_id, device_id):
    """Add a service (Kodi or Plex) to a device."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        svc_type = body.get('type', 'kodi')
        if svc_type not in ('kodi', 'plex'):
            return jsonify({'error': f'Invalid service type: {svc_type}'}), 400

        if svc_type == 'plex':
            svc_data = {'type': 'plex'}
        else:
            svc_data = {
                'type': 'kodi',
                'kodi_host': body.get('kodi_host', ''),
                'kodi_port': int(body.get('kodi_port', 8080)),
                'kodi_username': body.get('kodi_username', ''),
                'kodi_password': body.get('kodi_password', ''),
            }

        service = settings.add_service(room_id, device_id, svc_data)
        if service is None:
            return jsonify({'error': 'Room or device not found'}), 404
        return jsonify({'service': service}), 201
    except Exception as e:
        logger.error(f"POST services error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/rooms/<room_id>/devices/<device_id>/services/<service_id>', methods=['PUT'])
def update_service_endpoint(room_id, device_id, service_id):
    """Update a service (Kodi or Plex)."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        body.pop('id', None)
        svc_type = body.get('type', 'kodi')
        if svc_type not in ('kodi', 'plex'):
            return jsonify({'error': f'Invalid service type: {svc_type}'}), 400
        body['type'] = svc_type
        if 'kodi_port' in body:
            body['kodi_port'] = int(body['kodi_port'])

        service = settings.update_service(room_id, device_id, service_id, body)
        if service is None:
            return jsonify({'error': 'Service not found'}), 404
        return jsonify({'service': service})
    except Exception as e:
        logger.error(f"PUT services error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/rooms/<room_id>/devices/<device_id>/services/<service_id>', methods=['DELETE'])
def delete_service_endpoint(room_id, device_id, service_id):
    """Delete a service."""
    err = _require_admin()
    if err:
        return err
    try:
        deleted = settings.delete_service(room_id, device_id, service_id)
        if not deleted:
            return jsonify({'error': 'Service not found'}), 404
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"DELETE services error: {e}")
        return jsonify({'error': str(e)}), 500


# --- Default playback target ---

@app.route('/api/settings/rooms/default', methods=['PUT'])
def set_default_target():
    """Set the default playback target (room + device + service)."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        target = {
            'roomId': body.get('roomId', ''),
            'deviceId': body.get('deviceId', ''),
            'serviceId': body.get('serviceId', ''),
        }
        settings.update({'devices': {'defaultPlaybackTarget': target}})
        return jsonify({'defaultPlaybackTarget': target})
    except Exception as e:
        logger.error(f"PUT /api/settings/rooms/default error: {e}")
        return jsonify({'error': str(e)}), 500


# --- Device test & pair ---

@app.route('/api/rooms/<room_id>/devices/<device_id>/test', methods=['POST'])
def test_device_connection(room_id, device_id):
    """Test ADB connectivity to a specific device."""
    from app.adb_utils import test_room_connection as _test_room
    try:
        device = settings.get_device(room_id, device_id)
        if not device:
            return jsonify({'ok': False, 'needsPairing': False, 'message': 'Device not found', 'steps': []}), 404

        host = device.get('host', '')
        if not host:
            return jsonify({'ok': False, 'needsPairing': False, 'message': 'No host configured for this device', 'steps': []}), 400

        result = _test_room(
            host=host,
            connect_port=device.get('connectPort', 5555),
            platform=device.get('platform', 'android_tv'),
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"POST device test error: {e}")
        return jsonify({'ok': False, 'needsPairing': False, 'message': str(e), 'steps': []}), 500


@app.route('/api/rooms/<room_id>/devices/<device_id>/pair', methods=['POST'])
def pair_device_endpoint(room_id, device_id):
    """Pair with a device, then connect."""
    from app.adb_utils import adb_pair, adb_connect
    try:
        device = settings.get_device(room_id, device_id)
        if not device:
            return jsonify({'ok': False, 'needsPairing': False, 'hint': 'unknown', 'message': 'Device not found', 'steps': []}), 404

        body = request.get_json(silent=True) or {}
        pair_port = int(body.get('pairPort') or body.get('pair_port') or device.get('pairPort') or 0)
        pair_code = str(body.get('pairCode') or body.get('pair_code') or '').strip()
        host = device.get('host', '')

        if not host:
            return jsonify({'ok': False, 'needsPairing': True, 'hint': 'pair_failed', 'message': 'No host configured', 'steps': []}), 400
        if not pair_port:
            return jsonify({'ok': False, 'needsPairing': True, 'hint': 'pair_failed', 'message': 'Pair port is required', 'steps': []}), 400
        if not pair_code:
            return jsonify({'ok': False, 'needsPairing': True, 'hint': 'pair_failed', 'message': 'Pair code is required', 'steps': []}), 400

        steps = []

        pair_result = adb_pair(host, pair_port, pair_code)
        steps.append({
            'step': 'adb_pair', 'ok': pair_result['ok'], 'detail': pair_result['detail'],
            'stdout': pair_result.get('stdout', ''), 'stderr': pair_result.get('stderr', ''),
        })

        if not pair_result['ok']:
            return jsonify({
                'ok': False, 'needsPairing': True,
                'hint': pair_result.get('hint', 'pair_failed'),
                'message': pair_result['detail'], 'steps': steps,
            })

        connect_port = device.get('connectPort', 5555)
        connect_result = adb_connect(host, connect_port)
        steps.append({
            'step': 'adb_connect', 'ok': connect_result['ok'], 'detail': connect_result['detail'],
            'stdout': connect_result.get('stdout', ''), 'stderr': connect_result.get('stderr', ''),
        })

        if connect_result['ok']:
            settings.update_device(room_id, device_id, {'paired': True, 'pairPort': pair_port})

        return jsonify({
            'ok': connect_result['ok'],
            'needsPairing': not connect_result['ok'],
            'hint': 'paired' if connect_result['ok'] else connect_result.get('hint', 'unknown'),
            'message': 'Paired and connected!' if connect_result['ok'] else connect_result['detail'],
            'steps': steps,
        })
    except Exception as e:
        logger.error(f"POST device pair error: {e}")
        return jsonify({'ok': False, 'needsPairing': True, 'hint': 'unknown', 'message': str(e), 'steps': []}), 500


# --- Legacy room-level test/pair (for backwards compat) ---

@app.route('/api/rooms/<room_id>/test', methods=['POST'])
def test_room_connection_legacy(room_id):
    """Test ADB connectivity to first device in a room."""
    from app.adb_utils import test_room_connection as _test_room
    try:
        room = settings.get_room_by_id(room_id)
        if not room:
            return jsonify({'ok': False, 'needsPairing': False, 'message': 'Room not found', 'steps': []}), 404

        # Find host from room or first device
        host = room.get('host') or room.get('ip', '')
        connect_port = room.get('connectPort', room.get('adb_port', 5555))
        platform = room.get('platform', 'android_tv')
        if not host and isinstance(room.get('devices'), list) and room['devices']:
            dev = room['devices'][0]
            host = dev.get('host', '')
            connect_port = dev.get('connectPort', 5555)
            platform = dev.get('platform', 'android_tv')

        if not host:
            return jsonify({'ok': False, 'needsPairing': False, 'message': 'No host configured', 'steps': []}), 400

        result = _test_room(host=host, connect_port=connect_port, platform=platform)
        return jsonify(result)
    except Exception as e:
        logger.error(f"POST /api/rooms/{room_id}/test error: {e}")
        return jsonify({'ok': False, 'needsPairing': False, 'message': str(e), 'steps': []}), 500


@app.route('/api/rooms/<room_id>/pair', methods=['POST'])
def pair_room_legacy(room_id):
    """Pair with first device in a room (legacy compat)."""
    from app.adb_utils import adb_pair, adb_connect
    try:
        room = settings.get_room_by_id(room_id)
        if not room:
            return jsonify({'ok': False, 'needsPairing': False, 'hint': 'unknown', 'message': 'Room not found', 'steps': []}), 404

        # Find host from room or first device
        host = room.get('host') or room.get('ip', '')
        connect_port = room.get('connectPort', room.get('adb_port', 5555))
        if not host and isinstance(room.get('devices'), list) and room['devices']:
            dev = room['devices'][0]
            host = dev.get('host', '')
            connect_port = dev.get('connectPort', 5555)

        body = request.get_json(silent=True) or {}
        pair_port = int(body.get('pairPort', 0))
        pair_code = str(body.get('pairCode', '')).strip()

        if not host:
            return jsonify({'ok': False, 'needsPairing': True, 'hint': 'pair_failed', 'message': 'No host configured', 'steps': []}), 400
        if not pair_port:
            return jsonify({'ok': False, 'needsPairing': True, 'hint': 'pair_failed', 'message': 'Pair port is required', 'steps': []}), 400
        if not pair_code:
            return jsonify({'ok': False, 'needsPairing': True, 'hint': 'pair_failed', 'message': 'Pair code is required', 'steps': []}), 400

        steps = []
        pair_result = adb_pair(host, pair_port, pair_code)
        steps.append({
            'step': 'adb_pair', 'ok': pair_result['ok'], 'detail': pair_result['detail'],
            'stdout': pair_result.get('stdout', ''), 'stderr': pair_result.get('stderr', ''),
        })

        if not pair_result['ok']:
            return jsonify({
                'ok': False, 'needsPairing': True,
                'hint': pair_result.get('hint', 'pair_failed'),
                'message': pair_result['detail'], 'steps': steps,
            })

        connect_result = adb_connect(host, connect_port)
        steps.append({
            'step': 'adb_connect', 'ok': connect_result['ok'], 'detail': connect_result['detail'],
            'stdout': connect_result.get('stdout', ''), 'stderr': connect_result.get('stderr', ''),
        })

        if connect_result['ok']:
            settings.update_room(room_id, {'paired': True, 'pairPort': pair_port})

        return jsonify({
            'ok': connect_result['ok'],
            'needsPairing': not connect_result['ok'],
            'hint': 'paired' if connect_result['ok'] else connect_result.get('hint', 'unknown'),
            'message': 'Paired and connected!' if connect_result['ok'] else connect_result['detail'],
            'steps': steps,
        })
    except Exception as e:
        logger.error(f"POST /api/rooms/{room_id}/pair error: {e}")
        return jsonify({'ok': False, 'needsPairing': True, 'hint': 'unknown', 'message': str(e), 'steps': []}), 500


@app.route('/api/room/<room_id>/remote', methods=['POST'])
def room_remote(room_id):
    """Send a remote-control command to a room device.

    Routes commands through the appropriate backend:
    - power, vol_up, vol_down → Harmony Hub (if configured) or ADB
    - play, pause, stop → Kodi JSON-RPC (if device has Kodi service) or ADB
    - all other commands → ADB keyevent
    """
    from app.adb_utils import send_key_event
    try:
        room = settings.get_room_by_id(room_id)
        if not room:
            return jsonify({'ok': False, 'detail': 'Room not found'}), 404

        body = request.get_json(silent=True) or {}
        command = body.get('command', '').strip()
        req_device_id = body.get('device_id', '')
        if not command:
            return jsonify({'ok': False, 'detail': 'command is required'}), 400

        # Resolve the target device from the v3 hierarchy
        devs = room.get('devices', []) if isinstance(room.get('devices'), list) else []
        device = None
        if req_device_id:
            device = next((d for d in devs if isinstance(d, dict) and d.get('id') == req_device_id), None)
        if not device and devs:
            device = devs[0] if isinstance(devs[0], dict) else None

        host = (device.get('host', '') if device else '') or room.get('host') or room.get('ip') or ''
        port = (device.get('connectPort') if device else None) or room.get('connectPort') or room.get('adb_port', 5555)

        # --- Kodi JSON-RPC direct path (takes priority for playback + nav commands) ---
        # For devices with a Kodi service, route commands directly via Kodi's
        # JSON-RPC API instead of Harmony or ADB.  This is more reliable
        # because Kodi's HTTP API controls the player/UI directly.
        _KODI_PLAYBACK_CMDS = ('play', 'pause', 'stop', 'play_pause')
        _KODI_NAV_CMDS = ('up', 'down', 'left', 'right', 'select', 'home', 'back')
        _KODI_ALL_CMDS = _KODI_PLAYBACK_CMDS + _KODI_NAV_CMDS
        if command in _KODI_ALL_CMDS and device:
            kodi_svc = next(
                (s for s in (device.get('services') or [])
                 if isinstance(s, dict) and s.get('type') == 'kodi'),
                None,
            )
            if kodi_svc:
                from app.kodi_service import KodiService
                kodi_host = kodi_svc.get('kodi_host', '') or device.get('host', '')
                kodi_port = int(kodi_svc.get('kodi_port') or 8080)
                kodi_user = kodi_svc.get('kodi_username', '')
                kodi_pass = kodi_svc.get('kodi_password', '')
                if kodi_host:
                    try:
                        kodi = KodiService(kodi_host, kodi_port, kodi_user, kodi_pass)
                        if command in _KODI_PLAYBACK_CMDS:
                            player = kodi.get_active_player()
                            player_id = player.get('playerid', 1) if player else 1
                            if command == 'stop':
                                kodi.stop(player_id)
                            else:
                                kodi.pause_toggle(player_id)
                            return jsonify({'ok': True, 'detail': f'Kodi {command}'})
                        elif command in _KODI_NAV_CMDS:
                            # If Harmony is configured for navigation on this device,
                            # prefer Harmony over Kodi JSON-RPC for nav commands.
                            # Harmony sends IR/BT commands that the device handles
                            # natively, which is more reliable for navigation
                            # (Kodi Input.* during video playback maps to seek/OSD
                            # actions, not directional navigation).
                            _harmony_nav_device = (
                                (device.get('harmonyNavigationDevice', '') if device else '')
                                or room.get('harmonyNavigationDevice', '')
                            )
                            _has_harmony_nav = (
                                room.get('useHarmonyHub')
                                and room.get('harmonyHubIp')
                                and _harmony_nav_device
                            )
                            if _has_harmony_nav:
                                logger.debug(f"Skipping Kodi JSON-RPC nav for {command} — Harmony nav configured ({_harmony_nav_device})")
                                # Fall through to Harmony path below
                                pass
                            # Only route nav through Kodi if it has an active player
                            # (avoids sending Input.* to Kodi when Plex is playing)
                            elif (player := kodi.get_active_player()):
                                _kodi_nav_map = {
                                    'up': kodi.input_up,
                                    'down': kodi.input_down,
                                    'left': kodi.input_left,
                                    'right': kodi.input_right,
                                    'select': kodi.input_select,
                                    'home': kodi.input_home,
                                    'back': kodi.input_back,
                                }
                                _kodi_nav_map[command]()
                                return jsonify({'ok': True, 'detail': f'Kodi {command}'})
                    except Exception as kodi_err:
                        logger.warning(f"Kodi JSON-RPC {command} for room {room_id}: {kodi_err} — falling through")

        # --- Harmony Hub path (direct connection) ---
        # Maps our command names → Harmony device command names
        _HARMONY_VOLUME_CMDS = {
            'vol_up': 'VolumeUp', 'vol_down': 'VolumeDown', 'mute': 'Mute',
        }
        _HARMONY_NAV_CMDS = {
            'up': 'DirectionUp', 'down': 'DirectionDown',
            'left': 'DirectionLeft', 'right': 'DirectionRight',
            'select': 'Select', 'home': 'Home', 'back': 'Back',
            'channel_up': 'ChannelUp', 'channel_down': 'ChannelDown',
            'play': 'Play', 'pause': 'Pause', 'play_pause': 'Pause',
            'stop': 'Stop',
        }
        harmony_commands = {'power'} | set(_HARMONY_VOLUME_CMDS) | set(_HARMONY_NAV_CMDS)
        if command in harmony_commands and room.get('useHarmonyHub'):
            hub_ip = room.get('harmonyHubIp', '')
            volume_device = room.get('harmonyVolumeDevice', '')
            nav_device = (device.get('harmonyNavigationDevice', '') if device else '') or room.get('harmonyNavigationDevice', '')

            if hub_ip:
                from app import harmony_service
                try:
                    if command == 'power':
                        # Get current activity to determine if we should power on or off
                        current = harmony_service.get_current_activity(hub_ip)
                        if current == 'off':
                            # Power on — start the device's activity if configured
                            activity = device.get('harmonyActivity', '') if device else ''
                            if activity:
                                ok = harmony_service.start_activity(hub_ip, activity)
                            else:
                                ok = False
                        else:
                            ok = harmony_service.power_off(hub_ip)
                        logger.info(f"Harmony remote power (was '{current}'): ok={ok}")
                        return jsonify({'ok': ok, 'detail': f'Harmony power (was {current})'})
                    elif command in _HARMONY_VOLUME_CMDS:
                        # Volume/mute → send_command to the volume device
                        cmd = _HARMONY_VOLUME_CMDS[command]
                        target_device = volume_device
                        if not target_device:
                            return jsonify({'ok': False, 'detail': 'Set a Harmony volume device for this room in Settings'}), 400
                        ok = harmony_service.send_command(hub_ip, target_device, cmd)
                        logger.info(f"Harmony remote {command} → {target_device}: ok={ok}")
                        return jsonify({'ok': ok, 'detail': f'Harmony {command}'})
                    elif command in _HARMONY_NAV_CMDS and nav_device:
                        # Navigation/channel/playback → send_command to the navigation device
                        cmd = _HARMONY_NAV_CMDS[command]
                        ok = harmony_service.send_command(hub_ip, nav_device, cmd)
                        logger.info(f"Harmony remote {command} → {nav_device}: ok={ok}")
                        return jsonify({'ok': ok, 'detail': f'Harmony {command}'})
                except Exception as harmony_err:
                    logger.warning(f"Harmony remote {command} failed: {harmony_err}, falling through to ADB")

        # --- Home Assistant power path ---
        # When device.powerMethod is 'homeassistant' and command is 'power',
        # fire the HA entity directly (toggle behavior for remote control).
        if command == 'power' and device and device.get('powerMethod') == 'homeassistant':
            entity_id = device.get('haPowerEntity', '')
            if entity_id:
                ha_url = settings.get('homeassistant.url', '')
                ha_token = settings.get('homeassistant.access_token', '')
                if ha_url and ha_token:
                    from app.ha_service import fire_entity
                    result = fire_entity(ha_url, ha_token, entity_id)
                    return jsonify(result)
                else:
                    return jsonify({'ok': False, 'detail': 'Home Assistant not configured in settings'}), 400

        # --- Play/pause/stop routing ---
        # For playback commands, decide between Kodi JSON-RPC and ADB based
        # on which device is actually playing.  If no explicit device_id was
        # provided, check if any device has an active Plex session — if so,
        # route to that device via ADB (not to a Kodi device that isn't playing).
        if command in ('play', 'pause', 'stop', 'play_pause') and device:
            playback_device = device
            if not req_device_id and len(devs) > 1:
                # No explicit device — check if a Plex device has an active session
                plex_dev = None
                for dev in devs:
                    if not isinstance(dev, dict):
                        continue
                    for svc in (dev.get('services') or []):
                        if isinstance(svc, dict) and svc.get('type') == 'plex':
                            plex_dev = dev
                            break
                    if plex_dev:
                        break
                if plex_dev:
                    playback_device = plex_dev

            # Route to Kodi only if the targeted device actually has Kodi
            kodi_svc = next(
                (s for s in playback_device.get('services', [])
                 if isinstance(s, dict) and s.get('type') == 'kodi'),
                None,
            )
            if kodi_svc:
                from app.kodi_service import KodiService
                kodi_host = kodi_svc.get('kodi_host', '') or playback_device.get('host', '')
                kodi_port = int(kodi_svc.get('kodi_port') or 8080)
                kodi_user = kodi_svc.get('kodi_username', '')
                kodi_pass = kodi_svc.get('kodi_password', '')
                try:
                    kodi = KodiService(kodi_host, kodi_port, kodi_user, kodi_pass)
                    player = kodi.get_active_player()
                    player_id = player.get('playerid', 1) if player else 1
                    if command == 'stop':
                        kodi.stop(player_id)
                    else:
                        kodi.pause_toggle(player_id)
                    return jsonify({'ok': True})
                except Exception as kodi_err:
                    logger.warning(f"Kodi remote {command} for room {room_id}: {kodi_err} — falling through to ADB")

            # ADB keyevent (primary for Plex devices, fallback when Kodi fails)
            pb_host = playback_device.get('host', '') or host
            pb_port = playback_device.get('connectPort') or port
            if pb_host:
                mac_address = playback_device.get('macAddress', '') or ''
                result = send_key_event(pb_host, int(pb_port), command, mac_address=mac_address)
                return jsonify(result)

        # --- ADB keyevent fallback ---
        if not host:
            return jsonify({'ok': False, 'detail': 'Room has no host/IP configured'}), 400

        mac_address = (device.get('macAddress', '') if device else '') or ''
        result = send_key_event(host, int(port), command, mac_address=mac_address)
        return jsonify(result)
    except Exception as e:
        logger.error(f"POST /api/room/{room_id}/remote error: {e}")
        return jsonify({'ok': False, 'detail': str(e)}), 500


@app.route('/api/plex/playqueue', methods=['POST'])
def create_plex_playqueue():
    """Create a Plex play queue (e.g. for shuffle-play mode)."""
    try:
        body = request.get_json(silent=True) or {}
        rating_key = body.get('rating_key')
        shuffle = body.get('shuffle', True)

        if not rating_key:
            return jsonify({'success': False, 'error': 'rating_key is required'}), 400

        plex_url = settings.get('plex.url')
        plex_token = settings.get('plex.token')
        machine_id = settings.get('plex.machine_id') or ''

        if not plex_url or not plex_token:
            return jsonify({'success': False, 'error': 'Plex not configured'}), 500

        # Fetch machine_id from Plex server if not saved in settings
        if not machine_id:
            try:
                root = plex_service._get('/')
                if root is not None:
                    machine_id = root.get('machineIdentifier', '')
                    if machine_id:
                        settings.update({'plex': {'machine_id': machine_id}})
                        logger.info(f"Auto-saved Plex machine_id: {machine_id}")
            except Exception as mid_err:
                logger.warning(f"Could not fetch Plex machine_id: {mid_err}")

        if not machine_id:
            return jsonify({'success': False, 'error': 'Plex machine_id not configured and could not be auto-detected'}), 500

        # Plex-native: python-plexapi PlayQueue.create(..., shuffle=1)
        try:
            from plexapi.server import PlexServer
            from plexapi.playqueue import PlayQueue
            plex = PlexServer(plex_url, plex_token, timeout=10)
            item = plex.fetchItem(int(rating_key))
            pq = PlayQueue.create(plex, item, shuffle=1 if shuffle else 0)
            pq_id = str(pq.playQueueID)
            logger.info(
                "PlayQueue.create rk=%s shuffle=%s -> pq=%s shuffled=%s selected=%s",
                rating_key, shuffle, pq_id, getattr(pq, 'playQueueShuffled', None),
                getattr(getattr(pq, 'selectedItem', None), 'title', None),
            )
            return jsonify({
                'success': True,
                'play_queue_id': pq_id,
                'shuffled': bool(getattr(pq, 'playQueueShuffled', shuffle)),
                'selected_rating_key': str(getattr(getattr(pq, 'selectedItem', None), 'ratingKey', '') or ''),
            })
        except Exception as plexapi_err:
            logger.warning("PlayQueue.create via plexapi failed (%s); falling back to HTTP", plexapi_err)

        uri = f'server://{machine_id}/com.plexapp.plugins.library/library/metadata/{rating_key}'
        resp = requests.post(
            f"{plex_url}/playQueues",
            params={
                'uri': uri,
                'type': 'video',
                'shuffle': '1' if shuffle else '0',
            },
            headers={
                'X-Plex-Client-Identifier': 'luma',
                'X-Plex-Token': plex_token,
                'Accept': 'application/xml',
            },
            timeout=10,
        )

        if resp.status_code in (200, 201):
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.content)
            pq_id = root.get('playQueueID', '')
            return jsonify({'success': True, 'play_queue_id': pq_id})
        else:
            logger.error(f"Plex /playQueues returned {resp.status_code}: {resp.text[:500]}")
            return jsonify({'success': False, 'error': f'Plex returned {resp.status_code}'}), 502

    except Exception as e:
        logger.error(f"POST /api/plex/playqueue error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Connection test helpers
# ---------------------------------------------------------------------------

def _test_settings(section_key, override_body):
    """Merge override values from the request body on top of current saved
    settings for *section_key* (a dotted path) so the user can test before
    saving.  Returns the merged dict for that section.
    """
    import copy as _copy
    current = settings.get(section_key)
    if current is None:
        current = {}
    current = _copy.deepcopy(current) if isinstance(current, dict) else {}
    if override_body and isinstance(override_body, dict):
        for k, v in override_body.items():
            if v != SECRET_MASK:
                current[k] = v
    return current


def _infer_type_from_plex_section(section: dict) -> str:
    """Guess a media type tag from a Plex library section's type and title."""
    sec_type = section.get('type', '')
    title_lower = section.get('title', '').lower()

    # TV sections
    if sec_type == 'show':
        if '4k' in title_lower or '2160' in title_lower:
            return 'tv_4k'
        if 'remux' in title_lower:
            return 'tv_remux'
        return 'tv'

    # Movie sections
    if '4k' in title_lower or '2160' in title_lower:
        return '4k'
    if 'remux' in title_lower or 'fullrip' in title_lower or 'full rip' in title_lower:
        return 'fullrips'
    if 'recent' in title_lower:
        return 'recent'
    return 'movies'


@app.route('/api/settings/media/types', methods=['GET'])
def get_media_types_endpoint():
    """Return the list of media type definitions."""
    err = _require_admin()
    if err:
        return err
    try:
        return jsonify({'types': settings.get_media_types()})
    except Exception as e:
        logger.error(f"GET /api/settings/media/types error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/media/sync-from-plex', methods=['POST'])
def sync_media_paths_from_plex():
    """Auto-discover Plex library sections and sync into media.paths.

    For each Plex section:
    - If an entry with matching plex_section_id exists → update plex_locations
    - Else if an entry with matching name exists → set plex_section_id + plex_locations
    - Else → create a new entry with name from Plex, empty path/kodi_path, inferred type
    """
    err = _require_admin()
    if err:
        return err
    try:
        sections = plex_service.get_sections_with_locations()
        if not sections:
            return jsonify({'success': False, 'message': 'Could not fetch Plex sections. Check Plex connection.'}), 500

        paths = settings.get('media.paths') or []
        # Build quick-lookup indexes
        by_section_id = {p['plex_section_id']: i for i, p in enumerate(paths) if isinstance(p, dict) and p.get('plex_section_id')}
        by_name = {p['name'].lower(): i for i, p in enumerate(paths) if isinstance(p, dict) and p.get('name')}

        for sec in sections:
            sec_id = sec['id']
            sec_title = sec.get('title', f'Library {sec_id}')
            sec_locations = sec.get('locations', [])

            if sec_id in by_section_id:
                # Already linked — just update locations
                idx = by_section_id[sec_id]
                paths[idx]['plex_locations'] = sec_locations
            elif sec_title.lower() in by_name:
                # Name match (first sync) — link to this section
                idx = by_name[sec_title.lower()]
                paths[idx]['plex_section_id'] = sec_id
                paths[idx]['plex_locations'] = sec_locations
                by_section_id[sec_id] = idx
            else:
                # New entry
                new_entry = {
                    'name': sec_title,
                    'path': '',
                    'kodi_path': '',
                    'type': _infer_type_from_plex_section(sec),
                    'plex_section_id': sec_id,
                    'plex_locations': sec_locations,
                }
                paths.append(new_entry)
                by_section_id[sec_id] = len(paths) - 1
                by_name[sec_title.lower()] = len(paths) - 1

        ok, errors = settings.update({'media': {'paths': paths}})
        if not ok:
            return jsonify({'success': False, 'errors': errors}), 400

        return jsonify({'success': True, 'paths': settings.get('media.paths')})
    except Exception as e:
        logger.error(f"POST /api/settings/media/sync-from-plex error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/settings/test/plex', methods=['POST'])
def test_plex_connection():
    """Test connectivity to the Plex Media Server."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        cfg = _test_settings('plex', body)
        url = cfg.get('url', '').rstrip('/')
        token = cfg.get('token', '')
        if not url:
            return jsonify({'success': False, 'message': 'Plex URL is required'}), 400
        if not token:
            return jsonify({'success': False, 'message': 'Plex token is required'}), 400

        resp = requests.get(
            url,
            headers={'X-Plex-Token': token, 'Accept': 'application/xml'},
            timeout=5,
        )
        if resp.status_code == 200:
            friendly = 'Unknown'
            machine_id = ''
            content = resp.text.strip()
            # Try XML first (Plex default for Accept: application/xml)
            if content.startswith('<?xml') or content.startswith('<MediaContainer'):
                import xml.etree.ElementTree as ET
                root = ET.fromstring(content)
                friendly = root.attrib.get('friendlyName', 'Unknown')
                machine_id = root.attrib.get('machineIdentifier', '')
            else:
                # Fallback: try JSON (some Plex versions return JSON)
                try:
                    data = resp.json()
                    mc = data.get('MediaContainer', data)
                    friendly = mc.get('friendlyName', 'Unknown')
                    machine_id = mc.get('machineIdentifier', '')
                except Exception:
                    pass
            return jsonify({
                'success': True,
                'message': f'Connected to Plex server: {friendly}',
                'details': {'friendlyName': friendly, 'machineIdentifier': machine_id}
            })
        return jsonify({'success': False, 'message': f'Plex returned status {resp.status_code}'}), 200
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (5s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to Plex URL'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/sonarr', methods=['POST'])
def test_sonarr_connection():
    """Test connectivity to Sonarr."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        cfg = _test_settings('sonarr', body)
        url = cfg.get('url', '').rstrip('/')
        api_key = cfg.get('api_key', '')
        if not url:
            return jsonify({'success': False, 'message': 'Sonarr URL is required'}), 400
        if not api_key:
            return jsonify({'success': False, 'message': 'Sonarr API key is required'}), 400

        resp = requests.get(f"{url}/api/v3/system/status", headers={'X-Api-Key': api_key}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            version = data.get('version', 'unknown')
            return jsonify({'success': True, 'message': f'Connected to Sonarr v{version}', 'details': {'version': version}})
        return jsonify({'success': False, 'message': f'Sonarr returned status {resp.status_code}'}), 200
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (5s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to Sonarr URL'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/radarr', methods=['POST'])
def test_radarr_connection():
    """Test connectivity to Radarr."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        cfg = _test_settings('radarr', body)
        url = cfg.get('url', '').rstrip('/')
        api_key = cfg.get('api_key', '')
        if not url:
            return jsonify({'success': False, 'message': 'Radarr URL is required'}), 400
        if not api_key:
            return jsonify({'success': False, 'message': 'Radarr API key is required'}), 400

        resp = requests.get(f"{url}/api/v3/system/status", headers={'X-Api-Key': api_key}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            version = data.get('version', 'unknown')
            return jsonify({'success': True, 'message': f'Connected to Radarr v{version}', 'details': {'version': version}})
        return jsonify({'success': False, 'message': f'Radarr returned status {resp.status_code}'}), 200
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (5s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to Radarr URL'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/qbittorrent', methods=['POST'])
def test_qbittorrent_connection():
    """Test authentication to qBittorrent."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        cfg = _test_settings('qbittorrent', body)
        url = cfg.get('url', '').rstrip('/')
        username = cfg.get('username', '')
        password = cfg.get('password', '')
        if not url:
            return jsonify({'success': False, 'message': 'qBittorrent URL is required'}), 400

        resp = requests.post(
            f"{url}/api/v2/auth/login",
            data={'username': username, 'password': password},
            timeout=5
        )
        if resp.status_code == 200 and resp.text.strip().upper() == 'OK.':
            # Grab version
            sid = resp.cookies.get('SID', '')
            ver_resp = requests.get(f"{url}/api/v2/app/version", cookies={'SID': sid}, timeout=5)
            version = ver_resp.text.strip() if ver_resp.status_code == 200 else 'unknown'
            return jsonify({'success': True, 'message': f'Authenticated to qBittorrent {version}', 'details': {'version': version}})
        return jsonify({'success': False, 'message': 'Authentication failed -- check username/password'}), 200
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (5s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to qBittorrent URL'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/harmony', methods=['POST'])
def test_harmony_connection():
    """Test connectivity to a Harmony Hub via direct WebSocket connection."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        hub_ip = (body.get('hub_ip', '') or '').strip()
        if not hub_ip:
            return jsonify({'success': False, 'message': 'Harmony Hub IP is required'}), 400

        from app import harmony_service
        config = harmony_service.get_config(hub_ip)
        if config is None:
            return jsonify({'success': False, 'message': f'Could not connect to Harmony Hub at {hub_ip}'}), 200

        activities = [a.get('label', '') for a in config.get('activities', []) if a.get('label')]
        devices = [d.get('label', '') for d in config.get('devices', []) if d.get('label')]
        return jsonify({
            'success': True,
            'message': f'Connected to Harmony Hub ({len(activities)} activities, {len(devices)} devices)',
            'details': {'activities': activities, 'devices': devices, 'hub_ip': hub_ip},
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/tmdb', methods=['POST'])
def test_tmdb_connection():
    """Test TMDb API key validity."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        # TMDb key is nested under apis.tmdb
        apis_cfg = _test_settings('apis', body.get('apis', body))
        if isinstance(apis_cfg, dict) and 'tmdb' in apis_cfg:
            tmdb_cfg = apis_cfg['tmdb']
        else:
            tmdb_cfg = {}
        # Also allow flat body with just api_key
        api_key = body.get('api_key') or tmdb_cfg.get('api_key') or settings.get('apis.tmdb.api_key', '')
        if not api_key or api_key == SECRET_MASK:
            api_key = settings.get('apis.tmdb.api_key', '')
        if not api_key:
            return jsonify({'success': False, 'message': 'TMDb API key is required'}), 400

        resp = requests.get(
            'https://api.themoviedb.org/3/configuration',
            params={'api_key': api_key},
            timeout=5
        )
        if resp.status_code == 200:
            return jsonify({'success': True, 'message': 'TMDb API key is valid', 'details': {}})
        elif resp.status_code == 401:
            return jsonify({'success': False, 'message': 'Invalid TMDb API key'}), 200
        return jsonify({'success': False, 'message': f'TMDb returned status {resp.status_code}'}), 200
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (5s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to TMDb API'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/indexer', methods=['POST'])
def test_indexer_connection():
    """Test connectivity to the configured indexer (Prowlarr or Torznab)."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        cfg = _test_settings('indexer', body)
        provider = cfg.get('provider', 'prowlarr')
        api_key = cfg.get('api_key', '')
        if not api_key or api_key == SECRET_MASK:
            api_key = settings.get('indexer.api_key', '')
        if not api_key:
            return jsonify({'success': False, 'message': 'Indexer API key is required'}), 400

        if provider == 'torznab':
            torznab_url = cfg.get('torznab_url', '')
            if not torznab_url:
                return jsonify({'success': False, 'message': 'Torznab URL is required'}), 400
            from app.indexer_api import TorznabClient
            client = TorznabClient(torznab_url, api_key)
        else:
            base_url = cfg.get('base_url', '')
            if not base_url:
                return jsonify({'success': False, 'message': 'Prowlarr URL is required'}), 400
            from app.indexer_api import ProwlarrClient
            client = ProwlarrClient(base_url, api_key)

        if client.test_connection():
            return jsonify({'success': True, 'message': f'{provider.title()} connection successful', 'details': {}})
        return jsonify({'success': False, 'message': f'{provider.title()} connection failed'}), 200
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (10s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to indexer'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/shield-adb', methods=['POST'])
def test_shield_adb():
    """Test ADB connectivity to a room/device (legacy endpoint, delegates to adb_utils)."""
    err = _require_admin()
    if err:
        return err
    from app.adb_utils import adb_connect as _adb_connect
    try:
        body = request.get_json(silent=True) or {}
        ip = body.get('ip', '') or body.get('host', '') or settings.get_default_device_ip()
        port = int(body.get('adb_port', body.get('connectPort', settings.get_default_adb_port())))

        result = _adb_connect(ip, port)
        return jsonify({
            'success': result['ok'],
            'message': f"ADB {'connected to' if result['ok'] else 'failed:'} {ip}:{port} — {result['detail']}",
            'details': result,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/trakt', methods=['POST'])
def test_trakt_connection():
    """Test Trakt API client ID validity."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        client_id = body.get('client_id', '')
        if not client_id or client_id == SECRET_MASK:
            client_id = settings.get('apis.trakt.client_id', '')
        if not client_id:
            return jsonify({'success': False, 'message': 'Trakt client ID is required'}), 400

        resp = requests.get(
            'https://api.trakt.tv/shows/trending',
            headers={
                'Content-Type': 'application/json',
                'trakt-api-version': '2',
                'trakt-api-key': client_id,
            },
            params={'limit': 1},
            timeout=5,
        )
        if resp.status_code == 200:
            return jsonify({'success': True, 'message': 'Trakt API connection successful'})
        elif resp.status_code == 401:
            return jsonify({'success': False, 'message': 'Invalid Trakt client ID'}), 200
        elif resp.status_code == 429:
            return jsonify({'success': False, 'message': 'Trakt rate-limited — try again shortly'}), 200
        return jsonify({'success': False, 'message': f'Trakt returned status {resp.status_code}'}), 200
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (5s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not connect to Trakt API'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/homeassistant', methods=['POST'])
def test_homeassistant_connection():
    """Test Home Assistant connectivity."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        cfg = _test_settings('homeassistant', body)
        ha_url = cfg.get('url', '')
        ha_token = cfg.get('access_token', '')
        if not ha_url:
            return jsonify({'success': False, 'message': 'Home Assistant URL is required'}), 400
        if not ha_token:
            return jsonify({'success': False, 'message': 'Access token is required'}), 400

        from app.ha_service import test_connection
        result = test_connection(ha_url, ha_token)
        return jsonify({
            'success': result['ok'],
            'message': result['detail'],
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/api/settings/test/openai', methods=['POST'])
def test_openai_connection():
    """Test OpenAI API key and model validity."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        api_key = body.get('api_key', '')
        if not api_key or api_key == SECRET_MASK:
            api_key = settings.get('apis.openai.api_key', '')
        if not api_key:
            return jsonify({'success': False, 'message': 'OpenAI API key is required'}), 400

        model = body.get('model', '') or settings.get('apis.openai.model', 'gpt-4o-mini')

        from app import openai_client as openai_lib
        client = openai_lib.OpenAI(api_key=api_key)
        model_info = client.models.retrieve(model)
        return jsonify({'success': True, 'message': f'Connected successfully. Model: {model_info.id}'})
    except Exception as e:
        err = str(e)
        if 'AuthenticationError' in type(e).__name__ or 'Incorrect API key' in err:
            return jsonify({'success': False, 'message': 'Invalid OpenAI API key'}), 200
        if 'NotFoundError' in type(e).__name__:
            return jsonify({'success': False, 'message': f'Model "{model}" not found or not accessible'}), 200
        return jsonify({'success': False, 'message': err}), 200


@app.route('/api/settings/test/kodi', methods=['POST'])
def test_kodi_connection():
    """Test Kodi JSON-RPC connectivity."""
    err = _require_admin()
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        host = body.get('host', '').strip()
        port = int(body.get('port', 8080))
        username = body.get('username', '')
        password = body.get('password', '')

        if not host:
            return jsonify({'success': False, 'message': 'Kodi host is required'}), 400

        from app.kodi_service import KodiService
        kodi = KodiService(host=host, port=port, username=username, password=password)
        if kodi.ping():
            return jsonify({'success': True, 'message': f'Connected to Kodi at {host}:{port}'})
        else:
            return jsonify({'success': False, 'message': f'Kodi at {host}:{port} did not respond to ping'})
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timed out (10s)'}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': f'Could not connect to {host}:{port}'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


# ---------------------------------------------------------------------------
# Cloudflare Tunnel endpoints
# ---------------------------------------------------------------------------

@app.route('/api/tunnel/status', methods=['GET'])
def tunnel_status():
    """Return current Cloudflare Tunnel status."""
    return jsonify(get_tunnel_status())


@app.route('/api/tunnel/toggle', methods=['POST'])
def tunnel_toggle():
    """Start or stop the Cloudflare Tunnel based on current settings."""
    try:
        enabled = settings.get('network.tunnel.enabled', False)
        token = settings.get('network.tunnel.token', '')
        if enabled and token:
            result = start_tunnel(token)
        else:
            result = stop_tunnel()
        return jsonify(result)
    except Exception as e:
        logger.error(f"POST /api/tunnel/toggle error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/settings/test/tunnel', methods=['POST'])
def test_tunnel():
    """Check cloudflared binary and report version + status."""
    err = _require_admin()
    if err:
        return err
    try:
        version = get_cloudflared_version()
        if not version:
            return jsonify({
                'success': False,
                'message': 'cloudflared binary not found — rebuild Docker image',
            }), 200

        status = get_tunnel_status()
        msg = f'cloudflared ready — {version}'
        if status['running']:
            msg += f' (tunnel running, PID {status["pid"]})'
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 200


# =============================================================================
# Automation features (on/off summary for Settings → Automation)
# =============================================================================

@app.route('/api/automation/features')
def automation_features_status():
    """Which automation features and jobs are switched on.

    ``runtime_disabled`` is true on an instance started with
    LUMA_DISABLE_AUTOMATIONS=1 (e.g. the dev instance): switches still save,
    but no job thread runs there.
    """
    from app.automation_features import summary as _automation_summary
    return jsonify({
        'features': _automation_summary(settings),
        'runtime_disabled': _AUTOMATIONS_DISABLED,
    })


# =============================================================================
# Trending Watchlist Automation
# =============================================================================

from app.trending_watchlist import get_status as _tw_get_status, get_log as _tw_get_log, trigger_manual_run as _tw_trigger

@app.route('/api/automation/trending-watchlist/status')
def trending_watchlist_status():
    """Return last run info for the trending watchlist automation."""
    return jsonify(_tw_get_status())

@app.route('/api/automation/trending-watchlist/run', methods=['POST'])
def trending_watchlist_run():
    """Manually trigger a trending watchlist cycle."""
    _tw_trigger()
    return jsonify({'success': True, 'message': 'Cycle triggered'})

@app.route('/api/automation/trending-watchlist/log')
def trending_watchlist_log():
    """Return the persistent log of movies added by the trending watchlist."""
    return jsonify(_tw_get_log())

# ── Trending TV Watchlist Automation ──────────────────────────────

from app.trending_tv_watchlist import get_status as _ttv_get_status, get_log as _ttv_get_log, trigger_manual_run as _ttv_trigger

@app.route('/api/automation/trending-tv-watchlist/status')
def trending_tv_watchlist_status():
    """Return last run info for the trending TV watchlist automation."""
    return jsonify(_ttv_get_status())

@app.route('/api/automation/trending-tv-watchlist/run', methods=['POST'])
def trending_tv_watchlist_run():
    """Manually trigger a trending TV watchlist cycle."""
    _ttv_trigger()
    return jsonify({'success': True, 'message': 'Cycle triggered'})

@app.route('/api/automation/trending-tv-watchlist/log')
def trending_tv_watchlist_log():
    """Return the persistent log of shows added by the trending TV watchlist."""
    return jsonify(_ttv_get_log())

# ── Now Available Watchlist Automation ─────────────────────────────

from app.now_available_watchlist import get_status as _naw_get_status, get_log as _naw_get_log, trigger_manual_run as _naw_trigger

@app.route('/api/automation/now-available-watchlist/status')
def now_available_watchlist_status():
    """Return last run info for the now available watchlist automation."""
    return jsonify(_naw_get_status())

@app.route('/api/automation/now-available-watchlist/run', methods=['POST'])
def now_available_watchlist_run():
    """Manually trigger a now available watchlist cycle."""
    _naw_trigger()
    return jsonify({'success': True, 'message': 'Cycle triggered'})

@app.route('/api/automation/now-available-watchlist/log')
def now_available_watchlist_log():
    """Return the persistent log of movies added by the now available watchlist."""
    return jsonify(_naw_get_log())

# ── Anticipated Watchlist Automation ───────────────────────────────

from app.anticipated_watchlist import get_status as _anw_get_status, get_log as _anw_get_log, trigger_manual_run as _anw_trigger

@app.route('/api/automation/anticipated-watchlist/status')
def anticipated_watchlist_status():
    """Return last run info for the anticipated watchlist automation."""
    return jsonify(_anw_get_status())

@app.route('/api/automation/anticipated-watchlist/run', methods=['POST'])
def anticipated_watchlist_run():
    """Manually trigger an anticipated watchlist cycle."""
    _anw_trigger()
    return jsonify({'success': True, 'message': 'Cycle triggered'})

@app.route('/api/automation/anticipated-watchlist/log')
def anticipated_watchlist_log():
    """Return the persistent log of titles pre-ordered by the anticipated watchlist."""
    return jsonify(_anw_get_log())

# ── Latest Auto-Grab Automation ────────────────────────────────────

from app.latest_auto_grab import (
    get_status as _lag_get_status,
    get_log as _lag_get_log,
    trigger_manual_run as _lag_trigger,
)

@app.route('/api/automation/latest-auto-grab/status')
def latest_auto_grab_status():
    """Return last run info for the Latest auto-grab automation."""
    return jsonify(_lag_get_status())

@app.route('/api/automation/latest-auto-grab/run', methods=['POST'])
def latest_auto_grab_run():
    """Manually trigger a Latest auto-grab cycle."""
    _lag_trigger()
    return jsonify({'success': True, 'message': 'Cycle triggered'})

@app.route('/api/automation/latest-auto-grab/log')
def latest_auto_grab_log():
    """Return the persistent log of releases grabbed by the Latest auto-grab."""
    return jsonify(_lag_get_log())


# ── 4K Remux Auto-Grab Automation ──────────────────────────────────

from app.upgrade_4k_auto_grab import (
    get_status as _u4k_get_status,
    get_log as _u4k_get_log,
    trigger_manual_run as _u4k_trigger,
)

@app.route('/api/automation/upgrade-4k-auto-grab/status')
def upgrade_4k_auto_grab_status():
    """Return last run info for the 4K Remux auto-grab automation."""
    return jsonify(_u4k_get_status())

@app.route('/api/automation/upgrade-4k-auto-grab/run', methods=['POST'])
def upgrade_4k_auto_grab_run():
    """Manually trigger a 4K Remux auto-grab cycle."""
    _u4k_trigger()
    return jsonify({'success': True, 'message': 'Cycle triggered'})

@app.route('/api/automation/upgrade-4k-auto-grab/log')
def upgrade_4k_auto_grab_log():
    """Return the persistent log of releases grabbed by the 4K auto-grab."""
    return jsonify(_u4k_get_log())


# ── Latest automation addition (for Daily Kiosk "Now Available" widget) ───

_latest_auto_cache: dict = {}
_latest_auto_cache_ts: float = 0

@app.route('/api/automation/latest')
def automation_latest():
    """Return a "now available" pick for the daily kiosk.

    Strategy: pull the 10 most-recently-added titles from the automation
    activity log (across watchlist/trending-watchlist/trending-tv sources),
    enrich each with TMDb rating data in parallel, then return the single
    title with the highest vote_count. Ties break by newer added_at.

    Ranking by vote_count (not vote_average) surfaces the movie the
    broadest audience has actually seen — a 9.5 rating from 40 voters
    isn't as meaningful as 8.1 from 20,000. The fetched rating and vote
    count are included in the response so the widget can display them.
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor
    import requests as _req
    global _latest_auto_cache_ts

    now = _time.time()
    if _latest_auto_cache and (now - _latest_auto_cache_ts) < 300:
        return jsonify(_latest_auto_cache)

    # Gather logs from all automation sources (each returns newest-first).
    all_entries = []
    for get_log_fn in [_naw_get_log, _tw_get_log, _ttv_get_log]:
        try:
            entries = get_log_fn()
            if entries:
                all_entries.extend(entries[:10])
        except Exception:
            pass

    if not all_entries:
        return jsonify({'found': False})

    # "Now Available" must mean available in the Plex library — not merely
    # added to the watchlist by an automation (which may never have been
    # grabbed, e.g. a still-monitored title with no release yet). Filter the
    # candidate pool to titles actually present in Plex.
    owned_movies = owned_tv = None
    try:
        from app.plex_service import plex_service
        _idx = plex_service.build_ownership_index()
        owned_movies = _idx.get('movies_by_tmdb', {})
        owned_tv = _idx.get('tv_by_tmdb', {})
    except Exception as e:
        # Plex unreachable → can't verify; skip filtering rather than blank
        # the widget on a transient hiccup.
        logger.warning(f"automation_latest: Plex ownership check unavailable: {e}")

    def _in_plex(entry):
        if owned_movies is None:
            return True
        tid = entry.get('tmdb_id')
        if not tid:
            return False
        tid = int(tid)
        return tid in owned_movies or tid in owned_tv

    # Sort newest-first, de-dupe by (tmdb_id, media_type), take top 10.
    all_entries.sort(key=lambda e: e.get('added_at', ''), reverse=True)
    seen = set()
    pool: list = []
    for e in all_entries:
        key = (e.get('tmdb_id'), e.get('media_type', 'movie'))
        if key in seen or not key[0]:
            continue
        if not _in_plex(e):
            continue
        seen.add(key)
        pool.append(e)
        if len(pool) >= 10:
            break

    if not pool:
        return jsonify({'found': False})

    tmdb_key = settings.get('apis.tmdb.api_key', '') or Config.TMDB_API_KEY

    def fetch_tmdb(entry):
        tmdb_id = entry.get('tmdb_id')
        media_type = entry.get('media_type', 'movie')
        endpoint = 'tv' if media_type == 'tv' else 'movie'
        try:
            resp = _req.get(
                f'https://api.themoviedb.org/3/{endpoint}/{tmdb_id}',
                params={'api_key': tmdb_key, 'append_to_response': 'credits,external_ids'},
                timeout=6,
            )
            if not resp.ok:
                return None
            data = resp.json()
            return {
                'entry': entry,
                'data': data,
                'vote_count': int(data.get('vote_count') or 0),
                'vote_average': float(data.get('vote_average') or 0.0),
            }
        except Exception:
            return None

    # Parallel TMDb lookups — worst case 10 calls in ~1 request round-trip.
    with ThreadPoolExecutor(max_workers=8) as pool_executor:
        enriched = [x for x in pool_executor.map(fetch_tmdb, pool) if x]

    if not enriched:
        # Fall back to the newest entry if every TMDb call failed.
        latest_fallback = pool[0]
        result = {
            'found': True,
            'title': latest_fallback.get('title', 'Unknown'),
            'year': latest_fallback.get('year', ''),
            'tmdb_id': latest_fallback.get('tmdb_id'),
            'media_type': latest_fallback.get('media_type', 'movie'),
            'added_at': latest_fallback.get('added_at', ''),
            'backdrop_url': '',
            'overview': '',
            'cast': [],
            'rating': 0.0,
            'vote_count': 0,
        }
        _latest_auto_cache.clear()
        _latest_auto_cache.update(result)
        _latest_auto_cache_ts = now
        return jsonify(result)

    # Rank by vote_count descending; tie-break on newer added_at.
    enriched.sort(
        key=lambda x: (x['vote_count'], x['entry'].get('added_at', '')),
        reverse=True,
    )
    pick = enriched[0]
    entry = pick['entry']
    data = pick['data']

    title = entry.get('title') or data.get('title') or data.get('name') or 'Unknown'
    year = entry.get('year', '')
    if not year:
        rd = data.get('release_date') or data.get('first_air_date') or ''
        year = rd[:4] if rd else ''

    credits = data.get('credits') or {}
    cast_names = [
        c.get('name', '') for c in (credits.get('cast') or [])[:3]
        if c.get('name')
    ]
    backdrop_path = data.get('backdrop_path', '')

    imdb_id = ((data.get('external_ids') or {}).get('imdb_id')
               if isinstance(data.get('external_ids'), dict) else None) or data.get('imdb_id')
    # Prefer IMDb for the kiosk badge; TMDb vote_count stays ranking-only.
    tmp = [{'imdb_id': imdb_id or '', 'title': title, 'year': year,
            'rating': pick['vote_average']}]
    enrich_imdb_ratings(tmp, settings=settings, max_items=1, replace_rating=True)
    imdb_score = tmp[0].get('imdb_rating')
    imdb_votes = tmp[0].get('imdb_votes')

    result = {
        'found': True,
        'title': title,
        'year': year,
        'tmdb_id': entry.get('tmdb_id'),
        'media_type': entry.get('media_type', 'movie'),
        'added_at': entry.get('added_at', ''),
        'backdrop_url': f'https://image.tmdb.org/t/p/original{backdrop_path}' if backdrop_path else '',
        'overview': (data.get('overview') or '').strip(),
        'cast': cast_names,
        'rating': imdb_score,  # IMDb only — never TMDb vote_average
        'vote_count': imdb_votes if imdb_votes is not None else 0,
        'tmdb_vote_count': pick['vote_count'],
        'imdb_id': imdb_id or data.get('imdb_id'),
    }

    _latest_auto_cache.clear()
    _latest_auto_cache.update(result)
    _latest_auto_cache_ts = now
    return jsonify(result)

# =============================================================================
# SPA catch-all — React Router paths (must be LAST route registered)
# =============================================================================

@app.route('/trending/<path:rest>')
def spa_trending(rest=''):
    """Serve React SPA for /trending/* routes."""
    return _serve_spa()

# Initialize app
# ---------------------------------------------------------------------------
# Start flow (first run: Sign in with Plex, create PIN)
# ---------------------------------------------------------------------------

def _start_plex_saved(url: str, token: str, machine_id: str) -> int:
    """Save the Plex server picked in the Start flow and sync its libraries."""
    settings.update({'plex': {'url': url, 'token': token, 'machine_id': machine_id}})
    os.environ['PLEX_URL'], os.environ['PLEX_TOKEN'] = url, token
    os.environ['PLEX_MACHINE_ID'] = machine_id
    try:
        plex_service.update_config(url, token)
    except Exception as e:
        logger.warning(f"Start: plex_service update failed: {e}")
    try:
        sections = plex_service.get_sections_with_locations() or []
        paths = [{
            'name': sec.get('title') or f"Library {sec['id']}",
            'path': '', 'kodi_path': '',
            'type': _infer_type_from_plex_section(sec),
            'plex_section_id': sec['id'],
            'plex_locations': sec.get('locations', []),
        } for sec in sections]
        if paths:
            settings.update({'media': {'paths': paths}})
        return len(paths)
    except Exception as e:
        logger.warning(f"Start: library sync failed: {e}")
        return 0


@app.route('/api/integrations/status')
def integrations_status():
    """Which optional services are connected (settings presence only, no network calls)."""
    err = _require_auth()
    if err:
        return err
    def _set(*paths):
        return all(bool((settings.get(p) or '').strip()) if isinstance(settings.get(p), str) else bool(settings.get(p)) for p in paths)
    return jsonify({
        'plex': _set('plex.url', 'plex.token'),
        'tmdb': bool(settings.get('apis.tmdb.api_key') or os.environ.get('TMDB_API_KEY')),
        'radarr': _set('radarr.url', 'radarr.api_key'),
        'sonarr': _set('sonarr.url', 'sonarr.api_key'),
        'qbittorrent': _set('qbittorrent.url'),
        'indexer': _set('indexer.base_url', 'indexer.api_key'),
        'home_assistant': _set('homeassistant.url', 'homeassistant.access_token'),
        'rooms': len(settings.get('devices.rooms') or []),
    })


@app.route('/api/integrations/tmdb', methods=['POST'])
def integrations_tmdb():
    """Test (and optionally save) a TMDB API key from the Connect TMDB sheet."""
    err = _require_admin()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    key = str(body.get('api_key') or '').strip()
    if not key:
        return jsonify({'ok': False, 'error': 'Enter an API key'}), 400
    if key.startswith('eyJ'):
        return jsonify({'ok': False, 'error': 'Use the API Key, not the Read Access Token'}), 400
    try:
        r = requests.get('https://api.themoviedb.org/3/configuration', params={'api_key': key}, timeout=8)
    except Exception:
        return jsonify({'ok': False, 'error': "Couldn't reach TMDB"}), 502
    if r.status_code != 200:
        return jsonify({'ok': False, 'error': 'Key not accepted'}), 400
    if not body.get('save'):
        return jsonify({'ok': True})
    settings.update({'apis': {'tmdb': {'api_key': key}}})
    os.environ['TMDB_API_KEY'] = key
    Config.TMDB_API_KEY = key
    logger.info('TMDB connected')
    restart = os.environ.get('LUMA_NO_RESTART', '') not in ('1', 'true')
    if restart:
        # Several caches read the key once at startup; a quick restart picks it up everywhere.
        threading.Thread(target=_start_flow._restart_soon, daemon=True).start()
    return jsonify({'ok': True, 'restarting': restart})


from app import start_flow as _start_flow
_start_flow.init(
    settings=settings, user_auth=user_auth, session_cookie=AUTH_COOKIE,
    client_id=_INSTALL_SECRETS['plex_client_id'], on_plex_saved=_start_plex_saved,
    get_current_user=_get_current_user, secret=app.config['SECRET_KEY'],
)
app.register_blueprint(_start_flow.bp)

def _on_tmdb_key(key: str) -> None:
    """A Connectors save: the new key applies at once, no restart."""
    tmdb.api_key = key or None
    Config.TMDB_API_KEY = key
    if key:
        os.environ['TMDB_API_KEY'] = key
    else:
        os.environ.pop('TMDB_API_KEY', None)
    try:
        if key:
            threading.Thread(target=fast_search_index._refresh_tmdb_titles, daemon=True).start()
    except Exception:
        pass


def _on_trakt_change() -> None:
    """A Connectors save or remove: drop cached Trakt rails at once."""
    _trakt_cache.clear()


_TRAKT_VIEWS = {
    'trakt_movies': lambda: trakt_trending_movies(),
    'trakt_shows': lambda: trakt_trending_shows(),
    'trakt_anticipated_movies': lambda: trakt_anticipated_movies(),
    'trakt_anticipated_shows': lambda: trakt_anticipated_shows(),
}


def _fetch_trakt_rail(rail_id: str) -> list:
    """Prod's Trakt endpoint output for a Home rail (its cache, 429 handling
    and TMDB poster lookup), called in-process with limit=20."""
    view = _TRAKT_VIEWS.get(rail_id)
    if view is None:
        return []
    with app.test_request_context('/', query_string={'limit': '20', 'page': '1'}):
        rv = view()
    resp, code = (rv if isinstance(rv, tuple) else (rv, 200))
    if code != 200:
        return []
    data = resp.get_json(silent=True) or {}
    return data.get('movies') or data.get('shows') or []


from app import connectors as _connectors
_connectors.init(settings=settings, owner_or_error=_start_flow._owner_or_error, on_tmdb_key=_on_tmdb_key,
                 on_trakt_change=_on_trakt_change)
app.register_blueprint(_connectors.bp)

from app import home_rails as _home_rails
_home_rails.init(get_plex=lambda: plex_service, get_current_user=_get_current_user,
                 trakt_connected=_connectors.trakt_connected, fetch_trakt=_fetch_trakt_rail)
app.register_blueprint(_home_rails.bp)


if __name__ == '__main__':
    # Initialize services (database, caches, background threads)
    start_services(app)
    
    # Run the app with Flask dev server (development mode)
    from app.server_config import get_port, get_bind_address, get_https_cert
    
    port = get_port(settings)
    bind = get_bind_address(settings)
    https_config = get_https_cert(settings)
    
    logger.info(f"Starting Luma (dev mode) on {'https' if https_config else 'http'}://{bind}:{port}")
    
    # Use Flask development server
    if https_config:
        cert_path, key_path = https_config
        app.run(host=bind, port=port, debug=False, ssl_context=(cert_path, key_path))
    else:
        app.run(host=bind, port=port, debug=False)
