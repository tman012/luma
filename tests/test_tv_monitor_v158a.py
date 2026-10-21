"""v158a monitoring: options, inheritance, toggles, profiles, the edit sheet, bulk Missing,
delete (files need the signed-in owner and the instance gate), the v7 migration."""
import datetime as dt
import sqlite3

import pytest

from app import tv_calendar, tv_monitor, tv_scan, tv_settings, tv_store
from tests import tv_fake
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.tv_tree import snapshot

TODAY = '2026-10-18'


def _req(c, method, u, body=None, **q):
    return getattr(c, method)(u, json=body, query_string=q, environ_base=REMOTE, headers=HDR)


def _sid(title):
    return rows('SELECT id FROM series WHERE title = ?', title)[0]['id']


def _mon(sid):
    return {(r['season_number'], r['episode_number']): r['monitored'] for r in
            rows('SELECT season_number, episode_number, monitored FROM episodes WHERE series_id = ?', sid)}


# ------------------------------------------------------------------ options

def test_imported_shows_are_monitored_future_and_search_adds_future(scanned, owner):  # noqa: F811
    s = rows("SELECT monitored, monitor, monitor_new_seasons FROM series WHERE title = 'Severance'")[0]
    assert (s['monitored'], s['monitor'], s['monitor_new_seasons']) == (1, 'future', 1)
    sev = _mon(_sid('Severance'))
    today = dt.date.today().isoformat()
    air = {(r['season_number'], r['episode_number']): r['air_date'] for r in rows(
        "SELECT season_number, episode_number, air_date FROM episodes WHERE series_id = ?", _sid('Severance'))}
    assert all(sev[k] == int(air[k] is None or air[k] >= today) for k in sev)
    r = _req(owner, 'post', '/api/settings/tv/shows', {'tmdb_id': 6001})
    assert r.status_code == 201
    sid = r.get_json()['series_id']
    m = _mon(sid)
    assert rows('SELECT monitor FROM series WHERE id = ?', sid)[0]['monitor'] == 'future'   # v184: no monitor sent = Future
    air = {(r_['season_number'], r_['episode_number']): r_['air_date'] for r_ in rows(
        'SELECT season_number, episode_number, air_date FROM episodes WHERE series_id = ?', sid)}
    assert m[(0, 1)] == 0 and all(v == int(air[k] is None or air[k] >= today) for k, v in m.items() if k[0] > 0)


def test_add_takes_a_monitor_option_and_validates(scanned, owner):  # noqa: F811
    assert _req(owner, 'post', '/api/settings/tv/shows', {'tmdb_id': 6001, 'monitor': 'bogus'}).status_code == 400
    assert _req(owner, 'post', '/api/settings/tv/shows', {'tmdb_id': 6001, 'profile_id': 99}).status_code == 400
    r = _req(owner, 'post', '/api/settings/tv/shows', {'tmdb_id': 6001, 'monitor': 'none'})
    sid = r.get_json()['series_id']
    assert rows('SELECT monitored, monitor FROM series WHERE id = ?', sid)[0] == {'monitored': 0, 'monitor': 'none'}
    assert set(_mon(sid).values()) == {0}


@pytest.mark.parametrize('option,expect', [
    ('all', {(1, 1): 1, (1, 2): 1, (2, 1): 1, (2, 2): 1}),
    ('future', {(1, 1): 0, (1, 2): 0, (2, 1): 1, (2, 2): 1}),
    ('missing', {(1, 1): 0, (1, 2): 1, (2, 1): 1, (2, 2): 1}),
    ('existing', {(1, 1): 1, (1, 2): 0, (2, 1): 0, (2, 2): 0}),
    ('pilot', {(1, 1): 1, (1, 2): 0, (2, 1): 0, (2, 2): 0}),
    ('first_season', {(1, 1): 1, (1, 2): 1, (2, 1): 0, (2, 2): 0}),
    ('last_season', {(1, 1): 0, (1, 2): 0, (2, 1): 1, (2, 2): 1}),
    ('none', {(1, 1): 0, (1, 2): 0, (2, 1): 0, (2, 2): 0}),
])
def test_each_option(tv_env, option, expect):  # noqa: F811
    with tv_store.write() as c:
        c.execute("INSERT INTO series (id, tmdb_id, title, added_at) VALUES (1, 1, 'X', 0)")
        for (s, e), air in {(0, 1): '2020-01-01', (1, 1): '2020-01-01', (1, 2): '2020-01-08',
                            (2, 1): '2026-10-20', (2, 2): None}.items():
            c.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date) VALUES (1, ?, ?, ?)', (s, e, air))
        c.execute('INSERT INTO seasons (series_id, season_number) VALUES (1, 0), (1, 1), (1, 2)')
        c.execute("INSERT INTO episode_files (id, series_id, path_id, rel_path, origin, added_at, state) VALUES (1, 1, 'p', 'a.mkv', 'scan', 0, 'linked')")
        c.execute('INSERT INTO episode_file_links (file_id, episode_id) SELECT 1, id FROM episodes WHERE season_number = 1 AND episode_number = 1')
        tv_monitor.apply(c, 1, option, specials=False, today=TODAY)
    m = _mon(1)
    assert m.pop((0, 1)) == 0
    assert m == expect
    seasons = {r['season_number']: r['monitored'] for r in rows('SELECT season_number, monitored FROM seasons')}
    assert seasons[1] == int(expect[(1, 1)] or expect[(1, 2)]) and seasons[2] == int(expect[(2, 1)] or expect[(2, 2)])
    with tv_store.write() as c:
        tv_monitor.apply(c, 1, 'all', specials=True, today=TODAY)
    assert _mon(1)[(0, 1)] == 1                     # specials only with the switch on


def test_new_seasons_and_episodes_inherit(scanned, fake_tmdb, monkeypatch):  # noqa: F811
    sid = _sid('Severance')
    sev = dict(tv_fake.SHOWS[95396])
    sev['seasons'] = {**sev['seasons'], 3: 2}
    sev['air'] = {**sev.get('air', {}), (3, 1): '2027-01-01', (3, 2): '2027-01-08'}
    monkeypatch.setitem(tv_fake.SHOWS, 95396, sev)
    tv_scan.refresh_episodes(sid, 95396, 2000.0)
    assert _mon(sid)[(3, 1)] == 1 and _mon(sid)[(3, 2)] == 1          # All seasons
    with tv_store.write() as c:
        c.execute('UPDATE series SET monitor_new_seasons = 0 WHERE id = ?', (sid,))
    sev['seasons'] = {**sev['seasons'], 4: 1}
    sev['air'] = {**sev['air'], (4, 1): '2028-01-01'}
    tv_scan.refresh_episodes(sid, 95396, 3000.0)
    assert _mon(sid)[(4, 1)] == 0                                       # None: the new season stays off
    assert rows('SELECT monitored FROM seasons WHERE series_id = ? AND season_number = 4', sid)[0]['monitored'] == 0


# ------------------------------------------------------------------ toggles + calendar

def test_series_season_episode_toggles_and_calendar_flag(scanned, owner):  # noqa: F811
    sid = _sid('Severance')
    eid = rows('SELECT id FROM episodes WHERE series_id = ? AND season_number = 2 AND episode_number = 3', sid)[0]['id']
    r = _req(owner, 'put', f'/api/settings/tv/episodes/{eid}/monitored', {'monitored': False})
    assert r.status_code == 200
    cal = {(e['season'], e['episode']): e['monitored'] for e in tv_calendar.calendar(dt.date(2026, 10, 11), 7)['episodes']
           if e['show'] == 'Severance'}
    assert cal[(2, 3)] is False
    assert _req(owner, 'put', f'/api/settings/tv/episodes/{eid}/monitored', {'monitored': 'no'}).status_code == 400
    assert _req(owner, 'put', '/api/settings/tv/episodes/999999/monitored', {'monitored': True}).status_code == 404
    v = _req(owner, 'put', f'/api/settings/tv/shows/{sid}/seasons/1/monitored', {'monitored': True}).get_json()
    assert next(s for s in v['seasons'] if s['season'] == 1)['monitored'] is True
    assert all(e['monitored'] for e in v['episodes'] if e['season'] == 1)
    v = _req(owner, 'put', f'/api/settings/tv/shows/{sid}/monitored', {'monitored': False}).get_json()
    assert v['monitored'] is False
    assert not any(e['monitored'] for e in tv_calendar.calendar(dt.date(2026, 10, 11), 7)['episodes'] if e['show'] == 'Severance')
    with tv_store.connect() as conn:
        want = set(tv_monitor.wanted(conn))
    assert not want & {r['id'] for r in rows('SELECT id FROM episodes WHERE series_id = ?', sid)}   # series off: no grabs
    tmdb = rows('SELECT tmdb_id FROM series WHERE id = ?', sid)[0]['tmdb_id']
    assert _req(owner, 'get', f'/api/settings/tv/shows/by-tmdb/{tmdb}').get_json()['id'] == sid


def test_bulk_missing(scanned, owner):  # noqa: F811
    ids = [r['id'] for r in rows('SELECT id FROM series')]
    for bad in (None, {}, {'ids': []}, {'ids': ['1']}, {'ids': [True]}):
        assert _req(owner, 'post', '/api/settings/tv/shows/monitor-missing', bad).status_code == 400
    r = _req(owner, 'post', '/api/settings/tv/shows/monitor-missing', {'ids': ids + [999999]})
    assert r.get_json() == {'ok': True, 'updated': len(ids)}
    assert {r['monitor'] for r in rows('SELECT monitor FROM series')} == {'missing'}
    linked = {r['id'] for r in rows("SELECT l.episode_id AS id FROM episode_file_links l JOIN episode_files f ON f.id = l.file_id WHERE f.state = 'linked'")}
    for r in rows('SELECT id, season_number, monitored FROM episodes'):
        if r['season_number'] > 0:
            assert r['monitored'] == int(r['id'] not in linked)


# ------------------------------------------------------------------ edit sheet + delete

def test_edit_sheet_saves_and_validates(scanned, owner, tv_env):  # noqa: F811
    sid = _sid('Severance')
    v = _req(owner, 'get', f'/api/settings/tv/shows/{sid}').get_json()
    assert v['monitor_new_seasons'] == 'all' and v['season_folder'] is True and v['series_type'] == 'standard'
    p = _req(owner, 'post', '/api/settings/tv/profiles', {'name': 'HD', 'items': [{'id': '1080p-web', 'allowed': True}]}).get_json()
    body = {'monitored': False, 'monitor_new_seasons': 'none', 'season_folder': False, 'series_type': 'daily',
            'profile_id': p['id'], 'monitor_specials': True}
    v = _req(owner, 'put', f'/api/settings/tv/shows/{sid}', body).get_json()
    assert (v['monitored'], v['monitor_new_seasons'], v['season_folder'], v['series_type'], v['profile_id'], v['monitor_specials']) == \
        (False, 'none', False, 'daily', p['id'], True)
    r = _req(owner, 'put', f'/api/settings/tv/shows/{sid}', {'series_type': 'x', 'profile_id': 999, 'monitor_new_seasons': 1,
                                                            'path': '/etc'})
    assert r.status_code == 400 and set(r.get_json()['errors']) == {'series_type', 'profile_id', 'monitor_new_seasons', 'path'}
    new = tv_env / 'TV' / 'Severance Moved'
    new.mkdir()
    before = snapshot(tv_env)
    v = _req(owner, 'put', f'/api/settings/tv/shows/{sid}', {'path': str(new)}).get_json()
    assert v['folder'] == 'Severance Moved' and v['path'] == str(new.resolve())
    assert snapshot(tv_env) == before                                        # re-points; never moves files
    dark = _sid('Dark')
    r = _req(owner, 'put', f'/api/settings/tv/shows/{dark}', {'path': str(new)})
    assert r.status_code == 400 and r.get_json()['errors'] == {'path': 'Another show uses this folder'}
    assert _req(owner, 'put', '/api/settings/tv/shows/999999', {'monitored': True}).status_code == 404


def test_delete_keeps_files_unless_asked_and_files_need_the_signed_in_owner(scanned, owner, tv_env, monkeypatch):  # noqa: F811
    sid = _sid('Dark')
    folder = tv_env / 'TV' / rows('SELECT folder FROM series WHERE id = ?', sid)[0]['folder']
    assert folder.is_dir()
    from flask import jsonify
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', lambda: (None, (jsonify({'error': 'Sign in', 'sign_in': True}), 401)))
    assert _req(owner, 'delete', f'/api/settings/tv/shows/{sid}', delete_files='1').status_code == 401
    assert folder.is_dir() and rows('SELECT 1 FROM series WHERE id = ?', sid)
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', lambda: (object(), None))
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    r = _req(owner, 'delete', f'/api/settings/tv/shows/{sid}', delete_files='1')
    assert r.status_code == 403 and r.get_json()['job'] == 'tv_delete_files'
    assert folder.is_dir()
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_delete_files')
    assert _req(owner, 'delete', f'/api/settings/tv/shows/{sid}', delete_files='1').get_json() == {'ok': True, 'files_removed': True}
    assert not folder.exists() and not rows('SELECT 1 FROM series WHERE id = ?', sid)
    sev = _sid('Severance')
    sfolder = tv_env / 'TV' / rows('SELECT folder FROM series WHERE id = ?', sev)[0]['folder']
    monkeypatch.delenv('LUMA_INSTANCE')
    before = snapshot(tv_env)
    assert _req(owner, 'delete', f'/api/settings/tv/shows/{sev}').get_json() == {'ok': True, 'files_removed': False}
    assert snapshot(tv_env) == before and sfolder.is_dir()


# ------------------------------------------------------------------ profiles

def test_profiles_crud_default_cutoff_and_in_use(scanned, owner):  # noqa: F811
    g = _req(owner, 'get', '/api/settings/tv/profiles').get_json()
    assert g['profiles'] == [] and len(g['qualities']) == 14                      # no presets
    assert {'id': '1080p-web', 'name': '1080p WEB', 'resolution': '1080p', 'source': 'web'} in g['qualities']
    for bad in ({}, {'name': 'A', 'items': []}, {'name': 'A', 'items': [{'id': 'nope', 'allowed': True}]},
                {'name': 'A', 'items': [{'id': '720p-web', 'allowed': True}], 'cutoff': '1080p-web'}):
        assert _req(owner, 'post', '/api/settings/tv/profiles', bad).status_code == 400
    a = _req(owner, 'post', '/api/settings/tv/profiles', {'name': 'HD', 'upgrades': True, 'cutoff': '1080p-web',
             'items': [{'id': '1080p-web', 'allowed': True}, {'id': '720p-web', 'allowed': True}]})
    a = a.get_json()
    assert a['default'] is True and a['cutoff'] == '1080p-web' and len(a['items']) == 14
    assert [i['id'] for i in a['items'][:2]] == ['1080p-web', '720p-web']        # owner's order kept
    b = _req(owner, 'post', '/api/settings/tv/profiles', {'name': 'UHD', 'default': True,
             'items': [{'id': '2160p-web', 'allowed': True}]}).get_json()
    assert b['default'] and b['cutoff'] == '2160p-web' and tv_monitor.get_profile(a['id'])['default'] is False
    assert _req(owner, 'post', '/api/settings/tv/profiles', {'name': 'HD', 'items': [{'id': '720p-web', 'allowed': True}]}).status_code == 400
    r = _req(owner, 'post', '/api/settings/tv/shows', {'tmdb_id': 6001})
    assert rows('SELECT profile_id FROM series WHERE id = ?', r.get_json()['series_id'])[0]['profile_id'] == b['id']
    assert _req(owner, 'delete', f"/api/settings/tv/profiles/{b['id']}").status_code == 409          # default and in use
    assert _req(owner, 'delete', f"/api/settings/tv/profiles/{a['id']}").status_code == 200
    assert tv_monitor.quality_of('Show.S01E01.2160p.WEB-DL.DDP5.1-GRP') == '2160p-web'
    assert tv_monitor.quality_of('Show.S01E01.HDTV.x264-GRP') == 'sd-hdtv'


# ------------------------------------------------------------------ migration

def test_v7_migration_marks_existing_shows_monitored_future_with_a_verified_backup(tv_env):  # noqa: F811
    p = tv_store.db_path()
    if p.exists():
        p.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)
    tv_store._ready.clear()
    c = sqlite3.connect(str(p))
    for script in tv_store.MIGRATIONS[:6]:
        for stmt in tv_store._statements(script):
            c.execute(stmt)
    c.execute("INSERT INTO series (id, tmdb_id, title, added_at) VALUES (1, 1, 'Old', 0)")
    c.execute('INSERT INTO seasons (series_id, season_number) VALUES (1, 0), (1, 1), (1, 2)')
    for s_, e, air in ((0, 1, '2030-01-01'), (1, 1, '2001-01-01'), (2, 1, '2099-01-01')):
        c.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date) VALUES (1, ?, ?, ?)', (s_, e, air))
    c.execute('PRAGMA user_version = 6')
    c.commit()
    c.close()
    assert tv_store.schema_version() == 11
    bak = p.with_name(p.name + '.bak-v6')
    assert bak.exists()
    b = sqlite3.connect(str(bak))
    assert b.execute('PRAGMA user_version').fetchone()[0] == 6 and b.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    b.close()
    s = rows('SELECT monitored, monitor, monitor_new_seasons FROM series')[0]
    assert (s['monitored'], s['monitor'], s['monitor_new_seasons']) == (1, 'future', 1)
    assert _mon(1) == {(0, 1): 0, (1, 1): 0, (2, 1): 1}
    assert {r['season_number']: r['monitored'] for r in rows('SELECT season_number, monitored FROM seasons')} == {0: 0, 1: 0, 2: 1}


def _v6_db(rows_sql):
    p = tv_store.db_path()
    if p.exists():
        p.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)
    tv_store._ready.clear()
    c = sqlite3.connect(str(p))
    for script in tv_store.MIGRATIONS[:6]:
        for stmt in tv_store._statements(script):
            c.execute(stmt)
    for sql in rows_sql:
        c.execute(sql)
    c.execute('PRAGMA user_version = 6')
    c.commit()
    c.close()


def test_v158b_v7_migration_keeps_an_existing_monitoring_choice_and_reruns_never_reset(tv_env):  # noqa: F811
    _v6_db([
        "INSERT INTO series (id, tmdb_id, title, added_at) VALUES (1, 1, 'No choice', 0)",
        "INSERT INTO series (id, tmdb_id, title, added_at, monitored) VALUES (2, 2, 'Monitored', 0, 1)",
        "INSERT INTO series (id, tmdb_id, title, added_at, monitor_new_seasons) VALUES (3, 3, 'New seasons', 0, 1)",
        "INSERT INTO series (id, tmdb_id, title, added_at) VALUES (4, 4, 'One episode on', 0)",
        'INSERT INTO seasons (series_id, season_number) VALUES (1, 1), (2, 1), (4, 1)',
        "INSERT INTO episodes (series_id, season_number, episode_number, air_date, monitored) VALUES"
        " (1, 1, 1, '2001-01-01', 0), (1, 1, 2, '2099-01-01', 0), (2, 1, 1, '2001-01-01', 1),"
        " (4, 1, 1, '2001-01-01', 1), (4, 1, 2, '2099-01-01', 0)"])
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION == 11
    got = {r['id']: (r['monitored'], r['monitor'], r['monitor_new_seasons'], r['monitor_chosen'])
           for r in rows('SELECT id, monitored, monitor, monitor_new_seasons, monitor_chosen FROM series')}
    assert got[1] == (1, 'future', 1, 1)                                  # no choice: Future
    # v158c (v9): the kept choices get the label that matches their episodes; nothing else changes
    assert got[2][:3] == (1, 'all', 0) and got[3][:3] == (0, 'none', 1)
    assert got[4][:3] == (0, 'none', 0)                                   # v165: series off -> picked by hand, not 'pilot'
    assert _mon(2) == {(1, 1): 1} and _mon(4) == {(1, 1): 1, (1, 2): 0}  # flags kept as they were
    assert _mon(1) == {(1, 1): 0, (1, 2): 1}
    # the owner turns a show off entirely (an explicit "none"); a re-run never resets it
    tv_monitor.edit(1, {'monitor': 'none', 'monitored': False})
    before = rows('SELECT * FROM series ORDER BY id'), rows('SELECT * FROM episodes ORDER BY id')
    with tv_store.write() as conn:
        assert tv_store.monitor_defaults(conn) == 0
    assert (rows('SELECT * FROM series ORDER BY id'), rows('SELECT * FROM episodes ORDER BY id')) == before


def test_v158b_show_edit_needs_the_signed_in_owner(scanned, owner, monkeypatch):  # noqa: F811
    from flask import jsonify
    sid = _sid('Dark')
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', lambda: (None, (jsonify({'error': 'Sign in', 'sign_in': True}), 401)))
    before = rows('SELECT * FROM series WHERE id = ?', sid)
    assert _req(owner, 'put', f'/api/settings/tv/shows/{sid}', {'monitored': False}).status_code == 401
    assert rows('SELECT * FROM series WHERE id = ?', sid) == before
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', lambda: (object(), None))
    assert _req(owner, 'put', f'/api/settings/tv/shows/{sid}', {'monitored': False}).status_code == 200
