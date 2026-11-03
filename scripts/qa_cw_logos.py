#!/usr/bin/env python3
"""cw-logo verification (read-only). For every Continue Watching item this install's Plex returns, prints its type,
ids, the TMDb id the sheet uses for the clearlogo, and whether a logo resolves, plus how long each step takes (the
sheet decides logo vs text by its 1.0 s deadline).

Read-only: Plex GETs (/library/onDeck, /library/metadata/<key>) and TMDb GETs (/3/<type>/<id>/images) only. Nothing is
written: no settings, no database, no cache file (the app's in-memory caches are this process's own and die with it).
Uses this install's own configured Plex and TMDb settings, exactly as the app does; never prints them.

Inside the app container (from the app folder):
    python3 scripts/qa_cw_logos.py            # table
    python3 scripts/qa_cw_logos.py --json     # one JSON object per item
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests  # noqa: E402

from app import home_rails, tv_plex  # noqa: E402
from app.config import Config  # noqa: E402
from app.plex_service import plex_service  # noqa: E402
from app.settings_manager import SettingsManager  # noqa: E402


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _logo(tmdb_id: int, media_type: str, api_key: str):
    """(file_path | None, status) the way /api/clearlogo picks it (en, else any; PNG first; by votes)."""
    try:
        r = requests.get(f'https://api.themoviedb.org/3/{media_type}/{tmdb_id}/images',
                         params={'api_key': api_key, 'include_image_language': 'en,null'}, timeout=8)
    except Exception as e:  # noqa: BLE001
        return None, f'error {type(e).__name__}'
    if r.status_code == 404:
        return None, 'tmdb 404'
    if not r.ok:
        return None, f'tmdb {r.status_code}'
    logos = r.json().get('logos', [])
    en = [lg for lg in logos if lg.get('iso_639_1') == 'en']
    pool = en or logos
    png = sorted([lg for lg in pool if (lg.get('file_path') or '').lower().endswith('.png')], key=lambda x: x.get('vote_average', 0), reverse=True)
    oth = sorted([lg for lg in pool if not (lg.get('file_path') or '').lower().endswith('.png')], key=lambda x: x.get('vote_average', 0), reverse=True)
    best = (png + oth)[0] if (png or oth) else None
    return (best['file_path'] if best else None), ('ok' if best else 'no logo on TMDb')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    # Never write: a settings migration or bootstrap in this process must not save (the running app owns the file).
    SettingsManager._save = lambda self, *a, **k: None  # type: ignore[method-assign]
    if not SettingsManager().__dict__.get('_path') or not SettingsManager()._path.exists():
        print('No settings file here: run inside the app container'); return 2
    settings = SettingsManager()
    plex_service.update_config(settings.get('plex.url', ''), settings.get('plex.token', ''))
    api_key = settings.get('apis.tmdb.api_key', '') or Config.TMDB_API_KEY
    if not getattr(plex_service, 'is_configured', False):
        print('Plex is not configured here'); return 2
    t0 = time.monotonic()
    items = home_rails.continue_watching(plex_service)
    print(f'# {len(items)} Continue Watching items (onDeck {_ms(t0)} ms); TV index built in this process: {bool(tv_plex._shows)}')
    rows = []
    for c in items:
        kind = c.get('type')
        opens_movie = kind == 'movie'
        open_key = c.get('key') if opens_movie else (c.get('show_key') or c.get('key'))
        t1 = time.monotonic()
        meta = plex_service.get_metadata(int(open_key)) or {}
        plex_ms = _ms(t1)
        plex_tmdb = meta.get('tmdb_id')
        used = c.get('tmdb_hint') or plex_tmdb
        media = 'movie' if opens_movie else 'tv'
        t2 = time.monotonic()
        path, status = _logo(int(used), media, api_key) if used and api_key else (None, 'no tmdb id' if not used else 'no TMDb key')
        row = {'title': c.get('title'), 'type': kind, 'card_key': c.get('key'), 'show_key': c.get('show_key'),
               'opens': f'{media}:{open_key}', 'tmdb_hint': c.get('tmdb_hint'), 'plex_tmdb_id': plex_tmdb,
               'tmdb_used': used, 'hint_matches_plex': (c.get('tmdb_hint') == plex_tmdb) if c.get('tmdb_hint') else None,
               'logo': path, 'logo_status': status, 'plex_ms': plex_ms, 'logo_ms': _ms(t2),
               'plex_guids': meta.get('guids') or meta.get('guid')}
        rows.append(row)
        if args.json:
            print(json.dumps(row, default=str))
    if not args.json:
        hdr = f"{'type':8} {'title':32} {'opens':12} {'hint':>8} {'plex':>8} {'used':>8} {'plex ms':>7} {'logo ms':>7}  logo"
        print(hdr); print('-' * len(hdr))
        for r in rows:
            print(f"{str(r['type']):8} {str(r['title'])[:32]:32} {r['opens']:12} {str(r['tmdb_hint'] or '-'):>8} "
                  f"{str(r['plex_tmdb_id'] or '-'):>8} {str(r['tmdb_used'] or '-'):>8} {r['plex_ms']:>7} {r['logo_ms']:>7}  "
                  f"{'YES ' + r['logo'] if r['logo'] else 'NO (' + r['logo_status'] + ')'}")
        miss = [r for r in rows if not r['logo']]
        print(f'# logos resolved: {len(rows) - len(miss)}/{len(rows)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
