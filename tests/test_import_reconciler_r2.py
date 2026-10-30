"""import-reconciler r2 (Ops, Reviewer, Tester follow-ups on 0a292585):
1 'exists' of the same file marks the grab and the ledger imported (no 5 requeues, no import_failed push);
2 the host mover command is quoted (spaces, parentheses, quotes) on every path that builds it;
3 a movie already in the library with no grab record is library_present (terminal no-op);
4 a lost hash is blocklisted before the re-search, and re-searches are capped per target;
5 an empty qBittorrent list is not 'reachable';
6 movie_scanner.grab caches the .torrent only after the dedupe check;
7 grabbed: false with grabs off says reason 'disabled'."""
import importlib.util
import json
import logging
import os
import shlex
import stat
import subprocess
import sys

import pytest

from app import file_management as fm, import_reconciler as ir, library_mover as lm
from app import movie_grabs, movie_import as mi, movie_settings, tv_store
from tests.test_import_reconciler import NOW, H3, HOST_500, _grab_state, _heat, _skip_job, _tv_grab_row, rec  # noqa: F401
from tests.test_library_mover import H1, H2, TMDB, add_path, env, job, movie_file, torrent  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OTHER = {'hash': H3, 'name': 'x', 'category': 'radarr', 'tags': '', 'progress': 1, 'state': 'stalledUP'}


# ------------------------------------------------------------------ 1: 'exists' of the same file = imported

def _requeued_exists(rec, data=None):  # noqa: F811
    """Smile 2 shape: filed once, then the job is requeued (skipped -> waiting) and the mover finds the file."""
    _heat(rec)
    movie_grabs.record(3082, 'Heat.1995', H1, now=NOW - 7200)
    movie_grabs.set_state(H1, 'failed', error=HOST_500, now=NOW - 3600)
    dest = rec.media / 'Movies' / 'Heat (1995)'
    dest.mkdir()
    src = rec.dl / 'Heat.1995.mkv'
    (dest / 'Heat (1995).mkv').write_bytes(src.read_bytes() if data is None else data)
    _skip_job(H1, HOST_500)
    def apply(d):
        lm._requeue(d['jobs'][H1])
    fm.update(apply)
    rec.tick()
    return dest / 'Heat (1995).mkv'


def test_exists_same_file_marks_grab_and_ledger_imported_without_push(rec, monkeypatch):
    from app import library_notify
    pushes = []
    monkeypatch.setattr(library_notify, '_push', lambda *a: pushes.append(a))
    monkeypatch.setattr(library_notify, 'notify_library_added', lambda **k: pushes.append(k))
    f = _requeued_exists(rec)
    assert job(H1)['state'] == 'exists' and job(H1)['files'][0]['match'] is True
    g = movie_grabs.by_hash(H1)
    assert g['state'] == 'imported' and g['path'] == str(f)
    assert {k['decision'] for k in ir.keys(H1)} == {'imported'}
    out = rec.run(now=NOW + 99 * 7200)                       # later passes: imported, never import_failed
    assert out['already_imported'] == 1 and out['import_failed'] == 0 and out['requeued'] == 0
    assert pushes == []


def test_exists_different_size_keeps_old_behavior_and_logs(rec, caplog):
    caplog.set_level(logging.WARNING, logger='app.library_mover')
    f = _requeued_exists(rec, data=b'other bytes')
    assert f.read_bytes() == b'other bytes'                  # never overwritten
    assert job(H1)['state'] == 'exists' and job(H1)['files'][0]['match'] is False
    assert movie_grabs.by_hash(H1)['state'] == 'failed'
    assert all(k['decision'] != 'imported' for k in ir.keys(H1))
    assert any('does not match the download' in r.message for r in caplog.records)


def test_same_size_different_bytes_is_caught_by_sha(rec):
    src_len = len((b'movie-bytes' * 10))
    _requeued_exists(rec, data=b'X' * src_len)
    assert job(H1)['files'][0]['match'] is False and movie_grabs.by_hash(H1)['state'] == 'failed'


def test_reconciler_settles_an_exists_job_left_from_before(rec):
    """An 'exists' job recorded by r2's mover whose grab row is still open: the pass marks it imported."""
    _heat(rec)
    movie_grabs.record(3082, 'Heat.1995', H1, now=NOW - 7200)
    def apply(d):
        d['jobs'][H1].update(state='exists', dest=str(rec.media / 'Movies' / 'Heat (1995)'),
                             files=[{'file': 'Heat (1995).mkv', 'result': 'exists', 'match': True}], finished=NOW - 60)
    fm.update(apply)
    assert rec.run()['already_imported'] == 1
    assert movie_grabs.by_hash(H1)['state'] == 'imported' and ir.keys(H1)[0]['decision'] == 'imported'


# ------------------------------------------------------------------ 2: the host mover command is quoted

NASTY = ["/media/Video/Movies/Smile 2 (2024)/Smile 2 (2024).mkv", "/scratch/downloads/It's \"Q\" $(id) `x` ; & |.mkv"]


def _host():
    spec = importlib.util.spec_from_file_location('luma_remote_mover_host', os.path.join(ROOT, 'scripts', 'luma-remote-mover-host.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_host_helper_local_argv_passes_paths_as_single_arguments(monkeypatch):
    h = _host()
    monkeypatch.setattr(h, 'SSH_TARGET', '')
    src, dest = NASTY[1], NASTY[0]
    assert h.build_cmd('cp', src, dest) == [h.CLI, 'cp', src, dest]


def test_host_helper_ssh_command_is_shell_quoted(monkeypatch):
    h = _host()
    monkeypatch.setattr(h, 'SSH_TARGET', 'u@host')
    tmp = '/media/Video/Movies/Smile 2 (2024)/.luma-0123456789abcdef.luma-partial'
    cmd = h.build_cmd('cp', NASTY[1], NASTY[0], tmp)
    assert cmd[0] == 'ssh' and cmd[-3:-1] == ['u@host', '--']
    assert shlex.split(cmd[-1]) == [h.REMOTE_CLI, 'cp', NASTY[1], NASTY[0], tmp]
    # what the remote shell does with that one string: each path is exactly one argument
    out = subprocess.run(['bash', '-c', 'f() { printf "%s\\n" "$@"; }; eval "f ${0#* }"', cmd[-1]],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert out == ['cp', NASTY[1], NASTY[0], tmp]


def test_host_cli_script_quotes_for_ssh(tmp_path):
    """scripts/luma-host-remote-mover.sh with a stub ssh that hands its last argument to bash, like sshd."""
    stub = tmp_path / 'bin'
    stub.mkdir()
    (stub / 'ssh').write_text('#!/usr/bin/env bash\nexec bash -c "${@: -1}"\n')
    (stub / 'ssh').chmod(0o755)
    remote = tmp_path / 'remote.sh'
    remote.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    remote.chmod(0o755)
    env = dict(os.environ, PATH=f'{stub}:{os.environ["PATH"]}', LUMA_REMOTE_MOVER_PATH=str(remote),
               LUMA_REMOTE_SSH_KEY='/dev/null', LUMA_REMOTE_SSH_HOST='download-host')
    tmp = '/media/Video/Movies/Smile 2 (2024)/.luma-0123456789abcdef.luma-partial'
    p = subprocess.run(['bash', os.path.join(ROOT, 'scripts', 'luma-host-remote-mover.sh'), 'cp', NASTY[1], NASTY[0], tmp],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == ['cp', NASTY[1], NASTY[0], tmp]


def test_remote_mover_sends_paths_as_json_fields_not_a_command(monkeypatch, tmp_path):
    """The container side never builds a shell string: src/dest/tmp go as JSON fields."""
    from app import remote_mover as rm
    sent = {}
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(tmp_path / 'dl'))
    monkeypatch.setattr(rm, '_dest_observable', lambda d: False)
    monkeypatch.setattr(rm, '_http_post_unix', lambda sock, path, body, timeout: sent.update(json.loads(body)) or
                        (200, '{"ok": true, "rc": 0}'))
    src = str(tmp_path / 'dl' / "Smile 2 2024 (it's).mkv")
    assert rm.transfer(src, NASTY[0]) == 'copied'
    assert sent['src'].endswith("Smile 2 2024 (it's).mkv") and sent['dest'] == NASTY[0]


# ------------------------------------------------------------------ 3: in the library, no grab record

def test_no_grab_record_but_in_library_is_library_present(rec, tmp_path, caplog):
    lib = tmp_path / 'Video' / 'Movies'
    (lib / 'Max Payne (2008)').mkdir(parents=True)
    (lib / 'Max Payne (2008)' / 'Max Payne (2008).mkv').write_bytes(b'x')
    assert mi.save_settings({'root': str(lib)}) == {}
    caplog.set_level(logging.INFO, logger='app.import_reconciler')
    ts = [{'hash': H2, 'name': 'Max.Payne.2008.1080p.BluRay.x264', 'category': 'movie', 'tags': '', 'progress': 1,
           'state': 'stalledUP'},
          {'hash': H3, 'name': 'Tombstones.2025.1080p.WEB', 'category': 'movie', 'tags': '', 'progress': 1,
           'state': 'stalledUP'}]
    before = fm.load()['jobs']
    out = rec.run(ts=ts)
    assert out['library_present'] == 1 and out['skipped'] == 1 and out['requeued'] == 0
    assert fm.load()['jobs'] == before and movie_grabs.by_hash(H2) is None      # nothing adopted
    assert any(f'library_present Max.Payne.2008.1080p.BluRay.x264 [{H2[:8]}]: no grab record; already in the library'
               in r.message for r in caplog.records)
    assert any(f'skipped Tombstones.2025.1080p.WEB [{H3[:8]}]: no grab record' in r.message for r in caplog.records)


# ------------------------------------------------------------------ 4: lost hash blocklisted, re-search capped

def test_lost_tv_hash_is_blocklisted_before_the_research(rec, monkeypatch):
    _tv_grab_row(H2, title='Show.S01E04.1080p.WEB-CAKES')
    seen = []

    def search(sid, ids=None, **k):
        with tv_store.connect() as conn:
            seen.append([dict(r) for r in conn.execute('SELECT series_id, info_hash, release_title, reason FROM blocklist')])
        return {'ok': True, 'picks': []}
    monkeypatch.setattr('app.tv_search.search_show', search)
    assert rec.run(ts=[OTHER])['lost'] == 1
    assert seen == [[{'series_id': 147, 'info_hash': H2, 'release_title': 'Show.S01E04.1080p.WEB-CAKES', 'reason': 'lost'}]]


def test_lost_movie_hash_is_blocklisted_before_the_research(rec, monkeypatch):
    movie_grabs.record(3082, 'Heat.1995.1080p', H2, now=NOW - 3600)
    seen = []

    def search(tid, origin='auto', now=None):
        with movie_settings.connect() as conn:
            seen.append([tuple(r) for r in conn.execute('SELECT tmdb_id, info_hash FROM movie_blocklist')])
        return {'ok': True}
    monkeypatch.setattr('app.movie_scanner.search_movie', search)
    assert rec.run(ts=[OTHER])['lost'] == 1
    assert seen == [[(3082, H2)]]


def test_researches_are_capped_per_target(rec, monkeypatch, caplog):
    """Each re-search picks a new hash that qBit loses again: at most MAX_RESEARCHES re-searches."""
    caplog.set_level(logging.INFO, logger='app.import_reconciler')
    calls = []
    monkeypatch.setattr('app.tv_search.search_show', lambda sid, ids=None, **k: calls.append(sid) or
                        {'ok': True, 'picks': [{'grabbed': True}]})
    for i in range(ir.MAX_RESEARCHES + 2):
        h = f'{i:x}' * 40
        h = h[:40]
        _tv_grab_row(h, at=NOW + i * 600 - 3600)
        assert rec.run(ts=[OTHER], now=NOW + i * 600)['lost'] == 1
    assert len(calls) == ir.MAX_RESEARCHES
    assert any(f're-search skipped (limit of {ir.MAX_RESEARCHES} reached)' in r.message for r in caplog.records)


def test_blocklisted_release_is_not_picked_again(rec, monkeypatch):
    """The TV grab path refuses the blocklisted lost hash, so a re-search can't revive it."""
    from app import tv_grab
    _tv_grab_row(H2, title='Show.S01E04.1080p.WEB-CAKES')
    monkeypatch.setattr('app.tv_search.search_show', lambda *a, **k: {'ok': True, 'picks': []})
    rec.run(ts=[OTHER])
    monkeypatch.setattr(tv_grab, 'allowed', lambda: True)
    monkeypatch.setattr('app.tv_safety.check', lambda *a, **k: None)
    g = tv_grab.grab({'title': 'Show.S01E04.1080p.WEB-CAKES', 'downloadUrl': 'x'}, 147, 1, [8453], 'auto', NOW,
                     fetch=lambda u: ('magnet', f'magnet:?xt=urn:btih:{H2}'))
    assert g == {'ok': False, 'error': 'Blocklisted'}


# ------------------------------------------------------------------ 5: an empty qBit list is not reachable

def test_empty_qbit_list_is_not_reachable(rec, caplog):
    caplog.set_level(logging.INFO, logger='app.import_reconciler')
    ir._last['reachable'] = True
    ir.tick(now=NOW, fetch=lambda: ([], None))
    assert ir._last['reachable'] is False
    _heat(rec)
    ir.tick(now=NOW + 1, fetch=lambda: (list(rec.q.torrents.values()), None))
    assert ir._last['reachable'] is True
    assert any('qBittorrent is reachable again' in r.message for r in caplog.records)


def test_loop_reconnect_check_uses_reachable_not_checked(monkeypatch):
    """start()'s loop: ok comes from _last['reachable'] only (a lost-grab 'checked' count on an empty list
    no longer reads as reachable)."""
    import inspect
    src = inspect.getsource(ir.start)
    assert "ok = bool(_last['reachable'])" in src and "out.get('checked', 0) > 0" not in src


# ------------------------------------------------------------------ 6: grab cache after the dedupe

def test_movie_grab_caches_only_after_the_dedupe(rec, monkeypatch):
    from app import grab_cache, instance, movie_dupes, movie_scanner
    monkeypatch.setattr(movie_scanner, 'on_test', lambda: True)
    monkeypatch.setattr(instance, 'writes_allowed', lambda job: True)
    monkeypatch.setattr(movie_dupes, 'check', lambda *a, **k: None)
    movie_grabs.record(999, 'Other.Movie.2020', H2, now=NOW)            # the same hash, active for another movie
    mag = f'magnet:?xt=urn:btih:{H2}'
    out = movie_scanner.grab({'title': 'Heat.1995', 'downloadUrl': 'x'}, 3082, 'auto', NOW,
                             fetch=lambda u: ('magnet', mag), movie={'title': 'Heat', 'year': 1995})
    assert out.get('existing') and grab_cache.load(H2) is None
    with movie_settings.connect() as conn:
        movie_scanner.ensure(conn)
        conn.execute('INSERT INTO movie_blocklist (tmdb_id, release_title, info_hash, added_at) VALUES (3082, ?, ?, 1)',
                     ('Heat.1995.B', H3))
    out = movie_scanner.grab({'title': 'Heat.1995.B', 'downloadUrl': 'x'}, 3082, 'auto', NOW,
                             fetch=lambda u: ('magnet', f'magnet:?xt=urn:btih:{H3}'), movie={'title': 'Heat', 'year': 1995})
    assert out == {'ok': False, 'error': 'Blocklisted'} and grab_cache.load(H3) is None


# ------------------------------------------------------------------ 7: grabbed: false with grabs off

def test_grabs_off_says_reason_disabled(monkeypatch):
    from app import tv_grab, tv_search
    monkeypatch.setattr(tv_grab, 'allowed', lambda: False)
    g = tv_grab.grab({'title': 't', 'downloadUrl': 'x'}, 1, 1, [1], 'manual', NOW)
    assert g['ok'] is False and g['grabbed'] is False and g['reason'] == 'disabled' and g['error'] == tv_grab.OFF
    assert tv_search.why_not_grabbed(g) == 'disabled'
    assert tv_search.why_not_grabbed({'ok': False, 'error': tv_grab.OFF, 'blocked': True}) == 'disabled'
    assert tv_search.why_not_grabbed({'ok': True, 'existing': True}) == 'existing'
    assert tv_search.why_not_grabbed({'ok': True, 'existing': True, 'claimed': True}) == 'existing'
    assert tv_search.why_not_grabbed({'ok': False, 'claimed': True}) == 'duplicate'
    assert tv_search.why_not_grabbed({'ok': False, 'error': 'HTTP 500'}) == 'send_failed'
