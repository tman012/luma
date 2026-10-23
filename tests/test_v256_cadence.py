"""v256 (PM, Sonarr-style searching): TV settings' Air-time searches (at air, +1 h, +3 h), Slow sweep for
missing episodes (1, 2 and 4 days after that window, then weekly, oldest-searched first, 5 shows an hourly
pass, until grabbed) and On add (Search now / Wait for sweep), with validation. Every tracker, the search
engine and qBittorrent are mocked."""
import pytest

from app import tv_search, tv_settings, tv_store
from tests.test_tv_search_v158b import env, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401
from tests.test_v233_tv_audit import _timeline, make_show
from tests.test_tv_search_no_daily_cap import at

E = {'air_date': '2026-10-23', 'airstamp': '2026-10-24T01:00:00Z', 'runtime': 60}
OK = [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
DAY = 86400


def _set(**kw):
    st = tv_settings.load()
    st['automation'].update(kw)
    tv_settings.save(st)


def _only(sid):
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET monitored = CASE WHEN id = ? THEN 1 ELSE 0 END', (sid,))


def _ep(eid):
    return rows('SELECT next_search_at, last_search_at FROM episodes WHERE id = ?', eid)[0]


# ------------------------------------------------------------------ settings

def test_defaults_and_validation(tmp_path, monkeypatch):
    monkeypatch.setitem(tv_settings._cfg, 'store_path', str(tmp_path / 'tv_settings.json'))
    a = tv_settings.load()['automation']
    assert (a['air_searches'], a['sweep_enabled'], a['on_add']) == (True, True, 'search_now')
    data = tv_settings.load()
    assert tv_settings.apply_put(data, {'automation': {'air_searches': False, 'sweep_enabled': False,
                                                       'on_add': 'wait_for_sweep'}}) == {}
    tv_settings.save(data)
    a = tv_settings.load()['automation']
    assert (a['air_searches'], a['sweep_enabled'], a['on_add']) == (False, False, 'wait_for_sweep')
    for bad in ({'air_searches': 'yes'}, {'sweep_enabled': 1}, {'on_add': 'Search now'}, {'on_add': True},
                {'on_add': None}, {'sweep_days': 3}):
        errs = tv_settings.apply_put(tv_settings.load(), {'automation': bad})
        assert errs and set(errs) <= {'automation', 'automation.air_searches', 'automation.sweep_enabled',
                                      'automation.on_add'}
    raw = tv_settings._normalize({'automation': {'on_add': 'later', 'air_searches': 'x'}})['automation']
    assert raw['on_add'] == 'search_now' and raw['air_searches'] is True       # a bad stored value: the default
    assert tv_settings.view(tv_settings.load())['automation']['on_add'] == 'wait_for_sweep'


# ------------------------------------------------------------------ the cadence

def test_air_searches_off_goes_straight_to_the_sweep():
    c = tv_search.cadence({'air_searches': False})
    aired_at = tv_search.aired_at(E)
    assert tv_search.fast_window_end(E, c) == aired_at and not tv_search.early(E, aired_at, c)
    assert tv_search.first_search(E, aired_at + 60, c) == aired_at + DAY            # the sweep's first step
    assert tv_search.first_search(E, aired_at + 3 * DAY, c) == aired_at + 3 * DAY   # past it: now
    assert tv_search._next_retry(E, aired_at + DAY, c=c) == aired_at + 2 * DAY


def test_sweep_off_stops_after_the_air_searches():
    c = tv_search.cadence({'sweep_enabled': False})
    aired_at = tv_search.aired_at(E)
    assert tv_search._next_retry(E, aired_at, c=c) == aired_at + 3600
    assert tv_search._next_retry(E, aired_at + 3 * 3600, c=c) is None              # the feed alone after that
    assert tv_search.first_search(E, aired_at + DAY, c) is None
    both_off = tv_search.cadence({'air_searches': False, 'sweep_enabled': False})
    assert tv_search.first_search(E, aired_at, both_off) is None


def test_no_air_time_runs_to_the_end_of_the_air_date():
    e = {'air_date': '2026-10-21'}
    start = at(2026, 10, 21)
    assert tv_search.air_times(e) == [start, start + 3600] + [start + k * 3 * 3600 for k in range(1, 8)]


def test_sweep_off_episode_is_not_searched_again_after_its_window(env, monkeypatch):
    _set(sweep_enabled=False)
    sid, ids = make_show(env, "Grey's Anatomy", 'standard', 23, (2,), '2026-10-23', '2026-10-24T01:00:00Z', 60)
    _only(sid)
    when = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda q, p, s, i, now, cfgs=None, **k: (
        k.get('tv_ids') or when.append(now), ([], OK))[1])
    aired_at = tv_search.aired_at(E)
    _timeline(sid, aired_at - 900, aired_at + 3 * DAY)
    assert sorted(set(when)) == [aired_at, aired_at + 3600, aired_at + 3 * 3600]
    assert _ep(ids[2])['next_search_at'] is None


def test_air_searches_off_first_search_is_a_day_after_air(env, monkeypatch):
    _set(air_searches=False)
    sid, ids = make_show(env, "Grey's Anatomy", 'standard', 23, (2,), '2026-10-23', '2026-10-24T01:00:00Z', 60)
    _only(sid)
    when = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda q, p, s, i, now, cfgs=None, **k: (
        k.get('tv_ids') or when.append(now), ([], OK))[1])
    aired_at = tv_search.aired_at(E)
    _timeline(sid, aired_at - 900, aired_at + 2 * DAY + 3600)
    assert sorted(set(when)) == [aired_at + DAY, aired_at + 2 * DAY]


def _shows(n, now):
    """n monitored shows, each with one old missing episode due now; show k was last searched k days ago."""
    out = []
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET monitored = 0')
        for k in range(n):
            sid = conn.execute('INSERT INTO series (tmdb_id, title, monitored, added_at) VALUES (?, ?, 1, 0)',
                               (99990000 + k, f'Show {k}')).lastrowid
            conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date, monitored,'
                         " next_search_at, last_search_at) VALUES (?, 1, 1, '2025-01-01', 1, ?, ?)",
                         (sid, now - 60, now - (k + 1) * DAY))
            out.append(sid)
    return out


def test_the_sweep_takes_five_shows_an_hour_oldest_searched_first(env, monkeypatch):
    now = at(2026, 10, 23, 12)
    sids = _shows(7, now)
    calls = []

    def searched(s, ids, **k):
        calls.append((s, k.get('purpose')))
        with tv_store.write() as conn:
            conn.executemany('UPDATE episodes SET last_search_at = ?, next_search_at = ? WHERE id = ?',
                             [(k['now'], k['now'] + 7 * DAY, i) for i in ids])
        return {'picks': []}
    monkeypatch.setattr(tv_search, 'search_show', searched)
    out = tv_search.run_due(now)
    assert out['swept'] == 5 and calls == [(s, tv_search.BACKLOG) for s in sids[::-1][:5]]
    calls.clear()
    assert tv_search.run_due(now + 600)['shows'] == 0 and calls == []          # an hour between passes
    tv_search.run_due(now + 3600)
    assert calls == [(sids[1], tv_search.BACKLOG), (sids[0], tv_search.BACKLOG)]


def test_a_swept_episode_waits_its_next_step(env, monkeypatch):
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3,), '2026-10-01', '2026-10-02T00:00:00Z', 30)
    _only(sid)
    now = at(2026, 10, 23, 10)
    monkeypatch.setattr(tv_search, 'query_trackers', lambda *a, **k: ([], OK))
    assert tv_search.schedule(now) == 1 and _ep(ids[3])['next_search_at'] == now   # never searched: due
    assert tv_search.run_due(now)['swept'] == 1
    assert _ep(ids[3])['next_search_at'] == now + 7 * DAY                         # weeks after air: weekly


# ------------------------------------------------------------------ on add

def test_on_add_search_now_goes_ahead_of_the_sweep(env, monkeypatch):
    monkeypatch.setattr(tv_search, 'auto_on', lambda: True)
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3, 4), '2026-01-01', '2026-01-02T00:00:00Z', 30)
    _only(sid)
    now = at(2026, 10, 23, 12)
    tv_store.set_meta('tv_sweep_at', str(now))                                    # the sweep just ran
    assert tv_search.on_added(sid, now) == 2 and tv_search._add_now() == [sid]
    calls = []
    monkeypatch.setattr(tv_search, 'search_show', lambda s, i, **k: calls.append((s, sorted(i), k.get('purpose')))
                        or {'picks': []})
    tv_search.run_due(now + 60)
    assert calls == [(sid, sorted(ids.values()), None)] and tv_search._add_now() == []


def test_on_add_wait_for_sweep_queues_nothing(env, monkeypatch):
    monkeypatch.setattr(tv_search, 'auto_on', lambda: True)
    _set(on_add='wait_for_sweep')
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3,), '2026-01-01', '2026-01-02T00:00:00Z', 30)
    _only(sid)
    now = at(2026, 10, 23, 12)
    assert tv_search.on_added(sid, now) == 0 and tv_search._add_now() == []
    assert _ep(ids[3])['next_search_at'] is None
    assert tv_search.schedule(now) == 1                                           # the sweep picks it up
    calls = []
    monkeypatch.setattr(tv_search, 'search_show', lambda s, i, **k: calls.append(k.get('purpose')) or {'picks': []})
    tv_search.run_due(now)
    assert calls == [tv_search.BACKLOG]


@pytest.mark.parametrize('flag', ['air_searches', 'sweep_enabled'])
def test_a_cadence_turned_back_on_revives_the_episode(env, monkeypatch, flag):
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (3,), '2026-10-01', '2026-10-02T00:00:00Z', 30)
    _only(sid)
    now = at(2026, 10, 23, 10)
    _set(air_searches=False, sweep_enabled=False)
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = NULL, last_search_at = ? WHERE id = ?', (now - DAY, ids[3]))
    assert tv_search.schedule(now) == 0
    _set(**{flag: True})
    if flag == 'sweep_enabled':
        assert tv_search.schedule(now) == 1 and _ep(ids[3])['next_search_at'] == now
    else:
        assert tv_search.schedule(now) == 0                     # air-time searches alone: its window is long over


# ------------------------------------------------------------------ the migration

def test_the_migration_moves_everything_once_and_spreads_the_backlog(env, monkeypatch):
    now = at(2026, 10, 23, 12)
    sids = _shows(12, now)                                    # 12 shows due now on the old hourly cadence
    sid, ids = make_show(env, "Grey's Anatomy", 'standard', 23, (2,), '2026-10-23', '2026-10-24T01:00:00Z', 60)
    aired_at = tv_search.aired_at(E)
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET monitored = 1 WHERE id = ?', (sid,))
        conn.execute('UPDATE episodes SET next_search_at = ?, last_search_at = ? WHERE id = ?',
                     (aired_at + 900, aired_at, ids[2]))          # old 15-min retry inside the air window
        recent = conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date, monitored,'
                              " next_search_at, last_search_at) VALUES (?, 1, 2, '2026-10-21', 1, ?, ?)",
                              (sids[0], now + 600, now - 3000)).lastrowid
    t = aired_at + 600
    assert tv_search.migrate_cadence(t) > 0
    assert _ep(ids[2])['next_search_at'] == aired_at + 3600                        # its next air-time search
    by_show = {r['series_id']: r['next_search_at'] for r in rows(
        'SELECT series_id, next_search_at FROM episodes WHERE series_id IN (%s) AND id != ?' % ','.join('?' * 12),
        *sids, recent)}
    # last searched 1..12 days ago, aired long ago: weekly. Searched under 7 days ago: a week after that search;
    # the 6 overdue ones are due now, spread at the sweep's pace (5 shows an hour), oldest-searched first
    for k, s in enumerate(sids):
        if k < 6:
            assert by_show[s] == now - (k + 1) * DAY + 7 * DAY
    assert [by_show[s] for s in sids[::-1][:6]] == [t + k * 720 for k in range(6)]
    # no air time: its window ends 9 PM ET on its air date; W + 2 days (10-23 9 PM) has passed: due, and its
    # show (searched most recently) is spread last
    assert _ep(recent)['next_search_at'] == t + 6 * 720 and t > at(2026, 10, 23, 21)
    assert tv_search.migrate_cadence(t + 60) == 0                                  # once


def test_the_migration_clears_search_times_when_both_cadences_are_off(env):
    _set(air_searches=False, sweep_enabled=False)
    now = at(2026, 10, 23, 12)
    sids = _shows(2, now)
    tv_search.migrate_cadence(now)
    assert rows('SELECT next_search_at FROM episodes WHERE series_id IN (?, ?)', *sids) == \
        [{'next_search_at': None}, {'next_search_at': None}]
