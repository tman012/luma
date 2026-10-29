"""v198 Settings > Movies: profiles, trackers, preferred groups and monitoring in movies.db (not tv.db);
nothing reads Radarr; movie automations are off on the test instance."""
import os
import sqlite3

import pytest

from app import instance, movie_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import _client
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req

API = '/api/settings/movies'


@pytest.fixture
def ms(tmp_path, monkeypatch, owner):  # noqa: F811
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(tmp_path / 'no-seed.json'))
    monkeypatch.setattr(movie_settings, '_tracker_records', lambda: [{'id': 1, 'name': 'BHD', 'type': 'beyondhd'},
                                                                      {'id': 2, 'name': 'PHD', 'type': 'privatehd'}])
    return owner


def test_a_fresh_store_has_one_any_profile_and_is_off(ms, tmp_path):
    r = _req(ms, 'get', API)
    assert r.status_code == 200
    d = r.get_json()
    assert d['monitoring'] is False and d['preferred_groups'] == []
    assert [(p['name'], p['default']) for p in d['profiles']] == [('Any', True)]
    assert all(i['allowed'] for i in d['profiles'][0]['items'])
    assert d['trackers'] == [{'id': 1, 'name': 'BHD', 'enabled': False}, {'id': 2, 'name': 'PHD', 'enabled': False}]
    db = tmp_path / 'mdb' / 'movies.db'
    assert db.is_file() and oct(os.stat(db).st_mode & 0o777) == '0o600'
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {'profiles', 'meta', 'watchlist'}   # v202: the native watchlist
    assert str(db) != str(tv_store.db_path()) if hasattr(tv_store, 'db_path') else True


def test_put_saves_monitoring_groups_and_tracker_order(ms):
    r = _req(ms, 'put', API, {'monitoring': True, 'preferred_groups': ['NTb', ' FraMeSToR ', 'ntb'],
                               'trackers': [{'id': 2, 'enabled': True}, {'id': 1, 'enabled': False}]})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d['monitoring'] is True and d['preferred_groups'] == ['NTb', 'FraMeSToR']
    assert [(t['id'], t['enabled']) for t in d['trackers']] == [(2, True), (1, False)]
    for bad in ({'preferred_groups': ['g'] * 21}, {'preferred_groups': ['x' * 41]}, {'monitoring': 'yes'},
                {'trackers': [{'id': 9}]}, {'trackers': [{'id': 1}, {'id': 1}]}, {'radarr': 1}, {}):
        assert _req(ms, 'put', API, bad).status_code == 400, bad


def test_profiles_crud_with_cutoff_and_sizes(ms):
    q = [x['id'] for x in _req(ms, 'get', API).get_json()['qualities']]
    body = {'name': 'HD-1080p', 'items': [{'id': q[0], 'allowed': True}, {'id': q[1], 'allowed': True}],
            'cutoff': q[1], 'min_mb_per_min': 10, 'max_mb_per_min': 200}
    r = _req(ms, 'post', API + '/profiles', body)
    assert r.status_code == 201, r.get_json()
    p = r.get_json()
    assert (p['cutoff'], p['min_mb_per_min'], p['max_mb_per_min'], p['default']) == (q[1], 10, 200, False)
    assert len(p['items']) == len(q)                                 # every quality listed, in order
    assert _req(ms, 'post', API + '/profiles', {**body, 'name': 'hd-1080P'}).status_code == 400   # name taken
    assert _req(ms, 'put', f"{API}/profiles/{p['id']}", {**body, 'max_mb_per_min': 5}).status_code == 400
    assert _req(ms, 'put', f"{API}/profiles/{p['id']}", {**body, 'cutoff': q[5]}).status_code == 400
    assert _req(ms, 'put', f"{API}/profiles/{p['id']}", {**body, 'default': True}).get_json()['default'] is True
    anyp = next(x for x in _req(ms, 'get', API).get_json()['profiles'] if x['name'] == 'Any')
    assert anyp['default'] is False
    assert _req(ms, 'delete', f"{API}/profiles/{p['id']}").status_code == 409            # the default stays
    assert _req(ms, 'delete', f"{API}/profiles/{anyp['id']}").status_code == 200
    assert _req(ms, 'delete', f'{API}/profiles/999').status_code == 404


def test_writes_need_the_signed_in_owner(ms, minimal_app):  # noqa: F811
    from app import tv_settings
    from flask import jsonify
    tv_settings._cfg['signed_in_owner'] = lambda: (None, (jsonify({'error': 'Sign in'}), 401))
    try:
        assert _req(ms, 'put', API, {'monitoring': True}).status_code == 401
        assert _req(ms, 'post', API + '/profiles', {'name': 'x'}).status_code == 401
        assert _req(ms, 'get', API).status_code == 200
    finally:
        tv_settings._cfg.pop('signed_in_owner', None)


def test_nothing_reads_radarr():
    src = open(movie_settings.__file__).read().lower()
    assert 'radarr' not in src and 'requests' not in src


def test_roulette_follows_opt_in_on_test(monkeypatch):            # 2026-10-29 (Taylor): Roulette unlocked
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'movie_night')
    assert instance.writes_allowed('roulette') is False
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'roulette')
    assert instance.writes_allowed('roulette') is True


@pytest.mark.parametrize('job', ['upgrade_4k', 'franchise_completer'])
def test_movie_automations_are_off_on_test_even_when_opted_in(monkeypatch, job):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', job)
    assert instance.writes_allowed(job) is False
    monkeypatch.setattr(instance, 'is_test', lambda: False)                 # prod keeps them
    assert instance.writes_allowed(job) is True


def test_seeded_from_the_saved_export(ms, monkeypatch):
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(movie_settings.SEED_FILE))
    d = _req(ms, 'get', API).get_json()
    by = {p['name']: p for p in d['profiles']}
    assert len(by) == 9 and set(by) >= {'Any', 'SD', 'HD-720p', 'HD-1080p', 'Ultra-HD', 'HD - 720p/1080p', 'TFlix',
                                         '1080p Remux', '2160p Remux'}
    assert [p['name'] for p in d['profiles'] if p['default']] == ['TFlix']
    remux = by['2160p Remux']
    assert [i['id'] for i in remux['items'] if i['allowed']] == ['2160p-remux'] and remux['cutoff'] == '2160p-remux'
    assert (remux['min_mb_per_min'], remux['max_mb_per_min']) == (33.17, None)
    assert (by['TFlix']['cutoff'], by['TFlix']['max_mb_per_min']) == ('1080p-bluray', 100)
    assert [(t['id'], t['enabled']) for t in d['trackers']] == [(2, True), (1, True)]   # PHD (25) before BHD (40)
    raw = movie_settings.SEED_FILE.read_text().lower()
    assert 'http' not in raw and 'key' not in raw and 'password' not in raw
