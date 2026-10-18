"""Reviewer on 8b55bb8 (v158c): (1) automatic grabs are charged as background TV requests and
every redirect hop is charged; (2) the scoped qBittorrent add refuses duplicated keys and a query
string; (3) detach_path's deferred delete touches only rows still at the old folder."""
import pytest
import requests

from app import instance, tracker_sources
from tests.test_tracker_sources import (R, IPT_HTML, _search, env, ipt, signed_in, torrent_bytes,  # noqa: F401
                                        torznab, rss)
from app.tracker_guard import guard_key


def _tok(c):
    rel = _search(c)[0]
    return tracker_sources.release_token(rel['download_url'])


def _entries(g, gk):
    return list(g.log.get(gk, [])), list(g.purposes.get(gk, []))


def test_automatic_grab_is_a_background_tv_request_on_a_capped_tracker(signed_in, env):  # noqa: F811
    rec = ipt(env['store'])
    env['mock'].set('ipt.example', R(body=IPT_HTML), R(body=torrent_bytes()))
    tok = _tok(signed_in)
    gk = guard_key(env['store'].config(env['store'].get(rec['id'])))
    log0, pur0 = _entries(env['guard'], gk)
    kind, _ = tracker_sources.fetch_release(tok, automatic=True)
    log1, pur1 = _entries(env['guard'], gk)
    assert kind == 'torrent'
    assert [k for _, k in log1[len(log0):]] == ['b'] and [p for _, p in pur1[len(pur0):]] == ['tv']


def test_manual_grab_stays_interactive(signed_in, env):  # noqa: F811
    rec = ipt(env['store'])
    env['mock'].set('ipt.example', R(body=IPT_HTML), R(body=torrent_bytes()))
    tok = _tok(signed_in)
    gk = guard_key(env['store'].config(env['store'].get(rec['id'])))
    log0, pur0 = _entries(env['guard'], gk)
    tracker_sources.fetch_release(tok)
    log1, pur1 = _entries(env['guard'], gk)
    assert [k for _, k in log1[len(log0):]] == ['i'] and pur1 == pur0


def test_automatic_grab_on_an_uncapped_indexer_is_background_without_the_tv_purpose(signed_in, env):  # noqa: F811
    rec = torznab(env['store'])
    env['mock'].set('t.example', R(body=rss(('The Nut Job 2014 1080p WEB-DL', 'https://t.example/dl/1'))), R(body=torrent_bytes()))
    tok = _tok(signed_in)
    gk = guard_key(env['store'].config(env['store'].get(rec['id'])))
    log0, pur0 = _entries(env['guard'], gk)
    tracker_sources.fetch_release(tok, automatic=True)
    log1, pur1 = _entries(env['guard'], gk)
    assert [k for _, k in log1[len(log0):]] == ['b'] and pur1 == pur0


def test_every_redirect_hop_is_charged_and_a_refusal_stops_the_fetch(signed_in, env, monkeypatch):  # noqa: F811
    rec = ipt(env['store'])
    env['mock'].set('ipt.example', R(body=IPT_HTML), R(302, headers={'Location': 'https://ipt.example/f/2'}),
                    R(302, headers={'Location': 'https://ipt.example/f/3'}), R(body=torrent_bytes()))
    tok = _tok(signed_in)
    gk = guard_key(env['store'].config(env['store'].get(rec['id'])))
    n0 = len(env['guard'].log.get(gk, []))
    sent0 = len(env['mock'].calls)
    tracker_sources.fetch_release(tok)
    assert len(env['guard'].log[gk]) - n0 == 3 == len(env['mock'].calls) - sent0       # 3 requests, 3 charges
    env['mock'].set('ipt.example', R(302, headers={'Location': 'https://ipt.example/f/2'}), R(body=torrent_bytes()))
    real = env['guard'].take
    seen = []

    def take(*a, **k):
        seen.append(1)
        return None if len(seen) == 1 else 'hourly cap 40'
    monkeypatch.setattr(env['guard'], 'take', take)
    sent0 = len(env['mock'].calls)
    with pytest.raises(tracker_sources.GrabError) as e:
        tracker_sources.fetch_release(tok)
    assert e.value.status == 429 and len(env['mock'].calls) - sent0 == 1                # the second hop never went
    del real


# ------------------------------------------------------------------ (2) the scoped add

def _guarded(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')
    sent = []

    def fake_send(self, request, **kw):
        sent.append(request.url)
        r = requests.Response()
        r.status_code = 200
        r._content = b'Ok.'
        return r
    monkeypatch.setattr(requests.Session, 'send', fake_send)
    instance.uninstall_write_guard()
    monkeypatch.setattr(instance, '_installed', {'on': False})
    instance.install_write_guard()
    return sent


def test_scoped_add_refuses_duplicated_keys_and_a_query_string(monkeypatch):
    sent = _guarded(monkeypatch)
    try:
        s = requests.Session()
        add = 'http://qbit/api/v2/torrents/add'
        ok = {'urls': 'magnet:?', 'category': 'luma-test-tv', 'tags': 'luma-test-tv'}
        with instance.scoped_add('tv_grab', 'luma-test-tv'):
            s.post(add, data=ok)
            for bad in ([('urls', 'magnet:?'), ('category', 'luma-test-tv'), ('category', 'luma-tv'), ('tags', 'luma-test-tv')],
                        [('urls', 'magnet:?'), ('category', 'luma-test-tv'), ('tags', 'luma-test-tv'), ('tags', 'luma-tv')]):
                with pytest.raises(instance.WritesBlocked):
                    s.post(add, data=bad)
            with pytest.raises(instance.WritesBlocked):
                s.post(add + '?category=luma-tv', data=ok)
            with pytest.raises(instance.WritesBlocked):
                s.post(add, params={'savepath': '/x'}, data=ok)
            with pytest.raises(instance.WritesBlocked):                               # multipart duplicate too
                s.post(add, files={'torrents': ('r.torrent', b'd'), 'category': (None, 'luma-tv')},
                       data={'category': 'luma-test-tv', 'tags': 'luma-test-tv'})
            with pytest.raises(instance.WritesBlocked):
                s.post('http://qbit/api/v2/torrents/createCategory?savePath=/x', data={'category': 'luma-test-tv'})
        assert len(sent) == 1
    finally:
        instance.uninstall_write_guard()


def test_tv_grab_marks_its_own_auto_grabs_automatic(monkeypatch):
    from app import tv_grab
    seen = []
    monkeypatch.setattr(tv_grab, 'fetch_link', lambda u, automatic=False: seen.append(automatic) or (_ for _ in ()).throw(
        tracker_sources.GrabError('stop', 502)))
    monkeypatch.setattr(tv_grab, 'allowed', lambda: True)
    for origin in ('auto', 'manual'):
        tv_grab.grab({'title': 'x', 'downloadUrl': '/api/trackers/release/' + 'y' * 20}, 1, 1, [1], origin, 1000.0)
    assert seen == [True, False]
