"""v254 What you're missing on test: every prod hard-code is a setting (prod's values as defaults); the
settings shape the pool and the slate; ownership is Plex + in-flight grabs + the native watchlist (no Radarr);
dismiss and restore; the refresh cadence; the data-dir-only exemption. Trakt, TMDb, OMDb and Plex are fakes."""
import json
import os
import re
from datetime import date
from pathlib import Path

import pytest

from app import daily_missing as dm, instance
from app.automation import wym_store as ws

ROOT = Path(__file__).resolve().parents[1]
YEAR = date.today().year


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.headers = data, status, {}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._d


def film(tid, title, year=2010, votes=500, lang='en', poster=True, genres=(18,), adult=False):
    return {'id': tid, 'title': title, 'release_date': f'{year}-05-01', 'poster_path': f'/p{tid}.jpg' if poster else None,
            'original_language': lang, 'vote_count': votes, 'vote_average': 7.1, 'genre_ids': list(genres), 'adult': adult}


TMDB = [
    film(1, 'Heat', 1995, 9000), film(2, 'Owned By Id', 2000), film(3, 'Owned By Title', 2001),
    film(4, 'Grabbing', 2002), film(5, 'Watchlisted', 2003), film(6, 'Old', 1970), film(7, 'Future', YEAR + 1),
    film(8, 'Le Film', 2005, lang='fr'), film(9, 'No Poster', 2006, poster=False), film(10, 'Spooky', 2007, genres=(27,)),
    film(11, 'Adult', 2008, adult=True), film(12, 'Obscure', 2009, votes=20), film(13, 'Soft Tier', 2011, votes=60),
    film(14, 'Recent', YEAR), film(15, 'Comedy', 2012, genres=(35,)),
]
TRAKT = [{'watchers': 80, 'movie': {'title': 'Trakt Only', 'year': 2013, 'ids': {'tmdb': 20, 'imdb': 'tt20', 'trakt': 2},
                                    'votes': 0, 'genres': ['drama']}},
         {'watchers': 5, 'movie': {'title': 'Trakt Few', 'year': 2014, 'ids': {'tmdb': 21, 'trakt': 3}, 'votes': 10}}]
DETAIL = {20: {'poster_path': '/p20.jpg', 'original_language': 'en', 'vote_count': 10, 'genres': [{'id': 18}]},
          21: {'poster_path': '/p21.jpg', 'original_language': 'en', 'vote_count': 10}}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """Fake outside services; data files in tmp_path; builds run inline."""
    monkeypatch.setitem(ws._cfg, 'data_dir', str(tmp_path))
    monkeypatch.setattr(ws, '_changed', {'at': 0.0})
    monkeypatch.setattr(dm, '_state', {'building': False, 'last_attempt': 0.0, 'last_error': None})
    monkeypatch.setattr(dm, 'TRAKT_SLEEP_S', 0)
    monkeypatch.setattr(dm, 'TMDB_SLEEP_S', 0)
    dm._forget_memory()
    calls = []

    def http_get(url, params, headers=None, timeout=12):
        calls.append((url, dict(params or {})))
        if 'api.trakt.tv' in url:
            return Resp(TRAKT if params.get('page') == 1 and url.endswith('/movies/popular') else [])
        m = re.search(r'/3/movie/(\d+)$', url)
        if m:
            return Resp(DETAIL.get(int(m.group(1)), {}))
        if url.endswith('/movie/popular') and params.get('page') == 1:
            return Resp({'results': TMDB, 'total_pages': 1})
        return Resp({'results': [], 'total_pages': 1})

    plex = {'movies_by_tmdb': {'2': {'title': 'Owned By Id', 'year': 2000, 'tmdb_id': 2}},
            'movies_by_title': {'x': [{'title': 'Owned by title', 'year': 2001}]}}
    st = {'plex': plex, 'trakt': 'cid', 'tmdb': 'key', 'omdb': False, 'on': True}
    monkeypatch.setattr(dm, 'http_get', http_get)
    monkeypatch.setattr(dm, 'trakt_client_id', lambda: st['trakt'])
    monkeypatch.setattr(dm, 'tmdb_key', lambda: st['tmdb'])
    monkeypatch.setattr(dm, 'omdb_on', lambda: st['omdb'])
    monkeypatch.setattr(dm, 'omdb_ratings', lambda i: {'votes': '800', 'rating': 6.0} if i == 'tt20' else {'votes': '20,000', 'rating': 8})
    monkeypatch.setattr(dm, 'plex_index', lambda cached_only=False: st['plex'])
    monkeypatch.setattr(dm, 'in_flight', lambda: {4})
    monkeypatch.setattr(dm, 'watchlist_ids', lambda: {5})
    monkeypatch.setattr(dm, 'rail_on', lambda: st['on'])
    started = []
    monkeypatch.setitem(dm._start, 'fn', lambda target: started.append(target))
    st['calls'], st['started'] = calls, started
    yield st
    dm._forget_memory()


def ids(movies):
    return sorted(m['tmdb_id'] for m in movies)


# ---------------------------------------------------------------- settings

def test_defaults_are_prods_values_with_current_year():
    d = ws.defaults()
    assert d == {'movies_shown': 20, 'rebuild_hours': 8, 'no_repeat_days': 3, 'year_from': 1985, 'year_to': None,
                 'languages': ['en'], 'min_tmdb_votes': 150, 'min_imdb_votes': 1000, 'min_trakt_watchers': 50,
                 'trakt_lists': list(ws.TRAKT_LISTS), 'tmdb_lists': list(ws.TMDB_LISTS), 'exclude_genres': [],
                 'include_adult': False, 'require_poster': True}
    assert len(ws.TRAKT_LISTS) == 8 and len(ws.GENRES) == 14 and 'show' not in d   # Home screen owns show/hide
    assert ws.year_to(d) == YEAR and ws.year_to(d, date(2031, 1, 1)) == 2031


def test_save_validates_and_writes_0600(svc, tmp_path):
    ws.save({'movies_shown': 40, 'year_to': 2020, 'languages': ['fr', 'en'], 'exclude_genres': ['Horror']})
    s = ws.settings()
    assert s['movies_shown'] == 40 and s['year_to'] == 2020 and s['languages'] == ['en', 'fr']
    assert oct(os.stat(tmp_path / ws.SETTINGS_FILE).st_mode & 0o777) == '0o600'
    for bad in ({'movies_shown': 4}, {'movies_shown': 41}, {'rebuild_hours': 5}, {'no_repeat_days': 15},
                {'languages': []}, {'languages': ['xx']}, {'trakt_lists': ['nope']}, {'exclude_genres': ['War']},
                {'year_from': 2021}, {'year_to': 'soon'}, {'include_adult': 'yes'}, {'show': False}, {'radarr': 1}, None):
        with pytest.raises(ws.Invalid):
            ws.save(bad)
    ws.save({'year_to': None})
    assert ws.settings()['year_to'] is None


def test_a_bad_saved_value_falls_back_to_its_default(svc, tmp_path):
    (tmp_path / ws.SETTINGS_FILE).write_text(json.dumps({'movies_shown': 99, 'min_tmdb_votes': 300, 'show': False}))
    s = ws.settings()
    assert s['movies_shown'] == 20 and s['min_tmdb_votes'] == 300


# ---------------------------------------------------------------- the pool

def test_build_excludes_owned_grabbing_and_watchlisted_and_applies_filters(svc):
    pool = dm.build_pool(force=True)
    got = ids(pool['candidates'])
    # 2 Plex tmdb id, 3 Plex title/year, 4 in-flight grab, 5 native watchlist; 6/7 years, 8 language,
    # 9 no poster, 11 adult, 12 too few votes, 21 too few watchers
    assert got == [1, 10, 13, 14, 15, 20]
    assert pool['sig'] == dm.current_sig() and pool['built_at_iso']


def test_no_radarr_or_upgrade_db_in_ownership():
    code = (ROOT / 'app' / 'daily_missing.py').read_text().split('"""', 2)[2]          # past the module doc
    assert 'radarr' not in code.lower() and 'upgrade_library' not in code


def test_settings_reach_the_queries(svc):
    cfg = {**ws.defaults(), 'tmdb_lists': ['most_voted', 'by_genre'], 'languages': ['en', 'fr'], 'include_adult': True,
           'min_tmdb_votes': 400, 'year_from': 2000, 'year_to': 2003, 'exclude_genres': ['Horror'], 'trakt_lists': ['trending']}
    q = dm.tmdb_queries(cfg)
    assert {p for p, _, _ in q} == {'/discover/movie'}
    voted = q[0][2]
    assert voted['with_original_language'] == 'en|fr' and voted['include_adult'] == 'true' and voted['vote_count.gte'] == 400
    genres = [e['with_genres'] for _, _, e in q[1:]]
    assert len(genres) == 13 and '27' not in genres
    assert q[1][2]['primary_release_date.lte'] == '2003-12-31'
    assert dm.trakt_sources(cfg) == [('/movies/trending', 10)]
    yq = dm.tmdb_queries({**cfg, 'tmdb_lists': ['by_year']})
    assert [e['primary_release_date.gte'] for _, _, e in yq][::2] == ['2000-01-01', '2002-01-01']


def test_turning_lists_off_skips_those_sources(svc):
    ws.save({'trakt_lists': [], 'tmdb_lists': ['top_rated']})
    dm.build_pool(force=True)
    urls = {u for u, _ in svc['calls']}
    assert not any('trakt' in u for u in urls) and not any(u.endswith('/movie/popular') for u in urls)
    assert any(u.endswith('/movie/top_rated') for u in urls)


def test_filters_change_the_pool(svc):
    ws.save({'languages': ['en', 'fr'], 'include_adult': True, 'require_poster': False, 'year_from': 1960,
             'exclude_genres': ['Comedy'], 'min_tmdb_votes': 30, 'min_trakt_watchers': 0})
    got = ids(dm.build_pool(force=True)['candidates'])
    assert {6, 8, 9, 11, 12}.issubset(got) and 15 not in got, got


def test_crowd_gate_semantics():
    cfg = ws.defaults()
    ok = lambda **k: dm._crowd_ok(k, cfg)                       # noqa: E731
    assert ok(tmdb_vote_count=150) and ok(tmdb_vote_count=50) and not ok(tmdb_vote_count=49)
    assert ok(watchers=50) and ok(trakt_votes=500) and not ok(watchers=49, trakt_votes=499)
    assert not ok(imdb_votes=999, tmdb_vote_count=9000)          # IMDb hard floor (only known with OMDb)
    assert ok(imdb_votes='5,000')
    assert not dm._crowd_ok({'watchers': 1000}, {**cfg, 'min_trakt_watchers': 0})


def test_imdb_floor_needs_an_omdb_key(svc):
    svc['omdb'] = False
    assert 20 in ids(dm.build_pool(force=True)['candidates'])
    svc['omdb'] = True
    got = ids(dm.build_pool(force=True)['candidates'])
    assert 20 not in got and 1 in got                            # tt20 has 800 IMDb votes < 1,000


def test_pool_errors_keep_the_old_pool(svc):
    dm.build_pool(force=True)
    svc['trakt'] = svc['tmdb'] = ''
    with pytest.raises(dm.PoolError, match='No TMDb key or Trakt client ID'):
        dm.build_pool(force=True)
    svc['tmdb'], svc['plex'] = 'key', {}
    with pytest.raises(dm.PoolError, match="Can't reach Plex"):
        dm.build_pool(force=True)
    assert dm._read_pool()['candidates']


# ---------------------------------------------------------------- the slate

def test_slate_applies_settings_at_once_and_is_stable(svc):
    dm.build_pool(force=True)
    a = dm.get_rail()
    assert a['title'] == "What you're missing" and ids(a['movies']) == [1, 10, 13, 14, 15, 20]
    assert dm.get_rail()['movies'] == a['movies']                 # stable for the day
    ws.save({'exclude_genres': ['Horror'], 'movies_shown': 5})
    b = dm.get_rail()
    assert len(b['movies']) == 5 and 10 not in ids(b['movies'])
    assert all(m['owned'] is False and m['media_type'] == 'movie' for m in b['movies'])


def test_slate_rechecks_ownership(svc, monkeypatch):
    dm.build_pool(force=True)
    monkeypatch.setattr(dm, 'in_flight', lambda: {1, 4})
    assert 1 not in ids(dm.get_rail()['movies'])


def test_dont_repeat_prefers_unseen_titles(svc, monkeypatch):
    dm.build_pool(force=True)
    monkeypatch.setattr(dm, '_today_ny', lambda: '2026-10-22')
    ws.save({'movies_shown': 5})
    first = ids(dm.get_rail()['movies'])
    monkeypatch.setattr(dm, '_today_ny', lambda: '2026-10-23')
    dm._forget_memory()
    second = ids(dm.get_rail()['movies'])
    assert set(first) | set(second) == {1, 10, 13, 14, 15, 20} and len(set(first) & set(second)) == 4
    ws.save({'no_repeat_days': 0})
    assert len(ids(dm.get_rail()['movies'])) == 5


def test_dismiss_and_restore(svc):
    dm.build_pool(force=True)
    r = dm.dismiss(1)
    assert r['ok'] and r['dismissed_count'] == 1 and 1 not in ids(dm.get_rail()['movies'])
    d = dm.list_dismissed()
    assert d['items'][0]['tmdb_id'] == 1 and d['items'][0]['title'] == 'Heat' and d['items'][0]['year'] == 1995
    assert dm.dismiss(1)['already'] is True
    assert dm.restore(1)['restored'] is True and 1 in ids(dm.get_rail()['movies'])
    assert dm.list_dismissed()['count'] == 0
    with pytest.raises(ValueError):
        dm.dismiss(0)


def test_hidden_rail_serves_nothing_and_does_not_build(svc):
    svc['on'] = False
    r = dm.get_rail()
    assert r['enabled'] is False and r['movies'] == [] and svc['started'] == []
    assert dm.next_rebuild() is None and dm.status()['on_home'] is False


def test_no_pool_kicks_a_background_build_never_inline(svc):
    r = dm.get_rail()
    assert r['empty'] and r['movies'] == [] and len(svc['started']) == 1 and not svc['calls']
    svc['started'][0]()
    assert ids(dm.get_rail()['movies']) == [1, 10, 13, 14, 15, 20]


# ---------------------------------------------------------------- refresh cadence

def test_rebuild_every_hours(svc):
    now = 1_800_000_000.0
    assert dm.next_rebuild(now=now) == now                         # no pool: now
    pool = dm.build_pool(force=True)
    built = pool['built_at']
    assert dm.next_rebuild(now=built) == built + 8 * 3600
    assert not dm.due(now=built + 8 * 3600 - 1) and dm.due(now=built + 8 * 3600)
    ws.save({'rebuild_hours': 2})
    assert dm.next_rebuild(now=built) == built + 2 * 3600


def test_a_pool_change_rebuilds_after_a_pause(svc, monkeypatch):
    dm.build_pool(force=True)
    monkeypatch.setattr(ws.time, 'time', lambda: 1_900_000_000.0)
    ws.save({'min_tmdb_votes': 500})
    assert dm.next_rebuild(now=1_900_000_001.0) == 1_900_000_000.0 + dm.SETTLE_S
    assert dm.tick(now=1_900_000_001.0) is None
    assert dm.tick(now=1_900_000_000.0 + dm.SETTLE_S)['sig'] == dm.current_sig()


def test_display_only_changes_do_not_rebuild(svc):
    pool = dm.build_pool(force=True)
    ws.save({'movies_shown': 10, 'no_repeat_days': 5})
    assert dm.next_rebuild(now=pool['built_at']) == pool['built_at'] + 8 * 3600


def test_a_failed_rebuild_backs_off_until_an_edit(svc, monkeypatch):
    svc['plex'] = {}
    assert dm.rebuild() is None and dm.status()['error'] == "Can't reach Plex"
    t = dm._state['last_attempt']
    assert dm.next_rebuild(now=t + 1) == t + dm.RETRY_S
    monkeypatch.setattr(ws.time, 'time', lambda: t + 5)
    ws.save({'languages': ['en', 'fr']})
    assert dm.next_rebuild(now=t + 6) == t + 5 + dm.SETTLE_S


def test_status_in_et(svc):
    dm.build_pool(force=True)
    s = dm.status()
    assert s['pool_size'] == 6 and s['building'] is False and s['error'] is None
    assert s['last_rebuilt'].endswith(('-04:00', '-05:00')) and s['next_rebuild'].endswith(('-04:00', '-05:00'))


def test_refresh_now_is_single_flight(svc):
    assert dm.rebuild_async() is True and len(svc['started']) == 1
    dm._build_lock.acquire()
    try:
        assert dm.rebuild_async() is False and dm.rebuild() is None
    finally:
        dm._build_lock.release()


# ---------------------------------------------------------------- the exemption

def test_daily_missing_is_data_dir_only_not_gated():
    assert 'daily_missing' not in instance.GATED and instance.data_dir_only('daily_missing')
    assert all(job != 'daily_missing' for _, job in instance.ENDPOINTS)


def test_the_refresher_starts_on_test(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.delenv('LUMA_TEST_ALLOW', raising=False)
    monkeypatch.setattr(dm, '_thread_started', False)
    started = []
    monkeypatch.setattr(dm.threading.Thread, 'start', lambda self: started.append(self.name))
    assert dm.start_daily_missing_thread() is True and started == ['daily-missing-pool']
    assert dm.start_daily_missing_thread() is False


def test_the_module_never_writes_outside_its_json():
    src = (ROOT / 'app' / 'daily_missing.py').read_text()
    for bad in ('requests.post', 'requests.put', 'requests.delete', '.unlink(', 'shutil', 'qbit', 'add_movie',
                'grab(', 'sqlite3', 'os.remove', 'rmtree'):
        assert bad not in src, bad
    writes = re.findall(r'(\w+)\.write_text\(', src)
    assert writes and set(writes) == {'tmp'}
    assert re.findall(r"_data_dir\(\) / '([^']+)'", src) == ['what_youre_missing_pool.json',
                                                            'what_youre_missing_dismissed.json', 'what_youre_missing_served.json']
