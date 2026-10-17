"""Minimal build: prod's typo-tolerant search and Settings > Connectors (TMDB key)."""

import pytest

import app.main
from app.fast_search import FastSearchIndex
from tests.test_home_rails import REMOTE, _client

KEY = 'abcdef0123456789abcdef0123456789'

ITEMS = [
    {'rating_key': '1', 'title': 'Interstellar', 'year': 2014, 'media_type': 'movie', 'thumb': '/library/metadata/1/thumb/1'},
    {'rating_key': '2', 'title': 'The Godfather', 'year': 1972, 'media_type': 'movie', 'thumb': '/library/metadata/2/thumb/1'},
    {'rating_key': '3', 'title': 'The Godfather Part II', 'year': 1974, 'media_type': 'movie'},
    {'rating_key': '4', 'title': 'Inter Alia', 'year': 2020, 'media_type': 'movie'},
    {'rating_key': '5', 'title': 'Severance', 'year': 2022, 'media_type': 'show'},
]


def _index():
    from app.fast_search import normalize_text
    idx = FastSearchIndex()
    rows = []
    for it in ITEMS:
        r = dict(it, title_lower=it['title'].lower(), title_normalized=normalize_text(it['title']),
                 content_rating='', genres=[], genres_lower=[], section_id=1,
                 section_name='Movies' if it['media_type'] == 'movie' else 'TV Shows', viewCount=0, viewOffset=0)
        rows.append(r)
    idx._index = rows
    idx._library_titles = {r['title_normalized'] for r in rows}
    idx._title_map = {r['title_normalized']: r['title'] for r in rows}
    idx._initialized = True
    return idx


@pytest.fixture
def minimal(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><head></head><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    monkeypatch.setattr(app.main, 'fast_search_index', _index())
    saved = app.main.settings.get_section('apis')
    yield test_app
    app.main.settings.update({'apis': saved})


@pytest.fixture
def no_tmdb_network(monkeypatch):
    calls = []

    class R:
        status_code = 200

        def json(self):
            return {}

    def fake_get(url, **kw):
        calls.append(url)
        return R()
    monkeypatch.setattr('app.connectors.requests.get', fake_get)
    return calls


# ------------------------------------------------------------------ allowlist

@pytest.mark.parametrize('path', ['/search', '/api/library/search', '/settings/connectors',
                                  '/api/settings/connectors', '/api/settings/connectors/tmdb',
                                  '/api/settings/connectors/tmdb/test', '/api/tmdb/person_by_id/1/credits'])
def test_search_and_connectors_allowlisted(path):
    from app import minimal
    assert minimal.path_allowed(path, 'GET')
    assert path not in minimal.SIGNED_OUT_PATHS


@pytest.mark.parametrize('path', ['/api/search/natural', '/api/library/search/fast', '/api/indexer/search-all'])
def test_other_search_routes_stay_off(path):
    from app import minimal
    assert not minimal.path_allowed(path, 'GET')


def test_search_requires_sign_in(minimal):
    c = minimal.test_client()
    for p in ('/search?q=dune', '/api/library/search?q=dune'):
        assert c.get(p, environ_base=REMOTE).status_code in (401, 302)


# ------------------------------------------------------------------ fuzzy

@pytest.mark.parametrize('typo,want', [('Interstelar', 'Interstellar'), ('Godfahter', 'The Godfather'),
                                       ('interstellar', 'Interstellar')])
def test_library_search_typo_tolerant_exact_first(minimal, typo, want):
    c = _client(minimal)
    r = c.get(f'/api/library/search?q={typo}', environ_base=REMOTE)
    assert r.status_code == 200
    movies = r.get_json()['movies']
    assert movies and movies[0]['title'] == want
    assert movies[0]['poster_url'] is None or movies[0]['poster_url'].startswith('/api/img/plex?url=')
    body = r.get_data(as_text=True).lower()
    assert 'did you mean' not in body


def test_exact_title_ranks_first(minimal):
    c = _client(minimal)
    movies = c.get('/api/library/search?q=The Godfather', environ_base=REMOTE).get_json()['movies']
    assert movies[0]['title'] == 'The Godfather'


def test_tv_results_grouped(minimal):
    c = _client(minimal)
    data = c.get('/api/library/search?q=Severence', environ_base=REMOTE).get_json()
    assert [s['title'] for s in data['tv_shows']] == ['Severance']


# ------------------------------------------------------------------ TMDB off without a key

def test_no_key_means_tmdb_off(minimal, monkeypatch):
    monkeypatch.setenv('TMDB_API_KEY', 'env_key_must_be_ignored_123456')
    app.main.settings.update({'apis': {'tmdb': {'api_key': 'bootstrapped_from_env_000000', 'connector_saved': False}}})
    assert app.main.settings.get('apis.tmdb.api_key') == ''
    sent = []
    monkeypatch.setattr(app.main.requests, 'get', lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(AssertionError('network')))
    monkeypatch.setattr(app.main.tmdb, 'api_key', None)
    c = _client(minimal)
    r = c.get('/search?q=Interstelar', environ_base=REMOTE)
    assert r.status_code == 200
    d = r.get_json()
    assert d['movies'] == [] and d['tv_shows'] == []
    assert sent == []


def test_key_outside_minimal_unchanged(test_app, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    saved = app.main.settings.get_section('apis')
    try:
        app.main.settings.update({'apis': {'tmdb': {'api_key': 'prodkey_1234567890', 'connector_saved': False}}})
        assert app.main.settings.get('apis.tmdb.api_key') == 'prodkey_1234567890'
    finally:
        app.main.settings.update({'apis': saved})


# ------------------------------------------------------------------ Connectors

def test_connectors_fresh_install_has_no_key(minimal):
    app.main.settings.update({'apis': {'tmdb': {'api_key': '', 'connector_saved': False}}})
    c = _client(minimal)
    r = c.get('/api/settings/connectors', environ_base=REMOTE)
    assert r.status_code == 200 and r.get_json()['tmdb'] == {'set': False, 'last4': ''}
    assert r.headers['Cache-Control'] == 'no-store'


def test_save_masks_key_and_enables_tmdb(minimal, no_tmdb_network, monkeypatch):
    c = _client(minimal)
    hdr = {'Origin': 'http://localhost', 'Host': 'localhost'}
    r = c.put('/api/settings/connectors/tmdb', json={'api_key': KEY}, environ_base=REMOTE, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['tmdb'] == {'set': True, 'last4': KEY[-4:]}
    assert KEY not in r.get_data(as_text=True)
    g = c.get('/api/settings/connectors', environ_base=REMOTE)
    assert KEY not in g.get_data(as_text=True)
    assert app.main.settings.get('apis.tmdb.api_key') == KEY
    assert app.main.tmdb.api_key == KEY
    # Masked value back keeps the key; Test uses the stored key server-side.
    r = c.put('/api/settings/connectors/tmdb', json={'api_key': '********'}, environ_base=REMOTE, headers=hdr)
    assert app.main.settings.get('apis.tmdb.api_key') == KEY
    r = c.post('/api/settings/connectors/tmdb/test', json={}, environ_base=REMOTE, headers=hdr)
    assert r.get_json() == {'ok': True, 'message': 'Connected'}
    # Remove.
    r = c.put('/api/settings/connectors/tmdb', json={'api_key': ''}, environ_base=REMOTE, headers=hdr)
    assert r.get_json()['tmdb'] == {'set': False, 'last4': ''}
    assert app.main.settings.get('apis.tmdb.api_key') == ''
    assert not app.main.tmdb.api_key


def test_rejected_key_not_saved(minimal, monkeypatch):
    class R:
        status_code = 401
    monkeypatch.setattr('app.connectors.requests.get', lambda *a, **k: R())
    app.main.settings.update({'apis': {'tmdb': {'api_key': '', 'connector_saved': False}}})
    c = _client(minimal)
    hdr = {'Origin': 'http://localhost', 'Host': 'localhost'}
    r = c.put('/api/settings/connectors/tmdb', json={'api_key': 'bad_key_000000000'}, environ_base=REMOTE, headers=hdr)
    assert r.status_code == 400 and r.get_json() == {'error': 'Invalid key'}
    assert app.main.settings.get('apis.tmdb.api_key') == ''
    r = c.post('/api/settings/connectors/tmdb/test', json={'api_key': 'bad_key_000000000'}, environ_base=REMOTE, headers=hdr)
    assert r.get_json() == {'ok': False, 'error': 'Invalid key'}


def test_connectors_owner_only(minimal, no_tmdb_network):
    _client(minimal)                       # owner (first admin)
    other_admin = _client(minimal, admin=True, name='second')
    user = _client(minimal, admin=False, name='kid')
    hdr = {'Origin': 'http://localhost', 'Host': 'localhost'}
    for c in (other_admin, user):
        assert c.get('/api/settings/connectors', environ_base=REMOTE).status_code == 403
        assert c.put('/api/settings/connectors/tmdb', json={'api_key': KEY}, environ_base=REMOTE,
                     headers=hdr).status_code == 403
    anon = minimal.test_client()
    assert anon.get('/api/settings/connectors', environ_base=REMOTE).status_code in (401, 302)
    assert no_tmdb_network == []


def test_key_never_logged(minimal, no_tmdb_network, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    c = _client(minimal)
    hdr = {'Origin': 'http://localhost', 'Host': 'localhost'}
    c.put('/api/settings/connectors/tmdb', json={'api_key': KEY}, environ_base=REMOTE, headers=hdr)
    c.post('/api/settings/connectors/tmdb/test', json={'api_key': KEY}, environ_base=REMOTE, headers=hdr)
    assert KEY not in caplog.text


# ------------------------------------------------------------------ follow-ups

@pytest.mark.parametrize('path', ['/settings/connectors', '/settings/plex', '/settings/home-screen'])
def test_settings_deep_links_reload(minimal, path):
    c = _client(minimal)
    r = c.get(path, environ_base=REMOTE)
    assert r.status_code == 200 and b'id="root"' in r.data


def test_tmdb_errors_never_log_the_key(caplog, monkeypatch):
    import logging
    import requests as rq
    from app.main import TMDBClient, _redact_secrets

    class R:
        status_code = 401
        url = f'https://api.themoviedb.org/3/search/movie?query=x&api_key={KEY}'

        def raise_for_status(self):
            raise rq.HTTPError(f'401 Client Error: Unauthorized for url: {self.url}')

        def json(self):
            return {}
    monkeypatch.setattr(app.main.requests, 'get', lambda *a, **k: R())
    monkeypatch.setattr(app.main.fast_search_index, '_is_tmdb_rate_limited', lambda: False)
    caplog.set_level(logging.DEBUG)
    t = TMDBClient(KEY)
    for fn in (t.search_movies, t.search_tv, t.search_multi):
        fn('x')
    t.search_people('x')
    assert 'TMDB' in caplog.text and KEY not in caplog.text
    assert _redact_secrets(f'u?a=1&api_key={KEY}&b=2') == 'u?a=1&api_key=***&b=2'


def test_root_log_filter_masks_keys(capsys):
    import logging
    from app.main import _RedactSecretsFilter
    rec = logging.LogRecord('x', logging.ERROR, __file__, 1, 'GET /3/movie/1?api_key=%s', (KEY,), None)
    assert _RedactSecretsFilter().filter(rec) and KEY not in rec.getMessage()


@pytest.mark.parametrize('path', ['/settings/anything-new', '/settings/plex?pinID=1', '/settings/connectors/'])
def test_any_settings_section_reloads(minimal, path):
    c = _client(minimal)
    r = c.get(path, environ_base=REMOTE)
    assert r.status_code == 200 and b'id="root"' in r.data


def test_plex_change_server_same_tab_return_lands_in_settings(minimal, monkeypatch):
    """Change server in same-tab mode (PWA or pop-up blocked): Plex sends the
    browser back to /settings/plex on this host, which must load the app."""
    from urllib.parse import parse_qs, urlsplit, unquote
    from app import start_flow
    monkeypatch.setattr(start_flow, 'create_pin', lambda: {'id': 42, 'code': 'abcd'})
    monkeypatch.setattr(start_flow, 'valid_host', lambda h: 'localhost')
    c = _client(minimal)
    r = c.post('/api/settings/plex/pin', json={'mode': 'redirect'}, environ_base=REMOTE,
               headers={'Origin': 'http://localhost', 'Host': 'localhost'})
    assert r.status_code == 200
    auth = r.get_json()['auth_url']
    fwd = unquote(auth.split('forwardUrl=', 1)[1].split('&', 1)[0])
    assert fwd == 'http://localhost/settings/plex'
    back = c.get(urlsplit(fwd).path, environ_base=REMOTE)
    assert back.status_code == 200 and b'id="root"' in back.data


def test_library_search_one_letter_typo_without_tmdb_key():
    from app.fast_search import FastSearchIndex, normalize_text, _one_edit
    idx = FastSearchIndex()
    titles = ['The Nut Job', 'The Nut Job 2: Nutty by Nature', 'Nightcrawler', 'The Night Of', 'Not Okay']
    idx._index = [{'rating_key': str(i), 'title': t, 'year': 2014, 'media_type': 'movie', 'title_lower': t.lower(),
                   'title_normalized': normalize_text(t), 'content_rating': '', 'genres': [], 'genres_lower': [],
                   'section_id': 1, 'section_name': 'Movies', 'viewCount': 0, 'viewOffset': 0} for i, t in enumerate(titles)]
    idx._initialized = True
    for q in ('the nit job', 'The Nit Job', 'nit job', 'the nut jab'):
        assert [r['title'] for r in idx.search(q)][:1] == ['The Nut Job'], q
    assert _one_edit('nit', 'nut') and _one_edit('job', 'jobs') and not _one_edit('cat', 'bat')
    assert not _one_edit('it', 'at')


def test_api_library_search_the_nit_job(minimal, monkeypatch):
    from app.fast_search import FastSearchIndex, normalize_text
    idx = FastSearchIndex()
    t = 'The Nut Job'
    idx._index = [{'rating_key': '7', 'title': t, 'year': 2014, 'media_type': 'movie', 'title_lower': t.lower(),
                   'title_normalized': normalize_text(t), 'content_rating': '', 'genres': [], 'genres_lower': [],
                   'section_id': 1, 'section_name': 'Movies', 'viewCount': 0, 'viewOffset': 0,
                   'thumb': '/library/metadata/7/thumb/1'}]
    idx._initialized = True
    monkeypatch.setattr(app.main, 'fast_search_index', idx)
    app.main.settings.update({'apis': {'tmdb': {'api_key': '', 'connector_saved': False}}})
    c = _client(minimal)
    movies = c.get('/api/library/search?q=the nit job', environ_base=REMOTE).get_json()['movies']
    assert movies[0]['title'] == 'The Nut Job'
    assert movies[0]['poster_url'] == '/api/img/plex?url=/library/metadata/7/thumb/1'
