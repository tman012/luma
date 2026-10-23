from app import tv_search as ts


def test_after_the_early_phase_every_search_is_hourly_whatever_the_count():
    # v253 (Taylor): v203c's "two searches, then a daily catch-up" and v251's floor are gone: after the 6 h
    # early phase (inside it, every 15 min: test_v233_tv_audit) every search is hourly, forever
    e = {'air_date': '2026-10-20', 'airstamp': '2026-10-20T12:00:00Z', 'runtime': 60}
    late = ts.aired_at(e) + ts.FAST_WINDOW_S + 1000.0
    for n in (1, 2, 7):
        assert ts._next_retry(e, late, n) == late + ts.SLOW_S
    assert ts._next_retry(e, late + 30 * 86400, 2) == late + 30 * 86400 + ts.SLOW_S
