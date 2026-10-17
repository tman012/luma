"""#12 web kiosk home: minimal mode opens exactly one more endpoint, prod's
/api/hero/content (sign-in required), and nothing else the kiosk used."""

import pytest

import app.main
from tests.test_home_rails import REMOTE, _client

MOVIES = [{'rating_key': 100 + i, 'title': f'Movie {i}', 'year': 2026, 'summary': 'Plot.',
           'content_rating': 'PG-13', 'duration': 7200000, 'genres': ['Drama'],
           'backdrop_url': f'/api/img/plex?url=/library/metadata/{100 + i}/art/1'} for i in range(10)]


@pytest.fixture
def minimal_on(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><head></head><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))


def test_hero_content_is_the_only_new_kiosk_path():
    from app import minimal
    assert minimal.path_allowed('/api/hero/content', 'GET')
    assert '/api/hero/content' not in minimal.SIGNED_OUT_PATHS
    for off in ('/api/kiosk/settings', '/api/library/recent', '/api/img/tmdb', '/api/notifications',
                '/api/hero/refresh', '/api/screensaver/content', '/api/explore'):
        assert not minimal.path_allowed(off, 'GET'), off


def test_hero_content_needs_sign_in(test_app, minimal_on, monkeypatch):
    monkeypatch.setattr(app.main, '_build_hero_movies', lambda: (MOVIES, False))
    assert test_app.test_client().get('/api/hero/content', environ_base=REMOTE).status_code in (401, 302)


def test_hero_content_serves_the_selected_librarys_recent_movies(test_app, minimal_on, monkeypatch):
    monkeypatch.setattr(app.main, '_build_hero_movies', lambda: (MOVIES, False))
    r = _client(test_app).get('/api/hero/content', environ_base=REMOTE)
    assert r.status_code == 200
    body = r.json
    assert body['success'] is True and len(body['movies']) == 10
    assert body['movies'][0]['backdrop_url'].startswith('/api/img/plex?url=/library/metadata/')


def test_hero_limit_defaults_to_ten(test_app):
    from app.settings_manager import SettingsManager
    assert SettingsManager().get('ui.hero.limit', 10) == 10


def test_kiosk_settings_stays_off_in_minimal(test_app, minimal_on):
    assert _client(test_app).get('/api/kiosk/settings', environ_base=REMOTE).status_code == 404
