"""Start flow (Welcome → Plex → Access → Done), access modes and sign-in limits."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from flask import Flask, g, jsonify, request


class _Settings:
    def __init__(self):
        self.data = {}

    def get(self, path, default=None):
        cur = self.data
        for part in path.split('.'):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def update(self, patch):
        def merge(dst, src):
            for k, v in src.items():
                if isinstance(v, dict):
                    merge(dst.setdefault(k, {}), v)
                else:
                    dst[k] = v
        merge(self.data, patch)


RESOURCES = [
    {'name': 'Friend', 'provides': 'server', 'owned': False, 'clientIdentifier': 'f1', 'connections': []},
    {'name': 'Phone', 'provides': 'client,player', 'owned': True, 'clientIdentifier': 'p1', 'connections': []},
    {'name': 'Basement', 'provides': 'server', 'owned': True, 'presence': True, 'clientIdentifier': 'm1',
     'accessToken': 'srv-token',
     'connections': [
         {'uri': 'https://relay.example.plex.direct:8443', 'local': False, 'relay': True, 'protocol': 'https'},
         {'uri': 'https://203-0-113-5.example.plex.direct:32400', 'local': False, 'relay': False, 'protocol': 'https'},
         {'uri': 'http://192.0.2.10:32400', 'local': True, 'relay': False, 'protocol': 'http'},
         {'uri': 'https://192-0-2-10.example.plex.direct:32400', 'local': True, 'relay': False, 'protocol': 'https'},
     ]},
]


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data
        self.ok = 200 <= status < 300

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


class StartFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            'LUMA_STATE_DIR': self.tmp.name, 'DATABASE_PATH': os.path.join(self.tmp.name, 'media.db'),
            'LUMA_NO_RESTART': '1',
        })
        self.env.start()
        from app import user_auth, start_flow, access, first_run
        self.ua, self.sf, self.access = user_auth, start_flow, access
        self.assertEqual(str(first_run.data_dir()), self.tmp.name)
        user_auth.init_db()
        self.settings = _Settings()
        self.saved = []
        app = Flask(__name__)

        @app.before_request
        def load():
            tok = request.cookies.get('luma_session')
            g.current_user = user_auth.validate_session(tok) if tok else None
            if g.current_user is None and not access.needs_pin(self.settings, request):
                o = access.owner(user_auth)
                if o:
                    g.current_user = dict(o)

        @app.route('/whoami')
        def whoami():
            u = g.current_user
            return jsonify({'user': u and u['name']})

        def saved(uri, token, mid):
            self.saved.append((uri, token, mid))
            self.settings.update({'plex': {'url': uri, 'token': token, 'machine_id': mid}})
            return 3

        start_flow._pending.clear()
        start_flow.init(settings=self.settings, user_auth=user_auth, session_cookie='luma_session',
                        client_id='luma-test', on_plex_saved=saved,
                        get_current_user=lambda: getattr(g, 'current_user', None))
        app.register_blueprint(start_flow.bp)
        self.app = app
        self.client = app.test_client()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    # -- pure helpers -------------------------------------------------------

    def test_owned_servers_and_connection_order(self):
        servers = self.sf.owned_servers(RESOURCES)
        self.assertEqual([s['name'] for s in servers], ['Basement'])
        order = [c['uri'] for c in self.sf.ordered_connections(RESOURCES[2]['connections'])]
        self.assertEqual(order[0], 'https://192-0-2-10.example.plex.direct:32400')   # local https
        self.assertEqual(order[1], 'http://192.0.2.10:32400')                        # local http
        self.assertEqual(order[2], 'https://203-0-113-5.example.plex.direct:32400')  # remote
        self.assertTrue(order[3].startswith('https://relay'))                        # relay last

    def test_pick_connection_falls_back(self):
        server = self.sf.owned_servers(RESOURCES)[0]
        tried = []

        def tester(uri, token, mid):
            tried.append(uri)
            return 'relay' in uri
        conn = self.sf.pick_connection(server, 'tok', tester=tester)
        self.assertEqual(conn['kind'], 'relay')
        self.assertEqual(len(tried), 4)
        self.assertIsNone(self.sf.pick_connection(server, 'tok', tester=lambda *a: False))

    # -- the whole flow -----------------------------------------------------

    def _fake_plex(self, authorized=True):
        def post(url, **kw):
            self.assertIn('/api/v2/pins', url)
            self.assertEqual(kw['headers']['X-Plex-Client-Identifier'], 'luma-test')
            self.assertEqual(kw['headers']['X-Plex-Product'], 'Luma')
            return _Resp(201, {'id': 42, 'code': 'abcd'})

        def get(url, **kw):
            if '/api/v2/pins/42' in url:
                return _Resp(200, {'authToken': 'user-token' if authorized else None})
            if '/api/v2/user' in url:
                return _Resp(200, {'username': 'plexowner', 'title': 'Plex Owner', 'email': 'owner@example.com'})
            if '/api/v2/resources' in url:
                return _Resp(200, RESOURCES)
            raise AssertionError(url)
        return mock.patch.multiple('app.start_flow.requests', post=post, get=get)

    def test_full_flow(self):
        c = self.client
        self.assertEqual(c.get('/api/start/status').json, {'needs_start': True, 'in_progress': False, 'step': 'welcome'})
        with self._fake_plex(authorized=False):
            r = c.post('/api/start/plex/pin')
            self.assertEqual(r.status_code, 200)
            self.assertIn('app.plex.tv/auth#?', r.json['auth_url'])
            self.assertIn('clientID=luma-test', r.json['auth_url'])
            self.assertIn('code=abcd', r.json['auth_url'])
            self.assertEqual(c.get('/api/start/plex/pin').json['status'], 'waiting')
        with self._fake_plex():
            r = c.get('/api/start/plex/pin').json
        self.assertEqual(r['status'], 'authorized')
        self.assertEqual(r['servers'], [{'machine_id': 'm1', 'name': 'Basement'}])
        self.assertNotIn('user-token', str(r))

        with mock.patch.object(self.sf, 'probe', side_effect=lambda uri, tok, mid: '192-0-2-10' in uri):
            r = c.post('/api/start/plex/server', json={'machine_id': 'm1'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['connection'], 'local')
        self.assertEqual(self.saved, [('https://192-0-2-10.example.plex.direct:32400', 'srv-token', 'm1')])
        users = self.ua.list_users()
        self.assertEqual(len(users), 1)
        self.assertTrue(users[0]['is_admin'])
        self.assertEqual(users[0]['email'], 'owner@example.com')
        self.assertEqual(self.settings.get('security.access_mode'), 'pin_remote')

        st = c.get('/api/start/status').json
        self.assertEqual((st['needs_start'], st['in_progress'], st['step']), (False, True, 'done'))
        # Plex steps refuse once an account exists.
        self.assertEqual(c.post('/api/start/plex/pin').status_code, 409)
        self.assertEqual(c.post('/api/start/plex/server', json={'machine_id': 'm1'}).status_code, 409)
        self.assertTrue(self.sf.created_by_start())

        r = c.post('/api/start/finish')
        self.assertEqual(r.json, {'ok': True, 'restarting': False})
        st = c.get('/api/start/status').json
        self.assertEqual((st['needs_start'], st['in_progress']), (False, False))

    def test_other_browser_cannot_hijack(self):
        with self._fake_plex():
            self.client.post('/api/start/plex/pin')
            other = self.app.test_client()
            self.assertEqual(other.get('/api/start/plex/pin').status_code, 409)
            self.assertEqual(other.post('/api/start/plex/server', json={'machine_id': 'm1'}).status_code, 409)

    def test_existing_install_never_sees_start(self):
        self.ua.create_user('someone', '1234', is_admin=True)
        st = self.client.get('/api/start/status').json
        self.assertFalse(st['needs_start'])
        self.assertFalse(st['in_progress'])
        self.assertEqual(self.client.post('/api/start/plex/pin').status_code, 409)

    # -- access modes -------------------------------------------------------

    def _who(self, mode, remote_addr, headers=None, proxies=''):
        self.settings.update({'security': {'access_mode': mode, 'trusted_proxies': proxies}})
        r = self.client.get('/whoami', headers=headers or {}, environ_base={'REMOTE_ADDR': remote_addr})
        return r.json['user']

    def test_access_modes(self):
        self.ua.create_user('owner', '1234', is_admin=True)
        # Open mode: remote visitors need sign-in (no implicit owner)
        self.assertIsNone(self._who('open', '203.0.113.9'))
        # Open mode: local visitors get implicit owner
        self.assertEqual(self._who('open', '10.20.0.5'), 'owner')
        # Pin mode: everyone needs sign-in
        self.assertIsNone(self._who('pin', '10.20.0.5'))
        # Pin-remote mode: local gets owner, remote needs sign-in
        self.assertEqual(self._who('pin_remote', '10.20.0.5'), 'owner')
        self.assertEqual(self._who('pin_remote', '10.20.0.6'), 'owner')
        self.assertIsNone(self._who('pin_remote', '203.0.113.9'))
        # Relayed by a proxy that isn't trusted: treated as remote, whatever the headers claim.
        self.assertIsNone(self._who('pin_remote', '127.0.0.1', {'X-Forwarded-For': '10.20.0.5'}))
        self.assertIsNone(self._who('pin_remote', '172.21.0.1', {'CF-Connecting-IP': '10.20.0.5'}))
        # Trusted proxy: the forwarded client address decides.
        self.assertEqual(self._who('pin_remote', '172.20.0.2', {'X-Forwarded-For': '10.20.0.5'}, '172.20.0.0/16'), 'owner')
        self.assertIsNone(self._who('pin_remote', '172.20.0.2', {'X-Forwarded-For': '10.20.0.5, 198.51.100.7'}, '172.20.0.0/16'))
        self.assertIsNone(self._who('pin_remote', '172.20.0.2', {'CF-Connecting-IP': '198.51.100.7'}, '172.20.0.0/16'))

    def test_unset_mode_requires_pin(self):
        self.ua.create_user('owner', '1234', is_admin=True)
        s = _Settings()
        self.assertEqual(self.access.mode(s), 'pin')
        req = SimpleNamespace(remote_addr='10.20.0.5', headers={})
        self.assertTrue(self.access.needs_pin(s, req))

    def test_no_users_no_open_access(self):
        self.assertIsNone(self._who('open', '10.20.0.5'))


class LoginLimiterTest(unittest.TestCase):
    def setUp(self):
        from app import login_limiter
        self.ll = login_limiter
        login_limiter._fail.clear()

    def test_locks_after_five_failures(self):
        for _ in range(5):
            self.assertEqual(self.ll.retry_after('198.51.100.7', 'a@example.com'), 0)
            self.ll.record_failure('198.51.100.7', 'a@example.com')
        self.assertGreater(self.ll.retry_after('198.51.100.7', 'a@example.com'), 0)
        # Same account from elsewhere is locked too; other accounts from that address too.
        self.assertGreater(self.ll.retry_after('198.51.100.8', 'a@example.com'), 0)
        self.assertGreater(self.ll.retry_after('198.51.100.7', 'b@example.com'), 0)
        self.assertEqual(self.ll.retry_after('198.51.100.8', 'b@example.com'), 0)

    def test_proxy_address_not_shared(self):
        for _ in range(5):
            self.ll.record_failure('127.0.0.1', 'a@example.com')
        self.assertEqual(self.ll.retry_after('127.0.0.1', 'b@example.com'), 0)

    def test_success_clears(self):
        for _ in range(4):
            self.ll.record_failure('198.51.100.7', 'a@example.com')
        self.ll.record_success('198.51.100.7', 'a@example.com')
        self.ll.record_failure('198.51.100.7', 'a@example.com')
        self.assertEqual(self.ll.retry_after('198.51.100.7', 'a@example.com'), 0)


if __name__ == '__main__':
    unittest.main()
