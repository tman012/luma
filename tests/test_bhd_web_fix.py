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


def test_movie_with_tmdb_id_searches_by_id_not_text():
    m = Mock(R(dict(bhd_pages.page2(), page=1, total_pages=1)))
    tc.search(cfg(), 'Dune: Part Two 2024', [2000, 5000], 500, m, tmdb_id='693134', media_type='movie')
    body = m.calls[0]['json']
    assert body['tmdb_id'] == '693134' and body['categories'] == 'Movies' and 'search' not in body
    assert body['action'] == 'search' and body['rsskey'] == bhd_pages.RK


def test_tv_with_tmdb_id_keeps_only_the_episode_text():
    m = Mock(R({'status_code': 1, 'page': 1, 'results': [], 'total_pages': 1, 'total_results': 0,
                'success': True}))
    tc.search(cfg(), 'Breaking Bad S01E05', [5000], 500, m, tmdb_id='1396', media_type='tv')
    body = m.calls[0]['json']
    assert body['tmdb_id'] == '1396' and body['categories'] == 'TV' and body['search'] == 'S01E05'


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
    assert m.calls[0]['json']['search'] == sent


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
    assert mock.calls[0]['json']['tmdb_id'] == '693134'
    line = next(r.getMessage() for r in caplog.records if 'rows parsed' in r.getMessage())
    assert '104 rows parsed, 104 results, 0 dropped, pages 2, by id yes' in line
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


def test_id_with_rows_sends_no_text_search():
    m = Mock(R(WEB_ONLY))
    out = tc.search(cfg(), 'Dune: Part Two 2024', [2000, 5000], 500, m, tmdb_id='693134', media_type='movie')
    assert len(out) == 4 and len(m.calls) == 1 and 'search' not in m.calls[0]['json']
    assert tc.last_stats()['fallback'] == ''


def test_id_with_no_rows_falls_back_to_text_with_the_year():
    m = Mock(R(EMPTY), R(WEB_ONLY))
    out = tc.search(cfg(), 'Dune: Part Two 2024', [2000, 5000], 500, m, tmdb_id='693134', media_type='movie')
    assert len(out) == 4 and len(m.calls) == 2
    assert m.calls[0]['json']['tmdb_id'] == '693134'
    fb = m.calls[1]['json']
    assert 'tmdb_id' not in fb and fb['search'] == 'Dune Part Two 2024' and fb['categories'] == 'Movies'
    assert tc.last_stats()['fallback'] == 'text'


def test_id_and_year_text_empty_then_title_only():
    m = Mock(R(EMPTY), R(EMPTY), R(WEB_ONLY))
    out = tc.search(cfg(), 'Dune: Part Two 2024', [2000, 5000], 500, m, tmdb_id='693134', media_type='movie')
    assert len(out) == 4 and [c['json'].get('search') for c in m.calls] == [None, 'Dune Part Two 2024',
                                                                            'Dune Part Two']
    assert tc.last_stats()['fallback'] == 'text, no year'


def test_tv_id_with_no_rows_falls_back_to_title_and_episode():
    m = Mock(R(EMPTY), R(EMPTY))
    tc.search(cfg(), "Grey's Anatomy S01E05", [5000], 500, m, tmdb_id='1416', media_type='tv')
    assert len(m.calls) == 2
    assert m.calls[0]['json']['search'] == 'S01E05' and m.calls[0]['json']['tmdb_id'] == '1416'
    assert m.calls[1]['json']['search'] == 'Greys Anatomy S01E05' and m.calls[1]['json']['categories'] == 'TV'
    assert 'tmdb_id' not in m.calls[1]['json']


def test_fallback_is_one_budget_unit_and_not_asked_again(engine, caplog):
    eng, mock = engine
    mock.resps = [R(EMPTY), R(EMPTY), R(EMPTY), R(EMPTY)]
    caplog.set_level(logging.INFO)
    out = LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134)
    assert out == []
    # id, text + year, text alone; the search_all year retry does not ask BHD again
    assert [c['json'].get('search') for c in mock.calls] == [None, 'Dune Part Two 2024', 'Dune Part Two']
    assert len(eng.guard.log.get('site:beyondhd', [])) == 1
    assert 'by id yes, fallback text, no year' in caplog.text
    assert KEY not in caplog.text and bhd_pages.RK not in caplog.text and 'Dune' not in caplog.text


def test_fallback_hit_logged(engine, caplog):
    eng, mock = engine
    mock.resps = [R(EMPTY), R(WEB_ONLY)]
    caplog.set_level(logging.INFO)
    out = LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134)
    assert len(out) == 4 and len(eng.guard.log.get('site:beyondhd', [])) == 1
    assert '4 rows parsed, 4 results, 0 dropped, pages 2, by id yes, fallback text,' in caplog.text
