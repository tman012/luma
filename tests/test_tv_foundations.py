"""TV Phase 0 foundations: per-section tracker lists (store + API), TV purpose
budgets in the tracker guard, tv_settings.json and tv.db."""

import json
from pathlib import Path
import os
import sqlite3
import stat

import pytest

import app.main
from app import tv_naming
from app import minimal, section_trackers as st, tracker_guard, tv_budget, tv_settings, tv_store
from app.tracker_guard import TrackerGuard
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client

T0 = 1_800_000_000.0


# ---------------------------------------------------------------- section tracker lists

def _rec(i, name, enabled=True, cats=(5000,), type_='torznab', priority=25):
    return {'id': i, 'name': name, 'type': type_, 'enabled': enabled, 'categories': list(cats),
            'priority': priority, 'url': f'https://t{i}.example/api'}


RECS = [_rec(1, 'Alpha', priority=10), _rec(2, 'Bravo', priority=5), _rec(3, 'Charlie', cats=(2000,)),
        _rec(4, 'Delta', enabled=False)]


@pytest.fixture
def sec(tmp_path, monkeypatch):
    monkeypatch.setitem(st._cfg, 'store_path', str(tmp_path / 'st' / st.STORE_NAME))
    monkeypatch.setitem(st._cfg, 'list_trackers', lambda: [dict(r) for r in RECS])
    return tmp_path / 'st' / st.STORE_NAME


def test_nothing_is_on_by_default(sec):
    rows = st.arrange('tv')
    assert [r['id'] for r in rows] == [2, 1, 3, 4]               # new trackers: global priority order
    assert not any(r['enabled'] for r in rows)
    assert st.ordered_ids('tv') == [] and st.ordered_ids('movies') == []
    assert not sec.exists()                                      # a read writes nothing


def test_flags_global_switch_and_category(sec):
    rows = {r['id']: r for r in st.arrange('tv')}
    assert rows[3]['has_category'] is False and rows[1]['has_category'] is True
    assert rows[4]['global_enabled'] is False
    assert {r['id']: r['has_category'] for r in st.arrange('movies')} == {2: False, 1: False, 3: True, 4: False}


def test_order_and_allow_list(sec):
    # 4 and 3 were saved on earlier (when they were usable): they keep that saved state.
    saved = {'order': [1, 2, 3, 4], 'enabled': [3, 4]}
    new, err = st.check_put('tv', {'trackers': [{'id': 4, 'enabled': True}, {'id': 3, 'enabled': True},
                                                {'id': 1, 'enabled': True}, {'id': 2, 'enabled': False}]}, RECS, saved)
    assert err is None
    st.save({'sections': {'tv': new}})
    rows = st.arrange('tv')
    assert [r['id'] for r in rows] == [4, 3, 1, 2]
    assert [(r['enabled'], r['active']) for r in rows] == [(True, False), (True, False), (True, True), (False, False)]
    assert st.ordered_ids('tv') == [1]                           # 4 off globally, 3 has no TV category
    assert stat.S_IMODE(os.stat(sec).st_mode) == 0o600
    assert st.load()['sections']['movies'] == {'order': [], 'enabled': []}


def test_a_new_tracker_lands_last_and_off(sec, monkeypatch):
    st.save({'sections': {'tv': {'order': [1, 2], 'enabled': [1, 2]}}})
    monkeypatch.setitem(st._cfg, 'list_trackers', lambda: RECS + [_rec(9, 'Echo', priority=1)])
    rows = st.arrange('tv')
    assert [r['id'] for r in rows] == [1, 2, 9, 3, 4]             # listed ones first, the rest by priority
    assert [r['enabled'] for r in rows] == [True, True, False, False, False]


def test_forget_drops_a_deleted_tracker(sec):
    st.save({'sections': {'tv': {'order': [1, 2], 'enabled': [1, 2]}, 'movies': {'order': [2], 'enabled': [2]}}})
    st.forget(2)
    d = st.load()['sections']
    assert d['tv'] == {'order': [1], 'enabled': [1]} and d['movies'] == {'order': [], 'enabled': []}


@pytest.mark.parametrize('body,error', [
    (None, st.INVALID), ({}, st.INVALID), ({'trackers': 'x'}, st.INVALID),
    ({'trackers': [{'id': 1}]}, st.INVALID), ({'trackers': [{'id': '1', 'enabled': True}]}, st.INVALID),
    ({'trackers': [{'id': True, 'enabled': True}]}, st.INVALID),
    ({'trackers': [{'id': 1, 'enabled': 1}]}, st.INVALID),
    ({'trackers': [{'id': 77, 'enabled': True}]}, st.UNKNOWN),
    ({'trackers': [{'id': 1, 'enabled': True}] * (st.MAX_IDS + 1)}, st.INVALID),
])
def test_put_validation(body, error):
    assert st.check_put('tv', body, RECS) == (None, error)


def test_put_ignores_duplicates():
    new, err = st.check_put('tv', {'trackers': [{'id': 1, 'enabled': True}, {'id': 1, 'enabled': False}]}, RECS)
    assert err is None and new == {'order': [1], 'enabled': [1]}


def test_unreadable_store_is_quarantined(sec):
    sec.parent.mkdir(parents=True)
    sec.write_text('{nope')
    assert st.load() == st.empty()
    assert not sec.exists() and any(p.name.startswith(st.STORE_NAME + '.bad-') for p in sec.parent.iterdir())


def test_store_normalizes_junk(sec):
    sec.parent.mkdir(parents=True)
    sec.write_text(json.dumps({'sections': {'tv': {'order': [1, 1, 'x', -2, True, 3], 'enabled': [3, 8]},
                                            'other': {'order': [5]}}}))
    assert st.load()['sections'] == {'tv': {'order': [1, 3], 'enabled': [3]}, 'movies': {'order': [], 'enabled': []}}


# routes ---------------------------------------------------------------------

URL = '/api/settings/sections/tv/trackers'


@pytest.fixture
def owner(minimal_app, sec):
    return _client(minimal_app)


def test_routes_owner_gets_and_puts(owner):
    r = owner.get(URL, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    body = r.get_json()
    assert body['section'] == 'tv' and [t['id'] for t in body['trackers']] == [2, 1, 3, 4]
    cap = None if body['trackers'][0]['type_name'] in ('Torznab', 'Newznab') else tv_budget.AUTO_PER_TRACKER_DAILY   # v158c: no TV cap for generic indexers
    assert body['trackers'][0]['budget'] == {'used': 0, 'cap': cap}
    r = owner.put(URL, json={'trackers': [{'id': 1, 'enabled': True}, {'id': 2, 'enabled': True}]},
                  environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    assert [(t['id'], t['enabled']) for t in r.get_json()['trackers']] == [(1, True), (2, True), (3, False), (4, False)]
    assert st.ordered_ids('tv') == [1, 2]
    assert owner.put(URL, json={'trackers': [{'id': 99, 'enabled': True}]}, environ_base=REMOTE,
                     headers=HDR).get_json() == {'error': st.UNKNOWN}
    assert owner.get('/api/settings/sections/movies/trackers', environ_base=REMOTE, headers=HDR).status_code == 200
    assert owner.get('/api/settings/sections/music/trackers', environ_base=REMOTE, headers=HDR).status_code == 404


def test_routes_are_owner_only(minimal_app, sec):
    from app import user_auth
    _client(minimal_app)                                         # the owner
    anon = minimal_app.test_client()
    assert anon.get(URL, environ_base=REMOTE, headers=HDR).status_code == 401
    other = _client(minimal_app, name='second')                  # a second admin
    for meth, kw in (('get', {}), ('put', {'json': {'trackers': []}})):
        assert getattr(other, meth)(URL, environ_base=REMOTE, headers=HDR, **kw).status_code == 403
    plain = _client(minimal_app, admin=False, name='plain')
    assert plain.get(URL, environ_base=REMOTE, headers=HDR).status_code == 403
    assert not sec.exists()
    assert user_auth  # noqa


def test_routes_are_404_on_prod_builds(test_app, sec, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    c = _client(test_app)
    assert c.get(URL, environ_base=REMOTE, headers=HDR).status_code == 404
    assert c.get('/api/settings/tv', environ_base=REMOTE, headers=HDR).status_code == 404


def test_minimal_allowlist():
    for p, m in ((URL, 'GET'), (URL, 'PUT'), ('/api/settings/sections/movies/trackers', 'GET'),
                 ('/api/settings/tv', 'GET'), ('/api/settings/tv', 'PUT')):
        assert minimal.path_allowed(p, m), p


def test_deleting_a_tracker_drops_it_from_sections(sec):
    st.save({'sections': {'tv': {'order': [1, 2], 'enabled': [2]}}})
    app.main._on_tracker_deleted({'id': 2, 'name': 'Bravo', 'type': 'torznab'})
    assert st.load()['sections']['tv'] == {'order': [1], 'enabled': []}


# ---------------------------------------------------------------- guard purpose budgets

@pytest.fixture
def guard(tmp_path):
    return TrackerGuard(tmp_path / 'tracker_guard.json')


def test_tv_is_capped_at_8_a_day_per_tracker(guard):
    assert tv_budget.AUTO_PER_TRACKER_DAILY == 8
    sent = []
    t = T0
    while t < T0 + 86400:
        if guard.take('site:iptorrents', False, False, now=t, purpose='tv') is None:
            sent.append(t)
        t += 60
    assert len(sent) == 8
    assert all(b - a >= tv_budget.AUTO_MIN_GAP_S for a, b in zip(sent, sent[1:]))
    # the next day frees the budget again
    assert guard.take('site:iptorrents', False, False, now=sent[0] + 86400 + 1, purpose='tv') is None


def test_tv_hourly_cap_and_spacing(guard):
    # v202a (Taylor): 1-min spacing, hourly cap equal to the daily cap (8)
    assert guard.take('site:x', False, False, now=T0, purpose='tv') is None
    assert guard.take('site:x', False, False, now=T0 + 10, purpose='tv') == 'tv spacing'
    assert guard.take('site:x', False, False, now=T0 + 60, purpose='tv') is None
    assert guard.take('site:x', False, False, now=T0 + 600, purpose='tv') is None      # a 3rd in the hour: no 2/h cap
    assert tv_budget.AUTO_PER_TRACKER_HOURLY == tv_budget.AUTO_PER_TRACKER_DAILY


def test_tv_cap_across_trackers(guard, monkeypatch):
    monkeypatch.setitem(tv_budget.GUARD_LIMITS, 'all_trackers_day', 3)
    for i in range(3):
        assert guard.take(f'host:t{i}.example', False, False, now=T0, purpose='tv') is None
    assert guard.take('host:t9.example', False, False, now=T0, purpose='tv').startswith('tv daily cap across')


def test_tv_counts_on_top_of_the_guard_limits(guard):
    for i in range(5):                                          # empty the background bucket
        assert guard.take('site:x', False, False, now=T0 + i) is None
    assert guard.take('site:x', False, False, now=T0 + 10, purpose='tv') == 'rate limit (bucket empty)'
    assert guard.purpose_counts('site:x', 'tv', T0 + 10)['day'] == 0   # a refused request isn't counted


def test_other_traffic_does_not_use_tvs_budget_and_manual_is_interactive(guard):
    for i in range(20):
        assert guard.take('site:x', True, False, now=T0 + i, purpose='tv') is None   # manual: interactive budget
    assert guard.purpose_counts('site:x', 'tv', T0 + 30)['day'] == 0
    assert guard.counts('site:x', T0 + 30)[0] == 20


def test_purpose_log_persists_and_reloads(guard, tmp_path):
    import time
    now = time.time()
    assert guard.take('site:x', False, False, now=now, purpose='tv') is None
    raw = json.loads((tmp_path / 'tracker_guard.json').read_text())
    # `log` keeps the [ts, 'b'] shape every earlier guard reads; the purpose is beside it.
    assert raw['log']['site:x'] == [[now, 'b']] and raw['purposes']['site:x'] == [[now, 'tv']]
    fresh = TrackerGuard(tmp_path / 'tracker_guard.json')
    assert fresh.purpose_counts('site:x', 'tv', now + 1)['day'] == 1
    assert fresh.counts('site:x', now + 1)[1] == 1


def test_78d7f1e_files_with_b_colon_tv_are_read_as_both(tmp_path):
    import time
    now = time.time()
    raw = {'log': {'site:x': [[now - 10, 'b:tv'], [now - 20, 'b:junk!'], [now - 30, 'x'], [now - 40, 'i']]}}
    (tmp_path / 'g.json').write_text(json.dumps(raw))
    g = TrackerGuard(tmp_path / 'g.json')
    assert g.purpose_counts('site:x', 'tv', now)['day'] == 1
    g._load()
    assert g.log['site:x'] == [[now - 10, 'b'], [now - 40, 'i']]
    assert g.take('site:x', True, False, now=now) is None
    saved = json.loads((tmp_path / 'g.json').read_text())
    assert all(k in ('i', 'b') for _t, k in saved['log']['site:x'])
    assert saved['purposes']['site:x'] == [[now - 10, 'tv']]


def test_a_rolled_back_guard_still_counts_tv_requests(tmp_path, monkeypatch):
    """The 1a4ff5e guard keeps only [ts, 'i'|'b'] log entries; TV's requests
    must survive a rollback so its shared caps still count them."""
    import subprocess
    import sys
    import time
    now = time.time()
    g = TrackerGuard(tmp_path / 'tracker_guard.json')
    for i in range(3):
        assert g.take('site:x', False, False, now=now - 8000 + i * 3700, purpose='tv') is None
    old = subprocess.run(['git', 'show', '1a4ff5e:app/tracker_guard.py'], capture_output=True, text=True,
                         cwd=str(Path(__file__).resolve().parent.parent))
    if old.returncode != 0:
        pytest.skip('1a4ff5e not in this clone')
    mod = tmp_path / 'old_guard.py'
    mod.write_text(old.stdout)
    import importlib.util
    spec = importlib.util.spec_from_file_location('old_guard', mod)
    og = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, 'old_guard', og)
    spec.loader.exec_module(og)
    rolled = og.TrackerGuard(tmp_path / 'tracker_guard.json')
    rolled._load()
    assert rolled.counts('site:x', now)[1] == 3


@pytest.mark.parametrize('purpose', ['TV', 'tv!', '', 'x' * 17, 7])
def test_invalid_purpose_is_refused(guard, purpose):
    assert guard.take('site:x', False, False, now=T0, purpose=purpose) == 'invalid purpose'


def test_unknown_purpose_is_refused(guard):
    assert guard.take('site:x', False, False, now=T0, purpose='music') == 'unknown purpose music'


def test_tv_budget_take_and_usage(guard):
    cfg = {'type': 'iptorrents', 'url': 'https://iptorrents.com'}
    assert tv_budget.take(cfg, now=T0, guard=guard) is None
    assert tv_budget.usage(cfg, now=T0 + 1, guard=guard) == {'used': 1, 'cap': 8}
    assert guard.log == {'site:iptorrents': [[T0, 'b']]} and guard.purposes == {'site:iptorrents': [[T0, 'tv']]}


def test_search_one_charges_the_purpose(guard, monkeypatch):
    from app import tracker_clients, tracker_search
    s = tracker_search.TrackerSearch(guard=guard)
    monkeypatch.setattr(tracker_clients, 'search', lambda cfg, *a, **k: [])
    monkeypatch.setattr(guard, 'wait_spacing', lambda tid: None)
    cfg = {'id': 1, 'type': 'torznab', 'name': 'A', 'url': 'https://a.example/api', 'categories': [5000]}
    s.search_one(cfg, 'Show S01E01', [5000], 50, False, purpose='tv')
    assert [k for _t, k in guard.log['host:a.example']] == ['b']
    assert [k for _t, k in guard.purposes['host:a.example']] == ['tv']


# ---------------------------------------------------------------- tv_settings.json

@pytest.fixture
def tvs(tmp_path, monkeypatch):
    monkeypatch.setitem(tv_settings._cfg, 'store_path', str(tmp_path / 'tvs' / tv_settings.STORE_NAME))
    return tmp_path / 'tvs' / tv_settings.STORE_NAME


def test_defaults_follow_the_pm_rulings(tvs):
    d = tv_settings.load()
    assert d['naming']['series_folder'] == '{Series Title}'          # v185: Taylor's Sonarr NamingConfig
    assert d['upgrades'] == {'mode': 'keep_both'}
    assert d['automation']['enabled'] is False and d['automation']['feed_enabled'] is False
    assert d['paths'] == [] and d['rules'] == []
    assert not any('cap' in k for k in d['automation'])             # caps are code, not settings
    assert not tvs.exists()


def test_series_folder_naming(tvs):
    assert tv_settings.series_folder('The Bear', 2022) == 'The Bear'
    assert tv_settings.series_folder('Shōgun', '') == 'Shōgun'
    assert tv_settings.series_folder('Marvel: {Agents}/S.H.I.E.L.D.', 2013) == 'Marvel- {Agents}+S.H.I.E.L.D'
    d = tv_settings.load()
    d['naming']['series_folder'] = '{Series TitleYear}'
    assert tv_settings.series_folder('Andor', 2022, d) == 'Andor (2022)'
    assert tv_settings.series_folder('Andor', None, d) == 'Andor'


@pytest.mark.parametrize('key,value,ok', [
    ('series_folder', '{Series TitleYear}', True), ('series_folder', '{Series Title}', True),
    ('series_folder', '{Series Year}', False), ('series_folder', '{Series Title}/{Series Year}', False),
    ('series_folder', '{Series Title} {season}', False), ('series_folder', '{title}', False),
    ('series_folder', '{Series Title:>40}', False), ('series_folder', '{Series Title', False), ('series_folder', '', False),
    ('season_folder', 'Season {season:00}', True), ('season_folder', 'S{season}', True),
    ('standard', '{Series Title} - S{season:00}E{episode:00} - {Episode Title}', True),
    ('standard', '{Series Title} - {Episode Title}', False), ('daily', '{Series Title} - {Air-Date}', True),
    ('specials_folder', 'Specials', True), ('specials_folder', '..', False), ('standard', 7, False),
])
def test_template_validation(key, value, ok):
    assert (tv_naming.check_format(key, value) is None) is ok


def test_put_applies_and_rejects(tvs):
    d = tv_settings.load()
    assert tv_settings.apply_put(d, {'upgrades': {'mode': 'replace_verified'},
                                     'naming': {'series_folder': '{Series TitleYear}'},
                                     'automation': {'enabled': True, 'first_search_delay_h': 6}}) == {}
    assert d['upgrades']['mode'] == 'replace_verified' and d['naming']['series_folder'] == '{Series TitleYear}'
    d = tv_settings.load()
    errs = tv_settings.apply_put(d, {'upgrades': {'mode': 'delete_old'}, 'naming': {'standard': '{x}'},
                                     'automation': {'daily_cap': 99}, 'paths': []})
    assert set(errs) == {'upgrades.mode', 'naming.standard', 'automation', '_'}


def test_settings_routes(minimal_app, tvs):
    c = _client(minimal_app)
    r = c.get('/api/settings/tv', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['upgrades'] == {'mode': 'keep_both'}
    r = c.put('/api/settings/tv', json={'upgrades': {'mode': 'replace_verified'}}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and tv_settings.load()['upgrades']['mode'] == 'replace_verified'
    assert stat.S_IMODE(os.stat(tvs).st_mode) == 0o600
    r = c.put('/api/settings/tv', json={'upgrades': {'mode': 'x'}}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and tv_settings.load()['upgrades']['mode'] == 'replace_verified'
    other = _client(minimal_app, name='second')
    assert other.get('/api/settings/tv', environ_base=REMOTE, headers=HDR).status_code == 403
    assert minimal_app.test_client().get('/api/settings/tv', environ_base=REMOTE, headers=HDR).status_code == 401


def test_settings_unreadable_file_is_quarantined(tvs):
    tvs.parent.mkdir(parents=True)
    tvs.write_text('[')
    assert tv_settings.load() == tv_settings.empty()
    assert not tvs.exists()


# ---------------------------------------------------------------- tv.db

@pytest.fixture
def db(tmp_path):
    tv_store.init(str(tmp_path / 'db' / 'tv.db'))
    yield tmp_path / 'db' / 'tv.db'
    tv_store.init(None)
    tv_store._cfg.pop('path', None)


def test_schema_is_created_with_wal_and_0600(db):
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION == len(tv_store.MIGRATIONS) == 10
    with tv_store.connect() as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {'series', 'seasons', 'episodes', 'episode_files', 'episode_file_links', 'quality_profiles',
            'grabs', 'history', 'search_log', 'blocklist', 'unmatched'} <= names
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600


def test_constraints(db):
    with tv_store.write() as conn:
        conn.execute("INSERT INTO series (tmdb_id, title, added_at) VALUES (1, 'A', 0)")
    with pytest.raises(sqlite3.IntegrityError):
        with tv_store.write() as conn:
            conn.execute("INSERT INTO series (tmdb_id, title, added_at) VALUES (1, 'B', 0)")
    with pytest.raises(sqlite3.IntegrityError):
        with tv_store.write() as conn:
            conn.execute("INSERT INTO series (tmdb_id, series_type, added_at) VALUES (2, 'weird', 0)")
    with pytest.raises(sqlite3.IntegrityError):                   # foreign keys are on
        with tv_store.write() as conn:
            conn.execute('INSERT INTO seasons (series_id, season_number) VALUES (999, 1)')
    with tv_store.write() as conn:
        sid = conn.execute('SELECT id FROM series').fetchone()[0]
        conn.execute('INSERT INTO episodes (series_id, season_number, episode_number) VALUES (?, 1, 1)', (sid,))
        conn.execute('DELETE FROM series WHERE id = ?', (sid,))
    with tv_store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM episodes').fetchone()[0] == 0


def test_a_failed_write_rolls_back(db):
    with pytest.raises(RuntimeError):
        with tv_store.write() as conn:
            conn.execute("INSERT INTO series (tmdb_id, title, added_at) VALUES (5, 'A', 0)")
            raise RuntimeError('boom')
    with tv_store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM series').fetchone()[0] == 0


def test_corrupt_file_is_quarantined_and_starts_empty(db):
    db.parent.mkdir(parents=True)
    db.write_bytes(b'this is not a database' * 100)
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION
    assert any(p.name.startswith('tv.db.bad-') for p in db.parent.iterdir())


def test_a_newer_schema_is_refused(db):
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db))
    conn.execute('PRAGMA user_version = 99')
    conn.close()
    with pytest.raises(RuntimeError):
        tv_store.schema_version()
    assert db.exists()                                            # never thrown away


def test_migrations_run_in_order(db, monkeypatch):
    monkeypatch.setattr(tv_store, 'MIGRATIONS', tv_store.MIGRATIONS + ['ALTER TABLE series ADD COLUMN extra TEXT'])
    monkeypatch.setattr(tv_store, 'SCHEMA_VERSION', len(tv_store.MIGRATIONS))
    assert tv_store.schema_version() == len(tv_store.MIGRATIONS)
    with tv_store.connect() as conn:
        assert 'extra' in [r[1] for r in conn.execute('PRAGMA table_info(series)')]


def test_a_version_1_file_migrates_through_every_version(db):
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db))
    for stmt in tv_store._statements(tv_store.SCHEMA_V1):
        conn.execute(stmt)
    conn.execute("INSERT INTO series (tmdb_id, title, added_at, path_id, folder) VALUES (1, 'A', 0, 'tp_00000001', 'A')")
    conn.execute("INSERT INTO episode_files (series_id, path_id, rel_path, origin, added_at)"
                 " VALUES (1, 'tp_00000001', 'A/a.mkv', 'scan', 0)")
    conn.execute('PRAGMA user_version = 1')
    conn.commit()
    conn.close()
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION == 10       # 1 -> 2 -> ... -> 10, every step
    with tv_store.connect() as conn:
        cols = lambda t: {r[1] for r in conn.execute(f'PRAGMA table_info({t})')}            # noqa: E731
        assert {'state', 'parsed'} <= cols('episode_files')                                 # v2
        assert {'tier', 'pick'} <= cols('unmatched') and 'match_tier' in cols('series')    # v3
        assert {'airstamp', 'air_runtime', 'still_path', 'tvdb_image'} <= cols('episodes')  # v4, v5
        assert {'tvmaze_id', 'tvmaze_checked_at', 'stills_at'} <= cols('series')           # v4, v6
        assert {'episode_numbering', 'tmdb_cache', 'meta'} <= {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert conn.execute('SELECT state, parsed FROM episode_files').fetchone()[:] == ('linked', None)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO series (tmdb_id, title, added_at, path_id, folder) VALUES (2, 'B', 0, 'tp_00000001', 'A')")
        conn.execute("INSERT INTO unmatched (path_id, folder, ts) VALUES ('tp_00000001', 'X', 0)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO unmatched (path_id, folder, ts) VALUES ('tp_00000001', 'X', 1)")


def test_search_log_and_counts(db):
    for i in range(3):
        tv_store.log_search(tracker_key='site:iptorrents', purpose='episode', series_id=1, query='Show S01E01',
                            results=4, outcome='ok', now=T0 + i)
    tv_store.log_search(tracker_key='site:iptorrents', purpose='feed', now=T0 + 5)
    tv_store.log_search(tracker_key='host:a.example', purpose='season', now=T0 - 2 * 86400)
    assert tv_store.search_counts('tv', now=T0 + 10) == {'site:iptorrents': {'episode': 3, 'feed': 1}}
    tv_store.log_search(tracker_key='site:x', purpose='manual', now=T0 + 40 * 86400)   # prunes > 30 days
    with tv_store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM search_log').fetchone()[0] == 1
    with pytest.raises(ValueError):
        tv_store.log_search(tracker_key='site:x', purpose='rss')
    with pytest.raises(ValueError):
        tv_store.log_search(tracker_key='', purpose='feed')


def test_history(db):
    hid = tv_store.add_history('grabbed', episode_ids=[1, 2], detail={'title': 'x'}, now=T0)
    with tv_store.connect() as conn:
        row = conn.execute('SELECT * FROM history WHERE id = ?', (hid,)).fetchone()
    assert row['event'] == 'grabbed' and json.loads(row['episode_ids']) == [1, 2]


def test_meta(db):
    assert tv_store.get_meta('last_scan') is None
    tv_store.set_meta('last_scan', '1.5')
    tv_store.set_meta('last_scan', '2.5')
    assert tv_store.get_meta('last_scan') == '2.5'
    tv_store.set_meta('last_scan', None)
    assert tv_store.get_meta('last_scan') is None


def test_an_upgrade_backs_up_the_old_file_first_and_a_fresh_create_does_not(db):
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db))
    conn.execute('PRAGMA journal_mode = WAL')
    for stmt in tv_store._statements(tv_store.SCHEMA_V1):
        conn.execute(stmt)
    conn.execute("INSERT INTO series (tmdb_id, title, added_at) VALUES (7, 'In the WAL', 0)")
    conn.execute('PRAGMA user_version = 1')
    conn.commit()                                 # left in the -wal (connection still open, no checkpoint)
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION
    conn.close()
    bak = db.parent / 'tv.db.bak-v1'
    assert bak.exists() and stat.S_IMODE(os.stat(bak).st_mode) == 0o600
    b = sqlite3.connect(str(bak))
    assert b.execute('PRAGMA user_version').fetchone()[0] == 1                    # the old schema, untouched
    assert b.execute('SELECT title FROM series').fetchall() == [('In the WAL',)]   # the -wal's rows made it
    b.close()
    assert sorted(p.name for p in db.parent.iterdir() if '.bak-' in p.name) == ['tv.db.bak-v1']


def test_a_fresh_create_makes_no_backup(db):
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION
    assert not [p for p in db.parent.iterdir() if '.bak-' in p.name]


def _v1_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    for stmt in tv_store._statements(tv_store.SCHEMA_V1):
        conn.execute(stmt)
    conn.execute('PRAGMA user_version = 1')
    conn.commit()
    conn.close()


@pytest.mark.parametrize('minimal_build', [False, True], ids=['full', 'minimal'])
def test_app_creation_migrates_tv_db_to_the_current_schema(tmp_path, minimal_build):
    """Importing app.main (how both builds create the app) migrates tv.db at
    boot, backup first: no request needed."""
    import subprocess
    import sys
    data = tmp_path / 'data'
    _v1_file(data / 'tv.db')
    env = {**os.environ, 'LUMA_DATA_DIR': str(data), 'BASE_DIR': str(data),
           'DATABASE_PATH': str(data / 'media.db'), 'SETTINGS_PATH': str(data / 'settings.json'),
           'LUMA_SKIP_BACKGROUND_THREADS': '1'}
    env.pop('LUMA_MINIMAL', None)
    if minimal_build:
        env['LUMA_MINIMAL'] = '1'
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, '-c', 'import app.main'], cwd=str(root), env=env,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]
    conn = sqlite3.connect(str(data / 'tv.db'))
    assert conn.execute('PRAGMA user_version').fetchone()[0] == tv_store.SCHEMA_VERSION
    conn.close()
    assert (data / 'tv.db.bak-v1').exists()
    assert 'tv.db ready at schema v%d' % tv_store.SCHEMA_VERSION in r.stderr + r.stdout


def test_a_failed_boot_migration_is_logged_and_retried_lazily(db, monkeypatch, caplog):
    _v1_file(db)
    real = tv_store._migrate
    monkeypatch.setattr(tv_store, '_migrate', lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked')))
    with caplog.at_level('ERROR', logger='app.tv_store'):
        assert tv_store.migrate_on_start() is False          # never raises: boot goes on
    assert 'will retry on first use' in caplog.text
    monkeypatch.setattr(tv_store, '_migrate', real)
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION   # the lazy path migrates it


# ------------------------------------------------ verified atomic backup (Tester/Reviewer, v157)

def test_a_full_disk_during_the_backup_never_migrates_and_leaves_no_partial_file(db, monkeypatch, caplog):
    import errno
    _v1_file(db)

    def full(conn, out):
        out.execute('CREATE TABLE half (x)')                     # some bytes went out, then the disk filled up
        out.commit()
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(tv_store, '_copy', full)
    with caplog.at_level('ERROR', logger='app.tv_store'):
        assert tv_store.migrate_on_start() is False             # boot goes on
    assert 'no verified backup' in caplog.text
    left = sorted(f.name for f in db.parent.iterdir())
    assert not any('.bak-' in f for f in left), left             # no backup, no temp, no -journal
    c = sqlite3.connect(str(db))
    assert c.execute('PRAGMA user_version').fetchone()[0] == 1  # still the old schema
    c.close()
    monkeypatch.undo()
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION  # space back: the next open backs up, then migrates
    assert (db.parent / 'tv.db.bak-v1').exists()


def test_a_bad_existing_backup_is_made_again_and_a_good_one_kept(db):
    _v1_file(db)
    bak = db.parent / 'tv.db.bak-v1'
    bak.write_bytes(b'')                                         # empty: not a backup
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION
    c = sqlite3.connect(str(bak))
    assert c.execute('PRAGMA user_version').fetchone()[0] == 1 and c.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    c.close()
    assert stat.S_IMODE(os.stat(bak).st_mode) == 0o600
    assert not [f for f in db.parent.iterdir() if '.tmp' in f.name]


@pytest.mark.parametrize('bad', ['garbage', 'wrong_version'])
def test_backup_check_rejects_garbage_and_the_wrong_version(tmp_path, bad):
    p = tmp_path / 'x.db'
    if bad == 'garbage':
        p.write_bytes(b'not sqlite' * 100)
    else:
        c = sqlite3.connect(str(p)); c.execute('CREATE TABLE t (x)'); c.execute('PRAGMA user_version = 3'); c.commit(); c.close()
    assert tv_store._verified(str(p), 1) is False


def test_a_good_backup_is_kept_not_overwritten(db):
    _v1_file(db)
    bak = db.parent / 'tv.db.bak-v1'
    c = sqlite3.connect(str(bak)); c.execute('CREATE TABLE marker (x)'); c.execute('PRAGMA user_version = 1'); c.commit(); c.close()
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION
    c = sqlite3.connect(str(bak))
    assert c.execute("SELECT 1 FROM sqlite_master WHERE name = 'marker'").fetchone()   # the older copy stays
    c.close()
