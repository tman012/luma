"""PM prod hotfix: POST /api/downloads-guard/run with dry_run false deletes
files, so it needs the owner signed in with a session
(start_flow.require_signed_in_owner, ported from cursor/owner-session-guard
09d32bd). A dry run keeps today's access."""
import pytest

from app import downloads_guard, start_flow
from tests.test_home_rails import REMOTE, _client

RUN = '/api/downloads-guard/run'
LAN = {'REMOTE_ADDR': '192.168.1.20'}
HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}
KEY = 'svc-' + 'k' * 20
SIGN_IN = {'error': 'Sign in first', 'sign_in': True}


@pytest.fixture
def full(test_app, monkeypatch):
    import app.main
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    runs = []
    monkeypatch.setattr(downloads_guard, 'trigger_manual_run', lambda dry_run=True: runs.append(dry_run))
    monkeypatch.setattr(app.main, '_SERVICE_API_KEY', KEY)
    app.main.settings.update({'security': {'auth_required': 'disabled_local'}})
    test_app.runs = runs
    return test_app


def _run(c, dry, env=LAN, headers=HDR):
    body = {} if dry is None else {'dry_run': dry}
    return c.post(RUN, json=body, environ_base=env, headers=headers)


@pytest.mark.parametrize('env', [LAN, REMOTE])
def test_the_signed_in_owner_may_run_for_real(full, env):
    owner = _client(full)
    r = _run(owner, False, env)
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'dry_run': False}
    assert full.runs == [False]


def test_the_implicit_lan_owner_gets_a_sign_in_401(full):
    _client(full)                                   # the owner exists; this request has no session
    r = _run(full.test_client(), False)
    assert r.status_code == 401 and r.get_json() == SIGN_IN
    assert r.headers['Cache-Control'] == 'no-store'
    assert full.runs == []


@pytest.mark.parametrize('env', [LAN, REMOTE])
def test_the_service_key_gets_401(full, env):
    _client(full)
    r = _run(full.test_client(), False, env, dict(HDR, **{'X-API-Key': KEY}))
    assert r.status_code == 401 and r.get_json() == SIGN_IN and full.runs == []


def test_remote_without_a_session_gets_401(full):
    _client(full)
    r = _run(full.test_client(), False, REMOTE)
    assert r.status_code == 401 and full.runs == []


@pytest.mark.parametrize('who', ['admin', 'viewer', 'guest'])
@pytest.mark.parametrize('env', [LAN, REMOTE])
def test_others_get_403(full, who, env):
    _client(full)                                             # the owner
    c = _client(full, admin=(who == 'admin'), name=who)       # another admin, a viewer, a guest
    r = _run(c, False, env)
    assert r.status_code == 403 and r.get_json() == {'error': 'Owner only'}
    assert full.runs == []


def test_a_plex_approved_viewer_gets_403(full, monkeypatch):
    from app import user_auth
    _client(full)
    c = _client(full, admin=False, name='plexer')
    if hasattr(user_auth, 'is_plex_approved'):
        monkeypatch.setattr(user_auth, 'is_plex_approved', lambda u: True)
    assert _run(c, False).status_code == 403 and full.runs == []


@pytest.mark.parametrize('dry', [0, '', 0.0])
def test_falsy_dry_run_values_are_real_runs_and_checked(full, dry):
    _client(full)
    r = _run(full.test_client(), dry)
    assert r.status_code == 401 and full.runs == []


@pytest.mark.parametrize('dry', [True, None])          # explicit true, or the default
def test_dry_run_keeps_todays_access(full, dry):
    """A dry run deletes nothing: the implicit LAN owner, a viewer and the
    owner all still get it exactly as before."""
    _client(full)
    viewer = _client(full, admin=False, name='viewer')
    for c in (full.test_client(), viewer, _client(full, name='owner2')):
        r = _run(c, dry)
        assert r.status_code == 200 and r.get_json() == {'ok': True, 'dry_run': True}
    assert full.runs == [True, True, True]


def test_dry_run_from_outside_without_a_session_is_unchanged(full, monkeypatch):
    """Whatever the app answered before for a remote dry run (its own sign-in
    rule) it still answers: the new check only runs for a real run."""
    _client(full)
    calls = []
    real = start_flow.require_signed_in_owner
    monkeypatch.setattr(start_flow, 'require_signed_in_owner', lambda: (calls.append(1), real())[1])
    r = _run(full.test_client(), True, REMOTE)
    assert calls == [] and r.status_code != 500


def test_the_ported_helpers_match_owner_session_guard():
    """Same names and semantics as cursor/owner-session-guard (09d32bd)."""
    assert start_flow.SIGN_IN_FIRST == 'Sign in first'
    assert callable(start_flow.require_signed_in_owner) and callable(start_flow.signed_in_owner_required)
    import inspect
    assert 'allow_implicit' in inspect.signature(start_flow._owner_or_error).parameters


def test_the_decorator_gives_the_same_answers(full):
    """signed_in_owner_required, for future TV and movie deletes."""
    hit = []

    @start_flow.signed_in_owner_required
    def delete_something():
        hit.append(1)
        return 'deleted'
    owner = _client(full)
    import app.main
    with full.test_request_context('/x', environ_base=LAN):
        app.main.load_current_user()
        resp = delete_something()
        assert resp[1] == 401 and resp[0].get_json() == SIGN_IN and hit == []
    ck = owner.get_cookie('luma_auth_session')
    with full.test_request_context('/x', environ_base=LAN, headers={'Cookie': f'{ck.key}={ck.value}'}):
        app.main.load_current_user()
        assert delete_something() == 'deleted' and hit == [1]
