"""PM acceptance for v157 (Ops' last 24h on live v156): Cops S38E16, Treasure Island (2026)
S01E02 and American Hostage S01E06 turn downloaded through the new link paths (the mover
hook, the 60s poll, inotify), and Cops S38E16 shows on its own air date (Fri Oct 16, 2026:
TMDB's date, not the UTC day of its 9 PM ET airstamp or the 1:30 AM landing). tv.db only."""
import os
import threading
import time

import pytest

from app import tv_plex, tv_scan, tv_watch
from tests import tv_fake
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import _get, aired  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows  # noqa: F401
from tests.test_tv_downloaded_v157 import FakePlex, SHOWS as PLEX_SHOWS, _ep
from tests.tv_fake import fake_tmdb  # noqa: F401

WEEK = '2026-10-11'                                   # Sun Oct 11 .. Sat Oct 17
COPS, TREASURE, HOSTAGE = 9101, 9102, 9103
LANDED = {
    'Cops': 'Cops/Season 38/Cops.S38E16.Breakfast.Gram.1080p.WEB.h264-EDITH.mkv',
    'Treasure Island': 'Treasure Island (2026)/Season 01/Treasure.Island.2026.S01E02.1080p.WEB.h264-ETHEL.mkv',
    'American Hostage': 'American Hostage/Season 01/American.Hostage.S01E06.720p.WEB.h264-SKYFiRE.mkv',
}
WANT = {'Cops': (38, 16), 'Treasure Island': (1, 2), 'American Hostage': (1, 6)}


@pytest.fixture
def ops(aired, lib, monkeypatch):  # noqa: F811
    monkeypatch.setitem(tv_fake.SHOWS, COPS, {'name': 'Cops', 'first_air_date': '1989-03-11', 'seasons': {38: 16},
                                              'air': {(38, 15): '2026-10-09', (38, 16): '2026-10-16'}})
    monkeypatch.setitem(tv_fake.SHOWS, TREASURE, {'name': 'Treasure Island', 'first_air_date': '2026-10-08',
                                                  'seasons': {1: 6}, 'air': {(1, 1): '2026-10-08', (1, 2): '2026-10-15'}})
    # TMDB listed only five episodes at the last refresh; the sixth shows up there after the file lands
    monkeypatch.setitem(tv_fake.SHOWS, HOSTAGE, {'name': 'American Hostage', 'first_air_date': '2026-09-09',
                                                 'seasons': {1: 5}, 'air': {(1, 5): '2026-10-07'}})
    for rel in ('Cops/Season 38/Cops.S38E15.1080p.WEB.h264-EDITH.mkv',
                'Treasure Island (2026)/Season 01/Treasure.Island.2026.S01E01.1080p.WEB.h264-ETHEL.mkv',
                'American Hostage/Season 01/American.Hostage.S01E05.720p.WEB.h264-SKYFiRE.mkv'):
        _drop(lib, rel)
    tv_scan.scan_all(now=1000.0)
    assert {r['folder']: r['tmdb_id'] for r in rows('SELECT folder, tmdb_id FROM series')
            if r['tmdb_id'] in (COPS, TREASURE, HOSTAGE)} == {
        'Cops': COPS, 'Treasure Island (2026)': TREASURE, 'American Hostage': HOSTAGE}
    hostage = dict(tv_fake.SHOWS[HOSTAGE])
    hostage['seasons'] = {1: 6}
    hostage['air'] = {(1, 5): '2026-10-07', (1, 6): '2026-10-14'}
    monkeypatch.setitem(tv_fake.SHOWS, HOSTAGE, hostage)
    return lib


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(tv_scan, 'refresh_on_open', lambda *a, **k: False)
    tv_watch._sigs.clear()
    tv_plex._have.clear(); tv_plex._state['built_at'] = 0.0
    tv_plex._new.clear()
    yield
    tv_watch._sigs.clear(); tv_plex._have.clear(); tv_plex._shows.clear(); tv_plex._new.clear()
    tv_plex._state['built_at'] = 0.0


def _drop(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'v' * 64)
    return p


def _cards(owner):  # noqa: F811
    eps = _get(owner, '/api/settings/tv/calendar', start=WEEK, days='7').get_json()['episodes']
    return {e['show']: e for e in eps if (e['season'], e['episode']) == WANT.get(e['show'])}


def _snapshot(root):
    return {p: (os.stat(p).st_mtime_ns, os.stat(p).st_size) for p in root.rglob('*') if p.is_file()}


def test_cops_shows_on_its_own_air_date(owner, ops):  # noqa: F811
    with tv_scan.tv_store.write() as c:      # the 9 PM ET airing is already Oct 17 in UTC
        c.execute("UPDATE episodes SET airstamp = '2026-10-17T01:00:00Z' WHERE season_number = 38 AND episode_number = 16")
    cops = _cards(owner)['Cops']
    assert cops['air_date'] == '2026-10-16' and cops['downloaded'] is False
    sat = _get(owner, '/api/settings/tv/calendar', start='2026-10-17', days='2').get_json()['episodes']
    assert not [e for e in sat if e['show'] == 'Cops']                              # not on the 17th or the 18th


def test_the_mover_hook_links_all_three(owner, ops):  # noqa: F811
    assert not any(c['downloaded'] for c in _cards(owner).values())
    before = _snapshot(ops)
    for rel in LANDED.values():
        assert tv_watch.link_path(str(_drop(ops, rel))) is True
    cards = _cards(owner)
    assert {s: c['downloaded'] for s, c in cards.items()} == {s: True for s in WANT}, cards
    assert cards['Cops']['air_date'] == '2026-10-16'
    assert all(_snapshot(ops)[p] == v for p, v in before.items())                  # read-only on media


def test_the_poll_links_all_three(owner, ops):  # noqa: F811
    assert tv_watch.poll(prime=True) == 0
    for rel in LANDED.values():
        _drop(ops, rel)
    assert tv_watch.poll() == 3
    assert {s: c['downloaded'] for s, c in _cards(owner).items()} == {s: True for s in WANT}
    linked = {r['rel_path'] for r in rows("SELECT rel_path FROM episode_files WHERE state = 'linked'")}
    assert set(LANDED.values()) <= linked


def test_inotify_links_all_three(owner, ops):  # noqa: F811
    try:
        tv_watch._Inotify().close()
    except (OSError, AttributeError):
        pytest.skip('no inotify here')
    tv_watch.poll(prime=True)
    stop = threading.Event()
    t = threading.Thread(target=tv_watch._inotify_loop, args=(stop,), daemon=True)
    t.start()
    try:
        time.sleep(0.3)
        t0 = time.monotonic()
        for rel in LANDED.values():
            _drop(ops, rel)
        while time.monotonic() - t0 < 10:
            if len(rows("SELECT 1 FROM episode_files WHERE state = 'linked' AND rel_path IN (?, ?, ?)",
                        *LANDED.values())) == 3:
                break
            time.sleep(0.1)
        assert time.monotonic() - t0 < 8
    finally:
        stop.set()
        t.join(timeout=3)
    assert {s: c['downloaded'] for s, c in _cards(owner).items()} == {s: True for s in WANT}


def test_a_file_for_an_episode_tmdb_listed_late_re_reads_the_show_once(owner, ops, fake_tmdb):  # noqa: F811
    assert not rows('SELECT 1 FROM episodes e JOIN series s ON s.id = e.series_id'
                    ' WHERE s.tmdb_id = ? AND e.episode_number = 6', HOSTAGE)     # tv.db hasn't heard of E06 yet
    fake_tmdb.calls.clear()
    assert tv_watch.link_path(str(_drop(ops, LANDED['American Hostage']))) is True
    assert _cards(owner)['American Hostage']['downloaded'] is True
    assert [c for c in fake_tmdb.calls if c[0] == 'show'] == [('show', HOSTAGE)]   # just that show, once
    fake_tmdb.calls.clear()
    assert tv_watch.link_path(str(_drop(ops, LANDED['Cops']))) is True
    assert not [c for c in fake_tmdb.calls if c[0] == 'show']                       # a known episode: no TMDB call


def test_plex_is_the_primary_source_within_a_minute_however_the_file_arrived(owner, ops, monkeypatch):  # noqa: F811
    """Nothing lands in Luma's folders at all (Plex got them from elsewhere): one recently-added tick
    turns all three downloaded, including a show Plex added after its last full set (plex:// guid)."""
    plex = FakePlex(PLEX_SHOWS + [f'<Directory ratingKey="{k}" guid="plex://show/{k}"><Guid id="tmdb://{t}"/></Directory>'
                                  for k, t in ((51, COPS), (52, TREASURE))], [])
    tv_plex.init(get_plex=lambda: plex)
    assert tv_watch.plex_tick() >= 0                                              # boot: the full set (empty)
    assert not any(c['downloaded'] for c in _cards(owner).values())
    plex.shows.append(f'<Directory ratingKey="53" guid="plex://show/53"><Guid id="tmdb://{HOSTAGE}"/></Directory>')
    plex.recent_eps = [_ep(51, 38, 16, 'plex://show/51'), _ep(52, 1, 2, 'plex://show/52'),
                       _ep(53, 1, 6, 'plex://show/53')]
    t0 = time.monotonic()
    assert tv_watch.plex_tick() == 3
    cards = _cards(owner)
    assert {s: c['downloaded'] for s, c in cards.items()} == {s: True for s in WANT}, cards
    assert time.monotonic() - t0 < 1.0 and tv_watch.PLEX_POLL_S <= 60                # a tick, every minute at most
    assert ('/library/metadata/53', {'includeGuids': 1}) in plex.paths             # the new show learned once
    assert tv_watch.plex_tick() == 0


def test_the_import_hook_links_all_three(owner, ops):  # noqa: F811
    for rel in LANDED.values():
        assert tv_watch.episode_imported(str(_drop(ops, rel))) is True
    assert {s: c['downloaded'] for s, c in _cards(owner).items()} == {s: True for s in WANT}
    assert tv_watch.episode_imported(str(ops.parent / 'Movies' / 'x.mkv')) is False


def test_plex_has_its_own_thread(monkeypatch):
    started = []
    monkeypatch.delenv('LUMA_SKIP_BACKGROUND_THREADS', raising=False)
    monkeypatch.setitem(tv_watch._started, 'poll', False)
    monkeypatch.setattr(tv_watch, '_Thread', lambda target, args=(), daemon=True, name='': type(
        'T', (), {'start': lambda s: started.append(name)})())
    assert tv_watch.start() is True
    assert started == ['tv-link-poll', 'tv-plex-poll']
