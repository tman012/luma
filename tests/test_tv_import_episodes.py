"""TV Import, episode files: the file's own S/E wins over its folder,
specials by title, multi-episode files, year seasons, anime absolute
numbers, episode groups picked by how many files they link, and leftovers
that never block a show."""
import pytest

from app import tv_scan
from tests import tv_fake
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import rows, select
from tests.tv_fake import fake_tmdb  # noqa: F401

PID = 'p_0000bbbb'
EXTRA = {
    7001: {'name': 'Pirate Saga', 'first_air_date': '1999-10-20', 'tvdb': 81797, 'seasons': {1: 3, 2: 4}},
    7002: {'name': 'Yearly Show', 'first_air_date': '2023-01-01', 'tvdb': 81798, 'seasons': {2023: 2, 2024: 3}},
    7003: {'name': 'Holiday Show', 'first_air_date': '2010-01-01', 'tvdb': 81799, 'seasons': {0: 2, 1: 2},
           'titles': {0: {1: 'The Winter Gala', 2: 'Behind the Curtain'}}},
    # TMDB has one 6-episode season; TVDB (and the files) split it 3 + 3
    7004: {'name': 'Split Season', 'first_air_date': '2015-01-01', 'tvdb': 81800, 'seasons': {1: 6},
           'groups': {'g-dvd': {'name': 'DVD', 'type': 3, 'seasons': {1: [7004101, 7004102]}},
                      'g-tvdb': {'name': 'Aired Order (TVDB)', 'type': 1,
                                 'seasons': {1: [7004101, 7004102, 7004103], 2: [7004104, 7004105, 7004106]}}}},
    7005: {'name': 'Nothing Fits', 'first_air_date': '2001-01-01', 'tvdb': 81801, 'seasons': {1: 2}},
}
FILES = {
    'Pirate Saga [tvdbid-81797]': ['Pirate Saga - 001.mkv', 'Pirate Saga - 005.mkv', 'Pirate Saga - 007.mkv',
                                   'Pirate Saga - 099.mkv'],
    'Yearly Show [tvdbid-81798]': ['Season 2023/Yearly.Show.S2023E02.mkv', '2024/Yearly.Show.S2024E03.mkv'],
    'Holiday Show [tvdbid-81799]': ['Specials/Holiday Show - The Winter Gala.mkv',
                                    'Specials/Holiday.Show.S00E09.Behind.the.Curtain.mkv',
                                    'Season 1/Holiday.Show.S01E01E02E03.mkv'],
    'Split Season [tvdbid-81800]': ['Season 1/Split.S01E01.mkv', 'Season 1/Split.S01E03.mkv',
                                    'Season 2/Split.S02E01.mkv', 'Season 2/Split.S02E03.mkv'],
    'Nothing Fits [tvdbid-81801]': ['Season 9/Nothing.Fits.S09E01.mkv', 'Season 9/notes.mkv'],
}


@pytest.fixture
def lib(tv_env, fake_tmdb, monkeypatch):  # noqa: F811
    for tid, s in EXTRA.items():
        monkeypatch.setitem(tv_fake.SHOWS, tid, s)
    root = tv_env / 'TV'
    for folder, names in FILES.items():
        for n in names:
            p = root / folder / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'x')
    select(PID, root)
    tv_scan._state.update(running=False, error=None, summary=None)
    tv_scan._backoff.update(fails=0, until=0.0)
    tv_scan.scan_all(now=1000.0)
    return root


def _files(folder):
    out = {}
    for f in rows('SELECT f.id, f.rel_path, f.state FROM episode_files f JOIN series s ON s.id = f.series_id'
                  ' WHERE s.folder = ?', folder):
        eps = rows('SELECT e.season_number s, e.episode_number e FROM episode_file_links l JOIN episodes e'
                   ' ON e.id = l.episode_id WHERE l.file_id = ?', f['id'])
        out[f['rel_path'].split('/')[-1]] = (f['state'], sorted((x['s'], x['e']) for x in eps))
    return out


def test_anime_absolute_numbers_count_across_seasons(lib):
    f = _files('Pirate Saga [tvdbid-81797]')
    assert f['Pirate Saga - 001.mkv'] == ('linked', [(1, 1)])
    assert f['Pirate Saga - 005.mkv'] == ('linked', [(2, 2)])
    assert f['Pirate Saga - 007.mkv'] == ('linked', [(2, 4)])
    assert f['Pirate Saga - 099.mkv'][0] == 'mismatch'


def test_year_seasons(lib):
    f = _files('Yearly Show [tvdbid-81798]')
    assert f['Yearly.Show.S2023E02.mkv'] == ('linked', [(2023, 2)])
    assert f['Yearly.Show.S2024E03.mkv'] == ('linked', [(2024, 3)])


def test_specials_by_title_and_multi_episode(lib):
    f = _files('Holiday Show [tvdbid-81799]')
    assert f['Holiday Show - The Winter Gala.mkv'] == ('linked', [(0, 1)])
    assert f['Holiday.Show.S00E09.Behind.the.Curtain.mkv'] == ('linked', [(0, 2)])     # number off, title right
    assert f['Holiday.Show.S01E01E02E03.mkv'] == ('linked', [(1, 1), (1, 2)])           # the episodes that exist


def test_group_with_the_most_linked_files_wins(lib, fake_tmdb):
    s = rows("SELECT episode_order, episode_group_id FROM series WHERE folder = 'Split Season [tvdbid-81800]'")[0]
    assert (s['episode_order'], s['episode_group_id']) == ('group', 'g-tvdb')
    f = _files('Split Season [tvdbid-81800]')
    assert f['Split.S02E01.mkv'] == ('linked', [(1, 4)])          # TVDB S02E01 is TMDB S01E04
    assert f['Split.S02E03.mkv'] == ('linked', [(1, 6)])
    assert f['Split.S01E03.mkv'] == ('linked', [(1, 3)])


def test_no_group_lookup_when_files_fit(lib, fake_tmdb):
    assert ('episode_groups', 7002) not in fake_tmdb.calls
    assert ('episode_groups', 7004) in fake_tmdb.calls


def test_leftovers_never_block_a_show(lib):
    s = rows("SELECT tmdb_id, match_tier FROM series WHERE folder = 'Nothing Fits [tvdbid-81801]'")
    assert s and s[0]['tmdb_id'] == 7005
    f = _files('Nothing Fits [tvdbid-81801]')
    assert f['Nothing.Fits.S09E01.mkv'][0] == 'mismatch' and f['notes.mkv'][0] == 'unrecognized'


def test_group_survives_a_rescan(lib):
    tv_scan.scan_all(now=2000.0)
    assert _files('Split Season [tvdbid-81800]')['Split.S02E01.mkv'] == ('linked', [(1, 4)])
