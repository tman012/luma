"""first_run.export_settings_to_env with a saved settings file (round 4, coverage b)."""

import json

import pytest

from app import first_run

KEYS = list(first_run._SETTINGS_ENV)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('LUMA_STATE_DIR', str(tmp_path))
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def _write(state, data):
    (state / 'settings.json').write_text(json.dumps(data))


def test_populated_file_fills_unset_env(state, monkeypatch):
    import os
    _write(state, {'apis': {'tmdb': {'api_key': '  tmdb-from-ui '}, 'omdb': {'api_key': 'omdb-from-ui'}},
                   'plex': {'url': 'http://plex.example:32400', 'token': 'tok', 'machine_id': 'mid'}})
    monkeypatch.setenv('OMDB_API_KEY', 'omdb-from-env')
    first_run.export_settings_to_env()
    assert os.environ['TMDB_API_KEY'] == 'tmdb-from-ui'          # trimmed
    assert os.environ['OMDB_API_KEY'] == 'omdb-from-env'         # env never overridden
    assert os.environ['PLEX_URL'] == 'http://plex.example:32400'
    assert os.environ['PLEX_TOKEN'] == 'tok'
    assert os.environ['PLEX_MACHINE_ID'] == 'mid'


def test_blank_and_non_string_values_ignored(state):
    import os
    _write(state, {'apis': {'tmdb': {'api_key': '   '}, 'omdb': 'not-a-dict'},
                   'plex': {'url': 123, 'token': None}})
    first_run.export_settings_to_env()
    for key in KEYS:
        assert key not in os.environ


@pytest.mark.parametrize('content', [None, '{not json'])
def test_missing_or_corrupt_file_is_a_no_op(state, content):
    import os
    if content is not None:
        (state / 'settings.json').write_text(content)
    first_run.export_settings_to_env()
    for key in KEYS:
        assert key not in os.environ
