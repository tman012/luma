"""Reviewer's adversarial same-name set (tv_match): popularity never decides; a
same-name rival auto-picks only when the files fit the pick and rule out every
rival; review pre-picks the show the files fit best.

TMDB ids are placeholders (no TMDB call anywhere); titles, years, countries and
season layouts are the shows' real ones (episode counts per season, approximate
where TMDB's own counts vary). Results come in TMDB's order (the most popular
first), with vote counts in their real order of magnitude so a popularity lead
is present and must not matter."""
import pytest

from app import tv_match

TOP_GEAR_UK = {'id': 101, 'name': 'Top Gear', 'year': 2002, 'country': ['GB'], 'votes': 1600,
               'seasons': [10, 8, 9, 9, 7, 10, 6, 9, 7, 8, 6, 7, 6, 7, 6, 7, 6, 8, 7, 6, 6, 7, 8, 7, 6, 7, 7, 6, 6, 6, 6, 6, 6]}
TOP_GEAR_US = {'id': 102, 'name': 'Top Gear', 'year': 2010, 'country': ['US'], 'votes': 60, 'seasons': [10, 16, 16, 16, 12, 10]}
SHAMELESS_US = {'id': 111, 'name': 'Shameless', 'year': 2011, 'country': ['US'], 'votes': 2500, 'seasons': [12] * 11}
SHAMELESS_UK = {'id': 112, 'name': 'Shameless', 'year': 2004, 'country': ['GB'], 'votes': 300,
                'seasons': [7, 10, 16, 16, 16, 22, 16, 11, 14, 8, 6]}
GHOSTS_US = {'id': 121, 'name': 'Ghosts', 'year': 2021, 'country': ['US'], 'votes': 400, 'seasons': [18, 22, 10, 22]}
GHOSTS_UK = {'id': 122, 'name': 'Ghosts', 'year': 2019, 'country': ['GB'], 'votes': 250, 'seasons': [6, 6, 6, 6, 6]}
KINGDOM_KR = {'id': 131, 'name': 'Kingdom', 'year': 2019, 'country': ['KR'], 'votes': 2400, 'seasons': [6, 6]}
KINGDOM_US = {'id': 132, 'name': 'Kingdom', 'year': 2014, 'country': ['US'], 'votes': 200, 'seasons': [10, 20, 10]}
KINGDOM_UK = {'id': 133, 'name': 'Kingdom', 'year': 2007, 'country': ['GB'], 'votes': 30, 'seasons': [6, 6, 6]}
NIGHT_UK = {'id': 141, 'name': 'The Night Manager', 'year': 2016, 'country': ['GB'], 'votes': 1200, 'seasons': [6, 6]}
NIGHT_IN = {'id': 142, 'name': 'The Night Manager', 'year': 2023, 'country': ['IN'], 'votes': 40, 'seasons': [7]}
STAIRCASE_HBO = {'id': 151, 'name': 'The Staircase', 'year': 2022, 'country': ['US'], 'votes': 600, 'seasons': [8]}
STAIRCASE_DOC = {'id': 152, 'name': 'The Staircase', 'year': 2004, 'country': ['FR'], 'votes': 200, 'seasons': [13]}


def tmdb(*shows, year_filter=True):
    by = {s['id']: s for s in shows}

    def brief(s):
        return {'tmdb_id': s['id'], 'title': s['name'], 'original_title': s['name'], 'year': s['year'],
                'origin_country': s['country'], 'vote_count': s['votes'], 'poster_path': None}

    def search(q, year=None):
        if tv_match.normalize(q) != tv_match.normalize(shows[0]['name']):
            return []
        return [brief(s) for s in shows if not (year and year_filter) or s['year'] == year]

    def show(tid):
        s = by.get(int(tid))
        return s and {'id': s['id'], 'name': s['name'], 'original_name': s['name'], 'first_air_date': f"{s['year']}-01-10",
                      'origin_country': s['country'], 'alternative_titles': {'results': []},
                      'seasons': [{'season_number': i + 1, 'episode_count': c} for i, c in enumerate(s['seasons'])]}
    return search, show


def files(folder, seasons):
    """Sonarr-style names: {season: last episode} -> every episode 1..last."""
    return tv_match.file_hints([f'{folder}/Season {s:02d}/{folder} - S{s:02d}E{e:02d}.mkv'
                                for s, last in seasons.items() for e in range(1, last + 1)])


CASES = [
    # folder, shows in TMDB order, files {season: last episode}, right show, auto allowed
    ('Top Gear', (TOP_GEAR_UK, TOP_GEAR_US), {1: 10, 2: 16, 3: 16}, 102, False),      # US files: never UK
    ('Top Gear', (TOP_GEAR_UK, TOP_GEAR_US), {2: 16}, 102, False),
    ('Top Gear (US)', (TOP_GEAR_UK, TOP_GEAR_US), {1: 10, 2: 16}, 102, True),
    ('Top Gear', (TOP_GEAR_UK, TOP_GEAR_US), {19: 7, 20: 6}, 101, True),               # UK files rule the US one out
    ('Shameless (UK)', (SHAMELESS_US, SHAMELESS_UK), {1: 7, 2: 10, 3: 16}, 112, True),
    ('Shameless', (SHAMELESS_US, SHAMELESS_UK), {1: 7, 2: 10, 3: 16}, 112, False),
    ('Shameless (US)', (SHAMELESS_US, SHAMELESS_UK), {1: 12, 2: 12}, 111, True),
    ('Ghosts', (GHOSTS_US, GHOSTS_UK), {1: 6, 2: 6}, 122, False),
    ('Ghosts (UK)', (GHOSTS_US, GHOSTS_UK), {1: 6, 2: 6}, 122, True),
    ('Kingdom', (KINGDOM_KR, KINGDOM_US, KINGDOM_UK), {1: 10, 2: 20}, 132, False),
    ('Kingdom (US)', (KINGDOM_KR, KINGDOM_US, KINGDOM_UK), {1: 10, 2: 20}, 132, True),
    ('The Night Manager (2023)', (NIGHT_UK, NIGHT_IN), {1: 7}, 142, True),
    ('The Night Manager', (NIGHT_UK, NIGHT_IN), {1: 6}, 141, False),
    ('The Staircase', (STAIRCASE_HBO, STAIRCASE_DOC), {1: 3}, None, False),           # either is right: review only
]


@pytest.mark.parametrize('high_no_year', [70, 80])
@pytest.mark.parametrize('year_filter', [True, False])
@pytest.mark.parametrize('folder,shows,eps,right,auto_ok', CASES, ids=[f'{c[0]}-{i}' for i, c in enumerate(CASES)])
def test_adversarial_same_name(monkeypatch, high_no_year, year_filter, folder, shows, eps, right, auto_ok):
    monkeypatch.setattr(tv_match, 'HIGH_NO_YEAR', high_no_year)
    search, show = tmdb(*shows, year_filter=year_filter)
    m = tv_match.match(folder, files(folder.split(' (')[0], eps), search=search, show=show)
    if m['status'] == 'matched':
        assert auto_ok and m['tmdb_id'] == right, (folder, m['tmdb_id'], m['why'])     # never a wrong auto-pick
    else:
        assert m['tier'] == 'likely' and m['pick'] == (right or shows[0]['id']), (folder, m['pick'])
    if not auto_ok:
        assert m['status'] == 'unmatched'
    if m['status'] == 'unmatched':      # unchecked unless the files fit the pre-pick alone
        sole = [s['id'] for s in shows if tv_match.fits(tv_match.file_fit(files(folder.split(' (')[0], eps), show(s['id'])))]
        assert (m['reason'] == 'unclear') == (m['why'] in tv_match.SAME_NAME and sole != [m['pick']]), (folder, m['reason'])


def test_popularity_never_decides():
    """A 1000-to-1 vote lead between same-name shows the files can't separate stays in review."""
    big = {**STAIRCASE_HBO, 'votes': 100000}
    search, show = tmdb(big, {**STAIRCASE_DOC, 'votes': 10})
    m = tv_match.match('The Staircase', files('The Staircase', {1: 8}), search=search, show=show)
    assert (m['status'], m['tier']) == ('unmatched', 'likely')
    m = tv_match.match('The Staircase', None, search=search, show=show)
    assert (m['status'], m['tier']) == ('unmatched', 'likely')


def test_a_rival_without_details_is_never_ruled_out():
    search, show0 = tmdb(TOP_GEAR_UK, TOP_GEAR_US)
    m = tv_match.match('Top Gear', files('Top Gear', {19: 7}), search=search,
                       show=lambda tid: None if int(tid) == 102 else show0(tid))
    assert m['status'] == 'unmatched' and m['pick'] == 101


def test_fit_helpers():
    h = files('Top Gear', {2: 16})
    uk, us = (tmdb(TOP_GEAR_UK, TOP_GEAR_US)[1](i) for i in (101, 102))
    assert tv_match.contradicts(tv_match.file_fit(h, uk)) and not tv_match.fits(tv_match.file_fit(h, uk))
    assert tv_match.fits(tv_match.file_fit(h, us)) and not tv_match.contradicts(tv_match.file_fit(h, us))
    assert not tv_match.contradicts(None) and not tv_match.fits(None)
    assert tv_match.HIGH_NO_YEAR == 70


def test_adversarial_set_reviewer_runner():
    """Reviewer's 53-case set (luma-qa/tv-import-adversarial, run.py --mode both): exit 0, no wrong picks."""
    import os, subprocess, sys
    here = '/workspace/luma-qa/tv-import-adversarial/run.py'
    if not os.path.exists(here):
        pytest.skip('Reviewer adversarial set not on this machine')
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for n in ('70', '80'):
        r = subprocess.run([sys.executable, here, '--repo', repo, '--mode', 'both', '--high-no-year', n],
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stdout[-3000:]
