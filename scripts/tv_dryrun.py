#!/usr/bin/env python3
"""v177 TV grab dry run (read-only). Lists the episodes an automatic TV grab could take tonight under
tv_safety's rules: monitored show and episode, no linked file, aired at or after the floor (the start of
today ET or 24 h ago, whichever is earlier) or still to come, through 6 AM Eastern tomorrow (or
--until). Also prints the automatic grabs counted toward the daily ceiling (rolling 24 h).

Read-only: tv.db is opened with SQLite mode=ro (immutable when there is no -wal beside it, so no
-wal/-shm files are created either). tv_settings.json and section_trackers.json beside it are read as
plain files. No writes, no grabs, no tracker searches; no network unless --qbit (read-only: login and
torrents/info only).

Inside the :5002 container (from the app folder):
    python3 scripts/tv_dryrun.py --at 16:00                       # this install's tv.db (LUMA_DATA_DIR)
    python3 scripts/tv_dryrun.py --db /path/to/tv.db --at 16:00   # any copy
Options: --now <ISO time or epoch> (default: now), --until <ISO time> (default: 6 AM ET after --now),
--at HH:MM (repeatable): call out every episode airing within 30 min of that ET time today, eligible or
not, with the reason. --qbit URL: list the test/TV torrents and what the import would do (QBIT_USER and
QBIT_PASSWORD from the environment; never printed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import tv_safety  # noqa: E402

ET = ZoneInfo('America/New_York')


def _when(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        t = dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return (t if t.tzinfo else t.replace(tzinfo=ET)).timestamp()


def six_am_after(now: float) -> float:
    d = dt.datetime.fromtimestamp(now, ET)
    six = d.replace(hour=6, minute=0, second=0, microsecond=0)
    return (six if six > d else six + dt.timedelta(days=1)).timestamp()


def default_db() -> str:
    from app import paths
    return str(paths.data_path('tv.db'))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Read-only TV grab dry run (no grabs, no searches).')
    ap.add_argument('--db', help='tv.db to read (default: this install\'s)')
    ap.add_argument('--now', help='ISO time (ET when no zone) or epoch seconds')
    ap.add_argument('--until', help='ISO time (ET when no zone); default 6 AM ET after --now')
    ap.add_argument('--qbit', metavar='URL', help='also list the luma-test / TV-category torrents in this qBittorrent'
                    ' (read-only: login, torrents/info; user and password from QBIT_USER / QBIT_PASSWORD)')
    ap.add_argument('--at', action='append', default=[], metavar='HH:MM',
                    help='call out every episode airing within 30 min of this ET time today (repeatable)')
    a = ap.parse_args(argv)
    db = a.db or default_db()
    if not os.path.isfile(db):
        print(f'No tv.db at {db}', file=sys.stderr)
        return 2
    now = _when(a.now) or dt.datetime.now(dt.timezone.utc).timestamp()
    until = _when(a.until) or six_am_after(now)
    # read-only; a tv.db with no -wal beside it (a copy, or the app stopped) is opened immutable, so SQLite
    # creates no -wal/-shm files either. A live one already has them; a reader only takes a read lock.
    path = os.path.abspath(db)
    flags = 'mode=ro' + ('' if os.path.exists(path + '-wal') else '&immutable=1')
    conn = sqlite3.connect(f'file:{path}?{flags}', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = tv_safety.eligible(conn, now, until)
        try:
            done = tv_safety.automatic_today(conn, now)
        except sqlite3.Error:
            done = 0
        callouts = []
        today = dt.datetime.fromtimestamp(now, ET)
        for hhmm in a.at:
            h, m = (int(x) for x in hhmm.split(':'))
            t = today.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
            callouts.append((hhmm, [(r, tv_safety.explain(r, now, until))
                                    for r in tv_safety.airing_between(conn, t - 1800, t + 1800)]))
    finally:
        conn.close()
    fmt = lambda t: dt.datetime.fromtimestamp(t, ET).strftime('%a %b %-d, %-I:%M %p ET')  # noqa: E731
    se = lambda r: f"S{int(r['season_number'] or 0):02d}E{int(r['episode_number'] or 0):02d}"  # noqa: E731
    when = lambda r: fmt(r['start']) + ('' if r['airstamp'] else ' (date only: end of air date)')  # noqa: E731
    print(f'TV grab dry run (read-only) of {db}')
    print(f'Window: {fmt(tv_safety.floor_at(now))} (floor: start of today ET or 24 h ago, whichever is earlier)'
          f' through {fmt(until)}; now {fmt(now)}')
    print(f'Automatic grabs in the last 24 h: {done} of {tv_safety.DAILY_CAP}')
    print(f'Eligible episodes (monitored, no file): {len(rows)}')
    for r in rows:
        title = f" - {r['title']}" if r['title'] else ''
        print(f"  {r['show']}  {se(r)}{title}  {when(r)}")
    _settings_report(os.path.dirname(path))
    if a.qbit:
        _qbit_report(a.qbit)
    for hhmm, found in callouts:
        print(f'Airing within 30 min of {hhmm} ET today: {len(found)}')
        for r, why in found:
            mon = 'monitored' if r['show_monitored'] and r['ep_monitored'] else (
                'show not monitored' if not r['show_monitored'] else 'episode not monitored')
            title = f" - {r['title']}" if r['title'] else ''
            print(f"  {r['show']}  {se(r)}{title}  {when(r)}; {mon}; {'has a file' if r['has_file'] else 'no file'};"
                  f" {'ELIGIBLE' if why is None else 'not eligible: ' + why}")
    return 0


def _read_json(p: str):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        return {'_error': type(e).__name__}


def _settings_report(data_dir: str) -> None:
    """The TV automation switches (tv_settings.json) and the TV trackers switched on
    (section_trackers.json), read straight from the files (never through the app's loaders)."""
    from app.tv_settings import DEFAULT_AUTOMATION
    st = _read_json(os.path.join(data_dir, 'tv_settings.json'))
    auto = dict(DEFAULT_AUTOMATION)
    if isinstance(st, dict) and isinstance(st.get('automation'), dict):
        auto.update({k: v for k, v in st['automation'].items() if k in auto})
    src = 'tv_settings.json' if isinstance(st, dict) and '_error' not in st else (
        'tv_settings.json unreadable' if st else 'no tv_settings.json: defaults')
    print(f'TV automation ({src}): ' + ', '.join(f'{k}={auto[k]}' for k in
          ('enabled', 'feed_enabled', 'backlog_enabled', 'upgrades_enabled', 'import_enabled', 'first_search_delay_h')))
    print('  (v177: backlog_enabled is ignored; upgrades still refuse any episode with a file)')
    sec = _read_json(os.path.join(data_dir, 'section_trackers.json'))
    ids = []
    try:
        ids = [i for i in sec['sections']['tv']['enabled'] if isinstance(i, int)]
    except (TypeError, KeyError):
        pass
    if ids:
        print(f'TV trackers enabled: yes, {len(ids)} switched on in Settings > TV > Trackers (ids {", ".join(map(str, ids))};'
              ' each must also be on globally with a TV category)')
    else:
        print('TV trackers enabled: none yet (0 switched on in Settings > TV > Trackers), so nothing can be searched or grabbed')


def _qbit_report(url: str) -> None:
    """Read-only: qBittorrent login, then torrents/info for this instance's TV category/tag and the test
    movie tag. Prints what the TV download watch or the movie mover would do with each. Never writes."""
    import requests
    from app import qbit_owner, tv_downloads
    url = url.rstrip('/')
    tv_name, prod_tv = tv_downloads._names()
    movie_tag = qbit_owner.tag_name()
    s = requests.Session()
    try:
        r = s.post(f'{url}/api/v2/auth/login', data={'username': os.environ.get('QBIT_USER', ''),
                                                     'password': os.environ.get('QBIT_PASSWORD', '')},
                   timeout=10, allow_redirects=False)
        if r.status_code != 200 or r.text.strip() != 'Ok.':
            print(f'qBittorrent at {url}: login refused (HTTP {r.status_code})')
            return
        seen = {}
        for params in ({'category': tv_name}, {'tag': tv_name}, {'tag': movie_tag}, {'category': movie_tag}):
            got = s.get(f'{url}/api/v2/torrents/info', params=params, timeout=15, allow_redirects=False)
            for t in (got.json() if got.status_code == 200 else []):
                seen[t.get('hash')] = t
    except (requests.RequestException, ValueError) as e:
        print(f'qBittorrent at {url}: unreachable ({type(e).__name__})')
        return
    finally:
        s.close()
    print(f'qBittorrent torrents tagged or filed as {tv_name} / {movie_tag}: {len(seen)}')
    for t in sorted(seen.values(), key=lambda t: str(t.get('name') or '')):
        cat = str(t.get('category') or '')
        tags = {x.strip().casefold() for x in str(t.get('tags') or '').split(',') if x.strip()}
        done = float(t.get('progress') or 0) >= 1.0
        if prod_tv.casefold() == cat.casefold() or prod_tv.casefold() in tags:
            what = "ignored (carries production's TV name)"
        elif cat.casefold() == tv_name.casefold() or tv_name.casefold() in tags:
            what = ('TV download watch: imports it (hardlink, else copy) into its show folder on the next pass, '
                    'if each file parses to an episode of a library show' if done else 'TV download watch: waits until it completes')
        elif movie_tag.casefold() in tags or cat.casefold() == movie_tag.casefold():
            what = ('movie mover: files it (hardlink, else copy) into the movie library' if done
                    else 'movie mover: waits until it completes')
        else:
            what = 'ignored'
        print(f"  {t.get('name')}  category={cat or '-'} tags={','.join(sorted(tags)) or '-'} "
              f"progress={float(t.get('progress') or 0):.0%} -> {what}")


if __name__ == '__main__':
    sys.exit(main())
