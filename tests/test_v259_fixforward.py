"""v259 fix-forward (PM approved). Every tracker, Trakt and TMDB call is a fake; nothing leaves the box."""
import time as _time

import pytest

from app import tracker_clients, tracker_guard, tracker_search, tv_search
from app.tracker_guard import LIMITS, TrackerGuard, guard_key
from app.tracker_http import Response
from app.tracker_search import ReleaseTokens, TrackerSearch
from app.tracker_store import TrackerStore
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_foundations import tvs  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401

# ------------------------------------------------------------------ 2. the title fallback takes the guard's bucket

TZ = {'id': 2, 'name': 'Indexer', 'type': 'torznab', 'url': 'https://idx.example.org', 'api_key': 'k',
      'priority': 1, 'categories': [5000, 5030, 5040]}
CAPS = ('<?xml version="1.0"?><caps><searching><search available="yes" supportedParams="q"/>'
        '<tv-search available="yes" supportedParams="{}"/></searching></caps>')
EMPTY = '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
SHOW = {'id': 1, 'title': 'Ghosts (US)', 'series_type': 'standard', 'aliases': '[]', 'tvdb_id': 371980}
T0 = 2_000_000_000.0


@pytest.fixture
def real(tv_env, monkeypatch, tmp_path):  # noqa: F811
    """The real query_trackers, search engine and guard; only the tracker's HTTP answers are faked."""
    st = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key')
    guard = TrackerGuard(tmp_path / 'tracker_guard.json')
    w = {'sent': [], 'takes': [], 'caps': 'q,season,ep,tvdbid', 'clock': T0}

    def req(method, url, params=None, **k):
        t = (params or {}).get('t')
        w['sent'].append(t)
        return Response(200, {}, (CAPS.format(w['caps']) if t == 'caps' else EMPTY).encode())
    eng = TrackerSearch(store=st, guard=guard, request=req, tokens=ReleaseTokens(st, tmp_path / 'rel.json'))
    tracker_guard.reset_guard_for_tests(guard)
    tracker_search.reset_search_for_tests(eng)
    monkeypatch.setattr(tracker_search, 'NEG_TTL', 0)              # every search is really sent
    monkeypatch.setattr(tracker_search, 'TTL_BACKGROUND', 0)
    real_take = guard.take

    def take(tid, interactive, strict, now=None, purpose=None, spacing=True):
        r = real_take(tid, interactive, strict, now, purpose, spacing)
        w['takes'].append((tid, interactive, strict, purpose, r))
        return r
    monkeypatch.setattr(guard, 'take', take)
    monkeypatch.setattr(_time, 'time', lambda: w['clock'])
    monkeypatch.setattr(_time, 'sleep', lambda s: None)
    tracker_clients.clear_caps()
    w['guard'] = guard
    yield w
    tracker_clients.clear_caps()
    tracker_guard.reset_guard_for_tests(None)
    tracker_search.reset_search_for_tests(None)


def _episodes(n, w):
    for e in range(1, n + 1):
        tv_search._ask(SHOW, f'Ghosts US S05E{e:02d}', 'episode', 1, False, w['clock'], [TZ])


def test_the_title_fallback_asks_the_guard_exactly_as_the_id_search_does(real):
    _episodes(1, real)
    assert real['sent'] == ['caps', 'tvsearch', 'search']          # id found nothing: the title fallback
    gk = guard_key(TZ)
    # one take per request (the caps read rides on the id search's), every one the same background check
    assert [t[:4] for t in real['takes']] == [(gk, False, False, None)] * 3
    assert all(t[4] is None for t in real['takes'])


def test_the_title_fallback_is_held_by_the_burst_bucket_not_the_hourly_total(real):
    _episodes(20, real)
    assert len(real['sent']) == LIMITS['burst'] < LIMITS['global_per_hour']
    assert 'search' in real['sent']                               # title fallbacks were among them
    assert any(t[4] == 'rate limit (bucket empty)' for t in real['takes'])
    n = len(real['sent'])
    real['clock'] += LIMITS['refill_s']                           # one token back
    _episodes(20, real)
    assert len(real['sent']) == n + 1


def test_title_only_trackers_are_held_by_the_same_bucket(real):
    real['caps'] = 'q,season,ep'                                  # caps without ids: title only after the read
    _episodes(20, real)
    assert len(real['sent']) == LIMITS['burst']
    assert real['sent'].count('search') == LIMITS['burst'] - 1    # minus the caps read
    assert all(t[1:4] == (False, False, None) for t in real['takes'])


# ------------------------------------------------------------------ 3. removed TV caps in a save are ignored, then dropped

def test_a_save_with_the_removed_caps_is_ignored_not_a_400(tvs):  # noqa: F811
    from app import tv_settings
    d = tv_settings.load()
    body = {'automation': {'daily_search_cap': 50, 'daily_backlog_grab_cap': 10, 'on_add': 'wait_for_sweep'}}
    assert tv_settings.apply_put(d, body) == {}
    assert d['automation']['on_add'] == 'wait_for_sweep'
    assert not {'daily_search_cap', 'daily_backlog_grab_cap'} & set(d['automation'])
    assert body['automation']['daily_search_cap'] == 50                  # the caller's body isn't changed
    assert tv_settings.apply_put(d, {'automation': {'daily_search_cap': 50, 'bogus': 1}}) == {'automation': 'Invalid'}


def test_the_put_route_ignores_the_removed_caps(minimal_app, tvs):  # noqa: F811
    c = _client(minimal_app)
    r = c.put('/api/settings/tv', json={'automation': {'daily_search_cap': 50, 'daily_backlog_grab_cap': 3,
                                                       'feed_enabled': True}}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()
    a = r.get_json()['automation']
    assert a['feed_enabled'] is True and 'daily_search_cap' not in a and 'daily_backlog_grab_cap' not in a


def test_the_removed_caps_leave_the_file_on_the_next_load_or_save(tvs):  # noqa: F811
    import json
    from app import tv_settings
    p = tv_settings.store_path()
    raw = tv_settings.empty()
    raw['automation'].update(daily_search_cap=50, daily_backlog_grab_cap=10, feed_enabled=True)
    raw['extra_kept'] = 1                                                 # only the removed keys go on load
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw))
    d = tv_settings.load()
    assert d['automation']['feed_enabled'] is True and 'daily_search_cap' not in d['automation']
    on_disk = json.loads(p.read_text())
    assert not {'daily_search_cap', 'daily_backlog_grab_cap'} & set(on_disk['automation'])
    assert on_disk['automation']['feed_enabled'] is True and on_disk['extra_kept'] == 1
    m = p.stat().st_mtime_ns
    tv_settings.load()                                                    # nothing left to drop: no write
    assert p.stat().st_mtime_ns == m
    raw2 = dict(on_disk, automation=dict(on_disk['automation'], daily_backlog_grab_cap=4))
    p.write_text(json.dumps(raw2))
    tv_settings.save(raw2)                                                # and on a save
    assert 'daily_backlog_grab_cap' not in json.loads(p.read_text())['automation']
