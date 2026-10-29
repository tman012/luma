"""v203 the movie scanner and grab (test only): 15-min feeds (guard 'feed', no caps), searches once released
(digital/physical date or a feed match), 15 min for 6 h then hourly, TV's budget (daily cap, skip until reset,
skip logged once), the best release by profile/size/tracker priority/preferred groups/blocklist, grabs through
TV's client path with the movie category, recorded in movies.db movie_grabs (never twice); never on prod."""
from datetime import datetime
import sqlite3

import pytest

from app import instance, library_mover as lm, movie_scanner as sc, movie_settings, movie_watchlist, tv_budget, tv_grab
from app import tv_search
from tests.test_library_mover import Settings
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_v158c import FakeQbit, SITE, GEN, guard  # noqa: F401
from tests.test_tv_search_v158b import T  # noqa: F401  (a .torrent payload)

NOW = 2e9
MOVIE = {'tmdb_id': 693134, 'title': 'Dune: Part Two', 'year': 2024}
GB = 1024 ** 3


class Eng:
    def __init__(self):
        self.calls, self.now, self.results = [], NOW, {}

        class Tok:
            def flush(self):
                pass
        self.tokens = Tok()

    def search_one(self, cfg, query, cats, limit, interactive, purpose=None, **k):
        self.calls.append((cfg['id'], query, interactive, purpose, tuple(cats)))
        if purpose == 'feed':
            from app.tracker_guard import guard_key
            assert tv_budget._guard().take(guard_key(cfg), False, True, now=self.now, purpose='feed') is None
        elif purpose:
            assert tv_budget.take(cfg, self.now) is None
        return list(self.results.get(cfg['id'], []))

    def to_item(self, cfg, r, sv):
        return {**r, 'downloadUrl': r.get('downloadUrl', '/x'), 'indexerId': cfg['id'], 'indexer': cfg['name']}

    class store:
        @staticmethod
        def enabled_configs():
            return [SITE, GEN]


def rel(title, gb=10.0, seeders=20, **k):
    return {'title': title, 'size': int(gb * GB), 'seeders': seeders, **k}


@pytest.fixture
def mv(tmp_path, monkeypatch, guard, tv_env):  # noqa: F811
    from app import tracker_search
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'movie_grab')
    monkeypatch.delenv('LUMA_MOVIE_CATEGORY', raising=False)
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(tmp_path / 'no-seed.json'))
    monkeypatch.setattr(movie_settings, '_tracker_records', lambda: [{'id': 1, 'name': 'IPT', 'type': 'iptorrents'},
                                                                      {'id': 2, 'name': 'Indexer', 'type': 'torznab'}])
    e = Eng()
    monkeypatch.setattr(tracker_search, 'get_search', lambda: e)
    monkeypatch.setattr(tracker_search, '_match_categories', lambda cfg, cats: cats)
    monkeypatch.setattr(tracker_search, '_secret_values', lambda cfg: set())
    lm.init(settings=Settings())
    tv_grab._have_category.clear()
    q = FakeQbit()
    real = sc.grab
    monkeypatch.setattr(sc, 'grab', lambda item, tid, origin, now=None, **k: real(
        item, tid, origin, now, session_factory=q.factory, fetch=lambda u: ('torrent', T + item['title'].encode()[:0]
                                                                            if False else _torrent(item['title']))))
    movie_settings.apply_put({'trackers': [{'id': 1, 'enabled': True}, {'id': 2, 'enabled': True}]})
    movie_watchlist.add(dict(MOVIE), now=NOW - 100)
    e.q = q
    return e


def _torrent(name: str) -> bytes:
    n = name.encode()
    return b'd4:infod4:name' + str(len(n)).encode() + b':' + n + b'6:lengthi1eee'


def _fill():                                                       # IPT's daily cap used up
    start = NOW - tv_budget.AUTO_PER_TRACKER_DAILY * tv_budget.SLOT_S + 3600
    for i in range(tv_budget.AUTO_PER_TRACKER_DAILY):
        assert tv_budget.take(SITE, start + i * tv_budget.SLOT_S) is None


def _released(tid=693134, at=NOW - 60):
    with movie_settings.connect() as conn:
        sc.ensure(conn)
        # v256: available by its digital date (Settings > Movies availability, released by default)
        day = datetime.fromtimestamp(at, sc.TZ).strftime('%Y-%m-%d')
        sc._set(conn, tid, released_at=at, runtime=166, digital_date=day)


def _scan(tid=693134):
    with movie_settings.connect() as conn:
        sc.ensure(conn)
        r = conn.execute('SELECT * FROM movie_scan WHERE tmdb_id = ?', (tid,)).fetchone()
        return dict(r) if r else {}


def _grabs():
    with movie_settings.connect() as conn:
        sc.ensure(conn)
        return [dict(r) for r in conn.execute('SELECT * FROM movie_grabs')]


def test_off_on_prod_and_on_by_default_on_test(mv, monkeypatch):
    assert movie_settings.view()['monitoring'] is True and sc.enabled()
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    assert not sc.enabled() and sc.tick(NOW) == {'skipped': 'off'}
    assert sc.grab({'title': 'Dune.Part.Two.2024.1080p.WEB-DL-NTb'}, 693134, 'auto', NOW)['blocked']
    assert sc.start() is False
    assert mv.calls == []


def test_dates_from_tmdb_release_dates_mark_it_released():
    d = sc.dates_of({'runtime': 166, 'release_dates': {'results': [
        {'iso_3166_1': 'GB', 'release_dates': [{'type': 4, 'release_date': '2024-03-01T00:00:00.000Z'}]},
        {'iso_3166_1': 'US', 'release_dates': [{'type': 3, 'release_date': '2024-03-01T00:00:00.000Z'},
                                               {'type': 4, 'release_date': '2024-04-16T00:00:00.000Z'},
                                               {'type': 5, 'release_date': '2024-05-14T00:00:00.000Z'}]}]}})
    assert d == {'runtime': 166, 'digital_date': '2024-04-16', 'physical_date': '2024-05-14', 'cinema_date': '2024-03-01'}
    assert sc.released_at(d) == sc._day_start('2024-04-16')


def test_not_released_is_not_searched_released_is_searched_then_15_min_then_hourly(mv):
    assert sc.schedule(NOW) == 0                                   # no date yet
    _released(at=NOW - 60)
    assert sc.schedule(NOW) == 1
    out = sc.run_due(NOW)
    assert out['movies'] == 1 and _grabs() == []                 # nothing matched
    assert [c[:4] for c in mv.calls] == [(1, 'Dune: Part Two 2024', False, 'tv'), (2, 'Dune: Part Two 2024', False, None)]
    assert mv.calls[0][4][0] == 2000                              # movie categories
    assert _scan()['next_search_at'] == NOW + sc.FAST_S
    _released(at=NOW - 7 * 3600)
    mv.now = NOW + sc.FAST_S
    sc.run_due(NOW + sc.FAST_S)
    assert _scan()['next_search_at'] == NOW + sc.FAST_S + sc.SLOW_S


def test_picks_by_profile_size_priority_and_groups_and_grabs_once(mv):
    _released()
    movie_settings.apply_put({'preferred_groups': ['FLUX']})
    mv.results = {
        1: [rel('Dune.Part.Two.2024.2160p.WEB-DL-NTb', 20),          # not in the default profile? (Any: allowed)
            rel('Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1-NTb', 10),
            rel('Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1-FLUX', 10),
            rel('Dune.Part.Two.2024.1080p.WEB-DL-TINY', 0.1),
            rel('Dune.2021.1080p.WEB-DL-NTb', 10)],
        2: [rel('Dune.Part.Two.2024.1080p.WEB-DL-FLUX', 10, seeders=500)]}
    with movie_settings.connect() as conn:                        # a 1080p WEB profile, 20-100 MB/min
        conn.execute("UPDATE profiles SET items = ?, min_mb_per_min = 20, max_mb_per_min = 100",
                     ('[{"id": "1080p-web", "allowed": true}]',))
        conn.execute("INSERT INTO movie_blocklist (tmdb_id, release_title, added_at) VALUES (693134, ?, 0)",
                     ('Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1-FLUX',))
    sc.schedule(NOW)
    sc.run_due(NOW)
    g = _grabs()
    # the 2160p is outside the profile, TINY under the size floor, Dune (2021) not this movie, the IPT FLUX
    # blocklisted; tracker priority (IPT first) beats the indexer's FLUX with more seeders
    assert [x['release_title'] for x in g] == ['Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1-NTb']
    assert g[0]['tmdb_id'] == 693134 and g[0]['state'] == 'grabbed' and len(g[0]['hash']) == 40
    assert mv.q.torrents[0]['category'] == mv.q.torrents[0]['tags'] == 'luma-test'   # v324b: the test tag, not 'movie'
    assert _scan()['next_search_at'] is None
    mv.calls.clear()
    assert sc.run_due(NOW + 3600)['movies'] == 0 and sc.feed(NOW + 3600)['grabbed'] == 0     # never twice
    assert len(mv.q.torrents) == 1


def test_preferred_groups_empty_leaves_ranking_unchanged(mv):
    c = [rel('Dune.Part.Two.2024.1080p.WEB-DL-AAA', 10, seeders=5), rel('Dune.Part.Two.2024.1080p.WEB-DL-BBB', 10, seeders=50)]
    prof = movie_settings.profiles()[0]
    m = {**MOVIE, 'runtime': 166}
    assert [x['item']['title'][-3:] for x in sc.candidates([{**i, 'indexerId': 1} for i in c], m, prof, set(), [1])] == ['BBB', 'AAA']
    movie_settings.apply_put({'preferred_groups': ['AAA']})
    assert [x['item']['title'][-3:] for x in sc.candidates([{**i, 'indexerId': 1} for i in c], m, prof, set(), [1])] == ['AAA', 'BBB']


@pytest.mark.skip(reason='v204a (PM, Taylor): Luma daily per-tracker cap removed; see test_v204a_no_daily_cap')
def test_feed_every_15_min_exempt_from_caps_and_a_match_releases_and_grabs(mv):
    _fill()
    mv.results = {1: [rel('Dune.Part.Two.2024.1080p.WEB-DL-NTb', 10)]}
    out = sc.feed(NOW)
    assert out == {'trackers': 2, 'grabbed': 1}
    assert [c[3] for c in mv.calls] == ['feed', 'feed']
    assert _scan()['released_at'] == NOW
    mv.calls.clear()
    assert sc.feed(NOW + 600)['trackers'] == 0
    mv.now = NOW + sc.FEED_S
    assert sc.feed(NOW + sc.FEED_S)['trackers'] == 2


@pytest.mark.skip(reason='v204a (PM, Taylor): Luma daily per-tracker cap removed; see test_v204a_no_daily_cap')
def test_capped_tracker_skipped_until_reset_logged_once_and_waits_for_the_reset(mv, monkeypatch):
    monkeypatch.setattr(movie_settings, '_tracker_records', lambda: [{'id': 1, 'name': 'IPT', 'type': 'iptorrents'}])
    movie_settings.apply_put({'trackers': [{'id': 1, 'enabled': True}]})
    type(mv).store.enabled_configs = staticmethod(lambda: [SITE])
    _fill()
    _released()
    sc.schedule(NOW)
    sc.run_due(NOW)
    assert mv.calls == []                                          # IPT not asked
    reset = tv_budget.daily_reset(SITE, NOW)
    s = _scan()
    assert s['next_search_at'] == reset and s['last_search_at'] == NOW
    sc.schedule(NOW + 60)                                          # not pulled back
    assert _scan()['next_search_at'] == reset
    from app import tv_store
    with tv_store.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM search_log WHERE outcome = 'skipped' AND section = 'movies'").fetchone()[0]
    assert n == 1
    type(mv).store.enabled_configs = staticmethod(lambda: [SITE, GEN])


def test_manual_search_and_interactive_routes(mv, owner):  # noqa: F811
    from tests.test_v182_series_detail import _req
    mv.results = {1: [rel('Dune.Part.Two.2024.1080p.WEB-DL-NTb', 10), rel('Dune.2021.1080p.WEB-DL-X', 10)]}
    r = _req(owner, 'post', '/api/movies/watchlist/693134/interactive', {})
    assert r.status_code == 200
    d = r.get_json()
    assert [x['grabbable'] for x in d['releases']] == [True, False]
    assert d['releases'][1]['rejections'][0] == sc.NOT_THIS and mv.calls[0][2] is True     # interactive budget
    r = _req(owner, 'post', '/api/movies/watchlist/693134/interactive/grab',
             {'download_url': '/x', 'title': 'Dune.2021.1080p.WEB-DL-X', 'size': 1})
    assert r.status_code == 400
    r = _req(owner, 'post', '/api/movies/watchlist/693134/search', {})
    d = r.get_json()
    assert r.status_code == 200 and d['picks'][0]['grabbed'] and d['picks'][0]['quality'] == '1080p-web'
    assert _req(owner, 'post', '/api/movies/watchlist/1/search', {}).status_code == 404
