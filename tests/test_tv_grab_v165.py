"""Reviewer on 006b4a6 (v165, security): (1) the TV Sources popup grab takes only a release token
or one of main's Sources links: a raw URL or magnet is a 400 and never reaches safe_magnet or
outbound_guard (a pasted passkey link can't get around the tracker budget); signed-in owner only.
(2) the scoped qBittorrent add allowlists its fields. (3) v172: the LAN Prowlarr bridge is
gone (tracker port step 4): no Sources link is fetched from the LAN."""
import pytest
import requests

from app import indexer_api, instance, tracker_sources, tv_grab
from tests.test_tv_v158c import BODY, _post, known_token, popup, qenv  # noqa: F401
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

GRAB = '/api/settings/tv/grab'
LAN = {'REMOTE_ADDR': '192.168.1.20'}
KEY = 'svc-' + 'k' * 20
PROWLARR = 'http://192.168.1.5:9696'
PASSKEY_URL = 'https://tracker.example/download.php?id=1&passkey=0123456789abcdef'
MAGNET = 'magnet:?xt=urn:btih:' + 'a' * 40


# ------------------------------------------------------------------ (1) the route

@pytest.fixture
def auth(minimal_app, popup, monkeypatch):  # noqa: F811
    import app.main
    monkeypatch.setattr(app.main, '_SERVICE_API_KEY', KEY)
    app.main.settings.update({'security': {'auth_required': 'disabled_local'}})
    return minimal_app


def _raw(c, env=REMOTE, headers=HDR, body=None):
    return c.post(GRAB, json=body or BODY, environ_base=env, headers=headers)


def test_the_implicit_lan_owner_gets_401(auth, popup):  # noqa: F811
    _client(auth)
    r = _raw(auth.test_client(), LAN)
    assert r.status_code == 401 and r.get_json()['sign_in'] is True and popup.calls == []


@pytest.mark.parametrize('env', [LAN, REMOTE])
def test_the_service_key_gets_401(auth, popup, env):  # noqa: F811
    _client(auth)
    r = _raw(auth.test_client(), env, dict(HDR, **{'X-API-Key': KEY}))
    assert r.status_code == 401 and popup.calls == []


def test_no_session_gets_401(auth, popup):  # noqa: F811
    _client(auth)
    assert _raw(auth.test_client()).status_code == 401 and popup.calls == []


@pytest.mark.parametrize('who', ['admin', 'guest'])
def test_admin_and_guest_get_403(auth, popup, who):  # noqa: F811
    _client(auth)                                                      # the owner
    c = _client(auth, admin=(who == 'admin'), name=who)
    r = _raw(c)
    assert r.status_code == 403 and popup.calls == []


@pytest.mark.parametrize('url', [PASSKEY_URL, MAGNET, 'http://192.168.1.5:9696/1/download?apikey=x&link=y',
                                 '//tracker.example/x.torrent', 'ftp://x/y'])
def test_a_raw_url_or_magnet_is_a_400_and_nothing_is_fetched(qenv, owner, monkeypatch, url):  # noqa: F811
    from app import outbound_guard
    monkeypatch.setattr(outbound_guard, 'fetch', lambda *a, **k: pytest.fail('outbound_guard reached'))
    monkeypatch.setattr(tracker_sources, 'safe_magnet', lambda *a, **k: pytest.fail('safe_magnet reached'))
    monkeypatch.setattr(tv_grab, 'grab', lambda *a, **k: pytest.fail('grab reached'))
    r = _post(owner, {**BODY, 'download_url': url})
    assert r.status_code == 400 and r.get_json()['fallback'] is False


def test_the_route_asks_for_tokens_only(qenv, owner, monkeypatch, known_token):  # noqa: F811
    seen = []
    monkeypatch.setattr(tv_grab, 'grab', lambda *a, **k: seen.append(k.get('tokens_only')) or {'ok': False, 'error': 'x'})
    _post(owner, BODY)
    assert seen == [True]


# ------------------------------------------------------------------ (1) fetch_link, tokens only

@pytest.fixture
def no_outbound(monkeypatch):
    from app import outbound_guard
    monkeypatch.setattr(outbound_guard, 'fetch', lambda *a, **k: pytest.fail('outbound_guard reached'))


@pytest.fixture
def sources(monkeypatch):
    links = {}
    monkeypatch.setattr(tv_grab, '_link_resolvers', [lambda u: (u.startswith('/src/'), links.get(u))])
    return links


@pytest.mark.parametrize('url', [PASSKEY_URL, MAGNET])
def test_tokens_only_refuses_raw_links_before_any_fetch(no_outbound, sources, monkeypatch, url):
    monkeypatch.setattr(tracker_sources, 'safe_magnet', lambda *a, **k: pytest.fail('safe_magnet reached'))
    with pytest.raises(tracker_sources.GrabError) as e:
        tv_grab.fetch_link(url, tokens_only=True)
    assert e.value.status == 400


def test_tokens_only_takes_a_sources_link_to_a_magnet(no_outbound, sources):
    sources['/src/m'] = MAGNET
    assert tv_grab.fetch_link('/src/m', tokens_only=True) == ('magnet', MAGNET)
    with pytest.raises(tracker_sources.GrabError) as e:
        tv_grab.fetch_link('/src/gone', tokens_only=True)
    assert e.value.status == 410


class S(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


@pytest.fixture
def stale_prowlarr(monkeypatch):
    """v172: an install that still has its old Prowlarr saved (the bridge is gone; it must be ignored)."""
    from app import library_mover as mover
    monkeypatch.setitem(mover._cfg, 'settings', S({'indexer.provider': 'prowlarr', 'indexer.base_url': PROWLARR,
                                                   'indexer.api_key': 'k' * 32}))


@pytest.mark.parametrize('tokens_only', [True, False])
@pytest.mark.parametrize('real', [
    PROWLARR + '/3/download?apikey=k&link=abc&file=x',            # the saved Prowlarr itself (v172: no bridge)
    PASSKEY_URL,
    'http://10.0.0.9:9696/3/download?apikey=k&link=abc',          # another LAN host
    'http://192.168.1.5:9697/3/download?apikey=k&link=abc',
    'https://192.168.1.5:9696/3/download?apikey=k&link=abc',
    'http://192.168.1.50:9696/3/download?apikey=k&link=abc',
    'http://192.168.1.5.evil:9696/3/download',
    'http://user@192.168.1.5:9696/3/download',
])
def test_a_sources_link_is_never_fetched_from_the_lan_even_with_prowlarr_saved(
        sources, stale_prowlarr, monkeypatch, real, tokens_only):
    """v172 (tracker port step 4): the LAN Prowlarr bridge is gone. Popup grabs (tokens_only) refuse a
    non-magnet Sources link outright; Luma's own grabs go to outbound_guard, which refuses every private
    address, so nothing on the LAN is fetched either way, the saved Prowlarr included."""
    from app import outbound_guard
    tried = []

    def guard(u, *a, **k):
        tried.append(u)
        raise outbound_guard.BlockedURL('private')
    monkeypatch.setattr(outbound_guard, 'fetch', guard)
    sources['/src/x'] = real
    with pytest.raises(tracker_sources.GrabError) as e:
        tv_grab.fetch_link('/src/x', tokens_only=tokens_only)
    assert e.value.status == 400
    assert tried == ([] if tokens_only else [real])               # popup: nothing even tried


def test_the_lan_bridge_is_gone():
    assert not hasattr(tv_grab, 'lan_prowlarr_fetch')
    assert not hasattr(indexer_api, 'ProwlarrClient') and not hasattr(indexer_api, 'TorznabClient')
    import ast
    import inspect
    code = ast.unparse(ast.parse(inspect.getsource(tv_grab.fetch_link)).body[0].body[1:])   # no docstring
    assert 'prowlarr' not in code.lower() and 'indexer.' not in code


# ------------------------------------------------------------------ (2) the scoped add allowlist

def _guarded(monkeypatch):
    sent = []
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')
    monkeypatch.setattr(requests.Session, 'send', lambda self, req, **k: sent.append(req) or requests.Response())
    instance.uninstall_write_guard()
    monkeypatch.setattr(instance, '_installed', {'on': False})
    instance.install_write_guard()
    return sent


NAME = 'luma-test-tv'
ADD = 'http://qbit/api/v2/torrents/add'
CAT = 'http://qbit/api/v2/torrents/createCategory'


def test_the_allowlisted_add_fields_pass(monkeypatch):
    sent = _guarded(monkeypatch)
    try:
        s = requests.Session()
        with instance.scoped_add('tv_grab', NAME):
            s.post(ADD, data={'urls': MAGNET, 'category': NAME, 'tags': NAME, 'autoTMM': 'false', 'paused': 'true', 'stopped': 'true'})
            s.post(ADD, data={'category': NAME, 'tags': NAME, 'autoTMM': 'false'}, files={'torrents': ('r.torrent', b'd4:infoe')})
            s.post(CAT, data={'category': NAME})
        assert len(sent) == 3
    finally:
        instance.uninstall_write_guard()


@pytest.mark.parametrize('extra', [{'downloadPath': '/x'}, {'useDownloadPath': 'true'}, {'savepath': ''}, {'savepath': '/x'},
                                   {'savePath': '/x'}, {'rename': 'x'}, {'contentLayout': 'NoSubfolder'}, {'root_folder': 'true'},
                                   {'firstLastPiecePrio': 'true'}, {'sequentialDownload': 'true'}, {'upLimit': '1'},
                                   {'cookie': 'x'}, {'Category': NAME}, {'autoTMM': 'true'},
                                   {'autoTMM': 'False'}, {'autoTMM': ''}])
def test_any_other_add_field_is_refused(monkeypatch, extra):
    sent = _guarded(monkeypatch)
    try:
        s = requests.Session()
        with instance.scoped_add('tv_grab', NAME):
            with pytest.raises(instance.WritesBlocked):
                s.post(ADD, data={'urls': MAGNET, 'category': NAME, 'tags': NAME, 'autoTMM': 'false', **extra})
        assert sent == []
    finally:
        instance.uninstall_write_guard()


@pytest.mark.parametrize('data,files', [
    ({'category': NAME, 'savePath': ''}, None),
    ({'category': NAME, 'downloadPath': '/x'}, None),
    ({'category': NAME, 'tags': NAME}, None),
    ({'category': NAME, 'tags': NAME}, {'urls': ('x.torrent', b'd')}),          # urls only as text
    ({'urls': MAGNET, 'category': NAME, 'tags': NAME}, {'torrents': (None, 'd4:infoe')}),   # torrents only as a file
    ({'category': NAME, 'tags': NAME, 'autoTMM': 'false'}, None),               # an add with nothing to add
    ({'urls': MAGNET, 'category': NAME, 'tags': NAME}, None),                   # v166: autoTMM missing
])
def test_create_category_and_add_shapes_outside_the_allowlist_are_refused(monkeypatch, data, files):
    sent = _guarded(monkeypatch)
    try:
        s = requests.Session()
        url = CAT if 'tags' not in data else ADD
        with instance.scoped_add('tv_grab', NAME):
            with pytest.raises(instance.WritesBlocked):
                s.post(url, data=data, files=files)
        assert sent == []
    finally:
        instance.uninstall_write_guard()


def test_every_add_luma_makes_sends_autotmm_false():
    """TV grabs (tv_grab._fields) and Movies grabs (main._qbit_add_fields) both send autoTMM=false."""
    import app.main
    assert tv_grab._fields(NAME)['autoTMM'] == 'false'
    assert app.main._qbit_add_fields()['autoTMM'] == 'false'


# ------------------------------------------------------------------ v166 (Tester): junk tokens are a 400 too

JUNK = ['/api/trackers/release/', '/api/trackers/release/!!garbage!!', '/api/trackers/release/short',
        '/api/trackers/release/' + 'y' * 20, '/api/trackers/release/' + 'x' * 20 + '?a=1', '/api/trackers/release/' + 'x' * 20 + '#f',
        '/api/trackers/release/../../api/downloads/add', '/api/trackers/release/https://evil/x',
        '/api/sources/link/', '/api/sources/link/garbage', '/api/sources/link/' + 'a' * 32, '/api/sources/link/../x',
        '/api/sources/link/' + 'a' * 32 + '?q=1', '../', '/../api/trackers/release/' + 'x' * 20]


@pytest.mark.parametrize('url', JUNK)
@pytest.mark.parametrize('grabs_on', [True, False])
def test_a_junk_unknown_or_query_token_is_a_400_with_no_fallback(qenv, owner, monkeypatch, known_token, url, grabs_on):  # noqa: F811
    if not grabs_on:
        monkeypatch.delenv('LUMA_TEST_ALLOW')
    monkeypatch.setattr(tv_grab, 'grab', lambda *a, **k: pytest.fail('grab reached'))
    r = _post(owner, {**BODY, 'download_url': url})
    assert r.status_code == 400
    out = r.get_json()
    assert out['ok'] is False and out['fallback'] is False and 'blocked' not in out


def test_a_known_token_still_goes_through(qenv, owner, monkeypatch, known_token):  # noqa: F811
    seen = []
    monkeypatch.setattr(tv_grab, 'grab', lambda *a, **k: seen.append(a[0]['downloadUrl']) or {'ok': True})
    assert _post(owner, BODY).status_code == 200 and seen == [BODY['download_url']]
