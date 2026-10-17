"""Tester probes: path traversal through /api/img/plex. Only Plex library
image shapes reach Plex; anything else is refused before any upstream call."""

from types import SimpleNamespace

import pytest

import app.main

LAN = {'REMOTE_ADDR': '192.168.1.20'}


@pytest.fixture
def proxy(test_app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.main, 'get_plex_for_request',
                        lambda: SimpleNamespace(plex_url='http://plex.test', plex_token='owner'))

    class R:
        status_code = 200
        content = b'img'
        headers = {'Content-Type': 'image/jpeg'}

    monkeypatch.setattr(app.main.requests, 'get', lambda url, **k: calls.append(url) or R())
    return test_app.test_client(), calls


PROBES = [
    '/library/../:/prefs',
    '/library/%2e%2e/canary',
    '/library/%252e%252e/canary',
    '/library/../identity',
    '/library/metadata/1/thumb/../../../:/prefs',
    '/library/metadata/1/thumb/%2e%2e/%2e%2e/identity',
    '/library/metadata/1/thumb\\..\\..\\identity',
    '/library//metadata/1/thumb',
    '//plex.evil/library/metadata/1/thumb',
    'http://evil.example/library/metadata/1/thumb',
    '/library/metadata/1/thumb?X-Plex-Token=x',
    '/library/metadata/1/thumb#x',
    '/library/metadata/\u0661/thumb',   # non-ASCII digit
    '/:/prefs',
    '/identity',
    '/photo/:/transcode?url=/library/../:/prefs',
    '/photo/:/transcode?url=%2Flibrary%2F..%2Fidentity',
]


@pytest.mark.parametrize('probe', PROBES)
def test_traversal_probes_never_reach_plex(proxy, probe):
    c, calls = proxy
    r = c.get('/api/img/plex', query_string={'url': probe}, environ_base=LAN)
    assert r.status_code == 400
    assert calls == []


@pytest.mark.parametrize('raw_qs', [
    'url=/library/../:/prefs', 'url=/library/%2e%2e/canary', 'url=/library/../identity',
    'url=%2Flibrary%2F%2e%2e%2Fcanary',
])
def test_tester_query_strings_verbatim(proxy, raw_qs):
    c, calls = proxy
    r = c.get(f'/api/img/plex?{raw_qs}', environ_base=LAN)
    assert r.status_code == 400 and calls == []


@pytest.mark.parametrize('ok', [
    '/library/metadata/91164/thumb/1792125159', '/library/metadata/9/art/1792125159',
    '/library/metadata/9/thumb', '/library/collections/5/thumb/1', '/library/sections/2/composite/3',
])
def test_real_image_shapes_still_load(proxy, ok):
    c, calls = proxy
    r = c.get('/api/img/plex', query_string={'url': ok}, environ_base=LAN)
    assert r.status_code == 200 and r.headers['Content-Type'] == 'image/jpeg'
    assert calls == [f'http://plex.test{ok}']
    assert 'private' in r.headers['Cache-Control']


def test_non_image_upstream_is_502(test_app, monkeypatch):
    monkeypatch.setattr(app.main, 'get_plex_for_request',
                        lambda: SimpleNamespace(plex_url='http://plex.test', plex_token='owner'))

    class R:
        status_code = 200
        content = b'<xml/>'
        headers = {'Content-Type': 'text/xml'}

    monkeypatch.setattr(app.main.requests, 'get', lambda url, **k: R())
    r = test_app.test_client().get('/api/img/plex?url=/library/metadata/1/thumb', environ_base=LAN)
    assert r.status_code == 502
