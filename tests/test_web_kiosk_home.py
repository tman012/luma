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


class _OldPlex:
    plex_url = 'http://plex.kiosk-test'

    def health_check(self):
        return {'default_movie_section_id': 1}

    def get_recently_added(self, section_id=None, limit=10):
        old = 1_000_000_000  # 2001: far older than the 30-day window
        return [{'rating_key': 900 + i, 'title': f'Old {i}', 'type': 'movie', 'added_at': old - i,
                 'artPath': f'/library/metadata/{900 + i}/art/1', 'genres': []} for i in range(limit)]

    def get_all_movies(self, section_id, sort='addedAt:desc', limit=50, offset=0, genre=None, timeout=15):
        assert sort == 'addedAt:desc'
        old = 1_000_000_000
        movies = [{'rating_key': 900 + i, 'title': f'Old {i}', 'type': 'movie', 'added_at': old - i,
                   'artPath': f'/library/metadata/{900 + i}/art/1', 'genres': []} for i in range(limit)]
        return {'movies': movies, 'total': limit}


class _TwoRecentPlex(_OldPlex):
    """:5002's cause: Plex's section /recentlyAdded returned only 2 items, so the
    slideshow had almost nothing to rotate; the newest-by-date list has plenty,
    some without art."""
    def get_recently_added(self, section_id=None, limit=10):
        return super().get_recently_added(section_id, 2)

    def get_all_movies(self, section_id, sort='addedAt:desc', limit=50, offset=0, genre=None, timeout=15):
        out = super().get_all_movies(section_id, sort, limit)
        for m in out['movies'][:3]:
            m['artPath'] = None
        return out


def test_minimal_kiosk_uses_newest_by_date_not_plex_recently_added(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    monkeypatch.setattr(app.main, 'get_plex_for_request', lambda: _TwoRecentPlex())
    app.main._hero_result_cache.clear()
    with test_app.test_request_context('/api/hero/content'):
        movies, _ = app.main._build_hero_movies()
    app.main._hero_result_cache.clear()
    assert len(movies) == 10
    assert [m['title'] for m in movies] == [f'Old {i}' for i in range(3, 13)]   # art-less ones skipped
    assert all(m['backdrop_url'].endswith('&w=1920') for m in movies)            # no 8 MB originals


class _KioskPlex(_OldPlex):
    is_configured = True


def _kiosk_sections(_plex):
    return [{'id': '1', 'title': 'Movies', 'type': 'movie'}, {'id': '9', 'title': '4K Movies', 'type': 'movie'},
            {'id': '2', 'title': 'TV Shows', 'type': 'show'}]


def test_kiosk_library_setting(test_app, minimal_on, monkeypatch):
    from app import home_rails
    monkeypatch.setattr(app.main, 'get_plex_for_request', lambda: _KioskPlex())
    monkeypatch.setattr(home_rails, '_sections', _kiosk_sections)
    c = _client(test_app)
    r = c.get('/api/home/kiosk', environ_base=REMOTE)
    assert r.status_code == 200
    assert r.json['libraries'] == [{'id': '1', 'title': 'Movies'}, {'id': '9', 'title': '4K Movies'}]
    assert c.put('/api/home/kiosk', json={'id': '2'}, environ_base=REMOTE, headers={'Origin': 'http://localhost'}).status_code == 400   # not a movie library
    app.main._hero_result_cache['x'] = {'movies': [], 'ts': 0}
    r = c.put('/api/home/kiosk', json={'id': '9'}, environ_base=REMOTE, headers={'Origin': 'http://localhost'})
    assert r.status_code == 200 and r.json['id'] == '9'
    assert app.main.settings.get('ui.hero.library_section_id') == 9   # prod's hero setting, read by /api/hero/content
    assert app.main._hero_result_cache == {}
    assert c.get('/api/home/kiosk', environ_base=REMOTE).json['id'] == '9'
    viewer = _client(test_app, admin=False, name='viewer')
    assert viewer.get('/api/home/kiosk', environ_base=REMOTE).status_code == 403
    assert viewer.put('/api/home/kiosk', json={'id': '1'}, environ_base=REMOTE, headers={'Origin': 'http://localhost'}).status_code == 403
    assert test_app.test_client().get('/api/home/kiosk', environ_base=REMOTE).status_code in (401, 302)


def test_popup_read_paths_in_minimal():
    from app import minimal
    assert minimal.path_allowed('/api/plex/episode/123', 'GET')
    assert minimal.path_allowed('/settings/home-screen', 'GET')
    for off in ('/api/ratings/Heat', '/api/reviews/top', '/api/rt/reviews'):
        assert not minimal.path_allowed(off, 'GET'), off


@pytest.mark.parametrize('minimal, expected', [(True, 10), (False, 0)])
def test_minimal_kiosk_shows_newest_ten_regardless_of_age(test_app, monkeypatch, minimal, expected):
    if minimal:
        monkeypatch.setenv('LUMA_MINIMAL', '1')
    else:
        monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    monkeypatch.setattr(app.main, 'get_plex_for_request', lambda: _OldPlex())
    app.main._hero_result_cache.clear()
    with test_app.test_request_context('/api/hero/content'):
        movies, _ = app.main._build_hero_movies()
    app.main._hero_result_cache.clear()
    assert len(movies) == expected     # prod keeps its 30-day window
    if minimal:
        assert [m['title'] for m in movies] == [f'Old {i}' for i in range(10)]
