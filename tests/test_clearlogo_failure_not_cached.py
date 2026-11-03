"""cw-logo: a failed TMDb logo lookup is never cached as "no logo" (it hid a show's clearlogo for 24h per worker);
/api/clearlogo answers it as an error so the sheet retries. A 404 / empty list is a real "no logo" and is cached."""
from unittest import mock

import pytest
import requests

import app.main as m


@pytest.fixture(autouse=True)
def _clean():
    m._logo_cache.clear(); m._logo_cache_ts.clear()
    yield
    m._logo_cache.clear(); m._logo_cache_ts.clear()


@pytest.fixture
def key():
    with mock.patch.object(m.settings, 'get', return_value='k'):
        yield


def _resp(status, logos=None):
    r = mock.Mock(status_code=status, ok=200 <= status < 300)
    r.json.return_value = {'logos': logos or []}
    r.raise_for_status.side_effect = None if r.ok else requests.HTTPError(str(status))
    return r


def test_timeout_is_not_cached_and_next_call_gets_the_logo(key):
    good = _resp(200, [{'iso_639_1': 'en', 'file_path': '/l.png', 'vote_average': 5}])
    with mock.patch.object(m.requests, 'get', side_effect=[requests.Timeout('slow'), good]):
        assert m._fetch_one_logo(1, 'tv') is None
        assert 'tv:1' not in m._logo_cache
        assert m._fetch_one_logo(1, 'tv') == '/l.png'


def test_5xx_raises_for_the_endpoint_and_is_not_cached(key):
    with mock.patch.object(m.requests, 'get', return_value=_resp(503)):
        with pytest.raises(Exception):
            m._fetch_one_logo(2, 'tv', raise_errors=True)
    assert 'tv:2' not in m._logo_cache


def test_404_and_empty_are_real_no_logo_and_cached(key):
    with mock.patch.object(m.requests, 'get', return_value=_resp(404)):
        assert m._fetch_one_logo(3, 'tv', raise_errors=True) is None
    assert m._logo_cache.get('tv:3', 'x') is None and 'tv:3' in m._logo_cache
    with mock.patch.object(m.requests, 'get', return_value=_resp(200, [])):
        assert m._fetch_one_logo(4, 'tv', raise_errors=True) is None
    assert 'tv:4' in m._logo_cache


def test_endpoint_answers_a_failed_lookup_as_an_error_and_no_logo_as_200(test_app):
    client = test_app.test_client()
    with mock.patch.object(m, '_fetch_one_logo', side_effect=requests.Timeout('slow')):
        assert client.get('/api/clearlogo/5?type=tv').status_code == 500
    with mock.patch.object(m, '_fetch_one_logo', return_value=None) as f:
        r = client.get('/api/clearlogo/5?type=tv')
    assert r.status_code == 200 and r.get_json() == {'clearlogo_url': None}
    assert f.call_args.kwargs.get('raise_errors') is True
