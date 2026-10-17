"""Which settings changes widen automatic file deletion (PM ruling).

Downloads Guard deletes files on its own. A settings change that turns it on,
or makes it delete more, sooner, more often, or somewhere else, needs the
owner signed in with a session (start_flow.require_signed_in_owner), the same
as a manual delete. Turning it off or tightening it stays open to any admin.

`_widens_auto_delete(old, new)` compares two full settings dicts. It reads
them with downloads_guard's own parsers, so a value counts the way the
automation will read it, clamping included. It returns the widened setting
paths in a fixed order (empty when nothing widens, so it also reads as a
bool).
"""
from __future__ import annotations

from typing import Any, Dict, List

PREFIX = 'automation.downloads_guard.'
ENABLED = PREFIX + 'enabled'
MIN_FREE = PREFIX + 'min_free_gb'
RECLAIM = PREFIX + 'reclaim_gb'
DAILY_CAP = PREFIX + 'daily_cap_gb'
INTERVAL = PREFIX + 'check_interval_minutes'
ROOT = 'media.downloads_root'

# Settings that choose a folder the guard or the manual cleanup deletes from
# (storage_cleanup.downloads_root()). Any change to one's stored value widens,
# clearing included, guard on or off.
DELETE_ROOTS = (ROOT,)

# Every path _widens_auto_delete can name, in the order it names them.
WIDENING_PATHS = (ENABLED, MIN_FREE, RECLAIM, DAILY_CAP, INTERVAL) + DELETE_ROOTS


class _View:
    """Read-only dotted .get() over a plain settings dict (the shape
    downloads_guard's parsers expect)."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data if isinstance(data, dict) else {}

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split('.'):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _folder(view: _View, path: str = ROOT) -> Any:
    """The stored value of a folder setting, as saved: no resolving, no
    fallback. Any change to it counts, clearing included."""
    return view.get(path)


def _widens_auto_delete(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """Paths whose change from *old* to *new* lets automatic deletion delete
    more. A change to a folder deletions come from (DELETE_ROOTS) always
    widens, guard on or off. Otherwise, when the guard is off in *new*,
    nothing widens, because turning it off (with or without other number
    edits) always narrows. Turning it back on counts again later.

    - enabled: off to on.
    - min_free_gb: higher (the guard fires at more free space).
    - reclaim_gb: higher (each run deletes more).
    - daily_cap_gb: higher, or a cap changed to 0 (no cap). A change from 0
      to a cap tightens.
    - check_interval_minutes: shorter (runs more often).
    - media.downloads_root: any change to the stored value, clearing
      included, guard on or off (the manual cleanup deletes from it any
      time, and the guard does the moment it is turned on). Compared as
      saved, with no resolving and no fallback; a save that leaves it
      unchanged is fine.
    The tracker preference only orders what is spent first, never how much,
    so it is not a widening.
    """
    from app import downloads_guard as dg
    o, n = _View(old), _View(new)
    # The folder the guard and the manual cleanup delete from counts whether
    # or not the guard is on (PM ruling): the cleanup uses it any time, and
    # the guard uses it the moment it is turned on.
    moved = [p for p in DELETE_ROOTS if _folder(n, p) != _folder(o, p)]
    if not dg.enabled(n):
        return moved
    widened: List[str] = []
    if not dg.enabled(o):
        widened.append(ENABLED)
    if dg.min_free_bytes(n) > dg.min_free_bytes(o):
        widened.append(MIN_FREE)
    if dg.reclaim_bytes(n) > dg.reclaim_bytes(o):
        widened.append(RECLAIM)
    old_cap, new_cap = dg.daily_cap_bytes(o), dg.daily_cap_bytes(n)
    if old_cap and (new_cap == 0 or new_cap > old_cap):
        widened.append(DAILY_CAP)
    if dg.check_interval_seconds(n) < dg.check_interval_seconds(o):
        widened.append(INTERVAL)
    return widened + moved
