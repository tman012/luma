"""Minimal mode: tapping a Home card opens main's detail popup (read-only
endpoints, GET only, sign-in required); /login is the SPA's one-page form;
Home tells "Can't reach Plex" apart from an empty library."""

import xml.etree.ElementTree as ET

import pytest

from tests.test_home_rails import REMOTE, FakePlex, _client, plex  # noqa: F401

DETAIL = ['/api/plex/movie/11', '/api/plex/tv/9', '/api/movie/603', '/api/tv/1399',
          '/api/plex/ratings/11', '/api/plex/ultrablur/11', '/api/tmdb/colors/603',
          '/api/clearlogo/603', '/api/ratings/Dune', '/api/rt/reviews', '/api/plex/lookup']


@pytest.fixture
def minimal_on(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><head></head><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))


@pytest.mark.parametrize('path', DETAIL)
def test_detail_reads_allowed_get_only(path):
    from app import minimal
    assert minimal.path_allowed(path, 'GET')
    for m in ('POST', 'PUT', 'DELETE', 'PATCH'):
        assert not minimal.path_allowed(path, m)
    assert path not in minimal.SIGNED_OUT_PATHS


@pytest.mark.parametrize('path', ['/api/radarr/add', '/api/sonarr/series', '/api/grab', '/api/request/x',
                                  '/api/plex/play', '/api/movie/../radarr/add', '/api/download/1'])
def test_write_and_other_routes_stay_off(path):
    from app import minimal
    assert not minimal.path_allowed(path, 'GET')


def test_detail_reads_need_sign_in(test_app, minimal_on):
    c = test_app.test_client()
    for p in DETAIL[:4]:
        assert c.get(p, environ_base=REMOTE).status_code in (401, 302)
    assert c.post('/api/plex/movie/11', environ_base=REMOTE).status_code == 404


def test_login_serves_spa_one_page_form(test_app, minimal_on):
    c = test_app.test_client()
    r = c.get('/login?next=/settings/libraries', environ_base=REMOTE)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="root"' in html and '__LUMA_MINIMAL__' in html
    assert 'Remember me' not in html and 'csrf_token' not in html
    assert 'no-store' in r.headers['Cache-Control']
    # A stale form post (session expired) goes back to the same screen.
    r = c.post('/login?next=/x', data={'username': 'a', 'password': 'b'}, environ_base=REMOTE)
    assert r.status_code == 302 and '/login' in r.headers['Location'] and 'next=' in r.headers['Location']


def test_login_unchanged_outside_minimal(test_app, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    html = test_app.test_client().get('/login', environ_base=REMOTE).get_data(as_text=True)
    assert 'csrf_token' in html


def test_home_says_plex_down_when_plex_does_not_answer(test_app, plex):
    plex._get = lambda path, params=None, timeout=15: None
    r = _client(test_app).get('/api/home', environ_base=REMOTE)
    assert r.json == {'continue_watching': [], 'rails': [], 'plex': False}


def test_cards_carry_show_key_for_the_popup(test_app, plex):
    import tests.test_home_rails as t
    old = t.ON_DECK
    t.ON_DECK = old.replace('ratingKey="10"', 'ratingKey="10" grandparentRatingKey="9"')
    try:
        cw = _client(test_app).get('/api/home', environ_base=REMOTE).json['continue_watching']
    finally:
        t.ON_DECK = old
    assert cw[0]['show_key'] == '9' and cw[0]['season'] == 1 and cw[0]['episode'] == 2
    assert 'show_key' not in cw[1]
