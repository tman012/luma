"""BHD Sources missed WEB-DL/WEBRip rows for some titles: only page 1 of the
BHD API (100 rows, newest first) was read, the WEB uploads of a title with many
Remux/Encode uploads were on page 2, and a text query with punctuation or a
year could miss names; Sources' TMDB id was never used."""

import json
import logging

import pytest

from app import tracker_clients as tc
from app.tracker_guard import TrackerGuard
from app.tracker_http import Response
from app.tracker_search import LumaTrackersClient, ReleaseTokens, TrackerSearch
from app.tracker_store import TrackerStore
from tests.fixtures import bhd_pages

KEY = 'apikey0123456789abcdef0123456789'


def R(data, status=200):
    return Response(status, {}, json.dumps(data).encode())


class Mock:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []

    def __call__(self, method, url, **kw):
        self.calls.append({'method': method, 'url': url, **kw})
        return self.resps.pop(0) if len(self.resps) > 1 else self.resps[0]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(tc, '_sleep', lambda s: None)


def cfg():
    return {'id': 2, 'type': 'beyondhd', 'name': 'BHD', 'url': 'https://beyond-hd.me', 'api_key': KEY,
            'rss_key': bhd_pages.RK, 'categories': [2000, 5000], 'priority': 25, 'enabled': True}


def test_web_rows_on_page_two_are_returned():
    m = Mock(R(bhd_pages.page1()), R(bhd_pages.page2()))
    out = tc.search(cfg(), 'Dune Part Two 2024', [2000, 5000], 500, m)
    names = [r['title'] for r in out]
    assert len(out) == 104 and len(m.calls) == 2 and m.calls[1]['json']['page'] == 2
    assert sum('WEB-DL' in n for n in names) == 3 and sum('WEBRip' in n for n in names) == 1
    st = tc.last_stats()
    assert st['rows'] == 104 and st['kept'] == 104 and st['pages'] == 2
    assert st['kinds'] == {'Remux': 50, 'Encode': 50, 'WEB-DL': 3, 'WEBRip': 1}


def test_pages_stop_at_the_cap_and_at_the_last_page():
    one = dict(bhd_pages.page2(), page=1, total_pages=1)
    m = Mock(R(one))
    assert len(tc.search(cfg(), 'x', [2000], 500, m)) == 4 and len(m.calls) == 1
    many = dict(bhd_pages.page1(), total_pages=9)
    m = Mock(R(many))
    tc.search(cfg(), 'x', [2000], 500, m)
    assert len(m.calls) == tc.BHD_MAX_PAGES


@pytest.mark.parametrize('title,sent', [
    ("Dune: Part Two 2024", 'Dune Part Two 2024'),
    ("Ocean's Eleven 2001", 'Oceans Eleven 2001'),
    ("Mission: Impossible – Dead Reckoning Part One 2023", 'Mission Impossible Dead Reckoning Part One 2023'),
    ("Fast & Furious 2009", 'Fast & Furious 2009'),
])
def test_text_query_drops_punctuation_bhd_names_leave_out(title, sent):
    m = Mock(R({'status_code': 1, 'page': 1, 'results': [], 'total_pages': 1, 'total_results': 0,
                'success': True}))
    tc.search(cfg(), title, [2000, 5000], 500, m)
    assert m.calls[0]['json']['search'] == title          # first exactly as typed (as ee3bee4)
    assert any(c['json'].get('search') == sent for c in m.calls)


@pytest.fixture
def engine(tmp_path):
    store = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'k.key')
    rec, err = store.create({'type': 'beyondhd', 'name': 'BHD', 'url': 'https://beyond-hd.me',
                             'api_key': KEY, 'rss_key': bhd_pages.RK, 'categories': [2000, 5000],
                             'priority': 25})
    assert not err
    guard = TrackerGuard(tmp_path / 'guard.json')
    guard.wait_spacing = lambda gk: None
    mock = Mock(R(bhd_pages.page1()), R(bhd_pages.page2()))
    eng = TrackerSearch(store=store, guard=guard, request=mock,
                        tokens=ReleaseTokens(store, tmp_path / 'rel.json'))
    return eng, mock


def test_sources_gets_web_rows_and_logs_types(engine, caplog):
    eng, mock = engine
    caplog.set_level(logging.INFO)
    out = LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134)
    assert len(out) == 104
    assert {o['source'] for o in out} >= {'WEB-DL', 'WEBRip', 'REMUX', 'BluRay'}
    assert mock.calls[0]['json'] == {'action': 'search', 'rsskey': bhd_pages.RK, 'search': 'Dune: Part Two 2024'}
    line = next(r.getMessage() for r in caplog.records if 'rows parsed' in r.getMessage())
    assert '104 rows parsed, 104 results, 0 dropped, pages 2, by id no' in line
    assert 'WEB-DL 3' in line and 'WEBRip 1' in line and 'Remux 50' in line
    assert KEY not in caplog.text and bhd_pages.RK not in caplog.text


def test_rows_without_download_link_count_as_dropped(engine, caplog):
    eng, mock = engine
    p = dict(bhd_pages.page2(), page=1, total_pages=1)
    p['results'][0] = dict(p['results'][0], download_url='')
    mock.resps = [R(p)]
    caplog.set_level(logging.INFO)
    out = LumaTrackersClient(eng).search_all('Dune Part Two', 'movie', 2024)
    assert len(out) == 3
    assert '4 rows parsed, 3 results, 1 dropped' in caplog.text



EMPTY = {'status_code': 1, 'page': 1, 'results': [], 'total_pages': 1, 'total_results': 0, 'success': True}
WEB_ONLY = dict(bhd_pages.page2(), page=1, total_pages=1)
FAIL = {'status_code': 0, 'success': False, 'status_message': 'Something went wrong'}


def movie(m, title='Dune: Part Two 2024'):
    return tc.search(cfg(), title, [2000, 5000], 500, m, tmdb_id='693134', media_type='movie')


def test_text_first_exactly_like_ee3bee4_and_no_more_when_it_has_rows():
    m = Mock(R(WEB_ONLY))
    assert len(movie(m)) == 4 and len(m.calls) == 1
    assert m.calls[0]['json'] == {'action': 'search', 'rsskey': bhd_pages.RK, 'search': 'Dune: Part Two 2024'}
    assert tc.last_stats()['fallback'] == '' and not tc.last_stats()['by_id']


def test_tmdb_id_is_sent_as_movie_slash_id():
    m = Mock(R(EMPTY), R(EMPTY), R(EMPTY), R(WEB_ONLY))
    assert len(movie(m)) == 4
    assert [c['json'].get('search') for c in m.calls] == ['Dune: Part Two 2024', 'Dune Part Two 2024',
                                                         'Dune Part Two', None]
    idb = m.calls[3]['json']
    assert idb['tmdb_id'] == 'movie/693134' and 'categories' not in idb
    assert tc.last_stats()['by_id'] and tc.last_stats()['fallback'] == 'id'


def test_tv_id_is_tv_slash_id_with_the_episode_text():
    m = Mock(R(EMPTY))
    tc.search(cfg(), "Grey's Anatomy S01E05", [5000], 500, m, tmdb_id='1416', media_type='tv')
    assert [c['json'].get('search') for c in m.calls] == ["Grey's Anatomy S01E05", 'Greys Anatomy S01E05', 'S01E05']
    assert m.calls[2]['json']['tmdb_id'] == 'tv/1416'


@pytest.mark.parametrize('bad', [R(FAIL), R({'x': 1}, 500), Response(200, {}, b'<html>oops'), 'timeout'])
def test_an_attempt_that_errors_falls_back(bad):
    from app.tracker_http import TrackerError

    class M(Mock):
        def __call__(self, method, url, **kw):
            self.calls.append({'method': method, 'url': url, **kw})
            r = self.resps.pop(0)
            if r == 'timeout':
                raise TrackerError('timeout')
            return r
    m = M(bad, R(WEB_ONLY))
    assert len(movie(m)) == 4 and len(m.calls) == 2
    st = tc.last_stats()
    assert st['steps'][0]['error'] in ('response', 'http', 'timeout') and st['steps'][1]['rows'] == 4


def test_every_attempt_erroring_is_a_tracker_error_not_zero():
    from app.tracker_http import TrackerError
    m = Mock(R(FAIL))
    with pytest.raises(TrackerError) as e:
        movie(m)
    assert e.value.kind == 'response' and len(m.calls) == 4


def test_rate_limit_or_key_rejection_stops_the_chain():
    from app.tracker_http import TrackerError
    for resp, kind in ((R({}, 429), 'rate'), (R({'status_code': 0, 'success': False,
                                                 'status_message': 'Invalid API key'}), 'auth')):
        m = Mock(resp)
        with pytest.raises(TrackerError) as e:
            movie(m)
        assert e.value.kind == kind and len(m.calls) == 1


def test_chain_logs_each_step(engine, caplog):
    eng, mock = engine
    mock.resps = [R(FAIL), R(EMPTY), R(EMPTY), R(WEB_ONLY)]
    caplog.set_level(logging.INFO)
    out = LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134)
    assert len(out) == 4 and len(mock.calls) == 4
    assert len(eng.guard.log.get('site:beyondhd', [])) == 4      # caps count HTTP requests
    msgs = [r.getMessage() for r in caplog.records]
    assert 'Tracker BHD (id 1) search step 1 text: HTTP 200, success False, status_code 0, error response' in msgs
    assert 'Tracker BHD (id 1) search step 2 text stripped: HTTP 200, success True, status_code 1, 0 rows' in msgs
    assert 'Tracker BHD (id 1) search step 4 id: HTTP 200, success True, status_code 1, 4 rows' in msgs
    assert any('by id yes, fallback id' in x for x in msgs)
    assert KEY not in caplog.text and bhd_pages.RK not in caplog.text and 'Dune' not in caplog.text
    assert '693134' not in caplog.text


def test_all_errors_logged_and_counted_as_failure(engine, caplog):
    eng, mock = engine
    mock.resps = [R(FAIL)]
    caplog.set_level(logging.INFO)
    assert LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134) == []
    assert any(r.levelno == logging.WARNING and 'search failed: response (HTTP 200' in r.getMessage()
               for r in caplog.records)
    assert eng.store.get(1)['status']['state'] == 'error'


def test_chain_stops_starting_attempts_after_its_time_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(tc, '_monotonic', lambda: clock[0])

    class Slow(Mock):
        def __call__(self, method, url, **kw):
            clock[0] += 30.0                      # every request takes 30 s
            return super().__call__(method, url, **kw)
    m = Slow(R(EMPTY))
    assert movie(m) == [] and len(m.calls) == 2
    # Each request only gets what is left of the 45 s chain budget.
    assert [c['deadline_s'] for c in m.calls] == [45.0, 15.0]
    assert tc.last_stats()['stopped'] == 'budget'


# ------------------------------------------------------------------ budget per HTTP request

def test_every_page_and_fallback_is_charged(engine):
    eng, mock = engine
    mock.resps = [R(EMPTY), R(EMPTY), R(EMPTY), R(WEB_ONLY)]
    LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134)
    assert len(mock.calls) == 4 and len(eng.guard.log['site:beyondhd']) == 4


def test_pages_are_charged_too(engine):
    eng, mock = engine
    mock.resps = [R(bhd_pages.page1()), R(bhd_pages.page2())]
    assert len(LumaTrackersClient(eng).search_all('Dune Part Two', 'movie', 2024)) == 104
    assert len(eng.guard.log['site:beyondhd']) == 2


def test_chain_stops_when_the_budget_runs_out(engine, monkeypatch):
    from app import tracker_guard
    eng, mock = engine
    monkeypatch.setitem(tracker_guard.LIMITS, 'interactive_per_hour', 2)
    mock.resps = [R(EMPTY)]
    assert LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134) == []
    assert len(mock.calls) == 2 and len(eng.guard.log['site:beyondhd']) == 2
    assert eng.guard.blocked('site:beyondhd') is None          # running out is not a backoff


def test_budget_out_keeps_rows_already_found(engine, monkeypatch):
    from app import tracker_guard
    eng, mock = engine
    monkeypatch.setitem(tracker_guard.LIMITS, 'interactive_per_hour', 1)
    mock.resps = [R(bhd_pages.page1())]                      # total_pages 2, but no budget for page 2
    assert len(LumaTrackersClient(eng).search_all('Dune Part Two', 'movie', 2024)) == 100
    assert len(mock.calls) == 1


# ------------------------------------------------------------------ id errors

@pytest.mark.parametrize('bad', [
    R({'status_code': 0, 'success': False, 'status_message': 'Invalid tmdb_id'}),
    R({'status_code': 0, 'success': False, 'status_message': 'Unauthorized'}),
    R({}, 401), R({}, 403), R({}, 422),
])
def test_rejected_id_is_never_auth_or_backoff(engine, bad, caplog):
    eng, mock = engine
    mock.resps = [R(EMPTY), R(EMPTY), R(EMPTY), bad]
    caplog.set_level(logging.INFO)
    assert LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134) == []
    assert eng.guard.blocked('site:beyondhd') is None
    assert eng.store.get(1)['status']['state'] == 'ok'
    assert 'search step 4 id: HTTP' in caplog.text and 'error id (' in caplog.text


def test_rate_limit_on_the_id_still_stops_and_backs_off(engine):
    eng, mock = engine
    mock.resps = [R(EMPTY), R(EMPTY), R(EMPTY), R({}, 429)]
    LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134)
    assert eng.guard.blocked('site:beyondhd')[1] == 'rate'


# ------------------------------------------------------------------ one-time reset

def _auth_backoff(eng, message):
    from app.tracker_store import STATUS_ERROR
    eng.guard.failure('site:beyondhd', 'auth')
    eng.store.set_status(1, STATUS_ERROR, message)


def test_reset_clears_an_id_rejection_backoff_once(engine, tmp_path, caplog):
    from app.tracker_search import reset_bhd_id_backoffs_once
    eng, _ = engine
    _auth_backoff(eng, 'Invalid tmdb_id')
    caplog.set_level(logging.INFO)
    marker = tmp_path / 'm'
    assert reset_bhd_id_backoffs_once(eng.store, eng.guard, marker) == 1
    assert eng.guard.blocked('site:beyondhd') is None and eng.store.get(1)['status']['state'] == 'untested'
    assert 'cleared a backoff from a rejected TMDB id' in caplog.text and KEY not in caplog.text
    _auth_backoff(eng, 'Invalid tmdb_id')
    assert reset_bhd_id_backoffs_once(eng.store, eng.guard, marker) == 0     # runs once
    assert eng.guard.blocked('site:beyondhd')[1] == 'auth'


@pytest.mark.parametrize('msg', ['Unauthorized', 'HTTP 403', 'Invalid API key', ''])
def test_reset_keeps_a_real_auth_backoff(engine, tmp_path, msg):
    from app.tracker_search import reset_bhd_id_backoffs_once
    eng, _ = engine
    _auth_backoff(eng, msg)
    assert reset_bhd_id_backoffs_once(eng.store, eng.guard, tmp_path / 'm') == 0
    assert eng.guard.blocked('site:beyondhd')[1] == 'auth'
    assert eng.store.get(1)['status']['state'] == 'error'
