"""QA gate tv-phase0 23d6f30/78d7f1e (Tester): gaps on top of Builder's
test_mover_hardening / test_tv_foundations / test_qa_fm_82a1404. Box only."""

import json
import logging
import os

import pytest

import app.main
from app import file_management as fm
from app import library_mover as lm
from app import qbit_owner, section_trackers as st, tv_settings
from tests.test_library_mover import env, add_path, torrent, movie_file, job, TMDB, H1, H2  # noqa: F401
from tests.test_file_management import store, media, minimal, HDR, API, MAPPING  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_foundations import RECS, sec  # noqa: F401

H3 = 'd' * 40
URL = '/api/settings/sections/tv/trackers'
RONIN = {'tmdb_id': 8195, 'media_type': 'movie', 'title': 'Ronin', 'year': 1998}
MI = {'tmdb_id': 954, 'media_type': 'movie', 'title': 'Mission Impossible', 'year': 1996}


# ----------------------------------------------------------------- LUMA_DOWNLOADS_ROOT

def test_downloads_root_unset_defaults_to_slash_downloads(monkeypatch):
    monkeypatch.delenv('LUMA_DOWNLOADS_ROOT', raising=False)
    assert fm.downloads_root() == os.path.realpath('/downloads')
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', 'relative/dir')          # not absolute: default
    assert fm.downloads_root() == os.path.realpath('/downloads')


def test_downloads_root_missing_moves_nothing_and_flags_after_an_hour(env, monkeypatch, tmp_path, caplog):
    """Unset (default /downloads) and that folder absent: the mover waits,
    copies nothing, deletes nothing, and flags 'files not found' after an hour."""
    missing = tmp_path / 'no-such-downloads'
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(missing))
    add_path('Movies', env.media / 'Movies')
    src = movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    caplog.set_level(logging.WARNING, logger='app.library_mover')
    env.tick(now=1000)
    assert job(H1)['state'] == 'waiting' and job(H1)['pending_since'] == 1000
    env.tick(now=1000 + lm.NOT_FOUND_AFTER_S)
    assert job(H1)['status'] == lm.FILES_NOT_FOUND
    assert 'not found for 60 min (download folder not available)' in caplog.text
    assert fm.view()['not_found'] == [{'title': 'Heat', 'since': 1000}]
    assert src.read_bytes() == b'x' * 200 and not any(os.scandir(env.media / 'Movies'))


def test_downloads_root_slash_refuses_every_library_path(monkeypatch, tmp_path):
    """LUMA_DOWNLOADS_ROOT=/ (misconfig): fails safe, every library path overlaps it."""
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', '/')
    (tmp_path / 'lib').mkdir()
    assert fm.check_library_path(str(tmp_path / 'lib'), [])[1] == fm.IN_DOWNLOADS


def test_root_scoped_copy_lands_and_outside_save_path_is_never_read(env, tmp_path):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'h' * 200)
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    (outside / 'Ronin.1998.mkv').write_bytes(b'r' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    lm.track(H2, RONIN, 'Ronin.1998')
    torrent(env, H1, 'Heat.1995.mkv')
    torrent(env, H2, 'Ronin.1998.mkv', save=outside)
    env.tick(now=1000)
    assert job(H1)['state'] == 'done'
    dest = env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv'
    assert dest.read_bytes() == b'h' * 200 and oct(dest.stat().st_mode & 0o777) == '0o664'
    assert oct((env.media / 'Movies' / 'Heat (1995)').stat().st_mode & 0o777) == '0o775'
    assert job(H2)['state'] == 'waiting' and not (env.media / 'Movies' / 'Ronin (1998)').exists()


@pytest.mark.parametrize('rel', ['', 'lib', 'a/b'])
def test_library_path_in_or_equal_to_downloads_is_refused_via_api(minimal, media, tmp_path, monkeypatch, rel):
    dl = media / 'dlroot'
    (dl / rel).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(dl))
    c = _client(minimal)
    r = c.post(API, json={'name': 'X', 'path': str(dl / rel)}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and fm.IN_DOWNLOADS in json.dumps(r.get_json())
    r = c.post(API, json={'name': 'Parent', 'path': str(media)}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and fm.IN_DOWNLOADS in json.dumps(r.get_json())   # holds downloads


# ----------------------------------------------------------------- B5 via the route

def test_B5_path_map_put_files_a_flagged_grab_next_pass(minimal, env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    t = torrent(env, H1, 'Heat.1995.mkv')
    t['save_path'], t['content_path'] = '/qbit/dl/', '/qbit/dl/Heat.1995.mkv'
    env.tick(now=1000)
    env.tick(now=1000 + lm.NOT_FOUND_AFTER_S)
    assert job(H1)['status'] == lm.FILES_NOT_FOUND
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    c = _client(minimal)
    r = c.put(MAPPING, json={'path_map': [{'remote': '/qbit/dl', 'local': str(env.dl)}], 'transfer': 'copy'},
              environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()
    env.tick(now=1000 + lm.NOT_FOUND_AFTER_S + 30)
    assert job(H1)['state'] == 'done' and job(H1)['status'] == ''


# ----------------------------------------------------------------- B6 multi-file NoSubfolder

def test_B6_nosubfolder_multi_part_files_only_listed_parts(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Mission.Impossible.1996.CD1.mkv', b'1' * 300)
    movie_file(env, 'Mission.Impossible.1996.CD2.mkv', b'2' * 300)
    movie_file(env, 'Some.Other.Movie.2001.mkv', b'o' * 900)          # a neighbour, not this torrent's
    lm.track(H1, MI, 'Mission.Impossible.1996')
    t = torrent(env, H1, 'x', name='Mission.Impossible.1996')
    t['content_path'] = str(env.dl)
    env.q.files[H1] = [{'name': 'Mission.Impossible.1996.CD1.mkv', 'size': 300, 'priority': 1},
                       {'name': 'Mission.Impossible.1996.CD2.mkv', 'size': 300, 'priority': 1}]
    env.tick()
    assert job(H1)['state'] == 'done', job(H1)
    got = sorted(os.listdir(env.media / 'Movies' / 'Mission Impossible (1996)'))
    assert len(got) == 2 and all('Other' not in g for g in got), got
    assert sum(os.path.getsize(env.media / 'Movies' / 'Mission Impossible (1996)' / g) for g in got) == 600


# ----------------------------------------------------------------- per-job isolation

def test_one_oserror_and_one_crash_do_not_stop_a_third_job(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    for n in ('Heat.1995.mkv', 'Ronin.1998.mkv', 'Mission.Impossible.1996.mkv'):
        movie_file(env, n, b'z' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    lm.track(H2, RONIN, 'Ronin.1998')
    lm.track(H3, MI, 'Mission.Impossible.1996')
    torrent(env, H1, 'Heat.1995.mkv')
    torrent(env, H2, 'Ronin.1998.mkv')
    torrent(env, H3, 'Mission.Impossible.1996.mkv')
    real = lm.process

    def bad(job_, t, data, now, list_files=None):
        if job_['hash'] == H1:
            raise PermissionError(13, 'denied')
        if job_['hash'] == H2:
            raise KeyError('boom')
        return real(job_, t, data, now, list_files)
    monkeypatch.setattr(lm, 'process', bad)
    assert env.tick()['filed'] == 1
    assert job(H3)['state'] == 'done'
    assert job(H1)['error'] == 'EACCES' and job(H2)['error'] == 'KeyError'


# ----------------------------------------------------------------- tag-only (item 5 on the mover)

def test_tag_luma_falls_back_and_the_mover_drops_prod_luma_category(env, monkeypatch):
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'luma')
    assert qbit_owner.tag_name() == 'luma-test'
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv', tags='', category='luma')
    env.tick()
    assert job(H1) is None and not any(os.scandir(env.media / 'Movies'))


# ----------------------------------------------------------------- 3xx sign-in hold persists

def test_3xx_sign_in_holds_after_3_even_across_restart(env):
    from tests.test_library_mover import Resp
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'x')
    lm.reset_login()
    posts = []

    class S:
        def post(self, url, data=None, timeout=None, allow_redirects=True, **kw):
            assert allow_redirects is False
            posts.append(url)
            r = Resp(302, '')
            r.headers = {'Location': 'http://evil.invalid/steal'}
            return r

        def get(self, url, **kw):
            raise AssertionError('never follows: ' + url)

        def close(self):
            pass
    for i in range(30):
        lm.tick(now=i * 1000, session_factory=S)
    lm.init(settings=env.settings)                       # restart
    for i in range(30):
        lm.tick(now=100000 + i * 1000, session_factory=S)
    assert len(posts) == 3 and all('evil' not in p for p in posts)


# ----------------------------------------------------------------- TV trackers

def test_api_refuses_enabling_an_unusable_tracker(minimal, sec):
    """Delta is off in Settings > Trackers, Charlie has no TV category."""
    c = _client(minimal)
    body = {'trackers': [{'id': 4, 'enabled': True}, {'id': 3, 'enabled': True}, {'id': 1, 'enabled': False},
                         {'id': 2, 'enabled': False}]}
    r = c.put(URL, json=body, environ_base=REMOTE, headers=HDR)
    assert st.ordered_ids('tv') == []                       # never searched either way
    # QA-TV1 fixed: switching on an unusable tracker is refused and nothing is stored.
    assert r.status_code == 400, r.get_json()
    assert r.get_json() == {'error': 'Delta is off in Trackers'}
    assert st.load()['sections']['tv'] == {'order': [], 'enabled': []}


def test_put_without_origin_is_refused(minimal, sec):
    c = _client(minimal)
    r = c.put(URL, json={'trackers': [{'id': 1, 'enabled': True}]}, environ_base=REMOTE)
    assert r.status_code in (400, 403), r.status_code
    assert st.ordered_ids('tv') == []


def test_turning_tv_automation_on_sends_no_tracker_request(minimal, sec, tmp_path, monkeypatch):
    import requests
    monkeypatch.setitem(tv_settings._cfg, 'store_path', str(tmp_path / 'tvs' / tv_settings.STORE_NAME))
    sent = []
    real = requests.Session.request

    def spy(self, method, url, *a, **k):
        sent.append(url)
        raise AssertionError('no outbound request expected: ' + url)
    monkeypatch.setattr(requests.Session, 'request', spy)
    c = _client(minimal)
    r = c.put(URL, json={'trackers': [{'id': 1, 'enabled': True}, {'id': 2, 'enabled': True}]},
              environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    r = c.put('/api/settings/tv', json={'automation': {'enabled': True, 'feed_enabled': True,
                                                         'backlog_enabled': True, 'upgrades_enabled': True}},
              environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()
    assert c.get('/api/settings/tv', environ_base=REMOTE, headers=HDR).status_code == 200
    assert sent == []
    assert real


def test_no_production_code_calls_the_tv_purpose_yet():
    import pathlib
    import re
    root = pathlib.Path(app.main.__file__).parent
    hits = []
    for p in root.rglob('*.py'):
        if p.name in ('tv_budget.py', 'tracker_guard.py'):
            continue
        s = p.read_text()
        if re.search(r"tv_budget\.take\(|purpose\s*=\s*['\"]tv['\"]|purpose=tv_budget\.PURPOSE", s):
            hits.append(p.name)
    assert hits == [], hits


# ----------------------------------------------------------------- swaps after the scan (B3 widened)

def _swap_after_scan(env, monkeypatch, swap):
    add_path('Movies', env.media / 'Movies')
    src = movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    real = lm.find_video_files

    def scan_then_swap(*a, **k):
        out = real(*a, **k)
        swap(src)
        return out
    monkeypatch.setattr(lm, 'find_video_files', scan_then_swap)
    env.tick()
    return env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv'


def test_B3_symlink_swapped_in_after_the_scan_is_never_copied(env, monkeypatch, tmp_path):
    secret = tmp_path / 'secret.bin'
    secret.write_bytes(b'S' * 200)

    def swap(src):
        os.rename(src, str(src) + '.old')
        os.symlink(secret, src)
    dst = _swap_after_scan(env, monkeypatch, swap)
    assert not dst.exists() or dst.read_bytes() != b'S' * 200
    assert job(H1)['state'] == 'skipped' and 'symlink' in job(H1)['error'] or 'regular' in job(H1)['error'], job(H1)


def test_B3_different_file_swapped_in_after_the_scan(env, monkeypatch):
    """Same name, new inode and size after the scan picked the file."""
    def swap(src):
        os.rename(src, str(src) + '.old')
        src.write_bytes(b'Y' * 999)
    dst = _swap_after_scan(env, monkeypatch, swap)
    # QA-MV1 fixed: the open file must be the one the scan saw (device, inode, size).
    assert not dst.exists()
    assert job(H1)['state'] == 'waiting', job(H1)          # Pending(SOURCE_CHANGED): the next pass scans again


# ----------------------------------------------------------------- downloads root returns

def test_missing_root_waits_flags_then_recovers_when_it_returns(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    away = str(env.dl) + '.unmounted'
    os.rename(env.dl, away)                                  # the mount is gone
    env.tick(now=1000)
    env.tick(now=1000 + lm.NOT_FOUND_AFTER_S)
    assert job(H1)['state'] == 'waiting' and job(H1)['status'] == lm.FILES_NOT_FOUND
    assert not any(os.scandir(env.media / 'Movies'))
    os.rename(away, env.dl)                                  # back
    env.tick(now=1000 + lm.NOT_FOUND_AFTER_S + 30)
    assert job(H1)['state'] == 'done' and job(H1)['status'] == '' and fm.view()['not_found'] == []


# ----------------------------------------------------------------- only a successful Test lifts the hold

def test_failed_test_keeps_the_hold_and_only_a_successful_test_lifts_it(minimal, env, monkeypatch):
    from app import qbit_connector
    from tests.test_library_mover import Resp
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'x')
    lm.reset_login()
    log = []

    class S:
        def post(self, url, data=None, timeout=None, **kw):
            log.append(url)
            return Resp(200, 'Fails.')

        def get(self, *a, **k):
            return Resp(data=[])

        def close(self):
            pass
    for i in range(10):
        lm.tick(now=i * 1000, session_factory=S)
    assert len(log) == 3 and lm.login_state()['held']
    lm.init(settings=env.settings)                           # restart
    c = _client(minimal)
    creds = {'url': 'http://qbit.invalid:8080', 'username': 'u', 'password': 'p'}
    app.main.settings.update({'qbittorrent': dict(creds)})
    r = c.put('/api/settings/connectors/qbittorrent', json=creds, environ_base=REMOTE, headers=HDR)   # same-creds Save
    assert r.status_code in (200, 204), (r.status_code, r.get_data(as_text=True)[:200])
    assert lm.login_state()['held']
    monkeypatch.setattr(qbit_connector, 'check', lambda u, n, p: (qbit_connector.BAD_LOGIN, ''))
    r = c.post('/api/settings/connectors/qbittorrent/test', json=creds, environ_base=REMOTE, headers=HDR)   # failed Test
    assert r.get_json().get('ok') is False
    for i in range(10):
        lm.tick(now=20000 + i * 1000, session_factory=S)
    assert len(log) == 3 and lm.login_state()['held']
    monkeypatch.setattr(qbit_connector, 'check', lambda u, n, p: (None, 'v4.6.7'))
    r = c.post('/api/settings/connectors/qbittorrent/test', json=creds, environ_base=REMOTE, headers=HDR)   # good Test
    assert r.get_json().get('ok') is True, r.get_json()
    assert not lm.login_state()['held']
    lm.tick(now=40000, session_factory=S)
    assert len(log) == 4
