"""tv-phase0 mover hardening: Reviewer's probes (/workspace/fm1/probe) flipped
to the fixed behavior, plus the Tester items not covered by
tests/test_qa_fm_82a1404.py."""

import errno
import json
import os
import stat

import pytest

from app import file_management as fm
from app import library_mover as lm
from app import qbit_connector
from tests.test_library_mover import (env, add_path, torrent, movie_file, job, Resp, TMDB, H1, H2)  # noqa: F401
from tests.test_file_management import store, media, minimal, HDR, API, MAPPING  # noqa: F401
from tests.test_home_rails import REMOTE, _client

MOVIE = ('Movies', 'Heat (1995)', 'Heat (1995).mkv')


def _dst(env, *parts):
    return env.media.joinpath(*(parts or MOVIE))


# ---------------------------------------------------------- source bound to the downloads root (P1, P17)

def test_P1_save_path_outside_the_downloads_root_is_never_read(env, tmp_path):
    add_path('Movies', env.media / 'Movies')
    elsewhere = tmp_path / 'not-downloads' / 'private'
    elsewhere.mkdir(parents=True)
    (elsewhere / 'home-video.mkv').write_bytes(b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995.1080p')
    torrent(env, H1, 'home-video.mkv', save=elsewhere)
    env.tick(now=1000)
    assert job(H1)['state'] == 'waiting' and job(H1)['pending_since'] == 1000
    assert not any(os.scandir(env.media / 'Movies'))


def test_an_unmapped_save_path_is_flagged_at_once_and_listed(env, tmp_path):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    t = torrent(env, H1, 'Heat.1995.mkv')
    t['save_path'] = '/qbit-only/downloads/'
    t['content_path'] = '/qbit-only/downloads/Heat.1995.mkv'
    env.tick(now=1000)
    assert job(H1)['state'] == 'waiting' and job(H1)['status'] == lm.PATH_NOT_MAPPED
    env.tick(now=1000 + lm.NOT_FOUND_AFTER_S)
    assert job(H1)['status'] == lm.PATH_NOT_MAPPED          # never turns into "files not found"
    assert fm.view()['not_found'] == [{'title': 'Heat', 'since': 1000, 'reason': 'not_mapped'}]
    # the map fixes it: filed, flag cleared
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    fm.update(lambda d: d.__setitem__('path_map', [{'remote': '/qbit-only/downloads', 'local': str(env.dl)}]))
    env.tick(now=9000)
    assert job(H1)['state'] == 'done' and job(H1)['status'] == '' and job(H1)['pending_since'] is None
    assert fm.view()['not_found'] == []


def test_files_still_downloading_are_not_flagged(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995/Heat.1995.mkv.!qB', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995')
    env.tick(now=1000)
    env.tick(now=1000 + 10 * lm.NOT_FOUND_AFTER_S)
    assert job(H1)['state'] == 'waiting' and not job(H1).get('status') and job(H1).get('pending_since') is None


def test_content_outside_the_root_with_the_save_path_inside_is_refused(env, tmp_path):
    add_path('Movies', env.media / 'Movies')
    out = tmp_path / 'elsewhere'
    out.mkdir()
    (out / 'Heat.1995.mkv').write_bytes(b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    t = torrent(env, H1, 'Heat.1995.mkv')
    t['content_path'] = str(out / 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['state'] == 'skipped' and job(H1)['error'] == 'outside the downloads folder'


def test_missing_downloads_root_waits(env, monkeypatch, tmp_path):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(tmp_path / 'not-mounted'))
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['state'] == 'waiting'


def test_a_path_map_change_puts_path_skips_back_to_waiting(minimal, media, monkeypatch):
    c = _client(minimal)
    lib = media / 'Movies'
    assert c.post(API, json={'name': 'Movies', 'path': str(lib)}, environ_base=REMOTE, headers=HDR).status_code == 201

    def seed(d):
        d['jobs'][H1] = {'hash': H1, 'state': 'skipped', 'error': 'outside the downloads folder', 'attempts': 2,
                         'finished': 5, 'title': 'Heat', 'media_type': 'movie'}
        d['jobs'][H2] = {'hash': H2, 'state': 'skipped', 'error': 'insufficient space', 'finished': 5,
                         'title': 'Ronin', 'media_type': 'movie'}
    fm.update(seed)
    r = c.put(MAPPING, json={'path_map': [{'remote': '/data', 'local': str(media)}]}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()
    jobs = fm.load()['jobs']
    assert jobs[H1]['state'] == 'waiting' and jobs[H1]['attempts'] == 0 and 'finished' not in jobs[H1]
    assert jobs[H2]['state'] == 'skipped'
    # saving the same map again changes nothing
    fm.update(lambda d: d['jobs'][H1].update(state='skipped', error='outside the downloads folder'))
    c.put(MAPPING, json={'path_map': [{'remote': '/data', 'local': str(media)}]}, environ_base=REMOTE, headers=HDR)
    assert fm.load()['jobs'][H1]['state'] == 'skipped'


# ---------------------------------------------------------- library paths vs downloads (L1)

@pytest.mark.parametrize('rel', ['', 'lib', 'lib/deeper'])
def test_library_path_in_or_equal_to_downloads_is_refused(tmp_path, monkeypatch, rel):
    dl = tmp_path / 'downloads'
    (dl / 'lib' / 'deeper').mkdir(parents=True)
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(dl))
    assert fm.check_library_path(str(dl / rel) if rel else str(dl), []) == (None, fm.IN_DOWNLOADS)


def test_library_path_holding_downloads_is_refused(tmp_path, monkeypatch):
    (tmp_path / 'all' / 'downloads').mkdir(parents=True)
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(tmp_path / 'all' / 'downloads'))
    assert fm.check_library_path(str(tmp_path / 'all'), []) == (None, fm.IN_DOWNLOADS)
    (tmp_path / 'lib').mkdir()
    assert fm.check_library_path(str(tmp_path / 'lib'), [])[1] is None


def test_downloads_root_default_and_env(monkeypatch, tmp_path):
    monkeypatch.delenv('LUMA_DOWNLOADS_ROOT', raising=False)
    assert fm.downloads_root() == os.path.realpath('/downloads')
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', 'relative/path')
    assert fm.downloads_root() == os.path.realpath('/downloads')
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(tmp_path))
    assert fm.downloads_root() == os.path.realpath(tmp_path)


# ---------------------------------------------------------- source open (P5)

def test_P5_open_source_refuses_a_symlink(env, tmp_path):
    secret = tmp_path / 'secret'
    secret.write_bytes(b'S')
    os.symlink(secret, env.dl / 'link.mkv')
    with pytest.raises(lm.Refused):
        lm._open_source(str(env.dl / 'link.mkv'), os.path.realpath(env.dl))


def test_open_source_refuses_a_file_outside_the_root(env, tmp_path):
    (tmp_path / 'x.mkv').write_bytes(b'x')
    with pytest.raises(lm.Refused):
        lm._open_source(str(tmp_path / 'x.mkv'), os.path.realpath(env.dl))


def test_open_source_refuses_a_swap_between_check_and_open(env, monkeypatch):
    src = movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    real_open = os.open

    def swapping_open(path, flags, *a, **k):
        if path == str(src):
            os.rename(src, str(src) + '.old')    # keeps the inode alive, so it can't be reused
            src.write_bytes(b'y' * 200)          # a different inode under the same name
        return real_open(path, flags, *a, **k)
    monkeypatch.setattr(lm.os, 'open', swapping_open)
    with pytest.raises(lm.Refused):
        lm._open_source(str(src), os.path.realpath(env.dl))


def test_open_source_never_opens_a_fifo(env):
    os.mkfifo(env.dl / 'pipe.mkv')
    with pytest.raises(lm.Refused):
        lm._open_source(str(env.dl / 'pipe.mkv'), os.path.realpath(env.dl))


def test_hardlink_links_the_checked_inode(env):
    add_path('Movies', env.media / 'Movies')
    fm.update(lambda d: d.update(transfer='hardlink'))
    src = movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert _dst(env).stat().st_ino == src.stat().st_ino
    assert job(H1)['files'][0]['result'] == 'linked'


# ---------------------------------------------------------- destination (P8, P9, RENAME_NOREPLACE)

def test_P8_finish_never_follows_a_symlink(tmp_path):
    victim = tmp_path / 'victim'
    victim.write_bytes(b'v')
    os.chmod(victim, 0o600)
    os.symlink(victim, tmp_path / 'link')
    lm._finish(str(tmp_path / 'link'), 0o664, (12345, 12345))
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600


def test_P9_root_chown_uses_descriptors_only(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    calls = []
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    monkeypatch.setattr(os, 'fchown', lambda fd, u, g: calls.append((os.readlink(f'/proc/self/fd/{fd}'), u, g)))
    st = os.stat(env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert len(calls) == 2 and all(c[1:] == (st.st_uid, st.st_gid) for c in calls), calls
    assert calls[0][0].endswith('Heat (1995)') and calls[1][0].endswith('.luma-partial')


def test_rename_noreplace_never_replaces(tmp_path):
    (tmp_path / 'a').write_bytes(b'new')
    (tmp_path / 'b').write_bytes(b'old')
    dfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert lm._rename_noreplace(dfd, 'a', 'b') is False
        assert (tmp_path / 'b').read_bytes() == b'old' and (tmp_path / 'a').exists()
        assert lm._rename_noreplace(dfd, 'a', 'c') is True
        assert (tmp_path / 'c').read_bytes() == b'new' and not (tmp_path / 'a').exists()
    finally:
        os.close(dfd)


def test_rename_noreplace_fallback_without_renameat2(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, '_renameat2', False)
    (tmp_path / 'a').write_bytes(b'new')
    (tmp_path / 'b').write_bytes(b'old')
    dfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert lm._rename_noreplace(dfd, 'a', 'b') is False
        assert lm._rename_noreplace(dfd, 'a', 'c') is True
    finally:
        os.close(dfd)


def test_a_file_appearing_before_the_final_rename_is_kept(env, monkeypatch):
    """No links (CIFS) and someone else's file lands at the name mid-copy."""
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)

    def no_links(*a, **k):
        raise OSError(errno.EPERM, 'no links here')
    monkeypatch.setattr(lm.os, 'link', no_links)
    real = lm._paced_copy

    def copy_then_race(src, dst, exclusive=False):
        n = real(src, dst, exclusive=exclusive)
        _dst(env).write_bytes(b'theirs')
        return n
    monkeypatch.setattr(lm, '_paced_copy', copy_then_race)
    real_exists = lm._lexists_at
    seen = []

    def lexists(dfd, name):            # the pre-rename check misses it (a race)
        seen.append(name)
        return False if len(seen) == 2 else real_exists(dfd, name)
    monkeypatch.setattr(lm, '_lexists_at', lexists)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert _dst(env).read_bytes() == b'theirs'
    assert job(H1)['state'] == 'exists'
    assert os.listdir(_dst(env).parent) == ['Heat (1995).mkv']


# ---------------------------------------------------------- NoSubfolder (P13)

def test_P13_nosubfolder_files_only_listed_files(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    movie_file(env, 'Someone.Elses.2001.mkv', b'o' * 900)
    lm.track(H1, TMDB, 'Heat.1995')
    t = torrent(env, H1, 'x')
    t['content_path'] = str(env.dl)
    env.q.files[H1] = [{'name': 'Heat.1995.mkv', 'priority': 1}, {'name': 'Heat.1995.nfo', 'priority': 1}]
    env.tick()
    assert job(H1)['state'] == 'done'
    assert os.listdir(_dst(env).parent) == ['Heat (1995).mkv']
    assert any(c[1].endswith('/api/v2/torrents/files') and c[2] == {'hash': H1} for c in env.q.calls)


def test_nosubfolder_without_a_file_list_waits(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    t = torrent(env, H1, 'x')
    t['content_path'] = str(env.dl)
    env.tick()                                 # q.files has nothing: qBit answered null
    assert job(H1)['state'] == 'waiting'


@pytest.mark.parametrize('bad', ['../escape.mkv', '/etc/passwd', 'a/../../b.mkv', ''])
def test_nosubfolder_unsafe_names_are_refused(env, bad):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    t = torrent(env, H1, 'x')
    t['content_path'] = str(env.dl)
    env.q.files[H1] = [{'name': 'Heat.1995.mkv', 'priority': 1}, {'name': bad, 'priority': 1}]
    env.tick()
    assert job(H1)['state'] == 'skipped' and job(H1)['error'] == 'unsafe file name'
    assert not any(os.scandir(env.media / 'Movies'))


def test_nosubfolder_skips_files_not_downloaded(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.CD1.mkv', b'1' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    t = torrent(env, H1, 'x')
    t['content_path'] = str(env.dl)
    env.q.files[H1] = [{'name': 'Heat.1995.CD1.mkv', 'priority': 1}, {'name': 'Heat.1995.CD2.mkv', 'priority': 0}]
    env.tick()
    assert job(H1)['state'] == 'done' and os.listdir(_dst(env).parent) == ['Heat (1995).mkv']


# ---------------------------------------------------------- cleanup after a failed pass

def test_hardlink_mode_insufficient_space_leaves_no_empty_folder(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    fm.update(lambda d: d.update(transfer='hardlink'))
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    real_link = os.link

    def link(a, b, *args, **kw):
        if str(a).startswith('/proc/self/fd/'):
            raise OSError(errno.EXDEV, 'cross-device')
        return real_link(a, b, *args, **kw)
    monkeypatch.setattr(lm.os, 'link', link)
    monkeypatch.setattr(lm, 'free_bytes', lambda p: 0)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['state'] == 'failed' and job(H1)['error'] == 'insufficient space'
    assert not any(os.scandir(env.media / 'Movies'))


def test_a_failed_second_part_removes_the_first(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995/Heat.1995.CD1.mkv', b'1' * 100)
    movie_file(env, 'Heat.1995/Heat.1995.CD2.mkv', b'2' * 90)
    real = lm._paced_copy
    n = []

    def second_fails(src, dst, exclusive=False):
        n.append(dst)
        if len(n) == 2:
            raise OSError(errno.EIO, 'I/O error')
        return real(src, dst, exclusive=exclusive)
    monkeypatch.setattr(lm, '_paced_copy', second_fails)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995')
    env.tick()
    assert job(H1)['state'] == 'waiting' and job(H1)['attempts'] == 1
    assert not (env.media / 'Movies' / 'Heat (1995)').exists()
    monkeypatch.setattr(lm, '_paced_copy', real)
    env.tick()
    assert sorted(os.listdir(env.media / 'Movies' / 'Heat (1995)')) == ['Heat (1995) - pt1.mkv', 'Heat (1995) - pt2.mkv']


def test_a_failed_pass_keeps_a_folder_it_did_not_make(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    folder = env.media / 'Movies' / 'Heat (1995)'
    folder.mkdir()
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    monkeypatch.setattr(lm, '_paced_copy', lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EIO, 'x')))
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert folder.is_dir() and os.listdir(folder) == []


# ---------------------------------------------------------- one bad job never stops the pass

def test_an_unexpected_error_in_one_job_does_not_stop_the_others(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    movie_file(env, 'Ronin.1998.mkv', b'r' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    lm.track(H2, {'tmdb_id': 8195, 'media_type': 'movie', 'title': 'Ronin', 'year': 1998}, 'Ronin.1998')
    torrent(env, H1, 'Heat.1995.mkv')
    torrent(env, H2, 'Ronin.1998.mkv')
    real = lm.process

    def flaky(job_, t, data, now, list_files=None):
        if job_['hash'] == H1:
            raise ValueError('boom')
        return real(job_, t, data, now, list_files)
    monkeypatch.setattr(lm, 'process', flaky)
    out = env.tick()
    assert out['filed'] == 1 and job(H2)['state'] == 'done'
    assert job(H1)['state'] == 'waiting' and job(H1)['attempts'] == 1 and job(H1)['error'] == 'ValueError'


# ---------------------------------------------------------- sign-in hold persisted (F1, P16)

def _fails(log):
    class S:
        def post(self, url, data=None, timeout=None, **kw):
            log.append(url)
            return Resp(200, 'Fails.')

        def get(self, *a, **k):
            return Resp(data=[])

        def close(self):
            pass
    return S


def test_P16_hold_survives_restart_and_is_on_disk_0600(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'x')
    log = []
    for i in range(40):
        lm.tick(now=i * 100, session_factory=_fails(log))
    assert len(log) == 3
    path = lm._login_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rec = json.loads(path.read_text())
    assert rec['held'] is True and rec['failures'] == 3
    assert set(rec) == {'failures', 'next_at', 'held', 'creds'} and len(rec['creds']) == 64
    lm.init(settings=env.settings)
    for i in range(40):
        lm.tick(now=10000 + i * 100, session_factory=_fails(log))
    assert len(log) == 3


def test_new_credentials_start_fresh(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'x')
    log = []
    for i in range(40):
        lm.tick(now=i * 100, session_factory=_fails(log))
    env.settings.v['qbittorrent.password'] = 'new'
    lm.tick(now=5000, session_factory=_fails(log))
    assert len(log) == 4


def test_a_test_with_the_saved_credentials_releases_the_hold(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'x')
    log = []
    for i in range(40):
        lm.tick(now=i * 100, session_factory=_fails(log))
    qbit_connector._changed()                     # a Save: still held
    lm.tick(now=5000, session_factory=_fails(log))
    assert len(log) == 3
    qbit_connector._verified()                    # a successful Test of the saved credentials
    lm.tick(now=5100, session_factory=_fails(log))
    assert len(log) == 4


def test_is_saved_compares_the_tested_credentials(monkeypatch):
    class S:
        def get(self, k, d=None):
            return {'qbittorrent.url': 'http://qbit:8080', 'qbittorrent.username': 'u',
                    'qbittorrent.password': 'p'}.get(k, d)
    monkeypatch.setitem(qbit_connector._cfg, 'settings', S())
    assert qbit_connector._is_saved({'url': 'http://qbit:8080', 'username': 'u', 'password': 'p'})
    assert not qbit_connector._is_saved({'url': 'http://qbit:8080', 'username': 'u', 'password': 'other'})
    assert not qbit_connector._is_saved({'url': 'http://other:8080', 'username': 'u', 'password': 'p'})


def test_a_corrupt_login_file_starts_fresh(env):
    lm._login_path().parent.mkdir(parents=True, exist_ok=True)
    lm._login_path().write_text('{not json')
    lm.init(settings=env.settings)
    assert lm.login_state()['held'] is False


# ---------------------------------------------------------- manual cleanup route (F2)

def test_F2_cleanup_route_is_owner_only_and_needs_qbit(test_app, monkeypatch):
    from app import storage_cleanup
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    calls = []
    monkeypatch.setattr(storage_cleanup, 'run_cleanup',
                        lambda target, **kw: (calls.append((target, kw)), {'success': True})[1])
    owner = _client(test_app)
    other = _client(test_app, name='second')
    url = '/api/storage/downloads/cleanup'
    assert test_app.test_client().post(url, json={}, environ_base=REMOTE, headers=HDR).status_code == 401
    assert other.post(url, json={}, environ_base=REMOTE, headers=HDR).status_code == 403
    assert calls == []
    r = owner.post(url, json={'target_bytes': 5}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and calls == [(5, {'require_qbit': True})]
