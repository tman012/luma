"""QA gate c229f2e (Tester): Retry (refresh=1) can never be used to hammer a
tracker, and qBit 3xx messages never show an HTTP code."""
import re

import pytest

from app import tracker_clients as tc
from app import tracker_guard
from app.tracker_search import LumaTrackersClient
from tests.test_bhd_web_fix import FAIL, R, WEB_ONLY, engine  # noqa: F401  (fixture)
from tests.test_qbit_no_redirects import PASSWORD, server  # noqa: F401  (fixture)
from app import qbit_connector


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(tc, '_sleep', lambda s: None)
    monkeypatch.setattr(tracker_guard.TrackerGuard, 'wait_spacing', lambda self, tid: None)


def test_retry_respects_the_hourly_cap(engine, monkeypatch):
    """refresh=True clears a failure backoff, never the per-tracker hourly cap."""
    eng, mock = engine
    monkeypatch.setitem(tracker_guard.LIMITS, 'interactive_per_hour', 3)
    mock.resps = [R(FAIL)] * 50
    for _ in range(10):
        LumaTrackersClient(eng).search_all('Dune', 'movie', 2024, tmdb_id=693134, refresh=True)
    assert len(mock.calls) <= 3
    st = []
    n = len(mock.calls)
    LumaTrackersClient(eng).search_all('Dune', 'movie', 2024, tmdb_id=693134, refresh=True, statuses=st)
    assert len(mock.calls) == n and [x['status'] for x in st] == ['rate']


def test_every_request_of_a_retry_chain_is_charged(engine, monkeypatch):
    """A Retry that runs the whole BHD chain (pages, fallbacks) takes one guard
    slot per HTTP request, so N Retries can't send more than the cap."""
    eng, mock = engine
    monkeypatch.setitem(tracker_guard.LIMITS, 'interactive_per_hour', 5)
    mock.resps = [R(FAIL)] * 50
    for _ in range(4):
        LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134, refresh=True)
    hr, _, _ = eng.guard.counts('site:beyondhd', __import__('time').time())
    assert len(mock.calls) == hr <= 5


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_qbit_redirect_message_has_no_http_code(server, status):  # noqa: F811
    s = server(login_status=status)
    err, _ = qbit_connector.check(s.url, 'u', PASSWORD)
    assert err and not re.search(r'\b\d{3}\b|HTTP', err) and not s.caught
