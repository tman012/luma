"""v201 (PM): past a capped tracker's daily cap, automatic post-air searches stop asking it until the daily
reset; the skip is logged once per tracker per reset window; every tracker capped: the episode waits for the
earliest reset. Feeds stay exempt."""
from app import tv_budget, tv_search, tv_store
from tests.test_tv_v158c import GEN, SITE, engine, guard  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_file_management import store  # noqa: F401

T = 2e9


def fill(guard, start=T):
    for i in range(tv_budget.AUTO_PER_TRACKER_DAILY):
        assert tv_budget.take(SITE, start + i * tv_budget.SLOT_S, guard=guard) is None
    return start + (tv_budget.AUTO_PER_TRACKER_DAILY - 1) * tv_budget.SLOT_S


def skips():
    return rows("SELECT * FROM search_log WHERE outcome = 'skipped'")


def test_daily_reset_is_when_the_oldest_request_ages_out(guard):
    assert tv_budget.daily_reset(SITE, T, guard=guard) is None            # under the cap
    last = fill(guard)
    assert tv_budget.daily_reset(SITE, last + 1, guard=guard) == T + 86400
    assert tv_budget.daily_reset(GEN, last + 1, guard=guard) is None      # no cap


def test_cap_skip_is_logged_once_per_reset_window_and_ipt_is_not_asked(engine, guard):
    last = fill(guard)
    for k in range(30):                                                   # every minute for 30 min
        engine.now = last + 60 * (k + 1)
        _, st = tv_search.query_trackers('Show S01E03', 'episode', None, False, engine.now)
        assert {s['id']: s['status'] for s in st} == {1: tv_search.SLOT_BUSY, 2: 'ok'}
    assert all(c[0] == 2 for c in engine.calls)                          # IPT never asked
    assert len(skips()) == 1
    engine.now = T + 86400 + 1                                            # past the reset: IPT is asked again
    engine.calls.clear()
    tv_search.query_trackers('Show S01E03', 'episode', None, False, engine.now)
    assert engine.calls[0][0] == 1


def test_feed_stays_exempt_at_the_cap(engine, guard):
    last = fill(guard)
    engine.now = last + 60
    _, st = tv_search.query_trackers('', 'feed', None, False, engine.now, [SITE])
    assert st[0]['status'] == 'ok' and len(skips()) == 0


def test_every_tracker_capped_the_episode_waits_for_the_earliest_reset(scanned):  # noqa: F811
    with tv_store.connect() as conn:
        eid = conn.execute('SELECT id FROM episodes ORDER BY id LIMIT 1').fetchone()['id']
    out = {'picks': [], 'answered': False, 'statuses': [
        {'id': 1, 'status': tv_search.SLOT_BUSY, 'reset_at': T + 500},
        {'id': 3, 'status': tv_search.SLOT_BUSY, 'reset_at': T + 200}]}
    tv_search._after(1, [{'id': eid}], out, T, automatic=True)
    with tv_store.connect() as conn:
        assert conn.execute('SELECT next_search_at FROM episodes WHERE id = ?', (eid,)).fetchone()[0] == T + 200
    # one tracker answered: the usual retry rules, not the reset
    out['statuses'].append({'id': 2, 'status': 'ok'}); out['answered'] = True
    tv_search._after(1, [{'id': eid, 'air_date': None, 'airstamp': None}], out, T, automatic=True)
    with tv_store.connect() as conn:
        assert conn.execute('SELECT next_search_at FROM episodes WHERE id = ?', (eid,)).fetchone()[0] != T + 200


def test_v203_schedule_leaves_a_cap_reset_time_alone(scanned):  # noqa: F811
    import datetime as dt
    stamp = dt.datetime.fromtimestamp(T - 600, dt.timezone.utc)
    with tv_store.write() as conn:
        conn.execute('DELETE FROM episode_file_links')
        eid = conn.execute('SELECT id FROM episodes ORDER BY id LIMIT 1').fetchone()['id']
        conn.execute('UPDATE series SET monitored = 1')
        conn.execute('UPDATE episodes SET monitored = 1, last_search_at = NULL, next_search_at = NULL,'
                     ' airstamp = ?, air_date = ?, runtime = 30 WHERE id = ?',
                     (stamp.isoformat(), stamp.date().isoformat(), eid))
    assert tv_search.schedule(T) >= 1                                     # never searched: due at air time
    out = {'picks': [], 'answered': False, 'statuses': [{'id': 1, 'status': tv_search.SLOT_BUSY, 'reset_at': T + 5000}]}
    tv_search._after(1, [{'id': eid}], out, T, automatic=True)
    tv_search.schedule(T + 60)                                            # the next pass
    with tv_store.connect() as conn:
        assert conn.execute('SELECT next_search_at FROM episodes WHERE id = ?', (eid,)).fetchone()[0] == T + 5000
