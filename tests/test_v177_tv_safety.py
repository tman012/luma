"""v177 (Taylor's TV grab safety rules): one central check in tv_grab.grab. 1) v253 (Taylor, replaces the
24 h floor): automatic grabs for monitored episodes with an air date, however long ago they aired; 2) never
an episode that already has a file (manual too); 3) v253: missing episodes are searched until grabbed;
4) (removed) the daily ceiling; 5) the read-only dry run."""
import datetime as dt
import logging
import os
import shutil
import subprocess
import sys

import pytest

from app import tv_grab, tv_safety, tv_search, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_tv_search_v158b import _auto, allow_grabs, env, item  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

NOW = 1_800_000_000.0                                    # 2027-01-15 03:00 ET


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def air(eid, t=None, date=None):
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET airstamp = ?, air_date = ? WHERE id = ?',
                     (iso(t) if t is not None else None,
                      date if date is not None else (dt.datetime.fromtimestamp(t).date().isoformat() if t is not None else None), eid))


def adds(env):
    return [c for c in env.q.calls if c[0].endswith('/torrents/add')]


def grab(env, n, origin='auto', title='Severance.S02E03.1080p.WEB-DL-B', ids=None):
    return tv_grab.grab(item(title), env.sid, 2, ids or [env.e2[n]], origin, NOW)


@pytest.mark.parametrize('ago', [23 * 3600, 3600, -6 * 3600, 24 * 3600 + 60, 30 * 86400, 400 * 86400])
def test_no_air_date_floor_any_age_is_grabbed(env, monkeypatch, ago):
    allow_grabs(monkeypatch)                                     # v253: the 24 h floor is gone
    air(env.e2[3], NOW - ago)
    assert grab(env, 3).get('ok') and len(adds(env)) == 1


def test_no_air_date_is_never_grabbed(env, monkeypatch):
    allow_grabs(monkeypatch)
    air(env.e2[3], None, date='')
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET air_date = NULL, airstamp = NULL WHERE id = ?', (env.e2[3],))
    assert grab(env, 3)['refused'] == tv_safety.NO_DATE and adds(env) == []


def test_an_automatic_grab_needs_a_monitored_show_and_episode(env, monkeypatch, caplog):
    allow_grabs(monkeypatch)
    air(env.e2[3], NOW - 3 * 86400)
    for show, ep in ((1, 0), (0, 1)):
        with tv_store.write() as conn:
            conn.execute('UPDATE episodes SET monitored = ? WHERE id = ?', (ep, env.e2[3]))
            conn.execute('UPDATE series SET monitored = ? WHERE id = ?', (show, env.sid))
        with caplog.at_level(logging.INFO, logger='app.tv_safety'):
            assert grab(env, 3)['refused'] == tv_safety.UNMONITORED
    assert adds(env) == []
    assert any('TV grab refused (auto)' in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)
    assert grab(env, 3, origin='manual').get('ok')               # the owner's grab isn't held to it


def test_a_pack_with_one_old_episode_is_grabbed(env, monkeypatch):
    allow_grabs(monkeypatch)
    air(env.e2[3], NOW - 3600)
    air(env.e2[4], NOW - 5 * 86400)
    g = grab(env, 3, title='Severance.S02.1080p.WEB-DL-B', ids=[env.e2[3], env.e2[4]])
    assert g.get('ok') and len(adds(env)) == 1


def test_a_date_only_episode_of_any_age_is_grabbed(env, monkeypatch):
    allow_grabs(monkeypatch)
    today = dt.datetime.fromtimestamp(NOW).date()
    air(env.e2[3], None, date=(today - dt.timedelta(days=1)).isoformat())
    assert grab(env, 3).get('ok')
    air(env.e2[4], None, date=(today - dt.timedelta(days=3)).isoformat())
    assert grab(env, 4, title='Severance.S02E04.1080p.WEB-DL-B').get('ok')


def _linked_episode(sid):
    return rows("SELECT l.episode_id AS id FROM episode_file_links l JOIN episode_files f ON f.id = l.file_id"
                " JOIN episodes e ON e.id = l.episode_id WHERE f.state = 'linked' AND e.series_id = ?", sid)[0]['id']


@pytest.mark.parametrize('origin', ['auto', 'manual', 'upgrade'])
def test_never_an_episode_that_has_a_file(env, monkeypatch, origin):
    allow_grabs(monkeypatch)
    if origin == 'upgrade':                                      # even with upgrades switched on
        from app import tv_settings
        st = tv_settings.load()
        st['automation']['upgrades_enabled'] = True
        tv_settings.save(st)
    eid = _linked_episode(env.sid)
    air(eid, NOW - 3600)
    g = tv_grab.grab(item('Severance.S01E02.2160p.WEB-DL-B'), env.sid, 1, [eid], origin, NOW)
    assert g['refused'] == tv_safety.HAS_FILE and adds(env) == []


def test_a_manual_grab_is_not_held_to_the_floor(env, monkeypatch):
    allow_grabs(monkeypatch)
    air(env.e2[3], NOW - 30 * 86400)
    assert grab(env, 3, origin='manual').get('ok') and len(adds(env)) == 1


def _fill(n, origin='auto', at=NOW - 3600):
    with tv_store.write() as conn:
        for i in range(n):
            conn.execute("INSERT INTO grabs (hash, series_id, episode_ids, origin, grabbed_at, state) VALUES (?, NULL, '[]', ?, ?, 'imported')",
                         (f'{origin[0]}{i:039d}', origin, at))


@pytest.mark.skip(reason='v204b (PM, Taylor): daily ceiling of automatic TV grabs removed; see test_v204b_grab_retry')
def test_the_daily_ceiling_is_30_automatic_grabs_in_a_rolling_24h(env, monkeypatch, caplog):
    allow_grabs(monkeypatch)
    air(env.e2[3], NOW - 3600)
    _fill(29)
    _fill(40, 'manual')                                          # manual grabs don't count
    _fill(50, 'upgrade', at=NOW - 86400 - 60)                   # older than 24 h don't count
    assert grab(env, 3).get('ok')                                # the 30th
    air(env.e2[4], NOW - 3600)
    with caplog.at_level(logging.INFO, logger='app.tv_safety'):
        g = grab(env, 4, title='Severance.S02E04.1080p.WEB-DL-B')
    assert g['refused'] == tv_safety.CAP and len(adds(env)) == 1
    assert any('daily limit of 30' in r.getMessage() for r in caplog.records)
    assert grab(env, 4, origin='manual', title='Severance.S02E04.1080p.WEB-DL-B').get('ok')   # manual still works


def test_old_missing_episodes_are_scheduled_too(env, monkeypatch):
    allow_grabs(monkeypatch)
    _auto(backlog=False)                                         # backlog_enabled is ignored
    air(env.e2[3], NOW - 3 * 86400)                              # v253: old missing: scheduled too
    air(env.e2[4], NOW - 3600)
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = NULL, last_search_at = NULL')
    tv_search.schedule(NOW)
    due = {r['id'] for r in rows('SELECT id FROM episodes WHERE next_search_at IS NOT NULL')}
    assert env.e2[3] in due and env.e2[4] in due


def test_a_due_search_past_the_old_floor_is_searched_and_grabbed(env, monkeypatch):
    import time
    now = time.time()                                            # wanted() compares air dates with today
    allow_grabs(monkeypatch)
    _auto()
    air(env.e2[3], now - 3 * 86400)
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = ? WHERE id = ?', (now - 60, env.e2[3]))
    env.results = [item('Severance.S02E03.1080p.WEB-DL-B')]
    assert tv_search.run_due(now) == {'shows': 1, 'grabbed': 1, 'swept': 1}      # v256: the sweep's pass
    assert len(adds(env)) == 1
    assert rows('SELECT next_search_at FROM episodes WHERE id = ?', env.e2[3])[0]['next_search_at'] is None


def test_an_automatic_search_asks_for_old_episodes_too(env, monkeypatch):
    import time
    now = time.time()                                            # wanted() compares air dates with today
    allow_grabs(monkeypatch)
    before = tv_search.search_show(env.sid, origin='auto', now=now, do_grab=False)['wanted']
    for n in (3, 4, 6):
        air(env.e2[n], now - 10 * 86400)
    env.results = [item('Severance.S02E03.1080p.WEB-DL-B')]
    out = tv_search.search_show(env.sid, origin='auto', now=now)
    assert out['wanted'] >= 3 and out['wanted'] >= before and env.sent != []
    assert [p['episodes'] for p in out['picks'] if p['grabbed']] == [[env.e2[3]]]


def test_search_on_add_queues_every_wanted_episode(env, monkeypatch):
    import time
    now = time.time()
    allow_grabs(monkeypatch)
    _auto()
    for n, ago in ((3, 10 * 86400), (4, 3600), (6, 2 * 86400)):
        air(env.e2[n], now - ago)
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = NULL')
    tv_search.on_added(env.sid, now)
    due = {r['id'] for r in rows('SELECT id FROM episodes WHERE next_search_at IS NOT NULL')}
    assert {env.e2[3], env.e2[4], env.e2[6]} <= due


def test_the_feed_grabs_an_old_missing_episode(env, monkeypatch):
    import time
    now = time.time()                                            # wanted() compares air dates with today
    allow_grabs(monkeypatch)
    _auto(feed=True)
    air(env.e2[4], now - 5 * 86400)                              # v253: no floor for the feed either
    env.results = [item('Severance.S02E04.1080p.WEB-DL-B')]
    assert tv_search.feed(now) == {'trackers': 1, 'grabbed': 1} and len(adds(env)) == 1


# ------------------------------------------------------------------ the dry run

def test_the_dry_run_lists_tonight_read_only(env, tmp_path):
    for n, ago in ((3, 10 * 86400), (4, 3600), (5, -3600), (6, -2 * 3600)):
        air(env.e2[n], NOW - ago)                                # E5 has a file (the fixture's S02E05)
    src = str(tv_store.db_path())
    copy = tmp_path / 'tv.db'
    with tv_store.connect() as conn:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    shutil.copy(src, copy)
    os.chmod(copy, 0o444)
    before = (os.stat(copy).st_mtime_ns, open(copy, 'rb').read())
    script = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'tv_dryrun.py')

    def run(*extra):
        r = subprocess.run([sys.executable, script, '--db', str(copy), '--now', str(NOW), *extra],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return r.stdout
    out = run()
    assert 'S02E04' in out and 'S02E06' in out                   # aired an hour ago; airs tonight (5 AM ET)
    assert 'S02E03' in out and 'S02E05' not in out               # v253: old is eligible; has a file
    assert 'Automatic grabs in the last 24 h: 0' in out
    assert 'Eligible episodes (monitored, no file): 3' in out
    assert 'TV automation (no tv_settings.json: defaults): enabled=False, feed_enabled=False' in out
    assert 'TV trackers enabled: none yet (0 switched on' in out
    import json as _j
    (tmp_path / 'tv_settings.json').write_text(_j.dumps({'automation': {'enabled': True, 'feed_enabled': True}}))
    (tmp_path / 'section_trackers.json').write_text(_j.dumps({'sections': {'tv': {'order': [3, 1], 'enabled': [3, 1]}}}))
    out2 = run()
    assert 'TV automation (tv_settings.json): enabled=True, feed_enabled=True, backlog_enabled=False' in out2
    assert 'TV trackers enabled: yes, 2 switched on in Settings > TV > Trackers (ids 3, 1;' in out2
    assert 'Fri Jan 15, 2:00 AM ET' in out and 'through Fri Jan 15, 6:00 AM ET' in out
    assert 'Window: through Fri Jan 15, 6:00 AM ET (no air-date floor)' in out
    out = run('--until', dt.datetime.fromtimestamp(NOW + 3600, dt.timezone.utc).isoformat())
    assert 'S02E04' in out and 'S02E06' not in out and 'S02E03' in out
    assert (os.stat(copy).st_mtime_ns, open(copy, 'rb').read()) == before
    assert not os.path.exists(str(copy) + '-wal')


def test_the_floor_is_the_earlier_of_midnight_et_and_24h_ago():
    from zoneinfo import ZoneInfo
    et = ZoneInfo('America/New_York')
    t = dt.datetime(2026, 10, 18, 16, 45, tzinfo=et).timestamp()
    assert tv_safety.floor_at(t) == t - 86400                    # 24 h ago is earlier than midnight
    t = dt.datetime(2026, 11, 1, 23, 30, tzinfo=et).timestamp()  # the 25-hour day (DST ends)
    assert tv_safety.floor_at(t) == dt.datetime(2026, 11, 1, 0, 0, tzinfo=et).timestamp() < t - 86400
    today_4pm = dt.datetime(2026, 11, 1, 16, 0, tzinfo=et).timestamp()
    assert tv_safety.in_floor({'airstamp': iso(dt.datetime(2026, 11, 1, 0, 10, tzinfo=et).timestamp())}, t)
    assert tv_safety.in_floor({'airstamp': iso(today_4pm)}, t)


def test_the_dry_run_calls_out_the_4pm_episode_with_its_reason(env, tmp_path):
    from zoneinfo import ZoneInfo
    et = ZoneInfo('America/New_York')
    now = dt.datetime(2027, 1, 15, 17, 0, tzinfo=et).timestamp()
    four = dt.datetime(2027, 1, 15, 16, 0, tzinfo=et).timestamp()
    air(env.e2[3], four)
    air(env.e2[4], four + 15 * 60)
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET monitored = 0 WHERE id = ?', (env.e2[4],))
    with tv_store.connect() as conn:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    copy = tmp_path / 'tv.db'
    shutil.copy(str(tv_store.db_path()), copy)
    script = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'tv_dryrun.py')
    r = subprocess.run([sys.executable, script, '--db', str(copy), '--now', str(now), '--at', '16:00'],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = r.stdout.split('Airing within 30 min of 16:00 ET today: ')[1]
    assert out.startswith('2')                                   # (date-only episodes of the day would be listed too)
    assert 'S02E03 - Episode 3  Fri Jan 15, 4:00 PM ET; monitored; no file; ELIGIBLE' in out
    assert 'S02E04 - Episode 4  Fri Jan 15, 4:15 PM ET; episode not monitored; no file; not eligible: episode not monitored' in out


def test_an_upgrade_grab_needs_the_upgrades_switch(env, monkeypatch):
    from app import tv_settings
    allow_grabs(monkeypatch)
    air(env.e2[3], NOW - 3600)
    assert grab(env, 3, origin='upgrade')['refused'] == tv_safety.UPGRADES_OFF and adds(env) == []
    st = tv_settings.load()
    st['automation']['upgrades_enabled'] = True
    tv_settings.save(st)
    assert grab(env, 3, origin='upgrade').get('ok')              # no file yet, inside the floor


def test_nothing_reads_replace_verified_and_tv_never_deletes_an_episode_file():
    import pathlib
    import re
    app = pathlib.Path(__file__).resolve().parents[1] / 'app'
    users = [p.name for p in app.glob('*.py') if 'replace_verified' in p.read_text() and p.name != 'tv_settings.py']
    assert users == ['tv_safety.py']                             # a docstring mention only
    for name in ('tv_downloads.py', 'tv_grab.py', 'tv_search.py', 'tv_scan.py', 'tv_watch.py', 'tv_safety.py'):
        src = (app / name).read_text()
        assert not re.search(r'\b(os\.remove|os\.unlink|shutil\.rmtree|\.unlink\()', src), name
