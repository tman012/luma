import pytest
"""Reviewer: symlinks never make the TV scan count a show folder or a file twice
(a link to a sibling show folder, a loop back up, a hard link)."""
import os

from app import tv_scan
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.tv_tree import snapshot


def test_link_to_another_show_folder_counts_once(lib):
    os.symlink(lib / 'Severance (2022)', lib / 'Aaa Severance link')
    assert 'Aaa Severance link' not in tv_scan.show_folders(str(lib))
    assert 'Severance (2022)' in tv_scan.show_folders(str(lib))          # the real folder keeps it
    out = tv_scan.scan_all(now=1000.0)
    assert out['shows'] == 5 and out['unmatched'] == 3
    assert rows('SELECT folder FROM series WHERE tmdb_id = 95396') == [{'folder': 'Severance (2022)'}]


def test_loop_links_inside_a_show_count_each_file_once(lib):
    sev = lib / 'Severance (2022)'
    os.symlink(sev, sev / 'Season 01' / 'loop')                         # back up to the show
    os.symlink(sev / 'Season 01', sev / 'S1 again')                      # a sibling folder
    os.symlink(lib, lib / 'Library loop')                                # the library itself
    os.link(sev / 'Season 01' / 'Severance.S01E02.1080p.WEB-DL-GRP.mkv', sev / 'Extras' / 'hardlink.mkv')
    files = [rel for rel, _ in tv_scan.video_files(str(sev), str(lib))]
    assert len(files) == len({f.rsplit('/', 1)[-1] for f in files}) == 6
    assert 'Library loop' not in tv_scan.show_folders(str(lib))
    before = snapshot(lib)
    tv_scan.scan_all(now=1000.0)
    sid = rows('SELECT id FROM series WHERE tmdb_id = 95396')[0]['id']
    assert len(rows('SELECT id FROM episode_files WHERE series_id = ?', sid)) == 6
    assert snapshot(lib) == before


@pytest.fixture(autouse=True)
def _review_rows_stay(monkeypatch):
    """These tests drive the review flows (or count review rows): keep the pre-v157 rule so the small tree
    still has same-name rows in review. v157's auto-import is covered in test_tv_scan and the 687 fixture."""
    from app import tv_scan as _ts
    monkeypatch.setattr(_ts, 'AUTO_IMPORT', False)
