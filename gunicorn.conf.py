"""Gunicorn configuration for Luma production deployment."""

import os
import logging

logger = logging.getLogger(__name__)

# Worker configuration
bind = f"{os.environ.get('LUMA_BIND', '0.0.0.0')}:{os.environ.get('LUMA_PORT', '5001')}"
workers = 1
threads = 8
worker_class = 'gthread'
timeout = 120

# Logging
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# HTTPS configuration
certfile = os.environ.get('LUMA_HTTPS_CERT')
keyfile = os.environ.get('LUMA_HTTPS_KEY')


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
