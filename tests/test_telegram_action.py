"""Telegram admin links: full HMAC with SECRET_KEY, 24 h expiry, single use,
GET shows a confirm page only, the page escapes everything and forbids scripts."""

import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest

import app.main
import app.user_auth as user_auth
from app import action_tokens

REMOTE = {'REMOTE_ADDR': '203.0.113.7'}
PATH = '/api/admin/telegram-action'


@pytest.fixture
def env(test_app, tmp_path, monkeypatch):
    monkeypatch.setenv('LUMA_STATE_DIR', str(tmp_path))
    monkeypatch.setitem(app.main.app.config, 'SECRET_KEY', 'k' * 40)
    monkeypatch.setattr(app.main, '_public_app_url', lambda: 'https://luma.example')
    user_auth.create_user('owner', '1234', is_admin=True)
    uid = user_auth.create_user('<script>alert(1)</script>', '9999', is_admin=False)
    if isinstance(uid, dict):
        uid = uid['id']
    if not isinstance(uid, int):
        uid = user_auth.get_user_by_name('<script>alert(1)</script>')['id']
    return test_app.test_client(), uid, tmp_path


def _fields(action, uid):
    url = app.main._make_admin_action_url(action, uid)
    q = parse_qs(urlsplit(url).query)
    return url, {k: v[0] for k, v in q.items()}


def _settings(uid):
    return user_auth.get_user_settings(uid) if hasattr(user_auth, 'get_user_settings') else {}


def test_link_uses_full_hmac_sha256_with_secret_key(env):
    client, uid, _ = env
    url, f = _fields('approve_plex', uid)
    assert url.startswith('https://luma.example/api/admin/telegram-action?')
    assert set(f) == {'action', 'uid', 'exp', 'nonce', 'sig'}
    assert len(f['sig']) == 64
    import hashlib, hmac
    msg = f"telegram-action:{f['action']}:{f['uid']}:{f['exp']}:{f['nonce']}".encode()
    assert f['sig'] == hmac.new(b'k' * 40, msg, hashlib.sha256).hexdigest()
    assert abs(int(f['exp']) - (time.time() + 24 * 3600)) < 5


def test_get_shows_confirm_page_and_changes_nothing(env, monkeypatch):
    client, uid, _ = env
    calls = []
    monkeypatch.setattr(user_auth, 'save_user_settings', lambda *a, **k: calls.append(a))
    monkeypatch.setattr(user_auth, 'approve_user', lambda *a, **k: calls.append(a))
    monkeypatch.setattr(user_auth, 'deny_user', lambda *a, **k: calls.append(a))
    for action in action_tokens.ACTIONS:
        url, f = _fields(action, uid)
        for _ in range(2):  # opening twice (link previews) is harmless
            r = client.get(urlsplit(url).path + '?' + urlsplit(url).query, environ_base=REMOTE)
            assert r.status_code == 200
            assert b'<form method="post"' in r.data
    assert calls == []


def test_post_performs_once_then_replay_refused(env, monkeypatch):
    client, uid, _ = env
    calls = []
    monkeypatch.setattr(user_auth, 'save_user_settings', lambda u, s: calls.append((u, s)))
    _, f = _fields('deny_plex', uid)
    r = client.post(PATH, data=f, environ_base=REMOTE)
    assert r.status_code == 200 and calls == [(uid, {'use_shared_server': False, 'plex_access_status': 'denied'})]
    r = client.post(PATH, data=f, environ_base=REMOTE)
    assert r.status_code in (401, 410) and len(calls) == 1
    r = client.get(PATH, query_string=f, environ_base=REMOTE)
    assert r.status_code in (401, 410)


def test_used_nonce_survives_restart(env, monkeypatch):
    client, uid, state = env
    monkeypatch.setattr(user_auth, 'save_user_settings', lambda *a: None)
    _, f = _fields('deny_plex', uid)
    assert client.post(PATH, data=f, environ_base=REMOTE).status_code == 200
    assert (state / action_tokens.NONCE_FILENAME).exists()
    tok, reason = action_tokens.check('k' * 40, f, state)   # fresh read from disk
    assert (tok, reason) == (None, 'used')


def test_expired_link_refused(env, monkeypatch):
    client, uid, state = env
    f = action_tokens.sign('k' * 40, 'deny_plex', uid, now=time.time() - 24 * 3600 - 1)
    assert action_tokens.check('k' * 40, f, state) == (None, 'expired')
    assert client.post(PATH, data=f, environ_base=REMOTE).status_code == 401
    assert client.get(PATH, query_string=f, environ_base=REMOTE).status_code == 401


@pytest.mark.parametrize('field,value', [
    ('action', 'approve_user'), ('uid', '1'), ('exp', str(int(time.time()) + 999999)),
    ('nonce', 'other'), ('sig', '0' * 64),
])
def test_tampered_link_refused(env, monkeypatch, field, value):
    client, uid, state = env
    calls = []
    monkeypatch.setattr(user_auth, 'save_user_settings', lambda *a: calls.append(a))
    monkeypatch.setattr(user_auth, 'approve_user', lambda *a: calls.append(a))
    _, f = _fields('deny_plex', uid)
    f[field] = value
    assert client.post(PATH, data=f, environ_base=REMOTE).status_code == 401
    assert client.get(PATH, query_string=f, environ_base=REMOTE).status_code == 401
    assert calls == []


def test_truncated_signature_refused(env):
    client, uid, state = env
    _, f = _fields('deny_plex', uid)
    f['sig'] = f['sig'][:16]
    assert action_tokens.check('k' * 40, f, state) == (None, 'malformed')


def test_other_secret_refused(env):
    _, uid, state = env
    f = action_tokens.sign('another-secret', 'deny_plex', uid)
    assert action_tokens.check('k' * 40, f, state) == (None, 'bad signature')


def test_cross_site_post_refused(env, monkeypatch):
    client, uid, _ = env
    calls = []
    monkeypatch.setattr(user_auth, 'save_user_settings', lambda *a: calls.append(a))
    _, f = _fields('deny_plex', uid)
    r = client.post(PATH, data=f, environ_base=REMOTE, headers={'Origin': 'https://evil.example'})
    assert r.status_code == 403 and calls == []


def test_script_payload_is_escaped_and_csp_set(env):
    client, uid, _ = env
    url, f = _fields('approve_plex', uid)
    r = client.get(PATH, query_string=f, environ_base=REMOTE)
    body = r.get_data(as_text=True)
    assert '<script>' not in body
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in body
    csp = r.headers['Content-Security-Policy']
    assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp
    assert "script-src" not in csp
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert r.headers['Referrer-Policy'] == 'no-referrer'


def test_page_helper_escapes_every_value():
    with app.main.app.test_request_context('/api/admin/telegram-action'):
        r = app.main._admin_action_page('<b>t</b>', '<img src=x onerror=1>', form={'a"><x': '"><script>'},
                                        button='<i>go</i>')
    body = r.get_data(as_text=True)
    for raw in ('<b>t</b>', '<img', '"><x', '"><script>', '<i>go</i>'):
        assert raw not in body


def test_signed_out_remote_without_link_needs_sign_in(env):
    client, _, _ = env
    assert client.get(PATH, environ_base=REMOTE).status_code == 401
    assert client.post(PATH, data={}, environ_base=REMOTE).status_code == 401
