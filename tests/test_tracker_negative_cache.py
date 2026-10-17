"""PM-approved negative cache for tracker searches: 2-minute TTL; only a
signed-in answer with zero raw rows; never after parsed-then-dropped rows or an
auth, parse, timeout or 429 failure; keyed per tracker and exact query; every
hit logs 'cached empty answer'; an explicit user retry (refresh) bypasses it."""
import logging

import pytest

import app.main
from app import tracker_search as ts
from app.tracker_http import Response, TrackerError
from app.tracker_search import LumaTrackersClient, TrackerSearch, ReleaseTokens, TrackerSkipped
from app.tracker_guard import TrackerGuard
from app.tracker_store import TrackerStore

COOKIE = 'uid=1234567; pass=0123456789abcdef0123456789abcdef'
SIGNED_IN_EMPTY = '<a href=/lout.php>x</a><p>No Torrents Found!</p>'
ROWS = ('<a href=/lout.php>x</a><table id=torrents><tr><td><a href=/t?48>M</a>'
        '<td><a class=hv href=/t/9>Dune.2021.1080p.WEB-DL-GRP</a>'
        '<td><a href=/download.php/9/Dune.torrent>dl</a><td>4 GB<td>1<td>10<td>2</table>')
EMPTY_RSS = ('<?xml version="1.0"?><rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">'
             '<channel><title>x</title></channel></rss>')


def R(status=200, body=''):
    return Response(status, {}, body.encode())


class Mock:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []

    def __call__(self, method, url, **kw):
        self.calls.append(url)
        r = self.resps.pop(0) if len(self.resps) > 1 else self.resps[0]
        if isinstance(r, Exception):
            raise r
        return r


def _engine(tmp_path, mock, kind='iptorrents'):
    store = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'k.key')
    body = ({'type': 'iptorrents', 'name': 'IPT', 'url': 'https://iptorrents.com', 'cookie': COOKIE,
             'categories': [2000, 5000], 'priority': 25} if kind == 'iptorrents' else
            {'type': 'torznab', 'name': 'TZ', 'url': 'https://tz.example/api', 'api_key': 'ak' + 'x' * 30,
             'categories': [2000, 5000], 'priority': 25})
    rec, err = store.create(body)
    assert not err
    guard = TrackerGuard(tmp_path / 'guard.json')
    guard.wait_spacing = lambda gk: None
    eng = TrackerSearch(store=store, guard=guard, request=mock, tokens=ReleaseTokens(store, tmp_path / 'rel.json'))
    return eng, store.config(rec)


@pytest.mark.parametrize('kind,body', [('iptorrents', SIGNED_IN_EMPTY), ('torznab', EMPTY_RSS)])
def test_zero_raw_rows_is_cached_two_minutes_and_logged(tmp_path, monkeypatch, caplog, kind, body):
    mock = Mock(R(body=body))
    eng, cfg = _engine(tmp_path, mock, kind)
    caplog.set_level(logging.INFO)
    clock = [1000.0]
    monkeypatch.setattr(ts.time, 'time', lambda: clock[0])
    assert eng.search_one(cfg, 'Dune', [2000], 10, True) == []
    assert eng.search_one(cfg, 'Dune', [2000], 10, True) == []
    assert len(mock.calls) == 1
    assert sum('cached empty answer' in r.getMessage() for r in caplog.records) == 1
    clock[0] += 119
    eng.search_one(cfg, 'Dune', [2000], 10, True)
    assert len(mock.calls) == 1
    assert sum('cached empty answer' in r.getMessage() for r in caplog.records) == 2
    clock[0] += 2                                   # past 120 s
    eng.search_one(cfg, 'Dune', [2000], 10, True)
    assert len(mock.calls) == 2
    assert ts.NEG_TTL == 120


def test_key_is_per_tracker_and_exact_query(tmp_path):
    mock = Mock(R(body=SIGNED_IN_EMPTY))
    eng, cfg = _engine(tmp_path, mock)
    eng.search_one(cfg, 'Dune', [2000], 10, True)
    eng.search_one(cfg, 'Dune 2021', [2000], 10, True)
    eng.search_one(cfg, 'dune', [2000], 10, True)            # exact query: case matters
    assert len(mock.calls) == 3
    other = dict(cfg, id=cfg['id'] + 1)
    eng.search_one(other, 'Dune', [2000], 10, True)
    assert len(mock.calls) == 4


def test_refresh_bypasses_and_clears(tmp_path):
    mock = Mock(R(body=SIGNED_IN_EMPTY), R(body=ROWS))
    eng, cfg = _engine(tmp_path, mock)
    assert eng.search_one(cfg, 'Dune', [2000], 10, True) == []
    out = eng.search_one(cfg, 'Dune', [2000], 10, True, refresh=True)
    assert len(mock.calls) == 2 and len(out) == 1


@pytest.mark.parametrize('resp,kind', [
    (R(body='<form><input name=username><input name=password></form>'), 'auth'),   # signed out
    (R(body='<a href=/lout.php>x</a><table id=torrents><tr><td><td><td>'
            '<a href=/download.php/1/a.torrent>dl</a><td>1 GB</table>'), 'response'),  # parsed, dropped
    (R(status=429), None),
    (TrackerError('timeout'), 'timeout'),
    (R(status=500), None),
])
def test_never_cached_after_a_failure(tmp_path, resp, kind):
    mock = Mock(resp)
    eng, cfg = _engine(tmp_path, mock)
    eng.guard.take = lambda *a, **k: None          # no backoff: we are testing the cache only
    eng.guard.success = lambda *a, **k: None
    for _ in range(2):
        with pytest.raises((TrackerError, TrackerSkipped)):
            eng.search_one(cfg, 'Dune', [2000], 10, True)
    assert len(mock.calls) == 2
    assert not eng._neg_store()


def test_rows_dropped_after_parsing_are_not_negative_cached(tmp_path, monkeypatch):
    mock = Mock(R(body=ROWS))
    eng, cfg = _engine(tmp_path, mock)
    monkeypatch.setattr(eng, 'to_item', lambda *a, **k: None)     # every row dropped
    c = LumaTrackersClient(eng)
    assert c.search_all('Dune', 'movie', None) == []
    assert not eng._neg_store()
    c.search_all('Dune', 'movie', None)
    assert len(mock.calls) == 2


def test_all_results_refresh_flag_reaches_the_trackers(test_app, monkeypatch):
    seen = []

    class Fake(LumaTrackersClient):
        def __init__(self):
            pass

        def search_all(self, q, mt, year=None, refresh=False, statuses=None):
            seen.append(refresh)
            return []
    import app.indexer_api as ia
    monkeypatch.setattr(ia, 'get_indexer_client', lambda s: Fake())
    monkeypatch.setattr(app.main, '_indexer_viewer_denied', lambda: None)
    c = test_app.test_client()
    c.get('/api/sources/all-results?title=Dune&media_type=movie')
    c.get('/api/sources/all-results?title=Dune&media_type=movie&refresh=1')
    assert seen == [False, True]
