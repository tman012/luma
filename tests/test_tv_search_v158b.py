"""v158b: TV searching and grabbing (feed, after-air, on Add, manual, 12 h refresh), the
test-instance write guard for grabs, and the owner's trackers only."""
import json

import pytest
import requests

from app import instance, library_mover as lm, tv_downloads, tv_grab, tv_safety, tv_search, tv_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_library_mover import Resp, Settings
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

CFG = {'id': 7, 'name': 'MyTracker', 'type': 'torznab', 'priority': 1}
T = b'd8:announce3:url4:infod4:name4:file6:lengthi1eee'          # a tiny .torrent


def sev():
    return rows('SELECT id FROM series WHERE tmdb_id = 95396')[0]['id']


def eps(sid, season):
    return {r['episode_number']: r['id'] for r in rows('SELECT id, episode_number FROM episodes WHERE series_id = ? AND season_number = ?', sid, season)}


def item(title, seeders=10, size=2 * 1024 ** 3, tracker=7):
    return {'title': title, 'downloadUrl': '/api/trackers/release/' + 'x' * 20, 'indexerId': tracker,
            'indexer': 'MyTracker', 'seeders': seeders, 'size': size}


class FakeQ:
    def __init__(self):
        self.calls = []
        self.cats = {}

    def factory(self):
        q = self

        class S:
            def post(self, url, data=None, files=None, **kw):
                q.calls.append((url, dict(data or {}), bool(files)))
                if url.endswith('/torrents/createCategory'):
                    q.cats[(data or {})['category']] = {'name': (data or {})['category'], 'savePath': ''}
                return Resp()

            def get(self, url, *a, **k):
                if url.endswith('/torrents/categories'):
                    return Resp(data=dict(q.cats))
                return Resp(data=[])

            def close(self):
                pass
        return S()


@pytest.fixture
def env(scanned, monkeypatch):  # noqa: F811
    monkeypatch.delenv('LUMA_TEST_TV_TAG', raising=False)
    monkeypatch.delenv('LUMA_TV_CATEGORY', raising=False)
    monkeypatch.delenv('LUMA_TEST_ALLOW', raising=False)
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    lm.init(settings=Settings())
    tv_grab._have_category.clear()
    q = FakeQ()
    monkeypatch.setattr(tv_search, 'trackers', lambda: [CFG])
    sent = []

    def fake_query(query, purpose, sid, interactive, now, cfgs=None, tv_ids=None, **k):
        if tv_ids:          # v256: this fake tracker has no id search (test_v256_id_first covers the ID tier)
            return [], [{'id': 7, 'name': 'MyTracker', 'status': tv_search.NO_ID}]
        sent.append((query, purpose, interactive))
        return list(env.results), [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
    monkeypatch.setattr(tv_search, 'query_trackers', fake_query)
    real = tv_grab.grab
    monkeypatch.setattr(tv_grab, 'grab', lambda it, sid, season, ids, origin, now=None, **k:
                        real(it, sid, season, ids, origin, now, session_factory=q.factory, fetch=lambda tok: ('torrent', T)))

    class E:
        pass
    env = E()
    env.q, env.sent, env.results, env.lib = q, sent, [], scanned
    sid = sev()
    e2 = eps(sid, 2)
    with tv_store.write() as conn:                      # S02E03..E06 wanted (aired, no file, monitored)
        conn.execute('UPDATE series SET monitored = 1 WHERE id = ?', (sid,))
        conn.execute('UPDATE episodes SET monitored = 0 WHERE series_id = ?', (sid,))
        conn.executemany('UPDATE episodes SET monitored = 1 WHERE id = ?', [(e2[n],) for n in (3, 4, 5, 6)])
    env.sid, env.e2 = sid, e2
    return env


def allow_grabs(monkeypatch):
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')


# ------------------------------------------------------------------ choosing

def test_picks_the_profile_order_and_covers_the_most_with_a_pack(env):
    with tv_store.connect() as conn:
        show = tv_search._show(conn, env.sid)
        want = tv_search.wanted(conn, env.sid, today='2026-10-18')
    assert {w['episode_number'] for w in want} == {3, 4, 6}                    # S02E05 has a file
    its = [item('Severance.S02E03.720p.HDTV.x264-A'), item('Severance.S02E03.1080p.WEB-DL.DDP5.1-B'),
           item('Severance.S02.1080p.WEB-DL.DDP5.1-PACK'), item('Severance.S02E04.2160p.WEB-DL-C', seeders=0),
           item('Other.Show.S02E03.1080p.WEB-DL-D'), item('Severance.2019.S02E03.1080p.WEB-DL-E')]
    picks = tv_search.choose(tv_search.candidates(its, show, want))
    assert [p['item']['title'] for p in picks] == ['Severance.S02.1080p.WEB-DL.DDP5.1-PACK']
    assert sorted(picks[0]['covers']) == sorted(env.e2[n] for n in (3, 4, 6))
    blocked = {'Severance.S02.1080p.WEB-DL.DDP5.1-PACK'}
    picks = tv_search.choose(tv_search.candidates(its, show, want, blocked))
    assert [p['item']['title'] for p in picks] == ['Severance.S02E03.1080p.WEB-DL.DDP5.1-B']


# ------------------------------------------------------------------ grabs and the test instance

def test_test_instance_grabs_are_off_by_default_and_nothing_reaches_qbit(env):
    env.results = [item('Severance.S02E03.1080p.WEB-DL-B')]
    out = tv_search.manual(env.sid, episode_id=env.e2[3], now=5000.0)
    assert out['ok'] and out['grabs_allowed'] is False
    assert out['picks'][0]['grabbed'] is False and out['picks'][0]['error'] == tv_grab.OFF
    assert env.q.calls == [] and not rows('SELECT 1 FROM grabs')
    assert env.sent == [('Severance S02E03', 'manual', True)]          # manual: interactive budget


def test_allowed_grab_uses_only_the_tv_category_and_tag(env, monkeypatch):
    allow_grabs(monkeypatch)
    env.results = [item('Severance.S02E03.1080p.WEB-DL-B')]
    out = tv_search.manual(env.sid, episode_id=env.e2[3], now=5000.0)
    assert out['picks'][0]['grabbed'] is True
    (url, data, has_file), = [c for c in env.q.calls if c[0].endswith('/torrents/add')]
    assert url.endswith('/api/v2/torrents/add') and has_file
    assert data['category'] == data['tags'] == 'luma-test-tv' and 'savepath' not in data
    g = rows('SELECT * FROM grabs')[0]
    assert (g['series_id'], g['origin'], json.loads(g['episode_ids'])) == (env.sid, 'manual', [env.e2[3]])
    env.q.calls.clear()                                              # already grabbed: not wanted again
    out = tv_search.manual(env.sid, episode_id=env.e2[3], now=5100.0)
    assert out['wanted'] == 0 and not [c for c in env.q.calls if c[0].endswith('/torrents/add')]


def test_write_guard_lets_only_the_scoped_tv_add_through(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.delenv('LUMA_TEST_ALLOW', raising=False)
    sent = []

    def fake_send(self, request, **kw):
        sent.append(request.url)
        r = requests.Response()
        r.status_code = 200
        r._content = b'Ok.'
        return r
    monkeypatch.setattr(requests.Session, 'send', fake_send)
    instance.uninstall_write_guard()
    monkeypatch.setattr(instance, '_installed', {'on': False})
    instance.install_write_guard()
    try:
        s = requests.Session()
        add = 'http://qbit/api/v2/torrents/add'
        with pytest.raises(instance.WritesBlocked):                                  # no scope
            s.post(add, data={'urls': 'magnet:?', 'category': 'luma-test-tv', 'tags': 'luma-test-tv'})
        with instance.scoped_add('tv_grab', 'luma-test-tv'):
            with pytest.raises(instance.WritesBlocked):                              # tv_grab not allowed
                s.post(add, data={'urls': 'magnet:?', 'category': 'luma-test-tv', 'tags': 'luma-test-tv'})
        monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')
        with instance.scoped_add('tv_grab', 'luma-test-tv'):
            s.post(add, data={'urls': 'magnet:?', 'category': 'luma-test-tv', 'tags': 'luma-test-tv', 'autoTMM': 'false'})
            s.post(add, files={'torrents': ('r.torrent', T)},
                   data={'category': 'luma-test-tv', 'tags': 'luma-test-tv', 'autoTMM': 'false'})
            for bad in ({'category': 'luma-test-tv', 'tags': 'luma-test-tv,sonarr', 'autoTMM': 'false'},
                        {'category': 'tv', 'tags': 'luma-test-tv', 'autoTMM': 'false'},
                        {'category': 'luma-test-tv', 'tags': 'luma-test-tv', 'autoTMM': 'false', 'savepath': '/x'},
                        {'category': 'luma-test-tv', 'tags': 'luma-test-tv'},                 # v166: autoTMM missing
                        {'category': 'luma-test-tv', 'tags': 'luma-test-tv', 'autoTMM': 'true'}):
                with pytest.raises(instance.WritesBlocked):
                    s.post(add, data={'urls': 'magnet:?', **bad})
            with pytest.raises(instance.WritesBlocked):                              # any other write
                s.post('http://qbit/api/v2/torrents/delete', data={'hashes': 'all', 'deleteFiles': 'true'})
        with pytest.raises(instance.WritesBlocked):                                  # scope ended
            s.post(add, data={'urls': 'magnet:?', 'category': 'luma-test-tv', 'tags': 'luma-test-tv'})
        assert len(sent) == 2
    finally:
        instance.uninstall_write_guard()


def test_info_hash_from_torrent_and_magnet():
    import hashlib
    assert tv_grab.info_hash('torrent', T) == hashlib.sha1(b'd4:name4:file6:lengthi1ee').hexdigest()
    assert tv_grab.info_hash('magnet', 'magnet:?xt=urn:btih:' + 'A' * 40) == 'a' * 40
    assert tv_grab.info_hash('torrent', b'nope') is None


# ------------------------------------------------------------------ automatic searching

def _auto(on=True, feed=False, backlog=False, delay=4):
    st = tv_settings.load()
    st['automation'].update(enabled=on, feed_enabled=feed, backlog_enabled=backlog, first_search_delay_h=delay)
    tv_settings.save(st)


def test_nothing_automatic_unless_automation_is_on_and_grabs_are_allowed(env, monkeypatch):
    env.results = [item('Severance.S02E03.1080p.WEB-DL-B')]
    monkeypatch.setattr(tv_search, 'refresh', lambda now: 0)
    now = tv_search.aired_at(rows('SELECT air_date, airstamp, runtime FROM episodes WHERE id = ?', env.e2[3])[0]) + 5 * 3600
    assert tv_search.tick(now=now)['scheduled'] == 0 and env.sent == []                # automation off
    _auto(feed=True)
    assert tv_search.tick(now=now)['scheduled'] == 0 and env.sent == []                # grabs off on test
    allow_grabs(monkeypatch)
    out = tv_search.tick(now=now)
    assert out['scheduled'] >= 1 and env.sent


def test_after_air_search_waits_the_delay_and_backs_off(env, monkeypatch):
    allow_grabs(monkeypatch)
    _auto(delay=4)
    e3 = env.e2[3]
    with tv_store.write() as conn:              # v185.2a: the delay applies to an episode with a runtime
        conn.execute('UPDATE episodes SET runtime = 50 WHERE id = ?', (e3,))
    aired = tv_search.aired_at(rows('SELECT air_date, airstamp, runtime FROM episodes WHERE id = ?', e3)[0])
    now = aired + 60
    tv_search.schedule(now)
    nxt = rows('SELECT next_search_at FROM episodes WHERE id = ?', e3)[0]['next_search_at']
    assert nxt == pytest.approx(aired)   # v196a (Taylor): no first-search delay
    assert tv_search.run_due(aired - 1)['shows'] == 0                                    # not before air + runtime
    env.results = []
    assert tv_search.run_due(nxt + 1)['shows'] == 1
    r = rows('SELECT next_search_at, search_attempts FROM episodes WHERE id = ?', e3)[0]
    assert r['search_attempts'] == 1 and r['next_search_at'] == pytest.approx(aired + 3600)   # v256: at air, then +1 h
    env.results = [item('Severance.S02E03.1080p.WEB-DL-B')]
    assert tv_search.run_due(r['next_search_at'] + 1)['grabbed'] >= 1
    assert rows('SELECT next_search_at FROM episodes WHERE id = ?', e3)[0]['next_search_at'] is None


def test_search_on_add_follows_the_monitor_option(env, monkeypatch, owner):  # noqa: F811
    from app import tv_calendar
    import datetime as _dt
    from tests import tv_fake
    allow_grabs(monkeypatch)
    _auto()
    today = _dt.date.today()                       # v225: relative to today (the fixture's fixed E2 10-21 aired on that day)
    monkeypatch.setitem(tv_fake.SHOWS, 6001, {**tv_fake.SHOWS[6001], 'air': {
        (1, 1): (today - _dt.timedelta(days=7)).isoformat(), (1, 2): (today + _dt.timedelta(days=7)).isoformat(),
        (1, 3): (today + _dt.timedelta(days=14)).isoformat()}})
    out = tv_calendar.add(6001, PID, now=3000.0, monitor='all')                         # Severance Pay: E1 aired
    sid = out['series_id']
    due = {r['episode_number'] for r in rows('SELECT episode_number FROM episodes WHERE series_id = ?'
                                                 ' AND season_number = 1 AND next_search_at = 3000.0', sid)}
    aired_ = {r['episode_number'] for r in rows("SELECT episode_number FROM episodes WHERE series_id = ? AND season_number = 1"
                                                " AND air_date <= date('now', 'localtime')", sid)}
    assert 1 in due and 2 not in due and due == aired_                               # aired ones only (E2 airs next week)
    with tv_store.write() as conn:
        conn.execute('DELETE FROM series WHERE id = ?', (sid,))
    out = tv_calendar.add(6001, PID, now=3000.0, monitor='future')                      # Future: nothing aired wanted
    assert not rows('SELECT 1 FROM episodes WHERE series_id = ? AND next_search_at IS NOT NULL', out['series_id'])


def test_feed_polls_an_uncapped_indexer_every_15_min_and_grabs_wanted(env, monkeypatch):
    allow_grabs(monkeypatch)
    _auto(feed=True)
    env.results = [item('Severance.S02E04.1080p.WEB-DL-B'), item('Nobody.S01E01.1080p.WEB-DL-X')]
    # v177: inside the 24 h air-date floor (an hour after S02E04 aired; test_v177_tv_safety: never later)
    e4 = rows('SELECT air_date, airstamp, runtime FROM episodes WHERE id = ?', env.e2[4])[0]
    t0 = tv_safety.aired_start(e4) + 3600
    assert tv_search.feed(t0) == {'trackers': 1, 'grabbed': 1}
    assert env.sent[-1] == ('', 'feed', False)
    assert tv_search.feed(t0 + 14 * 60) == {'trackers': 0, 'grabbed': 0}                 # v158c: no cap -> 15 min
    assert tv_search.feed(t0 + 15 * 60) == {'trackers': 1, 'grabbed': 0}                 # already grabbed


def test_only_the_owners_tv_trackers_and_none_preconfigured(monkeypatch, tv_env):  # noqa: F811
    from app import section_trackers
    monkeypatch.setattr(section_trackers, 'ordered_ids', lambda section: [])
    assert tv_search.trackers() == []

    class Store:
        def enabled_configs(self):
            return [{'id': 1, 'name': 'A'}, {'id': 2, 'name': 'B'}, {'id': 3, 'name': 'C'}]

    class Eng:
        store = Store()
    monkeypatch.setattr('app.tracker_search.get_search', lambda: Eng())
    monkeypatch.setattr(section_trackers, 'ordered_ids', lambda section: [3, 1] if section == 'tv' else [2])
    assert [c['id'] for c in tv_search.trackers()] == [3, 1]


def test_series_refresh_every_12_hours(env, monkeypatch):
    from app import tv_scan
    calls = []
    monkeypatch.setattr(tv_scan, 'refresh_added', lambda now, **k: calls.append((now, k)) or 2)
    assert tv_search.refresh(1e9) == 2 and calls[0][1]['stale_s'] == 12 * 3600 and calls[0][1]['folders']
    assert tv_search.refresh(1e9 + 11 * 3600) == 0
    assert tv_search.refresh(1e9 + 12 * 3600) == 2 and len(calls) == 2


# ------------------------------------------------------------------ routes

def test_manual_search_route_needs_the_signed_in_owner_and_valid_body(env, owner, monkeypatch):  # noqa: F811
    from flask import jsonify
    from tests.test_tv_monitor_v158a import _req
    env.results = [item('Severance.S02.1080p.WEB-DL-PACK')]
    u = f'/api/settings/tv/shows/{env.sid}/search'
    assert _req(owner, 'post', u, {'season': 2, 'episode_id': 1}).status_code == 400
    assert _req(owner, 'post', u, {}).status_code == 400
    from app import tv_store
    with tv_store.write() as c:                     # v184: a pack only for a season with no file yet
        c.execute('DELETE FROM episode_file_links WHERE episode_id IN (SELECT id FROM episodes WHERE series_id = ? AND season_number = 2)', (env.sid,))
    r = _req(owner, 'post', u, {'season': 2})
    assert r.status_code == 200 and r.get_json()['picks'][0]['title'] == 'Severance.S02.1080p.WEB-DL-PACK'
    assert _req(owner, 'post', '/api/settings/tv/shows/999999/search', {'season': 1}).status_code == 404
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', lambda: (None, (jsonify({'error': 'Sign in'}), 401)))
    assert _req(owner, 'post', u, {'season': 2}).status_code == 401
