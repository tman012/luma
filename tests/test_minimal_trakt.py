"""Minimal build: Trakt in Settings > Connectors and its Home rails, which share
the Plex libraries' list (one order, one visibility)."""

import pytest

import app.main
from app import connectors, home_rails
from tests.test_home_rails import REMOTE, FakePlex, _client

CID = 'trakt0123456789abcdef0123456789abcdef'
HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}
TRAKT_IDS = ['trakt_movies', 'trakt_shows', 'trakt_anticipated_movies', 'trakt_anticipated_shows']


@pytest.fixture
def minimal(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    saved = app.main.settings.get_section('apis')
    fake = FakePlex()
    old = dict(home_rails._cfg)
    home_rails._cfg['get_plex'] = lambda: fake
    home_rails.save_prefs({'order': [], 'hidden': []})
    yield test_app
    home_rails._cfg.update(old)
    try:
        home_rails._prefs_path().unlink()
    except OSError:
        pass
    app.main.settings.update({'apis': saved})
    connectors._tests.clear()


@pytest.fixture
def trakt_ok(monkeypatch):
    """Trakt accepts the client ID; prod's endpoints answer from a fake API."""
    calls = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._body, self.headers = code, body, {}

        def json(self):
            return self._body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise app.main.requests.HTTPError(str(self.status_code))

    def fake_get(url, params=None, headers=None, **kw):
        calls.append((url, (headers or {}).get('trakt-api-key')))
        if 'api.trakt.tv' not in url:
            return R(404, {})
        if (headers or {}).get('trakt-api-key') != CID:
            return R(401, {})
        kind = 'movie' if '/movies/' in url else 'show'
        tag = 'Ant' if 'anticipated' in url else 'Trend'
        base = 100 if kind == 'movie' else 500
        return R(200, [{kind: {'title': f'{tag} {kind} {i}', 'year': 2025, 'ids': {'tmdb': base + i, 'trakt': i}}}
                       for i in range(3)])
    monkeypatch.setattr(connectors.requests, 'get', fake_get)
    monkeypatch.setattr(app.main.requests, 'get', fake_get)
    monkeypatch.setattr(app.main, 'enrich_imdb_ratings', lambda *a, **k: None)
    app.main._trakt_cache.clear()
    return calls


def _connect(c):
    r = c.put('/api/settings/connectors/trakt', json={'client_id': CID}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()
    return r


# ------------------------------------------------------------------ allowlist / gating

def test_trakt_connector_paths_allowlisted():
    from app import minimal
    for p in ('/api/settings/connectors/trakt', '/api/settings/connectors/trakt/test'):
        assert minimal.path_allowed(p, 'PUT') and p not in minimal.SIGNED_OUT_PATHS
    assert not minimal.path_allowed('/api/trakt/movies/trending')


def test_env_or_bootstrapped_trakt_ignored_in_minimal(minimal):
    app.main.settings.update({'apis': {'trakt': {'enabled': True, 'client_id': 'from_env_or_prod_1234',
                                                 'connector_saved': False}}})
    assert app.main.settings.get('apis.trakt.client_id') == ''
    assert app.main.settings.get('apis.trakt.enabled', False) is False
    c = _client(minimal)
    assert c.get('/api/settings/connectors', environ_base=REMOTE).get_json()['trakt'] == {'set': False, 'last4': ''}
    ids = [l['id'] for l in c.get('/api/home/libraries', environ_base=REMOTE).get_json()['libraries']]
    assert not set(ids) & set(TRAKT_IDS)


def test_trakt_outside_minimal_unchanged(test_app, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    saved = app.main.settings.get_section('apis')
    try:
        app.main.settings.update({'apis': {'trakt': {'enabled': True, 'client_id': 'prod_id_123456', 'connector_saved': False}}})
        assert app.main.settings.get('apis.trakt.client_id') == 'prod_id_123456'
    finally:
        app.main.settings.update({'apis': saved})


# ------------------------------------------------------------------ connector

def test_connect_masks_and_never_returns_client_id(minimal, trakt_ok, caplog):
    c = _client(minimal)
    r = _connect(c)
    assert r.get_json()['trakt'] == {'set': True, 'last4': CID[-4:]}
    assert CID not in r.get_data(as_text=True) and r.headers['Cache-Control'] == 'no-store'
    assert CID not in c.get('/api/settings/connectors', environ_base=REMOTE).get_data(as_text=True)
    assert app.main.settings.get('apis.trakt.client_id') == CID
    # Masked value keeps it; Test uses the stored ID server-side.
    c.put('/api/settings/connectors/trakt', json={'client_id': '********'}, environ_base=REMOTE, headers=HDR)
    assert app.main.settings.get('apis.trakt.client_id') == CID
    assert c.post('/api/settings/connectors/trakt/test', json={}, environ_base=REMOTE,
                  headers=HDR).get_json() == {'ok': True, 'message': 'Connected'}
    assert CID not in caplog.text
    # Remove.
    r = c.put('/api/settings/connectors/trakt', json={'client_id': ''}, environ_base=REMOTE, headers=HDR)
    assert r.get_json()['trakt'] == {'set': False, 'last4': ''}
    assert app.main.settings.get('apis.trakt.client_id') == ''


def test_bad_client_id_not_saved(minimal, trakt_ok):
    c = _client(minimal)
    r = c.put('/api/settings/connectors/trakt', json={'client_id': 'wrong_id_00000000'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and r.get_json() == {'error': 'Invalid client ID'}
    assert app.main.settings.get('apis.trakt.client_id') == ''
    r = c.post('/api/settings/connectors/trakt/test', json={'client_id': 'wrong_id_00000000'}, environ_base=REMOTE, headers=HDR)
    assert r.get_json() == {'ok': False, 'error': 'Invalid client ID'}


def test_trakt_connector_owner_only(minimal, trakt_ok):
    _client(minimal)
    for c in (_client(minimal, admin=True, name='second'), _client(minimal, admin=False, name='kid')):
        assert c.put('/api/settings/connectors/trakt', json={'client_id': CID}, environ_base=REMOTE,
                     headers=HDR).status_code == 403
        assert c.post('/api/settings/connectors/trakt/test', json={'client_id': CID}, environ_base=REMOTE,
                      headers=HDR).status_code == 403
    assert minimal.test_client().put('/api/settings/connectors/trakt', json={'client_id': CID},
                                     environ_base=REMOTE).status_code in (401, 302)
    assert trakt_ok == []


def test_save_and_test_have_separate_rate_limits(minimal, trakt_ok):
    c = _client(minimal)
    for _ in range(10):
        c.post('/api/settings/connectors/trakt/test', json={'client_id': CID}, environ_base=REMOTE, headers=HDR)
    assert c.post('/api/settings/connectors/trakt/test', json={'client_id': CID}, environ_base=REMOTE,
                  headers=HDR).get_json()['error'] == 'Rate limited'
    _connect(c)


# ------------------------------------------------------------------ rails: one list, one order

def test_trakt_rows_join_library_list_once_connected(minimal, trakt_ok):
    c = _client(minimal)
    before = c.get('/api/home/libraries', environ_base=REMOTE).get_json()['libraries']
    assert [l['id'] for l in before] == ['1', '2', '4']
    _connect(c)
    libs = c.get('/api/home/libraries', environ_base=REMOTE).get_json()['libraries']
    assert [l['id'] for l in libs] == ['1', '2', '4'] + TRAKT_IDS
    assert [l['title'] for l in libs[3:]] == ['Trending Movies', 'Trending Shows', 'Anticipated Movies', 'Anticipated Shows']
    assert all(l['enabled'] for l in libs) and {l['source'] for l in libs[3:]} == {'trakt'}


def test_combined_order_and_visibility_drive_home(minimal, trakt_ok):
    c = _client(minimal)
    _connect(c)
    rows = [{'id': 'trakt_shows', 'enabled': True}, {'id': '2', 'enabled': True},
            {'id': 'trakt_movies', 'enabled': False}, {'id': '1', 'enabled': True},
            {'id': '4', 'enabled': False}, {'id': 'trakt_anticipated_movies', 'enabled': True},
            {'id': 'trakt_anticipated_shows', 'enabled': True}]
    r = c.put('/api/home/libraries', json={'libraries': rows}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    assert [(l['id'], l['enabled']) for l in r.get_json()['libraries']] == [(x['id'], x['enabled']) for x in rows]
    assert home_rails.load_prefs() == {'order': [x['id'] for x in rows], 'hidden': ['trakt_movies', '4']}
    home = c.get('/api/home', environ_base=REMOTE).get_json()
    assert [x['id'] for x in home['rails']] == ['trakt_shows', '2', '1', 'trakt_anticipated_movies', 'trakt_anticipated_shows']
    shows = home['rails'][0]
    assert shows['title'] == 'Trending Shows'
    card = shows['items'][0]
    assert card == {'key': 'tmdb-show-500', 'type': 'show', 'title': 'Trend show 0', 'subtitle': '2025',
                    'tmdb_id': 500, 'poster': None}
    assert home['rails'][3]['items'][0]['title'] == 'Ant movie 0'


def test_disconnect_hides_trakt_rails_but_keeps_their_place(minimal, trakt_ok):
    c = _client(minimal)
    _connect(c)
    rows = [{'id': 'trakt_movies', 'enabled': True}, {'id': '1', 'enabled': True}, {'id': '2', 'enabled': True},
            {'id': '4', 'enabled': True}, {'id': 'trakt_shows', 'enabled': False},
            {'id': 'trakt_anticipated_movies', 'enabled': True}, {'id': 'trakt_anticipated_shows', 'enabled': True}]
    c.put('/api/home/libraries', json={'libraries': rows}, environ_base=REMOTE, headers=HDR)
    c.put('/api/settings/connectors/trakt', json={'client_id': ''}, environ_base=REMOTE, headers=HDR)
    assert [l['id'] for l in c.get('/api/home/libraries', environ_base=REMOTE).get_json()['libraries']] == ['1', '2', '4']
    assert all(not r['id'].startswith('trakt') for r in c.get('/api/home', environ_base=REMOTE).get_json()['rails'])
    # Saving the Plex-only list while disconnected keeps the Trakt slots.
    c.put('/api/home/libraries', json={'libraries': [{'id': '2', 'enabled': True}, {'id': '1', 'enabled': True},
                                                      {'id': '4', 'enabled': True}]}, environ_base=REMOTE, headers=HDR)
    _connect(c)
    libs = c.get('/api/home/libraries', environ_base=REMOTE).get_json()['libraries']
    assert [l['id'] for l in libs][:3] == ['2', '1', '4']
    assert {l['id']: l['enabled'] for l in libs}['trakt_movies'] is True


def test_trakt_failure_never_breaks_home(minimal, trakt_ok, monkeypatch):
    c = _client(minimal)
    _connect(c)
    monkeypatch.setitem(home_rails._cfg, 'fetch_trakt', lambda rid: (_ for _ in ()).throw(RuntimeError('down')))
    home = c.get('/api/home', environ_base=REMOTE).get_json()
    assert home['plex'] is True
    assert {r['id']: r['items'] for r in home['rails']}['trakt_movies'] == []
    assert {r['id']: len(r['items']) for r in home['rails']}['1'] == 1
