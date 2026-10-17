"""QA (fdbd8e7) — Reviewer's second IPT batch. Mocks only; fixture style from test_qa_ipt_ee3bee4.

(a) a row whose download or details link points at another host is dropped
(b) a login form PLUS the torrents table (with rows / empty) is an auth error, never negative-cached
(c) a signed-in page (sign-out link) whose text contains 'name=usernames' is NOT an auth error
(d) a banned page that still has the sign-out link: an error ('banned'), not 0 results
(e) /api;/v2.0/indexers/x/results/torznab is refused as a proxy/tracker URL
"""

import logging

import pytest

import app.main
from app import tracker_clients as tc
from app import tracker_store
from app.tracker_http import TrackerError, is_proxy_url
from tests.test_tracker_sources import R, env, ipt, signed_in  # noqa: F401
from tests.test_qa_ipt_ee3bee4 import CFG, LOGIN_FORM, Seq, ipt_page, ipt_row

LOGIN_BITS = ('<form method=post action=/take_login.php><input type=text name=username>'
              '<input type=password name=password></form>')


def _with_login_form(page):
    return page.replace('<body>', '<body>' + LOGIN_BITS, 1)


# ------------------------------------------------------------------ (a)

@pytest.mark.parametrize('which', ['download', 'details'])
def test_a_row_with_off_host_link_is_dropped(which):
    good = ipt_row(1, 'Dune Part Two 2024 1080p WEB-DL')
    bad = ipt_row(2, 'Dune Part Two 2024 2160p UHD BluRay')
    if which == 'download':
        bad = bad.replace('href=/download.php/2/2.torrent', 'href=https://evil.example/download.php/2/2.torrent')
    else:
        bad = bad.replace('href=/t/2>', 'href=https://evil.example/t/2>')
    out = tc.search(CFG, 'Dune Part Two 2024', [2000], 100, Seq(R(body=ipt_page(good, bad))))
    links = [(r['title'], r['download_url'], r.get('info_url'), r.get('guid')) for r in out]
    assert [r['title'] for r in out] == ['Dune Part Two 2024 1080p WEB-DL'], links
    assert not any('evil.example' in str(v) for row in links for v in row), links


# ------------------------------------------------------------------ (b)

@pytest.mark.parametrize('rows', ['with_rows', 'empty_table'])
def test_b_login_form_plus_table_is_auth_error(rows):
    body = ipt_row(1, 'Dune Part Two 2024 1080p WEB-DL') if rows == 'with_rows' else ''
    with pytest.raises(TrackerError) as e:
        tc.search(CFG, 'Dune Part Two 2024', [2000], 100, Seq(R(body=_with_login_form(ipt_page(body)))))
    assert e.value.kind == 'auth'


@pytest.mark.parametrize('rows', ['with_rows', 'empty_table'])
def test_b_login_form_plus_table_never_negative_cached(env, signed_in, rows, caplog):  # noqa: F811
    caplog.set_level(logging.INFO)
    ipt(env['store'])
    body = ipt_row(1, 'Breaking Bad S01E01 1080p WEB-DL') if rows == 'with_rows' else ''
    env['mock'].set('ipt.example', R(body=_with_login_form(ipt_page(body))))
    url = '/api/sources/all-results?title=Breaking Bad&media_type=tv'
    first = signed_in.get(url).get_json()
    assert first['releases'] == [], first          # a signed-out page's rows are not results
    app.main._all_results_cache.clear()
    second = signed_in.get(url).get_json()
    assert second['releases'] == []
    assert 'cached empty answer' not in caplog.text, 'auth page landed in the 2-minute empty-answer cache'
    assert ('tv', 'breaking bad', None) not in app.main._all_results_cache


# ------------------------------------------------------------------ (c)

@pytest.mark.parametrize('kind', ['rows', 'no_results'])
def test_c_signed_in_page_mentioning_name_usernames_is_not_auth(kind):
    if kind == 'rows':
        page = ipt_page(ipt_row(1, 'Dune Part Two 2024 1080p WEB-DL')).replace(
            '</table>', '</table><p>Tip: search name=usernames to find uploaders</p>')
    else:
        page = ('<html><a href=/lout.php>Log out</a><p>Tip: search name=usernames to find uploaders</p>'
                '<p>No Torrents Found!</p></html>')
    out = tc.search(CFG, 'Dune Part Two 2024', [2000], 100, Seq(R(body=page)))
    assert len(out) == (1 if kind == 'rows' else 0)


# ------------------------------------------------------------------ (d)

def test_d_banned_page_with_sign_out_link_is_an_error_not_zero_results():
    page = ('<html><a href=/lout.php>Log out</a><h1>Your account has been banned</h1>'
            '<p>Contact staff.</p></html>')
    with pytest.raises(TrackerError) as e:
        tc.search(CFG, 'Dune Part Two 2024', [2000], 100, Seq(R(body=page)))
    assert e.value.kind == 'banned'


def test_d_banned_page_route_shows_error_status_not_results(env, signed_in):  # noqa: F811
    ipt(env['store'])
    env['mock'].set('ipt.example', R(body='<html><a href=/lout.php>Log out</a><h1>Your account has been banned</h1></html>'))
    j = signed_in.get('/api/sources/all-results?title=Interstellar&media_type=movie').get_json()
    assert j['releases'] == []
    statuses = j.get('trackers') or j.get('tracker_status') or j.get('statuses') or []
    assert statuses and all(s.get('status') not in ('ok', '', None) for s in statuses), j


# ------------------------------------------------------------------ (e)

URL_E = 'http://jackett.example:9117/api;/v2.0/indexers/x/results/torznab'


def test_e_semicolon_jackett_path_is_a_proxy_url():
    assert is_proxy_url(URL_E)


@pytest.mark.parametrize('type_id', ['torznab', 'newznab'])
def test_e_semicolon_jackett_path_refused_by_store(tmp_path, type_id):
    store = tracker_store.TrackerStore(tmp_path / 't.json', tmp_path / 'k.key')
    rec, errors = store.create({'type': type_id, 'name': 'J', 'url': URL_E, 'api_key': 'qa-k-123456',
                                'categories': [2000]})
    assert errors and not rec, (rec, errors)
