"""v173/v174 (PM): Explore's movie categories live in Settings > Trackers > Search
(tracker_search.movie_categories), migrated from indexer.movie_categories with no loss. v174: the
never-wired max age, minimum seeders and result limit are gone (UI, schema, migration); a stale saved
value (under indexer or, from v173, under tracker_search) never changes Explore's results; v175 (Ops)
strips it at load (see test_v175_settings_migration.py)."""
import json

from app.settings_manager import SETTINGS_SCHEMA, SettingsManager, _migrate_tracker_search_settings

OLD = {'provider': 'prowlarr', 'base_url': 'http://192.168.1.30:9696', 'api_key': 'k' * 32,
       'movie_categories': '2000,2040', 'max_age_days': 45, 'min_seeders': 3, 'limit': 250}
GONE = ('max_age_days', 'min_seeders', 'limit')


def test_schema_keeps_only_movie_categories():
    ts = {k for k in SETTINGS_SCHEMA['tracker_search'] if not k.startswith('_')}
    assert ts == {'movie_categories'}
    assert 'movie_categories' not in SETTINGS_SCHEMA['indexer']
    for k in GONE:
        assert k not in SETTINGS_SCHEMA['indexer']


def test_an_upgrade_from_v172_moves_the_categories_and_tolerates_the_rest(tmp_path):
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'indexer': dict(OLD)}))
    sm = SettingsManager(str(p))
    assert sm.get('tracker_search.movie_categories') == '2000,2040'
    assert sm.get('indexer.base_url') == OLD['base_url'] and sm.get('indexer.api_key') == OLD['api_key']
    ok, errors = sm.update({'tracker_search': {'movie_categories': '2000,2045'}})
    assert ok, errors
    saved = json.loads(p.read_text())
    assert saved['tracker_search']['movie_categories'] == '2000,2045'
    assert 'movie_categories' not in saved['indexer']
    assert not any(k in saved['tracker_search'] for k in GONE)       # never migrated
    sm2 = SettingsManager(str(p))                                      # a restart: loads, nothing lost
    assert sm2.get('tracker_search.movie_categories') == '2000,2045'


def test_a_v173_file_with_the_moved_extras_loads_and_saves(tmp_path):
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'tracker_search': {'movie_categories': '2000,2040', 'max_age_days': 45,
                                                'min_seeders': 3, 'limit': 250}}))
    sm = SettingsManager(str(p))
    assert sm.get('tracker_search.movie_categories') == '2000,2040'
    ok, errors = sm.update({'tracker_search': {'movie_categories': '5000'}})
    assert ok, errors
    assert SettingsManager(str(p)).get('tracker_search.movie_categories') == '5000'


def test_fresh_default(tmp_path):
    sm = SettingsManager(str(tmp_path / 'settings.json'))
    assert sm.get('tracker_search.movie_categories') == '2000'
    saved = json.loads((tmp_path / 'settings.json').read_text()) if (tmp_path / 'settings.json').exists() else {}
    assert not any(k in (saved.get('tracker_search') or {}) for k in GONE)


def test_migration_moves_categories_strips_the_rest_and_an_already_moved_value_wins():
    data = {'indexer': {'movie_categories': '5000', 'limit': 120}, 'tracker_search': {'movie_categories': '2000,2045'}}
    assert _migrate_tracker_search_settings(data) is True
    assert data['tracker_search'] == {'movie_categories': '2000,2045'}
    assert data['indexer'] == {}                                     # v175 (Ops): stripped, not left behind
    assert _migrate_tracker_search_settings(data) is False


def test_nothing_reads_the_removed_settings():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    hits = []
    for f in (root / 'app').rglob('*.py'):
        t = f.read_text(encoding='utf-8')
        for k in GONE:
            for sec in ('indexer', 'tracker_search'):
                if f"'{sec}.{k}'" in t or f'"{sec}.{k}"' in t:
                    hits.append((f.name, sec, k))
    assert hits == []
    src = (root / 'app' / 'main.py').read_text()
    assert "indexer.movie_categories" not in src
    assert src.count("settings.get('tracker_search.movie_categories')") == 3


def _explore_once(monkeypatch, stale):
    import app.main
    seen = {}
    movies = [{'title': f'Movie Number {i}', 'year': 2000 + i % 20, 'seeders': i % 3, 'size': 10 ** 9 + i,
               'tracker': 'IPT', 'tmdb_id': str(1000 + i), 'poster': f'/p{i}.jpg',
               'publishDate': '2001-01-01T00:00:00Z'} for i in range(150)]

    class Client:
        def sweep_movies(self, categories='2000', **kw):
            seen['args'] = (categories, kw)
            return [dict(m) for m in movies]
    monkeypatch.setattr(app.main, 'get_indexer_client', lambda s: Client())
    real = app.main.settings.get
    vals = {'tracker_search.movie_categories': '2000,2045', 'apis.tmdb.api_key': ''}
    for k, v in stale.items():
        vals[f'tracker_search.{k}'] = vals[f'indexer.{k}'] = v
    monkeypatch.setattr(app.main.settings, 'get', lambda k, *a, **kw: vals[k] if k in vals else real(k, *a, **kw))
    out = {}
    monkeypatch.setattr(app.main.explore_cache, 'update_cache', lambda final: out.setdefault('final', final))
    app.main._update_explore_cache_inner()
    return seen['args'], out['final']


def test_stale_values_dont_change_explore(monkeypatch):
    """Explore's sweep gets the saved categories; stale max age / seeders / limit change nothing."""
    args0, final0 = _explore_once(monkeypatch, {})
    args1, final1 = _explore_once(monkeypatch, {'max_age_days': 1, 'min_seeders': 5000, 'limit': 10})
    assert args0 == args1 == ('2000,2045', {})
    assert len(final0) > 10 and final1 == final0
