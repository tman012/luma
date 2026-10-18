"""PR3: Sources and grab from Luma's own trackers (Settings > Trackers).

Mocked trackers and a mocked qBittorrent only. The legacy indexer clients
are rigged to fail, so every passing test proves the direct path."""

import hashlib
from xml.sax.saxutils import escape
import json
import time

import pytest

import app.main
import app.user_auth
from app import indexer_api, tracker_clients, tracker_http, tracker_guard, tracker_routes, tracker_search, tracker_sources, tracker_store
from app.tracker_guard import TrackerGuard, guard_key, ip_key
from app.tracker_http import Response
from app.tracker_search import ReleaseTokens, TrackerSearch
from app.tracker_store import TrackerStore

LAN = {'REMOTE_ADDR': '192.168.1.20'}
ORIGIN = {'Origin': 'http://localhost'}
PASSKEY = 'pk9f8e7d6c5b4a39281706f5e4d3c2b1a0'
APIKEY = 'ak0123456789abcdef0123456789abcdef'
COOKIE = 'uid=1234567; pass=abcdefabcdefabcdefabcdefabcdef12'
QBIT = {'qbittorrent.url': 'http://qbit.invalid:8080', 'qbittorrent.username': 'u',
        'qbittorrent.password': 'p'}
ADDRS = {'t.example': ['93.184.216.34'], 'ipt.example': ['93.184.216.40'],
         'cdn.example': ['93.184.216.50'], 'lan.example': ['192.168.1.50'],
         'meta.example': ['169.254.169.254']}
TMDB = {'tmdb_id': 284836, 'media_type': 'movie', 'title': 'The Nut Job', 'year': 2014}


def torrent_bytes(name=b'The.Nut.Job.2014.1080p'):
    info = b'd6:lengthi1024e4:name' + str(len(name)).encode() + b':' + name + \
        b'12:piece lengthi16384e6:pieces20:' + b'\x01' * 20 + b'e'
    return b'd8:announce' + str(len(f'https://t.example/a/{PASSKEY}')).encode() + b':' + \
        f'https://t.example/a/{PASSKEY}'.encode() + b'4:info' + info + b'e'


def R(status=200, body=b'', headers=None):
    if isinstance(body, str):
        body = body.encode()
    return Response(status, headers or {}, body)


def rss(*items):
    out = []
    for title, link in items:
        out.append(f'<item><title>{title}</title><guid>https://t.example/details/{abs(hash(title))}</guid>'
                   f'<link>{escape(link)}</link><size>1073741824</size>'
                   f'<torznab:attr name="seeders" value="42"/><torznab:attr name="peers" value="50"/></item>')
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
            'xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><title>x</title>'
            + ''.join(out) + '</channel></rss>')


IPT_HTML = """<html><body><table id="torrents">
<tr><th>Type</th><th>Name</th></tr>
<tr><td><a href="/t?48">Movies</a></td>
<td><a href="/t/555">The.Nut.Job.2014.1080p.BluRay.x264-GRP</a></td>
<td><a href="/download.php/555/The.Nut.Job.torrent">dl</a></td><td>4.5 GB</td><td>1,234</td><td>56</td></tr>
</table></body></html>"""


class MockTrackers:
    """Per-host queues of answers; records every request with its headers."""

    def __init__(self):
        self.by_host = {}
        self.calls = []

    def set(self, host, *responses):
        self.by_host[host] = list(responses)

    def hits(self, host):
        return [c for c in self.calls if c['url'].split('//', 1)[1].split('/', 1)[0].split(':')[0] == host]

    def __call__(self, method, url, **kw):
        host = url.split('//', 1)[1].split('/', 1)[0].split(':')[0]
        # Like tracker_http.request: the address scope sees (and may refuse)
        # every address before anything is sent.
        scope = tracker_http.current_scope()
        if scope is not None:
            scope.visit((ADDRS.get(host) or [host])[0])
        self.calls.append({'method': method, 'url': url, **kw})
        rs = self.by_host.get(host)
        if not rs:
            raise AssertionError(f'unexpected request to {host}')
        r = rs.pop(0) if len(rs) > 1 else rs[0]
        if isinstance(r, Exception):
            raise r
        return r


class FakeQbit:
    """qBittorrent Web API: login, add (file or urls), info by category."""

    def __init__(self):
        self.added = []
        self.torrents = []
        self.fetched = []

    def session_class(self):
        q = self

        class Resp:
            def __init__(self, status=200, text='Ok.', data=None):
                self.status_code, self.text, self._data = status, text, data
                self.headers, self.content, self.url = {}, text.encode(), ''

            def json(self):
                return self._data

        class Session:
            def post(self, url, data=None, files=None, timeout=None, **kw):
                if url.endswith('/api/v2/auth/login'):
                    return Resp()
                assert url.endswith('/api/v2/torrents/add'), url
                q.added.append({'data': dict(data or {}), 'files': files})
                if files:
                    h = app.main._torrent_info_hash(files['torrents'][1])
                else:
                    h = app.main._magnet_info_hash(data['urls'])
                q.torrents.append({'hash': h, 'added_on': int(time.time())})
                return Resp()

            def get(self, url, params=None, timeout=None, **kw):
                if url.endswith('/api/v2/torrents/info'):
                    return Resp(data=list(q.torrents))
                q.fetched.append(url)
                raise AssertionError('the server must not fetch a release link itself')
        return Session


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key')
    guard = TrackerGuard(tmp_path / 'tracker_guard.json')
    mock = MockTrackers()
    engine = TrackerSearch(store=store, guard=guard, request=mock,
                           tokens=ReleaseTokens(store, tmp_path / 'tracker_releases.json'))
    tracker_store.reset_store_for_tests(store)
    tracker_guard.reset_guard_for_tests(guard)
    tracker_search.reset_search_for_tests(engine)
    monkeypatch.setitem(tracker_guard.LIMITS, 'min_interval_s', 0.0)
    monkeypatch.setattr(tracker_sources, '_resolve', lambda host: ADDRS.get(host) or (
        [host.strip('[]')] if tracker_sources._literal(host) else []))
    tracker_clients.clear_avistaz_tokens()
    tracker_routes._test_log.clear()
    app.main._all_results_cache.clear()
    values = dict(QBIT)
    real_get = app.main.settings.get

    def get(key, *a, **kw):
        if str(key).startswith('indexer.'):
            return ''
        if key in values:
            return values[key]
        return real_get(key, *a, **kw)
    monkeypatch.setattr(app.main.settings, 'get', get)

    def no_prowlarr(*a, **kw):
        raise AssertionError('Prowlarr must not be contacted')
    # v172: the Prowlarr/Torznab clients are gone; the one Prowlarr read left (Import) must not run either
    from app import tracker_import
    monkeypatch.setattr(tracker_import, 'fetch_indexers', no_prowlarr)

    qbit = FakeQbit()
    import requests
    monkeypatch.setattr(requests, 'Session', qbit.session_class())
    real_sleep = time.sleep
    monkeypatch.setattr(time, 'sleep', lambda s: real_sleep(0) if s else None)
    tracked, origins = [], []
    # The real renamer wants /app; the grab only needs track_download.
    import sys
    import types
    fake_rename = types.ModuleType('app.auto_rename')
    fake_rename.auto_renamer = types.SimpleNamespace(track_download=lambda h, d: tracked.append((h, d)))
    monkeypatch.setitem(sys.modules, 'app.auto_rename', fake_rename)
    monkeypatch.setattr(app.main, '_record_origin', lambda origin, **kw: origins.append((origin, kw)))
    yield {'store': store, 'guard': guard, 'mock': mock, 'engine': engine, 'qbit': qbit,
           'tracked': tracked, 'origins': origins, 'settings': values}
    tracker_search.reset_search_for_tests(None)
    tracker_guard.reset_guard_for_tests(None)
    tracker_store.reset_store_for_tests(None)
    tracker_clients.clear_avistaz_tokens()
    app.main._all_results_cache.clear()


def _owner():
    return app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)


@pytest.fixture
def signed_in(test_app, env):
    """A real sign-in (session cookie), not implicit LAN access."""
    o = _owner()
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(o['id']))
    return c


@pytest.fixture
def lan_anon(test_app, env):
    """Signed out on the LAN: implicit owner access under the default rules."""
    _owner()
    c = test_app.test_client()
    c.environ_base.update(LAN)
    return c


def torznab(store, host='t.example', **over):
    body = {'type': 'torznab', 'name': 'Indexer', 'url': f'https://{host}/api', 'api_key': APIKEY,
            'categories': [2000, 5000], 'priority': 25}
    body.update(over)
    rec, errors = store.create(body)
    assert not errors, errors
    return rec


def ipt(store, host='ipt.example', **over):
    body = {'type': 'iptorrents', 'name': 'My private site', 'url': f'https://{host}', 'cookie': COOKIE,
            'categories': [2000, 5000], 'priority': 10}
    body.update(over)
    rec, errors = store.create(body)
    assert not errors, errors
    return rec


def _search(c, title='The Nut Job', media_type='movie'):
    r = c.get(f'/api/sources/all-results?title={title}&media_type={media_type}')
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['releases']


def _grab(c, url, tmdb=TMDB, name='The.Nut.Job.2014.1080p'):
    return c.post('/api/downloads/add', headers=ORIGIN, content_type='application/json',
                  data=json.dumps({'torrent_url': url, 'torrent_name': name, 'tmdb_data': tmdb}))


DL = f'https://t.example/dl/555?passkey={PASSKEY}'


# ------------------------------------------------------------------ zero trackers

def test_zero_trackers_sources_config_says_nothing_to_search(signed_in, env):
    r = signed_in.get('/api/sources/config')
    assert r.status_code == 200
    assert r.get_json()['indexer_configured'] is False
    assert _search(signed_in) == []
    assert env['mock'].calls == []


def test_one_tracker_sources_config_says_searchable(signed_in, env):
    torznab(env['store'])
    assert signed_in.get('/api/sources/config').get_json()['indexer_configured'] is True


# ------------------------------------------------------------------ grab

def test_grab_from_sources_sends_the_torrent_to_qbittorrent(signed_in, env):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))), R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    assert rel['download_url'].startswith('/api/trackers/release/')
    r = _grab(signed_in, rel['download_url'], name=rel['title'])
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body['success'] is True
    added = env['qbit'].added[0]
    assert added['files']['torrents'][1] == torrent_bytes()
    assert added['data']['category']
    dl = env['mock'].calls[-1]
    assert dl['url'] == DL and dl['headers'] == {} and dl['follow_redirects'] is False
    assert env['tracked'][0][1]['tmdb_id'] == TMDB['tmdb_id']
    # What gets recorded and returned is the release link, never the passkey.
    assert env['origins'][0][1]['download_url'] == rel['download_url']
    assert PASSKEY not in r.get_data(as_text=True)
    assert env['qbit'].fetched == []


def test_tv_grab_from_sources(signed_in, env):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('Severance S02 1080p WEB-DL', DL))), R(body=torrent_bytes()))
    rel = _search(signed_in, 'Severance', 'tv')[0]
    r = _grab(signed_in, rel['download_url'], tmdb={'tmdb_id': 95396, 'media_type': 'tv', 'title': 'Severance'})
    assert r.status_code == 200
    assert env['tracked'][0][1]['media_type'] == 'tv'


def test_ipt_cookie_goes_to_its_saved_base_url_only(signed_in, env):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=IPT_HTML), R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    assert rel['tracker_type'] == 'iptorrents' and rel['indexer'] == 'My private site'
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 200, r.get_data(as_text=True)
    dl = env['mock'].calls[-1]
    assert dl['url'] == 'https://ipt.example/download.php/555/The.Nut.Job.torrent'
    assert dl['headers']['Cookie'] == COOKIE


def test_redirect_to_another_site_drops_the_cookie(signed_in, env):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=IPT_HTML),
                    R(302, headers={'Location': 'https://cdn.example/f/555.torrent'}))
    env['mock'].set('cdn.example', R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    assert _grab(signed_in, rel['download_url']).status_code == 200
    cdn = env['mock'].hits('cdn.example')
    assert len(cdn) == 1 and cdn[0]['headers'] == {}


def test_same_host_other_port_is_another_origin(signed_in, env):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=IPT_HTML),
                    R(302, headers={'Location': 'https://ipt.example:8443/f/555.torrent'}),
                    R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    assert _grab(signed_in, rel['download_url']).status_code == 200
    last = env['mock'].calls[-1]
    assert last['url'].startswith('https://ipt.example:8443/') and last['headers'] == {}


def test_link_to_another_site_carrying_a_saved_secret_is_refused(signed_in, env):
    torznab(env['store'])
    leak = f'https://cdn.example/dl?apikey={APIKEY}'
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', leak))))
    env['mock'].set('cdn.example', R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 400          # v169 (Tester): the token's own target refused: a 400
    assert env['mock'].hits('cdn.example') == [] and env['qbit'].added == []
    assert APIKEY not in r.get_data(as_text=True)


@pytest.mark.parametrize('link', ['http://169.254.169.254/latest/meta-data', 'https://meta.example/x',
                                  'http://[fe80::1]/x', 'http://0.0.0.0/x', 'https://lan.example/x'])
def test_link_local_metadata_and_private_other_hosts_are_never_fetched(signed_in, env, link):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', link))))
    rel = _search(signed_in)[0]
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 400          # v169 (Tester): the token's own target refused: a 400
    assert len(env['mock'].calls) == 1 and env['qbit'].added == []


def test_tracker_on_a_private_address_gets_no_download_request(signed_in, env):
    torznab(env['store'], host='lan.example')
    link = f'https://lan.example/dl/1?apikey={APIKEY}'
    env['mock'].set('lan.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', link))), R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    calls = len(env['mock'].calls)
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 400 and env['qbit'].added == []
    assert len(env['mock'].calls) == calls


def test_public_torznab_link_with_its_own_key_is_fetched(signed_in, env):
    torznab(env['store'])
    link = f'https://t.example/dl/1?apikey={APIKEY}'
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', link))), R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    assert APIKEY not in rel['download_url']
    assert _grab(signed_in, rel['download_url']).status_code == 200
    assert len(env['qbit'].added) == 1


@pytest.mark.parametrize('port', [9117, 9696, 5001])
def test_any_port_on_a_public_host_is_fine(signed_in, env, port):
    torznab(env['store'])
    link = f'https://cdn.example:{port}/f/1.torrent'
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', link))))
    env['mock'].set('cdn.example', R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 200, r.get_data(as_text=True)


def test_magnet_release_goes_to_qbittorrent_as_a_magnet(signed_in, env):
    torznab(env['store'])
    ih = hashlib.sha1(b'x').hexdigest()
    magnet = f'magnet:?xt=urn:btih:{ih}&tr=https%3A%2F%2Ft.example%2Fa%2F{PASSKEY}'
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', magnet))))
    rel = _search(signed_in)[0]
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 200, r.get_data(as_text=True)
    assert env['qbit'].added[0]['data']['urls'] == magnet
    assert len(env['mock'].calls) == 1          # no download request for a magnet
    assert env['origins'][0][1]['download_url'] == rel['download_url']


def test_html_instead_of_a_torrent_adds_nothing(signed_in, env):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))), R(body='<html>login</html>'))
    rel = _search(signed_in)[0]
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 502 and env['qbit'].added == [] and env['tracked'] == []


def test_unknown_or_expired_link_adds_nothing(signed_in, env):
    torznab(env['store'])
    r = _grab(signed_in, '/api/trackers/release/' + 'x' * 32)
    assert r.status_code == 410
    assert env['mock'].calls == [] and env['qbit'].added == []


def test_removed_tracker_link_adds_nothing(signed_in, env):
    rec = torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))))
    rel = _search(signed_in)[0]
    env['store'].delete(rec['id'])
    torznab(env['store'], name='Other')
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 410 and len(env['mock'].calls) == 1


def test_qbittorrent_not_set_up_is_a_clean_error_before_any_tracker_request(signed_in, env):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))))
    rel = _search(signed_in)[0]
    env['settings']['qbittorrent.url'] = ''
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 503 and r.get_json()['error'] == 'qBittorrent isn’t set up'
    assert len(env['mock'].calls) == 1


# ------------------------------------------------------------------ rate limits

def test_download_429_honors_retry_after_and_backs_off_search_too(signed_in, env):
    rec = torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))),
                    R(429, headers={'Retry-After': '600'}))
    rel = _search(signed_in)[0]
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 429
    blocked = env['guard'].blocked(guard_key(env['store'].get(rec['id'])))
    assert blocked and blocked[1] == 'rate' and blocked[0] >= time.time() + 590
    n = len(env['mock'].calls)
    assert _grab(signed_in, rel['download_url']).status_code == 429
    app.main._all_results_cache.clear()
    env['engine']._cache.clear()
    _search(signed_in, 'Another title')
    assert len(env['mock'].calls) == n        # neither the grab nor a search was sent


def test_grab_counts_against_the_shared_rate_limit(signed_in, env, monkeypatch):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))), R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    monkeypatch.setattr(env['guard'], 'take', lambda *a, **kw: 'hourly cap 40')
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 429 and len(env['mock'].calls) == 1


# ------------------------------------------------------------------ access

def test_signed_out_lan_device_gets_401_with_trackers(lan_anon, env):
    torznab(env['store'])
    r = lan_anon.get('/api/sources/all-results?title=The Nut Job&media_type=movie')
    assert r.status_code == 401
    tok = env['engine'].tokens.issue(1, DL)
    r = lan_anon.post('/api/downloads/add', content_type='application/json',
                      data=json.dumps({'torrent_url': '/api/trackers/release/' + tok, 'tmdb_data': TMDB}))
    assert r.status_code == 401
    assert env['mock'].calls == [] and env['qbit'].added == []


def test_remote_signed_out_gets_401(test_app, env):
    torznab(env['store'])
    c = test_app.test_client()
    c.environ_base.update({'REMOTE_ADDR': '203.0.113.9'})
    assert c.get('/api/sources/all-results?title=x&media_type=movie').status_code == 401
    assert c.post('/api/downloads/add', content_type='application/json',
                  data=json.dumps({'torrent_url': '/api/trackers/release/' + 'a' * 32})).status_code == 401
    assert env['mock'].calls == []


def test_signed_in_user_without_approval_gets_403_and_approved_user_may_search(test_app, env):
    torznab(env['store'])
    _owner()
    u = app.user_auth.create_user('guest', 'pin-7654321', is_admin=False)
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(u['id']))
    assert c.get('/api/sources/all-results?title=x&media_type=movie').status_code == 403
    app.user_auth.save_user_settings(u['id'], {'plex_access_status': 'approved'})
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))))
    assert c.get('/api/sources/all-results?title=x&media_type=movie').status_code == 200


def test_release_links_are_only_relative_paths(env):
    assert tracker_sources.release_token('/api/trackers/release/' + 'a' * 32) == 'a' * 32
    assert tracker_sources.release_token('/api/trackers/release/../x') == ''
    assert tracker_sources.release_token('https://evil.example/api/trackers/release/' + 'a' * 32) is None
    assert tracker_sources.release_token(DL) is None
    assert tracker_sources.release_token('magnet:?xt=urn:btih:abc') is None


# ------------------------------------------------------------------ IP-keyed backoff (Torznab/Newznab)

T_IP = ADDRS['t.example'][0]


def test_grab_rate_limit_also_keys_the_server_address(signed_in, env):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))),
                    R(429, headers={'Retry-After': '600'}))
    rel = _search(signed_in)[0]
    assert _grab(signed_in, rel['download_url']).status_code == 429
    blocked = env['guard'].blocked(ip_key(T_IP))
    assert blocked and blocked[1] == 'rate' and blocked[0] >= time.time() + 590


def test_grab_respects_a_backoff_on_the_server_address(signed_in, env):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))), R(body=torrent_bytes()))
    rel = _search(signed_in)[0]
    calls = len(env['mock'].calls)
    env['guard'].failure(ip_key(T_IP), 'rate', 600)      # e.g. another name for the same server
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 429
    assert len(env['mock'].calls) == calls and env['qbit'].added == []


def test_grab_ban_page_backs_off_the_site_and_the_address(signed_in, env):
    rec = torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))),
                    R(body='<html>Your account is banned</html>'))
    rel = _search(signed_in)[0]
    r = _grab(signed_in, rel['download_url'])
    assert r.status_code == 429 and env['qbit'].added == []
    assert env['guard'].blocked(guard_key(env['store'].get(rec['id'])))[1] == 'banned'
    assert env['guard'].blocked(ip_key(T_IP))[1] == 'banned'


def test_site_type_grab_rate_limit_does_not_key_the_address(signed_in, env):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=IPT_HTML), R(429, headers={'Retry-After': '600'}))
    rel = _search(signed_in)[0]
    assert _grab(signed_in, rel['download_url']).status_code == 429
    assert env['guard'].blocked(ip_key(ADDRS['ipt.example'][0])) is None
    assert env['guard'].blocked('site:iptorrents')[1] == 'rate'
