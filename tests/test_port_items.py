"""Live-install behavior ported into Luma (port list items 5 to 8 and 11 to 13)."""

import json
from types import SimpleNamespace

import pytest

import app.main
import app.user_auth as user_auth
from app import plex_match, settings_manager as sm, sonarr_status

LAN = {'REMOTE_ADDR': '192.168.1.20'}
REMOTE = {'REMOTE_ADDR': '203.0.113.7'}


# --- 5: Sonarr episode monitoring sticks ----------------------------------

class FakeSonarr:
    """Minimal Sonarr: season toggles cascade to every episode, like the real one."""

    def __init__(self, keep=True):
        self.keep = keep
        self.series = {'id': 1, 'monitored': True,
                       'seasons': [{'seasonNumber': 1, 'monitored': False},
                                   {'seasonNumber': 2, 'monitored': True}]}
        self.eps = {i: {'id': i, 'seasonNumber': 1, 'monitored': False} for i in (11, 12, 13)}
        self.eps[21] = {'id': 21, 'seasonNumber': 2, 'monitored': True}
        self.commands = []

    def _request(self, method, endpoint, body=None, timeout=None):
        if method == 'GET' and endpoint == 'series/1':
            return json.loads(json.dumps(self.series))
        if method == 'GET' and endpoint == 'episode':
            return [dict(e) for e in self.eps.values()]
        if method == 'POST' and endpoint == 'command':
            self.commands.append(body)
            return {'id': 99}
        if method == 'PUT' and endpoint == 'series/1':
            for new in body['seasons']:
                old = next(s for s in self.series['seasons'] if s['seasonNumber'] == new['seasonNumber'])
                if new['monitored'] and not old['monitored']:
                    for e in self.eps.values():
                        if e['seasonNumber'] == new['seasonNumber']:
                            e['monitored'] = True
                old['monitored'] = new['monitored']
            return body
        if method == 'PUT' and endpoint == 'episode/monitor':
            if self.keep:
                for i in body['episodeIds']:
                    self.eps[i]['monitored'] = body['monitored']
            return [{}]
        raise AssertionError((method, endpoint))


@pytest.fixture
def sonarr(monkeypatch):
    s = FakeSonarr()

    def put(sonarr_, endpoint, body):
        return sonarr_._request('PUT', endpoint, body) is not None
    monkeypatch.setattr(sonarr_status, '_put', put)
    monkeypatch.setattr(sonarr_status.time, 'sleep', lambda s: None)
    return s


def test_episode_monitor_turns_season_on_without_cascade(sonarr):
    sonarr_status.set_episodes_monitored(sonarr, 1, [12], True)
    assert next(s for s in sonarr.series['seasons'] if s['seasonNumber'] == 1)['monitored']
    assert sonarr.eps[12]['monitored'] is True
    assert sonarr.eps[11]['monitored'] is False and sonarr.eps[13]['monitored'] is False
    assert sonarr.commands == [{'name': 'EpisodeSearch', 'episodeIds': [12]}]


def test_episode_monitor_read_back_failure_raises(sonarr):
    sonarr.keep = False   # Sonarr ignores the episode update
    sonarr.series['seasons'][0]['monitored'] = True
    with pytest.raises(sonarr_status.MonitorError):
        sonarr_status.set_episodes_monitored(sonarr, 1, [12], True)
    assert sonarr.commands == []


def test_episode_search_retried(sonarr, monkeypatch):
    answers = iter([None, 7])
    monkeypatch.setattr(sonarr_status, '_search', lambda s, body: next(answers))
    out = sonarr_status.set_episodes_monitored(sonarr, 1, [12], True)
    assert out['commandId'] == 7 and out['searching'] is True


# --- 6: Fix Match reliability --------------------------------------------

def _item(guid, title, tmdb, updated=1):
    return {'guid': guid, 'title': title, 'year': 2000, 'updated_at': updated, 'tmdb_id': tmdb, 'type': 'movie'}


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(plex_match.time, 'time', lambda: now[0])
    monkeypatch.setattr(plex_match.time, 'sleep', lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(plex_match, '_request', lambda *a, **k: None)
    return now


def test_wait_keeps_polling_until_tmdb_id_changes(clock, monkeypatch):
    before = _item('plex://old', 'Old', '1')
    seq = iter([_item('plex://new', 'New', '1', 2), _item('plex://new', 'New', '1', 2),
                _item('plex://new', 'New', '2', 2)])
    monkeypatch.setattr(plex_match, 'get_item', lambda plex, rk: next(seq, _item('plex://new', 'New', '2', 2)))
    got = plex_match._wait_refreshed(None, 5, before, 'plex://new', timeout=30.0)
    assert got['tmdb_id'] == '2'


def test_apply_reports_502_when_plex_never_switches(clock, monkeypatch):
    monkeypatch.setattr(plex_match, 'get_item', lambda plex, rk: _item('plex://old', 'Old', '1'))
    with pytest.raises(plex_match.MatchError) as e:
        plex_match.apply(None, 5, 'plex://new', 'New', 2000)
    assert e.value.status == 502
    assert clock[0] - 1000.0 >= 30.0   # waited the full 30 s


def test_fix_match_followup_rechecks_twice(monkeypatch):
    sleeps, invalidated = [], []

    class T:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(app.main.threading, 'Thread', T)
    monkeypatch.setattr(app.main.time, 'sleep', sleeps.append)
    monkeypatch.setattr(plex_match, 'get_item', lambda plex, rk: _item('g', 'T', '9'))
    monkeypatch.setattr(app.main, '_fix_match_invalidate', lambda p, rk, items: invalidated.append(items))
    app.main._fix_match_followup(None, 5, {'before': {}, 'after': {}})
    assert sleeps == [10, 45] and len(invalidated) == 2


# --- 7: poster freshness ---------------------------------------------------

class _ImgResp:
    status_code = 200
    content = b'poster-bytes'
    headers = {'Content-Type': 'image/jpeg'}


@pytest.fixture
def img(test_app, monkeypatch):
    monkeypatch.setattr(app.main, 'get_plex_for_request', lambda: SimpleNamespace(plex_url='http://plex.test', plex_token='t'))
    monkeypatch.setattr(app.main.requests, 'get', lambda *a, **k: _ImgResp())
    return test_app.test_client()


def test_unversioned_poster_revalidates(img):
    r = img.get('/api/img/plex?url=/library/metadata/5/thumb&r=2', environ_base=LAN)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-cache'
    etag = r.headers['ETag']
    r2 = img.get('/api/img/plex?url=/library/metadata/5/thumb&r=2', headers={'If-None-Match': etag}, environ_base=LAN)
    assert r2.status_code == 304


def test_versioned_poster_cached_a_day(img):
    r = img.get('/api/img/plex?url=/library/metadata/5/thumb/1700000000', environ_base=LAN)
    assert r.headers['Cache-Control'] == 'public, max-age=86400'
    assert 'ETag' not in r.headers


def test_unversioned_poster_urls_carry_cache_buster():
    import inspect
    import re
    from app import cache_updater, hero_cache
    src = inspect.getsource(cache_updater)
    assert '/thumb"' not in src.replace('/thumb&r=2"', '')
    # hero posters are cached as local files; any proxy URL still carries the buster
    src = inspect.getsource(hero_cache)
    assert not re.search(r'/api/img/plex\?url=[^"]*/thumb"', src)


# --- 8: request-page grab attribution -------------------------------------

def test_record_origin_uses_request_page_evidence(test_app, monkeypatch):
    from app import grab_origin
    seen = {}
    monkeypatch.setattr(grab_origin, 'record', lambda origin, **kw: seen.update(origin=origin, **kw))
    with test_app.test_request_context('/'):
        app.main.g.origin_evidence = 'request page: Sam'
        app.main._record_origin('manual', info_hash='abc')
    assert seen['evidence'] == 'request page: Sam'


def test_record_origin_explicit_evidence_wins(test_app, monkeypatch):
    from app import grab_origin
    seen = {}
    monkeypatch.setattr(grab_origin, 'record', lambda origin, **kw: seen.update(kw))
    with test_app.test_request_context('/'):
        app.main.g.origin_evidence = 'request page: Sam'
        app.main._record_origin('manual', evidence='mine')
    assert seen['evidence'] == 'mine'


# --- 11 and 13: legacy defaults on upgraded installs -----------------------

def _load(tmp_path, data):
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps(data))
    return sm.SettingsManager(str(p))


def test_upgraded_install_keeps_hidden_libraries(tmp_path):
    s = _load(tmp_path, {'requests': {'enabled': True}, 'plex': {'url': 'http://plex.test'}})
    assert s.get('request_portal.exclude_libraries') == sm.LEGACY_REQUEST_EXCLUDES


def test_upgraded_install_empty_list_gets_old_default(tmp_path):
    s = _load(tmp_path, {'requests': {'exclude_libraries': []}})
    assert s.get('request_portal.exclude_libraries') == sm.LEGACY_REQUEST_EXCLUDES


def test_upgraded_install_without_portal_section(tmp_path):
    s = _load(tmp_path, {'plex': {'url': 'http://plex.test'}})
    assert s.get('request_portal.exclude_libraries') == sm.LEGACY_REQUEST_EXCLUDES


def test_explicit_hidden_libraries_kept(tmp_path):
    s = _load(tmp_path, {'requests': {'exclude_libraries': ['Kids']}})
    assert s.get('request_portal.exclude_libraries') == ['Kids']


def test_cleared_list_after_upgrade_stays_cleared(tmp_path):
    s = _load(tmp_path, {'requests': {}})
    s.update({'request_portal': {'exclude_libraries': []}})
    again = sm.SettingsManager(str(tmp_path / 'settings.json'))
    assert again.get('request_portal.exclude_libraries') == []


def test_fresh_install_defaults(tmp_path):
    s = sm.SettingsManager(str(tmp_path / 'settings.json'))
    assert s.get('request_portal.exclude_libraries') == []
    assert s.get('qbittorrent.app_category') == 'luma'


def test_upgraded_install_keeps_qbit_category(tmp_path):
    s = _load(tmp_path, {'qbittorrent': {'url': 'http://qbit.test'}})
    assert s.get('qbittorrent.app_category') == 'media-search-app'


def test_explicit_qbit_category_kept(tmp_path):
    s = _load(tmp_path, {'qbittorrent': {'app_category': 'movies'}})
    assert s.get('qbittorrent.app_category') == 'movies'


def test_ai_settings_survive_upgrade(tmp_path):
    s = _load(tmp_path, {'apis': {'openai': {'enabled': True, 'api_key': 'sk-test'}, 'alfred': {'enabled': True}},
                         'ui': {'vibe_engine': {'newer_than_years': 3}}})
    assert s.get('apis.openai.api_key') == 'sk-test'
    assert s.get('apis.alfred.enabled') is True
    assert s.get('ui.vibe_engine.newer_than_years') == 3


# --- 12: signed Telegram links and /api/version work signed-out -----------

@pytest.fixture
def remote(test_app, monkeypatch):
    user_auth.create_user('owner', '1234', is_admin=True)
    monkeypatch.setattr(app.main, '_TELEGRAM_ACTION_SECRET', 'test-secret')
    return test_app.test_client()


def _signed(action, uid):
    import hashlib
    import hmac
    sig = hmac.new(b'test-secret', f'{action}:{uid}'.encode(), hashlib.sha256).hexdigest()[:16]
    return f'/api/admin/telegram-action?action={action}&uid={uid}&sig={sig}'


def test_signed_telegram_link_skips_sign_in(remote):
    r = remote.get(_signed('approve', 424242), environ_base=REMOTE)
    assert r.status_code != 401


def test_unsigned_or_forged_telegram_link_needs_sign_in(remote):
    for url in ('/api/admin/telegram-action?action=approve&uid=1&sig=deadbeefdeadbeef',
                '/api/admin/telegram-action?action=approve&uid=1',
                '/api/admin/telegram-action'):
        assert remote.get(url, environ_base=REMOTE).status_code == 401


def test_telegram_link_needs_secret(remote, monkeypatch):
    url = _signed('approve', 1)
    monkeypatch.setattr(app.main, '_TELEGRAM_ACTION_SECRET', '')
    assert remote.get(url, environ_base=REMOTE).status_code == 401


def test_version_open_remotely(remote):
    assert remote.get('/api/version', environ_base=REMOTE).status_code == 200


def test_other_admin_routes_still_need_sign_in(remote):
    assert remote.get('/api/admin/users', environ_base=REMOTE).status_code == 401
