"""The git commit this build came from, resolved once per process.

Order: LUMA_GIT_SHA env, the GIT_SHA file baked in by the Docker build
(--build-arg GIT_SHA=...), the repository's .git directory, else 'unknown'.
Nothing is run as a subprocess.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Optional

UNKNOWN = 'unknown'
ROOT = Path(__file__).resolve().parent.parent
_SHA_RE = re.compile(r'^[0-9a-f]{7,64}$')
_cached: Optional[str] = None
_lock = threading.Lock()


def _valid(value: str) -> Optional[str]:
    value = (value or '').strip().lower()
    return value if _SHA_RE.match(value) else None


def _git_dir(root: Path) -> Optional[Path]:
    dot = root / '.git'
    if dot.is_dir():
        return dot
    if dot.is_file():  # worktree: "gitdir: <path>"
        text = dot.read_text(errors='replace').strip()
        if text.startswith('gitdir:'):
            p = Path(text[len('gitdir:'):].strip())
            return p if p.is_absolute() else (root / p).resolve()
    return None


def _from_git(root: Path) -> Optional[str]:
    gd = _git_dir(root)
    if not gd:
        return None
    head = (gd / 'HEAD').read_text(errors='replace').strip()
    if not head.startswith('ref:'):
        return _valid(head)
    ref = head[len('ref:'):].strip()
    # A worktree keeps its refs in the main repository (commondir).
    common = gd
    if (gd / 'commondir').is_file():
        c = Path((gd / 'commondir').read_text().strip())
        common = c if c.is_absolute() else (gd / c).resolve()
    for base in (gd, common):
        f = base / ref
        if f.is_file():
            return _valid(f.read_text(errors='replace'))
    packed = common / 'packed-refs'
    if packed.is_file():
        for line in packed.read_text(errors='replace').splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return _valid(parts[0])
    return None


def resolve(root: Path = ROOT) -> str:
    sha = _valid(os.environ.get('LUMA_GIT_SHA', ''))
    if sha:
        return sha
    try:
        sha = _valid((root / 'GIT_SHA').read_text())
        if sha:
            return sha
    except OSError:
        pass
    try:
        sha = _from_git(root)
    except OSError:
        sha = None
    return sha or UNKNOWN


def git_sha() -> str:
    global _cached
    if _cached is None:
        with _lock:
            if _cached is None:
                _cached = resolve()
    return _cached
