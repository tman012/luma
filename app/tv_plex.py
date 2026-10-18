"""Plex as a "downloaded" source for the TV Calendar (read-only).

An episode counts as downloaded when Plex has it, matched by the show's TMDB or
TVDB id (Plex Guid tags 'tmdb://1399' / 'tvdb://121361', or a legacy agent guid
'com.plexapp.agents.thetvdb://121361?lang=en' / '...themoviedb://1399') plus
season and episode number. The set is built from Plex's TV sections (GETs
only, through Luma's existing Plex client), kept in memory, rebuilt every
FULL_TTL_S on a Calendar refresh, and topped up every minute from recently
added. Plex not configured or unreachable: an empty set, never an error.

v173 (Tester FF-5): Plex disconnected or removed (no configured Plex), or pointed at another server,
drops the index at once, so the Calendar stops showing Downloaded from a Plex that's gone, without
a restart.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

FULL_TTL_S = 30 * 60
PAGE = 2000
RECENT_N = 100

Key = Tuple[str, int, int, int]          # ('tmdb'|'tvdb', show id, season, episode)
_cfg: Dict[str, Any] = {}
_have: Set[Key] = set()
_shows: Dict[str, Tuple[Optional[int], Optional[int]]] = {}   # Plex show ratingKey -> (tmdb, tvdb)
_state: Dict[str, Any] = {'built_at': 0.0, 'down': False, 'server': None}
_new: List[Key] = []          # recently added keys not yet looked at by tv_watch.plex_tick
_lock = threading.Lock()

_NEW = re.compile(r'^(tmdb|tvdb)://(\d+)$')
_LEGACY = re.compile(r'^com\.plexapp\.agents\.(thetvdb|themoviedb|tmdb)://(\d+)')


def init(get_plex: Callable[[], Any]) -> None:
    _cfg['get_plex'] = get_plex


def show_ids(guid: Optional[str], guids: Any = ()) -> Tuple[Optional[int], Optional[int]]:
    """(tmdb, tvdb) from a show's Guid tags and/or its (legacy) guid attribute."""
    tmdb = tvdb = None
    for g in list(guids or ()) + [guid or '']:
        g = str(g or '').strip()
        m = _NEW.match(g)
        if m:
            kind, n = m.group(1), int(m.group(2))
        else:
            m = _LEGACY.match(g)
            if not m:
                continue
            kind, n = ('tvdb' if m.group(1) == 'thetvdb' else 'tmdb'), int(m.group(2))
        if kind == 'tmdb' and tmdb is None:
            tmdb = n
        elif kind == 'tvdb' and tvdb is None:
            tvdb = n
    return tmdb, tvdb


def _plex():
    try:
        p = (_cfg.get('get_plex') or (lambda: None))()
    except Exception:                       # noqa: BLE001
        return None
    return p if p is not None and getattr(p, 'is_configured', False) else None


def _server(p) -> str:
    return str(getattr(p, 'plex_url', '') or '').strip().rstrip('/').lower()


def forget(reason: str = 'Plex is no longer connected') -> None:
    """Drop the 'in Plex' index (v173). Safe to call any time; an in-flight rebuild for the old
    server doesn't write its result back (it checks the server again before storing)."""
    had = bool(_have or _shows or _new or _state.get('built_at'))
    _have.clear()
    _shows.clear()
    _new.clear()
    _state.update(built_at=0.0, down=False, server=None)
    if had:
        logger.info('TV Plex: index cleared (%s)', reason)


def _current() -> Optional[Any]:
    """The configured Plex, after dropping an index built from a Plex that's gone or changed."""
    p = _plex()
    if p is None:
        if _have or _shows or _new or _state.get('built_at'):
            forget()
        return None
    if _state.get('server') is not None and _server(p) != _state['server']:
        forget('Plex server changed')
    return p


def _sections(p) -> list:
    root = p._get('/library/sections')
    if root is None:
        return []
    return [d.get('key') for d in root.findall('.//Directory') if d.get('type') == 'show' and d.get('key')]


def _guids(el) -> list:
    return [g.get('id') for g in el.findall('Guid') if g.get('id')]


def _keys_for(ep, shows) -> Set[Key]:
    out: Set[Key] = set()
    try:
        s, e = int(ep.get('parentIndex')), int(ep.get('index'))
    except (TypeError, ValueError):
        return out
    tmdb, tvdb = shows.get(ep.get('grandparentRatingKey') or '', (None, None))
    if tmdb is None and tvdb is None:
        tmdb, tvdb = show_ids(ep.get('grandparentGuid'))
    if tmdb is not None:
        out.add(('tmdb', tmdb, s, e))
    if tvdb is not None:
        out.add(('tvdb', tvdb, s, e))
    return out


def rebuild(force: bool = False) -> int:
    """Every episode in Plex's TV sections (paged). Returns the set size; -1 when skipped."""
    p = _current()
    if p is None:
        return -1
    if not force and time.time() - _state['built_at'] < FULL_TTL_S:
        return -1
    if not _lock.acquire(blocking=False):
        return -1
    try:
        t0 = time.monotonic()
        shows: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
        have: Set[Key] = set()
        for sec in _sections(p):
            root = p._get(f'/library/sections/{sec}/all', params={'type': 2, 'includeGuids': 1}, timeout=30)
            for d in (root.findall('.//Directory') if root is not None else []):
                shows[d.get('ratingKey') or ''] = show_ids(d.get('guid'), _guids(d))
            start = 0
            while True:
                root = p._get(f'/library/sections/{sec}/all', timeout=60, params={
                    'type': 4, 'X-Plex-Container-Start': start, 'X-Plex-Container-Size': PAGE})
                if root is None:
                    break
                eps = root.findall('.//Video')
                for ep in eps:
                    have |= _keys_for(ep, shows)
                if len(eps) < PAGE:
                    break
                start += PAGE
        now = _plex()
        if now is None or _server(now) != _server(p):    # disconnected or repointed meanwhile
            forget('Plex changed during a rebuild')
            return -1
        _have.clear(); _have.update(have)
        _shows.clear(); _shows.update(shows)
        _state['built_at'] = time.time()
        _state['down'] = False
        _state['server'] = _server(p)
        logger.info("TV Plex: %d episode keys for %d shows in %.1fs", len(have), len(shows), time.monotonic() - t0)
        return len(have)
    except Exception as e:                    # noqa: BLE001 - Plex trouble never breaks the Calendar
        logger.info('TV Plex unavailable (%s)', type(e).__name__)
        _state['down'] = True                 # linked files answer alone until Plex is back
        return -1
    finally:
        _lock.release()


def _learn(p, key: Optional[str]) -> None:
    """A show Plex added since the last full set (new agent guids are plex://, ids live in Guid tags)."""
    if not key or key in _shows:
        return
    try:
        root = p._get(f'/library/metadata/{key}', params={'includeGuids': 1}, timeout=15)
        d = root.find('.//Directory') if root is not None else None
        _shows[key] = show_ids(d.get('guid'), _guids(d)) if d is not None else (None, None)
    except Exception as e:                    # noqa: BLE001 - asked again next minute
        logger.info('TV Plex show lookup failed (%s)', type(e).__name__)


def recent() -> int:
    """Top up from Plex's recently added TV episodes. Returns how many were new."""
    p = _current()
    if p is None or not _state['built_at']:
        return 0
    n = 0
    try:
        for sec in _sections(p):
            root = p._get(f'/library/sections/{sec}/recentlyAdded', params={
                'type': 4, 'X-Plex-Container-Start': 0, 'X-Plex-Container-Size': RECENT_N})
            for ep in (root.findall('.//Video') if root is not None else []):
                _learn(p, ep.get('grandparentRatingKey'))
                for k in _keys_for(ep, _shows):
                    if k not in _have:
                        _have.add(k)
                        _new.append(k)
                        n += 1
    except Exception as e:                    # noqa: BLE001
        logger.info('TV Plex recently added unavailable (%s)', type(e).__name__)
        _state['down'] = True
        return 0
    _state['down'] = False
    if n:
        logger.info('TV Plex: %d newly added episode key(s)', n)
    return n


def has(tmdb_id: Optional[int], tvdb_id: Optional[int], season: int, episode: int) -> bool:
    if not _have:
        return False
    if _current() is None or _state.get('down'):
        return False
    return ((tmdb_id is not None and ('tmdb', int(tmdb_id), season, episode) in _have)
            or (tvdb_id is not None and ('tvdb', int(tvdb_id), season, episode) in _have))
