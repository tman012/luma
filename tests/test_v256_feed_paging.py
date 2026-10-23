"""v256 (PM, Sonarr's RSS sync): the 15-min feed pages back (Torznab/Newznab offset) until it reaches
the last release it saw on that tracker, at most FEED_MAX_PAGES pages; a gap is logged when it can't.
A feed poll is never answered from the search cache. Every tracker here is a fake."""
import json
import logging
from email.utils import formatdate

import pytest

from app import tracker_clients, tracker_types, tv_search, tv_store
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401

TZ = {'id': 2, 'name': 'Indexer', 'type': 'torznab', 'url': 'https://idx.example.org'}
IPT = {'id': 1, 'name': 'IPT', 'type': 'iptorrents', 'url': 'https://iptorrents.com'}
T0 = 2_000_000_000.0


def row(n):
    """Release n: higher n is newer (published n minutes after T0)."""
    return {'title': f'Show.S01E{n:02d}.1080p.WEB-X', 'guid': f'https://idx.example.org/d/{n}?apikey=SECRET',
            'download_url': f'https://idx.example.org/d/{n}', 'pub_date': formatdate(T0 + n * 60)}


class Eng:
    """Serves a feed newest-first, LIMIT rows per offset page."""
    def __init__(self, newest):
        self.newest, self.calls, self.fail_at = newest, [], None

        class Tok:
            def flush(self):
                pass
        self.tokens = Tok()

    def search_one(self, cfg, query, cats, limit, interactive, purpose=None, offset=0, **k):
        self.calls.append((cfg['id'], query, purpose, offset))
        if self.fail_at is not None and offset >= self.fail_at:
            from app.tracker_search import TrackerSkipped
            raise TrackerSkipped('rate limited')
        ns = list(range(self.newest - offset, max(0, self.newest - offset - limit), -1))
        return [row(n) for n in ns]

    def to_item(self, cfg, r, sv):
        return {'title': r['title'], 'downloadUrl': '/x', 'indexerId': cfg['id']}


@pytest.fixture
def engine(monkeypatch, tv_env):  # noqa: F811
    from app import tracker_search
    e = Eng(newest=150)
    monkeypatch.setattr(tracker_search, 'get_search', lambda: e)
    monkeypatch.setattr(tracker_search, '_match_categories', lambda cfg, cats: cats)
    monkeypatch.setattr(tracker_search, '_secret_values', lambda cfg: set())
    return e


def test_first_poll_reads_one_page_and_remembers_the_newest_without_its_link(engine):
    items, st = tv_search.query_trackers('', 'feed', None, False, T0, [TZ])
    assert engine.calls == [(2, '', 'feed', 0)] and len(items) == 100
    assert st[0]['status'] == 'ok' and st[0]['pages'] == 1 and not st[0]['gap']
    raw = tv_store.get_meta('tv_feed_last:2')
    mark = json.loads(raw)
    assert mark['ts'] == T0 + 150 * 60 and 'SECRET' not in raw and 'idx.example.org' not in raw


def test_pages_back_until_the_last_release_seen(engine):
    tv_search.query_trackers('', 'feed', None, False, T0, [TZ])
    engine.calls.clear()
    engine.newest = 400                                   # 250 new releases since the last poll
    items, st = tv_search.query_trackers('', 'feed', None, False, T0 + 900, [TZ])
    assert [c[3] for c in engine.calls] == [0, 100, 200]  # page 3 holds release 150
    assert st[0]['pages'] == 3 and not st[0]['gap'] and len(items) == 300
    engine.calls.clear()
    tv_search.query_trackers('', 'feed', None, False, T0 + 1800, [TZ])   # nothing new: one page
    assert [c[3] for c in engine.calls] == [0]


def test_page_limit_then_a_gap_is_logged(engine, caplog):
    tv_search.query_trackers('', 'feed', None, False, T0, [TZ])
    engine.calls.clear()
    engine.newest = 150 + 100 * tv_search.FEED_MAX_PAGES + 50
    with caplog.at_level(logging.WARNING, logger='app.tv_search'):
        items, st = tv_search.query_trackers('', 'feed', None, False, T0 + 900, [TZ])
    assert len(engine.calls) == tv_search.FEED_MAX_PAGES == 10
    assert st[0]['gap'] and 'did not reach the last release seen' in caplog.text
    assert 'SECRET' not in caplog.text
    assert json.loads(tv_store.get_meta('tv_feed_last:2'))['ts'] == T0 + engine.newest * 60   # moves on


def test_a_refused_later_page_stops_paging_and_logs_the_gap(engine, caplog):
    tv_search.query_trackers('', 'feed', None, False, T0, [TZ])
    engine.newest, engine.fail_at = 600, 200
    with caplog.at_level(logging.WARNING, logger='app.tv_search'):
        items, st = tv_search.query_trackers('', 'feed', None, False, T0 + 900, [TZ])
    assert st[0]['status'] == 'ok' and st[0]['gap'] and len(items) == 200
    assert 'stopped' in caplog.text


def test_a_refused_first_page_is_a_failed_poll(engine):
    engine.fail_at = 0
    items, st = tv_search.query_trackers('', 'feed', None, False, T0, [TZ])
    assert items == [] and st[0]['status'] != 'ok'
    assert tv_store.get_meta('tv_feed_last:2') is None


def test_site_trackers_answer_one_page_and_log_a_gap(engine, caplog):
    assert not tracker_types.pages_feed('iptorrents') and tracker_types.pages_feed('newznab')
    tv_search.query_trackers('', 'feed', None, False, T0, [IPT])
    engine.calls.clear()
    engine.newest = 400
    with caplog.at_level(logging.WARNING, logger='app.tv_search'):
        items, st = tv_search.query_trackers('', 'feed', None, False, T0 + 900, [IPT])
    assert [c[3] for c in engine.calls] == [0] and st[0]['gap']


def test_movie_feed_keeps_its_own_marker(engine):
    tv_search.query_trackers('', 'feed', None, False, T0, [TZ], categories=[2000], section='movies')
    assert tv_store.get_meta('movies_feed_last:2') and tv_store.get_meta('tv_feed_last:2') is None


RSS = ('<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
       '<item><title>Show.S01E01.1080p.WEB-X</title><link>https://tz.example/d/1</link></item></channel></rss>')


def test_torznab_sends_offset_only_past_the_first_page():
    from app.tracker_http import Response, TrackerError
    sent = []

    def req(method, url, params=None, **k):
        sent.append(dict(params or {}))
        return Response(200, {}, RSS.encode())
    tracker_clients.search(TZ, '', [5000], 100, req)
    tracker_clients.search(TZ, '', [5000], 100, req, offset=200)
    assert 'offset' not in sent[0] and sent[1]['offset'] == 200 and sent[1]['limit'] == 100
    with pytest.raises(TrackerError):
        tracker_clients.search(IPT, '', [5000], 100, req, offset=100)   # a site tracker is never paged
    assert len(sent) == 2


def test_a_feed_poll_is_never_answered_from_the_cache(tmp_path):
    from app.tracker_http import Response
    from tests.test_tracker_negative_cache import Mock, _engine
    mock = Mock(Response(200, {}, RSS.encode()))
    eng, cfg = _engine(tmp_path, mock, 'torznab')
    assert len(eng.search_one(cfg, '', [5000], 100, False, purpose='feed')) == 1
    assert len(eng.search_one(cfg, '', [5000], 100, False, purpose='feed')) == 1
    assert len(mock.calls) == 2                                   # both polls asked the tracker
    eng.search_one(cfg, '', [5000], 100, False, purpose='feed', offset=100)
    assert len(mock.calls) == 3
    eng.search_one(cfg, 'Show S01E01', [5000], 100, False)
    eng.search_one(cfg, 'Show S01E01', [5000], 100, False)
    assert len(mock.calls) == 4                                   # a background search still caches
