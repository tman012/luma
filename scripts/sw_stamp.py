#!/usr/bin/env python3
"""Service worker version stamp: every frontend change bumps the SW version.

static/sw-stamp.json records the SW cache version (static/sw.js
CACHE_VERSION) and a SHA-256 of the frontend sources it was bumped for.
tests/test_sw_stamp.py fails when the frontend changed and the stamp didn't,
so a new build always ships a new SW (clients drop the old shell cache).

    python3 scripts/sw_stamp.py          # check; exit 1 when a bump is due
    python3 scripts/sw_stamp.py --bump   # bump CACHE_VERSION by one and restamp
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SW = ROOT / 'static' / 'sw.js'
STAMP = ROOT / 'static' / 'sw-stamp.json'
# What ends up in the built app (tests and node_modules don't).
SOURCES = ('frontend/src', 'frontend/public', 'frontend/index.html', 'frontend/package.json',
           'frontend/package-lock.json', 'frontend/vite.config.ts', 'frontend/tsconfig.json',
           'frontend/tsconfig.app.json')
VERSION_RE = re.compile(r"const CACHE_VERSION = '([a-z-]+)-v(\d+)';")


def frontend_sha256() -> str:
    h = hashlib.sha256()
    files = []
    for s in SOURCES:
        p = ROOT / s
        if p.is_dir():
            files += [f for f in p.rglob('*') if f.is_file()]
        elif p.is_file():
            files.append(p)
    for f in sorted(files, key=lambda x: x.relative_to(ROOT).as_posix()):
        h.update(f.relative_to(ROOT).as_posix().encode() + b'\0')
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def cache_version() -> str:
    m = VERSION_RE.search(SW.read_text())
    if not m:
        raise SystemExit('CACHE_VERSION not found in static/sw.js')
    return f'{m.group(1)}-v{m.group(2)}'


def check() -> list:
    """Problems, empty when the stamp is current."""
    try:
        stamp = json.loads(STAMP.read_text())
    except (OSError, ValueError):
        return ['static/sw-stamp.json is missing or unreadable']
    out = []
    if stamp.get('cache_version') != cache_version():
        out.append(f"sw-stamp.json is for {stamp.get('cache_version')}, sw.js is {cache_version()}")
    if stamp.get('frontend_sha256') != frontend_sha256():
        out.append('the frontend changed since the SW version was bumped: run scripts/sw_stamp.py --bump')
    return out


def bump() -> str:
    text = SW.read_text()
    m = VERSION_RE.search(text)
    new = f'{m.group(1)}-v{int(m.group(2)) + 1}'
    SW.write_text(text[:m.start()] + f"const CACHE_VERSION = '{new}';" + text[m.end():])
    STAMP.write_text(json.dumps({'cache_version': new, 'frontend_sha256': frontend_sha256()}, indent=2) + '\n')
    return new


if __name__ == '__main__':
    if '--bump' in sys.argv[1:]:
        print(bump())
    else:
        problems = check()
        print('\n'.join(problems) or 'ok')
        sys.exit(1 if problems else 0)
