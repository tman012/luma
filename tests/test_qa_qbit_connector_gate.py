"""QA gate (ceed5e5): Reviewer's added regression tests for the qBittorrent card
and the legacy qBittorrent settings routes.

(1) A stored password goes only to the exact URL it was saved with: every URL
    variant needs the password typed again, and nothing is sent anywhere.
(2) A redirect from qBittorrent's login is an error and is not followed.
(3) Non-minimal (prod) legacy routes: /api/settings/test/qbittorrent with the
    mask plus a new URL, and PUT /api/settings with a new qbittorrent.url.
(4) The service X-API-Key is refused on the card's routes.
"""

import pytest

import app.main
from app import qbit_connector
from tests.test_home_rails import REMOTE, _client
from tests.test_qbit_connector import API, HDR, PW, FakeQbit, R, minimal, qbit  # noqa: F401  (fixtures)

MASK = '********'
SAVED = 'http://192.168.1.95:8080'
VARIANTS = [
    'https://192.168.1.95:8080',          # scheme
    'http://192.168.1.95:8081',           # another port
    'http://192.168.1.95',                # no port (saved has :8080)
    'http://192.168.1.95:80',             # :80
    'http://192.168.1.95.:8080',          # trailing-dot host
    'http://192.168.1.95:8080/x',         # a path
    '//evil.example',                     # scheme-relative
    'http://192.168.1.95:8080//evil.example',
    'http://192%2e168.1.95:8080',         # %2e in the host
    'http://evil.example:8080',
]


def _save_owner_qbit(c):
    r = c.put(API, json={'url': SAVED, 'username': 'admin', 'password': PW, 'category': 'luma-test'},
              environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()


@pytest.mark.parametrize('url', VARIANTS)
@pytest.mark.parametrize('pw', [MASK, None])
def test_url_variant_needs_password_again_and_sends_nothing(minimal, qbit, url, pw):
    c = _client(minimal)
    _save_owner_qbit(c)
    qbit.calls.clear()
    body = {'url': url, 'username': 'admin', 'category': 'luma-test'}
    if pw is not None:
        body['password'] = pw
    r = c.post(API + '/test', json=body, environ_base=REMOTE, headers=HDR)
    j = r.get_json()
    assert r.status_code == 400 and j['ok'] is False, j
    assert 'password' in j['fields'] or j['fields'].get('url') == 'Invalid URL', j
    assert qbit.calls == [], qbit.calls
    # Save with the mask and the new URL must not carry the password over either.
    r = c.put(API, json=body, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400, r.get_json()
    assert app.main.settings.get('qbittorrent.url') == SAVED
    assert app.main.settings.get('qbittorrent.password') == PW


def test_same_url_with_mask_reuses_password(minimal, qbit):
    c = _client(minimal)
    _save_owner_qbit(c)
    qbit.calls.clear()
    r = c.post(API + '/test', json={'url': SAVED + '/', 'username': 'admin', 'password': MASK},
               environ_base=REMOTE, headers=HDR)
    assert r.get_json()['ok'] is True
    assert qbit.calls and all(call[1].startswith(SAVED + '/api/v2/') for call in qbit.calls)


def test_login_redirect_is_an_error_and_not_followed(minimal, monkeypatch):
    calls = []

    class S:
        def post(self, url, data=None, **kw):
            calls.append((url, kw))
            r = R(302, '')
            r.headers = {'Location': 'http://169.254.169.254/latest/meta-data/'}
            return r

        def get(self, url, **kw):
            calls.append((url, kw))
            return R(200, 'v4.6.5')

        def close(self):
            pass
    monkeypatch.setattr(qbit_connector.requests, 'Session', S)
    c = _client(minimal)
    r = c.post(API + '/test', json={'url': SAVED, 'username': 'admin', 'password': PW},
               environ_base=REMOTE, headers=HDR)
    j = r.get_json()
    assert j['ok'] is False and '302' in j['error'], j
    assert len(calls) == 1 and calls[0][1].get('allow_redirects') is False


def test_service_api_key_refused_on_card_routes(minimal, qbit, monkeypatch):
    monkeypatch.setenv('SERVICE_API_KEY', 'svc-' + 'k' * 20)
    _client(minimal)  # the owner exists
    c = minimal.test_client()
    h = dict(HDR, **{'X-API-Key': 'svc-' + 'k' * 20})
    assert c.get(API, environ_base=REMOTE, headers=h).status_code in (401, 403)
    assert c.put(API, json={'url': SAVED}, environ_base=REMOTE, headers=h).status_code in (401, 403)
    assert c.post(API + '/test', json={}, environ_base=REMOTE, headers=h).status_code in (401, 403)
    assert qbit.calls == []


# ------------------------------------------------------------------ (3) non-minimal legacy routes

@pytest.fixture
def full(test_app, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    saved = app.main.settings.get_section('qbittorrent')
    app.main.settings.update({'qbittorrent': {'url': SAVED, 'username': 'admin', 'password': PW, 'app_category': 'luma'}})
    yield test_app
    app.main.settings.update({'qbittorrent': saved})


def test_legacy_test_route_never_sends_saved_password_to_new_url(full, monkeypatch):
    sent = []

    def post(url, data=None, **kw):
        sent.append((url, dict(data or {})))
        return R(200, 'Fails.')
    monkeypatch.setattr(app.main.requests, 'post', post)
    c = _client(full)
    c.post('/api/settings/test/qbittorrent', json={'url': 'http://evil.example:8080', 'password': MASK},
           environ_base=REMOTE, headers=HDR)
    assert not [s for s in sent if s[1].get('password') == PW], sent


def test_legacy_put_settings_new_url_clears_or_requires_password(full):
    c = _client(full)
    r = c.put('/api/settings', json={'qbittorrent': {'url': 'http://evil.example:8080', 'password': MASK}},
              environ_base=REMOTE, headers=HDR)
    moved = app.main.settings.get('qbittorrent.url') == 'http://evil.example:8080'
    assert r.status_code == 400 or not moved or app.main.settings.get('qbittorrent.password') != PW
