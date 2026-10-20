"""v204a (PM, Taylor): no Luma-side daily per-tracker cap on searches or grabs (TV and movies).
Only the tracker's own signals (429 / rate limit, network errors) back off."""
from app import tv_budget
from app.tracker_guard import TrackerGuard

T0 = 1_800_000_000.0
GK = 'site:iptorrents'


def test_more_than_8_searches_and_grabs_a_day_are_allowed(tmp_path):
    g = TrackerGuard(tmp_path / 'g.json')
    t = T0
    for i in range(40):                       # 20 searches + 20 grabs in a few hours
        assert g.take(GK, False, False, now=t, purpose='tv') is None, i
        assert g.take(GK, False, False, now=t + 1, spacing=False) is None, i
        t += 600
    cfg = {'type': 'iptorrents', 'url': 'https://iptorrents.com'}
    assert tv_budget.under_daily(cfg, t, g) is True
    assert tv_budget.daily_reset(cfg, t, g) is None
    assert tv_budget.usage(cfg, t, g)['cap'] is None


def test_429_and_network_errors_still_back_off(tmp_path):
    g = TrackerGuard(tmp_path / 'g.json')
    end = g.failure(GK, 'rate', retry_after=120, now=T0, status=429)
    assert end > T0
    assert g.take(GK, False, False, now=T0 + 10, purpose='tv').startswith('backoff')
    g2 = TrackerGuard(tmp_path / 'g2.json')
    g2.failure(GK, 'network', now=T0)
    assert g2.take(GK, False, False, now=T0 + 10, spacing=False).startswith('backoff')
