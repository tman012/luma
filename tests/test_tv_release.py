"""tv_release: the shared corpus (tests/fixtures/tv_release_names.tsv, also
read by the frontend's detectTvScope test) plus pack-file rules."""

from pathlib import Path

import pytest

from app import tv_release as tr

CORPUS = Path(__file__).parent / 'fixtures' / 'tv_release_names.tsv'


def _rows():
    out = []
    for line in CORPUS.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        name, pack_type, pack_episode, summary = line.split('\t')
        out.append((name, pack_type, int(pack_episode) if pack_episode else None, summary))
    return out


ROWS = _rows()


def test_corpus_is_large_and_varied():
    kinds = {r[3].split()[0] for r in ROWS}
    assert len(ROWS) >= 150
    assert kinds == {'ep', 'multi', 'season', 'seasons', 'daily', 'abs', 'none'}


@pytest.mark.parametrize('name,pack_type,pack_episode,summary', ROWS, ids=[r[0][:60] for r in ROWS])
def test_corpus(name, pack_type, pack_episode, summary):
    assert tr.parse(name).summary() == summary
    assert tr.scope(name) == {'pack_type': pack_type, 'pack_episode': pack_episode}


@pytest.mark.parametrize('name,title,year', [
    ('Doctor.Who.2005.S13E01.720p.HDTV.x264-ORGANiC', 'Doctor Who', 2005),
    ('1923.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb', '1923', None),
    ('The.Office.US.S05E14.PROPER.720p', 'The Office US', None),
    ('Show Name (2019) Season 1 S01 1080p BluRay x265 HEVC 10bit', 'Show Name', 2019),
    ('[SubsPlease] One Piece - 1052 (1080p) [A1B2C3D4].mkv', 'One Piece', None),
    ("Grey's.Anatomy.S21E01.720p", "Grey's Anatomy", None),
    ('9-1-1.S08E01.1080p', '9-1-1', None),
    ('Shōgun.S01E10.1080p', 'Shōgun', None),
])
def test_titles_and_years(name, title, year):
    r = tr.parse(name)
    assert (r.title, r.year) == (title, year)


@pytest.mark.parametrize('name,res,group,proper', [
    ('The.Bear.S03E05.1080p.WEB.h264-ETHEL', '1080p', 'ETHEL', 0),
    ('Show.Name.S01E05.PROPER.REPACK.1080p.WEB.h264-GRP', '1080p', 'GRP', 2),
    ('Show.Name.S01E05.1080i.HDTV.MPA2.0.H.264-GRP', '1080p', 'GRP', 0),
    ('Show.Name.S01E05.1080p.WEB.h264-GRP[rarbg]', '1080p', 'GRP', 0),
    ('Show.Name.1x05-1x06.HDTV', '', '', 0),
    ('Show.Name.S01E05.XviD-AFG', '', 'AFG', 0),
])
def test_quality_bits(name, res, group, proper):
    r = tr.parse(name)
    assert (r.resolution, r.group, r.proper) == (res, group, proper)


def test_complete_flag():
    assert tr.parse('The.Bear.S03.COMPLETE.2160p').complete is True
    assert tr.parse('The.Bear.S03.2160p').complete is False


@pytest.mark.parametrize('name,rel,summary', [
    ('E05.mkv', 'Show.S02.1080p/E05.mkv', 'ep S02E05'),
    ('Episode 7 - Title.mkv', 'Show/Season 3/Episode 7 - Title.mkv', 'ep S03E07'),
    ('05 - The Title.mkv', 'Show (2019)/Season 01/05 - The Title.mkv', 'ep S01E05'),
    ('Show.S02E05.mkv', 'Pack.S09/Show.S02E05.mkv', 'ep S02E05'),           # the file's own SxxEyy wins
    ('Special.mkv', 'Show/Specials/Special.mkv', 'none'),
    ('E01.mkv', 'Show/Specials/E01.mkv', 'ep S00E01'),
    ('E05.mkv', 'Show/E05.mkv', 'none'),                                    # no season anywhere
    ('Show.2024.10.05.mkv', 'Show/Season 2024/Show.2024.10.05.mkv', 'daily 2024-10-05'),
])
def test_parse_file(name, rel, summary):
    assert tr.parse_file(name, rel).summary() == summary


@pytest.mark.parametrize('rel,season', [
    ('Show/Season 1/x.mkv', 1), ('Show/season.02/x.mkv', 2), ('Show/S03/x.mkv', 3),
    ('Show/Staffel 4/x.mkv', 4), ('Show/Specials/x.mkv', 0), ('Show/Extras/x.mkv', None), ('x.mkv', None),
])
def test_season_from_folder(rel, season):
    assert tr.season_from_folder(rel) == season


def test_scope_is_the_popups_rules():
    # Known differences from parse(), kept so the popup and server agree on scope.
    assert tr.scope('Show.S01E01E02.mkv') == {'pack_type': 'episode', 'pack_episode': 1}
    assert tr.scope('Show.S01E01-E03') == {'pack_type': 'season', 'pack_episode': None}
    assert tr.scope('Show.1x05') == {'pack_type': 'episode', 'pack_episode': 5}
    assert tr.scope('') == {'pack_type': 'season', 'pack_episode': None}


def test_garbage_never_raises():
    for s in ('', ' ', '-', '[', '[]', 'S', 'S01E', 'x' * 5000, 'S99E9999-E1', '\x00', 'Season', '- 1 -'):
        tr.parse(s)
        tr.parse_file(s, s)
        tr.scope(s)


def test_parse_file_bare_number_in_season_folder():
    r = tr.parse_file('05.mkv', 'Show/Season 2/05.mkv')
    assert (r.kind, r.season, r.episodes) == ('episode', 2, [5])
    assert tr.parse_file('05.mkv', 'Show/Extras/05.mkv').kind == 'unknown'
