"""v259: tick() runs migrate_cadence in its own try/except. A bad row logs, marks the v256 migration done and
never blocks scheduling, the feed poll or the due searches. Trackers are fakes (test_tv_search_v158b's env)."""
import logging

from app import tv_search, tv_settings, tv_store
from tests.test_tv_search_v158b import allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401


def _auto():
    st = tv_settings.load()
    st['automation'].update(enabled=True, feed_enabled=True)
    tv_settings.save(st)


def _bad_row(env):
    with tv_store.write() as conn:              # a last search time that isn't a number: _next_retry's float() fails
        conn.execute("UPDATE episodes SET next_search_at = 1, last_search_at = 'garbage' WHERE id = ?", (env.e2[4],))


def test_a_bad_row_in_the_migration_never_blocks_scheduling_or_the_feed(env, monkeypatch, caplog):  # noqa: F811
    allow_grabs(monkeypatch)
    _auto()
    monkeypatch.setattr(tv_search, 'refresh', lambda now: 0)
    feeds = []
    monkeypatch.setattr(tv_search, 'feed', lambda now: feeds.append(now) or {'grabbed': 0})
    _bad_row(env)
    env.results = [item('Severance.S02E03.1080p.WEB-DL-B')]
    now = tv_search.aired_at(rows('SELECT air_date, airstamp, runtime FROM episodes WHERE id = ?', env.e2[3])[0]) + 5 * 3600
    tv_store.set_meta(tv_search.MIGRATED_KEY, '')
    caplog.set_level(logging.ERROR, logger=tv_search.logger.name)
    out = tv_search.tick(now=now)
    assert 'cadence migration failed' in caplog.text                     # logged, with the traceback
    assert tv_store.get_meta(tv_search.MIGRATED_KEY) == str(now)          # marked done
    assert out['scheduled'] >= 1 and feeds == [now] and out['due'] is not None and env.sent


def test_the_migration_is_not_retried_on_every_tick(env, monkeypatch):  # noqa: F811
    allow_grabs(monkeypatch)
    _auto()
    monkeypatch.setattr(tv_search, 'refresh', lambda now: 0)
    monkeypatch.setattr(tv_search, 'feed', lambda now: {'grabbed': 0})
    calls = []

    def boom(now):
        calls.append(now)
        raise RuntimeError('bad row')
    monkeypatch.setattr(tv_search, 'migrate_cadence', boom)
    tv_store.set_meta(tv_search.MIGRATED_KEY, '')
    tv_search.tick(now=2_000_000_000.0)
    assert tv_store.get_meta(tv_search.MIGRATED_KEY)
    monkeypatch.undo()
    allow_grabs(monkeypatch)
    real = []
    monkeypatch.setattr(tv_search, 'refresh', lambda now: 0)
    monkeypatch.setattr(tv_search, 'feed', lambda now: {'grabbed': 0})
    orig = tv_search.migrate_cadence
    monkeypatch.setattr(tv_search, 'migrate_cadence', lambda now: real.append(orig(now)) or real[-1])
    tv_search.tick(now=2_000_000_100.0)
    assert len(calls) == 1 and real == [0]                               # the real one sees the key: does nothing


def test_even_when_marking_fails_the_tick_still_schedules(env, monkeypatch, caplog):  # noqa: F811
    allow_grabs(monkeypatch)
    _auto()
    monkeypatch.setattr(tv_search, 'refresh', lambda now: 0)
    monkeypatch.setattr(tv_search, 'feed', lambda now: {'grabbed': 0})
    monkeypatch.setattr(tv_search, 'migrate_cadence', lambda now: 1 / 0)
    real_set = tv_store.set_meta

    def set_meta(k, v):
        if k == tv_search.MIGRATED_KEY:
            raise OSError('disk')
        return real_set(k, v)
    monkeypatch.setattr(tv_store, 'set_meta', set_meta)
    out = tv_search.tick(now=2_000_000_000.0)
    assert out['due'] is not None
