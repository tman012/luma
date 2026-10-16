"""Server-built /api/img URLs follow the URL base (round 5, item 2)."""

import re

import pytest

import app.main
import app.user_auth as user_auth

LAN = {'REMOTE_ADDR': '192.168.1.20'}
ITEMS = [{
    'rating_key': '1', 'title': 'A',
    'poster_url': '/api/img/plex?url=/library/metadata/1/thumb',
    'backdrop_url': '/api/img/plex?url=/library/metadata/1/art',
    'clearlogo_url': '/api/img/plex?url=/library/metadata/1/clearLogo',
    'still': {'image': '/api/img/tmdb?path=/s.jpg&size=w500'},
    'cast': [{'profile_url': 'https://image.tmdb.org/t/p/w185/x.jpg'}],
    'already': '/luma/api/img/plex?url=/x',
    'link': '/api/settings',
}]
_IMAGE_KEY = re.compile(r'(_url|image|poster|backdrop|thumb|logo)$', re.I)


@pytest.fixture
def client(test_app, monkeypatch):
    user_auth.create_user('owner', '1234', is_admin=True)
    monkeypatch.setattr(app.main.library_cache, 'needs_update', lambda *_: False)
    monkeypatch.setattr(app.main.library_cache, 'get_continue_watching', lambda limit=25: ITEMS)
    yield test_app.test_client()
    test_app.config['URL_BASE'] = ''


def _walk(value, key=''):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, key)
    else:
        yield key, value


def test_every_image_field_under_base(client, test_app):
    test_app.config['URL_BASE'] = '/luma'
    data = client.get('/api/plex/continue_watching/cached', environ_base=LAN).get_json()
    checked = 0
    for key, value in _walk(data):
        if _IMAGE_KEY.search(key) and isinstance(value, str) and value.startswith('/'):
            assert value.startswith('/luma/api/img/'), (key, value)
            checked += 1
    assert checked == 4
    item = next(i for i in data['items'] if i['title'] == 'A') if 'items' in data else None
    flat = dict(_walk(data))
    assert flat['already'] == '/luma/api/img/plex?url=/x'                      # not doubled
    assert flat['profile_url'].startswith('https://image.tmdb.org/')            # absolute untouched
    assert flat['link'] == '/api/settings'                                       # only image URLs
    assert item is None or item['poster_url'].startswith('/luma/')


def test_no_base_leaves_urls_alone(client):
    data = client.get('/api/plex/continue_watching/cached', environ_base=LAN).get_json()
    assert dict(_walk(data))['poster_url'] == '/api/img/plex?url=/library/metadata/1/thumb'


def test_cached_data_stays_root_relative(client, test_app):
    test_app.config['URL_BASE'] = '/luma'
    client.get('/api/plex/continue_watching/cached', environ_base=LAN)
    assert ITEMS[0]['poster_url'].startswith('/api/img/')
