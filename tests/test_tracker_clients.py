"""Tracker clients against mocked trackers: parsing, errors, no secrets leaked."""

import json

import pytest

from app import tracker_clients as tc
from app import tracker_http
from app.tracker_http import Response, TrackerError
from app.tracker_store import TrackerStore

KEY = 'ak_0123456789abcdef0123456789abcdef'
PUBLIC_IP = '93.184.215.14'


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Tests have no DNS: every host name resolves to one public address."""
    monkeypatch.setattr(tracker_http, 'resolve', lambda host, port: [PUBLIC_IP])


class FakeTracker:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kw):
        self.calls.append({'method': method, 'url': url, **kw})
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


def R(status=200, body=b'', headers=None):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
    if isinstance(body, str):
        body = body.encode()
    return Response(status, headers or {}, body)


TORZNAB_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
<channel><title>x</title>
<item><title>Some.Movie.2024.2160p.WEB-DL.DDP5.1.H.265-GRP</title>
<guid>https://t.example/details/1</guid><link>https://t.example/dl/1?passkey=SECRET</link>
<comments>https://t.example/details/1</comments>
<pubDate>Fri, 16 Oct 2026 12:00:00 +0000</pubDate><size>12884901888</size>
<torznab:attr name="seeders" value="42"/><torznab:attr name="peers" value="50"/>
<torznab:attr name="infohash" value="ABCDEF0123456789ABCDEF0123456789ABCDEF01"/>
<torznab:attr name="imdbid" value="1234567"/><torznab:attr name="downloadvolumefactor" value="0"/>
<torznab:attr name="category" value="2045"/></item>
<item><title>Other</title><link>magnet:?xt=urn:btih:abc</link></item>
</channel></rss>"""

CAPS = '<?xml version="1.0"?><caps><server title="x"/><searching><search available="yes"/></searching></caps>'


def torznab_cfg(**kw):
    c = {'id': 1, 'type': 'torznab', 'name': 'J', 'url': 'http://tracker.example/api/torznab', 'api_key': KEY,
         'categories': [2000]}
    c.update(kw)
    return c


def test_torznab_search_parses_items():
    t = FakeTracker(R(body=TORZNAB_RSS))
    out = tc.search(torznab_cfg(), 'Some Movie', [2000, 2045], 100, t)
    assert len(out) == 2
    a = out[0]
    assert a['title'].startswith('Some.Movie.2024') and a['seeders'] == 42 and a['leechers'] == 8
    assert a['size'] == 12884901888 and a['freeleech'] is True and a['imdb_id'] == 'tt1234567'
    assert a['info_hash'] == 'abcdef0123456789abcdef0123456789abcdef01' and a['category'] == '2045'
    assert a['download_url'].endswith('passkey=SECRET') and a['protocol'] == 'torrent'
    assert out[1]['magnet_url'].startswith('magnet:') and out[1]['download_url'] == ''
    p = t.calls[0]['params']
    assert p['t'] == 'search' and p['q'] == 'Some Movie' and p['cat'] == '2000,2045' and p['apikey'] == KEY


def test_torznab_imdb_movie_search():
    t = FakeTracker(R(body=TORZNAB_RSS))
    tc.search(torznab_cfg(), 'tt1234567', [2000], 10, t)
    p = t.calls[0]['params']
    assert p['t'] == 'movie' and p['imdbid'] == '1234567' and 'q' not in p


def test_morethantv_appends_torznab_path():
    t = FakeTracker(R(body=TORZNAB_RSS))
    tc.search(torznab_cfg(type='morethantv', url='https://mtv.example'), 'x', [5000], 10, t)
    assert t.calls[0]['url'] == 'https://mtv.example/api/torznab'


def test_newznab_results_are_usenet():
    t = FakeTracker(R(body=TORZNAB_RSS))
    out = tc.search(torznab_cfg(type='newznab'), 'x', [2000], 10, t)
    assert out[0]['protocol'] == 'usenet'


@pytest.mark.parametrize('xml,msg', [
    ('<error code="100" description="Incorrect user credentials"/>', 'Unauthorized'),
    ('<error code="101" description="Account suspended"/>', 'Banned'),
    ('<error code="500" description="Request limit reached"/>', 'Rate limited'),
    ('<error code="300" description="No such function"/>', 'Unexpected response'),
    ('not xml', 'Unexpected response'),
])
def test_torznab_errors(xml, msg):
    with pytest.raises(TrackerError) as e:
        tc.search(torznab_cfg(), 'x', [2000], 10, FakeTracker(R(body=xml)))
    assert str(e.value) == msg


def test_torznab_test_checks_caps_then_search():
    t = FakeTracker(R(body=CAPS), R(body=TORZNAB_RSS))
    tc.test(torznab_cfg(), t)
    assert t.calls[0]['params']['t'] == 'caps' and t.calls[1]['params']['t'] == 'search'


@pytest.mark.parametrize('status,headers,kind,retry', [
    (401, {}, 'auth', None), (403, {}, 'auth', None), (429, {'Retry-After': '120'}, 'rate', 120.0),
    (500, {}, 'http', None), (503, {'Retry-After': '30'}, 'rate', 30.0),
])
def test_http_status_mapping(status, headers, kind, retry):
    with pytest.raises(TrackerError) as e:
        tc.search(torznab_cfg(), 'x', [2000], 10, FakeTracker(R(status, b'', headers)))
    assert e.value.kind == kind and e.value.retry_after == retry


def test_403_ban_page():
    with pytest.raises(TrackerError) as e:
        tc.search(torznab_cfg(), 'x', [2000], 10, FakeTracker(R(403, 'Your account has been banned')))
    assert e.value.kind == 'banned' and str(e.value) == 'Banned'


# ------------------------------------------------------------------ BeyondHD

def bhd_cfg():
    return {'id': 2, 'type': 'beyondhd', 'name': 'BHD', 'url': 'https://bhd.example', 'api_key': KEY,
            'rss_key': 'rk_secret_value_1234', 'categories': [2000]}


def test_beyondhd_search():
    body = {'status_code': 1, 'success': True, 'results': [{
        'name': 'Film 2023 1080p BluRay REMUX', 'download_url': 'https://bhd.example/dl/1/rk',
        'size': 30000000000, 'seeders': 10, 'leechers': 2, 'freeleech': 1, 'category': 'Movies',
        'url': 'https://bhd.example/t/1', 'imdb_id': 'tt7654321', 'created_at': '2026-10-15 10:00:00',
        'info_hash': 'FF' * 20}]}
    t = FakeTracker(R(body=body))
    out = tc.search(bhd_cfg(), 'tt7654321', [2000], 10, t)
    assert out[0]['seeders'] == 10 and out[0]['freeleech'] and out[0]['imdb_id'] == 'tt7654321'
    assert out[0]['pub_date'].startswith('Thu, 15 Oct 2026')
    call = t.calls[0]
    assert call['method'] == 'POST' and call['json']['imdb_id'] == 'tt7654321'
    assert call['json']['categories'] == 'Movies' and call['json']['rsskey'] == 'rk_secret_value_1234'


@pytest.mark.parametrize('msg,expect', [('Invalid API key', 'Unauthorized'), ('Too many requests', 'Rate limited'),
                                        ('User banned', 'Banned'), ('weird', 'Unexpected response')])
def test_beyondhd_failures(msg, expect):
    with pytest.raises(TrackerError) as e:
        tc.test(bhd_cfg(), FakeTracker(R(body={'status_code': 0, 'success': False, 'status_message': msg})))
    assert str(e.value) == expect


# ------------------------------------------------------------------ AvistaZ family

def avz_cfg(t='privatehd'):
    return {'id': 3, 'type': t, 'name': 'PHD', 'url': 'https://phd.example', 'username': 'me',
            'password': 'pw_secret_1', 'pid': 'pid_secret_1', 'categories': [2000]}


@pytest.fixture(autouse=True)
def _clear_tokens():
    tc.clear_avistaz_tokens()
    yield
    tc.clear_avistaz_tokens()


def test_avistaz_search_signs_in_and_reuses_token():
    data = {'data': [{'file_name': 'Movie.2022.1080p.BluRay.x264', 'download': 'https://phd.example/dl/9',
                      'file_size': 9000000000, 'seed': 33, 'leech': 1, 'download_multiply': 0,
                      'url': 'https://phd.example/t/9', 'created_at': '2026-10-01T00:00:00Z',
                      'movie_tv': {'imdb': '0111161'}, 'info_hash': 'aa' * 20}]}
    t = FakeTracker(R(body={'token': 'tok', 'expiry': 3600}), R(body=data), R(body=data))
    out = tc.search(avz_cfg(), 'Movie', [2000], 10, t)
    tc.search(avz_cfg(), 'Movie', [2000], 10, t)
    assert out[0]['seeders'] == 33 and out[0]['freeleech'] and out[0]['imdb_id'] == 'tt0111161'
    assert [c['url'].split('?')[0] for c in t.calls] == [
        'https://phd.example/api/v1/jackett/auth', 'https://phd.example/api/v1/jackett/torrents',
        'https://phd.example/api/v1/jackett/torrents']
    assert t.calls[1]['headers']['Authorization'] == 'Bearer tok'
    assert 'type=1' in t.calls[1]['url']


def test_avistaz_expired_token_retries_once():
    t = FakeTracker(R(body={'token': 'a'}), R(401), R(body={'token': 'b'}), R(body={'data': []}))
    assert tc.search(avz_cfg(), 'x', [2000], 10, t) == []
    assert t.calls[-1]['headers']['Authorization'] == 'Bearer b'


def test_avistaz_404_is_no_results():
    t = FakeTracker(R(body={'token': 'a'}), R(404))
    assert tc.search(avz_cfg(), 'x', [2000], 10, t) == []


def test_avistaz_bad_login():
    with pytest.raises(TrackerError) as e:
        tc.test(avz_cfg(), FakeTracker(R(422, '{"message":"invalid"}')))
    assert str(e.value) == 'Unauthorized'


def test_avistaz_family_types_share_client():
    for t in ('avistaz', 'privatehd', 'cinemaz', 'exoticaz'):
        assert tc.client_for(t)['search'] is tc.avistaz_search


# ------------------------------------------------------------------ IPTorrents

IPT_HTML = """<html><body><table id="torrents">
<tr><th>Type</th><th>Name</th></tr>
<tr><td><a href="/t?48">Movies</a></td>
<td><a href="/t/555">Great.Film.2025.1080p.WEB-DL</a> <span class="free">FreeLeech</span></td>
<td><a href="/download.php/555/Great.Film.torrent">dl</a></td><td>4.5 GB</td><td>1,234</td><td>56</td></tr>
</table></body></html>"""


def ipt_cfg():
    return {'id': 4, 'type': 'iptorrents', 'name': 'IPT', 'url': 'https://ipt.example',
            'cookie': 'uid=1; pass=secretpass', 'categories': [2000]}


def test_iptorrents_search_parses_table():
    t = FakeTracker(R(body=IPT_HTML))
    out = tc.search(ipt_cfg(), 'Great Film', [2000], 10, t)
    assert len(out) == 1
    r = out[0]
    assert r['title'] == 'Great.Film.2025.1080p.WEB-DL' and r['seeders'] == 1234 and r['leechers'] == 56
    assert r['size'] == int(4.5 * 1024 ** 3) and r['freeleech'] and r['category'] == '48'
    assert r['download_url'] == 'https://ipt.example/download.php/555/Great.Film.torrent'
    assert r['info_url'] == 'https://ipt.example/t/555'
    assert t.calls[0]['headers']['Cookie'] == 'uid=1; pass=secretpass'
    assert t.calls[0]['follow_redirects'] is False


@pytest.mark.parametrize('resp,expect', [
    (R(302, b'', {'Location': '/login.php'}), 'Unauthorized'),
    (R(body='<form><input name="username"><input name="password"></form>'), 'Unauthorized'),
    (R(body='<p>You are banned</p>'), 'Banned'),
    (R(body='<a href="/lout.php">Log out</a><p>No Torrents Found!</p>'), None),
    (R(body='<p>No Torrents Found!</p>'), 'Unauthorized'),     # no sign-out link: signed out
])
def test_iptorrents_auth_and_empty(resp, expect):
    t = FakeTracker(resp)
    if expect is None:
        assert tc.search(ipt_cfg(), 'x', [2000], 10, t) == []
    else:
        with pytest.raises(TrackerError) as e:
            tc.test(ipt_cfg(), t)
        assert str(e.value) == expect


# ------------------------------------------------------------------ HTTP layer

class _Raw:
    def __init__(self, status=200, headers=None, chunks=(b'ok',), url=''):
        self.status_code, self.headers, self.chunks, self.url = status, headers or {}, list(chunks), url
        self.closed = False

    def iter_content(self, n):
        yield from self.chunks

    def close(self):
        self.closed = True


class _Sess:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.resps.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        pass


def test_http_size_cap():
    s = _Sess(_Raw(chunks=[b'x' * 1000] * 20))
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://a.example/x', session=s, max_bytes=5000)
    assert e.value.kind == 'too_large'


def test_http_follows_same_host_redirect_only():
    s = _Sess(_Raw(302, {'Location': '/next'}), _Raw(200, chunks=[b'done']))
    r = tracker_http.request('GET', 'https://a.example/x', session=s)
    assert r.body == b'done' and s.calls[1][1] == 'https://a.example/next'
    s = _Sess(_Raw(302, {'Location': 'https://evil.example/steal'}))
    r = tracker_http.request('GET', 'https://a.example/x', session=s)
    assert r.status == 302 and len(s.calls) == 1
    s = _Sess(_Raw(302, {'Location': 'http://a.example/plain'}))
    assert tracker_http.request('GET', 'https://a.example/x', session=s).status == 302


def test_http_redirect_loop():
    s = _Sess(*[_Raw(302, {'Location': '/again'}) for _ in range(5)])
    with pytest.raises(TrackerError) as e:
        tracker_http.request('GET', 'https://a.example/x', session=s)
    assert e.value.kind == 'redirect'


def test_http_network_errors_are_short():
    import requests
    for exc, msg in ((requests.exceptions.ConnectTimeout(f'https://a.example/?apikey={KEY}'), 'Timed out'),
                     (requests.exceptions.ConnectionError(f'https://a.example/?apikey={KEY}'), 'Connection failed'),
                     (requests.exceptions.SSLError('bad cert'), 'Certificate error')):
        with pytest.raises(TrackerError) as e:
            tracker_http.request('GET', 'https://a.example/x', session=_Sess(exc))
        assert str(e.value) == msg and KEY not in repr(e.value)
        assert e.value.__cause__ is None and e.value.__suppress_context__


def test_retry_after_http_date():
    from email.utils import formatdate
    assert 100 < tracker_http.retry_after_seconds(formatdate(1_000_000_150, usegmt=True), 1_000_000_000) <= 150
    assert tracker_http.retry_after_seconds('bogus') is None
    assert tracker_http.retry_after_seconds('999999') == 86400.0


# ------------------------------------------------------------------ store

def test_store_survives_reload_and_sees_other_writers(tmp_path):
    a = TrackerStore(tmp_path / 't.json', tmp_path / 'k')
    b = TrackerStore(tmp_path / 't.json', tmp_path / 'k')
    rec, err = a.create({'type': 'iptorrents', 'name': 'IPT', 'url': 'https://ipt.example', 'cookie': 'c=1'})
    assert not err
    assert b.get(rec['id'])['name'] == 'IPT' and b.config(b.get(rec['id']))['cookie'] == 'c=1'
    b.set_enabled(rec['id'], False)
    assert a.get(rec['id'])['enabled'] is False


def test_store_quarantines_corrupt_file(tmp_path):
    p = tmp_path / 't.json'
    p.write_text('{nope')
    s = TrackerStore(p, tmp_path / 'k')
    assert s.list() == []
    assert any(x.name.startswith('t.json.bad-') for x in tmp_path.iterdir())


def test_store_drops_invalid_records(tmp_path):
    p = tmp_path / 't.json'
    p.write_text(json.dumps({'next_id': 1, 'trackers': [
        {'id': 3, 'type': 'iptorrents', 'name': 'ok'}, {'id': 'x', 'type': 'iptorrents', 'name': 'bad'},
        {'id': 4, 'type': 'cardigann', 'name': 'bad'}]}))
    s = TrackerStore(p, tmp_path / 'k')
    assert [r['id'] for r in s.list()] == [3]
    rec, _ = s.create({'type': 'iptorrents', 'name': 'new', 'url': 'https://i.example', 'cookie': 'c'})
    assert rec['id'] == 4


def test_status_message_is_truncated_and_validated(tmp_path):
    s = TrackerStore(tmp_path / 't.json', tmp_path / 'k')
    rec, _ = s.create({'type': 'iptorrents', 'name': 'IPT', 'url': 'https://ipt.example', 'cookie': 'c'})
    s.set_status(rec['id'], 'error', 'x' * 500)
    assert len(s.get(rec['id'])['status']['message']) == 80
    with pytest.raises(ValueError):
        s.set_status(rec['id'], 'weird')
