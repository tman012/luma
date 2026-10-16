"""Gunicorn configuration for Luma production deployment.

Bind address, port and HTTPS come from app.server_config, so the values saved
in Settings apply here too (LUMA_BIND / LUMA_PORT / LUMA_HTTPS_CERT and
LUMA_HTTPS_KEY in the environment still take precedence).
"""

import logging

from app import server_config
from app.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


def _bind(settings) -> str:
    host = server_config.get_bind_address(settings)
    port = server_config.get_port(settings)
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'  # IPv6 literal
    return f'{host}:{port}'


def _tls(settings):
    cert = server_config.get_https_cert(settings)
    return cert if cert else (None, None)


_settings = SettingsManager()

# Worker configuration
bind = _bind(_settings)
workers = 1
threads = 8
worker_class = 'gthread'
timeout = 120

# Logging
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# HTTPS configuration (both None means plain HTTP)
certfile, keyfile = _tls(_settings)


def post_worker_init(worker):
    """Initialize services after worker starts.
    
    This ensures database, caches, and background threads are initialized
    AFTER the fork, so they work correctly under gunicorn.
    """
    logger.info(f"[gunicorn] Initializing worker {worker.pid}")
    
    # Import and call start_services
    from app.main import app, start_services
    
    try:
        start_services(app)
        logger.info(f"[gunicorn] Worker {worker.pid} initialized successfully")
    except Exception as e:
        logger.error(f"[gunicorn] Worker {worker.pid} initialization failed: {e}", exc_info=True)
        raise
