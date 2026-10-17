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


def test_the_helper_a_new_downloads_root_widens_while_on():
    moved = _settings()
    moved['media']['downloads_root'] = '/'
    assert adg._widens_auto_delete(_settings(), moved) == [adg.ROOT]
    off_old, off_new = _settings(enabled=False), _settings(enabled=False)
    off_new['media']['downloads_root'] = '/'
    assert adg._widens_auto_delete(off_old, off_new) == []


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
