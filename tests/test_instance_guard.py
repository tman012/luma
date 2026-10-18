"""LUMA_INSTANCE=test (Taylor's hard rule): every job that grabs, moves, renames,
imports, unmonitors or deletes is OFF by default, gated at job start AND at the
write call; LUMA_TEST_ALLOW opts in by name; prod is unchanged. The library
mover is allowed on test, only for luma-test-tagged torrents and only by copy or
hardlink. App logs persist under /app/data/logs (10 MB x 5)."""
import importlib
import logging
import os
import shutil
import threading

import pytest
import requests
from flask import Flask

from app import instance
from app import library_mover as lm
from tests.test_library_mover import H1, TMDB, add_path, env, job, movie_file, torrent  # noqa: F401

START = {  # module: (start function, job)
    'auto_rename': ('start_monitor_thread', 'auto_rename'),
    'trending_watchlist': ('start_trending_watchlist_thread', 'trending_watchlist'),
    'trending_tv_watchlist': ('start_trending_tv_watchlist_thread', 'trending_tv_watchlist'),
    'now_available_watchlist': ('start_now_available_watchlist_thread', 'now_available_watchlist'),
    'anticipated_watchlist': ('start_anticipated_watchlist_thread', 'anticipated_watchlist'),
    'latest_auto_grab': ('start_latest_auto_grab_thread', 'latest_auto_grab'),
    'upgrade_4k_auto_grab': ('start_upgrade_4k_auto_grab_thread', 'upgrade_4k'),
    'upgrade_scheduler': ('start_upgrade_scheduler_thread', 'upgrade_scheduler'),
    'movie_night': ('start_movie_night_thread', 'movie_night'),
    'genre_roulette': ('start_genre_roulette_thread', 'roulette'),
    'franchise_completer': ('start_franchise_completer_thread', 'franchise_completer'),
    'watchlist_digital_dates': ('start_watchlist_digital_dates_thread', 'watchlist_digital_dates'),
    'daily_missing': ('start_daily_missing_thread', 'daily_missing'),
    'movie_collections': ('start_movie_collections_thread', 'movie_collections'),
    'downloads_guard': ('start_downloads_guard_thread', 'downloads_guard'),
}


@pytest.fixture
def test_instance(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.delenv('LUMA_TEST_ALLOW', raising=False)
    yield
    instance.uninstall_write_guard()


def test_prod_is_unchanged(monkeypatch):
    for v in (None, '', 'prod'):           # v174: 'PROD' or '  ' is no longer prod (FF-10, fail closed)
        if v is None:
            monkeypatch.delenv('LUMA_INSTANCE', raising=False)
        else:
            monkeypatch.setenv('LUMA_INSTANCE', v)
        assert not instance.is_test()
        assert all(instance.writes_allowed(j) for j in instance.GATED)
        assert instance.blocked_jobs() == {}
        assert instance.install_write_guard() is False


def test_test_blocks_every_writer_by_default_and_opts_in_by_name(test_instance, monkeypatch):
    assert set(instance.blocked_jobs()) == set(instance.GATED)
    for j in ('library_mover', 'auto_rename', 'daily_missing', 'downloads_guard', 'franchise_completer', 'roulette',
              'movie_night', 'anticipated_watchlist', 'now_available_watchlist', 'upgrade_4k', 'arr', 'qbit'):
        if j != 'library_mover':
            assert not instance.writes_allowed(j), j
    assert instance.writes_allowed('mover') and instance.writes_allowed('tv_link')     # allowed by default (scoped)
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'mover, Roulette')
    assert instance.writes_allowed('roulette') and not instance.writes_allowed('movie_night')
    with pytest.raises(instance.WritesBlocked):
        instance.require('movie_night')


def test_blocked_jobs_log_once_at_info(test_instance, caplog, monkeypatch):
    monkeypatch.setattr(instance, '_logged', [False])
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
        instance.log_blocked_once()
    lines = [r for r in caplog.records if 'is OFF' in r.getMessage()]
    assert len(lines) == len(instance.GATED) and all(r.levelno == logging.INFO for r in lines)


@pytest.mark.parametrize('mod', sorted(START))
def test_each_job_start_is_gated(test_instance, monkeypatch, mod):
    m = importlib.import_module(f'app.{mod}')
    fn, job_name = START[mod]
    started = []
    monkeypatch.setattr(threading.Thread, 'start', lambda self: started.append(self.name))
    assert getattr(m, fn)() is None
    assert started == []                                                   # no thread on test
    monkeypatch.setenv('LUMA_TEST_ALLOW', job_name)
    assert instance.writes_allowed(job_name)                               # opt-in reaches the same gate


def test_start_services_gates_each_scheduler(test_instance):
    src = open(os.path.join(os.path.dirname(instance.__file__), 'main.py')).read()
    for mod, (fn, job_name) in START.items():
        assert f"if _instance.writes_allowed('{job_name}'):\n" in src and fn in src, mod


@pytest.mark.parametrize('method,url,kind', [
    ('POST', 'http://radarr:7878/api/v3/movie', 'arr'),
    ('DELETE', 'http://radarr:7878/api/v3/movie/5?deleteFiles=true', 'arr'),
    ('PUT', 'http://sonarr:8989/api/v3/series/editor', 'arr'),              # unmonitor
    ('POST', 'http://sonarr:8989/api/v3/command', 'arr'),
    ('POST', 'http://prowlarr:9696/api/v1/search', 'arr'),                  # a grab
    ('POST', 'http://qbit:8080/api/v2/torrents/add', 'qbit'),
    ('POST', 'http://qbit:8080/api/v2/torrents/delete', 'qbit'),
    ('POST', 'http://qbit:8080/api/v2/torrents/setCategory', 'qbit'),
    ('POST', 'http://qbit:8080/api/v2/torrents/setLocation', 'qbit'),
    ('GET', 'http://qbit:8080/api/v2/torrents/pause?hashes=x', 'qbit'),
    ('GET', 'http://qbit:8080/api/v2/torrents/info', None),
    ('GET', 'http://qbit:8080/api/v2/torrents/files', None),
    ('POST', 'http://qbit:8080/api/v2/auth/login', None),
    ('GET', 'http://radarr:7878/api/v3/movie', None),
    ('POST', 'https://api.themoviedb.org/3/search/tv', None),
])
def test_write_kind(method, url, kind):
    assert instance.write_kind(method, url) == kind


def test_the_write_call_is_refused_on_test_and_reads_pass(test_instance, monkeypatch):
    sent = []

    def ok(self, req, **k):
        sent.append((req.method, req.url))
        r = requests.Response()
        r.status_code, r.url, r.request = 200, req.url, req
        return r
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', ok)
    assert instance.install_write_guard()
    s = requests.Session()
    s.get('http://qbit:8080/api/v2/torrents/info')
    for call in (lambda: s.post('http://qbit:8080/api/v2/torrents/delete', data={'hashes': 'x', 'deleteFiles': 'true'}),
                 lambda: requests.post('http://radarr:7878/api/v3/movie', json={}),
                 lambda: requests.put('http://sonarr:8989/api/v3/episode/monitor', json={'monitored': False})):
        with pytest.raises(requests.RequestException):
            call()
    assert sent == [('GET', 'http://qbit:8080/api/v2/torrents/info')]
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'qbit')
    s.post('http://qbit:8080/api/v2/torrents/delete', data={})
    assert len(sent) == 2


def test_manual_endpoints_are_refused_on_test(test_instance):
    app = Flask('t')
    hits = []
    for path in ('/api/genre-roulette/run', '/api/movie-night/run', '/api/downloads/add', '/api/sonarr/monitor/series',
                 '/api/automation/upgrade-4k-auto-grab/run', '/api/downloads-guard/run', '/api/franchise-completer/run'):
        app.add_url_rule(path, path, (lambda p=path: hits.append(p) or 'ok'), methods=['GET', 'POST'])
    instance.init_app(app)
    c = app.test_client()
    r = c.post('/api/genre-roulette/run')
    assert r.status_code == 403 and r.get_json()['job'] == 'roulette'
    for p in ('/api/movie-night/run', '/api/downloads/add', '/api/sonarr/monitor/series',
              '/api/automation/upgrade-4k-auto-grab/run', '/api/downloads-guard/run', '/api/franchise-completer/run'):
        assert c.post(p).status_code == 403, p
    assert c.get('/api/genre-roulette/run').status_code == 200        # reads still work
    assert hits == ['/api/genre-roulette/run']


def test_manual_endpoints_untouched_on_prod(monkeypatch):
    monkeypatch.delenv('LUMA_INSTANCE', raising=False)
    app = Flask('p')
    app.add_url_rule('/api/genre-roulette/run', 'r', lambda: 'ok', methods=['POST'])
    instance.init_app(app)
    assert app.test_client().post('/api/genre-roulette/run').status_code == 200


# ------------------------------------------------------------------ the mover on test

def _no_source_writes(monkeypatch, src_root):
    root = str(src_root)

    def refuse(name):
        def f(*a, **k):
            for x in a[:2]:
                if isinstance(x, (str, os.PathLike)) and str(x).startswith(root):
                    raise AssertionError(f'{name} touched a source file: {x}')
            return ORIG[name](*a, **k)
        return f
    ORIG = {'rename': os.rename, 'replace': os.replace, 'remove': os.remove, 'unlink': os.unlink, 'move': shutil.move,
            'rmtree': shutil.rmtree, 'rmdir': os.rmdir}
    for n in ('rename', 'replace', 'remove', 'unlink', 'rmdir'):
        monkeypatch.setattr(os, n, refuse(n))
    monkeypatch.setattr(shutil, 'move', refuse('move'))
    monkeypatch.setattr(shutil, 'rmtree', refuse('rmtree'))


@pytest.mark.parametrize('transfer', ['copy', 'hardlink'])
def test_mover_on_test_only_copies_or_hardlinks_and_never_touches_the_source(env, test_instance, monkeypatch, transfer):  # noqa: F811
    from app import file_management as fm
    add_path('Movies', env.media / 'Movies')
    fm.update(lambda d: d.__setitem__('transfer', transfer))
    src = movie_file(env, 'Heat.1995.1080p/Heat.1995.1080p.mkv')
    before = (src.stat().st_ino, src.read_bytes(), sorted(os.listdir(src.parent)))
    lm.track(H1, TMDB, 'Heat.1995.1080p')
    torrent(env, H1, 'Heat.1995.1080p')                                          # tagged luma-test
    _no_source_writes(monkeypatch, env.dl)
    assert env.tick()['filed'] == 1
    assert (src.stat().st_ino, src.read_bytes(), sorted(os.listdir(src.parent))) == before   # same file, same name, still there
    assert job(H1)['files'][0]['result'] in ('copied', 'linked')
    assert all(c[0] == 'GET' or c[1].endswith('/api/v2/auth/login') for c in env.q.calls)    # no qBit writes


def test_mover_on_test_skips_any_torrent_without_the_tag(env, test_instance):  # noqa: F811
    add_path('Movies', env.media / 'Movies')
    src = movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv', tags='', category='luma-test')             # a category of the same name doesn't count
    assert env.tick()['dropped'] == 1 and job(H1) is None
    assert not any(os.scandir(env.media / 'Movies')) and src.exists()


def test_mover_refuses_any_mode_but_copy_or_hardlink(tmp_path):
    with pytest.raises(lm.Refused):
        lm.transfer(str(tmp_path / 'x'), 0, str(tmp_path), 'x', 'move', None, str(tmp_path))


# ------------------------------------------------------------------ logs

def test_logs_persist_rotating_10mb_x5(tmp_path, monkeypatch):
    from logging.handlers import RotatingFileHandler
    from app import log_files
    monkeypatch.setenv('LUMA_LOG_DIR', str(tmp_path / 'logs'))
    root = logging.getLogger('luma-test-root')
    root.setLevel(logging.INFO)
    h = log_files.attach(root=root)
    try:
        assert isinstance(h, RotatingFileHandler) and (h.maxBytes, h.backupCount) == (10 * 1024 * 1024, 5)
        assert log_files.attach(root=root) is h                                   # once
        root.info('hello file')
        h.flush()
        assert 'hello file' in (tmp_path / 'logs' / 'luma.log').read_text()
        assert log_files.DEFAULT_DIR == '/app/data/logs'
    finally:
        root.removeHandler(h)
        h.close()


def test_an_unwritable_log_dir_never_breaks_boot(monkeypatch):
    from app import log_files
    monkeypatch.setenv('LUMA_LOG_DIR', '/proc/no-such/logs')
    assert log_files.attach(root=logging.getLogger('luma-test-root2')) is None
