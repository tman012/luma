"""v251 (b): after the 6 h fast window, catch-up searches run hourly until the 24 h air-date floor (v177)
closes, so a late upload still grabs on its own. The floor itself is unchanged. Trackers and qBit mocked."""
from app import tv_safety, tv_search
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401
from tests.test_v233_tv_audit import _timeline, distinct_torrents, make_show, ts

# Grey's Anatomy S23E02: Thursday 9:00 PM ET (01:00Z Friday), 60 min
E = {'air_date': '2026-10-23', 'airstamp': '2026-10-24T01:00:00Z', 'runtime': 60}


def test_the_24h_floor_is_unchanged():
    assert tv_safety.FLOOR_S == 24 * 3600
    start = tv_safety.aired_start(E)
    assert tv_safety.in_floor(E, start + 23 * 3600)
    assert not tv_safety.in_floor(E, start + 24 * 3600 + 1)


def test_hourly_after_the_fast_window_until_the_floor_closes():
    start = tv_safety.aired_start(E)
    late = tv_search.fast_window_end(E) + 60
    assert tv_search._next_retry(E, late, attempts=9) == late + tv_search.SLOW_S
    # the last hour: pulled in to just before the floor closes, never past it
    t = start + 24 * 3600 - 40 * 60
    nxt = tv_search._next_retry(E, t, attempts=9)
    assert nxt == start + tv_safety.FLOOR_S - tv_search.FLOOR_MARGIN_S and tv_safety.in_floor(E, nxt)
    # past the floor: the old daily value (run_due drops it as past the floor; nothing is searched)
    gone = start + 25 * 3600
    assert tv_search._next_retry(E, gone, attempts=9) == gone + tv_search.CATCHUP_S


def test_every_catchup_lands_inside_the_floor():
    start = tv_safety.aired_start(E)
    t = tv_search.fast_window_end(E)
    seen = []
    while True:
        t = tv_search._next_retry(E, t, attempts=9)
        if not tv_safety.in_floor(E, t):
            break
        seen.append(t)
    assert len(seen) >= 17                                   # ~hourly from 7 h to 24 h after it aired
    assert all(b - a <= tv_search.SLOW_S for a, b in zip(seen, seen[1:]))
    assert seen[-1] >= start + tv_safety.FLOOR_S - tv_search.SLOW_S


def test_greys_late_upload_grabs_on_its_own(env, monkeypatch):
    """Up on the tracker 14 h after air (past the 6 h window): the hourly catch-up grabs it inside the floor."""
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    sid, ids = make_show(env, "Grey's Anatomy", 'standard', 23, (2,), '2026-10-23', '2026-10-24T01:00:00Z', 60)
    start = tv_safety.aired_start(E)
    up_at = start + 14 * 3600
    name = 'Grey s Anatomy S23E02 1080p WEB H264-MeGusta'
    when = []

    def fake(query, purpose, s, interactive, now, cfgs=None):
        when.append(now)
        hit = [item(name)] if now >= up_at and query == 'Greys Anatomy S23E02' else []
        return hit, [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
    monkeypatch.setattr(tv_search, 'query_trackers', fake)
    _timeline(sid, start, start + 24 * 3600)
    g = rows('SELECT release_title, grabbed_at FROM grabs WHERE series_id = ?', sid)
    assert [r['release_title'] for r in g] == [name]
    assert up_at <= g[0]['grabbed_at'] <= up_at + tv_search.SLOW_S
    assert tv_safety.in_floor(E, g[0]['grabbed_at'])
    late = sorted({w for w in when if w > tv_search.fast_window_end(E)})
    assert late and all(b - a == tv_search.SLOW_S for a, b in zip(late, late[1:]))


def test_nothing_is_searched_past_the_floor(env, monkeypatch):
    sid, ids = make_show(env, "Grey's Anatomy", 'standard', 23, (2,), '2026-10-23', '2026-10-24T01:00:00Z', 60)
    start = tv_safety.aired_start(E)
    when = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda q, p, s, i, now, cfgs=None: (
        when.append(now), ([], [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]))[1])
    _timeline(sid, start, start + 30 * 3600)
    assert when and max(when) <= start + tv_safety.FLOOR_S              # the floor is inclusive (v177)
    assert all(tv_safety.in_floor(E, w) for w in when)
