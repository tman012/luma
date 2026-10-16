"""A bare `pytest` run never touches the container-only /app paths (round 4, item 11)."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_conftest_sets_paths_before_app_import():
    assert not os.environ['HERO_CACHE_DIR'].startswith('/app')
    assert Path(os.environ['FLASK_STATIC_FOLDER']).resolve() == (ROOT / 'static').resolve() \
        or not os.environ['FLASK_STATIC_FOLDER'].startswith('/app')


def test_app_uses_them():
    import app.hero_cache
    import app.main
    assert not str(app.hero_cache.hero_cache.cache_dir).startswith('/app')
    assert not str(app.hero_cache.hero_cache.cache_json).startswith('/app')
    assert not app.main.app.static_folder.startswith('/app')


def test_library_cache_constructs(tmp_path, monkeypatch):
    monkeypatch.setenv('LUMA_CACHE_DIR', str(tmp_path))
    from app.library_cache import LibraryCache
    LibraryCache()   # regression: module was missing `import os`
