"""Request settings card: share link and Recent Requests (port list item 10)."""

from tests.frontend_ts import FRONTEND, needs_ts, run_ts


@needs_ts
def test_link_uses_public_url_or_base_aware_fallback():
    out = run_ts('''
import { requestLink } from '@src/pages/settings/requestPortalLog';
(globalThis as any).window = {};
const a = requestLink('https://req.example.com/', 'http://srv:5001');
const b = requestLink('', 'http://srv:5001');
(globalThis as any).window.__LUMA_URL_BASE__ = '/luma';
const c = requestLink(undefined, 'http://srv:5001');
console.log(JSON.stringify([a, b, c]));
''')
    assert out == ['https://req.example.com/', 'http://srv:5001/request', 'http://srv:5001/luma/request']


@needs_ts
def test_recent_requests_last_twelve_and_labels():
    out = run_ts('''
import { describeRequest, recentRequests } from '@src/pages/settings/requestPortalLog';
const rows = Array.from({ length: 20 }, (_, i) => ({ id: String(i), ts: 0, who: 'Sam', kind: 'movie', title: 'M' + i, scope: '', action: 'add' }));
const list = recentRequests(rows);
const labels = [
  describeRequest({ id: 'a', ts: 0, who: 'x', kind: 'movie', title: 'Heat', scope: '', action: '' }),
  describeRequest({ id: 'b', ts: 0, who: 'x', kind: 'tv', title: 'Lost', scope: 'show', action: '' }),
  describeRequest({ id: 'c', ts: 0, who: 'x', kind: 'tv', title: 'Lost', scope: 'season', season: 2, action: '' }),
  describeRequest({ id: 'd', ts: 0, who: 'x', kind: 'tv', title: 'Lost', scope: 'episodes', season: 1, episodes: [3, 4], action: '' }),
];
console.log(JSON.stringify({ n: list.length, first: list[0].title, bad: recentRequests({}).length, labels }));
''')
    assert out == {'n': 12, 'first': 'M0', 'bad': 0,
                   'labels': ['Heat', 'Lost · All Seasons', 'Lost · Season 2', 'Lost · S1 E3, E4']}


def test_card_wires_log_link_and_api_client():
    src = (FRONTEND / 'src' / 'pages' / 'settings' / 'SecurityTab.tsx').read_text()
    assert "apiFetch('/api/request/admin/log')" in src
    assert 'Recent Requests' in src and 'navigator.clipboard.writeText(link)' in src
    # Editing a field other than the password must keep Save enabled.
    assert "setDirty(password !== '' || confirmPassword !== '')" not in src


def test_admin_log_and_public_url(test_app):
    import app.main
    import app.user_auth as user_auth
    user_auth.create_user('owner', '1234', is_admin=True)
    c = test_app.test_client()
    lan = {'REMOTE_ADDR': '192.168.1.20'}
    assert 'public_url' in c.get('/api/request/admin/settings', environ_base=lan).get_json()
    assert isinstance(c.get('/api/request/admin/log', environ_base=lan).get_json(), list)
    assert c.get('/api/request/admin/log', environ_base={'REMOTE_ADDR': '203.0.113.5'}).status_code == 401
