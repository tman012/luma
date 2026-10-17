"""QA regression tests for Reviewer's three #17 (16a326a) items.

1. A Torznab item's <guid>/<comments> carrying the tracker passkey must not
   reach the browser through /api/sources/all-results (owner or approved user).
2. /api/downloads/add with a pasted absolute URL (127.x, 10.x, 169.254.169.254,
   a LAN name) must never be fetched server-side, and a user who may not use
   trackers (signed in but not approved) must not push anything to qBittorrent.
3. A grab whose tracker link redirects to the metadata address, or to another
   site with a saved secret in the URL, gets 502 and sends nothing there.
   Pinned twice: on the engine's request seam, and through the REAL
   tracker_http.request (address checks on every hop) with
   tracker_http._pinned_session swapped for a real requests Session whose
   transport records every send (the env fixture swaps requests.Session for a
   fake qBittorrent, which would otherwise turn every tracker request into a
   fixture error that looks like a refusal).

Items 1 and 2 FAIL on 16a326a (they document the bugs); item 3 passes.
"""

import json

import pytest
import requests
import requests.adapters
import requests.sessions

import app.main
import app.user_auth
from app import tracker_clients, tracker_http, tracker_search
from tests.test_tracker_sources import (  # noqa: F401  (fixtures)
    ADDRS, APIKEY, DL, LAN, ORIGIN, PASSKEY, R, TMDB, _grab, _owner, _search, env, lan_anon, signed_in,
    torrent_bytes, torznab)

DETAILS = f'https://t.example/details.php?id=555&passkey={PASSKEY}'


def rss_with_details(title, link, guid=DETAILS, comments=DETAILS + '#comments'):
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
            'xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><title>x</title>'
            f'<item><title>{title}</title><guid>{guid.replace("&", "&amp;")}</guid>'
            f'<comments>{comments.replace("&", "&amp;")}</comments><link>{link.replace("&", "&amp;")}</link>'
            '<size>1073741824</size><torznab:attr name="seeders" value="42"/></item></channel></rss>')


def _member(test_app, approved):
    _owner()
    u = app.user_auth.create_user('member' + ('a' if approved else 'u'), 'pin-7654321', is_admin=False)
    if approved:
        app.user_auth.save_user_settings(u['id'], {'plex_access_status': 'approved'})
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(u['id']))
    return c


# ------------------------------------------------------------------ item 1

@pytest.mark.parametrize('where', ['guid', 'comments'])
def test_passkey_in_details_link_never_reaches_the_owner(signed_in, env, where):
    torznab(env['store'])
    guid = DETAILS if where == 'guid' else 'https://t.example/details.php?id=555'
    env['mock'].set('t.example', R(body=rss_with_details('The Nut Job 2014 1080p WEB-DL', DL, guid=guid,
                                                         comments=DETAILS if where == 'comments' else '')))
    r = signed_in.get('/api/sources/all-results?title=The Nut Job&media_type=movie')
    assert r.status_code == 200 and r.get_json()['releases'], r.get_data(as_text=True)
    assert PASSKEY not in r.get_data(as_text=True)


def test_passkey_in_details_link_never_reaches_an_approved_user(test_app, env):
    torznab(env['store'])
    c = _member(test_app, approved=True)
    env['mock'].set('t.example', R(body=rss_with_details('The Nut Job 2014 1080p WEB-DL', DL)))
    r = c.get('/api/sources/all-results?title=The Nut Job&media_type=movie')
    assert r.status_code == 200 and r.get_json()['releases']
    assert PASSKEY not in r.get_data(as_text=True)


# ------------------------------------------------------------------ item 2

INTERNAL = ['http://127.0.0.1:9696/x.torrent', 'http://10.0.0.5/x.torrent', 'http://169.254.169.254/latest/meta-data/',
            'http://192.168.1.20:8080/api/v2/app/preferences', 'http://[::1]:5001/x', 'http://lan.example/x.torrent']


@pytest.mark.parametrize('url', INTERNAL)
@pytest.mark.parametrize('approved', [False, True])
def test_pasted_internal_url_is_never_fetched_by_a_member(test_app, env, url, approved):
    c = _member(test_app, approved=approved)
    r = c.post('/api/downloads/add', headers=ORIGIN, content_type='application/json',
               data=json.dumps({'torrent_url': url, 'torrent_name': 'x'}))
    assert r.status_code in (400, 403), (r.status_code, r.get_data(as_text=True))
    assert env['qbit'].fetched == [] and env['qbit'].added == []


@pytest.mark.parametrize('url', INTERNAL)
def test_pasted_internal_url_is_never_fetched_for_the_owner(signed_in, env, url):
    r = _grab(signed_in, url)
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True))
    assert env['qbit'].fetched == [] and env['qbit'].added == []


def test_unapproved_user_cannot_push_a_magnet_to_qbittorrent(test_app, env):
    c = _member(test_app, approved=False)
    r = c.post('/api/downloads/add', headers=ORIGIN, content_type='application/json',
               data=json.dumps({'torrent_url': 'magnet:?xt=urn:btih:' + 'ab' * 20, 'torrent_name': 'x'}))
    assert r.status_code == 403
    assert env['qbit'].added == []


def test_anonymous_remote_gets_401_and_nothing_is_fetched(test_app, env):
    c = test_app.test_client()
    c.environ_base.update({'REMOTE_ADDR': '203.0.113.9'})
    r = c.post('/api/downloads/add', headers=ORIGIN, content_type='application/json',
               data=json.dumps({'torrent_url': 'http://169.254.169.254/latest/meta-data/'}))
    assert r.status_code == 401
    assert env['qbit'].fetched == [] and env['qbit'].added == []


# ------------------------------------------------------------------ item 3 (engine seam)

REDIRECTS = ['http://169.254.169.254/latest/meta-data/', 'https://meta.example/latest/meta-data/',
             'https://lan.example/x.torrent', f'https://cdn.example/dl?passkey={APIKEY}',
             f'https://cdn.example/dl?apikey={APIKEY}']


@pytest.mark.parametrize('loc', REDIRECTS)
def test_grab_redirect_to_metadata_or_secret_cross_site_is_refused(signed_in, env, loc):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss_with_details('The Nut Job 2014 1080p WEB-DL', DL, guid='g1', comments='')),
                    R(302, headers={'Location': loc}))
    env['mock'].set('cdn.example', R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 502
    # The code path ran: the tracker link itself was requested (and redirected) ...
    assert [c['url'] for c in env['mock'].hits('t.example')][-1] == DL
    # ... and nothing went anywhere else, nothing reached qBittorrent.
    assert env['mock'].hits('cdn.example') == [] and env['mock'].hits('169.254.169.254') == []
    assert env['qbit'].added == [] and APIKEY not in r.get_data(as_text=True)


# ------------------------------------------------------------------ item 3 (real tracker_http path)

class RecordingAdapter(requests.adapters.BaseAdapter):
    """Transport for the real request path: records each send, answers from a table."""

    def __init__(self, sent, table):
        super().__init__()
        self.sent, self.table = sent, table

    def send(self, request, **kw):
        self.sent.append(request.url)
        status, headers, body = self.table.get(request.url, (404, {}, b''))
        resp = requests.Response()
        resp.status_code, resp.url, resp.request = status, request.url, request
        resp.headers.update(headers)
        import io
        resp.raw = io.BytesIO(body)
        return resp

    def close(self):
        pass


@pytest.fixture
def real_http(env, monkeypatch):
    """Tracker requests go through the real tracker_http.request (address
    policy on every hop); only DNS and the socket are faked."""
    sent, table = [], {}
    pub = {'t.example': '93.184.216.34', 'cdn.example': '93.184.216.50', 'lan.example': '192.168.1.50',
           'meta.example': '169.254.169.254'}

    def resolve(host, port):
        h = host.strip('[]')
        try:
            import ipaddress
            return [str(ipaddress.ip_address(h))]
        except ValueError:
            return [pub[h]] if h in pub else []
    monkeypatch.setattr(tracker_http, 'resolve', resolve)

    def pinned(url, address):
        s = requests.sessions.Session()       # the real class, not the env fixture's fake qBittorrent
        s.trust_env = False
        a = RecordingAdapter(sent, table)
        s.mount('http://', a)
        s.mount('https://', a)
        return s
    monkeypatch.setattr(tracker_http, '_pinned_session', pinned)
    env['engine'].request = tracker_clients.http_request
    return sent, table


@pytest.mark.parametrize('loc,label', [
    ('http://169.254.169.254/latest/meta-data/', 'metadata literal'),
    ('https://meta.example/latest/meta-data/', 'name resolving to metadata'),
    ('http://127.0.0.1:7811/x', 'loopback'),
    ('http://10.0.0.5/x', 'private 10.x'),
    (f'https://cdn.example/dl?apikey={APIKEY}', 'cross-site hop carrying the saved key'),
])
def test_real_path_grab_redirect_is_refused_and_nothing_is_sent(signed_in, env, real_http, loc, label):
    sent, table = real_http
    torznab(env['store'])
    feed = rss_with_details('The Nut Job 2014 1080p WEB-DL', DL, guid='g2', comments='').encode()
    table.update({'https://t.example/api?t=search&q=The+Nut+Job&cat=2000%2C2010%2C2020%2C2030%2C2040%2C2045%2C2050%2C2060%2C2070%2C2080%2C5000%2C5010%2C5020%2C5030%2C5040%2C5045%2C5050%2C5060%2C5070%2C5080%2C5090&limit=500&apikey=' + APIKEY: (200, {}, feed)})
    # Any search URL shape: answer every t.example/api request with the feed.
    real_send = RecordingAdapter.send

    def send(self, request, **kw):
        if request.url.startswith('https://t.example/api'):
            self.sent.append(request.url)
            resp = requests.Response(); resp.status_code, resp.url, resp.request = 200, request.url, request
            import io
            resp.raw = io.BytesIO(feed)
            return resp
        return real_send(self, request, **kw)
    RecordingAdapter.send = send
    try:
        table[DL] = (302, {'Location': loc}, b'')
        table[loc] = (200, {}, torrent_bytes())
        rows = _search(signed_in)
        assert rows, 'search through the real path returned nothing'
        r = _grab(signed_in, rows[0]['download_url'])
    finally:
        RecordingAdapter.send = real_send
    assert r.status_code == 502, (label, r.status_code, r.get_data(as_text=True))
    assert DL in sent, f'{label}: the tracker link was never requested (fixture error, not a refusal)'
    assert loc not in sent and not any(u.startswith(('http://169.254', 'http://127.', 'http://10.', 'https://cdn.', 'https://meta.'))
                                        for u in sent), (label, sent)
    assert env['qbit'].added == []
