"""v170 tracker port step 2: optional owner-triggered Import from Prowlarr.

A fake Prowlarr (a fake session; nothing leaves the test) and fake trackers only.
"""

import json
import logging
import time

import pytest

import app.main
import app.user_auth
from app import indexer_api as ia
from app import tracker_guard, tracker_import, tracker_routes, tracker_store
from app.tracker_guard import TrackerGuard
from app.tracker_store import SECRET_MASK, TrackerStore

LAN = {'REMOTE_ADDR': '192.168.1.20'}
BASE = 'http://prowlarr.fake:9696'
PKEY = 'prowlarr-key-0123456789abcdef'
COOKIE = 'uid=4242; pass=feedfacefeedfacefeedfacefeedface'
BHD_KEY = 'bhd-api-0123456789abcdef0123'
BHD_RSS = 'bhd-rss-0123456789abcdef0123'
AVZ_PASS = 'avz-pass-9876543210'


def _f(**kw):
    return [{'name': k, 'value': v} for k, v in kw.items()]


def _indexers():
    return [
        {'id': 3, 'name': 'IPTorrents', 'definitionName': 'IPTorrents', 'implementation': 'Cardigann',
         'enable': True, 'priority': 10, 'protocol': 'torrent',
         'fields': _f(baseUrl='https://iptorrents.fake/', cookie=COOKIE, userAgent='UA/1')},
        {'id': 5, 'name': 'BeyondHD', 'definitionName': 'BeyondHD', 'implementation': 'BeyondHD',
         'enable': True, 'priority': 20,
         'fields': _f(baseUrl='https://beyond-hd.fake/', apiKey=BHD_KEY, rssKey=BHD_RSS)},
        {'id': 7, 'name': 'AvistaZ', 'definitionName': 'AvistaZ', 'implementation': 'AvistaZ',
         'enable': True, 'priority': 25,
         'fields': _f(baseUrl='https://avistaz.fake/', username='me', password='********', pid='pid123')},
        {'id': 9, 'name': 'Some Public Site', 'definitionName': 'somepublicsite', 'implementation': 'Cardigann',
         'enable': True, 'fields': _f(baseUrl='https://public.fake/')},
        {'id': 11, 'name': 'My Torznab', 'implementation': 'Torznab', 'definitionName': 'torznab',
         'enable': True, 'priority': 30, 'fields': _f(baseUrl='https://torznab.fake', apiPath='/api', apiKey='tzkey')},
    ]


class _Resp:
    def __init__(self, data, status=200):
        self.status_code = status
        self._body = json.dumps(data).encode()

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass


class FakeProwlarr:
    """Answers GET /api/v1/indexer; records everything; any other call fails the test."""

    def __init__(self, data=None, status=200):
        self.data = _indexers() if data is None else data
        self.status = status
        self.calls = []
        self.sessions = []

    def __call__(self):
        outer = self

        class S:
            trust_env = True

            def get(self, url, **kw):
                outer.calls.append(('GET', url, kw, self.trust_env))
                assert url == f'{BASE}/api/v1/indexer'
                return _Resp(outer.data, outer.status)

            def post(self, *a, **kw):
                raise AssertionError('Import only reads from Prowlarr')

            put = delete = post

            def close(self):
                pass
        s = S()
        self.sessions.append(s)
        return s


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('LUMA_STATE_DIR', str(tmp_path))
    s = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key')
    tracker_store.reset_store_for_tests(s)
    g = TrackerGuard(tmp_path / 'tracker_guard.json')
    tracker_guard.reset_guard_for_tests(g)
    ia._guard_reset_memory()
    tracker_import.reset_for_tests()
    fake = FakeProwlarr()
    monkeypatch.setattr(tracker_import, '_session_factory', fake)
    settings = {'indexer.provider': 'prowlarr', 'indexer.base_url': BASE, 'indexer.api_key': PKEY}
    monkeypatch.setitem(tracker_routes._cfg, 'get_setting', lambda k: settings.get(k))
    tested = []

    def tester(cfg):
        tested.append(dict(cfg))
    monkeypatch.setitem(tracker_routes._cfg, 'tester', tester)
    tracker_routes._test_log.clear()
    yield {'store': s, 'guard': g, 'fake': fake, 'settings': settings, 'tested': tested, 'dir': tmp_path}
    tracker_guard.reset_guard_for_tests(None)
    tracker_store.reset_store_for_tests(None)
    ia._guard_reset_memory()
    tracker_import.reset_for_tests()


@pytest.fixture
def owner(test_app, env):
    o = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.environ_base.update({'HTTP_ORIGIN': 'http://localhost', 'HTTP_HOST': 'localhost'})
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(o['id']))
    return c


def _post(c, path, body=None):
    return c.post(path, data=json.dumps(body or {}), content_type='application/json')


def _preview(c):
    r = _post(c, '/api/trackers/import/prowlarr/preview')
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _all_keys(p):
    return [r['key'] for r in p['indexers'] if r['state'] in ('ready', 'needs_credentials')]


# ------------------------------------------------------------------ optional, owner-triggered

def test_offered_only_when_prowlarr_is_set_up(owner, env):
    assert owner.get('/api/trackers/import/prowlarr').get_json() == {'available': True}
    env['settings']['indexer.api_key'] = ''
    assert owner.get('/api/trackers/import/prowlarr').get_json() == {'available': False}
    r = _post(owner, '/api/trackers/import/prowlarr/preview')
    assert r.status_code == 409 and r.get_json()['error'] == tracker_import.NOT_CONFIGURED
    env['settings'].update({'indexer.api_key': PKEY, 'indexer.provider': 'jackett'})
    assert owner.get('/api/trackers/import/prowlarr').get_json() == {'available': False}
    assert env['fake'].calls == []                  # asking whether it's offered never calls Prowlarr


def test_nothing_runs_or_seeds_on_its_own(test_app, env):
    """Booting the app, listing trackers and reading settings never contacts Prowlarr or adds one."""
    o = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update(LAN)
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(o['id']))
    assert c.get('/api/trackers').get_json() == {'trackers': []}
    assert env['fake'].calls == [] and env['store'].list() == []
    assert not (env['dir'] / 'trackers.json').exists()
    src = open(tracker_import.__file__).read()
    assert 'Thread(' not in src and 'schedule' not in src


def test_import_routes_are_owner_only(test_app, env):
    app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    u = app.user_auth.create_user('kid', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update({'REMOTE_ADDR': '203.0.113.9', 'HTTP_ORIGIN': 'http://localhost', 'HTTP_HOST': 'localhost'})
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(u['id']))
    assert c.get('/api/trackers/import/prowlarr').status_code == 403
    assert _post(c, '/api/trackers/import/prowlarr/preview').status_code == 403
    assert _post(c, '/api/trackers/import/prowlarr', {'preview_id': 'x', 'keys': [3]}).status_code == 403
    assert env['fake'].calls == [] and env['store'].list() == []


# ------------------------------------------------------------------ preview first

def test_preview_reads_once_and_shows_no_secrets(owner, env, caplog):
    caplog.set_level(logging.DEBUG)
    p = _preview(owner)
    assert len(env['fake'].calls) == 1
    _, url, kw, trust_env = env['fake'].calls[0]
    assert kw['headers']['X-Api-Key'] == PKEY and kw['allow_redirects'] is False and trust_env is False
    assert kw['timeout'] == tracker_import.TIMEOUT_S
    rows = {r['key']: r for r in p['indexers']}
    assert rows[3]['state'] == 'ready' and rows[3]['type'] == 'iptorrents'
    assert rows[5]['state'] == 'ready' and rows[5]['type'] == 'beyondhd'
    assert rows[7]['state'] == 'needs_credentials' and rows[7]['missing'] == ['Password']
    assert rows[9]['state'] == 'unsupported' and rows[9]['type'] is None
    assert rows[11]['state'] == 'ready' and rows[11]['type'] == 'torznab'
    text = json.dumps(p) + caplog.text
    for secret in (COOKIE, BHD_KEY, BHD_RSS, PKEY, 'tzkey', 'pid123', 'UA/1'):
        assert secret not in text
    assert env['store'].list() == []                # a preview creates nothing


def test_prowlarr_unreachable_or_odd_answers(owner, env):
    env['fake'].status = 401
    r = _post(owner, '/api/trackers/import/prowlarr/preview')
    assert r.status_code == 502 and r.get_json() == {'error': tracker_import.UNREACHABLE}
    env['fake'].status, env['fake'].data = 200, {'not': 'a list'}
    assert _post(owner, '/api/trackers/import/prowlarr/preview').status_code == 502
    assert env['store'].list() == []


# ------------------------------------------------------------------ created disabled, Test required

def test_import_creates_disabled_trackers_with_test_required(owner, env):
    p = _preview(owner)
    r = _post(owner, '/api/trackers/import/prowlarr', {'preview_id': p['preview_id'], 'keys': [3, 5, 7, 9, 11]})
    assert r.status_code == 201
    body = r.get_json()
    names = sorted(t['name'] for t in body['created'])
    assert names == ['AvistaZ', 'BeyondHD', 'IPTorrents', 'My Torznab']   # unsupported one not created
    for t in body['created']:
        assert t['enabled'] is False and t['test_required'] is True and t['imported'] is True
    avz = next(t for t in body['created'] if t['type'] == 'avistaz')
    assert avz['missing'] == ['password']
    assert env['tested'] == []                     # nothing contacted the trackers
    assert len(env['fake'].calls) == 1             # and Prowlarr only for the preview
    ipt = next(t for t in env['store'].list() if t['type'] == 'iptorrents')
    assert ipt['url'] == 'https://iptorrents.fake' and ipt['priority'] == 10
    tz = next(t for t in env['store'].list() if t['type'] == 'torznab')
    assert tz['url'].startswith('https://torznab.fake') and tz['url'].endswith('/api')


def test_credentials_encrypted_and_masked(owner, env):
    p = _preview(owner)
    body = _post(owner, '/api/trackers/import/prowlarr', {'preview_id': p['preview_id'], 'keys': [3, 5]}).get_json()
    raw = (env['dir'] / 'trackers.json').read_text()
    for secret in (COOKIE, BHD_KEY, BHD_RSS):
        assert secret not in raw and secret not in json.dumps(body)
        assert secret not in owner.get('/api/trackers').get_data(as_text=True)
    bhd = next(t for t in body['created'] if t['type'] == 'beyondhd')
    assert bhd['api_key'] == SECRET_MASK and bhd['rss_key'] == SECRET_MASK
    cfg = env['store'].config(next(t for t in env['store'].list() if t['type'] == 'beyondhd'))
    assert cfg['api_key'] == BHD_KEY and cfg['rss_key'] == BHD_RSS      # decrypts server side


def test_enabling_needs_a_passing_test(owner, env):
    p = _preview(owner)
    created = _post(owner, '/api/trackers/import/prowlarr', {'preview_id': p['preview_id'], 'keys': [3]}).get_json()
    tid = created['created'][0]['id']
    r = owner.put(f'/api/trackers/{tid}/enabled', data=json.dumps({'enabled': True}), content_type='application/json')
    assert r.status_code == 409 and r.get_json()['error'] == 'Test required'
    rec = owner.get(f'/api/trackers/{tid}').get_json()['tracker']
    edit = dict(rec, enabled=True)
    r = owner.put(f'/api/trackers/{tid}', data=json.dumps(edit), content_type='application/json')
    assert r.status_code == 400 and r.get_json()['fields']['enabled'] == 'Test required'
    assert env['store'].get(tid)['enabled'] is False
    # an edit that keeps it disabled is fine (and keeps the flag)
    r = owner.put(f'/api/trackers/{tid}', data=json.dumps(dict(rec, priority=12)), content_type='application/json')
    assert r.status_code == 200 and r.get_json()['tracker']['test_required'] is True
    assert _post(owner, f'/api/trackers/{tid}/test').get_json()['ok'] is True
    assert len(env['tested']) == 1
    assert owner.get(f'/api/trackers/{tid}').get_json()['tracker']['test_required'] is False
    r = owner.put(f'/api/trackers/{tid}/enabled', data=json.dumps({'enabled': True}), content_type='application/json')
    assert r.status_code == 200 and r.get_json()['tracker']['enabled'] is True


def test_failed_test_keeps_it_locked(owner, env, monkeypatch):
    from app.tracker_http import TrackerError
    p = _preview(owner)
    tid = _post(owner, '/api/trackers/import/prowlarr', {'preview_id': p['preview_id'], 'keys': [5]}).get_json()['created'][0]['id']

    def bad(cfg):
        raise TrackerError('auth', 'Sign-in failed')
    monkeypatch.setitem(tracker_routes._cfg, 'tester', bad)
    assert _post(owner, f'/api/trackers/{tid}/test').get_json()['ok'] is False
    r = owner.put(f'/api/trackers/{tid}/enabled', data=json.dumps({'enabled': True}), content_type='application/json')
    assert r.status_code == 409


def test_missing_credentials_test_answers_locally(owner, env):
    p = _preview(owner)
    tid = _post(owner, '/api/trackers/import/prowlarr', {'preview_id': p['preview_id'], 'keys': [7]}).get_json()['created'][0]['id']
    before = json.dumps(env['guard'].log)
    r = _post(owner, f'/api/trackers/{tid}/test').get_json()
    assert r == {'ok': False, 'error': 'Enter credentials'}
    assert env['tested'] == [] and json.dumps(env['guard'].log) == before
    # entering the password, then a Test, unlocks it
    rec = owner.get(f'/api/trackers/{tid}').get_json()['tracker']
    r = owner.put(f'/api/trackers/{tid}', data=json.dumps(dict(rec, password='new-pass-123')),
                  content_type='application/json')
    assert r.status_code == 200 and r.get_json()['tracker']['missing'] == []
    assert _post(owner, f'/api/trackers/{tid}/test').get_json()['ok'] is True
    assert env['tested'][0]['password'] == 'new-pass-123'


# ------------------------------------------------------------------ idempotent, one-time previews

def test_reimport_skips_existing_and_preview_is_single_use(owner, env):
    p = _preview(owner)
    body = {'preview_id': p['preview_id'], 'keys': [3]}
    assert _post(owner, '/api/trackers/import/prowlarr', body).status_code == 201
    r = _post(owner, '/api/trackers/import/prowlarr', body)
    assert r.status_code == 410 and r.get_json()['error'] == tracker_import.EXPIRED
    p2 = _preview(owner)
    assert {r['key']: r['state'] for r in p2['indexers']}[3] == 'exists'
    r = _post(owner, '/api/trackers/import/prowlarr', {'preview_id': p2['preview_id'], 'keys': [3]})
    assert r.status_code == 200 and r.get_json()['created'] == []
    assert len([t for t in env['store'].list() if t['type'] == 'iptorrents']) == 1


def test_owner_added_tracker_counts_as_existing(owner, env):
    env['store'].create({'type': 'iptorrents', 'name': 'Mine', 'url': 'https://iptorrents.fake', 'cookie': 'c=1'})
    p = _preview(owner)
    assert {r['key']: r['state'] for r in p['indexers']}[3] == 'exists'


def test_preview_expires(owner, env, monkeypatch):
    p = _preview(owner)
    real = time.time
    monkeypatch.setattr(tracker_import.time, 'time', lambda: real() + tracker_import.PREVIEW_TTL_S + 1)
    r = _post(owner, '/api/trackers/import/prowlarr', {'preview_id': p['preview_id'], 'keys': [3]})
    assert r.status_code == 410 and env['store'].list() == []


def test_bad_bodies(owner, env):
    assert _post(owner, '/api/trackers/import/prowlarr', {'keys': [3]}).status_code == 400
    assert _post(owner, '/api/trackers/import/prowlarr', {'preview_id': 'x', 'keys': 'all'}).status_code == 400


# ------------------------------------------------------------------ guard counters folded, stricter wins

def _seed_prowlarr_guard(ix, now, sends=6, tokens=0.5, cooldown=None, streak=2):
    with ia._budget_state_guard:
        ia._guard_load()
        ia._guard_log[ix] = [[now - 60 * i, 'b'] for i in range(sends)]
        ia._guard_buckets[ix] = [tokens, now]
        if cooldown:
            ia._guard_cooldown[ix] = cooldown
        ia._guard_streak[ix] = streak


def test_guard_state_is_folded_taking_the_stricter_value(owner, env):
    now = time.time()
    _seed_prowlarr_guard(3, now, sends=6, tokens=0.5, cooldown=now + 7200, streak=3)
    g = env['guard']
    g._load()
    g.log['site:iptorrents'] = [[now - 30, 'i']]
    g.cooldown['site:iptorrents'] = (now + 600, 'http')
    g._save()
    p = _preview(owner)
    _post(owner, '/api/trackers/import/prowlarr', {'preview_id': p['preview_id'], 'keys': [3]})
    gk = 'site:iptorrents'
    assert len(g.log[gk]) == 7                     # both paths' sends count
    assert g.buckets[gk][0] <= 0.6                  # the emptier bucket
    assert g.cooldown[gk][0] == pytest.approx(now + 7200, abs=1)   # the later cooldown
    assert g.streak[gk] == 3
    saved = json.loads((env['dir'] / 'tracker_guard.json').read_text())
    assert len(saved['log'][gk]) == 7
    assert list(env['dir'].glob('tracker_guard.json.bak-import-*'))   # backed up first


def test_fold_keeps_native_when_it_is_stricter_and_is_idempotent(env):
    g, now = env['guard'], time.time()
    g._load()
    g.log['site:beyondhd'] = [[now - 10 * i, 'b'] for i in range(4)]
    g.buckets['site:beyondhd'] = [0.0, now]
    g.cooldown['site:beyondhd'] = (now + 9000, 'rate')
    g.streak['site:beyondhd'] = 5
    log = [[now - 10, 'b'], [now - 500, 'i']]       # one shared send, one new
    g.fold('site:beyondhd', log, [4.0, now], now + 100, 1, now)
    g.fold('site:beyondhd', log, [4.0, now], now + 100, 1, now)
    assert len(g.log['site:beyondhd']) == 5
    assert g.buckets['site:beyondhd'][0] < 0.01
    assert g.cooldown['site:beyondhd'][0] == now + 9000
    assert g.streak['site:beyondhd'] == 5


def test_prowlarr_wide_pause_carries_over(owner, env):
    now = time.time()
    with ia._budget_state_guard:
        ia._guard_load()
        ia._guard_cooldown[ia.FAIL_CLOSED] = now + 1800
    p = _preview(owner)
    _post(owner, '/api/trackers/import/prowlarr', {'preview_id': p['preview_id'], 'keys': [5]})
    assert env['guard'].cooldown['site:beyondhd'][0] == pytest.approx(now + 1800, abs=1)


def test_fold_never_raises_a_cap(env):
    before = dict(tracker_guard.LIMITS)
    env['guard'].fold('site:iptorrents', [], None, 0, 0)
    assert tracker_guard.LIMITS == before
