"""v237: Settings > Home screen hero source ('Most popular') and count."""
import json
import time
from datetime import date

import pytest

from app import hero_source as hs, minimal


def test_rank_recent_first_by_popularity_then_the_rest():
    today = date(2026, 10, 21)
    rows = [
        {'title': 'Old Hit', 'popularity': 900, 'release_date': '2010-05-01'},
        {'title': 'New Small', 'popularity': 5, 'release_date': '2025-01-01'},
        {'title': 'New Big', 'popularity': 50, 'release_date': '2026-03-01'},
        {'title': 'Just Too Old', 'popularity': 999, 'release_date': '2024-10-20'},
        {'title': 'Old Mid', 'popularity': 100, 'release_date': '1999-01-01'},
    ]
    assert [r['title'] for r in hs.fallback(rows, today)] == ['New Big', 'New Small', 'Just Too Old', 'Old Hit', 'Old Mid']


def test_clamp_limit():
    assert [hs.clamp_limit(v) for v in (1, 5, 10, 30, 99, 'x', None)] == [5, 5, 10, 30, 30, 10, 10]


class FakeSettings:
    def __init__(self, d=None): self.d = d or {}
    def get(self, k, default=None):
        cur = self.d
        for p in k.split('.'):
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur
    def update(self, partial):
        def merge(a, b):
            for k, v in b.items():
                if isinstance(v, dict): merge(a.setdefault(k, {}), v)
                else: a[k] = v
        merge(self.d, partial); return True, []


class FakePlex:
    is_configured = True
    def __init__(self):
        self.calls = []
        self.movies = [
            {'rating_key': '1', 'title': 'A', 'tmdb_id': 11, 'artPath': '/a', 'added_at': 1000},
            {'rating_key': '2', 'title': 'B', 'tmdb_id': 22, 'artPath': '/b', 'added_at': 1001},
            {'rating_key': '3', 'title': 'C', 'tmdb_id': 33, 'artPath': '/c', 'added_at': 1002},
            {'rating_key': '4', 'title': 'NoTmdb', 'tmdb_id': None, 'artPath': '/d', 'added_at': 1003},
        ]
    def get_all_movies(self, sid, sort='', limit=50, offset=0):
        self.calls.append(('all', sid, limit))
        ms = sorted(self.movies, key=lambda m: -m['added_at']) if sort.startswith('addedAt') else list(self.movies)
        return {'movies': ms[:limit]}


@pytest.fixture
def env(tmp_path):
    s, plex, looked, cleared = FakeSettings(), FakePlex(), [], []
    tm = {11: {'popularity': 10, 'release_date': '2001-01-01'}, 22: {'popularity': 3, 'release_date': date.today().isoformat()},
          33: {'popularity': 80, 'release_date': '2003-01-01'}}
    user = {'u': {'id': 1, 'is_admin': True}}
    hs._inflight.clear(); hs._checked.clear()
    hs.init(settings=s, get_plex=lambda: plex, get_current_user=lambda: user['u'],
            tmdb_details=lambda t: looked.append(t) or tm[t], cache_dir=lambda: tmp_path,
            sections=lambda p: [{'id': '1', 'title': 'Movies', 'type': 'movie'}, {'id': '2', 'title': '4K', 'type': 'movie'},
                                {'id': '3', 'title': 'TV', 'type': 'show'}],
            clear_hero=lambda: cleared.append(1))
    return dict(s=s, plex=plex, looked=looked, cleared=cleared, user=user, tmp=tmp_path)


def test_build_ranks_caches_and_only_reads(env):
    out = hs.build('1')
    assert [m['rating_key'] for m in out] == ['2', '3', '1']      # recent first, then by popularity
    assert sorted(env['looked']) == [11, 22, 33]                   # TMDb lookups only for movies with ids
    data = json.loads((env['tmp'] / 'hero' / 'popular-1.json').read_text())
    assert [m['rating_key'] for m in data['movies']] == ['2', '3', '1']


def test_cache_refreshes_once_a_day_never_per_request(env):
    spawned = []
    spawn = lambda f: spawned.append(f)
    assert hs.ranked_keys('1', spawn=spawn) == []                  # no cache yet: one rebuild queued
    for _ in range(5):
        hs.ranked_keys('1', spawn=spawn)
    assert len(spawned) == 1                                       # in flight: no more
    spawned[0]()                                                   # the rebuild runs
    now = time.time()
    for _ in range(5):
        assert hs.ranked_keys('1', now=now + 3600, spawn=spawn) == ['2', '3', '1']
    assert len(spawned) == 1                                       # fresh: no refresh
    assert [c[2] for c in env['plex'].calls] == [hs.POOL_MAX, 1]   # one rebuild + one 15-min check
    hs.ranked_keys('1', now=now + hs.DAY_S + 1, spawn=spawn)
    hs.ranked_keys('1', now=now + hs.DAY_S + 2, spawn=spawn)
    assert len(spawned) == 2                                       # a day later: one refresh


def test_a_failed_refresh_waits_a_day(env):
    env['plex'].get_all_movies = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down'))
    spawned = []
    hs.ranked_keys('1', spawn=spawned.append); spawned[0]()
    hs.ranked_keys('1', spawn=spawned.append)
    assert len(spawned) == 1


def _app():
    from flask import Flask
    a = Flask('t'); a.register_blueprint(hs.bp); return a.test_client()


def test_settings_get_and_admin_put(env):
    c = _app()
    r = c.get('/api/settings/hero').get_json()
    assert r == {'source': 'newest', 'section_id': '', 'limit': 10,
                 'libraries': [{'id': '1', 'title': 'Movies'}, {'id': '2', 'title': '4K'}]}
    r = c.put('/api/settings/hero', json={'source': 'popular', 'section_id': '2', 'limit': 50}).get_json()
    assert (r['source'], r['section_id'], r['limit']) == ('popular', '2', 30)
    assert env['s'].d['ui']['hero'] == {'source': 'popular', 'library_section_id': 2, 'limit': 30}
    assert env['cleared']
    assert c.put('/api/settings/hero', json={'limit': 2}).get_json()['limit'] == 5
    assert c.put('/api/settings/hero', json={'source': 'bogus'}).status_code == 400
    assert c.put('/api/settings/hero', json={'section_id': '3'}).status_code == 400   # a TV library
    assert c.put('/api/settings/hero', json={'limit': 'ten'}).status_code == 400


def test_settings_put_is_admin_only(env):
    c = _app()
    env['user']['u'] = {'id': 2, 'is_admin': False}
    assert c.get('/api/settings/hero').status_code == 200
    assert c.put('/api/settings/hero', json={'limit': 20}).status_code == 403
    assert 'ui' not in env['s'].d
    env['user']['u'] = None
    assert c.get('/api/settings/hero').status_code == 401


def test_minimal_allowlist():
    assert minimal.path_allowed('/api/settings/hero', 'GET')
    assert minimal.path_allowed('/api/settings/hero', 'PUT')


def test_hero_content_uses_the_popular_ranking(monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    import app.main as m
    monkeypatch.setattr(hs, 'ranked_keys', lambda sid, **k: ['3', '1'])
    monkeypatch.setattr(hs, 'current', lambda: {'source': 'popular', 'section_id': '1', 'limit': 10})
    vals = {'ui.hero.source': 'popular', 'ui.hero.library_section_id': 1, 'ui.hero.limit': 10}
    orig = m.settings.get
    monkeypatch.setattr(m.settings, 'get', lambda k, d=None: vals.get(k, orig(k, d)))
    plex = FakePlex()
    monkeypatch.setattr(m, 'get_plex_for_request', lambda: plex)
    m._hero_result_cache.clear()
    movies, boost = m._build_hero_movies()
    m._hero_result_cache.clear()
    assert [x.get('title') for x in movies][:2] == ['C', 'A'] and boost is False


def test_v238_build_runs_outside_a_request_with_the_global_plex(monkeypatch, tmp_path):
    """The daily rebuild runs in a thread with no request context: main wires the global plex_service."""
    import flask
    import app.main as m
    plex = FakePlex()
    monkeypatch.setattr(m, 'plex_service', plex)
    monkeypatch.setattr(m, 'get_plex_for_request', lambda: (_ for _ in ()).throw(RuntimeError('request only')))
    monkeypatch.setitem(hs._cfg, 'cache_dir', lambda: tmp_path)
    monkeypatch.setitem(hs._cfg, 'tmdb_details', lambda t: {11: {'popularity': 1}, 22: {'popularity': 9}, 33: {'popularity': 5}}[t])
    # main's own get_plex, re-installed (an earlier test's init may have replaced _cfg)
    import importlib, re
    src = open(m.__file__).read()
    assert re.search(r"_hero_source\.init\(settings=settings, get_plex=lambda: plex_service", src)
    monkeypatch.setitem(hs._cfg, 'get_plex', lambda: m.plex_service)
    assert not flask.has_request_context()
    import threading
    out = {}
    t = threading.Thread(target=lambda: out.setdefault('r', hs.build('1'))); t.start(); t.join()
    assert [x['rating_key'] for x in out['r']] == ['2', '3', '1']


# --- v239: just-added popular first, obscure out, fallback fills ---------------
NOW = 1_800_000_000.0
DAY = 86400


def _row(k, pop, votes, added_days_ago, released):
    return {'rating_key': k, 'title': k, 'popularity': pop, 'vote_count': votes,
            'added_at': NOW - added_days_ago * DAY, 'release_date': released}


def test_v239_just_added_blockbuster_first_obscure_dropped_and_fill():
    rows = [
        _row('Blockbuster', 300, 9000, 3, '2026-07-01'),   # just added, famous
        _row('Cult Hit', 12, 450, 20, '1994-01-01'),        # just added, enough votes
        _row('Obscure', 2, 15, 1, '2026-09-01'),            # just added, nobody knows it: not fresh
        _row('Old Popular', 500, 20000, 400, '2010-01-01'),  # added long ago
        _row('Recent Release', 80, 1000, 300, '2025-06-01'),
    ]
    order = [r['rating_key'] for r in hs.rank(rows, today=date(2026, 10, 21), now=NOW)]
    assert order[:2] == ['Blockbuster', 'Cult Hit']
    # fallback fill: released in the last 2 years by popularity, then the rest by popularity
    assert order[2:] == ['Recent Release', 'Obscure', 'Old Popular']
    assert len(order) == len(set(order))


def test_v239_popularity_floor_alone_qualifies():
    rows = [_row('Trending', hs.POP_FLOOR + 1, 10, 5, '2026-01-01'), _row('Quiet', hs.POP_FLOOR, 199, 5, '2026-01-01')]
    assert hs.notable(rows[0]) and not hs.notable(rows[1])


def test_v239_build_uses_added_at_and_vote_count(env, monkeypatch):
    now = time.time()
    env['plex'].movies[0]['added_at'] = int(now - 2 * DAY)        # A: just added, famous
    monkeypatch.setitem(hs._cfg, 'tmdb_details', lambda t: {11: {'popularity': 1, 'vote_count': 5000, 'release_date': '1990-01-01'},
                                                         22: {'popularity': 3, 'vote_count': 1, 'release_date': '1990-01-01'},
                                                         33: {'popularity': 80, 'vote_count': 1, 'release_date': '1990-01-01'}}[t])
    assert [m['rating_key'] for m in hs.build('1')] == ['1', '3', '2']


def test_v239_new_addition_triggers_one_rebuild_at_most_every_15_minutes(env):
    hs._checked.clear()
    spawned = []
    hs.ranked_keys('1', spawn=spawned.append); spawned[0]()     # first build
    t0 = time.time()
    hs._checked['1'] = t0
    env['plex'].movies.append({'rating_key': '5', 'title': 'New', 'tmdb_id': 11, 'artPath': '/e', 'added_at': 5000})
    hs.ranked_keys('1', now=t0 + 60, spawn=spawned.append)       # within 15 min: no Plex check
    assert len(spawned) == 1
    n_calls = len(env['plex'].calls)
    hs.ranked_keys('1', now=t0 + hs.CHECK_S + 1, spawn=spawned.append)
    assert len(spawned) == 2                                       # newest addedAt changed: rebuild
    assert len(env['plex'].calls) == n_calls + 1 and env['plex'].calls[-1][2] == 1
    hs.ranked_keys('1', now=t0 + hs.CHECK_S + 5, spawn=spawned.append)
    assert len(spawned) == 2 and len(env['plex'].calls) == n_calls + 1   # in flight / within 15 min
    spawned[1]()
    hs.ranked_keys('1', now=t0 + 2 * hs.CHECK_S + 2, spawn=spawned.append)
    assert len(spawned) == 2                                       # unchanged after rebuild: none


def test_v239_requests_never_look_up_tmdb(env):
    hs._checked.clear()
    spawned = []
    hs.ranked_keys('1', spawn=spawned.append); spawned[0]()
    env['looked'].clear()
    for i in range(20):
        hs.ranked_keys('1', now=time.time() + i * hs.CHECK_S, spawn=lambda f: None)
    assert env['looked'] == []
