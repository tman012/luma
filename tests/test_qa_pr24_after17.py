"""QA (PR #24 merged into #17's head): release-link grabs and tracker magnets.

Pins Reviewer items (3a) approved user + release link and (3b) a tracker
magneturl carrying &#10; must never reach qBittorrent as two URLs."""

import pytest

import app.user_auth
from tests.test_tracker_sources import (  # noqa: F401  (fixtures)
    env, signed_in, torznab, R, _search, _grab, LAN, ORIGIN)

HASH = 'a' * 40


def _feed(magnet):
    from xml.sax.saxutils import quoteattr
    item = ('<item><title>The Nut Job 2014 1080p WEB-DL</title><guid>https://t.example/details/1</guid>'
            f'<link>{magnet.replace("&", "&amp;").replace(chr(10), "&#10;")}</link><size>1073741824</size>'
            f'<torznab:attr name="magneturl" value={quoteattr(magnet, {chr(10): "&#10;"})}/>'
            '<torznab:attr name="seeders" value="42"/></item>')
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
            'xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><title>x</title>'
            + item + '</channel></rss>')


@pytest.mark.parametrize('sep', ['\n', '\r\n'])
def test_tracker_magnet_with_newline_never_reaches_qbit_as_two_urls(signed_in, env, sep):
    torznab(env['store'])
    magnet = f'magnet:?xt=urn:btih:{HASH}&dn=x{sep}http://169.254.169.254/latest/meta-data/'
    env['mock'].set('t.example', R(body=_feed(magnet)))
    rels = _search(signed_in)
    assert rels, 'the feed must produce a release (code path must run)'
    r = _grab(signed_in, rels[0]['download_url'])
    for add in env['qbit'].added:
        urls = (add['data'] or {}).get('urls') or ''
        assert '\n' not in urls and '\r' not in urls, repr(urls)
    assert r.status_code in (200, 400, 502), r.get_data(as_text=True)


def test_approved_member_grab_of_a_release_link_is_403(test_app, env):
    """Adapted from 'approved member can grab': PM ruled grab admin-only, so
    an approved non-admin gets 403 and nothing is fetched or added."""
    app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    u = app.user_auth.create_user('friend', 'pin-7654321', is_admin=False)
    app.user_auth.save_user_settings(u['id'], {'use_shared_server': True, 'plex_access_status': 'approved'})
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(u['id']))
    torznab(env['store'])
    from tests.test_tracker_sources import rss, torrent_bytes, DL
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', DL))), R(body=torrent_bytes()))
    rels = _search(c)
    assert rels
    r = _grab(c, rels[0]['download_url'])
    assert r.status_code == 403, (r.status_code, r.get_data(as_text=True))
    assert r.get_json()['error'] == 'Admin access required'
    assert env['qbit'].added == []
