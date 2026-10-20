"""v202 the native movie watchlist (test only), movies.db created 0600, TFlix default + one-time migration."""
import json
import os
import sqlite3

import pytest

from app import instance, movie_settings, movie_watchlist
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req

W = '/api/movies/watchlist'


@pytest.fixture
def ms(tmp_path, monkeypatch, owner):  # noqa: F811
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(movie_settings.SEED_FILE))
    monkeypatch.setattr(instance, 'is_test', lambda: True)
    return owner


def test_add_list_and_remove_with_the_default_profile(ms):
    assert _req(ms, 'get', W + '/tmdb-ids').get_json() == {'tmdb_ids': []}          # never seeded
    r = _req(ms, 'post', W, {'tmdb_id': 438631, 'title': 'Dune', 'year': 2021})
    assert r.status_code == 201, r.get_json()
    profiles = {p['id']: p['name'] for p in _req(ms, 'get', '/api/settings/movies').get_json()['profiles']}
    assert profiles[r.get_json()['movie']['profile_id']] == 'TFlix'
    pid = next(i for i, n in profiles.items() if n == '2160p Remux')
    assert _req(ms, 'post', W, {'tmdb_id': 693134, 'title': 'Dune: Part Two', 'profile_id': pid}).status_code == 201
    assert sorted(_req(ms, 'get', W + '/tmdb-ids').get_json()['tmdb_ids']) == [438631, 693134]
    for bad in ({'tmdb_id': 'x', 'title': 'a'}, {'tmdb_id': 1}, {'tmdb_id': 1, 'title': 'a', 'profile_id': 999},
                {'tmdb_id': 1, 'title': 'a', 'radarr': 1}, {'tmdb_id': 1, 'title': 'a', 'year': 1}):
        assert _req(ms, 'post', W, bad).status_code == 400, bad
    assert _req(ms, 'delete', W + '/438631').status_code == 200
    assert _req(ms, 'delete', W + '/438631').status_code == 404
    assert _req(ms, 'get', W + '/tmdb-ids').get_json()['tmdb_ids'] == [693134]


def test_off_unless_the_test_instance(ms, monkeypatch):
    monkeypatch.setattr(instance, 'is_test', lambda: False)
    assert _req(ms, 'get', W + '/tmdb-ids').status_code == 404
    assert _req(ms, 'post', W, {'tmdb_id': 1, 'title': 'a'}).status_code == 404


def test_writes_need_the_signed_in_owner(ms):
    from flask import jsonify
    from app import tv_settings
    tv_settings._cfg['signed_in_owner'] = lambda: (None, (jsonify({'error': 'Sign in'}), 401))
    try:
        assert _req(ms, 'post', W, {'tmdb_id': 1, 'title': 'a'}).status_code == 401
        assert _req(ms, 'delete', W + '/1').status_code == 401
    finally:
        tv_settings._cfg.pop('signed_in_owner', None)


def test_nothing_touches_radarr():
    src = open(movie_watchlist.__file__).read()
    assert 'import requests' not in src and 'radarr_' not in src.lower()


def test_the_db_is_created_0600_before_sqlite_opens_it(tmp_path, monkeypatch):
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'm' / 'movies.db'))
    seen = []
    real = sqlite3.connect

    def spy(path, *a, **k):
        seen.append(oct(os.stat(path).st_mode & 0o777))
        return real(path, *a, **k)
    monkeypatch.setattr(movie_settings.sqlite3, 'connect', spy)
    old = os.umask(0o022)
    try:
        movie_settings.profiles()
    finally:
        os.umask(old)
    assert seen[0] == '0o600'


def _old_store(tmp_path, monkeypatch, changed=False):
    """A movies.db from 1b650df: seeded from the export with 'Any' as the default."""
    db = tmp_path / 'old' / 'movies.db'
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(db))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(movie_settings.SEED_FILE))
    movie_settings.profiles()
    c = sqlite3.connect(db)
    c.execute("UPDATE profiles SET is_default = (name = 'Any')")
    c.execute("DELETE FROM meta WHERE key = 'default_mig_tflix'")
    if changed:
        c.execute("INSERT INTO meta (key, value) VALUES ('default_changed', 'true')")
    c.commit()
    c.close()


def test_an_old_store_s_any_default_becomes_tflix_once(tmp_path, monkeypatch):
    _old_store(tmp_path, monkeypatch)
    assert [p['name'] for p in movie_settings.profiles() if p['default']] == ['TFlix']
    anyp = next(p for p in movie_settings.profiles() if p['name'] == 'Any')
    movie_settings.save_profile({'name': 'Any', 'items': anyp['items'], 'cutoff': anyp['cutoff'], 'default': True}, anyp['id'])
    assert [p['name'] for p in movie_settings.profiles() if p['default']] == ['Any']       # never redone


def test_the_owner_s_own_default_is_never_migrated(tmp_path, monkeypatch):
    _old_store(tmp_path, monkeypatch, changed=True)
    assert [p['name'] for p in movie_settings.profiles() if p['default']] == ['Any']
