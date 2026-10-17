"""Search and Test for each tracker type (see app.tracker_types).

Each client builds requests and parses answers; HTTP goes through the
*request* callable (app.tracker_http.request by default) so the manager can
add rate limits and tests can mock trackers. Clients raise
app.tracker_http.TrackerError with a short, UI-safe message.

Results are dicts with: title, download_url, magnet_url, guid, info_url,
size, seeders, leechers, pub_date (RFC 822), imdb_id, info_hash, freeleech,
category, protocol. download_url can carry a passkey: it stays server-side.

Clean-room code written from the sites' public API behavior. No code or
definitions from Prowlarr, Jackett or Cardigann. Libraries: stdlib and
requests only.
"""

from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode, urljoin, urlsplit

from app import tracker_types as tt
from app.tracker_http import Response, TrackerError, looks_banned, raise_for_status
from app.tracker_http import request as http_request

Request = Callable[..., Response]
IMDB_RE = re.compile(r'^tt\d{6,10}$')
DEFAULT_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'
SEARCH_LIMIT_MAX = 500


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _base(cfg: Dict[str, Any]) -> str:
    return str(cfg.get('url') or '').rstrip('/')


def _result(title, download_url, size=0, seeders=0, leechers=0, freeleech=False, category='',
            guid='', pub_date='', imdb_id='', info_hash='', magnet_url='', info_url='',
            protocol='torrent') -> Dict[str, Any]:
    imdb = str(imdb_id or '').strip()
    if imdb and imdb.isdigit():
        imdb = 'tt' + imdb.zfill(7)
    return {
        'title': ' '.join(str(title or '').split()), 'download_url': str(download_url or ''),
        'magnet_url': str(magnet_url or ''), 'guid': str(guid or download_url or ''),
        'info_url': str(info_url or ''), 'pub_date': str(pub_date or ''),
        'size': max(0, _int(size)), 'seeders': max(0, _int(seeders)), 'leechers': max(0, _int(leechers)),
        'protocol': protocol, 'info_hash': str(info_hash or '').lower(),
        'imdb_id': imdb if IMDB_RE.match(imdb) else '', 'freeleech': bool(freeleech),
        'category': str(category or ''),
    }


def _iso_to_rfc822(value: Any) -> str:
    from datetime import datetime, timezone
    from email.utils import format_datetime
    s = str(value or '').strip()
    if not s:
        return ''
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def media_kind(categories: List[int]) -> str:
    cats = [int(c) for c in categories or []]
    movie = any(2000 <= c < 3000 for c in cats)
    tv = any(5000 <= c < 6000 for c in cats)
    if movie and not tv:
        return 'movie'
    if tv and not movie:
        return 'tv'
    return 'any'


def _json(resp: Response) -> Any:
    try:
        return json.loads(resp.text() or 'null')
    except ValueError:
        raise TrackerError('response', resp.status) from None


def _split_imdb(query: str, imdb_id: str):
    q = (query or '').strip()
    if IMDB_RE.match(q) and not imdb_id:
        return '', q
    return q, imdb_id or ''


# ------------------------------------------------------------------ Torznab / Newznab

NS_TORZNAB = 'http://torznab.com/schemas/2015/feed'
NS_NEWZNAB = 'http://www.newznab.com/DTD/2010/feeds/attributes/'
_AUTH_CODES = {100, 101, 102}       # incorrect credentials, suspended, no API access
_RATE_CODES = {429, 500, 501}       # request limit reached / download limit reached


def torznab_endpoint(cfg: Dict[str, Any]) -> str:
    url = _base(cfg)
    path = (tt.get_type(cfg.get('type')) or {}).get('torznab_path')
    if path and url and not urlsplit(url).path.strip('/'):
        url += path
    return url


def _newznab_error(root: ET.Element) -> None:
    if root.tag == 'error' or root.tag.endswith('}error'):
        code = _int(root.get('code'), 0)
        desc = (root.get('description') or '').lower()
        if code in _AUTH_CODES or 'api key' in desc or 'credentials' in desc:
            raise TrackerError('banned' if code == 101 or looks_banned(desc) else 'auth')
        if code in _RATE_CODES or 'limit' in desc:
            raise TrackerError('rate')
        raise TrackerError('response')


def _parse_xml(resp: Response) -> ET.Element:
    raise_for_status(resp)
    if resp.status >= 300:
        raise TrackerError('redirect', resp.status)
    try:
        root = ET.fromstring(resp.body)
    except ET.ParseError:
        raise TrackerError('response', resp.status) from None
    _newznab_error(root)
    return root


def _torznab_params(cfg: Dict[str, Any], **params) -> Dict[str, Any]:
    out = {k: v for k, v in params.items() if v not in (None, '')}
    if cfg.get('api_key'):
        out['apikey'] = cfg['api_key']
    return out


def parse_torznab(root: ET.Element, protocol: str, limit: int) -> List[Dict[str, Any]]:
    channel = root.find('channel')
    if channel is None:
        raise TrackerError('response')
    out = []
    for item in channel.findall('item'):
        attrs: Dict[str, str] = {}
        for el in item.findall(f'{{{NS_TORZNAB}}}attr') + item.findall(f'{{{NS_NEWZNAB}}}attr'):
            attrs.setdefault(el.get('name', '').lower(), el.get('value', ''))
        enc = item.find('enclosure')
        link = (item.findtext('link') or '').strip()
        if enc is not None and enc.get('url') and not link:
            link = enc.get('url')
        size = attrs.get('size') or item.findtext('size') or (enc.get('length') if enc is not None else 0)
        seeders = _int(attrs.get('seeders'))
        peers = attrs.get('peers')
        leechers = _int(attrs.get('leechers')) if 'leechers' in attrs else max(0, _int(peers) - seeders)
        magnet = attrs.get('magneturl', '')
        if link.startswith('magnet:') and not magnet:
            magnet, link = link, ''
        dvf = attrs.get('downloadvolumefactor')
        out.append(_result(
            item.findtext('title'), link, size, seeders, leechers,
            freeleech=dvf is not None and dvf.strip() in ('0', '0.0'),
            category=attrs.get('category') or item.findtext('category') or '',
            guid=item.findtext('guid') or link, pub_date=item.findtext('pubDate') or '',
            imdb_id=attrs.get('imdbid') or attrs.get('imdb') or '', info_hash=attrs.get('infohash', ''),
            magnet_url=magnet, info_url=item.findtext('comments') or '', protocol=protocol))
        if len(out) >= limit:
            break
    return out


def torznab_search(cfg, query, categories, limit, request: Request = http_request, imdb_id='', tmdb_id=''):
    q, imdb = _split_imdb(query, imdb_id)
    kind = media_kind(categories)
    t = 'search'
    if imdb and kind == 'movie':
        t = 'movie'
    elif imdb and kind == 'tv':
        t = 'tvsearch'
    params = _torznab_params(cfg, t=t, q=q or None, cat=','.join(str(c) for c in categories),
                             limit=min(int(limit), SEARCH_LIMIT_MAX), extended=1,
                             imdbid=imdb[2:] if imdb and t != 'search' else None)
    if imdb and t == 'search':
        params['q'] = ' '.join(x for x in (q, imdb) if x)
    proto = (tt.get_type(cfg.get('type')) or {}).get('protocol', 'torrent')
    root = _parse_xml(request('GET', torznab_endpoint(cfg), params=params,
                              headers={'Accept': 'application/rss+xml, application/xml'}))
    return parse_torznab(root, proto, limit)


def torznab_test(cfg, request: Request = http_request):
    root = _parse_xml(request('GET', torznab_endpoint(cfg), params=_torznab_params(cfg, t='caps'),
                              headers={'Accept': 'application/xml'}))
    if root.tag != 'caps':
        raise TrackerError('response')
    # Caps answers even without a valid key on some servers: prove the key
    # with a one-item search.
    torznab_search(cfg, '', list(cfg.get('categories') or tt.DEFAULT_CATEGORIES), 1, request)


# ------------------------------------------------------------------ BeyondHD

def beyondhd_search(cfg, query, categories, limit, request: Request = http_request, imdb_id='', tmdb_id=''):
    q, imdb = _split_imdb(query, imdb_id)
    body: Dict[str, Any] = {'action': 'search', 'rsskey': cfg.get('rss_key', '')}
    if q:
        body['search'] = q
    if imdb:
        body['imdb_id'] = imdb
    if tmdb_id:
        body['tmdb_id'] = str(tmdb_id)
    kind = media_kind(categories)
    if kind != 'any':
        body['categories'] = 'Movies' if kind == 'movie' else 'TV'
    # BeyondHD's API takes the key in the path; this URL is never logged.
    resp = request('POST', f"{_base(cfg)}/api/torrents/{cfg.get('api_key', '')}", json=body,
                   headers={'Accept': 'application/json'})
    raise_for_status(resp)
    data = _json(resp)
    if not isinstance(data, dict):
        raise TrackerError('response', resp.status)
    if data.get('success') is False or _int(data.get('status_code', 1)) == 0:
        msg = str(data.get('status_message') or '').lower()
        if looks_banned(msg):
            raise TrackerError('banned')
        if 'key' in msg or 'auth' in msg or 'invalid' in msg:
            raise TrackerError('auth')
        if 'limit' in msg or 'too many' in msg:
            raise TrackerError('rate')
        raise TrackerError('response')
    out = []
    for r in data.get('results') or []:
        if not isinstance(r, dict):
            continue
        out.append(_result(
            r.get('name'), r.get('download_url'), r.get('size'), r.get('seeders'), r.get('leechers'),
            freeleech=_int(r.get('freeleech')) == 1 or str(r.get('promo100') or '') == '1',
            category=r.get('category'), guid=r.get('url') or r.get('id'), info_url=r.get('url') or '',
            pub_date=_iso_to_rfc822(r.get('created_at')), imdb_id=r.get('imdb_id'),
            info_hash=r.get('info_hash')))
        if len(out) >= limit:
            break
    return out


def beyondhd_test(cfg, request: Request = http_request):
    beyondhd_search(cfg, '', [2000], 1, request)


# ------------------------------------------------------------------ AvistaZ family

_avz_tokens: Dict[str, tuple] = {}
_avz_lock = threading.Lock()


def _avz_key(cfg) -> str:
    import hashlib
    raw = '\0'.join(str(cfg.get(k) or '') for k in ('url', 'username', 'password', 'pid'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def clear_avistaz_tokens() -> None:
    with _avz_lock:
        _avz_tokens.clear()


def _avz_token(cfg, request: Request, force: bool = False) -> str:
    key, now = _avz_key(cfg), time.time()
    with _avz_lock:
        hit = _avz_tokens.get(key)
        if hit and not force and hit[1] > now + 60:
            return hit[0]
    resp = request('POST', f'{_base(cfg)}/api/v1/jackett/auth',
                   data={'username': cfg.get('username', ''), 'password': cfg.get('password', ''),
                         'pid': cfg.get('pid', '')},
                   headers={'Accept': 'application/json'})
    if resp.status in (401, 403, 422):
        raise TrackerError('banned' if looks_banned(resp.text()) else 'auth', resp.status)
    raise_for_status(resp)
    data = _json(resp)
    token = data.get('token') if isinstance(data, dict) else None
    if not token or not isinstance(token, str):
        raise TrackerError('auth', resp.status)
    ttl = _int(data.get('expiry'), 3600) or 3600
    with _avz_lock:
        _avz_tokens[key] = (token, now + min(max(ttl, 120), 86400))
    return token


def avistaz_search(cfg, query, categories, limit, request: Request = http_request, imdb_id='', tmdb_id=''):
    q, imdb = _split_imdb(query, imdb_id)
    params: Dict[str, Any] = {'in': 1, 'search': q}
    kind = media_kind(categories)
    if kind != 'any':
        params['type'] = 1 if kind == 'movie' else 2
    if imdb:
        params['imdb'] = imdb
    elif tmdb_id:
        params['tmdb'] = str(tmdb_id)
    url = f'{_base(cfg)}/api/v1/jackett/torrents?{urlencode(params)}'
    resp = None
    for attempt in (0, 1):
        token = _avz_token(cfg, request, force=attempt == 1)
        resp = request('GET', url, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
        if resp.status != 401:
            break
    if resp.status == 404:          # the API answers 404 for no results
        return []
    raise_for_status(resp)
    data = _json(resp)
    if not isinstance(data, dict):
        raise TrackerError('response', resp.status)
    out = []
    for r in data.get('data') or []:
        if not isinstance(r, dict):
            continue
        mt = r.get('movie_tv') if isinstance(r.get('movie_tv'), dict) else {}
        dl = r.get('download_multiply')
        try:
            free = dl is not None and float(dl) == 0
        except (TypeError, ValueError):
            free = False
        out.append(_result(
            r.get('file_name'), r.get('download'), r.get('file_size'), r.get('seed'), r.get('leech'),
            freeleech=free, category=r.get('type'), guid=r.get('url') or r.get('id'),
            info_url=r.get('url') or '', pub_date=_iso_to_rfc822(r.get('created_at')),
            imdb_id=mt.get('imdb') or '', info_hash=r.get('info_hash')))
        if len(out) >= limit:
            break
    return out


def avistaz_test(cfg, request: Request = http_request):
    _avz_token(cfg, request, force=True)


# ------------------------------------------------------------------ IPTorrents

IPT_MOVIE_CATS = (72, 87, 89, 90, 96, 6, 48, 54, 62, 38, 68, 20, 100, 101)
IPT_TV_CATS = (73, 4, 5, 22, 23, 24, 25, 26, 55, 65, 66, 78, 79, 99)
_SIZE_RE = re.compile(r'([\d.,]+)\s*([KMGT]?i?B)', re.I)
_UNITS = {'B': 1, 'KB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3, 'TB': 1024 ** 4}


def _parse_size(text: str) -> int:
    m = _SIZE_RE.search(text or '')
    if not m:
        return 0
    unit = m.group(2).upper().replace('IB', 'B')
    try:
        return int(float(m.group(1).replace(',', '')) * _UNITS.get(unit, 1))
    except ValueError:
        return 0


class _IPTParser(HTMLParser):
    """Rows of the torrents table: cell text, links and freeleech markers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.rows: List[Dict[str, Any]] = []
        self.row: Optional[Dict[str, Any]] = None
        self.cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'table':
            if self.depth or a.get('id') == 'torrents':
                self.depth += 1
            return
        if not self.depth:
            return
        if tag == 'tr':
            self.row = {'cells': [], 'links': [], 'free': False}
        elif self.row is None:
            return
        elif tag == 'td':
            self.cell = []
        elif tag == 'a' and a.get('href'):
            self.row['links'].append(a['href'])
        elif tag == 'span' and 'free' in (a.get('class') or '').lower():
            self.row['free'] = True

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag == 'table':
            self.depth -= 1
        elif tag == 'td' and self.row is not None and self.cell is not None:
            self.row['cells'].append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
            if self.row is not None and data.strip().lower() == 'freeleech':
                self.row['free'] = True


def parse_iptorrents(html: str, base: str, limit: int) -> List[Dict[str, Any]]:
    p = _IPTParser()
    p.feed(html)
    out = []
    for row in p.rows:
        dl = next((h for h in row['links'] if '/download.php/' in h), '')
        if not dl:
            continue
        det = next((h for h in row['links'] if re.match(r'^/t/\d+', h)), '')
        cat = next((h for h in row['links'] if re.match(r'^/t\?\d+', h)), '')
        cells = row['cells']
        nums = [c for c in cells if re.fullmatch(r'[\d,]+', c)]
        size_cell = next((c for c in cells if _SIZE_RE.fullmatch(c.strip())), '')
        title = cells[1] if len(cells) > 1 else ''
        title = re.sub(r'\s*(FreeLeech|New)\b.*$', '', title, flags=re.I) if title else title
        info = urljoin(base + '/', det) if det else ''
        out.append(_result(title, urljoin(base + '/', dl), _parse_size(size_cell),
                           _int(nums[-2].replace(',', '')) if len(nums) >= 2 else 0,
                           _int(nums[-1].replace(',', '')) if nums else 0,
                           freeleech=row['free'], category=cat.split('?', 1)[1] if cat else '',
                           guid=info or urljoin(base + '/', dl), info_url=info))
        if len(out) >= limit:
            break
    return out


def iptorrents_headers(cfg) -> Dict[str, str]:
    return {'Cookie': str(cfg.get('cookie') or '').strip(),
            'User-Agent': str(cfg.get('user_agent') or '').strip() or DEFAULT_UA}


def iptorrents_search(cfg, query, categories, limit, request: Request = http_request, imdb_id='', tmdb_id=''):
    q, imdb = _split_imdb(query, imdb_id)
    q = q or imdb
    kind = media_kind(categories)
    cats = IPT_MOVIE_CATS if kind == 'movie' else IPT_TV_CATS if kind == 'tv' else ()
    parts = [str(c) for c in cats] + [urlencode({'q': q, 'qf': 'all' if IMDB_RE.match(q or '') else ''}), 'o=added']
    resp = request('GET', f"{_base(cfg)}/t?{'&'.join(parts)}", headers=iptorrents_headers(cfg),
                   follow_redirects=False)
    text = resp.text()
    if resp.status in (301, 302, 303, 307, 308):
        raise TrackerError('auth', resp.status)
    raise_for_status(resp)
    if 'id="torrents"' not in text:
        if looks_banned(text):
            raise TrackerError('banned', resp.status)
        if 'name="username"' in text or 'login.php' in text or 'name="password"' in text:
            raise TrackerError('auth', resp.status)
        if 'no torrents found' in text.lower():
            return []
        raise TrackerError('response', resp.status)
    return parse_iptorrents(text, _base(cfg), limit)


def iptorrents_test(cfg, request: Request = http_request):
    iptorrents_search(cfg, '', [2000], 1, request)


def iptorrents_download_headers(cfg) -> Dict[str, str]:
    return iptorrents_headers(cfg)


CLIENTS: Dict[str, Dict[str, Callable]] = {
    'torznab': {'search': torznab_search, 'test': torznab_test},
    'beyondhd': {'search': beyondhd_search, 'test': beyondhd_test},
    'avistaz': {'search': avistaz_search, 'test': avistaz_test},
    'iptorrents': {'search': iptorrents_search, 'test': iptorrents_test},
}


def client_for(type_id: str) -> Dict[str, Callable]:
    t = tt.get_type(type_id)
    if t is None:
        raise TrackerError('response')
    return CLIENTS[t['client']]


def test(cfg: Dict[str, Any], request: Request = http_request) -> None:
    """Raise TrackerError when *cfg* can't search; return None when it can."""
    client_for(cfg['type'])['test'](cfg, request)


def search(cfg: Dict[str, Any], query: str, categories: List[int], limit: int = 100,
           request: Request = http_request, imdb_id: str = '', tmdb_id: str = '') -> List[Dict[str, Any]]:
    return client_for(cfg['type'])['search'](cfg, query, categories, limit, request,
                                             imdb_id=imdb_id, tmdb_id=tmdb_id)
