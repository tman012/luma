from app import tv_search as ts


def test_two_automatic_searches_then_feed_only_with_a_daily_catchup():
    e = {'air_date': '2026-10-20', 'airstamp': None, 'runtime': 60}
    assert ts._next_retry(e, 1000.0, 1) == 1000.0 + ts.FAST_S
    assert ts._next_retry(e, 1000.0, 2) == 1000.0 + ts.CATCHUP_S
    assert ts._next_retry(e, 1000.0, 7) == 1000.0 + ts.CATCHUP_S
