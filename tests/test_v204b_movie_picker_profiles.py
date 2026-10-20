"""v204: the Add to watchlist picker lists Luma's own movie profiles (movies.db), TFlix first, filtered by
Settings > Movies 'Watchlist picker'; never Radarr."""
import pytest

from app import movie_settings as ms


@pytest.fixture
def movies_db(tmp_path, monkeypatch):
    monkeypatch.setitem(ms._cfg, 'db_path', str(tmp_path / 'mdb' / 'movies.db'))
    yield


def _mk(name, default=False):
    for p in ms.profiles():
        if p['name'] == name:
            return p['id']
    items = [{'id': q, 'allowed': True} for q in ms.quality_ids()[:1]]
    p = ms.save_profile({'name': name, 'items': items, 'cutoff': items[0]['id'], 'min_mb_per_min': None,
                         'max_mb_per_min': None, 'default': default})
    return p['id']


def test_picker_profiles(movies_db):
    t, z = _mk('TFlix', True), _mk('Zed')
    assert [p['name'] for p in ms.picker_profiles()][0] == 'TFlix'
    assert len(ms.picker_profiles()) >= 2
    assert ms.apply_put({'picker_profiles': [z, t]}) == {}
    assert [p['name'] for p in ms.picker_profiles()] == ['TFlix', 'Zed']
    assert ms.view()['picker_profiles'] == [z, t]
    assert ms.apply_put({'picker_profiles': [999999]}) == {'picker_profiles': 'Invalid'}
    assert ms.apply_put({'picker_profiles': []}) == {}
    assert len(ms.picker_profiles()) >= 2


def test_route_never_radarr():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / 'app' / 'movie_watchlist.py').read_text()
    i = src.index("'/api/movies/watchlist/profiles'")
    assert 'get_radarr' not in src[i:i + 400] and 'radarr_api' not in src and 'ms.picker_profiles()' in src[i:i + 400]
