"""v253a (PM, fix-forward on 8f6ae59): automatic grabs of backlog TV episodes (past the early phase) are held
to automation.daily_backlog_grab_cap per day ET (default 20), from every automatic path (feed matches,
scheduled backlog searches). Early-phase episodes and the owner's grabs are exempt. qBittorrent, every
tracker and the search engine are mocked."""
import json
import logging
import threading

import pytest

from app import tracker_sources, tv_budget, tv_grab, tv_search, tv_settings, tv_store
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401
from tests.test_v233_tv_audit import distinct_torrents, make_show
from tests.test_v251_tv_daily_search_cap import at

OK = [{'id': 7, 'name': 'MyTracker', 'status': 'ok'}]
NOW = at(2026, 10, 23, 10)
# aired weeks ago: backlog. Aired 9:00 PM ET last night, 30 min, so NOW (10 AM) is past the 6 h early phase
OLD = ('2026-10-01', '2026-10-02T01:00:00Z', 30)
# aired 8:00 AM ET today (12:00Z): NOW is inside its early phase
NEW = ('2026-10-23', '2026-10-23T12:00:00Z', 30)


def _gcap(n):
    d = tv_settings.load()
    d['automation']['daily_backlog_grab_cap'] = n
    tv_settings.save(d)


def _only(sid):
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET monitored = CASE WHEN id = ? THEN 1 ELSE 0 END', (sid,))


def _rel(n, title='Ghosts', season=5):
    return item(f'{title} S{season:02d}E{n:02d} 1080p WEB H264-GRP')


def _adds(env):
    return [c for c in env.q.calls if c[0].endswith('/torrents/add')]


def _real():
    return next(c.cell_contents for c in tv_grab.grab.__closure__ if getattr(c.cell_contents, '__name__', '') == 'grab')


@pytest.fixture
def grabs(env, monkeypatch):
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    return env


# ------------------------------------------------------------------ the setting

def test_setting_default_range_and_validation(env):
    assert tv_settings.empty()['automation']['daily_backlog_grab_cap'] == 20 == tv_budget.backlog_grab_cap()
    for good in (1, 20, 10000):
        d = tv_settings.load()
        assert tv_settings.apply_put(d, {'automation': {'daily_backlog_grab_cap': good}}) == {}
        tv_settings.save(d)
        assert tv_budget.backlog_grab_cap() == good
    for bad in (0, -1, 10001, True, '20', 2.5, None):
        d = tv_settings.load()
        assert tv_settings.apply_put(d, {'automation': {'daily_backlog_grab_cap': bad}}) == \
            {'automation.daily_backlog_grab_cap': tv_settings.INVALID}


@pytest.mark.parametrize('stored', [0, 10001, 'lots', True, None, [5], -3])
def test_a_corrupt_setting_fails_safe_to_20(env, stored):
    d = tv_settings.load()
    raw = dict(d, automation=dict(d['automation'], daily_backlog_grab_cap=stored))
    tv_settings.store_path().write_text(json.dumps(raw))
    assert tv_settings.load()['automation']['daily_backlog_grab_cap'] == 20
    assert tv_budget.backlog_grab_cap() == 20


def test_unreadable_settings_fail_safe_to_20(env, monkeypatch):
    tv_settings.store_path().write_text('{not json')
    assert tv_budget.backlog_grab_cap() == 20
    monkeypatch.setattr(tv_settings, 'load', lambda: (_ for _ in ()).throw(OSError('disk')))
    assert tv_budget.backlog_grab_cap() == 20


# ------------------------------------------------------------------ the cap

def test_backlog_grabs_stop_at_the_cap_and_log_once(grabs, caplog):
    _gcap(3)
    sid, ids = make_show(grabs, 'Ghosts', 'standard', 5, (1, 2, 3, 4, 5), *OLD)
    caplog.set_level(logging.INFO)
    out = [tv_grab.grab(_rel(n), sid, 5, [ids[n]], 'auto', NOW + n) for n in (1, 2, 3, 4, 5)]
    assert [g.get('ok') for g in out] == [True, True, True, False, False]
    assert all(g.get('capped') and g['error'] == tv_grab.BACKLOG_CAPPED for g in out[3:])
    assert len(_adds(grabs)) == 3 and tv_budget.backlog_grabs_today(NOW) == 3
    assert len(rows('SELECT hash FROM grabs WHERE series_id = ?', sid)) == 3
    assert sum('daily backlog grab limit of 3 reached' in r.getMessage() for r in caplog.records) == 1


def test_early_phase_episodes_are_exempt_and_not_counted(grabs):
    _gcap(1)
    sid, ids = make_show(grabs, 'Ghosts', 'standard', 5, (1, 2, 3, 4), *NEW)
    assert tv_search.early(dict(zip(('air_date', 'airstamp', 'runtime'), NEW)), NOW)
    out = [tv_grab.grab(_rel(n), sid, 5, [ids[n]], 'auto', NOW + n) for n in (1, 2, 3, 4)]
    assert all(g.get('ok') for g in out) and len(_adds(grabs)) == 4
    assert tv_budget.backlog_grabs_today(NOW) == 0


def test_a_grab_covering_an_early_phase_episode_is_exempt(grabs):
    _gcap(1)
    tv_budget.take_backlog_grab(NOW)                                         # the cap is already used up
    sid, ids = make_show(grabs, 'Ghosts', 'standard', 5, (1,), *OLD)
    with tv_store.write() as conn:
        new = conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, air_date, airstamp, runtime,'
                           ' monitored) VALUES (?, 5, 2, ?, ?, ?, 1)', (sid, *NEW)).lastrowid
    assert tv_grab.grab(item('Ghosts S05E01E02 1080p WEB H264-GRP'), sid, 5, [ids[1], new], 'auto', NOW)['ok']
    assert tv_grab.grab(_rel(1), sid, 5, [ids[1]], 'auto', NOW + 1).get('capped')   # backlog only: held


def test_manual_grabs_are_exempt_and_not_counted(grabs):
    _gcap(1)
    sid, ids = make_show(grabs, 'Ghosts', 'standard', 5, (1, 2, 3), *OLD)
    assert tv_grab.grab(_rel(1), sid, 5, [ids[1]], 'auto', NOW)['ok']
    assert tv_budget.backlog_grabs_today(NOW) == 1
    assert tv_grab.grab(_rel(2), sid, 5, [ids[2]], 'manual', NOW + 1)['ok']   # at the cap: the owner still grabs
    assert tv_budget.backlog_grabs_today(NOW) == 1
    assert tv_grab.grab(_rel(3), sid, 5, [ids[3]], 'auto', NOW + 2).get('capped')
    assert len(_adds(grabs)) == 2


def test_refund_when_nothing_reaches_qbittorrent(env, monkeypatch):
    allow_grabs(monkeypatch)
    _gcap(1)
    real = _real()
    sid, ids = make_show(env, 'Ghosts', 'standard', 5, (1, 2), *OLD)

    def boom(tok):
        raise tracker_sources.GrabError('Link expired')
    g = real(_rel(1), sid, 5, [ids[1]], 'auto', NOW, session_factory=env.q.factory, fetch=boom)
    assert not g['ok'] and tv_budget.backlog_grabs_today(NOW) == 0          # the link failed: given back

    def crash(tok):
        raise RuntimeError('unexpected')
    with pytest.raises(RuntimeError):
        real(_rel(1), sid, 5, [ids[1]], 'auto', NOW, session_factory=env.q.factory, fetch=crash)
    assert tv_budget.backlog_grabs_today(NOW) == 0

    monkeypatch.delenv('LUMA_TEST_ALLOW')                                    # grabs off: nothing sent
    assert real(_rel(1), sid, 5, [ids[1]], 'auto', NOW, session_factory=env.q.factory).get('blocked')
    assert tv_budget.backlog_grabs_today(NOW) == 0
    allow_grabs(monkeypatch)

    tor = ('torrent', b'd8:announce3:url4:infod4:name1:a6:lengthi1eee')
    assert real(_rel(1), sid, 5, [ids[1]], 'auto', NOW, session_factory=env.q.factory, fetch=lambda t: tor)['ok']
    assert tv_budget.backlog_grabs_today(NOW) == 1
    _gcap(5)                                                                 # the same release again: nothing new
    g = real(_rel(1), sid, 5, [ids[2]], 'auto', NOW + 1, session_factory=env.q.factory, fetch=lambda t: tor)
    assert g.get('existing') and tv_budget.backlog_grabs_today(NOW) == 1


def test_the_count_starts_over_at_midnight_et(grabs):
    _gcap(1)
    sid, ids = make_show(grabs, 'Ghosts', 'standard', 5, (1, 2, 3), *OLD)
    assert tv_grab.grab(_rel(1), sid, 5, [ids[1]], 'auto', at(2026, 10, 23, 23, 58))['ok']
    assert tv_grab.grab(_rel(2), sid, 5, [ids[2]], 'auto', at(2026, 10, 23, 23, 59)).get('capped')
    assert tv_grab.grab(_rel(2), sid, 5, [ids[2]], 'auto', at(2026, 10, 24, 0, 0))['ok']
    assert tv_budget.backlog_grabs_today(at(2026, 10, 24, 0, 1)) == 1
    assert tv_grab.grab(_rel(3), sid, 5, [ids[3]], 'auto', at(2026, 10, 24, 0, 2)).get('capped')


def test_reservations_are_atomic_across_threads(env):
    _gcap(20)
    got = []
    ts = [threading.Thread(target=lambda: got.append(tv_budget.take_backlog_grab(NOW))) for _ in range(40)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert got.count(True) == 20 and tv_budget.backlog_grabs_today(NOW) == 20


# ------------------------------------------------------------------ the automatic paths

def test_scheduled_backlog_search_is_capped_and_the_rest_stay_on_cadence(grabs):
    _gcap(1)
    sid, ids = make_show(grabs, 'Ghosts', 'standard', 5, (1, 2), *OLD)
    _only(sid)
    grabs.results = [_rel(1), _rel(2)]
    with tv_store.write() as conn:
        conn.executemany('UPDATE episodes SET next_search_at = ?, last_search_at = ? WHERE id = ?',
                         [(NOW - 60, NOW - 3660, ids[n]) for n in (1, 2)])
    out = tv_search.run_due(NOW)
    assert out == {'shows': 1, 'grabbed': 1} and len(_adds(grabs)) == 1
    with tv_store.connect() as conn:
        left = [e['id'] for e in tv_search.wanted(conn, sid)]
    assert len(left) == 1                                                    # still wanted, not marked
    nxt = rows('SELECT next_search_at FROM episodes WHERE id = ?', left[0])[0]['next_search_at']
    assert nxt == NOW + tv_search.SLOW_S                                     # back on its hourly cadence


def test_feed_matches_for_backlog_episodes_are_capped(grabs, caplog):
    _gcap(2)
    sid, ids = make_show(grabs, 'Ghosts', 'standard', 5, (1, 2, 3, 4), *OLD)
    _only(sid)
    grabs.results = [_rel(n) for n in (1, 2, 3, 4)]
    caplog.set_level(logging.INFO)
    out = tv_search.feed(NOW)
    assert out['grabbed'] == 2 and len(_adds(grabs)) == 2 and tv_budget.backlog_grabs_today(NOW) == 2
    with tv_store.connect() as conn:
        assert len(tv_search.wanted(conn, sid)) == 2                         # the held two stay wanted
    assert sum('daily backlog grab limit of 2 reached' in r.getMessage() for r in caplog.records) == 1


def test_feed_matches_for_early_phase_episodes_are_not_capped(grabs):
    _gcap(1)
    sid, ids = make_show(grabs, 'Ghosts', 'standard', 5, (1, 2, 3), *NEW)
    _only(sid)
    grabs.results = [_rel(n) for n in (1, 2, 3)]
    assert tv_search.feed(NOW)['grabbed'] == 3 and tv_budget.backlog_grabs_today(NOW) == 0
