"""Host names that LAN clients use to reach Luma.

Luma keeps no access log, so this small record is the only history of which
names (the Host header) home devices type in. It is written only for requests
from a local client address that are not request-portal traffic, holds names
only (no addresses, paths or users), and is used to suggest and seed
security.hostnames.

File: <data dir>/lan_hosts.json  ->  {"names": {"luma.lan": "2026-10-16T08:00:00+00:00"}}
"""

import ipaddress
import json
import logging
import os
import re
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

FILENAME = 'lan_hosts.json'
MAX_NAMES = 32
KEEP_DAYS = 90
_REWRITE_AFTER = timedelta(hours=6)

# Suffixes that only make sense inside a home network. A name with a dot must
# end in one of these to be seeded automatically; public domains are left for
# a person to add.
PRIVATE_SUFFIXES = ('.local', '.lan', '.home', '.home.arpa', '.internal',
                    '.localdomain', '.intranet', '.private', '.corp')

_LABEL = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')
_CONTAINER_ID = re.compile(r'^[0-9a-f]{12}$')

_lock = threading.Lock()
_last_write: Dict[str, datetime] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize(name: str) -> str:
    """Lower-cased name without port, brackets or trailing dot; '' when not a host name."""
    name = (name or '').strip().lower()
    if name.startswith('['):
        return ''
    if name.count(':') == 1:
        name = name.split(':', 1)[0]
    name = name.rstrip('.')
    if not name or len(name) > 253 or not all(_LABEL.match(p) for p in name.split('.')):
        return ''
    try:
        ipaddress.ip_address(name)
        return ''
    except ValueError:
        return name


def is_seedable(name: str) -> bool:
    """A private name: one label (e.g. 'docker-server') or a home-network suffix."""
    name = normalize(name)
    if not name or name == 'localhost' or name.endswith('.localhost'):
        return False
    if '.' not in name:
        return True
    return name.endswith(PRIVATE_SUFFIXES)


def _path(data_dir) -> Path:
    return Path(data_dir) / FILENAME


def load(data_dir) -> Dict[str, str]:
    try:
        names = json.loads(_path(data_dir).read_text()).get('names') or {}
        return {k: v for k, v in names.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def recent(data_dir, days: int = 30) -> List[str]:
    """Names seen from LAN clients within the last *days*, newest first."""
    cutoff = _now() - timedelta(days=days)
    out = []
    for name, seen in load(data_dir).items():
        try:
            when = datetime.fromisoformat(seen)
        except ValueError:
            continue
        if when >= cutoff:
            out.append((when, name))
    return [n for _, n in sorted(out, reverse=True)]


def record(host: str, data_dir) -> None:
    """Note that a LAN client reached Luma by *host*. Cheap; writes at most every few hours per name."""
    name = normalize(host)
    if not name or name == 'localhost':
        return
    now = _now()
    with _lock:
        last = _last_write.get(name)
        if last and now - last < _REWRITE_AFTER:
            return
        names = load(data_dir)
        cutoff = now - timedelta(days=KEEP_DAYS)
        kept = {}
        for k, v in names.items():
            try:
                if datetime.fromisoformat(v) >= cutoff:
                    kept[k] = v
            except ValueError:
                continue
        if name not in kept:
            logger.info('[hosts] LAN clients reach Luma as %s', name)
        kept[name] = now.isoformat(timespec='seconds')
        if len(kept) > MAX_NAMES:
            kept = dict(sorted(kept.items(), key=lambda kv: kv[1], reverse=True)[:MAX_NAMES])
        try:
            p = _path(data_dir)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix('.tmp')
            tmp.write_text(json.dumps({'names': kept}, indent=1, sort_keys=True))
            os.replace(tmp, p)
            _last_write[name] = now
        except OSError as exc:
            logger.debug('[hosts] could not save %s: %s', p, exc)


def own_hostnames(hostname: Optional[str] = None) -> List[str]:
    """This machine's name and its .local / .lan forms (Docker container ids skipped)."""
    raw = normalize(hostname if hostname is not None else socket.gethostname())
    if not raw or raw == 'localhost':
        return []
    short = raw.split('.', 1)[0]
    if _CONTAINER_ID.match(short):
        return []
    names = [short, f'{short}.local', f'{short}.lan']
    if raw != short and is_seedable(raw):
        names.insert(1, raw)
    return names


def seed_candidates(data_dir, configured_urls: Iterable[str] = (), exclude: Iterable[str] = (),
                    hostname: Optional[str] = None) -> Dict[str, List[str]]:
    """Names to seed security.hostnames with, grouped by source.

    LAN history from the last 30 days wins when it has any seedable name;
    otherwise configured URLs plus this machine's own names are used.
    """
    from urllib.parse import urlsplit

    skip = {normalize(n) for n in exclude if n}
    history = [n for n in recent(data_dir, 30) if is_seedable(n) and n not in skip]
    if history:
        return {'lan_history': history}
    configured = []
    for url in configured_urls:
        try:
            name = normalize(urlsplit((url or '').strip()).hostname or '')
        except ValueError:
            continue
        if name and is_seedable(name) and name not in skip and name not in configured:
            configured.append(name)
    own = [n for n in own_hostnames(hostname) if n not in skip and n not in configured]
    return {'configured': configured, 'hostname': own}
