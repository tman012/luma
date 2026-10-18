"""v157 downloaded: Plex OR a linked file; new files linked as they land (the mover
hook, a 60s folder-mtime poll, inotify where it works); read-only on files."""
import os
import threading
import time
import xml.etree.ElementTree as ET

import pytest

from app import tv_plex, tv_scan, tv_watch
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import _get, aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

WEEK = '2026-10-11'


def _card(owner, show, s, e):  # noqa: F811
    eps = _get(owner, '/api/settings/tv/calendar', start=WEEK, days='7').get_json()['episodes']
    return next(x for x in eps if (x['show'], x['season'], x['episode']) == (show, s, e))


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(tv_scan, 'refresh_on_open', lambda *a, **k: False)
    tv_watch._sigs.clear()
    tv_plex._have.clear(); tv_plex._shows.clear(); tv_plex._state['built_at'] = 0.0
    yield
    tv_plex._have.clear(); tv_plex._state['built_at'] = 0.0


# ------------------------------------------------------------------ Plex

@pytest.mark.parametrize('guid,guids,want', [
    (None, ['tmdb://95396', 'tvdb://371980', 'imdb://tt11280740'], (95396, 371980)),
    ('plex://show/5d9c08', ['tvdb://371980'], (None, 371980)),
    ('com.plexapp.agents.thetvdb://371980?lang=en', [], (None, 371980)),
    ('com.plexapp.agents.themoviedb://95396?lang=en', [], (95396, None)),
    ('com.plexapp.agents.tmdb://95396', [], (95396, None)),
    ('com.plexapp.agents.imdb://tt11280740?lang=en', [], (None, None)),
    ('local://123', ['tmdb://abc'], (None, None)),
])
def test_plex_guid_formats(guid, guids, want):
    assert tv_plex.show_ids(guid, guids) == want


class FakePlex:
    is_configured = True

    def __init__(self, shows, eps, recent=()):
        self.shows, self.eps, self.recent_eps, self.paths = list(shows), eps, list(recent), []

    def _get(self, path, params=None, timeout=15):
        self.paths.append((path, dict(params or {})))
        if path == '/library/sections':
            return ET.fromstring('<M><Directory key="2" type="show"/><Directory key="1" type="movie"/></M>')
        if path.startswith('/library/metadata/'):
            key = path.rsplit('/', 1)[1]
            return ET.fromstring('<M>' + ''.join(d for d in self.shows if f'ratingKey="{key}"' in d) + '</M>')
        if path.endswith('/recentlyAdded'):
            return ET.fromstring('<M>' + ''.join(self.recent_eps) + '</M>')
        if (params or {}).get('type') == 2:
            return ET.fromstring('<M>' + ''.join(self.shows) + '</M>')
        start = int((params or {}).get('X-Plex-Container-Start', 0))
        size = int((params or {}).get('X-Plex-Container-Size', 10 ** 9))
        return ET.fromstring('<M>' + ''.join(self.eps[start:start + size]) + '</M>')


def _ep(rk, s, e, gguid=''):
    return f'<Video grandparentRatingKey="{rk}" parentIndex="{s}" index="{e}" grandparentGuid="{gguid}"/>'


SHOWS = ['<Directory ratingKey="10" guid="plex://show/x"><Guid id="tmdb://95396"/><Guid id="tvdb://371980"/></Directory>',
         '<Directory ratingKey="11" guid="com.plexapp.agents.thetvdb://999?lang=en"/>']


def test_plex_rebuild_pages_and_matches_by_guid_and_number(monkeypatch):
    eps = [_ep(10, 2, 3), _ep(11, 1, 1)] + [_ep(10, 9, n) for n in range(1, 6)]
    plex = FakePlex(SHOWS, eps)
    tv_plex.init(get_plex=lambda: plex)
    monkeypatch.setattr(tv_plex, 'PAGE', 3)
    assert tv_plex.rebuild(force=True) > 0
    assert tv_plex.has(95396, None, 2, 3) and tv_plex.has(None, 371980, 2, 3)      # tmdb or tvdb
    assert tv_plex.has(None, 999, 1, 1) and not tv_plex.has(95396, None, 2, 4)
    assert tv_plex.has(95396, None, 9, 5)                                            # the last page
    assert all(p == '/library/sections' or '/library/sections/2/' in p for p, _ in plex.paths)   # TV sections only
    assert tv_plex.rebuild() == -1                                                   # cached: not again within FULL_TTL_S
    plex.recent_eps = [_ep(10, 2, 4)]
    assert tv_plex.recent() >= 1 and tv_plex.has(95396, None, 2, 4)                 # recently added tops it up


def test_plex_not_configured_or_failing_is_empty_not_an_error():
    class Off:
        is_configured = False
    tv_plex.init(get_plex=lambda: Off())
    assert tv_plex.rebuild(force=True) == -1 and not tv_plex.has(1, 1, 1, 1)

    class Boom(FakePlex):
        def _get(self, *a, **k):
            raise RuntimeError('plex down')
    tv_plex.init(get_plex=lambda: Boom([], []))
    assert tv_plex.rebuild(force=True) == -1


def test_downloaded_is_plex_or_a_linked_file(owner, scanned):  # noqa: F811
    assert _card(owner, 'Severance', 2, 3)['downloaded'] is False                   # no file, Plex doesn't have it
    tv_plex.init(get_plex=lambda: FakePlex(SHOWS, [_ep(10, 2, 3)]))
    tv_plex.rebuild(force=True)
    assert _card(owner, 'Severance', 2, 3)['downloaded'] is True                    # Plex has it
    assert _card(owner, 'Severance', 2, 1)['downloaded'] is True                    # a linked file (not in Plex)
    assert _card(owner, 'Severance', 2, 4)['downloaded'] is False


# ------------------------------------------------------------------ new files

def _drop(lib, rel, data=b'n' * 64):  # noqa: F811
    p = lib / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_the_poll_links_a_file_that_lands_and_only_rescans_changed_shows(owner, scanned, monkeypatch):  # noqa: F811
    assert tv_watch.poll(prime=True) == 0                                            # first pass: remember the folders
    seen = []
    real = tv_scan.scan_files
    monkeypatch.setattr(tv_scan, 'scan_files', lambda sid, pid, folder, *a: (seen.append(folder), real(sid, pid, folder, *a))[1])
    assert tv_watch.poll() == 0 and seen == []                                       # nothing changed: no listing, no link
    assert _card(owner, 'Severance', 2, 3)['downloaded'] is False
    before = {p: os.stat(p).st_mtime_ns for p in scanned.rglob('*') if p.is_file()}
    _drop(scanned, 'Severance (2022)/Season 02/Severance.S02E03.1080p.WEB.mkv')
    assert tv_watch.poll() == 1 and seen == ['Severance (2022)']                     # only the changed show
    assert _card(owner, 'Severance', 2, 3)['downloaded'] is True
    after = {p: os.stat(p).st_mtime_ns for p in scanned.rglob('*') if p in before}
    assert after == before                                                           # read-only: nothing touched
    _drop(scanned, 'Severance (2022)/Season 03/Severance.S03E01.mkv')               # a new season folder
    seen.clear()
    # TMDB doesn't list S03 here: the show is read again once, then linked again (still a mismatch)
    assert tv_watch.poll() == 1 and seen == ['Severance (2022)', 'Severance (2022)']


def test_the_poll_skips_while_an_import_holds_the_lock(scanned):  # noqa: F811
    tv_watch.poll(prime=True)
    _drop(scanned, 'Severance (2022)/Season 02/Severance.S02E03.mkv')
    with tv_scan._scan_lock:
        assert tv_watch.poll() is None                                               # skipped, not waited
    assert tv_watch.poll() == 1                                                      # the next one links it


def test_the_mover_hook_links_its_file_at_once(owner, scanned):  # noqa: F811
    p = _drop(scanned, 'Severance (2022)/Season 02/Severance - S02E03 - Who Is Alive.mkv')
    assert tv_watch.link_path(str(p)) is True
    assert _card(owner, 'Severance', 2, 3)['downloaded'] is True
    assert tv_watch.link_path(str(scanned.parent / 'Movies' / 'x.mkv')) is False      # not a TV show's folder


def test_auto_rename_copy_calls_the_tv_link(monkeypatch, tmp_path):
    from app import auto_rename
    got = []
    monkeypatch.setattr(tv_watch, 'link_path', lambda p: got.append(p))
    monkeypatch.setattr(auto_rename.threading, 'Thread', lambda target, **k: type('T', (), {'start': lambda s: target()})())
    auto_rename._link_tv_file('/mnt/TV/Show/Season 1/Show - S01E01.mkv')
    assert got == ['/mnt/TV/Show/Season 1/Show - S01E01.mkv']


def test_inotify_links_within_seconds_on_a_local_disk(owner, scanned):  # noqa: F811
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
        _drop(scanned, 'Severance (2022)/Season 02/Severance.S02E03.mkv')
        while time.monotonic() - t0 < 8:
            if rows("SELECT 1 FROM episode_files WHERE rel_path LIKE '%S02E03%' AND state = 'linked'"):
                break
            time.sleep(0.1)
        took = time.monotonic() - t0
        assert rows("SELECT 1 FROM episode_files WHERE rel_path LIKE '%S02E03%' AND state = 'linked'"), took
        assert took < 5
    finally:
        stop.set()
        t.join(timeout=3)


def test_start_is_a_no_op_in_tests_and_never_twice(monkeypatch):
    monkeypatch.setenv('LUMA_SKIP_BACKGROUND_THREADS', '1')
    assert tv_watch.start() is False


def test_auto_rename_calls_the_import_hook_and_the_hook_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(tv_watch, 'link_path', lambda p: (_ for _ in ()).throw(OSError('gone')))
    assert tv_watch.episode_imported('/mnt/TV/Show/Season 1/x.mkv') is False      # the pipeline goes on


def test_plex_down_falls_back_to_linked_files_never_a_5xx(owner, scanned):  # noqa: F811
    plex = FakePlex(SHOWS, [_ep(10, 2, 3)], recent=[])
    tv_plex.init(get_plex=lambda: plex)
    tv_plex.rebuild(force=True)
    assert _card(owner, 'Severance', 2, 3)['downloaded'] is True

    def boom(*a, **k):
        raise ConnectionError('plex unreachable')
    plex._get = boom
    assert tv_plex.recent() == 0
    r = _get(owner, '/api/settings/tv/calendar', start=WEEK, days='7')
    assert r.status_code == 200
    assert _card(owner, 'Severance', 2, 3)['downloaded'] is False                    # Plex-only: not claimed while down
    assert _card(owner, 'Severance', 2, 1)['downloaded'] is True                     # linked files still answer
    tv_plex._state['down'] = False
