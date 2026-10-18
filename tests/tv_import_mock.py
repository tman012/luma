"""A deterministic TMDB mock for TV Import acceptance, served from
tests/fixtures/tv_import/fixture.json (synthesized, see build_fixture.py).
No network. Optionally behaves like TMDB under load: a fixed latency per
request and a server-side limit that answers 429 (Retry-After) above a rate.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from app import tv_tmdb
from app.tv_match import normalize

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'tv_import', 'fixture.json')
_cache: Dict[str, Any] = {}


def load() -> Dict[str, Any]:
    if 'f' not in _cache:
        with open(FIXTURE, encoding='utf-8') as fh:
            _cache['f'] = json.load(fh)
    return _cache['f']


class MockTmdb:
    def __init__(self, fixture: Optional[Dict[str, Any]] = None, *, latency: float = 0.0,
                 server_rate: Optional[float] = None):
        self.f = fixture or load()
        self.shows = self.f['shows']
        self.latency = latency
        self.server_rate = server_rate
        self.calls: List[tuple] = []
        self.throttled = 0
        self._lock = threading.Lock()
        self._window: List[float] = []
        self._index = [(sid, ' ' + normalize(s['name']) + ' ', [' ' + normalize(a) + ' ' for a in s.get('aliases', [])])
                       for sid, s in self.shows.items()]

    # ------------------------------------------------------------ transport (a requests.Session stand-in)
    def session(self):
        return _Session(self)

    def handle(self, path: str, params: Dict[str, Any]):
        """(status, headers, body) for one GET, like TMDB."""
        with self._lock:
            self.calls.append((path, tuple(sorted((k, str(v)) for k, v in params.items() if k != 'api_key'))))
            if self.server_rate:
                now = time.monotonic()
                self._window = [t for t in self._window if now - t < 1.0]
                if len(self._window) >= self.server_rate:
                    self.throttled += 1
                    return 429, {'Retry-After': '1'}, {'status_code': 25}
                self._window.append(now)
        if self.latency:
            time.sleep(self.latency)
        parts = [p for p in path.split('/') if p]
        if parts == ['search', 'tv']:
            return 200, {}, {'results': [self.raw(i) for i in self._search(params.get('query', ''),
                                                                             params.get('first_air_date_year'))]}
        if parts[:1] == ['find']:
            return 200, {}, {'tv_results': []}
        if parts[:2] == ['tv', 'episode_group']:
            g = self._find_group(parts[2])
            return (200, {}, g) if g else (404, {}, {})
        if parts[:1] == ['tv'] and len(parts) >= 2 and parts[1].isdigit():
            sid = parts[1]
            if sid not in self.shows:
                return 404, {}, {}
            if len(parts) == 2:
                d = self.details(sid)
                for extra in str(params.get('append_to_response') or '').split(','):
                    if extra.startswith('season/'):
                        n = extra.split('/', 1)[1]
                        if n.isdigit() and self._season(sid, int(n)):
                            d[extra] = self._season(sid, int(n))
                if 'external_ids' not in str(params.get('append_to_response') or ''):
                    d.pop('external_ids', None)
                if 'alternative_titles' not in str(params.get('append_to_response') or ''):
                    d.pop('alternative_titles', None)
                return 200, {}, d
            if len(parts) == 4 and parts[2] == 'season' and parts[3].isdigit():
                se = self._season(sid, int(parts[3]))
                return (200, {}, se) if se else (404, {}, {})
            if parts[2:] == ['episode_groups']:
                return 200, {}, {'results': self._groups(sid)}
        return 404, {}, {}

    def raw(self, sid) -> Dict[str, Any]:
        s = self.shows[str(sid)]
        return {'id': int(sid), 'name': s['name'], 'original_name': s['original_name'],
                'first_air_date': s['first_air_date'], 'poster_path': f'/p{sid}.jpg', 'overview': '',
                'popularity': round(s['vote_count'] / 10, 1), 'vote_count': s['vote_count'],
                'origin_country': s['origin_country']}

    def _search(self, title: str, year=None) -> List[str]:
        q = normalize(title)
        if not q:
            return []
        ids = [str(i) for i in self.f['searches'].get(q, [])]
        needle = f' {q} '
        more = [sid for sid, name, alts in self._index
                if sid not in ids and (needle in name or any(needle in a for a in alts))]
        more.sort(key=lambda sid: (-self.shows[sid]['vote_count'], int(sid)))
        out = [sid for sid in ids + more if sid in self.shows]
        if year:
            out = [sid for sid in out if self.shows[sid]['first_air_date'][:4] == str(year)]
        return out[:20]

    def details(self, sid) -> Optional[Dict[str, Any]]:
        s = self.shows.get(str(sid))
        if not s:
            return None
        return {**self.raw(sid), 'status': 'Ended', 'networks': [{'name': 'Net'}],
                'external_ids': {'tvdb_id': None, 'imdb_id': None},
                'alternative_titles': {'results': [{'title': a} for a in s.get('aliases', [])]},
                'seasons': [{'season_number': int(n), 'episode_count': c} for n, c in s['seasons'].items()]}

    def _season(self, sid, n):
        s = self.shows[str(sid)]
        c = s['seasons'].get(str(n))
        if c is None:
            return None
        return {'name': f'Season {n}', 'season_number': int(n),
                'episodes': [{'episode_number': e, 'id': int(sid) * 10000 + int(n) * 100 + e, 'name': f'Episode {e}',
                              'air_date': f"{s['first_air_date'][:4] or '2000'}-02-01"} for e in range(1, c + 1)]}

    def _groups(self, sid):
        s = self.shows.get(str(sid)) or {}
        return [{'id': g['id'], 'name': g['name'], 'type': g['type'], 'episode_count': sum(g['seasons'].values()),
                 'group_count': len(g['seasons'])} for g in s.get('episode_groups', [])]

    def _find_group(self, gid):
        for sid, s in self.shows.items():
            for g in s.get('episode_groups', []):
                if g['id'] == gid:
                    return self._group(int(sid), g)
        return None

    def _group(self, sid, g):
        """A TVDB-order group over the same TMDB episodes: (s, e) is TMDB's own
        S/E where its season has it, else the next of TMDB's Specials."""
        default = {int(n): c for n, c in self.shows[str(sid)]['seasons'].items()}
        specials = iter(range(1, default.get(0, 0) + 1))
        groups = []
        if g['type'] == 3:             # DVD volumes: TMDB's own episodes in aired order, regrouped
            flat = iter(int(sid) * 10000 + n * 100 + e for n in sorted(default) if n for e in range(1, default[n] + 1))
            for n, c in sorted(g['seasons'].items(), key=lambda x: int(x[0])):
                groups.append({'name': f'Volume {n}', 'order': int(n),
                               'episodes': [{'id': next(flat), 'order': e} for e in range(c)]})
            return {'id': g['id'], 'name': g['name'], 'type': g['type'], 'groups': groups}
        for n, c in sorted(g['seasons'].items(), key=lambda x: int(x[0])):
            s, eps = int(n), []
            for e in range(1, c + 1):
                if e <= default.get(s, 0):
                    eid = int(sid) * 10000 + s * 100 + e
                else:
                    eid = int(sid) * 10000 + next(specials)
                eps.append({'id': eid, 'order': e - 1})
            groups.append({'name': f'Season {n}', 'order': s, 'episodes': eps})
        return {'id': g['id'], 'name': g['name'], 'type': g['type'], 'groups': groups}


class _Resp:
    def __init__(self, status, headers, body):
        self.status_code, self.headers, self._body = status, headers, body

    def json(self):
        return self._body


class _Session:
    def __init__(self, mock):
        self.mock = mock
        self.trust_env = True

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        from urllib.parse import urlsplit
        return _Resp(*self.mock.handle(urlsplit(url).path.split('/3', 1)[-1], dict(params or {})))

    def close(self):
        pass


def install(monkeypatch, mock: MockTmdb) -> MockTmdb:
    """Serve tv_tmdb's real HTTP code (pacing, retries, cache) from *mock*."""
    monkeypatch.setitem(tv_tmdb._cfg, 'get_key', lambda: 'k')
    monkeypatch.setitem(tv_tmdb._cfg, 'session_factory', mock.session)
    monkeypatch.setattr(tv_tmdb, 'tmdb_base_url', lambda: 'https://mock.tmdb/3')
    return mock

# ------------------------------------------------------------ Taylor's files

def file_names(folder: str) -> List[str]:
    """rel paths of Taylor's (synthesized) files in *folder*."""
    f = load()
    spec = f['files'][folder]
    title = folder.split(' (')[0]
    out = []
    if spec.get('tvdb'):
        sid = str(f['truth'][folder]['id'])
        g = f['shows'][sid]['episode_groups'][0]['seasons']
        multi = {(s, a): b for s, a, b in f['episode_shows'][folder]['multi']}
        for n, c in sorted(g.items(), key=lambda x: int(x[0])):
            s, e = int(n), 1
            while e <= c:
                if (s, e) in multi:
                    b = multi[(s, e)]
                    out.append(f'{folder}/Season {s}/{title} - S{s:02d}E{e:02d}-E{b:02d}.mkv')
                    e = b + 1
                    continue
                out.append(f'{folder}/Season {s}/{title} - S{s:02d}E{e:02d}.mkv')
                e += 1
        return out
    for n, c in sorted(spec['seasons'].items(), key=lambda x: int(x[0])):
        for e in range(1, c + 1):
            out.append(f'{folder}/Season {int(n)}/{title} - S{int(n):02d}E{e:02d}.mkv')
    return out
