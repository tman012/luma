"""v233 TV search audit (PM): Judy Justice by SxxEyy, season searches that miss episodes, the 15-min retry
window after air, and episodes with no air time. Every tracker, Plex and Sonarr call is mocked."""
import datetime as dt
import json

import pytest

from app import tv_grab, tv_search, tv_store
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401

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

    def fake(query, purpose, sid, interactive, now, cfgs=None):
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
    monkeypatch.setattr(tv_search, 'query_trackers', lambda q, p, s, i, now, cfgs=None: (
        when.append(now), ([], [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]))[1])
    aired = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() + 60 * runtime
    _timeline(sid, aired - 30 * 60, aired + 7 * 3600)
    passes = sorted({w for w in when})
    assert passes[0] == aired
    in_window = [w for w in passes if w <= aired + tv_search.FAST_WINDOW_S]
    assert in_window == [aired + i * tv_search.FAST_S for i in range(25)]               # every 15 min, 6 h
    assert [w for w in passes if w > aired + tv_search.FAST_WINDOW_S] == []            # then it falls back
    nxt = {r['next_search_at'] for r in rows('SELECT next_search_at FROM episodes WHERE id IN (%s)' % ','.join('?' * len(ids)), *ids.values())}
    assert nxt == {in_window[-1] + tv_search.CATCHUP_S}


def test_found_mid_window_is_grabbed_and_stops(env, monkeypatch):
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    sid, ids = make_show(env, 'Love Is Blind', 'standard', 11, (6, 8), '2026-10-21', '2026-10-21T07:00:00Z', 60)
    aired = ts(2026, 10, 21, 4)
    clock = {'t': 0}
    release = {'Love Is Blind S11': ['Love Is Blind S11E06 1080p WEB h264-HOTDOGWATER', 'Love Is Blind S11E08 1080p WEB h264-HOTDOGWATER']}

    def fake(query, purpose, s, interactive, now, cfgs=None):
        up = now >= aired + 2 * 3600 + 30 * 60                  # on IPT 2.5 h after air
        return ([item(t) for t in release.get(query, [])] if up else []), [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
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
    assert tv_search._next_retry(e, late, attempts=5) == late + tv_search.CATCHUP_S


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

    def fake(query, purpose, s, interactive, now, cfgs=None):
        when.append(now)
        up = now >= start + 3 * 3600
        return ([item(n) for n in names] if up and query == 'Vanderpump Rules S13' else []), \
            [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
    monkeypatch.setattr(tv_search, 'query_trackers', fake)
    _timeline(sid, start - 15 * 60, start + 4 * 3600)
    assert sorted(set(when)) == [start + i * tv_search.FAST_S for i in range(13)]
    assert sorted(r['release_title'] for r in rows('SELECT release_title FROM grabs WHERE series_id = ?', sid)) == names
