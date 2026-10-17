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
from typing import Any, Callable, Dict, List, Optional, Tuple
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

BHD_MAX_PAGES = 3                 # the API answers 100 rows a page
BHD_PAGE_GAP_S = 2.0              # same spacing the guard keeps between requests
_BHD_ID_RE = re.compile(r'\bS\d{1,3}(?:E\d{1,4})?\b', re.I)


def bhd_query_text(text: str) -> str:
    """BHD names drop most punctuation ('Dune Part Two', 'Oceans Eleven'):
    apostrophes go, colons, commas and dashes become spaces."""
    t = re.sub(r"[\u2019'`]", '', str(text or ''))
    t = re.sub(r'[:;,!?\u2013\u2014]|\s-\s', ' ', t)
    return ' '.join(t.split())


def release_kind(name: str, bhd_type: str = '') -> str:
    """WEB-DL, WEBRip, Remux, Encode or Other, for the per-type log counts."""
    n = f' {str(name or "").upper().replace(".", " ")} {str(bhd_type or "").upper()} '
    if 'REMUX' in n:
        return 'Remux'
    if re.search(r'\bWEB[ -]?RIP\b', n):
        return 'WEBRip'
    if re.search(r'\bWEB[ -]?DL\b|\bWEB\b', n):
        return 'WEB-DL'
    if re.search(r'\bBLU[ -]?RAY\b|\bBLURAY\b|\bBDRIP\b|\bX26[45]\b|\bH ?26[45]\b', n):
        return 'Encode'
    return 'Other'


def _bhd_error(data: Dict[str, Any]) -> None:
    msg = str(data.get('status_message') or '').lower()
    if looks_banned(msg):
        raise TrackerError('banned')
    if 'key' in msg or 'auth' in msg or 'unauthori' in msg:
        raise TrackerError('auth')
    if 'limit' in msg or 'too many' in msg:
        raise TrackerError('rate')
    raise TrackerError('response')


_TRAILING_YEAR_RE = re.compile(r'\s+(?:19|20)\d{2}$')
BHD_CHAIN_BUDGET_S = 45.0         # no new attempt after this; Sources waits 75 s
# Failures after which another attempt may still work. Rate limits, bans and
# rejected keys end the search at once (and start the guard's backoff).
_BHD_RETRYABLE = ('response', 'http', 'timeout', 'network', 'redirect', 'too_large')
_monotonic = time.monotonic


class OutOfBudget(Exception):
    """The guard refused another request of a multi-request search (every
    page and fallback is charged as its own request). Nothing was sent."""

    def __init__(self, reason: str = ''):
        super().__init__(reason)
        self.reason = reason


def bhd_tmdb_param(tmdb_id: str, media_type: str) -> str:
    """BHD's tmdb_id filter takes '<movie|tv>/<id>' (the same shape its
    results carry, e.g. 'movie/693134'); a bare number matches nothing."""
    return f"{'movie' if media_type == 'movie' else 'tv'}/{int(tmdb_id)}"


def beyondhd_search(cfg, query, categories, limit, request: Request = http_request, imdb_id='', tmdb_id='',
                    media_type=''):
    """BHD's own API, text first exactly as before (the query as given),
    then, only while nothing was found: the punctuation-stripped text, for
    movies the title without the year, and last the TMDB id
    ('movie/<id>' / 'tv/<id>', TV with its SxxEyy/Sxx text). An attempt that
    fails with a bad answer, an HTTP error or a timeout moves on to the next;
    a rate limit, ban or rejected key stops at once. When every attempt
    failed the error is raised, never an empty list. The whole chain is one
    search to the guard. Pages are followed up to BHD_MAX_PAGES."""
    q, imdb = _split_imdb(query, imdb_id)
    tmdb = str(tmdb_id or '').strip()
    has_id = bool(tmdb.isdigit() and int(tmdb) > 0 and media_type in ('movie', 'tv'))
    acc: Dict[str, Any] = {'status': None, 'bytes': 0, 'rows': 0, 'pages': 0, 'kinds': {}, 'total': None,
                           'by_id': False, 'fallback': '', 'steps': []}
    out: List[Dict[str, Any]] = []

    def stats():
        _set_stats(status=acc['status'], bytes=acc['bytes'], rows=acc['rows'], kept=len(out),
                   pages=acc['pages'], kinds=dict(acc['kinds']), by_id=acc['by_id'], total=acc['total'],
                   fallback=acc['fallback'], steps=list(acc['steps']), stopped=acc.get('stopped', ''))

    def run(body: Dict[str, Any], step: Dict[str, Any]) -> int:
        """All pages of one query; returns the raw rows it found."""
        found = 0
        for page in range(1, BHD_MAX_PAGES + 1):
            if acc['pages']:
                _sleep(BHD_PAGE_GAP_S)
            b = dict(body)
            if page > 1:
                b['page'] = page
            # BeyondHD's API takes the key in the path; this URL is never logged.
            resp = request('POST', f"{_base(cfg)}/api/torrents/{cfg.get('api_key', '')}", json=b,
                           headers={'Accept': 'application/json'})
            acc['status'], acc['pages'] = resp.status, acc['pages'] + 1
            acc['bytes'] += len(resp.body or b'')
            step['http'] = resp.status
            stats()
            raise_for_status(resp)
            data = _json(resp)
            if not isinstance(data, dict):
                raise TrackerError('response', resp.status)
            step['success'] = data.get('success')
            step['status_code'] = data.get('status_code')
            if data.get('success') is False or _int(data.get('status_code', 1)) == 0:
                _bhd_error(data)
            rows = data.get('results') or []
            if not isinstance(rows, list):
                raise TrackerError('response', resp.status)
            acc['total'] = _int(data.get('total_results'), 0) or None
            for r in rows:
                if not isinstance(r, dict):
                    continue
                found += 1
                acc['rows'] += 1
                k = release_kind(r.get('name'), r.get('type'))
                acc['kinds'][k] = acc['kinds'].get(k, 0) + 1
                if not r.get('name') or not r.get('download_url') or len(out) >= limit:
                    continue
                out.append(_result(
                    r.get('name'), r.get('download_url'), r.get('size'), r.get('seeders'), r.get('leechers'),
                    freeleech=_int(r.get('freeleech')) == 1 or str(r.get('promo100') or '') == '1',
                    category=r.get('category'), guid=r.get('url') or r.get('id'), info_url=r.get('url') or '',
                    pub_date=_iso_to_rfc822(r.get('created_at')), imdb_id=r.get('imdb_id'),
                    info_hash=r.get('info_hash')))
            stats()
            if len(out) >= limit or page >= _int(data.get('total_pages'), 1) or not rows:
                break
        return found

    base: Dict[str, Any] = {'action': 'search', 'rsskey': cfg.get('rss_key', '')}

    def text_body(text: str) -> Dict[str, Any]:
        b = dict(base)
        if text:
            b['search'] = text
        if imdb:
            b['imdb_id'] = imdb
        kind = media_kind(categories)
        if kind != 'any':
            b['categories'] = 'Movies' if kind == 'movie' else 'TV'
        return b

    attempts: List[Tuple[str, Dict[str, Any]]] = []
    seen = set()

    def add(name: str, body: Dict[str, Any]) -> None:
        key = json.dumps(body, sort_keys=True)
        if key not in seen:
            seen.add(key)
            attempts.append((name, body))

    raw_text = (q or '').strip()
    clean = bhd_query_text(raw_text)
    add('text', text_body(raw_text))                      # exactly what ee3bee4 sent
    if clean and clean != raw_text:
        add('text stripped', text_body(clean))
    if media_type == 'movie' and clean:
        bare = _TRAILING_YEAR_RE.sub('', clean)
        if bare and bare != clean:
            add('text no year', text_body(bare))
    if has_id:
        b = dict(base, tmdb_id=bhd_tmdb_param(tmdb, media_type))
        ep = _BHD_ID_RE.search(raw_text) if media_type == 'tv' else None
        if ep:
            b['search'] = ep.group(0).upper()
        add('id', b)

    started = _monotonic()
    errors: List[TrackerError] = []
    answered = False
    for i, (name, body) in enumerate(attempts):
        if i and _monotonic() - started > BHD_CHAIN_BUDGET_S:
            acc['stopped'] = 'budget'
            break
        step: Dict[str, Any] = {'step': name, 'http': None, 'success': None, 'status_code': None, 'rows': 0}
        acc['steps'].append(step)
        if i:
            acc['fallback'] = name
        acc['by_id'] = name == 'id'
        try:
            step['rows'] = run(body, step)
            answered = True
        except OutOfBudget:
            step['error'] = 'budget'
            stats()
            if answered or out:
                break
            raise
        except TrackerError as e:
            if name == 'id' and e.kind != 'rate':
                # BHD refusing the id is an id problem, never a rejected key or a
                # reason to back off: the text attempts above stand.
                step['error'] = f'id ({e.kind})'
                stats()
                continue
            step['error'] = e.kind
            errors.append(e)
            stats()
            if e.kind not in _BHD_RETRYABLE:
                if out:
                    break
                raise
            continue
        if step['rows']:
            break
    stats()
    if not answered and errors:
        raise errors[-1]
    return out


def beyondhd_test(cfg, request: Request = http_request):
    beyondhd_search(cfg, '', [2000], 1, request)


# ------------------------------------------------------------------ AvistaZ family

_avz_tokens: Dict[str, tuple] = {}
_avz_lock = threading.Lock()


def _avz_key(cfg) -> str:
    """Token cache key: the site (normalized host) and the account, so a
    path or case variant of the URL reuses a valid token."""
    import hashlib
    from app.tracker_guard import normalize_host
    raw = '\0'.join([normalize_host(cfg.get('url'))] + [str(cfg.get(k) or '') for k in ('username', 'password', 'pid')])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def clear_avistaz_tokens() -> None:
    with _avz_lock:
        _avz_tokens.clear()


# Pause between an AvistaZ sign-in and the request that follows it, so a
# Test or search never sends two requests closer than the guard's spacing.
AVZ_LOGIN_GAP_S = 2.0
_sleep = time.sleep


def _avz_token(cfg, request: Request, force: bool = False) -> str:
    """A bearer token for these credentials: the cached one, or a fresh
    sign-in followed by AVZ_LOGIN_GAP_S of quiet before the caller's next
    request."""
    key, now = _avz_key(cfg), time.time()
    with _avz_lock:
        hit = _avz_tokens.get(key)
        if hit and not force and hit[1] > now + 60:
            return hit[0]
    token = _avz_login(cfg, request, key, now)
    _sleep(AVZ_LOGIN_GAP_S)
    return token


def _avz_login(cfg, request: Request, key: str, now: float) -> str:
    # '/api/v1/jackett/...' is the AvistaZ sites' own API path name; nothing
    # here talks to Jackett.
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
    """A one-item search: signs in only when there is no valid token for
    these credentials (a changed password or PID is a new token key), and
    re-signs in once if the site rejects the token."""
    avistaz_search(cfg, '', [2000], 1, request)


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


_IPT_DETAILS_RE = re.compile(r'^(?:https?://[^/]+)?/(?:t/\d+|details\.php\?id=\d+)(?:[/?#]|$)')
_IPT_CAT_RE = re.compile(r'^(?:/t)?\?(\d+)$')
_IPT_TABLE_RE = re.compile(r'<table\b[^>]*\bid\s*=\s*["\']?torrents\b', re.I)
# Only a signed-in IPT page links its sign-out page.
_IPT_SIGNED_IN = ('lout.php', 'logout.php')
_IPT_BADGE_RE = re.compile(r'\s*\b(FreeLeech|New)\b\s*$', re.I)


class _IPTParser(HTMLParser):
    """Rows of IPT's torrents table: cell text (with each cell's classes),
    links, the details link text (the release name) and freeleech markers.

    IPT's pages are minified: attributes may be unquoted and optional end
    tags (</td>, </tr>) may be left out, so a cell or row also ends where
    the next one starts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.rows: List[Dict[str, Any]] = []
        self.row: Optional[Dict[str, Any]] = None
        self.cell: Optional[List[str]] = None
        self.cell_class = ''
        self.name: Optional[List[str]] = None      # inside the details link

    def _end_cell(self):
        if self.row is not None and self.cell is not None:
            self.row['cells'].append(' '.join(''.join(self.cell).split()))
            self.row['classes'].append(self.cell_class)
        self.cell = None
        self.name = None

    def _end_row(self):
        self._end_cell()
        if self.row is not None:
            self.rows.append(self.row)
        self.row = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or '') for k, v in attrs}
        if tag == 'table':
            if self.depth:
                self.depth += 1
            elif a.get('id', '').strip().lower() == 'torrents':
                self.depth = 1
            return
        if not self.depth:
            return
        if tag == 'tr' and self.depth == 1:
            self._end_row()
            self.row = {'cells': [], 'classes': [], 'links': [], 'free': False, 'name': ''}
        elif self.row is None:
            return
        elif tag in ('td', 'th') and self.depth == 1:
            self._end_cell()
            self.cell = []
            self.cell_class = a.get('class', '').lower()
        elif tag == 'a' and a.get('href'):
            href = a['href'].strip()
            self.row['links'].append(href)
            if not self.row['name'] and _IPT_DETAILS_RE.match(href) and '#' not in href:
                self.name = []
        elif tag == 'span' and 'free' in a.get('class', '').lower():
            self.row['free'] = True

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag == 'table':
            if self.depth == 1:
                self._end_row()
            self.depth -= 1
        elif tag == 'a' and self.name is not None and self.row is not None:
            text = ' '.join(''.join(self.name).split())
            if text:
                self.row['name'] = text
            self.name = None
        elif self.depth != 1:
            return
        elif tag in ('td', 'th'):
            self._end_cell()
        elif tag == 'tr':
            self._end_row()

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
            if self.row is not None and data.strip().lower() == 'freeleech':
                self.row['free'] = True
        if self.name is not None:
            self.name.append(data)


# Rows parsed / kept by the last IPT search on this thread (for the log line).
_stats = threading.local()


def last_stats() -> Dict[str, Any]:
    return dict(getattr(_stats, 'value', None) or {})


def _set_stats(**kw) -> None:
    _stats.value = kw


def _cell_int(row, cls: str) -> Optional[int]:
    for c, k in zip(row['cells'], row['classes']):
        if cls in k.split():
            return _int(c.replace(',', ''))
    return None


def _host_port(u) -> Tuple[str, Optional[int]]:
    port = u.port
    if port == {'http': 80, 'https': 443}.get(u.scheme):
        port = None
    return (u.hostname or '').lower().rstrip('.'), port


def on_host(url: str, base: str) -> bool:
    """True when *url* (already absolute) is http(s) on *base*'s host and port."""
    try:
        u, b = urlsplit(url), urlsplit(base)
        return u.scheme in ('http', 'https') and bool(u.hostname) and _host_port(u) == _host_port(b)
    except ValueError:
        return False


def parse_iptorrents(html: str, base: str, limit: int, counts: Optional[Dict[str, int]] = None
                     ) -> List[Dict[str, Any]]:
    p = _IPTParser()
    p.feed(html)
    p.close()
    p._end_row()
    out = []
    rows = 0
    offhost = 0
    for row in p.rows:
        dl = next((h for h in row['links'] if '/download.php' in h), '')
        if not dl:
            continue
        rows += 1
        det = next((h for h in row['links'] if _IPT_DETAILS_RE.match(h) and '#' not in h), '')
        cat = next((m.group(1) for m in (_IPT_CAT_RE.match(h) for h in row['links']) if m), '')
        cells = row['cells']
        title = row['name']
        if not title and len(cells) > 1:
            title = _IPT_BADGE_RE.sub('', cells[1])
        if not title:
            continue
        nums = [c for c in cells if re.fullmatch(r'[\d,]+', c)]
        seeders = _cell_int(row, 't_seeders')
        leechers = _cell_int(row, 't_leechers')
        if seeders is None:
            seeders = _int(nums[-2].replace(',', '')) if len(nums) >= 2 else 0
        if leechers is None:
            leechers = _int(nums[-1].replace(',', '')) if nums else 0
        size_cell = next((c for c in cells if _SIZE_RE.fullmatch(c.strip())), '')
        info = urljoin(base + '/', det) if det else ''
        dl_abs = urljoin(base + '/', dl)
        if not on_host(dl_abs, base) or (info and not on_host(info, base)):
            # A download or details link on another host: never IPT's own row.
            offhost += 1
            continue
        out.append(_result(title, dl_abs, _parse_size(size_cell), seeders, leechers,
                           freeleech=row['free'], category=cat,
                           guid=info or dl_abs, info_url=info))
        if len(out) >= limit:
            break
    if counts is not None:
        counts['rows'] = rows
        counts['kept'] = len(out)
        if offhost:
            counts['offhost'] = offhost
    return out


def iptorrents_headers(cfg) -> Dict[str, str]:
    return {'Cookie': str(cfg.get('cookie') or '').strip(),
            'User-Agent': str(cfg.get('user_agent') or '').strip() or DEFAULT_UA}


# A sign-in form field: an <input> named exactly username or password, or
# any password input. Page text such as 'name=usernames' never matches.
_IPT_LOGIN_INPUT_RE = re.compile(
    r'<input\b[^>]*?(?:\bname\s*=\s*["\']?(?:username|password)(?=["\'\s/>])'
    r'|\btype\s*=\s*["\']?password(?=["\'\s/>]))', re.I)


def _ipt_login_form(text: str) -> bool:
    return bool(_IPT_LOGIN_INPUT_RE.search(text or ''))


def _ipt_signed_out(text: str) -> bool:
    if _ipt_login_form(text):
        return True
    low = text.lower()
    return not any(m in low for m in _IPT_SIGNED_IN)


def iptorrents_search(cfg, query, categories, limit, request: Request = http_request, imdb_id='', tmdb_id=''):
    q, imdb = _split_imdb(query, imdb_id)
    q = q or imdb
    kind = media_kind(categories)
    cats = IPT_MOVIE_CATS if kind == 'movie' else IPT_TV_CATS if kind == 'tv' else ()
    parts = [str(c) for c in cats] + [urlencode({'q': q, 'qf': 'all' if IMDB_RE.match(q or '') else ''}), 'o=added']
    _set_stats(status=None, bytes=0, rows=0, kept=0)
    resp = request('GET', f"{_base(cfg)}/t?{'&'.join(parts)}", headers=iptorrents_headers(cfg),
                   follow_redirects=False)
    text = resp.text()
    _set_stats(status=resp.status, bytes=len(resp.body or b''), rows=0, kept=0)
    if resp.status in (301, 302, 303, 307, 308):
        raise TrackerError('auth', resp.status)          # IPT sends a signed-out visitor to its login page
    raise_for_status(resp)
    if _ipt_login_form(text):
        # A sign-in form means the cookie was not accepted, even when the page
        # also carries a torrents table: never results, never "0 results".
        raise TrackerError('auth', resp.status)
    if not _IPT_TABLE_RE.search(text):
        if looks_banned(text) and _ipt_signed_out(text):
            raise TrackerError('banned', resp.status)
        if _ipt_signed_out(text):
            # A login form, or a page without the sign-out link: the cookie
            # was not accepted. Never "0 results".
            raise TrackerError('auth', resp.status)
        if looks_banned(text):
            raise TrackerError('banned', resp.status)
        if 'no torrents found' in text.lower():
            return []
        raise TrackerError('response', resp.status)
    counts: Dict[str, int] = {}
    out = parse_iptorrents(text, _base(cfg), limit, counts)
    _set_stats(status=resp.status, bytes=len(resp.body or b''), **counts)
    if counts.get('rows') and not out:
        raise TrackerError('response', resp.status)      # rows we can't read: a parser problem, not "no results"
    return out


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
           request: Request = http_request, imdb_id: str = '', tmdb_id: str = '',
           media_type: str = '') -> List[Dict[str, Any]]:
    if (tt.get_type(cfg['type']) or {}).get('client') == 'beyondhd':
        return beyondhd_search(cfg, query, categories, limit, request, imdb_id=imdb_id, tmdb_id=tmdb_id,
                               media_type=media_type)
    return client_for(cfg['type'])['search'](cfg, query, categories, limit, request,
                                             imdb_id=imdb_id, tmdb_id=tmdb_id)
