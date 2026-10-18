"""Settings > Connectors > TheTVDB: masked key and PIN (GET says only 'set'),
owner-only changes, Test logs in to v4 (mocked), and the Calendar air times
from TVDB: the network's zone (DST, a non-US network), no key or no match
falls back, the token refreshes on 401, and nothing secret is logged."""
import datetime as dt
import logging

import pytest

import app.main
from app import tv_airtimes, tv_calendar, tv_tvdb
from tests.test_minimal_search import REMOTE, _client, minimal  # noqa: F401
from tests.test_tv_airtimes import Maze, SEV, _eps, write
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_file_management import store  # noqa: F401

KEY, PIN = 'tvdbkey-1234567890abcd', 'PIN99887766'
HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}


class Tvdb:
    """A fake TheTVDB v4: login with KEY (and PIN), series extended + episodes."""
    def __init__(self, series=None, expire_after=None):
        self.series, self.logins, self.gets, self.n = series or {}, [], [], 0
        self.expire_after = expire_after

    def post(self, path, body):
        self.logins.append(body)
        if body.get('apikey') != KEY:
            return 401, {'status': 'failure'}
        return 200, {'data': {'token': f'tok{len(self.logins)}'}}

    def get(self, path, params, tok):
        self.gets.append((path, tok))
        self.n += 1
        if self.expire_after and self.n == self.expire_after:
            return 401, {}
        sid = int(path.split('/')[2])
        s = self.series.get(sid)
        if s is None:
            return 404, {}
        if path.endswith('/extended'):
            return 200, {'data': {'airsTime': s['airs'], 'originalNetwork': {'country': s['country']}}}
        if params.get('page', 0) > 0:
            return 200, {'data': {'episodes': []}}
        return 200, {'data': {'episodes': [{'seasonNumber': se, 'number': n, 'aired': a, 'runtime': rt,
                                            'image': s.get('images', {}).get((se, n))}
                                           for (se, n), (a, rt) in s['eps'].items()]}}


@pytest.fixture
def tvdb():
    fake = Tvdb()
    tv_tvdb.configure(post=fake.post, get=fake.get)
    yield fake
    tv_tvdb.configure()


def _set(key='', pin=''):
    app.main.settings.update({'apis': {'tvdb': {'api_key': key, 'pin': pin}}})


# ------------------------------------------------------------------ connector

def test_get_masks_key_and_pin_and_owner_saves(minimal, tvdb):  # noqa: F811
    _set()
    c = _client(minimal)
    assert c.get('/api/settings/connectors', environ_base=REMOTE).get_json()['tvdb'] == {'set': False, 'last4': '', 'pin_set': False}
    r = c.put('/api/settings/connectors/tvdb', json={'api_key': KEY, 'pin': PIN}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['tvdb'] == {'set': True, 'last4': KEY[-4:], 'pin_set': True}
    g = c.get('/api/settings/connectors', environ_base=REMOTE).get_data(as_text=True)
    assert KEY not in g and PIN not in g
    assert tvdb.logins[-1] == {'apikey': KEY, 'pin': PIN}
    r = c.post('/api/settings/connectors/tvdb/test', json={}, environ_base=REMOTE, headers=HDR)      # the stored key
    assert r.get_json() == {'ok': True, 'message': 'Connected'}
    r = c.put('/api/settings/connectors/tvdb', json={'api_key': ''}, environ_base=REMOTE, headers=HDR)
    assert r.get_json()['tvdb'] == {'set': False, 'last4': '', 'pin_set': False}


def test_test_error_and_a_rejected_key_is_not_saved(minimal, tvdb):  # noqa: F811
    _set()
    c = _client(minimal)
    r = c.post('/api/settings/connectors/tvdb/test', json={'api_key': 'wrong-key-000000'}, environ_base=REMOTE, headers=HDR)
    assert r.get_json() == {'ok': False, 'error': 'Invalid key or PIN'}
    r = c.put('/api/settings/connectors/tvdb', json={'api_key': 'wrong-key-000000'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and app.main.settings.get('apis.tvdb.api_key') == ''


def test_owner_only(minimal, tvdb):  # noqa: F811
    _client(minimal)
    for c in (_client(minimal, admin=True, name='second'), _client(minimal, admin=False, name='kid')):
        assert c.put('/api/settings/connectors/tvdb', json={'api_key': KEY}, environ_base=REMOTE, headers=HDR).status_code == 403
        assert c.post('/api/settings/connectors/tvdb/test', json={'api_key': KEY}, environ_base=REMOTE, headers=HDR).status_code == 403
    anon = minimal.test_client()
    assert anon.put('/api/settings/connectors/tvdb', json={'api_key': KEY}, environ_base=REMOTE, headers=HDR).status_code in (401, 302)
    assert tvdb.logins == []


def test_key_and_pin_never_logged(minimal, tvdb, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    c = _client(minimal)
    c.put('/api/settings/connectors/tvdb', json={'api_key': KEY, 'pin': PIN}, environ_base=REMOTE, headers=HDR)
    c.post('/api/settings/connectors/tvdb/test', json={'api_key': KEY, 'pin': PIN}, environ_base=REMOTE, headers=HDR)
    assert KEY not in caplog.text and PIN not in caplog.text
    _set()


# ------------------------------------------------------------------ air times

def test_airstamp_in_the_network_zone_across_dst():
    ny = 'America/New_York'
    assert tv_tvdb.airstamp('2026-10-18', '21:00', ny) == '2026-10-19T01:00:00+00:00'      # EDT
    assert tv_tvdb.airstamp('2026-11-02', '21:00', ny) == '2026-11-03T02:00:00+00:00'      # EST after Nov 1
    assert tv_tvdb.airstamp('2026-10-18', '21:00', tv_tvdb.zone_for('GBR')) == '2026-10-18T20:00:00+00:00'   # BST
    assert tv_tvdb.airstamp('2026-11-01', '21:00', tv_tvdb.zone_for('gbr')) == '2026-11-01T21:00:00+00:00'   # GMT
    assert tv_tvdb.airstamp('2026-10-18', None, ny) is None and tv_tvdb.airstamp('2026-10-18', '21:00', None) is None
    assert tv_tvdb.zone_for('xyz') is None


def test_with_a_key_air_times_come_from_tvdb(scanned, tvdb, monkeypatch):  # noqa: F811
    monkeypatch.setattr('app.connectors.tvdb_credentials', lambda: (KEY, ''))
    tvdb.series[371980] = {'airs': '21:00', 'country': 'usa', 'eps': {(2, 1): ('2026-10-12', 42)}}
    tvdb.series[334824] = {'airs': '09:00', 'country': 'deu', 'eps': {(1, 1): ('2026-10-15', 60)}}   # a German network
    maze = Maze({1: SEV})
    tv_airtimes.configure(get=maze, sleep=lambda s: None)
    tv_airtimes.refresh(now=5000.0)
    assert _eps(95396) == {(2, 1): ('2026-10-13T01:00:00+00:00', 42)}
    assert _eps(70523) == {(1, 1): ('2026-10-15T07:00:00+00:00', 60)}                    # 09:00 CEST
    asked = {c[1].get('thetvdb') for c in maze.calls if c[0] == '/lookup/shows'}
    assert not asked & {371980, 334824}                                                  # TVDB knew these: TVmaze not asked
    out = {(e['show'], e['season'], e['episode']): e['airstamp'] for e in tv_calendar.calendar(dt.date(2026, 10, 11), 7)['episodes']}
    assert out[('Severance', 2, 2)] is None                                              # no TVDB data: the date only


def test_no_key_falls_back_to_tvmaze_and_a_401_logs_in_again(scanned, tvdb, monkeypatch):  # noqa: F811
    monkeypatch.setattr('app.connectors.tvdb_credentials', lambda: ('', ''))
    tv_airtimes.configure(get=Maze({1: SEV}), sleep=lambda s: None)
    tv_airtimes.refresh(now=5000.0)
    assert _eps(95396)[(2, 1)] == ('2026-10-13T01:00:00+00:00', 42) and tvdb.logins == []
    # the cached token expires mid-refresh: one new login, then on
    tvdb.series[371980] = {'airs': '21:00', 'country': 'usa', 'eps': {(2, 1): ('2026-10-12', 42)}}
    tvdb.expire_after = 2
    creds = (KEY, '')
    assert tv_tvdb.episodes(371980, creds, lambda: None) == {(2, 1): {'airstamp': '2026-10-13T01:00:00+00:00', 'runtime': 42, 'image': None}}
    assert len(tvdb.logins) == 2


def test_tvdb_rematch_guard(scanned, tvdb, monkeypatch):  # noqa: F811
    monkeypatch.setattr('app.connectors.tvdb_credentials', lambda: (KEY, ''))
    tvdb.series[371980] = {'airs': '21:00', 'country': 'usa', 'eps': {(2, 1): ('2026-10-12', 42)}}
    sid = rows('SELECT id FROM series WHERE tmdb_id = 95396')[0]['id']
    real = tv_tvdb.episodes

    def rematch_meanwhile(tvdb_id, creds, pace):
        out = real(tvdb_id, creds, pace)
        if tvdb_id == 371980:
            write('UPDATE series SET tmdb_id = 999001 WHERE id = ?', sid)
        return out
    monkeypatch.setattr(tv_tvdb, 'episodes', rematch_meanwhile)
    tv_airtimes.refresh(now=5000.0)
    assert rows('SELECT COUNT(*) AS n FROM episodes WHERE series_id = ? AND airstamp IS NOT NULL', sid)[0]['n'] == 0


# ------------------------------------------------------------------ stills

def test_stills_tvdb_image_with_a_key_and_tmdb_still_and_backdrop(scanned, tvdb, monkeypatch):  # noqa: F811
    from app import tv_scan, tv_store
    monkeypatch.setattr('app.connectors.tvdb_credentials', lambda: (KEY, ''))
    tvdb.series[371980] = {'airs': '21:00', 'country': 'usa', 'eps': {(2, 1): ('2026-10-12', 42), (2, 2): ('2026-10-13', 42)},
                           'images': {(2, 1): '/banners/episodes/371980/1.jpg', (2, 2): 'https://evil.example/x.jpg'}}
    sid = rows('SELECT id FROM series WHERE tmdb_id = 95396')[0]['id']
    with tv_store.write() as conn:                                     # TMDB's still for S02E02 (the season read)
        tv_scan._store_episodes(conn, sid, {2: {'episodes': [{'episode_number': 2, 'name': 'Episode 2', 'air_date': '2026-10-13',
                                                              'still_path': '/still22.jpg'},
                                                             {'episode_number': 3, 'name': 'Episode 3', 'air_date': '2026-10-14',
                                                              'still_path': 'javascript:x'}]}})
        conn.execute("UPDATE series SET backdrop_path = '/bd.jpg' WHERE id = ?", (sid,))
    tv_airtimes.refresh(now=5000.0)
    out = {(e['season'], e['episode']): (e['tvdb_image'], e['still_path'], e['backdrop_path'])
           for e in tv_calendar.calendar(dt.date(2026, 10, 11), 7)['episodes'] if e['show'] == 'Severance'}
    assert out[(2, 1)][0] == 'https://artworks.thetvdb.com/banners/episodes/371980/1.jpg'
    assert out[(2, 2)] == (None, '/still22.jpg', '/bd.jpg')            # a foreign host is never stored
    assert out[(2, 3)] == (None, None, '/bd.jpg')                      # not an image path: dropped


def test_tvdb_image_urls_only_from_the_artwork_host():
    assert tv_tvdb.image_url('/banners/a.jpg') == 'https://artworks.thetvdb.com/banners/a.jpg'
    assert tv_tvdb.image_url('https://artworks.thetvdb.com/banners/a.jpg') == 'https://artworks.thetvdb.com/banners/a.jpg'
    for bad in (None, '', 'https://evil.example/a.jpg', 'https://artworks.thetvdb.com.evil/a.jpg', '/a b.jpg', 7):
        assert tv_tvdb.image_url(bad) is None


# ------------------------------------------------------------------ Reviewer on b07c949

def test_with_a_key_shows_tvdb_lacks_fall_back_to_tvmaze_by_imdb(scanned, tvdb, monkeypatch):  # noqa: F811
    monkeypatch.setattr('app.connectors.tvdb_credentials', lambda: (KEY, ''))
    write('UPDATE series SET tvdb_id = NULL WHERE tmdb_id = 70523')                     # Dark: no tvdb_id
    dark = {'tvdb': -1, 'imdb': 'tt5753856', 'eps': {(1, 1): ('2026-10-15T07:00:00+00:00', 60)}}
    sev = {'tvdb': -2, 'imdb': 'tt11280740', 'eps': {(2, 1): ('2026-10-13T01:00:00+00:00', 42)}}     # Severance: TVDB 404s
    maze = Maze({1: dark, 2: sev})
    tv_airtimes.configure(get=maze, sleep=lambda s: None)
    tv_airtimes.refresh(now=5000.0)
    assert _eps(70523) == {(1, 1): ('2026-10-15T07:00:00+00:00', 60)}
    assert _eps(95396) == {(2, 1): ('2026-10-13T01:00:00+00:00', 42)}
    assert ('/lookup/shows', {'imdb': 'tt5753856'}) in maze.calls
    assert any(p == '/series/371980/extended' for p, _ in tvdb.gets)                     # TVDB was asked first


class _Redirect:
    """A requests.Session whose every answer is a 307 to elsewhere."""
    calls = []

    def __init__(self):
        self.trust_env = True

    def _go(self, method, url, **kw):
        _Redirect.calls.append((method, url, kw.get('allow_redirects'), self.trust_env))

        class R:
            status_code = 307
            content = b''
            headers = {'Location': 'https://evil.example/steal'}

            def json(self):
                return {}
        return R()

    def post(self, url, **kw):
        return self._go('POST', url, **kw)

    def get(self, url, **kw):
        return self._go('GET', url, **kw)


def test_a_redirect_is_never_followed(monkeypatch):
    import requests
    tv_tvdb.configure()
    tv_airtimes.configure(get=None, sleep=lambda s: None)
    _Redirect.calls = []
    monkeypatch.setattr(requests, 'Session', _Redirect)
    assert tv_tvdb.check_login(KEY, PIN) == 'Unexpected response'                       # the key and PIN go nowhere else
    with pytest.raises(requests.HTTPError):
        tv_airtimes._get('/lookup/shows', {'imdb': 'tt1'})
    assert [(m, u.split('/')[2], follow, env) for m, u, follow, env in _Redirect.calls] == [
        ('POST', 'api4.thetvdb.com', False, False), ('GET', 'api.tvmaze.com', False, False)]
