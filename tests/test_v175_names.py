"""v175 (Designer): one sentence-case name for each feature on every chip and label."""
import re

from app import grab_origin


def test_origin_chip_labels_use_the_feature_names():
    L = grab_origin.LABELS
    assert L['genre_roulette'] == 'Genre roulette'
    assert L['franchise'] == 'Franchise completer'
    assert L['movie_night'] == 'Movie night'
    assert L['now_available'] == 'Now available'
    for v in L.values():
        assert not re.search(r'Movie Night|Genre Roulette|Franchise Completer|Downloads Guard', v)


def test_user_facing_error_details_use_the_feature_names():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / 'app'
    src = (root / 'franchise_completer.py').read_text()
    assert 'Franchise completer quality profile is not set' in src
