"""v181 (Taylor): the Series bulk editor and the episode select. Signed-in owner only, tv.db only,
and never a search or grab: a bulk All on 600+ shows enqueues no search and no grab itself. v253
(Taylor): the scheduler then searches every monitored, missing episode (as backlog, a few shows a pass,
held to its share of the daily cap); the no-file rule still decides what it may look at."""
import time

import pytest
from flask import jsonify

from app import tv_grab, tv_monitor, tv_search, tv_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import rows

URL = '/api/settings/tv/shows/bulk'
N = 650
OLD = '2019-03-0'


def _req(c, method, u, body=None):
    return getattr(c, method)(u, json=body, environ_base=REMOTE, headers=HDR)


@pytest.fixture
def many(tv_env):  # noqa: F811
    """650 shows, 6 old aired episodes each (2 with a file), none inside the 24 h floor."""
    with tv_store.write() as c:
        c.execute("INSERT INTO quality_profiles (id, name, items, cutoff, upgrades, is_default) VALUES (1, 'HD', '[]', NULL, 0, 1)")
        c.execute("INSERT INTO quality_profiles (id, name, items, cutoff, upgrades, is_default) VALUES (2, 'UHD', '[]', NULL, 0, 0)")
        for sid in range(1, N + 1):
            c.execute("INSERT INTO series (id, tmdb_id, title, added_at, monitor, monitored) VALUES (?, ?, ?, 0, 'none', 0)",
                      (sid, 100000 + sid, f'Show {sid}'))
            c.execute('INSERT INTO seasons (series_id, season_number) VALUES (?, 1), (?, 2)', (sid, sid))
            for s in (1, 2):
                for e in (1, 2, 3):
                    c.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date, monitored)'
                              ' VALUES (?, ?, ?, ?, 0)', (sid, s, e, f'{OLD}{e}'))
            c.execute("INSERT INTO episode_files (series_id, path_id, rel_path, origin, added_at, state) VALUES (?, 'p', ?, 'scan', 0, 'linked')",
                      (sid, f's{sid}.mkv'))
            fid = c.execute('SELECT last_insert_rowid() AS i').fetchone()['i']
            c.execute('INSERT INTO episode_file_links (file_id, episode_id) SELECT ?, id FROM episodes WHERE series_id = ?'
                      ' AND season_number = 1 AND episode_number IN (1, 2)', (fid, sid))
    return list(range(1, N + 1))


@pytest.fixture
def spies(monkeypatch):
    calls = []
    for mod, names in ((tv_search, ('search_show', 'on_added', 'schedule', 'run_due', 'manual', 'query_trackers', 'feed', 'tick')),
                       (tv_grab, ('grab',))):
        for n in names:
            real = getattr(mod, n)
            monkeypatch.setattr(mod, n, lambda *a, _n=n, _r=real, **k: calls.append(_n) or _r(*a, **k))
    return calls


def _queued():
    return rows('SELECT COUNT(*) AS n FROM episodes WHERE next_search_at IS NOT NULL')[0]['n']


def test_bulk_all_on_650_shows_enqueues_no_search_and_no_grab(owner, many, spies, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_grab, 'allowed', lambda: True)                       # even with grabs on
    monkeypatch.setattr(tv_search, 'automation', lambda: {'enabled': True, 'feed_enabled': True, 'first_search_delay_h': 0})
    t0 = time.time()
    r = _req(owner, 'put', URL, {'ids': many, 'changes': {'monitor': 'all', 'monitored': True}})
    assert r.status_code == 200, r.get_data()
    assert r.get_json() == {'ok': True, 'updated': N, 'not_found': []}
    assert time.time() - t0 < 30
    assert spies == []                                                          # no search, no grab, nothing scheduled
    assert _queued() == 0
    assert rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0
    assert rows("SELECT COUNT(*) AS n FROM series WHERE monitor = 'all' AND monitored = 1")[0]['n'] == N
    assert rows('SELECT COUNT(*) AS n FROM episodes WHERE monitored = 0')[0]['n'] == 0
    # v253: the next scheduler pass queues the missing episodes (never one with a file); no trackers here,
    # so the backlog pass sends nothing and grabs nothing
    spies.clear()
    assert tv_search.schedule(time.time()) == N * 4 == _queued()
    assert rows('SELECT COUNT(*) AS n FROM episodes e WHERE e.next_search_at IS NOT NULL AND EXISTS (SELECT 1 FROM'
                ' episode_file_links l WHERE l.episode_id = e.id)')[0]['n'] == 0
    assert tv_search.run_due(time.time())['grabbed'] == 0
    assert 'grab' not in spies and rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0


def test_bulk_missing_starts_no_search_itself(owner, many, spies):  # noqa: F811
    r = _req(owner, 'put', URL, {'ids': many[:300], 'changes': {'monitor': 'missing'}})
    assert r.status_code == 200 and r.get_json()['updated'] == 300
    assert spies == [] and _queued() == 0
    on = rows('SELECT season_number AS s, episode_number AS e, monitored AS m FROM episodes WHERE series_id = 1')
    assert {(x['s'], x['e']): x['m'] for x in on} == {(1, 1): 0, (1, 2): 0, (1, 3): 1, (2, 1): 1, (2, 2): 1, (2, 3): 1}
    with tv_store.connect() as conn:
        assert tv_search.wanted(conn, 1) != []
    assert tv_search.schedule(time.time()) > 0                                  # v253: the scheduler's job


def test_every_field_and_monitor_option(owner, many):  # noqa: F811
    ch = {'monitored': False, 'monitor_new_seasons': 'none', 'profile_id': 2, 'series_type': 'anime', 'season_folder': False}
    assert _req(owner, 'put', URL, {'ids': [1, 2, 999999], 'changes': ch}).get_json() == {'ok': True, 'updated': 2, 'not_found': [999999]}
    s = rows('SELECT monitored, monitor_new_seasons, profile_id, series_type, season_folder FROM series WHERE id IN (1, 2)')
    assert all(r == {'monitored': 0, 'monitor_new_seasons': 0, 'profile_id': 2, 'series_type': 'anime', 'season_folder': 0} for r in s)
    assert rows('SELECT profile_id FROM series WHERE id = 3')[0]['profile_id'] != 2              # others untouched
    for opt in tv_monitor.OPTIONS:
        assert _req(owner, 'put', URL, {'ids': [5], 'changes': {'monitor': opt}}).status_code == 200
        assert rows('SELECT monitor FROM series WHERE id = 5')[0]['monitor'] == opt
    assert sum(r['monitored'] for r in rows('SELECT monitored FROM episodes WHERE series_id = 5')) == 0     # None


@pytest.mark.parametrize('body', [
    None, {}, {'ids': []}, {'ids': [1]}, {'ids': [1], 'changes': {}}, {'ids': ['1'], 'changes': {'monitored': True}},
    {'ids': [True], 'changes': {'monitored': True}}, {'ids': [0], 'changes': {'monitored': True}},
    {'ids': list(range(1, 2002)), 'changes': {'monitored': True}},
    {'ids': [1], 'changes': {'monitored': 'yes'}}, {'ids': [1], 'changes': {'monitor': 'everything'}},
    {'ids': [1], 'changes': {'monitor_new_seasons': True}}, {'ids': [1], 'changes': {'series_type': 'sports'}},
    {'ids': [1], 'changes': {'season_folder': 1}}, {'ids': [1], 'changes': {'profile_id': 99}},
    {'ids': [1], 'changes': {'profile_id': None}},
    {'ids': [1], 'changes': {'path': '/media/TV/x'}}, {'ids': [1], 'changes': {'root_folder': '/media'}},
    {'ids': [1], 'changes': {'delete': True}}, {'ids': [1], 'changes': {'monitor_specials': True}},
    {'ids': [1], 'changes': {'monitored': True}, 'search': True},
])
def test_invalid_is_400_and_changes_nothing(owner, many, spies, body):  # noqa: F811
    before = rows('SELECT * FROM series WHERE id = 1')
    r = _req(owner, 'put', URL, body)
    assert r.status_code == 400, body
    assert rows('SELECT * FROM series WHERE id = 1') == before and spies == []


def test_non_owner_is_refused(minimal_app, owner, many, monkeypatch):  # noqa: F811
    body = {'ids': [1], 'changes': {'monitored': True}}
    ep = {'episode_ids': [1], 'monitored': True}
    anon = minimal_app.test_client()
    assert _req(anon, 'put', URL, body).status_code == 401
    assert _req(anon, 'put', '/api/settings/tv/shows/1/episodes/monitored', ep).status_code == 401
    for c in (_client(minimal_app, name='second'), _client(minimal_app, admin=False, name='plain')):
        assert _req(c, 'put', URL, body).status_code == 403
        assert _req(c, 'put', '/api/settings/tv/shows/1/episodes/monitored', ep).status_code == 403
    # the owner's account without a signed-in session (an implicit or PIN session): 401
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', lambda: (None, (jsonify({'error': 'Sign in', 'sign_in': True}), 401)))
    assert _req(owner, 'put', URL, body).status_code == 401
    assert _req(owner, 'put', '/api/settings/tv/shows/1/episodes/monitored', ep).status_code == 401
    assert rows('SELECT monitored FROM series WHERE id = 1')[0]['monitored'] == 0


def test_episode_select_monitors_only_the_chosen(owner, many, spies):  # noqa: F811
    ids = [r['id'] for r in rows('SELECT id FROM episodes WHERE series_id = 7 AND season_number = 2')]
    r = _req(owner, 'put', '/api/settings/tv/shows/7/episodes/monitored', {'episode_ids': ids, 'monitored': True})
    assert r.status_code == 200 and r.get_json()['updated'] == 3
    v = r.get_json()
    assert [e['monitored'] for e in v['episodes']] == [False, False, False, True, True, True]
    assert [s['monitored'] for s in v['seasons']] == [False, True]
    other = [r['id'] for r in rows('SELECT id FROM episodes WHERE series_id = 8')]
    assert _req(owner, 'put', '/api/settings/tv/shows/7/episodes/monitored', {'episode_ids': other, 'monitored': True}).get_json()['updated'] == 0
    r = _req(owner, 'put', '/api/settings/tv/shows/7/episodes/monitored', {'episode_ids': ids[:1], 'monitored': False})
    assert [e['monitored'] for e in r.get_json()['episodes']] == [False, False, False, False, True, True]
    assert spies == [] and _queued() == 0
    for bad in (None, {'episode_ids': ids}, {'episode_ids': [], 'monitored': True}, {'episode_ids': ['x'], 'monitored': True},
                {'episode_ids': ids, 'monitored': 1}, {'episode_ids': ids, 'monitored': True, 'search': True}):
        assert _req(owner, 'put', '/api/settings/tv/shows/7/episodes/monitored', bad).status_code == 400
    assert _req(owner, 'put', '/api/settings/tv/shows/99999/episodes/monitored', {'episode_ids': ids, 'monitored': True}).status_code == 404


def test_writes_only_tv_db(owner, many, tmp_path, monkeypatch):  # noqa: F811
    import requests
    sent = []
    monkeypatch.setattr(requests.Session, 'request', lambda self, m, u, *a, **k: sent.append((m, u)))
    r = _req(owner, 'put', URL, {'ids': many, 'changes': {'monitor': 'all', 'profile_id': 2}})
    assert r.status_code == 200 and sent == []


def test_routes_are_404_on_prod_builds(test_app, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    c = _client(test_app)
    assert _req(c, 'put', URL, {'ids': [1], 'changes': {'monitored': True}}).status_code == 404
