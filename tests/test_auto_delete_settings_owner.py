"""PM ruling: a settings change that turns on or widens automatic file
deletion (Downloads Guard) needs the owner signed in with a session. Turning
it off or tightening it stays open to any admin."""
import pytest

import app.main
from app import auto_delete_guard as adg
from tests.test_home_rails import REMOTE, _client

LAN = {'REMOTE_ADDR': '192.168.1.20'}
HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}
KEY = 'svc-' + 'k' * 20
SIGN_IN = {'error': 'Sign in first', 'sign_in': True}
BASE = {'enabled': True, 'min_free_gb': 150, 'reclaim_gb': 100, 'daily_cap_gb': 500,
        'check_interval_minutes': 15, 'trackers': ['ipt']}


@pytest.fixture
def full(test_app, monkeypatch, tmp_path):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    monkeypatch.setattr(app.main, '_SERVICE_API_KEY', KEY)
    saved_auto = app.main.settings.get_section('automation')
    saved_media = app.main.settings.get_section('media')
    root = tmp_path / 'downloads'
    root.mkdir()
    (tmp_path / 'elsewhere').mkdir()
    app.main.settings.update({'security': {'auth_required': 'disabled_local'},
                              'automation': {'downloads_guard': dict(BASE)},
                              'media': {'downloads_root': str(root)}})
    test_app.tmp = tmp_path
    yield test_app
    app.main.settings.update({'automation': saved_auto, 'media': saved_media})


def _guard(**kw):
    return {'automation': {'downloads_guard': kw}}


def _put(c, body, env=LAN, headers=HDR):
    return c.put('/api/settings', json=body, environ_base=env, headers=headers)


def _now(key):
    return app.main.settings.get(adg.PREFIX + key)


WIDENING = [
    ('min_free_gb', 151),        # fires at more free space
    ('reclaim_gb', 101),         # deletes more per run
    ('daily_cap_gb', 501),       # higher ceiling
    ('daily_cap_gb', 0),         # no ceiling at all
    ('check_interval_minutes', 14),   # runs more often
]
TIGHTENING = [
    ('min_free_gb', 149),
    ('reclaim_gb', 99),
    ('daily_cap_gb', 499),
    ('check_interval_minutes', 16),
    ('trackers', []),            # order of spending only, never more
]


def _as(full, who):
    owner = _client(full)                       # the owner (first admin)
    full.owner = owner
    if who == 'owner':
        return owner, {}
    if who == 'implicit':
        return full.test_client(), {}
    if who == 'key':
        return full.test_client(), {'X-API-Key': KEY}
    return _client(full, admin=True, name='second'), {}


# ── the helper ────────────────────────────────────────────────────────────

def _settings(**kw):
    g = dict(BASE, **kw)
    return {'automation': {'downloads_guard': g}, 'media': {'downloads_root': '/downloads'}}


@pytest.mark.parametrize('key,value', WIDENING)
def test_the_helper_names_each_widening_field(key, value):
    assert adg._widens_auto_delete(_settings(), _settings(**{key: value})) == [adg.PREFIX + key]


@pytest.mark.parametrize('key,value', TIGHTENING)
def test_the_helper_lets_tightening_through(key, value):
    assert adg._widens_auto_delete(_settings(), _settings(**{key: value})) == []


def test_the_helper_enabling_widens_and_disabling_never_does():
    off = _settings(enabled=False)
    assert adg._widens_auto_delete(off, _settings()) == [adg.ENABLED]
    assert adg._widens_auto_delete(_settings(), off) == []
    # off plus looser numbers in one save is still only an off
    assert adg._widens_auto_delete(_settings(), _settings(enabled=False, reclaim_gb=999)) == []
    assert adg._widens_auto_delete(off, off) == []


def test_the_helper_cap_from_none_to_some_tightens():
    assert adg._widens_auto_delete(_settings(daily_cap_gb=0), _settings(daily_cap_gb=10)) == []


def test_the_helper_reads_values_like_the_guard_does():
    # Clamped at the guard's own limits: 10 000 GB and 12 000 GB read the same.
    assert adg._widens_auto_delete(_settings(min_free_gb=10_000), _settings(min_free_gb=12_000)) == []
    # Strings count the way the guard parses them.
    assert adg._widens_auto_delete(_settings(), _settings(reclaim_gb='300')) == [adg.RECLAIM]
    # Unparseable falls back to the default (100), so no change.
    assert adg._widens_auto_delete(_settings(), _settings(reclaim_gb='lots')) == []


def test_the_helper_a_new_downloads_root_widens_on_or_off():
    moved = _settings()
    moved['media']['downloads_root'] = '/'
    assert adg._widens_auto_delete(_settings(), moved) == [adg.ROOT]
    off_old, off_new = _settings(enabled=False), _settings(enabled=False)
    off_new['media']['downloads_root'] = '/'
    assert adg._widens_auto_delete(off_old, off_new) == [adg.ROOT]
    # turning it off in the same save doesn't excuse the move
    off_moved = _settings(enabled=False)
    off_moved['media']['downloads_root'] = '/'
    assert adg._widens_auto_delete(_settings(), off_moved) == [adg.ROOT]
    # any change to the stored value counts: no resolving, no fallback
    for value in ('/downloads/', '', None):
        changed = _settings(enabled=False)
        changed['media']['downloads_root'] = value
        assert adg._widens_auto_delete(off_old, changed) == [adg.ROOT], value
    gone = _settings(enabled=False)
    del gone['media']['downloads_root']
    assert adg._widens_auto_delete(off_old, gone) == [adg.ROOT]
    # unchanged is fine, on or off
    assert adg._widens_auto_delete(off_old, _settings(enabled=False)) == []
    assert adg._widens_auto_delete(_settings(), _settings()) == []
    assert adg.ROOT in adg.DELETE_ROOTS and adg.ROOT in adg.WIDENING_PATHS


# ── the route ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('key,value', WIDENING)
@pytest.mark.parametrize('env', [LAN, REMOTE])
def test_the_signed_in_owner_may_widen(full, key, value, env):
    owner, _ = _as(full, 'owner')
    r = _put(owner, _guard(**{key: value}), env)
    assert r.status_code == 200 and r.get_json()['success'] is True
    assert _now(key) == value


@pytest.mark.parametrize('key,value', WIDENING)
@pytest.mark.parametrize('who', ['implicit', 'key'])
def test_widening_without_a_session_gets_a_sign_in_401(full, key, value, who):
    c, extra = _as(full, who)
    r = _put(c, _guard(**{key: value}), LAN, dict(HDR, **extra))
    assert r.status_code == 401 and r.get_json() == SIGN_IN
    assert r.headers['Cache-Control'] == 'no-store'
    assert _now(key) == BASE[key]


@pytest.mark.parametrize('key,value', WIDENING)
def test_another_admin_widening_gets_403(full, key, value):
    c, _ = _as(full, 'admin')
    r = _put(c, _guard(**{key: value}))
    assert r.status_code == 403 and r.get_json() == {'error': 'Owner only'}
    assert _now(key) == BASE[key]


def test_the_service_key_from_outside_gets_401(full):
    c, extra = _as(full, 'key')
    r = _put(c, _guard(reclaim_gb=999), REMOTE, dict(HDR, **extra))
    assert r.status_code == 401 and _now('reclaim_gb') == 100


def test_no_session_from_outside_gets_401(full):
    _as(full, 'owner')
    r = _put(full.test_client(), _guard(reclaim_gb=999), REMOTE)
    assert r.status_code == 401 and _now('reclaim_gb') == 100


@pytest.mark.parametrize('who', ['viewer', 'guest'])
def test_non_admins_get_403_as_today(full, who):
    _client(full)
    c = _client(full, admin=False, name=who)
    for body in (_guard(reclaim_gb=999), _guard(enabled=False)):
        assert _put(c, body).status_code == 403
    assert _now('reclaim_gb') == 100 and _now('enabled') is True


@pytest.mark.parametrize('who', ['implicit', 'key', 'admin'])
def test_enabling_needs_the_owner(full, who):
    app.main.settings.update(_guard(enabled=False))
    c, extra = _as(full, who)
    r = _put(c, _guard(enabled=True), LAN, dict(HDR, **extra))
    assert r.status_code == (403 if who == 'admin' else 401)
    assert _now('enabled') is False
    assert _put(full.owner, _guard(enabled=True)).status_code == 200 and _now('enabled') is True


@pytest.mark.parametrize('who', ['implicit', 'key', 'admin', 'owner'])
def test_any_admin_may_disable(full, who):
    c, extra = _as(full, who)
    r = _put(c, _guard(enabled=False, reclaim_gb=999), LAN, dict(HDR, **extra))
    assert r.status_code == 200, r.get_json()
    assert _now('enabled') is False


@pytest.mark.parametrize('key,value', TIGHTENING)
@pytest.mark.parametrize('who', ['implicit', 'key', 'admin'])
def test_any_admin_may_tighten(full, key, value, who):
    c, extra = _as(full, who)
    r = _put(c, _guard(**{key: value}), LAN, dict(HDR, **extra))
    assert r.status_code == 200, r.get_json()
    assert _now(key) == value


def test_moving_the_downloads_root_while_on_needs_the_owner(full):
    c, _ = _as(full, 'implicit')
    elsewhere = str(full.tmp / 'elsewhere')
    r = _put(c, {'media': {'downloads_root': elsewhere}})
    assert r.status_code == 401 and r.get_json() == SIGN_IN
    assert _put(full.owner, {'media': {'downloads_root': elsewhere}}).status_code == 200
    assert app.main.settings.get('media.downloads_root') == elsewhere


def test_other_settings_keep_lan_access(full):
    c, _ = _as(full, 'implicit')
    r = _put(c, {'ui': {'theme': 'dark'}})
    assert r.status_code != 401 and r.status_code != 403


def test_a_mixed_save_is_refused_whole(full):
    c, _ = _as(full, 'implicit')
    r = _put(c, _guard(reclaim_gb=999, check_interval_minutes=30))
    assert r.status_code == 401
    assert _now('reclaim_gb') == 100 and _now('check_interval_minutes') == 15


# ── the folder deletions come from, guard off ─────────────────────────────

@pytest.mark.parametrize('who', ['implicit', 'key', 'admin'])
def test_moving_the_downloads_root_while_off_is_refused(full, who):
    app.main.settings.update(_guard(enabled=False))
    before = app.main.settings.get('media.downloads_root')
    c, extra = _as(full, who)
    r = _put(c, {'media': {'downloads_root': str(full.tmp / 'elsewhere')}}, LAN, dict(HDR, **extra))
    assert r.status_code == (403 if who == 'admin' else 401)
    assert r.get_json() == ({'error': 'Owner only'} if who == 'admin' else SIGN_IN)
    assert r.headers['Cache-Control'] == 'no-store'
    assert app.main.settings.get('media.downloads_root') == before


def test_the_owner_may_move_the_downloads_root_while_off(full):
    app.main.settings.update(_guard(enabled=False))
    elsewhere = str(full.tmp / 'elsewhere')
    _as(full, 'owner')
    assert _put(full.owner, {'media': {'downloads_root': elsewhere}}).status_code == 200
    assert app.main.settings.get('media.downloads_root') == elsewhere


def test_turning_off_and_moving_in_one_save_is_refused(full):
    c, _ = _as(full, 'admin')
    r = _put(c, {'automation': {'downloads_guard': {'enabled': False}},
                 'media': {'downloads_root': str(full.tmp / 'elsewhere')}})
    assert r.status_code == 403 and _now('enabled') is True


# ── decided under the settings lock, against the merged result ─────────────

def test_a_save_landing_between_check_and_write_cannot_widen(full, monkeypatch):
    """The guard is on. The implicit LAN owner sends enabled=true (no change
    against what it read). Before its write, an admin turns the guard off.
    Judged against the settings it is about to replace, the implicit save
    would turn the guard back on: refused."""
    c, _ = _as(full, 'implicit')
    admin = _client(full, admin=True, name='second')
    real = app.main._start_flow.require_signed_in_owner

    fired = []

    def interleave():
        out = real()
        if not fired:                          # once: the admin's own save runs this too
            fired.append(1)
            assert _put(admin, _guard(enabled=False)).status_code == 200   # lands first
        return out
    monkeypatch.setattr(app.main._start_flow, 'require_signed_in_owner', interleave)
    r = _put(c, _guard(enabled=True))
    assert r.status_code == 401 and r.get_json() == SIGN_IN
    assert _now('enabled') is False


def test_concurrent_saves_never_leave_the_guard_on(full):
    """Threads race an admin turning the guard off against the implicit LAN
    owner re-sending enabled=true. Either order must end with it off: the
    implicit save only succeeds while it is already on."""
    import threading
    implicit = full.test_client()
    _as(full, 'owner')
    admin = _client(full, admin=True, name='racer')
    for _ in range(25):
        app.main.settings.update(_guard(enabled=True))
        go = threading.Barrier(2)
        out = {}

        def turn_off():
            go.wait()
            out['off'] = _put(admin, _guard(enabled=False)).status_code

        def turn_on():
            go.wait()
            out['on'] = _put(implicit, _guard(enabled=True)).status_code
        ts = [threading.Thread(target=turn_off), threading.Thread(target=turn_on)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        assert out['off'] == 200 and out['on'] in (200, 401)
        assert _now('enabled') is False, out


def test_the_veto_runs_under_the_settings_lock(full, monkeypatch):
    seen = []
    real = app.main._auto_delete_veto

    def spy(owner_err):
        v = real(owner_err)
        if v is None:
            return None

        def checked(current, candidate):
            seen.append(app.main.settings._lock.locked())
            return v(current, candidate)
        return checked
    monkeypatch.setattr(app.main, '_auto_delete_veto', spy)
    c, _ = _as(full, 'implicit')
    assert _put(c, _guard(reclaim_gb=999)).status_code == 401
    assert seen == [True]


@pytest.mark.parametrize('who', ['implicit', 'key', 'admin'])
@pytest.mark.parametrize('on', [True, False])
def test_clearing_the_downloads_root_is_refused(full, who, on):
    app.main.settings.update(_guard(enabled=on))
    before = app.main.settings.get('media.downloads_root')
    c, extra = _as(full, who)
    r = _put(c, {'media': {'downloads_root': ''}}, LAN, dict(HDR, **extra))
    assert r.status_code == (403 if who == 'admin' else 401)
    assert app.main.settings.get('media.downloads_root') == before


@pytest.mark.parametrize('who', ['implicit', 'key', 'admin'])
def test_resaving_the_same_downloads_root_is_fine(full, who):
    app.main.settings.update(_guard(enabled=False))
    same = app.main.settings.get('media.downloads_root')
    c, extra = _as(full, who)
    r = _put(c, {'media': {'downloads_root': same}, 'automation': {'downloads_guard': {'reclaim_gb': 50}}},
             LAN, dict(HDR, **extra))
    assert r.status_code == 200, r.get_json()
    assert _now('reclaim_gb') == 50


def test_the_owner_may_clear_the_downloads_root(full):
    _as(full, 'owner')
    assert _put(full.owner, {'media': {'downloads_root': ''}}).status_code == 200
    assert app.main.settings.get('media.downloads_root') == ''
