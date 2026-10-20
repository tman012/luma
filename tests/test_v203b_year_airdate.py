from app import tv_search


def test_release_year_matching_the_episode_air_year_is_not_a_wrong_show():
    show = {'names': {tv_search.tv_match.normalize('CIA')}, 'year': 2024, 'profile': None}
    want = [{'id': 1, 'season_number': 2, 'episode_number': 3, 'air_date': '2026-10-19', 'runtime': 60}]
    items = [{'title': 'CIA.2026.S02E03.1080p.WEB.h264-ETHEL', 'seeders': 5},
             {'title': 'CIA.2019.S02E03.1080p.WEB.h264-ETHEL', 'seeders': 5}]
    got = [c['item']['title'] for c in tv_search.candidates(items, show, want)]
    assert got == ['CIA.2026.S02E03.1080p.WEB.h264-ETHEL']
