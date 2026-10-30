"""Read Luma's settings without the app: no app.main import, no schedulers or threads, no bootstrap,
migration or write (Ops tooling, one-off scripts, tests). A thin front on settings_manager.SettingsFile,
which reads settings.json exactly as it is on disk (missing or unreadable: empty).

    from app import settings_read
    settings_read.get('qbittorrent.url')
"""
from __future__ import annotations

from typing import Any, Optional

from app.settings_manager import SettingsFile, settings_file_path

__all__ = ['load', 'get', 'path']


def path():
    return settings_file_path()


def load(settings_path: Optional[str] = None) -> SettingsFile:
    return SettingsFile(settings_path)


def get(dotted_path: str, default: Any = None, settings_path: Optional[str] = None) -> Any:
    return SettingsFile(settings_path).get(dotted_path, default)
