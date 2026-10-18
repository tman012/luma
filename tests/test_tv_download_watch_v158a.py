"""v158a TV download watch: only luma-test-tv torrents, through the mover's transfer
(hardlink/copy, never overwrite, never delete), linked in tv.db on the same pass."""
import datetime as dt
import os
import time

import pytest

from app import instance, library_mover as lm, tv_calendar, tv_downloads, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_library_mover import Resp, Settings
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

H1, H2, H3 = 'a' * 40, 'b' * 40, 'c' * 40


class Q:
    def __init__(self):
        self.torrents, self.calls = [], []

    def factory(self):
        q = self

        class S:
            def post(self, url, data=None, timeout=None, **kw):
                q.calls.append(('POST', url, data))
                return Resp()

            def get(self, url, params=None, timeout=None, **kw):
                q.calls.append(('GET', url, dict(params or {})))
                assert url.endswith(('/api/v2/torrents/info', '/api/v2/torrents/files')), url
                return Resp(data=list(q.torrents))         # a misbehaving qBit: answers everything

            def close(self):
                pass
        return S()


@pytest.fixture
def dl(scanned, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(lm, 'STABLE_WAIT_S', 0)
    monkeypatch.setattr(lm, 'MIN_VIDEO_BYTES', 10)
    monkeypatch.delenv('LUMA_TEST_TV_TAG', raising=False)
    lm.init(settings=Settings())
    root = tmp_path / 'downloads'
    (root / 'incomplete').mkdir(parents=True)
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(root))
    q = Q()

    class E:
        pass
    e = E()
    e.root, e.q, e.lib = root, q, scanned
    e.tick = lambda now=None: tv_downloads.tick(now=now, session_factory=q.factory)
    return e


def grab(e, h, name, files, tags='luma-test-tv', progress=1.0):
    d = e.root / name
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / f).write_bytes(b'episode-bytes' * 10)
    e.q.torrents.append({'hash': h, 'name': name, 'tags': tags, 'category': 'tv', 'progress': progress,
                         'save_path': str(e.root) + '/', 'content_path': str(d), 'added_on': time.time() - 100,
                         'completion_on': int(time.time())})
    return d


def _down(season, episode):
    out = tv_calendar.calendar(dt.date(2026, 10, 11), 7)['episodes']
    return next(x['downloaded'] for x in out if x['show'] == 'Severance' and (x['season'], x['episode']) == (season, episode))


def test_imports_only_the_tv_tag_and_links_the_card_on_the_same_pass(dl):
    src = grab(dl, H1, 'Severance.S02E03.1080p.WEB-DL.DDP5.1-GRP', ['Severance.S02E03.1080p.WEB-DL.DDP5.1-GRP.mkv'])
    other = grab(dl, H2, 'Severance.S02E04.1080p.WEB-DL-GRP', ['Severance.S02E04.1080p.WEB-DL-GRP.mkv'], tags='luma-test')
    prod = grab(dl, H3, 'Severance.S02E06.1080p.WEB-DL-GRP', ['Severance.S02E06.1080p.WEB-DL-GRP.mkv'], tags='sonarr')
    assert _down(2, 3) is False
    before_src = {p: p.stat().st_mtime_ns for d in (src, other, prod) for p in d.iterdir()}
    out = dl.tick()
    assert out == {'checked': 1, 'imported': 1}
    dest = dl.lib / 'Severance (2022)' / 'Season 02' / 'Severance.S02E03.1080p.WEB-DL.DDP5.1-GRP.mkv'
    assert dest.is_file()
    assert _down(2, 3) is True and _down(2, 4) is False and _down(2, 6) is False
    assert {p: p.stat().st_mtime_ns for d in (src, other, prod) for p in d.iterdir()} == before_src   # sources untouched
    assert not (dl.lib / 'Severance (2022)' / 'Season 02' / 'Severance.S02E04.1080p.WEB-DL-GRP.mkv').exists()
    assert all(m == 'GET' or u.endswith('/api/v2/auth/login') for m, u, _ in dl.q.calls)    # reads only (+ sign-in)
    assert [c[2] for c in dl.q.calls if c[1].endswith('/torrents/info')] == [{'tag': 'luma-test-tv', 'filter': 'completed'}]
    st = tv_downloads.status(H1)
    assert st['state'] == 'imported' and st['files'][0]['linked'] is True
    assert tv_downloads.status(H2) is None and tv_downloads.status(H3) is None
    assert dl.tick() == {'checked': 0, 'imported': 0}                                   # once


def test_never_overwrites_and_waits_while_incomplete_or_unwritable(dl, monkeypatch):
    season = dl.lib / 'Severance (2022)' / 'Season 02'
    season.mkdir(parents=True, exist_ok=True)
    keep = season / 'Severance.S02E03.720p.HDTV-OLD.mkv'
    grab(dl, H1, 'Severance.S02E03.720p.HDTV-OLD', [keep.name], progress=0.5)
    keep.write_bytes(b'already-here')
    assert dl.tick()['checked'] == 0                                                     # not complete
    dl.q.torrents[0]['progress'] = 1.0
    real_access = os.access
    monkeypatch.setattr(tv_downloads.os, 'access', lambda p, m: False if os.path.realpath(p) == str(dl.lib.resolve()) else real_access(p, m))
    dl.tick()
    assert tv_downloads.status(H1)['state'] == 'waiting' and tv_downloads.status(H1)['error'] == tv_downloads.NOT_WRITABLE
    monkeypatch.setattr(tv_downloads.os, 'access', real_access)
    dl.tick()
    assert keep.read_bytes() == b'already-here'                                          # never overwritten
    assert tv_downloads.status(H1)['files'][0]['how'] == 'exists'


def test_off_on_test_unless_allowed_by_default_and_unknown_shows_wait(dl, monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    assert instance.writes_allowed('tv_import')                                          # allowed by default (tag-scoped)
    grab(dl, H1, 'Nobody.Knows.This.S01E01.1080p.WEB-DL-GRP', ['Nobody.Knows.This.S01E01.1080p.WEB-DL-GRP.mkv'])
    dl.tick()
    assert tv_downloads.status(H1)['state'] == 'waiting' and tv_downloads.status(H1)['error'] == tv_downloads.NO_SHOW
    monkeypatch.setattr(instance, 'TEST_DEFAULT_ALLOWED', frozenset({'mover'}))
    dl.q.calls.clear()
    assert dl.tick() == {'checked': 0, 'imported': 0} and dl.q.calls == []


def test_the_tv_tag_can_never_be_the_movie_tag_or_an_app_category(monkeypatch):
    for bad in ('luma-test', 'sonarr', 'tv', 'bad,tag', ''):
        monkeypatch.setenv('LUMA_TEST_TV_TAG', bad)
        assert tv_downloads.tag_name() == 'luma-test-tv'
    monkeypatch.setenv('LUMA_TEST_TV_TAG', 'my-tv')
    assert tv_downloads.tag_name() == 'my-tv'
