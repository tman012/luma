from app import tv_search as ts


def test_two_automatic_searches_then_feed_only_with_a_daily_catchup():
    # v233 (PM): this rule holds after the 6 h fast window (inside it, every 15 min: test_v233_tv_audit)
    e = {'air_date': '2026-10-20', 'airstamp': '2026-10-20T12:00:00Z', 'runtime': 60}
    late = ts.aired_at(e) + ts.FAST_WINDOW_S + 1000.0
    assert ts._next_retry(e, late, 1) == late + ts.FAST_S
    # v251: the catch-up is hourly while inside the 24 h air-date floor (test_v251_tv_catchup_hourly)
    assert ts._next_retry(e, late, 2) == late + ts.SLOW_S
    assert ts._next_retry(e, late, 7) == late + ts.SLOW_S
