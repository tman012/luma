"""Merge of cursor/bhd-web-fix (223412b) into sources-followup: the BHD chain
and the negative cache, statuses and refresh live together."""
import logging

import pytest

from app import tracker_clients as tc
from app.tracker_search import LumaTrackersClient
from tests.test_bhd_web_fix import EMPTY, FAIL, R, WEB_ONLY, engine  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(tc, '_sleep', lambda s: None)


def test_all_steps_error_is_never_negative_cached_and_shows_a_status(engine):
    eng, mock = engine
    mock.resps = [R(FAIL)]
    st = []
    assert LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134, statuses=st) == []
    assert not eng._neg_store()
    assert st == [{'id': 1, 'name': 'BHD', 'status': 'unavailable'}]


def test_auth_error_shows_sign_in_status(engine):
    eng, mock = engine
    mock.resps = [R({'status_code': 0, 'success': False, 'status_message': 'Invalid API key'})]
    st = []
    LumaTrackersClient(eng).search_all('Dune', 'movie', 2024, tmdb_id=693134, statuses=st)
    assert [x['status'] for x in st] == ['auth']


def test_chain_stopped_by_request_budget_is_not_negative_cached(engine, monkeypatch):
    from app import tracker_guard
    eng, mock = engine
    monkeypatch.setitem(tracker_guard.LIMITS, 'interactive_per_hour', 2)
    mock.resps = [R(EMPTY)]
    LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134)
    assert not eng._neg_store()


def test_chain_stopped_by_time_budget_is_not_negative_cached(engine, monkeypatch):
    eng, mock = engine
    t = iter([0.0] + [100.0] * 50)
    monkeypatch.setattr(tc, '_monotonic', lambda: next(t))
    mock.resps = [R(EMPTY)]
    assert LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134) == []
    assert not eng._neg_store()


def test_a_step_error_then_empty_is_not_negative_cached(engine):
    eng, mock = engine
    mock.resps = [R(FAIL), R(EMPTY), R(EMPTY), R(EMPTY)]
    LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134)
    assert not eng._neg_store()


def test_a_clean_empty_chain_is_negative_cached_keyed_with_the_id(engine, caplog):
    eng, mock = engine
    mock.resps = [R(EMPTY)]
    c = LumaTrackersClient(eng)
    caplog.set_level(logging.INFO)
    c.search_all('Nothing Here', 'movie', None, tmdb_id=42)
    n = len(mock.calls)
    c.search_all('Nothing Here', 'movie', None, tmdb_id=42)
    assert len(mock.calls) == n and 'cached empty answer' in caplog.text
    c.search_all('Nothing Here', 'movie', None, tmdb_id=43)              # another id: asked again
    assert len(mock.calls) > n
    m = len(mock.calls)
    c.search_all('Nothing Here', 'movie', None, tmdb_id=42, refresh=True)  # Retry bypasses
    assert len(mock.calls) > m


def test_stats_are_reset_per_search_so_torznab_is_not_judged_by_bhd(tmp_path):
    from tests.test_tracker_negative_cache import EMPTY_RSS, Mock, R as RR, _engine
    eng, cfg = _engine(tmp_path, Mock(RR(body=EMPTY_RSS)), 'torznab')
    tc._set_stats(rows=9, steps=[{'error': 'response'}])        # left over from a BHD search
    assert eng.search_one(cfg, 'Dune', [2000], 10, True) == []
    assert eng._neg_store()                                      # judged on its own answer


# ------------------------------------------------------------------ Tester bugs 5/6/3 (4049342)

def test_bhd_request_never_outlives_the_chain_budget(monkeypatch):
    """Bug 5: an attempt starting at 44.9 s gets the 3 s floor, not 60 s; the
    whole chain stays well under the 75 s Sources wait."""
    from tests.test_bhd_web_fix import Mock, movie
    clock = [0.0]
    monkeypatch.setattr(tc, '_monotonic', lambda: clock[0])
    monkeypatch.setattr(tc, '_sleep', lambda s: clock.__setitem__(0, clock[0] + s))
    seen = []

    class M(Mock):
        def __call__(self, method, url, **kw):
            seen.append(kw['deadline_s'])
            clock[0] += kw['deadline_s']          # worst case: every request runs to its deadline
            return R(EMPTY)
    movie(M(R(EMPTY)))
    assert seen[0] == tc.BHD_CHAIN_BUDGET_S and all(d <= tc.BHD_CHAIN_BUDGET_S for d in seen)
    assert clock[0] <= tc.BHD_CHAIN_BUDGET_S + tc.BHD_MIN_REQUEST_S + tc.BHD_PAGE_GAP_S * 3 < 75


def test_no_new_page_past_the_chain_deadline(monkeypatch):
    from tests.fixtures import bhd_pages
    from tests.test_bhd_web_fix import Mock, movie
    clock = [0.0]
    monkeypatch.setattr(tc, '_monotonic', lambda: clock[0])

    class M(Mock):
        def __call__(self, method, url, **kw):
            clock[0] += 50.0
            return super().__call__(method, url, **kw)
    m = M(R(bhd_pages.page1()), R(bhd_pages.page2()))       # page 1 of 2
    out = movie(m)
    assert len(m.calls) == 1 and len(out) == 100 and tc.last_stats()['stopped'] == 'budget'


def test_tracker_http_request_deadline_is_capped_by_the_caller(monkeypatch):
    from app import tracker_http as th
    seen = {}

    class S:
        def request(self, *a, **kw):
            seen.update(kw)
            raise th.requests.exceptions.Timeout()

        def close(self):
            pass
    monkeypatch.setattr(th, 'check_target', lambda u: ('93.184.216.34', 443))
    monkeypatch.setattr(th, '_pinned_session', lambda u, a: S())
    with pytest.raises(th.TrackerError):
        th.request('GET', 'https://t.example/x', deadline_s=4.0)
    assert seen['timeout'] == (min(th.CONNECT_TIMEOUT, 4.0), min(th.READ_TIMEOUT, 4.0))


def test_retry_clears_the_trackers_failure_backoff_like_test(engine):
    """Bug 6: Retry (refresh) asks again at once after an auth/http backoff."""
    eng, mock = engine
    eng.guard.failure('site:beyondhd', 'auth')
    st = []
    LumaTrackersClient(eng).search_all('Dune', 'movie', None, statuses=st)
    assert st and st[0]['status'] == 'auth' and not mock.calls       # backoff: nothing sent
    mock.resps = [R(WEB_ONLY)]
    st = []
    out = LumaTrackersClient(eng).search_all('Dune', 'movie', None, refresh=True, statuses=st)
    assert len(out) == 4 and not st and eng.guard.blocked('site:beyondhd') is None


@pytest.mark.parametrize('kind', ['rate', 'banned'])
def test_retry_never_clears_a_rate_limit_or_ban(engine, kind):
    eng, mock = engine
    eng.guard.failure('site:beyondhd', kind)
    LumaTrackersClient(eng).search_all('Dune', 'movie', None, refresh=True)
    assert not mock.calls and eng.guard.blocked('site:beyondhd')[1] == kind


def test_partial_failure_is_not_route_cached(test_app, monkeypatch):
    """Bug 3: rows from one tracker plus another tracker's error are shown with
    that tracker's status, and the answer is not cached."""
    import app.indexer_api as ia
    import app.main

    class Fake(LumaTrackersClient):
        def __init__(self):
            pass

        def search_all(self, q, mt, year=None, tmdb_id=None, refresh=False, statuses=None):
            statuses.append({'id': 2, 'name': 'BHD', 'status': 'unavailable'})
            return [{'title': 'Dune.2021.1080p.WEB-DL-GRP', 'indexer': 'IPT', 'indexer_id': 1, 'size': 1,
                     'seeders': 5, 'download_url': '/api/trackers/release/abc', 'guid': 'g1'}]
    monkeypatch.setattr(ia, 'get_indexer_client', lambda s: Fake())
    monkeypatch.setattr(app.main, '_indexer_viewer_denied', lambda: None)
    app.main._all_results_cache.clear()
    j = test_app.test_client().get('/api/sources/all-results?title=Dune&media_type=movie').get_json()
    assert len(j['releases']) == 1 and j['trackers'] == [{'name': 'BHD', 'status': 'unavailable'}]
    assert not app.main._all_results_cache


def test_retry_skips_the_answer_cache_and_shows_the_new_error(engine):
    """Tester bhd gate: after a good answer, BHD fails; Retry must reach BHD and
    show its status (not the cached rows), then a later Retry brings rows back."""
    eng, mock = engine
    mock.resps = [R(WEB_ONLY)]
    assert len(LumaTrackersClient(eng).search_all('Dune', 'movie', None)) == 4
    n = len(mock.calls)
    assert len(LumaTrackersClient(eng).search_all('Dune', 'movie', None)) == 4 and len(mock.calls) == n  # cached
    mock.resps = [R({'status_code': 0, 'success': False, 'status_message': 'Invalid API key'})]
    st = []
    out = LumaTrackersClient(eng).search_all('Dune', 'movie', None, refresh=True, statuses=st)
    assert len(mock.calls) > n and [x['status'] for x in st] == ['auth']
    assert out == []                     # the failure is shown, not the cached rows
    mock.resps = [R(WEB_ONLY)]
    st = []
    out = LumaTrackersClient(eng).search_all('Dune', 'movie', None, refresh=True, statuses=st)
    assert len(out) == 4 and not st
