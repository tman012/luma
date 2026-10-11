"""
tracker_files — which tracker did a file in the downloads folder come from?

The downloads folder is flat: several hundred top-level entries, all saved to
the same path, with nothing in a filename saying where the release came from.
The only place that knowledge lives is qBittorrent, so this module asks it and
returns the mapping keyed the way the filesystem is keyed — by top-level entry
name.

Attribution is by ANNOUNCE HOST, not by qBittorrent category. The categories
here say who *asked* for the download (radarr / tv-sonarr / the Luma app category),
which is orthogonal: a Sonarr grab and a Radarr grab can both come from
IPTorrents. The announce URL is the only field that identifies the tracker.

Two announce fields get consulted, in order:
  * ``tracker`` — the one qBittorrent is currently talking to. Blank when every
    tracker in the torrent is unreachable, which is exactly the moment a
    tracker-gated cleanup would otherwise mis-sort a file.
  * the ``tr=`` list inside ``magnet_uri`` — every announce URL the torrent
    carries, working or not. It is already in the ``torrents/info`` payload, so
    this fallback costs no extra requests.

Hosts match against a list of base domains. IPTorrents announces from several
unrelated-looking domains (stackoverflow.tech, empirehost.me, bgp.technology)
with a per-torrent subdomain in front, so a host matches when it *equals* a
base domain or is a subdomain of one — never on a bare string suffix, which
would let ``bgp.technology`` swallow every ``.technology`` host.

An unrecognised host classifies as ``None`` rather than being guessed at.
Callers that delete files must read ``None`` as "not the tracker I asked for":
a wrong guess here destroys the wrong data permanently. ``summarise()`` reports
the hosts that went unclassified, so a tracker moving to a new announce domain
surfaces as a visible gap instead of quietly reclassifying its whole back
catalogue.
"""

import logging
import os
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


# (key, label, base announce domains). Verified against the live qBittorrent
# instance. Add a domain to an existing row rather than opening a second row
# for the same tracker — callers key off `key`, and one tracker under two keys
# would let it be both preferred and not preferred at once.
TRACKERS = (
    ('ipt', 'IPTorrents', (
        'iptorrents.com', 'iptorrents.me',
        'stackoverflow.tech', 'empirehost.me', 'bgp.technology',
    )),
    ('beyondhd', 'BeyondHD', ('beyond-hd.me',)),
    ('morethantv', 'MoreThanTV', ('morethantv.me',)),
    ('privatehd', 'PrivateHD', ('privatehd.to',)),
    ('pixelhd', 'PixelHD', ('pixelhd.me',)),
)

LABELS: Dict[str, str] = {key: label for key, label, _ in TRACKERS}

# qBittorrent states that mean the torrent is still being fetched or verified.
# Kept here rather than in the caller because "is this torrent finished" is a
# question about qBittorrent's vocabulary, which is this module's business.
BUSY_STATES = frozenset({
    'downloading', 'stalledDL', 'metaDL', 'queuedDL', 'pausedDL',
    'checkingDL', 'checkingUP', 'checkingResumeData', 'allocating',
    'moving', 'forcedDL',
})


def label_for(key: Optional[str]) -> str:
    """Display name for a tracker key. Unknown keys read as "unknown"."""
    if not key:
        return 'unknown'
    return LABELS.get(key, key)


def known_trackers() -> List[Dict[str, str]]:
    """The trackers this module can attribute, for the settings picker."""
    return [{'key': key, 'label': label} for key, label, _ in TRACKERS]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_host(host: str) -> Optional[str]:
    """Tracker key for an announce host, or None when it isn't recognised."""
    h = (host or '').strip().lower().rstrip('.')
    if not h:
        return None
    for key, _label, domains in TRACKERS:
        for d in domains:
            if h == d or h.endswith('.' + d):
                return key
    return None


def announce_hosts(torrent: Dict[str, Any]) -> List[str]:
    """Every announce host for a torrent, current one first, de-duplicated."""
    hosts: List[str] = []

    try:
        current = urlparse(torrent.get('tracker') or '').hostname
    except Exception:
        current = None
    if current:
        hosts.append(current)

    magnet = torrent.get('magnet_uri') or ''
    if magnet:
        try:
            for url in parse_qs(urlparse(magnet).query).get('tr', []):
                h = urlparse(url).hostname
                if h and h not in hosts:
                    hosts.append(h)
        except Exception:
            pass

    return hosts


def classify(torrent: Dict[str, Any]) -> Optional[str]:
    """Tracker key for a torrent, or None when no announce host is recognised."""
    for host in announce_hosts(torrent):
        key = classify_host(host)
        if key:
            return key
    return None


# ---------------------------------------------------------------------------
# qBittorrent access
# ---------------------------------------------------------------------------

# Generous on purpose. A qBittorrent hashing or flushing a 90GB torrent takes
# 20-50s to answer torrents/info for ~650 torrents, and that is *precisely* the
# state the downloads folder is in when a free-space guard needs an answer: the
# condition that triggers the cleanup is the condition that slows the client
# down. A tight timeout here reads a busy client as a dead one and — because
# attribution is refused rather than guessed — cancels the run that was most
# needed. Slow is not unreachable.
_INFO_TIMEOUT = 90
_INFO_RETRIES = 2


def fetch_torrents() -> Optional[List[Dict[str, Any]]]:
    """Every torrent qBittorrent knows about, or None if it can't be reached.

    ``None`` and ``[]`` are deliberately different answers. To a caller that
    deletes files, an empty client means "nothing here is attributable to any
    tracker" while an unreachable one means "attribution is unavailable right
    now" — and only the second should hold a tracker-gated run back. Collapsing
    the two would turn a dead qBittorrent into a silent, unrestricted delete.

    Retried, because a single slow answer under download load is not evidence
    the client is gone, and the cost of wrongly concluding it is gone is a
    cleanup that silently doesn't happen.
    """
    import time as _time

    for attempt in range(1, _INFO_RETRIES + 2):
        try:
            from app.auto_rename import auto_renamer
            session = auto_renamer.get_authenticated_session()
            if not session:
                logger.warning('tracker_files: qBittorrent authentication failed '
                               '(attempt %d)', attempt)
                continue
            url = auto_renamer._qbit_settings()[0]
            started = _time.monotonic()
            resp = session.get(f'{url}/api/v2/torrents/info', timeout=_INFO_TIMEOUT)
            elapsed = _time.monotonic() - started
            if resp.status_code != 200:
                logger.warning('tracker_files: torrents/info returned %s (attempt %d)',
                               resp.status_code, attempt)
                continue
            data = resp.json()
            if not isinstance(data, list):
                logger.warning('tracker_files: torrents/info returned %s, not a list',
                               type(data).__name__)
                return None
            # Surfaced so a client heading toward unusable is visible before it
            # starts cancelling runs outright.
            if elapsed > 15:
                logger.warning('tracker_files: qBittorrent took %.0fs to list %d '
                               'torrents — slow but usable', elapsed, len(data))
            return data
        except Exception as e:
            logger.warning('tracker_files: could not read qBittorrent torrents '
                           '(attempt %d): %s', attempt, e)

    logger.error('tracker_files: qBittorrent unreadable after %d attempts',
                 _INFO_RETRIES + 1)
    return None


def _entry_key(torrent: Dict[str, Any]) -> str:
    """Top-level downloads entry a torrent's data lives under.

    Basename rather than full path: the folder is keyed on names, and matching
    on the basename keeps working if either side's path mapping is changed.
    """
    raw = torrent.get('content_path') or torrent.get('root_path') or torrent.get('name') or ''
    return os.path.basename(str(raw).rstrip('/'))


def by_entry(torrents: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Dict[str, Any]]]:
    """Map top-level entry name -> attribution, or None if qBittorrent is down.

    Each value carries the *set* of trackers backing that entry, not one key,
    because a cross-seeded file is seeded on more than one tracker at once.
    A caller freeing space "from tracker X" must not delete a file that is also
    another tracker's seed, so it needs to see both — one key would hide it.
    """
    if torrents is None:
        torrents = fetch_torrents()
    if torrents is None:
        return None

    out: Dict[str, Dict[str, Any]] = {}
    for t in torrents:
        key = _entry_key(t)
        if not key:
            continue

        tracker = classify(t)
        hosts = announce_hosts(t)
        unfinished = (t.get('progress') or 0) < 1.0 or t.get('state') in BUSY_STATES

        rec = out.get(key)
        if rec is None:
            rec = out[key] = {
                'trackers': set(),
                'hosts': [],
                'hashes': [],
                'unclassified': False,
                'unfinished': False,
                'added_on': None,
                'completion_on': None,
                'size_bytes': 0,
            }

        if tracker:
            rec['trackers'].add(tracker)
        else:
            # Recorded, not silently dropped: an entry with an unrecognised
            # announce host must never pass as "belongs to my chosen tracker".
            rec['unclassified'] = True
        for h in hosts:
            if h not in rec['hosts']:
                rec['hosts'].append(h)
        if t.get('hash'):
            rec['hashes'].append(t['hash'])
        rec['unfinished'] = rec['unfinished'] or unfinished
        rec['size_bytes'] += int(t.get('size') or 0)

        # Oldest wins for both dates — with a cross-seed, the earliest grab is
        # when this data actually arrived on disk.
        for field in ('added_on', 'completion_on'):
            val = t.get(field)
            if not val or val <= 0:
                continue
            if rec[field] is None or val < rec[field]:
                rec[field] = val

    return out


def entry_trackers(record: Optional[Dict[str, Any]]) -> Set[str]:
    """Tracker keys on an attribution record, tolerant of a missing record."""
    if not record:
        return set()
    trackers = record.get('trackers')
    return set(trackers) if trackers else set()


def belongs_only_to(record: Optional[Dict[str, Any]], wanted: Set[str]) -> bool:
    """True when every tracker behind an entry is in *wanted*.

    "Only" rather than "any" on purpose. An entry cross-seeded on IPTorrents and
    BeyondHD would satisfy "any IPTorrents", and deleting it to free IPTorrents
    space would take out the BeyondHD seed as collateral. Requiring all of them
    keeps a tracker-scoped cleanup scoped. An entry with an unrecognised
    announce host never qualifies.
    """
    if not record or record.get('unclassified'):
        return False
    trackers = entry_trackers(record)
    return bool(trackers) and trackers.issubset(wanted)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summarise() -> Dict[str, Any]:
    """Per-tracker torrent counts and sizes, plus what went unclassified.

    Backs the settings picker, so the trackers offered are the ones actually
    present, with the size behind each — the number that says whether a
    tracker-scoped cleanup has any runway. ``unknown_hosts`` is the important
    half: a tracker that has moved to a new announce domain shows up there
    instead of quietly dropping out of its own row.
    """
    torrents = fetch_torrents()
    if torrents is None:
        return {'available': False, 'trackers': [], 'unknown_hosts': [],
                'unclassified_count': 0, 'unclassified_bytes': 0, 'total_count': 0}

    counts: Dict[str, int] = {}
    sizes: Dict[str, int] = {}
    unknown: Dict[str, int] = {}
    unclassified_count = 0
    unclassified_bytes = 0

    for t in torrents:
        size = int(t.get('size') or 0)
        key = classify(t)
        if key:
            counts[key] = counts.get(key, 0) + 1
            sizes[key] = sizes.get(key, 0) + size
        else:
            unclassified_count += 1
            unclassified_bytes += size
            for h in announce_hosts(t) or ['(no announce url)']:
                unknown[h] = unknown.get(h, 0) + 1

    trackers = [
        {
            'key': key,
            'label': label,
            'count': counts.get(key, 0),
            'size_bytes': sizes.get(key, 0),
        }
        for key, label, _ in TRACKERS
    ]

    return {
        'available': True,
        'trackers': trackers,
        'unknown_hosts': [
            {'host': h, 'count': c}
            for h, c in sorted(unknown.items(), key=lambda kv: -kv[1])
        ],
        'unclassified_count': unclassified_count,
        'unclassified_bytes': unclassified_bytes,
        'total_count': len(torrents),
    }
