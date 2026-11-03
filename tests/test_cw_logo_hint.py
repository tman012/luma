"""cw-logo: Continue Watching cards name the TMDb id of what a tap opens (tmdb_hint), for every item type, so the sheet
starts the clearlogo at the tap; /api/clearlogo answers are never browser-cached."""
import xml.etree.ElementTree as ET
from unittest import mock

import pytest

from app import home_rails, tv_plex
import app.main as m


def el(xml):
    return ET.fromstring(xml)


@pytest.fixture(autouse=True)
def _shows():
    tv_plex._shows.clear()
    yield
    tv_plex._shows.clear()


def test_movie_hint_from_its_guid_tags():
    c = home_rails._card(el('<Video ratingKey="900" type="movie" title="Film" guid="plex://movie/abc">'
                            '<Guid id="imdb://tt1"/><Guid id="tmdb://603"/></Video>'), wide=True)
    assert c['tmdb_hint'] == 603 and c['key'] == '900'


def test_movie_legacy_agent_guid():
    c = home_rails._card(el('<Video ratingKey="901" type="movie" title="Old" guid="com.plexapp.agents.themoviedb://550?lang=en"/>'))
    assert c['tmdb_hint'] == 550


def test_episode_hint_is_the_show_id_from_the_tv_index_never_the_episode_id():
    tv_plex._shows['4501'] = (6191, 73587)
    c = home_rails._card(el('<Video ratingKey="4591" type="episode" grandparentRatingKey="4501" grandparentTitle="Show" '
                            'parentIndex="1" index="2" guid="plex://episode/x"><Guid id="tmdb://999999"/></Video>'), wide=True)
    assert c['show_key'] == '4501' and c['tmdb_hint'] == 6191


def test_episode_legacy_grandparent_guid_fallback():
    c = home_rails._card(el('<Video ratingKey="4591" type="episode" grandparentRatingKey="4501" '
                            'grandparentGuid="com.plexapp.agents.themoviedb://6191?lang=en" parentIndex="1" index="2"/>'))
    assert c['tmdb_hint'] == 6191


def test_season_hint_is_the_show_id():
    tv_plex._shows['4501'] = (6191, None)
    c = home_rails._card(el('<Directory ratingKey="4550" type="season" parentRatingKey="4501" parentTitle="Show" title="Season 1"/>'))
    assert c['show_key'] == '4501' and c['tmdb_hint'] == 6191


def test_no_tmdb_id_anywhere_means_no_hint():
    c = home_rails._card(el('<Video ratingKey="4591" type="episode" grandparentRatingKey="4501" '
                            'grandparentGuid="plex://show/abc" parentIndex="1" index="2"/>'))
    assert 'tmdb_hint' not in c


def test_continue_watching_asks_plex_for_guids():
    plex = mock.Mock()
    plex._get.return_value = el('<MediaContainer><Video ratingKey="900" type="movie" title="F"><Guid id="tmdb://603"/></Video></MediaContainer>')
    out = home_rails.continue_watching(plex)
    assert plex._get.call_args.kwargs['params']['includeGuids'] == 1
    assert out[0]['tmdb_hint'] == 603


def test_clearlogo_is_never_browser_cached(test_app):
    client = test_app.test_client()
    for ret in ('/l.png', None):
        with mock.patch.object(m, '_fetch_one_logo', return_value=ret):
            r = client.get('/api/clearlogo/5?type=tv')
        assert r.status_code == 200 and 'no-store' in r.headers.get('Cache-Control', '')
    with mock.patch.object(m, '_fetch_one_logo', side_effect=RuntimeError('x')):
        r = client.get('/api/clearlogo/5?type=tv')
    assert r.status_code == 500 and 'no-store' in r.headers.get('Cache-Control', '')


# --- cw-logo cold path: the logo on the card, a fast first logo fetch ---------------------------------------------

def _logo_cfg(cache, warm_result=None):
    """home_rails with a fake logo cache: cache maps (tid, type) -> path|None (known); warm fills it."""
    calls = []

    def cached(tid, mtype):
        k = (tid, mtype)
        return (True, cache[k]) if k in cache else (False, None)

    def warm(pairs):
        from concurrent.futures import Future
        calls.append(list(pairs))
        out = {}
        for p in pairs:
            if warm_result and p in warm_result:
                cache[p] = warm_result[p]
            f = Future(); f.set_result(cache.get(p)); out[p] = f
        return out
    return cached, warm, calls


def test_cards_carry_the_cached_logo_and_a_known_no_logo():
    cached, warm, calls = _logo_cfg({(603, 'movie'): '/m.png', (6191, 'tv'): None})
    with mock.patch.dict(home_rails._cfg, logo_cached=cached, logo_warm=warm):
        out = home_rails.with_logos([{'key': '900', 'type': 'movie', 'tmdb_hint': 603},
                                     {'key': '4591', 'type': 'episode', 'show_key': '4501', 'tmdb_hint': 6191},
                                     {'key': 'x', 'type': 'movie'}])
    assert out[0]['clearlogo_url'] == 'https://image.tmdb.org/t/p/original/m.png'
    assert out[1]['has_logo'] is False and 'clearlogo_url' not in out[1]
    assert 'clearlogo_url' not in out[2] and calls == []


def test_cw_waits_briefly_for_uncached_logos_other_rails_only_warm():
    cached, warm, calls = _logo_cfg({}, warm_result={(6191, 'tv'): '/s.png'})
    with mock.patch.dict(home_rails._cfg, logo_cached=cached, logo_warm=warm):
        cw = home_rails.with_logos([{'key': '4591', 'type': 'episode', 'tmdb_hint': 6191}], wait_s=0.2)
    assert cw[0]['clearlogo_url'].endswith('/s.png') and calls == [[(6191, 'tv')]]
    cached, warm, calls = _logo_cfg({}, warm_result={(7, 'tv'): '/r.png'})
    with mock.patch.dict(home_rails._cfg, logo_cached=cached, logo_warm=warm):
        rail = home_rails.with_logos([{'key': '1', 'type': 'show', 'tmdb_id': 7}])
    assert 'clearlogo_url' not in rail[0] and calls == [[(7, 'tv')]]


def test_cw_logo_wait_is_bounded():
    import time as _t
    from concurrent.futures import Future
    never = Future()
    with mock.patch.dict(home_rails._cfg, logo_cached=lambda *a: (False, None), logo_warm=lambda pairs: {p: never for p in pairs}):
        t0 = _t.monotonic()
        out = home_rails.with_logos([{'key': '1', 'type': 'movie', 'tmdb_hint': 5}], wait_s=0.1)
    assert _t.monotonic() - t0 < 0.5 and 'clearlogo_url' not in out[0]


def test_logo_cache_peek_never_fetches():
    m._logo_cache.clear(); m._logo_cache_ts.clear()
    with mock.patch.object(m.requests, 'get', side_effect=AssertionError('no network')):
        assert m._cached_logo(1, 'tv') == (False, None)
    m._logo_cache['tv:1'] = '/a.png'; m._logo_cache_ts['tv:1'] = m.time.time()
    assert m._cached_logo(1, 'tv') == (True, '/a.png')
    m._logo_cache.clear(); m._logo_cache_ts.clear()


def test_img_tmdb_accepts_logo_size_w300_and_serves_a_repeat_from_memory(test_app):
    m._TMDB_IMG_MEM.clear()
    seen = []

    def fake_fetch(url):
        seen.append(url)
        return 200, 'image/png', b'PNGDATA'
    client = test_app.test_client()
    with mock.patch.object(m, '_img_fetch', side_effect=fake_fetch):
        r1 = client.get('/api/img/tmdb?path=/l.png&size=w300')
        r2 = client.get('/api/img/tmdb?path=/l.png&size=w300')
    assert r1.status_code == 200 and r2.data == b'PNGDATA'
    assert seen == ['https://image.tmdb.org/t/p/w300/l.png']
    m._TMDB_IMG_MEM.clear()


def test_logo_warm_resolves_and_pulls_the_image_into_memory():
    m._TMDB_IMG_MEM.clear(); m._logo_cache.clear(); m._logo_cache_ts.clear()
    with mock.patch.object(m, '_fetch_one_logo', return_value='/w.png'), \
         mock.patch.object(m, '_img_fetch', return_value=(200, 'image/png', b'X')) as f:
        futs = m._warm_logos([(9, 'tv')])
        assert futs[(9, 'tv')].result(timeout=5) == '/w.png'
    assert m._tmdb_img_mem_get('https://image.tmdb.org/t/p/w300/w.png')
    assert {c.args[0] for c in f.call_args_list} == {'https://image.tmdb.org/t/p/w300/w.png', 'https://image.tmdb.org/t/p/original/w.png'}
    m._TMDB_IMG_MEM.clear()


# v344: a title with no TMDb id never raises (LIVE 1:41 AM: int('None')): it answers no-logo, 200, no TMDb call.
def test_clearlogo_missing_id_is_no_logo_not_an_error(test_app, monkeypatch):
    calls = []
    monkeypatch.setattr(m, '_fetch_one_logo', lambda *a, **k: calls.append(a) or '/x.png')
    c = test_app.test_client()
    for bad in ('None', 'null', 'undefined', '0', '-3', 'abc'):
        r = c.get(f'/api/clearlogo/{bad}?type=movie')
        assert r.status_code == 200, bad
        assert r.get_json() == {'clearlogo_url': None}
        assert r.headers.get('Cache-Control') == 'no-store'
    assert calls == []
    assert c.get('/api/clearlogo/578?type=movie').get_json()['clearlogo_url'].endswith('/x.png')


# v345 (Reviewer): ASCII digits only ('²' passes str.isdigit but int() raises), and the type is allowlisted.
def test_clearlogo_unicode_digits_and_type_allowlist(test_app, monkeypatch):
    calls = []
    monkeypatch.setattr(m, '_fetch_one_logo', lambda tid, media, **k: calls.append((tid, media)) or None)
    c = test_app.test_client()
    for bad in ('\u00b2', '\u0663', '1\u00b2', '\uff11'):
        r = c.get('/api/clearlogo/' + bad)
        assert r.status_code == 200 and r.get_json() == {'clearlogo_url': None}, repr(bad)
    assert calls == []
    for t, want in (('tv', 'tv'), ('movie', 'movie'), ('person', 'movie'), ('../x', 'movie'), ('TV', 'movie')):
        c.get(f'/api/clearlogo/12?type={t}')
        assert calls[-1] == (12, want), t
