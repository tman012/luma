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
