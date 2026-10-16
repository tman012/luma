"""Free space handler — deletes oldest IPTorrents files from qBittorrent."""

import logging
from typing import Any, Dict

import requests

from config import QBIT_URL, QBIT_USER, QBIT_PASS

logger = logging.getLogger(__name__)

_IPT_TRACKER_PATTERNS = ('bgp.technology', 'iptorrents.com', 'ipt.rocks')
_DELETE_COUNT = 20


def _format_bytes(b: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(b) < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024  # type: ignore[assignment]
    return f'{b:.1f} PB'


def _is_ipt(tracker: str) -> bool:
    t = tracker.lower()
    return any(p in t for p in _IPT_TRACKER_PATTERNS)


def _get_session() -> requests.Session:
    session = requests.Session()
    try:
        resp = session.post(
            f'{QBIT_URL}/api/v2/auth/login',
            data={'username': QBIT_USER, 'password': QBIT_PASS},
            timeout=5,
        )
        if resp.text.strip() != 'Ok.':
            logger.warning('qBittorrent auth failed: %s', resp.text[:100])
    except requests.RequestException as e:
        logger.warning('qBittorrent auth error: %s', e)
    return session


def handle_free_space() -> Dict[str, Any]:
    """Delete the oldest IPTorrents files to free up space in downloads."""
    if not QBIT_URL:
        return {
            'type': 'free_space_result',
            'status': 'error',
            'message': 'qBittorrent is not configured.',
            'data': {},
        }

    try:
        session = _get_session()
        resp = session.get(
            f'{QBIT_URL}/api/v2/torrents/info',
            params={'filter': 'all'},
            timeout=15,
        )
        resp.raise_for_status()
        all_torrents = resp.json()
    except Exception as e:
        return {
            'type': 'free_space_result',
            'status': 'error',
            'message': f'Could not reach qBittorrent: {e}',
            'data': {},
        }

    ipt = [t for t in all_torrents if _is_ipt(t.get('tracker', ''))]

    if not ipt:
        return {
            'type': 'free_space_result',
            'status': 'ok',
            'message': 'No IPTorrents files found in downloads.',
            'data': {'deleted': 0, 'freed_bytes': 0},
        }

    # Oldest first
    ipt.sort(key=lambda t: t.get('added_on', 0))
    to_delete = ipt[:_DELETE_COUNT]

    hashes = '|'.join(t['hash'] for t in to_delete)
    freed_bytes = sum(t.get('size', 0) for t in to_delete)
    names = [t['name'] for t in to_delete]

    try:
        del_resp = session.post(
            f'{QBIT_URL}/api/v2/torrents/delete',
            data={'hashes': hashes, 'deleteFiles': 'true'},
            timeout=30,
        )
        del_resp.raise_for_status()
    except Exception as e:
        return {
            'type': 'free_space_result',
            'status': 'error',
            'message': f'Delete request failed: {e}',
            'data': {},
        }

    count = len(to_delete)
    freed_str = _format_bytes(freed_bytes)
    remaining = len(ipt) - count

    if count == len(ipt):
        summary = f'Removed all {count} IPTorrents files and freed {freed_str}.'
    else:
        summary = f'Removed {count} oldest IPTorrents files and freed {freed_str}. {remaining} IPT files remain.'

    return {
        'type': 'free_space_result',
        'status': 'ok',
        'message': summary,
        'data': {
            'deleted': count,
            'freed_bytes': freed_bytes,
            'freed': freed_str,
            'remaining_ipt': remaining,
            'files_removed': names,
        },
    }
