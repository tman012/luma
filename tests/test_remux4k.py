"""v359 Settings > Automation > 4K Remux auto-grab, native (prod upgrade_4k_auto_grab.py without Prowlarr or
Radarr). Trackers, TMDb, Plex and the grab path are replaced here."""
import time

import pytest

from app import instance, movie_dupes, movie_import
from app.automation import gr_store, remux4k, remux4k_routes
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.test_v204c_movie_dupes import _layout, qbit
from tests.test_v203_movie_scanner import mv  # noqa: F401
from tests.test_tv_v158c import guard  # noqa: F401

API = '/api/settings/automation/4k-remux'
H = 3600


def item(title, seeders=300, age_h=2.0, imdb=None, tracker='IPT', guid=None, now=None):
    now = time.time() if now is None else now
    pub = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - age_h * H)) if age_h is not None else None
    return {'title': title, 'seeders': seeders, 'size': 60_000_000_000, 'indexer': tracker, 'publishDate': pub,
            'downloadUrl': f'/x/{guid or title}', 'guid': guid or f'luma-tracker:{tracker}:{title}', 'imdbId': imdb}


class World:
    def __init__(self):
        self.items = {'IPT': [], 'BHD': []}
        self.status = {}
        self.searches = []
        self.tmdb = {}            # (path, query or None) -> body
        self.tmdb_calls = []
        self.plex = {'movies_by_title': {'x': [{'title': 'Owned 4K', 'year': 2020, 'library_section_id': 4,
                                                 'tmdb_id': 900}]}}
        self.plex_error = False
        self.grabs = []
        self.grab_result = None
        self.sleeps = []
        self.mirror = []


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setitem(gr_store._cfg, 'db_path', str(tmp_path / 'automation.db'))
    monkeypatch.setattr(remux4k, '_legacy', lambda: {'enabled': True, 'min_seeders': 200, 'max_age_hours': 36,
                                                     'scan_interval_minutes': 15, 'plex_4k_section_id': 4})
    monkeypatch.setattr(remux4k, '_mirror', lambda cfg: world.mirror.append(dict(cfg)))
    world = World()

    def search(cfg, now):
        world.searches.append(cfg['name'])
        st = world.status.get(cfg['name'])
        if st:
            nxt = st.pop(0) if isinstance(st, list) else st
            if nxt != 'ok':
                return [], [{'name': cfg['name'], **nxt}]
        return list(world.items[cfg['name']]), [{'name': cfg['name'], 'status': 'ok'}]

    def tmdb_get(path, params):
        world.tmdb_calls.append((path, params.get('query')))
        return world.tmdb.get((path, params.get('query')), {'results': [], 'movie_results': []})

    def plex():
        if world.plex_error:
            raise RuntimeError('plex down')
        return world.plex

    def grab(it, tmdb_id, movie, now):
        world.grabs.append((it['title'], tmdb_id, movie))
        return world.grab_result or {'ok': True, 'hash': f'h{len(world.grabs)}'}

    monkeypatch.setattr(remux4k, 'tracker_list', lambda: [{'id': 1, 'name': 'IPT'}, {'id': 2, 'name': 'BHD'}])
    monkeypatch.setattr(remux4k, 'search', search)
    monkeypatch.setattr(remux4k, 'tmdb_key', lambda: 'k')
    monkeypatch.setattr(remux4k, 'tmdb_get', tmdb_get)
    monkeypatch.setattr(remux4k, 'plex_index', plex)
    monkeypatch.setattr(remux4k, 'grabs_allowed', lambda: True)
    monkeypatch.setattr(remux4k, 'grab', grab)
    monkeypatch.setattr(remux4k, 'note_origin', lambda h, it, c: None)
    monkeypatch.setitem(remux4k._sleep, 'fn', lambda s: world.sleeps.append(s))
    monkeypatch.setitem(remux4k._preview, 'result', None)
    return world


def movie(world, query, tmdb_id, title, year):
    world.tmdb[('/search/movie', query)] = {'results': [{'id': tmdb_id, 'title': title, 'original_title': title,
                                                         'release_date': f'{year}-05-01'}]}


def by_release(res):
    return {r['release']: r['decision'] for r in res['releases']}


# ------------------------------------------------------------------ helpers

def test_quality_is_2160p_and_remux():
    assert remux4k.is_2160p_remux('Sinners.2025.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.7.1-GRP')
    assert remux4k.is_2160p_remux('Sinners 2025 UHD BluRay REMUX HDR')
    assert not remux4k.is_2160p_remux('Sinners.2025.1080p.BluRay.REMUX.AVC-GRP')
    assert not remux4k.is_2160p_remux('Sinners.2025.2160p.UHD.BluRay.x265-GRP')
    assert not remux4k.is_2160p_remux('Sinners.2025.UHD.1080p.REMUX')


def test_publish_dates():
    assert remux4k.published('2026-11-26T05:00:00Z') == remux4k.published('Thu, 26 Nov 2026 05:00:00 +0000')
    assert remux4k.published('') is None and remux4k.published('junk') is None
    assert remux4k.published(1_800_000_000) == 1_800_000_000.0


# ------------------------------------------------------------------ settings

def test_settings_import_prod_values_and_validate(w):
    s = remux4k.settings()
    assert s == {'enabled': True, 'min_seeders': 200, 'max_age_hours': 36, 'scan_interval_minutes': 15,
                 'plex_4k_section_id': 4}
    assert remux4k.save({'min_seeders': 250, 'quality_profile_id': 9})['min_seeders'] == 250   # Radarr key ignored
    assert w.mirror[-1]['min_seeders'] == 250 and 'quality_profile_id' not in w.mirror[-1]
    for bad in ({'min_seeders': 0}, {'max_age_hours': 169}, {'scan_interval_minutes': 4}, {'enabled': 'yes'},
                {'min_seeders': 1.5}, {'min_seeders': True}):
        with pytest.raises(remux4k.Invalid):
            remux4k.save(bad)
    assert remux4k.settings()['min_seeders'] == 250


def test_defaults_are_prods_code_defaults_off(tmp_path, monkeypatch):
    monkeypatch.setitem(gr_store._cfg, 'db_path', str(tmp_path / 'a.db'))
    monkeypatch.setattr(remux4k, '_legacy', lambda: {})
    assert remux4k.settings() == {'enabled': False, 'min_seeders': 100, 'max_age_hours': 24,
                                  'scan_interval_minutes': 15, 'plex_4k_section_id': 0}


# ------------------------------------------------------------------ the cycle

def test_grabs_every_qualifying_movie_and_skips_with_reasons(w):
    now = time.time()
    w.items['IPT'] = [
        item('Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP', 400, now=now),
        item('Sinners.2025.2160p.UHD.BluRay.REMUX.HDR-OTHER', 250, now=now),
        item('Low.Seeds.2025.2160p.BluRay.REMUX-GRP', 150, now=now),
        item('Old.Movie.2024.2160p.BluRay.REMUX-GRP', 900, age_h=50, now=now),
        item('Sinners.2025.1080p.BluRay.REMUX-GRP', 999, now=now),
        item('Owned.4K.2020.2160p.BluRay.REMUX-GRP', 300, now=now),
        item('Nobody.Knows.2025.2160p.BluRay.REMUX-GRP', 300, now=now),
        item('Undated.2025.2160p.BluRay.REMUX-GRP', 300, age_h=None, now=now),
    ]
    w.items['BHD'] = [item('Weapons 2025 2160p UHD BluRay REMUX DV HDR10', 220, imdb='tt26581740', tracker='BHD',
                           now=now)]
    movie(w, 'sinners', 1233413, 'Sinners', 2025)
    movie(w, 'owned 4k', 900, 'Owned 4K', 2020)
    w.tmdb[('/find/tt26581740', None)] = {'movie_results': [{'id': 1078605, 'title': 'Weapons',
                                                             'release_date': '2025-08-06'}]}
    res = remux4k.run('scheduled', now=now)
    assert res['error'] is None and res["grabbed"] == 2 and res["candidates"] == 3
    assert sorted(t for _, t, _ in w.grabs) == [1078605, 1233413]
    assert ('Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP', 1233413, {'title': 'Sinners', 'year': 2025}) in w.grabs
    d = by_release(res)
    assert d['Sinners.2025.2160p.UHD.BluRay.REMUX.HDR-OTHER'] == 'same_movie'
    assert d['Low.Seeds.2025.2160p.BluRay.REMUX-GRP'] == 'seeders'
    assert d['Old.Movie.2024.2160p.BluRay.REMUX-GRP'] == 'age'
    assert d['Owned.4K.2020.2160p.BluRay.REMUX-GRP'] == 'in_4k'
    assert d['Nobody.Knows.2025.2160p.BluRay.REMUX-GRP'] == 'no_tmdb'
    assert d['Undated.2025.2160p.BluRay.REMUX-GRP'] == 'no_date'
    assert 'Sinners.2025.1080p.BluRay.REMUX-GRP' not in d
    assert res['skipped_in_4k_titles'] == ['Owned 4K (2020)'] and res['checked'] == 9
    assert {g['tmdb_id'] for g in remux4k.grabs()} == {1078605, 1233413}
    assert remux4k.title_for(1233413) == {'tmdb_id': 1233413, 'title': 'Sinners', 'year': 2025}
    st = remux4k.status()
    assert st['last_result']['grabbed'] == 2 and 'releases' not in st['last_result'] and st['grabbed_total'] == 2
    assert remux4k.history()[0]['grabbed'] == 2 and remux4k.history()[0]['kind'] == 'scheduled'
    # next cycle: grabbed movies and tried releases are never grabbed again; TMDb answers are cached
    calls = len(w.tmdb_calls)
    w.grabs.clear()
    res = remux4k.run('scheduled', now=now + 900)
    assert w.grabs == [] and res['grabbed'] == 0
    d = by_release(res)
    assert d['Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP'] == 'tried'
    assert d['Sinners.2025.2160p.UHD.BluRay.REMUX.HDR-OTHER'] == 'already_grabbed'
    assert d['Owned.4K.2020.2160p.BluRay.REMUX-GRP'] == 'tried'
    assert len(w.tmdb_calls) == calls


def test_preview_grabs_nothing_and_records_nothing(w):
    now = time.time()
    w.items['IPT'] = [item('Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP', 400, now=now)]
    movie(w, 'sinners', 1233413, 'Sinners', 2025)
    res = remux4k.run('preview', now=now)
    assert res['dry'] and res['would_grab'] == 1 and w.grabs == []
    assert by_release(res) == {'Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP': 'would_grab'}
    assert remux4k.history() == [] and remux4k._tried() == {}
    assert remux4k.preview_state()['result']['would_grab'] == 1
    assert remux4k.run('scheduled', now=now)['grabbed'] == 1           # still grabbed for real afterwards


def test_unreadable_or_empty_4k_library_refuses_the_cycle(w):
    now = time.time()
    w.items['IPT'] = [item('Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP', 400, now=now)]
    movie(w, 'sinners', 1233413, 'Sinners', 2025)
    w.plex_error = True
    res = remux4k.run('scheduled', now=now)
    assert res['error'] == "Couldn't read the 4K Plex library" and w.grabs == []
    assert by_release(res)['Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP'] == 'refused'
    w.plex_error = False
    w.plex = {'movies_by_title': {'x': [{'title': 'Other', 'year': 2020, 'library_section_id': 1, 'tmdb_id': 5}]}}
    assert remux4k.run('scheduled', now=now)['error'] == "Couldn't read the 4K Plex library" and w.grabs == []
    assert remux4k._tried() == {}                                       # refused, not tried: the next cycle retries


def test_paced_tracker_waits_only_a_spacing_gap_else_skips(w):
    now = time.time()
    w.items['BHD'] = [item('Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP', 400, tracker='BHD', now=now)]
    movie(w, 'sinners', 1233413, 'Sinners', 2025)
    w.status['IPT'] = {'status': 'rate', 'reset_at': time.time() + 3600}
    w.status['BHD'] = [{'status': 'tv slot', 'reset_at': time.time() + 30}, 'ok']
    res = remux4k.run('scheduled', now=now)
    assert w.searches == ['IPT', 'BHD', 'BHD'] and len(w.sleeps) == 1 and w.sleeps[0] <= 31
    assert res['trackers'][0]['status'] == 'rate' and res['trackers'][1]['status'] == 'ok'
    assert res['grabbed'] == 1


def test_all_trackers_held_is_an_error_not_a_grab(w):
    w.status = {'IPT': {'status': 'rate', 'reset_at': time.time() + 3600},
                'BHD': {'status': 'rate', 'reset_at': time.time() + 3600}}
    res = remux4k.run('scheduled')
    assert res['error'] == 'Tracker limits reached; the next scan tries again' and w.grabs == []


def test_qbit_unreachable_stops_and_retries_next_cycle(w):
    now = time.time()
    w.items['IPT'] = [item('Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP', 400, now=now),
                      item('Weapons.2025.2160p.UHD.BluRay.REMUX-GRP', 300, now=now)]
    movie(w, 'sinners', 1, 'Sinners', 2025)
    movie(w, 'weapons', 2, 'Weapons', 2025)
    w.grab_result = {'ok': False, 'error': movie_dupes.NO_QBIT, 'duplicate': True, 'blocked': True}
    res = remux4k.run('scheduled', now=now)
    assert len(w.grabs) == 1 and res['error'] == "Grabs stopped: qBittorrent can't be reached"
    assert set(by_release(res).values()) == {'stopped'} and remux4k._tried() == {}


def test_duplicate_is_tried_once(w):
    now = time.time()
    w.items['IPT'] = [item('Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP', 400, now=now)]
    movie(w, 'sinners', 1, 'Sinners', 2025)
    w.grab_result = {'ok': False, 'error': movie_dupes.ON_DISK, 'duplicate': True, 'blocked': True}
    res = remux4k.run('scheduled', now=now)
    assert res['rejected'] == 1 and by_release(res)['Sinners.2025.2160p.UHD.BluRay.REMUX.DV-GRP'] == 'duplicate'
    assert remux4k.run('scheduled', now=now)['rejected'] == 0 and len(w.grabs) == 1


def test_grabs_off_refuses_real_runs_not_preview(w, monkeypatch):
    monkeypatch.setattr(remux4k, 'grabs_allowed', lambda: False)
    assert remux4k.run('manual')['error'] == 'Grabs are off on this install'
    assert remux4k.run('preview')['error'] is None


def test_schedule_first_run_after_boot_then_interval(w, monkeypatch):
    monkeypatch.setitem(remux4k._boot, 'at', 1000.0)
    assert remux4k.next_run() == 1050.0
    remux4k._put_state(last_auto_at=2000.0)
    assert remux4k.next_run() == 2000.0 + 15 * 60
    remux4k.save({'enabled': False})
    assert remux4k.next_run() is None


# ------------------------------------------------------------------ gates and shared paths

def test_native_gate_opens_only_the_native_job(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'upgrade_4k,movie_grab')
    assert instance.native_allowed('upgrade_4k') is True
    assert instance.writes_allowed('upgrade_4k') is False                 # the legacy Radarr job stays off
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'movie_grab')
    assert instance.native_allowed('upgrade_4k') is False


def test_4k_scope_dupes_count_only_4k_copies(tmp_path, monkeypatch, mv):  # noqa: F811
    video = _layout(tmp_path, monkeypatch)
    d = video / 'Movies' / 'Dune- Part Two (2024)'
    d.mkdir()
    (d / 'Dune- Part Two (2024).mkv').write_bytes(b'0')
    assert movie_dupes.on_disk('Dune: Part Two', 2024) == str(d)
    assert movie_dupes.on_disk('Dune: Part Two', 2024, '4k') is None
    k = video / '4K' / 'Dune- Part Two (2024)'
    k.mkdir()
    (k / 'Dune- Part Two (2024).mkv').write_bytes(b'0')
    assert movie_dupes.on_disk('Dune: Part Two', 2024, '4k') == str(k)
    f, _ = qbit([{'name': 'Dune.Part.Two.2024.1080p.BluRay.x264-GRP'}])
    assert movie_dupes.in_qbit('Dune: Part Two', 2024, f, 0, '4k') == {'hit': None}
    assert movie_dupes.in_qbit('Dune: Part Two', 2024, f, 0) == {'hit': 'Dune.Part.Two.2024.1080p.BluRay.x264-GRP'}
    f, _ = qbit([{'name': 'Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX-GRP'}])
    assert movie_dupes.in_qbit('Dune: Part Two', 2024, f, 0, '4k')['hit']


def test_route_files_a_4k_remux_grab_under_4k_only(tmp_path, monkeypatch):
    video = _layout(tmp_path, monkeypatch)
    (video / 'Movies' / 'Dune- Part Two (2024)').mkdir()
    cfg = movie_import.naming()
    lib = str(video / 'Recently Released')
    rel = 'Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX-GRP'
    assert movie_import.route(lib, rel, 'a.mkv', 'Dune: Part Two', 2024, cfg, only_4k=True) == str(video / '4K')
    assert movie_import.route(lib, rel, 'a.mkv', 'Dune: Part Two', 2024, cfg) == str(video / 'Movies')   # others unchanged


def test_owns_only_its_grabs(w):
    remux4k._record_grab('abc', {'tmdb_id': 77, 'title': 'Sinners', 'year': 2025, 'release': 'r'}, None, 1.0)
    assert remux4k.owns('ABC') and not remux4k.owns('def')
    assert movie_import._is_4k_grab('abc') and not movie_import._is_4k_grab('zzz')


def test_import_finds_a_4k_grab_not_on_the_watchlist(w, monkeypatch, mv):  # noqa: F811
    remux4k._record_grab('abc', {'tmdb_id': 77, 'title': 'Sinners', 'year': 2025, 'release': 'r'}, None, 1.0)
    monkeypatch.setattr(gr_store, 'title_for', lambda t: None)
    assert movie_import._watch(77) == {'tmdb_id': 77, 'title': 'Sinners', 'year': 2025}


# ------------------------------------------------------------------ routes / minimal allowlist

@pytest.fixture
def api(w, monkeypatch, owner):  # noqa: F811
    ran = []
    monkeypatch.setattr(remux4k, 'run', lambda kind='scheduled', now=None: ran.append(kind) or {})
    monkeypatch.setitem(remux4k_routes._start, 'fn', lambda target: target())
    monkeypatch.setattr(remux4k, 'plex_sections', lambda: [{'id': 1, 'title': 'Movies'}, {'id': 4, 'title': '4K Movies'}])
    owner.ran = ran
    return owner


def test_allowlisted_in_minimal_mode():
    from app import minimal
    assert minimal.path_allowed(API, 'GET') and minimal.path_allowed(API, 'PUT')
    for sub in ('/status', '/run', '/preview'):
        assert minimal.path_allowed(API + sub, 'POST')


def test_get_put_run_and_preview(api):
    r = _req(api, 'get', API)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    d = r.get_json()
    assert d['settings']['min_seeders'] == 200 and d['libraries'][1] == {'id': 4, 'title': '4K Movies'}
    assert d['history'] == [] and d['grabs'] == []
    r = _req(api, 'put', API, {'max_age_hours': 48})
    assert r.status_code == 200 and r.get_json()['settings']['max_age_hours'] == 48
    r = _req(api, 'put', API, {'min_seeders': 0})
    assert r.status_code == 400 and 'min_seeders' in r.get_json()['errors']
    assert _req(api, 'post', API + '/run').status_code == 202
    assert _req(api, 'post', API + '/preview').status_code == 202 and api.ran == ['manual', 'preview']
    s = _req(api, 'get', API + '/status').get_json()
    assert set(s) == {'status', 'history', 'preview'}


def test_run_while_running_is_409(api, monkeypatch):
    monkeypatch.setattr(remux4k, 'running', lambda: True)
    assert _req(api, 'post', API + '/run').status_code == 409
