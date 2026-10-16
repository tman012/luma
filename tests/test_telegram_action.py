"""Telegram admin links: full HMAC with SECRET_KEY, 24 h expiry, single use,
GET shows a confirm page only, the page escapes everything and forbids scripts."""

import json
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
    assert set(f) == {'action', 'uid', 'exp', 'nonce', 'ver', 'sig'}
    assert len(f['sig']) == 64
    import hashlib, hmac
    msg = f"telegram-action:{f['action']}:{f['uid']}:{f['exp']}:{f['nonce']}:{f['ver']}".encode()
    assert f['sig'] == hmac.new(b'k' * 40, msg, hashlib.sha256).hexdigest()
    assert abs(int(f['exp']) - (time.time() + 24 * 3600)) < 5


def test_get_shows_confirm_page_and_changes_nothing(env, monkeypatch):
    client, uid, _ = env
    user_auth.set_user_status(uid, 'pending')
    _set_plex(uid, 'pending')
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
    _set_plex(uid, 'pending')   # deny_plex needs a pending or approved request
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
    _set_plex(uid, 'pending')   # deny_plex needs a pending or approved request
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


def test_corrupt_nonce_store_revokes_earlier_links(env, monkeypatch, caplog):
    client, uid, state = env
    _set_plex(uid, 'pending')   # deny_plex needs a pending or approved request
    monkeypatch.setattr(user_auth, 'save_user_settings', lambda *a: None)
    _, used = _fields('deny_plex', uid)
    assert client.post(PATH, data=used, environ_base=REMOTE).status_code == 200
    _, unused_old = _fields('approve_plex', uid)
    (state / action_tokens.NONCE_FILENAME).write_text('{broken')
    with caplog.at_level('ERROR'):
        # replay of the used link after corruption: refused
        assert client.post(PATH, data=used, environ_base=REMOTE).status_code == 401
    assert any('moved it to' in r.message for r in caplog.records)
    assert [p for p in state.iterdir() if p.name.startswith(action_tokens.NONCE_FILENAME + '.bad-')]
    # every link issued before the corruption is refused, even an unused one
    assert action_tokens.check('k' * 40, unused_old, state)[1] == 'revoked'
    # the cutoff is saved: still refused after a fresh read
    saved = json.loads((state / action_tokens.NONCE_FILENAME).read_text())
    assert saved['reject_issued_before'] > 0
    # links issued after the cutoff work
    later = action_tokens.sign('k' * 40, 'deny_plex', uid, now=saved['reject_issued_before'] + 1)
    later['exp'] = str(int(later['exp']))
    tok, reason = action_tokens.check('k' * 40, later, state, now=saved['reject_issued_before'] + 2)
    assert reason is None


@pytest.mark.parametrize('raw', ['[]', '{"used": []}', '{"used": {"n": "x"}}', 'null'])
def test_wrong_shape_nonce_store_revokes(env, raw):
    _, uid, state = env
    _, f = _fields('deny_plex', uid)
    (state / action_tokens.NONCE_FILENAME).write_text(raw)
    assert action_tokens.check('k' * 40, f, state) == (None, 'revoked')


def test_failed_action_releases_link_for_retry(env, monkeypatch):
    client, uid, state = env
    _set_plex(uid, 'pending')   # deny_plex needs a pending or approved request
    def boom(*a):
        raise RuntimeError('db locked')
    monkeypatch.setattr(user_auth, 'save_user_settings', boom)
    _, f = _fields('deny_plex', uid)
    r = client.post(PATH, data=f, environ_base=REMOTE)
    assert r.status_code == 500 and b'already been used' not in r.data and b'Try the link again' in r.data
    monkeypatch.setattr(user_auth, 'save_user_settings', lambda *a: None)
    assert client.post(PATH, data=f, environ_base=REMOTE).status_code == 200


def _set_plex(uid, status):
    user_auth.save_user_settings(uid, {'plex_access_status': status})


def test_partial_write_keeps_link_used_and_replay_is_harmless(env, monkeypatch):
    """approve_plex saves, then fails; a deny follows; replaying the approve
    link must not undo the deny."""
    client, uid, state = env
    _set_plex(uid, 'pending')
    real_save = user_auth.save_user_settings

    def save_then_fail(u, s):
        real_save(u, s)
        raise RuntimeError('crashed after the write')

    monkeypatch.setattr(user_auth, 'save_user_settings', save_then_fail)
    _, approve = _fields('approve_plex', uid)
    r = client.post(PATH, data=approve, environ_base=REMOTE)
    assert r.status_code == 500 and b'Try the link again' not in r.data
    assert _settings(uid)['plex_access_status'] == 'approved'
    monkeypatch.setattr(user_auth, 'save_user_settings', real_save)

    _, deny = _fields('deny_plex', uid)
    assert client.post(PATH, data=deny, environ_base=REMOTE).status_code == 200
    assert _settings(uid)['plex_access_status'] == 'denied'

    r = client.post(PATH, data=approve, environ_base=REMOTE)
    assert r.status_code in (401, 410)          # still used: never released
    assert _settings(uid)['plex_access_status'] == 'denied'
    # even if the link were released, the precondition protects the deny
    action_tokens.release(state, approve['nonce'])
    r = client.post(PATH, data=approve, environ_base=REMOTE)
    assert r.status_code == 409 and b'Already Handled' in r.data
    assert _settings(uid)['plex_access_status'] == 'denied'
    assert action_tokens.check('k' * 40, approve, state) == (None, 'used')   # consumed


@pytest.mark.parametrize('action,setup', [
    ('approve_user', lambda uid: user_auth.set_user_status(uid, 'active')),
    ('deny_user', lambda uid: user_auth.set_user_status(uid, 'active')),
    ('approve_plex', lambda uid: _set_plex(uid, 'approved')),
    ('approve_plex', lambda uid: _set_plex(uid, 'denied')),
    ('deny_plex', lambda uid: _set_plex(uid, 'denied')),
])
def test_handled_request_shows_already_handled(env, action, setup):
    client, uid, state = env
    setup(uid)
    before = (user_auth.get_user_by_id(uid), _settings(uid))
    _, f = _fields(action, uid)
    r = client.get(PATH, query_string=f, environ_base=REMOTE)
    assert r.status_code == 409 and b'<form' not in r.data
    r = client.post(PATH, data=f, environ_base=REMOTE)
    assert r.status_code == 409 and b'already handled' in r.data
    assert (user_auth.get_user_by_id(uid), _settings(uid)) == before
    assert action_tokens.check('k' * 40, f, state) == (None, 'used')


@pytest.mark.parametrize('action', ['approve_user', 'deny_user'])
def test_pending_account_actions_still_work(env, action):
    client, uid, _ = env
    user_auth.set_user_status(uid, 'pending')
    _, f = _fields(action, uid)
    assert client.post(PATH, data=f, environ_base=REMOTE).status_code == 200
    u = user_auth.get_user_by_id(uid)
    assert (u and u['status']) == ('active' if action == 'approve_user' else None)


def test_quarantine_and_cutoff_happen_under_file_lock(env, monkeypatch):
    _, uid, state = env
    import app.paths as paths
    seen = []
    real_q, real_save = paths.quarantine, action_tokens._save
    monkeypatch.setattr(paths, 'quarantine', lambda *a, **k: (seen.append(action_tokens._held.depth), real_q(*a, **k))[1])
    monkeypatch.setattr(action_tokens, '_save', lambda *a: (seen.append(action_tokens._held.depth), real_save(*a))[1])
    (state / action_tokens.NONCE_FILENAME).write_text('{broken')
    _, f = _fields('deny_plex', uid)
    action_tokens.check('k' * 40, f, state)
    assert seen and all(d >= 1 for d in seen)
    assert action_tokens._held.depth == 0


def test_quarantine_names_are_unique(tmp_path):
    from app.paths import quarantine
    names = set()
    for _ in range(3):
        p = tmp_path / 'state.json'
        p.write_text('x')
        names.add(quarantine(p, 'test').name)
    assert len(names) == 3
    assert all(re.fullmatch(r'state\.json\.bad-\d+-\d+-\d+', n) for n in names)


# ---- Links are bound to one request (ver) ----

def _post(client, f):
    return client.post(PATH, data=f, environ_base=REMOTE)


def test_old_approve_plex_link_cannot_approve_newer_request(env):
    client, uid, _ = env
    user_auth.save_user_settings(uid, {'plex_access_status': 'pending', 'plex_access_requested_at': '100.000001'})
    _, old = _fields('approve_plex', uid)
    # admin denies, user asks again: a new request with a new version
    user_auth.save_user_settings(uid, {'plex_access_status': 'denied'})
    user_auth.save_user_settings(uid, {'plex_access_status': 'pending', 'plex_access_requested_at': '200.000002'})
    r = client.get(PATH, query_string=old, environ_base=REMOTE)
    assert r.status_code == 410
    assert _post(client, old).status_code == 410
    assert user_auth.get_user_settings(uid).get('plex_access_status') == 'pending'
    _, new = _fields('approve_plex', uid)
    assert _post(client, new).status_code == 200
    assert user_auth.get_user_settings(uid).get('plex_access_status') == 'approved'


def test_tampered_ver_is_bad_signature(env):
    client, uid, _ = env
    user_auth.save_user_settings(uid, {'plex_access_status': 'pending', 'plex_access_requested_at': '1.0'})
    _, f = _fields('approve_plex', uid)
    f['ver'] = 'p:2.0'
    assert client.get(PATH, query_string=f, environ_base=REMOTE).status_code != 200


@pytest.mark.parametrize('status,ok', [('pending', True), ('approved', True), ('denied', False), (None, False)])
def test_deny_plex_needs_pending_or_approved(env, status, ok):
    _, uid, _ = env
    user_auth.save_user_settings(uid, {'plex_access_status': status})
    assert app.main._admin_action_pending('deny_plex', user_auth.get_user_by_id(uid)) is ok
