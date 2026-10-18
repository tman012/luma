"""M33 (v167): deselecting a library and rescanning never moves its shows to a duplicate folder in
another library; reselecting brings them back as they were. Removing the Library path still lets
the other folder take the show (unchanged). M34 lives below (v168)."""
import json
import os

from app import file_management as fm, tv_scan, tv_settings
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

PID2 = 'p_0000bbbb'


def _second(tv_env):  # noqa: F811
    root = tv_env / 'TV2'
    (root / 'Severance' / 'Season 01').mkdir(parents=True)
    for e in (1, 2, 3):
        (root / 'Severance' / 'Season 01' / f'Severance.S01E0{e}.mkv').write_bytes(b'x')
    select(PID2, root, name='TV2')
    return root


def _deselect(pid):
    st = tv_settings.load()
    st['libraries'] = [i for i in st['libraries'] if i != pid]
    tv_settings.save(st)


def _severance():
    return rows('SELECT id, path_id, folder FROM series WHERE tmdb_id = 95396')


def test_deselecting_a_library_never_moves_its_duplicate_shows(lib, tv_env):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before = _severance()
    assert [(r['path_id'], r['folder']) for r in before] == [(PID, 'Severance (2022)')]
    files_before = rows('SELECT id, rel_path FROM episode_files WHERE series_id = ? ORDER BY id', before[0]['id'])
    hist_before = rows('SELECT * FROM history WHERE series_id = ? ORDER BY id', before[0]['id'])
    _second(tv_env)
    _deselect(PID)
    for now in (2000.0, 3000.0):                                  # rescans, twice
        tv_scan.scan_all(now=now)
        assert _severance() == before                             # same id, library and folder
    un = {r['folder']: r for r in rows('SELECT * FROM unmatched WHERE path_id = ?', PID2)}
    assert un['Severance']['reason'] == 'duplicate'
    assert [c['tmdb_id'] for c in json.loads(un['Severance']['candidates'])] == [95396]
    assert rows('SELECT id, rel_path FROM episode_files WHERE series_id = ? ORDER BY id', before[0]['id']) == files_before
    assert rows('SELECT * FROM history WHERE series_id = ? ORDER BY id', before[0]['id']) == hist_before
    select(PID, lib)                                              # reselect: it's back as it was
    tv_scan.scan_all(now=4000.0)
    assert _severance() == before
    assert any(s['id'] == before[0]['id'] for s in tv_scan.library_view()['shows'])


def test_removing_the_library_path_still_lets_the_other_folder_take_it(lib, tv_env):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    sid = _severance()[0]['id']
    _second(tv_env)
    _deselect(PID)
    data = fm.load()
    data['paths'] = [p for p in data['paths'] if p['id'] != PID]
    fm.save(data)
    tv_scan.scan_all(now=2000.0)
    assert [(r['id'], r['path_id'], r['folder']) for r in _severance()] == [(sid, PID2, 'Severance')]


def test_is_library_path():
    assert not tv_scan._is_library_path('p_nothere')
