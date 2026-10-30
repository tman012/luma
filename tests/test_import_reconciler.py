"""Import reconciler (P0): completed Luma-owned torrents that never imported are matched against the library
and the import jobs and re-queued (stale terminal skipped/failed after a restart, a refused remote copy, a
lost mover job); never a double import; permanent failures stay terminal; TV keys per hash+file."""
import json
import logging
import os
import re
import threading
import time

import pytest

from app import file_management as fm, import_reconciler as ir, instance, library_mover as lm
from app import movie_grabs, movie_import as mi, movie_settings, tv_downloads, tv_store
from tests.test_library_mover import H1, H2, TMDB, add_path, env, job, movie_file, torrent  # noqa: F401

NOW = 2_000_000_000.0
HOST_500 = "host mover failed HTTP 500 rc=2: bash: -c: line 1: syntax error near unexpected token '('"
H3 = 'd' * 40


@pytest.fixture
def rec(env, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(instance, 'is_test', lambda: True)
    monkeypatch.setitem(ir._cfg, 'db_path', str(tmp_path / 'store' / ir.DB_NAME))
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(movie_settings.SEED_FILE))
    monkeypatch.setitem(tv_store._cfg, 'path', str(tmp_path / 'tvdb' / 'tv.db'))
    from app.automation import gr_store
    monkeypatch.setitem(gr_store._cfg, 'db_path', str(tmp_path / 'auto' / 'automation.db'))
    from app import grab_cache
    monkeypatch.setitem(grab_cache._cfg, 'dir', str(tmp_path / 'grab_cache'))
    meta = {}
    monkeypatch.setattr(tv_store, 'get_meta', lambda k: meta.get(k))
    monkeypatch.setattr(tv_store, 'set_meta', lambda k, v: meta.__setitem__(k, v))
    env.meta = meta
    env.run = lambda now=NOW, ts=None, files=None: ir.tick(
        now=now, fetch=lambda: (list(ts if ts is not None else env.q.torrents.values()),
                                (lambda h: (files or {}).get(h))))
    return env


def _skip_job(h, err, finished=NOW - 3600, **kw):
    def apply(d):
        d['jobs'][h].update(state='skipped', error=err, finished=finished, **kw)
    fm.update(apply)


def _heat(env):  # noqa: F811
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    return torrent(env, H1, 'Heat.1995.mkv')


# ------------------------------------------------------------------ matching

def test_owned_names_come_from_the_code_not_a_hardcoded_list(rec, monkeypatch):
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'qa-box')
    monkeypatch.setenv('LUMA_MOVIE_CATEGORY', 'films')
    names = {n.casefold() for n in ir.owned_names()}
    assert {'qa-box', 'films', tv_downloads.category().casefold()} <= names
    assert ir.is_owned({'hash': H2, 'tags': 'x, QA-Box'}, set())
    assert ir.is_owned({'hash': H2, 'category': 'films'}, set())
    assert not ir.is_owned({'hash': H2, 'category': 'radarr', 'tags': ''}, set())
    assert ir.is_owned({'hash': H2, 'category': 'radarr', 'tags': ''}, {H2})          # a hash a mover job tracks
    assert not ir.is_owned({'hash': H2, 'category': tv_downloads.other_category()}, {H2})  # the other install's


def test_done_job_is_already_imported_and_untouched(rec, caplog):
    _heat(rec)
    assert rec.tick()['filed'] == 1
    caplog.set_level(logging.INFO, logger='app.import_reconciler')
    out = rec.run()
    assert out['already_imported'] == 1 and out['requeued'] == 0
    assert job(H1)['state'] == 'done'
    assert any('already_imported Heat.1995.mkv' in r.message and 'mover job done' in r.message for r in caplog.records)
    assert any('pass done: 1 checked, 1 already imported, 0 requeued, 0 re-added, 0 lost, 0 import failed, 0 skipped' in r.message for r in caplog.records)


def test_matched_by_file_in_library_and_by_tmdb_of_another_grab(rec, tmp_path):
    f = tmp_path / 'Heat (1995).mkv'
    f.write_bytes(b'x')
    movie_grabs.record(3082, 'Heat.1995.REMUX', H2, now=NOW)
    movie_grabs.mark_imported(H2, path=str(f), now=NOW)
    movie_grabs.record(3082, 'Heat.1995.1080p', H3, now=NOW)
    movie_grabs.set_state(H3, 'failed', error=HOST_500, now=NOW - 3600)
    t = {'hash': H3, 'name': 'Heat.1995.1080p', 'tags': 'luma-test', 'category': 'luma-test', 'progress': 1,
         'state': 'stalledUP'}
    out = rec.run(ts=[t])
    assert out['already_imported'] == 1 and movie_grabs.by_hash(H3)['state'] == 'failed'


def test_incomplete_paused_and_foreign_torrents_are_ignored(rec):
    _heat(rec)
    ts = [dict(rec.q.torrents[H1], progress=0.5), dict(rec.q.torrents[H1], hash=H2, state='pausedUP'),
          {'hash': H3, 'name': 'x', 'category': 'radarr', 'tags': '', 'progress': 1, 'state': 'stalledUP'}]
    assert rec.run(ts=ts)['checked'] == 0


# ------------------------------------------------------------------ requeue of stale terminal states

def test_refused_remote_copy_is_requeued_then_filed_once(rec, caplog):
    """The Watcher in the Woods / Smile 2 case: the old code skipped the job for a host mover refusal."""
    _heat(rec)
    _skip_job(H1, HOST_500)
    caplog.set_level(logging.INFO, logger='app.import_reconciler')
    out = rec.run()
    assert out['requeued'] == 1 and job(H1)['state'] == 'waiting' and job(H1)['reconcile_requeues'] == 1
    assert any(f'requeued Heat.1995.mkv [{H1[:8]}]: skipped: {HOST_500}' in r.message for r in caplog.records)
    assert rec.tick()['filed'] == 1 and job(H1)['state'] == 'done'
    assert rec.run(now=NOW + 7200)['already_imported'] == 1


def test_only_imported_dismissed_or_qa_disposable_are_terminal(rec):
    _heat(rec)
    _skip_job(H1, 'not a video')                               # skipped/unknown: retryable (PM spec)
    assert rec.run()['requeued'] == 1
    _skip_job(H1, 'episode not monitored')                     # a user dismissal
    assert rec.run(now=NOW + 3600)['requeued'] == 0 and job(H1)['state'] == 'skipped'
    _skip_job(H1, 'ENOENT')
    rec.q.torrents[H1]['tags'] = 'luma-test, qa-disposable'    # a QA disposable
    out = rec.run(now=NOW + 7200)
    assert out['requeued'] == 0 and job(H1)['state'] == 'skipped'


def test_backoff_1m_5m_30m_2h_then_import_failed_with_one_push(rec, monkeypatch):
    from app import library_notify
    pushes = []
    monkeypatch.setattr(library_notify, '_push', lambda *a: pushes.append(a))
    _heat(rec)
    _skip_job(H1, 'ENOENT', finished=NOW - 30)
    assert rec.run()['requeued'] == 0                                        # ended < 1 min ago
    t = NOW + 60
    for n, wait in enumerate((0,) + ir.BACKOFF_S):                           # 5 requeues
        t += wait
        _skip_job(H1, 'ENOENT', finished=t - 60)
        if wait:
            assert rec.run(now=t - 1)['requeued'] == 0, n                   # inside the backoff
        assert rec.run(now=t)['requeued'] == 1, n
    t += 10 * 7200
    _skip_job(H1, 'ENOENT', finished=t - 3600)
    out = rec.run(now=t)
    assert out['requeued'] == 0 and out['import_failed'] == 1 and job(H1)['state'] == 'skipped'
    assert rec.run(now=t + 1)['import_failed'] == 1
    assert len(pushes) == 1 and 'could not be imported' in pushes[0][1]
    assert ir.keys(H1)[0]['requeues'] == ir.MAX_REQUEUES and ir.keys(H1)[0]['decision'] == 'import_failed'


def test_failed_movie_grab_is_requeued_for_movie_import(rec, tmp_path):
    lib = tmp_path / 'MovieLib'
    lib.mkdir()
    assert mi.save_settings({'root': str(lib)}) == {}
    movie_grabs.record(3082, 'Heat.1995', H2, now=NOW)
    movie_grabs.set_state(H2, 'failed', error='helper socket unreachable', now=NOW - 3600)
    t = {'hash': H2, 'name': 'Heat.1995', 'tags': 'luma-test', 'category': 'luma-test', 'progress': 1,
         'state': 'stalledUP', 'amount_left': 0}
    assert rec.run(ts=[t])['requeued'] == 1 and movie_grabs.by_hash(H2)['state'] == 'grabbed'
    assert rec.run(ts=[t], now=NOW + 1)['skipped'] == 1                       # now queued in movie import


def test_grab_with_no_movie_import_folder_goes_to_the_mover(rec):
    """GR/watchlist grabs (movie_grabs only) never import when Settings > Movies has no import folder."""
    add_path('Movies', rec.media / 'Movies')
    movie_file(rec, 'Heat.1995.mkv')
    with movie_settings.connect() as conn:
        conn.execute("INSERT INTO watchlist (tmdb_id, title, year, added_at) VALUES (3082, 'Heat', 1995, 1)")
    movie_grabs.record(3082, 'Heat.1995', H1, now=NOW)
    torrent(rec, H1, 'Heat.1995.mkv')
    assert rec.run()['requeued'] == 1 and job(H1)['state'] == 'waiting'
    assert rec.tick()['filed'] == 1
    assert movie_grabs.by_hash(H1)['state'] == 'imported'


# ------------------------------------------------------------------ no double import

def test_waiting_and_inflight_jobs_are_never_touched(rec):
    _heat(rec)
    lm._set_inflight(H1, '/media/Movies/Heat (1995)/Heat (1995).mkv', '/media/Movies/Heat (1995)/.luma-x.luma-partial')
    lm._set_job(H1, retry_at=NOW + 60)
    before = dict(job(H1))
    out = rec.run()
    assert out['skipped'] == 1 and out['requeued'] == 0 and job(H1) == before


def test_concurrent_passes_requeue_once(rec):
    _heat(rec)
    _skip_job(H1, HOST_500)
    results = []
    barrier = threading.Barrier(4)

    def go():
        barrier.wait()
        results.append(ir.reconcile(list(rec.q.torrents.values()), NOW)['requeued'])
    threads = [threading.Thread(target=go) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sum(results) == 1 and job(H1)['reconcile_requeues'] == 1


def test_pass_racing_a_live_job_leaves_it_alone(rec, monkeypatch):
    """The job is retried by hand between the reconciler's read and its write: no second requeue."""
    _heat(rec)
    _skip_job(H1, HOST_500)
    real = ir.claim

    def claim_then_retry(*a, **k):
        r = real(*a, **k)
        assert lm.retry(H1)                       # the owner's Retry lands meanwhile
        return r
    monkeypatch.setattr(ir, 'claim', claim_then_retry)
    out = rec.run()
    assert out['requeued'] == 0 and out['skipped'] == 1
    assert ir.keys(H1)[0]['requeues'] == 0        # the claim was given back
    assert 'reconcile_requeues' not in job(H1)


def test_the_tick_lock_skips_an_overlapping_pass(rec):
    _heat(rec)
    _skip_job(H1, HOST_500)
    assert ir._lock.acquire(blocking=False)
    try:
        assert rec.run()['checked'] == 0
    finally:
        ir._lock.release()
    assert job(H1)['state'] == 'skipped'


# ------------------------------------------------------------------ restart / reboot

def test_restart_job_dropped_while_qbit_restarted_is_tracked_again(rec):
    """Before a reboot the reconciler saw the job; qBit came back with an empty list and (old code) the mover
    dropped the job after 3 passes. The next pass tracks it again from the ledger and it is filed once."""
    _heat(rec)
    assert rec.run(ts=[])['checked'] == 0
    rec.run()                                          # seen: snapshot kept
    lm._drop_job(H1, 'removed from qBittorrent')       # what the old orphan rule did
    assert job(H1) is None
    out = rec.run(now=NOW + 1)
    assert out['requeued'] == 1 and job(H1)['state'] == 'waiting' and job(H1)['title'] == 'Heat'
    assert rec.tick()['filed'] == 1
    assert rec.run(now=NOW + 2 * ir.RETRY_AFTER_S)['already_imported'] == 1


def test_restart_qbit_empty_list_no_longer_drops_the_job_quickly(rec):
    add_path('Movies', rec.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    for i in range(5):
        rec.tick(now=NOW + 30 * i)
    assert job(H1)['state'] == 'waiting'


def test_restart_stale_skipped_job_from_before_restart_is_requeued(rec):
    """A job skipped before a restart (finished hours ago, the reconciler never ran) is picked up by the
    first pass after start."""
    _heat(rec)
    _skip_job(H1, 'the file changed while being checked', finished=NOW - 6 * 3600)
    assert rec.run()['requeued'] == 1


def test_qbit_unreachable_decides_nothing(rec):
    _heat(rec)
    _skip_job(H1, HOST_500)
    assert ir.tick(now=NOW, fetch=lambda: (None, None))['checked'] == 0
    assert job(H1)['state'] == 'skipped'


# ------------------------------------------------------------------ TV

TV_FILES = [{'name': 'Show.S01.1080p/Show.S01E01E02.1080p.mkv'}, {'name': 'Show.S01.1080p/Show.S01E03.1080p.mkv'},
            {'name': 'Show.S01.1080p/sample.mkv'}, {'name': 'Show.S01.1080p/Show.nfo'}]


def _tv(h=H2):
    return {'hash': h, 'name': 'Show.S01.1080p', 'category': tv_downloads.category(), 'tags': '', 'progress': 1,
            'state': 'stalledUP'}


def test_tv_multi_episode_keys_per_file_and_one_requeue(rec):
    rec.meta[tv_downloads._key(H2)] = json.dumps({'state': 'refused', 'error': HOST_500, 'at': NOW - 3600})
    out = rec.run(ts=[_tv()], files={H2: TV_FILES})
    assert out['requeued'] == 1 and tv_downloads.status(H2)['state'] == 'waiting'
    ks = {k['key']: k for k in ir.keys(H2)}
    assert set(ks) == {H2, f'{H2}:Show.S01.1080p/Show.S01E01E02.1080p.mkv', f'{H2}:Show.S01.1080p/Show.S01E03.1080p.mkv'}
    assert all(k['requeues'] == 1 for k in ks.values())
    out = rec.run(ts=[_tv()], files={H2: TV_FILES}, now=NOW + 1)         # queued now: left to the TV watch
    assert out['requeued'] == 0 and out['skipped'] == 1


def test_tv_imported_and_permanent_are_left_alone(rec):
    rec.meta[tv_downloads._key(H2)] = json.dumps({'state': 'imported', 'files': [{'path': '/x'}]})
    rec.meta[tv_downloads._key(H3)] = json.dumps({'state': 'refused', 'error': tv_downloads.UNMONITORED, 'at': 0})
    out = rec.run(ts=[_tv(H2), _tv(H3)], files={H2: TV_FILES, H3: TV_FILES})
    assert out['already_imported'] == 1 and out['skipped'] == 1 and out['requeued'] == 0
    assert tv_downloads.status(H3)['state'] == 'refused'


def test_tv_failed_file_error_is_requeued_after_restart(rec):
    rec.meta[tv_downloads._key(H2)] = json.dumps({'state': 'failed', 'error': 'file error (EIO)', 'at': NOW - 86400,
                                                  'os_errors': 6})
    assert rec.run(ts=[_tv()], files={H2: TV_FILES})['requeued'] == 1
    assert tv_downloads.status(H2)['os_errors'] == 0


# ------------------------------------------------------------------ source fixes

def test_movie_import_cut_off_remote_copy_is_not_failed_for_good(monkeypatch, tmp_path):
    from app import remote_mover as rm
    monkeypatch.setattr(rm, 'enabled', lambda: True)

    def cut(*a, **k):
        raise rm.RemoteConnectionLost('helper socket unreachable')
    monkeypatch.setattr(rm, 'transfer', cut)
    src = tmp_path / 'a.mkv'
    src.write_bytes(b'x')
    with pytest.raises(mi.Skip):
        mi.copy_verified(str(src), str(tmp_path / 'lib' / 'a.mkv'))

    def refuse(*a, **k):
        raise rm.RemoteMoverError('src missing')
    monkeypatch.setattr(rm, 'transfer', refuse)
    with pytest.raises(mi.Refused):
        mi.copy_verified(str(src), str(tmp_path / 'lib' / 'a.mkv'))


def test_requeue_failed_is_atomic(rec):
    movie_grabs.record(3082, 'Heat', H2, now=NOW)
    assert not movie_grabs.requeue_failed(H2)                     # not failed
    movie_grabs.set_state(H2, 'failed', error='x', now=NOW)
    assert not movie_grabs.requeue_failed(H2, expect_error='y')   # failed for another reason meanwhile
    assert movie_grabs.requeue_failed(H2, expect_error='x')
    assert not movie_grabs.requeue_failed(H2)


def test_tv_no_episodes_yet_is_retried_capped(rec):
    """A special (e.g. a Halloween episode) whose episode wasn't in the show's metadata when it finished."""
    rec.meta[tv_downloads._key(H2)] = json.dumps({'state': 'refused', 'error': tv_downloads.NO_EPISODES,
                                                  'at': NOW - 3600, 'matcher': tv_downloads.MATCHER})
    assert rec.run(ts=[_tv()], files={H2: TV_FILES})['requeued'] == 1


# ------------------------------------------------------------------ window, adoption

def test_completed_older_than_14_days_is_not_looked_at(rec):
    t = dict(_heat(rec), completion_on=NOW - 15 * 86400)
    _skip_job(H1, 'ENOENT')
    assert rec.run(ts=[t])['checked'] == 0


def test_adopts_outside_watched_categories_only_when_luma_grabbed_it(rec):
    movie_grabs.record(3082, 'Heat', H2, now=NOW - 86400)
    movie_grabs.set_state(H2, 'failed', error='ENOENT', now=NOW - 3600)
    ours = {'hash': H2, 'name': 'Heat', 'category': 'somewhere-else', 'tags': '', 'progress': 1, 'state': 'stalledUP'}
    stranger = dict(ours, hash=H3, name='Other')
    out = rec.run(ts=[ours, stranger])
    assert out['checked'] == 1 and out['requeued'] == 1


# ------------------------------------------------------------------ lost grabs (qBit took the add, then lost it)

def _tv_grab_row(h, state='downloading', at=NOW - 3600, sid=147, eids=(8453,), title='Show S01E04 1080p'):
    with tv_store.write() as conn:
        conn.execute("INSERT INTO series (id, title, tmdb_id, added_at) VALUES (?, 'Show', 99, 1) ON CONFLICT DO NOTHING", (sid,))
        conn.execute("INSERT INTO grabs (hash, series_id, season_number, episode_ids, release_title, origin, grabbed_at,"
                     " state) VALUES (?, ?, 1, ?, ?, 'auto', ?, ?)", (h, sid, json.dumps(list(eids)), title, at, state))


def _grab_state(h):
    with tv_store.connect() as conn:
        r = conn.execute('SELECT state, error FROM grabs WHERE hash = ?', (h,)).fetchone()
    return dict(r) if r else None


def test_lost_tv_grab_without_cache_is_marked_lost_and_researched(rec, monkeypatch):
    _tv_grab_row(H2)
    searched = []
    monkeypatch.setattr('app.tv_search.search_show', lambda sid, ids=None, **k: searched.append((sid, ids)) or
                        {'ok': True, 'picks': [{'grabbed': True}]})
    other = {'hash': H3, 'name': 'x', 'category': 'radarr', 'tags': '', 'progress': 1, 'state': 'stalledUP'}
    out = rec.run(ts=[other])
    assert out['lost'] == 1
    assert _grab_state(H2)['state'] == 'failed' and _grab_state(H2)['error'].startswith('lost: not in qBittorrent')
    assert searched == [(147, [8453])]
    assert rec.run(ts=[other], now=NOW + 600)['lost'] == 0                      # never twice


def test_young_or_present_grab_or_empty_qbit_is_not_lost(rec):
    _tv_grab_row(H2, at=NOW - 60)                                               # < 5 min old
    _tv_grab_row(H3)
    assert rec.run(ts=[])['lost'] == 0                                          # qBit lists nothing: restarting
    present = {'hash': H3, 'name': 'x', 'category': 'luma-test-tv', 'tags': '', 'progress': 0.4, 'state': 'downloading'}
    assert rec.run(ts=[present])['lost'] == 0
    assert _grab_state(H2)['state'] == _grab_state(H3)['state'] == 'downloading'


def test_lost_grab_with_cached_torrent_is_readded_once_then_lost(rec, monkeypatch):
    from app import grab_cache
    _tv_grab_row(H2)
    grab_cache.save(H2, 'magnet', f'magnet:?xt=urn:btih:{H2}')
    sent = []
    monkeypatch.setattr(ir, 'readd', lambda g, kind, payload, **k: sent.append((g['hash'], kind, payload)) or None)
    monkeypatch.setattr('app.tv_search.search_show', lambda *a, **k: {'ok': True, 'picks': []})
    other = {'hash': H3, 'name': 'x', 'category': 'radarr', 'tags': '', 'progress': 1, 'state': 'stalledUP'}
    assert rec.run(ts=[other])['readded'] == 1 and sent == [(H2, 'magnet', f'magnet:?xt=urn:btih:{H2}')]
    assert _grab_state(H2)['state'] == 'downloading'
    assert rec.run(ts=[other], now=NOW + 60)['skipped'] >= 1                     # waiting for qBit to list it
    out = rec.run(ts=[other], now=NOW + ir.LOST_AFTER_S + 1)                    # still missing after the re-add
    assert out['lost'] == 1 and len(sent) == 1 and 'even after a re-add' in _grab_state(H2)['error']


def test_lost_movie_grab_is_failed_and_researched(rec, monkeypatch):
    movie_grabs.record(3082, 'Heat.1995', H2, now=NOW - 3600)
    calls = []
    monkeypatch.setattr('app.movie_scanner.search_movie', lambda tid, origin='auto', now=None: calls.append(tid) or {'ok': True})
    other = {'hash': H3, 'name': 'x', 'category': 'radarr', 'tags': '', 'progress': 1, 'state': 'stalledUP'}
    assert rec.run(ts=[other])['lost'] == 1
    assert movie_grabs.by_hash(H2)['state'] == 'failed' and calls == [3082]


def test_grab_cache_roundtrip(rec):
    from app import grab_cache
    assert grab_cache.save(H2, 'torrent', b'd4:infod4:name1:xee')
    assert grab_cache.load(H2) == ('torrent', b'd4:infod4:name1:xee')
    assert grab_cache.load(H3) is None and not grab_cache.save('nothex', 'torrent', b'x')


# ------------------------------------------------------------------ acceptance: Eternally Yours snapshot replay

SNAP = '/workspace/luma-qa/missed-imports/eternally-yours/eternally-yours-20261030-200807'
EY = '7f3aecf2507e105f89a70f3dbe0a27253eaa7a00'


@pytest.mark.skipif(not os.path.isfile(os.path.join(SNAP, 'tv.db')), reason='Eternally Yours snapshot not on this box')
def test_acceptance_eternally_yours_snapshot_gives_lost_and_is_researched_in_one_cycle(rec, tmp_path, monkeypatch):
    """qBit .72 took the add (12:16:24 AM ET) and hard-restarted at 12:16:39 before writing its resume data:
    the torrent is gone, the grab row stayed 'downloading'. Replay the real tv.db and qBit's torrent list."""
    import shutil
    db = tmp_path / 'replay' / 'tv.db'
    db.parent.mkdir()
    shutil.copy(os.path.join(SNAP, 'tv.db'), db)
    monkeypatch.setitem(tv_store._cfg, 'path', str(db))
    with open(os.path.join(SNAP, 'qbit_fastresume_72.json')) as f:
        snap = json.load(f)
    hashes = _snap_hashes(snap)
    assert EY not in hashes and len(hashes) > 10
    qbit = [{'hash': h, 'name': h, 'category': 'other', 'tags': '', 'progress': 1, 'state': 'stalledUP'} for h in hashes]
    assert _grab_state(EY)['state'] == 'downloading'
    searched = []
    monkeypatch.setattr('app.tv_search.search_show', lambda sid, ids=None, **k: searched.append((sid, ids)) or
                        {'ok': True, 'picks': [{'grabbed': True}]})
    now = time.time()
    out = ir.tick(now=now, fetch=lambda: (qbit, None))
    assert out['lost'] >= 1
    assert _grab_state(EY)['state'] == 'failed' and _grab_state(EY)['error'].startswith('lost:')
    assert (147, [8453]) in searched
    assert ir.keys(EY)[0]['decision'] == 'lost'


def _snap_hashes(snap):
    out = set()

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if isinstance(k, str) and re.fullmatch(r'[0-9a-f]{40}', k.lower()):
                    out.add(k.lower())
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            m = re.fullmatch(r'(?:.*/)?([0-9a-f]{40})(?:\.fastresume|\.torrent)?', v.lower())
            if m:
                out.add(m.group(1))
    walk(snap)
    return out
