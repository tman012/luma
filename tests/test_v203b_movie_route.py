import time
from app import movie_import as mi

CFG = dict(mi.DEFAULT_NAMING)
NOW = time.mktime((2026, 10, 20, 3, 0, 0, 0, 0, -1))


def lay(tmp_path):
    for v in mi.ROUTE_FOLDERS.values():
        (tmp_path / v).mkdir()
    return str(tmp_path / 'Movies')


def test_routes_like_prod(tmp_path):
    lib = lay(tmp_path)
    r = lambda rel, y: mi.route(lib, rel, rel + '.mkv', 'Dune', y, CFG, NOW).rsplit('/', 1)[1]
    assert r('Dune.2021.2160p.WEB-DL.x265-G', 2021) == '4K'
    assert r('Dune.2021.1080p.BluRay.REMUX.AVC-G', 2021) == 'Full Rips'
    assert r('Dune.2025.1080p.WEB-DL-G', 2025) == 'Recently Released'
    assert r('Dune.2024.1080p.WEB-DL-G', 2024) == 'Movies'


def test_existing_folder_anywhere_wins(tmp_path):
    lib = lay(tmp_path)
    (tmp_path / 'Movies' / 'Dune (2025)').mkdir()
    assert mi.route(lib, 'Dune.2025.2160p.WEB-DL-G', 'x.mkv', 'Dune', 2025, CFG, NOW).endswith('/Movies')


def test_other_layout_is_one_root(tmp_path):
    (tmp_path / 'films').mkdir()
    assert mi.route(str(tmp_path / 'films'), 'Dune.2025.2160p.WEB-DL-G', 'x.mkv', 'Dune', 2025, CFG, NOW) == str(tmp_path / 'films')


def test_missing_sibling_falls_back_to_the_set_root(tmp_path):
    (tmp_path / 'Movies').mkdir()
    assert mi.route(str(tmp_path / 'Movies'), 'Dune.2025.2160p.WEB-DL-G', 'x.mkv', 'Dune', 2025, CFG, NOW) == str(tmp_path / 'Movies')
