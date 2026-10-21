"""v158c (PM): capped trackers poll within their daily budget, spread evenly (an automatic search
may take a free slot); indexers with no cap every 15 min. The TV category is created on the first
grab when it's missing; when that fails nothing is added and the job shows the error. The TV
Sources popup grabs through Luma's own path (Sonarr only as a fallback)."""
import json

import pytest
import requests

from app import instance, library_mover as lm, tracker_guard, tv_budget, tv_downloads, tv_grab, tv_search, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_library_mover import Resp, Settings
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_tv_search_v158b import T, allow_grabs, env, eps, item, sev  # noqa: F401

SITE = {'id': 1, 'name': 'IPT', 'type': 'iptorrents', 'url': 'https://iptorrents.com'}
GEN = {'id': 2, 'name': 'Indexer', 'type': 'torznab', 'url': 'https://idx.example.org'}


@pytest.fixture
def guard(tmp_path, monkeypatch):
    g = tracker_guard.TrackerGuard(tmp_path / 'g.json')
    monkeypatch.setattr(tv_budget, '_guard', lambda: g)
    return g


def take(g, cfg, at):
    assert tv_budget.take(cfg, at, guard=g) is None


# ------------------------------------------------------------------ ruling 1: slots

@pytest.mark.skip(reason='v204a (PM, Taylor): Luma daily per-tracker cap removed; see test_v204a_no_daily_cap')
def test_capped_tracker_slots_are_spread_evenly_and_the_cap_is_not_raised(guard):
    assert tv_budget.AUTO_PER_TRACKER_DAILY == 8 and tv_budget.SLOT_S == 3 * 3600
    assert tv_budget.capped(SITE) and not tv_budget.capped(GEN)
    t = 2e9
    assert tv_budget.slot_free(SITE, t)
    for i in range(8):                                           # one per 3 h fills the day
        at = t + i * tv_budget.SLOT_S
        assert tv_budget.slot_free(SITE, at)
        take(guard, SITE, at)
        assert not tv_budget.slot_free(SITE, at + tv_budget.SLOT_S - 1)
        assert tv_budget.next_slot(SITE, at) == at + tv_budget.SLOT_S
    assert tv_budget.usage(SITE, t + 8 * tv_budget.SLOT_S - 1, guard=guard) == {'used': 8, 'cap': 8}
    assert tv_budget.slot_free(SITE, t + 8 * tv_budget.SLOT_S)           # the first one aged out
    assert tv_budget.slot_free(GEN, t) and tv_budget.next_slot(GEN, t) is None
    assert tv_budget.usage(GEN, t, guard=guard)['cap'] is None


class Eng:
    def __init__(self):
        self.calls = []

        class Tok:
            def flush(self):
                pass
        self.tokens = Tok()

    def search_one(self, cfg, query, cats, limit, interactive, purpose=None, **k):
        self.calls.append((cfg['id'], query, interactive, purpose))
        if purpose == 'feed':                  # v185.2a: the guard's own 'feed' purpose (no caps)
            from app.tracker_guard import guard_key
            assert tv_budget._guard().take(guard_key(cfg), False, True, now=self.now, purpose='feed') is None
        elif purpose:
            assert tv_budget.take(cfg, self.now) is None
        return [{'title': f'{query} r'}]

    def to_item(self, cfg, r, sv):
        return {'title': r['title'], 'downloadUrl': '/x', 'indexerId': cfg['id']}


@pytest.fixture
def engine(monkeypatch, guard, tv_env):  # noqa: F811
    from app import tracker_search
    e = Eng()
    monkeypatch.setattr(tracker_search, 'get_search', lambda: e)
    monkeypatch.setattr(tracker_search, '_match_categories', lambda cfg, cats: cats)
    monkeypatch.setattr(tracker_search, '_secret_values', lambda cfg: set())
    monkeypatch.setattr(tv_search, 'trackers', lambda: [SITE, GEN])
    return e


def test_automatic_searches_take_a_free_slot_on_capped_trackers_only(engine):
    t = 2e9
    engine.now = t
    items, st = tv_search.query_trackers('Show S01E01', 'episode', None, False, t)
    assert engine.calls == [(1, 'Show S01E01', False, 'tv'), (2, 'Show S01E01', False, None)]
    engine.calls.clear()
    engine.now = t + 600
    items, st = tv_search.query_trackers('Show S01E02', 'episode', None, False, t + 600)
    # v196c (Taylor): no 3 h slot for post-air searches; only the daily cap (and the guard) hold IPT back
    assert engine.calls == [(1, 'Show S01E02', False, 'tv'), (2, 'Show S01E02', False, None)]
    assert {s['id']: s['status'] for s in st} == {1: 'ok', 2: 'ok'}
    engine.calls.clear()                                                     # manual: interactive, no slots
    tv_search.query_trackers('Show S01E02', 'manual', None, True, t + 600)
    assert engine.calls == [(1, 'Show S01E02', True, None), (2, 'Show S01E02', True, None)]


def test_feed_capped_in_free_slots_uncapped_every_15_min_and_a_post_air_search_moves_the_feed(engine, guard):
    """v185.2a (Taylor): every feed, a capped site's (IPT) too, is due every 15 min; the guard's caps still hold."""
    t = 2e9
    engine.now = t
    assert tv_search.feed_due(SITE, t) and tv_search.feed_due(GEN, t)
    tv_search.feed(t)
    assert [c[0] for c in engine.calls] == [1, 2]
    for cfg in (SITE, GEN):
        assert tv_search.feed_due(cfg, t + 15 * 60) and not tv_search.feed_due(cfg, t + 14 * 60)


@pytest.mark.skip(reason='v204a (PM, Taylor): Luma daily per-tracker cap removed; see test_v204a_no_daily_cap')
def test_status_reports_slots(engine):
    out = tv_search.status(2e9)
    assert out['slot_s'] == 3 * 3600 and out['feed_every_s'] == 15 * 60
    assert {x['id']: x['cap'] for x in out['trackers']} == {1: 8, 2: None}


# ------------------------------------------------------------------ ruling 4: the category, against a fake qBit

class FakeQbit:
    def __init__(self, cats=None):
        self.cats = dict(cats or {})
        self.calls = []
        self.torrents = []
        self.fail_create = None        # None | int status | Exception
        self.fail_read = False

    def factory(self):
        q = self

        class S:
            def post(self, url, data=None, files=None, **kw):
                q.calls.append(('POST', url.rsplit('/api/v2/', 1)[-1], dict(data or {})))
                if url.endswith('/torrents/createCategory'):
                    if isinstance(q.fail_create, Exception):
                        raise q.fail_create
                    if q.fail_create:
                        return Resp(q.fail_create, 'Fails.')
                    q.cats[data['category']] = {'name': data['category'], 'savePath': ''}
                if url.endswith('/torrents/add'):
                    if (data or {}).get('category') not in q.cats:
                        return Resp(409, 'Fails.')
                    q.torrents.append(dict(data))
                return Resp()

            def get(self, url, *a, **k):
                q.calls.append(('GET', url.rsplit('/api/v2/', 1)[-1], {}))
                if url.endswith('/torrents/categories'):
                    return Resp(500, '') if q.fail_read else Resp(data=dict(q.cats))
                return Resp(data=[])

            def close(self):
                pass
        return S()


@pytest.fixture
def qenv(scanned, monkeypatch):  # noqa: F811
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')
    monkeypatch.delenv('LUMA_TEST_TV_TAG', raising=False)
    monkeypatch.delenv('LUMA_TV_CATEGORY', raising=False)
    lm.init(settings=Settings())
    tv_grab._have_category.clear()
    return sev()


T2 = b'd8:announce3:url4:infod4:name5:file26:lengthi1eee'


def _grab(q, sid, title='Severance.S02E03.1080p.WEB-DL-B', now=5000.0, payload=T):
    e3 = eps(sid, 2)[3]
    return tv_grab.grab(item(title), sid, 2, [e3], 'manual', now, session_factory=q.factory, fetch=lambda u: ('torrent', payload))


def test_first_grab_creates_the_missing_category_then_adds(qenv):
    q = FakeQbit()
    assert _grab(q, qenv)['ok']
    ops = [c[1] for c in q.calls if c[1] != 'auth/login']
    assert ops == ['torrents/categories', 'torrents/createCategory', 'torrents/add']
    create = [c for c in q.calls if c[1] == 'torrents/createCategory'][0][2]
    assert create == {'category': 'luma-test-tv'}                             # no save path
    assert q.torrents[0]['category'] == q.torrents[0]['tags'] == 'luma-test-tv'
    q.calls.clear()                                                           # checked once per run
    out = _grab(q, qenv, 'Severance.S02E04.1080p.WEB-DL-B', payload=T2)
    assert out['ok'] and not out.get('existing') and len(q.torrents) == 2
    assert 'torrents/categories' not in [c[1] for c in q.calls]


def test_an_existing_category_is_not_created_again(qenv):
    q = FakeQbit({'luma-test-tv': {}})
    assert _grab(q, qenv)['ok']
    assert 'torrents/createCategory' not in [c[1] for c in q.calls]


@pytest.mark.parametrize('fail', [500, requests.ConnectionError('down'), 'read'])
def test_category_failure_adds_nothing_and_shows_on_the_job(qenv, fail):
    q = FakeQbit()
    if fail == 'read':
        q.fail_read = True
        want = tv_grab.CATEGORY_UNREAD
    else:
        q.fail_create = fail
        want = "Couldn't create the qBittorrent category luma-test-tv"
    out = _grab(q, qenv)
    assert out == {'ok': False, 'error': want}
    assert q.torrents == [] and 'torrents/add' not in [c[1] for c in q.calls]   # no half-added torrent
    assert not rows('SELECT 1 FROM grabs')
    (job,) = tv_downloads.failures(now=5001.0)
    assert job['state'] == 'grab_failed' and job['error'] == want and job['retry'] is False
    assert job['name'] == 'Severance.S02E03.1080p.WEB-DL-B'
    q.fail_create, q.fail_read = None, False                                  # fixed: the next grab clears the job
    assert _grab(q, qenv, now=6000.0)['ok']
    assert tv_downloads.failures(now=6001.0) == []


def test_a_409_race_where_the_category_now_exists_is_fine(qenv):
    q = FakeQbit()
    orig = q.factory

    def factory():
        s = orig()
        post = s.post

        def p(url, data=None, files=None, **kw):
            if url.endswith('/torrents/createCategory'):
                q.cats['luma-test-tv'] = {}
                return Resp(409, 'Fails.')
            return post(url, data, files, **kw)
        s.post = p
        return s
    q.factory = factory
    assert _grab(q, qenv)['ok'] and len(q.torrents) == 1


def test_an_unexpected_error_in_the_category_step_never_crashes(qenv, monkeypatch):
    monkeypatch.setattr(tv_grab, '_categories', lambda s, u: 1 / 0)
    q = FakeQbit()
    out = _grab(q, qenv)
    assert out['ok'] is False and 'category' in out['error'] and q.torrents == []


def test_write_guard_lets_only_this_category_be_created(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')
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
        cc = 'http://qbit/api/v2/torrents/createCategory'
        with pytest.raises(instance.WritesBlocked):
            s.post(cc, data={'category': 'luma-test-tv'})                    # no scope
        with instance.scoped_add('tv_grab', 'luma-test-tv'):
            s.post(cc, data={'category': 'luma-test-tv'})
            for bad in ({'category': 'luma-tv'}, {'category': 'luma-test-tv', 'savePath': '/data'}):
                with pytest.raises(instance.WritesBlocked):
                    s.post(cc, data=bad)
            for other in ('editCategory', 'removeCategories'):
                with pytest.raises(instance.WritesBlocked):
                    s.post(f'http://qbit/api/v2/torrents/{other}', data={'category': 'luma-test-tv'})
        monkeypatch.setenv('LUMA_TEST_ALLOW', '')
        with instance.scoped_add('tv_grab', 'luma-test-tv'):
            with pytest.raises(instance.WritesBlocked):                       # tv_grab off
                s.post(cc, data={'category': 'luma-test-tv'})
        assert len(sent) == 1
    finally:
        instance.uninstall_write_guard()


# ------------------------------------------------------------------ ruling 2: the job list

def test_failures_flag_only_no_space_rows_for_retry(qenv):
    tv_store.set_meta('tvdl:' + 'a' * 40, json.dumps({'state': 'failed', 'error': lm.INSUFFICIENT_SPACE, 'name': 'A', 'at': 10}))
    tv_store.set_meta('tvdl:' + 'b' * 40, json.dumps({'state': 'refused', 'error': 'no show in the library', 'name': 'B', 'at': 20}))
    tv_store.set_meta('tvgrab:' + 'c' * 40, json.dumps({'state': 'grab_failed', 'error': 'x', 'name': 'C', 'at': 30}))
    tv_store.set_meta('tvgrab:' + 'd' * 40, json.dumps({'state': 'grab_failed', 'error': 'old', 'name': 'D', 'at': 1}))
    out = tv_downloads.failures(now=7 * 86400 + 20)
    assert [(f['name'], f['retry']) for f in out] == [('C', False), ('B', False), ('A', True)]   # D: older than 7 days
    assert tv_downloads.retry('a' * 40)
    assert [f['name'] for f in tv_downloads.failures(now=7 * 86400 + 20)] == ['C', 'B']


# ------------------------------------------------------------------ ruling 3: the TV Sources popup grab

@pytest.fixture
def known_token(monkeypatch):
    """BODY's release token is one Luma's tracker search issued (v166: the route checks it's known)."""
    from app import tracker_search

    class Tokens:
        def resolve(self, t):
            return (1, 'https://tracker.example/dl/1') if t == 'x' * 20 else None

    class Eng:
        tokens = Tokens()
    monkeypatch.setattr(tracker_search, 'get_search', lambda: Eng())


@pytest.fixture
def popup(qenv, monkeypatch, known_token):
    q = FakeQbit()
    real = tv_grab.grab
    monkeypatch.setattr(tv_grab, 'grab', lambda it, sid, season, ids, origin, now=None, **k:
                        real(it, sid, season, ids, origin, now, session_factory=q.factory, fetch=lambda u: ('torrent', T)))
    return q


def _post(c, body):
    from tests.test_tv_monitor_v158a import _req
    return _req(c, 'post', '/api/settings/tv/grab', body)


BODY = {'download_url': '/api/trackers/release/' + 'x' * 20, 'title': 'Severance.S02E03.1080p.WEB-DL-B',
        'tmdb_id': 95396, 'season': 2, 'episode': 3, 'size': 123, 'indexer': 'IPT', 'quality': '1080p'}


def test_popup_grab_goes_through_luma_with_the_tv_category(popup, qenv, owner):  # noqa: F811
    r = _post(owner, BODY)
    assert r.status_code == 200 and r.get_json()['ok'] is True
    assert popup.torrents[0]['category'] == popup.torrents[0]['tags'] == 'luma-test-tv'
    g = rows('SELECT * FROM grabs')[0]
    assert (g['series_id'], g['origin'], g['season_number']) == (qenv, 'manual', 2)
    assert json.loads(g['episode_ids']) == [eps(qenv, 2)[3]]
    with tv_store.write() as conn:
        conn.execute('DELETE FROM grabs')
    pack = {**BODY, 'episode': None, 'title': 'Severance.S02.1080p.WEB-DL-PACK'}
    out = _post(owner, pack).get_json()                                       # v177: never over an episode with a file
    assert out['ok'] is False and out['error'] == 'Not grabbed: already has a file' and rows('SELECT * FROM grabs') == []
    with tv_store.write() as conn:
        conn.execute('DELETE FROM episode_file_links WHERE episode_id IN (SELECT id FROM episodes WHERE series_id = ?'
                     ' AND season_number = 2)', (qenv,))
    r = _post(owner, pack)
    assert r.get_json()['ok'] is True, r.get_json()                                        # a season: every episode of it
    g = rows('SELECT * FROM grabs')[0]
    assert sorted(json.loads(g['episode_ids'])) == sorted(eps(qenv, 2).values())


def test_popup_grab_is_off_on_test_by_default_and_never_falls_back(popup, owner, monkeypatch):  # noqa: F811
    monkeypatch.delenv('LUMA_TEST_ALLOW')
    out = _post(owner, BODY).get_json()
    assert out == {'ok': False, 'error': tv_grab.OFF, 'blocked': True, 'fallback': False}
    assert popup.calls == []


def test_popup_grab_link_failure_offers_the_sonarr_fallback(qenv, owner, monkeypatch, known_token):  # noqa: F811
    from app import tracker_sources
    real = tv_grab.grab

    def bad(u):
        raise tracker_sources.GrabError('This download link has expired. Search again.', 410)
    monkeypatch.setattr(tv_grab, 'grab', lambda it, sid, season, ids, origin, now=None, **k:
                        real(it, sid, season, ids, origin, now, session_factory=FakeQbit().factory, fetch=bad))
    out = _post(owner, BODY).get_json()
    assert out['ok'] is False and out['fallback'] is True and 'expired' in out['error']


def test_popup_grab_validates_the_body(popup, owner):  # noqa: F811
    for b in ({}, {**BODY, 'tmdb_id': 'x'}, {**BODY, 'season': -1}, {**BODY, 'download_url': ''}, {**BODY, 'episode': True}):
        assert _post(owner, b).status_code == 400


def test_popup_grab_adds_a_show_not_in_tv_yet_with_monitoring_off(popup, owner):  # noqa: F811
    out = _post(owner, {**BODY, 'tmdb_id': 6001, 'season': 1, 'episode': 1, 'title': 'Severance.Pay.S01E01.1080p.WEB-DL-Z'}).get_json()
    assert out['ok'] is True
    s = rows('SELECT id, monitor, monitored FROM series WHERE tmdb_id = 6001')[0]
    assert (s['monitor'], s['monitored']) == ('none', 0) and out['series_id'] == s['id']


def test_fetch_link_resolves_tokens_sources_links_and_magnets(monkeypatch):
    from app import tracker_sources
    seen = []
    monkeypatch.setattr(tracker_sources, 'fetch_release', lambda tok, automatic=False: seen.append(automatic) or ('torrent', b'd...'))
    assert tv_grab.fetch_link('/api/trackers/release/' + 'x' * 20)[0] == 'torrent'
    assert tv_grab.fetch_link('/api/trackers/release/' + 'x' * 20, automatic=True)[0] == 'torrent'
    assert seen == [False, True]                      # Reviewer: automatic grabs are charged as such
    m = 'magnet:?xt=urn:btih:' + 'a' * 40
    assert tv_grab.fetch_link(m) == ('magnet', m)
    tv_grab._link_resolvers.clear()
    tv_grab.register_link_resolver(lambda u: (True, None) if u.startswith('/src/') else (False, None))
    try:
        with pytest.raises(tracker_sources.GrabError, match='expired'):
            tv_grab.fetch_link('/src/abc')
        with pytest.raises(tracker_sources.GrabError):
            tv_grab.fetch_link('')
    finally:
        tv_grab._link_resolvers.clear()


# ------------------------------------------------------------------ PM 5: monitor labels after v6

def test_v9_labels_kept_v6_choices_from_their_episodes_without_changing_them(tv_env):  # noqa: F811
    import sqlite3
    from tests.test_tv_monitor_v158a import _mon, _v6_db
    _v6_db([
        "INSERT INTO series (id, tmdb_id, title, added_at, monitored) VALUES (1, 1, 'All on', 0, 1)",
        "INSERT INTO series (id, tmdb_id, title, added_at, monitored) VALUES (2, 2, 'Future only', 0, 1)",
        "INSERT INTO series (id, tmdb_id, title, added_at, monitored) VALUES (3, 3, 'Odd mix', 0, 1)",
        "INSERT INTO series (id, tmdb_id, title, added_at) VALUES (4, 4, 'Nothing', 0)",
        'INSERT INTO seasons (series_id, season_number) VALUES (1, 1), (2, 1), (2, 2), (3, 1), (3, 2), (4, 1)',
        "INSERT INTO episodes (series_id, season_number, episode_number, air_date, monitored) VALUES"
        " (1, 1, 1, '2001-01-01', 1), (1, 1, 2, '2099-01-01', 1),"
        " (2, 1, 1, '2001-01-01', 0), (2, 2, 1, '2099-01-01', 1), (2, 2, 2, NULL, 1),"
        " (3, 1, 1, '2001-01-01', 0), (3, 1, 2, '2001-02-01', 1), (3, 2, 1, '2002-01-01', 0), (3, 2, 2, '2099-01-01', 1),"
        " (4, 1, 1, '2001-01-01', 0)"])
    before = {sid: _mon(sid) for sid in (1, 2, 3)} if False else None
    assert tv_store.schema_version() == 11
    got = {r['id']: (r['monitored'], r['monitor']) for r in rows('SELECT id, monitored, monitor FROM series')}
    assert got[1] == (1, 'all') and got[2] == (1, 'future')
    assert got[3][0] == 1 and got[3][1] != 'none'                         # closest option, still not 'None'
    assert got[4] == (1, 'future')                                        # v7: no choice -> Future (unchanged rule)
    assert _mon(1) == {(1, 1): 1, (1, 2): 1}                              # episodes untouched
    assert _mon(2) == {(1, 1): 0, (2, 1): 1, (2, 2): 1}
    assert _mon(3) == {(1, 1): 0, (1, 2): 1, (2, 1): 0, (2, 2): 1}
    with tv_store.write() as conn:                                        # running it again changes nothing
        assert tv_store.monitor_labels(conn) == 0
    del before, sqlite3



def test_v9_keeps_none_when_the_owner_picked_episodes_by_hand(tv_env):  # noqa: F811
    """Tester (v165): None chosen, then one episode switched on by hand -> still 'none', not 'pilot'."""
    from tests.test_tv_monitor_v158a import _mon, _v6_db
    _v6_db([
        "INSERT INTO series (id, tmdb_id, title, added_at, monitored) VALUES (1, 1, 'Picked by hand', 0, 0)",
        "INSERT INTO series (id, tmdb_id, title, added_at, monitored) VALUES (2, 2, 'Odd one', 0, 0)",
        "INSERT INTO series (id, tmdb_id, title, added_at, monitored) VALUES (3, 3, 'Pilot, switch on', 0, 1)",
        'INSERT INTO seasons (series_id, season_number) VALUES (1, 1), (2, 1), (2, 2), (3, 1)',
        "INSERT INTO episodes (series_id, season_number, episode_number, air_date, monitored) VALUES"
        " (1, 1, 1, '2001-01-01', 1), (1, 1, 2, '2001-01-08', 0),"
        " (2, 1, 1, '2001-01-01', 0), (2, 2, 3, '2002-01-01', 1),"
        " (3, 1, 1, '2001-01-01', 1), (3, 1, 2, '2001-01-08', 0)"])
    assert tv_store.schema_version() == 11
    got = {r['id']: (r['monitored'], r['monitor']) for r in rows('SELECT id, monitored, monitor FROM series')}
    assert got[1] == (0, 'none')                                          # matches 'pilot' exactly, still None
    assert got[2] == (0, 'none')
    assert got[3] == (1, 'pilot')                                         # series switch on: a real Pilot choice
    assert _mon(1) == {(1, 1): 1, (1, 2): 0}                              # episodes untouched
    assert _mon(2) == {(1, 1): 0, (2, 3): 1}
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET monitor = 'none', monitored = 0 WHERE id = 3")
        conn.execute('UPDATE episodes SET monitored = 0 WHERE series_id = 3')
        conn.execute('UPDATE episodes SET monitored = 1 WHERE series_id = 3 AND episode_number = 1')
        assert tv_store.monitor_labels(conn) == 0                         # explicit None + one episode later
    assert rows('SELECT monitor FROM series WHERE id = 3')[0]['monitor'] == 'none'

# ------------------------------------------------------------------ PM 7: fail closed

@pytest.fixture
def bare_env(monkeypatch, tmp_path):
    for k in ('LUMA_INSTANCE', 'LUMA_TEST_QBIT_TAG', 'LUMA_TEST_TV_TAG', 'LUMA_TEST_ALLOW', 'LUMA_PORT', 'PORT', 'LUMA_BIND'):
        monkeypatch.delenv(k, raising=False)
    d = tmp_path / 'data'
    d.mkdir()
    monkeypatch.setenv('LUMA_DATA_DIR', str(d))
    return d


@pytest.mark.parametrize('sign', ['marker', 'port', 'bind', 'env', 'dir'])
def test_unset_instance_that_looks_like_test_is_treated_as_test(bare_env, monkeypatch, tmp_path, sign, caplog):
    assert instance.name() == 'prod' and instance.test_signs() == []
    if sign == 'marker':
        (bare_env / instance.MARKER).write_text('')
    elif sign == 'port':
        monkeypatch.setenv('LUMA_PORT', '5002')
    elif sign == 'bind':
        monkeypatch.setenv('LUMA_BIND', '0.0.0.0:5002')
    elif sign == 'env':
        monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'luma-test')
    else:
        d = tmp_path / 'luma-test-data'
        d.mkdir()
        monkeypatch.setenv('LUMA_DATA_DIR', str(d))
    assert instance.test_signs() and instance.assumed_test() and instance.is_test()
    for job in ('tv_grab', 'tv_delete_files', 'storage_cleanup', 'qbit', 'arr', 'downloads_guard'):
        assert not instance.writes_allowed(job)                      # grabs and deletes refused
    assert tv_grab.allowed() is False and tv_downloads.category() == 'luma-test-tv'
    monkeypatch.setattr(instance, '_logged', [False])
    import logging
    with caplog.at_level(logging.WARNING, logger='app.instance'):
        instance.log_blocked_once()
    assert any('LUMA_INSTANCE is not set but this looks like the TEST install' in r.getMessage() for r in caplog.records)
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')                       # v175 (PM): prod on a test-like host fails closed
    assert instance.is_test() and not instance.assumed_test() and instance.prod_conflict()
    assert not any(instance.writes_allowed(j) for j in instance.GATED)


@pytest.mark.skip(reason='v204a (PM, Taylor): Luma daily per-tracker cap removed; see test_v204a_no_daily_cap')
def test_v185_2a_feed_polls_are_exempt_from_every_cap_but_not_from_backoff(engine, guard):
    """PM / Tester BLOCK: a feed poll is the guard's 'feed' purpose: it consumes no cap, slot or bucket;
    a tracker's backoff / 429 Retry-After still stops it."""
    from app.tracker_guard import guard_key
    t = 2e9
    gk = guard_key(SITE)
    for i in range(8):                                                       # IPT's 8/day TV budget is spent
        take(guard, SITE, t + i * 7200)
    now = t + 8 * 7200
    assert tv_budget.take(SITE, now, guard=guard) is not None                # a search is still capped
    engine.now = now
    for k in range(4):                                                       # four 15-min feed polls after 8
        engine.calls.clear()
        items, st = tv_search.query_trackers('', 'feed', None, False, now + k * 900, [SITE])
        assert engine.calls == [(1, '', False, 'feed')] and st[0]['status'] == 'ok'
        assert guard.take(gk, False, True, now=now + k * 900, purpose='feed') is None
    guard.failure(gk, 'rate', retry_after=3600, status=429)                  # a 429 with Retry-After
    assert guard.take(gk, False, True, purpose='feed') is not None           # the feed backs off too


def test_v185_2a_feeds_never_starve_a_post_air_search(engine, guard):
    """(a) the feed due every 15 min plus an aired no-runtime episode: its search gets the first slot;
    (b) after 24 h of feeds the capped tracker still has its whole post-air budget."""
    from app.tracker_guard import guard_key
    t = 2e9
    gk = guard_key(SITE)
    for k in range(96):                                                      # 24 h of 15-min feed polls
        assert guard.take(gk, False, True, now=t + k * 900, purpose='feed') is None
    end = t + 96 * 900
    assert tv_budget.usage(SITE, end, guard=guard)['used'] == 0
    assert tv_budget.slot_free(SITE, end, guard=guard)
    engine.now = end                                                         # (a) the episode search goes now
    engine.calls.clear()
    tv_search.query_trackers('Show S01E03', 'episode', None, False, end, [SITE])
    assert engine.calls == [(1, 'Show S01E03', False, 'tv')]
    for i in range(1, 8):                                                    # (b) the rest of the day's budget
        assert tv_budget.take(SITE, end + i * 7200, guard=guard) is None


def test_v196c_past_the_daily_cap_ipt_is_skipped_and_logged(engine, monkeypatch):
    from app import tv_budget
    monkeypatch.setattr(tv_budget, 'under_daily', lambda cfg, now=None, guard=None: False)
    items, st = tv_search.query_trackers('Show S01E03', 'episode', None, False, 2e9)
    assert {s['id']: s['status'] for s in st} == {1: tv_search.SLOT_BUSY, 2: 'ok'}
    assert len(rows("SELECT * FROM search_log WHERE outcome = 'skipped'")) == 1
