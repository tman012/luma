"""IPT on :5002 returned no Sources: the results table parsed to rows with
download links but no titles (IPT's minified markup leaves out </td>), every
row was dropped after its release token was issued, and the dropped answer
was cached so repeat searches never reached IPT and logged nothing."""

import logging
from pathlib import Path

import pytest

from app import tracker_clients as tc
from app.tracker_http import Response, TrackerError
from app.tracker_search import LumaTrackersClient, TrackerSearch, ReleaseTokens
from app.tracker_guard import TrackerGuard
from app.tracker_store import TrackerStore

FIXTURE = (Path(__file__).parent / 'fixtures' / 'ipt_search_minified.html').read_text()
COOKIE = 'uid=1234567; pass=0123456789abcdef0123456789abcdef'


def R(status=200, body=b'', headers=None):
    return Response(status, headers or {}, body.encode() if isinstance(body, str) else body)


class Mock:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []

    def __call__(self, method, url, **kw):
        self.calls.append({'method': method, 'url': url, **kw})
        return self.resps.pop(0) if len(self.resps) > 1 else self.resps[0]


def cfg():
    return {'id': 1, 'type': 'iptorrents', 'name': 'IPT', 'url': 'https://ipt.example',
            'cookie': COOKIE, 'categories': [2000, 5000], 'priority': 25, 'enabled': True}


def test_minified_ipt_page_parses_every_row():
    out = tc.search(cfg(), 'Dune Part Two 2024', [2000, 5000], 100, Mock(R(body=FIXTURE)))
    assert [r['title'] for r in out] == ['Dune Part Two 2024 2160p UHD BluRay x265-GRP',
                                         'Dune Part Two 2024 1080p WEB-DL DDP5 1 H 264-NEW']
    a, b = out
    assert (a['seeders'], a['leechers'], a['size']) == (312, 9, int(18.4 * 1024 ** 3))
    assert a['freeleech'] and not b['freeleech'] and (b['seeders'], b['leechers']) == (1077, 21)
    assert a['category'] == '48' and a['info_url'] == 'https://ipt.example/t/6123456'
    assert a['download_url'].startswith('https://ipt.example/download.php/6123456/')
    assert tc.last_stats()['rows'] == 2 and tc.last_stats()['kept'] == 2


@pytest.fixture
def engine(tmp_path):
    store = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'k.key')
    rec, err = store.create({'type': 'iptorrents', 'name': 'IPT', 'url': 'https://iptorrents.com',
                             'cookie': COOKIE, 'categories': [2000, 5000], 'priority': 25})
    assert not err
    guard = TrackerGuard(tmp_path / 'guard.json')
    guard.wait_spacing = lambda gk: None
    mock = Mock(R(body=FIXTURE))
    eng = TrackerSearch(store=store, guard=guard, request=mock,
                        tokens=ReleaseTokens(store, tmp_path / 'rel.json'))
    return eng, mock


def test_sources_search_all_returns_ipt_rows(engine, caplog):
    eng, mock = engine
    caplog.set_level(logging.INFO)
    out = LumaTrackersClient(eng).search_all('Dune Part Two', 'movie', 2024)
    assert len(out) == 2 and out[0]['tracker'] == 'IPT'
    assert all(o['download_url'].startswith('/api/trackers/release/') for o in out)
    line = next(r.getMessage() for r in caplog.records if 'rows parsed' in r.getMessage())
    assert 'HTTP 200' in line and '2 rows parsed' in line and '2 results' in line
    assert COOKIE not in caplog.text and 'pass=' not in caplog.text and '?q=' not in caplog.text


def test_signed_out_page_is_auth_not_empty():
    signed_out = R(body='<html><body><p>No Torrents Found!</p><a href=/login.php>Log in</a></body></html>')
    with pytest.raises(TrackerError) as e:
        tc.search(cfg(), 'x', [2000], 10, Mock(signed_out))
    assert e.value.kind == 'auth'
    with pytest.raises(TrackerError) as e:
        tc.test(cfg(), Mock(signed_out))
    assert e.value.kind == 'auth'
    login = R(body='<form action=/do-login.php><input name=username><input type=password name=password></form>')
    with pytest.raises(TrackerError) as e:
        tc.test(cfg(), Mock(login))
    assert e.value.kind == 'auth'


def test_signed_in_empty_page_is_empty():
    page = R(body='<html><a href=/lout.php>Log out</a><p>No Torrents Found!</p></html>')
    assert tc.search(cfg(), 'x', [2000], 10, Mock(page)) == []


def test_rows_without_names_are_a_failure_not_zero_results():
    html = ('<a href=/lout.php>x</a><table id=torrents><tr><td><td><td><a href=/download.php/1/a.torrent>dl</a>'
            '<td>1 GB</table>')
    with pytest.raises(TrackerError) as e:
        tc.search(cfg(), 'x', [2000], 10, Mock(R(body=html)))
    assert e.value.kind == 'response'


def test_failure_logs_one_redacted_warning(engine, caplog):
    eng, mock = engine
    mock.resps = [R(body='<form><input name=username><input name=password></form>')]
    caplog.set_level(logging.INFO)
    assert LumaTrackersClient(eng).search_all('Dune Part Two', 'movie', 2024) == []
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and 'search failed' in r.getMessage()]
    assert len(warns) == 1 and warns[0].startswith('Tracker IPT (id 1) search failed: auth (HTTP 200, ')
    assert COOKIE not in caplog.text and 'iptorrents.com' not in caplog.text


def test_no_results_with_year_retries_once_without_it(engine):
    eng, mock = engine
    empty = R(body='<a href=/lout.php>x</a><p>No Torrents Found!</p>')
    mock.resps = [empty, R(body=FIXTURE)]
    out = LumaTrackersClient(eng).search_all('Dune Part Two', 'movie', 2024)
    assert len(out) == 2 and len(mock.calls) == 2
    assert 'q=Dune+Part+Two+2024' in mock.calls[0]['url'] and 'q=Dune+Part+Two&' in mock.calls[1]['url']


def test_empty_answer_does_not_skip_the_next_search(engine):
    eng, mock = engine
    empty = R(body='<a href=/lout.php>x</a><p>No Torrents Found!</p>')
    mock.resps = [empty, R(body=FIXTURE)]
    c = LumaTrackersClient(eng)
    assert c.search_all('Dune Part Two', 'movie', None) == []
    # PM: an empty answer is cached for 2 minutes; an explicit retry asks again.
    assert len(c.search_all('Dune Part Two', 'movie', None, refresh=True)) == 2
    assert len(mock.calls) == 2


def test_tv_never_sends_the_year(engine):
    eng, mock = engine
    LumaTrackersClient(eng).search_all('Breaking Bad S01E05', 'tv', 2008)
    assert len(mock.calls) == 1
    assert 'q=Breaking+Bad+S01E05&' in mock.calls[0]['url'] and '2008' not in mock.calls[0]['url']
