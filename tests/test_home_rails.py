"""#11 Home: Continue Watching, recently-added rails per selected library,
the saved Libraries choice, and minimal-mode access."""

import xml.etree.ElementTree as ET
from unittest import mock

import pytest

REMOTE = {'REMOTE_ADDR': '203.0.113.9'}

SECTIONS = """<MediaContainer>
<Directory key="1" type="movie" title="Movies"/>
<Directory key="2" type="show" title="TV Shows"/>
<Directory key="3" type="artist" title="Music"/>
<Directory key="4" type="movie" title="Kids"/>
</MediaContainer>"""
ON_DECK = """<MediaContainer>
<Video ratingKey="10" type="episode" title="Pilot" grandparentTitle="Severance" parentIndex="1" index="2"
 grandparentThumb="/library/metadata/9/thumb/1" art="/library/metadata/9/art/1" duration="1000" viewOffset="250"/>
<Video ratingKey="11" type="movie" title="Dune" year="2021" thumb="/library/metadata/11/thumb/2" duration="0"/>
</MediaContainer>"""
RECENT = {
    '1': '<MediaContainer><Video ratingKey="21" type="movie" title="Heat" year="1995" thumb="/library/metadata/21/thumb/3"/></MediaContainer>',
    '2': '<MediaContainer><Directory ratingKey="31" type="season" title="Season 2" parentTitle="Andor" parentThumb="/library/metadata/30/thumb/4"/></MediaContainer>',
    '4': '<MediaContainer><Video ratingKey="41" type="movie" title="Up" thumb="http://evil/x.jpg"/></MediaContainer>',
}


class FakePlex:
    is_configured = True

    def __init__(self):
        self.calls = []

    def _get(self, path, params=None, timeout=15):
        self.calls.append(path)
        if path == '/library/sections':
            return ET.fromstring(SECTIONS)
        if path == '/library/onDeck':
            return ET.fromstring(ON_DECK)
        sid = path.split('/')[3]
        return ET.fromstring(RECENT.get(sid, '<MediaContainer/>'))


@pytest.fixture
def plex(test_app):
    from app import home_rails
    fake = FakePlex()
    old = dict(home_rails._cfg)
    home_rails._cfg['get_plex'] = lambda: fake
    try:
        home_rails.save_prefs({'order': [], 'hidden': []})
    except OSError:
        pass
    yield fake
    home_rails._cfg.update(old)
    try:
        home_rails._prefs_path().unlink()
    except OSError:
        pass


def _client(test_app, admin=True, name='owner'):
    import secrets
    from app import user_auth
    u = user_auth.create_user(f'{name}{secrets.token_hex(3)}', secrets.token_urlsafe(12), is_admin=admin)
    c = test_app.test_client()
    c.set_cookie('luma_auth_session', user_auth.create_session(u['id']))
    return c


def test_arrange_defaults_all_on_in_plex_order():
    from app import home_rails
    secs = [{'id': '1', 'title': 'Movies', 'type': 'movie'}, {'id': '2', 'title': 'TV', 'type': 'show'},
            {'id': '3', 'title': 'Music', 'type': 'artist'}]
    out = home_rails.arrange(secs, {'order': [], 'hidden': []})
    assert [(l['id'], l['enabled']) for l in out] == [('1', True), ('2', True)]


def test_arrange_saved_order_hidden_and_new_last():
    from app import home_rails
    secs = [{'id': str(i), 'title': str(i), 'type': 'movie'} for i in (1, 2, 4, 5)]
    out = home_rails.arrange(secs, {'order': ['4', '1', 'gone'], 'hidden': ['1']})
    assert [l['id'] for l in out] == ['4', '1', '2', '5']
    assert [l['enabled'] for l in out] == [True, False, True, True]


def test_home_requires_sign_in(test_app, plex):
    r = test_app.test_client().get('/api/home', environ_base=REMOTE)
    assert r.status_code in (401, 302)
    assert '/library/onDeck' not in plex.calls


def test_home_payload(test_app, plex):
    c = _client(test_app)
    r = c.get('/api/home', environ_base=REMOTE)
    assert r.status_code == 200
    body = r.json
    assert body['plex'] is True
    cw = body['continue_watching']
    assert cw[0] == {'key': '10', 'type': 'episode', 'title': 'Severance', 'subtitle': 'S1 E2',
                     'poster': '/api/img/plex?url=%2Flibrary%2Fmetadata%2F9%2Fthumb%2F1&w=300',
                     'art': '/api/img/plex?url=%2Flibrary%2Fmetadata%2F9%2Fart%2F1&w=480', 'progress': 0.25}
    assert cw[1]['progress'] == 0 and cw[1]['subtitle'] == '2021'
    assert [x['title'] for x in body['rails']] == ['Movies', 'TV Shows', 'Kids']
    assert body['rails'][1]['items'][0]['title'] == 'Andor'
    assert body['rails'][2]['items'][0]['poster'] is None          # never proxies foreign URLs
    assert 'token' not in r.get_data(as_text=True).lower()
    assert r.headers['Cache-Control'] == 'no-store'


def test_libraries_saved_server_side_and_applied(test_app, plex):
    c = _client(test_app)
    r = c.get('/api/home/libraries', environ_base=REMOTE)
    assert [l['id'] for l in r.json['libraries']] == ['1', '2', '4']
    assert all(l['enabled'] for l in r.json['libraries'])
    r = c.put('/api/home/libraries', json={'libraries': [
        {'id': '4', 'enabled': True}, {'id': '1', 'enabled': False}, {'id': '2', 'enabled': True},
        {'id': '999', 'enabled': True}]}, environ_base=REMOTE, headers={'Origin': 'http://localhost'})
    assert r.status_code == 200, r.json
    assert [(l['id'], l['enabled']) for l in r.json['libraries']] == [('4', True), ('1', False), ('2', True)]
    from app import home_rails
    assert home_rails.load_prefs() == {'order': ['4', '1', '2'], 'hidden': ['1']}
    body = c.get('/api/home', environ_base=REMOTE).json
    assert [x['title'] for x in body['rails']] == ['Kids', 'TV Shows']
    assert '/library/sections/1/recentlyAdded' not in plex.calls


@pytest.mark.parametrize('body', [{}, {'libraries': 'x'}, {'libraries': [{'id': '1'}]},
                                  {'libraries': [{'id': None, 'enabled': True}]},
                                  {'libraries': [{'id': '1', 'enabled': 'yes'}]}])
def test_libraries_rejects_bad_body(test_app, plex, body):
    c = _client(test_app)
    r = c.put('/api/home/libraries', json=body, environ_base=REMOTE, headers={'Origin': 'http://localhost'})
    assert r.status_code == 400


def test_libraries_put_admin_only(test_app, plex):
    c = _client(test_app, admin=False, name='guest')
    r = c.put('/api/home/libraries', json={'libraries': []}, environ_base=REMOTE,
              headers={'Origin': 'http://localhost'})
    assert r.status_code == 403


def test_home_without_plex(test_app):
    from app import home_rails
    old = dict(home_rails._cfg)
    home_rails._cfg['get_plex'] = lambda: None
    try:
        r = _client(test_app).get('/api/home', environ_base=REMOTE)
        assert r.json == {'continue_watching': [], 'rails': [], 'plex': False}
    finally:
        home_rails._cfg.update(old)


def test_minimal_mode_allows_home_with_auth(test_app, plex, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    from app import minimal
    for p in ('/api/home', '/api/home/libraries', '/api/img/plex', '/settings/libraries'):
        assert minimal.path_allowed(p)
        assert p not in minimal.SIGNED_OUT_PATHS
    assert test_app.test_client().get('/api/home', environ_base=REMOTE).status_code in (401, 302)
    assert _client(test_app).get('/api/home', environ_base=REMOTE).status_code == 200
    assert _client(test_app).get('/settings/libraries', environ_base=REMOTE).status_code == 200
