"""v260 Downloads guard on the test install (PM): prod 1b4ea74's cleaner, deleting only through qBittorrent's
torrents/delete with deleteFiles=true. Every outside service is replaced: qBittorrent (fetch_torrents,
delete_with_files, the HTTP session), Plex (plex_playing_files, requests.get) and statvfs (shutil.disk_usage)."""
import shutil
import time
from collections import namedtuple

import pytest
import requests
from flask import jsonify

from app import downloads_guard as dg
from app import file_management as fm
from app import instance, minimal, qbit_owner, storage_cleanup as sc, tv_settings, tv_store
from app.automation import dg_routes, dg_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req

API = '/api/settings/automation/downloads-guard'
GIB = 1024 ** 3
NOW = 1_800_000_000.0
DAY = 86400
IPT = 'https://ssl.empirehost.me/a/announce'
BHD = 'https://beyond-hd.me/announce/x'
UNKNOWN = 'https://tracker.example.org/announce'
Usage = namedtuple('Usage', 'total used free')


def tor(h, name, *, size=10, tracker=IPT, done=NOW - 10 * DAY, progress=1.0, state='stalledUP', tags='',
        category='', seeding_time=30 * DAY, ratio=2.0, root='/downloads'):
    return {'hash': h, 'name': name, 'content_path': f'{root}/{name}', 'save_path': root, 'size': size * GIB,
            'tracker': tracker, 'completion_on': done, 'added_on': done - 3600, 'progress': progress, 'state': state,
            'tags': tags, 'category': category, 'seeding_time': seeding_time, 'ratio': ratio}


@pytest.fixture
def env(tmp_path, monkeypatch, store, tv_env):  # noqa: F811
    """A guard folder, prod's defaults, qBittorrent / Plex / statvfs replaced. env.torrents feeds qBit."""
    monkeypatch.setitem(dg_store._cfg, 'path', str(tmp_path / 'dg.json'))
    monkeypatch.setattr(dg, '_data_dir', lambda: tmp_path)
    dl = tmp_path / 'downloads'
    dl.mkdir()
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(dl))
    fm.save({**fm.load(), 'path_map': [{'remote': '/downloads', 'local': str(dl)}]})

    class Env:
        root = str(dl)
        torrents = []
        deleted = []
        playing = []
        qbit_ok = True
        delete_ok = True
    e = Env()
    monkeypatch.setattr(sc, 'fetch_torrents', lambda: list(e.torrents) if e.qbit_ok else None)
    monkeypatch.setattr(sc, 'delete_with_files', lambda hashes: (e.deleted.append(list(hashes)), e.delete_ok)[1])
    monkeypatch.setattr(sc, 'plex_playing_files', lambda: e.playing if e.playing is not None else None)
    monkeypatch.setattr(shutil, 'disk_usage', lambda p: Usage(1000 * GIB, 900 * GIB, 100 * GIB))
    monkeypatch.setattr(sc.time, 'time', lambda: NOW)
    return e


def names(plan):
    return [x['name'] for x in plan['entries']]


# ------------------------------------------------------------------ defaults

def test_defaults_match_prod():
    d = dg_store.defaults()
    assert d['enabled'] is False
    assert d['check_interval_minutes'] == dg._DEFAULT_CHECK_MINUTES == 15
    assert d['min_free_gb'] == dg._DEFAULT_MIN_FREE_GB == 150
    assert d['reclaim_gb'] == dg._DEFAULT_RECLAIM_GB == 100
    assert d['daily_cap_gb'] == dg._DEFAULT_DAILY_CAP_GB == 500
    assert d['trackers'] == list(dg._DEFAULT_TRACKERS) == ['ipt']
    assert d['pill_gb'] * GIB == sc.DEFAULT_TARGET_BYTES == 100 * GIB
    assert d['min_age_hours'] == sc.MIN_AGE_HOURS == 48
    assert d['max_run_gb'] * GIB == sc.MAX_TARGET_BYTES == 2 * 1024 ** 4
    assert d['min_seed_hours'] == 0 and d['min_ratio'] == 0
    assert d['scope'] == 'all' and d['folder'] == ''


def test_unreadable_settings_file_reads_as_defaults(env, tmp_path):
    (tmp_path / 'dg.json').write_text('{nope')
    assert dg_store.settings() == dg_store.defaults()


def test_folder_defaults_to_media_downloads_root(env):
    assert sc.downloads_root() == env.root
    assert dg_store.save({'folder': env.root})['folder'] == env.root


# ------------------------------------------------------------------ eligibility

def test_finished_only(env):
    env.torrents = [tor('a1', 'Done'), tor('a2', 'Partial', progress=0.5, state='downloading'),
                    tor('a3', 'Checking', state='checkingUP'), tor('a4', 'Paused', progress=0.9, state='pausedDL')]
    p = sc.plan_cleanup(1000 * GIB)
    assert names(p) == ['Done'] and p['held']['unfinished'] == 3


def test_qbit_down_means_no_run(env):
    env.qbit_ok = False
    env.torrents = [tor('a1', 'Done')]
    out = sc.run_cleanup(100 * GIB)
    assert out['success'] is False and out['error'] == sc.QBIT_UNREADABLE and env.deleted == []
    r = dg.run_cycle(force=True)
    assert r['error'] == sc.QBIT_UNREADABLE and env.deleted == []


def test_plex_playing_is_skipped(env):
    env.torrents = [tor('a1', 'Heat.1995.1080p'), tor('a2', 'Zodiac.2007.mkv')]
    env.playing = ['/data/downloads/Heat.1995.1080p/Heat.1995.1080p.mkv', '/media/Movies/Arrival (2016)/Arrival.mkv']
    p = sc.plan_cleanup(1000 * GIB)
    assert names(p) == ['Zodiac.2007.mkv'] and p['held']['playing'] == 1
    env.playing = ['C:\\downloads\\Zodiac.2007.mkv']
    assert names(sc.plan_cleanup(1000 * GIB)) == ['Heat.1995.1080p']


def test_plex_unreadable_deletes_nothing(env):
    env.playing = None
    env.torrents = [tor('a1', 'Done')]
    out = sc.run_cleanup(100 * GIB)
    assert out['error'] == sc.PLEX_UNREADABLE and env.deleted == []


def test_plex_sessions_are_read_only_and_mapped(monkeypatch):
    seen = []

    class R:
        status_code = 200

        def json(self):
            return {'MediaContainer': {'Metadata': [{'Media': [{'Part': [{'file': '/data/a/b.mkv'}]}]}]}}
    monkeypatch.setattr('app.settings_manager.SettingsFile.get',
                        lambda self, k, d=None: {'plex.url': 'http://plex:32400', 'plex.token': 'tok'}.get(k, d))
    monkeypatch.setattr(requests, 'get', lambda url, **kw: (seen.append(url), R())[1])
    monkeypatch.setattr(requests, 'post', lambda *a, **k: pytest.fail('no Plex writes'))
    assert sc.plex_playing_files() == ['/data/a/b.mkv'] and seen == ['http://plex:32400/status/sessions']

    def boom(url, **kw):
        raise requests.ConnectionError()
    monkeypatch.setattr(requests, 'get', boom)
    assert sc.plex_playing_files() is None


def test_not_imported_and_mid_import_are_skipped(env):
    data = fm.load()
    data['jobs'] = {'m1': {'hash': 'm1', 'state': 'waiting'}, 'm2': {'hash': 'm2', 'state': 'done'},
                    'm3': {'hash': 'm3', 'state': 'failed'}, 'm4': {'hash': 'm4', 'state': 'exists'}}
    fm.save(data)
    with tv_store.write() as conn:
        for h, st in (('t1', 'importing'), ('t2', 'imported'), ('t3', 'downloading')):
            conn.execute("INSERT INTO grabs (hash, origin, grabbed_at, state) VALUES (?, 'auto', ?, ?)", (h, NOW, st))
    env.torrents = [tor(h, f'N{h}') for h in ('m1', 'm2', 'm3', 'm4', 't1', 't2', 't3')] + [tor('p1', 'Prods')]
    p = sc.plan_cleanup(1000 * GIB)
    assert sorted(names(p)) == ['Nm2', 'Nm4', 'Nt2', 'Prods']      # imported, and prod's untracked downloads
    assert p['held']['not imported'] == 4


def test_this_installs_download_without_an_import_record_stays(env):
    env.torrents = [tor('x1', 'Ours', tags=qbit_owner.tag_name()), tor('x2', 'Theirs')]
    assert names(sc.plan_cleanup(1000 * GIB)) == ['Theirs']


def test_scope_only_this_installs_downloads(env):
    data = fm.load()
    data['jobs'] = {'x1': {'hash': 'x1', 'state': 'done'}}
    fm.save(data)
    env.torrents = [tor('x1', 'Ours', tags=qbit_owner.tag_name()), tor('x2', 'Theirs')]
    assert sorted(names(sc.plan_cleanup(1000 * GIB))) == ['Ours', 'Theirs']
    dg_store.save({'scope': 'test_tagged'})
    p = sc.plan_cleanup(1000 * GIB)
    assert names(p) == ['Ours'] and p['held']['out of scope'] == 1


def test_age_seed_time_and_ratio(env):
    env.torrents = [tor('a1', 'Fresh', done=NOW - 47 * 3600), tor('a2', 'Old', done=NOW - 49 * 3600, seeding_time=3600,
                                                                 ratio=0.2)]
    assert names(sc.plan_cleanup(1000 * GIB)) == ['Old']
    dg_store.save({'min_seed_hours': 2})
    assert names(sc.plan_cleanup(1000 * GIB)) == []
    dg_store.save({'min_seed_hours': 0, 'min_ratio': 1.0})
    assert names(sc.plan_cleanup(1000 * GIB)) == []
    dg_store.save({'min_ratio': 0, 'min_age_hours': 1})
    assert sorted(names(sc.plan_cleanup(1000 * GIB))) == ['Fresh', 'Old']


def test_library_and_outside_folder_are_never_touched(env, tmp_path):
    lib = tmp_path / 'downloads' / 'Library'
    lib.mkdir()
    fm.save({**fm.load(), 'paths': [{'id': 'p_aaaaaaaa', 'name': 'Lib', 'path': str(lib)}]})
    env.torrents = [tor('a1', 'Library/Heat.mkv'), tor('a2', 'Elsewhere', root='/other'), tor('a3', 'Fine')]
    p = sc.plan_cleanup(1000 * GIB)
    assert names(p) == ['Fine'] and p['held'] == {'library': 1, 'outside folder': 1}


# ------------------------------------------------------------------ order, size, deletes

def test_tracker_order_and_cross_seeds(env):
    env.torrents = [
        tor('i1', 'IptOld', done=NOW - 30 * DAY), tor('b1', 'BhdNew', tracker=BHD, done=NOW - 5 * DAY),
        tor('c1', 'Cross', done=NOW - 40 * DAY), tor('c2', 'Cross', tracker=BHD, done=NOW - 40 * DAY),
        tor('u1', 'Unknown', tracker=UNKNOWN, done=NOW - 90 * DAY), tor('i2', 'IptNew', done=NOW - 3 * DAY)]
    p = sc.plan_cleanup(1000 * GIB, prefer_trackers=['beyondhd', 'ipt'])
    assert names(p) == ['BhdNew', 'Cross', 'IptOld', 'IptNew', 'Unknown']
    assert [x['phase'] for x in p['entries']] == ['preferred'] * 4 + ['fallback']
    p = sc.plan_cleanup(1000 * GIB, prefer_trackers=['ipt'])
    assert names(p) == ['IptOld', 'IptNew', 'Unknown', 'Cross', 'BhdNew']
    assert names(sc.plan_cleanup(1000 * GIB, prefer_trackers=[])) == ['Unknown', 'Cross', 'IptOld', 'BhdNew', 'IptNew']
    cross = next(x for x in p['entries'] if x['name'] == 'Cross')
    assert sorted(cross['hashes']) == ['c1', 'c2'] and cross['size_bytes'] == 10 * GIB


def test_delete_goes_through_qbit_with_files_and_stops_at_target(env):
    env.torrents = [tor('a1', 'A', size=40, done=NOW - 9 * DAY), tor('a2', 'B', size=40, done=NOW - 8 * DAY),
                    tor('a3', 'C', size=40, done=NOW - 7 * DAY)]
    out = sc.run_cleanup(50 * GIB)
    assert out['success'] and env.deleted == [['a1'], ['a2']] and out['freed_bytes'] == 80 * GIB
    env.deleted.clear()
    env.delete_ok = False
    out = sc.run_cleanup(10 * GIB)
    assert out['failed'] == ['A'] and out['freed_bytes'] == 0


def test_per_run_ceiling_is_a_setting(env):
    env.torrents = [tor(f'h{i}', f'N{i}', size=10, done=NOW - (20 - i) * DAY) for i in range(10)]
    dg_store.save({'max_run_gb': 25})
    p = sc.plan_cleanup(1000 * GIB)
    assert p['target_bytes'] == 25 * GIB and len(p['entries']) == 3


def test_one_run_at_a_time(env):
    sc._run_lock.acquire()
    try:
        assert sc.run_cleanup(GIB)['error'] == sc.BUSY_RUN and sc.running()
    finally:
        sc._run_lock.release()


def test_no_rmtree_or_remove_in_the_cleaner():
    src = open(sc.__file__).read()
    assert 'rmtree' not in src.replace('never rmtree', '') and 'os.remove' not in src and 'os.unlink' not in src


def test_delete_with_files_posts_exactly_those_hashes(monkeypatch):
    posts = []

    class S:
        def post(self, url, data=None, **kw):
            posts.append((url, data))
            return type('R', (), {'status_code': 200})()

        def close(self):
            pass
    monkeypatch.setattr(sc, '_session', lambda: (S(), 'http://qbit:8080'))
    assert sc.delete_with_files(['AA', 'bb']) is True
    assert posts == [('http://qbit:8080/api/v2/torrents/delete', {'hashes': 'AA|bb', 'deleteFiles': 'true'})]
    monkeypatch.setattr(sc, '_session', lambda: (None, ''))
    assert sc.delete_with_files(['aa']) is False


def _prepared(hashes, delete_files='true', path='/api/v2/torrents/delete'):
    return requests.Request('POST', 'http://qbit:8080' + path,
                            data={'hashes': hashes, 'deleteFiles': delete_files}).prepare()


def test_the_write_gate_lets_only_the_scoped_delete_through(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'downloads_guard')
    assert instance.writes_allowed('downloads_guard') and not instance.writes_allowed('qbit')
    assert not instance._scoped_delete_ok(_prepared('aa'))                  # outside the scope
    with instance.scoped_delete('downloads_guard', ['AA', 'bb']):
        assert instance._scoped_delete_ok(_prepared('aa|bb'))
        assert not instance._scoped_delete_ok(_prepared('aa|cc'))           # a hash not asked for
        assert not instance._scoped_delete_ok(_prepared('all'))
        assert not instance._scoped_delete_ok(_prepared('aa', 'false'))
        assert not instance._scoped_delete_ok(_prepared('aa', path='/api/v2/torrents/pause'))
    monkeypatch.setenv('LUMA_TEST_ALLOW', '')
    with instance.scoped_delete('downloads_guard', ['aa']):
        assert not instance._scoped_delete_ok(_prepared('aa'))              # the job itself is off


# ------------------------------------------------------------------ the guard

def test_guard_trigger_daily_limit_and_force(env):
    dg_store.save({'enabled': True, 'min_free_gb': 150, 'reclaim_gb': 100, 'daily_cap_gb': 120})
    env.torrents = [tor(f'h{i}', f'N{i}', size=30, done=NOW - (30 - i) * DAY) for i in range(10)]
    v = dg.evaluate()
    assert v['triggered'] and v['target_bytes'] == 100 * GIB
    r = dg.run_cycle()
    assert r['freed_bytes'] == 120 * GIB and len(env.deleted) == 4
    v = dg.evaluate()
    assert v['triggered'] is False and v['freed_today_bytes'] == 120 * GIB   # the daily limit is spent
    assert dg.run_cycle()['freed_bytes'] == 0 and len(env.deleted) == 4
    assert dg.run_cycle(force=True)['freed_bytes'] == 0 and len(env.deleted) == 4  # Clean up now obeys it too (prod)


def test_disabled_guard_never_triggers(env):
    env.torrents = [tor('a1', 'A')]
    assert dg.evaluate()['triggered'] is False and dg.run_cycle()['freed_bytes'] == 0 and env.deleted == []


def test_scheduler_needs_the_instance_gate(monkeypatch):
    started = []
    monkeypatch.setattr(dg.threading, 'Thread', lambda **kw: type('T', (), {'start': lambda s: started.append(1)})())
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', '')
    dg.start_downloads_guard_thread()
    assert started == []
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'downloads_guard')
    dg.start_downloads_guard_thread()
    assert started == [1]


# ------------------------------------------------------------------ routes and owner gates

@pytest.fixture
def site(env, owner):  # noqa: F811
    return owner


def _sign_in_401():
    tv_settings._cfg['signed_in_owner'] = lambda: (None, (jsonify({'error': 'Sign in', 'sign_in': True}), 401))


def test_routes_are_allowlisted_in_minimal_mode():
    for p, m in ((API, 'GET'), (API, 'PUT'), ('/api/storage/downloads', 'GET'), ('/api/storage/downloads/cleanup', 'POST'),
                 ('/api/downloads-guard/status', 'GET'), ('/api/downloads-guard/trackers', 'GET'),
                 ('/api/downloads-guard/preview', 'GET'), ('/api/downloads-guard/run', 'POST')):
        assert minimal.path_allowed(p, m), p
    assert instance.endpoint_job('POST', '/api/storage/downloads/cleanup') == 'downloads_guard'
    assert instance.endpoint_job('POST', '/api/downloads-guard/run') == 'downloads_guard'


def test_get_and_round_trip(site):
    d = _req(site, 'get', API).get_json()
    assert d['settings'] == dg_store.defaults() and d['defaults'] == dg_store.defaults()
    assert [t['key'] for t in d['trackers']][:2] == ['ipt', 'beyondhd']
    r = _req(site, 'put', API, {'trackers': ['beyondhd', 'ipt'], 'enabled': True, 'scope': 'test_tagged'})
    assert r.status_code == 200 and r.get_json()['settings']['trackers'] == ['beyondhd', 'ipt']
    for bad in ({'reclaim_gb': 0}, {'trackers': ['ipt', 'ipt']}, {'scope': 'mine'}, {'folder': 'rel'},
                {'min_ratio': -1}, {'radarr': 1}, {'enabled': 'yes'}, [1]):
        assert _req(site, 'put', API, bad).status_code == 400, bad


@pytest.mark.parametrize('change', [
    {'enabled': True}, {'reclaim_gb': 101}, {'max_run_gb': 4096}, {'pill_gb': 200}, {'daily_cap_gb': 0},
    {'daily_cap_gb': 600}, {'min_age_hours': 24}, {'check_interval_minutes': 5}, {'min_free_gb': 200},
    {'trackers': ['ipt', 'beyondhd']}, {'scope': 'test_tagged'}, {'folder': '/tmp'}])
def test_widening_needs_the_signed_in_owner(site, change):
    _sign_in_401()
    try:
        r = _req(site, 'put', API, change)
        assert r.status_code == 401 and r.get_json()['sign_in'] is True
        assert dg_store.settings() == dg_store.defaults()
    finally:
        tv_settings._cfg.pop('signed_in_owner', None)


@pytest.mark.parametrize('change', [{'reclaim_gb': 50}, {'daily_cap_gb': 100}, {'min_age_hours': 72},
                                    {'trackers': []}, {'min_ratio': 1.5}, {'enabled': False}])
def test_tightening_is_open_to_the_owner(site, change):
    _sign_in_401()
    try:
        assert _req(site, 'put', API, change).status_code == 200
    finally:
        tv_settings._cfg.pop('signed_in_owner', None)


def test_clean_up_now_and_the_pill_need_the_signed_in_owner(site, monkeypatch):
    calls = []
    monkeypatch.setattr(sc, 'run_cleanup', lambda *a, **k: calls.append(a) or {'success': True})
    monkeypatch.setattr(dg, 'trigger_manual_run', lambda dry_run: calls.append(('run', dry_run)))
    from app import start_flow
    monkeypatch.setattr(start_flow, 'require_signed_in_owner',
                        lambda: (None, (jsonify({'error': 'Sign in', 'sign_in': True}), 401)))
    import app.main as main
    monkeypatch.setattr(main._start_flow, 'require_signed_in_owner', start_flow.require_signed_in_owner)
    assert _req(site, 'post', '/api/storage/downloads/cleanup', {}).status_code == 401
    assert _req(site, 'post', '/api/downloads-guard/run', {'dry_run': False}).status_code == 401
    assert calls == []
    assert _req(site, 'post', '/api/downloads-guard/run', {'dry_run': True}).status_code == 200
    assert calls == [('run', True)]


def test_pill_reads_free_space_and_its_settings(site):
    dg_store.save({'pill_gb': 50, 'min_age_hours': 72})
    d = _req(site, 'get', '/api/storage/downloads').get_json()
    assert d['available'] and d['free_bytes'] == 100 * GIB
    assert d['cleanup_target_bytes'] == 50 * GIB and d['min_age_hours'] == 72


def test_pill_cleanup_is_capped_at_the_pill_amount(site, monkeypatch):
    seen = []
    monkeypatch.setattr(sc, 'run_cleanup', lambda target, **k: seen.append((target, k['prefer_trackers'])) or {'success': True})
    import app.main as main
    monkeypatch.setattr(main, '_signed_in_owner_only', lambda fn: fn, raising=False)
    from app import start_flow
    monkeypatch.setattr(start_flow, 'require_signed_in_owner', lambda: ({'id': 1}, None))
    r = _req(site, 'post', '/api/storage/downloads/cleanup', {'target_bytes': 10 ** 15})
    assert r.status_code == 200 and seen == [(100 * GIB, [])]


def test_off_on_the_test_instance_until_opted_in(site, monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', '')
    assert _req(site, 'get', API).get_json()['allowed'] is False
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'downloads_guard')
    assert _req(site, 'get', API).get_json()['allowed'] is True


def test_status_and_trackers_read_qbit_only(site, env):
    env.torrents = [tor('a1', 'A'), tor('b1', 'B', tracker=BHD)]
    t = _req(site, 'get', '/api/downloads-guard/trackers').get_json()
    got = {x['key']: x for x in t['trackers']}
    assert t['available'] and got['ipt']['count'] == 1 and got['beyondhd']['count'] == 1
    s = _req(site, 'get', '/api/downloads-guard/status').get_json()
    assert s['status']['evaluation']['enabled'] is False and env.deleted == []
    p = _req(site, 'get', '/api/downloads-guard/preview').get_json()
    assert [e['name'] for e in p['entries']] == ['A', 'B'] and env.deleted == []
    assert dg_routes.URL == API
