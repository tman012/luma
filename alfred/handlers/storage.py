"""Storage status handler."""

import os
import shutil
from typing import Any, Dict, List

from config import STORAGE_PATHS

_PATH_LABELS: Dict[str, str] = {
    '/downloads': 'Downloads',
    '/mnt/media': 'Media (NAS)',
    '/mnt/downloads': 'Downloads',
    '/storage': 'Storage',
    '/': 'System',
}


def _label(path: str) -> str:
    return _PATH_LABELS.get(path, path)


def _format_bytes(b: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(b) < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024  # type: ignore[assignment]
    return f'{b:.1f} PB'


def handle_storage() -> Dict[str, Any]:
    """Return disk space for configured paths."""
    volumes: List[Dict[str, Any]] = []
    seen_devices: Dict[int, str] = {}

    for path in STORAGE_PATHS:
        path = path.strip()
        if not path or not os.path.exists(path):
            continue
        try:
            stat = os.stat(path)
            dev = stat.st_dev
            if dev in seen_devices:
                continue
            seen_devices[dev] = path
            usage = shutil.disk_usage(path)
            volumes.append({
                'path': path,
                'label': _label(path),
                'total_bytes': usage.total,
                'used_bytes': usage.used,
                'free_bytes': usage.free,
                'total': _format_bytes(usage.total),
                'used': _format_bytes(usage.used),
                'free': _format_bytes(usage.free),
                'percent_used': round(usage.used / usage.total * 100, 1),
            })
        except OSError:
            continue

    if not volumes:
        return {
            'type': 'storage_status',
            'status': 'error',
            'message': 'Could not read storage information.',
            'data': {},
        }

    downloads = next((v for v in volumes if v['path'] == '/downloads'), None) or volumes[0]
    message = f"You have {downloads['free']} free in Downloads ({downloads['percent_used']}% used of {downloads['total']} total)."

    return {
        'type': 'storage_status',
        'status': 'pending',
        'message': message + ' Want me to free up some space?',
        'data': {
            'downloads_free': downloads['free'],
            'downloads_total': downloads['total'],
            'downloads_percent_used': downloads['percent_used'],
            'pending_free_space': True,
        },
    }
