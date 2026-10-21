"""v248 OMDb connector: Settings > Connectors key (masked, owner only, Test = a real
lookup of tt0162222, mocked here) and app/omdb.get_ratings (cache, fail open)."""
import logging
from datetime import datetime, timedelta

import pytest
import requests as _rq

import app.main
from app import omdb
from tests.test_minimal_search import REMOTE, _client, minimal  # noqa: F401

KEY = 'omdbkey-1234abcd'
HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}
CAST_AWAY = {'Response': 'True', 'imdbID': 'tt0162222', 'imdbRating': '7.8', 'imdbVotes': '665,432'}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _rq.HTTPError(str(self.status_code))


class FakeOmdb:
    """Stands in for the requests module inside app.omdb: never the network."""
    exceptions = _rq.exceptions

    def __init__(self):
        self.calls, self.fail = [], None

    def get(self, url, params=None, **kw):
        self.calls.append(dict(params or {}))
        if self.fail:
            raise self.fail
        if params.get('apikey') != KEY:
            return Resp(401, {'Response': 'False', 'Error': 'Invalid API key!'})
        if params.get('i') == 'tt0162222':
            return Resp(200, CAST_AWAY)
        if params.get('i') == 'tt0000404':
            return Resp(200, {'Response': 'False', 'Error': 'Incorrect IMDb ID.'})
        return Resp(200, {'Response': 'True', 'imdbID': params['i'], 'imdbRating': 'N/A', 'imdbVotes': 'N/A'})

    def Session(self):
        fake = self

        class S:
            trust_env = True
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, url, **kw): return fake.get(url, **kw)
        return S()


@pytest.fixture
def fake(monkeypatch):
    f = FakeOmdb()
    monkeypatch.setattr(omdb, 'requests', f)
    omdb.forget()
    yield f
    omdb.forget()


def _set(key):
    app.main.settings.update({'apis': {'omdb': {'api_key': key, 'connector_saved': True}}})


def test_connector_masks_tests_saves_and_removes(minimal, fake, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    _set('')
    c = _client(minimal)
    assert c.get('/api/settings/connectors', environ_base=REMOTE).get_json()['omdb'] == {'set': False, 'last4': ''}
    r = c.post('/api/settings/connectors/omdb/test', json={'api_key': 'wrong-key-000'}, environ_base=REMOTE, headers=HDR)
    assert r.get_json() == {'ok': False, 'error': 'Invalid key'}
    r = c.post('/api/settings/connectors/omdb/test', json={'api_key': KEY}, environ_base=REMOTE, headers=HDR)
    assert r.get_json() == {'ok': True, 'message': 'Connected'}
    assert fake.calls[-1] == {'apikey': KEY, 'i': 'tt0162222'}
    r = c.put('/api/settings/connectors/omdb', json={'api_key': 'wrong-key-000'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400
    r = c.put('/api/settings/connectors/omdb', json={'api_key': KEY}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['omdb'] == {'set': True, 'last4': KEY[-4:]}
    assert app.main.settings.get('apis.omdb.api_key') == KEY
    g = c.get('/api/settings/connectors', environ_base=REMOTE).get_data(as_text=True)
    assert KEY not in g
    r = c.post('/api/settings/connectors/omdb/test', json={}, environ_base=REMOTE, headers=HDR)     # the stored key
    assert r.get_json()['ok'] is True
    fake.fail = _rq.exceptions.Timeout()
    assert c.post('/api/settings/connectors/omdb/test', json={}, environ_base=REMOTE, headers=HDR).get_json() == {'ok': False, 'error': 'Timed out'}
    fake.fail = None
    r = c.put('/api/settings/connectors/omdb', json={'api_key': ''}, environ_base=REMOTE, headers=HDR)
    assert r.get_json()['omdb'] == {'set': False, 'last4': ''}
    assert KEY not in caplog.text


def test_connector_is_owner_only(minimal, fake):  # noqa: F811
    c = app.main.app.test_client()
    r = c.put('/api/settings/connectors/omdb', json={'api_key': KEY}, headers=HDR)
    assert r.status_code in (401, 403)
    assert fake.calls == []


def test_minimal_ignores_a_key_not_saved_in_connectors(minimal):  # noqa: F811
    app.main.settings.update({'apis': {'omdb': {'api_key': KEY, 'connector_saved': False}}})
    assert app.main.settings.get('apis.omdb.api_key') == ''
    _set(KEY)
    assert app.main.settings.get('apis.omdb.api_key') == KEY
    _set('')


def test_minimal_allows_the_omdb_paths():
    from app import minimal as m
    assert m.ALLOWED_PATHS >= {'/api/settings/connectors/omdb', '/api/settings/connectors/omdb/test'}
    assert not m.path_allowed('/api/ratings/Heat', 'GET')   # v217 stays: title lookups closed; get_ratings is by id


def test_get_ratings_caches_and_fails_open(fake, monkeypatch):
    monkeypatch.setattr(omdb, 'api_key', lambda: KEY)
    assert omdb.get_ratings('tt0162222') == {'rating': 7.8, 'votes': 665432}
    assert omdb.get_ratings('tt0162222') == {'rating': 7.8, 'votes': 665432}
    assert len(fake.calls) == 1                                          # cached
    assert omdb.get_ratings('tt0000404') == {'rating': None, 'votes': None}   # asked: IMDb has nothing (a verdict)
    assert omdb.get_ratings('tt0000404') == {'rating': None, 'votes': None}
    assert len(fake.calls) == 2
    assert omdb.get_ratings('tt1234567') == {'rating': None, 'votes': None}   # N/A
    fake.fail = _rq.exceptions.ConnectionError()
    assert omdb.get_ratings('tt7654321') is None                         # could not ask: fail open
    assert omdb.get_ratings('tt7654321') is None
    assert 'tt7654321' not in omdb._cache                                # failures never cached
    fake.fail = None
    assert omdb.get_ratings('tt7654321') == {'rating': None, 'votes': None}
    assert omdb.get_ratings('not-an-id') is None


def test_no_key_means_no_call(fake, monkeypatch):
    monkeypatch.setattr(omdb, 'api_key', lambda: '')
    assert omdb.get_ratings('tt0162222') is None
    assert fake.calls == []


def test_stale_verdicts_expire_and_the_cache_is_capped(fake, monkeypatch):
    monkeypatch.setattr(omdb, 'api_key', lambda: KEY)
    old = (datetime.now() - timedelta(days=omdb.CACHE_DAYS + 1)).strftime('%Y-%m-%d')
    omdb._cache['tt0162222'] = {'rating': 1.0, 'votes': 1, 'at': old}
    assert omdb.get_ratings('tt0162222') == {'rating': 7.8, 'votes': 665432}
    monkeypatch.setattr(omdb, 'CACHE_MAX', 3)
    for i in range(5):
        omdb.get_ratings(f'tt100000{i}')
    assert len(omdb._cache) <= 3
