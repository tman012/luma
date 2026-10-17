"""Direct tracker search (Settings > Trackers) against mocked trackers.

No Prowlarr, no Jackett: every test runs with Prowlarr unconfigured or with
the Prowlarr client rigged to fail, so a passing test proves the direct path."""

import json
import time

import pytest

import app.main
import app.user_auth
from app import indexer_api, tracker_clients, tracker_guard, tracker_routes, tracker_search, tracker_store
from app.tracker_guard import TrackerGuard
from app.tracker_http import Response, TrackerError
from app.tracker_search import LumaTrackersClient, ReleaseTokens, TrackerSearch, TrackerSkipped
from app.tracker_store import TrackerStore

LAN = {'REMOTE_ADDR': '192.168.1.20'}
PASSKEY = 'pk9f8e7d6c5b4a39281706f5e4d3c2b1a0'
APIKEY = 'ak0123456789abcdef0123456789abcdef'
COOKIE = 'uid=1234567; pass=abcdefabcdefabcdefabcdefabcdef12'


def R(status=200, body=b'', headers=None):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
    if isinstance(body, str):
        body = body.encode()
    return Response(status, headers or {}, body)


def rss(*items):
    out = []
    for title, seeders, size in items:
        slug = title.replace(' ', '.')
        out.append(
            f'<item><title>{title}</title><guid>https://t.example/details/{slug}</guid>'
            f'<link>https://t.example/dl/{slug}?passkey={PASSKEY}</link>'
            f'<comments>https://t.example/details/{slug}</comments><size>{size}</size>'
            f'<torznab:attr name="seeders" value="{seeders}"/><torznab:attr name="peers" value="{seeders + 3}"/>'
            f'</item>')
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
            'xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><title>x</title>'
            + ''.join(out) + '</channel></rss>')


EMPTY_RSS = rss()

IPT_HTML = """<html><body><table id="torrents">
<tr><th>Type</th><th>Name</th></tr>
<tr><td><a href="/t?48">Movies</a></td>
<td><a href="/t/555">The.Nut.Job.2014.1080p.BluRay.x264-GRP</a></td>
<td><a href="/download.php/555/The.Nut.Job.torrent">dl</a></td><td>4.5 GB</td><td>1,234</td><td>56</td></tr>
</table></body></html>"""


class MockTrackers:
    """Answers per host; records every request (headers included)."""

    def __init__(self):
        self.by_host = {}
        self.calls = []

    def set(self, host, *responses):
        self.by_host[host] = list(responses)

    def hits(self, host):
        return [c for c in self.calls if f'//{host}' in c['url']]

    def __call__(self, method, url, **kw):
        self.calls.append({'method': method, 'url': url, **kw})
        host = url.split('//', 1)[1].split('/', 1)[0]
        rs = self.by_host.get(host)
        if not rs:
            raise AssertionError(f'unexpected request to {host}')
        r = rs.pop(0) if len(rs) > 1 else rs[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fresh store, guard, search engine and mocked trackers; Prowlarr unset."""
    store = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key')
    guard = TrackerGuard(tmp_path / 'tracker_guard.json')
    mock = MockTrackers()
    engine = TrackerSearch(store=store, guard=guard, request=mock,
                           tokens=ReleaseTokens(store, tmp_path / 'tracker_releases.json'))
    tracker_store.reset_store_for_tests(store)
    tracker_guard.reset_guard_for_tests(guard)
    tracker_search.reset_search_for_tests(engine)
    monkeypatch.setitem(tracker_guard.LIMITS, 'min_interval_s', 0.0)
    tracker_clients.clear_avistaz_tokens()
    tracker_routes._test_log.clear()
    app.main._all_results_cache.clear()
    monkeypatch.setattr(app.main.settings, 'get', _settings_without_indexer(app.main.settings.get))

    def no_prowlarr(*a, **kw):
        raise AssertionError('Prowlarr must not be contacted')
    for name in ('search_all', '_search_single', 'search_movies', 'sweep_movies', 'get_indexers', 'test_connection'):
        monkeypatch.setattr(indexer_api.ProwlarrClient, name, no_prowlarr)
        monkeypatch.setattr(indexer_api.TorznabClient, name, no_prowlarr, raising=False)
    yield {'store': store, 'guard': guard, 'mock': mock, 'engine': engine, 'tmp': tmp_path}
    tracker_search.reset_search_for_tests(None)
    tracker_guard.reset_guard_for_tests(None)
    tracker_store.reset_store_for_tests(None)
    tracker_clients.clear_avistaz_tokens()
    app.main._all_results_cache.clear()


def _settings_without_indexer(real_get):
    def get(key, *a, **kw):
        if str(key).startswith('indexer.'):
            return ''
        return real_get(key, *a, **kw)
    return get


@pytest.fixture
def client(test_app, env):
    owner = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(LAN)
    # Searching trackers needs a real sign-in; implicit LAN access gets 401.
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(owner['id']))
    c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
    return c


def torznab(store, name='Jack', host='t.example', **over):
    body = {'type': 'torznab', 'name': name, 'url': f'https://{host}/api', 'api_key': APIKEY,
            'categories': [2000, 5000], 'priority': 25}
    body.update(over)
    rec, errors = store.create(body)
    assert not errors, errors
    return rec


def ipt(store, name='IPT', host='ipt.example', **over):
    body = {'type': 'iptorrents', 'name': name, 'url': f'https://{host}', 'cookie': COOKIE,
            'categories': [2000, 5000], 'priority': 10}
    body.update(over)
    rec, errors = store.create(body)
    assert not errors, errors
    return rec


def _all(c, title='The Nut Job', media_type='movie', year=None):
    q = f'/api/sources/all-results?title={title}&media_type={media_type}'
    if year:
        q += f'&year={year}'
    return c.get(q)


# ------------------------------------------------------------------ no trackers, no Prowlarr

def test_fresh_install_has_no_indexer_client(env):
    assert env['store'].list() == []
    assert indexer_api.get_indexer_client(app.main.settings) is None


def test_sources_with_zero_trackers_is_a_clean_empty_answer(client, env):
    r = _all(client)
    assert r.status_code == 200
    data = r.get_json()
    assert data == {'success': True, 'total': 0, 'releases': []}
    assert env['mock'].calls == []


def test_disabled_tracker_is_not_searched(client, env):
    rec = torznab(env['store'])
    env['store'].set_enabled(rec['id'], False)
    assert indexer_api.get_indexer_client(app.main.settings) is None
    assert _all(client).get_json()['releases'] == []
    assert env['mock'].calls == []


# ------------------------------------------------------------------ direct search

def test_enabled_tracker_replaces_prowlarr_even_when_prowlarr_is_set(env, monkeypatch):
    torznab(env['store'])
    monkeypatch.setattr(app.main.settings, 'get', lambda k, *a, **kw: {
        'indexer.provider': 'prowlarr', 'indexer.api_key': 'x' * 32,
        'indexer.base_url': 'http://prowlarr.invalid'}.get(k, ''))
    assert isinstance(indexer_api.get_indexer_client(app.main.settings), LumaTrackersClient)


def test_sources_returns_segmentable_releases_without_secrets(client, env):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(
        ('The Nut Job 2014 2160p UHD BluRay REMUX HDR HEVC-GRP', 12, 60 * 1024 ** 3),
        ('The Nut Job 2014 1080p WEB-DL DDP5.1 H.264-GRP', 80, 6 * 1024 ** 3),
        ('The Nut Job 2014 720p BluRay x264-GRP', 5, 3 * 1024 ** 3),
    )))
    r = _all(client, year=2014)
    assert r.status_code == 200
    rels = r.get_json()['releases']
    assert len(rels) == 3
    by_q = {x['quality']: x for x in rels}
    assert set(by_q) == {'2160P Remux', 'WEBDL-1080P', 'BLURAY-720P'}
    web = by_q['WEBDL-1080P']
    assert web['indexer'] == 'Jack' and web['seeders'] == 80 and web['size'] == 6 * 1024 ** 3
    assert web['tracker_type'] == 'torznab' and web['protocol'] == 'torrent'
    assert web['download_url'].startswith('/api/trackers/release/')
    assert web['guid'] == 'https://t.example/details/The.Nut.Job.2014.1080p.WEB-DL.DDP5.1.H.264-GRP'
    assert PASSKEY not in r.get_data(as_text=True) and APIKEY not in r.get_data(as_text=True)
    sent = env['mock'].calls[0]
    assert sent['params']['q'] == 'The Nut Job 2014' and sent['params']['apikey'] == APIKEY
    assert '2000' in sent['params']['cat'] and '5000' in sent['params']['cat']


def test_release_token_resolves_server_side_only(env):
    rec = torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('Movie 2020 1080p WEB-DL', 3, 1000))))
    out = LumaTrackersClient(env['engine']).search_all('Movie', 'movie', 2020)
    tok = out[0]['download_url'].rsplit('/', 1)[1]
    assert env['engine'].tokens.resolve(tok) == (rec['id'], f'https://t.example/dl/Movie.2020.1080p.WEB-DL?passkey={PASSKEY}')
    saved = (env['tmp'] / 'tracker_releases.json').read_text()
    assert PASSKEY not in saved and 't.example' not in saved
    again = ReleaseTokens(env['store'], env['tmp'] / 'tracker_releases.json')
    assert again.resolve(tok)[0] == rec['id']
    assert again.resolve('not-a-token') is None


def test_ipt_results_carry_tracker_name_and_type(client, env):
    ipt(env['store'], name='My private site')
    env['mock'].set('ipt.example', R(body=IPT_HTML))
    rels = _all(client).get_json()['releases']
    assert len(rels) == 1
    assert rels[0]['indexer'] == 'My private site' and rels[0]['tracker_type'] == 'iptorrents'
    assert rels[0]['seeders'] == 1234 and rels[0]['quality'] == 'BLURAY-1080P'
    assert env['mock'].calls[0]['headers']['Cookie'] == COOKIE
    assert 'abcdefabcdef' not in json.dumps(rels)


def test_results_ordered_by_priority_then_seeders(env):
    ipt(env['store'], priority=1)
    torznab(env['store'], priority=50)
    env['mock'].set('ipt.example', R(body=IPT_HTML))
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', 9999, 1))))
    out = LumaTrackersClient(env['engine']).search_all('The Nut Job', 'movie')
    assert [o['indexer'] for o in out] == ['IPT', 'Jack']


def test_one_failing_tracker_does_not_hide_the_others(client, env):
    torznab(env['store'])
    bad = torznab(env['store'], name='Down', host='down.example')
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', 4, 1))))
    env['mock'].set('down.example', R(502))
    rels = _all(client).get_json()['releases']
    assert [x['indexer'] for x in rels] == ['Jack']
    assert env['store'].get(bad['id'])['status']['state'] == 'error'
    # Backed off: the next search does not touch the failing tracker.
    app.main._all_results_cache.clear()
    n = len(env['mock'].hits('down.example'))
    _all(client, title='Another')
    assert len(env['mock'].hits('down.example')) == n


def test_rate_limit_honors_retry_after(env):
    rec = torznab(env['store'])
    env['mock'].set('t.example', R(429, headers={'Retry-After': '600'}))
    eng = env['engine']
    cfg = env['store'].config(rec)
    with pytest.raises(TrackerError):
        eng.search_one(cfg, 'x', [2000], 10, True)
    until, kind = env['guard'].blocked(rec['id'])
    assert kind == 'rate' and until - time.time() > 590
    with pytest.raises(TrackerSkipped):
        eng.search_one(cfg, 'y', [2000], 10, True)
    assert len(env['mock'].calls) == 1


def test_answers_are_cached_but_empty_answers_are_not(env):
    rec = torznab(env['store'])
    cfg = env['store'].config(rec)
    eng = env['engine']
    env['mock'].set('t.example', R(body=EMPTY_RSS))
    assert eng.search_one(cfg, 'x', [2000], 10, True) == []
    assert eng.search_one(cfg, 'x', [2000], 10, True) == []
    assert len(env['mock'].calls) == 2
    env['mock'].set('t.example', R(body=rss(('X 2020 1080p WEB-DL', 1, 1))))
    eng.search_one(cfg, 'y', [2000], 10, True)
    eng.search_one(cfg, 'y', [2000], 10, True)
    assert len(env['mock'].calls) == 3


def test_avistaz_family_is_rate_strict_for_user_searches(env):
    rec, errors = env['store'].create({'type': 'privatehd', 'name': 'PHD', 'url': 'https://phd.example',
                                       'username': 'me', 'password': 'pw_secret_1', 'pid': 'pid_secret_1',
                                       'categories': [2000], 'priority': 5})
    assert not errors
    env['mock'].set('phd.example', R(body={'token': 'tok', 'expiry': 3600}), R(body={'data': []}))
    cfg = env['store'].config(rec)
    sent = 0
    for i in range(7):
        try:
            env['engine'].search_one(cfg, f'q{i}', [2000], 10, True)
            sent += 1
        except TrackerSkipped:
            pass
    assert sent == 5


def test_tracker_only_gets_categories_it_is_set_up_for(env):
    torznab(env['store'], categories=[2000])
    env['mock'].set('t.example', R(body=rss(('Show S01 1080p WEB-DL', 1, 1))))
    client = LumaTrackersClient(env['engine'])
    assert client._search_single('Show', '5000', 50) == []
    assert env['mock'].calls == []
    client._search_single('Movie', '2000', 50)
    cats = env['mock'].calls[0]['params']['cat'].split(',')
    assert '2000' in cats and not any(c.startswith('5') for c in cats)


def test_pinned_search_on_backed_off_tracker_says_skipped(env):
    rec = torznab(env['store'])
    env['guard'].failure(rec['id'], 'network')
    with pytest.raises(indexer_api.IndexerSkipped):
        LumaTrackersClient(env['engine'])._search_single('x', '2000', 10, indexer_ids=[rec['id']])


def test_adding_a_tracker_drops_cached_empty_sources(client, env):
    assert _all(client).get_json()['releases'] == []
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', 4, 1))))
    r = client.post('/api/trackers', data=json.dumps({
        'type': 'torznab', 'name': 'Jack', 'url': 'https://t.example/api', 'api_key': APIKEY,
        'categories': [2000], 'priority': 25}), content_type='application/json')
    assert r.status_code == 201, r.get_json()
    assert len(_all(client).get_json()['releases']) == 1


def test_disabling_a_tracker_drops_cached_sources(client, env):
    rec = torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', 4, 1))))
    assert len(_all(client).get_json()['releases']) == 1
    r = client.put(f"/api/trackers/{rec['id']}/enabled", data=json.dumps({'enabled': False}),
                   content_type='application/json')
    assert r.status_code == 200
    assert _all(client).get_json()['releases'] == []


def test_titles_carrying_a_secret_are_dropped(env):
    torznab(env['store'])
    env['mock'].set('t.example', R(body=rss((f'Leaky {APIKEY}', 1, 1), ('Fine 2020 1080p WEB-DL', 1, 1))))
    out = LumaTrackersClient(env['engine']).search_all('x', 'movie')
    assert [o['title'] for o in out] == ['Fine 2020 1080p WEB-DL']


def test_parser_bug_is_a_short_error_and_backoff(env, monkeypatch):
    rec = torznab(env['store'])
    monkeypatch.setitem(tracker_clients.CLIENTS['torznab'], 'search',
                        lambda *a, **kw: (_ for _ in ()).throw(KeyError('boom')))
    out = LumaTrackersClient(env['engine']).search_all('x', 'movie')
    assert out == []
    st = env['store'].get(rec['id'])['status']
    assert st['state'] == 'error' and st['message'] == 'Unexpected response'


def test_engine_follows_a_swapped_store(tmp_path, env):
    tracker_search.reset_search_for_tests(None)
    eng = tracker_search.get_search()
    assert eng.store is env['store']
    other = TrackerStore(tmp_path / 'o.json', tmp_path / 'o.key')
    tracker_store.reset_store_for_tests(other)
    assert eng.store is other and eng.tokens._store is other
