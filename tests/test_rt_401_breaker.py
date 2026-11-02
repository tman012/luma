"""RTCF 401 (bot protection): breaker, quiet logs, scores-only path, redaction."""
import logging
import pytest

from app import rt_scraper as rs

PAGE = ('<html>Tomatometer "emsId":"d622009d-7292-36aa-9432-6c987c3f59e5" '
        '"criticsScore":{"score":"45"} "audienceScore":{"score":"60"}</html>')


class Resp:
    def __init__(self, status, text='', js=None):
        self.status_code, self.text, self._js = status, text, js

    def json(self):
        return self._js or {}


class FakeSession:
    def __init__(self, rtcf_status=401):
        self.urls, self.rtcf_status, self.headers = [], rtcf_status, {}

    def get(self, url, timeout=None, headers=None):
        self.urls.append(url)
        if '/napi/rtcf/' in url:
            return Resp(self.rtcf_status, js={'reviews': [], 'pageInfo': {}})
        if url.endswith('/reviews'):
            return Resp(200, '<html></html>')
        return Resp(200, PAGE)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def scraper(monkeypatch):
    clock = Clock()
    br = rs.RTCFBreaker(cooldown=60, max_cooldown=240, clock=clock)
    monkeypatch.setattr(rs, 'rtcf_breaker', br)
    s = rs.RTScraper()
    s._cache = {}
    s.session = FakeSession()
    return s, br, clock


def rtcf(s):
    return [u for u in s.session.urls if '/napi/rtcf/' in u]


def test_401_opens_breaker_and_skips_rtcf(scraper, caplog):
    s, br, clock = scraper
    caplog.set_level(logging.INFO, logger='app.rt_scraper')
    r = s.get_reviews('Escape from L.A.', 1996, 20)
    assert r['success'] and r['critic_score'] == 45 and r['audience_score'] == 60
    assert len(rtcf(s)) == 1  # audience call skipped by the open breaker
    for i in range(10):
        s.get_reviews(f'Other {i}', 2000, 20)
    assert len(rtcf(s)) == 1
    warns = [m for m in caplog.messages if 'RTCF' in m and 'status 401' in m]
    assert len(warns) == 1 and 'returned status' not in ' '.join(caplog.messages)
    assert br.state()['open'] and br.skipped >= 10


def test_backoff_doubles_and_recovers(scraper, caplog):
    s, br, clock = scraper
    caplog.set_level(logging.INFO, logger='app.rt_scraper')
    s.get_reviews('A', 2000, 20)
    assert br.cooldown == 60
    clock.t += 61
    s.get_reviews('B', 2000, 20)
    assert br.cooldown == 120 and br.denials == 2
    clock.t += 121
    s.session.rtcf_status = 200
    s.get_reviews('C', 2000, 20)
    assert br.denials == 0 and not br.state()['open']
    assert any('recovered' in m for m in caplog.messages)
    msgs = [m for m in caplog.messages if 'unavailable' in m]
    assert len(msgs) == 2 and 'skipped since last check' in msgs[1]


def test_backoff_capped(scraper):
    s, br, clock = scraper
    for _ in range(6):
        br.record(401)
    assert br.cooldown == 240


def test_scores_only_never_calls_review_apis(scraper):
    s, br, clock = scraper
    for kw in ({'with_reviews': False}, {}):
        s._cache = {}
        n = len(s.session.urls)
        mr = 1 if kw else 0
        r = s.get_reviews('Tulsa King', 2022, mr, 'movie', **kw)
        new = s.session.urls[n:]
        assert r['critic_score'] == 45
        assert not any('/napi/rtcf/' in u or u.endswith('/reviews') for u in new)
    assert br.denials == 0


def test_rt_scores_uses_scores_only(monkeypatch):
    from app import main
    calls = []
    monkeypatch.setattr(main.rt_scraper, 'get_reviews',
                        lambda *a, **k: calls.append(k) or {'success': True, 'critic_score': 1, 'audience_score': 2})
    assert main._rt_scores('X', 2000, 'movie') == {'critic': 1, 'audience': 2}
    assert calls[0].get('with_reviews') is False


def test_redact_url():
    u = 'https://x/y?type=critic&api_key=abc123&token=zz&after=c1'
    out = rs.redact_url(u)
    assert 'abc123' not in out and 'zz' not in out and 'after=c1' in out
