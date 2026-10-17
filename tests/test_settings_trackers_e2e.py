"""Settings > Trackers end to end: a fresh install has zero trackers; a
tracker added, tested, disabled and deleted through the Settings API is
exactly what Sources (/api/sources/all-results) searches. Secrets stay masked."""

import pytest

from app import tracker_clients, tracker_routes, tracker_store
from tests.test_tracker_search import APIKEY, R, _all, client, env, rss  # noqa: F401  (fixtures)

@pytest.fixture(autouse=True)
def mocked_tester(env, monkeypatch):
    """Settings > Trackers Test goes to the same mocked trackers as Sources."""
    monkeypatch.setitem(tracker_routes._cfg, 'tester', lambda cfg: tracker_clients.test(cfg, env['mock']))


FORM = {'type': 'torznab', 'name': 'My indexer', 'url': 'https://t.example/api', 'api_key': APIKEY,
        'categories': [2000, 5000], 'priority': 25, 'enabled': True}


def test_fresh_store_is_empty_and_sources_has_nothing_to_search(client, env):
    assert env['store'].list() == []
    r = client.get('/api/trackers')
    assert r.status_code == 200 and r.get_json() == {'trackers': []}
    assert env['mock'].calls == []


def test_added_tracker_is_what_sources_searches(client, env):
    mock = env['mock']
    caps = R(200, '<?xml version="1.0"?><caps><searching><search available="yes"/></searching></caps>')
    mock.set('t.example', caps, R(200, rss(('The Nut Job 2014 1080p BluRay x264-GRP', 50, 4 * 1024 ** 3))))

    r = client.post('/api/trackers', json=FORM)
    assert r.status_code == 201, r.get_json()
    t = r.get_json()['tracker']
    assert t['api_key'] == tracker_store.SECRET_MASK and APIKEY not in r.get_data(as_text=True)
    assert APIKEY not in client.get('/api/trackers').get_data(as_text=True)

    # Test from the form (saved tracker): the key goes only to its own URL.
    r = client.post(f"/api/trackers/{t['id']}/test")
    assert r.get_json()['ok'] is True, r.get_json()
    assert all('t.example' in c['url'] for c in mock.calls)

    mock.calls.clear()
    rows = _all(client).get_json()
    found = rows if isinstance(rows, list) else (rows.get('results') or rows.get('releases') or [])
    assert mock.hits('t.example'), 'Sources must query the saved tracker'
    assert any('Nut.Job' in (x.get('title') or '') or 'Nut Job' in (x.get('title') or '') for x in found)
    assert APIKEY not in str(rows)

    # Disabled with the Enabled switch: Sources leaves it alone.
    assert client.put(f"/api/trackers/{t['id']}/enabled", json={'enabled': False}).status_code == 200
    mock.calls.clear()
    _all(client, title='Another Title')
    assert mock.hits('t.example') == []

    # Deleted: gone from the list and from Sources.
    assert client.put(f"/api/trackers/{t['id']}/enabled", json={'enabled': True}).status_code == 200
    assert client.delete(f"/api/trackers/{t['id']}").status_code == 200
    assert client.get('/api/trackers').get_json() == {'trackers': []}
    mock.calls.clear()
    _all(client, title='Third Title')
    assert mock.hits('t.example') == []


def test_failed_test_answers_a_specific_secret_free_error(client, env):
    env['mock'].set('t.example', R(401, b'Unauthorized'))
    r = client.post('/api/trackers', json=FORM)
    tid = r.get_json()['tracker']['id']
    out = client.post(f'/api/trackers/{tid}/test')
    body = out.get_json()
    assert body['ok'] is False and body['error'] and body['error'] != 'Unexpected response'
    assert APIKEY not in out.get_data(as_text=True)
    listed = client.get('/api/trackers').get_json()['trackers'][0]
    assert listed['status']['state'] == 'error' and listed['status']['message'] == body['error']
