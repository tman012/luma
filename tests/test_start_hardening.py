"""Start flow hardening: username normalization, validated forward host,
stale password claims, Plex auth mode allowlist."""

import time
from unittest import mock

import pytest

from tests.test_minimal_start import ORIGIN, REMOTE, _PinResp, start_owner  # noqa: F401


def _post(c, name, pw='pass-word-1'):
    return c.post('/api/start/account', json={'username': name, 'password': pw}, headers=ORIGIN)


# -- usernames ---------------------------------------------------------------

def test_name_helpers():
    from app import user_auth as ua
    assert ua.normalize_name('\uff42\uff4f\uff42') == 'bob'
    assert ua.name_key('\u00e9mile') == ua.name_key('\u00c9mile')
    assert ua.name_problem('bob\u200b') and ua.name_problem('a\nb')
    assert ua.name_problem('b\u043eb')            # Cyrillic o in Latin name
    assert ua.name_problem('\u00e9mile') is None and ua.name_problem('Taylor 2') is None


@pytest.mark.parametrize('name', ['\u305f\u306a\u304b\u592a\u90ce', '\u5c71\u7530\u30bf\u30ed\u30a6',
                                  '\uae40\u6c11\uc218', 'Taylor\u592a\u90ce', 'user42', '\u0418\u0432\u0430\u043d',
                                  '\u039d\u03af\u03ba\u03bf\u03c2', '\u30bf\u30ed\u30fc', '\u4f50\u3005\u6728'])
def test_normal_mixed_script_names_pass(name):
    from app import user_auth as ua
    assert ua.name_problem(ua.normalize_name(name)) is None, name


def test_japanese_name_end_to_end(start_owner):
    c, _ = start_owner
    assert _post(c, '\u305f\u306a\u304b\u592a\u90ce').status_code == 200


def test_name_key_column_backfilled_and_used(test_app):
    import secrets
    from app import user_auth
    u = user_auth.create_user('\u00c9mile', secrets.token_urlsafe(12))
    with user_auth._get_db() as conn:
        assert conn.execute('SELECT name_key FROM users WHERE id = ?', (u['id'],)).fetchone()[0] == '\u00e9mile'
        conn.execute('UPDATE users SET name_key = NULL WHERE id = ?', (u['id'],))
    assert user_auth.identity_conflict('\u00e9MILE')          # legacy row without a key
    user_auth.init_db()                                          # backfill
    with user_auth._get_db() as conn:
        assert conn.execute('SELECT name_key FROM users WHERE id = ?', (u['id'],)).fetchone()[0] == '\u00e9mile'
        idx = [r[1] for r in conn.execute("PRAGMA index_list('users')").fetchall()]
    assert 'idx_users_name_key' in idx
    assert user_auth.identity_conflict('\u00c9MILE')


@pytest.mark.parametrize('name', ['bob\u200b', 'a\nb', 'b\u043eb', '\u200d', 'x\u202ey', 'b\u0336ob',
                                  'bo\u0338b', 'a\u20dd', 'p\u0430ypal', '\u03b1lpha', 'b\u043e\u03b2'])
def test_start_rejects_hidden_and_mixed_script(start_owner, name):
    c, _ = start_owner
    r = _post(c, name)
    assert r.status_code == 400 and r.json['error']


def test_fullwidth_stored_as_ascii(test_app, start_owner):
    from app import user_auth
    c, user = start_owner
    assert _post(c, '\uff42\uff4f\uff42').status_code == 200
    assert user_auth.get_user_by_id(user['id'])['name'] == 'bob'


@pytest.mark.parametrize('existing,attempt', [('\u00e9mile', '\u00c9mile'), ('bob', '\uff22\uff2f\uff22'),
                                              ('Stra\u00dfe', 'STRASSE')])
def test_casefold_uniqueness(start_owner, existing, attempt):
    import secrets
    from app import user_auth
    c, _ = start_owner
    user_auth.create_user(existing, secrets.token_urlsafe(12))
    r = _post(c, attempt)
    assert r.status_code == 400 and 'already exists' in r.json['error']


def test_create_user_rejects_zero_width_duplicate(test_app):
    import secrets
    from app import user_auth
    user_auth.create_user('carol', secrets.token_urlsafe(12))
    with pytest.raises(ValueError):
        user_auth.create_user('carol\u200b', secrets.token_urlsafe(12))


def test_owner_name_falls_back_for_bad_plex_name():
    from app import start_flow, user_auth
    assert start_flow._owner_name(user_auth, 'b\u043eb') == 'Owner'
    assert start_flow._owner_name(user_auth, 'a@b.c') == 'Owner'
    assert start_flow._owner_name(user_auth, None) == 'Owner'
    assert start_flow._owner_name(user_auth, ' plexuser ') == 'plexuser'


# -- forward URL -------------------------------------------------------------

def _forward(test_app, base_url, mode='redirect'):
    from urllib.parse import parse_qs, urlsplit
    with mock.patch('app.start_flow.requests.post', return_value=_PinResp()):
        r = test_app.test_client().post('/api/start/plex/pin', json={'mode': mode}, headers=ORIGIN,
                                        base_url=base_url, environ_base=REMOTE)
    assert r.status_code == 200
    return parse_qs(urlsplit(r.json['auth_url']).fragment[1:]).get('forwardUrl')


@pytest.mark.parametrize('host', ['evil.com/x', 'a b', 'x"y', 'host:99999', '10.0.0.5:0', '10.0.0.5:65536',
                                  '-bad-.com', 'evil.example', 'evil.example:5002'])
def test_valid_host_rejects(host):
    from app import start_flow
    assert start_flow.valid_host(host) is None


@pytest.mark.parametrize('host', ['luma.local', 'luma.local:5002', '10.0.0.5:8080', '10.0.0.5:65535',
                                  '[::1]:5002', 'localhost', 'nas:1'])
def test_valid_host_accepts_known(host):
    from app import start_flow
    assert start_flow.valid_host(host) == host


@pytest.mark.parametrize('host', ['evil.com/<x>', 'a b', 'x"y', 'evil.example'])
def test_forward_url_omitted_for_bad_host(test_app, host):
    from app import start_flow
    with test_app.test_request_context('/api/start/plex/pin', environ_overrides={'HTTP_HOST': host}):
        assert start_flow.forward_url('redirect') == ''
        assert 'forwardUrl' not in start_flow.auth_url('abc', start_flow.forward_url('redirect'))


def test_evil_example_gets_no_forward_url_by_default(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    monkeypatch.delenv('LUMA_ALLOWED_HOSTS', raising=False)
    assert _forward(test_app, 'http://evil.example') is None
    assert _forward(test_app, 'http://evil.example:5002') is None


def test_allowed_hosts_admit_public_name(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    monkeypatch.setenv('LUMA_ALLOWED_HOSTS', 'luma.example.com')
    assert _forward(test_app, 'https://luma.example.com') == ['https://luma.example.com/start']
    assert _forward(test_app, 'https://other.example.com') is None


def test_popup_never_sends_forward_url(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    assert _forward(test_app, 'http://192.168.1.62:5002', mode='popup') is None


def test_bogus_mode_is_popup(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    for mode in (['redirect'], {'x': 1}, 'REDIRECT', 7):
        assert _forward(test_app, 'http://10.0.0.5:5002', mode=mode) is None


# -- password claim ----------------------------------------------------------

def test_stale_claim_expires(start_owner):
    from app import start_flow
    c, _ = start_owner
    st = start_flow._load_state()
    st['password_claimed'] = time.time() - start_flow.CLAIM_TTL - 1
    start_flow._save_state(st)
    assert _post(c, 'fresh1').status_code == 200


def test_live_claim_blocks(start_owner):
    from app import start_flow
    c, _ = start_owner
    st = start_flow._load_state()
    st['password_claimed'] = time.time()
    start_flow._save_state(st)
    assert _post(c, 'fresh2').status_code == 409


def test_legacy_true_claim_does_not_block(start_owner):
    from app import start_flow
    c, _ = start_owner
    st = start_flow._load_state()
    st['password_claimed'] = True
    start_flow._save_state(st)
    assert _post(c, 'fresh3').status_code == 200


def test_claim_cleared_at_startup(start_owner):
    from app import start_flow
    st = start_flow._load_state()
    st['password_claimed'] = time.time()
    start_flow._save_state(st)
    start_flow.clear_stale_claim()
    assert 'password_claimed' not in start_flow._load_state()
    assert start_flow._load_state().get('owner_uid')


# -- cropped wordmark --------------------------------------------------------

def test_cropped_wordmark_has_no_transparent_padding():
    from pathlib import Path
    from PIL import Image
    p = Path(__file__).resolve().parents[1] / 'static' / 'images' / 'luma_wordmark.webp'
    im = Image.open(p).convert('RGBA')
    assert im.size == (916, 416)
    a = im.getchannel('A')
    # every edge row/column carries visible pixels
    w, h = im.size
    assert a.crop((0, 0, w, 1)).getextrema()[1] > 8 and a.crop((0, h - 1, w, h)).getextrema()[1] > 8
    assert a.crop((0, 0, 1, h)).getextrema()[1] > 8 and a.crop((w - 1, 0, w, h)).getextrema()[1] > 8


def test_cropped_wordmark_loads_signed_out_in_minimal(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    r = test_app.test_client().get('/static/images/luma_wordmark.webp', environ_base=REMOTE)
    assert r.status_code == 200
