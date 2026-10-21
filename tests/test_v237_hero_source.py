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
    assert [r['title'] for r in hs.rank(rows, today)] == ['New Big', 'New Small', 'Just Too Old', 'Old Hit', 'Old Mid']


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
    def __init__(self): self.calls = []
    def get_all_movies(self, sid, sort='', limit=50, offset=0):
        self.calls.append(('all', sid))
        return {'movies': [
            {'rating_key': '1', 'title': 'A', 'tmdb_id': 11, 'artPath': '/a'},
            {'rating_key': '2', 'title': 'B', 'tmdb_id': 22, 'artPath': '/b'},
            {'rating_key': '3', 'title': 'C', 'tmdb_id': 33, 'artPath': '/c'},
            {'rating_key': '4', 'title': 'NoTmdb', 'tmdb_id': None, 'artPath': '/d'},
        ]}


@pytest.fixture
def env(tmp_path):
    s, plex, looked, cleared = FakeSettings(), FakePlex(), [], []
    tm = {11: {'popularity': 10, 'release_date': '2001-01-01'}, 22: {'popularity': 3, 'release_date': date.today().isoformat()},
          33: {'popularity': 80, 'release_date': '2003-01-01'}}
    user = {'u': {'id': 1, 'is_admin': True}}
    hs._inflight.clear()
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
    assert len(env['plex'].calls) == 1
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
