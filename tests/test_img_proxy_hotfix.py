"""Prod hotfix: /api/img/plex only proxies Plex library image paths, only
answers with images, and never lets a shared cache keep a reply (served
images are private to this browser for a day; errors are never stored)."""

from types import SimpleNamespace

import pytest

import app.main

LAN = {'REMOTE_ADDR': '192.168.1.20'}


class _Resp:
    def __init__(self, status=200, ctype='image/jpeg', chunks=(b'img',), headers=None):
        self.status_code = status
        self.chunks = list(chunks)
        self.headers = {'Content-Type': ctype} if ctype is not None else {}
        self.headers.update(headers or {})
        self.closed = False

    def iter_content(self, chunk_size=1):
        yield from self.chunks

    def close(self):
        self.closed = True


@pytest.fixture
def plex(test_app, monkeypatch):
    calls = []
    state = {'resp': _Resp()}
    monkeypatch.setattr(app.main, 'get_plex_for_request',
                        lambda: SimpleNamespace(plex_url='http://plex.test', plex_token='t'))

    def fake_get(url, **k):
        calls.append(url)
        state['kwargs'] = k
        return state['resp']
    monkeypatch.setattr(app.main.requests, 'get', fake_get)
    return test_app.test_client(), calls, state


def _get(client, query):
    return client.get('/api/img/plex?' + query, environ_base=LAN)


@pytest.mark.parametrize('query', [
    'url=/library/metadata/1/../../../identity',
    'url=/library/metadata/1/thumb/..',
    'url=/library/%2e%2e/accounts',
    'url=/library/%2E%2E/accounts',
    'url=/library/metadata/1/thumb%2F..%2F..%2Faccounts',
    'url=%252Flibrary%252Fmetadata%252F1%252Fthumb',
    'url=/library/%252e%252e/accounts',
    'url=/library/metadata/1/thumb%5C..%5Caccounts',
    'url=/library/metadata/1\\thumb',
    'url=/library/sections',
    'url=/library/metadata/1',
    'url=/library/metadata/1/thumb/1/extra',
    'url=/library/metadata/1/file',
    'url=/library/metadata/%D9%A1/thumb',
    'url=/library/metadata/1/thumb%0A',
    'url=/photo/:/transcode?url=/accounts',
    'url=/photo/:/transcode%3Furl%3D%252Flibrary%252F..%252Faccounts',
    'url=/photo/:/transcode%3Furl%3D/library/metadata/1/thumb%26url%3D/accounts',
    'url=/accounts',
    'url=/identity',
    'url=http://evil.test/library/metadata/1/thumb',
    'url=//evil.test/library/metadata/1/thumb',
    'url=',
])
def test_traversal_and_unknown_paths_refused(plex, query):
    client, calls, _ = plex
    r = _get(client, query)
    assert r.status_code == 400
    assert calls == []
    assert r.headers['Cache-Control'] == 'private, no-store'


@pytest.mark.parametrize('path, upstream', [
    ('/library/metadata/12345/thumb', '/library/metadata/12345/thumb'),
    ('/library/metadata/12345/thumb/1700000000', '/library/metadata/12345/thumb/1700000000'),
    ('/library/metadata/12345/art/1700000000', '/library/metadata/12345/art/1700000000'),
    ('/library/metadata/12345/clearLogo/1700000000', '/library/metadata/12345/clearLogo/1700000000'),
    ('/library/metadata/12345/banner', '/library/metadata/12345/banner'),
    ('/library/collections/77/composite/1700000000', '/library/collections/77/composite/1700000000'),
    ('/library/sections/2/composite/1700000000', '/library/sections/2/composite/1700000000'),
    ('%2Flibrary%2Fmetadata%2F12345%2Fthumb%2F1700000000', '/library/metadata/12345/thumb/1700000000'),
    ('/photo/:/transcode%3Furl%3D%252Flibrary%252Fmetadata%252F5%252Fthumb', '/library/metadata/5/thumb'),
])
def test_valid_plex_image_shapes_allowed(plex, path, upstream):
    client, calls, _ = plex
    r = _get(client, 'url=' + path + '&r=2')
    assert r.status_code == 200
    assert r.data == b'img'
    assert calls == ['http://plex.test' + upstream]
    assert r.headers['Content-Type'] == 'image/jpeg'
    assert r.headers['Cache-Control'] == 'private, max-age=86400'
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'ETag' not in r.headers


def test_resize_still_goes_through_transcoder(plex):
    client, calls, _ = plex
    r = _get(client, 'url=/library/metadata/5/thumb/9&w=300')
    assert r.status_code == 200
    assert calls[0].startswith('http://plex.test/photo/:/transcode?')
    assert 'url=%2Flibrary%2Fmetadata%2F5%2Fthumb%2F9' in calls[0]


@pytest.mark.parametrize('ctype', [
    'text/html', 'text/html; charset=utf-8', 'application/json', 'application/xml',
    'image/svg+xml', 'image/svg+xml; charset=utf-8', '', None, 'image/', 'imagex/png',
    'image/x-icon', 'image/bmp', 'image/tiff', 'image/jpeg2',
])
def test_non_image_upstream_refused(plex, ctype):
    client, _, state = plex
    state['resp'] = _Resp(200, ctype)
    r = _get(client, 'url=/library/metadata/1/thumb')
    assert r.status_code == 502
    assert b'img' not in r.data
    assert not r.headers['Content-Type'].startswith(('text/html', 'application/json', 'application/xml', 'image/svg'))
    assert r.headers['Cache-Control'] == 'private, no-store'


@pytest.mark.parametrize('ctype, out', [
    ('image/png', 'image/png'), ('IMAGE/WEBP', 'image/webp'), ('image/jpeg; charset=binary', 'image/jpeg'),
    ('image/gif', 'image/gif'), ('image/avif', 'image/avif'),
])
def test_image_type_forced_and_normalized(plex, ctype, out):
    client, _, state = plex
    state['resp'] = _Resp(200, ctype)
    r = _get(client, 'url=/library/metadata/1/thumb')
    assert r.status_code == 200
    assert r.headers['Content-Type'] == out


@pytest.mark.parametrize('status', [401, 404, 500])
def test_upstream_errors_never_publicly_cached(plex, status):
    client, _, state = plex
    state['resp'] = _Resp(status, 'text/html')
    r = _get(client, 'url=/library/metadata/1/thumb')
    assert r.status_code == 404
    assert r.headers['Cache-Control'] == 'private, no-store'


def test_upstream_exception_never_publicly_cached(plex, monkeypatch):
    client, _, _ = plex

    def boom(*a, **k):
        raise app.main.requests.ConnectionError('down')
    monkeypatch.setattr(app.main.requests, 'get', boom)
    r = _get(client, 'url=/library/metadata/1/thumb')
    assert r.status_code == 502
    assert r.headers['Cache-Control'] == 'private, no-store'

@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_redirect_never_followed(plex, status):
    """A Plex that redirects elsewhere must not get the token sent on."""
    client, calls, state = plex
    state['resp'] = _Resp(status, 'text/html', headers={'Location': 'http://evil.test/steal'})
    r = _get(client, 'url=/library/metadata/1/thumb')
    assert r.status_code == 502
    assert state['kwargs']['allow_redirects'] is False
    assert calls == ['http://plex.test/library/metadata/1/thumb']
    assert 'Location' not in r.headers
    assert r.headers['Cache-Control'] == 'private, no-store'


def test_redirect_with_real_requests_not_followed(test_app, monkeypatch):
    """Same, through the real requests library against a fake Plex that redirects."""
    import requests
    from requests.adapters import BaseAdapter
    from requests.models import Response as RResponse

    seen = []

    class FakePlex(BaseAdapter):
        def send(self, request, **kwargs):
            seen.append((request.url, request.headers.get('X-Plex-Token')))
            r = RResponse()
            r.request, r.url, r.raw = request, request.url, None
            r._content, r._content_consumed = b'', True
            if request.url.startswith('http://plex.test/'):
                r.status_code = 302
                r.headers['Location'] = 'http://evil.test/steal'
            else:
                r.status_code = 200
                r.headers['Content-Type'] = 'image/jpeg'
                r._content = b'stolen'
            return r

        def close(self):
            pass

    def get_via_fake(url, **k):
        with requests.Session() as s:
            s.mount('http://', FakePlex())
            return s.get(url, **k)
    monkeypatch.setattr(app.main.requests, 'get', get_via_fake)
    monkeypatch.setattr(app.main, 'get_plex_for_request',
                        lambda: SimpleNamespace(plex_url='http://plex.test', plex_token='tok'))
    r = test_app.test_client().get('/api/img/plex?url=/library/metadata/1/thumb', environ_base=LAN)
    assert r.status_code == 502
    assert [u for u, _ in seen] == ['http://plex.test/library/metadata/1/thumb']


def test_oversized_declared_length_refused(plex):
    client, _, state = plex
    state['resp'] = _Resp(200, headers={'Content-Length': str(20 * 1024 * 1024 + 1)})
    r = _get(client, 'url=/library/metadata/1/thumb')
    assert r.status_code == 502
    assert state['resp'].closed
    assert r.headers['Cache-Control'] == 'private, no-store'


def test_oversized_stream_aborted(plex):
    client, _, state = plex
    mb = b'x' * (1024 * 1024)
    pulled = []

    def chunks():
        for _ in range(40):
            pulled.append(1)
            yield mb
    state['resp'] = _Resp(200)
    state['resp'].iter_content = lambda chunk_size=1: chunks()
    r = _get(client, 'url=/library/metadata/1/thumb')
    assert r.status_code == 502
    assert len(pulled) == 21
    assert state['resp'].closed


def test_image_at_cap_served(plex):
    client, _, state = plex
    state['resp'] = _Resp(200, chunks=[b'x' * (20 * 1024 * 1024)])
    r = _get(client, 'url=/library/metadata/1/thumb')
    assert r.status_code == 200
    assert len(r.data) == 20 * 1024 * 1024


def test_plex_not_configured_never_stored(test_app, monkeypatch):
    monkeypatch.setattr(app.main, 'get_plex_for_request', lambda: None)
    monkeypatch.setattr(app.main.settings, 'get', lambda *a, **k: None)
    r = test_app.test_client().get('/api/img/plex?url=/library/metadata/1/thumb', environ_base=LAN)
    assert r.status_code == 503
    assert r.headers['Cache-Control'] == 'private, no-store'
