"""Tester dcb9cc1 M17: Wrong show? onto a show another folder holds never
deletes this folder's show or its history; it is hidden until a folder
takes it back."""
import json

import pytest

from app import tv_scan
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.tv_tree import snapshot


def _sid(folder):
    return rows('SELECT id FROM series WHERE folder = ?', folder)[0]['id']


def _history(sid):
    return [(r['id'], r['event']) for r in rows('SELECT id, event FROM history WHERE series_id = ? ORDER BY id', sid)]


def _visible():
    return {s['folder']: s['tmdb_id'] for s in tv_scan.library_view()['shows']}


@pytest.fixture
def scanned(lib):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    return lib


def test_m17_wrong_show_onto_a_held_show_keeps_this_folders_show_and_history(scanned):
    sev, dark = _sid('Severance (2022)'), _sid('Dark [tmdbid-70523]')
    before = _history(sev)
    assert before
    files_before = snapshot(scanned)
    out = tv_scan.rematch(sev, 70523, now=2000.0)
    assert out == {'ok': True, 'series_id': dark}
    # Severance's row, episodes, settings and history are all still there, hidden
    s = rows('SELECT * FROM series WHERE id = ?', sev)[0]
    assert (s['tmdb_id'], s['path_id'], s['folder']) == (95396, None, None)
    assert rows('SELECT COUNT(*) AS n FROM episodes WHERE series_id = ?', sev)[0]['n'] > 0
    assert _history(sev)[:len(before)] == before and _history(sev)[-1][1] == 'detached'
    # the other folder is the duplicate; the picked show is here
    assert _visible()['Severance (2022)'] == 70523
    assert 'Dark [tmdbid-70523]' not in _visible()
    un = rows("SELECT reason FROM unmatched WHERE folder = 'Dark [tmdbid-70523]'")
    assert un == [{'reason': 'duplicate'}]
    assert snapshot(scanned) == files_before          # rows only


def test_m17_a_hidden_show_comes_back_with_its_history(scanned):
    sev = _sid('Severance (2022)')
    tv_scan.rematch(sev, 70523, now=2000.0)
    before = _history(sev)
    # Wrong show? back to Severance: the hidden row returns, history and all
    out = tv_scan.rematch(_sid('Severance (2022)'), 95396, now=3000.0)
    assert out['ok'] and out['series_id'] == sev
    assert _history(sev)[:len(before)] == before
    assert _visible()['Severance (2022)'] == 95396
