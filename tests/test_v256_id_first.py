"""v256 (PM, Sonarr's search tiers): an automatic TV search asks each tracker by the show's id first
(tvdb, imdb or tmdb, where the tracker's caps allow) and by title only where the id search found
nothing; no alternative-title retries. The owner's manual searches are unchanged. Every tracker is a fake."""
import json

import pytest

from app import tracker_clients, tv_search
from app.tracker_http import Response
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401

TZ = {'id': 2, 'name': 'Indexer', 'type': 'torznab', 'url': 'https://idx.example.org', 'api_key': 'k'}
IPT = {'id': 1, 'name': 'IPT', 'type': 'iptorrents', 'url': 'https://iptorrents.com'}
BHD = {'id': 3, 'name': 'BHD', 'type': 'beyondhd', 'url': 'https://beyond-hd.me', 'api_key': 'a', 'rss_key': 'r'}
AVZ = {'id': 4, 'name': 'AvistaZ', 'type': 'avistaz', 'url': 'https://avistaz.to'}
SHOW = {'id': 1, 'title': 'Ghosts (US)', 'series_type': 'standard', 'aliases': '["Ghosts: Américain"]',
        'tmdb_id': 95396, 'tvdb_id': 371980, 'imdb_id': 'tt11379026'}
T0 = 2_000_000_000.0


class Eng:
    def __init__(self):
        self.calls, self.by_id, self.by_title = [], {}, {}

        class Tok:
            def flush(self):
                pass
        self.tokens = Tok()

    def search_one(self, cfg, query, cats, limit, interactive, purpose=None, tv_ids=None, **k):
        self.calls.append((cfg['id'], query, bool(tv_ids)))
        if tv_ids:
            ans = self.by_id.get(cfg['id'], [])
            if ans == 'no-id':
                raise tracker_clients.NoIdSearch()
            return [{'title': t} for t in ans]
        return [{'title': t} for t in self.by_title.get(cfg['id'], [])]

    def to_item(self, cfg, r, sv):
        return {'title': r['title'], 'downloadUrl': '/x', 'indexerId': cfg['id']}


@pytest.fixture
def engine(monkeypatch, tv_env):  # noqa: F811
    from app import tracker_search
    e = Eng()
    monkeypatch.setattr(tracker_search, 'get_search', lambda: e)
    monkeypatch.setattr(tracker_search, '_match_categories', lambda cfg, cats: cats)
    monkeypatch.setattr(tracker_search, '_secret_values', lambda cfg: set())
    tracker_clients.clear_caps()
    yield e
    tracker_clients.clear_caps()


def test_id_first_and_title_only_where_the_id_search_found_nothing(engine):
    engine.by_id = {2: ['Ghosts.US.S05E03.1080p.WEB-A'], 3: []}
    engine.by_title = {3: ['Ghosts.US.S05E03.720p.WEB-B']}
    items, st, n = tv_search._ask(SHOW, 'Ghosts US S05E03', 'episode', 1, False, T0, [IPT, TZ, BHD])
    assert engine.calls == [(2, 'Ghosts US S05E03', True), (3, 'Ghosts US S05E03', True),
                            (1, 'Ghosts US S05E03', False), (3, 'Ghosts US S05E03', False)]
    assert sorted(i['title'] for i in items) == ['Ghosts.US.S05E03.1080p.WEB-A', 'Ghosts.US.S05E03.720p.WEB-B']
    assert n == 2 and sorted(s['id'] for s in st) == [1, 2, 3] and all(s['status'] == 'ok' for s in st)


def test_a_tracker_that_cannot_search_by_id_gets_the_title_search(engine):
    engine.by_id = {2: 'no-id'}
    tv_search._ask(SHOW, 'Ghosts US S05', 'season', 1, False, T0, [TZ])
    assert engine.calls == [(2, 'Ghosts US S05', True), (2, 'Ghosts US S05', False)]


def test_no_alternative_titles_in_automatic_searches(engine):
    for purpose in ('episode', 'season', tv_search.BACKLOG, tv_search.AUTOMATION):
        engine.calls.clear()
        tv_search._ask(SHOW, 'Ghosts US S05E03', purpose, 1, False, T0, [IPT])
        assert engine.calls == [(1, 'Ghosts US S05E03', False)]


def test_manual_searches_are_unchanged_title_then_alternative_titles(engine):
    tv_search._ask(SHOW, 'Ghosts US S05E03', 'manual', 1, True, T0, [TZ])
    assert engine.calls == [(2, 'Ghosts US S05E03', False), (2, 'Ghosts S05E03', False),
                            (2, 'Ghosts Americain S05E03', False)]
    engine.calls.clear()
    tv_search._ask(SHOW, 'Ghosts US S05E03', tv_search.MANUAL_BACKGROUND, 1, False, T0, [TZ])
    assert not any(c[2] for c in engine.calls)                    # the owner's monitor click: title only


def test_daily_date_queries_and_shows_without_ids_go_by_title(engine):
    daily = dict(SHOW, series_type='daily')
    tv_search._ask(daily, 'Ghosts US S05E03', 'episode', 1, False, T0, [TZ])
    tv_search._ask(SHOW, 'Ghosts US 2026.10.21', 'episode', 1, False, T0, [TZ])
    assert not any(c[2] for c in engine.calls)
    assert tv_search._tv_ids(SHOW, 'Ghosts US S05E03') == {'tvdb_id': 371980, 'imdb_id': 'tt11379026',
                                                           'tmdb_id': 95396, 'season': 5, 'episode': 3}
    assert tv_search._tv_ids(SHOW, 'Ghosts US S05') == {'tvdb_id': 371980, 'imdb_id': 'tt11379026',
                                                        'tmdb_id': 95396, 'season': 5}


# ------------------------------------------------------------------ the clients

CAPS = ('<?xml version="1.0"?><caps><searching><search available="yes" supportedParams="q"/>'
        '<tv-search available="yes" supportedParams="{}"/></searching></caps>')
RSS = ('<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
       '<item><title>Ghosts.US.S05E03.1080p.WEB-A</title><link>https://idx.example.org/d/1</link></item></channel></rss>')


def fake_torznab(params_caps):
    sent = []

    def req(method, url, params=None, **k):
        sent.append(dict(params or {}))
        body = CAPS.format(params_caps) if (params or {}).get('t') == 'caps' else RSS
        return Response(200, {}, body.encode())
    return sent, req


IDS = {'tvdb_id': 371980, 'imdb_id': 'tt11379026', 'tmdb_id': 95396, 'season': 5, 'episode': 3}


def test_torznab_id_search_uses_the_caps_and_reads_them_once_a_day():
    tracker_clients.clear_caps()
    sent, req = fake_torznab('q,season,ep,tvdbid,imdbid')
    assert tracker_clients.tv_id_possible(TZ, IDS)                     # caps not read yet
    out = tracker_clients.search(TZ, 'Ghosts US S05E03', [5000], 100, req, tv_ids=IDS)
    assert len(out) == 1 and [p['t'] for p in sent] == ['caps', 'tvsearch']
    p = sent[1]
    assert p['tvdbid'] == '371980' and p['season'] == 5 and p['ep'] == 3
    assert 'q' not in p and 'imdbid' not in p and 'tmdbid' not in p   # one id, the best the caps allow
    tracker_clients.search(TZ, 'Ghosts US S05', [5000], 100, req, tv_ids=dict(IDS, episode=None))
    assert [p['t'] for p in sent] == ['caps', 'tvsearch', 'tvsearch'] and 'ep' not in sent[2]
    tracker_clients.clear_caps()


def test_torznab_imdb_id_without_tt_and_no_id_caps_mean_title():
    tracker_clients.clear_caps()
    sent, req = fake_torznab('q,season,ep,imdbid')
    tracker_clients.search(TZ, 'x', [5000], 100, req, tv_ids=IDS)
    assert sent[1]['imdbid'] == '11379026'
    tracker_clients.clear_caps()
    sent, req = fake_torznab('q,season,ep')
    with pytest.raises(tracker_clients.NoIdSearch):
        tracker_clients.search(TZ, 'x', [5000], 100, req, tv_ids=IDS)
    assert [p['t'] for p in sent] == ['caps']
    assert not tracker_clients.tv_id_possible(TZ, IDS)                 # known now: no guard token spent on it
    tracker_clients.clear_caps()


def test_unreadable_caps_mean_title_for_six_hours():
    tracker_clients.clear_caps()

    def req(method, url, params=None, **k):
        return Response(200, {}, b'not xml')
    with pytest.raises(tracker_clients.NoIdSearch):
        tracker_clients.search(TZ, 'x', [5000], 100, req, tv_ids=IDS)
    assert not tracker_clients.tv_id_possible(TZ, IDS)
    tracker_clients.clear_caps()


def test_beyondhd_id_tier_sends_the_tmdb_attempt_alone():
    bodies = []

    def req(method, url, json=None, **k):
        bodies.append(json)
        return Response(200, {}, b'{"status_code": 1, "success": true, "results": [], "total_pages": 1}')
    assert tracker_clients.search(BHD, 'Ghosts US S05E03', [5000], 100, req, tv_ids=IDS) == []
    assert len(bodies) == 1 and bodies[0]['tmdb_id'] == 'tv/95396' and bodies[0]['search'] == 'S05E03'
    with pytest.raises(tracker_clients.NoIdSearch):
        tracker_clients.search(BHD, 'x', [5000], 100, req, tv_ids={'season': 5, 'episode': 3, 'tvdb_id': 1})


def test_avistaz_id_tier_needs_an_imdb_or_tmdb_id(monkeypatch):
    sent = []
    monkeypatch.setattr(tracker_clients, '_avz_get', lambda cfg, req, params: (
        sent.append(dict(params)), Response(200, {}, json.dumps({'data': []}).encode()))[1])
    tracker_clients.search(AVZ, 'x', [5000], 100, None, tv_ids=IDS)
    assert sent[0]['imdb'] == 'tt11379026' and sent[0]['search'] == 'S05E03'
    with pytest.raises(tracker_clients.NoIdSearch):
        tracker_clients.search(AVZ, 'x', [5000], 100, None, tv_ids={'season': 5, 'tvdb_id': 1})
    assert not tracker_clients.tv_id_possible(IPT, IDS)
