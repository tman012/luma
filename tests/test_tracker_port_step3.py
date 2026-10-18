"""Tracker port step 3 (v171): movie grabs on the native stack, verified.

Sources movie grabs from IPTorrents, BeyondHD and PrivateHD go through release tokens only: the browser
sees '/api/trackers/release/<token>', never a tracker URL, passkey, RSS key or PID; /api/downloads/add
resolves the token server-side, hands the bytes to qBit with the movie category and tracks the TMDB
movie. Each grab is charged once to its tracker's guard. Prowlarr and the legacy indexer clients are
rigged to fail (tests.test_tracker_sources.env), so a pass proves the native path. Fake trackers and a
fake qBittorrent only."""
import json
import time

import pytest

import app.main

from app import tracker_sources
from app.tracker_guard import guard_key
from tests import test_tracker_sources as ts
from tests.test_tracker_sources import (IPT_HTML, R, TMDB, _grab, _search, env, signed_in, torrent_bytes,  # noqa: F401
                                        COOKIE)

RK = 'rk_secret_value_1234'
BHD_KEY = 'bhdapikey0123456789abcdef0123456789'
PHD_PW, PHD_PID = 'pw_secret_1', 'pid_secret_123456'


def _ipt(store):
    return ts.ipt(store)


def _bhd(store):
    rec, err = store.create({'type': 'beyondhd', 'name': 'BeyondHD', 'url': 'https://bhd.example', 'api_key': BHD_KEY,
                             'rss_key': RK, 'categories': [2000], 'priority': 20})
    assert not err, err
    return rec


def _phd(store):
    rec, err = store.create({'type': 'privatehd', 'name': 'PrivateHD', 'url': 'https://phd.example', 'username': 'me',
                             'password': PHD_PW, 'pid': PHD_PID, 'categories': [2000], 'priority': 15})
    assert not err, err
    return rec


BHD_SEARCH = json.dumps({'status_code': 1, 'success': True, 'results': [{
    'name': 'The Nut Job 2014 1080p BluRay REMUX', 'download_url': f'https://bhd.example/torrent/download/1.{RK}',
    'size': 30000000000, 'seeders': 10, 'leechers': 2, 'freeleech': 0, 'category': 'Movies',
    'url': 'https://bhd.example/torrents/1', 'imdb_id': 'tt1821658', 'created_at': '2026-10-15 10:00:00'}]})
PHD_SEARCH = json.dumps({'data': [{
    'file_name': 'The.Nut.Job.2014.1080p.BluRay.x264-GRP', 'download': f'https://phd.example/rss/download/9/{PHD_PID}.torrent',
    'file_size': 9000000000, 'seed': 33, 'leech': 1, 'download_multiply': 1,
    'url': 'https://phd.example/torrent/9', 'created_at': '2026-10-01T00:00:00Z', 'movie_tv': {'imdb': '1821658'}}]})

CASES = {
    'iptorrents': (_ipt, 'ipt.example', lambda: [R(body=IPT_HTML), R(body=torrent_bytes())], [ts.COOKIE]),
    'beyondhd': (_bhd, 'bhd.example', lambda: [R(body=BHD_SEARCH), R(body=torrent_bytes())], [RK, BHD_KEY]),
    'privatehd': (_phd, 'phd.example', lambda: [R(body=json.dumps({'token': 'tok', 'expiry': 3600})), R(body=PHD_SEARCH),
                                                R(body=torrent_bytes())], [PHD_PW, PHD_PID, 'tok']),
}


@pytest.fixture
def hosts(monkeypatch):
    monkeypatch.setitem(ts.ADDRS, 'bhd.example', ['93.184.216.60'])
    monkeypatch.setitem(ts.ADDRS, 'phd.example', ['93.184.216.70'])


@pytest.mark.parametrize('kind', list(CASES))
def test_movie_grab_on_native_release_token_only(signed_in, env, hosts, kind):  # noqa: F811
    make, host, answers, secrets = CASES[kind]
    rec = make(env['store'])
    env['mock'].set(host, *answers())
    r = signed_in.get('/api/sources/all-results?title=The Nut Job&media_type=movie&year=2014')
    assert r.status_code == 200, r.get_data(as_text=True)
    raw = r.get_data(as_text=True)
    rels = r.get_json()['releases']
    assert rels and all(x['tracker_type'] == kind for x in rels)
    for x in rels:                                                   # release tokens only
        assert tracker_sources.release_token(x['download_url']), x['download_url']
        assert not x.get('magnet_url') and not str(x.get('info_url') or '').startswith('/api/sources/link/')
    for s in secrets + [host]:
        assert s not in raw, (kind, s)                               # no tracker URL or secret reaches the browser
    cfg = env['store'].config(env['store'].get(rec['id']))
    before = env['guard'].counts(guard_key(cfg), time.time())
    g = _grab(signed_in, rels[0]['download_url'], name=rels[0]['title'])
    assert g.status_code == 200, g.get_data(as_text=True)
    body = g.get_data(as_text=True)
    assert g.get_json()['success'] is True
    for s in secrets:
        assert s not in body
    added = env['qbit'].added[-1]
    assert added['files']['torrents'][1] == torrent_bytes()          # the server fetched it; qBit fetched nothing
    assert added['data']['category'] == app.main._qbit_app_category()             # Luma's own category
    assert env['qbit'].fetched == []
    assert env['tracked'][-1][1]['tmdb_id'] == TMDB['tmdb_id'] and env['tracked'][-1][1]['media_type'] == 'movie'
    assert env['origins'][-1][1]['download_url'] == rels[0]['download_url']
    after = env['guard'].counts(guard_key(cfg), time.time())
    assert after != before                                          # the download was charged to this tracker
    dl = env['mock'].calls[-1]
    assert dl['url'].split('//', 1)[1].startswith(host) and dl['follow_redirects'] is False


@pytest.mark.parametrize('kind', list(CASES))
def test_a_token_from_one_tracker_never_reaches_another(signed_in, env, hosts, kind):  # noqa: F811
    make, host, answers, _ = CASES[kind]
    make(env['store'])
    env['mock'].set(host, *answers())
    tok = _search(signed_in, 'The Nut Job')[0]['download_url']
    env['mock'].calls.clear()
    bad = tok[:-1] + ('0' if tok[-1] != '0' else '1')                # an unknown token: nothing fetched, nothing added
    g = _grab(signed_in, bad)
    assert g.status_code in (404, 410) and env['qbit'].added == [] and env['mock'].calls == []


def test_a_movie_release_magnet_with_no_hash_is_refused_before_qbit(signed_in, env):  # noqa: F811
    rec = ts.torznab(env['store'])
    tok = env['engine'].tokens.issue(rec['id'], 'magnet:?dn=The.Nut.Job.2014.1080p')
    g = _grab(signed_in, '/api/trackers/release/' + tok)
    assert g.status_code == 400 and env['qbit'].added == []
