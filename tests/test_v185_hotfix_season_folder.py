"""v185 hotfix (Taylor): a new season folder follows the Season folder format exactly (never the style
of the show's other season folders); an existing folder for that same season is reused as it is
('Season 8' or 'Season 08'); nothing is renamed."""
import pytest

from app import tv_naming

D = tv_naming.DEFAULTS
PAD = {**D, 'season_folder': 'Season {season:00}'}


@pytest.mark.parametrize('existing', [['Season 8'], ['Season 08'], ['season 8'], ['S08'], ['Series 8'], ['Season 008']])
def test_an_existing_same_season_folder_is_reused_as_is(existing):
    assert tv_naming.season_folder(8, existing + ['Season 01', 'Extras'], D) == existing[0]
    assert tv_naming.season_folder(8, existing + ['Season 1'], PAD) == existing[0]


@pytest.mark.parametrize('cfg,want', [(D, 'Season 9'), (PAD, 'Season 09'), ({**D, 'season_folder': 'S{season:00}'}, 'S09')])
def test_a_new_folder_follows_the_setting_exactly_whatever_the_siblings(cfg, want):
    for sibs in ([], ['Season 01', 'Season 02'], ['Season 1', 'Season 2'], ['S01'], ['Extras', '.hidden']):
        assert tv_naming.season_folder(9, sibs, cfg) == want


def test_two_folders_for_one_season_prefer_the_formats_name():
    assert tv_naming.season_folder(8, ['Season 08', 'Season 8'], D) == 'Season 8'
    assert tv_naming.season_folder(8, ['Season 8', 'Season 08'], PAD) == 'Season 08'
    assert tv_naming.season_folder(8, ['S08', 'Series 8'], D) in ('S08', 'Series 8')


def test_specials():
    assert tv_naming.season_folder(0, ['Season 00', 'Season 01'], D) == 'Season 00'
    assert tv_naming.season_folder(0, ['specials'], D) == 'specials'
    assert tv_naming.season_folder(0, ['Season 01'], D) == 'Specials'
    assert tv_naming.season_folder(0, [], {**D, 'specials_folder': 'Season {season:00}'}) == 'Season 00'


def test_season_10_and_up_never_match_a_shorter_number():
    assert tv_naming.season_folder(10, ['Season 1', 'Season 01'], D) == 'Season 10'
    assert tv_naming.season_folder(1, ['Season 10', 'Season 11'], PAD) == 'Season 01'
