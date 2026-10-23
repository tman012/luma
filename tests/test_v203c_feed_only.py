from app import tv_search as ts


def test_after_the_air_window_the_sweep_then_weekly_whatever_the_count(monkeypatch):
    # v256 (PM): v253's hourly-forever is replaced by the slow sweep: 1, 2 and 4 days after the air-time searches,
    # then weekly, whatever the attempt count (test_v256_cadence covers the settings)
    monkeypatch.setattr(ts, 'automation', lambda: {'air_searches': True, 'sweep_enabled': True})
    e = {'air_date': '2026-10-20', 'airstamp': '2026-10-20T12:00:00Z', 'runtime': 60}
    w = ts.aired_at(e) + 3 * 3600
    for n in (1, 2, 7):
        assert ts._next_retry(e, w + 1000.0, n) == w + 86400
    assert ts._next_retry(e, w + 30 * 86400, 2) == w + 30 * 86400 + 7 * 86400
