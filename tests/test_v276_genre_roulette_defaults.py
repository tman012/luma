"""v276: Genre Roulette default is 3 movies / 3 genres; settings.json exposes the block."""
from app.genre_roulette import _DEFAULT_GENRES_PER_NIGHT, _DEFAULT_MOVIES_PER_NIGHT
from app.settings_manager import SETTINGS_SCHEMA, _build_defaults, _migrate_genre_roulette_block


def test_default_movies_per_night_is_three():
    assert _DEFAULT_MOVIES_PER_NIGHT == 3
    assert _DEFAULT_GENRES_PER_NIGHT == 3


def test_schema_exposes_genre_roulette_with_prod_defaults():
    gr = SETTINGS_SCHEMA['automation']['genre_roulette']
    assert gr['movies_per_night']['default'] == 3
    assert gr['genres_per_night']['default'] == 3
    assert gr['enabled']['default'] is False
    defaults = _build_defaults(SETTINGS_SCHEMA)['automation']['genre_roulette']
    assert defaults['movies_per_night'] == 3
    assert defaults['genres_per_night'] == 3


def test_migrate_seeds_missing_block_once():
    data = {'automation': {'movie_night': {'enabled': False}}}
    assert _migrate_genre_roulette_block(data) is True
    assert data['automation']['genre_roulette']['movies_per_night'] == 3
    assert data['automation']['genre_roulette']['genres_per_night'] == 3
    # second pass is a no-op (do not overwrite)
    data['automation']['genre_roulette']['movies_per_night'] = 9
    assert _migrate_genre_roulette_block(data) is False
    assert data['automation']['genre_roulette']['movies_per_night'] == 9
