"""Luma decides (Taylor, Oct 18, reversing the backed-only rule from Tester on
4d22f6c): review pre-checks every best guess, backed or not, so one Import all
imports everything. Only a true tie starts unchecked (reason 'unclear'): another
top result with the same title, year and country scores that the files fit just
as well. Rows with no TMDB result and duplicates are unchecked elsewhere."""
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


@pytest.mark.parametrize('folder,show,eps,reason', [
    ('WWE Raw', RAW_CLASSICS, None, 'not_exact'),           # no files: Luma's guess, checked
    ('WWE Raw', RAW_CLASSICS, {2024: 150}, 'unclear'),      # files that don't fit it (PM): unchecked
    ('True Story', SMACKDOWN, None, 'not_exact'),
    ('WrestleMania', ROAD_TO_WM, None, 'not_exact'),
    ('WrestleMania', ROAD_TO_WM, {38: 3}, 'unclear'),       # files from a season it doesn't have
    ('WrestleMania', ROAD_TO_WM, {1: 3}, 'unclear'),        # its seasons don't end where the files do
])
def test_partial_guesses_start_checked_unless_the_files_disagree(folder, show, eps, reason):
    search, sh = tmdb(show)
    hints = files(folder, eps) if eps else None
    m = tv_match.match(folder, hints, search=search, show=sh)
    assert m['status'] == 'unmatched' and m['tier'] == 'likely' and m['pick'] == show['id']
    assert m['reason'] == reason


def test_files_that_fit_back_a_partial_title():
    search, sh = tmdb(COSMOS_2014)
    m = tv_match.match('Cosmos Spacetime', files('Cosmos Spacetime', {1: 13, 2: 13}), search=search, show=sh)
    assert (m['status'], m['tier'], m['pick'], m['reason']) == ('unmatched', 'likely', 204, 'not_exact')
    m = tv_match.match('Cosmos Spacetime', None, search=search, show=sh)                     # no files: still checked
    assert (m['status'], m['tier'], m['pick'], m['reason']) == ('unmatched', 'likely', 204, 'not_exact')


def test_files_must_single_out_the_pick():
    """Files that fit two partial titles equally well: a true tie, unchecked (Penn & Teller: Bull! vs Bull!: Unscripted)."""
    a = {'id': 301, 'name': 'Penn & Teller: Bull!', 'year': 2003, 'seasons': [13]}
    b = {'id': 302, 'name': 'Penn & Teller: Bull!: Unscripted', 'year': 2008, 'seasons': [13]}
    search, sh = tmdb(a, b)
    m = tv_match.match('Penn & Teller- Bullshit', files('Penn & Teller- Bullshit', {1: 13}), search=search, show=sh)
    assert m['status'] == 'unmatched' and m['reason'] == 'unclear'


def test_an_exact_title_backs_its_pick():
    rows = [{'c': {'tmdb_id': 1}, 'title': tv_match.W_EXACT}, {'c': {'tmdb_id': 2}, 'title': tv_match.W_EXACT - 1}]
    assert tv_match.backed(rows, None, {}, 1) is True
    assert tv_match.backed(rows, None, {}, 2) is False


def test_files_break_a_partial_title_tie():
    """The same two partial titles, but the files only fit one of them: that one is the pick, checked."""
    a = {'id': 301, 'name': 'Penn & Teller: Bull!', 'year': 2003, 'seasons': [13] * 8}
    b = {'id': 302, 'name': 'Penn & Teller: Bull!: Unscripted', 'year': 2008, 'seasons': [13]}
    search, sh = tmdb(a, b)
    m = tv_match.match('Penn & Teller- Bullshit', files('Penn & Teller- Bullshit', {5: 13, 6: 13}), search=search, show=sh)
    assert (m['status'], m['pick'], m['reason']) == ('unmatched', 301, 'not_exact')


def test_two_partial_titles_without_files_tie():
    a = {'id': 401, 'name': 'Bullshit Stories', 'year': 2003, 'seasons': [13]}
    b = {'id': 402, 'name': 'Bullshit Tales', 'year': 2003, 'seasons': [13]}
    search, sh = tmdb(a, b)
    m = tv_match.match('Bullshit', None, search=search, show=sh)
    assert m['status'] == 'unmatched' and m['tier'] == 'likely' and m['reason'] == 'unclear'   # stored without a pick


# ------------------------------------------------------------------ Tester on 0a022bf

def test_a_brand_folder_leads_with_the_show_itself():
    """'WWE Raw' -> Raw (checked), never WWE Raw Classics / Top 10."""
    raw = {'id': 4656, 'name': 'Raw', 'year': 1993, 'seasons': [52] * 30}
    top10 = {'id': 205, 'name': 'WWE Raw Top 10', 'year': 2025, 'seasons': [10]}
    search, sh = tmdb(RAW_CLASSICS, top10, raw)
    m = tv_match.match('WWE Raw', None, search=search, show=sh)
    assert (m['status'], m['pick'], m['reason']) == ('unmatched', 4656, 'not_exact')
    assert m['candidates'][0]['tmdb_id'] == 4656
    m = tv_match.match('WWE WrestleMania', None, search=tmdb(ROAD_TO_WM)[0], show=tmdb(ROAD_TO_WM)[1])
    assert m['pick'] == 203 and m['reason'] == 'not_exact'     # no WrestleMania itself: Luma's guess (files decide below)
    m = tv_match.match('WWE WrestleMania', files('WWE WrestleMania', {38: 3}), search=tmdb(ROAD_TO_WM)[0], show=tmdb(ROAD_TO_WM)[1])
    assert m['reason'] == 'unclear'


def test_files_fit_another_show_is_never_prechecked(monkeypatch):
    cosmos = {'id': 501, 'name': 'Cosmos', 'year': 1980, 'seasons': [13]}
    odyssey = {'id': 502, 'name': 'Cosmos: A Spacetime Odyssey', 'year': 2014, 'seasons': [13]}
    search, sh = tmdb(cosmos, odyssey)
    monkeypatch.setattr(tv_match, 'decide', lambda *a, **k: ('likely', 'files fit another show'))
    m = tv_match.match('Cosmos', files('Cosmos', {1: 13}), search=search, show=sh)
    assert m['status'] == 'unmatched' and m['reason'] == 'unclear'


def test_a_guess_another_folder_owns_gives_way_to_the_same_title():
    from app import tv_scan
    cands = [{'tmdb_id': 2316, 'title': 'The Office'}, {'tmdb_id': 2996, 'title': 'The Office'}, {'tmdb_id': 9, 'title': 'Office Space'}]
    assert tv_scan._skip_owned(2316, cands, {2316}) == (2996, [cands[1], cands[0], cands[2]])
    assert tv_scan._skip_owned(2316, cands, {2316, 2996}) == (None, cands)       # none left: unchecked
    assert tv_scan._skip_owned(2316, cands, set()) == (2316, cands)
    assert tv_scan._skip_owned(None, cands, {2316})[0] is None                     # an unchecked guess stays unchecked
