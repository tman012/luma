"""App logs persisted under /app/data/logs (LUMA_LOG_DIR), rotated 10 MB x 5, in addition to stdout."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Iterable, Optional

LOG_DIR_ENV = 'LUMA_LOG_DIR'
DEFAULT_DIR = '/app/data/logs'
FILE_NAME = 'luma.log'
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5
FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


def log_dir() -> str:
    return os.environ.get(LOG_DIR_ENV) or DEFAULT_DIR


def attach(filters: Iterable[logging.Filter] = (), root: Optional[logging.Logger] = None) -> Optional[RotatingFileHandler]:
    """Add the rotating file handler to the root logger once; never raises (stdout keeps working)."""
    root = root or logging.getLogger()
    path = os.path.join(log_dir(), FILE_NAME)
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler) and os.path.abspath(getattr(h, 'baseFilename', '')) == os.path.abspath(path):
            return h
    try:
        os.makedirs(log_dir(), mode=0o750, exist_ok=True)
        h = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding='utf-8', delay=True)
    except OSError as e:
        logging.getLogger(__name__).warning('File logs off (%s): %s', log_dir(), type(e).__name__)
        return None
    h.setFormatter(logging.Formatter(FORMAT))
    h.setLevel(logging.INFO)
    for f in filters:
        h.addFilter(f)
    root.addHandler(h)
    return h
