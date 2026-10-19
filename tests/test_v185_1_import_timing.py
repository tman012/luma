"""v185.1 (Ops/Tester, live AHS S08 import on the test instance): the grab row read 'imported' at
12:30:39 AM ET (the pass's start) although the mover's last copy landed at 12:45:19 AM ET. The grab is
marked imported (finished_at and the 'imported' history event) only once every file is copied or
renamed into place, with that time; while copying it stays 'downloading'; a failed copy leaves it
open and retryable."""
import json

from app import library_mover as mover, tv_downloads
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_download_watch_v158a import H1, dl, grab  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_v184_grab_import import NAME, _events, _grab_row, _release_names, _row, time_show  # noqa: F401


def test_the_grab_is_stamped_when_the_last_copy_lands_and_is_open_while_copying(dl, time_show, monkeypatch):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    grab(dl, H1, NAME, [NAME + '.mkv'])
    start, clock = 1000.0, {'t': 1000.0}
    seen = []
    real = mover.transfer

    def slow(*a, **k):                                   # a 15-minute copy; the row is looked at mid-copy
        seen.append((_row(H1)['state'], _row(H1)['finished_at'], len(_events(H1))))
        clock['t'] += 880.0
        return real(*a, **k)
    monkeypatch.setattr(mover, 'transfer', slow)
    out = tv_downloads.tick(now=start, session_factory=dl.q.factory, clock=lambda: clock['t'])
    assert out == {'checked': 1, 'imported': 1}
    assert len(seen) == 1 and seen[0][0] in (None, 'downloading')
    assert seen[0][1:] == (None, 0)                      # mid-copy: no finished_at, no 'imported' event
    r = _row(H1)
    assert (r['state'], r['finished_at']) == ('imported', 1880.0)
    ev = _events(H1)
    assert len(ev) == 1 and ev[0]['ts'] == 1880.0
    assert tv_downloads.status(H1)['at'] == 1880.0


def test_a_failed_copy_leaves_the_grab_open_and_retryable(dl, time_show, monkeypatch):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    grab(dl, H1, NAME, [NAME + '.mkv'])

    def broken(*a, **k):
        raise OSError(5, 'Input/output error')
    monkeypatch.setattr(mover, 'transfer', broken)
    assert tv_downloads.tick(now=2000.0, session_factory=dl.q.factory)['imported'] == 0
    r = _row(H1)
    assert r['state'] in (None, 'downloading') and r['finished_at'] is None and _events(H1) == []
    assert tv_downloads.status(H1)['state'] == 'waiting'          # the OSError backoff retries it
