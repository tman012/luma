"""Hotfix errtext: an API error reply never carries exception text or a secret
(OMDb apikey, tokens). The detail goes to the server log, redacted."""
import logging

import pytest
import requests as _rq

import app.main
from app import omdb

SECRET = 'SECRETKEY123'
BAD_URL = f'http://www.omdbapi.com/?apikey={SECRET}&t=Dune'


def _assert_clean(resp, *, status):
    assert resp.status_code == status
    body = resp.get_data(as_text=True)
    for bad in (SECRET, 'apikey', 'omdbapi.com', 'ConnectionError', 'Max retries', 'HTTPConnectionPool'):
        assert bad not in body, (bad, body)
    return resp.get_json()


@pytest.fixture
def client(test_app):
    return test_app.test_client()


def test_ratings_request_failure_hides_key_and_exception(client, monkeypatch, caplog):
    monkeypatch.setattr(omdb, 'api_key', lambda: SECRET)
    app.main._omdb_ratings_cache.clear()

    def boom(url, **kw):
        raise _rq.exceptions.ConnectionError(f"HTTPConnectionPool(host='www.omdbapi.com'): Max retries exceeded with url: {url}")
    monkeypatch.setattr(app.main.requests, 'get', boom)
    with caplog.at_level(logging.WARNING):
        r = client.get('/api/ratings/Dune')
    data = _assert_clean(r, status=500)
    assert data == {'error': 'Something went wrong'}
    logged = '\n'.join(rec.getMessage() for rec in caplog.records)
    assert 'ConnectionError' in logged and SECRET not in app.main._redact_secrets(logged)


def test_ratings_timeout_hides_key(client, monkeypatch):
    monkeypatch.setattr(omdb, 'api_key', lambda: SECRET)
    app.main._omdb_ratings_cache.clear()

    def boom(url, **kw):
        raise _rq.exceptions.ReadTimeout(f'Read timed out for {url}')
    monkeypatch.setattr(app.main.requests, 'get', boom)
    _assert_clean(client.get('/api/ratings/Dune?year=2021'), status=500)


class _BoomRadarr:
    def get_quality_profiles(self):
        raise _rq.exceptions.HTTPError(f'500 Server Error for url: http://radarr:7878/api/v3/qualityprofile?apikey={SECRET}')

    get_root_folders = get_quality_profiles


@pytest.mark.parametrize('path', ['/api/radarr/quality-profiles', '/api/radarr/root-folders'])
def test_radarr_routes_hide_exception(client, monkeypatch, path):
    monkeypatch.setattr(app.main, 'get_radarr_for_request', lambda *a, **k: _BoomRadarr())
    data = _assert_clean(client.get(path), status=500)
    assert data == {'error': 'Something went wrong'}


def test_no_route_returns_raw_exception_text():
    """Static audit: no jsonify/return in main.py hands str(e) or f'{e}' to a client,
    apart from app-authored validation messages (user_auth ValueError, MatchError)."""
    import re
    src = open(app.main.__file__).read().splitlines()
    allowed = {"return jsonify({'error': str(e)}), 409", "return jsonify({'error': str(e)}), 400",
               "return jsonify({'error': str(exc)}), exc.status"}
    bad = [ln.strip() for ln in src
           if ln.strip().startswith('return') and re.search(r"str\((e|exc|err)\)|\{(e|exc|err)\}|err_msg\}", ln)
           and ln.strip() not in allowed]
    assert bad == []


def test_backstop_redacts_secrets_in_error_bodies(test_app):
    from flask import jsonify
    detail = f'GET {BAD_URL} X-Plex-Token=plextok99 Authorization: Bearer abc.def'
    for status, payload in ((200, {'ok': False, 'detail': detail}), (502, {'error': detail})):
        with test_app.test_request_context('/api/anything'):
            resp = jsonify(payload)
            resp.status_code = status
            body = app.main._redact_api_error_bodies(resp).get_data(as_text=True)
        assert SECRET not in body and 'plextok99' not in body and 'abc.def' not in body
    with test_app.test_request_context('/api/anything'):
        ok = jsonify({'ok': True, 'url': BAD_URL})       # r2b: every /api JSON reply is redacted, 200s too
        assert SECRET not in app.main._redact_api_error_bodies(ok).get_data(as_text=True)


def test_redact_helper_covers_headers_and_params():
    red = app.main._redact_secrets(f'{BAD_URL}&api_key=k2 token=t3 X-Plex-Token: p4 Authorization: Bearer b5')
    for s in (SECRET, 'k2', 't3', 'p4', 'b5'):
        assert s not in red


# ── r2 (Tester B1 + Reviewer F1-F4) ─────────────────────────────────
def test_taste_film_unreachable_tmdb_returns_no_key(client, monkeypatch):
    """Tester B1 repro: TMDb key set, TMDb unreachable, GET /api/taste/film/1 (a 200)."""
    from app import taste
    monkeypatch.setattr(taste, '_tmdb_key', lambda settings=None: SECRET)

    def boom(url, params=None, **kw):
        raise _rq.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='api.themoviedb.org', port=443): Max retries exceeded with url: "
            f"/3/movie/1?api_key={params['api_key']}")
    monkeypatch.setattr(_rq, 'get', boom)
    r = client.get('/api/taste/film/1')
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    for bad in (SECRET, 'api_key', 'HTTPSConnectionPool', 'Max retries'):
        assert bad not in body, (bad, body)
    assert r.get_json()['film']['error'] == 'Lookup failed'


def test_backstop_redacts_nested_error_on_200(test_app):
    from flask import jsonify
    with test_app.test_request_context('/api/anything'):
        resp = jsonify({'film': {'error': f'boom {BAD_URL}'}, 'items': [{'message': f'token={SECRET}'}]})
        body = app.main._redact_api_error_bodies(resp).get_data(as_text=True)
    assert resp.status_code == 200 and SECRET not in body


def test_backstop_keeps_json_valid_with_escaped_quote(test_app):
    import json
    from flask import jsonify
    with test_app.test_request_context('/api/anything'):
        resp = jsonify({'error': 'x apikey=SEK" y', 'h': 'Authorization: Bearer SEK2"'}), 502
        out = app.main._redact_api_error_bodies(test_app.make_response(resp))
        data = json.loads(out.get_data(as_text=True))         # must still parse
    assert 'SEK' not in json.dumps(data)


def test_radarr_lookup_error_is_fixed_text():
    src = open(app.main.__file__).read()
    assert "{_lookup_err}" not in src


def test_test_connection_catch_all_specific_messages(test_app):
    with test_app.test_request_context('/api/settings/test/radarr'):
        assert app.main._public_error(_rq.exceptions.SSLError(BAD_URL)) == 'SSL certificate problem'
        assert app.main._public_error(_rq.exceptions.InvalidURL(BAD_URL)) == 'Invalid URL'
        assert app.main._public_error(_rq.exceptions.MissingSchema(BAD_URL)) == 'Invalid URL'
        assert app.main._public_error(RuntimeError(BAD_URL)) == 'Something went wrong'
    with test_app.test_request_context('/api/ratings/x'):
        assert app.main._public_error(_rq.exceptions.SSLError(BAD_URL)) == 'Something went wrong'


def test_tmdb_test_connection_invalid_url(client, monkeypatch):
    def boom(*a, **k): raise _rq.exceptions.InvalidURL(f'bad {BAD_URL}')
    monkeypatch.setattr(app.main.requests, 'get', boom)
    r = client.post('/api/settings/test/tmdb', json={'api_key': SECRET})
    body = r.get_data(as_text=True)
    if r.status_code == 200:
        assert r.get_json()['message'] == 'Invalid URL'
    assert SECRET not in body


def test_static_no_jsonify_str_e():
    """Reviewer F4: no jsonify(...) anywhere in app/ carries str(e)/f'{e}', outside the
    app-authored validation replies."""
    import re
    from pathlib import Path
    root = Path(app.main.__file__).parent
    allowed = {"return jsonify({'error': str(e)}), 409", "return jsonify({'error': str(e)}), 400",
               "return jsonify({'error': str(exc)}), exc.status",
               "return _no_store(jsonify({'error': str(e)}), 502)",   # TmdbError: fixed constants
               "isinstance(e, daily_missing.PoolError)"}
    pat = re.compile(r"jsonify\(.*(str\((e|exc|err)\)|\{(e|exc|err|_\w*err)(!r)?\}|repr\((e|exc)\))")
    bad = []
    for p in root.rglob('*.py'):
        for n, ln in enumerate(p.read_text().splitlines(), 1):
            if pat.search(ln) and not any(a in ln for a in allowed):
                bad.append(f'{p.name}:{n}: {ln.strip()}')
    assert bad == [], '\n'.join(bad)


def test_200_path_helpers_return_fixed_text(monkeypatch):
    from app import voice_service

    class Plex:
        def hub_search(self, *a, **k): raise RuntimeError(BAD_URL)
    assert voice_service._execute_tool('search_library', {'query': 'x'}, Plex(), None, None) == {'error': 'Tool failed'}
    import inspect
    from app import franchise_completer, genre_roulette, movie_night, latest_auto_grab, \
        now_available_watchlist, anticipated_watchlist
    for mod in (franchise_completer, genre_roulette, movie_night, latest_auto_grab,
                now_available_watchlist, anticipated_watchlist, voice_service):
        src = inspect.getsource(mod)
        assert "Run failed: {e}" not in src and "'detail': str(e)" not in src and "'error': str(e)" not in src, mod
        assert 'fetch failed: {e}' not in src and 'unavailable ({e})' not in src, mod


def test_backstop_redacts_every_200_and_keeps_others_byte_identical(test_app):
    import json
    from flask import jsonify
    with test_app.test_request_context('/api/anything'):
        resp = jsonify({'poster': f'http://x/p.jpg?api_key={SECRET}', 'title': 'Dune',
                        'h': f'X-Plex-Token: {SECRET}\\"'})
        out = app.main._redact_api_error_bodies(resp)
        data = json.loads(out.get_data(as_text=True))
    assert out.status_code == 200 and SECRET not in json.dumps(data) and data['title'] == 'Dune'
    with test_app.test_request_context('/api/anything'):
        plain = jsonify({'ok': True, 'title': 'Dune'})
        before = plain.get_data()
        assert app.main._redact_api_error_bodies(plain).get_data() == before


def test_omdb_votes_log_is_redacted(monkeypatch, caplog):
    import logging
    app.main.settings.update({'apis': {'omdb': {'api_key': SECRET}}})

    def boom(url, **kw): raise _rq.exceptions.ConnectionError(f'Max retries exceeded with url: {url}')
    monkeypatch.setattr(app.main.requests, 'get', boom)
    with caplog.at_level(logging.DEBUG):
        try:
            app.main.fetch_imdb_votes_batch([{'title': 'Dune', 'year': 2021}])
        except Exception:
            pass
    src = open(app.main.__file__).read()
    assert 'OMDB votes for {title}: {_redact_secrets(e)}' in src
    assert all(SECRET not in r.msg if isinstance(r.msg, str) else True for r in caplog.records)
