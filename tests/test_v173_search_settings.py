"""v173 (PM): Explore's four indexer settings move to Settings > Trackers as global search settings
(tracker_search.*); an upgrade carries the saved values over, nothing lost."""
import json

from app.settings_manager import SETTINGS_SCHEMA, SettingsManager, _migrate_tracker_search_settings

OLD = {'provider': 'prowlarr', 'base_url': 'http://192.168.1.30:9696', 'api_key': 'k' * 32,
       'movie_categories': '2000,2040', 'max_age_days': 45, 'min_seeders': 3, 'limit': 250}
KEYS = ('movie_categories', 'max_age_days', 'min_seeders', 'limit')


def test_schema_has_them_under_tracker_search_only():
    for k in KEYS:
        assert k in SETTINGS_SCHEMA['tracker_search'] and k not in SETTINGS_SCHEMA['indexer']


def test_an_upgrade_keeps_the_saved_values(tmp_path):
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'indexer': dict(OLD)}))
    sm = SettingsManager(str(p))
    for k in KEYS:
        assert sm.get(f'tracker_search.{k}') == OLD[k], k
    assert sm.get('indexer.base_url') == OLD['base_url'] and sm.get('indexer.api_key') == OLD['api_key']
    sm.update({'tracker_search': {'min_seeders': 4}})
    saved = json.loads(p.read_text())
    assert saved['tracker_search'] == {**{k: OLD[k] for k in KEYS}, 'min_seeders': 4}
    assert not any(k in saved['indexer'] for k in KEYS)
    sm2 = SettingsManager(str(p))                          # a restart: migrated once, nothing lost
    assert sm2.get('tracker_search.min_seeders') == 4 and sm2.get('tracker_search.limit') == 250


def test_a_fresh_install_gets_the_defaults(tmp_path):
    sm = SettingsManager(str(tmp_path / 'settings.json'))
    assert sm.get('tracker_search.movie_categories') == '2000'
    assert sm.get('tracker_search.max_age_days') == 30
    assert sm.get('tracker_search.min_seeders') == 1
    assert sm.get('tracker_search.limit') == 100


def test_a_value_already_moved_wins_and_partial_old_sets_move():
    data = {'indexer': {'movie_categories': '5000', 'limit': 120}, 'tracker_search': {'movie_categories': '2000,2045'}}
    assert _migrate_tracker_search_settings(data) is True
    assert data['tracker_search'] == {'movie_categories': '2000,2045', 'limit': 120}
    assert data['indexer'] == {}
    assert _migrate_tracker_search_settings(data) is False


def test_explore_and_search_read_the_moved_categories():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / 'app' / 'main.py').read_text()
    assert "indexer.movie_categories" not in src
    assert src.count("settings.get('tracker_search.movie_categories')") == 3


def test_explore_sweep_uses_the_saved_categories(monkeypatch):
    import app.main
    seen = {}

    class Client:
        def sweep_movies(self, categories='2000', **kw):
            seen['categories'] = categories
            return []
    monkeypatch.setattr(app.main, 'get_indexer_client', lambda s: Client())
    real = app.main.settings.get
    monkeypatch.setattr(app.main.settings, 'get',
                        lambda k, *a, **kw: '2000,2045' if k == 'tracker_search.movie_categories' else real(k, *a, **kw))
    app.main._update_explore_cache_inner()
    assert seen['categories'] == '2000,2045'
