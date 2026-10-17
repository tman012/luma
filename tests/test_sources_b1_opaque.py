"""Reviewer B1: /api/sources/all-results on the Prowlarr/Torznab path (no Luma
trackers) never sends a key, passkey or announce passkey to the browser.
Every download link and magnet is opaque and swapped back in downloads/add;
every URL field is an opaque id; the configured indexer key is removed from
every field; and the viewer check runs on this path too."""
import json

import pytest

import app.main
import app.user_auth
from app import tracker_sources
from tests.test_downloads_add_ssrf import (  # noqa: F401  (fixtures)
    net, qbit, client, _owner_client, _post, LAN, PUBLIC, TORRENT)

IDX_KEY = 'idxKEY7f3a91c2'
UUID = '3f2b8c4e-91d7-4a6b-b0e2-7c5d9a1f8e34'
LEAKS = [
    # (download_url, secret that must not appear)
    ('https://jackett.example/dl/x/?jackett_apikey=jk9a8b7c6d5e4f3a2b1c0d&path=abc&file=X', 'jk9a8b7c6d5e4f3a2b1c0d'),
    ('https://unit3d.example/torrent/download/12345.r55k3yAbCdEf9', 'r55k3yAbCdEf9'),
    ('https://t.example/dl.php?id=5&pk=pk51df00aa', 'pk51df00aa'),
    ('https://t.example/getnzb/5.nzb?r=rkey1234ab', 'rkey1234ab'),
    ('https://t.example/dl/5/shortPK9x/file.torrent', 'shortPK9x'),
    (f'https://t.example/rss/download/5/{UUID}.torrent', UUID),
    (' https://t.example/dl?id=5&passkey=spacePK123', 'spacePK123'),
    ('magnet:?xt=urn:btih:' + 'a' * 40 + '&dn=X&tr=https%3A%2F%2Ft.example%2Fannounce%2FannPK77aa', 'annPK77aa'),
    (f'https://prowlarr.example/api/v1/indexer/1/download?apikey={IDX_KEY}&link=x', IDX_KEY),
]


class _FakeIndexer:
    def __init__(self, items):
        self.items, self.calls = items, 0

    def search_all(self, title, media_type, year):
        self.calls += 1
        return self.items


def _item(url, **kw):
    base = {'guid': url.strip(), 'title': 'Movie.2014.1080p-GRP', 'indexer': 'idx', 'indexer_id': 1,
            'size': 1, 'seeders': 3, 'download_url': url, 'protocol': 'torrent'}
    base.update(kw)
    return base


@pytest.fixture
def prowlarr(test_app, monkeypatch):
    monkeypatch.setattr(tracker_sources, 'trackers_in_use', lambda: False)
    app.main.settings.update({'indexer': {'api_key': IDX_KEY}})
    app.main._all_results_cache.clear()
    fake = _FakeIndexer([])
    import app.indexer_api as ia
    monkeypatch.setattr(ia, 'get_indexer_client', lambda s: fake)
    yield fake
    app.main._all_results_cache.clear()


def _user(test_app, admin=True, approved=False):
    app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    u = app.user_auth.create_user(f'u{admin}{approved}', 'pin-7654321', is_admin=admin) if not admin else \
        app.user_auth.get_user_by_name('owner') if hasattr(app.user_auth, 'get_user_by_name') else None
    if u is None:
        u = [x for x in app.user_auth.list_users() if x['name'] == 'owner'][0]
    if approved:
        app.user_auth.save_user_settings(u['id'], {'plex_access_status': 'approved'})
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(u['id']))
    return c


def _get(c):
    return c.get('/api/sources/all-results?title=Movie&media_type=movie&year=2014')


@pytest.mark.parametrize('url,secret', LEAKS)
def test_no_secret_reaches_the_browser(test_app, prowlarr, url, secret):
    prowlarr.items = [_item(url, info_url=url.strip(), comments=url.strip() + '#c')]
    r = _get(_user(test_app))
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_data(as_text=True)
    assert secret not in body
    rel = r.get_json()['releases'][0]
    assert rel['download_url'].startswith(app.main.SOURCE_LINK_PREFIX)
    assert rel['guid'].startswith('luma-link:'), rel['guid']
    for k in ('info_url', 'comments'):
        assert k not in rel or rel[k].startswith('luma-link:'), rel[k]
    # The helper itself makes every URL field opaque, whatever the route copies.
    pub = app.main._all_results_public({'releases': [dict(_item(url), info_url=url, comments=url, link=url)]})
    assert secret not in json.dumps(pub)
    assert all(pub['releases'][0][k].startswith('luma-link:') for k in ('guid', 'info_url', 'comments', 'link'))
    # Swapped back server-side to the real (trimmed) link.
    assert app.main._resolve_source_link(rel['download_url']) == (True, url.strip())


def test_cached_answer_is_redacted_too(test_app, prowlarr):
    prowlarr.items = [_item(LEAKS[0][0])]
    c = _user(test_app)
    _get(c)
    r = _get(c)
    assert prowlarr.calls == 1 and LEAKS[0][1] not in r.get_data(as_text=True)


def test_configured_key_removed_from_every_field(test_app, prowlarr):
    prowlarr.items = [_item('https://ok.example/a.torrent', title=f'Movie {IDX_KEY}', indexer=f'idx-{IDX_KEY}')]
    body = _get(_user(test_app)).get_data(as_text=True)
    assert IDX_KEY not in body


def test_even_a_harmless_http_link_is_opaque(test_app, prowlarr):
    prowlarr.items = [_item('https://ok.example/a.torrent')]
    rel = _get(_user(test_app)).get_json()['releases'][0]
    assert rel['download_url'].startswith(app.main.SOURCE_LINK_PREFIX)
    assert 'ok.example' not in json.dumps(rel)


def test_luma_tracker_release_links_stay_as_they_are():
    assert app.main._public_download_link('/api/trackers/release/abc') == '/api/trackers/release/abc'
    assert app.main._public_download_link('') == ''


@pytest.mark.parametrize('approved,code', [(False, 403), (True, 200)])
def test_viewer_check_on_the_prowlarr_path(test_app, prowlarr, approved, code):
    prowlarr.items = [_item(LEAKS[2][0])]
    c = _user(test_app, admin=False, approved=approved)
    r = _get(c)
    assert r.status_code == code
    assert prowlarr.calls == (1 if approved else 0)
    assert LEAKS[2][1] not in r.get_data(as_text=True)


def test_opaque_magnet_is_swapped_back_and_added(client, net, qbit):
    _owner_client(client)
    magnet = LEAKS[7][0]
    link = app.main._public_download_link(magnet)
    assert link.startswith(app.main.SOURCE_LINK_PREFIX)
    r = _post(client, link)
    assert r.status_code == 200, r.get_json()
    assert net.opens == []


def test_opaque_link_to_internal_host_is_still_refused(client, net, qbit):
    _owner_client(client)
    link = app.main._public_download_link(' http://169.254.169.254/latest/meta-data/?pk=x')
    r = _post(client, link)
    assert r.status_code == 400 and net.opens == [] and qbit.logins == 0
