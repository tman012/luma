"""v165 (Tester on 006b4a6): X-Men: The Animated Series stayed in review on the 687-folder fixture with
real vote counts, because "X-Men '97" (more votes, a near name) fits the files too (43 of 44 S/E). The
Builder fixture had no vote counts, so the near-name rule never fired there. A near name now sends a
folder to review only if the folder's name could be short for it (Cosmos -> Cosmos: A Spacetime Odyssey);
a folder that says more than the near name (The Animated Series vs '97) can't be that show."""
from app import tv_match


def tmdb(*shows):
    by = {s['id']: s for s in shows}

    def search(q, year=None):
        return [{'tmdb_id': s['id'], 'title': s['name'], 'original_title': s['name'], 'year': s['year'],
                 'origin_country': [], 'vote_count': s['votes'], 'poster_path': None} for s in shows]

    def show(tid):
        s = by.get(int(tid))
        return s and {'id': s['id'], 'name': s['name'], 'original_name': s['name'], 'first_air_date': f"{s['year']}-01-15",
                      'origin_country': [], 'alternative_titles': {'results': [{'title': a} for a in s.get('aliases', [])]},
                      'seasons': [{'season_number': i + 1, 'episode_count': c} for i, c in enumerate(s['seasons'])]}
    return search, show


def files(folder, seasons):
    return tv_match.file_hints([f'{folder}/Season {s:02d}/{folder} - S{s:02d}E{e:02d}.mkv'
                                for s, last in seasons.items() for e in range(1, last + 1)])


XMEN = {'id': 4574, 'name': 'X-Men', 'year': 1992, 'votes': 800, 'aliases': ['X-Men: The Animated Series'], 'seasons': [13, 17, 14]}
XMEN97 = {'id': 800556, 'name': "X-Men '97", 'year': 2024, 'votes': 1950, 'seasons': [16, 16, 16]}
NIGHTS = {'id': 750556, 'name': 'X-Men Nights', 'year': 2015, 'votes': 5, 'seasons': [8]}


def test_x_men_the_animated_series_auto_imports_with_real_vote_counts():
    search, show = tmdb(XMEN, XMEN97, NIGHTS)
    m = tv_match.match('X-Men- The Animated Series', files('X-Men- The Animated Series', {1: 13, 2: 17, 3: 14}), search=search, show=show)
    assert (m['status'], m['tier'], m['tmdb_id']) == ('matched', 'high', 4574)


def test_a_near_name_the_folder_could_be_short_for_still_sends_it_to_review():
    cosmos = {'id': 501, 'name': 'Cosmos', 'year': 1980, 'votes': 800, 'seasons': [13]}
    odyssey = {'id': 502, 'name': 'Cosmos: A Spacetime Odyssey', 'year': 2014, 'votes': 1950, 'seasons': [13]}
    search, show = tmdb(cosmos, odyssey)
    m = tv_match.match('Cosmos', files('Cosmos', {1: 13}), search=search, show=show)
    assert m['status'] == 'unmatched' and m['why'] == 'files fit another show' and m['reason'] == 'unclear'


def test_could_be_short_for():
    f = tv_match._could_be_short_for
    assert f('Cosmos', {'title': 'Cosmos: A Spacetime Odyssey'})
    assert f('The Office', {'title': 'Office Ladies'})                    # filler words don't count against it
    assert not f('X-Men: The Animated Series', {'title': "X-Men '97"})
    assert f('X-Men: The Animated Series', {'title': "X-Men '97"}, {'alternative_titles': {'results': [{'title': 'X-Men: The Animated Series Revival'}]}})
    assert f('', {'title': 'Anything'})                                   # nothing to compare: keep the review rule
