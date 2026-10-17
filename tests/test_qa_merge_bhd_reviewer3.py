"""QA (4049342) — Reviewer's BHD merge items, next to test_merge_bhd_sources.py.

(a) /api/sources/all-results end to end with a mock BHD whose rsskey and api key appear in
    download_url, url AND name: no secret anywhere in the body, first call and cached call
(b) an id rejection (BHD refuses the tmdb/imdb id) is not negative-cached
(c) the all-results cache misses when tmdb_id changes
(d) the same query and id as movie vs tv are separate negative-cache keys
(e) BHD+IPT with a year: IPT retries without the year, BHD is not asked again
"""
import json
import logging

import pytest

import app.main
from app import tracker_clients as tc
from app.tracker_search import LumaTrackersClient
from tests.fixtures import bhd_pages
from tests.test_bhd_web_fix import EMPTY, KEY, R as RJ, engine  # noqa: F401  (fixture)
from tests.test_tracker_sources import R, env, signed_in  # noqa: F401  (fixtures)
from tests.test_qa_ipt_ee3bee4 import SIGNED_IN_EMPTY, ipt_page, ipt_row

RK = bhd_pages.RK
IDREJ = {'success': False, 'status_code': 0, 'status_message': 'Invalid tmdb_id'}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(tc, '_sleep', lambda s: None)


def _bhd(store, host='t.example'):
    rec, err = store.create({'type': 'beyondhd', 'name': 'BHD', 'url': f'https://{host}', 'api_key': KEY,
                             'rss_key': RK, 'categories': [2000, 5000], 'priority': 25})
    assert not err, err
    return rec


def _ipt(store):
    rec, err = store.create({'type': 'iptorrents', 'name': 'IPT', 'url': 'https://ipt.example',
                             'cookie': 'uid=12345; pass=qaiptpass0123456789abcdef', 'categories': [2000, 5000],
                             'priority': 10})
    assert not err, err
    return rec


def _page(rows):
    return {'status_code': 1, 'page': 1, 'results': rows, 'total_pages': 1, 'total_results': len(rows), 'success': True}


def _secret_rows():
    r1 = bhd_pages.row(1, 'Interstellar 2014 1080p BluRay x264-QA', '1080p', '2024-01-01 10:00:00')
    r1['download_url'] = f'https://t.example/torrent/download/auto.900001.{RK}?api_token={KEY}'
    r1['url'] = f'https://t.example/details/900001?api_token={KEY}&rsskey={RK}'
    r2 = bhd_pages.row(2, f'Interstellar 2014 2160p Remux {RK}', 'UHD Remux', '2024-01-01 10:00:00')   # secret in name
    r3 = bhd_pages.row(3, f'Interstellar 2014 720p WEB-DL api_token={KEY}', '720p', '2024-01-01 10:00:00')
    for r in (r2, r3):
        r['download_url'] = f'https://t.example/torrent/download/auto.{r["id"]}.{RK}'
        r['url'] = f'https://t.example/details/{r["id"]}?rsskey={RK}'
    return [r1, r2, r3]


def _bhd_calls(env):
    return [c for c in env['mock'].hits('t.example') if c['method'] == 'POST']


# ------------------------------------------------------------------ (a)

def test_a_no_bhd_secret_in_all_results_first_and_cached_call(env, signed_in):  # noqa: F811
    _bhd(env['store'])
    env['mock'].set('t.example', R(body=json.dumps(_page(_secret_rows()))))
    url = '/api/sources/all-results?title=Interstellar&media_type=movie&year=2014&tmdb_id=157336'
    first = signed_in.get(url)
    n = len(_bhd_calls(env))
    second = signed_in.get(url)
    assert len(_bhd_calls(env)) == n, 'second call should be served from the cache'
    for r in (first, second):
        body = r.get_data(as_text=True)
        assert r.status_code == 200 and r.get_json()['releases'], body[:300]
        assert KEY not in body and RK not in body and 'api_token' not in body and 'rsskey' not in body, body[:400]


# ------------------------------------------------------------------ (b)

def test_b_id_rejection_is_not_negative_cached(engine):  # noqa: F811
    eng, mock = engine
    mock.resps = [RJ(EMPTY), RJ(EMPTY), RJ(IDREJ)]          # text, text without year, id
    c = LumaTrackersClient(eng)
    assert c.search_all('Nothing Here', 'movie', 2024, tmdb_id=42) == []
    assert 'tmdb_id' in json.dumps(mock.calls[-1].get('json') or {}), [c.get('json') for c in mock.calls]
    assert not eng._neg_store()
    n = len(mock.calls)
    c.search_all('Nothing Here', 'movie', 2024, tmdb_id=42)
    assert len(mock.calls) > n                                   # asked again


# ------------------------------------------------------------------ (c)

def test_c_all_results_cache_misses_when_tmdb_id_changes(env, signed_in):  # noqa: F811
    _bhd(env['store'])
    env['mock'].set('t.example', R(body=json.dumps(_page(_secret_rows()[:1]))))
    base = '/api/sources/all-results?title=Interstellar&media_type=movie&year=2014&tmdb_id='
    assert signed_in.get(base + '157336').get_json()['releases']
    n1 = len(_bhd_calls(env))
    signed_in.get(base + '157336')
    assert len(_bhd_calls(env)) == n1                            # same id: cached
    assert signed_in.get(base + '999999').get_json()['releases']
    assert len(_bhd_calls(env)) > n1                             # new id: cache miss


# ------------------------------------------------------------------ (d)

def test_d_movie_and_tv_have_separate_negative_cache_keys(engine):  # noqa: F811
    eng, mock = engine
    mock.resps = [RJ(EMPTY)]
    c = LumaTrackersClient(eng)
    c.search_all('Nothing Here', 'movie', None, tmdb_id=42)
    n = len(mock.calls)
    assert len(eng._neg_store()) == 1
    c.search_all('Nothing Here', 'tv', None, tmdb_id=42)
    assert len(mock.calls) > n, 'tv answered from the movie negative-cache entry'
    assert len(eng._neg_store()) == 2


# ------------------------------------------------------------------ (e)

def test_e_bhd_plus_ipt_with_year_ipt_retries_bhd_not_asked_again(env, signed_in, caplog):  # noqa: F811
    _bhd(env['store'])
    _ipt(env['store'])
    env['mock'].set('t.example', R(body=json.dumps(EMPTY)))
    env['mock'].set('ipt.example', R(body=SIGNED_IN_EMPTY), R(body=ipt_page(ipt_row(1, 'Interstellar 1080p BluRay x264-QA'))))
    caplog.set_level(logging.INFO)
    r = signed_in.get('/api/sources/all-results?title=Interstellar&media_type=movie&year=2014&tmdb_id=157336')
    j = r.get_json()
    assert r.status_code == 200 and [x['title'] for x in j['releases']] == ['Interstellar 1080p BluRay x264-QA'], j
    assert 'retrying without it' in caplog.text
    ipt_q = [c['url'] for c in env['mock'].hits('ipt.example')]
    assert len(ipt_q) == 2 and '2014' in ipt_q[0] and '2014' not in ipt_q[1], ipt_q
    bodies = [c.get('json') or {} for c in _bhd_calls(env)]
    steps = [b.get('search') or b.get('tmdb_id') for b in bodies]
    # one chain only: text with the year, text without it, then the id — never repeated
    assert len(steps) == len(set(map(str, steps))) and len(steps) <= 3, steps
