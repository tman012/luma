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
        ok = jsonify({'ok': True, 'url': BAD_URL})       # success bodies are untouched
        assert SECRET in app.main._redact_api_error_bodies(ok).get_data(as_text=True)


def test_redact_helper_covers_headers_and_params():
    red = app.main._redact_secrets(f'{BAD_URL}&api_key=k2 token=t3 X-Plex-Token: p4 Authorization: Bearer b5')
    for s in (SECRET, 'k2', 't3', 'p4', 'b5'):
        assert s not in red
