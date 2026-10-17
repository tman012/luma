"""New setup flow: Skip Plex (same network only), the 24 h setup window, the
first-account lock, and Settings > Plex (owner only) to connect Plex later."""

import threading
import time
import unittest
from unittest import mock

import tests.test_start_flow as tsf

LAN = {'REMOTE_ADDR': '192.168.1.57'}
ORIGIN = {'Origin': 'http://192.168.1.62:5002'}
HOST = '192.168.1.62:5002'
GOOD = {'username': 'taylor', 'password': 'correct horse'}


class _Base(unittest.TestCase):
    def setUp(self):
        tsf.StartFlowTest.setUp(self)
        self.sf._owner_pending.clear()

    tearDown = tsf.StartFlowTest.tearDown
    _fake_plex = tsf.StartFlowTest._fake_plex

    def skip(self, body=None, env=None, headers=None, host=HOST, client=None):
        h = dict(ORIGIN)
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        return (client or self.client).post('/api/start/skip', json=GOOD if body is None else body,
                                            environ_base=env or LAN, headers=h, base_url=f'http://{host}')


class SkipTest(_Base):
    def test_status_offers_skip_on_the_lan_only(self):
        st = self.client.get('/api/start/status', environ_base=LAN, base_url=f'http://{HOST}').json
        self.assertTrue(st['skip'])
        self.assertNotIn('closed', st)
        st = self.client.get('/api/start/status', environ_base={'REMOTE_ADDR': '203.0.113.9'},
                             base_url=f'http://{HOST}').json
        self.assertFalse(st['skip'])

    def test_skip_creates_the_owner_signed_in(self):
        r = self.skip()
        self.assertEqual(r.status_code, 200, r.json)
        users = self.ua.list_users()
        self.assertEqual([(u['name'], bool(u['is_admin'])) for u in users], [('taylor', True)])
        self.assertTrue(self.ua.authenticate('taylor', 'correct horse') if hasattr(self.ua, 'authenticate')
                        else self.ua.verify_password(users[0]['id'], 'correct horse'))
        cookies = r.headers.getlist('Set-Cookie')
        self.assertTrue(any(c.startswith('luma_auth_session=') and 'HttpOnly' in c for c in cookies))
        st = self.sf._load_state()
        self.assertEqual((st['owner_uid'], st['password_set'], st['plex_skipped']), (users[0]['id'], True, True))
        self.assertEqual(self.settings.get('security.auth_required'), 'disabled_local')
        self.assertEqual(self.saved, [])   # no Plex saved
        status = self.client.get('/api/start/status', environ_base=LAN, base_url=f'http://{HOST}').json
        self.assertEqual((status['needs_start'], status['in_progress'], status['password_set']), (False, True, True))
        # Once: a second Skip (or a Plex step) is refused.
        self.assertEqual(self.skip({'username': 'other', 'password': 'another pass'}).status_code, 409)
        self.assertEqual(self.client.post('/api/start/plex/pin', environ_base=LAN).status_code, 409)

    def test_minimal_build_signs_everyone_in(self):
        with mock.patch.dict('os.environ', {'LUMA_MINIMAL': '1'}):
            self.assertEqual(self.skip().status_code, 200)
        self.assertEqual(self.settings.get('security.auth_required'), 'enabled')

    def _refused(self, **kw):
        r = self.skip(**kw)
        self.assertEqual(r.status_code, 403, (kw, r.json))
        self.assertEqual(self.ua.list_users(), [])

    def test_refused_from_a_public_address(self):
        self._refused(env={'REMOTE_ADDR': '203.0.113.9'})

    def test_refused_when_relayed(self):
        for hdr in ({'X-Forwarded-For': '192.168.1.57'}, {'X-Real-IP': '192.168.1.57'},
                    {'Forwarded': 'for=192.168.1.57'}, {'Via': '1.1 caddy'}, {'X-Forwarded-Host': HOST},
                    {'True-Client-IP': '192.168.1.57'}):
            self._refused(headers=hdr)

    def test_refused_through_cloudflare(self):
        for hdr in ({'CF-Connecting-IP': '192.168.1.57'}, {'CF-Ray': 'abc'}, {'Cf-Visitor': '{}'},
                    {'CDN-Loop': 'cloudflare'}):
            self._refused(headers=hdr)

    def test_refused_for_a_foreign_host_name(self):
        # DNS rebinding: a public name pointed at the LAN address.
        self._refused(host='evil.example', headers={'Origin': 'http://evil.example'})

    def test_refused_cross_site(self):
        self._refused(headers={'Origin': 'http://evil.example'})
        self._refused(headers={'Origin': None})                              # no Origin or Referer
        self._refused(headers={'Sec-Fetch-Site': 'cross-site'})
        self._refused(headers={'Origin': None, 'Referer': 'http://evil.example/x'})

    def test_trusted_proxy_with_a_lan_client_is_allowed(self):
        self.settings.update({'security': {'trusted_proxies': '172.18.0.0/16'}})
        r = self.skip(env={'REMOTE_ADDR': '172.18.0.2'}, headers={'X-Forwarded-For': '192.168.1.57'})
        self.assertEqual(r.status_code, 200, r.json)

    def test_trusted_proxy_with_a_public_client_is_refused(self):
        self.settings.update({'security': {'trusted_proxies': '172.18.0.0/16'}})
        self._refused(env={'REMOTE_ADDR': '172.18.0.2'}, headers={'X-Forwarded-For': '203.0.113.9'})

    def test_window_closes_after_24_hours(self):
        with mock.patch.object(self.sf, '_boot', time.monotonic() - self.sf.SETUP_WINDOW - 1):
            st = self.client.get('/api/start/status', environ_base=LAN, base_url=f'http://{HOST}').json
            self.assertTrue(st['closed'])
            r = self.skip()
            self.assertEqual(r.status_code, 403)
            self.assertTrue(r.json['closed'])
            self.assertEqual(self.client.post('/api/start/plex/pin', environ_base=LAN).status_code, 403)
        self.assertEqual(self.ua.list_users(), [])

    def test_bad_input_creates_nothing(self):
        for body, msg in (({'username': '', 'password': 'correct horse'}, 'Enter a username'),
                          ({'username': 'a@b', 'password': 'correct horse'}, "Username can't contain @"),
                          ({'username': 'taylor', 'password': 'short'}, 'Use at least 8 characters'),
                          ({'username': 'taylor', 'password': ' leading space'}, None),
                          ({'username': 'taylor'}, 'Enter a password'), ([], 'Enter a username')):
            r = self.skip(body)
            self.assertEqual(r.status_code, 400, body)
            if msg:
                self.assertEqual(r.json['error'], msg)
        self.assertEqual(self.ua.list_users(), [])

    def test_racing_skips_make_one_owner(self):
        results = []
        barrier = threading.Barrier(6)

        def go(i):
            c = self.app.test_client()
            barrier.wait()
            results.append(self.skip({'username': f'user{i}', 'password': 'correct horse'}, client=c).status_code)
        threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200] + [409] * 5)
        self.assertEqual(len(self.ua.list_users()), 1)


class OwnerClosesSetupForGoodTest(_Base):
    def _restart(self):
        """What a container restart resets: process memory and the window clock."""
        self.sf._pending.clear()
        self.sf._owner_pending.clear()
        self.sf._boot = time.monotonic()
        self.sf.clear_stale_claim()

    def _all_refused(self, c):
        st = c.get('/api/start/status', environ_base=LAN, base_url=f'http://{HOST}').json
        self.assertFalse(st['needs_start'])
        self.assertNotIn('skip', st)
        self.assertNotIn('closed', st)
        with self._fake_plex():
            for method, path, body in (('post', '/api/start/skip', {'username': 'intruder', 'password': 'correct horse'}),
                                       ('post', '/api/start/plex/pin', {}),
                                       ('get', '/api/start/plex/pin', None),
                                       ('post', '/api/start/plex/server', {'machine_id': 'm1'})):
                kw = dict(environ_base=LAN, base_url=f'http://{HOST}', headers=ORIGIN)
                r = getattr(c, method)(path, json=body, **kw) if body is not None else getattr(c, method)(path, **kw)
                self.assertEqual(r.status_code, 409, (path, r.json))
        self.assertEqual([u['name'] for u in self.ua.list_users()], ['taylor'])
        self.assertEqual(self.saved, [])

    def test_restart_with_an_owner_keeps_setup_closed(self):
        self.assertEqual(self.skip().status_code, 200)
        for _ in range(3):                      # any number of restarts
            self._restart()
            self.assertTrue(self.sf.setup_window_open())   # the window clock did reset...
            self._all_refused(self.app.test_client())       # ...but an owner exists: closed for good
        # Even a browser holding a stale start cookie from before the restart.
        c = self.app.test_client()
        c.set_cookie(self.sf.START_COOKIE, 'old-nonce')
        self._all_refused(c)

    def test_restart_with_a_plex_owner_keeps_setup_closed(self):
        c = self.client
        with self._fake_plex():
            c.post('/api/start/plex/pin', environ_base=LAN, base_url=f'http://{HOST}')
            c.get('/api/start/plex/pin', environ_base=LAN, base_url=f'http://{HOST}')
        with mock.patch.object(self.sf, 'probe', return_value=True):
            self.assertEqual(c.post('/api/start/plex/server', json={'machine_id': 'm1'}, environ_base=LAN,
                                    base_url=f'http://{HOST}').status_code, 200)
        self.saved.clear()
        self.ua.set_name_and_password(self.ua.list_users()[0]['id'], 'taylor', 'correct horse') \
            if hasattr(self.ua, 'set_name_and_password') else None
        self._restart()
        self._all_refused(self.app.test_client())

    def test_restart_with_no_owner_reopens_the_window(self):
        with mock.patch.object(self.sf, '_boot', time.monotonic() - self.sf.SETUP_WINDOW - 1):
            self.assertEqual(self.skip().status_code, 403)
        self._restart()
        self.assertEqual(self.skip().status_code, 200)

    def test_skip_claim_is_logged_with_the_client_ip(self):
        with self.assertLogs('app.start_flow', level='INFO') as logs:
            self.assertEqual(self.skip().status_code, 200)
        line = next(l for l in logs.output if 'Plex skipped' in l)
        self.assertIn('INFO', line)
        self.assertIn('192.168.1.57', line)


class SettingsPlexTest(_Base):
    def _owner(self):
        r = self.skip()
        self.assertEqual(r.status_code, 200)
        c = self.app.test_client()
        uid = self.ua.list_users()[0]['id']
        c.set_cookie('luma_auth_session', self.ua.create_session(uid))
        return c

    def test_owner_connects_plex_later(self):
        c = self._owner()
        self.assertEqual(c.get('/api/settings/plex').json, {'connected': False, 'server': ''})
        self.assertEqual(c.get('/api/settings/plex/pin').json, {'status': 'none'})
        with self._fake_plex(authorized=False):
            r = c.post('/api/settings/plex/pin', json={'mode': 'redirect'}, headers=ORIGIN, base_url=f'http://{HOST}')
            self.assertEqual(r.status_code, 200, r.json)
            self.assertIn('code=abcd', r.json['auth_url'])
            self.assertIn('settings%2Fplex', r.json['auth_url'])   # same-tab sign-in returns to Settings > Plex
            self.assertEqual(c.get('/api/settings/plex/pin').json['status'], 'waiting')
        with self._fake_plex():
            r = c.get('/api/settings/plex/pin').json
        self.assertEqual(r, {'status': 'authorized', 'servers': [{'machine_id': 'm1', 'name': 'Basement'}]})
        self.assertNotIn('token', str(r))
        with mock.patch.object(self.sf, 'probe', side_effect=lambda uri, tok, mid: '192-0-2-10' in uri):
            r = c.post('/api/settings/plex/server', json={'machine_id': 'm1'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['server'], 'Basement')
        self.assertEqual(self.saved, [('https://192-0-2-10.example.plex.direct:32400', 'srv-token', 'm1')])
        self.assertEqual(c.get('/api/settings/plex').json, {'connected': True, 'server': 'Basement'})
        self.assertNotIn('plex_skipped', self.sf._load_state())
        # The pending sign-in is used up.
        self.assertEqual(c.post('/api/settings/plex/server', json={'machine_id': 'm1'}).status_code, 409)

    def test_unknown_server_is_refused(self):
        c = self._owner()
        with self._fake_plex():
            c.post('/api/settings/plex/pin', json={})
            c.get('/api/settings/plex/pin')
        self.assertEqual(c.post('/api/settings/plex/server', json={'machine_id': 'f1'}).status_code, 409)
        self.assertEqual(self.saved, [])

    def test_owner_only(self):
        self._owner()
        admin = self.ua.create_user('admin2', 'another pass', is_admin=True, status='active')
        viewer = self.ua.create_user('viewer', 'another pass', is_admin=False, status='active')
        for u in (admin, viewer):
            c = self.app.test_client()
            c.set_cookie('luma_auth_session', self.ua.create_session(u['id']))
            for method, path in (('get', '/api/settings/plex'), ('post', '/api/settings/plex/pin'),
                                 ('get', '/api/settings/plex/pin'), ('post', '/api/settings/plex/server')):
                self.assertEqual(getattr(c, method)(path, json={} if method == 'post' else None).status_code, 403,
                                 (u['name'], path))
        anon = self.app.test_client()
        self.assertEqual(anon.get('/api/settings/plex', environ_base={'REMOTE_ADDR': '203.0.113.9'}).status_code, 401)

    def test_sign_in_expires_after_15_minutes(self):
        c = self._owner()
        with self._fake_plex():
            c.post('/api/settings/plex/pin', json={})
            c.get('/api/settings/plex/pin')
        uid = self.ua.list_users()[0]['id']
        self.sf._owner_pending[uid]['created'] -= 16 * 60
        self.assertEqual(c.post('/api/settings/plex/server', json={'machine_id': 'm1'}).status_code, 409)
        self.assertEqual(c.get('/api/settings/plex/pin').json, {'status': 'expired'})
        self.assertEqual(self.saved, [])

    def test_pending_sign_in_is_per_owner_session_state(self):
        # Another (non-owner) browser can't pick a server from the owner's sign-in.
        c = self._owner()
        with self._fake_plex():
            c.post('/api/settings/plex/pin', json={})
            c.get('/api/settings/plex/pin')
        viewer = self.ua.create_user('viewer', 'another pass', is_admin=False, status='active')
        v = self.app.test_client()
        v.set_cookie('luma_auth_session', self.ua.create_session(viewer['id']))
        self.assertEqual(v.post('/api/settings/plex/server', json={'machine_id': 'm1'}).status_code, 403)
        self.assertEqual(self.saved, [])


def test_minimal_mode_paths():
    from app import minimal
    for p in ('/settings/plex', '/api/settings/plex', '/api/settings/plex/pin', '/api/settings/plex/server',
              '/api/start/skip'):
        assert minimal.path_allowed(p, 'GET'), p
    assert '/api/settings/plex' not in minimal.SIGNED_OUT_PATHS
