"""v282: HTTPS Caddy → Docker publish must not CSRF-403 POST /api/push/subscribe."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.user_auth as user_auth

ROOT = Path(__file__).resolve().parents[1]

# Caddy (host network) → localhost:5002 → container sees Docker bridge gateway.
CADDY_PEER = {'REMOTE_ADDR': '192.168.32.1'}
HTTPS_ORIGIN = {
    'Host': '192.168.1.62:5463',
    'Origin': 'https://192.168.1.62:5463',
    'Referer': 'https://192.168.1.62:5463/',
    'X-Forwarded-Proto': 'https',
    'X-Forwarded-For': '192.168.1.50',
    'Sec-Fetch-Site': 'same-origin',
    'Content-Type': 'application/json',
}
SUB = {'endpoint': 'https://web.push.apple.com/v282-test', 'keys': {'p256dh': 'YQ', 'auth': 'YQ'}}


@pytest.fixture
def owner(test_app):
    return user_auth.create_user('owner', '1234', is_admin=True)


def test_default_trusted_proxies_covers_caddy_docker_bridge():
    from app.settings_manager import SETTINGS_SCHEMA, _DEFAULT_TRUSTED_PROXIES
    default = SETTINGS_SCHEMA['security']['trusted_proxies']['default']
    assert default == _DEFAULT_TRUSTED_PROXIES
    for part in ('127.0.0.1/32', '::1/128', '172.16.0.0/12', '192.168.32.0/20'):
        assert part in default


def test_migrate_seeds_empty_trusted_proxies():
    from app.settings_manager import _migrate_seed_trusted_proxies, _DEFAULT_TRUSTED_PROXIES
    data = {'security': {'trusted_proxies': ''}}
    assert _migrate_seed_trusted_proxies(data, existing=True) is True
    assert data['security']['trusted_proxies'] == _DEFAULT_TRUSTED_PROXIES
    assert data['_migrations']['seed_trusted_proxies_v282'] is True
    # Second run is a no-op
    assert _migrate_seed_trusted_proxies(data, existing=True) is False


def test_migrate_does_not_overwrite_existing_trusted_proxies():
    from app.settings_manager import _migrate_seed_trusted_proxies
    data = {'security': {'trusted_proxies': '127.0.0.1/32, ::1/128'}}
    assert _migrate_seed_trusted_proxies(data, existing=True) is True  # marker only
    assert data['security']['trusted_proxies'] == '127.0.0.1/32, ::1/128'


def test_subscribe_https_origin_ok_behind_caddy_peer(test_app, owner):
    """Empty→seeded trusted_proxies: XFP https + Origin https + peer 192.168.32.1 → not 403."""
    import app.main as main
    from app.auth_routes import AUTH_COOKIE
    from app.push_service import remove_subscription, get_subscription_count

    # Simulate LIVE before tip: empty string, then migration seed (as on settings load).
    main.settings.update({'security': {'trusted_proxies': ''}})
    from app.settings_manager import _migrate_seed_trusted_proxies, _DEFAULT_TRUSTED_PROXIES
    raw = {'_migrations': {}, 'security': {'trusted_proxies': ''}}
    _migrate_seed_trusted_proxies(raw, existing=True)
    main.settings.update({'security': {'trusted_proxies': raw['security']['trusted_proxies']}})
    assert main.settings.get('security.trusted_proxies') == _DEFAULT_TRUSTED_PROXIES

    token = user_auth.create_session(owner['id'])
    client = test_app.test_client()
    client.set_cookie(AUTH_COOKIE, token)

    r = client.post('/api/push/subscribe', data=json.dumps(SUB), headers=HTTPS_ORIGIN,
                    environ_base=CADDY_PEER, content_type='application/json')
    body = r.get_json() or {}
    assert r.status_code != 403, body
    assert r.status_code == 200 and body.get('success') is True

    # Cleanup
    remove_subscription(SUB['endpoint'])
    assert get_subscription_count() == 0


def test_empty_trusted_proxies_keeps_http_scheme_behind_caddy_headers(test_app):
    """Control: empty trusted_proxies → X-Forwarded-Proto ignored → scheme stays http."""
    import app.main as main
    from app.auth_routes import is_same_origin

    main.settings.update({'security': {'trusted_proxies': ''}})
    with test_app.test_request_context(
        '/api/push/subscribe', method='POST',
        headers=HTTPS_ORIGIN,
        environ_base=CADDY_PEER,
        data=json.dumps(SUB),
    ):
        from flask import request
        assert request.scheme == 'http'
        assert request.host == '192.168.1.62:5463'
        assert not is_same_origin('https://192.168.1.62:5463')
        assert is_same_origin('http://192.168.1.62:5463')


def test_sw_is_v282_or_above():
    import re
    sw = (ROOT / 'static/sw.js').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 282
