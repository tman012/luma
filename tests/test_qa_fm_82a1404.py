"""QA gate 17ea918 / 82a1404 (file management, Movies): Tester regressions on top of
Builder's suite and Reviewer's probes (/workspace/fm1/probe). The bugs these
were written against (B2, B3, B5, B6, L1, F1, F2, F3, item 5) are fixed in
tv-phase0, so every test here must pass."""

import errno
import os
import stat

import pytest

import app.main
from app import file_management as fm
from app import library_mover as lm
from app import user_auth
from tests.test_library_mover import (env, add_path, torrent, movie_file, job, Resp, TMDB, H1, H2, TAG)  # noqa: F401
from tests.test_file_management import store, media, minimal, HDR, API, RULES, MAPPING  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_auto_rename_test_install_guard import renamer, _t, _info, H_TEST  # noqa: F401

LAN = {'REMOTE_ADDR': '192.168.1.20'}


# (a) B1 -------------------------------------------------------------------
def test_a_prod_still_files_its_own_tracked_grab_even_if_tagged(renamer, monkeypatch):
    ar, r = renamer
    r.tracked_downloads = {H_TEST: _info(ar, H_TEST)}          # prod's own track_download (prod grabbed it)
    monkeypatch.setattr(r, 'get_torrent_info', lambda h: _t(h, tags='luma-test'))
    r.check_downloads()
    assert r.processed == [H_TEST]


# (b) ----------------------------------------------------------------------
def test_b_finish_on_a_symlink_never_chmods_the_target(tmp_path):
    victim = tmp_path / 'victim'
    victim.write_bytes(b'v')
    os.chmod(victim, 0o600)
    link = tmp_path / 'link'
    os.symlink(victim, link)
    lm._finish(str(link), 0o664, None)
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600


# (c) ----------------------------------------------------------------------
def test_c_source_swapped_to_symlink_after_check_is_not_copied(env, tmp_path, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    secret = tmp_path / 'secret.bin'
    secret.write_bytes(b'S' * 200)
    src = movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    real_check = lm._check_space

    def swap(folder, need):
        if folder.endswith('Heat (1995)'):
            os.unlink(src)
            os.symlink(secret, src)
        return real_check(folder, need)
    monkeypatch.setattr(lm, '_check_space', swap)
    env.tick()
    dst = env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv'
    assert not (dst.exists() and dst.read_bytes()[:1] == b'S')
    assert secret.read_bytes() == b'S' * 200


# (d) ----------------------------------------------------------------------
def test_d_failed_qbit_sign_in_backs_off(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'x')
    logins = []

    class S:
        def post(self, url, data=None, timeout=None, **kw):
            logins.append(url)
            return Resp(200, 'Fails.')

        def close(self):
            pass
    for i in range(10):                      # 5 minutes of passes
        lm.tick(now=1000 + i * 30, session_factory=S)
    assert len(logins) <= 4, len(logins)


# (e) ----------------------------------------------------------------------
def test_e_adding_the_path_map_files_on_the_next_pass(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    t = torrent(env, H1, 'Heat.1995.mkv')
    t['save_path'] = '/qbit-only/downloads/'
    t['content_path'] = '/qbit-only/downloads/Heat.1995.mkv'
    env.tick()
    fm.update(lambda d: d.__setitem__('path_map', [{'remote': '/qbit-only/downloads', 'local': str(env.dl)}]))
    env.tick()
    assert job(H1)['state'] == 'done', job(H1)
    assert (env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv').read_bytes() == b'x' * 200


# (f) ----------------------------------------------------------------------
def test_f_nosubfolder_layout_files_only_its_own_file(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.1080p.mkv', b'x' * 300)
    movie_file(env, 'Other.Movie.2001.1080p.mkv', b'o' * 900)   # someone else's download in the same folder
    lm.track(H1, TMDB, 'Heat.1995.1080p')
    t = torrent(env, H1, 'Heat.1995.1080p.mkv', name='Heat.1995.1080p')
    t['content_path'] = str(env.dl)                            # qBit reports the save path itself
    env.q.files[H1] = [{'name': 'Heat.1995.1080p.mkv', 'size': 300, 'priority': 1}]   # torrents/files
    env.tick()
    assert job(H1)['state'] == 'done', job(H1)
    copied = sorted(os.listdir(env.media / 'Movies' / 'Heat (1995)'))
    assert copied == ['Heat (1995).mkv']
    assert (env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv').read_bytes() == b'x' * 300


def test_f_single_file_torrent_files_correctly_and_ignores_neighbours(env):
    """The normal single-file layout (content_path is the file): only that file."""
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.1080p.mkv', b'x' * 300)
    movie_file(env, 'Other.Movie.2001.1080p.mkv', b'o' * 900)
    lm.track(H1, TMDB, 'Heat.1995.1080p')
    torrent(env, H1, 'Heat.1995.1080p.mkv')
    env.tick()
    assert job(H1)['state'] == 'done'
    assert sorted(os.listdir(env.media / 'Movies')) == ['Heat (1995)']
    assert os.listdir(env.media / 'Movies' / 'Heat (1995)') == ['Heat (1995).mkv']


# (g) owner matrix ----------------------------------------------------------
ROUTES = (('get', API, None), ('post', API, {'name': 'M', 'path': None}), ('put', RULES, {'rules': []}),
          ('put', MAPPING, {'transfer': 'hardlink'}), ('delete', API + '/p_0123abcd', None),
          ('put', API + '/p_0123abcd', {'name': 'x'}))


def _call(c, meth, url, body, media, env_base):
    if body and 'path' in body:
        body = {**body, 'path': str(media / 'Movies')}
    return getattr(c, meth)(url, json=body, environ_base=env_base, headers=HDR)


@pytest.mark.parametrize('mode,env_base', [('disabled_local', LAN), ('enabled', LAN), ('disabled_local', REMOTE), ('enabled', REMOTE)])
def test_g_anonymous_and_implicit_lan_get_401(minimal, media, mode, env_base):
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'security': {'auth_required': mode}})
    c = minimal.test_client()
    for meth, url, body in ROUTES:
        r = _call(c, meth, url, body, media, env_base)
        assert r.status_code == 401, (mode, meth, url, r.status_code)
    assert fm.load()['paths'] == []


@pytest.mark.parametrize('kind', ['second_admin', 'approved', 'plain', 'unapproved'])
def test_g_non_owners_get_403(minimal, media, kind):
    _client(minimal)                                   # the owner (first admin)
    u = user_auth.create_user(f'u_{kind}', 'Pw-12345678', is_admin=(kind == 'second_admin'))
    if kind == 'approved':
        user_auth.save_user_settings(u['id'], {'use_shared_server': True, 'plex_access_status': 'approved'})
    if kind == 'unapproved':
        user_auth.save_user_settings(u['id'], {'plex_access_status': 'pending'})
    c = minimal.test_client()
    c.set_cookie('luma_auth_session', user_auth.create_session(u['id']))
    for meth, url, body in ROUTES:
        for eb in (REMOTE, LAN):
            r = _call(c, meth, url, body, media, eb)
            assert r.status_code == 403, (kind, meth, url, r.status_code)
    assert fm.load()['paths'] == []


def test_g_owner_gets_200_and_no_store(minimal, media):
    c = _client(minimal)
    r = c.get(API, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and 'no-store' in r.headers.get('Cache-Control', '')
    r = c.post(API, json={'name': 'Movies', 'path': str(media / 'Movies')}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 201


def test_g_routes_are_404_on_prod_builds(test_app, store, monkeypatch, media):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    c = _client(test_app)
    assert c.get(API, environ_base=REMOTE, headers=HDR).status_code == 404


# QA extras ------------------------------------------------------------------
def test_library_path_inside_downloads_is_refused(minimal, media, tmp_path, monkeypatch):
    c = _client(minimal)
    dl = media / 'downloads'          # a writable downloads folder (box); :5002 mounts it read-only
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(dl))
    (dl / 'lib').mkdir(parents=True)
    app.main.settings.update({'qbittorrent': {'url': 'http://qbit.invalid:8080'}})
    r = c.post(API, json={'name': 'In DL', 'path': str(dl / 'lib')}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400


def test_first_enabled_match_wins_and_disabled_rules_are_skipped(env, monkeypatch):
    p4k = add_path('4K', env.media / 'Full Rips')
    pmov = add_path('Movies', env.media / 'Movies')
    pfb = add_path('Recent', env.media / 'Recently Released')
    fm.update(lambda d: d.update(rules=[
        {'id': 'r_00000001', 'enabled': False, 'path_id': p4k, 'released_within': None, 'resolution': ['2160p'], 'source': []},
        {'id': 'r_00000002', 'enabled': True, 'path_id': pmov, 'released_within': None, 'resolution': [], 'source': ['remux']},
        {'id': 'r_00000003', 'enabled': True, 'path_id': p4k, 'released_within': None, 'resolution': ['2160p'], 'source': []}],
        fallback_path_id=pfb))
    movie_file(env, 'Heat.1995.2160p.UHD.BluRay.REMUX.mkv', b'r' * 200)
    lm.track(H1, TMDB, 'Heat.1995.2160p.UHD.BluRay.REMUX')
    torrent(env, H1, 'Heat.1995.2160p.UHD.BluRay.REMUX.mkv')
    env.tick()
    assert job(H1)['path_id'] == pmov          # rule 1 off, rule 2 (remux) beats rule 3 (2160p)


def test_source_untouched_and_no_qbit_writes(env):
    add_path('Movies', env.media / 'Movies')
    src = movie_file(env, 'Heat.1995.1080p.mkv', b'x' * 300)
    before = os.stat(src)
    lm.track(H1, TMDB, 'Heat.1995.1080p')
    torrent(env, H1, 'Heat.1995.1080p.mkv')
    for i in range(3):
        env.tick()
    after = os.stat(src)
    assert (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns)
    assert all(c[1].endswith(('/api/v2/auth/login', '/api/v2/torrents/info')) for c in env.q.calls), env.q.calls


def test_idempotent_no_recopy_on_later_passes(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.1080p.mkv', b'x' * 300)
    lm.track(H1, TMDB, 'Heat.1995.1080p')
    torrent(env, H1, 'Heat.1995.1080p.mkv')
    env.tick()
    dst = env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv'
    st = os.stat(dst)
    n = len(env.q.calls)
    env.tick(); env.tick()
    assert os.stat(dst).st_mtime_ns == st.st_mtime_ns and len(env.q.calls) == n


def test_unicode_multi_file_names(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Café Déjà Vu 2025 1080p WEB-DL ✓/CD1 日本.mkv', b'1' * 200)
    movie_file(env, 'Café Déjà Vu 2025 1080p WEB-DL ✓/CD2 日本.mkv', b'2' * 200)
    movie_file(env, 'Café Déjà Vu 2025 1080p WEB-DL ✓/sample.mkv', b's' * 200)
    lm.track(H1, {'tmdb_id': 900002, 'media_type': 'movie', 'title': 'Café: Déjà Vu / Part "1"', 'year': 2025}, 'Café Déjà Vu 2025 1080p WEB-DL ✓')
    torrent(env, H1, 'Café Déjà Vu 2025 1080p WEB-DL ✓')
    env.tick()
    folder = env.media / 'Movies' / 'Café- Déjà Vu Part 1 (2025)'
    assert sorted(os.listdir(folder)) == ['Café- Déjà Vu Part 1 (2025) - pt1.mkv', 'Café- Déjà Vu Part 1 (2025) - pt2.mkv']
    assert (folder / 'Café- Déjà Vu Part 1 (2025) - pt1.mkv').read_bytes() == b'1' * 200


def test_real_enospc_on_a_full_folder_leaves_no_partial(env, monkeypatch):
    """free_bytes lies (CIFS-style), the write itself hits ENOSPC."""
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 300)
    monkeypatch.setattr(lm, 'free_bytes', lambda p: 10 ** 15)

    def full(src, dst, exclusive=False):
        os.write(dst, b'half')             # the mover hands over its partial's descriptor
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(lm, '_paced_copy', full)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    env.tick()
    assert job(H1)['state'] == 'skipped' and job(H1)['error'] == 'insufficient space'
    folder = env.media / 'Movies' / 'Heat (1995)'
    assert not folder.exists() or os.listdir(folder) == []


# 82a1404 ------------------------------------------------------------------
def test_backoff_stops_after_3_rejections_and_connector_save_releases(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'x')
    logins = []

    class S:
        def post(self, url, data=None, timeout=None, **kw):
            logins.append(now[0])
            return Resp(200, 'Fails.')

        def get(self, *a, **k):
            raise AssertionError('no torrents/info without a session')

        def close(self):
            pass
    now = [1000.0]
    for i in range(120):                       # one hour of 30 s passes
        now[0] = 1000.0 + i * 30
        lm.tick(now=now[0], session_factory=S)
    assert len(logins) == 3, logins
    assert logins[1] - logins[0] >= 60 and logins[2] - logins[1] >= 120, logins
    assert lm.login_state()['held'] is True
    lm.reset_login()                            # what a connector save/test triggers
    lm.tick(now=now[0] + 30, session_factory=S)
    assert len(logins) == 4


def test_guard_keeps_tagged_and_still_deletes_untagged(tmp_path, monkeypatch):
    from app import storage_cleanup, tracker_files
    from tests.test_downloads_guard_test_install import _entry, _t
    root = tmp_path / 'downloads'
    root.mkdir()
    monkeypatch.setattr(storage_cleanup, 'downloads_root', lambda: str(root))
    monkeypatch.setattr(storage_cleanup, '_forget_torrents', lambda names: 0)
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    _entry(root, 'Old.Prod.1999.mkv', size=4096)
    _entry(root, 'Old.Test.1998.mkv', size=4096)           # older than prod's, finished, tagged
    monkeypatch.setattr(tracker_files, 'fetch_torrents', lambda: [
        _t('Old.Prod.1999.mkv', '/downloads/Old.Prod.1999.mkv'),
        _t('Old.Test.1998.mkv', '/downloads/Old.Test.1998.mkv', tags='luma-test')])
    out = storage_cleanup.run_cleanup(target_bytes=1, require_qbit=True)
    assert out.get('success'), out
    assert (root / 'Old.Test.1998.mkv').exists()
    assert not (root / 'Old.Prod.1999.mkv').exists()


# Reviewer r2 (82a1404) ------------------------------------------------------
def _fails_session(log):
    class S:
        trust_env = True

        def post(self, url, data=None, timeout=None, **kw):
            log.append(url)
            return Resp(200, 'Fails.')

        def get(self, *a, **k):
            return Resp(data=[])

        def close(self):
            pass
    return S


def test_F1_rejected_sign_ins_total_at_most_3_across_restart_and_save(env):
    from app import qbit_connector
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'x')
    lm.reset_login()
    log = []
    for i in range(20):
        lm.tick(now=i * 100, session_factory=_fails_session(log))
    lm.init(settings=env.settings)                 # a restart
    for i in range(20):
        lm.tick(now=5000 + i * 100, session_factory=_fails_session(log))
    qbit_connector._changed()                      # Save with the same credentials
    for i in range(20):
        lm.tick(now=10000 + i * 100, session_factory=_fails_session(log))
    assert len(log) <= 3, len(log)


def _dl_entry(root, rel, age_h=100):
    import time as _time
    p = root / rel
    p.mkdir(parents=True)
    (p / 'f.mkv').write_bytes(b'x' * 1000)
    t = _time.time() - age_h * 3600
    os.utime(p / 'f.mkv', (t, t))
    os.utime(p, (t, t))
    return p


def test_F2_manual_cleanup_with_qbit_down_deletes_nothing(tmp_path, monkeypatch):
    from app import storage_cleanup as sc, tracker_files
    root = tmp_path / 'dl'
    root.mkdir()
    _dl_entry(root, 'Old.Test.Grab')
    monkeypatch.setattr(tracker_files, 'fetch_torrents', lambda *a, **k: None)
    monkeypatch.setattr(sc, 'downloads_root', lambda: str(root))
    monkeypatch.setattr(sc, '_forget_torrents', lambda names: 0)
    out = sc.run_cleanup(10)                        # the manual pill's call (no require_qbit)
    assert out.get('deleted_count', 0) == 0 and (root / 'Old.Test.Grab').exists(), out


def test_F2_cleanup_routes_and_guard_are_unreachable_in_minimal(minimal):
    from app import minimal as mn
    for p, m in (('/api/storage/downloads/cleanup', 'POST'), ('/api/storage/downloads', 'GET'),
                 ('/api/downloads-guard/run', 'POST'), ('/api/downloads-guard/preview', 'GET'),
                 ('/api/downloads-guard/status', 'GET')):
        assert not mn.path_allowed(p, m), p
    c = _client(minimal)
    assert c.post('/api/storage/downloads/cleanup', json={'target_bytes': 1}, environ_base=REMOTE, headers=HDR).status_code == 404
    assert c.post('/api/downloads-guard/run', json={}, environ_base=REMOTE, headers=HDR).status_code == 404


def test_F3_tagged_in_category_folder_does_not_protect_untagged_siblings(tmp_path, monkeypatch):
    from app import storage_cleanup as sc, tracker_files
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    root = tmp_path / 'dl'
    root.mkdir()
    _dl_entry(root, 'radarr/Test.Movie.2020')
    _dl_entry(root, 'radarr/Prod.Movie.2001')
    os.utime(root / 'radarr', (0, 0))
    torrents = [{'hash': '1' * 40, 'name': 'Prod.Movie.2001', 'content_path': '/downloads/radarr/Prod.Movie.2001', 'tags': '', 'category': 'radarr', 'progress': 1, 'state': 'stalledUP'},
                {'hash': '2' * 40, 'name': 'Test.Movie.2020', 'content_path': '/downloads/radarr/Test.Movie.2020', 'tags': 'luma-test', 'category': 'radarr', 'progress': 1, 'state': 'stalledUP'}]
    monkeypatch.setattr(tracker_files, 'fetch_torrents', lambda *a, **k: torrents)
    names = sc.luma_test_entry_names(torrents)
    assert 'Test.Movie.2020' in names
    assert 'radarr' not in names and 'downloads' not in names, sorted(names)


def test_item5_tag_luma_does_not_claim_prods_luma_category(tmp_path, monkeypatch):
    from app import qbit_owner, storage_cleanup as sc, tracker_files
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'luma')
    prod = {'hash': '1' * 40, 'name': 'Prod.Movie', 'content_path': '/downloads/Prod.Movie', 'tags': '', 'category': 'luma', 'progress': 1, 'state': 'stalledUP'}
    assert not qbit_owner.is_test_torrent(prod)
    root = tmp_path / 'dl'
    root.mkdir()
    _dl_entry(root, 'Prod.Movie')
    monkeypatch.setattr(tracker_files, 'fetch_torrents', lambda *a, **k: [prod])
    assert [c['name'] for c in sc.scan_candidates(str(root))] == ['Prod.Movie']
