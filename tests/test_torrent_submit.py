"""Offline proof for tracker preservation on a qBittorrent add.

Synthetic fixtures only. A details-page comment is not an announce. A tracker
URL already on the session counts whether or not its status is Working.
A private add is success only for the requested info-hash, with the supplied
usable tracker URLs present. Public DHT magnets stay adds.
"""
import logging
from urllib.parse import quote

import pytest
import requests

import app.main
from app import instance, torrent_submit as ts
from app.qbit_connector import add_ok, add_pending
from tests.test_tv_v158c import FakeQbit, _grab, qenv  # noqa: F401
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_tv_search_v158b import allow_grabs, env, eps, item, sev  # noqa: F401

PASSKEY = 'pk-FIXTURE-9f3a'
ANNOUNCE = f'https://tracker.example/announce/{PASSKEY}'
BACKUP = f'https://backup.example/announce/{PASSKEY}'
COMMENT = b'https://beyond-hd.me/torrents/example'
OTHER = 'ab' * 20


def bstr(s):
    if isinstance(s, str):
        s = s.encode()
    return str(len(s)).encode() + b':' + s


def bint(n):
    return f'i{n}e'.encode()


def blist(parts):
    return b'l' + b''.join(parts) + b'e'


def bdict(pairs):
    return b'd' + b''.join(bstr(k) + v for k, v in pairs) + b'e'


def torrent_bytes(tiers, private=True, comment=COMMENT):
    info = [
        (b'length', bint(1)),
        (b'name', bstr(b'fixture')),
        (b'piece length', bint(16384)),
        (b'pieces', bstr(b'0123456789abcdefghij')),
    ]
    if private:
        info.append((b'private', bint(1)))
    top = []
    if tiers and tiers[0]:
        top.append((b'announce', bstr(tiers[0][0])))
    if tiers:
        groups = [blist(bstr(u) for u in tier) for tier in tiers]
        top.append((b'announce-list', blist(groups)))
    if comment is not None:
        top.append((b'comment', bstr(comment)))
    top.append((b'info', bdict(info)))
    top.sort(key=lambda kv: kv[0])
    return bdict(top)


def _parse_tiers(body):
    tier, out = 0, {}
    for line in (body or '').split('\n'):
        if not line.strip():
            tier += 1
            continue
        out.setdefault(tier, []).append(line.strip())
    return out


class R:
    def __init__(self, status=200, text='Ok.', js=None):
        self.status_code, self.text, self._js = status, text, js
        self.content = text.encode() if isinstance(text, str) else text
        self.headers = {}

    def json(self):
        if self._js is None:
            raise ValueError('not json')
        return self._js


class Api:
    """In-memory torrents/trackers. Status is stored and ignored by the check."""

    def __init__(self):
        self.lists = {}
        self.posts = []
        self.added = []
        self.gets = []

    def get(self, url, params=None, timeout=None, allow_redirects=None):
        self.gets.append(params.get('hash') if params else None)
        h = (params or {}).get('hash')
        if h not in self.lists:
            return R(404, 'Not Found')
        return R(200, '[]', list(self.lists[h]))

    def post(self, url, data=None, files=None, timeout=None, allow_redirects=None):
        if not str(url).endswith('/torrents/addTrackers'):
            self.added.append(files['torrents'][1] if files else (data or {}).get('urls'))
            return R(200, 'Ok.')
        self.posts.append(dict(data or {}))
        h = data['hash']
        self.lists.setdefault(h, [])
        tier = 0
        for line in data['urls'].split('\n'):
            if not line.strip():
                tier += 1
                continue
            self.lists[h].append({'url': line.strip(), 'status': 4, 'tier': tier})
        return R(200, 'Ok.')


def test_comment_is_not_a_tracker_and_tiers_round_trip():
    raw = torrent_bytes([[ANNOUNCE], [BACKUP]])
    info = ts.inspect('torrent', raw)
    assert info.private is True
    assert info.tiers == [[ANNOUNCE], [BACKUP]]
    assert COMMENT.decode() not in str(info.tiers)
    parsed = _parse_tiers(ts.encode_tiers(info.tiers))
    assert parsed[0] == [ANNOUNCE] and parsed[1] == [BACKUP]
    assert ts.inspect('torrent', torrent_bytes([], comment=COMMENT)).tiers == []
    assert ts.inspect('magnet', 'magnet:?xt=urn:btih:' + 'c' * 40).tiers == []


def test_private_restore_keeps_bytes_and_only_missing_urls(caplog):
    caplog.set_level(logging.INFO)
    raw = torrent_bytes([[ANNOUNCE], [BACKUP]])
    h = app.main._torrent_info_hash(raw)
    api = Api()
    api.lists[h] = [{'url': ANNOUNCE, 'status': 5, 'tier': 0},
                    {'url': '** [DHT] **', 'status': 0, 'tier': -1}]
    assert ts.settle(api, 'http://qbit', h, 'torrent', raw, duplicate=True, sent=True) == ts.CONFIRMED
    assert api.posts and api.posts[0]['hash'] == h
    assert _parse_tiers(api.posts[0]['urls']) == {1: [BACKUP]}
    assert ANNOUNCE not in api.posts[0]['urls'].split('\n')
    assert COMMENT.decode() not in api.posts[0]['urls']
    assert PASSKEY not in caplog.text
    again = Api()
    again.lists[h] = [{'url': ANNOUNCE, 'status': 0}, {'url': BACKUP, 'status': 4}]
    assert ts.settle(again, 'http://qbit', h, 'torrent', raw, duplicate=False, sent=True) == ts.CONFIRMED
    assert again.posts == []


def test_private_without_trackers_is_not_added_and_not_success():
    raw = torrent_bytes([], comment=COMMENT)
    h = app.main._torrent_info_hash(raw)
    api = Api()
    assert ts.precheck(api, 'http://qbit', h, 'torrent', raw) == ts.UNUSABLE
    assert h not in api.lists
    api.lists[h] = [{'url': '** [DHT] **', 'status': 2}]
    assert ts.precheck(api, 'http://qbit', h, 'torrent', raw) == ts.UNUSABLE
    api.lists[h] = [{'url': ANNOUNCE, 'status': 5}]
    assert ts.precheck(api, 'http://qbit', h, 'torrent', raw) == ts.CONFIRMED
    assert ts.settle(Api(), 'http://qbit', h, 'torrent', raw, duplicate=False, sent=True) == ts.PENDING


def test_other_hash_is_not_this_torrent():
    raw = torrent_bytes([[ANNOUNCE]])
    h = app.main._torrent_info_hash(raw)
    api = Api()
    api.lists[OTHER] = [{'url': ANNOUNCE, 'status': 2}]
    assert ts.settle(api, 'http://qbit', h, 'torrent', raw, duplicate=False, sent=True) == ts.PENDING
    assert OTHER not in [p.get('hash') for p in api.posts]
    assert api.gets == [h]


def test_public_dht_and_announced_public_do_not_call_trackers():
    magnet = 'magnet:?xt=urn:btih:' + 'd' * 40
    api = Api()
    assert ts.precheck(api, 'http://qbit', 'd' * 40, 'magnet', magnet) == ts.SKIP
    assert ts.settle(api, 'http://qbit', 'd' * 40, 'magnet', magnet, duplicate=False, sent=True) == ts.SKIP
    public = torrent_bytes([[ANNOUNCE]], private=False, comment=None)
    h = app.main._torrent_info_hash(public)
    assert ts.inspect('torrent', public).private is False
    assert ts.settle(api, 'http://qbit', h, 'torrent', public, duplicate=False, sent=True) == ts.SKIP
    assert api.gets == [] and api.posts == []


def test_pending_count_is_not_acceptance():
    body = '{"failure_count":0,"pending_count":1}'
    assert add_pending(R(202, body)) is True
    assert add_ok(R(202, body)) is False
    assert add_pending(R(200, '{"failure_count":0,"pending_count":0}')) is False
    assert add_ok(R(200, '{"failure_count":0,"pending_count":0}')) is True


def test_trackerless_magnet_does_not_hide_one_with_trackers():
    bare = 'magnet:?xt=urn:btih:' + 'e' * 40
    tracked = bare + '&tr=' + quote(ANNOUNCE, safe='')
    assert ts.prefer_download_target(bare, tracked) == tracked
    assert ts.prefer_download_target('https://t.example/dl/1', tracked) == 'https://t.example/dl/1'
    from app.tracker_search import TrackerSearch

    class Tokens:
        def issue(self, tracker_id, target):
            self.target = target
            return 'tok'

    tokens = Tokens()
    item = TrackerSearch.to_item(
        type('S', (), {'tokens': tokens})(),
        {'id': 1, 'name': 'Example', 'type': 'torznab'},
        {'download_url': bare, 'magnet_url': tracked, 'title': 'Example.Release'}, [])
    assert tokens.target == tracked
    assert PASSKEY not in str(item)


def test_add_trackers_stays_inside_the_write_guard(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')
    sent = []

    def fake_send(self, request, **kw):
        sent.append(request.url)
        r = requests.Response()
        r.status_code = 200
        r._content = b'Ok.'
        return r

    monkeypatch.setattr(requests.Session, 'send', fake_send)
    instance.uninstall_write_guard()
    monkeypatch.setattr(instance, '_installed', {'on': False})
    instance.install_write_guard()
    try:
        s = requests.Session()
        url = 'http://qbit/api/v2/torrents/addTrackers'
        h = 'a' * 40
        with instance.scoped_add('tv_grab', 'luma-test-tv') as scope:
            with pytest.raises(instance.WritesBlocked):
                s.post(url, data={'hash': h, 'urls': ANNOUNCE})
            scope.allow_trackers([h.upper()])
            s.post(url, data={'hash': h, 'urls': ANNOUNCE + '\n\n' + BACKUP})
            with pytest.raises(instance.WritesBlocked):
                s.post(url, data={'hash': OTHER, 'urls': ANNOUNCE})
            with pytest.raises(instance.WritesBlocked):
                s.post(url, data={'hash': h, 'urls': ANNOUNCE, 'category': 'x'})
            with pytest.raises(instance.WritesBlocked):
                s.post(url, data={'hash': h, 'urls': '   '})
            with pytest.raises(instance.WritesBlocked):
                s.post('http://qbit/api/v2/torrents/removeTrackers', data={'hash': h, 'urls': ANNOUNCE})
        with pytest.raises(instance.WritesBlocked):
            s.post(url, data={'hash': h, 'urls': ANNOUNCE})
        assert sent == [url]
        assert PASSKEY not in ' '.join(sent)
    finally:
        instance.uninstall_write_guard()


# ------------------------------------------------------------------ /api/downloads/add

class RouteQbit:
    def __init__(self, mode='keep'):
        self.mode = mode
        self.torrents = []
        self.added = []
        self.tracker_posts = []
        self.gets = []
        self.lists = {}
        self.trust_env = True

    def post(self, url, data=None, files=None, timeout=None, **k):
        if url.endswith('/auth/login'):
            return R()
        if url.endswith('/torrents/add'):
            blob = files['torrents'][1] if files else None
            self.added.append(blob if blob is not None else data.get('urls'))
            if self.mode == 'pending':
                return R(202, '{"failure_count":0,"pending_count":1}')
            if blob is not None:
                h = app.main._torrent_info_hash(blob)
            else:
                h = app.main._magnet_info_hash(data['urls'])
            shown = OTHER if self.mode == 'other' else h
            self.torrents.append({'hash': shown, 'added_on': 1})
            if self.mode == 'keep' and blob is not None:
                self.lists[h] = [{'url': u, 'status': 5, 'tier': i}
                                 for i, tier in enumerate(ts.inspect('torrent', blob).tiers) for u in tier]
            elif self.mode != 'other':
                self.lists[h] = []
            return R()
        if url.endswith('/torrents/addTrackers'):
            self.tracker_posts.append(dict(data))
            h = data['hash']
            self.lists.setdefault(h, [])
            tier = 0
            for line in data['urls'].split('\n'):
                if not line.strip():
                    tier += 1
                    continue
                self.lists[h].append({'url': line.strip(), 'status': 1, 'tier': tier})
            return R()
        return R(404, '')

    def get(self, url, params=None, timeout=None, **k):
        self.gets.append(url)
        if url.endswith('/torrents/info'):
            return R(200, '[]', list(self.torrents))
        if url.endswith('/torrents/trackers'):
            h = (params or {}).get('hash')
            if h not in self.lists:
                return R(404, '')
            return R(200, '[]', list(self.lists[h]))
        raise AssertionError(url)


@pytest.fixture
def client(test_app):
    return test_app.test_client()


def _route(monkeypatch, q, net=None):
    import sys
    import time
    import types
    fake = types.ModuleType('app.auto_rename')
    fake.auto_renamer = types.SimpleNamespace(track_download=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, 'app.auto_rename', fake)
    monkeypatch.setattr(requests, 'Session', lambda: q)
    monkeypatch.setattr(app.main, '_record_origin', lambda *a, **k: None)
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    app.main.settings.update({'qbittorrent': {
        'url': 'http://qbit.test', 'username': 'u', 'password': 'p'}})


def _own(client):
    from tests.test_downloads_add_ssrf import ORIGIN, REMOTE, _owner_client, _require_auth_everywhere
    _require_auth_everywhere()
    _owner_client(client)
    return ORIGIN, REMOTE


def test_private_add_confirms_hash_and_restores_dropped_trackers(client, monkeypatch, caplog):
    from tests.test_downloads_add_ssrf import PUBLIC, Net
    caplog.set_level(logging.DEBUG)
    raw = torrent_bytes([[ANNOUNCE], [BACKUP]])
    h = app.main._torrent_info_hash(raw)
    q = RouteQbit('drop')
    _route(monkeypatch, q)
    net = Net(monkeypatch)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/dl/1')] = (200, {}, raw)
    origin, remote = _own(client)
    r = client.post('/api/downloads/add', json={'torrent_url': 'https://tracker.example/dl/1'},
                    headers=origin, environ_base=remote)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['success'] is True and body['hash'] == h
    assert q.added == [raw]
    assert len(q.tracker_posts) == 1 and q.tracker_posts[0]['hash'] == h
    assert _parse_tiers(q.tracker_posts[0]['urls']) == {0: [ANNOUNCE], 1: [BACKUP]}
    assert COMMENT.decode() not in q.tracker_posts[0]['urls']
    assert {row['url'] for row in q.lists[h]} == {ANNOUNCE, BACKUP}
    assert PASSKEY not in r.get_data(as_text=True)
    assert PASSKEY not in caplog.text


def test_private_add_does_not_adopt_a_different_hash(client, monkeypatch):
    from tests.test_downloads_add_ssrf import PUBLIC, Net
    raw = torrent_bytes([[ANNOUNCE]])
    h = app.main._torrent_info_hash(raw)
    q = RouteQbit('other')
    _route(monkeypatch, q)
    net = Net(monkeypatch)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/dl/2')] = (200, {}, raw)
    origin, remote = _own(client)
    r = client.post('/api/downloads/add', json={'torrent_url': 'https://tracker.example/dl/2'},
                    headers=origin, environ_base=remote)
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body.get('success') is not True and body.get('hash') != OTHER
    assert body.get('pending') is True
    assert h != OTHER and OTHER not in r.get_data(as_text=True)


def test_private_trackerless_payload_is_not_uploaded(client, monkeypatch):
    from tests.test_downloads_add_ssrf import PUBLIC, Net
    raw = torrent_bytes([], comment=COMMENT)
    q = RouteQbit('drop')
    _route(monkeypatch, q)
    net = Net(monkeypatch)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/dl/3')] = (200, {}, raw)
    origin, remote = _own(client)
    r = client.post('/api/downloads/add', json={'torrent_url': 'https://tracker.example/dl/3'},
                    headers=origin, environ_base=remote)
    assert r.status_code == 409, r.get_json()
    assert q.added == [] and q.tracker_posts == []
    text = r.get_data(as_text=True)
    assert 'beyond-hd' not in text and PASSKEY not in text
    assert r.get_json().get('success') is not True


def test_intact_non_working_trackers_are_kept(client, monkeypatch):
    from tests.test_downloads_add_ssrf import PUBLIC, Net
    raw = torrent_bytes([[ANNOUNCE], [BACKUP]])
    h = app.main._torrent_info_hash(raw)
    q = RouteQbit('keep')
    q.torrents.append({'hash': h, 'added_on': 1})
    q.lists[h] = [{'url': ANNOUNCE, 'status': 0, 'tier': 0},
                  {'url': BACKUP, 'status': 5, 'tier': 1}]
    _route(monkeypatch, q)
    net = Net(monkeypatch)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/dl/4')] = (200, {}, raw)
    origin, remote = _own(client)
    r = client.post('/api/downloads/add', json={'torrent_url': 'https://tracker.example/dl/4'},
                    headers=origin, environ_base=remote)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['hash'] == h and r.get_json()['message'] == 'Already downloading'
    assert q.added == [] and q.tracker_posts == []


def test_public_magnet_and_public_announce_do_not_read_trackers(client, monkeypatch):
    from tests.test_downloads_add_ssrf import PUBLIC, Net
    from tests.test_tracker_sources import torrent_bytes as public_torrent
    q = RouteQbit('drop')
    _route(monkeypatch, q)
    origin, remote = _own(client)
    magnet = 'magnet:?xt=urn:btih:' + 'f' * 40
    r = client.post('/api/downloads/add', json={'torrent_url': magnet},
                    headers=origin, environ_base=remote)
    assert r.status_code == 200, r.get_json()
    assert q.added == [magnet] and q.tracker_posts == []
    assert not any(u.endswith('/torrents/trackers') for u in q.gets)
    raw = public_torrent()
    q2 = RouteQbit('drop')
    _route(monkeypatch, q2)
    net = Net(monkeypatch)
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/dl/5')] = (200, {}, raw)
    r2 = client.post('/api/downloads/add', json={'torrent_url': 'https://tracker.example/dl/5'},
                     headers=origin, environ_base=remote)
    assert r2.status_code == 200, r2.get_json()
    assert q2.added == [raw] and q2.tracker_posts == []
    assert not any(u.endswith('/torrents/trackers') for u in q2.gets)


def test_async_pending_is_not_a_confirmed_download(client, monkeypatch):
    q = RouteQbit('pending')
    _route(monkeypatch, q)
    origin, remote = _own(client)
    r = client.post('/api/downloads/add', json={'torrent_url': 'magnet:?xt=urn:btih:' + '1' * 40},
                    headers=origin, environ_base=remote)
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body.get('pending') is True and body.get('success') is not True
    assert 'hash' not in body


def test_readd_restores_missing_tiers_and_refuses_a_different_hash(monkeypatch):
    from app import import_reconciler as ir
    from app import tv_grab
    raw = torrent_bytes([[ANNOUNCE], [BACKUP]])
    h = app.main._torrent_info_hash(raw)
    api = Api()
    api.lists[h] = []
    monkeypatch.setattr('app.library_mover._qbit_session', lambda *a, **k: (api, 'http://qbit'))
    monkeypatch.setattr(tv_grab, 'ensure_category', lambda *a, **k: None)
    assert ir.readd({'kind': 'tv', 'hash': h}, 'torrent', raw, now=1) is None
    assert api.posts and api.posts[0]['hash'] == h
    assert {row['url'] for row in api.lists[h]} == {ANNOUNCE, BACKUP}
    bare = torrent_bytes([], comment=COMMENT)
    bare_h = app.main._torrent_info_hash(bare)
    quiet = Api()
    monkeypatch.setattr('app.library_mover._qbit_session', lambda *a, **k: (quiet, 'http://qbit'))
    assert ir.readd({'kind': 'tv', 'hash': bare_h}, 'torrent', bare, now=1) == ts.NO_USABLE
    assert quiet.posts == []
    assert ir.readd({'kind': 'tv', 'hash': OTHER}, 'torrent', raw, now=1) == ts.NOT_CONFIRMED


def test_movie_grab_confirms_private_hash_and_keeps_non_working_urls(tmp_path, monkeypatch):
    from app import grab_cache, movie_dupes, movie_scanner, movie_settings, tv_grab
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(movie_settings.SEED_FILE))
    monkeypatch.setitem(grab_cache._cfg, 'dir', str(tmp_path / 'cache'))
    monkeypatch.setattr(movie_scanner, 'on_test', lambda: True)
    monkeypatch.setattr(instance, 'writes_allowed', lambda job: True)
    monkeypatch.setattr(movie_dupes, 'check', lambda *a, **k: None)
    tv_grab._have_category.clear()
    raw = torrent_bytes([[ANNOUNCE]])
    h = app.main._torrent_info_hash(raw)
    api = Api()
    api.lists[h] = []
    name = movie_scanner.category()

    def get(url, params=None, timeout=None, allow_redirects=None):
        if url.endswith('/torrents/categories'):
            return R(200, '{}', {name: {'name': name}})
        return Api.get(api, url, params=params, timeout=timeout, allow_redirects=allow_redirects)

    api.get = get
    monkeypatch.setattr('app.library_mover._qbit_session', lambda *a, **k: (api, 'http://qbit'))
    out = movie_scanner.grab({'title': 'Fixture', 'downloadUrl': 'x'}, 3082, 'manual', 50,
                             fetch=lambda u: ('torrent', raw), movie={'title': 'Fixture', 'year': 1995})
    assert out['ok'] is True and out['hash'] == h
    assert api.posts and {row['url'] for row in api.lists[h]} == {ANNOUNCE}
    held = Api()
    held.lists[h] = [{'url': ANNOUNCE, 'status': 5}]
    monkeypatch.setattr('app.library_mover._qbit_session', lambda *a, **k: (held, 'http://qbit'))
    again = movie_scanner.grab({'title': 'Fixture', 'downloadUrl': 'x'}, 3082, 'manual', 60,
                               fetch=lambda u: ('torrent', raw), movie={'title': 'Fixture', 'year': 1995})
    assert again.get('existing') is True and again['ok'] is False
    assert held.posts == []


class _TvQ(FakeQbit):
    def __init__(self):
        super().__init__()
        self.added = []
        self.lists = {}
        self.tracker_posts = []

    def factory(self):
        q = self
        base = super().factory()

        class S:
            def post(self, url, data=None, files=None, **kw):
                if url.endswith('/torrents/add') and files:
                    blob = files['torrents'][1]
                    q.added.append(blob)
                    h = app.main._torrent_info_hash(blob)
                    q.lists.setdefault(h, [])
                if url.endswith('/torrents/addTrackers'):
                    q.tracker_posts.append(dict(data))
                    h = data['hash']
                    tier = 0
                    for line in data['urls'].split('\n'):
                        if not line.strip():
                            tier += 1
                            continue
                        q.lists.setdefault(h, []).append({'url': line.strip(), 'status': 4, 'tier': tier})
                    return R()
                return base.post(url, data=data, files=files, **kw)

            def get(self, url, params=None, *a, **k):
                if url.endswith('/torrents/trackers'):
                    h = (params or {}).get('hash')
                    if h not in q.lists:
                        return R(404, '')
                    return R(200, '[]', list(q.lists[h]))
                return base.get(url, params, *a, **k)

            def close(self):
                pass
        return S()


def test_tv_private_grab_refuses_a_trackerless_payload(qenv):
    q = FakeQbit()
    out = _grab(q, qenv, payload=torrent_bytes([], comment=COMMENT))
    assert out['ok'] is False and out.get('status') == 409
    assert out.get('link') is None and out.get('client') is None and out.get('blocked') is None
    assert not any(c[1] == 'torrents/add' for c in q.calls)
    assert 'beyond-hd' not in out['error'] and PASSKEY not in out['error']


def test_tv_private_grab_restores_trackers_for_that_hash(qenv):
    raw = torrent_bytes([[ANNOUNCE], [BACKUP]])
    h = app.main._torrent_info_hash(raw)
    q = _TvQ()
    out = _grab(q, qenv, payload=raw)
    assert out['ok'] is True and out['hash'] == h
    assert q.added == [raw]
    assert _parse_tiers(q.tracker_posts[0]['urls']) == {0: [ANNOUNCE], 1: [BACKUP]}
    assert q.tracker_posts[0]['hash'] == h
    q.tracker_posts.clear()
    q.added.clear()
    q.lists[h] = [{'url': ANNOUNCE, 'status': 0}, {'url': BACKUP, 'status': 5}]
    again = _grab(q, qenv, payload=raw)
    assert again.get('existing') is True and again['ok'] is True
    assert q.added == [] and q.tracker_posts == []
