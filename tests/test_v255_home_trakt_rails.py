"""v255: Home's Trakt rails (Anticipated) on test. Every Trakt call sends Trakt's required headers (with an
identifying User-Agent) through app/trakt_http; a non-200 or an exception is logged (never silent, never the
key); a failed or still-loading Trakt rail keeps its last good cards; rail fetches don't queue behind the
Plex rails. Trakt, TMDB and OMDb are mocked."""
import logging
import threading
import time

import pytest

import app.main
from app import connectors, home_rails, trakt_http
from tests.test_minimal_trakt import CID, HDR, REMOTE, _client, _connect, minimal, _key_tests_via_requests_get  # noqa: F401

ANT = ('trakt_anticipated_movies', 'trakt_anticipated_shows')


class R:
    def __init__(self, code, body=None):
        self.status_code, self._body, self.headers = code, body if body is not None else [], {}
        self.ok = code < 400

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise app.main.requests.HTTPError(f'{self.status_code} Client Error')


@pytest.fixture
def trakt(monkeypatch):
    """A fake Trakt: records each call's headers; answers .status (default 200) for the rail endpoints."""
    st = {'status': 200, 'calls': [], 'raise': None}

    def fake_get(url, params=None, headers=None, **kw):
        st['calls'].append((url, dict(headers or {})))
        if 'api.trakt.tv' not in url:
            return R(404, {})
        if (headers or {}).get('trakt-api-key') != CID:
            return R(401, {})
        if '/shows/trending' in url and (params or {}).get('limit') == 1:
            return R(200, [])                                   # the Connectors key check
        if st['raise']:
            raise st['raise']
        if st['status'] != 200:
            return R(st['status'], {})
        kind = 'movie' if '/movies/' in url else 'show'
        base = 100 if kind == 'movie' else 500
        return R(200, [{kind: {'title': f'Ant {kind} {i}', 'year': 2027, 'ids': {'tmdb': base + i, 'trakt': i}}}
                       for i in range(3)])
    monkeypatch.setattr(app.main.requests, 'get', fake_get)
    monkeypatch.setattr(app.main, 'enrich_imdb_ratings', lambda *a, **k: None)
    app.main._trakt_cache.clear()
    return st


def _home(c):
    return {r['id']: r['items'] for r in c.get('/api/home', environ_base=REMOTE).get_json()['rails']}


def test_headers_include_an_identifying_user_agent():
    h = trakt_http.headers('abc')
    assert h['trakt-api-version'] == '2' and h['trakt-api-key'] == 'abc' and h['Content-Type'] == 'application/json'
    assert h['User-Agent'].startswith('Luma/') and 'python-requests' not in h['User-Agent']


def test_anticipated_rails_fill_and_every_trakt_call_sends_the_headers(minimal, trakt):
    c = _client(minimal)
    _connect(c)
    rails = _home(c)
    for rid in ANT:
        assert [x['title'] for x in rails[rid]] == [f"Ant {'movie' if 'movies' in rid else 'show'} {i}" for i in range(3)]
    sent = [h for u, h in trakt['calls'] if 'api.trakt.tv' in u]
    assert sent and all(h.get('User-Agent') == trakt_http.USER_AGENT and h.get('trakt-api-version') == '2' for h in sent)


@pytest.mark.parametrize('code', [403, 401, 500, 502, 429])
def test_a_non_200_is_logged_never_silent_and_never_the_key(minimal, trakt, caplog, code):
    c = _client(minimal)
    _connect(c)
    trakt['status'] = code
    caplog.set_level(logging.WARNING)
    rails = _home(c)
    assert all(rails[rid] == [] for rid in ANT)                    # nothing good yet: empty, Home still answers
    text = caplog.text
    assert f'Trakt /movies/anticipated: HTTP {code}' in text and f'Trakt /shows/anticipated: HTTP {code}' in text
    assert 'Home: Trakt rail trakt_anticipated_movies got HTTP' in text
    assert CID not in text


def test_an_exception_is_logged_and_never_the_key(minimal, trakt, caplog):
    c = _client(minimal)
    _connect(c)
    trakt['raise'] = app.main.requests.exceptions.ConnectionError('boom')
    caplog.set_level(logging.WARNING)
    rails = _home(c)
    assert all(rails[rid] == [] for rid in ANT)
    assert 'Trakt /movies/anticipated failed: ConnectionError' in caplog.text and CID not in caplog.text


def test_trakt_http_get_logs_status_and_reraises(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(trakt_http.requests, 'get', lambda url, **kw: R(404))
    assert trakt_http.get('/movies/anticipated', 'secret-client-id').status_code == 404
    assert 'Trakt /movies/anticipated: HTTP 404' in caplog.text

    def boom(url, **kw):
        raise trakt_http.requests.exceptions.Timeout()
    monkeypatch.setattr(trakt_http.requests, 'get', boom)
    with pytest.raises(trakt_http.requests.exceptions.Timeout):
        trakt_http.get('/shows/anticipated', 'secret-client-id')
    assert 'Trakt /shows/anticipated failed: Timeout' in caplog.text and 'secret-client-id' not in caplog.text


def test_a_failed_refresh_keeps_the_last_good_cards(minimal, trakt):
    c = _client(minimal)
    _connect(c)
    first = _home(c)
    assert len(first['trakt_anticipated_movies']) == 3
    app.main._trakt_cache.clear()                                  # the 15-min cache expired
    trakt['status'] = 503
    assert _home(c)['trakt_anticipated_movies'] == first['trakt_anticipated_movies']


def test_a_rail_still_loading_at_the_deadline_shows_its_last_cards(minimal, trakt, monkeypatch):
    c = _client(minimal)
    _connect(c)
    good = _home(c)['trakt_anticipated_shows']
    real = home_rails._cfg['fetch_trakt']
    gate = threading.Event()

    def slow(rid):
        if rid in ANT:
            gate.wait(5)
        return real(rid)
    monkeypatch.setitem(home_rails._cfg, 'fetch_trakt', slow)
    monkeypatch.setattr(home_rails, 'HOME_DEADLINE_S', 0.5)
    try:
        rails = _home(c)
    finally:
        gate.set()
    assert rails['trakt_anticipated_shows'] == good and rails['trakt_movies']   # served, not dropped


def test_a_first_ever_slow_rail_is_dropped_not_emptied(minimal, trakt, monkeypatch):
    c = _client(minimal)
    _connect(c)
    gate = threading.Event()
    real = home_rails._cfg['fetch_trakt']
    monkeypatch.setitem(home_rails._cfg, 'fetch_trakt', lambda rid: (gate.wait(5), real(rid))[1] if rid in ANT else real(rid))
    monkeypatch.setattr(home_rails, 'HOME_DEADLINE_S', 0.5)
    try:
        rails = _home(c)
    finally:
        gate.set()
    assert not set(ANT) & set(rails) and '1' in rails


def test_rail_fetches_run_side_by_side(minimal, trakt, monkeypatch):
    """Every rail gets its own worker: the four Trakt rails and the Plex rails are all running at once (with
    the old 6-worker pool the last rails waited for a free worker)."""
    c = _client(minimal)
    _connect(c)
    n = len(home_rails.arrange(home_rails._sections(home_rails._plex()), home_rails.load_prefs(),
                               home_rails.extra_rails())) + 1
    assert n <= home_rails.HOME_MAX_WORKERS
    barrier = threading.Barrier(4, timeout=3)
    real = home_rails._cfg['fetch_trakt']

    def together(rid):
        barrier.wait()                                             # all four Trakt rails must be in flight
        return real(rid)
    monkeypatch.setitem(home_rails._cfg, 'fetch_trakt', together)
    rails = _home(c)
    assert all(len(rails[r]) == 3 for r in ANT)


def test_a_connector_change_drops_the_last_good_cards(minimal, trakt):
    c = _client(minimal)
    _connect(c)
    _home(c)
    assert home_rails.trakt_last('trakt_anticipated_movies')
    c.put('/api/settings/connectors/trakt', json={'client_id': ''}, environ_base=REMOTE, headers=HDR)
    assert home_rails.trakt_last('trakt_anticipated_movies') is None


def test_the_connectors_key_check_and_daily_missing_send_the_user_agent(monkeypatch):
    seen = []
    monkeypatch.setattr(connectors, '_test_get', lambda url, **kw: (seen.append(kw.get('headers') or {}), R(200))[1])
    assert connectors.test_trakt_client_id('abc') is None
    assert seen[0]['User-Agent'] == trakt_http.USER_AGENT
    from app import daily_missing
    assert daily_missing._trakt_headers('abc')['User-Agent'] == trakt_http.USER_AGENT
