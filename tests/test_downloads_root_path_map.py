"""PM: LUMA_DOWNLOADS_ROOT can be any path (:5002 uses /storage/downloads while
qBit reports /downloads, so the path map is required). No hardcoded /downloads;
a grab whose save path doesn't map under the root says "Path not mapped"."""
import os
import re
from pathlib import Path

from app import file_management as fm
from app import library_mover as lm
from app import storage_cleanup
import app.main as m  # noqa: E402  (import before the env fixture points fm at a temp store)
from tests.test_library_mover import (env, add_path, torrent, movie_file, job, TMDB, H1, H2)  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _qbit_says_downloads(t, rel):
    t['save_path'] = '/downloads/'
    t['content_path'] = '/downloads/' + rel
    return t


def test_any_root_with_the_path_map_files_the_grab(env, tmp_path, monkeypatch):
    storage = tmp_path / 'storage' / 'downloads'
    os.rename(env.dl, storage) if storage.parent.mkdir(parents=True) is None else None
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(storage))
    env.dl = storage
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    _qbit_says_downloads(torrent(env, H1, 'Heat.1995.mkv'), 'Heat.1995.mkv')
    fm.update(lambda d: d.__setitem__('path_map', [{'remote': '/downloads', 'local': str(storage)}]))
    env.tick(now=1000)
    assert job(H1)['state'] == 'done' and job(H1)['status'] == ''
    assert (env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv').read_bytes() == b'x' * 200


def test_without_the_map_it_says_path_not_mapped_at_once(env, tmp_path, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    src = movie_file(env, 'Heat.1995.mkv', b'x' * 200)
    lm.track(H1, TMDB, 'Heat.1995')
    _qbit_says_downloads(torrent(env, H1, 'Heat.1995.mkv'), 'Heat.1995.mkv')
    env.tick(now=1000)
    assert job(H1)['state'] == 'waiting' and job(H1)['status'] == lm.PATH_NOT_MAPPED == 'path not mapped'
    assert fm.view()['not_found'] == [{'title': 'Heat', 'since': 1000, 'reason': 'not_mapped'}]
    assert src.read_bytes() == b'x' * 200 and not any(os.scandir(env.media / 'Movies'))
    # the map arrives: filed next pass, flag cleared
    fm.update(lambda d: d.__setitem__('path_map', [{'remote': '/downloads', 'local': str(env.dl)}]))
    env.tick(now=1030)
    assert job(H1)['state'] == 'done' and job(H1)['status'] == '' and fm.view()['not_found'] == []


def test_not_mapped_then_files_missing_restarts_the_hour(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    t = _qbit_says_downloads(torrent(env, H1, 'Heat.1995.mkv'), 'Heat.1995.mkv')
    env.tick(now=1000)
    assert job(H1)['status'] == lm.PATH_NOT_MAPPED
    t['save_path'], t['content_path'] = str(env.dl) + '/', str(env.dl / 'gone' / 'Heat.1995.mkv')
    env.tick(now=2000)                                  # mapped now, files not there yet
    assert job(H1)['status'] == '' and job(H1)['pending_since'] == 2000
    env.tick(now=2000 + lm.NOT_FOUND_AFTER_S)
    assert job(H1)['status'] == lm.FILES_NOT_FOUND


def test_storage_cleanup_uses_the_same_root(monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(tmp_path))
    assert storage_cleanup.downloads_root() == fm.downloads_root() == os.path.realpath(tmp_path)


def test_no_hardcoded_slash_downloads_left():
    """Only the one default (fm.DEFAULT_DOWNLOADS_ROOT, plus Config's env
    default and the settings schema default) names /downloads. The shared
    app/auto_delete_guard.py (byte-identical with prod-destructive-guard
    63ea469) compares the stored value as saved, with no fallback."""
    allowed = {'app/file_management.py': 1, 'app/config.py': 1, 'app/settings_manager.py': 1}
    pat = re.compile(r"""['"]/downloads/?['"]""")
    for p in sorted((ROOT / 'app').glob('*.py')):
        rel = p.relative_to(ROOT).as_posix()
        n = len(pat.findall(p.read_text()))
        assert n == allowed.get(rel, 0), (rel, n)


# ------------------------------------------------------------- Activity

def test_activity_row_says_not_mapped(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    lm.track(H2, dict(TMDB, tmdb_id=1), 'Other.2000')
    _qbit_says_downloads(torrent(env, H1, 'Heat.1995.mkv'), 'Heat.1995.mkv')
    env.tick(now=1000)
    downloads = [{'hash': 'f' * 40, 'status': 'downloading', 'title': 'Busy'}]
    torrents = [{'hash': H1.upper(), 'name': 'Heat.1995.mkv', 'total_size': 9, 'tracker': ''}]
    m._mark_not_mapped(downloads, torrents, {}, set())
    assert downloads[0]['status'] == 'downloading'
    row = downloads[1]
    assert row['hash'] == H1 and row['status'] == 'not_mapped' and row['title'] == 'Heat'
    assert row['progress'] == 1.0 and row['size_total'] == 9
    assert len(downloads) == 2                          # H2 isn't flagged


def test_activity_existing_row_is_relabeled(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    _qbit_says_downloads(torrent(env, H1, 'Heat.1995.mkv'), 'Heat.1995.mkv')
    env.tick(now=1000)
    downloads = [{'hash': H1, 'status': 'importing', 'title': 'Heat'}]
    m._mark_not_mapped(downloads, [], {}, set())
    assert downloads == [{'hash': H1, 'status': 'not_mapped', 'state': 'not_mapped', 'title': 'Heat'}]
