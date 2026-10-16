#!/usr/bin/env python3
"""One-shot helper: list (and optionally unmonitor/delete) monitored Radarr
movies with no file — the "ghost" leftovers from Genre Roulette failed grabs
and similar add-then-reject paths.

Default is dry-run (list only). Destructive modes require an explicit flag.

Usage (on docker-server / inside luma):
  PYTHONPATH=/app python3 scripts/cleanup_radarr_ghosts.py
  PYTHONPATH=/app python3 scripts/cleanup_radarr_ghosts.py --only-parked
  PYTHONPATH=/app python3 scripts/cleanup_radarr_ghosts.py --only-parked --unmonitor
  PYTHONPATH=/app python3 scripts/cleanup_radarr_ghosts.py --only-parked --delete

--only-parked limits to TMDB IDs currently in genre_roulette parked map
(plus other classic ghosts that match parked history). Anticipated/trending
future titles are left alone unless you omit --only-parked (still dry-run
unless --unmonitor/--delete).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _data_dir() -> Path:
    docker = Path('/app/data')
    if docker.is_dir():
        return docker
    return Path(__file__).resolve().parent.parent / 'data'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--only-parked', action='store_true',
                    help='Only Genre Roulette parked TMDB IDs (safer)')
    ap.add_argument('--unmonitor', action='store_true',
                    help='Set monitored=false (keeps Radarr entry)')
    ap.add_argument('--delete', action='store_true',
                    help='Delete from Radarr (deleteFiles=false)')
    args = ap.parse_args()
    if args.unmonitor and args.delete:
        print('Pick only one of --unmonitor / --delete', file=sys.stderr)
        return 2

    from app.settings_manager import SettingsManager
    from app.radarr_api import RadarrAPI

    sm = SettingsManager()
    radarr = RadarrAPI(sm.get('radarr.url'), sm.get('radarr.api_key'))
    movies = radarr.get_movies() or []
    parked = {}
    try:
        st = json.loads((_data_dir() / 'genre_roulette_state.json').read_text())
        parked = st.get('parked') or {}
    except Exception as e:
        print(f'warn: could not read genre_roulette parked map: {e}')

    parked_ids = set()
    for tid in parked:
        try:
            parked_ids.add(int(tid))
        except (TypeError, ValueError):
            pass

    ghosts = []
    for m in movies:
        if not m.get('monitored'):
            continue
        if m.get('hasFile') and (m.get('sizeOnDisk') or 0) > 0:
            continue
        tid = m.get('tmdbId')
        if args.only_parked and int(tid or 0) not in parked_ids:
            continue
        ghosts.append(m)

    print(f'ghosts matched: {len(ghosts)} '
          f'(only_parked={args.only_parked}, '
          f'action={"delete" if args.delete else "unmonitor" if args.unmonitor else "dry-run"})')
    for m in sorted(ghosts, key=lambda x: x.get('added') or ''):
        tid = m.get('tmdbId')
        mark = 'PARKED' if int(tid or 0) in parked_ids else '     '
        print(f"  {mark} {m.get('added')} | {m.get('title')} ({m.get('year')}) "
              f"id={m.get('id')} tmdb={tid} qp={m.get('qualityProfileId')}")

    if not ghosts or (not args.unmonitor and not args.delete):
        if ghosts and not args.unmonitor and not args.delete:
            print('\nDry-run only. Re-run with --unmonitor or --delete to apply.')
        return 0

    ok = fail = 0
    for m in ghosts:
        mid = m.get('id')
        title = m.get('title')
        try:
            if args.delete:
                if radarr.delete_movie(mid, delete_files=False):
                    print(f'  deleted {title} ({mid})')
                    ok += 1
                else:
                    print(f'  FAIL delete {title} ({mid})')
                    fail += 1
            else:
                if radarr.unmonitor_movie(mid):
                    print(f'  unmonitored {title} ({mid})')
                    ok += 1
                else:
                    print(f'  FAIL unmonitor {title} ({mid})')
                    fail += 1
        except Exception as e:
            print(f'  ERROR {title}: {e}')
            fail += 1
    print(f'done ok={ok} fail={fail}')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
