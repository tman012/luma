"""Folder to TMDB show matching (app/tv_match)."""
import pytest

from app import tv_match
from tests.tv_fake import fake_tmdb  # noqa: F401


@pytest.mark.parametrize('name,title,year,ids', [
    ('Severance (2022)', 'Severance', 2022, {}),
    ('Severance', 'Severance', None, {}),
    ('Severance.2022', 'Severance', 2022, {}),
    ('The Office (US) {tvdb-73244}', 'The Office', None, {'tvdb': 73244}),
    ('Dark (2017) [tmdbid-70523]', 'Dark', 2017, {'tmdb': 70523}),
    ('Dark {tmdb-70523}', 'Dark', None, {'tmdb': 70523}),
    ('Taskmaster [imdbid-tt4934214]', 'Taskmaster', None, {'imdb': 'tt4934214'}),
    ('Taskmaster {imdb-tt4934214}', 'Taskmaster', None, {'imdb': 'tt4934214'}),
    ('1923 (2022)', '1923', 2022, {}),
    ('2001', '2001', None, {}),
])
def test_parse_folder(name, title, year, ids):
    g = tv_match.parse_folder(name)
    assert (g['title'], g['year'], g['ids']) == (title, year, ids)


@pytest.mark.parametrize('a,b', [('Marvel’s Agents of S.H.I.E.L.D.', "marvels agents of s h i e l d"),
                                 ('Law & Order', 'law and order'), ('Pokémon', 'pokemon')])
def test_normalize(a, b):
    assert tv_match.normalize(a) == b


def test_tmdb_tag_via_show(fake_tmdb):
    m = tv_match.match('Dark [tmdbid-70523]')
    assert (m['status'], m['tmdb_id'], m['via']) == ('matched', 70523, 'tmdb')
    assert [c[0] for c in fake_tmdb.calls] == ['show']


def test_tvdb_and_imdb_tags_via_find(fake_tmdb):
    assert tv_match.match('The Office (US) {tvdb-73244}')['tmdb_id'] == 2316
    assert tv_match.match('Taskmaster [imdbid-tt4934214]')['tmdb_id'] == 63770
    assert ('find', '73244', 'tvdb_id') in fake_tmdb.calls and ('find', 'tt4934214', 'imdb_id') in fake_tmdb.calls


def test_unknown_tmdb_tag_falls_back_to_search(fake_tmdb):
    m = tv_match.match('Severance (2022) {tmdb-999999}')
    assert (m['status'], m['tmdb_id'], m['via']) == ('matched', 95396, 'search')


def test_exact_title_and_year(fake_tmdb):
    assert tv_match.match('Severance (2022)')['tmdb_id'] == 95396
    assert tv_match.match('Mystery Show (2019)')['tmdb_id'] == 1002


def test_single_exact_hit_without_year(fake_tmdb):
    m = tv_match.match('Severance')
    assert m['status'] == 'matched' and m['tmdb_id'] == 95396


def test_two_hits_without_year_is_unmatched_with_candidates(fake_tmdb):
    m = tv_match.match('Mystery Show')
    assert m['status'] == 'unmatched' and m['reason'] == 'not_exact'
    assert sorted(c['tmdb_id'] for c in m['candidates']) == [1001, 1002]


def test_wrong_year_is_unmatched_never_accepted(fake_tmdb):
    m = tv_match.match('Severance (2019)')
    assert m['status'] == 'unmatched' and [c['tmdb_id'] for c in m['candidates']] == [95396]


def test_partial_title_is_not_exact(fake_tmdb):
    m = tv_match.match('Office')
    assert m['status'] == 'unmatched' and [c['tmdb_id'] for c in m['candidates']] == [2316]


def test_no_results(fake_tmdb):
    m = tv_match.match('Zzz Nothing')
    assert (m['status'], m['tier'], m['candidates'], m['reason'], m['pick']) == ('unmatched', 'none', [], 'no_results', None)
    assert m['guess'] == {'title': 'Zzz Nothing', 'year': None, 'country': None}


def test_top_five_only(fake_tmdb, monkeypatch):
    from app import tv_tmdb
    monkeypatch.setattr(tv_tmdb, 'search', lambda t, y=None: [{'tmdb_id': i, 'title': f'X{i}', 'original_title': '',
                                                              'year': 2000} for i in range(1, 9)])
    assert [c['tmdb_id'] for c in tv_match.match('X')['candidates']] == [1, 2, 3, 4, 5]


def test_tmdb_error_propagates(fake_tmdb):
    from app import tv_tmdb
    fake_tmdb.fail = tv_tmdb.NO_KEY
    with pytest.raises(tv_tmdb.TmdbError):
        tv_match.match('Severance')
