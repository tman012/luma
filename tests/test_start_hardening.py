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


@pytest.mark.parametrize('name', ['bob\u200b', 'a\nb', 'b\u043eb', '\u200d', 'x\u202ey'])
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

def _forward(test_app, base_url, env=None, **kw):
    from urllib.parse import parse_qs, urlsplit
    with mock.patch('app.start_flow.requests.post', return_value=_PinResp()):
        r = test_app.test_client().post('/api/start/plex/pin', json={'mode': 'popup'}, headers=ORIGIN,
                                        base_url=base_url, environ_base={**REMOTE, **(env or {})}, **kw)
    assert r.status_code == 200
    return parse_qs(urlsplit(r.json['auth_url']).fragment[1:]).get('forwardUrl')


@pytest.mark.parametrize('host', ['evil.com/x', 'a b', 'x"y', 'host:99999999', '-bad-.com'])
def test_valid_host_rejects_malformed(host):
    from app import start_flow
    assert start_flow.valid_host(host) is None


@pytest.mark.parametrize('host', ['luma.local', 'luma.local:5002', '10.0.0.5:8080', '[::1]:5002', 'localhost'])
def test_valid_host_accepts(host):
    from app import start_flow
    assert start_flow.valid_host(host) == host


@pytest.mark.parametrize('host', ['evil.com/<x>', 'a b', 'x"y'])
def test_forward_url_omitted_for_bad_host(test_app, host):
    from app import start_flow
    with test_app.test_request_context('/api/start/plex/pin', environ_overrides={'HTTP_HOST': host}):
        assert start_flow.forward_url('popup') == ''
        assert 'forwardUrl' not in start_flow.auth_url('abc', start_flow.forward_url('popup'))


def test_forward_url_respects_allowed_hosts(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    monkeypatch.setenv('LUMA_ALLOWED_HOSTS', 'luma.home:5002')
    assert _forward(test_app, 'http://attacker.example') is None
    assert _forward(test_app, 'http://luma.home:5002') == ['http://luma.home:5002/start/plex-done']


def test_bogus_mode_is_popup(test_app, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    for mode in (['redirect'], {'x': 1}, 'REDIRECT', 7):
        with mock.patch('app.start_flow.requests.post', return_value=_PinResp()):
            r = test_app.test_client().post('/api/start/plex/pin', json={'mode': mode}, headers=ORIGIN,
                                            base_url='http://h.local', environ_base=REMOTE)
        q = parse_qs(urlsplit(r.json['auth_url']).fragment[1:])
        assert q['forwardUrl'] == ['http://h.local/start/plex-done']


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
