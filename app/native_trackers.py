"""Native tracker site types for the Luma indexer manager.

Each type is a definition only. Instances (URL, credentials, categories,
priority) are added by the admin in Settings; nothing is preconfigured.

Clean-room implementation written from the sites' public API behavior.
No code or definitions from Prowlarr, Jackett or Cardigann (GPL).
Libraries: stdlib and requests (Apache-2.0) only.
"""

import json
import re
import threading
import time
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode, urljoin, urlsplit

# Every credential-bearing field an indexer instance may carry. These are
# masked in API output, preserved on masked updates and never logged.
SECRET_FIELDS = ('api_key', 'password', 'pid', 'cookie', 'rss_key')

# type -> metadata used by validation, the Settings form and dispatch.
# 'fields' are the per-type inputs besides name/url/categories/priority.
SITE_TYPES: Dict[str, Dict[str, Any]] = {
    'torznab':    {'label': 'Torznab',     'native': False, 'fields': ['api_key']},
    'newznab':    {'label': 'Newznab',     'native': False, 'fields': ['api_key']},
    'morethantv': {'label': 'MoreThanTV',  'native': False, 'fields': ['api_key'],
                   'torznab_path': '/api/torznab'},
    'beyondhd':   {'label': 'BeyondHD',    'native': True,  'fields': ['api_key', 'rss_key']},
    'avistaz':    {'label': 'AvistaZ / PrivateHD', 'native': True,
                   'fields': ['username', 'password', 'pid']},
    'iptorrents': {'label': 'IPTorrents',  'native': True,  'fields': ['cookie', 'user_agent']},
}

REQUIRED_FIELDS = {
    'torznab': ('api_key',), 'newznab': ('api_key',), 'morethantv': ('api_key',),
    'beyondhd': ('api_key', 'rss_key'), 'avistaz': ('username', 'password', 'pid'),
    'iptorrents': ('cookie',),
}

IMDB_RE = re.compile(r'^tt\d{6,10}$')
DEFAULT_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'


class NativeAuthError(Exception):
    """Credentials were rejected by the site."""


def is_native(indexer_type: str) -> bool:
    return bool(SITE_TYPES.get(str(indexer_type or '').lower(), {}).get('native'))


def torznab_url(config: Dict[str, Any]) -> str:
    """Effective Torznab endpoint for torznab-family types."""
    url = str(config.get('url') or '').rstrip('/')
    meta = SITE_TYPES.get(str(config.get('type') or '').lower(), {})
    path = meta.get('torznab_path')
    if path and url and not urlsplit(url).path.strip('/'):
        url = url + path
    return url


def missing_credentials(config: Dict[str, Any]) -> List[str]:
    t = str(config.get('type') or '').lower()
    return [f for f in REQUIRED_FIELDS.get(t, ()) if not str(config.get(f) or '').strip()]


def mask_config(config: Dict[str, Any], mask: str) -> Dict[str, Any]:
    out = dict(config)
    for f in SECRET_FIELDS:
        if out.get(f):
            out[f] = mask
    return out


def scrub(text: str, config: Dict[str, Any]) -> str:
    """Remove any of this instance's secret values from text."""
    text = str(text or '')
    for f in SECRET_FIELDS:
        v = str(config.get(f) or '')
        if len(v) >= 4:
            text = text.replace(v, '***')
    return text


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _base(config: Dict[str, Any]) -> str:
    return str(config.get('url') or '').rstrip('/')


def _result(title, download_url, size, seeders, leechers, freeleech, category,
            guid='', pub_date='', imdb_id='', info_hash='') -> Dict[str, Any]:
    return {
        'title': str(title or ''), 'download_url': str(download_url or ''), 'magneturl': '',
        'guid': str(guid or download_url or ''), 'pub_date': str(pub_date or ''),
        'size': _int(size), 'seeders': _int(seeders), 'leechers': _int(leechers),
        'protocol': 'torrent', 'indexer': '', 'info_hash': str(info_hash or ''),
        'imdb_id': str(imdb_id or ''), 'freeleech': bool(freeleech), 'category': str(category or ''),
    }


def _iso_to_rfc822(value: str) -> str:
    """'2026-10-16 12:00:00' or ISO 8601 -> RFC 822 (UTC assumed when naive)."""
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


def _media_kind(categories: List[int]) -> str:
    cats = [int(c) for c in categories or []]
    movie = any(2000 <= c < 3000 for c in cats)
    tv = any(5000 <= c < 6000 for c in cats)
    if movie and not tv:
        return 'movie'
    if tv and not movie:
        return 'tv'
    return 'any'


# HTTP: the manager passes request(method, url, **kwargs) -> (status, headers, body bytes).
# It does SSRF pinning, size caps, guard cooldowns and backoff; clients only
# build requests and parse responses.
Request = Callable[..., Any]


# ---------------------------------------------------------------- BeyondHD

def beyondhd_search(config, query, categories, limit, request, imdb_id='', tmdb_id=''):
    body: Dict[str, Any] = {'action': 'search', 'rsskey': config.get('rss_key', '')}
    q = (query or '').strip()
    if IMDB_RE.match(q) and not imdb_id:
        imdb_id, q = q, ''
    if q:
        body['search'] = q
    if imdb_id:
        body['imdb_id'] = imdb_id
    if tmdb_id:
        body['tmdb_id'] = str(tmdb_id)
    kind = _media_kind(categories)
    if kind == 'movie':
        body['categories'] = 'Movies'
    elif kind == 'tv':
        body['categories'] = 'TV'
    url = f"{_base(config)}/api/torrents/{config.get('api_key', '')}"
    status, _h, raw = request('POST', url, json=body,
                              headers={'Accept': 'application/json'})
    if status in (401, 403):
        raise NativeAuthError('BeyondHD rejected the API key')
    data = json.loads(raw.decode('utf-8', 'replace') or '{}')
    if not data.get('success', True) or _int(data.get('status_code', 1)) == 0:
        msg = str(data.get('status_message') or 'BeyondHD search failed')
        if 'key' in msg.lower() or 'auth' in msg.lower():
            raise NativeAuthError(msg)
        raise ValueError(msg)
    out = []
    for r in (data.get('results') or [])[:limit]:
        if not isinstance(r, dict):
            continue
        imdb = str(r.get('imdb_id') or '')
        out.append(_result(
            r.get('name'), r.get('download_url'), r.get('size'), r.get('seeders'),
            r.get('leechers'), _int(r.get('freeleech')) == 1,
            r.get('category'), guid=r.get('url') or r.get('id'),
            pub_date=_iso_to_rfc822(r.get('created_at')), imdb_id=imdb,
            info_hash=r.get('info_hash')))
    return out


def beyondhd_test(config, request):
    beyondhd_search(config, '', [2000], 1, request)
    return 'Connection successful'


# ---------------------------------------------------------------- AvistaZ family

_avistaz_tokens: Dict[str, tuple] = {}
_avistaz_lock = threading.Lock()


def _avistaz_token(config, request, force=False) -> str:
    key = f"{config.get('id')}|{_base(config)}|{config.get('username')}"
    now = time.time()
    with _avistaz_lock:
        cached = _avistaz_tokens.get(key)
        if cached and not force and cached[1] > now + 60:
            return cached[0]
    status, _h, raw = request('POST', f'{_base(config)}/api/v1/jackett/auth', data={
        'username': config.get('username', ''), 'password': config.get('password', ''),
        'pid': config.get('pid', '')}, headers={'Accept': 'application/json'})
    try:
        data = json.loads(raw.decode('utf-8', 'replace') or '{}')
    except ValueError:
        data = {}
    token = data.get('token') if isinstance(data, dict) else None
    if status in (401, 403, 422) or not token:
        raise NativeAuthError('Sign-in rejected')
    expiry = _int(data.get('expiry'), 3600) or 3600
    with _avistaz_lock:
        _avistaz_tokens[key] = (token, now + expiry)
    return token


def avistaz_search(config, query, categories, limit, request, imdb_id='', tmdb_id=''):
    params: Dict[str, Any] = {'in': 1}
    q = (query or '').strip()
    if IMDB_RE.match(q) and not imdb_id:
        imdb_id, q = q, ''
    kind = _media_kind(categories)
    if kind == 'movie':
        params['type'] = 1
    elif kind == 'tv':
        params['type'] = 2
    if imdb_id:
        params['imdb'] = imdb_id
    elif tmdb_id:
        params['tmdb'] = str(tmdb_id)
    params['search'] = q
    url = f'{_base(config)}/api/v1/jackett/torrents?{urlencode(params)}'
    for attempt in (0, 1):
        token = _avistaz_token(config, request, force=attempt == 1)
        status, _h, raw = request('GET', url, headers={
            'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
        if status != 401:
            break
    if status in (401, 403):
        raise NativeAuthError('Sign-in rejected')
    if status == 404:   # no results
        return []
    data = json.loads(raw.decode('utf-8', 'replace') or '{}')
    out = []
    for r in (data.get('data') or [])[:limit]:
        if not isinstance(r, dict):
            continue
        mt = r.get('movie_tv') or {}
        imdb = str(mt.get('imdb') or '') if isinstance(mt, dict) else ''
        if imdb and not imdb.startswith('tt'):
            imdb = 'tt' + imdb
        dl_mult = r.get('download_multiply')
        out.append(_result(
            r.get('file_name'), r.get('download'), r.get('file_size'), r.get('seed'),
            r.get('leech'), dl_mult is not None and float(dl_mult or 0) == 0,
            r.get('type'), guid=r.get('url') or r.get('id'),
            pub_date=_iso_to_rfc822(r.get('created_at')), imdb_id=imdb,
            info_hash=r.get('info_hash')))
    return out


def avistaz_test(config, request):
    _avistaz_token(config, request, force=True)
    return 'Connection successful'


# ---------------------------------------------------------------- IPTorrents

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
    """Collect rows of the torrents table: cells' text, links and markers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_table = 0
        self.rows: List[Dict[str, Any]] = []
        self.row: Optional[Dict[str, Any]] = None
        self.cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'table':
            if self.in_table or a.get('id') == 'torrents':
                self.in_table += 1
            return
        if not self.in_table:
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
        if not self.in_table:
            return
        if tag == 'table':
            self.in_table -= 1
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
        det = next((h for h in row['links'] if re.match(r'^/t/\d+', h)), '')
        cat = next((h for h in row['links'] if re.match(r'^/t\?\d+', h)), '')
        if not dl:
            continue
        cells = row['cells']
        nums = [c for c in cells if re.fullmatch(r'[\d,]+', c)]
        size_cell = next((c for c in cells if _SIZE_RE.fullmatch(c.strip())), '')
        name_cell = cells[1] if len(cells) > 1 else ''
        title = name_cell
        seeders = _int(nums[-2].replace(',', '')) if len(nums) >= 2 else 0
        leechers = _int(nums[-1].replace(',', '')) if nums else 0
        out.append(_result(title, urljoin(base + '/', dl), _parse_size(size_cell),
                           seeders, leechers, row['free'],
                           cat.split('?', 1)[1] if cat else '',
                           guid=urljoin(base + '/', det) if det else urljoin(base + '/', dl)))
        if len(out) >= limit:
            break
    return out


def iptorrents_headers(config) -> Dict[str, str]:
    return {'Cookie': str(config.get('cookie') or '').strip(),
            'User-Agent': str(config.get('user_agent') or '').strip() or DEFAULT_UA}


def iptorrents_search(config, query, categories, limit, request, imdb_id='', tmdb_id=''):
    q = (query or '').strip() or imdb_id
    kind = _media_kind(categories)
    cats = IPT_MOVIE_CATS if kind == 'movie' else IPT_TV_CATS if kind == 'tv' else ()
    qs = '&'.join([str(c) for c in cats] + [urlencode({'q': q, 'qf': 'all' if IMDB_RE.match(q) else ''}), 'o=added'])
    status, headers, raw = request('GET', f'{_base(config)}/t?{qs}', headers=iptorrents_headers(config))
    text = raw.decode('utf-8', 'replace')
    if status in (301, 302, 303, 401, 403) or 'login.php' in str(headers.get('Location', '')) \
            or ('id="torrents"' not in text and ('name="username"' in text or 'login' in text[:4000].lower())):
        raise NativeAuthError('Cookie rejected')
    return parse_iptorrents(text, _base(config), limit)


def iptorrents_test(config, request):
    iptorrents_search(config, '', [2000], 1, request)
    return 'Connection successful'


CLIENTS = {
    'beyondhd': (beyondhd_search, beyondhd_test),
    'avistaz': (avistaz_search, avistaz_test),
    'iptorrents': (iptorrents_search, iptorrents_test),
}


def download_headers(config) -> Dict[str, str]:
    """Extra headers for fetching a .torrent from this instance's own host."""
    if str(config.get('type') or '').lower() == 'iptorrents':
        return iptorrents_headers(config)
    return {}
