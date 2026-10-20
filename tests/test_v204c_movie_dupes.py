"""v204c (PM): pre-grab duplicate guard for movies (library folders in every routed root, and
qBittorrent's torrents in every category), failing closed when qBittorrent can't be asked."""
import logging
import os

import requests

from app import movie_dupes, movie_import
from tests.test_v203_movie_scanner import MOVIE, NOW, _grabs, mv, rel, sc  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_v158c import guard  # noqa: F401


class R:
    def __init__(self, status=200, data=None):
        self.status_code, self._d, self.text = status, data, 'Ok.'
        self.headers, self.cookies = {}, {}

    def json(self):
        return self._d


def qbit(torrents=None, fail=False):
    calls = []

    def factory():
        class S:
            def get(self, url, *a, **k):
                calls.append((url, k.get('params')))
                if fail:
                    raise requests.ConnectionError('down')
                return R(data=list(torrents or []))

            def post(self, url, *a, **k):
                return R()

            def close(self):
                pass
        return S()
    return factory, calls


def _layout(tmp_path, monkeypatch):
    video = tmp_path / 'Video'
    for v in movie_import.ROUTE_FOLDERS.values():
        (video / v).mkdir(parents=True)
    monkeypatch.setattr(movie_import, 'root', lambda: str(video / 'Recently Released'))
    return video


def test_on_disk_in_any_routed_root_needs_a_video_file(tmp_path, monkeypatch, mv):  # noqa: F811
    video = _layout(tmp_path, monkeypatch)
    assert movie_dupes.on_disk('Dune: Part Two', 2024) is None
    d = video / 'Full Rips' / 'Dune- Part Two (2024)'
    d.mkdir()
    (d / 'notes.nfo').write_text('x')
    assert movie_dupes.on_disk('Dune: Part Two', 2024) is None              # a folder with no video file
    (d / 'Dune- Part Two (2024).mkv').write_bytes(b'0')
    assert movie_dupes.on_disk('Dune: Part Two', 2024) == str(d)
    assert movie_dupes.on_disk('Dune: Part Two', 2021) is None


def test_qbit_match_by_title_and_year_any_category(mv, monkeypatch):  # noqa: F811
    f, calls = qbit([{'name': 'Dune.Part.Two.2024.2160p.UHD.BluRay.x265-GRP', 'category': 'radarr'}])
    assert movie_dupes.in_qbit('Dune: Part Two', 2024, f, NOW) == {'hit': 'Dune.Part.Two.2024.2160p.UHD.BluRay.x265-GRP'}
    assert calls and all(p is None for _, p in calls if _.endswith('/torrents/info'))      # every category
    assert movie_dupes.in_qbit('Dune: Part Two', 2021, f, NOW) == {'hit': None}
    assert movie_dupes.in_qbit('Dune', 2024, f, NOW) == {'hit': None}


def test_qbit_unreachable_fails_closed(mv, caplog):  # noqa: F811
    f, _ = qbit(fail=True)
    assert movie_dupes.in_qbit('Dune: Part Two', 2024, f, NOW) is None
    with caplog.at_level(logging.INFO, logger='app.movie_dupes'):
        assert movie_dupes.check('Dune: Part Two', 2024, f, NOW) == movie_dupes.NO_QBIT
    assert any('skipped' in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


def test_scanner_grab_skips_a_duplicate_and_sends_nothing(tmp_path, monkeypatch, mv, caplog):  # noqa: F811
    video = _layout(tmp_path, monkeypatch)
    d = video / 'Movies' / 'Dune- Part Two (2024)'
    d.mkdir()
    (d / 'Dune- Part Two (2024).mkv').write_bytes(b'0')
    with caplog.at_level(logging.INFO, logger='app.movie_dupes'):
        g = sc.grab(rel('Dune.Part.Two.2024.1080p.WEB-DL-GRP'), MOVIE['tmdb_id'], 'auto', NOW)
    assert g['ok'] is False and g['duplicate'] is True and g['error'] == movie_dupes.ON_DISK
    assert not [c for c in mv.q.calls if c[1] == 'torrents/add'] and _grabs() == []
    assert any('already in the library' in r.getMessage() for r in caplog.records)
    os.remove(d / 'Dune- Part Two (2024).mkv')
    monkeypatch.setattr(movie_dupes, 'in_qbit', lambda *a, **k: {'hit': 'Dune.Part.Two.2024.REMUX'})
    g = sc.grab(rel('Dune.Part.Two.2024.1080p.WEB-DL-GRP'), MOVIE['tmdb_id'], 'manual', NOW)
    assert g['error'] == movie_dupes.IN_QBIT and _grabs() == []
    monkeypatch.setattr(movie_dupes, 'in_qbit', lambda *a, **k: {'hit': None})
    assert sc.grab(rel('Dune.Part.Two.2024.1080p.WEB-DL-GRP'), MOVIE['tmdb_id'], 'auto', NOW)['ok'] is True
