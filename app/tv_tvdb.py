"""TheTVDB v4 for Calendar air times (Settings > Connectors key, optional PIN).

POST /v4/login gives a bearer token, cached here (server side, never logged)
and fetched again on a 401. A show maps through TMDB's external tvdb_id;
/series/{id}/extended gives airsTime and the original network's country,
/series/{id}/episodes/default gives each episode's aired date and runtime.
The air time is local to the network's country, so the UTC airstamp is
computed with that country's zone (zoneinfo, stdlib). Paced on its own
budget (TMDB's is untouched). No key: TVmaze, else the date only.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

BASE = 'https://api4.thetvdb.com/v4'
TIMEOUT_S = 10
PAGES_MAX = 20

# Network country (TVDB's 3-letter code) -> the zone its schedule is in.
COUNTRY_TZ = {
    'usa': 'America/New_York', 'can': 'America/Toronto', 'gbr': 'Europe/London', 'irl': 'Europe/Dublin',
    'aus': 'Australia/Sydney', 'nzl': 'Pacific/Auckland', 'deu': 'Europe/Berlin', 'fra': 'Europe/Paris',
    'esp': 'Europe/Madrid', 'ita': 'Europe/Rome', 'nld': 'Europe/Amsterdam', 'bel': 'Europe/Brussels',
    'swe': 'Europe/Stockholm', 'nor': 'Europe/Oslo', 'dnk': 'Europe/Copenhagen', 'fin': 'Europe/Helsinki',
    'pol': 'Europe/Warsaw', 'jpn': 'Asia/Tokyo', 'kor': 'Asia/Seoul', 'chn': 'Asia/Shanghai', 'twn': 'Asia/Taipei',
    'ind': 'Asia/Kolkata', 'bra': 'America/Sao_Paulo', 'mex': 'America/Mexico_City', 'arg': 'America/Argentina/Buenos_Aires',
    'zaf': 'Africa/Johannesburg', 'isr': 'Asia/Jerusalem', 'tur': 'Europe/Istanbul', 'rus': 'Europe/Moscow',
}

_tok: Dict[str, Any] = {'token': None, 'for': None}
_lock = threading.Lock()
_cfg: Dict[str, Any] = {'post': None, 'get': None}


class TvdbError(Exception):
    pass


def forget_token() -> None:
    with _lock:
        _tok.update(token=None, **{'for': None})


def _login(key: str, pin: str) -> Tuple[Optional[str], Optional[str]]:
    """(token, None) or (None, short error)."""
    body = {'apikey': key, **({'pin': pin} if pin else {})}
    try:
        if _cfg['post'] is not None:
            status, data = _cfg['post']('/login', body)
        else:
            r = requests.post(f'{BASE}/login', json=body, timeout=TIMEOUT_S)
            status, data = r.status_code, (r.json() if r.content else {})
    except requests.exceptions.Timeout:
        return None, 'Timed out'
    except (requests.RequestException, ValueError):
        return None, "Can't reach TheTVDB"
    if status in (401, 403):
        return None, 'Invalid key or PIN'
    if status == 429:
        return None, 'Rate limited'
    tok = ((data or {}).get('data') or {}).get('token') if isinstance(data, dict) else None
    if status != 200 or not isinstance(tok, str) or not tok:
        return None, 'Unexpected response'
    return tok, None


def check_login(key: str, pin: str = '') -> Optional[str]:
    return _login(key, pin)[1]


def _token(key: str, pin: str, renew: bool = False) -> str:
    with _lock:
        if not renew and _tok['token'] and _tok['for'] == (key, pin):
            return _tok['token']
        tok, err = _login(key, pin)
        if not tok:
            raise TvdbError(err or 'login failed')
        _tok.update(token=tok, **{'for': (key, pin)})
        return tok


def _get(path: str, creds: Tuple[str, str], params: Optional[Dict[str, Any]] = None) -> Any:
    """'data' of a GET; None on 404; a 401 logs in again once."""
    for attempt in (0, 1):
        tok = _token(*creds, renew=attempt == 1)
        if _cfg['get'] is not None:
            status, data = _cfg['get'](path, params or {}, tok)
        else:
            r = requests.get(f'{BASE}{path}', params=params, timeout=TIMEOUT_S, headers={'Authorization': f'Bearer {tok}'})
            status, data = r.status_code, (r.json() if r.content else {})
        if status == 401 and attempt == 0:
            continue
        if status == 404:
            return None
        if status != 200:
            raise TvdbError(f'HTTP {status}')
        return (data or {}).get('data') if isinstance(data, dict) else None
    return None


ART = 'https://artworks.thetvdb.com'


def image_url(v: Any) -> Optional[str]:
    """An episode image on TheTVDB's artwork host (a relative '/banners/...' gets the host), else None."""
    if not isinstance(v, str) or not v or len(v) > 300 or any(c in v for c in ' "<>'):
        return None
    if v.startswith('/'):
        v = ART + v
    return v if v.startswith(ART + '/') else None


def zone_for(country: Optional[str]) -> Optional[str]:
    return COUNTRY_TZ.get(str(country or '').lower()) if country else None


def airstamp(aired: Optional[str], airs_time: Optional[str], tz: Optional[str]) -> Optional[str]:
    """The UTC ISO airstamp for a local date + 'HH:MM' in zone *tz*, else None."""
    if not aired or not airs_time or not tz:
        return None
    try:
        d = dt.date.fromisoformat(aired[:10])
        hh, mm = (int(x) for x in airs_time.strip()[:5].split(':'))
        local = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=ZoneInfo(tz))
    except (ValueError, KeyError, Exception):   # noqa: BLE001 - a bad value is no time
        return None
    return local.astimezone(dt.timezone.utc).isoformat()


def episodes(tvdb_id: int, creds: Tuple[str, str], pace: Callable[[], None]) -> Dict[tuple, Dict[str, Any]]:
    """{(season, number): {'airstamp', 'runtime'}} from TVDB, {} when it doesn't know the show."""
    ext = _get(f'/series/{int(tvdb_id)}/extended', creds, {'short': 'true'})
    if not isinstance(ext, dict):
        return {}
    net = ext.get('originalNetwork') or ext.get('latestNetwork') or {}
    tz = zone_for(net.get('country') if isinstance(net, dict) else None) or zone_for(ext.get('originalCountry'))
    airs = ext.get('airsTime') if isinstance(ext.get('airsTime'), str) else None
    out: Dict[tuple, Dict[str, Any]] = {}
    for page in range(PAGES_MAX):
        pace()
        data = _get(f'/series/{int(tvdb_id)}/episodes/default', creds, {'page': page})
        eps = (data or {}).get('episodes') if isinstance(data, dict) else None
        if not eps:
            break
        for e in eps:
            s, n = e.get('seasonNumber'), e.get('number')
            if not isinstance(s, int) or not isinstance(n, int):
                continue
            stamp = airstamp(e.get('aired'), airs, tz)
            rt = e.get('runtime') if isinstance(e.get('runtime'), int) and 0 < e.get('runtime') < 1000 else None
            img = image_url(e.get('image'))
            if stamp or img:
                out[(s, n)] = {'airstamp': stamp, 'runtime': rt, 'image': img}
        if len(eps) < 500:
            break
    return out


def configure(post=None, get=None) -> None:
    """Tests: a fake TheTVDB."""
    _cfg.update(post=post, get=get)
    forget_token()
