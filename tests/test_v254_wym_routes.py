"""v254 What you're missing routes and Home rail on test (minimal) builds: the rail, dismiss, restore and
refresh are allowlisted; dismiss, restore, refresh and settings changes need the signed-in owner (implicit LAN:
401); settings round trip; the rail is a regular Home screen rail (last by default, reorderable, hideable,
the one show/hide), inside /api/home with the dismiss flag for the signed-in owner. Outside services are fakes."""
import pytest
from flask import jsonify

from app import daily_missing as dm, home_rails, instance, minimal, movie_settings, tv_settings
from app.automation import wym_store as ws
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client, plex  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.test_v254_wym_pool import svc  # noqa: F401

API = '/api/settings/automation/what-youre-missing'
RAIL = '/api/rails/what-youre-missing'
DENY = lambda: (None, (jsonify({'error': 'Sign in first', 'sign_in': True}), 401))   # noqa: E731  implicit LAN owner


@pytest.fixture
def wym(owner, svc, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    owner.svc = svc
    return owner


def test_allowlist():
    for p in (RAIL, RAIL + '/dismiss', RAIL + '/restore', RAIL + '/refresh', API, API + '/status'):
        assert minimal.path_allowed(p), p
    assert not minimal.path_allowed(RAIL + '/dismissed')
    assert minimal.path_allowed('/settings/automation')


def test_rail_get_needs_sign_in(minimal_app, svc):  # noqa: F811
    c = minimal_app.test_client()
    assert c.get(RAIL, environ_base=REMOTE, headers=HDR).status_code == 401


def test_rail_get_and_limit(wym):
    dm.build_pool(force=True)
    r = _req(wym, 'get', RAIL + '?limit=2')
    assert r.status_code == 200 and len(r.get_json()['movies']) == 2 and r.get_json()['title'] == "What you're missing"
    assert len(_req(wym, 'get', RAIL + '?limit=999').get_json()['movies']) == 6


def test_dismiss_restore_round_trip(wym):
    dm.build_pool(force=True)
    r = _req(wym, 'post', RAIL + '/dismiss', {'tmdb_id': 1})
    d = r.get_json()
    assert r.status_code == 200 and d['ok'] and 1 not in [m['tmdb_id'] for m in d['rail']['movies']]
    v = _req(wym, 'get', API).get_json()
    assert [m['tmdb_id'] for m in v['dismissed']] == [1] and v['dismissed'][0]['title'] == 'Heat'
    r = _req(wym, 'post', RAIL + '/restore', {'tmdb_id': 1})
    assert r.status_code == 200 and r.get_json()['restored'] and r.get_json()['dismissed'] == []
    for bad in ({}, {'tmdb_id': 0}, {'tmdb_id': 'x'}, {'tmdb_id': True}):
        assert _req(wym, 'post', RAIL + '/dismiss', bad).status_code == 400
        assert _req(wym, 'post', RAIL + '/restore', bad).status_code == 400


def test_changes_need_the_signed_in_owner(wym, monkeypatch):
    dm.build_pool(force=True)
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', DENY)
    assert _req(wym, 'post', RAIL + '/dismiss', {'tmdb_id': 1}).status_code == 401
    assert _req(wym, 'post', RAIL + '/restore', {'tmdb_id': 1}).status_code == 401
    assert _req(wym, 'post', RAIL + '/refresh').status_code == 401
    assert _req(wym, 'put', API, {'movies_shown': 10}).status_code == 401
    assert _req(wym, 'get', RAIL).status_code == 200 and _req(wym, 'get', API).status_code == 200
    assert dm.list_dismissed()['count'] == 0 and ws.settings()['movies_shown'] == 20 and wym.svc['started'] == []


def test_non_owners_get_no_settings(wym, monkeypatch):
    monkeypatch.setitem(tv_settings._cfg, 'owner_or_error', lambda: (None, (jsonify({'error': 'Forbidden'}), 403)))
    assert _req(wym, 'get', API).status_code == 403 and _req(wym, 'get', API + '/status').status_code == 403


def test_refresh_starts_a_background_rebuild(wym):
    r = _req(wym, 'post', RAIL + '/refresh')
    assert r.status_code == 202 and r.get_json()['ok'] and len(wym.svc['started']) == 1
    dm._build_lock.acquire()
    try:
        assert _req(wym, 'post', RAIL + '/refresh').status_code == 409
    finally:
        dm._build_lock.release()
    wym.svc['started'][0]()
    assert _req(wym, 'get', API + '/status').get_json()['status']['pool_size'] == 6


def test_settings_view_and_put(wym, monkeypatch):
    woke = []
    monkeypatch.setattr(dm, 'wake', lambda: woke.append(1))
    v = _req(wym, 'get', API).get_json()
    assert v['settings'] == ws.defaults() and len(v['trakt_lists']) == 8 and len(v['genres']) == 14
    assert v['languages'][0] == {'id': 'en', 'label': 'English'} and v['rebuild_hours'] == [2, 4, 6, 8, 12, 24]
    assert v['imdb_active'] is False and v['status']['on_home'] is True and v['dismissed'] == []
    r = _req(wym, 'put', API, {'exclude_genres': ['Horror'], 'year_to': 2020})
    assert r.status_code == 200 and r.get_json()['settings']['exclude_genres'] == ['Horror'] and woke == [1]
    r = _req(wym, 'put', API, {'year_from': 2021})
    assert r.status_code == 400 and r.get_json()['errors'] == {'year_from': 'Invalid'}


def test_settings_are_minimal_only(test_app, svc, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    c = _client(test_app)
    assert c.get(API, environ_base=REMOTE, headers=HDR).status_code == 404


# ---------------------------------------------------------------- Home screen

@pytest.fixture
def home(plex, svc, test_app, monkeypatch):  # noqa: F811
    monkeypatch.setattr(instance, 'is_test', lambda: True)
    monkeypatch.setitem(home_rails._cfg, 'trakt_connected', lambda: False)
    monkeypatch.setattr(dm, 'rail_on', lambda: home_rails.WYM_ID not in home_rails.load_prefs()['hidden'])
    from app import movie_watchlist
    monkeypatch.setattr(movie_watchlist, 'movies', lambda: [])
    dm.build_pool(force=True)
    return _client(test_app)


def _libs(c):
    return c.get('/api/home/libraries', environ_base=REMOTE).get_json()['libraries']


def _save(c, rows):
    return c.put('/api/home/libraries', json={'libraries': rows}, environ_base=REMOTE, headers={'Origin': 'http://localhost'})


def test_a_regular_rail_last_by_default(home):
    libs = _libs(home)
    assert libs[-1] == {'id': 'what_youre_missing', 'title': "What you're missing", 'type': 'movie', 'source': 'wym', 'enabled': True}
    rails = home.get('/api/home', environ_base=REMOTE).get_json()['rails']
    assert rails[-1]['id'] == 'what_youre_missing' and [c['tmdb_id'] for c in rails[-1]['items']] == [m['tmdb_id'] for m in dm.get_rail()['movies']]
    card = rails[-1]['items'][0]
    assert card['key'] == f"missing-{card['tmdb_id']}" and card['poster'].startswith(home_rails.TMDB_POSTER) and card['type'] == 'movie'
    assert 'dismiss' not in rails[-1]                             # full build: no owner session check passes


def test_a_saved_arrangement_picks_it_up_at_the_end(home):
    ids = [l['id'] for l in _libs(home) if l['id'] != 'what_youre_missing']
    home_rails.save_prefs({'order': list(reversed(ids)), 'hidden': [ids[0]]})
    libs = _libs(home)
    assert [l['id'] for l in libs] == list(reversed(ids)) + ['what_youre_missing'] and libs[-1]['enabled']


def test_reorder_and_hide_is_the_one_switch(home, monkeypatch):
    woke = []
    monkeypatch.setattr(dm, 'wake', lambda: woke.append(1))
    libs = _libs(home)
    rows = [{'id': 'what_youre_missing', 'enabled': False}] + [{'id': l['id'], 'enabled': True} for l in libs if l['id'] != 'what_youre_missing']
    assert _save(home, rows).status_code == 200
    libs2 = _libs(home)
    assert libs2[0]['id'] == 'what_youre_missing' and libs2[0]['enabled'] is False and woke == [1]
    assert all(r['id'] != 'what_youre_missing' for r in home.get('/api/home', environ_base=REMOTE).get_json()['rails'])
    assert dm.status()['on_home'] is False and dm.next_rebuild() is None
    rows[0]['enabled'] = True
    _save(home, rows)
    assert home.get('/api/home', environ_base=REMOTE).get_json()['rails'][0]['id'] == 'what_youre_missing'
    assert woke == [1, 1]


def test_home_rail_marks_dismiss_for_the_signed_in_owner(home, monkeypatch):
    monkeypatch.setattr(tv_settings, '_signed_in_owner', lambda: None)
    rail = home.get('/api/home', environ_base=REMOTE).get_json()['rails'][-1]
    assert rail['id'] == 'what_youre_missing' and rail['dismiss'] is True


def test_home_uses_the_cached_plex_index(home, monkeypatch):
    seen = []
    monkeypatch.setattr(dm, 'plex_index', lambda cached_only=False: seen.append(cached_only) or {})
    home.get('/api/home', environ_base=REMOTE)
    assert seen and all(seen)


def test_an_empty_rail_is_left_out_by_the_client_and_never_blocks(home, monkeypatch):
    dm._forget_memory()
    monkeypatch.setattr(dm, '_read_pool', lambda: None)
    rails = home.get('/api/home', environ_base=REMOTE).get_json()['rails']
    wr = [r for r in rails if r['id'] == 'what_youre_missing']
    assert wr == [] or wr[0]['items'] == []
