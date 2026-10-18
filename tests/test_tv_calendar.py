"""TV Calendar and Search v1 (docs/design/tv.md): episodes by air date with
downloaded flags, air dates refreshed on open (24h cache), Search ordering, Add
(owner only, tv.db only, one of the selected TV libraries)."""
import datetime as dt
import json

import pytest

from app import file_management as fm, tv_calendar, tv_scan, tv_settings, tv_store, tv_tmdb
from tests import tv_fake
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.tv_tree import snapshot

D = dt.date


@pytest.fixture
def aired(monkeypatch):
    """Severance's episodes air a week apart from 2026-10-10 (Sat); Dark's one episode on 2026-10-15."""
    sev = dict(tv_fake.SHOWS[95396])
    sev['air'] = {(1, e): (D(2026, 10, 10) + dt.timedelta(days=7 * (e - 1))).isoformat() for e in range(1, 10)}
    sev['air'].update({(2, e): (D(2026, 10, 12) + dt.timedelta(days=e - 1)).isoformat() for e in range(1, 11)})
    monkeypatch.setitem(tv_fake.SHOWS, 95396, sev)
    monkeypatch.setitem(tv_fake.SHOWS, 70523, {**tv_fake.SHOWS[70523], 'air': {(1, 1): '2026-10-15'}})
    # a show only TMDB knows (for Search and Add), with Specials
    monkeypatch.setitem(tv_fake.SHOWS, 6001, {'name': 'Severance Pay', 'first_air_date': '2026-01-01',
                                             'seasons': {0: 1, 1: 3}, 'air': {(1, 1): '2026-10-14', (1, 2): '2026-10-21'}})


@pytest.fixture
def scanned(aired, lib):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    return lib


def _get(c, u, **q):
    return c.get(u, query_string=q, environ_base=REMOTE, headers=HDR)


def _post(c, u, body):
    return c.post(u, json=body, environ_base=REMOTE, headers=HDR)


# ------------------------------------------------------------------ calendar

def test_week_by_air_date_with_downloaded_flags(scanned):
    out = tv_calendar.calendar(D(2026, 10, 11), 7)                  # Sun Oct 11 .. Sat Oct 17
    eps = [(e['air_date'], e['show'], e['season'], e['episode'], e['downloaded']) for e in out['episodes']]
    assert eps == [
        ('2026-10-12', 'Severance', 2, 1, True),        # S02E01E02 is one linked file
        ('2026-10-13', 'Severance', 2, 2, True),
        ('2026-10-14', 'Severance', 2, 3, False),
        ('2026-10-15', 'Dark', 1, 1, True),
        ('2026-10-15', 'Severance', 2, 4, False),
        ('2026-10-16', 'Severance', 2, 5, True),        # S02E05 in the Season 01 folder: its own S/E wins
        ('2026-10-17', 'Severance', 1, 2, True),
        ('2026-10-17', 'Severance', 2, 6, False),
    ]
    e = out['episodes'][0]
    assert set(e) == {'episode_id', 'series_id', 'show', 'year', 'poster_path', 'season', 'episode', 'title',
                      'air_date', 'airstamp', 'runtime', 'tvdb_image', 'still_path', 'backdrop_path', 'downloaded'}
    assert e['poster_path'] == '/p95396.jpg' and e['title'] == 'Episode 1' and out['libraries'] == 1


def test_range_is_half_open_and_capped(scanned):
    assert [e['air_date'] for e in tv_calendar.calendar(D(2026, 10, 10), 1)['episodes']] == ['2026-10-10']
    assert tv_calendar.calendar(D(2026, 10, 9), 1)['episodes'] == []
    assert tv_calendar.calendar(D(2026, 1, 1), 999)['days'] == tv_calendar.MAX_DAYS


def test_only_selected_libraries(scanned):
    st = tv_settings.load()
    st['libraries'] = []
    tv_settings.save(st)
    out = tv_calendar.calendar(D(2026, 10, 11), 7)
    assert out['episodes'] == [] and out['libraries'] == 0


def test_calendar_route(owner, scanned):  # noqa: F811
    r = _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7')
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    assert len(r.get_json()['episodes']) == 8
    for bad in ({'start': '2026-13-01'}, {'start': 'x'}, {}, {'start': '2026-10-11', 'days': '0'},
                {'start': '2026-10-11', 'days': '63'}, {'start': '2026-10-11', 'days': '-1'}):
        assert _get(owner, '/api/settings/tv/calendar', **bad).status_code == 400


# ------------------------------------------------------------------ refresh on open

class _Now:
    """The Calendar-open refresh run inline."""
    def __init__(self, target, daemon=None, name=None):
        self.target = target

    def start(self):
        self.target()


def test_calendar_open_refreshes_stale_air_dates(owner, scanned, fake_tmdb, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_scan, '_RefreshThread', _Now)
    monkeypatch.setitem(tv_fake.SHOWS[70523]['air'], (1, 1), '2026-10-16')       # TMDB moved Dark's episode
    fresh = []
    real = tv_tmdb.fresh
    monkeypatch.setattr(tv_tmdb, 'fresh', lambda: (fresh.append(1), real())[1])
    r = _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7')
    assert r.status_code == 200 and fresh                                        # past the 24h cache, shared budget
    assert rows("SELECT e.air_date FROM episodes e JOIN series s ON s.id = e.series_id WHERE s.tmdb_id = 70523"
                " AND e.season_number = 1 AND e.episode_number = 1")[0]['air_date'] == '2026-10-16'
    n = len(fake_tmdb.calls)
    _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7')
    assert len(fake_tmdb.calls) == n                                             # fresh now: no TMDB call


def test_calendar_open_never_refreshes_during_an_import_and_files_stay(owner, scanned, fake_tmdb, monkeypatch, tv_env):  # noqa: F811
    monkeypatch.setattr(tv_scan, '_RefreshThread', _Now)
    before = snapshot(tv_env)
    n = len(fake_tmdb.calls)
    with tv_scan._scan_lock:
        assert tv_scan.refresh_on_open() is False
        assert _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7').status_code == 200
    assert len(fake_tmdb.calls) == n
    fake_tmdb.fail = "Couldn't reach TMDB"
    assert _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7').status_code == 200   # TMDB down: tv.db answers
    assert not tv_scan._refresh_lock.locked()
    assert snapshot(tv_env) == before                                           # reads only: no files touched


# ------------------------------------------------------------------ search

def test_search_library_first_then_tmdb_without_duplicates(scanned, owner):  # noqa: F811
    r = _get(owner, '/api/settings/tv/find', q='Severance')
    res = r.get_json()['results']
    assert [(x['title'], x['in_library']) for x in res] == [('Severance', True), ('Severance Pay', False)]
    assert res[0]['series_id'] and res[0]['seasons'] == 2
    assert res[1]['seasons'] == 1 and res[1]['series_id'] is None


def test_search_library_ranks_exact_then_prefix_then_contains(scanned):
    tv_calendar.add(6001, None, now=2000.0)
    res = tv_calendar.find('sever')['results']
    assert [x['title'] for x in res if x['in_library']] == ['Severance', 'Severance Pay']
    res = tv_calendar.find('severance pay')['results']
    assert [x['title'] for x in res] == ['Severance Pay']


def test_search_by_id_and_bad_queries(scanned, owner, fake_tmdb):  # noqa: F811
    res = _get(owner, '/api/settings/tv/find', q='tmdb:70523').get_json()['results']
    assert [(x['title'], x['in_library']) for x in res] == [('Dark', False)]      # ids go to TMDB
    assert _get(owner, '/api/settings/tv/find', q='a').status_code == 400
    assert _get(owner, '/api/settings/tv/find', q='x' * 201).status_code == 400
    fake_tmdb.fail = "Couldn't reach TMDB"
    assert _get(owner, '/api/settings/tv/find', q='Severance').status_code == 502


# ------------------------------------------------------------------ add

def test_add_with_one_library_puts_the_show_on_the_calendar_and_touches_no_files(scanned, owner, tv_env):  # noqa: F811
    before = snapshot(tv_env)
    r = _post(owner, '/api/settings/tv/shows', {'tmdb_id': 6001})
    assert r.status_code == 201
    sid = r.get_json()['series_id']
    s = rows('SELECT path_id, folder, monitored FROM series WHERE id = ?', sid)[0]
    assert (s['path_id'], s['folder'], s['monitored']) == (PID, None, 0)
    assert [(e['show'], e['air_date'], e['downloaded']) for e in tv_calendar.calendar(D(2026, 10, 11), 14)['episodes']
            if e['show'] == 'Severance Pay'] == [('Severance Pay', '2026-10-14', False), ('Severance Pay', '2026-10-21', False)]
    assert snapshot(tv_env) == before                                # no folders, no files
    assert json.loads(rows("SELECT detail FROM history WHERE series_id = ? AND event = 'added'", sid)[0]['detail'])['via'] == 'search'
    assert _post(owner, '/api/settings/tv/shows', {'tmdb_id': 6001}).status_code == 409


def test_add_needs_a_choice_with_two_libraries(scanned, owner, tv_env):  # noqa: F811
    select('p_0000cccc', tv_env / 'Anime', name='Anime')
    r = _post(owner, '/api/settings/tv/shows', {'tmdb_id': 6001})
    assert r.status_code == 400 and r.get_json()['error'] == 'Choose a library'
    r = _post(owner, '/api/settings/tv/shows', {'tmdb_id': 6001, 'path_id': 'p_0000cccc'})
    assert r.status_code == 201
    assert rows('SELECT path_id FROM series WHERE tmdb_id = 6001')[0]['path_id'] == 'p_0000cccc'


def test_add_validation(scanned, owner, tv_env):  # noqa: F811
    data = fm.load()
    data['paths'].append({'id': 'p_00f11f11', 'name': 'Movies', 'path': str(tv_env / 'Movies')})
    fm.save(data)
    for body in (None, {}, {'tmdb_id': 'x'}, {'tmdb_id': 0}, {'tmdb_id': True}, {'tmdb_id': 6001, 'path_id': 5},
                 {'tmdb_id': 6001, 'path_id': 'p' * 65}):
        assert _post(owner, '/api/settings/tv/shows', body).status_code == 400, body
    r = _post(owner, '/api/settings/tv/shows', {'tmdb_id': 6001, 'path_id': 'p_00f11f11'})   # a Movies path
    assert r.status_code == 400 and r.get_json()['error'] == 'Not a TV library'
    assert _post(owner, '/api/settings/tv/shows', {'tmdb_id': 424242}).status_code == 404
    r = _post(owner, '/api/settings/tv/shows', {'tmdb_id': 95396})              # in the library from its folder
    assert r.status_code == 409 and r.get_json()['series_id']
    assert not rows('SELECT 1 FROM series WHERE tmdb_id IN (6001, 424242)')


def test_add_with_no_libraries(owner, tv_env, fake_tmdb, aired):  # noqa: F811
    r = _post(owner, '/api/settings/tv/shows', {'tmdb_id': 6001})
    assert r.status_code == 409 and r.get_json()['error'] == 'No TV libraries'


def test_add_is_owner_only(minimal_app, lib, aired):  # noqa: F811
    _client(minimal_app)                                              # the owner
    other = _client(minimal_app, name='second')
    assert _post(other, '/api/settings/tv/shows', {'tmdb_id': 6001}).status_code == 403
    r = minimal_app.test_client().post('/api/settings/tv/shows', json={'tmdb_id': 6001}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 401
    for u in ('/api/settings/tv/calendar?start=2026-10-11', '/api/settings/tv/find?q=Dark'):
        assert other.get(u, environ_base=REMOTE, headers=HDR).status_code == 403
        assert minimal_app.test_client().get(u, environ_base=REMOTE, headers=HDR).status_code == 401
    assert not rows('SELECT 1 FROM series WHERE tmdb_id = 6001')


def test_added_show_survives_scans_and_a_folder_takes_it_later(scanned, tv_env):
    sid = tv_calendar.add(6001, None, now=2000.0)['series_id']
    tv_scan.scan_all(now=3000.0)
    assert rows('SELECT path_id, folder FROM series WHERE id = ?', sid)[0]['path_id'] == PID       # not "folder gone"
    folder = tv_env / 'TV' / 'Severance Pay [tmdbid-6001]'
    folder.mkdir()
    (folder / 'Severance.Pay.S01E01.mkv').write_bytes(b'x' * 64)
    tv_scan.scan_all(now=4000.0)
    s = rows('SELECT id, folder FROM series WHERE tmdb_id = 6001')
    assert s == [{'id': sid, 'folder': 'Severance Pay [tmdbid-6001]'}]                            # same row, history kept
    ep = [e for e in tv_calendar.calendar(D(2026, 10, 14), 1)['episodes'] if e['show'] == 'Severance Pay']
    assert ep[0]['downloaded'] is True


def test_add_brings_back_a_hidden_show_with_its_history(scanned):
    sid = rows("SELECT id FROM series WHERE folder = 'Dark [tmdbid-70523]'")[0]['id']
    with tv_store.write() as conn:
        tv_scan.detach(conn, sid, 1500.0, 'test')
    (scanned / 'Dark [tmdbid-70523]' / 'Dark.S01E01.mkv').unlink()
    (scanned / 'Dark [tmdbid-70523]' / 'link.S01E02.mkv').unlink()
    (scanned / 'Dark [tmdbid-70523]').rmdir()
    out = tv_calendar.add(70523, None, now=2000.0)
    assert out == {'ok': True, 'series_id': sid}
    assert [r['event'] for r in rows('SELECT event FROM history WHERE series_id = ? ORDER BY id', sid)][-2:] == ['detached', 'added']


def test_add_waits_for_a_running_import(scanned, monkeypatch):
    monkeypatch.setattr(tv_scan, 'LOCK_WAIT_S', 0.05)
    assert tv_scan._scan_lock.acquire(blocking=False)
    try:
        assert tv_calendar.add(6001, None)['status'] == 409
    finally:
        tv_scan._scan_lock.release()


# ------------------------------------------------------------------ refresh

def test_added_shows_refresh_daily_on_the_next_scan(scanned, fake_tmdb, monkeypatch):
    sid = tv_calendar.add(6001, None, now=10_000.0)['series_id']
    calls = len(fake_tmdb.calls)
    assert tv_scan.refresh_added(now=10_000.0 + tv_scan.REFRESH_EPISODES_S - 1) == 0
    assert len(fake_tmdb.calls) == calls                              # not due: no TMDB call
    monkeypatch.setitem(tv_fake.SHOWS[6001]['air'], (1, 3), '2026-10-28')
    fresh = []
    real = tv_tmdb.fresh
    monkeypatch.setattr(tv_tmdb, 'fresh', lambda: (fresh.append(1), real())[1])
    out = tv_scan.scan_all(now=10_000.0 + tv_scan.REFRESH_EPISODES_S)
    assert out['refreshed'] == 1 and fresh                            # past the cache, like folder shows
    assert rows('SELECT air_date FROM episodes WHERE series_id = ? AND season_number = 1 AND episode_number = 3', sid)[0]['air_date'] == '2026-10-28'
    assert rows('SELECT tmdb_refreshed_at FROM series WHERE id = ?', sid)[0]['tmdb_refreshed_at'] == 10_000.0 + tv_scan.REFRESH_EPISODES_S


def test_refresh_skips_shows_of_unselected_libraries(scanned):
    tv_calendar.add(6001, None, now=10_000.0)
    st = tv_settings.load()
    st['libraries'] = []
    tv_settings.save(st)
    assert tv_scan.refresh_added(now=10_000.0 + 10 * tv_scan.REFRESH_EPISODES_S) == 0


def test_refresh_survives_tmdb_down(scanned, fake_tmdb):
    tv_calendar.add(6001, None, now=10_000.0)
    fake_tmdb.fail = "Couldn't reach TMDB"
    assert tv_scan.refresh_added(now=10_000.0 + tv_scan.REFRESH_EPISODES_S) == 0
