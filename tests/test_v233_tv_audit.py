"""v233 TV search audit (PM): Judy Justice by SxxEyy, season searches that miss episodes, the 15-min retry
window after air, and episodes with no air time. Every tracker, Plex and Sonarr call is mocked."""
import datetime as dt
import json

import pytest

from app import tv_grab, tv_search, tv_store
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401

_real_query_trackers = tv_search.query_trackers   # before any fixture mocks it
ET = dt.timezone(dt.timedelta(hours=-4))          # October: EDT


def ts(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=ET).timestamp()


def distinct_torrents(env, monkeypatch):
    """The fixture fetches one tiny .torrent for every grab (one hash); give each release its own."""
    real = next(c.cell_contents for c in tv_grab.grab.__closure__ if getattr(c.cell_contents, '__name__', '') == 'grab')
    monkeypatch.setattr(tv_grab, 'grab', lambda it, sid, season, ids, origin, now=None, **k: real(
        it, sid, season, ids, origin, now, session_factory=env.q.factory,
        fetch=lambda tok: ('torrent', b'd8:announce3:url4:infod4:name%d:%s6:lengthi1eee' % (len(it['title']), it['title'].encode()))))


def make_show(env, title, series_type, season, numbers, air_date, airstamp=None, runtime=None):
    """Turn the fixture's show into *title* with wanted S{season}E{n} episodes aired on *air_date*."""
    sid = env.sid
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET title = ?, aliases = ?, year = NULL, series_type = ? WHERE id = ?',
                     (title, '[]', series_type, sid))
        conn.execute('UPDATE episodes SET monitored = 0 WHERE series_id = ?', (sid,))
        ids = {}
        for n in numbers:
            cur = conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date, airstamp, runtime,'
                               ' monitored) VALUES (?, ?, ?, ?, ?, ?, 1)', (sid, season, n, air_date, airstamp, runtime))
            ids[n] = cur.lastrowid
    return sid, ids


# ------------------------------------------------------------------ 1. Judy Justice

def test_judy_justice_is_not_in_the_daily_titles_but_raw_is():
    assert 'judy justice' not in tv_store.DAILY_TITLES
    assert {'wwe raw', 'wwe monday night raw'} <= set(tv_store.DAILY_TITLES)


def test_v12_migration_puts_judy_justice_back_to_standard_raw_stays_daily(env):
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET title = 'Judy Justice', series_type = 'daily' WHERE id = ?", (env.sid,))
        other = conn.execute('SELECT id FROM series WHERE id != ? ORDER BY id LIMIT 1', (env.sid,)).fetchone()['id']
        conn.execute("UPDATE series SET title = 'WWE Raw', series_type = 'daily' WHERE id = ?", (other,))
        assert tv_store.undaily_titles(conn) == [env.sid]
        assert tv_store.undaily_titles(conn) == []                                   # idempotent
        assert tv_store.daily_types(conn) == []                                      # v11 no longer sets it Daily
    got = {r['id']: r['series_type'] for r in rows('SELECT id, series_type FROM series WHERE id IN (?, ?)', env.sid, other)}
    assert got == {env.sid: 'standard', other: 'daily'}


def test_v12_keeps_daily_when_a_grab_was_named_by_date(env):
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET title = 'Judy Justice', series_type = 'daily' WHERE id = ?", (env.sid,))
        conn.execute("INSERT INTO grabs (hash, series_id, release_title, origin, grabbed_at) VALUES (?, ?, ?, 'auto', 1)",
                     ('f' * 40, env.sid, 'Judy.Justice.2026.10.19.720p.WEB.h264-GRP'))
        assert tv_store.undaily_titles(conn) == []


def test_daily_shows_search_by_date_and_by_sxxeyy_when_numbered():
    show = {'title': 'WWE Raw', 'series_type': 'daily'}
    want = [{'season_number': 2026, 'episode_number': 42, 'air_date': '2026-10-20'},
            {'season_number': 0, 'episode_number': 0, 'air_date': '2026-10-13'}]
    assert tv_search._queries(show, want) == [('episode', 'WWE Raw 2026.10.13'), ('episode', 'WWE Raw 2026.10.20'),
                                              ('episode', 'WWE Raw S2026E42')]


@pytest.mark.parametrize('series_type', ['standard', 'daily'])
def test_judy_justice_e117_e118_found_and_grabbed(env, monkeypatch, series_type):
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    sid, ids = make_show(env, 'Judy Justice', series_type, 4, (117, 118), '2026-10-20', '2026-10-20T07:00:00Z', 30)
    names = ['Judy Justice S04E117 1080p WEB h264-EDITH', 'Judy Justice S04E118 1080p WEB h264-EDITH']
    env.results = [item(n) for n in names]
    out = tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 20, 4))
    sent = [q for q, _, _ in env.sent]
    # standard: the season query; daily: by date and by SxxEyy (date alone found nothing on IPT)
    assert sent == (['Judy Justice S04'] if series_type == 'standard' else
                    ['Judy Justice 2026.10.20', 'Judy Justice S04E117', 'Judy Justice S04E118'])
    assert sorted(p['title'] for p in out['picks'] if p['grabbed']) == names
    assert sorted(r['release_title'] for r in rows('SELECT release_title FROM grabs WHERE series_id = ?', sid)) == names


# ------------------------------------------------------------------ 2. a season search that misses episodes

def _per_query(env, monkeypatch, answers):
    """query_trackers mocked per query (the fixture's mock answers every query the same)."""
    sent = []

    def fake(query, purpose, sid, interactive, now, cfgs=None, tv_ids=None, **k):
        if tv_ids:                              # v256: no id search on this fake tracker
            return [], [{'id': 7, 'name': 'MyTracker', 'status': tv_search.NO_ID}]
        sent.append(query)
        return [item(t) for t in answers.get(query, [])], [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
    monkeypatch.setattr(tv_search, 'query_trackers', fake)
    return sent


def test_love_is_blind_e06_e08_found_after_a_capped_season_search(env, monkeypatch):
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    # the season query: 50 rows (IPT's cap) of other seasons and episodes, none of E06 or E08
    capped = [f'Love Is Blind S{s:02d}E{e:02d} 1080p WEB h264-OTHER' for s in (8, 9, 10) for e in range(1, 17)][:50]
    names = {'Love Is Blind S11E06': ['Love Is Blind S11E06 1080p WEB h264-HOTDOGWATER'],
             'Love Is Blind S11E08': ['Love Is Blind S11E08 1080p WEB h264-HOTDOGWATER']}
    sent = _per_query(env, monkeypatch, {'Love Is Blind S11': capped, **names})
    out = tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 21, 4, 15))
    assert sent == ['Love Is Blind S11', 'Love Is Blind S11E06', 'Love Is Blind S11E08']
    assert sorted(p['title'] for p in out['picks'] if p['grabbed']) == \
        ['Love Is Blind S11E06 1080p WEB h264-HOTDOGWATER', 'Love Is Blind S11E08 1080p WEB h264-HOTDOGWATER']
    assert sorted(r['release_title'] for r in rows('SELECT release_title FROM grabs WHERE series_id = ?', sid)) == \
        ['Love Is Blind S11E06 1080p WEB h264-HOTDOGWATER', 'Love Is Blind S11E08 1080p WEB h264-HOTDOGWATER']


def test_no_per_episode_queries_when_the_season_search_found_everything(env, monkeypatch):
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    sent = _per_query(env, monkeypatch, {'Love Is Blind S11': ['Love Is Blind S11E06 1080p WEB h264-HOTDOGWATER',
                                                               'Love Is Blind S11E08 1080p WEB h264-HOTDOGWATER']})
    tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 21, 4, 15), do_grab=False)
    assert sent == ['Love Is Blind S11']


def test_only_the_missed_episode_is_searched_again(env, monkeypatch):
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    sent = _per_query(env, monkeypatch, {'Love Is Blind S11': ['Love Is Blind S11E06 1080p WEB h264-HOTDOGWATER']})
    tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 21, 4, 15), do_grab=False)
    assert sent == ['Love Is Blind S11', 'Love Is Blind S11E08']


# ------------------------------------------------------------------ 3. the 15-min retry window after air

def _timeline(sid, start, end, step=15 * 60):
    """run schedule + run_due every *step* from *start* to *end*; the times each episode was searched."""
    t = start
    while t <= end:
        tv_search.schedule(t)
        tv_search.run_due(t)
        t += step


@pytest.mark.parametrize('title,season,numbers,stamp,runtime', [
    ('Love Is Blind', 11, (6, 8), '2026-10-21T07:00:00Z', 60),          # 3:00 AM ET, Netflix
    ('Dancing with the Stars', 35, (7,), '2026-10-21T00:00:00Z', 120),  # 8:00 PM ET 10-20
])
def test_new_episodes_retry_every_15_minutes_for_6_hours_after_air(env, monkeypatch, title, season, numbers, stamp, runtime):
    sid, ids = make_show(env, title, 'standard', season, numbers, stamp[:10] if stamp[11:13] >= '04' else
                         (dt.date.fromisoformat(stamp[:10]) - dt.timedelta(days=1)).isoformat(), stamp, runtime)
    when = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda q, p, s, i, now, cfgs=None, **k: (
        when.append(now), ([], [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]))[1])
    aired = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() + 60 * runtime
    _timeline(sid, aired - 30 * 60, aired + 7 * 3600)
    passes = sorted({w for w in when})
    assert passes[0] == aired
    in_window = [w for w in passes if w <= aired + tv_search.FAST_WINDOW_S]
    assert in_window == [aired + i * tv_search.FAST_S for i in range(25)]               # every 15 min, 6 h
    # then it falls back: hourly (v253: forever, no 24 h floor)
    after = [w for w in passes if w > aired + tv_search.FAST_WINDOW_S]
    assert after == [in_window[-1] + k * tv_search.SLOW_S for k in range(1, len(after) + 1)]
    nxt = {r['next_search_at'] for r in rows('SELECT next_search_at FROM episodes WHERE id IN (%s)' % ','.join('?' * len(ids)), *ids.values())}
    assert nxt == {(after or in_window)[-1] + tv_search.SLOW_S}


def test_found_mid_window_is_grabbed_and_stops(env, monkeypatch):
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    aired = ts(2026, 10, 21, 4)
    clock = {'t': 0}
    release = {'Love Is Blind S11': ['Love Is Blind S11E06 1080p WEB h264-HOTDOGWATER', 'Love Is Blind S11E08 1080p WEB h264-HOTDOGWATER']}

    def fake(query, purpose, s, interactive, now, cfgs=None, tv_ids=None, **k):
        if tv_ids:                              # v256: no id search on this fake tracker
            return [], [{'id': 7, 'name': 'MyTracker', 'status': tv_search.NO_ID}]
        up = now >= aired + 2 * 3600 + 30 * 60                  # on IPT 2.5 h after air
        # on IPT both ways: the season query and each SxxEyy (v233.6 retries only the SxxEyy ones)
        hit = [t for t in release['Love Is Blind S11'] if query == 'Love Is Blind S11' or t.startswith(query + ' ')]
        return ([item(t) for t in hit] if up else []), [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
    monkeypatch.setattr(tv_search, 'query_trackers', fake)
    _timeline(sid, aired, aired + 4 * 3600)
    g = rows('SELECT release_title, grabbed_at FROM grabs WHERE series_id = ? ORDER BY release_title', sid)
    assert [r['release_title'] for r in g] == release['Love Is Blind S11']
    assert {r['grabbed_at'] for r in g} == {aired + 10 * tv_search.FAST_S}
    assert all(r['next_search_at'] is None for r in rows('SELECT next_search_at FROM episodes WHERE id IN (?, ?)', *ids.values()))


def test_next_retry_rule():
    e = {'airstamp': '2026-10-21T07:00:00Z', 'runtime': 60, 'air_date': '2026-10-21'}
    aired = ts(2026, 10, 21, 4)
    assert tv_search._next_retry(e, aired + 60, attempts=5) == aired + 60 + tv_search.FAST_S
    late = aired + tv_search.FAST_WINDOW_S + 1
    assert tv_search._next_retry(e, late, attempts=5) == late + tv_search.SLOW_S       # v253: hourly, forever


# ------------------------------------------------------------------ 4. episodes with no air time

def _vpr(env, usual_stamp=None, runtime=None):
    sid, ids = make_show(env, 'Vanderpump Rules', 'standard', 13, (1, 2), '2026-10-21', None, runtime)
    if usual_stamp:
        with tv_store.write() as conn:                    # last season aired at the show's usual time
            for n, day in ((9, '2025-03-11'), (10, '2025-03-18')):
                conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date, airstamp, monitored)'
                             ' VALUES (?, 12, ?, ?, ?, 0)', (sid, n, day, f'{day}T{usual_stamp}'))
    return sid, ids


def test_no_air_time_uses_the_shows_usual_air_time(env):
    sid, ids = _vpr(env, usual_stamp='01:00:00Z', runtime=60)         # 9:00 PM ET in March (EDT): 01:00Z next day
    with tv_store.connect() as conn:
        assert tv_search.usual_times(conn, [sid]) == {sid: (21, 0)}
    tv_search.schedule(ts(2026, 10, 21, 8))
    got = {r['next_search_at'] for r in rows('SELECT next_search_at FROM episodes WHERE id IN (?, ?)', *ids.values())}
    assert got == {ts(2026, 10, 21, 22)}                              # 9:00 PM ET + 60 min


def test_no_air_time_and_no_usual_time_starts_at_the_air_date_in_et(env):
    sid, ids = _vpr(env)
    tv_search.schedule(ts(2026, 10, 21, 8))
    got = {r['next_search_at'] for r in rows('SELECT next_search_at FROM episodes WHERE id IN (?, ?)', *ids.values())}
    assert got == {ts(2026, 10, 21, 0)}                               # 12:00 AM ET on 10-21, not the end of the day


def test_no_air_time_still_gets_the_15_minute_window_and_a_grab(env, monkeypatch):
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    sid, ids = _vpr(env)
    start = ts(2026, 10, 21, 0)
    names = ['Vanderpump Rules S13E01 1080p WEB h264-EDITH', 'Vanderpump Rules S13E02 1080p WEB h264-EDITH']
    when = []

    def fake(query, purpose, s, interactive, now, cfgs=None, tv_ids=None, **k):
        if tv_ids:                              # v256: no id search on this fake tracker
            return [], [{'id': 7, 'name': 'MyTracker', 'status': tv_search.NO_ID}]
        when.append(now)
        up = now >= start + 3 * 3600
        hit = [n for n in names if query == 'Vanderpump Rules S13' or n.startswith(query + ' ')]
        return ([item(n) for n in hit] if up else []), \
            [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
    monkeypatch.setattr(tv_search, 'query_trackers', fake)
    _timeline(sid, start - 15 * 60, start + 4 * 3600)
    assert sorted(set(when)) == [start + i * tv_search.FAST_S for i in range(13)]
    assert sorted(r['release_title'] for r in rows('SELECT release_title FROM grabs WHERE series_id = ?', sid)) == names


# ------------------------------------------------------------------ v233.5 (Reviewer): null episode numbers

def test_queries_and_fallback_skip_episodes_with_no_number():
    show = {'title': 'Love Is Blind', 'series_type': 'standard'}
    want = [{'id': 1, 'season_number': 11, 'episode_number': 6, 'air_date': '2026-10-21'},
            {'id': 2, 'season_number': 11, 'episode_number': None, 'air_date': '2026-10-21'},
            {'id': 3, 'season_number': None, 'episode_number': None, 'air_date': '2026-10-21'}]
    assert tv_search._queries(show, want) == [('episode', 'Love Is Blind S11E06')]
    daily = {'title': 'WWE Raw', 'series_type': 'daily'}
    assert tv_search._queries(daily, [{'season_number': None, 'episode_number': None, 'air_date': '2026-10-20'}]) == \
        [('episode', 'WWE Raw 2026.10.20')]


def test_fallback_with_a_null_number_does_not_raise(env, monkeypatch):
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    real = tv_search.wanted
    monkeypatch.setattr(tv_search, 'wanted', lambda *a, **k: [
        {**e, 'episode_number': None} if e['episode_number'] == 8 else e for e in real(*a, **k)] + [
        {'id': 9999, 'season_number': 11, 'episode_number': None, 'air_date': '2026-10-21', 'airstamp': None}])
    sent = _per_query(env, monkeypatch, {})
    out = tv_search.search_show(sid, None, origin='auto', now=ts(2026, 10, 21, 4, 15), do_grab=False)
    assert out['ok'] and sent == ['Love Is Blind S11E06']


# ------------------------------------------------------------------ v233.6 (Tester FF1): retry only the missed

def test_retries_search_only_the_missed_episodes_no_repeat_season_query(env, monkeypatch):
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    capped = [f'Love Is Blind S{s:02d}E{e:02d} 1080p WEB h264-OTHER' for s in (8, 9, 10) for e in range(1, 17)][:50]
    log = []

    def fake(query, purpose, s, interactive, now, cfgs=None, tv_ids=None, **k):
        if tv_ids:                              # v256: no id search on this fake tracker
            return [], [{'id': 7, 'name': 'MyTracker', 'status': tv_search.NO_ID}]
        log.append((now, query))
        return ([item(t) for t in capped] if query == 'Love Is Blind S11' else []), [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
    monkeypatch.setattr(tv_search, 'query_trackers', fake)
    aired = ts(2026, 10, 21, 4)
    _timeline(sid, aired, aired + tv_search.FAST_WINDOW_S)
    first = [q for t, q in log if t == aired]
    assert first == ['Love Is Blind S11', 'Love Is Blind S11E06', 'Love Is Blind S11E08']
    later = [q for t, q in log if t > aired]
    assert later and set(later) == {'Love Is Blind S11E06', 'Love Is Blind S11E08'}      # no season query again
    assert len(later) == 2 * 24 and len(log) == 3 + 48                                     # was ~75


def test_a_new_episode_still_gets_its_own_query_next_to_the_missed_ones(env, monkeypatch):
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8, 9, 10), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    tv_search._set_missed(sid, [ids[6], ids[8]])
    sent = _per_query(env, monkeypatch, {})
    tv_search.search_show(sid, None, origin='auto', now=ts(2026, 10, 21, 4, 15), do_grab=False)
    assert sent[:3] == ['Love Is Blind S11', 'Love Is Blind S11E06', 'Love Is Blind S11E08']   # E09+E10: season
    assert 'Love Is Blind S11' not in sent[3:]


def test_manual_queries_ignore_the_missed_marks(env, monkeypatch):
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    tv_search._set_missed(sid, list(ids.values()))
    sent = _per_query(env, monkeypatch, {})
    tv_search.search_show(sid, None, origin='manual', now=ts(2026, 10, 21, 4, 15), do_grab=False,
                          queries=[('season', 'Love Is Blind S11')])
    assert sent == ['Love Is Blind S11']


# ------------------------------------------------------------------ v233.7 (Tester FF2): date-only window

def test_no_air_time_no_usual_slot_retries_until_the_end_of_the_air_date_in_et(env, monkeypatch):
    sid, ids = _vpr(env)
    when = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda q, p, s, i, now, cfgs=None, **k: (
        when.append(now), ([], [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]))[1])
    start, end = ts(2026, 10, 21, 0), ts(2026, 10, 22, 0)
    _timeline(sid, start, end + 3600)
    passes = sorted(set(when))
    assert passes[0] == start                                           # every 15 min through the whole day ET
    assert passes == [start + i * tv_search.FAST_S for i in range(97)] + [end + tv_search.SLOW_S]   # v251: then hourly
    assert tv_search.fast_window_end({'air_date': '2026-10-21'}) == end


def test_a_usual_slot_keeps_the_6_hour_window():
    e = {'air_date': '2026-10-21', 'usual_time': (21, 0), 'runtime': 60}
    assert tv_search.fast_window_end(e) == ts(2026, 10, 21, 22) + tv_search.FAST_WINDOW_S
    e = {'air_date': '2026-10-21', 'airstamp': '2026-10-21T07:00:00Z', 'runtime': 60}
    assert tv_search.fast_window_end(e) == ts(2026, 10, 21, 4) + tv_search.FAST_WINDOW_S


# ------------------------------------------------------------------ v233.8 (Reviewer): every window search is charged

def test_each_search_in_the_6h_window_is_charged_to_the_background_budget(env, monkeypatch, tmp_path):
    """The real query_trackers and search engine with a real guard; only the tracker's HTTP client is mocked.
    Each search the 15-min window sends takes one background ('tv' purpose) slot in the guard."""
    import time as _time
    from app import tracker_clients, tracker_guard, tracker_search
    from app.tracker_guard import TrackerGuard, guard_key
    from app.tracker_search import ReleaseTokens, TrackerSearch
    from app.tracker_store import TrackerStore
    store = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key')
    guard = TrackerGuard(tmp_path / 'tracker_guard.json')
    engine = TrackerSearch(store=store, guard=guard, request=lambda *a, **k: None,
                           tokens=ReleaseTokens(store, tmp_path / 'tracker_releases.json'))
    tracker_guard.reset_guard_for_tests(guard)
    tracker_search.reset_search_for_tests(engine)
    monkeypatch.setattr(tracker_search, 'NEG_TTL', 0)               # no cached answers: every search is sent
    monkeypatch.setattr(tracker_search, 'TTL_BACKGROUND', 0)
    ipt = {'id': 7, 'name': 'IPTorrents', 'type': 'iptorrents', 'priority': 1, 'categories': [5000, 5030, 5040]}
    monkeypatch.setattr(tv_search, 'trackers', lambda: [ipt])
    monkeypatch.setattr(tv_search, 'query_trackers', _real_query_trackers)   # the env fixture's mock replaced it
    sent = []
    monkeypatch.setattr(tracker_clients, 'search', lambda cfg, q, cats, limit, req, **k: (sent.append(q), [])[1])
    takes = []
    real_take = guard.take
    monkeypatch.setattr(guard, 'take', lambda tid, interactive, strict, now=None, purpose=None, spacing=True: (
        lambda r: (takes.append((tid, interactive, purpose, r)), r)[1])(real_take(tid, interactive, strict, now, purpose, spacing)))
    clock = {'t': 0.0}
    monkeypatch.setattr(_time, 'time', lambda: clock['t'])
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    aired = ts(2026, 10, 21, 4)
    t = aired
    while t <= aired + tv_search.FAST_WINDOW_S:
        clock['t'] = t
        tv_search.schedule(t)
        tv_search.run_due(t)
        t += tv_search.FAST_S
    # every request asks the guard first, as a background 'tv' request (never the interactive budget)
    assert takes and all(tk[:3] == (guard_key(ipt), False, 'tv') for tk in takes)
    ok = [tk for tk in takes if tk[3] is None]
    assert len(sent) == len(ok)                                     # nothing is sent without a charge
    assert len(guard.log.get(guard_key(ipt), [])) == len(sent)      # and each one sent is recorded
    # each 15-min pass sends at least one search through the whole window (the 60 s gap holds the rest back)
    assert len(sent) >= 25

