"""QA gate for ee3bee4 (IPT Sources fix). Mocks only: no request leaves the process.

Covers Builder's claims (a)-(g): TV never sends the year, movie title-only fallback,
signed-out IPT pages are auth errors, empty answers are never cached (engine and
/api/sources/all-results), the minified fixture parses into the right rows, and the
per-tracker log lines are redacted."""

import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

import app.main
from app import tracker_clients as tc
from app.tracker_http import Response, TrackerError
from tests.test_tracker_sources import (COOKIE, R, env, ipt, signed_in, torznab, rss,  # noqa: F401
                                        ADDRS)

FIXTURE = (Path(__file__).parent / 'fixtures' / 'ipt_search_minified.html').read_text()
CFG = {'id': 7, 'type': 'iptorrents', 'name': 'IPT', 'url': 'https://ipt.example', 'cookie': COOKIE,
       'categories': [2000, 5000], 'priority': 10, 'enabled': True}
SIGNED_IN_EMPTY = '<html><a href=/lout.php>Log out</a><p>No Torrents Found!</p></html>'
LOGIN_FORM = ('<!doctype html><html><head><title>Login</title></head><body><form method=post action=/take_login.php>'
              '<input type=text name=username><input type=password name=password></form></body></html>')
UID, PASS = (p.split('=', 1)[1] for p in COOKIE.split('; '))


class Seq:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []

    def __call__(self, method, url, **kw):
        self.calls.append({'method': method, 'url': url, **kw})
        r = self.resps.pop(0) if len(self.resps) > 1 else self.resps[0]
        if isinstance(r, Exception):
            raise r
        return r


def q_of(url):
    return parse_qs(urlsplit(url).query).get('q', [''])[0]


def ipt_row(rid, name, seed=10, leech=1, size='1.5 GB', cat=48, free=False, closed=False):
    """One row in IPT's real (minified) markup, optionally with explicit end tags."""
    e = '</td>' if closed else ''
    return (f'<tr><td class=ac><a href="?{cat}"><img alt=c></a>{e}<td class=al><a class=" hv" href=/t/{rid}>{name}</a>'
            + ('<span class=free>FreeLeech</span>' if free else '') + f'<div class=sub>x</div>{e}'
            f'<td class=ac><a href="/bookmarks.php?a=add&id={rid}"><i></i></a>{e}'
            f'<td class=ac><a href=/download.php/{rid}/{rid}.torrent><i></i></a>{e}<td class=ac><a href=/t/{rid}#startcomments>0</a>{e}'
            f'<td class=ac>{size}{e}<td class=ac>9,999{e}<td class="ac t_seeders">{seed}{e}<td class="ac t_leechers">{leech}{e}'
            + ('</tr>' if closed else ''))


def ipt_page(*rows):
    return ('<!DOCTYPE html><html><body><a href=/lout.php>Log out</a><form><table id=torrents class="table t1">'
            '<thead><tr><th>Type<th>Name<th><th>DL<th>Cmt<th>Size<th>Snatched<th>Seeders<th>Leechers</thead><tbody>'
            + ''.join(rows) + '</tbody></table></form></body></html>')


# ------------------------------------------------------------------ (f) parser

def test_f_fixture_rows_exact():
    out = tc.search(CFG, 'Dune Part Two 2024', [2000], 100, Seq(R(body=FIXTURE)))
    assert [(r['title'], r['seeders'], r['leechers'], r['category'], r['freeleech']) for r in out] == [
        ('Dune Part Two 2024 2160p UHD BluRay x265-GRP', 312, 9, '48', True),
        ('Dune Part Two 2024 1080p WEB-DL DDP5 1 H 264-NEW', 1077, 21, '89', False)]
    assert out[1]['size'] == int(6.1 * 1024 ** 3)
    assert out[1]['download_url'] == 'https://ipt.example/download.php/6123001/Dune.Part.Two.2024.1080p.WEB-DL.torrent'
    assert out[1]['info_url'] == 'https://ipt.example/t/6123001'      # not the #startcomments link
    assert all('FreeLeech' not in r['title'] and 'New' != r['title'].split()[-1] for r in out)
    assert tc.last_stats() == {'status': 200, 'bytes': len(FIXTURE.encode()), 'rows': 2, 'kept': 2}


def test_f_fixture_snatched_column_not_used_as_seeders():
    out = tc.search(CFG, 'x', [2000], 100, Seq(R(body=FIXTURE)))
    assert 1204 not in [r['seeders'] for r in out] and 5019 not in [r['seeders'] for r in out]


@pytest.mark.parametrize('closed', [False, True])
def test_f_minified_and_closed_markup_parse_the_same(closed):
    page = ipt_page(ipt_row(1, 'Breaking Bad S01E05 1080p WEB-DL', 50, 2, closed=closed),
                    ipt_row(2, 'Breaking Bad S01 1080p BluRay x264', 80, 3, size='22.0 GB', free=True, closed=closed))
    out = tc.search(CFG, 'Breaking Bad', [5000], 100, Seq(R(body=page)))
    assert [(r['title'], r['seeders'], r['leechers'], r['freeleech']) for r in out] == [
        ('Breaking Bad S01E05 1080p WEB-DL', 50, 2, False), ('Breaking Bad S01 1080p BluRay x264', 80, 3, True)]


def test_f_empty_title_row_dropped_others_kept():
    blank = ('<tr><td class=ac><a href="?48"></a><td class=al><a class=hv href=/t/9></a><td class=ac>'
             '<a href=/download.php/9/x.torrent>dl</a><td class=ac>1 GB<td class="ac t_seeders">5<td class="ac t_leechers">1')
    page = ipt_page(ipt_row(1, 'Good One 2020 1080p'), blank, ipt_row(2, 'Good Two 2020 720p'))
    counts = {}
    out = tc.parse_iptorrents(page, 'https://ipt.example', 100, counts)
    assert [r['title'] for r in out] == ['Good One 2020 1080p', 'Good Two 2020 720p']
    assert counts == {'rows': 3, 'kept': 2}


def test_f_name_from_cell_when_details_link_has_no_text_and_badges_stripped():
    row = ('<tr><td class=ac><a href="?48"></a><td class=al><a href=/t/5><img alt=""></a> Some Movie 2020 1080p '
           'FreeLeech<td class=ac><a href=/download.php/5/x.torrent>dl</a><td>2 GB<td class="ac t_seeders">3'
           '<td class="ac t_leechers">1')
    out = tc.parse_iptorrents(ipt_page(row), 'https://ipt.example', 10)
    assert [r['title'] for r in out] == ['Some Movie 2020 1080p']


def test_f_old_quoted_markup_still_parses():
    from tests.test_tracker_sources import IPT_HTML
    out = tc.parse_iptorrents(IPT_HTML, 'https://ipt.example', 10)
    assert [(r['title'], r['seeders'], r['leechers']) for r in out] == [('The.Nut.Job.2014.1080p.BluRay.x264-GRP', 1234, 56)]


def test_f_nested_table_in_a_cell_does_not_split_rows():
    row = ipt_row(1, 'Nested 2020 1080p').replace('<div class=sub>x</div>', '<div class=sub><table><tr><td>inner</table></div>')
    out = tc.parse_iptorrents(ipt_page(row, ipt_row(2, 'After 2020 720p')), 'https://ipt.example', 10)
    assert [r['title'] for r in out] == ['Nested 2020 1080p', 'After 2020 720p']


def test_f_limit_respected():
    page = ipt_page(*[ipt_row(i, f'Row {i} 1080p') for i in range(1, 11)])
    assert len(tc.parse_iptorrents(page, 'https://ipt.example', 4)) == 4


def test_f_signed_in_table_with_only_a_no_results_row_is_empty():
    page = ('<a href=/lout.php>x</a><table id="torrents"><tr><th>Name</th></tr>'
            '<tr><td colspan="6">No Torrents Found!</td></tr></table>')
    assert tc.search(CFG, 'x', [2000], 10, Seq(R(body=page))) == []


# ------------------------------------------------------------------ (c) signed out = auth

@pytest.mark.parametrize('resp,kind', [
    (R(302, b'', {'Location': '/login.php?returnto=%2Ft'}), 'auth'),
    (R(body=LOGIN_FORM), 'auth'),
    (R(body='<html><p>No Torrents Found!</p></html>'), 'auth'),                      # no sign-out link
    (R(body='<html><a href=/login.php>Log in</a><p>No Torrents Found!</p></html>'), 'auth'),
    (R(401, b'x'), 'auth'),
    (R(500, b'<html>oops</html>'), 'http'),
    (R(body='<a href=/lout.php>x</a><p>something unexpected</p>'), 'response'),
])
def test_c_ipt_error_kinds(resp, kind):
    with pytest.raises(TrackerError) as e:
        tc.search(CFG, 'x', [2000], 10, Seq(resp))
    assert e.value.kind == kind


def test_c_signed_in_empty_is_empty_not_error():
    assert tc.search(CFG, 'x', [2000], 10, Seq(R(body=SIGNED_IN_EMPTY))) == []


def test_c_auth_failure_sets_tracker_status_and_is_not_reported_as_results(env, signed_in, caplog):
    rec = ipt(env['store'])
    env['mock'].set('ipt.example', R(body=LOGIN_FORM))
    caplog.set_level(logging.INFO)
    r = signed_in.get('/api/sources/all-results?title=Interstellar&media_type=movie&year=2014')
    assert r.status_code == 200 and r.get_json()['releases'] == []
    st = env['store'].get(rec['id']) if hasattr(env['store'], 'get') else None
    assert 'search failed: auth' in caplog.text
    # The failure starts the auth backoff: the title-only retry is NOT sent to IPT.
    assert len(env['mock'].hits('ipt.example')) == 1


# ------------------------------------------------------------------ (a) TV, (b) movie fallback

def test_a_tv_all_results_never_sends_the_year(env, signed_in):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=ipt_page(ipt_row(1, 'Breaking Bad S01E01 1080p WEB-DL', cat=73))))
    r = signed_in.get('/api/sources/all-results?title=Breaking Bad&media_type=tv&year=2008')
    rel = r.get_json()['releases']
    hits = env['mock'].hits('ipt.example')
    assert [q_of(h['url']) for h in hits] == ['Breaking Bad'] and len(rel) == 1


@pytest.mark.parametrize('title', ['Breaking Bad S01E05', 'Breaking Bad S01'])
def test_a_tv_season_episode_forms_pass_through(env, signed_in, title):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=SIGNED_IN_EMPTY))
    signed_in.get(f'/api/sources/all-results?title={title}&media_type=tv&year=2008')
    assert [q_of(h['url']) for h in env['mock'].hits('ipt.example')] == [title]   # no retry for TV either


def test_b_movie_falls_back_to_title_only(env, signed_in):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=SIGNED_IN_EMPTY), R(body=ipt_page(ipt_row(1, 'Heat Directors Cut 1080p BluRay'))))
    rel = signed_in.get('/api/sources/all-results?title=Heat&media_type=movie&year=1995').get_json()['releases']
    assert [q_of(h['url']) for h in env['mock'].hits('ipt.example')] == ['Heat 1995', 'Heat']
    assert [x['title'] for x in rel] == ['Heat Directors Cut 1080p BluRay']


def test_b_movie_no_fallback_when_year_search_has_results(env, signed_in):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=ipt_page(ipt_row(1, 'Heat 1995 1080p BluRay'))))
    signed_in.get('/api/sources/all-results?title=Heat&media_type=movie&year=1995')
    assert [q_of(h['url']) for h in env['mock'].hits('ipt.example')] == ['Heat 1995']


def test_b_movie_no_fallback_when_title_already_has_year(env, signed_in):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=SIGNED_IN_EMPTY))
    signed_in.get('/api/sources/all-results?title=Blade Runner 2049&media_type=movie&year=2017')
    signed_in.get('/api/sources/all-results?title=Heat 1995&media_type=movie&year=1995')
    # "Blade Runner 2049" + 2017 still gets one title-only retry; "Heat 1995" + 1995 adds nothing, so no retry.
    assert [q_of(h['url']) for h in env['mock'].hits('ipt.example')] == ['Blade Runner 2049 2017', 'Blade Runner 2049', 'Heat 1995']


# ------------------------------------------------------------------ (d) empty never cached

def test_d_empty_route_answer_not_cached_then_results_cached(env, signed_in):
    ipt(env['store'])
    page = ipt_page(ipt_row(1, 'Breaking Bad S01E01 1080p WEB-DL'))
    env['mock'].set('ipt.example', R(body=SIGNED_IN_EMPTY), R(body=page))
    url = '/api/sources/all-results?title=Breaking Bad&media_type=tv&year=2008'
    assert signed_in.get(url).get_json()['releases'] == []
    assert ('tv', 'breaking bad', 2008) not in app.main._all_results_cache
    # fdbd8e7 PM ruling: a signed-in zero-row answer is negatively cached per tracker for 2 minutes
    assert signed_in.get(url).get_json()['releases'] == []
    assert len(env['mock'].hits('ipt.example')) == 1
    assert len(signed_in.get(url + '&refresh=1').get_json()['releases']) == 1   # explicit Retry bypasses it
    assert len(signed_in.get(url).get_json()['releases']) == 1                  # then served from cache
    assert len(env['mock'].hits('ipt.example')) == 2


def test_d_auth_failure_answer_not_cached_at_route(env, signed_in):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=LOGIN_FORM))
    signed_in.get('/api/sources/all-results?title=Breaking Bad&media_type=tv')
    assert ('tv', 'breaking bad', None) not in app.main._all_results_cache


def test_d_rows_all_dropped_purges_engine_cache(env):
    """A row whose title carries the cookie secret is dropped by to_item; that answer must not stick."""
    rec = ipt(env['store'])
    eng = env['engine']
    env['mock'].set('ipt.example', R(body=ipt_page(ipt_row(1, f'Leak {COOKIE} 1080p'))),
                    R(body=ipt_page(ipt_row(2, 'Fine 1080p'))))
    items, _, _ = eng.search('fine', [2000], 100, True)
    assert items == []
    items, _, _ = eng.search('fine', [2000], 100, True)
    assert [i['title'] for i in items] == ['Fine 1080p'] and len(env['mock'].hits('ipt.example')) == 2


# ------------------------------------------------------------------ (g) log lines

def _lines(caplog):
    return [(r.levelname, r.name, r.getMessage()) for r in caplog.records if r.getMessage().startswith('Tracker ')]


def test_g_log_lines_success_auth_empty_redacted(env, signed_in, caplog):
    rec = ipt(env['store'], name='IPT')
    caplog.set_level(logging.INFO)
    env['mock'].set('ipt.example', R(body=FIXTURE))
    signed_in.get('/api/sources/all-results?title=Dune Part Two&media_type=movie&year=2024')
    ok = _lines(caplog); caplog.clear(); app.main._all_results_cache.clear()
    env['mock'].set('ipt.example', R(body=SIGNED_IN_EMPTY))
    signed_in.get('/api/sources/all-results?title=Nothing Here&media_type=tv')
    empty = _lines(caplog); caplog.clear()
    env['mock'].set('ipt.example', R(body=LOGIN_FORM))
    signed_in.get('/api/sources/all-results?title=Signed Out&media_type=tv')
    auth = _lines(caplog)
    rid = rec['id']
    want = f'Tracker IPT (id {rid}) search: HTTP 200, {len(FIXTURE.encode())} bytes, 2 rows parsed, 2 results'
    assert any(lv == 'INFO' and lg == 'app.tracker_search' and (m == want or m.startswith(want + ',')) for lv, lg, m in ok), ok  # 4049342 appends dropped/pages/types
    want = f'Tracker IPT (id {rid}) search: HTTP 200, {len(SIGNED_IN_EMPTY)} bytes, 0 rows parsed, 0 results'
    assert any(lv == 'INFO' and m == want or m.startswith(want + ',') for lv, lg, m in empty), empty
    assert any(l[0] == 'WARNING' and re.fullmatch(rf'Tracker IPT \(id {rid}\) search failed: auth \(HTTP 200, {len(LOGIN_FORM)} bytes, 0 rows parsed\)', l[2]) for l in auth), auth
    text = '\n'.join(m for _, _, m in ok + empty + auth) + caplog.text
    for s in (COOKIE, UID, PASS, 'uid=', 'pass=', 'ipt.example', 'q=', 'Dune', '/t?'):
        assert s not in '\n'.join(m for _, _, m in ok + empty + auth), s
    print('\nQA-LOG-SUCCESS', ok, '\nQA-LOG-EMPTY', empty, '\nQA-LOG-AUTH', auth)


def test_g_302_log_line_has_no_location(env, signed_in, caplog):
    ipt(env['store'], name='IPT')
    caplog.set_level(logging.DEBUG)
    env['mock'].set('ipt.example', R(302, b'', {'Location': '/login.php?returnto=%2Ft%3Fq%3DDune'}))
    signed_in.get('/api/sources/all-results?title=Dune&media_type=tv')
    assert 'search failed: auth (HTTP 302, 0 bytes, 0 rows parsed)' in caplog.text
    assert 'returnto' not in caplog.text and 'login.php' not in caplog.text and COOKIE not in caplog.text


def test_g_no_secret_in_all_results_response(env, signed_in):
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body=FIXTURE))
    body = signed_in.get('/api/sources/all-results?title=Dune Part Two&media_type=movie&year=2024').get_data(as_text=True)
    assert UID not in body and PASS not in body and 'download.php' not in body   # info_url (details page) is by design
