"""v253 Settings > Automation > Anticipated (prod 1b4ea74 anticipated_watchlist.py, native: shows via the TV Add,
movies onto the native watchlist). Trakt, TMDB, Plex, tv.db and the movie watchlist are all replaced here."""
import pytest
from flask import jsonify

from app import minimal, movie_settings
from app.automation import antic, antic_routes, gr_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req

API = '/api/settings/automation/anticipated'
NOW = 1_800_000_000.0


def show(tmdb, tvdb=None, title=None, year=2027):
    return {'list_count': 100, 'show': {'title': title or f'Show {tmdb}', 'year': year,
                                        'ids': {'tmdb': tmdb, 'tvdb': tvdb if tvdb is not None else tmdb + 1000}}}


def movie(tmdb, title=None, year=2027):
    return {'list_count': 100, 'movie': {'title': title or f'Movie {tmdb}', 'year': year, 'ids': {'tmdb': tmdb}}}


class World:
    def __init__(self):
        self.trakt = {'shows': [], 'movies': []}
        self.busy = set()
        self.trakt_calls = []
        self.lang = {}
        self.released = {}
        self.plex = {'movies': {'x|1': {}}, 'tv': {'y|1': {}}, 'tv_by_tmdb': {}, 'tv_by_tvdb': {}, 'movies_by_tmdb': {}}
        self.plex_error = False
        self.tv_db = set()
        self.watchlist = set()
        self.grabbed = set()
        self.tv_adds = []
        self.movie_adds = []


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setitem(gr_store._cfg, 'db_path', str(tmp_path / 'automation.db'))
    monkeypatch.setattr(antic, '_legacy', antic.defaults)
    world = World()

    def fetch(media, cid, page, limit):
        world.trakt_calls.append((media, page, limit))
        if media in world.busy:
            raise antic.Busy()
        return world.trakt[media][:limit]

    def plex():
        if world.plex_error:
            raise RuntimeError('plex down')
        return world.plex

    def tv_add(tmdb, path_id, monitor, profile_id, search):
        world.tv_adds.append((tmdb, path_id, monitor, profile_id, search))
        world.tv_db.add(tmdb)
        return {'ok': True, 'series_id': len(world.tv_adds)}

    def movie_add(e, profile_id, search, available_at, now):
        world.movie_adds.append((e['tmdb'], profile_id, search, available_at))
        world.watchlist.add(e['tmdb'])
        return {'ok': True}

    monkeypatch.setattr(antic, 'trakt_client_id', lambda: 'cid')
    monkeypatch.setattr(antic, 'fetch_trakt', fetch)
    monkeypatch.setattr(antic, 'show_details', lambda t: {'language': world.lang.get(t, '')})
    monkeypatch.setattr(antic, 'movie_details', lambda t: {'language': world.lang.get(t, ''), 'available_at': world.released.get(t)})
    monkeypatch.setattr(antic, 'plex_index', plex)
    monkeypatch.setattr(antic, 'in_tv_db', lambda tmdb, tvdb: tmdb in world.tv_db)
    monkeypatch.setattr(antic, 'on_watchlist', lambda tmdb: tmdb in world.watchlist)
    monkeypatch.setattr(antic, 'movie_grabbed', lambda tmdb: tmdb in world.grabbed)
    monkeypatch.setattr(antic, 'libraries', lambda: [{'id': 'p_tv', 'name': 'TV'}, {'id': 'p_kids', 'name': 'Kids TV'}])
    monkeypatch.setattr(antic, 'tv_add', tv_add)
    monkeypatch.setattr(antic, 'movie_add', movie_add)
    return world


def on(**groups):
    body = {k: {'enabled': True, **v} for k, v in groups.items()}
    antic.save(body)


# ------------------------------------------------------------------ settings

def test_defaults_are_prods_values(w):
    s = antic.settings()
    assert s['interval_hours'] == 2 and s['when_busy'] == 'skip' and s['history_kept'] == 200
    sh, mv = s['shows'], s['movies']
    assert sh == {'enabled': False, 'limit': 20, 'profile_id': None, 'search_on_add': True, 'languages': ['en'],
                  'allow_unknown_language': True, 'skip_plex': True, 'skip_if_plex_unreadable': True,
                  'no_readd': True, 'page': 1, 'path_id': None, 'monitor': 'all', 'require_tvdb': True}
    assert mv['enabled'] is False and mv['limit'] == 20 and mv['languages'] == [] and 'monitor' not in mv
    assert 'require_tvdb' not in mv and 'path_id' not in mv


def test_validation(w):
    for bad in ({'shows': {'limit': 41}}, {'shows': {'limit': 0}}, {'interval_hours': 25}, {'when_busy': 'wait'},
                {'history_kept': 100}, {'shows': {'monitor': 'some'}}, {'shows': {'languages': ['xx']}},
                {'movies': {'monitor': 'all'}}, {'movies': {'require_tvdb': True}}, {'radarr': 1}, None, [1],
                {'shows': 'on'}, {'shows': {'page': 0}}):
        with pytest.raises(antic.Invalid):
            antic.save(bad)
    antic.save({'shows': {'limit': 40, 'languages': ['ja', 'en']}, 'interval_hours': 24, 'history_kept': 500})
    s = antic.settings()
    assert s['shows']['limit'] == 40 and s['shows']['languages'] == ['en', 'ja'] and s['interval_hours'] == 24


def test_legacy_prod_keys_import_off(tmp_path, monkeypatch):
    monkeypatch.setitem(gr_store._cfg, 'db_path', str(tmp_path / 'a.db'))

    class S:
        vals = {'automation.anticipated_watchlist.limit': 12, 'automation.anticipated_watchlist.search_on_add': False,
                'automation.anticipated_watchlist.tv_allowed_languages': ['EN', 'fr'], 'automation.interval_hours': 6,
                'automation.anticipated_watchlist.enabled': True}

        def get(self, k, d=None):
            return self.vals.get(k, d)
    import app.settings_manager as sm
    monkeypatch.setattr(sm, 'SettingsFile', S)
    s = antic.settings()
    assert s['shows']['limit'] == 12 and s['movies']['limit'] == 12 and s['shows']['search_on_add'] is False
    assert s['shows']['languages'] == ['en', 'fr'] and s['interval_hours'] == 6
    assert s['shows']['enabled'] is False and s['movies']['enabled'] is False
    assert s['shows']['no_readd'] is True and s['movies']['no_readd'] is True      # v259 PM ruling


# ------------------------------------------------------------------ shows

def test_adds_top_shows_natively_with_the_settings(w):
    w.trakt['shows'] = [show(1), show(2)]
    w.lang = {1: 'en', 2: 'en'}
    on(shows={'monitor': 'all', 'path_id': 'p_kids', 'profile_id': 7, 'search_on_add': False})
    r = antic.run('scheduled', NOW)
    assert r['shows']['added'] == 2 and r['shows']['checked'] == 2
    assert w.tv_adds == [(1, 'p_kids', 'all', 7, False), (2, 'p_kids', 'all', 7, False)]
    assert w.trakt_calls == [('shows', 1, 20)]
    h = antic.history()
    assert [x['title'] for x in h] == ['Show 1', 'Show 2'] or [x['title'] for x in h] == ['Show 2', 'Show 1']
    assert all(x['kind'] == 'shows' and x['added_at'] == NOW for x in h)


def test_limit_and_page(w):
    w.trakt['shows'] = [show(i) for i in range(1, 31)]
    w.lang = {i: 'en' for i in range(1, 31)}
    on(shows={'limit': 5, 'page': 3})
    r = antic.run('scheduled', NOW)
    assert w.trakt_calls == [('shows', 3, 5)] and r['shows']['checked'] == 5 and len(w.tv_adds) == 5


def test_first_library_is_the_default(w):
    w.trakt['shows'] = [show(1)]
    w.lang = {1: 'en'}
    on(shows={})
    antic.run('scheduled', NOW)
    assert w.tv_adds[0][1] == 'p_tv'


def test_each_show_skip_rule(w):
    w.trakt['shows'] = [
        {'show': {'title': 'No ids', 'ids': {}}},           # no TMDB id
        show(2, tvdb=0),                                     # no TVDB id
        show(3), show(4), show(5), show(6),
    ]
    w.lang = {2: 'en', 3: 'fr', 4: 'en', 5: 'en', 6: 'en'}
    w.plex['tv_by_tvdb'] = {1004: {}}                        # 4 is in Plex (by TVDB)
    w.tv_db.add(5)                                           # 5 is already in tv.db
    on(shows={})
    r = antic.run('scheduled', NOW)['shows']
    assert r['skipped'] == {'no_tmdb': 1, 'no_tvdb': 1, 'language': 1, 'plex': 1, 'existing': 1, 'removed': 0,
                            'grabbed': 0}
    assert [a[0] for a in w.tv_adds] == [6] and r['checked'] == 6


def test_rules_switched_off(w):
    w.trakt['shows'] = [show(2, tvdb=0), show(4)]
    w.lang = {2: 'en', 4: 'en'}
    w.plex['tv_by_tmdb'] = {'4': {}}
    on(shows={'require_tvdb': False, 'skip_plex': False})
    antic.run('scheduled', NOW)
    assert [a[0] for a in w.tv_adds] == [2, 4]


def test_languages_none_means_any_and_unknown(w):
    w.trakt['shows'] = [show(1), show(2), show(3)]
    w.lang = {1: 'en', 2: 'ko'}                              # 3: unknown
    on(shows={'languages': ['en'], 'no_readd': False})        # re-runs below re-add
    r = antic.run('scheduled', NOW)['shows']
    assert [a[0] for a in w.tv_adds] == [1, 3] and r['skipped']['language'] == 1
    w.tv_db.clear(); w.tv_adds.clear()
    antic.save({'shows': {'allow_unknown_language': False}})
    antic.run('scheduled', NOW)
    assert [a[0] for a in w.tv_adds] == [1]
    w.tv_db.clear(); w.tv_adds.clear()
    antic.save({'shows': {'languages': []}})
    antic.run('scheduled', NOW)
    assert [a[0] for a in w.tv_adds] == [1, 2, 3]


def test_readd_toggle(w):
    w.trakt['shows'] = [show(1)]
    w.lang = {1: 'en'}
    on(shows={'no_readd': False})
    antic.run('scheduled', NOW)
    w.tv_db.clear()                                          # the owner deleted it
    antic.run('scheduled', NOW + 10)
    assert [a[0] for a in w.tv_adds] == [1, 1]               # off (prod's behaviour): re-added
    w.tv_db.clear()
    antic.save({'shows': {'no_readd': True}})
    r = antic.run('scheduled', NOW + 20)['shows']
    assert len(w.tv_adds) == 2 and r['skipped']['removed'] == 1


def test_dont_readd_removed_is_on_by_default(w):
    w.trakt['shows'] = [show(1)]
    w.trakt['movies'] = [movie(2)]
    w.lang = {1: 'en'}
    on(shows={}, movies={})
    antic.run('scheduled', NOW)
    w.tv_db.clear(); w.watchlist.clear()                     # the owner removed both
    r = antic.run('scheduled', NOW + 10)
    assert len(w.tv_adds) == 1 and len(w.movie_adds) == 1
    assert r['shows']['skipped']['removed'] == 1 and r['movies']['skipped']['removed'] == 1


def test_saved_settings_without_the_key_read_on_and_an_explicit_value_is_kept(w):
    import json as _json
    d = antic.defaults()
    for kind in antic.KINDS:
        del d[kind]['no_readd']                              # saved before the key existed
    d['shows']['limit'] = 7
    with antic.connect() as conn:
        antic._set_meta(conn, 'antic_settings', d)
    s = antic.settings()
    assert s['shows']['no_readd'] is True and s['movies']['no_readd'] is True and s['shows']['limit'] == 7
    antic.save({'shows': {'limit': 8}})                      # an unrelated save doesn't freeze the default
    with antic.connect() as conn:
        raw = antic._meta(conn, 'antic_settings', None)
    assert 'no_readd' not in raw['shows'] and 'no_readd' not in raw['movies']
    antic.save({'movies': {'no_readd': False}})              # explicit off
    antic.save({'shows': {'limit': 9}, 'interval_hours': 4})
    s = antic.settings()
    assert s['movies']['no_readd'] is False and s['shows']['no_readd'] is True
    with antic.connect() as conn:
        raw = antic._meta(conn, 'antic_settings', None)
    assert raw['movies']['no_readd'] is False and 'no_readd' not in raw['shows']
    antic.save({'shows': {'no_readd': True}})                # explicit on is kept too
    with antic.connect() as conn:
        assert antic._meta(conn, 'antic_settings', None)['shows']['no_readd'] is True
    assert _json.dumps(antic.settings())


def test_first_load_does_not_freeze_the_readd_default(w):
    antic.settings()
    with antic.connect() as conn:
        raw = antic._meta(conn, 'antic_settings', None)
    assert 'no_readd' not in raw['shows'] and 'no_readd' not in raw['movies']


def test_readd_memory_matches_by_tvdb_too(w):
    on(shows={})
    w.trakt['shows'] = [show(1, tvdb=555)]
    w.lang = {1: 'en'}
    antic.run('scheduled', NOW)
    assert antic.seen('shows', 999, 555) and not antic.seen('movies', 1, None)


def test_plex_unreadable_skips_the_run_or_not(w):
    w.trakt['shows'] = [show(1)]
    w.lang = {1: 'en'}
    w.plex_error = True
    on(shows={})
    r = antic.run('scheduled', NOW)
    assert r['shows']['error'] == "Plex can't be read" and w.tv_adds == [] and w.trakt_calls == []
    w.plex_error = False
    w.plex = {'movies': {}, 'tv': {}}                        # empty counts as unreadable
    assert antic.run('scheduled', NOW)['shows']['error'] == "Plex can't be read"
    antic.save({'shows': {'skip_if_plex_unreadable': False}})
    w.plex_error = True
    r = antic.run('scheduled', NOW)
    assert r['shows']['added'] == 1


def test_busy_skip_or_retry(w):
    w.busy.add('shows')
    on(shows={})
    antic.run('scheduled', NOW)
    st = antic.state()
    assert st['last_result']['shows']['error'] == 'Trakt is busy' and st['retry_at'] is None
    assert antic.next_run() == NOW + 2 * 3600
    antic.save({'when_busy': 'retry'})
    antic.run('scheduled', NOW)
    assert antic.state()['retry_at'] == NOW + 15 * 60 and antic.next_run() == NOW + 15 * 60
    w.busy.clear()
    antic.run('scheduled', NOW + 15 * 60)
    assert antic.state()['retry_at'] is None


def test_schedule_uses_its_own_interval(w):
    on(shows={})
    antic.save({'interval_hours': 6})
    antic.run('scheduled', NOW)
    assert antic.next_run() == NOW + 6 * 3600
    assert not antic.due(NOW + 6 * 3600 - 1) and antic.due(NOW + 6 * 3600)
    antic.run('manual', NOW + 100)                           # Run now doesn't move the schedule
    assert antic.next_run() == NOW + 6 * 3600
    antic.save({'shows': {'enabled': False}})
    assert antic.next_run() is None


def test_run_now_runs_when_off_and_scheduled_does_not(w):
    w.trakt['shows'] = [show(1)]
    w.trakt['movies'] = [movie(9)]
    w.lang = {1: 'en'}
    assert antic.run('scheduled', NOW) == {'shows': None, 'movies': None, 'error': None}
    assert w.trakt_calls == []
    r = antic.run('manual', NOW)
    assert r['shows']['added'] == 1 and r['movies']['added'] == 1


def test_trakt_not_connected(w, monkeypatch):
    monkeypatch.setattr(antic, 'trakt_client_id', lambda: '')
    on(shows={})
    assert antic.run('scheduled', NOW)['error'] == 'Trakt not connected'


def test_history_kept(w):
    antic.save({'history_kept': 50})
    on(shows={'languages': []})
    for i in range(60):
        w.trakt['shows'] = [show(100 + i)]
        antic.run('scheduled', NOW + i)
    h = antic.history()
    assert len(h) == 50 and h[0]['tmdb_id'] == 159
    assert antic.seen('shows', 100, None)                    # the re-add memory isn't trimmed


# ------------------------------------------------------------------ movies

def test_movies_go_to_the_native_watchlist(w):
    w.trakt['movies'] = [movie(1), movie(2), movie(3), movie(4), movie(5), {'movie': {'title': 'x', 'ids': {}}}]
    w.plex['movies_by_tmdb'] = {2: {}}
    w.plex['movies'] = {'movie 3|2027': {}}                  # title|year match, prod :306
    w.watchlist.add(4)
    w.grabbed.add(5)
    w.released = {1: NOW - 10}
    on(movies={'profile_id': 3})
    r = antic.run('scheduled', NOW)['movies']
    assert w.movie_adds == [(1, 3, True, NOW - 10)]
    assert r['skipped'] == {'no_tmdb': 1, 'plex': 2, 'existing': 1, 'grabbed': 1, 'language': 0, 'no_tvdb': 0,
                            'removed': 0}
    assert w.trakt_calls == [('movies', 1, 20)]


def test_movie_languages_default_any(w):
    w.trakt['movies'] = [movie(1), movie(2)]
    w.lang = {1: 'en', 2: 'ja'}
    on(movies={'no_readd': False})                           # the re-run below re-adds
    antic.run('scheduled', NOW)
    assert [a[0] for a in w.movie_adds] == [1, 2]
    w.watchlist.clear(); w.movie_adds.clear()
    antic.save({'movies': {'languages': ['en']}})
    antic.run('scheduled', NOW)
    assert [a[0] for a in w.movie_adds] == [1]


def test_movie_readd_toggle(w):
    w.trakt['movies'] = [movie(1)]
    on(movies={'no_readd': True})
    antic.run('scheduled', NOW)
    w.watchlist.clear()
    r = antic.run('scheduled', NOW + 1)['movies']
    assert len(w.movie_adds) == 1 and r['skipped']['removed'] == 1


def test_groups_are_independent(w):
    w.trakt['shows'] = [show(1)]
    w.trakt['movies'] = [movie(1)]
    w.lang = {1: 'en'}
    w.busy.add('movies')
    on(shows={}, movies={})
    r = antic.run('scheduled', NOW)
    assert r['shows']['added'] == 1 and r['movies']['error'] == 'Trakt is busy'


def test_movie_add_real_watchlist_and_search_when_released(tmp_path, monkeypatch):
    """The real native path: movies.db watchlist row; released + Search when added: due on the scanner's next pass."""
    from app import movie_scanner, movie_watchlist
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(tmp_path / 'no-seed.json'))
    e = {'tmdb': 77, 'tvdb': None, 'title': 'Dune Messiah', 'year': 2026}
    assert antic.movie_add(e, None, True, NOW - 5, NOW)['ok']
    assert 77 in movie_watchlist.tmdb_ids()
    with movie_settings.connect() as conn:
        row = conn.execute('SELECT next_search_at FROM movie_scan WHERE tmdb_id = 77').fetchone()
    assert row['next_search_at'] == NOW
    e2 = {'tmdb': 78, 'tvdb': None, 'title': 'Unreleased', 'year': 2028}
    assert antic.movie_add(e2, None, True, None, NOW)['ok']
    with movie_settings.connect() as conn:
        movie_scanner.ensure(conn)
        assert conn.execute('SELECT 1 FROM movie_scan WHERE tmdb_id = 78').fetchone() is None   # waits for release


def test_tv_add_passes_search_through(monkeypatch):
    from app import tv_calendar
    got = {}
    monkeypatch.setattr(tv_calendar, 'add', lambda *a, **k: got.update(k) or {'ok': True, 'series_id': 1})
    antic.tv_add(5, 'p', 'all', None, False)
    assert got == {'monitor': 'all', 'profile_id': None, 'search': False}


# ------------------------------------------------------------------ routes

@pytest.fixture
def api(w, monkeypatch, owner):  # noqa: F811
    ran = []
    monkeypatch.setattr(antic, 'run', lambda kind='scheduled', now=None: ran.append(kind) or {})
    monkeypatch.setitem(antic_routes._start, 'fn', lambda target: target())
    monkeypatch.setattr(antic, 'tv_profiles', lambda: [{'id': 1, 'name': 'HD-1080p', 'default': True}])
    monkeypatch.setattr(antic, 'movie_profiles', lambda: [{'id': 4, 'name': 'TFlix', 'default': True}])
    owner.ran = ran
    return owner


def test_allowlisted_in_minimal_mode():
    assert minimal.path_allowed(API, 'GET') and minimal.path_allowed(API, 'PUT')
    for sub in ('/status', '/run'):
        assert minimal.path_allowed(API + sub, 'POST')


def test_get_put_and_run(api):
    r = _req(api, 'get', API)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    d = r.get_json()
    assert d['settings']['shows']['enabled'] is False and d['libraries'][0]['name'] == 'TV'
    assert d['monitor'][0] == 'all' and 'en' in d['languages'] and d['history'] == []
    assert d['profiles'][0]['name'] == 'HD-1080p' and d['movie_profiles'][0]['name'] == 'TFlix'
    r = _req(api, 'put', API, {'shows': {'enabled': True, 'limit': 10}})
    assert r.status_code == 200 and r.get_json()['settings']['shows']['limit'] == 10
    assert r.get_json()['status']['next_run']
    r = _req(api, 'put', API, {'shows': {'limit': 99}})
    assert r.status_code == 400 and r.get_json()['errors'] == {'shows.limit': 'Invalid'}
    assert _req(api, 'post', API + '/run').status_code == 202 and api.ran == ['manual']
    assert _req(api, 'get', API + '/status').status_code == 200


def test_implicit_lan_owner_gets_401(api):
    from app import tv_settings
    tv_settings._cfg['signed_in_owner'] = lambda: (None, (jsonify({'error': 'Sign in'}), 401))
    try:
        assert _req(api, 'put', API, {'shows': {'enabled': True}}).status_code == 401
        assert _req(api, 'post', API + '/run').status_code == 401
        assert _req(api, 'get', API).status_code == 200
        assert api.ran == [] and antic.settings()['shows']['enabled'] is False
    finally:
        tv_settings._cfg.pop('signed_in_owner', None)


def test_non_owner_gets_nothing(api, monkeypatch):
    from app import tv_settings
    monkeypatch.setitem(tv_settings._cfg, 'owner_or_error', lambda: (None, (jsonify({'error': 'Forbidden'}), 403)))
    assert _req(api, 'get', API).status_code == 403
    assert _req(api, 'post', API + '/run').status_code == 403


def test_run_while_running_is_409(api, monkeypatch):
    monkeypatch.setattr(antic, 'running', lambda: True)
    assert _req(api, 'post', API + '/run').status_code == 409


# ------------------------------------------------------------------ v257: the v256 cadence and availability

def test_search_when_added_goes_through_the_v256_on_add_setting(monkeypatch):
    """Shows: Search when added hands the new show to tv_search.on_added (tv_calendar._search_on_add), which
    honors Settings' On add: Search now queues it, Wait for sweep leaves it to the sweep. Never a direct search."""
    from app import tv_calendar, tv_search
    got = []
    monkeypatch.setattr(tv_search, 'on_added', lambda sid, now=None: got.append((sid, now)) or 0)
    tv_calendar._search_on_add(7, NOW)
    assert got == [(7, NOW)]
    monkeypatch.undo()
    monkeypatch.setattr(tv_search, 'auto_on', lambda: True)
    monkeypatch.setattr(tv_search, 'cadence', lambda: {'air_searches': True, 'sweep_enabled': True, 'on_add': 'wait_for_sweep'})
    assert tv_search.on_added(7, NOW) == 0              # nothing queued ahead of the sweep (no tv.db touched)


@pytest.mark.parametrize('mode,expect', [('announced', 0.0), ('in_cinemas', 'cinema'), ('released', None)])
def test_movie_details_follow_the_v256_availability(monkeypatch, mode, expect):
    from app import movie_scanner, movie_settings as ms
    payload = {'original_language': 'en', 'release_dates': {'results': [
        {'iso_3166_1': 'US', 'release_dates': [{'type': 3, 'release_date': '2026-12-18T00:00:00.000Z'}]}]}}
    monkeypatch.setattr(movie_scanner, '_tmdb', lambda t: payload)
    monkeypatch.setattr(ms, 'availability', lambda conn=None: mode)
    d = antic.movie_details(1)
    assert d['language'] == 'en'
    if expect == 'cinema':
        assert d['available_at'] == movie_scanner._day_start('2026-12-18')
    else:
        # released: no digital/physical date, so cinema + 90 days
        want = expect if mode == 'announced' else movie_scanner._day_start('2026-12-18') + movie_scanner.CINEMA_TO_RELEASE_S
        assert d['available_at'] == want


def test_an_unavailable_movie_is_never_searched_early(tmp_path, monkeypatch):
    """Even queued, run_due skips a watchlist movie until it is available (v256), so Anticipated can't grab early."""
    from app import movie_scanner
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(tmp_path / 'no-seed.json'))
    e = {'tmdb': 90, 'tvdb': None, 'title': 'Future', 'year': 2027}
    assert antic.movie_add(e, None, True, None, NOW)['ok']
    with movie_settings.connect() as conn:
        movie_scanner.ensure(conn)
        movie_scanner._set(conn, 90, next_search_at=NOW)   # even if something queued it
    searched = []
    monkeypatch.setattr(movie_scanner, 'search_movie', lambda tid, origin='auto', now=None: searched.append(tid) or {})
    assert movie_scanner.run_due(NOW + 1)['movies'] == 0 and searched == []


def test_trakt_goes_through_trakt_http(monkeypatch):
    """v255: every Trakt call uses app/trakt_http (headers, identifying User-Agent, logged failures)."""
    import requests
    from app import trakt_http
    seen = {}

    class R:
        def __init__(self, code, body=None):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    def fake(url, headers=None, params=None, timeout=None, **kw):
        seen.update(url=url, headers=headers, params=params, timeout=timeout)
        return R(200, [show(1)])
    monkeypatch.setattr(requests, 'get', fake)
    out = antic.fetch_trakt('movies', 'cid', 2, 7)
    assert out == [show(1)] and seen['url'] == 'https://api.trakt.tv/movies/anticipated'
    assert seen['headers'] == trakt_http.headers('cid') and seen['headers']['User-Agent'] == trakt_http.USER_AGENT
    assert seen['params'] == {'page': '2', 'limit': '7', 'extended': 'full'}
    monkeypatch.setattr(requests, 'get', lambda *a, **k: R(429))
    with pytest.raises(antic.Busy):
        antic.fetch_trakt('shows', 'cid', 1, 20)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: R(500))
    with pytest.raises(RuntimeError):
        antic.fetch_trakt('shows', 'cid', 1, 20)
