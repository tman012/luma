"""v256 (PM): the guard's docstring no longer claims an unenforced 300-per-day cap; the back-off stays."""
from app import tracker_guard


def test_no_daily_cap_is_documented_or_enforced(tmp_path):
    doc = tracker_guard.__doc__
    assert '300 per tracker per rolling 24 h,' not in doc and 'no per-day cap' in doc
    assert 'per_day' not in tracker_guard.LIMITS
    g = tracker_guard.TrackerGuard(tmp_path / 'g.json')
    g.wait_spacing = lambda gk: None
    t = 2e9
    for i in range(400):                                 # 400 interactive searches over 24 h: all sent
        assert g.take('host:idx.example.org', True, False, now=t + i * 216) is None


def test_the_back_off_is_kept():
    L = tracker_guard.LIMITS
    assert (L['rate_s'], L['rate_max_s'], L['banned_s'], L['network_s'], L['network_max_s'],
            L['http400_s'], L['http400_max_s']) == (3600, 6 * 3600, 24 * 3600, 300, 3600, 900, 6 * 3600)
