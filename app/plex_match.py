"""
plex_match — "Fix Match" for Plex items, driven from Luma's movie/show popup.

Uses Plex's own match API with the server token (never sent to the browser):

    GET  /library/metadata/{rk}/matches?manual=1&title=&year=&agent=&language=
    PUT  /library/metadata/{rk}/match?guid=&name=&year=
    PUT  /library/metadata/{rk}/refresh
    PUT  /library/metadata/{rk}/unmatch

The agent comes from the item's library section (e.g. tv.plex.agents.movie /
tv.plex.agents.series). Candidate posters from the modern agents are public
images.plex.tv URLs with no token; any server-relative thumb is routed through
Luma's /api/img/plex proxy instead.
"""

from __future__ import annotations

import logging
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

_AGENT_FALLBACK = {'movie': 'tv.plex.agents.movie', 'show': 'tv.plex.agents.series'}
_section_agent_cache: Dict[str, tuple] = {}  # "<plex_url>|<section>" → (ts, agent)
_SECTION_TTL = 3600
_lock = threading.Lock()


class MatchError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _request(plex, method: str, path: str, params: Optional[dict] = None, timeout: int = 30):
    if not plex or not plex.is_configured:
        raise MatchError('Plex not configured', 503)
    url = f'{plex.plex_url}{path}'
    try:
        resp = plex._session.request(method, url, params=params, timeout=timeout)
    except Exception as exc:  # network
        logger.error('Plex match %s %s failed: %s', method, path, type(exc).__name__)
        raise MatchError('Plex unavailable', 502)
    if resp.status_code == 404:
        raise MatchError('Not found', 404)
    if resp.status_code >= 400:
        logger.error('Plex match %s %s → HTTP %s', method, path, resp.status_code)
        raise MatchError('Plex rejected the request', 502)
    return resp


def _xml(resp) -> ET.Element:
    try:
        return ET.fromstring(resp.content)
    except ET.ParseError:
        raise MatchError('Unexpected response from Plex', 502)


def get_item(plex, rating_key: int) -> Dict[str, Any]:
    """Current match info for an item (movie or show)."""
    root = _xml(_request(plex, 'GET', f'/library/metadata/{rating_key}', params={'includeGuids': 1}))
    el = None
    for child in root:
        if child.tag in ('Video', 'Directory'):
            el = child
            break
    if el is None:
        raise MatchError('Not found', 404)
    kind = el.get('type') or ''
    if kind not in ('movie', 'show'):
        raise MatchError('Only movies and shows can be matched', 400)
    ext = {}
    for g in el.findall('Guid'):
        gid = g.get('id') or ''
        if '://' in gid:
            src, val = gid.split('://', 1)
            ext[src] = val
    tmdb = ext.get('tmdb')
    return {
        'rating_key': rating_key,
        'type': kind,
        'title': el.get('title') or '',
        'year': int(el.get('year')) if (el.get('year') or '').isdigit() else None,
        'guid': el.get('guid') or '',
        'section_id': el.get('librarySectionID') or '',
        'tmdb_id': int(tmdb) if tmdb and tmdb.isdigit() else None,
        'imdb_id': ext.get('imdb'),
        'tvdb_id': ext.get('tvdb'),
        'updated_at': el.get('updatedAt'),
    }


def _section_agent(plex, section_id: str, kind: str) -> str:
    key = f'{plex.plex_url}|{section_id}'
    now = time.time()
    with _lock:
        hit = _section_agent_cache.get(key)
        if hit and now - hit[0] < _SECTION_TTL:
            return hit[1]
    agent = ''
    try:
        root = _xml(_request(plex, 'GET', '/library/sections', timeout=15))
        for d in root.findall('Directory'):
            if d.get('key') == str(section_id):
                agent = d.get('agent') or ''
                break
    except MatchError:
        pass
    agent = agent if agent and agent != 'tv.plex.agents.none' else _AGENT_FALLBACK.get(kind, '')
    with _lock:
        _section_agent_cache[key] = (now, agent)
    return agent


def _thumb(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    if raw.startswith('/'):
        return f'/api/img/plex?url={quote(raw, safe="")}&w=150&h=225'
    p = urlparse(raw)
    if p.scheme != 'https' or 'token' in (p.query or '').lower():
        return None
    return raw


def search(plex, rating_key: int, title: str, year: Optional[int]) -> Dict[str, Any]:
    item = get_item(plex, rating_key)
    agent = _section_agent(plex, item['section_id'], item['type'])
    params: Dict[str, Any] = {'manual': 1, 'title': title or item['title'], 'agent': agent, 'language': 'en-US'}
    if year:
        params['year'] = year
    root = _xml(_request(plex, 'GET', f'/library/metadata/{rating_key}/matches', params=params, timeout=45))
    results: List[Dict[str, Any]] = []
    seen = set()
    for sr in root.findall('SearchResult'):
        guid = sr.get('guid') or ''
        if not guid or guid in seen:
            continue
        seen.add(guid)
        y = sr.get('year') or ''
        score = sr.get('score')
        results.append({
            'guid': guid,
            'name': sr.get('name') or '',
            'year': int(y) if y.isdigit() else None,
            'thumb': _thumb(sr.get('thumb')),
            'score': int(score) if (score or '').isdigit() else None,
            'summary': (sr.get('summary') or '')[:300],
            'current': guid == item['guid'],
        })
    # Current match first, then Plex's own order.
    results.sort(key=lambda r: 0 if r['current'] else 1)
    return {
        'current': {k: item[k] for k in ('rating_key', 'type', 'title', 'year', 'guid')},
        'results': results[:25],
    }


def _wait_refreshed(plex, rating_key: int, before: Dict[str, Any], guid: Optional[str], timeout: float = 20.0) -> Dict[str, Any]:
    """Poll until Plex shows the change, so the popup reloads with fresh data.

    Re-applying the current match changes nothing visible, so that returns
    quickly. A new match waits for the guid and then the refreshed title/year.
    """
    start = time.time()
    deadline = start + timeout
    latest = before
    while time.time() < deadline:
        time.sleep(0.75)
        try:
            latest = get_item(plex, rating_key)
        except MatchError:
            continue
        if guid:
            if latest['guid'] != guid:
                continue
            if guid == before['guid']:
                if time.time() - start >= 1.5:
                    return latest
                continue
            if (latest['title'], latest['year'], latest['updated_at']) != (before['title'], before['year'], before['updated_at']):
                return latest
        elif latest['guid'] != before['guid']:
            return latest
    return latest


def apply(plex, rating_key: int, guid: str, name: str, year: Optional[int]) -> Dict[str, Any]:
    if not guid or '://' not in guid:
        raise MatchError('Invalid match', 400)
    before = get_item(plex, rating_key)
    params: Dict[str, Any] = {'guid': guid, 'name': name or ''}
    if year:
        params['year'] = year
    _request(plex, 'PUT', f'/library/metadata/{rating_key}/match', params=params)
    try:
        _request(plex, 'PUT', f'/library/metadata/{rating_key}/refresh', params={'force': 1})
    except MatchError as exc:
        logger.warning('Plex refresh after match failed for %s: %s', rating_key, exc)
    after = _wait_refreshed(plex, rating_key, before, guid)
    logger.info('Fix Match: %s %r (%s) → %r (%s) guid %s→%s', rating_key, before['title'], before['year'],
                after['title'], after['year'], before['guid'], after['guid'])
    return {'before': before, 'after': after}


def unmatch(plex, rating_key: int) -> Dict[str, Any]:
    before = get_item(plex, rating_key)
    _request(plex, 'PUT', f'/library/metadata/{rating_key}/unmatch')
    after = _wait_refreshed(plex, rating_key, before, None, timeout=10.0)
    logger.info('Fix Match: unmatched %s %r (%s)', rating_key, before['title'], before['year'])
    return {'before': before, 'after': after}
