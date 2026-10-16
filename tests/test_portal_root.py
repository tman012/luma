"""The portal host's root redirects to the portal page; the main host's root
is unchanged."""
import pytest

import app.main

PORTAL = 'requests.example.com'


@pytest.fixture
def client(test_app, monkeypatch):
    saved = app.main.settings.get('request_portal')
    app.main.settings.update({'request_portal': {'hostname': PORTAL, 'enabled': True}})
    monkeypatch.setitem(test_app.config, 'URL_BASE', '')
    yield test_app.test_client()
    if saved is not None:
        app.main.settings.update({'request_portal': saved})


@pytest.mark.parametrize('host', [PORTAL, PORTAL + '.', 'Requests.Example.COM', 'REQUESTS.example.com.:443'])
@pytest.mark.parametrize('method', ['get', 'head'])
def test_portal_root_redirects_to_request(client, host, method):
    r = getattr(client, method)('/', headers={'Host': host})
    assert r.status_code == 302
    assert r.headers['Location'].rstrip('/').rsplit('/', 1)[-1] == 'request'


def test_query_string_kept(client):
    r = client.get('/?ref=qr&x=a%20b', headers={'Host': PORTAL})
    assert r.status_code == 302 and r.headers['Location'].endswith('/request?ref=qr&x=a%20b')


def test_url_base_aware(client, test_app, monkeypatch):
    monkeypatch.setitem(test_app.config, 'URL_BASE', '/luma')
    r = client.get('/?a=1', headers={'Host': PORTAL})
    assert r.status_code == 302 and r.headers['Location'].endswith('/luma/request?a=1')


def test_main_host_root_unchanged(client, monkeypatch):
    """The main host's / still serves the app (same response as with no portal)."""
    def get_root():
        r = client.get('/', headers={'Host': 'luma.example.com'}, environ_base={'REMOTE_ADDR': '127.0.0.1'})
        return r.status_code, r.headers.get('Location')
    with_portal = get_root()
    app.main.settings.update({'request_portal': {'hostname': '', 'enabled': False}})
    assert with_portal == get_root()
    assert not (with_portal[1] or '').endswith('/request')


@pytest.mark.parametrize('path', ['/library', '/api/settings', '/favicon.ico', '/request/../settings'])
def test_other_portal_paths_still_404(client, path):
    assert client.get(path, headers={'Host': PORTAL}).status_code == 404
