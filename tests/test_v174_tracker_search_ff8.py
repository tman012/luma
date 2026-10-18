"""v174 (Tester FF-8 on c669f76, PM): Trackers > Search keeps only Movie categories, with a real
owner-only editor that works on the luma-test (minimal) build.

* /api/trackers/search-settings (GET/PUT): the signed-in owner only (other admins 403, plain users 403,
  no session or the implicit LAN owner 401); allowed on minimal builds, where /api/settings is 404.
* Validation (here and on PUT /api/settings): a string of known Newznab movie categories, capped in
  length; junk, huge strings, other types and unknown keys are 400 and nothing is stored.
* PUT /api/settings with tracker_search: owner only too (admins 403).
* settings.json is backed up before a migration rewrites it.
* Explore's category expansion never int()s a huge digit string."""
import json
import os
import stat

import pytest

import app.main
import app.user_auth
from app import minimal
from app.settings_manager import (KNOWN_MOVIE_CATEGORIES, SettingsManager, validate_tracker_search)
from tests.test_trackers_api import LAN, ORIGIN, REMOTE, store  # noqa: F401

API = '/api/trackers/search-settings'


@pytest.fixture
def keep_setting():
    before = app.main.settings.get('tracker_search.movie_categories')
    yield
    app.main.settings.update({'tracker_search': {'movie_categories': before if isinstance(before, str) else '2000'}})


def _client(test_app, user=None, env=REMOTE):
    c = test_app.test_client()
    c.environ_base.update(env)
    c.environ_base.update({'HTTP_ORIGIN': 'http://localhost', 'HTTP_HOST': 'localhost'})
    if user:
        c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(user['id']))
    return c


@pytest.fixture
def users(test_app, store):  # noqa: F811
    owner = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    admin2 = app.user_auth.create_user('second', 'pin-1234567', is_admin=True)
    kid = app.user_auth.create_user('kid', 'pin-1234567')
    return {'owner': owner, 'admin2': admin2, 'kid': kid}


def _put(c, body, path=API):
    return c.put(path, data=json.dumps(body), content_type='application/json', headers=ORIGIN)


def test_the_route_is_open_on_minimal_builds_and_api_settings_is_not():
    for method in ('GET', 'PUT'):
        assert minimal.path_allowed(API, method)
    assert not minimal.path_allowed('/api/settings', 'GET')


def test_the_owner_reads_and_saves(test_app, users, keep_setting):
    c = _client(test_app, users['owner'])
    r = c.get(API)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    assert r.get_json()['known_categories'] == list(KNOWN_MOVIE_CATEGORIES)
    r = _put(c, {'movie_categories': ' 2000, 2045 ,2045'})
    assert r.status_code == 200 and r.get_json()['movie_categories'] == '2000,2045'
    assert app.main.settings.get('tracker_search.movie_categories') == '2000,2045'


def test_the_owner_saves_on_a_minimal_build(test_app, users, keep_setting, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    c = _client(test_app, users['owner'])
    assert c.get('/api/settings').status_code == 404               # why the card was dead (FF-8a)
    assert c.get(API).status_code == 200
    assert _put(c, {'movie_categories': '2040'}).status_code == 200
    assert app.main.settings.get('tracker_search.movie_categories') == '2040'


@pytest.mark.parametrize('who,status', [('admin2', 403), ('kid', 403), (None, 401)])
def test_anyone_else_is_refused_and_nothing_changes(test_app, users, keep_setting, who, status):
    before = app.main.settings.get('tracker_search.movie_categories')
    c = _client(test_app, users[who] if who else None)
    assert c.get(API).status_code == status
    assert _put(c, {'movie_categories': '2010'}).status_code == status
    assert _put(c, {'tracker_search': {'movie_categories': '2010'}}, '/api/settings').status_code in (status, 401, 403)
    assert app.main.settings.get('tracker_search.movie_categories') == before


def test_a_non_owner_admin_gets_403_on_api_settings_too(test_app, users, keep_setting):
    before = app.main.settings.get('tracker_search.movie_categories')
    r = _put(_client(test_app, users['admin2']), {'tracker_search': {'movie_categories': '2010'}}, '/api/settings')
    assert r.status_code == 403
    assert app.main.settings.get('tracker_search.movie_categories') == before


def test_the_implicit_lan_owner_is_refused(test_app, users, keep_setting):
    c = _client(test_app, None, LAN)
    assert c.get(API).status_code == 401
    assert _put(c, {'movie_categories': '2010'}).status_code == 401


BAD = ['abc', '', '   ', '2000;DROP', '2000,,2040', '2000,', '5000', '2000,5000', '1' * 100000, '9' * 5000,
       '2000,' * 30 + '2000', '0002000', '２０００', '-2000', '2000.0', '1e3']


@pytest.mark.parametrize('value', BAD + [2000, 2000.0, None, True, [2000], {'a': 1}])
def test_junk_is_400_and_nothing_is_stored(test_app, users, keep_setting, value):
    before = app.main.settings.get('tracker_search.movie_categories')
    c = _client(test_app, users['owner'])
    r = _put(c, {'movie_categories': value})
    assert r.status_code == 400 and 'movie_categories' in r.get_json()['fields']
    r = _put(c, {'tracker_search': {'movie_categories': value}}, '/api/settings')
    assert r.status_code == 400
    assert app.main.settings.get('tracker_search.movie_categories') == before


@pytest.mark.parametrize('body', [{'bogus_key': 1}, {'movie_categories': '2000', 'bogus_key': 1},
                                  {'max_age_days': 30}, {'limit': 100}, {'min_seeders': 1}])
def test_unknown_keys_are_400(test_app, users, keep_setting, body):
    c = _client(test_app, users['owner'])
    assert _put(c, body).status_code == 400
    assert _put(c, {'tracker_search': body}, '/api/settings').status_code == 400
    assert 'bogus_key' not in (app.main.settings.get('tracker_search') or {})


@pytest.mark.parametrize('body', ['[]', '"2000"', 'null', 'not json'])
def test_a_non_object_body_is_400(test_app, users, body):
    r = _client(test_app, users['owner']).put(API, data=body, content_type='application/json', headers=ORIGIN)
    assert r.status_code == 400


def test_validator_cases():
    assert validate_tracker_search({'movie_categories': '2000, 2040'}) == ({'movie_categories': '2000,2040'}, {})
    for v in BAD:
        assert validate_tracker_search({'movie_categories': v})[1], v
    assert validate_tracker_search('x')[1]
    assert validate_tracker_search({'nope': 1})[1] == {'nope': 'Unknown setting'}


def test_explore_expansion_never_int_s_a_huge_string():
    from app.indexer_api import _expand_movie_categories
    assert _expand_movie_categories('9' * 5000 + ',2040') == [2040]
    assert 2050 in _expand_movie_categories('2000')


# -------------------------------------------------------------- backup before the migration

def test_settings_json_is_backed_up_before_the_migration(tmp_path):
    p = tmp_path / 'settings.json'
    old = {'indexer': {'provider': 'prowlarr', 'movie_categories': '2000,2040'}}
    p.write_text(json.dumps(old))
    SettingsManager(str(p))
    baks = sorted(tmp_path.glob('settings.json.bak-*'))
    assert len(baks) == 1 and json.loads(baks[0].read_text()) == old       # the exact pre-migration file
    assert stat.S_IMODE(os.stat(baks[0]).st_mode) == 0o600
    assert json.loads(p.read_text())['tracker_search']['movie_categories'] == '2000,2040'
    SettingsManager(str(p))                                                # nothing to migrate: no new backup
    assert len(list(tmp_path.glob('settings.json.bak-*'))) == 1


def test_backups_are_capped(tmp_path):
    p = tmp_path / 'settings.json'
    for i in range(SettingsManager.MIGRATION_BACKUPS + 3):
        p.write_text(json.dumps({'indexer': {'movie_categories': f'20{i:02d}'}}))
        SettingsManager(str(p))
    assert len(list(tmp_path.glob('settings.json.bak-*'))) == SettingsManager.MIGRATION_BACKUPS


def test_a_fresh_install_makes_no_backup(tmp_path):
    SettingsManager(str(tmp_path / 'settings.json'))
    assert list(tmp_path.glob('settings.json.bak-*')) == []


def test_a_real_boot_logs_the_backup_line(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    (tmp_path / 'settings.json').write_text(json.dumps({'indexer': {'movie_categories': '2000,2040'}}))
    env = {'HOME': os.environ.get('HOME', '/tmp'), 'PATH': '/usr/bin:/bin', 'LUMA_MINIMAL': '1',
           'DATABASE_PATH': str(tmp_path / 'luma.db'), 'LUMA_DATA_DIR': str(tmp_path),
           'LUMA_CACHE_DIR': str(tmp_path / 'cache'), 'LUMA_LOG_DIR': str(tmp_path / 'logs'),
           'LUMA_SKIP_BACKGROUND_THREADS': '1', 'FLASK_STATIC_FOLDER': str(repo / 'static')}
    out = subprocess.run([sys.executable, '-c', 'import app.main'], cwd=str(repo), env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    text = out.stdout + out.stderr
    assert text.count('settings.json backed up to settings.json.bak-') == 1
    assert len(list(tmp_path.glob('settings.json.bak-*'))) == 1
