"""v253 (Taylor): a monitored, missing TV episode is searched until it is grabbed. The air-time search and
the 15-min retries through the early phase (6 h after air) stay; then hourly, forever, under the per-tracker
daily cap (feed polls exempt), with newly aired episodes ahead of the backlog. The 24 h floor is gone.
Every tracker, the search engine and qBittorrent are mocked."""
import json

from app import tv_budget, tv_safety, tv_search, tv_store
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401
from tests.test_v233_tv_audit import _timeline, distinct_torrents, make_show
from tests.test_v251_tv_daily_search_cap import IPT, TL, _cap, at, eng  # noqa: F401

# Grey's Anatomy S23E02: Thursday 9:00 PM ET (01:00Z Friday), 60 min
E = {'air_date': '2026-10-23', 'airstamp': '2026-10-24T01:00:00Z', 'runtime': 60}
OK = [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]


def _only(sid):
    """Just this show is monitored (the fixture library has others)."""
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET monitored = CASE WHEN id = ? THEN 1 ELSE 0 END', (sid,))


def _ep(eid):
    return rows('SELECT next_search_at, last_search_at, search_attempts FROM episodes WHERE id = ?', eid)[0]


def test_the_cadence_15_min_early_then_hourly_forever():
    aired_at = tv_search.aired_at(E)
    assert tv_search.FAST_S == 15 * 60 and tv_search.SLOW_S == 3600 and tv_search.FAST_WINDOW_S == 6 * 3600
    assert tv_search.fast_window_end(E) == aired_at + 6 * 3600              # the early phase is unchanged
    for t in (aired_at, aired_at + 3600, aired_at + 6 * 3600 - 1):
        assert tv_search._next_retry(E, t, attempts=1) == t + tv_search.FAST_S
        assert tv_search._next_retry(E, t, attempts=40) == t + tv_search.FAST_S
    for t in (aired_at + 6 * 3600, aired_at + 23 * 3600, aired_at + 25 * 3600, aired_at + 30 * 86400):
        assert tv_search._next_retry(E, t, attempts=1) == t + tv_search.SLOW_S
        assert tv_search._next_retry(E, t, attempts=99) == t + tv_search.SLOW_S
    for gone in ('AUTO_SEARCHES', 'CATCHUP_S', 'FLOOR_MARGIN_S'):
        assert not hasattr(tv_search, gone)
    assert not hasattr(tv_safety, 'OLD')


def test_15_min_early_then_hourly_past_24h_and_never_stops(env, monkeypatch):
    sid, ids = make_show(env, "Grey's Anatomy", 'standard', 23, (2,), '2026-10-23', '2026-10-24T01:00:00Z', 60)
    _only(sid)
    when = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda q, p, s, i, now, cfgs=None: (when.append(now), ([], OK))[1])
    aired_at = tv_search.aired_at(E)
    _timeline(sid, aired_at - 30 * 60, aired_at + 72 * 3600)
    passes = sorted(set(when))
    early = [w for w in passes if w <= aired_at + tv_search.FAST_WINDOW_S]
    assert early == [aired_at + i * tv_search.FAST_S for i in range(25)]   # air time, then every 15 min for 6 h
    late = [w for w in passes if w > aired_at + tv_search.FAST_WINDOW_S]
    assert late == [early[-1] + k * tv_search.SLOW_S for k in range(1, len(late) + 1)]
    assert max(passes) >= aired_at + 71 * 3600                              # well past the old 24 h floor
    assert _ep(ids[2])['next_search_at'] == max(passes) + tv_search.SLOW_S


def test_greys_late_upload_past_24h_grabs_on_its_own(env, monkeypatch):
    """Up on the tracker 30 h after air (past the old 24 h floor): the hourly search grabs it, then stops."""
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    sid, ids = make_show(env, "Grey's Anatomy", 'standard', 23, (2,), '2026-10-23', '2026-10-24T01:00:00Z', 60)
    _only(sid)
    up_at = tv_safety.aired_start(E) + 30 * 3600
    name = 'Grey s Anatomy S23E02 1080p WEB H264-MeGusta'
    when = []

    def fake(query, purpose, s, interactive, now, cfgs=None):
        when.append(now)
        return ([item(name)] if now >= up_at and query == 'Greys Anatomy S23E02' else []), OK
    monkeypatch.setattr(tv_search, 'query_trackers', fake)
    _timeline(sid, tv_safety.aired_start(E), up_at + 6 * 3600)
    g = rows('SELECT release_title, grabbed_at FROM grabs WHERE series_id = ?', sid)
    assert [r['release_title'] for r in g] == [name] and up_at <= g[0]['grabbed_at'] <= up_at + tv_search.SLOW_S
    assert not tv_safety.in_floor(E, g[0]['grabbed_at'])                    # it was past the old floor
    assert max(when) == g[0]['grabbed_at']                                  # stops once grabbed
    assert _ep(ids[2])['next_search_at'] is None


def test_an_episode_dropped_by_the_old_floor_is_searched_again(env, monkeypatch):
    """Special Forces S05E05: searched, then dropped past the 24 h floor (next_search_at NULL, last_search_at
    set). Nothing else in tv.db keeps it out: the next pass schedules it and searches it."""
    sid, ids = make_show(env, 'Special Forces: World\'s Toughest Test', 'standard', 5, (5,), '2026-10-14',
                         '2026-10-15T00:00:00Z', 60)
    _only(sid)
    now = at(2026, 10, 23, 10)
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = NULL, last_search_at = ?, search_attempts = 30 WHERE id = ?',
                     (at(2026, 10, 15, 19), ids[5]))
    seen = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda q, p, s, i, t, cfgs=None: (seen.append((q, p)), ([], OK))[1])
    assert tv_search.schedule(now) == 1 and _ep(ids[5])['next_search_at'] == now
    assert tv_search.run_due(now)['shows'] == 1
    assert seen == [('Special Forces Worlds Toughest Test S05E05', tv_search.BACKLOG)]
    assert _ep(ids[5])['next_search_at'] == now + tv_search.SLOW_S


def test_grabbed_or_imported_stops_and_a_failed_grab_searches_again(env, monkeypatch):
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3,), '2026-10-01', '2026-10-02T00:00:00Z', 30)
    _only(sid)
    now = at(2026, 10, 23, 10)
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = NULL, last_search_at = ? WHERE id = ?', (now - 86400, ids[3]))
        conn.execute("INSERT INTO grabs (hash, series_id, episode_ids, origin, grabbed_at, state) VALUES (?, ?, ?, 'auto', ?, ?)",
                     ('a' * 40, sid, json.dumps([ids[3]]), now - 86400, 'downloading'))
    for state in ('downloading', 'importing', 'imported'):                 # imported: a deleted file isn't re-grabbed
        with tv_store.write() as conn:
            conn.execute('UPDATE grabs SET state = ?', (state,))
        assert tv_search.schedule(now) == 0 and _ep(ids[3])['next_search_at'] is None
    with tv_store.write() as conn:                                          # a grab already due is dropped too
        conn.execute('UPDATE episodes SET next_search_at = ? WHERE id = ?', (now - 60, ids[3]))
    sent = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda *a, **k: (sent.append(a), ([], OK))[1])
    assert tv_search.run_due(now)['shows'] == 0 and sent == [] and _ep(ids[3])['next_search_at'] is None
    with tv_store.write() as conn:
        conn.execute("UPDATE grabs SET state = 'failed'")
    assert tv_search.schedule(now) == 1 and _ep(ids[3])['next_search_at'] == now


def test_unmonitored_stops_and_monitored_again_resumes(env, monkeypatch):
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3,), '2026-10-01', '2026-10-02T00:00:00Z', 30)
    _only(sid)
    now = at(2026, 10, 23, 10)
    sent = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda *a, **k: (sent.append(a), ([], OK))[1])
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = ?, last_search_at = ? WHERE id = ?', (now - 60, now - 3660, ids[3]))
    for col, table in (('monitored', 'episodes'), ('monitored', 'series')):
        with tv_store.write() as conn:
            conn.execute(f'UPDATE {table} SET {col} = 0 WHERE id = ?', (ids[3] if table == 'episodes' else sid,))
        tv_search.schedule(now)
        assert tv_search.run_due(now)['shows'] == 0 and sent == []
        assert _ep(ids[3])['next_search_at'] is None                        # stopped, not left due
        with tv_store.write() as conn:
            conn.execute(f'UPDATE {table} SET {col} = 1 WHERE id = ?', (ids[3] if table == 'episodes' else sid,))
        assert tv_search.schedule(now) == 1 and _ep(ids[3])['next_search_at'] == now
        assert tv_search.run_due(now)['shows'] == 1 and len(sent) == 1
        assert _ep(ids[3])['next_search_at'] == now + tv_search.SLOW_S       # back on its hourly cadence
        sent.clear()
        with tv_store.write() as conn:
            conn.execute('UPDATE episodes SET next_search_at = ?, last_search_at = ? WHERE id = ?', (now - 60, now - 3660, ids[3]))


def test_backlog_searches_stop_at_their_share_and_leave_the_rest_for_new_episodes(eng):
    _cap(50)
    now = at(2026, 10, 23, 10)
    assert tv_budget.backlog_cap() == 30
    for i in range(40):
        _, st = tv_search.query_trackers(f'Old S01E{i:02d}', tv_search.BACKLOG, None, False, now + i, [IPT])
    assert len(eng.sent) == 30 and st[0]['status'] == tv_search.SLOT_BUSY
    assert st[0]['reset_at'] == at(2026, 10, 24)
    for i in range(25):                                                     # newly aired: the remaining 20
        tv_search.query_trackers(f'New S01E{i:02d}', 'episode', None, False, now + 100 + i, [IPT])
    assert len(eng.sent) == 50 and tv_budget.searches_today(IPT, now) == 50
    for i in range(5):                                                      # feed polls are never held or counted
        tv_search.query_trackers('', 'feed', None, False, now + 200 + i, [IPT])
    assert len(eng.sent) == 55 and tv_budget.searches_today(IPT, now) == 50
    tv_search.query_trackers('Old S01E01', tv_search.BACKLOG, None, False, at(2026, 10, 24, 0, 1), [IPT])
    assert len(eng.sent) == 56                                              # a new day ET


def test_a_held_backlog_search_waits_for_midnight(eng, env):
    _cap(10)
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3,), '2026-10-01', '2026-10-02T00:00:00Z', 30)
    _only(sid)
    now = at(2026, 10, 23, 10)
    for t in (IPT, TL):
        for _ in range(6):
            tv_budget.count_search(t, now - 60)                             # 6 of 10: past the backlog share
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = ?, last_search_at = ? WHERE id = ?', (now - 1, now - 3600, ids[3]))
    assert tv_search.run_due(now)['shows'] == 1 and eng.sent == []
    assert _ep(ids[3])['next_search_at'] == at(2026, 10, 24)


def test_early_phase_shows_go_ahead_of_the_backlog(env, monkeypatch):
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3,), '2026-09-01', '2026-09-02T00:00:00Z', 30)
    now = at(2026, 10, 23, 22)
    with tv_store.write() as conn:
        new = conn.execute("INSERT INTO series (tmdb_id, title, monitored, added_at) VALUES (99999001, 'Fresh', 1, 0)").lastrowid
        fresh = conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date, airstamp, runtime,'
                             " monitored) VALUES (?, 1, 1, '2026-10-23', '2026-10-24T01:00:00Z', 30, 1)", (new,)).lastrowid
        conn.execute('UPDATE series SET monitored = CASE WHEN id IN (?, ?) THEN 1 ELSE 0 END', (sid, new))
        conn.execute('UPDATE episodes SET next_search_at = ?, last_search_at = ? WHERE id = ?', (now - 86400, now - 90000, ids[3]))
        conn.execute('UPDATE episodes SET next_search_at = ?, last_search_at = ? WHERE id = ?', (now - 60, now - 960, fresh))
    calls = []
    monkeypatch.setattr(tv_search, 'search_show', lambda s, i, **k: calls.append((s, k.get('purpose'))) or {'picks': []})
    tv_search.run_due(now, max_shows=1)                                     # one slot: the new episode gets it
    assert calls == [(new, None)]
    calls.clear()
    tv_search.run_due(now, max_shows=5)
    assert calls == [(new, None), (sid, tv_search.BACKLOG)]


def test_the_backlog_rotates_longest_unsearched_first(env, monkeypatch):
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3,), '2026-09-01', '2026-09-02T00:00:00Z', 30)
    now = at(2026, 10, 23, 0)
    with tv_store.write() as conn:
        old = conn.execute("INSERT INTO series (tmdb_id, title, monitored, added_at) VALUES (99999002, 'Older', 1, 0)").lastrowid
        eid = conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date, monitored)'
                           " VALUES (?, 1, 1, '2026-01-01', 1)", (old,)).lastrowid
        conn.execute('UPDATE series SET monitored = CASE WHEN id IN (?, ?) THEN 1 ELSE 0 END', (sid, old))
        # both held to the same midnight; Ghosts was searched more recently
        conn.execute('UPDATE episodes SET next_search_at = ?, last_search_at = ? WHERE id = ?', (now, now - 3600, ids[3]))
        conn.execute('UPDATE episodes SET next_search_at = ?, last_search_at = ? WHERE id = ?', (now, now - 5 * 86400, eid))
    calls = []
    monkeypatch.setattr(tv_search, 'search_show', lambda s, i, **k: calls.append(s) or {'picks': []})
    tv_search.run_due(now, max_shows=1)
    assert calls == [old]
