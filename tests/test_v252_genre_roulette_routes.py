"""v252 Genre Roulette routes: owner only; changes, runs and previews need the signed-in owner (implicit LAN
gets 401); minimal allowlist; settings round trip with validation; status and history; run and preview start
in the background (no outside service is touched: the engine is replaced)."""
import pytest
from flask import jsonify

from app import minimal, movie_settings
from app.automation import gr_engine, gr_routes, gr_select, gr_store as gs
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req

API = '/api/settings/automation/genre-roulette'


@pytest.fixture
def gr(tmp_path, monkeypatch, owner):  # noqa: F811
    monkeypatch.setitem(gs._cfg, 'db_path', str(tmp_path / 'automation.db'))
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(tmp_path / 'no-seed.json'))
    monkeypatch.setattr(gr_select, 'omdb_key_set', lambda: False)
    ran = []
    monkeypatch.setattr(gr_engine, 'run', lambda kind, **k: ran.append(kind) or {'status': 'done', 'grabbed': 0,
                                                                                 'wanted': 3, 'picks': []})
    monkeypatch.setitem(gr_routes._start, 'fn', lambda target: target())
    owner.ran = ran
    return owner


def test_allowlisted_in_minimal_mode():
    assert minimal.path_allowed(API, 'GET') and minimal.path_allowed(API, 'PUT')
    for sub in ('/status', '/run', '/preview'):
        assert minimal.path_allowed(API + sub, 'POST')
    assert minimal.path_allowed('/settings/automation')


def test_get_has_settings_genres_profiles_status_history(gr):
    r = _req(gr, 'get', API)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    d = r.get_json()
    assert d['settings']['enabled'] is False and d['settings']['time'] == '22:50'
    assert len(d['genres']) == 18 and 'Science Fiction' in d['genres']
    assert [p['name'] for p in d['profiles']] == ['Any'] and d['imdb_active'] is False
    assert d['status']['next_run'] is None and d['history'] == []


def test_put_round_trip_and_validation(gr):
    r = _req(gr, 'put', API, {'enabled': True, 'exclude_genres': ['Documentary', 'Horror'], 'min_votes': 1000})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d['settings']['enabled'] and d['settings']['exclude_genres'] == ['Documentary', 'Horror']
    assert d['status']['next_run']
    for bad in ({'min_votes': -1}, {'radarr': 1}, {'min_rating': 6.5}, {'library': 'Movies'}, None, [1]):
        r = _req(gr, 'put', API, bad)
        assert r.status_code == 400, bad
    assert _req(gr, 'get', API).get_json()['settings']['min_votes'] == 1000


def test_run_and_preview_start_in_the_background(gr):
    assert _req(gr, 'post', API + '/run').status_code == 202
    assert _req(gr, 'post', API + '/preview').status_code == 202
    assert gr.ran == ['manual', 'preview']
    s = _req(gr, 'get', API + '/status').get_json()
    assert s['preview']['running'] is False and s['preview']['result']['status'] == 'done'


def test_run_while_running_is_409(gr, monkeypatch):
    monkeypatch.setattr(gr_engine, 'running', lambda: {'running': True})
    assert _req(gr, 'post', API + '/run').status_code == 409


def test_changes_and_runs_need_the_signed_in_owner(gr):
    from app import tv_settings
    tv_settings._cfg['signed_in_owner'] = lambda: (None, (jsonify({'error': 'Sign in'}), 401))
    try:
        assert _req(gr, 'put', API, {'enabled': True}).status_code == 401
        assert _req(gr, 'post', API + '/run').status_code == 401
        assert _req(gr, 'post', API + '/preview').status_code == 401
        assert _req(gr, 'get', API).status_code == 200
        assert gr.ran == [] and gs.settings()['enabled'] is False
    finally:
        tv_settings._cfg.pop('signed_in_owner', None)


def test_non_owners_get_nothing(gr, monkeypatch):
    from app import tv_settings
    monkeypatch.setitem(tv_settings._cfg, 'owner_or_error', lambda: (None, (jsonify({'error': 'Forbidden'}), 403)))
    assert _req(gr, 'get', API).status_code == 403
    assert _req(gr, 'get', API + '/status').status_code == 403
    assert _req(gr, 'post', API + '/run').status_code == 403
