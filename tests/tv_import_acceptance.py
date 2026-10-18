"""TV Import acceptance: Taylor's 687 real folder names, synthesized files,
and the synthesized TMDB mock (tests/fixtures/tv_import, build_fixture.py).
The real scan runs end to end: tv_scan's 8 workers, tv_tmdb's HTTP code
(token bucket, 429 handling, append_to_response batches) against the mock's
transport, then tv.db is read back.

Used by tests/test_tv_import_acceptance.py; the timed run (35 req/s, TMDB-
like latency) is `python -m tests.tv_import_acceptance --timed`."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections import Counter
from typing import Any, Dict
from unittest import mock

from app import file_management as fm, tv_scan, tv_settings, tv_store, tv_tmdb
from tests import tv_import_mock as M

EPISODE_SHOWS = ('SpongeBob SquarePants', 'Hard Knocks (2001)', 'Friends')
PID = 'p_7a1b2c3d'


def build_tree(root: str, cap_known: int = 3) -> int:
    """Taylor's folders under *root* with empty video files. The 562 folders
    the old matcher matched keep at most *cap_known* files a season (their
    match never needs files); the rest keep every file."""
    f = M.load()
    # the 562 the old matcher matched carry synthesized ids 800000+
    unmatched_before = {k for k, t in f['truth'].items() if not 800000 <= (t.get('id') or 0) < 900000}
    n = 0
    for folder in f['folders']:
        os.makedirs(os.path.join(root, folder), exist_ok=True)
        per: Counter = Counter()
        for rel in M.file_names(folder):
            season = rel.split('/')[1]
            if folder not in EPISODE_SHOWS and folder not in unmatched_before and cap_known:
                per[season] += 1
                if per[season] > cap_known:
                    continue
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'wb').close()
            n += 1
    return n


def evaluate() -> Dict[str, Any]:
    """Tiers and checks from tv.db after an import."""
    f = M.load()
    truth = f['truth']
    with tv_store.connect() as conn:
        series = {r['folder']: dict(r) for r in conn.execute('SELECT * FROM series WHERE path_id IS NOT NULL')}
        review = {r['folder']: dict(r) for r in conn.execute('SELECT * FROM unmatched')}
        leftovers = {}
        for name in EPISODE_SHOWS:
            s = series.get(name)
            if s:
                st = Counter(r['state'] for r in conn.execute('SELECT state FROM episode_files WHERE series_id = ?',
                                                              (s['id'],)))
                leftovers[name] = {'files': sum(st.values()), 'linked': st['linked'],
                                   'left': st['mismatch'] + st['unrecognized'],
                                   'pct': round(100 * (st['mismatch'] + st['unrecognized']) / max(sum(st.values()), 1), 2),
                                   'group': s['episode_group_id']}
    tiers: Counter = Counter()
    wrong, ambiguous_auto, missed = [], [], []
    for folder in f['folders']:
        t = truth[folder]
        if folder in series:
            s = series[folder]
            tier = s['match_tier'] or 'id'
            tiers[tier] += 1
            if t.get('accept'):
                ambiguous_auto.append((folder, s['tmdb_id']))
                if s['tmdb_id'] not in t['accept']:
                    wrong.append((folder, s['tmdb_id'], t['accept']))
            elif s['tmdb_id'] != t['id']:
                wrong.append((folder, s['tmdb_id'], t['id']))
        else:
            r = review.get(folder)
            tier = r['tier'] if r else 'missing'
            tiers[tier] += 1
            if t.get('id') and not t.get('duplicate'):
                missed.append((folder, tier, r and r['pick'], t['id']))
    n = len(f['folders'])
    auto = sum(v for k, v in tiers.items() if k in ('id', 'high'))
    # Decidable: the right show is known from the folder and exists once. Not decidable: accept-list
    # folders (the right show can't be known), a second folder for a show (duplicate), no TMDB answer.
    undecidable = {k for k in f['folders'] if truth[k].get('accept') or truth[k].get('duplicate') or not truth[k].get('id')}
    decidable = n - len(undecidable)
    auto_decidable = sum(1 for k in f['folders'] if k not in undecidable and k in series)
    likely_right = sum(1 for folder, r in review.items() if r['tier'] == 'likely'
                       and r['pick'] == truth.get(folder, {}).get('id'))
    likely = [folder for folder, r in review.items() if r['tier'] == 'likely']
    return {'folders': n, 'tiers': dict(tiers), 'auto': auto, 'auto_pct': round(100 * auto / n, 2),
            'decidable': decidable, 'auto_decidable': auto_decidable,
            'decidable_pct': round(100 * auto_decidable / max(decidable, 1), 2),
            'wrong': wrong, 'ambiguous_auto': ambiguous_auto, 'missed': missed, 'leftovers': leftovers,
            'likely_with_right_guess': f'{likely_right} of {len(likely)}',
            'same_name': {k: _where(k, series, review) for k in ('Cheers', 'The Office (US)', 'Love', 'Cheat (2019)')}}


def _where(folder, series, review):
    if folder in series:
        return {'auto': series[folder]['tmdb_id'], 'year': series[folder]['year']}
    r = review.get(folder)
    return {'review': r['tier'], 'pick': r['pick']} if r else None


def run(root: str, *, rate: float = 0.0, latency: float = 0.0, server_rate=None) -> Dict[str, Any]:
    """Import *root* (already built) as one TV library; the setup (stores in a
    temp dir) is the caller's. Returns timings and the TMDB call count."""
    m = M.MockTmdb(latency=latency, server_rate=server_rate)
    fm.save({**fm.load(), 'paths': [{'id': PID, 'name': 'TV', 'path': root}], 'fallback_path_id': None})
    tv_settings.save({**tv_settings.load(), 'libraries': [PID]})
    seen = {}

    def watch():                       # when the table is reviewable: every folder decided
        while not seen.get('stop'):
            p = tv_scan.progress()
            if p['total'] and p['done'] >= p['total'] and 'decided' not in seen:
                seen['decided'] = time.monotonic()
            time.sleep(0.05)
    with mock.patch.dict(tv_tmdb._cfg, {'get_key': lambda: 'k', 'session_factory': m.session, 'cache': True}), \
            mock.patch.object(tv_tmdb, 'tmdb_base_url', lambda: 'https://mock.tmdb/3'), \
            mock.patch.object(tv_tmdb, 'RATE_PER_S', rate):
        import threading
        w = threading.Thread(target=watch, daemon=True)
        t0 = time.monotonic()
        w.start()
        assert tv_scan.run(now=time.time())
        t1 = time.monotonic()
        seen['stop'] = True
        w.join()
    calls = Counter(c[0].split('/')[1] if c[0].count('/') else c[0] for c in m.calls)
    return {'seconds_to_review_table': round(seen.get('decided', t1) - t0, 1), 'seconds_total': round(t1 - t0, 1),
            'tmdb_calls': len(m.calls), 'throttled_429': m.throttled, 'calls_by_kind': dict(calls),
            'error': tv_scan.status()['error']}


def main(timed: bool) -> None:
    tmp = tempfile.mkdtemp(prefix='tvimport-')
    store = os.path.join(tmp, 'data')
    os.makedirs(store)
    root = os.path.join(tmp, 'media', 'TV')
    n_files = build_tree(root)
    with mock.patch.dict(os.environ, {'LUMA_BROWSE_ROOTS': os.path.join(tmp, 'media')}), \
            mock.patch.object(fm, 'store_path', lambda: __import__('pathlib').Path(store) / 'fm.json'), \
            mock.patch.dict(tv_settings._cfg, {'store_path': os.path.join(store, 'tvs.json')}), \
            mock.patch.dict(tv_store._cfg, {'path': os.path.join(store, 'tv.db')}):
        tv_store._ready.clear()
        kw = {'rate': tv_tmdb.RATE_PER_S, 'latency': 0.12, 'server_rate': 40} if timed else {}
        timing = run(root, **kw)
        ev = evaluate()
    print(json.dumps({'files': n_files, **timing, **{k: v for k, v in ev.items()
                                                      if k not in ('missed', 'ambiguous_auto')}}, indent=1))
    print('missed', len(ev['missed']))
    for x in ev['missed']:
        print('   ', x)
    print('ambiguous_auto', ev['ambiguous_auto'])


if __name__ == '__main__':
    main('--timed' in sys.argv)
