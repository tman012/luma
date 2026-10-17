"""PM ruling: while a tracker is in auth backoff (or failed), all-results
reports it per tracker so Sources shows e.g. "IPTorrents sign-in failed" with
Retry instead of the empty state. Name and a short status only."""
import pytest

import app.main
from app import tracker_search as ts
from app.tracker_http import TrackerError
from app.tracker_search import LumaTrackersClient, TrackerSkipped, search_status

from tests.test_tracker_negative_cache import Mock, R, SIGNED_IN_EMPTY, _engine


@pytest.mark.parametrize('err,want', [
    (TrackerSkipped('backoff (auth)'), 'auth'),
    (TrackerSkipped('backoff (banned)'), 'auth'),
    (TrackerSkipped('backoff (rate)'), 'rate'),
    (TrackerSkipped('backoff (timeout)'), 'unavailable'),
    (TrackerSkipped('hourly cap 30'), 'rate'),
    (TrackerSkipped('rate limit (bucket empty)'), 'rate'),
    (TrackerSkipped('paused'), 'unavailable'),
    (TrackerError('auth'), 'auth'),
    (TrackerError('banned'), 'auth'),
    (TrackerError('rate'), 'rate'),
    (TrackerError('timeout'), 'unavailable'),
    (TrackerError('response'), 'unavailable'),
])
def test_status_mapping(err, want):
    assert search_status(err) == want


def test_auth_backoff_is_reported_per_tracker(tmp_path):
    mock = Mock(R(body=SIGNED_IN_EMPTY))
    eng, cfg = _engine(tmp_path, mock)
    eng.guard.failure(ts.guard_key(cfg), 'auth')
    st = []
    assert LumaTrackersClient(eng).search_all('Dune', 'movie', 2021, statuses=st) == []
    assert not mock.calls                                  # backoff: nothing sent
    assert st == [{'id': cfg['id'], 'name': 'IPT', 'status': 'auth'}]


def test_sign_in_failure_is_reported(tmp_path):
    eng, cfg = _engine(tmp_path, Mock(R(body='<form action=/take_login.php>login</form>')))
    st = []
    LumaTrackersClient(eng).search_all('Dune', 'tv', None, statuses=st)
    assert [x['status'] for x in st] == ['auth']


def test_healthy_empty_answer_reports_nothing(tmp_path):
    eng, cfg = _engine(tmp_path, Mock(R(body=SIGNED_IN_EMPTY)))
    st = []
    LumaTrackersClient(eng).search_all('Dune', 'movie', None, statuses=st)
    assert st == []


def _fake(monkeypatch, statuses, rows=()):
    class Fake(LumaTrackersClient):
        def __init__(self):
            pass

        def search_all(self, q, mt, year=None, tmdb_id=None, refresh=False, statuses=None):
            statuses.extend(list(STAT))
            return list(rows)
    STAT = statuses
    import app.indexer_api as ia
    monkeypatch.setattr(ia, 'get_indexer_client', lambda s: Fake())
    monkeypatch.setattr(app.main, '_indexer_viewer_denied', lambda: None)


def test_all_results_payload_carries_name_and_status_only(test_app, monkeypatch):
    app.main._all_results_cache.clear()
    _fake(monkeypatch, [{'id': 7, 'name': 'IPTorrents', 'status': 'auth'}])
    r = test_app.test_client().get('/api/sources/all-results?title=Dune&media_type=movie')
    assert r.status_code == 200
    j = r.get_json()
    assert j['releases'] == []
    assert j['trackers'] == [{'name': 'IPTorrents', 'status': 'auth'}]


def test_all_results_error_is_generic(test_app, monkeypatch):
    class Boom(LumaTrackersClient):
        def __init__(self):
            pass

        def search_all(self, *a, **k):
            raise RuntimeError('https://t.example/rss?passkey=0123456789abcdef')
    import app.indexer_api as ia
    monkeypatch.setattr(ia, 'get_indexer_client', lambda s: Boom())
    monkeypatch.setattr(app.main, '_indexer_viewer_denied', lambda: None)
    app.main._all_results_cache.clear()
    r = test_app.test_client().get('/api/sources/all-results?title=Dune&media_type=movie&refresh=1')
    assert r.status_code == 500
    assert 'passkey' not in r.get_data(as_text=True)
    assert r.get_json() == {'error': 'Search failed'}
