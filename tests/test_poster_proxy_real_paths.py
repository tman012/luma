"""Posters on :5002 404'd: Home asks /api/img/plex with w only, and Plex's
photo transcoder needs both width and height. These use the exact URL shapes
seen in the luma-test log."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import app.main

LAN = {'REMOTE_ADDR': '192.168.1.20'}


class _Resp:
    def __init__(self, status=200, ctype='image/jpeg'):
        self.status_code = status
        self.content = b'img'
        self.headers = {'Content-Type': ctype}

    def iter_content(self, chunk_size=1):
        yield self.content

    def close(self):
        pass


@pytest.fixture
def plex(test_app, monkeypatch):
    calls = []
    state = {'transcode_status': 200}
    monkeypatch.setattr(app.main, 'get_plex_for_request',
                        lambda: SimpleNamespace(plex_url='http://plex.test', plex_token='t'))

    def fake_get(url, **k):
        calls.append(url)
        if '/photo/:/transcode' in url:
            q = parse_qs(urlsplit(url).query)
            if 'width' not in q or 'height' not in q:
                return _Resp(400, 'text/plain')     # what Plex does without both
            return _Resp(state['transcode_status'])
        return _Resp()
    monkeypatch.setattr(app.main.requests, 'get', fake_get)
    return test_app.test_client(), calls, state


# Exact query strings from the luma-test log (Home poster and Continue art).
REAL = [
    'url=%2Flibrary%2Fmetadata%2F91164%2Fthumb%2F1792125159&w=300',
    'url=%2Flibrary%2Fmetadata%2F91160%2Fthumb%2F1792119905&w=300',
    'url=%2Flibrary%2Fmetadata%2F9%2Fart%2F1792125159&w=480',
    'url=%2Flibrary%2Fmetadata%2F9%2Fart%2F1792125159&w=1920',
]


@pytest.mark.parametrize('qs', REAL)
def test_real_home_image_urls_load(plex, qs):
    c, calls, _ = plex
    r = c.get(f'/api/img/plex?{qs}', environ_base=LAN)
    assert r.status_code == 200 and r.headers['Content-Type'].startswith('image/')
    q = parse_qs(urlsplit(calls[0]).query)
    w = int(parse_qs(qs)['w'][0])
    assert q['width'] == [str(w)] and q['height'] == [str(min(2000, w * 2))] and q['minSize'] == ['0']
    assert q['url'][0].startswith('/library/metadata/')


def test_width_and_height_keep_cover_mode(plex):
    c, calls, _ = plex
    c.get('/api/img/plex?url=%2Flibrary%2Fmetadata%2F5%2Fthumb%2F1&w=200&h=300', environ_base=LAN)
    q = parse_qs(urlsplit(calls[0]).query)
    assert q['width'] == ['200'] and q['height'] == ['300'] and q['minSize'] == ['1']


def test_falls_back_to_the_original_when_transcoder_refuses(plex):
    c, calls, state = plex
    state['transcode_status'] = 500
    r = c.get('/api/img/plex?url=%2Flibrary%2Fmetadata%2F5%2Fthumb%2F1&w=300', environ_base=LAN)
    assert r.status_code == 200
    assert calls[-1] == 'http://plex.test/library/metadata/5/thumb/1'


def test_outside_urls_still_refused(plex):
    c, calls, _ = plex
    for u in ('http://evil.example/x.jpg', '/photo/:/transcode?url=http://evil.example/x.jpg&width=1&height=1'):
        assert c.get('/api/img/plex', query_string={'url': u, 'w': 300}, environ_base=LAN).status_code == 400
    assert calls == []
