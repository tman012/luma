"""QA osg-ab1c841: edges of the signed-in-owner guard on POST /api/downloads-guard/run and the tracker 401s.
Complements tests/test_signed_in_owner_guard.py (Builder) and tests/test_qa_fm_82a1404.py (minimal 404s)."""
import pytest

from tests.test_home_rails import REMOTE, _client
from tests.test_file_management import HDR
from tests.test_signed_in_owner_guard import KEY, LAN, _run, full, guard  # noqa: F401  (fixtures)


@pytest.mark.parametrize('falsy', [False, 0, None, ''])
def test_every_falsy_dry_run_is_a_real_run_and_is_guarded(guard, falsy):
    """{"dry_run": 0 | null | ""} are real runs (bool() of them is False): same 401 as false, nothing triggered."""
    _client(guard)
    r = guard.test_client().post('/api/downloads-guard/run', json={'dry_run': falsy}, environ_base=LAN, headers=HDR)
    assert r.status_code == 401 and r.get_json() == {'error': 'Sign in first', 'sign_in': True}
    assert guard.runs == []


@pytest.mark.parametrize('truthy', [True, 1, 'false', 'no'])
def test_truthy_dry_run_values_stay_a_dry_run(guard, truthy):
    """A string "false" is truthy -> a dry run (safe direction): never deletes, LAN access unchanged."""
    _client(guard)
    r = guard.test_client().post('/api/downloads-guard/run', json={'dry_run': truthy}, environ_base=LAN, headers=HDR)
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'dry_run': True}
    assert guard.runs == [True]


def test_service_key_wins_over_an_owner_cookie(guard):
    """X-API-Key takes precedence over the session cookie: a real run is refused even with the owner's cookie."""
    owner = _client(guard)
    r = _run(owner, False, REMOTE, dict(HDR, **{'X-API-Key': KEY}))
    assert r.status_code == 401 and r.get_json()['sign_in'] is True
    assert guard.runs == []


def test_non_json_body_runs_nothing(guard):
    # Adapted from the QA patch (which expected a 200 dry run): since the
    # reviewer's ruling (main._json_object_body, same as sources-followup) a
    # body that isn't a JSON object is a 400 and nothing runs, dry or real.
    _client(guard)
    r = guard.test_client().post('/api/downloads-guard/run', data='dry_run=false', environ_base=LAN,
                                 headers=dict(HDR, **{'Content-Type': 'application/x-www-form-urlencoded'}))
    assert r.status_code == 400 and guard.runs == []


@pytest.mark.parametrize('method,url,body', [('PUT', '/api/trackers/1/enabled', {'enabled': True}),
                                             ('POST', '/api/trackers/test', {}),
                                             ('DELETE', '/api/trackers/1', None)])
def test_tracker_writes_from_the_implicit_lan_owner_say_sign_in(full, method, url, body):
    _client(full)
    r = full.test_client().open(url, method=method, json=body, environ_base=LAN, headers=HDR)
    assert r.status_code == 401 and r.get_json().get('sign_in') is True


def test_tracker_routes_refuse_the_service_key(full):
    _client(full)
    r = full.test_client().get('/api/trackers', environ_base=REMOTE, headers=dict(HDR, **{'X-API-Key': KEY}))
    assert r.status_code in (401, 403) and 'api_key' not in r.get_data(as_text=True)
