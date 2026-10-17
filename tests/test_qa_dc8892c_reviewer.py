"""QA (dc8892c) regression tests for Reviewer's added items.

(a) /api/sources/all-results with NO Luma trackers (the Prowlarr/Jackett
    indexer path): no field of any release may carry a Jackett API key
    (jackett_apikey=), a UNIT3D ID.RSSKEY download path, pk=, a short or UUID
    path passkey, or a passkey in a magnet's announce (tr=).
(b) An unapproved signed-in user calling all-results with no trackers gets 403
    and the indexer receives no call.
(c) An upstream that trickles its headers (and then its body) must be cut by
    outbound_guard.fetch's total deadline.
"""

import socket
import threading
import time
from urllib.parse import quote

import pytest

import app.main
import app.user_auth
from app import indexer_api, outbound_guard as og
from app import tracker_guard, tracker_search, tracker_store
from app.tracker_guard import TrackerGuard
from app.tracker_store import TrackerStore
from tests.test_tracker_sources import LAN, _owner


@pytest.fixture
def no_trackers(tmp_path):
    """An empty Settings > Trackers (so all-results takes the indexer path), real indexer clients."""
    tracker_store.reset_store_for_tests(TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key'))
    tracker_guard.reset_guard_for_tests(TrackerGuard(tmp_path / 'tracker_guard.json'))
    app.main._all_results_cache.clear()
    for c in (indexer_api._query_cache, indexer_api._status_cache, indexer_api._guard_log, indexer_api._guard_buckets,
              indexer_api._guard_cooldown, indexer_api._guard_streak, indexer_api._indexer_last_ts):
        c.clear()
    yield
    tracker_search.reset_search_for_tests(None)
    tracker_guard.reset_guard_for_tests(None)
    tracker_store.reset_store_for_tests(None)
    app.main._all_results_cache.clear()


@pytest.fixture
def signed_in(test_app, no_trackers):
    o = _owner()
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(o['id']))
    return c

JACKETT_KEY = 'qajk' + 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6'[:28]
RSSKEY = 'f00dfeedc0ffee1234567890abcdef12'
PK = 'pkQA77short'
SHORTPK = 'ab12cd34ef56'
UUIDPK = '3f2a9c1e-8b7d-4e5f-a1b2-c3d4e5f60718'
ANNPK = 'annQApasskey0099'
SECRETS = [JACKETT_KEY, RSSKEY, PK, SHORTPK, UUIDPK, ANNPK]
IH = 'a' * 40

FORMS = {
    'jackett_apikey': f'http://jackett.example:9117/dl/tz/?jackett_apikey={JACKETT_KEY}&path=Q2xI&file=Movie',
    'unit3d_rsskey': f'https://unit3d.example/torrent/download/12345.{RSSKEY}',
    'pk_query': f'https://t.example/download.php?id=7&pk={PK}',
    'short_path_passkey': f'https://t.example/rss/download/12345/{SHORTPK}/movie.torrent',
    'uuid_path_passkey': f'https://t.example/dl/12345/{UUIDPK}',
    'magnet_announce': f'magnet:?xt=urn:btih:{IH}&dn=Movie&tr=' + quote(f'https://t.example/announce/{ANNPK}', safe=''),
}


def _rss(link, guid, comments):
    esc = lambda s: s.replace('&', '&amp;')
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
            'xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><title>x</title>'
            f'<item><title>The Nut Job 2014 1080p WEB-DL x264-QA</title><guid>{esc(guid)}</guid>'
            f'<comments>{esc(comments)}</comments><link>{esc(link)}</link>'
            f'<torznab:attr name="magneturl" value="{esc(FORMS["magnet_announce"])}"/>'
            f'<torznab:attr name="details" value="{esc(comments)}"/>'
            '<size>1073741824</size><torznab:attr name="seeders" value="9"/></item></channel></rss>')


class _Resp:
    def __init__(self, text):
        self.text, self.status_code, self.content = text, 200, text.encode()

    def raise_for_status(self):
        pass

    def json(self):
        import json
        return json.loads(self.text)


class _Upstream:
    def __init__(self, body):
        self.body, self.calls = body, []

    def session(self):
        up = self

        class S:
            headers = {}

            def get(self, url, **kw):
                up.calls.append(url)
                return _Resp(up.body() if callable(up.body) else up.body)

            def close(self):
                pass
        return S


def _torznab(monkeypatch, body):
    up = _Upstream(body)
    monkeypatch.setattr(indexer_api.requests, 'Session', up.session())
    app.main.settings.update({'indexer': {'provider': 'torznab', 'api_key': 'qaIndexerKey123',
                                          'torznab_url': 'http://jackett.example:9117/api/v2.0/indexers/all/results/torznab/api'}})
    app.main._all_results_cache.clear()
    return up


def _prowlarr(monkeypatch, items):
    import json
    up = _Upstream(lambda: json.dumps(items))
    monkeypatch.setattr(indexer_api.requests, 'Session', up.session())
    app.main.settings.update({'indexer': {'provider': 'prowlarr', 'api_key': 'qaIndexerKey123',
                                          'base_url': 'http://prowlarr.example:9696'}})
    app.main._all_results_cache.clear()
    return up


def _leaks(text):
    return [s for s in SECRETS if s in text]


@pytest.mark.parametrize('form', sorted(FORMS))
def test_a_torznab_indexer_path_never_returns_a_key(signed_in, monkeypatch, form):
    url = FORMS[form]
    up = _torznab(monkeypatch, _rss(link=url, guid=url, comments=url))
    r = signed_in.get('/api/sources/all-results?title=The Nut Job&media_type=movie')
    assert r.status_code == 200 and r.get_json()['releases'], r.get_data(as_text=True)[:300]
    assert up.calls, 'indexer was not called'
    assert _leaks(r.get_data(as_text=True)) == [], (form, r.get_json()['releases'][0])


@pytest.mark.parametrize('form', sorted(FORMS))
def test_a_prowlarr_indexer_path_never_returns_a_key(signed_in, monkeypatch, form):
    url = FORMS[form]
    items = [{'title': 'The Nut Job 2014 1080p WEB-DL x264-QA', 'guid': url, 'downloadUrl': url, 'magnetUrl': FORMS['magnet_announce'],
              'infoUrl': url, 'commentUrl': url, 'indexer': 'QA', 'indexerId': 1, 'seeders': 9, 'size': 1073741824, 'protocol': 'torrent'}]
    up = _prowlarr(monkeypatch, items)
    r = signed_in.get('/api/sources/all-results?title=The Nut Job&media_type=movie')
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    rel = r.get_json().get('releases') or []
    assert up.calls and rel, (up.calls, r.get_data(as_text=True)[:300])
    assert _leaks(r.get_data(as_text=True)) == [], (form, rel[0])


def _unapproved(test_app):
    _owner()
    u = app.user_auth.create_user('memberu', 'pin-7654321', is_admin=False)
    app.user_auth.save_user_settings(u['id'], {'plex_access_status': 'pending'})
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(u['id']))
    return c


@pytest.mark.parametrize('kind', ['torznab', 'prowlarr'])
def test_b_unapproved_without_trackers_gets_403_and_no_indexer_call(test_app, no_trackers, monkeypatch, kind):
    c = _unapproved(test_app)
    up = _torznab(monkeypatch, _rss(FORMS['pk_query'], FORMS['pk_query'], '')) if kind == 'torznab' else \
        _prowlarr(monkeypatch, [{'title': 'The Nut Job 2014 1080p', 'guid': 'g', 'downloadUrl': FORMS['pk_query'], 'indexer': 'QA'}])
    r = c.get('/api/sources/all-results?title=The Nut Job&media_type=movie')
    assert r.status_code == 403, (r.status_code, r.get_data(as_text=True)[:200])
    assert up.calls == []


# --------------------------------------------- (a)/(b) Reviewer's B1 repro (fake client)

IXKEY = 'qaIxApiKey9f8e7d6c5b4a39281706'
RKEY = 'rQA5566778899aabbcc'
PATH20 = 'Qa20charPathKey12345'
assert len(PATH20) == 20
B1_LINKS = {
    'jackett_apikey': f'http://jackett.example:9117/dl/x/?jackett_apikey={JACKETT_KEY}&path=abc&file=Movie',
    'unit3d_rsskey': f'https://unit3d.example/torrent/download/123.{RSSKEY}',
    'pk_query': f'https://t.example/download.php?id=7&pk={PK}',
    'r_query': f'https://t.example/download.php?id=8&r={RKEY}',
    'path20': f'https://t.example/rss/download/123/{PATH20}/movie.torrent',
    'uuid_path': f'https://t.example/dl/123/{UUIDPK}',
    'leading_space': f' https://t.example/download.php?id=9&pk={PK}',
    'magnet_announce': f'magnet:?xt=urn:btih:{IH}&dn=Movie&tr=' + quote(f'https://t.example/announce/{ANNPK}', safe=''),
}
B1_SECRETS = [IXKEY, JACKETT_KEY, RSSKEY, PK, RKEY, PATH20, UUIDPK, ANNPK]


class _FakeIndexer:
    def __init__(self):
        self.calls = []

    def search_all(self, title, media_type, year=None):
        self.calls.append((title, media_type, year))
        return [{'title': f'Movie 2024 1080p WEB-DL {k}', 'guid': link, 'download_url': link, 'indexer': 'QA',
                 'indexer_id': 1, 'size': 1 << 30, 'seeders': 9, 'resolution': '1080p', 'source': 'WEB-DL',
                 'protocol': 'torrent'} for k, link in B1_LINKS.items()]


@pytest.fixture
def b1(test_app, no_trackers, monkeypatch):
    fake = _FakeIndexer()
    monkeypatch.setattr(indexer_api, 'get_indexer_client', lambda *_a, **_k: fake)
    monkeypatch.setattr(app.main._tracker_sources_mod(), 'trackers_in_use', lambda: False)
    real_get = app.main.settings.get
    monkeypatch.setattr(app.main.settings, 'get',
                        lambda key, *a, **kw: IXKEY if key == 'indexer.api_key' else real_get(key, *a, **kw))
    return fake


def test_b1_unapproved_lan_user_gets_403_and_no_search_all(test_app, b1):
    r = _unapproved(test_app).get('/api/sources/all-results?title=Movie&media_type=movie')
    assert r.status_code == 403, (r.status_code, r.get_data(as_text=True)[:200])
    assert b1.calls == []


@pytest.mark.parametrize('form', sorted(B1_LINKS))
def test_b1_owner_gets_opaque_links_and_no_secret(signed_in, b1, form):
    r = signed_in.get('/api/sources/all-results?title=Movie&media_type=movie')
    assert r.status_code == 200 and b1.calls
    rel = [x for x in r.get_json()['releases'] if x['title'].endswith(' ' + form)]
    assert rel, r.get_data(as_text=True)[:300]
    x = rel[0]
    for f in ('download_url', 'magnet_url', 'link', 'magnet'):
        v = (x.get(f) or '').strip()
        if v.lower().startswith(('http:', 'https:', 'magnet:')):
            raise AssertionError((form, f, 'not an opaque token', v[:60]))
    text = repr(x)
    assert [k for k in B1_SECRETS if k in text] == [], (form, {k: str(v)[:90] for k, v in x.items() if isinstance(v, str)})


# ------------------------------------------------------------------ (c)

def _serve(handler):
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(5)
    seen = []

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handler, args=(c, seen), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return srv, srv.getsockname()[1], seen


def _header_drip(c, seen):
    c.recv(4096)
    seen.append('request')
    head = b'HTTP/1.1 200 OK\r\n' + b''.join(b'X-Drip-%d: y\r\n' % i for i in range(10))
    try:
        for b in head:                 # valid header lines, one byte every 0.15 s (~25 s), never a blank line
            time.sleep(0.15)
            c.sendall(bytes([b]))
    except OSError:
        pass
    finally:
        c.close()


def _header_then_body_drip(c, seen):
    c.recv(4096)
    seen.append('request')
    try:
        for b in b'HTTP/1.1 200 OK\r\nContent-Type: application/x-bittorrent\r\n\r\n':
            time.sleep(0.1)
            c.sendall(bytes([b]))
        for _ in range(60):
            time.sleep(0.25)
            c.sendall(b'0')
    except OSError:
        pass
    finally:
        c.close()


def _allow_loopback(monkeypatch, names):
    real = socket.getaddrinfo

    def gai(host, port, *a, **k):
        if host in names:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
        return real(host, port, *a, **k)
    monkeypatch.setattr(og.socket, 'getaddrinfo', gai)
    monkeypatch.setattr(og, 'is_blocked_ip', lambda ip: False)


@pytest.mark.parametrize('handler', [_header_drip, _header_then_body_drip], ids=['headers', 'headers+body'])
def test_c_trickled_headers_hit_the_total_deadline(monkeypatch, handler):
    _allow_loopback(monkeypatch, {'drip.test'})
    srv, port, seen = _serve(handler)
    t = time.monotonic()
    try:
        with pytest.raises(og.FetchError):
            og.fetch(f'http://drip.test:{port}/x', read_timeout=1.0, connect_timeout=1.0, total_timeout=3.0)
    finally:
        took = time.monotonic() - t
        srv.close()
    assert seen == ['request']
    assert took < 5.0, f'held for {took:.1f}s with a 3 s total deadline'
