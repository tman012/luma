"""Tester (c229f2e QA, hammer-5xx): back-to-back Retries kept 2 s only from
chain start to chain start, so the gaps between requests read 2.05, 2.05,
0.06. The spacing timer is now stamped on EVERY charged request, so any two
requests to one tracker are at least min_interval_s apart, across Retries
too."""
import pytest

from app import tracker_clients as tc
from app import tracker_guard
from app.tracker_guard import TrackerGuard
from app.tracker_search import LumaTrackersClient
from tests.test_bhd_web_fix import FAIL, R, engine  # noqa: F401  (fixture)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(tracker_guard.time, 'time', c.time)
    monkeypatch.setattr(tracker_guard.time, 'sleep', c.sleep)
    monkeypatch.setattr(tc, '_sleep', c.sleep)                 # the chain's own page gap
    monkeypatch.setattr(tc, '_monotonic', c.time, raising=False)
    return c


def _real_spacing(eng):
    # The shared fixture turns spacing off; this test is about spacing.
    eng.guard.wait_spacing = TrackerGuard.wait_spacing.__get__(eng.guard)


def _gaps(times):
    return [round(b - a, 3) for a, b in zip(times, times[1:])]


def test_back_to_back_retries_keep_2s_gaps(engine, clock, monkeypatch):
    eng, mock = engine
    _real_spacing(eng)
    monkeypatch.setitem(tracker_guard.LIMITS, 'interactive_per_hour', 50)
    sent = []
    inner = mock.__call__

    def stamped(method, url, **kw):
        sent.append(clock.t)
        clock.t += 0.05                 # the tracker answers in 50 ms
        return inner(method, url, **kw)
    eng.request = stamped
    mock.resps = [R(FAIL)] * 50         # 5xx-like bad answers: each Retry runs the whole chain
    for _ in range(4):
        try:
            LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134,
                                               refresh=True)
        except Exception:
            pass
    assert len(sent) >= 6, sent         # several chains of several requests each
    gaps = _gaps(sent)
    assert min(gaps) >= tracker_guard.LIMITS['min_interval_s'], gaps


def test_every_charged_request_stamps_the_timer(engine, clock):
    eng, mock = engine
    stamps = []
    real = eng.guard.mark_sent
    eng.guard.mark_sent = lambda gk: (stamps.append(clock.t), real(gk))[1]
    mock.resps = [R(FAIL)] * 50
    try:
        LumaTrackersClient(eng).search_all('Dune: Part Two', 'movie', 2024, tmdb_id=693134, refresh=True)
    except Exception:
        pass
    assert len(stamps) == len(mock.calls) > 1


def test_wait_spacing_counts_from_the_last_request(tmp_path, clock):
    g = TrackerGuard(tmp_path / 'g.json')
    g.wait_spacing('t')                 # chain start
    clock.t += 5
    g.mark_sent('t')                    # a later page of the same chain
    before = clock.t
    g.wait_spacing('t')                 # the next Retry
    assert clock.t - before >= tracker_guard.LIMITS['min_interval_s']
