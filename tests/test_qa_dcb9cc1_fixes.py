"""Tester dcb9cc1 M17, M18: Wrong show? onto a show another folder holds,
and scans after a manual pick, never delete a show or its history and never
undo the owner's pick."""
import json

import pytest

from app import tv_scan, tv_store
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


def test_m18_scans_keep_the_owners_manual_pick(scanned):
    sev, dark = _sid('Severance (2022)'), _sid('Dark [tmdbid-70523]')
    tv_scan.rematch(sev, 70523, now=2000.0)
    for t, retry in ((3000.0, True), (4000.0, False), (5000.0, True)):
        tv_scan.scan_all(now=t, retry_unmatched=retry)
        assert rows('SELECT folder FROM series WHERE id = ?', dark) == [{'folder': 'Severance (2022)'}]
        assert rows("SELECT reason FROM unmatched WHERE folder = 'Dark [tmdbid-70523]'") == [{'reason': 'duplicate'}]
    assert rows('SELECT path_id FROM series WHERE id = ?', sev) == [{'path_id': None}]
    assert [e for _, e in _history(sev)].count('detached') == 1


def test_m18_a_manual_match_of_an_unmatched_folder_also_sticks(scanned):
    dark = _sid('Dark [tmdbid-70523]')
    uid = rows("SELECT id FROM unmatched WHERE folder = 'Mystery Show'")[0]['id']
    assert tv_scan.match_unmatched(uid, 70523, now=2000.0)['series_id'] == dark
    tv_scan.scan_all(now=3000.0)
    assert rows('SELECT folder FROM series WHERE id = ?', dark) == [{'folder': 'Mystery Show'}]


def test_m18_without_a_manual_pick_the_better_folder_still_wins(scanned):
    # an automatic duplicate is still settled by folder_rank (unchanged)
    dark = _sid('Dark [tmdbid-70523]')
    with tv_store.write() as conn:
        conn.execute("UPDATE history SET detail = json_set(detail, '$.via', 'scan') WHERE series_id = ?", (dark,))
    owner = rows('SELECT * FROM series WHERE id = ?', dark)[0]
    with tv_store.connect() as conn:
        assert tv_scan._manual_here(conn, owner) is False
