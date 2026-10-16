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
    assert not str(app.hero_cache.CACHE_DIR).startswith('/app')
    assert not app.main.app.static_folder.startswith('/app')
