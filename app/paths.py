"""Single source of truth for Luma's on-disk locations.

LUMA_DATA_DIR   persistent data (settings, databases, JSON state); default /app/data
LUMA_CACHE_DIR  rebuildable caches (explore, library, hero); default /app/cache
HERO_CACHE_DIR  hero images; default $LUMA_CACHE_DIR/hero
"""

import os
from pathlib import Path

DEFAULT_DATA_DIR = '/app/data'
DEFAULT_CACHE_DIR = '/app/cache'


def data_dir() -> Path:
    """Directory for persistent data (LUMA_DATA_DIR, default /app/data)."""
    return Path(os.environ.get('LUMA_DATA_DIR') or DEFAULT_DATA_DIR)


def data_path(*parts: str) -> Path:
    """Path of a file inside the data directory."""
    return data_dir().joinpath(*parts)


def cache_dir() -> Path:
    """Root of the cache tree (LUMA_CACHE_DIR, default /app/cache)."""
    return Path(os.environ.get('LUMA_CACHE_DIR') or DEFAULT_CACHE_DIR)


def hero_cache_dir() -> Path:
    """Hero image directory: HERO_CACHE_DIR, else $LUMA_CACHE_DIR/hero."""
    explicit = os.environ.get('HERO_CACHE_DIR')
    if explicit:
        return Path(explicit)
    return cache_dir() / 'hero'
