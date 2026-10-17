"""Paced file copy, shared by the auto-rename monitor and the library mover.

Moved out of app/auto_rename.py unchanged (auto_rename re-imports the same
names), so the library mover can use it without importing the auto-rename
monitor and its container-only data files.
"""

import os
import queue
import threading
from typing import List

# Library copies are paced rather than run flat out. The library share is CIFS
# and drains at ~40-50 MB/s, while reads from the local downloads pool arrive
# an order of magnitude faster. An unpaced copy therefore parks tens of GB of
# dirty pages against a slow backing device, blows past the kernel's global
# vm.dirty_ratio limit, and throttles *every* writer on the host — including
# the Radarr and Prowlarr SQLite databases — in balance_dirty_pages until the
# copy drains. That is what makes source searches hang for the duration of a
# library copy and recover the instant it finishes.
COPY_CHUNK_BYTES = 8 * 1024 * 1024    # read/write unit
COPY_FLUSH_BYTES = 64 * 1024 * 1024   # fsync + drop cache every this many bytes


def _drop_cache(fd: int):
    """Best-effort eviction of a file's pages from the page cache.

    Stops a multi-GB copy from evicting everything else on the box. Advisory
    only — POSIX_FADV_DONTNEED leaves dirty pages alone and is absent on some
    platforms, so failure here is never fatal to the copy.
    """
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError):
        pass


def _paced_copy(source: str, destination: str, on_progress=None, exclusive: bool = False) -> int:
    """Copy source → destination without letting dirty pages pile up.

    Each COPY_FLUSH_BYTES window is handed to a flusher thread so its fsync
    overlaps the writes for the next window. Flushing inline instead costs
    ~25% of throughput (the share sits idle while we refill); overlapping gets
    that back while still capping our dirty footprint at two windows.

    `on_progress` is called with the byte count after each completed window.
    With `exclusive`, the destination must not exist yet and must not be a
    symlink (O_CREAT | O_EXCL | O_NOFOLLOW); FileExistsError says it was there.
    Returns the number of bytes copied.
    """
    windows = queue.Queue(maxsize=1)
    flush_errors: List[OSError] = []

    def flusher(fd: int):
        # Always drains to the None sentinel — an early return would let the
        # writer block forever on a full queue.
        while True:
            if windows.get() is None:
                return
            try:
                os.fsync(fd)
                _drop_cache(fd)
            except OSError as e:
                flush_errors.append(e)

    copied = 0
    pending = 0
    if exclusive:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        dst_file = os.fdopen(os.open(destination, flags, 0o664), 'wb')
    else:
        dst_file = open(destination, 'wb')
    with open(source, 'rb') as src, dst_file as dst:
        fd = dst.fileno()
        thread = threading.Thread(target=flusher, args=(fd,), daemon=True)
        thread.start()
        try:
            while True:
                chunk = src.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
                pending += len(chunk)

                if pending >= COPY_FLUSH_BYTES:
                    dst.flush()
                    windows.put(1)
                    _drop_cache(src.fileno())
                    pending = 0
                    if on_progress:
                        on_progress(copied)
                if flush_errors:
                    raise flush_errors[0]
        finally:
            windows.put(None)
            thread.join()

        dst.flush()
        os.fsync(fd)
        _drop_cache(fd)
        _drop_cache(src.fileno())

    if flush_errors:
        raise flush_errors[0]
    return copied
