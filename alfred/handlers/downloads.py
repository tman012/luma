"""Download status handler — queries qBittorrent WebUI API."""

import logging
from typing import Any, Dict, List, Optional

import requests

from config import QBIT_URL, QBIT_USER, QBIT_PASS

logger = logging.getLogger(__name__)

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """Get or create an authenticated qBittorrent session."""
    global _session
    if _session is None:
        _session = requests.Session()
    # Always try to auth (cookies expire)
    try:
        resp = _session.post(
            f'{QBIT_URL}/api/v2/auth/login',
            data={'username': QBIT_USER, 'password': QBIT_PASS},
            timeout=5,
        )
        if resp.text.strip() != 'Ok.':
            logger.warning('qBittorrent auth failed: %s', resp.text[:100])
    except requests.RequestException as e:
        logger.warning('qBittorrent auth error: %s', e)
    return _session


def _format_speed(bps: int) -> str:
    mbps = bps / (1024 * 1024)
    if mbps >= 1:
        return f'{mbps:.1f} MB/s'
    kbps = bps / 1024
    return f'{kbps:.0f} KB/s'


def _format_eta(seconds: int) -> str:
    if seconds < 0 or seconds > 86400 * 30:
        return 'unknown'
    if seconds < 60:
        return f'{seconds}s'
    if seconds < 3600:
        return f'{seconds // 60}m'
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    return f'{hours}h {mins}m'


def handle_downloads(filter_type: str = 'downloading') -> Dict[str, Any]:
    """Return current download status from qBittorrent.

    Args:
        filter_type: qBittorrent filter — downloading, completed, all, etc.
    """
    if not QBIT_URL:
        return {
            'type': 'download_status',
            'status': 'error',
            'message': 'qBittorrent is not configured.',
            'data': {},
        }

    try:
        session = _get_session()
        resp = session.get(
            f'{QBIT_URL}/api/v2/torrents/info',
            params={'filter': filter_type},
            timeout=10,
        )
        resp.raise_for_status()
        torrents = resp.json()
    except Exception as e:
        return {
            'type': 'download_status',
            'status': 'error',
            'message': f'Could not reach qBittorrent: {e}',
            'data': {},
        }

    items: List[Dict[str, Any]] = []
    for t in torrents:
        items.append({
            'name': t.get('name', ''),
            'state': t.get('state', ''),
            'progress': round(t.get('progress', 0) * 100, 1),
            'download_speed': _format_speed(t.get('dlspeed', 0)),
            'upload_speed': _format_speed(t.get('upspeed', 0)),
            'eta': _format_eta(t.get('eta', -1)),
            'size': round(t.get('size', 0) / (1024 ** 3), 2),
            'category': t.get('category', ''),
            'hash': t.get('hash', ''),
        })

    if not items:
        msg = 'Nothing is downloading right now.' if filter_type == 'downloading' else f'No {filter_type} torrents.'
    elif len(items) == 1:
        i = items[0]
        msg = f'{i["name"][:50]} — {i["progress"]:.0f}% at {i["download_speed"]}'
    else:
        msg = f'{len(items)} active downloads.'

    return {
        'type': 'download_status',
        'status': 'ok',
        'message': msg,
        'data': {
            'count': len(items),
            'downloads': items,
        },
        'suggested_ui_action': {
            'action': 'open_downloads',
        } if items else None,
    }


def handle_queue() -> Dict[str, Any]:
    """Return queue status — downloading + queued + stalled items."""
    if not QBIT_URL:
        return {
            'type': 'queue_status',
            'status': 'error',
            'message': 'qBittorrent is not configured.',
            'data': {},
        }

    try:
        session = _get_session()

        # Get transfer info for global speeds
        transfer_resp = session.get(f'{QBIT_URL}/api/v2/transfer/info', timeout=5)
        transfer_resp.raise_for_status()
        transfer = transfer_resp.json()

        # Get all non-completed torrents
        resp = session.get(
            f'{QBIT_URL}/api/v2/torrents/info',
            params={'filter': 'all'},
            timeout=10,
        )
        resp.raise_for_status()
        all_torrents = resp.json()
    except Exception as e:
        return {
            'type': 'queue_status',
            'status': 'error',
            'message': f'Could not reach qBittorrent: {e}',
            'data': {},
        }

    downloading = [t for t in all_torrents if t.get('state', '').startswith(('downloading', 'stalledDL', 'forcedDL'))]
    queued = [t for t in all_torrents if t.get('state', '').startswith('queued')]
    stalled = [t for t in all_torrents if 'stalled' in t.get('state', '')]
    errored = [t for t in all_torrents if t.get('state', '') in ('error', 'missingFiles')]

    parts = []
    if downloading:
        parts.append(f'{len(downloading)} downloading')
    if queued:
        parts.append(f'{len(queued)} queued')
    if stalled:
        parts.append(f'{len(stalled)} stalled')
    if errored:
        parts.append(f'{len(errored)} with errors')

    status = 'ok'
    if errored:
        status = 'error'
    elif stalled and not downloading:
        status = 'pending'

    msg = ', '.join(parts) if parts else 'Queue is empty.'
    if downloading:
        msg += f' — {_format_speed(transfer.get("dl_info_speed", 0))} total'

    return {
        'type': 'queue_status',
        'status': status,
        'message': msg,
        'data': {
            'downloading': len(downloading),
            'queued': len(queued),
            'stalled': len(stalled),
            'errored': len(errored),
            'global_download_speed': _format_speed(transfer.get('dl_info_speed', 0)),
            'global_upload_speed': _format_speed(transfer.get('up_info_speed', 0)),
        },
    }
