"""Reviewer IPT prod items: same-host links, stricter sign-in form detection,
path parameters can't hide a proxy URL."""
import pytest

from app import tracker_clients as tc
from app.tracker_http import TrackerError, is_proxy_url
from tests.test_qa_ipt_ee3bee4 import CFG, Seq, ipt_page, ipt_row
from tests.test_tracker_sources import R


@pytest.mark.parametrize('href,kept', [
    ('/download.php/2/2.torrent', True),
    ('https://ipt.example/download.php/2/2.torrent', True),
    ('https://ipt.example:443/download.php/2/2.torrent', True),
    ('//evil.example/download.php/2/2.torrent', False),
    ('https://ipt.example.evil.example/download.php/2/2.torrent', False),
    ('https://ipt.example:8443/download.php/2/2.torrent', False),
    ('http://127.0.0.1/download.php/2/2.torrent', False),
])
def test_download_link_must_be_on_the_tracker_host(href, kept):
    row = ipt_row(2, 'Dune Part Two 2024 2160p').replace('href=/download.php/2/2.torrent', f'href={href}')
    page = ipt_page(ipt_row(1, 'Dune Part Two 2024 1080p'), row)
    out = tc.search(CFG, 'Dune', [2000], 100, Seq(R(body=page)))
    assert len(out) == (2 if kept else 1)
    if not kept:
        assert tc.last_stats().get('offhost') == 1


def test_every_row_off_host_is_an_error_not_zero_results():
    row = ipt_row(1, 'Dune 2021').replace('href=/download.php/1/1.torrent',
                                         'href=https://evil.example/download.php/1/1.torrent')
    with pytest.raises(TrackerError):
        tc.search(CFG, 'Dune', [2000], 100, Seq(R(body=ipt_page(row))))


@pytest.mark.parametrize('form,auth', [
    ('<input type=password name=pw>', True),
    ('<input name="username">', True),
    ("<input name='password' type='password'>", True),
    ('<input name=usernames>', False),
    ('<input name=username_hint>', False),
    ('<p>name=username</p>', False),
])
def test_sign_in_form_detection_is_exact(form, auth):
    page = ipt_page(ipt_row(1, 'Dune 2021 1080p')).replace('<body>', '<body>' + form, 1)
    if auth:
        with pytest.raises(TrackerError) as e:
            tc.search(CFG, 'Dune', [2000], 100, Seq(R(body=page)))
        assert e.value.kind == 'auth'
    else:
        assert len(tc.search(CFG, 'Dune', [2000], 100, Seq(R(body=page)))) == 1


@pytest.mark.parametrize('url', [
    'http://j.example:9117/api;/v2.0/indexers/x/results/torznab',
    'http://j.example:9117/api;x=1/v2.0;y/indexers/x/results/torznab',
    'http://j.example/prowlarr;a/1/api',
    'http://j.example/api%3B/v2.0/indexers/x',
])
def test_path_parameters_do_not_hide_a_proxy(url):
    assert is_proxy_url(url)


def test_plain_tracker_url_is_not_a_proxy():
    assert not is_proxy_url('https://tracker.example/api;jsessionid=1')


def test_on_host_is_strict_about_www_and_subdomains():
    from app.tracker_clients import on_host
    base = 'https://iptorrents.com'
    assert on_host('https://iptorrents.com/t/1', base)
    assert on_host('http://IPTorrents.com./t/1', base)            # scheme, case, trailing dot
    assert not on_host('https://www.iptorrents.com/t/1', base)    # www is another host
    assert not on_host('https://iptorrents.com/t/1', 'https://www.iptorrents.com')
    assert not on_host('https://cdn.iptorrents.com/t/1', base)
    assert not on_host('https://iptorrents.com:8443/t/1', base)
