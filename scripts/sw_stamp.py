#!/usr/bin/env python3
"""Service worker version stamp: every frontend change bumps the SW version.

static/sw-stamp.json records the SW cache version (static/sw.js
CACHE_VERSION) and a SHA-256 of the frontend sources it was bumped for.
tests/test_sw_stamp.py fails when the frontend changed and the stamp didn't,
or when the version isn't above LAST_SHIPPED, so a new build always ships a
new SW (clients drop the old shell cache).

LAST_SHIPPED is the newest SW version any deployed build carries (v141:
:5002 runs ab1c841, cursor/owner-session-guard); TAKEN lists versions other
branches or builds hold above it (v142 cursor/sources-followup, v144
login-error-states, v145 this branch's 01abfd9, which Ops is shipping).
v143 was this branch's and never shipped; v146 went out in the f0db05d bundle
(the TV libraries selector), so it's taken too (Tester M38: never reuse it);
TV Import carries v147 and is with the gates (4d22f6c), so it's taken too;
TV Import fixes (Reviewer, Oct 18, 4b82ab5) carry v148, so it's taken; TV Calendar carries v149.
A version above LAST_SHIPPED that isn't TAKEN hasn't
shipped yet, so further frontend changes on the same branch may restamp it
(--restamp) instead of bumping again; once it ships, raise LAST_SHIPPED and
the next change has to --bump. Version tests assert "above LAST_SHIPPED",
never a fixed number.

    python3 scripts/sw_stamp.py            # check; exit 1 when a bump is due
    python3 scripts/sw_stamp.py --bump     # CACHE_VERSION + 1, and restamp
    python3 scripts/sw_stamp.py --set 140  # CACHE_VERSION = 140 (must be above LAST_SHIPPED)
    python3 scripts/sw_stamp.py --restamp  # same unshipped version, new frontend hash
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
LAST_SHIPPED = 141                   # :5002 runs ab1c841 (v141)
TAKEN = frozenset({142, 144, 145, 146, 147, 148})   # sources-followup v142, login-error-states v144, 01abfd9 v145 (Ops), f0db05d v146, 4d22f6c v147 (TV Import, gated), 4b82ab5 v148 (Import fixes)
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


def version_number() -> int:
    m = VERSION_RE.search(SW.read_text())
    if not m:
        raise SystemExit('CACHE_VERSION not found in static/sw.js')
    return int(m.group(2))


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
    if version_number() <= LAST_SHIPPED:
        out.append(f'sw.js is v{version_number()}, not above the last shipped v{LAST_SHIPPED}: run scripts/sw_stamp.py --bump')
    elif version_number() in TAKEN:
        out.append(f'v{version_number()} is taken by another branch: run scripts/sw_stamp.py --bump')
    if stamp.get('cache_version') != cache_version():
        out.append(f"sw-stamp.json is for {stamp.get('cache_version')}, sw.js is {cache_version()}")
    if stamp.get('frontend_sha256') != frontend_sha256():
        out.append('the frontend changed since the SW version was bumped: run scripts/sw_stamp.py --bump')
    return out


def set_version(n: int) -> str:
    if n <= LAST_SHIPPED or n in TAKEN:
        raise SystemExit(f'v{n} is shipped or taken (last shipped v{LAST_SHIPPED}, taken {sorted(TAKEN)})')
    text = SW.read_text()
    m = VERSION_RE.search(text)
    new = f'{m.group(1)}-v{n}'
    SW.write_text(text[:m.start()] + f"const CACHE_VERSION = '{new}';" + text[m.end():])
    return restamp()


def bump() -> str:
    n = max(version_number() + 1, LAST_SHIPPED + 1)
    while n in TAKEN:
        n += 1
    return set_version(n)


def restamp() -> str:
    """Record the current frontend for the current (unshipped) version."""
    if version_number() <= LAST_SHIPPED or version_number() in TAKEN:
        raise SystemExit(f'v{version_number()} is shipped or taken: --bump instead')
    STAMP.write_text(json.dumps({'cache_version': cache_version(), 'frontend_sha256': frontend_sha256()},
                                indent=2) + '\n')
    return cache_version()


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--bump' in args:
        print(bump())
    elif '--set' in args:
        print(set_version(int(args[args.index('--set') + 1])))
    elif '--restamp' in args:
        print(restamp())
    else:
        problems = check()
        print('\n'.join(problems) or 'ok')
        sys.exit(1 if problems else 0)
