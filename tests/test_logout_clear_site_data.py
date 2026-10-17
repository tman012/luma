"""Sign-out tells the browser to drop its HTTP cache for the site, so a
signed-in user's cached images don't outlive the session."""

import app.user_auth as ua

LAN = {'REMOTE_ADDR': '192.168.1.100'}


def test_api_logout_clears_site_cache(test_app):
    uid = ua.create_user('csd1', '1234')['id']
    client = test_app.test_client()
    client.set_cookie('luma_auth_session', ua.create_session(uid))
    r = client.post('/api/auth/logout', environ_base=LAN, headers={'Origin': 'http://localhost'})
    assert r.status_code == 200
    assert r.headers['Clear-Site-Data'] == '"cache"'


def test_form_logout_clears_site_cache(test_app):
    uid = ua.create_user('csd2', '1234')['id']
    client = test_app.test_client()
    client.set_cookie('luma_auth_session', ua.create_session(uid))
    r = client.post('/logout', environ_base=LAN, headers={'Origin': 'http://localhost'})
    assert r.status_code == 302
    assert r.headers['Clear-Site-Data'] == '"cache"'


def test_refused_logout_does_not_clear(test_app):
    r = test_app.test_client().post('/logout', environ_base=LAN)
    assert r.status_code == 403
    assert 'Clear-Site-Data' not in r.headers
