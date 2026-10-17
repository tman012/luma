"""Tester tv1-8646479 M1/M2: two folders with the same show. The candidate shown
is the show's real TMDB title; the better folder keeps the show (exact title and
year, then most linked files, then naming); Match / Wrong show? never dead-end."""
import json
import shutil

from app import tv_scan
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_library_paths import tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.tv_tree import snapshot

SEV = 95396
JUNK = 'Sev.Complete.1080p.WEB-GRP [tmdbid-95396]'


def _owner():
    return [r['folder'] for r in rows('SELECT folder FROM series WHERE tmdb_id = ?', SEV)]


def _dup(folder):
    r = rows('SELECT * FROM unmatched WHERE folder = ?', folder)
    assert r and r[0]['reason'] == 'duplicate', r
    return json.loads(r[0]['candidates'])


def _files(root, folder, eps):
    (root / folder).mkdir(exist_ok=True)
    for e in eps:
        (root / folder / f'Severance.S01E0{e}.mkv').write_bytes(b'x' * 16)


def test_candidate_is_the_real_tmdb_title(lib):
    _files(lib, JUNK, [1])
    tv_scan.scan_all(now=1000.0)
    assert _owner() == ['Severance (2022)']
    assert _dup(JUNK) == [{'tmdb_id': SEV, 'title': 'Severance', 'original_title': 'Severance', 'year': 2022,
                           'poster_path': f'/p{SEV}.jpg', 'overview': ''}]


def test_exact_title_year_folder_beats_an_id_tagged_copy_that_came_first(lib):
    shutil.move(str(lib / 'Severance (2022)'), str(lib / JUNK))
    tv_scan.scan_all(now=1000.0)
    assert _owner() == [JUNK]
    sid = rows('SELECT id FROM series WHERE tmdb_id = ?', SEV)[0]['id']
    _files(lib, 'Severance (2022)', [3])
    out = tv_scan.scan_all(now=2000.0)
    assert _owner() == ['Severance (2022)']
    assert rows('SELECT id FROM series WHERE tmdb_id = ?', SEV)[0]['id'] == sid      # same show row, moved
    assert [c['title'] for c in _dup(JUNK)] == ['Severance']
    assert {r['rel_path'].split('/')[0] for r in rows('SELECT rel_path FROM episode_files WHERE series_id = ?', sid)} \
        == {'Severance (2022)'}
    assert out['shows'] == 5 and out['unmatched'] == 4
    tv_scan.scan_all(now=3000.0)                                                     # stable
    assert _owner() == ['Severance (2022)'] and _dup(JUNK)


def test_most_linked_files_then_naming(lib):
    shutil.rmtree(lib / 'Severance (2022)')
    _files(lib, 'Severance', [1])
    tv_scan.scan_all(now=1000.0)
    _files(lib, 'Severance [tvdb-371980]', [1, 2, 3])        # same quality (title only), more episodes
    tv_scan.scan_all(now=2000.0)
    assert _owner() == ['Severance [tvdb-371980]'] and _dup('Severance')
    _files(lib, 'Severance', [2, 4])                           # now even: the plain name wins
    (lib / 'Severance [tvdb-371980]' / 'Severance.S01E01.mkv').unlink()
    tv_scan.scan_all(now=3000.0)
    tv_scan.scan_all(now=4000.0)
    assert _owner() == ['Severance'] and _dup('Severance [tvdb-371980]')


def test_both_new_in_one_scan_order_does_not_matter(lib):
    _files(lib, 'Aaa Severance [tmdbid-95396]', [1, 2, 3, 4, 5])  # sorts first, junk name
    out = tv_scan.scan_all(now=1000.0)
    assert _owner() == ['Severance (2022)'] and _dup('Aaa Severance [tmdbid-95396]')
    assert out['shows'] == 5 and out['unmatched'] == 4


def test_match_on_a_duplicate_moves_the_show_no_409(lib):
    _files(lib, JUNK, [1])
    tv_scan.scan_all(now=1000.0)
    uid = rows('SELECT id FROM unmatched WHERE folder = ?', JUNK)[0]['id']
    before = snapshot(lib)
    out = tv_scan.match_unmatched(uid, SEV, now=2000.0)
    assert out['ok'] is True
    assert _owner() == [JUNK] and [c['title'] for c in _dup('Severance (2022)')] == ['Severance']
    assert snapshot(lib) == before                                                   # rows only
    h = [json.loads(r['detail']) for r in rows("SELECT detail FROM history WHERE event = 'matched'"
                                               ' AND series_id = ?', out['series_id'])]
    assert h[-1]['via'] == 'manual' and h[-1]['from_folder'] == 'Severance (2022)' and h[-1]['folder'] == JUNK


def test_rank():
    show = {'title': 'Severance', 'original_title': 'Severance', 'aliases': '[]', 'year': 2022}
    r = tv_scan.folder_rank
    assert r('Severance (2022)', show, 0) > r('Severance', show, 9) > r(JUNK, show, 99)
    assert r('Severance', show, 3) > r('Severance', show, 2)
    assert r('Severance', show, 2) > r('Severance [tvdb-371980]', show, 2)
    assert r('Severance (2019)', show, 5)[0] == 0                                    # wrong year: no quality
