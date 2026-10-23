"""v261 (PM LOW): Never delete chips and Unkeep rows show qBit's torrent name (read-only), else …last4. The name
is stored with the hash at Keep time and the seeded hash's name is read lazily. The INFO log stays last-4."""
import logging

from app import downloads_guard as dg, storage_cleanup as sc
from app.automation import dg_store
from tests.test_v182_series_detail import _req
from tests.test_v260_downloads_guard import (API, KEEP_TORRENTS, NOW, DAY, env, keep, ordered, site, tor,  # noqa: F401
                                             ordered_site, owner, store, tv_env, minimal_app)

AHS = '7224bfad84076c0a76c36fb891d3b91ac226efb6'


def test_keep_stores_qbits_name_from_the_last_preview(keep):
    c = ordered_site()
    keep.torrents = KEEP_TORRENTS + [tor('b' * 40, 'Oldest.Movie.2001', done=NOW - 999 * DAY)]
    _req(c, 'get', '/api/downloads-guard/preview')
    r = _req(c, 'post', '/api/downloads-guard/keep', {'hashes': ['B' * 40]}).get_json()
    assert r['protected_names']['b' * 40] == 'Oldest.Movie.2001'
    assert _req(c, 'get', API).get_json()['protected_names']['b' * 40] == 'Oldest.Movie.2001'
    rows = {x['name']: x for x in _req(c, 'get', '/api/downloads-guard/preview').get_json()['rows']}
    assert rows['Oldest.Movie.2001']['torrent_name'] == 'Oldest.Movie.2001' and rows['Oldest.Movie.2001']['kept']
    _req(c, 'post', '/api/downloads-guard/unkeep', {'hashes': ['b' * 40]})
    assert 'b' * 40 not in dg_store.names() and 'b' * 40 not in dg_store._raw_names()   # forgotten with the hash


def test_keep_without_a_known_name_stores_none(keep):
    r = _req(ordered_site(), 'post', '/api/downloads-guard/keep', {'hashes': ['e' * 40]}).get_json()
    assert 'e' * 40 not in r['protected_names']                                # the UI shows …eeee


def test_seeded_hash_name_is_read_lazily_and_read_only(env, site, monkeypatch):
    dg_store.seed()
    assert dg_store.names() == {}
    reads = []
    env.torrents = [tor(AHS.upper(), 'American.Horror.Story.S01'), tor('a' * 40, 'Other')]
    real = sc.fetch_torrents
    monkeypatch.setattr(sc, 'fetch_torrents', lambda: (reads.append(1), real())[1])
    monkeypatch.setattr(sc, 'delete_with_files', lambda h: (_ for _ in ()).throw(AssertionError('qBit write')))

    class Now:
        def __init__(self, target=None, **kw):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(dg.threading, 'Thread', Now)
    view = _req(site, 'get', API).get_json()                                    # first GET: reads qBit once
    assert reads == [1]
    assert dg_store.names() == {AHS: 'American.Horror.Story.S01'}
    assert set(dg_store._raw_names()) == {AHS}                                   # only Never delete names kept
    _req(site, 'get', API)
    assert reads == [1]                                                          # known now: no more reads
    assert _req(site, 'get', API).get_json()['protected_names'] == {AHS: 'American.Horror.Story.S01'}
    assert view['protected_names'] in ({}, {AHS: 'American.Horror.Story.S01'})


def test_lazy_read_is_throttled_when_qbit_is_down(env, site, monkeypatch):
    dg_store.seed()
    env.qbit_ok = False
    reads = []
    real = sc.fetch_torrents
    monkeypatch.setattr(sc, 'fetch_torrents', lambda: (reads.append(1), real())[1])

    class Now:
        def __init__(self, target=None, **kw):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(dg.threading, 'Thread', Now)
    for _ in range(3):
        assert _req(site, 'get', API).status_code == 200
    assert reads == [1] and dg_store.names() == {}


def test_boot_reads_fill_the_seeded_name(env, monkeypatch):
    from app import dlmon_prune
    monkeypatch.setattr(dlmon_prune, 'run_once', lambda: None)
    cfg = dg_store.seed()
    env.torrents = [tor(AHS, 'American.Horror.Story.S01')]
    dg._boot_reads(cfg)
    assert dg_store.names() == {AHS: 'American.Horror.Story.S01'}


def test_log_line_stays_last_four(env, caplog):
    dg_store.seed()
    env.torrents = [tor(AHS, 'American.Horror.Story.S01')]
    dg_store.remember_names({AHS: 'American.Horror.Story.S01'})
    caplog.set_level(logging.INFO, logger=sc.logger.name)
    sc.log_never_delete_now(dg_store.settings())
    line = [r.getMessage() for r in caplog.records if 'never-delete' in r.getMessage()][0]
    assert '…efb6' in line and 'American' not in line and AHS not in line


def test_names_are_display_only(env):
    dg_store.seed()
    dg_store.remember_names({AHS: '  ' + 'x' * 500, 'f' * 40: 'Not kept'})
    assert dg_store.names() == {AHS: 'x' * dg_store.NAME_MAX}
    assert 'protected_names' not in dg_store.settings()
    assert dg_store.widens(dg_store.settings(), dg_store.settings()) == []
