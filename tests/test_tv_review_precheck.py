"""Review pre-checks a row only when its pick is backed: an exact title, or
files that fit it (Tester on 4d22f6c: WWE Raw -> Raw Classics, True Story ->
SmackDown, WrestleMania -> Road to WrestleMania were pre-checked). Anything
else is pre-picked but unchecked (reason 'unclear'; the UI's startsChecked)."""
import pytest

from app import tv_match


def tmdb(*shows):
    by = {s['id']: s for s in shows}

    def search(q, year=None):
        return [{'tmdb_id': s['id'], 'title': s['name'], 'original_title': s['name'], 'year': s['year'],
                 'origin_country': ['US'], 'vote_count': 0, 'poster_path': None} for s in shows]

    def show(tid):
        s = by.get(int(tid))
        return s and {'id': s['id'], 'name': s['name'], 'original_name': s['name'], 'first_air_date': f"{s['year']}-01-10",
                      'origin_country': ['US'], 'alternative_titles': {'results': []},
                      'seasons': [{'season_number': i + 1, 'episode_count': c} for i, c in enumerate(s['seasons'])]}
    return search, show


def files(folder, seasons):
    return tv_match.file_hints([f'{folder}/Season {s:02d}/{folder} - S{s:02d}E{e:02d}.mkv'
                                for s, last in seasons.items() for e in range(1, last + 1)])


RAW_CLASSICS = {'id': 201, 'name': 'WWE Raw Classics', 'year': 2012, 'seasons': [40]}
SMACKDOWN = {'id': 202, 'name': 'WWE SmackDown', 'year': 1999, 'seasons': [52] * 20}
ROAD_TO_WM = {'id': 203, 'name': 'Road to WrestleMania', 'year': 2015, 'seasons': [4]}
COSMOS_2014 = {'id': 204, 'name': 'Cosmos: A Spacetime Odyssey', 'year': 2014, 'seasons': [13, 13, 13]}


@pytest.mark.parametrize('folder,show,eps', [
    ('WWE Raw', RAW_CLASSICS, None),
    ('WWE Raw', RAW_CLASSICS, {2024: 150}),          # files that don't fit it
    ('True Story', SMACKDOWN, None),
    ('WrestleMania', ROAD_TO_WM, None),
    ('WrestleMania', ROAD_TO_WM, {38: 3}),            # files from a season it doesn't have
    ('WrestleMania', ROAD_TO_WM, {1: 3}),             # 3 episodes of season 1 fit nearly anything: not enough
])
def test_unbacked_picks_start_unchecked(folder, show, eps):
    search, sh = tmdb(show)
    hints = files(folder, eps) if eps else None
    m = tv_match.match(folder, hints, search=search, show=sh)
    assert m['status'] == 'unmatched' and m['tier'] == 'likely' and m['pick'] == show['id']
    assert m['reason'] == 'unclear'


def test_files_that_fit_back_a_partial_title():
    search, sh = tmdb(COSMOS_2014)
    m = tv_match.match('Cosmos Spacetime', files('Cosmos Spacetime', {1: 13, 2: 13}), search=search, show=sh)
    assert (m['status'], m['tier'], m['pick'], m['reason']) == ('unmatched', 'likely', 204, 'not_exact')
    m = tv_match.match('Cosmos Spacetime', None, search=search, show=sh)                     # no files: unchecked
    assert (m['status'], m['tier'], m['pick'], m['reason']) == ('unmatched', 'likely', 204, 'unclear')


def test_files_must_single_out_the_pick():
    """Files that fit two partial titles back neither (Penn & Teller: Bull! vs Bull!: Unscripted)."""
    a = {'id': 301, 'name': 'Penn & Teller: Bull!', 'year': 2003, 'seasons': [13]}
    b = {'id': 302, 'name': 'Penn & Teller: Bull!: Unscripted', 'year': 2008, 'seasons': [13]}
    search, sh = tmdb(a, b)
    m = tv_match.match('Penn & Teller- Bullshit', files('Penn & Teller- Bullshit', {1: 13}), search=search, show=sh)
    assert m['status'] == 'unmatched' and m['reason'] == 'unclear'


def test_an_exact_title_backs_its_pick():
    rows = [{'c': {'tmdb_id': 1}, 'title': tv_match.W_EXACT}, {'c': {'tmdb_id': 2}, 'title': tv_match.W_EXACT - 1}]
    assert tv_match.backed(rows, None, {}, 1) is True
    assert tv_match.backed(rows, None, {}, 2) is False
